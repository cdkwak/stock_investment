from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.providers.naver_current_web_observation import (
    NaverCurrentWebObservationError,
    naver_web_current_quote,
    naver_web_current_route,
)


RETRIEVED = datetime(2026, 8, 21, 4, 26, 45, tzinfo=timezone.utc)


def _payload() -> dict:
    return {
        "itemCode": "000660", "closePrice": "1,734,000", "marketStatus": "OPEN",
        "localTradedAt": "2026-08-21T13:26:15+09:00", "delayTime": 0,
        "stockExchangeType": {
            "code": "KS", "zoneId": "Asia/Seoul", "nationType": "KOR",
            "stockType": "domestic", "delayTime": 0, "startTime": "0900", "endTime": "1530",
        },
    }


def test_exact_domestic_contract_maps_missing_top_level_currency_to_krw_per_share() -> None:
    source = naver_web_current_quote(_payload(), retrieved_at=RETRIEVED)

    assert source.value.unit == "KRW per share"
    assert source.value.value == 1_734_000.0
    assert source.value.provider_timestamp_utc == "2026-08-21T04:26:15+00:00"
    assert source.value.display_only and not source.value.pit_safe


@pytest.mark.parametrize("field, value", [
    ("code", "KQ"), ("zoneId", "UTC"), ("nationType", "USA"),
    ("stockType", "overseas"), ("delayTime", 20), ("startTime", "0800"),
])
def test_any_nonexact_exchange_contract_is_numeric_free(field: str, value: object) -> None:
    payload = _payload()
    payload["stockExchangeType"][field] = value
    with pytest.raises(NaverCurrentWebObservationError, match="exact Korean domestic KRW contract"):
        naver_web_current_quote(payload, retrieved_at=RETRIEVED)


def test_closed_or_stale_payload_is_numeric_free() -> None:
    closed = _payload()
    closed["marketStatus"] = "CLOSE"
    with pytest.raises(NaverCurrentWebObservationError, match="marketStatus"):
        naver_web_current_quote(closed, retrieved_at=RETRIEVED)

    stale = _payload()
    stale["localTradedAt"] = "2026-08-21T12:00:00+09:00"
    with pytest.raises(NaverCurrentWebObservationError, match="60-minute"):
        naver_web_current_quote(stale, retrieved_at=RETRIEVED)


def test_atomic_projection_and_api_zero_replay(tmp_path) -> None:
    route = naver_web_current_route()
    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(tmp_path / "current.json"))
    result = coordinator.refresh(
        route,
        primary_attempt=lambda: naver_web_current_quote(_payload(), retrieved_at=RETRIEVED),
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("fallback disabled")),
    )

    assert result.api_calls == 1
    assert result.observation is not None
    replay = coordinator.replay(route)
    assert replay.api_calls == 0
    assert replay.observation == result.observation
