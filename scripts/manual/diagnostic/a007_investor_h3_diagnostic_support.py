"""Frozen offline plan for the one-call Investor H3 availability diagnostic."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

from scripts.manual.diagnostic.a007_investor_h1_diagnostic_support import classify_availability_response
from scripts.manual.pilot.pykrx_short_selling_pilot_support import PilotStopped


PYKRX_VERSION = "1.2.8"
BUSINESS_BLD = "dbms/MDC/STAT/srt/MDCSTAT30301"
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"
MAX_BUSINESS_REQUESTS = 1
MAX_RAW_HTTP_REQUESTS = 6
EXPECTED_RAW_HTTP_REQUESTS = 6
REQUIRE_ZERO_RETRY_AUTH_SESSION = True
SCOPE_ID = "20140106_20160106_KOSPI_volume_H3_availability"
SCOPE = {"strtDd": "20140106", "endDd": "20160106", "inqCondTpCd": 1, "mktTpCd": 1}
EXPECTED_BUSINESS_DATA = {
    "bld": BUSINESS_BLD, "strtDd": SCOPE["strtDd"], "endDd": SCOPE["endDd"],
    "inqCondTpCd": "1", "mktTpCd": "1",
}
EXPECTED_DATE_COUNT = 494
EXPECTED_DATE_SHA256 = "f7e9ea0562ab3b198d690300e8eb4faad015d56b6bea0c4d9919cf599332f28e"
CANONICAL_DATE_SOURCES = (
    {
        "path": "data/published/kr_equity_canonical_universe_daily/market=KOSPI/year=2014/data.parquet",
        "bytes": 444892,
        "sha256": "f7be6b65e7e2b9e2013ebc292725b84ce60cc9ad1f3cdccd172b2841e78d3ee8",
    },
    {
        "path": "data/published/kr_equity_canonical_universe_daily/market=KOSPI/year=2015/data.parquet",
        "bytes": 429814,
        "sha256": "bc19d4e1d81e07002c5de457858d30ddc275c867bac5aebd5cf54ec98ae7264c",
    },
    {
        "path": "data/published/kr_equity_canonical_universe_daily/market=KOSPI/year=2016/data.parquet",
        "bytes": 370066,
        "sha256": "fd8d574005292d8d7f8d37c131308e8e42df3e04b446387f517a4b4cee2054b5",
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
        "purpose": "diagnose_MDCSTAT30301_2014_2016_historical_availability",
        "pykrx_version": PYKRX_VERSION, "raw_http_request_limit": 6,
        "raw_http_requests_expected": 6, "retry_count": 0, "run_id": run_id,
        "scope": dict(SCOPE), "scope_id": SCOPE_ID, "scope_sha256": scope_sha256(dates),
        "version": 1,
    }


def classify_response(body: bytes, dates: tuple[str, ...]):
    return classify_availability_response(
        body, dates, scope=SCOPE, full_range_classification="H3_FULL_RANGE_AVAILABLE",
    )
