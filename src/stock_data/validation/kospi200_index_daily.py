from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.kospi200_index_daily import KR_KOSPI200_INDEX_DAILY


class KOSPI200IndexValidationError(ValueError):
    pass


def validate_kospi200_index_daily(frame: pd.DataFrame) -> None:
    if list(frame.columns) != list(KR_KOSPI200_INDEX_DAILY.column_names) or frame.empty:
        raise KOSPI200IndexValidationError("KOSPI200 index schema or content is invalid")
    dates = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.strftime("%Y-%m-%d").equals(frame["date"].astype(str)):
        raise KOSPI200IndexValidationError("date must be valid YYYY-MM-DD")
    expected = {
        "symbol": "KOSPI200", "ticker": "1028", "source": "pykrx",
        "source_operation": "get_index_ohlcv",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
    }
    for column, value in expected.items():
        if not frame[column].astype(str).eq(value).all():
            raise KOSPI200IndexValidationError(f"{column} identity differs")
    if frame.duplicated(["date"]).any() or not dates.is_monotonic_increasing:
        raise KOSPI200IndexValidationError("date key is duplicate or unsorted")
    numeric_columns = ["open", "high", "low", "close", "volume", "trading_value", "market_cap"]
    numeric = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise KOSPI200IndexValidationError("numeric value is null or non-finite")
    if (numeric < 0).any().any():
        raise KOSPI200IndexValidationError("numeric value is negative")
    source_zero = (
        numeric["open"].eq(0) & numeric["high"].eq(0)
        & numeric["low"].eq(0) & numeric["close"].ge(0)
    )
    source_anomaly = (
        numeric["open"].eq(0) & numeric["high"].eq(0)
        & numeric["low"].gt(0) & numeric["close"].lt(numeric["low"])
    )
    expected_status = pd.Series("VALID", index=frame.index)
    expected_status.loc[source_anomaly] = "SOURCE_ANOMALY_OPEN_HIGH_ZERO_CLOSE_BELOW_LOW"
    if not frame["ohlc_status"].astype(str).eq(expected_status).all():
        raise KOSPI200IndexValidationError("ohlc_status differs from preserved source values")
    invalid = ~source_zero & ~source_anomaly & (
        (numeric["high"] < numeric["low"])
        | ~numeric["open"].between(numeric["low"], numeric["high"])
        | ~numeric["close"].between(numeric["low"], numeric["high"])
    )
    if invalid.any():
        raise KOSPI200IndexValidationError("OHLC relationship is invalid")
