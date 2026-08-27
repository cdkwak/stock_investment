from datetime import datetime, timezone

import pytest

from stock_data.providers.nasdaq_soxx_info_current_observation import (
    NasdaqSoxxInfoObservationError,
    nasdaq_soxx_info_quote,
)


RETRIEVED = datetime(2026, 8, 21, 8, 9, 35, tzinfo=timezone.utc)


def _payload() -> dict:
    return {
        "data": {
            "symbol": "SOXX", "assetClass": "ETF", "exchange": "NASDAQ-GM",
            "marketStatus": "Pre-Market",
            "primaryData": {
                "lastSalePrice": "$526.6332",
                "lastTradeTimestamp": "Aug 21, 2026 4:08 AM ET",
                "isRealTime": True,
                "currency": None,
            },
        },
    }


def test_exact_soxx_etf_dollar_marker_maps_to_usd_per_share() -> None:
    source = nasdaq_soxx_info_quote(_payload(), retrieved_at=RETRIEVED, request_count=1)
    assert source.value.identity.symbol == "SOXX"
    assert source.value.unit == "USD per share"
    assert source.value.value == 526.6332
    assert source.value.provider_timestamp_utc == "2026-08-21T08:08:00+00:00"


@pytest.mark.parametrize("price", ["526.6332", "$$526.6332", "€526.6332", "$526.63$"])
def test_missing_multiple_or_other_currency_marker_is_rejected(price: str) -> None:
    payload = _payload(); payload["data"]["primaryData"]["lastSalePrice"] = price
    with pytest.raises(NasdaqSoxxInfoObservationError, match="dollar marker"):
        nasdaq_soxx_info_quote(payload, retrieved_at=RETRIEVED, request_count=1)


@pytest.mark.parametrize("mutate, message", [
    (lambda payload: payload["data"].update(assetClass="STOCK"), "SOXX ETF"),
    (lambda payload: payload["data"]["primaryData"].update(currency="KRW"), "currency"),
    (lambda payload: payload["data"]["primaryData"].update(isRealTime=False), "isRealTime"),
])
def test_contradictory_identity_currency_or_delay_is_rejected(mutate, message: str) -> None:
    payload = _payload(); mutate(payload)
    with pytest.raises(NasdaqSoxxInfoObservationError, match=message):
        nasdaq_soxx_info_quote(payload, retrieved_at=RETRIEVED, request_count=1)
