from __future__ import annotations

import socket
import threading
import time
import json
from pathlib import Path
from unittest import mock

import pytest

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app as app_module

from PySide6 import QtCore, QtWidgets

from market_backtest.phase1_replay import (
    Phase1ReplayReceipt,
    Phase1ReplayRequest,
    run_phase1_replay,
)
from stock_data.gui.backtest_service import (
    BacktestReplayService,
    BacktestReplayServiceError,
    BacktestResultService,
    BacktestWorkflowError,
)
from stock_data.gui.main_window import MainWindow


PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_BUNDLE_FILES = (
    "bundle.json",
    "experiments.json",
    "portfolio_ledger.json",
    "result.json",
    "signals.csv",
)


def test_gui_replay_failure_keeps_exception_contract_and_writes_sanitized_event(
    tmp_path: Path,
) -> None:
    def fail(_request: Phase1ReplayRequest) -> Phase1ReplayReceipt:
        raise RuntimeError("private holding 005930")

    session_id = "a" * 32
    run_id = "b" * 32
    service = BacktestReplayService(
        tmp_path, runner=fail, diagnostic_session_id=session_id,
    )
    with pytest.raises(BacktestReplayServiceError, match="offline replay failed"):
        service.run(diagnostic_run_id=run_id)

    events = list(
        (tmp_path / "artifacts/runtime_logs/application").glob("*.json")
    )
    assert len(events) == 1
    payload = json.loads(events[0].read_text(encoding="utf-8"))
    assert payload["code"] == "BACKTEST_WORKER_FAILED"
    assert payload["session_id"] == service.session_id == session_id
    assert payload["run_id"] == run_id
    assert "005930" not in events[0].read_text(encoding="utf-8")


def test_app_session_reaches_window_and_backtest_worker_emits_one_correlated_failure(
    tmp_path: Path,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    session_id = "c" * 32

    def fail(_request: Phase1ReplayRequest) -> Phase1ReplayReceipt:
        raise RuntimeError("private account value")

    window = app_module.build_main_window(
        tmp_path, {}, diagnostic_session_id=session_id,
    )
    window.backtest_service.runner = fail
    window.show()
    try:
        assert window._diagnostic_session_id == session_id
        assert window.backtest_service.diagnostic_session_id == session_id
        assert window._request_backtest_run() is True
        _wait_until(app, lambda: window._backtest_thread is None)

        paths = list(
            (tmp_path / "artifacts/runtime_logs/application").glob("*.json")
        )
        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        failures = [
            item for item in payloads
            if item.get("code") == "BACKTEST_WORKER_FAILED"
        ]
        assert len(failures) == 1
        assert failures[0]["session_id"] == session_id
        assert failures[0]["run_id"]
    finally:
        window.close()
        _wait_until(
            app,
            lambda: all(
                thread is None for thread in window._managed_worker_threads()
            ),
        )


def _deny_network(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("the offline backtest workflow attempted network access")


def _wait_until(
    app: QtWidgets.QApplication,
    predicate,
    *,
    timeout_seconds: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.processEvents(QtCore.QEventLoop.AllEvents, 50)
        if predicate():
            return
        time.sleep(0.005)
    pytest.fail("timed out waiting for the GUI worker")


def _curve_values(curve: object) -> tuple[float, ...]:
    assert curve is not None
    _x, y = curve.getData()
    return tuple(float(value) for value in y)


@pytest.fixture(scope="module")
def frozen_replay_bundle(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Phase1ReplayReceipt]:
    """Publish the production frozen replay once, with sockets unavailable."""
    output_root = tmp_path_factory.mktemp("gui_phase1_replay") / "published"
    with (
        mock.patch.object(socket, "socket", new=_deny_network),
        mock.patch.object(socket, "create_connection", new=_deny_network),
    ):
        receipt = run_phase1_replay(Phase1ReplayRequest(
            project_root=PROJECT_ROOT,
            output_root=output_root,
        ))
    return output_root.resolve(), receipt


def test_result_service_runs_once_accepts_only_five_files_and_exports_exact_bytes(
    frozen_replay_bundle: tuple[Path, Phase1ReplayReceipt],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root, production_receipt = frozen_replay_bundle
    monkeypatch.setattr(socket, "socket", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    requests: list[Phase1ReplayRequest] = []

    def injected_runner(request: Phase1ReplayRequest) -> Phase1ReplayReceipt:
        requests.append(request)
        return production_receipt

    service = BacktestResultService(
        PROJECT_ROOT,
        output_root=output_root,
        runner=injected_runner,
    )
    accepted = service.run_validated()

    assert requests == [Phase1ReplayRequest(PROJECT_ROOT.resolve(), output_root)]
    assert tuple(sorted(path.name for path in output_root.iterdir())) == (
        EXPECTED_BUNDLE_FILES
    )
    assert tuple(sorted(accepted.artifact_bodies)) == EXPECTED_BUNDLE_FILES
    assert tuple(item.name for item in accepted.receipt.artifacts) == (
        EXPECTED_BUNDLE_FILES
    )
    assert service.load_validated_bundle(accepted.receipt) == accepted
    assert accepted.view.holdout is not None
    assert accepted.view.holdout.results_reviewed is False
    assert accepted.view.portfolio is not None
    assert accepted.view.portfolio.curve

    destination = tmp_path / "exact-export"
    export_receipt = service.export_exact_bundle(accepted, destination)
    assert export_receipt.status == "EXPORTED"
    assert export_receipt.bundle_digest == accepted.receipt.bundle_digest
    assert tuple(sorted(path.name for path in destination.iterdir())) == (
        EXPECTED_BUNDLE_FILES
    )
    assert {
        name: (destination / name).read_bytes()
        for name in EXPECTED_BUNDLE_FILES
    } == dict(accepted.artifact_bodies)

    unexpected = output_root / "unexpected.json"
    unexpected.write_bytes(b"{}")
    try:
        with pytest.raises(BacktestWorkflowError):
            service.load_validated_bundle(accepted.receipt)
    finally:
        unexpected.unlink()


def test_main_window_worker_is_nonblocking_preserves_accepted_ui_on_tamper_and_cleans_up(
    frozen_replay_bundle: tuple[Path, Phase1ReplayReceipt],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_root, production_receipt = frozen_replay_bundle
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(socket, "socket", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)

    gui_root = tmp_path / "empty-gui-project"
    gui_root.mkdir()
    started = threading.Event()
    release = threading.Event()
    requests: list[Phase1ReplayRequest] = []
    runner_thread_ids: list[int] = []

    def delayed_runner(request: Phase1ReplayRequest) -> Phase1ReplayReceipt:
        requests.append(request)
        runner_thread_ids.append(threading.get_ident())
        started.set()
        if not release.wait(10.0):
            raise RuntimeError("test runner release was not delivered")
        return production_receipt

    window: MainWindow | None = None
    signals_path = output_root / "signals.csv"
    original_signals: bytes | None = None
    closed = False
    try:
        window = MainWindow(
            PROJECT_ROOT,
            account_snapshot_path=gui_root / "missing-toss.json",
            kb_account_snapshot_path=gui_root / "missing-kb.json",
            family_account_snapshot_path=gui_root / "missing-family.json",
            toss_runtime_enabled=False,
            net_worth_history_root=gui_root / "net-worth",
            dashboard_preferences_path=gui_root / "preferences.json",
            backtest_runner=delayed_runner,
            backtest_output_root=output_root,
        )
        window.show()
        _wait_until(
            app,
            lambda: (
                window is not None
                and window._backtest_thread is None
                and window.backtest_page.has_accepted_bundle
            ),
        )

        main_thread_id = threading.get_ident()
        heartbeat_thread_ids: list[int] = []
        QtCore.QTimer.singleShot(
            0, lambda: heartbeat_thread_ids.append(threading.get_ident()),
        )
        assert window._request_backtest_run() is True
        assert window._request_backtest_run() is False
        _wait_until(app, started.is_set)
        _wait_until(app, lambda: bool(heartbeat_thread_ids))

        assert heartbeat_thread_ids == [main_thread_id]
        assert runner_thread_ids and runner_thread_ids[0] != main_thread_id
        assert len(requests) == 1
        assert requests[0] == Phase1ReplayRequest(
            PROJECT_ROOT.resolve(), output_root,
        )
        assert window.backtest_page.run_button.isEnabled() is False

        release.set()
        _wait_until(app, lambda: window is not None and window._backtest_thread is None)
        assert len(requests) == 1
        accepted = window._accepted_backtest_bundle
        assert accepted is not None
        assert window.backtest_page.has_accepted_bundle

        gui_export = tmp_path / "gui-export"
        assert window._start_backtest_job("EXPORT", gui_export) is True
        _wait_until(app, lambda: window is not None and window._backtest_thread is None)
        assert tuple(sorted(path.name for path in gui_export.iterdir())) == (
            EXPECTED_BUNDLE_FILES
        )
        assert {
            name: (gui_export / name).read_bytes()
            for name in EXPECTED_BUNDLE_FILES
        } == dict(accepted.artifact_bodies)

        metrics_text = window.backtest_page.portfolio_metrics.body.text()
        receipt_text = window.backtest_page.bundle_receipt.body.text()
        nav_values = _curve_values(window.backtest_page.nav_curve)
        drawdown_values = _curve_values(window.backtest_page.drawdown_curve)

        original_signals = signals_path.read_bytes()
        signals_path.write_bytes(original_signals + b"tampered\n")
        assert window._request_backtest_reload() is True
        _wait_until(app, lambda: window is not None and window._backtest_thread is None)

        assert window._accepted_backtest_bundle is accepted
        assert window.backtest_page.has_accepted_bundle
        assert window.backtest_page.portfolio_metrics.body.text() == metrics_text
        assert window.backtest_page.bundle_receipt.body.text() == receipt_text
        assert _curve_values(window.backtest_page.nav_curve) == nav_values
        assert _curve_values(window.backtest_page.drawdown_curve) == drawdown_values
        assert "그대로 보존" in window.backtest_page.workflow_status.text()

        signals_path.write_bytes(original_signals)
        original_signals = None
        assert window.close() is True
        app.processEvents()
        closed = True
        assert window._backtest_thread is None
        assert window._account_thread is None
        assert window._current_observation_thread is None
        assert window._equity_thread is None
        assert window._us_etf_thread is None
        assert not any(
            thread.isRunning()
            for thread in window.findChildren(QtCore.QThread)
        )
    finally:
        release.set()
        if original_signals is not None:
            signals_path.write_bytes(original_signals)
        if window is not None and not closed:
            window.close()
            app.processEvents()
