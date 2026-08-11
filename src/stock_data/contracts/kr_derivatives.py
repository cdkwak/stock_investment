from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


SOURCE = "data_go_kr"
FUTURES_OPERATION = "GetDerivativeProductInfoService/getStockFuturesPriceInfo"
OPTIONS_OPERATION = "GetDerivativeProductInfoService/getOptionsPriceInfo"


def _column(
    name: str,
    dtype: str,
    *,
    nullable: bool = False,
    unit: str | None = None,
    description: str | None = None,
) -> ColumnContract:
    return ColumnContract(name, dtype, nullable, unit, description)


def _dataset(
    name: str,
    description: str,
    operation: str,
    columns: tuple[ColumnContract, ...],
) -> DatasetContract:
    return DatasetContract(
        name=name,
        version=1,
        status="draft",
        description=description,
        source=f"{SOURCE}:{operation}",
        layer="normalized",
        storage_format="parquet",
        frequency="daily",
        timezone="Asia/Seoul",
        primary_key=("date", "contract"),
        sort_key=("date", "maturity_month", "contract"),
        partition_by=("year",),
        columns=columns,
    )


COMMON_IDENTITY = (
    _column("date", "date32"),
    _column("underlying", "string", description="KOSPI200 or KOSDAQ150."),
    _column("contract", "string", description="Source short contract code (srtnCd)."),
    _column("isin", "string"),
    _column("name", "string", description="Losslessly preserved source item name."),
    _column("product_category", "string", description="Exact source prdCtg value."),
    _column(
        "maturity_month",
        "string",
        description="YYYY-MM contract month parsed from the verified source item-name format; not an expiry date.",
    ),
)
OHLC = (
    _column("open", "float64"),
    _column("high", "float64"),
    _column("low", "float64"),
    _column("close", "float64"),
)
ACTIVITY = (
    _column("volume", "int64", unit="contracts"),
    _column("trading_value", "int64", unit="KRW"),
    _column("open_interest", "int64", unit="contracts"),
    _column("source", "string"),
    _column("source_operation", "string"),
)


def _futures(name: str, underlying: str) -> DatasetContract:
    return _dataset(
        name,
        f"Daily {underlying} outright futures source observations; spreads and unrelated derivatives are excluded.",
        FUTURES_OPERATION,
        COMMON_IDENTITY
        + OHLC
        + (
            _column("underlying_value", "float64", description="Source sptPrc field."),
            _column("settlement_price", "float64", description="Source stmPrc field."),
        )
        + ACTIVITY,
    )


def _options(name: str, underlying: str) -> DatasetContract:
    return _dataset(
        name,
        f"Daily regular-monthly {underlying} options source observations; weekly, mini and single-stock options are excluded.",
        OPTIONS_OPERATION,
        COMMON_IDENTITY
        + (
            _column("call_put", "string", description="CALL or PUT parsed from verified C/P source-name token."),
            _column("strike", "float64", unit="index_points"),
        )
        + OHLC
        + (
            _column("next_day_base_price", "float64", description="Source nxtDdBsPrc; not the underlying index value."),
            _column("implied_volatility", "float64", unit="source_unit"),
        )
        + ACTIVITY,
    )


KR_KOSPI200_FUTURES_DAILY = _futures("kr_kospi200_futures_daily", "KOSPI200")
KR_KOSPI200_OPTIONS_DAILY = _options("kr_kospi200_options_daily", "KOSPI200")
KR_KOSDAQ150_FUTURES_DAILY = _futures("kr_kosdaq150_futures_daily", "KOSDAQ150")
KR_KOSDAQ150_OPTIONS_DAILY = _options("kr_kosdaq150_options_daily", "KOSDAQ150")

KR_DERIVATIVE_CONTRACTS = (
    KR_KOSPI200_FUTURES_DAILY,
    KR_KOSPI200_OPTIONS_DAILY,
    KR_KOSDAQ150_FUTURES_DAILY,
    KR_KOSDAQ150_OPTIONS_DAILY,
)
