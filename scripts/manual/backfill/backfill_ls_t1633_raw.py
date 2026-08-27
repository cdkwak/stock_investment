"""Bounded LS t1633 historical Raw acquisition; no Normalized writes."""
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
        append_jsonl, atomic_bytes, date_profile, parse_rows, program_identity_residuals,
    )
except ModuleNotFoundError:
    from ls_derivatives_investor_pilot import (  # type: ignore[no-redef]
        OFFICIAL_BASE_URL, REQUIRED_ENV, atomic_json, credential_value, iso_utc,
        now_utc, official_base_url, post_oauth_once, safe_oauth_error,
    )
    from ls_derivatives_raw_backfill import _contains_secret  # type: ignore[no-redef]
    from pilot_ls_t8428_t1633_followup import (  # type: ignore[no-redef]
        append_jsonl, atomic_bytes, date_profile, parse_rows, program_identity_residuals,
    )

START_DATE, END_DATE = "20000101", "20260814"
MIN_INTERVAL_SECONDS = 1.05
MAX_PAGES_PER_STREAM = 16
STREAMS = (
    {"id": "kospi_amount", "market": "0", "measure": "0"},
    {"id": "kospi_quantity", "market": "0", "measure": "1"},
    {"id": "kosdaq_amount", "market": "1", "measure": "0"},
    {"id": "kosdaq_quantity", "market": "1", "measure": "1"},
)


def frozen_plan(streams: tuple[dict[str, str], ...] = STREAMS, adopted_run: str | None = None) -> dict[str, object]:
    return {"source": "LS_OPENAPI", "tr_code": "t1633", "endpoint": "/stock/program",
        "start_date": START_DATE, "end_date": END_DATE, "streams": streams, "adopted_run": adopted_run,
        "max_pages_per_stream": MAX_PAGES_PER_STREAM, "max_data_calls": len(streams) * MAX_PAGES_PER_STREAM,
        "oauth_cap": 1, "retry_count": 0, "normalized_writes": False}


def plan_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def request_block(stream: dict[str, str], cursor: str) -> dict[str, str]:
    return {"gubun": stream["market"], "gubun1": stream["measure"], "gubun2": "0",
        "gubun3": "1", "fdate": START_DATE, "tdate": END_DATE, "gubun4": "0",
        "date": cursor, "exchgubun": "K"}


def audit_rows(rows: list[dict[str, object]]) -> dict[str, object]:
    profile = date_profile(rows)
    if rows and not profile["strict_descending"]:
        raise ValueError("source rows not reverse chronological")
    residuals = [program_identity_residuals(row) for row in rows]
    maxima = {name: max((abs(values[name]) for values in residuals), default=0) for name in (
        "total_buy_minus_sell_minus_net", "arbitrage_buy_minus_sell_minus_net",
        "non_arbitrage_buy_minus_sell_minus_net", "components_minus_total_net")}
    if any(value > 1 for value in maxima.values()):
        raise ValueError("program arithmetic anomaly")
    return {**profile, "max_abs_identity_residuals": maxima}


def fixed_point_boundary(rows: list[dict[str, object]], request_cursor: str, next_cursor: str) -> bool:
    return bool(rows) and next_cursor == request_cursor and all(str(row.get("date")) == request_cursor for row in rows)


def verify_adopted_kospi_amount(root: Path, run_id: str) -> None:
    matches = list((root / "data/landing/ls/t1633_program_trading_raw").glob(f"plan=*/run={run_id}"))
    if len(matches) != 1: raise ValueError("adopted run path mismatch")
    checkpoint = json.loads((matches[0] / "checkpoint.json").read_text(encoding="utf-8"))
    summary = checkpoint.get("stream_summary", {}).get("kospi_amount", {})
    if checkpoint.get("secret_scan") != "PASS" or checkpoint.get("data_calls") != 16:
        raise ValueError("adopted checkpoint gate failed")
    if summary != {"unique_dates": 6174, "date_min": "20010801", "date_max": "20260814"}:
        raise ValueError("adopted coverage mismatch")
    for page in range(1, 15):
        prefix = f"{page:03d}_kospi_amount_page_{page:02d}"
        raw = (matches[0] / f"{prefix}.response.json").read_bytes()
        provenance = json.loads((matches[0] / f"{prefix}.provenance.json").read_text(encoding="utf-8"))
        if hashlib.sha256(raw).hexdigest() != provenance["raw_response_sha256"]:
            raise ValueError("adopted response hash mismatch")


def finalize_adopted_kospi_amount(root: Path, run_id: str) -> dict[str, object]:
    verify_adopted_kospi_amount(root, run_id)
    run_dir = next((root / "data/landing/ls/t1633_program_trading_raw").glob(f"plan=*/run={run_id}"))
    path = run_dir / "checkpoint.json"; checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint.update({"status": "RAW_STREAM_COMPLETE_ADOPTED_WITH_REDUNDANT_BOUNDARY_PROBES",
        "stopped_reason": None, "source_floor_status": "FIXED_POINT_SOURCE_FLOOR",
        "observed_earliest_market_date": "20010801", "accepted_pages": list(range(1, 15)),
        "redundant_boundary_probe_pages": [15, 16], "normalized_writes": False,
        "offline_finalized_at": iso_utc(now_utc())})
    atomic_json(path, checkpoint); return checkpoint


def finalize_complete_run(root: Path, run_id: str) -> dict[str, object]:
    matches = list((root / "data/landing/ls/t1633_program_trading_raw").glob(f"plan=*/run={run_id}"))
    if len(matches) != 1: raise ValueError("complete run path mismatch")
    path = matches[0] / "checkpoint.json"; checkpoint = json.loads(path.read_text(encoding="utf-8"))
    expected = {"kospi_quantity": {"unique_dates": 6174, "date_min": "20010801", "date_max": "20260814"},
        "kosdaq_amount": {"unique_dates": 5280, "date_min": "20030113", "date_max": "20260814"},
        "kosdaq_quantity": {"unique_dates": 5280, "date_min": "20030113", "date_max": "20260814"}}
    if checkpoint.get("status") != "RAW_BACKFILL_COMPLETE_REVIEW_REQUIRED" or checkpoint.get("stream_summary") != expected:
        raise ValueError("complete run checkpoint mismatch")
    for provenance_path in matches[0].glob("*.provenance.json"):
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        raw_path = provenance_path.with_name(provenance_path.name.replace(".provenance.json", ".response.json"))
        if hashlib.sha256(raw_path.read_bytes()).hexdigest() != provenance["raw_response_sha256"]:
            raise ValueError("complete run response hash mismatch")
    checkpoint.update({"status": "RAW_BACKFILL_COMPLETE_WITH_LIMITS", "source_floor_status": "FIXED_POINT_SOURCE_FLOOR",
        "unit_semantics": {"amount": "UNIT_INFERRED_CROSS_SOURCE_KRW_MILLION", "quantity": "UNIT_INFERRED_MAGNITUDE_THOUSAND_SHARES"},
        "normalized_writes": False, "offline_finalized_at": iso_utc(now_utc())})
    atomic_json(path, checkpoint); return checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--confirm-live-bounded-backfill", action="store_true")
    parser.add_argument("--adopt-kospi-amount-run")
    parser.add_argument("--offline-finalize-complete-run"); args = parser.parse_args()
    if args.offline_finalize_complete_run:
        result = finalize_complete_run(args.root, args.offline_finalize_complete_run)
        print(json.dumps({"status": result["status"], "streams": result["stream_summary"]}, sort_keys=True)); return 0
    if args.adopt_kospi_amount_run and not args.confirm_live_bounded_backfill:
        result = finalize_adopted_kospi_amount(args.root, args.adopt_kospi_amount_run)
        print(json.dumps({"status": result["status"], "earliest": result["observed_earliest_market_date"]}, sort_keys=True))
        return 0
    selected_streams = STREAMS
    if args.adopt_kospi_amount_run:
        verify_adopted_kospi_amount(args.root, args.adopt_kospi_amount_run)
        selected_streams = STREAMS[1:]
    plan = frozen_plan(selected_streams, args.adopt_kospi_amount_run); plan_sha = plan_digest(plan)
    if not args.confirm_live_bounded_backfill:
        print(json.dumps({"status": "NOT_EXECUTED_CONFIRMATION_REQUIRED", "max_data_calls": plan["max_data_calls"]}, sort_keys=True)); return 2
    load_dotenv(args.root / ".env", override=False)
    if not all(os.getenv(name, "") for name in REQUIRED_ENV):
        print(json.dumps({"status": "NOT_EXECUTED_CREDENTIALS_MISSING"}, sort_keys=True)); return 2
    app_key, app_secret = credential_value("LS_APP_KEY"), credential_value("LS_APP_SECRET")
    base_url = official_base_url(os.environ["LS_BASE_URL"])
    if base_url != OFFICIAL_BASE_URL: raise ValueError("official base URL mismatch")
    run_id = now_utc().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = args.root / "data/landing/ls/t1633_program_trading_raw" / f"plan={plan_sha}" / f"run={run_id}"
    checkpoint_path, ledger_path = run_dir / "checkpoint.json", run_dir / "call_ledger.jsonl"
    lock_path = args.root / "data/state/locks/ls_t1633_program_trading.lock"; lock_path.parent.mkdir(parents=True, exist_ok=True)
    try: fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        print(json.dumps({"status": "NOT_EXECUTED_LOCKED"}, sort_keys=True)); return 3
    os.write(fd, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode()); os.close(fd)
    session = requests.Session(); session.mount("https://", HTTPAdapter(max_retries=0))
    token = None; calls = 0; status = "RUN_CREATED"; reason = None; last_call = None
    results: list[dict[str, object]] = []; all_dates: dict[str, dict[str, dict[str, object]]] = {}
    atomic_json(checkpoint_path, {"schema": "stock_data.ls_t1633_raw_v1", "run_id": run_id,
        "status": status, "plan": plan, "plan_sha256": plan_sha, "oauth_calls": 0,
        "data_calls": 0, "results": [], "retry_count": 0, "normalized_writes": False})
    try:
        started = now_utc(); auth = post_oauth_once(session, base_url + "/oauth2/token", app_key, app_secret); captured = now_utc()
        try: auth_payload = auth.json()
        except ValueError: auth_payload = {}
        token = auth_payload.get("access_token") if isinstance(auth_payload, dict) else None
        ok = auth.status_code == 200 and isinstance(token, str) and bool(token)
        code, message = safe_oauth_error(auth_payload, (app_key, app_secret))
        append_jsonl(ledger_path, {"event": "HTTP_RESPONSE", "operation": "oauth2/token", "sequence": 0,
            "started_at": iso_utc(started), "captured_at": iso_utc(captured), "http_status": auth.status_code,
            "error_code": code, "error_message": message, "retry_count": 0, "outcome": "PASS" if ok else "FAIL",
            "token_persisted": False})
        if not ok: raise ValueError("oauth failed")
        for stream in selected_streams:
            cursor, header_key = " ", ""; seen: dict[str, dict[str, object]] = {}; terminal = False
            for page in range(1, MAX_PAGES_PER_STREAM + 1):
                if last_call is not None: time.sleep(max(0, MIN_INTERVAL_SECONDS - (time.monotonic() - last_call)))
                block = request_block(stream, cursor); started = now_utc()
                response = session.post(base_url + "/stock/program", headers={"content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}", "tr_cd": "t1633", "tr_cont": "N" if page == 1 else "Y",
                    "tr_cont_key": header_key}, json={"t1633InBlock": block}, timeout=30)
                last_call = time.monotonic(); captured = now_utc(); calls += 1
                raw = response.content; raw_sha = hashlib.sha256(raw).hexdigest()
                if _contains_secret(raw, (app_key, app_secret, token or "")): raise ValueError("secret echo")
                label = f"{calls:03d}_{stream['id']}_page_{page:02d}"; raw_path = run_dir / f"{label}.response.json"; atomic_bytes(raw_path, raw)
                content_type = response.headers.get("content-type", "").lower()
                if response.status_code != 200 or "json" not in content_type: raise ValueError("transport or content-type anomaly")
                payload = response.json(); rows, empty = parse_rows(payload, "t1633OutBlock1"); audit = audit_rows(rows)
                for row in rows:
                    date = str(row["date"])
                    if date in seen and seen[date] != row: raise ValueError("conflicting overlap")
                    seen[date] = row
                cont, next_key = response.headers.get("tr_cont"), response.headers.get("tr_cont_key", "")
                out = payload.get("t1633OutBlock"); next_cursor = str(out.get("date", "")) if isinstance(out, dict) else ""
                provenance = {"schema": "stock_data.ls_t1633_raw_provenance_v1", "source": "LS_OPENAPI", "tr_code": "t1633",
                    "endpoint": "/stock/program", "plan_sha256": plan_sha, "stream": stream, "page": page,
                    "request_block": block, "request_tr_cont": "N" if page == 1 else "Y", "captured_at": iso_utc(captured),
                    "http_status": response.status_code, "rsp_cd": payload.get("rsp_cd"), "response_tr_cont": cont,
                    "response_tr_cont_key_sha256": hashlib.sha256(next_key.encode()).hexdigest() if next_key else None,
                    "raw_response_sha256": raw_sha, "raw_response_bytes": len(raw), **audit, "normalized_writes": False}
                atomic_json(run_dir / f"{label}.provenance.json", provenance)
                append_jsonl(ledger_path, {"event": "HTTP_RESPONSE", "sequence": calls, "stream": stream["id"], "page": page,
                    "started_at": iso_utc(started), "captured_at": iso_utc(captured), "http_status": response.status_code,
                    "rsp_cd": payload.get("rsp_cd"), "row_count": len(rows), "raw_response_sha256": raw_sha,
                    "raw_response_bytes": len(raw), "retry_count": 0, "outcome": "VALID_EMPTY" if empty else "PASS"})
                result = {"stream": stream["id"], "page": page, **audit, "response_tr_cont": cont, "next_cursor": next_cursor}
                results.append(result); all_dates[stream["id"]] = seen
                atomic_json(checkpoint_path, {"schema": "stock_data.ls_t1633_raw_v1", "run_id": run_id, "status": "CAPTURING",
                    "plan": plan, "plan_sha256": plan_sha, "oauth_calls": 1, "data_calls": calls, "results": results,
                    "stream_unique_dates": {key: len(value) for key, value in all_dates.items()}, "retry_count": 0, "normalized_writes": False})
                if empty or cont != "Y" or fixed_point_boundary(rows, cursor, next_cursor): terminal = True; break
                if not next_cursor or not next_key: raise ValueError("continuation evidence missing")
                cursor, header_key = next_cursor, next_key
            if not terminal: raise ValueError("page cap reached before terminal")
        status = "RAW_BACKFILL_COMPLETE_REVIEW_REQUIRED"
    except Exception as error:
        status, reason = "STOPPED", type(error).__name__
    finally:
        summary = {key: {"unique_dates": len(value), "date_min": min(value) if value else None,
            "date_max": max(value) if value else None} for key, value in all_dates.items()}
        final = {"schema": "stock_data.ls_t1633_raw_v1", "run_id": run_id, "status": status,
            "stopped_reason": reason, "plan": plan, "plan_sha256": plan_sha, "oauth_calls": 1,
            "data_calls": calls, "results": results, "stream_summary": summary, "retry_count": 0,
            "normalized_writes": False, "token_persisted": False, "unit_status": "UNIT_INFERRED_CROSS_SOURCE",
            "completed_at": iso_utc(now_utc())}
        atomic_json(checkpoint_path, final)
        secret_ok = not any(_contains_secret(path.read_bytes(), (app_key, app_secret, token or "")) for path in run_dir.iterdir() if path.is_file())
        final["secret_scan"] = "PASS" if secret_ok else "FAIL"; atomic_json(checkpoint_path, final)
        try:
            if json.loads(lock_path.read_text())["run_id"] == run_id: lock_path.unlink()
        except (OSError, ValueError, KeyError): pass
    print(json.dumps({"status": status, "run_id": run_id, "data_calls": calls, "streams": final["stream_summary"],
        "retry_count": 0, "secret_scan": final.get("secret_scan")}, sort_keys=True))
    return 0 if status == "RAW_BACKFILL_COMPLETE_REVIEW_REQUIRED" else 4


if __name__ == "__main__": sys.exit(main())
