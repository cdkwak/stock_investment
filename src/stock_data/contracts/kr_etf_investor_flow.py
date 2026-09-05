from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_ETF_INVESTOR_FLOW_DAILY = DatasetContract(
    name="kr_etf_investor_flow_daily",
    version=1,
    status="active",
    description=(
        "Per-symbol KRX-listed ETF investor net-purchase amounts from the "
        "pykrx ETF daily trading-value view. Values are provider-native KRW "
        "amounts retained for descriptive display; predictive point-in-time "
        "finality is not claimed."
    ),
    source=(
        "KRX MDCSTAT04902[13207] via pykrx "
        "stock.get_etf_trading_volume_and_value(..., '거래대금', '순매수')"
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
        ColumnContract("institution_net_krw", "int64", False, "KRW"),
        ColumnContract("other_corporation_net_krw", "int64", False, "KRW"),
        ColumnContract("individual_net_krw", "int64", False, "KRW"),
        ColumnContract("foreign_net_krw", "int64", False, "KRW"),
        ColumnContract("total_net_krw", "int64", False, "KRW"),
        ColumnContract("provider", "string", False),
        ColumnContract("retrieved_at", "timestamp[us, UTC]", False),
    ),
)


__all__ = ["KR_ETF_INVESTOR_FLOW_DAILY"]
