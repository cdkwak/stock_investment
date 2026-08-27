from pathlib import Path
import threading

import pandas as pd
import pytest

from stock_data.contracts.kr_index_daily import KR_INDEX_DAILY
from stock_data.storage import atomic_parquet, partition_generation


def normalized_rows(days: tuple[str, ...], *, close_offset: float = 0) -> pd.DataFrame:
    records = []
    for day in days:
        for market, base in (("KOSDAQ", 900.0), ("KOSPI", 3000.0)):
            records.append([
                day, market, market, base, base + 20, base - 10,
                base + 10 + close_offset, 10, 100, 1000, "pykrx",
            ])
    return pd.DataFrame(records, columns=KR_INDEX_DAILY.column_names)


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


def test_clean_generation_read_preserves_exact_root_inventory(tmp_path: Path) -> None:
    root = tmp_path / "kr_index_daily"
    atomic_parquet.write_kr_index_daily_atomic(
        normalized_rows(("2025-12-31", "2026-01-02")), root,
    )

    def inventory() -> tuple[tuple[str, bytes | None], ...]:
        return tuple(
            (path.relative_to(root).as_posix(), path.read_bytes() if path.is_file() else None)
            for path in sorted(root.rglob("*"))
        )

    before = inventory()
    assert not any(name.startswith(".partition-generation") for name, _ in before)
    assert atomic_parquet.read_kr_index_daily(root).shape == (4, 11)
    assert inventory() == before


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


def test_interrupted_second_partition_recovers_previous_generation_on_read(
    monkeypatch, tmp_path: Path,
) -> None:
    root = tmp_path / "kr_index_daily"
    previous = normalized_rows(("2025-12-31", "2026-01-02"))
    candidate = normalized_rows(("2025-12-31", "2026-01-02"), close_offset=7)
    atomic_parquet.write_kr_index_daily_atomic(previous, root)
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
        atomic_parquet.write_kr_index_daily_atomic(candidate, root)
    assert (root / ".partition-generation.json").is_file()

    monkeypatch.undo()
    pd.testing.assert_frame_equal(
        atomic_parquet.read_kr_index_daily(root),
        previous.sort_values(list(KR_INDEX_DAILY.sort_key)).reset_index(drop=True),
    )
    assert not (root / ".partition-generation.json").exists()
    assert not (root / ".partition-generation").exists()

    atomic_parquet.write_kr_index_daily_atomic(candidate, root)
    pd.testing.assert_frame_equal(
        atomic_parquet.read_kr_index_daily(root),
        candidate.sort_values(list(KR_INDEX_DAILY.sort_key)).reset_index(drop=True),
    )
    assert not (root / ".partition-generation.json").exists()
    assert not (root / ".partition-generation").exists()


def test_absent_root_reader_blocks_first_generation_promotion(
    monkeypatch, tmp_path: Path,
) -> None:
    root = tmp_path / "kr_index_daily"
    original_replace = Path.replace
    first_partition_promoted = threading.Event()
    writer_attempted_lock = threading.Event()
    writer_finished = threading.Event()
    writer_errors: list[BaseException] = []
    reader_thread = threading.current_thread()

    def observe_first_partition(self, target_path):
        result = original_replace(self, target_path)
        if self.name.endswith(".parquet.tmp"):
            first_partition_promoted.set()
        return result

    def observe_lock_attempt() -> None:
        if threading.current_thread() is not reader_thread:
            writer_attempted_lock.set()

    def write_first_generation() -> None:
        try:
            atomic_parquet.write_kr_index_daily_atomic(
                normalized_rows(("2025-12-31", "2026-01-02")), root,
            )
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_finished.set()

    monkeypatch.setattr(Path, "replace", observe_first_partition)
    monkeypatch.setattr(
        partition_generation, "_generation_lock_attempt", observe_lock_attempt,
    )
    with partition_generation.readable_generation(root):
        assert not root.exists()
        writer = threading.Thread(target=write_first_generation, daemon=True)
        writer.start()
        assert writer_attempted_lock.wait(timeout=2)
        assert not first_partition_promoted.wait(timeout=0.2)
        assert not writer_finished.is_set()

    writer.join(timeout=5)
    assert not writer.is_alive()
    assert writer_errors == []
    assert first_partition_promoted.is_set()
    assert atomic_parquet.read_kr_index_daily(root).shape == (4, 11)
