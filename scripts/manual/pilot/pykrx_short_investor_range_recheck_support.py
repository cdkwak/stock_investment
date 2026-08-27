"""Pure plan and response validation for the bounded Short Investor recheck."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Mapping, Sequence


PYKRX_VERSION = "1.2.8"
BUSINESS_BLD = "dbms/MDC/STAT/srt/MDCSTAT30301"
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"
MAX_BUSINESS_REQUESTS = 18
MAX_RAW_HTTP_REQUESTS = 26
MIN_BUSINESS_INTERVAL_SECONDS = 8.0
SOURCE_FIELDS = (
    "TRD_DD", "STR_CONST_VAL1", "STR_CONST_VAL2", "STR_CONST_VAL3",
    "STR_CONST_VAL4", "STR_CONST_VAL5",
)
MARKET_CODES = {"KOSPI": "1", "KOSDAQ": "2"}
METRIC_CODES = {"volume": "1", "trading_value": "2"}
EXPECTED_COLUMNS = ("기관", "개인", "외국인", "기타", "합계")


class RecheckStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class Probe:
    probe_id: str
    market: str
    metric: str
    start: str
    end: str
    expected_dates: tuple[str, ...]
    stage: str

    @property
    def expected_business_data(self) -> dict[str, str]:
        return {
            "strtDd": self.start,
            "endDd": self.end,
            "inqCondTpCd": METRIC_CODES[self.metric],
            "mktTpCd": MARKET_CODES[self.market],
            "bld": BUSINESS_BLD,
        }


KNOWN_DATES = ("20200106", "20200107", "20200108", "20200109", "20200110")
RECENT_1 = ("20260807",)
RECENT_5 = ("20260803", "20260804", "20260805", "20260806", "20260807")
RECENT_20 = (
    "20260710", "20260713", "20260714", "20260715", "20260716",
    "20260720", "20260721", "20260722", "20260723",
    "20260724", "20260727", "20260728", "20260729", "20260730",
    "20260731", "20260803", "20260804", "20260805", "20260806",
    "20260807",
)
# July 17 is absent from the retained 2026 exchange calendar. The frozen set is
# bound to the local canonical market calendar rather than weekday inference.
RECENT_60 = (
    "20260513", "20260514", "20260515", "20260518", "20260519",
    "20260520", "20260521", "20260522", "20260526", "20260527",
    "20260528", "20260529", "20260601", "20260602", "20260604",
    "20260605", "20260608", "20260609", "20260610", "20260611",
    "20260612", "20260615", "20260616", "20260617", "20260618",
    "20260619", "20260622", "20260623", "20260624", "20260625",
    "20260626", "20260629", "20260630", "20260701", "20260702",
    "20260703", "20260706", "20260707", "20260708", "20260709",
    "20260710", "20260713", "20260714", "20260715", "20260716",
    "20260720", "20260721", "20260722", "20260723", "20260724",
    "20260727", "20260728", "20260729", "20260730", "20260731",
    "20260803", "20260804", "20260805", "20260806", "20260807",
)

# Frozen from the retained KOSPI/KOSDAQ canonical calendars.  This is the
# first 60-date window beginning at the retained positive KOSPI source
# boundary; it is a diagnostic gate, not an inferred availability history.
BOUNDARY_60 = (
    "20170522", "20170523", "20170524", "20170525", "20170526", "20170529",
    "20170530", "20170531", "20170601", "20170602", "20170605", "20170607",
    "20170608", "20170609", "20170612", "20170613", "20170614", "20170615",
    "20170616", "20170619", "20170620", "20170621", "20170622", "20170623",
    "20170626", "20170627", "20170628", "20170629", "20170630", "20170703",
    "20170704", "20170705", "20170706", "20170707", "20170710", "20170711",
    "20170712", "20170713", "20170714", "20170717", "20170718", "20170719",
    "20170720", "20170721", "20170724", "20170725", "20170726", "20170727",
    "20170728", "20170731", "20170801", "20170802", "20170803", "20170804",
    "20170807", "20170808", "20170809", "20170810", "20170811", "20170814",
)

HISTORICAL_WINDOWS = {
    "2015": ("20150102", "20150105", "20150106", "20150107", "20150108"),
    "2010": ("20100104", "20100105", "20100106", "20100107", "20100108"),
    "2008": ("20080102", "20080103", "20080104", "20080107", "20080108"),
}


def make_probe(
    probe_id: str, market: str, metric: str, dates: Sequence[str], stage: str,
) -> Probe:
    frozen = tuple(dates)
    if not frozen:
        raise ValueError("probe dates must not be empty")
    return Probe(probe_id, market, metric, frozen[0], frozen[-1], frozen, stage)


def _integer(value: object, field: str) -> int:
    text = str(value).replace(",", "").strip()
    if not text.isdigit():
        raise RecheckStopped(f"INVALID_NONNEGATIVE_INTEGER:{field}")
    return int(text)


def parse_raw(body: bytes) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    if body.lstrip().startswith(b"<"):
        raise RecheckStopped("HTML_OR_RESTRICTION_RESPONSE")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RecheckStopped("NON_JSON_RESPONSE") from error
    if not isinstance(payload, dict) or payload.get("_error_code") or payload.get("error") or payload.get("errors"):
        raise RecheckStopped("SOURCE_ERROR")
    if set(payload) not in ({"OutBlock_1"}, {"OutBlock_1", "CURRENT_DATETIME"}):
        raise RecheckStopped("TOP_LEVEL_SCHEMA_MISMATCH")
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list):
        raise RecheckStopped("SOURCE_BLOCK_MISSING")
    if len(rows) == 1 and isinstance(rows[0], Mapping):
        row = rows[0]
        if set(row) == set(SOURCE_FIELDS) and not str(row["TRD_DD"]).strip():
            if all(_integer(row[field], field) == 0 for field in SOURCE_FIELDS[1:]):
                return rows, ()
    dates: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != set(SOURCE_FIELDS):
            raise RecheckStopped(f"ROW_SCHEMA_MISMATCH:{index}")
        try:
            date = datetime.strptime(str(row["TRD_DD"]), "%Y/%m/%d").strftime("%Y%m%d")
        except ValueError as error:
            raise RecheckStopped(f"INVALID_DATE:{index}") from error
        values = [_integer(row[field], field) for field in SOURCE_FIELDS[1:]]
        if sum(values[:4]) != values[4]:
            raise RecheckStopped(f"INVESTOR_TOTAL_MISMATCH:{date}")
        dates.append(date)
    if len(dates) != len(set(dates)):
        raise RecheckStopped("DUPLICATE_SOURCE_DATE")
    return rows, tuple(dates)


def classify(probe: Probe, body: bytes) -> dict[str, object]:
    rows, source_order = parse_raw(body)
    expected = set(probe.expected_dates)
    actual = set(source_order)
    if not rows:
        classification = "VALID_EMPTY"
    elif not source_order:
        classification = "VALID_EMPTY_PLACEHOLDER"
    elif actual == expected:
        classification = (
            "REGRESSION_PASS_MULTIROW" if probe.stage == "known_positive"
            else "RANGE_PASS"
        )
    elif len(rows) == 1 and source_order[0] == probe.end and len(expected) > 1:
        classification = "REGRESSION_FAIL_RANGE_COLLAPSE" if probe.stage == "known_positive" else "RANGE_END_ONLY"
    else:
        classification = "DATE_COVERAGE_MISMATCH"
    return {
        "classification": classification,
        "source_rows": len(rows),
        "source_order": list(source_order),
        "observed_dates": sorted(actual),
        "missing_dates": sorted(expected - actual),
        "extra_dates": sorted(actual - expected),
        "raw_order": "descending" if list(source_order) == sorted(source_order, reverse=True) else "ascending" if list(source_order) == sorted(source_order) else "other",
        "totals_valid": True,
        "negative_values": False,
    }


def plan_sha256(probes: Sequence[Probe]) -> str:
    payload = [
        {"probe_id": p.probe_id, "market": p.market, "metric": p.metric,
         "start": p.start, "end": p.end, "expected_dates": p.expected_dates,
         "stage": p.stage, "business_data": p.expected_business_data}
        for p in probes
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
