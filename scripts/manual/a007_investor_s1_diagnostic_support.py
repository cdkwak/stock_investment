"""Frozen validation plan for the A007 Investor S1 availability diagnostic."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

from scripts.manual.a007_investor_range_diagnostic_support import (
    DiagnosticClassification,
    classify_exact_response,
)
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped


PYKRX_VERSION = "1.2.8"
BUSINESS_BLD = "dbms/MDC/STAT/srt/MDCSTAT30301"
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"
MAX_BUSINESS_REQUESTS = 1
MAX_RAW_HTTP_REQUESTS = 6
EXPECTED_RAW_HTTP_REQUESTS = 6
REQUIRE_ZERO_RETRY_AUTH_SESSION = True
SCOPE_ID = "20240807_20260807_KOSPI_volume_S1_diagnostic"
SCOPE = {
    "strtDd": "20240807",
    "endDd": "20260807",
    "inqCondTpCd": 1,
    "mktTpCd": 1,
}
EXPECTED_BUSINESS_DATA = {
    "bld": BUSINESS_BLD,
    "strtDd": SCOPE["strtDd"],
    "endDd": SCOPE["endDd"],
    "inqCondTpCd": str(SCOPE["inqCondTpCd"]),
    "mktTpCd": str(SCOPE["mktTpCd"]),
}
EXPECTED_DATE_COUNT = 485
EXPECTED_DATE_SHA256 = (
    "18d0a12c19a17b7cff44f6834006385197c9d72e22b9519fd223b2e0541188a7"
)

# The expected set is derived only from these exact retained KOSPI canonical-
# universe partitions. Any later input revision requires an explicit new plan.
CANONICAL_DATE_SOURCES = (
    {
        "path": (
            "data/published/kr_equity_canonical_universe_daily/"
            "market=KOSPI/year=2024/data.parquet"
        ),
        "bytes": 595239,
        "sha256": "61a1c1171605388b2121df9142e28e8d704a75b236c8a62512bce148c5a5c7bf",
    },
    {
        "path": (
            "data/published/kr_equity_canonical_universe_daily/"
            "market=KOSPI/year=2025/data.parquet"
        ),
        "bytes": 435587,
        "sha256": "9bb6936cd3f87f740b1688ca722f7d53bd2dcbfa1aab8a9a5df38b53bf08e9d4",
    },
    {
        "path": (
            "data/published/kr_equity_canonical_universe_daily/"
            "market=KOSPI/year=2026/data.parquet"
        ),
        "bytes": 283341,
        "sha256": "5d63d0208c10db311e0a602a4290707cd6526286dbd1906de43891bbabf43df5",
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
        len(dates) != EXPECTED_DATE_COUNT
        or not dates
        or dates[0] != SCOPE["strtDd"]
        or dates[-1] != SCOPE["endDd"]
        or _date_digest(dates) != EXPECTED_DATE_SHA256
    ):
        raise PilotStopped("CANONICAL_EXPECTED_DATE_SET_MISMATCH")
    return dates


def scope_sha256(dates: tuple[str, ...]) -> str:
    payload = {
        "bld": BUSINESS_BLD,
        "expected_date_count": EXPECTED_DATE_COUNT,
        "expected_date_sha256": _date_digest(dates),
        "scope": SCOPE,
        "scope_id": SCOPE_ID,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def manifest_payload(
    *, run_id: str, created_at_utc: str, dates: tuple[str, ...],
) -> dict[str, object]:
    if len(dates) != EXPECTED_DATE_COUNT or _date_digest(dates) != EXPECTED_DATE_SHA256:
        raise PilotStopped("CANONICAL_EXPECTED_DATE_SET_MISMATCH")
    return {
        "business_request_limit": MAX_BUSINESS_REQUESTS,
        "canonical_date_sources": [dict(item) for item in CANONICAL_DATE_SOURCES],
        "checkpoint_writes": False,
        "created_at_utc": created_at_utc,
        "expected_date_count": EXPECTED_DATE_COUNT,
        "expected_date_sha256": EXPECTED_DATE_SHA256,
        "expected_dates": list(dates),
        "historical_failed_scope_retried": False,
        "normalized_writes": False,
        "parallelism": 1,
        "purpose": "discover_MDCSTAT30301_S1_two_year_historical_availability",
        "pykrx_version": PYKRX_VERSION,
        "raw_http_request_limit": MAX_RAW_HTTP_REQUESTS,
        "raw_http_requests_expected": EXPECTED_RAW_HTTP_REQUESTS,
        "retry_count": 0,
        "run_id": run_id,
        "scope": dict(SCOPE),
        "scope_id": SCOPE_ID,
        "scope_sha256": scope_sha256(dates),
        "version": 1,
    }


def classify_response(
    body: bytes, dates: tuple[str, ...],
) -> DiagnosticClassification:
    return classify_exact_response(
        body, dates=dates, classification="S1_FULL_RANGE_CONFIRMED"
    )
