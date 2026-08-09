from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
import re
import time
from typing import Callable

import pandas as pd
from dotenv import load_dotenv

from stock_data.contracts.kr_index_daily import KR_INDEX_DAILY
from stock_data.storage.atomic_parquet import read_kr_index_daily, write_kr_index_daily_atomic
from stock_data.validation.kr_index_daily import validate_kr_index_daily
from stock_data.providers.pykrx.safety import PykrxRequestPolicy, require_manual_live_access


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "normalized" / "kr_index_daily"
OVERLAP_DAYS = 10
INDEX_TICKERS = {"KOSPI": "1001", "KOSDAQ": "2001"}
PYKRX_COLUMN_MAP = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "trading_value",
    "상장시가총액": "market_cap",
}


class PykrxCollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CollectionResult:
    mode: str
    requested_start: str
    requested_end: str
    fetched_rows: int
    total_rows: int
    replaced_rows: int
    output_path: Path


def _sanitize(value: object) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)\b(KRX_ID|KRX_PW|API_KEY|PASSWORD|TOKEN|SECRET)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]",
        text,
    )
    return re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)


def _stock_module():
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            load_dotenv(PROJECT_ROOT / ".env", override=False)
            from pykrx import stock
        return stock
    except Exception as error:
        raise PykrxCollectionError(
            f"pykrx initialization failed: {type(error).__name__}: {_sanitize(error)}"
        ) from None


def _parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(f"date must use YYYY-MM-DD: {value}") from error


def _normalize_response(dataframe: pd.DataFrame, market: str) -> pd.DataFrame:
    if dataframe.empty:
        return pd.DataFrame(columns=KR_INDEX_DAILY.column_names)
    normalized = dataframe.reset_index().rename(columns=PYKRX_COLUMN_MAP)
    first_column = normalized.columns[0]
    normalized = normalized.rename(columns={first_column: "date"})
    missing = set(PYKRX_COLUMN_MAP.values()) - set(normalized.columns)
    if missing:
        raise PykrxCollectionError(f"pykrx response is missing columns: {sorted(missing)}")
    normalized.insert(1, "symbol", market)
    normalized.insert(2, "market", market)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.strftime("%Y-%m-%d")
    normalized["source"] = "pykrx"
    normalized = normalized[list(KR_INDEX_DAILY.column_names)]
    normalized = normalized.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    validate_kr_index_daily(normalized)
    return normalized


def fetch_indices(
    start: date,
    end: date,
    *,
    stock_module=None,
    policy: PykrxRequestPolicy | None = None,
    manual: bool = False,
) -> pd.DataFrame:
    if stock_module is None:
        require_manual_live_access(manual=manual, requested_days=(end - start).days + 1)
    stock = stock_module or _stock_module()
    request_policy = policy or PykrxRequestPolicy()
    frames: list[pd.DataFrame] = []
    for market, ticker in INDEX_TICKERS.items():
        last_error = None
        for attempt in range(3):
            try:
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    request_policy.before_request()
                    response = stock.get_index_ohlcv(
                        start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
                    )
                break
            except Exception as error:
                last_error = error
                request_policy.record_failure()
        else:
            raise PykrxCollectionError(
                f"{market} collection failed: {type(last_error).__name__}: {_sanitize(last_error)}"
            ) from None
        normalized = _normalize_response(response, market)
        if normalized.empty:
            raise PykrxCollectionError(f"{market} returned no rows")
        frames.append(normalized)
        request_policy.record_success()
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    validate_kr_index_daily(combined)
    return combined


def collect_kr_index_daily(
    start_date: str | date,
    end_date: str | date,
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    fetcher: Callable[[date, date], pd.DataFrame] = fetch_indices,
    incremental: bool = False,
    overlap_days: int = OVERLAP_DAYS,
) -> CollectionResult:
    requested_start = _parse_date(start_date)
    requested_end = _parse_date(end_date)
    if requested_start > requested_end:
        raise ValueError("start_date must not be after end_date")
    if overlap_days < 0:
        raise ValueError("overlap_days must be nonnegative")

    existing: pd.DataFrame | None = None
    fetch_start = requested_start
    mode = "full"
    if incremental and output_path.exists():
        existing = read_kr_index_daily(output_path)
        latest = pd.to_datetime(existing["date"], errors="raise").max().date()
        fetch_start = max(requested_start, latest - timedelta(days=overlap_days))
        mode = "incremental"
    incoming = fetcher(fetch_start, requested_end)
    validate_kr_index_daily(incoming)
    if incoming["date"].min() < fetch_start.isoformat() or incoming["date"].max() > requested_end.isoformat():
        raise PykrxCollectionError("provider returned rows outside the requested range")

    replaced_rows = 0
    if existing is not None:
        incoming_keys = set(map(tuple, incoming[["date", "symbol"]].to_numpy()))
        existing_keys = existing[["date", "symbol"]].apply(tuple, axis=1)
        replaced_rows = int(existing_keys.isin(incoming_keys).sum())
        combined = pd.concat([existing.loc[~existing_keys.isin(incoming_keys)], incoming], ignore_index=True)
    else:
        combined = incoming.copy()
    combined = combined.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    validate_kr_index_daily(combined)
    write_kr_index_daily_atomic(combined, output_path)
    return CollectionResult(
        mode=mode,
        requested_start=fetch_start.isoformat(),
        requested_end=requested_end.isoformat(),
        fetched_rows=len(incoming),
        total_rows=len(combined),
        replaced_rows=replaced_rows,
        output_path=output_path,
    )
