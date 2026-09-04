from dataclasses import dataclass

from stock_data.contracts.base import ColumnContract, DatasetContract


_CONTRACT_ONLY_STATUS = "contract_only_no_entitlement"
_UTC_TIMESTAMP = "timestamp[us, UTC]"


CBOE_DAILY_PCR_DAILY = DatasetContract(
    name="cboe_daily_pcr_daily", version=1,
    status="active_personal_display_only",
    description=(
        "Once-daily Cboe venue-scoped option product-group put/call counts and "
        "ratios for personal non-commercial local display only; never a U.S.-wide total."
    ),
    source="cboe_daily_market_statistics_public", layer="normalized",
    storage_format="parquet", frequency="daily", timezone="America/New_York",
    primary_key=("date", "scope"), sort_key=("date", "scope"),
    partition_by=("year",), columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("scope", "string", False),
        ColumnContract("call_volume", "int64", False, "contracts"),
        ColumnContract("put_volume", "int64", False, "contracts"),
        ColumnContract("volume_pcr", "float64", True, "ratio"),
        ColumnContract("call_oi", "int64", True, "contracts"),
        ColumnContract("put_oi", "int64", True, "contracts"),
        ColumnContract("oi_pcr", "float64", True, "ratio"),
        ColumnContract("provider", "string", False),
        ColumnContract("retrieved_at", _UTC_TIMESTAMP, False),
    ),
)


@dataclass(frozen=True)
class CboePutCallScopePolicy:
    scope_id: str
    label: str
    scope_kind: str
    parent_scope_id: str | None
    official_page_label: str
    meaning: str


CBOE_PUT_CALL_SCOPE_POLICIES = {
    policy.scope_id: policy
    for policy in (
        CboePutCallScopePolicy(
            "CBOE_TOTAL", "Cboe 전체 상품 P/C", "VENUE_AGGREGATE", None,
            "TOTAL PUT/CALL RATIO",
            "Cboe daily statistics page SUM OF ALL PRODUCTS; not the entire U.S. options market.",
        ),
        CboePutCallScopePolicy(
            "CBOE_INDEX", "Cboe 지수옵션 P/C", "PRODUCT_GROUP", "CBOE_TOTAL",
            "INDEX PUT/CALL RATIO", "Index options within the Cboe-reported scope.",
        ),
        CboePutCallScopePolicy(
            "CBOE_ETP", "Cboe ETP 옵션 P/C", "PRODUCT_GROUP", "CBOE_TOTAL",
            "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO",
            "Exchange-traded-product options within the Cboe-reported scope; not QQQ or SOXX specifically.",
        ),
        CboePutCallScopePolicy(
            "CBOE_EQUITY", "Cboe 개별주식 옵션 P/C", "PRODUCT_GROUP", "CBOE_TOTAL",
            "EQUITY PUT/CALL RATIO",
            "Single-stock equity-option flow on the Cboe Options Exchange (C1).",
        ),
        CboePutCallScopePolicy(
            "CBOE_VIX", "Cboe VIX 옵션 P/C", "EXACT_PRODUCT_FAMILY", "CBOE_INDEX",
            "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO",
            "VIX option family only; distinct from the VIX index level.",
        ),
        CboePutCallScopePolicy(
            "CBOE_SPX_SPXW", "Cboe SPX+SPXW 옵션 P/C", "COMBINED_ROOTS", "CBOE_INDEX",
            "SPX + SPXW PUT/CALL RATIO",
            "Combined SPX and SPXW option roots; not an all-S&P-options or all-index proxy.",
        ),
    )
}


CBOE_DAILY_OPTION_PCR_OBSERVATION = DatasetContract(
    name="cboe_daily_option_pcr_observation",
    version=1,
    status="contract_only_license_blocked",
    description=(
        "Unregistered schema for six separately identified Cboe Daily Market Statistics "
        "volume put/call scopes. It grants no website extraction, retention, automation, "
        "publication, or display authority."
    ),
    source="Cboe Daily Market Statistics official page",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="America/Chicago publication time unresolved",
    primary_key=("trade_date", "scope_id", "captured_at_utc"),
    sort_key=("trade_date", "scope_id", "captured_at_utc"),
    partition_by=("scope_id", "year"),
    columns=(
        ColumnContract("trade_date", "date32", False),
        ColumnContract("scope_id", "string", False),
        ColumnContract("scope_kind", "string", False),
        ColumnContract("parent_scope_id", "string", True),
        ColumnContract("official_page_label", "string", False),
        ColumnContract("venue_scope", "string", False),
        ColumnContract("call_volume", "int64", False, "contracts"),
        ColumnContract("put_volume", "int64", False, "contracts"),
        ColumnContract("total_volume", "int64", False, "contracts"),
        ColumnContract("published_volume_pcr", "float64", False, "ratio"),
        ColumnContract("call_open_interest", "int64", True, "contracts"),
        ColumnContract("put_open_interest", "int64", True, "contracts"),
        ColumnContract("total_open_interest", "int64", True, "contracts"),
        ColumnContract("captured_at_utc", _UTC_TIMESTAMP, False),
        ColumnContract("landing_sha256", "string", False),
        ColumnContract("source_scope_status", "string", False),
        ColumnContract("finality_status", "string", False),
        ColumnContract("usage_status", "string", False),
        ColumnContract("pit_status", "string", False),
    ),
)


ORATS_OPTION_CORE_OBSERVATION = DatasetContract(
    name="orats_option_core_observation",
    version=1,
    status=_CONTRACT_ONLY_STATUS,
    description=(
        "Provider-specific ORATS daily core observations retained by capture. This is a "
        "contract-only schema: it grants no entitlement, collection, normalization, or "
        "publication authority."
    ),
    source="orats_delayed_cores",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="America/New_York",
    primary_key=("trade_date", "provider_ticker", "captured_at_utc"),
    sort_key=("trade_date", "provider_ticker", "captured_at_utc"),
    partition_by=("provider_ticker", "year"),
    columns=(
        ColumnContract("trade_date", "date32", False),
        ColumnContract("provider_ticker", "string", False),
        ColumnContract("asset_type", "string", False),
        ColumnContract("call_volume", "int64", True, "contracts"),
        ColumnContract("put_volume", "int64", True, "contracts"),
        ColumnContract("call_open_interest", "int64", True, "contracts"),
        ColumnContract("put_open_interest", "int64", True, "contracts"),
        ColumnContract("provider_updated_at_utc", _UTC_TIMESTAMP, True),
        ColumnContract("provider_snapshot_at_utc", _UTC_TIMESTAMP, True),
        ColumnContract("captured_at_utc", _UTC_TIMESTAMP, False),
        ColumnContract("landing_sha256", "string", False),
        ColumnContract("observation_status", "string", False),
        ColumnContract("source", "string", False),
    ),
)


US_UNDERLYING_OPTION_PCR_DAILY = DatasetContract(
    name="us_underlying_option_pcr_daily",
    version=1,
    status=_CONTRACT_ONLY_STATUS,
    description=(
        "Provider-neutral daily U.S. underlying option volume and open-interest put/call "
        "ratios selected from one licensed provider observation without fallback, source "
        "averaging, or cross-underlying aggregation."
    ),
    source="orats_option_core_observation",
    layer="derived",
    storage_format="parquet",
    frequency="daily",
    timezone="America/New_York",
    primary_key=("trade_date", "underlying", "scope", "session"),
    sort_key=("trade_date", "underlying", "scope", "session"),
    partition_by=("underlying", "year"),
    columns=(
        ColumnContract("trade_date", "date32", False),
        ColumnContract("underlying", "string", False),
        ColumnContract("underlying_type", "string", False),
        ColumnContract("provider", "string", False),
        ColumnContract("provider_ticker", "string", False),
        ColumnContract("scope", "string", False),
        ColumnContract("root_scope_status", "string", False),
        ColumnContract("session", "string", False),
        ColumnContract("call_volume", "int64", True, "contracts"),
        ColumnContract("put_volume", "int64", True, "contracts"),
        ColumnContract("volume_pcr", "float64", True, "ratio"),
        ColumnContract("volume_finality_status", "string", False),
        ColumnContract("call_open_interest", "int64", True, "contracts"),
        ColumnContract("put_open_interest", "int64", True, "contracts"),
        ColumnContract("open_interest_pcr", "float64", True, "ratio"),
        ColumnContract("open_interest_timing_status", "string", False),
        ColumnContract("selected_capture_at_utc", _UTC_TIMESTAMP, False),
        ColumnContract("available_at_utc", _UTC_TIMESTAMP, False),
        ColumnContract("input_dataset", "string", False),
        ColumnContract("landing_sha256", "string", False),
        ColumnContract("revision_status", "string", False),
        ColumnContract("observation_status", "string", False),
        ColumnContract("pit_status", "string", False),
    ),
)


DASHBOARD_US_OPTION_PCR_DAILY = DatasetContract(
    name="dashboard_us_option_pcr_daily",
    version=1,
    status=_CONTRACT_ONLY_STATUS,
    description=(
        "Stable descriptive Dashboard projection of provider-neutral U.S. underlying "
        "option put/call ratios. Freshness is computed at read time from retained dates "
        "and an approved publication policy; entitlement and display gate results are "
        "persisted without granting collection or publication authority."
    ),
    source="us_underlying_option_pcr_daily",
    layer="published",
    storage_format="parquet",
    frequency="daily",
    timezone="America/New_York",
    primary_key=("trade_date", "underlying", "scope", "session"),
    sort_key=("trade_date", "underlying", "scope", "session"),
    partition_by=("underlying", "year"),
    columns=(
        ColumnContract("trade_date", "date32", False),
        ColumnContract("underlying", "string", False),
        ColumnContract("underlying_type", "string", False),
        ColumnContract("scope", "string", False),
        ColumnContract("root_scope_status", "string", False),
        ColumnContract("session", "string", False),
        ColumnContract("call_volume", "int64", True, "contracts"),
        ColumnContract("put_volume", "int64", True, "contracts"),
        ColumnContract("volume_pcr", "float64", True, "ratio"),
        ColumnContract("volume_finality_status", "string", False),
        ColumnContract("call_open_interest", "int64", True, "contracts"),
        ColumnContract("put_open_interest", "int64", True, "contracts"),
        ColumnContract("open_interest_pcr", "float64", True, "ratio"),
        ColumnContract("open_interest_timing_status", "string", False),
        ColumnContract("available_at_utc", _UTC_TIMESTAMP, False),
        ColumnContract("provider", "string", False),
        ColumnContract("revision_status", "string", False),
        ColumnContract("observation_status", "string", False),
        ColumnContract("pit_status", "string", False),
        ColumnContract("input_dataset", "string", False),
        ColumnContract("landing_sha256", "string", False),
        ColumnContract("entitlement_status", "string", False),
        ColumnContract("display_status", "string", False),
        ColumnContract("blocked_reason", "string", True),
        ColumnContract("projection_version", "string", False),
    ),
)


US_OPTION_PCR_CONTRACTS = (
    ORATS_OPTION_CORE_OBSERVATION,
    US_UNDERLYING_OPTION_PCR_DAILY,
    DASHBOARD_US_OPTION_PCR_DAILY,
)


__all__ = [
    "CBOE_DAILY_PCR_DAILY",
    "CBOE_DAILY_OPTION_PCR_OBSERVATION",
    "CBOE_PUT_CALL_SCOPE_POLICIES",
    "CboePutCallScopePolicy",
    "DASHBOARD_US_OPTION_PCR_DAILY",
    "ORATS_OPTION_CORE_OBSERVATION",
    "US_OPTION_PCR_CONTRACTS",
    "US_UNDERLYING_OPTION_PCR_DAILY",
]
