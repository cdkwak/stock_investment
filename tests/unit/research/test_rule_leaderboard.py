from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from stock_data.research import rule_leaderboard
from stock_data.research.rule_leaderboard import (
    CYCLES,
    RESULT_KEYS,
    build_leaderboard,
    evaluate_definition,
)


def _candidate(
    candidate_id: str, side: str, indicators: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "id": candidate_id,
        "name": candidate_id,
        "side": side,
        "basket": "KR",
        "status": "active",
        "definition": {"type": "ladder", "indicators": indicators, "levels": len(indicators)},
        "added_on": "2026-09-04",
        "reason": "synthetic planted crash",
    }


def _temp_root() -> Path:
    root = (
        Path(__file__).parents[3]
        / ".tmp"
        / "agents"
        / "research-validation-20260905"
        / "fixtures"
        / uuid4().hex
    )
    root.mkdir(parents=True)
    return root


def _frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    specifications = [
        ("2000-04-03", "2000-08-01", -0.30, -0.15, 25.0, 0.25),
        ("2000-05-01", "2000-09-01", -0.35, -0.16, 24.0, 0.20),
        ("2001-01-02", "2001-05-15", -0.05, 0.15, 75.0, -0.20),
        ("2001-02-01", "2001-06-15", -0.04, 0.16, 76.0, -0.15),
        ("2003-01-02", "2003-05-15", -0.08, 0.00, 50.0, -0.05),
        ("2004-01-02", "2004-05-15", -0.25, 0.00, 45.0, 0.08),
        ("2020-03-02", "2020-07-15", -0.40, -0.20, 20.0, 0.30),
        ("2020-04-01", "2020-08-15", -0.32, -0.14, 28.0, 0.22),
        ("2022-02-01", "2022-06-15", -0.05, 0.20, 80.0, -0.25),
        ("2022-03-01", "2022-07-15", -0.04, 0.18, 78.0, -0.18),
        ("2022-05-02", "2022-09-15", -0.05, 0.00, 50.0, 0.12),
        ("2023-01-02", "2023-05-15", -0.24, 0.00, 45.0, 0.10),
        ("2026-09-03", None, -0.25, 0.00, 46.0, None),
    ]
    for position, (when, end, drawdown, disp, rsi, return_60) in enumerate(specifications):
        rows.append({
            "date": pd.Timestamp(when),
            "observation_date": when,
            "outcome_end_date_90": end,
            "series_id": "KOSPI200",
            "basket": "KR",
            "close": 100.0 + position,
            "drawdown252": drawdown,
            "disp60": disp,
            "rsi14": rsi,
            "vol_index_percentile252": 0.5,
            "realized_volatility_20d": 0.20,
            "forward_return_20": None if return_60 is None else return_60 / 3,
            "forward_return_60": return_60,
            "forward_return_90": None if return_60 is None else return_60 * 1.2,
            "forward_realized_volatility_60": None if return_60 is None else 0.25,
            "forward_max_drawdown_60": None if return_60 is None else -0.10,
        })
    return pd.DataFrame(rows)


def test_leaderboard_exact_schema_levels_cycles_current_and_analog() -> None:
    registry = {
        "schema_version": 1,
        "attempt_count": 2,
        "history": [
            {"date": "2026-09-04", "action": "add", "id": "drawdown", "reason": "test"},
            {"date": "2026-09-04", "action": "add", "id": "overheat", "reason": "test"},
        ],
        "candidates": [
            _candidate("drawdown", "drawdown", [
                {"key": "drawdown252", "op": "<=", "threshold": -0.20},
                {"key": "disp60", "op": "<=", "threshold": -0.10},
            ]),
            _candidate("overheat", "overheat", [
                {"key": "disp60", "op": ">=", "threshold": 0.10},
                {"key": "rsi14", "op": ">=", "threshold": 70.0},
            ]),
        ],
    }
    payload = build_leaderboard(
        _frame(), registry, version="a" * 64, generated_at="2026-09-04T00:00:00+00:00",
        compound_references={
            "KR": [{
                "underlying": "KOSPI",
                "rows": [{
                    "row_kind": "strategy", "drawdown_threshold": -.2,
                    "disp60_threshold": -.1, "levels": 2,
                    "leverage_multiple": 2, "base_exposure": 1.0,
                    "exit": "a", "cost_enabled": True,
                    "holdout": {
                        "final_wealth_multiple": 3.2,
                        "baseline_final_wealth_multiple": 2.5,
                        "relative_to_baseline": 1.28,
                    },
                }],
                "plateau": {
                    "best_fit_relative_to_baseline": 1.2609,
                    "neighbourhood_mean": .809,
                    "sharp_peak": True,
                },
            }, {
                "underlying": "KOSPI200",
                "rows": [{
                    "row_kind": "strategy", "drawdown_threshold": -.2,
                    "disp60_threshold": -.1, "levels": 2,
                    "leverage_multiple": 2, "base_exposure": 1.0,
                    "exit": "a", "cost_enabled": True,
                    "holdout": {
                        "final_wealth_multiple": 2.0,
                        "baseline_final_wealth_multiple": 2.4,
                        "relative_to_baseline": 0.8333,
                    },
                }],
                "plateau": {
                    "best_fit_relative_to_baseline": 1.1,
                    "neighbourhood_mean": 1.05,
                    "sharp_peak": False,
                },
            }],
        },
    )
    assert set(payload) == {
        "schema_version", "generated_at", "rules_version", "attempt_count",
        "fit_window", "holdout_window", "cycles", "candidates", "warnings",
    }
    assert payload["schema_version"] == 2
    assert payload["cycles"] == list(CYCLES)
    assert payload["fit_window"] == {"end": "2015-12-31"}
    assert payload["holdout_window"] == {"start": "2016-01-01"}

    drawdown, overheat = payload["candidates"]
    assert set(drawdown) == {
        "id", "name", "side", "basket", "status", "definition", "added_on",
        "reason", "results", "levels", "cycles", "current", "compound_ladder",
    }
    assert tuple(drawdown["results"]["fit"]) == RESULT_KEYS
    assert tuple(drawdown["results"]["holdout"]) == RESULT_KEYS
    assert [item["level"] for item in drawdown["levels"]] == [0, 1, 2]
    assert drawdown["results"]["fit"]["n"] == 2
    assert drawdown["results"]["holdout"]["n"] == 2
    # 2000-04-03 and 2000-05-01 are 28 days apart -> one episode (overlapping 60-day windows).
    assert drawdown["results"]["fit"]["independent_events"] == 1
    assert drawdown["results"]["holdout"]["independent_events"] == 1
    assert drawdown["results"]["fit"]["cycles_with_signal"] == 1
    assert drawdown["results"]["fit"]["signals_outside_cycles"] == 0
    assert len(CYCLES) == 9
    kospi = {
        "underlying": "KOSPI",
        "holdout_final_wealth_multiple": 3.2,
        "holdout_baseline_final_wealth_multiple": 2.5,
        "holdout_relative_to_baseline": 1.28,
        "plateau_verdict": "뾰족한 봉우리 · 최적 1.26배 / 이웃 0.81배",
    }
    kospi200 = {
        "underlying": "KOSPI200",
        "holdout_final_wealth_multiple": 2.0,
        "holdout_baseline_final_wealth_multiple": 2.4,
        "holdout_relative_to_baseline": 0.8333,
        "plateau_verdict": "넓은 고원 · 최적 1.10배 / 이웃 1.05배",
    }
    assert drawdown["compound_ladder"] == {
        "status": "matched", "product_basis": "synthetic_2x", "cost_enabled": True,
        "combination_label": "합성 2배 · 출구 a · 거래비용 포함 · 기본 노출 1.0",
        "underlyings": [kospi, kospi200],
        **kospi,
    }
    assert overheat["compound_ladder"] == {"status": "unavailable"}
    assert next(
        item for item in drawdown["cycles"] if item["id"] == "dotcom_2000"
    )["verdict"] == "hit"
    assert next(
        item for item in overheat["cycles"] if item["id"] == "bear_2022"
    )["verdict"] == "hit"
    assert drawdown["current"]["date"] == "2026-09-03"
    assert drawdown["current"]["score"] == drawdown["current"]["level"] == 1
    assert drawdown["current"]["max_level"] == 2
    assert drawdown["current"]["exposure"] == 0.5
    # Analogues are FIT-window rows only (the 2020 hold-out signal no longer leaks in).
    assert drawdown["current"]["analog"] == {"n": 1, "mean_60": 0.08, "hit_60": 1.0}
    assert set(drawdown["current"]["indicators"]) == {
        "drawdown252", "disp60", "rsi14", "volidx_pct",
    }
    assert set(drawdown["current"]) == {
        "date", "score", "level", "max_level", "exposure", "indicators", "analog",
    }
    assert set(drawdown["current"]["analog"]) == {"n", "mean_60", "hit_60"}
    assert all(
        set(item) == {"id", "signals", "first_signal", "mean_60", "verdict"}
        for item in drawdown["cycles"]
    )
    assert all(
        set(item) == {"level", "fit", "holdout"} for item in drawdown["levels"]
    )


@pytest.mark.parametrize(
    ("definition", "expected_level", "expected_exposure"),
    [
        (
            {
                "type": "ladder",
                "indicators": [
                    {"key": "drawdown252", "op": "<=", "threshold": -0.20},
                    {"key": "disp60", "op": "<=", "threshold": -0.10},
                ],
                "levels": 2,
            },
            1,
            0.5,
        ),
        ({"type": "vol_target", "target_vol": 0.15, "window": 20}, 0, 0.75),
        (
            {
                "type": "hybrid",
                "ladder": {
                    "side": "drawdown",
                    "indicators": [
                        {"key": "drawdown252", "op": "<=", "threshold": -0.20},
                        {"key": "disp60", "op": "<=", "threshold": -0.10},
                    ],
                    "levels": 2,
                },
                "vol_target": {"target_vol": 0.15, "window": 20},
            },
            1,
            1.0,
        ),
    ],
)
def test_evaluate_definition_covers_ladder_vol_target_and_hybrid(
    monkeypatch: pytest.MonkeyPatch,
    definition: dict[str, object],
    expected_level: int,
    expected_exposure: float,
) -> None:
    monkeypatch.setattr(rule_leaderboard, "_load_cached_evaluation_frame", lambda *_: _frame())

    result = evaluate_definition(
        Path("."),
        definition,
        "KR",
        "drawdown",
    )

    assert set(result) == {
        "id", "name", "side", "basket", "status", "definition", "added_on",
        "reason", "results", "levels", "cycles", "current", "compound_ladder",
    }
    assert tuple(result["results"]["fit"]) == RESULT_KEYS
    assert tuple(result["results"]["holdout"]) == RESULT_KEYS
    assert result["current"]["level"] == expected_level
    assert result["current"]["exposure"] == pytest.approx(expected_exposure)


def test_compound_references_are_loaded_from_summary_and_matching_grid() -> None:
    project_root = _temp_root()
    output = project_root / "artifacts/research/compound_ladder"
    output.mkdir(parents=True)
    grid_path = output / "grid_kr_kospi.json"
    grid_path.write_text(json.dumps([{
        "row_kind": "strategy", "basket": "KR", "underlying": "KOSPI",
        "drawdown_threshold": -.2, "disp60_threshold": -.1, "levels": 2,
        "leverage_multiple": 2, "base_exposure": 1.0, "exit": "a",
        "cost_enabled": True,
        "holdout": {
            "final_wealth_multiple": 3.0,
            "baseline_final_wealth_multiple": 2.5,
            "relative_to_baseline": 1.2,
        },
    }]), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps({
        "baskets": {"KR": [{
            "underlying": "KOSPI",
            "grid_path": "artifacts/research/compound_ladder/grid_kr_kospi.json",
            "plateau": [{
                "surface": "threshold_x_levels", "sharp_peak": False,
                "best_fit_relative_to_baseline": 1.2, "neighbourhood_mean": 1.1,
            }],
        }]},
    }), encoding="utf-8")

    references = rule_leaderboard._read_compound_references(project_root)

    assert [item["underlying"] for item in references["KR"]] == ["KOSPI"]
    assert references["KR"][0]["rows"][0]["holdout"]["relative_to_baseline"] == 1.2


def test_independent_episodes_pool_series_and_split_on_90_day_gaps() -> None:
    dates = pd.Series(pd.to_datetime([
        "2020-03-02", "2020-03-02",  # KOSPI and KOSPI200 on the same day -> once
        "2020-03-20", "2020-05-15",  # within 90 days of the previous -> same episode
        "2020-09-01",                # 109 days later -> new episode
        "2022-06-01", "2022-10-15",  # 136 days apart -> two episodes
    ]))
    assert rule_leaderboard._independent_episodes(dates) == 4
    assert rule_leaderboard._independent_episodes(pd.Series([], dtype="datetime64[ns]")) == 0
