"""Bounded, provider-free release readiness checks for the local desktop app.

This module never calls collectors, providers, schedulers, or mutation entry
points.  It reports retained local state and verifies that the GUI keeps typed
stale/unknown values suppressed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, time as wall_time, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.gui.backtest_service import BacktestResultService
from stock_data.gui.health_service import (
    DailyHealthArtifactService,
    HealthArtifactView,
    summarize_health_artifact,
)
from stock_data.gui.services import DashboardDisplayState, DashboardService
from stock_data.orchestration.global_market_60m import CURRENT_SERIES_IDS
from stock_data.orchestration.yahoo_market_current import NATIVE_15M_SERIES


REPORT_SCHEMA_VERSION = 1
KST = ZoneInfo("Asia/Seoul")
HEALTH_CONSUMER_CONTRACT_FIELDS = (
    "display_consumer_eligibility", "display_consumer_reason",
    "research_consumer_eligibility", "research_consumer_reason",
    "predictive_consumer_eligibility", "predictive_consumer_reason",
)
SCHEDULER_TASK_HAS_NOT_RUN = 0x00041303
KR_MARKET_DAILY_LANE_CONTRACT_VERSION = 5
KR_MARKET_DAILY_LEGACY_LANE_CONTRACT_CUTOFF = datetime(
    2026, 8, 26, 20, 30, tzinfo=KST,
)
KR_MARKET_DAILY_V2_LANE_CONTRACT_CUTOFF = datetime(
    2026, 8, 26, 20, 30, tzinfo=KST,
)
KR_MARKET_DAILY_V3_LANE_CONTRACT_CUTOFF = datetime(
    2026, 8, 26, 20, 30, tzinfo=KST,
)
KR_MARKET_DAILY_V4_LANE_CONTRACT_CUTOFF = datetime(
    2026, 8, 27, 20, 30, tzinfo=KST,
)
KR_MARKET_DAILY_SLOT_TASKS = {
    "STOCK_DATA_KR_MARKET_DAILY_0910": "09:10",
    "STOCK_DATA_KR_MARKET_DAILY_1410": "14:10",
    "STOCK_DATA_KR_MARKET_DAILY_2030": "20:30",
}
KR_MARKET_DAILY_V3_SLOT_LANES = {
    "09:10": (
        "KR_INDEX_FUNDAMENTAL_DAILY", "SHORT_SELLING_DAILY",
        "LIQUIDITY_CREDIT_OBSERVATION",
    ),
    "14:10": (
        "CANONICAL_EQUITY_DAILY", "SHORT_SELLING_DAILY", "LENDING_DAILY",
    ),
    "20:30": (
        "CANONICAL_EQUITY_DAILY", "KOSPI200_BREADTH_DAILY",
        "SHORT_SELLING_DAILY", "LENDING_DAILY", "VKOSPI_DAILY",
        "KR_INDEX_DAILY", "DERIVATIVES_PRICE_DAILY",
        "MARKET_INVESTOR_DAILY",
        "LIQUIDITY_CREDIT_OBSERVATION",
    ),
}
KR_MARKET_DAILY_V4_SLOT_LANES = {
    **KR_MARKET_DAILY_V3_SLOT_LANES,
    "09:10": (
        "KR_INDEX_FUNDAMENTAL_DAILY",
        "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION",
        "SHORT_SELLING_DAILY", "LIQUIDITY_CREDIT_OBSERVATION",
    ),
}
KR_MARKET_DAILY_SLOT_LANES = {
    **KR_MARKET_DAILY_V4_SLOT_LANES,
    "09:10": (
        "KR_INDEX_FUNDAMENTAL_DAILY",
        "KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION",
        "SHORT_SELLING_DAILY", "LIQUIDITY_CREDIT_DAILY",
    ),
    "20:30": (
        "CANONICAL_EQUITY_DAILY", "KOSPI200_BREADTH_DAILY",
        "SHORT_SELLING_DAILY", "SHORT_SELLING_BALANCE_DAILY",
        "SHORT_SELLING_INVESTOR_DAILY", "LENDING_DAILY", "VKOSPI_DAILY",
        "KR_INDEX_DAILY", "DERIVATIVES_PRICE_DAILY", "MARKET_INVESTOR_DAILY",
        "LIQUIDITY_CREDIT_DAILY", "LS_T8462_DAILY", "TOSS_KR_TREASURY_DAILY",
    ),
}
KR_MARKET_DAILY_V2_SLOT_LANES = {
    **KR_MARKET_DAILY_V3_SLOT_LANES,
    "20:30": (
        "CANONICAL_EQUITY_DAILY", "SHORT_SELLING_DAILY", "LENDING_DAILY",
        "VKOSPI_DAILY", "KR_INDEX_DAILY", "DERIVATIVES_PRICE_DAILY",
        "MARKET_INVESTOR_DAILY",
        "LIQUIDITY_CREDIT_OBSERVATION",
    ),
}
KR_MARKET_DAILY_LEGACY_SLOT_LANES = {
    **KR_MARKET_DAILY_V3_SLOT_LANES,
    "20:30": (
        "CANONICAL_EQUITY_DAILY", "SHORT_SELLING_DAILY", "LENDING_DAILY",
        "VKOSPI_DAILY", "KR_INDEX_DAILY", "MARKET_INVESTOR_DAILY",
        "LIQUIDITY_CREDIT_OBSERVATION",
    ),
}
EXPECTED_SCHEDULED_TASKS = (
    "STOCK_DATA_BOK_TREASURY_DAILY",
    "STOCK_DATA_DAILY_HEALTH",
    "STOCK_DATA_FRED_DAILY",
    *KR_MARKET_DAILY_SLOT_TASKS,
    "STOCK_DATA_GLOBAL_INDEX_DAILY",
    "STOCK_DATA_GLOBAL_ETF_SOXX_DAILY",
    "STOCK_DATA_GLOBAL_FUTURES_DAILY",
    "STOCK_DATA_KBSEC_ACCOUNT_DAILY",
    "STOCK_DATA_TOSS_ACCOUNT_DAILY",
    "STOCK_DATA_TOSS_DOMESTIC_30M",
    "STOCK_DATA_YAHOO_MARKET_30M",
)
SCHEDULER_RESULT_POLICIES = {
    "STOCK_DATA_YAHOO_MARKET_30M": (
        "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json",
        timedelta(hours=2),
    ),
    "STOCK_DATA_KR_MARKET_DAILY_SLOT_BUNDLE": (
        "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json",
        timedelta(hours=36),
    ),
}
DAILY_SCHEDULER_RESULT_POLICIES = {
    "STOCK_DATA_DAILY_HEALTH": (
        "artifacts/scheduler_logs/STOCK_DATA_DAILY_HEALTH_last.json", "06:30",
    ),
    "STOCK_DATA_FRED_DAILY": (
        "artifacts/scheduler_logs/STOCK_DATA_FRED_DAILY_last.json", "06:00",
    ),
    "STOCK_DATA_GLOBAL_ETF_SOXX_DAILY": (
        "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_ETF_DAILY_last.json", "06:10",
    ),
    "STOCK_DATA_GLOBAL_INDEX_DAILY": (
        "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_INDEX_DAILY_last.json", "06:20",
    ),
    "STOCK_DATA_GLOBAL_FUTURES_DAILY": (
        "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_COMMODITY_DAILY_last.json", "22:10",
    ),
}
GOVERNING_HEALTH_RECEIPTS = (
    "artifacts/scheduler_logs/STOCK_DATA_FRED_DAILY_last.json",
    "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_ETF_DAILY_last.json",
    "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_INDEX_DAILY_last.json",
    "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_COMMODITY_DAILY_last.json",
    "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json",
    "artifacts/scheduler_logs/STOCK_DATA_CANONICAL_EQUITY_DAILY_last.json",
)
REQUIRED_DATA_ROOTS = (
    "data/landing", "data/normalized", "data/derived", "data/published", "data/state",
)
PROTECTED_USER_ROOTS = ("data", "artifacts/local_user")
EXACT_USER_DATA_PATTERNS = (
    "artifacts/local_user/**/*",
    "data/local/**/*",
    "data/normalized/toss_account_snapshot/**/*",
)
EXPECTED_GUI_PAGES = (
    "오늘",
    "시장",
    "종목",
    "관심종목",
    "계좌",
    "판단 근거",
    "데이터 상태",
    "리서치",
    "백테스트",
    "미국 ETF",
)
EXPECTED_GUI_WORKERS = (
    "account",
    "current_observation",
    "equity",
    "us_etf",
    "backtest",
    "detached",
)

EXPECTED_YAHOO_TERMINAL_ROUTES = (
    *(("GLOBAL_30M", series_id) for series_id in CURRENT_SERIES_IDS),
    *(("NATIVE_15M", series_id) for series_id in NATIVE_15M_SERIES),
)
YAHOO_TERMINAL_OUTCOMES_BY_LANE = {
    "GLOBAL_30M": frozenset({
        "CURRENT_30M_ACCEPTED",
        "NO_NEW_30M_BAR_PRESERVED",
        "OLDER_30M_BAR_PRIOR_VALUE_PRESERVED",
        "REVISED_30M_BAR_PRIOR_VALUE_PRESERVED",
    }),
    "NATIVE_15M": frozenset({
        "CURRENT_15M_ACCEPTED",
        "NO_NEW_15M_BAR_PRESERVED",
        "OLDER_15M_BAR_PRIOR_VALUE_PRESERVED",
        "REVISED_15M_BAR_PRIOR_VALUE_PRESERVED",
    }),
}
EXPECTED_TOSS_ELIGIBLE_OUTCOME_SLOTS = frozenset({
    "DOMESTIC_ROUTE_1", "DOMESTIC_ROUTE_2",
    "DOMESTIC_ROUTE_3", "DOMESTIC_ROUTE_4",
})
EXPECTED_TOSS_INELIGIBLE_OUTCOME_SLOTS = frozenset({"OPERATION"})
NATIVE_GUI_QUIESCENCE_TIMEOUT_MS = 30_000
NATIVE_GUI_QUIESCENCE_POLL_MS = 50
NATIVE_GUI_HEALTH_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class NativeGuiQuiescence:
    """Typed outcome from a bounded managed-worker event drain."""

    state: str
    polls: int
    waited_ms: int
    active_threads: int


@dataclass(frozen=True, slots=True)
class SmokeCheck:
    check_id: str
    status: str
    summary: str
    category: str = "release"


@dataclass(frozen=True, slots=True)
class TreeIdentity:
    sha256: str
    file_count: int
    total_bytes: int


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")


def _combined_file_identity(paths: Iterable[Path], root: Path) -> TreeIdentity:
    digest = sha256()
    count = 0
    total = 0
    for path in sorted({Path(item) for item in paths}):
        try:
            if path.is_symlink():
                continue
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(root.resolve()).as_posix()
            if not resolved.is_file():
                continue
            size = resolved.stat().st_size
            file_digest = sha256()
            with resolved.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    file_digest.update(block)
            digest.update(_json_bytes((relative, size, file_digest.hexdigest())))
            count += 1
            total += size
        except (FileNotFoundError, OSError, ValueError):
            continue
    return TreeIdentity(digest.hexdigest(), count, total)


def tree_metadata_identity(project_root: Path) -> TreeIdentity:
    """Bounded no-payload identity used before and after the read-only smoke."""

    project_root = Path(project_root).resolve()
    digest = sha256()
    count = 0
    total = 0
    for relative_root in PROTECTED_USER_ROOTS:
        root = project_root / relative_root
        if not root.exists():
            digest.update(_json_bytes((relative_root, "MISSING")))
            continue
        for path in sorted(root.rglob("*")):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                stat = path.stat()
                relative = path.relative_to(project_root).as_posix()
                digest.update(_json_bytes((relative, stat.st_size, stat.st_mtime_ns)))
                count += 1
                total += stat.st_size
            except (FileNotFoundError, OSError, ValueError):
                digest.update(_json_bytes((str(path), "UNREADABLE")))
    return TreeIdentity(digest.hexdigest(), count, total)


def code_identity(project_root: Path) -> TreeIdentity:
    project_root = Path(project_root).resolve()
    paths = [project_root / "app.py", project_root / "pyproject.toml"]
    paths.extend((project_root / "src").rglob("*.py"))
    paths.append(project_root / "scripts/maintenance/run_release_readiness_smoke.py")
    return _combined_file_identity(paths, project_root)


def user_data_content_identity(project_root: Path) -> TreeIdentity:
    root = Path(project_root).resolve()
    paths: set[Path] = set()
    for pattern in EXACT_USER_DATA_PATTERNS:
        paths.update(path for path in root.glob(pattern) if path.is_file())
    return _combined_file_identity(paths, root)


def _exact_file_identity(path: Path, root: Path) -> TreeIdentity:
    """Identify one canonical in-root file without following symlinks."""

    empty = _combined_file_identity((), root)
    try:
        path = Path(path)
        absolute = Path(os.path.abspath(path))
        if path.is_symlink():
            return empty
        resolved = path.resolve(strict=True)
        resolved.relative_to(Path(root).resolve(strict=True))
        if resolved != absolute or not resolved.is_file():
            return empty
    except (FileNotFoundError, OSError, ValueError):
        return empty
    return _combined_file_identity((path,), root)


def check_backtest_gui_bundle(
    project_root: Path,
) -> tuple[SmokeCheck, Path, TreeIdentity]:
    """Validate and identify the exact result artifact consumed by the GUI."""

    root = Path(project_root).resolve()
    service = BacktestResultService(root)
    path = service.result_path
    before = _exact_file_identity(path, root)
    try:
        view = service.load()
    except Exception as error:
        return (
            SmokeCheck(
                "BACKTEST_GUI_BUNDLE",
                "FAIL",
                f"canonical GUI result validation failed: {type(error).__name__}",
                "backtest",
            ),
            path,
            before,
        )
    after = _exact_file_identity(path, root)
    stable = before == after
    ready = (
        view.artifact_state == "READY"
        and view.warning is None
        and view.input_coverage is not None
    )
    accepted = ready and after.file_count == 1 and stable
    return (
        SmokeCheck(
            "BACKTEST_GUI_BUNDLE",
            "PASS" if accepted else "FAIL",
            f"service_ready={ready} exact_files={after.file_count} "
            f"exact_bytes={after.total_bytes} stable={stable}",
            "backtest",
        ),
        path,
        after,
    )


def check_required_roots(project_root: Path) -> SmokeCheck:
    missing = []
    unreadable = []
    for relative in REQUIRED_DATA_ROOTS:
        path = Path(project_root) / relative
        if not path.is_dir():
            missing.append(relative)
        elif not os.access(path, os.R_OK):
            unreadable.append(relative)
    if missing or unreadable:
        return SmokeCheck(
            "DATA_ROOT_READABILITY", "FAIL",
            f"missing={len(missing)} unreadable={len(unreadable)}",
        )
    return SmokeCheck(
        "DATA_ROOT_READABILITY", "PASS",
        f"required_roots={len(REQUIRED_DATA_ROOTS)} readable",
    )


def check_health_schema_version(project_root: Path) -> SmokeCheck:
    path = Path(project_root) / "artifacts/daily_health/universe_data_v2_20260819.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 2:
            raise ValueError("unsupported health schema")
        if not isinstance(payload.get("datasets"), list) or not payload["datasets"]:
            raise ValueError("health datasets missing")
        if any(
            not isinstance(row, dict)
            or any(
                not isinstance(row.get(field), str) or not row[field].strip()
                for field in HEALTH_CONSUMER_CONTRACT_FIELDS
            )
            for row in payload["datasets"]
        ):
            raise ValueError("health consumer contract fields missing")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return SmokeCheck(
            "SCHEMA_VERSION_READABILITY", "FAIL",
            "health schema/version is missing, unreadable, or incompatible",
        )
    return SmokeCheck(
        "SCHEMA_VERSION_READABILITY", "PASS",
        f"health_schema=2 retained_rows={len(payload['datasets'])}",
    )


def assess_health(view: HealthArtifactView) -> tuple[SmokeCheck, tuple[str, ...]]:
    if view.artifact_state != "READY" or not view.rows:
        return (
            SmokeCheck("TYPED_HEALTH", "FAIL", "typed health artifact unavailable or invalid"),
            (),
        )
    freshness = Counter(row.freshness for row in view.rows)
    blocked = sum(row.operational == "BLOCKED" for row in view.rows)
    summary = summarize_health_artifact(view)
    managed_total = int(summary["managed_total"])
    managed_acceptable = int(summary["managed_acceptable"])
    managed_invalid = managed_total - managed_acceptable
    outside_stale_unknown = (
        freshness["STALE"] + freshness["UNKNOWN"]
        - int(summary["managed_stale"]) - int(summary["managed_unknown"])
    )
    conditions = (
        f"managed_current={summary['managed_current']}",
        f"managed_expected_provider_lag={summary['managed_expected_lag']}",
        f"approved_deferred_or_blocked={blocked}",
        f"outside_managed_stale_or_unknown={outside_stale_unknown}",
    )
    status = (
        "FAIL" if not managed_total or managed_invalid
        else "DEGRADED" if outside_stale_unknown
        else "PASS"
    )
    return (
        SmokeCheck(
            "TYPED_HEALTH", status,
            " ".join((
                f"rows={len(view.rows)}", f"current={freshness['CURRENT']}",
                f"expected_lag={freshness['EXPECTED_LAG']}",
                f"stale={freshness['STALE']}", f"unknown={freshness['UNKNOWN']}",
                f"blocked={blocked}", f"managed={managed_total}",
                f"managed_acceptable={managed_acceptable}",
                f"managed_invalid={managed_invalid}",
            )),
            "retained_data",
        ),
        conditions,
    )


def _aware_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} is not an aware timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} is not an aware timestamp")
    return parsed


def assess_health_consistency(
    project_root: Path, view: HealthArtifactView, *, now: datetime | None = None,
) -> SmokeCheck:
    """Bind the typed Health view to its raw artifact and governing receipts."""

    root = Path(project_root).resolve()
    artifact = root / "artifacts/daily_health/universe_data_v2_20260819.json"
    read_clock = now or datetime.now(KST)
    if read_clock.tzinfo is None or read_clock.utcoffset() is None:
        raise ValueError("Health consistency clock must be timezone-aware")
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 2:
            raise ValueError("Health schema differs")
        rows = payload.get("datasets")
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Health rows differ")
        identifiers = [row.get("dataset") for row in rows]
        if (
            any(not isinstance(item, str) or not item for item in identifiers)
            or len(set(identifiers)) != len(identifiers)
            or payload.get("dataset_count") != len(rows)
            or view.artifact_state != "READY"
            or tuple(identifiers) != tuple(row.dataset for row in view.rows)
        ):
            raise ValueError("Health dataset identity/count differs")
        if any(type(row.get("automation_enabled")) is not bool for row in rows):
            raise ValueError("Health automation flags differ")
        if any(
            any(
                not isinstance(row.get(field), str) or not row[field].strip()
                for field in HEALTH_CONSUMER_CONTRACT_FIELDS
            )
            for row in rows
        ):
            raise ValueError("Health consumer contract fields differ")
        typed_rows = {row.dataset: row for row in view.rows}
        if any(
            raw.get(field) != getattr(typed_rows[str(raw["dataset"])], field)
            for raw in rows
            for field in HEALTH_CONSUMER_CONTRACT_FIELDS
        ):
            raise ValueError("Health consumer contract values differ")
        managed = [row for row in rows if row["automation_enabled"]]
        managed_invalid = sum(
            row.get("freshness") not in {"CURRENT", "EXPECTED_LAG"}
            for row in managed
        )
        if (
            payload.get("automation_enabled_count") != len(managed)
            or not managed
            or managed_invalid
            or type(payload.get("runtime_coverage_failure_count")) is not int
            or payload["runtime_coverage_failure_count"] != 0
            or type(payload.get("runtime_coverage_validated_count")) is not int
            or payload["runtime_coverage_validated_count"] <= 0
        ):
            raise ValueError("Health managed SLO differs")
        actionable = sum(
            row["automation_enabled"]
            and row.get("operational") not in {"BLOCKED", "MANUAL_ONLY", "NOT_APPLICABLE"}
            and (
                row.get("freshness") in {"STALE", "UNKNOWN"}
                or str(row.get("runtime_coverage", "")).startswith("FAILED:")
            )
            for row in rows
        )
        if payload.get("actionable_incident_count") != actionable:
            raise ValueError("Health actionable count differs")
        generated = _aware_datetime(payload.get("generated_at"), field="Health generated_at")
        as_of = _aware_datetime(payload.get("as_of"), field="Health as_of")
        if generated != as_of or generated > read_clock.astimezone(generated.tzinfo) + timedelta(minutes=5):
            raise ValueError("Health generation clock differs")
        artifact_mtime = datetime.fromtimestamp(artifact.stat().st_mtime, timezone.utc)
        if artifact_mtime + timedelta(seconds=2) < generated.astimezone(timezone.utc):
            raise ValueError("Health artifact predates its generation clock")

        readback_path = root / "artifacts/scheduler_logs/STOCK_DATA_DAILY_HEALTH_last.json"
        readback = json.loads(readback_path.read_text(encoding="utf-8"))
        if not isinstance(readback, dict) or readback.get("status") != "SUCCESS":
            raise ValueError("Health readback receipt differs")
        readback_finished = _aware_datetime(
            readback.get("finished_at_utc"), field="Health readback finished_at_utc",
        )
        if (
            readback_finished < generated
            or Path(str(readback.get("output", ""))).resolve() != artifact
            or readback.get("dataset_count") != len(rows)
            or readback.get("runtime_coverage_validated_count")
            != payload["runtime_coverage_validated_count"]
            or readback.get("runtime_coverage_failure_count") != 0
            or readback.get("api_calls") != 0
        ):
            raise ValueError("Health readback does not bind the artifact")

        receipt_times = []
        for relative in GOVERNING_HEALTH_RECEIPTS:
            receipt = json.loads((root / relative).read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or receipt.get("status") not in {"PASS", "NOOP"}:
                raise ValueError("governing scheduler receipt is not successful")
            finished = _aware_datetime(
                receipt.get("finished_at_utc"), field="governing finished_at_utc",
            )
            health = receipt.get("health_projection")
            if (
                not _health_projection_is_complete(health)
                or health.get("dataset_count") != len(rows)
            ):
                raise ValueError("governing Health projection differs")
            receipt_times.append(finished)
        latest_governing = max(receipt_times)
        if generated < latest_governing:
            raise ValueError("Health generation predates a governing success")
    except (
        FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
        TypeError, ValueError,
    ):
        return SmokeCheck(
            "HEALTH_RECEIPT_RECONCILIATION", "FAIL",
            "raw Health, readback, managed SLO, or governing receipt reconciliation failed",
            "operations",
        )
    return SmokeCheck(
        "HEALTH_RECEIPT_RECONCILIATION", "PASS",
        f"rows={len(rows)} managed={len(managed)} managed_invalid=0 "
        f"governing_receipts={len(receipt_times)} chronology=consistent",
        "operations",
    )


def _frame_digest(frame: pd.DataFrame) -> str:
    digest = sha256()
    digest.update(_json_bytes(tuple((str(column), str(dtype)) for column, dtype in zip(frame.columns, frame.dtypes))))
    if len(frame):
        digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()


def _snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    metrics = snapshot.get("dashboard_metrics", {})
    metric_rows = []
    for key, metric in sorted(metrics.items()):
        metric_rows.append((
            key, metric.dataset_id, metric.series_id, metric.as_of,
            metric.expected_as_of, metric.freshness, metric.display_state.value,
            metric.value, metric.source_timestamp,
        ))
    series_rows = []
    for key, view in sorted(snapshot.get("dashboard_series", {}).items()):
        series_rows.append((key, view.metric.series_id, _frame_digest(view.frame)))
    return sha256(_json_bytes((metric_rows, series_rows, snapshot.get("health_rows", {})))).hexdigest()


def run_local_service_smoke(project_root: Path) -> dict[str, object]:
    service = DashboardService(Path(project_root))
    first = service.snapshot("U")
    first_digest = _snapshot_digest(first)
    first_files = tuple(service.query.files_read)
    second = service.snapshot("U")
    second_digest = _snapshot_digest(second)
    chart_first = service.chart_series("KOSPI", "120D")
    chart_second = service.chart_series("KOSPI", "120D")
    chart_stable = _frame_digest(chart_first) == _frame_digest(chart_second)

    leaks = []
    unavailable_current = []
    for key, metric in first.get("dashboard_metrics", {}).items():
        if metric.freshness in {"STALE", "UNKNOWN"} and metric.displays_value:
            leaks.append(key)
        if (
            metric.freshness == "CURRENT"
            and metric.display_state is not DashboardDisplayState.VALUE
        ):
            unavailable_current.append(key)
    return {
        "snapshot_stable": first_digest == second_digest,
        "chart_stable": chart_stable,
        "chart_rows": len(chart_first),
        "freshness_leaks": tuple(leaks),
        "current_unavailable": tuple(unavailable_current),
        "read_files": tuple({*first_files, *service.query.files_read}),
        "snapshot_digest": first_digest,
        "chart_digest": _frame_digest(chart_first),
    }


def assess_local_service(result: Mapping[str, object]) -> tuple[SmokeCheck, SmokeCheck]:
    if result.get("freshness_leaks"):
        suppression = SmokeCheck(
            "FRESHNESS_SUPPRESSION", "FAIL",
            f"stale_or_unknown_numeric_leaks={len(result['freshness_leaks'])}",
        )
    elif result.get("current_unavailable"):
        suppression = SmokeCheck(
            "FRESHNESS_SUPPRESSION", "DEGRADED",
            f"current_headlines_unavailable={len(result['current_unavailable'])}; numeric leak=0",
        )
    else:
        suppression = SmokeCheck(
            "FRESHNESS_SUPPRESSION", "PASS", "stale_or_unknown_numeric_leaks=0",
        )
    cache_ok = bool(result.get("snapshot_stable")) and bool(result.get("chart_stable"))
    chart_rows = int(result.get("chart_rows", 0))
    cache = SmokeCheck(
        "LOCAL_CACHE_AND_CHART",
        "PASS" if cache_ok and chart_rows else "FAIL" if not cache_ok else "DEGRADED",
        f"deterministic_repeat={cache_ok} chart_rows={chart_rows}",
    )
    return suppression, cache


def _scheduler_definition_policies(project_root: Path) -> dict[str, dict[str, object]]:
    root = Path(project_root).resolve()
    pythonw = root / ".venv/Scripts/pythonw.exe"
    provider = root / "scripts/maintenance/run_provider_scheduler.py"

    def python_daily(
        arguments: str, start: str, limit: str = "PT15M", *,
        start_when_available: bool = True,
    ) -> dict[str, object]:
        return {
            "execute": str(pythonw), "arguments": arguments,
            "working_directory": str(root), "trigger_type": "MSFT_TaskDailyTrigger",
            "start_time": start, "days_interval": 1,
            "repetition_interval": "", "repetition_duration": "",
            "start_when_available": start_when_available,
            "disallow_start_if_on_batteries": False,
            "stop_if_going_on_batteries": False, "wake_to_run": True,
            "multiple_instances": "IgnoreNew", "execution_time_limit": limit,
        }

    health_runner = root / "scripts/maintenance/reconcile_daily_health_artifact.py"
    health_arguments = (
        f'"{health_runner}" --artifact "{root / "artifacts/daily_health/core_data_20260818.json"}" '
        f'--universe-output "{root / "artifacts/daily_health/universe_data_v2_20260819.json"}" '
        f'--execution-log "{root / "artifacts/scheduler_logs/STOCK_DATA_DAILY_HEALTH_last.json"}" '
        "--universe-only"
    )
    policies = {
        "STOCK_DATA_BOK_TREASURY_DAILY": python_daily(
            f'"{root / "scripts/maintenance/run_bok_ecos_treasury_finality_observation.py"}" '
            f'--project-root "{root}"',
            "17:10", start_when_available=False,
        ),
        "STOCK_DATA_DAILY_HEALTH": python_daily(health_arguments, "06:30", "PT5M"),
        "STOCK_DATA_FRED_DAILY": python_daily(f'"{provider}" --lane FRED_DAILY', "06:00"),
        "STOCK_DATA_GLOBAL_INDEX_DAILY": python_daily(
            f'"{provider}" --lane GLOBAL_INDEX_DAILY', "06:20",
        ),
        "STOCK_DATA_GLOBAL_ETF_SOXX_DAILY": python_daily(
            f'"{provider}" --lane GLOBAL_ETF_DAILY', "06:10",
        ),
        "STOCK_DATA_GLOBAL_FUTURES_DAILY": python_daily(
            f'"{provider}" --lane GLOBAL_COMMODITY_DAILY', "22:10",
        ),
        "STOCK_DATA_KBSEC_ACCOUNT_DAILY": python_daily(
            f'"{root / "scripts/maintenance/run_kbsec_account_snapshot.py"}" '
            f'--project-root "{root}"',
            "07:10", "PT5M",
        ),
        "STOCK_DATA_TOSS_ACCOUNT_DAILY": python_daily(
            f'"{root / "scripts/maintenance/run_toss_account_snapshot.py"}" '
            f'--project-root "{root}"',
            "07:00", "PT5M",
        ),
    }
    for task_name, slot in KR_MARKET_DAILY_SLOT_TASKS.items():
        allow_latest = " --allow-latest-occurrence" if slot == "20:30" else ""
        policies[task_name] = python_daily(
            f'"{provider}" --bundle KR_MARKET_DAILY --scheduled-slot {slot}{allow_latest}',
            slot, "PT30M", start_when_available=slot == "20:30",
        )
    toss_runner = root / "scripts/manual/collect/collect_toss_domestic_ur246.py"
    policies["STOCK_DATA_TOSS_DOMESTIC_30M"] = {
        "execute": str(pythonw),
        "arguments": (
            f'"{toss_runner}" --project-root "{root}" '
            "--confirm-ur246-window"
        ),
        "working_directory": str(root),
        "trigger_type": "MSFT_TaskWeeklyTrigger", "start_time": "09:00",
        "days_of_week_mask": 62, "repetition_interval": "PT30M",
        "repetition_duration": "PT6H", "start_when_available": True,
        "disallow_start_if_on_batteries": False,
        "stop_if_going_on_batteries": False, "wake_to_run": True,
        "multiple_instances": "IgnoreNew", "execution_time_limit": "PT25M",
    }
    yahoo_runner = root / "scripts/maintenance/run_yahoo_market_current.py"
    policies["STOCK_DATA_YAHOO_MARKET_30M"] = {
        "execute": str(pythonw),
        "arguments": f'"{yahoo_runner}" --project-root "{root}"',
        "working_directory": str(root), "trigger_type": "MSFT_TaskTimeTrigger",
        "start_minute_allowed": ("02", "32"), "repetition_interval": "PT30M",
        # Windows represents an omitted repetition duration as empty or PT0S.
        "repetition_duration_allowed": ("", "PT0S"),
        "start_when_available": True, "multiple_instances": "IgnoreNew",
        "disallow_start_if_on_batteries": False,
        "stop_if_going_on_batteries": False, "wake_to_run": True,
        "execution_time_limit": "PT15M",
    }
    return policies


def query_windows_scheduler(
    project_root: Path | None = None,
) -> tuple[dict[str, object], ...]:
    if os.name != "nt":
        raise RuntimeError("Windows Task Scheduler is not available")
    quoted = ",".join("'" + item.replace("'", "''") + "'" for item in EXPECTED_SCHEDULED_TASKS)
    script = (
        "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
        "$allTasks=@(Get-ScheduledTask -ErrorAction SilentlyContinue);"
        "$tasks=@($allTasks|Where-Object {$_.TaskName -like 'STOCK_DATA_*'});"
        f"$names=@({quoted});$rows=@();foreach($name in $names){{"
        "$task=$tasks|Where-Object {$_.TaskName -eq $name}|Select-Object -First 1;"
        "if($null -eq $task){$rows += [pscustomobject]@{name=$name;exists=$false;state='MISSING';last_result=$null;namespace_task_count=$allTasks.Count}}"
        "else{$info=Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue;"
        "$actions=@($task.Actions);$triggers=@($task.Triggers);"
        "$rows += [pscustomobject]@{name=$name;exists=$true;state=[string]$task.State;"
        "last_result=if($null -eq $info){$null}else{[int64]$info.LastTaskResult};"
        "namespace_task_count=$allTasks.Count;"
        "action_count=$actions.Count;execute=if($actions.Count -eq 1){[string]$actions[0].Execute}else{''};"
        "arguments=if($actions.Count -eq 1){[string]$actions[0].Arguments}else{''};"
        "working_directory=if($actions.Count -eq 1){[string]$actions[0].WorkingDirectory}else{''};"
        "trigger_count=$triggers.Count;trigger_types=@($triggers|ForEach-Object{[string]$_.CimClass.CimClassName});"
        "trigger_enabled=@($triggers|ForEach-Object{[bool]$_.Enabled});"
        "start_times=@($triggers|ForEach-Object{try{([datetime]$_.StartBoundary).ToString('HH:mm')}catch{''}});"
        "days_intervals=@($triggers|ForEach-Object{if($null -eq $_.DaysInterval){0}else{[int]$_.DaysInterval}});"
        "days_of_week_masks=@($triggers|ForEach-Object{if($null -eq $_.DaysOfWeek){0}else{[int]$_.DaysOfWeek}});"
        "repetition_intervals=@($triggers|ForEach-Object{if($null -eq $_.Repetition){''}else{[string]$_.Repetition.Interval}});"
        "repetition_durations=@($triggers|ForEach-Object{if($null -eq $_.Repetition){''}else{[string]$_.Repetition.Duration}});"
        "start_when_available=[bool]$task.Settings.StartWhenAvailable;"
        "disallow_start_if_on_batteries=[bool]$task.Settings.DisallowStartIfOnBatteries;"
        "stop_if_going_on_batteries=[bool]$task.Settings.StopIfGoingOnBatteries;"
        "wake_to_run=[bool]$task.Settings.WakeToRun;"
        "multiple_instances=[string]$task.Settings.MultipleInstances;"
        "execution_time_limit=[string]$task.Settings.ExecutionTimeLimit}}};"
        "ConvertTo-Json -Compress -InputObject @($rows)"
    )
    shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if shell is None:
        raise RuntimeError("PowerShell is unavailable")
    completed = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    payload = json.loads(completed.stdout.strip())
    if not isinstance(payload, list):
        raise RuntimeError("scheduler probe did not return an array")
    return tuple(item for item in payload if isinstance(item, dict))


def _single(value: object) -> object:
    return value[0] if isinstance(value, list) and len(value) == 1 else None


def _normalized_path(value: object) -> str:
    return os.path.normcase(os.path.normpath(str(value))).casefold()


def _scheduler_definition_matches(
    row: Mapping[str, object], policy: Mapping[str, object],
) -> bool:
    if row.get("action_count") != 1 or row.get("trigger_count") != 1:
        return False
    if "execute" in policy:
        if _normalized_path(row.get("execute")) != _normalized_path(policy["execute"]):
            return False
    elif Path(str(row.get("execute", ""))).name.casefold() != str(
        policy["execute_basename"]
    ).casefold():
        return False
    if (
        row.get("arguments") != policy["arguments"]
        or _normalized_path(row.get("working_directory", ""))
        != _normalized_path(policy["working_directory"])
        or _single(row.get("trigger_types")) != policy["trigger_type"]
        or _single(row.get("trigger_enabled")) is not True
    ):
        return False
    start = _single(row.get("start_times"))
    if "start_time" in policy and start != policy["start_time"]:
        return False
    if "start_minute_allowed" in policy and (
        not isinstance(start, str) or start[-2:] not in policy["start_minute_allowed"]
    ):
        return False
    if "days_interval" in policy and _single(row.get("days_intervals")) != policy["days_interval"]:
        return False
    if "days_of_week_mask" in policy and _single(
        row.get("days_of_week_masks")
    ) != policy["days_of_week_mask"]:
        return False
    interval = _single(row.get("repetition_intervals"))
    duration = _single(row.get("repetition_durations"))
    if interval in {None, "PT0S"}:
        interval = ""
    if duration is None:
        duration = ""
    if interval != policy["repetition_interval"]:
        return False
    if "repetition_duration_allowed" in policy:
        if duration not in policy["repetition_duration_allowed"]:
            return False
    elif duration not in {policy["repetition_duration"], "PT0S" if not policy["repetition_duration"] else None}:
        return False
    return bool(
        row.get("start_when_available") is policy["start_when_available"]
        and row.get("disallow_start_if_on_batteries")
        is policy["disallow_start_if_on_batteries"]
        and row.get("stop_if_going_on_batteries")
        is policy["stop_if_going_on_batteries"]
        and row.get("wake_to_run") is policy["wake_to_run"]
        and row.get("multiple_instances") == policy["multiple_instances"]
        and row.get("execution_time_limit") == policy["execution_time_limit"]
    )


def assess_scheduler(
    rows: Iterable[Mapping[str, object]], project_root: Path | None = None,
) -> SmokeCheck:
    rows = tuple(rows)
    by_name = {str(row.get("name")): row for row in rows}
    missing = 0
    disabled = 0
    failed = 0
    definition_mismatch = 0
    policies = _scheduler_definition_policies(project_root or Path.cwd())
    for name in EXPECTED_SCHEDULED_TASKS:
        row = by_name.get(name)
        if row is None or row.get("exists") is not True:
            missing += 1
            continue
        state = str(row.get("state", "UNKNOWN")).upper()
        if state == "DISABLED":
            disabled += 1
        result = row.get("last_result")
        if result not in {0, None, SCHEDULER_TASK_HAS_NOT_RUN} and state != "RUNNING":
            failed += 1
        if not _scheduler_definition_matches(row, policies[name]):
            definition_mismatch += 1
    namespace_counts = {
        row.get("namespace_task_count") for row in rows
        if type(row.get("namespace_task_count")) is int
    }
    namespace_unavailable = (
        missing == len(EXPECTED_SCHEDULED_TASKS) and namespace_counts == {0}
    )
    status = (
        "DEGRADED" if namespace_unavailable
        else "FAIL" if missing or disabled or failed or definition_mismatch
        else "PASS"
    )
    return SmokeCheck(
        "SCHEDULER_READ_ONLY_STATUS", status,
        f"expected={len(EXPECTED_SCHEDULED_TASKS)} missing={missing} disabled={disabled} "
        f"nonzero={failed} definition_mismatch={definition_mismatch} "
        f"namespace_visible={missing < len(EXPECTED_SCHEDULED_TASKS)} "
        f"namespace_probe={'UNAVAILABLE' if namespace_unavailable else 'VISIBLE_OR_UNPROBED'}",
        "operations",
    )


def _expected_kr_market_daily_lanes(
    payload: Mapping[str, object], *, scheduled_for: datetime,
) -> tuple[str, ...]:
    """Resolve the exact lane contract with bounded v1-v4 receipt windows."""
    slot = str(payload.get("scheduled_slot"))
    current = KR_MARKET_DAILY_SLOT_LANES.get(slot)
    if current is None:
        raise ValueError("scheduled slot is unsupported")
    if payload.get("lane_contract_version") == KR_MARKET_DAILY_LANE_CONTRACT_VERSION:
        return current
    if (
        payload.get("lane_contract_version") == 4
        and scheduled_for.astimezone(KST)
        <= KR_MARKET_DAILY_V4_LANE_CONTRACT_CUTOFF
    ):
        return KR_MARKET_DAILY_V4_SLOT_LANES[slot]
    if (
        payload.get("lane_contract_version") == 3
        and scheduled_for.astimezone(KST)
        <= KR_MARKET_DAILY_V3_LANE_CONTRACT_CUTOFF
    ):
        return KR_MARKET_DAILY_V3_SLOT_LANES[slot]
    if (
        payload.get("lane_contract_version") == 2
        and scheduled_for.astimezone(KST)
        <= KR_MARKET_DAILY_V2_LANE_CONTRACT_CUTOFF
    ):
        return KR_MARKET_DAILY_V2_SLOT_LANES[slot]
    if (
        "lane_contract_version" not in payload
        and scheduled_for.astimezone(KST)
        <= KR_MARKET_DAILY_LEGACY_LANE_CONTRACT_CUTOFF
    ):
        return KR_MARKET_DAILY_LEGACY_SLOT_LANES[slot]
    raise ValueError("KR market lane contract version differs")


def assess_scheduler_results(
    project_root: Path, *, now: datetime | None = None,
    required_tasks: Iterable[str] | None = None,
) -> SmokeCheck:
    """Fail closed on the latest required local task-result envelopes."""
    clock = now or datetime.now(KST)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("scheduler result clock must be timezone-aware")
    selected_tasks = (
        tuple(SCHEDULER_RESULT_POLICIES)
        if required_tasks is None else tuple(required_tasks)
    )
    if (
        not selected_tasks
        or len(set(selected_tasks)) != len(selected_tasks)
        or any(task_name not in SCHEDULER_RESULT_POLICIES for task_name in selected_tasks)
    ):
        raise ValueError("required scheduler result tasks differ")
    missing = malformed = stale = failed = degraded = 0
    degraded_datasets: set[str] = set()
    for task_name in selected_tasks:
        relative, maximum_age = SCHEDULER_RESULT_POLICIES[task_name]
        try:
            payload = _strict_json_object(Path(project_root) / relative)
            if payload.get("schema_version") != 1:
                raise ValueError("scheduler result schema differs")
            raw_finished = payload.get("finished_at_utc")
            if not isinstance(raw_finished, str):
                raise ValueError("scheduler result timestamp missing")
            finished = datetime.fromisoformat(raw_finished.replace("Z", "+00:00"))
            if finished.tzinfo is None or finished.utcoffset() is None:
                raise ValueError("scheduler result timestamp is naive")
        except FileNotFoundError:
            missing += 1
            continue
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            malformed += 1
            continue
        age = clock.astimezone(KST) - finished.astimezone(KST)
        if age < -timedelta(minutes=5) or age > maximum_age:
            stale += 1
            continue
        if (
            payload.get("status") != "PASS"
            and not (
                task_name == "STOCK_DATA_KR_MARKET_DAILY_SLOT_BUNDLE"
                and payload.get("status") == "DEGRADED"
            )
        ):
            failed += 1
            continue
        if task_name == "STOCK_DATA_YAHOO_MARKET_30M":
            if not _yahoo_terminal_outcomes_are_complete(payload):
                failed += 1
        else:
            if payload.get("scheduler_process_status") != "SUCCESS":
                failed += 1
                continue
            if task_name == "STOCK_DATA_KR_MARKET_DAILY_SLOT_BUNDLE":
                if payload.get("bundle") != "KR_MARKET_DAILY":
                    failed += 1
                    continue
                slot = payload.get("scheduled_slot")
                if str(slot) not in KR_MARKET_DAILY_SLOT_LANES:
                    failed += 1
                    continue
                try:
                    scheduled_for = datetime.fromisoformat(
                        str(payload["scheduled_for"]).replace("Z", "+00:00")
                    )
                    if (
                        scheduled_for.tzinfo is None
                        or scheduled_for.utcoffset() is None
                        or scheduled_for.astimezone(KST).strftime("%H:%M") != slot
                    ):
                        raise ValueError("scheduled occurrence identity differs")
                    expected_lanes = _expected_kr_market_daily_lanes(
                        payload, scheduled_for=scheduled_for,
                    )
                    token = scheduled_for.astimezone(timezone.utc).strftime(
                        "%Y%m%dT%H%M%SZ"
                    )
                    expected_receipt = (
                        "data/state/provider_scheduler/kr_market_daily_occurrences/"
                        f"{token}-{str(slot).replace(':', '')}.json"
                    )
                    if payload.get("occurrence_receipt") != expected_receipt:
                        raise ValueError("occurrence receipt path differs")
                    receipt = json.loads(
                        (Path(project_root) / expected_receipt).read_text(encoding="utf-8")
                    )
                    if receipt != payload:
                        raise ValueError("last pointer differs from terminal receipt")
                    if (
                        payload.get("occurrence_status") != "TERMINAL_SUCCESS"
                        or payload.get("terminal_exit_code") != 0
                        or payload.get("eligible_lanes") != list(expected_lanes)
                    ):
                        raise ValueError("occurrence is not terminal success")
                    outcomes = payload.get("outcomes")
                    if not isinstance(outcomes, list) or len(outcomes) != len(expected_lanes):
                        raise ValueError("due-lane outcomes are incomplete")
                    by_lane = {
                        str(item.get("lane")): item
                        for item in outcomes if isinstance(item, dict)
                    }
                    if tuple(by_lane) != expected_lanes:
                        raise ValueError("due-lane identity or order differs")
                    health = payload.get("health_projection")
                    if not _health_projection_is_complete(health):
                        raise ValueError("Health reconciliation is unresolved")
                    if payload.get("status") != health["status"]:
                        raise ValueError("bundle and Health status differ")
                    if health["status"] == "DEGRADED":
                        degraded += 1
                        degraded_datasets.update(health["unacceptable_datasets"])
                        degraded_datasets.update(
                            health["runtime_coverage_failed_datasets"]
                        )
                    api_total = 0
                    for lane, outcome in by_lane.items():
                        lane_status = str(outcome.get("status", ""))
                        if (
                            outcome.get("scheduled_slot") != slot
                            or outcome.get("scheduled_for") != payload["scheduled_for"]
                            or not lane_status
                            or lane_status.startswith(("FAIL", "DEGRADED"))
                        ):
                            raise ValueError("lane terminal identity differs")
                        api_calls = outcome.get("api_calls")
                        if (
                            not isinstance(api_calls, int)
                            or isinstance(api_calls, bool)
                            or api_calls < 0
                        ):
                            raise ValueError("lane API count is invalid")
                        api_total += api_calls
                        result = outcome.get("result")
                        if (
                            not isinstance(result, dict)
                            or result.get("scheduled_slot") != slot
                            or result.get("scheduled_for") != payload["scheduled_for"]
                            or result.get("api_calls", 0) != api_calls
                            or result.get("scheduler_process_status") != "SUCCESS"
                            or result.get("health_projection") != health
                        ):
                            raise ValueError("lane readback evidence is incomplete")
                        if (
                            lane not in {
                                "LIQUIDITY_CREDIT_OBSERVATION",
                                "LIQUIDITY_CREDIT_DAILY",
                            }
                            and outcome.get("advancement_status")
                            not in {"UPDATED", "NOOP_CURRENT"}
                        ):
                            raise ValueError("managed lane advancement is unresolved")
                    if api_total != payload.get("api_calls"):
                        raise ValueError("bundle API total differs")
                except (
                    FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
                    KeyError, TypeError, ValueError,
                ):
                    failed += 1
    status = (
        "FAIL" if any((missing, malformed, stale, failed))
        else "DEGRADED" if degraded
        else "PASS"
    )
    degraded_summary = (
        f" degraded={degraded} unacceptable_datasets={sorted(degraded_datasets)}"
        if degraded else ""
    )
    return SmokeCheck(
        "SCHEDULER_RESULT_STATUS", status,
        f"required={len(selected_tasks)} missing={missing} malformed={malformed} "
        f"stale={stale} failed={failed}{degraded_summary}",
        "operations",
    )


def _latest_daily_occurrence(clock: datetime, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    local = clock.astimezone(KST)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate <= local else candidate - timedelta(days=1)


def _latest_yahoo_occurrence(clock: datetime) -> datetime:
    local = clock.astimezone(KST)
    minute = 32 if local.minute >= 32 else 2 if local.minute >= 2 else 32
    candidate = local.replace(minute=minute, second=0, microsecond=0)
    if local.minute < 2:
        candidate -= timedelta(hours=1)
    return candidate


def _latest_kr_occurrence(clock: datetime) -> datetime:
    return max(
        _latest_daily_occurrence(clock, slot)
        for slot in KR_MARKET_DAILY_SLOT_LANES
    )


def _latest_toss_occurrence(clock: datetime) -> datetime:
    local = clock.astimezone(KST)
    for days_back in range(8):
        day = (local - timedelta(days=days_back)).date()
        if day.weekday() >= 5:
            continue
        if days_back:
            return datetime.combine(day, wall_time(15, 0), tzinfo=KST)
        if local.time() < wall_time(9, 0):
            continue
        if local.time() >= wall_time(15, 0):
            return datetime.combine(day, wall_time(15, 0), tzinfo=KST)
        minute = 30 if local.minute >= 30 else 0
        return local.replace(minute=minute, second=0, microsecond=0)
    raise ValueError("no bounded Toss occurrence found")


def _result_finished_after_due(payload: Mapping[str, object], due: datetime) -> bool:
    finished = _aware_datetime(payload.get("finished_at_utc"), field="receipt finished_at_utc")
    return finished.astimezone(KST) >= due.astimezone(KST)


def _health_projection_is_complete(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "status", "dataset_count", "runtime_coverage_validated_count",
        "runtime_coverage_failure_count", "unacceptable_datasets",
        "runtime_coverage_failed_datasets",
    }:
        return False
    dataset_count = value.get("dataset_count")
    validated_count = value.get("runtime_coverage_validated_count")
    failure_count = value.get("runtime_coverage_failure_count")
    unacceptable = value.get("unacceptable_datasets")
    runtime_failed = value.get("runtime_coverage_failed_datasets")
    if (
        value.get("status") not in {"PASS", "DEGRADED"}
        or type(dataset_count) is not int or dataset_count <= 0
        or type(validated_count) is not int
        or not 0 <= validated_count <= dataset_count
        or type(failure_count) is not int
        or not 0 <= failure_count <= dataset_count
        or type(unacceptable) is not list
        or type(runtime_failed) is not list
        or any(type(dataset) is not str or not dataset for dataset in unacceptable)
        or any(type(dataset) is not str or not dataset for dataset in runtime_failed)
        or unacceptable != sorted(set(unacceptable))
        or runtime_failed != sorted(set(runtime_failed))
        or failure_count != len(runtime_failed)
    ):
        return False
    has_degradation = bool(unacceptable or runtime_failed)
    return value["status"] == ("DEGRADED" if has_degradation else "PASS")


def _strict_json_object(path: Path) -> dict[str, object]:
    """Load retained evidence while rejecting duplicate JSON object keys."""

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError(f"duplicate JSON key: {key}")
            payload[key] = value
        return payload

    payload = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates,
    )
    if not isinstance(payload, dict):
        raise ValueError("retained JSON root differs")
    return payload


def _yahoo_terminal_outcomes_are_complete(
    payload: Mapping[str, object],
) -> bool:
    """Bind one successful terminal outcome to every contracted Yahoo route."""

    count_fields = (
        "failed", "accepted", "api_calls", "max_api_calls", "preserved",
    )
    if any(type(payload.get(field)) is not int for field in count_fields):
        return False
    outcomes = payload.get("series_terminal_outcomes")
    if not isinstance(outcomes, list) or len(outcomes) != len(
        EXPECTED_YAHOO_TERMINAL_ROUTES
    ):
        return False
    observed: list[tuple[str, str]] = []
    preserved = 0
    for item in outcomes:
        if (
            not isinstance(item, dict)
            or set(item) != {"series_id", "lane", "outcome"}
            or not isinstance(item.get("series_id"), str)
            or not isinstance(item.get("lane"), str)
            or not isinstance(item.get("outcome"), str)
            or item["outcome"] not in YAHOO_TERMINAL_OUTCOMES_BY_LANE.get(
                item["lane"], frozenset(),
            )
        ):
            return False
        observed.append((item["lane"], item["series_id"]))
        preserved += int(item["outcome"].endswith("PRESERVED"))
    return (
        tuple(observed) == EXPECTED_YAHOO_TERMINAL_ROUTES
        and payload.get("failed") == 0
        and payload.get("accepted") == len(EXPECTED_YAHOO_TERMINAL_ROUTES)
        and payload.get("api_calls") == len(EXPECTED_YAHOO_TERMINAL_ROUTES)
        and payload.get("max_api_calls") == len(EXPECTED_YAHOO_TERMINAL_ROUTES)
        and payload.get("preserved") == preserved
    )


def _toss_terminal_outcomes_are_complete(
    outcomes: object, *, classification: object,
) -> bool:
    """Require the exact sanitized Toss route set for the occurrence class."""

    if not isinstance(outcomes, dict):
        return False
    if classification == "ELIGIBLE":
        expected = EXPECTED_TOSS_ELIGIBLE_OUTCOME_SLOTS
        accepted = {"COMPLETE", "NO_REPEAT"}
    elif classification == "INELIGIBLE":
        expected = EXPECTED_TOSS_INELIGIBLE_OUTCOME_SLOTS
        accepted = {"CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"}
    else:
        return False
    return (
        set(outcomes) == expected
        and all(type(value) is str and value in accepted for value in outcomes.values())
    )


def _toss_terminal_receipt_is_complete(payload: Mapping[str, object]) -> bool:
    """Validate one sanitized terminal independently of the mutable pointer."""

    classification = payload.get("classification")
    oauth_calls = payload.get("oauth_calls")
    business_calls = payload.get("business_calls")
    if (
        payload.get("schema_version") != 1
        or payload.get("operation_id") != "UR-246"
        or payload.get("receipt_kind") != "TERMINAL"
        or payload.get("terminal_status") != "TERMINAL_SUCCESS"
        or payload.get("terminal_exit_code") != 0
        or payload.get("failure_reason") != "NONE"
        or classification not in {"ELIGIBLE", "INELIGIBLE"}
        or not _toss_terminal_outcomes_are_complete(
            payload.get("outcomes"), classification=classification,
        )
        or type(oauth_calls) is not int
        or not 0 <= oauth_calls <= 1
        or type(business_calls) is not int
        or not 0 <= business_calls <= 4
    ):
        return False
    return classification != "INELIGIBLE" or (
        oauth_calls == 0 and business_calls == 0
    )


def _toss_account_daily_terminal_is_complete(
    project_root: Path, payload: Mapping[str, object], *, expected: datetime,
) -> bool:
    """Bind one daily account success to its exact identifier-free snapshot."""

    required_keys = {
        "schema_version", "operation", "occurrence_date", "scheduled_for",
        "claimed_at_utc", "status", "finished_at_utc", "outcome", "reason",
        "token_calls", "account_calls", "normalized", "normalized_sha256",
    }
    if set(payload) != required_keys:
        return False
    try:
        scheduled = _aware_datetime(
            payload.get("scheduled_for"), field="Toss account scheduled_for",
        )
        claimed = _aware_datetime(
            payload.get("claimed_at_utc"), field="Toss account claimed_at_utc",
        )
        finished = _aware_datetime(
            payload.get("finished_at_utc"), field="Toss account finished_at_utc",
        )
    except (TypeError, ValueError):
        return False
    normalized_relative = "data/normalized/toss_account_snapshot/latest.json"
    digest = payload.get("normalized_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or digest != digest.lower()
    ):
        return False
    try:
        int(digest, 16)
        normalized = (Path(project_root) / normalized_relative).resolve()
        normalized.relative_to(Path(project_root).resolve())
        actual_digest = sha256(normalized.read_bytes()).hexdigest()
    except (FileNotFoundError, OSError, ValueError):
        return False
    return bool(
        payload.get("schema_version") == 1
        and payload.get("operation") == "TOSS_ACCOUNT_READONLY_DAILY"
        and payload.get("occurrence_date") == expected.date().isoformat()
        and scheduled.astimezone(KST) == expected.astimezone(KST)
        and claimed.astimezone(timezone.utc) >= scheduled.astimezone(timezone.utc)
        and finished.astimezone(timezone.utc) >= claimed.astimezone(timezone.utc)
        and payload.get("status") == "TERMINAL_SUCCESS"
        and payload.get("outcome") == "SUCCEEDED"
        and payload.get("reason") is None
        and type(payload.get("token_calls")) is int
        and payload["token_calls"] in {0, 1}
        and type(payload.get("account_calls")) is int
        and payload["account_calls"] == 3
        and payload.get("normalized") == normalized_relative
        and digest == actual_digest
    )


def _kbsec_account_daily_terminal_is_complete(
    project_root: Path, payload: Mapping[str, object], *, expected: datetime,
) -> bool:
    """Bind one daily KB success to its exact identifier-free local snapshot."""

    required_keys = {
        "schema_version", "operation", "occurrence_date", "scheduled_for",
        "claimed_at_utc", "status", "finished_at_utc", "outcome", "reason",
        "supplier_calls", "snapshot", "snapshot_sha256",
    }
    if set(payload) != required_keys:
        return False
    try:
        scheduled = _aware_datetime(
            payload.get("scheduled_for"), field="KB account scheduled_for",
        )
        claimed = _aware_datetime(
            payload.get("claimed_at_utc"), field="KB account claimed_at_utc",
        )
        finished = _aware_datetime(
            payload.get("finished_at_utc"), field="KB account finished_at_utc",
        )
    except (TypeError, ValueError):
        return False
    snapshot_relative = "data/local/account_snapshots/kb_self.json"
    digest = payload.get("snapshot_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or digest != digest.lower()
    ):
        return False
    try:
        int(digest, 16)
        snapshot = (Path(project_root) / snapshot_relative).resolve()
        snapshot.relative_to(Path(project_root).resolve())
        actual_digest = sha256(snapshot.read_bytes()).hexdigest()
    except (FileNotFoundError, OSError, ValueError):
        return False
    return bool(
        payload.get("schema_version") == 1
        and payload.get("operation") == "KBSEC_ACCOUNT_READONLY_DAILY"
        and payload.get("occurrence_date") == expected.date().isoformat()
        and scheduled.astimezone(KST) == expected.astimezone(KST)
        and claimed.astimezone(timezone.utc) >= scheduled.astimezone(timezone.utc)
        and finished.astimezone(timezone.utc) >= claimed.astimezone(timezone.utc)
        and payload.get("status") == "TERMINAL_SUCCESS"
        and payload.get("outcome") == "SUCCEEDED"
        and payload.get("reason") is None
        and type(payload.get("supplier_calls")) is int
        and payload["supplier_calls"] == 1
        and payload.get("snapshot") == snapshot_relative
        and digest == actual_digest
    )


def assess_due_scheduler_outcomes(
    project_root: Path, *, now: datetime | None = None,
) -> SmokeCheck:
    """Require one outcome-complete retained result for every latest due task."""

    root = Path(project_root).resolve()
    clock = now or datetime.now(KST)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("due scheduler clock must be timezone-aware")
    failures = 0
    degraded = 0
    degraded_datasets: set[str] = set()
    due = 0
    lane_names = {
        "STOCK_DATA_FRED_DAILY": "FRED_DAILY",
        "STOCK_DATA_GLOBAL_ETF_SOXX_DAILY": "GLOBAL_ETF_DAILY",
        "STOCK_DATA_GLOBAL_INDEX_DAILY": "GLOBAL_INDEX_DAILY",
        "STOCK_DATA_GLOBAL_FUTURES_DAILY": "GLOBAL_COMMODITY_DAILY",
    }
    for task_name, (relative, hhmm) in DAILY_SCHEDULER_RESULT_POLICIES.items():
        due += 1
        try:
            payload = json.loads((root / relative).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("daily receipt differs")
            occurrence = _latest_daily_occurrence(clock, hhmm)
            if not _result_finished_after_due(payload, occurrence):
                raise ValueError("latest daily occurrence is unresolved")
            if task_name == "STOCK_DATA_DAILY_HEALTH":
                if (
                    payload.get("status") != "SUCCESS"
                    or payload.get("api_calls") != 0
                    or type(payload.get("dataset_count")) is not int
                    or payload["dataset_count"] <= 0
                    or type(payload.get("runtime_coverage_validated_count")) is not int
                    or payload["runtime_coverage_validated_count"] <= 0
                    or payload.get("runtime_coverage_failure_count") != 0
                ):
                    raise ValueError("daily Health receipt is incomplete")
                continue
            phases = payload.get("phases")
            if (
                payload.get("schema_version") != 1
                or payload.get("status") not in {"PASS", "NOOP"}
                or payload.get("scheduler_process_status") != "SUCCESS"
                or payload.get("lane") != lane_names[task_name]
                or payload.get("advancement_status") not in {"UPDATED", "NOOP_CURRENT"}
                or not isinstance(phases, list)
                or not phases
                or any(
                    not isinstance(phase, dict)
                    or not isinstance(phase.get("status"), str)
                    or phase["status"].startswith(("FAIL", "DEGRADED"))
                    or type(phase.get("http_calls")) is not int
                    or phase["http_calls"] < 0
                    for phase in phases
                )
                or sum(phase["http_calls"] for phase in phases) != payload.get("api_calls")
                or not _health_projection_is_complete(payload.get("health_projection"))
            ):
                raise ValueError("daily provider receipt is incomplete")
            health = payload["health_projection"]
            if health["status"] == "DEGRADED":
                degraded += 1
                degraded_datasets.update(health["unacceptable_datasets"])
                degraded_datasets.update(health["runtime_coverage_failed_datasets"])
        except (
            FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
            KeyError, TypeError, ValueError,
        ):
            failures += 1

    # The latest due KR slot must bind the exact immutable terminal receipt.
    due += 1
    try:
        payload = json.loads(
            (root / "artifacts/scheduler_logs/STOCK_DATA_KR_MARKET_DAILY_last.json").read_text(
                encoding="utf-8"
            )
        )
        expected = _latest_kr_occurrence(clock)
        scheduled = _aware_datetime(payload.get("scheduled_for"), field="KR scheduled_for")
        kr_result = assess_scheduler_results(
            root, now=clock,
            required_tasks=("STOCK_DATA_KR_MARKET_DAILY_SLOT_BUNDLE",),
        )
        if scheduled.astimezone(KST) != expected or kr_result.status not in {
            "PASS", "DEGRADED",
        }:
            raise ValueError("latest KR occurrence is unresolved")
        if kr_result.status == "DEGRADED":
            degraded += 1
            health = payload["health_projection"]
            degraded_datasets.update(health["unacceptable_datasets"])
            degraded_datasets.update(health["runtime_coverage_failed_datasets"])
    except (
        FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
        TypeError, ValueError,
    ):
        failures += 1

    # Yahoo's latest minute-02/32 wake must have a terminal outcome for every route.
    due += 1
    try:
        yahoo = _strict_json_object(
            root / "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json"
        )
        expected = _latest_yahoo_occurrence(clock)
        if (
            not _result_finished_after_due(yahoo, expected)
            or yahoo.get("schema_version") != 1
            or yahoo.get("status") != "PASS"
            or not _yahoo_terminal_outcomes_are_complete(yahoo)
        ):
            raise ValueError("latest Yahoo occurrence is unresolved")
    except (
        FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
        TypeError, ValueError,
    ):
        failures += 1

    # Toss uses an immutable terminal receipt plus a monotonic last pointer.
    due += 1
    try:
        pointer_path = root / "data/state/provider_scheduler/toss_domestic_ur246_last.json"
        pointer = _strict_json_object(pointer_path)
        expected = _latest_toss_occurrence(clock)
        scheduled = _aware_datetime(pointer.get("scheduled_for"), field="Toss scheduled_for")
        receipt_relative = pointer.get("receipt_path")
        if not isinstance(receipt_relative, str):
            raise ValueError("Toss receipt path differs")
        receipt_token = scheduled.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        expected_receipt_relative = (
            "data/state/provider_scheduler/toss_domestic_ur246_occurrences/"
            f"{receipt_token}.json"
        )
        if receipt_relative != expected_receipt_relative:
            raise ValueError("Toss immutable receipt identity differs")
        receipt_path = (root / receipt_relative).resolve()
        receipt_path.relative_to(root)
        receipt = _strict_json_object(receipt_path)
        pointer_terminal = dict(pointer)
        pointer_terminal.pop("receipt_path", None)
        expected_token = expected.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        due_receipt_relative = (
            "data/state/provider_scheduler/toss_domestic_ur246_occurrences/"
            f"{expected_token}.json"
        )
        due_receipt = _strict_json_object(root / due_receipt_relative)
        due_scheduled = _aware_datetime(
            due_receipt.get("scheduled_for"), field="Toss due scheduled_for",
        )
        if (
            receipt != pointer_terminal
            or not _toss_terminal_receipt_is_complete(pointer_terminal)
            or due_scheduled.astimezone(KST) != expected
            or not _toss_terminal_receipt_is_complete(due_receipt)
            or scheduled.astimezone(KST) < expected
            or (
                scheduled.astimezone(KST) > expected
                and pointer.get("classification") != "INELIGIBLE"
            )
            or not _result_finished_after_due(due_receipt, expected)
        ):
            raise ValueError("latest Toss occurrence is unresolved")
    except (
        FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
        TypeError, ValueError,
    ):
        failures += 1

    # The daily Toss account task must bind a successful identifier-free
    # occurrence to the exact sanitized Normalized snapshot and last pointer.
    due += 1
    try:
        expected = _latest_daily_occurrence(clock, "07:00")
        occurrence_relative = (
            "data/state/toss_account_snapshot_occurrences/"
            f"{expected.date().isoformat()}.json"
        )
        receipt = _strict_json_object(root / occurrence_relative)
        pointer = _strict_json_object(
            root / "artifacts/scheduler_logs/STOCK_DATA_TOSS_ACCOUNT_DAILY_last.json"
        )
        if (
            pointer != receipt
            or not _toss_account_daily_terminal_is_complete(
                root, receipt, expected=expected,
            )
        ):
            raise ValueError("latest Toss account occurrence is unresolved")
    except (
        FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
        TypeError, ValueError,
    ):
        failures += 1

    # The daily KB account task must bind a successful identifier-free
    # occurrence to the exact sanitized local snapshot and last pointer.
    due += 1
    try:
        expected = _latest_daily_occurrence(clock, "07:10")
        occurrence_relative = (
            "data/state/kbsec_account_snapshot_occurrences/"
            f"{expected.date().isoformat()}.json"
        )
        receipt = _strict_json_object(root / occurrence_relative)
        pointer = _strict_json_object(
            root / "artifacts/scheduler_logs/STOCK_DATA_KBSEC_ACCOUNT_DAILY_last.json"
        )
        if (
            pointer != receipt
            or not _kbsec_account_daily_terminal_is_complete(
                root, receipt, expected=expected,
            )
        ):
            raise ValueError("latest KB account occurrence is unresolved")
    except (
        FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
        TypeError, ValueError,
    ):
        failures += 1

    status = "FAIL" if failures else "DEGRADED" if degraded else "PASS"
    degraded_summary = (
        f" degraded={degraded} unacceptable_datasets={sorted(degraded_datasets)}"
        if degraded else ""
    )
    return SmokeCheck(
        "DUE_OCCURRENCE_OUTCOMES", status,
        f"due_task_groups={due} complete={due - failures} failed={failures}"
        f"{degraded_summary}",
        "operations",
    )


def verify_isolated_update_preservation(project_root: Path) -> SmokeCheck:
    """Exercise update-copy behavior against synthetic user files only."""

    try:
        with tempfile.TemporaryDirectory(prefix="stock_release_stage_") as directory:
            stage = Path(directory)
            user_root = stage / "user_data"
            app_root = stage / "application"
            user_root.mkdir()
            marker = user_root / "retained_profile.json"
            marker.write_bytes(b'{"schema_version":1,"synthetic":true}\n')
            before = sha256(marker.read_bytes()).hexdigest()
            app_root.mkdir()
            shutil.copy2(Path(project_root) / "app.py", app_root / "app.py")
            shutil.copy2(
                Path(project_root) / "src/stock_data/gui/health_service.py",
                app_root / "health_service.py",
            )
            after = sha256(marker.read_bytes()).hexdigest()
            if before != after:
                raise RuntimeError("synthetic user marker changed")
    except (OSError, RuntimeError):
        return SmokeCheck(
            "ISOLATED_UPDATE_PRESERVATION", "FAIL",
            "isolated synthetic preservation check failed",
        )
    return SmokeCheck(
        "ISOLATED_UPDATE_PRESERVATION", "PASS",
        "synthetic user files preserved in isolated staging; production untouched",
    )


def _page_has_horizontal_overflow(page: object, qt_widgets: object) -> bool:
    """Return whether one visible page root needs more horizontal space."""

    if isinstance(page, qt_widgets.QScrollArea):
        return page.horizontalScrollBar().maximum() > 0
    if not isinstance(page, qt_widgets.QWidget):
        return True
    layout = page.layout()
    if layout is None:
        return False
    available_width = page.contentsRect().width()
    return bool(
        available_width > 0
        and layout.minimumSize().width() > available_width
    )


def _market_chart_smoke_state(dashboard: object) -> str:
    """Classify a native chart without weakening its typed freshness gate."""

    frame = getattr(dashboard, "_market_frame", None)
    if frame is not None and len(frame):
        return "RENDERED"
    if getattr(dashboard, "_market_frame_issue", None):
        return "RENDER_FAILED"
    selector = getattr(dashboard, "market_asset", None)
    selected = selector.currentText() if selector is not None else None
    chart_metrics = getattr(dashboard, "CHART_METRICS", {})
    metrics = getattr(dashboard, "_metrics", {})
    metric = metrics.get(chart_metrics.get(selected, selected)) if selected else None
    if (
        metric is not None
        and getattr(metric, "freshness", None) == "STALE"
        and getattr(metric, "displays_value", None) is False
    ):
        return "INTENTIONAL_UNAVAILABLE"
    return "RENDER_FAILED"


def _stage_native_gui_user_data(
    project_root: Path, isolated_root: Path,
) -> dict[str, object]:
    """Copy GUI user inputs to disposable paths before opening MainWindow.

    The native smoke intentionally exercises the real retained market-data
    routes, but no component with a persistence API should receive a canonical
    account or net-worth path.  Invalid, missing, or symlinked inputs are left
    absent so the GUI renders its typed unavailable state instead of relaxing
    the read-only boundary.
    """

    project_root = Path(project_root).resolve()
    isolated_root = Path(isolated_root).resolve()

    def copy_regular(relative: str) -> Path:
        source = project_root / relative
        target = isolated_root / relative
        try:
            absolute = Path(os.path.abspath(source))
            if source.is_symlink():
                return target
            resolved = source.resolve(strict=True)
            resolved.relative_to(project_root)
            if resolved != absolute or not resolved.is_file():
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resolved, target)
        except (FileNotFoundError, OSError, ValueError):
            pass
        return target

    toss_snapshot = copy_regular(
        "data/normalized/toss_account_snapshot/latest.json"
    )
    kb_snapshot = copy_regular("data/local/account_snapshots/kb_self.json")
    family_snapshot = copy_regular(
        "data/local/account_snapshots/family_mirae_etf.json"
    )
    watchlist = copy_regular("artifacts/local_user/watchlists.json")
    net_worth_source = project_root / "data/local/net_worth_history"
    try:
        net_worth_records = tuple(net_worth_source.glob("record-*.json"))
    except OSError:
        net_worth_records = ()
    for source in net_worth_records:
        try:
            relative = source.relative_to(project_root).as_posix()
        except ValueError:
            continue
        copy_regular(relative)

    return {
        "account_snapshot_path": toss_snapshot,
        "kb_account_snapshot_path": kb_snapshot,
        "family_account_snapshot_path": family_snapshot,
        "watchlist_path": watchlist,
        "net_worth_history_root": isolated_root / "data/local/net_worth_history",
        "dashboard_preferences_path": (
            isolated_root / "artifacts/local_user/dashboard_preferences.json"
        ),
        "toss_runtime_enabled": False,
    }


def _teardown_native_gui(app: object, window: object | None, qt_core: object, *, created_app: bool) -> None:
    """Drain deferred Qt objects and stop only an application created here."""

    if window is not None:
        window.close()
        window.deleteLater()
    app.processEvents()
    qt_core.QCoreApplication.sendPostedEvents(None, qt_core.QEvent.DeferredDelete)
    app.processEvents()
    if created_app:
        app.closeAllWindows()
        app.quit()
        qt_core.QCoreApplication.sendPostedEvents(None, qt_core.QEvent.DeferredDelete)
        app.processEvents()


def _wait_for_managed_gui_quiescence(
    window: object,
    app: object,
    qt_core: object,
    *,
    timeout_ms: int = NATIVE_GUI_QUIESCENCE_TIMEOUT_MS,
    poll_interval_ms: int = NATIVE_GUI_QUIESCENCE_POLL_MS,
    sleep_ms: Callable[[int], None] | None = None,
) -> NativeGuiQuiescence:
    """Drain Qt events until every MainWindow-managed thread is released.

    The elapsed budget advances only through the bounded ``qWait`` calls. A
    final event drain at the exact deadline allows a just-finished worker's
    ``destroyed`` slot to clear the corresponding MainWindow references.
    """

    if timeout_ms < 0:
        raise ValueError("timeout_ms must be non-negative")
    if poll_interval_ms <= 0:
        raise ValueError("poll_interval_ms must be positive")
    sleeper = sleep_ms or (lambda wait_ms: time.sleep(wait_ms / 1_000))

    polls = 0
    waited_ms = 0
    while True:
        app.processEvents()
        qt_core.QCoreApplication.sendPostedEvents(
            None, qt_core.QEvent.DeferredDelete,
        )
        app.processEvents()
        threads = tuple(window._managed_worker_threads())
        stopped_threads = tuple(
            thread for thread in threads
            if thread is not None
            and callable(getattr(thread, "isRunning", None))
            and not thread.isRunning()
        )
        for thread in stopped_threads:
            # MainWindow has already queued retirement from QThread.finished.
            # A receiver-specific flush is required inside nested event drains.
            qt_core.QCoreApplication.sendPostedEvents(
                thread, qt_core.QEvent.DeferredDelete,
            )
        if stopped_threads:
            app.processEvents()
            threads = tuple(window._managed_worker_threads())
        polls += 1
        active_threads = sum(
            thread is not None for thread in threads
        )
        if active_threads == 0:
            return NativeGuiQuiescence(
                state="QUIESCENT",
                polls=polls,
                waited_ms=waited_ms,
                active_threads=0,
            )
        if waited_ms >= timeout_ms:
            return NativeGuiQuiescence(
                state="TIMEOUT",
                polls=polls,
                waited_ms=waited_ms,
                active_threads=active_threads,
            )
        wait_ms = min(poll_interval_ms, timeout_ms - waited_ms)
        # Python workers own the retained local reads. A Python sleep releases
        # the GIL; QTest.qWait can keep reacquiring it while driving a nested
        # loop and starve those workers despite continuing to pump GUI events.
        sleeper(wait_ms)
        waited_ms += wait_ms


def run_native_gui_smoke(project_root: Path) -> dict[str, object]:
    """Open one native window, visit required pages, and close every worker."""

    from PySide6 import QtCore, QtWidgets
    from stock_data.gui.account_snapshot_service import build_account_portfolio_presentation
    from stock_data.gui.font_policy import configure_application_font
    from stock_data.gui import main_window as gui_main_window

    existing_app = QtWidgets.QApplication.instance()
    created_app = existing_app is None
    app = existing_app or QtWidgets.QApplication([])
    font_policy = configure_application_font(app)
    window = None
    try:
        with tempfile.TemporaryDirectory(prefix="stock_release_gui_") as directory:
            isolated_root = Path(directory)
            isolated_user_paths = _stage_native_gui_user_data(
                Path(project_root), isolated_root,
            )
            watchlist_path = isolated_user_paths.pop("watchlist_path")
            isolated_paths = (
                watchlist_path,
                *(value for value in isolated_user_paths.values() if isinstance(value, Path)),
            )
            gui_user_data_isolation = (
                "FULLY_ISOLATED"
                if (
                    isolated_user_paths.get("toss_runtime_enabled") is False
                    and all(
                        path.resolve().is_relative_to(isolated_root.resolve())
                        for path in isolated_paths
                    )
                )
                else "UNVERIFIED"
            )
            watchlist_service = gui_main_window.LocalWatchlistService
            with patch.object(
                gui_main_window,
                "LocalWatchlistService",
                new=lambda _canonical_path: watchlist_service(watchlist_path),
            ):
                health_start = time.monotonic()
                window = gui_main_window.MainWindow(
                    Path(project_root), **isolated_user_paths,
                )
            watchlist_isolated = bool(
                window.watchlist_service.path.resolve() == watchlist_path.resolve()
                and watchlist_path.resolve().is_relative_to(isolated_root.resolve())
            )
            window.showNormal()
            window.resize(1600, 900)
            window.show()
            app.processEvents()
            time.sleep(0.5)
            app.processEvents()
            # MainWindow startup already queues the provider-free Dashboard,
            # index, Backtest, account, and net-worth reads. Re-enqueuing the
            # same reads here makes the single local-read lane process a
            # duplicate Dashboard plus pending index/chart work before close.
            startup_quiescence = _wait_for_managed_gui_quiescence(
                window, app, QtCore,
            )
            health_render_elapsed_ms = round((time.monotonic() - health_start) * 1000)

            registered = tuple(
                (window.tabs.tabText(index), window.tabs.widget(index))
                for index in range(window.tabs.count())
            )
            page_states: dict[str, bool] = {}
            clipped = []
            for name, page in registered:
                window.tabs.setCurrentWidget(page)
                app.processEvents()
                page_states[name] = bool(
                    window.tabs.currentWidget() is page and page.isVisible()
                )
                if _page_has_horizontal_overflow(page, QtWidgets):
                    clipped.append(name)
            backtest_runnable = bool(window.backtest_page.run_button.isEnabled())
            window.tabs.setCurrentWidget(window.dashboard)
            app.processEvents()
            # Visiting a lazily loaded page may start a provider-free worker
            # (for example the Research Workspace candidate scan). Drain that
            # work before attempting to close the window; otherwise Qt can
            # reject close and later destroy a still-running QThread.
            post_page_quiescence = _wait_for_managed_gui_quiescence(
                window, app, QtCore,
            )
            quiescence = NativeGuiQuiescence(
                state=(
                    "QUIESCENT"
                    if (
                        startup_quiescence.state == "QUIESCENT"
                        and post_page_quiescence.state == "QUIESCENT"
                    )
                    else "TIMEOUT"
                ),
                polls=(
                    startup_quiescence.polls + post_page_quiescence.polls
                ),
                waited_ms=(
                    startup_quiescence.waited_ms
                    + post_page_quiescence.waited_ms
                ),
                active_threads=max(
                    startup_quiescence.active_threads,
                    post_page_quiescence.active_threads,
                ),
            )
            screen = window.screen() or app.primaryScreen()
            available = screen.availableGeometry() if screen is not None else None
            baseline_supported = bool(
                available is not None
                and available.width() >= 1600
                and available.height() >= 900
            )
            dashboard_loaded = bool(window.dashboard._metrics)
            dashboard_card_overlaps: list[str] = []
            for card_id, card in window.dashboard.market_cards.items():
                visible_widgets = tuple(
                    widget for widget in (
                        card.title, card.body, card.meta,
                        card.comparison, card.sparkline,
                    )
                    if widget.isVisible()
                )
                for upper, lower in zip(visible_widgets, visible_widgets[1:]):
                    if upper.geometry().bottom() >= lower.geometry().top():
                        dashboard_card_overlaps.append(
                            f"{card_id}:{upper.objectName() or type(upper).__name__}"
                            f"->{lower.objectName() or type(lower).__name__}"
                        )
            health_rows = tuple(window.data_status_page._report_rows)
            health_loaded = bool(health_rows)
            health_managed_rows = tuple(
                row for row in health_rows
                if getattr(row, "automation", "").endswith(" / ENABLED")
            )
            health_managed_freshness = Counter(
                getattr(row, "freshness", "UNKNOWN") for row in health_managed_rows
            )
            health_managed_acceptable = (
                health_managed_freshness["CURRENT"]
                + health_managed_freshness["EXPECTED_LAG"]
            )
            index_rendered = bool(
                window.index_page._index_view is not None
                and (
                    len(window.index_page._frame)
                    or window.index_page._index_view.unavailable_reason
                )
            )
            market_chart_state = _market_chart_smoke_state(window.dashboard)
            market_chart_rendered = market_chart_state in {
                "RENDERED", "INTENTIONAL_UNAVAILABLE",
            }
            account_available = build_account_portfolio_presentation(
                window.account_page._portfolio
            ).available
            net_worth_available = window.net_worth_page._view is not None
            read_files = tuple({
                *window.service.query.files_read,
                window.backtest_service.result_path,
            })
            window.close()
            app.processEvents()
            worker_states = {
                "account": (
                    window._account_thread is None
                    and window._account_worker is None
                    and window._account_pending_trigger is None
                ),
                "current_observation": (
                    window._current_observation_thread is None
                    and window._current_observation_worker is None
                ),
                "equity": (
                    window._equity_thread is None
                    and window._equity_worker is None
                    and window._equity_pending is None
                    and window._candidate_scan_pending is False
                ),
                "us_etf": (
                    window._us_etf_thread is None
                    and window._us_etf_worker is None
                    and window._us_etf_pending is None
                ),
                "backtest": (
                    window._backtest_thread is None
                    and window._backtest_worker is None
                    and window._backtest_action is None
                ),
                "detached": not window._detached_windows,
            }
            workers_closed = bool(not window.isVisible() and all(worker_states.values()))
            result = {
                "baseline_supported": baseline_supported,
                "pages": tuple(name for name, _page in registered),
                "page_states": page_states,
                "clipped_pages": tuple(clipped),
                "dashboard_loaded": dashboard_loaded,
                "dashboard_card_overlaps": tuple(dashboard_card_overlaps),
                "font_family": font_policy.family,
                "font_glyphs_supported": font_policy.glyphs_supported,
                "health_loaded": health_loaded,
                "health_row_count": len(health_rows),
                "health_managed_total": len(health_managed_rows),
                "health_managed_current": health_managed_freshness["CURRENT"],
                "health_managed_expected_lag": health_managed_freshness["EXPECTED_LAG"],
                "health_managed_acceptable": health_managed_acceptable,
                "health_render_elapsed_ms": health_render_elapsed_ms,
                "health_render_timeout_ms": NATIVE_GUI_HEALTH_TIMEOUT_MS,
                "index_rendered": index_rendered,
                "market_chart_rendered": market_chart_rendered,
                "market_chart_state": market_chart_state,
                "watchlist_isolated": watchlist_isolated,
                "gui_user_data_isolation": gui_user_data_isolation,
                "account_state": "AVAILABLE" if account_available else "INTENTIONAL_EMPTY_OR_UNAVAILABLE",
                "net_worth_state": "AVAILABLE" if net_worth_available else "INTENTIONAL_EMPTY_OR_UNAVAILABLE",
                "backtest_runnable": backtest_runnable,
                "worker_states": worker_states,
                "workers_closed": workers_closed,
                "worker_quiescence_state": quiescence.state,
                "worker_quiescence_polls": quiescence.polls,
                "worker_quiescence_waited_ms": quiescence.waited_ms,
                "worker_quiescence_active_threads": quiescence.active_threads,
                "read_files": read_files,
            }
    finally:
        _teardown_native_gui(app, window, QtCore, created_app=created_app)
    return result


def assess_native_gui(result: Mapping[str, object]) -> SmokeCheck:
    required_flags = (
        "dashboard_loaded", "health_loaded", "index_rendered",
        "market_chart_rendered", "watchlist_isolated", "backtest_runnable",
        "workers_closed",
    )
    failed = [item for item in required_flags if not result.get(item)]
    market_chart_state = result.get("market_chart_state")
    accepted_market_chart_states = {"RENDERED", "INTENTIONAL_UNAVAILABLE"}
    market_chart_contract_ok = bool(
        market_chart_state in accepted_market_chart_states
        and result.get("market_chart_rendered") is True
    )
    isolation = result.get("gui_user_data_isolation")
    isolation_contract_ok = isolation == "FULLY_ISOLATED"
    health_rows = result.get("health_row_count")
    health_managed = result.get("health_managed_total")
    health_acceptable = result.get("health_managed_acceptable")
    health_elapsed = result.get("health_render_elapsed_ms")
    health_bound = result.get("health_render_timeout_ms", NATIVE_GUI_HEALTH_TIMEOUT_MS)
    health_contract_ok = bool(
        type(health_rows) is int and health_rows > 0
        and type(health_managed) is int and health_managed > 0
        and type(health_acceptable) is int and health_acceptable == health_managed
        and type(health_elapsed) is int
        and type(health_bound) is int and health_bound == NATIVE_GUI_HEALTH_TIMEOUT_MS
        and health_elapsed <= health_bound
    )
    pages = tuple(result.get("pages", ()))
    page_states = result.get("page_states")
    page_contract_ok = bool(
        pages == EXPECTED_GUI_PAGES
        and isinstance(page_states, Mapping)
        and tuple(page_states) == EXPECTED_GUI_PAGES
        and all(page_states.get(name) is True for name in EXPECTED_GUI_PAGES)
    )
    worker_states = result.get("worker_states")
    worker_contract_ok = bool(
        isinstance(worker_states, Mapping)
        and tuple(worker_states) == EXPECTED_GUI_WORKERS
        and all(worker_states.get(name) is True for name in EXPECTED_GUI_WORKERS)
    )
    quiescence_state = result.get("worker_quiescence_state", "QUIESCENT")
    quiescence_ok = quiescence_state == "QUIESCENT"
    clipped = tuple(result.get("clipped_pages", ()))
    dashboard_card_overlaps = tuple(result.get("dashboard_card_overlaps", ()))
    font_glyphs_supported = result.get("font_glyphs_supported") is True
    baseline = bool(result.get("baseline_supported"))
    status = (
        "FAIL"
        if (
            failed or clipped or dashboard_card_overlaps
            or not font_glyphs_supported
            or not page_contract_ok or not worker_contract_ok
            or not quiescence_ok
            or not market_chart_contract_ok or not isolation_contract_ok
            or not health_contract_ok
        )
        else "PASS" if baseline else "DEGRADED"
    )
    return SmokeCheck(
        "NATIVE_GUI_1600X900", status,
        f"pages={len(pages)} page_contract={page_contract_ok} failed={len(failed)} "
        f"clipped={len(clipped)} card_overlaps={len(dashboard_card_overlaps)} "
        f"font_glyphs={font_glyphs_supported} worker_contract={worker_contract_ok} "
        f"worker_quiescence={quiescence_state} "
        f"market_chart_state={market_chart_state} "
        f"market_chart_contract={market_chart_contract_ok} "
        f"health_rows={health_rows} health_managed={health_managed} "
        f"health_acceptable={health_acceptable} health_elapsed_ms={health_elapsed} "
        f"health_bound_ms={health_bound} health_contract={health_contract_ok} "
        f"user_data_isolation={isolation} isolation_contract={isolation_contract_ok} "
        f"screen_baseline_supported={baseline} workers_closed={bool(result.get('workers_closed'))} "
        f"account={result.get('account_state')} net_worth={result.get('net_worth_state')}",
        "gui",
    )


def _read_input_candidates(project_root: Path, *collections: Iterable[Path]) -> tuple[Path, ...]:
    root = Path(project_root)
    paths: set[Path] = {root / "artifacts/daily_health/universe_data_v2_20260819.json"}
    for collection in collections:
        paths.update(Path(item) for item in collection)
    for pattern in (
        "data/normalized/toss_account_snapshot/**/*.json",
        "data/local/account_snapshots/*.json",
        "data/local/net_worth_history/*.json",
        "artifacts/scheduler_logs/*.json",
    ):
        paths.update(root.glob(pattern))
    return tuple(paths)


def run_release_readiness(
    project_root: Path,
    *,
    scheduler_probe: Callable[[], Iterable[Mapping[str, object]]] | None = None,
    service_runner: Callable[[Path], Mapping[str, object]] = run_local_service_smoke,
    gui_runner: Callable[[Path], Mapping[str, object]] = run_native_gui_smoke,
    now: datetime | None = None,
) -> dict[str, object]:
    project_root = Path(project_root).resolve()
    clock = now or datetime.now(KST)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("release readiness clock must be timezone-aware")
    before = tree_metadata_identity(project_root)
    user_before = user_data_content_identity(project_root)
    backtest_check, backtest_path, backtest_identity = check_backtest_gui_bundle(
        project_root
    )
    checks: list[SmokeCheck] = [
        check_required_roots(project_root),
        check_health_schema_version(project_root),
        backtest_check,
    ]
    backtest_check_index = len(checks) - 1
    conditions: tuple[str, ...] = ()
    read_files: list[Path] = [backtest_path]
    gui_user_data_isolation = "UNVERIFIED"

    health = DailyHealthArtifactService(project_root).load()
    health_check, conditions = assess_health(health)
    checks.append(health_check)
    checks.append(assess_health_consistency(project_root, health, now=clock))
    try:
        service = dict(service_runner(project_root))
        checks.extend(assess_local_service(service))
        read_files.extend(service.get("read_files", ()))
        service_identity = {
            "snapshot_sha256": service.get("snapshot_digest"),
            "chart_sha256": service.get("chart_digest"),
        }
    except Exception as error:
        checks.append(SmokeCheck(
            "LOCAL_SERVICE", "FAIL", f"bounded local service check failed: {type(error).__name__}",
        ))
        service_identity = {"snapshot_sha256": None, "chart_sha256": None}
    try:
        scheduler_rows = tuple(
            query_windows_scheduler(project_root)
            if scheduler_probe is None else scheduler_probe()
        )
        checks.append(assess_scheduler(scheduler_rows, project_root))
    except Exception as error:
        checks.append(SmokeCheck(
            "SCHEDULER_READ_ONLY_STATUS", "FAIL",
            f"read-only scheduler probe failed: {type(error).__name__}", "operations",
        ))
    checks.append(assess_scheduler_results(project_root, now=clock))
    checks.append(assess_due_scheduler_outcomes(project_root, now=clock))
    checks.append(verify_isolated_update_preservation(project_root))
    try:
        gui = dict(gui_runner(project_root))
        gui_user_data_isolation = str(
            gui.get("gui_user_data_isolation", "UNVERIFIED")
        )
        checks.append(assess_native_gui(gui))
        read_files.extend(gui.get("read_files", ()))
    except Exception as error:
        checks.append(SmokeCheck(
            "NATIVE_GUI_1600X900", "FAIL",
            f"native GUI smoke failed: {type(error).__name__}", "gui",
        ))

    after = tree_metadata_identity(project_root)
    user_after = user_data_content_identity(project_root)
    final_backtest_identity = _exact_file_identity(backtest_path, project_root)
    if final_backtest_identity != backtest_identity:
        checks[backtest_check_index] = SmokeCheck(
            "BACKTEST_GUI_BUNDLE",
            "FAIL",
            "canonical GUI result changed during release readiness",
            "backtest",
        )
    backtest_identity = final_backtest_identity
    protected_unchanged = before == after
    exact_user_unchanged = user_before == user_after
    unchanged = protected_unchanged and exact_user_unchanged
    if exact_user_unchanged:
        user_data_change_attribution = "UNCHANGED"
    elif gui_user_data_isolation == "FULLY_ISOLATED":
        user_data_change_attribution = "CONCURRENT_EXTERNAL_DRIFT"
    else:
        user_data_change_attribution = "IN_PROCESS_MUTATION_NOT_EXCLUDED"
    checks.append(SmokeCheck(
        "USER_DATA_BYTE_IDENTITY", "PASS" if unchanged else "FAIL",
        f"exact_user_files={user_after.file_count} exact_user_bytes={user_after.total_bytes} "
        f"protected_files={after.file_count} protected_bytes={after.total_bytes} "
        f"exact_user_unchanged={exact_user_unchanged} "
        f"protected_unchanged={protected_unchanged} "
        f"attribution={user_data_change_attribution}",
        "safety",
    ))
    input_identity = _combined_file_identity(
        _read_input_candidates(project_root, read_files), project_root,
    )
    code = code_identity(project_root)
    statuses = Counter(check.status for check in checks)
    overall = "FAIL" if statuses["FAIL"] else "DEGRADED" if statuses["DEGRADED"] else "PASS"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": overall,
        "mode": "OFFLINE_READ_ONLY_PROVIDER_SAFE",
        "observed_at_kst": clock.astimezone(KST).isoformat(timespec="seconds"),
        "code_identity": asdict(code),
        "data_identity": {
            "retained_inputs": asdict(input_identity),
            "protected_before": asdict(before),
            "protected_after": asdict(after),
            "exact_user_data_before": asdict(user_before),
            "exact_user_data_after": asdict(user_after),
            "backtest_gui_bundle": asdict(backtest_identity),
            **service_identity,
        },
        "checks": [asdict(check) for check in checks],
        "release_blockers": [check.check_id for check in checks if check.status == "FAIL"],
        "degraded_conditions": [check.check_id for check in checks if check.status == "DEGRADED"],
        "expected_conditions": list(conditions),
        "external_calls": 0,
        "scheduler_mutations": 0,
        "gui_user_data_isolation": gui_user_data_isolation,
        "user_data_change_attribution": user_data_change_attribution,
        "data_mutations": (
            0 if unchanged else user_data_change_attribution
            if not exact_user_unchanged else "PROTECTED_TREE_CHANGE_DETECTED"
        ),
        "sensitive_values_in_report": False,
    }


__all__ = [
    "EXPECTED_GUI_PAGES", "EXPECTED_GUI_WORKERS", "EXPECTED_SCHEDULED_TASKS",
    "KR_MARKET_DAILY_SLOT_TASKS",
    "REPORT_SCHEMA_VERSION", "SCHEDULER_TASK_HAS_NOT_RUN", "SmokeCheck",
    "TreeIdentity", "assess_due_scheduler_outcomes", "assess_health",
    "assess_health_consistency", "assess_local_service", "assess_native_gui",
    "assess_scheduler", "assess_scheduler_results", "check_health_schema_version",
    "check_required_roots", "code_identity",
    "check_backtest_gui_bundle",
    "query_windows_scheduler", "run_local_service_smoke", "run_native_gui_smoke",
    "run_release_readiness", "tree_metadata_identity", "user_data_content_identity",
    "verify_isolated_update_preservation",
]
