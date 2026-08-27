"""Exactly-two-call Landing-only FRED/ALFRED revision diagnostic."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from dotenv import load_dotenv
import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.pilot.fred_alfred_revision_pilot_support import (
    FredAlfredPilotError, SERIES_ID, compare_current_to_retained,
    parse_metadata, parse_revision_observations,
)
from stock_data.providers.public_http_capture import capture_public_response


BASE_URL = "https://api.stlouisfed.org/fred"
LANDING_RELATIVE = Path("data/landing/diagnostics/fred_alfred_revision_pilot")
API_KEY_ENV = "FRED_API_KEY"
OBSERVATION_START = "2026-07-01"
OBSERVATION_END = "2026-08-06"
MAX_REQUESTS = 2


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(run_root: Path, manifest: dict[str, object]) -> dict[str, object]:
    manifest_path = run_root / "manifest.json"
    with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    return {"run_root": str(run_root), "manifest_sha256": _sha(manifest_path), **manifest}


def _request(session, *, endpoint: str, public: dict[str, str], api_key: str, landing: Path):
    response = session.get(
        f"{BASE_URL}/{endpoint}", params={**public, "api_key": api_key},
        headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=30,
    )
    if api_key.encode("utf-8") in bytes(response.content):
        raise FredAlfredPilotError("response unexpectedly contains the credential")
    receipt = capture_public_response(
        root=landing, provider="fred", operation=endpoint.replace("/", "_"),
        request_url=f"{BASE_URL}/{endpoint}", request_parameters=public,
        response=response,
    )
    if int(response.status_code) != 200:
        raise FredAlfredPilotError(f"FRED HTTP {int(response.status_code)}")
    return bytes(response.content), receipt


def run(project_root: Path, *, session=None) -> dict[str, object]:
    load_dotenv(project_root / ".env", override=False)
    api_key = os.environ.get(API_KEY_ENV, "")
    if not re.fullmatch(r"[a-z0-9]{32}", api_key):
        raise FredAlfredPilotError("FRED_API_KEY is missing or not the documented 32-character form")
    root = project_root / LANDING_RELATIVE
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_root = root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    lock = root / ".pilot.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise FredAlfredPilotError("FRED/ALFRED pilot lock is already held") from error
    calls = []
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(run_id)
        session = session or requests.Session()
        metadata_params = {"series_id": SERIES_ID, "file_type": "json"}
        metadata_body, metadata_receipt = _request(
            session, endpoint="series", public=metadata_params, api_key=api_key,
            landing=run_root,
        )
        metadata = parse_metadata(metadata_body)
        calls.append({"sequence": 1, "operation": "series", "landing": str(metadata_receipt.call_root.relative_to(run_root)), "sha256": metadata_receipt.response_body_sha256})
        observation_params = {
            "series_id": SERIES_ID, "file_type": "json", "output_type": "2",
            "realtime_start": "1776-07-04", "realtime_end": "9999-12-31",
            "observation_start": OBSERVATION_START, "observation_end": OBSERVATION_END,
            "units": "lin", "sort_order": "asc", "limit": "128",
        }
        observation_body, observation_receipt = _request(
            session, endpoint="series/observations", public=observation_params,
            api_key=api_key, landing=run_root,
        )
        observations = parse_revision_observations(observation_body)
        calls.append({"sequence": 2, "operation": "series/observations", "landing": str(observation_receipt.call_root.relative_to(run_root)), "sha256": observation_receipt.response_body_sha256})
        comparison = compare_current_to_retained(
            observations, project_root / "data/normalized/fred_treasury_yield_daily",
        )
        manifest = {
            "version": 1, "status": "PILOT_COMPLETE_REVIEW_REQUIRED",
            "source": "fred_alfred_api", "series_id": SERIES_ID,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "raw_requests": len(calls), "retry_count": 0,
            "credential": {"environment_variable": API_KEY_ENV, "configured": True, "value_persisted": False},
            "metadata": vars(metadata), "observation_scope": observation_params,
            "observations_returned": len(observations),
            "revision_rows": sum(row["realtime_start"] != row["realtime_end"] or row["realtime_end"] != "9999-12-31" for row in observations),
            "comparison_to_retained": comparison, "calls": calls,
            "normalized_mutation": False,
        }
        return _write_manifest(run_root, manifest)
    finally:
        if lock.exists() and lock.read_text(encoding="utf-8") == run_id:
            lock.unlink()


def finalize_failed_scope_offline(project_root: Path, run_root: Path) -> dict[str, object]:
    """Bind the exact retained two-call HTTP-400 run without new network I/O."""
    expected_parent = (project_root / LANDING_RELATIVE).resolve()
    run_root = run_root.resolve()
    if run_root.parent != expected_parent or not run_root.is_dir():
        raise FredAlfredPilotError("failed run must be an immediate pilot Landing child")
    records = []
    for path in run_root.rglob("call.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        body_path = path.with_name(str(record.get("landing_body_file", "")))
        if not body_path.is_file() or _sha(body_path) != record.get("response_body_sha256"):
            raise FredAlfredPilotError("Landing body and call record do not reconcile")
        if body_path.stat().st_size != record.get("response_bytes"):
            raise FredAlfredPilotError("Landing byte count differs")
        records.append((record, body_path))
    records.sort(key=lambda pair: str(pair[0].get("captured_at_utc", "")))
    if len(records) != MAX_REQUESTS:
        raise FredAlfredPilotError("failed run does not contain exactly two calls")
    first, second = records
    if first[0].get("operation") != "series" or first[0].get("http_status") != 200:
        raise FredAlfredPilotError("first call is not the successful metadata request")
    if second[0].get("operation") != "series_observations" or second[0].get("http_status") != 400:
        raise FredAlfredPilotError("second call is not the fail-closed observation request")
    metadata = parse_metadata(first[1].read_bytes())
    error_payload = json.loads(second[1].read_bytes())
    if error_payload.get("error_code") != 400 or "exceeds the maximum number of vintage dates" not in str(error_payload.get("error_message", "")):
        raise FredAlfredPilotError("HTTP-400 payload is not the verified vintage-date cap")
    calls = [{
        "sequence": sequence,
        "operation": record["operation"],
        "http_status": record["http_status"],
        "landing": str(body.relative_to(run_root)),
        "sha256": record["response_body_sha256"],
    } for sequence, (record, body) in enumerate(records, start=1)]
    manifest = {
        "version": 1, "status": "PILOT_STOPPED_SCOPE_TOO_BROAD",
        "source": "fred_alfred_api", "series_id": SERIES_ID,
        "finalized_offline_at_utc": datetime.now(timezone.utc).isoformat(),
        "network_requests_during_finalization": 0,
        "raw_requests": MAX_REQUESTS, "retry_count": 0,
        "credential": {"environment_variable": API_KEY_ENV, "configured": True, "value_persisted": False},
        "metadata": vars(metadata),
        "failure_classification": "ALFRED_VINTAGE_DATE_LIMIT_EXCEEDED",
        "normalized_mutation": False, "calls": calls,
    }
    return _write_manifest(run_root, manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-live-two-call-pilot", action="store_true")
    parser.add_argument("--finalize-failed-run", type=Path)
    args = parser.parse_args(argv)
    if args.finalize_failed_run is not None:
        if args.confirm_live_two_call_pilot:
            raise SystemExit("offline finalization and live confirmation are mutually exclusive")
        try:
            result = finalize_failed_scope_offline(
                args.project_root.resolve(), args.finalize_failed_run,
            )
        except FredAlfredPilotError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if not args.confirm_live_two_call_pilot:
        raise SystemExit("explicit live two-call confirmation is required")
    try:
        result = run(args.project_root.resolve())
    except (FredAlfredPilotError, requests.RequestException) as error:
        key = os.environ.get(API_KEY_ENV, "")
        raise SystemExit(str(error).replace(key, "<redacted>")) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
