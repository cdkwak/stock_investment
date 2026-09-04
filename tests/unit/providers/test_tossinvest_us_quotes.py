from __future__ import annotations

import pytest

from stock_data.providers.tossinvest.client import (
    TossInvestAPIResponse,
    TossInvestHTTPDiagnostics,
    TossInvestRateLimit,
    TossInvestRateLimitError,
    TossInvestResponseError,
)
from stock_data.providers.tossinvest.us_quotes import (
    TossInvestUSQuoteRateLimited,
    fetch_us_quotes,
)


PAYLOAD = {
    "result": [{
        "symbol": "SKHY",
        "timestamp": "2026-09-04T21:41:00.000+09:00",
        "lastPrice": "164.5",
        "currency": "USD",
    }],
}


class Client:
    def __init__(self, payload=PAYLOAD, *, status=200, retry_after=None):
        self.payload = payload
        self.status = status
        self.retry_after = retry_after
        self.calls = []

    def get_market_data(self, path, *, params=None):
        self.calls.append((path, params))
        return TossInvestAPIResponse(
            self.status,
            self.payload,
            TossInvestRateLimit(
                group="STOCK_PRICE", limit=15,
                retry_after_seconds=self.retry_after,
            ),
        )


def test_fetch_us_quotes_parses_verified_skhy_payload_with_one_call() -> None:
    client = Client()
    rows = fetch_us_quotes(client, ("SKHY",))
    assert rows[0] == {
        "symbol": "SKHY",
        "timestamp_kst": "2026-09-04T21:41:00.000+09:00",
        "last_price": 164.5,
        "currency": "USD",
        "retrieved_at_utc": rows[0]["retrieved_at_utc"],
    }
    assert client.calls == [
        ("/api/v1/prices", {"symbols": "SKHY"}),
    ]


def test_fetch_us_quotes_accepts_valid_empty_result() -> None:
    assert fetch_us_quotes(Client({"result": []}), ("SKHY",)) == []


def test_fetch_us_quotes_rejects_partial_nonempty_result() -> None:
    with pytest.raises(TossInvestResponseError, match="omitted"):
        fetch_us_quotes(Client(), ("SKHY", "SOXL"))


def test_fetch_us_quotes_converts_429_to_no_retry_signal() -> None:
    class RateLimitedClient:
        calls = 0

        def get_market_data(self, path, *, params=None):
            self.calls += 1
            rate_limit = TossInvestRateLimit(
                group="STOCK_PRICE", limit=15, retry_after_seconds=1,
            )
            raise TossInvestRateLimitError(
                "rate limited",
                details=TossInvestHTTPDiagnostics(
                    http_status=429, rate_limit=rate_limit,
                ),
            )

    client = RateLimitedClient()
    with pytest.raises(TossInvestUSQuoteRateLimited) as caught:
        fetch_us_quotes(client, ("SKHY",))
    assert caught.value.retry_after_seconds == 1
    assert client.calls == 1
