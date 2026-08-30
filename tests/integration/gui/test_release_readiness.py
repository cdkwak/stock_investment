from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault(
        "QT_QPA_FONTDIR",
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    )

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from stock_data.gui.main_window import DashboardPage
from stock_data.orchestration.release_readiness import (
    NATIVE_GUI_HEALTH_TIMEOUT_MS,
    assess_native_gui,
    run_native_gui_smoke,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
VIEWPORT_CASES = (
    ((2560, 1440), 1.0),
    ((1920, 1080), 1.0),
    ((1600, 900), 1.0),
    ((1366, 768), 1.0),
    ((1280, 720), 1.0),
    ((2560, 1440), 1.25),
    ((1920, 1080), 1.25),
    ((1600, 900), 1.25),
    ((1366, 768), 1.25),
    ((1280, 720), 1.25),
)


def test_provider_free_cold_gui_renders_managed_health_within_bound() -> None:
    result = run_native_gui_smoke(PROJECT_ROOT)

    assert result["health_row_count"] > 0
    assert result["health_managed_total"] > 0
    assert result["health_managed_acceptable"] == result["health_managed_total"]
    assert result["health_render_timeout_ms"] == NATIVE_GUI_HEALTH_TIMEOUT_MS
    assert result["health_render_elapsed_ms"] <= NATIVE_GUI_HEALTH_TIMEOUT_MS
    assert result["font_glyphs_supported"] is True
    assert result["dashboard_card_overlaps"] == ()
    assert assess_native_gui(result).status in {"PASS", "DEGRADED"}


@pytest.mark.parametrize(("viewport", "font_scale"), VIEWPORT_CASES)
def test_dashboard_indicator_toolbar_viewport_contract(viewport, font_scale) -> None:
    qt_messages = []
    previous_handler = QtCore.qInstallMessageHandler(
        lambda message_type, _context, message: qt_messages.append(
            (message_type, message)
        )
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    original_font = QtGui.QFont(app.font())
    font = QtGui.QFont(original_font)
    if font_scale != 1.0:
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * font_scale)
        else:
            font.setPixelSize(max(1, round(font.pixelSize() * font_scale)))
        app.setFont(font)

    page = DashboardPage()
    try:
        page.resize(*viewport)
        page.show()
        page.market_indicator_button.setChecked(True)
        app.processEvents()
        page._apply_dashboard_density()
        app.processEvents()

        header_widgets = (
            page.kospi_chart_title,
            page.market_asset_label,
            page.market_asset,
            page.market_period_label,
            page.market_period,
            page.reload_button,
            page.market_indicator_button,
        )
        header_rects = [
            QtCore.QRect(
                widget.mapTo(page.kospi_panel, QtCore.QPoint()), widget.size(),
            )
            for widget in header_widgets
        ]
        assert all(widget.isVisible() for widget in header_widgets)
        assert all(page.kospi_panel.rect().contains(rect) for rect in header_rects)
        assert (
            page.kospi_chart_title.width()
            >= page.kospi_chart_title.sizeHint().width()
        )
        assert page.market_asset.width() >= page.market_asset.sizeHint().width()
        assert page.market_period.width() >= page.market_period.sizeHint().width()
        assert page.market_asset_label.buddy() is page.market_asset
        assert page.market_period_label.buddy() is page.market_period
        header_interactive = (
            page.market_asset,
            page.market_period,
            page.reload_button,
            page.market_indicator_button,
        )
        assert all(
            widget.focusPolicy() & QtCore.Qt.TabFocus
            for widget in header_interactive
        )
        assert all(
            widget.accessibleName().strip() for widget in header_interactive
        )
        for index, left in enumerate(header_rects):
            assert all(
                not left.intersects(right) for right in header_rects[index + 1:]
            )

        panel = page.market_indicator_panel
        controls = tuple(panel._control_widgets)
        assert panel.isVisible()
        assert all(widget.isVisible() for widget in controls)
        assert all(
            widget.width() >= widget.sizeHint().width() for widget in controls
        )

        rects = [
            QtCore.QRect(widget.mapTo(panel, QtCore.QPoint()), widget.size())
            for widget in controls
        ]
        assert all(panel.rect().contains(rect) for rect in rects)
        for index, left in enumerate(rects):
            assert all(not left.intersects(right) for right in rects[index + 1:])

        interactive = tuple(
            widget for widget in controls
            if isinstance(
                widget,
                (QtWidgets.QCheckBox, QtWidgets.QComboBox, QtWidgets.QPushButton),
            )
        )
        assert all(widget.focusPolicy() & QtCore.Qt.TabFocus for widget in interactive)
        assert all(widget.accessibleName().strip() for widget in interactive)
        focus_order = []
        cursor = interactive[0]
        for _ in range(512):
            if cursor in interactive and cursor not in focus_order:
                focus_order.append(cursor)
            cursor = cursor.nextInFocusChain()
            if cursor is interactive[0]:
                break
        assert tuple(focus_order) == interactive
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
        app.setFont(original_font)
        QtCore.qInstallMessageHandler(previous_handler)
    assert not qt_messages
