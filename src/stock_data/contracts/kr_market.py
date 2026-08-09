from stock_data.contracts.base import ColumnContract, DatasetContract


KR_INVESTOR_FLOW_DAILY = DatasetContract(
    name="kr_investor_flow_daily", version=1, status="active",
    description="Daily net trading value by verified pykrx investor class and market.",
    source="pykrx", layer="normalized", storage_format="parquet", frequency="daily",
    timezone="Asia/Seoul", primary_key=("date", "market"),
    sort_key=("date", "market"), partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False), ColumnContract("market", "string", False),
        ColumnContract("institution_net_buy", "int64", False),
        ColumnContract("other_corporation_net_buy", "int64", False),
        ColumnContract("individual_net_buy", "int64", False),
        ColumnContract("foreign_net_buy", "int64", False),
        ColumnContract("total_net_buy", "int64", False),
    ),
)

KR_MARKET_BREADTH_DAILY = DatasetContract(
    name="kr_market_breadth_daily", version=1, status="active",
    description="Market breadth derived from consecutive equity close observations.",
    source="kr_equity_price_daily+kr_equity_canonical_universe_daily", layer="derived", storage_format="parquet",
    frequency="daily", timezone="Asia/Seoul", primary_key=("date", "market"),
    sort_key=("date", "market"), partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False), ColumnContract("market", "string", False),
        ColumnContract("advancing", "int64", False), ColumnContract("declining", "int64", False),
        ColumnContract("unchanged", "int64", False), ColumnContract("total", "int64", False),
    ),
)
