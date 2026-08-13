"""Prepared one-call historical ALFRED revision-detection pilot; no implicit I/O."""
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


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.fred_alfred_revision_pilot_support import (
    FredAlfredPilotError, parse_revision_observations,
)
from stock_data.providers.public_http_capture import capture_public_response


URL = "https://api.stlouisfed.org/fred/series/observations"
API_KEY_ENV = "FRED_API_KEY"
LANDING_RELATIVE = Path("data/landing/diagnostics/fred_alfred_historical_revision")
SERIES_ID = "DGS10"
REALTIME_START = "2008-09-12"
REALTIME_END = "2008-09-26"
OBSERVATION_START = "2008-08-15"
OBSERVATION_END = "2008-09-25"
MAX_ROWS = 128


def public_parameters() -> dict[str, str]:
    return {
        "series_id": SERIES_ID, "file_type": "json", "output_type": "1",
        "realtime_start": REALTIME_START, "realtime_end": REALTIME_END,
        "observation_start": OBSERVATION_START, "observation_end": OBSERVATION_END,
        "units": "lin", "sort_order": "asc", "limit": str(MAX_ROWS),
    }


def validate_response(body: bytes) -> tuple[dict[str, object], ...]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FredAlfredPilotError("historical response is not JSON") from error
    expected = public_parameters()
    for field in ("realtime_start", "realtime_end", "observation_start", "observation_end", "units", "sort_order"):
        if payload.get(field) != expected[field]:
            raise FredAlfredPilotError(f"historical response {field} differs")
    if payload.get("output_type") != 1:
        raise FredAlfredPilotError("historical response output_type differs")
    try:
        count, offset, limit = int(payload["count"]), int(payload["offset"]), int(payload["limit"])
    except (KeyError, TypeError, ValueError) as error:
        raise FredAlfredPilotError("historical pagination metadata is invalid") from error
    rows = parse_revision_observations(body)
    if count != len(rows) or offset != 0 or limit != MAX_ROWS or count > MAX_ROWS:
        raise FredAlfredPilotError("historical response is incomplete")
    keys = [(row["date"], row["realtime_start"], row["realtime_end"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise FredAlfredPilotError("historical real-time row key is duplicated")
    if any(not OBSERVATION_START <= str(row["date"]) <= OBSERVATION_END for row in rows):
        raise FredAlfredPilotError("historical observation date exceeds scope")
    if any(not REALTIME_START <= str(row["realtime_start"]) <= str(row["realtime_end"]) <= REALTIME_END for row in rows):
        raise FredAlfredPilotError("historical real-time interval exceeds scope")
    return rows


def run(project_root: Path, *, session=None) -> dict[str, object]:
    load_dotenv(project_root / ".env", override=False)
    key = os.environ.get(API_KEY_ENV, "")
    if not re.fullmatch(r"[a-z0-9]{32}", key):
        raise FredAlfredPilotError("FRED_API_KEY is missing or malformed")
    root = project_root / LANDING_RELATIVE
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_root = root / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    lock = root / ".pilot.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise FredAlfredPilotError("historical ALFRED pilot lock is already held") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(run_id)
        response = (session or requests.Session()).get(
            URL, params={**public_parameters(), "api_key": key},
            headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=30,
        )
        body = bytes(response.content)
        if key.encode() in body:
            raise FredAlfredPilotError("response unexpectedly contains the credential")
        receipt = capture_public_response(
            root=run_root, provider="fred", operation="series_observations",
            request_url=URL, request_parameters=public_parameters(), response=response,
        )
        if int(response.status_code) != 200:
            raise FredAlfredPilotError(f"FRED HTTP {int(response.status_code)}")
        rows = validate_response(body)
        versions: dict[str, set[float | None]] = {}
        for row in rows:
            versions.setdefault(str(row["date"]), set()).add(row["numeric_value"])
        manifest = {
            "version": 1, "status": "PILOT_COMPLETE_REVIEW_REQUIRED",
            "source": "fred_alfred_api", "series_id": SERIES_ID,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "raw_requests": 1, "retry_count": 0,
            "credential": {"environment_variable": API_KEY_ENV, "configured": True, "value_persisted": False},
            "request_parameters": public_parameters(), "rows": len(rows),
            "unique_observation_dates": len(versions),
            "dates_with_multiple_value_versions": sum(len(values) > 1 for values in versions.values()),
            "call": {"landing": str(receipt.call_root.relative_to(run_root)), "sha256": receipt.response_body_sha256},
            "normalized_mutation": False,
            "interpretation_gate": "contract activation requires useful revision evidence or an explicit provenance-only decision",
        }
        path = run_root / "manifest.json"
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        return {"run_root": str(run_root), "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **manifest}
    finally:
        if lock.exists() and lock.read_text(encoding="utf-8") == run_id:
            lock.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-live-one-call-pilot", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_live_one_call_pilot:
        raise SystemExit("explicit live one-call confirmation is required")
    try:
        result = run(args.project_root.resolve())
    except (FredAlfredPilotError, requests.RequestException) as error:
        key = os.environ.get(API_KEY_ENV, "")
        raise SystemExit(str(error).replace(key, "<redacted>")) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
