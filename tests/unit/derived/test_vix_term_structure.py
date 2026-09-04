from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_data.contracts.global_market import (
    FRED_VIX_DAILY,
    GLOBAL_INDEX_PRICE_DAILY,
)
from stock_data.derived.vix_term_structure import (
    VixTermStructureBuildError,
    build_vix_term_structure_dataset,
    calculate_vix_term_structure,
    validate_vix_term_structure,
)
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.validation.global_market import validate_fred, validate_global_index


def _indices(dates: pd.DatetimeIndex, *, vix9d, vix3m, vix6m, skew) -> pd.DataFrame:
    values = {
        "VIX9D": np.asarray(vix9d, dtype=float),
        "VIX3M": np.asarray(vix3m, dtype=float),
        "VIX6M": np.asarray(vix6m, dtype=float),
        "SKEW": np.asarray(skew, dtype=float),
    }
    return pd.DataFrame([
        {"date": day.date(), "symbol": symbol, "close": close}
        for position, day in enumerate(dates)
        for symbol, series in values.items()
        for close in (series[position],)
    ])


def test_vix_term_structure_calculates_ratios_and_regimes() -> None:
    dates = pd.date_range("2026-08-03", periods=2, freq="B")
    fred = pd.DataFrame({"date": dates.date, "vixcls": [15.0, 30.0]})
    indices = _indices(
        dates,
        vix9d=[12.0, 36.0], vix3m=[20.0, 25.0],
        vix6m=[22.0, 24.0], skew=[145.0, 130.0],
    )

    result = calculate_vix_term_structure(fred, indices)

    assert result["regime"].tolist() == ["contango", "backwardation"]
    assert result["ratio_1m_3m"].tolist() == pytest.approx([0.75, 1.2])
    assert result["ratio_9d_1m"].tolist() == pytest.approx([0.8, 1.2])
    assert result["pct_rank_252"].isna().all()
    validation = validate_vix_term_structure(fred, indices, result)
    assert validation.complete_curve_rows == 2


def test_vix_term_structure_uses_full_252_observation_percentile_window() -> None:
    dates = pd.date_range("2025-01-02", periods=252, freq="B")
    ratios = np.linspace(0.5, 1.5, num=252)
    vix3m = np.full(252, 20.0)
    vix = ratios * vix3m
    fred = pd.DataFrame({"date": dates.date, "vixcls": vix})
    indices = _indices(
        dates,
        vix9d=vix * 0.9, vix3m=vix3m,
        vix6m=np.full(252, 22.0), skew=np.full(252, 140.0),
    )

    result = calculate_vix_term_structure(fred, indices)

    assert result["pct_rank_252"].iloc[:-1].isna().all()
    assert result["pct_rank_252"].iloc[-1] == pytest.approx(1.0)
    assert result["ratio_1m_3m"].iloc[-1] == pytest.approx(1.5)


def test_vix_term_structure_validation_rejects_formula_drift() -> None:
    dates = pd.date_range("2026-08-03", periods=2, freq="B")
    fred = pd.DataFrame({"date": dates.date, "vixcls": [15.0, 30.0]})
    indices = _indices(
        dates,
        vix9d=[12.0, 36.0], vix3m=[20.0, 25.0],
        vix6m=[22.0, 24.0], skew=[145.0, 130.0],
    )
    result = calculate_vix_term_structure(fred, indices)
    result.loc[0, "ratio_1m_3m"] = 99.0

    with pytest.raises(VixTermStructureBuildError, match="formulas"):
        validate_vix_term_structure(fred, indices, result)


def test_offline_builder_persists_dual_source_lineage(tmp_path) -> None:
    dates = pd.date_range("2025-01-02", periods=270, freq="B")
    fred = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "vixcls": np.linspace(12.0, 24.0, 270)})
    compact_indices = _indices(
        dates,
        vix9d=np.linspace(11.0, 23.0, 270),
        vix3m=np.full(270, 20.0), vix6m=np.full(270, 22.0),
        skew=np.full(270, 140.0),
    )
    indices = compact_indices.assign(
        source_ticker=compact_indices["symbol"].map({
            "VIX9D": "^VIX9D", "VIX3M": "^VIX3M",
            "VIX6M": "^VIX6M", "SKEW": "^SKEW",
        }),
        open=compact_indices["close"], high=compact_indices["close"],
        low=compact_indices["close"], volume=pd.Series(
            [pd.NA] * len(compact_indices), dtype="Int64",
        ),
    )[list(GLOBAL_INDEX_PRICE_DAILY.column_names)]
    fred_root = tmp_path / "data/normalized/fred_vix_daily"
    index_root = tmp_path / "data/normalized/global_index_price_daily"
    write_dataset_atomic(fred, fred_root, FRED_VIX_DAILY, validate_fred)
    write_dataset_atomic(indices, index_root, GLOBAL_INDEX_PRICE_DAILY, validate_global_index)
    state_root = tmp_path / "data/state"
    state_root.mkdir(parents=True)
    fred_state = state_root / "fred_vix_daily.json"
    index_state = state_root / "global_index_price_daily.json"
    fred_state.write_text('{"dataset":"fred_vix_daily"}\n', encoding="utf-8")
    index_state.write_text('{"dataset":"global_index_price_daily"}\n', encoding="utf-8")
    output_root = tmp_path / "data/derived/us_vix_term_structure_daily"
    output_state = state_root / "us_vix_term_structure_daily.json"

    receipt = build_vix_term_structure_dataset(
        fred_vix_root=fred_root, fred_vix_state_path=fred_state,
        global_index_root=index_root, global_index_state_path=index_state,
        output_root=output_root, output_state_path=output_state,
    )

    assert receipt["api_calls"] == 0
    assert [item["dataset"] for item in receipt["inputs"]] == [
        "fred_vix_daily", "global_index_price_daily",
    ]
    assert receipt["validation"]["pct_rank_rows"] == 19
    assert output_state.is_file()
    assert len(list(output_root.glob("year=*/data.parquet"))) == 2
