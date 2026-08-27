"""Pure parsing and cursor rules for OpenDART action-source observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import re
from typing import Iterable
from zoneinfo import ZoneInfo


class OpenDartIntakeError(ValueError):
    pass


_RECEIPT = re.compile(r"\d{14}\Z")
_CORP_CODE = re.compile(r"\d{8}\Z")
_STOCK_CODE = re.compile(r"\d{6}\Z")
_DAY = re.compile(r"\d{8}\Z")
_LIST_FIELDS = (
    "corp_cls", "corp_name", "corp_code", "stock_code", "report_nm",
    "rcept_no", "flr_nm", "rcept_dt", "rm",
)


@dataclass(frozen=True, order=True)
class FilingCursor:
    receipt_date: str
    receipt_no: str

    def __post_init__(self) -> None:
        if not _DAY.fullmatch(self.receipt_date) or not _RECEIPT.fullmatch(self.receipt_no):
            raise OpenDartIntakeError("cursor requires YYYYMMDD and a 14-digit receipt")
        datetime.strptime(self.receipt_date, "%Y%m%d")


@dataclass(frozen=True)
class ParsedListPage:
    page_no: int
    page_count: int
    total_count: int
    total_page: int
    body_sha256: str
    rows: tuple[dict[str, object], ...]


_EVENT_KEYWORDS = (
    ("bonus_free_issue", ("무상증자",)),
    ("rights_issue", ("유상증자",)),
    ("capital_reduction", ("감자",)),
    ("merger", ("합병",)),
    ("company_division", ("회사분할", "분할")),
    ("share_split_consolidation", ("주식분할", "주식병합", "액면분할", "액면병합")),
    ("cash_dividend", ("현금·현물배당", "현금배당", "배당")),
)


def classify_report_name(report_name: str) -> str:
    if not isinstance(report_name, str):
        return "unsupported"
    for family, keywords in _EVENT_KEYWORDS:
        if any(keyword in report_name for keyword in keywords):
            return family
    return "unsupported"


def parse_list_page(body: bytes, *, captured_at_utc: str) -> ParsedListPage:
    try:
        captured = datetime.fromisoformat(captured_at_utc.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise OpenDartIntakeError("capture time is invalid") from error
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise OpenDartIntakeError("capture time must be timezone-aware")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenDartIntakeError("list response is not UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise OpenDartIntakeError("list response root must be an object")
    status = payload.get("status")
    if status == "013":
        return ParsedListPage(1, 100, 0, 0, hashlib.sha256(body).hexdigest(), ())
    if status != "000" or not isinstance(payload.get("list"), list):
        raise OpenDartIntakeError("list response status/schema failed")
    try:
        page_no = int(payload["page_no"])
        page_count = int(payload["page_count"])
        total_count = int(payload["total_count"])
        total_page = int(payload["total_page"])
    except (KeyError, TypeError, ValueError) as error:
        raise OpenDartIntakeError("pagination metadata is invalid") from error
    items = payload["list"]
    if page_no < 1 or page_count < 1 or total_count < len(items) or total_page < 1:
        raise OpenDartIntakeError("pagination values are inconsistent")
    digest = hashlib.sha256(body).hexdigest()
    rows: list[dict[str, object]] = []
    for ordinal, item in enumerate(items):
        if not isinstance(item, dict) or any(field not in item for field in _LIST_FIELDS):
            raise OpenDartIntakeError("documented list fields are missing")
        receipt = item["rcept_no"]
        corp_code = item["corp_code"]
        receipt_date = item["rcept_dt"]
        stock_code = item["stock_code"]
        if not isinstance(receipt, str) or not _RECEIPT.fullmatch(receipt):
            raise OpenDartIntakeError("receipt identity is invalid")
        if not isinstance(corp_code, str) or not _CORP_CODE.fullmatch(corp_code):
            raise OpenDartIntakeError("corp_code is invalid")
        if not isinstance(receipt_date, str) or not _DAY.fullmatch(receipt_date):
            raise OpenDartIntakeError("receipt date is invalid")
        datetime.strptime(receipt_date, "%Y%m%d")
        if stock_code not in (None, "") and (
            not isinstance(stock_code, str) or not _STOCK_CODE.fullmatch(stock_code)
        ):
            raise OpenDartIntakeError("stock_code is invalid")
        family = classify_report_name(str(item["report_nm"]))
        row = {field: item[field] for field in _LIST_FIELDS}
        row.update({
            "event_family": family,
            "landing_response_body_sha256": digest,
            "source_item_ordinal": ordinal,
            "captured_at_utc": captured.astimezone(timezone.utc).isoformat(),
            "revision_parent_status": "UNVERIFIED_NO_EXPLICIT_PARENT",
            "original_receipt_no": None,
            "revises_receipt_no": None,
        })
        rows.append(row)
    return ParsedListPage(
        page_no, page_count, total_count, total_page, digest, tuple(rows)
    )


def merge_pages_after_cursor(
    pages: Iterable[ParsedListPage], cursor: FilingCursor | None,
) -> tuple[tuple[dict[str, object], ...], FilingCursor | None]:
    pages = tuple(pages)
    if not pages:
        return (), cursor
    expected_pages = list(range(1, pages[0].total_page + 1))
    if [page.page_no for page in pages] != expected_pages:
        raise OpenDartIntakeError("pages are not complete and contiguous")
    if any(
        page.total_page != pages[0].total_page
        or page.total_count != pages[0].total_count
        or page.page_count != pages[0].page_count
        for page in pages
    ):
        raise OpenDartIntakeError("pagination metadata drifted across pages")
    by_receipt: dict[str, dict[str, object]] = {}
    fingerprints: dict[str, str] = {}
    for page in pages:
        for row in page.rows:
            receipt = str(row["rcept_no"])
            fingerprint = json.dumps(
                {key: row[key] for key in _LIST_FIELDS},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            if receipt in fingerprints and fingerprints[receipt] != fingerprint:
                raise OpenDartIntakeError("duplicate receipt has conflicting source fields")
            fingerprints[receipt] = fingerprint
            by_receipt.setdefault(receipt, row)
    if len(by_receipt) != pages[0].total_count:
        raise OpenDartIntakeError("deduplicated receipt count differs from total_count")
    ordered = sorted(
        by_receipt.values(), key=lambda row: (str(row["rcept_dt"]), str(row["rcept_no"]))
    )
    selected = tuple(
        row for row in ordered
        if cursor is None or FilingCursor(str(row["rcept_dt"]), str(row["rcept_no"])) > cursor
    )
    next_cursor = cursor
    if ordered:
        candidate = FilingCursor(str(ordered[-1]["rcept_dt"]), str(ordered[-1]["rcept_no"]))
        if next_cursor is None or candidate > next_cursor:
            next_cursor = candidate
    return selected, next_cursor


def conservative_availability(row: dict[str, object]) -> dict[str, str]:
    filing_day = datetime.strptime(str(row["rcept_dt"]), "%Y%m%d").date()
    usable = filing_day + timedelta(days=1)
    next_kst = datetime.combine(usable, time.min, ZoneInfo("Asia/Seoul"))
    captured = datetime.fromisoformat(str(row["captured_at_utc"]).replace("Z", "+00:00"))
    available = max(captured.astimezone(timezone.utc), next_kst.astimezone(timezone.utc))
    return {
        "observation_time_utc": captured.astimezone(timezone.utc).isoformat(),
        "available_at_utc": available.isoformat(),
        "usable_from": usable.isoformat(),
        "availability_basis": "MAX_OF_RETAINED_CAPTURE_AND_NEXT_CALENDAR_DATE",
    }


def identity_observation(row: dict[str, object]) -> dict[str, object]:
    digest = str(row["landing_response_body_sha256"])
    ordinal = int(row["source_item_ordinal"])
    identity = hashlib.sha256(
        f"opendart-list\0{digest}\0{ordinal}".encode("utf-8")
    ).hexdigest()
    return {
        "identity_observation_id": identity,
        "corp_code": row["corp_code"],
        "stock_code": row["stock_code"] or None,
        "market": None,
        "corp_cls": row["corp_cls"] or None,
        "security_class": None,
        "isin": None,
        "valid_from": None,
        "valid_to": None,
        "effective_date_basis": "UNAVAILABLE_FROM_OPENDART_LIST",
        "predecessor_security_id": None,
        "successor_security_id": None,
        "relationship_type": None,
        "official_event_id": None,
        "source": "OpenDART list",
        "source_receipt_no": row["rcept_no"],
        "landing_response_body_sha256": digest,
        "source_item_ordinal": ordinal,
        "observed_at_utc": row["captured_at_utc"],
        "identity_status": "CURRENT_AT_CAPTURE_EFFECTIVE_DATES_UNVERIFIED",
        "observation_year": int(str(row["captured_at_utc"])[:4]),
    }


__all__ = [
    "FilingCursor", "OpenDartIntakeError", "ParsedListPage",
    "classify_report_name", "conservative_availability", "identity_observation",
    "merge_pages_after_cursor", "parse_list_page",
]
