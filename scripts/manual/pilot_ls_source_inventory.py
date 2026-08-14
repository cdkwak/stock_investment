"""Bounded, retry-free LS OpenAPI source inventory pilot; Raw evidence only."""
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

try:
    from scripts.manual.ls_derivatives_investor_pilot import (
        OFFICIAL_BASE_URL, REQUIRED_ENV, atomic_json, credential_value, iso_utc,
        now_utc, official_base_url, post_oauth_once, safe_oauth_error,
    )
    from scripts.manual.ls_derivatives_raw_backfill import _contains_secret
except ModuleNotFoundError:
    from ls_derivatives_investor_pilot import (  # type: ignore[no-redef]
        OFFICIAL_BASE_URL, REQUIRED_ENV, atomic_json, credential_value, iso_utc,
        now_utc, official_base_url, post_oauth_once, safe_oauth_error,
    )
    from ls_derivatives_raw_backfill import _contains_secret  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[2]
MAX_DATA_CALLS = 11
MIN_INTERVAL_SECONDS = 1.05
TARGET_DATE = "20260814"


def frozen_scopes() -> list[dict[str, object]]:
    return [
        {"id": "program_kospi_20260814", "endpoint": "/stock/program", "tr_code": "t1633", "block": {"gubun": "0", "gubun1": "0", "gubun2": "0", "gubun3": "1", "fdate": TARGET_DATE, "tdate": TARGET_DATE, "gubun4": "0", "date": " ", "exchgubun": "K"}, "output": "t1633OutBlock1"},
        {"id": "program_kospi_20000104", "endpoint": "/stock/program", "tr_code": "t1633", "block": {"gubun": "0", "gubun1": "0", "gubun2": "0", "gubun3": "1", "fdate": "20000104", "tdate": "20000104", "gubun4": "0", "date": " ", "exchgubun": "K"}, "output": "t1633OutBlock1"},
        {"id": "program_kosdaq_20260814", "endpoint": "/stock/program", "tr_code": "t1633", "block": {"gubun": "1", "gubun1": "0", "gubun2": "0", "gubun3": "1", "fdate": TARGET_DATE, "tdate": TARGET_DATE, "gubun4": "0", "date": " ", "exchgubun": "K"}, "output": "t1633OutBlock1"},
        {"id": "funds_recent", "endpoint": "/stock/investinfo", "tr_code": "t8428", "block": {"fdate": "20200101", "tdate": TARGET_DATE, "gubun": "1", "key_date": " ", "upcode": "001", "cnt": 500}, "output": "t8428OutBlock1"},
        {"id": "funds_20000104", "endpoint": "/stock/investinfo", "tr_code": "t8428", "block": {"fdate": "20000104", "tdate": "20000104", "gubun": "1", "key_date": " ", "upcode": "001", "cnt": 10}, "output": "t8428OutBlock1"},
        {"id": "kospi200_futures_master", "endpoint": "/futureoption/market-data", "tr_code": "t8467", "block": {"gubun": ""}, "output": "t8467OutBlock"},
        {"id": "expired_future_daily", "endpoint": "/futureoption/market-data", "tr_code": "t2214", "block": {"shcode": "A0166000", "futcheck": "0", "date": "", "cts_code": "", "lastdate": "", "cnt": 500}, "output": "t2214OutBlock1"},
        {"id": "current_future_open_interest", "endpoint": "/futureoption/market-data", "tr_code": "t2424", "block": None, "output": "t2424OutBlock1"},
        {"id": "etf_kodex200_daily", "endpoint": "/stock/etf", "tr_code": "t1903", "block": {"shcode": "069500", "date": ""}, "output": "t1903OutBlock1"},
        {"id": "samsung_foreign_holding", "endpoint": "/stock/frgr-itt", "tr_code": "t1716", "block": {"shcode": "005930", "gubun": "0", "fromdt": "19900101", "todt": TARGET_DATE, "prapp": 0, "prgubun": "0", "orggubun": "0", "frggubun": "0", "exchgubun": "K"}, "output": "t1716OutBlock"},
        {"id": "samsung_fundamentals", "endpoint": "/stock/investinfo", "tr_code": "t3320", "block": {"gicode": "005930"}, "output": "t3320OutBlock1"},
    ]


def _plan_digest(plan: list[dict[str, object]]) -> str:
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _row_dates(rows: object) -> list[str]:
    if not isinstance(rows, list):
        return []
    values = []
    for row in rows:
        if isinstance(row, dict):
            value = row.get("date", row.get("dt"))
            if value is not None:
                values.append(str(value)[:8])
    return sorted(set(values))


def _output_rows(payload: object, output_key: str) -> tuple[object, bool]:
    if not isinstance(payload, dict):
        return None, False
    rows = payload.get(output_key)
    valid_empty = (
        payload.get("rsp_cd") == "00000"
        and output_key not in payload
        and set(payload).issubset({"rsp_cd", "rsp_msg"})
    )
    return ([] if valid_empty else rows), valid_empty


def _select_current_future(payload: object) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("t8467OutBlock"), list):
        raise ValueError("futures master rows missing")
    for row in payload["t8467OutBlock"]:
        if isinstance(row, dict) and str(row.get("shcode", "")).startswith("A") and not str(row.get("hname", "")).startswith("F SP"):
            return str(row["shcode"])
    raise ValueError("current KOSPI200 futures code unavailable")


def _verified_resume(root: Path, run_dir: Path, plan: list[dict[str, object]], plan_digest: str) -> tuple[list[dict[str, object]], str]:
    allowed = (root / "data/landing/diagnostics/ls_openapi_source_inventory").resolve()
    resolved = run_dir.resolve()
    if resolved.parent != allowed or run_dir.is_symlink():
        raise ValueError("resume run must be a plain immediate child of the LS inventory Landing root")
    checkpoint = json.loads((resolved / "checkpoint.json").read_text(encoding="utf-8"))
    if checkpoint.get("plan_sha256") != plan_digest or checkpoint.get("plan") != plan:
        raise ValueError("resume plan mismatch")
    if checkpoint.get("status") != "PILOT_STOPPED" or checkpoint.get("secret_scan") != "PASS":
        raise ValueError("resume source is not an audited stopped run")
    completed = int(checkpoint.get("data_calls", -1))
    results = checkpoint.get("results")
    if not isinstance(results, list) or len(results) != completed or not 0 < completed < MAX_DATA_CALLS:
        raise ValueError("resume result count mismatch")
    for sequence, result in enumerate(results, start=1):
        if not isinstance(result, dict) or result.get("scope_id") != plan[sequence - 1]["id"]:
            raise ValueError("resume scope order mismatch")
        label = f"{sequence:02d}_{plan[sequence - 1]['id']}"
        raw_path = resolved / f"{label}.response.json"
        provenance_path = resolved / f"{label}.provenance.json"
        if raw_path.is_symlink() or provenance_path.is_symlink() or not raw_path.is_file() or not provenance_path.is_file():
            raise ValueError("resume evidence topology mismatch")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        if provenance.get("scope_id") != result["scope_id"] or provenance.get("plan_sha256") != plan_digest:
            raise ValueError("resume provenance mismatch")
        if hashlib.sha256(raw_path.read_bytes()).hexdigest() != provenance.get("raw_response_sha256"):
            raise ValueError("resume raw hash mismatch")
    return results, str(checkpoint["run_id"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--confirm-live-bounded-pilot", action="store_true")
    parser.add_argument("--resume-run-dir", type=Path)
    args = parser.parse_args()
    plan = frozen_scopes()
    plan_digest = _plan_digest(plan)
    if len(plan) != MAX_DATA_CALLS:
        raise RuntimeError("frozen LS call plan differs from hard cap")
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

    adopted_results: list[dict[str, object]] = []
    continuation_of: str | None = None
    if args.resume_run_dir is not None:
        adopted_results, continuation_of = _verified_resume(args.root.resolve(), args.resume_run_dir, plan, plan_digest)
    start_index = len(adopted_results)

    run_id = now_utc().strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = args.root / "data/landing/diagnostics/ls_openapi_source_inventory" / run_id
    ledger_path = run_dir / "call_ledger.jsonl"
    checkpoint_path = run_dir / "checkpoint.json"
    lock_path = args.root / "data/state/locks/ls_openapi_source_inventory.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        print(json.dumps({"status": "NOT_EXECUTED_LOCKED"}, sort_keys=True))
        return 3
    os.write(descriptor, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode("utf-8"))
    os.close(descriptor)

    atomic_json(checkpoint_path, {"schema": "stock_data.ls_source_inventory_pilot_v1", "run_id": run_id, "continuation_of": continuation_of, "status": "RUN_CREATED", "plan": plan, "plan_sha256": plan_digest, "oauth_calls": 0, "network_data_calls": 0, "adopted_data_calls": start_index, "data_calls": start_index, "max_data_calls": MAX_DATA_CALLS, "retry_count": 0, "normalized_writes": False})
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=0)
    session.mount("https://", adapter)
    token: str | None = None
    data_calls = 0
    status = "AUTH_NOT_ATTEMPTED"
    stopped_reason: str | None = None
    results: list[dict[str, object]] = list(adopted_results)
    selected_future: str | None = None
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
        _append_jsonl(ledger_path, {"event": "HTTP_RESPONSE", "operation": "oauth2/token", "sequence": 1, "retry_count": 0, "started_at": iso_utc(started), "captured_at": iso_utc(completed), "http_status": auth.status_code, "error_code": error_code, "error_message": error_message, "outcome": "PASS" if auth_ok else "FAIL", "credentials_persisted": False, "token_persisted": False})
        if not auth_ok:
            status = "PILOT_STOPPED"
            stopped_reason = "oauth_failed"
        else:
            for sequence, scope in enumerate(plan[start_index:], start=start_index + 1):
                if start_index + data_calls >= MAX_DATA_CALLS:
                    raise RuntimeError("data-call cap exceeded")
                block = scope["block"]
                if scope["id"] == "current_future_open_interest":
                    if not selected_future:
                        raise ValueError("current futures master did not provide a usable code")
                    block = {"focode": selected_future, "bdgubun": "2", "nmin": 0, "tcgubun": "0", "cnt": 20}
                if last_call is not None:
                    delay = MIN_INTERVAL_SECONDS - (time.monotonic() - last_call)
                    if delay > 0:
                        time.sleep(delay)
                started = now_utc()
                response = session.post(base_url + str(scope["endpoint"]), headers={"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}", "tr_cd": str(scope["tr_code"]), "tr_cont": "N", "tr_cont_key": ""}, json={f"{scope['tr_code']}InBlock": block}, timeout=30)
                last_call = time.monotonic()
                completed = now_utc()
                data_calls += 1
                raw = response.content
                digest = hashlib.sha256(raw).hexdigest()
                if _contains_secret(raw, (app_key, app_secret, token)):
                    _append_jsonl(ledger_path, {"event": "HTTP_RESPONSE", "operation": scope["endpoint"], "tr_code": scope["tr_code"], "sequence": sequence, "retry_count": 0, "http_status": response.status_code, "raw_response_sha256": digest, "raw_response_bytes": len(raw), "raw_persisted": False, "outcome": "FAIL_SECRET_ECHO"})
                    stopped_reason = "secret_echo"
                    break
                label = f"{sequence:02d}_{scope['id']}"
                raw_path = run_dir / f"{label}.response.json"
                _atomic_bytes(raw_path, raw)
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                rows, valid_empty = _output_rows(payload, str(scope["output"]))
                content_type = response.headers.get("content-type", "").lower()
                valid_shape = (
                    response.status_code == 200
                    and "json" in content_type
                    and isinstance(payload, dict)
                    and payload.get("rsp_cd") == "00000"
                    and (isinstance(rows, (list, dict)) or valid_empty)
                )
                if scope["tr_code"] == "t8467" and valid_shape:
                    selected_future = _select_current_future(payload)
                dates = _row_dates(rows)
                provenance = {"schema": "stock_data.ls_source_inventory_provenance_v1", "source": "LS_OPENAPI", "plan_sha256": plan_digest, "tr_code": scope["tr_code"], "endpoint": scope["endpoint"], "scope_id": scope["id"], "request_block": block, "captured_at": iso_utc(completed), "http_status": response.status_code, "response_content_type": content_type, "rsp_cd": payload.get("rsp_cd") if isinstance(payload, dict) else None, "source_classification": "VALID_EMPTY" if valid_empty else "ROWS", "row_count": len(rows) if isinstance(rows, list) else (1 if isinstance(rows, dict) else 0), "date_min": dates[0] if dates else None, "date_max": dates[-1] if dates else None, "raw_response_sha256": digest, "raw_response_bytes": len(raw), "response_tr_cont": response.headers.get("tr_cont"), "response_tr_cont_key_present": bool(response.headers.get("tr_cont_key")), "normalized_writes": False}
                atomic_json(run_dir / f"{label}.provenance.json", provenance)
                outcome = "PASS" if valid_shape else "FAIL"
                event = {"event": "HTTP_RESPONSE", "operation": scope["endpoint"], "tr_code": scope["tr_code"], "scope_id": scope["id"], "sequence": sequence, "retry_count": 0, "started_at": iso_utc(started), "captured_at": iso_utc(completed), "http_status": response.status_code, "rsp_cd": provenance["rsp_cd"], "row_count": provenance["row_count"], "raw_response_sha256": digest, "raw_response_bytes": len(raw), "raw_persisted": True, "outcome": outcome}
                _append_jsonl(ledger_path, event)
                results.append({"scope_id": scope["id"], "tr_code": scope["tr_code"], "row_count": provenance["row_count"], "date_min": provenance["date_min"], "date_max": provenance["date_max"], "rsp_cd": provenance["rsp_cd"], "tr_cont": provenance["response_tr_cont"]})
                atomic_json(checkpoint_path, {"schema": "stock_data.ls_source_inventory_pilot_v1", "run_id": run_id, "continuation_of": continuation_of, "status": "CAPTURING", "plan": plan, "plan_sha256": plan_digest, "oauth_calls": 1, "network_data_calls": data_calls, "adopted_data_calls": start_index, "data_calls": start_index + data_calls, "max_data_calls": MAX_DATA_CALLS, "retry_count": 0, "results": results, "normalized_writes": False})
                if outcome != "PASS":
                    stopped_reason = f"scope_failed:{scope['id']}"
                    break
            status = "PILOT_COMPLETE_REVIEW_REQUIRED" if start_index + data_calls == MAX_DATA_CALLS and stopped_reason is None else "PILOT_STOPPED"
    except Exception as error:
        status = "PILOT_STOPPED"
        stopped_reason = type(error).__name__
    finally:
        checkpoint = {"schema": "stock_data.ls_source_inventory_pilot_v1", "run_id": run_id, "continuation_of": continuation_of, "status": status, "plan": plan, "plan_sha256": plan_digest, "oauth_calls": 1, "network_data_calls": data_calls, "adopted_data_calls": start_index, "data_calls": start_index + data_calls, "retry_count": 0, "max_data_calls": MAX_DATA_CALLS, "stopped_reason": stopped_reason, "results": results, "normalized_writes": False, "token_persisted": False, "completed_at": iso_utc(now_utc())}
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
    print(json.dumps({"status": status, "run_id": run_id, "continuation_of": continuation_of, "oauth_calls": 1, "network_data_calls": data_calls, "adopted_data_calls": start_index, "data_calls": start_index + data_calls, "retry_count": 0, "secret_scan": secret_ok}, sort_keys=True))
    return 0 if status == "PILOT_COMPLETE_REVIEW_REQUIRED" else 4


if __name__ == "__main__":
    sys.exit(main())
