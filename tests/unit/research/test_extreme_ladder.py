from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_data.research.extreme_ladder import (
    IndicatorSpec,
    add_extreme_flags,
    aggregate_ladder_levels,
    build_equal_weight_scores,
    evaluate_standalone_indicators,
    indicator_survives,
    monotonicity_check,
    rolling_percentile_rank,
    volatility_target_exposure,
)


def test_vol_index_percentile_and_upper_extreme_buckets() -> None:
    increasing = pd.Series(np.arange(1.0, 254.0))
    percentile = rolling_percentile_rank(increasing)

    assert percentile.iloc[:251].isna().all()
    assert percentile.iloc[251] == pytest.approx(251.5 / 252.0)

    frame = pd.DataFrame(
        {
            "drawdown252": [-0.35, -0.05],
            "disp60": [-0.12, 0.12],
            "rsi14": [25.0, 75.0],
            "vol_index_percentile252": [0.90, 0.10],
        }
    )
    flags = add_extreme_flags(frame)

    assert bool(flags.loc[0, "flag_drawdown252_low"])
    assert bool(flags.loc[0, "flag_fear_high"])
    assert bool(flags.loc[1, "flag_disp60_high"])
    assert bool(flags.loc[1, "flag_rsi14_high"])
    assert bool(flags.loc[1, "flag_greed_low"])


def test_vol_percentile_and_extreme_flags_do_not_change_after_future_edit() -> None:
    original = pd.Series(20.0 + np.sin(np.arange(500) / 17.0))
    changed = original.copy()
    changed.iloc[401:] = np.linspace(1.0, 100.0, len(changed) - 401)

    left_pct = rolling_percentile_rank(original)
    right_pct = rolling_percentile_rank(changed)
    pd.testing.assert_series_equal(left_pct.iloc[:401], right_pct.iloc[:401])

    base = pd.DataFrame(
        {
            "drawdown252": np.linspace(-0.4, 0.0, len(original)),
            "disp60": np.sin(np.arange(len(original)) / 13.0) * 0.15,
            "rsi14": 50.0 + np.sin(np.arange(len(original)) / 11.0) * 30.0,
            "vol_index_percentile252": left_pct,
        }
    )
    altered = base.copy()
    altered.loc[401:, "vol_index_percentile252"] = right_pct.iloc[401:]
    pd.testing.assert_frame_equal(
        add_extreme_flags(base).iloc[:401],
        add_extreme_flags(altered).iloc[:401],
    )


@pytest.mark.parametrize(
    ("fit_difference", "holdout_difference", "fit_n", "holdout_n", "expected"),
    [
        (0.01, 0.02, 15, 15, True),
        (-0.01, -0.02, 15, 15, True),
        (0.01, -0.02, 15, 15, False),
        (0.01, 0.02, 14, 15, False),
        (0.0, 0.0, 20, 20, False),
    ],
)
def test_survivor_rule(
    fit_difference: float,
    holdout_difference: float,
    fit_n: int,
    holdout_n: int,
    expected: bool,
) -> None:
    assert (
        indicator_survives(
            fit_difference, holdout_difference, fit_n, holdout_n
        )
        is expected
    )


def test_standalone_fit_uses_90_session_outcome_end() -> None:
    frame = pd.DataFrame(
        {
            "observation_date": [
                "2015-01-02",
                "2015-01-03",
                "2015-10-01",
                "2016-01-04",
                "2016-01-05",
            ],
            "outcome_end_date_90": [
                "2015-05-01",
                "2015-05-02",
                "2016-02-01",
                "2016-05-02",
                "2016-05-03",
            ],
            "series_id": ["SYNTH"] * 5,
            "basket": ["KR"] * 5,
            "forward_return_60": [0.20, 0.0, 9.0, 0.30, 0.0],
            "flag": [True, False, True, True, False],
        }
    )
    result = evaluate_standalone_indicators(
        frame,
        baskets=("KR",),
        specs=(IndicatorSpec("DRAWDOWN", "synthetic", "flag"),),
        min_events=1,
    ).iloc[0]

    assert result["fit_n"] == 1
    assert result["fit_difference"] == pytest.approx(0.10)
    assert result["holdout_n"] == 1
    assert result["holdout_difference"] == pytest.approx(0.15)
    assert bool(result["survives"])


def test_ladder_level_aggregation_and_monotonicity() -> None:
    frame = pd.DataFrame(
        {
            "observation_date": ["2015-01-02", "2015-01-03", "2016-01-04"],
            "outcome_end_date_90": ["2015-06-01", "2015-06-02", "2016-06-01"],
            "basket": ["KR"] * 3,
            "side": ["DRAWDOWN"] * 3,
            "indicator_count": [2] * 3,
            "score_level": pd.Series([0, 2, 2], dtype="Int64"),
            "forward_return_20": [0.01, 0.03, 0.04],
            "forward_return_60": [0.02, 0.06, 0.08],
            "forward_return_90": [0.03, 0.09, 0.12],
            "forward_realized_volatility_60": [0.1, 0.2, 0.3],
            "forward_max_drawdown_60": [-0.05, -0.08, -0.10],
        }
    )
    result = aggregate_ladder_levels(frame, min_events=1)
    fit_level_2 = result.loc[
        result["period"].eq("FIT") & result["score_level"].eq(2)
    ].iloc[0]

    assert fit_level_2["n"] == 1
    assert fit_level_2["mean_return_60"] == pytest.approx(0.06)
    assert result.loc[result["period"].eq("FIT"), "score_level"].tolist() == [0, 1, 2]
    assert set(result.loc[result["period"].eq("FIT"), "monotonicity"]) == {"PASS"}

    increasing = pd.DataFrame(
        {"score_level": [0, 1, 2], "n": [15, 15, 15], "mean_return_60": [0.0, 0.1, 0.2]}
    )
    assert monotonicity_check(increasing, side="DRAWDOWN") == "PASS"
    assert monotonicity_check(increasing, side="OVERHEAT") == "FAIL"


def test_equal_weight_ladder_requires_two_to_four_survivors() -> None:
    frame = pd.DataFrame(
        {
            "basket": ["KR"],
            "flag_drawdown252_low": pd.Series([True], dtype="boolean"),
            "flag_disp60_low": pd.Series([False], dtype="boolean"),
        }
    )
    one = pd.DataFrame(
        {
            "basket": ["KR"],
            "side": ["DRAWDOWN"],
            "flag_column": ["flag_drawdown252_low"],
            "survives": [True],
        }
    )
    two = pd.concat(
        [
            one,
            pd.DataFrame(
                {
                    "basket": ["KR"],
                    "side": ["DRAWDOWN"],
                    "flag_column": ["flag_disp60_low"],
                    "survives": [True],
                }
            ),
        ],
        ignore_index=True,
    )

    assert build_equal_weight_scores(frame, one)["score_level"].isna().all()
    eligible = build_equal_weight_scores(frame, two)
    assert eligible.loc[eligible["side"].eq("DRAWDOWN"), "score_level"].iloc[0] == 1


def test_volatility_targeting_exposure_math() -> None:
    exposure = volatility_target_exposure(
        pd.Series([0.0, 0.10, 0.15, 0.30, np.nan]), target_vol=0.15
    )

    assert exposure.iloc[:4].tolist() == pytest.approx([1.0, 1.0, 1.0, 0.5])
    assert np.isnan(exposure.iloc[4])
