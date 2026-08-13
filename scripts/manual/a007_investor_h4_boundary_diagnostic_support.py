"""Frozen two-date H4 boundary-pair diagnostic plan."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Mapping

import pyarrow.parquet as pq

from scripts.manual.a007_investor_range_diagnostic_support import DiagnosticClassification, SOURCE_FIELDS
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped

PYKRX_VERSION = "1.2.8"
BUSINESS_BLD = "dbms/MDC/STAT/srt/MDCSTAT30301"
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"
MAX_BUSINESS_REQUESTS = 1
MAX_RAW_HTTP_REQUESTS = EXPECTED_RAW_HTTP_REQUESTS = 6
REQUIRE_ZERO_RETRY_AUTH_SESSION = True
SCOPE_ID = "20170519_20170522_KOSPI_volume_H4_boundary_pair"
SCOPE = {"strtDd": "20170519", "endDd": "20170522", "inqCondTpCd": 1, "mktTpCd": 1}
EXPECTED_BUSINESS_DATA = {"bld": BUSINESS_BLD, "strtDd": "20170519", "endDd": "20170522", "inqCondTpCd": "1", "mktTpCd": "1"}
EXPECTED_DATE_COUNT = 2
EXPECTED_DATE_SHA256 = "a8e1c5b7be734fb70104c2a93405a36610ccd9dbef05e85cb3bf55789ececfd1"
CANONICAL_DATE_SOURCES = ({
    "path": "data/published/kr_equity_canonical_universe_daily/market=KOSPI/year=2017/data.parquet",
    "bytes": 449053, "sha256": "fad22bfd52c0513b4710d13470927cfc766589d6bce9fdb8c5494c795c30b7bd",
},)


def _digest(dates: tuple[str, ...]) -> str:
    return hashlib.sha256(("\n".join(dates) + "\n").encode()).hexdigest()


def expected_dates(project_root: Path) -> tuple[str, ...]:
    root = project_root.resolve(); source = CANONICAL_DATE_SOURCES[0]; path = (root / source["path"]).resolve()
    try: path.relative_to(root)
    except ValueError as error: raise PilotStopped("CANONICAL_DATE_SOURCE_PATH_ESCAPE") from error
    if not path.is_file() or path.is_symlink(): raise PilotStopped("CANONICAL_DATE_SOURCE_MISSING")
    raw = path.read_bytes()
    if len(raw) != source["bytes"] or hashlib.sha256(raw).hexdigest() != source["sha256"]: raise PilotStopped("CANONICAL_DATE_SOURCE_CHANGED")
    values = pq.read_table(path, columns=["date"])["date"].to_pylist()
    dates = tuple(sorted({str(v).replace("-", "") for v in values if SCOPE["strtDd"] <= str(v).replace("-", "") <= SCOPE["endDd"]}))
    if len(dates) != 2 or dates != ("20170519", "20170522") or _digest(dates) != EXPECTED_DATE_SHA256: raise PilotStopped("CANONICAL_EXPECTED_DATE_SET_MISMATCH")
    return dates


def scope_sha256(dates: tuple[str, ...]) -> str:
    payload = {"bld": BUSINESS_BLD, "expected_date_count": 2, "expected_date_sha256": _digest(dates), "scope": SCOPE, "scope_id": SCOPE_ID}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def manifest_payload(*, run_id: str, created_at_utc: str, dates: tuple[str, ...]) -> dict[str, object]:
    if len(dates) != 2 or _digest(dates) != EXPECTED_DATE_SHA256: raise PilotStopped("CANONICAL_EXPECTED_DATE_SET_MISMATCH")
    return {"business_request_limit": 1, "canonical_date_sources": [dict(x) for x in CANONICAL_DATE_SOURCES], "checkpoint_writes": False,
        "created_at_utc": created_at_utc, "expected_date_count": 2, "expected_date_sha256": EXPECTED_DATE_SHA256, "expected_dates": list(dates),
        "normalized_writes": False, "parallelism": 1, "purpose": "diagnose_MDCSTAT30301_H4_boundary_pair", "pykrx_version": PYKRX_VERSION,
        "raw_http_request_limit": 6, "raw_http_requests_expected": 6, "retry_count": 0, "run_id": run_id, "scope": dict(SCOPE),
        "scope_id": SCOPE_ID, "scope_sha256": scope_sha256(dates), "version": 1}


def _integer(value: object, field: str) -> int:
    text = str(value).replace(",", "").strip()
    if not text or text.startswith("+") or not text.lstrip("-").isdigit(): raise PilotStopped(f"INVALID_INTEGER:{field}")
    parsed = int(text)
    if parsed < 0: raise PilotStopped(f"NEGATIVE_VALUE:{field}")
    return parsed


def classify_response(body: bytes, dates: tuple[str, ...]) -> DiagnosticClassification:
    if body.lstrip().startswith(b"<"): raise PilotStopped("HTML_OR_RESTRICTION_RESPONSE")
    try: payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error: raise PilotStopped("NON_JSON_RESPONSE") from error
    if not isinstance(payload, dict) or set(payload) not in ({"OutBlock_1"}, {"OutBlock_1", "CURRENT_DATETIME"}): raise PilotStopped("TOP_LEVEL_SCHEMA_MISMATCH")
    current = payload.get("CURRENT_DATETIME")
    if current is not None:
        try: datetime.strptime(current, "%Y.%m.%d %p %I:%M:%S")
        except (TypeError, ValueError) as error: raise PilotStopped("CURRENT_DATETIME_INVALID") from error
    rows = payload["OutBlock_1"]
    if not isinstance(rows, list) or not rows: raise PilotStopped("ANOMALOUS_EMPTY_RANGE")
    observed=[]; totals=[]
    for index,row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != set(SOURCE_FIELDS): raise PilotStopped(f"SCHEMA_MISMATCH:{index}")
        try: day=datetime.strptime(str(row["TRD_DD"]).strip(), "%Y/%m/%d").strftime("%Y%m%d")
        except ValueError as error: raise PilotStopped(f"INVALID_DATE:{index}") from error
        if day not in dates: raise PilotStopped(f"OUT_OF_SCOPE_DATE:{day}")
        components=[_integer(row[f"STR_CONST_VAL{i}"],f"STR_CONST_VAL{i}") for i in range(1,5)]; total=_integer(row["STR_CONST_VAL5"],"STR_CONST_VAL5")
        if sum(components)!=total: raise PilotStopped(f"INVESTOR_TOTAL_MISMATCH:{day}")
        observed.append(day); totals.append(total)
    if len(set(observed))!=len(observed): raise PilotStopped("DUPLICATE_SOURCE_DATE")
    actual=set(observed)
    if actual==set(dates) and len(rows)==2 and all(v>0 for v in totals):
        return DiagnosticClassification("RANGE_WINDOW_EFFECT",2,tuple(sorted(observed)),2,current)
    if actual=={"20170522"} and len(rows)==1 and totals[0]>0:
        return DiagnosticClassification("BOUNDARY_SHAPED_CONFIRMED",1,("20170522",),1,current)
    raise PilotStopped(f"AMBIGUOUS_STOP:{len(actual)}/2")
