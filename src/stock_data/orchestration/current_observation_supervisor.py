"""Transport-free process supervisor for activated display-only observations.

Provider adapters are injected by the caller.  This module neither creates a
client nor decides that a source is activated: it only executes the exact
manifest supplied by an authorized operation and keeps the result in UR-118's
atomic, display-only observation store.
"""

from __future__ import annotations

import os
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from enum import IntEnum, StrEnum
from pathlib import Path
from threading import Lock
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from stock_data.orchestration.automatic_fallback import (
    AttemptFailure,
    ExecutionKind,
    FailureKind,
    SourceObservation,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    CurrentObservationRefreshResult,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationInterval,
)


KST = ZoneInfo("Asia/Seoul")
TIMEOUT_SECONDS = 10
RETRY_COUNT = 0


class CurrentObservationSupervisorError(ValueError):
    """The activation manifest is not safe to execute."""


class AcquisitionProvider(StrEnum):
    KB = "KB"
    LS = "LS"
    TOSS = "TOSS"
    FDR = "FDR"
    YFINANCE = "YFINANCE"


class BrokerPriority(IntEnum):
    TOSS = 10
    KB = 20
    LS = 30
    FDR = 40
    YFINANCE = 50


class SupervisorRouteOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    INACTIVE = "INACTIVE"
    WINDOW_CLOSED = "WINDOW_CLOSED"
    NOT_DUE = "NOT_DUE"
    NO_ADAPTER = "NO_ADAPTER"
    ORPHANED_IN_PROGRESS = "ORPHANED_IN_PROGRESS"
    DURABLE_STATE_ERROR = "DURABLE_STATE_ERROR"
    API_ZERO_REPLAY = "API_ZERO_REPLAY"


class SupervisorTickOutcome(StrEnum):
    DECIDED = "DECIDED"
    COALESCED = "COALESCED"
    PROCESS_LOCKED = "PROCESS_LOCKED"
    CLOSED = "CLOSED"


Attempt = Callable[[], SourceObservation[CurrentObservation]]


@dataclass(frozen=True)
class DueWindow:
    """A 30/60-minute due cadence with an optional KST provider window."""

    cadence: timedelta
    opens_kst: time | None = None
    closes_kst: time | None = None

    def __post_init__(self) -> None:
        if self.cadence not in {timedelta(minutes=30), timedelta(minutes=60)}:
            raise CurrentObservationSupervisorError("current-observation cadence must be 30m or 60m")
        if (self.opens_kst is None) != (self.closes_kst is None):
            raise CurrentObservationSupervisorError("provider window needs both KST bounds or neither")
        if self.opens_kst is not None and self.closes_kst is not None and self.opens_kst > self.closes_kst:
            raise CurrentObservationSupervisorError("overnight provider windows are not accepted")

    def is_open(self, now: datetime) -> bool:
        if now.tzinfo is None or now.utcoffset() is None:
            raise CurrentObservationSupervisorError("supervisor clock must be timezone-aware")
        if self.opens_kst is None:
            return True
        local = now.astimezone(KST).timetz().replace(tzinfo=None)
        assert self.closes_kst is not None
        return self.opens_kst <= local <= self.closes_kst

    def is_due(self, now: datetime, previous: datetime | None) -> bool:
        if not self.is_open(now):
            return False
        return previous is None or now - previous >= self.cadence


@dataclass(frozen=True)
class CurrentObservationActivation:
    """One exact route that an external operation may activate explicitly."""

    operation_id: str
    runbook: str
    provider: AcquisitionProvider
    route: CurrentObservationRoute
    interval: ObservationInterval
    due_window: DueWindow
    request_cap: int
    finality: ObservationFinality
    activated: bool = False
    fallback_route: bool = False
    timeout_seconds: int = TIMEOUT_SECONDS
    retry_count: int = RETRY_COUNT
    display_only: bool = True
    pit_safe: bool = False

    def __post_init__(self) -> None:
        if not self.operation_id or not self.runbook:
            raise CurrentObservationSupervisorError("activation needs operation and runbook")
        if self.interval not in self.route.interval_precedence:
            raise CurrentObservationSupervisorError("activation interval is absent from route precedence")
        if self.request_cap != 1:
            raise CurrentObservationSupervisorError("activated provider route request cap must be exactly one")
        if self.timeout_seconds != TIMEOUT_SECONDS or self.retry_count != RETRY_COUNT:
            raise CurrentObservationSupervisorError("current-observation timeout/retry policy is fixed")
        if not self.display_only or self.pit_safe:
            raise CurrentObservationSupervisorError("activation must remain display-only and PIT-blocked")
        if self.provider in {AcquisitionProvider.KB, AcquisitionProvider.LS, AcquisitionProvider.TOSS}:
            if self.fallback_route:
                raise CurrentObservationSupervisorError("broker route cannot be declared as a fallback")
        elif self.provider is AcquisitionProvider.FDR:
            if not self.fallback_route or self.interval is not ObservationInterval.DAILY:
                raise CurrentObservationSupervisorError("FDR is a separately activated daily fallback route only")
        elif self.provider is AcquisitionProvider.YFINANCE:
            if not self.fallback_route or self.activated:
                raise CurrentObservationSupervisorError("yfinance fallback remains disabled without accepted current evidence")

    @property
    def priority(self) -> BrokerPriority:
        return BrokerPriority[self.provider.value]


@dataclass(frozen=True)
class RouteAttempts:
    """Injected source attempts; neither callable is invoked for inactive routes."""

    primary: Attempt
    fallback: Attempt | None = None


@dataclass(frozen=True)
class SupervisorRouteResult:
    route_id: str
    outcome: SupervisorRouteOutcome
    observation: CurrentObservation | None
    refresh: CurrentObservationRefreshResult | None
    reason: str | None
    api_calls: int


@dataclass(frozen=True)
class SupervisorTickResult:
    outcome: SupervisorTickOutcome
    routes: tuple[SupervisorRouteResult, ...]
    api_calls: int


class CurrentObservationProcessLock:
    """Fail-closed, same-volume file lock for an independently started process."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._token: str | None = None

    def acquire(self) -> bool:
        if self._token is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        try:
            with self.path.open("x", encoding="utf-8") as handle:
                handle.write(token)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            return False
        self._token = token
        return True

    def release(self) -> None:
        token = self._token
        self._token = None
        if token is None:
            return
        try:
            if self.path.read_text(encoding="utf-8") == token:
                self.path.unlink()
        except FileNotFoundError:
            return


class AttemptClaimStatus(StrEnum):
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class DurableAttemptRecord:
    status: AttemptClaimStatus
    attempted_at_utc: str

    def timestamp(self) -> datetime:
        try:
            parsed = datetime.fromisoformat(self.attempted_at_utc)
        except ValueError as error:
            raise CurrentObservationSupervisorError("durable attempt timestamp is invalid") from error
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise CurrentObservationSupervisorError("durable attempt timestamp must be UTC")
        return parsed


class CurrentObservationAttemptStore:
    """Atomic per-route claim ledger persisted before any adapter invocation."""

    _SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def _read_state(self) -> dict[str, object]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema_version": self._SCHEMA_VERSION, "records": {}}
        except (OSError, json.JSONDecodeError) as error:
            raise CurrentObservationSupervisorError("durable attempt state is unreadable") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "records"}
            or payload["schema_version"] != self._SCHEMA_VERSION
            or not isinstance(payload["records"], dict)
        ):
            raise CurrentObservationSupervisorError("durable attempt state schema mismatch")
        for route_id, record in payload["records"].items():
            if not isinstance(route_id, str) or not isinstance(record, dict) or set(record) != set(DurableAttemptRecord.__dataclass_fields__):
                raise CurrentObservationSupervisorError("durable attempt record schema mismatch")
            DurableAttemptRecord(AttemptClaimStatus(record["status"]), str(record["attempted_at_utc"])).timestamp()
        return payload

    def _snapshot(self) -> bytes | None:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def _restore(self, snapshot: bytes | None) -> None:
        if snapshot is None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            return
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.rollback")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with temporary.open("xb") as handle:
                handle.write(snapshot)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _write_state(self, state: dict[str, object]) -> None:
        snapshot = self._snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if self._read_state() != state:
                raise CurrentObservationSupervisorError("durable attempt state readback mismatch")
        except Exception:
            self._restore(snapshot)
            raise
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def record(self, route_id: str) -> DurableAttemptRecord | None:
        with self._lock:
            raw = self._read_state()["records"].get(route_id)
            if raw is None:
                return None
            return DurableAttemptRecord(AttemptClaimStatus(raw["status"]), str(raw["attempted_at_utc"]))

    def claim(self, route_id: str, now: datetime) -> DurableAttemptRecord:
        if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
            raise CurrentObservationSupervisorError("durable claim timestamp must be UTC")
        record = DurableAttemptRecord(AttemptClaimStatus.CLAIMED, now.isoformat())
        with self._lock:
            state = self._read_state()
            records = dict(state["records"])
            records[route_id] = asdict(record)
            state["records"] = records
            self._write_state(state)
            raw = self._read_state()["records"].get(route_id)
            readback = None if raw is None else DurableAttemptRecord(
                AttemptClaimStatus(raw["status"]), str(raw["attempted_at_utc"])
            )
            if readback != record:
                raise CurrentObservationSupervisorError("durable attempt claim readback mismatch")
        return record

    def complete(self, route_id: str) -> DurableAttemptRecord:
        with self._lock:
            state = self._read_state()
            raw = state["records"].get(route_id)
            if raw is None or AttemptClaimStatus(raw["status"]) is not AttemptClaimStatus.CLAIMED:
                raise CurrentObservationSupervisorError("durable attempt completion lacks a matching claim")
            record = DurableAttemptRecord(AttemptClaimStatus.COMPLETED, str(raw["attempted_at_utc"]))
            records = dict(state["records"])
            records[route_id] = asdict(record)
            state["records"] = records
            self._write_state(state)
            raw = self._read_state()["records"].get(route_id)
            readback = None if raw is None else DurableAttemptRecord(
                AttemptClaimStatus(raw["status"]), str(raw["attempted_at_utc"])
            )
            if readback != record:
                raise CurrentObservationSupervisorError("durable attempt completion readback mismatch")
        return record


class CurrentObservationAcquisitionSupervisor:
    """Execute an explicit manifest with broker order, due gates, and replay."""

    def __init__(
        self,
        *,
        store: CurrentObservationFileStore,
        activations: tuple[CurrentObservationActivation, ...],
        attempts: Mapping[str, RouteAttempts],
        process_lock: CurrentObservationProcessLock,
        clock: Callable[[], datetime],
        attempt_store: CurrentObservationAttemptStore | None = None,
    ) -> None:
        route_ids = [activation.route.route_id for activation in activations]
        if len(route_ids) != len(set(route_ids)):
            raise CurrentObservationSupervisorError("activation manifest has duplicate route IDs")
        unknown = set(attempts) - set(route_ids)
        if unknown:
            raise CurrentObservationSupervisorError("attempts include a route absent from the activation manifest")
        self._store = store
        self._coordinator = CurrentObservationCoordinator(store)
        self._activations = {activation.route.route_id: activation for activation in activations}
        self._attempts = dict(attempts)
        self._process_lock = process_lock
        self._attempt_store = attempt_store or CurrentObservationAttemptStore(
            store.path.with_name(f"{store.path.stem}.attempts.json")
        )
        self._clock = clock
        self._state_lock = Lock()
        self._tick_active = False
        self._closed = False

    def close(self) -> None:
        with self._state_lock:
            self._closed = True

    def replay(self, route_ids: tuple[str, ...] | None = None) -> SupervisorTickResult:
        """Read retained observations without an adapter, process lock, or write."""
        selected = self._select(route_ids)
        results = tuple(
            SupervisorRouteResult(
                route_id=activation.route.route_id,
                outcome=SupervisorRouteOutcome.API_ZERO_REPLAY,
                observation=(refresh := self._coordinator.replay(activation.route)).observation,
                refresh=refresh,
                reason=None,
                api_calls=0,
            )
            for activation in selected
        )
        return SupervisorTickResult(SupervisorTickOutcome.DECIDED, results, 0)

    def tick(self, route_ids: tuple[str, ...] | None = None) -> SupervisorTickResult:
        with self._state_lock:
            if self._closed:
                return SupervisorTickResult(SupervisorTickOutcome.CLOSED, (), 0)
            if self._tick_active:
                return SupervisorTickResult(SupervisorTickOutcome.COALESCED, (), 0)
            self._tick_active = True
        try:
            if not self._process_lock.acquire():
                return SupervisorTickResult(SupervisorTickOutcome.PROCESS_LOCKED, (), 0)
            try:
                now = self._clock()
                if now.tzinfo is None or now.utcoffset() is None:
                    raise CurrentObservationSupervisorError("supervisor clock must be timezone-aware")
                results = tuple(self._tick_route(activation, now) for activation in self._select(route_ids))
                return SupervisorTickResult(
                    SupervisorTickOutcome.DECIDED,
                    results,
                    sum(result.api_calls for result in results),
                )
            finally:
                self._process_lock.release()
        finally:
            with self._state_lock:
                self._tick_active = False

    def _select(self, route_ids: tuple[str, ...] | None) -> tuple[CurrentObservationActivation, ...]:
        if route_ids is None:
            selected = tuple(self._activations.values())
        else:
            try:
                selected = tuple(self._activations[route_id] for route_id in route_ids)
            except KeyError as error:
                raise CurrentObservationSupervisorError("unknown activation route") from error
        return tuple(sorted(selected, key=lambda activation: (activation.priority, activation.route.route_id)))

    def _tick_route(self, activation: CurrentObservationActivation, now: datetime) -> SupervisorRouteResult:
        route = activation.route
        if not activation.activated:
            return SupervisorRouteResult(route.route_id, SupervisorRouteOutcome.INACTIVE, None, None, "INACTIVE_OR_UNAPPROVED", 0)
        if not activation.due_window.is_open(now):
            return SupervisorRouteResult(route.route_id, SupervisorRouteOutcome.WINDOW_CLOSED, None, None, "PROVIDER_WINDOW_CLOSED", 0)
        try:
            durable = self._attempt_store.record(route.route_id)
        except (CurrentObservationSupervisorError, OSError):
            return SupervisorRouteResult(route.route_id, SupervisorRouteOutcome.DURABLE_STATE_ERROR, None, None, "DURABLE_STATE_UNREADABLE", 0)
        if durable is not None and durable.status is AttemptClaimStatus.CLAIMED:
            return SupervisorRouteResult(route.route_id, SupervisorRouteOutcome.ORPHANED_IN_PROGRESS, None, None, "ORPHANED_DURABLE_CLAIM_NO_REPEAT", 0)
        previous = durable.timestamp() if durable is not None else None
        if not activation.due_window.is_due(now, previous):
            return SupervisorRouteResult(route.route_id, SupervisorRouteOutcome.NOT_DUE, None, None, "CADENCE_NOT_DUE", 0)
        supplied = self._attempts.get(route.route_id)
        if supplied is None:
            return SupervisorRouteResult(route.route_id, SupervisorRouteOutcome.NO_ADAPTER, None, None, "NO_INJECTED_ADAPTER", 0)

        try:
            self._attempt_store.claim(route.route_id, now.astimezone(timezone.utc))
        except (CurrentObservationSupervisorError, OSError):
            return SupervisorRouteResult(route.route_id, SupervisorRouteOutcome.DURABLE_STATE_ERROR, None, None, "DURABLE_CLAIM_WRITE_OR_READBACK_FAILED", 0)
        primary = self._checked_attempt(activation, supplied.primary)
        fallback = self._checked_attempt(activation, supplied.fallback) if supplied.fallback else self._numeric_free_fallback
        try:
            refresh = self._coordinator.refresh(
                route,
                primary_attempt=primary,
                fallback_attempt=fallback,
                execution_kind=ExecutionKind.NORMAL_SCHEDULE,
            )
        except Exception:
            # The durable claim intentionally remains in progress. A restart
            # therefore fails closed rather than repeating an uncertain attempt.
            return SupervisorRouteResult(route.route_id, SupervisorRouteOutcome.ORPHANED_IN_PROGRESS, None, None, "ATTEMPT_INTERRUPTED_AFTER_DURABLE_CLAIM", 0)
        try:
            self._attempt_store.complete(route.route_id)
        except (CurrentObservationSupervisorError, OSError):
            return SupervisorRouteResult(route.route_id, SupervisorRouteOutcome.DURABLE_STATE_ERROR, refresh.observation, refresh, "DURABLE_COMPLETION_WRITE_OR_READBACK_FAILED", refresh.api_calls)
        return SupervisorRouteResult(
            route.route_id, SupervisorRouteOutcome.EXECUTED,
            refresh.observation, refresh, None, refresh.api_calls,
        )

    @staticmethod
    def _numeric_free_fallback() -> SourceObservation[CurrentObservation]:
        raise AttemptFailure(FailureKind.SCHEMA_ERROR, safe_code="NO_SEPARATE_FALLBACK_ADAPTER", request_count=0)

    @staticmethod
    def _checked_attempt(activation: CurrentObservationActivation, attempt: Attempt) -> Attempt:
        def checked() -> SourceObservation[CurrentObservation]:
            source = attempt()
            observation = source.value
            if source.provenance.request_count > activation.request_cap:
                raise AttemptFailure(FailureKind.SCHEMA_ERROR, safe_code="REQUEST_CAP_EXCEEDED", request_count=0)
            if observation.finality is not activation.finality:
                raise AttemptFailure(FailureKind.SCHEMA_ERROR, safe_code="FINALITY_MISMATCH", request_count=0)
            if observation.display_only is not activation.display_only or observation.pit_safe is not activation.pit_safe:
                raise AttemptFailure(FailureKind.SCHEMA_ERROR, safe_code="DISPLAY_PIT_MISMATCH", request_count=0)
            return source
        return checked


__all__ = [
    "AcquisitionProvider", "AttemptClaimStatus", "BrokerPriority", "CurrentObservationAcquisitionSupervisor",
    "CurrentObservationActivation", "CurrentObservationProcessLock",
    "CurrentObservationAttemptStore", "CurrentObservationSupervisorError", "DurableAttemptRecord", "DueWindow", "RouteAttempts",
    "SupervisorRouteOutcome", "SupervisorRouteResult", "SupervisorTickOutcome",
    "SupervisorTickResult", "TIMEOUT_SECONDS", "RETRY_COUNT",
]
