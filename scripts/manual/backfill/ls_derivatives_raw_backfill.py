"""Bounded raw-only LS t8462 capture for six products and D/N/U codes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from dotenv import load_dotenv
import requests

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.manual.pilot.ls_derivatives_investor_pilot import (
        ENDPOINT, OFFICIAL_BASE_URL, REQUIRED_ENV, TR_CODE, atomic_json,
        credential_value, iso_utc, now_utc, official_base_url, post_oauth_once,
        safe_oauth_error,
    )
except ModuleNotFoundError:
    from ls_derivatives_investor_pilot import (  # type: ignore[no-redef]
        ENDPOINT, OFFICIAL_BASE_URL, REQUIRED_ENV, TR_CODE, atomic_json,
        credential_value, iso_utc, now_utc, official_base_url, post_oauth_once,
        safe_oauth_error,
    )


FROM_DATE = "20250718"
TO_DATE = "20260814"
MAX_DATA_CALLS = 18
MIN_INTERVAL_SECONDS = 1.05
SUFFIXES = ("00", "01", "02", "03", "04", "05", "06", "07", "08", "15", "17", "18")
EXPECTED_ROW_KEYS = {"date", *(f"sv_{value}" for value in SUFFIXES), *(f"sa_{value}" for value in SUFFIXES)}


def scopes() -> list[dict[str, str]]:
    result = []
    for asset, asset_name in (("K2I", "KOSPI200"), ("MKI", "MINI_KOSPI200")):
        for product, product_name in (("F", "FUTURES"), ("C", "CALL"), ("P", "PUT")):
            for session in ("D", "N", "U"):
                result.append({
                    "asset_code": asset,
                    "asset_name": asset_name,
                    "product_code": product,
                    "product_name": product_name,
                    "requested_session_code": session,
                    "from_date": FROM_DATE,
                    "to_date": TO_DATE,
                })
    return result


def request_block(scope: dict[str, str]) -> dict[str, str]:
    return {
        "tm_rng": scope["requested_session_code"],
        "fot_clsf_cd": scope["product_code"],
        "bsc_asts_id": scope["asset_code"],
        "gubun2": "1",
        "gubun3": "1",
        "from_date": scope["from_date"],
        "to_date": scope["to_date"],
    }


def validate_payload(payload: object, scope: dict[str, str]) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or payload.get("rsp_cd") != "00000":
        raise ValueError("provider response is not successful")
    expected_echo = {
        "tm_rng": scope["requested_session_code"],
        "fot_clsf_cd": scope["product_code"],
        "bsc_asts_id": scope["asset_code"],
    }
    if payload.get("t8462OutBlock") != expected_echo:
        raise ValueError("response echo differs from requested scope")
    rows = payload.get("t8462OutBlock1")
    if not isinstance(rows, list):
        raise ValueError("response rows are missing")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != EXPECTED_ROW_KEYS:
            raise ValueError("response row schema differs")
        date = str(row["date"])
        if not (scope["from_date"] <= date <= scope["to_date"]) or date in seen:
            raise ValueError("response date is out of range or duplicated")
        seen.add(date)
        for key in EXPECTED_ROW_KEYS - {"date"}:
            value = row[key]
            if isinstance(value, bool):
                raise ValueError("boolean numeric source value")
            int(str(value))
    return rows


def _contains_secret(body: bytes, secrets: tuple[str, ...]) -> bool:
    return any(value and value.encode("utf-8") in body for value in secrets)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--confirm-live-raw-backfill", action="store_true")
    args = parser.parse_args()
    load_dotenv(args.root / ".env", override=False)
    configured = {name: bool(os.getenv(name, "")) for name in REQUIRED_ENV}
    if not args.confirm_live_raw_backfill or not all(configured.values()):
        print(json.dumps({"status": "NOT_EXECUTED", "configured": configured}, sort_keys=True))
        return 2

    app_key = credential_value("LS_APP_KEY")
    app_secret = credential_value("LS_APP_SECRET")
    base_url = official_base_url(os.environ["LS_BASE_URL"])
    if base_url != OFFICIAL_BASE_URL:
        raise ValueError("official base URL mismatch")
    run_id = now_utc().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = args.root / "data/landing/ls_openapi/t8462_raw" / run_id
    lock_path = args.root / "data/state/locks/ls_derivatives_investor_pilot.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        print(json.dumps({"status": "NOT_EXECUTED", "reason": "LOCKED"}, sort_keys=True))
        return 3
    os.write(descriptor, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode())
    os.close(descriptor)

    ledger: list[dict[str, object]] = []
    written: list[Path] = []
    token: str | None = None
    data_calls = 0
    status = "AUTH_NOT_ATTEMPTED"
    stopped_reason: str | None = None
    session = requests.Session()
    last_call: float | None = None
    try:
        started = now_utc()
        auth = post_oauth_once(session, base_url + "/oauth2/token", app_key, app_secret)
        completed = now_utc()
        try:
            auth_payload = auth.json()
        except ValueError:
            auth_payload = {}
        token = auth_payload.get("access_token") if isinstance(auth_payload, dict) else None
        auth_ok = auth.status_code == 200 and isinstance(token, str) and bool(token)
        error_code, error_message = safe_oauth_error(auth_payload, (app_key, app_secret))
        ledger.append({
            "event": "HTTP_RESPONSE", "provider": "LS_OPENAPI", "operation": "oauth2/token",
            "sequence": 1, "retry_count": 0, "started_at": iso_utc(started),
            "captured_at": iso_utc(completed), "http_status": auth.status_code,
            "content_type": auth.headers.get("content-type"), "error_code": error_code,
            "error_message": error_message, "outcome": "PASS" if auth_ok else "FAIL",
            "credentials_persisted": False, "token_persisted": False,
        })
        if not auth_ok:
            status = "AUTH_FAILED"
            stopped_reason = "oauth_failed"
        else:
            status = "AUTH_PASS"
            for sequence, scope in enumerate(scopes(), start=1):
                if data_calls >= MAX_DATA_CALLS:
                    raise RuntimeError("data call cap exceeded")
                if last_call is not None:
                    delay = MIN_INTERVAL_SECONDS - (time.monotonic() - last_call)
                    if delay > 0:
                        time.sleep(delay)
                block = request_block(scope)
                started = now_utc()
                response = session.post(
                    base_url + ENDPOINT,
                    headers={
                        "content-type": "application/json; charset=utf-8",
                        "authorization": f"Bearer {token}", "tr_cd": TR_CODE,
                        "tr_cont": "N", "tr_cont_key": "",
                    },
                    json={"t8462InBlock": block}, timeout=30,
                )
                last_call = time.monotonic()
                completed = now_utc()
                data_calls += 1
                raw = response.content
                if _contains_secret(raw, (app_key, app_secret, token)):
                    stopped_reason = "secret_echo_in_response"
                    break
                label = f"{sequence:02d}_{scope['asset_code']}_{scope['product_code']}_{scope['requested_session_code']}"
                raw_path = run_dir / f"{label}.response.json"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(raw)
                written.append(raw_path)
                try:
                    payload = response.json()
                    rows = validate_payload(payload, scope)
                except (ValueError, TypeError, KeyError) as error:
                    stopped_reason = type(error).__name__
                    rows = []
                    payload = {}
                dates = sorted(str(row["date"]) for row in rows)
                metadata = {
                    "schema": "stock_data.ls_t8462_raw_capture_v1",
                    "source": "LS_OPENAPI", "tr_code": TR_CODE,
                    **scope, "captured_at": iso_utc(completed),
                    "rsp_cd": payload.get("rsp_cd") if isinstance(payload, dict) else None,
                    "row_count": len(rows), "market_date_min": dates[0] if dates else None,
                    "market_date_max": dates[-1] if dates else None,
                    "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                    "raw_response_bytes": len(raw),
                    "response_tr_cont": response.headers.get("tr_cont"),
                    "response_tr_cont_key_present": bool(response.headers.get("tr_cont_key")),
                    "normalized_writes": False,
                }
                meta_path = run_dir / f"{label}.provenance.json"
                atomic_json(meta_path, metadata)
                written.append(meta_path)
                call_ok = (
                    response.status_code == 200
                    and "json" in response.headers.get("content-type", "").lower()
                    and metadata["rsp_cd"] == "00000"
                    and stopped_reason is None
                )
                ledger.append({
                    "event": "HTTP_RESPONSE", "provider": "LS_OPENAPI", "operation": ENDPOINT,
                    "tr_code": TR_CODE, "sequence": sequence, "retry_count": 0,
                    "started_at": iso_utc(started), "captured_at": iso_utc(completed),
                    "http_status": response.status_code, "content_type": response.headers.get("content-type"),
                    "scope": scope, "rsp_cd": metadata["rsp_cd"], "row_count": len(rows),
                    "raw_response_sha256": metadata["raw_response_sha256"],
                    "raw_response_bytes": len(raw), "outcome": "PASS" if call_ok else "FAIL",
                })
                if not call_ok:
                    if response.status_code in (403, 429):
                        stopped_reason = f"access_restriction_{response.status_code}"
                    break
            status = "RAW_BACKFILL_COMPLETE_REVIEW_REQUIRED" if data_calls == 18 and stopped_reason is None else "RAW_BACKFILL_STOPPED"
    except Exception as error:
        status = "RAW_BACKFILL_STOPPED"
        stopped_reason = type(error).__name__
    finally:
        run_dir.mkdir(parents=True, exist_ok=True)
        ledger_path = run_dir / "call_ledger.jsonl"
        ledger_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ledger), encoding="utf-8")
        checkpoint = {
            "schema": "stock_data.ls_t8462_raw_checkpoint_v1", "run_id": run_id,
            "status": status, "oauth_calls": 1, "data_calls": data_calls,
            "retry_count": 0, "max_data_calls": MAX_DATA_CALLS,
            "requested_from_date": FROM_DATE, "requested_to_date": TO_DATE,
            "stopped_reason": stopped_reason, "normalized_writes": False,
            "completed_at": iso_utc(now_utc()), "token_persisted": False,
        }
        atomic_json(run_dir / "checkpoint.json", checkpoint)
        scan_paths = written + [ledger_path, run_dir / "checkpoint.json"]
        secret_ok = not any(_contains_secret(path.read_bytes(), (app_key, app_secret, token or "")) for path in scan_paths)
        if not secret_ok:
            checkpoint["status"] = "SECRET_SCAN_FAILED"
            atomic_json(run_dir / "checkpoint.json", checkpoint)
            status = "SECRET_SCAN_FAILED"
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            if owner.get("run_id") == run_id:
                lock_path.unlink()
        except (FileNotFoundError, OSError, ValueError):
            pass
    print(json.dumps({"status": status, "run_id": run_id, "oauth_calls": 1, "data_calls": data_calls, "retry_count": 0, "secret_scan": secret_ok}, sort_keys=True))
    return 0 if status == "RAW_BACKFILL_COMPLETE_REVIEW_REQUIRED" else 4


if __name__ == "__main__":
    sys.exit(main())
