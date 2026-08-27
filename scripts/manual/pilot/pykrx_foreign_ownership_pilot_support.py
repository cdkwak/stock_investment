"""Offline-safe support for the bounded KRX foreign-ownership pilot."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
MAX_BUSINESS_REQUESTS = 20
MAX_RAW_HTTP_REQUESTS = 28
HTTP_TIMEOUT_SECONDS = 20
MIN_BUSINESS_INTERVAL_SECONDS = 8.0
MAX_JITTER_SECONDS = 2.0

AUTH_ENDPOINT_PATHS = frozenset({
    "/contents/MDC/COMS/client/MDCCOMS001.cmd",
    "/contents/MDC/COMS/client/view/login.jsp",
    "/contents/MDC/COMS/client/MDCCOMS001D1.cmd",
})
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"
MARKET_IDS = {"KOSPI": "STK", "KOSDAQ": "KSQ"}

MEASURE_FIELDS = (
    "LIST_SHRS", "FORN_HD_QTY", "FORN_SHR_RT",
    "FORN_ORD_LMT_QTY", "FORN_LMT_EXHST_RT",
)
FULL_MARKET_FIELDS = (
    "ISU_SRT_CD", "ISU_ABBRV", "TDD_CLSPRC", "FLUC_TP_CD",
    "CMPPREVDD_PRC", "FLUC_RT", *MEASURE_FIELDS,
)
SYMBOL_FIELDS = (
    "TRD_DD", "TDD_CLSPRC", "FLUC_TP_CD", "CMPPREVDD_PRC",
    "FLUC_RT", *MEASURE_FIELDS,
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


PROBE_MATRIX = (
    ProbeSpec(1, "market_recent_kospi", "market", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "20260814", "market": "KOSPI", "balance_limit": "0"}, "nonempty", FULL_MARKET_FIELDS),
    ProbeSpec(2, "market_recent_kosdaq", "market", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "20260814", "market": "KOSDAQ", "balance_limit": "0"}, "nonempty", FULL_MARKET_FIELDS),
    ProbeSpec(3, "market_kospi_20100104", "market", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "20100104", "market": "KOSPI", "balance_limit": "0"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(4, "market_kosdaq_20100104", "market", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "20100104", "market": "KOSDAQ", "balance_limit": "0"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(5, "market_kospi_20000104", "market", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "20000104", "market": "KOSPI", "balance_limit": "0"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(6, "market_kosdaq_20000104", "market", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "20000104", "market": "KOSDAQ", "balance_limit": "0"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(7, "market_kospi_19970103", "market", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "19970103", "market": "KOSPI", "balance_limit": "0"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(8, "market_kosdaq_19970103", "market", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "19970103", "market": "KOSDAQ", "balance_limit": "0"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(9, "market_kospi_19950104", "market", "dbms/MDC/STAT/standard/MDCSTAT03701", {"date": "19950104", "market": "KOSPI", "balance_limit": "0"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(10, "samsung_2026", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "20260102", "todate": "20260814", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "nonempty", SYMBOL_FIELDS),
    ProbeSpec(11, "samsung_2025", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "20250102", "todate": "20251230", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "nonempty", SYMBOL_FIELDS),
    ProbeSpec(12, "samsung_2021", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "20210104", "todate": "20211230", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "boundary", SYMBOL_FIELDS),
    ProbeSpec(13, "samsung_2015", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "20150102", "todate": "20151230", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "boundary", SYMBOL_FIELDS),
    ProbeSpec(14, "samsung_2010", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "20100104", "todate": "20101230", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "boundary", SYMBOL_FIELDS),
    ProbeSpec(15, "samsung_2000", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "20000104", "todate": "20001226", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "boundary", SYMBOL_FIELDS),
    ProbeSpec(16, "samsung_1997", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "19970103", "todate": "19971226", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "boundary", SYMBOL_FIELDS),
    ProbeSpec(17, "samsung_1995", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "19950104", "todate": "19951226", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "boundary", SYMBOL_FIELDS),
    ProbeSpec(18, "samsung_1990", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "19900104", "todate": "19901226", "market": "KOSPI", "symbol": "005930", "isin": "KR7005930003"}, "boundary", SYMBOL_FIELDS),
    ProbeSpec(19, "delisted_kospi_003410", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "20240102", "todate": "20240807", "market": "KOSPI", "symbol": "003410", "isin": "KR7003410008"}, "boundary", SYMBOL_FIELDS),
    ProbeSpec(20, "delisted_kosdaq_030270", "symbol", "dbms/MDC/STAT/standard/MDCSTAT03702", {"fromdate": "20190102", "todate": "20191230", "market": "KOSDAQ", "symbol": "030270", "isin": "KR7030270003"}, "boundary", SYMBOL_FIELDS),
)
assert len(PROBE_MATRIX) == MAX_BUSINESS_REQUESTS


def expected_business_payload(probe: ProbeSpec) -> dict[str, str]:
    scope = probe.scope
    if probe.operation == "market":
        return {
            "searchType": "1", "mktId": MARKET_IDS[scope["market"]],
            "trdDd": scope["date"], "isuLmtRto": scope["balance_limit"],
            "bld": probe.bld,
        }
    if probe.operation == "symbol":
        return {
            "searchType": "2", "strtDd": scope["fromdate"],
            "endDd": scope["todate"], "isuCd": scope["isin"],
            "bld": probe.bld,
        }
    raise PilotStopped(f"UNKNOWN_OPERATION:{probe.operation}")


class PilotStopped(RuntimeError):
    pass


class PilotLocked(PilotStopped):
    pass


class BudgetExceeded(PilotStopped):
    pass


class ArtifactSafetyError(PilotStopped):
    pass


class CredentialLeakDetected(PilotStopped):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def matrix_sha256() -> str:
    body = json.dumps([asdict(item) for item in PROBE_MATRIX], sort_keys=True, separators=(",", ":"))
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


def assert_no_credentials(body: bytes, secrets: Iterable[str]) -> None:
    if any(secret and secret.encode("utf-8") in body for secret in secrets):
        raise CredentialLeakDetected("credential value detected in diagnostic artifact")


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_bytes_atomic_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ArtifactSafetyError(f"refusing to overwrite Landing body: {path.name}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise ArtifactSafetyError(f"Landing body appeared during atomic write: {path.name}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class AppendOnlyLedger:
    def __init__(self, path: Path, *, secrets: Iterable[str]) -> None:
        self.path = path
        self.secrets = tuple(item for item in secrets if item)
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **fields: object) -> None:
        payload = redact({"event": event, "recorded_at_utc": utc_now(), **fields}, self.secrets)
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        assert_no_credentials(encoded, self.secrets)
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@contextmanager
def shared_d_owned_krx_lock(path: Path, *, run_id: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise PilotLocked(f"D-owned KRX stream lock exists: {path}") from None
    try:
        payload = {"owner": "D", "stream": "KRX", "task": "C010", "run_id": run_id, "pid": os.getpid(), "token": token}
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
            raise PilotLocked("cannot verify D-owned KRX lock; lock retained") from error
        if existing.get("token") != token or existing.get("run_id") != run_id:
            raise PilotLocked("D-owned KRX lock ownership changed; lock retained")
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


def _decimal(value: object) -> Decimal | None:
    text = str(value).replace(",", "").strip()
    if text in {"", "-"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise PilotStopped(f"INVALID_NUMERIC_VALUE:{value}") from error


def audit_source_rows(probe: ProbeSpec, rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    null_counts = {field: 0 for field in MEASURE_FIELDS}
    relationship_violations = 0
    for row in rows:
        parsed = {field: _decimal(row.get(field)) for field in MEASURE_FIELDS}
        for field, value in parsed.items():
            null_counts[field] += value is None
            if value is not None and value < 0:
                raise PilotStopped(f"NEGATIVE_MEASURE:{probe.name}:{field}")
        listed, held, share_ratio, limit, exhaustion = (parsed[field] for field in MEASURE_FIELDS)
        if listed not in {None, Decimal(0)} and held is not None and share_ratio is not None:
            relationship_violations += abs(held / listed * 100 - share_ratio) > Decimal("0.02")
        if limit not in {None, Decimal(0)} and held is not None and exhaustion is not None:
            relationship_violations += abs(held / limit * 100 - exhaustion) > Decimal("0.02")
    return {"null_counts": null_counts, "ratio_relationship_violations": relationship_violations}


def classify_business_body(probe: ProbeSpec, body: bytes) -> tuple[str, int, dict[str, object]]:
    if body.lstrip().startswith(b"<"):
        raise PilotStopped(f"HTML_OR_RESTRICTION_RESPONSE:{probe.name}")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PilotStopped(f"NON_JSON_RESPONSE:{probe.name}") from error
    if not isinstance(payload, dict) or payload.get("_error_code") or payload.get("error") or payload.get("errors"):
        raise PilotStopped(f"SOURCE_ERROR_PAYLOAD:{probe.name}")
    rows = payload.get("output")
    if not isinstance(rows, list):
        raise PilotStopped(f"EXPECTED_OUTPUT_MISSING:{probe.name}")
    if not rows:
        if probe.expectation == "nonempty":
            raise PilotStopped(f"ANOMALOUS_EMPTY:{probe.name}")
        return "COVERAGE_EMPTY", 0, {"null_counts": {}, "ratio_relationship_violations": 0}
    for row in rows:
        if not isinstance(row, dict):
            raise PilotStopped(f"INVALID_ROW:{probe.name}")
        missing = set(probe.required_fields) - set(row)
        if missing:
            raise PilotStopped(f"SCHEMA_MISMATCH:{probe.name}:{','.join(sorted(missing))}")
    return "SUCCESS", len(rows), audit_source_rows(probe, rows)
