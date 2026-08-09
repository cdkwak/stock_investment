from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

import pandas as pd

from stock_data.contracts.kr_index_daily import KR_INDEX_DAILY
from stock_data.validation.kr_index_daily import validate_kr_index_daily


PART_FILE_NAME = "data.parquet"


def _from_storage(dataframe: pd.DataFrame) -> pd.DataFrame:
    restored = dataframe[list(KR_INDEX_DAILY.column_names)].copy()
    restored["date"] = pd.to_datetime(restored["date"], errors="raise").dt.strftime("%Y-%m-%d")
    restored = restored.sort_values(list(KR_INDEX_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_kr_index_daily(restored)
    return restored


def read_kr_index_daily(root: Path) -> pd.DataFrame:
    paths = sorted(root.glob("market=*/year=*/data.parquet"))
    if not paths:
        raise FileNotFoundError(f"kr_index_daily Parquet partitions not found: {root}")
    return _from_storage(pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True))


def _partition_path(root: Path, market: str, year: int) -> Path:
    return root / f"market={market}" / f"year={year}" / PART_FILE_NAME


def write_kr_index_daily_atomic(dataframe: pd.DataFrame, root: Path) -> None:
    validate_kr_index_daily(dataframe)
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path | None] = {}
    committed: list[Path] = []
    try:
        working = dataframe.copy()
        working["year"] = pd.to_datetime(working["date"], errors="raise").dt.year
        for (market, year), partition in working.groupby(["market", "year"], sort=True):
            target = _partition_path(root, str(market), int(year))
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                suffix=".parquet.tmp", prefix="kr_index_daily_", dir=target.parent, delete=False
            ) as temporary:
                staged[target] = Path(temporary.name)
            stored = partition.drop(columns="year").copy()
            stored["date"] = pd.to_datetime(stored["date"], errors="raise").dt.date
            stored.to_parquet(staged[target], index=False, engine="pyarrow")
            verified = _from_storage(pd.read_parquet(staged[target], engine="pyarrow"))
            expected = partition.drop(columns="year").reset_index(drop=True)
            expected["date"] = expected["date"].astype(str)
            if not verified.equals(expected):
                raise ValueError("temporary Parquet content differs from input partition")

        for target in staged:
            if target.exists():
                with tempfile.NamedTemporaryFile(
                    suffix=".parquet.bak", prefix="kr_index_daily_", dir=target.parent, delete=False
                ) as backup:
                    backup_path = Path(backup.name)
                shutil.copy2(target, backup_path)
                backups[target] = backup_path
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
