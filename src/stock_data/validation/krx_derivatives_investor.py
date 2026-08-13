from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.krx_derivatives_investor import (
    KR_KOSPI200_FUTURES_INVESTOR_TRADING_DAILY,
    KR_KOSPI200_OPTIONS_INVESTOR_TRADING_DAILY,
)


def validate_derivatives_investor(dataframe: pd.DataFrame, contract) -> None:
    if contract not in {
        KR_KOSPI200_FUTURES_INVESTOR_TRADING_DAILY,
        KR_KOSPI200_OPTIONS_INVESTOR_TRADING_DAILY,
    }:
        raise ValueError("unsupported derivatives investor contract")
    if dataframe.empty or list(dataframe.columns) != list(contract.column_names):
        raise ValueError("invalid schema or empty dataset")
    dates = pd.to_datetime(dataframe["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or (dates < pd.Timestamp("1999-04-26")).any():
        raise ValueError("date precedes verified meaningful source coverage")
    expected_product = (
        "KOSPI200_FUTURES"
        if contract is KR_KOSPI200_FUTURES_INVESTOR_TRADING_DAILY
        else "KOSPI200_OPTIONS"
    )
    if not dataframe["product"].eq(expected_product).all():
        raise ValueError("product differs from contract")
    allowed_rights = {"NA"} if expected_product.endswith("FUTURES") else {"ALL", "CALL", "PUT"}
    if not dataframe["option_right"].isin(allowed_rights).all():
        raise ValueError("invalid option-right scope")
    if not dataframe["session"].isin({"ALL", "REGULAR", "NIGHT"}).all():
        raise ValueError("invalid session")
    if dataframe["investor_type_source"].astype(str).str.strip().eq("").any():
        raise ValueError("empty source investor label")
    numeric_columns = [
        "sell_volume", "buy_volume", "net_buy_volume", "sell_trading_value",
        "buy_trading_value", "net_buy_trading_value",
    ]
    numeric = dataframe[numeric_columns].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("invalid numeric value")
    if (numeric[["sell_volume", "buy_volume", "sell_trading_value", "buy_trading_value"]] < 0).any().any():
        raise ValueError("sell/buy values cannot be negative")
    if not np.allclose(numeric["buy_volume"] - numeric["sell_volume"], numeric["net_buy_volume"]):
        raise ValueError("source volume net does not equal buy minus sell")
    if not np.allclose(
        numeric["buy_trading_value"] - numeric["sell_trading_value"],
        numeric["net_buy_trading_value"],
    ):
        raise ValueError("source value net does not equal buy minus sell")
    if dataframe[["volume_unit_source", "trading_value_unit_source"]].astype(str).apply(
        lambda values: values.str.strip().eq("")
    ).any().any():
        raise ValueError("source unit is missing")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise ValueError("duplicate primary key")
    sorted_frame = dataframe.sort_values(list(contract.sort_key), kind="stable")
    if not sorted_frame.index.equals(dataframe.index):
        raise ValueError("rows are not sorted")
