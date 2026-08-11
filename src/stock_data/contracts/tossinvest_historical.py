from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


SOURCE = "tossinvest_open_api"
PROVENANCE = (
    ColumnContract("source", "string", False),
    ColumnContract("source_operation", "string", False),
    ColumnContract("source_date", "string", False),
    ColumnContract("collected_at", "timestamp[ns, UTC]", False),
    ColumnContract("updated_at", "timestamp[ns, UTC]", True),
    ColumnContract(
        "availability_date",
        "string",
        True,
        description=(
            "KST date derived from source updatedAt; null when the source "
            "provides no update timestamp."
        ),
    ),
)


def _dataset(name, description, key, partition_by, columns, *, status="active"):
    return DatasetContract(
        name=name,
        version=1,
        status=status,
        description=description,
        source=SOURCE,
        layer="normalized",
        storage_format="parquet",
        frequency="daily",
        timezone="Asia/Seoul",
        primary_key=tuple(key),
        sort_key=tuple(key),
        partition_by=tuple(partition_by),
        columns=tuple(columns) + PROVENANCE,
    )


_DATE = ColumnContract("date", "date32", False)
_SYMBOL = ColumnContract("symbol", "string", False)
_INT = lambda name, nullable=True, unit=None: ColumnContract(name, "int64", nullable, unit)
_FLOAT = lambda name, nullable=True, unit=None: ColumnContract(name, "float64", nullable, unit)

_INVESTOR_AMOUNT_COLUMNS = tuple(
    _INT(f"{group}_{side}_amount", unit="KRW")
    for group in (
        "individual",
        "foreigner",
        "institution",
        "other_corporation",
        "institution_financial_investment",
        "institution_insurance",
        "institution_trust",
        "institution_private_equity_fund",
        "institution_bank",
        "institution_other_financial_institution",
        "institution_pension_fund",
    )
    for side in ("buy", "sell")
)

KR_MARKET_INVESTOR_TRADING_DAILY = _dataset(
    "kr_market_investor_trading_daily",
    "Daily KOSPI/KOSDAQ investor buy and sell amounts, including institution breakdown.",
    ("date", "market"),
    ("market", "year"),
    (_DATE, ColumnContract("market", "string", False)) + _INVESTOR_AMOUNT_COLUMNS,
)

KR_EQUITY_SHORT_SELLING_DAILY = _dataset(
    "kr_equity_short_selling_daily",
    "Per-symbol daily short-selling volume, amount, and source-published ratios.",
    ("date", "symbol"),
    ("year",),
    (
        _DATE,
        _SYMBOL,
        _INT("short_selling_volume", unit="shares"),
        _INT("short_selling_amount", unit="KRW"),
        _FLOAT("short_selling_volume_rate", unit="ratio"),
        _FLOAT("short_selling_amount_rate", unit="ratio"),
    ),
    status="draft_blocked",
)

KR_EQUITY_PROGRAM_TRADING_DAILY = _dataset(
    "kr_equity_program_trading_daily",
    "Per-symbol daily arbitrage and non-arbitrage program buy/sell volumes.",
    ("date", "symbol"),
    ("year",),
    (
        _DATE,
        _SYMBOL,
        _INT("arbitrage_buy_volume", unit="shares"),
        _INT("arbitrage_sell_volume", unit="shares"),
        _INT("non_arbitrage_buy_volume", unit="shares"),
        _INT("non_arbitrage_sell_volume", unit="shares"),
    ),
    status="draft_blocked",
)

KR_EQUITY_SECURITIES_LENDING_DAILY = _dataset(
    "kr_equity_securities_lending_daily",
    "Per-symbol daily securities-lending execution, repayment, and balance.",
    ("date", "symbol"),
    ("year",),
    (
        _DATE,
        _SYMBOL,
        _INT("execution_quantity", unit="shares"),
        _INT("repayment_quantity", unit="shares"),
        _INT("balance_quantity", unit="shares"),
        _INT("balance_amount", unit="KRW"),
    ),
    status="draft_blocked",
)

KR_EQUITY_CREDIT_TRADING_DAILY = _dataset(
    "kr_equity_credit_trading_daily",
    "Per-symbol daily margin-loan and stock-loan activity and balances.",
    ("date", "symbol"),
    ("year",),
    (
        _DATE,
        _SYMBOL,
        *(
            column
            for prefix in ("margin_loan", "stock_loan")
            for column in (
                _INT(f"{prefix}_new_quantity", unit="shares"),
                _INT(f"{prefix}_return_quantity", unit="shares"),
                _INT(f"{prefix}_balance_quantity", unit="shares"),
                _FLOAT(f"{prefix}_balance_rate", unit="ratio"),
                _FLOAT(f"{prefix}_trading_rate", unit="ratio"),
            )
        ),
    ),
    status="draft_blocked",
)

KR_TREASURY_YIELD_DAILY = _dataset(
    "kr_treasury_yield_daily",
    "Daily Korean government-bond yield candles kept separate from US FRED yields.",
    ("date", "instrument"),
    ("instrument", "year"),
    (
        _DATE,
        ColumnContract("instrument", "string", False),
        ColumnContract("maturity_years", "int64", False, "years"),
        _FLOAT("open", False, "percent"),
        _FLOAT("high", False, "percent"),
        _FLOAT("low", False, "percent"),
        _FLOAT("close", False, "percent"),
        _INT("volume", True),
    ),
)

TOSSINVEST_HISTORICAL_CONTRACTS = (
    KR_MARKET_INVESTOR_TRADING_DAILY,
    KR_EQUITY_SHORT_SELLING_DAILY,
    KR_EQUITY_PROGRAM_TRADING_DAILY,
    KR_EQUITY_SECURITIES_LENDING_DAILY,
    KR_EQUITY_CREDIT_TRADING_DAILY,
    KR_TREASURY_YIELD_DAILY,
)
