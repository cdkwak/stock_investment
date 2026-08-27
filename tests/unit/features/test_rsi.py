from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_features.rsi import build_wilder_rsi14


def source(closes: tuple[float, ...]) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=len(closes))
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": pd.Series(closes, dtype="float64"),
        "ticker": "1028",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
    })


def reference_wilder(values: tuple[float, ...]) -> tuple[float, ...]:
    delta = np.diff(np.asarray(values, dtype="float64"))
    gain = np.maximum(delta, 0.0)
    loss = np.maximum(-delta, 0.0)
    average_gain = float(gain[:14].mean())
    average_loss = float(loss[:14].mean())

    def value() -> float:
        if average_gain == average_loss == 0.0:
            return 50.0
        if average_loss == 0.0:
            return 100.0
        if average_gain == 0.0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + average_gain / average_loss)

    output = [value()]
    for index in range(14, len(delta)):
        average_gain = (average_gain * 13.0 + float(gain[index])) / 14.0
        average_loss = (average_loss * 13.0 + float(loss[index])) / 14.0
        output.append(value())
    return tuple(output[:-1])  # The final observation has no retained T+1 use.


def test_wilder_rsi14_exact_seed_recurrence_and_t_plus_one_clock():
    closes = tuple(float(value) for value in (
        44, 44.15, 43.9, 44.35, 44.9, 45.1, 44.8, 45.3, 45.8, 45.6,
        46.0, 45.7, 46.4, 46.2, 46.8, 47.1, 46.6, 47.4, 47.0, 47.8,
    ))
    frame = source(closes)

    result = build_wilder_rsi14(frame)

    assert result["rsi_14"].tolist() == pytest.approx(reference_wilder(closes))
    assert result["observation_date"].iloc[0] == frame["date"].iloc[14]
    assert result["usable_from"].iloc[0] == frame["date"].iloc[15] + "T09:00:00+09:00"
    assert result["available_at"].iloc[0] == frame["date"].iloc[14] + "T15:30:00+09:00"
    assert result["pit_status"].eq("PIT_SAFE_EOD_T_PLUS_1").all()


def test_flat_gain_only_and_loss_only_windows_have_explicit_values():
    flat = build_wilder_rsi14(source(tuple([100.0] * 18)))
    rising = build_wilder_rsi14(source(tuple(float(value) for value in range(100, 118))))
    falling = build_wilder_rsi14(source(tuple(float(value) for value in range(118, 100, -1))))

    assert flat["rsi_14"].eq(50.0).all()
    assert rising["rsi_14"].eq(100.0).all()
    assert falling["rsi_14"].eq(0.0).all()


def test_insufficient_warmup_is_typed_empty_without_imputation():
    result = build_wilder_rsi14(source(tuple(float(value) for value in range(100, 115))))

    assert result.empty
    assert "rsi_14" in result.columns


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0])
def test_missing_nonfinite_or_nonpositive_close_fails_closed(bad):
    frame = source(tuple(float(value) for value in range(100, 118)))
    frame.loc[5, "close"] = bad

    with pytest.raises(ValueError, match="finite, and positive"):
        build_wilder_rsi14(frame)


def test_unsorted_duplicate_wrong_identity_and_outcome_namespace_fail_closed():
    frame = source(tuple(float(value) for value in range(100, 118)))
    frame.loc[2, "date"] = frame.loc[1, "date"]
    with pytest.raises(ValueError, match="unique and sorted"):
        build_wilder_rsi14(frame)

    frame = source(tuple(float(value) for value in range(100, 118)))
    frame["ticker"] = "OTHER"
    with pytest.raises(ValueError, match="identity"):
        build_wilder_rsi14(frame)

    frame = source(tuple(float(value) for value in range(100, 118)))
    frame["forward_return_20d"] = 0.0
    with pytest.raises(ValueError, match="outcome namespace"):
        build_wilder_rsi14(frame)


def test_later_source_edit_does_not_change_earlier_rsi_rows():
    closes = tuple(float(value) for value in range(100, 125))
    original = build_wilder_rsi14(source(closes))
    changed = list(closes)
    changed[-2] = 500.0
    revised = build_wilder_rsi14(source(tuple(changed)))

    pd.testing.assert_frame_equal(original.iloc[:-1], revised.iloc[:-1])
