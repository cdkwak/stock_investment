"""Typed U.S. option P/C views with market and per-symbol scopes kept separate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from stock_data.contracts.us_option_pcr import CBOE_PUT_CALL_SCOPE_POLICIES
from stock_data.providers.yahoo_symbol_options import (
    ALL_PILOT_SYMBOLS,
    SymbolOptionPCRStatus,
    YahooSymbolVolumePCR,
)


class USOptionPCRDisplayState(str, Enum):
    LICENSE_BLOCKED = "LICENSE_BLOCKED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    VALUE = "VALUE"
    PRICE_ONLY = "PRICE_ONLY"
    SUPPRESSED = "SUPPRESSED"


@dataclass(frozen=True)
class USOptionPCRScopeView:
    scope_id: str
    label: str
    group: str
    value: float | None
    display_state: USOptionPCRDisplayState
    reason: str
    source_scope: str
    finality_status: str
    usage_status: str
    captured_at_kst: str | None = None
    latest_contract_trade_at_kst: str | None = None
    expiry_count: int = 0

    @property
    def displays_value(self) -> bool:
        return self.display_state is USOptionPCRDisplayState.VALUE and self.value is not None


_FUTURE_EXACT_SCOPES = (
    ("NASDAQ", "Nasdaq 옵션 P/C", "NASDAQ", "Nasdaq market/underlying scope is not defined by an approved exact source."),
    ("QQQ", "QQQ 옵션 P/C", "NASDAQ", "Cboe ETP aggregate is not a QQQ-specific ratio."),
    ("NDX", "NDX 옵션 P/C", "NASDAQ", "Cboe Index aggregate and SPX+SPXW are not an NDX-specific ratio."),
    ("SOXX", "SOXX 옵션 P/C", "SOXX", "Cboe ETP aggregate is not a SOXX-specific ratio."),
)


def current_us_option_pcr_scope_views() -> tuple[USOptionPCRScopeView, ...]:
    """Expose stable scopes without reading or retaining license-blocked values."""
    cboe = tuple(
        USOptionPCRScopeView(
            scope_id=policy.scope_id,
            label=policy.label,
            group="CBOE_MARKET_STATISTICS",
            value=None,
            display_state=USOptionPCRDisplayState.LICENSE_BLOCKED,
            reason=(
                "Cboe 사전 승인·서명 라이선스와 보존/표시 권한이 확인되지 않아 "
                "숫자를 표시하지 않습니다."
            ),
            source_scope=policy.meaning,
            finality_status="PUBLICATION_AND_REVISION_TIME_UNRESOLVED",
            usage_status="PRIOR_WRITTEN_APPROVAL_AND_SIGNED_LICENSE_REQUIRED",
        )
        for policy in CBOE_PUT_CALL_SCOPE_POLICIES.values()
    )
    unavailable = tuple(
        USOptionPCRScopeView(
            scope_id=scope_id,
            label=label,
            group=group,
            value=None,
            display_state=USOptionPCRDisplayState.SOURCE_UNAVAILABLE,
            reason=reason,
            source_scope="EXACT_UNDERLYING_OR_MARKET_SCOPE_REQUIRED",
            finality_status="NOT_APPLICABLE_WITHOUT_SOURCE",
            usage_status="NO_APPROVED_SOURCE",
        )
        for scope_id, label, group, reason in _FUTURE_EXACT_SCOPES
    )
    return cboe + unavailable


def yahoo_symbol_option_pcr_scope_views(
    observations: tuple[YahooSymbolVolumePCR, ...] = (),
) -> tuple[USOptionPCRScopeView, ...]:
    """Project all pilot symbols without filling, averaging, or cross-symbol totals."""
    indexed = {row.symbol: row for row in observations}
    if len(indexed) != len(observations):
        raise ValueError("duplicate Yahoo per-symbol P/C observation")
    unknown = set(indexed).difference(ALL_PILOT_SYMBOLS)
    if unknown:
        raise ValueError(f"Yahoo per-symbol P/C observation outside pilot scope: {sorted(unknown)}")
    views: list[USOptionPCRScopeView] = []
    for symbol in ALL_PILOT_SYMBOLS:
        row = indexed.get(symbol)
        status = row.status if row else SymbolOptionPCRStatus.NO_RETAINED_CHAIN
        display_state = (
            USOptionPCRDisplayState.VALUE
            if status is SymbolOptionPCRStatus.AVAILABLE
            else USOptionPCRDisplayState.PRICE_ONLY
            if status is SymbolOptionPCRStatus.PRICE_ONLY
            else USOptionPCRDisplayState.SUPPRESSED
        )
        views.append(USOptionPCRScopeView(
            scope_id=f"YAHOO_{symbol}",
            label=f"{symbol} 옵션 거래량 P/C",
            group="YAHOO_PER_SYMBOL_RESEARCH",
            value=row.value if row else None,
            display_state=display_state,
            reason=row.reason if row else "No retained populated Yahoo chain evidence.",
            source_scope=f"{symbol} ONLY / SELECTED EXPLICIT EXPIRIES / REGULAR CONTRACTS ONLY",
            finality_status=(
                row.provider_timestamp_status if row else "NO_RETAINED_TIMESTAMP"
            ),
            usage_status=(
                "DASHBOARD_RESEARCH_ONLY_NOT_BACKTEST"
                if row and row.displays_value else status.value
            ),
            captured_at_kst=(row.captured_at_kst.isoformat() if row and row.captured_at_kst else None),
            latest_contract_trade_at_kst=(
                row.latest_contract_trade_at_kst.isoformat()
                if row and row.latest_contract_trade_at_kst else None
            ),
            expiry_count=row.expiry_count if row else 0,
        ))
    return tuple(views)


__all__ = [
    "USOptionPCRDisplayState", "USOptionPCRScopeView",
    "current_us_option_pcr_scope_views", "yahoo_symbol_option_pcr_scope_views",
]
