from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.kr_index_daily import KR_INDEX_DAILY


class DatasetValidationError(ValueError):
    pass


def validate_kr_index_daily(dataframe: pd.DataFrame) -> None:
    expected = list(KR_INDEX_DAILY.column_names)
    if list(dataframe.columns) != expected:
        raise DatasetValidationError("kr_index_daily column order or schema is invalid")
    if dataframe.empty:
        raise DatasetValidationError("kr_index_daily must not be empty")

    dates = pd.to_datetime(dataframe["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        raise DatasetValidationError("date must use a valid YYYY-MM-DD value")
    if not dates.dt.strftime("%Y-%m-%d").equals(dataframe["date"].astype(str)):
        raise DatasetValidationError("date must use YYYY-MM-DD")

    for column in ("symbol", "market", "source"):
        if dataframe[column].isna().any() or dataframe[column].astype(str).str.strip().eq("").any():
            raise DatasetValidationError(f"{column} must not be empty")
    if not dataframe["source"].eq("pykrx").all():
        raise DatasetValidationError("source must be pykrx")
    if not dataframe["market"].isin({"KOSPI", "KOSDAQ"}).all():
        raise DatasetValidationError("market must be KOSPI or KOSDAQ")
    allowed_symbols = {"KOSPI": {"KOSPI", "KOSPI200_IT"}, "KOSDAQ": {"KOSDAQ"}}
    consistent = [
        str(symbol) in allowed_symbols[str(market)]
        for symbol, market in zip(dataframe["symbol"], dataframe["market"])
    ]
    if not all(consistent):
        raise DatasetValidationError("symbol and market are inconsistent")
    if dataframe.duplicated(list(KR_INDEX_DAILY.primary_key)).any():
        raise DatasetValidationError("date+symbol contains duplicates")

    numeric_columns = list(KR_INDEX_DAILY.column_names[3:10])
    numeric = dataframe[numeric_columns].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise DatasetValidationError("numeric columns must not contain missing values")
    if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise DatasetValidationError("numeric columns contain infinity")
    if (numeric < 0).any().any():
        raise DatasetValidationError("price, volume, value, and market cap must be nonnegative")
    source_zero = (
        numeric["open"].eq(0)
        & numeric["high"].eq(0)
        & numeric["low"].eq(0)
        & numeric["close"].ge(0)
    )
    invalid_ohlc = ~source_zero & (
        (numeric["high"] < numeric["low"])
        | ~numeric["open"].between(numeric["low"], numeric["high"])
        | ~numeric["close"].between(numeric["low"], numeric["high"])
    )
    if invalid_ohlc.any():
        raise DatasetValidationError("OHLC relationship is invalid")

    order = dataframe.sort_values(list(KR_INDEX_DAILY.sort_key), kind="stable").index
    if not order.equals(dataframe.index):
        raise DatasetValidationError("rows must be sorted by date and symbol")
