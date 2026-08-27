from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DerivativesPriceAuthority:
    """Current authority boundary for the KOSPI200 price daily chain."""

    authority_id: str
    source: str
    operations: tuple[str, ...]
    products: tuple[str, ...]
    session: str
    source_date_field: str
    source_date_semantics: str
    permission_status: str
    finality_status: str
    observation_calendar: str
    provider_availability_policy: str
    expected_lag_policy: str
    finality_policy: str
    fallback_allowed: bool
    silent_merge_allowed: bool

    @property
    def live_validation_ready(self) -> bool:
        return (
            self.permission_status == "ACTIVE_EXACT_DATE_OPERATION_APPROVED"
            and self.finality_status == "EXPLICIT_FINAL_DATE_RULE_APPROVED"
        )

    def __post_init__(self) -> None:
        if self.fallback_allowed or self.silent_merge_allowed:
            raise ValueError("derivatives price authority must fail closed across sources")


DATA_GO_KR_KOSPI200_DERIVATIVES_PRICE = DerivativesPriceAuthority(
    authority_id="data_go_kr_kospi200_derivatives_price",
    source="data_go_kr:1160100/GetDerivativeProductInfoService",
    operations=(
        "getStockFuturesPriceInfo",
        "getOptionsPriceInfo",
    ),
    products=(
        "파생 선물 코스피200 (주간)",
        "파생 옵션 코스피200",
    ),
    session="KRX_REGULAR_SESSION",
    source_date_field="basDt",
    source_date_semantics="EXACT_REQUESTED_TRADING_DATE_ONLY",
    permission_status="ACTIVE_EXACT_DATE_OPERATION_APPROVED",
    finality_status="EXPLICIT_FINAL_DATE_RULE_APPROVED",
    observation_calendar="XKRX",
    provider_availability_policy="EXACT_BASDT_AFTER_COMPLETED_SUCCESSOR_XKRX_SESSION",
    expected_lag_policy="T_PLUS_1_COMPLETED_XKRX_SESSION",
    finality_policy="TARGET_REQUIRES_A_LATER_COMPLETED_XKRX_SESSION",
    fallback_allowed=False,
    silent_merge_allowed=False,
)


__all__ = [
    "DATA_GO_KR_KOSPI200_DERIVATIVES_PRICE",
    "DerivativesPriceAuthority",
]
