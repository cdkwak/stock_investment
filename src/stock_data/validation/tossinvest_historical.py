from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


KST = ZoneInfo("Asia/Seoul")
ALLOWED_MARKETS = {"KOSPI", "KOSDAQ"}
ALLOWED_TREASURY = {f"KR_BOND_{tenor}Y" for tenor in (2, 3, 5, 10, 20, 30)}


def validate_toss_historical(dataframe: pd.DataFrame, contract) -> None:
    if dataframe.empty:
        raise ValueError(f"{contract.name} is empty")
    if list(dataframe.columns) != list(contract.column_names):
        raise ValueError(f"{contract.name} schema does not match its contract")
    for column in contract.columns:
        if not column.nullable and dataframe[column.name].isna().any():
            raise ValueError(f"non-null column contains null: {column.name}")
    for name in ("date", "source_date"):
        parsed = pd.to_datetime(dataframe[name], format="%Y-%m-%d", errors="coerce")
        if parsed.isna().any():
            raise ValueError(f"invalid {name}")
    availability = pd.to_datetime(
        dataframe["availability_date"], format="%Y-%m-%d", errors="coerce"
    )
    invalid_availability = dataframe["availability_date"].notna() & availability.isna()
    if invalid_availability.any():
        raise ValueError("invalid availability_date")
    if not dataframe["source"].eq("tossinvest_open_api").all():
        raise ValueError("invalid Toss provenance")
    if not dataframe["source_date"].eq(dataframe["date"]).all():
        raise ValueError("source_date must equal the source record date")
    collected = pd.to_datetime(dataframe["collected_at"], utc=True, errors="coerce")
    if collected.isna().any():
        raise ValueError("invalid collected_at")
    updated = pd.to_datetime(dataframe["updated_at"], utc=True, errors="coerce")
    has_updated = dataframe["updated_at"].notna()
    if updated[has_updated].isna().any():
        raise ValueError("invalid updated_at")
    if dataframe.loc[~has_updated, "availability_date"].notna().any():
        raise ValueError("availability_date requires source updated_at")
    if dataframe.loc[has_updated, "availability_date"].isna().any():
        raise ValueError("availability_date is required with source updated_at")
    if has_updated.any():
        expected = updated[has_updated].dt.tz_convert(KST).dt.strftime("%Y-%m-%d")
        actual = dataframe.loc[has_updated, "availability_date"].astype(str)
        if not expected.reset_index(drop=True).equals(actual.reset_index(drop=True)):
            raise ValueError("availability_date differs from updated_at")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise ValueError("duplicate primary key")
    expected_order = dataframe.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    if not dataframe.reset_index(drop=True).equals(expected_order):
        raise ValueError("rows are not sorted")

    if "symbol" in dataframe and not dataframe["symbol"].astype(str).str.fullmatch(r"\d{6}").all():
        raise ValueError("invalid Korean stock symbol")
    if "market" in dataframe and not set(dataframe["market"].dropna().astype(str)) <= ALLOWED_MARKETS:
        raise ValueError("invalid Korean market")
    if "instrument" in dataframe and not set(dataframe["instrument"].astype(str)) <= ALLOWED_TREASURY:
        raise ValueError("invalid Korean treasury instrument")

    identity = {
        "date", "market", "symbol", "instrument", "source", "source_operation",
        "source_date", "collected_at", "updated_at", "availability_date",
    }
    numeric = [name for name in contract.column_names if name not in identity]
    for name in numeric:
        values = pd.to_numeric(dataframe[name], errors="coerce")
        invalid = dataframe[name].notna() & values.isna()
        if invalid.any() or np.isinf(values.dropna().astype(float)).any():
            raise ValueError(f"invalid numeric values in {name}")
    if contract.name == "kr_treasury_yield_daily":
        required = dataframe[["open", "high", "low", "close"]]
        if required.isna().any().any():
            raise ValueError("treasury OHLC is missing")
        invalid_ohlc = (
            (dataframe["high"] < dataframe[["open", "close", "low"]].max(axis=1))
            | (dataframe["low"] > dataframe[["open", "close", "high"]].min(axis=1))
        )
        if invalid_ohlc.any():
            raise ValueError("invalid treasury OHLC")
    if contract.name == "kr_market_investor_trading_daily":
        groups = ("individual", "foreigner", "institution", "other_corporation")
        for side in ("buy", "sell"):
            detail = [
                f"institution_{name}_{side}_amount"
                for name in (
                    "financial_investment", "insurance", "trust",
                    "private_equity_fund", "bank",
                    "other_financial_institution", "pension_fund",
                )
            ]
            known = dataframe[[f"institution_{side}_amount", *detail]].notna().all(axis=1)
            if known.any() and not dataframe.loc[known, f"institution_{side}_amount"].eq(
                dataframe.loc[known, detail].sum(axis=1)
            ).all():
                raise ValueError("institution breakdown does not equal its total")
        buy = [f"{name}_buy_amount" for name in groups]
        sell = [f"{name}_sell_amount" for name in groups]
        known = dataframe[[*buy, *sell]].notna().all(axis=1)
        if known.any() and not dataframe.loc[known, buy].sum(axis=1).eq(
            dataframe.loc[known, sell].sum(axis=1)
        ).all():
            raise ValueError("market investor buy and sell totals differ")
