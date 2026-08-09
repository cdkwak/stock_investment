import numpy as np
import pandas as pd


class DataV1ValidationError(ValueError):
    pass


def validate_data_v1(dataframe: pd.DataFrame, contract, *, allow_empty: bool = True) -> None:
    if tuple(dataframe.columns) != contract.column_names:
        raise DataV1ValidationError(f"{contract.name}: schema mismatch")
    if dataframe.empty:
        if allow_empty:
            return
        raise DataV1ValidationError(f"{contract.name}: empty")
    if dataframe[list(contract.primary_key)].isna().any().any():
        raise DataV1ValidationError(f"{contract.name}: null primary key")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise DataV1ValidationError(f"{contract.name}: duplicate primary key")
    dates = pd.to_datetime(dataframe["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        raise DataV1ValidationError(f"{contract.name}: invalid date")
    numeric = dataframe.select_dtypes(include="number")
    if numeric.isna().any().any() or np.isinf(numeric.to_numpy(dtype=float)).any():
        raise DataV1ValidationError(f"{contract.name}: NaN or infinity")
    signed_price_fields = {"open", "high", "low", "close", "spot_price", "settlement_price",
                           "next_day_base_price"}
    nonnegative = numeric[[column for column in numeric.columns if column not in signed_price_fields]]
    if (nonnegative < 0).any().any():
        raise DataV1ValidationError(f"{contract.name}: negative source value")
    if {"open", "high", "low", "close"}.issubset(dataframe.columns):
        active = dataframe[["open", "high", "low", "close"]].ne(0).any(axis=1)
        bad = active & ((dataframe["high"] < dataframe[["open", "close", "low"]].max(axis=1)) |
                        (dataframe["low"] > dataframe[["open", "close", "high"]].min(axis=1)))
        if bad.any():
            raise DataV1ValidationError(f"{contract.name}: OHLC")
    ordered = dataframe.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    if not dataframe.reset_index(drop=True).equals(ordered):
        raise DataV1ValidationError(f"{contract.name}: sort order")
