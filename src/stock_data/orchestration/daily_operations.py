from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum, StrEnum
import json
import os
from pathlib import Path
import re
import socket
from types import MappingProxyType
from typing import Any

from stock_data.orchestration.dataset_universe import (
    ConsumerEligibility,
    ConsumerReasonCode,
    validate_consumer_decision,
)


class Cadence(StrEnum):
    KR_DAILY = "KR_DAILY"
    GLOBAL_DAILY = "GLOBAL_DAILY"
    GLOBAL_30M = "GLOBAL_30M"
    SNAPSHOT = "SNAPSHOT"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    MANUAL = "MANUAL"
    RESEARCH_ONLY = "RESEARCH_ONLY"


class DatasetTier(StrEnum):
    TIER_1_CRITICAL = "TIER_1_CRITICAL"
    TIER_2_IMPORTANT = "TIER_2_IMPORTANT"
    TIER_3_DELAYED = "TIER_3_DELAYED"
    TIER_4_RESEARCH = "TIER_4_RESEARCH"


class OperationalStatus(StrEnum):
    AUTO_READY = "AUTO_READY"
    MANUAL_READY = "MANUAL_READY"
    INCREMENTAL_READY = "INCREMENTAL_READY"
    EXPECTED_LAG = "EXPECTED_LAG"
    SNAPSHOT_ONLY = "SNAPSHOT_ONLY"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    BLOCKED = "BLOCKED"
    NO_UPDATE_REQUIRED = "NO_UPDATE_REQUIRED"
    UNKNOWN = "UNKNOWN"


class IdempotencyStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    UNSAFE = "UNSAFE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PitStatus(StrEnum):
    PIT_SAFE = "PIT_SAFE"
    PIT_LIMITED = "PIT_LIMITED"
    PIT_BLOCKED = "PIT_BLOCKED"
    NON_PREDICTIVE = "NON_PREDICTIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class OperationalEligibility(StrEnum):
    """Whether the retained lane may be operated for descriptive use.

    This deliberately does not derive from :class:`PitStatus`: a source may be
    safe to collect and serve as current/descriptive data while remaining
    unavailable to a predictive consumer.
    """

    ELIGIBLE = "ELIGIBLE"
    BLOCKED = "BLOCKED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    UNKNOWN = "UNKNOWN"


class PredictiveEligibility(StrEnum):
    """Whether a dataset may be consumed as a point-in-time model input."""

    ELIGIBLE = "ELIGIBLE"
    BLOCKED = "BLOCKED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    UNKNOWN = "UNKNOWN"


class FreshnessClassification(StrEnum):
    """Date coverage only; deliberately independent of finality/authorization."""

    CURRENT = "CURRENT"
    EXPECTED_LAG = "EXPECTED_LAG"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class FinalityClassification(StrEnum):
    CONFIRMED = "CONFIRMED"
    MANUAL_CONFIRMED = "MANUAL_CONFIRMED"
    AS_RETRIEVED = "AS_RETRIEVED"
    UNKNOWN = "UNKNOWN"


class OperationalClassification(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    MANUAL_ONLY = "MANUAL_ONLY"
    BLOCKED = "BLOCKED"


class PredictiveClassification(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    BLOCKED = "BLOCKED"
    RESEARCH_ONLY = "RESEARCH_ONLY"


class FinalityEvidence(StrEnum):
    CONFIRMED_RULE = "CONFIRMED_RULE"
    MANUAL_CONFIRMATION = "MANUAL_CONFIRMATION"
    AS_RETRIEVED = "AS_RETRIEVED"
    UNKNOWN = "UNKNOWN"


class FinalityStatus(StrEnum):
    FINAL = "FINAL"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class FreshnessStatus(StrEnum):
    CURRENT = "CURRENT"
    EXPECTED_LAG = "EXPECTED_LAG"
    PROVIDER_DELAY = "PROVIDER_DELAY"
    STALE = "STALE"
    MISSING = "MISSING"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class FreshnessReason(StrEnum):
    AT_EXPECTED_LATEST = "AT_EXPECTED_LATEST"
    NORMAL_SOURCE_LAG = "NORMAL_SOURCE_LAG"
    PROVIDER_NOT_FINAL = "PROVIDER_NOT_FINAL"
    BEHIND_EXPECTED_LATEST = "BEHIND_EXPECTED_LATEST"
    NO_RETAINED_DATA = "NO_RETAINED_DATA"
    PARTIAL_DATA = "PARTIAL_DATA"
    DATASET_BLOCKED = "DATASET_BLOCKED"
    FINALITY_UNKNOWN = "FINALITY_UNKNOWN"
    EXPECTED_LATEST_UNKNOWN = "EXPECTED_LATEST_UNKNOWN"
    ACTUAL_AFTER_EXPECTED = "ACTUAL_AFTER_EXPECTED"


class FailureCode(StrEnum):
    AUTH_FAILURE = "AUTH_FAILURE"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    PROVIDER_DELAY = "PROVIDER_DELAY"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    PARTIAL_RESPONSE = "PARTIAL_RESPONSE"
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    KEY_DUPLICATION = "KEY_DUPLICATION"
    STALE_DATA = "STALE_DATA"
    UNIT_CHANGE = "UNIT_CHANGE"
    SEMANTIC_CHANGE = "SEMANTIC_CHANGE"
    ENDPOINT_DEPRECATED = "ENDPOINT_DEPRECATED"
    SERVICE_TERMINATED = "SERVICE_TERMINATED"
    CHECKPOINT_CONFLICT = "CHECKPOINT_CONFLICT"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


class AuthType(StrEnum):
    API_KEY = "API_KEY"
    OAUTH2 = "OAUTH2"
    SESSION_LOGIN = "SESSION_LOGIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AuthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    EXPIRING_SOON = "EXPIRING_SOON"
    EXPIRED = "EXPIRED"
    REFRESH_FAILED = "REFRESH_FAILED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class LaneReadinessStatus(StrEnum):
    READY = "READY"
    READY_WITH_FINALITY_GATE = "READY_WITH_FINALITY_GATE"
    READY_WITH_LIMITS = "READY_WITH_LIMITS"
    MANUAL_ONLY = "MANUAL_ONLY"
    BLOCKED = "BLOCKED"
    RESEARCH_ONLY = "RESEARCH_ONLY"


@dataclass(frozen=True)
class LaneReadiness:
    lane: str
    status: LaneReadinessStatus
    source: str
    expected_cadence: str
    finality_handling: str
    api_operation: str
    checkpoint: str
    idempotency: str
    health_integration: str
    scheduler_eligible: bool
    blocker: str | None
    next_action: str

    def __post_init__(self) -> None:
        _require_enum(self.status, LaneReadinessStatus, "status")
        for field in (
            "lane", "source", "expected_cadence", "finality_handling",
            "api_operation", "checkpoint", "idempotency",
            "health_integration", "next_action",
        ):
            _require_text(getattr(self, field), field)
        if self.scheduler_eligible and self.status is not LaneReadinessStatus.READY:
            raise ValueError("only an unqualified READY lane may be scheduler eligible")
        if self.status is not LaneReadinessStatus.READY and not self.blocker:
            raise ValueError("a constrained lane must state its blocker")


class DailyRunStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class StageStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    UNKNOWN = "UNKNOWN"


_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _require_enum(value: object, kind: type[Enum], field: str) -> None:
    if not isinstance(value, kind):
        raise TypeError(f"{field} must be {kind.__name__}")


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _canonical_tuple(values: Iterable[str], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field} must be an iterable of strings, not one string")
    result = tuple(sorted(values))
    if any(not isinstance(value, str) or not value for value in result):
        raise ValueError(f"{field} contains an invalid value")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} contains duplicates")
    return result


@dataclass(frozen=True)
class ProviderAuthMetadata:
    provider_id: str
    auth_type: AuthType
    credential_env_keys: tuple[str, ...]
    expires_at_env_key: str | None = None
    refresh_supported: bool = False
    auth_health_supported: bool = False

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.provider_id):
            raise ValueError("provider_id is invalid")
        _require_enum(self.auth_type, AuthType, "auth_type")
        keys = _canonical_tuple(self.credential_env_keys, "credential_env_keys")
        if any(not _ENV_NAME.fullmatch(key) for key in keys):
            raise ValueError("credential_env_keys must contain environment key names")
        object.__setattr__(self, "credential_env_keys", keys)
        if self.expires_at_env_key is not None and not _ENV_NAME.fullmatch(self.expires_at_env_key):
            raise ValueError("expires_at_env_key is invalid")
        if self.auth_type is AuthType.NOT_APPLICABLE and (keys or self.expires_at_env_key):
            raise ValueError("auth metadata marked not applicable cannot declare credentials")
        if self.auth_type is not AuthType.NOT_APPLICABLE and not keys:
            raise ValueError("authenticated providers require credential key names")


def evaluate_auth_status(
    metadata: ProviderAuthMetadata,
    environment: Mapping[str, str],
    *,
    as_of: datetime,
    expiring_within: timedelta = timedelta(days=30),
) -> AuthStatus:
    """Evaluate only configuration metadata; never return or persist a credential value."""
    _require_aware(as_of, "as_of")
    if expiring_within < timedelta(0):
        raise ValueError("expiring_within cannot be negative")
    if metadata.auth_type is AuthType.NOT_APPLICABLE:
        return AuthStatus.NOT_APPLICABLE
    if any(not environment.get(key, "").strip() for key in metadata.credential_env_keys):
        return AuthStatus.REAUTH_REQUIRED
    if not metadata.auth_health_supported or metadata.expires_at_env_key is None:
        return AuthStatus.UNKNOWN
    raw_expiry = environment.get(metadata.expires_at_env_key, "").strip()
    if not raw_expiry:
        return AuthStatus.UNKNOWN
    try:
        expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        _require_aware(expires_at, "credential expiry")
    except ValueError:
        return AuthStatus.UNKNOWN
    if expires_at <= as_of:
        return AuthStatus.EXPIRED
    if expires_at <= as_of + expiring_within:
        return AuthStatus.EXPIRING_SOON
    return AuthStatus.HEALTHY


@dataclass(frozen=True)
class FinalityPolicy:
    evidence: FinalityEvidence
    timezone: str
    provider_available_rule: str | None = None
    provider_final_rule: str | None = None
    collection_window: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.evidence, FinalityEvidence, "evidence")
        _require_text(self.timezone, "timezone")


@dataclass(frozen=True)
class FreshnessPolicy:
    policy_id: str
    timezone: str
    expected_latest_rule: str
    finality: FinalityPolicy

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.policy_id):
            raise ValueError("policy_id is invalid")
        _require_text(self.timezone, "timezone")
        _require_text(self.expected_latest_rule, "expected_latest_rule")
        if not isinstance(self.finality, FinalityPolicy):
            raise TypeError("finality must be FinalityPolicy")
        if self.finality.timezone != self.timezone:
            raise ValueError("freshness and finality timezone differ")


@dataclass(frozen=True)
class DatasetOperationSpec:
    dataset_id: str
    economic_variable: str
    cadence: Cadence
    tier: DatasetTier
    primary_source: str
    contract_id: str | None
    contract_version: int | None
    operational_status: OperationalStatus
    freshness_policy: FreshnessPolicy
    pipeline_dependencies: tuple[str, ...]
    idempotency_status: IdempotencyStatus
    pit_status: PitStatus
    automation_enabled: bool
    provider_auth_id: str
    validation_policy: str
    dashboard_required: bool = False
    model_input_required: bool = False
    candidate: bool = False

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.dataset_id):
            raise ValueError("dataset_id is invalid")
        _require_text(self.economic_variable, "economic_variable")
        _require_enum(self.cadence, Cadence, "cadence")
        _require_enum(self.tier, DatasetTier, "tier")
        _require_text(self.primary_source, "primary_source")
        _require_enum(self.operational_status, OperationalStatus, "operational_status")
        _require_enum(self.idempotency_status, IdempotencyStatus, "idempotency_status")
        _require_enum(self.pit_status, PitStatus, "pit_status")
        if not isinstance(self.freshness_policy, FreshnessPolicy):
            raise TypeError("freshness_policy must be FreshnessPolicy")
        dependencies = _canonical_tuple(self.pipeline_dependencies, "pipeline_dependencies")
        if self.dataset_id in dependencies:
            raise ValueError("dataset cannot depend on itself")
        object.__setattr__(self, "pipeline_dependencies", dependencies)
        if (self.contract_id is None) != (self.contract_version is None):
            raise ValueError("contract_id and contract_version must be declared together")
        if self.contract_id is not None:
            if not _IDENTIFIER.fullmatch(self.contract_id) or self.contract_version < 1:
                raise ValueError("contract identity is invalid")
        if not _IDENTIFIER.fullmatch(self.provider_auth_id):
            raise ValueError("provider_auth_id is invalid")
        _require_text(self.validation_policy, "validation_policy")
        if self.automation_enabled:
            if self.operational_status is not OperationalStatus.AUTO_READY:
                raise ValueError("automation requires AUTO_READY status")
            if self.cadence in {Cadence.MANUAL, Cadence.RESEARCH_ONLY}:
                raise ValueError("manual/research cadence cannot be automated")
            if self.tier is DatasetTier.TIER_4_RESEARCH or self.candidate:
                raise ValueError("research/candidate dataset cannot be automated")

    @property
    def operational_eligibility(self) -> OperationalEligibility:
        """Return collection/serving eligibility independently of PIT status."""
        if self.candidate or self.operational_status is OperationalStatus.BLOCKED:
            return OperationalEligibility.BLOCKED
        if (
            self.operational_status is OperationalStatus.RESEARCH_ONLY
            or self.cadence is Cadence.RESEARCH_ONLY
        ):
            return OperationalEligibility.RESEARCH_ONLY
        if self.operational_status is OperationalStatus.UNKNOWN:
            return OperationalEligibility.UNKNOWN
        return OperationalEligibility.ELIGIBLE

    @property
    def predictive_eligibility(self) -> PredictiveEligibility:
        """Return predictive/PIT eligibility without changing collection policy."""
        if (
            self.candidate
            or self.tier is DatasetTier.TIER_4_RESEARCH
            or self.cadence is Cadence.RESEARCH_ONLY
            or self.pit_status is PitStatus.NON_PREDICTIVE
        ):
            return PredictiveEligibility.RESEARCH_ONLY
        if self.pit_status is PitStatus.PIT_SAFE:
            return PredictiveEligibility.ELIGIBLE
        if self.pit_status in {
            PitStatus.PIT_LIMITED,
            PitStatus.PIT_BLOCKED,
            PitStatus.UNKNOWN,
        }:
            return PredictiveEligibility.BLOCKED
        return PredictiveEligibility.UNKNOWN

    @property
    def operational_classification(self) -> OperationalClassification:
        """Exact operational dimension used by health/report consumers."""
        if self.candidate or self.operational_status in {
            OperationalStatus.BLOCKED, OperationalStatus.RESEARCH_ONLY,
            OperationalStatus.UNKNOWN,
        } or self.cadence is Cadence.RESEARCH_ONLY:
            return OperationalClassification.BLOCKED
        if self.operational_status in {
            OperationalStatus.AUTO_READY, OperationalStatus.INCREMENTAL_READY,
        }:
            return OperationalClassification.ELIGIBLE
        return OperationalClassification.MANUAL_ONLY

    @property
    def predictive_classification(self) -> PredictiveClassification:
        mapping = {
            PredictiveEligibility.ELIGIBLE: PredictiveClassification.ELIGIBLE,
            PredictiveEligibility.RESEARCH_ONLY: PredictiveClassification.RESEARCH_ONLY,
            PredictiveEligibility.BLOCKED: PredictiveClassification.BLOCKED,
            PredictiveEligibility.UNKNOWN: PredictiveClassification.BLOCKED,
        }
        return mapping[self.predictive_eligibility]


class DatasetOperationsRegistry(Mapping[str, DatasetOperationSpec]):
    """Immutable operations registry; orchestration selection is metadata-driven."""

    def __init__(
        self,
        specs: Iterable[DatasetOperationSpec],
        provider_auth: Mapping[str, ProviderAuthMetadata],
    ) -> None:
        items = tuple(specs)
        if any(not isinstance(spec, DatasetOperationSpec) for spec in items):
            raise TypeError("registry accepts DatasetOperationSpec values only")
        ids = tuple(spec.dataset_id for spec in items)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate dataset_id")
        providers = dict(provider_auth)
        if any(key != value.provider_id for key, value in providers.items()):
            raise ValueError("provider auth registry key differs")
        missing = sorted({spec.provider_auth_id for spec in items} - providers.keys())
        if missing:
            raise ValueError(f"provider auth metadata missing: {missing}")
        self._specs = MappingProxyType({spec.dataset_id: spec for spec in sorted(items, key=lambda item: item.dataset_id)})
        self._provider_auth = MappingProxyType(dict(sorted(providers.items())))

    def __getitem__(self, dataset_id: str) -> DatasetOperationSpec:
        return self._specs[dataset_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._specs)

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def provider_auth(self) -> Mapping[str, ProviderAuthMetadata]:
        return self._provider_auth

    def select(
        self,
        *,
        cadence: Cadence | None = None,
        executable_only: bool = False,
    ) -> tuple[DatasetOperationSpec, ...]:
        if cadence is not None:
            _require_enum(cadence, Cadence, "cadence")
        selected = self._specs.values()
        if cadence is not None:
            selected = (spec for spec in selected if spec.cadence is cadence)
        if executable_only:
            selected = (spec for spec in selected if spec.automation_enabled)
        return tuple(selected)

    def with_spec(self, spec: DatasetOperationSpec) -> DatasetOperationsRegistry:
        if spec.dataset_id in self:
            raise ValueError(f"duplicate dataset_id: {spec.dataset_id}")
        return DatasetOperationsRegistry((*self._specs.values(), spec), self._provider_auth)


@dataclass(frozen=True)
class FreshnessContext:
    market_date: date | None
    expected_latest: date | None
    actual_latest: date | None
    provider_available_at: datetime | None = None
    provider_final_at: datetime | None = None
    partial: bool = False
    blocked: bool = False

    def __post_init__(self) -> None:
        for field in ("provider_available_at", "provider_final_at"):
            value = getattr(self, field)
            if value is not None:
                _require_aware(value, field)
        if self.provider_available_at and self.provider_final_at:
            if self.provider_final_at < self.provider_available_at:
                raise ValueError("provider finality precedes availability")


@dataclass(frozen=True)
class FreshnessResult:
    dataset_id: str
    as_of: datetime
    market_date: date | None
    expected_latest: date | None
    actual_latest: date | None
    freshness_status: FreshnessStatus
    finality_status: FinalityStatus
    reason_code: FreshnessReason
    review_required: bool
    freshness_classification: FreshnessClassification
    finality_classification: FinalityClassification

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.dataset_id):
            raise ValueError("freshness dataset_id is invalid")
        _require_aware(self.as_of, "as_of")
        _require_enum(self.freshness_status, FreshnessStatus, "freshness_status")
        _require_enum(self.finality_status, FinalityStatus, "finality_status")
        _require_enum(self.reason_code, FreshnessReason, "reason_code")
        _require_enum(
            self.freshness_classification, FreshnessClassification,
            "freshness_classification",
        )
        _require_enum(
            self.finality_classification, FinalityClassification,
            "finality_classification",
        )


def _freshness_classification(context: FreshnessContext) -> FreshnessClassification:
    """Classify retained date coverage without consulting source finality."""
    if context.partial or context.expected_latest is None:
        return FreshnessClassification.UNKNOWN
    if context.actual_latest is not None and context.actual_latest > context.expected_latest:
        return FreshnessClassification.UNKNOWN
    if context.actual_latest is None or context.actual_latest < context.expected_latest:
        return FreshnessClassification.STALE
    if context.market_date is not None and context.expected_latest < context.market_date:
        return FreshnessClassification.EXPECTED_LAG
    return FreshnessClassification.CURRENT


def _finality_classification(
    evidence: FinalityEvidence, status: FinalityStatus,
) -> FinalityClassification:
    if evidence is FinalityEvidence.AS_RETRIEVED:
        return FinalityClassification.AS_RETRIEVED
    if status is not FinalityStatus.FINAL:
        return FinalityClassification.UNKNOWN
    if evidence is FinalityEvidence.CONFIRMED_RULE:
        return FinalityClassification.CONFIRMED
    if evidence is FinalityEvidence.MANUAL_CONFIRMATION:
        return FinalityClassification.MANUAL_CONFIRMED
    return FinalityClassification.UNKNOWN


def evaluate_freshness(
    spec: DatasetOperationSpec,
    *,
    as_of: datetime,
    context: FreshnessContext,
) -> FreshnessResult:
    """Pure evaluation over explicit calendar/finality inputs; no weekday inference."""
    _require_aware(as_of, "as_of")
    policy = spec.freshness_policy.finality
    if policy.evidence is FinalityEvidence.UNKNOWN:
        finality = FinalityStatus.UNKNOWN
    elif context.provider_final_at is None:
        finality = FinalityStatus.UNKNOWN
    elif as_of < context.provider_final_at:
        finality = FinalityStatus.PENDING
    else:
        finality = FinalityStatus.FINAL

    def result(status: FreshnessStatus, reason: FreshnessReason, review: bool) -> FreshnessResult:
        return FreshnessResult(
            dataset_id=spec.dataset_id,
            as_of=as_of,
            market_date=context.market_date,
            expected_latest=context.expected_latest,
            actual_latest=context.actual_latest,
            freshness_status=status,
            finality_status=finality,
            reason_code=reason,
            review_required=review,
            freshness_classification=_freshness_classification(context),
            finality_classification=_finality_classification(policy.evidence, finality),
        )

    if context.blocked or spec.operational_status is OperationalStatus.BLOCKED:
        return result(FreshnessStatus.BLOCKED, FreshnessReason.DATASET_BLOCKED, True)
    if context.partial:
        return result(FreshnessStatus.PARTIAL, FreshnessReason.PARTIAL_DATA, True)
    if context.expected_latest is None:
        return result(FreshnessStatus.UNKNOWN, FreshnessReason.EXPECTED_LATEST_UNKNOWN, True)
    if context.actual_latest is not None and context.actual_latest > context.expected_latest:
        return result(FreshnessStatus.UNKNOWN, FreshnessReason.ACTUAL_AFTER_EXPECTED, True)
    if finality is FinalityStatus.UNKNOWN:
        return result(FreshnessStatus.UNKNOWN, FreshnessReason.FINALITY_UNKNOWN, True)
    if finality is FinalityStatus.PENDING:
        return result(FreshnessStatus.PROVIDER_DELAY, FreshnessReason.PROVIDER_NOT_FINAL, False)
    if context.actual_latest is None:
        return result(FreshnessStatus.MISSING, FreshnessReason.NO_RETAINED_DATA, True)
    if context.actual_latest < context.expected_latest:
        return result(FreshnessStatus.STALE, FreshnessReason.BEHIND_EXPECTED_LATEST, True)
    if context.market_date is not None and context.expected_latest < context.market_date:
        return result(FreshnessStatus.EXPECTED_LAG, FreshnessReason.NORMAL_SOURCE_LAG, False)
    return result(FreshnessStatus.CURRENT, FreshnessReason.AT_EXPECTED_LATEST, False)


@dataclass(frozen=True)
class DailyDryRunEntry:
    """One registry-driven, offline-only daily operation planning row."""

    dataset_id: str
    cadence: Cadence
    tier: DatasetTier
    operational_eligibility: OperationalEligibility
    predictive_eligibility: PredictiveEligibility
    actual_latest: date | None
    expected_latest: date | None
    freshness_status: FreshnessStatus
    planned_action: str
    blocked_reason: str | None
    dependencies: tuple[str, ...]
    estimated_api_calls: int = 0

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.dataset_id):
            raise ValueError("dry-run dataset_id is invalid")
        _require_enum(self.cadence, Cadence, "cadence")
        _require_enum(self.tier, DatasetTier, "tier")
        _require_enum(self.operational_eligibility, OperationalEligibility, "operational_eligibility")
        _require_enum(self.predictive_eligibility, PredictiveEligibility, "predictive_eligibility")
        _require_enum(self.freshness_status, FreshnessStatus, "freshness_status")
        _require_text(self.planned_action, "planned_action")
        if self.blocked_reason is not None:
            _require_text(self.blocked_reason, "blocked_reason")
        object.__setattr__(self, "dependencies", _canonical_tuple(self.dependencies, "dependencies"))
        if self.estimated_api_calls < 0:
            raise ValueError("estimated_api_calls cannot be negative")

    @property
    def dataset(self) -> str:
        """Human-facing alias used by dry-run table consumers."""
        return self.dataset_id


def build_daily_operations_dry_run(
    *,
    as_of: datetime,
    registry: DatasetOperationsRegistry | None = None,
    contexts: Mapping[str, FreshnessContext] | None = None,
    retained_latest: Mapping[str, date | None] | None = None,
    expected_latest: Mapping[str, date | None] | None = None,
    market_dates: Mapping[str, date | None] | None = None,
    cadence: Cadence | None = None,
) -> tuple[DailyDryRunEntry, ...]:
    """Plan daily work from registry metadata without invoking any collector.

    ``contexts`` is the preferred input because it carries explicit calendar
    and finality evidence.  The separate mappings are convenience inputs for a
    read-only reconciliation caller; missing values remain unknown rather than
    being inferred from weekdays or the wall clock.
    """
    _require_aware(as_of, "as_of")
    selected_registry = registry if registry is not None else DATASET_OPERATIONS
    supplied_contexts = contexts or {}
    retained = retained_latest or {}
    expected = expected_latest or {}
    markets = market_dates or {}
    rows: list[DailyDryRunEntry] = []
    for spec in selected_registry.select(cadence=cadence):
        supplied = supplied_contexts.get(spec.dataset_id)
        if supplied is None:
            supplied = FreshnessContext(
                market_date=markets.get(spec.dataset_id),
                expected_latest=expected.get(spec.dataset_id),
                actual_latest=retained.get(spec.dataset_id),
            )
        else:
            # Explicit context wins; convenience retained values never mutate
            # or override evidence supplied by the caller.
            if spec.dataset_id in retained:
                supplied = replace(supplied, actual_latest=retained[spec.dataset_id])
        freshness = evaluate_freshness(spec, as_of=as_of, context=supplied)
        op = spec.operational_eligibility
        predictive = spec.predictive_eligibility
        reason: str | None = None
        if op is OperationalEligibility.BLOCKED:
            action = "BLOCKED"
            reason = "operational eligibility is blocked"
        elif op is OperationalEligibility.RESEARCH_ONLY:
            action = "RESEARCH_ONLY"
            reason = "research-only operation"
        elif freshness.freshness_status is FreshnessStatus.BLOCKED:
            action = "BLOCKED"
            reason = "freshness context is blocked"
        elif freshness.freshness_status in {
            FreshnessStatus.CURRENT,
            FreshnessStatus.EXPECTED_LAG,
        }:
            action = "NO_UPDATE_REQUIRED"
        elif freshness.freshness_status in {
            FreshnessStatus.STALE,
            FreshnessStatus.MISSING,
            FreshnessStatus.PARTIAL,
        }:
            if spec.operational_status in {
                OperationalStatus.AUTO_READY,
                OperationalStatus.INCREMENTAL_READY,
            }:
                action = "BOUNDED_CATCH_UP_ELIGIBLE"
            elif spec.operational_status is OperationalStatus.MANUAL_READY:
                action = "MANUAL_OPERATION_REVIEW"
                reason = "manual operation requires explicit runbook execution"
            else:
                action = "BLOCKED_NO_APPROVED_OPERATION"
                reason = "no approved incremental/daily operation"
        else:
            action = "REVIEW_REQUIRED"
            reason = "freshness/finality evidence is incomplete"
        if reason is None:
            if predictive is PredictiveEligibility.BLOCKED:
                reason = "predictive/PIT eligibility is blocked"
            elif predictive is PredictiveEligibility.RESEARCH_ONLY:
                reason = "research-only/predictive use is not permitted"
        rows.append(DailyDryRunEntry(
            dataset_id=spec.dataset_id,
            cadence=spec.cadence,
            tier=spec.tier,
            operational_eligibility=op,
            predictive_eligibility=predictive,
            actual_latest=freshness.actual_latest,
            expected_latest=freshness.expected_latest,
            freshness_status=freshness.freshness_status,
            planned_action=action,
            blocked_reason=reason,
            dependencies=spec.pipeline_dependencies,
        ))
    return tuple(rows)


# Concise aliases keep the planner easy to discover without introducing a
# second implementation or a dataset-specific execution branch.
build_daily_dry_run_plan = build_daily_operations_dry_run
plan_daily_operations = build_daily_operations_dry_run


@dataclass(frozen=True)
class FailurePolicy:
    retry_allowed: bool
    fallback_allowed: bool
    stop_lane: bool
    review_required: bool


def _failure_policy(*, retry: bool = False, stop: bool = True, review: bool = True) -> FailurePolicy:
    return FailurePolicy(retry, False, stop, review)


FAILURE_POLICIES: Mapping[FailureCode, FailurePolicy] = MappingProxyType({
    FailureCode.AUTH_FAILURE: _failure_policy(),
    FailureCode.RATE_LIMIT: _failure_policy(),
    FailureCode.NETWORK_FAILURE: _failure_policy(retry=True),
    FailureCode.PROVIDER_DELAY: _failure_policy(stop=False, review=False),
    FailureCode.EMPTY_RESPONSE: _failure_policy(),
    FailureCode.PARTIAL_RESPONSE: _failure_policy(),
    FailureCode.SCHEMA_CHANGE: _failure_policy(),
    FailureCode.KEY_DUPLICATION: _failure_policy(),
    FailureCode.STALE_DATA: _failure_policy(stop=False),
    FailureCode.UNIT_CHANGE: _failure_policy(),
    FailureCode.SEMANTIC_CHANGE: _failure_policy(),
    FailureCode.ENDPOINT_DEPRECATED: _failure_policy(),
    FailureCode.SERVICE_TERMINATED: _failure_policy(),
    FailureCode.CHECKPOINT_CONFLICT: _failure_policy(),
    FailureCode.VALIDATION_FAILURE: _failure_policy(),
    FailureCode.UNKNOWN_FAILURE: _failure_policy(),
})


def policy_for_failure(code: FailureCode | str) -> tuple[FailureCode, FailurePolicy]:
    try:
        resolved = code if isinstance(code, FailureCode) else FailureCode(code)
    except (TypeError, ValueError):
        resolved = FailureCode.UNKNOWN_FAILURE
    return resolved, FAILURE_POLICIES[resolved]


@dataclass(frozen=True)
class DailyRun:
    run_id: str
    run_date: date
    cadence_group: Cadence
    status: DailyRunStatus
    datasets_attempted: tuple[str, ...]
    datasets_succeeded: tuple[str, ...] = ()
    datasets_failed: tuple[str, ...] = ()
    started_at: datetime | None = None
    finished_at: datetime | None = None
    review_required: bool = False

    def __post_init__(self) -> None:
        if not _RUN_ID.fullmatch(self.run_id):
            raise ValueError("run_id is invalid")
        _require_enum(self.cadence_group, Cadence, "cadence_group")
        _require_enum(self.status, DailyRunStatus, "status")
        attempted = _canonical_tuple(self.datasets_attempted, "datasets_attempted")
        succeeded = _canonical_tuple(self.datasets_succeeded, "datasets_succeeded")
        failed = _canonical_tuple(self.datasets_failed, "datasets_failed")
        object.__setattr__(self, "datasets_attempted", attempted)
        object.__setattr__(self, "datasets_succeeded", succeeded)
        object.__setattr__(self, "datasets_failed", failed)
        if not set(succeeded).issubset(attempted) or not set(failed).issubset(attempted):
            raise ValueError("run results must be attempted datasets")
        if set(succeeded) & set(failed):
            raise ValueError("dataset cannot both succeed and fail")
        if self.started_at is not None:
            _require_aware(self.started_at, "started_at")
        if self.finished_at is not None:
            _require_aware(self.finished_at, "finished_at")
        terminal = self.status in {
            DailyRunStatus.SUCCEEDED, DailyRunStatus.DEGRADED,
            DailyRunStatus.FAILED, DailyRunStatus.ABORTED,
        }
        if terminal != (self.finished_at is not None):
            raise ValueError("terminal run and finished_at must agree")
        if self.status is DailyRunStatus.RUNNING and self.started_at is None:
            raise ValueError("running run requires started_at")
        if self.status is DailyRunStatus.PLANNED and (self.started_at or self.finished_at):
            raise ValueError("planned run cannot have timestamps")
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finished_at precedes started_at")
        if self.status is DailyRunStatus.SUCCEEDED and failed:
            raise ValueError("succeeded run cannot contain failed datasets")


_RUN_TRANSITIONS = MappingProxyType({
    DailyRunStatus.PLANNED: frozenset({DailyRunStatus.RUNNING, DailyRunStatus.ABORTED}),
    DailyRunStatus.RUNNING: frozenset({
        DailyRunStatus.SUCCEEDED, DailyRunStatus.DEGRADED,
        DailyRunStatus.FAILED, DailyRunStatus.ABORTED,
    }),
})


def transition_run(
    run: DailyRun,
    status: DailyRunStatus,
    *,
    at: datetime,
    datasets_succeeded: Iterable[str] = (),
    datasets_failed: Iterable[str] = (),
    review_required: bool | None = None,
) -> DailyRun:
    _require_enum(status, DailyRunStatus, "status")
    _require_aware(at, "at")
    if status not in _RUN_TRANSITIONS.get(run.status, frozenset()):
        raise ValueError(f"invalid run transition: {run.status.value}->{status.value}")
    if status is DailyRunStatus.RUNNING:
        return replace(run, status=status, started_at=at)
    return replace(
        run,
        status=status,
        datasets_succeeded=tuple(datasets_succeeded),
        datasets_failed=tuple(datasets_failed),
        finished_at=at,
        review_required=run.review_required if review_required is None else review_required,
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        _require_aware(value, "serialized datetime")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def serialize_json(value: object) -> bytes:
    payload = asdict(value) if hasattr(value, "__dataclass_fields__") else value
    return (json.dumps(
        _json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise RuntimeError(f"temporary path already exists: {temporary.name}")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_run_checkpoint(
    path: Path,
    run: DailyRun,
    *,
    expected_previous: DailyRun | None = None,
) -> None:
    if path.exists():
        current = read_run_checkpoint(path)
        if current == run:
            return
        if current.run_id != run.run_id:
            raise DailyRunLockError("checkpoint run identity differs")
        if expected_previous is None or current != expected_previous:
            raise DailyRunLockError("checkpoint compare-and-swap conflict")
    elif expected_previous is not None:
        raise DailyRunLockError("expected previous checkpoint is missing")
    _atomic_write(path, serialize_json(run))


def read_run_checkpoint(path: Path) -> DailyRun:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return DailyRun(
        run_id=payload["run_id"],
        run_date=date.fromisoformat(payload["run_date"]),
        cadence_group=Cadence(payload["cadence_group"]),
        status=DailyRunStatus(payload["status"]),
        datasets_attempted=tuple(payload["datasets_attempted"]),
        datasets_succeeded=tuple(payload["datasets_succeeded"]),
        datasets_failed=tuple(payload["datasets_failed"]),
        started_at=datetime.fromisoformat(payload["started_at"]) if payload["started_at"] else None,
        finished_at=datetime.fromisoformat(payload["finished_at"]) if payload["finished_at"] else None,
        review_required=payload["review_required"],
    )


class DailyRunLockError(RuntimeError):
    pass


@dataclass(frozen=True)
class DailyRunLockMetadata:
    version: int
    run_id: str
    pid: int
    owner: str
    acquired_at: datetime

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ValueError("unsupported lock metadata version")
        if not _RUN_ID.fullmatch(self.run_id):
            raise ValueError("lock run_id is invalid")
        if self.pid < 1:
            raise ValueError("lock pid is invalid")
        _require_text(self.owner, "owner")
        _require_aware(self.acquired_at, "acquired_at")


class DailyRunLock:
    """Exclusive advisory lock. Existing or stale-looking locks require manual recovery."""

    def __init__(self, path: Path, *, run_id: str, acquired_at: datetime | None = None) -> None:
        self.path = path
        self.metadata = DailyRunLockMetadata(
            version=1,
            run_id=run_id,
            pid=os.getpid(),
            owner=socket.gethostname() or "unknown-host",
            acquired_at=acquired_at or datetime.now(timezone.utc),
        )
        self._body = serialize_json(self.metadata)
        self._held = False

    def acquire(self) -> DailyRunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise DailyRunLockError(
                f"daily run lock exists; manual recovery required: {self.path.name}"
            ) from error
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self._body)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            self.path.unlink(missing_ok=True)
            raise
        self._held = True
        return self

    def release(self) -> None:
        if not self._held:
            raise DailyRunLockError("daily run lock is not held")
        try:
            current = self.path.read_bytes()
        except FileNotFoundError as error:
            raise DailyRunLockError("owned daily run lock is missing") from error
        if current != self._body:
            raise DailyRunLockError("daily run lock ownership differs; refusing removal")
        self.path.unlink()
        self._held = False

    def __enter__(self) -> DailyRunLock:
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


@dataclass(frozen=True)
class DatasetHealth:
    run_id: str
    dataset_id: str
    cadence: Cadence
    tier: DatasetTier
    primary_source: str
    expected_latest: date | None
    actual_latest: date | None
    freshness_status: FreshnessStatus
    collector_status: StageStatus
    validation_status: StageStatus
    downstream_status: StageStatus
    auth_status: AuthStatus
    error_code: FailureCode | None
    review_required: bool
    warnings: tuple[str, ...] = ()
    dashboard_required: bool = False
    model_input_required: bool = False
    pit_status: PitStatus = PitStatus.UNKNOWN
    operational_eligibility: OperationalEligibility = OperationalEligibility.UNKNOWN
    predictive_eligibility: PredictiveEligibility = PredictiveEligibility.UNKNOWN
    display_consumer_eligibility: ConsumerEligibility = ConsumerEligibility.UNKNOWN
    display_consumer_reason: ConsumerReasonCode = ConsumerReasonCode.NOT_CLASSIFIED
    research_consumer_eligibility: ConsumerEligibility = ConsumerEligibility.UNKNOWN
    research_consumer_reason: ConsumerReasonCode = ConsumerReasonCode.NOT_CLASSIFIED
    predictive_consumer_eligibility: ConsumerEligibility = ConsumerEligibility.UNKNOWN
    predictive_consumer_reason: ConsumerReasonCode = ConsumerReasonCode.NOT_CLASSIFIED
    blocked_reason: str | None = None
    freshness_classification: FreshnessClassification | None = None
    finality_classification: FinalityClassification = FinalityClassification.UNKNOWN
    operational_classification: OperationalClassification | None = None
    predictive_classification: PredictiveClassification | None = None

    def __post_init__(self) -> None:
        if not _RUN_ID.fullmatch(self.run_id) or not _IDENTIFIER.fullmatch(self.dataset_id):
            raise ValueError("health identity is invalid")
        _require_text(self.primary_source, "primary_source")
        for field, kind in (
            ("cadence", Cadence), ("tier", DatasetTier),
            ("freshness_status", FreshnessStatus), ("collector_status", StageStatus),
            ("validation_status", StageStatus), ("downstream_status", StageStatus),
            ("auth_status", AuthStatus), ("pit_status", PitStatus),
            ("operational_eligibility", OperationalEligibility),
            ("predictive_eligibility", PredictiveEligibility),
            ("display_consumer_eligibility", ConsumerEligibility),
            ("display_consumer_reason", ConsumerReasonCode),
            ("research_consumer_eligibility", ConsumerEligibility),
            ("research_consumer_reason", ConsumerReasonCode),
            ("predictive_consumer_eligibility", ConsumerEligibility),
            ("predictive_consumer_reason", ConsumerReasonCode),
            ("finality_classification", FinalityClassification),
        ):
            _require_enum(getattr(self, field), kind, field)
        if self.freshness_classification is None:
            object.__setattr__(self, "freshness_classification", _classify_health_dates(self))
        if self.operational_classification is None:
            inferred = (
                OperationalClassification.BLOCKED
                if self.operational_eligibility in {
                    OperationalEligibility.BLOCKED, OperationalEligibility.RESEARCH_ONLY,
                    OperationalEligibility.UNKNOWN,
                }
                else OperationalClassification.MANUAL_ONLY
            )
            object.__setattr__(self, "operational_classification", inferred)
        if self.predictive_classification is None:
            inferred_predictive = {
                PredictiveEligibility.ELIGIBLE: PredictiveClassification.ELIGIBLE,
                PredictiveEligibility.RESEARCH_ONLY: PredictiveClassification.RESEARCH_ONLY,
                PredictiveEligibility.BLOCKED: PredictiveClassification.BLOCKED,
                PredictiveEligibility.UNKNOWN: PredictiveClassification.BLOCKED,
            }[self.predictive_eligibility]
            object.__setattr__(self, "predictive_classification", inferred_predictive)
        _require_enum(
            self.freshness_classification, FreshnessClassification,
            "freshness_classification",
        )
        _require_enum(
            self.operational_classification, OperationalClassification,
            "operational_classification",
        )
        _require_enum(
            self.predictive_classification, PredictiveClassification,
            "predictive_classification",
        )
        for axis in ("display", "research", "predictive"):
            registered = DATASET_UNIVERSE[self.dataset_id]
            eligibility_field = f"{axis}_consumer_eligibility"
            reason_field = f"{axis}_consumer_reason"
            eligibility = getattr(self, eligibility_field)
            reason = getattr(self, reason_field)
            expected = (
                getattr(registered, eligibility_field),
                getattr(registered, reason_field),
            )
            if (
                eligibility is ConsumerEligibility.UNKNOWN
                and reason is ConsumerReasonCode.NOT_CLASSIFIED
            ):
                object.__setattr__(self, eligibility_field, expected[0])
                object.__setattr__(self, reason_field, expected[1])
                eligibility, reason = expected
            validate_consumer_decision(
                axis,
                eligibility,
                reason,
            )
            if (eligibility, reason) != expected:
                raise ValueError(f"{axis} consumer decision differs from typed registry")
        if self.error_code is not None:
            _require_enum(self.error_code, FailureCode, "error_code")
            if not self.review_required:
                raise ValueError("failure requires review")
        warnings = tuple(self.warnings)
        if any(not isinstance(warning, str) or not warning for warning in warnings):
            raise ValueError("warnings contain an invalid value")
        object.__setattr__(self, "warnings", warnings)
        if self.blocked_reason is not None:
            _require_text(self.blocked_reason, "blocked_reason")


def _classify_health_dates(item: DatasetHealth) -> FreshnessClassification:
    context = FreshnessContext(
        market_date=None,
        expected_latest=item.expected_latest,
        actual_latest=item.actual_latest,
        partial=item.freshness_status is FreshnessStatus.PARTIAL,
    )
    if item.freshness_status is FreshnessStatus.EXPECTED_LAG:
        return FreshnessClassification.EXPECTED_LAG
    return _freshness_classification(context)


def dataset_health_from_freshness(
    run_id: str,
    spec: DatasetOperationSpec,
    freshness: FreshnessResult,
    *,
    collector_status: StageStatus = StageStatus.NOT_RUN,
    validation_status: StageStatus = StageStatus.NOT_RUN,
    downstream_status: StageStatus = StageStatus.NOT_RUN,
    auth_status: AuthStatus = AuthStatus.UNKNOWN,
    error_code: FailureCode | None = None,
    warnings: Iterable[str] = (),
) -> DatasetHealth:
    if freshness.dataset_id != spec.dataset_id:
        raise ValueError("freshness result belongs to another dataset")
    consumer = DATASET_UNIVERSE[spec.dataset_id]
    return DatasetHealth(
        run_id=run_id,
        dataset_id=spec.dataset_id,
        cadence=spec.cadence,
        tier=spec.tier,
        primary_source=spec.primary_source,
        expected_latest=freshness.expected_latest,
        actual_latest=freshness.actual_latest,
        freshness_status=freshness.freshness_status,
        collector_status=collector_status,
        validation_status=validation_status,
        downstream_status=downstream_status,
        auth_status=auth_status,
        error_code=error_code,
        review_required=freshness.review_required or error_code is not None,
        warnings=tuple(warnings),
        dashboard_required=spec.dashboard_required,
        model_input_required=spec.model_input_required,
        pit_status=spec.pit_status,
        operational_eligibility=spec.operational_eligibility,
        predictive_eligibility=spec.predictive_eligibility,
        display_consumer_eligibility=consumer.display_consumer_eligibility,
        display_consumer_reason=consumer.display_consumer_reason,
        research_consumer_eligibility=consumer.research_consumer_eligibility,
        research_consumer_reason=consumer.research_consumer_reason,
        predictive_consumer_eligibility=consumer.predictive_consumer_eligibility,
        predictive_consumer_reason=consumer.predictive_consumer_reason,
        freshness_classification=freshness.freshness_classification,
        finality_classification=freshness.finality_classification,
        operational_classification=spec.operational_classification,
        predictive_classification=spec.predictive_classification,
        blocked_reason=(
            "operational eligibility is blocked"
            if spec.operational_eligibility is OperationalEligibility.BLOCKED
            else "predictive/PIT eligibility is blocked"
            if spec.predictive_eligibility is PredictiveEligibility.BLOCKED
            else None
        ),
    )


@dataclass(frozen=True)
class DailyHealthReport:
    run_id: str
    as_of: datetime
    overall_status: DailyRunStatus
    critical_core_ready: bool
    dashboard_ready: bool
    model_input_ready: bool
    current_count: int
    expected_lag_count: int
    stale_count: int
    failed_count: int
    blocked_count: int
    datasets: tuple[DatasetHealth, ...]
    operational_blocked_count: int = 0
    predictive_blocked_count: int = 0
    research_only_count: int = 0
    freshness_unknown_count: int = 0
    finality_confirmed_count: int = 0
    finality_manual_confirmed_count: int = 0
    finality_as_retrieved_count: int = 0
    finality_unknown_count: int = 0
    operational_eligible_count: int = 0
    operational_manual_only_count: int = 0
    predictive_eligible_count: int = 0

    def __post_init__(self) -> None:
        if not _RUN_ID.fullmatch(self.run_id):
            raise ValueError("report run_id is invalid")
        _require_aware(self.as_of, "as_of")
        _require_enum(self.overall_status, DailyRunStatus, "overall_status")
        if self.overall_status not in {
            DailyRunStatus.SUCCEEDED, DailyRunStatus.DEGRADED, DailyRunStatus.FAILED,
        }:
            raise ValueError("health report requires a terminal aggregate status")
        ids = tuple(item.dataset_id for item in self.datasets)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("report datasets must be unique and sorted")
        if any(item.run_id != self.run_id for item in self.datasets):
            raise ValueError("dataset health run_id differs")
        expected_counts = (
            sum(item.freshness_classification is FreshnessClassification.CURRENT for item in self.datasets),
            sum(item.freshness_classification is FreshnessClassification.EXPECTED_LAG for item in self.datasets),
            sum(item.freshness_classification is FreshnessClassification.STALE for item in self.datasets),
            sum(_hard_failure(item) for item in self.datasets),
            sum(
                item.freshness_status is FreshnessStatus.BLOCKED
                or item.collector_status is StageStatus.BLOCKED
                or item.validation_status is StageStatus.BLOCKED
                or item.downstream_status is StageStatus.BLOCKED
                for item in self.datasets
            ),
            sum(
                item.operational_classification is OperationalClassification.BLOCKED
                for item in self.datasets
            ),
            sum(
                item.predictive_classification is PredictiveClassification.BLOCKED
                for item in self.datasets
            ),
            sum(
                item.predictive_classification is PredictiveClassification.RESEARCH_ONLY
                for item in self.datasets
            ),
            sum(item.freshness_classification is FreshnessClassification.UNKNOWN for item in self.datasets),
            sum(item.finality_classification is FinalityClassification.CONFIRMED for item in self.datasets),
            sum(item.finality_classification is FinalityClassification.MANUAL_CONFIRMED for item in self.datasets),
            sum(item.finality_classification is FinalityClassification.AS_RETRIEVED for item in self.datasets),
            sum(item.finality_classification is FinalityClassification.UNKNOWN for item in self.datasets),
            sum(item.operational_classification is OperationalClassification.ELIGIBLE for item in self.datasets),
            sum(item.operational_classification is OperationalClassification.MANUAL_ONLY for item in self.datasets),
            sum(item.predictive_classification is PredictiveClassification.ELIGIBLE for item in self.datasets),
        )
        if expected_counts != (
            self.current_count, self.expected_lag_count, self.stale_count,
            self.failed_count, self.blocked_count,
            self.operational_blocked_count, self.predictive_blocked_count,
            self.research_only_count,
            self.freshness_unknown_count,
            self.finality_confirmed_count, self.finality_manual_confirmed_count,
            self.finality_as_retrieved_count, self.finality_unknown_count,
            self.operational_eligible_count, self.operational_manual_only_count,
            self.predictive_eligible_count,
        ):
            raise ValueError("health report counts differ from dataset rows")

    def to_json_bytes(self) -> bytes:
        return serialize_json(self)

    def dimension_summary(self) -> dict[str, dict[str, int]]:
        """Stable GUI/report adapter; legacy flat counters remain available."""
        return {
            "freshness": {
                "CURRENT": self.current_count,
                "EXPECTED_LAG": self.expected_lag_count,
                "STALE": self.stale_count,
                "UNKNOWN": self.freshness_unknown_count,
            },
            "finality": {
                "CONFIRMED": self.finality_confirmed_count,
                "MANUAL_CONFIRMED": self.finality_manual_confirmed_count,
                "AS_RETRIEVED": self.finality_as_retrieved_count,
                "UNKNOWN": self.finality_unknown_count,
            },
            "operational": {
                "ELIGIBLE": self.operational_eligible_count,
                "MANUAL_ONLY": self.operational_manual_only_count,
                "BLOCKED": self.operational_blocked_count,
            },
            "predictive": {
                "ELIGIBLE": self.predictive_eligible_count,
                "BLOCKED": self.predictive_blocked_count,
                "RESEARCH_ONLY": self.research_only_count,
            },
        }


_READY_FRESHNESS = frozenset({FreshnessStatus.CURRENT, FreshnessStatus.EXPECTED_LAG})
_FAILED_STAGE = frozenset({StageStatus.FAILED, StageStatus.BLOCKED})
_BAD_AUTH = frozenset({AuthStatus.EXPIRED, AuthStatus.REFRESH_FAILED, AuthStatus.REAUTH_REQUIRED})


def _health_ready(item: DatasetHealth) -> bool:
    return (
        item.freshness_status in _READY_FRESHNESS
        and item.error_code is None
        and item.auth_status not in _BAD_AUTH
        and item.collector_status not in _FAILED_STAGE
        and item.validation_status not in _FAILED_STAGE
        and item.downstream_status not in _FAILED_STAGE
    )


def _hard_failure(item: DatasetHealth) -> bool:
    return (
        item.error_code is not None
        or item.collector_status is StageStatus.FAILED
        or item.validation_status is StageStatus.FAILED
        or item.downstream_status is StageStatus.FAILED
    )


def build_daily_health_report(
    *,
    run_id: str,
    as_of: datetime,
    datasets: Iterable[DatasetHealth],
) -> DailyHealthReport:
    _require_aware(as_of, "as_of")
    items = tuple(sorted(datasets, key=lambda item: item.dataset_id))
    ids = tuple(item.dataset_id for item in items)
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate dataset health")
    if any(item.run_id != run_id for item in items):
        raise ValueError("dataset health run_id differs")
    tier1 = tuple(item for item in items if item.tier is DatasetTier.TIER_1_CRITICAL)
    production = tuple(
        item for item in items
        if item.predictive_classification is not PredictiveClassification.RESEARCH_ONLY
    )
    dashboard = tuple(item for item in items if item.dashboard_required)
    model = tuple(item for item in items if item.model_input_required)
    critical_ready = bool(tier1) and all(_health_ready(item) for item in tier1)
    dashboard_ready = bool(dashboard) and all(_health_ready(item) for item in dashboard)
    model_ready = bool(model) and all(
        _health_ready(item) and item.pit_status is PitStatus.PIT_SAFE for item in model
    )
    if any(_hard_failure(item) for item in tier1):
        overall = DailyRunStatus.FAILED
    elif any(not _health_ready(item) for item in production):
        overall = DailyRunStatus.DEGRADED
    else:
        overall = DailyRunStatus.SUCCEEDED
    return DailyHealthReport(
        run_id=run_id,
        as_of=as_of,
        overall_status=overall,
        critical_core_ready=critical_ready,
        dashboard_ready=dashboard_ready,
        model_input_ready=model_ready,
        current_count=sum(item.freshness_classification is FreshnessClassification.CURRENT for item in items),
        expected_lag_count=sum(item.freshness_classification is FreshnessClassification.EXPECTED_LAG for item in items),
        stale_count=sum(item.freshness_classification is FreshnessClassification.STALE for item in items),
        failed_count=sum(_hard_failure(item) for item in items),
        blocked_count=sum(
            item.freshness_status is FreshnessStatus.BLOCKED
            or item.collector_status is StageStatus.BLOCKED
            or item.validation_status is StageStatus.BLOCKED
            or item.downstream_status is StageStatus.BLOCKED
            for item in items
        ),
        datasets=items,
        operational_blocked_count=sum(
            item.operational_classification is OperationalClassification.BLOCKED
            for item in items
        ),
        predictive_blocked_count=sum(
            item.predictive_classification is PredictiveClassification.BLOCKED
            for item in items
        ),
        research_only_count=sum(
            item.predictive_classification is PredictiveClassification.RESEARCH_ONLY
            for item in items
        ),
        freshness_unknown_count=sum(
            item.freshness_classification is FreshnessClassification.UNKNOWN for item in items
        ),
        finality_confirmed_count=sum(
            item.finality_classification is FinalityClassification.CONFIRMED for item in items
        ),
        finality_manual_confirmed_count=sum(
            item.finality_classification is FinalityClassification.MANUAL_CONFIRMED for item in items
        ),
        finality_as_retrieved_count=sum(
            item.finality_classification is FinalityClassification.AS_RETRIEVED for item in items
        ),
        finality_unknown_count=sum(
            item.finality_classification is FinalityClassification.UNKNOWN for item in items
        ),
        operational_eligible_count=sum(
            item.operational_classification is OperationalClassification.ELIGIBLE for item in items
        ),
        operational_manual_only_count=sum(
            item.operational_classification is OperationalClassification.MANUAL_ONLY for item in items
        ),
        predictive_eligible_count=sum(
            item.predictive_classification is PredictiveClassification.ELIGIBLE for item in items
        ),
    )


PROVIDER_AUTH_METADATA: Mapping[str, ProviderAuthMetadata] = MappingProxyType({
    "kbsec": ProviderAuthMetadata(
        "kbsec", AuthType.OAUTH2, ("KBSEC_APP_KEY", "KBSEC_APP_SECRET"),
    ),
    "bok_ecos": ProviderAuthMetadata(
        "bok_ecos", AuthType.API_KEY, ("BOK_ECOS_API_KEY",),
        expires_at_env_key="BOK_ECOS_API_KEY_EXPIRES_AT", auth_health_supported=True,
    ),
    "data_go_kr": ProviderAuthMetadata(
        "data_go_kr", AuthType.API_KEY, ("DATA_GO_KR_SERVICE_KEY",),
    ),
    "fred_api": ProviderAuthMetadata(
        "fred_api", AuthType.API_KEY, ("FRED_API_KEY",), auth_health_supported=False,
    ),
    "fred_public": ProviderAuthMetadata("fred_public", AuthType.NOT_APPLICABLE, ()),
    "ls": ProviderAuthMetadata(
        "ls", AuthType.OAUTH2, ("LS_APP_KEY", "LS_APP_SECRET"),
    ),
    "krx_open_api": ProviderAuthMetadata(
        "krx_open_api", AuthType.API_KEY, ("KRX_AUTH_KEY",),
        expires_at_env_key="KRX_AUTH_KEY_EXPIRES_AT", auth_health_supported=True,
    ),
    "pykrx_login": ProviderAuthMetadata(
        "pykrx_login", AuthType.SESSION_LOGIN, ("KRX_ID", "KRX_PW"),
    ),
    "tossinvest": ProviderAuthMetadata(
        "tossinvest", AuthType.OAUTH2,
        ("TOSSINVEST_CLIENT_ID", "TOSSINVEST_CLIENT_SECRET"),
        expires_at_env_key="TOSSINVEST_EXPIRES_AT",
        refresh_supported=True,
        auth_health_supported=True,
    ),
    "yahoo": ProviderAuthMetadata("yahoo", AuthType.NOT_APPLICABLE, ()),
    "cboe_public": ProviderAuthMetadata("cboe_public", AuthType.NOT_APPLICABLE, ()),
})


DAILY_LANE_READINESS = (
    LaneReadiness("KR_EQUITY_PROVISIONAL_DAILY", LaneReadinessStatus.READY,
        "KRX/pykrx market-wide equity OHLCV", "KR trading daily",
        "same completed XKRX session after 20:30 KST",
        "two-call KOSPI/KOSDAQ Landing-first capture and atomic append",
        "date-scoped immutable Landing plus checkpoint and latest-completed state",
        "offline frame parsing, target lag, atomic promotion, and API-zero idempotency",
        "display and condition alerts only; canonical rows take precedence; backtest blocked",
        True, None,
        "run immediately after Canonical Equity in the 20:30 bundle; missing both market frames is EXPECTED_PROVIDER_LAG"),
    LaneReadiness("KR_ETF_PRICE_DAILY", LaneReadinessStatus.READY,
        "authenticated KRX/pykrx", "KR trading daily", "latest completed XKRX session",
        "watchlist-plus-retained-master symbol resolution and per-symbol 30-session range capture",
        "immutable Landing frames plus exact state/checkpoint and atomic contract writes",
        "confirmed offline symbol/window/lag/idempotency fixtures and retained-date replay",
        "AS_RETRIEVED display-only health adapter", True, None,
        "run immediately after canonical equity in the 20:30 KST bundle; at most 10 selected symbols and never the full ETF universe"),
    LaneReadiness("KR_INDEX_FUNDAMENTAL_DAILY", LaneReadinessStatus.READY,
        "KRX MDCSTAT00702", "KR trading daily", "prior completed XKRX session",
        "two-ticker retry-zero range capture then joint normalized/state promotion",
        "immutable Landing hashes plus accepted-date state",
        "confirmed exact-session two-market atomic promotion and pre-network replay",
        "descriptive non-predictive health adapter", True, None,
        "run in the 09:10 KST bundle; official publication/revision finality remains unresolved"),
    LaneReadiness("KR_INDEX_DAILY", LaneReadinessStatus.READY,
        "KRX/pykrx", "KR trading daily", "explicit accepted KRX trading date",
        "reviewed one-date Landing capture then offline promotion", "per-capture manifest and retained state",
        "confirmed atomic two-dataset promotion and pre-network replay", "availability-aware health adapter", True,
        None, "schedule after 18:30 KST using the bounded empirical exact-date rule"),
    LaneReadiness("MARKET_INVESTOR_DAILY", LaneReadinessStatus.READY,
        "Toss Invest", "KR trading daily", "completed KRX date after 18:30 KST",
        "two-market exact-date capture and joint source/bridge promotion",
        "completed-date state plus atomic dataset writers",
        "confirmed KOSPI/KOSDAQ joint promotion and credential-free zero-call replay",
        "exact-date KOSPI Dashboard join", True, None,
        "schedule after 19:20 KST; market data only and no account/order endpoints"),
    LaneReadiness("GLOBAL_INDEX_DAILY", LaneReadinessStatus.READY,
        "Yahoo chart", "global trading daily", "completed XNYS session",
        "global_current_refresh yahoo prepare/promote", "run checkpoint plus promotion journal",
        "confirmed symbol-scoped CAS promotion and exact-session pre-network no-op",
        "global-index Dashboard health adapter", True, None,
        "schedule registered SP500/NASDAQ_COMPOSITE/NASDAQ100/SOX/DOW_JONES after session close"),
    LaneReadiness("FRED_DAILY", LaneReadinessStatus.READY,
        "FRED fredgraph CSV", "source-specific daily/weekly publication", "H.15 next-business-day, H.10 weekly, VIX next-business-day availability",
        "global_current_refresh FRED prepare/promote", "run checkpoint plus promotion journal",
        "confirmed CAS promotion and live pre-network same-end no-op", "typed-universe availability-aware health adapter", True,
        None, "operate after the latest source publication window and preserve predictive PIT block"),
    LaneReadiness("GLOBAL_ETF_DAILY", LaneReadinessStatus.READY,
        "Yahoo chart", "global trading daily", "explicit registered-symbol reviewed end date",
        "global_current_refresh yahoo_etf prepare/promote", "run checkpoint plus promotion journal",
        "confirmed symbol-scoped CAS promotion and exact-session pre-network no-op", "contract-registry ETF health adapter", True,
        None, "schedule the explicitly registered Yahoo ETF symbols"),
    LaneReadiness("GLOBAL_EQUITY_DAILY", LaneReadinessStatus.READY,
        "Yahoo chart", "global trading daily", "explicit registered-symbol reviewed end date",
        "global_current_refresh yahoo_equity prepare/promote", "run checkpoint plus promotion journal",
        "confirmed symbol-scoped CAS promotion and exact-session pre-network no-op", "contract-registry equity health adapter", True,
        None, "schedule the explicitly registered Yahoo equity symbols"),
    LaneReadiness("TOSSINVEST_US_QUOTES_30M", LaneReadinessStatus.READY,
        "Toss Securities Open API /api/v1/prices", "30-minute U.S. watchlist quote observation",
        "each :00/:30 boundary in [17:00,06:00) KST; outside the window retain the last in-window boundary",
        "one retry-zero multi-symbol quote request", "immutable Landing plus append-only Normalized Parquet and latest artifact",
        "confirmed one-call validation, atomic append, prior-valid preservation, and API-zero dry run",
        "descriptive direct-display health adapter; never an official bar or close", True, None,
        "run only in the bounded overnight window and preserve the last accepted observation outside it"),
    LaneReadiness("CBOE_DAILY_PCR", LaneReadinessStatus.READY,
        "Cboe Daily Market Statistics public daily file", "daily at 06:30 KST",
        "as-retrieved venue-scoped completed-date values; predictive finality blocked",
        "one public CSV/JSON request after coordinator endpoint verification",
        "immutable sha256 Landing plus date-keyed Normalized and last receipt",
        "confirmed one-call ceiling, date idempotency, strict scope/count validation, and API-zero dry run",
        "personal local display only; guest/public and redistribution forbidden", True, None,
        "run daily after 06:30 KST only after one coordinator curl verifies the configured machine URL"),
    LaneReadiness("KB_TRANSACTIONS_DAILY", LaneReadinessStatus.MANUAL_ONLY,
        "KB Securities SWQA2301", "calendar daily at 07:20 KST",
        "as-retrieved through the prior calendar day; seven-day overlap plus retained-row gap",
        "read-only paginated POST with at most 40 page calls",
        "identifier-free Landing, row-hash state, local cash-flow ledger, and last receipt",
        "raw-row sha256 idempotency, daily occurrence claim, and API-zero dry run",
        "local account return cash-flow input; OTHER rows excluded", False,
        "first coordinator live run pending",
        "run --confirm-live once, inspect the receipt and local ledger, then enable automation"),
    LaneReadiness("GLOBAL_COMMODITY_DAILY", LaneReadinessStatus.READY,
        "Yahoo chart", "global futures completed daily", "next US business day after 08:00 ET",
        "global_current_refresh yahoo_dashboard_futures prepare/promote", "run checkpoint plus promotion journal",
        "confirmed symbol-scoped CAS promotion, forming-session exclusion, and exact-session pre-network no-op",
        "NQ/Gold/WTI dashboard health adapter", True, None,
        "schedule NQ=F/GC=F/CL=F/ES=F/YM=F/DX=F after the provider finality window; descriptive daily bars only"),
    LaneReadiness("VKOSPI_DAILY", LaneReadinessStatus.READY,
        "KRX MDC", "KR trading daily", "reviewed finalized KRX date only",
        "offline exact-date retained-Landing append wrapper", "atomic Raw/Normalized/checkpoint transaction",
        "confirmed live exact-date match and zero-call replay", "post-close XKRX health adapter", True,
        None, "run after 18:30 KST with bounded empirical finality; retain PIT_LIMITED revision label"),
    LaneReadiness("CANONICAL_EQUITY_DAILY", LaneReadinessStatus.READY,
        "data.go.kr", "KR trading daily",
        "D+1 after 13:00 KST plus dual-stream exact-date acceptance", "canonical_equity_daily_incremental",
        "accepted-date state plus atomic breadth pending/completion checkpoint", "confirmed crash recovery and accepted-target integrity",
        "DailyHealthReport adapter", True, None,
        "catch up consecutive missing XKRX sessions within three-session, six-call, and ten-minute budgets; stop at the first unresolved date and retain the explicit finality gate"),
    LaneReadiness("KOSPI200_BREADTH_DAILY", LaneReadinessStatus.READY,
        "KRX MDCSTAT00601 plus canonical equity", "KR trading daily",
        "latest canonical accepted date only; exact-date membership required",
        "Landing-first exact KOSPI200 membership and atomic breadth transaction",
        "immutable Landing plus three-output transaction checkpoint",
        "confirmed live 200-member promotion and pre-network API-zero replay",
        "exact-date KOSPI200 breadth Dashboard adapter", True, None,
        "run after canonical equity in the 20:30 KST bundle; no membership backprojection"),
    LaneReadiness("LS_T8462_DAILY", LaneReadinessStatus.READY,
        "LS OpenAPI t8462", "KR post-close daily Raw observation",
        "one accepted post-full-session date; official revision timing unresolved",
        "18-scope Raw capture only", "provider Raw ledger/checkpoint", "confirmed live zero-call same-date replay",
        "research-only health row", True, None,
        "run at 20:30 KST as Raw-only; never promote to Normalized or predictive use"),
    LaneReadiness("DERIVATIVES_PRICE_DAILY", LaneReadinessStatus.READY,
        "data.go.kr 1160100 KOSPI200 futures/options exact-date endpoints", "KRX regular-session trading daily",
        "basDt exact-date after a completed successor XKRX session; approved T+1 rule",
        "offline exact-date Source-to-Bridge-to-Basis/PCR-to-Wall DAG transaction",
        "affected-date transaction journal/checkpoint", "confirmed atomic rollback, production readback, and API-zero replay",
        "seven-output contract-validated runtime probes bound to the completion checkpoint", True,
        None,
        "run bounded consecutive catch-up in the 20:30 KST bundle; preserve the two-call-per-session and T+1 limits"),
    LaneReadiness("DERIVATIVES_INVESTOR_DAILY", LaneReadinessStatus.MANUAL_ONLY,
        "settings-bound official KRX CSV", "KR trading daily",
        "operator-reviewed post-close file/date only", "manual futures investor import",
        "manual import audit/checkpoint", "confirmed for retained manual import",
        "manual health observation required", False,
        "permission and automatic post-close availability are not established",
        "retain the reviewed manual route; do not schedule"),
    LaneReadiness("SHORT_SELLING_DAILY", LaneReadinessStatus.READY,
        "authenticated KRX/pykrx", "KR trading daily",
        "KRX publishes same-date trading data after 20:00; project eligibility remains next XKRX session", "offline exact-date gate over bounded batch runner",
        "date-scoped two-market journal/checkpoint plus immutable Landing", "trading two-market atomic fixture suite and balance/investor live replay confirmed",
        "ExactDateDailyPlan freshness adapter", True, None,
        "schedule trading on the next-XKRX-session T+1 target"),
    LaneReadiness("SHORT_SELLING_BALANCE_DAILY", LaneReadinessStatus.READY,
        "authenticated KRX/pykrx", "KR trading daily",
        "T+2 after 18:10 KST; later provider corrections are possible",
        "bounded exact-date two-market collection", "date-scoped checkpoint plus immutable Landing",
        "exact-date balance fixtures and retained live replay",
        "AS_RETRIEVED health row; predictive use blocked", True, None,
        "run at 20:30 KST for the T+2 eligible date and preserve revisions as separate evidence"),
    LaneReadiness("SHORT_SELLING_INVESTOR_DAILY", LaneReadinessStatus.READY,
        "authenticated KRX/pykrx", "KR trading daily",
        "same trading date after 18:10 KST",
        "bounded exact-date four-scope collection", "date-scoped checkpoint plus immutable Landing",
        "exact-date investor fixtures and retained live replay",
        "AS_RETRIEVED health row; predictive use blocked", True, None,
        "run at 20:30 KST and retain the exact provider response"),
    LaneReadiness("LENDING_DAILY", LaneReadinessStatus.READY,
        "data.go.kr stock-lending APIs", "KR provider daily",
        "official D+1 provider business day after 13:00 KST", "offline exact-date gate over historical stock-lending runner",
        "historical state and exclusive lock", "three endpoints live through 2026-08-14 with zero-call replay",
        "provider-publication-aware health adapter", True, None,
        "schedule after 13:00 KST with exact target and independent endpoint stop boundaries"),
    LaneReadiness("LIQUIDITY_CREDIT_DAILY", LaneReadinessStatus.READY,
        "data.go.kr KOFIA statistics", "KR provider daily",
        "TWO_PASS_MANUAL_FINALITY_GATE", "immutable two-pass exact-date observations",
        "historical checkpoint plus per-date finality state", "stable dates replay before network",
        "ExactDateDailyPlan freshness adapter", True, None,
        "keep 20:30 provisional plus 09:10 confirmation in the existing KR bundle"),
    LaneReadiness("BOK_TREASURY_OBSERVATION_DAILY", LaneReadinessStatus.READY_WITH_FINALITY_GATE,
        "BOK ECOS StatisticSearch 817Y002",
        "provider-publication daily observations; not an XKRX session clock",
        "explicit reviewed target only; publication lag and revision finality are unknown",
        "six exact-date tenor calls, one per 2Y/3Y/5Y/10Y/20Y/30Y, retry zero",
        "single all-tenor transaction journal/checkpoint after immutable Landing",
        "pre-network API-zero replay contract; live replay not yet validated",
        "expected latest remains UNKNOWN and numeric display must fail closed",
        False,
        "provider publication/revision timing and a first reviewed daily live run remain unresolved",
        "finish the three-batch observation; keep source observation separate from canonical daily use"),
    LaneReadiness("BOK_FX_DAILY", LaneReadinessStatus.READY,
        "BOK ECOS StatisticSearch 731Y001 item 0000001",
        "provider weekday daily observations; BOK holiday calendar is unverified",
        "today at/after 16:00 KST, otherwise previous business day",
        "one retry-zero range call from retained latest + 1 through target, capped at 30 sessions",
        "immutable raw JSON, redacted call ledger, manifest, and atomic append-only Parquet",
        "offline fixture parsing, range cap, target lag, idempotency, and read-back validation",
        "display and account valuation; finality unknown and predictive/backtest use blocked",
        True, None,
        "run in the existing 20:30 KR market bundle; a missing target row is EXPECTED_PROVIDER_LAG"),
    LaneReadiness("TOSS_KR_TREASURY_DAILY", LaneReadinessStatus.READY,
        "Toss Invest market-indicator candles", "KR government-bond daily OHLC",
        "T+1 completed XKRX successor session; retained AS_RETRIEVED",
        "six bounded calls and one all-tenor atomic append", "incremental state plus immutable Landing",
        "contract validation and pre-network retained-date replay",
        "descriptive source health only; never relabel as BOK/KOFIA official yield", True, None,
        "run in the 20:30 KST bundle for the latest T+1 eligible date"),
    LaneReadiness("BROKER_SNAPSHOT", LaneReadinessStatus.MANUAL_ONLY,
        "KB Securities", "post-close snapshot", "weekday/clock gate; accepted exchange calendar absent",
        "kbsec_daily_market_snapshot", "daily state and exclusive lock", "partial across snapshot slices",
        "snapshot health adapter required", False,
        "calendar gate, crash recovery, and all-slice transaction are incomplete",
        "harden the existing snapshot transaction before scheduling"),
)


def _unknown_finality(timezone_name: str) -> FinalityPolicy:
    return FinalityPolicy(FinalityEvidence.UNKNOWN, timezone_name)


REPRESENTATIVE_DATASET_SPECS = (
    DatasetOperationSpec(
        dataset_id="kr_equity_canonical_universe_daily",
        economic_variable="Korean equity price, cap, and point-in-time provider universe chain",
        cadence=Cadence.KR_DAILY,
        tier=DatasetTier.TIER_1_CRITICAL,
        primary_source="marcap+krx_open_api+data_go_kr",
        contract_id="kr_equity_canonical_universe_daily",
        contract_version=1,
        operational_status=OperationalStatus.AUTO_READY,
        freshness_policy=FreshnessPolicy(
            "kr_equity_d_plus_1",
            "Asia/Seoul",
            "explicit reviewed Korean trading date after the official publication window",
            FinalityPolicy(
                FinalityEvidence.CONFIRMED_RULE,
                "Asia/Seoul",
                provider_available_rule="both official streams return exact-date non-empty rows",
                provider_final_rule="D+1 business day after 13:00 KST plus dual-stream validation",
                collection_window="14:10 and 20:30 KST scheduler occurrences",
            ),
        ),
        pipeline_dependencies=(
            "kr_equity_market_cap_daily", "kr_equity_price_daily",
            "kr_equity_universe_daily", "kr_market_breadth_daily",
        ),
        idempotency_status=IdempotencyStatus.CONFIRMED,
        pit_status=PitStatus.PIT_LIMITED,
        automation_enabled=True,
        provider_auth_id="data_go_kr",
        validation_policy="exact-date dual-stream schema/key/market validation and atomic acceptance",
        dashboard_required=True,
    ),
    DatasetOperationSpec(
        dataset_id="global_index_price_daily",
        economic_variable="Overseas index daily OHLCV",
        cadence=Cadence.GLOBAL_DAILY,
        tier=DatasetTier.TIER_1_CRITICAL,
        primary_source="yahoo_chart_api",
        contract_id="global_index_price_daily",
        contract_version=1,
        operational_status=OperationalStatus.AUTO_READY,
        freshness_policy=FreshnessPolicy(
            "global_index_reviewed_end",
            "UTC",
            "completed XNYS session retained after provider response validation",
            FinalityPolicy(
                FinalityEvidence.AS_RETRIEVED, "UTC",
                provider_available_rule="latest completed XNYS session",
                provider_final_rule="three exact registered symbols pass overlap/revision validation",
                collection_window="after the completed XNYS session",
            ),
        ),
        pipeline_dependencies=(),
        idempotency_status=IdempotencyStatus.CONFIRMED,
        pit_status=PitStatus.PIT_LIMITED,
        automation_enabled=True,
        provider_auth_id="yahoo",
        validation_policy="capture-first overlap/revision audit and offline CAS promotion",
        dashboard_required=True,
    ),
    DatasetOperationSpec(
        dataset_id="fred_treasury_yield_daily",
        economic_variable="U.S. Treasury constant maturity rates",
        cadence=Cadence.GLOBAL_DAILY,
        tier=DatasetTier.TIER_2_IMPORTANT,
        primary_source="fred",
        contract_id="fred_treasury_yield_daily",
        contract_version=1,
        operational_status=OperationalStatus.AUTO_READY,
        freshness_policy=FreshnessPolicy(
            "fred_h15_next_business_day_1615_et",
            "America/New_York",
            "H.15 observation is expected after the following provider business-day 16:15 ET release",
            FinalityPolicy(
                FinalityEvidence.AS_RETRIEVED, "America/New_York",
                provider_available_rule="H.15 is posted Monday-Friday at 16:15 ET",
                provider_final_rule="exact expected observation present in immutable FRED capture",
                collection_window="after 16:30 ET",
            ),
        ),
        pipeline_dependencies=("us_treasury_spread_daily",),
        idempotency_status=IdempotencyStatus.CONFIRMED,
        pit_status=PitStatus.PIT_BLOCKED,
        automation_enabled=True,
        provider_auth_id="fred_public",
        validation_policy="capture-first per-series revision audit and yield+spread atomic promotion",
        dashboard_required=True,
    ),
    DatasetOperationSpec(
        dataset_id="ls_t8462_daily_raw",
        economic_variable="Provider-specific KOSPI200 derivatives investor Raw observation",
        cadence=Cadence.KR_DAILY,
        tier=DatasetTier.TIER_3_DELAYED,
        primary_source="ls_openapi_t8462",
        contract_id=None,
        contract_version=None,
        operational_status=OperationalStatus.AUTO_READY,
        freshness_policy=FreshnessPolicy(
            "ls_t8462_post_close_observation",
            "Asia/Seoul",
            "explicit Korean trading date selected for one post-close Raw attempt",
            _unknown_finality("Asia/Seoul"),
        ),
        pipeline_dependencies=(),
        idempotency_status=IdempotencyStatus.CONFIRMED,
        pit_status=PitStatus.NON_PREDICTIVE,
        automation_enabled=True,
        provider_auth_id="ls",
        validation_policy="18-scope Raw/provenance/ledger completeness; no Normalized promotion",
    ),
)


def _registered_manual_spec(
    dataset_id: str,
    economic_variable: str,
    source: str,
    contract_version: int,
    *,
    provider_auth_id: str,
    status: OperationalStatus = OperationalStatus.MANUAL_READY,
    pit: PitStatus = PitStatus.PIT_BLOCKED,
    cadence: Cadence = Cadence.KR_DAILY,
    tier: DatasetTier = DatasetTier.TIER_2_IMPORTANT,
    dependencies: tuple[str, ...] = (),
    idempotency: IdempotencyStatus = IdempotencyStatus.PARTIAL,
    dashboard_required: bool = False,
    automation_enabled: bool = False,
) -> DatasetOperationSpec:
    """Build conservative registry metadata; this never grants execution authority."""
    timezone_name = "UTC" if cadence is Cadence.GLOBAL_DAILY else "Asia/Seoul"
    return DatasetOperationSpec(
        dataset_id=dataset_id,
        economic_variable=economic_variable,
        cadence=cadence,
        tier=tier,
        primary_source=source,
        contract_id=dataset_id,
        contract_version=contract_version,
        operational_status=status,
        freshness_policy=FreshnessPolicy(
            f"{dataset_id}.reviewed_finality",
            timezone_name,
            "explicit source-calendar date supplied by the owning reviewed operation",
            _unknown_finality(timezone_name),
        ),
        pipeline_dependencies=dependencies,
        idempotency_status=idempotency,
        pit_status=pit,
        automation_enabled=automation_enabled,
        provider_auth_id=provider_auth_id,
        validation_policy="contract, key, exact-date, provenance, and retained-history validation",
        dashboard_required=dashboard_required,
    )


# Dataset-level coverage for the retained core lanes.  Presence in this registry
# is health/planning metadata only; Data Status plus an active runbook remains
# required before any provider call or production mutation.
CORE_DATASET_SPECS = REPRESENTATIVE_DATASET_SPECS + (
    _registered_manual_spec(
        "kr_equity_price_provisional_daily",
        "Same-session provisional Korean equity daily prices",
        "KRX/pykrx stock.get_market_ohlcv_by_ticker",
        1,
        provider_auth_id="pykrx_login",
        status=OperationalStatus.AUTO_READY,
        pit=PitStatus.NON_PREDICTIVE,
        tier=DatasetTier.TIER_1_CRITICAL,
        idempotency=IdempotencyStatus.CONFIRMED,
        dashboard_required=True,
        automation_enabled=True,
    ),
    _registered_manual_spec("kr_equity_price_daily", "Korean equity daily prices", "data.go.kr",
        2, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_LIMITED, tier=DatasetTier.TIER_1_CRITICAL,
        idempotency=IdempotencyStatus.CONFIRMED, automation_enabled=True),
    _registered_manual_spec("kr_equity_market_cap_daily", "Korean equity daily market cap", "data.go.kr",
        2, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_LIMITED, tier=DatasetTier.TIER_1_CRITICAL,
        idempotency=IdempotencyStatus.CONFIRMED, automation_enabled=True),
    _registered_manual_spec("kr_equity_universe_daily", "Provider equity universe observations", "data.go.kr",
        2, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_LIMITED, tier=DatasetTier.TIER_1_CRITICAL,
        idempotency=IdempotencyStatus.CONFIRMED, automation_enabled=True),
    _registered_manual_spec("kr_market_breadth_daily", "Canonical-universe market breadth", "derived canonical equity",
        1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_LIMITED, tier=DatasetTier.TIER_1_CRITICAL,
        dependencies=("kr_equity_canonical_universe_daily", "kr_equity_price_daily"),
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True,
        automation_enabled=True),
    _registered_manual_spec("kr_index_constituent_daily", "Exact-date KOSPI200 membership", "KRX MDCSTAT00601:1028",
        1, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_SAFE, tier=DatasetTier.TIER_1_CRITICAL,
        idempotency=IdempotencyStatus.CONFIRMED, automation_enabled=True),
    _registered_manual_spec("kr_kospi200_constituent_price_daily", "Exact-member KOSPI200 daily OHLCV", "retained exact-date equity prices",
        1, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_SAFE, tier=DatasetTier.TIER_1_CRITICAL,
        dependencies=("kr_index_constituent_daily", "kr_equity_price_daily"),
        idempotency=IdempotencyStatus.CONFIRMED, automation_enabled=True),
    _registered_manual_spec("kr_kospi200_breadth_daily", "Exact-date KOSPI200 market breadth", "exact membership and retained equity prices",
        1, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_SAFE, tier=DatasetTier.TIER_1_CRITICAL,
        dependencies=("kr_kospi200_constituent_price_daily", "kr_equity_price_daily"),
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True,
        automation_enabled=True),
    _registered_manual_spec("kr_index_daily", "Korean market index OHLCV", "KRX/pykrx",
        2, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_LIMITED, tier=DatasetTier.TIER_1_CRITICAL,
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True, automation_enabled=True),
    _registered_manual_spec(
        "kr_index_fundamental_daily",
        "KOSPI/KOSDAQ weighted PER, PBR, and dividend yield",
        "KRX MDCSTAT00702 tickers 1001/2001", 1,
        provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.NON_PREDICTIVE, tier=DatasetTier.TIER_2_IMPORTANT,
        idempotency=IdempotencyStatus.CONFIRMED, automation_enabled=True,
    ),
    _registered_manual_spec("kr_kospi200_index_daily", "KOSPI200 spot index OHLCV", "KRX/pykrx ticker 1028",
        1, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_SAFE, tier=DatasetTier.TIER_1_CRITICAL,
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True, automation_enabled=True),
    _registered_manual_spec("fred_vix_daily", "FRED VIX close", "FRED VIXCLS", 1,
        provider_auth_id="fred_public", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_LIMITED, cadence=Cadence.GLOBAL_DAILY,
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True, automation_enabled=True),
    _registered_manual_spec("fred_usd_fx_daily", "FRED USD foreign-exchange observations", "FRED",
        1, provider_auth_id="fred_public", status=OperationalStatus.AUTO_READY,
        cadence=Cadence.GLOBAL_DAILY, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    _registered_manual_spec("us_treasury_spread_daily", "Derived U.S. Treasury term spreads", "FRED yields",
        1, provider_auth_id="fred_public", cadence=Cadence.GLOBAL_DAILY,
        dependencies=("fred_treasury_yield_daily",), idempotency=IdempotencyStatus.CONFIRMED),
    _registered_manual_spec(
        "us_vix_term_structure_daily",
        "VIX 기간구조: 1개월/3개월 비율 < 1 = 콘탱고(평온), > 1 = 백워데이션(공포)",
        "FRED VIXCLS plus Yahoo Cboe volatility indices",
        1, provider_auth_id="yahoo", cadence=Cadence.GLOBAL_DAILY,
        dependencies=("fred_vix_daily", "global_index_price_daily"),
        idempotency=IdempotencyStatus.CONFIRMED,
    ),
    _registered_manual_spec("global_etf_price_daily", "Registered global ETF OHLCV", "Yahoo chart API",
        1, provider_auth_id="yahoo", status=OperationalStatus.AUTO_READY,
        cadence=Cadence.GLOBAL_DAILY, idempotency=IdempotencyStatus.CONFIRMED,
        dashboard_required=True, automation_enabled=True),
    _registered_manual_spec("global_equity_price_daily", "Registered global equity OHLCV", "Yahoo chart API",
        1, provider_auth_id="yahoo", status=OperationalStatus.AUTO_READY,
        cadence=Cadence.GLOBAL_DAILY, idempotency=IdempotencyStatus.CONFIRMED,
        dashboard_required=True, automation_enabled=True),
    DatasetOperationSpec(
        dataset_id="tossinvest_us_quote_30m",
        economic_variable="As-retrieved U.S. watchlist quotes",
        cadence=Cadence.GLOBAL_30M,
        tier=DatasetTier.TIER_3_DELAYED,
        primary_source="Toss Securities Open API /api/v1/prices",
        contract_id="tossinvest_us_quote_30m",
        contract_version=1,
        operational_status=OperationalStatus.AUTO_READY,
        freshness_policy=FreshnessPolicy(
            "tossinvest_us_quote_global_30m_window",
            "Asia/Seoul",
            "latest :00/:30 due boundary in [17:00,06:00) KST; outside the window retain the last in-window boundary",
            FinalityPolicy(
                FinalityEvidence.AS_RETRIEVED,
                "Asia/Seoul",
                provider_available_rule="one accepted multi-symbol response at the governing in-window boundary",
                provider_final_rule="as-retrieved positive finite USD quotes with source timestamps",
                collection_window="[17:00,06:00) KST at :00/:30 boundaries",
            ),
        ),
        pipeline_dependencies=(),
        idempotency_status=IdempotencyStatus.CONFIRMED,
        pit_status=PitStatus.NON_PREDICTIVE,
        automation_enabled=True,
        provider_auth_id="tossinvest",
        validation_policy="one-call identity, USD, positive-finite price, timestamp, Landing, and atomic append validation",
        dashboard_required=True,
    ),
    DatasetOperationSpec(
        dataset_id="cboe_daily_pcr_daily",
        economic_variable="Cboe venue-scoped option product-group put/call ratios",
        cadence=Cadence.GLOBAL_DAILY,
        tier=DatasetTier.TIER_3_DELAYED,
        primary_source="Cboe Daily Market Statistics public daily file",
        contract_id="cboe_daily_pcr_daily",
        contract_version=1,
        operational_status=OperationalStatus.MANUAL_READY,
        freshness_policy=FreshnessPolicy(
            "cboe_daily_pcr_0630_kst", "Asia/Seoul",
            "latest Cboe observation date due once daily at 06:30 KST",
            FinalityPolicy(
                FinalityEvidence.AS_RETRIEVED, "Asia/Seoul",
                provider_available_rule="five required Cboe product-group rows in one response",
                provider_final_rule="as-retrieved descriptive display only; predictive finality blocked",
                collection_window="one request per date at or after 06:30 KST",
            ),
        ),
        pipeline_dependencies=(),
        idempotency_status=IdempotencyStatus.CONFIRMED,
        pit_status=PitStatus.NON_PREDICTIVE,
        automation_enabled=False,
        provider_auth_id="cboe_public",
        validation_policy="personal-only, one-call, Landing sha256, exact date/scope, non-negative counts, put/call ratios, atomic promotion",
        dashboard_required=True,
    ),
    DatasetOperationSpec(
        dataset_id="kbsec_transactions_daily",
        economic_variable="Identifier-free KB account cash-flow transaction history",
        cadence=Cadence.KR_DAILY,
        tier=DatasetTier.TIER_3_DELAYED,
        primary_source="KB Securities SWQA2301",
        contract_id="kbsec_transactions_daily",
        contract_version=1,
        operational_status=OperationalStatus.MANUAL_READY,
        freshness_policy=FreshnessPolicy(
            "kbsec_transactions_0720_kst", "Asia/Seoul",
            "prior calendar day covered once daily at 07:20 KST",
            FinalityPolicy(
                FinalityEvidence.AS_RETRIEVED, "Asia/Seoul",
                provider_available_rule="blank nxt_key after at most 40 six-row pages",
                provider_final_rule="as-retrieved read-only transaction history",
                collection_window="daily at 07:20 KST after the 07:00/07:10 balance snapshots",
            ),
        ),
        pipeline_dependencies=(),
        idempotency_status=IdempotencyStatus.CONFIRMED,
        pit_status=PitStatus.NON_PREDICTIVE,
        automation_enabled=False,
        provider_auth_id="kbsec",
        validation_policy=(
            "Landing-first identifier-free projection, strict pagination/page ceiling, "
            "raw-row sha256 merge, atomic local-ledger write, and prior-valid preservation"
        ),
        dashboard_required=True,
    ),
    _registered_manual_spec(
        "kr_etf_master", "Current Korean ETF identities", "KRX/pykrx",
        1, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        cadence=Cadence.KR_DAILY,
        pit=PitStatus.PIT_BLOCKED, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True,
    ),
    _registered_manual_spec(
        "kr_etf_price_daily", "Selected Korean ETF daily OHLCV and NAV", "KRX/pykrx",
        1, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_BLOCKED, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True,
    ),
    _registered_manual_spec("kr_vkospi_daily", "Official KRX VKOSPI daily", "KRX MDCSTAT01201:1300",
        1, provider_auth_id="krx_open_api", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_LIMITED, idempotency=IdempotencyStatus.CONFIRMED,
        dashboard_required=True, automation_enabled=True),
    _registered_manual_spec("kr_derivatives_futures_daily", "Source futures prices/activity", "data.go.kr",
        1, provider_auth_id="data_go_kr", status=OperationalStatus.BLOCKED),
    _registered_manual_spec("kr_derivatives_options_daily", "Source option prices/activity", "data.go.kr",
        1, provider_auth_id="data_go_kr", status=OperationalStatus.BLOCKED),
    _registered_manual_spec("kr_kospi200_futures_provider_bridge_daily", "KOSPI200 futures provider bridge",
        "retained provider inputs", 1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        dependencies=("kr_kospi200_futures_daily",), idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    _registered_manual_spec("kr_kospi200_options_provider_bridge_daily", "KOSPI200 options provider bridge",
        "retained provider inputs", 1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        dependencies=("kr_kospi200_options_daily",), idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    _registered_manual_spec("kr_kospi200_futures_nearest_listed_daily", "Nearest-listed KOSPI200 futures and basis",
        "futures bridge", 1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_SAFE, dependencies=("kr_kospi200_futures_provider_bridge_daily", "kr_kospi200_index_daily"),
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True,
        automation_enabled=True),
    _registered_manual_spec("kr_kospi200_option_pcr_daily", "KOSPI200 option put/call ratios",
        "options bridge", 1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_SAFE, dependencies=("kr_kospi200_options_provider_bridge_daily",),
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True,
        automation_enabled=True),
    _registered_manual_spec("kr_kospi200_option_walls_daily", "KOSPI200 option wall observations",
        "options bridge plus KOSPI200 spot", 1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_SAFE, dependencies=("kr_kospi200_index_daily", "kr_kospi200_options_provider_bridge_daily"),
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True,
        automation_enabled=True),
    _registered_manual_spec("kr_kospi200_futures_investor_net_purchase_daily", "Official futures investor net purchase",
        "settings-bound official KRX CSV", 1, provider_auth_id="krx_open_api", pit=PitStatus.PIT_LIMITED),
    _registered_manual_spec("kr_short_selling_trading_daily", "Official aggregate short-selling trading", "KRX/pykrx",
        2, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True,
        automation_enabled=True),
    _registered_manual_spec("kr_short_selling_balance_daily", "Official short-selling balances", "KRX/pykrx",
        2, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_BLOCKED, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    _registered_manual_spec("kr_short_selling_investor_daily", "Official short-selling by investor", "KRX/pykrx",
        2, provider_auth_id="pykrx_login", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_BLOCKED, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    _registered_manual_spec("kr_stock_lending_daily", "Per-symbol stock lending", "data.go.kr",
        1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        idempotency=IdempotencyStatus.CONFIRMED, automation_enabled=True),
    _registered_manual_spec("kr_stock_lending_market_daily", "Market stock-lending aggregate", "data.go.kr",
        1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        idempotency=IdempotencyStatus.CONFIRMED, automation_enabled=True),
    _registered_manual_spec("kr_stock_lending_participant_daily", "Participant stock-lending aggregate", "data.go.kr",
        1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        idempotency=IdempotencyStatus.CONFIRMED, automation_enabled=True),
    _registered_manual_spec("kr_market_liquidity_daily", "Securities-market liquidity aggregates", "data.go.kr KOFIA",
        1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_BLOCKED, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    _registered_manual_spec("kr_credit_balance_daily", "Credit and collateral-loan balances", "data.go.kr KOFIA",
        1, provider_auth_id="data_go_kr", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_BLOCKED, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    _registered_manual_spec("kr_market_investor_trading_daily", "Toss market investor trading source", "Toss Invest",
        1, provider_auth_id="tossinvest", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_BLOCKED, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    _registered_manual_spec("kr_treasury_yield_daily", "Korean government-bond yield OHLC observations", "Toss Invest",
        1, provider_auth_id="tossinvest", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_BLOCKED, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    _registered_manual_spec("bok_ecos_kr_treasury_yield_source_observation", "BOK ECOS Korean treasury source observation", "BOK ECOS",
        1, provider_auth_id="bok_ecos", status=OperationalStatus.AUTO_READY,
        pit=PitStatus.PIT_BLOCKED, idempotency=IdempotencyStatus.CONFIRMED,
        automation_enabled=True),
    DatasetOperationSpec(
        dataset_id="bok_ecos_usd_krw_daily",
        economic_variable="Official daily KRW per USD reference rate",
        cadence=Cadence.KR_DAILY,
        tier=DatasetTier.TIER_1_CRITICAL,
        primary_source="BOK ECOS StatisticSearch 731Y001/0000001",
        contract_id="bok_ecos_usd_krw_daily",
        contract_version=1,
        operational_status=OperationalStatus.AUTO_READY,
        freshness_policy=FreshnessPolicy(
            "bok_ecos_fx_daily_1600_kst",
            "Asia/Seoul",
            "today at or after 16:00 KST, otherwise previous provider business day",
            FinalityPolicy(
                FinalityEvidence.UNKNOWN,
                "Asia/Seoul",
                provider_available_rule=(
                    "requested target row present; absence is EXPECTED_PROVIDER_LAG"
                ),
                provider_final_rule=(
                    "UNVERIFIED publication/revision timing; descriptive use only"
                ),
                collection_window="existing KR market task at 20:30 KST",
            ),
        ),
        pipeline_dependencies=(),
        idempotency_status=IdempotencyStatus.CONFIRMED,
        pit_status=PitStatus.PIT_BLOCKED,
        automation_enabled=True,
        provider_auth_id="bok_ecos",
        validation_policy=(
            "Landing-first table/item/date/unit/value validation, append-only merge, "
            "atomic Parquet, and read-back validation"
        ),
        dashboard_required=True,
    ),
    _registered_manual_spec("kr_market_investor_net_purchase_bridge_daily", "Provider-boundary market investor bridge",
        "legacy pykrx plus Toss", 1, provider_auth_id="tossinvest", pit=PitStatus.PIT_BLOCKED,
        dependencies=("kr_market_investor_net_purchase_daily", "kr_market_investor_trading_daily"),
        idempotency=IdempotencyStatus.CONFIRMED, dashboard_required=True,
        status=OperationalStatus.AUTO_READY, automation_enabled=True),
    _registered_manual_spec("kb_investor_flow_snapshot", "Provider-specific current investor-flow snapshot",
        "KB Securities IVSA0070", 3, provider_auth_id="kbsec", status=OperationalStatus.SNAPSHOT_ONLY,
        pit=PitStatus.NON_PREDICTIVE, cadence=Cadence.SNAPSHOT, tier=DatasetTier.TIER_3_DELAYED),
)


DATASET_OPERATIONS = DatasetOperationsRegistry(
    CORE_DATASET_SPECS,
    PROVIDER_AUTH_METADATA,
)

# The executable/health registry above contains only reviewed operational
# datasets.  The full retained/contracted catalog is separate
# so research/static rows cannot become executable merely by being known.
from stock_data.orchestration.dataset_universe import (  # noqa: E402
    AutomationPolicy, DataGrain, DataRole, DatasetRefreshClass,
    DatasetUniverseRegistry, DatasetUniverseSpec, GuiUse,
    OperationalBlockerReason, PredictivePitStatus, RefreshPolicy,
    RegistryDisposition, SchedulerGroup, SchedulerManagement,
    UniverseOperationalStatus, build_dataset_universe,
)

DATASET_UNIVERSE = build_dataset_universe(DATASET_OPERATIONS)


@dataclass(frozen=True)
class DailyGapStatus:
    dataset_id: str
    role: str
    refresh_policy: str
    operational_status: str
    automation_policy: str
    retained_start: date | None
    retained_latest: date | None
    expected_latest: date | None
    finality: str
    missing_dates: tuple[date, ...]
    plan_status: str
    pre_network_noop: bool


def build_daily_universe_gap_status(
    *,
    expected_dates: Mapping[str, Iterable[date]],
    retained_dates: Mapping[str, Iterable[date]],
    finality_by_dataset: Mapping[str, str],
) -> tuple[DailyGapStatus, ...]:
    """Evaluate all daily-grain rows from explicit calendars only.

    The function deliberately has no weekday/today fallback. A lane without an
    externally reviewed expected-date set or retained-date index remains
    ``CALENDAR_UNAVAILABLE`` and cannot authenticate to a provider.
    """
    rows = []
    for spec in DATASET_UNIVERSE.values():
        if spec.data_grain is not DataGrain.DAILY:
            continue
        expected = tuple(sorted(set(expected_dates.get(spec.dataset_id, ()))))
        retained = tuple(sorted(set(retained_dates.get(spec.dataset_id, ()))))
        if any(not isinstance(value, date) for value in (*expected, *retained)):
            raise TypeError("daily gap calendars must contain date values")
        if spec.dataset_id not in expected_dates or spec.dataset_id not in retained_dates:
            missing = ()
            status = "CALENDAR_UNAVAILABLE"
            noop = False
        else:
            missing = tuple(value for value in expected if value not in set(retained))
            status = "MISSING_DATES" if missing else "NOOP_IDEMPOTENT"
            noop = not missing
        rows.append(DailyGapStatus(
            dataset_id=spec.dataset_id,
            role=spec.data_role.value,
            refresh_policy=spec.refresh_policy.value,
            operational_status=spec.operational_status.value,
            automation_policy=spec.automation_policy.value,
            retained_start=retained[0] if retained else None,
            retained_latest=retained[-1] if retained else None,
            expected_latest=expected[-1] if expected else None,
            finality=finality_by_dataset.get(spec.dataset_id, "UNKNOWN"),
            missing_dates=missing,
            plan_status=status,
            pre_network_noop=noop,
        ))
    if len(rows) != 72:
        raise RuntimeError("daily-grain universe count differs from the typed registry")
    return tuple(rows)


__all__ = [
    "AuthStatus", "AuthType", "Cadence", "DAILY_LANE_READINESS", "DATASET_OPERATIONS", "DailyHealthReport",
    "DailyDryRunEntry", "DailyRun", "DailyRunLock", "DailyRunLockError", "DailyRunStatus", "DatasetHealth",
    "DatasetOperationSpec", "DatasetOperationsRegistry", "DatasetTier", "FAILURE_POLICIES",
    "DATASET_UNIVERSE", "AutomationPolicy", "DailyGapStatus", "DataGrain", "DataRole", "DatasetRefreshClass",
    "ConsumerEligibility", "ConsumerReasonCode", "DatasetUniverseRegistry",
    "DatasetUniverseSpec", "GuiUse", "OperationalBlockerReason",
    "PredictivePitStatus", "RefreshPolicy", "SchedulerManagement", "UniverseOperationalStatus",
    "FailureCode", "FailurePolicy", "FinalityClassification", "FinalityEvidence", "FinalityPolicy", "FinalityStatus",
    "FreshnessContext", "FreshnessPolicy", "FreshnessReason", "FreshnessResult",
    "FreshnessClassification", "FreshnessStatus", "IdempotencyStatus", "OperationalClassification", "OperationalEligibility", "OperationalStatus",
    "LaneReadiness", "LaneReadinessStatus", "PitStatus", "PredictiveClassification", "PredictiveEligibility",
    "RegistryDisposition", "SchedulerGroup",
    "CORE_DATASET_SPECS", "PROVIDER_AUTH_METADATA", "ProviderAuthMetadata", "REPRESENTATIVE_DATASET_SPECS",
    "StageStatus", "build_daily_health_report", "dataset_health_from_freshness",
    "build_daily_dry_run_plan", "build_daily_operations_dry_run",
    "build_daily_universe_gap_status",
    "evaluate_auth_status", "evaluate_freshness", "plan_daily_operations", "policy_for_failure",
    "read_run_checkpoint", "serialize_json", "transition_run", "write_run_checkpoint",
]
