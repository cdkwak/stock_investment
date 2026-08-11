from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


def _column(
    name: str,
    dtype: str,
    *,
    nullable: bool = False,
    unit: str | None = None,
    description: str | None = None,
) -> ColumnContract:
    return ColumnContract(name, dtype, nullable, unit, description)


KR_KOSPI200_FUTURES_NEAREST_LISTED_DAILY = DatasetContract(
    name="kr_kospi200_futures_nearest_listed_daily",
    version=1,
    status="active",
    description=(
        "Daily nearest source-listed KOSPI200 outright futures observation by "
        "provider segment and source session. This is not a back-adjusted or "
        "expiry-calendar continuous contract."
    ),
    source="kr_kospi200_futures_provider_bridge_daily+normalized_provider_rows",
    layer="derived",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "bridge_segment", "session"),
    sort_key=("date", "bridge_segment", "session"),
    partition_by=("year",),
    columns=(
        _column("date", "date32"),
        _column("bridge_segment", "string"),
        _column("session", "string"),
        _column("source_session_label", "string", nullable=True),
        _column("source_contract_code", "string"),
        _column("source_name", "string"),
        _column("maturity_month", "string"),
        _column(
            "expiry_date",
            "date32",
            nullable=True,
            description="Always null: retained sources do not supply exact expiry.",
        ),
        _column("expiry_status", "string"),
        _column("selection_rule", "string"),
        _column("contract_transition", "bool"),
        _column("close", "float64", unit="source_native_price"),
        _column(
            "settlement_price",
            "float64",
            nullable=True,
            unit="source_native_price",
        ),
        _column("spot_value", "float64", unit="source_native_price"),
        _column(
            "settlement_basis",
            "float64",
            nullable=True,
            unit="source_native_price_difference",
            description=(
                "settlement_price minus same-row spot_value for regular sessions; "
                "null when session alignment is not verified."
            ),
        ),
        _column("basis_status", "string"),
        _column("price_unit_status", "string"),
        _column("volume", "int64", nullable=True),
        _column("open_interest", "int64", nullable=True),
        _column("source", "string"),
        _column("source_operation", "string"),
        _column("input_bridge_dataset", "string"),
        _column("input_normalized_dataset", "string"),
        _column("predictive_use_status", "string"),
    ),
)


