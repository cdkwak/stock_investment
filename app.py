from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from PySide6 import QtWidgets
from dotenv import dotenv_values
from runtime_diagnostics import (
    RuntimeDiagnosticStore,
    lifecycle_event,
    new_session_id,
    safe_append,
    safe_record_failure,
)

from stock_data.gui.main_window import MainWindow
from stock_data.gui.font_policy import configure_application_font
from stock_data.orchestration.kbsec_account_runtime import (
    KBSEC_ACCOUNT_REQUIRED_ENV_NAMES,
    build_kbsec_account_runtime,
)
from stock_data.orchestration.naver_mobile_home_windows import is_active as ur167_manifest_is_active
from stock_data.orchestration.naver_mobile_home_ur191_windows import eligible_boundary as ur191_eligible_boundary
from stock_data.orchestration.naver_remaining_session_windows import is_active as ur161_manifest_is_active
from stock_data.orchestration.nasdaq_soxx_ur193_windows import is_active as ur193_manifest_is_active
from stock_data.orchestration.naver_equity_ur199_windows import eligible_identities as ur203_eligible_identities
from stock_data.orchestration.toss_account_runtime import build_toss_account_runtime
from scripts.manual.collect.collect_naver_mobile_basic_ur161_windows import run as run_naver_mobile_basic_000660
from scripts.manual.collect.collect_naver_mobile_home_ur167_windows import run as run_naver_mobile_home
from scripts.manual.collect.collect_naver_mobile_home_ur191_windows import run as run_naver_mobile_home_ur191
from scripts.manual.collect.collect_nasdaq_soxx_ur193_windows import run as run_nasdaq_soxx_ur193
from scripts.manual.collect.collect_naver_equity_ur199_windows import run as run_naver_equity_ur199


_TOSS_RUNTIME_KEYS = (
    "TOSSINVEST_CLIENT_ID",
    "TOSSINVEST_CLIENT_SECRET",
    "TOSSINVEST_ACCOUNT_SEQ",
)
_ACCOUNT_RUNTIME_KEYS = (*_TOSS_RUNTIME_KEYS, *KBSEC_ACCOUNT_REQUIRED_ENV_NAMES)


def _runtime_environment(project_root: Path, process_environment: Mapping[str, str]) -> dict[str, str]:
    """Load only named read-only account settings; process values take precedence."""
    result = dict(process_environment)
    dotenv_path = project_root / ".env"
    if not dotenv_path.is_file():
        return result
    values = dotenv_values(dotenv_path)
    for key in _ACCOUNT_RUNTIME_KEYS:
        value = values.get(key)
        if key not in result and isinstance(value, str) and value:
            result[key] = value
    return result


def _ur161_current_observation_runner(
    project_root: Path, *, now: datetime,
):
    """Return the approved CLI only in an exact active public window.

    An absent, malformed, or inactive manifest is a local-only Dashboard
    condition.  This function deliberately does not create a manifest, read an
    environment file, or construct a provider transport.
    """
    try:
        active = ur161_manifest_is_active(project_root, now=now)
    except (OSError, RuntimeError, ValueError):
        return None
    return (lambda: run_naver_mobile_basic_000660(project_root)) if active else None


def _ur167_current_observation_runner(project_root: Path, *, now: datetime):
    """Return only the exact UR-167 collector in its public active window."""
    try:
        active = ur167_manifest_is_active(project_root, now=now)
    except (OSError, RuntimeError, ValueError):
        return None
    return (lambda: run_naver_mobile_home(project_root)) if active else None


def _ur191_current_observation_runner(project_root: Path, *, now: datetime):
    """Return UR-191 only for its current unattempted half-open boundary."""
    try:
        boundary = ur191_eligible_boundary(project_root, now=now)
    except (OSError, RuntimeError, ValueError):
        return None
    return (lambda: run_naver_mobile_home_ur191(project_root, now=now)) if boundary else None


def _ur193_current_observation_runner(project_root: Path, *, now: datetime):
    """Inject SOXX only for an unattempted current half-open manifest window."""
    try:
        active = ur193_manifest_is_active(project_root, now=now)
    except (OSError, RuntimeError, ValueError):
        return None
    return (lambda: run_nasdaq_soxx_ur193(project_root)) if active else None


def _ur203_current_observation_runner(project_root: Path, *, now: datetime):
    """Use only UR-203's public read-only preflight before callable injection."""
    try:
        eligible = ur203_eligible_identities(project_root, now=now)
    except (OSError, RuntimeError, ValueError):
        return None
    return (lambda: run_naver_equity_ur199(project_root, now=now)) if eligible else None


def _dashboard_current_observation_runner(project_root: Path, *, now: datetime):
    """Compose independent manifest-gated routes in one serial GUI worker."""
    candidates = (
        ("UR161", _ur161_current_observation_runner(project_root, now=now)),
        ("UR167", _ur167_current_observation_runner(project_root, now=now)),
        ("UR191", _ur191_current_observation_runner(project_root, now=now)),
        ("UR193", _ur193_current_observation_runner(project_root, now=now)),
        ("UR203", _ur203_current_observation_runner(project_root, now=now)),
    )
    active = tuple((name, runner) for name, runner in candidates if runner is not None)
    if not active:
        return None

    def run() -> dict[str, object]:
        results: dict[str, object] = {}
        for name, runner in active:
            try:
                results[name] = runner()
            except Exception as error:
                # Route-local collector state owns any durable failure; the
                # sibling route remains independently eligible in this worker.
                results[name] = {"status": "FAILED", "safe_code": type(error).__name__}
        return results

    return run


def build_main_window(
    project_root: Path, environment: Mapping[str, str],
    *, diagnostic_session_id: str | None = None,
) -> MainWindow:
    """Wire opt-in runtime boundaries without loading a dotenv file."""
    toss_runtime = build_toss_account_runtime(project_root, environment)
    kb_runtime = build_kbsec_account_runtime(project_root, environment)
    # Each public manifest is evaluated independently at each due request; one
    # worker serializes active collectors and the GUI performs one local reread.
    current_observation_runner_factory = lambda: _dashboard_current_observation_runner(
        project_root, now=datetime.now(timezone.utc),
    )
    return MainWindow(
        project_root,
        account_refresher=toss_runtime.refresher,
        toss_runtime_enabled=toss_runtime.enabled,
        toss_runtime_reason=toss_runtime.reason,
        kb_account_refresher=kb_runtime.refresher,
        kb_runtime_enabled=kb_runtime.enabled,
        kb_runtime_reason=kb_runtime.reason,
        current_observation_runner_factory=current_observation_runner_factory,
        diagnostic_session_id=diagnostic_session_id,
    )


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    project_root = Path(__file__).resolve().parent
    session_id = new_session_id()
    store = RuntimeDiagnosticStore(project_root / "artifacts/runtime_logs/application")
    original_hook = sys.excepthook

    def diagnostic_hook(error_type, error, trace) -> None:
        if isinstance(error, BaseException):
            safe_record_failure(
                store,
                project_root=project_root, domain="GUI",
                kind="TERMINAL_FAILURE", session_id=session_id, run_id=None,
                code="UNHANDLED_EXCEPTION", stage="EVENT_LOOP", error=error,
            )
        original_hook(error_type, error, trace)

    sys.excepthook = diagnostic_hook
    safe_append(store, lifecycle_event(
        domain="GUI", session_id=session_id, code="APP_STARTED", stage="MAIN",
    ))
    try:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        configure_application_font(app)
        window = build_main_window(
            project_root, _runtime_environment(project_root, os.environ),
            diagnostic_session_id=session_id,
        )
        window.show()
        return app.exec()
    except Exception as error:
        safe_record_failure(
            store,
            project_root=project_root, domain="GUI",
            kind="TERMINAL_FAILURE", session_id=session_id, run_id=None,
            code="APP_MAIN_FAILED", stage="MAIN", error=error,
        )
        raise
    finally:
        safe_append(store, lifecycle_event(
            domain="GUI", session_id=session_id, code="APP_STOPPED", stage="MAIN",
        ))
        sys.excepthook = original_hook


if __name__ == "__main__":
    raise SystemExit(main())
