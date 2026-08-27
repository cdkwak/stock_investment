"""Bounded LS t8428 continuation to the reachable source floor; Raw only."""
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
    from scripts.manual.pilot.pilot_ls_t8428_t1633_followup import (
        append_jsonl, atomic_bytes, audit_t8428_pages, date_profile, parse_rows,
    )
except ModuleNotFoundError:
    from ls_derivatives_investor_pilot import (  # type: ignore[no-redef]
        OFFICIAL_BASE_URL, REQUIRED_ENV, atomic_json, credential_value, iso_utc,
        now_utc, official_base_url, post_oauth_once, safe_oauth_error,
    )
    from ls_derivatives_raw_backfill import _contains_secret  # type: ignore[no-redef]
    from pilot_ls_t8428_t1633_followup import (  # type: ignore[no-redef]
        append_jsonl, atomic_bytes, audit_t8428_pages, date_profile, parse_rows,
    )

SOURCE_RUN = "20260814T180318Z_89b4b2fc50cf4a18adb3ca03f3b48813"
SOURCE_DIR = Path("data/landing/diagnostics/ls_t8428_t1633_followup") / SOURCE_RUN
START_CURSOR = "20160613"
CONTINUATION_KEY = "0"
CONTINUATION_KEY_SHA256 = "5feceb66ffc86f38d952786c6d696c79c2dbc239dd4e91b46729d73a27fb57e9"
MAX_PAGES = 12
MIN_INTERVAL_SECONDS = 1.05


def plan() -> dict[str, object]:
    return {
        "source": "LS_OPENAPI", "tr_code": "t8428", "endpoint": "/stock/investinfo",
        "request": {"fdate": "20000101", "tdate": "20260814", "gubun": "1", "upcode": "001", "cnt": 500},
        "adopted_source_run": SOURCE_RUN, "start_cursor": START_CURSOR,
        "continuation_key_sha256": CONTINUATION_KEY_SHA256, "max_pages": MAX_PAGES,
        "oauth_cap": 1, "retry_count": 0, "normalized_writes": False,
    }


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_adopted_pages(root: Path) -> list[list[dict[str, object]]]:
    source = root / SOURCE_DIR
    checkpoint = json.loads((source / "checkpoint.json").read_text(encoding="utf-8"))
    if checkpoint.get("status") != "PILOT_COMPLETE_REVIEW_REQUIRED" or checkpoint.get("secret_scan") != "PASS":
        raise ValueError("adopted checkpoint gate failed")
    pages = []
    for number in range(1, 6):
        raw_path = source / f"{number:02d}_t8428_page_{number}.response.json"
        provenance = json.loads((source / f"{number:02d}_t8428_page_{number}.provenance.json").read_text(encoding="utf-8"))
        raw = raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != provenance["raw_response_sha256"]:
            raise ValueError("adopted response hash mismatch")
        payload = json.loads(raw)
        rows, empty = parse_rows(payload, "t8428OutBlock1")
        if empty or len(rows) != 500:
            raise ValueError("adopted page shape mismatch")
        pages.append(rows)
    audit = audit_t8428_pages(pages)
    if audit["date_min"] != START_CURSOR or audit["conflicting_overlaps"]:
        raise ValueError("adopted continuation boundary mismatch")
    if hashlib.sha256(CONTINUATION_KEY.encode()).hexdigest() != CONTINUATION_KEY_SHA256:
        raise ValueError("continuation key binding mismatch")
    return pages


def finalize_retained_source_error(root: Path, run_id: str) -> dict[str, object]:
    matches = list((root / "data/landing/ls/t8428_surrounding_funds_raw").glob(f"plan=*/run={run_id}"))
    if len(matches) != 1:
        raise ValueError("retained run path mismatch")
    run_dir = matches[0]
    checkpoint_path, ledger_path = run_dir / "checkpoint.json", run_dir / "call_ledger.jsonl"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("status") not in {"STOPPED", "SOURCE_ERROR_BOUNDARY_STOP"} or checkpoint.get("data_calls") != 6:
        raise ValueError("retained stopped checkpoint mismatch")
    raw_path = run_dir / "06.response.json"
    raw = raw_path.read_bytes(); payload = json.loads(raw)
    if payload.get("rsp_cd") != "IGW40014" or "t8428OutBlock1" in payload:
        raise ValueError("retained source error mismatch")
    raw_sha = hashlib.sha256(raw).hexdigest()
    existing = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line]
    request_block = {**plan()["request"], "key_date": "20060601"}
    finalized_at = iso_utc(now_utc())
    provenance = {
        "schema": "stock_data.ls_t8428_raw_provenance_v1", "source": "LS_OPENAPI",
        "tr_code": "t8428", "endpoint": "/stock/investinfo", "plan_sha256": checkpoint["plan_sha256"],
        "page": 6, "request_block": request_block, "request_tr_cont": "Y",
        "request_tr_cont_key_sha256": CONTINUATION_KEY_SHA256, "captured_at": None,
        "http_status": 200, "rsp_cd": payload["rsp_cd"], "source_classification": "SOURCE_ERROR",
        "raw_response_sha256": raw_sha, "raw_response_bytes": len(raw), "normalized_writes": False,
        "offline_finalized": True, "offline_finalized_at": finalized_at,
    }
    atomic_json(run_dir / "06.provenance.json", provenance)
    failure_event = {"event": "HTTP_RESPONSE", "sequence": 6, "page": 6,
        "captured_at": None, "http_status": 200, "rsp_cd": payload["rsp_cd"],
        "row_count": 0, "raw_response_sha256": raw_sha, "raw_response_bytes": len(raw),
        "retry_count": 0, "outcome": "SOURCE_ERROR", "offline_finalized": True,
        "offline_finalized_at": finalized_at}
    existing = [row for row in existing if row.get("sequence") != 6]
    atomic_bytes(ledger_path, ("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in [*existing, failure_event])).encode("utf-8"))
    checkpoint.update({"status": "SOURCE_ERROR_BOUNDARY_STOP", "stopped_reason": "IGW40014",
        "observed_earliest_market_date": checkpoint["combined_audit"]["date_min"],
        "source_floor_status": "OBSERVED_EARLIEST_ONLY", "failed_cursor": "20060601",
        "failed_response_sha256": raw_sha, "offline_finalized_at": finalized_at})
    atomic_json(checkpoint_path, checkpoint)
    return checkpoint


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--confirm-live-bounded-backfill", action="store_true")
    parser.add_argument("--offline-finalize-source-error-run")
    args = parser.parse_args()
    if args.offline_finalize_source_error_run:
        result = finalize_retained_source_error(args.root, args.offline_finalize_source_error_run)
        print(json.dumps({"status": result["status"], "data_calls": result["data_calls"], "earliest": result["observed_earliest_market_date"]}, sort_keys=True))
        return 0
    frozen = plan()
    plan_sha = digest(frozen)
    adopted_pages = load_adopted_pages(args.root)
    if not args.confirm_live_bounded_backfill:
        print(json.dumps({"status": "NOT_EXECUTED_CONFIRMATION_REQUIRED", "max_pages": MAX_PAGES}, sort_keys=True))
        return 2

    load_dotenv(args.root / ".env", override=False)
    if not all(os.getenv(name, "") for name in REQUIRED_ENV):
        print(json.dumps({"status": "NOT_EXECUTED_CREDENTIALS_MISSING"}, sort_keys=True))
        return 2
    app_key, app_secret = credential_value("LS_APP_KEY"), credential_value("LS_APP_SECRET")
    base_url = official_base_url(os.environ["LS_BASE_URL"])
    if base_url != OFFICIAL_BASE_URL:
        raise ValueError("official base URL mismatch")

    run_id = now_utc().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = args.root / "data/landing/ls/t8428_surrounding_funds_raw" / f"plan={plan_sha}" / f"run={run_id}"
    checkpoint_path, ledger_path = run_dir / "checkpoint.json", run_dir / "call_ledger.jsonl"
    lock_path = args.root / "data/state/locks/ls_t8428_surrounding_funds.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        print(json.dumps({"status": "NOT_EXECUTED_LOCKED"}, sort_keys=True)); return 3
    os.write(fd, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode()); os.close(fd)

    session = requests.Session(); session.mount("https://", HTTPAdapter(max_retries=0))
    token = None; calls = 0; status = "RUN_CREATED"; reason = None
    cursor, header_key, last_call = START_CURSOR, CONTINUATION_KEY, None
    new_pages: list[list[dict[str, object]]] = []
    results: list[dict[str, object]] = []
    atomic_json(checkpoint_path, {"schema": "stock_data.ls_t8428_raw_v1", "run_id": run_id, "status": status, "plan": frozen, "plan_sha256": plan_sha, "oauth_calls": 0, "data_calls": 0, "results": [], "normalized_writes": False})
    try:
        started = now_utc(); auth = post_oauth_once(session, base_url + "/oauth2/token", app_key, app_secret); captured = now_utc()
        try: auth_payload = auth.json()
        except ValueError: auth_payload = {}
        token = auth_payload.get("access_token") if isinstance(auth_payload, dict) else None
        ok = auth.status_code == 200 and isinstance(token, str) and bool(token)
        code, message = safe_oauth_error(auth_payload, (app_key, app_secret))
        append_jsonl(ledger_path, {"event": "HTTP_RESPONSE", "operation": "oauth2/token", "sequence": 0, "started_at": iso_utc(started), "captured_at": iso_utc(captured), "http_status": auth.status_code, "error_code": code, "error_message": message, "retry_count": 0, "outcome": "PASS" if ok else "FAIL", "token_persisted": False})
        if not ok: raise ValueError("oauth failed")
        for page in range(1, MAX_PAGES + 1):
            if last_call is not None:
                time.sleep(max(0, MIN_INTERVAL_SECONDS - (time.monotonic() - last_call)))
            block = {**frozen["request"], "key_date": cursor}
            started = now_utc()
            response = session.post(base_url + "/stock/investinfo", headers={"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}", "tr_cd": "t8428", "tr_cont": "Y", "tr_cont_key": header_key}, json={"t8428InBlock": block}, timeout=30)
            last_call = time.monotonic(); captured = now_utc(); calls += 1
            raw = response.content; raw_sha = hashlib.sha256(raw).hexdigest()
            if _contains_secret(raw, (app_key, app_secret, token or "")): raise ValueError("secret echo")
            raw_path = run_dir / f"{page:02d}.response.json"; atomic_bytes(raw_path, raw)
            content_type = response.headers.get("content-type", "").lower()
            if response.status_code != 200 or "json" not in content_type: raise ValueError("transport or content-type anomaly")
            payload = response.json(); rows, empty = parse_rows(payload, "t8428OutBlock1")
            profile = date_profile(rows)
            if rows and not profile["strict_descending"]: raise ValueError("page order anomaly")
            next_key = response.headers.get("tr_cont_key", ""); cont = response.headers.get("tr_cont")
            out = payload.get("t8428OutBlock"); next_cursor = str(out.get("date", "")) if isinstance(out, dict) else ""
            provenance = {"schema": "stock_data.ls_t8428_raw_provenance_v1", "source": "LS_OPENAPI", "tr_code": "t8428", "endpoint": "/stock/investinfo", "plan_sha256": plan_sha, "page": page, "request_block": block, "request_tr_cont": "Y", "request_tr_cont_key_sha256": hashlib.sha256(header_key.encode()).hexdigest(), "captured_at": iso_utc(captured), "http_status": response.status_code, "rsp_cd": payload.get("rsp_cd"), "response_tr_cont": cont, "response_tr_cont_key_sha256": hashlib.sha256(next_key.encode()).hexdigest() if next_key else None, "raw_response_sha256": raw_sha, "raw_response_bytes": len(raw), **profile, "normalized_writes": False}
            atomic_json(run_dir / f"{page:02d}.provenance.json", provenance)
            append_jsonl(ledger_path, {"event": "HTTP_RESPONSE", "sequence": calls, "page": page, "started_at": iso_utc(started), "captured_at": iso_utc(captured), "http_status": response.status_code, "rsp_cd": payload.get("rsp_cd"), "row_count": len(rows), "raw_response_sha256": raw_sha, "raw_response_bytes": len(raw), "retry_count": 0, "outcome": "VALID_EMPTY" if empty else "PASS"})
            new_pages.append(rows); results.append({"page": page, **profile, "response_tr_cont": cont, "next_cursor": next_cursor})
            combined = audit_t8428_pages(adopted_pages + new_pages)
            if combined["conflicting_overlaps"]: raise ValueError("conflicting overlap")
            atomic_json(checkpoint_path, {"schema": "stock_data.ls_t8428_raw_v1", "run_id": run_id, "status": "CAPTURING", "plan": frozen, "plan_sha256": plan_sha, "oauth_calls": 1, "data_calls": calls, "results": results, "combined_audit": combined, "retry_count": 0, "normalized_writes": False})
            if empty or cont != "Y": status = "RAW_BACKFILL_COMPLETE_REVIEW_REQUIRED"; break
            if not next_cursor or not next_key: raise ValueError("continuation evidence missing")
            cursor, header_key = next_cursor, next_key
        else: status = "BOUNDED_STOP_CONTINUATION_REMAINS"
    except Exception as error:
        status, reason = "STOPPED", type(error).__name__
    finally:
        final = {"schema": "stock_data.ls_t8428_raw_v1", "run_id": run_id, "status": status, "stopped_reason": reason, "plan": frozen, "plan_sha256": plan_sha, "oauth_calls": 1, "data_calls": calls, "results": results, "retry_count": 0, "normalized_writes": False, "token_persisted": False, "completed_at": iso_utc(now_utc())}
        if new_pages: final["combined_audit"] = audit_t8428_pages(adopted_pages + new_pages)
        atomic_json(checkpoint_path, final)
        secret_ok = not any(_contains_secret(p.read_bytes(), (app_key, app_secret, token or "")) for p in run_dir.iterdir() if p.is_file())
        final["secret_scan"] = "PASS" if secret_ok else "FAIL"; atomic_json(checkpoint_path, final)
        try:
            if json.loads(lock_path.read_text())["run_id"] == run_id: lock_path.unlink()
        except (OSError, ValueError, KeyError): pass
    print(json.dumps({"status": status, "run_id": run_id, "data_calls": calls, "retry_count": 0, "secret_scan": final.get("secret_scan")}, sort_keys=True))
    return 0 if status == "RAW_BACKFILL_COMPLETE_REVIEW_REQUIRED" else 4


if __name__ == "__main__": sys.exit(main())
