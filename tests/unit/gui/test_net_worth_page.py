from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from stock_data.gui.main_window import (
    MainWindow,
    NetWorthPage,
    NetWorthSnapshotDialog,
)
from stock_data.gui.net_worth_service import (
    LocalNetWorthHistoryStore,
    NetWorthHistoryRecord,
    NetWorthPersistenceError,
    NetWorthTimelineDeltaState,
    NetWorthTimelineDisplayState,
    NetWorthValidationError,
    NetWorthView,
    calculate_net_worth,
    parse_snapshot,
)
from stock_data.orchestration.account_privacy import MASKED_VALUE


def _asset(
    record_id: str,
    claim_id: str,
    asset_class: str,
    value: int,
    *,
    holder: str = "SELF",
    owner: str = "SELF",
    uncertainty: str = "EXACT",
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "economic_claim_id": claim_id,
        "asset_class": asset_class,
        "gross_value_krw": value,
        "economic_value_krw": value,
        "registered_holder_role": holder,
        "economic_owner_role": owner,
        "valuation_date": "2026-08-20",
        "valuation_method": "USER_DECLARED",
        "valuation_source": "USER_LOCAL",
        "valuation_status": "CURRENT",
        "uncertainty": uncertainty,
    }


def _liability(
    record_id: str,
    claim_id: str,
    liability_class: str,
    value: int,
    *,
    unused: int = 0,
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "economic_claim_id": claim_id,
        "liability_class": liability_class,
        "gross_principal_krw": value,
        "economic_principal_krw": value,
        "unused_limit_krw": unused,
        "registered_holder_role": "SELF",
        "economic_owner_role": "SELF",
        "valuation_date": "2026-08-20",
        "valuation_method": "STATEMENT_VALUE",
        "valuation_source": "OFFICIAL_STATEMENT",
        "valuation_status": "CURRENT",
        "uncertainty": "EXACT",
    }


def _payload() -> dict[str, object]:
    return {
        "schema_version": "local-net-worth-snapshot/v1",
        "snapshot_id": "synthetic-snapshot-a",
        "as_of_date": "2026-08-20",
        "recorded_at_utc": "2026-08-20T00:00:00+00:00",
        "base_currency": "KRW",
        "assets": [
            _asset("synthetic-cash", "synthetic-claim-cash", "CASH", 120_000),
            _asset(
                "synthetic-investment", "synthetic-claim-investment",
                "INVESTMENT", 180_000, holder="FAMILY", owner="SELF",
                uncertainty="LOW",
            ),
            _asset("synthetic-estate", "synthetic-claim-estate", "REAL_ESTATE", 400_000),
            _asset("synthetic-jeonse", "synthetic-claim-jeonse", "JEONSE_DEPOSIT", 500_000),
            _asset("synthetic-receivable", "synthetic-claim-receivable", "OTHER_RECEIVABLE", 50_000),
        ],
        "liabilities": [
            _liability("synthetic-mortgage", "synthetic-claim-mortgage", "MORTGAGE", 125_000),
            _liability("synthetic-jeonse-loan", "synthetic-claim-jeonse-loan", "JEONSE_LOAN", 200_000),
            _liability(
                "synthetic-overdraft", "synthetic-claim-overdraft",
                "DRAWN_OVERDRAFT", 30_000, unused=70_000,
            ),
            _liability("synthetic-other-debt", "synthetic-claim-other-debt", "OTHER_DEBT", 20_000),
        ],
    }


def _view(payload: dict[str, object] | None = None) -> NetWorthView:
    snapshot = parse_snapshot(payload or _payload())
    return NetWorthView(snapshot, calculate_net_worth(snapshot))


def _history_record(
    payload: dict[str, object],
    *,
    saved_at: datetime,
    record_digest: str,
) -> NetWorthHistoryRecord:
    return NetWorthHistoryRecord(
        saved_at_utc=saved_at,
        snapshot_digest="s" * 64,
        previous_record_digest=None,
        record_digest=record_digest,
        view=_view(payload),
    )


def _dated_payload(
    as_of: str,
    snapshot_id: str,
    *,
    asset_delta: int = 0,
) -> dict[str, object]:
    payload = deepcopy(_payload())
    payload["snapshot_id"] = snapshot_id
    payload["as_of_date"] = as_of
    payload["recorded_at_utc"] = f"{as_of}T00:00:00+00:00"
    for entry in (*payload["assets"], *payload["liabilities"]):
        entry["valuation_date"] = as_of
    payload["assets"][0]["gross_value_krw"] += asset_delta
    payload["assets"][0]["economic_value_krw"] += asset_delta
    return payload


def _text(page: NetWorthPage) -> str:
    return "\n".join(label.text() for label in page.findChildren(QtWidgets.QLabel))


def _widget_state_strings(root: QtWidgets.QWidget) -> tuple[str, ...]:
    values: list[str] = []
    for widget in (root, *root.findChildren(QtWidgets.QWidget)):
        values.extend((
            widget.toolTip(), widget.statusTip(), widget.whatsThis(),
            widget.accessibleName(), widget.accessibleDescription(),
        ))
        if isinstance(widget, QtWidgets.QLabel):
            values.append(widget.text())
        if isinstance(widget, QtWidgets.QAbstractButton):
            values.append(widget.text())
        if isinstance(widget, QtWidgets.QComboBox):
            values.extend(widget.itemText(index) for index in range(widget.count()))
    return tuple(value for value in values if value)


def _select(combo: QtWidgets.QComboBox, value: str) -> None:
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def _first_dialog_payload() -> dict[str, object]:
    dialog = NetWorthSnapshotDialog()
    dialog.date_edit.setDate(QtCore.QDate(2026, 8, 20))
    asset = dialog.add_asset()
    asset.gross.setValue(120_000)
    asset.economic.setValue(120_000)
    dialog.liabilities_empty_confirm.setChecked(True)
    payload = dialog.snapshot_payload()
    dialog.close()
    return payload


def test_net_worth_page_shows_separate_totals_classes_and_attribution() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = NetWorthPage()
    page.render(_view())
    app.processEvents()

    assert page.headline_labels["liquid"].text() == "300,000 KRW"
    assert page.headline_labels["assets"].text() == "1,250,000 KRW"
    assert page.headline_labels["liabilities"].text() == "375,000 KRW"
    assert page.headline_labels["net_worth"].text() == "875,000 KRW"
    assert page.headline_labels["unused_credit"].text() == "70,000 KRW"
    text = _text(page)
    assert "전세 보증금" in text and "전세 대출" in text
    assert "사용한 마이너스통장" in text and "미사용 한도 70,000 KRW · 부채 제외" in text
    assert "명의 역할 가족 · 경제 귀속 본인" in text
    assert "USER_LOCAL" in text and "불확실성 LOW" in text
    assert "synthetic-claim" not in text and "synthetic-snapshot" not in text


def test_net_worth_page_masks_values_and_never_reveals_claim_identity() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = NetWorthPage()
    page.render(_view())
    page.hide_values.setChecked(True)
    app.processEvents()
    text = _text(page)

    assert MASKED_VALUE in text
    assert not any(value in text for value in ("1,250,000", "875,000", "500,000", "70,000"))
    assert "synthetic-claim" not in text and "synthetic-snapshot" not in text
    assert "명의 역할 가족 · 경제 귀속 본인" in text


def test_net_worth_timeline_uses_latest_revision_keeps_gaps_and_syncs_selection() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    first = _history_record(
        _dated_payload("2026-08-20", "timeline-first"),
        saved_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
        record_digest="a" * 64,
    )
    revised = _history_record(
        _dated_payload("2026-08-20", "timeline-revised", asset_delta=10_000),
        saved_at=datetime(2026, 8, 20, 2, tzinfo=timezone.utc),
        record_digest="b" * 64,
    )
    gap_payload = _dated_payload("2026-08-21", "timeline-gap")
    gap_payload["assets"][0]["valuation_status"] = "STALE"
    gap = _history_record(
        gap_payload,
        saved_at=datetime(2026, 8, 21, 1, tzinfo=timezone.utc),
        record_digest="c" * 64,
    )
    latest = _history_record(
        _dated_payload("2026-08-22", "timeline-latest", asset_delta=50_000),
        saved_at=datetime(2026, 8, 22, 1, tzinfo=timezone.utc),
        record_digest="d" * 64,
    )
    page = NetWorthPage()

    page.set_history((latest, first, gap, revised))
    app.processEvents()

    assert [page.date_selector.itemData(index) for index in range(3)] == [
        date(2026, 8, 20),
        date(2026, 8, 21),
        date(2026, 8, 22),
    ]
    assert page.selected_date == date(2026, 8, 22)
    assert page._timeline.points[0].net_worth_krw == revised.view.totals.net_worth_krw
    assert page._timeline.points[1].display_state is NetWorthTimelineDisplayState.GAP
    assert page._timeline.points[2].delta_state is NetWorthTimelineDeltaState.AVAILABLE
    assert "2026-08-20 대비 +40,000 KRW" in page.timeline_delta.text()
    series = page.timeline_chart.chart().series()
    assert len(series) == 2
    assert all(item.pointsVisible() for item in series)
    assert [[point.x() for point in item.points()] for item in series] == [[0.0], [2.0]]
    value_axes = page.timeline_chart.chart().axes(QtCore.Qt.Vertical)
    assert len(value_axes) == 1
    assert value_axes[0].min() < 885_000 < 925_000 < value_axes[0].max()

    page.date_selector.setCurrentIndex(1)
    app.processEvents()
    assert page.selected_date == date(2026, 8, 21)
    assert "GAP" in page.summary.text()
    assert page.asset_rows.count() == 0 and page.liability_rows.count() == 0
    assert all(label.text() == "N/A" for label in page.headline_labels.values())
    assert page.timeline_chart.isHidden()
    assert page.timeline_chart.chart().series() == []
    assert page.timeline_chart.chart().axes() == []
    state = "\n".join(_widget_state_strings(page))
    assert not any(value in state for value in ("885,000", "925,000", "40,000"))

    page.date_selector.setCurrentIndex(0)
    page.set_history((gap, revised, latest, first))
    app.processEvents()
    assert page.selected_date == date(2026, 8, 20)
    assert page.headline_labels["net_worth"].text() == "885,000 KRW"
    page.close()
    app.processEvents()


def test_net_worth_timeline_privacy_empty_single_and_currency_gap_are_numeric_safe() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    first = _history_record(
        _dated_payload("2026-08-20", "privacy-first"),
        saved_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
        record_digest="a" * 64,
    )
    latest = _history_record(
        _dated_payload("2026-08-22", "privacy-latest", asset_delta=50_000),
        saved_at=datetime(2026, 8, 22, 1, tzinfo=timezone.utc),
        record_digest="c" * 64,
    )
    mismatch = _history_record(
        _dated_payload("2026-08-21", "privacy-mismatch", asset_delta=20_000),
        saved_at=datetime(2026, 8, 21, 1, tzinfo=timezone.utc),
        record_digest="b" * 64,
    )
    mismatch = replace(
        mismatch,
        view=replace(
            mismatch.view,
            snapshot=replace(mismatch.view.snapshot, base_currency="USD"),
        ),
    )
    page = NetWorthPage()

    page.set_history((latest, mismatch, first))
    page.date_selector.setCurrentIndex(1)
    app.processEvents()
    assert page._selected_timeline_point is not None
    assert page._selected_timeline_point.display_reason == "CURRENCY_MISMATCH"
    assert "CURRENCY_MISMATCH" in page.timeline_delta.text()
    assert page.asset_rows.count() == 0 and page.liability_rows.count() == 0

    page.set_history((latest, first))
    page.hide_values.setChecked(True)
    app.processEvents()
    assert page.timeline_chart.isHidden()
    assert page.timeline_chart.chart().series() == []
    assert page.timeline_chart.chart().axes() == []
    private_state = "\n".join(_widget_state_strings(page))
    assert "금액 숨김" in page.timeline_delta.text()
    assert not any(character.isdigit() for character in page.timeline_dates.text())
    assert not any(value in private_state for value in ("875,000", "925,000", "50,000"))

    page.hide_values.setChecked(False)
    page.set_history((first,))
    app.processEvents()
    assert "NO_PREVIOUS_COMPLETE" in page.timeline_delta.text()
    assert not any(character.isdigit() for character in page.timeline_delta.text())

    page.set_history(())
    app.processEvents()
    assert page.timeline_panel.isHidden()
    assert page.timeline_chart.chart().series() == []
    assert page.timeline_chart.chart().axes() == []
    assert "숫자 표시 안 함" in page.summary.text()
    empty_state = "\n".join(_widget_state_strings(page))
    assert not any(value in empty_state for value in ("875,000", "925,000", "50,000"))
    page.close()
    app.processEvents()


def test_net_worth_timeline_scrubs_populated_state_before_unavailable_and_restores() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    first = _history_record(
        _dated_payload("2026-08-20", "scrub-first"),
        saved_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
        record_digest="a" * 64,
    )
    latest = _history_record(
        _dated_payload("2026-08-22", "scrub-latest", asset_delta=50_000),
        saved_at=datetime(2026, 8, 22, 1, tzinfo=timezone.utc),
        record_digest="b" * 64,
    )
    page = NetWorthPage()
    page.set_history((first, latest))
    app.processEvents()
    assert "+50,000 KRW" in page.timeline_delta.text()
    assert page.timeline_chart.chart().series()
    assert page.timeline_chart.chart().axes()
    marker = "PRIVATE_TIMELINE_MARKER_2152"
    page.timeline_panel.setToolTip(marker)
    page.timeline_dates.setAccessibleDescription(marker)
    page.timeline_delta.setStatusTip(marker)
    page.timeline_chart.setWhatsThis(marker)
    page.timeline_chart.chart().setTitle(marker)
    page.timeline_chart.chart().setToolTip(marker)
    page.timeline_chart.chart().legend().setToolTip(marker)

    page.render_unavailable("로컬 순자산 이력 검증 실패 · HISTORY_INVALID")
    app.processEvents()

    assert page.timeline_panel.isHidden() and page.timeline_chart.isHidden()
    assert page.timeline_dates.text() == "이력 없음"
    assert page.timeline_delta.text() == "이전 완전 스냅샷 비교 불가"
    assert page.timeline_chart.chart().series() == []
    assert page.timeline_chart.chart().axes() == []
    assert page.timeline_chart.chart().title() == ""
    assert page.timeline_chart.chart().toolTip() == ""
    assert page.timeline_chart.chart().legend().toolTip() == ""
    state = "\n".join(_widget_state_strings(page))
    assert marker not in state
    assert not any(value in state for value in ("875,000", "925,000", "50,000"))

    page.set_history((first, latest))
    app.processEvents()
    assert page.timeline_panel.isHidden() is False
    assert page.timeline_chart.isHidden() is False
    assert "+50,000 KRW" in page.timeline_delta.text()
    assert page.timeline_chart.chart().series()
    assert page.timeline_chart.chart().axes()

    invalid = replace(
        latest,
        view=replace(
            latest.view,
            totals=replace(
                latest.view.totals,
                net_worth_krw=latest.view.totals.net_worth_krw + 1,
            ),
        ),
    )
    page.set_history((first, invalid))
    app.processEvents()
    assert page._selected_timeline_point is not None
    assert page._selected_timeline_point.display_reason == "SNAPSHOT_INVALID"
    assert page.timeline_chart.isHidden()
    assert page.timeline_chart.chart().series() == []
    assert page.timeline_chart.chart().axes() == []
    invalid_state = "\n".join(_widget_state_strings(page))
    assert not any(value in invalid_state for value in ("875,000", "925,001", "50,001"))
    page.close()
    app.processEvents()


def test_net_worth_timeline_fails_chart_closed_for_unrepresentable_exact_amounts() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ordinary = _history_record(
        _dated_payload("2026-08-20", "chart-range-ordinary"),
        saved_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
        record_digest="a" * 64,
    )
    huge = _history_record(
        _dated_payload(
            "2026-08-22",
            "chart-range-huge",
            asset_delta=10**400,
        ),
        saved_at=datetime(2026, 8, 22, 1, tzinfo=timezone.utc),
        record_digest="b" * 64,
    )
    finite_but_inexact = _history_record(
        _dated_payload(
            "2026-08-21",
            "chart-range-inexact",
            asset_delta=2**53 + 1,
        ),
        saved_at=datetime(2026, 8, 21, 1, tzinfo=timezone.utc),
        record_digest="c" * 64,
    )
    page = NetWorthPage()

    page.set_history((huge,))
    app.processEvents()

    assert page.headline_labels["net_worth"].text() == (
        f"{huge.view.totals.net_worth_krw:,} KRW"
    )
    assert page.timeline_delta.text() == (
        "순자산 이력 차트 표시 불가 · CHART_VALUE_OUT_OF_RANGE"
    )
    assert not any(character.isdigit() for character in page.timeline_delta.text())
    assert page.timeline_chart.isHidden()
    assert page.timeline_chart.chart().series() == []
    assert page.timeline_chart.chart().axes() == []

    page.set_history((finite_but_inexact,))
    app.processEvents()
    assert page.timeline_delta.text() == (
        "순자산 이력 차트 표시 불가 · CHART_VALUE_OUT_OF_RANGE"
    )
    assert page.timeline_chart.isHidden()
    assert page.timeline_chart.chart().series() == []
    assert page.timeline_chart.chart().axes() == []

    page.set_history((ordinary, huge))
    app.processEvents()
    assert page.timeline_delta.text() == (
        "순자산 이력 차트 표시 불가 · CHART_VALUE_OUT_OF_RANGE"
    )
    assert page.timeline_chart.isHidden()
    assert page.timeline_chart.chart().series() == []
    assert page.timeline_chart.chart().axes() == []

    page.set_history((ordinary,))
    app.processEvents()
    assert page.timeline_chart.isHidden() is False
    assert page.timeline_chart.chart().series()
    assert page.timeline_chart.chart().axes()
    page.close()
    app.processEvents()


def test_net_worth_page_suppresses_stale_claim_and_affected_totals() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    payload = deepcopy(_payload())
    payload["assets"][2]["valuation_status"] = "STALE"
    page = NetWorthPage()
    page.render(_view(payload))
    app.processEvents()

    assert page.headline_labels["liquid"].text() == "300,000 KRW"
    assert page.headline_labels["assets"].text() == "N/A"
    assert page.headline_labels["liabilities"].text() == "375,000 KRW"
    assert page.headline_labels["net_worth"].text() == "N/A"
    estate_card = page.asset_rows.itemAt(2).widget()
    estate_text = "\n".join(
        label.text() for label in estate_card.findChildren(QtWidgets.QLabel)
    )
    assert "STALE" in estate_text and "400,000" not in estate_text
    assert "N/A" in estate_text


def test_net_worth_page_empty_and_corrupt_states_are_intentional_numeric_free() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = NetWorthPage()
    page.render_unavailable("로컬 순자산 이력 검증 실패 · HISTORY_INVALID")
    app.processEvents()

    assert page.empty_state.isVisible() is False or not page.empty_state.isHidden()
    assert page.headlines.isHidden() and page.breakdowns.isHidden()
    assert not page.remove_button.isEnabled()
    assert "숫자 표시 안 함" in page.summary.text()
    assert not any(character.isdigit() for character in page.summary.text())


def test_net_worth_unavailable_copy_is_stable_across_privacy_toggles() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = NetWorthPage()
    reason = "로컬 순자산 이력 검증 실패 · HISTORY_INVALID"
    page.render_unavailable(reason)
    expected = page.empty_detail.text()

    for hidden in (True, False, True, False):
        page.hide_values.setChecked(hidden)
        app.processEvents()
        assert page.empty_detail.text() == expected
        assert page._unavailable_reason == reason

    assert expected.count("과거 날짜 값을 대신 표시하거나 외부 공급자를 호출하지 않습니다.") == 1
    page.close()
    app.processEvents()


def test_net_worth_unavailable_scrubs_private_dynamic_widgets_and_metadata() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = NetWorthPage()
    view = _view()
    page.set_history((
        type("HistoryRecord", (), {"view": view})(),
    ))
    page.render(view)
    marker = "PRIVATE_NET_WORTH_MARKER_94D9"
    detached_asset = page.asset_rows.itemAt(0).widget()
    detached_liability = page.liability_rows.itemAt(0).widget()
    assert detached_asset is not None and detached_liability is not None
    page.setToolTip(marker)
    page.setAccessibleDescription(marker)
    page.date_selector.setToolTip(marker)
    page.headline_labels["net_worth"].setText(marker)
    page.headline_meta["assets"].setAccessibleName(marker)
    detached_asset.setToolTip(marker)
    detached_asset.findChildren(QtWidgets.QLabel)[0].setText(marker)
    detached_liability.setAccessibleDescription(marker)

    page.render_unavailable(marker)

    state = "\n".join(_widget_state_strings(page))
    detached_state = "\n".join(
        (*_widget_state_strings(detached_asset), *_widget_state_strings(detached_liability))
    )
    assert marker not in state and marker not in detached_state
    assert not any(value in state for value in (
        "875,000 KRW", "1,250,000 KRW", "USER_LOCAL", "2026-08-20",
    ))
    assert page.asset_rows.count() == 0
    assert page.liability_rows.count() == 0
    assert page.date_selector.count() == 0
    assert all(label.text() == "N/A" for label in page.headline_labels.values())
    assert all(meta.text() == "현재 표시 불가" for meta in page.headline_meta.values())
    assert page._history == () and page._view is None
    page.close()
    app.processEvents()


def test_main_window_registers_combined_account_workspace_and_loads_local_history(
    tmp_path: Path,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    history_root = tmp_path / "synthetic-history"
    LocalNetWorthHistoryStore(history_root).save_snapshot(_payload())
    missing_account = tmp_path / "missing-account.json"
    window = MainWindow(
        tmp_path,
        account_snapshot_path=missing_account,
        kb_account_snapshot_path=missing_account,
        family_account_snapshot_path=missing_account,
        toss_runtime_enabled=False,
        net_worth_history_root=history_root,
    )
    window._reload_net_worth()
    app.processEvents()

    labels = [window.tabs.tabText(index) for index in range(window.tabs.count())]
    assert labels[-2:] == ["계좌·순자산", "Backtest"]
    assert [
        window.account_workspace_tabs.tabText(index)
        for index in range(window.account_workspace_tabs.count())
    ] == ["계좌·보유", "순자산·증감"]
    window.tabs.setCurrentWidget(window.account_workspace_page)
    window.account_workspace_tabs.setCurrentWidget(window.net_worth_page)
    assert window.tabs.currentWidget() is window.account_workspace_page
    assert window.account_workspace_tabs.currentWidget() is window.net_worth_page
    assert window.net_worth_page.headline_labels["net_worth"].text() == "875,000 KRW"
    assert window.net_worth_page.selected_date.isoformat() == "2026-08-20"
    window.close()
    app.processEvents()


def test_net_worth_snapshot_dialog_has_only_controlled_identifier_free_inputs() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = NetWorthSnapshotDialog()
    dialog.date_edit.setDate(QtCore.QDate(2026, 8, 20))
    asset = dialog.add_asset()
    asset.gross.setValue(120_000)
    asset.economic.setValue(120_000)
    dialog.liabilities_empty_confirm.setChecked(True)

    payload = dialog.snapshot_payload()
    snapshot = parse_snapshot(payload)

    assert snapshot.assets[0].record_id.startswith("asset-")
    assert snapshot.assets[0].economic_claim_id.startswith("claim-")
    assert snapshot.snapshot_id.startswith("snapshot-")
    visible = "\n".join(_widget_state_strings(dialog))
    assert snapshot.snapshot_id not in visible
    assert snapshot.assets[0].record_id not in visible
    assert snapshot.assets[0].economic_claim_id not in visible
    assert not dialog.findChildren(QtWidgets.QTextEdit)
    assert not dialog.findChildren(QtWidgets.QPlainTextEdit)
    assert all(not combo.isEditable() for combo in dialog.findChildren(QtWidgets.QComboBox))
    assert all(
        isinstance(line_edit.parentWidget(), QtWidgets.QAbstractSpinBox)
        for line_edit in dialog.findChildren(QtWidgets.QLineEdit)
    )
    dialog.close()
    app.processEvents()


def test_net_worth_dialog_requires_explicit_empty_sections_and_normalizes_missing() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    empty = NetWorthSnapshotDialog()
    with pytest.raises(NetWorthValidationError):
        empty.snapshot_payload()
    empty.assets_empty_confirm.setChecked(True)
    empty.liabilities_empty_confirm.setChecked(True)
    snapshot = parse_snapshot(empty.snapshot_payload())
    assert snapshot.assets == () and snapshot.liabilities == ()
    empty.close()

    dialog = NetWorthSnapshotDialog()
    dialog.date_edit.setDate(QtCore.QDate(2026, 8, 20))
    asset = dialog.add_asset()
    _select(asset.status, "MISSING")
    liability = dialog.add_liability()
    _select(liability.class_combo, "DRAWN_OVERDRAFT")
    liability.unused.setValue(70_000)
    snapshot = parse_snapshot(dialog.snapshot_payload())
    missing = snapshot.assets[0]
    assert asset.gross.text() == "N/A" and asset.economic.text() == "N/A"
    assert asset.valuation_date.text() == "N/A"
    assert missing.gross_value_krw is None
    assert missing.economic_value_krw is None
    assert missing.valuation_date is None
    assert missing.valuation_method.value == "NOT_AVAILABLE"
    assert missing.valuation_source.value == "NOT_AVAILABLE"
    assert missing.uncertainty.value == "UNKNOWN"
    assert snapshot.liabilities[0].unused_limit_krw == 70_000
    dialog.close()
    app.processEvents()


def test_net_worth_revision_preserves_row_ids_and_rejects_semantic_noop() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    baseline = _view()
    dialog = NetWorthSnapshotDialog(baseline=baseline)

    assert [editor.record_id for editor in dialog.asset_editors] == [
        entry.record_id for entry in baseline.snapshot.assets
    ]
    assert [editor.record_id for editor in dialog.liability_editors] == [
        entry.record_id for entry in baseline.snapshot.liabilities
    ]
    with pytest.raises(NetWorthValidationError, match="NOOP"):
        dialog.snapshot_payload()

    first = dialog.asset_editors[0]
    first.gross.setValue(first.gross.value() + 1_000)
    first.economic.setValue(first.economic.value() + 1_000)
    revised = parse_snapshot(dialog.snapshot_payload())
    assert revised.assets[0].record_id == baseline.snapshot.assets[0].record_id
    assert (
        revised.assets[0].economic_claim_id
        == baseline.snapshot.assets[0].economic_claim_id
    )
    assert revised.as_of_date == baseline.snapshot.as_of_date
    dialog.close()
    app.processEvents()


def test_main_window_create_and_same_date_revision_save_once_reload_and_select(
    tmp_path: Path,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    payloads = [_first_dialog_payload()]
    factory_baselines: list[NetWorthView | None] = []

    class AcceptedDialog:
        def __init__(self, payload: dict[str, object]) -> None:
            self.accepted_payload = payload

        def exec(self) -> int:
            return QtWidgets.QDialog.Accepted

    def factory(baseline, _parent):
        factory_baselines.append(baseline)
        return AcceptedDialog(payloads.pop(0))

    missing = tmp_path / "missing-account.json"
    history_root = tmp_path / "history"
    window = MainWindow(
        tmp_path,
        account_snapshot_path=missing,
        kb_account_snapshot_path=missing,
        family_account_snapshot_path=missing,
        toss_runtime_enabled=False,
        net_worth_history_root=history_root,
        net_worth_dialog_factory=factory,
    )
    original_save = window.net_worth_store.save_snapshot
    save_calls: list[dict[str, object]] = []

    def save_once(payload):
        save_calls.append(deepcopy(payload))
        return original_save(payload)

    window.net_worth_store.save_snapshot = save_once
    window.net_worth_page.create_button.click()
    app.processEvents()
    assert len(save_calls) == 1
    baseline = window.net_worth_page._view
    assert baseline is not None
    assert window.net_worth_page.selected_date == date(2026, 8, 20)

    revision = NetWorthSnapshotDialog(baseline=baseline)
    revision.asset_editors[0].gross.setValue(130_000)
    revision.asset_editors[0].economic.setValue(130_000)
    revised_payload = revision.snapshot_payload()
    revision.close()
    payloads.append(revised_payload)
    window.net_worth_page.revise_button.click()
    app.processEvents()

    history = window.net_worth_store.load_history()
    assert len(save_calls) == 2 and len(history) == 2
    assert factory_baselines == [None, baseline]
    assert history[0].view.snapshot.assets[0].record_id == (
        history[1].view.snapshot.assets[0].record_id
    )
    assert window.net_worth_page.selected_date == date(2026, 8, 20)
    assert window.net_worth_page._view == history[-1].view
    window.close()
    app.processEvents()


def test_main_window_invalid_noop_and_persistence_failure_write_nothing(
    tmp_path: Path,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    history_root = tmp_path / "history"
    store = LocalNetWorthHistoryStore(history_root)
    store.save_snapshot(_payload())
    missing = tmp_path / "missing-account.json"
    window = MainWindow(
        tmp_path,
        account_snapshot_path=missing,
        kb_account_snapshot_path=missing,
        family_account_snapshot_path=missing,
        toss_runtime_enabled=False,
        net_worth_history_root=history_root,
    )
    window._reload_net_worth()
    baseline = window.net_worth_page._view
    assert baseline is not None
    original_save = window.net_worth_store.save_snapshot
    calls: list[object] = []

    def spy(payload):
        calls.append(payload)
        return original_save(payload)

    window.net_worth_store.save_snapshot = spy
    window._save_net_worth_snapshot(_payload(), baseline=baseline)
    invalid = deepcopy(_payload())
    invalid["assets"][0]["gross_value_krw"] = 1
    invalid["assets"][0]["economic_value_krw"] = 2
    window._save_net_worth_snapshot(invalid, baseline=baseline)
    assert calls == []
    assert len(window.net_worth_store.load_history()) == 1

    revision = NetWorthSnapshotDialog(baseline=baseline)
    revision.asset_editors[0].gross.setValue(121_000)
    revision.asset_editors[0].economic.setValue(121_000)
    revised_payload = revision.snapshot_payload()
    revision.close()

    def fail(_payload):
        raise NetWorthPersistenceError("PRIVATE_VALUE_MUST_NOT_SURFACE")

    window.net_worth_store.save_snapshot = fail
    window._save_net_worth_snapshot(revised_payload, baseline=baseline)
    assert len(window.net_worth_store.load_history()) == 1
    assert window.net_worth_page._view == baseline
    assert "PRIVATE_VALUE" not in window.net_worth_page.summary.text()
    assert not any(character.isdigit() for character in window.net_worth_page.summary.text())
    window.close()
    app.processEvents()
