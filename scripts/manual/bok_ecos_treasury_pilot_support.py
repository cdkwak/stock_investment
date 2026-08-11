"""Offline-safe support for a bounded BOK ECOS Treasury diagnostic pilot.

No table or item identity is embedded here.  D must supply codes and exact
labels copied from reviewed ECOS metadata.  This module performs no I/O on
import and contains no implicit live mode.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import quote


BASE_URL = "https://ecos.bok.or.kr"
ITEM_LIST_OPERATION = "StatisticItemList"
VALUE_OPERATION = "StatisticSearch"
TENORS = ("2Y", "3Y", "5Y", "10Y", "20Y", "30Y")
VALUE_TENORS = ("2Y", "3Y")
DATE_ROLES = (
    "recent_normal",
    "two_year_introduction_boundary",
    "retained_source_gap",
    "early_2019",
)
MAX_METADATA_REQUESTS = 1
MAX_VALUE_REQUESTS = 8
MAX_VALUE_OBSERVATIONS = 16


class EcosPilotError(RuntimeError):
    pass


@dataclass(frozen=True)
class TenorIdentity:
    item_code: str
    item_name: str
    unit_name: str


@dataclass(frozen=True)
class PilotConfig:
    table_code: str
    table_name: str
    cycle: str
    tenors: Mapping[str, TenorIdentity]
    dates: Mapping[str, str]


@dataclass(frozen=True)
class ValueScope:
    tenor: str
    source_date: str
    role: str

    @property
    def scope_id(self) -> str:
        return f"{self.source_date}_{self.tenor}"


@dataclass(frozen=True)
class ParsedValue:
    classification: str
    observations: tuple[dict[str, object], ...]


def _code(value: object, field: str) -> str:
    text = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        raise EcosPilotError(f"{field} is not an explicit ECOS code")
    if any(word in text.lower() for word in ("placeholder", "example", "from_review")):
        raise EcosPilotError(f"{field} is still a placeholder")
    return text


def load_config(path: Path) -> PilotConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EcosPilotError("pilot config is not readable JSON") from error
    if not isinstance(payload, dict):
        raise EcosPilotError("pilot config root must be an object")
    if set(payload) != {"table_code", "table_name", "cycle", "tenors", "dates"}:
        raise EcosPilotError("pilot config fields differ from the reviewed schema")
    table_code = _code(payload["table_code"], "table_code")
    table_name = str(payload["table_name"]).strip()
    if not table_name:
        raise EcosPilotError("table_name must be copied from ECOS metadata")
    cycle = str(payload["cycle"]).strip()
    if cycle != "D":
        raise EcosPilotError("Treasury pilot requires a documented daily ECOS table")
    raw_tenors = payload["tenors"]
    if not isinstance(raw_tenors, dict) or tuple(raw_tenors) != TENORS:
        raise EcosPilotError("all six tenors must be supplied in canonical order")
    tenors: dict[str, TenorIdentity] = {}
    for tenor in TENORS:
        raw = raw_tenors[tenor]
        if not isinstance(raw, dict) or set(raw) != {"item_code", "item_name", "unit_name"}:
            raise EcosPilotError(f"{tenor} identity fields are incomplete")
        name = str(raw["item_name"]).strip()
        unit = str(raw["unit_name"]).strip()
        if not name or not unit:
            raise EcosPilotError(f"{tenor} identity labels must be explicit")
        tenors[tenor] = TenorIdentity(_code(raw["item_code"], f"{tenor}.item_code"), name, unit)
    if len({value.item_code for value in tenors.values()}) != len(TENORS):
        raise EcosPilotError("six-tenor item codes must be unique")
    raw_dates = payload["dates"]
    if not isinstance(raw_dates, dict) or tuple(raw_dates) != DATE_ROLES:
        raise EcosPilotError("four diagnostic date roles must be supplied in canonical order")
    dates: dict[str, str] = {}
    for role in DATE_ROLES:
        text = str(raw_dates[role]).strip()
        if not re.fullmatch(r"\d{8}", text):
            raise EcosPilotError(f"{role} must be YYYYMMDD")
        try:
            date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
        except ValueError as error:
            raise EcosPilotError(f"{role} is not a calendar date") from error
        dates[role] = text
    if len(set(dates.values())) != len(DATE_ROLES):
        raise EcosPilotError("diagnostic dates must be unique")
    return PilotConfig(table_code, table_name, cycle, tenors, dates)


def config_sha256(config: PilotConfig) -> str:
    payload = {
        "table_code": config.table_code,
        "table_name": config.table_name,
        "cycle": config.cycle,
        "tenors": {
            tenor: vars(config.tenors[tenor]) for tenor in TENORS
        },
        "dates": {role: config.dates[role] for role in DATE_ROLES},
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def item_list_url(api_key: str, config: PilotConfig) -> str:
    return (
        f"{BASE_URL}/api/{ITEM_LIST_OPERATION}/{quote(api_key, safe='')}/json/kr/"
        f"1/10000/{quote(config.table_code, safe='')}/"
    )


def value_url(api_key: str, config: PilotConfig, scope: ValueScope) -> str:
    item = config.tenors[scope.tenor]
    # ECOS StatisticSearch documents ITEM_CODE2/3 as optional trailing path
    # dimensions. This pilot supplies only the verified first item dimension.
    return (
        f"{BASE_URL}/api/{VALUE_OPERATION}/{quote(api_key, safe='')}/json/kr/1/2/"
        f"{quote(config.table_code, safe='')}/{config.cycle}/{scope.source_date}/"
        f"{scope.source_date}/{quote(item.item_code, safe='')}/"
    )


def redacted_route(operation: str, config: PilotConfig, scope: ValueScope | None = None) -> str:
    if operation == ITEM_LIST_OPERATION:
        return f"/api/{operation}/<redacted>/json/kr/1/10000/{config.table_code}/"
    if operation != VALUE_OPERATION or scope is None:
        raise EcosPilotError("invalid redacted route request")
    return (
        f"/api/{operation}/<redacted>/json/kr/1/2/{config.table_code}/D/"
        f"{scope.source_date}/{scope.source_date}/{config.tenors[scope.tenor].item_code}/"
    )


def plan_value_scopes(config: PilotConfig) -> tuple[ValueScope, ...]:
    scopes = tuple(
        ValueScope(tenor, config.dates[role], role)
        for role in DATE_ROLES
        for tenor in VALUE_TENORS
    )
    if len(scopes) != MAX_VALUE_REQUESTS or len({scope.scope_id for scope in scopes}) != len(scopes):
        raise EcosPilotError("value plan must contain exactly eight unique scopes")
    return scopes


def _json_object(body: bytes) -> dict[str, object]:
    if body.lstrip().startswith(b"<"):
        raise EcosPilotError("HTML response is not ECOS JSON")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EcosPilotError("response is not valid JSON") from error
    if not isinstance(value, dict):
        raise EcosPilotError("ECOS response root is not an object")
    return value


def _rows(body: bytes, operation: str) -> tuple[list[dict[str, object]], int]:
    payload = _json_object(body)
    block = payload.get(operation)
    if block is None:
        result = payload.get("RESULT")
        if isinstance(result, dict) and result.get("CODE") == "INFO-200":
            return [], 0
        raise EcosPilotError(f"{operation} response block is missing")
    if not isinstance(block, dict) or not isinstance(block.get("row"), list):
        raise EcosPilotError(f"{operation}.row is missing or invalid")
    try:
        total = int(block["list_total_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise EcosPilotError(f"{operation}.list_total_count is invalid") from error
    rows = block["row"]
    if any(not isinstance(row, dict) for row in rows):
        raise EcosPilotError(f"{operation} contains a non-object row")
    return rows, total


def parse_item_metadata(body: bytes, config: PilotConfig) -> tuple[dict[str, str], ...]:
    rows, total = _rows(body, ITEM_LIST_OPERATION)
    if total < len(rows):
        raise EcosPilotError("metadata total is smaller than returned rows")
    by_code = {str(row.get("ITEM_CODE", "")).strip(): row for row in rows}
    verified = []
    for tenor in TENORS:
        expected = config.tenors[tenor]
        row = by_code.get(expected.item_code)
        if row is None:
            raise EcosPilotError(f"metadata is missing configured {tenor} item")
        actual = {
            "STAT_CODE": str(row.get("STAT_CODE", "")).strip(),
            "STAT_NAME": str(row.get("STAT_NAME", "")).strip(),
            "ITEM_CODE": str(row.get("ITEM_CODE", "")).strip(),
            "ITEM_NAME": str(row.get("ITEM_NAME", "")).strip(),
            "CYCLE": str(row.get("CYCLE", "")).strip(),
            "UNIT_NAME": str(row.get("UNIT_NAME", "")).strip(),
            "START_TIME": str(row.get("START_TIME", "")).strip(),
            "END_TIME": str(row.get("END_TIME", "")).strip(),
        }
        if actual["STAT_CODE"] != config.table_code or actual["STAT_NAME"] != config.table_name:
            raise EcosPilotError(f"{tenor} table identity differs from approved config")
        if actual["ITEM_NAME"] != expected.item_name or actual["UNIT_NAME"] != expected.unit_name:
            raise EcosPilotError(f"{tenor} label/unit differs from approved config")
        if actual["CYCLE"] != config.cycle:
            raise EcosPilotError(f"{tenor} is not daily")
        if not re.fullmatch(r"\d{8}", actual["START_TIME"]):
            raise EcosPilotError(f"{tenor} metadata has no daily start date")
        if actual["END_TIME"] and not re.fullmatch(r"\d{8}", actual["END_TIME"]):
            raise EcosPilotError(f"{tenor} metadata end date is invalid")
        verified.append({"tenor": tenor, **actual})
    return tuple(verified)


def parse_value(body: bytes, config: PilotConfig, scope: ValueScope) -> ParsedValue:
    rows, total = _rows(body, VALUE_OPERATION)
    if not rows and total == 0:
        return ParsedValue("VALID_EMPTY", ())
    if total != len(rows) or total > 2:
        raise EcosPilotError("value response exceeds the two-observation scope cap")
    expected = config.tenors[scope.tenor]
    observations = []
    for row in rows:
        required = {"STAT_CODE", "STAT_NAME", "ITEM_CODE1", "ITEM_NAME1", "UNIT_NAME", "TIME", "DATA_VALUE"}
        if not required.issubset(row):
            raise EcosPilotError("value row is missing documented fields")
        if str(row["STAT_CODE"]).strip() != config.table_code or str(row["STAT_NAME"]).strip() != config.table_name:
            raise EcosPilotError("value table identity differs")
        if str(row["ITEM_CODE1"]).strip() != expected.item_code or str(row["ITEM_NAME1"]).strip() != expected.item_name:
            raise EcosPilotError("value tenor identity differs")
        if str(row["UNIT_NAME"]).strip() != expected.unit_name:
            raise EcosPilotError("value unit differs")
        if str(row["TIME"]).strip() != scope.source_date:
            raise EcosPilotError("value source date differs from bounded scope")
        try:
            value = Decimal(str(row["DATA_VALUE"]).strip())
        except InvalidOperation as error:
            raise EcosPilotError("DATA_VALUE is not decimal") from error
        if not value.is_finite():
            raise EcosPilotError("DATA_VALUE is not finite")
        observations.append(
            {
                "source": "bok_ecos",
                "source_operation": VALUE_OPERATION,
                "source_table_code": config.table_code,
                "source_item_code": expected.item_code,
                "source_item_name": expected.item_name,
                "source_date": scope.source_date,
                "tenor": scope.tenor,
                "unit_name": expected.unit_name,
                "value": str(value),
                "published_at": None,
                "revision_id": None,
                "availability_status": "blocked_unknown_first_publication_and_revision",
            }
        )
    return ParsedValue("SUCCESS", tuple(observations))

