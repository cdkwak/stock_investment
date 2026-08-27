"""Explicit six-request BOK ECOS Treasury historical backfill.

No I/O occurs on import. Live use requires an approved plan digest and explicit
confirmation. Responses are immutable Landing before parsing; retry count is 0.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time
from uuid import uuid4

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.manual.backfill.bok_ecos_treasury_backfill_support import (
    BackfillError, CONTRACT, MAX_REQUESTS, MAX_THROTTLE_SECONDS,
    MIN_THROTTLE_SECONDS, OPERATION, load_plan, parse_response, plan_sha256,
    redacted_route, request_url,
)
from scripts.manual.pilot.pilot_bok_ecos_treasury import (
    Ledger, PilotStopped, _atomic_replace, _immutable_bytes, _lock, _sha,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1


API_KEY_ENV = "BOK_ECOS_API_KEY"
LANDING_RELATIVE = Path("data/landing/bok_ecos_kr_treasury_yield_source_observation")
NORMALIZED_RELATIVE = Path("data/normalized") / CONTRACT.name
STATE_RELATIVE = Path("data/state") / f"{CONTRACT.name}.json"
TIMEOUT_SECONDS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata_summary(project_root: Path, digest: str, plan) -> Path:
    candidates = []
    root = project_root / "data/landing/diagnostics/bok_ecos_treasury_pilot"
    for path in root.glob("metadata_*/metadata_summary.json"):
        if _sha(path) == digest:
            candidates.append(path)
    if len(candidates) != 1:
        raise BackfillError("exactly one approved metadata summary must be retained")
    summary = json.loads(candidates[0].read_text(encoding="utf-8"))
    rows = summary.get("six_tenor_identity")
    if not isinstance(rows, list) or len(rows) != MAX_REQUESTS:
        raise BackfillError("approved metadata does not contain six tenors")
    by_tenor = {row.get("tenor"): row for row in rows if isinstance(row, dict)}
    for scope in plan.scopes:
        row = by_tenor.get(scope.tenor, {})
        if (
            row.get("STAT_CODE") != plan.table_code
            or row.get("STAT_NAME") != plan.table_name
            or row.get("ITEM_CODE") != scope.item_code
            or row.get("ITEM_NAME") != scope.item_name
            or row.get("CYCLE") != plan.cycle
            or row.get("UNIT_NAME") != scope.unit_name
            or row.get("START_TIME") != scope.start_date
            or row.get("END_TIME") != scope.end_date
        ):
            raise BackfillError("backfill plan differs from approved metadata")
    return candidates[0]


class Client:
    def __init__(self, session, ledger: Ledger, key: str, initial: int, max_requests: int):
        self.session, self.ledger, self.key = session, ledger, key
        self.requests, self.max_requests = initial, max_requests

    def get(self, plan, scope, sequence: int, landing: Path) -> tuple[bytes, str]:
        if self.requests >= self.max_requests:
            raise BackfillError("hard live-request cap reached")
        self.requests += 1
        started = time.monotonic()
        try:
            response = self.session.get(request_url(self.key, plan, scope), timeout=TIMEOUT_SECONDS)
        except requests.RequestException as error:
            message = str(error).replace(self.key, "<redacted>")
            self.ledger.append("HTTP_ERROR", sequence=sequence, operation=OPERATION,
                               scope=scope.tenor, route=redacted_route(plan, scope), error=message)
            raise BackfillError(message) from error
        body = bytes(response.content)
        if self.key.encode() in body:
            self.ledger.append("SECRET_RESPONSE_BLOCKED", sequence=sequence,
                               operation=OPERATION, scope=scope.tenor,
                               route=redacted_route(plan, scope))
            raise BackfillError("response body contains credential")
        captured = _now()
        digest = hashlib.sha256(body).hexdigest()
        self.ledger.append(
            "HTTP_RESPONSE", sequence=sequence, operation=OPERATION,
            scope=scope.tenor, route=redacted_route(plan, scope),
            status_code=int(response.status_code),
            elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            response_bytes=len(body), response_sha256=digest,
            captured_at_utc=captured,
        )
        _immutable_bytes(landing, body)
        if response.status_code != 200:
            raise BackfillError(f"ECOS HTTP {response.status_code}")
        return body, captured


def _resume_count(ledger_path: Path, completed: dict[str, object]) -> int:
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    if any(row.get("event") in {"HTTP_ERROR", "SECRET_RESPONSE_BLOCKED"} for row in rows):
        raise BackfillError("retry0 forbids resume after request failure")
    responses = [row for row in rows if row.get("event") == "HTTP_RESPONSE"]
    if [row.get("sequence") for row in responses] != list(range(1, len(responses) + 1)):
        raise BackfillError("ledger response sequence differs")
    live_completed = [value for value in completed.values() if not value.get("adopted")]
    if len(responses) != len(live_completed):
        raise BackfillError("ledger/checkpoint counts differ")
    return len(responses)


def _adopted_3y_evidence(project_root: Path, run_dir: Path, plan) -> dict[str, object]:
    root = project_root / "data/landing/diagnostics/bok_ecos_treasury_page_semantics"
    directory = run_dir.resolve()
    if directory.parent != root.resolve() or not directory.name.startswith("run_"):
        raise BackfillError("3Y adoption run directory differs")
    names = {
        "checkpoint": directory / "checkpoint.json",
        "ledger": directory / "call_ledger.jsonl",
        "summary": directory / "page_semantics_summary.json",
        "landing": directory / "response_01_3Y_19981113_20260813.json",
    }
    if not all(path.is_file() for path in names.values()):
        raise BackfillError("3Y adoption evidence is incomplete")
    checkpoint = json.loads(names["checkpoint"].read_text(encoding="utf-8"))
    summary = json.loads(names["summary"].read_text(encoding="utf-8"))
    ledger = [json.loads(line) for line in names["ledger"].read_text(encoding="utf-8").splitlines()]
    body = names["landing"].read_bytes()
    body_hash = hashlib.sha256(body).hexdigest()
    summary_hash = _sha(names["summary"])
    if [row.get("event") for row in ledger] != ["RUN_CREATED", "HTTP_RESPONSE", "RUN_COMPLETED"]:
        raise BackfillError("3Y adoption ledger events differ")
    response = ledger[1]
    if (
        checkpoint.get("status") != "PAGE_SEMANTICS_PASS_REVIEW_REQUIRED"
        or checkpoint.get("scope") != "3Y_19981113_20260813"
        or checkpoint.get("plan_sha256") != plan_sha256(plan)
        or checkpoint.get("metadata_summary_sha256") != plan.metadata_summary_sha256
        or checkpoint.get("raw_requests") != 1
        or checkpoint.get("retry_count") != 0
        or checkpoint.get("normalized_writes") != 0
        or checkpoint.get("landing_sha256") != body_hash
        or checkpoint.get("summary_sha256") != summary_hash
        or response.get("sequence") != 1
        or response.get("status_code") != 200
        or response.get("operation") != OPERATION
        or response.get("response_bytes") != len(body)
        or response.get("response_sha256") != body_hash
    ):
        raise BackfillError("3Y adoption checkpoint/Landing/ledger differs")
    scope = next(value for value in plan.scopes if value.tenor == "3Y")
    frame = parse_response(
        body, plan, scope, capture_id=checkpoint["run_id"],
        captured_at_utc=response["captured_at_utc"],
        landing_response_sha256=body_hash,
    )
    if (
        summary.get("declared_total") != len(frame)
        or summary.get("returned_rows") != len(frame)
        or summary.get("unique_dates") != frame["date"].nunique()
        or summary.get("first_date") != frame["date"].min()
        or summary.get("last_date") != frame["date"].max()
        or summary.get("raw_requests") != 1
        or summary.get("retry_count") != 0
        or summary.get("normalized_writes") != 0
    ):
        raise BackfillError("3Y adoption summary differs")
    return {
        "body": body, "landing_sha256": body_hash,
        "captured_at_utc": response["captured_at_utc"],
        "capture_id": checkpoint["run_id"], "rows": len(frame),
        "first_date": frame["date"].min(), "last_date": frame["date"].max(),
        "source_run": str(directory.relative_to(project_root)).replace("\\", "/"),
        "source_ledger_sha256": _sha(names["ledger"]),
        "source_checkpoint_sha256": _sha(names["checkpoint"]),
        "source_summary_sha256": summary_hash,
    }


def _merge_existing(project_root: Path, captured: pd.DataFrame) -> pd.DataFrame:
    root = project_root / NORMALIZED_RELATIVE
    validator = lambda frame: validate_data_v1(frame, CONTRACT, allow_empty=False)
    try:
        existing = read_dataset(root, CONTRACT, validator)
    except FileNotFoundError:
        existing = pd.DataFrame(columns=CONTRACT.column_names)
    merged = pd.concat([existing, captured], ignore_index=True)
    duplicated = merged.duplicated(list(CONTRACT.primary_key), keep=False)
    if duplicated.any():
        for _, group in merged[duplicated].groupby(list(CONTRACT.primary_key), dropna=False):
            if len(group.drop_duplicates()) != 1:
                raise BackfillError("existing observation primary key differs")
        merged = merged.drop_duplicates(list(CONTRACT.primary_key), keep="first")
    merged = merged.sort_values(list(CONTRACT.sort_key), kind="stable").reset_index(drop=True)
    validator(merged)
    return merged


def run_backfill(
    *, project_root: Path, plan_path: Path, approve_plan_sha256: str,
    resume_run_dir: Path | None = None, adopt_3y_page_run_dir: Path | None = None,
    session=None, sleep_fn=time.sleep,
    jitter_fn=random.uniform,
) -> dict[str, object]:
    plan = load_plan(plan_path)
    actual_plan_hash = plan_sha256(plan)
    if approve_plan_sha256 != actual_plan_hash:
        raise BackfillError("approved plan digest differs")
    metadata_path = _metadata_summary(project_root, plan.metadata_summary_sha256, plan)
    key = os.environ.get(API_KEY_ENV, "")
    if not key:
        raise BackfillError(f"{API_KEY_ENV} is required")
    landing_root = project_root / LANDING_RELATIVE
    adoption = None
    if resume_run_dir is None:
        if adopt_3y_page_run_dir is not None:
            adoption = _adopted_3y_evidence(project_root, adopt_3y_page_run_dir, plan)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
        run_dir = landing_root / f"run_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=False)
        checkpoint = {
            "version": 1, "dataset": CONTRACT.name, "contract_version": CONTRACT.version,
            "run_id": run_id, "plan_sha256": actual_plan_hash,
            "metadata_summary_sha256": plan.metadata_summary_sha256,
            "status": "CREATED", "max_raw_requests": MAX_REQUESTS - (1 if adoption else 0),
            "adopted_scopes": ["3Y"] if adoption else [], "completed": {},
        }
        _atomic_replace(run_dir / "checkpoint.json", checkpoint)
    else:
        run_dir = resume_run_dir.resolve()
        if run_dir.parent != landing_root.resolve() or not run_dir.name.startswith("run_"):
            raise BackfillError("resume run directory differs")
        checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
        run_id = run_dir.name.removeprefix("run_")
        if (
            checkpoint.get("run_id") != run_id
            or checkpoint.get("plan_sha256") != actual_plan_hash
            or checkpoint.get("metadata_summary_sha256") != plan.metadata_summary_sha256
            or checkpoint.get("status") not in {"CREATED", "IN_PROGRESS"}
        ):
            raise BackfillError("resume checkpoint identity differs")
    ledger_path = run_dir / "call_ledger.jsonl"
    completed = checkpoint["completed"]
    initial = _resume_count(ledger_path, completed) if ledger_path.exists() else 0
    ledger = Ledger(ledger_path, secrets=(key,))
    frames = []
    with _lock(landing_root, run_id):
        ledger.append("RUN_RESUMED" if resume_run_dir else "RUN_CREATED", run_id=run_id,
                      plan_sha256=actual_plan_hash, metadata_summary_file=str(metadata_path.relative_to(project_root)))
        if adoption is not None:
            landing = run_dir / "response_02_3Y.json"
            _immutable_bytes(landing, adoption["body"])
            completed["3Y"] = {
                "landing_file": landing.name, "landing_sha256": adoption["landing_sha256"],
                "captured_at_utc": adoption["captured_at_utc"],
                "capture_id": adoption["capture_id"], "rows": adoption["rows"],
                "first_date": adoption["first_date"], "last_date": adoption["last_date"],
                "adopted": True, "source_run": adoption["source_run"],
                "source_ledger_sha256": adoption["source_ledger_sha256"],
                "source_checkpoint_sha256": adoption["source_checkpoint_sha256"],
                "source_summary_sha256": adoption["source_summary_sha256"],
            }
            checkpoint["status"] = "IN_PROGRESS"
            _atomic_replace(run_dir / "checkpoint.json", checkpoint)
            ledger.append(
                "ADOPTED_RESPONSE", scope="3Y", operation=OPERATION,
                landing_file=landing.name, response_sha256=adoption["landing_sha256"],
                rows=adoption["rows"], source_run=adoption["source_run"],
                source_ledger_sha256=adoption["source_ledger_sha256"],
                source_checkpoint_sha256=adoption["source_checkpoint_sha256"],
                source_summary_sha256=adoption["source_summary_sha256"],
                network_requests=0,
            )
        client = Client(
            session or requests.Session(), ledger, key, initial,
            int(checkpoint["max_raw_requests"]),
        )
        for sequence, scope in enumerate(plan.scopes, 1):
            landing = run_dir / f"response_{sequence:02d}_{scope.tenor}.json"
            if scope.tenor in completed:
                entry = completed[scope.tenor]
                if not landing.is_file() or _sha(landing) != entry.get("landing_sha256"):
                    raise BackfillError("completed Landing hash differs")
                body, captured = landing.read_bytes(), entry["captured_at_utc"]
            else:
                if landing.exists():
                    raise BackfillError("uncheckpointed Landing cannot be adopted")
                if client.requests:
                    sleep_fn(jitter_fn(MIN_THROTTLE_SECONDS, MAX_THROTTLE_SECONDS))
                body, captured = client.get(plan, scope, client.requests + 1, landing)
                frame = parse_response(
                    body, plan, scope, capture_id=run_id, captured_at_utc=captured,
                    landing_response_sha256=_sha(landing),
                )
                completed[scope.tenor] = {
                    "landing_file": landing.name, "landing_sha256": _sha(landing),
                    "captured_at_utc": captured, "capture_id": run_id, "rows": len(frame),
                    "first_date": frame["date"].min(), "last_date": frame["date"].max(),
                }
                checkpoint["status"] = "IN_PROGRESS"
                _atomic_replace(run_dir / "checkpoint.json", checkpoint)
            frame = parse_response(
                body, plan, scope, capture_id=completed[scope.tenor].get("capture_id", run_id),
                captured_at_utc=captured,
                landing_response_sha256=_sha(landing),
            )
            if len(frame) != completed[scope.tenor]["rows"]:
                raise BackfillError("completed row count differs")
            frames.append(frame)
        captured_frame = pd.concat(frames, ignore_index=True).sort_values(
            list(CONTRACT.sort_key), kind="stable"
        ).reset_index(drop=True)
        merged = _merge_existing(project_root, captured_frame)
        validator = lambda frame: validate_data_v1(frame, CONTRACT, allow_empty=False)
        write_dataset_atomic(merged, project_root / NORMALIZED_RELATIVE, CONTRACT, validator)
        files = sorted((project_root / NORMALIZED_RELATIVE).rglob("data.parquet"))
        state = {
            "version": 1, "dataset": CONTRACT.name, "contract_version": CONTRACT.version,
            "status": "ARTIFACT_COMPLETE_PROVENANCE_LIMITED",
            "run_id": run_id, "plan_sha256": actual_plan_hash,
            "metadata_summary_sha256": plan.metadata_summary_sha256,
            "raw_requests": int(checkpoint["max_raw_requests"]),
            "source_responses": MAX_REQUESTS,
            "adopted_scopes": checkpoint.get("adopted_scopes", []),
            "rows_in_capture": len(captured_frame),
            "rows_total": len(merged), "first_date": merged["date"].min(),
            "last_date": merged["date"].max(),
            "publication_revision_semantics": "unknown; predictive use blocked",
            "parquet": [{"path": str(path.relative_to(project_root)).replace("\\", "/"),
                         "bytes": path.stat().st_size, "sha256": _sha(path)} for path in files],
            "completed_at_utc": _now(),
        }
        _atomic_replace(project_root / STATE_RELATIVE, state)
        checkpoint.update({
            "status": "DATA_COMPLETE_REVIEW_REQUIRED",
            "raw_requests": int(checkpoint["max_raw_requests"]),
            "source_responses": MAX_REQUESTS,
            "rows_in_capture": len(captured_frame), "rows_total": len(merged),
            "state_sha256": _sha(project_root / STATE_RELATIVE),
        })
        _atomic_replace(run_dir / "checkpoint.json", checkpoint)
        ledger.append("RUN_COMPLETED", status=checkpoint["status"],
                      raw_requests=int(checkpoint["max_raw_requests"]),
                      source_responses=MAX_REQUESTS,
                      rows_in_capture=len(captured_frame), rows_total=len(merged),
                      state_sha256=checkpoint["state_sha256"])
    return {"run_dir": str(run_dir), **checkpoint}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded BOK ECOS Treasury historical backfill")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approve-plan-sha256", required=True)
    parser.add_argument("--resume-run-dir", type=Path)
    parser.add_argument("--adopt-3y-page-run-dir", type=Path)
    parser.add_argument("--confirm-live-historical-backfill", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_live_historical_backfill:
        raise SystemExit("Refusing to run: explicit live historical confirmation required")
    try:
        result = run_backfill(
            project_root=args.project_root.resolve(), plan_path=args.plan.resolve(),
            approve_plan_sha256=args.approve_plan_sha256,
            resume_run_dir=args.resume_run_dir,
            adopt_3y_page_run_dir=args.adopt_3y_page_run_dir,
        )
    except (BackfillError, PilotStopped, requests.RequestException) as error:
        key = os.environ.get(API_KEY_ENV, "")
        raise SystemExit(str(error).replace(key, "<redacted>") if key else str(error)) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
