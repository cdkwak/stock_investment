from dataclasses import replace

import pytest

from stock_data.contracts.vix_futures import (
    VIXFuturesRouteStatus,
    VIXFuturesSeriesKind,
    YAHOO_CFE_VIX_FUTURES_15M,
    validate_vix_futures_route_contract,
)


def test_current_yahoo_vix_futures_route_is_explicitly_identity_unverified() -> None:
    contract = YAHOO_CFE_VIX_FUTURES_15M
    validate_vix_futures_route_contract(contract)
    assert contract.status is VIXFuturesRouteStatus.UNAVAILABLE_IDENTITY_UNVERIFIED
    assert contract.provider_symbol is None
    assert contract.exchange == "CFE"
    assert contract.exchange_product_root == "VX"
    assert contract.source_timezone == "America/Chicago"
    assert "YAHOO_PROVIDER_SYMBOL_NOT_EVIDENCED" in contract.unresolved_rules
    assert "EXACT_EXPIRY_OR_PROVIDER_ROLL_POLICY_NOT_EVIDENCED" in contract.unresolved_rules


def test_spot_vix_symbol_cannot_be_accepted_as_vix_futures() -> None:
    invalid = replace(
        YAHOO_CFE_VIX_FUTURES_15M,
        status=VIXFuturesRouteStatus.ACCEPTED_LISTED_CONTRACT,
        provider_symbol="^VIX",
        series_kind=VIXFuturesSeriesKind.LISTED_CONTRACT,
        contract_symbol="VX/U6",
        expiry="2026-09-16",
        front_month_selection_policy="EXACT_AS_OF_SELECTION",
        session_contract_status="ACTIVE",
        unresolved_rules=(),
    )
    with pytest.raises(ValueError, match="non-spot"):
        validate_vix_futures_route_contract(invalid)


def test_listed_and_continuous_routes_require_different_identity_fields() -> None:
    listed_without_expiry = replace(
        YAHOO_CFE_VIX_FUTURES_15M,
        status=VIXFuturesRouteStatus.ACCEPTED_LISTED_CONTRACT,
        provider_symbol="PROVIDER_LISTED_EXAMPLE",
        series_kind=VIXFuturesSeriesKind.LISTED_CONTRACT,
        contract_symbol="VX/EXAMPLE",
        front_month_selection_policy="EXACT_AS_OF_SELECTION",
        session_contract_status="ACTIVE", unresolved_rules=(),
    )
    with pytest.raises(ValueError, match="symbol, expiry"):
        validate_vix_futures_route_contract(listed_without_expiry)

    continuous_with_expiry = replace(
        YAHOO_CFE_VIX_FUTURES_15M,
        status=VIXFuturesRouteStatus.ACCEPTED_PROVIDER_CONTINUOUS,
        provider_symbol="PROVIDER_CONTINUOUS_EXAMPLE",
        series_kind=VIXFuturesSeriesKind.PROVIDER_CONTINUOUS,
        expiry="2026-09-16", roll_policy="PROVIDER_DOCUMENTED_ROLL",
        session_contract_status="ACTIVE", unresolved_rules=(),
    )
    with pytest.raises(ValueError, match="roll policy only"):
        validate_vix_futures_route_contract(continuous_with_expiry)
