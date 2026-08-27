"""Offline-safe support for the bounded authenticated KRX fundamentals pilot.

This module intentionally defines a Landing-only feasibility probe.  It is not
a dataset collector and it must never be used to infer a stable normalized
schema before the captured full-market responses are reviewed.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import time
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


PYKRX_VERSION = "1.2.8"
MAX_BUSINESS_REQUESTS = 7
# Seven business calls plus at most eight observed/allowed authentication calls.
MAX_RAW_HTTP_REQUESTS = 15
HTTP_TIMEOUT_SECONDS = 20
MIN_BUSINESS_INTERVAL_SECONDS = 3.0
MAX_JITTER_SECONDS = 1.0

AUTH_ENDPOINT_PATHS = frozenset({
    "/contents/MDC/COMS/client/MDCCOMS001.cmd",
    "/contents/MDC/COMS/client/view/login.jsp",
    "/contents/MDC/COMS/client/MDCCOMS001D1.cmd",
})
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"
MARKET_IDS = {"KOSPI": "STK", "KOSDAQ": "KSQ"}

FULL_MARKET_FIELDS = (
    "ISU_SRT_CD", "ISU_ABBRV", "TDD_CLSPRC", "EPS", "PER", "BPS", "PBR", "DPS", "DVD_YLD",
)
# C005 retained raw response verified this exact per-symbol response inventory.
SYMBOL_FIELDS = (
    "TRD_DD", "TDD_CLSPRC", "EPS", "PER", "BPS", "PBR", "DPS", "DVD_YLD",
)


@dataclass(frozen=True)
class ProbeSpec:
    sequence: int
    name: str
    operation: str
    bld: str
    scope: Mapping[str, str]
    expectation: str
    required_fields: tuple[str, ...]


def _probe(name: str, operation: str, scope: Mapping[str, str], expectation: str, fields: Sequence[str]) -> ProbeSpec:
    return ProbeSpec(
        sequence=len(PROBE_MATRIX) + 1, name=name, operation=operation,
        bld=("dbms/MDC/STAT/standard/MDCSTAT03501" if operation == "market" else "dbms/MDC/STAT/standard/MDCSTAT03502"),
        scope=dict(scope), expectation=expectation, required_fields=tuple(fields),
    )


# The historical dates are known trading dates.  They are coverage sentinels,
# not assertions about first availability.
PROBE_MATRIX = (
    ProbeSpec(1, "market_recent_kospi", "market", "dbms/MDC/STAT/standard/MDCSTAT03501", {"date": "20260810", "market": "KOSPI"}, "nonempty", FULL_MARKET_FIELDS),
    ProbeSpec(2, "market_recent_kosdaq", "market", "dbms/MDC/STAT/standard/MDCSTAT03501", {"date": "20260810", "market": "KOSDAQ"}, "nonempty", FULL_MARKET_FIELDS),
    ProbeSpec(3, "market_source_coverage_kospi_20080102", "market", "dbms/MDC/STAT/standard/MDCSTAT03501", {"date": "20080102", "market": "KOSPI"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(4, "market_source_coverage_kosdaq_20080102", "market", "dbms/MDC/STAT/standard/MDCSTAT03501", {"date": "20080102", "market": "KOSDAQ"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(5, "symbol_recent_listed", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03502", {"fromdate": "20260810", "todate": "20260810", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "nonempty", SYMBOL_FIELDS),
    ProbeSpec(6, "symbol_historical_kospi_delisted", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03502", {"fromdate": "20240708", "todate": "20240708", "market": "KOSPI", "symbol": "003410", "isin": "KR7003410008"}, "nonempty", SYMBOL_FIELDS),
    ProbeSpec(7, "symbol_historical_kosdaq_delisted", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03502", {"fromdate": "20191230", "todate": "20191230", "market": "KOSDAQ", "symbol": "030270", "isin": "KR7030270003"}, "boundary", SYMBOL_FIELDS),
)
assert len(PROBE_MATRIX) == MAX_BUSINESS_REQUESTS

INDEX_PROBE_MATRIX = (
    ProbeSpec(1, "index_recent_krx", "index_market", "dbms/MDC/STAT/standard/MDCSTAT00701", {"date": "20260812", "group": "01"}, "nonempty", ("IDX_NM", "WT_PER", "WT_STKPRC_NETASST_RTO", "DIV_YD")),
    ProbeSpec(2, "index_recent_kospi", "index_market", "dbms/MDC/STAT/standard/MDCSTAT00701", {"date": "20260812", "group": "02"}, "nonempty", ("IDX_NM", "WT_PER", "WT_STKPRC_NETASST_RTO", "DIV_YD")),
    ProbeSpec(3, "index_recent_kosdaq", "index_market", "dbms/MDC/STAT/standard/MDCSTAT00701", {"date": "20260812", "group": "03"}, "nonempty", ("IDX_NM", "WT_PER", "WT_STKPRC_NETASST_RTO", "DIV_YD")),
    ProbeSpec(4, "index_floor_kospi_20100104", "index_market", "dbms/MDC/STAT/standard/MDCSTAT00701", {"date": "20100104", "group": "02"}, "boundary", ("IDX_NM", "WT_PER", "WT_STKPRC_NETASST_RTO", "DIV_YD")),
    ProbeSpec(5, "index_floor_kosdaq_20100104", "index_market", "dbms/MDC/STAT/standard/MDCSTAT00701", {"date": "20100104", "group": "03"}, "boundary", ("IDX_NM", "WT_PER", "WT_STKPRC_NETASST_RTO", "DIV_YD")),
)

CONSTITUENT_PROBE_MATRIX = (
    ProbeSpec(1, "kospi200_recent", "constituent", "dbms/MDC/STAT/standard/MDCSTAT00601", {"date": "20260812", "ticker": "1028"}, "nonempty", ("ISU_SRT_CD",)),
    ProbeSpec(2, "kospi200_20200102", "constituent", "dbms/MDC/STAT/standard/MDCSTAT00601", {"date": "20200102", "ticker": "1028"}, "nonempty", ("ISU_SRT_CD",)),
    ProbeSpec(3, "kosdaq150_recent", "constituent", "dbms/MDC/STAT/standard/MDCSTAT00601", {"date": "20260812", "ticker": "2203"}, "nonempty", ("ISU_SRT_CD",)),
    ProbeSpec(4, "kosdaq150_20200102", "constituent", "dbms/MDC/STAT/standard/MDCSTAT00601", {"date": "20200102", "ticker": "2203"}, "nonempty", ("ISU_SRT_CD",)),
)

SECTOR_PROBE_MATRIX = (
    ProbeSpec(1, "sector_kospi_recent", "sector", "dbms/MDC/STAT/standard/MDCSTAT03901", {"date": "20260812", "market": "KOSPI"}, "nonempty", ("ISU_SRT_CD", "IDX_IND_NM")),
    ProbeSpec(2, "sector_kospi_20200102", "sector", "dbms/MDC/STAT/standard/MDCSTAT03901", {"date": "20200102", "market": "KOSPI"}, "nonempty", ("ISU_SRT_CD", "IDX_IND_NM")),
    ProbeSpec(3, "sector_kosdaq_recent", "sector", "dbms/MDC/STAT/standard/MDCSTAT03901", {"date": "20260812", "market": "KOSDAQ"}, "nonempty", ("ISU_SRT_CD", "IDX_IND_NM")),
    ProbeSpec(4, "sector_kosdaq_20200102", "sector", "dbms/MDC/STAT/standard/MDCSTAT03901", {"date": "20200102", "market": "KOSDAQ"}, "nonempty", ("ISU_SRT_CD", "IDX_IND_NM")),
)

CREDIT_PROBE_MATRIX = (
    ProbeSpec(1, "credit_snapshot_recent", "bond_snapshot", "dbms/MDC/STAT/standard/MDCSTAT11401", {"date": "20260812"}, "nonempty", ("ITM_TP_NM", "LST_ORD_BAS_YD", "CMP_YD")),
    ProbeSpec(2, "credit_snapshot_20200102", "bond_snapshot", "dbms/MDC/STAT/standard/MDCSTAT11401", {"date": "20200102"}, "boundary", ("ITM_TP_NM", "LST_ORD_BAS_YD", "CMP_YD")),
    ProbeSpec(3, "credit_snapshot_20100104", "bond_snapshot", "dbms/MDC/STAT/standard/MDCSTAT11401", {"date": "20100104"}, "boundary", ("ITM_TP_NM", "LST_ORD_BAS_YD", "CMP_YD")),
    ProbeSpec(4, "credit_snapshot_20000104", "bond_snapshot", "dbms/MDC/STAT/standard/MDCSTAT11401", {"date": "20000104"}, "boundary", ("ITM_TP_NM", "LST_ORD_BAS_YD", "CMP_YD")),
    ProbeSpec(5, "credit_aa_recent_range", "bond_range", "dbms/MDC/STAT/standard/MDCSTAT11402", {"fromdate": "20260803", "todate": "20260812", "code": "3009"}, "nonempty", ("DISCLS_DD", "LST_ORD_BAS_YD", "CMP_YD")),
    ProbeSpec(6, "credit_bbb_recent_range", "bond_range", "dbms/MDC/STAT/standard/MDCSTAT11402", {"fromdate": "20260803", "todate": "20260812", "code": "3010"}, "nonempty", ("DISCLS_DD", "LST_ORD_BAS_YD", "CMP_YD")),
    ProbeSpec(7, "cd91_recent_range", "bond_range", "dbms/MDC/STAT/standard/MDCSTAT11402", {"fromdate": "20260803", "todate": "20260812", "code": "4000"}, "nonempty", ("DISCLS_DD", "LST_ORD_BAS_YD", "CMP_YD")),
)

# One source-native range per target series.  The retained pilot proved the raw
# schema and a 2000 empty / 2010 positive boundary; these requests deliberately
# start immediately after that empty sentinel and preserve whatever source floor
# KRX returns without inventing missing dates.
def _credit_backfill_matrix() -> tuple[ProbeSpec, ...]:
    probes: list[ProbeSpec] = []
    final = datetime.strptime("20260812", "%Y%m%d").date()
    for series, code in (("aa", "3009"), ("bbb", "3010"), ("cd91", "4000")):
        start = datetime.strptime("20000105", "%Y%m%d").date()
        part = 1
        while start <= final:
            end = min(start + timedelta(days=729), final)
            probes.append(ProbeSpec(
                len(probes) + 1, f"credit_{series}_history_{part:02d}", "bond_range",
                "dbms/MDC/STAT/standard/MDCSTAT11402",
                {"fromdate": start.strftime("%Y%m%d"), "todate": end.strftime("%Y%m%d"), "code": code},
                "boundary", ("DISCLS_DD", "LST_ORD_BAS_YD", "CMP_YD"),
            ))
            start = end + timedelta(days=1)
            part += 1
    return tuple(probes)


CREDIT_BACKFILL_MATRIX = _credit_backfill_matrix()


def _index_backfill_matrix() -> tuple[ProbeSpec, ...]:
    probes: list[ProbeSpec] = []
    final = datetime.strptime("20260812", "%Y%m%d").date()
    for label, ticker in (("kospi", "1001"), ("kospi200", "1028"), ("kosdaq", "2001"), ("kosdaq150", "2203"), ("krx300", "5300")):
        start = datetime.strptime("20000101", "%Y%m%d").date()
        part = 1
        while start <= final:
            end = min(start + timedelta(days=729), final)
            probes.append(ProbeSpec(
                len(probes) + 1, f"index_{label}_history_{part:02d}", "index_range",
                "dbms/MDC/STAT/standard/MDCSTAT00702",
                {"fromdate": start.strftime("%Y%m%d"), "todate": end.strftime("%Y%m%d"), "ticker": ticker},
                "boundary", ("TRD_DD", "CLSPRC_IDX", "WT_PER", "WT_STKPRC_NETASST_RTO", "DIV_YD"),
            ))
            start = end + timedelta(days=1)
            part += 1
    return tuple(probes)


INDEX_BACKFILL_MATRIX = _index_backfill_matrix()


OPTIMIZATION_PROBE_MATRIX = (
    ProbeSpec(1, "foreign_all_recent", "foreign_all", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "20260812"}, "nonempty", ("ISU_SRT_CD", "LIST_SHRS", "FORN_HD_QTY", "FORN_SHR_RT", "FORN_ORD_LMT_QTY", "FORN_LMT_EXHST_RT")),
    ProbeSpec(2, "foreign_all_20200102", "foreign_all", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "20200102"}, "nonempty", ("ISU_SRT_CD", "LIST_SHRS", "FORN_HD_QTY", "FORN_SHR_RT", "FORN_ORD_LMT_QTY", "FORN_LMT_EXHST_RT")),
    ProbeSpec(3, "foreign_all_20000105", "foreign_all", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "20000105"}, "boundary", ("ISU_SRT_CD", "LIST_SHRS", "FORN_HD_QTY", "FORN_SHR_RT", "FORN_ORD_LMT_QTY", "FORN_LMT_EXHST_RT")),
    ProbeSpec(4, "equity_fundamental_all_recent", "market", "dbms/MDC/STAT/standard/MDCSTAT03501", {"date": "20260812", "market": "ALL"}, "nonempty", FULL_MARKET_FIELDS),
    ProbeSpec(5, "equity_fundamental_all_20200102", "market", "dbms/MDC/STAT/standard/MDCSTAT03501", {"date": "20200102", "market": "ALL"}, "nonempty", FULL_MARKET_FIELDS),
    ProbeSpec(6, "equity_fundamental_all_20080103", "market", "dbms/MDC/STAT/standard/MDCSTAT03501", {"date": "20080103", "market": "ALL"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(7, "sector_kospi_20200102", "sector", "dbms/MDC/STAT/standard/MDCSTAT03901", {"date": "20200102", "market": "KOSPI"}, "nonempty", ("ISU_SRT_CD", "IDX_IND_NM")),
    ProbeSpec(8, "sector_kosdaq_20200102", "sector", "dbms/MDC/STAT/standard/MDCSTAT03901", {"date": "20200102", "market": "KOSDAQ"}, "nonempty", ("ISU_SRT_CD", "IDX_IND_NM")),
    ProbeSpec(9, "sector_kosdaq_recent", "sector", "dbms/MDC/STAT/standard/MDCSTAT03901", {"date": "20260812", "market": "KOSDAQ"}, "nonempty", ("ISU_SRT_CD", "IDX_IND_NM")),
)


class PilotStopped(RuntimeError):
    pass


class PilotLocked(PilotStopped):
    pass


class ResumeSafetyError(PilotStopped):
    pass


class BudgetExceeded(PilotStopped):
    pass


class CredentialLeakDetected(PilotStopped):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def matrix_sha256(probes: Sequence[ProbeSpec] = PROBE_MATRIX) -> str:
    body = json.dumps([asdict(probe) for probe in probes], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


_SENSITIVE = re.compile(r"(?i)(password|passwd|pw|secret|token|cookie|authorization|krx_id|krx_pw)")


def redact(value: object, secrets: Iterable[str] = ()) -> object:
    secret_values = tuple(item for item in secrets if item)
    if isinstance(value, Mapping):
        return {str(key): "[REDACTED]" if _SENSITIVE.search(str(key)) else redact(item, secret_values) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, secret_values) for item in value]
    if isinstance(value, str):
        for secret in secret_values:
            value = value.replace(secret, "[REDACTED]")
    return value


def assert_no_credentials(content: bytes, secrets: Iterable[str]) -> None:
    for secret in secrets:
        if secret and secret.encode("utf-8") in content:
            raise CredentialLeakDetected("credential value detected before artifact write")


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_bytes_atomic_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ResumeSafetyError(f"refusing to overwrite Landing response: {path.name}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise ResumeSafetyError(f"Landing response appeared during write: {path.name}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class AppendOnlyLedger:
    def __init__(self, path: Path, *, secrets: Iterable[str]) -> None:
        self.path, self.secrets = path, tuple(item for item in secrets if item)
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **fields: object) -> None:
        encoded = (json.dumps(redact({"event": event, "recorded_at_utc": utc_now(), **fields}, self.secrets), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        assert_no_credentials(encoded, self.secrets)
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def records(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]


@contextmanager
def shared_d_owned_krx_lock(path: Path, *, run_id: str):
    """Use the existing D-owned KRX lock path without assuming task ownership."""
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise PilotLocked(f"D-owned KRX stream lock exists: {path}") from None
    try:
        payload = {"owner": "D", "stream": "KRX", "task": "C008", "run_id": run_id, "pid": os.getpid(), "token": token}
        os.write(descriptor, json.dumps(payload, sort_keys=True).encode("utf-8"))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            raise PilotLocked("cannot verify shared KRX lock; lock retained") from error
        if existing.get("token") != token or existing.get("run_id") != run_id:
            raise PilotLocked("shared KRX lock ownership changed; lock retained")
        path.unlink()


class BusinessThrottle:
    def __init__(self, *, sleep_fn: Callable[[float], None] = time.sleep, monotonic_fn: Callable[[], float] = time.monotonic, jitter_fn: Callable[[float, float], float] = random.uniform) -> None:
        self.sleep_fn, self.monotonic_fn, self.jitter_fn = sleep_fn, monotonic_fn, jitter_fn
        self.last_started: float | None = None

    def before_request(self) -> float:
        now = self.monotonic_fn()
        delay = 0.0
        if self.last_started is not None:
            delay = max(0.0, MIN_BUSINESS_INTERVAL_SECONDS + self.jitter_fn(0.0, MAX_JITTER_SECONDS) - (now - self.last_started))
            if delay:
                self.sleep_fn(delay)
        self.last_started = self.monotonic_fn()
        return delay


def landing_name(probe: ProbeSpec) -> str:
    return f"response_{probe.sequence:02d}_{probe.name}.json"


def classify_business_body(probe: ProbeSpec, body: bytes) -> tuple[str, int]:
    if body.lstrip().startswith(b"<"):
        raise PilotStopped(f"HTML_OR_RESTRICTION_RESPONSE:{probe.name}")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PilotStopped(f"NON_JSON_RESPONSE:{probe.name}") from error
    if not isinstance(payload, dict) or payload.get("_error_code") or payload.get("error") or payload.get("errors"):
        raise PilotStopped(f"SOURCE_ERROR_PAYLOAD:{probe.name}")
    rows = payload.get("block1") if probe.operation == "sector" else payload.get("output")
    if not isinstance(rows, list):
        raise PilotStopped(f"EXPECTED_OUTPUT_MISSING:{probe.name}")
    if not rows:
        if probe.expectation == "nonempty":
            raise PilotStopped(f"ANOMALOUS_EMPTY:{probe.name}")
        return "COVERAGE_EMPTY", 0
    for row in rows:
        if not isinstance(row, dict) or set(probe.required_fields) - set(row):
            raise PilotStopped(f"SCHEMA_MISMATCH:{probe.name}")
    return "SUCCESS", len(rows)
