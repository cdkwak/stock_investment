from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Mapping

from stock_data.gui.health_service import DECISION_HOLD_DEPENDENCY_MAP


CONTRACT_ID = "gui-refresh-status/v1"

SURFACE_IDS = (
    "DASHBOARD_CURRENT",
    "DATA_HEALTH",
    "ACCOUNT_SNAPSHOT",
    "US_MARKET_FLOW",
)
RETRY_ACTION_IDS = frozenset({"dashboard-local-reread"})
_CURRENT_DATASET_IDS = frozenset({
    "market_price_60m_current",
    "market_price_15m_current",
    "KR_INDEX_CURRENT",
})
_RECEIPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_YAHOO_ROUTES = (
    *(('GLOBAL_30M', item) for item in (
        'USD_KRW_60M', 'UST2_FUTURES_60M', 'UST10_FUTURES_60M',
        'UST30_FUTURES_60M', 'KOSPI_CURRENT_60M', 'KOSDAQ_CURRENT_60M',
        'SP500_CURRENT_60M', 'NASDAQ_CURRENT_60M', 'NQ_FUTURES_CURRENT_60M',
        'SOXX_CURRENT_60M', 'GOLD_CURRENT_60M', 'WTI_CURRENT_60M',
        'BITCOIN_CURRENT_60M',
        'SP500_FUTURES_CURRENT_60M', 'DOW_FUTURES_CURRENT_60M',
        'SOX_CURRENT_60M', 'DOLLAR_INDEX_CURRENT_60M',
    )),
    *(('NATIVE_15M', item) for item in ('^VIX', '^FVX', '^TNX', '^TYX')),
)
_YAHOO_OUTCOMES = {
    "GLOBAL_30M": frozenset({
        "CURRENT_30M_ACCEPTED", "NO_NEW_30M_BAR_PRESERVED",
        "OLDER_30M_BAR_PRIOR_VALUE_PRESERVED",
        "REVISED_30M_BAR_PRIOR_VALUE_PRESERVED",
    }),
    "NATIVE_15M": frozenset({
        "CURRENT_15M_ACCEPTED", "NO_NEW_15M_BAR_PRESERVED",
        "OLDER_15M_BAR_PRIOR_VALUE_PRESERVED",
        "REVISED_15M_BAR_PRIOR_VALUE_PRESERVED",
    }),
}


@dataclass(frozen=True)
class ComponentStatus:
    component_id: str
    operation_state: str
    freshness_state: str
    source_as_of: str | None
    source_time_basis: str
    market_date: str | None
    last_success_at: str | None
    retained_value_state: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurfaceStatus:
    surface_id: str
    cadence_kind: str
    cadence_seconds: int | None
    observation_semantics: str
    operation_state: str
    freshness_state: str
    source_as_of: str | None
    source_time_basis: str
    market_date: str | None
    last_success_at: str | None
    last_success_receipt_id: str | None
    next_eligible_at: str | None
    next_eligible_basis: str
    retained_value_state: str
    retry_capability: str
    retry_action_id: str | None
    reason_codes: tuple[str, ...]
    component_results: tuple[ComponentStatus, ...]


@dataclass(frozen=True)
class RefreshStatusProjection:
    schema_version: int
    contract_id: str
    generated_at_utc: str
    overall_state: str
    surfaces: tuple[SurfaceStatus, ...]

    def surface(self, surface_id: str) -> SurfaceStatus:
        if surface_id not in SURFACE_IDS:
            raise ValueError("refresh surface is not allowlisted")
        return next(item for item in self.surfaces if item.surface_id == surface_id)


def _aware_utc(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _market_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _strict_json(path: Path) -> dict[str, object]:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, value in values:
            if key in output:
                raise ValueError("duplicate receipt key")
            output[key] = value
        return output

    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    if not isinstance(payload, dict):
        raise ValueError("receipt root differs")
    return payload


def _receipt(
    root: Path, relative: str, *, validator: Callable[[Mapping[str, object]], bool],
) -> tuple[str | None, str | None]:
    """Read one fixed local receipt and expose only sanitized completion metadata."""
    path = root / relative
    try:
        payload = _strict_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None, None
    if not validator(payload):
        return None, None
    finished = _aware_utc(payload.get("finished_at_utc"))
    if finished is None:
        return None, None
    receipt_id = payload.get("run_id")
    if not isinstance(receipt_id, str) or _RECEIPT_ID.fullmatch(receipt_id) is None:
        receipt_id = None
    return finished, receipt_id


def _yahoo_receipt_complete(payload: Mapping[str, object]) -> bool:
    outcomes = payload.get("series_terminal_outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != len(_YAHOO_ROUTES):
        return False
    observed: list[tuple[str, str]] = []
    preserved = 0
    for item in outcomes:
        if (
            not isinstance(item, dict)
            or set(item) != {"lane", "series_id", "outcome"}
            or not isinstance(item.get("lane"), str)
            or not isinstance(item.get("series_id"), str)
            or not isinstance(item.get("outcome"), str)
            or item["outcome"] not in _YAHOO_OUTCOMES.get(item["lane"], ())
        ):
            return False
        observed.append((item["lane"], item["series_id"]))
        preserved += int(item["outcome"].endswith("PRESERVED"))
    return (
        payload.get("status") == "PASS"
        and tuple(observed) == _YAHOO_ROUTES
        and payload.get("failed") == 0
        and payload.get("accepted") == len(_YAHOO_ROUTES)
        and payload.get("api_calls") == len(_YAHOO_ROUTES)
        and payload.get("max_api_calls") == len(_YAHOO_ROUTES)
        and payload.get("preserved") == preserved
    )


def _health_receipt_complete(payload: Mapping[str, object]) -> bool:
    return bool(
        payload.get("status") in {"SUCCESS", "PASS", "NOOP"}
        and type(payload.get("dataset_count")) is int
        and payload["dataset_count"] > 0
        and type(payload.get("runtime_coverage_validated_count")) is int
        and payload["runtime_coverage_validated_count"] > 0
        and payload.get("runtime_coverage_failure_count") == 0
        and payload.get("api_calls") == 0
    )


def _metric_field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _current_surface(
    root: Path, metrics: Mapping[str, object], *, in_progress: bool,
) -> SurfaceStatus:
    candidates = tuple(
        value for value in metrics.values()
        if _metric_field(value, "dataset_id") in _CURRENT_DATASET_IDS
    )
    display_candidates = tuple(
        value for value in candidates if bool(_metric_field(value, "displays_value"))
    )
    accepted = tuple(
        (value, parsed) for value in display_candidates
        if (parsed := _aware_utc(_metric_field(value, "source_timestamp"))) is not None
    )
    times = tuple(parsed for _value, parsed in accepted)
    source_as_of = max(times) if times else None
    last_success, receipt_id = _receipt(
        root,
        "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json",
        validator=_yahoo_receipt_complete,
    )
    if not candidates:
        operation, freshness, retained = "UNKNOWN", "UNKNOWN", "SUPPRESSED"
        reasons = ("SOURCE_METADATA_MISSING",)
    elif not accepted:
        operation, freshness, retained = "FAILED", "UNKNOWN", "SUPPRESSED"
        reasons = (
            ("SOURCE_TIMESTAMP_INVALID", "COMPONENT_FAILED")
            if display_candidates else ("COMPONENT_FAILED",)
        )
    elif len(accepted) != len(candidates):
        operation, freshness, retained = "PARTIAL_FAILURE", "UNKNOWN", "DISPLAYABLE_WITH_WARNING"
        reasons = (
            ("SOURCE_TIMESTAMP_INVALID", "PARTIAL_COMPONENTS")
            if len(accepted) != len(display_candidates)
            else ("PARTIAL_COMPONENTS",)
        )
    else:
        operation, freshness, retained = "SUCCEEDED", "CURRENT", "DISPLAYABLE"
        reasons = ()
    if in_progress and operation == "SUCCEEDED":
        operation = "IN_PROGRESS"
    component = ComponentStatus(
        "CURRENT_MARKET_PROJECTIONS", operation, freshness, source_as_of,
        "PROVIDER_TIMESTAMP" if source_as_of else "NONE", None,
        last_success, retained, reasons,
    )
    return SurfaceStatus(
        "DASHBOARD_CURRENT", "FIXED_INTERVAL", 1800, "PERIODIC_CURRENT",
        operation, freshness, source_as_of,
        "PROVIDER_TIMESTAMP" if source_as_of else "NONE", None,
        last_success, receipt_id, None, "UNKNOWN", retained,
        "LOCAL_REREAD", "dashboard-local-reread", reasons, (component,),
    )


def _health_surface(root: Path, health: Mapping[str, object]) -> SurfaceStatus:
    managed_total = health.get("managed_total")
    managed_acceptable = health.get("managed_acceptable")
    lag = health.get("managed_expected_lag")
    display_gap = health.get("display_gap", 0)
    decision_hold_causes = health.get("decision_hold_causes", ())
    last_success, receipt_id = _receipt(
        root,
        "artifacts/scheduler_logs/STOCK_DATA_DAILY_HEALTH_last.json",
        validator=_health_receipt_complete,
    )
    if type(managed_total) is not int or managed_total <= 0:
        operation, freshness, retained = "UNKNOWN", "UNKNOWN", "SUPPRESSED"
        reasons = ("SOURCE_METADATA_MISSING",)
    elif (
        type(decision_hold_causes) is not tuple
        or any(cause not in DECISION_HOLD_DEPENDENCY_MAP for cause in decision_hold_causes)
    ):
        operation, freshness, retained = "UNKNOWN", "UNKNOWN", "SUPPRESSED"
        reasons = ("SOURCE_METADATA_MISSING",)
    elif decision_hold_causes:
        operation, freshness, retained = "PARTIAL_FAILURE", "STALE", "DISPLAYABLE_WITH_WARNING"
        reasons = ("DECISION_HOLD", *decision_hold_causes, "RETAINED_VALUE_STALE")
    elif managed_acceptable != managed_total:
        operation, freshness, retained = "PARTIAL_FAILURE", "STALE", "DISPLAYABLE_WITH_WARNING"
        reasons = ("PARTIAL_COMPONENTS", "RETAINED_VALUE_STALE")
    elif type(display_gap) is not int or display_gap < 0:
        operation, freshness, retained = "UNKNOWN", "UNKNOWN", "SUPPRESSED"
        reasons = ("SOURCE_METADATA_MISSING",)
    elif display_gap:
        operation, freshness, retained = "PARTIAL_FAILURE", "STALE", "DISPLAYABLE_WITH_WARNING"
        reasons = ("VISIBLE_DATA_GAPS", "RETAINED_VALUE_STALE")
    else:
        operation = "SUCCEEDED"
        freshness = "EXPECTED_LAG" if isinstance(lag, int) and lag else "CURRENT"
        retained = "DISPLAYABLE_WITH_WARNING" if freshness == "EXPECTED_LAG" else "DISPLAYABLE"
        reasons = ("EXPECTED_LAG",) if freshness == "EXPECTED_LAG" else ()
    component = ComponentStatus(
        "MANAGED_DATASETS", operation, freshness, None, "NONE", None,
        last_success, retained, reasons,
    )
    return SurfaceStatus(
        "DATA_HEALTH", "SCHEDULED_LOCAL", None, "LAST_COMPLETED_SESSION",
        operation, freshness, None, "NONE", None, last_success, receipt_id,
        None, "UNKNOWN", retained, "LOCAL_REREAD", "dashboard-local-reread",
        reasons, (component,),
    )


def _account_surface(account: object) -> SurfaceStatus:
    available = bool(getattr(account, "available", False))
    source_as_of = _aware_utc(getattr(account, "as_of", None))
    market_date = None if source_as_of else _market_date(getattr(account, "as_of", None))
    last_success = _aware_utc(getattr(account, "last_reconciled_at", None))
    if available:
        operation, freshness, retained = "SUCCEEDED", "CURRENT", "DISPLAYABLE"
        reasons = () if source_as_of or market_date else ("SOURCE_METADATA_MISSING",)
        if reasons:
            freshness, retained = "UNKNOWN", "DISPLAYABLE_WITH_WARNING"
    else:
        operation, freshness, retained = "UNKNOWN", "UNKNOWN", "SUPPRESSED"
        reasons = ("SOURCE_METADATA_MISSING",)
    component = ComponentStatus(
        "READONLY_ACCOUNT_LOCAL", operation, freshness, source_as_of,
        "RETRIEVAL_TIMESTAMP" if source_as_of else "MARKET_DATE" if market_date else "NONE",
        market_date, last_success, retained, reasons,
    )
    return SurfaceStatus(
        "ACCOUNT_SNAPSHOT", "MANUAL", None, "RETAINED_ONLY", operation,
        freshness, source_as_of, component.source_time_basis, market_date,
        last_success, None, None, "MANUAL_ONLY", retained,
        "READONLY_REFRESH_REQUEST", None, reasons, (component,),
    )


def _unsupported_surface() -> SurfaceStatus:
    reasons = ("REFRESH_UNSUPPORTED", "RETRY_NOT_ALLOWED")
    component = ComponentStatus(
        "US_INVESTOR_CLASSIFICATION", "UNSUPPORTED", "NOT_APPLICABLE", None,
        "NONE", None, None, "NOT_APPLICABLE", reasons,
    )
    return SurfaceStatus(
        "US_MARKET_FLOW", "UNSUPPORTED", None, "UNSUPPORTED", "UNSUPPORTED",
        "NOT_APPLICABLE", None, "NONE", None, None, None, None, "UNSUPPORTED",
        "NOT_APPLICABLE", "NONE", None, reasons, (component,),
    )


def project_refresh_status(
    project_root: Path,
    *,
    health: Mapping[str, object] | None,
    metrics: Mapping[str, object] | None,
    account: object = None,
    generated_at_utc: object | None = None,
    current_in_progress: bool = False,
) -> RefreshStatusProjection:
    """Build a strict, provider-free status view from already accepted local views."""
    generated = _aware_utc(generated_at_utc)
    if generated is None:
        generated = datetime.now(timezone.utc).isoformat()
    surfaces = (
        _current_surface(Path(project_root), metrics or {}, in_progress=current_in_progress),
        _health_surface(Path(project_root), health or {}),
        _account_surface(account),
        _unsupported_surface(),
    )
    actionable = tuple(item for item in surfaces if item.operation_state != "UNSUPPORTED")
    states = {item.operation_state for item in actionable}
    if "PARTIAL_FAILURE" in states or "FAILED" in states and len(states) > 1:
        overall = "PARTIAL_FAILURE"
    elif states == {"FAILED"}:
        overall = "FAILED"
    elif "IN_PROGRESS" in states:
        overall = "IN_PROGRESS"
    elif states == {"SUCCEEDED"}:
        overall = "SUCCEEDED"
    elif "SUCCEEDED" in states:
        overall = "PARTIAL_FAILURE"
    else:
        overall = "UNKNOWN"
    projection = RefreshStatusProjection(1, CONTRACT_ID, generated, overall, surfaces)
    if tuple(item.surface_id for item in projection.surfaces) != SURFACE_IDS:
        raise ValueError("refresh surfaces differ from the closed registry")
    for item in projection.surfaces:
        if item.retry_action_id is not None and item.retry_action_id not in RETRY_ACTION_IDS:
            raise ValueError("refresh retry action is not allowlisted")
    return projection
