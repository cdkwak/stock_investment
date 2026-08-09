from pathlib import Path

import pandas as pd
import pytest

from stock_data.storage import atomic_parquet
from test_pykrx_kr_index_collector import normalized_rows


def test_atomic_write_uses_market_year_partitions(tmp_path: Path) -> None:
    root = tmp_path / "kr_index_daily"
    atomic_parquet.write_kr_index_daily_atomic(
        normalized_rows(("2025-12-31", "2026-01-02")), root
    )
    paths = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.parquet"))
    assert paths == [
        "market=KOSDAQ/year=2025/data.parquet",
        "market=KOSDAQ/year=2026/data.parquet",
        "market=KOSPI/year=2025/data.parquet",
        "market=KOSPI/year=2026/data.parquet",
    ]
    assert atomic_parquet.read_kr_index_daily(root).shape == (4, 11)
    assert not list(root.rglob("*.tmp"))


def test_parquet_stores_date_as_date32(tmp_path: Path) -> None:
    root = tmp_path / "kr_index_daily"
    atomic_parquet.write_kr_index_daily_atomic(normalized_rows(("2026-08-03",)), root)
    stored = pd.read_parquet(root / "market=KOSPI/year=2026/data.parquet")
    assert str(stored["date"].dtype) == "object"
    assert type(stored.loc[0, "date"]).__name__ == "date"


def test_read_back_failure_preserves_existing_partitions(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "kr_index_daily"
    atomic_parquet.write_kr_index_daily_atomic(normalized_rows(("2026-08-03",)), root)
    before = {path: path.read_bytes() for path in root.rglob("*.parquet")}
    monkeypatch.setattr(
        atomic_parquet,
        "_from_storage",
        lambda dataframe: (_ for _ in ()).throw(ValueError("simulated validation failure")),
    )
    with pytest.raises(ValueError, match="simulated"):
        atomic_parquet.write_kr_index_daily_atomic(
            normalized_rows(("2026-08-03",), close_offset=1), root
        )
    assert {path: path.read_bytes() for path in root.rglob("*.parquet")} == before
    assert not list(root.rglob("*.tmp"))

