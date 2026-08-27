from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)
from stock_data.orchestration.kbsec_intraday_current_foundation import (
    KBSecIntradayContract,
    KBSecIntradayTransportResult,
    KBSecIntradayWindowedCollector,
)


NOW = datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)


def _route() -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id="kbsec-intraday:synthetic:XKRX:TEST",
            primary_provider="KB_SECURITIES",
            primary_route="KBSEC:SYNTHETIC",
            fallback_provider="LOCAL_CIRCUIT",
            fallback_upstream_provider="LOCAL_CIRCUIT",
            fallback_route="LOCAL_CIRCUIT",
            fallback_enabled=False,
        ),
        identity=ObservationIdentity("KB_INTRADAY_TEST", "XKRX", "TEST"),
        interval_precedence=(ObservationInterval.MINUTES_30,),
    )


def _contract(parser, *, windows: tuple[str, ...] = ("2026-08-21T18:00:00+09:00",)) -> KBSecIntradayContract:
    return KBSecIntradayContract(
        operation_id="UR-237-SYNTHETIC",
        route=_route(),
        interval=ObservationInterval.MINUTES_30,
        finality=ObservationFinality.PROVISIONAL,
        state_path=Path("data/state/kbsec_intraday_synthetic.json"),
        landing_root=Path("data/landing/kbsec/intraday_synthetic"),
        projection_path=Path("data/state/current_observations/kbsec_intraday_synthetic.json"),
        allowed_window_ids=windows,
        parser=parser,
    )


def _source(payload, now: datetime, *, value: float = 123.45) -> SourceObservation[CurrentObservation]:
    assert payload == {"dataHeader": {}, "dataBody": {"synthetic": True}}
    route = _route()
    observation = CurrentObservation(
        route_id=route.route_id,
        identity=route.identity,
        interval=ObservationInterval.MINUTES_30,
        value=value,
        unit="synthetic points",
        provider="KB_SECURITIES",
        upstream_provider="KB_SECURITIES_OPEN_API",
        source_route="KBSEC:SYNTHETIC",
        provider_timestamp_utc=(now - timedelta(minutes=5)).isoformat(),
        retrieved_at_utc=now.isoformat(),
        finality=ObservationFinality.PROVISIONAL,
    )
    return SourceObservation(observation, SourceProvenance(
        provider=observation.provider,
        upstream_provider=observation.upstream_provider,
        source_route=observation.source_route,
        retrieved_at_utc=observation.retrieved_at_utc,
        request_count=1,
    ))


def _payload() -> KBSecIntradayTransportResult:
    return KBSecIntradayTransportResult({"dataHeader": {}, "dataBody": {"synthetic": True}})


def test_schema_injected_runner_claims_landing_before_exact_projection_and_replays_api_zero(tmp_path: Path) -> None:
    contract = _contract(_source)
    runner = KBSecIntradayWindowedCollector(tmp_path, contract)
    window = contract.allowed_window_ids[0]

    result = runner.run(window_id=window, now=NOW, transport_factory=_payload)

    assert result.status == "COMPLETE_ACCEPTED"
    assert result.business_requests == 1
    assert result.landing_sha256 is not None
    assert result.observation is not None and result.observation.value == 123.45
    state = json.loads((tmp_path / contract.state_path).read_text(encoding="utf-8"))
    record = state["windows"][window]
    landing = tmp_path / record["landing_file"]
    assert record["status"] == "COMPLETE_ACCEPTED"
    assert record["landing_sha256"] == result.landing_sha256
    assert landing.is_file()
    assert runner.run(window_id=window, now=NOW, transport_factory=lambda: (_ for _ in ()).throw(AssertionError("no repeat"))).status == "NO_REPEAT"
    replay = runner.replay()
    assert replay.status == "API_ZERO_REPLAY" and replay.business_requests == replay.replay_api_calls == 0
    assert replay.observation == result.observation


def test_bad_schema_after_landing_preserves_prior_and_never_promotes_wrong_route(tmp_path: Path) -> None:
    first_window, second_window = "2026-08-21T18:00:00+09:00", "2026-08-21T18:30:00+09:00"
    contract = _contract(_source, windows=(first_window, second_window))
    runner = KBSecIntradayWindowedCollector(tmp_path, contract)
    accepted = runner.run(window_id=first_window, now=NOW, transport_factory=_payload)
    assert accepted.observation is not None

    def wrong_route(payload, now):
        source = _source(payload, now, value=999.0)
        changed = CurrentObservation(
            route_id="kbsec-intraday:wrong:XKRX:TEST",
            identity=source.value.identity,
            interval=source.value.interval,
            value=source.value.value,
            unit=source.value.unit,
            provider=source.value.provider,
            upstream_provider=source.value.upstream_provider,
            source_route=source.value.source_route,
            provider_timestamp_utc=source.value.provider_timestamp_utc,
            retrieved_at_utc=source.value.retrieved_at_utc,
            finality=source.value.finality,
        )
        return SourceObservation(changed, source.provenance)

    failed = KBSecIntradayWindowedCollector(tmp_path, _contract(wrong_route, windows=(first_window, second_window))).run(
        window_id=second_window, now=NOW + timedelta(minutes=30), transport_factory=_payload,
    )

    assert failed.status == "COMPLETE_FAILURE" and failed.business_requests == 1
    assert failed.landing_sha256 is None
    assert failed.observation == accepted.observation
    assert not KBSecIntradayWindowedCollector(tmp_path, contract).is_active(window_id=second_window)


def test_unmanifested_malformed_or_orphaned_state_is_api_zero_and_transport_free(tmp_path: Path) -> None:
    contract = _contract(_source)
    runner = KBSecIntradayWindowedCollector(tmp_path, contract)
    called = False

    def forbidden():
        nonlocal called
        called = True
        raise AssertionError("transport must stay unused")

    assert runner.run(window_id="unknown", now=NOW, transport_factory=forbidden).status == "WINDOW_NOT_MANIFESTED"
    ledger = tmp_path / contract.state_path
    ledger.parent.mkdir(parents=True)
    ledger.write_text("not-json", encoding="utf-8")
    assert runner.run(window_id=contract.allowed_window_ids[0], now=NOW, transport_factory=forbidden).status == "LEDGER_INVALID"
    assert called is False

    ledger.write_text(json.dumps({
        "schema_version": 1,
        "operation_id": "UR-237-SYNTHETIC",
        "windows": {contract.allowed_window_ids[0]: {"status": "ATTEMPTING"}},
    }), encoding="utf-8")
    assert runner.run(window_id=contract.allowed_window_ids[0], now=NOW, transport_factory=forbidden).status == "ORPHAN_ATTEMPTING_NO_REPEAT"
    assert called is False


def test_same_window_is_coalesced_before_a_second_transport_invocation(tmp_path: Path) -> None:
    contract = _contract(_source)
    runner = KBSecIntradayWindowedCollector(tmp_path, contract)
    entered, release = Event(), Event()
    first_result = []

    def slow_transport() -> KBSecIntradayTransportResult:
        entered.set()
        assert release.wait(timeout=2)
        return _payload()

    thread = Thread(target=lambda: first_result.append(runner.run(
        window_id=contract.allowed_window_ids[0], now=NOW, transport_factory=slow_transport,
    )))
    thread.start()
    assert entered.wait(timeout=2)
    coalesced = runner.run(
        window_id=contract.allowed_window_ids[0], now=NOW, transport_factory=lambda: (_ for _ in ()).throw(AssertionError("duplicate")),
    )
    release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert coalesced.status == "COALESCED" and coalesced.business_requests == 0
    assert first_result[0].status == "COMPLETE_ACCEPTED"
