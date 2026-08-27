from __future__ import annotations

from pathlib import Path

import pytest

from stock_data.orchestration.current_observation import (
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    ObservationFinality,
    ObservationInterval,
    ObservationTimestampBasis,
)
from stock_data.orchestration.toss_market_current_observation import (
    TossCurrentObservationError,
    TossProviderBoundary,
    market_candle,
    market_investor_observation,
    market_price_snapshot,
)


RETRIEVED = "2026-08-21T10:00:00+00:00"


def _snapshot_payload(symbol: str = "KOSPI") -> dict:
    return {"result": [{
        "symbol": symbol,
        "timestamp": "2026-08-21T15:30:00+09:00",
        "lastPrice": "3,210.50",
    }]}


def _candle_payload() -> dict:
    return {"result": {"candles": [{
        "timestamp": "2026-08-21T15:00:00+09:00",
        "openPrice": "3,200.0",
        "highPrice": "3,220.0",
        "lowPrice": "3,190.0",
        "closePrice": "3,210.5",
    }]}}


def _investor_payload(market_date: str = "2026-08-21") -> dict:
    return {"result": {"records": [{
        "date": market_date,
        "updatedAt": "2026-08-21T18:10:00+09:00",
        "individual": {"buyAmount": "1,234", "sellAmount": "100"},
        "foreigner": {"buyAmount": "200", "sellAmount": "300"},
        "institution": {"buyAmount": "400", "sellAmount": "500"},
        "otherCorporation": {"buyAmount": "600", "sellAmount": "700"},
    }]}}


def test_price_snapshot_preserves_exact_market_identity_timestamp_unit_and_provisional_boundary() -> None:
    candidate = market_price_snapshot(_snapshot_payload(), market="KOSPI", retrieved_at_utc=RETRIEVED)

    observation = candidate.observation
    assert observation.identity.dataset_id == "TOSS_MARKET_PRICE_SNAPSHOT"
    assert observation.identity.market == "XKRX" and observation.identity.symbol == "KOSPI"
    assert observation.interval is ObservationInterval.SNAPSHOT
    assert observation.value == 3210.5 and observation.unit == "index points"
    assert observation.provider_timestamp_utc == "2026-08-21T06:30:00+00:00"
    assert candidate.market_date == "2026-08-21"
    assert candidate.provider_boundary is TossProviderBoundary.MARKET_INDICATOR
    assert candidate.is_provisional and observation.display_only and not observation.pit_safe
    assert observation.timestamp_basis is ObservationTimestampBasis.PROVIDER_TIMESTAMP


def test_price_snapshot_models_explicit_null_provider_timestamp_as_retrieval_time_only() -> None:
    payload = _snapshot_payload()
    payload["result"][0]["timestamp"] = None

    candidate = market_price_snapshot(
        payload, market="KOSPI", retrieved_at_utc=RETRIEVED,
    )

    observation = candidate.observation
    assert observation.provider_timestamp_utc == RETRIEVED
    assert observation.retrieved_at_utc == RETRIEVED
    assert observation.timestamp_basis is ObservationTimestampBasis.RETRIEVAL_TIMESTAMP
    assert candidate.market_date == "2026-08-21"
    assert observation.display_only and not observation.pit_safe


def test_price_snapshot_does_not_treat_empty_provider_timestamp_as_absent() -> None:
    payload = _snapshot_payload()
    payload["result"][0]["timestamp"] = ""

    with pytest.raises(TossCurrentObservationError, match="price timestamp is required"):
        market_price_snapshot(payload, market="KOSPI", retrieved_at_utc=RETRIEVED)


def test_candle_preserves_daily_interval_and_only_explicit_daily_status_can_be_final() -> None:
    daily = market_candle(
        _candle_payload(), market="KOSDAQ", interval=ObservationInterval.DAILY,
        retrieved_at_utc=RETRIEVED, finality=ObservationFinality.FINAL,
    )

    assert daily.observation.interval is ObservationInterval.DAILY
    assert daily.observation.finality is ObservationFinality.FINAL
    assert not daily.is_provisional


@pytest.mark.parametrize(
    "interval",
    [
        ObservationInterval.MINUTES_15,
        ObservationInterval.MINUTES_30,
        ObservationInterval.MINUTES_60,
    ],
)
def test_candle_rejects_intraday_intervals_without_retained_source_evidence(interval: ObservationInterval) -> None:
    with pytest.raises(TossCurrentObservationError, match="interval=1d only"):
        market_candle(
            _candle_payload(), market="KOSDAQ", interval=interval,
            retrieved_at_utc=RETRIEVED,
        )


def test_investor_observation_is_krx_only_provider_bound_with_exact_date_and_krw_unit() -> None:
    candidate = market_investor_observation(
        _investor_payload(), market="KOSDAQ", metric="individual_buy_amount",
        retrieved_at_utc=RETRIEVED,
    )

    observation = candidate.observation
    assert observation.identity.dataset_id == "TOSS_MARKET_INVESTOR_KRX_ONLY"
    assert observation.identity.symbol == "KOSDAQ"
    assert observation.interval is ObservationInterval.DAILY
    assert observation.value == 1234.0 and observation.unit == "KRW"
    assert candidate.market_date == "2026-08-21"
    assert candidate.provider_boundary is TossProviderBoundary.KRX_ONLY_PROVIDER_EOD
    assert observation.finality is ObservationFinality.AS_RETRIEVED and candidate.is_provisional
    assert "KRX_ONLY_PROVIDER_EOD" in observation.route_id
    assert observation.source_route.endswith("/KOSDAQ/investor-trading")


def test_adapter_rejects_mismatched_market_duplicates_missing_provider_timestamp_and_unsupported_metric() -> None:
    with pytest.raises(TossCurrentObservationError, match="exactly one requested market"):
        market_price_snapshot(_snapshot_payload("KOSDAQ"), market="KOSPI", retrieved_at_utc=RETRIEVED)
    with pytest.raises(TossCurrentObservationError, match="exactly one object"):
        market_candle({"result": {"candles": _candle_payload()["result"]["candles"] * 2}}, market="KOSPI", interval=ObservationInterval.DAILY, retrieved_at_utc=RETRIEVED)
    bad_investor = _investor_payload()
    del bad_investor["result"]["records"][0]["updatedAt"]
    with pytest.raises(TossCurrentObservationError, match="updatedAt"):
        market_investor_observation(bad_investor, market="KOSPI", metric="individual_buy_amount", retrieved_at_utc=RETRIEVED)
    with pytest.raises(TossCurrentObservationError, match="unsupported"):
        market_investor_observation(_investor_payload(), market="KOSPI", metric="combined_market_amount", retrieved_at_utc=RETRIEVED)


def test_candidate_composes_with_atomic_current_observation_projection_and_no_fallback(tmp_path: Path) -> None:
    candidate = market_investor_observation(
        _investor_payload(), market="KOSPI", metric="foreigner_sell_amount", retrieved_at_utc=RETRIEVED,
    )
    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(tmp_path / "toss-current.json"))
    result = coordinator.refresh(
        candidate.route(), primary_attempt=lambda: candidate.source,
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("Toss route has no fallback")),
    )

    assert result.decision is not None and result.decision.fallback_attempts == 0
    assert result.observation == candidate.observation
    assert coordinator.replay(candidate.route()).observation == candidate.observation
