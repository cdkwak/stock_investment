from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from stock_data.contracts.manual_account_market_values import (
    ManualAccountMarketValueCache,
    ManualAccountMarketValueRow,
    ManualAccountSectionSummary,
    manual_account_basis_sha256,
    manual_account_market_value_cache_payload,
    parse_manual_account_market_value_cache,
)
from stock_data.gui.manual_account_snapshot import parse_manual_account_snapshot


def _snapshot(quantity: int = 2):
    return parse_manual_account_snapshot({
        "schema_version": 1, "source_sheet": "아빠",
        "snapshot_date": "2026-02-03", "currency": "KRW",
        "holdings": [{
            "section": "ISA", "name": "Fixture Alpha", "ticker": "111111",
            "quantity": quantity, "average_cost": 100, "purchase_total": 100 * quantity,
        }],
    })


def _cache() -> ManualAccountMarketValueCache:
    return ManualAccountMarketValueCache(
        "아빠", "2026-02-03", manual_account_basis_sha256(_snapshot()),
        "2026-08-26T05:00:05+00:00",
        (ManualAccountMarketValueRow(
            "ISA", "111111", "AVAILABLE", "KRW", "111111.KS",
            "YAHOO_CHART_API", "XKRX", "KRW_PER_SHARE", Decimal("90"),
            "2026-08-26T05:00:00+00:00", "2026-08-26T05:00:05+00:00",
            "AS_RETRIEVED", Decimal("180"), Decimal("100"),
            Decimal("-20"), Decimal("-10"), None,
        ),),
        (ManualAccountSectionSummary(
            "ISA", "KRW", 1, 1, Decimal("180"), True,
        ),),
    )


def test_cache_round_trip_preserves_decimal_labels_and_negative_return() -> None:
    cache = _cache()
    payload = manual_account_market_value_cache_payload(cache)

    assert parse_manual_account_market_value_cache(payload) == cache
    assert payload["rows"][0]["price"] == "90"
    assert payload["rows"][0]["unrealized_pnl"] == "-20"
    assert payload["rows"][0]["return_pct"] == "-10"


def test_basis_digest_is_stable_and_changes_with_acquisition_facts() -> None:
    assert manual_account_basis_sha256(_snapshot()) == manual_account_basis_sha256(
        _snapshot()
    )
    assert manual_account_basis_sha256(_snapshot()) != manual_account_basis_sha256(
        _snapshot(quantity=3)
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "naive_clock", "unavailable_numeric", "unsafe_reason", "summary_total",
        "weight", "provider", "provider_symbol", "finality", "clock_order",
    ],
)
def test_cache_parser_rejects_malformed_or_unreconciled_values(mutation: str) -> None:
    payload = deepcopy(manual_account_market_value_cache_payload(_cache()))
    if mutation == "naive_clock":
        payload["generated_at"] = "2026-08-26T05:00:05"
    elif mutation == "unavailable_numeric":
        payload["rows"][0].update(status="UNAVAILABLE", reason="PROVIDER_EMPTY")
    elif mutation == "unsafe_reason":
        payload["rows"][0] = {
            **payload["rows"][0], "status": "UNAVAILABLE", "reason": "token=secret",
            **{field: None for field in (
                "provider_symbol", "provider", "exchange", "unit", "price", "as_of",
                "captured_at", "finality", "market_value", "weight_pct",
                "unrealized_pnl", "return_pct",
            )},
        }
        payload["section_summaries"][0].update(
            available_rows=0, market_value="0", complete=False,
        )
    elif mutation == "summary_total":
        payload["section_summaries"][0]["market_value"] = "181"
    elif mutation == "weight":
        payload["rows"][0]["weight_pct"] = "99"
    elif mutation == "provider":
        payload["rows"][0]["provider"] = "UNTRUSTED"
    elif mutation == "provider_symbol":
        payload["rows"][0]["provider_symbol"] = "111111.BAD"
    elif mutation == "finality":
        payload["rows"][0]["finality"] = "UNTRUSTED"
    else:
        payload["rows"][0]["captured_at"] = "2026-08-26T05:00:06+00:00"
    with pytest.raises((TypeError, ValueError)):
        parse_manual_account_market_value_cache(payload)


def test_direct_cache_revalidation_rejects_basis_digest_tamper() -> None:
    payload = manual_account_market_value_cache_payload(
        replace(_cache(), basis_sha256="x" * 64)
    )
    with pytest.raises(ValueError, match="digest"):
        parse_manual_account_market_value_cache(payload)
