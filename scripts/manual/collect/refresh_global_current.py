"""Capture-first, review-gated refresh for the retained Yahoo/FRED datasets.

The live command can only create immutable Landing evidence and a candidate
tree.  Publication is a separate, zero-network command guarded by a content
manifest compare-and-swap (CAS).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from uuid import uuid4

import exchange_calendars as xcals
import pandas as pd
import requests
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_data.contracts.global_market import (  # noqa: E402
    EndpointWindowPolicy,
    FRED_TREASURY_YIELD_DAILY, FRED_USD_FX_DAILY, FRED_VIX_DAILY,
    GLOBAL_COMMODITY_FUTURES_DAILY, GLOBAL_INDEX_PRICE_DAILY,
    GLOBAL_INDEX_REGISTRY, GLOBAL_INDEX_SYMBOLS_BY_PROVIDER, US_TREASURY_SPREAD_DAILY,
    US_VIX_TERM_STRUCTURE_DAILY,
    global_index_endpoint_window,
)
from stock_data.contracts.global_etf import GLOBAL_ETF_PRICE_DAILY  # noqa: E402
from stock_data.contracts.global_equity import GLOBAL_EQUITY_PRICE_DAILY  # noqa: E402
from stock_data.derived.treasury_spread import (  # noqa: E402
    calculate_treasury_spreads, validate_treasury_spreads,
)
from stock_data.derived.vix_term_structure import (  # noqa: E402
    INDEX_SYMBOLS as VIX_TERM_INDEX_SYMBOLS,
    calculate_vix_term_structure, validate_vix_term_structure,
)
from stock_data.providers.fred import fetch_series  # noqa: E402
from stock_data.providers.fred import URL as FRED_URL  # noqa: E402
from stock_data.providers.financedatareader_fred import fetch_vixcls  # noqa: E402
from stock_data.providers.cboe_index_history import (  # noqa: E402
    fetch_cboe_index_history,
)
from stock_data.providers.yahoo import (  # noqa: E402
    COMMODITY_CONFIG, CONFIG, EQUITY_REGISTRY, ETF_REGISTRY,
    GLOBAL_ETF_DAILY_SYMBOLS,
    GLOBAL_FUTURES_DAILY_SYMBOLS, _epoch,
    fetch_commodity_future, fetch_global_equity, fetch_global_etf,
    fetch_global_index,
)
from stock_data.contracts.global_equity import GLOBAL_EQUITY_DAILY_SYMBOLS  # noqa: E402
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar  # noqa: E402
from stock_data.orchestration.automatic_fallback import (  # noqa: E402
    AttemptFailure, CircuitRecord, DecisionOutcome, ExecutionKind, FailureKind,
    ProviderRole, SourceObservation, SourceProvenance, ValidationReceipt,
)
from stock_data.orchestration.fred_vix_fallback import execute_vixcls_fallback  # noqa: E402
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic  # noqa: E402
from stock_data.validation.global_market import (  # noqa: E402
    validate_fred, validate_global_commodity_futures, validate_global_equity,
    validate_global_etf, validate_global_index,
)


PHASES = {
    "yahoo": (
        len(GLOBAL_INDEX_SYMBOLS_BY_PROVIDER["yahoo_chart_api"]),
        GLOBAL_INDEX_PRICE_DAILY,
        GLOBAL_INDEX_SYMBOLS_BY_PROVIDER["yahoo_chart_api"],
    ),
    "cboe_index": (
        len(GLOBAL_INDEX_SYMBOLS_BY_PROVIDER["cboe_index_history_csv"]),
        GLOBAL_INDEX_PRICE_DAILY,
        GLOBAL_INDEX_SYMBOLS_BY_PROVIDER["cboe_index_history_csv"],
    ),
    "yahoo_etf": (
        len(GLOBAL_ETF_DAILY_SYMBOLS), GLOBAL_ETF_PRICE_DAILY,
        GLOBAL_ETF_DAILY_SYMBOLS,
    ),
    "yahoo_equity": (
        len(GLOBAL_EQUITY_DAILY_SYMBOLS), GLOBAL_EQUITY_PRICE_DAILY,
        GLOBAL_EQUITY_DAILY_SYMBOLS,
    ),
    "yahoo_dashboard_futures": (
        len(GLOBAL_FUTURES_DAILY_SYMBOLS), GLOBAL_COMMODITY_FUTURES_DAILY,
        GLOBAL_FUTURES_DAILY_SYMBOLS,
    ),
    "fred_yields": (3, FRED_TREASURY_YIELD_DAILY, ("DGS2", "DGS10", "DGS30")),
    "fred_fx": (2, FRED_USD_FX_DAILY, ("DEXKOUS", "DEXJPUS")),
    "fred_vix": (1, FRED_VIX_DAILY, ("VIXCLS",)),
}
LOCK = Path("data/state/global_current_refresh.lock")
REPARSE_POINT = 0x400
YAHOO_PHASES = frozenset({
    "yahoo", "yahoo_etf", "yahoo_equity", "yahoo_dashboard_futures",
})
SYMBOL_PHASES = YAHOO_PHASES | {"cboe_index"}
PROVIDER_NATIVE_ENDPOINT_TOLERANCE_SESSIONS = 5


class RefreshError(RuntimeError):
    pass


def _endpoint_window_policy(phase: str, item: str) -> EndpointWindowPolicy:
    if phase == "yahoo":
        return global_index_endpoint_window(item)
    return EndpointWindowPolicy.STRICT_EXCHANGE


def _move_us_sessions(anchor: date, count: int) -> date:
    calendar = ExchangeTradingCalendar(ExchangeMarket.US)
    result = anchor
    step = calendar.next_trading_day if count >= 0 else calendar.previous_trading_day
    for _ in range(abs(count)):
        result = step(result)
    return result


def _exchange_calendar_name(phase: str, item: str) -> str:
    if phase == "yahoo":
        return str(GLOBAL_INDEX_REGISTRY[item].get("exchange_calendar") or "XNYS")
    return "XNYS"


def _calendar_sessions_in_range(
    calendar_name: str, start: date, end: date,
) -> tuple[date, ...]:
    calendar = xcals.get_calendar(calendar_name)
    lower = max(pd.Timestamp(start), calendar.first_session)
    upper = min(pd.Timestamp(end), calendar.last_session)
    if upper < lower:
        return ()
    return tuple(
        stamp.date() for stamp in calendar.sessions_in_range(lower, upper)
    )


def _latest_calendar_session(calendar_name: str, endpoint: date) -> date:
    sessions = _calendar_sessions_in_range(
        calendar_name, endpoint - timedelta(days=14), endpoint,
    )
    if not sessions:
        raise RefreshError(
            f"no {calendar_name} exchange session found near planned endpoint"
        )
    return sessions[-1]


def _response_covers_endpoint_window(
    *, policy: EndpointWindowPolicy, observed_start: date, observed_end: date,
    planned_start: date, planned_end: date, exchange_calendar: str | None = None,
) -> bool:
    if policy is EndpointWindowPolicy.STRICT_EXCHANGE:
        expected_end = (
            _latest_calendar_session(exchange_calendar, planned_end)
            if exchange_calendar is not None else planned_end
        )
        return observed_start >= planned_start and observed_end == expected_end
    if policy is EndpointWindowPolicy.PROVIDER_NATIVE:
        return (
            observed_start <= _move_us_sessions(
                planned_start, PROVIDER_NATIVE_ENDPOINT_TOLERANCE_SESSIONS,
            )
            and observed_end >= _move_us_sessions(
                planned_end, -PROVIDER_NATIVE_ENDPOINT_TOLERANCE_SESSIONS,
            )
        )
    raise RefreshError(f"unsupported endpoint-window policy: {policy}")


def _select_phase_items(
    phase: str, configured: tuple[str, ...], symbols: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if symbols is None:
        return configured
    if phase not in SYMBOL_PHASES:
        raise RefreshError("symbol selection is supported only for symbol phases")
    selected = tuple(str(item).strip() for item in symbols)
    if not selected or any(not item for item in selected):
        raise RefreshError("at least one non-empty symbol is required")
    if len(selected) != len(set(selected)):
        raise RefreshError("symbol selection contains duplicates")
    unknown = set(selected).difference(configured)
    if unknown:
        raise RefreshError(f"symbol selection is not registered: {sorted(unknown)}")
    return selected


def _landing_root(project_root: Path, phase: str, run_id: str) -> Path:
    if phase == "cboe_index":
        return project_root / "data/landing/cboe_index_history" / run_id
    return project_root / "data/landing/global_current_refresh" / run_id


def _cboe_response_covers_endpoint_window(
    *, observed_end: date, planned_end: date,
) -> bool:
    previous_session = _move_us_sessions(planned_end, -1)
    return previous_session <= observed_end <= planned_end


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _assert_plain_path(base: Path, path: Path, *, must_exist: bool = True) -> Path:
    """Reject escape, links, junctions/reparse points, and unexpected topology."""
    base = Path(os.path.abspath(base))
    absolute = Path(os.path.abspath(path))
    try:
        absolute.relative_to(base)
    except ValueError as error:
        raise RefreshError("path escapes its required root") from error
    current = base
    for component in absolute.relative_to(base).parts:
        current /= component
        if os.path.lexists(current):
            info = current.lstat()
            if current.is_symlink() or (getattr(info, "st_file_attributes", 0) & REPARSE_POINT):
                raise RefreshError("links/reparse points are forbidden in refresh paths")
    if must_exist and not absolute.exists():
        raise RefreshError("required refresh path does not exist")
    return absolute


def _files_manifest(root: Path) -> dict[str, object]:
    if not root.is_dir():
        raise RefreshError(f"dataset root is absent: {root}")
    _assert_plain_path(root.parent, root)
    partition_keys = {
        GLOBAL_INDEX_PRICE_DAILY.name: ("symbol", "year"),
        GLOBAL_ETF_PRICE_DAILY.name: ("symbol", "year"),
        GLOBAL_EQUITY_PRICE_DAILY.name: ("symbol", "year"),
        GLOBAL_COMMODITY_FUTURES_DAILY.name: ("symbol", "year"),
        FRED_TREASURY_YIELD_DAILY.name: ("year",),
        FRED_USD_FX_DAILY.name: ("year",),
        FRED_VIX_DAILY.name: ("year",),
        US_TREASURY_SPREAD_DAILY.name: ("year",),
        US_VIX_TERM_STRUCTURE_DAILY.name: ("year",),
    }.get(root.name)
    if partition_keys is None:
        raise RefreshError(f"unknown dataset topology: {root.name}")
    entries_on_disk = sorted(root.rglob("*"))
    for entry in entries_on_disk:
        _assert_plain_path(root, entry)
        relative = entry.relative_to(root)
        if entry.is_dir():
            parts = relative.parts
            if len(parts) > len(partition_keys):
                raise RefreshError(f"unexpected nested dataset directory: {relative}")
            for number, part in enumerate(parts):
                prefix = partition_keys[number] + "="
                if not part.startswith(prefix) or not part[len(prefix):]:
                    raise RefreshError(f"unexpected dataset directory: {relative}")
    all_files = [path for path in entries_on_disk if path.is_file()]
    files = []
    for path in all_files:
        relative = path.relative_to(root).as_posix()
        parts = Path(relative).parts
        if len(parts) != len(partition_keys) + 1 or parts[-1] != "data.parquet":
            raise RefreshError(f"unexpected dataset topology: {relative}")
        values = {}
        for number, key in enumerate(partition_keys):
            prefix = key + "="
            if not parts[number].startswith(prefix):
                raise RefreshError(f"unexpected dataset topology: {relative}")
            values[key] = parts[number][len(prefix):]
        try:
            year = int(values["year"])
        except ValueError as error:
            raise RefreshError(f"invalid year partition: {relative}") from error
        if year < 1800 or year > 2200:
            raise RefreshError(f"invalid year partition: {relative}")
        _assert_plain_path(root, path)
        files.append(path)
    digest = hashlib.sha256()
    rows = 0
    entries = []
    for path in files:
        body_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(root).as_posix()
        count = len(pd.read_parquet(path, columns=["date"]))
        entries.append({"path": relative, "rows": count, "sha256": body_hash})
        digest.update(relative.encode() + b"\0" + body_hash.encode() + b"\n")
        rows += count
    if not files:
        raise RefreshError("dataset root has no partitions")
    return {"files": len(files), "rows": rows, "manifest_sha256": digest.hexdigest(), "entries": entries}


def _dataset_manifest(root: Path) -> dict[str, object]:
    return _files_manifest(root) if root.is_dir() else {"exists": False}


def _file_fingerprint(path: Path) -> dict[str, object]:
    _assert_plain_path(path.parent, path, must_exist=False)
    if not os.path.lexists(path):
        return {"exists": False}
    if not path.is_file():
        raise RefreshError("state fingerprint target is not a plain file")
    body = path.read_bytes()
    return {"exists": True, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def _fred_retained_end_noop(
    *, phase: str, end: date, items: tuple[str, ...], existing: pd.DataFrame,
    production_state: Path, production_manifest: dict[str, object], contract_name: str,
) -> dict[str, object] | None:
    """Return a verified pre-network no-op when the exact FRED end is retained."""
    if not phase.startswith("fred_"):
        return None
    requested = end.isoformat()
    fully_retained = all(
        not (rows := existing.loc[
            pd.to_numeric(existing[item.lower()], errors="coerce").notna(), "date"
        ]).empty
        and requested in set(rows.astype(str))
        for item in items
    )
    if not fully_retained:
        return None
    if not production_state.is_file():
        raise RefreshError("retained FRED end lacks operational state")
    try:
        state = json.loads(production_state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RefreshError("retained FRED operational state is unreadable") from error
    if state.get("dataset") != contract_name or state.get("phase") != phase:
        raise RefreshError("retained FRED operational state identity mismatch")
    if state.get("candidate_dataset") != production_manifest:
        raise RefreshError("retained FRED operational state manifest mismatch")
    coverage = state.get("coverage")
    if not isinstance(coverage, dict) or any(
        not isinstance(coverage.get(item), dict)
        or coverage[item].get("observed_end") != requested
        for item in items
    ):
        raise RefreshError("retained FRED operational state coverage mismatch")
    return {
        "version": 2,
        "phase": phase,
        "status": "NOOP_IDEMPOTENT",
        "reason": "requested FRED end is retained and state/manifest verified",
        "requested_end": requested,
        "http_calls": 0,
        "normalized_mutation": False,
        "pre_dataset": production_manifest,
        "retained_run_id": state.get("run_id"),
        "predictive_status": "PIT_BLOCKED_PENDING_VINTAGE_RESOLVER",
    }


@contextmanager
def _lock(project_root: Path, run_id: str):
    path = project_root / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RefreshError("global current refresh lock is already held") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(run_id)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        if path.exists() and path.read_text(encoding="utf-8") == run_id:
            path.unlink()


class BudgetSession:
    """A retry-free hard call budget. Providers make exactly one get per item."""
    def __init__(self, limit: int, backend=None):
        self.limit = limit
        self.backend = backend or requests.Session()
        self.calls = 0
        self.statuses: list[int] = []

    def get(self, *args, **kwargs):
        if self.calls >= self.limit:
            raise RefreshError("phase HTTP-call cap reached")
        self.calls += 1
        response = self.backend.get(*args, **kwargs)
        self.statuses.append(int(response.status_code))
        return response


def _circuit_payload(record: CircuitRecord) -> dict[str, object]:
    return {
        "version": 1,
        "route_id": "fred_vix_daily:VIXCLS",
        "is_open": record.is_open,
        "failure_kind": record.failure_kind.value if record.failure_kind else None,
        "safe_code": record.safe_code,
        "generation": record.generation,
    }


def _read_circuit(path: Path) -> CircuitRecord:
    if not path.is_file():
        return CircuitRecord()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or payload.get("route_id") != "fred_vix_daily:VIXCLS":
        raise RefreshError("VIX fallback circuit identity differs")
    kind = payload.get("failure_kind")
    return CircuitRecord(
        is_open=bool(payload.get("is_open")),
        failure_kind=FailureKind(kind) if kind else None,
        safe_code=payload.get("safe_code"),
        generation=int(payload.get("generation", 0)),
    )


def _decision_payload(decision, observation: SourceObservation) -> dict[str, object]:
    provenance = observation.provenance
    return {
        "version": 1,
        "route_id": decision.route_id,
        "execution_kind": decision.execution_kind.value,
        "outcome": decision.outcome.value,
        "selected_role": decision.selected_role.value,
        "preserved_prior": decision.preserved_prior,
        "primary_attempts": decision.primary_attempts,
        "fallback_attempts": decision.fallback_attempts,
        "primary_requests": decision.primary_requests,
        "fallback_requests": decision.fallback_requests,
        "selected_provenance": {
            "provider": provenance.provider,
            "upstream_provider": provenance.upstream_provider,
            "source_route": provenance.source_route,
            "retrieved_at_utc": provenance.retrieved_at_utc,
            "request_count": provenance.request_count,
            "retry_count": provenance.retry_count,
        },
        "events": [
            {
                "sequence": event.sequence,
                "event": event.event,
                "role": event.role.value,
                "provider": event.provider,
                "upstream_provider": event.upstream_provider,
                "source_route": event.source_route,
                "attempt": event.attempt,
                "request_count": event.request_count,
                "retry_count": event.retry_count,
                "failure_kind": event.failure_kind.value if event.failure_kind else None,
                "safe_code": event.safe_code,
                "validation_result": event.validation_result,
                "selected_observation_date": event.selected_observation_date,
                "outcome": event.outcome.value if event.outcome else None,
            }
            for event in decision.events
        ],
    }


class _VixCandidateCircuitStore:
    def __init__(self, production_path: Path, bundle_path: Path) -> None:
        self.production_path = production_path
        self.bundle_path = bundle_path

    def load(self, route_id: str) -> CircuitRecord:
        if route_id != "fred_vix_daily:VIXCLS":
            raise RefreshError("VIX fallback circuit route differs")
        candidate = self.bundle_path / "circuit.json"
        return _read_circuit(candidate if candidate.is_file() else self.production_path)

    def save(self, route_id: str, record: CircuitRecord) -> None:
        if route_id != "fred_vix_daily:VIXCLS":
            raise RefreshError("VIX fallback circuit route differs")
        # A failed fallback changes no numeric data, so its open circuit is the
        # sole atomic state mutation and must survive before the next schedule.
        _atomic_json(self.production_path, _circuit_payload(record))


class _VixCandidatePromotion:
    """Atomically stage selected values, immutable decision, and circuit."""

    def __init__(self, bundle_path: Path, circuit_store: _VixCandidateCircuitStore) -> None:
        self.bundle_path = bundle_path
        self.circuit_store = circuit_store

    def snapshot(self):
        return self.bundle_path.exists()

    def stage(self, observation, decision):
        return observation, decision

    def commit(self, staged) -> None:
        observation, decision = staged
        if self.bundle_path.exists():
            raise RefreshError("VIX fallback candidate bundle already exists")
        stage = self.bundle_path.with_name(f".{self.bundle_path.name}.stage")
        if stage.exists():
            raise RefreshError("VIX fallback candidate stage already exists")
        try:
            stage.mkdir(parents=True)
            observation.value.to_parquet(stage / "selected.parquet", index=False)
            _atomic_json(stage / "decision.json", _decision_payload(decision, observation))
            _atomic_json(stage / "circuit.json", _circuit_payload(decision.circuit_after))
            stage.replace(self.bundle_path)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    def verify_readback(self, observation, decision) -> None:
        restored = pd.read_parquet(self.bundle_path / "selected.parquet")
        if not restored.equals(observation.value.reset_index(drop=True)):
            raise RefreshError("VIX fallback selected-value readback differs")
        if json.loads((self.bundle_path / "decision.json").read_text(encoding="utf-8")) != _decision_payload(decision, observation):
            raise RefreshError("VIX fallback decision readback differs")

    def rollback(self, snapshot) -> None:
        if self.bundle_path.exists() and not snapshot:
            shutil.rmtree(self.bundle_path)


def _vix_validation(observation: SourceObservation[pd.DataFrame]) -> ValidationReceipt:
    frame = observation.value
    if list(frame.columns) != ["date", "vixcls"]:
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR, safe_code="VIX_SCHEMA_COLUMNS",
            request_count=observation.provenance.request_count,
        )
    try:
        validate_fred(frame)
        finite = frame.loc[pd.to_numeric(frame["vixcls"], errors="coerce").notna(), "date"]
        if finite.empty or frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
            raise ValueError("invalid VIX date/value coverage")
    except (ValueError, TypeError) as error:
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR, safe_code="VIX_SCHEMA_VALIDATION",
            request_count=observation.provenance.request_count,
        ) from error
    return ValidationReceipt(str(finite.max()), "fred_vix_daily/v1")


def _execute_vix_route(
    *, project_root: Path, state_root: Path, landing_root: Path,
    start: date, end: date, existing: pd.DataFrame, session,
) -> tuple[pd.DataFrame, dict[str, object], list[int]]:
    bundle = state_root / "fred_vix_fallback_selection"
    production_circuit = project_root / "data/state/automatic_fallback/fred_vix_daily_vixcls.json"
    store = _VixCandidateCircuitStore(production_circuit, bundle)
    promotion = _VixCandidatePromotion(bundle, store)
    primary_budget = BudgetSession(1, session)
    fallback_budget = BudgetSession(2, session)

    def primary_attempt():
        try:
            frame = fetch_series(
                "VIXCLS", start, end=end, session=primary_budget,
                capture_root=landing_root,
            )
        except requests.Timeout as error:
            raise AttemptFailure(
                FailureKind.TIMEOUT, safe_code="FRED_PRIMARY_TIMEOUT",
                request_count=primary_budget.calls,
            ) from error
        except requests.HTTPError as error:
            raise AttemptFailure(
                FailureKind.HTTP_ERROR, safe_code="FRED_PRIMARY_HTTP",
                request_count=primary_budget.calls,
            ) from error
        except requests.RequestException as error:
            raise AttemptFailure(
                FailureKind.HTTP_ERROR, safe_code="FRED_PRIMARY_TRANSPORT",
                request_count=primary_budget.calls,
            ) from error
        except (RuntimeError, ValueError, pd.errors.ParserError) as error:
            raise AttemptFailure(
                FailureKind.SCHEMA_ERROR, safe_code="FRED_PRIMARY_SCHEMA",
                request_count=primary_budget.calls,
            ) from error
        return SourceObservation(
            frame,
            SourceProvenance(
                provider="fred", upstream_provider="FRED",
                source_route="fredgraph_csv:VIXCLS",
                retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                request_count=primary_budget.calls, retry_count=0,
            ),
        )

    def fallback_attempt():
        return fetch_vixcls(
            start=start, end=end, capture_root=landing_root,
            session=fallback_budget,
        )

    decision = execute_vixcls_fallback(
        circuit_store=store,
        primary_attempt=primary_attempt,
        primary_validator=_vix_validation,
        fallback_attempt=fallback_attempt,
        fallback_validator=_vix_validation,
        promotion=promotion,
        prior_valid=existing,
        execution_kind=ExecutionKind.NORMAL_SCHEDULE,
    )
    if decision.selected_role is ProviderRole.NONE:
        raise RefreshError(f"VIX route failed closed: {decision.outcome.value}")
    selected = pd.read_parquet(bundle / "selected.parquet")
    receipt = json.loads((bundle / "decision.json").read_text(encoding="utf-8"))
    statuses = primary_budget.statuses + fallback_budget.statuses
    expected = 3 if decision.outcome is DecisionOutcome.FALLBACK_ACCEPTED else 1
    if primary_budget.calls + fallback_budget.calls != expected or statuses != [200] * expected:
        raise RefreshError("VIX route call/status accounting differs")
    return selected, receipt, statuses


def _finite_latest(frame: pd.DataFrame, columns: tuple[str, ...]) -> dict[str, str]:
    result = {}
    for column in columns:
        selected = frame.loc[pd.to_numeric(frame[column], errors="coerce").notna(), "date"]
        if selected.empty:
            raise RefreshError(f"{column} has no finite source values")
        result[column] = str(selected.max())
    return result


def _fred_as_of_observations(
    frame_by_item: dict[str, pd.DataFrame], captures: list[dict[str, object]],
    landing_root: Path,
) -> list[dict[str, object]]:
    """Describe exactly what the current FRED CSV retrieval showed.

    The public fredgraph CSV route does not expose ALFRED realtime periods or
    the series ``last_updated`` field.  Those values therefore remain null;
    they are never inferred from the observation date or capture timestamp.
    """
    capture_by_item = {str(item["item"]): item for item in captures}
    rows: list[dict[str, object]] = []
    for item in sorted(frame_by_item):
        frame = frame_by_item[item]
        column = item.lower()
        finite = frame.loc[pd.to_numeric(frame[column], errors="coerce").notna(), ["date", column]]
        if finite.empty:
            raise RefreshError(f"{item} has no finite as-retrieved observation")
        latest = finite.sort_values("date", kind="stable").iloc[-1]
        call_path = landing_root / str(capture_by_item[item]["path"])
        call = json.loads(call_path.read_text(encoding="utf-8"))
        rows.append({
            "series_id": item,
            "observation_date": str(latest["date"]),
            "value": float(latest[column]),
            "retrieved_at": str(call["captured_at_utc"]),
            "realtime_start": None,
            "realtime_end": None,
            "series_last_updated": None,
            "vintage_metadata_status": "UNAVAILABLE_FROM_FREDGRAPH_CSV",
            "source": "FRED fredgraph.csv",
            "operational_status": "CURRENT_AS_RETRIEVED",
            "predictive_status": "PIT_BLOCKED_PENDING_VINTAGE_RESOLVER",
        })
    return rows


def _merge(existing: pd.DataFrame, incoming: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    incoming_keys = pd.MultiIndex.from_frame(incoming[keys])
    existing_keys = pd.MultiIndex.from_frame(existing[keys])
    result = pd.concat([existing.loc[~existing_keys.isin(incoming_keys)], incoming], ignore_index=True)
    return result.sort_values(keys, kind="stable").reset_index(drop=True)


def _series_revision(
    existing: pd.DataFrame, incoming: pd.DataFrame, *, item: str, phase: str,
    planned_start: str | None = None, planned_end: str | None = None,
) -> dict[str, object]:
    if phase in SYMBOL_PHASES:
        old = existing.loc[existing.symbol.eq(item)].set_index("date")
        new = incoming.loc[incoming.symbol.eq(item)].set_index("date")
        columns = ["open", "high", "low", "close", "volume", "source_ticker"]
        if phase in {"yahoo_etf", "yahoo_equity"}:
            columns += ["adjusted_close", "currency", "exchange", "provider", "adjustment_status"]
        elif phase == "yahoo_dashboard_futures":
            columns += ["asset", "ohlc_status"]
    else:
        column = item.lower()
        old = existing.set_index("date")[[column]]
        new = incoming.set_index("date")[[column]]
        columns = [column]
    overlap = old.index.intersection(new.index)
    revised_finite = finite_to_null = null_to_finite = 0
    for column in columns:
        left, right = old.loc[overlap, column], new.loc[overlap, column]
        finite_to_null += int((left.notna() & right.isna()).sum())
        null_to_finite += int((left.isna() & right.notna()).sum())
        revised_finite += int((left.notna() & right.notna() & ~left.eq(right)).sum())
    lower = pd.Timestamp(planned_start) if planned_start else pd.to_datetime(incoming["date"]).min()
    upper = pd.Timestamp(planned_end) if planned_end else pd.to_datetime(incoming["date"]).max()
    bounded = old.loc[pd.to_datetime(old.index).to_series(index=old.index).between(lower, upper)]
    return {
        "item": item, "response_start": str(incoming.date.min()), "response_end": str(incoming.date.max()),
        "overlap_rows": len(overlap), "inserted_rows": len(new.index.difference(old.index)),
        "revised_finite_cells": revised_finite, "finite_to_null_cells": finite_to_null,
        "null_to_finite_cells": null_to_finite,
        "source_omitted_existing_dates": len(set(bounded.index) - set(new.index)),
    }


def _verify_captures(
    landing_root: Path, phase: str, plan: list[dict[str, str]], *,
    vix_fallback: bool = False,
) -> list[dict[str, object]]:
    if phase == "cboe_index":
        expected_names = {
            name
            for entry in plan
            for name in (f"{entry['item']}.csv", f"{entry['item']}.json")
        }
        actual_names = {entry.name for entry in landing_root.iterdir()}
        if actual_names != expected_names or any(not entry.is_file() for entry in landing_root.iterdir()):
            raise RefreshError("Cboe Landing root contains unexpected topology")
        records = []
        for item_plan in plan:
            item = item_plan["item"]
            path = landing_root / f"{item}.json"
            body = landing_root / f"{item}.csv"
            _assert_plain_path(landing_root, path)
            _assert_plain_path(landing_root, body)
            record = json.loads(path.read_text(encoding="utf-8"))
            required = {
                "capture_version", "provider", "operation", "captured_at_utc",
                "request_url", "request_parameters", "http_status",
                "response_content_type", "response_body_sha256", "response_bytes",
                "landing_body_file",
            }
            content = body.read_bytes()
            if (
                set(record) != required
                or record["capture_version"] != 1
                or record["provider"] != "cboe_index_history_csv"
                or record["operation"] != "daily_history_csv"
                or record["request_url"] != GLOBAL_INDEX_REGISTRY[item]["source_url"]
                or record["request_parameters"] != {"symbol": item}
                or record["landing_body_file"] != body.name
                or int(record["http_status"]) != 200
                or record["response_body_sha256"] != hashlib.sha256(content).hexdigest()
                or record["response_bytes"] != len(content)
                or not isinstance(record["response_content_type"], str)
            ):
                raise RefreshError("Cboe Landing record does not bind exactly to frozen plan")
            try:
                datetime.fromisoformat(str(record["captured_at_utc"]).replace("Z", "+00:00"))
            except (TypeError, ValueError) as error:
                raise RefreshError("Cboe Landing capture timestamp differs") from error
            records.append({
                "item": item,
                "provider": record["provider"],
                "path": path.relative_to(landing_root).as_posix(),
                "body_path": body.relative_to(landing_root).as_posix(),
                "call_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "body_sha256": record["response_body_sha256"],
                "response_bytes": record["response_bytes"],
            })
        return records
    expected_provider = "yahoo" if phase in YAHOO_PHASES else "fred"
    expected_operation = ({
        "yahoo": "chart", "yahoo_etf": "etf_chart_daily",
        "yahoo_equity": "equity_chart_daily",
        "yahoo_dashboard_futures": "commodity_chart_daily",
    }.get(phase)
                          or "fredgraph_csv")
    for entry in landing_root.rglob("*"):
        _assert_plain_path(landing_root, entry)
        parts = entry.relative_to(landing_root).parts
        normal = (
            (len(parts) == 1 and entry.is_dir() and parts[0] == expected_provider)
            or (len(parts) == 2 and entry.is_dir() and parts == (expected_provider, expected_operation))
            or (len(parts) == 3 and entry.is_dir() and parts[:2] == (expected_provider, expected_operation))
            or (len(parts) == 4 and entry.is_file() and parts[:2] == (expected_provider, expected_operation)
                and parts[3] in {"call.json", "response.body"})
        )
        fallback = vix_fallback and (
            (len(parts) == 1 and entry.is_dir() and parts[0] == "fred_via_financedatareader")
            or (len(parts) == 2 and entry.is_dir() and parts[0] == "fred_via_financedatareader"
                and parts[1] in {"fredgraph_csv_1", "fredgraph_csv_2"})
            or (len(parts) == 3 and entry.is_dir() and parts[0] == "fred_via_financedatareader"
                and parts[1] in {"fredgraph_csv_1", "fredgraph_csv_2"})
            or (len(parts) == 4 and entry.is_file() and parts[0] == "fred_via_financedatareader"
                and parts[1] in {"fredgraph_csv_1", "fredgraph_csv_2"}
                and parts[3] in {"call.json", "response.body"})
        )
        if not normal and not fallback:
            raise RefreshError("Landing root contains unexpected topology")
    records = []
    for path in sorted(landing_root.rglob("call.json")):
        _assert_plain_path(landing_root, path)
        record = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "capture_version", "provider", "operation", "captured_at_utc",
            "request_url", "request_parameters", "http_status",
            "response_content_type", "response_body_sha256", "response_bytes",
            "landing_body_file",
        }
        if set(record) != required or record["capture_version"] != 1 or record["landing_body_file"] != "response.body":
            raise RefreshError("Landing call schema/value differs")
        if set(child.name for child in path.parent.iterdir()) != {"call.json", "response.body"}:
            raise RefreshError("Landing call directory topology differs")
        if path.parent.parent.parent != landing_root / record["provider"]:
            raise RefreshError("Landing provider/operation topology differs")
        if not re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z_[0-9a-f]{32}", path.parent.name):
            raise RefreshError("Landing call-directory identity differs")
        try:
            stamp = datetime.fromisoformat(str(record["captured_at_utc"]).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        except (ValueError, TypeError) as error:
            raise RefreshError("Landing capture timestamp differs") from error
        if not path.parent.name.startswith(stamp + "_"):
            raise RefreshError("Landing call-directory timestamp differs")
        body = path.with_name(record["landing_body_file"])
        _assert_plain_path(landing_root, body)
        content = body.read_bytes()
        if (hashlib.sha256(content).hexdigest() != record["response_body_sha256"]
                or len(content) != record["response_bytes"]
                or not isinstance(record["response_content_type"], str)):
            raise RefreshError("Landing body hash differs from call record")
        if int(record.get("http_status", 0)) != 200:
            raise RefreshError("Landing call is not HTTP 200")
        parameters = record.get("request_parameters")
        if not isinstance(parameters, dict):
            raise RefreshError("Landing parameters are absent")
        if phase in YAHOO_PHASES:
            item = parameters.get("symbol")
            item_plan = next((entry for entry in plan if entry["item"] == item), None)
            ticker = (
                CONFIG.get(item, "") if phase == "yahoo" else
                str(ETF_REGISTRY.get(item, {}).get("source_ticker", "")) if phase == "yahoo_etf" else
                str(EQUITY_REGISTRY.get(item, {}).get("source_ticker", "")) if phase == "yahoo_equity" else
                str(COMMODITY_CONFIG.get(item, ("", ""))[0])
            )
            expected_provider = "yahoo"
            expected_operation = (
                "chart" if phase == "yahoo" else
                "etf_chart_daily" if phase == "yahoo_etf" else
                "equity_chart_daily" if phase == "yahoo_equity" else
                "commodity_chart_daily"
            )
            expected_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
            expected_parameters = {
                "symbol": item,
                "period1": str(_epoch(date.fromisoformat(item_plan["start"]))) if item_plan else "",
                "period2": str(_epoch(date.fromisoformat(item_plan["end"]) + timedelta(days=1))) if item_plan else "",
                "interval": "1d", "events": "history",
                "includeAdjustedClose": (
                    "true" if phase in {"yahoo_etf", "yahoo_equity"} else "false"
                ),
            }
        elif record.get("provider") == "fred_via_financedatareader":
            item = parameters.get("id")
            item_plan = next((entry for entry in plan if entry["item"] == item), None)
            expected_provider = "fred_via_financedatareader"
            expected_operation = path.parent.parent.name
            if expected_operation not in {"fredgraph_csv_1", "fredgraph_csv_2"}:
                raise RefreshError("FDR/FRED Landing operation differs")
            expected_url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
            expected_parameters = {
                "id": item,
                "cosd": item_plan["start"] if item_plan else "",
                "coed": item_plan["end"] if item_plan else "",
                "fdr_version": "0.9.202",
            }
        else:
            item = parameters.get("id")
            item_plan = next((entry for entry in plan if entry["item"] == item), None)
            expected_provider, expected_operation = "fred", "fredgraph_csv"
            expected_url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
            expected_parameters = {"id": item, "cosd": item_plan["start"] if item_plan else "", "coed": item_plan["end"] if item_plan else ""}
        if (item_plan is None or record.get("provider") != expected_provider
                or record.get("operation") != expected_operation
                or record.get("request_url") != expected_url or parameters != expected_parameters):
            raise RefreshError("Landing record does not bind exactly to frozen plan")
        records.append({"item": item, "provider": record["provider"],
                        "path": path.relative_to(landing_root).as_posix(),
                        "call_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "body_sha256": record["response_body_sha256"],
                        "response_bytes": record["response_bytes"]})
    expected_count = len(plan) + (2 if vix_fallback else 0)
    if (len(records) != expected_count
            or {record["item"] for record in records} != {entry["item"] for entry in plan}):
        raise RefreshError("Landing call-record count differs")
    return records


def _build_spread_candidate(yields: pd.DataFrame, root: Path) -> dict[str, object]:
    source = yields.copy()
    source["date"] = pd.to_datetime(source["date"]).dt.date
    result = calculate_treasury_spreads(source)
    validation = validate_treasury_spreads(source, result)
    def validator(frame: pd.DataFrame) -> None:
        dates = set(pd.to_datetime(frame["date"]).dt.date)
        selected = source.loc[source["date"].isin(dates)].reset_index(drop=True)
        restored = frame.copy()
        restored["date"] = pd.to_datetime(restored["date"]).dt.date
        validate_treasury_spreads(selected, restored.reset_index(drop=True))
    write_dataset_atomic(result, root, US_TREASURY_SPREAD_DAILY, validator)
    return {"rows": validation.rows, "coverage_start": validation.coverage_start, "coverage_end": validation.coverage_end}


def _build_vix_term_structure_candidate(
    project_root: Path, indices: pd.DataFrame, root: Path,
) -> dict[str, object]:
    vix = read_dataset(
        project_root / "data/normalized" / FRED_VIX_DAILY.name,
        FRED_VIX_DAILY,
        validate_fred,
    )
    result = calculate_vix_term_structure(vix, indices)
    validation = validate_vix_term_structure(vix, indices, result)
    expected = result.copy()
    expected["date"] = pd.to_datetime(expected["date"]).dt.date

    def validator(frame: pd.DataFrame) -> None:
        if tuple(frame.columns) != tuple(US_VIX_TERM_STRUCTURE_DAILY.column_names) or frame.empty:
            raise RefreshError("VIX term-structure candidate schema is empty or differs")
        restored = frame.copy()
        restored["date"] = pd.to_datetime(restored["date"], errors="raise").dt.date
        dates = set(pd.to_datetime(frame["date"]).dt.date)
        selected = expected.loc[expected["date"].isin(dates)].reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(
                restored.reset_index(drop=True), selected,
                check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-12,
            )
        except AssertionError as error:
            raise RefreshError("VIX term-structure candidate differs after read-back") from error

    write_dataset_atomic(result, root, US_VIX_TERM_STRUCTURE_DAILY, validator)
    return {
        "rows": validation.rows,
        "coverage_start": validation.coverage_start,
        "coverage_end": validation.coverage_end,
        "complete_curve_rows": validation.complete_curve_rows,
        "pct_rank_rows": validation.pct_rank_rows,
    }


def _parse_retained_fred_capture(call_path: Path, item: str) -> pd.DataFrame:
    record = json.loads(call_path.read_text(encoding="utf-8"))
    body = call_path.with_name("response.body")
    if hashlib.sha256(body.read_bytes()).hexdigest() != record.get("response_body_sha256"):
        raise RefreshError("retained FRED Landing body hash differs")
    frame = pd.read_csv(StringIO(body.read_bytes().decode("utf-8")))
    if (frame.empty or len(frame.columns) != 2
            or frame.columns[0] not in {"DATE", "observation_date"}
            or frame.columns[1].upper() != item):
        raise RefreshError("retained FRED Landing schema/series identity differs")
    frame.columns = ["date", item.lower()]
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d")
    frame[item.lower()] = pd.to_numeric(frame[item.lower()], errors="coerce")
    finite = frame[item.lower()].dropna()
    if finite.empty or not pd.Series(finite).map(lambda value: pd.notna(value) and abs(float(value)) != float("inf")).all():
        raise RefreshError("retained FRED Landing has no valid finite values")
    if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
        raise RefreshError("retained FRED Landing dates differ")
    return frame


def adopt_stopped_fred_yields(
    project_root: Path, checkpoint_path: Path, *, accepted_observed_end: date,
    confirm_requested_end: date,
) -> dict[str, object]:
    """Offline adoption of one already captured stopped yields run; zero HTTP."""
    project_root = project_root.resolve()
    checkpoint_path = _assert_plain_path(project_root, checkpoint_path)
    stopped = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    run_id = stopped.get("run_id")
    if (stopped.get("phase") != "fred_yields" or stopped.get("status") != "STOPPED"
            or stopped.get("http_calls") != 3 or stopped.get("http_statuses") != [200, 200, 200]
            or stopped.get("retry_count") != 0):
        raise RefreshError("stopped run is not an exact healthy three-call yields capture")
    expected_checkpoint = project_root / "data/state/global_current_refresh" / str(run_id) / "checkpoint.json"
    if checkpoint_path != expected_checkpoint.absolute():
        raise RefreshError("stopped checkpoint topology differs")
    plan = stopped.get("frozen_plan")
    if (not isinstance(plan, list) or [entry.get("item") for entry in plan] != ["DGS2", "DGS10", "DGS30"]
            or {entry.get("end") for entry in plan} != {confirm_requested_end.isoformat()}):
        raise RefreshError("requested-end confirmation or frozen yields plan differs")
    landing = _assert_plain_path(project_root, project_root / "data/landing/global_current_refresh" / run_id)
    captures = _verify_captures(landing, "fred_yields", plan)
    call_by_item = {}
    for capture in captures:
        call_by_item[capture["item"]] = landing / capture["path"]
    frames = {item: _parse_retained_fred_capture(call_by_item[item], item) for item in ("DGS2", "DGS10", "DGS30")}
    as_of_observations = _fred_as_of_observations(frames, captures, landing)
    endpoints = {item: frame.date.max() for item, frame in frames.items()}
    if set(endpoints.values()) != {accepted_observed_end.isoformat()}:
        raise RefreshError("FRED series endpoints are unequal or differ from reviewed observed end")
    for entry in plan:
        frame = frames[entry["item"]]
        if frame.date.min() < entry["start"] or frame.date.max() > entry["end"]:
            raise RefreshError("retained FRED response lies outside frozen requested window")
    production = project_root / "data/normalized" / FRED_TREASURY_YIELD_DAILY.name
    state = project_root / "data/state" / f"{FRED_TREASURY_YIELD_DAILY.name}.json"
    existing = read_dataset(production, FRED_TREASURY_YIELD_DAILY, validate_fred)
    if _files_manifest(production) != stopped["pre_dataset"]:
        raise RefreshError("yield production changed since stopped capture")
    incoming = frames["DGS2"]
    for item in ("DGS10", "DGS30"):
        incoming = incoming.merge(frames[item], on="date", how="outer", validate="one_to_one")
    incoming = incoming.sort_values("date", kind="stable").reset_index(drop=True)
    validate_fred(incoming)
    plan_by_item = {entry["item"]: entry for entry in plan}
    revisions = {item: _series_revision(
        existing, frames[item], item=item, phase="fred_yields",
        planned_start=plan_by_item[item]["start"], planned_end=accepted_observed_end.isoformat(),
    ) for item in frames}
    if any(report["source_omitted_existing_dates"] or report["finite_to_null_cells"] for report in revisions.values()):
        raise RefreshError("retained FRED response omits or nulls retained observations")
    candidate = _merge(existing, incoming, ["date"])
    validate_fred(candidate)
    candidate_parent = project_root / "data/staging/global_current_refresh" / run_id
    candidate_root = candidate_parent / FRED_TREASURY_YIELD_DAILY.name
    if candidate_parent.exists():
        raise RefreshError("adoption candidate path already exists")
    with _lock(project_root, run_id):
        write_dataset_atomic(candidate, candidate_root, FRED_TREASURY_YIELD_DAILY, validate_fred)
        spread_root = candidate_parent / US_TREASURY_SPREAD_DAILY.name
        spread_validation = _build_spread_candidate(candidate, spread_root)
        spread_state = candidate_parent / f"{US_TREASURY_SPREAD_DAILY.name}.state.json"
        _atomic_json(spread_state, {
            "dataset": US_TREASURY_SPREAD_DAILY.name, "status": "artifact_complete_provenance_limited",
            "source_dataset": FRED_TREASURY_YIELD_DAILY.name, "source_manifest": _files_manifest(candidate_root),
            "output_manifest": _files_manifest(spread_root), "validation": spread_validation, "run_id": run_id,
        })
        candidate_manifest = _files_manifest(candidate_root)
        coverage = {item: {"planned_start": plan_by_item[item]["start"],
                           "requested_end": confirm_requested_end.isoformat(),
                           "accepted_observed_end": accepted_observed_end.isoformat(),
                           "observed_start": frames[item].date.min(), "observed_end": frames[item].date.max()}
                    for item in frames}
        state_path = candidate_parent / f"{FRED_TREASURY_YIELD_DAILY.name}.state.json"
        _atomic_json(state_path, {"dataset": FRED_TREASURY_YIELD_DAILY.name,
            "status": "artifact_complete_provenance_limited", "run_id": run_id,
            "adoption": "reviewed_publication_lag", "requested_end": confirm_requested_end.isoformat(),
            "accepted_observed_end": accepted_observed_end.isoformat(), "frozen_plan": plan,
            "landing_captures": captures, "coverage": coverage, "revision_report": revisions,
            "pre_dataset": stopped["pre_dataset"], "candidate_dataset": candidate_manifest,
            "as_of_observations": as_of_observations})
        adopted = dict(stopped)
        adopted.update({"version": 3, "status": "CANDIDATE_REVIEW_REQUIRED", "error_type": None,
            "adoption": "reviewed_publication_lag", "requested_end": confirm_requested_end.isoformat(),
            "accepted_observed_end": accepted_observed_end.isoformat(), "landing_captures": captures,
            "coverage": coverage, "revision_report": revisions, "candidate_dataset": candidate_manifest,
            "as_of_observations": as_of_observations,
            "candidate_root": candidate_root.relative_to(project_root).as_posix(),
            "pre_operational_state": _file_fingerprint(state),
            "candidate_operational_state": state_path.relative_to(project_root).as_posix(),
            "candidate_operational_state_fingerprint": _file_fingerprint(state_path),
            "pre_spread": _files_manifest(project_root / "data/derived" / US_TREASURY_SPREAD_DAILY.name),
            "pre_spread_state": _file_fingerprint(project_root / "data/state" / f"{US_TREASURY_SPREAD_DAILY.name}.json"),
            "candidate_spread": spread_validation, "candidate_spread_manifest": _files_manifest(spread_root),
            "candidate_spread_state": spread_state.relative_to(project_root).as_posix(),
            "candidate_spread_state_fingerprint": _file_fingerprint(spread_state)})
        adopted["approval_digest"] = _approval_digest(adopted)
        _atomic_json(checkpoint_path, adopted)
    return adopted


def adopt_stopped_fred_fx(
    project_root: Path, checkpoint_path: Path, *, accepted_observed_end: date,
    confirm_requested_end: date,
) -> dict[str, object]:
    """Offline adoption of one already captured stopped FX run; zero HTTP."""
    project_root = project_root.resolve()
    checkpoint_path = _assert_plain_path(project_root, checkpoint_path)
    stopped = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    run_id = stopped.get("run_id")
    items = ("DEXKOUS", "DEXJPUS")
    if (stopped.get("phase") != "fred_fx" or stopped.get("status") != "STOPPED"
            or stopped.get("http_calls") != 2 or stopped.get("http_statuses") != [200, 200]
            or stopped.get("retry_count") != 0):
        raise RefreshError("stopped run is not an exact healthy two-call FX capture")
    expected_checkpoint = project_root / "data/state/global_current_refresh" / str(run_id) / "checkpoint.json"
    if checkpoint_path != expected_checkpoint.absolute():
        raise RefreshError("stopped checkpoint topology differs")
    plan = stopped.get("frozen_plan")
    if (not isinstance(plan, list) or [entry.get("item") for entry in plan] != list(items)
            or {entry.get("end") for entry in plan} != {confirm_requested_end.isoformat()}):
        raise RefreshError("requested-end confirmation or frozen FX plan differs")
    landing = _assert_plain_path(project_root, project_root / "data/landing/global_current_refresh" / run_id)
    captures = _verify_captures(landing, "fred_fx", plan)
    call_by_item = {capture["item"]: landing / capture["path"] for capture in captures}
    frames = {item: _parse_retained_fred_capture(call_by_item[item], item) for item in items}
    as_of_observations = _fred_as_of_observations(frames, captures, landing)
    endpoints = {item: frame.date.max() for item, frame in frames.items()}
    if set(endpoints.values()) != {accepted_observed_end.isoformat()}:
        raise RefreshError("FRED FX series endpoints are unequal or differ from reviewed observed end")
    plan_by_item = {entry["item"]: entry for entry in plan}
    for item in items:
        frame, entry = frames[item], plan_by_item[item]
        if frame.date.min() < entry["start"] or frame.date.max() > entry["end"]:
            raise RefreshError("retained FRED FX response lies outside frozen requested window")
    production = project_root / "data/normalized" / FRED_USD_FX_DAILY.name
    production_state = project_root / "data/state" / f"{FRED_USD_FX_DAILY.name}.json"
    existing = read_dataset(production, FRED_USD_FX_DAILY, validate_fred)
    if _files_manifest(production) != stopped["pre_dataset"]:
        raise RefreshError("FX production changed since stopped capture")
    incoming = frames[items[0]].merge(frames[items[1]], on="date", how="outer", validate="one_to_one")
    incoming = incoming.sort_values("date", kind="stable").reset_index(drop=True)
    validate_fred(incoming)
    revisions = {item: _series_revision(
        existing, frames[item], item=item, phase="fred_fx",
        planned_start=plan_by_item[item]["start"], planned_end=accepted_observed_end.isoformat(),
    ) for item in items}
    if any(report["source_omitted_existing_dates"] or report["finite_to_null_cells"] for report in revisions.values()):
        raise RefreshError("retained FRED FX response omits or nulls retained observations")
    candidate = _merge(existing, incoming, ["date"])
    validate_fred(candidate)
    candidate_parent = project_root / "data/staging/global_current_refresh" / run_id
    candidate_root = candidate_parent / FRED_USD_FX_DAILY.name
    if candidate_parent.exists():
        raise RefreshError("FX adoption candidate path already exists")
    with _lock(project_root, run_id):
        write_dataset_atomic(candidate, candidate_root, FRED_USD_FX_DAILY, validate_fred)
        candidate_manifest = _files_manifest(candidate_root)
        coverage = {item: {"planned_start": plan_by_item[item]["start"],
                           "requested_end": confirm_requested_end.isoformat(),
                           "accepted_observed_end": accepted_observed_end.isoformat(),
                           "observed_start": frames[item].date.min(), "observed_end": frames[item].date.max()}
                    for item in items}
        state_path = candidate_parent / f"{FRED_USD_FX_DAILY.name}.state.json"
        _atomic_json(state_path, {"dataset": FRED_USD_FX_DAILY.name,
            "status": "artifact_complete_provenance_limited", "run_id": run_id,
            "adoption": "reviewed_publication_lag", "requested_end": confirm_requested_end.isoformat(),
            "accepted_observed_end": accepted_observed_end.isoformat(), "frozen_plan": plan,
            "landing_captures": captures, "coverage": coverage, "revision_report": revisions,
            "pre_dataset": stopped["pre_dataset"], "candidate_dataset": candidate_manifest,
            "as_of_observations": as_of_observations})
        adopted = dict(stopped)
        adopted.update({"version": 3, "status": "CANDIDATE_REVIEW_REQUIRED", "error_type": None,
            "adoption": "reviewed_publication_lag", "requested_end": confirm_requested_end.isoformat(),
            "accepted_observed_end": accepted_observed_end.isoformat(), "landing_captures": captures,
            "coverage": coverage, "revision_report": revisions, "candidate_dataset": candidate_manifest,
            "as_of_observations": as_of_observations,
            "candidate_root": candidate_root.relative_to(project_root).as_posix(),
            "pre_operational_state": _file_fingerprint(production_state),
            "candidate_operational_state": state_path.relative_to(project_root).as_posix(),
            "candidate_operational_state_fingerprint": _file_fingerprint(state_path)})
        adopted["approval_digest"] = _approval_digest(adopted)
        _atomic_json(checkpoint_path, adopted)
    return adopted


def prepare_phase(
    project_root: Path, phase: str, *, end: date, start: date | None = None,
    symbols: tuple[str, ...] | None = None, session=None,
) -> dict[str, object]:
    """Make a reviewable Landing/candidate bundle; never mutate production."""
    project_root = project_root.resolve()
    _assert_plain_path(project_root.parent, project_root)
    if phase not in PHASES:
        raise RefreshError("unknown phase")
    configured_limit, contract, configured_items = PHASES[phase]
    items = _select_phase_items(phase, configured_items, symbols)
    limit = len(items) if phase in SYMBOL_PHASES else configured_limit
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    state_root = project_root / "data/state/global_current_refresh" / run_id
    landing_root = _landing_root(project_root, phase, run_id)
    candidate_root = project_root / "data/staging/global_current_refresh" / run_id / contract.name
    production_root = project_root / "data/normalized" / contract.name
    production_state = project_root / "data/state" / f"{contract.name}.json"
    _assert_plain_path(
        project_root, production_root,
        must_exist=phase not in {
            "yahoo_etf", "yahoo_equity", "yahoo_dashboard_futures",
        },
    )
    _assert_plain_path(project_root, production_state, must_exist=False)
    for prospective in (state_root, landing_root, candidate_root.parent):
        _assert_plain_path(project_root, prospective, must_exist=False)
    checkpoint_path = state_root / "checkpoint.json"
    validator = (
        validate_global_index if phase in {"yahoo", "cboe_index"} else
        validate_global_etf if phase == "yahoo_etf" else
        validate_global_equity if phase == "yahoo_equity" else
        validate_global_commodity_futures if phase == "yahoo_dashboard_futures" else
        validate_fred
    )
    existing = (read_dataset(production_root, contract, validator) if production_root.is_dir()
                else pd.DataFrame(columns=contract.column_names))
    pre = _dataset_manifest(production_root)
    fred_noop = _fred_retained_end_noop(
        phase=phase, end=end, items=items, existing=existing,
        production_state=production_state, production_manifest=pre,
        contract_name=contract.name,
    )
    if fred_noop is not None:
        return fred_noop
    if phase in SYMBOL_PHASES and not existing.empty and (
        start is not None or phase == "cboe_index"
    ):
        requested_start = start or end
        fully_retained = all(
            not (rows := existing.loc[existing["symbol"].eq(item), "date"]).empty
            and {
                value.isoformat() for value in _calendar_sessions_in_range(
                    _exchange_calendar_name(phase, item), requested_start, end,
                )
            } <= set(rows.astype(str))
            for item in items
        )
        if fully_retained:
            return {
                "version": 2,
                "phase": phase,
                "status": "NOOP_IDEMPOTENT",
                "reason": "requested symbol window is already retained",
                "requested_start": requested_start.isoformat(),
                "requested_end": end.isoformat(),
                "http_calls": 0,
                "normalized_mutation": False,
                "pre_dataset": pre,
            }
    plan = []
    for item in items:
        selected = (existing.loc[existing["symbol"].eq(item), "date"]
                    if phase in SYMBOL_PHASES
                    else existing.loc[pd.to_numeric(existing[item.lower()], errors="coerce").notna(), "date"])
        if selected.empty:
            if phase == "cboe_index":
                item_start = date(1900, 1, 1)
            elif phase not in YAHOO_PHASES or start is None:
                raise RefreshError(f"cannot derive overlap start for {item}")
            else:
                item_start = start
        else:
            item_start = (
                date(1900, 1, 1) if phase == "cboe_index"
                else start or (date.fromisoformat(str(selected.max())) - timedelta(days=10))
            )
        if item_start > end:
            raise RefreshError("explicit start is after end")
        plan.append({"item": item, "start": item_start.isoformat(), "end": end.isoformat()})
    vix_circuit = project_root / "data/state/automatic_fallback/fred_vix_daily_vixcls.json"
    max_http_calls = 3 if phase == "fred_vix" else limit
    checkpoint = {
        "version": 2, "run_id": run_id, "phase": phase, "status": "CREATED",
        "frozen_plan": plan, "max_http_calls": max_http_calls, "retry_count": 0,
        "pre_dataset": pre, "pre_operational_state": _file_fingerprint(production_state),
        "normalized_mutation": False,
    }
    if phase == "fred_vix":
        checkpoint["pre_fallback_circuit"] = _file_fingerprint(vix_circuit)
    _atomic_json(checkpoint_path, checkpoint)
    budget = BudgetSession(limit, session)
    with _lock(project_root, run_id):
        try:
            frames = []
            fallback_decision = None
            route_statuses = budget.statuses
            for item_plan in plan:
                start = date.fromisoformat(item_plan["start"])
                if phase == "yahoo":
                    frames.append(fetch_global_index(item_plan["item"], start, end, session=budget, capture_root=landing_root))
                elif phase == "cboe_index":
                    frames.append(fetch_cboe_index_history(
                        item_plan["item"], session=budget, capture_root=landing_root,
                    ))
                elif phase == "yahoo_etf":
                    frames.append(fetch_global_etf(item_plan["item"], start, end, session=budget, capture_root=landing_root))
                elif phase == "yahoo_equity":
                    frames.append(fetch_global_equity(
                        item_plan["item"], start, end,
                        session=budget, capture_root=landing_root,
                    ))
                elif phase == "yahoo_dashboard_futures":
                    frames.append(fetch_commodity_future(
                        item_plan["item"], start, end,
                        session=budget, capture_root=landing_root,
                    ))
                elif phase == "fred_vix":
                    frame, fallback_decision, route_statuses = _execute_vix_route(
                        project_root=project_root, state_root=state_root,
                        landing_root=landing_root, start=start, end=end,
                        existing=existing, session=session,
                    )
                    frames.append(frame)
                else:
                    frames.append(fetch_series(item_plan["item"], start, end=end, session=budget, capture_root=landing_root))
            route_calls = len(route_statuses) if phase == "fred_vix" else budget.calls
            expected_calls = (
                3 if fallback_decision is not None
                and fallback_decision["outcome"] == DecisionOutcome.FALLBACK_ACCEPTED.value
                else limit
            )
            if route_calls != expected_calls or route_statuses != [200] * expected_calls:
                raise RefreshError("phase call/status accounting differs")
            captures = _verify_captures(
                landing_root, phase, plan,
                vix_fallback=phase == "fred_vix" and expected_calls == 3,
            )
            frame_by_item = dict(zip(items, frames, strict=True))
            coverage = {}
            for item_plan in plan:
                item = item_plan["item"]
                frame = frame_by_item[item]
                observed_start = date.fromisoformat(str(frame.date.min()))
                observed_end = date.fromisoformat(str(frame.date.max()))
                planned_start = date.fromisoformat(item_plan["start"])
                coverage_policy = (
                    _endpoint_window_policy(phase, item)
                    if phase in YAHOO_PHASES else None
                )
                exchange_calendar = (
                    _exchange_calendar_name(phase, item)
                    if phase in YAHOO_PHASES else None
                )
                covers_window = (
                    _cboe_response_covers_endpoint_window(
                        observed_end=observed_end, planned_end=end,
                    )
                    if phase == "cboe_index" else
                    _response_covers_endpoint_window(
                        policy=coverage_policy,
                        observed_start=observed_start,
                        observed_end=observed_end,
                        planned_start=planned_start,
                        planned_end=end,
                        exchange_calendar=exchange_calendar,
                    )
                    if coverage_policy is not None
                    else observed_start >= planned_start and observed_end == end
                )
                if not covers_window:
                    raise RefreshError(f"{item} response does not cover the strict planned endpoint window")
                retained = (existing.loc[existing.symbol.eq(item), "date"]
                            if phase in SYMBOL_PHASES
                            else existing.loc[pd.to_numeric(existing[item.lower()], errors="coerce").notna(), "date"])
                retained_latest = retained.max() if not retained.empty else None
                if retained_latest is not None and observed_start > date.fromisoformat(str(retained_latest)):
                    raise RefreshError(f"{item} response does not overlap retained coverage")
                coverage_entry = {
                    "planned_start": item_plan["start"], "planned_end": item_plan["end"],
                    "observed_start": observed_start.isoformat(), "observed_end": observed_end.isoformat(),
                }
                if coverage_policy is not None:
                    coverage_entry.update({
                        "coverage_first": observed_start.isoformat(),
                        "coverage_last": observed_end.isoformat(),
                        "coverage_policy": coverage_policy.value,
                        "exchange_calendar": exchange_calendar,
                    })
                elif phase == "cboe_index":
                    coverage_entry.update({
                        "coverage_first": observed_start.isoformat(),
                        "coverage_last": observed_end.isoformat(),
                        "coverage_policy": "cboe_full_history_last_row",
                    })
                coverage[item] = coverage_entry
            if phase in SYMBOL_PHASES:
                incoming = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
                validator = (
                    validate_global_index if phase in {"yahoo", "cboe_index"} else
                    validate_global_etf if phase == "yahoo_etf" else
                    validate_global_equity if phase == "yahoo_equity" else
                    validate_global_commodity_futures
                )
                validator(incoming)
                expected_symbols = (
                    set(items)
                )
                strict_symbols = {
                    item for item in items
                    if _endpoint_window_policy(phase, item) is EndpointWindowPolicy.STRICT_EXCHANGE
                } if phase in YAHOO_PHASES else set()
                strict_endpoints = incoming.loc[incoming.symbol.isin(strict_symbols)].groupby("symbol")["date"].max()
                expected_strict_endpoints = {
                    item: _latest_calendar_session(
                        _exchange_calendar_name(phase, item), end,
                    ).isoformat()
                    for item in strict_symbols
                }
                if (set(incoming.symbol) != expected_symbols
                        or set(strict_endpoints.index) != strict_symbols
                        or any(
                            strict_endpoints[item] != expected
                            for item, expected in expected_strict_endpoints.items()
                        )):
                    raise RefreshError("registered provider did not reach the accepted completed-session window")
                if phase == "yahoo_dashboard_futures":
                    endpoint = incoming.loc[incoming["date"].eq(end.isoformat())]
                    if (
                        set(endpoint["symbol"]) != expected_symbols
                        or not endpoint["ohlc_status"].eq("VALID").all()
                        or pd.to_numeric(endpoint["close"], errors="coerce").isna().any()
                    ):
                        raise RefreshError("Yahoo futures endpoint is not a completed valid daily bar")
                keys = ["date", "symbol"]
            else:
                incoming = frames[0]
                for frame in frames[1:]:
                    incoming = incoming.merge(frame, on="date", how="outer", validate="one_to_one")
                incoming = incoming.sort_values("date", kind="stable").reset_index(drop=True)
                if pd.to_datetime(incoming.date).max().date() > end:
                    raise RefreshError("FRED response exceeded explicit end")
                validate_fred(incoming)
                old_latest = _finite_latest(existing, tuple(item.lower() for item in items))
                new_latest = _finite_latest(incoming, tuple(item.lower() for item in items))
                if any(new_latest[name] < old_latest[name] for name in old_latest):
                    raise RefreshError("FRED finite coverage regressed")
                keys = ["date"]
                validator = validate_fred
            plan_by_item = {entry["item"]: entry for entry in plan}
            revision = {
                item: _series_revision(
                    existing, frame_by_item[item], item=item, phase=phase,
                    planned_start=plan_by_item[item]["start"], planned_end=plan_by_item[item]["end"],
                ) for item in items
            }
            if any(report["source_omitted_existing_dates"] or report["finite_to_null_cells"] for report in revision.values()):
                raise RefreshError("source omitted retained dates or changed finite values to null")
            candidate = _merge(existing, incoming, keys) if not existing.empty else incoming.copy()
            validator(candidate)
            write_dataset_atomic(candidate, candidate_root, contract, validator)
            if _dataset_manifest(production_root) != pre:
                raise RefreshError("production root changed during capture preparation")
            extra = {}
            if phase.startswith("fred_"):
                extra["as_of_observations"] = _fred_as_of_observations(
                    frame_by_item, captures, landing_root,
                )
            if phase == "fred_yields":
                spread_root = candidate_root.parent / US_TREASURY_SPREAD_DAILY.name
                spread_state = candidate_root.parent / f"{US_TREASURY_SPREAD_DAILY.name}.state.json"
                spread_validation = _build_spread_candidate(candidate, spread_root)
                _atomic_json(spread_state, {
                    "dataset": US_TREASURY_SPREAD_DAILY.name,
                    "status": "artifact_complete_provenance_limited",
                    "source_dataset": contract.name,
                    "source_manifest": _files_manifest(candidate_root),
                    "output_manifest": _files_manifest(spread_root),
                    "validation": spread_validation,
                    "run_id": run_id,
                })
                extra = {**extra,
                    "pre_spread": _files_manifest(project_root / "data/derived" / US_TREASURY_SPREAD_DAILY.name),
                    "pre_spread_state": _file_fingerprint(project_root / "data/state" / f"{US_TREASURY_SPREAD_DAILY.name}.json"),
                    "candidate_spread": spread_validation,
                    "candidate_spread_manifest": _files_manifest(spread_root),
                    "candidate_spread_state": spread_state.relative_to(project_root).as_posix(),
                    "candidate_spread_state_fingerprint": _file_fingerprint(spread_state),
                }
            if (
                phase in {"yahoo", "cboe_index"}
                and set(VIX_TERM_INDEX_SYMBOLS) <= set(candidate["symbol"].astype(str))
                and any(item in VIX_TERM_INDEX_SYMBOLS for item in items)
                and (project_root / "data/normalized" / FRED_VIX_DAILY.name).is_dir()
            ):
                term_root = candidate_root.parent / US_VIX_TERM_STRUCTURE_DAILY.name
                term_state = candidate_root.parent / f"{US_VIX_TERM_STRUCTURE_DAILY.name}.state.json"
                term_validation = _build_vix_term_structure_candidate(
                    project_root, candidate, term_root,
                )
                _atomic_json(term_state, {
                    "dataset": US_VIX_TERM_STRUCTURE_DAILY.name,
                    "status": "artifact_complete_provenance_limited",
                    "source_datasets": [
                        FRED_VIX_DAILY.name, GLOBAL_INDEX_PRICE_DAILY.name,
                    ],
                    "fred_vix_source_manifest": _files_manifest(
                        project_root / "data/normalized" / FRED_VIX_DAILY.name
                    ),
                    "global_index_source_manifest": _files_manifest(candidate_root),
                    "output_manifest": _files_manifest(term_root),
                    "validation": term_validation,
                    "run_id": run_id,
                })
                extra = {**extra,
                    "pre_vix_term_structure": _dataset_manifest(
                        project_root / "data/derived" / US_VIX_TERM_STRUCTURE_DAILY.name
                    ),
                    "pre_vix_term_structure_state": _file_fingerprint(
                        project_root / "data/state" / f"{US_VIX_TERM_STRUCTURE_DAILY.name}.json"
                    ),
                    "candidate_vix_term_structure": term_validation,
                    "candidate_vix_term_structure_manifest": _files_manifest(term_root),
                    "candidate_vix_term_structure_state": term_state.relative_to(project_root).as_posix(),
                    "candidate_vix_term_structure_state_fingerprint": _file_fingerprint(term_state),
                }
            candidate_manifest = _files_manifest(candidate_root)
            operational_state = candidate_root.parent / f"{contract.name}.state.json"
            state_payload = {
                "dataset": contract.name, "status": "artifact_complete_provenance_limited", "run_id": run_id,
                "phase": phase, "frozen_plan": plan, "landing_captures": captures,
                "coverage": coverage, "revision_report": revision,
                "pre_dataset": pre, "candidate_dataset": candidate_manifest,
                **({"as_of_observations": extra["as_of_observations"]}
                   if "as_of_observations" in extra else {}),
                **({"fallback_decision": fallback_decision}
                   if fallback_decision is not None else {}),
            }
            _atomic_json(operational_state, state_payload)
            checkpoint.update({
                "status": "CANDIDATE_REVIEW_REQUIRED", "http_calls": route_calls,
                "http_statuses": route_statuses, "landing_captures": captures,
                "coverage": coverage, "revision_report": revision, "candidate_dataset": candidate_manifest,
                "candidate_root": candidate_root.relative_to(project_root).as_posix(),
                "candidate_operational_state": operational_state.relative_to(project_root).as_posix(),
                "candidate_operational_state_fingerprint": _file_fingerprint(operational_state), **extra,
            })
            if fallback_decision is not None:
                candidate_circuit = state_root / "fred_vix_fallback_selection/circuit.json"
                checkpoint.update({
                    "fallback_decision": fallback_decision,
                    "candidate_fallback_circuit": candidate_circuit.relative_to(project_root).as_posix(),
                    "candidate_fallback_circuit_fingerprint": _file_fingerprint(candidate_circuit),
                })
            checkpoint["approval_digest"] = _approval_digest(checkpoint)
            _atomic_json(checkpoint_path, checkpoint)
            return checkpoint
        except Exception as error:
            stopped_statuses = route_statuses if phase == "fred_vix" else budget.statuses
            stopped_captures = []
            if landing_root.is_dir():
                try:
                    stopped_captures = _verify_captures(
                        landing_root, phase, plan,
                        vix_fallback=phase == "fred_vix" and len(stopped_statuses) == 3,
                    )
                except Exception:
                    stopped_captures = []
            checkpoint.update({"status": "STOPPED", "http_calls": (
                                   len(stopped_statuses) if phase == "fred_vix" else budget.calls
                               ),
                               "http_statuses": stopped_statuses, "error_type": type(error).__name__,
                               "landing_captures": stopped_captures,
                               "post_dataset": _dataset_manifest(production_root)})
            _atomic_json(checkpoint_path, checkpoint)
            try:
                setattr(error, "checkpoint", dict(checkpoint))
            except Exception:
                pass
            raise


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _artifact_fingerprint(path: Path) -> dict[str, object]:
    if path.is_dir():
        digest = hashlib.sha256()
        files = []
        for child in sorted(path.rglob("*")):
            _assert_plain_path(path, child)
            if child.is_file():
                body = child.read_bytes()
                relative = child.relative_to(path).as_posix()
                value = hashlib.sha256(body).hexdigest()
                files.append({"path": relative, "bytes": len(body), "sha256": value})
                digest.update(relative.encode() + b"\0" + value.encode() + b"\n")
        return {"kind": "directory", "value": {"files": files, "manifest_sha256": digest.hexdigest()}}
    return {"kind": "file", "value": _file_fingerprint(path)}


def _copy_artifact(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _recover_transaction(
    journal_path: Path, *, committed: bool,
    allowed_pairs: list[tuple[Path, Path]], project_root: Path,
) -> None:
    if not journal_path.is_file():
        return
    _assert_plain_path(project_root, journal_path)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    entries = journal.get("replacements", [])
    observed_pairs = [(Path(entry.get("source", "")), Path(entry.get("target", ""))) for entry in entries]
    if observed_pairs != allowed_pairs:
        raise RefreshError("transaction journal ordered source/target identity differs")
    token = journal_path.parent.name
    for number, entry in enumerate(entries):
        target = _assert_plain_path(project_root, Path(entry["target"]), must_exist=False)
        source = _assert_plain_path(project_root, Path(entry["source"]), must_exist=False)
        stage = _assert_plain_path(project_root, Path(entry["stage"]), must_exist=False)
        backup = _assert_plain_path(project_root, Path(entry["backup"]), must_exist=False)
        if (Path(entry["stage"]) != target.parent / f".{target.name}.refresh-{token}-{number}.stage"
                or Path(entry["backup"]) != target.parent / f".{target.name}.refresh-{token}-{number}.backup"):
            raise RefreshError("transaction journal scratch topology differs")
        if committed:
            available = next((path for path in (target, stage, source) if path.exists() and _artifact_fingerprint(path) == entry["source_fingerprint"]), None)
            if available is None:
                raise RefreshError("committed transaction has no verified canonical source copy")
        elif entry["original_exists"]:
            original_available = (
                backup.exists() and _artifact_fingerprint(backup) == entry["pre_target_fingerprint"]
            ) or (
                target.exists() and _artifact_fingerprint(target) == entry["pre_target_fingerprint"]
            )
            if not original_available:
                raise RefreshError("uncommitted transaction has no verified original copy")
    if committed:
        for entry in entries:
            target, source, stage = Path(entry["target"]), Path(entry["source"]), Path(entry["stage"])
            if not target.exists() or _artifact_fingerprint(target) != entry["source_fingerprint"]:
                verified = next(path for path in (stage, source) if path.exists() and _artifact_fingerprint(path) == entry["source_fingerprint"])
                if target.exists():
                    _remove_path(target)
                _copy_artifact(verified, target)
            if _artifact_fingerprint(target) != entry["source_fingerprint"]:
                raise RefreshError("committed recovery canonical verification failed")
    else:
        for entry in reversed(entries):
            target, backup = Path(entry["target"]), Path(entry["backup"])
            if backup.exists() and _artifact_fingerprint(backup) == entry["pre_target_fingerprint"]:
                if target.exists():
                    _remove_path(target)
                backup.replace(target)
            elif entry["original_exists"] and target.exists() and _artifact_fingerprint(target) == entry["pre_target_fingerprint"]:
                pass
            elif not entry["original_exists"] and target.exists():
                _remove_path(target)
    for entry in entries:
        for name in ("stage", "backup"):
            path = Path(entry[name])
            if path.exists():
                _remove_path(path)
    journal["status"] = "COMMITTED_RECOVERED" if committed else "ROLLED_BACK_RECOVERED"
    _atomic_json(journal_path, journal)


def _replace_roots_atomically(
    replacements: list[tuple[Path, Path]], finalize=None, *, journal_path: Path | None = None,
) -> None:
    """Install whole-root copies with rollback and optional crash journal."""
    stages, backups, installed = [], [], []
    cleanup_backups = False
    try:
        journal_entries = []
        token = journal_path.parent.name if journal_path is not None else uuid4().hex
        for number, (source, target) in enumerate(replacements):
            stage = target.parent / f".{target.name}.refresh-{token}-{number}.stage"
            backup = target.parent / f".{target.name}.refresh-{token}-{number}.backup"
            if stage.exists() or backup.exists():
                raise RefreshError("transaction scratch path already exists; recover first")
            target.parent.mkdir(parents=True, exist_ok=True)
            journal_entries.append({"source": str(source), "target": str(target), "stage": str(stage),
                                    "backup": str(backup), "original_exists": target.exists(),
                                    "source_fingerprint": _artifact_fingerprint(source),
                                    "pre_target_fingerprint": _artifact_fingerprint(target) if target.exists() else {"exists": False}})
            stages.append((stage, target))
        if journal_path is not None:
            _atomic_json(journal_path, {"version": 1, "status": "PREPARING", "replacements": journal_entries})
        for number, (source, target) in enumerate(replacements):
            stage = stages[number][0]
            if source.is_dir():
                shutil.copytree(source, stage)
            else:
                shutil.copy2(source, stage)
        if journal_path is not None:
            _atomic_json(journal_path, {"version": 1, "status": "PREPARED", "replacements": journal_entries})
        for number, (stage, target) in enumerate(stages):
            backup = Path(journal_entries[number]["backup"])
            if target.exists():
                target.replace(backup)
            else:
                backup = None
            backups.append((backup, target))
            stage.replace(target)
            installed.append(target)
        if finalize is not None:
            finalize()
        if journal_path is not None:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            journal["status"] = "COMMITTED"
            _atomic_json(journal_path, journal)
        cleanup_backups = True
    except BaseException:
        for target in reversed(installed):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for backup, target in reversed(backups):
            if backup is not None and backup.exists():
                backup.replace(target)
        cleanup_backups = True
        raise
    finally:
        for stage, _ in stages:
            if stage.is_dir():
                shutil.rmtree(stage, ignore_errors=True)
            else:
                stage.unlink(missing_ok=True)
        for backup, _ in backups:
            if cleanup_backups and backup is not None:
                if backup.is_dir():
                    shutil.rmtree(backup, ignore_errors=True)
                else:
                    backup.unlink(missing_ok=True)


def _approval_digest(checkpoint: dict[str, object]) -> str:
    keys = [
        "run_id", "phase", "frozen_plan", "max_http_calls", "retry_count",
        "http_calls", "http_statuses", "landing_captures", "coverage",
        "revision_report", "pre_dataset", "candidate_dataset",
        "pre_operational_state", "candidate_root", "candidate_operational_state",
        "candidate_operational_state_fingerprint",
    ]
    if "as_of_observations" in checkpoint:
        keys.append("as_of_observations")
    keys.extend(key for key in (
        "pre_spread", "pre_spread_state", "candidate_spread",
        "candidate_spread_manifest", "candidate_spread_state",
        "candidate_spread_state_fingerprint",
        "pre_vix_term_structure", "pre_vix_term_structure_state",
        "candidate_vix_term_structure", "candidate_vix_term_structure_manifest",
        "candidate_vix_term_structure_state",
        "candidate_vix_term_structure_state_fingerprint",
        "pre_fallback_circuit", "fallback_decision",
        "candidate_fallback_circuit",
        "candidate_fallback_circuit_fingerprint",
    ) if key in checkpoint)
    payload = {key: checkpoint[key] for key in keys}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def promote_phase(project_root: Path, checkpoint_path: Path, *, approval_digest: str) -> dict[str, object]:
    """Zero-network CAS promotion; the global lock covers recovery and preflight."""
    project_root = project_root.resolve()
    _assert_plain_path(project_root.parent, project_root)
    checkpoint_path = _assert_plain_path(project_root, checkpoint_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    run_id = checkpoint.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"\d{8}T\d{6}Z_[0-9a-f]{32}", run_id):
        raise RefreshError("invalid run identity")
    expected = project_root / "data/state/global_current_refresh" / run_id / "checkpoint.json"
    if checkpoint_path != expected.absolute():
        raise RefreshError("checkpoint path does not match its run identity")
    with _lock(project_root, run_id):
        return _promote_locked(project_root, checkpoint_path, approval_digest)


def _promote_locked(project_root: Path, checkpoint_path: Path, approval_digest: str) -> dict[str, object]:
    checkpoint_path = _assert_plain_path(project_root, checkpoint_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    run_id, phase = checkpoint.get("run_id"), checkpoint.get("phase")
    if phase not in PHASES:
        raise RefreshError("unknown checkpoint phase")
    configured_limit, contract, configured_items = PHASES[phase]
    plan = checkpoint.get("frozen_plan", [])
    if not isinstance(plan, list) or not plan:
        raise RefreshError("checkpoint frozen plan is absent")
    items = tuple(entry.get("item") for entry in plan if isinstance(entry, dict))
    if len(items) != len(plan):
        raise RefreshError("checkpoint frozen plan item schema differs")
    _select_phase_items(phase, configured_items, items if phase in SYMBOL_PHASES else None)
    if phase not in SYMBOL_PHASES and items != configured_items:
        raise RefreshError("checkpoint frozen plan differs from the registered phase")
    limit = len(items) if phase in SYMBOL_PHASES else configured_limit
    fallback_decision = checkpoint.get("fallback_decision")
    expected_calls = (
        3 if phase == "fred_vix" and isinstance(fallback_decision, dict)
        and fallback_decision.get("outcome") == DecisionOutcome.FALLBACK_ACCEPTED.value
        else limit
    )
    expected_cap = 3 if phase == "fred_vix" else limit
    if (checkpoint.get("max_http_calls") != expected_cap
            or checkpoint.get("http_calls") != expected_calls
            or checkpoint.get("retry_count") != 0
            or checkpoint.get("http_statuses") != [200] * expected_calls
            or len(checkpoint.get("landing_captures", [])) != expected_calls
            or [entry.get("item") for entry in plan] != list(items)
            or checkpoint.get("approval_digest") != approval_digest
            or _approval_digest(checkpoint) != approval_digest):
        raise RefreshError("checkpoint approval/call/plan accounting differs")
    production = project_root / "data/normalized" / contract.name
    state = project_root / "data/state" / f"{contract.name}.json"
    candidate_parent = project_root / "data/staging/global_current_refresh" / run_id
    candidate = candidate_parent / contract.name
    candidate_state = candidate_parent / f"{contract.name}.state.json"
    if (checkpoint.get("candidate_root") != candidate.relative_to(project_root).as_posix()
            or checkpoint.get("candidate_operational_state") != candidate_state.relative_to(project_root).as_posix()):
        raise RefreshError("checkpoint candidate path topology differs")
    replacements = [(candidate, production), (candidate_state, state)]
    fallback_circuit = candidate_fallback_circuit = None
    if phase == "fred_vix":
        fallback_circuit = project_root / "data/state/automatic_fallback/fred_vix_daily_vixcls.json"
        candidate_fallback_circuit = project_root / str(checkpoint.get("candidate_fallback_circuit", ""))
        if checkpoint.get("candidate_fallback_circuit") != candidate_fallback_circuit.relative_to(project_root).as_posix():
            raise RefreshError("VIX fallback circuit topology differs")
        replacements.append((candidate_fallback_circuit, fallback_circuit))
    spread = spread_state = candidate_spread = candidate_spread_state = None
    if phase == "fred_yields":
        spread = project_root / "data/derived" / US_TREASURY_SPREAD_DAILY.name
        spread_state = project_root / "data/state" / f"{US_TREASURY_SPREAD_DAILY.name}.json"
        candidate_spread = candidate_parent / US_TREASURY_SPREAD_DAILY.name
        candidate_spread_state = candidate_parent / f"{US_TREASURY_SPREAD_DAILY.name}.state.json"
        if checkpoint.get("candidate_spread_state") != candidate_spread_state.relative_to(project_root).as_posix():
            raise RefreshError("checkpoint spread-state topology differs")
        replacements += [(candidate_spread, spread), (candidate_spread_state, spread_state)]
    term = term_state = candidate_term = candidate_term_state = None
    if "candidate_vix_term_structure_state" in checkpoint:
        term = project_root / "data/derived" / US_VIX_TERM_STRUCTURE_DAILY.name
        term_state = project_root / "data/state" / f"{US_VIX_TERM_STRUCTURE_DAILY.name}.json"
        candidate_term = candidate_parent / US_VIX_TERM_STRUCTURE_DAILY.name
        candidate_term_state = candidate_parent / f"{US_VIX_TERM_STRUCTURE_DAILY.name}.state.json"
        if (
            checkpoint.get("candidate_vix_term_structure_state")
            != candidate_term_state.relative_to(project_root).as_posix()
        ):
            raise RefreshError("checkpoint VIX term-structure state topology differs")
        replacements += [(candidate_term, term), (candidate_term_state, term_state)]
    for source, target in replacements:
        _assert_plain_path(project_root, source, must_exist=False)
        _assert_plain_path(project_root, target, must_exist=False)
    journal_path = checkpoint_path.with_name("promotion_transaction.json")
    _assert_plain_path(project_root, journal_path, must_exist=False)
    _recover_transaction(
        journal_path, committed=checkpoint.get("status") == "PROMOTED",
        allowed_pairs=replacements, project_root=project_root,
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("status") == "PROMOTED":
        return checkpoint
    if checkpoint.get("status") != "CANDIDATE_REVIEW_REQUIRED":
        raise RefreshError("checkpoint is not review-ready")
    landing = _assert_plain_path(project_root, _landing_root(project_root, phase, run_id))
    for source, target in replacements:
        _assert_plain_path(project_root, source)
        _assert_plain_path(project_root, target, must_exist=False)
    if (_verify_captures(
            landing, phase, checkpoint["frozen_plan"],
            vix_fallback=phase == "fred_vix" and expected_calls == 3,
        ) != checkpoint["landing_captures"]
            or _dataset_manifest(production) != checkpoint["pre_dataset"]
            or _files_manifest(candidate) != checkpoint["candidate_dataset"]
            or _file_fingerprint(state) != checkpoint["pre_operational_state"]
            or _file_fingerprint(candidate_state) != checkpoint["candidate_operational_state_fingerprint"]):
        raise RefreshError("locked CAS/input validation differs")
    if phase == "fred_vix" and (
            _file_fingerprint(fallback_circuit) != checkpoint["pre_fallback_circuit"]
            or _file_fingerprint(candidate_fallback_circuit)
            != checkpoint["candidate_fallback_circuit_fingerprint"]):
        raise RefreshError("VIX fallback circuit CAS/input validation differs")
    if phase == "fred_yields" and (
            _files_manifest(spread) != checkpoint["pre_spread"]
            or _files_manifest(candidate_spread) != checkpoint["candidate_spread_manifest"]
            or _file_fingerprint(spread_state) != checkpoint["pre_spread_state"]
            or _file_fingerprint(candidate_spread_state) != checkpoint["candidate_spread_state_fingerprint"]):
        raise RefreshError("locked Treasury spread CAS/input differs")
    if candidate_term is not None and (
            _dataset_manifest(term) != checkpoint["pre_vix_term_structure"]
            or _files_manifest(candidate_term)
            != checkpoint["candidate_vix_term_structure_manifest"]
            or _file_fingerprint(term_state)
            != checkpoint["pre_vix_term_structure_state"]
            or _file_fingerprint(candidate_term_state)
            != checkpoint["candidate_vix_term_structure_state_fingerprint"]):
        raise RefreshError("locked VIX term-structure CAS/input differs")
    promoted = dict(checkpoint)
    promoted.update({"status": "PROMOTED", "normalized_mutation": True,
                     "post_dataset": checkpoint["candidate_dataset"],
                     "promoted_at_utc": datetime.now(timezone.utc).isoformat()})
    _replace_roots_atomically(
        replacements, finalize=lambda: _atomic_json(checkpoint_path, promoted),
        journal_path=journal_path,
    )
    return promoted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--phase", choices=tuple(PHASES))
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument(
        "--symbols", nargs="+",
        help="Exact registered canonical symbol ids for Yahoo/Cboe symbol phases; omitted means the full phase registry.",
    )
    parser.add_argument("--confirm-live-landing-only", action="store_true")
    parser.add_argument("--promote-checkpoint", type=Path)
    parser.add_argument("--confirm-offline-promotion", action="store_true")
    parser.add_argument("--approval-digest")
    parser.add_argument("--adopt-stopped-fred-yields", type=Path)
    parser.add_argument("--adopt-stopped-fred-fx", type=Path)
    parser.add_argument("--accepted-observed-end", type=date.fromisoformat)
    parser.add_argument("--confirm-requested-end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if args.adopt_stopped_fred_fx:
        if not args.accepted_observed_end or not args.confirm_requested_end:
            raise SystemExit("adoption requires accepted observed end and requested-end confirmation")
        result = adopt_stopped_fred_fx(
            root, args.adopt_stopped_fred_fx.resolve(),
            accepted_observed_end=args.accepted_observed_end,
            confirm_requested_end=args.confirm_requested_end,
        )
    elif args.adopt_stopped_fred_yields:
        if not args.accepted_observed_end or not args.confirm_requested_end:
            raise SystemExit("adoption requires accepted observed end and requested-end confirmation")
        result = adopt_stopped_fred_yields(
            root, args.adopt_stopped_fred_yields.resolve(),
            accepted_observed_end=args.accepted_observed_end,
            confirm_requested_end=args.confirm_requested_end,
        )
    elif args.promote_checkpoint:
        if not args.confirm_offline_promotion:
            raise SystemExit("explicit offline-promotion confirmation is required")
        if not args.approval_digest:
            raise SystemExit("exact approval digest is required")
        result = promote_phase(root, args.promote_checkpoint.resolve(), approval_digest=args.approval_digest)
    else:
        if not args.phase or not args.end or not args.confirm_live_landing_only:
            raise SystemExit("phase, explicit end, and Landing-only live confirmation are required")
        result = prepare_phase(
            root, args.phase, end=args.end, start=args.start,
            symbols=tuple(args.symbols) if args.symbols else None,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
