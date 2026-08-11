"""Offline-safe support for a bounded authenticated KRX ETF pilot."""

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
MAX_BUSINESS_REQUESTS = 5
MAX_RAW_HTTP_REQUESTS = 13
HTTP_TIMEOUT_SECONDS = 20
MIN_BUSINESS_INTERVAL_SECONDS = 8.0
MAX_JITTER_SECONDS = 2.0

AUTH_ENDPOINT_PATHS = frozenset({
    "/contents/MDC/COMS/client/MDCCOMS001.cmd",
    "/contents/MDC/COMS/client/view/login.jsp",
    "/contents/MDC/COMS/client/MDCCOMS001D1.cmd",
})
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"

FULL_MARKET_FIELDS = (
    "ISU_SRT_CD", "ISU_CD", "SECUGRP_ID", "ISU_ABBRV",
    "TDD_CLSPRC", "CMPPREVDD_PRC", "FLUC_TP_CD", "FLUC_RT", "NAV",
    "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL", "ACC_TRDVAL",
    "MKTCAP", "INVSTASST_NETASST_TOTAMT", "LIST_SHRS", "IDX_IND_NM",
    "OBJ_STKPRC_IDX", "CMPPREVDD_IDX", "FLUC_TP_CD1", "FLUC_RT1",
)
SYMBOL_FIELDS = (
    "TRD_DD", "TDD_CLSPRC", "FLUC_TP_CD", "CMPPREVDD_PRC", "FLUC_RT",
    "LST_NAV", "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "ACC_TRDVOL",
    "ACC_TRDVAL", "MKTCAP", "INVSTASST_NETASST_TOTAMT", "LIST_SHRS",
    "IDX_IND_NM", "OBJ_STKPRC_IDX", "FLUC_TP_CD1", "CMPPREVDD_IDX",
    "IDX_FLUC_RT",
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


# 069500/KR7069500007 is verified in retained C005 output.  2008-01-02 is a
# canonical trading date and only a source-coverage sentinel, not an earliest
# source date.  Historical universe comes from 04301, never today's ETF list.
PROBE_MATRIX = (
    ProbeSpec(1, "market_recent_20260810", "market", "dbms/MDC/STAT/standard/MDCSTAT04301", {"date": "20260810"}, "nonempty", FULL_MARKET_FIELDS),
    ProbeSpec(2, "market_source_coverage_20080102", "market", "dbms/MDC/STAT/standard/MDCSTAT04301", {"date": "20080102"}, "boundary", FULL_MARKET_FIELDS),
    ProbeSpec(3, "symbol_current_recent_069500", "symbol", "dbms/MDC/STAT/standard/MDCSTAT04501", {"fromdate": "20260810", "todate": "20260810", "symbol": "069500", "isin": "KR7069500007"}, "nonempty", SYMBOL_FIELDS),
    ProbeSpec(4, "symbol_historical_069500_20080102", "symbol", "dbms/MDC/STAT/standard/MDCSTAT04501", {"fromdate": "20080102", "todate": "20080102", "symbol": "069500", "isin": "KR7069500007"}, "boundary", SYMBOL_FIELDS),
    ProbeSpec(5, "symbol_weekend_valid_empty_069500", "symbol", "dbms/MDC/STAT/standard/MDCSTAT04501", {"fromdate": "20260809", "todate": "20260809", "symbol": "069500", "isin": "KR7069500007"}, "empty", SYMBOL_FIELDS),
)
assert len(PROBE_MATRIX) == MAX_BUSINESS_REQUESTS


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
            raise ArtifactSafetyError(f"Landing body appeared during write: {path.name}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class AppendOnlyLedger:
    def __init__(self, path: Path, *, secrets: Iterable[str]) -> None:
        self.path = path
        self.secrets = tuple(item for item in secrets if item)
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


@contextmanager
def shared_d_owned_krx_lock(path: Path, *, run_id: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise PilotLocked(f"D-owned KRX stream lock exists: {path}") from None
    try:
        payload = {"owner": "D", "stream": "KRX", "task": "C011", "run_id": run_id, "pid": os.getpid(), "token": token}
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
    nav_field = "NAV" if probe.operation == "market" else "LST_NAV"
    numeric_fields = (nav_field, "TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC", "ACC_TRDVOL", "ACC_TRDVAL", "OBJ_STKPRC_IDX")
    null_counts = {field: 0 for field in numeric_fields}
    ohlc_violations = 0
    negative_count_violations = 0
    for row in rows:
        values = {field: _decimal(row.get(field)) for field in numeric_fields}
        for field, value in values.items():
            null_counts[field] += value is None
            if field in {"ACC_TRDVOL", "ACC_TRDVAL"} and value is not None and value < 0:
                negative_count_violations += 1
        opn, high, low, close = (values[field] for field in ("TDD_OPNPRC", "TDD_HGPRC", "TDD_LWPRC", "TDD_CLSPRC"))
        if all(item is not None and item > 0 for item in (opn, high, low, close)):
            ohlc_violations += high < max(opn, low, close) or low > min(opn, high, close)
    return {"null_counts": null_counts, "ohlc_violations": ohlc_violations, "negative_count_violations": negative_count_violations}


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
        classification = "VALID_EMPTY" if probe.expectation == "empty" else "COVERAGE_EMPTY"
        return classification, 0, {"null_counts": {}, "ohlc_violations": 0, "negative_count_violations": 0}
    if probe.expectation == "empty":
        raise PilotStopped(f"UNEXPECTED_NONEMPTY:{probe.name}")
    for row in rows:
        if not isinstance(row, dict):
            raise PilotStopped(f"INVALID_ROW:{probe.name}")
        missing = set(probe.required_fields) - set(row)
        if missing:
            raise PilotStopped(f"SCHEMA_MISMATCH:{probe.name}:{','.join(sorted(missing))}")
    return "SUCCESS", len(rows), audit_source_rows(probe, rows)

