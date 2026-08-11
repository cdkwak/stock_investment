from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


SOURCE = "legacy_stock_investment"


def _column(
    name: str,
    dtype: str,
    nullable: bool = False,
    unit: str | None = None,
    description: str | None = None,
) -> ColumnContract:
    return ColumnContract(name, dtype, nullable, unit, description)


def _dataset(
    name: str,
    description: str,
    *,
    layer: str,
    primary_key: tuple[str, ...],
    sort_key: tuple[str, ...],
    columns: tuple[ColumnContract, ...],
) -> DatasetContract:
    return DatasetContract(
        name=name,
        version=1,
        status="active",
        description=description,
        source=SOURCE,
        layer=layer,
        storage_format="parquet",
        frequency="daily",
        timezone="Asia/Seoul",
        primary_key=primary_key,
        sort_key=sort_key,
        partition_by=("year",),
        columns=columns,
    )


_DATE_AND_PRODUCT = (
    _column("date", "date32"),
    _column("product_name", "string"),
)
_PRICES = (
    _column("close", "float64", True),
    _column("change", "float64", True),
    _column("open", "float64", True),
    _column("high", "float64", True),
    _column("low", "float64", True),
)
_ACTIVITY_AND_PROVENANCE = (
    _column("volume", "int64", True, description="Source value; economic unit is unverified."),
    _column("trading_value", "int64", True, description="Source value; monetary scale is unverified."),
    _column("open_interest", "int64", True, description="Source value; economic unit is unverified."),
    _column("source", "string"),
    _column("source_operation", "string"),
)


KRX_LEGACY_KOSPI200_FUTURES_DAILY = _dataset(
    "krx_legacy_kospi200_futures_daily",
    "Legacy-import KOSPI200 futures observations with source session and row identity preserved.",
    layer="normalized",
    primary_key=("date", "market_name", "contract"),
    sort_key=("date", "source_file_row_no"),
    columns=(
        _DATE_AND_PRODUCT
        + (
            _column("market_name", "string"),
            _column("contract", "string"),
            _column("name", "string"),
        )
        + _PRICES
        + (
            _column("spot_price", "float64", True),
            _column("settlement_price", "float64", True),
        )
        + _ACTIVITY_AND_PROVENANCE
        + (_column("source_file_row_no", "int64"),)
    ),
)


KRX_LEGACY_KOSPI200_OPTIONS_DAILY = _dataset(
    "krx_legacy_kospi200_options_daily",
    "Legacy-import KOSPI200 option observations with upstream duplicate rows preserved.",
    layer="normalized",
    primary_key=("date", "source_row_no"),
    sort_key=("date", "source_file_row_no"),
    columns=(
        _DATE_AND_PRODUCT
        + (
            _column("right_type", "string"),
            _column("contract", "string"),
            _column("name", "string"),
        )
        + _PRICES
        + (
            _column("implied_volatility", "float64", True),
            _column("next_day_base_price", "float64", True),
        )
        + _ACTIVITY_AND_PROVENANCE
        + (
            _column("source_file_row_no", "int64"),
            _column("source_row_no", "int64"),
        )
    ),
)


KR_KOSPI200_OPTION_PCR_DAILY = _dataset(
    "kr_kospi200_option_pcr_daily",
    "Daily KOSPI200 option put/call ratios derived from the legacy-import option rows.",
    layer="derived",
    primary_key=("date", "scope", "market_scope"),
    sort_key=("date", "scope", "market_scope"),
    columns=(
        _column("date", "date32"),
        _column("scope", "string"),
        _column("market_scope", "string"),
        _column("observation_status", "string"),
        _column("call_volume", "int64", True, description="Aggregated source value; economic unit is unverified."),
        _column("put_volume", "int64", True, description="Aggregated source value; economic unit is unverified."),
        _column("volume_pcr", "float64", True, "ratio"),
        _column("call_open_interest", "int64", True, description="Aggregated source value; economic unit is unverified."),
        _column("put_open_interest", "int64", True, description="Aggregated source value; economic unit is unverified."),
        _column("open_interest_pcr", "float64", True, "ratio"),
        _column("call_rows", "int64", unit="rows"),
        _column("put_rows", "int64", unit="rows"),
        _column("unclassified_rows", "int64", unit="rows"),
        _column("source", "string"),
        _column("source_operation", "string"),
        _column("input_dataset", "string"),
    ),
)


LEGACY_KOSPI200_CONTRACTS = (
    KRX_LEGACY_KOSPI200_FUTURES_DAILY,
    KRX_LEGACY_KOSPI200_OPTIONS_DAILY,
    KR_KOSPI200_OPTION_PCR_DAILY,
)
