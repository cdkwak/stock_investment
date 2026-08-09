from pathlib import Path
import shutil
import tempfile
from typing import Callable

import pandas as pd


def read_dataset(root: Path, contract, validator: Callable[[pd.DataFrame], None]) -> pd.DataFrame:
    paths = sorted(root.rglob("data.parquet"))
    if not paths:
        raise FileNotFoundError(root)
    result = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    if "date" in result:
        result["date"] = pd.to_datetime(result["date"], errors="raise").dt.strftime("%Y-%m-%d")
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
            working["_year"] = pd.to_datetime(working["date"], errors="raise").dt.year
            group_columns.append("_year")
        else:
            group_columns.append(column)
    staged = {}
    backups = {}
    committed = []
    try:
        for key, partition in working.groupby(group_columns, sort=True):
            values = key if isinstance(key, tuple) else (key,)
            target_dir = root
            for name, value in zip(contract.partition_by, values):
                target_dir /= f"{name}={value}"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "data.parquet"
            with tempfile.NamedTemporaryFile(
                suffix=".parquet.tmp", prefix=contract.name + "_", dir=target_dir, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
            stored = partition.drop(columns="_year", errors="ignore").copy()
            if "date" in stored:
                stored["date"] = pd.to_datetime(stored["date"], errors="raise").dt.date
            stored.to_parquet(temporary_path, index=False)
            verified = pd.read_parquet(temporary_path)
            if "date" in verified:
                verified["date"] = pd.to_datetime(verified["date"]).dt.strftime("%Y-%m-%d")
            verified = verified[list(contract.column_names)].reset_index(drop=True)
            validator(verified)
            staged[target] = temporary_path
        for target in staged:
            if target.exists():
                with tempfile.NamedTemporaryFile(
                    suffix=".parquet.bak", prefix=contract.name + "_",
                    dir=target.parent, delete=False,
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
