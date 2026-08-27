"""Bounded LS t8428 pagination and t1633 history pilot; Raw evidence only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from dotenv import load_dotenv
import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.manual.pilot.ls_derivatives_investor_pilot import (
        OFFICIAL_BASE_URL, REQUIRED_ENV, atomic_json, credential_value, iso_utc,
        now_utc, official_base_url, post_oauth_once, safe_oauth_error,
    )
    from scripts.manual.backfill.ls_derivatives_raw_backfill import _contains_secret
except ModuleNotFoundError:
    from ls_derivatives_investor_pilot import (  # type: ignore[no-redef]
        OFFICIAL_BASE_URL, REQUIRED_ENV, atomic_json, credential_value, iso_utc,
        now_utc, official_base_url, post_oauth_once, safe_oauth_error,
    )
    from ls_derivatives_raw_backfill import _contains_secret  # type: ignore[no-redef]


MAX_T8428_PAGES = 5
MAX_T1633_CALLS = 12
MAX_DATA_CALLS = MAX_T8428_PAGES + MAX_T1633_CALLS
MIN_INTERVAL_SECONDS = 1.05
TARGET_DATE = "20260814"
HISTORY_DATES = ("20260813", "20260731", "20260102", "20250102", "20210104")


def t1633_scopes() -> list[dict[str, str]]:
    scopes: list[dict[str, str]] = []
    for market, market_name in (("0", "kospi"), ("1", "kosdaq")):
        scopes.append({"id": f"program_{market_name}_{TARGET_DATE}_quantity", "market": market, "market_name": market_name, "measure": "1", "date": TARGET_DATE})
    for date in HISTORY_DATES:
        for market, market_name in (("0", "kospi"), ("1", "kosdaq")):
            scopes.append({"id": f"program_{market_name}_{date}_amount", "market": market, "market_name": market_name, "measure": "0", "date": date})
    return scopes


def frozen_plan() -> dict[str, object]:
    return {
        "t8428": {"pages": MAX_T8428_PAGES, "request": {"fdate": "20000101", "tdate": TARGET_DATE, "gubun": "1", "upcode": "001", "cnt": 500}},
        "t1633": t1633_scopes(),
        "max_data_calls": MAX_DATA_CALLS,
        "retry_count": 0,
        "normalized_writes": False,
    }


def plan_digest(plan: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def parse_rows(payload: object, output_key: str) -> tuple[list[dict[str, object]], bool]:
    if not isinstance(payload, dict) or payload.get("rsp_cd") != "00000":
        raise ValueError("source response code mismatch")
    rows = payload.get(output_key)
    if rows is None and set(payload).issubset({"rsp_cd", "rsp_msg"}):
        return [], True
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("source output shape mismatch")
    return rows, False


def date_profile(rows: list[dict[str, object]]) -> dict[str, object]:
    dates = [str(row.get("date", "")) for row in rows]
    if any(len(date) != 8 or not date.isdigit() for date in dates):
        raise ValueError("source date shape mismatch")
    return {
        "rows": len(rows),
        "unique_dates": len(set(dates)),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "strict_descending": all(left > right for left, right in zip(dates, dates[1:])),
    }


def audit_t8428_pages(pages: list[list[dict[str, object]]]) -> dict[str, object]:
    seen: dict[str, dict[str, object]] = {}
    overlaps: list[str] = []
    conflicts: list[str] = []
    ranges: list[dict[str, object]] = []
    for number, rows in enumerate(pages, start=1):
        profile = date_profile(rows)
        if rows and not profile["strict_descending"]:
            raise ValueError("t8428 page is not strictly reverse chronological")
        for row in rows:
            date = str(row["date"])
            if date in seen:
                overlaps.append(date)
                if seen[date] != row:
                    conflicts.append(date)
            else:
                seen[date] = row
        ranges.append({"page": number, **profile})
    return {
        "pages": ranges,
        "physical_rows": sum(len(page) for page in pages),
        "unique_dates": len(seen),
        "boundary_overlaps": overlaps,
        "conflicting_overlaps": conflicts,
        "date_min": min(seen) if seen else None,
        "date_max": max(seen) if seen else None,
    }


def program_identity_residuals(row: dict[str, object]) -> dict[str, int]:
    value = lambda name: int(row[name])
    return {
        "total_buy_minus_sell_minus_net": value("tot1") - value("tot2") - value("tot3"),
        "arbitrage_buy_minus_sell_minus_net": value("cha1") - value("cha2") - value("cha3"),
        "non_arbitrage_buy_minus_sell_minus_net": value("bcha1") - value("bcha2") - value("bcha3"),
        "components_minus_total_net": value("cha3") + value("bcha3") - value("tot3"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--confirm-live-bounded-pilot", action="store_true")
    args = parser.parse_args()
    plan = frozen_plan()
    digest = plan_digest(plan)
    if len(t1633_scopes()) != MAX_T1633_CALLS:
        raise RuntimeError("frozen t1633 plan mismatch")
    if not args.confirm_live_bounded_pilot:
        print(json.dumps({"status": "NOT_EXECUTED_CONFIRMATION_REQUIRED", "oauth_cap": 1, "data_cap": MAX_DATA_CALLS}, sort_keys=True))
        return 2

    load_dotenv(args.root / ".env", override=False)
    if not all(os.getenv(name, "") for name in REQUIRED_ENV):
        print(json.dumps({"status": "NOT_EXECUTED_CREDENTIALS_MISSING"}, sort_keys=True))
        return 2
    app_key = credential_value("LS_APP_KEY")
    app_secret = credential_value("LS_APP_SECRET")
    base_url = official_base_url(os.environ["LS_BASE_URL"])
    if base_url != OFFICIAL_BASE_URL:
        raise ValueError("official base URL mismatch")

    run_id = now_utc().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = args.root / "data/landing/diagnostics/ls_t8428_t1633_followup" / run_id
    checkpoint_path = run_dir / "checkpoint.json"
    ledger_path = run_dir / "call_ledger.jsonl"
    lock_path = args.root / "data/state/locks/ls_t8428_t1633_followup.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        print(json.dumps({"status": "NOT_EXECUTED_LOCKED"}, sort_keys=True))
        return 3
    os.write(descriptor, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode("utf-8"))
    os.close(descriptor)

    atomic_json(checkpoint_path, {"schema": "stock_data.ls_t8428_t1633_followup_v1", "run_id": run_id, "status": "RUN_CREATED", "plan": plan, "plan_sha256": digest, "oauth_calls": 0, "data_calls": 0, "retry_count": 0, "normalized_writes": False})
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=0))
    token: str | None = None
    data_calls = 0
    results: list[dict[str, object]] = []
    status = "AUTH_NOT_ATTEMPTED"
    stopped_reason: str | None = None
    last_call: float | None = None
    next_header_key = ""
    next_body_date = " "

    def call(scope_id: str, tr_code: str, endpoint: str, block: dict[str, object], output_key: str, tr_cont: str, tr_cont_key: str) -> tuple[dict[str, object], list[dict[str, object]], requests.Response]:
        nonlocal data_calls, last_call
        if data_calls >= MAX_DATA_CALLS:
            raise RuntimeError("data-call cap exceeded")
        if last_call is not None:
            delay = MIN_INTERVAL_SECONDS - (time.monotonic() - last_call)
            if delay > 0:
                time.sleep(delay)
        started = now_utc()
        response = session.post(
            base_url + endpoint,
            headers={"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}", "tr_cd": tr_code, "tr_cont": tr_cont, "tr_cont_key": tr_cont_key},
            json={f"{tr_code}InBlock": block}, timeout=30,
        )
        last_call = time.monotonic()
        captured = now_utc()
        data_calls += 1
        raw = response.content
        raw_sha = hashlib.sha256(raw).hexdigest()
        if _contains_secret(raw, (app_key, app_secret, token or "")):
            raise ValueError("secret echo in source response")
        label = f"{data_calls:02d}_{scope_id}"
        raw_path = run_dir / f"{label}.response.json"
        atomic_bytes(raw_path, raw)
        content_type = response.headers.get("content-type", "").lower()
        if response.status_code != 200 or "json" not in content_type:
            raise ValueError("HTTP or content-type mismatch")
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError("non-JSON source response") from error
        rows, valid_empty = parse_rows(payload, output_key)
        profile = date_profile(rows)
        header_key = response.headers.get("tr_cont_key", "")
        provenance = {
            "schema": "stock_data.ls_followup_provenance_v1", "source": "LS_OPENAPI", "plan_sha256": digest,
            "scope_id": scope_id, "tr_code": tr_code, "endpoint": endpoint, "request_block": block,
            "request_tr_cont": tr_cont, "request_tr_cont_key_present": bool(tr_cont_key),
            "captured_at": iso_utc(captured), "http_status": response.status_code, "response_content_type": content_type,
            "rsp_cd": payload.get("rsp_cd"), "source_classification": "VALID_EMPTY" if valid_empty else "ROWS",
            **profile, "raw_response_sha256": raw_sha, "raw_response_bytes": len(raw),
            "response_tr_cont": response.headers.get("tr_cont"), "response_tr_cont_key_present": bool(header_key),
            "response_tr_cont_key_length": len(header_key),
            "response_tr_cont_key_sha256": hashlib.sha256(header_key.encode("utf-8")).hexdigest() if header_key else None,
            "normalized_writes": False,
        }
        atomic_json(run_dir / f"{label}.provenance.json", provenance)
        event = {"event": "HTTP_RESPONSE", "sequence": data_calls, "scope_id": scope_id, "tr_code": tr_code, "started_at": iso_utc(started), "captured_at": iso_utc(captured), "http_status": response.status_code, "rsp_cd": payload.get("rsp_cd"), "row_count": len(rows), "raw_response_sha256": raw_sha, "raw_response_bytes": len(raw), "retry_count": 0, "outcome": "VALID_EMPTY" if valid_empty else "PASS"}
        append_jsonl(ledger_path, event)
        result = {"scope_id": scope_id, "tr_code": tr_code, **profile, "valid_empty": valid_empty, "response_tr_cont": response.headers.get("tr_cont"), "next_body_date": payload.get(f"{tr_code}OutBlock", {}).get("date") if isinstance(payload.get(f"{tr_code}OutBlock"), dict) else None}
        results.append(result)
        atomic_json(checkpoint_path, {"schema": "stock_data.ls_t8428_t1633_followup_v1", "run_id": run_id, "status": "CAPTURING", "plan": plan, "plan_sha256": digest, "oauth_calls": 1, "data_calls": data_calls, "retry_count": 0, "results": results, "normalized_writes": False})
        return payload, rows, response

    try:
        started = now_utc()
        auth = post_oauth_once(session, base_url + "/oauth2/token", app_key, app_secret)
        captured = now_utc()
        try:
            auth_payload = auth.json()
        except ValueError:
            auth_payload = {}
        token = auth_payload.get("access_token") if isinstance(auth_payload, dict) else None
        auth_ok = auth.status_code == 200 and isinstance(token, str) and bool(token)
        error_code, error_message = safe_oauth_error(auth_payload, (app_key, app_secret))
        append_jsonl(ledger_path, {"event": "HTTP_RESPONSE", "operation": "oauth2/token", "sequence": 0, "retry_count": 0, "started_at": iso_utc(started), "captured_at": iso_utc(captured), "http_status": auth.status_code, "error_code": error_code, "error_message": error_message, "outcome": "PASS" if auth_ok else "FAIL", "credentials_persisted": False, "token_persisted": False})
        if not auth_ok:
            raise ValueError("oauth failed")

        for page in range(1, MAX_T8428_PAGES + 1):
            block = {"fdate": "20000101", "tdate": TARGET_DATE, "gubun": "1", "key_date": next_body_date, "upcode": "001", "cnt": 500}
            payload, rows, response = call(f"t8428_page_{page}", "t8428", "/stock/investinfo", block, "t8428OutBlock1", "N" if page == 1 else "Y", next_header_key)
            if not rows:
                break
            out = payload.get("t8428OutBlock")
            if not isinstance(out, dict) or not str(out.get("date", "")):
                raise ValueError("t8428 continuation body key missing")
            next_body_date = str(out["date"])
            next_header_key = response.headers.get("tr_cont_key", "")
            if page < MAX_T8428_PAGES and response.headers.get("tr_cont") == "Y" and not next_header_key:
                raise ValueError("t8428 continuation header key missing")
            if response.headers.get("tr_cont") != "Y":
                break

        for scope in t1633_scopes():
            block = {"gubun": scope["market"], "gubun1": scope["measure"], "gubun2": "0", "gubun3": "1", "fdate": scope["date"], "tdate": scope["date"], "gubun4": "0", "date": " ", "exchgubun": "K"}
            call(scope["id"], "t1633", "/stock/program", block, "t1633OutBlock1", "N", "")
        status = "PILOT_COMPLETE_REVIEW_REQUIRED"
    except Exception as error:
        status = "PILOT_STOPPED"
        stopped_reason = type(error).__name__
    finally:
        checkpoint = {"schema": "stock_data.ls_t8428_t1633_followup_v1", "run_id": run_id, "status": status, "plan": plan, "plan_sha256": digest, "oauth_calls": 1, "data_calls": data_calls, "max_data_calls": MAX_DATA_CALLS, "retry_count": 0, "stopped_reason": stopped_reason, "results": results, "normalized_writes": False, "token_persisted": False, "completed_at": iso_utc(now_utc())}
        atomic_json(checkpoint_path, checkpoint)
        files = [path for path in run_dir.iterdir() if path.is_file()]
        secret_ok = not any(_contains_secret(path.read_bytes(), (app_key, app_secret, token or "")) for path in files)
        checkpoint["secret_scan"] = "PASS" if secret_ok else "FAIL"
        if not secret_ok:
            checkpoint["status"] = status = "SECRET_SCAN_FAILED"
        atomic_json(checkpoint_path, checkpoint)
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            if owner.get("run_id") == run_id:
                lock_path.unlink()
        except (FileNotFoundError, OSError, ValueError):
            pass
    print(json.dumps({"status": status, "run_id": run_id, "oauth_calls": 1, "data_calls": data_calls, "retry_count": 0, "secret_scan": secret_ok}, sort_keys=True))
    return 0 if status == "PILOT_COMPLETE_REVIEW_REQUIRED" else 4


if __name__ == "__main__":
    sys.exit(main())
