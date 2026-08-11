from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Callable, Iterable

import pandas as pd

from stock_data.contracts.tossinvest_historical import KR_TREASURY_YIELD_DAILY
from stock_data.providers.tossinvest import (
    TossInvestAuthenticationError,
    TossInvestClient,
    TossInvestHTTPError,
    TossInvestRateLimit,
    TossInvestRateLimitError,
    TossInvestResponseError,
    TossInvestTimeoutError,
)
from stock_data.providers.tossinvest.historical import (
    TREASURY_YIELD_OPERATION,
    normalize_treasury_yield,
)
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.validation.tossinvest_historical import validate_toss_historical


FORBIDDEN_LANDING_KEYS = {
    "authorization", "access_token", "refresh_token", "client_id", "client_secret"
}
TREASURY_INSTRUMENTS = tuple(
    f"KR_BOND_{tenor}Y" for tenor in (2, 3, 5, 10, 20, 30)
)
_LANDING_PAGE = re.compile(r"_p(?P<page>\d{5})\.json$")


class TossHistoricalError(RuntimeError):
    pass


class TossRateLimitHeaderError(TossHistoricalError):
    pass


@dataclass(frozen=True)
class TossHistoricalResult:
    dataset: str
    status: str
    token_calls: int
    market_calls: int
    completed_targets: int
    valid_empty_targets: int
    failed_targets: int
    rows: int


class TossHistoricalState:
    def __init__(self, path: Path, dataset: str, payload: dict[str, Any]):
        self.path = path
        self.dataset = dataset
        self.payload = payload

    @classmethod
    def load(cls, path: Path, dataset: str) -> "TossHistoricalState":
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("dataset") != dataset:
                raise ValueError("Toss checkpoint dataset mismatch")
        else:
            payload = {
                "dataset": dataset,
                "status": "pending",
                "completed_targets": [],
                "valid_empty_targets": [],
                "failed_targets": {},
                "progress": {},
                "token_calls": 0,
                "market_calls": 0,
                "last_rate_limit": None,
            }
        return cls(path, dataset, payload)

    @property
    def completed(self) -> set[str]:
        return set(self.payload.get("completed_targets", []))

    @property
    def valid_empty(self) -> set[str]:
        return set(self.payload.get("valid_empty_targets", []))

    @property
    def progress(self) -> dict[str, dict[str, Any]]:
        return self.payload.setdefault("progress", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".json.tmp", prefix=self.path.stem + "_",
                dir=self.path.parent, delete=False,
            ) as handle:
                json.dump(self.payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def mark_completed(self, targets: Iterable[str]) -> None:
        values = {str(value) for value in targets}
        self.payload["completed_targets"] = sorted(self.completed | values)
        self.payload["valid_empty_targets"] = sorted(self.valid_empty - values)
        for target in values:
            self.progress.pop(target, None)
            self.payload.setdefault("failed_targets", {}).pop(target, None)
        self.save()

    def mark_empty(self, target: str) -> None:
        self.payload["valid_empty_targets"] = sorted(self.valid_empty | {target})
        self.progress.pop(target, None)
        self.payload.setdefault("failed_targets", {}).pop(target, None)
        self.save()

    def mark_failed(self, target: str, error: Exception) -> None:
        self.payload.setdefault("failed_targets", {})[target] = type(error).__name__
        self.payload["status"] = "stopped_error"
        self.save()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _assert_no_secrets(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json.tmp", prefix="toss_",
            dir=path.parent, delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise RuntimeError("Toss landing read-back differs")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _assert_no_secrets(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in FORBIDDEN_LANDING_KEYS:
                raise ValueError("sensitive field is forbidden in Toss landing")
            _assert_no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_secrets(item)


def _rate_payload(rate: TossInvestRateLimit) -> dict[str, Any]:
    return {
        "group": rate.group,
        "limit": rate.limit,
        "remaining": rate.remaining,
        "reset_seconds": rate.reset_seconds,
        "retry_after_seconds": rate.retry_after_seconds,
    }


def _pace(rate: TossInvestRateLimit) -> None:
    if rate.limit is None or rate.remaining is None or rate.reset_seconds is None:
        raise TossRateLimitHeaderError("Toss rate-limit headers are incomplete")
    if rate.remaining <= 1:
        time.sleep(max(rate.reset_seconds, rate.retry_after_seconds or 0))


def _request(client: TossInvestClient, state: TossHistoricalState, path: str,
             params: dict[str, Any]):
    for attempt in range(2):
        state.payload["market_calls"] = int(state.payload.get("market_calls", 0)) + 1
        try:
            return client.get_market_data(path, params=params)
        except TossInvestRateLimitError:
            state.payload["status"] = "stopped_429"
            state.save()
            raise
        except TossInvestTimeoutError:
            if attempt == 0:
                time.sleep(1.0)
                continue
            raise
        except TossInvestHTTPError as error:
            status = error.details.http_status if error.details else None
            if status is not None and status >= 500 and attempt == 0:
                time.sleep(1.0)
                continue
            raise
    raise AssertionError("unreachable")


def _extract(payload: dict[str, Any], row_key: str, cursor_key: str) -> tuple[list[dict[str, Any]], str | None]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise TossInvestResponseError("Toss historical result must be an object")
    rows = result.get(row_key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise TossInvestResponseError(f"Toss result.{row_key} must be an array")
    cursor = result.get(cursor_key)
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        raise TossInvestResponseError(f"Toss result.{cursor_key} is invalid")
    return rows, cursor


def _normalize_partition_dates(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ("date", "source_date"):
        if column in result:
            result[column] = (
                pd.to_datetime(result[column], errors="raise")
                .dt.strftime("%Y-%m-%d")
                .astype("string")
            )
    if "availability_date" in result:
        original = result["availability_date"]
        parsed = pd.to_datetime(original, errors="coerce")
        if (original.notna() & parsed.isna()).any():
            raise ValueError("invalid availability_date")
        result["availability_date"] = parsed.dt.strftime("%Y-%m-%d").astype("string")
    for column in ("collected_at", "updated_at"):
        if column in result:
            result[column] = pd.to_datetime(result[column], utc=True, errors="coerce")
    return result


def build_treasury_from_landing(
    project_root: Path,
    *,
    instruments: Iterable[str] = TREASURY_INSTRUMENTS,
    expected_files: int = 60,
    expected_rows: int = 11_162,
) -> pd.DataFrame:
    targets = tuple(str(value) for value in instruments)
    if len(targets) != len(set(targets)) or not targets:
        raise ValueError("treasury instruments must be unique and nonempty")
    if not set(targets) <= set(TREASURY_INSTRUMENTS):
        raise ValueError("unsupported Korean treasury instrument")

    state_path = project_root / "data/state/toss_kr_treasury_yield_daily.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("dataset") != KR_TREASURY_YIELD_DAILY.name:
        raise RuntimeError("treasury state dataset mismatch")
    if state.get("status") != "complete":
        raise RuntimeError("treasury state is not complete")
    if set(state.get("completed_targets", [])) != set(targets):
        raise RuntimeError("treasury completed targets differ")
    if state.get("failed_targets") or state.get("progress"):
        raise RuntimeError("treasury state contains failed or pending targets")
    if int(state.get("market_calls", -1)) != expected_files:
        raise RuntimeError("treasury state call count differs from landing expectation")

    landing_root = (
        project_root / "data/landing/tossinvest" / TREASURY_YIELD_OPERATION
    )
    frames: list[pd.DataFrame] = []
    file_count = 0
    for target in targets:
        indexed: list[tuple[int, Path]] = []
        for path in (landing_root / target).glob("*.json"):
            match = _LANDING_PAGE.search(path.name)
            if match is None:
                raise RuntimeError("treasury landing filename is invalid")
            indexed.append((int(match.group("page")), path))
        indexed.sort()
        if [value for value, _ in indexed] != list(range(len(indexed))):
            raise RuntimeError("treasury landing page sequence is not contiguous")
        previous_cursor: str | None = None
        seen_cursors: set[str] = set()
        for position, path in indexed:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            _assert_no_secrets(envelope)
            if (
                envelope.get("source") != "tossinvest_open_api"
                or envelope.get("operation") != TREASURY_YIELD_OPERATION
                or envelope.get("target") != target
                or envelope.get("cursor_parameter") != "before"
                or envelope.get("cursor") != previous_cursor
            ):
                raise RuntimeError("treasury landing envelope metadata differs")
            collected_at = datetime.fromisoformat(str(envelope.get("collected_at")))
            if collected_at.tzinfo is None:
                raise RuntimeError("treasury landing collected_at is not timezone-aware")
            raw_response = envelope.get("raw_response")
            if not isinstance(raw_response, dict):
                raise RuntimeError("treasury landing raw_response is invalid")
            rows, next_cursor = _extract(raw_response, "candles", "nextBefore")
            is_last = position == len(indexed) - 1
            if is_last != (next_cursor is None):
                raise RuntimeError("treasury landing terminal cursor differs")
            if next_cursor is not None:
                if next_cursor == previous_cursor or next_cursor in seen_cursors:
                    raise RuntimeError("treasury landing cursor did not advance")
                seen_cursors.add(next_cursor)
            if rows:
                frames.append(
                    normalize_treasury_yield(
                        rows, instrument=target, collected_at=collected_at
                    )
                )
            previous_cursor = next_cursor
            file_count += 1
    if file_count != expected_files:
        raise RuntimeError("treasury landing file count differs")
    if not frames:
        raise RuntimeError("treasury landing contains no rows")

    result = pd.concat(frames, ignore_index=True)
    result = _normalize_partition_dates(result)
    result = result[list(KR_TREASURY_YIELD_DAILY.column_names)]
    result = result.sort_values(
        list(KR_TREASURY_YIELD_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    if len(result) != expected_rows:
        raise RuntimeError("treasury landing row count differs")
    validate_toss_historical(result, KR_TREASURY_YIELD_DAILY)
    return result


def _read_treasury_without_semantic_validation(root: Path) -> pd.DataFrame:
    paths = sorted(root.glob("instrument=*/year=*/data.parquet"))
    if not paths:
        raise FileNotFoundError(root)
    result = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    result = _normalize_partition_dates(result)
    return result[list(KR_TREASURY_YIELD_DAILY.column_names)].sort_values(
        list(KR_TREASURY_YIELD_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)


def rebuild_treasury_from_landing_atomic(
    project_root: Path,
    *,
    instruments: Iterable[str] = TREASURY_INSTRUMENTS,
    expected_files: int = 60,
    expected_rows: int = 11_162,
    expected_partitions: int = 48,
) -> Path:
    incoming = build_treasury_from_landing(
        project_root,
        instruments=instruments,
        expected_files=expected_files,
        expected_rows=expected_rows,
    )
    live_root = project_root / "data/normalized" / KR_TREASURY_YIELD_DAILY.name
    existing = _read_treasury_without_semantic_validation(live_root)
    comparable = [
        name
        for name in KR_TREASURY_YIELD_DAILY.column_names
        if name != "availability_date"
    ]
    pd.testing.assert_frame_equal(
        existing[comparable], incoming[comparable], check_dtype=False
    )
    if not existing["availability_date"].eq(existing["date"]).all():
        raise RuntimeError("existing treasury availability is not the known guessed date")
    if incoming["availability_date"].notna().any():
        raise RuntimeError("rebuilt treasury availability must be unknown")

    parent = live_root.parent
    staging_root = Path(
        tempfile.mkdtemp(prefix=".kr_treasury_yield_daily.rebuild.", dir=parent)
    )
    backup_root = live_root.with_name(live_root.name + ".availability_backup")
    if backup_root.exists():
        shutil.rmtree(staging_root)
        raise FileExistsError(backup_root)
    promoted = False
    try:
        write_dataset_atomic(
            incoming,
            staging_root,
            KR_TREASURY_YIELD_DAILY,
            lambda frame: validate_toss_historical(
                frame, KR_TREASURY_YIELD_DAILY
            ),
        )
        if len(list(staging_root.glob("instrument=*/year=*/data.parquet"))) != expected_partitions:
            raise RuntimeError("rebuilt treasury partition count differs")
        staged = _read_treasury_without_semantic_validation(staging_root)
        validate_toss_historical(staged, KR_TREASURY_YIELD_DAILY)
        pd.testing.assert_frame_equal(staged, incoming, check_dtype=False)
        live_root.replace(backup_root)
        try:
            staging_root.replace(live_root)
            promoted = True
        except Exception:
            backup_root.replace(live_root)
            raise
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root)
    if not promoted:
        raise RuntimeError("treasury rebuild was not promoted")
    return backup_root


def merge_dataset_atomic(incoming: pd.DataFrame, *, root: Path, contract) -> int:
    if incoming.empty:
        return 0
    incoming = _normalize_partition_dates(incoming)
    incoming = incoming[list(contract.column_names)]
    frames = []
    working = incoming.copy()
    for column in contract.partition_by:
        if column == "year":
            working["_year"] = pd.to_datetime(working["date"], errors="raise").dt.year
    group_columns = ["_year" if name == "year" else name for name in contract.partition_by]
    for key, partition in working.groupby(group_columns, sort=True):
        values = key if isinstance(key, tuple) else (key,)
        target = root
        for name, value in zip(contract.partition_by, values):
            target /= f"{name}={value}"
        existing_path = target / "data.parquet"
        part = partition.drop(columns="_year", errors="ignore")
        if existing_path.exists():
            existing = _normalize_partition_dates(pd.read_parquet(existing_path))
            part = pd.concat([existing, part], ignore_index=True)
        part = part.drop_duplicates(list(contract.primary_key), keep="last")
        frames.append(part[list(contract.column_names)])
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    validate_toss_historical(combined, contract)
    write_dataset_atomic(combined, root, contract,
                         lambda frame: validate_toss_historical(frame, contract))
    return len(incoming)


def _landing_rows(project_root: Path, progress: dict[str, Any], row_key: str,
                  normalize: Callable[[list[dict[str, Any]], datetime], pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for relative in progress.get("landing_files", []):
        envelope = json.loads((project_root / relative).read_text(encoding="utf-8"))
        rows, _ = _extract(envelope["raw_response"], row_key, progress["cursor_key"])
        collected_at = datetime.fromisoformat(envelope["collected_at"])
        if rows:
            frames.append(normalize(rows, collected_at))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _flush_ready(project_root: Path, state: TossHistoricalState, *, contract, row_key: str,
                 normalize_for_target: Callable[[str, list[dict[str, Any]], datetime], pd.DataFrame]) -> int:
    ready = [target for target, item in state.progress.items() if item.get("status") == "ready"]
    if not ready:
        return 0
    frames = []
    for target in ready:
        progress = state.progress[target]
        frame = _landing_rows(
            project_root, progress, row_key,
            lambda rows, observed, target=target: normalize_for_target(target, rows, observed),
        )
        if not frame.empty:
            frames.append(frame)
    written = 0
    if frames:
        incoming = pd.concat(frames, ignore_index=True)
        incoming = incoming.drop_duplicates(list(contract.primary_key), keep="last")
        incoming = incoming.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        written = merge_dataset_atomic(
            incoming,
            root=project_root / "data/normalized" / contract.name,
            contract=contract,
        )
    state.mark_completed(ready)
    return written


def backfill_toss_targets(
    project_root: Path,
    *,
    client: TossInvestClient,
    contract,
    targets: Iterable[str],
    endpoint_for_target: Callable[[str], str],
    base_params: dict[str, Any],
    cursor_parameter: str,
    cursor_key: str,
    row_key: str,
    operation: str,
    normalize_for_target: Callable[[str, list[dict[str, Any]], datetime], pd.DataFrame],
    batch_size: int = 25,
) -> TossHistoricalResult:
    state = TossHistoricalState.load(
        project_root / "data/state" / f"toss_{contract.name}.json", contract.name
    )
    initial_market_calls = client.market_request_count
    initial_token_calls = client.token_request_count
    written = _flush_ready(project_root, state, contract=contract, row_key=row_key,
                           normalize_for_target=normalize_for_target)
    skip = state.completed | state.valid_empty
    pending = [str(target) for target in targets if str(target) not in skip]
    state.payload["status"] = "running"
    state.save()
    try:
        for target in pending:
            progress = state.progress.setdefault(target, {
                "status": "fetching",
                "next_cursor": None,
                "page_index": 0,
                "landing_files": [],
                "seen_cursors": [],
                "cursor_key": cursor_key,
            })
            if progress.get("status") == "ready":
                continue
            while True:
                cursor = progress.get("next_cursor")
                params = dict(base_params)
                if cursor is not None:
                    params[cursor_parameter] = cursor
                response = _request(client, state, endpoint_for_target(target), params)
                rows, next_cursor = _extract(response.payload, row_key, cursor_key)
                observed = datetime.now(timezone.utc)
                page_index = int(progress.get("page_index", 0))
                stamp = observed.strftime("%Y%m%dT%H%M%S%fZ")
                relative = Path("data/landing/tossinvest") / operation / target / f"{stamp}_p{page_index:05d}.json"
                envelope = {
                    "collected_at": observed.isoformat(),
                    "source": "tossinvest_open_api",
                    "operation": operation,
                    "target": target,
                    "cursor_parameter": cursor_parameter,
                    "cursor": cursor,
                    "rate_limit": _rate_payload(response.rate_limit),
                    "raw_response": response.payload,
                }
                _atomic_json(project_root / relative, envelope)
                progress["landing_files"].append(relative.as_posix())
                progress["page_index"] = page_index + 1
                progress["next_cursor"] = next_cursor
                state.payload["last_rate_limit"] = _rate_payload(response.rate_limit)
                if next_cursor is not None:
                    seen = set(progress.get("seen_cursors", []))
                    if next_cursor == cursor or next_cursor in seen:
                        raise TossInvestResponseError("Toss cursor did not advance")
                    progress.setdefault("seen_cursors", []).append(next_cursor)
                state.save()
                _pace(response.rate_limit)
                if next_cursor is None:
                    if not progress["landing_files"] or (
                        page_index == 0 and not rows
                    ):
                        state.mark_empty(target)
                    else:
                        progress["status"] = "ready"
                        state.save()
                    break
            ready_count = sum(item.get("status") == "ready" for item in state.progress.values())
            if ready_count >= batch_size:
                written += _flush_ready(project_root, state, contract=contract,
                                        row_key=row_key,
                                        normalize_for_target=normalize_for_target)
        written += _flush_ready(project_root, state, contract=contract, row_key=row_key,
                                normalize_for_target=normalize_for_target)
        state.payload["status"] = "complete"
    except TossInvestRateLimitError:
        written += _flush_ready(project_root, state, contract=contract, row_key=row_key,
                                normalize_for_target=normalize_for_target)
        state.payload["status"] = "stopped_429"
    except (TossInvestAuthenticationError, TossInvestResponseError,
            TossRateLimitHeaderError, TossInvestHTTPError, TossInvestTimeoutError) as error:
        state.mark_failed(target, error)
    finally:
        state.payload["token_calls"] = int(state.payload.get("token_calls", 0)) + (
            client.token_request_count - initial_token_calls
        )
        state.payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        state.save()
    return TossHistoricalResult(
        dataset=contract.name,
        status=str(state.payload["status"]),
        token_calls=client.token_request_count - initial_token_calls,
        market_calls=client.market_request_count - initial_market_calls,
        completed_targets=len(state.completed),
        valid_empty_targets=len(state.valid_empty),
        failed_targets=len(state.payload.get("failed_targets", {})),
        rows=written,
    )
