from datetime import datetime, timezone

import pytest

from stock_data.providers.naver_desktop_005930_current_html_observation import (
    NaverDesktopHtmlObservationError,
    naver_desktop_005930_html_quote,
)


def _body(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "symbol": "005930", "venue": "KRX", "price": "71,200", "unit": "KRW per share",
        "provider_timestamp": "2026-08-21T14:30:00+09:00", "session": "OPEN", "delay_seconds": 0,
    }
    payload.update(overrides)
    import json
    return ("<script id=\"naver-current-observation\" type=\"application/json\">" + json.dumps(payload) + "</script>").encode()


def test_accepts_only_explicit_same_body_contract() -> None:
    source = naver_desktop_005930_html_quote(_body(), retrieved_at=datetime(2026, 8, 21, 5, 31, tzinfo=timezone.utc))
    assert source.value.identity.symbol == "005930"
    assert source.value.value == 71200.0
    assert source.value.unit == "KRW per share"


@pytest.mark.parametrize(("body", "message"), [
    (b"<html>ordinary page text</html>", "schema is missing"),
    (_body(provider_timestamp="not-a-time"), "must be ISO"),
    (_body(symbol="000660"), "symbol differs"),
    (_body(venue="NXT"), "venue must be KRX"),
    (_body(unit="USD per share"), "unit must be KRW"),
    (_body(session="CLOSED"), "session must be OPEN"),
    (_body(delay_seconds=600), "delayed quote"),
])
def test_rejects_missing_or_nonexact_body_fields(body: bytes, message: str) -> None:
    with pytest.raises(NaverDesktopHtmlObservationError, match=message):
        naver_desktop_005930_html_quote(body, retrieved_at=datetime(2026, 8, 21, 5, 31, tzinfo=timezone.utc))
