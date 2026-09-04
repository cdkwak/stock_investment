from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from stock_data.contracts.global_etf import (
    GLOBAL_ETF_DAILY_SYMBOLS,
    GLOBAL_ETF_PRICE_DAILY,
    GLOBAL_ETF_REGISTRY,
)
from stock_data.contracts.global_equity import (
    GLOBAL_EQUITY_PRICE_DAILY,
    GLOBAL_EQUITY_REGISTRY,
)
from stock_data.contracts.global_market import (
    GLOBAL_COMMODITY_FUTURES_DAILY,
    GLOBAL_INDEX_DAILY_SYMBOLS,
    GLOBAL_INDEX_PRICE_DAILY,
    GLOBAL_INDEX_REGISTRY,
)
from stock_data.contracts.market_60m import MARKET_PRICE_60M_OBSERVATION
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.global_market import (
    validate_global_commodity_futures, validate_global_equity,
    validate_global_etf, validate_global_index,
)
from stock_data.validation.market_60m import validate_market_price_60m
from stock_data.providers.public_http_capture import capture_public_response


# Backwards-compatible provider alias; contracts/global_market.py is authoritative.
CONFIG = {
    symbol: str(spec["source_ticker"])
    for symbol, spec in GLOBAL_INDEX_REGISTRY.items()
}
# Backwards-compatible provider alias; contracts/global_etf.py is authoritative.
ETF_REGISTRY = GLOBAL_ETF_REGISTRY
EQUITY_REGISTRY = GLOBAL_EQUITY_REGISTRY
COMMODITY_CONFIG = {
    "GOLD": ("GC=F", "Gold"), "SILVER": ("SI=F", "Silver"),
    "COPPER": ("HG=F", "Copper"), "WTI_CRUDE_OIL": ("CL=F", "WTI Crude Oil"),
    "BRENT_CRUDE_OIL": ("BZ=F", "Brent Crude Oil"),
    "NASDAQ100_FUTURES": ("NQ=F", "Nasdaq 100 E-mini vendor-continuous future"),
    "SP500_FUTURES": ("ES=F", "S&P 500 E-mini vendor-continuous future"),
    "DOW_FUTURES": ("YM=F", "Dow E-mini vendor-continuous future"),
}
GLOBAL_FUTURES_DAILY_SYMBOLS = (
    "NASDAQ100_FUTURES", "GOLD", "WTI_CRUDE_OIL",
    "SP500_FUTURES", "DOW_FUTURES",
)
SUPPORTED_START = {
    "SP500": date(1928, 1, 3),
    "NASDAQ_COMPOSITE": date(1971, 2, 5),
    "NASDAQ100": date(1985, 10, 1),
}
NY = ZoneInfo("America/New_York")

# V1 is intentionally small and explicit. A symbol absent here cannot cause a
# network request, and session windows must come from an external reviewed
# exchange calendar rather than being inferred from weekdays.
MARKET_60M_REGISTRY = {
    "KOSPI": {"provider_symbol": "^KS11", "market": "KR", "asset_type": "INDEX", "timezone": "Asia/Seoul"},
    "KOSDAQ": {"provider_symbol": "^KQ11", "market": "KR", "asset_type": "INDEX", "timezone": "Asia/Seoul"},
    "KOSPI200": {"provider_symbol": "^KS200", "market": "KR", "asset_type": "INDEX", "timezone": "Asia/Seoul"},
    "005930": {"provider_symbol": "005930.KS", "market": "KR", "asset_type": "EQUITY", "timezone": "Asia/Seoul"},
    "000660": {"provider_symbol": "000660.KS", "market": "KR", "asset_type": "EQUITY", "timezone": "Asia/Seoul"},
    **{
        symbol: {
            "provider_symbol": symbol, "market": "US",
            "asset_type": "ETF" if symbol in {"SPY", "QQQ", "SOXX", "SOXL", "TQQQ", "TLT", "GLD"} else "EQUITY",
            "timezone": "America/New_York",
        }
        for symbol in ("SPY", "QQQ", "SOXX", "SOXL", "TQQQ", "NVDA", "AMD", "AVGO", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "TLT", "GLD")
    },
}

# Exact free/delayed dashboard scope. Treasury symbols are continuous futures
# prices; they are never interpreted as, or converted into, Treasury yields.
GLOBAL_MARKET_60M_REGISTRY = {
    "KOSPI_CURRENT_60M": {
        "provider_symbol": "^KS11", "market": "XKRX", "asset_type": "INDEX",
        "timezone": "Asia/Seoul", "instrument_type": "INDEX",
        "regular_session_close": "15:30",
    },
    "KOSDAQ_CURRENT_60M": {
        "provider_symbol": "^KQ11", "market": "XKRX", "asset_type": "INDEX",
        "timezone": "Asia/Seoul", "instrument_type": "INDEX",
        "regular_session_close": "15:30",
    },
    "USD_KRW_60M": {
        "provider_symbol": "KRW=X", "market": "GLOBAL_FX", "asset_type": "FOREX",
        "timezone": "Asia/Seoul", "instrument_type": "CURRENCY",
    },
    "UST2_FUTURES_60M": {
        "provider_symbol": "ZT=F", "market": "CBOT", "asset_type": "FUTURE_CONTINUOUS",
        "timezone": "America/Chicago", "instrument_type": "FUTURE",
    },
    "UST10_FUTURES_60M": {
        "provider_symbol": "ZN=F", "market": "CBOT", "asset_type": "FUTURE_CONTINUOUS",
        "timezone": "America/Chicago", "instrument_type": "FUTURE",
    },
    "UST30_FUTURES_60M": {
        "provider_symbol": "ZB=F", "market": "CBOT", "asset_type": "FUTURE_CONTINUOUS",
        "timezone": "America/Chicago", "instrument_type": "FUTURE",
    },
    "SP500_CURRENT_60M": {
        "provider_symbol": "^GSPC", "market": "XNYS", "asset_type": "INDEX",
        "timezone": "America/New_York", "instrument_type": "INDEX",
        "regular_session_close": "16:00",
    },
    "NASDAQ_CURRENT_60M": {
        "provider_symbol": "^IXIC", "market": "XNAS", "asset_type": "INDEX",
        "timezone": "America/New_York", "instrument_type": "INDEX",
        "regular_session_close": "16:00",
    },
    "NQ_FUTURES_CURRENT_60M": {
        "provider_symbol": "NQ=F", "market": "CME", "asset_type": "FUTURE_CONTINUOUS",
        "timezone": "America/New_York", "instrument_type": "FUTURE",
    },
    "SP500_FUTURES_CURRENT_60M": {
        "provider_symbol": "ES=F", "market": "CME", "asset_type": "FUTURE_CONTINUOUS",
        "timezone": "America/New_York", "instrument_type": "FUTURE",
        "expected_currency": "USD", "accepted_yahoo_exchanges": ("CME",),
    },
    "DOW_FUTURES_CURRENT_60M": {
        "provider_symbol": "YM=F", "market": "CBOT", "asset_type": "FUTURE_CONTINUOUS",
        "timezone": "America/New_York", "instrument_type": "FUTURE",
        "expected_currency": "USD", "accepted_yahoo_exchanges": ("CBT", "CBOT"),
    },
    "SOX_CURRENT_60M": {
        "provider_symbol": "^SOX", "market": "XNAS", "asset_type": "INDEX",
        "timezone": "America/New_York", "instrument_type": "INDEX",
        "regular_session_close": "16:00", "expected_currency": "USD",
        "accepted_yahoo_exchanges": ("NIM", "NGM", "NMS", "NASDAQ"),
    },
    "DOLLAR_INDEX_CURRENT_60M": {
        "provider_symbol": "DX-Y.NYB", "market": "ICE", "asset_type": "INDEX",
        "timezone": "America/New_York", "instrument_type": "INDEX",
        "expected_currency": "USD", "accepted_yahoo_exchanges": ("NYB", "ICE"),
    },
    "SOXX_CURRENT_60M": {
        "provider_symbol": "SOXX", "market": "XNAS", "asset_type": "ETF",
        "timezone": "America/New_York", "instrument_type": "ETF",
        "regular_session_close": "16:00",
    },
    "GOLD_CURRENT_60M": {
        "provider_symbol": "GC=F", "market": "COMEX", "asset_type": "FUTURE_CONTINUOUS",
        "timezone": "America/New_York", "instrument_type": "FUTURE",
    },
    "WTI_CURRENT_60M": {
        "provider_symbol": "CL=F", "market": "NYMEX", "asset_type": "FUTURE_CONTINUOUS",
        "timezone": "America/New_York", "instrument_type": "FUTURE",
    },
    "BITCOIN_CURRENT_60M": {
        "provider_symbol": "BTC-USD", "market": "CRYPTO", "asset_type": "CRYPTOCURRENCY",
        "timezone": "UTC", "instrument_type": "CRYPTOCURRENCY",
    },
}


def _epoch(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=NY).timestamp())


def _drop_daily_provider_gaps(
    frame: pd.DataFrame, price_columns: tuple[str, ...], dataset_label: str,
) -> tuple[pd.DataFrame, list[str]]:
    gap_mask = frame[list(price_columns)].isna().all(axis=1)
    provider_gap_dates = frame.loc[gap_mask, "date"].astype(str).tolist()
    frame = frame.loc[~gap_mask].reset_index(drop=True)
    if frame.empty:
        raise RuntimeError(f"Yahoo returned no {dataset_label} rows after provider gaps")
    return frame, provider_gap_dates


def _expected_60m_starts(
    session_windows: dict[date, tuple[datetime, datetime]],
) -> dict[date, tuple[pd.Timestamp, ...]]:
    expected = {}
    for market_date, (opened, closed) in session_windows.items():
        if opened.tzinfo is None or closed.tzinfo is None or opened >= closed:
            raise ValueError("60m session windows must be ordered timezone-aware datetimes")
        starts = []
        cursor = pd.Timestamp(opened).tz_convert("UTC")
        end = pd.Timestamp(closed).tz_convert("UTC")
        while cursor < end:
            starts.append(cursor)
            cursor += timedelta(hours=1)
        expected[market_date] = tuple(starts)
    return expected


def fetch_market_60m(
    symbol: str,
    *,
    session_windows: dict[date, tuple[datetime, datetime]],
    session=requests,
    capture_root: Path | None = None,
    retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    """Fetch complete regular sessions for one registered symbol.

    Calendar/finality ownership stays outside the provider adapter. The caller
    must pass exact accepted session windows, including holidays, DST and early
    closes. A partial or unexpected Yahoo session fails closed.
    """
    if symbol not in MARKET_60M_REGISTRY:
        raise ValueError(f"unregistered 60m symbol: {symbol}")
    if not session_windows:
        raise ValueError("60m fetch requires at least one explicit session window")
    spec = MARKET_60M_REGISTRY[symbol]
    expected = _expected_60m_starts(session_windows)
    opened = min(value[0] for value in session_windows.values())
    closed = max(value[1] for value in session_windows.values())
    params = {
        "period1": int(opened.timestamp()), "period2": int(closed.timestamp()) + 60,
        "interval": "60m", "events": "history", "includeAdjustedClose": "false",
        "includePrePost": "false",
    }
    ticker = str(spec["provider_symbol"])
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
    response = session.get(
        url, params=params,
        headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=30,
    )
    receipt = None
    if capture_root is not None:
        receipt = capture_public_response(
            root=capture_root, provider="yahoo", operation="chart_60m",
            request_url=url, request_parameters={"symbol": symbol, **params}, response=response,
        )
    response.raise_for_status()
    chart = response.json().get("chart")
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise RuntimeError("Yahoo 60m response contains an error")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1:
        raise RuntimeError("Yahoo 60m result is missing")
    item = results[0]
    meta = item.get("meta") or {}
    if str(meta.get("symbol")) != ticker or str(meta.get("dataGranularity")) not in {"60m", "1h"}:
        raise RuntimeError("Yahoo 60m identity or granularity differs")
    if str(meta.get("exchangeTimezoneName")) != str(spec["timezone"]):
        raise RuntimeError("Yahoo 60m exchange timezone differs")
    timestamps = item.get("timestamp") or []
    quote_rows = ((item.get("indicators") or {}).get("quote") or [])
    if not timestamps or len(quote_rows) != 1:
        raise RuntimeError("Yahoo returned empty 60m data")
    values = quote_rows[0]
    if any(len(values.get(column) or []) != len(timestamps) for column in ("open", "high", "low", "close", "volume")):
        raise RuntimeError("Yahoo 60m timestamp/value lengths differ")
    starts = pd.to_datetime(timestamps, unit="s", utc=True)
    observed_at = retrieved_at
    if observed_at is None and receipt is not None:
        observed_at = datetime.fromisoformat(receipt.captured_at_utc.replace("Z", "+00:00"))
    observed_at = observed_at or datetime.now(tz=ZoneInfo("UTC"))
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    rows = []
    for market_date, expected_starts in expected.items():
        opened_at = pd.Timestamp(session_windows[market_date][0]).tz_convert("UTC")
        closed_at = pd.Timestamp(session_windows[market_date][1]).tz_convert("UTC")
        positions = [index for index, value in enumerate(starts) if opened_at <= value < closed_at]
        observed_starts = tuple(starts[index] for index in positions)
        if observed_starts != expected_starts:
            raise RuntimeError(f"Yahoo 60m session incomplete or unexpected: {symbol} {market_date}")
        for index in positions:
            start = starts[index]
            end = min(start + timedelta(hours=1), closed_at)
            rows.append({
                "market_date": market_date, "market": spec["market"], "symbol": symbol,
                "asset_type": spec["asset_type"], "bar_start": start, "bar_end": end,
                "timezone": spec["timezone"], "session": "REGULAR", "interval": "60m",
                "actual_duration_minutes": int((end - start).total_seconds() // 60),
                **{column: values[column][index] for column in ("open", "high", "low", "close", "volume")},
                "provider": "yahoo_chart_api", "provider_symbol": ticker,
                "adjustment_status": "PROVIDER_UNADJUSTED_INTRADAY",
                "retrieved_at": observed_at, "fallback_used": False, "fallback_reason": None,
            })
    frame = pd.DataFrame(rows, columns=MARKET_PRICE_60M_OBSERVATION.column_names)
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = frame["volume"].astype("Int64")
    validate_market_price_60m(frame)
    return frame


def fetch_global_market_60m(
    series_id: str,
    *,
    start: datetime,
    end: datetime,
    session=requests,
    capture_root: Path | None = None,
    retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    """Fetch finalized delayed 60-minute bars for one exact global identity."""
    if series_id not in GLOBAL_MARKET_60M_REGISTRY:
        raise ValueError(f"unregistered global 60m series: {series_id}")
    if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end)):
        raise ValueError("global 60m bounds must be timezone-aware")
    if start >= end or end - start > timedelta(days=10):
        raise ValueError("global 60m bounds must be ordered and at most 10 days")
    spec = GLOBAL_MARKET_60M_REGISTRY[series_id]
    ticker = str(spec["provider_symbol"])
    params = {
        "period1": int(start.timestamp()), "period2": int(end.timestamp()),
        "interval": "60m", "events": "history", "includeAdjustedClose": "false",
        "includePrePost": "true",
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
    response = session.get(
        url, params=params,
        headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=30,
    )
    receipt = None
    if capture_root is not None:
        receipt = capture_public_response(
            root=capture_root, provider="yahoo", operation="global_chart_60m",
            request_url=url, request_parameters={"series_id": series_id, **params},
            response=response,
        )
    response.raise_for_status()
    chart = response.json().get("chart")
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise RuntimeError("Yahoo global 60m response contains an error")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1:
        raise RuntimeError("Yahoo global 60m result is missing")
    item = results[0]
    meta = item.get("meta") or {}
    returned_symbol = str(meta.get("symbol"))
    accepted_symbols = {ticker}
    if series_id == "USD_KRW_60M":
        # Yahoo has been observed canonicalizing the requested KRW=X identity
        # to USDKRW=X. No other alias or fallback is accepted.
        accepted_symbols.add("USDKRW=X")
    if returned_symbol not in accepted_symbols or str(meta.get("dataGranularity")) not in {"60m", "1h"}:
        raise RuntimeError("Yahoo global 60m identity or granularity differs")
    if str(meta.get("instrumentType")) != str(spec["instrument_type"]):
        raise RuntimeError("Yahoo global 60m instrument type differs")
    timestamps = item.get("timestamp") or []
    quote_rows = ((item.get("indicators") or {}).get("quote") or [])
    if not timestamps or len(quote_rows) != 1:
        raise RuntimeError("Yahoo returned empty global 60m data")
    values = quote_rows[0]
    for column in ("open", "high", "low", "close"):
        if len(values.get(column) or []) != len(timestamps):
            raise RuntimeError("Yahoo global 60m timestamp/value lengths differ")
    volumes = values.get("volume")
    if volumes is None:
        volumes = [None] * len(timestamps)
    if len(volumes) != len(timestamps):
        raise RuntimeError("Yahoo global 60m timestamp/value lengths differ")
    observed_at = retrieved_at
    if observed_at is None and receipt is not None:
        observed_at = datetime.fromisoformat(receipt.captured_at_utc.replace("Z", "+00:00"))
    observed_at = observed_at or datetime.now(tz=ZoneInfo("UTC"))
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    provider_time = pd.to_datetime(meta.get("regularMarketTime"), unit="s", utc=True, errors="coerce")
    cutoff = pd.Timestamp(observed_at).tz_convert("UTC")
    if not pd.isna(provider_time):
        cutoff = min(cutoff, provider_time)
    starts = pd.to_datetime(timestamps, unit="s", utc=True)
    rows = []
    for index, bar_start in enumerate(starts):
        bar_end = bar_start + timedelta(hours=1)
        session_close_text = spec.get("regular_session_close")
        if session_close_text is not None:
            local_start = bar_start.tz_convert(str(spec["timezone"]))
            close_clock = datetime.strptime(str(session_close_text), "%H:%M").time()
            local_close = pd.Timestamp(
                datetime.combine(local_start.date(), close_clock),
                tz=str(spec["timezone"]),
            )
            close_utc = local_close.tz_convert("UTC")
            if bar_start >= close_utc:
                continue
            if bar_start < close_utc < bar_end:
                bar_end = close_utc
        if bar_start < pd.Timestamp(start).tz_convert("UTC") or bar_start >= pd.Timestamp(end).tz_convert("UTC"):
            continue
        if bar_end > cutoff:
            continue
        prices = [values[column][index] for column in ("open", "high", "low", "close")]
        numeric_prices = pd.to_numeric(pd.Series(prices), errors="coerce")
        if numeric_prices.isna().any() or not np.isfinite(numeric_prices.to_numpy()).all():
            # Provider holes are omitted, never filled or interpolated.
            continue
        local_date = bar_start.tz_convert(str(spec["timezone"])).date()
        rows.append({
            "market_date": local_date, "market": spec["market"], "symbol": series_id,
            "asset_type": spec["asset_type"], "bar_start": bar_start, "bar_end": bar_end,
            "timezone": spec["timezone"], "session": "GLOBAL_CONTINUOUS", "interval": "60m",
            "actual_duration_minutes": int((bar_end - bar_start).total_seconds() // 60),
            **{column: values[column][index] for column in ("open", "high", "low", "close")},
            "volume": volumes[index], "provider": "yahoo_chart_api", "provider_symbol": ticker,
            "adjustment_status": "PROVIDER_UNADJUSTED_INTRADAY_DELAYED",
            "retrieved_at": observed_at, "fallback_used": False, "fallback_reason": None,
        })
    if not rows:
        raise RuntimeError("Yahoo returned no finalized global 60m bars")
    frame = pd.DataFrame(rows, columns=MARKET_PRICE_60M_OBSERVATION.column_names)
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = frame["volume"].astype("Int64")
    validate_market_price_60m(frame)
    return frame


def fetch_global_index(
    symbol: str, start: date, end: date, *, session=requests,
    capture_root: Path | None = None,
) -> pd.DataFrame:
    spec = GLOBAL_INDEX_REGISTRY[symbol]
    ticker = str(spec["source_ticker"])
    params = {"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1)),
              "interval": "1d", "events": "history", "includeAdjustedClose": "false"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
    response = session.get(
        url, params=params,
        headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=30,
    )
    if capture_root is not None:
        capture_public_response(
            root=capture_root, provider="yahoo", operation="chart",
            request_url=url, request_parameters={"symbol": symbol, **params}, response=response,
        )
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart")
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise RuntimeError("Yahoo chart response contains an error")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1:
        raise RuntimeError("Yahoo chart result is missing")
    item = results[0]
    meta = item.get("meta") or {}
    exchange = str(meta.get("exchangeName") or meta.get("fullExchangeName") or "")
    accepted_exchanges = tuple(spec.get("accepted_yahoo_exchanges") or ())
    require_exchange = bool(spec.get("require_exchange_identity"))
    if (
        str(meta.get("symbol")) != ticker
        or str(meta.get("instrumentType", "")).upper()
        != str(spec["instrument_type"])
        or str(meta.get("dataGranularity")) != "1d"
        or (require_exchange and not exchange)
        or (accepted_exchanges and exchange and exchange not in accepted_exchanges)
    ):
        raise RuntimeError("Yahoo index identity or granularity differs")
    timestamps = item.get("timestamp") or []
    quote_rows = ((item.get("indicators") or {}).get("quote") or [])
    if not timestamps or len(quote_rows) != 1:
        raise RuntimeError("Yahoo returned empty index data")
    values = quote_rows[0]
    if any(len(values.get(column) or []) != len(timestamps) for column in ("open","high","low","close","volume")):
        raise RuntimeError("Yahoo timestamp and value array lengths differ")
    frame = pd.DataFrame({
        "date": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(NY).date,
        "symbol": symbol, "source_ticker": ticker,
        **{column: values[column] for column in ("open","high","low","close","volume")},
    })
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame, provider_gap_dates = _drop_daily_provider_gaps(
        frame, ("open", "high", "low", "close"), "index",
    )
    for column in ("open","high","low","close","volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if spec.get("ohlc_fill_from_close"):
        # CBOE volatility indices (^VIX9D, ^VIX3M, ^VIX6M, ^SKEW) are published as a single
        # daily value; Yahoo leaves open/high/low null on many rows. Treat the close as the
        # day's level and drop rows without a close as provider gaps instead of failing.
        missing_close = frame["close"].isna()
        if missing_close.any():
            provider_gap_dates = tuple(sorted(set(provider_gap_dates) | set(frame.loc[missing_close, "date"])))
            frame = frame.loc[~missing_close].copy()
        for column in ("open", "high", "low"):
            frame[column] = frame[column].fillna(frame["close"])
        frame["high"] = frame[["high", "close"]].max(axis=1)
        frame["low"] = frame[["low", "close"]].min(axis=1)
    numeric = frame[["open","high","low","close"]]
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        raise RuntimeError("Yahoo OHLC contains missing or infinite values")
    if ((frame.high < frame.low) | ~frame.open.between(frame.low,frame.high) | ~frame.close.between(frame.low,frame.high)).any():
        raise RuntimeError("Yahoo OHLC relationship is invalid")
    frame["volume"] = frame["volume"].astype("Int64")
    frame = frame[list(GLOBAL_INDEX_PRICE_DAILY.column_names)]
    frame.attrs["provider_gap_dates"] = provider_gap_dates
    return frame


def _fetch_global_registered_security(
    symbol: str, start: date, end: date, *, registry, contract, validator,
    label: str, operation: str, session=requests,
    capture_root: Path | None = None, retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    if symbol not in registry:
        raise ValueError(f"unregistered global {label}: {symbol}")
    spec = registry[symbol]
    ticker = str(spec["source_ticker"])
    params = {"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1)),
              "interval": "1d", "events": "history", "includeAdjustedClose": "true"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
    response = session.get(url, params=params,
                           headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=30)
    receipt = None
    if capture_root is not None:
        receipt = capture_public_response(
            root=capture_root, provider="yahoo", operation=operation,
            request_url=url, request_parameters={"symbol": symbol, **params}, response=response,
        )
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart")
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise RuntimeError(f"Yahoo {label} chart response contains an error")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1:
        raise RuntimeError(f"Yahoo {label} chart result is missing")
    item = results[0]
    meta = item.get("meta") or {}
    if (
        str(meta.get("symbol")) != ticker
        or str(meta.get("instrumentType", "")).upper()
        != str(spec["instrument_type"]).upper()
    ):
        raise RuntimeError(f"Yahoo {label} identity/instrument type differs")
    if str(meta.get("dataGranularity")) != "1d":
        raise RuntimeError(f"Yahoo {label} response is not daily")
    currency = str(meta.get("currency") or "")
    exchange = str(meta.get("exchangeName") or meta.get("fullExchangeName") or "")
    expected_currency = str(spec.get("expected_currency") or "USD")
    accepted_exchanges = tuple(spec.get("accepted_yahoo_exchanges") or ())
    if currency != expected_currency or not exchange:
        raise RuntimeError(f"Yahoo {label} currency/exchange identity differs")
    if accepted_exchanges and exchange not in accepted_exchanges:
        raise RuntimeError(f"Yahoo {label} exchange identity differs")
    timestamps = item.get("timestamp") or []
    quote_rows = ((item.get("indicators") or {}).get("quote") or [])
    adjusted_rows = ((item.get("indicators") or {}).get("adjclose") or [])
    if not timestamps or len(quote_rows) != 1 or len(adjusted_rows) != 1:
        raise RuntimeError(f"Yahoo returned incomplete {label} data")
    values, adjusted = quote_rows[0], adjusted_rows[0].get("adjclose") or []
    if (any(len(values.get(column) or []) != len(timestamps)
            for column in ("open", "high", "low", "close", "volume"))
            or len(adjusted) != len(timestamps)):
        raise RuntimeError(f"Yahoo {label} timestamp/value lengths differ")
    observed_at = retrieved_at
    if observed_at is None and receipt is not None:
        observed_at = datetime.fromisoformat(receipt.captured_at_utc.replace("Z", "+00:00"))
    observed_at = observed_at or datetime.now(tz=ZoneInfo("UTC"))
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    frame = pd.DataFrame({
        "date": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(NY).date,
        "symbol": symbol, "source_ticker": ticker,
        **{column: values[column] for column in ("open", "high", "low", "close", "volume")},
        "adjusted_close": adjusted,
        "currency": currency,
        "exchange": exchange,
        "provider": "yahoo_chart_api", "retrieved_at": observed_at,
        "adjustment_status": "SOURCE_ADJUSTED_CLOSE_RETAINED_SEPARATELY",
    })
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame, provider_gap_dates = _drop_daily_provider_gaps(
        frame, ("open", "high", "low", "close", "adjusted_close"), label.upper(),
    )
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
    for column in ("open", "high", "low", "close", "adjusted_close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = frame["volume"].astype("Int64")
    frame = frame[list(contract.column_names)]
    validator(frame)
    frame.attrs["provider_gap_dates"] = provider_gap_dates
    return frame


def fetch_global_etf(
    symbol: str, start: date, end: date, *, session=requests,
    capture_root: Path | None = None, retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    """Fetch one explicitly registered ETF."""
    return _fetch_global_registered_security(
        symbol, start, end, session=session, capture_root=capture_root,
        retrieved_at=retrieved_at, registry=ETF_REGISTRY,
        contract=GLOBAL_ETF_PRICE_DAILY, validator=validate_global_etf,
        label="ETF", operation="etf_chart_daily",
    )


def fetch_global_equity(
    symbol: str, start: date, end: date, *, session=requests,
    capture_root: Path | None = None, retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    """Fetch one explicitly registered U.S. equity or depositary receipt."""
    return _fetch_global_registered_security(
        symbol, start, end, session=session, capture_root=capture_root,
        retrieved_at=retrieved_at, registry=EQUITY_REGISTRY,
        contract=GLOBAL_EQUITY_PRICE_DAILY, validator=validate_global_equity,
        label="equity", operation="equity_chart_daily",
    )


def fetch_commodity_future(
    symbol: str, start: date, end: date, *, session=requests,
    capture_root: Path | None = None,
) -> pd.DataFrame:
    """Fetch an explicit daily range for one Yahoo continuous-futures ticker."""
    ticker, asset = COMMODITY_CONFIG[symbol]
    params = {"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1)),
              "interval": "1d", "events": "history", "includeAdjustedClose": "false"}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
    response = session.get(url, params=params, headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=30)
    if capture_root is not None:
        capture_public_response(
            root=capture_root, provider="yahoo", operation="commodity_chart_daily",
            request_url=url, request_parameters={"symbol": symbol, **params}, response=response,
        )
    response.raise_for_status()
    payload = response.json()
    chart = payload.get("chart")
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise RuntimeError("Yahoo commodity chart response contains an error")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1:
        raise RuntimeError("Yahoo commodity chart result is missing")
    item = results[0]
    meta = item.get("meta") or {}
    if (
        str(meta.get("symbol")) != ticker
        or str(meta.get("instrumentType", "")).upper() != "FUTURE"
        or str(meta.get("dataGranularity")) != "1d"
    ):
        raise RuntimeError("Yahoo futures identity or granularity differs")
    timestamps = item.get("timestamp") or []
    quote_rows = ((item.get("indicators") or {}).get("quote") or [])
    if not timestamps or len(quote_rows) != 1:
        raise RuntimeError("Yahoo returned empty commodity data")
    values = quote_rows[0]
    if any(len(values.get(column) or []) != len(timestamps) for column in ("open", "high", "low", "close", "volume")):
        raise RuntimeError("Yahoo commodity timestamp and value array lengths differ")
    # Yahoo may append one live, still-forming futures bar whose timestamp is
    # exactly meta.regularMarketTime rather than the exchange-local day
    # boundary used by completed daily bars.  Retain it in Landing evidence but
    # exclude it from the completed-daily candidate.  Never deduplicate by date:
    # any other duplicate remains a validation failure.
    regular_market_time = meta.get("regularMarketTime")
    completed_positions = list(range(len(timestamps)))
    if timestamps and regular_market_time == timestamps[-1]:
        observed = pd.Timestamp(timestamps[-1], unit="s", tz="UTC").tz_convert(NY)
        if observed.time() != pd.Timestamp("00:00:00").time():
            completed_positions.pop()
    if not completed_positions:
        raise RuntimeError("Yahoo returned no completed commodity daily bars")
    frame = pd.DataFrame({
        "date": pd.to_datetime(
            [timestamps[position] for position in completed_positions], unit="s", utc=True,
        ).tz_convert(NY).date,
        "symbol": symbol, "source_ticker": ticker, "asset": asset,
        **{
            column: [values[column][position] for position in completed_positions]
            for column in ("open", "high", "low", "close", "volume")
        },
    })
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    frame = frame.loc[frame["date"].between(start.isoformat(), end.isoformat())].reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("Yahoo returned no commodity daily bars inside the requested range")
    frame, provider_gap_dates = _drop_daily_provider_gaps(
        frame, ("open", "high", "low", "close"), "commodity daily",
    )
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = frame["volume"].astype("Int64")
    prices = frame[["open", "high", "low", "close"]]
    if prices.isna().any().any():
        raise RuntimeError("Yahoo commodity OHLC contains partial or invalid values")
    complete = prices.notna().all(axis=1)
    missing = ~prices.notna().any(axis=1)
    relation = complete & (prices.high.ge(prices.low) & prices.open.between(prices.low, prices.high) & prices.close.between(prices.low, prices.high))
    frame["ohlc_status"] = "PARTIAL_MISSING"
    frame.loc[missing, "ohlc_status"] = "ALL_MISSING"
    frame.loc[complete & ~relation, "ohlc_status"] = "SOURCE_RELATION_ANOMALY"
    frame.loc[relation, "ohlc_status"] = "VALID"
    frame = frame[list(GLOBAL_COMMODITY_FUTURES_DAILY.column_names)]
    validate_global_commodity_futures(frame)
    frame.attrs["provider_gap_dates"] = provider_gap_dates
    return frame


def collect_commodity_futures(
    start: date, end: date, *, root: Path, capture_root: Path,
    overlap_days: int = 10, session=requests,
) -> pd.DataFrame:
    """Capture five explicit daily ranges, then atomically upsert one dataset."""
    existing = (
        read_dataset(root, GLOBAL_COMMODITY_FUTURES_DAILY, validate_global_commodity_futures)
        if root.exists() and any(root.rglob("data.parquet"))
        else None
    )
    frames = []
    for symbol in COMMODITY_CONFIG:
        symbol_start = start
        if existing is not None:
            symbol_rows = existing.loc[existing["symbol"] == symbol]
            if not symbol_rows.empty:
                latest = pd.Timestamp(symbol_rows["date"].max()).date()
                symbol_start = max(start, latest - timedelta(days=overlap_days))
        frames.append(fetch_commodity_future(symbol, symbol_start, end, session=session, capture_root=capture_root))
    result = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    if existing is not None:
        keys = set(map(tuple, result[["date", "symbol"]].to_numpy()))
        old_keys = existing[["date", "symbol"]].apply(tuple, axis=1)
        result = pd.concat([existing.loc[~old_keys.isin(keys)], result], ignore_index=True)
        result = result.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
    validate_global_commodity_futures(result)
    write_dataset_atomic(result, root, GLOBAL_COMMODITY_FUTURES_DAILY, validate_global_commodity_futures)
    return result


def collect_global_indices(
    start: date, end: date, *, root, overlap_days: int = 10,
    capture_root: Path | None = None, session=requests,
) -> pd.DataFrame:
    existing = (
        read_dataset(root, GLOBAL_INDEX_PRICE_DAILY, validate_global_index)
        if root.exists() and any(root.rglob("data.parquet"))
        else None
    )
    fetch_start = max(start, pd.Timestamp(existing.date.max()).date()-timedelta(days=overlap_days)) if existing is not None else start
    frames=[]; errors=[]
    for symbol in CONFIG:
        try:
            symbol_start = max(fetch_start, SUPPORTED_START.get(symbol, fetch_start))
            frames.append(fetch_global_index(
                symbol, symbol_start, end, session=session, capture_root=capture_root,
            ))
        except Exception as error:
            errors.append(f"{symbol}: {type(error).__name__}")
    if not frames:
        raise RuntimeError("all Yahoo symbols failed: " + "; ".join(errors))
    incoming=pd.concat(frames,ignore_index=True)
    if errors:
        raise RuntimeError("partial Yahoo failure: " + "; ".join(errors))
    if existing is not None:
        keys=set(map(tuple,incoming[["date","symbol"]].to_numpy()))
        oldkeys=existing[["date","symbol"]].apply(tuple,axis=1)
        incoming=pd.concat([existing.loc[~oldkeys.isin(keys)],incoming],ignore_index=True)
    incoming=incoming.sort_values(["date","symbol"],kind="stable").reset_index(drop=True)
    validate_global_index(incoming)
    write_dataset_atomic(incoming,root,GLOBAL_INDEX_PRICE_DAILY,validate_global_index)
    return incoming
