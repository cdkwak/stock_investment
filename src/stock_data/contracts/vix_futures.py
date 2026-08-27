"""Typed fail-closed source contract for a future Yahoo/CFE VX route."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VIXFuturesRouteStatus(StrEnum):
    UNAVAILABLE_IDENTITY_UNVERIFIED = "UNAVAILABLE_IDENTITY_UNVERIFIED"
    ACCEPTED_LISTED_CONTRACT = "ACCEPTED_LISTED_CONTRACT"
    ACCEPTED_PROVIDER_CONTINUOUS = "ACCEPTED_PROVIDER_CONTINUOUS"


class VIXFuturesSeriesKind(StrEnum):
    LISTED_CONTRACT = "LISTED_CONTRACT"
    PROVIDER_CONTINUOUS = "PROVIDER_CONTINUOUS"


@dataclass(frozen=True)
class VIXFuturesRouteContract:
    route_id: str
    status: VIXFuturesRouteStatus
    provider: str
    provider_symbol: str | None
    exchange: str
    exchange_product_root: str
    source_timezone: str
    interval: str
    series_kind: VIXFuturesSeriesKind | None
    contract_symbol: str | None
    expiry: str | None
    front_month_selection_policy: str | None
    roll_policy: str | None
    session_contract_status: str
    source_evidence: tuple[str, ...]
    unresolved_rules: tuple[str, ...]


YAHOO_CFE_VIX_FUTURES_15M = VIXFuturesRouteContract(
    route_id="YAHOO_CFE_VIX_FUTURES_15M",
    status=VIXFuturesRouteStatus.UNAVAILABLE_IDENTITY_UNVERIFIED,
    provider="Yahoo",
    provider_symbol=None,
    exchange="CFE",
    exchange_product_root="VX",
    source_timezone="America/Chicago",
    interval="15m",
    series_kind=None,
    contract_symbol=None,
    expiry=None,
    front_month_selection_policy=None,
    roll_policy=None,
    session_contract_status="EVIDENCE_REQUIRED",
    source_evidence=(
        "https://www.cboe.com/tradable-products/vix/vix-futures/",
        "https://www.cboe.com/tradable-products/vix/vix-futures/specifications",
        "https://www.cboe.com/tradable-products/vix/vix-futures/vendor-symbols/",
        "https://www.cboe.com/about/hours/us-futures",
        "https://finance.yahoo.com/quote/%5EVIX/",
    ),
    unresolved_rules=(
        "YAHOO_PROVIDER_SYMBOL_NOT_EVIDENCED",
        "YAHOO_RETURNED_IDENTITY_TO_CFE_VX_EQUIVALENCE_NOT_EVIDENCED",
        "LISTED_CONTRACT_OR_PROVIDER_CONTINUOUS_KIND_NOT_EVIDENCED",
        "EXACT_EXPIRY_OR_PROVIDER_ROLL_POLICY_NOT_EVIDENCED",
        "YAHOO_BAR_TO_CFE_TRADE_DATE_AND_SESSION_MAPPING_NOT_EVIDENCED",
        "CFE_COMPLETED_15M_BAR_MAPPING_NOT_ACCEPTED",
    ),
)


def validate_vix_futures_route_contract(contract: VIXFuturesRouteContract) -> None:
    """Reject a route that could silently turn a lookalike ticker into VX futures."""
    if (
        contract.exchange != "CFE"
        or contract.exchange_product_root != "VX"
        or contract.source_timezone != "America/Chicago"
        or contract.interval != "15m"
    ):
        raise ValueError("VIX futures exchange identity or native interval differs")
    if contract.status is VIXFuturesRouteStatus.UNAVAILABLE_IDENTITY_UNVERIFIED:
        if any((
            contract.provider_symbol,
            contract.series_kind,
            contract.contract_symbol,
            contract.expiry,
            contract.front_month_selection_policy,
            contract.roll_policy,
        )):
            raise ValueError("unverified VIX futures route must not claim identity or roll fields")
        if not contract.unresolved_rules:
            raise ValueError("unverified VIX futures route must retain exact unresolved rules")
        return
    if not contract.provider_symbol or contract.provider_symbol == "^VIX":
        raise ValueError("accepted VIX futures route requires a non-spot provider symbol")
    if contract.session_contract_status != "ACTIVE":
        raise ValueError("accepted VIX futures route requires an active CFE session contract")
    if contract.series_kind is VIXFuturesSeriesKind.LISTED_CONTRACT:
        if not all((
            contract.contract_symbol,
            contract.expiry,
            contract.front_month_selection_policy,
        )) or contract.roll_policy is not None:
            raise ValueError("listed VX identity requires symbol, expiry, and selection policy")
    elif contract.series_kind is VIXFuturesSeriesKind.PROVIDER_CONTINUOUS:
        if not contract.roll_policy or contract.contract_symbol or contract.expiry:
            raise ValueError("continuous VX identity requires provider roll policy only")
    else:
        raise ValueError("accepted VIX futures route requires an exact series kind")
    if contract.unresolved_rules:
        raise ValueError("accepted VIX futures route retains unresolved identity rules")


__all__ = [
    "VIXFuturesRouteContract", "VIXFuturesRouteStatus", "VIXFuturesSeriesKind",
    "YAHOO_CFE_VIX_FUTURES_15M", "validate_vix_futures_route_contract",
]
