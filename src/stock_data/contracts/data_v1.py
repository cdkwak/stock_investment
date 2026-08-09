from stock_data.contracts.base import ColumnContract, DatasetContract


def _dataset(name, description, source, primary_key, columns, *, frequency="daily", layer="normalized"):
    return DatasetContract(
        name=name, version=1, status="draft", description=description,
        source=source, layer=layer, storage_format="parquet", frequency=frequency,
        timezone="Asia/Seoul", primary_key=primary_key, sort_key=primary_key,
        partition_by=("year",), columns=tuple(ColumnContract(*column) for column in columns),
    )


DATE = ("date", "date32", False)
STRING = lambda name: (name, "string", False)
INT = lambda name, unit=None: (name, "int64", False, unit)
FLOAT = lambda name, unit=None: (name, "float64", False, unit)

KR_MARKET_LIQUIDITY_DAILY = _dataset(
    "kr_market_liquidity_daily", "Daily Korean securities-market funding and liquidity aggregates.",
    "data_go_kr:GetKofiaStatisticsInfoService/getSecuritiesMarketTotalCapitalInfo", ("date",),
    (DATE, INT("investor_deposits", "KRW"), INT("exchange_derivatives_deposits", "KRW"),
     INT("customer_rp_sell_balance", "KRW"), INT("brokerage_receivables", "KRW"),
     INT("forced_sale_amount", "KRW"), FLOAT("forced_sale_ratio", "percent")),
)

KR_CREDIT_BALANCE_DAILY = _dataset(
    "kr_credit_balance_daily", "Daily Korean credit financing, stock lending and collateral-loan aggregates.",
    "data_go_kr:GetKofiaStatisticsInfoService/getGrantingOfCreditBalanceInfo", ("date",),
    (DATE, INT("credit_financing_total"), INT("credit_financing_kospi"), INT("credit_financing_kosdaq"),
     INT("credit_stock_lending_total"), INT("credit_stock_lending_kospi"),
     INT("credit_stock_lending_kosdaq"), INT("subscription_loan"), INT("securities_collateral_loan")),
)

DERIVATIVE_ID = (DATE, STRING("product_category"), STRING("symbol"), STRING("isin"), STRING("name"))
OHLC = (FLOAT("open"), FLOAT("high"), FLOAT("low"), FLOAT("close"))
KR_DERIVATIVES_FUTURES_DAILY = _dataset(
    "kr_derivatives_futures_daily", "Daily source futures prices and activity.",
    "data_go_kr:GetDerivativeProductInfoService/getStockFuturesPriceInfo", ("date", "symbol"),
    DERIVATIVE_ID + OHLC + (FLOAT("spot_price"), FLOAT("settlement_price"), INT("volume"),
                            INT("trading_value", "KRW"), INT("open_interest")),
)
KR_DERIVATIVES_OPTIONS_DAILY = _dataset(
    "kr_derivatives_options_daily", "Daily source option prices, activity and implied volatility.",
    "data_go_kr:GetDerivativeProductInfoService/getOptionsPriceInfo", ("date", "symbol"),
    DERIVATIVE_ID + OHLC + (FLOAT("next_day_base_price"), FLOAT("implied_volatility"), INT("volume"),
                            INT("trading_value", "KRW"), INT("open_interest")),
)

KR_STOCK_LENDING_DAILY = _dataset(
    "kr_stock_lending_daily", "Per-symbol stock lending activity and balances; distinct from short selling.",
    "data_go_kr:GetCMStckLnbInfoService/getStckLnbDetail; coverage_policy=2021-04+; license=portal_product_page_required",
    ("date", "market", "symbol"),
    (DATE, STRING("market"), STRING("symbol"), STRING("name"), INT("executed_shares", "shares"),
     INT("returned_shares", "shares"), INT("balance_shares", "shares"), INT("balance_amount", "KRW")),
)
KR_STOCK_LENDING_MARKET_DAILY = _dataset(
    "kr_stock_lending_market_daily", "Source-published market aggregate stock lending trend.",
    "data_go_kr:GetCMStckLnbInfoService/getStckLnbProgress; coverage_policy=2021-04+; license=portal_product_page_required",
    ("date",), (DATE, INT("executed_shares", "shares"), INT("returned_shares", "shares"),
                         INT("balance_shares", "shares"), INT("balance_amount", "KRW")),
)
KR_STOCK_LENDING_PARTICIPANT_DAILY = _dataset(
    "kr_stock_lending_participant_daily", "Daily stock lending amounts and ratios by participant class.",
    "data_go_kr:GetCMStckLnbInfoService/getStckLnbInvpnDetail; coverage_policy=2021-04+; license=portal_product_page_required",
    ("date", "participant_group", "participant_detail"),
    (DATE, STRING("participant_group"), STRING("participant_detail"), INT("lender_amount", "KRW"),
     FLOAT("lender_ratio", "percent"), INT("borrower_amount", "KRW"), FLOAT("borrower_ratio", "percent")),
)

KR_EQUITY_DIVIDEND = _dataset(
    "kr_equity_dividend", "Source dividend events; no price adjustment is applied.",
    "data_go_kr:GetStocDiviInfoService_V2/getDiviInfo_V2", ("date", "isin", "dividend_record_date", "event_type"),
    (DATE, STRING("isin"), ("corp_no", "string", True), STRING("company"), STRING("security_type"), STRING("event_type"),
     STRING("dividend_record_date"), ("cash_payment_date", "string", True),
     ("stock_delivery_date", "string", True), FLOAT("ordinary_dividend_amount", "KRW_per_share"),
     FLOAT("ordinary_cash_dividend_ratio", "percent"), FLOAT("ordinary_stock_dividend_ratio", "percent"),
     FLOAT("differential_dividend_amount", "KRW_per_share"), FLOAT("differential_cash_dividend_ratio", "percent"),
     FLOAT("differential_stock_dividend_ratio", "percent"), FLOAT("par_value", "KRW")), frequency="event",
)
KR_EQUITY_RIGHTS_SCHEDULE = _dataset(
    "kr_equity_rights_schedule", "Source corporate-right schedule events with source event type preserved.",
    "data_go_kr:GetStocRighScheService_V2/getRighExerReasSche_V2",
    ("issuer_code", "event_type_code", "exercise_start_date", "exercise_end_date", "issuance_reason_code"),
    (DATE, STRING("issuer_code"), ("corporate_number", "string", True), STRING("company"),
     STRING("issuance_reason_code"), STRING("issuance_reason"), STRING("event_type_code"), STRING("event_type"),
     ("exercise_start_date", "string", True), ("exercise_end_date", "string", True),
     ("registry_close_start_date", "string", True), ("registry_close_end_date", "string", True),
     FLOAT("par_value", "KRW")), frequency="event",
)

DATA_V1_CONTRACTS = (
    KR_MARKET_LIQUIDITY_DAILY, KR_CREDIT_BALANCE_DAILY,
    KR_DERIVATIVES_FUTURES_DAILY, KR_DERIVATIVES_OPTIONS_DAILY,
    KR_STOCK_LENDING_DAILY, KR_STOCK_LENDING_MARKET_DAILY, KR_STOCK_LENDING_PARTICIPANT_DAILY,
    KR_EQUITY_DIVIDEND, KR_EQUITY_RIGHTS_SCHEDULE,
)
