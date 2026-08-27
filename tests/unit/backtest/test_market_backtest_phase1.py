from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import inspect
import json
import socket

import numpy as np
import pandas as pd
import pytest

from market_backtest.holdout import CoverageHoldout
from market_backtest.input_readiness import (
    INPUT_READINESS_SCHEMA,
    KOSPI200_INPUT_SPEC_V1,
    BacktestInputSpecV1,
    FrozenReadEvidenceV1,
    InputReadinessReason,
    InputReadinessState,
    assess_backtest_input_readiness_v1,
)
from market_backtest.labels import (
    LABEL_HORIZONS_TRADING_DAYS, LABEL_NAMESPACE,
    MAX_LABEL_HORIZON_TRADING_DAYS,
    build_forward_labels,
)
from market_backtest.portfolio import KOSPI200_FROZEN_HOLDOUT_V1
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


def _spaced_dates(start: str, end: str, count: int) -> tuple[str, ...]:
    available = pd.date_range(start, end, freq="D")
    positions = np.linspace(0, len(available) - 1, num=count, dtype="int64")
    result = tuple(available[positions].strftime("%Y-%m-%d"))
    assert len(result) == len(set(result)) == count
    assert result[0] == start and result[-1] == end
    return result


def readiness_calendar() -> tuple[str, ...]:
    return (
        *_spaced_dates("1990-01-03", "2021-08-13", 8225),
        *_spaced_dates("2021-08-17", "2026-08-14", 1222),
    )


def readiness_features(
    calendar: tuple[str, ...], indices: tuple[int, ...] = (60, 61, 123),
) -> pd.DataFrame:
    observation_dates = [calendar[index] for index in indices]
    next_dates = [calendar[index + 1] for index in indices]
    rows = len(indices)
    return pd.DataFrame({
        "observation_date": observation_dates,
        "ticker": [_TICKER_FOR_TEST] * rows,
        "date_semantics": ["KRX_TRADING_DATE_DAILY_FINAL"] * rows,
        "observation_time": [
            value + "T15:30:00+09:00" for value in observation_dates
        ],
        "available_at": [
            value + "T15:30:00+09:00" for value in observation_dates
        ],
        "usable_from": [value + "T09:00:00+09:00" for value in next_dates],
        "return_5d": [0.01] * rows,
        "return_20d": [0.02] * rows,
        "return_60d": [0.03] * rows,
        "realized_volatility_20d": [0.04] * rows,
        "ma_distance_60d": [0.05] * rows,
        "rolling_drawdown_60d": [-0.06] * rows,
        "source_dataset": ["kr_kospi200_index_daily"] * rows,
        "source_contract_version": [1] * rows,
        "feature_set_version": [1] * rows,
        "pit_status": ["PIT_SAFE_EOD_T_PLUS_1"] * rows,
    })


_TICKER_FOR_TEST = "1028"


def ready_input_arguments() -> list[object]:
    calendar = readiness_calendar()
    spec = KOSPI200_INPUT_SPEC_V1
    evidence = FrozenReadEvidenceV1(spec.manifest, spec.manifest)
    return [
        spec,
        evidence,
        calendar,
        readiness_features(calendar),
        KOSPI200_FROZEN_HOLDOUT_V1,
    ]


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


def test_input_readiness_public_contract_is_exact_and_closed():
    assert INPUT_READINESS_SCHEMA == "backtest-input-readiness/v1"
    assert {state.value for state in InputReadinessState} == {
        "READY", "NOT_AVAILABLE", "BLOCKED",
    }
    assert {reason.value for reason in InputReadinessReason} == {
        "READY",
        "INPUT_NOT_AVAILABLE",
        "SCHEMA_INVALID",
        "ORDERED_DATE_KEY_INVALID",
        "MANIFEST_IDENTITY_MISMATCH",
        "FROZEN_DIGEST_MISMATCH",
        "FROZEN_READ_CHANGED",
        "SOURCE_IDENTITY_MISMATCH",
        "FINALITY_NOT_PROVEN",
        "FEATURE_VERSION_MISMATCH",
        "CLOCK_MISMATCH",
        "PIT_NOT_SAFE",
        "LABEL_NAMESPACE_PRESENT",
        "HOLDOUT_NOT_SEALED",
        "HOLDOUT_CROSSED",
        "PURGE_LT_LABEL_HORIZON",
    }
    manifest = KOSPI200_INPUT_SPEC_V1.manifest
    assert (
        manifest.dataset,
        manifest.contract_version,
        manifest.coverage_start,
        manifest.coverage_end,
        manifest.rows,
        manifest.files,
        manifest.bytes,
        manifest.root_manifest_sha256,
        manifest.decision_rule,
    ) == (
        "kr_kospi200_index_daily",
        1,
        "1990-01-03",
        "2026-08-14",
        9447,
        37,
        738068,
        "a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713",
        "T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION",
    )
    assert (
        KOSPI200_INPUT_SPEC_V1.ticker,
        KOSPI200_INPUT_SPEC_V1.date_semantics,
        KOSPI200_INPUT_SPEC_V1.feature_set_version,
        KOSPI200_INPUT_SPEC_V1.pit_status,
        KOSPI200_INPUT_SPEC_V1.label_horizon_sessions,
        KOSPI200_INPUT_SPEC_V1.purge_sessions,
        KOSPI200_INPUT_SPEC_V1.embargo_sessions,
    ) == (
        "1028",
        "KRX_TRADING_DATE_DAILY_FINAL",
        1,
        "PIT_SAFE_EOD_T_PLUS_1",
        60,
        60,
        5,
    )
    assert tuple(inspect.signature(assess_backtest_input_readiness_v1).parameters) == (
        "spec",
        "frozen_read",
        "retained_calendar",
        "development_feature_rows",
        "holdout",
    )


def test_input_readiness_ready_is_provider_free_canonical_and_replay_stable(
    monkeypatch: pytest.MonkeyPatch,
):
    arguments = ready_input_arguments()
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("network access is forbidden")
        ),
    )

    first = assess_backtest_input_readiness_v1(*arguments)
    second = assess_backtest_input_readiness_v1(*arguments)

    assert first == second
    assert first.state is InputReadinessState.READY
    assert first.reason is InputReadinessReason.READY
    assert first.schema == INPUT_READINESS_SCHEMA
    assert first.declared_manifest == KOSPI200_INPUT_SPEC_V1.manifest
    assert first.pre_read_manifest == first.post_read_manifest == first.declared_manifest
    assert first.holdout == KOSPI200_FROZEN_HOLDOUT_V1
    assert first.retained_calendar_rows == 9447
    assert first.development_feature_rows == 3
    assert hashlib.sha256(first.canonical_json).hexdigest() == first.receipt_sha256
    payload = json.loads(first.canonical_json)
    assert payload["state"] == "READY"
    assert payload["holdout"]["results_reviewed"] is False
    assert payload["split"] == {
        "embargo_sessions": 5,
        "label_horizon_sessions": 60,
        "policy": "PURGED_EXPANDING_WALK_FORWARD",
        "purge_sessions": 60,
    }

    changed_values = arguments[3].copy(deep=True)
    changed_values.loc[:, list(KOSPI200_INPUT_SPEC_V1.feature_names)] = 987654.321
    without_feature_values = assess_backtest_input_readiness_v1(
        arguments[0], arguments[1], arguments[2], changed_values, arguments[4],
    )
    assert without_feature_values == first
    assert b"987654.321" not in first.canonical_json


def test_input_readiness_uses_exact_next_retained_date_without_weekday_inference():
    arguments = ready_input_arguments()
    calendar = arguments[2]
    gap_index = next(
        index
        for index in range(60, 8224)
        if (
            datetime.fromisoformat(calendar[index + 1])
            - datetime.fromisoformat(calendar[index])
        ).days > 1
    )
    features = readiness_features(calendar, (gap_index,))

    receipt = assess_backtest_input_readiness_v1(
        arguments[0], arguments[1], calendar, features, arguments[4],
    )

    assert receipt.state is InputReadinessState.READY
    assert features.iloc[0].usable_from == calendar[gap_index + 1] + "T09:00:00+09:00"


@pytest.mark.parametrize("missing_index", range(5))
def test_input_readiness_absent_required_evidence_is_not_available(missing_index):
    arguments = ready_input_arguments()
    arguments[missing_index] = None

    receipt = assess_backtest_input_readiness_v1(*arguments)

    assert receipt.state is InputReadinessState.NOT_AVAILABLE
    assert receipt.reason is InputReadinessReason.INPUT_NOT_AVAILABLE


def test_input_readiness_absent_pre_or_post_manifest_is_not_available():
    arguments = ready_input_arguments()
    manifest = KOSPI200_INPUT_SPEC_V1.manifest
    for evidence in (
        FrozenReadEvidenceV1(None, manifest),
        FrozenReadEvidenceV1(manifest, None),
    ):
        receipt = assess_backtest_input_readiness_v1(
            arguments[0], evidence, arguments[2], arguments[3], arguments[4],
        )
        assert receipt.state is InputReadinessState.NOT_AVAILABLE
        assert receipt.reason is InputReadinessReason.INPUT_NOT_AVAILABLE


@pytest.mark.parametrize(
    "reason",
    [
        InputReadinessReason.SCHEMA_INVALID,
        InputReadinessReason.ORDERED_DATE_KEY_INVALID,
        InputReadinessReason.MANIFEST_IDENTITY_MISMATCH,
        InputReadinessReason.FROZEN_DIGEST_MISMATCH,
        InputReadinessReason.FROZEN_READ_CHANGED,
        InputReadinessReason.SOURCE_IDENTITY_MISMATCH,
        InputReadinessReason.FINALITY_NOT_PROVEN,
        InputReadinessReason.FEATURE_VERSION_MISMATCH,
        InputReadinessReason.CLOCK_MISMATCH,
        InputReadinessReason.PIT_NOT_SAFE,
        InputReadinessReason.LABEL_NAMESPACE_PRESENT,
        InputReadinessReason.HOLDOUT_NOT_SEALED,
        InputReadinessReason.HOLDOUT_CROSSED,
        InputReadinessReason.PURGE_LT_LABEL_HORIZON,
    ],
)
def test_input_readiness_every_unsafe_mutation_has_a_stable_blocked_reason(reason):
    spec, evidence, calendar, features, holdout = ready_input_arguments()
    if reason is InputReadinessReason.SCHEMA_INVALID:
        features = features.drop(columns="return_5d")
    elif reason is InputReadinessReason.ORDERED_DATE_KEY_INVALID:
        features.at[1, "observation_date"] = features.at[0, "observation_date"]
    elif reason is InputReadinessReason.MANIFEST_IDENTITY_MISMATCH:
        spec = replace(spec, manifest=replace(spec.manifest, files=38))
    elif reason is InputReadinessReason.FROZEN_DIGEST_MISMATCH:
        spec = replace(
            spec,
            manifest=replace(spec.manifest, root_manifest_sha256="0" * 64),
        )
    elif reason is InputReadinessReason.FROZEN_READ_CHANGED:
        evidence = replace(
            evidence,
            post_read_manifest=replace(spec.manifest, rows=spec.manifest.rows + 1),
        )
    elif reason is InputReadinessReason.SOURCE_IDENTITY_MISMATCH:
        features.at[0, "ticker"] = "9999"
    elif reason is InputReadinessReason.FINALITY_NOT_PROVEN:
        features.at[0, "date_semantics"] = "UNVERIFIED"
    elif reason is InputReadinessReason.FEATURE_VERSION_MISMATCH:
        features.at[0, "feature_set_version"] = 2
    elif reason is InputReadinessReason.CLOCK_MISMATCH:
        features.at[0, "usable_from"] = features.at[0, "observation_time"]
    elif reason is InputReadinessReason.PIT_NOT_SAFE:
        features.at[0, "pit_status"] = "PIT_BLOCKED"
    elif reason is InputReadinessReason.LABEL_NAMESPACE_PRESENT:
        features["outcome_hidden"] = 1.0
    elif reason is InputReadinessReason.HOLDOUT_NOT_SEALED:
        holdout = replace(holdout, results_reviewed=0)
    elif reason is InputReadinessReason.HOLDOUT_CROSSED:
        features.at[len(features) - 1, "observation_date"] = holdout.holdout_start
    elif reason is InputReadinessReason.PURGE_LT_LABEL_HORIZON:
        spec = replace(spec, purge_sessions=59)
    else:  # pragma: no cover - the closed enum list makes this unreachable
        raise AssertionError(reason)

    receipt = assess_backtest_input_readiness_v1(
        spec, evidence, calendar, features, holdout,
    )

    assert receipt.state is InputReadinessState.BLOCKED
    assert receipt.reason is reason
    assert hashlib.sha256(receipt.canonical_json).hexdigest() == receipt.receipt_sha256


@pytest.mark.parametrize(
    "column",
    sorted({
        "forward_hidden",
        "future_hidden",
        "label_hidden",
        "outcome_hidden",
        "mae_hidden",
        "mfe_hidden",
        *LABEL_NAMESPACE,
    }),
)
def test_input_readiness_rejects_every_label_and_outcome_namespace(column):
    arguments = ready_input_arguments()
    arguments[3][column] = 1.0

    receipt = assess_backtest_input_readiness_v1(*arguments)

    assert receipt.state is InputReadinessState.BLOCKED
    assert receipt.reason is InputReadinessReason.LABEL_NAMESPACE_PRESENT


def test_input_readiness_rejects_non_bool_false_holdout_state():
    arguments = ready_input_arguments()
    arguments[4] = replace(arguments[4], results_reviewed=0)

    receipt = assess_backtest_input_readiness_v1(*arguments)

    assert receipt.state is InputReadinessState.BLOCKED
    assert receipt.reason is InputReadinessReason.HOLDOUT_NOT_SEALED
    assert receipt.holdout is None


def test_input_readiness_holdout_date_short_circuits_non_date_value_access():
    class HoldoutSentinel:
        def __eq__(self, _other):
            raise AssertionError("non-date equality must not run")

        def __float__(self):
            raise AssertionError("non-date conversion must not run")

    arguments = ready_input_arguments()
    sentinel = HoldoutSentinel()
    frame = pd.DataFrame({
        column: [
            KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
            if column == "observation_date"
            else sentinel
        ]
        for column in arguments[3].columns
    })

    receipt = assess_backtest_input_readiness_v1(
        arguments[0], arguments[1], arguments[2], frame, arguments[4],
    )

    assert receipt.state is InputReadinessState.BLOCKED
    assert receipt.reason is InputReadinessReason.HOLDOUT_CROSSED


def test_input_readiness_purge_must_cover_the_declared_label_horizon():
    arguments = ready_input_arguments()
    arguments[0] = BacktestInputSpecV1(
        label_horizon_sessions=60,
        purge_sessions=59,
    )

    receipt = assess_backtest_input_readiness_v1(*arguments)

    assert receipt.state is InputReadinessState.BLOCKED
    assert receipt.reason is InputReadinessReason.PURGE_LT_LABEL_HORIZON
