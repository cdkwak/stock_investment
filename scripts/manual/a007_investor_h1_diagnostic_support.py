"""Frozen offline plan for the one-call Investor H1 availability diagnostic."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Mapping

import pyarrow.parquet as pq

from scripts.manual.a007_investor_range_diagnostic_support import (
    DiagnosticClassification,
    SOURCE_FIELDS,
)
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped


PYKRX_VERSION = "1.2.8"
BUSINESS_BLD = "dbms/MDC/STAT/srt/MDCSTAT30301"
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"
MAX_BUSINESS_REQUESTS = 1
MAX_RAW_HTTP_REQUESTS = 6
EXPECTED_RAW_HTTP_REQUESTS = 6
REQUIRE_ZERO_RETRY_AUTH_SESSION = True
SCOPE_ID = "20100104_20120104_KOSPI_volume_H1_availability"
SCOPE = {"strtDd": "20100104", "endDd": "20120104", "inqCondTpCd": 1, "mktTpCd": 1}
EXPECTED_BUSINESS_DATA = {
    "bld": BUSINESS_BLD, "strtDd": SCOPE["strtDd"], "endDd": SCOPE["endDd"],
    "inqCondTpCd": "1", "mktTpCd": "1",
}
EXPECTED_DATE_COUNT = 502
EXPECTED_DATE_SHA256 = "4614186ad1bdaa70a8796ad96efa2b99e47990b0d7c521cb2a2c9fc5df758628"
CANONICAL_DATE_SOURCES = (
    {
        "path": "data/published/kr_equity_canonical_universe_daily/market=KOSPI/year=2010/data.parquet",
        "bytes": 481928,
        "sha256": "451bfd8c1df5ca63f83d0e09b20fe6cafa0ab1fbf8833dba3107e86fa621ba00",
    },
    {
        "path": "data/published/kr_equity_canonical_universe_daily/market=KOSPI/year=2011/data.parquet",
        "bytes": 480159,
        "sha256": "1c368324b39c60cecfa8bd63ffe9ec3e2576f9323dc000a9cb404250d7a361e5",
    },
    {
        "path": "data/published/kr_equity_canonical_universe_daily/market=KOSPI/year=2012/data.parquet",
        "bytes": 513732,
        "sha256": "d8262d22aeedc02ca0290583c7a18725481789f1fa655a06171094929e937725",
    },
)


def _date_digest(dates: tuple[str, ...]) -> str:
    return hashlib.sha256(("\n".join(dates) + "\n").encode()).hexdigest()


def expected_dates(project_root: Path) -> tuple[str, ...]:
    root = project_root.resolve()
    observed: set[str] = set()
    for source in CANONICAL_DATE_SOURCES:
        path = (root / str(source["path"])).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise PilotStopped("CANONICAL_DATE_SOURCE_PATH_ESCAPE") from error
        if not path.is_file() or path.is_symlink():
            raise PilotStopped(f"CANONICAL_DATE_SOURCE_MISSING:{source['path']}")
        raw = path.read_bytes()
        if len(raw) != source["bytes"] or hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise PilotStopped(f"CANONICAL_DATE_SOURCE_CHANGED:{source['path']}")
        try:
            values = pq.read_table(path, columns=["date"])["date"].to_pylist()
        except Exception as error:
            raise PilotStopped(f"CANONICAL_DATE_SOURCE_INVALID:{source['path']}") from error
        for value in values:
            day = str(value).replace("-", "")
            if SCOPE["strtDd"] <= day <= SCOPE["endDd"]:
                observed.add(day)
    dates = tuple(sorted(observed))
    if (
        len(dates) != EXPECTED_DATE_COUNT or not dates
        or dates[0] != SCOPE["strtDd"] or dates[-1] != SCOPE["endDd"]
        or _date_digest(dates) != EXPECTED_DATE_SHA256
    ):
        raise PilotStopped("CANONICAL_EXPECTED_DATE_SET_MISMATCH")
    return dates


def scope_sha256(dates: tuple[str, ...]) -> str:
    payload = {
        "bld": BUSINESS_BLD, "expected_date_count": EXPECTED_DATE_COUNT,
        "expected_date_sha256": _date_digest(dates), "scope": SCOPE, "scope_id": SCOPE_ID,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def manifest_payload(*, run_id: str, created_at_utc: str, dates: tuple[str, ...]) -> dict[str, object]:
    if len(dates) != EXPECTED_DATE_COUNT or _date_digest(dates) != EXPECTED_DATE_SHA256:
        raise PilotStopped("CANONICAL_EXPECTED_DATE_SET_MISMATCH")
    return {
        "business_request_limit": 1, "canonical_date_sources": [dict(value) for value in CANONICAL_DATE_SOURCES],
        "checkpoint_writes": False, "created_at_utc": created_at_utc,
        "expected_date_count": EXPECTED_DATE_COUNT, "expected_date_sha256": EXPECTED_DATE_SHA256,
        "expected_dates": list(dates), "normalized_writes": False, "parallelism": 1,
        "purpose": "diagnose_MDCSTAT30301_2010_2012_historical_availability",
        "pykrx_version": PYKRX_VERSION, "raw_http_request_limit": 6,
        "raw_http_requests_expected": 6, "retry_count": 0, "run_id": run_id,
        "scope": dict(SCOPE), "scope_id": SCOPE_ID, "scope_sha256": scope_sha256(dates),
        "version": 1,
    }


def _integer(value: object, field: str) -> int:
    text = str(value).replace(",", "").strip()
    if not text or text.startswith("+") or not text.lstrip("-").isdigit():
        raise PilotStopped(f"INVALID_INTEGER:{field}")
    parsed = int(text)
    if parsed < 0:
        raise PilotStopped(f"NEGATIVE_VALUE:{field}")
    return parsed


def classify_response(body: bytes, dates: tuple[str, ...]) -> DiagnosticClassification:
    if body.lstrip().startswith(b"<"):
        raise PilotStopped("HTML_OR_RESTRICTION_RESPONSE")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PilotStopped("NON_JSON_RESPONSE") from error
    if not isinstance(payload, dict) or set(payload) not in (
        {"OutBlock_1"}, {"OutBlock_1", "CURRENT_DATETIME"},
    ):
        raise PilotStopped("TOP_LEVEL_SCHEMA_MISMATCH")
    source_current_datetime = payload.get("CURRENT_DATETIME")
    if source_current_datetime is not None:
        if not isinstance(source_current_datetime, str):
            raise PilotStopped("CURRENT_DATETIME_INVALID")
        try:
            datetime.strptime(source_current_datetime, "%Y.%m.%d %p %I:%M:%S")
        except ValueError as error:
            raise PilotStopped("CURRENT_DATETIME_INVALID") from error
    rows = payload["OutBlock_1"]
    if not isinstance(rows, list) or not rows:
        raise PilotStopped("ANOMALOUS_EMPTY_RANGE")
    observed: list[str] = []
    positive = 0
    totals: list[int] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise PilotStopped(f"INVALID_ROW:{index}")
        missing = sorted(set(SOURCE_FIELDS) - set(row))
        extra = sorted(set(row) - set(SOURCE_FIELDS))
        if missing or extra:
            detail = ",".join(missing) if missing else "extra=" + ",".join(extra)
            raise PilotStopped(f"SCHEMA_MISMATCH:{index}:{detail}")
        try:
            day = datetime.strptime(str(row["TRD_DD"]).strip(), "%Y/%m/%d").strftime("%Y%m%d")
        except ValueError as error:
            raise PilotStopped(f"INVALID_DATE:{index}") from error
        if not SCOPE["strtDd"] <= day <= SCOPE["endDd"]:
            raise PilotStopped(f"OUT_OF_SCOPE_DATE:{day}")
        components = [_integer(row[field], field) for field in SOURCE_FIELDS[1:5]]
        total = _integer(row["STR_CONST_VAL5"], "STR_CONST_VAL5")
        if sum(components) != total:
            raise PilotStopped(f"INVESTOR_TOTAL_MISMATCH:{day}")
        observed.append(day)
        totals.append(total)
        positive += int(total > 0)
    if len(set(observed)) != len(observed):
        raise PilotStopped("DUPLICATE_SOURCE_DATE")
    actual = set(observed)
    if actual == set(dates) and len(rows) == len(dates):
        return DiagnosticClassification(
            "H1_FULL_RANGE_AVAILABLE", len(rows), tuple(sorted(observed)), positive,
            source_current_datetime,
        )
    if len(rows) == 1 and observed[0] == SCOPE["endDd"] and totals[0] == 0:
        return DiagnosticClassification(
            "PRE_AVAILABILITY_COLLAPSE", 1, (observed[0],), 0, source_current_datetime,
        )
    raise PilotStopped(f"AMBIGUOUS_STOP:{len(actual)}/{len(dates)}")
