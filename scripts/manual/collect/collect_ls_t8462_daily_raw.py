"""Append-only daily LS t8462 Raw collector; never writes Normalized data."""
from __future__ import annotations

import argparse
from datetime import date
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
    from scripts.manual.backfill.ls_derivatives_raw_backfill import (
        MIN_INTERVAL_SECONDS, _contains_secret, request_block, scopes, validate_payload,
    )
except ModuleNotFoundError:
    from ls_derivatives_investor_pilot import (  # type: ignore[no-redef]
        ENDPOINT, OFFICIAL_BASE_URL, REQUIRED_ENV, TR_CODE, atomic_json,
        credential_value, iso_utc, now_utc, official_base_url, post_oauth_once,
        safe_oauth_error,
    )
    from ls_derivatives_raw_backfill import (  # type: ignore[no-redef]
        MIN_INTERVAL_SECONDS, _contains_secret, request_block, scopes, validate_payload,
    )


RETENTION_QUERY_START = "20200101"
BASELINE_RUN_ID = "20260814T165922Z_da488bc5fd024f559b0ef70f6d340e1f"
MAX_DATA_CALLS = 18
INSTITUTION_FIELDS = ("sv_01", "sv_03", "sv_04", "sv_02", "sv_05", "sv_06", "sv_15", "sv_00")


def validate_market_date(value: str) -> str:
    if len(value) != 8 or not value.isdigit():
        raise ValueError("market date must be YYYYMMDD")
    parsed = date(int(value[:4]), int(value[4:6]), int(value[6:]))
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError("invalid market date")
    return value


def daily_scopes(market_date: str) -> list[dict[str, str]]:
    validate_market_date(market_date)
    if market_date < RETENTION_QUERY_START:
        raise ValueError("market date precedes retained query start")
    return [{**scope, "from_date": RETENTION_QUERY_START, "to_date": market_date} for scope in scopes()]


def scope_id(scope: dict[str, str]) -> str:
    return f"{scope['asset_code']}_{scope['product_code']}_{scope['requested_session_code']}"


def institution_reconciliation(rows: list[dict[str, object]], scope: dict[str, str]) -> list[dict[str, object]]:
    output = []
    for row in rows:
        components_sum = sum(int(row[field]) for field in INSTITUTION_FIELDS)
        provider_aggregate = int(row["sv_18"])
        difference = provider_aggregate - components_sum
        if difference == 0:
            status = "MATCH"
        elif (
            scope["asset_code"] == "K2I"
            and scope["product_code"] in {"C", "P"}
            and scope["requested_session_code"] == "U"
            and "20250718" <= str(row["date"]) <= "20251223"
        ):
            status = "OPTION_SPECIFIC_SEMANTICS"
        else:
            status = "PROVIDER_AGGREGATE_DIFFERENCE"
        output.append({
            "market_date": str(row["date"]),
            "institution_provider_aggregate": provider_aggregate,
            "institution_components_sum": components_sum,
            "institution_aggregate_difference": difference,
            "institution_aggregate_status": status,
        })
    return output


def baseline_retention(project_root: Path) -> dict[str, dict[str, str | None]]:
    root = project_root / "data/landing/ls_openapi/t8462_raw" / BASELINE_RUN_ID
    output = {}
    for sequence, scope in enumerate(scopes(), start=1):
        path = root / f"{sequence:02d}_{scope_id(scope)}.response.json"
        rows = validate_payload(json.loads(path.read_text(encoding="utf-8")), scope)
        dates = sorted(str(row["date"]) for row in rows)
        output[scope_id(scope)] = {
            "earliest_market_date": dates[0] if dates else None,
            "second_market_date": dates[1] if len(dates) > 1 else None,
        }
    return output


def retention_transition(previous: dict[str, str | None], dates: list[str]) -> str:
    if not dates or previous.get("earliest_market_date") is None:
        return "OBSERVED_EARLIEST_ONLY"
    current = dates[0]
    old = previous["earliest_market_date"]
    if current == old:
        return "OBSERVED_EARLIEST_ONLY"
    if current == previous.get("second_market_date"):
        return "ROLLING_RETENTION"
    if current > old:
        return "EARLIEST_MOVED_REVIEW_REQUIRED"
    return "EARLIEST_MOVED_BACKWARD_REVIEW_REQUIRED"


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_ledger(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _successful_attempt_exists(root: Path, market_date: str) -> bool:
    landing = root / "data/landing/ls_openapi/t8462_daily_raw" / market_date
    for path in landing.glob("*/checkpoint.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            payload.get("status") == "DAILY_COLLECTION_COMPLETE"
            and payload.get("secret_scan") == "PASS"
        ):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--confirm-live-daily-raw", action="store_true")
    args = parser.parse_args()
    market_date = validate_market_date(args.market_date)
    if not args.confirm_live_daily_raw:
        print(json.dumps({"status": "NOT_EXECUTED_CONFIRMATION_REQUIRED", "market_date": market_date}, sort_keys=True))
        return 2
    if _successful_attempt_exists(args.root, market_date):
        print(json.dumps({"status": "NOT_EXECUTED_ALREADY_ATTEMPTED", "market_date": market_date}, sort_keys=True))
        return 3

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
    run_dir = args.root / "data/landing/ls_openapi/t8462_daily_raw" / market_date / run_id
    lock_path = args.root / "data/state/locks/ls_t8462_daily_raw.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        print(json.dumps({"status": "NOT_EXECUTED_LOCKED"}, sort_keys=True))
        return 3
    os.write(descriptor, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode())
    os.close(descriptor)

    atomic_json(run_dir / "checkpoint.json", {
        "schema": "stock_data.ls_t8462_daily_raw_checkpoint_v1", "run_id": run_id,
        "market_date": market_date, "status": "RUN_CREATED", "oauth_calls": 0,
        "data_calls": 0, "retry_count": 0, "normalized_writes": False,
    })
    ledger_path = run_dir / "call_ledger.jsonl"
    ledger: list[dict[str, object]] = []
    token: str | None = None
    status = "AUTH_NOT_ATTEMPTED"
    stopped_reason: str | None = None
    data_calls = 0
    session = requests.Session()
    previous_retention = baseline_retention(args.root)
    current_retention: dict[str, dict[str, object]] = {}
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
        auth_event = {
            "event": "HTTP_RESPONSE", "operation": "oauth2/token", "sequence": 1,
            "retry_count": 0, "started_at": iso_utc(started), "captured_at": iso_utc(completed),
            "http_status": auth.status_code, "error_code": error_code,
            "error_message": error_message, "outcome": "PASS" if auth_ok else "FAIL",
            "credentials_persisted": False, "token_persisted": False,
        }
        ledger.append(auth_event)
        _append_ledger(ledger_path, auth_event)
        if not auth_ok:
            stopped_reason = "oauth_failed"
            status = "DAILY_COLLECTION_STOPPED"
        else:
            plan = daily_scopes(market_date)
            if len(plan) != MAX_DATA_CALLS:
                raise RuntimeError("daily scope count differs from hard call cap")
            for sequence, scope in enumerate(plan, start=1):
                if data_calls >= MAX_DATA_CALLS:
                    raise RuntimeError("daily data-call cap exceeded")
                if last_call is not None:
                    delay = MIN_INTERVAL_SECONDS - (time.monotonic() - last_call)
                    if delay > 0:
                        time.sleep(delay)
                started = now_utc()
                response = session.post(
                    base_url + ENDPOINT,
                    headers={
                        "content-type": "application/json; charset=utf-8",
                        "authorization": f"Bearer {token}", "tr_cd": TR_CODE,
                        "tr_cont": "N", "tr_cont_key": "",
                    },
                    json={"t8462InBlock": request_block(scope)}, timeout=30,
                )
                last_call = time.monotonic()
                completed = now_utc()
                data_calls += 1
                raw = response.content
                if _contains_secret(raw, (app_key, app_secret, token)):
                    stopped_reason = "secret_echo_in_response"
                    secret_event = {
                        "event": "HTTP_RESPONSE", "operation": ENDPOINT, "tr_code": TR_CODE,
                        "sequence": sequence, "retry_count": 0, "scope_id": scope_id(scope),
                        "started_at": iso_utc(started), "captured_at": iso_utc(completed),
                        "http_status": response.status_code,
                        "content_type": response.headers.get("content-type"),
                        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
                        "raw_response_bytes": len(raw), "raw_persisted": False,
                        "outcome": "FAIL_SECRET_ECHO",
                    }
                    ledger.append(secret_event)
                    _append_ledger(ledger_path, secret_event)
                    break
                label = f"{sequence:02d}_{scope_id(scope)}"
                raw_path = run_dir / f"{label}.response.json"
                _atomic_bytes(raw_path, raw)
                try:
                    payload = response.json()
                    rows = validate_payload(payload, scope)
                except (ValueError, TypeError, KeyError):
                    rows = []
                    payload = {}
                    stopped_reason = "response_validation_failed"
                dates = sorted(str(row["date"]) for row in rows)
                target_market_date_present = market_date in set(dates)
                if stopped_reason is None and not target_market_date_present:
                    stopped_reason = "target_market_date_missing"
                transition = retention_transition(previous_retention[scope_id(scope)], dates)
                current_retention[scope_id(scope)] = {
                    "earliest_market_date": dates[0] if dates else None,
                    "second_market_date": dates[1] if len(dates) > 1 else None,
                    "retention_status": transition,
                }
                provenance = {
                    "schema": "stock_data.ls_t8462_daily_raw_provenance_v1",
                    "source": "LS_OPENAPI", "tr_code": TR_CODE, **scope,
                    "captured_at": iso_utc(completed), "rsp_cd": payload.get("rsp_cd") if isinstance(payload, dict) else None,
                    "row_count": len(rows), "market_date_min": dates[0] if dates else None,
                    "market_date_max": dates[-1] if dates else None,
                    "target_market_date": market_date,
                    "target_market_date_present": target_market_date_present,
                    "raw_response_sha256": hashlib.sha256(raw).hexdigest(), "raw_response_bytes": len(raw),
                    "response_tr_cont": response.headers.get("tr_cont"),
                    "semantic_status": {
                        "sv": "CONFIRMED_EMPIRICAL_MULTI_PRODUCT_MULTI_DATE_SIGNED_NET_CONTRACTS",
                        "sa": "CONFIRMED_EMPIRICAL_MULTI_PRODUCT_MULTI_DATE_SIGNED_100_MILLION_KRW_NET_PURCHASE",
                        "U": "CONFIRMED_EMPIRICAL_ALL",
                        "D": "CONFIRMED_EMPIRICAL_REGULAR_WITH_CATEGORY_BOUNDARY",
                        "N": "CONFIRMED_EMPIRICAL_NIGHT_WITH_CATEGORY_BOUNDARY",
                        "investor_categories": "LS_NATIVE_CATEGORY",
                        "institution_other_classification": "KNOWN_PROVIDER_CLASSIFICATION_DIFFERENCE_NON_BLOCKING_FOR_RAW",
                        "session_finality": "UNRESOLVED",
                        "publication_revision_timing": "UNRESOLVED",
                        "predictive_pit": "BLOCKED",
                        "option_u_historical_difference": "AGGREGATE_FIELD_SEMANTICS",
                    },
                    "institution_reconciliation": institution_reconciliation(rows, scope),
                    "retention": current_retention[scope_id(scope)], "normalized_writes": False,
                }
                atomic_json(run_dir / f"{label}.provenance.json", provenance)
                call_ok = (
                    response.status_code == 200 and "json" in response.headers.get("content-type", "").lower()
                    and provenance["rsp_cd"] == "00000" and stopped_reason is None
                )
                call_event = {
                    "event": "HTTP_RESPONSE", "operation": ENDPOINT, "tr_code": TR_CODE,
                    "sequence": sequence, "retry_count": 0, "scope_id": scope_id(scope),
                    "started_at": iso_utc(started), "captured_at": iso_utc(completed),
                    "http_status": response.status_code, "rsp_cd": provenance["rsp_cd"],
                    "content_type": response.headers.get("content-type"),
                    "row_count": len(rows), "raw_response_sha256": provenance["raw_response_sha256"],
                    "raw_response_bytes": provenance["raw_response_bytes"],
                    "raw_persisted": True, "outcome": "PASS" if call_ok else "FAIL",
                }
                ledger.append(call_event)
                _append_ledger(ledger_path, call_event)
                atomic_json(run_dir / "checkpoint.json", {
                    "schema": "stock_data.ls_t8462_daily_raw_checkpoint_v1",
                    "run_id": run_id, "market_date": market_date, "status": "CAPTURING",
                    "oauth_calls": 1, "data_calls": data_calls, "retry_count": 0,
                    "latest_scope_id": scope_id(scope), "retention": current_retention,
                    "normalized_writes": False, "token_persisted": False,
                })
                if not call_ok:
                    if response.status_code in (403, 429):
                        stopped_reason = f"access_restriction_{response.status_code}"
                    break
            artifact_counts = {
                "raw_responses": len(list(run_dir.glob("*.response.json"))),
                "provenance_sidecars": len(list(run_dir.glob("*.provenance.json"))),
                "ledger_events": len(ledger),
            }
            complete = (
                data_calls == MAX_DATA_CALLS
                and artifact_counts == {
                    "raw_responses": MAX_DATA_CALLS,
                    "provenance_sidecars": MAX_DATA_CALLS,
                    "ledger_events": MAX_DATA_CALLS + 1,
                }
                and stopped_reason is None
            )
            status = "DAILY_COLLECTION_COMPLETE" if complete else "DAILY_COLLECTION_STOPPED"
    except Exception as error:
        status = "DAILY_COLLECTION_STOPPED"
        stopped_reason = type(error).__name__
    finally:
        if not ledger_path.exists():
            _atomic_bytes(ledger_path, b"")
        checkpoint = {
            "schema": "stock_data.ls_t8462_daily_raw_checkpoint_v1", "run_id": run_id,
            "market_date": market_date, "status": status, "oauth_calls": 1,
            "data_calls": data_calls, "retry_count": 0, "normalized_writes": False,
            "stopped_reason": stopped_reason, "retention": current_retention,
            "artifact_counts": {
                "raw_responses": len(list(run_dir.glob("*.response.json"))),
                "provenance_sidecars": len(list(run_dir.glob("*.provenance.json"))),
                "ledger_events": len(ledger),
            },
            "completed_at": iso_utc(now_utc()), "token_persisted": False,
        }
        atomic_json(run_dir / "checkpoint.json", checkpoint)
        paths = list(run_dir.glob("*"))
        secret_ok = not any(_contains_secret(path.read_bytes(), (app_key, app_secret, token or "")) for path in paths if path.is_file())
        checkpoint["secret_scan"] = "PASS" if secret_ok else "FAIL"
        if not secret_ok:
            status = "SECRET_SCAN_FAILED"
            checkpoint["status"] = status
        atomic_json(run_dir / "checkpoint.json", checkpoint)
        state_path = args.root / "data/state/ls_t8462_daily_raw.json"
        prior_runs = []
        if state_path.is_file():
            prior_runs = json.loads(state_path.read_text(encoding="utf-8")).get("runs", [])
        atomic_json(state_path, {
            "schema": "stock_data.ls_t8462_daily_raw_state_v1", "audit_status": "CLOSED",
            "collection_status": "DAILY_COLLECTION_ACTIVE" if status == "DAILY_COLLECTION_COMPLETE" else "DAILY_COLLECTION_STOPPED",
            "normalized_writes": False, "latest_retention": current_retention,
            "runs": [*prior_runs, {"run_id": run_id, "market_date": market_date, "status": status,
                "oauth_calls": 1, "data_calls": data_calls, "retry_count": 0,
                "landing_run": run_dir.relative_to(args.root).as_posix()}],
        })
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            if owner.get("run_id") == run_id:
                lock_path.unlink()
        except (FileNotFoundError, OSError, ValueError):
            pass
    print(json.dumps({"status": status, "run_id": run_id, "market_date": market_date, "oauth_calls": 1, "data_calls": data_calls, "retry_count": 0, "secret_scan": secret_ok}, sort_keys=True))
    return 0 if status == "DAILY_COLLECTION_COMPLETE" else 4


if __name__ == "__main__":
    sys.exit(main())
