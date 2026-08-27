from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

import pytest

from stock_data.orchestration.automatic_fallback import (
    AttemptFailure,
    FailureKind,
    RoutePolicy,
    SourceObservation,
    SourceProvenance,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationFileStore,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)
from stock_data.orchestration.current_observation_supervisor import (
    AcquisitionProvider,
    AttemptClaimStatus,
    CurrentObservationAcquisitionSupervisor,
    CurrentObservationActivation,
    CurrentObservationAttemptStore,
    CurrentObservationProcessLock,
    CurrentObservationSupervisorError,
    DueWindow,
    RouteAttempts,
    SupervisorRouteOutcome,
    SupervisorTickOutcome,
)


NOW = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)


def _route(name: str, *, interval: ObservationInterval = ObservationInterval.MINUTES_30) -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=f"current:{name}", primary_provider=f"{name}-provider",
            primary_route=f"{name}-route", fallback_provider="LOCAL_CIRCUIT",
            fallback_upstream_provider="LOCAL_CIRCUIT", fallback_route="LOCAL_CIRCUIT",
            fallback_enabled=True, max_primary_requests=1, max_fallback_requests=1,
        ),
        identity=ObservationIdentity("CURRENT_TEST", "XKRX", name),
        interval_precedence=(interval,),
    )


def _activation(
    name: str,
    provider: AcquisitionProvider,
    *,
    interval: ObservationInterval = ObservationInterval.MINUTES_30,
    cadence: timedelta = timedelta(minutes=30),
    activated: bool = True,
    fallback_route: bool = False,
    window: DueWindow | None = None,
) -> CurrentObservationActivation:
    return CurrentObservationActivation(
        operation_id=f"UR131_{name}", runbook="CURRENT_OBSERVATION_ACQUISITION_SUPERVISOR.md",
        provider=provider, route=_route(name, interval=interval), interval=interval,
        due_window=window or DueWindow(cadence), request_cap=1,
        finality=ObservationFinality.AS_RETRIEVED, activated=activated,
        fallback_route=fallback_route,
    )


def _source(activation: CurrentObservationActivation, value: float = 100.0) -> SourceObservation[CurrentObservation]:
    route = activation.route
    observation = CurrentObservation(
        route_id=route.route_id, identity=route.identity, interval=activation.interval,
        value=value, unit="index points", provider=route.fallback_policy.primary_provider,
        upstream_provider="provider-upstream", source_route=route.fallback_policy.primary_route,
        provider_timestamp_utc="2026-08-21T07:30:00+00:00",
        retrieved_at_utc="2026-08-21T08:00:00+00:00",
        finality=activation.finality,
    )
    return SourceObservation(observation, SourceProvenance(
        provider=observation.provider, upstream_provider=observation.upstream_provider,
        source_route=observation.source_route, retrieved_at_utc=observation.retrieved_at_utc,
        request_count=1,
    ))


def _supervisor(
    tmp_path: Path,
    activations: tuple[CurrentObservationActivation, ...],
    attempts: dict[str, RouteAttempts],
    clock=lambda: NOW,
    *,
    store: CurrentObservationFileStore | None = None,
    lock: CurrentObservationProcessLock | None = None,
    attempt_store: CurrentObservationAttemptStore | None = None,
) -> CurrentObservationAcquisitionSupervisor:
    return CurrentObservationAcquisitionSupervisor(
        store=store or CurrentObservationFileStore(tmp_path / "observations.json"),
        activations=activations, attempts=attempts,
        process_lock=lock or CurrentObservationProcessLock(tmp_path / "supervisor.lock"),
        clock=clock,
        attempt_store=attempt_store,
    )


def test_manifest_runs_brokers_in_toss_kb_ls_priority_order(tmp_path: Path) -> None:
    activations = tuple(_activation(name, provider) for name, provider in (
        ("TOSS", AcquisitionProvider.TOSS), ("KB", AcquisitionProvider.KB), ("LS", AcquisitionProvider.LS),
    ))
    called: list[str] = []
    attempts = {
        activation.route.route_id: RouteAttempts(
            primary=lambda activation=activation: (called.append(activation.provider.value), _source(activation))[1]
        )
        for activation in activations
    }

    result = _supervisor(tmp_path, activations, attempts).tick()

    assert result.outcome is SupervisorTickOutcome.DECIDED
    assert called == ["TOSS", "KB", "LS"]
    assert [item.route_id for item in result.routes] == [
        "current:TOSS", "current:KB", "current:LS",
    ]
    assert result.api_calls == 3


def test_30m_and_60m_due_logic_never_invokes_not_due_routes(tmp_path: Path) -> None:
    clock = [NOW]
    thirty = _activation("KB30", AcquisitionProvider.KB)
    sixty = _activation("LS60", AcquisitionProvider.LS, interval=ObservationInterval.MINUTES_60, cadence=timedelta(minutes=60))
    calls: list[str] = []
    attempts = {
        activation.route.route_id: RouteAttempts(primary=lambda activation=activation: (calls.append(activation.route.route_id), _source(activation))[1])
        for activation in (thirty, sixty)
    }
    supervisor = _supervisor(tmp_path, (thirty, sixty), attempts, clock=lambda: clock[0])

    assert all(item.outcome is SupervisorRouteOutcome.EXECUTED for item in supervisor.tick().routes)
    clock[0] += timedelta(minutes=30)
    second = supervisor.tick()
    assert [item.outcome for item in second.routes] == [SupervisorRouteOutcome.EXECUTED, SupervisorRouteOutcome.NOT_DUE]
    clock[0] += timedelta(minutes=30)
    assert all(item.outcome is SupervisorRouteOutcome.EXECUTED for item in supervisor.tick().routes)
    assert calls == ["current:KB30", "current:LS60", "current:KB30", "current:KB30", "current:LS60"]


def test_inactive_and_closed_window_routes_are_numeric_free_without_adapter_call(tmp_path: Path) -> None:
    inactive = _activation("YF", AcquisitionProvider.YFINANCE, interval=ObservationInterval.DAILY, activated=False, fallback_route=True)
    closed_window = DueWindow(timedelta(minutes=30), opens_kst=time(18, 0), closes_kst=time(18, 30))
    kb = _activation("KB", AcquisitionProvider.KB, window=closed_window)
    called = False

    def forbidden():
        nonlocal called
        called = True
        raise AssertionError("inactive/window-closed route must not invoke adapter")

    result = _supervisor(tmp_path, (inactive, kb), {
        inactive.route.route_id: RouteAttempts(forbidden), kb.route.route_id: RouteAttempts(forbidden),
    }).tick()

    assert [item.outcome for item in result.routes] == [SupervisorRouteOutcome.WINDOW_CLOSED, SupervisorRouteOutcome.INACTIVE]
    assert all(item.observation is None and item.api_calls == 0 for item in result.routes)
    assert called is False


def test_typed_failure_opens_route_circuit_and_preserves_prior_valid_observation(tmp_path: Path) -> None:
    activation = _activation("KB", AcquisitionProvider.KB)
    store = CurrentObservationFileStore(tmp_path / "observations.json")
    successful = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(lambda: _source(activation, 100.0)),
    }, store=store)
    initial = successful.tick().routes[0].observation
    assert initial is not None

    def failure():
        raise AttemptFailure(FailureKind.TIMEOUT, safe_code="SYNTHETIC_TIMEOUT", request_count=1)

    failed = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(failure),
    }, store=store, clock=lambda: NOW + timedelta(minutes=30))
    result = failed.tick().routes[0]

    assert result.outcome is SupervisorRouteOutcome.EXECUTED
    assert result.observation == initial
    assert result.refresh is not None and result.refresh.api_calls == 1
    assert store.load(activation.route.route_id).is_open


def test_atomic_write_failure_preserves_prior_valid_observation(tmp_path: Path) -> None:
    class FailOnceStore(CurrentObservationFileStore):
        fail_next_write = False

        def _write_state(self, state):
            if self.fail_next_write:
                self.fail_next_write = False
                raise OSError("synthetic atomic failure")
            super()._write_state(state)

    activation = _activation("KB", AcquisitionProvider.KB)
    store = FailOnceStore(tmp_path / "observations.json")
    first = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(lambda: _source(activation, 100.0)),
    }, store=store).tick().routes[0].observation
    assert first is not None
    store.fail_next_write = True
    result = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(lambda: _source(activation, 101.0)),
    }, store=store, clock=lambda: NOW + timedelta(minutes=30)).tick().routes[0]

    assert result.observation == first
    assert store.select(activation.route) == first


def test_tick_coalesces_and_replay_is_api_zero(tmp_path: Path) -> None:
    activation = _activation("KB", AcquisitionProvider.KB)
    entered, release = Event(), Event()
    calls = 0

    def primary():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return _source(activation)

    supervisor = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(primary),
    })
    thread = Thread(target=supervisor.tick)
    thread.start()
    assert entered.wait(timeout=2)
    assert supervisor.tick().outcome is SupervisorTickOutcome.COALESCED
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive() and calls == 1

    replay = supervisor.replay()
    assert replay.api_calls == 0
    assert replay.routes[0].outcome is SupervisorRouteOutcome.API_ZERO_REPLAY
    assert replay.routes[0].observation is not None


def test_process_lock_and_fdr_yfinance_manifest_gates_fail_closed(tmp_path: Path) -> None:
    fdr = _activation("FDR", AcquisitionProvider.FDR, interval=ObservationInterval.DAILY, fallback_route=True)
    with pytest.raises(CurrentObservationSupervisorError, match="yfinance"):
        _activation("YF", AcquisitionProvider.YFINANCE, interval=ObservationInterval.DAILY, activated=True, fallback_route=True)

    first_lock = CurrentObservationProcessLock(tmp_path / "shared.lock")
    assert first_lock.acquire()
    blocked = _supervisor(tmp_path, (fdr,), {
        fdr.route.route_id: RouteAttempts(lambda: (_ for _ in ()).throw(AssertionError("locked process must not call"))),
    }, lock=CurrentObservationProcessLock(tmp_path / "shared.lock")).tick()
    first_lock.release()

    assert blocked.outcome is SupervisorTickOutcome.PROCESS_LOCKED
    assert blocked.api_calls == 0


def test_restart_uses_durable_success_and_failure_attempt_timestamps(tmp_path: Path) -> None:
    activation = _activation("KB", AcquisitionProvider.KB)
    store = CurrentObservationFileStore(tmp_path / "observations.json")
    ledger = CurrentObservationAttemptStore(tmp_path / "attempts.json")
    calls = 0

    def success():
        nonlocal calls
        calls += 1
        return _source(activation)

    first = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(success),
    }, store=store, attempt_store=ledger).tick()
    assert first.routes[0].outcome is SupervisorRouteOutcome.EXECUTED
    assert ledger.record(activation.route.route_id).status is AttemptClaimStatus.COMPLETED

    success_restart = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(success),
    }, store=store, attempt_store=ledger, clock=lambda: NOW + timedelta(minutes=15)).tick()
    assert success_restart.routes[0].outcome is SupervisorRouteOutcome.NOT_DUE
    assert calls == 1

    def failure():
        nonlocal calls
        calls += 1
        raise AttemptFailure(FailureKind.TIMEOUT, safe_code="RESTART_TIMEOUT", request_count=1)

    failure_run = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(failure),
    }, store=store, attempt_store=ledger, clock=lambda: NOW + timedelta(minutes=30)).tick()
    assert failure_run.routes[0].outcome is SupervisorRouteOutcome.EXECUTED
    assert ledger.record(activation.route.route_id).status is AttemptClaimStatus.COMPLETED

    failure_restart = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(failure),
    }, store=store, attempt_store=ledger, clock=lambda: NOW + timedelta(minutes=45)).tick()
    assert failure_restart.routes[0].outcome is SupervisorRouteOutcome.NOT_DUE
    assert calls == 2


def test_orphaned_durable_claim_is_no_repeat_after_process_restart(tmp_path: Path) -> None:
    activation = _activation("KB", AcquisitionProvider.KB)
    ledger = CurrentObservationAttemptStore(tmp_path / "attempts.json")
    ledger.claim(activation.route.route_id, NOW)
    called = False

    def forbidden():
        nonlocal called
        called = True
        raise AssertionError("orphaned claim must not invoke adapter")

    result = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(forbidden),
    }, attempt_store=ledger, clock=lambda: NOW + timedelta(hours=2)).tick()

    assert result.routes[0].outcome is SupervisorRouteOutcome.ORPHANED_IN_PROGRESS
    assert result.routes[0].reason == "ORPHANED_DURABLE_CLAIM_NO_REPEAT"
    assert called is False


def test_durable_claim_write_failure_rolls_back_before_adapter_invocation(tmp_path: Path) -> None:
    class FailOnceAttemptStore(CurrentObservationAttemptStore):
        fail_next_write = False

        def _write_state(self, state):
            if self.fail_next_write:
                self.fail_next_write = False
                raise OSError("synthetic durable state write failure")
            super()._write_state(state)

    activation = _activation("KB", AcquisitionProvider.KB)
    ledger = FailOnceAttemptStore(tmp_path / "attempts.json")
    ledger.fail_next_write = True
    called = False

    def forbidden():
        nonlocal called
        called = True
        raise AssertionError("failed durable claim must not invoke adapter")

    result = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(forbidden),
    }, attempt_store=ledger).tick()

    assert result.routes[0].outcome is SupervisorRouteOutcome.DURABLE_STATE_ERROR
    assert result.routes[0].reason == "DURABLE_CLAIM_WRITE_OR_READBACK_FAILED"
    assert ledger.record(activation.route.route_id) is None
    assert called is False


def test_durable_claim_readback_mismatch_rolls_back_before_adapter_invocation(tmp_path: Path) -> None:
    class ReadbackMismatchStore(CurrentObservationAttemptStore):
        mismatch = False

        def _read_state(self):
            state = super()._read_state()
            if self.mismatch and state["records"]:
                return {"schema_version": 1, "records": {}}
            return state

    activation = _activation("KB", AcquisitionProvider.KB)
    ledger = ReadbackMismatchStore(tmp_path / "attempts.json")
    ledger.mismatch = True
    called = False

    def forbidden():
        nonlocal called
        called = True
        raise AssertionError("failed durable readback must not invoke adapter")

    result = _supervisor(tmp_path, (activation,), {
        activation.route.route_id: RouteAttempts(forbidden),
    }, attempt_store=ledger).tick()
    ledger.mismatch = False

    assert result.routes[0].outcome is SupervisorRouteOutcome.DURABLE_STATE_ERROR
    assert result.routes[0].reason == "DURABLE_CLAIM_WRITE_OR_READBACK_FAILED"
    assert ledger.record(activation.route.route_id) is None
    assert called is False
