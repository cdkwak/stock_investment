from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Callable, Iterable, Mapping

import pandas as pd
import requests

from stock_data.pipelines.backfill_state import BackfillState
from stock_data.providers.data_go_kr.client import (
    DataGoKrClient,
    service_key_from_environment,
    write_landing_pages_atomic,
)
from stock_data.providers.data_go_kr.derivatives import (
    DerivativeProductSpec,
    normalize_derivatives,
    range_request_filters,
    request_filters,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1


@dataclass(frozen=True)
class DerivativeBackfillResult:
    dataset: str
    api_calls: int
    completed_dates: tuple[str, ...]
    valid_empty_dates: tuple[str, ...]
    unresolved_dates: tuple[str, ...]
    rows_written: int
    minimum_date: str | None
    maximum_date: str | None


class _RateLimitedSession:
    def __init__(
        self,
        *,
        max_calls: int,
        min_interval_seconds: float,
        sleep_fn: Callable[[float], None],
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_calls = max_calls
        self.min_interval_seconds = min_interval_seconds
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.request_count = 0
        self._last_request_at: float | None = None

    def get(self, *args, **kwargs):
        if self.request_count >= self.max_calls:
            raise RuntimeError("derivative API call cap reached")
        if self._last_request_at is not None:
            wait = self.min_interval_seconds - (self.monotonic_fn() - self._last_request_at)
            if wait > 0:
                self.sleep_fn(wait)
        self.request_count += 1
        self._last_request_at = self.monotonic_fn()
        return requests.get(*args, **kwargs)


def _landing_path(project_root: Path, spec: DerivativeProductSpec, base_date: str) -> Path:
    return (
        project_root
        / "data/landing/data_go_kr"
        / spec.contract.name
        / f"{base_date}.json"
    )


def _items_from_pages(pages: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    for payload in pages:
        try:
            body = payload["response"]["body"]  # type: ignore[index]
        except (KeyError, TypeError):
            raise RuntimeError("landing response envelope is invalid") from None
        container = body.get("items") or {}
        item = container.get("item", []) if isinstance(container, dict) else []
        page_rows = item if isinstance(item, list) else [item]
        if not all(isinstance(row, dict) for row in page_rows):
            raise RuntimeError("landing response items are invalid")
        rows.extend(page_rows)
    return rows


def _read_staged_landing(path: Path) -> tuple[Mapping[str, object], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(page, dict) for page in payload):
        raise RuntimeError("staged landing JSON is invalid")
    return tuple(payload)


def _validator(contract):
    return lambda frame: validate_data_v1(frame, contract)


def collect_derivative_dates(
    *,
    project_root: Path,
    spec: DerivativeProductSpec,
    dates: Iterable[str],
    max_calls: int,
    min_interval_seconds: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> DerivativeBackfillResult:
    if max_calls < 1:
        raise ValueError("max_calls must be positive")
    if min_interval_seconds < 0:
        raise ValueError("min_interval_seconds must be nonnegative")
    requested_dates = tuple(dict.fromkeys(str(value) for value in dates))
    if any(len(value) != 8 or not value.isdigit() for value in requested_dates):
        raise ValueError("dates must be YYYYMMDD")

    contract = spec.contract
    state = BackfillState.load(
        project_root / "data/state" / f"{contract.name}.json", contract.name
    )
    pending = state.pending(requested_dates)
    client = DataGoKrClient(
        endpoint=spec.endpoint,
        service_key=service_key_from_environment(project_root),
        max_attempts=1,
    )
    frames: list[pd.DataFrame] = []
    staged_dates: list[str] = []
    valid_empty: list[str] = []
    api_calls = 0

    for base_date in pending:
        if api_calls >= max_calls:
            break
        landing_path = _landing_path(project_root, spec, base_date)
        try:
            if base_date in (state.staged_partitions or set()) and landing_path.exists():
                pages = _read_staged_landing(landing_path)
                items = _items_from_pages(pages)
            else:
                if api_calls:
                    sleep_fn(min_interval_seconds)
                result = client.fetch_all(
                    filters=request_filters(spec, base_date),
                    num_of_rows=9999,
                    max_pages=1,
                )
                api_calls += len(result.pages)
                if result.total_count == 0:
                    state.mark_valid_empty(base_date)
                    valid_empty.append(base_date)
                    continue
                pages = result.pages
                items = list(result.items)
                write_landing_pages_atomic(pages, landing_path)
                state.mark_staged(base_date)
            frame = normalize_derivatives(items, spec)
            if frame.empty:
                raise RuntimeError("target category returned no promotable outright rows")
            expected_date = pd.to_datetime(base_date, format="%Y%m%d").strftime("%Y-%m-%d")
            if set(frame["date"]) != {expected_date}:
                raise RuntimeError("response date differs from requested date")
            frames.append(frame)
            staged_dates.append(base_date)
        except Exception as error:
            state.mark_failed(base_date, type(error).__name__)
            raise

    root = project_root / "data/normalized" / contract.name
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        if root.exists():
            existing = read_dataset(root, contract, _validator(contract))
            combined = pd.concat([existing, combined], ignore_index=True)
        combined = combined.drop_duplicates(list(contract.primary_key), keep="last")
        combined = combined.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        validate_data_v1(combined, contract, allow_empty=False)
        write_dataset_atomic(combined, root, contract, _validator(contract))
        restored = read_dataset(root, contract, _validator(contract))
        if len(restored) != len(combined):
            raise RuntimeError("stored derivative row count differs after read-back")
        for base_date in staged_dates:
            state.mark_completed(base_date)
    elif root.exists():
        restored = read_dataset(root, contract, _validator(contract))
    else:
        restored = pd.DataFrame(columns=contract.column_names)

    return DerivativeBackfillResult(
        dataset=contract.name,
        api_calls=api_calls,
        completed_dates=tuple(staged_dates),
        valid_empty_dates=tuple(valid_empty),
        unresolved_dates=(),
        rows_written=len(restored),
        minimum_date=None if restored.empty else str(restored["date"].min()),
        maximum_date=None if restored.empty else str(restored["date"].max()),
    )


def _range_chunks(dates: tuple[str, ...], maximum_dates: int) -> tuple[tuple[str, ...], ...]:
    if maximum_dates < 1:
        raise ValueError("maximum_dates must be positive")
    chunks: list[tuple[str, ...]] = []
    current: list[str] = []
    current_month: str | None = None
    for value in dates:
        month = value[:6]
        if current and (month != current_month or len(current) >= maximum_dates):
            chunks.append(tuple(current))
            current = []
        current.append(value)
        current_month = month
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def collect_derivative_ranges(
    *,
    project_root: Path,
    spec: DerivativeProductSpec,
    dates: Iterable[str],
    max_calls: int,
    min_interval_seconds: float = 1.0,
    maximum_dates_per_range: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> DerivativeBackfillResult:
    if max_calls < 1:
        raise ValueError("max_calls must be positive")
    if min_interval_seconds < 0:
        raise ValueError("min_interval_seconds must be nonnegative")
    requested_dates = tuple(dict.fromkeys(str(value) for value in dates))
    if any(len(value) != 8 or not value.isdigit() for value in requested_dates):
        raise ValueError("dates must be YYYYMMDD")
    contract = spec.contract
    state = BackfillState.load(
        project_root / "data/state" / f"{contract.name}.json", contract.name
    )
    root = project_root / "data/normalized" / contract.name
    pending_before_recovery = tuple(state.pending(requested_dates))
    if root.exists() and pending_before_recovery:
        stored = read_dataset(root, contract, _validator(contract))
        stored_dates = set(pd.to_datetime(stored["date"]).dt.strftime("%Y%m%d"))
        recovered = set(pending_before_recovery) & stored_dates
        if recovered:
            state.mark_completed_many(recovered)
    pending = tuple(state.pending(requested_dates))
    maximum_dates = maximum_dates_per_range or (60 if spec.kind == "futures" else 5)
    session = _RateLimitedSession(
        max_calls=max_calls,
        min_interval_seconds=min_interval_seconds,
        sleep_fn=sleep_fn,
    )
    client = DataGoKrClient(
        endpoint=spec.endpoint,
        service_key=service_key_from_environment(project_root),
        session=session,
        max_attempts=1,
    )
    frames: list[pd.DataFrame] = []
    completed_candidates: set[str] = set()
    unresolved_candidates: set[str] = set()

    cursor = 0
    while cursor < len(pending):
        remaining = max_calls - session.request_count
        if remaining < 1:
            break
        limit = maximum_dates
        if spec.kind == "options" and remaining < 3:
            # Recent regular options fit one date per page; at the tail of a
            # capped run, keep the range no larger than the remaining pages.
            limit = min(limit, remaining)
        month = pending[cursor][:6]
        stop = cursor
        while stop < len(pending) and pending[stop][:6] == month and stop - cursor < limit:
            stop += 1
        chunk = pending[cursor:stop]
        cursor = stop
        start_date, end_date = chunk[0], chunk[-1]
        landing_path = (
            project_root
            / "data/landing/data_go_kr"
            / contract.name
            / "ranges"
            / f"{start_date}_{end_date}.json"
        )
        try:
            if all(value in (state.staged_partitions or set()) for value in chunk) and landing_path.exists():
                pages = _read_staged_landing(landing_path)
                items = _items_from_pages(pages)
            else:
                result = client.fetch_all(
                    filters=range_request_filters(spec, start_date, end_date),
                    num_of_rows=9999,
                    max_pages=remaining,
                )
                pages = result.pages
                items = list(result.items)
                write_landing_pages_atomic(pages, landing_path)
                state.mark_staged_many(chunk)
            frame = normalize_derivatives(items, spec)
            expected = set(chunk)
            source_dates = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y%m%d")
            requested_upper_bound = range_request_filters(spec, start_date, end_date)["endBasDt"]
            unexpected = set(source_dates) - expected - {requested_upper_bound}
            if unexpected:
                raise RuntimeError("range response contains an unexpected date")
            frame = frame[source_dates.isin(expected)].reset_index(drop=True)
            returned = set(pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y%m%d"))
            if not frame.empty:
                frames.append(frame)
            completed_candidates.update(returned)
            # Absence inside a successful range is not enough to prove a valid
            # empty date; the portal may have range-boundary/source gaps. Keep
            # it staged for a later exact-date probe.
            unresolved_candidates.update(expected - returned)
        except Exception as error:
            state.mark_failed_many(chunk, type(error).__name__)
            raise

    if frames:
        combined = pd.concat(frames, ignore_index=True)
        if root.exists():
            existing = read_dataset(root, contract, _validator(contract))
            combined = pd.concat([existing, combined], ignore_index=True)
        combined = combined.drop_duplicates(list(contract.primary_key), keep="last")
        combined = combined.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        validate_data_v1(combined, contract, allow_empty=False)
        write_dataset_atomic(combined, root, contract, _validator(contract))
        restored = read_dataset(root, contract, _validator(contract))
        if len(restored) != len(combined):
            raise RuntimeError("stored derivative row count differs after read-back")
    elif root.exists():
        restored = read_dataset(root, contract, _validator(contract))
    else:
        restored = pd.DataFrame(columns=contract.column_names)
    if completed_candidates:
        state.mark_completed_many(completed_candidates)
    return DerivativeBackfillResult(
        dataset=contract.name,
        api_calls=session.request_count,
        completed_dates=tuple(sorted(completed_candidates)),
        valid_empty_dates=(),
        unresolved_dates=tuple(sorted(unresolved_candidates)),
        rows_written=len(restored),
        minimum_date=None if restored.empty else str(restored["date"].min()),
        maximum_date=None if restored.empty else str(restored["date"].max()),
    )


def local_equity_trading_dates(
    project_root: Path, *, start: str, end: str | None = None
) -> tuple[str, ...]:
    # The KOSPI index calendar is the smallest authoritative XKRX session
    # calendar retained by the project.  Avoid scanning every equity market
    # partition (which is slower and can be ACL-restricted on shared hosts).
    index_root = project_root / "data/normalized/kr_index_daily/market=KOSPI"
    equity_root = project_root / "data/normalized/kr_equity_price_daily"
    paths = sorted(index_root.rglob("data.parquet"))
    if not paths:
        paths = sorted(equity_root.rglob("data.parquet"))
    if not paths:
        raise FileNotFoundError("local equity trading calendar is unavailable")
    dates = pd.concat([pd.read_parquet(path, columns=["date"]) for path in paths], ignore_index=True)
    values = pd.to_datetime(dates["date"], errors="raise").dt.strftime("%Y%m%d")
    selected = sorted({value for value in values if value >= start and (end is None or value <= end)})
    return tuple(selected)
