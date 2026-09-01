from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PySide6 import QtCore, QtWidgets

from stock_data.gui import dashboard_preferences as subject
from stock_data.gui.main_window import (
    DashboardPage,
    DashboardPreferencesDialog,
    IndicatorControlPanel,
    MainWindow,
)
from stock_data.gui.services import (
    DashboardDisplayState, DashboardMetricView, DashboardSparklineView,
)


def _renderable_dashboard_metric() -> DashboardMetricView:
    return DashboardMetricView(
        dataset_id="fixture", series_id="KOSPI", label="KOSPI", value=3000.0,
        unit="points", as_of="2026-08-18", expected_as_of="2026-08-18",
        source="fixture", freshness="CURRENT", pit_status="PIT_LIMITED",
        pit_label="fixture", automation_policy="MANUAL", automation_enabled=False,
        display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
        route="FIXTURE",
    )


def _renderable_dashboard_frame() -> pd.DataFrame:
    positions = np.arange(70, dtype=float)
    close = 3000.0 + positions
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=70),
        "open": close - 1.0, "high": close + 2.0, "low": close - 3.0,
        "close": close, "volume": 1000.0 + positions,
        "ma5": close - 5.0, "ma20": close - 20.0,
        "ma60": close - 60.0, "ma120": close - 120.0,
        "ema20": close - 18.0,
        "bollinger_upper": close + 30.0, "bollinger_mid": close,
        "bollinger_lower": close - 30.0,
        "rsi14": 35.0 + positions / 4.0,
        "disparity60": 97.0 + positions / 25.0,
    })


def test_dashboard_distinguishes_async_startup_loading_from_typed_unavailable_state() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()

    assert all(
        card.body.text() == "불러오는 중…" for card in page.market_cards.values()
    )
    assert page.kospi_chart_title.text() == "KOSPI 차트 · 불러오는 중…"
    assert page.market_chart_status.text() == "로컬 차트 불러오는 중…"

    page.render({"dashboard_metrics": {"KOSPI": _renderable_dashboard_metric()}})
    page.render_market_chart(_renderable_dashboard_frame())
    app.processEvents()

    assert page.market_cards["KOSPI"].body.text() != "불러오는 중…"
    assert "불러오는 중" not in page.kospi_chart_title.text()
    assert "불러오는 중" not in page.market_chart_status.text()
    page.close()
    app.processEvents()


def test_dashboard_labels_current_us_index_etf_and_future_as_distinct_cards() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    labels = {
        "KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ",
        "NQ_FUTURES": "Nasdaq 100", "NASDAQ": "Nasdaq", "SP500": "S&P 500",
        "SOXX": "SOXX", "GOLD": "GOLD", "WTI": "WTI", "BITCOIN": "BITCOIN",
        "USD_KRW_60M": "USD/KRW",
    }
    metrics = {
        key: replace(
            _renderable_dashboard_metric(),
            series_id=key, label=label, value=100.0,
            route=f"yahoo-global60m-current:X:{key}",
            freshness="CURRENT_COMPLETED_60M",
        )
        for key, label in labels.items()
    }

    page.render({"dashboard_metrics": metrics})
    app.processEvents()

    assert {key: page.market_cards[key].title.text() for key in labels} == labels
    assert page.market_cards["BITCOIN"].badge.text() == "24시간"
    assert all(page.market_cards[key].badge.text() == "선물 거래" for key in ("NQ_FUTURES", "GOLD", "WTI"))
    assert all(page.market_cards[key].badge.text() == "60분 완료" for key in ("NASDAQ", "SP500", "SOXX"))
    page.close()
    app.processEvents()


def test_top_strip_prepends_korean_indices_and_renders_nine_session_sparklines() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    metrics = {
        key: replace(
            _renderable_dashboard_metric(), series_id=key, label=label,
            freshness=("MARKET_CLOSED_LAST_FINAL" if key in {"KOSPI", "KOSDAQ"} else "CURRENT"),
        )
        for key, label in page.TOP_METRICS
    }
    views = {
        key: DashboardSparklineView(
            asset=key, lane_id="YAHOO_GLOBAL60M_CURRENT_SESSION", series_id=key,
            frame=pd.DataFrame({
                "date": pd.date_range("2026-08-21T00:00:00Z", periods=4, freq="h"),
                "value": [100.0, 101.0, 100.5, 102.0],
            }),
            interval="60m", session_label="장 시작 후 2026-08-21",
            session_date="2026-08-21", visual_window="completed bars only",
            as_of_kst="2026-08-21 13:00 KST",
            source_timestamp="2026-08-21T04:00:00+00:00",
            source="fixture", freshness=metrics[key].freshness,
            display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
            reference_value=100.0,
        )
        for key, _label in page.TOP_METRICS
    }

    page.render({"dashboard_metrics": metrics, "market_card_sparklines": views})
    page.show()
    page.market_details_button.click()
    app.processEvents()

    assert tuple(page.market_cards) == (
        "KOSPI", "KOSDAQ", "NQ_FUTURES", "NASDAQ", "SP500", "SOXX",
        "GOLD", "WTI", "BITCOIN", "USD_KRW_60M",
    )
    assert all(page.market_cards[key].sparkline.isVisible() for key in page.market_cards)
    assert all(page.market_cards[key].sparkline.height() == 18 for key in page.market_cards)
    assert all(page.market_cards[key].meta.isVisible() for key in page.market_cards)
    assert all(page.market_cards[key].meta.text().endswith("08-18") for key in page.market_cards)
    assert all(
        page.market_cards[key].meta.geometry().right()
        <= page.market_cards[key].rect().right()
        for key in page.market_cards
    )
    assert page.market_cards["KOSPI"].sparkline._reference_value == 100.0
    assert page.market_cards["KOSPI"].sparkline._color.name() == "#e52f3c"
    falling = replace(
        views["KOSPI"],
        frame=views["KOSPI"].frame.assign(value=[101.0, 100.5, 99.0, 98.0]),
    )
    page.market_cards["KOSPI"].set_intraday_sparkline(falling)
    assert page.market_cards["KOSPI"].sparkline._color.name() == "#2878d8"
    assert page.market_cards["KOSPI"].badge.text() == "장마감"
    page.close()
    app.processEvents()


def _custom() -> subject.DashboardPreferences:
    return replace(
        subject.DEFAULT_PREFERENCES,
        card_order=("BITCOIN", "USD_KRW_60M", "KOSPI", "KOSDAQ", "NQ_FUTURES", "NASDAQ", "SP500", "SOXX", "GOLD", "WTI"),
        hidden_cards=frozenset({"GOLD", "WTI"}),
        pinned_cards=frozenset({"BITCOIN"}),
        section_order=(
            "NQ_CHART", "KOSPI_CHART", "MARKET_FLOW", "MARKET_TEMPERATURE",
            "ACCOUNT_SUMMARY", "FX_RATES", "DERIVATIVES",
        ),
        hidden_sections=frozenset({"ACCOUNT_SUMMARY"}),
        density="COMPACT",
        default_market_asset="S&P 500",
        default_market_period="1Y",
        default_nq_interval="주봉",
        window_geometry=subject.WindowGeometry(-9000, 9000, 2100, 1300, True),
    )


def test_missing_settings_return_exact_accepted_default(tmp_path: Path) -> None:
    result = subject.LocalDashboardPreferencesStore(tmp_path / "layout.json").load()
    assert result.preferences == subject.DEFAULT_PREFERENCES
    assert result.reason == "DEFAULT_MISSING"
    assert result.preferences.window_geometry == subject.WindowGeometry(40, 40, 1600, 900, False)


def test_dashboard_market_period_presets_are_ordered_and_all_round_trip(
    tmp_path: Path,
) -> None:
    assert subject.MARKET_PERIODS == (
        "60D", "120D", "1Y", "3Y", "5Y", "10Y", "MAX",
    )
    store = subject.LocalDashboardPreferencesStore(tmp_path / "layout.json")
    for period in subject.MARKET_PERIODS:
        expected = replace(subject.DEFAULT_PREFERENCES, default_market_period=period)
        store.save(expected)
        assert store.load().preferences == expected


def test_dashboard_period_combos_use_the_shared_extended_contract() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    dialog = DashboardPreferencesDialog(subject.DEFAULT_PREFERENCES)
    expected = list(subject.MARKET_PERIODS)
    assert [page.market_period.itemText(index) for index in range(page.market_period.count())] == expected
    assert [dialog.market_period.itemText(index) for index in range(dialog.market_period.count())] == expected
    dialog.close()
    page.close()
    app.processEvents()


def test_versioned_atomic_roundtrip_keeps_watchlists_and_private_data_out(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dashboard_preferences.json"
    watchlist = tmp_path / "watchlists.json"
    watchlist.write_text('{"separate":true}', encoding="utf-8")
    store = subject.LocalDashboardPreferencesStore(path)
    store.save(_custom())

    loaded = store.load()
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = path.read_text(encoding="utf-8").lower()
    assert loaded.preferences == _custom() and loaded.reason == "LOADED"
    assert payload["schema_version"] == subject.SCHEMA_VERSION
    assert not any(token in body for token in (
        "credential", "token", "account_balance", "provider_payload", "market_data",
    ))
    assert watchlist.read_text(encoding="utf-8") == '{"separate":true}'
    assert store.backup_path.is_file()


def test_v1_migration_adds_sections_pinning_nq_and_rewrites_v2(tmp_path: Path) -> None:
    path = tmp_path / "layout.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "visible_cards": [item for item in subject.CARD_IDS if item != "WTI"],
        "card_order": list(reversed(subject.CARD_IDS)),
        "compact": True,
        "default_market_asset": "Nasdaq",
        "default_market_period": "60D",
        "window_geometry": {"x": 5, "y": 6, "width": 1500, "height": 850, "maximized": False},
    }), encoding="utf-8")

    result = subject.LocalDashboardPreferencesStore(path).load()
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert result.reason == "MIGRATED_V1"
    assert result.preferences.hidden_cards == frozenset({"WTI"})
    assert result.preferences.pinned_cards == frozenset()
    assert result.preferences.section_order == subject.SECTION_IDS
    assert result.preferences.default_nq_interval == "일봉"
    assert migrated["schema_version"] == 8


def test_corrupt_primary_recovers_latest_valid_backup_without_startup_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "layout.json"
    store = subject.LocalDashboardPreferencesStore(path)
    store.save(_custom())
    path.write_text("{broken", encoding="utf-8")

    result = store.load()
    assert result.reason == "RECOVERED_LAST_VALID"
    assert result.preferences == _custom()
    assert path.read_text(encoding="utf-8") == "{broken"


def test_failed_atomic_replace_preserves_primary_and_cleans_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "layout.json"
    store = subject.LocalDashboardPreferencesStore(path)
    store.save(subject.DEFAULT_PREFERENCES)
    before = path.read_bytes()
    original = subject.os.replace

    def fail_primary(source, target):
        if Path(target) == path:
            raise OSError("synthetic replace failure")
        return original(source, target)

    monkeypatch.setattr(subject.os, "replace", fail_primary)
    with pytest.raises(subject.DashboardPreferencesError, match="WRITE_FAILED"):
        store.save(_custom())
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".*.tmp"))


def test_parent_directory_creation_failure_is_translated_without_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "unavailable"
    path = parent / "layout.json"
    sibling = tmp_path / "keep.txt"
    sibling.write_text("unchanged", encoding="utf-8")
    original = Path.mkdir

    def fail_exact_parent(self, *args, **kwargs):
        if self == parent:
            raise PermissionError("synthetic parent denial")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_exact_parent)

    with pytest.raises(subject.DashboardPreferencesError, match="WRITE_FAILED"):
        subject.LocalDashboardPreferencesStore(path).save(_custom())

    assert not parent.exists()
    assert sibling.read_text(encoding="utf-8") == "unchanged"
    assert not list(tmp_path.rglob("*.tmp"))


def test_safe_geometry_clamps_offscreen_and_reset_is_exact(tmp_path: Path) -> None:
    geometry = subject.safe_window_geometry(
        _custom().window_geometry,
        (0, 0, 1920, 1040),
    )
    assert geometry == subject.WindowGeometry(0, 0, 1920, 1040, True)

    store = subject.LocalDashboardPreferencesStore(tmp_path / "layout.json")
    store.save(_custom())
    reset = store.reset()
    assert reset == subject.DEFAULT_PREFERENCES
    assert store.load().preferences == subject.DEFAULT_PREFERENCES


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"account_balance": 123}),
        lambda payload: payload["card_order"].append("UNKNOWN"),
        lambda payload: payload.update({"pinned_cards": ["GOLD"], "hidden_cards": ["GOLD"]}),
        lambda payload: payload["window_geometry"].update({"width": 999999}),
    ],
)
def test_invalid_or_private_shaped_settings_fail_to_defaults(tmp_path: Path, mutation) -> None:
    path = tmp_path / "layout.json"
    payload = subject.preferences_payload(subject.DEFAULT_PREFERENCES)
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = subject.LocalDashboardPreferencesStore(path).load()
    assert result.reason == "DEFAULT_CORRUPT"
    assert result.preferences == subject.DEFAULT_PREFERENCES


def test_preferences_dialog_supports_keyboard_reorder_pin_and_accessible_controls() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dialog = DashboardPreferencesDialog(subject.DEFAULT_PREFERENCES)
    emitted = []
    dialog.preferences_applied.connect(emitted.append)
    assert "순서" in dialog.card_list.accessibleName()
    assert "Alt Up" in dialog.card_up.accessibleName()
    dialog.card_list.setCurrentRow(1)
    dialog._move(dialog.card_list, -1)
    dialog._toggle_pin()
    dialog.section_list.setCurrentRow(1)
    dialog._move(dialog.section_list, -1)
    dialog.density.setCurrentIndex(dialog.density.findData("COMPACT"))
    dialog._apply()

    preferences = emitted[-1]
    assert preferences.card_order[:2] == ("KOSDAQ", "KOSPI")
    assert preferences.pinned_cards == frozenset({"KOSDAQ"})
    assert preferences.section_order[:2] == ("NQ_CHART", "KOSPI_CHART")
    assert preferences.density == "COMPACT"
    dialog.close()
    app.processEvents()


def test_dashboard_applies_hidden_pinned_density_and_sections_without_reload() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    reloads = []
    page.market_chart_requested.connect(lambda *request: reloads.append(request))
    preferences = replace(
        _custom(), window_geometry=subject.DEFAULT_PREFERENCES.window_geometry,
    )
    page.apply_preferences(preferences)

    ordered = [
        page.top_strip.itemAt(index).widget()
        for index in range(page.top_strip.count())
    ]
    assert ordered[0] is page.market_cards["BITCOIN"]
    assert page.market_cards["BITCOIN"].property("pinned") is True
    assert page.market_cards["GOLD"].isHidden()
    assert page.market_cards["NQ_FUTURES"].comparison.isHidden()
    assert page.account_placeholder.isHidden()
    assert not page.preferences_button.isHidden()
    assert not page.freshness.isHidden()
    assert reloads == []
    page.close()
    app.processEvents()


def test_main_window_layout_change_is_call_free_and_restart_restores_settings(
    tmp_path: Path,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "dashboard_preferences.json"
    custom = replace(
        _custom(),
        default_market_asset="KOSPI",
        default_market_period="120D",
        default_nq_interval="일봉",
        window_geometry=subject.DEFAULT_PREFERENCES.window_geometry,
    )
    first = MainWindow(
        tmp_path, dashboard_preferences_path=path, toss_runtime_enabled=False,
    )
    chart_reads = []
    first.refresh_market_chart = lambda *args: chart_reads.append(args)
    first._apply_and_save_dashboard_preferences(custom)
    assert chart_reads == []
    assert first.dashboard.market_cards["GOLD"].isHidden()
    first.close()
    app.processEvents()

    second = MainWindow(
        tmp_path, dashboard_preferences_path=path, toss_runtime_enabled=False,
    )
    assert second._dashboard_preferences.card_order == custom.card_order
    assert second._dashboard_preferences.hidden_cards == custom.hidden_cards
    assert second.dashboard.market_cards["GOLD"].isHidden()
    second._reset_dashboard_preferences()
    assert second._dashboard_preferences == subject.DEFAULT_PREFERENCES
    assert not second.dashboard.market_cards["GOLD"].isHidden()
    second.close()
    app.processEvents()


def test_dashboard_indicator_panel_is_single_owner_and_persists_display_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MainWindow, "_queue_local_dashboard_reload", lambda _self: None)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "dashboard_preferences.json"
    window = MainWindow(
        tmp_path, dashboard_preferences_path=path, toss_runtime_enabled=False,
    )
    page = window.dashboard
    default = subject.DEFAULT_PREFERENCES.dashboard_indicators
    selected = replace(
        default,
        ma5=True,
        ma20=True,
        ma60=False,
        ma120=True,
        volume=False,
        rsi14_mode="Overlay",
        disparity60_mode="Overlay",
        ema20=True,
        bollinger_bands=True,
    )

    assert page.market_indicator_panel.settings() == default
    assert page.market_indicator_panel.isHidden()
    assert all(not hasattr(page, name) for name in (
        "market_volume_toggle", "market_ma60_toggle", "market_rsi_toggle",
        "market_disparity_toggle",
    ))
    page._dashboard_indicator_changed(selected)
    assert page.market_indicator_panel.isHidden()
    assert window._dashboard_preferences.dashboard_indicators == selected
    assert subject.LocalDashboardPreferencesStore(path).load().preferences.dashboard_indicators == selected

    page.apply_preferences(subject.DEFAULT_PREFERENCES)
    assert page.market_indicator_panel.settings() == default
    window.close()
    app.processEvents()

    restarted = MainWindow(
        tmp_path, dashboard_preferences_path=path, toss_runtime_enabled=False,
    )
    assert restarted._dashboard_preferences.dashboard_indicators == selected
    restored = restarted.dashboard
    assert restored.market_indicator_panel.settings() == selected
    assert restored.market_indicator_panel.isHidden()
    restarted.resize(1600, 900)
    restarted.show()
    app.processEvents()
    restored.render({"dashboard_metrics": {"KOSPI": _renderable_dashboard_metric()}})
    frame = _renderable_dashboard_frame()
    restored.render_market_chart(frame)
    app.processEvents()

    assert [item.name() for item in restored.market_chart.listDataItems()] == [
        "MA5", "MA20", "MA120", "EMA20",
        "BB 상단", "BB 중심", "BB 하단",
    ]
    assert set(restored._market_overlay_items) == {"rsi14", "disparity60"}
    assert restored._market_rsi_overlay_axis.isVisible()
    assert restored._market_disparity_overlay_axis.isVisible()
    assert restored.market_indicator.isHidden()
    assert "MA5" in restored.market_indicator_legend.text()
    assert "BB(20,2)" in restored.market_indicator_legend.text()
    scene_pos = restored.market_chart.getPlotItem().vb.mapViewToScene(
        QtCore.QPointF(69.0, float(frame.iloc[-1].close)),
    )
    restored._mouse_moved((scene_pos,))
    for label in (
        "MA5", "MA20", "MA120", "EMA20", "BB 상단", "BB 중심", "BB 하단",
        "RSI14", "괴리60",
    ):
        assert label in restored.market_chart.toolTip()
    restarted.close()
    app.processEvents()


def test_indicator_panel_normalizes_restored_mixed_lower_units() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = IndicatorControlPanel(allows_lower_panels=True)
    mixed = replace(
        subject.DEFAULT_PREFERENCES.index_indicators,
        rsi14_mode="Panel", atr14_mode="Panel", obv_mode="Panel",
    )
    panel.apply(mixed)
    settings = panel.settings()
    assert settings.rsi14_mode == "Panel"
    assert settings.atr14_mode == settings.obv_mode == "Off"
    panel.extra_lower["adx14_mode"].setCurrentIndex(1)
    assert panel.settings().rsi14_mode == "Off"
    assert panel.settings().adx14_mode == "Panel"
    panel.close()
    app.processEvents()


def test_main_window_write_failure_reverts_visible_state_and_keeps_notices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(
        tmp_path,
        dashboard_preferences_path=tmp_path / "layout.json",
        toss_runtime_enabled=False,
    )
    previous = window._dashboard_preferences

    def fail(_preferences):
        raise subject.DashboardPreferencesError("synthetic")

    monkeypatch.setattr(window.dashboard_preferences_store, "save", fail)
    window._apply_and_save_dashboard_preferences(
        replace(_custom(), window_geometry=previous.window_geometry)
    )
    assert window._dashboard_preferences == previous
    assert not window.dashboard.market_cards["GOLD"].isHidden()
    assert "저장 실패" in window.dashboard.preference_status.text()
    assert not window.dashboard.freshness.isHidden()
    window.close()
    app.processEvents()
