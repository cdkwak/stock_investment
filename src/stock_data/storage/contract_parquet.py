from pathlib import Path
from typing import Callable

import pandas as pd
import pyarrow.parquet as pq

from stock_data.storage.contract_arrow import (
    dataframe_to_contract_table,
    restore_contract_dates,
)
from stock_data.storage.partition_generation import (
    readable_generation,
    writable_generation,
)


def _partition_date_column(contract) -> str:
    columns = set(contract.column_names)
    for candidate in ("date", "market_date", "source_snapshot_date"):
        if candidate in columns:
            return candidate
    raise ValueError(f"{contract.name}: year partition requires a date column")


def read_dataset(root: Path, contract, validator: Callable[[pd.DataFrame], None]) -> pd.DataFrame:
    with readable_generation(root):
        paths = sorted(root.rglob("data.parquet"))
        if not paths:
            raise FileNotFoundError(root)
        result = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
        result = restore_contract_dates(result, contract)
        result = result[list(contract.column_names)].sort_values(
            list(contract.sort_key), kind="stable"
        ).reset_index(drop=True)
        validator(result)
        return result


def write_dataset_atomic(dataframe: pd.DataFrame, root: Path, contract, validator) -> None:
    validator(dataframe)
    working = dataframe.copy()
    group_columns = []
    for column in contract.partition_by:
        if column == "year":
            date_column = _partition_date_column(contract)
            working["_year"] = pd.to_datetime(working[date_column], errors="raise").dt.year
            group_columns.append("_year")
        else:
            group_columns.append(column)
    with writable_generation(root, contract.name) as generation:
        for key, partition in working.groupby(group_columns, sort=True):
            values = key if isinstance(key, tuple) else (key,)
            target_dir = root
            for name, value in zip(contract.partition_by, values):
                target_dir /= f"{name}={value}"
            target = target_dir / "data.parquet"
            temporary_path = generation.stage_path(target)
            stored = partition.drop(columns="_year", errors="ignore").copy()
            pq.write_table(dataframe_to_contract_table(stored, contract), temporary_path)
            verified = pd.read_parquet(temporary_path)
            verified = restore_contract_dates(verified, contract)
            verified = verified[list(contract.column_names)].reset_index(drop=True)
            validator(verified)
        generation.publish()
