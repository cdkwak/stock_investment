from datetime import datetime, timezone

import pytest

from stock_data.providers.naver_domestic_stock_current_observation import NaverDomesticStockObservationError, naver_domestic_stock_quote


RETRIEVED = datetime(2026, 8, 21, 4, 26, 45, tzinfo=timezone.utc)


def _payload() -> dict:
    return {"cd": "A005930", "mks": "KOSPI", "ms": "OPEN", "nv": "71,200", "dt": "20260821132615"}


def test_exact_kospi_regular_contract_maps_to_krw_per_share() -> None:
    source = naver_domestic_stock_quote(_payload(), retrieved_at=RETRIEVED)
    assert source.value.identity.symbol == "005930"
    assert source.value.unit == "KRW per share"
    assert source.value.value == 71200.0


@pytest.mark.parametrize("field, value, message", [
    ("cd", "A000660", "A005930"), ("mks", "NXT", "KOSPI"),
    ("ms", "CLOSE", "OPEN"), ("dt", "20260821110000", "60-minute"),
])
def test_missing_or_nonregular_identity_evidence_is_numeric_free(field: str, value: str, message: str) -> None:
    payload = _payload(); payload[field] = value
    with pytest.raises(NaverDomesticStockObservationError, match=message):
        naver_domestic_stock_quote(payload, retrieved_at=RETRIEVED)
