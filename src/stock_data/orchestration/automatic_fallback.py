from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable, Generic, Protocol, TypeVar


T = TypeVar("T")
SnapshotT = TypeVar("SnapshotT")
StagedT = TypeVar("StagedT")


class FailureKind(StrEnum):
    TIMEOUT = "TIMEOUT"
    HTTP_ERROR = "HTTP_ERROR"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    EMPTY_RESULT = "EMPTY_RESULT"
    AUTHENTICATION_REJECTED = "AUTHENTICATION_REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    AMBIGUOUS_SEMANTICS = "AMBIGUOUS_SEMANTICS"
    PROMOTION_ERROR = "PROMOTION_ERROR"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


class ExecutionKind(StrEnum):
    NORMAL_SCHEDULE = "NORMAL_SCHEDULE"
    AUTHORIZED_HEALTH_CHECK = "AUTHORIZED_HEALTH_CHECK"


class DecisionOutcome(StrEnum):
    PRIMARY_ACCEPTED = "PRIMARY_ACCEPTED"
    PRIMARY_RECOVERED = "PRIMARY_RECOVERED"
    FALLBACK_ACCEPTED = "FALLBACK_ACCEPTED"
    PRIOR_VALID_PRESERVED = "PRIOR_VALID_PRESERVED"
    NUMERIC_FREE_FAIL_CLOSED = "NUMERIC_FREE_FAIL_CLOSED"


class ProviderRole(StrEnum):
    PRIMARY = "PRIMARY"
    FALLBACK = "FALLBACK"
    NONE = "NONE"


class FallbackInvariantError(RuntimeError):
    """The controller or an adapter violated a non-retryable control invariant."""


class AttemptFailure(RuntimeError):
    """A sanitized, typed failure from one provider attempt or validation step."""

    def __init__(
        self,
        kind: FailureKind,
        *,
        safe_code: str,
        request_count: int = 1,
        retry_count: int = 0,
    ) -> None:
        if not safe_code or any(character.isspace() for character in safe_code):
            raise ValueError("safe_code must be one non-empty token")
        if request_count < 0:
            raise ValueError("request_count must be non-negative")
        if retry_count != 0:
            raise FallbackInvariantError("automatic fallback requires retry_count=0")
        super().__init__(safe_code)
        self.kind = kind
        self.safe_code = safe_code
        self.request_count = request_count
        self.retry_count = retry_count


@dataclass(frozen=True)
class SourceProvenance:
    provider: str
    upstream_provider: str
    source_route: str
    retrieved_at_utc: str
    request_count: int
    retry_count: int = 0

    def __post_init__(self) -> None:
        if not self.provider or not self.upstream_provider or not self.source_route:
            raise ValueError("provider provenance fields must be non-empty")
        parsed = datetime.fromisoformat(self.retrieved_at_utc)
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("retrieved_at_utc must be timezone-aware UTC")
        if self.request_count < 1:
            raise ValueError("accepted provider observation requires at least one request")
        if self.retry_count != 0:
            raise FallbackInvariantError("accepted provider observation must use retry_count=0")


@dataclass(frozen=True)
class SourceObservation(Generic[T]):
    value: T
    provenance: SourceProvenance


@dataclass(frozen=True)
class ValidationReceipt:
    selected_observation_date: str
    schema_id: str
    result: str = "PASS"

    def __post_init__(self) -> None:
        if self.result != "PASS":
            raise ValueError("only a passing validation receipt may be returned")
        if not self.selected_observation_date or not self.schema_id:
            raise ValueError("validation receipt fields must be non-empty")


@dataclass(frozen=True)
class CircuitRecord:
    is_open: bool = False
    failure_kind: FailureKind | None = None
    safe_code: str | None = None
    generation: int = 0


class CircuitStore(Protocol):
    def load(self, route_id: str) -> CircuitRecord: ...

    def save(self, route_id: str, record: CircuitRecord) -> None: ...


class InMemoryCircuitStore:
    """Deterministic store for tests and callers that persist state elsewhere."""

    def __init__(self) -> None:
        self._records: dict[str, CircuitRecord] = {}

    def load(self, route_id: str) -> CircuitRecord:
        return self._records.get(route_id, CircuitRecord())

    def save(self, route_id: str, record: CircuitRecord) -> None:
        self._records[route_id] = record


@dataclass(frozen=True)
class RoutePolicy:
    route_id: str
    primary_provider: str
    primary_route: str
    fallback_provider: str
    fallback_upstream_provider: str
    fallback_route: str
    fallback_enabled: bool
    max_primary_requests: int = 1
    max_fallback_requests: int = 1
    eligible_primary_failures: frozenset[FailureKind] = frozenset(
        {
            FailureKind.TIMEOUT,
            FailureKind.HTTP_ERROR,
            FailureKind.SCHEMA_ERROR,
            FailureKind.EMPTY_RESULT,
        }
    )

    def __post_init__(self) -> None:
        fields = (
            self.route_id,
            self.primary_provider,
            self.primary_route,
            self.fallback_provider,
            self.fallback_upstream_provider,
            self.fallback_route,
        )
        if any(not value for value in fields):
            raise ValueError("route policy identity fields must be non-empty")
        if self.max_primary_requests < 1 or self.max_fallback_requests < 1:
            raise ValueError("route request budgets must be positive")
        if FailureKind.PROMOTION_ERROR in self.eligible_primary_failures:
            raise ValueError("a local promotion failure must never trigger a provider fallback")


@dataclass(frozen=True)
class DecisionEvent:
    sequence: int
    event: str
    route_id: str
    role: ProviderRole
    provider: str | None
    upstream_provider: str | None
    source_route: str | None
    attempt: int
    request_count: int
    retry_count: int
    failure_kind: FailureKind | None = None
    safe_code: str | None = None
    validation_result: str | None = None
    selected_observation_date: str | None = None
    outcome: DecisionOutcome | None = None


@dataclass(frozen=True)
class FallbackDecision(Generic[T]):
    route_id: str
    execution_kind: ExecutionKind
    outcome: DecisionOutcome
    selected_role: ProviderRole
    active_value: T | None
    preserved_prior: bool
    primary_attempts: int
    fallback_attempts: int
    primary_requests: int
    fallback_requests: int
    circuit_before: CircuitRecord
    circuit_after: CircuitRecord
    events: tuple[DecisionEvent, ...]


class AtomicPromotionBoundary(Protocol, Generic[T, SnapshotT, StagedT]):
    """One atomic data+provenance+circuit decision boundary owned by the caller.

    When ``decision.circuit_after`` differs from ``circuit_before``, ``commit``
    must persist that circuit transition in the same transaction as the value
    and decision. ``rollback`` must restore all three.
    """

    def snapshot(self) -> SnapshotT: ...

    def stage(self, observation: SourceObservation[T], decision: FallbackDecision[T]) -> StagedT: ...

    def commit(self, staged: StagedT) -> None: ...

    def verify_readback(
        self, observation: SourceObservation[T], decision: FallbackDecision[T]
    ) -> None: ...

    def rollback(self, snapshot: SnapshotT) -> None: ...


Validator = Callable[[SourceObservation[T]], ValidationReceipt]
Attempt = Callable[[], SourceObservation[T]]


@dataclass(frozen=True)
class _Accepted(Generic[T]):
    observation: SourceObservation[T]
    receipt: ValidationReceipt


@dataclass(frozen=True)
class _Failed:
    failure: AttemptFailure


class AutomaticFallbackController(Generic[T]):
    """Official-first, retry-zero controller for one explicitly scoped route.

    The controller never selects a second fallback and never writes provider
    data itself. The supplied promotion boundary must stage the selected value
    and immutable decision/provenance together, then provide verified readback
    or rollback.
    """

    def __init__(self, circuit_store: CircuitStore) -> None:
        self._circuits = circuit_store

    @staticmethod
    def _attempt(
        attempt: Attempt[T], validator: Validator[T], expected_provider: str,
        expected_upstream: str | None, expected_route: str,
    ) -> _Accepted[T] | _Failed:
        try:
            observation = attempt()
        except AttemptFailure as failure:
            return _Failed(failure)
        except Exception:
            return _Failed(
                AttemptFailure(
                    FailureKind.UNEXPECTED_ERROR,
                    safe_code="UNEXPECTED_PROVIDER_ERROR",
                    request_count=0,
                )
            )

        provenance = observation.provenance
        if provenance.provider != expected_provider or provenance.source_route != expected_route:
            return _Failed(
                AttemptFailure(
                    FailureKind.AMBIGUOUS_SEMANTICS,
                    safe_code="PROVENANCE_IDENTITY_MISMATCH",
                    request_count=provenance.request_count,
                )
            )
        if expected_upstream is not None and provenance.upstream_provider != expected_upstream:
            return _Failed(
                AttemptFailure(
                    FailureKind.AMBIGUOUS_SEMANTICS,
                    safe_code="UPSTREAM_IDENTITY_MISMATCH",
                    request_count=provenance.request_count,
                )
            )
        try:
            receipt = validator(observation)
        except AttemptFailure as failure:
            if failure.request_count == 0:
                failure = AttemptFailure(
                    failure.kind,
                    safe_code=failure.safe_code,
                    request_count=provenance.request_count,
                )
            return _Failed(failure)
        except Exception:
            return _Failed(
                AttemptFailure(
                    FailureKind.SCHEMA_ERROR,
                    safe_code="UNEXPECTED_VALIDATOR_ERROR",
                    request_count=provenance.request_count,
                )
            )
        return _Accepted(observation, receipt)

    @staticmethod
    def _final_event(
        events: list[DecisionEvent], policy: RoutePolicy, outcome: DecisionOutcome,
        role: ProviderRole,
    ) -> None:
        events.append(
            DecisionEvent(
                sequence=len(events) + 1,
                event="DECISION",
                route_id=policy.route_id,
                role=role,
                provider=None,
                upstream_provider=None,
                source_route=None,
                attempt=0,
                request_count=0,
                retry_count=0,
                outcome=outcome,
            )
        )

    @staticmethod
    def _decision(
        *, policy: RoutePolicy, execution_kind: ExecutionKind,
        outcome: DecisionOutcome, role: ProviderRole, active_value: T | None,
        prior_valid: T | None, primary_attempts: int, fallback_attempts: int,
        primary_requests: int, fallback_requests: int,
        circuit_before: CircuitRecord, circuit_after: CircuitRecord,
        events: list[DecisionEvent],
    ) -> FallbackDecision[T]:
        AutomaticFallbackController._final_event(events, policy, outcome, role)
        return FallbackDecision(
            route_id=policy.route_id,
            execution_kind=execution_kind,
            outcome=outcome,
            selected_role=role,
            active_value=active_value,
            preserved_prior=prior_valid is not None and active_value is prior_valid,
            primary_attempts=primary_attempts,
            fallback_attempts=fallback_attempts,
            primary_requests=primary_requests,
            fallback_requests=fallback_requests,
            circuit_before=circuit_before,
            circuit_after=circuit_after,
            events=tuple(events),
        )

    def _promote(
        self,
        boundary: AtomicPromotionBoundary[T, SnapshotT, StagedT],
        accepted: _Accepted[T], decision: FallbackDecision[T],
    ) -> AttemptFailure | None:
        snapshot = boundary.snapshot()
        try:
            staged = boundary.stage(accepted.observation, decision)
            boundary.commit(staged)
            boundary.verify_readback(accepted.observation, decision)
            if self._circuits.load(decision.route_id) != decision.circuit_after:
                raise FallbackInvariantError("atomic promotion did not persist circuit state")
        except Exception:
            try:
                boundary.rollback(snapshot)
            except Exception as rollback_error:
                raise FallbackInvariantError("atomic promotion rollback failed") from rollback_error
            return AttemptFailure(
                FailureKind.PROMOTION_ERROR,
                safe_code="ATOMIC_PROMOTION_ROLLED_BACK",
                request_count=0,
            )
        return None

    def execute(
        self,
        *,
        policy: RoutePolicy,
        execution_kind: ExecutionKind,
        primary_attempt: Attempt[T],
        primary_validator: Validator[T],
        fallback_attempt: Attempt[T],
        fallback_validator: Validator[T],
        promotion: AtomicPromotionBoundary[T, SnapshotT, StagedT],
        prior_valid: T | None,
    ) -> FallbackDecision[T]:
        circuit_before = self._circuits.load(policy.route_id)
        circuit_after = circuit_before
        events: list[DecisionEvent] = []

        primary = self._attempt(
            primary_attempt,
            primary_validator,
            policy.primary_provider,
            None,
            policy.primary_route,
        )
        primary_requests = (
            primary.observation.provenance.request_count
            if isinstance(primary, _Accepted)
            else primary.failure.request_count
        )
        if primary_requests > policy.max_primary_requests:
            raise FallbackInvariantError("primary adapter exceeded policy request budget")

        if isinstance(primary, _Accepted):
            events.append(
                DecisionEvent(
                    sequence=1,
                    event="PRIMARY_VALIDATED",
                    route_id=policy.route_id,
                    role=ProviderRole.PRIMARY,
                    provider=primary.observation.provenance.provider,
                    upstream_provider=primary.observation.provenance.upstream_provider,
                    source_route=primary.observation.provenance.source_route,
                    attempt=1,
                    request_count=primary_requests,
                    retry_count=0,
                    validation_result=primary.receipt.result,
                    selected_observation_date=primary.receipt.selected_observation_date,
                )
            )
            outcome = (
                DecisionOutcome.PRIMARY_RECOVERED
                if circuit_before.is_open
                else DecisionOutcome.PRIMARY_ACCEPTED
            )
            if circuit_before.is_open:
                circuit_after = CircuitRecord(generation=circuit_before.generation + 1)
            provisional = self._decision(
                policy=policy,
                execution_kind=execution_kind,
                outcome=outcome,
                role=ProviderRole.PRIMARY,
                active_value=primary.observation.value,
                prior_valid=prior_valid,
                primary_attempts=1,
                fallback_attempts=0,
                primary_requests=primary_requests,
                fallback_requests=0,
                circuit_before=circuit_before,
                circuit_after=circuit_after,
                events=events,
            )
            promotion_failure = self._promote(promotion, primary, provisional)
            if promotion_failure is None:
                return provisional
            if circuit_before.is_open:
                circuit_after = circuit_before
            events = list(provisional.events[:-1])
            events.append(
                DecisionEvent(
                    sequence=len(events) + 1,
                    event="PRIMARY_PROMOTION_FAILED",
                    route_id=policy.route_id,
                    role=ProviderRole.PRIMARY,
                    provider=primary.observation.provenance.provider,
                    upstream_provider=primary.observation.provenance.upstream_provider,
                    source_route=primary.observation.provenance.source_route,
                    attempt=1,
                    request_count=0,
                    retry_count=0,
                    failure_kind=promotion_failure.kind,
                    safe_code=promotion_failure.safe_code,
                )
            )
            return self._decision(
                policy=policy,
                execution_kind=execution_kind,
                outcome=(
                    DecisionOutcome.PRIOR_VALID_PRESERVED
                    if prior_valid is not None
                    else DecisionOutcome.NUMERIC_FREE_FAIL_CLOSED
                ),
                role=ProviderRole.NONE,
                active_value=prior_valid,
                prior_valid=prior_valid,
                primary_attempts=1,
                fallback_attempts=0,
                primary_requests=primary_requests,
                fallback_requests=0,
                circuit_before=circuit_before,
                circuit_after=circuit_after,
                events=events,
            )

        primary_failure = primary.failure
        events.append(
            DecisionEvent(
                sequence=1,
                event="PRIMARY_FAILED",
                route_id=policy.route_id,
                role=ProviderRole.PRIMARY,
                provider=policy.primary_provider,
                upstream_provider=None,
                source_route=policy.primary_route,
                attempt=1,
                request_count=primary_failure.request_count,
                retry_count=0,
                failure_kind=primary_failure.kind,
                safe_code=primary_failure.safe_code,
            )
        )

        fallback_allowed = (
            policy.fallback_enabled
            and not circuit_before.is_open
            and primary_failure.kind in policy.eligible_primary_failures
        )
        if not fallback_allowed:
            return self._decision(
                policy=policy,
                execution_kind=execution_kind,
                outcome=(
                    DecisionOutcome.PRIOR_VALID_PRESERVED
                    if prior_valid is not None
                    else DecisionOutcome.NUMERIC_FREE_FAIL_CLOSED
                ),
                role=ProviderRole.NONE,
                active_value=prior_valid,
                prior_valid=prior_valid,
                primary_attempts=1,
                fallback_attempts=0,
                primary_requests=primary_failure.request_count,
                fallback_requests=0,
                circuit_before=circuit_before,
                circuit_after=circuit_after,
                events=events,
            )

        fallback = self._attempt(
            fallback_attempt,
            fallback_validator,
            policy.fallback_provider,
            policy.fallback_upstream_provider,
            policy.fallback_route,
        )
        fallback_requests = (
            fallback.observation.provenance.request_count
            if isinstance(fallback, _Accepted)
            else fallback.failure.request_count
        )
        if fallback_requests > policy.max_fallback_requests:
            raise FallbackInvariantError("fallback adapter exceeded policy request budget")

        if isinstance(fallback, _Failed):
            failure = fallback.failure
            circuit_after = CircuitRecord(
                is_open=True,
                failure_kind=failure.kind,
                safe_code=failure.safe_code,
                generation=circuit_before.generation + 1,
            )
            self._circuits.save(policy.route_id, circuit_after)
            events.append(
                DecisionEvent(
                    sequence=2,
                    event="FALLBACK_FAILED_CIRCUIT_OPEN",
                    route_id=policy.route_id,
                    role=ProviderRole.FALLBACK,
                    provider=policy.fallback_provider,
                    upstream_provider=policy.fallback_upstream_provider,
                    source_route=policy.fallback_route,
                    attempt=1,
                    request_count=failure.request_count,
                    retry_count=0,
                    failure_kind=failure.kind,
                    safe_code=failure.safe_code,
                )
            )
            return self._decision(
                policy=policy,
                execution_kind=execution_kind,
                outcome=(
                    DecisionOutcome.PRIOR_VALID_PRESERVED
                    if prior_valid is not None
                    else DecisionOutcome.NUMERIC_FREE_FAIL_CLOSED
                ),
                role=ProviderRole.NONE,
                active_value=prior_valid,
                prior_valid=prior_valid,
                primary_attempts=1,
                fallback_attempts=1,
                primary_requests=primary_failure.request_count,
                fallback_requests=failure.request_count,
                circuit_before=circuit_before,
                circuit_after=circuit_after,
                events=events,
            )

        events.append(
            DecisionEvent(
                sequence=2,
                event="FALLBACK_VALIDATED",
                route_id=policy.route_id,
                role=ProviderRole.FALLBACK,
                provider=fallback.observation.provenance.provider,
                upstream_provider=fallback.observation.provenance.upstream_provider,
                source_route=fallback.observation.provenance.source_route,
                attempt=1,
                request_count=fallback_requests,
                retry_count=0,
                validation_result=fallback.receipt.result,
                selected_observation_date=fallback.receipt.selected_observation_date,
            )
        )
        provisional = self._decision(
            policy=policy,
            execution_kind=execution_kind,
            outcome=DecisionOutcome.FALLBACK_ACCEPTED,
            role=ProviderRole.FALLBACK,
            active_value=fallback.observation.value,
            prior_valid=prior_valid,
            primary_attempts=1,
            fallback_attempts=1,
            primary_requests=primary_failure.request_count,
            fallback_requests=fallback_requests,
            circuit_before=circuit_before,
            circuit_after=circuit_after,
            events=events,
        )
        promotion_failure = self._promote(promotion, fallback, provisional)
        if promotion_failure is None:
            return provisional

        circuit_after = CircuitRecord(
            is_open=True,
            failure_kind=promotion_failure.kind,
            safe_code=promotion_failure.safe_code,
            generation=circuit_before.generation + 1,
        )
        self._circuits.save(policy.route_id, circuit_after)
        events = list(provisional.events[:-1])
        events.append(
            DecisionEvent(
                sequence=len(events) + 1,
                event="FALLBACK_PROMOTION_FAILED_CIRCUIT_OPEN",
                route_id=policy.route_id,
                role=ProviderRole.FALLBACK,
                provider=fallback.observation.provenance.provider,
                upstream_provider=fallback.observation.provenance.upstream_provider,
                source_route=fallback.observation.provenance.source_route,
                attempt=1,
                request_count=0,
                retry_count=0,
                failure_kind=promotion_failure.kind,
                safe_code=promotion_failure.safe_code,
            )
        )
        return self._decision(
            policy=policy,
            execution_kind=execution_kind,
            outcome=(
                DecisionOutcome.PRIOR_VALID_PRESERVED
                if prior_valid is not None
                else DecisionOutcome.NUMERIC_FREE_FAIL_CLOSED
            ),
            role=ProviderRole.NONE,
            active_value=prior_valid,
            prior_valid=prior_valid,
            primary_attempts=1,
            fallback_attempts=1,
            primary_requests=primary_failure.request_count,
            fallback_requests=fallback_requests,
            circuit_before=circuit_before,
            circuit_after=circuit_after,
            events=events,
        )
