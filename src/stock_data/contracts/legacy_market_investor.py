from stock_data.contracts.base import ColumnContract, DatasetContract


C004_START_DATE = "1999-01-04"
C004_END_DATE = "2014-06-30"
A001_START_DATE = "2014-07-01"


KR_MARKET_INVESTOR_NET_PURCHASE_DAILY = DatasetContract(
    name="kr_market_investor_net_purchase_daily",
    version=1,
    status="active",
    description=(
        "Checksum-fixed legacy PyKRX KOSPI investor net-purchase observations, "
        "scoped strictly before the A001 provider boundary."
    ),
    source="legacy_stock_investment_pykrx_1.2.8",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market"),
    sort_key=("date", "market"),
    partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("institution_net_buy", "int64", False, "unit_unknown", "Signed source trading-value integer; exact monetary scale is unverified."),
        ColumnContract("other_corporation_net_buy", "int64", False, "unit_unknown", "Signed source trading-value integer; exact monetary scale is unverified."),
        ColumnContract("individual_net_buy", "int64", False, "unit_unknown", "Signed source trading-value integer; exact monetary scale is unverified."),
        ColumnContract("foreign_net_buy", "int64", False, "unit_unknown", "Signed source trading-value integer; exact monetary scale is unverified."),
        ColumnContract("total_net_buy", "int64", False, "unit_unknown", "Signed source trading-value integer; exact monetary scale is unverified."),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("provider_boundary", "string", False),
    ),
)


LEGACY_MARKET_INVESTOR_CONTRACTS = (KR_MARKET_INVESTOR_NET_PURCHASE_DAILY,)
