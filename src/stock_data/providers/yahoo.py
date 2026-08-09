from __future__ import annotations

from datetime import date, datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from stock_data.contracts.global_market import GLOBAL_INDEX_PRICE_DAILY
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.global_market import validate_global_index


CONFIG = {"SP500": "^GSPC", "NASDAQ_COMPOSITE": "^IXIC", "NASDAQ100": "^NDX"}
SUPPORTED_START = {
    "SP500": date(1928, 1, 3),
    "NASDAQ_COMPOSITE": date(1971, 2, 5),
    "NASDAQ100": date(1985, 10, 1),
}
NY = ZoneInfo("America/New_York")


def _epoch(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=NY).timestamp())


def fetch_global_index(symbol: str, start: date, end: date, *, session=requests) -> pd.DataFrame:
    ticker = CONFIG[symbol]
    response = session.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}",
        params={"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1)),
                "interval": "1d", "events": "history", "includeAdjustedClose": "false"},
        headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=30,
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
    for column in ("open","high","low","close","volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    numeric = frame[["open","high","low","close"]]
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        raise RuntimeError("Yahoo OHLC contains missing or infinite values")
    if ((frame.high < frame.low) | ~frame.open.between(frame.low,frame.high) | ~frame.close.between(frame.low,frame.high)).any():
        raise RuntimeError("Yahoo OHLC relationship is invalid")
    frame["volume"] = frame["volume"].astype("Int64")
    return frame[list(GLOBAL_INDEX_PRICE_DAILY.column_names)]


def collect_global_indices(start: date, end: date, *, root, overlap_days: int = 10) -> pd.DataFrame:
    existing = (
        read_dataset(root, GLOBAL_INDEX_PRICE_DAILY, validate_global_index)
        if root.exists() and any(root.rglob("data.parquet"))
        else None
    )
    fetch_start = max(start, pd.Timestamp(existing.date.max()).date()-timedelta(days=overlap_days)) if existing is not None else start
    frames=[]; errors=[]
    for symbol in CONFIG:
        try:
            symbol_start = max(fetch_start, SUPPORTED_START[symbol])
            frames.append(fetch_global_index(symbol,symbol_start,end))
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
