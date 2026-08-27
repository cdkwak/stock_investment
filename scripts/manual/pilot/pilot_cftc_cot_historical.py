"""Bounded, Landing-only CFTC COT historical pilot (two official annual files)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_data.providers.cftc import (
    DISAGGREGATED_FUTURES_ONLY_URL, TFF_FUTURES_ONLY_URL,
    CftcCotSchemaError, parse_historical_zip, summarize_target_coverage,
)
from stock_data.providers.public_http_capture import capture_public_response


LANDING_RELATIVE = Path("data/landing/cftc/cot_historical_pilot")
STATE_RELATIVE = Path("data/state/us_cftc_cot_historical_pilot")
YEAR = 2025
MAX_REQUESTS = 2


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(f".{path.name}.{uuid4().hex}.stage")
    with stage.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    stage.replace(path)


def _request(session, *, url: str, family: str, landing: Path):
    response = session.get(url, headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=60)
    receipt = capture_public_response(
        root=landing, provider="cftc", operation=f"cot_{family}_futures_only_annual_zip",
        request_url=url, request_parameters={}, response=response,
    )
    if int(response.status_code) != 200:
        raise CftcCotSchemaError(f"CFTC {family} HTTP {int(response.status_code)}")
    return bytes(response.content), receipt


def run(project_root: Path, *, session=None, year: int = YEAR) -> dict[str, object]:
    """Perform exactly two source captures and no Normalized/Canonical mutation."""
    if year != YEAR:
        raise ValueError(f"bounded pilot only permits year {YEAR}")
    landing_parent = project_root / LANDING_RELATIVE
    state_root = project_root / STATE_RELATIVE
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_root = landing_parent / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    lock = state_root / ".pilot.lock"
    state_root.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise CftcCotSchemaError("CFTC pilot lock is already held") from error
    calls: list[dict[str, object]] = []
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(run_id)
        session = session or requests.Session()
        source_bodies = {}
        for sequence, (family, url_template) in enumerate((
            ("tff", TFF_FUTURES_ONLY_URL), ("disaggregated", DISAGGREGATED_FUTURES_ONLY_URL),
        ), start=1):
            url = url_template.format(year=year)
            body, receipt = _request(session, url=url, family=family, landing=run_root)
            source_bodies[family] = body
            calls.append({
                "sequence": sequence, "family": family, "source_url": url,
                "landing": str(receipt.call_root.relative_to(run_root)),
                "captured_at_utc": receipt.captured_at_utc,
                "response_sha256": receipt.response_body_sha256,
                "response_bytes": receipt.response_bytes,
            })
        coverage = {
            family: summarize_target_coverage(parse_historical_zip(source_bodies[family], family=family), family=family)
            for family in ("tff", "disaggregated")
        }
        manifest = {
            "version": 1, "status": "PILOT_COMPLETE_REVIEW_REQUIRED",
            "provider": "cftc", "dataset_scope": "cot_futures_only_historical",
            "year": year, "run_id": run_id,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "raw_requests": len(calls), "retry_count": 0, "calls": calls,
            "target_coverage": coverage,
            "position_date_policy": "source As_of_Date_In_Form_YYMMDD retained in raw annual file",
            "release_date_policy": "not inferred; CFTC says historical release-date list is unavailable",
            "normalized_mutation": False, "canonical_mutation": False,
        }
        _atomic_json(run_root / "manifest.json", manifest)
        state = {
            "version": 1, "status": manifest["status"], "run_id": run_id,
            "landing_manifest": str((run_root / "manifest.json").relative_to(project_root)),
            "landing_manifest_sha256": _sha(run_root / "manifest.json"),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "normalized_mutation": False,
        }
        _atomic_json(state_root / "latest.json", state)
        return manifest
    except Exception as error:
        _atomic_json(run_root / "manifest.json", {
            "version": 1, "status": "PILOT_STOPPED_SCHEMA_OR_SOURCE_ERROR",
            "provider": "cftc", "dataset_scope": "cot_futures_only_historical",
            "year": year, "run_id": run_id,
            "stopped_at_utc": datetime.now(timezone.utc).isoformat(),
            "raw_requests": len(calls), "retry_count": 0, "calls": calls,
            "failure_class": type(error).__name__, "failure_detail": str(error),
            "normalized_mutation": False, "canonical_mutation": False,
        })
        raise
    finally:
        if lock.exists() and lock.read_text(encoding="utf-8") == run_id:
            lock.unlink()


def finalize_retained_run(project_root: Path, run_root: Path) -> dict[str, object]:
    """Revalidate an exactly-two-call stopped run with no network I/O.

    The stopped manifest is retained untouched.  A separate adoption record
    records the parser-only revalidation and retains the PIT restriction.
    """
    expected_parent = (project_root / LANDING_RELATIVE).resolve()
    run_root = run_root.resolve()
    if run_root.parent != expected_parent or not run_root.is_dir():
        raise CftcCotSchemaError("retained run must be an immediate CFTC pilot Landing child")
    stopped_path = run_root / "manifest.json"
    stopped = json.loads(stopped_path.read_text(encoding="utf-8"))
    calls = stopped.get("calls")
    if stopped.get("status") != "PILOT_STOPPED_SCHEMA_OR_SOURCE_ERROR" or not isinstance(calls, list) or len(calls) != MAX_REQUESTS:
        raise CftcCotSchemaError("retained run is not the expected stopped two-file pilot")
    payloads = {}
    for call in calls:
        family, relative = call.get("family"), call.get("landing")
        if family not in {"tff", "disaggregated"} or not isinstance(relative, str):
            raise CftcCotSchemaError("retained call record has an invalid family or Landing path")
        body = run_root / relative / "response.body"
        if not body.is_file() or _sha(body) != call.get("response_sha256"):
            raise CftcCotSchemaError("retained CFTC Landing body does not reconcile to its receipt")
        payloads[family] = body.read_bytes()
    if set(payloads) != {"tff", "disaggregated"}:
        raise CftcCotSchemaError("retained run does not have one TFF and one Disaggregated source")
    coverage = {
        family: summarize_target_coverage(parse_historical_zip(payloads[family], family=family), family=family)
        for family in ("tff", "disaggregated")
    }
    adoption = {
        "version": 1, "status": "PILOT_COMPLETE_REVIEW_REQUIRED",
        "provider": "cftc", "dataset_scope": "cot_futures_only_historical",
        "run_id": stopped["run_id"], "year": stopped["year"],
        "finalized_offline_at_utc": datetime.now(timezone.utc).isoformat(),
        "network_requests_during_finalization": 0, "raw_requests": MAX_REQUESTS,
        "prior_stopped_manifest_sha256": _sha(stopped_path), "calls": calls,
        "target_coverage": coverage,
        "position_date_policy": "source As_of_Date_In_Form_YYMMDD retained in raw annual file",
        "release_date_policy": "not inferred; CFTC says historical release-date list is unavailable",
        "normalized_mutation": False, "canonical_mutation": False,
    }
    _atomic_json(run_root / "adoption.json", adoption)
    _atomic_json(project_root / STATE_RELATIVE / "latest.json", {
        "version": 1, "status": adoption["status"], "run_id": adoption["run_id"],
        "landing_adoption": str((run_root / "adoption.json").relative_to(project_root)),
        "landing_adoption_sha256": _sha(run_root / "adoption.json"),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "normalized_mutation": False,
    })
    return adoption


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-live-two-file-pilot", action="store_true")
    parser.add_argument("--finalize-retained-run", type=Path)
    args = parser.parse_args(argv)
    if args.finalize_retained_run is not None:
        if args.confirm_live_two_file_pilot:
            raise SystemExit("offline finalization and live confirmation are mutually exclusive")
        try:
            result = finalize_retained_run(args.project_root.resolve(), args.finalize_retained_run)
        except CftcCotSchemaError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if not args.confirm_live_two_file_pilot:
        raise SystemExit("explicit live two-file confirmation is required")
    try:
        manifest = run(args.project_root.resolve())
    except (CftcCotSchemaError, requests.RequestException) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
