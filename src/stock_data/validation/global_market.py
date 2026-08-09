import numpy as np
import pandas as pd


def validate_global_index(dataframe: pd.DataFrame) -> None:
    required = ["date","symbol","source_ticker","open","high","low","close","volume"]
    if list(dataframe.columns) != required or dataframe.empty:
        raise ValueError("global index schema invalid or empty")
    if dataframe.duplicated(["date","symbol"]).any():
        raise ValueError("duplicate global index key")
    numeric = dataframe[["open","high","low","close","volume"]].apply(pd.to_numeric,errors="coerce")
    prices = numeric[["open","high","low","close"]]
    if prices.isna().any().any() or not np.isfinite(prices.to_numpy()).all():
        raise ValueError("invalid global index price")
    if ((prices.high<prices.low)|~prices.open.between(prices.low,prices.high)|~prices.close.between(prices.low,prices.high)).any():
        raise ValueError("invalid global OHLC")


def validate_fred(dataframe: pd.DataFrame) -> None:
    if dataframe.empty or "date" not in dataframe or dataframe["date"].duplicated().any():
        raise ValueError("invalid FRED dataset")
    values = dataframe.drop(columns="date").apply(pd.to_numeric,errors="coerce")
    finite = values.to_numpy(dtype="float64")
    if not np.isfinite(finite[~np.isnan(finite)]).all():
        raise ValueError("invalid FRED values")
