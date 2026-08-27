from __future__ import annotations

import argparse
import base64
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
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[3]
MAX_HTTP_REQUESTS = 14
HTTP_TIMEOUT_SECONDS = 20
PROBES = (
    ("short_selling_historical_delisted", "get_shorting_status_by_date", ("20240708", "20240708", "003410")),
    ("investor_flow_recent_listed", "get_market_trading_value_by_date", ("20260810", "20260810", "005930")),
    ("fundamentals_historical_delisted", "get_market_fundamental_by_date", ("20240708", "20240708", "003410")),
    ("etf_recent_market", "get_etf_ohlcv_by_ticker", ("20260810",)),
    ("foreign_ownership_recent_listed", "get_exhaustion_rates_of_foreign_investment_by_date", ("20260810", "20260810", "005930")),
)


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(path)


def _load_krx_credentials(env_path: Path) -> tuple[bool, bool]:
    values: dict[str, str] = {}
    if env_path.is_file():
        for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in {"KRX_ID", "KRX_PW"}:
                values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("KRX_ID", "KRX_PW"):
        if not os.getenv(key) and values.get(key):
            os.environ[key] = values[key]
    return bool(os.getenv("KRX_ID")), bool(os.getenv("KRX_PW"))


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _classify_probes(probes: list[dict[str, object]]) -> str:
    all_success = len(probes) == len(PROBES) and all(probe.get("status") == "SUCCESS" for probe in probes)
    all_nonempty = all(isinstance(probe.get("rows"), int) and probe["rows"] > 0 for probe in probes)
    return (
        "AUTHENTICATED_SOURCE_FEASIBLE"
        if all_success and all_nonempty
        else "AUTHENTICATED_SOURCE_PARTIAL_OR_EMPTY"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded sequential pykrx authenticated feasibility diagnostic")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = ROOT / "data/landing/diagnostics/pykrx_login" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    ledger_path = run_dir / "diagnostic_ledger.json"
    id_configured, pw_configured = _load_krx_credentials(args.env_file)
    ledger: dict[str, object] = {
        "run_id": run_id,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "pykrx_version": importlib.metadata.version("pykrx"),
        "credentials": {"KRX_ID_configured": id_configured, "KRX_PW_configured": pw_configured},
        "policy": {
            "maximum_http_requests": MAX_HTTP_REQUESTS,
            "per_request_timeout_seconds": HTTP_TIMEOUT_SECONDS,
            "retry_loops": 0,
            "parallel_requests": 0,
            "bulk_backfill": False,
            "probe_count": len(PROBES),
        },
        "http_requests": [],
        "probes": [],
    }
    _write_json_atomic(ledger_path, ledger)
    if not (id_configured and pw_configured):
        ledger["classification"] = "AUTH_CONFIGURATION_MISSING"
        ledger["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(ledger_path, ledger)
        print(json.dumps({"run_dir": str(run_dir), "classification": ledger["classification"], "http_requests": 0}))
        return 2

    original_request = requests.Session.request
    current_probe = {"name": "authentication"}

    def bounded_request(session, method, url, **kwargs):
        requests_log = ledger["http_requests"]
        if len(requests_log) >= MAX_HTTP_REQUESTS:
            raise RuntimeError("pykrx diagnostic HTTP request budget exhausted")
        sequence = len(requests_log) + 1
        started = time.monotonic()
        kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
        base_entry = {
            "sequence": sequence,
            "probe": current_probe["name"],
            "method": str(method).upper(),
            "url": _safe_url(str(url)),
        }
        try:
            response = original_request(session, method, url, **kwargs)
        except Exception as exc:
            entry = {
                **base_entry, "status": "ERROR",
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                "error_type": type(exc).__name__, "error": str(exc),
            }
            requests_log.append(entry)
            _write_json_atomic(ledger_path, ledger)
            raise
        entry = {
            **base_entry, "status": "RESPONSE", "status_code": response.status_code,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "response_bytes": len(response.content),
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
        }
        if current_probe["name"] != "authentication":
            body_name = f"response_{sequence:02d}_{current_probe['name']}.base64.json"
            _write_json_atomic(run_dir / body_name, {
                "encoding": "base64", "body": base64.b64encode(response.content).decode("ascii"),
                "sha256": entry["response_sha256"], "bytes": entry["response_bytes"],
            })
            entry["preserved_body"] = body_name
        requests_log.append(entry)
        _write_json_atomic(ledger_path, ledger)
        return response

    requests.Session.request = bounded_request
    try:
        try:
            captured = io.StringIO()
            with redirect_stdout(captured), redirect_stderr(captured):
                from pykrx import stock
                from pykrx.website.comm import get_session
            session = get_session()
            ledger["authentication"] = {
                "session_available": session is not None,
                "is_authenticated": bool(session is not None and getattr(session, "is_authenticated", False)),
                "session_valid": bool(session is not None and session.is_valid()),
            }
        except Exception as exc:
            ledger["authentication"] = {
                "session_available": False, "is_authenticated": False,
                "session_valid": False, "error_type": type(exc).__name__, "error": str(exc),
            }
            ledger["classification"] = "AUTHENTICATION_ERROR"
        _write_json_atomic(ledger_path, ledger)
        if ledger["authentication"]["is_authenticated"]:
            for probe_name, function_name, arguments in PROBES:
                current_probe["name"] = probe_name
                result_path = run_dir / f"result_{probe_name}.json"
                try:
                    frame = getattr(stock, function_name)(*arguments)
                    if not isinstance(frame, pd.DataFrame):
                        frame = pd.DataFrame(frame)
                    result_payload = json.loads(frame.to_json(orient="split", date_format="iso", force_ascii=False))
                    _write_json_atomic(result_path, result_payload)
                    probe = {
                        "name": probe_name, "function": function_name,
                        "arguments": list(arguments), "status": "SUCCESS",
                        "rows": len(frame), "columns": [str(column) for column in frame.columns],
                        "result_file": result_path.name,
                        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
                    }
                except Exception as exc:
                    probe = {
                        "name": probe_name, "function": function_name,
                        "arguments": list(arguments), "status": "ERROR",
                        "error_type": type(exc).__name__, "error": str(exc),
                    }
                ledger["probes"].append(probe)
                _write_json_atomic(ledger_path, ledger)
            ledger["classification"] = _classify_probes(ledger["probes"])
        elif "classification" not in ledger:
            ledger["classification"] = "AUTHENTICATION_FAILED"
    finally:
        requests.Session.request = original_request
        ledger["actual_http_requests"] = len(ledger["http_requests"])
        ledger["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(ledger_path, ledger)

    credential_values = [os.getenv("KRX_ID", ""), os.getenv("KRX_PW", "")]
    removed_leaks = []
    for artifact in run_dir.rglob("*"):
        if artifact.is_file():
            content = artifact.read_bytes()
            if any(value and value.encode("utf-8") in content for value in credential_values):
                artifact.unlink()
                removed_leaks.append(artifact.name)
    if removed_leaks:
        raise RuntimeError("credential-bearing diagnostic artifacts were removed")
    print(json.dumps({
        "run_dir": str(run_dir), "classification": ledger["classification"],
        "http_requests": ledger["actual_http_requests"],
        "probe_statuses": {probe["name"]: probe["status"] for probe in ledger["probes"]},
        "probe_rows": {probe["name"]: probe.get("rows") for probe in ledger["probes"]},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
