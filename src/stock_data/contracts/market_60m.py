from stock_data.contracts.base import ColumnContract, DatasetContract


MARKET_PRICE_60M_OBSERVATION = DatasetContract(
    name="market_price_60m_observation",
    version=2,
    status="active",
    description=(
        "Provider-specific finalized 60-minute OHLCV observations for reviewed regular "
        "sessions and an explicit global-continuous allowlist. A provider is selected for "
        "an entire symbol/market-date/session; bars are never stitched."
    ),
    source="explicit_provider_registry",
    layer="normalized",
    storage_format="parquet",
    frequency="intraday",
    timezone="row_timezone",
    primary_key=("provider", "symbol", "bar_start"),
    sort_key=("market_date", "symbol", "provider", "bar_start"),
    partition_by=("market", "symbol", "year"),
    columns=(
        ColumnContract("market_date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("asset_type", "string", False),
        ColumnContract("bar_start", "timestamp[us, UTC]", False),
        ColumnContract("bar_end", "timestamp[us, UTC]", False),
        ColumnContract("timezone", "string", False),
        ColumnContract("session", "string", False),
        ColumnContract("interval", "string", False),
        ColumnContract("actual_duration_minutes", "int16", False),
        ColumnContract("open", "float64", False),
        ColumnContract("high", "float64", False),
        ColumnContract("low", "float64", False),
        ColumnContract("close", "float64", False),
        ColumnContract("volume", "int64", True),
        ColumnContract("provider", "string", False),
        ColumnContract("provider_symbol", "string", False),
        ColumnContract("adjustment_status", "string", False),
        ColumnContract("retrieved_at", "timestamp[us, UTC]", False),
        ColumnContract("fallback_used", "bool", False),
        ColumnContract("fallback_reason", "string", True),
    ),
)


__all__ = ["MARKET_PRICE_60M_OBSERVATION"]
