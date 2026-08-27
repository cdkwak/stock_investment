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
FINALITY_MAX_VALUE_REQUESTS = len(TENORS)
FINALITY_MAX_ROWS_PER_REQUEST = 32
FINALITY_TABLE_CODE = "817Y002"
FINALITY_TABLE_NAME = "1.3.2.1. 시장금리(일별)"
FINALITY_UNIT_NAME = "연%"
FINALITY_UI_TRANSACTION = "OSUUA02R03"


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


@dataclass(frozen=True)
class FinalityValueScope:
    tenor: str
    start_date: str
    end_date: str

    @property
    def scope_id(self) -> str:
        return f"{self.start_date}_{self.end_date}_{self.tenor}"


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


def finality_value_url(
    api_key: str, config: PilotConfig, scope: FinalityValueScope,
) -> str:
    item = config.tenors[scope.tenor]
    return (
        f"{BASE_URL}/api/{VALUE_OPERATION}/{quote(api_key, safe='')}/json/kr/"
        f"1/{FINALITY_MAX_ROWS_PER_REQUEST}/{quote(config.table_code, safe='')}/"
        f"{config.cycle}/{scope.start_date}/{scope.end_date}/"
        f"{quote(item.item_code, safe='')}/"
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


def finality_redacted_route(config: PilotConfig, scope: FinalityValueScope) -> str:
    return (
        f"/api/{VALUE_OPERATION}/<redacted>/json/kr/1/"
        f"{FINALITY_MAX_ROWS_PER_REQUEST}/{config.table_code}/D/"
        f"{scope.start_date}/{scope.end_date}/{config.tenors[scope.tenor].item_code}/"
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


def load_finality_config(
    metadata_summary_path: Path, *, approve_sha256: str,
) -> PilotConfig:
    """Load only the already-reviewed six-tenor identity from retained evidence."""
    try:
        body = metadata_summary_path.read_bytes()
        payload = json.loads(body)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EcosPilotError("retained metadata summary is not readable JSON") from error
    if hashlib.sha256(body).hexdigest() != approve_sha256:
        raise EcosPilotError("retained metadata approval hash differs")
    if not isinstance(payload, dict):
        raise EcosPilotError("retained metadata summary root must be an object")
    rows = payload.get("six_tenor_identity")
    if not isinstance(rows, list) or len(rows) != len(TENORS):
        raise EcosPilotError("retained metadata must contain exactly six tenor identities")
    by_name: dict[str, dict[str, object]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise EcosPilotError("retained metadata contains a non-object identity")
        name = str(raw.get("ITEM_NAME", "")).strip()
        if name in by_name:
            raise EcosPilotError("retained metadata contains a duplicate identity")
        by_name[name] = raw
    tenors: dict[str, TenorIdentity] = {}
    for tenor in TENORS:
        name = f"국고채({tenor.removesuffix('Y')}년)"
        raw = by_name.get(name)
        if raw is None:
            raise EcosPilotError(f"retained metadata is missing {tenor}")
        if (
            str(raw.get("STAT_CODE", "")).strip() != FINALITY_TABLE_CODE
            or str(raw.get("STAT_NAME", "")).strip() != FINALITY_TABLE_NAME
            or str(raw.get("CYCLE", "")).strip() != "D"
            or str(raw.get("UNIT_NAME", "")).strip() != FINALITY_UNIT_NAME
        ):
            raise EcosPilotError(f"retained {tenor} identity differs from finality contract")
        tenors[tenor] = TenorIdentity(
            _code(raw.get("ITEM_CODE", ""), f"{tenor}.item_code"),
            name,
            FINALITY_UNIT_NAME,
        )
    if len({item.item_code for item in tenors.values()}) != len(TENORS):
        raise EcosPilotError("retained six-tenor item codes must be unique")
    return PilotConfig(
        FINALITY_TABLE_CODE, FINALITY_TABLE_NAME, "D", tenors, {},
    )


def plan_finality_scopes(
    config: PilotConfig, *, start_date: str, end_date: str,
) -> tuple[FinalityValueScope, ...]:
    for field, text in (("start_date", start_date), ("end_date", end_date)):
        if not re.fullmatch(r"\d{8}", text):
            raise EcosPilotError(f"{field} must be YYYYMMDD")
        try:
            date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
        except ValueError as error:
            raise EcosPilotError(f"{field} is not a calendar date") from error
    if start_date > end_date:
        raise EcosPilotError("finality range start exceeds end")
    scopes = tuple(FinalityValueScope(tenor, start_date, end_date) for tenor in TENORS)
    if len(scopes) != FINALITY_MAX_VALUE_REQUESTS:
        raise EcosPilotError("finality plan must contain exactly six tenor scopes")
    return scopes


def parse_finality_value(
    body: bytes, config: PilotConfig, scope: FinalityValueScope,
) -> tuple[dict[str, object], ...]:
    rows, total = _rows(body, VALUE_OPERATION)
    if not rows and total == 0:
        raise EcosPilotError(f"{scope.tenor} finality scope is valid-empty")
    if total != len(rows) or total > FINALITY_MAX_ROWS_PER_REQUEST:
        raise EcosPilotError("finality response count differs or exceeds the row cap")
    expected = config.tenors[scope.tenor]
    observations: list[dict[str, object]] = []
    seen_dates: set[str] = set()
    for row in rows:
        required = {
            "STAT_CODE", "STAT_NAME", "ITEM_CODE1", "ITEM_NAME1",
            "UNIT_NAME", "TIME", "DATA_VALUE",
        }
        if not isinstance(row, dict) or not required.issubset(row):
            raise EcosPilotError("finality value row is missing documented fields")
        if str(row["STAT_CODE"]).strip() != config.table_code:
            raise EcosPilotError("finality value table code differs")
        if str(row["STAT_NAME"]).strip() != config.table_name:
            raise EcosPilotError("finality value table name differs")
        if str(row["ITEM_CODE1"]).strip() != expected.item_code:
            raise EcosPilotError("finality value item code differs")
        if str(row["ITEM_NAME1"]).strip() != expected.item_name:
            raise EcosPilotError("finality value item name differs")
        if str(row["UNIT_NAME"]).strip() != expected.unit_name:
            raise EcosPilotError("finality value unit differs")
        source_date = str(row["TIME"]).strip()
        if not re.fullmatch(r"\d{8}", source_date):
            raise EcosPilotError("finality value source date is invalid")
        if not scope.start_date <= source_date <= scope.end_date:
            raise EcosPilotError("finality value source date is outside the bounded range")
        if source_date in seen_dates:
            raise EcosPilotError("finality value contains a duplicate source date")
        seen_dates.add(source_date)
        try:
            value = Decimal(str(row["DATA_VALUE"]).strip())
        except InvalidOperation as error:
            raise EcosPilotError("finality DATA_VALUE is not decimal") from error
        if not value.is_finite():
            raise EcosPilotError("finality DATA_VALUE is not finite")
        observations.append(
            {
                "source_table_code": config.table_code,
                "source_table_name": config.table_name,
                "source_item_code": expected.item_code,
                "source_item_name": expected.item_name,
                "source_date": source_date,
                "tenor": scope.tenor,
                "unit_name": expected.unit_name,
                "value": str(value),
            }
        )
    return tuple(sorted(observations, key=lambda row: str(row["source_date"])))


def parse_finality_ui_marker(body: bytes) -> dict[str, str]:
    payload = _json_object(body)
    header = payload.get("header")
    data = payload.get("data")
    if not isinstance(header, dict) or str(header.get("rspnDvsnCd")) != "0":
        raise EcosPilotError("official UI response status is not successful")
    if not isinstance(data, dict) or not isinstance(data.get("dsInfoList"), list):
        raise EcosPilotError("official UI table information is missing")
    rows = data["dsInfoList"]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise EcosPilotError("official UI must return exactly one table information row")
    row = rows[0]
    if row.get("dsId") != FINALITY_TABLE_CODE or row.get("dsNm") != FINALITY_TABLE_NAME:
        raise EcosPilotError("official UI table identity differs")
    provisional = str(row.get("prvsMrkYn", "")).strip()
    breaking = str(row.get("brknwsMrkYn", "")).strip()
    if provisional not in {"Y", "N"} or breaking not in {"Y", "N"}:
        raise EcosPilotError("official UI marker flags are invalid")
    return {
        "source_table_code": FINALITY_TABLE_CODE,
        "provisional_marker_flag": provisional,
        "breaking_marker_flag": breaking,
    }


def select_finality_target(
    observations: Mapping[str, tuple[dict[str, object], ...]],
) -> str:
    if tuple(observations) != TENORS:
        raise EcosPilotError("finality observations must contain six tenors in contract order")
    common: set[str] | None = None
    for tenor in TENORS:
        dates = {str(row["source_date"]) for row in observations[tenor]}
        common = dates if common is None else common.intersection(dates)
    if not common:
        raise EcosPilotError("six tenors have no common provider-native date")
    return max(common)
