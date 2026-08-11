from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import re
from typing import Callable

import pandas as pd

from stock_data.contracts.kr_short_selling import (
    KR_SHORT_SELLING_BALANCE_DAILY,
    KR_SHORT_SELLING_INVESTOR_DAILY,
    KR_SHORT_SELLING_TRADING_DAILY,
)


TRADING_BLD = "dbms/MDC/STAT/srt/MDCSTAT30101"
BALANCE_BLD = "dbms/MDC/STAT/srt/MDCSTAT30501"
INVESTOR_BLD = "dbms/MDC/STAT/srt/MDCSTAT30301"
BUSINESS_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
MARKET_IDS = {"KOSPI": "STK", "KOSDAQ": "KSQ"}
MARKET_TYPE_CODES = {"KOSPI": 1, "KOSDAQ": 2}
METRIC_CODES = {"volume": 1, "trading_value": 2}
INVESTOR_FIELDS = {
    "STR_CONST_VAL1": "institution",
    "STR_CONST_VAL2": "individual",
    "STR_CONST_VAL3": "foreign",
    "STR_CONST_VAL4": "other",
    "STR_CONST_VAL5": "total",
}

TRADING_RAW_FIELDS = (
    "ISU_CD", "ISU_ABBRV", "SECUGRP_NM", "CVSRTSELL_TRDVOL",
    "UPTICKRULE_APPL_TRDVOL", "UPTICKRULE_EXCPT_TRDVOL", "ACC_TRDVOL",
    "TRDVOL_WT", "CVSRTSELL_TRDVAL", "UPTICKRULE_APPL_TRDVAL",
    "UPTICKRULE_EXCPT_TRDVAL", "ACC_TRDVAL", "TRDVAL_WT",
)
BALANCE_RAW_FIELDS = (
    "ISU_CD", "ISU_ABBRV", "BAL_QTY", "LIST_SHRS", "BAL_AMT", "MKTCAP", "BAL_RTO",
)
INVESTOR_RAW_FIELDS = ("TRD_DD", *INVESTOR_FIELDS)


class ShortSellingSourceError(RuntimeError):
    pass


class ShortSellingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedShortSellingResponse:
    dataframe: pd.DataFrame
    classification: str
    source_rows: int


@dataclass(frozen=True)
class RequestScope:
    dataset: str
    scope_id: str
    params: dict[str, object]
    market: str
    start_date: str
    end_date: str
    metric: str | None = None


def _date_text(value: str) -> str:
    return datetime.strptime(value.replace("-", ""), "%Y%m%d").date().isoformat()


def _integer(value: object, field: str) -> int:
    text = str(value).replace(",", "").strip()
    if not re.fullmatch(r"-?\d+", text):
        raise ShortSellingSourceError(f"{field} is not an integer source value")
    return int(text)


def _float(value: object, field: str) -> float:
    text = str(value).replace(",", "").strip()
    if not re.fullmatch(r"-?(?:\d+(?:\.\d*)?|\.\d+)", text):
        raise ShortSellingSourceError(f"{field} is not a decimal source value")
    result = float(text)
    if not math.isfinite(result):
        raise ShortSellingSourceError(f"{field} is not finite")
    return result


def _source_rows(body: bytes, required_fields: tuple[str, ...]) -> list[dict[str, object]]:
    stripped = body.lstrip()
    if stripped.startswith(b"<"):
        raise ShortSellingSourceError("HTML/restriction response is not source data")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ShortSellingSourceError("response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ShortSellingSourceError("response root is not an object")
    if payload.get("_error_code") or payload.get("error") or payload.get("errors"):
        raise ShortSellingSourceError("source returned an error payload")
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list):
        raise ShortSellingSourceError("OutBlock_1 is missing or not a list")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ShortSellingSourceError(f"source row {index} is not an object")
        missing = sorted(set(required_fields) - set(row))
        if missing:
            raise ShortSellingSourceError(f"source row {index} missing fields: {missing}")
    return rows


def parse_trading_body(body: bytes, *, date: str, market: str) -> ParsedShortSellingResponse:
    rows = _source_rows(body, TRADING_RAW_FIELDS)
    if not rows:
        return ParsedShortSellingResponse(
            pd.DataFrame(columns=KR_SHORT_SELLING_TRADING_DAILY.column_names),
            "VALID_EMPTY", 0,
        )
    normalized = []
    for row in rows:
        normalized.append(
            {
                "date": _date_text(date),
                "market": market,
                "symbol": str(row["ISU_CD"]).strip(),
                "source_name": str(row["ISU_ABBRV"]).strip(),
                "source_security_type": str(row["SECUGRP_NM"]).strip(),
                "short_volume": _integer(row["CVSRTSELL_TRDVOL"], "CVSRTSELL_TRDVOL"),
                "uptick_rule_applied_short_volume": _integer(
                    row["UPTICKRULE_APPL_TRDVOL"], "UPTICKRULE_APPL_TRDVOL"
                ),
                "uptick_rule_exempt_short_volume": _integer(
                    row["UPTICKRULE_EXCPT_TRDVOL"], "UPTICKRULE_EXCPT_TRDVOL"
                ),
                "total_trading_volume": _integer(row["ACC_TRDVOL"], "ACC_TRDVOL"),
                "short_volume_ratio": _float(row["TRDVOL_WT"], "TRDVOL_WT"),
                "short_trading_value": _integer(row["CVSRTSELL_TRDVAL"], "CVSRTSELL_TRDVAL"),
                "uptick_rule_applied_short_trading_value": _integer(
                    row["UPTICKRULE_APPL_TRDVAL"], "UPTICKRULE_APPL_TRDVAL"
                ),
                "uptick_rule_exempt_short_trading_value": _integer(
                    row["UPTICKRULE_EXCPT_TRDVAL"], "UPTICKRULE_EXCPT_TRDVAL"
                ),
                "total_trading_value": _integer(row["ACC_TRDVAL"], "ACC_TRDVAL"),
                "short_trading_value_ratio": _float(row["TRDVAL_WT"], "TRDVAL_WT"),
            }
        )
    frame = pd.DataFrame(normalized, columns=KR_SHORT_SELLING_TRADING_DAILY.column_names)
    frame = frame.sort_values(list(KR_SHORT_SELLING_TRADING_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_trading(frame)
    return ParsedShortSellingResponse(frame, "SUCCESS", len(rows))


def parse_balance_body(body: bytes, *, date: str, market: str) -> ParsedShortSellingResponse:
    rows = _source_rows(body, BALANCE_RAW_FIELDS)
    if not rows:
        return ParsedShortSellingResponse(
            pd.DataFrame(columns=KR_SHORT_SELLING_BALANCE_DAILY.column_names),
            "VALID_EMPTY", 0,
        )
    normalized = [
        {
            "date": _date_text(date),
            "market": market,
            "symbol": str(row["ISU_CD"]).strip(),
            "source_name": str(row["ISU_ABBRV"]).strip(),
            "short_balance": _integer(row["BAL_QTY"], "BAL_QTY"),
            "shares_outstanding": _integer(row["LIST_SHRS"], "LIST_SHRS"),
            "short_balance_value": _integer(row["BAL_AMT"], "BAL_AMT"),
            "market_cap": _integer(row["MKTCAP"], "MKTCAP"),
            "short_balance_ratio": _float(row["BAL_RTO"], "BAL_RTO"),
        }
        for row in rows
    ]
    frame = pd.DataFrame(normalized, columns=KR_SHORT_SELLING_BALANCE_DAILY.column_names)
    frame = frame.sort_values(list(KR_SHORT_SELLING_BALANCE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_balance(frame)
    return ParsedShortSellingResponse(frame, "SUCCESS", len(rows))


def _is_blank_zero_placeholder(rows: list[dict[str, object]]) -> bool:
    if len(rows) != 1 or str(rows[0]["TRD_DD"]).strip():
        return False
    return all(_integer(rows[0][field], field) == 0 for field in INVESTOR_FIELDS)


def parse_investor_body(
    body: bytes, *, market: str, metric: str,
) -> ParsedShortSellingResponse:
    rows = _source_rows(body, INVESTOR_RAW_FIELDS)
    if not rows:
        return ParsedShortSellingResponse(
            pd.DataFrame(columns=KR_SHORT_SELLING_INVESTOR_DAILY.column_names),
            "VALID_EMPTY", 0,
        )
    if _is_blank_zero_placeholder(rows):
        return ParsedShortSellingResponse(
            pd.DataFrame(columns=KR_SHORT_SELLING_INVESTOR_DAILY.column_names),
            "VALID_EMPTY_PLACEHOLDER", 1,
        )
    if any(not str(row["TRD_DD"]).strip() for row in rows):
        raise ShortSellingSourceError("blank investor date is not an all-zero placeholder")
    normalized = []
    for row in rows:
        date = datetime.strptime(str(row["TRD_DD"]), "%Y/%m/%d").date().isoformat()
        for source_field, investor_type in INVESTOR_FIELDS.items():
            normalized.append(
                {
                    "date": date,
                    "market": market,
                    "investor_type": investor_type,
                    "metric": metric,
                    "value": _integer(row[source_field], source_field),
                }
            )
    frame = pd.DataFrame(normalized, columns=KR_SHORT_SELLING_INVESTOR_DAILY.column_names)
    frame = frame.sort_values(list(KR_SHORT_SELLING_INVESTOR_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_investor(frame)
    return ParsedShortSellingResponse(frame, "SUCCESS", len(rows))


def _validate_common(frame: pd.DataFrame, contract) -> None:
    if tuple(frame.columns) != contract.column_names:
        raise ShortSellingValidationError(f"{contract.name} columns differ from v2 contract")
    if frame[list(contract.primary_key)].isna().any(axis=None):
        raise ShortSellingValidationError(f"{contract.name} primary key contains null")
    if frame.duplicated(list(contract.primary_key)).any():
        raise ShortSellingValidationError(f"{contract.name} primary key is duplicated")
    if not frame["market"].isin(MARKET_IDS).all():
        raise ShortSellingValidationError(f"{contract.name} has unsupported market")
    pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="raise")


def _nonnegative(frame: pd.DataFrame, columns: list[str], dataset: str) -> None:
    if frame[columns].isna().any(axis=None) or (frame[columns] < 0).any(axis=None):
        raise ShortSellingValidationError(f"{dataset} has null/negative source values")


def validate_trading(frame: pd.DataFrame) -> None:
    _validate_common(frame, KR_SHORT_SELLING_TRADING_DAILY)
    if frame.empty:
        return
    if not frame["symbol"].str.fullmatch(r"[0-9A-Z]{6}").all():
        raise ShortSellingValidationError("trading symbol must be a six-character KRX short code")
    if frame[["source_name", "source_security_type"]].eq("").any(axis=None):
        raise ShortSellingValidationError("trading source identity fields are blank")
    integer_columns = [
        "short_volume", "uptick_rule_applied_short_volume",
        "uptick_rule_exempt_short_volume", "total_trading_volume",
        "short_trading_value", "uptick_rule_applied_short_trading_value",
        "uptick_rule_exempt_short_trading_value", "total_trading_value",
    ]
    ratio_columns = ["short_volume_ratio", "short_trading_value_ratio"]
    _nonnegative(frame, integer_columns + ratio_columns, "trading")
    if (frame["short_volume"] > frame["total_trading_volume"]).any():
        raise ShortSellingValidationError("short volume exceeds total trading volume")
    if (frame["short_trading_value"] > frame["total_trading_value"]).any():
        raise ShortSellingValidationError("short value exceeds total trading value")
    # Do not synthesize an accounting identity here.  The 2008 pilot contains
    # non-zero short totals with source-zero uptick components.  Those zeros are
    # preserved verbatim because historical applicability is not documented.
    if (frame[ratio_columns] > 100).any(axis=None):
        raise ShortSellingValidationError("trading ratio is outside percent bounds")


def validate_balance(frame: pd.DataFrame) -> None:
    _validate_common(frame, KR_SHORT_SELLING_BALANCE_DAILY)
    if frame.empty:
        return
    if not frame["symbol"].str.fullmatch(r"[0-9A-Z]{6}").all() or frame["source_name"].eq("").any():
        raise ShortSellingValidationError("balance source identity is invalid")
    _nonnegative(
        frame,
        [
            "short_balance", "shares_outstanding", "short_balance_value",
            "market_cap", "short_balance_ratio",
        ],
        "balance",
    )
    if (frame["short_balance_ratio"] > 100).any():
        raise ShortSellingValidationError("balance ratio is outside percent bounds")


def validate_investor(frame: pd.DataFrame) -> None:
    _validate_common(frame, KR_SHORT_SELLING_INVESTOR_DAILY)
    if frame.empty:
        return
    if not frame["investor_type"].isin(INVESTOR_FIELDS.values()).all():
        raise ShortSellingValidationError("investor type is unsupported")
    if not frame["metric"].isin(METRIC_CODES).all():
        raise ShortSellingValidationError("investor metric is unsupported")
    _nonnegative(frame, ["value"], "investor")
    for _, group in frame.groupby(["date", "market", "metric"], sort=False):
        values = group.set_index("investor_type")["value"]
        if set(values.index) != set(INVESTOR_FIELDS.values()):
            raise ShortSellingValidationError("investor group is incomplete")
        if values["total"] != values[["institution", "individual", "foreign", "other"]].sum():
            raise ShortSellingValidationError("investor total differs from source class sum")


PARSERS: dict[str, Callable[..., ParsedShortSellingResponse]] = {
    "trading": parse_trading_body,
    "balance": parse_balance_body,
    "investor": parse_investor_body,
}


def trading_scope(date: str, market: str) -> RequestScope:
    text = date.replace("-", "")
    return RequestScope(
        "trading", f"{text}_{market}",
        {"bld": TRADING_BLD, "trdDd": text, "mktId": MARKET_IDS[market], "inqCond": "STMFRTSCIFDRFS"},
        market, text, text,
    )


def balance_scope(date: str, market: str) -> RequestScope:
    text = date.replace("-", "")
    return RequestScope(
        "balance", f"{text}_{market}",
        {"bld": BALANCE_BLD, "trdDd": text, "mktTpCd": MARKET_TYPE_CODES[market]},
        market, text, text,
    )


def investor_scope(start: str, end: str, market: str, metric: str) -> RequestScope:
    start_text, end_text = start.replace("-", ""), end.replace("-", "")
    if (datetime.strptime(end_text, "%Y%m%d") - datetime.strptime(start_text, "%Y%m%d")).days > 730:
        raise ValueError("investor source range must not exceed 730 calendar days")
    return RequestScope(
        "investor", f"{start_text}_{end_text}_{market}_{metric}",
        {
            "bld": INVESTOR_BLD, "strtDd": start_text, "endDd": end_text,
            "inqCondTpCd": METRIC_CODES[metric], "mktTpCd": MARKET_TYPE_CODES[market],
        },
        market, start_text, end_text, metric,
    )
