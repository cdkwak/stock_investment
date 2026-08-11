from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from stock_data.contracts.kr_equity import (
    KR_EQUITY_MARKET_CAP_DAILY, KR_EQUITY_PRICE_DAILY, KR_EQUITY_UNIVERSE_DAILY,
)
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.validation.kr_equity import validate_equity_market_cap, validate_equity_price


RAW_COLUMNS = (
    "Code", "Name", "Close", "Dept", "ChangeCode", "Changes", "ChangesRatio", "Volume",
    "Amount", "Open", "High", "Low", "Marcap", "Stocks", "Market", "MarketId", "Rank", "Date",
)
MARKETS = {"STK": "KOSPI", "KSQ": "KOSDAQ"}


@dataclass(frozen=True)
class NormalizedMarcap:
    price: pd.DataFrame
    market_cap: pd.DataFrame
    universe: pd.DataFrame
    quarantine: pd.DataFrame


def _symbol(value: object) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("marcap symbol is empty")
    return text.zfill(6) if text.isnumeric() else text


def _integer_series(frame: pd.DataFrame, column: str) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any() or not np.isfinite(values.to_numpy(dtype="float64")).all():
        raise ValueError(f"marcap {column} is missing or non-finite after quarantine")
    if (values < 0).any() or (values % 1).ne(0).any():
        raise ValueError(f"marcap {column} is negative or non-integral")
    return values.astype("int64")


def normalize_annual(raw: pd.DataFrame, source_file: str | Path) -> NormalizedMarcap:
    if tuple(raw.columns) != RAW_COLUMNS:
        raise ValueError("marcap annual parquet schema is invalid")
    working = raw.copy()
    if not working["MarketId"].isin(MARKETS).all():
        raise ValueError("marcap MarketId is outside STK/KSQ")
    expected_market = working["MarketId"].map(MARKETS)
    if not expected_market.equals(working["Market"]):
        raise ValueError("marcap Market and MarketId disagree")
    dates = pd.to_datetime(working["Date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("marcap Date is invalid")

    missing_close = working["Close"].isna()
    suspended = (
        working["Open"].eq(0) & working["High"].eq(0) & working["Low"].eq(0)
        & working["Close"].fillna(0).ge(0)
    )
    relation_invalid = ~suspended & (
        (working["High"] < working["Low"])
        | ~working["Open"].between(working["Low"], working["High"])
        | ~working["Close"].between(working["Low"], working["High"])
    )
    quarantined = missing_close | relation_invalid
    quarantine = working.loc[quarantined].copy()
    quarantine["quarantine_reason"] = np.where(
        missing_close.loc[quarantined], "missing_close;ohlc_invalid", "ohlc_invalid")
    quarantine["source"] = "financedata_marcap"
    quarantine["source_file"] = Path(source_file).name

    valid = working.loc[~quarantined].copy()
    date = dates.loc[~quarantined].dt.strftime("%Y-%m-%d")
    market = valid["MarketId"].map(MARKETS)
    symbol = valid["Code"].map(_symbol)
    common = pd.DataFrame({"date": date, "market": market, "symbol": symbol})
    provenance = pd.DataFrame({
        "source": "financedata_marcap", "source_operation": Path(source_file).name,
        "source_date": date,
    })

    price = common.assign(
        open=_integer_series(valid, "Open"), high=_integer_series(valid, "High"),
        low=_integer_series(valid, "Low"), close=_integer_series(valid, "Close"),
        volume=_integer_series(valid, "Volume"), trading_value=_integer_series(valid, "Amount"),
        source=provenance["source"], source_operation=provenance["source_operation"],
        source_date=provenance["source_date"],
    )[list(KR_EQUITY_PRICE_DAILY.column_names)]
    cap = common.assign(
        market_cap=_integer_series(valid, "Marcap"),
        shares_outstanding=_integer_series(valid, "Stocks"),
        source=provenance["source"], source_operation=provenance["source_operation"],
        source_date=provenance["source_date"],
    )[list(KR_EQUITY_MARKET_CAP_DAILY.column_names)]
    universe = common.assign(
        isin=None, name=None, short_name=valid["Name"].astype(str), english_name=None,
        security_group=None, security_type=None, listing_date=None,
        listed_shares=_integer_series(valid, "Stocks"), par_value=None,
        corporate_number=None, corporate_name=None,
        source=provenance["source"], source_operation=provenance["source_operation"],
        source_date=provenance["source_date"],
    )[list(KR_EQUITY_UNIVERSE_DAILY.column_names)]

    for frame, contract, validator in (
        (price, KR_EQUITY_PRICE_DAILY, validate_equity_price),
        (cap, KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap),
        (universe, KR_EQUITY_UNIVERSE_DAILY,
         lambda value: validate_data_v1(value, KR_EQUITY_UNIVERSE_DAILY)),
    ):
        frame.sort_values(list(contract.sort_key), kind="stable", inplace=True)
        frame.reset_index(drop=True, inplace=True)
        validator(frame)
    return NormalizedMarcap(price, cap, universe, quarantine.reset_index(drop=True))
