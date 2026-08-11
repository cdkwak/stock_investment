"""Bounded Landing-only OpenDART free-issue feasibility pilot.

This manual diagnostic must remain unexecuted until D approves the exact issuer,
date window and three-call budget. It writes no Normalized or canonical event.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.providers.opendart_free_issue import parse_observations, request_matrix


MAX_BUSINESS_REQUESTS = 3
MAX_RAW_HTTP_REQUESTS = 3
RETRY_COUNT = 0
LANDING_ROOT = ROOT / "data/landing/diagnostics/opendart_free_issue_pilot"


class PilotStopped(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_url(url: str) -> str:
    value = urlsplit(url)
    return urlunsplit((value.scheme, value.netloc, value.path, "", ""))


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_new(path: Path, body: bytes) -> None:
    if path.exists():
        raise PilotStopped(f"refusing to overwrite Landing: {path.name}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_ledger(path: Path, payload: object, api_key: str) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
    if api_key.encode() in encoded:
        raise PilotStopped("credential detected in ledger record")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run_pilot(*, corp_code: str, begin_date: str, end_date: str,
              landing_root: Path = LANDING_ROOT) -> dict[str, object]:
    api_key = os.getenv("OPENDART_API_KEY", "")
    if len(api_key) != 40:
        raise PilotStopped("OPENDART_API_KEY must be a 40-character environment value")
    matrix = request_matrix(corp_code, begin_date, end_date)
    if len(matrix) != MAX_BUSINESS_REQUESTS:
        raise AssertionError("fixed request matrix differs from hard budget")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = landing_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    ledger_path = run_dir / "call_ledger.jsonl"
    checkpoint_path = run_dir / "checkpoint.json"
    manifest = {
        "run_id": run_id, "dataset": "opendart_free_issue_source_pilot",
        "business_request_limit": 3, "raw_http_request_limit": 3,
        "retry_count": RETRY_COUNT, "parallelism": 1, "normalized_writes": False,
        "scope": {"corp_code": corp_code, "begin_date": begin_date, "end_date": end_date},
        "requests": [{"sequence": item.sequence, "operation": item.operation,
                      "endpoint": _safe_url(item.endpoint),
                      "public_parameters": dict(item.public_parameters)} for item in matrix],
    }
    _atomic_json(run_dir / "manifest.json", manifest)
    checkpoint: dict[str, object] = {
        "run_id": run_id, "status": "CREATED", "completed": {},
        "raw_http_requests": 0, "updated_at_utc": _utc_now(),
    }
    _atomic_json(checkpoint_path, checkpoint)
    session = requests.Session()
    try:
        for item in matrix:
            if int(checkpoint["raw_http_requests"]) >= MAX_RAW_HTTP_REQUESTS:
                raise PilotStopped("raw HTTP budget exhausted")
            checkpoint["raw_http_requests"] = int(checkpoint["raw_http_requests"]) + 1
            checkpoint.update({"status": "IN_PROGRESS", "active_operation": item.operation,
                               "updated_at_utc": _utc_now()})
            _atomic_json(checkpoint_path, checkpoint)
            _append_ledger(ledger_path, {
                "event": "REQUEST_STARTED", "recorded_at_utc": _utc_now(),
                "raw_sequence": checkpoint["raw_http_requests"],
                "operation": item.operation, "method": "GET",
                "url": _safe_url(item.endpoint),
            }, api_key)
            started = time.monotonic()
            response = session.get(
                item.endpoint, params={"crtfc_key": api_key, **item.public_parameters},
                timeout=20, allow_redirects=False,
            )
            captured_at_utc = _utc_now()
            body = response.content
            if api_key.encode() in body:
                raise PilotStopped("credential echoed in response; Landing write refused")
            body_path = run_dir / f"response_{item.sequence:02d}_{item.operation}.json"
            _atomic_new(body_path, body)
            digest = hashlib.sha256(body).hexdigest()
            _append_ledger(ledger_path, {
                "event": "HTTP_RESPONSE", "recorded_at_utc": _utc_now(),
                "raw_sequence": checkpoint["raw_http_requests"], "operation": item.operation,
                "method": "GET", "url": _safe_url(item.endpoint),
                "status_code": response.status_code,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                "response_bytes": len(body), "response_sha256": digest,
                "body_file": body_path.name,
            }, api_key)
            if response.status_code != 200:
                raise PilotStopped(f"HTTP status {response.status_code} for {item.operation}")
            classification, rows = parse_observations(
                item.operation, body, captured_at_utc=captured_at_utc,
            )
            completed = checkpoint["completed"]
            assert isinstance(completed, dict)
            completed[item.operation] = {
                "classification": classification, "rows": len(rows),
                "body_file": body_path.name, "body_sha256": digest,
            }
            checkpoint.pop("active_operation", None)
            checkpoint.update({"status": "IN_PROGRESS", "updated_at_utc": _utc_now()})
            _atomic_json(checkpoint_path, checkpoint)
            _append_ledger(ledger_path, {
                "event": "REQUEST_COMPLETED", "recorded_at_utc": _utc_now(),
                "operation": item.operation, "classification": classification,
                "rows": len(rows), "raw_http_requests": checkpoint["raw_http_requests"],
            }, api_key)
    except Exception as error:
        safe_error = str(error).replace(api_key, "[REDACTED]")
        checkpoint.update({"status": "STOPPED", "stop_reason": safe_error,
                           "updated_at_utc": _utc_now()})
        _atomic_json(checkpoint_path, checkpoint)
        _append_ledger(ledger_path, {"event": "RUN_STOPPED", "recorded_at_utc": _utc_now(),
                                    "error_type": type(error).__name__, "error": safe_error}, api_key)
        raise
    checkpoint.update({"status": "COMPLETE", "updated_at_utc": _utc_now()})
    _atomic_json(checkpoint_path, checkpoint)
    _append_ledger(ledger_path, {"event": "RUN_COMPLETED", "recorded_at_utc": _utc_now(),
                                "business_requests": 3, "raw_http_requests": 3}, api_key)
    for path in run_dir.iterdir():
        if path.is_file() and api_key.encode() in path.read_bytes():
            raise PilotStopped(f"credential detected in artifact: {path.name}")
    return {"run_dir": str(run_dir), "status": "COMPLETE", "raw_http_requests": 3}


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded Landing-only OpenDART free-issue pilot")
    parser.add_argument("--corp-code", required=True)
    parser.add_argument("--begin-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--confirm-live-manual-pilot", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_manual_pilot:
        print("Refusing to run: --confirm-live-manual-pilot is required", file=sys.stderr)
        return 2
    print(json.dumps(run_pilot(corp_code=args.corp_code, begin_date=args.begin_date,
                               end_date=args.end_date), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

