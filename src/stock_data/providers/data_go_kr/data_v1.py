from __future__ import annotations

from typing import Mapping, Sequence
import pandas as pd

from stock_data.contracts.data_v1 import (
    KR_CREDIT_BALANCE_DAILY, KR_DERIVATIVES_FUTURES_DAILY, KR_DERIVATIVES_OPTIONS_DAILY,
    KR_EQUITY_DIVIDEND, KR_EQUITY_RIGHTS_SCHEDULE, KR_MARKET_LIQUIDITY_DAILY,
    KR_STOCK_LENDING_DAILY, KR_STOCK_LENDING_MARKET_DAILY, KR_STOCK_LENDING_PARTICIPANT_DAILY,
)
from stock_data.validation.data_v1 import validate_data_v1


BASE = "https://apis.data.go.kr/1160100/service"
ENDPOINTS = {
    "market_liquidity": BASE + "/GetKofiaStatisticsInfoService/getSecuritiesMarketTotalCapitalInfo",
    "credit_balance": BASE + "/GetKofiaStatisticsInfoService/getGrantingOfCreditBalanceInfo",
    "futures": BASE + "/GetDerivativeProductInfoService/getStockFuturesPriceInfo",
    "options": BASE + "/GetDerivativeProductInfoService/getOptionsPriceInfo",
    "stock_lending": BASE + "/GetCMStckLnbInfoService/getStckLnbDetail",
    "stock_lending_market": BASE + "/GetCMStckLnbInfoService/getStckLnbProgress",
    "stock_lending_participant": BASE + "/GetCMStckLnbInfoService/getStckLnbInvpnDetail",
    "dividend_https": "https://apis.data.go.kr/1160100/GetStocDiviInfoService_V2/getDiviInfo_V2",
    "rights_https": "https://apis.data.go.kr/1160100/GetStocRighScheService_V2/getRighExerReasSche_V2",
}


def _text(item, field, *, nullable=False):
    value = item.get(field)
    if value is None or str(value).strip() in {"", "NULL"}:
        if nullable:
            return None
        raise ValueError(f"field {field} is missing")
    return str(value).strip()


def _number(item, field, *, integer=False):
    value = _text(item, field)
    try:
        return int(value) if integer else float(value)
    except ValueError:
        raise ValueError(f"field {field} is not numeric") from None


def _date(item, field="basDt"):
    value = _text(item, field)
    parsed = pd.to_datetime(value, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"field {field} is not YYYYMMDD")
    return parsed.strftime("%Y-%m-%d")


def _normalize(items: Sequence[Mapping[str, object]], contract, mapping):
    rows = [{target: parser(item, source) for target, source, parser in mapping} for item in items]
    frame = pd.DataFrame(rows, columns=contract.column_names)
    frame = frame.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    validate_data_v1(frame, contract)
    return frame


T = lambda item, field: _text(item, field)
TN = lambda item, field: _text(item, field, nullable=True)
I = lambda item, field: _number(item, field, integer=True)
F = lambda item, field: _number(item, field)
D = lambda item, field: _date(item, field)


def normalize_market_liquidity(items):
    return _normalize(items, KR_MARKET_LIQUIDITY_DAILY, (
        ("date", "basDt", D), ("investor_deposits", "invrDpsgAmt", I),
        ("exchange_derivatives_deposits", "onbdDrvPrdTrRcAdvAmt", I),
        ("customer_rp_sell_balance", "toCstRpchCndBndSlgBal", I),
        ("brokerage_receivables", "brkTrdUcolMny", I),
        ("forced_sale_amount", "brkTrdUcolMnyVsOppsTrdAmt", I),
        ("forced_sale_ratio", "ucolMnyVsOppsTrdRlImpt", F),
    ))


def normalize_credit_balance(items):
    return _normalize(items, KR_CREDIT_BALANCE_DAILY, (
        ("date", "basDt", D), ("credit_financing_total", "crdTrFingWhl", I),
        ("credit_financing_kospi", "crdTrFingScrs", I), ("credit_financing_kosdaq", "crdTrFingKosdaq", I),
        ("credit_stock_lending_total", "crdTrLndrWhl", I), ("credit_stock_lending_kospi", "crdTrLndrScrs", I),
        ("credit_stock_lending_kosdaq", "crdTrLndrKosdaq", I), ("subscription_loan", "sbscCapLn", I),
        ("securities_collateral_loan", "dpsgScrtMogFing", I),
    ))


def _derivative(items, contract, extra):
    return _normalize(items, contract, (
        ("date", "basDt", D), ("product_category", "prdCtg", T), ("symbol", "srtnCd", T),
        ("isin", "isinCd", T), ("name", "itmsNm", T), ("open", "mkp", F), ("high", "hipr", F),
        ("low", "lopr", F), ("close", "clpr", F), *extra,
        ("volume", "trqu", I), ("trading_value", "trPrc", I), ("open_interest", "opnint", I),
    ))


def normalize_futures(items):
    return _derivative(items, KR_DERIVATIVES_FUTURES_DAILY,
                       (("spot_price", "sptPrc", F), ("settlement_price", "stmPrc", F)))


def normalize_options(items):
    return _derivative(items, KR_DERIVATIVES_OPTIONS_DAILY,
                       (("next_day_base_price", "nxtDdBsPrc", F), ("implied_volatility", "iptVlty", F)))


def normalize_stock_lending(items):
    return _normalize(items, KR_STOCK_LENDING_DAILY, (
        ("date", "basDt", D), ("market", "mrktClsfNm", T), ("symbol", "stckItmsCd", T),
        ("name", "stckItmsNm", T), ("executed_shares", "cclStckCnt", I),
        ("returned_shares", "rdptStckCnt", I), ("balance_shares", "balnStckCnt", I),
        ("balance_amount", "balnStckAmt", I),
    ))


def normalize_stock_lending_market(items):
    return _normalize(items, KR_STOCK_LENDING_MARKET_DAILY, (
        ("date", "basDt", D), ("executed_shares", "cclStckCnt", I),
        ("returned_shares", "rdptStckCnt", I), ("balance_shares", "balnStckCnt", I),
        ("balance_amount", "balnStckAmt", I),
    ))


def normalize_stock_lending_participant(items):
    return _normalize(items, KR_STOCK_LENDING_PARTICIPANT_DAILY, (
        ("date", "basDt", D), ("participant_group", "invpnClsfNm", T),
        ("participant_detail", "invpnClsfDtlNm", T), ("lender_amount", "lndeCclStckAmt", I),
        ("lender_ratio", "lndeCclStckAmtRto", F), ("borrower_amount", "borCclStckAmt", I),
        ("borrower_ratio", "borCclStckAmtRto", F),
    ))


def normalize_dividend(items):
    return _normalize(items, KR_EQUITY_DIVIDEND, (
        ("date", "basDt", D), ("isin", "isinCd", T), ("corp_no", "crno", TN),
        ("company", "stckIssuCmpyNm", T),
        ("security_type", "scrsItmsKcdNm", T), ("event_type", "stckDvdnRcdNm", T),
        ("dividend_record_date", "dvdnBasDt", T), ("cash_payment_date", "cashDvdnPayDt", TN),
        ("stock_delivery_date", "stckHndvDt", TN), ("ordinary_dividend_amount", "stckGenrDvdnAmt", F),
        ("ordinary_cash_dividend_ratio", "stckGenrCashDvdnRt", F),
        ("ordinary_stock_dividend_ratio", "stckGenrDvdnRt", F),
        ("differential_dividend_amount", "stckGrdnDvdnAmt", F),
        ("differential_cash_dividend_ratio", "cashGrdnDvdnRt", F),
        ("differential_stock_dividend_ratio", "stckGrdnDvdnRt", F), ("par_value", "stckParPrc", F),
    ))


def normalize_rights(items):
    return _normalize(items, KR_EQUITY_RIGHTS_SCHEDULE, (
        ("date", "basDt", D), ("issuer_code", "scrsIssuMnbdCd", T),
        ("corporate_number", "crno", TN), ("company", "stckIssuCmpyNm", T),
        ("issuance_reason_code", "stckIssuRcd", T), ("issuance_reason", "stckIssuRcdNm", T),
        ("event_type_code", "rgtExertRcd", T), ("event_type", "rgtExertRcdNm", T),
        ("exercise_start_date", "rgtExertSttgDt", TN),
        ("exercise_end_date", "rgtExertEdDt", TN), ("registry_close_start_date", "nmlsLckSttgDt", TN),
        ("registry_close_end_date", "nmlsLckEdDt", TN), ("par_value", "stckParPrc", F),
    ))
