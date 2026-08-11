"""Bounded Landing-only authenticated KRX ETF feasibility pilot."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.pykrx_etf_pilot_support import (
    AUTH_ENDPOINT_PATHS, BUSINESS_ENDPOINT_PATH, BusinessThrottle, BudgetExceeded,
    HTTP_TIMEOUT_SECONDS, MAX_BUSINESS_REQUESTS, MAX_RAW_HTTP_REQUESTS,
    PROBE_MATRIX, PYKRX_VERSION, AppendOnlyLedger, PilotStopped, ProbeSpec,
    assert_no_credentials, classify_business_body, landing_name, matrix_sha256,
    redact, safe_url, shared_d_owned_krx_lock, utc_now, write_bytes_atomic_new,
    write_json_atomic,
)


LANDING_ROOT = ROOT / "data/landing/diagnostics/pykrx_etf_pilot"
D_OWNED_LOCK_PATH = ROOT / "data/state/d_owned_krx_short_selling.lock"


def _load_credentials(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                key, value = raw.split("=", 1)
                if key.strip() in {"KRX_ID", "KRX_PW"}:
                    values[key.strip()] = value.strip().strip("\"'")
    for key in ("KRX_ID", "KRX_PW"):
        if not os.getenv(key) and values.get(key):
            os.environ[key] = values[key]
    return os.getenv("KRX_ID", ""), os.getenv("KRX_PW", "")


class HttpCapture:
    def __init__(self, *, ledger: AppendOnlyLedger, secrets: tuple[str, ...], run_dir: Path) -> None:
        self.ledger, self.secrets, self.run_dir = ledger, secrets, run_dir
        self.count, self.current_probe, self._original, self._business = 0, "authentication", None, []

    def __enter__(self):
        self._original = requests.Session.request
        requests.Session.request = lambda session, method, url, **kwargs: self._request(session, method, url, **kwargs)
        return self

    def __exit__(self, *unused):
        if self._original is not None:
            requests.Session.request = self._original

    def _request(self, session, method, url, **kwargs):
        path = requests.utils.urlparse(str(url)).path
        authentication = path in AUTH_ENDPOINT_PATHS
        if not authentication and path != BUSINESS_ENDPOINT_PATH:
            raise PilotStopped(f"UNAPPROVED_ENDPOINT:{path}")
        if self.count >= MAX_RAW_HTTP_REQUESTS:
            self.ledger.append("HTTP_BUDGET_EXHAUSTED", probe=self.current_probe)
            raise BudgetExceeded("raw HTTP budget exhausted")
        self.count += 1
        kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
        started = time.monotonic()
        try:
            response = self._original(session, method, url, **kwargs)
        except Exception as error:
            self.ledger.append("HTTP_ERROR", raw_sequence=self.count, probe=self.current_probe, error_type=type(error).__name__, error=redact(str(error), self.secrets))
            raise
        record = {"raw_sequence": self.count, "probe": self.current_probe, "method": str(method).upper(), "url": safe_url(str(url)), "status_code": response.status_code, "elapsed_ms": round((time.monotonic() - started) * 1000, 3), "response_bytes": len(response.content), "authentication": authentication}
        if not authentication:
            assert_no_credentials(response.content, self.secrets)
            matching = [item for item in PROBE_MATRIX if item.name == self.current_probe]
            if len(matching) != 1:
                raise PilotStopped(f"UNKNOWN_BUSINESS_PROBE:{self.current_probe}")
            body_path = self.run_dir / landing_name(matching[0])
            write_bytes_atomic_new(body_path, response.content)
            record.update({"response_sha256": hashlib.sha256(response.content).hexdigest(), "body_file": body_path.name})
            self._business.append((response, body_path))
        self.ledger.append("HTTP_RESPONSE", **record)
        if response.status_code in {403, 429}:
            raise PilotStopped(f"HTTP_RESTRICTION:{response.status_code}:{self.current_probe}")
        if response.status_code != 200:
            raise PilotStopped(f"HTTP_ERROR_STATUS:{response.status_code}:{self.current_probe}")
        return response

    def take_business(self, probe: ProbeSpec):
        if len(self._business) != 1:
            raise PilotStopped(f"BUSINESS_REQUEST_COUNT_MISMATCH:{probe.name}:{len(self._business)}")
        return self._business.pop()


def _operation_by_bld(core, bld: str):
    candidates = []
    for value in vars(core).values():
        if isinstance(value, type):
            try:
                instance = value()
                if getattr(instance, "bld", None) == bld:
                    candidates.append(instance)
            except Exception:
                pass
    if len(candidates) != 1:
        raise PilotStopped(f"CORE_OPERATION_UNRESOLVED:{bld}:{len(candidates)}")
    return candidates[0]


def _execute(core, probe: ProbeSpec) -> None:
    operation, scope = _operation_by_bld(core, probe.bld), probe.scope
    if probe.operation == "market":
        operation.fetch(scope["date"])
    elif probe.operation == "symbol":
        operation.fetch(scope["fromdate"], scope["todate"], scope["isin"])
    else:
        raise AssertionError(probe.operation)


def run_pilot(*, env_file: Path, landing_root: Path = LANDING_ROOT, lock_path: Path = D_OWNED_LOCK_PATH, throttle: BusinessThrottle | None = None) -> dict[str, object]:
    if importlib.metadata.version("pykrx") != PYKRX_VERSION:
        raise PilotStopped(f"pykrx must equal {PYKRX_VERSION}")
    etf_id, etf_pw = _load_credentials(env_file)
    if not etf_id or not etf_pw:
        raise PilotStopped("KRX credentials are not configured")
    secrets = (etf_id, etf_pw)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = landing_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    ledger = AppendOnlyLedger(run_dir / "call_ledger.jsonl", secrets=secrets)
    checkpoint = {"run_id": run_id, "status": "CREATED", "raw_http_requests": 0, "completed": {}, "matrix_sha256": matrix_sha256(), "updated_at_utc": utc_now()}
    write_json_atomic(run_dir / "checkpoint.json", checkpoint)
    write_json_atomic(run_dir / "manifest.json", {"run_id": run_id, "pykrx_version": PYKRX_VERSION, "business_request_limit": MAX_BUSINESS_REQUESTS, "raw_http_request_limit": MAX_RAW_HTTP_REQUESTS, "retry_count": 0, "parallelism": 1, "normalized_writes": False, "excluded_operations": ["MDCSTAT04601", "MDCSTAT05901", "MDCSTAT06001"], "probes": [{"sequence": item.sequence, "name": item.name, "bld": item.bld, "scope": dict(item.scope), "required_fields": list(item.required_fields)} for item in PROBE_MATRIX]})
    throttle = throttle or BusinessThrottle()
    with shared_d_owned_krx_lock(lock_path, run_id=run_id):
        with HttpCapture(ledger=ledger, secrets=secrets, run_dir=run_dir) as capture:
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    from pykrx.website.comm import get_session
                    from pykrx.website.krx.etx import core
                session = get_session()
                if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
                    raise PilotStopped("AUTHENTICATION_FAILED")
                for probe in PROBE_MATRIX:
                    slept = throttle.before_request()
                    capture.current_probe = probe.name
                    ledger.append("PROBE_STARTED", probe=probe.name, business_sequence=probe.sequence, bld=probe.bld, scope=dict(probe.scope), throttle_sleep_seconds=round(slept, 6))
                    _execute(core, probe)
                    response, body_path = capture.take_business(probe)
                    classification, rows, audit = classify_business_body(probe, response.content)
                    checkpoint["completed"][probe.name] = {"classification": classification, "rows": rows, "audit": audit, "body_file": body_path.name, "body_sha256": hashlib.sha256(response.content).hexdigest()}
                    checkpoint.update({"status": "IN_PROGRESS", "raw_http_requests": capture.count, "updated_at_utc": utc_now()})
                    write_json_atomic(run_dir / "checkpoint.json", checkpoint)
                    ledger.append("PROBE_COMPLETED", probe=probe.name, classification=classification, rows=rows, audit=audit, raw_http_requests=capture.count)
            except Exception as error:
                checkpoint.update({"status": "STOPPED", "raw_http_requests": capture.count, "stop_type": type(error).__name__, "stop_reason": redact(str(error), secrets), "updated_at_utc": utc_now()})
                write_json_atomic(run_dir / "checkpoint.json", checkpoint)
                ledger.append("RUN_STOPPED", error_type=type(error).__name__, error=redact(str(error), secrets), raw_http_requests=capture.count)
                raise
            checkpoint.update({"status": "COMPLETE", "raw_http_requests": capture.count, "updated_at_utc": utc_now()})
            write_json_atomic(run_dir / "checkpoint.json", checkpoint)
            ledger.append("RUN_COMPLETED", raw_http_requests=capture.count, business_requests=len(checkpoint["completed"]))
    for artifact in run_dir.rglob("*"):
        if artifact.is_file():
            assert_no_credentials(artifact.read_bytes(), secrets)
    return {"run_dir": str(run_dir), "status": checkpoint["status"], "raw_http_requests": checkpoint["raw_http_requests"], "completed_probes": len(checkpoint["completed"])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded Landing-only authenticated pykrx ETF pilot")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--confirm-live-manual-pilot", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_manual_pilot:
        print("Refusing to run: --confirm-live-manual-pilot is required", file=sys.stderr)
        return 2
    print(json.dumps(run_pilot(env_file=args.env_file), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

