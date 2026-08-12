from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from typing import Callable

import pandas as pd
import pyarrow.parquet as pq

from stock_data.contracts.base import DatasetContract
from stock_data.storage.contract_arrow import (
    dataframe_to_contract_table,
    restore_contract_dates,
)


Validator = Callable[[pd.DataFrame], None]


def _restore(dataframe: pd.DataFrame, contract: DatasetContract, validator: Validator) -> pd.DataFrame:
    restored = dataframe[list(contract.column_names)].copy()
    restored = restore_contract_dates(restored, contract)
    restored = restored.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    validator(restored)
    return restored


def read_partitioned(root: Path, contract: DatasetContract, validator: Validator) -> pd.DataFrame:
    paths = sorted(root.rglob("data.parquet"))
    if not paths:
        raise FileNotFoundError(f"{contract.name} partitions not found: {root}")
    frames = [pd.read_parquet(path, engine="pyarrow") for path in paths]
    return _restore(pd.concat(frames, ignore_index=True), contract, validator)


def _target(root: Path, market: str, year: int | None) -> Path:
    path = root / f"market={market}"
    if year is not None:
        path /= f"year={year}"
    return path / "data.parquet"


def write_partitioned_atomic(
    dataframe: pd.DataFrame,
    root: Path,
    contract: DatasetContract,
    validator: Validator,
) -> None:
    validator(dataframe)
    working = dataframe.copy()
    has_year = "year" in contract.partition_by
    if has_year:
        working["_year"] = pd.to_datetime(working["date"], errors="raise").dt.year
    group_columns = ["market", *( ["_year"] if has_year else [])]
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    committed: list[Path] = []
    try:
        for key, partition in working.groupby(group_columns, sort=True):
            values = key if isinstance(key, tuple) else (key,)
            market = str(values[0])
            year = int(values[1]) if has_year else None
            target = _target(root, market, year)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                suffix=".parquet.tmp", prefix=f"{contract.name}_", dir=target.parent, delete=False
            ) as temporary:
                staged[target] = Path(temporary.name)
            stored = partition.drop(columns="_year", errors="ignore").copy()
            table = dataframe_to_contract_table(stored, contract)
            pq.write_table(table, staged[target])
            verified = _restore(pd.read_parquet(staged[target], engine="pyarrow"), contract, validator)
            expected = _restore(table.to_pandas(), contract, validator)
            if not verified.equals(expected):
                raise ValueError(f"temporary {contract.name} partition differs from input")
        for target in staged:
            if target.exists():
                with tempfile.NamedTemporaryFile(
                    suffix=".parquet.bak", prefix=f"{contract.name}_", dir=target.parent, delete=False
                ) as backup:
                    backups[target] = Path(backup.name)
                shutil.copy2(target, backups[target])
            else:
                backups[target] = None
        for target, temporary in staged.items():
            temporary.replace(target)
            committed.append(target)
    except Exception:
        for target in reversed(committed):
            backup = backups.get(target)
            if backup is None:
                target.unlink(missing_ok=True)
            elif backup.exists():
                backup.replace(target)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        for backup in backups.values():
            if backup is not None:
                backup.unlink(missing_ok=True)

