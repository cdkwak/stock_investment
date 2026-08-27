from dataclasses import replace
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from market_backtest.labels import build_forward_labels
from market_backtest.holdout import CoverageHoldout, define_untouched_holdout, development_only


def test_coverage_only_holdout_is_frozen_without_outcome_input():
    dates = pd.Series(pd.bdate_range("2015-01-02", "2026-08-14").strftime("%Y-%m-%d"))
    policy = define_untouched_holdout(dates)
    assert policy.policy_id == "UNTOUCHED_FINAL_5_CALENDAR_YEARS"
    assert policy.holdout_start == "2021-08-16"
    assert policy.results_reviewed is False
    assert policy.development_observations + policy.holdout_observations == len(dates)
    frame = pd.DataFrame({"observation_date": dates, "feature": range(len(dates))})
    development = development_only(frame, policy)
    assert development.observation_date.max() < policy.holdout_start


def test_holdout_policy_rejects_reviewed_state_or_invalid_coverage():
    policy = CoverageHoldout("x", "2020-01-01", "2021-01-01", "2020-07-01", 10, 10)
    with pytest.raises(ValueError, match="untouched"):
        replace(policy, results_reviewed=True)
    duplicate = pd.Series(["2020-01-01", "2020-01-01"])
    with pytest.raises(ValueError, match="unique"):
        define_untouched_holdout(duplicate, final_calendar_years=1)


@pytest.mark.parametrize(
    "invalid_date",
    [
        pytest.param("2020-1-02", id="nonpadded-month"),
        pytest.param("2020-01-2", id="nonpadded-day"),
        pytest.param(" 2020-01-02", id="leading-space"),
        pytest.param("2020-01-02 ", id="trailing-space"),
        pytest.param("2020/01/02", id="alternate-separator"),
        pytest.param("2020-02-30", id="invalid-calendar-date"),
        pytest.param("0001-01-01", id="below-pandas-bound"),
        pytest.param("9999-12-31", id="above-pandas-bound"),
        pytest.param(pd.Timestamp("2020-01-02"), id="timestamp"),
        pytest.param(pd.Timestamp("2020-01-02").date(), id="date-object"),
        pytest.param(datetime(2020, 1, 2), id="datetime-object"),
        pytest.param(20200102, id="integer"),
        pytest.param(2020.0102, id="float"),
        pytest.param(True, id="boolean"),
        pytest.param(None, id="none"),
        pytest.param(pd.NA, id="pandas-missing"),
        pytest.param("0001-01-01", id="before-pandas-range"),
        pytest.param("2262-04-12", id="after-pandas-range"),
        pytest.param("9999-12-31", id="maximum-calendar-year"),
    ],
)
def test_holdout_dates_require_byte_exact_canonical_strings(invalid_date):
    dates = pd.Series(
        [invalid_date, "2020-01-03", "2021-01-04"], dtype="object",
    )
    before = dates.copy(deep=True)

    with pytest.raises(ValueError) as coverage_error:
        define_untouched_holdout(dates, final_calendar_years=1)

    assert str(coverage_error.value) == (
        "holdout coverage dates must be canonical YYYY-MM-DD strings"
    )
    pd.testing.assert_series_equal(dates, before)

    policy = CoverageHoldout(
        "x", "2019-01-01", "2021-01-01", "2020-01-01", 1, 2,
    )
    frame = pd.DataFrame({"observation_date": [invalid_date], "feature": [1]})
    frame_before = frame.copy(deep=True)

    with pytest.raises(ValueError) as development_error:
        development_only(frame, policy)

    assert str(development_error.value) == (
        "development observation dates must be canonical YYYY-MM-DD strings"
    )
    pd.testing.assert_frame_equal(frame, frame_before)


def test_development_boundary_rejects_noncanonical_observation_date_before_filter():
    policy = CoverageHoldout(
        "x", "2020-01-01", "2021-01-01", "2020-07-01", 10, 10,
    )
    frame = pd.DataFrame({
        "observation_date": ["2020-1-02"],
        "feature": [object()],
    })

    with pytest.raises(
        ValueError,
        match="development observation dates must be canonical YYYY-MM-DD strings",
    ):
        development_only(frame, policy)


def test_development_boundary_filters_parsed_dates_not_categorical_order():
    policy = CoverageHoldout(
        "x", "2020-01-01", "2021-01-01", "2020-07-01", 10, 10,
    )
    frame = pd.DataFrame({
        "observation_date": pd.Categorical(
            ["2020-08-01", "2020-06-01"],
            categories=["2020-08-01", "2020-07-01", "2020-06-01"],
            ordered=True,
        ),
        "feature": ["holdout", "development"],
    })
    before = frame.copy(deep=True)

    result = development_only(frame, policy)

    assert result.to_dict("records") == [{
        "observation_date": "2020-06-01", "feature": "development",
    }]
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("ordered", [False, True])
def test_development_filter_uses_calendar_dates_not_categorical_order(ordered):
    policy = CoverageHoldout(
        "x", "2019-01-01", "2021-01-01", "2020-01-01", 1, 2,
    )
    frame = pd.DataFrame({
        "observation_date": pd.Categorical(
            ["2019-01-01", "2020-01-01", "2021-01-01"],
            categories=["2021-01-01", "2019-01-01", "2020-01-01"],
            ordered=ordered,
        ),
        "feature": [1, 2, 3],
    })
    before = frame.copy(deep=True)

    development = development_only(frame, policy)

    assert development["observation_date"].astype("object").tolist() == [
        "2019-01-01",
    ]
    pd.testing.assert_frame_equal(frame, before)


def test_development_labels_are_fully_available_before_holdout():
    dates = pd.bdate_range("2014-01-02", "2026-08-14")
    source = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": 100.0 * np.cumprod(np.full(len(dates), 1.0001)),
        "ticker": "1028",
        "date_semantics": "KRX_TRADING_DATE_DAILY_FINAL",
    })
    policy = define_untouched_holdout(source["date"])
    changed = source.copy()
    changed.loc[changed["date"].ge(policy.holdout_start), "close"] *= 2.0

    original = development_only(build_forward_labels(source), policy)
    holdout_changed = development_only(build_forward_labels(changed), policy)

    pd.testing.assert_frame_equal(original, holdout_changed)
    available_at = pd.to_datetime(original["label_available_at"], format="ISO8601", utc=True)
    assert available_at.lt(pd.Timestamp(policy.holdout_start, tz="Asia/Seoul")).all()

    with pytest.raises(ValueError, match="required for label frames"):
        development_only(build_forward_labels(source).drop(columns="label_available_at"), policy)


@pytest.mark.parametrize("invalid", ["not-a-timestamp", None, "2020-01-02T15:30:00"])
def test_development_labels_reject_invalid_availability_timestamp(invalid):
    policy = CoverageHoldout("x", "2020-01-01", "2021-01-01", "2020-07-01", 10, 10)
    frame = pd.DataFrame({
        "observation_date": ["2020-01-02"],
        "label_available_at": [invalid],
    })

    with pytest.raises(ValueError, match="label_available_at"):
        development_only(frame, policy)
