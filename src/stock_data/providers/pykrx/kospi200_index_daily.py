from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from pathlib import Path
from typing import Callable

import pandas as pd

from stock_data.contracts.kospi200_index_daily import KR_KOSPI200_INDEX_DAILY
from stock_data.providers.pykrx.kr_index_daily import _stock_module
from stock_data.providers.pykrx.safety import PykrxRequestPolicy, require_manual_live_access
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.validation.kospi200_index_daily import validate_kospi200_index_daily


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "normalized" / "kr_kospi200_index_daily"
TICKER = "1028"
SYMBOL = "KOSPI200"
SOURCE_OPERATION = "get_index_ohlcv"
DATE_SEMANTICS = "KRX_TRADING_DATE_DAILY_FINAL"
OHLC_VALID = "VALID"
OHLC_SOURCE_ANOMALY = "SOURCE_ANOMALY_OPEN_HIGH_ZERO_CLOSE_BELOW_LOW"
PYKRX_COLUMN_MAP = {
    "시가": "open", "고가": "high", "저가": "low", "종가": "close",
    "거래량": "volume", "거래대금": "trading_value", "상장시가총액": "market_cap",
}


class KOSPI200IndexCollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CollectionResult:
    requested_start: str
    requested_end: str
    rows: int
    coverage_start: str
    coverage_end: str
    business_calls: int
    retry_count: int
    output_path: Path


def _parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(f"date must use YYYY-MM-DD: {value}") from error


def normalize_response(response: pd.DataFrame) -> pd.DataFrame:
    if response.empty:
        raise KOSPI200IndexCollectionError("KOSPI200 returned no rows")
    normalized = response.reset_index().rename(columns=PYKRX_COLUMN_MAP)
    normalized = normalized.rename(columns={normalized.columns[0]: "date"})
    missing = set(PYKRX_COLUMN_MAP.values()) - set(normalized.columns)
    if missing:
        raise KOSPI200IndexCollectionError(f"response missing columns: {sorted(missing)}")
    normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.strftime("%Y-%m-%d")
    normalized.insert(1, "symbol", SYMBOL)
    normalized.insert(2, "ticker", TICKER)
    anomaly = (
        normalized["open"].eq(0) & normalized["high"].eq(0)
        & normalized["low"].gt(0) & normalized["close"].lt(normalized["low"])
    )
    normalized["ohlc_status"] = OHLC_VALID
    normalized.loc[anomaly, "ohlc_status"] = OHLC_SOURCE_ANOMALY
    normalized["source"] = "pykrx"
    normalized["source_operation"] = SOURCE_OPERATION
    normalized["date_semantics"] = DATE_SEMANTICS
    normalized = normalized[list(KR_KOSPI200_INDEX_DAILY.column_names)].sort_values("date").reset_index(drop=True)
    validate_kospi200_index_daily(normalized)
    return normalized


def fetch_kospi200_index(
    start: date,
    end: date,
    *,
    stock_module=None,
    policy: PykrxRequestPolicy | None = None,
    manual: bool = False,
    authorized_historical: bool = False,
) -> pd.DataFrame:
    """One business call, retry zero."""
    if stock_module is None and not authorized_historical:
        require_manual_live_access(manual=manual, requested_days=(end - start).days + 1)
    stock = stock_module or _stock_module()
    request_policy = policy or PykrxRequestPolicy()
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            request_policy.before_request()
            response = stock.get_index_ohlcv(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), TICKER)
    except Exception as error:
        request_policy.record_failure()
        raise KOSPI200IndexCollectionError(
            f"KOSPI200 retry-zero collection failed: {type(error).__name__}: {error}"
        ) from None
    request_policy.record_success()
    return normalize_response(response)


def collect_kospi200_index_daily(
    start_date: str | date,
    end_date: str | date,
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    fetcher: Callable[[date, date], pd.DataFrame] = fetch_kospi200_index,
) -> CollectionResult:
    start, end = _parse_date(start_date), _parse_date(end_date)
    if start > end:
        raise ValueError("start_date must not be after end_date")
    frame = fetcher(start, end)
    if frame["date"].min() < start.isoformat() or frame["date"].max() > end.isoformat():
        raise KOSPI200IndexCollectionError("provider returned rows outside requested range")
    write_dataset_atomic(frame, output_path, KR_KOSPI200_INDEX_DAILY, validate_kospi200_index_daily)
    return CollectionResult(
        requested_start=start.isoformat(), requested_end=end.isoformat(), rows=len(frame),
        coverage_start=frame["date"].min(), coverage_end=frame["date"].max(),
        business_calls=1, retry_count=0, output_path=output_path,
    )
