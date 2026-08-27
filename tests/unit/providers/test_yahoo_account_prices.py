from dataclasses import replace

import pytest

from stock_data.providers.yahoo_account_prices import (
    YahooAccountPriceSymbol,
    normalize_yahoo_account_price,
    validate_yahoo_account_price_symbol,
    yahoo_account_price_unavailable,
)


def _symbol() -> YahooAccountPriceSymbol:
    return YahooAccountPriceSymbol("ISA", "111111", "111111.KS", "XKRX")


def _payload() -> dict[str, object]:
    return {
        "provider": "YAHOO_CHART_API", "provider_symbol": "111111.KS",
        "exchange": "XKRX", "currency": "KRW", "unit": "KRW_PER_SHARE",
        "price": "125.50", "as_of": "2026-08-26T05:00:00+00:00",
        "captured_at": "2026-08-26T05:00:05+00:00",
        "finality": "AS_RETRIEVED",
    }


def test_normalizer_preserves_explicit_identity_unit_and_aware_clocks() -> None:
    value = normalize_yahoo_account_price(_payload(), _symbol())

    assert (value.section, value.ticker, value.provider_symbol) == (
        "ISA", "111111", "111111.KS",
    )
    assert str(value.price) == "125.50"
    assert value.provider == "YAHOO_CHART_API"
    assert value.currency == "KRW" and value.unit == "KRW_PER_SHARE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_symbol", "111111.KQ"), ("exchange", "XKOS"),
        ("currency", "USD"), ("unit", "INDEX_POINTS"),
        ("provider", "OTHER"), ("price", 0), ("price", "NaN"),
        ("as_of", "2026-08-26T05:00:00"),
        ("captured_at", "2026-08-26T04:59:59+00:00"),
        ("finality", "REALTIME"),
    ],
)
def test_normalizer_fails_closed_on_identity_unit_clock_or_value_mismatch(
    field: str, value: object,
) -> None:
    payload = _payload()
    payload[field] = value
    with pytest.raises((TypeError, ValueError)):
        normalize_yahoo_account_price(payload, _symbol())


def test_symbol_requires_explicit_exchange_qualified_mapping() -> None:
    assert validate_yahoo_account_price_symbol(_symbol()) == _symbol()
    with pytest.raises(ValueError, match="exchange-qualified"):
        validate_yahoo_account_price_symbol(replace(_symbol(), provider_symbol="111111"))
    with pytest.raises(ValueError, match="currency"):
        validate_yahoo_account_price_symbol(replace(_symbol(), currency="USD"))


@pytest.mark.parametrize(
    ("provider_symbol", "exchange"),
    [
        ("garbage/anything", "XKRX"),
        ("111111.BAD", "XKRX"),
        ("111111.KQ ", "XKRX"),
        ("111111.KQ", "NOT_AN_EXCHANGE"),
        ("222222.KQ", "XKRX"),
    ],
)
def test_symbol_rejects_bad_suffix_exchange_whitespace_or_ticker_binding(
    provider_symbol: str, exchange: str,
) -> None:
    with pytest.raises(ValueError):
        validate_yahoo_account_price_symbol(
            replace(_symbol(), provider_symbol=provider_symbol, exchange=exchange),
        )


def test_unavailable_reason_is_sanitized_and_value_free() -> None:
    result = yahoo_account_price_unavailable(_symbol(), "PROVIDER_EMPTY")
    assert result.reason == "PROVIDER_EMPTY"
    with pytest.raises(ValueError):
        yahoo_account_price_unavailable(_symbol(), "token=secret")
