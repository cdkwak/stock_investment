from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
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


def _nullable_date(item, field):
    if _text(item, field, nullable=True) is None:
        return None
    return _date(item, field)


def _nullable_decimal_22_3(item, field):
    value = _text(item, field, nullable=True)
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"field {field} is not decimal") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"field {field} is invalid")
    sign, digits, exponent = parsed.as_tuple()
    integer_digits = max(len(digits) + exponent, 0)
    fractional_digits = max(-exponent, 0)
    if integer_digits + fractional_digits > 22 or fractional_digits > 3:
        raise ValueError(f"field {field} exceeds decimal(22,3)")
    return parsed


def _fiscal_month_day(item, field):
    value = _text(item, field, nullable=True)
    if value is None:
        return None
    if re.fullmatch(r"\d{4}", value) is None:
        raise ValueError(f"field {field} is not MMDD")
    try:
        datetime.strptime("2000" + value, "%Y%m%d")
    except ValueError:
        raise ValueError(f"field {field} is not MMDD") from None
    return value


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


RIGHTS_SOURCE_FIELDS = (
    "basDt", "issuCmpyKsdCustNo", "crno", "stckIssuCmpyNm",
    "scrsIssuMnbdCd", "scrsIssuMnbdCdNm", "stckIssuRcd", "stckIssuRcdNm",
    "rgtExertRcd", "rgtExertRcdNm", "rgtExertSttgDt", "rgtExertEdDt",
    "nmlsLckSttgDt", "nmlsLckEdDt", "trsnmDptyDcd", "trsnmDptyDcdNm",
    "stckParPrc", "stckStacMd",
)


def _source_record_sha256(item):
    raw = {
        field: None if item.get(field) is None else str(item.get(field))
        for field in RIGHTS_SOURCE_FIELDS
    }
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_rights(items, *, landing_response_body_sha256: str, source_page_no: int):
    """Normalize immutable Rights source observations, never canonical events."""
    if re.fullmatch(r"[0-9a-f]{64}", landing_response_body_sha256) is None:
        raise ValueError("landing_response_body_sha256 is not lowercase SHA-256")
    if not isinstance(source_page_no, int) or isinstance(source_page_no, bool) or source_page_no < 1:
        raise ValueError("source_page_no must be a positive integer")
    rows = []
    for ordinal, item in enumerate(items):
        rows.append({
            "source_snapshot_date": D(item, "basDt"),
            "landing_response_body_sha256": landing_response_body_sha256,
            "source_item_ordinal": ordinal,
            "source_page_no": source_page_no,
            "source_record_sha256": _source_record_sha256(item),
            "ksd_issuer_customer_no": T(item, "issuCmpyKsdCustNo"),
            "corporate_number": TN(item, "crno"),
            "issuer_name": T(item, "stckIssuCmpyNm"),
            "securities_issuer_entity_code": TN(item, "scrsIssuMnbdCd"),
            "securities_issuer_entity_name": TN(item, "scrsIssuMnbdCdNm"),
            "issuance_reason_code": TN(item, "stckIssuRcd"),
            "issuance_reason_name": TN(item, "stckIssuRcdNm"),
            "rights_exercise_reason_code": TN(item, "rgtExertRcd"),
            "rights_exercise_reason_name": TN(item, "rgtExertRcdNm"),
            "exercise_start_date": _nullable_date(item, "rgtExertSttgDt"),
            "exercise_end_date": _nullable_date(item, "rgtExertEdDt"),
            "registry_close_start_date": _nullable_date(item, "nmlsLckSttgDt"),
            "registry_close_end_date": _nullable_date(item, "nmlsLckEdDt"),
            "transfer_agent_classification_code": TN(item, "trsnmDptyDcd"),
            "transfer_agent_classification_name": TN(item, "trsnmDptyDcdNm"),
            "par_value": _nullable_decimal_22_3(item, "stckParPrc"),
            "fiscal_month_day": _fiscal_month_day(item, "stckStacMd"),
        })
    frame = pd.DataFrame(rows, columns=KR_EQUITY_RIGHTS_SCHEDULE.column_names)
    frame = frame.sort_values(list(KR_EQUITY_RIGHTS_SCHEDULE.sort_key), kind="stable").reset_index(drop=True)
    validate_data_v1(frame, KR_EQUITY_RIGHTS_SCHEDULE)
    return frame
