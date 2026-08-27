"""Unregistered research contracts for Yahoo per-symbol option volume P/C."""

from stock_data.contracts.base import ColumnContract, DatasetContract


_STATUS = "research_only_live_evidence_pending"
_UTC = "timestamp[us, UTC]"


YAHOO_SYMBOL_OPTION_CONTRACT_OBSERVATION = DatasetContract(
    name="yahoo_symbol_option_contract_observation",
    version=1,
    status=_STATUS,
    description=(
        "One retained Yahoo option contract from one explicit underlying and expiry. "
        "Yahoo REGULAR classification and independent multiplier verification remain separate."
    ),
    source="yahoo_unofficial_option_chain",
    layer="normalized",
    storage_format="parquet",
    frequency="snapshot",
    timezone="UTC",
    primary_key=("symbol", "expiry_at_utc", "contract_symbol", "captured_at_utc"),
    sort_key=("symbol", "expiry_at_utc", "contract_symbol", "captured_at_utc"),
    partition_by=("symbol", "capture_date"),
    columns=(
        ColumnContract("symbol", "string", False),
        ColumnContract("expiry_at_utc", _UTC, False),
        ColumnContract("contract_symbol", "string", False),
        ColumnContract("side", "string", False),
        ColumnContract("strike", "float64", False),
        ColumnContract("contract_size", "string", False),
        ColumnContract("multiplier_status", "string", False),
        ColumnContract("volume", "int64", True, "contracts"),
        ColumnContract("open_interest", "int64", True, "contracts"),
        ColumnContract("last_trade_at_utc", _UTC, True),
        ColumnContract("underlying_quote_at_utc", _UTC, True),
        ColumnContract("captured_at_utc", _UTC, False),
        ColumnContract("capture_date", "date32", False),
        ColumnContract("landing_sha256", "string", False),
        ColumnContract("source", "string", False),
    ),
)


YAHOO_SYMBOL_OPTION_VOLUME_PCR_RESEARCH = DatasetContract(
    name="yahoo_symbol_option_volume_pcr_research",
    version=1,
    status=_STATUS,
    description=(
        "Separate per-underlying put volume divided by call volume over explicit retained "
        "expiries and independently verified standard contracts; never a market aggregate."
    ),
    source=YAHOO_SYMBOL_OPTION_CONTRACT_OBSERVATION.name,
    layer="derived",
    storage_format="parquet",
    frequency="snapshot",
    timezone="Asia/Seoul",
    primary_key=("symbol", "captured_at_utc", "expiry_scope_sha256"),
    sort_key=("symbol", "captured_at_utc", "expiry_scope_sha256"),
    partition_by=("symbol", "capture_date"),
    columns=(
        ColumnContract("symbol", "string", False),
        ColumnContract("call_volume", "int64", True, "contracts"),
        ColumnContract("put_volume", "int64", True, "contracts"),
        ColumnContract("volume_pcr", "float64", True, "ratio"),
        ColumnContract("expiry_count", "int64", False),
        ColumnContract("standard_contract_count", "int64", False),
        ColumnContract("excluded_nonstandard_count", "int64", False),
        ColumnContract("captured_at_utc", _UTC, False),
        ColumnContract("captured_at_kst", "timestamp[us, Asia/Seoul]", False),
        ColumnContract("latest_contract_trade_at_utc", _UTC, True),
        ColumnContract("provider_timestamp_status", "string", False),
        ColumnContract("observation_status", "string", False),
        ColumnContract("blocked_reason", "string", True),
        ColumnContract("expiry_scope_sha256", "string", False),
        ColumnContract("input_dataset", "string", False),
        ColumnContract("backtest_eligible", "bool", False),
    ),
)


YAHOO_SYMBOL_OPTION_PCR_CONTRACTS = (
    YAHOO_SYMBOL_OPTION_CONTRACT_OBSERVATION,
    YAHOO_SYMBOL_OPTION_VOLUME_PCR_RESEARCH,
)


__all__ = [
    "YAHOO_SYMBOL_OPTION_CONTRACT_OBSERVATION",
    "YAHOO_SYMBOL_OPTION_PCR_CONTRACTS",
    "YAHOO_SYMBOL_OPTION_VOLUME_PCR_RESEARCH",
]
