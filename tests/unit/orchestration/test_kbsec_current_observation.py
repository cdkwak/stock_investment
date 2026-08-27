from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from stock_data.orchestration.automatic_fallback import RoutePolicy
from stock_data.orchestration.current_observation import (
    CurrentObservationCoordinator, CurrentObservationFileStore, CurrentObservationRoute,
    ObservationInterval, ObservationTimestampBasis,
)
from stock_data.orchestration.kbsec_current_observation import adapt_kb_snapshot_frames
from stock_data.providers.kbsec.client import KBSecResponse
from stock_data.providers.kbsec.market_summary import normalize_market_summary


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "kbsec_ivsa0070.json"


def _frames():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return normalize_market_summary(
        KBSecResponse("200", "0024", payload["dataBody"], payload),
        collected_at=datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc),
    )


def test_retained_unresolved_kb_slice_is_numeric_free_without_inventing_provider_timestamp():
    result = adapt_kb_snapshot_frames(
        _frames(), provider_timestamp_utc=None,
        retrieved_at_utc="2026-08-21T08:00:00+00:00", request_count=1,
    )
    assert result.observations == ()
    assert result.numeric_free
    assert "SLICE_DATE_UNRESOLVED" in {item.reason for item in result.numeric_free}
    assert all(
        item.reason in {"SLICE_DATE_UNRESOLVED", "SOURCE_VALUE_UNAVAILABLE"}
        for item in result.numeric_free
    )


def test_verified_slice_timestamp_maps_seven_contract_slices_and_preserves_unavailable_derivative_fields():
    frames = _frames()
    for frame in frames.values():
        frame["availability_status"] = "CURRENT_DAY_CLOSE"
        frame["market_date"] = "2026-08-21"
    result = adapt_kb_snapshot_frames(
        frames, provider_timestamp_utc="2026-08-21T07:59:00+00:00",
        retrieved_at_utc="2026-08-21T08:00:00+00:00", request_count=1,
    )
    datasets = {item.value.identity.dataset_id for item in result.observations}
    assert datasets == {
        "KB_MARKET_BREADTH_SNAPSHOT", "KB_PROGRAM_TRADING_SNAPSHOT",
        "KB_INVESTOR_FLOW_SNAPSHOT", "KB_MARKET_LIQUIDITY_SNAPSHOT",
        "KB_DERIVATIVES_SUMMARY_SNAPSHOT", "KB_DOMESTIC_INDEX_SNAPSHOT",
        "KB_GLOBAL_SYMBOL_SNAPSHOT",
    }
    assert all(item.value.interval is ObservationInterval.SNAPSHOT for item in result.observations)
    assert all(item.value.finality.value == "PROVISIONAL" for item in result.observations)
    unavailable = [item for item in result.numeric_free if item.dataset_id == "kb_investor_flow_snapshot"]
    assert unavailable and {item.reason for item in unavailable} == {"SOURCE_VALUE_UNAVAILABLE"}


def test_verified_kb_slices_use_explicit_retrieval_time_when_provider_time_is_absent():
    frames = _frames()
    for frame in frames.values():
        frame["availability_status"] = "CURRENT_DAY_CLOSE"
        frame["market_date"] = "2026-08-21"
    retrieved = "2026-08-21T08:00:00+00:00"

    result = adapt_kb_snapshot_frames(
        frames, provider_timestamp_utc=None,
        retrieved_at_utc=retrieved, request_count=1,
    )

    assert result.observations
    assert all(
        item.value.timestamp_basis is ObservationTimestampBasis.RETRIEVAL_TIMESTAMP
        for item in result.observations
    )
    assert all(
        item.value.provider_timestamp_utc == retrieved
        and item.value.retrieved_at_utc == retrieved
        for item in result.observations
    )


def test_one_kb_scalar_promotes_atomically_and_replays_api_zero(tmp_path: Path):
    frames = _frames()
    for frame in frames.values():
        frame["availability_status"] = "CURRENT_DAY_CLOSE"
        frame["market_date"] = "2026-08-21"
    adapted = adapt_kb_snapshot_frames(
        frames, provider_timestamp_utc="2026-08-21T07:59:00+00:00",
        retrieved_at_utc="2026-08-21T08:00:00+00:00", request_count=1,
    )
    source = next(item for item in adapted.observations if item.value.identity.dataset_id == "KB_DOMESTIC_INDEX_SNAPSHOT" and item.value.unit == "index-points")
    route = CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=source.value.route_id, primary_provider="KB_SECURITIES",
            primary_route="KBSEC:IVSA0070", fallback_provider="NO_FALLBACK",
            fallback_upstream_provider="NO_FALLBACK", fallback_route="NO_FALLBACK",
            fallback_enabled=False,
        ),
        identity=source.value.identity, interval_precedence=(ObservationInterval.SNAPSHOT,),
    )
    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(tmp_path / "kb-current.json"))
    first = coordinator.refresh(route, primary_attempt=lambda: source, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")))
    replay = coordinator.replay(route)
    assert first.api_calls == 1 and first.observation == source.value
    assert replay.api_calls == 0 and replay.observation == source.value
