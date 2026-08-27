"""Provider-independent, display-only current-observation foundation.

This module deliberately contains no transport, credentials, scheduler, GUI, or
canonical-data integration.  An authorized caller injects one primary attempt
and one explicitly scoped fallback attempt.  The selected observation is kept
in a small atomic local envelope that remains separate from EOD history.
"""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Callable, Generic, TypeVar

from stock_data.orchestration.automatic_fallback import (
    AtomicPromotionBoundary,
    AutomaticFallbackController,
    CircuitRecord,
    CircuitStore,
    ExecutionKind,
    FallbackDecision,
    RoutePolicy,
    SourceObservation,
    ValidationReceipt,
)


class CurrentObservationError(RuntimeError):
    """Raised for a local current-observation contract or storage violation."""


class ObservationInterval(StrEnum):
    SNAPSHOT = "snapshot"
    MINUTES_15 = "15m"
    MINUTES_30 = "30m"
    MINUTES_60 = "60m"
    DAILY = "1d"


class ObservationFinality(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    POST_CLOSE_SNAPSHOT = "POST_CLOSE_SNAPSHOT"
    AS_RETRIEVED = "AS_RETRIEVED"
    FINAL = "FINAL"


class ObservationTimestampBasis(StrEnum):
    """What the effective observation timestamp actually represents."""

    PROVIDER_TIMESTAMP = "PROVIDER_TIMESTAMP"
    RETRIEVAL_TIMESTAMP = "RETRIEVAL_TIMESTAMP"


class CurrentObservationOutcome(StrEnum):
    DECIDED = "DECIDED"
    COALESCED = "COALESCED"
    API_ZERO_REPLAY = "API_ZERO_REPLAY"


@dataclass(frozen=True)
class ObservationIdentity:
    """Exact identity; callers must not treat a ticker alone as interchangeable."""

    dataset_id: str
    market: str
    symbol: str

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if not re.fullmatch(r"[A-Z0-9_.=^:-]{1,64}", value):
                raise ValueError(f"invalid current-observation {name}")


@dataclass(frozen=True)
class CurrentObservation:
    """One source-native observation that is ineligible for canonical/Backtest use."""

    route_id: str
    identity: ObservationIdentity
    interval: ObservationInterval
    value: float
    unit: str
    provider: str
    upstream_provider: str
    source_route: str
    provider_timestamp_utc: str
    retrieved_at_utc: str
    finality: ObservationFinality
    display_only: bool = True
    pit_safe: bool = False
    timestamp_basis: ObservationTimestampBasis = ObservationTimestampBasis.PROVIDER_TIMESTAMP

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:=-]{1,128}", self.route_id):
            raise ValueError("invalid current-observation route_id")
        self.identity.validate()
        if not isinstance(self.value, (int, float)) or isinstance(self.value, bool):
            raise ValueError("current-observation value must be numeric")
        if not math.isfinite(float(self.value)):
            raise ValueError("current-observation value must be finite")
        if not re.fullmatch(r"[A-Za-z0-9% /_.^-]{1,48}", self.unit):
            raise ValueError("invalid current-observation unit")
        for value in (self.provider, self.upstream_provider, self.source_route):
            if not value or any(character.isspace() for character in value):
                raise ValueError("current-observation provenance fields must be tokens")
        provider_time = _utc_timestamp(self.provider_timestamp_utc, "provider_timestamp_utc")
        retrieved = _utc_timestamp(self.retrieved_at_utc, "retrieved_at_utc")
        if provider_time > retrieved:
            raise ValueError("provider timestamp cannot be after retrieval")
        if not isinstance(self.timestamp_basis, ObservationTimestampBasis):
            raise ValueError("invalid current-observation timestamp basis")
        if (
            self.timestamp_basis is ObservationTimestampBasis.RETRIEVAL_TIMESTAMP
            and provider_time != retrieved
        ):
            raise ValueError(
                "retrieval-time observations must retain the retrieval instant as the effective timestamp"
            )
        if not self.display_only or self.pit_safe:
            raise ValueError("current observations must remain display-only and PIT-blocked")


@dataclass(frozen=True)
class CurrentObservationRoute:
    """One exact provider priority route and its allowed display intervals."""

    fallback_policy: RoutePolicy
    identity: ObservationIdentity
    interval_precedence: tuple[ObservationInterval, ...]

    def __post_init__(self) -> None:
        self.identity.validate()
        if not self.interval_precedence:
            raise ValueError("current-observation route needs at least one interval")
        if len(set(self.interval_precedence)) != len(self.interval_precedence):
            raise ValueError("current-observation interval precedence cannot repeat an interval")

    @property
    def route_id(self) -> str:
        return self.fallback_policy.route_id


@dataclass(frozen=True)
class CurrentObservationRefreshResult:
    route_id: str
    outcome: CurrentObservationOutcome
    observation: CurrentObservation | None
    decision: FallbackDecision[CurrentObservation] | None
    api_calls: int


def _utc_timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return parsed


def _observation_payload(observation: CurrentObservation) -> dict[str, object]:
    return {
        "route_id": observation.route_id,
        "identity": asdict(observation.identity),
        "interval": observation.interval.value,
        "value": observation.value,
        "unit": observation.unit,
        "provider": observation.provider,
        "upstream_provider": observation.upstream_provider,
        "source_route": observation.source_route,
        "provider_timestamp_utc": observation.provider_timestamp_utc,
        "retrieved_at_utc": observation.retrieved_at_utc,
        "finality": observation.finality.value,
        "display_only": observation.display_only,
        "pit_safe": observation.pit_safe,
        "timestamp_basis": observation.timestamp_basis.value,
    }


def _decode_observation(payload: object) -> CurrentObservation:
    legacy_fields = {
        "route_id", "identity", "interval", "value", "unit", "provider",
        "upstream_provider", "source_route", "provider_timestamp_utc",
        "retrieved_at_utc", "finality", "display_only", "pit_safe",
    }
    if (
        not isinstance(payload, dict)
        or frozenset(payload) not in {frozenset(legacy_fields), frozenset(legacy_fields | {"timestamp_basis"})}
    ):
        raise CurrentObservationError("current-observation row schema mismatch")
    identity_payload = payload["identity"]
    if not isinstance(identity_payload, dict) or set(identity_payload) != set(ObservationIdentity.__dataclass_fields__):
        raise CurrentObservationError("current-observation identity schema mismatch")
    try:
        observation = CurrentObservation(
            route_id=str(payload["route_id"]),
            identity=ObservationIdentity(**identity_payload),
            interval=ObservationInterval(str(payload["interval"])),
            value=float(payload["value"]),
            unit=str(payload["unit"]),
            provider=str(payload["provider"]),
            upstream_provider=str(payload["upstream_provider"]),
            source_route=str(payload["source_route"]),
            provider_timestamp_utc=str(payload["provider_timestamp_utc"]),
            retrieved_at_utc=str(payload["retrieved_at_utc"]),
            finality=ObservationFinality(str(payload["finality"])),
            display_only=payload["display_only"],
            pit_safe=payload["pit_safe"],
            timestamp_basis=ObservationTimestampBasis(
                str(payload.get("timestamp_basis", ObservationTimestampBasis.PROVIDER_TIMESTAMP.value))
            ),
        )
        if not isinstance(observation.display_only, bool) or not isinstance(observation.pit_safe, bool):
            raise ValueError("display-only flags must be booleans")
        observation.validate()
        return observation
    except (TypeError, ValueError) as error:
        raise CurrentObservationError("invalid current-observation row") from error


def _record_payload(record: CircuitRecord) -> dict[str, object]:
    return {
        "is_open": record.is_open,
        "failure_kind": record.failure_kind.value if record.failure_kind else None,
        "safe_code": record.safe_code,
        "generation": record.generation,
    }


def _decode_record(payload: object) -> CircuitRecord:
    if not isinstance(payload, dict) or set(payload) != {
        "is_open", "failure_kind", "safe_code", "generation",
    }:
        raise CurrentObservationError("current-observation circuit schema mismatch")
    from stock_data.orchestration.automatic_fallback import FailureKind

    try:
        return CircuitRecord(
            is_open=payload["is_open"],
            failure_kind=(FailureKind(payload["failure_kind"]) if payload["failure_kind"] else None),
            safe_code=payload["safe_code"],
            generation=payload["generation"],
        )
    except (TypeError, ValueError) as error:
        raise CurrentObservationError("invalid current-observation circuit") from error


class CurrentObservationFileStore(CircuitStore):
    """Strict, atomically replaced local envelope for display-only observations."""

    _SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def _read_state(self) -> dict[str, object]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": self._SCHEMA_VERSION, "observations": [], "circuits": {}, "decisions": {}}
        except (OSError, json.JSONDecodeError) as error:
            raise CurrentObservationError("current-observation storage is unreadable") from error
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "observations", "circuits", "decisions",
        } or payload["schema_version"] != self._SCHEMA_VERSION:
            raise CurrentObservationError("current-observation envelope schema mismatch")
        if not isinstance(payload["observations"], list) or not isinstance(payload["circuits"], dict) or not isinstance(payload["decisions"], dict):
            raise CurrentObservationError("current-observation envelope types are invalid")
        observations = [_decode_observation(item) for item in payload["observations"]]
        keys = {(item.route_id, item.identity, item.interval) for item in observations}
        if len(keys) != len(observations):
            raise CurrentObservationError("duplicate current-observation storage key")
        for route_id, record in payload["circuits"].items():
            if not isinstance(route_id, str):
                raise CurrentObservationError("invalid current-observation circuit route")
            _decode_record(record)
        return payload

    def _write_state(self, state: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def load(self, route_id: str) -> CircuitRecord:
        with self._lock:
            state = self._read_state()
            payload = state["circuits"].get(route_id)
            return CircuitRecord() if payload is None else _decode_record(payload)

    def save(self, route_id: str, record: CircuitRecord) -> None:
        with self._lock:
            state = self._read_state()
            circuits = dict(state["circuits"])
            circuits[route_id] = _record_payload(record)
            state["circuits"] = circuits
            self._write_state(state)

    def observations(self, route: CurrentObservationRoute) -> tuple[CurrentObservation, ...]:
        with self._lock:
            state = self._read_state()
        return tuple(
            item for item in (_decode_observation(raw) for raw in state["observations"])
            if item.route_id == route.route_id and item.identity == route.identity
        )

    def select(self, route: CurrentObservationRoute) -> CurrentObservation | None:
        candidates = {item.interval: item for item in self.observations(route)}
        for interval in route.interval_precedence:
            if interval in candidates:
                return candidates[interval]
        return None

    def promotion_boundary(self, route: CurrentObservationRoute) -> "_FilePromotionBoundary":
        return _FilePromotionBoundary(self, route)


@dataclass(frozen=True)
class _Snapshot:
    existed: bool
    content: bytes | None


class _FilePromotionBoundary(AtomicPromotionBoundary[CurrentObservation, _Snapshot, dict[str, object]]):
    def __init__(self, store: CurrentObservationFileStore, route: CurrentObservationRoute) -> None:
        self._store = store
        self._route = route

    def snapshot(self) -> _Snapshot:
        try:
            return _Snapshot(True, self._store.path.read_bytes())
        except FileNotFoundError:
            return _Snapshot(False, None)

    def stage(
        self, observation: SourceObservation[CurrentObservation], decision: FallbackDecision[CurrentObservation],
    ) -> dict[str, object]:
        state = self._store._read_state()
        observations = [_decode_observation(raw) for raw in state["observations"]]
        observations = [
            item for item in observations
            if (item.route_id, item.identity, item.interval) != (
                observation.value.route_id, observation.value.identity, observation.value.interval
            )
        ]
        observations.append(observation.value)
        observations.sort(key=lambda item: (item.route_id, item.identity.dataset_id, item.identity.market, item.identity.symbol, item.interval.value))
        state["observations"] = [_observation_payload(item) for item in observations]
        circuits = dict(state["circuits"])
        circuits[self._route.route_id] = _record_payload(decision.circuit_after)
        state["circuits"] = circuits
        decisions = dict(state["decisions"])
        decisions[self._route.route_id] = {
            "outcome": decision.outcome.value,
            "selected_role": decision.selected_role.value,
            "primary_requests": decision.primary_requests,
            "fallback_requests": decision.fallback_requests,
        }
        state["decisions"] = decisions
        return state

    def commit(self, staged: dict[str, object]) -> None:
        self._store._write_state(staged)

    def verify_readback(
        self, observation: SourceObservation[CurrentObservation], decision: FallbackDecision[CurrentObservation],
    ) -> None:
        if observation.value not in self._store.observations(self._route):
            raise CurrentObservationError("current-observation atomic readback mismatch")
        state = self._store._read_state()
        if state["decisions"].get(self._route.route_id, {}).get("outcome") != decision.outcome.value:
            raise CurrentObservationError("current-observation decision readback mismatch")

    def rollback(self, snapshot: _Snapshot) -> None:
        if snapshot.existed:
            assert snapshot.content is not None
            temporary = self._store.path.with_name(f".{self._store.path.name}.{uuid.uuid4().hex}.rollback")
            self._store.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with temporary.open("xb") as stream:
                    stream.write(snapshot.content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self._store.path)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        else:
            try:
                self._store.path.unlink()
            except FileNotFoundError:
                pass


Attempt = Callable[[], SourceObservation[CurrentObservation]]
T = TypeVar("T")


class CurrentObservationCoordinator:
    """Coalesce a route refresh and delegate official-first fallback control."""

    def __init__(self, store: CurrentObservationFileStore) -> None:
        self._store = store
        self._state_lock = Lock()
        self._operation_lock = Lock()
        self._active_routes: set[str] = set()

    @staticmethod
    def _validator(route: CurrentObservationRoute) -> Callable[[SourceObservation[CurrentObservation]], ValidationReceipt]:
        def validate(source: SourceObservation[CurrentObservation]) -> ValidationReceipt:
            observation = source.value
            observation.validate()
            if observation.route_id != route.route_id or observation.identity != route.identity:
                raise ValueError("current-observation exact identity mismatch")
            if observation.interval not in route.interval_precedence:
                raise ValueError("current-observation interval is not allowed for route")
            if observation.provider != source.provenance.provider:
                raise ValueError("current-observation provider provenance mismatch")
            if observation.upstream_provider != source.provenance.upstream_provider:
                raise ValueError("current-observation upstream provenance mismatch")
            if observation.source_route != source.provenance.source_route:
                raise ValueError("current-observation source-route provenance mismatch")
            if observation.retrieved_at_utc != source.provenance.retrieved_at_utc:
                raise ValueError("current-observation retrieval provenance mismatch")
            return ValidationReceipt(
                selected_observation_date=observation.provider_timestamp_utc[:10],
                schema_id="current-observation-v1",
            )
        return validate

    def replay(self, route: CurrentObservationRoute) -> CurrentObservationRefreshResult:
        """Return retained selected state with no provider attempt or storage write."""
        return CurrentObservationRefreshResult(
            route_id=route.route_id,
            outcome=CurrentObservationOutcome.API_ZERO_REPLAY,
            observation=self._store.select(route),
            decision=None,
            api_calls=0,
        )

    def refresh(
        self,
        route: CurrentObservationRoute,
        *,
        primary_attempt: Attempt,
        fallback_attempt: Attempt,
        execution_kind: ExecutionKind = ExecutionKind.NORMAL_SCHEDULE,
    ) -> CurrentObservationRefreshResult:
        with self._state_lock:
            if route.route_id in self._active_routes:
                return CurrentObservationRefreshResult(
                    route.route_id, CurrentObservationOutcome.COALESCED,
                    self._store.select(route), None, 0,
                )
            self._active_routes.add(route.route_id)
        try:
            # One envelope is shared across routes, so stage/commit/readback must
            # not interleave with another route's atomic replacement.
            with self._operation_lock:
                decision = AutomaticFallbackController[CurrentObservation](self._store).execute(
                    policy=route.fallback_policy,
                    execution_kind=execution_kind,
                    primary_attempt=primary_attempt,
                    primary_validator=self._validator(route),
                    fallback_attempt=fallback_attempt,
                    fallback_validator=self._validator(route),
                    promotion=self._store.promotion_boundary(route),
                    prior_valid=self._store.select(route),
                )
            return CurrentObservationRefreshResult(
                route_id=route.route_id,
                outcome=CurrentObservationOutcome.DECIDED,
                observation=self._store.select(route),
                decision=decision,
                api_calls=decision.primary_requests + decision.fallback_requests,
            )
        finally:
            with self._state_lock:
                self._active_routes.remove(route.route_id)
