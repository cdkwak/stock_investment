from stock_data.contracts.base import ColumnContract, DatasetContract


KR_INDEX_CONSTITUENT_DAILY = DatasetContract(
    name="kr_index_constituent_daily",
    version=1,
    status="accepted_exact_date_only",
    description=(
        "Dated index membership observation. A row is valid only for its exact "
        "queried date; it never implies membership before or after that date."
    ),
    source="KRX MDCSTAT00601 exact-date observation",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "index_ticker", "symbol"),
    sort_key=("date", "index_ticker", "symbol"),
    partition_by=("index_ticker", "year"),
    columns=(
        ColumnContract("date", "date32", False, description="Exact membership effective date."),
        ColumnContract("observation_date", "date32", False, description="Exact source query date; must equal date."),
        ColumnContract("index_symbol", "string", False),
        ColumnContract("index_ticker", "string", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("name", "string", True),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("source_captured_at", "string", False),
        ColumnContract("source_sha256", "string", False),
        ColumnContract("pit_status", "string", False),
    ),
)


KR_KOSPI200_CONSTITUENT_PRICE_DAILY = DatasetContract(
    name="kr_kospi200_constituent_price_daily",
    version=1,
    status="accepted_exact_date_only",
    description="Exact-date KOSPI200-member OHLCV subset bound to same-date membership.",
    source="kr_equity_price_daily+kr_index_constituent_daily",
    layer="published",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "symbol"),
    sort_key=("date", "symbol"),
    partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("membership_observation_date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("open", "int64", False),
        ColumnContract("high", "int64", False),
        ColumnContract("low", "int64", False),
        ColumnContract("close", "int64", False),
        ColumnContract("volume", "int64", False),
        ColumnContract("trading_value", "int64", False),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("source_date", "date32", False),
    ),
)


KR_KOSPI200_BREADTH_DAILY = DatasetContract(
    name="kr_kospi200_breadth_daily",
    version=1,
    status="accepted_exact_date_only",
    description="KOSPI200-only breadth from complete same-date membership and prices.",
    source="kr_kospi200_constituent_price_daily+previous-session kr_equity_price_daily",
    layer="derived",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "index_ticker"),
    sort_key=("date", "index_ticker"),
    partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("membership_observation_date", "date32", False),
        ColumnContract("previous_session_date", "date32", False),
        ColumnContract("index_symbol", "string", False),
        ColumnContract("index_ticker", "string", False),
        ColumnContract("advancing", "int64", False),
        ColumnContract("declining", "int64", False),
        ColumnContract("unchanged", "int64", False),
        ColumnContract("total", "int64", False),
        ColumnContract("missing_price_count", "int64", False),
        ColumnContract("scope_status", "string", False),
    ),
)


KOSPI200_CONSTITUENT_BREADTH_CONTRACTS = (
    KR_INDEX_CONSTITUENT_DAILY,
    KR_KOSPI200_CONSTITUENT_PRICE_DAILY,
    KR_KOSPI200_BREADTH_DAILY,
)
