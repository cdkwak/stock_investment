from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.base import DatasetContract
from stock_data.contracts.kr_equity import (
    KR_EQUITY_MARKET_CAP_DAILY,
    KR_EQUITY_MASTER,
    KR_EQUITY_PRICE_DAILY,
)


class EquityValidationError(ValueError):
    pass


def _base(dataframe: pd.DataFrame, contract: DatasetContract, *, allow_empty: bool) -> None:
    if list(dataframe.columns) != list(contract.column_names):
        raise EquityValidationError(f"{contract.name} schema or column order is invalid")
    if dataframe.empty:
        if allow_empty:
            return
        raise EquityValidationError(f"{contract.name} must not be empty")
    for column in ("market", "symbol"):
        values = dataframe[column].fillna("").astype(str).str.strip()
        if values.eq("").any():
            raise EquityValidationError(f"{column} must not be empty")
    if not dataframe["market"].isin({"KOSPI", "KOSDAQ"}).all():
        raise EquityValidationError("market must be KOSPI or KOSDAQ")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise EquityValidationError(f"{'+'.join(contract.primary_key)} contains duplicates")
    expected = dataframe.sort_values(list(contract.sort_key), kind="stable").index
    if not expected.equals(dataframe.index):
        raise EquityValidationError(f"{contract.name} rows are not sorted")


def _dates(dataframe: pd.DataFrame) -> None:
    dates = pd.to_datetime(dataframe["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.strftime("%Y-%m-%d").equals(dataframe["date"].astype(str)):
        raise EquityValidationError("date must be valid YYYY-MM-DD")


def _provenance(dataframe: pd.DataFrame) -> None:
    for column in ("source", "source_operation", "source_date"):
        if dataframe[column].fillna("").astype(str).str.strip().eq("").any():
            raise EquityValidationError(f"{column} must not be empty")
    source_dates = pd.to_datetime(dataframe["source_date"], errors="coerce")
    if source_dates.isna().any():
        raise EquityValidationError("source_date must be valid")
    if not source_dates.dt.strftime("%Y-%m-%d").equals(dataframe["date"].astype(str)):
        raise EquityValidationError("daily source_date must equal date")


def _numeric(dataframe: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    numeric = dataframe[list(columns)].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise EquityValidationError("numeric values must not be missing")
    if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise EquityValidationError("numeric values contain infinity")
    if (numeric < 0).any().any():
        raise EquityValidationError("numeric values must be nonnegative")
    return numeric


def validate_equity_price(dataframe: pd.DataFrame, *, allow_empty: bool = False) -> None:
    _base(dataframe, KR_EQUITY_PRICE_DAILY, allow_empty=allow_empty)
    if dataframe.empty:
        return
    _dates(dataframe)
    _provenance(dataframe)
    numeric = _numeric(
        dataframe, ("open", "high", "low", "close", "volume", "trading_value")
    )
    suspended = (
        numeric["open"].eq(0)
        & numeric["high"].eq(0)
        & numeric["low"].eq(0)
        & numeric["close"].ge(0)
    )
    invalid = ~suspended & (
        (numeric["high"] < numeric["low"])
        | ~numeric["open"].between(numeric["low"], numeric["high"])
        | ~numeric["close"].between(numeric["low"], numeric["high"])
    )
    if invalid.any():
        raise EquityValidationError("OHLC relationship is invalid")


def validate_equity_market_cap(dataframe: pd.DataFrame, *, allow_empty: bool = False) -> None:
    _base(dataframe, KR_EQUITY_MARKET_CAP_DAILY, allow_empty=allow_empty)
    if dataframe.empty:
        return
    _dates(dataframe)
    _provenance(dataframe)
    _numeric(dataframe, ("market_cap", "shares_outstanding"))


def validate_equity_master(dataframe: pd.DataFrame, *, allow_empty: bool = False) -> None:
    _base(dataframe, KR_EQUITY_MASTER, allow_empty=allow_empty)
    if dataframe.empty:
        return
    if dataframe["name"].fillna("").astype(str).str.strip().eq("").any():
        raise EquityValidationError("name must not be empty")
    if dataframe["source"].fillna("").astype(str).str.strip().eq("").any():
        raise EquityValidationError("master source must not be empty")
    populated_isin = dataframe["isin"].dropna().astype(str).str.strip()
    if populated_isin[populated_isin.ne("")].duplicated().any():
        raise EquityValidationError("master ISIN collision")
    for column in ("listing_date", "delisting_date", "deposit_registration_date",
                   "deposit_cancellation_date", "source_date"):
        values = dataframe[column].dropna().astype(str)
        if not values.empty and pd.to_datetime(values, errors="coerce").isna().any():
            raise EquityValidationError(f"invalid master {column}")
