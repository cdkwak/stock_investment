from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any


YAHOO_ACCOUNT_PRICE_PROVIDER = "YAHOO_CHART_API"
YAHOO_ACCOUNT_PRICE_UNIT = "KRW_PER_SHARE"
YAHOO_ACCOUNT_PRICE_FINALITIES = frozenset({"AS_RETRIEVED", "COMPLETED_SESSION"})


@dataclass(frozen=True, slots=True)
class YahooAccountPriceSymbol:
    section: str
    ticker: str
    provider_symbol: str
    exchange: str
    currency: str = "KRW"


@dataclass(frozen=True, slots=True)
class YahooAccountPriceObservation:
    section: str
    ticker: str
    provider_symbol: str
    exchange: str
    currency: str
    unit: str
    price: Decimal
    provider: str
    as_of: str
    captured_at: str
    finality: str


@dataclass(frozen=True, slots=True)
class YahooAccountPriceUnavailable:
    section: str
    ticker: str
    reason: str


def validate_yahoo_account_price_symbol(
    value: YahooAccountPriceSymbol,
) -> YahooAccountPriceSymbol:
    if not isinstance(value, YahooAccountPriceSymbol):
        raise TypeError("Yahoo account-price symbol is required")
    if value.section not in {"ISA", "종합"}:
        raise ValueError("Yahoo account-price section is invalid")
    if re.fullmatch(r"\d{6}", value.ticker) is None:
        raise ValueError("Yahoo account-price ticker must be six digits")
    if (
        not isinstance(value.provider_symbol, str)
        or re.fullmatch(r"\d{6}\.(KS|KQ)", value.provider_symbol) is None
        or value.provider_symbol[:6] != value.ticker
    ):
        raise ValueError("Yahoo account-price symbol must be explicitly exchange-qualified")
    if value.exchange != "XKRX":
        raise ValueError("Yahoo account-price exchange must be XKRX")
    if value.currency != "KRW":
        raise ValueError("Yahoo account-price currency must be KRW")
    return value


def _aware_iso(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be an aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an aware timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def normalize_yahoo_account_price(
    payload: Any,
    expected: YahooAccountPriceSymbol,
) -> YahooAccountPriceObservation:
    """Normalize one injected result; this module owns no transport."""

    expected = validate_yahoo_account_price_symbol(expected)
    required = {
        "provider", "provider_symbol", "exchange", "currency", "unit",
        "price", "as_of", "captured_at", "finality",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("Yahoo account-price payload keys differ")
    if payload["provider"] != YAHOO_ACCOUNT_PRICE_PROVIDER:
        raise ValueError("Yahoo account-price provider differs")
    if payload["provider_symbol"] != expected.provider_symbol:
        raise ValueError("Yahoo account-price symbol differs")
    if payload["exchange"] != expected.exchange:
        raise ValueError("Yahoo account-price exchange differs")
    if payload["currency"] != expected.currency:
        raise ValueError("Yahoo account-price currency differs")
    if payload["unit"] != YAHOO_ACCOUNT_PRICE_UNIT:
        raise ValueError("Yahoo account-price unit differs")
    if payload["finality"] not in YAHOO_ACCOUNT_PRICE_FINALITIES:
        raise ValueError("Yahoo account-price finality differs")
    raw_price = payload["price"]
    if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float, str)):
        raise TypeError("Yahoo account price must be numeric")
    try:
        price = Decimal(str(raw_price))
    except InvalidOperation as error:
        raise ValueError("Yahoo account price is invalid") from error
    if not price.is_finite() or price <= 0:
        raise ValueError("Yahoo account price must be positive and finite")
    as_of = _aware_iso(payload["as_of"], "as_of")
    captured_at = _aware_iso(payload["captured_at"], "captured_at")
    if datetime.fromisoformat(captured_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        as_of.replace("Z", "+00:00")
    ):
        raise ValueError("Yahoo account price captured_at predates as_of")
    return YahooAccountPriceObservation(
        section=expected.section, ticker=expected.ticker,
        provider_symbol=expected.provider_symbol, exchange=expected.exchange,
        currency=expected.currency, unit=YAHOO_ACCOUNT_PRICE_UNIT,
        price=price, provider=YAHOO_ACCOUNT_PRICE_PROVIDER,
        as_of=as_of, captured_at=captured_at, finality=payload["finality"],
    )


def yahoo_account_price_unavailable(
    expected: YahooAccountPriceSymbol, reason: str,
) -> YahooAccountPriceUnavailable:
    expected = validate_yahoo_account_price_symbol(expected)
    if (
        not isinstance(reason, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", reason) is None
    ):
        raise ValueError("Yahoo account-price unavailable reason is invalid")
    return YahooAccountPriceUnavailable(expected.section, expected.ticker, reason)


__all__ = [
    "YAHOO_ACCOUNT_PRICE_FINALITIES", "YAHOO_ACCOUNT_PRICE_PROVIDER",
    "YAHOO_ACCOUNT_PRICE_UNIT", "YahooAccountPriceObservation",
    "YahooAccountPriceSymbol", "YahooAccountPriceUnavailable",
    "normalize_yahoo_account_price", "validate_yahoo_account_price_symbol",
    "yahoo_account_price_unavailable",
]
