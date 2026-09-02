from __future__ import annotations

import os
from pathlib import Path
import shutil
from uuid import uuid4

import pandas as pd
import pytest

from stock_data.features.liquidity import liquidity_snapshot


@pytest.fixture
def project_root() -> Path:
    """Create a permissive unique root without pytest's Windows mode-0700 ACL."""
    parent = Path(os.environ["TEMP"])
    root = parent / f"stock-data-liquidity-{uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _write_partition(root: Path, dataset: str, year: int, frame: pd.DataFrame) -> None:
    target = root / "data" / "normalized" / dataset / "market=KOSPI" / f"year={year}"
    target.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target / "data.parquet", index=False)


def test_liquidity_snapshot_uses_common_20_sessions_and_latest_as_of_cap(
    project_root: Path,
):
    dates = pd.bdate_range("2025-12-08", periods=26)
    as_of = dates[22]
    rows = []
    for ordinal, day in enumerate(dates):
        rows.append({"date": day, "symbol": "000001", "trading_value": ordinal * 100})
        if day != dates[3]:
            rows.append({"date": day, "symbol": "000002", "trading_value": 1_000})
    prices = pd.DataFrame(rows)
    for year, frame in prices.groupby(prices["date"].dt.year):
        _write_partition(project_root, "kr_equity_price_daily", int(year), frame)

    caps = pd.DataFrame([
        {"date": dates[21], "symbol": "000001", "market_cap": 10_000},
        {"date": dates[21], "symbol": "000002", "market_cap": 20_000},
        {"date": dates[21], "symbol": "000003", "market_cap": 30_000},
        {"date": dates[24], "symbol": "000001", "market_cap": 99_999},
    ])
    for year, frame in caps.groupby(caps["date"].dt.year):
        _write_partition(project_root, "kr_equity_market_cap_daily", int(year), frame)

    result = liquidity_snapshot(project_root, as_of)

    assert list(result.columns) == ["symbol", "avg_value_20d", "market_cap"]
    assert result["symbol"].tolist() == ["000001", "000002", "000003"]
    expected = prices.loc[
        (prices["symbol"] == "000001") & prices["date"].isin(dates[3:23]),
        "trading_value",
    ].mean()
    assert result.loc[result["symbol"] == "000001", "avg_value_20d"].item() == expected
    assert pd.isna(result.loc[result["symbol"] == "000002", "avg_value_20d"].item())
    assert pd.isna(result.loc[result["symbol"] == "000003", "avg_value_20d"].item())
    assert result.set_index("symbol").loc["000001", "market_cap"] == 10_000


def test_liquidity_snapshot_returns_typed_empty_frame_before_retained_history(
    project_root: Path,
):
    frame = pd.DataFrame([
        {"date": pd.Timestamp("2026-01-02"), "symbol": "000001", "trading_value": 100},
    ])
    cap = pd.DataFrame([
        {"date": pd.Timestamp("2026-01-02"), "symbol": "000001", "market_cap": 1_000},
    ])
    _write_partition(project_root, "kr_equity_price_daily", 2026, frame)
    _write_partition(project_root, "kr_equity_market_cap_daily", 2026, cap)

    result = liquidity_snapshot(project_root, "2025-12-31")

    assert result.empty
    assert str(result.dtypes["symbol"]) == "string"
    assert str(result.dtypes["avg_value_20d"]) == "Float64"
    assert str(result.dtypes["market_cap"]) == "Int64"


def test_liquidity_snapshot_fails_closed_on_duplicate_date_symbol(project_root: Path):
    prices = pd.DataFrame([
        {"date": pd.Timestamp("2026-01-02"), "symbol": "000001", "trading_value": 100},
        {"date": pd.Timestamp("2026-01-02"), "symbol": "000001", "trading_value": 200},
    ])
    caps = pd.DataFrame([
        {"date": pd.Timestamp("2026-01-02"), "symbol": "000001", "market_cap": 1_000},
    ])
    _write_partition(project_root, "kr_equity_price_daily", 2026, prices)
    _write_partition(project_root, "kr_equity_market_cap_daily", 2026, caps)

    with pytest.raises(ValueError, match="duplicate date-symbol"):
        liquidity_snapshot(project_root, "2026-01-02")


def test_liquidity_snapshot_distinguishes_missing_dataset_from_valid_empty(
    project_root: Path,
):
    with pytest.raises(FileNotFoundError, match="kr_equity_price_daily"):
        liquidity_snapshot(project_root, "2026-01-02")
