"""Offline-safe OpenDART corporation-map and all-accounts parsing.

VERIFIED against the official OpenDART guide on 2026-09-03:
``corpCode.xml`` is a one-parameter ZIP response; ``fnlttSinglAcntAll.json``
uses corp/year/report/scope parameters; interim income-statement
``thstrm_amount`` is the three-month value and ``thstrm_add_amount`` is
cumulative; status ``013`` is no data and ``020`` is request-limit exceeded.

UNVERIFIED by the guide: issuer account-ID uniformity and calendar-quarter
period-end inference. Name fallbacks and period-end mapping are therefore
explicit, deterministic project policy rather than provider claims. The guide
documents ``fs_div`` as a request parameter, not as an account-row response
field; this module binds it from the validated request scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from io import BytesIO
import json
import re
from typing import Iterable, Mapping, Sequence
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from stock_data.contracts.kr_fundamentals import OPEN_DART_TERMS_URL


CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
FINANCIAL_STATEMENT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
SUCCESS_STATUS = "000"
NO_DATA_STATUS = "013"
DAILY_LIMIT_STATUS = "020"
DOCUMENTED_DAILY_LIMIT = 20_000
REPORT_CODES = ("11013", "11012", "11014", "11011")
STATEMENT_SCOPES = ("CFS", "OFS")

_CORP_CODE = re.compile(r"\d{8}\Z")
_STOCK_CODE = re.compile(r"\d{6}\Z")
_RECEIPT = re.compile(r"\d{14}\Z")
_YEAR = re.compile(r"\d{4}\Z")
_MODIFY_DATE = re.compile(r"\d{8}\Z")

_REQUIRED_ACCOUNT_FIELDS = (
    "rcept_no", "reprt_code", "bsns_year", "corp_code", "sj_div",
    "account_id", "account_nm", "thstrm_amount", "thstrm_add_amount",
    "frmtrm_amount", "frmtrm_add_amount", "ord", "currency",
)

_ACCOUNT_IDS: Mapping[str, tuple[str, ...]] = {
    "revenue": (
        "ifrs-full_Revenue",
        "dart_OperatingRevenue",
        "dart_Revenue",
    ),
    "operating_income": (
        "dart_OperatingIncomeLoss",
        "ifrs-full_ProfitLossFromOperatingActivities",
    ),
    "net_income": ("ifrs-full_ProfitLoss", "dart_ProfitLoss"),
    "total_liabilities": ("ifrs-full_Liabilities",),
    "total_equity": ("ifrs-full_Equity",),
}
_ACCOUNT_NAMES: Mapping[str, tuple[str, ...]] = {
    "revenue": ("매출액", "영업수익"),
    "operating_income": ("영업이익", "영업이익(손실)", "영업손익"),
    "net_income": ("당기순이익", "당기순이익(손실)", "당기순손익"),
    "total_liabilities": ("부채총계",),
    "total_equity": ("자본총계",),
}
_INCOME_METRICS = frozenset({"revenue", "operating_income", "net_income"})
_BALANCE_METRICS = frozenset({"total_liabilities", "total_equity"})


class OpenDartFundamentalsError(ValueError):
    """Captured provider bytes cannot support the contracted interpretation."""


class OpenDartDailyLimitError(OpenDartFundamentalsError):
    """Provider status 020 requires a hard stop for the current provider day."""


@dataclass(frozen=True)
class OpenDartRequest:
    operation: str
    endpoint: str
    public_parameters: Mapping[str, str]


def corp_code_request() -> OpenDartRequest:
    return OpenDartRequest("corp_code_map", CORP_CODE_URL, {})


def financial_statement_request(
    corp_code: str, bsns_year: int | str, reprt_code: str, fs_div: str,
) -> OpenDartRequest:
    corp_code = str(corp_code)
    year = str(bsns_year)
    if not _CORP_CODE.fullmatch(corp_code):
        raise OpenDartFundamentalsError("corp_code must be exactly eight digits")
    if not _YEAR.fullmatch(year) or int(year) < 2015:
        raise OpenDartFundamentalsError("bsns_year must be a four-digit year from 2015")
    if reprt_code not in REPORT_CODES:
        raise OpenDartFundamentalsError("reprt_code is not supported")
    if fs_div not in STATEMENT_SCOPES:
        raise OpenDartFundamentalsError("fs_div must be CFS or OFS")
    return OpenDartRequest(
        "financial_statement",
        FINANCIAL_STATEMENT_URL,
        {"corp_code": corp_code, "bsns_year": year, "reprt_code": reprt_code, "fs_div": fs_div},
    )


def parse_corp_code_zip(body: bytes) -> list[dict[str, object]]:
    """Parse the single XML member of a captured official corporation ZIP."""
    try:
        with ZipFile(BytesIO(body)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) != 1 or not members[0].filename.lower().endswith(".xml"):
                raise OpenDartFundamentalsError("corporation ZIP must contain exactly one XML file")
            if members[0].file_size > 100_000_000:
                raise OpenDartFundamentalsError("corporation XML exceeds the safety limit")
            xml_body = archive.read(members[0])
    except BadZipFile:
        _raise_xml_status(body)
        raise OpenDartFundamentalsError("corporation response is not a ZIP file")
    try:
        root = ET.fromstring(xml_body)
    except ET.ParseError as error:
        raise OpenDartFundamentalsError("corporation XML is malformed") from error
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in root.findall(".//list"):
        values = {name: (item.findtext(name) or "").strip() for name in (
            "corp_code", "corp_name", "corp_eng_name", "stock_code", "modify_date",
        )}
        corp_code = values["corp_code"]
        stock_code = values["stock_code"]
        modify_date = values["modify_date"]
        if not _CORP_CODE.fullmatch(corp_code) or corp_code in seen:
            raise OpenDartFundamentalsError("corporation XML has an invalid or duplicate corp_code")
        if not values["corp_name"]:
            raise OpenDartFundamentalsError("corporation XML has a blank corp_name")
        if stock_code and not _STOCK_CODE.fullmatch(stock_code):
            raise OpenDartFundamentalsError("corporation XML has an invalid stock_code")
        if not _MODIFY_DATE.fullmatch(modify_date):
            raise OpenDartFundamentalsError("corporation XML has an invalid modify_date")
        try:
            parsed_date = datetime.strptime(modify_date, "%Y%m%d").date().isoformat()
        except ValueError as error:
            raise OpenDartFundamentalsError("corporation modify_date is not a calendar date") from error
        seen.add(corp_code)
        rows.append({
            "corp_code": corp_code,
            "corp_name": values["corp_name"],
            "stock_code": stock_code or None,
            "modify_date": parsed_date,
        })
    if not rows:
        raise OpenDartFundamentalsError("corporation XML contains no list rows")
    return sorted(rows, key=lambda row: str(row["corp_code"]))


def _raise_xml_status(body: bytes) -> None:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return
    status = (root.findtext("status") or "").strip()
    message = (root.findtext("message") or "").strip()
    if status == DAILY_LIMIT_STATUS:
        raise OpenDartDailyLimitError("OpenDART daily request limit reached (status 020)")
    if status:
        raise OpenDartFundamentalsError(
            f"OpenDART corporation-map status is not successful: {status!r}; message={message!r}"
        )


def parse_financial_statement(
    body: bytes,
    *,
    expected_corp_code: str,
    expected_year: int | str,
    expected_report_code: str,
    requested_fs_div: str,
) -> tuple[str, list[dict[str, object]]]:
    """Validate one captured all-accounts payload and bind its request scope."""
    request = financial_statement_request(
        expected_corp_code, expected_year, expected_report_code, requested_fs_div,
    )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenDartFundamentalsError("financial response is not UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise OpenDartFundamentalsError("financial response root must be an object")
    status = payload.get("status")
    message = payload.get("message")
    if not isinstance(status, str) or not isinstance(message, str):
        raise OpenDartFundamentalsError("financial response status/message is missing")
    if status == DAILY_LIMIT_STATUS:
        raise OpenDartDailyLimitError("OpenDART daily request limit reached (status 020)")
    if status == NO_DATA_STATUS:
        return "VALID_EMPTY", []
    if status != SUCCESS_STATUS:
        raise OpenDartFundamentalsError(
            f"OpenDART financial status is not successful: {status!r}; message={message!r}"
        )
    items = payload.get("list")
    if not isinstance(items, list) or not items:
        raise OpenDartFundamentalsError("successful financial response list must be non-empty")
    rows: list[dict[str, object]] = []
    for ordinal, item in enumerate(items):
        if not isinstance(item, dict):
            raise OpenDartFundamentalsError("financial response list item must be an object")
        missing = [field for field in _REQUIRED_ACCOUNT_FIELDS if field not in item]
        if missing:
            raise OpenDartFundamentalsError(f"documented financial fields missing: {missing!r}")
        if (item["corp_code"] != request.public_parameters["corp_code"]
                or str(item["bsns_year"]) != request.public_parameters["bsns_year"]
                or item["reprt_code"] != request.public_parameters["reprt_code"]):
            raise OpenDartFundamentalsError("financial response identity differs from request")
        if "fs_div" in item and item["fs_div"] not in (None, "", requested_fs_div):
            raise OpenDartFundamentalsError("financial response fs_div differs from request scope")
        receipt = item["rcept_no"]
        if not isinstance(receipt, str) or not _RECEIPT.fullmatch(receipt):
            raise OpenDartFundamentalsError("rcept_no must be exactly 14 digits")
        if item["sj_div"] not in {"BS", "IS", "CIS", "CF", "SCE"}:
            raise OpenDartFundamentalsError("financial response sj_div is unsupported")
        row = dict(item)
        row["fs_div"] = requested_fs_div
        row["source_item_ordinal"] = ordinal
        rows.append(row)
    return "SUCCESS", rows


def normalize_quarter(
    *,
    symbol: str,
    rows: Sequence[Mapping[str, object]],
    retrieved_at: str,
    q3_rows: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Map one response scope/receipt to one normalized quarterly row.

    Annual income values are de-cumulated with the same year's Q3 cumulative
    values. Balance-sheet values always use the point-in-time current amount.
    """
    if not _STOCK_CODE.fullmatch(symbol):
        raise OpenDartFundamentalsError("symbol must be exactly six digits")
    if not rows:
        raise OpenDartFundamentalsError("financial rows are required")
    identity = {
        (str(row["corp_code"]), str(row["bsns_year"]), str(row["reprt_code"]),
         str(row["fs_div"]), str(row["rcept_no"]))
        for row in rows
    }
    if len(identity) != 1:
        raise OpenDartFundamentalsError("financial rows mix request or filing identities")
    corp_code, year_text, reprt_code, fs_div, receipt = next(iter(identity))
    if reprt_code not in REPORT_CODES or fs_div not in STATEMENT_SCOPES:
        raise OpenDartFundamentalsError("financial identity contains unsupported code")
    retrieved = _aware_utc(retrieved_at)
    selected: dict[str, Mapping[str, object] | None] = {
        metric: _select_account(rows, metric) for metric in _ACCOUNT_IDS
    }
    _require_one_currency([row for row in selected.values() if row is not None])
    result: dict[str, object] = {
        "symbol": symbol,
        "corp_code": corp_code,
        "bsns_year": int(year_text),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
        "period_end": period_end(int(year_text), reprt_code).isoformat(),
        "rcept_no": receipt,
        "retrieved_at": retrieved.isoformat(),
        "source_terms_ref": OPEN_DART_TERMS_URL,
    }
    for metric in _ACCOUNT_IDS:
        row = selected[metric]
        value = _amount(row.get("thstrm_amount")) if row is not None else None
        if reprt_code == "11011" and metric in _INCOME_METRICS:
            q3_row = _select_account(q3_rows or (), metric)
            if row is None or q3_row is None:
                value = None
            else:
                _require_one_currency([row, q3_row])
                cumulative = _amount(q3_row.get("thstrm_add_amount"))
                value = None if value is None or cumulative is None else value - cumulative
        result[metric] = value
    equity = result["total_equity"]
    liabilities = result["total_liabilities"]
    result["debt_ratio_pct"] = (
        None if equity is None or liabilities is None or int(equity) <= 0
        else float(int(liabilities) / int(equity) * 100.0)
    )
    return result


def period_end(bsns_year: int, reprt_code: str) -> date:
    """Return the explicit calendar-quarter project convention (UNVERIFIED)."""
    month_day = {"11013": (3, 31), "11012": (6, 30), "11014": (9, 30), "11011": (12, 31)}
    if reprt_code not in month_day or bsns_year < 2015:
        raise OpenDartFundamentalsError("unsupported business year/report code")
    month, day = month_day[reprt_code]
    return date(bsns_year, month, day)


def _select_account(
    rows: Iterable[Mapping[str, object]], metric: str,
) -> Mapping[str, object] | None:
    if metric not in _ACCOUNT_IDS:
        raise OpenDartFundamentalsError(f"unknown metric: {metric}")
    allowed_statements = {"BS"} if metric in _BALANCE_METRICS else {"IS", "CIS"}
    candidates = [row for row in rows if str(row.get("sj_div")) in allowed_statements]
    if metric in _INCOME_METRICS:
        is_rows = [row for row in candidates if row.get("sj_div") == "IS"]
        candidates = is_rows or [row for row in candidates if row.get("sj_div") == "CIS"]
    ids = _ACCOUNT_IDS[metric]
    names = _ACCOUNT_NAMES[metric]
    ranked = []
    for row in candidates:
        account_id = str(row.get("account_id") or "")
        account_name = str(row.get("account_nm") or "").strip()
        if account_id in ids:
            match_rank = (0, ids.index(account_id))
        elif account_name in names:
            match_rank = (1, names.index(account_name))
        else:
            continue
        detail_rank = 0 if not str(row.get("account_detail") or "").strip() else 1
        try:
            order = int(str(row.get("ord") or "999999").replace(",", ""))
        except ValueError:
            order = 999999
        ranked.append((match_rank, detail_rank, order, int(row.get("source_item_ordinal", 0)), row))
    return min(ranked, key=lambda item: item[:-1])[-1] if ranked else None


def _amount(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        parsed = int(text)
    except ValueError as error:
        raise OpenDartFundamentalsError(f"financial amount is not an integer: {value!r}") from error
    return -parsed if negative else parsed


def _require_one_currency(rows: Iterable[Mapping[str, object]]) -> None:
    materialized = list(rows)
    if not materialized:
        return
    currencies = {str(row.get("currency") or "").strip() for row in materialized}
    currencies.discard("")
    if len(currencies) != 1:
        raise OpenDartFundamentalsError(
            "selected financial accounts have missing or mixed currencies"
        )


def _aware_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise OpenDartFundamentalsError("retrieved_at must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OpenDartFundamentalsError("retrieved_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


__all__ = [
    "CORP_CODE_URL", "DAILY_LIMIT_STATUS", "DOCUMENTED_DAILY_LIMIT",
    "FINANCIAL_STATEMENT_URL", "NO_DATA_STATUS", "OpenDartDailyLimitError",
    "OpenDartFundamentalsError", "OpenDartRequest", "REPORT_CODES",
    "STATEMENT_SCOPES", "corp_code_request", "financial_statement_request",
    "normalize_quarter", "parse_corp_code_zip", "parse_financial_statement",
    "period_end",
]
