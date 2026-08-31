"""A Korean, read-only operations overview for the Python PM projection."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from PySide6 import QtCore, QtGui, QtWidgets

from stock_data.gui.font_policy import configure_application_font
from stock_data.orchestration.workflow_control.monitoring import (
    EventView,
    MonitoringSnapshot,
    MonitoringSnapshotAdapter,
    MonitoringWarning,
    TaskView,
)


class SnapshotReader(Protocol):
    def snapshot(self) -> MonitoringSnapshot: ...


_DEFAULT_REFRESH_INTERVAL_MS = 5_000
_MAX_REFRESH_INTERVAL_MS = 60 * 60 * 1_000
_STATE_META = {
    "active": ("●", "진행 중", "active"), "working": ("●", "진행 중", "active"),
    "review": ("◇", "검토 중", "review"), "reviewing": ("◇", "검토 중", "review"),
    "ready": ("▶", "시작 대기", "neutral"), "idle": ("○", "대기", "neutral"),
    "blocked": ("!", "해결 필요", "error"), "failed": ("!", "해결 필요", "error"),
    "stalled": ("!", "확인 필요", "review"), "stale": ("!", "갱신 필요", "review"),
    "stopped": ("■", "완료", "neutral"), "done": ("■", "완료", "neutral"),
    "unregistered": ("—", "미등록", "error"),
    "unknown": ("?", "확인 필요", "unknown"),
}
_DOMAIN_TITLES = {
    "gui": "화면 개선 작업", "data": "데이터 확인 작업", "research": "조사 작업",
    "backtest": "검증 작업",
}


def _state_meta(value: str | None) -> tuple[str, str, str]:
    return _STATE_META.get(str(value or "unknown").casefold(), _STATE_META["unknown"])


def _format_time(value: datetime | None) -> str:
    return value.astimezone().strftime("%H:%M") if value else "확인되지 않음"


def _age_text(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "확인되지 않음"
    seconds = max(0, int((now - value).total_seconds()))
    if seconds < 60:
        return f"{seconds}초 전"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    return f"{seconds // 3600}시간 전"


def _role_connected(role: object) -> bool:
    return bool(
        getattr(role, "active", False)
        and getattr(role, "fresh", False)
        and str(getattr(role, "state", "")).casefold()
        not in {"stopped", "recovery_required"}
    )


def _actual_activity_at(snapshot: MonitoringSnapshot) -> datetime | None:
    values = [event.occurred_at for event in snapshot.events]
    values.extend(
        role.heartbeat_at
        for role in (*snapshot.pm, *snapshot.leads, *snapshot.workers, *snapshot.reviewers)
        if role.heartbeat_at is not None
    )
    return max(values, default=None)


class StateBadge(QtWidgets.QLabel):
    """A state always carries icon, Korean text, and semantic colour."""

    def __init__(self, state: str | None = "unknown", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stateBadge")
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.set_state(state)

    def set_state(self, state: str | None) -> None:
        icon, label, tone = _state_meta(state)
        self.setText(f"{icon} {label}")
        self.setProperty("tone", tone)
        self.setAccessibleName(f"상태: {label}")
        self.style().unpolish(self)
        self.style().polish(self)


class CopyListWidget(QtWidgets.QListWidget):
    """Selectable recent activity, with the familiar Ctrl+C copy behaviour."""

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.matches(QtGui.QKeySequence.Copy):
            selected = [item.text() for item in self.selectedItems()]
            if selected:
                QtWidgets.QApplication.clipboard().setText("\n".join(selected))
                event.accept()
                return
        super().keyPressEvent(event)


class RoleCard(QtWidgets.QFrame):
    """A compact factual task card (kept under its former public name)."""

    def __init__(self, task: TaskView, *, lead_connected: bool, workers: int, reviewers: int, now: datetime, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskCard")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        top = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(task.human_title or _DOMAIN_TITLES.get(str(task.domain or "").casefold(), "제목이 없는 작업 문서"))
        self.title.setObjectName("cardTitle")
        self.title.setWordWrap(True)
        self.badge = StateBadge(task.state)
        document_badges = {
            "active": ("● 진행 문서", "진행 중인 작업 문서"),
            "review": ("◇ 검토 문서", "검토 중인 작업 문서"),
        }
        document_badge = document_badges.get(task.state.casefold())
        if document_badge is not None:
            self.badge.setText(document_badge[0])
            self.badge.setAccessibleName(f"문서 상태: {document_badge[1]}")
        top.addWidget(self.title, 1)
        top.addWidget(self.badge)
        layout.addLayout(top)
        self.summary = QtWidgets.QLabel(task.summary or "작업 문서의 설명이 없습니다.")
        self.summary.setObjectName("cardSummary")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.summary)
        owner_text = "지정됨" if task.owner else "미지정"
        session_text = "연결됨" if lead_connected else "연결되지 않음"
        self.lead = QtWidgets.QLabel(f"문서상 담당: {owner_text} · 실제 리드 세션: {session_text}")
        self.lead.setObjectName("cardMeta")
        layout.addWidget(self.lead)
        self.counts = QtWidgets.QLabel(f"실제 작업자 {workers}명 ↔ 실제 검토자 {reviewers}명")
        self.counts.setObjectName("cardMeta")
        layout.addWidget(self.counts)
        self.activity = QtWidgets.QLabel(
            f"실제 활동 {_age_text(task.last_activity, now)} · "
            f"문서 갱신 {_age_text(task.updated_at, now)} · 수정 요청 {max(0, task.fix_count)}회"
        )
        self.activity.setObjectName("cardMeta")
        layout.addWidget(self.activity)
        self.setAccessibleName(f"{self.title.text()}, {self.badge.text()}, {self.summary.text()}, {self.lead.text()}, {self.counts.text()}, {self.activity.text()}")


class OperationsDashboard(QtWidgets.QMainWindow):
    """Read-only presentation; the only action asks for a fresh snapshot."""

    def __init__(self, snapshot_provider: Callable[[], MonitoringSnapshot] | SnapshotReader | None = None, *, repository_root: Path | None = None, refresh_interval_ms: int | None = _DEFAULT_REFRESH_INTERVAL_MS, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        if refresh_interval_ms is not None and (isinstance(refresh_interval_ms, bool) or not 0 < refresh_interval_ms <= _MAX_REFRESH_INTERVAL_MS):
            raise ValueError("refresh_interval_ms must be between 1 and 3600000, or None")
        self._provider = snapshot_provider or MonitoringSnapshotAdapter(Path(repository_root or Path.cwd()))
        self._snapshot: MonitoringSnapshot | None = None
        self._task_cards: list[RoleCard] = []
        self._lead_cards = self._task_cards
        self._refresh_in_progress = False
        self.setWindowTitle("프로젝트 작업 현황")
        self.setMinimumSize(640, 520)
        self.resize(1280, 720)
        self._build_ui()
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setTimerType(QtCore.Qt.CoarseTimer)
        self._refresh_timer.timeout.connect(self.refresh_snapshot)
        if refresh_interval_ms is not None:
            self._refresh_timer.start(refresh_interval_ms)
        self.refresh_snapshot()

    @property
    def snapshot(self) -> MonitoringSnapshot | None:
        return self._snapshot

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(objectName="dashboardRoot")
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)
        header = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QVBoxLayout()
        self.page_title = QtWidgets.QLabel("프로젝트 작업 현황", objectName="pageTitle")
        self.page_subtitle = QtWidgets.QLabel("실제 세션과 작업 문서 상태를 구분해 보여줍니다", objectName="pageSubtitle")
        copy.addWidget(self.page_title); copy.addWidget(self.page_subtitle)
        header.addLayout(copy, 1)
        status = QtWidgets.QVBoxLayout()
        self.refresh_status = QtWidgets.QLabel("화면 자동 확인 · 5초 간격", objectName="refreshStatus")
        self.last_refreshed = QtWidgets.QLabel("화면 확인 시각을 읽는 중", objectName="lastRefreshed")
        status.addWidget(self.refresh_status, alignment=QtCore.Qt.AlignRight); status.addWidget(self.last_refreshed, alignment=QtCore.Qt.AlignRight)
        header.addLayout(status)
        self.refresh_button = QtWidgets.QPushButton("정보 다시 확인", objectName="refreshButton")
        self.refresh_button.setMinimumHeight(40)
        self.refresh_button.setAccessibleName("정보 갱신 상태를 읽기 전용으로 다시 확인")
        self.refresh_button.clicked.connect(self.refresh_snapshot)
        header.addWidget(self.refresh_button)
        outer.addLayout(header)
        self.scroll = QtWidgets.QScrollArea(objectName="dashboardScroll")
        self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.content = QtWidgets.QWidget(objectName="dashboardContent")
        self.content_layout = QtWidgets.QGridLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0); self.content_layout.setHorizontalSpacing(10); self.content_layout.setVerticalSpacing(7)
        self.scroll.setWidget(self.content); outer.addWidget(self.scroll, 1)
        self.pm_card = QtWidgets.QFrame(objectName="pmCard")
        self.pm_card.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Maximum)
        self.pm_card.setMaximumHeight(160)
        pm = QtWidgets.QVBoxLayout(self.pm_card); pm.setContentsMargins(16, 13, 16, 13); pm.setSpacing(5)
        pm_top = QtWidgets.QHBoxLayout()
        self.pm_heading = QtWidgets.QLabel("Python 작업 관리자(PM)", objectName="pmHeading")
        self.pm_badge = StateBadge(); pm_top.addWidget(self.pm_heading, 1); pm_top.addWidget(self.pm_badge); pm.addLayout(pm_top)
        self.pm_decision = QtWidgets.QLabel("세션 상태: 확인 중", objectName="pmDecision")
        self.pm_assignment = QtWidgets.QLabel("실행 연결: 확인 중", objectName="pmAssignment")
        self.pm_next = QtWidgets.QLabel("최근 PM 기록: 확인 중", objectName="pmNext")
        for label in (self.pm_decision, self.pm_assignment, self.pm_next):
            label.setWordWrap(True); label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); pm.addWidget(label)
        self.pm_activity = self.pm_assignment; self.pm_freshness = self.last_refreshed
        self.flow_panel = QtWidgets.QFrame(objectName="flowPanel")
        flow = QtWidgets.QHBoxLayout(self.flow_panel); flow.setContentsMargins(14, 9, 14, 9)
        self.flow_text = QtWidgets.QLabel("운영 구조 · 대화창 → 목표·문서 계획 → PM → 리드 → 작업자 ↔ 검토자 → 리드 → PM", objectName="flowText"); self.flow_text.setWordWrap(True); flow.addWidget(self.flow_text)
        self.flow_panel.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Maximum)
        self.flow_panel.setMaximumHeight(50)
        self.task_panel = self._make_panel("진행·검토·확인 필요 작업"); self.lead_section = self.task_panel
        self.lead_grid_host = QtWidgets.QWidget(); self.lead_grid = QtWidgets.QGridLayout(self.lead_grid_host)
        self.lead_grid.setContentsMargins(0, 0, 0, 0); self.lead_grid.setHorizontalSpacing(8); self.lead_grid.setVerticalSpacing(8)
        self.task_panel.layout().addWidget(self.lead_grid_host)  # type: ignore[union-attr]
        self.queue_panel = self._make_panel("작업 문서 요약 · 문서 담당과 실제 연결")
        self.queue_text = QtWidgets.QLabel(objectName="queueText"); self.queue_text.setWordWrap(True); self.queue_text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.queue_panel.layout().addWidget(self.queue_text)  # type: ignore[union-attr]
        self.event_panel = self._make_panel("최근 활동")
        self.event_list = CopyListWidget(objectName="eventList")
        self.event_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection); self.event_list.setWordWrap(True); self.event_list.setTextElideMode(QtCore.Qt.ElideNone); self.event_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.event_list.setMaximumHeight(80)
        self.event_list.setAccessibleName("최근 활동 목록. 방향키로 선택하고 Control C로 복사")
        self.event_panel.layout().addWidget(self.event_list)  # type: ignore[union-attr]
        self.event_panel.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Maximum)
        self.event_panel.setMaximumHeight(150)
        self.warning_box = QtWidgets.QFrame(objectName="warningBox")
        self.warning_box.setMaximumHeight(54)
        warning = QtWidgets.QVBoxLayout(self.warning_box); warning.setContentsMargins(14, 9, 14, 9)
        top = QtWidgets.QHBoxLayout(); self.warning_summary = QtWidgets.QLabel(objectName="warningSummary"); self.warning_summary.setWordWrap(True)
        self.warning_toggle = QtWidgets.QToolButton(text="자세히 보기", objectName="warningToggle"); self.warning_toggle.setCheckable(True); self.warning_toggle.setAccessibleName("경고 상세 내용 펼치기 또는 접기"); self.warning_toggle.toggled.connect(self._toggle_warning_details)
        top.addWidget(self.warning_summary, 1); top.addWidget(self.warning_toggle); warning.addLayout(top)
        self.warning_details = QtWidgets.QWidget(); self.warning_layout = QtWidgets.QVBoxLayout(self.warning_details); self.warning_layout.setContentsMargins(0, 2, 0, 0); self.warning_layout.setSpacing(4); warning.addWidget(self.warning_details)
        self._reflow_content(); self.setStyleSheet(_DASHBOARD_QSS)
        self.setTabOrder(self.refresh_button, self.warning_toggle); self.setTabOrder(self.warning_toggle, self.event_list)

    @staticmethod
    def _make_panel(title: str) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame(objectName="panel"); layout = QtWidgets.QVBoxLayout(panel); layout.setContentsMargins(14, 12, 14, 12); layout.setSpacing(8)
        layout.addWidget(QtWidgets.QLabel(title, objectName="sectionTitle")); return panel

    def _read_snapshot(self) -> MonitoringSnapshot:
        return self._provider() if callable(self._provider) else self._provider.snapshot()

    @QtCore.Slot()
    def refresh_snapshot(self) -> None:
        if self._refresh_in_progress: return
        self._refresh_in_progress = True; self.refresh_button.setEnabled(False)
        try:
            try: snapshot = self._read_snapshot()
            except Exception as error:
                snapshot = MonitoringSnapshot(datetime.now(timezone.utc), warnings=(MonitoringWarning("SNAPSHOT_UNREADABLE", "정보를 읽지 못했습니다.", "error", "정보를 다시 확인하세요.", f"원인: {type(error).__name__}"),))
            self.render_snapshot(snapshot)
        finally:
            self.refresh_button.setEnabled(True); self._refresh_in_progress = False

    def render_snapshot(self, snapshot: MonitoringSnapshot) -> None:
        self._snapshot = snapshot; now = snapshot.observed_at
        actual_activity = _actual_activity_at(snapshot)
        self.last_refreshed.setText(
            f"화면 확인 {_format_time(now)} · 실제 활동 {_age_text(actual_activity, now)}"
        )
        pm = max(snapshot.pm, key=lambda role: (_role_connected(role), role.generation, role.heartbeat_at or datetime.min.replace(tzinfo=timezone.utc)), default=None)
        connected_pm = pm if pm is not None and _role_connected(pm) else None
        registered_pm = pm if pm is not None and pm.active else None
        self.pm_badge.set_state(
            connected_pm.state if connected_pm else "stale" if registered_pm else "unregistered"
        )
        assigned = self._display_tasks(snapshot)
        current_documents = sum(task.state in {"active", "review"} for task in snapshot.tasks)
        if connected_pm is None and registered_pm is not None:
            self.pm_decision.setText("세션 상태: 등록되어 있지만 최근 활동 확인이 오래되었습니다.")
            self.pm_assignment.setText(f"작업 문서: 진행·검토 문서 {current_documents}건 · PM 연결 재확인 필요")
            self.pm_next.setText("필요한 조치: 다음 Python PM 깨우기 결과와 역할 heartbeat를 확인하세요.")
        elif connected_pm is None:
            self.pm_decision.setText("세션 상태: 등록되어 작동 중인 PM 세션이 없습니다.")
            self.pm_assignment.setText(f"작업 문서: 진행·검토 문서 {current_documents}건 · PM 실행 연결 없음")
            self.pm_next.setText("필요한 조치: Python PM을 시작하고 문서를 실제 리드 세션에 연결하세요.")
        else:
            task_title = next(
                (task.human_title for task in assigned if task.task_id == connected_pm.active_task_id),
                None,
            )
            self.pm_decision.setText(
                f"최근 PM 판단: {snapshot.pm_current_decision or '기록 없음'}"
            )
            self.pm_assignment.setText(
                f"실행 연결: {task_title or '현재 연결된 작업 없음'}"
            )
            self.pm_next.setText(
                f"다음 행동 기록: {snapshot.pm_next_action or '기록 없음'}"
            )
        self.pm_card.setAccessibleName("작업 관리자 PM. " + " ".join((self.pm_badge.text(), self.pm_decision.text(), self.pm_assignment.text(), self.pm_next.text())))
        self._render_tasks(assigned, snapshot); self._render_queue(snapshot); self._render_events(snapshot.events); self._render_warnings(snapshot.warnings); self._reflow_content()

    def _display_tasks(self, snapshot: MonitoringSnapshot) -> list[TaskView]:
        current = [task for task in snapshot.tasks if task.state in {"active", "review", "blocked", "failed"}]
        if not current: current = list(snapshot.tasks[:1])
        return sorted(current, key=lambda task: ({"blocked": 0, "failed": 0, "review": 1, "active": 2}.get(task.state, 3), task.task_id))[:3]

    def _render_tasks(self, tasks: list[TaskView], snapshot: MonitoringSnapshot) -> None:
        while self.lead_grid.count():
            item = self.lead_grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        if not tasks: tasks = [TaskView("", "unknown", human_title="현재 확인할 작업 없음", summary="작업 목록이 비어 있거나 아직 확인되지 않았습니다.")]
        self._task_cards = []; self._lead_cards = self._task_cards
        for task in tasks:
            lead_connected = any(_role_connected(lead) and lead.active_task_id == task.task_id for lead in snapshot.leads)
            workers = sum(_role_connected(worker) and worker.active_task_id == task.task_id for worker in snapshot.workers)
            reviewers = sum(_role_connected(reviewer) and reviewer.active_task_id == task.task_id for reviewer in snapshot.reviewers)
            self._task_cards.append(RoleCard(task, lead_connected=lead_connected, workers=workers, reviewers=reviewers, now=snapshot.observed_at))
        self._reflow_task_cards()

    def _reflow_task_cards(self) -> None:
        if not self._task_cards: return
        while self.lead_grid.count(): self.lead_grid.takeAt(0)
        width = max(1, self.width()); desired = 3 if width >= 1280 else 2 if width >= 680 else 1
        columns = min(desired, len(self._task_cards))
        for index, card in enumerate(self._task_cards): self.lead_grid.addWidget(card, index // columns, index % columns)
        for index in range(columns): self.lead_grid.setColumnStretch(index, 1)

    def _render_queue(self, snapshot: MonitoringSnapshot) -> None:
        queue = snapshot.queue
        if queue is None: text = "작업 문서를 확인하지 못했습니다. 다음 화면 확인 때 다시 읽습니다."
        else:
            counts = " · ".join((f"시작 대기 {queue.count('ready')}", f"진행 중 {queue.count('active')}", f"검토 중 {queue.count('review')}", f"해결 필요 {queue.count('blocked')}", f"완료 {queue.count('done')}"))
            ownership = "문서상 담당: 현재 문서 없음 · 실제 리드 세션: 연결 없음"
            if queue.current_tasks:
                declared = sum(bool(item.owner) for item in queue.current_tasks)
                connected = sum(
                    any(_role_connected(lead) and lead.active_task_id == item.task_id for lead in snapshot.leads)
                    for item in queue.current_tasks
                )
                ownership = (
                    f"문서상 담당: {declared}/{len(queue.current_tasks)}건 지정 · "
                    f"실제 리드 세션: {connected}/{len(queue.current_tasks)}건 연결"
                )
            goal = snapshot.goal_summary or "목표 설명은 이 화면의 관측 자료에 없습니다."
            action = snapshot.queue_action or "작업 문서 상태를 확인하지 못했습니다."
            text = f"{goal}\n{action}\n{counts}\n{ownership}"
        self.queue_text.setText(text); self.queue_panel.setAccessibleName("작업 문서 요약. " + text.replace("\n", " "))

    def _render_events(self, events: tuple[EventView, ...]) -> None:
        self.event_list.clear(); rows = sorted(events, key=lambda item: item.occurred_at, reverse=True)
        if not rows: self.event_list.addItem("— 실제 에이전트 활동 기록이 없습니다."); return
        for event in rows:
            item = QtWidgets.QListWidgetItem(f"{_format_time(event.occurred_at)}  {event.human_message or '최근 활동을 확인했습니다.'}")
            item.setToolTip(f"발생 시각: {event.occurred_at.isoformat()}"); self.event_list.addItem(item)

    def _render_warnings(self, warnings: tuple[MonitoringWarning, ...]) -> None:
        while self.warning_layout.count():
            item = self.warning_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.warning_box.setVisible(bool(warnings))
        if not warnings: return
        priority = {
            "PM_SESSION_MISSING": 0,
            "DUPLICATE_PM_GENERATION": 1,
            "LEAD_SESSION_MISSING": 2,
            "REVIEWER_SESSION_MISSING": 3,
            "OWNERSHIP_CONFLICT": 4,
        }
        ordered = tuple(sorted(
            warnings,
            key=lambda warning: (
                0 if warning.severity == "error" else 1,
                priority.get(warning.code, 99),
                warning.code,
            ),
        ))
        first = ordered[0]; title = first.human_title or first.message; suffix = f" 외 {len(ordered) - 1}건" if len(ordered) > 1 else ""
        self.warning_summary.setText(f"! 확인할 내용 {len(warnings)}건 · {title}{suffix}")
        for warning in ordered:
            action = warning.operator_action or "정보를 다시 확인하세요."
            label = QtWidgets.QLabel(f"! {warning.human_title or warning.message} · {action}", objectName="warningText"); label.setWordWrap(True); label.setAccessibleName(f"경고: {warning.human_title or warning.message}. 안내: {action}"); self.warning_layout.addWidget(label)
        self._toggle_warning_details(self.warning_toggle.isChecked())

    def _toggle_warning_details(self, expanded: bool) -> None:
        self.warning_details.setVisible(expanded)
        self.warning_box.setMaximumHeight(QtWidgets.QWIDGETSIZE_MAX if expanded else 54)
        self.warning_toggle.setText("접기" if expanded else "자세히 보기")
        self.warning_toggle.setAccessibleName("경고 상세 내용 접기" if expanded else "경고 상세 내용 펼치기")

    def _reflow_content(self) -> None:
        while self.content_layout.count(): self.content_layout.takeAt(0)
        # ``content`` is not resized until after the first show event.  The
        # viewport/window width is therefore the reliable responsive input.
        width = max(1, self.scroll.viewport().width(), self.width() - 40)
        wide = width >= 1160
        # A single active card must not absorb an entire high-resolution
        # viewport.  Narrow layouts still grow naturally for wrapped cards.
        compact_maximum = 220 if wide else 16_777_215
        self.task_panel.setMaximumHeight(compact_maximum)
        self.queue_panel.setMaximumHeight(compact_maximum)
        self.content_layout.addWidget(self.pm_card, 0, 0, 1, 3); self.content_layout.addWidget(self.flow_panel, 1, 0, 1, 3)
        if wide:
            self.content_layout.addWidget(self.task_panel, 2, 0, 1, 2); self.content_layout.addWidget(self.queue_panel, 2, 2); self.content_layout.addWidget(self.event_panel, 3, 0, 1, 3); self.content_layout.addWidget(self.warning_box, 4, 0, 1, 3)
        elif width >= 760:
            self.content_layout.addWidget(self.task_panel, 2, 0, 1, 2); self.content_layout.addWidget(self.queue_panel, 3, 0); self.content_layout.addWidget(self.event_panel, 3, 1); self.content_layout.addWidget(self.warning_box, 4, 0, 1, 2)
        else:
            for row, widget in enumerate((self.task_panel, self.queue_panel, self.event_panel, self.warning_box), 2): self.content_layout.addWidget(widget, row, 0, 1, 3)
        for column in range(3): self.content_layout.setColumnStretch(column, 1)
        self._reflow_task_cards()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event); self._reflow_content()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape: self.close(); event.accept(); return
        super().keyPressEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._refresh_timer.stop(); super().closeEvent(event)


def render_dashboard_png(snapshot: MonitoringSnapshot, path: Path, *, size: tuple[int, int] = (1280, 720)) -> Path:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([]); configure_application_font(app)
    window = OperationsDashboard(lambda: snapshot, refresh_interval_ms=None); window.resize(*size); window.show(); app.processEvents()
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(target), "PNG"): raise RuntimeError(f"could not save dashboard screenshot: {target}")
    window.close(); window.deleteLater(); app.processEvents(); return target


_DASHBOARD_QSS = """
QWidget#dashboardRoot, QWidget#dashboardContent { background:#111315; color:#F2F4F7; font-size:16px; }
QFrame#pmCard, QFrame#panel, QFrame#taskCard { background:#191C20; border:1px solid #30363D; border-radius:10px; }
QFrame#pmCard { border:1px solid #F59E0B; background:#24201A; }
QFrame#flowPanel { background:#22262B; border-radius:8px; color:#F2F4F7; }
QFrame#warningBox { background:#24201A; border:1px solid #F59E0B; border-radius:8px; }
QLabel#pageTitle { color:#F2F4F7; font-size:28px; font-weight:700; }
QLabel#pageSubtitle, QLabel#refreshStatus { color:#B7BDC7; font-size:16px; }
QLabel#lastRefreshed, QLabel#cardMeta, QLabel#cardSummary { color:#B7BDC7; font-size:14px; }
QLabel#pmHeading { color:#F2F4F7; font-size:21px; font-weight:650; }
QLabel#pmDecision, QLabel#pmAssignment, QLabel#pmNext { color:#F2F4F7; font-size:16px; }
QLabel#sectionTitle { color:#F2F4F7; font-size:21px; font-weight:650; }
QLabel#cardTitle { color:#F2F4F7; font-size:16px; font-weight:650; }
QLabel#flowText, QLabel#queueText, QLabel#warningSummary { color:#F2F4F7; font-size:16px; }
QLabel#stateBadge { border-radius:7px; padding:4px 8px; font-size:14px; font-weight:650; }
QLabel#stateBadge[tone="active"] { background:#153D2A; color:#85E6AD; }
QLabel#stateBadge[tone="review"] { background:#4A3315; color:#FFB454; }
QLabel#stateBadge[tone="error"] { background:#4A2023; color:#FF9C9C; }
QLabel#stateBadge[tone="neutral"], QLabel#stateBadge[tone="unknown"] { background:#2B3138; color:#B7BDC7; }
QLabel#warningText { color:#FFCC7A; font-size:14px; }
QPushButton#refreshButton, QToolButton#warningToggle { background:#22262B; color:#F2F4F7; border:1px solid #F59E0B; border-radius:6px; padding:7px 12px; font-weight:650; }
QPushButton#refreshButton:hover, QToolButton#warningToggle:hover { background:#3A2B18; }
QPushButton#refreshButton:focus, QToolButton#warningToggle:focus, QListWidget#eventList:focus { outline:0; border:2px solid #FFB454; }
QListWidget#eventList { background:#191C20; color:#F2F4F7; border:1px solid #30363D; border-radius:6px; padding:3px; font-size:16px; }
QListWidget#eventList::item { padding:7px 5px; border-bottom:1px solid #30363D; }
QListWidget#eventList::item:selected { background:#3A2B18; color:#F2F4F7; }
QScrollArea#dashboardScroll { border:0; background:transparent; }
"""

__all__ = ["OperationsDashboard", "RoleCard", "StateBadge", "render_dashboard_png"]

if __name__ == "__main__":
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([]); configure_application_font(app)
    window = OperationsDashboard(repository_root=Path.cwd()); window.show(); raise SystemExit(app.exec())
