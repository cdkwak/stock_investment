"""Continue the LS t8462 pilot without repeating retained K2I futures evidence."""
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
        safe_json, safe_oauth_error, secret_scan,
    )
except ModuleNotFoundError:  # Direct script execution adds this directory to sys.path.
    from ls_derivatives_investor_pilot import (  # type: ignore[no-redef]
        ENDPOINT, OFFICIAL_BASE_URL, REQUIRED_ENV, TR_CODE, atomic_json,
        credential_value, iso_utc, now_utc, official_base_url, post_oauth_once,
        safe_json, safe_oauth_error, secret_scan,
    )


MAX_INVESTOR_CALLS = 13
MIN_CALL_INTERVAL_SECONDS = 1.05
REUSED_RUN_ID = "20260814T164315Z_1f6e2f359c7a436c86ee8a7a019f5b66"


def validate_reused_k2i_f(root: Path, run_id: str) -> dict[str, object]:
    run_dir = root / "data/landing/diagnostics/ls_derivatives_investor_pilot" / run_id
    response_path = run_dir / "20260814_k2i_f_d.json"
    ledger_path = run_dir / "call_ledger.jsonl"
    payload = json.loads(response_path.read_text(encoding="utf-8"))
    ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    call = next(item for item in ledger if item.get("tr_code") == TR_CODE)
    request = call["request"]["t8462InBlock"]
    expected = {
        "tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I",
        "gubun2": "1", "gubun3": "1", "from_date": "20260814", "to_date": "20260814",
    }
    block = payload.get("t8462OutBlock")
    rows = payload.get("t8462OutBlock1")
    if request != expected or payload.get("rsp_cd") != "00000":
        raise ValueError("retained K2I futures request is not reusable")
    if block != {"tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I"}:
        raise ValueError("retained K2I futures response echo is invalid")
    if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("date") != "20260814":
        raise ValueError("retained K2I futures row is invalid")
    return {
        "run_id": run_id,
        "landing_relative_path": response_path.relative_to(root).as_posix(),
        "response_body_sha256": call["response_body_sha256"],
        "row_count": 1,
    }


def response_shape(payload: object, request: dict[str, object]) -> tuple[bool, int, str | None]:
    if not isinstance(payload, dict) or payload.get("rsp_cd") != "00000":
        return False, 0, "provider_error"
    block = payload.get("t8462OutBlock")
    rows = payload.get("t8462OutBlock1")
    if not isinstance(block, dict) or not isinstance(rows, list):
        return False, 0, "schema_anomaly"
    expected_echo = {
        "tm_rng": request["tm_rng"],
        "fot_clsf_cd": request["fot_clsf_cd"],
        "bsc_asts_id": request["bsc_asts_id"],
    }
    if block != expected_echo:
        return False, len(rows), "request_echo_anomaly"
    for row in rows:
        if not isinstance(row, dict) or "date" not in row:
            return False, len(rows), "row_schema_anomaly"
    return True, len(rows), None


def call_specs() -> list[tuple[str, dict[str, object], str]]:
    def request(date_from: str, date_to: str, asset: str, product: str, session: str) -> dict[str, object]:
        return {
            "tm_rng": session, "fot_clsf_cd": product, "bsc_asts_id": asset,
            "gubun2": "1", "gubun3": "1", "from_date": date_from, "to_date": date_to,
        }

    return [
        ("20260814_k2i_c_d", request("20260814", "20260814", "K2I", "C", "D"), "product"),
        ("20260814_k2i_p_d", request("20260814", "20260814", "K2I", "P", "D"), "product"),
        ("20260814_mki_f_d", request("20260814", "20260814", "MKI", "F", "D"), "product"),
        ("20260814_mki_c_d", request("20260814", "20260814", "MKI", "C", "D"), "product"),
        ("20260814_mki_p_d", request("20260814", "20260814", "MKI", "P", "D"), "product"),
        ("20260814_k2i_f_n", request("20260814", "20260814", "K2I", "F", "N"), "session"),
        ("20260813_k2i_f_d_history", request("20260813", "20260813", "K2I", "F", "D"), "history"),
        ("20260731_k2i_f_d_history", request("20260731", "20260731", "K2I", "F", "D"), "history"),
        ("20260102_k2i_f_d_history", request("20260102", "20260102", "K2I", "F", "D"), "history"),
        ("20250102_k2i_f_d_history", request("20250102", "20250102", "K2I", "F", "D"), "history"),
        ("20210104_k2i_f_d_history", request("20210104", "20210104", "K2I", "F", "D"), "history"),
        ("20210104_20260814_k2i_f_d_range", request("20210104", "20260814", "K2I", "F", "D"), "range"),
        ("20260815_k2i_f_d_holiday", request("20260815", "20260815", "K2I", "F", "D"), "holiday"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live-followup", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--reuse-run-id", default=REUSED_RUN_ID)
    args = parser.parse_args()

    load_dotenv(args.root / ".env", override=False)
    configured = {name: bool(os.getenv(name, "").strip()) for name in REQUIRED_ENV}
    if not args.confirm_live_followup or not all(configured.values()):
        print(json.dumps({"status": "NOT_EXECUTED", "configured": configured}, sort_keys=True))
        return 2

    reused = validate_reused_k2i_f(args.root, args.reuse_run_id)
    app_key = credential_value("LS_APP_KEY")
    app_secret = credential_value("LS_APP_SECRET")
    base_url = official_base_url(os.environ["LS_BASE_URL"])
    if base_url != OFFICIAL_BASE_URL:
        raise ValueError("official LS base URL mismatch")
    secrets = (app_key, app_secret)
    run_id = now_utc().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = args.root / "data/landing/diagnostics/ls_derivatives_investor_pilot" / run_id
    lock_path = args.root / "data/state/locks/ls_derivatives_investor_pilot.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        print(json.dumps({"status": "NOT_EXECUTED", "reason": "LOCKED"}, sort_keys=True))
        return 3
    os.write(descriptor, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode("utf-8"))
    os.close(descriptor)

    ledger: list[dict[str, object]] = []
    paths: list[Path] = []
    access_token: str | None = None
    investor_calls = 0
    status = "AUTH_NOT_ATTEMPTED"
    stopped_reason: str | None = None
    last_call_at: float | None = None
    session = requests.Session()
    try:
        started = now_utc()
        token_response = post_oauth_once(session, base_url + "/oauth2/token", app_key, app_secret)
        completed = now_utc()
        try:
            token_payload = token_response.json()
        except ValueError:
            token_payload = {}
        access_token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
        auth_ok = token_response.status_code == 200 and isinstance(access_token, str) and bool(access_token)
        error_code, error_message = safe_oauth_error(token_payload, secrets)
        ledger.append({
            "provider": "ls_securities_openapi", "operation": "oauth2/token", "attempt": 1,
            "retry_count": 0, "started_at_utc": iso_utc(started), "completed_at_utc": iso_utc(completed),
            "http_status": token_response.status_code,
            "response_content_type": token_response.headers.get("content-type"),
            "ls_error_code": error_code, "ls_error_message": error_message,
            "outcome": "PASS" if auth_ok else "FAIL",
            "credentials_persisted": False, "token_persisted": False,
        })
        if not auth_ok:
            status = "AUTH_FAILED"
            stopped_reason = "oauth_failed"
        else:
            status = "AUTH_PASS"
            authorization = f"Bearer {access_token}"
            for label, request, phase in call_specs():
                if investor_calls >= MAX_INVESTOR_CALLS:
                    raise RuntimeError("investor_call_budget_exceeded")
                if last_call_at is not None:
                    remaining = MIN_CALL_INTERVAL_SECONDS - (time.monotonic() - last_call_at)
                    if remaining > 0:
                        time.sleep(remaining)
                started = now_utc()
                investor_calls += 1
                response = session.post(
                    base_url + ENDPOINT,
                    headers={
                        "content-type": "application/json; charset=utf-8",
                        "authorization": authorization,
                        "tr_cd": TR_CODE, "tr_cont": "N", "tr_cont_key": "",
                    },
                    json={"t8462InBlock": request}, timeout=30,
                )
                last_call_at = time.monotonic()
                completed = now_utc()
                content_type = response.headers.get("content-type", "")
                try:
                    payload: object = response.json()
                except ValueError:
                    payload = None
                if payload is None:
                    stopped_reason = "non_json_response"
                    break
                safe_payload = safe_json(payload, (*secrets, access_token))
                landing_path = run_dir / f"{label}.json"
                atomic_json(landing_path, safe_payload)
                paths.append(landing_path)
                shape_ok, row_count, anomaly = response_shape(safe_payload, request)
                call_ok = response.status_code == 200 and "json" in content_type.lower() and shape_ok
                rows = safe_payload.get("t8462OutBlock1", []) if isinstance(safe_payload, dict) else []
                dates = [str(row.get("date")) for row in rows if isinstance(row, dict)]
                ledger.append({
                    "provider": "ls_securities_openapi", "operation": ENDPOINT, "tr_code": TR_CODE,
                    "attempt": investor_calls, "retry_count": 0, "phase": phase,
                    "started_at_utc": iso_utc(started), "completed_at_utc": iso_utc(completed),
                    "http_status": response.status_code, "response_content_type": content_type,
                    "rsp_cd": safe_payload.get("rsp_cd") if isinstance(safe_payload, dict) else None,
                    "row_count": row_count, "first_date": min(dates) if dates else None,
                    "last_date": max(dates) if dates else None,
                    "response_tr_cont": response.headers.get("tr_cont"),
                    "response_tr_cont_key_present": bool(response.headers.get("tr_cont_key")),
                    "request": {"t8462InBlock": request},
                    "response_body_sha256": hashlib.sha256(response.content).hexdigest(),
                    "landing_relative_path": landing_path.relative_to(args.root).as_posix(),
                    "outcome": "PASS" if call_ok else "FAIL", "anomaly": anomaly,
                    "request_headers_persisted": False,
                })
                if not call_ok:
                    stopped_reason = (
                        "access_restriction" if response.status_code in (403, 429) else anomaly or "provider_failure"
                    )
                    break
            status = "PILOT_COMPLETED" if stopped_reason is None else "PILOT_STOPPED"
    except Exception as error:
        status = "PILOT_STOPPED"
        stopped_reason = type(error).__name__
    finally:
        ledger_path = run_dir / "call_ledger.jsonl"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in ledger),
            encoding="utf-8",
        )
        paths.append(ledger_path)
        checkpoint_path = run_dir / "checkpoint.json"
        checkpoint = {
            "provider": "ls_securities_openapi", "run_id": run_id, "status": status,
            "token_calls": 1, "investor_calls": investor_calls,
            "total_calls": 1 + investor_calls, "retry_count": 0,
            "max_investor_calls": MAX_INVESTOR_CALLS, "stopped_reason": stopped_reason,
            "completed_at_utc": iso_utc(now_utc()), "reused_evidence": reused,
            "credential_values_persisted": False, "token_persisted": False,
        }
        atomic_json(checkpoint_path, checkpoint)
        paths.append(checkpoint_path)
        scan_ok = secret_scan(paths, (*secrets, access_token or ""))
        if not scan_ok:
            status = "SECRET_SCAN_FAILED"
            checkpoint["status"] = status
            atomic_json(checkpoint_path, checkpoint)
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            if owner.get("run_id") == run_id:
                lock_path.unlink()
        except (FileNotFoundError, OSError, ValueError):
            pass

    print(json.dumps({
        "status": status, "run_id": run_id, "token_calls": 1,
        "investor_calls": investor_calls, "retry_count": 0, "secret_scan": scan_ok,
    }, sort_keys=True))
    return 0 if status == "PILOT_COMPLETED" else 4


if __name__ == "__main__":
    sys.exit(main())
