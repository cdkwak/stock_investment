from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from stock_data.contracts.market_15m import MARKET_PRICE_15M_OBSERVATION
from stock_data.providers.public_http_capture import capture_public_response
from stock_data.validation.market_15m import (
    DELAYED_CLASSIFICATION,
    YAHOO_15M_IDENTITIES,
    validate_market_price_15m,
    yahoo_native_15m_grid_aligned,
)


YAHOO_15M_REGISTRY = {
    symbol: {
        "market": market,
        "instrument_type": instrument,
        "session": session_name,
        "display_timezone": "Asia/Seoul",
        "include_pre_post": session_name == "GLOBAL_CONTINUOUS",
    }
    for symbol, (market, instrument, session_name) in YAHOO_15M_IDENTITIES.items()
}


def fetch_market_15m(
    series_id: str,
    *,
    start: datetime,
    end: datetime,
    session=requests,
    capture_root: Path | None = None,
    retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    """Fetch completed native Yahoo 15m bars for one exact delayed identity."""
    if series_id not in YAHOO_15M_REGISTRY:
        raise ValueError(f"unregistered Yahoo 15m identity: {series_id}")
    if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end)):
        raise ValueError("Yahoo 15m bounds must be timezone-aware")
    if start >= end or end - start > timedelta(days=8):
        raise ValueError("Yahoo 15m bounds must be ordered and at most 8 days")
    observed_at = retrieved_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    observed_at = observed_at.astimezone(timezone.utc)

    spec = YAHOO_15M_REGISTRY[series_id]
    params = {
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "15m",
        "events": "history",
        "includeAdjustedClose": "false",
        "includePrePost": "true" if spec["include_pre_post"] else "false",
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(series_id, safe='')}"
    response = session.get(
        url,
        params=params,
        headers={"User-Agent": "stock-investment-rev1/0.1"},
        timeout=30,
    )
    if capture_root is not None:
        capture_public_response(
            root=capture_root,
            provider="yahoo",
            operation="chart_15m",
            request_url=url,
            request_parameters={"series_id": series_id, **params},
            response=response,
        )
    response.raise_for_status()
    chart = response.json().get("chart")
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise RuntimeError("Yahoo 15m response contains an error")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1:
        raise RuntimeError("Yahoo 15m result is missing")
    item = results[0]
    meta = item.get("meta") or {}
    if str(meta.get("symbol")) != series_id or str(meta.get("dataGranularity")) != "15m":
        raise RuntimeError("Yahoo 15m identity or native granularity differs")
    source_timezone = str(meta.get("exchangeTimezoneName") or "")
    try:
        source_zone = ZoneInfo(source_timezone)
    except Exception as error:
        raise RuntimeError("Yahoo 15m source timezone is missing or invalid") from error
    timestamps = item.get("timestamp") or []
    quote_rows = ((item.get("indicators") or {}).get("quote") or [])
    if not timestamps or len(quote_rows) != 1:
        raise RuntimeError("Yahoo returned empty 15m data")
    values = quote_rows[0]
    columns = ("open", "high", "low", "close", "volume")
    if any(len(values.get(column) or []) != len(timestamps) for column in columns):
        raise RuntimeError("Yahoo 15m timestamp/value lengths differ")

    rows = []
    lower = pd.Timestamp(start).tz_convert("UTC")
    upper = pd.Timestamp(end).tz_convert("UTC")
    for index, start_value in enumerate(pd.to_datetime(timestamps, unit="s", utc=True)):
        bar_end = start_value + timedelta(minutes=15)
        if start_value < lower or start_value >= upper or bar_end > observed_at:
            continue
        # Yahoo can append one quote-snapshot timestamp after the last native
        # interval.  It carries OHLC values but is not on the advertised 15m
        # grid, so never round or relabel it as a completed bar.  Restrict the
        # omission to the final provider row; an off-grid row in the middle is
        # retained here so the contract validator fails the whole response.
        if (
            index == len(timestamps) - 1
            and not yahoo_native_15m_grid_aligned(series_id, start_value)
        ):
            continue
        # Yahoo can insert a fully empty timestamp as a market-data gap while
        # returning later completed bars in the same response.  It is not an
        # observation and must not poison the otherwise valid frame.  Partial
        # OHLC rows remain present so the strict validator still rejects them.
        if all(pd.isna(values[column][index]) for column in ("open", "high", "low", "close")):
            continue
        rows.append({
            "market_date": start_value.tz_convert(source_zone).date(),
            "market": spec["market"],
            "series_id": series_id,
            "provider_symbol": series_id,
            "instrument_type": spec["instrument_type"],
            "bar_start": start_value,
            "bar_end": bar_end,
            "source_timezone": source_timezone,
            "display_timezone": spec["display_timezone"],
            "session": spec["session"],
            "interval": "15m",
            **{column: values[column][index] for column in columns},
            "provider": "yahoo_chart_api",
            "data_availability": DELAYED_CLASSIFICATION,
            "retrieved_at": observed_at,
        })
    frame = pd.DataFrame(rows, columns=MARKET_PRICE_15M_OBSERVATION.column_names)
    if frame.empty:
        raise RuntimeError("Yahoo returned no completed 15m bars inside the requested bounds")
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = frame["volume"].astype("Int64")
    validate_market_price_15m(frame)
    return frame


__all__ = ["YAHOO_15M_REGISTRY", "fetch_market_15m"]
