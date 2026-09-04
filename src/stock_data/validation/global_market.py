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


def validate_global_etf(dataframe: pd.DataFrame) -> None:
    required = [
        "date", "symbol", "source_ticker", "open", "high", "low", "close",
        "adjusted_close", "volume", "currency", "exchange", "provider",
        "retrieved_at", "adjustment_status",
    ]
    if list(dataframe.columns) != required or dataframe.empty:
        raise ValueError("global ETF schema invalid or empty")
    if dataframe.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate global ETF key")
    identity = dataframe[[
        "date", "symbol", "source_ticker", "currency", "exchange", "provider",
        "retrieved_at", "adjustment_status",
    ]]
    if identity.isna().any().any():
        raise ValueError("global ETF identity/provenance is null")
    if not dataframe["provider"].eq("yahoo_chart_api").all():
        raise ValueError("global ETF provider differs")
    if not dataframe["adjustment_status"].eq("SOURCE_ADJUSTED_CLOSE_RETAINED_SEPARATELY").all():
        raise ValueError("global ETF adjustment semantics differ")
    timestamps = pd.to_datetime(dataframe["retrieved_at"], errors="coerce", utc=True)
    if timestamps.isna().any():
        raise ValueError("global ETF retrieval timestamp is invalid")
    numeric = dataframe[["open", "high", "low", "close", "adjusted_close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    )
    prices = numeric[["open", "high", "low", "close", "adjusted_close"]]
    if prices.isna().any().any() or not np.isfinite(prices.to_numpy()).all():
        raise ValueError("invalid global ETF price")
    if ((numeric.high < numeric.low)
            | ~numeric.open.between(numeric.low, numeric.high)
            | ~numeric.close.between(numeric.low, numeric.high)).any():
        raise ValueError("invalid global ETF OHLC")
    if (numeric.volume.dropna() < 0).any():
        raise ValueError("invalid global ETF volume")


def validate_global_equity(dataframe: pd.DataFrame) -> None:
    required = [
        "date", "symbol", "source_ticker", "open", "high", "low", "close",
        "adjusted_close", "volume", "currency", "exchange", "provider",
        "retrieved_at", "adjustment_status",
    ]
    if list(dataframe.columns) != required or dataframe.empty:
        raise ValueError("global equity schema invalid or empty")
    if dataframe.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate global equity key")
    identity = dataframe[[
        "date", "symbol", "source_ticker", "currency", "exchange", "provider",
        "retrieved_at", "adjustment_status",
    ]]
    if identity.isna().any().any():
        raise ValueError("global equity identity/provenance is null")
    if not dataframe["provider"].eq("yahoo_chart_api").all():
        raise ValueError("global equity provider differs")
    if not dataframe["adjustment_status"].eq(
        "SOURCE_ADJUSTED_CLOSE_RETAINED_SEPARATELY"
    ).all():
        raise ValueError("global equity adjustment semantics differ")
    timestamps = pd.to_datetime(dataframe["retrieved_at"], errors="coerce", utc=True)
    if timestamps.isna().any():
        raise ValueError("global equity retrieval timestamp is invalid")
    numeric = dataframe[["open", "high", "low", "close", "adjusted_close", "volume"]].apply(
        pd.to_numeric, errors="coerce"
    )
    prices = numeric[["open", "high", "low", "close", "adjusted_close"]]
    if prices.isna().any().any() or not np.isfinite(prices.to_numpy()).all():
        raise ValueError("invalid global equity price")
    if ((numeric.high < numeric.low)
            | ~numeric.open.between(numeric.low, numeric.high)
            | ~numeric.close.between(numeric.low, numeric.high)).any():
        raise ValueError("invalid global equity OHLC")
    if (numeric.volume.dropna() < 0).any():
        raise ValueError("invalid global equity volume")


def validate_global_commodity_futures(dataframe: pd.DataFrame) -> None:
    required = ["date", "symbol", "source_ticker", "asset", "open", "high", "low", "close", "volume", "ohlc_status"]
    if list(dataframe.columns) != required or dataframe.empty:
        raise ValueError("commodity futures schema invalid or empty")
    if dataframe.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate commodity futures key")
    if dataframe[["date", "symbol", "source_ticker", "asset", "ohlc_status"]].isna().any().any():
        raise ValueError("commodity futures identity/status is null")
    numeric = dataframe[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    finite = numeric.to_numpy(dtype="float64")
    if not np.isfinite(finite[~np.isnan(finite)]).all() or (numeric.volume.dropna() < 0).any():
        raise ValueError("invalid commodity futures numeric value")
    prices = numeric[["open", "high", "low", "close"]]
    complete = prices.notna().all(axis=1)
    missing = ~prices.notna().any(axis=1)
    relation = complete & (prices.high.ge(prices.low) & prices.open.between(prices.low, prices.high) & prices.close.between(prices.low, prices.high))
    expected = pd.Series("PARTIAL_MISSING", index=dataframe.index)
    expected.loc[missing] = "ALL_MISSING"
    expected.loc[complete & ~relation] = "SOURCE_RELATION_ANOMALY"
    expected.loc[relation] = "VALID"
    if not dataframe.ohlc_status.astype(str).equals(expected.astype(str)):
        raise ValueError("commodity futures OHLC status mismatch")


def validate_fred(dataframe: pd.DataFrame) -> None:
    if dataframe.empty or "date" not in dataframe or dataframe["date"].duplicated().any():
        raise ValueError("invalid FRED dataset")
    values = dataframe.drop(columns="date").apply(pd.to_numeric,errors="coerce")
    finite = values.to_numpy(dtype="float64")
    if not np.isfinite(finite[~np.isnan(finite)]).all():
        raise ValueError("invalid FRED values")
