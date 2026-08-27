from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from market_backtest.labels import (
    LABEL_HORIZONS_TRADING_DAYS, MAX_LABEL_HORIZON_TRADING_DAYS,
    build_forward_labels,
)
from market_backtest.signals import build_descriptive_signals, evaluate_signals
from market_backtest.walk_forward import expanding_walk_forward
from market_features.kospi200 import build_kospi200_features


def source(rows: int = 180) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-02", periods=rows).strftime("%Y-%m-%d"),
        "close": 100.0 * np.cumprod(np.full(rows, 1.001)),
        "ticker": "1028",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
    })


def test_labels_are_forward_outcomes_with_horizon_availability():
    assert LABEL_HORIZONS_TRADING_DAYS == (5, 20, 60)
    assert MAX_LABEL_HORIZON_TRADING_DAYS == 60
    labels = build_forward_labels(source())
    assert len(labels) == 120
    assert labels.iloc[0].forward_return_5d == pytest.approx(1.001**5 - 1)
    assert labels.iloc[0].label_available_at.startswith(source().iloc[60].date)
    assert labels["ticker"].eq("1028").all()
    assert labels["date_semantics"].eq("KRX_TRADING_DATE_DAILY_FINAL").all()
    assert labels.filter(regex="forward_|mae|mfe").notna().all().all()
    broken = source(); broken.loc[10, "close"] = np.inf
    with pytest.raises(ValueError, match="key/value"):
        build_forward_labels(broken)
    mixed = source(); mixed.loc[10, "ticker"] = "9999"
    with pytest.raises(ValueError, match="source identity must be constant"):
        build_forward_labels(mixed)


def test_matching_source_identity_retains_the_sixty_row_overlap():
    canonical = source()
    signals = build_descriptive_signals(build_kospi200_features(canonical))
    outcome_labels = build_forward_labels(canonical)

    assert evaluate_signals(signals, outcome_labels)["observations"] == 60


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("ticker", "9999"),
        ("date_semantics", "UNVERIFIED_INTRADAY"),
    ],
)
def test_evaluation_rejects_label_source_identity_mismatch_before_metrics(
    column, value,
):
    canonical = source()
    signals = build_descriptive_signals(build_kospi200_features(canonical))
    mismatched = source()
    mismatched[column] = value
    outcome_labels = build_forward_labels(mismatched)

    with pytest.raises(ValueError, match="signal/label source identity differs"):
        evaluate_signals(signals, outcome_labels)


def test_future_change_does_not_leak_beyond_declared_label_horizon():
    original = source()
    changed = original.copy(); changed.loc[100:, "close"] *= 2
    left = build_forward_labels(original)
    right = build_forward_labels(changed)
    cutoff = original.iloc[39].date
    pd.testing.assert_frame_equal(
        left[left.observation_date <= cutoff].reset_index(drop=True),
        right[right.observation_date <= cutoff].reset_index(drop=True),
    )


@pytest.mark.parametrize(
    "invalid_date",
    [
        pytest.param("2024-1-02", id="nonpadded-month"),
        pytest.param("2024-01-2", id="nonpadded-day"),
        pytest.param(" 2024-01-02", id="leading-space"),
        pytest.param("2024-01-02 ", id="trailing-space"),
        pytest.param("2024/01/02", id="alternate-separator"),
        pytest.param("2024-02-30", id="invalid-calendar-date"),
        pytest.param("0001-01-01", id="below-pandas-bound"),
        pytest.param("9999-12-31", id="above-pandas-bound"),
        pytest.param(pd.Timestamp("2024-01-02"), id="timestamp"),
        pytest.param(pd.Timestamp("2024-01-02").date(), id="date-object"),
        pytest.param(datetime(2024, 1, 2), id="datetime-object"),
        pytest.param(20240102, id="integer"),
        pytest.param(2024.0102, id="float"),
        pytest.param(True, id="boolean"),
        pytest.param(None, id="none"),
        pytest.param(pd.NA, id="pandas-missing"),
        pytest.param("0001-01-01", id="before-pandas-range"),
        pytest.param("2262-04-12", id="after-pandas-range"),
        pytest.param("9999-12-31", id="maximum-calendar-year"),
    ],
)
def test_label_source_dates_require_byte_exact_canonical_strings(invalid_date):
    frame = source()
    frame["date"] = frame["date"].astype("object")
    frame.at[0, "date"] = invalid_date
    before = frame.copy(deep=True)

    with pytest.raises(ValueError) as error:
        build_forward_labels(frame)

    assert str(error.value) == (
        "label source dates must be canonical YYYY-MM-DD strings"
    )
    pd.testing.assert_frame_equal(frame, before)


def test_label_date_error_precedes_identity_and_numeric_conversion(monkeypatch):
    frame = source()
    frame.at[0, "date"] = "2024-1-02"
    frame.at[0, "ticker"] = 1028
    monkeypatch.setattr(
        pd,
        "to_numeric",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("numeric conversion must not run")
        ),
    )

    with pytest.raises(ValueError, match="canonical YYYY-MM-DD strings"):
        build_forward_labels(frame)


def test_purged_embargoed_expanding_splits_do_not_overlap():
    splits = expanding_walk_forward(
        observations=1000, minimum_train=400, test_size=100, purge=60, embargo=5,
    )
    assert splits
    for split in splits:
        assert split.train_end + split.purge == split.test_start
        assert split.train_start == 0
        assert split.train_end <= split.test_start < split.test_end
    for previous, current in zip(splits, splits[1:]):
        assert current.test_start == previous.test_end + previous.embargo


def test_walk_forward_rejects_insufficient_or_invalid_dimensions():
    with pytest.raises(ValueError):
        expanding_walk_forward(observations=10, minimum_train=10, test_size=2, purge=1, embargo=0)
    with pytest.raises(ValueError):
        expanding_walk_forward(observations=100, minimum_train=50, test_size=10, purge=-1, embargo=0)
