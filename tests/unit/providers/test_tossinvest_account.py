from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from stock_data.providers.tossinvest import (
    TossInvestResponseError,
    attach_buying_power,
    normalize_buying_power_payload,
    normalize_holdings_payload,
)


def holdings_payload() -> dict:
    return {"result": {
        "totalPurchaseAmount": {"krw": "2000", "usd": "10"},
        "marketValue": {
            "amount": {"krw": "2200", "usd": "11"},
            "amountAfterCost": {"krw": "2180", "usd": "10.8"},
        },
        "profitLoss": {
            "amount": {"krw": "200", "usd": "1"},
            "amountAfterCost": {"krw": "180", "usd": "0.8"},
            "rate": "0.1", "rateAfterCost": "0.09",
        },
        "dailyProfitLoss": {
            "amount": {"krw": "50", "usd": "0.2"}, "rate": "0.02",
        },
        "items": [
            {
                "symbol": "005930", "name": "Fixture KR", "marketCountry": "KR",
                "currency": "KRW", "quantity": "2", "lastPrice": "1100",
                "averagePurchasePrice": "1000",
                "marketValue": {"purchaseAmount": "2000", "amount": "2200", "amountAfterCost": "2180"},
                "profitLoss": {"amount": "200", "amountAfterCost": "180", "rate": "0.1", "rateAfterCost": "0.09"},
                "dailyProfitLoss": {"amount": "50", "rate": "0.02"},
                "cost": {"commission": "10", "tax": "10"},
            },
            {
                "symbol": "AAPL", "name": "Fixture US", "marketCountry": "US",
                "currency": "USD", "quantity": "1", "lastPrice": "11",
                "averagePurchasePrice": "10",
                "marketValue": {"purchaseAmount": "10", "amount": "11", "amountAfterCost": "10.8"},
                "profitLoss": {"amount": "1", "amountAfterCost": "0.8", "rate": "0.1", "rateAfterCost": "0.08"},
                "dailyProfitLoss": {"amount": "0.2", "rate": "0.02"},
                "cost": {"commission": "0.1", "tax": None},
            },
        ],
    }}


def test_holdings_projection_is_identifier_free_and_keeps_currency_buckets():
    normalized = normalize_holdings_payload(
        holdings_payload(), collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
    )

    assert normalized["schema_version"] == 1
    assert normalized["source_operation"] == "getHoldings"
    assert normalized["cash_balance"] is None
    assert normalized["buying_power"] is None
    assert [row["currency"] for row in normalized["summaries"]] == ["KRW", "USD"]
    assert normalized["positions"][0]["purchase_amount"] == "2000"
    assert normalized["positions"][1]["profit_loss_after_cost"] == "0.8"
    rendered = repr(normalized)
    assert "accountNo" not in rendered and "accountSeq" not in rendered


@pytest.mark.parametrize(
    ("field", "private_text"),
    (
        ("symbol", "123456789012"),
        ("name", "accountNo=123456789012"),
        ("name", "valuation=987654321"),
    ),
)
def test_holdings_projection_rejects_private_position_display_text_value_free(
    field, private_text,
):
    payload = deepcopy(holdings_payload())
    payload["result"]["items"][0][field] = private_text

    with pytest.raises(TossInvestResponseError) as captured:
        normalize_holdings_payload(
            payload, collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
        )

    assert private_text not in str(captured.value)
    assert str(captured.value) == (
        "holding display text contains sensitive account data"
    )


def test_holdings_projection_preserves_valid_domestic_and_global_security_text():
    payload = deepcopy(holdings_payload())
    payload["result"]["items"][1]["symbol"] = "BRK.B"
    payload["result"]["items"][1]["name"] = "Accountable Balance Holdings"

    normalized = normalize_holdings_payload(
        payload, collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
    )

    assert normalized["positions"][0]["symbol"] == "005930"
    assert normalized["positions"][1]["symbol"] == "BRK.B"
    assert normalized["positions"][1]["name"] == "Accountable Balance Holdings"


def test_buying_power_pair_is_currency_exact_and_identifier_free():
    rows = [
        normalize_buying_power_payload(
            {"result": {"currency": "KRW", "cashBuyingPower": "5000000"}},
            expected_currency="KRW",
        ),
        normalize_buying_power_payload(
            {"result": {"currency": "USD", "cashBuyingPower": "3500.5"}},
            expected_currency="USD",
        ),
    ]
    snapshot = attach_buying_power(
        normalize_holdings_payload(
            holdings_payload(), collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
        ),
        rows,
    )

    assert snapshot["schema_version"] == 2
    assert snapshot["buying_power"][0]["cash_buying_power"] == "5000000"
    assert snapshot["buying_power"][1]["cash_buying_power"] == "3500.5"
    assert "account" not in repr(snapshot).lower()


@pytest.mark.parametrize(
    "payload,currency",
    [
        ({"result": {"currency": "USD", "cashBuyingPower": "1"}}, "KRW"),
        ({"result": {"currency": "KRW", "cashBuyingPower": "1.5"}}, "KRW"),
        ({"result": {"currency": "USD", "cashBuyingPower": "-1"}}, "USD"),
    ],
)
def test_buying_power_rejects_mismatch_fractional_krw_and_negative(payload, currency):
    with pytest.raises(TossInvestResponseError):
        normalize_buying_power_payload(payload, expected_currency=currency)


@pytest.mark.parametrize("mutation", ["summary_mismatch", "partial", "identity", "duplicate"])
def test_holdings_projection_fails_closed_on_partial_mismatch_or_identity(mutation):
    payload = deepcopy(holdings_payload())
    if mutation == "summary_mismatch":
        payload["result"]["marketValue"]["amount"]["krw"] = "9999"
    elif mutation == "partial":
        del payload["result"]["items"][0]["profitLoss"]
    elif mutation == "identity":
        payload["result"]["accountSeq"] = 7
    else:
        payload["result"]["items"].append(deepcopy(payload["result"]["items"][0]))

    with pytest.raises(TossInvestResponseError):
        normalize_holdings_payload(
            payload, collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
        )
