from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_EQUITY_INVESTOR_FLOW_DAILY = DatasetContract(
    name="kr_equity_investor_flow_daily",
    version=1,
    status="active",
    description=(
        "Per-symbol Korean equity investor net-purchase amounts from the pykrx "
        "daily trading-value view. Values are provider-native won amounts and "
        "as-retrieved observations; predictive point-in-time finality is not claimed."
    ),
    source=(
        "KRX/pykrx stock.get_market_trading_value_by_date "
        "on='순매수', detail=False"
    ),
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "symbol"),
    sort_key=("date", "symbol"),
    partition_by=("symbol", "year"),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("foreign_net", "int64", False, "KRW"),
        ColumnContract("institution_net", "int64", False, "KRW"),
        ColumnContract("individual_net", "int64", False, "KRW"),
        ColumnContract("other_corp_net", "int64", False, "KRW"),
        ColumnContract("total_net", "int64", False, "KRW"),
        ColumnContract("source", "string", False),
        ColumnContract("captured_at", "timestamp[us, UTC]", False),
    ),
)


__all__ = ["KR_EQUITY_INVESTOR_FLOW_DAILY"]
