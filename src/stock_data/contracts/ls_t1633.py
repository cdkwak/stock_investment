from __future__ import annotations

from dataclasses import dataclass

from stock_data.contracts.base import ColumnContract, DatasetContract


LS_T1633_AMOUNT_MULTIPLIER = 1_000_000
LS_T1633_QUANTITY_MULTIPLIER = 1_000
LS_T1633_FINALITY_POLICY = "EMPIRICAL_NEXT_XKRX_SESSION_T_PLUS_1"


@dataclass(frozen=True)
class LST1633Authority:
    source: str
    operation: str
    endpoint: str
    amount_selector: str
    quantity_selector: str
    permission_status: str
    finality_status: str

    @property
    def live_ready(self) -> bool:
        return (
            self.permission_status == "ACTIVE_EXACT_DATE_OPERATION_APPROVED"
            and self.finality_status == "EXPLICIT_FINAL_DATE_RULE_APPROVED"
        )


LS_T1633_AUTHORITY = LST1633Authority(
    source="ls_open_api",
    operation="t1633",
    endpoint="/stock/program",
    amount_selector="0",
    quantity_selector="1",
    permission_status="ACTIVE_EXACT_DATE_OPERATION_APPROVED",
    finality_status="EXPLICIT_FINAL_DATE_RULE_APPROVED",
)


def _metric_columns(suffix: str, unit: str) -> tuple[ColumnContract, ...]:
    return tuple(
        ColumnContract(f"{group}_{side}_{suffix}", "int64", False, unit)
        for group in ("total", "arbitrage", "non_arbitrage")
        for side in ("buy", "sell", "net")
    )


LS_T1633_PROGRAM_TRADING_DAILY = DatasetContract(
    name="ls_t1633_program_trading_daily",
    version=1,
    status="operational_with_empirical_finality",
    description=(
        "Provider-bounded KOSPI/KOSDAQ daily program-trading totals from LS t1633. "
        "Amounts and quantities are converted from empirically confirmed LS source "
        "units. Operation uses a reviewed fail-closed next-XKRX-session T+1 minimum; "
        "predictive revision safety is not implied."
    ),
    source="ls_open_api:t1633",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market"),
    sort_key=("date", "market"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        *_metric_columns("amount", "KRW"),
        *_metric_columns("volume", "shares"),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("source_market_code", "string", False),
        ColumnContract("source_amount_selector", "string", False),
        ColumnContract("source_quantity_selector", "string", False),
        ColumnContract("source_session", "string", False),
        ColumnContract("source_exchange_scope", "string", False),
        ColumnContract("source_date", "string", False),
        ColumnContract("collected_at", "timestamp[us, UTC]", False),
        ColumnContract("amount_landing_sha256", "string", False),
        ColumnContract("quantity_landing_sha256", "string", False),
        ColumnContract("unit_evidence", "string", False),
        ColumnContract("finality_status", "string", False),
    ),
)


__all__ = [
    "LS_T1633_AMOUNT_MULTIPLIER",
    "LS_T1633_AUTHORITY",
    "LS_T1633_FINALITY_POLICY",
    "LS_T1633_PROGRAM_TRADING_DAILY",
    "LS_T1633_QUANTITY_MULTIPLIER",
    "LST1633Authority",
]
