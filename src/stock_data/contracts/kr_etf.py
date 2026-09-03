from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_ETF_MASTER = DatasetContract(
    name="kr_etf_master",
    version=1,
    status="active",
    description=(
        "Current, explicitly observed Korean ETF identities. Market is the provider-level "
        "KRX venue because the selected pykrx ETF calls do not expose a KOSPI/KOSDAQ board. "
        "Listing date remains null unless a future contracted source supplies it."
    ),
    source="KRX/pykrx ETF ticker list and ticker name",
    layer="normalized",
    storage_format="parquet",
    frequency="event",
    timezone="Asia/Seoul",
    primary_key=("market", "symbol"),
    sort_key=("market", "symbol"),
    partition_by=("market",),
    columns=(
        ColumnContract("symbol", "string", False),
        ColumnContract("name", "string", False),
        ColumnContract("market", "string", False),
        ColumnContract("security_type", "string", False),
        ColumnContract("listing_status", "string", False),
        ColumnContract("listing_date", "date32", True),
        ColumnContract("leverage_multiple", "int64", False, "times"),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("source_date", "date32", False),
    ),
)


KR_ETF_PRICE_DAILY = DatasetContract(
    name="kr_etf_price_daily",
    version=1,
    status="active",
    description=(
        "Provider-native Korean ETF daily OHLCV, trading value, and nullable NAV. "
        "Zero-valued no-trade fields are retained without fabrication."
    ),
    source="KRX/pykrx get_etf_ohlcv_by_date",
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
        ColumnContract("open", "int64", False, "KRW"),
        ColumnContract("high", "int64", False, "KRW"),
        ColumnContract("low", "int64", False, "KRW"),
        ColumnContract("close", "int64", False, "KRW"),
        ColumnContract("volume", "int64", False, "shares"),
        ColumnContract("trading_value", "int64", False, "KRW"),
        ColumnContract("nav", "float64", True, "KRW"),
        ColumnContract("source", "string", False),
        ColumnContract("source_operation", "string", False),
        ColumnContract("source_date", "date32", False),
    ),
)


def infer_kr_etf_leverage_multiple(name: str) -> int:
    """Apply the documented name-only exposure rule for Korean ETFs.

    ``인버스2X`` is negative exposure. Other ``레버리지`` or ``2X`` tokens are
    positive two-times exposure. No other name wording is interpreted.
    """

    value = str(name).strip()
    if not value:
        raise ValueError("ETF name is required for leverage inference")
    folded = value.upper().replace(" ", "")
    if "인버스2X" in folded:
        return -2
    if "레버리지" in value or "2X" in folded:
        return 2
    return 1


KR_ETF_CONTRACTS = (KR_ETF_MASTER, KR_ETF_PRICE_DAILY)


__all__ = [
    "KR_ETF_CONTRACTS", "KR_ETF_MASTER", "KR_ETF_PRICE_DAILY",
    "infer_kr_etf_leverage_multiple",
]
