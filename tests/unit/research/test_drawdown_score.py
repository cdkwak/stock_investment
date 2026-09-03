from __future__ import annotations

import pandas as pd
import pytest

from stock_data.research.drawdown_score import (
    ScoreThresholds,
    compute_drawdown_score,
    evaluate_threshold,
    score_event_mask,
    search_score_grid,
)


LOOSE = ScoreThresholds(
    drawdown252=(-0.10, -0.20, -0.30),
    disp60=(-0.05, -0.10, -0.15),
    rsi14=(40.0, 30.0, 20.0),
    trigger_score=3,
)
STRICT = ScoreThresholds(
    drawdown252=(-0.30, -0.40, -0.50),
    disp60=(-0.15, -0.25, -0.35),
    rsi14=(20.0, 10.0, 5.0),
    trigger_score=3,
)


def _row(date: str, dd: float, disp: float, rsi: float, outcome: float) -> dict[str, object]:
    return {
        "observation_date": date,
        "outcome_end_date_60": date,
        "series_id": "SYNTH",
        "drawdown252": dd,
        "disp60": disp,
        "rsi14": rsi,
        "forward_return_60": outcome,
    }


def test_score_points_and_crossing_event() -> None:
    frame = pd.DataFrame(
        [
            _row("2010-01-01", 0.0, 0.0, 50.0, 0.0),
            _row("2010-01-02", -0.31, -0.16, 19.0, 0.1),
            _row("2010-01-03", -0.32, -0.17, 18.0, 0.2),
        ]
    )

    assert compute_drawdown_score(frame, LOOSE).tolist() == [0, 9, 9]
    assert score_event_mask(frame, LOOSE).tolist() == [False, True, False]


def test_fit_boundary_requires_the_60_session_outcome_to_finish_in_fit() -> None:
    frame = pd.DataFrame(
        [
            _row("2015-01-02", 0.0, 0.0, 50.0, 0.0),
            _row("2015-03-02", -0.15, -0.08, 35.0, 0.20),
            _row("2015-06-01", 0.0, 0.0, 50.0, 0.0),
            _row("2015-12-20", -0.15, -0.08, 35.0, 0.90),
            _row("2016-01-05", 0.0, 0.0, 50.0, 0.0),
            _row("2016-02-01", -0.15, -0.08, 35.0, 0.30),
        ]
    )
    frame.loc[3, "outcome_end_date_60"] = "2016-03-20"
    frame.loc[5, "outcome_end_date_60"] = "2016-05-01"

    result = evaluate_threshold(
        frame,
        LOOSE,
        fit_end="2015-12-31",
        holdout_start="2016-01-01",
    )

    assert result["fit_n"] == 1
    assert result["fit_mean"] == pytest.approx(0.20)
    assert result["holdout_n"] == 1
    assert result["holdout_mean"] == pytest.approx(0.30)


def test_new_score_callers_use_90_session_outcome_end_for_fit_isolation() -> None:
    frame = pd.DataFrame(
        [
            _row("2015-01-02", 0.0, 0.0, 50.0, 0.0),
            _row("2015-03-02", -0.15, -0.08, 35.0, 0.20),
            _row("2015-06-01", 0.0, 0.0, 50.0, 0.0),
            _row("2015-10-01", -0.15, -0.08, 35.0, 9.0),
        ]
    )
    frame["outcome_end_date_90"] = [
        "2015-05-01",
        "2015-07-01",
        "2015-10-01",
        "2016-02-01",
    ]

    result = evaluate_threshold(
        frame,
        LOOSE,
        fit_end="2015-12-31",
        holdout_start="2016-01-01",
    )

    assert result["fit_n"] == 1
    assert result["fit_mean"] == pytest.approx(0.20)


def test_grid_ranking_uses_fit_only_and_prefers_toy_winner() -> None:
    frame = pd.DataFrame(
        [
            _row("2010-01-01", 0.0, 0.0, 50.0, 0.0),
            _row("2010-02-01", -0.15, -0.08, 35.0, 0.30),
            _row("2010-03-01", 0.0, 0.0, 50.0, 0.0),
            _row("2010-04-01", -0.35, -0.20, 15.0, -0.20),
            _row("2016-01-04", 0.0, 0.0, 50.0, 0.0),
            _row("2016-02-01", -0.35, -0.20, 15.0, 9.99),
        ]
    )

    grid, winner = search_score_grid(
        frame,
        fit_end="2015-12-31",
        holdout_start="2016-01-01",
        min_events=1,
        candidates=(STRICT, LOOSE),
    )

    assert winner == LOOSE
    assert grid.iloc[0]["threshold_id"] == LOOSE.threshold_id
    assert grid.iloc[0]["fit_mean"] == pytest.approx(0.05)
    assert grid.iloc[0]["holdout_mean"] == pytest.approx(9.99)


def test_invalid_threshold_order_fails_closed() -> None:
    with pytest.raises(ValueError, match="strictly decreasing"):
        ScoreThresholds(
            drawdown252=(-0.30, -0.20, -0.10),
            disp60=(-0.05, -0.10, -0.15),
            rsi14=(40.0, 30.0, 20.0),
            trigger_score=3,
        )
