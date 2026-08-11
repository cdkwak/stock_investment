"""Offline-safe support for the bounded authenticated KRX fundamentals pilot.

This module intentionally defines a Landing-only feasibility probe.  It is not
a dataset collector and it must never be used to infer a stable normalized
schema before the captured full-market responses are reviewed.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
MIN_BUSINESS_INTERVAL_SECONDS = 8.0
MAX_JITTER_SECONDS = 2.0

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


def matrix_sha256() -> str:
    body = json.dumps([asdict(probe) for probe in PROBE_MATRIX], sort_keys=True, separators=(",", ":"))
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
    rows = payload.get("output")
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
