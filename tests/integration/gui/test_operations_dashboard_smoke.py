from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtGui, QtWidgets

from stock_data.gui.operations_dashboard import OperationsDashboard, render_dashboard_png
from stock_data.orchestration.workflow_control.contracts import EventKind, EventSource, Priority, TaskState, WorkflowEvent
from stock_data.orchestration.workflow_control.monitoring import EventView, MonitoringSnapshot, RoleView, TaskView
from stock_data.orchestration.workflow_control.production import build_production_service
from stock_data.orchestration.workflow_control.queue_adapter import QueueSnapshot
from stock_data.orchestration.workflow_control.service import ServiceMode


NOW = datetime(2026, 8, 30, 6, 32, tzinfo=timezone.utc)


def _snapshot() -> MonitoringSnapshot:
    role = RoleView("opaque-pm", "project_manager", "active", 1, NOW, None, None, True)
    queue = QueueSnapshot(NOW, (("new", 0), ("waiting", 0), ("ready", 3), ("active", 1), ("review", 1), ("blocked", 0), ("done", 0)), ("RQ-20260830T010101-AB12",), 0)
    return MonitoringSnapshot(
        NOW, (role,), (), (), (), queue,
        (TaskView("RQ-20260830T010101-AB12", "active", domain="gui", human_title="화면 상태 확인", summary="현재 작업을 확인하고 있습니다."),),
        (EventView("event", NOW, "TASK_TRANSITION", "SYSTEM", human_message="작업 상태가 바뀌었습니다."),),
        (), {"queue_document": NOW},
    )


@pytest.fixture
def app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize("size", ((1280, 720), (1600, 900), (960, 720)))
def test_dashboard_sizes_show_read_only_primary_information_without_overflow(app: QtWidgets.QApplication, size: tuple[int, int]) -> None:
    dashboard = OperationsDashboard(lambda: _snapshot(), refresh_interval_ms=None)
    try:
        dashboard.resize(*size); dashboard.show(); app.processEvents()
        assert dashboard.scroll.horizontalScrollBar().maximum() == 0
        assert dashboard.pm_card.isVisible() and dashboard._task_cards[0].isVisible()
        assert dashboard.queue_panel.isVisible() and dashboard.event_list.item(0) is not None
        assert [button.text() for button in dashboard.findChildren(QtWidgets.QPushButton)] == ["정보 다시 확인"]
        if size == (1280, 720):
            assert dashboard.scroll.verticalScrollBar().maximum() == 0
            assert (
                dashboard.flow_text.fontMetrics().horizontalAdvance(
                    dashboard.flow_text.text()
                ) <= dashboard.flow_text.contentsRect().width()
            )
    finally:
        dashboard.close(); dashboard.deleteLater(); app.processEvents()


@pytest.mark.parametrize("size", ((1280, 720), (1600, 900)))
def test_dashboard_render_helper_writes_deterministic_dark_png(tmp_path: Path, size: tuple[int, int]) -> None:
    target = render_dashboard_png(_snapshot(), tmp_path / f"operations-{size[0]}x{size[1]}.png", size=size)
    image = QtGui.QImage(str(target))
    assert image.size().width() == size[0] and image.size().height() == size[1]
    assert image.pixelColor(0, 0).name().lower() == "#111315"


def test_dashboard_tolerates_125_percent_system_font_without_horizontal_cutoff(app: QtWidgets.QApplication) -> None:
    original = QtGui.QFont(app.font())
    large = QtGui.QFont(original)
    large.setPointSizeF(max(1.0, original.pointSizeF() * 1.25))
    app.setFont(large)
    dashboard = OperationsDashboard(lambda: _snapshot(), refresh_interval_ms=None)
    try:
        dashboard.resize(1280, 720); dashboard.show(); app.processEvents()
        assert dashboard.scroll.horizontalScrollBar().maximum() == 0
        assert dashboard.pm_card.isVisible() and dashboard._task_cards[0].isVisible()
        assert dashboard.event_list.item(0) is not None
    finally:
        dashboard.close(); dashboard.deleteLater(); app.setFont(original); app.processEvents()


@pytest.mark.parametrize("size", ((1280, 720), (1600, 900)))
def test_recent_activity_is_top_aligned_and_never_a_giant_empty_panel(app: QtWidgets.QApplication, size: tuple[int, int]) -> None:
    dashboard = OperationsDashboard(lambda: _snapshot(), refresh_interval_ms=None)
    try:
        dashboard.resize(*size); dashboard.show(); app.processEvents()
        assert dashboard.pm_card.height() <= 160
        assert dashboard.event_panel.height() <= 150
        assert dashboard.flow_panel.height() <= 50
        assert dashboard.warning_box.height() <= 54
        assert dashboard.event_list.geometry().top() < 70
        assert dashboard.event_list.item(0) is not None
        assert dashboard.event_list.visualItemRect(dashboard.event_list.item(0)).top() < 12
        if size == (1600, 900):
            assert dashboard.task_panel.height() <= 220
            assert dashboard.queue_panel.height() <= 220
        assert "진행·검토·확인 필요 작업" in "\n".join(
            label.text() for label in dashboard.findChildren(QtWidgets.QLabel)
        )
    finally:
        dashboard.close(); dashboard.deleteLater(); app.processEvents()


def _production_repository(root: Path) -> Path:
    (root / "src" / "stock_data").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# dashboard composition fixture\n", encoding="utf-8")
    return root


def _codex_stub(root: Path) -> tuple[str, str]:
    script = root / "codex_stub.py"
    script.write_text("import json\nprint(json.dumps({'type':'thread.started','thread_id':'019cafe0-1234-7000-8000-abcdef123456'}))\nprint(json.dumps({'type':'turn.completed'}))\n", encoding="utf-8")
    return sys.executable, str(script)


def test_default_dashboard_reads_a_real_python_pm_snapshot(app: QtWidgets.QApplication, tmp_path: Path) -> None:
    repository = _production_repository(tmp_path / "repository")
    service = build_production_service(repository, "pm-gui", ServiceMode.CANARY, command=_codex_stub(tmp_path))
    service.start()
    try:
        task = "RQ-20260830T010101-AB12"
        service.canary((WorkflowEvent("dashboard-active", NOW, EventKind.TASK_TRANSITION, EventSource.SYSTEM, task_id=task, from_state=TaskState.READY, to_state=TaskState.ACTIVE, priority=Priority.P1, domain="gui", reason_code="DASHBOARD_CANARY"),))
        dashboard = OperationsDashboard(repository_root=repository, refresh_interval_ms=None)
        try:
            dashboard.resize(1280, 720); dashboard.show(); app.processEvents()
            assert dashboard.snapshot is not None
            assert dashboard.snapshot.pm[0].active is True
            assert dashboard._task_cards[0].title.text() == "화면 개선 작업"
            assert "activity-" not in "\n".join(label.text() for label in dashboard.findChildren(QtWidgets.QLabel))
        finally:
            dashboard.close(); dashboard.deleteLater(); app.processEvents()
    finally:
        service.close()
