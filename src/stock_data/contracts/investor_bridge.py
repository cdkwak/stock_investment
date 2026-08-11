from stock_data.contracts.base import ColumnContract, DatasetContract


KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY = DatasetContract(
    name="kr_market_investor_net_purchase_bridge_daily",
    version=1,
    status="active",
    description=(
        "Published provider-boundary bridge of market investor net purchases. "
        "Rows retain their provider-specific value_unit and predictive-use status."
    ),
    source="kr_market_investor_net_purchase_daily+kr_market_investor_trading_daily",
    layer="published",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market"),
    sort_key=("date", "market"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("institution_net_purchase", "int64", False),
        ColumnContract("other_corporation_net_purchase", "int64", False),
        ColumnContract("individual_net_purchase", "int64", False),
        ColumnContract("foreign_net_purchase", "int64", False),
        ColumnContract("total_net_purchase", "int64", False),
        ColumnContract(
            "value_unit", "string", False, None,
            "KRW for Toss rows; unit_unknown for the checksum-fixed legacy rows.",
        ),
        ColumnContract("source_dataset", "string", False),
        ColumnContract("source_provider", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("provider_segment", "string", False),
        ColumnContract("availability_date", "string", True),
        ColumnContract(
            "predictive_use_status", "string", False, None,
            "Legacy values are blocked by unknown unit/availability; Toss rows are eligible from availability_date.",
        ),
    ),
)


INVESTOR_BRIDGE_CONTRACTS = (KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,)
