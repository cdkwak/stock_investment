from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_EQUITY_PRICE_PROVISIONAL_DAILY = DatasetContract(
    name="kr_equity_price_provisional_daily",
    version=1,
    status="active",
    description=(
        "Same-session Korean equity OHLCV observed after the close and replaced "
        "for consumer use by canonical daily rows."
    ),
    source="KRX/pykrx stock.get_market_ohlcv_by_ticker",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"),
    sort_key=("date", "market", "symbol"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("open", "int64", False),
        ColumnContract("high", "int64", False),
        ColumnContract("low", "int64", False),
        ColumnContract("close", "int64", False),
        ColumnContract("volume", "int64", False),
        ColumnContract("trading_value", "int64", False),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("source_date", "date32", False),
        ColumnContract("provisional", "bool", False),
        ColumnContract("observed_at", "timestamp[us, UTC]", False),
    ),
)


class ProvisionalEquityValidationError(ValueError):
    pass


def validate_kr_equity_price_provisional_daily(
    dataframe: pd.DataFrame, *, allow_empty: bool = False,
) -> None:
    contract = KR_EQUITY_PRICE_PROVISIONAL_DAILY
    if list(dataframe.columns) != list(contract.column_names):
        raise ProvisionalEquityValidationError(
            f"{contract.name} schema or column order is invalid"
        )
    if dataframe.empty:
        if allow_empty:
            return
        raise ProvisionalEquityValidationError(f"{contract.name} must not be empty")
    if not dataframe["market"].isin({"KOSPI", "KOSDAQ"}).all():
        raise ProvisionalEquityValidationError("market must be KOSPI or KOSDAQ")
    if not dataframe["symbol"].astype(str).str.fullmatch(r"[0-9A-Z]{6}").all():
        raise ProvisionalEquityValidationError("symbol must be a six-character KRX code")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise ProvisionalEquityValidationError("date+market+symbol contains duplicates")
    expected = dataframe.sort_values(list(contract.sort_key), kind="stable").index
    if not expected.equals(dataframe.index):
        raise ProvisionalEquityValidationError(f"{contract.name} rows are not sorted")

    dates = pd.to_datetime(dataframe["date"], format="%Y-%m-%d", errors="coerce")
    source_dates = pd.to_datetime(dataframe["source_date"], errors="coerce")
    if dates.isna().any() or source_dates.isna().any():
        raise ProvisionalEquityValidationError("date and source_date must be valid")
    if not dates.dt.strftime("%Y-%m-%d").equals(dataframe["date"].astype(str)):
        raise ProvisionalEquityValidationError("date must be valid YYYY-MM-DD")
    if not source_dates.dt.strftime("%Y-%m-%d").equals(dataframe["date"].astype(str)):
        raise ProvisionalEquityValidationError("source_date must equal date")
    for column in ("source", "source_operation"):
        if dataframe[column].fillna("").astype(str).str.strip().eq("").any():
            raise ProvisionalEquityValidationError(f"{column} must not be empty")
    if not dataframe["provisional"].map(lambda value: type(value) in {bool, np.bool_}).all():
        raise ProvisionalEquityValidationError("provisional must be boolean")
    if not dataframe["provisional"].all():
        raise ProvisionalEquityValidationError("every row must remain provisional")
    observed = pd.to_datetime(dataframe["observed_at"], errors="coerce", utc=True)
    if observed.isna().any():
        raise ProvisionalEquityValidationError("observed_at must be a UTC timestamp")

    columns = ("open", "high", "low", "close", "volume", "trading_value")
    numeric = dataframe[list(columns)].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise ProvisionalEquityValidationError("numeric values must be finite and present")
    if (numeric < 0).any().any() or (numeric % 1 != 0).any().any():
        raise ProvisionalEquityValidationError("numeric values must be nonnegative integers")
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
        raise ProvisionalEquityValidationError("OHLC relationship is invalid")


__all__ = [
    "KR_EQUITY_PRICE_PROVISIONAL_DAILY",
    "ProvisionalEquityValidationError",
    "validate_kr_equity_price_provisional_daily",
]
