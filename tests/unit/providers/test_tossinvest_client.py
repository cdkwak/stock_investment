from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest
import requests

from stock_data.providers.tossinvest import (
    DEFAULT_BASE_URL,
    TossInvestAuthenticationError,
    TossInvestClient,
    TossInvestConfigurationError,
    TossInvestRateLimitError,
    TossInvestResponseError,
)


FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "tossinvest_token_success.json"


class RecordingAdapter(requests.adapters.BaseAdapter):
    def __init__(self, responses: list[tuple[int, object, dict[str, str]]]):
        self.responses = list(responses)
        self.requests: list[requests.PreparedRequest] = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        status, payload, headers = self.responses.pop(0)
        response = requests.Response()
        response.status_code = status
        response.request = request
        response.headers.update(headers)
        if isinstance(payload, str):
            response._content = payload.encode()
        else:
            response._content = json.dumps(payload).encode()
        return response

    def close(self):
        pass


def session_with(adapter: RecordingAdapter) -> requests.Session:
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def success_payload() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_token_request_is_exact_form_urlencoded_and_cached():
    adapter = RecordingAdapter(
        [
            (
                200,
                success_payload(),
                {
                    "Content-Type": "application/json",
                    "X-RateLimit-Limit": "5",
                    "X-RateLimit-Remaining": "4",
                    "X-RateLimit-Reset": "1",
                },
            )
        ]
    )
    client = TossInvestClient(
        client_id="unit-client-id",
        client_secret="unit-client-secret",
        session=session_with(adapter),
        clock=lambda: 100.0,
    )

    assert client.access_token() == "fixture-access-token"
    assert client.authorization_headers() == {
        "Authorization": "Bearer fixture-access-token"
    }
    assert len(adapter.requests) == 1

    prepared = adapter.requests[0]
    assert prepared.method == "POST"
    assert prepared.url == f"{DEFAULT_BASE_URL}/oauth2/token"
    assert prepared.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert "Authorization" not in prepared.headers
    body = prepared.body.decode() if isinstance(prepared.body, bytes) else prepared.body
    parsed = parse_qs(body, keep_blank_values=True)
    assert set(parsed) == {"grant_type", "client_id", "client_secret"}
    assert parsed == {
        "grant_type": ["client_credentials"],
        "client_id": ["unit-client-id"],
        "client_secret": ["unit-client-secret"],
    }
    assert not body.lstrip().startswith("{")

    metadata = client.token_metadata
    assert metadata is not None
    assert metadata.http_status == 200 and metadata.expires_in == 86400
    assert metadata.rate_limit.group == "AUTH"
    assert metadata.rate_limit.limit == 5
    assert metadata.rate_limit.remaining == 4
    assert client.token_request_count == 1
    assert client.market_request_count == 0


def test_token_refreshes_inside_expiry_margin():
    now = [0.0]
    first = success_payload()
    second = {**first, "access_token": "second-fixture-token"}
    adapter = RecordingAdapter(
        [
            (200, first, {"Content-Type": "application/json"}),
            (200, second, {"Content-Type": "application/json"}),
        ]
    )
    client = TossInvestClient(
        client_id="unit-client-id",
        client_secret="unit-client-secret",
        session=session_with(adapter),
        clock=lambda: now[0],
    )

    assert client.access_token() == "fixture-access-token"
    now[0] = 86341.0
    assert client.access_token() == "second-fixture-token"
    assert len(adapter.requests) == 2


def test_from_environment_uses_default_base_url_and_requires_credentials(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("TOSSINVEST_BASE_URL", raising=False)
    monkeypatch.setenv("TOSSINVEST_CLIENT_ID", "env-client-id")
    monkeypatch.setenv("TOSSINVEST_CLIENT_SECRET", "env-client-secret")
    client = TossInvestClient.from_environment(project_root=tmp_path)
    assert client.base_url == DEFAULT_BASE_URL

    monkeypatch.delenv("TOSSINVEST_CLIENT_SECRET")
    with pytest.raises(TossInvestConfigurationError) as caught:
        TossInvestClient.from_environment(project_root=tmp_path)
    assert "TOSSINVEST_CLIENT_SECRET" in str(caught.value)
    assert "env-client-id" not in str(caught.value)


def test_401_keeps_safe_oauth_diagnostics_and_redacts_credentials():
    adapter = RecordingAdapter(
        [
            (
                401,
                {
                    "error": "invalid_client",
                    "error_description": (
                        "unit-client-id unit-client-secret "
                        "Authorization: Bearer leaked-token"
                    ),
                },
                {
                    "Content-Type": "application/json; charset=utf-8",
                    "WWW-Authenticate": "Basic realm=\"openapi\"",
                    "X-Request-Id": "request-401",
                },
            )
        ]
    )
    client = TossInvestClient(
        client_id="unit-client-id",
        client_secret="unit-client-secret",
        session=session_with(adapter),
    )

    with pytest.raises(TossInvestAuthenticationError) as caught:
        client.access_token()
    details = caught.value.details
    assert details is not None
    assert details.http_status == 401
    assert details.content_type == "application/json"
    assert details.error_code == "invalid_client"
    assert details.request_id == "request-401"
    assert details.www_authenticate == 'Basic realm="openapi"'
    exposed = repr(details)
    assert "unit-client-id" not in exposed
    assert "unit-client-secret" not in exposed
    assert "leaked-token" not in exposed
    assert len(adapter.requests) == 1


def test_429_preserves_rate_limit_diagnostics_without_retry():
    adapter = RecordingAdapter(
        [
            (
                429,
                {
                    "error": {
                        "requestId": "request-429",
                        "code": "rate-limit-exceeded",
                        "message": "요청 한도를 초과했습니다.",
                    }
                },
                {
                    "Content-Type": "application/json",
                    "X-RateLimit-Limit": "5",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": "1",
                    "Retry-After": "1",
                },
            )
        ]
    )
    client = TossInvestClient(
        client_id="unit-client-id",
        client_secret="unit-client-secret",
        session=session_with(adapter),
    )

    with pytest.raises(TossInvestRateLimitError) as caught:
        client.access_token()
    details = caught.value.details
    assert details is not None
    assert details.http_status == 429
    assert details.error_code == "rate-limit-exceeded"
    assert details.error_message == "요청 한도를 초과했습니다."
    assert details.request_id == "request-429"
    assert details.rate_limit is not None
    assert details.rate_limit.group == "AUTH"
    assert details.rate_limit.limit == 5
    assert details.rate_limit.remaining == 0
    assert details.rate_limit.reset_seconds == 1
    assert details.rate_limit.retry_after_seconds == 1
    assert dict(details.rate_limit.raw_headers) == {
        "X-RateLimit-Limit": "5",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": "1",
        "Retry-After": "1",
    }
    assert len(adapter.requests) == 1


def test_read_only_market_get_reuses_cached_token_and_preserves_rate_limit():
    adapter = RecordingAdapter(
        [
            (200, success_payload(), {"Content-Type": "application/json"}),
            (
                200,
                {
                    "result": [
                        {
                            "symbol": "KOSPI",
                            "timestamp": "2026-08-11T15:30:00+09:00",
                            "lastPrice": "3200.00",
                        }
                    ]
                },
                {
                    "Content-Type": "application/json",
                    "X-RateLimit-Limit": "10",
                    "X-RateLimit-Remaining": "9",
                    "X-RateLimit-Reset": "1",
                },
            ),
        ]
    )
    client = TossInvestClient(
        client_id="unit-client-id",
        client_secret="unit-client-secret",
        session=session_with(adapter),
    )

    response = client.get_market_data(
        "/api/v1/market-indicators/prices", params={"symbols": "KOSPI"}
    )
    assert response.http_status == 200
    assert response.payload["result"][0]["symbol"] == "KOSPI"
    assert response.rate_limit.group == "MARKET_INDICATOR"
    assert response.rate_limit.limit == 10
    assert client.token_request_count == 1
    assert client.market_request_count == 1
    assert len(adapter.requests) == 2
    prepared = adapter.requests[1]
    assert prepared.method == "GET"
    assert prepared.url.endswith("/api/v1/market-indicators/prices?symbols=KOSPI")
    assert prepared.headers["Authorization"] == "Bearer fixture-access-token"
    assert "X-Tossinvest-Account" not in prepared.headers


def test_read_only_market_get_rejects_order_path_without_network():
    adapter = RecordingAdapter([])
    client = TossInvestClient(
        client_id="unit-client-id",
        client_secret="unit-client-secret",
        session=session_with(adapter),
    )
    with pytest.raises(TossInvestConfigurationError):
        client.get_market_data("/api/v1/orders", params={})
    assert adapter.requests == []
    assert client.token_request_count == 0
    assert client.market_request_count == 0


def test_read_only_stock_price_get_is_allowlisted_without_account_header():
    adapter = RecordingAdapter([
        (200, success_payload(), {"Content-Type": "application/json"}),
        (200, {"result": [{
            "symbol": "005930", "timestamp": "2026-08-21T10:30:00+09:00",
            "lastPrice": "70000", "currency": "KRW",
        }]}, {"Content-Type": "application/json"}),
    ])
    client = TossInvestClient(
        client_id="unit-client-id", client_secret="unit-client-secret",
        session=session_with(adapter),
    )

    response = client.get_market_data("/api/v1/prices", params={"symbols": "005930"})

    assert response.rate_limit.group == "STOCK_PRICE"
    assert client.token_request_count == client.market_request_count == 1
    prepared = adapter.requests[1]
    assert prepared.method == "GET" and prepared.url.endswith("/api/v1/prices?symbols=005930")
    assert "X-Tossinvest-Account" not in prepared.headers


def test_read_only_account_discovery_and_holdings_keep_selector_out_of_url():
    adapter = RecordingAdapter([
        (200, success_payload(), {"Content-Type": "application/json"}),
        (200, {"result": [{
            "accountNo": "fixture-private-number", "accountSeq": 7,
            "accountType": "BROKERAGE",
        }]}, {"Content-Type": "application/json", "X-RateLimit-Limit": "1"}),
        (200, {"result": {
            "totalPurchaseAmount": {"krw": "0", "usd": None},
            "marketValue": {
                "amount": {"krw": "0", "usd": None},
                "amountAfterCost": {"krw": "0", "usd": None},
            },
            "profitLoss": {
                "amount": {"krw": "0", "usd": None},
                "amountAfterCost": {"krw": "0", "usd": None},
                "rate": "0", "rateAfterCost": "0",
            },
            "dailyProfitLoss": {
                "amount": {"krw": "0", "usd": None}, "rate": "0",
            },
            "items": [],
        }}, {"Content-Type": "application/json", "X-RateLimit-Limit": "5"}),
    ])
    client = TossInvestClient(
        client_id="unit-client-id", client_secret="unit-client-secret",
        session=session_with(adapter),
    )

    selector = client.brokerage_account_seq()
    holdings = client.get_holdings(account_seq=selector)

    assert selector == 7
    assert holdings.rate_limit.group == "ASSET"
    assert client.account_request_count == 2
    account_request, holdings_request = adapter.requests[1:]
    assert account_request.url.endswith("/api/v1/accounts")
    assert "X-Tossinvest-Account" not in account_request.headers
    assert holdings_request.url.endswith("/api/v1/holdings")
    assert holdings_request.headers["X-Tossinvest-Account"] == "7"
    assert "fixture-private-number" not in repr(holdings)


def test_buying_power_is_exact_currency_read_only_order_info_request():
    adapter = RecordingAdapter([
        (200, success_payload(), {"Content-Type": "application/json"}),
        (200, {"result": {
            "currency": "KRW", "cashBuyingPower": "5000000",
        }}, {"Content-Type": "application/json", "X-RateLimit-Limit": "5"}),
    ])
    client = TossInvestClient(
        client_id="unit-client-id", client_secret="unit-client-secret",
        session=session_with(adapter),
    )

    response = client.get_buying_power(account_seq=7, currency="KRW")

    assert response.rate_limit.group == "ORDER_INFO"
    prepared = adapter.requests[1]
    assert prepared.url.endswith("/api/v1/buying-power?currency=KRW")
    assert prepared.headers["X-Tossinvest-Account"] == "7"


def test_account_selection_fails_closed_when_brokerage_account_is_ambiguous():
    adapter = RecordingAdapter([
        (200, success_payload(), {"Content-Type": "application/json"}),
        (200, {"result": [
            {"accountNo": "one", "accountSeq": 1, "accountType": "BROKERAGE"},
            {"accountNo": "two", "accountSeq": 2, "accountType": "BROKERAGE"},
        ]}, {"Content-Type": "application/json"}),
    ])
    client = TossInvestClient(
        client_id="unit-client-id", client_secret="unit-client-secret",
        session=session_with(adapter),
    )

    with pytest.raises(TossInvestResponseError, match="not unambiguous") as caught:
        client.brokerage_account_seq()

    assert "one" not in str(caught.value) and "two" not in str(caught.value)
    assert client.account_request_count == 1
