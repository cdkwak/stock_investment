"""Pure validation support for the one-call A007 Investor range diagnostic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Mapping

from scripts.manual.pilot.pykrx_short_selling_pilot_support import PilotStopped


PYKRX_VERSION = "1.2.8"
BUSINESS_BLD = "dbms/MDC/STAT/srt/MDCSTAT30301"
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"
MAX_BUSINESS_REQUESTS = 1
MAX_RAW_HTTP_REQUESTS = 6

# 2026-08-10 is a retained, positive one-day pilot observation.  The preceding
# four dates are canonical KOSPI trading dates.  This deliberately tests range
# behavior without making another request against the failed historical scope.
EXPECTED_DATES = (
    "20260804",
    "20260805",
    "20260806",
    "20260807",
    "20260810",
)
SCOPE_ID = "20260804_20260810_KOSPI_volume_range_diagnostic"
SCOPE = {
    "strtDd": EXPECTED_DATES[0],
    "endDd": EXPECTED_DATES[-1],
    "inqCondTpCd": 1,
    "mktTpCd": 1,
}
SOURCE_FIELDS = (
    "TRD_DD",
    "STR_CONST_VAL1",
    "STR_CONST_VAL2",
    "STR_CONST_VAL3",
    "STR_CONST_VAL4",
    "STR_CONST_VAL5",
)


@dataclass(frozen=True)
class DiagnosticClassification:
    classification: str
    source_rows: int
    observed_dates: tuple[str, ...]
    positive_total_dates: int
    source_current_datetime: str | None = None


def expected_dates(unused_project_root=None) -> tuple[str, ...]:
    """Return the immutable dates used by the original recent-range probe."""

    return EXPECTED_DATES


def scope_sha256(dates: tuple[str, ...] = EXPECTED_DATES) -> str:
    payload = {
        "bld": BUSINESS_BLD,
        "expected_dates": dates,
        "scope": SCOPE,
        "scope_id": SCOPE_ID,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def manifest_payload(
    *, run_id: str, created_at_utc: str,
    dates: tuple[str, ...] = EXPECTED_DATES,
) -> dict[str, object]:
    return {
        "business_request_limit": MAX_BUSINESS_REQUESTS,
        "created_at_utc": created_at_utc,
        "expected_dates": list(dates),
        "historical_failed_scope_retried": False,
        "normalized_writes": False,
        "checkpoint_writes": False,
        "parallelism": 1,
        "purpose": "diagnose_current_MDCSTAT30301_multi_day_range_semantics",
        "pykrx_version": PYKRX_VERSION,
        "raw_http_request_limit": MAX_RAW_HTTP_REQUESTS,
        "retry_count": 0,
        "run_id": run_id,
        "scope": dict(SCOPE),
        "scope_id": SCOPE_ID,
        "scope_sha256": scope_sha256(dates),
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


def classify_exact_response(
    body: bytes, *, dates: tuple[str, ...], classification: str,
) -> DiagnosticClassification:
    stripped = body.lstrip()
    if stripped.startswith(b"<"):
        raise PilotStopped("HTML_OR_RESTRICTION_RESPONSE")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PilotStopped("NON_JSON_RESPONSE") from error
    if not isinstance(payload, dict):
        raise PilotStopped("INVALID_JSON_ROOT")
    if payload.get("_error_code") or payload.get("error") or payload.get("errors"):
        raise PilotStopped("SOURCE_ERROR_PAYLOAD")
    allowed_top_level = {"OutBlock_1"}, {"OutBlock_1", "CURRENT_DATETIME"}
    if set(payload) not in allowed_top_level:
        raise PilotStopped("TOP_LEVEL_SCHEMA_MISMATCH")
    source_current_datetime = payload.get("CURRENT_DATETIME")
    if source_current_datetime is not None:
        if not isinstance(source_current_datetime, str):
            raise PilotStopped("CURRENT_DATETIME_INVALID")
        try:
            datetime.strptime(source_current_datetime, "%Y.%m.%d %p %I:%M:%S")
        except ValueError as error:
            raise PilotStopped("CURRENT_DATETIME_INVALID") from error
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list):
        raise PilotStopped("EXPECTED_BLOCK_MISSING")
    if not rows:
        raise PilotStopped("ANOMALOUS_EMPTY_RANGE")

    observed: list[str] = []
    positive_total_dates = 0
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise PilotStopped(f"INVALID_ROW:{index}")
        missing = sorted(set(SOURCE_FIELDS) - set(row))
        if missing:
            raise PilotStopped(f"SCHEMA_MISMATCH:{index}:{','.join(missing)}")
        extra = sorted(set(row) - set(SOURCE_FIELDS))
        if extra:
            raise PilotStopped(f"SCHEMA_MISMATCH:{index}:extra={','.join(extra)}")
        raw_date = str(row["TRD_DD"]).strip()
        try:
            parsed_date = datetime.strptime(raw_date, "%Y/%m/%d").strftime("%Y%m%d")
        except ValueError as error:
            raise PilotStopped(f"INVALID_DATE:{index}") from error
        observed.append(parsed_date)
        components = [_integer(row[field], field) for field in SOURCE_FIELDS[1:5]]
        total = _integer(row["STR_CONST_VAL5"], "STR_CONST_VAL5")
        if sum(components) != total:
            raise PilotStopped(f"INVESTOR_TOTAL_MISMATCH:{parsed_date}")
        positive_total_dates += int(total > 0)

    if len(set(observed)) != len(observed):
        raise PilotStopped("DUPLICATE_SOURCE_DATE")
    expected = set(dates)
    actual = set(observed)
    if actual != expected:
        missing = ",".join(sorted(expected - actual)) or "-"
        extra = ",".join(sorted(actual - expected)) or "-"
        raise PilotStopped(
            f"DATE_COVERAGE_MISMATCH:{len(actual)}/{len(expected)}:missing={missing}:extra={extra}"
        )
    if positive_total_dates == 0:
        raise PilotStopped("NO_POSITIVE_KNOWN_RECENT_OBSERVATION")
    return DiagnosticClassification(
        classification=classification,
        source_rows=len(rows),
        observed_dates=tuple(sorted(observed)),
        positive_total_dates=positive_total_dates,
        source_current_datetime=source_current_datetime,
    )


def classify_response(
    body: bytes, dates: tuple[str, ...] = EXPECTED_DATES,
) -> DiagnosticClassification:
    return classify_exact_response(
        body, dates=dates, classification="MULTI_DATE_RANGE_CONFIRMED"
    )

