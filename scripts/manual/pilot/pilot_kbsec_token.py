"""Execute exactly one retry-free KB Securities OAuth access sentinel."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.pilot.kbsec_token_pilot_support import (
    SCHEMA,
    OneCallCaptureSession,
    atomic_json,
    iso_utc,
    response_evidence,
    secret_scan,
    utc_now,
)
from stock_data.providers.kbsec.client import KBSecClient, KBSecError


REQUIRED = ("KBSEC_BASE_URL", "KBSEC_APP_KEY", "KBSEC_APP_SECRET")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live-token", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    load_dotenv(args.root / ".env", override=False)
    readiness = {name: bool(os.getenv(name, "").strip()) for name in REQUIRED}
    if not args.confirm_live_token or not all(readiness.values()):
        print(json.dumps({"status": "NOT_EXECUTED", "credentials": readiness}, sort_keys=True))
        return 2

    run_id = utc_now().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = args.root / "data/landing/diagnostics/kbsec_token_pilot" / run_id
    lock_path = args.root / "data/state/locks/kbsec_token_pilot.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        print(json.dumps({"status": "NOT_EXECUTED", "reason": "LOCKED"}))
        return 3
    os.write(descriptor, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode("utf-8"))
    os.close(descriptor)

    started = utc_now()
    session = OneCallCaptureSession()
    status = "STOPPED_NO_RESPONSE"
    error_type = None
    try:
        client = KBSecClient(session=session)
        client.access_token()
        status = "TOKEN_OK_AUDIT_REQUIRED"
    except KBSecError as error:
        status = "TOKEN_FAILED"
        error_type = type(error).__name__
    finally:
        completed = utc_now()
        secrets = tuple(os.getenv(name, "") for name in REQUIRED)
        landing = response_evidence(session.captured_response, known_secrets=secrets)
        response_path = run_dir / "response.redacted.json"
        ledger_path = run_dir / "call_ledger.jsonl"
        checkpoint_path = run_dir / "checkpoint.json"
        atomic_json(response_path, {"schema": SCHEMA + ".safe_landing", "version": 1, **landing})
        ledger = {
            "schema": SCHEMA + ".call_ledger", "version": 1, "run_id": run_id,
            "provider": "kb_securities_open_api", "operation": "oauth2/token",
            "attempt": 1, "retry_count": 0, "started_at_utc": iso_utc(started),
            "completed_at_utc": iso_utc(completed), "http_status": landing.get("http_status"),
            "response_sha256": landing.get("raw_response_sha256"), "outcome": status,
            "request_envelope_keys": ["dataHeader", "dataBody"],
            "request_data_body_keys": ["appKey", "appSecret", "grantType"],
            "credential_names": list(REQUIRED), "credential_values_persisted": False,
        }
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        checkpoint = {
            "schema": SCHEMA + ".checkpoint", "version": 1, "run_id": run_id,
            "status": status, "error_type": error_type, "request_count": session.request_count,
            "retry_count": 0, "completed_at_utc": iso_utc(completed),
            "landing_relative_path": response_path.relative_to(args.root).as_posix(),
            "ledger_relative_path": ledger_path.relative_to(args.root).as_posix(),
        }
        atomic_json(checkpoint_path, checkpoint)
        paths = [response_path, ledger_path, checkpoint_path]
        scan_ok = secret_scan(paths, secrets)
        if not scan_ok:
            status = "DATA_INTEGRITY_FAILURE"
            checkpoint["status"] = status
            atomic_json(checkpoint_path, checkpoint)
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            if owner.get("run_id") == run_id:
                lock_path.unlink()
        except (FileNotFoundError, ValueError, OSError):
            pass

    print(json.dumps({
        "status": status, "run_id": run_id, "request_count": session.request_count,
        "retry_count": 0, "http_status": landing.get("http_status"),
        "response_sha256": landing.get("raw_response_sha256"), "secret_scan": scan_ok,
    }, sort_keys=True))
    return 0 if status == "TOKEN_OK_AUDIT_REQUIRED" else 4


if __name__ == "__main__":
    sys.exit(main())
