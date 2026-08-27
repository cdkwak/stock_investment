"""Exactly-one-call BOK ECOS 3Y historical page-semantics diagnostic."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[3]
for value in (ROOT, ROOT / "src"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from scripts.manual.backfill.backfill_bok_ecos_treasury import _metadata_summary
from scripts.manual.backfill.bok_ecos_treasury_backfill_support import (
    BackfillError, OPERATION, load_plan, parse_response, plan_sha256,
    redacted_route, request_url,
)
from scripts.manual.pilot.pilot_bok_ecos_treasury import (
    Ledger, _atomic_replace, _immutable_bytes, _lock, _sha,
)


API_KEY_ENV = "BOK_ECOS_API_KEY"
LANDING_RELATIVE = Path("data/landing/diagnostics/bok_ecos_treasury_page_semantics")
TIMEOUT_SECONDS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_pilot(*, project_root: Path, plan_path: Path, approve_plan_sha256: str, session=None):
    plan = load_plan(plan_path)
    digest = plan_sha256(plan)
    if approve_plan_sha256 != digest:
        raise BackfillError("approved plan digest differs")
    _metadata_summary(project_root, plan.metadata_summary_sha256, plan)
    scope = next(value for value in plan.scopes if value.tenor == "3Y")
    if scope.start_date != "19981113" or scope.end_date != "20260813":
        raise BackfillError("3Y diagnostic range differs from approved scope")
    key = os.environ.get(API_KEY_ENV, "")
    if not key:
        raise BackfillError(f"{API_KEY_ENV} is required")
    root = project_root / LANDING_RELATIVE
    if root.exists() and any(root.glob("run_*")):
        raise BackfillError("page-semantics pilot has already been attempted")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = root / f"run_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    checkpoint = {
        "version": 1, "run_id": run_id, "status": "CREATED",
        "operation": OPERATION, "scope": "3Y_19981113_20260813",
        "plan_sha256": digest, "metadata_summary_sha256": plan.metadata_summary_sha256,
        "max_raw_requests": 1, "retry_count": 0, "normalized_writes": 0,
    }
    _atomic_replace(run_dir / "checkpoint.json", checkpoint)
    ledger = Ledger(run_dir / "call_ledger.jsonl", secrets=(key,))
    with _lock(root, run_id):
        ledger.append("RUN_CREATED", run_id=run_id, plan_sha256=digest,
                      max_raw_requests=1, retry_count=0)
        landing = run_dir / "response_01_3Y_19981113_20260813.json"
        started = time.monotonic()
        try:
            response = (session or requests.Session()).get(
                request_url(key, plan, scope), timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            message = str(error).replace(key, "<redacted>")
            ledger.append("HTTP_ERROR", sequence=1, operation=OPERATION, scope="3Y",
                          route=redacted_route(plan, scope), error=message)
            checkpoint["status"] = "STOPPED_REQUEST_ERROR"
            _atomic_replace(run_dir / "checkpoint.json", checkpoint)
            raise BackfillError(message) from error
        body = bytes(response.content)
        if key.encode() in body:
            ledger.append("SECRET_RESPONSE_BLOCKED", sequence=1, operation=OPERATION,
                          scope="3Y", route=redacted_route(plan, scope))
            checkpoint["status"] = "STOPPED_SECRET_RESPONSE"
            _atomic_replace(run_dir / "checkpoint.json", checkpoint)
            raise BackfillError("response body contains credential")
        captured = _now()
        body_hash = hashlib.sha256(body).hexdigest()
        ledger.append(
            "HTTP_RESPONSE", sequence=1, operation=OPERATION, scope="3Y",
            route=redacted_route(plan, scope), status_code=int(response.status_code),
            elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            response_bytes=len(body), response_sha256=body_hash,
            captured_at_utc=captured,
        )
        _immutable_bytes(landing, body)
        if response.status_code != 200:
            checkpoint.update(status="STOPPED_HTTP_ERROR", raw_requests=1,
                              landing_sha256=body_hash)
            _atomic_replace(run_dir / "checkpoint.json", checkpoint)
            raise BackfillError(f"ECOS HTTP {response.status_code}")
        frame = parse_response(
            body, plan, scope, capture_id=run_id, captured_at_utc=captured,
            landing_response_sha256=body_hash,
        )
        payload = json.loads(body)
        declared = int(payload[OPERATION]["list_total_count"])
        first_date, last_date = frame["date"].min(), frame["date"].max()
        if first_date != "1998-11-13" or last_date != "2026-08-13":
            checkpoint.update(status="STOPPED_ENDPOINT_MISMATCH", raw_requests=1,
                              landing_sha256=body_hash, rows_returned=len(frame),
                              first_date=first_date, last_date=last_date)
            _atomic_replace(run_dir / "checkpoint.json", checkpoint)
            raise BackfillError("historical response endpoints differ")
        summary = {
            "version": 1, "status": "PAGE_SEMANTICS_PASS_REVIEW_REQUIRED",
            "run_id": run_id, "operation": OPERATION, "tenor": "3Y",
            "requested_start": scope.start_date, "requested_end": scope.end_date,
            "requested_row_start": 1, "requested_row_end": plan.max_rows_per_request,
            "declared_total": declared, "returned_rows": len(frame),
            "unique_dates": int(frame["date"].nunique()),
            "first_date": first_date, "last_date": last_date,
            "landing_file": landing.name, "landing_bytes": len(body),
            "landing_sha256": body_hash, "captured_at_utc": captured,
            "retry_count": 0, "raw_requests": 1, "normalized_writes": 0,
            "publication_revision_semantics": "not_established_by_this_pilot",
        }
        _atomic_replace(run_dir / "page_semantics_summary.json", summary)
        checkpoint.update(
            status=summary["status"], raw_requests=1,
            landing_sha256=body_hash, rows_returned=len(frame),
            declared_total=declared, unique_dates=summary["unique_dates"],
            first_date=first_date, last_date=last_date,
            summary_sha256=_sha(run_dir / "page_semantics_summary.json"),
        )
        _atomic_replace(run_dir / "checkpoint.json", checkpoint)
        ledger.append("RUN_COMPLETED", status=checkpoint["status"], raw_requests=1,
                      rows_returned=len(frame), summary_sha256=checkpoint["summary_sha256"])
    return {"run_dir": str(run_dir), **checkpoint}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-call BOK ECOS 3Y page-semantics pilot")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approve-plan-sha256", required=True)
    parser.add_argument("--confirm-exact-one-live-request", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_exact_one_live_request:
        raise SystemExit("Refusing to run: exact one-request confirmation required")
    try:
        result = run_pilot(
            project_root=args.project_root.resolve(), plan_path=args.plan.resolve(),
            approve_plan_sha256=args.approve_plan_sha256,
        )
    except BackfillError as error:
        key = os.environ.get(API_KEY_ENV, "")
        raise SystemExit(str(error).replace(key, "<redacted>") if key else str(error)) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
