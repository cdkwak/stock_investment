"""Offline-safe source-observation parsing for OpenDART free-issue pilots.

This module does not define a canonical corporate action or adjustment factor.
Every row is one immutable occurrence in one captured OpenDART response.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Mapping


class OpenDartObservationError(ValueError):
    """The captured response cannot support a reproducible observation."""


VALID_EMPTY_STATUS = "013"
SUCCESS_STATUS = "000"
OPERATIONS = ("list", "fricDecsn", "pifricDecsn")

LIST_FIELDS = (
    "corp_cls", "corp_name", "corp_code", "stock_code", "report_nm",
    "rcept_no", "flr_nm", "rcept_dt", "rm",
)
FRIC_FIELDS = (
    "rcept_no", "corp_cls", "corp_code", "corp_name", "nstk_ostk_cnt",
    "nstk_estk_cnt", "fv_ps", "bfic_tisstk_ostk", "bfic_tisstk_estk",
    "nstk_asstd", "nstk_ascnt_ps_ostk", "nstk_ascnt_ps_estk",
    "nstk_dividrk", "nstk_dlprd", "nstk_lstprd", "bddd", "od_a_at_t",
    "od_a_at_b", "adt_a_atn",
)
PIFRIC_FIELDS = (
    "rcept_no", "corp_cls", "corp_code", "corp_name",
    "piic_nstk_ostk_cnt", "piic_nstk_estk_cnt", "piic_fv_ps",
    "piic_bfic_tisstk_ostk", "piic_bfic_tisstk_estk", "piic_fdpp_fclt",
    "piic_fdpp_bsninh", "piic_fdpp_op", "piic_fdpp_dtrp",
    "piic_fdpp_ocsa", "piic_fdpp_etc", "piic_ic_mthn",
    "fric_nstk_ostk_cnt", "fric_nstk_estk_cnt", "fric_fv_ps",
    "fric_bfic_tisstk_ostk", "fric_bfic_tisstk_estk", "fric_nstk_asstd",
    "fric_nstk_ascnt_ps_ostk", "fric_nstk_ascnt_ps_estk",
    "fric_nstk_dividrk", "fric_nstk_dlprd", "fric_nstk_lstprd",
    "fric_bddd", "fric_od_a_at_t", "fric_od_a_at_b",
    "fric_adt_a_atn", "ssl_at", "ssl_bgd", "ssl_edd",
)

_FIELDS = {"list": LIST_FIELDS, "fricDecsn": FRIC_FIELDS, "pifricDecsn": PIFRIC_FIELDS}
_RECEIPT = re.compile(r"\d{14}\Z")
_CORP_CODE = re.compile(r"\d{8}\Z")
_DAY = re.compile(r"\d{8}\Z")


@dataclass(frozen=True)
class PilotRequest:
    sequence: int
    operation: str
    endpoint: str
    public_parameters: Mapping[str, str]


def validate_scope(corp_code: str, begin_date: str, end_date: str) -> None:
    if not _CORP_CODE.fullmatch(corp_code):
        raise OpenDartObservationError("corp_code must be exactly eight digits")
    if not _DAY.fullmatch(begin_date) or not _DAY.fullmatch(end_date):
        raise OpenDartObservationError("dates must use YYYYMMDD")
    try:
        begin = datetime.strptime(begin_date, "%Y%m%d").date()
        end = datetime.strptime(end_date, "%Y%m%d").date()
    except ValueError as error:
        raise OpenDartObservationError("date is not a calendar date") from error
    if begin < date(2015, 1, 1) or end < begin:
        raise OpenDartObservationError("pilot range must start on/after 20150101 and end on/after start")
    if (end - begin).days > 31:
        raise OpenDartObservationError("bounded pilot range cannot exceed 32 calendar days inclusive")


def request_matrix(corp_code: str, begin_date: str, end_date: str) -> tuple[PilotRequest, ...]:
    """Return the fixed three-call pilot matrix without a credential parameter."""
    validate_scope(corp_code, begin_date, end_date)
    common = {"corp_code": corp_code, "bgn_de": begin_date, "end_de": end_date}
    return (
        PilotRequest(1, "list", "https://opendart.fss.or.kr/api/list.json", {
            **common, "last_reprt_at": "N", "pblntf_ty": "B", "sort": "date",
            "sort_mth": "asc", "page_no": "1", "page_count": "100",
        }),
        PilotRequest(2, "fricDecsn", "https://opendart.fss.or.kr/api/fricDecsn.json", common),
        PilotRequest(3, "pifricDecsn", "https://opendart.fss.or.kr/api/pifricDecsn.json", common),
    )


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def parse_observations(operation: str, body: bytes, *, captured_at_utc: str) -> tuple[str, list[dict[str, object]]]:
    """Parse documented fields and preserve source null, zero, and strings."""
    if operation not in _FIELDS:
        raise OpenDartObservationError(f"unsupported operation: {operation}")
    try:
        captured = datetime.fromisoformat(captured_at_utc.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise OpenDartObservationError("captured_at_utc must be an ISO timestamp") from error
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise OpenDartObservationError("captured_at_utc must be timezone-aware")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenDartObservationError("response is not UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise OpenDartObservationError("response root must be an object")
    status = payload.get("status")
    if status == VALID_EMPTY_STATUS:
        return "VALID_EMPTY", []
    if status != SUCCESS_STATUS:
        raise OpenDartObservationError(f"OpenDART status is not successful: {status!r}")
    items = payload.get("list")
    if not isinstance(items, list):
        raise OpenDartObservationError("successful response list must be an array")
    if operation == "list":
        _validate_single_page(payload, len(items))
    digest = body_sha256(body)
    observations: list[dict[str, object]] = []
    fields = _FIELDS[operation]
    for ordinal, item in enumerate(items):
        if not isinstance(item, dict):
            raise OpenDartObservationError("response list item must be an object")
        missing = set(fields) - set(item)
        if missing:
            raise OpenDartObservationError(f"documented fields missing: {sorted(missing)!r}")
        receipt, corp_code = item["rcept_no"], item["corp_code"]
        if not isinstance(receipt, str) or not _RECEIPT.fullmatch(receipt):
            raise OpenDartObservationError("rcept_no must be a 14-digit source filing identity")
        if not isinstance(corp_code, str) or not _CORP_CODE.fullmatch(corp_code):
            raise OpenDartObservationError("response corp_code must be eight digits")
        row = {
            "source_operation": operation,
            "landing_response_body_sha256": digest,
            "source_item_ordinal": ordinal,
            "captured_at_utc": captured_at_utc,
        }
        row.update((field, item[field]) for field in fields)
        observations.append(row)
    return ("SUCCESS" if observations else "VALID_EMPTY"), observations


def _validate_single_page(payload: Mapping[str, object], item_count: int) -> None:
    required = ("page_no", "page_count", "total_count", "total_page")
    if any(field not in payload for field in required):
        raise OpenDartObservationError("list pagination metadata is incomplete")
    try:
        page_no, total_count, total_page = (
            int(payload["page_no"]), int(payload["total_count"]), int(payload["total_page"])
        )
    except (TypeError, ValueError) as error:
        raise OpenDartObservationError("list pagination metadata is invalid") from error
    if page_no != 1 or total_page > 1:
        raise OpenDartObservationError("bounded pilot refuses pagination beyond the first page")
    if total_count != item_count:
        raise OpenDartObservationError("single-page row count differs from total_count")


def response_identity(operation: str, digest: str, ordinal: int) -> tuple[str, str, int]:
    if operation not in OPERATIONS or not re.fullmatch(r"[0-9a-f]{64}", digest) or ordinal < 0:
        raise OpenDartObservationError("invalid immutable observation identity")
    return operation, digest, ordinal

