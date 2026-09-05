from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_data.research.foreign_transfer import (
    classify_episode,
    compute_volatility_scale,
    normalized_thresholds,
    restrict_japan_window,
)


def test_volatility_scale_uses_daily_log_returns_for_synthetic_pair() -> None:
    korea_log_returns = np.array([0.01, -0.01, 0.02, -0.02])
    market_log_returns = 2.0 * korea_log_returns
    korea = pd.Series(np.exp(np.r_[0.0, korea_log_returns].cumsum()))
    market = pd.Series(np.exp(np.r_[0.0, market_log_returns].cumsum()))

    assert compute_volatility_scale(market, korea) == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("scale", "expected"),
    [
        (0.10, (-0.05, -0.03)),
        (1.00, (-0.20, -0.10)),
        (10.0, (-0.60, -0.30)),
    ],
)
def test_normalized_thresholds_apply_fixed_clamps(
    scale: float, expected: tuple[float, float]
) -> None:
    assert normalized_thresholds(
        scale,
        drawdown_threshold=-0.20,
        disp60_threshold=-0.10,
    ) == pytest.approx(expected)


def test_normalized_thresholds_require_explicit_source_thresholds() -> None:
    with pytest.raises(ValueError, match="drawdown_threshold is undecided.*⑥"):
        normalized_thresholds(1.0)


@pytest.mark.parametrize(
    ("date", "market", "expected_class", "expected_window"),
    [
        ("1997-07-01", "TAIEX", "synchronous", "asia_crisis_1997_1998"),
        ("1997-07-01", "SP500", "idiosyncratic", None),
        ("2008-08-01", "NIKKEI225", "synchronous", "gfc_2008_2009"),
        ("2011-09-01", "DAX", "synchronous", "euro_crisis_2011"),
        ("2011-09-01", "TAIEX", "idiosyncratic", None),
        ("2022-10-31", "NASDAQ100", "synchronous", "rate_shock_2022"),
        ("2022-11-01", "NASDAQ100", "idiosyncratic", None),
    ],
)
def test_episode_classification_uses_the_fixed_window_table(
    date: str,
    market: str,
    expected_class: str,
    expected_window: str | None,
) -> None:
    assert classify_episode(date, market) == (expected_class, expected_window)


def test_japan_window_restriction_is_inclusive_and_discards_outside_rows() -> None:
    frame = pd.DataFrame(
        {
            "date": ["1989-12-29", "1990-01-01", "2012-12-31", "2013-01-01"],
            "close": [1.0, 2.0, 3.0, 4.0],
        }
    )

    result = restrict_japan_window(frame)

    assert result["date"].tolist() == [pd.Timestamp("1990-01-01"), pd.Timestamp("2012-12-31")]
    assert result["close"].tolist() == [2.0, 3.0]
