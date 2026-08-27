from __future__ import annotations

from datetime import timedelta

from stock_data.orchestration.current_observation import CurrentObservationFileStore, ObservationIdentity
from stock_data.providers.yfinance_websocket_observation import (
    YFWebSocketActivationManifest, YFWebSocketAdapterStatus, YFWebSocketInjectedMessage,
    YFWebSocketNumericAvailability, YFWebSocketObservationAdapter, YFWebSocketObservationError,
    YFWebSocketTimeUnit,
)


MANIFEST = YFWebSocketActivationManifest(
    identity=ObservationIdentity("DASHBOARD_CURRENT", "XUS", "TEST"), exchange="NMS",
    exchange_timezone="America/New_York", currency="USD", unit="USD", event_time_unit=YFWebSocketTimeUnit.SECONDS,
)
RETRIEVED = "2026-08-21T01:01:00+00:00"


def _adapter(tmp_path, *, throttle: timedelta = timedelta(seconds=1)):
    return YFWebSocketObservationAdapter(
        manifest=MANIFEST, store=CurrentObservationFileStore(tmp_path / "current.json"), throttle=throttle,
    )


def _message(sequence: int, *, price: str = "6500.25", timestamp: str = "1787274000", symbol: str = "TEST"):
    return YFWebSocketInjectedMessage(sequence, {
        "id": symbol, "price": price, "time": timestamp, "currency": "USD", "exchange": "NMS",
    })


def test_injected_pricingdata_promotes_atomically_but_never_claims_numeric_availability(tmp_path):
    adapter = _adapter(tmp_path)

    accepted = adapter.ingest(_message(1), retrieved_at_utc=RETRIEVED)
    replay = adapter.replay()

    assert accepted.status is YFWebSocketAdapterStatus.ACCEPTED
    assert accepted.provider_calls == 0
    assert accepted.observation is not None
    assert accepted.observation.value == 6500.25
    assert accepted.observation.finality.value == "PROVISIONAL"
    assert accepted.observation.display_only and not accepted.observation.pit_safe
    assert accepted.numeric_availability is YFWebSocketNumericAvailability.UNAVAILABLE_UNTIL_ACCEPTED_LIVE_PILOT
    assert replay.status is YFWebSocketAdapterStatus.API_ZERO_REPLAY
    assert replay.provider_calls == 0 and replay.observation == accepted.observation


def test_malformed_empty_disconnect_and_rate_limit_preserve_prior_valid(tmp_path):
    adapter = _adapter(tmp_path)
    first = adapter.ingest(_message(1), retrieved_at_utc=RETRIEVED)

    malformed = adapter.ingest(_message(2, price="nan"), retrieved_at_utc=RETRIEVED)
    empty = adapter.ingest(None, retrieved_at_utc=RETRIEVED)
    disconnected = adapter.disconnected()
    rate_limited = adapter.rate_limited()

    assert first.observation is not None
    assert [item.status for item in (malformed, empty, disconnected, rate_limited)] == [
        YFWebSocketAdapterStatus.MALFORMED, YFWebSocketAdapterStatus.EMPTY,
        YFWebSocketAdapterStatus.DISCONNECTED, YFWebSocketAdapterStatus.RATE_LIMITED,
    ]
    assert all(item.observation == first.observation and item.provider_calls == 0
               for item in (malformed, empty, disconnected, rate_limited))


def test_sequence_duplicate_out_of_order_and_throttle_never_overwrite_prior(tmp_path):
    adapter = _adapter(tmp_path, throttle=timedelta(seconds=10))
    first = adapter.ingest(_message(2), retrieved_at_utc=RETRIEVED)
    duplicate = adapter.ingest(_message(2, price="6501"), retrieved_at_utc=RETRIEVED)
    older = adapter.ingest(_message(1), retrieved_at_utc=RETRIEVED)
    throttled = adapter.ingest(_message(3, timestamp="1787274001"), retrieved_at_utc="2026-08-21T01:01:01+00:00")

    assert [item.status for item in (duplicate, older, throttled)] == [
        YFWebSocketAdapterStatus.DUPLICATE, YFWebSocketAdapterStatus.OUT_OF_ORDER,
        YFWebSocketAdapterStatus.THROTTLED,
    ]
    assert first.observation is not None
    assert all(item.observation == first.observation for item in (duplicate, older, throttled))


def test_manifest_identity_exchange_currency_and_event_order_are_exact(tmp_path):
    adapter = _adapter(tmp_path, throttle=timedelta())
    first = adapter.ingest(_message(2), retrieved_at_utc=RETRIEVED)
    wrong_symbol = adapter.ingest(_message(3, symbol="OTHER"), retrieved_at_utc=RETRIEVED)
    earlier_event = adapter.ingest(_message(4, timestamp="1787273999"), retrieved_at_utc=RETRIEVED)

    assert first.observation is not None
    assert wrong_symbol.status is YFWebSocketAdapterStatus.MALFORMED
    assert earlier_event.status is YFWebSocketAdapterStatus.OUT_OF_ORDER
    assert wrong_symbol.observation == earlier_event.observation == first.observation


def test_manifest_explicitly_converts_seconds_and_milliseconds_without_magnitude_inference(tmp_path):
    seconds = _adapter(tmp_path / "seconds")
    milliseconds_manifest = YFWebSocketActivationManifest(
        identity=MANIFEST.identity, exchange="NMS", exchange_timezone="America/New_York",
        currency="USD", unit="USD", event_time_unit=YFWebSocketTimeUnit.MILLISECONDS,
    )
    milliseconds = YFWebSocketObservationAdapter(
        manifest=milliseconds_manifest, store=CurrentObservationFileStore(tmp_path / "milliseconds.json"),
    )

    from_seconds = seconds.ingest(_message(1, timestamp="1787274000"), retrieved_at_utc=RETRIEVED)
    from_milliseconds = milliseconds.ingest(_message(1, timestamp="1787274000000"), retrieved_at_utc=RETRIEVED)

    assert from_seconds.observation is not None and from_milliseconds.observation is not None
    assert from_seconds.observation.provider_timestamp_utc == from_milliseconds.observation.provider_timestamp_utc


def test_manifest_rejects_absent_or_unsupported_event_time_unit():
    kwargs = dict(identity=MANIFEST.identity, exchange="NMS", exchange_timezone="America/New_York", currency="USD", unit="USD")
    try:
        YFWebSocketActivationManifest(**kwargs)  # type: ignore[call-arg]
    except TypeError:
        pass
    else:
        raise AssertionError("event time unit must be supplied")
    try:
        YFWebSocketActivationManifest(**kwargs, event_time_unit="MICROSECONDS")  # type: ignore[arg-type]
    except YFWebSocketObservationError:
        pass
    else:
        raise AssertionError("unsupported event time unit must be rejected")
