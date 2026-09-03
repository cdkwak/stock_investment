from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.kr_etf import KR_ETF_MASTER, KR_ETF_PRICE_DAILY


def _require_columns(dataframe: pd.DataFrame, expected: tuple[str, ...], label: str) -> None:
    if list(dataframe.columns) != list(expected) or dataframe.empty:
        raise ValueError(f"{label} schema invalid or empty")


def _validate_symbols(values: pd.Series, label: str) -> None:
    if not values.astype(str).str.fullmatch(r"\d{6}").all():
        raise ValueError(f"{label} symbol is not a six-digit KRX code")


def validate_kr_etf_master(dataframe: pd.DataFrame) -> None:
    _require_columns(dataframe, KR_ETF_MASTER.column_names, "Korean ETF master")
    if dataframe.duplicated(list(KR_ETF_MASTER.primary_key)).any():
        raise ValueError("duplicate Korean ETF master key")
    _validate_symbols(dataframe["symbol"], "Korean ETF master")
    required = dataframe[[
        "symbol", "name", "market", "security_type", "listing_status",
        "leverage_multiple", "source", "source_operation", "source_date",
    ]]
    if required.isna().any().any() or dataframe["name"].astype(str).str.strip().eq("").any():
        raise ValueError("Korean ETF master identity/provenance is incomplete")
    if not dataframe["market"].astype(str).eq("KRX").all():
        raise ValueError("Korean ETF master market differs")
    if not dataframe["security_type"].astype(str).eq("ETF").all():
        raise ValueError("Korean ETF master security type differs")
    if not dataframe["listing_status"].astype(str).eq("LISTED_AT_SOURCE_DATE").all():
        raise ValueError("Korean ETF master listing status differs")
    multiples = pd.to_numeric(dataframe["leverage_multiple"], errors="coerce")
    if multiples.isna().any() or not multiples.isin((-2, 1, 2)).all():
        raise ValueError("Korean ETF master leverage multiple differs")
    source_dates = pd.to_datetime(dataframe["source_date"], errors="coerce")
    if source_dates.isna().any():
        raise ValueError("Korean ETF master source date is invalid")
    listing_dates = pd.to_datetime(dataframe["listing_date"], errors="coerce")
    present = dataframe["listing_date"].notna()
    if listing_dates[present].isna().any() or (listing_dates[present] > source_dates[present]).any():
        raise ValueError("Korean ETF master listing date is invalid")


def validate_kr_etf_price_daily(dataframe: pd.DataFrame) -> None:
    _require_columns(dataframe, KR_ETF_PRICE_DAILY.column_names, "Korean ETF price")
    if dataframe.duplicated(list(KR_ETF_PRICE_DAILY.primary_key)).any():
        raise ValueError("duplicate Korean ETF price key")
    _validate_symbols(dataframe["symbol"], "Korean ETF price")
    if dataframe[["date", "symbol", "source", "source_operation", "source_date"]].isna().any().any():
        raise ValueError("Korean ETF price identity/provenance is incomplete")
    dates = pd.to_datetime(dataframe["date"], errors="coerce")
    source_dates = pd.to_datetime(dataframe["source_date"], errors="coerce")
    if dates.isna().any() or source_dates.isna().any() or not dates.equals(source_dates):
        raise ValueError("Korean ETF price source date differs")
    numeric = dataframe[[
        "open", "high", "low", "close", "volume", "trading_value", "nav",
    ]].apply(pd.to_numeric, errors="coerce")
    required = numeric.drop(columns="nav")
    if required.isna().any().any():
        raise ValueError("Korean ETF required numeric value is missing")
    finite = numeric.to_numpy(dtype="float64")
    if not np.isfinite(finite[~np.isnan(finite)]).all():
        raise ValueError("Korean ETF numeric value is non-finite")
    if (required < 0).any().any() or (numeric["nav"].dropna() < 0).any():
        raise ValueError("Korean ETF numeric value is negative")
    active_ohlc = numeric[["open", "high", "low"]].ne(0).all(axis=1)
    relation = (
        numeric["high"].ge(numeric["low"])
        & numeric["open"].between(numeric["low"], numeric["high"])
        & numeric["close"].between(numeric["low"], numeric["high"])
    )
    if (~relation[active_ohlc]).any():
        raise ValueError("Korean ETF OHLC relationship is invalid")


__all__ = ["validate_kr_etf_master", "validate_kr_etf_price_daily"]
