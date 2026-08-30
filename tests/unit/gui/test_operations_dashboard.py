from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from stock_data.gui.operations_dashboard import OperationsDashboard
from stock_data.orchestration.workflow_control.monitoring import (
    EventView,
    MonitoringSnapshot,
    MonitoringWarning,
    RoleView,
    TaskView,
)
from stock_data.orchestration.workflow_control.queue_adapter import QueueSnapshot


NOW = datetime(2026, 8, 30, 6, 32, tzinfo=timezone.utc)


def _role(key: str, kind: str, state: str, task: str | None = None, *, fresh: bool = True) -> RoleView:
    return RoleView(key, kind, state, 3, NOW - timedelta(seconds=18), NOW + timedelta(minutes=2), task, fresh)


def sample_snapshot(*, warning: bool = True, events: bool = True) -> MonitoringSnapshot:
    queue = QueueSnapshot(
        NOW,
        (("new", 1), ("waiting", 0), ("ready", 3), ("active", 1), ("review", 1), ("blocked", 0), ("done", 42)),
        ("RQ-20260830T063000-UX01",),
        155,
    )
    return MonitoringSnapshot(
        observed_at=NOW,
        pm=(_role("python-pm", "project_manager", "active", "RQ-20260830T063000-UX01"),),
        leads=(
            _role("gui-lead", "domain_lead", "active", "RQ-20260830T063000-UX01"),
            _role("data-lead", "domain_lead", "idle"),
            _role("research-lead", "domain_lead", "review", "RQ-20260830T063100-BC24"),
        ),
        workers=(_role("worker-a", "worker", "active", "RQ-20260830T063000-UX01"), _role("worker-b", "worker", "idle")),
        reviewers=(_role("reviewer-a", "reviewer", "review", "RQ-20260830T063100-BC24"),),
        queue=queue,
        tasks=(TaskView("RQ-20260830T063000-UX01", "active", "P1", "gui", NOW),),
        events=(
            EventView("event-2", NOW, "TASK_TRANSITION", "WORKER", "RQ-20260830T063000-UX01", "STARTED"),
            EventView("event-1", NOW - timedelta(minutes=1), "ESCALATION", "PM", "RQ-20260830T063100-BC24", "OWNERSHIP_CONFLICT"),
        ) if events else (),
        warnings=(MonitoringWarning("OWNERSHIP_CONFLICT", "UX-02 소유권 충돌을 확인하세요.", "error"),) if warning else (),
        source_freshness={"queue": NOW, "roles": NOW - timedelta(seconds=18), "events": NOW},
    )


@pytest.fixture
def app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _shown(window: OperationsDashboard, app: QtWidgets.QApplication) -> OperationsDashboard:
    window.show()
    app.processEvents()
    return window


def test_dashboard_renders_pm_leads_workers_queue_and_events(app: QtWidgets.QApplication) -> None:
    dashboard = _shown(OperationsDashboard(lambda: sample_snapshot()), app)
    try:
        assert dashboard.pm_heading.text() == "PM · python-pm"
        assert "writer 세대 3" in dashboard.pm_activity.text()
        assert len(dashboard._lead_cards) == 4
        assert dashboard._lead_cards[0].counts.text() == "Worker 1명 · Reviewer 0명"
        assert dashboard._lead_cards[1].counts.text() == "Worker 0명 · Reviewer 0명"
        assert dashboard._lead_cards[2].counts.text() == "Worker 0명 · Reviewer 1명"
        assert dashboard._lead_cards[3].title.text() == "Reviewer · reviewer-a"
        assert dashboard._lead_cards[3].counts.text() == "Queue 검토 담당"
        assert dashboard.event_list.count() == 2
        assert dashboard.event_list.wordWrap() is True
        assert dashboard.event_list.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff
        assert "Ready 3" in dashboard.queue_text.text()
        assert "활성 작업: RQ-20260830T063000-UX01" in dashboard.queue_text.text()
    finally:
        dashboard.close()


def test_dashboard_reflows_lead_cards_for_desktop_and_narrow_width(app: QtWidgets.QApplication) -> None:
    dashboard = _shown(OperationsDashboard(lambda: sample_snapshot()), app)
    try:
        dashboard.resize(1600, 900)
        app.processEvents()
        assert max(dashboard.lead_grid.getItemPosition(index)[1] for index in range(3)) == 2
        dashboard.resize(960, 720)
        app.processEvents()
        assert max(dashboard.lead_grid.getItemPosition(index)[1] for index in range(3)) <= 1
        assert dashboard.minimumWidth() == 960
    finally:
        dashboard.close()


def test_accessible_warning_and_unknown_states_use_icon_and_text(app: QtWidgets.QApplication) -> None:
    dashboard = _shown(OperationsDashboard(lambda: sample_snapshot()), app)
    try:
        warning_text = dashboard.warning_layout.itemAt(0).widget().text()
        assert warning_text.startswith("!")
        assert "소유권 충돌" in warning_text
        unknown = OperationsDashboard(lambda: MonitoringSnapshot(observed_at=NOW))
        _shown(unknown, app)
        try:
            assert unknown.pm_badge.text() == "? 알 수 없음"
            assert unknown.pm_badge.accessibleName() == "상태: 알 수 없음"
            assert "?" in unknown.event_list.item(0).text()
            assert "? Queue 상태" in unknown.queue_text.text()
            assert unknown._lead_cards[0].title.text() == "Lead · 활성 Lead 없음"
            assert unknown._lead_cards[0].badge.text() == "○ 대기"
        finally:
            unknown.close()
    finally:
        dashboard.close()


def test_accessible_controls_have_focus_and_escape_closes_only_dashboard(app: QtWidgets.QApplication) -> None:
    dashboard = _shown(OperationsDashboard(lambda: sample_snapshot()), app)
    try:
        assert dashboard.refresh_button.focusPolicy() & QtCore.Qt.TabFocus
        dashboard.refresh_button.setFocus()
        QtWidgets.QApplication.sendEvent(dashboard, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))
        app.processEvents()
        assert not dashboard.isVisible()
    finally:
        dashboard.close()


def test_refresh_failure_is_visible_and_does_not_expose_mutation_controls(app: QtWidgets.QApplication) -> None:
    def broken() -> MonitoringSnapshot:
        raise RuntimeError("fixture failure")

    dashboard = _shown(OperationsDashboard(broken), app)
    try:
        assert dashboard.warning_box.isVisible()
        assert "상태 스냅샷을 읽지 못했습니다" in dashboard.warning_layout.itemAt(0).widget().text()
        buttons = dashboard.findChildren(QtWidgets.QPushButton)
        assert [button.text() for button in buttons] == ["새로 고침"]
        assert not any(word in dashboard.findChild(QtWidgets.QWidget).accessibleName().lower() for word in ("claim", "dispatch", "stop"))
    finally:
        dashboard.close()


def test_dashboard_timer_refreshes_and_stops_cleanly(app: QtWidgets.QApplication) -> None:
    calls = 0

    def provider() -> MonitoringSnapshot:
        nonlocal calls
        calls += 1
        return sample_snapshot(warning=False)

    dashboard = _shown(
        OperationsDashboard(provider, refresh_interval_ms=20), app,
    )
    try:
        assert calls == 1
        QtTest.QTest.qWait(90)
        assert calls >= 2
        assert dashboard._refresh_in_progress is False
    finally:
        dashboard.close()
        app.processEvents()
    assert dashboard._refresh_timer.isActive() is False
