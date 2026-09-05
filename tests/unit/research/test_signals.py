from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_data.research.compound_ladder import LadderSpec, ladder_levels
from stock_data.research.signals import (
    BuySignalSpec,
    compute_signal_features,
    evaluate_buy_signal,
)


def _features(**overrides: list[float]) -> pd.DataFrame:
    frame = pd.DataFrame({
        "date": pd.bdate_range("2020-01-01", periods=4),
        "close": [100.0, 80.0, 75.0, 90.0],
        "drawdown252": [-0.10, -0.20, -0.25, -0.18],
        "disp60": [-0.05, -0.08, -0.12, -0.04],
        "rsi14": [50.0, 35.0, 25.0, 45.0],
    })
    for key, value in overrides.items():
        frame[key] = value
    return frame


def test_compute_signal_features_contains_a_to_e_inputs_and_reuses_wilder_rsi() -> None:
    count = 320
    prices = pd.DataFrame({
        "date": pd.bdate_range("2019-01-01", periods=count),
        "series_id": "SYNTH",
        "basket": "RESEARCH",
        "close": 100.0 + np.sin(np.arange(count) / 9.0) * 8.0 + np.arange(count) * 0.02,
    })

    result = compute_signal_features(prices)

    assert {"drawdown252", "disp60", "rsi14", "drawdown252_change_1", "running_min_close"} <= set(result)
    assert result["rsi14"].dropna().between(0.0, 100.0).all()
    assert result["drawdown252_change_1"].equals(result.groupby("series_id")["drawdown252"].diff())
    assert not any(column.startswith("event_") for column in result)


def test_signal_feature_namespace_rejects_forward_outcomes() -> None:
    prices = pd.DataFrame({
        "date": pd.bdate_range("2020-01-01", periods=20),
        "series_id": "SYNTH",
        "basket": "RESEARCH",
        "close": np.linspace(100.0, 110.0, 20),
        "forward_return_21": 0.1,
    })
    with pytest.raises(ValueError, match="outcome namespace is forbidden"):
        compute_signal_features(prices)
    with pytest.raises(ValueError, match="outcome namespace is forbidden"):
        evaluate_buy_signal(
            _features().assign(outcome_return=0.1),
            BuySignalSpec(kind="A", drawdown_threshold=-0.20),
        )


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (BuySignalSpec(kind="A", drawdown_threshold=-0.20), [False, True, True, False]),
        (
            BuySignalSpec(
                kind="B", drawdown_threshold=-0.20, disp60_threshold=-0.10
            ),
            [False, False, True, False],
        ),
        (
            BuySignalSpec(
                kind="C", drawdown_threshold=-0.20, rsi14_threshold=30.0
            ),
            [False, False, True, False],
        ),
        (
            BuySignalSpec(
                kind="D",
                drawdown_threshold=-0.20,
                deceleration_lookback_days=1,
                deceleration_tolerance=0.0,
            ),
            [False, False, False, False],
        ),
        (
            BuySignalSpec(
                kind="E", drawdown_threshold=-0.20, rebound_pct=0.20
            ),
            [False, False, False, True],
        ),
    ],
)
def test_candidate_definitions_a_to_e(spec: BuySignalSpec, expected: list[bool]) -> None:
    result = evaluate_buy_signal(_features(), spec)
    assert result["signal"].tolist() == expected


def test_decelerating_candidate_uses_n_day_change_and_opposite_of_widening() -> None:
    result = evaluate_buy_signal(
        _features(drawdown252=[-0.10, -0.30, -0.35, -0.34]),
        BuySignalSpec(
            kind="D",
            drawdown_threshold=-0.20,
            deceleration_lookback_days=1,
            deceleration_tolerance=0.0,
        ),
    )
    assert result["drawdown252_change_1"].tolist() == pytest.approx(
        [np.nan, -0.20, -0.05, 0.01], nan_ok=True
    )
    assert result["signal"].tolist() == [False, False, False, True]


def test_ladder_accepts_pluggable_candidate_and_compatibility_b_is_identical() -> None:
    features = _features()
    ladder = LadderSpec(
        drawdown_threshold=-0.20,
        disp60_threshold=-0.10,
        product_share_at_max=0.5,
        levels=2,
    )
    explicit = ladder_levels(
        features,
        ladder,
        BuySignalSpec(kind="B", drawdown_threshold=-0.20, disp60_threshold=-0.10),
    )
    compatible = ladder_levels(features, ladder)
    pd.testing.assert_series_equal(explicit["observed_level"], compatible["observed_level"])
    assert explicit["target_weight"].max() == pytest.approx(0.5)


def test_candidate_specific_missing_parameters_fail_under_rule_six() -> None:
    with pytest.raises(ValueError, match="disp60_threshold is undecided.*⑥"):
        BuySignalSpec(kind="B", drawdown_threshold=-0.20)
    with pytest.raises(ValueError, match="rsi14_threshold is undecided.*⑥"):
        BuySignalSpec(kind="C", drawdown_threshold=-0.20)
    with pytest.raises(ValueError, match="deceleration_lookback_days is undecided.*⑥"):
        BuySignalSpec(kind="D", drawdown_threshold=-0.20)
    with pytest.raises(ValueError, match="rebound_pct is undecided.*⑥"):
        BuySignalSpec(kind="E", drawdown_threshold=-0.20)
