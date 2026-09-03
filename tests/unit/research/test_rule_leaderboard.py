from __future__ import annotations

import pandas as pd

from stock_data.research.rule_leaderboard import CYCLES, RESULT_KEYS, build_leaderboard


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
        _frame(), registry, version="a" * 64, generated_at="2026-09-04T00:00:00+00:00"
    )
    assert set(payload) == {
        "schema_version", "generated_at", "rules_version", "attempt_count",
        "fit_window", "holdout_window", "cycles", "candidates", "warnings",
    }
    assert payload["cycles"] == list(CYCLES)
    assert payload["fit_window"] == {"end": "2015-12-31"}
    assert payload["holdout_window"] == {"start": "2016-01-01"}

    drawdown, overheat = payload["candidates"]
    assert set(drawdown) == {
        "id", "name", "side", "basket", "status", "definition", "added_on",
        "reason", "results", "levels", "cycles", "current",
    }
    assert tuple(drawdown["results"]["fit"]) == RESULT_KEYS
    assert tuple(drawdown["results"]["holdout"]) == RESULT_KEYS
    assert [item["level"] for item in drawdown["levels"]] == [0, 1, 2]
    assert drawdown["results"]["fit"]["n"] == 2
    assert drawdown["results"]["holdout"]["n"] == 2
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
    assert drawdown["current"]["analog"] == {"n": 2, "mean_60": 0.09, "hit_60": 1.0}
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
