from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import random
import re
import shutil
import tempfile
import time
from typing import Callable, Iterable, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

from stock_data.contracts.kr_short_selling import SHORT_SELLING_CONTRACTS
from stock_data.providers.pykrx.short_selling import (
    BUSINESS_URL,
    PARSERS,
    RequestScope,
    ShortSellingSourceError,
    balance_scope,
    investor_scope,
    trading_scope,
    validate_balance,
    validate_investor,
    validate_trading,
)


PYKRX_VERSION = "1.2.8"
MARKETS = ("KOSPI", "KOSDAQ")
METRICS = ("volume", "trading_value")
MINIMUM_SOURCE_DATES = {
    "trading": date(2008, 1, 2),
    "balance": date(2016, 6, 30),
    "investor": date(2008, 1, 2),
}
# Default KRX sequential cadence: 4.5 seconds with symmetric ±0.5-second
# jitter.  Individual BLD operations may be elevated when their retained
# restriction history warrants it; callers remain single-stream and retry-zero.
DEFAULT_MIN_INTERVAL_SECONDS = 4.5
DEFAULT_MAX_JITTER_SECONDS = 0.5
HTTP_TIMEOUT_SECONDS = 20
AUTH_PATHS = frozenset(
    {
        "/contents/MDC/COMS/client/MDCCOMS001.cmd",
        "/contents/MDC/COMS/client/view/login.jsp",
        "/contents/MDC/COMS/client/MDCCOMS001D1.cmd",
    }
)


class ShortSellingCollectionStopped(RuntimeError):
    pass


class ShortSellingRunLocked(ShortSellingCollectionStopped):
    pass


class ShortSellingResumeError(ShortSellingCollectionStopped):
    pass


class ShortSellingBudgetExceeded(ShortSellingCollectionStopped):
    pass


@dataclass(frozen=True)
class RawResponse:
    status_code: int
    content: bytes
    content_type: str = "application/json"
    raw_sequence: int | None = None


@dataclass(frozen=True)
class BatchResult:
    dataset: str
    planned_scopes: int
    previously_completed_scopes: int
    recovered_scopes: int
    requested_business_calls: int
    completed_now: int
    normalized_rows: int
    raw_http_requests: int
    checkpoint_path: Path
    normalized_root: Path


@dataclass(frozen=True)
class BackfillEstimate:
    through_date: str
    trading_dates: int
    balance_dates: int
    canonical_trading_rows: int
    canonical_balance_rows: int
    canonical_investor_rows: int
    trading_business_requests: int
    balance_business_requests: int
    investor_business_requests: int
    total_business_requests: int
    projected_landing_bytes_from_pilot_median: int
    projected_normalized_parquet_bytes_from_pilot_sample: int
    minimum_runtime_hours: float
    nominal_runtime_hours: float


class SourceClient(Protocol):
    raw_count: int

    def __enter__(self): ...
    def __exit__(self, exc_type, exc, traceback): ...
    def fetch(self, scope: RequestScope) -> RawResponse: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _redact_text(value: object, secrets: Iterable[str] = ()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return re.sub(
        r"(?i)\b(KRX_ID|KRX_PW|PASSWORD|TOKEN|SECRET|COOKIE)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]", text,
    )


def _assert_no_secret(content: bytes, secrets: Iterable[str]) -> None:
    if any(secret and secret.encode("utf-8") in content for secret in secrets):
        raise ShortSellingCollectionStopped("credential value detected before artifact write")


def _atomic_json(path: Path, payload: object) -> None:
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


def _atomic_json_new(path: Path, payload: object) -> None:
    if path.exists():
        raise ShortSellingResumeError(f"refusing to overwrite provenance: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise ShortSellingResumeError(f"provenance appeared during write: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_body_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ShortSellingResumeError(f"refusing to overwrite Landing body: {path}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise ShortSellingResumeError(f"Landing body appeared during write: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class AppendOnlyRedactedLedger:
    def __init__(
        self, path: Path, *, secrets: Iterable[str] = (), run_id: str | None = None,
    ) -> None:
        self.path = path
        self.secrets = tuple(secret for secret in secrets if secret)
        self.run_id = run_id
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **fields: object) -> None:
        safe = {
            key: ("[REDACTED]" if re.search(r"(?i)(cookie|password|secret|token|krx_id|krx_pw)", key)
                  else json.loads(json.dumps(value, default=str)))
            for key, value in fields.items()
        }
        encoded = (
            json.dumps(
                {
                    "event": event, "recorded_at_utc": utc_now(),
                    **({"run_id": self.run_id} if self.run_id else {}), **safe,
                },
                ensure_ascii=False, sort_keys=True,
            ) + "\n"
        ).encode("utf-8")
        for secret in self.secrets:
            encoded = encoded.replace(secret.encode("utf-8"), b"[REDACTED]")
        _assert_no_secret(encoded, self.secrets)
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@contextmanager
def d_owned_short_selling_lock(path: Path, *, run_id: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    payload = json.dumps({"owner": "D", "run_id": run_id, "pid": os.getpid(), "token": token})
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ShortSellingRunLocked("another D-owned KRX process holds the short-selling lock") from None
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
            raise ShortSellingRunLocked("cannot verify D-owned lock; lock retained") from error
        if existing.get("token") != token or existing.get("run_id") != run_id:
            raise ShortSellingRunLocked("D-owned lock ownership changed; lock retained")
        path.unlink()


class ConservativeThrottle:
    def __init__(
        self,
        *,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        max_jitter_seconds: float = DEFAULT_MAX_JITTER_SECONDS,
        endpoint_policies: Mapping[str, tuple[float, float]] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        jitter_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if min_interval_seconds < 4 or not (0 <= max_jitter_seconds <= 2):
            raise ValueError("production throttle requires base>=4 seconds and 0<=jitter<=2 seconds")
        self.min_interval_seconds = min_interval_seconds
        self.max_jitter_seconds = max_jitter_seconds
        self.endpoint_policies = dict(endpoint_policies or {})
        for endpoint, (base, jitter) in self.endpoint_policies.items():
            if base < 4 or not (0 <= jitter <= 2):
                raise ValueError(f"invalid endpoint throttle policy: {endpoint}")
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.jitter_fn = jitter_fn
        self.last_started: float | None = None

    def wait(self, endpoint: str | None = None) -> float:
        now = self.monotonic_fn()
        slept = 0.0
        if self.last_started is not None:
            base, jitter = self.endpoint_policies.get(
                endpoint or "", (self.min_interval_seconds, self.max_jitter_seconds)
            )
            minimum = base + self.jitter_fn(-jitter, jitter)
            remaining = minimum - (now - self.last_started)
            if remaining > 0:
                self.sleep_fn(remaining)
                slept = remaining
        self.last_started = self.monotonic_fn()
        return slept


class AuthenticatedPykrxRawClient:
    """Version-pinned, single-stream raw client. It never retries."""

    def __init__(
        self,
        *,
        project_root: Path,
        ledger: AppendOnlyRedactedLedger,
        max_raw_calls: int,
        initial_raw_count: int = 0,
    ) -> None:
        self.project_root = project_root
        self.ledger = ledger
        self.max_raw_calls = max_raw_calls
        self.raw_count = initial_raw_count
        self.business_raw_count = 0
        self._original = None
        self._session_getter = None
        self._patched = None

    def __enter__(self):
        if importlib.metadata.version("pykrx") != PYKRX_VERSION:
            raise ShortSellingCollectionStopped(f"pykrx must equal {PYKRX_VERSION}")
        load_dotenv(self.project_root / ".env", override=False)
        credentials = tuple(
            value for value in (os.getenv("KRX_ID"), os.getenv("KRX_PW")) if value
        )
        self.ledger.secrets = credentials
        self._original = requests.Session.request

        def patched(session, method, url, **kwargs):
            return self._request(session, method, url, **kwargs)

        self._patched = patched
        requests.Session.request = patched
        try:
            captured = io.StringIO()
            with redirect_stdout(captured), redirect_stderr(captured):
                from pykrx.website.comm import get_session
            self._session_getter = get_session
            session = get_session()
            if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
                raise ShortSellingCollectionStopped("authenticated pykrx session is unavailable")
        except Exception:
            requests.Session.request = self._original
            self._original = None
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._original is not None:
            requests.Session.request = self._original

    def _request(self, session, method, url, **kwargs):
        path = urlsplit(str(url)).path
        if path not in AUTH_PATHS and str(url).split("?", 1)[0] != BUSINESS_URL:
            raise ShortSellingCollectionStopped(f"unapproved KRX endpoint: {path}")
        if self.raw_count >= self.max_raw_calls:
            raise ShortSellingBudgetExceeded("raw HTTP request budget exhausted")
        self.raw_count += 1
        is_auth = path in AUTH_PATHS
        if not is_auth:
            self.business_raw_count += 1
        sequence = self.raw_count
        kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
        started = time.monotonic()
        assert self._original is not None
        try:
            response = self._original(session, method, url, **kwargs)
        except Exception as error:
            self.ledger.append(
                "HTTP_ERROR", raw_sequence=sequence, method=str(method).upper(),
                url=_safe_url(str(url)), error_type=type(error).__name__,
                error=_redact_text(error),
            )
            raise
        self.ledger.append(
            "HTTP_RESPONSE", raw_sequence=sequence, method=str(method).upper(),
            url=_safe_url(str(url)), status_code=response.status_code,
            elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            response_bytes=len(response.content), authentication=is_auth,
            **({} if is_auth else {"response_sha256": hashlib.sha256(response.content).hexdigest()}),
        )
        setattr(response, "_short_selling_raw_sequence", sequence)
        return response

    def fetch(self, scope: RequestScope) -> RawResponse:
        assert self._session_getter is not None
        before_business = self.business_raw_count
        captured = io.StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            session = self._session_getter()
        if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
            raise ShortSellingCollectionStopped("session refresh/authentication failed")
        response = session.post(BUSINESS_URL, data=scope.params, timeout=HTTP_TIMEOUT_SECONDS)
        if self.business_raw_count != before_business + 1:
            raise ShortSellingCollectionStopped("business scope did not use exactly one raw request")
        return RawResponse(
            status_code=response.status_code,
            content=response.content,
            content_type=response.headers.get("Content-Type", ""),
            raw_sequence=getattr(response, "_short_selling_raw_sequence", None),
        )


def load_canonical_trading_dates(
    root: Path, *, start: date, end: date,
) -> tuple[date, ...]:
    if end < start:
        raise ValueError("end date precedes start date")
    dates: set[date] = set()
    for market in MARKETS:
        for year in range(start.year, end.year + 1):
            path = root / f"market={market}" / f"year={year}" / "data.parquet"
            if not path.is_file():
                raise FileNotFoundError(f"canonical universe partition is missing: {path}")
            values = pd.read_parquet(path, columns=["date"])["date"]
            dates.update(pd.to_datetime(values, errors="raise").dt.date)
    selected = tuple(sorted(day for day in dates if start <= day <= end))
    if not selected:
        raise ValueError("canonical calendar contains no trading dates in requested range")
    return selected


def calculate_backfill_estimate(
    *, canonical_root: Path, pilot_run: Path, through_date: date,
) -> BackfillEstimate:
    """Calculate a transparent capacity estimate from the canonical PIT calendar
    and exact full-market pilot bodies. It performs no source requests.
    """
    calendars = {
        dataset: load_canonical_trading_dates(
            canonical_root, start=MINIMUM_SOURCE_DATES[dataset], end=through_date
        )
        for dataset in ("trading", "balance", "investor")
    }
    canonical_rows: dict[str, int] = {}
    for dataset in ("trading", "balance"):
        allowed = set(calendars[dataset])
        rows = 0
        for market in MARKETS:
            for year in range(MINIMUM_SOURCE_DATES[dataset].year, through_date.year + 1):
                path = canonical_root / f"market={market}" / f"year={year}" / "data.parquet"
                values = pd.read_parquet(path, columns=["date"])["date"]
                rows += sum(value in allowed for value in pd.to_datetime(values).dt.date)
        canonical_rows[dataset] = rows

    byte_rates: dict[str, list[float]] = {"trading": [], "balance": [], "investor": []}
    normalized_samples: dict[str, list[pd.DataFrame]] = {
        "trading": [], "balance": [], "investor": [],
    }
    for dataset, indexes in (("trading", range(1, 5)), ("balance", range(5, 9))):
        for index in indexes:
            matches = list(pilot_run.glob(f"response_{index:02d}_*.json"))
            if len(matches) != 1:
                raise FileNotFoundError(f"expected one pilot response for sequence {index}")
            body = matches[0].read_bytes()
            rows = _source_row_count_for_estimate(body)
            if rows:
                byte_rates[dataset].append(len(body) / rows)
                normalized_samples[dataset].append(
                    PARSERS[dataset](
                        body, date="2020-01-02",
                        market="KOSPI" if index % 2 else "KOSDAQ",
                    ).dataframe
                )
    investor_files = []
    for index in range(9, 17):
        matches = list(pilot_run.glob(f"response_{index:02d}_*.json"))
        if len(matches) != 1:
            raise FileNotFoundError(f"expected one pilot response for sequence {index}")
        investor_files.append(matches[0])
    for offset, path in enumerate(investor_files):
        investor_body = path.read_bytes()
        source_rows = _source_row_count_for_estimate(investor_body)
        if source_rows:
            byte_rates["investor"].append(len(investor_body) / source_rows)
        normalized_samples["investor"].append(
            PARSERS["investor"](
                investor_body, market="KOSPI" if offset % 4 < 2 else "KOSDAQ",
                metric="volume" if offset % 2 == 0 else "trading_value",
            ).dataframe
        )
    medians = {
        dataset: float(pd.Series(rates).median()) for dataset, rates in byte_rates.items()
    }
    projected_bytes = round(
        canonical_rows["trading"] * medians["trading"]
        + canonical_rows["balance"] * medians["balance"]
        + len(calendars["investor"]) * len(MARKETS) * len(METRICS) * medians["investor"]
    )
    investor_rows = len(calendars["investor"]) * len(MARKETS) * len(METRICS) * 5
    projected_normalized = 0
    projected_rows = {
        "trading": canonical_rows["trading"],
        "balance": canonical_rows["balance"],
        "investor": investor_rows,
    }
    for dataset, frames in normalized_samples.items():
        sample = pd.concat(frames, ignore_index=True)
        buffer = io.BytesIO()
        stored = sample.copy()
        stored["date"] = pd.to_datetime(stored["date"]).dt.date
        stored.to_parquet(buffer, index=False, engine="pyarrow")
        projected_normalized += round(len(buffer.getvalue()) / len(sample) * projected_rows[dataset])
    requests = {
        "trading": len(calendars["trading"]) * len(MARKETS),
        "balance": len(calendars["balance"]) * len(MARKETS),
        "investor": len(_investor_chunks(calendars["investor"])) * len(MARKETS) * len(METRICS),
    }
    total = sum(requests.values())
    return BackfillEstimate(
        through_date=through_date.isoformat(),
        trading_dates=len(calendars["trading"]), balance_dates=len(calendars["balance"]),
        canonical_trading_rows=canonical_rows["trading"],
        canonical_balance_rows=canonical_rows["balance"],
        canonical_investor_rows=investor_rows,
        trading_business_requests=requests["trading"],
        balance_business_requests=requests["balance"],
        investor_business_requests=requests["investor"], total_business_requests=total,
        projected_landing_bytes_from_pilot_median=projected_bytes,
        projected_normalized_parquet_bytes_from_pilot_sample=projected_normalized,
        minimum_runtime_hours=round(total * 8 / 3600, 2),
        nominal_runtime_hours=round(total * 9 / 3600, 2),
    )


def _source_row_count_for_estimate(body: bytes) -> int:
    payload = json.loads(body)
    rows = payload.get("OutBlock_1") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ShortSellingResumeError("pilot response has no OutBlock_1 list")
    return len(rows)


def _investor_chunks(
    trading_dates: tuple[date, ...], *, max_trading_dates: int | None = None,
) -> list[tuple[date, date]]:
    if max_trading_dates is not None and max_trading_dates < 1:
        raise ValueError("investor max_trading_dates must be positive")
    chunks = []
    index = 0
    while index < len(trading_dates):
        start = trading_dates[index]
        if max_trading_dates is not None:
            end_index = min(index + max_trading_dates - 1, len(trading_dates) - 1)
        else:
            limit = start + timedelta(days=730)
            end_index = index
            while end_index + 1 < len(trading_dates) and trading_dates[end_index + 1] <= limit:
                end_index += 1
        chunks.append((start, trading_dates[end_index]))
        index = end_index + 1
    return chunks


def plan_scopes(
    dataset: str, trading_dates: tuple[date, ...], *,
    investor_max_trading_dates: int | None = None,
) -> tuple[RequestScope, ...]:
    if dataset not in SHORT_SELLING_CONTRACTS:
        raise ValueError(f"unsupported short-selling dataset: {dataset}")
    if not trading_dates or tuple(sorted(set(trading_dates))) != trading_dates:
        raise ValueError("trading_dates must be non-empty, unique, and ascending")
    if trading_dates[0] < MINIMUM_SOURCE_DATES[dataset]:
        raise ValueError(f"{dataset} start precedes smoke-confirmed source boundary")
    korea_today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    if dataset in {"trading", "investor"} and trading_dates[-1] >= korea_today:
        raise ValueError(f"{dataset} enforces a T+1 minimum collection policy")
    scopes = []
    if dataset in {"trading", "balance"}:
        factory = trading_scope if dataset == "trading" else balance_scope
        for day in trading_dates:
            for market in MARKETS:
                scopes.append(factory(day.isoformat(), market))
    else:
        for start, end in _investor_chunks(
            trading_dates, max_trading_dates=investor_max_trading_dates,
        ):
            for market in MARKETS:
                for metric in METRICS:
                    scopes.append(investor_scope(start.isoformat(), end.isoformat(), market, metric))
    return tuple(scopes)


VALIDATORS = {
    "trading": validate_trading,
    "balance": validate_balance,
    "investor": validate_investor,
}


def _restore_partition(frame: pd.DataFrame, dataset: str) -> pd.DataFrame:
    contract = SHORT_SELLING_CONTRACTS[dataset]
    restored = frame[list(contract.column_names)].copy()
    restored["date"] = pd.to_datetime(restored["date"], errors="raise").dt.strftime("%Y-%m-%d")
    restored = restored.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    VALIDATORS[dataset](restored)
    return restored


def upsert_normalized_atomic(frame: pd.DataFrame, *, dataset: str, root: Path) -> None:
    if frame.empty:
        return
    contract = SHORT_SELLING_CONTRACTS[dataset]
    validator = VALIDATORS[dataset]
    validator(frame)
    working = frame.copy()
    working["_year"] = pd.to_datetime(working["date"], errors="raise").dt.year
    for (market, year), incoming in working.groupby(["market", "_year"], sort=True):
        target = root / f"market={market}" / f"year={int(year)}" / "data.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        incoming = incoming.drop(columns="_year").reset_index(drop=True)
        if target.exists():
            existing = _restore_partition(pd.read_parquet(target), dataset)
            keys = list(contract.primary_key)
            incoming_keys = set(map(tuple, incoming[keys].astype(str).to_numpy()))
            keep = ~existing[keys].astype(str).apply(tuple, axis=1).isin(incoming_keys)
            combined = pd.concat([existing.loc[keep], incoming], ignore_index=True)
        else:
            combined = incoming
        combined = combined.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        validator(combined)
        temporary = None
        backup = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".parquet.tmp", prefix=f"{contract.name}_", dir=target.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
            stored = combined.copy()
            stored["date"] = pd.to_datetime(stored["date"]).dt.date
            stored.to_parquet(temporary, index=False, engine="pyarrow")
            verified = _restore_partition(pd.read_parquet(temporary), dataset)
            if not verified.equals(combined):
                raise ShortSellingResumeError("temporary normalized partition differs after read-back")
            if target.exists():
                with tempfile.NamedTemporaryFile(
                    suffix=".parquet.bak", prefix=f"{contract.name}_", dir=target.parent, delete=False
                ) as stream:
                    backup = Path(stream.name)
                shutil.copy2(target, backup)
            os.replace(temporary, target)
            temporary = None
        except Exception:
            if backup is not None and backup.exists():
                os.replace(backup, target)
                backup = None
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if backup is not None:
                backup.unlink(missing_ok=True)


def _new_checkpoint(dataset: str) -> dict[str, object]:
    return {
        "dataset": dataset,
        "contract_version": 2,
        "completed": {},
        "status": "CREATED",
        "updated_at_utc": utc_now(),
    }


def _load_checkpoint(path: Path, dataset: str) -> dict[str, object]:
    if not path.exists():
        return _new_checkpoint(dataset)
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("dataset") != dataset or checkpoint.get("contract_version") != 2:
        raise ShortSellingResumeError("checkpoint identity/version mismatch")
    if not isinstance(checkpoint.get("completed"), dict):
        raise ShortSellingResumeError("checkpoint completed map is invalid")
    return checkpoint


def _parse_scope(scope: RequestScope, body: bytes):
    if scope.dataset == "investor":
        return PARSERS[scope.dataset](body, market=scope.market, metric=scope.metric)
    return PARSERS[scope.dataset](body, date=scope.start_date, market=scope.market)


def _validate_scope_coverage(
    scope: RequestScope, frame: pd.DataFrame, canonical_dates: set[date],
) -> None:
    if scope.dataset != "investor":
        return
    start = datetime.strptime(scope.start_date, "%Y%m%d").date()
    end = datetime.strptime(scope.end_date, "%Y%m%d").date()
    expected = {day.isoformat() for day in canonical_dates if start <= day <= end}
    observed = set(frame["date"].astype(str))
    if observed != expected:
        raise ShortSellingCollectionStopped(
            f"ANOMALOUS_INVESTOR_DATE_COVERAGE:{scope.scope_id}"
        )


def _landing_path(landing_root: Path, scope: RequestScope) -> Path:
    return landing_root / scope.dataset / f"{scope.scope_id}.json"


def _provenance_path(landing_path: Path) -> Path:
    return landing_path.with_name(f"{landing_path.name}.provenance.json")


def _scope_sha256(scope: RequestScope) -> str:
    identity = {
        "dataset": scope.dataset, "scope_id": scope.scope_id,
        "market": scope.market, "start_date": scope.start_date,
        "end_date": scope.end_date, "metric": scope.metric,
        "params": scope.params,
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_landing_provenance_new(
    path: Path, scope: RequestScope, response: RawResponse, *,
    run_id: str, ledger_path: Path, project_root: Path,
) -> None:
    if type(response.raw_sequence) is not int or response.raw_sequence < 1:
        raise ShortSellingResumeError("business response has no positive raw sequence")
    try:
        relative_ledger = ledger_path.resolve().relative_to(project_root.resolve())
    except ValueError as error:
        raise ShortSellingResumeError("ledger is outside project root") from error
    _atomic_json_new(
        _provenance_path(path),
        {
            "version": 2, "dataset": scope.dataset, "scope_id": scope.scope_id,
            "scope_sha256": _scope_sha256(scope),
            "run_id": run_id, "raw_sequence": response.raw_sequence,
            "ledger_relative_path": relative_ledger.as_posix(),
            "http_status_code": response.status_code,
            "content_type": response.content_type,
            "response_bytes": len(response.content),
            "body_sha256": hashlib.sha256(response.content).hexdigest(),
            "captured_at_utc": utc_now(),
        },
    )


def _read_recoverable_landing(
    path: Path, scope: RequestScope, *, project_root: Path,
) -> bytes:
    evidence_path = _provenance_path(path)
    if not evidence_path.is_file():
        raise ShortSellingResumeError(
            f"Landing has no durable HTTP provenance and cannot be adopted: {scope.scope_id}"
        )
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ShortSellingResumeError(f"invalid Landing provenance: {scope.scope_id}") from error
    body = path.read_bytes()
    expected = {
        "version": 2, "dataset": scope.dataset, "scope_id": scope.scope_id,
        "scope_sha256": _scope_sha256(scope), "http_status_code": 200,
        "response_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(),
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ShortSellingResumeError(
            f"Landing provenance/status mismatch; capture retained but not recoverable: {scope.scope_id}"
        )
    _validate_ledger_correlation(evidence, scope=scope, project_root=project_root)
    return body


def _validate_ledger_correlation(
    evidence: Mapping[str, object], *, scope: RequestScope, project_root: Path,
) -> None:
    run_id = evidence.get("run_id")
    sequence = evidence.get("raw_sequence")
    relative = evidence.get("ledger_relative_path")
    if not isinstance(run_id, str) or not run_id or type(sequence) is not int or sequence < 1:
        raise ShortSellingResumeError("Landing provenance has invalid run/sequence identity")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ShortSellingResumeError("Landing provenance has invalid ledger path")
    try:
        ledger_path = (project_root / Path(relative)).resolve()
        ledger_path.relative_to(project_root.resolve())
    except ValueError as error:
        raise ShortSellingResumeError("Landing provenance ledger escapes project root") from error
    if ledger_path.name != "call_ledger.jsonl" or ledger_path.parent.name != run_id:
        raise ShortSellingResumeError("Landing provenance ledger/run path mismatch")
    if not ledger_path.is_file():
        raise ShortSellingResumeError("Landing provenance ledger is missing")
    records = []
    try:
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("ledger record is not an object")
            records.append(record)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ShortSellingResumeError("Landing provenance ledger is invalid") from error
    http_matches = [
        record for record in records
        if record.get("event") == "HTTP_RESPONSE"
        and record.get("run_id") == run_id
        and type(record.get("raw_sequence")) is int
        and record.get("raw_sequence") == sequence
    ]
    scope_matches = [
        record for record in records
        if record.get("event") == "SCOPE_HTTP_CORRELATED"
        and record.get("run_id") == run_id
        and type(record.get("raw_sequence")) is int
        and record.get("raw_sequence") == sequence
        and record.get("scope") == scope.scope_id
        and record.get("scope_sha256") == _scope_sha256(scope)
    ]
    if len(http_matches) != 1 or len(scope_matches) != 1:
        raise ShortSellingResumeError("Landing has no unique ledger response/scope correlation")
    http = http_matches[0]
    required_http = {
        "method": "POST", "url": _safe_url(BUSINESS_URL), "status_code": 200,
        "response_bytes": evidence["response_bytes"],
        "response_sha256": evidence["body_sha256"], "authentication": False,
    }
    if any(http.get(key) != value for key, value in required_http.items()):
        raise ShortSellingResumeError("Landing HTTP ledger correlation mismatch")


def _validate_completed_landing(
    landing_root: Path, completed: Mapping[str, object], scope: RequestScope,
    *, project_root: Path,
) -> bytes:
    record = completed[scope.scope_id]
    if not isinstance(record, Mapping):
        raise ShortSellingResumeError("checkpoint scope record is invalid")
    path = _landing_path(landing_root, scope)
    if not path.is_file():
        raise ShortSellingResumeError(f"Landing/checkpoint mismatch: {scope.scope_id}")
    body = _read_recoverable_landing(path, scope, project_root=project_root)
    if hashlib.sha256(body).hexdigest() != record.get("body_sha256"):
        raise ShortSellingResumeError(f"Landing/checkpoint mismatch: {scope.scope_id}")
    return body


def _validate_completed_normalized(
    *, scope: RequestScope, body: bytes, normalized_root: Path,
    partition_cache: OrderedDict[Path, pd.DataFrame],
) -> None:
    parsed = _parse_scope(scope, body)
    if parsed.classification != "SUCCESS":
        raise ShortSellingResumeError(f"completed scope no longer parses as success: {scope.scope_id}")
    contract = SHORT_SELLING_CONTRACTS[scope.dataset]
    expected = parsed.dataframe.copy()
    expected["_year"] = pd.to_datetime(expected["date"], errors="raise").dt.year
    for (market, year), group in expected.groupby(["market", "_year"], sort=True):
        path = normalized_root / f"market={market}" / f"year={int(year)}" / "data.parquet"
        if path not in partition_cache:
            if not path.is_file():
                raise ShortSellingResumeError(
                    f"completed normalized partition is missing: {scope.scope_id}"
                )
            partition_cache[path] = _restore_partition(pd.read_parquet(path), scope.dataset)
            partition_cache.move_to_end(path)
            while len(partition_cache) > 4:
                partition_cache.popitem(last=False)
        actual_partition = partition_cache[path]
        group = group.drop(columns="_year")
        keys = list(contract.primary_key)
        wanted = set(map(tuple, group[keys].astype(str).to_numpy()))
        selected = actual_partition[
            actual_partition[keys].astype(str).apply(tuple, axis=1).isin(wanted)
        ]
        selected = selected.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        group = group.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        if not selected.equals(group):
            raise ShortSellingResumeError(
                f"completed normalized rows differ from Landing: {scope.scope_id}"
            )


def run_short_selling_batch(
    *,
    dataset: str,
    trading_dates: tuple[date, ...],
    max_business_calls: int,
    project_root: Path,
    client_factory: Callable[[AppendOnlyRedactedLedger], SourceClient],
    throttle: ConservativeThrottle | None = None,
    landing_root: Path | None = None,
    normalized_root: Path | None = None,
    checkpoint_path: Path | None = None,
    lock_path: Path | None = None,
    investor_max_trading_dates: int | None = None,
) -> BatchResult:
    if max_business_calls < 1:
        raise ValueError("max_business_calls must be positive and explicit")
    scopes = plan_scopes(
        dataset, trading_dates, investor_max_trading_dates=investor_max_trading_dates,
    )
    landing_root = landing_root or project_root / "data/landing/pykrx/short_selling"
    normalized_root = normalized_root or project_root / "data/normalized" / SHORT_SELLING_CONTRACTS[dataset].name
    checkpoint_path = checkpoint_path or project_root / "data/state" / f"{SHORT_SELLING_CONTRACTS[dataset].name}_v2.json"
    lock_path = lock_path or project_root / "data/state/d_owned_krx_short_selling.lock"
    checkpoint = _load_checkpoint(checkpoint_path, dataset)
    completed = checkpoint["completed"]
    assert isinstance(completed, dict)
    partition_cache: OrderedDict[Path, pd.DataFrame] = OrderedDict()
    for scope in scopes:
        if scope.scope_id in completed:
            body = _validate_completed_landing(
                landing_root, completed, scope, project_root=project_root
            )
            _validate_completed_normalized(
                scope=scope, body=body, normalized_root=normalized_root,
                partition_cache=partition_cache,
            )
    pending = [scope for scope in scopes if scope.scope_id not in completed]
    # Fail before authentication if an uncheckpointed canonical Landing object
    # cannot prove that it came from this exact scope with original HTTP 200.
    for scope in pending:
        path = _landing_path(landing_root, scope)
        if path.exists():
            _read_recoverable_landing(path, scope, project_root=project_root)
    if not pending:
        return BatchResult(
            dataset=dataset, planned_scopes=len(scopes),
            previously_completed_scopes=len(scopes), recovered_scopes=0,
            requested_business_calls=0, completed_now=0, normalized_rows=0,
            raw_http_requests=0, checkpoint_path=checkpoint_path,
            normalized_root=normalized_root,
        )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = landing_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    ledger = AppendOnlyRedactedLedger(run_dir / "call_ledger.jsonl", run_id=run_id)
    throttle = throttle or ConservativeThrottle()
    recovered = calls = completed_now = normalized_rows = 0
    canonical_date_set = set(trading_dates)
    previously_completed = len(scopes) - len(pending)

    with d_owned_short_selling_lock(lock_path, run_id=run_id):
        with client_factory(ledger) as client:
            try:
                for scope in pending:
                    path = _landing_path(landing_root, scope)
                    if path.exists():
                        body = _read_recoverable_landing(
                            path, scope, project_root=project_root
                        )
                        parsed = _parse_scope(scope, body)
                        recovered += 1
                        ledger.append("SCOPE_RECOVERED_WITHOUT_REQUEST", scope=scope.scope_id)
                    else:
                        if calls >= max_business_calls:
                            break
                        slept = throttle.wait(scope.params["bld"])
                        ledger.append(
                            "SCOPE_STARTED", dataset=dataset, scope=scope.scope_id,
                            params={key: value for key, value in scope.params.items() if key != "bld"},
                            bld=scope.params["bld"], throttle_sleep_seconds=round(slept, 6),
                        )
                        response = client.fetch(scope)
                        calls += 1
                        if type(response.raw_sequence) is not int or response.raw_sequence < 1:
                            raise ShortSellingCollectionStopped(
                                "business response has no positive raw sequence"
                            )
                        ledger.append(
                            "SCOPE_HTTP_CORRELATED", scope=scope.scope_id,
                            scope_sha256=_scope_sha256(scope),
                            raw_sequence=response.raw_sequence,
                        )
                        _atomic_body_new(path, response.content)
                        _write_landing_provenance_new(
                            path, scope, response, run_id=run_id,
                            ledger_path=ledger.path, project_root=project_root,
                        )
                        ledger.append(
                            "LANDING_WRITTEN", scope=scope.scope_id, status_code=response.status_code,
                            body_file=str(path.relative_to(project_root)), response_bytes=len(response.content),
                            response_sha256=hashlib.sha256(response.content).hexdigest(),
                        )
                        if response.status_code in {403, 429}:
                            raise ShortSellingCollectionStopped(f"KRX restriction HTTP {response.status_code}")
                        if response.status_code != 200:
                            raise ShortSellingCollectionStopped(f"KRX HTTP {response.status_code}")
                        # The current run is held to the same durable evidence
                        # standard as a later recovery before normalization.
                        verified_body = _read_recoverable_landing(
                            path, scope, project_root=project_root
                        )
                        parsed = _parse_scope(scope, verified_body)
                    if parsed.classification != "SUCCESS":
                        raise ShortSellingCollectionStopped(
                            f"ANOMALOUS_{parsed.classification}:{scope.scope_id}"
                        )
                    _validate_scope_coverage(scope, parsed.dataframe, canonical_date_set)
                    upsert_normalized_atomic(parsed.dataframe, dataset=dataset, root=normalized_root)
                    normalized_rows += len(parsed.dataframe)
                    completed[scope.scope_id] = {
                        "body_file": str(path.relative_to(project_root)),
                        "body_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "classification": parsed.classification,
                        "source_rows": parsed.source_rows,
                        "normalized_rows": len(parsed.dataframe),
                        "completed_at_utc": utc_now(),
                    }
                    checkpoint["status"] = "IN_PROGRESS"
                    checkpoint["updated_at_utc"] = utc_now()
                    _atomic_json(checkpoint_path, checkpoint)
                    completed_now += 1
                    ledger.append(
                        "SCOPE_COMPLETED", scope=scope.scope_id,
                        classification=parsed.classification, normalized_rows=len(parsed.dataframe),
                    )
            except Exception as error:
                checkpoint["status"] = "STOPPED"
                checkpoint["stop_type"] = type(error).__name__
                checkpoint["stop_reason"] = _redact_text(error)
                checkpoint["updated_at_utc"] = utc_now()
                _atomic_json(checkpoint_path, checkpoint)
                ledger.append("RUN_STOPPED", error_type=type(error).__name__, error=_redact_text(error))
                raise
            remaining = sum(scope.scope_id not in completed for scope in scopes)
            checkpoint["status"] = "BATCH_COMPLETE" if remaining == 0 else "BATCH_LIMIT_REACHED"
            checkpoint["updated_at_utc"] = utc_now()
            _atomic_json(checkpoint_path, checkpoint)
            ledger.append(
                "RUN_COMPLETED", dataset=dataset, business_calls=calls,
                completed_now=completed_now, recovered_scopes=recovered,
            )
            raw_count = client.raw_count
    return BatchResult(
        dataset=dataset,
        planned_scopes=len(scopes),
        previously_completed_scopes=previously_completed,
        recovered_scopes=recovered,
        requested_business_calls=calls,
        completed_now=completed_now,
        normalized_rows=normalized_rows,
        raw_http_requests=raw_count,
        checkpoint_path=checkpoint_path,
        normalized_root=normalized_root,
    )
