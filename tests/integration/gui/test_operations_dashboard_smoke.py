from __future__ import annotations

import os
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from stock_data.gui.operations_dashboard import OperationsDashboard, render_dashboard_png
from stock_data.orchestration.workflow_control.contracts import (
    EventKind,
    EventSource,
    Priority,
    TaskState,
    WorkflowEvent,
)
from stock_data.orchestration.workflow_control.monitoring import (
    MonitoringSnapshot,
    MonitoringSnapshotAdapter,
    RoleView,
)
from stock_data.orchestration.workflow_control.production import build_production_service
from stock_data.orchestration.workflow_control.queue_adapter import (
    QueueSnapshot,
    RequestQueueStatusAdapter,
)
from stock_data.orchestration.workflow_control.service import ServiceMode


NOW = datetime(2026, 8, 30, 6, 32, tzinfo=timezone.utc)


def _snapshot() -> MonitoringSnapshot:
    role = RoleView("python-pm", "project_manager", "active", 1, NOW, None, None, True)
    lead = RoleView("gui-lead", "domain_lead", "active", 1, NOW, None, None, True)
    queue = QueueSnapshot(NOW, (("new", 0), ("waiting", 0), ("ready", 3), ("active", 0), ("review", 0), ("blocked", 0), ("done", 0)), (), 0)
    return MonitoringSnapshot(NOW, (role,), (lead,), (), (), queue, (), (), (), {"queue": NOW})


@pytest.fixture
def app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize("size", ((1280, 720), (1600, 900)))
def test_dashboard_offscreen_smoke_has_no_horizontal_overflow_or_mutation_controls(app: QtWidgets.QApplication, size: tuple[int, int]) -> None:
    dashboard = OperationsDashboard(lambda: _snapshot())
    try:
        dashboard.resize(*size)
        dashboard.show()
        app.processEvents()
        assert dashboard.pm_card.isVisible()
        assert dashboard.pm_card.geometry().top() < dashboard.lead_section.geometry().top()
        assert dashboard.scroll.horizontalScrollBar().maximum() == 0
        assert [button.text() for button in dashboard.findChildren(QtWidgets.QPushButton)] == ["새로 고침"]
        assert dashboard.refresh_button.minimumHeight() >= 36
        dashboard.refresh_button.setFocus()
        assert dashboard.focusWidget() is dashboard.refresh_button
    finally:
        dashboard.close()
        dashboard.deleteLater()
        app.processEvents()


def test_dashboard_render_helper_writes_png(tmp_path) -> None:
    target = render_dashboard_png(_snapshot(), tmp_path / "operations-dashboard.png", size=(1280, 720))
    assert target.is_file()
    image = QtGui.QImage(str(target))
    assert image.width() == 1280
    assert image.height() == 720


def test_dashboard_large_font_keeps_primary_cards_visible(app: QtWidgets.QApplication) -> None:
    original = QtGui.QFont(app.font())
    larger = QtGui.QFont(original)
    larger.setPointSizeF(max(1.0, original.pointSizeF() * 1.25))
    app.setFont(larger)
    dashboard = OperationsDashboard(lambda: _snapshot())
    try:
        dashboard.resize(1280, 720)
        dashboard.show()
        app.processEvents()
        assert dashboard.pm_card.isVisible()
        assert dashboard.pm_heading.height() >= dashboard.pm_heading.fontMetrics().height()
        assert dashboard.scroll.horizontalScrollBar().maximum() == 0
        assert all(card.isVisible() for card in dashboard._lead_cards)
    finally:
        dashboard.close()
        dashboard.deleteLater()
        app.setFont(original)
        app.processEvents()


def _production_repository(root: Path) -> Path:
    (root / "src" / "stock_data").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# dashboard composition fixture\n", encoding="utf-8")
    return root


def _codex_stub(root: Path) -> tuple[str, str]:
    script = root / "codex_stub.py"
    script.write_text(
        "import json\nprint(json.dumps({'type':'thread.started','thread_id':'019cafe0-1234-7000-8000-abcdef123456'}))\nprint(json.dumps({'type':'turn.completed'}))\n",
        encoding="utf-8",
    )
    return sys.executable, str(script)


@pytest.mark.parametrize("size", ((1280, 720), (1600, 900)))
def test_default_repository_dashboard_observes_canonical_production_activity(
    app: QtWidgets.QApplication, tmp_path: Path, size: tuple[int, int],
) -> None:
    repository = _production_repository(tmp_path / "repository")
    service = build_production_service(
        repository, "pm-gui", ServiceMode.CANARY, command=_codex_stub(tmp_path),
    )
    service.start()
    task = "RQ-20260830T010101-AB12"
    service.canary((WorkflowEvent(
        "dashboard-canonical-active", NOW, EventKind.TASK_TRANSITION, EventSource.SYSTEM,
        task_id=task, from_state=TaskState.READY, to_state=TaskState.ACTIVE,
        priority=Priority.P1, domain="gui", reason_code="DASHBOARD_CANARY",
    ),))
    dashboard = OperationsDashboard(repository_root=repository)
    try:
        dashboard.resize(*size)
        dashboard.show()
        app.processEvents()
        assert dashboard.snapshot is not None
        assert dashboard.snapshot.pm[0].state == "idle"
        assert dashboard.snapshot.pm[0].active is True
        assert dashboard.snapshot.leads[0].active_task_id == task
        assert dashboard.snapshot.events[0].event_id == "dashboard-canonical-active"
        assert dashboard.pm_heading.text().startswith("PM · activity-")
        assert dashboard.scroll.horizontalScrollBar().maximum() == 0
        rendered = tmp_path / f"canonical-dashboard-{size[0]}x{size[1]}.png"
        assert dashboard.grab().save(str(rendered), "PNG")
        image = QtGui.QImage(str(rendered))
        assert (image.width(), image.height()) == size
    finally:
        dashboard.close()
        dashboard.deleteLater()
        app.processEvents()
        service.close()


def test_default_dashboard_timer_projects_queue_active_to_review_transition(
    app: QtWidgets.QApplication, tmp_path: Path,
) -> None:
    repository = _production_repository(tmp_path / "queue-repository")
    script = repository / "scripts" / "request_queue.py"
    script.parent.mkdir(parents=True)
    script.write_text("# status fixture; the injected runner is read-only\n", encoding="utf-8")
    queue_root = repository / "artifacts" / "request_queue"
    active_root = queue_root / "active"
    review_root = queue_root / "review"
    active_root.mkdir(parents=True)
    review_root.mkdir(parents=True)
    task_id = "RQ-20260830T010101-AB12"
    directory_name = f"P1-{task_id}-dashboard-refresh"
    active_task = active_root / directory_name
    active_task.mkdir()

    def write_meta(path: Path, state: str, updated_at: str) -> None:
        path.joinpath("META.json").write_text(json.dumps({
            "id": task_id,
            "state": state,
            "owner": "gui_lead",
            "lead_owner": "gui_lead",
            "reviewer": "gui_reviewer",
            "domain": "gui",
            "updated_at": updated_at,
        }), encoding="utf-8")

    write_meta(active_task, "active", "2026-08-30T06:32:00+00:00")

    def status_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        active = sorted(path.name for path in active_root.iterdir() if path.is_dir())
        review = sorted(path.name for path in review_root.iterdir() if path.is_dir())
        output = (
            f"new=0 waiting=0 ready=0 active={len(active)} review={len(review)} blocked=0 done=0 compacted=0\n"
            f"active={','.join(active) if active else '-'}\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    queue = RequestQueueStatusAdapter(repository, runner=status_runner)
    provider = MonitoringSnapshotAdapter(repository, queue_adapter=queue)
    dashboard = OperationsDashboard(provider, refresh_interval_ms=20)
    try:
        dashboard.show()
        app.processEvents()
        assert dashboard.pm_heading.text() == "PM · canonical-queue-pm"
        assert dashboard.pm_badge.text() == "● 작업 중"
        assert any(card.title.text() == "Lead · gui_lead" for card in dashboard._lead_cards)
        assert not dashboard.snapshot.workers
        assert not any(
            warning.code == "OWNERSHIP_CONFLICT" and "없습니다" in warning.message
            for warning in dashboard.snapshot.warnings
        )

        review_task = review_root / directory_name
        active_task.rename(review_task)
        write_meta(review_task, "review", "2026-08-30T06:33:00+00:00")
        QtTest.QTest.qWait(100)

        assert dashboard.snapshot is not None
        assert dashboard.snapshot.queue is not None
        assert dashboard.snapshot.queue.count("active") == 0
        assert dashboard.snapshot.queue.count("review") == 1
        assert dashboard.pm_badge.text() == "◇ 검토 중"
        assert any(
            card.title.text() == "Reviewer · gui_reviewer"
            and task_id in card.task.text()
            for card in dashboard._lead_cards
        )
        assert "review · Lead gui_lead · Reviewer gui_reviewer" in dashboard.queue_text.text()
    finally:
        dashboard.close()
        dashboard.deleteLater()
        app.processEvents()
