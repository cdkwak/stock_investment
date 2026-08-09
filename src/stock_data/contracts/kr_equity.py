from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_EQUITY_PRICE_DAILY = DatasetContract(
    name="kr_equity_price_daily",
    version=1,
    status="draft",
    description="Daily source prices and trading activity for Korean equities.",
    source="data_go_kr:GetStockSecuritiesInfoService/getStockPriceInfo",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"),
    sort_key=("date", "market", "symbol"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("open", "int64", False),
        ColumnContract("high", "int64", False),
        ColumnContract("low", "int64", False),
        ColumnContract("close", "int64", False),
        ColumnContract("volume", "int64", False),
        ColumnContract("trading_value", "int64", False),
    ),
)

KR_EQUITY_MARKET_CAP_DAILY = DatasetContract(
    name="kr_equity_market_cap_daily",
    version=1,
    status="draft",
    description="Daily market capitalization facts for Korean equities.",
    source="data_go_kr:GetStockSecuritiesInfoService/getStockPriceInfo",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"),
    sort_key=("date", "market", "symbol"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("market_cap", "int64", False),
        ColumnContract("shares_outstanding", "int64", False),
    ),
)

KR_EQUITY_MASTER = DatasetContract(
    name="kr_equity_master",
    version=2,
    status="active",
    description="Korean equity identity and authoritative listing lifecycle facts.",
    source="data_go_kr_stock_issuance+daily_source_identity",
    layer="normalized",
    storage_format="parquet",
    frequency="event",
    timezone=None,
    primary_key=("symbol", "market"),
    sort_key=("market", "symbol"),
    partition_by=("market",),
    columns=(
        ColumnContract("symbol", "string", False),
        ColumnContract("name", "string", False),
        ColumnContract("market", "string", False),
        ColumnContract("isin", "string", True),
        ColumnContract("corp_no", "string", True),
        ColumnContract("company_name", "string", True),
        ColumnContract("security_type_code", "string", True),
        ColumnContract("security_type_name", "string", True),
        ColumnContract("par_value", "float64", True, "KRW"),
        ColumnContract("issued_shares", "int64", True, "shares"),
        ColumnContract("listing_date", "string", True),
        ColumnContract("delisting_date", "string", True),
        ColumnContract("deposit_registration_date", "string", True),
        ColumnContract("deposit_cancellation_date", "string", True),
        ColumnContract("source", "string", False),
        ColumnContract("source_date", "string", True),
    ),
)

KR_EQUITY_UNIVERSE_DAILY = DatasetContract(
    name="kr_equity_universe_daily", version=1, status="draft",
    description="Point-in-time KOSPI/KOSDAQ listed-equity universe and identity.",
    source="data_go_kr:GetKrxListedInfoService/getItemInfo", layer="normalized",
    storage_format="parquet", frequency="daily", timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"), sort_key=("date", "market", "symbol"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False), ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False), ColumnContract("isin", "string", False),
        ColumnContract("name", "string", False), ColumnContract("corporate_number", "string", True),
        ColumnContract("corporate_name", "string", False),
    ),
)

KR_EQUITY_CANONICAL_UNIVERSE_DAILY = DatasetContract(
    name="kr_equity_canonical_universe_daily", version=1, status="active",
    description="Published point-in-time equity universe formed from daily listed-info and price-source union.",
    source="kr_equity_universe_daily+data_go_kr_stock_price_identity+kr_equity_master(metadata_only)",
    layer="published", storage_format="parquet", frequency="daily", timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"), sort_key=("date", "market", "symbol"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False), ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False), ColumnContract("isin", "string", False),
        ColumnContract("name", "string", False), ColumnContract("listed_info_present", "bool", False),
        ColumnContract("price_present", "bool", False), ColumnContract("master_present", "bool", False),
        ColumnContract("universe_source", "string", False), ColumnContract("security_type", "string", False),
        ColumnContract("listing_date", "string", True), ColumnContract("delisting_date", "string", True),
    ),
)
