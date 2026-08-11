from stock_data.contracts.base import ColumnContract, DatasetContract


COMMON = (
    ColumnContract("snapshot_date", "date32", False),
    ColumnContract("market_date", "date32", False),
    ColumnContract("collected_at", "timestamp[us, UTC]", False),
    ColumnContract("source", "string", False),
    ColumnContract("source_operation", "string", False),
    ColumnContract("is_provisional", "bool", False),
)


def _dataset(name, description, key, columns):
    return DatasetContract(
        name=name, version=1, status="active", description=description,
        source="kb_securities_open_api", layer="normalized", storage_format="parquet",
        frequency="intraday_snapshot", timezone="Asia/Seoul",
        primary_key=("collected_at", *key), sort_key=("collected_at", *key),
        partition_by=("snapshot_date",), columns=COMMON + tuple(columns),
    )


KB_MARKET_BREADTH_SNAPSHOT = _dataset("kb_market_breadth_snapshot", "Provisional KB market breadth.", ("market",), (
    ColumnContract("market", "string", False), ColumnContract("upper_limit", "int64", True),
    ColumnContract("advancing", "int64", False), ColumnContract("unchanged", "int64", False),
    ColumnContract("declining", "int64", False), ColumnContract("lower_limit", "int64", True),
))
KB_PROGRAM_TRADING_SNAPSHOT = _dataset("kb_program_trading_snapshot", "Provisional KB program trading summary.", (), (
    ColumnContract("arbitrage_net_buy", "int64", True), ColumnContract("non_arbitrage_net_buy", "int64", True),
))
KB_INVESTOR_FLOW_SNAPSHOT = _dataset("kb_investor_flow_snapshot", "Provisional KB investor flow by class.", ("investor_code",), (
    ColumnContract("investor_code", "string", False), ColumnContract("investor_name", "string", False),
    *(ColumnContract(name, "int64", True) for name in ("kospi_net_buy", "kosdaq_net_buy", "futures_net_buy", "call_option_net_buy", "put_option_net_buy", "star_futures_net_buy", "stock_futures_net_buy")),
))
KB_MARKET_LIQUIDITY_SNAPSHOT = _dataset("kb_market_liquidity_snapshot", "Provisional KB market liquidity balances.", (), tuple(
    ColumnContract(name, "float64", True) for name in ("customer_deposit", "customer_deposit_change", "receivables", "receivables_change", "credit_balance", "credit_balance_change", "futures_deposit", "futures_deposit_change")
))
KB_DERIVATIVES_SUMMARY_SNAPSHOT = _dataset("kb_derivatives_summary_snapshot", "Provisional KB derivative quotes.", ("instrument_code",), (
    ColumnContract("instrument_code", "string", False), ColumnContract("instrument_name", "string", False),
    ColumnContract("current_price", "float64", True), ColumnContract("change_direction_code", "string", True),
    ColumnContract("change", "float64", True), ColumnContract("change_pct", "float64", True),
    ColumnContract("volume", "int64", True), ColumnContract("open_interest", "int64", True),
))
KB_DOMESTIC_INDEX_SNAPSHOT = _dataset("kb_domestic_index_snapshot", "Provisional KB domestic index quotes.", ("index_code",), (
    ColumnContract("index_code", "string", False), ColumnContract("index_name", "string", False),
    ColumnContract("current_index", "float64", True), ColumnContract("change_direction_code", "string", True),
    ColumnContract("change", "float64", True), ColumnContract("change_pct", "float64", True),
    ColumnContract("volume", "int64", True), ColumnContract("trading_value", "int64", True),
))
KB_GLOBAL_SYMBOL_SNAPSHOT = _dataset("kb_global_symbol_snapshot", "Provisional KB global symbol quotes.", ("symbol_code",), (
    ColumnContract("symbol_code", "string", False), ColumnContract("symbol_name", "string", False),
    ColumnContract("source_datetime", "string", True), ColumnContract("current_price", "float64", True),
    ColumnContract("change_direction_code", "string", True), ColumnContract("change", "float64", True),
    ColumnContract("change_pct", "float64", True),
))

KBSEC_SNAPSHOT_CONTRACTS = (KB_MARKET_BREADTH_SNAPSHOT, KB_PROGRAM_TRADING_SNAPSHOT,
    KB_INVESTOR_FLOW_SNAPSHOT, KB_MARKET_LIQUIDITY_SNAPSHOT, KB_DERIVATIVES_SUMMARY_SNAPSHOT,
    KB_DOMESTIC_INDEX_SNAPSHOT, KB_GLOBAL_SYMBOL_SNAPSHOT)
