from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from stock_data.contracts.kr_equity import (
    KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
    KR_EQUITY_PRICE_DAILY,
)
from stock_data.contracts.kr_market import KR_MARKET_BREADTH_DAILY
from stock_data.pipelines.market_breadth_rebuild import (
    DATASET,
    MarketBreadthRebuildError,
    rebuild_market_breadth,
)
from stock_data.storage.contract_arrow import contract_arrow_schema, dataframe_to_contract_table


def _write(root: Path, relative: str, frame: pd.DataFrame, contract) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(dataframe_to_contract_table(frame, contract), path)


def _fixtures(root: Path, *, wrong_existing: bool = False) -> Path:
    price_rows = []
    universe_rows = []
    closes = {
        "2025-12-30": {"000001": 100, "000002": 50},
        "2026-01-02": {"000001": 110, "000002": 50},
    }
    for day, symbols in closes.items():
        for symbol, close in symbols.items():
            price_rows.append({
                "date": day, "market": "KOSPI", "symbol": symbol,
                "open": close, "high": close, "low": close, "close": close,
                "volume": 1, "trading_value": close,
                "source": "fixture", "source_operation": "fixture", "source_date": day,
            })
            universe_rows.append({
                "date": day, "market": "KOSPI", "symbol": symbol, "isin": None,
                "name": symbol, "listed_info_present": True, "price_present": True,
                "master_present": False, "universe_source": "listed_info+price",
                "security_type": "common", "listing_date": None, "delisting_date": None,
            })
    prices = pd.DataFrame(price_rows, columns=KR_EQUITY_PRICE_DAILY.column_names)
    universe = pd.DataFrame(
        universe_rows, columns=KR_EQUITY_CANONICAL_UNIVERSE_DAILY.column_names
    )
    for year in (2025, 2026):
        _write(
            root,
            f"data/normalized/kr_equity_price_daily/market=KOSPI/year={year}/data.parquet",
            prices[prices.date.str[:4].eq(str(year))].reset_index(drop=True),
            KR_EQUITY_PRICE_DAILY,
        )
        _write(
            root,
            f"data/published/kr_equity_canonical_universe_daily/market=KOSPI/year={year}/data.parquet",
            universe[universe.date.str[:4].eq(str(year))].reset_index(drop=True),
            KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
        )
    existing = pd.DataFrame([{
        "date": "2026-01-02", "market": "KOSPI",
        "advancing": 0 if wrong_existing else 1,
        "declining": 0, "unchanged": 2 if wrong_existing else 1, "total": 2,
    }], columns=KR_MARKET_BREADTH_DAILY.column_names)
    output = root / "data/derived/kr_market_breadth_daily/market=KOSPI/year=2026/data.parquet"
    _write(root, output.relative_to(root).as_posix(), existing, KR_MARKET_BREADTH_DAILY)
    return output


def test_dry_run_preserves_existing_values_and_records_retained_lineage(tmp_path: Path) -> None:
    output = _fixtures(tmp_path)
    original = output.read_bytes()
    result = rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    state = result["state"]
    assert result["status"] == "DRY_RUN_PASS"
    assert state["api_calls"] == 0
    assert state["rows"] == 1
    assert state["coverage_start"] == state["coverage_end"] == "2026-01-02"
    assert state["existing_values_preserved"]["existing_rows"] == 1
    assert set(state["input_manifests"]) == {
        "kr_equity_price_daily", "kr_equity_canonical_universe_daily"
    }
    assert state["output_manifest"][0]["path"] == (
        "data/derived/kr_market_breadth_daily/market=KOSPI/year=2026/data.parquet"
    )
    assert output.read_bytes() == original
    assert not (tmp_path / "data/state/kr_market_breadth_daily_rebuild.json").exists()


def test_rebuild_fails_closed_if_an_existing_value_would_change(tmp_path: Path) -> None:
    output = _fixtures(tmp_path, wrong_existing=True)
    original = output.read_bytes()
    with pytest.raises(MarketBreadthRebuildError, match="changes existing"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    assert output.read_bytes() == original
    assert not list((tmp_path / "data").glob(f".{DATASET}.rebuild.*"))


def test_apply_is_atomic_and_writes_exact_contract_and_state(tmp_path: Path) -> None:
    output = _fixtures(tmp_path)
    with pytest.raises(MarketBreadthRebuildError, match="exact dataset confirmation"):
        rebuild_market_breadth(project_root=tmp_path, mode="apply")
    result = rebuild_market_breadth(
        project_root=tmp_path, mode="apply", confirmation=DATASET
    )
    assert result["status"] == "REBUILT"
    assert pq.ParquetFile(output).schema_arrow.equals(
        contract_arrow_schema(KR_MARKET_BREADTH_DAILY), check_metadata=False
    )
    state_path = tmp_path / "data/state/kr_market_breadth_daily_rebuild.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == result["state"]
    assert state["output_manifest"][0]["bytes"] == output.stat().st_size
    assert not (tmp_path / "data/state/.kr_market_breadth_daily.rebuild.transaction.json").exists()
    assert not list((tmp_path / "data/derived").glob(f".{DATASET}.rebuild.backup.*"))


def test_apply_rolls_back_output_and_state_on_promotion_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _fixtures(tmp_path)
    original = output.read_bytes()
    original_replace = Path.replace

    def fail_state_promotion(path: Path, target: Path):
        if path.name == "state.json":
            raise OSError("injected state promotion failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_state_promotion)
    with pytest.raises(OSError, match="injected"):
        rebuild_market_breadth(
            project_root=tmp_path, mode="apply", confirmation=DATASET
        )
    assert output.read_bytes() == original
    assert not (tmp_path / "data/state/kr_market_breadth_daily_rebuild.json").exists()
    assert not (tmp_path / "data/state/.kr_market_breadth_daily.rebuild.transaction.json").exists()
    assert not list((tmp_path / "data/derived").glob(f".{DATASET}.rebuild.backup.*"))
