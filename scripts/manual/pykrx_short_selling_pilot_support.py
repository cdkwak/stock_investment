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
MAX_BUSINESS_REQUESTS = 25
MAX_RAW_HTTP_REQUESTS = 33
MAX_RECOVERED_RESUME_RAW_HTTP_REQUESTS = 38
MAX_SECOND_RECOVERED_RESUME_RAW_HTTP_REQUESTS = 40
HTTP_TIMEOUT_SECONDS = 20
MIN_BUSINESS_INTERVAL_SECONDS = 5.0
MAX_JITTER_SECONDS = 0.25

AUTH_ENDPOINT_PATHS = frozenset(
    {
        "/contents/MDC/COMS/client/MDCCOMS001.cmd",
        "/contents/MDC/COMS/client/view/login.jsp",
        "/contents/MDC/COMS/client/MDCCOMS001D1.cmd",
    }
)
BUSINESS_ENDPOINT_PATH = "/comm/bldAttendant/getJsonData.cmd"

MARKET_IDS = {"KOSPI": "STK", "KOSDAQ": "KSQ"}
MARKET_TYPE_CODES = {"KOSPI": 1, "KOSDAQ": 2}
STOCK_SECURITY_GROUP = ("STMFRTSCIFDRFS",)


@dataclass(frozen=True)
class ProbeSpec:
    sequence: int
    name: str
    operation: str
    bld: str
    scope: Mapping[str, object]
    expectation: str
    required_fields: tuple[str, ...]


TRADING_MARKET_FIELDS = (
    "ISU_CD", "ISU_ABBRV", "SECUGRP_NM", "CVSRTSELL_TRDVOL",
    "ACC_TRDVOL", "TRDVOL_WT", "CVSRTSELL_TRDVAL", "ACC_TRDVAL",
    "TRDVAL_WT",
)
TRADING_SYMBOL_FIELDS = (
    "TRD_DD", "CVSRTSELL_TRDVOL", "ACC_TRDVOL", "TRDVOL_WT",
    "CVSRTSELL_TRDVAL", "ACC_TRDVAL", "TRDVAL_WT",
)
BALANCE_MARKET_FIELDS = (
    "ISU_CD", "ISU_ABBRV", "BAL_QTY", "LIST_SHRS", "BAL_AMT", "MKTCAP",
    "BAL_RTO",
)
BALANCE_SYMBOL_FIELDS = (
    "RPT_DUTY_OCCR_DD", "BAL_QTY", "LIST_SHRS", "BAL_AMT", "MKTCAP",
    "BAL_RTO",
)
INVESTOR_FIELDS = (
    "TRD_DD", "STR_CONST_VAL1", "STR_CONST_VAL2", "STR_CONST_VAL3",
    "STR_CONST_VAL4", "STR_CONST_VAL5",
)
STATUS_FIELDS = (
    "TRD_DD", "CVSRTSELL_TRDVOL", "UPTICKRULE_APPL_TRDVOL",
    "UPTICKRULE_EXCPT_TRDVOL", "STR_CONST_VAL1", "CVSRTSELL_TRDVAL",
    "UPTICKRULE_APPL_TRDVAL", "UPTICKRULE_EXCPT_TRDVAL", "STR_CONST_VAL2",
)


PROVISIONAL_FIELD_INVENTORY = {
    "MDCSTAT30101": {
        "key": ("request_date", "market", "ISU_CD"),
        "fields": TRADING_MARKET_FIELDS,
        "provisional_mapping": {
            "ISU_CD": "symbol", "ISU_ABBRV": "source_name",
            "SECUGRP_NM": "source_security_type",
            "CVSRTSELL_TRDVOL": "short_volume", "ACC_TRDVOL": "total_trading_volume",
            "TRDVOL_WT": "short_volume_ratio",
            "CVSRTSELL_TRDVAL": "short_trading_value",
            "ACC_TRDVAL": "total_trading_value",
            "TRDVAL_WT": "short_trading_value_ratio",
        },
    },
    "MDCSTAT30501": {
        "key": ("request_obligation_date", "market", "ISU_CD"),
        "fields": BALANCE_MARKET_FIELDS,
        "provisional_mapping": {
            "ISU_CD": "symbol", "ISU_ABBRV": "source_name",
            "BAL_QTY": "short_balance", "LIST_SHRS": "shares_outstanding",
            "BAL_AMT": "short_balance_value", "MKTCAP": "market_cap",
            "BAL_RTO": "short_balance_ratio",
        },
        "pit_restriction": "request date is not a verified historical availability date",
    },
    "MDCSTAT30301": {
        "key": ("TRD_DD", "market", "metric", "investor_type"),
        "fields": INVESTOR_FIELDS,
        "investor_mapping": {
            "STR_CONST_VAL1": "institution", "STR_CONST_VAL2": "individual",
            "STR_CONST_VAL3": "foreign", "STR_CONST_VAL4": "other",
            "STR_CONST_VAL5": "total",
        },
    },
    "MDCSTAT30102": {"qa_only": True, "fields": TRADING_SYMBOL_FIELDS},
    "MDCSTAT30502": {"qa_only": True, "fields": BALANCE_SYMBOL_FIELDS},
    "MDCSTAT30001": {"qa_only": True, "fields": STATUS_FIELDS},
}


def _probe(
    sequence: int,
    name: str,
    operation: str,
    bld_suffix: str,
    scope: Mapping[str, object],
    expectation: str,
    fields: Sequence[str],
) -> ProbeSpec:
    return ProbeSpec(
        sequence=sequence,
        name=name,
        operation=operation,
        bld=f"dbms/MDC/STAT/srt/{bld_suffix}",
        scope=dict(scope),
        expectation=expectation,
        required_fields=tuple(fields),
    )


def build_probe_matrix() -> tuple[ProbeSpec, ...]:
    probes: list[ProbeSpec] = []

    def add(name, operation, bld, scope, expectation, fields):
        probes.append(_probe(len(probes) + 1, name, operation, bld, scope, expectation, fields))

    for date, label, expectation in (
        ("20260810", "recent", "nonempty"),
        ("20080102", "coverage_sentinel", "boundary"),
    ):
        for market in ("KOSPI", "KOSDAQ"):
            add(
                f"trading_market_{label}_{market.lower()}", "trading_market",
                "MDCSTAT30101", {"date": date, "market": market}, expectation,
                TRADING_MARKET_FIELDS,
            )

    for date, label, expectation in (
        ("20260807", "recent", "nonempty"),
        ("20160630", "coverage_sentinel", "boundary"),
    ):
        for market in ("KOSPI", "KOSDAQ"):
            add(
                f"balance_market_{label}_{market.lower()}", "balance_market",
                "MDCSTAT30501", {"date": date, "market": market}, expectation,
                BALANCE_MARKET_FIELDS,
            )

    for date, label, expectation in (
        ("20260810", "recent", "nonempty"),
        ("20080102", "coverage_sentinel", "boundary"),
    ):
        for market in ("KOSPI", "KOSDAQ"):
            for metric in ("volume", "trading_value"):
                add(
                    f"investor_{label}_{market.lower()}_{metric}", "investor",
                    "MDCSTAT30301",
                    {"fromdate": date, "todate": date, "market": market, "metric": metric},
                    expectation, INVESTOR_FIELDS,
                )

    add(
        "trading_symbol_recent_listed", "trading_symbol", "MDCSTAT30102",
        {"fromdate": "20260810", "todate": "20260810", "symbol": "005930", "isin": "KR7005930003"},
        "nonempty", TRADING_SYMBOL_FIELDS,
    )
    add(
        "trading_symbol_historical_delisted", "trading_symbol", "MDCSTAT30102",
        {"fromdate": "20240708", "todate": "20240708", "symbol": "003410", "isin": "KR7003410008"},
        "nonempty", TRADING_SYMBOL_FIELDS,
    )
    add(
        "balance_symbol_recent_listed", "balance_symbol", "MDCSTAT30502",
        {"fromdate": "20260807", "todate": "20260807", "symbol": "005930", "isin": "KR7005930003"},
        "nonempty", BALANCE_SYMBOL_FIELDS,
    )
    add(
        "balance_symbol_historical_delisted", "balance_symbol", "MDCSTAT30502",
        {"fromdate": "20240708", "todate": "20240708", "symbol": "003410", "isin": "KR7003410008"},
        "boundary", BALANCE_SYMBOL_FIELDS,
    )
    add(
        "status_symbol_recent_listed", "status_symbol", "MDCSTAT30001",
        {"fromdate": "20260807", "todate": "20260807", "symbol": "005930", "isin": "KR7005930003"},
        "nonempty", STATUS_FIELDS,
    )
    add(
        "trading_symbol_weekend_valid_empty", "trading_symbol", "MDCSTAT30102",
        {"fromdate": "20260809", "todate": "20260809", "symbol": "005930", "isin": "KR7005930003"},
        "empty", TRADING_SYMBOL_FIELDS,
    )
    add(
        "balance_symbol_weekend_valid_empty", "balance_symbol", "MDCSTAT30502",
        {"fromdate": "20260809", "todate": "20260809", "symbol": "005930", "isin": "KR7005930003"},
        "empty", BALANCE_SYMBOL_FIELDS,
    )
    add(
        "investor_weekend_valid_empty", "investor", "MDCSTAT30301",
        {"fromdate": "20260809", "todate": "20260809", "market": "KOSPI", "metric": "volume"},
        "empty", INVESTOR_FIELDS,
    )
    add(
        "trading_symbol_kosdaq_historical_delisted", "trading_symbol", "MDCSTAT30102",
        {"fromdate": "20191230", "todate": "20191230", "symbol": "030270", "isin": "KR7030270003"},
        "boundary", TRADING_SYMBOL_FIELDS,
    )
    if len(probes) != MAX_BUSINESS_REQUESTS:
        raise AssertionError(f"probe matrix must contain exactly {MAX_BUSINESS_REQUESTS} calls")
    return tuple(probes)


PROBE_MATRIX = build_probe_matrix()


def matrix_sha256(probes: Sequence[ProbeSpec] = PROBE_MATRIX) -> str:
    payload = json.dumps([asdict(probe) for probe in probes], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PilotStopped(RuntimeError):
    pass


class PilotLocked(PilotStopped):
    pass


class LockOwnershipError(PilotStopped):
    pass


class BudgetExceeded(PilotStopped):
    pass


class ResumeSafetyError(PilotStopped):
    pass


class CredentialLeakDetected(PilotStopped):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


_SENSITIVE_KEY = re.compile(r"(?i)(password|passwd|pw|secret|token|cookie|authorization|krx_id|krx_pw)")


def redact(value: object, credential_values: Iterable[str] = ()) -> object:
    secrets = tuple(secret for secret in credential_values if secret)
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        result = value
        for secret in secrets:
            result = result.replace(secret, "[REDACTED]")
        result = re.sub(
            r"(?i)\b(KRX_ID|KRX_PW|PASSWORD|TOKEN|SECRET)(\s*[:=]\s*)([^\s,;]+)",
            r"\1\2[REDACTED]", result,
        )
        return result
    return value


def assert_no_credentials(content: bytes, credential_values: Iterable[str]) -> None:
    for secret in credential_values:
        if secret and secret.encode("utf-8") in content:
            raise CredentialLeakDetected("credential value detected before artifact write")


def write_bytes_atomic_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ResumeSafetyError(f"refusing to overwrite existing artifact: {path.name}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise ResumeSafetyError(f"artifact appeared during atomic write: {path.name}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class AppendOnlyLedger:
    def __init__(self, path: Path, *, credential_values: Iterable[str] = ()) -> None:
        self.path = path
        self.credential_values = tuple(secret for secret in credential_values if secret)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **fields: object) -> None:
        payload = redact({"event": event, "recorded_at_utc": utc_now(), **fields}, self.credential_values)
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        assert_no_credentials(encoded, self.credential_values)
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def records(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        records = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ResumeSafetyError(f"ledger line {number} is not valid JSON") from error
        return records


@contextmanager
def d_owned_run_lock(path: Path, *, run_id: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    payload = json.dumps({"owner": "D", "run_id": run_id, "pid": os.getpid(), "token": token})
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise PilotLocked(f"D-owned pilot lock already exists: {path}") from None
    try:
        os.write(descriptor, payload.encode("utf-8"))
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
            raise LockOwnershipError("cannot verify D-owned lock before release") from error
        if existing.get("token") != token or existing.get("run_id") != run_id:
            raise LockOwnershipError("D-owned lock ownership changed; lock retained")
        path.unlink()


class BusinessThrottle:
    def __init__(
        self,
        *,
        min_interval_seconds: float = MIN_BUSINESS_INTERVAL_SECONDS,
        max_jitter_seconds: float = MAX_JITTER_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        jitter_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.max_jitter_seconds = max_jitter_seconds
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.jitter_fn = jitter_fn
        self._last_started_at: float | None = None

    def before_business_request(self) -> float:
        now = self.monotonic_fn()
        slept = 0.0
        if self._last_started_at is not None:
            target = self.min_interval_seconds + self.jitter_fn(0.0, self.max_jitter_seconds)
            remaining = target - (now - self._last_started_at)
            if remaining > 0:
                self.sleep_fn(remaining)
                slept = remaining
        self._last_started_at = self.monotonic_fn()
        return slept


def initial_checkpoint(run_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "matrix_sha256": matrix_sha256(),
        "status": "CREATED",
        "raw_http_requests": 0,
        "completed": {},
        "updated_at_utc": utc_now(),
    }


def load_checkpoint(path: Path, *, run_id: str) -> dict[str, object]:
    if not path.exists():
        return initial_checkpoint(run_id)
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("run_id") != run_id:
        raise ResumeSafetyError("checkpoint run_id mismatch")
    if checkpoint.get("matrix_sha256") != matrix_sha256():
        raise ResumeSafetyError("checkpoint probe matrix mismatch")
    if not isinstance(checkpoint.get("completed"), dict):
        raise ResumeSafetyError("checkpoint completed map is invalid")
    return checkpoint


def verify_completed_artifacts(run_dir: Path, checkpoint: Mapping[str, object]) -> None:
    completed = checkpoint.get("completed", {})
    assert isinstance(completed, Mapping)
    expected_prefix = {probe.name for probe in PROBE_MATRIX[:len(completed)]}
    if set(completed) != expected_prefix:
        raise ResumeSafetyError("completed probes are not an exact matrix prefix")
    for name, record in completed.items():
        if not isinstance(record, Mapping):
            raise ResumeSafetyError(f"invalid checkpoint record for {name}")
        body_file = record.get("body_file")
        expected_hash = record.get("body_sha256")
        if not isinstance(body_file, str) or not isinstance(expected_hash, str):
            raise ResumeSafetyError(f"incomplete checkpoint record for {name}")
        path = run_dir / body_file
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ResumeSafetyError(f"Landing/checkpoint mismatch for {name}")


def reconstruct_raw_request_count(records: Iterable[Mapping[str, object]]) -> int:
    sequences = [
        int(record["raw_sequence"])
        for record in records
        if record.get("event") in {"HTTP_RESPONSE", "HTTP_ERROR"} and "raw_sequence" in record
    ]
    return max(sequences, default=0)


def classify_business_body(probe: ProbeSpec, body: bytes, *, content_type: str = "") -> tuple[str, int]:
    stripped = body.lstrip()
    # KRX currently labels successful JSON business responses as text/html.
    # Treat the bytes, not that unreliable header, as the restriction signal.
    if stripped.startswith(b"<"):
        raise PilotStopped(f"HTML_OR_RESTRICTION_RESPONSE:{probe.name}")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PilotStopped(f"NON_JSON_RESPONSE:{probe.name}") from error
    if not isinstance(payload, dict):
        raise PilotStopped(f"INVALID_JSON_ROOT:{probe.name}")
    if payload.get("_error_code") or payload.get("error") or payload.get("errors"):
        raise PilotStopped(f"SOURCE_ERROR_PAYLOAD:{probe.name}")
    rows = payload.get("OutBlock_1")
    if not isinstance(rows, list):
        raise PilotStopped(f"EXPECTED_BLOCK_MISSING:{probe.name}")
    if not rows:
        if probe.expectation == "nonempty":
            raise PilotStopped(f"ANOMALOUS_EMPTY:{probe.name}")
        return ("VALID_EMPTY" if probe.expectation == "empty" else "COVERAGE_EMPTY", 0)
    if probe.expectation == "empty":
        if (
            probe.operation == "investor"
            and all(isinstance(row, dict) for row in rows)
            and all(
                str(row.get("TRD_DD", "")).strip() == ""
                and all(
                    str(row.get(field, "")).replace(",", "").strip()
                    in {"0", "0.0"}
                    for field in INVESTOR_FIELDS[1:]
                )
                for row in rows
            )
        ):
            return "VALID_EMPTY_PLACEHOLDER", len(rows)
        raise PilotStopped(f"UNEXPECTED_NONEMPTY:{probe.name}")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PilotStopped(f"INVALID_ROW:{probe.name}:{index}")
        missing = sorted(set(probe.required_fields) - set(row))
        if missing:
            raise PilotStopped(f"SCHEMA_MISMATCH:{probe.name}:{','.join(missing)}")
    return "SUCCESS", len(rows)


def recover_verified_content_type_orphan(
    run_dir: Path,
    checkpoint: dict[str, object],
    ledger_records: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], str] | None:
    """Adopt one response after a narrowly verified parser false positive.

    Recovery is deliberately narrow: the checkpoint reason, next matrix probe,
    exact Landing hash, and the sole matching HTTP 200 ledger record must agree.
    No other orphan or stop reason is made resumable automatically.
    """

    completed = checkpoint.get("completed")
    if not isinstance(completed, dict) or len(completed) >= len(PROBE_MATRIX):
        return None
    probe = PROBE_MATRIX[len(completed)]
    reason = checkpoint.get("stop_reason")
    content_type_reason = f"HTML_OR_RESTRICTION_RESPONSE:{probe.name}"
    placeholder_reason = f"UNEXPECTED_NONEMPTY:{probe.name}"
    if checkpoint.get("status") != "STOPPED":
        return None
    if reason == content_type_reason:
        recovery_kind = "content_type_false_positive"
    elif reason == placeholder_reason and probe.name == "investor_weekend_valid_empty":
        recovery_kind = "investor_weekend_zero_placeholder"
    else:
        return None
    body_path = run_dir / landing_body_name(probe)
    if not body_path.is_file():
        return None
    matches = [
        record for record in ledger_records
        if record.get("event") == "HTTP_RESPONSE"
        and record.get("authentication") is False
        and record.get("probe") == probe.name
        and record.get("body_file") == body_path.name
    ]
    if len(matches) != 1 or matches[0].get("status_code") != 200:
        raise ResumeSafetyError("content-type orphan ledger evidence differs")
    body = body_path.read_bytes()
    body_hash = hashlib.sha256(body).hexdigest()
    if matches[0].get("response_sha256") != body_hash:
        raise ResumeSafetyError("content-type orphan Landing hash differs")
    classification, rows = classify_business_body(probe, body)
    record = {
        "business_sequence": probe.sequence,
        "classification": classification,
        "rows": rows,
        "body_file": body_path.name,
        "body_sha256": body_hash,
        "response_bytes": len(body),
    }
    completed[probe.name] = record
    checkpoint["status"] = f"RECOVERED_{recovery_kind.upper()}"
    checkpoint.pop("stop_type", None)
    checkpoint.pop("stop_reason", None)
    checkpoint["updated_at_utc"] = utc_now()
    return record, recovery_kind


def landing_body_name(probe: ProbeSpec) -> str:
    return f"response_{probe.sequence:02d}_{probe.name}.json"


def validate_no_orphan_artifact(run_dir: Path, probe: ProbeSpec, checkpoint: Mapping[str, object]) -> None:
    completed = checkpoint.get("completed", {})
    assert isinstance(completed, Mapping)
    path = run_dir / landing_body_name(probe)
    if probe.name not in completed and path.exists():
        raise ResumeSafetyError(f"orphan Landing body requires audit before resume: {path.name}")
