"""Versioned contracts for authenticated KRX short-selling observations.

The source date for trading and investor datasets is usable no earlier than the
next verified trading day (T+1 project minimum).  The balance source date is the
KRX report-obligation occurrence date, not a verified publication/availability
date; predictive use therefore remains blocked.
"""

from stock_data.contracts.base import ColumnContract, DatasetContract


_STRING = lambda name, nullable=False, description=None: ColumnContract(
    name, "string", nullable, description=description
)
_INT = lambda name, unit, nullable=False, description=None: ColumnContract(
    name, "int64", nullable, unit, description
)
_FLOAT = lambda name, unit, nullable=False, description=None: ColumnContract(
    name, "float64", nullable, unit, description
)


KR_SHORT_SELLING_TRADING_DAILY = DatasetContract(
    name="kr_short_selling_trading_daily",
    version=2,
    status="implementation_ready_t_plus_1_minimum",
    description=(
        "Per-symbol full-market KRX short-selling trading observations from "
        "MDCSTAT30101. Source names, security type, totals, ratios, and uptick-rule "
        "components are preserved. Research availability is no earlier than T+1."
    ),
    source="authenticated_pykrx_1.2.8:MDCSTAT30101",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"),
    sort_key=("date", "market", "symbol"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False, description="KRX trading date"),
        _STRING("market"),
        _STRING("symbol"),
        _STRING("source_name"),
        _STRING("source_security_type"),
        _INT("short_volume", "shares"),
        _INT("uptick_rule_applied_short_volume", "shares"),
        _INT("uptick_rule_exempt_short_volume", "shares"),
        _INT("total_trading_volume", "shares"),
        _FLOAT("short_volume_ratio", "percent"),
        _INT("short_trading_value", "KRW"),
        _INT("uptick_rule_applied_short_trading_value", "KRW"),
        _INT("uptick_rule_exempt_short_trading_value", "KRW"),
        _INT("total_trading_value", "KRW"),
        _FLOAT("short_trading_value_ratio", "percent"),
    ),
)


KR_SHORT_SELLING_BALANCE_DAILY = DatasetContract(
    name="kr_short_selling_balance_daily",
    version=2,
    status="implementation_ready_predictive_use_blocked",
    description=(
        "Per-symbol full-market KRX short-balance observations from MDCSTAT30501. "
        "The date is explicitly the report-obligation occurrence date; historical "
        "publication availability is not supplied and predictive use is blocked."
    ),
    source="authenticated_pykrx_1.2.8:MDCSTAT30501",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market", "symbol"),
    sort_key=("date", "market", "symbol"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract(
            "date", "date32", False,
            description="KRX report-obligation occurrence date; not availability date",
        ),
        _STRING("market"),
        _STRING("symbol"),
        _STRING("source_name"),
        _INT("short_balance", "shares"),
        _INT("shares_outstanding", "shares"),
        _INT("short_balance_value", "KRW"),
        _INT("market_cap", "KRW"),
        _FLOAT("short_balance_ratio", "percent"),
    ),
)


KR_SHORT_SELLING_INVESTOR_DAILY = DatasetContract(
    name="kr_short_selling_investor_daily",
    version=2,
    status="implementation_ready_t_plus_1_minimum",
    description=(
        "Market-level KRX short-selling observations from MDCSTAT30301, normalized "
        "to investor class and source metric. Research availability is no earlier "
        "than T+1. Blank-date all-zero source placeholders are valid-empty, not rows."
    ),
    source="authenticated_pykrx_1.2.8:MDCSTAT30301",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "market", "investor_type", "metric"),
    sort_key=("date", "market", "metric", "investor_type"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False, description="KRX trading date"),
        _STRING("market"),
        _STRING(
            "investor_type", description="institution, individual, foreign, other, or total"
        ),
        _STRING("metric", description="volume or trading_value"),
        _INT("value", "metric_dependent", description="shares for volume; KRW for trading_value"),
    ),
)


SHORT_SELLING_CONTRACTS = {
    "trading": KR_SHORT_SELLING_TRADING_DAILY,
    "balance": KR_SHORT_SELLING_BALANCE_DAILY,
    "investor": KR_SHORT_SELLING_INVESTOR_DAILY,
}
