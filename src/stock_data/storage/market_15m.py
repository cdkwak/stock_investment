from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_data.contracts.market_15m import MARKET_PRICE_15M_OBSERVATION
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.market_15m import validate_market_price_15m


VALUE_COLUMNS = ("open", "high", "low", "close", "volume")


def read_market_15m(root: Path) -> pd.DataFrame:
    return read_dataset(root, MARKET_PRICE_15M_OBSERVATION, validate_market_price_15m)


def merge_market_15m_exact(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    validate_market_price_15m(incoming)
    if existing.empty:
        result = incoming.copy()
    else:
        validate_market_price_15m(existing)
        keys = list(MARKET_PRICE_15M_OBSERVATION.primary_key)
        overlap = existing.merge(incoming, on=keys, how="inner", suffixes=("_old", "_new"))
        for column in VALUE_COLUMNS:
            old = pd.to_numeric(overlap[f"{column}_old"], errors="coerce")
            new = pd.to_numeric(overlap[f"{column}_new"], errors="coerce")
            if not (old.eq(new) | (old.isna() & new.isna())).all():
                raise ValueError(f"retained 15m overlap differs: {column}")
        result = pd.concat([existing, incoming], ignore_index=True).drop_duplicates(
            keys, keep="first"
        )
    result = result.sort_values(
        list(MARKET_PRICE_15M_OBSERVATION.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_market_price_15m(result)
    return result


def write_market_15m_atomic(dataframe: pd.DataFrame, root: Path) -> None:
    write_dataset_atomic(
        dataframe, root, MARKET_PRICE_15M_OBSERVATION, validate_market_price_15m
    )


__all__ = ["merge_market_15m_exact", "read_market_15m", "write_market_15m_atomic"]
