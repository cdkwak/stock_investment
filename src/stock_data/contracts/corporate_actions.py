"""Contract-only schemas for auditable equity corporate-action adjustment.

These contracts intentionally are not registered in the runtime Dataset
Registry.  No retained source observation currently satisfies the required
event identity, revision lineage, availability, and economic-term fields.
They define the boundary a future bounded source/promotion operation must pass;
they do not authorize that operation or reinterpret retained dividend/issuance
observations as adjustment factors.
"""

from stock_data.contracts.base import ColumnContract, DatasetContract


_STATUS = "contract_only_canonical_identity_blocked"
_UTC = "timestamp[us, UTC]"


EQUITY_CORPORATE_ACTION_EVENT_VERSION = DatasetContract(
    name="equity_corporate_action_event_version",
    version=1,
    status=_STATUS,
    description=(
        "Versioned, PIT-visible corporate-action events with exact security identity, "
        "explicit revision lineage, and verified economic terms. Source observations "
        "remain separate and are not eligible merely because they resemble an event."
    ),
    source="official_source_unselected_no_current_observation_eligible",
    layer="normalized",
    storage_format="parquet",
    frequency="event",
    timezone="UTC",
    primary_key=("event_id", "version_number"),
    sort_key=("security_id", "effective_date", "same_date_sequence", "event_id", "version_number"),
    partition_by=("effective_year",),
    columns=(
        ColumnContract("event_id", "string", False),
        ColumnContract("event_version_id", "string", False),
        ColumnContract("version_number", "int32", False),
        ColumnContract("revises_event_version_id", "string", True),
        ColumnContract("revision_state", "string", False),
        ColumnContract("security_id", "string", False),
        ColumnContract("security_id_scheme", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("action_type", "string", False),
        ColumnContract("source_filing_date", "date32", True),
        ColumnContract("announcement_date", "date32", True),
        ColumnContract("decision_date", "date32", True),
        ColumnContract("record_date", "date32", True),
        ColumnContract("ex_date", "date32", True),
        ColumnContract("effective_date", "date32", False),
        ColumnContract("payment_date", "date32", True),
        ColumnContract("listing_date", "date32", True),
        ColumnContract("same_date_sequence", "int32", False),
        ColumnContract("price_factor", "decimal128(28,12)", True, "multiplier"),
        ColumnContract("volume_factor", "decimal128(28,12)", True, "multiplier"),
        ColumnContract("turnover_factor", "decimal128(28,12)", True, "multiplier"),
        ColumnContract("cash_amount", "decimal128(28,8)", True, "currency_per_share"),
        ColumnContract("currency", "string", True),
        ColumnContract("factor_method", "string", True),
        ColumnContract("predecessor_security_id", "string", True),
        ColumnContract("successor_security_id", "string", True),
        ColumnContract("predecessor_symbol", "string", True),
        ColumnContract("successor_symbol", "string", True),
        ColumnContract("source", "string", False),
        ColumnContract("source_event_id", "string", False),
        ColumnContract("source_observation_id", "string", False),
        ColumnContract("available_at_utc", _UTC, False),
        ColumnContract("retrieved_at_utc", _UTC, False),
        ColumnContract("availability_basis", "string", False),
        ColumnContract("source_revision_indicator", "string", True),
        ColumnContract("revision_parent_status", "string", False),
        ColumnContract("finality", "string", False),
        ColumnContract("effective_year", "int32", False),
    ),
)


EQUITY_PRICE_ADJUSTMENT_FACTOR_CHAIN = DatasetContract(
    name="equity_price_adjustment_factor_chain",
    version=1,
    status=_STATUS,
    description=(
        "Deterministic, versioned split/capital factor chain selected as of one UTC "
        "knowledge timestamp. Cash distributions and total return remain separate."
    ),
    source=EQUITY_CORPORATE_ACTION_EVENT_VERSION.name,
    layer="derived",
    storage_format="parquet",
    frequency="event",
    timezone="UTC",
    primary_key=("factor_chain_id", "effective_date", "same_date_sequence", "event_id"),
    sort_key=("factor_chain_id", "effective_date", "same_date_sequence", "event_id"),
    partition_by=("security_id",),
    columns=(
        ColumnContract("factor_chain_id", "string", False),
        ColumnContract("factor_chain_version", "int32", False),
        ColumnContract("security_id", "string", False),
        ColumnContract("as_of_knowledge_at_utc", _UTC, False),
        ColumnContract("through_date", "date32", False),
        ColumnContract("event_id", "string", False),
        ColumnContract("event_version_id", "string", False),
        ColumnContract("action_type", "string", False),
        ColumnContract("effective_date", "date32", False),
        ColumnContract("same_date_sequence", "int32", False),
        ColumnContract("price_factor", "decimal128(28,12)", False, "multiplier"),
        ColumnContract("volume_factor", "decimal128(28,12)", False, "multiplier"),
        ColumnContract("turnover_factor", "decimal128(28,12)", False, "multiplier"),
        ColumnContract("factor_method", "string", False),
        ColumnContract("source_observation_id", "string", False),
        ColumnContract("pit_status", "string", False),
    ),
)


EQUITY_PRICE_SPLIT_CAPITAL_ADJUSTED_DAILY = DatasetContract(
    name="equity_price_split_capital_adjusted_daily",
    version=1,
    status=_STATUS,
    description=(
        "Separate derived price view retaining provider-native OHLCV beside explicit "
        "split/capital-adjusted fields. It is neither a cash-dividend adjustment nor a "
        "total-return series and never overwrites the source price dataset."
    ),
    source="kr_equity_price_daily+equity_price_adjustment_factor_chain",
    layer="derived",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "security_id"),
    sort_key=("date", "security_id"),
    partition_by=("security_id", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("security_id", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("native_open", "decimal128(28,8)", False),
        ColumnContract("native_high", "decimal128(28,8)", False),
        ColumnContract("native_low", "decimal128(28,8)", False),
        ColumnContract("native_close", "decimal128(28,8)", False),
        ColumnContract("native_volume", "decimal128(38,8)", False, "shares"),
        ColumnContract("native_turnover", "decimal128(38,8)", True, "source_currency"),
        ColumnContract("adjusted_open", "decimal128(28,8)", False),
        ColumnContract("adjusted_high", "decimal128(28,8)", False),
        ColumnContract("adjusted_low", "decimal128(28,8)", False),
        ColumnContract("adjusted_close", "decimal128(28,8)", False),
        ColumnContract("adjusted_volume", "decimal128(38,8)", False, "shares"),
        ColumnContract("adjusted_turnover", "decimal128(38,8)", True, "source_currency"),
        ColumnContract("provider_adjustment_status", "string", False),
        ColumnContract("factor_chain_id", "string", False),
        ColumnContract("applied_event_version_ids", "list<string>", False),
        ColumnContract("adjustment_scope", "string", False),
    ),
)


CORPORATE_ACTION_CONTRACTS = (
    EQUITY_CORPORATE_ACTION_EVENT_VERSION,
    EQUITY_PRICE_ADJUSTMENT_FACTOR_CHAIN,
    EQUITY_PRICE_SPLIT_CAPITAL_ADJUSTED_DAILY,
)


__all__ = [
    "CORPORATE_ACTION_CONTRACTS",
    "EQUITY_CORPORATE_ACTION_EVENT_VERSION",
    "EQUITY_PRICE_ADJUSTMENT_FACTOR_CHAIN",
    "EQUITY_PRICE_SPLIT_CAPITAL_ADJUSTED_DAILY",
]
