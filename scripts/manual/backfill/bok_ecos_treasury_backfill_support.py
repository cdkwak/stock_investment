"""Offline-safe plan and parser for the BOK ECOS Treasury backfill."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote

import pandas as pd

from stock_data.contracts.bok_ecos_treasury import (
    BOK_ECOS_TREASURY_TENORS,
    BOK_ECOS_KR_TREASURY_YIELD_SOURCE_OBSERVATION as CONTRACT,
)
from stock_data.validation.data_v1 import validate_data_v1


BASE_URL = "https://ecos.bok.or.kr"
OPERATION = "StatisticSearch"
TENORS = BOK_ECOS_TREASURY_TENORS
MAX_REQUESTS = 6
MIN_THROTTLE_SECONDS = 3.0
MAX_THROTTLE_SECONDS = 5.0
AVAILABILITY = "blocked_unknown_first_publication_and_revision"


class BackfillError(RuntimeError):
    pass


@dataclass(frozen=True)
class Scope:
    tenor: str
    maturity_years: int
    item_code: str
    item_name: str
    unit_name: str
    start_date: str
    end_date: str


@dataclass(frozen=True)
class Plan:
    dataset: str
    contract_version: int
    table_code: str
    table_name: str
    cycle: str
    metadata_summary_sha256: str
    max_rows_per_request: int
    scopes: tuple[Scope, ...]


def _date(value: object, field: str) -> str:
    text = str(value)
    if not re.fullmatch(r"\d{8}", text):
        raise BackfillError(f"{field} must be YYYYMMDD")
    try:
        date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
    except ValueError as error:
        raise BackfillError(f"{field} is not a date") from error
    return text


def load_plan(path: Path) -> Plan:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackfillError("backfill plan is not readable JSON") from error
    required = {
        "dataset", "contract_version", "table_code", "table_name", "cycle",
        "metadata_summary_sha256", "end_date", "max_rows_per_request", "tenors",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise BackfillError("backfill plan fields differ")
    if raw["dataset"] != CONTRACT.name or raw["contract_version"] != CONTRACT.version:
        raise BackfillError("backfill contract identity differs")
    if raw["table_code"] != "817Y002" or raw["cycle"] != "D":
        raise BackfillError("backfill source identity differs")
    if not re.fullmatch(r"[0-9a-f]{64}", str(raw["metadata_summary_sha256"])):
        raise BackfillError("metadata summary digest is invalid")
    if raw["max_rows_per_request"] != 10000:
        raise BackfillError("backfill row cap must be exactly 10000")
    tenors = raw["tenors"]
    if not isinstance(tenors, dict) or tuple(tenors) != TENORS:
        raise BackfillError("backfill requires six canonical tenors")
    end = _date(raw["end_date"], "end_date")
    scopes = []
    for tenor in TENORS:
        value = tenors[tenor]
        if not isinstance(value, dict) or set(value) != {
            "maturity_years", "item_code", "item_name", "unit_name", "start_date"
        }:
            raise BackfillError(f"{tenor} plan fields differ")
        start = _date(value["start_date"], f"{tenor}.start_date")
        if start > end or value["maturity_years"] != int(tenor[:-1]):
            raise BackfillError(f"{tenor} range or maturity differs")
        scopes.append(Scope(
            tenor, value["maturity_years"], str(value["item_code"]),
            str(value["item_name"]), str(value["unit_name"]), start, end,
        ))
    if len({scope.item_code for scope in scopes}) != MAX_REQUESTS:
        raise BackfillError("item codes are not unique")
    return Plan(
        raw["dataset"], raw["contract_version"], raw["table_code"],
        raw["table_name"], raw["cycle"], raw["metadata_summary_sha256"],
        raw["max_rows_per_request"], tuple(scopes),
    )


def plan_sha256(plan: Plan) -> str:
    payload = {
        "dataset": plan.dataset, "contract_version": plan.contract_version,
        "table_code": plan.table_code, "table_name": plan.table_name,
        "cycle": plan.cycle, "metadata_summary_sha256": plan.metadata_summary_sha256,
        "max_rows_per_request": plan.max_rows_per_request,
        "scopes": [vars(scope) for scope in plan.scopes],
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def request_url(api_key: str, plan: Plan, scope: Scope) -> str:
    return (
        f"{BASE_URL}/api/{OPERATION}/{quote(api_key, safe='')}/json/kr/1/"
        f"{plan.max_rows_per_request}/{plan.table_code}/{plan.cycle}/"
        f"{scope.start_date}/{scope.end_date}/{scope.item_code}/"
    )


def redacted_route(plan: Plan, scope: Scope) -> str:
    return (
        f"/api/{OPERATION}/<redacted>/json/kr/1/{plan.max_rows_per_request}/"
        f"{plan.table_code}/{plan.cycle}/{scope.start_date}/{scope.end_date}/"
        f"{scope.item_code}/"
    )


def parse_response(
    body: bytes, plan: Plan, scope: Scope, *, capture_id: str,
    captured_at_utc: str, landing_response_sha256: str,
) -> pd.DataFrame:
    if body.lstrip().startswith(b"<"):
        raise BackfillError("HTML response is not ECOS JSON")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackfillError("response is not valid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get(OPERATION), dict):
        raise BackfillError("StatisticSearch response block is missing")
    block = payload[OPERATION]
    rows = block.get("row")
    if not isinstance(rows, list) or not rows:
        raise BackfillError("historical response is empty")
    try:
        total = int(block["list_total_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise BackfillError("historical total count is invalid") from error
    if total != len(rows) or total > plan.max_rows_per_request:
        raise BackfillError("historical response is truncated or exceeds cap")
    output = []
    seen_dates = set()
    for ordinal, row in enumerate(rows):
        if not isinstance(row, dict):
            raise BackfillError("historical row is not an object")
        required = {
            "STAT_CODE", "STAT_NAME", "ITEM_CODE1", "ITEM_NAME1",
            "UNIT_NAME", "TIME", "DATA_VALUE",
        }
        if not required.issubset(row):
            raise BackfillError("historical row is missing documented fields")
        if (
            str(row["STAT_CODE"]).strip() != plan.table_code
            or str(row["STAT_NAME"]).strip() != plan.table_name
            or str(row["ITEM_CODE1"]).strip() != scope.item_code
            or str(row["ITEM_NAME1"]).strip() != scope.item_name
            or str(row["UNIT_NAME"]).strip() != scope.unit_name
        ):
            raise BackfillError("historical row source identity differs")
        source_date = _date(row["TIME"], "TIME")
        if not scope.start_date <= source_date <= scope.end_date or source_date in seen_dates:
            raise BackfillError("historical date is outside scope or duplicated")
        seen_dates.add(source_date)
        try:
            value = Decimal(str(row["DATA_VALUE"]).strip())
        except InvalidOperation as error:
            raise BackfillError("historical yield is not decimal") from error
        if not value.is_finite() or value < 0 or value.as_tuple().exponent < -3:
            raise BackfillError("historical yield is invalid or exceeds source precision")
        output.append({
            "date": f"{source_date[:4]}-{source_date[4:6]}-{source_date[6:]}",
            "tenor": scope.tenor, "maturity_years": scope.maturity_years,
            "yield_percent": value, "source_agency": "KOFIA",
            "distributor": "BOK_ECOS", "source_operation": OPERATION,
            "source_table_code": plan.table_code, "source_table_name": plan.table_name,
            "source_item_code": scope.item_code, "source_item_name": scope.item_name,
            "source_unit_name": scope.unit_name, "source_cycle": plan.cycle,
            "capture_id": capture_id, "captured_at_utc": captured_at_utc,
            "landing_response_sha256": landing_response_sha256,
            "source_item_ordinal": ordinal, "published_at_utc": None,
            "revision_id": None, "availability_status": AVAILABILITY,
        })
    frame = pd.DataFrame(output, columns=CONTRACT.column_names).sort_values(
        list(CONTRACT.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_data_v1(frame, CONTRACT, allow_empty=False)
    return frame
