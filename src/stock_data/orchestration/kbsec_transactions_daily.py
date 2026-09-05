"""Landing-first daily KB SWQA2301 transaction-to-cash-flow lane."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from stock_data.contracts.kbsec_transactions import (
    KBSEC_TRANSACTIONS_DAILY,
    KBSecTransactionCategory,
    KBSecTransactionDirection,
)
from stock_data.orchestration.current_observation_supervisor import (
    CurrentObservationProcessLock,
)
from stock_data.providers.kbsec.transactions import (
    KBSecTransactionContractError,
    KBSecTransactionsClient,
    OPERATION,
    continuation_key,
    normalize_landing_transaction_row,
    project_transaction_page_for_landing,
    transaction_request_body,
    validate_landing_transaction_page,
)


LANE = "KB_TRANSACTIONS_DAILY"
TASK_NAME = "STOCK_DATA_KB_TRANSACTIONS_DAILY"
KST = ZoneInfo("Asia/Seoul")
INITIAL_START_DATE = date(2025, 1, 1)
OVERLAP_DAYS = 7
MAX_PAGE_CALLS = 40
LANDING_ROOT = Path("data/landing/kbsec/transactions")
STATE_PATH = Path("data/state/kbsec_transactions_daily/state.json")
OCCURRENCE_ROOT = Path("data/state/kbsec_transactions_daily/occurrences")
LOCK_PATH = Path("data/state/kbsec_transactions_daily/operation.lock")
CASH_FLOWS_PATH = Path("artifacts/local_user/cash_flows.json")
RECEIPT_PATH = Path(
    "artifacts/scheduler_logs/STOCK_DATA_KB_TRANSACTIONS_DAILY_last.json"
)
_CONFIG_NAMES = ("KBSEC_BASE_URL", "KBSEC_APP_KEY", "KBSEC_APP_SECRET")
_FLOW_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_STATE_ROW_KEYS = {
    "date", "direction", "category", "summary_name",
    "transaction_type_code", "summary_type_code", "raw_row_sha256",
}


class KBSecTransactionsDailyError(RuntimeError):
    pass


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != body:
            raise OSError("atomic JSON readback differs")
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: object) -> None:
    _atomic_bytes(path, _json_bytes(payload))


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise KBSecTransactionsDailyError("retained JSON is unreadable") from error


def _load_state(root: Path) -> dict[str, Any]:
    path = root / STATE_PATH
    if not path.is_file():
        return {
            "schema_version": 1,
            "dataset": KBSEC_TRANSACTIONS_DAILY.name,
            "last_success_date": None,
            "last_retained_date": None,
            "rows": [],
        }
    payload = _load_json(path)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "dataset", "last_success_date", "last_retained_date", "rows",
    }:
        raise KBSecTransactionsDailyError("KB transaction state schema differs")
    if (
        payload["schema_version"] != 1
        or payload["dataset"] != KBSEC_TRANSACTIONS_DAILY.name
        or not isinstance(payload["rows"], list)
    ):
        raise KBSecTransactionsDailyError("KB transaction state identity differs")
    hashes: set[str] = set()
    dates: list[str] = []
    for row in payload["rows"]:
        if not isinstance(row, dict) or set(row) != _STATE_ROW_KEYS:
            raise KBSecTransactionsDailyError("KB transaction state row schema differs")
        digest = row["raw_row_sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise KBSecTransactionsDailyError("KB transaction state hash differs")
        if digest in hashes:
            raise KBSecTransactionsDailyError("KB transaction state hash is duplicated")
        hashes.add(digest)
        try:
            dates.append(date.fromisoformat(str(row["date"])).isoformat())
        except ValueError:
            raise KBSecTransactionsDailyError("KB transaction state date differs") from None
    expected_latest = max(dates) if dates else None
    if payload["last_retained_date"] != expected_latest:
        raise KBSecTransactionsDailyError("KB transaction state latest date differs")
    return payload


def request_window(
    now: datetime, *, last_retained_date: str | None,
) -> tuple[date, date]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("KB transaction lane requires a timezone-aware clock")
    end_date = now.astimezone(KST).date() - timedelta(days=1)
    overlap_start = end_date - timedelta(days=OVERLAP_DAYS - 1)
    if last_retained_date is None:
        start_date = INITIAL_START_DATE
    else:
        try:
            next_after_retained = date.fromisoformat(last_retained_date) + timedelta(days=1)
        except ValueError:
            raise KBSecTransactionsDailyError("last retained transaction date is invalid") from None
        start_date = min(overlap_start, next_after_retained)
    if start_date > end_date:
        start_date = overlap_start
    return start_date, end_date


def _request_plan(now: datetime, state: Mapping[str, Any]) -> dict[str, Any]:
    start_date, end_date = request_window(
        now, last_retained_date=state.get("last_retained_date"),
    )
    return {
        "lane": LANE,
        "dataset": KBSEC_TRANSACTIONS_DAILY.name,
        "due_time_kst": "07:20",
        "window": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "overlap_days": OVERLAP_DAYS,
        },
        "request": {
            "method": "POST",
            "path": "/api/v1/swqa2301",
            "first_page_body": transaction_request_body(start_date, end_date),
            "pagination": "dataBody.nxt_key until blank",
            "rows_per_page": 6,
            "max_page_calls": MAX_PAGE_CALLS,
        },
        "api_calls": 0,
    }


def _claim_occurrence(root: Path, now: datetime) -> tuple[Path, bool]:
    occurrence_date = now.astimezone(KST).date()
    path = root / OCCURRENCE_ROOT / f"{occurrence_date.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "lane": LANE,
        "occurrence_date": occurrence_date.isoformat(),
        "started_at_utc": now.astimezone(timezone.utc).isoformat(),
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return path, False
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_json_bytes(payload))
        stream.flush()
        os.fsync(stream.fileno())
    return path, True


def _safe_state_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in (
        "date", "direction", "category", "summary_name",
        "transaction_type_code", "summary_type_code", "raw_row_sha256",
    )}


def _ledger_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    amount = int(row["amount_krw"])
    if row["category"] == KBSecTransactionCategory.DIVIDEND.value:
        amount -= int(row["tax_krw"])
    if amount <= 0 or amount > 1_000_000_000_000_000:
        raise KBSecTransactionsDailyError("KB automatic cash-flow amount is not positive")
    if row["direction"] == KBSecTransactionDirection.OUT.value:
        amount = -amount
    digest = str(row["raw_row_sha256"])
    return {
        "id": f"kb_auto_{digest[:56]}",
        "date": row["date"],
        "amount_krw": amount,
        "account": "kb_auto",
        "memo": f"{row['category']} · {row['summary_name']}"[:200],
    }


def merge_cash_flow_ledger(
    project_root: Path, rows: Sequence[Mapping[str, Any]],
) -> tuple[int, str | None]:
    """Append non-OTHER rows under the existing cash-flow JSON v1 contract."""

    root = Path(project_root).resolve()
    path = root / CASH_FLOWS_PATH
    if path.is_file():
        payload = _load_json(path)
    else:
        payload = {"schema_version": 1, "entries": []}
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "entries"}
        or payload["schema_version"] != 1
        or not isinstance(payload["entries"], list)
    ):
        raise KBSecTransactionsDailyError("cash-flow ledger schema differs")
    entries = payload["entries"]
    existing_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "id", "date", "amount_krw", "account", "memo",
        }:
            raise KBSecTransactionsDailyError("cash-flow ledger entry schema differs")
        flow_id = entry["id"]
        if not isinstance(flow_id, str) or _FLOW_ID.fullmatch(flow_id) is None:
            raise KBSecTransactionsDailyError("cash-flow ledger entry id differs")
        if flow_id in existing_ids:
            raise KBSecTransactionsDailyError("cash-flow ledger id is duplicated")
        existing_ids.add(flow_id)

    additions: list[dict[str, Any]] = []
    for row in rows:
        if row["category"] == KBSecTransactionCategory.OTHER.value:
            continue
        entry = _ledger_entry(row)
        if entry["id"] not in existing_ids:
            additions.append(entry)
            existing_ids.add(entry["id"])
    if not additions:
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        return 0, digest
    updated = {"schema_version": 1, "entries": [*entries, *additions]}
    body = _json_bytes(updated)
    _atomic_bytes(path, body)
    return len(additions), hashlib.sha256(body).hexdigest()


def _receipt(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status", ""))
    receipt = {
        **payload,
        "task_name": TASK_NAME,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "scheduler_process_status": (
            "SUCCESS" if status.startswith(("COMPLETE", "NOOP")) else "FAIL"
        ),
    }
    _atomic_json(root / RECEIPT_PATH, receipt)
    return receipt


def _failure_receipt(
    root: Path, base: Mapping[str, Any], *, status: str, api_calls: int,
    landing_files: Sequence[str], error: Exception,
) -> dict[str, Any]:
    return _receipt(root, {
        **base,
        "status": status,
        "api_calls": api_calls,
        "landing_files": list(landing_files),
        "error_type": type(error).__name__,
        "ledger_entries_added": 0,
    })


def run_kbsec_transactions_daily(
    project_root: Path,
    *,
    now: datetime,
    environment: Mapping[str, str] | None = None,
    dry_run: bool = False,
    confirm_live: bool = False,
    client_factory: Callable[..., KBSecTransactionsClient] = KBSecTransactionsClient,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = _load_state(root)
    base = _request_plan(now, state)
    if dry_run:
        return {**base, "status": "DRY_RUN_PASS"}
    if not confirm_live:
        raise KBSecTransactionsDailyError("live execution requires --confirm-live")

    lock = CurrentObservationProcessLock(root / LOCK_PATH)
    if not lock.acquire():
        return _receipt(root, {**base, "status": "PROCESS_LOCKED_API_ZERO"})
    try:
        occurrence_path, claimed = _claim_occurrence(root, now)
        if not claimed:
            return _receipt(root, {
                **base,
                "status": "NOOP_DAILY_OCCURRENCE_ALREADY_CLAIMED",
                "occurrence": occurrence_path.relative_to(root).as_posix(),
            })
        values = environment or {}
        missing = [
            name for name in _CONFIG_NAMES
            if not isinstance(values.get(name), str) or not str(values[name]).strip()
        ]
        if missing:
            return _receipt(root, {
                **base,
                "status": "RUNTIME_CONFIG_REQUIRED_API_ZERO",
                "required_environment_names": list(_CONFIG_NAMES),
            })
        try:
            client = client_factory(
                base_url=values["KBSEC_BASE_URL"],
                app_key=values["KBSEC_APP_KEY"],
                app_secret=values["KBSEC_APP_SECRET"],
            )
        except Exception as error:
            return _failure_receipt(
                root, base, status="CLIENT_INITIALIZATION_FAILED_API_ZERO",
                api_calls=0, landing_files=(), error=error,
            )

        start_date = date.fromisoformat(base["window"]["start_date"])
        end_date = date.fromisoformat(base["window"]["end_date"])
        run_id = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        landing_dir = root / LANDING_ROOT / f"date={end_date.isoformat()}" / run_id
        next_key = ""
        seen_next_keys: set[str] = set()
        api_calls = 0
        landing_files: list[str] = []
        normalized: dict[str, dict[str, Any]] = {}
        while True:
            if api_calls >= MAX_PAGE_CALLS:
                return _failure_receipt(
                    root, base, status="PAGE_LIMIT_EXCEEDED_LANDING_PRESERVED",
                    api_calls=api_calls, landing_files=landing_files,
                    error=KBSecTransactionsDailyError("page call ceiling reached"),
                )
            try:
                api_calls += 1
                response = client.transaction_history_page(
                    start_date, end_date, next_key=next_key,
                )
                captured_at = datetime.now(timezone.utc)
                landing = project_transaction_page_for_landing(
                    response, retrieved_at=captured_at, page_number=api_calls,
                )
                landing_file = landing_dir / f"page_{api_calls:02d}.json"
                _atomic_json(landing_file, landing)
                landed = _load_json(landing_file)
                if landed != landing:
                    raise OSError("KB transaction Landing readback differs")
                landing_files.append(landing_file.relative_to(root).as_posix())
                validate_landing_transaction_page(landing)
                for raw_row in landing["rows"]:
                    row = normalize_landing_transaction_row(raw_row)
                    normalized.setdefault(row["raw_row_sha256"], row)
                continuation = continuation_key(response)
            except Exception as error:
                return _failure_receipt(
                    root, base, status="PAGE_ERROR_LANDING_PRESERVED",
                    api_calls=api_calls, landing_files=landing_files, error=error,
                )
            if not continuation:
                break
            if continuation in seen_next_keys:
                return _failure_receipt(
                    root, base, status="PAGINATION_LOOP_LANDING_PRESERVED",
                    api_calls=api_calls, landing_files=landing_files,
                    error=KBSecTransactionsDailyError("continuation key repeated"),
                )
            seen_next_keys.add(continuation)
            next_key = continuation

        incoming = sorted(
            normalized.values(), key=lambda row: (row["date"], row["raw_row_sha256"]),
        )
        try:
            ledger_added, ledger_sha256 = merge_cash_flow_ledger(root, incoming)
            retained = {
                str(row["raw_row_sha256"]): dict(row)
                for row in state["rows"]
            }
            for row in incoming:
                retained.setdefault(row["raw_row_sha256"], _safe_state_row(row))
            state_rows = sorted(
                retained.values(), key=lambda row: (row["date"], row["raw_row_sha256"]),
            )
            last_retained = max((str(row["date"]) for row in state_rows), default=None)
            _atomic_json(root / STATE_PATH, {
                "schema_version": 1,
                "dataset": KBSEC_TRANSACTIONS_DAILY.name,
                "last_success_date": now.astimezone(KST).date().isoformat(),
                "last_retained_date": last_retained,
                "rows": state_rows,
            })
        except Exception as error:
            return _failure_receipt(
                root, base, status="PROMOTION_ERROR_PRIOR_STATE_PRESERVED",
                api_calls=api_calls, landing_files=landing_files, error=error,
            )
        category_counts = Counter(row["category"] for row in incoming)
        return _receipt(root, {
            **base,
            "status": "COMPLETE",
            "api_calls": api_calls,
            "landing_files": landing_files,
            "rows_observed": len(incoming),
            "category_counts": dict(sorted(category_counts.items())),
            "ledger_entries_added": ledger_added,
            "ledger_sha256": ledger_sha256,
            "state": STATE_PATH.as_posix(),
            "occurrence": occurrence_path.relative_to(root).as_posix(),
        })
    finally:
        lock.release()


def _load_environment(project_root: Path) -> dict[str, str]:
    file_values = dotenv_values(
        project_root.resolve() / ".env", encoding="utf-8", interpolate=False,
    )
    return {
        name: os.environ.get(name) or (
            file_values.get(name) if isinstance(file_values.get(name), str) else ""
        )
        for name in _CONFIG_NAMES
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the read-only KB SWQA2301 daily cash-flow lane",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args(argv)
    now = args.as_of or datetime.now(timezone.utc)
    environment = None if args.dry_run else _load_environment(args.project_root)
    try:
        result = run_kbsec_transactions_daily(
            args.project_root,
            now=now,
            environment=environment,
            dry_run=args.dry_run,
            confirm_live=args.confirm_live,
        )
    except (KBSecTransactionsDailyError, ValueError):
        result = {
            "lane": LANE,
            "status": "CLI_FAILURE",
            "api_calls": 0,
            "error_type": "SANITIZED_INTERNAL_FAILURE",
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if str(result["status"]).startswith(("COMPLETE", "NOOP", "DRY_RUN")) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CASH_FLOWS_PATH",
    "INITIAL_START_DATE",
    "KBSecTransactionsDailyError",
    "LANDING_ROOT",
    "LANE",
    "MAX_PAGE_CALLS",
    "OCCURRENCE_ROOT",
    "RECEIPT_PATH",
    "STATE_PATH",
    "merge_cash_flow_ledger",
    "request_window",
    "run_kbsec_transactions_daily",
]
