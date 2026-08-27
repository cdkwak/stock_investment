from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class InvestorDataRole(StrEnum):
    OFFICIAL_CANONICAL = "OFFICIAL_CANONICAL"
    PROVIDER_DESCRIPTIVE_CROSS_CHECK = "PROVIDER_DESCRIPTIVE_CROSS_CHECK"


@dataclass(frozen=True)
class DerivativesInvestorAuthority:
    authority_id: str
    role: InvestorDataRole
    source: str
    permitted_layer: str
    operational_use: str
    predictive_use: str
    fallback_allowed: bool
    silent_merge_allowed: bool
    finality_status: str

    def __post_init__(self) -> None:
        if self.fallback_allowed or self.silent_merge_allowed:
            raise ValueError("derivatives investor authority must fail closed across providers")


OFFICIAL_KRX_DERIVATIVES_INVESTOR = DerivativesInvestorAuthority(
    authority_id="official_krx_screen_15007",
    role=InvestorDataRole.OFFICIAL_CANONICAL,
    source="krx_basic_statistics:15007",
    permitted_layer="NORMALIZED_REVIEWED_MANUAL_INPUT",
    operational_use="reviewed official canonical investor observations only",
    predictive_use="BLOCKED_PENDING_PUBLICATION_AND_FINALITY_POLICY",
    fallback_allowed=False,
    silent_merge_allowed=False,
    finality_status="MANUAL_FINALITY_GATE",
)


LS_T8462_DERIVATIVES_INVESTOR = DerivativesInvestorAuthority(
    authority_id="ls_t8462_provider_observation",
    role=InvestorDataRole.PROVIDER_DESCRIPTIVE_CROSS_CHECK,
    source="ls_openapi:t8462",
    permitted_layer="RAW_ONLY",
    operational_use="descriptive provider observation and official-source cross-check only",
    predictive_use="RESEARCH_ONLY_NON_PREDICTIVE",
    fallback_allowed=False,
    silent_merge_allowed=False,
    finality_status="OFFICIAL_REVISION_TIMING_UNRESOLVED",
)


DERIVATIVES_INVESTOR_AUTHORITIES = (
    OFFICIAL_KRX_DERIVATIVES_INVESTOR,
    LS_T8462_DERIVATIVES_INVESTOR,
)


__all__ = [
    "DERIVATIVES_INVESTOR_AUTHORITIES", "DerivativesInvestorAuthority",
    "InvestorDataRole", "LS_T8462_DERIVATIVES_INVESTOR",
    "OFFICIAL_KRX_DERIVATIVES_INVESTOR",
]
