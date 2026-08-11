from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import time
from typing import Callable, Iterable, Mapping

import pandas as pd
import requests

from stock_data.contracts.data_v1 import (
    KR_STOCK_LENDING_DAILY,
    KR_STOCK_LENDING_MARKET_DAILY,
    KR_STOCK_LENDING_PARTICIPANT_DAILY,
)
from stock_data.pipelines.backfill_state import BackfillState
from stock_data.providers.data_go_kr.client import (
    DataGoKrClient,
    service_key_from_environment,
    write_landing_pages_atomic,
)
from stock_data.providers.data_go_kr.data_v1 import (
    ENDPOINTS,
    normalize_stock_lending,
    normalize_stock_lending_market,
    normalize_stock_lending_participant,
)
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1


@dataclass(frozen=True)
class StockLendingSpec:
    key: str
    endpoint: str
    contract: object
    normalizer: Callable[[Iterable[Mapping[str, object]]], pd.DataFrame]


STOCK_LENDING_SPECS = {
    "detail": StockLendingSpec(
        "detail", ENDPOINTS["stock_lending"], KR_STOCK_LENDING_DAILY,
        normalize_stock_lending,
    ),
    "market": StockLendingSpec(
        "market", ENDPOINTS["stock_lending_market"], KR_STOCK_LENDING_MARKET_DAILY,
        normalize_stock_lending_market,
    ),
    "participant": StockLendingSpec(
        "participant", ENDPOINTS["stock_lending_participant"],
        KR_STOCK_LENDING_PARTICIPANT_DAILY, normalize_stock_lending_participant,
    ),
}


@dataclass(frozen=True)
class StockLendingBackfillResult:
    dataset: str
    status: str
    api_calls: int
    landing_pages: int
    source_rows: int | None
    normalized_rows: int
    trading_dates: int
    minimum_date: str | None
    maximum_date: str | None
    rate_limit_remaining: str | None


class StockLendingBackfillLocked(RuntimeError):
    pass


@contextmanager
def stock_lending_run_lock(project_root: Path):
    """Prevent wrapper timeouts from starting an overlapping resume process."""
    path = project_root / "data/state/fsc_stock_lending_backfill.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise StockLendingBackfillLocked(
            "another FSC stock-lending backfill process holds the run lock"
        ) from None
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)


class _RateLimitedSession:
    def __init__(
        self, *, backend, max_calls: int, min_interval_seconds: float,
        sleep_fn: Callable[[float], None],
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.max_calls = max_calls
        self.min_interval_seconds = min_interval_seconds
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.request_count = 0
        self.last_status: int | None = None
        self.last_headers: dict[str, str] = {}
        self._last_request_at: float | None = None

    def get(self, *args, **kwargs):
        if self.request_count >= self.max_calls:
            raise RuntimeError("stock lending API call cap reached")
        if self._last_request_at is not None:
            wait = self.min_interval_seconds - (self.monotonic_fn() - self._last_request_at)
            if wait > 0:
                self.sleep_fn(wait)
        self.request_count += 1
        self._last_request_at = self.monotonic_fn()
        response = self.backend.get(*args, **kwargs)
        self.last_status = response.status_code
        self.last_headers = {
            name: response.headers[name]
            for name in (
                "X-RateLimit-Limit", "X-RateLimit-Remaining",
                "X-RateLimit-Reset", "Retry-After",
            )
            if name in response.headers
        }
        return response


def _history_state(project_root: Path, spec: StockLendingSpec) -> BackfillState:
    dataset = f"{spec.contract.name}_historical"
    return BackfillState.load(project_root / "data/state" / f"{dataset}.json", dataset)


def _run_marker(start_date: str, end_date: str | None) -> str:
    return f"range:{start_date}:{end_date or 'open'}"


def _page_marker(run_marker: str, page_no: int) -> str:
    return f"{run_marker}:page:{page_no:05d}"


def _landing_path(
    project_root: Path, spec: StockLendingSpec, start_date: str,
    end_date: str | None, page_no: int,
) -> Path:
    range_name = f"{start_date}_{end_date or 'open'}"
    return (
        project_root / "data/landing/data_go_kr" / spec.contract.name
        / "historical" / range_name / f"page={page_no:05d}.json"
    )


def _read_page(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise RuntimeError("stock lending landing page is invalid")
    return payload[0]


def _page_metadata(payload: Mapping[str, object]) -> tuple[int, int, list[Mapping[str, object]]]:
    try:
        body = payload["response"]["body"]  # type: ignore[index]
        total = int(body["totalCount"])
        page_no = int(body["pageNo"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("stock lending landing pagination metadata is invalid") from None
    container = body.get("items") or {}
    item = container.get("item", []) if isinstance(container, dict) else []
    rows = item if isinstance(item, list) else [item]
    if total < 0 or not all(isinstance(row, dict) for row in rows):
        raise RuntimeError("stock lending landing rows are invalid")
    return total, page_no, rows


def _completed_source_rows(
    project_root: Path,
    spec: StockLendingSpec,
    start_date: str,
    end_date: str | None,
    state: BackfillState,
    marker: str,
) -> int | None:
    terminal = state.completed_partitions | state.valid_empty_partitions
    prefix = marker + ":page:"
    try:
        page_numbers = sorted(
            int(value.removeprefix(prefix))
            for value in terminal
            if value.startswith(prefix)
        )
    except ValueError:
        return None
    if not page_numbers or page_numbers != list(range(1, len(page_numbers) + 1)):
        return None

    expected_total: int | None = None
    expected_pages: int | None = None
    expected_page_size: int | None = None
    landed_rows = 0
    try:
        for page_number in page_numbers:
            path = _landing_path(
                project_root, spec, start_date, end_date, page_number
            )
            if not path.exists():
                return None
            payload = _read_page(path)
            total, returned_page, rows = _page_metadata(payload)
            body = payload["response"]["body"]  # type: ignore[index]
            page_size = int(body["numOfRows"])
            if page_size < 1 or returned_page != page_number:
                return None
            if expected_total is None:
                expected_total = total
                expected_page_size = page_size
                expected_pages = max(1, math.ceil(total / page_size))
            elif total != expected_total or page_size != expected_page_size:
                return None
            landed_rows += len(rows)
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        return None
    if (
        expected_total is None
        or expected_pages != len(page_numbers)
        or landed_rows != expected_total
    ):
        return None
    return expected_total


def _validate_date(value: str) -> None:
    if len(value) != 8 or not value.isdigit():
        raise ValueError("dates must be YYYYMMDD")
    pd.to_datetime(value, format="%Y%m%d", errors="raise")


def _filters(start_date: str, end_date: str | None) -> dict[str, str]:
    result = {"beginBasDt": start_date}
    if end_date is not None:
        result["endBasDt"] = end_date
    return result


def _existing_year(project_root: Path, spec: StockLendingSpec, year: str) -> pd.DataFrame:
    path = (
        project_root / "data/normalized" / spec.contract.name
        / f"year={year}" / "data.parquet"
    )
    if not path.exists():
        return pd.DataFrame(columns=spec.contract.column_names)
    frame = pd.read_parquet(path)
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d")
    frame = frame[list(spec.contract.column_names)].sort_values(
        list(spec.contract.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_data_v1(frame, spec.contract)
    return frame


def _dataset_summary(project_root: Path, spec: StockLendingSpec) -> tuple[int, int, str | None, str | None]:
    root = project_root / "data/normalized" / spec.contract.name
    rows = 0
    dates: set[str] = set()
    minimum: str | None = None
    maximum: str | None = None
    for path in sorted(root.glob("year=*/data.parquet")):
        frame = pd.read_parquet(path)
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d")
        frame = frame[list(spec.contract.column_names)].sort_values(
            list(spec.contract.sort_key), kind="stable"
        ).reset_index(drop=True)
        validate_data_v1(frame, spec.contract)
        rows += len(frame)
        dates.update(frame["date"])
        if not frame.empty:
            current_minimum = str(frame["date"].min())
            current_maximum = str(frame["date"].max())
            minimum = current_minimum if minimum is None else min(minimum, current_minimum)
            maximum = current_maximum if maximum is None else max(maximum, current_maximum)
    return rows, len(dates), minimum, maximum


def _write_normalized(
    *, project_root: Path, spec: StockLendingSpec,
    landing_paths: tuple[Path, ...], start_date: str, end_date: str | None,
) -> tuple[int, int, str, str, set[str]]:
    years: set[str] = set()
    source_dates: set[str] = set()
    source_rows = 0
    for path in landing_paths:
        _, _, rows = _page_metadata(_read_page(path))
        source_rows += len(rows)
        for row in rows:
            value = str(row.get("basDt", ""))
            if len(value) != 8 or not value.isdigit():
                raise RuntimeError("stock lending source date is invalid")
            if value < start_date or (end_date is not None and value >= end_date):
                raise RuntimeError("stock lending response is outside the requested range")
            years.add(value[:4])
            source_dates.add(value)
    if not source_rows:
        raise RuntimeError("stock lending source unexpectedly returned no rows")

    root = project_root / "data/normalized" / spec.contract.name
    for year in sorted(years):
        raw_rows: list[Mapping[str, object]] = []
        for path in landing_paths:
            _, _, rows = _page_metadata(_read_page(path))
            raw_rows.extend(row for row in rows if str(row.get("basDt", "")).startswith(year))
        incoming = spec.normalizer(raw_rows)
        existing = _existing_year(project_root, spec, year)
        combined = pd.concat([existing, incoming], ignore_index=True)
        combined = combined.drop_duplicates(list(spec.contract.primary_key), keep="last")
        combined = combined.sort_values(list(spec.contract.sort_key), kind="stable").reset_index(drop=True)
        validate_data_v1(combined, spec.contract, allow_empty=False)
        write_dataset_atomic(
            combined, root, spec.contract,
            lambda frame: validate_data_v1(frame, spec.contract),
        )
        restored = _existing_year(project_root, spec, year)
        if len(restored) != len(combined):
            raise RuntimeError("stock lending atomic read-back row count differs")

    normalized_rows, trading_dates, minimum, maximum = _dataset_summary(project_root, spec)
    if minimum is None or maximum is None:
        raise RuntimeError("stock lending normalized dataset is empty")
    return normalized_rows, trading_dates, minimum, maximum, source_dates


def collect_stock_lending_history(
    *, project_root: Path, spec: StockLendingSpec, start_date: str = "20210401",
    end_date: str | None = None, max_calls: int = 1000, page_size: int = 9999,
    min_interval_seconds: float = 0.5, resume: bool = True,
    service_key: str | None = None, session=None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> StockLendingBackfillResult:
    _validate_date(start_date)
    if end_date is not None:
        _validate_date(end_date)
        if end_date <= start_date:
            raise ValueError("end_date must be greater than start_date")
    if max_calls < 1 or page_size < 1 or page_size > 9999:
        raise ValueError("invalid call or page budget")
    if min_interval_seconds < 0:
        raise ValueError("min_interval_seconds must be nonnegative")

    state = _history_state(project_root, spec)
    marker = _run_marker(start_date, end_date)
    if resume and marker not in state.pending((marker,)):
        rows, dates, minimum, maximum = _dataset_summary(project_root, spec)
        source_rows = _completed_source_rows(
            project_root, spec, start_date, end_date, state, marker
        )
        return StockLendingBackfillResult(
            spec.contract.name,
            "VALID_EMPTY" if marker in state.valid_empty_partitions else "COMPLETE",
            0, 0, source_rows, rows, dates,
            minimum, maximum, None,
        )

    rate_session = _RateLimitedSession(
        # Keep one TCP/TLS connection for hundreds of sequential pages. This
        # changes no request semantics and materially reduces portal load.
        backend=session or requests.Session(), max_calls=max_calls,
        min_interval_seconds=min_interval_seconds, sleep_fn=sleep_fn,
    )
    client = DataGoKrClient(
        endpoint=spec.endpoint,
        service_key=service_key or service_key_from_environment(project_root),
        session=rate_session,
        max_attempts=2,
        backoff_seconds=1.0,
        sleep_fn=sleep_fn,
    )
    expected_total: int | None = None
    expected_pages: int | None = None
    landing_paths: list[Path] = []
    landed_rows = 0
    page_no = 1
    while expected_pages is None or page_no <= expected_pages:
        page_marker = _page_marker(marker, page_no)
        landing_path = _landing_path(project_root, spec, start_date, end_date, page_no)
        try:
            if landing_path.exists() and resume:
                payload = _read_page(landing_path)
                known_pages = (state.staged_partitions or set()) | state.completed_partitions
                if page_marker not in known_pages:
                    state.mark_staged(page_marker)
            else:
                if rate_session.request_count >= max_calls:
                    rows, dates, minimum, maximum = _dataset_summary(project_root, spec)
                    return StockLendingBackfillResult(
                        spec.contract.name, "PARTIAL", rate_session.request_count,
                        len(landing_paths), landed_rows, rows, dates, minimum, maximum,
                        rate_session.last_headers.get("X-RateLimit-Remaining"),
                    )
                page = client.fetch_page(
                    filters=_filters(start_date, end_date),
                    num_of_rows=page_size,
                    page_no=page_no,
                )
                payload = page.payload
                write_landing_pages_atomic((payload,), landing_path)
                state.mark_staged(page_marker)
            total, returned_page, rows = _page_metadata(payload)
            if returned_page != page_no:
                raise RuntimeError("stock lending source returned a different page")
            if expected_total is None:
                expected_total = total
                expected_pages = max(1, math.ceil(total / page_size))
            elif total != expected_total:
                raise RuntimeError("stock lending totalCount changed during pagination")
            landed_rows += len(rows)
            landing_paths.append(landing_path)
            page_no += 1
        except Exception as error:
            state.mark_failed(page_marker, type(error).__name__)
            raise

    if landed_rows != expected_total:
        raise RuntimeError("stock lending landing rows differ from totalCount")
    if expected_total == 0:
        state.mark_valid_empty_many([
            marker,
            *(
                _page_marker(marker, value)
                for value in range(1, len(landing_paths) + 1)
            ),
        ])
        return StockLendingBackfillResult(
            spec.contract.name, "VALID_EMPTY", rate_session.request_count,
            len(landing_paths), 0, 0, 0, None, None,
            rate_session.last_headers.get("X-RateLimit-Remaining"),
        )
    normalized_rows, trading_dates, minimum, maximum, source_dates = _write_normalized(
        project_root=project_root, spec=spec, landing_paths=tuple(landing_paths),
        start_date=start_date, end_date=end_date,
    )
    operational_state = BackfillState.load(
        project_root / "data/state" / f"{spec.contract.name}.json", spec.contract.name
    )
    operational_state.mark_completed_many(source_dates)
    state.mark_completed_many(_page_marker(marker, value) for value in range(1, len(landing_paths) + 1))
    state.mark_completed(marker)
    return StockLendingBackfillResult(
        spec.contract.name, "COMPLETE", rate_session.request_count,
        len(landing_paths), expected_total, normalized_rows, trading_dates,
        minimum, maximum, rate_session.last_headers.get("X-RateLimit-Remaining"),
    )
