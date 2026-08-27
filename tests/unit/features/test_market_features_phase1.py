from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from market_features.frozen import inspect_frozen_kospi200, verify_frozen_kospi200
from market_features.kospi200 import FEATURE_DEFINITIONS, build_kospi200_features
from market_features.types import FrozenInputManifest


def source(rows: int = 150) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=rows)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": 100.0 * np.cumprod(np.full(rows, 1.001)),
        "ticker": "1028",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
    })


def test_feature_contract_and_t_plus_one_clock_are_deterministic():
    first = build_kospi200_features(source())
    second = build_kospi200_features(source())
    pd.testing.assert_frame_equal(first, second)
    assert len(FEATURE_DEFINITIONS) == 6
    assert first.iloc[0].observation_date == source().iloc[60].date
    assert first["ticker"].eq("1028").all()
    assert first["date_semantics"].eq("KRX_TRADING_DATE_DAILY_FINAL").all()
    expected_next = source().iloc[61].date + "T09:00:00+09:00"
    assert first.iloc[0].usable_from == expected_next
    assert first.pit_status.eq("PIT_SAFE_EOD_T_PLUS_1").all()
    assert not any(column.startswith("forward_") for column in first.columns)


def test_future_source_change_does_not_change_prior_features():
    original = source()
    changed = original.copy()
    changed.loc[120:, "close"] *= 1.5
    left = build_kospi200_features(original)
    right = build_kospi200_features(changed)
    cutoff = original.iloc[119].date
    pd.testing.assert_frame_equal(
        left[left.observation_date <= cutoff].reset_index(drop=True),
        right[right.observation_date <= cutoff].reset_index(drop=True),
    )


def test_invalid_identity_order_and_values_fail_closed():
    wrong = source(); wrong.loc[0, "ticker"] = "0000"
    with pytest.raises(ValueError, match="identity"):
        build_kospi200_features(wrong)
    coerced = source(); coerced["ticker"] = 1028
    with pytest.raises(ValueError, match="identity"):
        build_kospi200_features(coerced)
    duplicate = pd.concat([source(), source().iloc[[-1]]], ignore_index=True)
    with pytest.raises(ValueError, match="unique"):
        build_kospi200_features(duplicate)
    nonfinite = source(); nonfinite.loc[5, "close"] = np.inf
    with pytest.raises(ValueError, match="positive and finite"):
        build_kospi200_features(nonfinite)
    missing = source(); missing.loc[5, "close"] = np.nan
    with pytest.raises(ValueError, match="positive and finite"):
        build_kospi200_features(missing)


def test_label_namespace_cannot_enter_feature_builder():
    contaminated = source()
    contaminated["forward_return_20d"] = 0.0
    with pytest.raises(ValueError, match="label namespace"):
        build_kospi200_features(contaminated)


@pytest.mark.parametrize(
    "invalid_date",
    [
        pytest.param("2025-1-02", id="nonpadded-month"),
        pytest.param("2025-01-2", id="nonpadded-day"),
        pytest.param(" 2025-01-02", id="leading-space"),
        pytest.param("2025-01-02 ", id="trailing-space"),
        pytest.param("2025/01/02", id="alternate-separator"),
        pytest.param("2025-02-30", id="invalid-calendar-date"),
        pytest.param("0001-01-01", id="below-pandas-bound"),
        pytest.param("9999-12-31", id="above-pandas-bound"),
        pytest.param(pd.Timestamp("2025-01-02"), id="timestamp"),
        pytest.param(pd.Timestamp("2025-01-02").date(), id="date-object"),
        pytest.param(datetime(2025, 1, 2), id="datetime-object"),
        pytest.param(20250102, id="integer"),
        pytest.param(2025.0102, id="float"),
        pytest.param(True, id="boolean"),
        pytest.param(None, id="none"),
        pytest.param(pd.NA, id="pandas-missing"),
        pytest.param("0001-01-01", id="before-pandas-range"),
        pytest.param("2262-04-12", id="after-pandas-range"),
        pytest.param("9999-12-31", id="maximum-calendar-year"),
    ],
)
def test_feature_source_dates_require_byte_exact_canonical_strings(invalid_date):
    frame = source()
    frame["date"] = frame["date"].astype("object")
    frame.at[0, "date"] = invalid_date
    before = frame.copy(deep=True)

    with pytest.raises(ValueError) as error:
        build_kospi200_features(frame)

    assert str(error.value) == "source dates must be canonical YYYY-MM-DD strings"
    pd.testing.assert_frame_equal(frame, before)


def test_feature_date_error_precedes_numeric_conversion(monkeypatch):
    frame = source()
    frame.at[0, "date"] = "2025-1-02"
    monkeypatch.setattr(
        pd,
        "to_numeric",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("numeric conversion must not run")
        ),
    )

    with pytest.raises(ValueError, match="canonical YYYY-MM-DD strings"):
        build_kospi200_features(frame)


def test_frozen_manifest_requires_real_digest_and_positive_counts():
    manifest = FrozenInputManifest(
        "kr_kospi200_index_daily", 1, "1990-01-03", "2026-08-14", 9447,
        37, 738068, "a" * 64, "T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION",
    )
    assert manifest.rows == 9447
    with pytest.raises(ValueError, match="SHA-256"):
        FrozenInputManifest(
            "x", 1, "a", "b", 1, 1, 1, "bad",
            "T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION",
        )


def test_frozen_manifest_exact_reproducibility_detects_one_file_mutation(tmp_path):
    root = tmp_path / "frozen"
    for year, values in ((2024, ["2024-12-30", "2024-12-31"]), (2025, ["2025-01-02"])):
        path = root / f"year={year}" / "data.parquet"
        path.parent.mkdir(parents=True)
        pd.DataFrame({"date": values, "close": [100.0] * len(values)}).to_parquet(path, index=False)
    expected = inspect_frozen_kospi200(root)
    assert verify_frozen_kospi200(root, expected) == expected
    changed_path = root / "year=2025/data.parquet"
    changed = pd.read_parquet(changed_path)
    changed.loc[0, "close"] = 101.0
    changed.to_parquet(changed_path, index=False)
    with pytest.raises(ValueError, match="root_manifest_sha256"):
        verify_frozen_kospi200(root, expected)
