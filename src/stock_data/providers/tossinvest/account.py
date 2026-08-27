from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from stock_data.contracts.toss_account_snapshot import (
    TOSS_ACCOUNT_CURRENCIES,
    TOSS_ACCOUNT_FORBIDDEN_KEYS,
    TOSS_ACCOUNT_OPERATION,
    TOSS_BUYING_POWER_OPERATION,
    TOSS_ACCOUNT_SNAPSHOT_SCHEMA_VERSION,
    TOSS_ACCOUNT_SOURCE,
    TOSS_ACCOUNT_SOURCE_SPEC_VERSION,
)
from stock_data.orchestration.account_privacy import redact_account_text
from stock_data.providers.tossinvest.client import TossInvestResponseError


_SYMBOL = re.compile(r"^[A-Za-z0-9.\-]{1,32}$")


def _decimal(value: Any, field: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str) or not value or len(value) > 30:
        raise TossInvestResponseError(f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise TossInvestResponseError(f"{field} must be a decimal string") from None
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise TossInvestResponseError(f"{field} is outside the contract")
    return parsed


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise TossInvestResponseError(f"{field} must be non-empty text")
    if any(ord(character) < 32 for character in value):
        raise TossInvestResponseError(f"{field} contains control characters")
    return value.strip()


def _identifier_free_display_text(value: str) -> str:
    if redact_account_text(value, limit=max(1, len(value))) != value:
        raise TossInvestResponseError(
            "holding display text contains sensitive account data"
        )
    return value


def _object(value: Any, required: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not required.issubset(value):
        raise TossInvestResponseError(f"{field} is incomplete")
    return value


def _canonical(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _price_pair(value: Any, field: str) -> dict[str, Decimal | None]:
    row = _object(value, {"krw"}, field)
    result: dict[str, Decimal | None] = {
        "KRW": _decimal(row["krw"], f"{field}.krw")
    }
    usd = row.get("usd")
    result["USD"] = None if usd is None else _decimal(usd, f"{field}.usd")
    return result


def _forbidden_key_scan(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key) in TOSS_ACCOUNT_FORBIDDEN_KEYS:
                raise TossInvestResponseError("account response contains forbidden identity/auth data")
            _forbidden_key_scan(nested)
    elif isinstance(value, list):
        for nested in value:
            _forbidden_key_scan(nested)


def normalize_holdings_payload(
    payload: Any,
    *,
    collected_at: datetime,
) -> dict[str, Any]:
    """Validate official getHoldings output and emit an identifier-free projection."""
    if collected_at.tzinfo is None or collected_at.utcoffset() is None:
        raise TossInvestResponseError("collected_at must be timezone-aware")
    _forbidden_key_scan(payload)
    envelope = _object(payload, {"result"}, "holdings envelope")
    result = _object(
        envelope["result"],
        {"totalPurchaseAmount", "marketValue", "profitLoss", "dailyProfitLoss", "items"},
        "holdings result",
    )
    market_value = _object(
        result["marketValue"], {"amount", "amountAfterCost"}, "marketValue"
    )
    profit_loss = _object(
        result["profitLoss"], {"amount", "amountAfterCost", "rate", "rateAfterCost"},
        "profitLoss",
    )
    daily_profit_loss = _object(
        result["dailyProfitLoss"], {"amount", "rate"}, "dailyProfitLoss"
    )
    summary_pairs = {
        "purchase_amount": _price_pair(result["totalPurchaseAmount"], "totalPurchaseAmount"),
        "market_value": _price_pair(market_value["amount"], "marketValue.amount"),
        "market_value_after_cost": _price_pair(
            market_value["amountAfterCost"], "marketValue.amountAfterCost"
        ),
        "profit_loss": _price_pair(profit_loss["amount"], "profitLoss.amount"),
        "profit_loss_after_cost": _price_pair(
            profit_loss["amountAfterCost"], "profitLoss.amountAfterCost"
        ),
        "daily_profit_loss": _price_pair(
            daily_profit_loss["amount"], "dailyProfitLoss.amount"
        ),
    }
    overall_rates = {
        "profit_loss_rate": _decimal(profit_loss["rate"], "profitLoss.rate"),
        "profit_loss_rate_after_cost": _decimal(
            profit_loss["rateAfterCost"], "profitLoss.rateAfterCost"
        ),
        "daily_profit_loss_rate": _decimal(
            daily_profit_loss["rate"], "dailyProfitLoss.rate"
        ),
    }

    items = result["items"]
    if not isinstance(items, list):
        raise TossInvestResponseError("holdings items must be an array")
    positions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    sums = {
        currency: {field: Decimal("0") for field in summary_pairs}
        for currency in TOSS_ACCOUNT_CURRENCIES
    }
    for index, raw in enumerate(items):
        item = _object(
            raw,
            {
                "symbol", "name", "marketCountry", "currency", "quantity",
                "lastPrice", "averagePurchasePrice", "marketValue", "profitLoss",
                "dailyProfitLoss", "cost",
            },
            f"items[{index}]",
        )
        symbol = item["symbol"]
        if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
            raise TossInvestResponseError("holding symbol is invalid")
        symbol = _identifier_free_display_text(symbol)
        name = _identifier_free_display_text(_text(item["name"], "holding.name"))
        country = item["marketCountry"]
        currency = item["currency"]
        if (country, currency) not in {("KR", "KRW"), ("US", "USD")}:
            raise TossInvestResponseError("holding country/currency pair is invalid")
        identity = (country, symbol)
        if identity in seen:
            raise TossInvestResponseError("holding identity is duplicated")
        seen.add(identity)

        item_market = _object(
            item["marketValue"], {"purchaseAmount", "amount", "amountAfterCost"},
            "holding.marketValue",
        )
        item_profit = _object(
            item["profitLoss"], {"amount", "amountAfterCost", "rate", "rateAfterCost"},
            "holding.profitLoss",
        )
        item_daily = _object(
            item["dailyProfitLoss"], {"amount", "rate"}, "holding.dailyProfitLoss"
        )
        item_cost = _object(item["cost"], {"commission"}, "holding.cost")
        values = {
            "quantity": _decimal(item["quantity"], "holding.quantity", nonnegative=True),
            "last_price": _decimal(item["lastPrice"], "holding.lastPrice", nonnegative=True),
            "average_purchase_price": _decimal(
                item["averagePurchasePrice"], "holding.averagePurchasePrice", nonnegative=True
            ),
            "purchase_amount": _decimal(
                item_market["purchaseAmount"], "holding.marketValue.purchaseAmount",
                nonnegative=True,
            ),
            "market_value": _decimal(
                item_market["amount"], "holding.marketValue.amount", nonnegative=True
            ),
            "market_value_after_cost": _decimal(
                item_market["amountAfterCost"], "holding.marketValue.amountAfterCost",
                nonnegative=True,
            ),
            "profit_loss": _decimal(item_profit["amount"], "holding.profitLoss.amount"),
            "profit_loss_after_cost": _decimal(
                item_profit["amountAfterCost"], "holding.profitLoss.amountAfterCost"
            ),
            "profit_loss_rate": _decimal(item_profit["rate"], "holding.profitLoss.rate"),
            "profit_loss_rate_after_cost": _decimal(
                item_profit["rateAfterCost"], "holding.profitLoss.rateAfterCost"
            ),
            "daily_profit_loss": _decimal(
                item_daily["amount"], "holding.dailyProfitLoss.amount"
            ),
            "daily_profit_loss_rate": _decimal(
                item_daily["rate"], "holding.dailyProfitLoss.rate"
            ),
            "commission": _decimal(
                item_cost["commission"], "holding.cost.commission", nonnegative=True
            ),
        }
        tax_raw = item_cost.get("tax")
        tax = None if tax_raw is None else _decimal(
            tax_raw, "holding.cost.tax", nonnegative=True
        )
        positions.append({
            "symbol": symbol,
            "name": name,
            "market_country": country,
            "currency": currency,
            **{key: _canonical(value) for key, value in values.items()},
            "tax": None if tax is None else _canonical(tax),
        })
        for summary_field in sums[currency]:
            sums[currency][summary_field] += values[summary_field]

    summaries: list[dict[str, str]] = []
    for currency in TOSS_ACCOUNT_CURRENCIES:
        expected_values = {
            field: summary_pairs[field][currency] for field in summary_pairs
        }
        present = any(value is not None for value in expected_values.values())
        if not present:
            if sums[currency] != {field: Decimal("0") for field in summary_pairs}:
                raise TossInvestResponseError("holding summary currency is missing")
            continue
        if any(value is None for value in expected_values.values()):
            raise TossInvestResponseError("holding summary currency is partial")
        if any(expected_values[field] != sums[currency][field] for field in summary_pairs):
            raise TossInvestResponseError("holding summary does not reconcile with positions")
        summaries.append({
            "currency": currency,
            **{field: _canonical(expected_values[field]) for field in summary_pairs},
        })

    return {
        "schema_version": 1,
        "provider": TOSS_ACCOUNT_SOURCE,
        "source_operation": TOSS_ACCOUNT_OPERATION,
        "source_spec_version": TOSS_ACCOUNT_SOURCE_SPEC_VERSION,
        "collected_at": collected_at.isoformat(),
        "registered_holder_scope": "SELF",
        "economic_attribution_scope": "SELF",
        "cash_balance": None,
        "buying_power": None,
        "unsupported_fields": ["cash_balance", "buying_power", "realized_pnl"],
        "summaries": summaries,
        "overall_rates": {
            key: _canonical(value) for key, value in overall_rates.items()
        },
        "positions": positions,
    }


def normalize_buying_power_payload(payload: Any, *, expected_currency: str) -> dict[str, str]:
    """Validate one official, cash-only buying-power response."""
    if expected_currency not in TOSS_ACCOUNT_CURRENCIES:
        raise TossInvestResponseError("buying-power currency is unsupported")
    _forbidden_key_scan(payload)
    envelope = _object(payload, {"result"}, "buying-power envelope")
    result = _object(
        envelope["result"], {"currency", "cashBuyingPower"}, "buying-power result"
    )
    if set(result) != {"currency", "cashBuyingPower"}:
        raise TossInvestResponseError("buying-power result has unexpected fields")
    if result["currency"] != expected_currency:
        raise TossInvestResponseError("buying-power currency does not match the request")
    value = _decimal(
        result["cashBuyingPower"], "cashBuyingPower", nonnegative=True
    )
    if expected_currency == "KRW" and value != value.to_integral_value():
        raise TossInvestResponseError("KRW buying power must be an integer amount")
    return {
        "currency": expected_currency,
        "cash_buying_power": _canonical(value),
        "source_operation": TOSS_BUYING_POWER_OPERATION,
    }


def attach_buying_power(
    holdings_snapshot: dict[str, Any], buying_power_rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Attach an exact KRW/USD pair without mutating the validated holdings view."""
    expected = set(TOSS_ACCOUNT_CURRENCIES)
    currencies = [row.get("currency") for row in buying_power_rows]
    if len(currencies) != len(expected) or set(currencies) != expected:
        raise TossInvestResponseError("buying-power responses must contain KRW and USD once")
    result = dict(holdings_snapshot)
    result["schema_version"] = TOSS_ACCOUNT_SNAPSHOT_SCHEMA_VERSION
    result["unsupported_fields"] = ["cash_balance", "realized_pnl"]
    result["buying_power"] = sorted(
        (dict(row) for row in buying_power_rows), key=lambda row: row["currency"]
    )
    return result


__all__ = [
    "attach_buying_power", "normalize_buying_power_payload", "normalize_holdings_payload",
]
