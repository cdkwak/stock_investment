"""Bounded, retry-free LS OpenAPI derivatives-investor pilot.

This diagnostic intentionally issues one OAuth request and at most eleven
read-only t8462 requests. OAuth credentials and the access token are never
persisted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
from uuid import uuid4

from dotenv import load_dotenv
import requests


ROOT = Path(__file__).resolve().parents[3]
PROVIDER = "ls_securities_openapi"
TR_CODE = "t8462"
ENDPOINT = "/futureoption/investor"
TOKEN_ENDPOINT = "/oauth2/token"
OFFICIAL_BASE_URL = "https://openapi.ls-sec.co.kr:8080"
OFFICIAL_TOKEN_URL = OFFICIAL_BASE_URL + TOKEN_ENDPOINT
MAX_INVESTOR_CALLS = 11
REQUIRED_ENV = ("LS_APP_KEY", "LS_APP_SECRET", "LS_BASE_URL")
SENSITIVE_KEY = re.compile(r"(?i)(?:access[_-]?token|authorization|secret|appkey|credential|cookie|password)")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def safe_json(value: object, secrets: tuple[str, ...]) -> object:
    """Redact sensitive keys and any in-memory secret substrings."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else safe_json(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [safe_json(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, "[REDACTED]")
        return result
    return value


def secret_scan(paths: list[Path], secrets: tuple[str, ...]) -> bool:
    needles = [secret.encode("utf-8") for secret in secrets if secret]
    return all(needle not in path.read_bytes() for path in paths for needle in needles)


def official_base_url(value: str) -> str:
    base = value
    parsed = urlparse(base)
    if (
        base != OFFICIAL_BASE_URL
        or parsed.scheme != "https"
        or parsed.hostname != "openapi.ls-sec.co.kr"
        or parsed.port != 8080
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("LS_BASE_URL is not the exact official LS OpenAPI base URL")
    return base


def credential_value(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise ValueError(f"{name} is missing or empty")
    if value != value.strip():
        raise ValueError(f"{name} contains leading or trailing whitespace")
    return value


def safe_oauth_error(payload: object, secrets: tuple[str, ...]) -> tuple[object, object]:
    """Return only documented/provider error code and message fields, redacted."""
    if not isinstance(payload, dict):
        return None, None
    code = payload.get("rsp_cd", payload.get("error"))
    message = payload.get("rsp_msg", payload.get("error_description"))
    return safe_json(code, secrets), safe_json(message, secrets)


def oauth_request(app_key: str, app_secret: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return the exact official sample header and query parameters."""
    return (
        {"content-type": "application/x-www-form-urlencoded"},
        {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecretkey": app_secret,
            "scope": "oob",
        },
    )


def post_oauth_once(
    session: requests.Session, url: str, app_key: str, app_secret: str
) -> requests.Response:
    headers, params = oauth_request(app_key, app_secret)
    return session.post(url, headers=headers, params=params, timeout=30)


def post_once(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, object],
) -> tuple[requests.Response, object]:
    response = session.post(url, headers=headers, json=body, timeout=30)
    try:
        payload: object = response.json()
    except ValueError as error:
        raise RuntimeError("LS OpenAPI returned a non-JSON response") from error
    return response, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live-pilot", action="store_true")
    parser.add_argument("--oauth-retest-single-day", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()

    load_dotenv(args.root / ".env", override=False)
    configured = {name: bool(os.getenv(name, "").strip()) for name in REQUIRED_ENV}
    if not args.confirm_live_pilot or not all(configured.values()):
        print(json.dumps({"status": "NOT_EXECUTED", "configured": configured}, sort_keys=True))
        return 2

    app_key = credential_value("LS_APP_KEY")
    app_secret = credential_value("LS_APP_SECRET")
    base_url = official_base_url(os.environ["LS_BASE_URL"])
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

    paths: list[Path] = []
    ledger: list[dict[str, object]] = []
    investor_calls = 0
    token_calls = 0
    retry_count = 0
    status = "AUTH_NOT_ATTEMPTED"
    stopped_reason: str | None = None
    access_token: str | None = None
    session = requests.Session()
    try:
        token_started = now_utc()
        token_calls = 1
        token_response = post_oauth_once(session, base_url + TOKEN_ENDPOINT, app_key, app_secret)
        token_completed = now_utc()
        try:
            token_payload = token_response.json()
        except ValueError:
            token_payload = {}
        access_token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
        auth_ok = token_response.status_code == 200 and isinstance(access_token, str) and bool(access_token)
        error_code, error_message = safe_oauth_error(token_payload, secrets)
        ledger.append({
            "provider": PROVIDER,
            "operation": "oauth2/token",
            "attempt": 1,
            "retry_count": 0,
            "started_at_utc": iso_utc(token_started),
            "completed_at_utc": iso_utc(token_completed),
            "http_status": token_response.status_code,
            "response_content_type": token_response.headers.get("content-type"),
            "ls_error_code": error_code,
            "ls_error_message": error_message,
            "outcome": "PASS" if auth_ok else "FAIL",
            "credentials_persisted": False,
            "token_persisted": False,
        })
        if not auth_ok:
            status = "AUTH_FAILED_HTTP_403" if token_response.status_code == 403 else "AUTH_FAILED"
            stopped_reason = "OAuth response did not contain a usable access token"
        else:
            status = "AUTH_PASS"
            auth_header = f"Bearer {access_token}"
            calls = [
                ("20260814_k2i_f_d", "20260814", "K2I", "F", "D"),
                ("20260814_k2i_c_d", "20260814", "K2I", "C", "D"),
                ("20260814_k2i_p_d", "20260814", "K2I", "P", "D"),
                ("20260814_mki_f_d", "20260814", "MKI", "F", "D"),
                ("20260814_mki_c_d", "20260814", "MKI", "C", "D"),
                ("20260814_mki_p_d", "20260814", "MKI", "P", "D"),
            ]
            if args.oauth_retest_single_day:
                calls = calls[:1]
            history_dates = ("20260813", "20260731", "20260102", "20250102", "20210104")
            target_day_pass = True
            for label, date, asset, product, session_code in calls:
                if investor_calls >= MAX_INVESTOR_CALLS:
                    raise RuntimeError("LS investor-call budget exceeded")
                started = now_utc()
                body = {
                    "t8462InBlock": {
                        "tm_rng": session_code,
                        "fot_clsf_cd": product,
                        "bsc_asts_id": asset,
                        "gubun2": "1",
                        "gubun3": "1",
                        "from_date": date,
                        "to_date": date,
                    }
                }
                investor_calls += 1
                try:
                    response, payload = post_once(
                        session,
                        base_url + ENDPOINT,
                        headers={
                            "content-type": "application/json; charset=utf-8",
                            "authorization": auth_header,
                            "tr_cd": TR_CODE,
                            "tr_cont": "N",
                            "tr_cont_key": "",
                        },
                        body=body,
                    )
                except Exception as error:
                    stopped_reason = type(error).__name__
                    target_day_pass = False
                    break
                completed = now_utc()
                safe_payload = safe_json(payload, (*secrets, access_token))
                landing_path = run_dir / f"{label}.json"
                atomic_json(landing_path, safe_payload)
                paths.append(landing_path)
                body_sha256 = hashlib.sha256(response.content).hexdigest()
                rsp_cd = safe_payload.get("rsp_cd") if isinstance(safe_payload, dict) else None
                rows = safe_payload.get("t8462OutBlock1") if isinstance(safe_payload, dict) else None
                row_count = len(rows) if isinstance(rows, list) else 0
                call_ok = response.status_code == 200 and rsp_cd == "00000"
                ledger.append({
                    "provider": PROVIDER,
                    "operation": ENDPOINT,
                    "tr_code": TR_CODE,
                    "attempt": investor_calls,
                    "retry_count": 0,
                    "started_at_utc": iso_utc(started),
                    "completed_at_utc": iso_utc(completed),
                    "http_status": response.status_code,
                    "rsp_cd": rsp_cd,
                    "row_count": row_count,
                    "request": body,
                    "response_body_sha256": body_sha256,
                    "landing_relative_path": landing_path.relative_to(args.root).as_posix(),
                    "outcome": "PASS" if call_ok else "FAIL",
                    "request_headers_persisted": False,
                })
                if not call_ok:
                    stopped_reason = "t8462 HTTP or provider error"
                    target_day_pass = False
                    break

            if target_day_pass and not args.oauth_retest_single_day:
                for date in history_dates:
                    if investor_calls >= MAX_INVESTOR_CALLS:
                        raise RuntimeError("LS investor-call budget exceeded")
                    label = f"{date}_k2i_f_d_history"
                    started = now_utc()
                    body = {
                        "t8462InBlock": {
                            "tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I",
                            "gubun2": "1", "gubun3": "1", "from_date": date, "to_date": date,
                        }
                    }
                    investor_calls += 1
                    try:
                        response, payload = post_once(
                            session,
                            base_url + ENDPOINT,
                            headers={
                                "content-type": "application/json; charset=utf-8",
                                "authorization": auth_header,
                                "tr_cd": TR_CODE,
                                "tr_cont": "N",
                                "tr_cont_key": "",
                            },
                            body=body,
                        )
                    except Exception as error:
                        stopped_reason = type(error).__name__
                        break
                    completed = now_utc()
                    safe_payload = safe_json(payload, (*secrets, access_token))
                    landing_path = run_dir / f"{label}.json"
                    atomic_json(landing_path, safe_payload)
                    paths.append(landing_path)
                    rsp_cd = safe_payload.get("rsp_cd") if isinstance(safe_payload, dict) else None
                    rows = safe_payload.get("t8462OutBlock1") if isinstance(safe_payload, dict) else None
                    row_count = len(rows) if isinstance(rows, list) else 0
                    call_ok = response.status_code == 200 and rsp_cd == "00000"
                    ledger.append({
                        "provider": PROVIDER, "operation": ENDPOINT, "tr_code": TR_CODE,
                        "attempt": investor_calls, "retry_count": 0,
                        "started_at_utc": iso_utc(started), "completed_at_utc": iso_utc(completed),
                        "http_status": response.status_code, "rsp_cd": rsp_cd,
                        "row_count": row_count, "request": body,
                        "response_body_sha256": hashlib.sha256(response.content).hexdigest(),
                        "landing_relative_path": landing_path.relative_to(args.root).as_posix(),
                        "outcome": "PASS" if call_ok else "FAIL", "request_headers_persisted": False,
                    })
                    if not call_ok:
                        stopped_reason = "historical t8462 HTTP or provider error"
                        break
                status = "PILOT_COMPLETED" if stopped_reason is None else "PILOT_STOPPED"
            elif target_day_pass:
                status = "PILOT_COMPLETED"
            else:
                status = "PILOT_STOPPED"
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
            "provider": PROVIDER, "run_id": run_id, "status": status,
            "token_calls": token_calls, "investor_calls": investor_calls,
            "total_calls": token_calls + investor_calls, "retry_count": retry_count,
            "max_investor_calls": MAX_INVESTOR_CALLS,
            "stopped_reason": stopped_reason,
            "completed_at_utc": iso_utc(now_utc()),
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
        "status": status, "run_id": run_id, "token_calls": token_calls,
        "investor_calls": investor_calls, "retry_count": retry_count,
        "secret_scan": scan_ok,
    }, sort_keys=True))
    return 0 if status == "PILOT_COMPLETED" else 4


if __name__ == "__main__":
    sys.exit(main())
