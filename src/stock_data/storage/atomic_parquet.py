from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_data.contracts.kr_index_daily import KR_INDEX_DAILY
from stock_data.storage.partition_generation import (
    readable_generation,
    writable_generation,
)
from stock_data.validation.kr_index_daily import validate_kr_index_daily


PART_FILE_NAME = "data.parquet"


def _from_storage(dataframe: pd.DataFrame) -> pd.DataFrame:
    restored = dataframe[list(KR_INDEX_DAILY.column_names)].copy()
    restored["date"] = pd.to_datetime(restored["date"], errors="raise").dt.strftime("%Y-%m-%d")
    restored = restored.sort_values(list(KR_INDEX_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_kr_index_daily(restored)
    return restored


def read_kr_index_daily(root: Path) -> pd.DataFrame:
    with readable_generation(root):
        paths = sorted(root.glob("market=*/year=*/data.parquet"))
        if not paths:
            raise FileNotFoundError(f"kr_index_daily Parquet partitions not found: {root}")
        return _from_storage(
            pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
        )


def _partition_path(root: Path, market: str, year: int) -> Path:
    return root / f"market={market}" / f"year={year}" / PART_FILE_NAME


def write_kr_index_daily_atomic(dataframe: pd.DataFrame, root: Path) -> None:
    validate_kr_index_daily(dataframe)
    with writable_generation(root, "kr_index_daily") as generation:
        working = dataframe.copy()
        working["year"] = pd.to_datetime(working["date"], errors="raise").dt.year
        for (market, year), partition in working.groupby(["market", "year"], sort=True):
            target = _partition_path(root, str(market), int(year))
            staged = generation.stage_path(target)
            stored = partition.drop(columns="year").copy()
            stored["date"] = pd.to_datetime(stored["date"], errors="raise").dt.date
            stored.to_parquet(staged, index=False, engine="pyarrow")
            verified = _from_storage(pd.read_parquet(staged, engine="pyarrow"))
            expected = partition.drop(columns="year").reset_index(drop=True)
            expected["date"] = expected["date"].astype(str)
            if not verified.equals(expected):
                raise ValueError("temporary Parquet content differs from input partition")
        generation.publish()
