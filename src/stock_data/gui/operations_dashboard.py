"""Read-only PySide6 operations dashboard for the Python workflow controller.

The widget intentionally receives only :class:`MonitoringSnapshot` values.  It
does not know how to claim Queue work, dispatch an agent, or change controller
state; the sole interactive control requests a fresh read-only snapshot.
"""
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
    RoleView,
    TaskView,
)


class SnapshotReader(Protocol):
    def snapshot(self) -> MonitoringSnapshot: ...


_STATE_META: dict[str, tuple[str, str, str]] = {
    "active": ("●", "작업 중", "active"),
    "working": ("●", "작업 중", "active"),
    "idle": ("○", "대기", "neutral"),
    "reviewing": ("◇", "검토 중", "warning"),
    "ready": ("▶", "준비", "neutral"),
    "review": ("◇", "검토", "warning"),
    "stalled": ("!", "중단 확인 필요", "warning"),
    "stopped": ("■", "종료", "neutral"),
    "blocked": ("!", "차단", "error"),
    "failed": ("!", "실패", "error"),
    "stale": ("!", "오래됨", "warning"),
    "unknown": ("?", "알 수 없음", "unknown"),
}

_WARNING_TONE = {"error": "error", "warning": "warning"}
_EVENT_KIND_LABELS = {
    "TASK_TRANSITION": "작업 상태 전환",
    "REVIEW_RESULT": "검토 결과",
    "REWORK_REQUESTED": "재작업 요청",
    "ESCALATION": "에스컬레이션",
    "SESSION_STARTED": "세션 시작",
    "QUEUE_SNAPSHOT": "Queue 스냅샷",
}
_EVENT_SOURCE_LABELS = {"PM": "PM", "WORKER": "Worker", "REVIEWER": "Reviewer", "LEAD": "Lead", "QUEUE": "Queue", "SYSTEM": "시스템"}
_DEFAULT_REFRESH_INTERVAL_MS = 5_000
_MAX_REFRESH_INTERVAL_MS = 60 * 60 * 1_000


def _state_meta(value: str | None) -> tuple[str, str, str]:
    return _STATE_META.get(str(value or "unknown").lower(), ("?", str(value or "알 수 없음"), "unknown"))


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "확인되지 않음"
    local = value.astimezone()
    return local.strftime("%H:%M:%S")


def _age_text(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "기준 시각 없음"
    seconds = max(0, int((now - value).total_seconds()))
    if seconds < 60:
        return f"{seconds}초 전"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    return f"{seconds // 3600}시간 전"


def _queue_count(snapshot: MonitoringSnapshot, state: str) -> int:
    return snapshot.queue.count(state) if snapshot.queue is not None else 0


def _role_label(role: RoleView) -> str:
    return {"project_manager": "PM", "domain_lead": "Lead", "worker": "Worker", "reviewer": "Reviewer"}.get(role.role_kind, role.role_kind)


class StateBadge(QtWidgets.QLabel):
    """Colour is decorative only: the icon and Korean status text are visible."""

    def __init__(self, state: str | None, parent: QtWidgets.QWidget | None = None) -> None:
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


class RoleCard(QtWidgets.QFrame):
    def __init__(self, role: RoleView, *, workers: int, reviewers: int, now: datetime, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("leadCard")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setMinimumWidth(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.role_key = role.role_key
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)
        top = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(f"{_role_label(role)} · {role.role_key}")
        self.title.setObjectName("cardTitle")
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.badge = StateBadge(role.state)
        top.addWidget(self.title, 1)
        top.addWidget(self.badge)
        layout.addLayout(top)
        task = role.active_task_id or "진행 중인 작업 없음"
        self.task = QtWidgets.QLabel(f"작업: {task}")
        self.task.setObjectName("cardTask")
        self.task.setWordWrap(True)
        self.task.setMinimumWidth(0)
        self.task.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.task.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.task)
        membership = (
            "Queue 검토 담당"
            if role.role_kind == "reviewer"
            else f"Worker {workers}명 · Reviewer {reviewers}명"
        )
        self.counts = QtWidgets.QLabel(membership)
        self.counts.setObjectName("cardMeta")
        self.counts.setMinimumWidth(0)
        self.counts.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(self.counts)
        fresh_icon = "●" if role.fresh else "!"
        freshness = "정상" if role.fresh else "오래됨"
        self.freshness = QtWidgets.QLabel(f"{fresh_icon} 활동 {_age_text(role.heartbeat_at, now)} · {freshness}")
        self.freshness.setObjectName("cardMeta")
        self.freshness.setMinimumWidth(0)
        self.freshness.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(self.freshness)
        self.setAccessibleName(f"{self.title.text()}, {self.badge.text()}, {self.task.text()}, {self.counts.text()}, {self.freshness.text()}")


class OperationsDashboard(QtWidgets.QMainWindow):
    """A self-contained, read-only view over ``MonitoringSnapshot``."""

    def __init__(
        self,
        snapshot_provider: Callable[[], MonitoringSnapshot] | SnapshotReader | None = None,
        *,
        repository_root: Path | None = None,
        refresh_interval_ms: int | None = _DEFAULT_REFRESH_INTERVAL_MS,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        root = Path(repository_root or Path.cwd())
        if snapshot_provider is None:
            snapshot_provider = MonitoringSnapshotAdapter(root)
        self._provider = snapshot_provider
        self._snapshot: MonitoringSnapshot | None = None
        self._lead_cards: list[RoleCard] = []
        self._refresh_in_progress = False
        if refresh_interval_ms is not None and (
            isinstance(refresh_interval_ms, bool)
            or refresh_interval_ms <= 0
            or refresh_interval_ms > _MAX_REFRESH_INTERVAL_MS
        ):
            raise ValueError("refresh_interval_ms must be between 1 and 3600000, or None")
        self.setWindowTitle("Python PM 관제 화면")
        self.setMinimumSize(960, 600)
        self.resize(1280, 720)
        self._build_ui()
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(False)
        self._refresh_timer.setTimerType(QtCore.Qt.CoarseTimer)
        self._refresh_timer.timeout.connect(self.refresh_snapshot)
        if refresh_interval_ms is not None:
            self._refresh_timer.start(refresh_interval_ms)
        self.refresh_snapshot()

    @property
    def snapshot(self) -> MonitoringSnapshot | None:
        return self._snapshot

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        central.setObjectName("dashboardRoot")
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        heading = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("Python PM 관제")
        title.setObjectName("pageTitle")
        subtitle = QtWidgets.QLabel("읽기 전용 · Python PM 정식 실행 상태와 Queue를 하나의 스냅샷으로 표시합니다")
        subtitle.setObjectName("pageSubtitle")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)
        self.refresh_button = QtWidgets.QPushButton("새로 고침")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setMinimumHeight(36)
        self.refresh_button.setToolTip("상태를 읽기 전용으로 다시 불러옵니다")
        self.refresh_button.clicked.connect(self.refresh_snapshot)
        header.addWidget(self.refresh_button)
        outer.addLayout(header)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setObjectName("dashboardScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.content = QtWidgets.QWidget()
        self.content.setObjectName("dashboardContent")
        self.content_layout = QtWidgets.QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(12)
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll, 1)

        self.pm_card = QtWidgets.QFrame()
        self.pm_card.setObjectName("pmCard")
        pm_layout = QtWidgets.QVBoxLayout(self.pm_card)
        pm_layout.setContentsMargins(16, 14, 16, 14)
        pm_layout.setSpacing(7)
        pm_top = QtWidgets.QHBoxLayout()
        self.pm_heading = QtWidgets.QLabel("PM")
        self.pm_heading.setObjectName("pmHeading")
        self.pm_badge = StateBadge("unknown")
        pm_top.addWidget(self.pm_heading, 1)
        pm_top.addWidget(self.pm_badge)
        pm_layout.addLayout(pm_top)
        self.pm_activity = QtWidgets.QLabel("상태를 불러오는 중입니다")
        self.pm_activity.setObjectName("pmActivity")
        self.pm_activity.setWordWrap(True)
        pm_layout.addWidget(self.pm_activity)
        self.pm_freshness = QtWidgets.QLabel("기준 시각 확인 중")
        self.pm_freshness.setObjectName("cardMeta")
        pm_layout.addWidget(self.pm_freshness)
        self.content_layout.addWidget(self.pm_card)

        self.warning_box = QtWidgets.QFrame()
        self.warning_box.setObjectName("warningBox")
        self.warning_layout = QtWidgets.QVBoxLayout(self.warning_box)
        self.warning_layout.setContentsMargins(14, 10, 14, 10)
        self.warning_layout.setSpacing(5)
        self.content_layout.addWidget(self.warning_box)

        self.lead_section = QtWidgets.QWidget()
        lead_outer = QtWidgets.QVBoxLayout(self.lead_section)
        lead_outer.setContentsMargins(0, 0, 0, 0)
        lead_outer.setSpacing(7)
        lead_title = QtWidgets.QLabel("Lead 및 Reviewer 실행 상태")
        lead_title.setObjectName("sectionTitle")
        lead_outer.addWidget(lead_title)
        self.lead_grid_host = QtWidgets.QWidget()
        self.lead_grid_host.installEventFilter(self)
        self.lead_grid = QtWidgets.QGridLayout(self.lead_grid_host)
        self.lead_grid.setContentsMargins(0, 0, 0, 0)
        self.lead_grid.setHorizontalSpacing(10)
        self.lead_grid.setVerticalSpacing(10)
        lead_outer.addWidget(self.lead_grid_host)
        self.content_layout.addWidget(self.lead_section)

        lower = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        lower.setObjectName("dashboardLower")
        lower.setMinimumWidth(0)
        lower.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lower.setChildrenCollapsible(False)
        self.event_panel = self._make_panel("최근 이벤트")
        self.event_list = QtWidgets.QListWidget()
        self.event_list.setObjectName("eventList")
        self.event_list.setAlternatingRowColors(True)
        self.event_list.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.event_list.setFocusPolicy(QtCore.Qt.NoFocus)
        self.event_list.setWordWrap(True)
        self.event_list.setTextElideMode(QtCore.Qt.ElideNone)
        self.event_list.setResizeMode(QtWidgets.QListView.Adjust)
        self.event_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.event_list.setMinimumWidth(0)
        self.event_list.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.event_panel.layout().addWidget(self.event_list)  # type: ignore[union-attr]
        self.event_panel.setMinimumWidth(0)
        self.event_panel.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lower.addWidget(self.event_panel)
        self.queue_panel = self._make_panel("Queue 및 소스 신선도")
        self.queue_text = QtWidgets.QLabel()
        self.queue_text.setObjectName("queueText")
        self.queue_text.setWordWrap(True)
        self.queue_text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.queue_text.setMinimumWidth(0)
        self.queue_text.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.queue_panel.layout().addWidget(self.queue_text)  # type: ignore[union-attr]
        self.queue_panel.setMinimumWidth(0)
        self.queue_panel.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lower.addWidget(self.queue_panel)
        lower.setStretchFactor(0, 3)
        lower.setStretchFactor(1, 2)
        self.content_layout.addWidget(lower)
        self.content_layout.addStretch(1)

        self.setStyleSheet(_DASHBOARD_QSS)
        self.setTabOrder(self.refresh_button, self.scroll)

    @staticmethod
    def _make_panel(title: str) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame()
        panel.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        label = QtWidgets.QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        return panel

    def _read_snapshot(self) -> MonitoringSnapshot:
        provider = self._provider
        if callable(provider):
            return provider()
        return provider.snapshot()

    @QtCore.Slot()
    def refresh_snapshot(self) -> None:
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("읽는 중…")
        try:
            try:
                snapshot = self._read_snapshot()
            except Exception as error:  # provider failure becomes a visible, non-mutating state
                now = datetime.now(timezone.utc)
                snapshot = MonitoringSnapshot(
                    observed_at=now,
                    warnings=(MonitoringWarning("SNAPSHOT_UNREADABLE", f"상태 스냅샷을 읽지 못했습니다: {type(error).__name__}", "error"),),
                )
            self.render_snapshot(snapshot)
            self.refresh_button.setAccessibleDescription(f"마지막 읽기 {_format_time(snapshot.observed_at)}")
        finally:
            self.refresh_button.setText("새로 고침")
            self.refresh_button.setEnabled(True)
            self._refresh_in_progress = False

    def render_snapshot(self, snapshot: MonitoringSnapshot) -> None:
        self._snapshot = snapshot
        now = snapshot.observed_at
        # The adapter normally returns one PM, but direct read-only snapshots
        # can contain audit data.  Never let tuple insertion order choose the
        # writer shown to an operator: prefer a live PM, then the newest real
        # generation and heartbeat deterministically.
        pm = max(
            snapshot.pm,
            key=lambda role: (
                role.active,
                role.generation,
                role.heartbeat_at or datetime.min.replace(tzinfo=timezone.utc),
                role.role_key,
            ),
            default=None,
        )
        if pm is None:
            self.pm_heading.setText("PM · 연결 정보 없음")
            self.pm_badge.set_state("unknown")
            self.pm_activity.setText("? 활성 PM 정보를 찾지 못했습니다. 역할 소스와 서비스 상태를 확인하세요.")
            self.pm_freshness.setText(f"기준 시각 {_format_time(now)} · PM 활동 시간 없음")
        else:
            self.pm_heading.setText(f"PM · {pm.role_key}")
            self.pm_badge.set_state(pm.state)
            task = pm.active_task_id or "현재 배정된 작업 없음"
            source = "Queue 기준" if pm.role_key == "canonical-queue-pm" else f"writer 세대 {pm.generation}"
            heartbeat_label = "Queue 갱신" if pm.role_key == "canonical-queue-pm" else "heartbeat"
            self.pm_activity.setText(f"{_state_meta(pm.state)[0]} {task} · {source}")
            self.pm_freshness.setText(
                f"활동 {_age_text(pm.heartbeat_at, now)} · {heartbeat_label} {_format_time(pm.heartbeat_at)}"
            )
        self.pm_card.setAccessibleName(f"{self.pm_heading.text()}, {self.pm_badge.text()}, {self.pm_activity.text()}, {self.pm_freshness.text()}")
        self._render_warnings(snapshot.warnings)
        self._render_leads(snapshot)
        self._render_events(snapshot.events)
        self._render_queue(snapshot)

    def _render_warnings(self, warnings: tuple[MonitoringWarning, ...]) -> None:
        while self.warning_layout.count():
            item = self.warning_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.warning_box.setVisible(bool(warnings))
        for warning in warnings:
            tone = _WARNING_TONE.get(warning.severity, "warning")
            icon = "!" if tone in {"error", "warning"} else "?"
            label = QtWidgets.QLabel(f"{icon} {warning.message}")
            label.setObjectName("warningText")
            label.setProperty("tone", tone)
            label.setWordWrap(True)
            label.setAccessibleName(f"경고 {warning.code}: {warning.message}")
            self.warning_layout.addWidget(label)

    def _render_leads(self, snapshot: MonitoringSnapshot) -> None:
        while self.lead_grid.count():
            item = self.lead_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        roles = [*snapshot.leads, *snapshot.reviewers]
        if not roles:
            source_unknown = any(
                warning.code in {"EXECUTION_SOURCE_MISSING", "EXECUTION_SOURCE_UNREADABLE"}
                for warning in snapshot.warnings
            )
            newest_pm = max(snapshot.pm, key=lambda item: item.generation, default=None)
            roles = [RoleView(
                "상태 확인 불가" if source_unknown else "활성 Lead 없음",
                "domain_lead",
                "unknown" if source_unknown else "idle",
                newest_pm.generation if newest_pm is not None else 0,
                newest_pm.heartbeat_at if newest_pm is not None else None,
                None,
                None,
                not source_unknown,
                False,
            )]
        self._lead_cards = []
        for role in roles:
            # An execution role is shown only under the Lead responsible for
            # the same active Queue task.  Idle Leads and unassigned roles are
            # deliberately not inferred into ownership counts.
            task_id = role.active_task_id
            workers = sum(
                1 for worker in snapshot.workers
                if worker.active and task_id is not None and worker.active_task_id == task_id
            )
            reviewers = sum(
                1 for reviewer in snapshot.reviewers
                if reviewer.active and task_id is not None and reviewer.active_task_id == task_id
            )
            self._lead_cards.append(
                RoleCard(role, workers=workers, reviewers=reviewers, now=snapshot.observed_at)
            )
        self._reflow_lead_cards()

    def _reflow_lead_cards(self) -> None:
        if not self._lead_cards:
            return
        width = max(1, self.lead_grid_host.width())
        columns = 3 if width >= 1080 else 2 if width >= 700 else 1
        while self.lead_grid.count():
            self.lead_grid.takeAt(0)
        for index, card in enumerate(self._lead_cards):
            self.lead_grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.lead_grid.setColumnStretch(column, 1)

    def _render_events(self, events: tuple[EventView, ...]) -> None:
        self.event_list.clear()
        if not events:
            item = QtWidgets.QListWidgetItem("? 이벤트 기록이 없습니다.")
            item.setToolTip("이벤트 소스가 비어 있거나 확인되지 않았습니다.")
            self.event_list.addItem(item)
            return
        for event in sorted(events, key=lambda row: row.occurred_at, reverse=True):
            task = event.task_id or "작업 없음"
            reason = f" · {event.reason_code}" if event.reason_code else ""
            source = _EVENT_SOURCE_LABELS.get(event.source, event.source)
            kind = _EVENT_KIND_LABELS.get(event.kind, event.kind)
            text = f"{_format_time(event.occurred_at)}  {source}  {kind}  {task}{reason}"
            item = QtWidgets.QListWidgetItem(text)
            item.setToolTip(f"발생 시각: {event.occurred_at.isoformat()}\nActor: {event.source}\n작업: {task}")
            self.event_list.addItem(item)

    def _render_queue(self, snapshot: MonitoringSnapshot) -> None:
        queue = snapshot.queue
        if queue is None:
            queue_text = "? Queue 상태를 확인할 수 없습니다.\nQueue 소스를 다시 읽어야 합니다."
        else:
            queue_text = " · ".join(
                f"{label} {queue.count(key)}" for key, label in (("ready", "Ready"), ("active", "Active"), ("review", "Review"), ("blocked", "Blocked"), ("done", "Done"))
            )
            queue_text += f"\n활성 작업: {', '.join(queue.active_task_ids) if queue.active_task_ids else '없음'}"
            if queue.current_tasks:
                current_lines = []
                for task in queue.current_tasks:
                    owner = task.lead_owner
                    reviewer = f" · Reviewer {task.reviewer}" if task.reviewer else ""
                    current_lines.append(
                        f"{task.task_id} · {task.state} · Lead {owner}{reviewer} · 갱신 {_format_time(task.updated_at)}"
                    )
                queue_text += "\n현재 소유권:\n" + "\n".join(current_lines)
            queue_text += f"\n압축 보관: {queue.compacted_count}"
        fresh_lines = []
        for source, moment in snapshot.source_freshness.items():
            mark = "●" if moment is not None else "?"
            fresh_lines.append(f"{mark} {source}: {_format_time(moment)} · {_age_text(moment, snapshot.observed_at)}")
        if not fresh_lines:
            fresh_lines.append("? 소스 신선도 정보 없음")
        self.queue_text.setText(queue_text + "\n\n" + "\n".join(fresh_lines))
        self.queue_panel.setAccessibleName(self.queue_text.text().replace("\n", " · "))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reflow_lead_cards()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.lead_grid_host and event.type() == QtCore.QEvent.Resize:
            self._reflow_lead_cards()
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Stop polling before child widgets and providers are released."""
        if hasattr(self, "_refresh_timer"):
            self._refresh_timer.stop()
        super().closeEvent(event)


def render_dashboard_png(
    snapshot: MonitoringSnapshot,
    path: Path,
    *,
    size: tuple[int, int] = (1280, 720),
) -> Path:
    """Render a deterministic offscreen evidence image for automated/manual QA."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    configure_application_font(app)
    window = OperationsDashboard(lambda: snapshot)
    window.resize(*size)
    window.show()
    app.processEvents()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image = window.grab().toImage()
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"could not save dashboard screenshot: {target}")
    window.close()
    window.deleteLater()
    app.processEvents()
    return target


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    configure_application_font(app)
    window = OperationsDashboard(repository_root=Path.cwd())
    window.show()
    return app.exec()


_DASHBOARD_QSS = """
QWidget#dashboardRoot, QWidget#dashboardContent { background:#f4f7fb; color:#132238; font-size:10pt; }
QFrame#pmCard, QFrame#panel, QFrame#leadCard { background:#ffffff; border:1px solid #d7e0eb; border-radius:10px; }
QFrame#pmCard { border:2px solid #8db2d7; background:#f9fcff; }
QFrame#warningBox { background:#fff8e8; border:1px solid #e8c56d; border-radius:8px; }
QLabel#pageTitle { color:#10233d; font-size:20pt; font-weight:800; }
QLabel#pageSubtitle, QLabel#cardMeta { color:#5e7188; font-size:9.5pt; }
QLabel#pmHeading { color:#17375e; font-size:14pt; font-weight:800; }
QLabel#pmActivity { color:#203a57; font-size:12pt; font-weight:650; }
QLabel#sectionTitle { color:#17375e; font-size:12pt; font-weight:750; }
QLabel#cardTitle { color:#31506f; font-size:11pt; font-weight:750; }
QLabel#cardTask { color:#203a57; font-size:10.5pt; font-weight:650; }
QLabel#stateBadge { border-radius:7px; padding:3px 7px; font-size:9.5pt; font-weight:750; }
QLabel#stateBadge[tone="active"] { background:#e7f5ee; color:#176b49; }
QLabel#stateBadge[tone="neutral"] { background:#edf3f8; color:#42617f; }
QLabel#stateBadge[tone="warning"] { background:#fff3d6; color:#8a5b12; }
QLabel#stateBadge[tone="error"] { background:#fbeceb; color:#a33f38; }
QLabel#stateBadge[tone="unknown"] { background:#f0f2f5; color:#5e6876; }
QLabel#warningText { font-size:10pt; font-weight:650; }
QLabel#warningText[tone="warning"] { color:#805b16; }
QLabel#warningText[tone="error"] { color:#9c322d; }
QPushButton#refreshButton { background:#ffffff; border:1px solid #7da5ce; border-radius:6px; padding:7px 12px; color:#174f88; font-weight:700; }
QPushButton#refreshButton:hover { background:#edf4fb; }
QPushButton#refreshButton:focus { border:2px solid #2f6fb2; outline:0; }
QListWidget#eventList { background:#ffffff; border:1px solid #d7e0eb; border-radius:6px; alternate-background-color:#f7f9fc; padding:3px; }
QListWidget#eventList::item { padding:7px 5px; border-bottom:1px solid #edf1f6; }
QScrollArea#dashboardScroll { border:0; background:transparent; }
"""


__all__ = ["OperationsDashboard", "RoleCard", "StateBadge", "render_dashboard_png"]


if __name__ == "__main__":
    raise SystemExit(main())
