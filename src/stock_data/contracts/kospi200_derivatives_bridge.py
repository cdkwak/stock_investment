from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


PRIMARY_KEY = ("date", "bridge_segment", "session", "source_contract_code")


def _column(name: str, dtype: str, nullable: bool = False, description: str | None = None) -> ColumnContract:
    return ColumnContract(name, dtype, nullable, None, description)


_COMMON_COLUMNS = (
    _column("date", "date32"),
    _column("bridge_segment", "string"),
    _column("session", "string"),
    _column("source_session_label", "string", True),
    _column("underlying", "string"),
    _column("instrument_type", "string"),
    _column("source_contract_code", "string"),
    _column("isin", "string", True),
    _column("source_name", "string"),
    _column("source_product_label", "string"),
    _column("maturity_month", "string"),
    _column("expiry_date", "date32", True, "Not supplied by either retained source; maturity_month is not treated as expiry."),
    _column("expiry_status", "string"),
)

_MARKET_COLUMNS = (
    _column("open", "float64", True),
    _column("high", "float64", True),
    _column("low", "float64", True),
    _column("close", "float64", True),
    _column("volume", "int64", True),
    _column("open_interest", "int64", True),
    _column("price_unit_status", "string"),
    _column("volume_unit_status", "string"),
    _column("open_interest_unit_status", "string"),
    _column("source", "string"),
    _column("source_operation", "string"),
    _column("input_dataset", "string"),
    _column("source_row_no", "int64", True),
    _column("predictive_use_status", "string", False, "Contract rows only; no continuous or front-month roll rule is defined."),
)


def _dataset(name: str, instrument: str, columns: tuple[ColumnContract, ...]) -> DatasetContract:
    return DatasetContract(
        name=name,
        version=1,
        status="active",
        description=(
            f"Published provider-boundary union of KOSPI200 {instrument} contract rows; "
            "provider/session boundaries and unverified units remain explicit."
        ),
        source="legacy_stock_investment+data_go_kr",
        layer="published",
        storage_format="parquet",
        frequency="daily",
        timezone="Asia/Seoul",
        primary_key=PRIMARY_KEY,
        sort_key=PRIMARY_KEY,
        partition_by=("year",),
        columns=columns,
    )


KR_KOSPI200_FUTURES_PROVIDER_BRIDGE_DAILY = _dataset(
    "kr_kospi200_futures_provider_bridge_daily",
    "futures",
    _COMMON_COLUMNS + _MARKET_COLUMNS,
)

KR_KOSPI200_OPTIONS_PROVIDER_BRIDGE_DAILY = _dataset(
    "kr_kospi200_options_provider_bridge_daily",
    "options",
    _COMMON_COLUMNS
    + (
        _column("call_put", "string"),
        _column("strike", "float64"),
        _column("strike_unit_status", "string"),
    )
    + _MARKET_COLUMNS,
)

KOSPI200_DERIVATIVES_BRIDGE_CONTRACTS = (
    KR_KOSPI200_FUTURES_PROVIDER_BRIDGE_DAILY,
    KR_KOSPI200_OPTIONS_PROVIDER_BRIDGE_DAILY,
)
