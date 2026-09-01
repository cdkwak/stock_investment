from __future__ import annotations

import os
import sys
from collections.abc import Mapping
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
from stock_data.orchestration.toss_account_runtime import build_toss_account_runtime


_TOSS_RUNTIME_KEYS = (
    "TOSSINVEST_CLIENT_ID",
    "TOSSINVEST_CLIENT_SECRET",
    "TOSSINVEST_ACCOUNT_SEQ",
)
_ACCOUNT_RUNTIME_KEYS = (*_TOSS_RUNTIME_KEYS, *KBSEC_ACCOUNT_REQUIRED_ENV_NAMES)


def _runtime_environment(project_root: Path, process_environment: Mapping[str, str]) -> dict[str, str]:
    """Load only named read-only account settings; process values take precedence."""
    result = {
        key: value
        for key in _ACCOUNT_RUNTIME_KEYS
        if isinstance((value := process_environment.get(key)), str) and value
    }
    dotenv_path = project_root / ".env"
    if not dotenv_path.is_file():
        return result
    values = dotenv_values(dotenv_path, interpolate=False)
    for key in _ACCOUNT_RUNTIME_KEYS:
        value = values.get(key)
        if key not in result and isinstance(value, str) and value:
            result[key] = value
    return result


def build_main_window(
    project_root: Path, environment: Mapping[str, str],
    *, diagnostic_session_id: str | None = None,
) -> MainWindow:
    """Wire opt-in runtime boundaries without loading a dotenv file."""
    toss_runtime = build_toss_account_runtime(project_root, environment)
    kb_runtime = build_kbsec_account_runtime(project_root, environment)
    return MainWindow(
        project_root,
        account_refresher=toss_runtime.refresher,
        toss_runtime_enabled=toss_runtime.enabled,
        toss_runtime_reason=toss_runtime.reason,
        kb_account_refresher=kb_runtime.refresher,
        kb_runtime_enabled=kb_runtime.enabled,
        kb_runtime_reason=kb_runtime.reason,
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
