from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from stock_data.gui.operations_dashboard import OperationsDashboard
from stock_data.orchestration.workflow_control.monitoring import (
    EventView, MonitoringSnapshot, MonitoringWarning, RoleView, TaskView,
)
from stock_data.orchestration.workflow_control.queue_adapter import QueueSnapshot


NOW = datetime(2026, 8, 30, 6, 32, tzinfo=timezone.utc)
TASK_ID = "RQ-20260830T063000-UX01"


def _role(kind: str, state: str, task: str | None = None) -> RoleView:
    return RoleView(f"internal-{kind}", kind, state, 3, NOW - timedelta(seconds=18), None, task, True)


def sample_snapshot(*, warning_count: int = 1) -> MonitoringSnapshot:
    queue = QueueSnapshot(
        NOW,
        (("new", 1), ("waiting", 0), ("ready", 3), ("active", 1), ("review", 1), ("blocked", 0), ("done", 42)),
        (TASK_ID,), 155,
    )
    warnings = tuple(
        MonitoringWarning(f"W{index}", "원본 경고", "warning", f"확인 항목 {index + 1}", "작업 목록을 다시 확인하세요.")
        for index in range(warning_count)
    )
    return MonitoringSnapshot(
        NOW,
        pm=(_role("project_manager", "active", TASK_ID),),
        leads=(_role("domain_lead", "active", TASK_ID),),
        workers=(_role("worker", "active", TASK_ID),),
        reviewers=(_role("reviewer", "review", TASK_ID),),
        queue=queue,
        tasks=(TaskView(TASK_ID, "active", "P1", "gui", NOW, "작업 내용을 다듬고 있습니다.", "internal-lead", human_title="관제 화면 개선", lead="internal-lead", fix_count=2, last_activity=NOW - timedelta(minutes=1)),),
        events=(EventView("event-1", NOW, "REWORK_REQUESTED", "WORKER", TASK_ID, human_message="수정 요청을 전달했습니다."),),
        warnings=warnings,
        source_freshness={"queue": NOW},
        pm_current_decision="진행 중인 화면 개선을 검토합니다.",
        pm_next_action="수정 요청 반영을 확인합니다.",
        goal_summary="내 요청을 화면 개선 목표로 정리했습니다.",
        queue_action="작업 목록 반영 완료",
        proposal_state="PM 배정됨",
    )


@pytest.fixture
def app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _shown(window: OperationsDashboard, app: QtWidgets.QApplication, size: tuple[int, int] = (1280, 720)) -> OperationsDashboard:
    window.resize(*size)
    window.show()
    app.processEvents()
    return window


def test_dashboard_uses_korean_glanceable_priority_and_hides_technical_identifiers(app: QtWidgets.QApplication) -> None:
    dashboard = _shown(OperationsDashboard(lambda: sample_snapshot(), refresh_interval_ms=None), app)
    try:
        assert dashboard.windowTitle() == "프로젝트 작업 현황"
        assert dashboard.page_subtitle.text() == "PM이 작업을 나누고 검토하는 현재 흐름입니다"
        assert dashboard.flow_text.text() == (
            "요청 → 목표 정리 → 작업 목록 → 작업 관리자 → 담당 리드 → "
            "작업자 ↔ 검토자 → 담당 리드 → 작업 관리자"
        )
        assert dashboard.pm_heading.text() == "작업 관리자(PM)"
        card = dashboard._task_cards[0]
        assert card.title.text() == "관제 화면 개선"
        assert card.counts.text() == "작업자 1명 ↔ 검토자 1명"
        assert "수정 요청 2회" in card.activity.text()
        visible = "\n".join(label.text() for label in dashboard.findChildren(QtWidgets.QLabel))
        assert TASK_ID not in visible
        for term in ("Worker", "Reviewer", "Ready", "Active", "Review", "Queue"):
            assert term not in visible
    finally:
        dashboard.close()


@pytest.mark.parametrize("size, columns", [((1600, 900), 3), ((960, 720), 2)])
def test_task_cards_reflow_without_horizontal_scroll(app: QtWidgets.QApplication, size: tuple[int, int], columns: int) -> None:
    snapshot = replace(sample_snapshot(), tasks=tuple(
        TaskView(
            f"RQ-20260830T06300{index}-UX0{index}", "active", domain="gui",
            human_title=f"화면 개선 {index}", summary="작업 내용을 확인하고 있습니다.",
        )
        for index in range(1, 4)
    ))
    dashboard = _shown(OperationsDashboard(lambda: snapshot, refresh_interval_ms=None), app, size)
    try:
        assert dashboard.scroll.horizontalScrollBar().maximum() == 0
        assert dashboard.lead_grid.columnCount() >= min(columns, len(dashboard._task_cards))
        assert dashboard.pm_card.isVisible() and dashboard.event_panel.isVisible()
    finally:
        dashboard.close()


def test_1280_first_screen_contains_pm_task_queue_summary_and_event(app: QtWidgets.QApplication) -> None:
    dashboard = _shown(OperationsDashboard(lambda: sample_snapshot(), refresh_interval_ms=None), app)
    try:
        assert dashboard.scroll.verticalScrollBar().maximum() == 0
        viewport = dashboard.scroll.viewport().rect()
        for widget in (dashboard.pm_card, dashboard._task_cards[0], dashboard.queue_panel, dashboard.event_panel):
            assert dashboard.scroll.viewport().mapFromGlobal(widget.mapToGlobal(widget.rect().topLeft())).y() < viewport.bottom()
        assert dashboard.event_list.count() >= 1
    finally:
        dashboard.close()


def test_warning_summary_collapses_three_or_more_items(app: QtWidgets.QApplication) -> None:
    dashboard = _shown(OperationsDashboard(lambda: sample_snapshot(warning_count=3), refresh_interval_ms=None), app)
    try:
        assert "3건" in dashboard.warning_summary.text()
        assert not dashboard.warning_details.isVisible()
        dashboard.warning_toggle.click()
        assert dashboard.warning_details.isVisible()
        assert dashboard.warning_layout.count() == 3
    finally:
        dashboard.close()


def test_keyboard_focus_accessibility_selection_and_copy(app: QtWidgets.QApplication) -> None:
    dashboard = _shown(OperationsDashboard(lambda: sample_snapshot(), refresh_interval_ms=None), app)
    try:
        assert dashboard.refresh_button.accessibleName().startswith("정보 갱신 상태")
        assert dashboard.event_list.accessibleName().startswith("최근 활동 목록")
        dashboard.event_list.setFocus()
        dashboard.event_list.setCurrentRow(0)
        QtTest.QTest.keyClick(dashboard.event_list, QtCore.Qt.Key_C, QtCore.Qt.ControlModifier)
        assert "수정 요청" in QtWidgets.QApplication.clipboard().text()
        assert "border:2px solid #FFB454" in dashboard.styleSheet()
    finally:
        dashboard.close()


def test_type_scale_uses_actual_28_21_16_and_14_pixel_roles(app: QtWidgets.QApplication) -> None:
    dashboard = _shown(OperationsDashboard(lambda: sample_snapshot(), refresh_interval_ms=None), app)
    try:
        section = next(
            label for label in dashboard.findChildren(QtWidgets.QLabel)
            if label.objectName() == "sectionTitle"
        )
        assert dashboard.page_title.font().pixelSize() >= 28
        assert section.font().pixelSize() >= 21
        assert dashboard.pm_decision.font().pixelSize() >= 16
        assert dashboard._task_cards[0].summary.font().pixelSize() >= 14
        assert dashboard._task_cards[0].activity.font().pixelSize() >= 14
        assert dashboard.event_list.fontMetrics().height() >= 16
    finally:
        dashboard.close()


def test_five_second_auto_refresh_and_read_only_failure_state(app: QtWidgets.QApplication) -> None:
    calls = 0
    def provider() -> MonitoringSnapshot:
        nonlocal calls
        calls += 1
        return sample_snapshot(warning_count=0)
    dashboard = _shown(OperationsDashboard(provider, refresh_interval_ms=20), app)
    try:
        QtTest.QTest.qWait(80)
        assert calls >= 2
        assert "5초마다 자동 확인" in dashboard.refresh_status.text()
        assert [button.text() for button in dashboard.findChildren(QtWidgets.QPushButton)] == ["정보 다시 확인"]
    finally:
        dashboard.close()
    assert not dashboard._refresh_timer.isActive()
