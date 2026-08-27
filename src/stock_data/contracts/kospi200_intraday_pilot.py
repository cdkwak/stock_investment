"""Review-required KOSPI200 constituent intraday source-observation contract."""

from stock_data.contracts.base import ColumnContract, DatasetContract


RAW_BAR_TIME_POLICY = "PROVIDER_TIME_LABEL_PRESERVED_START_END_UNKNOWN"
RAW_REVISION_POLICY = "AS_RETRIEVED_HISTORICAL_REVISION_FREEZE_UNKNOWN"


LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT = DatasetContract(
    name="ls_t8412_kospi200_constituent_15m_pilot",
    version=1,
    status="review_required_not_registered",
    description=(
        "Exact-date LS t8412 native N-minute source observations for a bounded "
        "sample drawn from the same-date KOSPI200 membership. Provider time is "
        "preserved without asserting start-label or end-label semantics."
    ),
    source="LS OpenAPI /stock/chart t8412",
    layer="raw",
    storage_format="parquet",
    frequency="15-minute source observation",
    timezone="Asia/Seoul provider time with UTC capture time",
    primary_key=("provider", "symbol", "market_date", "provider_time"),
    sort_key=("market_date", "symbol", "provider_time"),
    partition_by=("year",),
    columns=(
        ColumnContract("market_date", "date32", False),
        ColumnContract("membership_observation_date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("provider_symbol", "string", False),
        ColumnContract(
            "provider_time", "string", False,
            description="Source time label; start/end-bar meaning remains unresolved.",
        ),
        ColumnContract(
            "bar_time_policy", "string", False,
            description="Typed Raw-only policy; provider_time is not reinterpreted.",
        ),
        ColumnContract("interval_minutes", "int64", False, unit="minutes"),
        ColumnContract("source_session_start", "string", False),
        ColumnContract("source_session_end", "string", False),
        ColumnContract("open", "int64", False, unit="provider_native_price"),
        ColumnContract("high", "int64", False, unit="provider_native_price"),
        ColumnContract("low", "int64", False, unit="provider_native_price"),
        ColumnContract("close", "int64", False, unit="provider_native_price"),
        ColumnContract(
            "volume", "int64", False, unit="provider_native_volume",
            description="LS jdiff_vol preserved without an unsupported shares claim.",
        ),
        ColumnContract("adjustment_code", "int64", False),
        ColumnContract("adjustment_rate", "float64", False),
        ColumnContract("provider", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("captured_at", "timestamp[us, UTC]", False),
        ColumnContract("source_sha256", "string", False),
        ColumnContract(
            "revision_policy", "string", False,
            description="Typed Raw-only as-retrieved policy; no revision freeze is claimed.",
        ),
        ColumnContract("finality_status", "string", False),
        ColumnContract("pit_status", "string", False),
    ),
)


__all__ = [
    "LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT",
    "RAW_BAR_TIME_POLICY",
    "RAW_REVISION_POLICY",
]
