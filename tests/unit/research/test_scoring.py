from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_data.research.scoring import (
    result_card,
    score_buy_events,
    score_sell_events,
    validate_result_card,
)
from stock_data.research.signals import BuySignalSpec


def _prices(count: int = 700) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.bdate_range("2018-01-01", periods=count),
        "close": 100.0 + np.arange(count, dtype="float64") * 0.2,
    })


def test_buy_scoring_reports_mean_median_win_rate_and_shared_episode_count() -> None:
    prices = _prices(400)
    events = prices.loc[[0, 20, 100], "date"]

    result = score_buy_events(prices, events, horizons=(21, 63))

    assert result["side"] == "buy"
    assert result["events_total"] == 3
    assert result["events_independent"] == 2
    assert result["horizons"]["21"]["events_mature"] == 3
    assert result["horizons"]["21"]["mean_return"] > 0.0
    assert result["horizons"]["21"]["median_return"] > 0.0
    assert result["horizons"]["21"]["win_rate"] == 1.0
    assert "mean_realized_volatility" not in result["horizons"]["21"]


def test_sell_scoring_reports_only_volatility_and_max_drawdown_distributions() -> None:
    prices = _prices(180)
    prices["close"] += np.sin(np.arange(len(prices)) / 2.0) * 3.0
    events = prices.loc[[0, 80], "date"]

    result = score_sell_events(prices, events, horizons=(21, 63))

    assert result["side"] == "sell"
    assert result["events_total"] == 2
    assert result["events_independent"] == 2
    row = result["horizons"]["21"]
    assert row["mean_realized_volatility"] > 0.0
    assert row["median_realized_volatility"] > 0.0
    assert row["mean_max_drawdown"] <= 0.0
    assert row["median_max_drawdown"] <= 0.0
    assert "mean_return" not in row and "win_rate" not in row


def test_result_card_contains_mandatory_counts_medians_and_aligned_path() -> None:
    prices = _prices()
    prices.loc[252:302, "close"] = np.linspace(prices.loc[251, "close"], 70.0, 51)
    prices.loc[303:, "close"] = np.linspace(70.0, 150.0, len(prices) - 303)
    spec = BuySignalSpec(kind="A", drawdown_threshold=-0.20)

    card = result_card(spec, prices)

    assert card["claim"].startswith("A:")
    assert isinstance(card["events_total"], int)
    assert isinstance(card["events_independent"], int)
    assert [row["label"] for row in card["table"]] == ["1개월", "3개월", "6개월", "12개월"]
    assert all(
        {"events_total", "events_independent", "events_mature", "median_return"} <= set(row)
        for row in card["table"]
    )
    zero = next(row for row in card["average_path"] if row["offset_sessions"] == 0)
    assert zero["mean_index"] == pytest.approx(100.0)

    invalid = {**card, "table": [dict(row) for row in card["table"]]}
    del invalid["table"][0]["median_return"]
    with pytest.raises(ValueError, match="counts, mean, median, and win rate"):
        validate_result_card(invalid)
