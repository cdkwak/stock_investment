from datetime import date
from pathlib import Path
import socket
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_data.orchestration.dataset_universe import probe_retained_coverage


@pytest.fixture
def tmp_path() -> Path:
    """Avoid Python 3.13's Windows 0700 pytest temporary ACL."""

    root = (
        Path(__file__).parents[3]
        / ".tmp/agents/health-retained-coverage-20260905/fixtures"
        / uuid4().hex
    )
    root.mkdir(parents=True)
    return root


def _write_dates(path: Path, values: list[date]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"date": pa.array(values, type=pa.date32()), "value": values}),
        path,
    )


def test_probe_retained_coverage_reads_synthetic_hive_parquet_footers(
    tmp_path, monkeypatch,
) -> None:
    root = tmp_path / "data/normalized/synthetic_daily"
    _write_dates(
        root / "symbol=AAA/year=2024/data.parquet",
        [date(2024, 1, 2), date(2024, 12, 30)],
    )
    _write_dates(
        root / "symbol=BBB/year=2026/data.parquet",
        [date(2026, 9, 3), date(2026, 9, 5)],
    )

    def network_forbidden(*_args, **_kwargs):
        raise AssertionError("coverage probing must not use the network")

    monkeypatch.setattr(socket, "socket", network_forbidden)

    assert probe_retained_coverage(tmp_path, "synthetic_daily") == (
        "2024-01-02", "2026-09-05",
    )


def test_probe_retained_coverage_prefers_date_partition_names(tmp_path) -> None:
    root = tmp_path / "data/normalized/partitioned_daily"
    _write_dates(root / "date=2026-09-04/data.parquet", [date(2001, 1, 1)])
    _write_dates(root / "date=2026-09-05/data.parquet", [date(2001, 1, 1)])

    assert probe_retained_coverage(tmp_path, "partitioned_daily") == (
        "2026-09-04", "2026-09-05",
    )


def test_probe_retained_coverage_returns_none_without_retained_parquet(
    tmp_path,
) -> None:
    assert probe_retained_coverage(tmp_path, "missing_daily") is None


def test_probe_cache_invalidates_when_partition_directory_mtime_changes(
    tmp_path,
) -> None:
    root = tmp_path / "data/normalized/cache_daily"
    _write_dates(root / "year=2025/data.parquet", [date(2025, 12, 31)])
    assert probe_retained_coverage(tmp_path, "cache_daily") == (
        "2025-12-31", "2025-12-31",
    )

    _write_dates(root / "year=2026/data.parquet", [date(2026, 9, 5)])
    assert probe_retained_coverage(tmp_path, "cache_daily") == (
        "2025-12-31", "2026-09-05",
    )
