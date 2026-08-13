from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


def _column(name: str, dtype: str, *, unit: str | None = None, description: str | None = None) -> ColumnContract:
    return ColumnContract(name, dtype, False, unit, description)


_COLUMNS = (
    _column("date", "date32"),
    _column("product", "string", description="KOSPI200_FUTURES or KOSPI200_OPTIONS."),
    _column("option_right", "string", description="NA for futures; ALL, CALL, or PUT for options."),
    _column("session", "string", description="ALL, REGULAR, or NIGHT source selection."),
    _column("investor_type_source", "string", description="Losslessly retained KRX category label."),
    _column("sell_volume", "float64", unit="volume_unit_source"),
    _column("buy_volume", "float64", unit="volume_unit_source"),
    _column("net_buy_volume", "float64", unit="volume_unit_source"),
    _column("sell_trading_value", "float64", unit="trading_value_unit_source"),
    _column("buy_trading_value", "float64", unit="trading_value_unit_source"),
    _column("net_buy_trading_value", "float64", unit="trading_value_unit_source"),
    _column("volume_unit_source", "string", description="Exact unit selected in the KRX request."),
    _column("trading_value_unit_source", "string", description="Exact unit selected in the KRX request."),
    _column("source", "string"),
    _column("source_operation", "string"),
)


def _contract(name: str, product: str) -> DatasetContract:
    return DatasetContract(
        name=name,
        version=1,
        status="active",
        description=(
            f"Daily {product} investor trading totals from authenticated free KRX Basic "
            "Statistics screen 15007. Contract/maturity/strike identity is not supplied."
        ),
        source="krx_basic_statistics:15007",
        layer="normalized",
        storage_format="parquet",
        frequency="daily",
        timezone="Asia/Seoul",
        primary_key=("date", "product", "option_right", "session", "investor_type_source"),
        sort_key=("date", "option_right", "session", "investor_type_source"),
        partition_by=("year",),
        columns=_COLUMNS,
    )


KR_KOSPI200_FUTURES_INVESTOR_TRADING_DAILY = _contract(
    "kr_kospi200_futures_investor_trading_daily", "KOSPI200 futures"
)
KR_KOSPI200_OPTIONS_INVESTOR_TRADING_DAILY = _contract(
    "kr_kospi200_options_investor_trading_daily", "KOSPI200 options"
)

KRX_DERIVATIVES_INVESTOR_CONTRACTS = (
    KR_KOSPI200_FUTURES_INVESTOR_TRADING_DAILY,
    KR_KOSPI200_OPTIONS_INVESTOR_TRADING_DAILY,
)
