from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_data.contracts.base import ColumnContract, DatasetContract
from stock_data.storage import equity_parquet


CONTRACT = DatasetContract(
    name="equity_physical_fixture", version=1, status="active", description="fixture",
    source="fixture", layer="normalized", storage_format="parquet", frequency="daily",
    timezone="Asia/Seoul", primary_key=("date", "market", "symbol"),
    sort_key=("date", "market", "symbol"), partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("optional_count", "int64", True),
        ColumnContract("optional_label", "string", True),
    ),
)


def _frame(value: int = 1) -> pd.DataFrame:
    return pd.DataFrame(
        [["2026-08-07", "KOSPI", "A", value, None]],
        columns=CONTRACT.column_names,
    )


def _two_year_frame(value: int = 1) -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["2025-12-31", "KOSPI", "A", value, None],
            ["2026-01-02", "KOSPI", "A", value, None],
        ],
        columns=CONTRACT.column_names,
    )


def _validate(frame: pd.DataFrame) -> None:
    assert tuple(frame.columns) == CONTRACT.column_names
    assert not frame.empty


def test_equity_storage_enforces_physical_arrow_schema(tmp_path: Path) -> None:
    root = tmp_path / "equity"
    equity_parquet.write_partitioned_atomic(_frame(), root, CONTRACT, _validate)
    schema = pq.ParquetFile(next(root.rglob("data.parquet"))).schema_arrow
    assert schema == pa.schema([
        pa.field("date", pa.date32(), nullable=False),
        pa.field("market", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("optional_count", pa.int64(), nullable=True),
        pa.field("optional_label", pa.string(), nullable=True),
    ])


def test_equity_storage_failure_preserves_existing(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "equity"
    equity_parquet.write_partitioned_atomic(_frame(), root, CONTRACT, _validate)
    target = next(root.rglob("data.parquet"))
    before = target.read_bytes()
    original = Path.replace

    def fail_replace(self, target_path):
        if self.name.endswith(".parquet.tmp"):
            raise OSError("simulated replace failure")
        return original(self, target_path)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        equity_parquet.write_partitioned_atomic(_frame(2), root, CONTRACT, _validate)
    assert target.read_bytes() == before


def test_equity_storage_recovers_whole_generation_after_interruption(
    monkeypatch, tmp_path: Path,
) -> None:
    root = tmp_path / "equity"
    previous = _two_year_frame()
    candidate = _two_year_frame(2)
    equity_parquet.write_partitioned_atomic(previous, root, CONTRACT, _validate)
    original_replace = Path.replace
    promoted = 0

    def interrupt_second_partition(self, target_path):
        nonlocal promoted
        if self.name.endswith(".parquet.tmp"):
            promoted += 1
            if promoted == 2:
                raise KeyboardInterrupt("simulated process interruption")
        return original_replace(self, target_path)

    monkeypatch.setattr(Path, "replace", interrupt_second_partition)
    with pytest.raises(KeyboardInterrupt, match="simulated"):
        equity_parquet.write_partitioned_atomic(candidate, root, CONTRACT, _validate)
    assert (root / ".partition-generation.json").is_file()

    monkeypatch.undo()
    pd.testing.assert_frame_equal(
        equity_parquet.read_partitioned(root, CONTRACT, _validate),
        previous,
    )
    assert not (root / ".partition-generation.json").exists()
    assert not (root / ".partition-generation").exists()

    equity_parquet.write_partitioned_atomic(candidate, root, CONTRACT, _validate)
    pd.testing.assert_frame_equal(
        equity_parquet.read_partitioned(root, CONTRACT, _validate),
        candidate,
    )
    assert not (root / ".partition-generation.json").exists()
    assert not (root / ".partition-generation").exists()
