from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import StrEnum
import hashlib
import json
import math

import pandas as pd

from market_features.types import FrozenInputManifest

from .holdout import CoverageHoldout
from .labels import LABEL_NAMESPACE


INPUT_READINESS_SCHEMA = "backtest-input-readiness/v1"

_EXPECTED_MANIFEST = FrozenInputManifest(
    dataset="kr_kospi200_index_daily",
    contract_version=1,
    coverage_start="1990-01-03",
    coverage_end="2026-08-14",
    rows=9447,
    files=37,
    bytes=738068,
    root_manifest_sha256=(
        "a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713"
    ),
    decision_rule="T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION",
)
_EXPECTED_HOLDOUT = CoverageHoldout(
    policy_id="UNTOUCHED_FINAL_5_CALENDAR_YEARS",
    coverage_start="1990-01-03",
    coverage_end="2026-08-14",
    holdout_start="2021-08-17",
    development_observations=8225,
    holdout_observations=1222,
    results_reviewed=False,
)
_EXPECTED_FEATURE_NAMES = (
    "return_5d",
    "return_20d",
    "return_60d",
    "realized_volatility_20d",
    "ma_distance_60d",
    "rolling_drawdown_60d",
)
_METADATA_COLUMNS = (
    "observation_date",
    "ticker",
    "date_semantics",
    "observation_time",
    "available_at",
    "usable_from",
    "source_dataset",
    "source_contract_version",
    "feature_set_version",
    "pit_status",
)
_EXPECTED_COLUMNS = frozenset((*_METADATA_COLUMNS, *_EXPECTED_FEATURE_NAMES))
_TICKER = "1028"
_DATE_SEMANTICS = "KRX_TRADING_DATE_DAILY_FINAL"
_PIT_STATUS = "PIT_SAFE_EOD_T_PLUS_1"
_SPLIT_POLICY = "PURGED_EXPANDING_WALK_FORWARD"
_LABEL_HORIZON_SESSIONS = 60
_PURGE_SESSIONS = 60
_EMBARGO_SESSIONS = 5
_LABEL_PREFIXES = ("forward_", "future_", "label_", "outcome_", "mae_", "mfe_")


class InputReadinessState(StrEnum):
    READY = "READY"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    BLOCKED = "BLOCKED"


class InputReadinessReason(StrEnum):
    READY = "READY"
    INPUT_NOT_AVAILABLE = "INPUT_NOT_AVAILABLE"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    ORDERED_DATE_KEY_INVALID = "ORDERED_DATE_KEY_INVALID"
    MANIFEST_IDENTITY_MISMATCH = "MANIFEST_IDENTITY_MISMATCH"
    FROZEN_DIGEST_MISMATCH = "FROZEN_DIGEST_MISMATCH"
    FROZEN_READ_CHANGED = "FROZEN_READ_CHANGED"
    SOURCE_IDENTITY_MISMATCH = "SOURCE_IDENTITY_MISMATCH"
    FINALITY_NOT_PROVEN = "FINALITY_NOT_PROVEN"
    FEATURE_VERSION_MISMATCH = "FEATURE_VERSION_MISMATCH"
    CLOCK_MISMATCH = "CLOCK_MISMATCH"
    PIT_NOT_SAFE = "PIT_NOT_SAFE"
    LABEL_NAMESPACE_PRESENT = "LABEL_NAMESPACE_PRESENT"
    HOLDOUT_NOT_SEALED = "HOLDOUT_NOT_SEALED"
    HOLDOUT_CROSSED = "HOLDOUT_CROSSED"
    PURGE_LT_LABEL_HORIZON = "PURGE_LT_LABEL_HORIZON"


@dataclass(frozen=True, slots=True)
class BacktestInputSpecV1:
    manifest: FrozenInputManifest = field(default_factory=lambda: _EXPECTED_MANIFEST)
    ticker: str = _TICKER
    date_semantics: str = _DATE_SEMANTICS
    feature_set_version: int = 1
    feature_names: tuple[str, ...] = _EXPECTED_FEATURE_NAMES
    pit_status: str = _PIT_STATUS
    split_policy: str = _SPLIT_POLICY
    label_horizon_sessions: int = _LABEL_HORIZON_SESSIONS
    purge_sessions: int = _PURGE_SESSIONS
    embargo_sessions: int = _EMBARGO_SESSIONS


KOSPI200_INPUT_SPEC_V1 = BacktestInputSpecV1()


@dataclass(frozen=True, slots=True)
class FrozenReadEvidenceV1:
    pre_read_manifest: FrozenInputManifest | None
    post_read_manifest: FrozenInputManifest | None


@dataclass(frozen=True, slots=True)
class BacktestInputReadinessReceiptV1:
    schema: str
    state: InputReadinessState
    reason: InputReadinessReason
    declared_manifest: FrozenInputManifest | None
    pre_read_manifest: FrozenInputManifest | None
    post_read_manifest: FrozenInputManifest | None
    ticker: str | None
    date_semantics: str | None
    feature_set_version: int | None
    feature_columns: tuple[str, ...]
    pit_status: str | None
    retained_calendar_rows: int | None
    retained_calendar_sha256: str | None
    development_feature_rows: int | None
    feature_metadata_sha256: str | None
    split_policy: str | None
    label_horizon_sessions: int | None
    purge_sessions: int | None
    embargo_sessions: int | None
    holdout: CoverageHoldout | None
    canonical_json: bytes
    receipt_sha256: str


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _manifest_has_scalar_shape(value: object) -> bool:
    return (
        isinstance(value, FrozenInputManifest)
        and type(value.dataset) is str
        and type(value.contract_version) is int
        and type(value.coverage_start) is str
        and type(value.coverage_end) is str
        and type(value.rows) is int
        and type(value.files) is int
        and type(value.bytes) is int
        and type(value.root_manifest_sha256) is str
        and type(value.decision_rule) is str
    )


def _safe_manifest(value: object) -> FrozenInputManifest | None:
    return value if _manifest_has_scalar_shape(value) else None


def _holdout_has_scalar_shape(value: object) -> bool:
    return (
        isinstance(value, CoverageHoldout)
        and type(value.policy_id) is str
        and type(value.coverage_start) is str
        and type(value.coverage_end) is str
        and type(value.holdout_start) is str
        and type(value.development_observations) is int
        and type(value.holdout_observations) is int
        and type(value.results_reviewed) is bool
    )


def _receipt(
    state: InputReadinessState,
    reason: InputReadinessReason,
    *,
    spec: object,
    frozen_read: object,
    holdout: object,
    calendar_values: tuple[str, ...] | None = None,
    feature_rows: int | None = None,
    feature_metadata: tuple[tuple[object, ...], ...] | None = None,
) -> BacktestInputReadinessReceiptV1:
    declared_manifest = _safe_manifest(getattr(spec, "manifest", None))
    pre_read_manifest = _safe_manifest(
        getattr(frozen_read, "pre_read_manifest", None)
    )
    post_read_manifest = _safe_manifest(
        getattr(frozen_read, "post_read_manifest", None)
    )
    safe_holdout = holdout if _holdout_has_scalar_shape(holdout) else None
    safe_spec = isinstance(spec, BacktestInputSpecV1)
    feature_columns = (
        tuple(spec.feature_names)
        if safe_spec
        and type(spec.feature_names) is tuple
        and all(type(name) is str for name in spec.feature_names)
        else ()
    )
    calendar_digest = (
        hashlib.sha256(_canonical_json(calendar_values)).hexdigest()
        if calendar_values is not None
        else None
    )
    metadata_digest = (
        hashlib.sha256(_canonical_json(feature_metadata)).hexdigest()
        if feature_metadata is not None
        else None
    )
    fields = {
        "schema": INPUT_READINESS_SCHEMA,
        "state": state.value,
        "reason": reason.value,
        "input": {
            "declared_manifest": (
                asdict(declared_manifest) if declared_manifest is not None else None
            ),
            "pre_read_manifest": (
                asdict(pre_read_manifest) if pre_read_manifest is not None else None
            ),
            "post_read_manifest": (
                asdict(post_read_manifest) if post_read_manifest is not None else None
            ),
            "ticker": spec.ticker if safe_spec and type(spec.ticker) is str else None,
            "date_semantics": (
                spec.date_semantics
                if safe_spec and type(spec.date_semantics) is str
                else None
            ),
            "feature_set_version": (
                spec.feature_set_version
                if safe_spec and type(spec.feature_set_version) is int
                else None
            ),
            "feature_columns": feature_columns,
            "pit_status": (
                spec.pit_status
                if safe_spec and type(spec.pit_status) is str
                else None
            ),
        },
        "calendar": {
            "rows": len(calendar_values) if calendar_values is not None else None,
            "sha256": calendar_digest,
        },
        "development_features": {
            "rows": feature_rows,
            "metadata_sha256": metadata_digest,
        },
        "split": {
            "policy": (
                spec.split_policy
                if safe_spec and type(spec.split_policy) is str
                else None
            ),
            "label_horizon_sessions": (
                spec.label_horizon_sessions
                if safe_spec and type(spec.label_horizon_sessions) is int
                else None
            ),
            "purge_sessions": (
                spec.purge_sessions
                if safe_spec and type(spec.purge_sessions) is int
                else None
            ),
            "embargo_sessions": (
                spec.embargo_sessions
                if safe_spec and type(spec.embargo_sessions) is int
                else None
            ),
        },
        "holdout": asdict(safe_holdout) if safe_holdout is not None else None,
    }
    canonical_json = _canonical_json(fields)
    receipt_sha256 = hashlib.sha256(canonical_json).hexdigest()
    return BacktestInputReadinessReceiptV1(
        schema=INPUT_READINESS_SCHEMA,
        state=state,
        reason=reason,
        declared_manifest=declared_manifest,
        pre_read_manifest=pre_read_manifest,
        post_read_manifest=post_read_manifest,
        ticker=fields["input"]["ticker"],
        date_semantics=fields["input"]["date_semantics"],
        feature_set_version=fields["input"]["feature_set_version"],
        feature_columns=feature_columns,
        pit_status=fields["input"]["pit_status"],
        retained_calendar_rows=fields["calendar"]["rows"],
        retained_calendar_sha256=calendar_digest,
        development_feature_rows=feature_rows,
        feature_metadata_sha256=metadata_digest,
        split_policy=fields["split"]["policy"],
        label_horizon_sessions=fields["split"]["label_horizon_sessions"],
        purge_sessions=fields["split"]["purge_sessions"],
        embargo_sessions=fields["split"]["embargo_sessions"],
        holdout=safe_holdout,
        canonical_json=canonical_json,
        receipt_sha256=receipt_sha256,
    )


def _blocked(
    reason: InputReadinessReason,
    *,
    spec: object,
    frozen_read: object,
    holdout: object,
    calendar_values: tuple[str, ...] | None = None,
    feature_rows: int | None = None,
) -> BacktestInputReadinessReceiptV1:
    return _receipt(
        InputReadinessState.BLOCKED,
        reason,
        spec=spec,
        frozen_read=frozen_read,
        holdout=holdout,
        calendar_values=calendar_values,
        feature_rows=feature_rows,
    )


def _canonical_dates(values: object) -> tuple[tuple[str, ...], tuple[date, ...]] | None:
    try:
        if isinstance(values, (pd.Series, pd.Index)):
            raw = values.tolist()
        elif isinstance(values, Sequence) and not isinstance(
            values, (str, bytes, bytearray)
        ):
            raw = list(values)
        else:
            return None
    except Exception:
        return None
    canonical: list[str] = []
    parsed: list[date] = []
    for value in raw:
        if type(value) is not str:
            return None
        try:
            parsed_value = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
        if parsed_value.isoformat() != value:
            return None
        canonical.append(value)
        parsed.append(parsed_value)
    return tuple(canonical), tuple(parsed)


def _all_exact(values: pd.Series, expected: object, expected_type: type) -> bool:
    try:
        return all(
            type(value) is expected_type and value == expected
            for value in values.tolist()
        )
    except Exception:
        return False


def _has_label_namespace(columns: tuple[str, ...]) -> bool:
    return any(
        column in LABEL_NAMESPACE or column.startswith(_LABEL_PREFIXES)
        for column in columns
    )


def assess_backtest_input_readiness_v1(
    spec: BacktestInputSpecV1 | None,
    frozen_read: FrozenReadEvidenceV1 | None,
    retained_calendar: Sequence[str] | pd.Series | pd.Index | None,
    development_feature_rows: pd.DataFrame | None,
    holdout: CoverageHoldout | None,
) -> BacktestInputReadinessReceiptV1:
    """Assess the additive v1 gate without I/O, provider access, or outcomes."""
    if (
        spec is None
        or frozen_read is None
        or retained_calendar is None
        or development_feature_rows is None
        or holdout is None
        or (
            isinstance(frozen_read, FrozenReadEvidenceV1)
            and (
                frozen_read.pre_read_manifest is None
                or frozen_read.post_read_manifest is None
            )
        )
    ):
        return _receipt(
            InputReadinessState.NOT_AVAILABLE,
            InputReadinessReason.INPUT_NOT_AVAILABLE,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )

    if not isinstance(spec, BacktestInputSpecV1):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if not _manifest_has_scalar_shape(spec.manifest):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if spec.manifest.root_manifest_sha256 != _EXPECTED_MANIFEST.root_manifest_sha256:
        return _blocked(
            InputReadinessReason.FROZEN_DIGEST_MISMATCH,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if spec.manifest != _EXPECTED_MANIFEST:
        return _blocked(
            InputReadinessReason.MANIFEST_IDENTITY_MISMATCH,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if type(spec.ticker) is not str or spec.ticker != _TICKER:
        return _blocked(
            InputReadinessReason.SOURCE_IDENTITY_MISMATCH,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if type(spec.date_semantics) is not str or spec.date_semantics != _DATE_SEMANTICS:
        return _blocked(
            InputReadinessReason.FINALITY_NOT_PROVEN,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if (
        type(spec.feature_set_version) is not int
        or spec.feature_set_version != 1
        or type(spec.feature_names) is not tuple
        or spec.feature_names != _EXPECTED_FEATURE_NAMES
    ):
        return _blocked(
            InputReadinessReason.FEATURE_VERSION_MISMATCH,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if type(spec.pit_status) is not str or spec.pit_status != _PIT_STATUS:
        return _blocked(
            InputReadinessReason.PIT_NOT_SAFE,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    split_values = (
        spec.label_horizon_sessions,
        spec.purge_sessions,
        spec.embargo_sessions,
    )
    if any(type(value) is not int for value in split_values):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if spec.purge_sessions < spec.label_horizon_sessions:
        return _blocked(
            InputReadinessReason.PURGE_LT_LABEL_HORIZON,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if (
        type(spec.split_policy) is not str
        or spec.split_policy != _SPLIT_POLICY
        or spec.label_horizon_sessions != _LABEL_HORIZON_SESSIONS
        or spec.purge_sessions != _PURGE_SESSIONS
        or spec.embargo_sessions != _EMBARGO_SESSIONS
    ):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )

    if not isinstance(frozen_read, FrozenReadEvidenceV1):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    pre = frozen_read.pre_read_manifest
    post = frozen_read.post_read_manifest
    if not _manifest_has_scalar_shape(pre) or not _manifest_has_scalar_shape(post):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if pre != post:
        return _blocked(
            InputReadinessReason.FROZEN_READ_CHANGED,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if pre.root_manifest_sha256 != _EXPECTED_MANIFEST.root_manifest_sha256:
        return _blocked(
            InputReadinessReason.FROZEN_DIGEST_MISMATCH,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    if pre != spec.manifest:
        return _blocked(
            InputReadinessReason.MANIFEST_IDENTITY_MISMATCH,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )

    calendar = _canonical_dates(retained_calendar)
    if calendar is None:
        return _blocked(
            InputReadinessReason.ORDERED_DATE_KEY_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
        )
    calendar_values, calendar_dates = calendar
    if (
        not calendar_dates
        or len(set(calendar_dates)) != len(calendar_dates)
        or any(left >= right for left, right in zip(calendar_dates, calendar_dates[1:]))
    ):
        return _blocked(
            InputReadinessReason.ORDERED_DATE_KEY_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
        )
    if (
        len(calendar_dates) != spec.manifest.rows
        or calendar_values[0] != spec.manifest.coverage_start
        or calendar_values[-1] != spec.manifest.coverage_end
    ):
        return _blocked(
            InputReadinessReason.MANIFEST_IDENTITY_MISMATCH,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
        )

    if not isinstance(holdout, CoverageHoldout):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
        )
    if not _holdout_has_scalar_shape(holdout) or holdout != _EXPECTED_HOLDOUT:
        return _blocked(
            InputReadinessReason.HOLDOUT_NOT_SEALED,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
        )
    development_dates = tuple(
        value for value in calendar_values if value < holdout.holdout_start
    )
    holdout_dates = tuple(
        value for value in calendar_values if value >= holdout.holdout_start
    )
    if (
        len(development_dates) != holdout.development_observations
        or len(holdout_dates) != holdout.holdout_observations
        or not holdout_dates
        or holdout_dates[0] != holdout.holdout_start
    ):
        return _blocked(
            InputReadinessReason.HOLDOUT_NOT_SEALED,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
        )

    if not isinstance(development_feature_rows, pd.DataFrame):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
        )
    columns = tuple(development_feature_rows.columns)
    if not columns or any(type(column) is not str for column in columns):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )
    if _has_label_namespace(columns):
        return _blocked(
            InputReadinessReason.LABEL_NAMESPACE_PRESENT,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )
    if (
        development_feature_rows.empty
        or len(columns) != len(set(columns))
        or frozenset(columns) != _EXPECTED_COLUMNS
    ):
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )

    feature_date_result = _canonical_dates(
        development_feature_rows["observation_date"]
    )
    if feature_date_result is None:
        return _blocked(
            InputReadinessReason.ORDERED_DATE_KEY_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )
    feature_dates, parsed_feature_dates = feature_date_result
    if (
        len(set(parsed_feature_dates)) != len(parsed_feature_dates)
        or any(
            left >= right
            for left, right in zip(parsed_feature_dates, parsed_feature_dates[1:])
        )
    ):
        return _blocked(
            InputReadinessReason.ORDERED_DATE_KEY_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )
    calendar_index = {value: index for index, value in enumerate(calendar_values)}
    if any(value not in calendar_index for value in feature_dates):
        return _blocked(
            InputReadinessReason.ORDERED_DATE_KEY_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )
    if any(
        value >= holdout.holdout_start
        or calendar_index[value] + 1 >= len(calendar_values)
        or calendar_values[calendar_index[value] + 1] >= holdout.holdout_start
        for value in feature_dates
    ):
        return _blocked(
            InputReadinessReason.HOLDOUT_CROSSED,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )

    if not _all_exact(development_feature_rows["ticker"], _TICKER, str) or not _all_exact(
        development_feature_rows["source_dataset"], _EXPECTED_MANIFEST.dataset, str
    ) or not _all_exact(
        development_feature_rows["source_contract_version"], 1, int
    ):
        return _blocked(
            InputReadinessReason.SOURCE_IDENTITY_MISMATCH,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )
    if not _all_exact(
        development_feature_rows["date_semantics"], _DATE_SEMANTICS, str
    ):
        return _blocked(
            InputReadinessReason.FINALITY_NOT_PROVEN,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )
    if not _all_exact(
        development_feature_rows["feature_set_version"], 1, int
    ):
        return _blocked(
            InputReadinessReason.FEATURE_VERSION_MISMATCH,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )
    if not _all_exact(development_feature_rows["pit_status"], _PIT_STATUS, str):
        return _blocked(
            InputReadinessReason.PIT_NOT_SAFE,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )

    metadata: list[tuple[object, ...]] = []
    for row_number, observation_date in enumerate(feature_dates):
        next_date = calendar_values[calendar_index[observation_date] + 1]
        expected_observation = observation_date + "T15:30:00+09:00"
        expected_usable = next_date + "T09:00:00+09:00"
        observation_time = development_feature_rows["observation_time"].iloc[row_number]
        available_at = development_feature_rows["available_at"].iloc[row_number]
        usable_from = development_feature_rows["usable_from"].iloc[row_number]
        if (
            type(observation_time) is not str
            or observation_time != expected_observation
            or type(available_at) is not str
            or available_at != expected_observation
            or type(usable_from) is not str
            or usable_from != expected_usable
        ):
            return _blocked(
                InputReadinessReason.CLOCK_MISMATCH,
                spec=spec,
                frozen_read=frozen_read,
                holdout=holdout,
                calendar_values=calendar_values,
                feature_rows=len(development_feature_rows),
            )
        metadata.append((
            observation_date,
            _TICKER,
            _DATE_SEMANTICS,
            observation_time,
            available_at,
            usable_from,
            _EXPECTED_MANIFEST.dataset,
            1,
            1,
            _PIT_STATUS,
        ))

    try:
        for name in _EXPECTED_FEATURE_NAMES:
            numeric = pd.to_numeric(
                development_feature_rows[name], errors="raise"
            ).astype("float64")
            if not all(math.isfinite(value) for value in numeric.tolist()):
                raise ValueError
    except Exception:
        return _blocked(
            InputReadinessReason.SCHEMA_INVALID,
            spec=spec,
            frozen_read=frozen_read,
            holdout=holdout,
            calendar_values=calendar_values,
            feature_rows=len(development_feature_rows),
        )

    return _receipt(
        InputReadinessState.READY,
        InputReadinessReason.READY,
        spec=spec,
        frozen_read=frozen_read,
        holdout=holdout,
        calendar_values=calendar_values,
        feature_rows=len(development_feature_rows),
        feature_metadata=tuple(metadata),
    )


__all__ = [
    "INPUT_READINESS_SCHEMA",
    "KOSPI200_INPUT_SPEC_V1",
    "BacktestInputReadinessReceiptV1",
    "BacktestInputSpecV1",
    "FrozenReadEvidenceV1",
    "InputReadinessReason",
    "InputReadinessState",
    "assess_backtest_input_readiness_v1",
]
