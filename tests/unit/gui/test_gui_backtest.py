from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pyqtgraph as pg
import pytest
from PySide6 import QtCharts, QtCore, QtGui, QtTest, QtWidgets

import app as app_module
import market_backtest.phase1_replay as phase1_replay
import stock_data.gui.backtest_service as backtest_service_module
import stock_data.gui.main_window as main_window_module
import stock_data.gui.research_workspace_preferences as research_preferences_module
from stock_data.gui.account_value_history import (
    AccountValueHistoryPoint,
    AccountValueHistorySeries,
)
from stock_data.gui.account_snapshot_service import (
    AccountAssetPoint,
    AccountCurrencySummaryView,
    AccountPortfolioEntryView,
    AccountPortfolioView,
    AccountPositionView,
    AccountSnapshotState,
    AccountSnapshotView,
    AccountSourceActionView,
    LocalAccountPortfolioService,
    LocalAccountSnapshotService,
    LocalAccountSourceSpec,
)
from stock_data.gui.backtest_service import BacktestResultService, BacktestWorkflowError
from stock_data.gui.backtest_scenario_service import (
    SCENARIO_ID,
    SCENARIO_INPUT_VERSION,
    BacktestScenarioInputs,
    BacktestScenarioService,
)
from market_backtest.portfolio import KOSPI200_FROZEN_HOLDOUT_V1
from stock_data.gui.health_service import HealthArtifactView, HealthDatasetRow
from stock_data.gui.main_window import (
    AccountPage, BacktestPage, CandlestickItem, DashboardPage, DataStatusPage,
    DetachedChartWindow, EquityChartWorker, GlobalSymbolSwitcher, IndividualEquityPage, IndexPage, MainWindow, WatchlistPage, _aggregate_ohlc,
    _chart_reference_metadata, _continuous_connection_mask, _display_message,
    _daily_session_axis_mapping, _downsample_market_frame,
    _fmt_krw_flow, _plot_continuous_line,
    _freshness_label, _market_session_bar_state, _session_axis_warning,
)
from stock_data.gui.manual_account_store import (
    LocalManualAccountStore, ManualAccountPosition, ManualAccountRecord,
)
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.naver_remaining_session_windows import ensure_manifest, is_active
from stock_data.orchestration.naver_mobile_home_ur191_windows import (
    MANIFEST_PATH as UR191_MANIFEST_PATH,
    STATE_PATH as UR191_STATE_PATH,
    eligible_boundary as ur191_eligible_boundary,
    manifest_payload as ur191_manifest_payload,
)
from stock_data.orchestration.naver_equity_ur199_windows import (
    MANIFEST_PATH as UR203_MANIFEST_PATH,
    STATE_PATH as UR203_STATE_PATH,
    eligible_identities as ur203_eligible_identities,
    manifest_payload as ur203_manifest_payload,
)
from stock_data.orchestration.nasdaq_soxx_ur193_windows import STATE_PATH as UR193_STATE_PATH
from stock_data.orchestration.account_privacy import MASKED_VALUE
from stock_data.orchestration.toss_account_snapshot import AccountRefreshTrigger
from stock_data.providers.tossinvest import normalize_holdings_payload
from stock_data.providers.kbsec import normalize_domestic_balance_payload
from stock_data.gui.services import (
    CurrentObservationCoverageView, DashboardAverageComparisonView,
    DashboardChartCoverage, DASHBOARD_CHART_COVERAGE_ATTR,
    DashboardCurrentStageView, DashboardDisplayState, DashboardMetricView, DashboardSeriesView,
    MarketValuationView, MarketValuationWindowView,
    DashboardSparklineView, DashboardService,
    EquityIdentity, EquitySearchView, EquitySeriesView, IndexSeriesView,
    NormalizedBenchmarkComparisonView,
    US_ETF_CHART_IDENTITIES,
    MarketFundingValue, MarketFundingView,
    MarketInvestorFlowValue, MarketInvestorFlowView,
    TossShortWatchlistView, TreasuryRateView, VIXSourceView,
)
from stock_data.contracts.toss_short_watchlist import TOSS_EQUITY_SHORT_WATCHLIST_DAILY
from stock_data.orchestration.toss_short_watchlist_daily import validate_watchlist_dataset
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.gui.vix_futures_adapter import build_vix_futures_dashboard_view
from stock_data.gui.watchlist_service import (
    DEFAULT_LIST_ID, NamedWatchlist, WatchlistItem, WatchlistQuote, WatchlistState,
)


def test_global_symbol_worker_unions_exact_local_catalogs_without_series_reads():
    kr = EquityIdentity("005930", "삼성전자", "KOSPI", "KR7005930003", "1975-06-11", "STOCK")
    us = next(item for item in US_ETF_CHART_IDENTITIES if item.symbol == "SPY")
    calls: list[tuple[str, str]] = []
    service = SimpleNamespace(
        equity=SimpleNamespace(search=lambda query: (calls.append(("KR", query)) or EquitySearchView(query, (kr,)))),
        us_etf=SimpleNamespace(search=lambda query: (calls.append(("US", query)) or EquitySearchView(query, (us,)))),
    )
    worker = EquityChartWorker(service, "global_search", "삼성")
    completed: list[tuple[str, object, object]] = []
    worker.completed.connect(lambda *args: completed.append(args))

    worker.run()

    assert calls == [("KR", "삼성"), ("US", "삼성")]
    assert completed[0][0:2] == ("global_search", "삼성")
    assert completed[0][2].matches == (kr, us)


def test_global_symbol_switcher_requires_explicit_selection_and_shows_identity_context():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    switcher = GlobalSymbolSwitcher()
    kr = EquityIdentity("005930", "삼성전자", "KOSPI", "KR7005930003", "1975-06-11", "STOCK")
    us = next(item for item in US_ETF_CHART_IDENTITIES if item.symbol == "SPY")
    selected: list[EquityIdentity] = []
    switcher.identity_selected.connect(selected.append)
    switcher.query.setText("s")

    switcher.render(EquitySearchView("s", (kr, us)))

    assert switcher.results.count() == 2
    assert "KOSPI" in switcher.results.item(0).text()
    assert "KRW" in switcher.results.item(0).text()
    assert "US ETF" in switcher.results.item(1).text()
    assert "USD" in switcher.results.item(1).text()
    assert "명시적으로 선택" in switcher.status.text()
    assert selected == []
    switcher.results.setCurrentRow(1)
    switcher._choose()
    assert selected == [us]
    switcher.query.setText("unknown-local-symbol")
    switcher.render(EquitySearchView("unknown-local-symbol", ()))
    assert switcher.results.count() == 0
    assert "일치하는 로컬 식별정보" in switcher.status.text()
    switcher.close()


def test_context_watchlist_sidebar_is_visible_shows_recent_flow_and_delegates_exact_mutations():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage()
    samsung = EquityIdentity(
        "005930", "삼성전자", "KOSPI", "KR7005930003", "1975-06-11", "STOCK"
    )
    state = WatchlistState((NamedWatchlist(
        DEFAULT_LIST_ID, "관심종목", (WatchlistItem(samsung, "2026-08-25T00:00:00+09:00"),),
    ),))
    toggles: list[tuple[EquityIdentity, str, bool]] = []
    moves: list[tuple[str, tuple[str, str], int]] = []
    opens: list[tuple[EquityIdentity, str]] = []
    page.favorite_toggled.connect(lambda *args: toggles.append(args))
    page.watchlist_item_moved.connect(lambda *args: moves.append(args))
    page.context_identity_open_requested.connect(
        lambda identity: opens.append((identity, page.period.currentText()))
    )

    page.set_watchlists(state)

    assert not page.context_watchlist_rail.isCheckable()
    assert page.context_watchlist_splitter.widget(1) is page.context_watchlist_rail
    assert not page.context_watchlist_items.isHidden()
    assert page.context_watchlist_items.count() == 1
    quote = WatchlistQuote(
        samsung, 76_000, 500, 0.66, "2026-08-25 KST 일봉", "CURRENT",
        five_session_pct=2.5,
        recent_period_pct=6.0,
        recent_closes=(70_000, 71_000, 72_000, 74_000, 73_000, 76_000),
    )
    page.render_context_watchlist_quotes((samsung,), (quote,))
    row_text = page.context_watchlist_items.item(0).text()
    assert "76,000원 · 당일 +0.66%" in row_text
    assert "5거래일 +2.50%" in row_text
    assert "최근 6개 +6.00%" in row_text
    assert any(character in row_text for character in "▁▂▃▄▅▆▇█")
    new_identity = EquityIdentity(
        "000660", "SK하이닉스", "KOSPI", "KR7000660001", "1996-12-26", "STOCK"
    )
    page._selected_identity = new_identity
    page._sync_favorite_controls()
    assert page.context_watchlist_add.isEnabled()
    page.context_watchlist_add.click()
    page.context_watchlist_items.setCurrentRow(0)
    page._open_context_watchlist_item()
    page._move_context_watchlist_item(1)
    page._remove_context_watchlist_item()
    assert opens == [(samsung, page.period.currentText())]
    assert moves == [(DEFAULT_LIST_ID, samsung.key, 1)]
    assert toggles == [
        (new_identity, DEFAULT_LIST_ID, True),
        (samsung, DEFAULT_LIST_ID, False),
    ]
    page.close()
    restarted = IndividualEquityPage()
    assert not restarted.context_watchlist_rail.isCheckable()
    assert not restarted.context_watchlist_items.isHidden()
    restarted.close()


def test_main_window_ctrl_k_switcher_routes_exact_kr_and_us_identity(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    window.current_observation_reload_timer.stop()
    window.resize(1600, 900)
    window.show()
    for origin in (window.dashboard, window.account_page, window.equity_page):
        window.tabs.setCurrentWidget(origin)
        window.global_symbol_shortcut.activated.emit()
        app.processEvents()
        QtTest.QTest.qWait(10)
        app.processEvents()
        assert window.global_symbol_switcher.isVisible()
        assert window.global_symbol_switcher.focusWidget() is window.global_symbol_switcher.query
        window.global_symbol_switcher.hide()
    assert window.equity_page.minimumSizeHint().width() <= window.tabs.width()
    assert window.us_etf_page.minimumSizeHint().width() <= window.tabs.width()
    calls: list[tuple[str, EquityIdentity]] = []
    monkeypatch.setattr(
        window.equity_page, "_request_identity",
        lambda identity: calls.append(("KR", identity)),
    )
    monkeypatch.setattr(
        window.us_etf_page, "_request_identity",
        lambda identity: calls.append(("US", identity)),
    )
    samsung = EquityIdentity(
        "005930", "삼성전자", "KOSPI", "KR7005930003", "1975-06-11", "STOCK"
    )
    spy = next(item for item in US_ETF_CHART_IDENTITIES if item.symbol == "SPY")
    window._open_global_identity(samsung)
    assert window.tabs.currentWidget() is window.equity_page
    window._open_global_identity(spy)
    assert window.tabs.currentWidget() is window.us_etf_page
    assert calls == [("KR", samsung), ("US", spy)]
    mixed = WatchlistState((NamedWatchlist(
        DEFAULT_LIST_ID,
        "혼합 관심종목",
        (
            WatchlistItem(samsung, "2026-08-25T00:00:00+09:00"),
            WatchlistItem(spy, "2026-08-25T00:01:00+09:00"),
        ),
    ),))
    window.equity_page.set_watchlists(mixed)
    window.us_etf_page.set_watchlists(mixed)
    window.tabs.setCurrentWidget(window.equity_page)
    window.equity_page.context_watchlist_items.setCurrentRow(1)
    window.equity_page._open_context_watchlist_item()
    assert window.tabs.currentWidget() is window.us_etf_page
    window.us_etf_page.context_watchlist_items.setCurrentRow(0)
    window.us_etf_page._open_context_watchlist_item()
    assert window.tabs.currentWidget() is window.equity_page
    assert calls == [
        ("KR", samsung), ("US", spy), ("US", spy), ("KR", samsung),
    ]
    window.global_symbol_switcher.close()
    window.close()


def test_main_window_deduplicates_and_distributes_context_watchlist_flow(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    window.current_observation_reload_timer.stop()
    samsung = _equity_identity()
    window._watchlist_state = WatchlistState((NamedWatchlist(
        DEFAULT_LIST_ID,
        "관심종목",
        (WatchlistItem(samsung, "2026-08-26T08:00:00+09:00"),),
    ),))
    requests = []
    monkeypatch.setattr(
        window,
        "_request_equity_job",
        lambda action, request: requests.append((action, request)),
    )

    window._render_watchlists()

    assert requests == [("watchlist", (samsung,))]
    quote = WatchlistQuote(
        samsung, 76_000, 500, 0.66, "2026-08-25 KST 일봉", "CURRENT",
        five_session_pct=2.5,
        recent_period_pct=6.0,
        recent_closes=(70_000, 71_000, 72_000, 74_000, 73_000, 76_000),
    )
    window._equity_loaded("watchlist", (samsung,), (quote,))

    assert "5거래일 +2.50%" in window.equity_page.context_watchlist_items.item(0).text()
    assert "최근 6개 +6.00%" in window.us_etf_page.context_watchlist_items.item(0).text()
    window.close()
    app.processEvents()


@pytest.mark.parametrize("universe", ["KR", "US"])
def test_research_workspace_renders_exact_typed_ohlcv_and_suppresses_unavailable(
    universe, tmp_path,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = research_preferences_module.LocalResearchWorkspacePreferencesStore(
        tmp_path / "research-workspace.json"
    )
    page = main_window_module.ResearchWorkspacePage(
        store, store.load().preferences,
    )
    identity = _equity_identity() if universe == "KR" else _us_etf_identity()
    view = _equity_series_view(identity)
    page.begin_identity(identity)
    page.render_series(view)

    displayed = page.displayed_ohlcv()
    assert len(displayed) == len(view.frame)
    for index in (0, len(view.frame) - 1):
        row = view.frame.iloc[index]
        assert displayed[index] == (
            pd.Timestamp(row.date).date().isoformat(),
            float(row.open), float(row.high), float(row.low),
            float(row.close), float(row.volume),
        )
    assert identity.symbol in page.instrument_facts.text()
    assert view.source in page.source_status.text()

    blocked = replace(
        view,
        frame=pd.DataFrame(),
        display_state=DashboardDisplayState.REFRESH_REQUIRED,
        freshness="STALE",
        unavailable_reason="fixture stale",
    )
    page.begin_identity(identity)
    page.render_series(blocked)
    assert page.displayed_ohlcv() == ()
    assert not page.chart.listDataItems()
    assert "fixture stale" in page.summary.text()
    page.close()
    app.processEvents()


def test_research_workspace_panel_preset_save_restart_and_reset(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "research-workspace.json"
    store = research_preferences_module.LocalResearchWorkspacePreferencesStore(path)
    page = main_window_module.ResearchWorkspacePage(store, store.load().preferences)
    page.panel_selector.setCurrentIndex(page.panel_selector.findData("SOURCE_STATUS"))
    page.panel_visible.setChecked(False)
    page.panel_size.setValue(333)
    page.panel_up.click()
    page.preset_name.setText("분석")
    page.save_preset_button.click()
    expected_order = page.panel_order

    restarted_store = research_preferences_module.LocalResearchWorkspacePreferencesStore(path)
    restarted = main_window_module.ResearchWorkspacePage(
        restarted_store, restarted_store.load().preferences,
    )
    assert restarted.panel_order == expected_order
    assert restarted.logical_sizes["SOURCE_STATUS"] == 333
    assert not restarted.panel_visibility["SOURCE_STATUS"]
    assert restarted.preset_selector.currentData() == "분석"
    restarted.reset_preset_button.click()
    assert restarted.panel_order == research_preferences_module.PANEL_IDS
    assert all(restarted.panel_visibility.values())
    assert restarted.preset_selector.count() == 1
    page.close(); restarted.close(); app.processEvents()


def test_research_workspace_ctrl_k_preserves_workspace_and_routes_kr_us_locally(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(
        tmp_path,
        toss_runtime_enabled=False,
        research_workspace_preferences_path=tmp_path / "research-workspace.json",
    )
    window.current_observation_reload_timer.stop()
    calls = []
    monkeypatch.setattr(
        window, "_request_equity_job", lambda action, request: calls.append(("KR", action, request))
    )
    monkeypatch.setattr(
        window, "_request_us_etf_job", lambda action, request: calls.append(("US", action, request))
    )
    samsung = _equity_identity()
    spy = _us_etf_identity()
    for identity in (samsung, spy):
        window.tabs.setCurrentWidget(window.research_workspace_page)
        window.global_symbol_shortcut.activated.emit()
        assert window._global_symbol_origin is window.research_workspace_page
        window._open_global_identity(identity)
        assert window.tabs.currentWidget() is window.research_workspace_page
        assert window.research_workspace_page._selected_identity == identity
    assert calls == [
        ("KR", "candidate_scan", None),
        ("KR", "research_series", (samsung, "120D")),
        ("US", "research_series", (spy, "120D")),
    ]
    assert window._equity_thread is None and window._us_etf_thread is None
    window.global_symbol_switcher.close(); window.close(); app.processEvents()


def test_research_workspace_result_routing_preserves_destination_and_selected_identity(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(
        tmp_path,
        toss_runtime_enabled=False,
        research_workspace_preferences_path=tmp_path / "research-workspace.json",
    )
    window.current_observation_reload_timer.stop()
    samsung = _equity_identity()
    spy = _us_etf_identity()
    kr_view = _equity_series_view(samsung)
    us_view = _equity_series_view(spy)
    chart_results = []
    monkeypatch.setattr(window.equity_page, "render_series", chart_results.append)

    window.research_workspace_page.begin_identity(spy)
    window._equity_loaded("series", (samsung, "120D"), kr_view)
    window._equity_loaded("research_series", (samsung, "120D"), kr_view)
    assert chart_results == [kr_view]
    assert window.research_workspace_page.displayed_ohlcv() == ()

    window._us_etf_loaded("research_series", (spy, "120D"), us_view)
    assert window.research_workspace_page.displayed_ohlcv()
    window.close(); app.processEvents()


def test_research_workspace_rejects_same_key_with_changed_exact_identity(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = research_preferences_module.LocalResearchWorkspacePreferencesStore(
        tmp_path / "research-workspace.json"
    )
    page = main_window_module.ResearchWorkspacePage(
        store, store.load().preferences,
    )
    identity = _equity_identity()
    page.begin_identity(identity)
    spoofed = replace(
        _equity_series_view(identity),
        identity=replace(identity, name="변조 이름"),
    )
    page.render_series(spoofed)
    assert page.displayed_ohlcv() == ()
    assert not page.chart.listDataItems()
    assert page.instrument_facts.text() == identity.display_label
    page.close(); app.processEvents()


def test_research_workspace_watchlist_and_1600_maximized_layout_are_local(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(
        tmp_path,
        toss_runtime_enabled=False,
        research_workspace_preferences_path=tmp_path / "research-workspace.json",
    )
    _stub_fast_startup_local_reads(window)
    _drain_main_window_workers(app, window)
    window.current_observation_reload_timer.stop()
    samsung = _equity_identity()
    state = WatchlistState((NamedWatchlist(
        DEFAULT_LIST_ID, "관심종목",
        (WatchlistItem(samsung, "2026-08-25T00:00:00+09:00"),),
    ),))
    window.research_workspace_page.set_watchlists(state)
    window.resize(1600, 900); window.show(); app.processEvents()
    page = window.research_workspace_page
    window.tabs.setCurrentWidget(page); app.processEvents()
    assert page.watchlist_items.count() == 1
    assert "005930" in page.watchlist_items.item(0).text()
    assert page.minimumSizeHint().width() <= window.tabs.width()
    assert page.geometry().bottom() <= window.tabs.contentsRect().bottom()
    window.showMaximized(); app.processEvents()
    assert page.geometry().bottom() <= window.tabs.contentsRect().bottom()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))
    window.close(); app.processEvents()


def _metric(
    series_id: str,
    value: float | None,
    *,
    freshness: str,
    state: DashboardDisplayState,
    as_of: str = "2026-08-18",
    unit: str = "points",
    dataset_id: str = "fixture",
    change: float | None = None,
    change_pct: float | None = None,
    automation_enabled: bool = False,
    automation_policy: str = "MANUAL",
) -> DashboardMetricView:
    return DashboardMetricView(
        dataset_id=dataset_id, series_id=series_id, label=series_id,
        value=value, unit=unit, as_of=as_of, expected_as_of="2026-08-18",
        source="fixture source", freshness=freshness, pit_status="PIT_LIMITED",
        pit_label="설명용", automation_policy=automation_policy,
        automation_enabled=automation_enabled,
        display_state=state, unavailable_reason="fixture unavailable" if value is None else None,
        route="FIXTURE", change=change, change_pct=change_pct,
    )


@pytest.mark.parametrize("universe", ["KR_EQUITY", "US_ETF"])
def test_equity_page_persistent_instrument_facts_are_source_safe(universe):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage(universe=universe)
    if universe == "US_ETF":
        identity = next(item for item in US_ETF_CHART_IDENTITIES if item.symbol == "SPY")
        view = _blocked_us_etf_series_view(identity)
    else:
        identity = EquityIdentity(
            "005930", "삼성전자", "KOSPI", "KR7005930003", "1975-06-11", "STOCK",
        )
        view = replace(
            _equity_series_view(identity),
            frame=pd.DataFrame(),
            display_state=DashboardDisplayState.REFRESH_REQUIRED,
            freshness="STALE",
            as_of=None,
            reference_kst=None,
            unavailable_reason="stale fixture",
        )

    page.begin_series(identity)
    assert not page.instrument_facts.isVisible()
    page.render_series(view)

    assert not page.instrument_facts.isHidden()
    assert identity.symbol in page.instrument_facts_identity.text()
    assert identity.market in page.instrument_facts_identity.text()
    assert (identity.currency or "미보존") in page.instrument_facts_context.text()
    assert "기준 미확인" in page.instrument_facts_context.text()
    assert "KRW 환산: 승인된 로컬 필드 없음" in page.instrument_facts_risk.text()
    assert page.instrument_facts.property("displaysPriceFacts") is False
    assert "52주" in page.instrument_facts_risk.text()
    assert "$" not in page.instrument_facts_risk.text()
    if universe == "US_ETF":
        assert identity.issuer in page.instrument_facts_risk.text()
        assert identity.leverage_style in page.instrument_facts_risk.text()
    else:
        assert "ETF 레버리지·분배 특성 해당 없음" in page.instrument_facts_risk.text()
    page.close()
    app.processEvents()


def _comparison(
    series_id: str,
    *,
    kind: str = "relative_percent",
    comparison_5: float | None = 1.25,
    comparison_20: float | None = -0.75,
) -> DashboardAverageComparisonView:
    return DashboardAverageComparisonView(
        series_id=series_id, comparison_kind=kind,
        interval="completed daily", as_of="2026-08-18", latest_value=119.0,
        mean_5=117.5 if comparison_5 is not None else None,
        mean_20=120.0 if comparison_20 is not None else None,
        comparison_5=comparison_5, comparison_20=comparison_20,
        coverage_5=("2026-08-12", "2026-08-18", 5) if comparison_5 is not None else None,
        coverage_20=("2026-07-22", "2026-08-18", 20) if comparison_20 is not None else None,
        display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
        reason_5=None if comparison_5 is not None else "실제 완료 일봉 5개가 필요합니다.",
        reason_20=None if comparison_20 is not None else "실제 완료 일봉 20개가 필요합니다.",
    )


def _market_flow_view(market: str) -> MarketInvestorFlowView:
    return MarketInvestorFlowView(
        dataset_id="kr_market_investor_net_purchase_bridge_daily",
        market=market,
        values=(
            MarketInvestorFlowValue("FOREIGN", "외국인", 10_000_000_000, 30_000_000_000),
            MarketInvestorFlowValue("INSTITUTION", "기관", -6_000_000_000, -18_000_000_000),
            MarketInvestorFlowValue("INDIVIDUAL", "개인", -4_000_000_000, -12_000_000_000),
        ),
        as_of="2026-08-19",
        expected_as_of="2026-08-19",
        value_unit="KRW",
        source="Toss Securities Open API",
        source_operation="getMarketIndicatorInvestorTrading",
        provider_segment="TOSS_2014_PRESENT",
        freshness="CURRENT",
        finality="DAILY_FINAL",
        display_state=DashboardDisplayState.VALUE,
        unavailable_reason=None,
        weekly_unavailable_reason=None,
        covered_sessions=("2026-08-17", "2026-08-18", "2026-08-19"),
        required_sessions=("2026-08-17", "2026-08-18", "2026-08-19"),
        missing_sessions=(),
        partial_week=True,
    )


def _market_funding_view() -> MarketFundingView:
    return MarketFundingView(values=(
        MarketFundingValue(
            "CREDIT_FINANCING", "신용융자 잔고", 19_500_000,
            "provider-native", "2026-08-06", "data.go.kr/KOFIA", "STALE",
            "최신값이 아닌 보존값입니다.",
        ),
        MarketFundingValue(
            "INVESTOR_DEPOSITS", "투자자 예탁금", 62_000_000_000_000,
            "KRW", "2026-08-06", "data.go.kr/KOFIA", "STALE",
            "최신값이 아닌 보존값입니다.",
        ),
        MarketFundingValue(
            "RECEIVABLES", "위탁매매 미수금", None,
            "KRW", None, "data.go.kr/KOFIA", "UNKNOWN", "보존값 없음",
        ),
        MarketFundingValue(
            "FORCED_SALE", "반대매매 금액", 8_000_000_000,
            "KRW", "2026-08-06", "data.go.kr/KOFIA", "STALE",
            "최신값이 아닌 보존값입니다.",
        ),
    ))


def _write_empty_toss_account_snapshot(path: Path) -> None:
    snapshot = normalize_holdings_payload({"result": {
        "totalPurchaseAmount": {"krw": "0", "usd": None},
        "marketValue": {
            "amount": {"krw": "0", "usd": None},
            "amountAfterCost": {"krw": "0", "usd": None},
        },
        "profitLoss": {
            "amount": {"krw": "0", "usd": None},
            "amountAfterCost": {"krw": "0", "usd": None},
            "rate": "0", "rateAfterCost": "0",
        },
        "dailyProfitLoss": {
            "amount": {"krw": "0", "usd": None}, "rate": "0",
        },
        "items": [],
    }}, collected_at=datetime(2026, 8, 20, 1, 2, 3, tzinfo=timezone.utc))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot), encoding="utf-8")


def _write_toss_account_snapshot_with_position(path: Path) -> None:
    _write_empty_toss_account_snapshot(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["positions"] = [{
        "symbol": "005930",
        "name": "Fixture Equity",
        "market_country": "KR",
        "currency": "KRW",
        "quantity": "0",
        "last_price": "0",
        "average_purchase_price": "0",
        "purchase_amount": "0",
        "market_value": "0",
        "market_value_after_cost": "0",
        "profit_loss": "0",
        "profit_loss_after_cost": "0",
        "profit_loss_rate": "0",
        "profit_loss_rate_after_cost": "0",
        "daily_profit_loss": "0",
        "daily_profit_loss_rate": "0",
        "commission": "0",
        "tax": None,
    }]
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_toss_short_watchlist_fixture(root: Path) -> None:
    frame = pd.DataFrame({
        "date": [pd.Timestamp("2026-08-19"), pd.Timestamp("2026-08-19")],
        "market": ["KOSPI", "KOSPI"],
        "symbol": ["000660", "005930"],
        "short_selling_volume": [248_815, 1_586_828],
        "short_selling_amount": [377_019_707_500, 396_498_888_250],
        "short_selling_volume_rate": [0.012, 0.034],
        "short_selling_amount_rate": [0.013, 0.035],
        "source_scope": ["KRX_ONLY_PROVIDER_EOD"] * 2,
        "watchlist_version": ["2026-08-20-v1"] * 2,
        "source": ["tossinvest_open_api"] * 2,
        "source_operation": ["getStockShortSelling"] * 2,
        "source_date": ["2026-08-19"] * 2,
        "collected_at": [pd.Timestamp("2026-08-20T00:07:33Z")] * 2,
        "updated_at": [
            pd.Timestamp("2026-08-19T09:13:47Z"),
            pd.Timestamp("2026-08-19T09:14:07Z"),
        ],
        "availability_date": ["2026-08-19"] * 2,
    })
    write_dataset_atomic(
        frame,
        root / "data/normalized/toss_equity_short_watchlist_daily",
        TOSS_EQUITY_SHORT_WATCHLIST_DAILY,
        validate_watchlist_dataset,
    )
    state = root / "data/state/toss_equity_short_watchlist_daily.json"
    journal = root / "data/state/toss_equity_short_watchlist_daily_journal.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "dataset": "toss_equity_short_watchlist_daily",
        "watchlist_version": "2026-08-20-v1",
        "status": "SUCCEEDED",
        "completed_date": "2026-08-19",
        "completed_symbols": ["000660", "005930"],
        "landing_files": ["landing-005930", "landing-000660"],
        "token_calls": 1,
        "market_calls": 2,
        "retained_rows": 2,
    }), encoding="utf-8")
    journal.write_text(json.dumps({
        "dataset": "toss_equity_short_watchlist_daily",
        "status": "SUCCEEDED",
        "target_date": "2026-08-19",
    }), encoding="utf-8")


def _write_kb_account_snapshot(path: Path) -> None:
    payload = normalize_domestic_balance_payload({
        "dataHeader": {
            "resultCode": "200", "processCode": "0011",
            "processTime": "20260622162350500",
        },
        "dataBody": {
            "grid_cnt1": "0001", "tl_data_cnt": "0001",
            "nt_asts_val_amt": "000000000001066450",
            "scrts_nt_val_amt": "000000000000426500",
            "byng_amt_sum": "000000000000360050",
            "val_amt_sum": "000000000000426500",
            "val_pl_sum": "000000000000066450",
            "Record1": [{
                "is_cd": "A005930", "is_nm": "Fixture Equity", "clsf": "현금",
                "ec_q_p6": "000000001.000000",
                "ordr_psbl_q_p6": "000000001.000000",
                "byng_avr_prc": "000000360050.00",
                "now_prc": "000000426500.00",
                "byng_amt": "000000000000360050",
                "val_amt": "000000000000426500",
                "val_pl": "000000000000066450",
            }],
        },
    }, collected_at=datetime(2026, 8, 20, 1, 2, 3, tzinfo=timezone.utc))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_family_account_snapshot(path: Path) -> None:
    payload = {
        "schema_version": 3,
        "state": "FAMILY_LOCAL_MANUAL",
        "provider": "MIRAE_ASSET_LOCAL_MANUAL",
        "source_mode": "LOCAL_MANUAL",
        "as_of": "2026-08-19T21:00:00+09:00",
        "last_reconciled_at": "2026-08-19T21:05:00+09:00",
        "registered_holder_scope": "FAMILY_MEMBER",
        "economic_attribution_scope": "USER_DECLARED_FUNDS",
        "legal_ownership_claimed": False,
        "include_in_user_fund_total": True,
        "currency": "KRW",
        "total_assets": 500_000,
        "securities_value": 500_000,
        "cash_balance": None,
        "available_cash": None,
        "realized_pnl": None,
        "unrealized_pnl": 20_000,
        "positions": [{
            "symbol": "ETF1", "name": "Fixture ETF", "quantity": 2,
            "market_value": 500_000, "realized_pnl": None,
            "unrealized_pnl": 20_000,
        }],
        "asset_history": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_all_local_account_sources_reject_private_position_text_value_free(tmp_path):
    writers = (
        _write_toss_account_snapshot_with_position,
        _write_kb_account_snapshot,
        _write_family_account_snapshot,
    )
    private_cases = (
        ("symbol", "123456789012"),
        ("name", "accountNo=123456789012"),
    )
    for source_index, writer in enumerate(writers):
        for case_index, (field, private_text) in enumerate(private_cases):
            path = tmp_path / f"source-{source_index}-case-{case_index}.json"
            writer(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["positions"][0][field] = private_text
            path.write_text(json.dumps(payload), encoding="utf-8")

            view = LocalAccountSnapshotService(path).load()

            assert view.state is AccountSnapshotState.NOT_AVAILABLE
            assert view.reason == "ACCOUNT_SNAPSHOT_INVALID"
            assert private_text not in repr(view)

    for source_index, writer in enumerate(writers):
        path = tmp_path / f"valid-source-{source_index}.json"
        writer(path)
        view = LocalAccountSnapshotService(path).load()
        assert view.displays_values
        assert view.positions[0].symbol in {"005930", "A005930", "ETF1"}
        assert view.positions[0].name in {"Fixture Equity", "Fixture ETF"}


def test_freshness_copy_maps_typed_states_to_concise_korean():
    assert {
        status: _freshness_label(status)
        for status in (
            "CURRENT", "EXPECTED_LAG", "STALE", "UNKNOWN", "READ_FAILURE",
            "MARKET_CLOSED_LAST_FINAL",
        )
    } == {
        "CURRENT": "최신 확정",
        "EXPECTED_LAG": "발행 대기",
        "STALE": "갱신 필요",
        "UNKNOWN": "확인 필요",
        "READ_FAILURE": "읽기 실패",
        "MARKET_CLOSED_LAST_FINAL": "장마감",
    }


def test_market_closed_last_verified_has_distinct_holiday_copy():
    assert _freshness_label("MARKET_CLOSED_LAST_VERIFIED") == "휴장 · 최근 검증값"


def test_market_session_bar_splits_krx_nxt_and_us_regular_after_hours_with_dst():
    domestic_open = _market_session_bar_state("2026-08-21T05:20:00Z")
    us_open = _market_session_bar_state("2026-08-21T15:18:00Z")
    krx_closed_nxt_open = _market_session_bar_state("2026-08-21T07:00:00Z")
    summer_after = _market_session_bar_state("2026-08-21T21:00:00Z")
    winter_after = _market_session_bar_state("2026-01-16T21:30:00Z")

    assert domestic_open.domestic_label == "KRX 장중 09:00~15:30 · NXT 장중 08:00~20:00"
    assert domestic_open.domestic_open is True
    assert domestic_open.us_label == "미국 장 시작 전 · 22:30 KST"
    assert domestic_open.us_open is False
    assert us_open.domestic_label == "KRX 장마감 09:00~15:30 · NXT 장마감 08:00~20:00"
    assert us_open.us_label == "미국 정규장 22:30~05:00 KST"
    assert us_open.us_open is True
    assert krx_closed_nxt_open.domestic_label == (
        "KRX 장마감 09:00~15:30 · NXT 장중 08:00~20:00"
    )
    assert summer_after.us_label == "미국 애프터장 05:00~09:00 KST"
    assert winter_after.us_label == "미국 애프터장 06:00~10:00 KST"


def test_current_gate_copy_distinguishes_stale_from_daily_only_route():
    stale = replace(_metric(
        "SOXX", None, freshness="CURRENT_GATE_BLOCKED",
        state=DashboardDisplayState.REFRESH_REQUIRED,
    ), unavailable_reason="CURRENT_SOURCE_DATE_NOT_TODAY_KST: stale source")
    daily_only = replace(_metric(
        "NASDAQ", None, freshness="CURRENT_GATE_BLOCKED",
        state=DashboardDisplayState.REFRESH_REQUIRED,
    ), unavailable_reason="CURRENT_SOURCE_TIMESTAMP_REQUIRED: daily source only")
    assert _display_message(stale) == "갱신 필요"
    assert _display_message(daily_only) == "실시간 미연동"
    assert _freshness_label("CURRENT_GATE_BLOCKED") == "실시간 없음"


def test_market_investor_flow_uses_readable_korean_large_number_units():
    assert _fmt_krw_flow(29_641_780_924) == "+296억"
    assert _fmt_krw_flow(-1_148_803_714_291) == "-1.15조"
    assert _fmt_krw_flow(1_200_003_676_142) == "+1.20조"


def test_nq_candle_periods_use_only_observed_ohlc_rows():
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2026-08-03", "2026-08-04", "2026-08-10"]),
        "open": [100.0, 102.0, 110.0], "high": [105.0, 108.0, 115.0],
        "low": [98.0, 101.0, 109.0], "close": [103.0, 107.0, 114.0], "volume": [10, 20, 30],
    })
    weekly = _aggregate_ohlc(daily, "주봉", market=ExchangeMarket.US)
    monthly = _aggregate_ohlc(daily, "월봉", market=ExchangeMarket.US)
    assert len(weekly) == 2
    assert weekly.iloc[0][["open", "high", "low", "close"]].tolist() == [
        100.0, 108.0, 98.0, 107.0,
    ]
    assert monthly.iloc[0][["open", "high", "low", "close"]].tolist() == [
        100.0, 115.0, 98.0, 114.0,
    ]
    assert weekly["volume"].tolist() == [30, 30]
    # The prior week omitted documented XNYS sessions, and the last week has
    # not reached its final exchange session. Neither bar is historical-complete.
    assert weekly["incomplete_period"].tolist() == [True, True]


def test_aggregate_ohlc_uses_exchange_calendar_reference_and_valid_volume_only():
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    sessions = calendar.sessions_in_range(date(2026, 7, 1), date(2026, 8, 19))
    values = np.arange(len(sessions), dtype=float) + 100.0
    frame = pd.DataFrame({
        "date": pd.to_datetime(sessions),
        "open": values,
        "high": values + 2.0,
        "low": values - 1.0,
        "close": values + 1.0,
        "volume": [10.0] * len(sessions),
    })

    monthly = _aggregate_ohlc(
        frame, "월봉", reference_date="2026-08-19", market=ExchangeMarket.KR,
    )
    assert monthly["incomplete_period"].tolist() == [False, True]
    july = frame[frame.date.dt.month.eq(7)]
    assert monthly.iloc[0][["open", "high", "low", "close"]].tolist() == [
        july.open.iloc[0], july.high.max(), july.low.min(), july.close.iloc[-1],
    ]
    assert monthly.iloc[0].volume == len(july) * 10.0

    # An expected session absent from the retained frame never becomes a
    # completed historical aggregate. Known calendar closures are absent from
    # ``sessions`` by construction, so they do not create the same failure.
    gap_date = pd.Timestamp(sessions[5])
    with_gap = frame[frame.date.ne(gap_date)].reset_index(drop=True)
    weekly = _aggregate_ohlc(
        with_gap, "주봉", reference_date="2026-08-19", market=ExchangeMarket.KR,
    )
    assert weekly.loc[weekly.date.dt.isocalendar().week.eq(gap_date.isocalendar().week), "incomplete_period"].item()

    volume_frame = frame[
        frame.date.between(pd.Timestamp("2026-08-03"), pd.Timestamp("2026-08-14"))
    ].copy().reset_index(drop=True)
    volume_frame.loc[:4, "volume"] = [10.0, np.nan, -1.0, np.inf, 20.0]
    volume_frame.loc[5:, "volume"] = np.nan
    volumes = _aggregate_ohlc(
        volume_frame, "주봉", reference_date="2026-08-19", market=ExchangeMarket.KR,
    )
    assert volumes.iloc[0].volume == 30.0
    assert pd.isna(volumes.iloc[1].volume)


def test_aggregate_ohlc_reconciles_december_january_exchange_period_boundary():
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    sessions = calendar.sessions_in_range(date(2025, 12, 1), date(2026, 1, 2))
    values = np.arange(len(sessions), dtype=float) + 100.0
    frame = pd.DataFrame({
        "date": pd.to_datetime(sessions),
        "open": values,
        "high": values + 4.0,
        "low": values - 2.0,
        "close": values + 1.0,
        "volume": np.arange(len(sessions), dtype=float) + 10.0,
    })

    weekly = _aggregate_ohlc(
        frame, "주봉", reference_date="2026-01-02", market=ExchangeMarket.KR,
    )
    cross_year_week = frame[frame.date.ge(pd.Timestamp("2025-12-29"))]
    assert weekly.iloc[-1][["open", "high", "low", "close", "volume"]].tolist() == [
        cross_year_week.open.iloc[0], cross_year_week.high.max(),
        cross_year_week.low.min(), cross_year_week.close.iloc[-1],
        cross_year_week.volume.sum(),
    ]
    assert not weekly.iloc[-1].incomplete_period

    monthly = _aggregate_ohlc(
        frame, "월봉", reference_date="2026-01-02", market=ExchangeMarket.KR,
    )
    assert monthly["date"].dt.strftime("%Y-%m").tolist() == ["2025-12", "2026-01"]
    december = frame[frame.date.dt.month.eq(12)]
    assert monthly.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [
        december.open.iloc[0], december.high.max(), december.low.min(),
        december.close.iloc[-1], december.volume.sum(),
    ]
    assert monthly["incomplete_period"].tolist() == [False, True]


def test_index_measurement_clear_is_accessible_and_state_free():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndexPage()
    page._measurement_points = [1, 2]
    page.measurement.setText("측정 A → B · +1.00")
    page.clear_measurement_button.click()
    assert page._measurement_points == []
    assert page.measurement.text() != "측정 A → B · +1.00"
    assert page.clear_measurement_button.accessibleName() == "두 점 측정 제거"
    page.close(); app.processEvents()


def test_index_reload_emits_current_selection_once_without_clearing_chart():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndexPage()
    view = _index_series_view()
    page.render(view)
    requested: list[tuple[str, str]] = []
    page.request_series.connect(
        lambda index, period: requested.append((index, period))
    )
    before = page._frame.copy()

    page.index_reload_button.click()

    assert requested == [("KOSPI", "120D")]
    pd.testing.assert_frame_equal(page._frame, before)
    assert page.index.currentText() == "KOSPI"
    assert page.period.currentText() == "120D"
    assert page.index_reload_button.accessibleName() == (
        "현재 지수와 기간의 로컬 시계열 다시 읽기"
    )
    page.close()
    app.processEvents()


def test_equity_page_keeps_only_its_identity_safe_reload_control():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage()

    assert page.index_reload_button.isHidden()
    assert page.reload_button.text() == "다시 읽기"
    assert page.reload_button is not page.index_reload_button
    page.close()
    app.processEvents()


def test_index_measurement_reports_exact_displayed_points_and_survives_resize():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndexPage(); page.resize(1600, 900); page.show()
    frame = pd.DataFrame({"date": pd.bdate_range("2026-08-10", periods=3), "open": [100, 101, 102], "high": [101, 102, 103], "low": [99, 100, 101], "close": [100, 102, 101], "volume": [10, 20, 30], "ma5": [float("nan")]*3, "ma20": [float("nan")]*3, "ma60": [float("nan")]*3, "ma120": [float("nan")]*3, "rsi14": [float("nan")]*3, "disparity60": [float("nan")]*3})
    page.render(frame); app.processEvents()
    page._measurement_points = [0]
    page._measure_clicked(type("Event", (), {"scenePos": lambda self: page.plot.plotItem.vb.mapViewToScene(QtCore.QPointF(2, 101))})())
    assert "3" in page.measurement.text() and "+1.00" in page.measurement.text() and "거래량" in page.measurement.text()
    before = page.measurement.text(); page.resize(1920, 1000); app.processEvents(); assert page.measurement.text() == before
    page.close(); app.processEvents()


def test_equity_timeframe_switch_is_local_and_clears_stale_measurement_crosshair_and_indicators():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    identity = _equity_identity()
    page = IndividualEquityPage()
    page.resize(1600, 900); page.show()
    page._selected_identity = identity
    requested: list[tuple[EquityIdentity, str]] = []
    page.series_requested.connect(lambda selected, period: requested.append((selected, period)))
    page.rsi.setCurrentText("Panel")
    page.render_series(_equity_series_view(identity))
    app.processEvents()
    assert page.timeframe.currentText() == "일봉"
    page._measurement_points = [0, 1]
    page.measurement.setText("old measurement")
    page.crosshairs[0].show()
    page._manual_view = True

    page.timeframe.setCurrentText("월봉")
    app.processEvents()

    assert requested == []
    assert page._frame["incomplete_period"].iloc[-1]
    assert page._frame["rsi14"].isna().all()
    assert page._measurement_points == []
    assert page.measurement.text() == "측정: 두 관측값을 선택하세요"
    assert not any(line.isVisible() for line in page.crosshairs)
    assert not page._manual_view

    page.timeframe.setCurrentText("일봉")
    app.processEvents()
    assert requested == []
    assert page._frame["rsi14"].notna().any()
    page.close(); app.processEvents()


def test_equity_timeframe_status_marks_only_latest_incomplete_aggregate():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    identity = _equity_identity()
    incomplete = _equity_series_view(identity)
    page = IndividualEquityPage()
    page.resize(1600, 900); page.show()
    page._selected_identity = identity
    page.render_series(incomplete)
    page.timeframe.setCurrentText("주봉")
    app.processEvents()
    assert not page.timeframe_aggregate_status.isHidden()
    assert page.timeframe_aggregate_status.text() == "진행 중 집계"
    assert page.timeframe_aggregate_status.accessibleName() == "선택 종목 주기 집계 상태"

    complete_frame = incomplete.frame.loc[
        incomplete.frame.date.le(pd.Timestamp("2026-08-14"))
    ].reset_index(drop=True)
    completed = replace(
        incomplete, frame=complete_frame, as_of="2026-08-14", expected_as_of="2026-08-14",
    )
    page.render_series(completed)
    app.processEvents()
    assert page.timeframe.currentText() == "주봉"
    assert page.timeframe_aggregate_status.isHidden()

    page.timeframe.setCurrentText("일봉")
    app.processEvents()
    assert page.timeframe_aggregate_status.isHidden()
    assert page._frame["ma5"].notna().any()
    page.close(); app.processEvents()


def test_equity_measurement_supports_exact_mouse_and_keyboard_observation_selection():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    identity = _equity_identity()
    view = _equity_series_view(identity)
    page = IndividualEquityPage()
    page.resize(1600, 900); page.show()
    page._selected_identity = identity
    page.render_series(view)
    page.timeframe.setCurrentText("일봉")
    app.processEvents()

    mouse_scene = page.plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(0.0, 69_950.0))
    page._measure_clicked(type("Event", (), {"scenePos": lambda self: mouse_scene})())
    page._show_observation(4)
    page.add_measurement_button.setFocus()
    QtTest.QTest.keyClick(page.add_measurement_button, QtCore.Qt.Key_Space)
    app.processEvents()

    assert page.add_measurement_button.accessibleName() == "현재 표시 관측값을 두 점 측정에 추가"
    assert "2026-03-05 → 2026-03-11" in page.measurement.text()
    assert "5 세션" in page.measurement.text()
    assert "+201.68" in page.measurement.text()
    assert "(+0.29%)" in page.measurement.text()
    assert "거래량 Δ +33,613" in page.measurement.text()
    before = page.measurement.text()
    page.resize(1920, 1000); app.processEvents()
    assert page.measurement.text() == before
    page.close(); app.processEvents()


def test_daily_session_axis_compresses_weekend_but_keeps_true_observation_dates():
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-02-06", "2026-02-09"])})

    mapping = _daily_session_axis_mapping(frame, ExchangeMarket.US)

    assert mapping.positions.tolist() == [0.0, 1.0]
    assert [value.date().isoformat() for value in mapping.dates] == ["2026-02-06", "2026-02-09"]
    assert mapping.missing_sessions == ()
    assert _session_axis_warning(mapping) == ""


def test_daily_session_axis_treats_exchange_holiday_as_known_closure():
    # XNYS was closed for Martin Luther King Jr. Day on 2026-01-19.
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-16", "2026-01-20"])})

    mapping = _daily_session_axis_mapping(frame, ExchangeMarket.US)

    assert mapping.calendar_name == "XNYS"
    assert mapping.missing_sessions == ()


@pytest.mark.parametrize(
    "observed_dates",
    (
        ("2026-06-02", "2026-06-04"),
        ("2026-07-16", "2026-07-20"),
    ),
)
def test_daily_session_axis_treats_official_krx_2026_one_off_dates_as_closures(
    observed_dates,
):
    frame = pd.DataFrame({"date": pd.to_datetime(observed_dates)})

    mapping = _daily_session_axis_mapping(frame, ExchangeMarket.KR)

    assert mapping.calendar_name == "XKRX"
    assert mapping.missing_sessions == ()
    assert _session_axis_warning(mapping) == ""


def test_daily_session_axis_surfaces_genuine_missing_trading_session():
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-01-20", "2026-01-22"])})

    mapping = _daily_session_axis_mapping(frame, ExchangeMarket.US)

    assert mapping.positions.tolist() == [0.0, 1.0]
    assert [value.date().isoformat() for value in mapping.missing_sessions] == ["2026-01-21"]
    assert "source missing XNYS sessions: 2026-01-21" == _session_axis_warning(mapping)


def test_continuous_curve_uses_exact_coordinates_antialias_and_session_gap_mask():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    plot = pg.PlotWidget()
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-20", "2026-01-22"]),
    })
    mapping = _daily_session_axis_mapping(frame, ExchangeMarket.US)
    x = np.array([0.0, 1.0])
    y = np.array([100.125, 101.875])
    global_antialias = pg.getConfigOption("antialias")

    item = _plot_continuous_line(
        plot, x, y, color="#53d8fb", width=1.35, name="MA5", mapping=mapping,
    )
    rendered_x, rendered_y = item.getData()
    connect = item.curve.opts["connect"]
    pen = item.curve.opts["pen"]

    assert np.array_equal(rendered_x, x)
    assert np.array_equal(rendered_y, y)
    assert connect.dtype == np.int32 and connect.tolist() == [0, 0]
    assert item.curve.opts["antialias"] is True
    assert item.opts["autoDownsample"] is False
    assert pen.isCosmetic()
    assert pen.capStyle() == QtCore.Qt.RoundCap
    assert pen.joinStyle() == QtCore.Qt.RoundJoin
    assert pg.getConfigOption("antialias") == global_antialias

    holiday_mapping = _daily_session_axis_mapping(
        pd.DataFrame({"date": pd.to_datetime(["2026-01-16", "2026-01-20"])}),
        ExchangeMarket.US,
    )
    assert _continuous_connection_mask(x, y, holiday_mapping).tolist() == [1, 0]
    assert _continuous_connection_mask(x, np.array([100.0, np.nan]), holiday_mapping).tolist() == [0, 0]
    plot.close()
    app.processEvents()


def test_dashboard_has_one_direct_runtime_class_and_unified_chart_selector():
    assert DashboardPage.__bases__ == (QtWidgets.QScrollArea,)
    assert not hasattr(main_window_module, "LegacyDashboardPage")
    assert not hasattr(main_window_module, "DashboardPhase1FunctionalPage")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    emitted = []
    page.market_chart_requested.connect(
        lambda asset, period: emitted.append((asset, period))
    )

    assert [page.market_asset.itemText(index) for index in range(page.market_asset.count())] == [
        "KOSPI", "KOSDAQ", "Nasdaq 100", "Nasdaq 100 Futures", "Nasdaq",
        "S&P 500", "SOXX", "GOLD", "WTI",
    ]
    assert page.market_asset.accessibleName() == "시장 가격 차트 자산"
    assert page.market_period.accessibleName() == "시장 가격 차트 기간"
    assert page.reload_button.text() == "로컬 새로고침"
    assert not hasattr(page, "chart_shortcuts")
    assert page.market_chart_header_controls.indexOf(page.market_asset) < page.market_chart_header_controls.indexOf(page.market_period)
    assert page.market_chart_header_controls.indexOf(page.market_period) < page.market_chart_header_controls.indexOf(page.reload_button)
    assert page.market_indicator_controls.indexOf(page.market_indicator_panel) == 0
    assert not hasattr(page, "market_volume_toggle")
    assert not hasattr(page, "market_ma60_toggle")
    assert not hasattr(page, "market_rsi_toggle")
    assert not hasattr(page, "market_disparity_toggle")

    for selected in ("KOSDAQ", "S&P 500", "Nasdaq"):
        page.market_asset.setCurrentText(selected)
    app.processEvents()
    assert emitted[-3:] == [("KOSDAQ", "120D"), ("SP500", "120D"), ("NASDAQ", "120D")]
    assert page.market_indicator_panel.isHidden()
    page.market_indicator_button.click()
    assert not page.market_indicator_panel.isHidden()
    page.close()
    app.processEvents()


def test_dashboard_combines_valuation_with_temperature_and_keeps_market_flow_reachable():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()

    assert not hasattr(page, "kospi_flow")
    assert page.market_flow_panel.accessibleName() == "KOSPI KOSDAQ 시장 수급"
    assert page.market_context_tabs.accessibleName() == "시장 수급"
    assert [
        page.market_context_tabs.tabText(index)
        for index in range(page.market_context_tabs.count())
    ] == ["시장 수급"]
    assert page.market_context_tabs.currentWidget() is page.market_flow_panel
    assert not page.market_valuation_panel.isHidden()
    assert page.market_valuation_panel.parentWidget() is page.oscillator_panel
    assert "밸류에이션" in page.oscillator_panel.accessibleName()
    assert not page.market_flow_panel.isHidden()
    assert page.side_layout.indexOf(page.market_flow_panel) == -1
    assert page.side_layout.indexOf(page.market_valuation_panel) == -1
    assert page.side_layout.indexOf(page.market_context_tabs) == -1
    assert page.left_layout.indexOf(page.market_context_tabs) >= 0
    assert [
        page.market_flow_panel.tabs.tabText(index)
        for index in range(page.market_flow_panel.tabs.count())
    ] == ["KOSPI", "KOSDAQ", "신용·자금"]

    page.render({
        "market_flow_views": {
            market: _market_flow_view(market)
            for market in ("KOSPI", "KOSDAQ")
        },
        "market_funding_view": _market_funding_view(),
    })
    kospi = page.market_flow_panel.pages["KOSPI"]
    kosdaq = page.market_flow_panel.pages["KOSDAQ"]
    assert kospi.latest_labels["FOREIGN"].text() == "순매수 +100억"
    assert kospi.latest_labels["INSTITUTION"].text() == "순매도 -60억"
    assert kospi.weekly_labels["FOREIGN"].text() == "순매수 +300억"
    assert kospi.detail.text() == "2026-08-19 장마감"
    assert kospi.latest_bars["FOREIGN"]._value == 10_000_000_000
    assert kospi.latest_bars["INSTITUTION"]._value == -6_000_000_000
    assert kospi.latest_bars["FOREIGN"]._scale == 10_000_000_000
    assert "미국 수급 미지원" in page.market_flow_panel.us_scope_note.text()
    assert "국내 외국인·기관·개인" in page.market_flow_panel.us_scope_note.toolTip()
    assert "market=KOSPI" in kospi.toolTip()
    assert "finality=DAILY_FINAL" in kospi.toolTip()
    assert "market=KOSDAQ" in kosdaq.toolTip()
    funding = page.market_flow_panel.funding_page
    assert funding.value_labels["CREDIT_FINANCING"].text() == "19,500,000 · 공급자 원단위"
    assert funding.value_labels["INVESTOR_DEPOSITS"].text() == "62.00조원"
    assert funding.value_labels["RECEIVABLES"].text() == "보존값 없음"
    assert "freshness=STALE" in funding.toolTip()

    page.resize(1600, 900)
    page.show()
    app.processEvents()
    assert page.horizontalScrollBar().maximum() == 0
    assert page.market_flow_panel.isVisible()
    assert page.market_context_tabs.geometry().right() <= page.viewport().width()
    assert page.market_valuation_panel.isVisible()
    page.close()
    app.processEvents()


def test_market_flow_panel_suppresses_only_incomplete_week_numbers():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    incomplete = replace(
        _market_flow_view("KOSPI"),
        values=tuple(
            replace(value, week_to_date_value=None)
            for value in _market_flow_view("KOSPI").values
        ),
        weekly_unavailable_reason="이번 주 보존 세션이 누락되어 누계를 표시하지 않습니다.",
        covered_sessions=("2026-08-17", "2026-08-19"),
        required_sessions=("2026-08-17", "2026-08-18", "2026-08-19"),
        missing_sessions=("2026-08-18",),
    )

    page.market_flow_panel.set_views({
        "KOSPI": incomplete,
        "KOSDAQ": _market_flow_view("KOSDAQ"),
    })
    kospi = page.market_flow_panel.pages["KOSPI"]
    assert kospi.latest_labels["FOREIGN"].text() == "순매수 +100억"
    assert all(label.text() == "표시 제한" for label in kospi.weekly_labels.values())
    assert "누락 2026-08-18" in kospi.detail.text()

    page.close()
    app.processEvents()


def test_dashboard_chart_and_local_reload_requests_reject_reentrant_duplicates():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    chart_requests = []
    reload_requests = []

    def on_chart(asset, period):
        chart_requests.append((asset, period))
        page._request_market_chart()

    def on_reload():
        reload_requests.append(True)
        page.reload_button.click()

    page.market_chart_requested.connect(on_chart)
    page.reload_requested.connect(on_reload)
    page.market_asset.setCurrentText("KOSDAQ")
    page.reload_button.click()
    app.processEvents()

    assert chart_requests == [("KOSDAQ", "120D")]
    assert reload_requests == [True]
    assert page.reload_button.isEnabled()
    page.close()
    app.processEvents()


def test_dashboard_health_summary_uses_all_retained_rows_and_fails_closed(
    tmp_path,
):
    freshness = (
        ["CURRENT"] * 7 + ["EXPECTED_LAG"] * 7 + ["STALE"] * 5
        + ["EXPECTED_LAG"] + ["STALE"] * 14
        + ["UNKNOWN"] * 17 + ["NOT_APPLICABLE"] * 29
    )
    rows = tuple(
        HealthDatasetRow(
            dataset=f"dataset_{index}", role="SOURCE", cadence="DAILY",
            latest="N/A", expected="N/A", freshness=status,
            operational="BLOCKED" if index < 4 else "READY",
            blocker="N/A", pit="PIT_BLOCKED" if index < 5 else "PIT_SAFE",
            automation=(
                "SCHEDULED / ENABLED" if index < 19
                else "NO_REFRESH / DISABLED"
            ), source="fixture",
            runtime_coverage="NOT_PROBED",
        )
        for index, status in enumerate(freshness)
    )
    service = DashboardService(tmp_path)
    summary = service.data_health(
        health=HealthArtifactView("READY", "retained 80-row health", rows)
    )
    assert summary == {
        "overall": "DEGRADED", "current": 7, "expected_lag": 8,
        "stale": 19, "operational_blocked": 4,
        "predictive_blocked": 5, "research_only": 0, "failed": 17,
        "managed_total": 19, "managed_acceptable": 14,
        "managed_current": 7, "managed_expected_lag": 7,
        "managed_stale": 5, "managed_unknown": 0,
        "managed_not_applicable": 0,
        "display_total": 0, "display_gap": 0,
        "display_stale": 0, "display_unknown": 0,
        "source": "retained 80-row health",
    }
    unavailable = service.data_health(
        health=HealthArtifactView(
            "REPORT NOT AVAILABLE", "missing health", (), "missing",
        )
    )
    assert unavailable["overall"] == "UNKNOWN"
    assert unavailable["current"] == 0 and unavailable["failed"] == 1


def _write_ur166_mobile_home_projection(root: Path) -> None:
    path = root / "data/state/current_observations/naver_mobile_home_current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    observations = []
    for dataset_id, market, symbol, unit, value, timestamp in (
        ("KR_INDEX_CURRENT", "XKRX", "KOSPI", "index points", 6938.86, "2026-08-21T05:14:00+00:00"),
        ("KR_INDEX_CURRENT", "XKRX", "KOSDAQ", "index points", 802.27, "2026-08-21T05:14:00+00:00"),
        ("FX_CURRENT", "KRW", "USD_KRW", "KRW per USD", 1381.7, "2026-08-21T05:12:00+00:00"),
    ):
        observations.append({
            "route_id": f"naver-mobile-home-current:{market}:{symbol}",
            "identity": {"dataset_id": dataset_id, "market": market, "symbol": symbol},
            "interval": "snapshot", "value": value, "unit": unit,
            "provider": "NAVER_FINANCE_WEB", "upstream_provider": "NAVER_FINANCE_WEB",
            "source_route": "NAVER_WEB:/", "provider_timestamp_utc": timestamp,
            "retrieved_at_utc": "2026-08-21T05:18:11+00:00", "finality": "PROVISIONAL",
            "display_only": True, "pit_safe": False,
        })
    path.write_text(json.dumps({"schema_version": 1, "observations": observations}), encoding="utf-8")


def test_dashboard_service_locally_overlays_ur166_rows_without_diagnostic_strip(tmp_path):
    _write_ur166_mobile_home_projection(tmp_path)
    service = DashboardService(tmp_path)
    snapshot = service.snapshot(now_utc=datetime(2026, 8, 21, 5, 20, tzinfo=timezone.utc))
    assert snapshot["health_rows"] == {}
    assert snapshot["data_health"]["overall"] == "UNKNOWN"
    assert snapshot["data_health"]["current"] == 0

    for series_id, value, unit in (
        ("KOSPI", 6938.86, "index points"),
        ("KOSDAQ", 802.27, "index points"),
        ("USD_KRW", 1381.7, "KRW per USD"),
    ):
        metric = snapshot["dashboard_metrics"][series_id]
        coverage = snapshot["current_observation_coverage"][series_id]
        assert metric.displays_value and metric.value == pytest.approx(value)
        assert metric.unit == unit
        assert metric.source_timestamp is not None
        assert metric.freshness == "CURRENT_PROVISIONAL"
        assert metric.pit_status == "PIT_BLOCKED"
        assert coverage.displays_value and coverage.value == pytest.approx(value)
        assert coverage.provider == "NAVER_FINANCE_WEB"

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render(snapshot)
    assert page.current_observation_strip_cells == []
    assert page.domestic_market_session.accessibleName() == "국내 정규장"
    assert page.us_market_session.accessibleName() == "미국 장마감"
    page.close()
    app.processEvents()

    stale = service.snapshot(now_utc=datetime(2026, 8, 21, 6, 21, tzinfo=timezone.utc))
    assert stale["dashboard_metrics"]["KOSPI"].value is None
    assert stale["dashboard_metrics"]["KOSPI"].unavailable_reason.startswith(
        "CURRENT_SOURCE_AGE_OVER_60M"
    )


def test_dashboard_coverage_copy_labels_retained_source_date_not_live_refresh():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page._render_current_observation_coverage({
        "SP500": CurrentObservationCoverageView(
            coverage_id="SP500", label="S&P 500", value=7684.13,
            unit="index points", provider="FinanceDataReader / Yahoo daily",
            route="YAHOO:^GSPC", interval="1d", as_of="2026-08-20",
            retrieved_at_utc="2026-08-20T16:50:44+00:00",
            freshness="RETAINED_AS_RETRIEVED", finality="AS_RETRIEVED_DAILY",
            display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
            provider_timestamp_utc="2026-08-20T00:00:00+00:00",
            source_route="YAHOO:^GSPC", display_only=True, pit_safe=False,
        ),
    })

    assert page.current_observation_status.text() == "Retained: 1 display / 0 unavailable"
    tooltip = page.current_observation_status.toolTip()
    assert "source-date label, not a live provider refresh" in tooltip
    assert "as_of=2026-08-20" in tooltip
    assert "freshness=RETAINED_AS_RETRIEVED" in tooltip
    assert "provider_timestamp_utc=2026-08-20T00:00:00+00:00" in tooltip
    assert "display_only=True | pit_safe=False" in tooltip
    page.close()
    app.processEvents()


def test_dashboard_current_diagnostic_is_hidden_and_not_rendered_in_session_bar():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page._render_current_observation_coverage({
        "SOXX": CurrentObservationCoverageView(
            coverage_id="SOXX", label="SOXX current ETF quote", value=526.6332,
            unit="USD per share", provider="NASDAQ_OFFICIAL",
            route="nasdaq-soxx-info-api:NASDAQ:SOXX", interval="snapshot",
            as_of="2026-08-21 17:08 KST", retrieved_at_utc="2026-08-21T08:09:35.261238+00:00",
            freshness="CURRENT_PROVISIONAL", finality="PROVISIONAL",
            display_state=DashboardDisplayState.VALUE,
            unavailable_reason="Nasdaq official retained current snapshot; route-local USD-per-ETF-share contract; display-only; PIT-blocked.",
            provider_timestamp_utc="2026-08-21T08:08:00+00:00",
            source_route="NASDAQ_OFFICIAL:api.nasdaq.com/api/quote/SOXX/info?assetclass=etf",
            display_only=True, pit_safe=False,
        ),
    })
    assert page.current_observation_status.isHidden()
    assert page.current_observation_strip_cells == []
    assert "SOXX" not in page.domestic_market_session.text()
    assert "SOXX" not in page.us_market_session.text()
    assert "provider=NASDAQ_OFFICIAL" in page.current_observation_status.toolTip()
    page.close()
    app.processEvents()


def test_dashboard_current_strip_excludes_owner_only_inferred_nxt_close_values():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    owner_only = {
        key: CurrentObservationCoverageView(
            coverage_id=key, label=f"{symbol} inferred NXT close", value=value,
            unit="KRW per share", provider="tossinvest_open_api", route=route,
            interval="snapshot", as_of="2026-08-21 19:59:59 KST",
            retrieved_at_utc="2026-08-21T13:28:08+00:00",
            freshness="NXT_SESSION_CLOSE_INFERRED", finality=finality,
            display_state=DashboardDisplayState.VALUE,
            unavailable_reason="TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW; NOT_LIVE",
            provider_timestamp_utc="2026-08-21T10:59:59+00:00",
            source_route="/api/v1/prices", display_only=True, pit_safe=False,
            nxt_session_gate=True, nxt_venue_inferred=True,
            visible_label="NXT \uB9C8\uAC10(\uC2DC\uAC04\uCC3D \uCD94\uB860) 19:59:59",
        )
        for key, symbol, value, route, finality in (
            ("EQUITY_000660_NXT_CLOSE", "000660", 1_761_000.0, "toss-stock-price:000660:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW", "POST_CLOSE_SNAPSHOT"),
            ("EQUITY_005930_NXT_CLOSE", "005930", 270_000.0, "toss-stock-price:005930:snapshot:PROVISIONAL:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW", "PROVISIONAL"),
        )
    }
    page.render({"dashboard_metrics": {}, "current_observation_coverage": owner_only})

    assert not [cell for cell in page.current_observation_strip_cells if not cell.isHidden()]
    assert "2 Korean-equity owner-only" in page.current_observation_status.text()
    assert "1,761,000" not in page.current_observation_status.toolTip()
    assert "270,000" not in page.current_observation_status.toolTip()
    assert "000660 1,761,000 KRW per share" in page.toss_short_watchlist.body.text()
    assert "NOT_LIVE" in page.toss_short_watchlist.toolTip()
    page.close()
    app.processEvents()


def test_equity_detail_header_labels_timestamp_valid_ls_15m_as_display_only_and_pit_blocked():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    identity = _equity_identity()
    page = IndividualEquityPage()
    page._selected_identity = identity
    page.render_series(replace(
        _equity_series_view(identity),
        current_value=71_500.0,
        current_unit="provider_native_price",
        current_source_date="2026-08-21",
        current_retrieved_at_utc="2026-08-21T05:55:00+00:00",
        current_provider="LS_OPENAPI",
        current_refresh_status="CURRENT_SOURCE_TIMESTAMP_VALID",
        current_route="ls-t8412-current:XKRX:005930",
        current_interval="15m",
        current_finality="AS_RETRIEVED",
        current_provider_timestamp_utc="2026-08-21T05:45:00+00:00",
        current_source_route="LS_OPENAPI:/stock/chart:t8412",
        current_display_only=True,
        current_pit_safe=False,
    ))

    assert "LS 15m retained 71,500" in page.summary.text()
    assert "LS 15m retained; display-only, PIT-blocked" in page.status.text()
    assert "current_route=ls-t8412-current:XKRX:005930" in page.status.toolTip()
    assert "current_provider_timestamp_utc=2026-08-21T05:45:00+00:00" in page.status.toolTip()
    assert "current_pit_safe=False" in page.status.toolTip()
    page.close()
    app.processEvents()


def test_equity_detail_header_owns_inferred_nxt_close_not_live_accessibility():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    identity = _equity_identity()
    page = IndividualEquityPage()
    page._selected_identity = identity
    page.render_series(replace(
        _equity_series_view(identity),
        current_value=270_000.0,
        current_unit="KRW per share",
        current_source_date="2026-08-21",
        current_retrieved_at_utc="2026-08-21T13:28:08+00:00",
        current_provider="tossinvest_open_api",
        current_refresh_status="NXT_SESSION_CLOSE_INFERRED",
        current_route="toss-stock-price:005930:snapshot:PROVISIONAL:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
        current_interval="snapshot", current_finality="PROVISIONAL",
        current_provider_timestamp_utc="2026-08-21T10:59:59+00:00",
        current_source_route="/api/v1/prices:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
        current_display_only=True, current_pit_safe=False,
        current_unavailable_reason="TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW; NOT_LIVE",
        current_visible_label="NXT \uB9C8\uAC10(\uC2DC\uAC04\uCC3D \uCD94\uB860) 19:59:59",
    ))

    assert "270,000 (KRW per share)" in page.summary.text()
    assert "NOT_LIVE" in page.status.text()
    assert "TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW" in page.status.toolTip()
    page.close()
    app.processEvents()


def test_index_page_keeps_fitted_axes_on_resize_and_preserves_manual_view_until_new_selection():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndexPage()
    frame = pd.DataFrame({
        "date": pd.bdate_range("2026-01-02", periods=120),
        "close": np.linspace(6000.0, 7200.0, 120),
        "ma5": np.linspace(5975.0, 7175.0, 120),
        "ma20": np.linspace(5950.0, 7150.0, 120),
        "ma60": np.linspace(5900.0, 7100.0, 120),
        "ma120": np.linspace(5800.0, 7000.0, 120),
        "rsi14": np.linspace(35.0, 65.0, 120),
        "disparity60": np.linspace(96.0, 103.0, 120),
        "volume": np.linspace(100.0, 1000.0, 120),
    })
    page.resize(1600, 900)
    page.show()
    page.render(frame)
    QtTest.QTest.qWait(5)

    x = np.arange(len(frame), dtype=float)
    fitted_x, fitted_y = page.plot.getViewBox().viewRange()
    volume_x, volume_y = page.volume.getViewBox().viewRange()
    assert isinstance(page.plot.getAxis("bottom"), main_window_module.SessionDateAxisItem)
    assert isinstance(page.volume.getAxis("bottom"), main_window_module.SessionDateAxisItem)
    assert isinstance(page.indicator.getAxis("bottom"), main_window_module.SessionDateAxisItem)
    assert fitted_x[0] < x[0] < x[-1] < fitted_x[1]
    assert fitted_x[1] - fitted_x[0] < (x[-1] - x[0]) * 1.1
    assert fitted_y[0] < frame.ma120.min() < frame.close.max() < fitted_y[1]
    assert volume_y[0] == 0.0
    assert volume_y[1] > frame.volume.max()
    assert np.allclose(fitted_x, volume_x)
    normal_ticks = page._price_axis.tickValues(fitted_x[0], fitted_x[1], 1200)[0][1]
    assert len(normal_ticks) <= 12
    assert page._price_axis.tickStrings(normal_ticks, 1.0, 1.0)[0] == "2026-01-02"
    assert page._price_axis.tickStrings(normal_ticks, 1.0, 1.0)[-1] == frame.date.iloc[-1].date().isoformat()
    assert page._volume_axis.tickValues(fitted_x[0], fitted_x[1], 1200) == []
    assert page._indicator_axis.tickValues(fitted_x[0], fitted_x[1], 1200) == []

    page.resize(2200, 1200)
    QtTest.QTest.qWait(5)
    resized_x, resized_y = page.plot.getViewBox().viewRange()
    resized_volume_x, _ = page.volume.getViewBox().viewRange()
    assert resized_x[0] < x[0] < x[-1] < resized_x[1]
    assert resized_x[1] - resized_x[0] < (x[-1] - x[0]) * 1.1
    assert resized_y[0] < frame.ma120.min() < frame.close.max() < resized_y[1]
    assert np.allclose(resized_x, resized_volume_x)
    wide_ticks = page._price_axis.tickValues(resized_x[0], resized_x[1], 1900)[0][1]
    assert len(wide_ticks) <= 19
    assert len(wide_ticks) >= len(normal_ticks)

    page.plot.setRange(xRange=(x[30], x[80]), yRange=(6250.0, 6800.0), padding=0)
    page.volume.setYRange(0.0, 500.0, padding=0)
    page._manual_view = True  # Simulates a user zoom/pan after pyqtgraph's manual signal.
    manual_x, manual_y = page.plot.getViewBox().viewRange()
    manual_volume_y = page.volume.getViewBox().viewRange()[1]
    page.resize(1800, 1000)
    QtTest.QTest.qWait(5)
    assert np.allclose(page.plot.getViewBox().viewRange()[0], manual_x)
    assert np.allclose(page.plot.getViewBox().viewRange()[1], manual_y)
    assert np.allclose(page.volume.getViewBox().viewRange()[1], manual_volume_y)

    page._reset_view_and_request()
    page.render(frame.iloc[-60:].reset_index(drop=True))
    reset_x = page.plot.getViewBox().viewRange()[0]
    recent_x = np.arange(60, dtype=float)
    assert not page._manual_view
    assert reset_x[0] < recent_x[0] < recent_x[-1] < reset_x[1]
    assert reset_x[1] - reset_x[0] < (recent_x[-1] - recent_x[0]) * 1.1

    page.rsi.setCurrentText("Panel")
    page.render(frame.iloc[-60:].reset_index(drop=True))
    indicator_x = page.indicator.getViewBox().viewRange()[0]
    assert indicator_x[0] < 0.0 < 59.0 < indicator_x[1]
    assert indicator_x[1] - indicator_x[0] < 59.0 * 1.1
    scene_pos = page.plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(59.0, 7200.0))
    page._mouse_moved((scene_pos,))
    assert frame.date.iloc[-1].date().isoformat() in page.hover.text()
    assert "종 7,200.00" in page.hover.text()
    assert "거래량 1,000주" in page.hover.text()
    assert "RSI14 65.00" in page.hover.text()
    assert all(line.pos().x() == 59.0 for line in page.crosshairs)
    page.close()
    app.processEvents()


def _index_series_view(*, display: bool = True) -> IndexSeriesView:
    dates = pd.bdate_range(end="2026-08-19", periods=120)
    close = pd.Series(np.linspace(3000.0, 3120.0, 120))
    frame = pd.DataFrame({
        "date": dates, "symbol": "KOSPI", "open": close - 2,
        "high": close + 5, "low": close - 5, "close": close,
        "volume": np.linspace(400_000_000, 1_500_000_000, 120),
        "ma5": close.rolling(5).mean(), "ma20": close.rolling(20).mean(),
        "ma60": close.rolling(60).mean(), "ma120": close.rolling(120).mean(),
        "rsi14": np.linspace(35.0, 65.0, 120),
        "disparity60": np.linspace(96.0, 103.0, 120),
    })
    if not display:
        return IndexSeriesView.unavailable(
            "KOSPI", "코스피 종합지수", "KRX:KOSPI", "120D",
            "kr_index_daily", "지수 기준일이 기대 완료일보다 오래되었습니다.",
            freshness="STALE", expected_as_of="2026-08-19", source="pykrx",
            state=DashboardDisplayState.REFRESH_REQUIRED,
        )
    change = float(close.iloc[-1] - close.iloc[-2])
    return IndexSeriesView(
        index="KOSPI", name="코스피 종합지수", exact_identity="KRX:KOSPI",
        period="120D", dataset_id="kr_index_daily", frame=frame,
        display_state=DashboardDisplayState.VALUE, freshness="CURRENT",
        as_of="2026-08-19", expected_as_of="2026-08-19", source="pykrx",
        reference_kst="2026-08-19 KST 일봉 · 일중 기준시각 미보존",
        change=change, change_pct=change / float(close.iloc[-2]) * 100,
        period_high=float(frame.high.max()), period_low=float(frame.low.min()),
    )


def test_index_information_layer_maps_latest_legend_crosshair_leave_and_stale_state():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndexPage()
    page.rsi.setCurrentText("Panel")
    page.disparity.setCurrentText("Overlay")
    current = _index_series_view()
    page.render(current)

    assert "코스피 종합지수 · KRX:KOSPI · 120D" in page.index_summary.text()
    assert "최근 3,120.00포인트" in page.index_summary.text()
    assert "기간 고 3,125.00 / 저 2,995.00" in page.index_summary.text()
    assert "2026-08-19 KST 일봉 · 일중 기준시각 미보존" in page.index_meta.text()
    assert "지수 포인트" in page.index_meta.text()
    assert "pykrx" not in page.index_summary.text() + page.index_meta.text()
    assert "source=pykrx" in page.index_detail_button.toolTip()
    assert page.latest_value_marker is not None
    legend = page.index_legend.text()
    for label in ("종가", "MA5", "MA20", "MA60", "MA120", "거래량", "RSI14 (Panel)", "60일 괴리율 (Overlay)"):
        assert label in legend
    assert "#53d8fb" in legend and "#ff8fab" in legend

    scene_pos = page.plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(60.0, 3060.0))
    page._mouse_moved((scene_pos,))
    selected_text = page.hover.text()
    assert current.frame.date.iloc[60].date().isoformat() in selected_text
    assert all(label in selected_text for label in ("시 ", "고 ", "저 ", "종 ", "등락 ", "거래량 ", "MA60", "RSI14", "60일 괴리율"))
    page.leaveEvent(QtCore.QEvent(QtCore.QEvent.Leave))
    assert "2026-08-19" in page.hover.text() and "종 3,120.00" in page.hover.text()

    page.render(_index_series_view(display=False))
    visible = page.index_summary.text() + page.index_meta.text() + page.hover.text()
    assert "현재 숫자 표시 불가" in visible
    assert "3,120.00" not in visible
    assert page.latest_value_marker is None
    assert page._frame.empty
    page.close()
    app.processEvents()


def test_index_retained_stale_frame_stays_visible_with_prominent_warning():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndexPage()
    stale = replace(
        _index_series_view(),
        freshness="STALE",
        expected_as_of="2026-08-20",
        unavailable_reason=(
            "STALE retained history: as_of=2026-08-19, expected=2026-08-20; "
            "current-data claims and actions remain blocked."
        ),
    )

    page.render(stale)

    assert not page._frame.empty
    assert page.latest_value_marker is not None
    assert page.index_meta.text().startswith("STALE RETAINED HISTORY:")
    assert "expected=2026-08-20" in page.index_meta.text()
    assert "warning=STALE retained history" in page.index_detail_button.toolTip()
    page.close()
    app.processEvents()


def test_index_rendered_curves_keep_source_values_nan_breaks_and_state_after_resize():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndexPage()
    page.rsi.setCurrentText("Panel")
    view = _index_series_view()
    frame = view.frame.copy()
    frame.loc[60, "ma5"] = np.nan
    page.resize(1600, 900)
    page.show()
    page.render(replace(view, frame=frame))
    app.processEvents()

    price_items = {
        item.name(): item
        for item in page.plot.items()
        if isinstance(item, pg.PlotDataItem) and item.name()
    }
    indicator_items = {
        item.name(): item
        for item in page.indicator.items()
        if isinstance(item, pg.PlotDataItem) and item.name()
    }
    assert set(price_items) == {"종가", "MA5", "MA20", "MA60", "MA120"}
    assert set(indicator_items) == {"RSI14"}
    x, ma5 = price_items["MA5"].getData()
    assert np.array_equal(x, np.arange(120, dtype=float))
    assert np.allclose(ma5, frame["ma5"].to_numpy(dtype=float), equal_nan=True)
    connect = price_items["MA5"].curve.opts["connect"]
    assert connect[59] == 0 and connect[60] == 0
    assert connect[58] == 1 and connect[61] == 1
    assert all(item.curve.opts["antialias"] for item in (*price_items.values(), *indicator_items.values()))

    before = {
        name: tuple(np.array(values, copy=True) for values in item.getData())
        for name, item in price_items.items()
    }
    page.resize(2200, 1200)
    QtTest.QTest.qWait(5)
    for name, item in price_items.items():
        after_x, after_y = item.getData()
        assert np.array_equal(after_x, before[name][0])
        assert np.allclose(after_y, before[name][1], equal_nan=True)
    assert page.latest_value_marker is not None
    assert "#53d8fb" in page.index_legend.text()
    page.close()
    app.processEvents()


def test_index_overlay_indicators_use_independent_scales_exact_mapping_and_clean_states():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndexPage()
    view = _index_series_view()
    frame = view.frame.copy()
    frame.loc[40, "rsi14"] = np.nan
    frame.loc[50, "disparity60"] = np.nan
    view = replace(view, frame=frame)
    page.resize(1600, 900)
    page.show()
    page.render(view)
    app.processEvents()
    price_y_without_overlays = page.plot.getViewBox().viewRange()[1]

    page.rsi.setCurrentText("Overlay")
    page.disparity.setCurrentText("Overlay")
    page.render(view)
    app.processEvents()

    assert set(page._overlay_items) == {"rsi14", "disparity60"}
    assert set(page._overlay_guides) == {"rsi14", "disparity60"}
    assert page._rsi_overlay_axis.isVisible()
    assert page._disparity_overlay_axis.isVisible()
    assert "RSI14 (0–100)" in page._rsi_overlay_axis.labelText
    assert "0=100%" in page._disparity_overlay_axis.labelText
    assert np.allclose(page.plot.getViewBox().viewRange()[1], price_y_without_overlays)

    rsi_x, rsi_y = page._overlay_items["rsi14"].getData()
    disparity_x, disparity_y = page._overlay_items["disparity60"].getData()
    assert np.array_equal(rsi_x, np.arange(len(frame), dtype=float))
    assert np.allclose(rsi_y, frame["rsi14"].to_numpy(dtype=float), equal_nan=True)
    assert np.array_equal(disparity_x, rsi_x)
    assert np.allclose(
        disparity_y, frame["disparity60"].to_numpy(dtype=float) - 100.0,
        equal_nan=True,
    )
    assert page._overlay_items["rsi14"].curve.opts["connect"][39] == 0
    assert page._overlay_items["rsi14"].curve.opts["connect"][40] == 0
    assert page._overlay_items["disparity60"].curve.opts["connect"][49] == 0
    assert page._overlay_items["disparity60"].curve.opts["connect"][50] == 0
    assert [line.value() for line in page._overlay_guides["rsi14"]] == [30.0, 70.0]
    assert [line.value() for line in page._overlay_guides["disparity60"]] == [0.0]
    assert np.allclose(page._rsi_overlay_view.viewRange()[1], [0.0, 100.0])
    disparity_range = page._disparity_overlay_view.viewRange()[1]
    assert disparity_range[0] < 0.0 < disparity_range[1]
    assert np.isclose(abs(disparity_range[0]), abs(disparity_range[1]))

    page.plot.setXRange(25.0, 75.0, padding=0)
    app.processEvents()
    expected_x = page.plot.getViewBox().viewRange()[0]
    assert np.allclose(page._rsi_overlay_view.viewRange()[0], expected_x)
    assert np.allclose(page._disparity_overlay_view.viewRange()[0], expected_x)
    assert page._rsi_overlay_view.geometry() == page.plot.getViewBox().sceneBoundingRect()
    assert page._disparity_overlay_view.geometry() == page.plot.getViewBox().sceneBoundingRect()

    scene_pos = page.plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(60.0, 3060.0))
    page._mouse_moved((scene_pos,))
    assert f"RSI14 {float(frame.rsi14.iloc[60]):,.2f}" in page.hover.text()
    assert f"60일 괴리율 {float(frame.disparity60.iloc[60]):,.2f}" in page.hover.text()

    page.rsi.setCurrentText("Off")
    page.disparity.setCurrentText("Off")
    page._manual_view = False
    page.render(view)
    app.processEvents()
    assert page._overlay_items == {}
    assert page._overlay_guides == {}
    assert not page._rsi_overlay_axis.isVisible()
    assert not page._disparity_overlay_axis.isVisible()
    assert not page.indicator.isVisible()
    assert not any(
        isinstance(item, (pg.PlotDataItem, pg.InfiniteLine))
        for overlay in (page._rsi_overlay_view, page._disparity_overlay_view)
        for item in overlay.addedItems
    )
    page.close()
    app.processEvents()


def _equity_identity(
    symbol: str = "005930", name: str = "삼성전자", market: str = "KOSPI",
) -> EquityIdentity:
    return EquityIdentity(
        symbol=symbol, name=name, market=market, isin=f"KR7{symbol}003",
        listing_date="1975-06-11", security_type="보통주",
    )


def _us_etf_identity(symbol: str = "SPY") -> EquityIdentity:
    return next(identity for identity in US_ETF_CHART_IDENTITIES if identity.symbol == symbol)


def _blocked_us_etf_series_view(identity: EquityIdentity) -> EquitySeriesView:
    return EquitySeriesView(
        identity=identity, period="120D", frame=pd.DataFrame(),
        display_state=DashboardDisplayState.PROHIBITED, freshness="BLOCKED",
        as_of=None, expected_as_of="2026-08-19",
        source="yahoo_chart_api; accepted local scope: SOXX only",
        reference_kst=None, price_mode="provider-native original OHLCV; USD",
        unavailable_reason=(
            f"{identity.symbol} is outside the authorized local symbol scope; "
            "the current accepted lane is SOXX-only."
        ),
    )


def _equity_series_view(
    identity: EquityIdentity, *, display: bool = True,
) -> EquitySeriesView:
    if not display:
        return EquitySeriesView(
            identity=identity, period="120D", frame=pd.DataFrame(),
            display_state=DashboardDisplayState.REFRESH_REQUIRED,
            freshness="STALE", as_of=None, expected_as_of="2026-08-19",
            source="fixture", reference_kst=None,
            unavailable_reason="가격 기준일이 기대 완료일보다 오래되었습니다.",
        )
    dates = pd.bdate_range(end="2026-08-19", periods=120)
    close = pd.Series(np.linspace(70_000, 76_000, 120))
    frame = pd.DataFrame({
        "date": dates, "open": close - 50, "high": close + 100,
        "low": close - 100, "close": close,
        "volume": np.linspace(1_000_000, 2_000_000, 120),
        "ma5": close.rolling(5).mean(), "ma20": close.rolling(20).mean(),
        "ma60": close.rolling(60).mean(), "ma120": close.rolling(120).mean(),
        "rsi14": np.linspace(40, 60, 120),
        "disparity60": np.linspace(96, 103, 120),
    })
    return EquitySeriesView(
        identity=identity, period="120D", frame=frame,
        display_state=DashboardDisplayState.VALUE, freshness="CURRENT",
        as_of="2026-08-19", expected_as_of="2026-08-19", source="fixture",
        reference_kst="2026-08-19 KST 일봉 · 정확한 시각 미보존",
        change=50.0, change_pct=0.07, period_high=float(frame.high.max()),
        period_low=float(frame.low.min()),
    )


@pytest.mark.parametrize(
    ("universe", "example_query", "identity"),
    [
        ("KR_EQUITY", "005930", _equity_identity()),
        ("US_ETF", "SOXX", _us_etf_identity("SPY")),
    ],
)
def test_equity_empty_state_keeps_search_primary_without_automatic_series(
    universe, example_query, identity, tmp_path,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage(universe=universe)
    page.resize(1600, 900)
    page.show()
    app.processEvents()
    searches = []
    series_requests = []
    page.search_requested.connect(searches.append)
    page.series_requested.connect(lambda *request: series_requests.append(request))

    assert not page.empty_state.isHidden()
    assert page.empty_state.accessibleName()
    assert page.guided_search_button.accessibleName()
    assert page.guided_search_button.focusPolicy() != QtCore.Qt.NoFocus
    assert page.empty_direct_search_button.accessibleName()
    assert page.empty_direct_search_button.focusPolicy() != QtCore.Qt.NoFocus
    assert page.search_input.isVisible() and page.search_button.isVisible()
    for widget in page._chart_workspace_widgets:
        assert not widget.isVisible()
    assert 80 <= page.empty_state.height() <= page.empty_state.sizeHint().height()
    assert page.empty_state.geometry().bottom() < 300

    capture = page.grab()
    capture_path = tmp_path / f"{universe.lower()}-empty-chart-1600x900.png"
    assert capture.size() == QtCore.QSize(1600, 900)
    assert capture.save(str(capture_path))

    page.search_input.setFocus()
    QtTest.QTest.keyClick(page.search_input, QtCore.Qt.Key_Tab)
    assert page.search_button.hasFocus()
    QtTest.QTest.keyClick(page.search_button, QtCore.Qt.Key_Tab)
    assert page.search_results.hasFocus()
    QtTest.QTest.keyClick(page.search_results, QtCore.Qt.Key_Tab)
    assert page.guided_search_button.hasFocus()
    QtTest.QTest.keyClick(page.guided_search_button, QtCore.Qt.Key_Space)
    app.processEvents()
    assert page.search_input.text() == example_query
    assert searches == [example_query]
    assert series_requests == []
    assert page.empty_state.isVisible()

    QtTest.QTest.keyClick(page.guided_search_button, QtCore.Qt.Key_Tab)
    assert page.empty_direct_search_button.hasFocus()
    QtTest.QTest.keyClick(page.empty_direct_search_button, QtCore.Qt.Key_Space)
    app.processEvents()
    assert page.search_input.hasFocus()

    page.search_button.setEnabled(True)
    page.search_input.setText("direct query")
    page.search_button.click()
    app.processEvents()
    assert searches == [example_query, "direct query"]
    assert series_requests == []

    page.render_search(EquitySearchView(
        "direct query", (), "LOCAL_CATALOG_UNAVAILABLE",
    ))
    assert page.search_feedback.isVisible()
    assert page.search_feedback.text() == "LOCAL_CATALOG_UNAVAILABLE"
    page.render_search(EquitySearchView("direct query", (identity,)))
    assert not page.search_feedback.isVisible()
    page.search_results.setCurrentIndex(1)
    page.open_button.click()
    app.processEvents()
    assert series_requests == [(identity, "120D")]
    assert not page.empty_state.isVisible()
    assert page.plot.isVisible() and page.workspace.isVisible()
    page.close()
    app.processEvents()


@pytest.mark.parametrize(
    ("universe", "kind", "identity"),
    [
        ("KR_EQUITY", "equity", _equity_identity()),
        ("US_ETF", "us_etf", _us_etf_identity("SPY")),
    ],
)
def test_equity_selected_unavailable_and_detached_states_keep_workspace(
    universe, kind, identity,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = IndividualEquityPage(universe=universe)
    source.begin_series(identity)
    unavailable = (
        _equity_series_view(identity, display=False)
        if universe == "KR_EQUITY"
        else _blocked_us_etf_series_view(identity)
    )
    source.render_series(unavailable)
    source.resize(1600, 900)
    source.show()
    app.processEvents()

    assert not source.empty_state.isVisible()
    assert source.plot.isVisible() and source.reload_button.isVisible()
    assert source._frame.empty
    detached = DetachedChartWindow(kind, SimpleNamespace(), source)
    detached.show()
    app.processEvents()
    assert detached.page._selected_identity == identity
    assert not detached.page.empty_state.isVisible()
    assert detached.page.plot.isVisible()
    assert detached.page._frame.empty

    detached.close()
    source.close()
    app.processEvents()
    assert detached._thread is None


@pytest.mark.parametrize(
    ("universe", "kind", "identity"),
    [
        ("KR_EQUITY", "equity", _equity_identity()),
        ("US_ETF", "us_etf", _us_etf_identity("SPY")),
    ],
)
def test_equity_search_feedback_never_replaces_selected_chart_status(
    universe, kind, identity,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage(universe=universe)
    page.resize(1600, 900)
    page.show()
    app.processEvents()
    page.begin_series(identity)
    loading_status = page.status.text()

    page.search_input.setText("unrelated")
    page.render_search(EquitySearchView(
        "unrelated", (), "LOCAL_IDENTITY_SEARCH_FAILED",
    ))

    assert page.search_feedback.isVisible()
    assert page.search_feedback.accessibleName() == "종목 검색 결과 상태"
    assert page.search_feedback.text() == "LOCAL_IDENTITY_SEARCH_FAILED"
    assert page.status.text() == loading_status

    page.search_input.setText("retry")
    page._request_search()
    assert not page.search_feedback.isVisible()
    assert page.status.text() == loading_status

    page.search_input.setText("empty")
    page.render_search(EquitySearchView("empty", ()))
    assert page.search_feedback.isVisible()
    assert page.search_feedback.text() == "일치하는 로컬 검색 결과가 없습니다."
    assert page.status.text() == loading_status

    page.render_series(_equity_series_view(identity))
    accepted_view = page._series_view
    page._request_identity(identity)
    reload_failure = (
        _equity_series_view(identity, display=False)
        if universe == "KR_EQUITY"
        else _blocked_us_etf_series_view(identity)
    )
    page.render_series(reload_failure)
    retained_failure_status = page.status.text()
    retained_failure_tooltip = page.status.toolTip()
    assert page._series_view is accepted_view
    assert "새로고침 실패" in retained_failure_status
    assert "accepted chart" in retained_failure_tooltip
    page.search_input.setText("failed after chart")
    page.render_search(EquitySearchView(
        "failed after chart", (), "LOCAL_CATALOG_UNAVAILABLE",
    ))
    assert page.search_feedback.isVisible()
    assert page.status.text() == retained_failure_status
    assert page.status.toolTip() == retained_failure_tooltip

    detached = DetachedChartWindow(kind, SimpleNamespace(), page)
    detached.show()
    app.processEvents()
    assert detached.page._selected_identity == identity
    assert detached.page._series_view is not None
    assert detached.page._series_view.displays_values
    assert detached.page.search_feedback.isVisible()
    assert detached.page.search_feedback.text() == "LOCAL_CATALOG_UNAVAILABLE"
    assert detached.page.status.text() == retained_failure_status
    assert detached.page.status.toolTip() == retained_failure_tooltip

    detached.close()
    page.close()
    app.processEvents()
    assert detached._thread is None


def test_individual_equity_page_requires_explicit_identity_and_clears_prior_symbol_state():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage()
    page.resize(1600, 900); page.show(); app.processEvents()
    samsung = _equity_identity()
    kakao = _equity_identity("035720", "카카오", "KOSDAQ")
    requests = []
    page.series_requested.connect(lambda identity, period: requests.append((identity, period)))
    page.search_input.setText("삼성")
    page.render_search(EquitySearchView("삼성", (samsung, kakao)))

    assert page.search_results.currentData() is None
    assert not page.open_button.isEnabled()
    page.search_results.setCurrentIndex(1)
    page.open_button.click()
    app.processEvents()

    assert requests == [(samsung, "120D")]
    assert page._frame.empty
    page.render_series(_equity_series_view(samsung))
    assert not page._frame.empty
    assert "삼성전자 · 005930 · KOSPI" in page.summary.text()
    assert "원본(미조정) OHLCV" in page.status.text()
    assert any(isinstance(item, CandlestickItem) for item in page.plot.items())
    page.volume.getAxis("left").tickValues(0, 2_000_000, 180)
    assert page.volume.getAxis("left").labelText == "거래량(만주)"

    page.begin_series(kakao)
    assert page._frame.empty
    assert "삼성전자" not in page.summary.text()
    assert "76,000" not in page.summary.text()
    assert "가격·지표·툴팁을 초기화" in page.status.text()
    page.render_series(_equity_series_view(kakao, display=False))
    assert page._frame.empty
    assert "이전 종목 상태 초기화 완료" in page.hover.text()
    assert "76,000" not in page.summary.text()
    page.close()
    app.processEvents()


@pytest.mark.parametrize("universe", ["KR_EQUITY", "US_ETF"])
def test_same_identity_reload_preserves_accepted_chart_on_pending_and_failure(
    universe,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage(universe=universe)
    identity = _equity_identity() if universe == "KR_EQUITY" else _us_etf_identity("TQQQ")
    accepted = _equity_series_view(identity)
    page.begin_series(identity)
    page.render_series(accepted)
    page.plot.setXRange(20.0, 60.0, padding=0)
    page._manual_view = True
    page._add_measurement_point(10)
    page._add_measurement_point(20)
    accepted_frame = page._frame.copy()
    accepted_summary = page.summary.text()
    accepted_measurement = page.measurement.text()
    accepted_range = page.plot.getViewBox().viewRange()[0]
    benchmark_id = "KOSPI" if universe == "KR_EQUITY" else "SPY"
    comparison = NormalizedBenchmarkComparisonView(
        target=identity, benchmark_id=benchmark_id,
        benchmark_label=benchmark_id, period="120D",
        common_start="2026-03-05", target_as_of="2026-08-19",
        benchmark_as_of="2026-08-19", target_freshness="CURRENT",
        benchmark_freshness="CURRENT", currency=identity.currency or "KRW",
        target_price_basis="PROVIDER_NATIVE_ORIGINAL_PRICE",
        benchmark_price_basis="KRX_INDEX_LEVEL" if universe == "KR_EQUITY" else "PROVIDER_NATIVE_ORIGINAL_PRICE",
        display_state=DashboardDisplayState.VALUE,
        frame=pd.DataFrame({
            "date": accepted_frame.date.iloc[[0, 1, 3]].reset_index(drop=True),
            "target_position": [0.0, 1.0, 3.0],
            "target_normalized": [100.0, 102.0, 104.0],
            "benchmark_normalized": [100.0, 101.0, 103.0],
        }),
    )
    comparison_requests = []
    page.comparison_requested.connect(comparison_requests.append)
    page.comparison_toggle.setChecked(True)
    page.render_comparison(comparison)
    comparison_requests.clear()
    accepted_comparison_summary = page.comparison_summary.text()
    requests = []
    page.series_requested.connect(
        lambda requested_identity, period: requests.append(
            (requested_identity, period)
        )
    )

    page.reload_button.click()
    app.processEvents()

    assert requests == [(identity, "120D")]
    assert page._series_view is accepted
    assert page._frame.equals(accepted_frame)
    assert page.summary.text() == accepted_summary
    assert page.measurement.text() == accepted_measurement
    assert np.allclose(page.plot.getViewBox().viewRange()[0], accepted_range)
    assert page._comparison_view is comparison
    assert page.comparison_summary.text() == accepted_comparison_summary
    assert page.comparison_toggle.isChecked()
    assert "기존 검증 차트를 유지" in page.status.text()
    assert not page.reload_button.isEnabled()

    failure = (
        _equity_series_view(identity, display=False)
        if universe == "KR_EQUITY"
        else _blocked_us_etf_series_view(identity)
    )
    page.render_series(failure)
    app.processEvents()

    assert page._series_view is accepted
    assert page._frame.equals(accepted_frame)
    assert page.summary.text() == accepted_summary
    assert page.measurement.text() == accepted_measurement
    assert np.allclose(page.plot.getViewBox().viewRange()[0], accepted_range)
    assert page._comparison_view is comparison
    assert page.comparison_summary.text() == accepted_comparison_summary
    assert page.comparison_toggle.isChecked()
    assert "새로고침 실패" in page.status.text()
    assert "기존 검증 차트 유지" in page.status.text()
    assert page.reload_button.isEnabled()

    replacement_frame = accepted.frame.copy()
    replacement_frame.loc[
        replacement_frame.index[20], ["open", "high", "low", "close"]
    ] += 1_000.0
    replacement_frame.loc[replacement_frame.index[-1], ["open", "high", "low", "close"]] = (
        79_950.0, 80_100.0, 79_900.0, 80_000.0,
    )
    replacement = replace(
        accepted, frame=replacement_frame, freshness="CURRENT_RELOADED",
        change=4_050.0, change_pct=5.33, period_high=80_100.0,
    )
    page.reload_button.click()
    page.render_series(replacement)
    app.processEvents()
    assert page._series_view is replacement
    assert page._frame.equals(replacement.frame.reset_index(drop=True))
    assert page.summary.text() != accepted_summary
    assert "80,000" in page.summary.text()
    assert page.measurement.text() != accepted_measurement
    assert "+1,504.20" in page.measurement.text()
    assert np.allclose(page.plot.getViewBox().viewRange()[0], accepted_range)
    assert page._comparison_view is None
    assert len(comparison_requests) == 1
    assert comparison_requests[0] is replacement

    other_identity = (
        _equity_identity("035720", "카카오", "KOSDAQ")
        if universe == "KR_EQUITY" else _us_etf_identity("QQQ")
    )
    page._request_identity(other_identity)
    app.processEvents()
    assert requests[-1] == (other_identity, "120D")
    assert page._series_view is None
    assert page._frame.empty
    assert "80,000" not in page.summary.text()
    assert page._comparison_view is None
    assert not page.comparison_toggle.isChecked()
    assert page.measurement.text() != accepted_measurement
    page.close()
    app.processEvents()


@pytest.mark.parametrize("universe", ["KR_EQUITY", "US_ETF"])
def test_same_identity_reload_clears_measurement_when_saved_indices_disappear(
    universe,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage(universe=universe)
    identity = _equity_identity() if universe == "KR_EQUITY" else _us_etf_identity("TQQQ")
    accepted = _equity_series_view(identity)
    page.begin_series(identity)
    page.render_series(accepted)
    page._add_measurement_point(110)
    page._add_measurement_point(119)
    assert page._measurement_points == [110, 119]

    shorter_frame = accepted.frame.iloc[:100].reset_index(drop=True)
    replacement = replace(
        accepted,
        frame=shorter_frame,
        freshness="CURRENT_RELOADED",
        period_high=float(shorter_frame.high.max()),
        period_low=float(shorter_frame.low.min()),
    )
    page._request_identity(identity)
    page.render_series(replacement)
    app.processEvents()

    assert page._series_view is replacement
    assert page._measurement_points == []
    assert page.measurement.text() == "측정: 두 관측값을 선택하세요"
    page.close()
    app.processEvents()


def test_individual_equity_retained_stale_frame_stays_visible_with_warning():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage()
    identity = _equity_identity()
    page._selected_identity = identity
    stale = replace(
        _equity_series_view(identity),
        freshness="STALE",
        expected_as_of="2026-08-20",
        unavailable_reason=(
            "STALE retained history: as_of=2026-08-19, expected=2026-08-20; "
            "current-data claims and actions remain blocked."
        ),
    )

    page.render_series(stale)

    assert not page._frame.empty
    assert page.status.text().startswith("STALE RETAINED HISTORY:")
    assert "expected=2026-08-20" in page.status.text()
    assert "warning=STALE retained history" in page.status.toolTip()
    page.close()
    app.processEvents()


def test_security_workspace_tabs_are_identity_bound_numeric_free_and_restore_chart_state():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage()
    page.resize(1600, 900); page.show(); app.processEvents()
    identity = _equity_identity()
    requests = []
    page.series_requested.connect(lambda *value: requests.append(value))
    page._selected_identity = identity
    page.render_series(_equity_series_view(identity))
    expected = f"{identity.name} · {identity.market}:{identity.symbol}"
    assert all(expected in label.text() for label in (
        page.workspace_info, page.workspace_dividend, page.workspace_option, page.workspace_watchlist,
    ))
    assert "76,000" not in page.workspace_dividend.text() + page.workspace_option.text()
    page.workspace.setCurrentIndex(3); app.processEvents(); assert requests == []
    page.plot.setXRange(8, 22, padding=0); page.plot.setYRange(75_000, 77_000, padding=0)
    ranges = page.plot.getViewBox().viewRange()
    page.workspace.setFocus(); page._manual_view = True
    settings = page.indicator_panel.settings()
    page.large_chart_button.setChecked(True); page.large_chart_button.setChecked(False); app.processEvents()
    assert page._selected_identity.key == identity.key
    assert page.workspace.currentIndex() == 3 and page._manual_view and page.indicator_panel.settings() == settings
    assert page.plot.getViewBox().viewRange() == ranges
    assert app.focusWidget() is page.workspace.tabBar()
    page.close(); app.processEvents()


def test_individual_equity_page_crosshair_reports_exact_ohlcv_and_enabled_indicators():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage()
    identity = _equity_identity()
    page._selected_identity = identity
    page.rsi.setCurrentText("Panel")
    page.disparity.setCurrentText("Panel")
    page.resize(1600, 900)
    page.show()
    page.render_series(_equity_series_view(identity))
    QtTest.QTest.qWait(5)

    scene_pos = page.plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(119.0, 76_000.0))
    page._mouse_moved((scene_pos,))

    assert "2026-08-19" in page.hover.text()
    assert "시 75,950" in page.hover.text()
    assert "종 76,000" in page.hover.text()
    assert "거래량 2,000,000주" in page.hover.text()
    assert "RSI14 60.00" in page.hover.text()
    assert "괴리60 103.00" in page.hover.text()
    assert all(line.pos().x() == 119.0 for line in page.crosshairs)
    page.close()
    app.processEvents()


def test_individual_equity_common_base_comparison_keeps_session_crosshair_and_detached_copy():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    identity = _equity_identity()
    source = IndividualEquityPage()
    source.resize(1600, 900)
    source.show()
    source._selected_identity = identity
    source.render_series(_equity_series_view(identity))
    comparison = NormalizedBenchmarkComparisonView(
        target=identity, benchmark_id="KOSPI", benchmark_label="KOSPI (KRX:KOSPI)",
        period="120D", common_start="2026-03-05", target_as_of="2026-08-19",
        benchmark_as_of="2026-08-19", target_freshness="CURRENT",
        benchmark_freshness="CURRENT", currency="KRW",
        target_price_basis="PROVIDER_NATIVE_ORIGINAL_PRICE",
        benchmark_price_basis="KRX_INDEX_LEVEL", display_state=DashboardDisplayState.VALUE,
        frame=pd.DataFrame({
            "date": source._frame.date.iloc[[0, 1, 3]].reset_index(drop=True),
            "target_position": [0.0, 1.0, 3.0],
            "target_normalized": [100.0, 102.0, 104.0],
            "benchmark_normalized": [100.0, 101.0, 103.0],
        }),
    )
    source.comparison_toggle.setChecked(True)
    source.render_comparison(comparison)
    QtTest.QTest.qWait(5)

    assert source.comparison_panel.isVisible()
    assert source.comparison_plot.isVisible()
    assert len(source._comparison_items) == 3
    assert "시작 2026-03-05" in source.comparison_summary.text()
    assert "원본 가격 / 지수 레벨" in source.comparison_summary.text()
    assert source._comparison_axis.tickValues(-1.0, 5.0, 1200) == []
    scene_pos = source.plot.getPlotItem().vb.mapViewToScene(QtCore.QPointF(3.0, 70_150.0))
    source._mouse_moved((scene_pos,))
    assert source._comparison_crosshair.pos().x() == 3.0
    assert "공통100 005930 104.00" in source.hover.text()
    assert "KOSPI 103.00" in source.hover.text()

    detached = DetachedChartWindow("equity", SimpleNamespace(), source)
    assert detached.page.comparison_toggle.isChecked()
    assert detached.page._comparison_view is not None
    assert detached.page._comparison_view.frame.equals(comparison.frame)
    assert detached.page._comparison_view.frame is not comparison.frame
    detached.close()
    source.close()
    app.processEvents()
    assert detached._thread is None


def test_detached_equity_preserves_local_timeframe_measurement_and_manual_range():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    identity = _equity_identity()
    source = IndividualEquityPage()
    source.resize(1600, 900); source.show()
    source._selected_identity = identity
    source.render_series(_equity_series_view(identity))
    source.timeframe.setCurrentText("월봉")
    app.processEvents()
    source._show_observation(0)
    source.add_measurement_button.click()
    source._show_observation(2)
    source.add_measurement_button.click()
    source.plot.setXRange(0.0, 3.0, padding=0)
    source._manual_view = True
    source_range = source.plot.getViewBox().viewRange()[0]

    detached = DetachedChartWindow("equity", SimpleNamespace(), source)
    detached.show(); app.processEvents()

    assert detached.page.timeframe.currentText() == "월봉"
    assert detached.page._measurement_points == [0, 2]
    assert detached.page.measurement.text() == source.measurement.text()
    assert detached.page._manual_view
    assert np.allclose(detached.page.plot.getViewBox().viewRange()[0], source_range)
    detached.close(); source.close(); app.processEvents()
    assert detached._thread is None


def test_watchlist_page_empty_populated_fail_closed_and_exact_open_actions():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = WatchlistPage()
    empty = WatchlistState((NamedWatchlist(DEFAULT_LIST_ID, "관심종목"),))
    page.render(empty)
    assert page.table.rowCount() == 0
    assert "종목 검색 또는 차트에서" in page.notice.text()

    samsung = _equity_identity()
    kakao = _equity_identity("035720", "카카오", "KOSDAQ")
    state = WatchlistState((NamedWatchlist(
        DEFAULT_LIST_ID,
        "관심종목",
        (WatchlistItem(samsung, "2026-08-20T16:00:00+09:00"),
         WatchlistItem(kakao, "2026-08-20T16:01:00+09:00")),
    ),))
    quotes = (
        WatchlistQuote(samsung, 76_000, 50, 0.07, "2026-08-19 KST 일봉", "CURRENT"),
        WatchlistQuote(kakao, None, None, None, None, "STALE", "기준일이 오래되었습니다."),
    )
    opened = []
    page.open_requested.connect(lambda identity, detached: opened.append((identity, detached)))
    page.render(state, quotes)

    assert page.table.rowCount() == 2
    assert "삼성전자 · 005930 · KOSPI" in page.table.item(0, 0).text()
    assert "76,000원" in page.table.item(0, 1).text()
    assert "2026-08-19 KST" in page.table.item(0, 2).text()
    assert page.table.item(1, 1).text() == "가격·등락 숨김"
    assert "기준일이 오래되었습니다" in page.table.item(1, 2).text()
    assert "76000" not in page.table.item(1, 1).text()
    page._open_row(1, 0)
    assert opened == [(kakao, False)]
    page.close()
    app.processEvents()


def test_search_and_chart_stars_use_selected_named_list_without_identity_ambiguity():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage()
    samsung = _equity_identity()
    second = NamedWatchlist("second", "반도체")
    state = WatchlistState((NamedWatchlist(DEFAULT_LIST_ID, "관심종목"), second))
    page.set_watchlists(state)
    page.search_input.setText("삼성")
    page.render_search(EquitySearchView("삼성", (samsung,)))
    page.search_results.setCurrentIndex(1)
    page.favorite_target.setCurrentIndex(page.favorite_target.findData("second"))
    toggles = []
    page.favorite_toggled.connect(lambda *args: toggles.append(args))
    page.search_favorite_button.click()
    page.begin_series(samsung)
    page.chart_favorite_button.click()

    assert toggles == [(samsung, "second", True), (samsung, "second", True)]
    populated = WatchlistState((
        NamedWatchlist(DEFAULT_LIST_ID, "관심종목"),
        NamedWatchlist("second", "반도체", (WatchlistItem(samsung, "fixture"),)),
    ))
    page.set_watchlists(populated)
    assert page.search_favorite_button.text().startswith("★")
    assert page.chart_favorite_button.text().startswith("★")
    page.close()
    app.processEvents()


def test_us_etf_page_is_dedicated_exact_identity_and_numeric_free_when_blocked():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = IndividualEquityPage(universe="US_ETF")
    spy = _us_etf_identity()
    requests = []
    page.series_requested.connect(lambda identity, period: requests.append((identity, period)))
    page.search_input.setText("SPY")
    page.render_search(EquitySearchView("SPY", (spy,)))

    assert page.universe == "US_ETF"
    assert "ETF" in page.title.text()
    assert page.search_results.currentData() is None
    page.search_results.setCurrentIndex(1)
    page.open_button.click()
    app.processEvents()
    assert requests == [(spy, "120D")]

    blocked = _blocked_us_etf_series_view(spy)
    page.render_series(blocked)
    assert page._frame.empty
    assert page.summary.text() == spy.display_label
    assert blocked.unavailable_reason == page.status.text()
    assert "SOXX-only" in page.status.text()
    assert "identity_source=https://www.ssga.com/" in page.status.toolTip()
    assert "$" not in page.summary.text()
    assert "SPY" not in [page.index.itemText(index) for index in range(page.index.count())]
    comparison_requests = []
    page.comparison_requested.connect(comparison_requests.append)
    page.comparison_toggle.setChecked(True)
    assert comparison_requests == [blocked]
    page.render_comparison(NormalizedBenchmarkComparisonView.unavailable(
        spy, "120D", "No numeric comparison is available; no benchmark file was read.",
        benchmark_id="SP500_OR_NASDAQ100",
        benchmark_label="S&P 500 (SP500) or Nasdaq-100 (NASDAQ100)",
        currency="USD", target_freshness="BLOCKED",
    ))
    assert not page.comparison_panel.isHidden()
    assert page.comparison_plot.isHidden()
    assert "S&P 500" in page.comparison_summary.toolTip()
    assert "currency=USD" in page.comparison_summary.toolTip()
    page.close()
    app.processEvents()


def test_us_etf_watchlist_worker_routes_each_market_to_its_dedicated_service():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    samsung = _equity_identity()
    spy = _us_etf_identity()
    calls = []

    service = SimpleNamespace(
        equity=SimpleNamespace(series=lambda identity, period: (
            calls.append(("KR", identity.symbol, period)) or _equity_series_view(identity)
        )),
        us_etf=SimpleNamespace(series=lambda identity, period: (
            calls.append(("US", identity.symbol, period)) or _blocked_us_etf_series_view(identity)
        )),
    )
    completed = []
    worker = EquityChartWorker(service, "watchlist", (samsung, spy))
    worker.completed.connect(lambda *result: completed.append(result))
    worker.run()
    app.processEvents()

    assert calls == [("KR", "005930", "20D"), ("US", "SPY", "20D")]
    quotes = completed[0][2]
    assert quotes[0].price is not None
    assert quotes[1].price is None and quotes[1].unavailable_reason.endswith("SOXX-only.")


def test_us_etf_watchlist_uses_native_usd_format_without_krw_conversion():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    spy = _us_etf_identity()
    state = WatchlistState((NamedWatchlist(
        DEFAULT_LIST_ID,
        "favorites",
        (WatchlistItem(spy, "2026-08-20T20:30:00+09:00"),),
    ),))
    quote = WatchlistQuote(
        spy, 645.12, 1.25, 0.19, "2026-08-19 U.S. session · KST display", "CURRENT",
    )
    page = WatchlistPage()
    page.render(state, (quote,))

    assert page.table.item(0, 1).text() == "$645.12 · $+1.25 (+0.19%)"
    assert "KRW" not in page.table.item(0, 1).text()
    page.close()
    app.processEvents()


def test_detached_us_etf_window_clones_universe_and_keeps_blocked_state_independent():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    spy = _us_etf_identity()
    source = IndividualEquityPage(universe="US_ETF")
    source._selected_identity = spy
    source.render_series(_blocked_us_etf_series_view(spy))
    service = SimpleNamespace(
        equity=SimpleNamespace(),
        us_etf=SimpleNamespace(
            search=lambda query: EquitySearchView(query, (spy,)),
            series=lambda identity, period: _blocked_us_etf_series_view(identity),
        ),
    )

    detached = DetachedChartWindow("us_etf", service, source)
    detached.show()
    app.processEvents()

    assert detached.kind == "us_etf"
    assert detached.page.universe == "US_ETF"
    assert detached.page is not source
    assert detached.page._selected_identity == spy
    assert "SPY" in detached.windowTitle()
    assert detached.page._frame.empty and "SOXX-only" in detached.page.status.text()
    source.search_input.setText("changed only in source")
    assert detached.page.search_input.text() != source.search_input.text()

    detached.close()
    source.close()
    app.processEvents()
    assert detached._thread is None


def test_equity_search_worker_runs_off_gui_thread_and_closes_cleanly():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    worker_threads = []
    completed = []

    def search(query):
        worker_threads.append(threading.get_ident())
        return EquitySearchView(query, (_equity_identity(),))

    service = SimpleNamespace(search=search)
    thread = QtCore.QThread()
    worker = EquityChartWorker(service, "search", "삼성")
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.completed.connect(lambda *result: completed.append(result))
    worker.completed.connect(thread.quit)
    main_thread = threading.get_ident()
    thread.start()
    for _ in range(100):
        app.processEvents()
        if not thread.isRunning() and completed:
            break
        QtTest.QTest.qWait(5)

    assert worker_threads and worker_threads[0] != main_thread
    assert completed[0][0] == "search"
    assert completed[0][2].matches == (_equity_identity(),)
    assert thread.wait(1_000)


def _detached_index_frame() -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-08-19", periods=120)
    close = pd.Series(np.linspace(6000.0, 7200.0, 120))
    return pd.DataFrame({
        "date": dates,
        "close": close,
        "ma5": close.rolling(5).mean(),
        "ma20": close.rolling(20).mean(),
        "ma60": close.rolling(60).mean(),
        "ma120": close.rolling(120).mean(),
        "rsi14": np.linspace(35.0, 65.0, 120),
        "disparity60": np.linspace(96.0, 103.0, 120),
        "volume": np.linspace(100.0, 1000.0, 120),
    })


def test_detached_index_and_equity_windows_clone_then_keep_all_state_independent():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    index_source = IndexPage()
    index_source.index.setCurrentText("KOSDAQ")
    index_source.period.setCurrentText("60D")
    index_source.rsi.setCurrentText("Panel")
    index_source.render(_detached_index_frame().tail(60).reset_index(drop=True))
    index_source.plot.setXRange(10.0, 40.0, padding=0)
    index_source._manual_view = True
    index_source.crosshairs[0].setPos(32.0)
    index_source.crosshairs[0].show()

    equity_source = IndividualEquityPage()
    identity = _equity_identity()
    equity_source._selected_identity = identity
    equity_source.disparity.setCurrentText("Panel")
    equity_source.render_series(_equity_series_view(identity))

    service = SimpleNamespace(
        index=SimpleNamespace(
            chart_view=lambda _index, _period: replace(
                _index_series_view(), index=_index, name=_index,
                exact_identity=f"KRX:{_index}", period=_period,
                frame=_detached_index_frame(),
            )
        ),
        equity=SimpleNamespace(
            search=lambda query: EquitySearchView(query, (identity,)),
            series=lambda _identity, _period: _equity_series_view(_identity),
        ),
    )
    detached_index = DetachedChartWindow("index", service, index_source)
    detached_equity = DetachedChartWindow("equity", service, equity_source)
    detached_index.show()
    detached_equity.show()
    app.processEvents()

    assert detached_index.page.index.currentText() == "KOSDAQ"
    assert detached_index.page.period.currentText() == "60D"
    assert detached_index.page.rsi.currentText() == "Panel"
    assert detached_index.page._manual_view
    assert np.allclose(detached_index.page.plot.getViewBox().viewRange()[0], (10.0, 40.0))
    assert detached_index.page.crosshairs[0].pos().x() == 32.0
    assert "KOSDAQ · 60D" in detached_index.windowTitle()
    assert "2026-08-19 KST 일봉" in detached_index.windowTitle()

    assert detached_equity.page._selected_identity == identity
    assert detached_equity.page.disparity.currentText() == "Panel"
    assert "삼성전자 · 005930 · KOSPI" in detached_equity.windowTitle()
    assert "2026-08-19 KST 일봉" in detached_equity.windowTitle()

    index_source.period.setCurrentText("1Y")
    equity_source.rsi.setCurrentText("Overlay")
    detached_index.page.rsi.setCurrentText("Off")
    detached_equity.page.disparity.setCurrentText("Off")
    detached_index.page.crosshairs[0].setPos(15.0)
    app.processEvents()

    assert detached_index.page.period.currentText() == "60D"
    assert index_source.rsi.currentText() == "Panel"
    assert detached_equity.page.rsi.currentText() != equity_source.rsi.currentText()
    assert equity_source.disparity.currentText() == "Panel"
    assert index_source.crosshairs[0].pos().x() == 32.0

    destroyed: list[str] = []
    detached_index.destroyed.connect(lambda: destroyed.append("index"))
    detached_equity.destroyed.connect(lambda: destroyed.append("equity"))
    detached_index.close()
    detached_equity.close()
    index_source.close()
    equity_source.close()
    app.processEvents()
    assert set(destroyed) == {"index", "equity"}


def test_detached_chart_workers_are_off_thread_and_failures_do_not_cross_windows():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = IndexPage()
    source.render(_detached_index_frame())
    worker_threads: list[int] = []

    def chart_view(index, period):
        worker_threads.append(threading.get_ident())
        time.sleep(0.01)
        if index == "KOSDAQ":
            raise ValueError("fixture read failure")
        return replace(
            _index_series_view(), index=index, name=index,
            exact_identity=f"KRX:{index}", period=period,
            frame=_detached_index_frame(),
        )

    service = SimpleNamespace(
        index=SimpleNamespace(chart_view=chart_view),
        equity=SimpleNamespace(),
    )
    healthy = DetachedChartWindow("index", service, source)
    failing = DetachedChartWindow("index", service, source)
    healthy.show()
    failing.show()
    main_thread = threading.get_ident()
    healthy.page.period.setCurrentText("60D")
    failing.page.index.setCurrentText("KOSDAQ")
    for _ in range(200):
        app.processEvents()
        if healthy._thread is None and failing._thread is None:
            break
        QtTest.QTest.qWait(5)

    assert worker_threads and all(thread_id != main_thread for thread_id in worker_threads)
    assert not healthy.page._frame.empty
    assert failing.page._frame.empty
    assert "가격·등락·기간 통계·지표 숨김" in failing.page.hover.text()
    assert healthy.page.hover.text() != failing.page.hover.text()

    healthy.close()
    failing.close()
    source.close()
    app.processEvents()
    assert healthy._thread is None and failing._thread is None


def test_detached_index_reload_uses_its_own_current_selection_and_result_path():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = IndexPage()
    source.render(_index_series_view())
    calls: list[tuple[str, str]] = []

    def chart_view(index, period):
        calls.append((index, period))
        return replace(
            _index_series_view(), index=index, name=index,
            exact_identity=f"KRX:{index}", period=period,
        )

    service = SimpleNamespace(
        index=SimpleNamespace(chart_view=chart_view),
        equity=SimpleNamespace(),
    )
    detached = DetachedChartWindow("index", service, source)
    detached.show()
    before_source = source._frame.copy()

    detached.page.index_reload_button.click()
    for _ in range(200):
        app.processEvents()
        if detached._thread is None:
            break
        QtTest.QTest.qWait(5)

    assert calls == [("KOSPI", "120D")]
    assert detached.page.index.currentText() == "KOSPI"
    assert detached.page.period.currentText() == "120D"
    assert not detached.page._frame.empty
    pd.testing.assert_frame_equal(source._frame, before_source)
    detached.close()
    source.close()
    app.processEvents()


def test_busy_detached_close_is_nonblocking_and_retries_after_thread_destruction():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = IndexPage()
    source.render(_detached_index_frame())
    started = threading.Event()
    release = threading.Event()

    def chart_view(index, period):
        started.set()
        assert release.wait(3)
        return replace(
            _index_series_view(), index=index, name=index,
            exact_identity=f"KRX:{index}", period=period,
            frame=_detached_index_frame(),
        )

    service = SimpleNamespace(
        index=SimpleNamespace(chart_view=chart_view),
        equity=SimpleNamespace(),
    )
    window = DetachedChartWindow("index", service, source)
    closed: list[object] = []
    destroyed: list[object] = []
    window.closed.connect(closed.append)
    window.destroyed.connect(lambda *_args: destroyed.append(object()))
    window.show()
    window.page.period.setCurrentText("1Y")
    for _ in range(100):
        app.processEvents()
        if started.is_set():
            break
        QtTest.QTest.qWait(5)
    assert started.is_set()

    before = time.perf_counter()
    window.close()
    elapsed = time.perf_counter() - before
    app.processEvents()

    assert elapsed < 0.1
    assert window.isVisible()
    assert window._close_pending is True
    assert closed == []

    release.set()
    for _ in range(300):
        app.processEvents()
        if destroyed:
            break
        QtTest.QTest.qWait(5)

    assert len(closed) == 1
    assert len(destroyed) == 1
    source.close()


def test_main_close_keeps_refresh_timers_until_busy_detached_child_closes(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window)
    _drain_main_window_workers(app, window)
    window.show()
    for _ in range(200):
        app.processEvents()
        if not any(thread is not None for thread in window._managed_worker_threads()):
            break
        QtTest.QTest.qWait(5)
    started = threading.Event()
    release = threading.Event()

    def chart_view(index, period):
        started.set()
        assert release.wait(3)
        return replace(
            _index_series_view(), index=index, name=index,
            exact_identity=f"KRX:{index}", period=period,
            frame=_detached_index_frame(),
        )

    service = SimpleNamespace(
        index=SimpleNamespace(chart_view=chart_view),
        equity=SimpleNamespace(),
    )
    detached = DetachedChartWindow("index", service, window.index_page)
    detached.closed.connect(window._detached_closed)
    window._detached_windows.add(detached)
    detached.show()
    detached.page.period.setCurrentText("1Y")
    for _ in range(100):
        app.processEvents()
        if started.is_set():
            break
        QtTest.QTest.qWait(5)
    assert started.is_set()

    window.local_reload_timer.start(60_000)
    window.current_observation_reload_timer.start(60_000)
    before = time.perf_counter()
    window.close()
    elapsed = time.perf_counter() - before
    app.processEvents()

    assert elapsed < 0.1
    assert window.isVisible()
    assert window._closing is False
    assert window._close_pending is True
    assert window.local_reload_timer.isActive()
    assert window.current_observation_reload_timer.isActive()

    release.set()
    for _ in range(400):
        app.processEvents()
        if not window.isVisible():
            break
        QtTest.QTest.qWait(5)

    assert not window.isVisible()
    assert window._detached_windows == set()
    assert not window.local_reload_timer.isActive()
    assert not window.current_observation_reload_timer.isActive()


def test_main_window_closes_and_forgets_all_detached_windows(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window)
    _drain_local_read_workers(app, window)
    window.index_page.render(_detached_index_frame())
    identity = _equity_identity()
    window.equity_page._selected_identity = identity
    window.equity_page.render_series(_equity_series_view(identity))

    detached_index = window.open_detached_index()
    detached_equity = window.open_detached_equity()
    app.processEvents()
    assert window._detached_windows == {detached_index, detached_equity}
    assert detached_index.page is not window.index_page
    assert detached_equity.page is not window.equity_page

    destroyed: list[str] = []
    detached_index.destroyed.connect(lambda: destroyed.append("index"))
    detached_equity.destroyed.connect(lambda: destroyed.append("equity"))
    detached_index.close()
    app.processEvents()
    assert detached_index not in window._detached_windows
    assert detached_equity in window._detached_windows
    window.close()
    app.processEvents()

    assert not window.isVisible()
    assert window._detached_windows == set()
    assert set(destroyed) == {"index", "equity"}
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_volume_axis_uses_one_readable_unit_and_bounded_ticks():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    axis = main_window_module.VolumeAxisItem(orientation="left")

    billion_ticks = axis.tickValues(0.0, 1_620_000_000.0, 180)[0][1]
    assert 3 <= len(billion_ticks) <= 5
    assert axis.autoSIPrefix is False
    assert axis.labelText == "거래량(억주)"
    assert axis.tickStrings(billion_ticks, 1.0, 1.0) == ["0", "5", "10", "15"]
    assert all("e" not in label.lower() for label in axis.tickStrings(billion_ticks, 1.0, 1.0))

    stock_ticks = axis.tickValues(0.0, 3_240_000.0, 180)[0][1]
    assert 3 <= len(stock_ticks) <= 5
    assert axis.labelText == "거래량(만주)"
    assert all("e" not in label.lower() for label in axis.tickStrings(stock_ticks, 1.0, 1.0))


def test_exact_share_volume_formatter_is_unscaled_and_comma_separated():
    assert main_window_module._format_exact_share_volume(1_586_828) == "거래량 1,586,828주"
    assert main_window_module._format_exact_share_volume(9_999_999_999_999_999) == "거래량 9,999,999,999,999,999주"
    assert main_window_module._format_exact_share_volume(np.nan) == "거래량 N/A"


def _payload() -> dict:
    return {
        "status": "DESCRIPTIVE_SIGNAL_REPLAY_NOT_PORTFOLIO_BACKTEST",
        "frozen_manifest": {
            "dataset": "kr_kospi200_index_daily",
            "contract_version": 1,
            "coverage_start": "1990-01-03",
            "coverage_end": "2026-08-14",
            "rows": 9447,
            "files": 37,
            "bytes": 738068,
            "root_manifest_sha256": phase1_replay.EXPECTED_FROZEN_DIGEST,
            "decision_rule": "T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION",
        },
        "thresholds": {
            "realized_volatility_20d": 0.25,
            "rolling_drawdown_60d": -0.1,
            "ma_distance_60d": -0.08,
            "return_20d": -0.05,
            "minimum_conditions": 2,
        },
        "metrics": {
            "observations": 10,
            "true_positive": 2,
            "false_positive": 3,
            "false_negative": 2,
            "true_negative": 3,
            "precision": 0.4,
            "recall": 0.5,
            "false_positive_rate": 0.5,
            "event_prevalence": 0.4,
            "pr_auc_average_precision": 0.4,
            "mean_forward_return_20d": 0.01,
            "mean_forward_max_drawdown_20d": -0.1,
            "mean_mae_20d": -0.1,
            "mean_mfe_20d": 0.1,
        },
        "crisis_replay": [{
            "event": "development_event",
            "start": "2020-01-01",
            "end": "2020-06-30",
            "status": "DIAGNOSTIC_ONLY",
            "observations": 50,
            "risk_off_observations": 10,
            "mean_forward_20d_return": -0.01,
            "worst_forward_20d_drawdown": -0.2,
        }],
    }


def _write_result(root: Path, payload: dict) -> Path:
    path = root / "artifacts/backtest/phase1_signal_replay/result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def strict_phase1_bodies() -> dict[str, bytes]:
    """Build the real strict producer payload once, without publishing it."""
    project_root = Path(__file__).resolve().parents[3]
    bundle = phase1_replay._build_replay_bundle(project_root)
    return dict(bundle.bodies)


def _write_strict_phase1_bundle(
    root: Path, bodies: dict[str, bytes],
) -> Path:
    output = root / phase1_replay.DEFAULT_OUTPUT_RELATIVE
    output.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (output / name).write_bytes(body)
    return output


def _retarget_strict_phase1_bodies(
    original: dict[str, bytes], project_root: Path, output: Path,
) -> dict[str, bytes]:
    base = {
        name: original[name]
        for name in ("signals.csv", "result.json", "experiments.json", "portfolio_ledger.json")
    }
    registry = json.loads(base["experiments.json"])
    result_path = output.resolve() / "result.json"
    try:
        registered = result_path.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        registered = result_path.as_posix()
    registry["experiments"][0]["result_artifact"] = registered
    base["experiments.json"] = (
        json.dumps(
            registry, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return dict(
        phase1_replay._bind_bundle(
            base,
            frozen_input_digest=phase1_replay.EXPECTED_FROZEN_DIGEST,
        ).bodies
    )


def _prepare_strict_phase1_bundle(
    root: Path, original: dict[str, bytes],
) -> tuple[Path, Path, dict[str, bytes]]:
    project_root = Path(__file__).resolve().parents[3]
    output = root / phase1_replay.DEFAULT_OUTPUT_RELATIVE
    bodies = _retarget_strict_phase1_bodies(original, project_root, output)
    _write_strict_phase1_bundle(root, bodies)
    return project_root, output, bodies


def _rebind_strict_phase1_bodies(
    original: dict[str, bytes],
    *,
    mutate_result=None,
    mutate_ledger=None,
    mutate_experiment=None,
    mutate_signals=None,
) -> dict[str, bytes]:
    base = {
        name: original[name]
        for name in ("signals.csv", "result.json", "experiments.json", "portfolio_ledger.json")
    }
    result = json.loads(base["result.json"])
    ledger = json.loads(base["portfolio_ledger.json"])
    if mutate_ledger is not None:
        mutate_ledger(ledger)
        base["portfolio_ledger.json"] = phase1_replay._json_bytes(ledger)
        result["portfolio_foundation"]["ledger_artifact_digest"] = (
            phase1_replay.artifact_bytes_digest(base["portfolio_ledger.json"])
        )
    if mutate_result is not None:
        mutate_result(result)
    base["result.json"] = phase1_replay._json_bytes(result, pretty=True)
    if mutate_signals is not None:
        base["signals.csv"] = mutate_signals(base["signals.csv"])

    registry = json.loads(base["experiments.json"])
    experiment = registry["experiments"][0]
    experiment["signals_artifact_digest"] = phase1_replay.artifact_bytes_digest(
        base["signals.csv"]
    )
    experiment["result_artifact_digest"] = phase1_replay.artifact_bytes_digest(
        base["result.json"]
    )
    if mutate_experiment is not None:
        mutate_experiment(experiment)
    base["experiments.json"] = (
        json.dumps(
            registry, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return dict(
        phase1_replay._bind_bundle(
            base,
            frozen_input_digest=phase1_replay.EXPECTED_FROZEN_DIGEST,
        ).bodies
    )


def test_backtest_result_service_reads_typed_local_view_without_mutation(tmp_path):
    path = _write_result(tmp_path, _payload())
    before = path.read_bytes()

    view = BacktestResultService(tmp_path).load()

    assert path.read_bytes() == before
    assert view.artifact_state == "READY"
    assert view.input_coverage is not None
    assert view.input_coverage.coverage_end == "2026-08-14"
    assert dict((item.name, item.value) for item in view.metrics)["precision"] == 0.4
    assert view.horizons == (
        "forward return: 20 trading days",
        "forward max drawdown: 20 trading days",
    )
    assert "EQUITY CURVE UNAVAILABLE" in view.portfolio_scope


def test_backtest_result_service_fails_closed_for_missing_or_unknown_result(tmp_path):
    missing = BacktestResultService(tmp_path).load()
    assert missing.artifact_state == "RESULT NOT AVAILABLE"
    assert missing.metrics == ()

    payload = _payload()
    payload["status"] = "PORTFOLIO_BACKTEST"
    _write_result(tmp_path, payload)
    rejected = BacktestResultService(tmp_path).load()
    assert rejected.artifact_state == "RESULT NOT AVAILABLE"
    assert "not the accepted non-portfolio experiment" in (rejected.warning or "")


def test_backtest_result_service_fails_closed_for_unaccepted_input_boundary(tmp_path):
    for field, value, warning in (
        ("dataset", "other_dataset", "dataset is not the accepted"),
        ("contract_version", 2, "contract version is not accepted"),
        ("decision_rule", "SAME_DAY_DECISION", "decision rule is not accepted"),
        ("coverage_start", "2026-08-15", "coverage is reversed"),
        ("coverage_end", "2026/08/14", "must be an ISO date"),
    ):
        payload = _payload()
        payload["frozen_manifest"][field] = value
        _write_result(tmp_path, payload)

        rejected = BacktestResultService(tmp_path).load()

        assert rejected.artifact_state == "RESULT NOT AVAILABLE"
        assert warning in (rejected.warning or "")


def test_backtest_legacy_result_rejects_unknown_metrics_and_holdout_diagnostics(
    tmp_path,
):
    payload = _payload()
    payload["metrics"] = {"made_up": -999}
    _write_result(tmp_path, payload)
    assert BacktestResultService(tmp_path).load().artifact_state == (
        "RESULT NOT AVAILABLE"
    )

    payload = _payload()
    payload["crisis_replay"] = [{
        "event": "leaked_holdout",
        "start": "2022-01-01",
        "end": "2022-12-31",
        "status": "DIAGNOSTIC_ONLY",
        "observations": 1,
        "risk_off_observations": 1,
        "mean_forward_20d_return": 0.5,
        "worst_forward_20d_drawdown": -0.2,
    }]
    _write_result(tmp_path, payload)
    assert BacktestResultService(tmp_path).load().artifact_state == (
        "RESULT NOT AVAILABLE"
    )


def test_backtest_page_displays_result_scope_without_equity_calculation(tmp_path):
    _write_result(tmp_path, _payload())
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = BacktestPage()

    page.render(BacktestResultService(tmp_path).load())

    assert "DESCRIPTIVE_SIGNAL_REPLAY" in page.experiment.body.text()
    assert "precision: 0.4000" in page.metrics.body.text()
    assert "development_event" in page.crises.body.text()
    assert "EQUITY CURVE UNAVAILABLE" in page.scope.body.text()
    assert "No features, signals, labels, metrics" in page.scope.body.text()
    page.close()
    app.processEvents()


def _gui_scenario_inputs() -> BacktestScenarioInputs:
    sessions = tuple(
        value.date().isoformat()
        for value in pd.bdate_range("2020-01-02", periods=30)
    )
    features_dates = sessions[:-1]
    market = pd.DataFrame({
        "session_date": sessions,
        "open": np.linspace(100.0, 129.0, 30),
        "close": np.linspace(100.5, 129.5, 30),
        "instrument_id": ["KRX:1028"] * 30,
        "currency": ["KRW"] * 30,
    })
    features = pd.DataFrame({
        "observation_date": features_dates,
        "ticker": ["1028"] * 29,
        "date_semantics": ["KRX_TRADING_DATE_DAILY_FINAL"] * 29,
        "instrument_id": ["KRX:1028"] * 29,
        "usable_from": [
            f"{sessions[index + 1]}T09:00:00+09:00" for index in range(29)
        ],
        "pit_status": ["PIT_SAFE_EOD_T_PLUS_1"] * 29,
        "rsi_14": [20.0] * 20 + [80.0] * 9,
    })
    label_dates = features_dates[:-1]
    labels = pd.DataFrame({
        "observation_date": label_dates,
        "ticker": ["1028"] * 28,
        "date_semantics": ["KRX_TRADING_DATE_DAILY_FINAL"] * 28,
        "label_available_at": ["2020-06-01T15:30:00+09:00"] * 28,
        "label_version": pd.Series([1] * 28, dtype="int64"),
        "forward_return_20d": np.linspace(0.01, 0.037, 28),
        "forward_max_drawdown_20d": np.linspace(-0.02, -0.047, 28),
    })
    return BacktestScenarioInputs(
        SCENARIO_INPUT_VERSION,
        SCENARIO_ID,
        market,
        features,
        labels,
        KOSPI200_FROZEN_HOLDOUT_V1,
    )


def test_backtest_page_renders_fixed_scenario_without_controls_or_ranking():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = BacktestPage()
    page.resize(1600, 900)
    page.configure_scenario_available(True)
    view = BacktestScenarioService().evaluate(_gui_scenario_inputs())

    page.render_scenario(view)
    page.show()
    app.processEvents()

    assert "RSI14_LOW_30" in page.scenario_conditions.body.text()
    assert "conditional / unconditional / difference" in (
        page.scenario_conditions.body.text()
    )
    assert "RSI14_HIGH_70" in page.scenario_coverage.body.text()
    assert "winner 선택 없음" in page.scenario_coverage.body.text()
    assert "투자 추천 아님" in page.scenario_coverage.body.text()
    assert "historical-next-open/v1" in page.scenario_execution.body.text()
    assert "total return difference" in page.scenario_matched_hold.body.text()
    assert "holdout 미검토" in page.scenario_status.text()
    assert page.scenario_button.isEnabled()
    assert not page.scenario_panel.findChildren(QtWidgets.QSpinBox)
    assert not page.scenario_panel.findChildren(QtWidgets.QDoubleSpinBox)
    assert not page.scenario_panel.findChildren(QtWidgets.QLineEdit)
    assert page.horizontalScrollBar().maximum() == 0
    assert page.scenario_panel.width() <= page.viewport().width()

    prior = tuple(
        card.body.text() for card in (
            page.scenario_conditions,
            page.scenario_coverage,
            page.scenario_execution,
            page.scenario_matched_hold,
        )
    )
    with pytest.raises(ValueError):
        page.render_scenario(replace(view, recommendation_provided=True))
    page.set_workflow_failure("SCENARIO")
    assert tuple(
        card.body.text() for card in (
            page.scenario_conditions,
            page.scenario_coverage,
            page.scenario_execution,
            page.scenario_matched_hold,
        )
    ) == prior
    assert "그대로 보존" in page.scenario_status.text()
    page.close()
    app.processEvents()


def test_backtest_page_keeps_no_entry_matched_hold_numeric_free():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = BacktestPage()
    inputs = _gui_scenario_inputs()
    features = inputs.features.copy(deep=True)
    features["rsi_14"] = 50.0
    view = BacktestScenarioService().evaluate(
        replace(inputs, features=features)
    )

    page.render_scenario(view)

    assert "NO_ENTRY_OBSERVATION" in page.scenario_matched_hold.body.text()
    assert "진입 관측 없음" in page.scenario_matched_hold.body.text()
    assert "difference:" not in page.scenario_matched_hold.body.text()
    assert "None" not in page.scenario_matched_hold.body.text()
    page.close()
    app.processEvents()


def test_backtest_page_is_summary_first_with_collapsed_accessible_evidence():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = BacktestPage()
    layout = page.widget().layout()

    assert layout.indexOf(page.portfolio_metrics) < layout.indexOf(page.nav_plot)
    assert layout.indexOf(page.nav_plot) < layout.indexOf(page.evidence_toggle)
    assert layout.indexOf(page.evidence_toggle) < layout.indexOf(page.evidence_panel)
    assert page.evidence_panel.isHidden()
    assert page.fixed_configuration.parentWidget() is page.evidence_panel
    assert page.bundle_receipt.parentWidget() is page.evidence_panel
    assert "펼치기" in page.evidence_toggle.text()
    assert page.evidence_toggle.accessibleName()

    page.evidence_toggle.setChecked(True)
    app.processEvents()
    assert not page.evidence_panel.isHidden()
    assert "접기" in page.evidence_toggle.text()
    assert page.evidence_toggle.arrowType() == QtCore.Qt.DownArrow

    page.evidence_toggle.setChecked(False)
    app.processEvents()
    assert page.evidence_panel.isHidden()
    assert page.evidence_toggle.arrowType() == QtCore.Qt.RightArrow
    page.close()
    app.processEvents()


def test_backtest_result_accepts_untouched_holdout_without_outcome_inspection(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    payload = _payload()
    payload["crisis_replay"].append({
        "event": "sealed_event", "start": "2022-01-01", "end": "2022-12-31",
        "status": "UNTOUCHED_HOLDOUT", "holdout_observations_excluded": "NOT_INSPECTED",
    })
    _write_result(tmp_path, payload)

    view = BacktestResultService(tmp_path).load()

    assert view.artifact_state == "READY"
    holdout = view.crises[-1]
    assert holdout.status == "UNTOUCHED_HOLDOUT"
    assert holdout.observations is None
    page = BacktestPage()
    page.render(view)
    assert "outcomes NOT INSPECTED" in page.crises.body.text()
    page.close()
    app.processEvents()


def test_backtest_service_accepts_current_retained_artifact():
    root = Path(__file__).resolve().parents[3]
    artifact_root = root / phase1_replay.DEFAULT_OUTPUT_RELATIVE
    before = {
        name: (artifact_root / name).read_bytes()
        for name in phase1_replay._OWNED_FILES
    }
    view = BacktestResultService(root).load()
    after = {
        name: (artifact_root / name).read_bytes()
        for name in phase1_replay._OWNED_FILES
    }
    assert view.artifact_state == "READY"
    assert before == after
    assert view.holdout is not None
    assert view.holdout.results_reviewed is False
    assert any(row.status == "DIAGNOSTIC_ONLY" for row in view.crises)
    assert any(row.status == "UNTOUCHED_HOLDOUT" for row in view.crises)


def test_backtest_service_rejects_changed_explicit_phase1_dependency(
    tmp_path, strict_phase1_bodies, monkeypatch,
):
    project_root, output, _written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    accepted_digest = phase1_replay.phase1_code_digest(project_root)
    assert BacktestResultService(
        project_root, output_root=output,
    ).load_validated_bundle().receipt.status == "READY"

    monkeypatch.setattr(
        backtest_service_module,
        "phase1_code_digest",
        lambda _root: "0" * 64 if accepted_digest != "0" * 64 else "1" * 64,
    )
    with pytest.raises(BacktestWorkflowError):
        BacktestResultService(
            project_root, output_root=output,
        ).load_validated_bundle()


def test_backtest_service_accepts_only_the_bound_five_file_generation(
    tmp_path, strict_phase1_bodies,
):
    project_root, output, expected_bodies = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )

    accepted = BacktestResultService(
        project_root, output_root=output,
    ).load_validated_bundle()

    assert accepted.receipt.status == "READY"
    assert accepted.receipt.frozen_input_digest == phase1_replay.EXPECTED_FROZEN_DIGEST
    assert tuple(accepted.artifact_bodies) == tuple(sorted(expected_bodies))
    assert dict(accepted.artifact_bodies) == expected_bodies
    assert accepted.view.artifact_state == "READY"
    assert accepted.view.holdout is not None
    assert accepted.view.holdout.development_observations == 8_225
    assert accepted.view.holdout.holdout_observations == 1_222
    assert accepted.view.holdout.results_reviewed is False
    assert accepted.view.portfolio is not None
    assert accepted.view.portfolio.status == "DEVELOPMENT_ONLY_CLOSE_PROXY"
    assert accepted.view.portfolio.instrument_claim == "NOT_EXECUTABLE_INSTRUMENT"
    assert accepted.view.portfolio.assumptions["one_way_transaction_cost_rate"] == 0.001
    assert accepted.view.portfolio.assumptions["leverage_allowed"] is False
    assert accepted.view.portfolio.curve
    assert accepted.view.bundle_receipt == accepted.receipt


@pytest.mark.parametrize(
    "artifact_name",
    ("bundle.json", "experiments.json", "portfolio_ledger.json", "result.json", "signals.csv"),
)
def test_backtest_service_rejects_each_tampered_artifact_without_legacy_fallback(
    tmp_path, strict_phase1_bodies, artifact_name,
):
    project_root, output, bodies = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    (output / artifact_name).write_bytes(bodies[artifact_name] + b" ")
    service = BacktestResultService(project_root, output_root=output)

    with pytest.raises(BacktestWorkflowError):
        service.load_validated_bundle()

    unavailable = service.load()
    assert unavailable.artifact_state == "RESULT NOT AVAILABLE"
    assert "strict local backtest bundle is invalid" in (unavailable.warning or "")


def test_backtest_service_never_downgrades_a_strict_result_to_legacy(
    tmp_path, strict_phase1_bodies,
):
    project_root, output, _written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    (output / "bundle.json").unlink()
    (output / "portfolio_ledger.json").unlink()

    view = BacktestResultService(project_root, output_root=output).load()

    assert view.artifact_state == "RESULT NOT AVAILABLE"
    assert "strict local backtest bundle is invalid" in (view.warning or "")


@pytest.mark.parametrize(
    "case",
    (
        "scope", "holdout", "ledger", "threshold", "holdout_diagnostic",
        "split", "result_artifact", "foundation_bool", "metrics", "signal_flag",
        "grid", "code_digest", "feature_versions", "threshold_digest",
    ),
)
def test_backtest_service_rejects_rebound_semantic_tampering(
    tmp_path, strict_phase1_bodies, case,
):
    def mutate_result(result):
        if case == "scope":
            result["metrics_scope"] = "FULL_SAMPLE"
        elif case == "holdout":
            result["untouched_holdout_policy"]["results_reviewed"] = True
        elif case == "threshold":
            result["thresholds"]["realized_volatility_20d"] = 0.99
        elif case == "foundation_bool":
            result["portfolio_foundation"]["metrics"]["initial_nav"] = True
        elif case == "metrics":
            result["metrics"] = {"made_up": -999}
            result["development_metrics"] = {"made_up": -999}
        elif case == "grid":
            result["predefined_small_grid"][0]["metrics"] = {"made_up": -999}
        elif case == "holdout_diagnostic":
            leaked = {
                "event": "bear_market_2022",
                "start": "2022-01-01",
                "end": "2022-12-31",
                "status": "DIAGNOSTIC_ONLY",
                "observations": 1,
                "risk_off_observations": 1,
                "adverse_observations": 1,
                "event_precision": 1.0,
                "event_recall": 1.0,
                "first_risk_off_date": "2022-01-03",
                "worst_forward_20d_drawdown": -0.2,
                "mean_forward_20d_return": 0.123,
                "holdout_observations_excluded": 0,
            }
            result["crisis_replay"][3] = leaked
            result["crisis_replay_development_only"][3] = dict(leaked)

    def mutate_experiment(experiment):
        if case == "holdout":
            experiment["holdout_results_reviewed"] = True
        elif case == "split":
            experiment["purge"] = 999
            experiment["embargo"] = 999
            experiment["label_horizon_trading_days"] = 1
        elif case == "result_artifact":
            experiment["result_artifact"] = "result.json"
        elif case == "code_digest":
            experiment["code_tree_digest"] = "b" * 64
        elif case == "feature_versions":
            experiment["feature_versions"] = ["made_up:v999"]
        elif case == "threshold_digest":
            experiment["threshold_values_digest"] = "c" * 64

    def mutate_ledger(ledger):
        if case == "ledger":
            ledger["simulation"]["metrics"]["ending_nav"] += 0.01

    def mutate_signals(body):
        if case != "signal_flag":
            return body
        text = body.decode("utf-8")
        return text.replace(",False,False,False,False,0,False,1\n", (
            ",NOT_BOOLEAN,False,False,False,0,False,1\n"
        ), 1).encode("utf-8")

    bodies = _rebind_strict_phase1_bodies(
        strict_phase1_bodies,
        mutate_result=mutate_result,
        mutate_ledger=mutate_ledger if case == "ledger" else None,
        mutate_experiment=(
            mutate_experiment if case != "result_artifact" else None
        ),
        mutate_signals=mutate_signals if case == "signal_flag" else None,
    )
    project_root, output, written = _prepare_strict_phase1_bundle(
        tmp_path, bodies,
    )
    if case == "result_artifact":
        # Retargeting makes the producer fixture valid for this temporary
        # output root.  Apply this attack only after that legitimate rewrite.
        written = _rebind_strict_phase1_bodies(
            written, mutate_experiment=mutate_experiment,
        )
        _write_strict_phase1_bundle(tmp_path, written)

    with pytest.raises(BacktestWorkflowError):
        BacktestResultService(
            project_root, output_root=output,
        ).load_validated_bundle()


def test_backtest_service_binds_runner_receipt_and_rejects_post_run_tamper(
    tmp_path, strict_phase1_bodies,
):
    project_root, output, written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    accepted = BacktestResultService(
        project_root, output_root=output,
    ).load_validated_bundle()
    requests = []

    def runner(request):
        requests.append(request)
        return accepted.receipt

    service = BacktestResultService(
        project_root, output_root=output, runner=runner,
    )
    rerun = service.run_validated()
    assert rerun.receipt == accepted.receipt
    assert len(requests) == 1
    assert requests[0].output_root == output

    (output / "signals.csv").write_bytes(
        written["signals.csv"] + b"tampered"
    )
    with pytest.raises(BacktestWorkflowError):
        service.run_validated()


def test_backtest_exact_export_uses_only_the_accepted_immutable_bytes(
    tmp_path, strict_phase1_bodies,
):
    project_root, output, written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    service = BacktestResultService(project_root, output_root=output)
    accepted = service.load_validated_bundle()
    (output / "result.json").write_bytes(b"source generation changed")
    destination = tmp_path / "exported-backtest"

    receipt = service.export_exact_bundle(accepted, destination)

    assert receipt.status == "EXPORTED"
    assert receipt.bundle_digest == accepted.receipt.bundle_digest
    assert tuple(path.name for path in sorted(destination.iterdir())) == tuple(
        sorted(written)
    )
    assert {
        path.name: path.read_bytes() for path in destination.iterdir()
    } == written
    with pytest.raises(BacktestWorkflowError):
        service.export_exact_bundle(accepted, destination)


def test_backtest_exact_export_failure_never_publishes_partial_destination(
    tmp_path, strict_phase1_bodies, monkeypatch,
):
    project_root, output, _written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    service = BacktestResultService(project_root, output_root=output)
    accepted = service.load_validated_bundle()
    destination = tmp_path / "interrupted-export"

    def fail_fsync(_descriptor):
        raise OSError("injected export sync failure")

    monkeypatch.setattr(backtest_service_module.os, "fsync", fail_fsync)

    with pytest.raises(BacktestWorkflowError):
        service.export_exact_bundle(accepted, destination)

    assert not destination.exists()
    assert not tuple(tmp_path.glob(f".{destination.name}.backtest-export-*.stage"))


def test_backtest_exact_export_rejects_redirected_parent(
    tmp_path, strict_phase1_bodies,
):
    project_root, output, _written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    service = BacktestResultService(project_root, output_root=output)
    accepted = service.load_validated_bundle()
    real_parent = tmp_path / "real-export-parent"
    redirected_parent = tmp_path / "redirected-export-parent"
    real_parent.mkdir()
    try:
        redirected_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable on this host: {error}")

    with pytest.raises(BacktestWorkflowError):
        service.export_exact_bundle(
            accepted, redirected_parent / "should-not-be-created",
        )

    assert not (real_parent / "should-not-be-created").exists()


def test_backtest_page_renders_fixed_close_proxy_and_preserves_it_on_failure(
    tmp_path, strict_phase1_bodies,
):
    project_root, output, _written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    accepted = BacktestResultService(
        project_root, output_root=output,
    ).load_validated_bundle()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = BacktestPage()

    page.render_validated_bundle(accepted.view)
    app.processEvents()

    assert page.has_accepted_bundle is True
    assert page.export_button.isEnabled()
    assert "10bp" in page.fixed_configuration.body.text()
    assert "NOT_EXECUTABLE_INSTRUMENT" in page.portfolio_metrics.body.text()
    assert accepted.receipt.bundle_digest in page.bundle_receipt.body.text()
    assert not page.findChildren(QtWidgets.QLineEdit)
    assert not page.findChildren(QtWidgets.QSpinBox)
    nav_x, nav_y = page.nav_curve.getData()
    drawdown_x, drawdown_y = page.drawdown_curve.getData()
    expected_nav = np.asarray(
        [point.nav for point in accepted.view.portfolio.curve], dtype="float64",
    )
    expected_drawdown = np.asarray(
        [point.drawdown for point in accepted.view.portfolio.curve], dtype="float64",
    )
    assert np.array_equal(nav_x, np.arange(len(expected_nav), dtype="float64"))
    assert np.array_equal(drawdown_x, nav_x)
    assert np.array_equal(nav_y, expected_nav)
    assert np.array_equal(drawdown_y, expected_drawdown)
    nav_description = page.nav_plot.accessibleDescription()
    drawdown_description = page.drawdown_plot.accessibleDescription()
    curve = accepted.view.portfolio.curve
    expected_period = f"{curve[0].date}부터 {curve[-1].date}"
    expected_observations = f"관측 {len(curve):,}개"
    assert "개발 전용 실행 불가능 close-proxy" in nav_description
    assert expected_period in nav_description
    assert expected_observations in nav_description
    assert "초기 NAV 1.000000" in nav_description
    assert "종료 NAV" in nav_description
    assert "총수익률" in nav_description
    assert "봉인 holdout 결과는 미검토" in nav_description
    assert "개발 전용 실행 불가능 close-proxy" in drawdown_description
    assert expected_period in drawdown_description
    assert expected_observations in drawdown_description
    assert "최대 낙폭" in drawdown_description
    assert "봉인 holdout 결과는 미검토" in drawdown_description

    prior_metrics = page.portfolio_metrics.body.text()
    prior_receipt = page.bundle_receipt.body.text()
    invalid_holdout = replace(accepted.view.holdout, results_reviewed=True)
    with pytest.raises(ValueError):
        page.render_validated_bundle(
            replace(accepted.view, holdout=invalid_holdout),
        )
    page.set_workflow_failure("RUN")
    assert page.portfolio_metrics.body.text() == prior_metrics
    assert page.bundle_receipt.body.text() == prior_receipt
    assert np.array_equal(page.nav_curve.getData()[1], expected_nav)
    assert page.nav_plot.accessibleDescription() == nav_description
    assert page.drawdown_plot.accessibleDescription() == drawdown_description
    assert "그대로 보존" in page.workflow_status.text()
    page.close()
    app.processEvents()


def test_backtest_page_rolls_back_when_second_curve_creation_fails(
    tmp_path, strict_phase1_bodies, monkeypatch,
):
    project_root, output, _written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    accepted = BacktestResultService(
        project_root, output_root=output,
    ).load_validated_bundle()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = BacktestPage()
    page.render_validated_bundle(accepted.view)
    app.processEvents()
    old_nav_curve = page.nav_curve
    old_drawdown_curve = page.drawdown_curve
    old_nav = tuple(float(value) for value in old_nav_curve.getData()[1])
    old_drawdown = tuple(
        float(value) for value in old_drawdown_curve.getData()[1]
    )
    old_cards = (
        page.experiment.body.text(),
        page.portfolio_metrics.body.text(),
        page.bundle_receipt.body.text(),
    )
    old_status = page.workflow_status.text()

    def fail_plot(*_args, **_kwargs):
        raise RuntimeError("injected drawdown plot failure")

    monkeypatch.setattr(page.drawdown_plot, "plot", fail_plot)
    with pytest.raises(RuntimeError, match="injected drawdown"):
        page.render_validated_bundle(accepted.view)

    assert page.nav_curve is old_nav_curve
    assert page.drawdown_curve is old_drawdown_curve
    assert tuple(float(value) for value in page.nav_curve.getData()[1]) == old_nav
    assert tuple(
        float(value) for value in page.drawdown_curve.getData()[1]
    ) == old_drawdown
    assert len(page.nav_plot.listDataItems()) == 1
    assert len(page.drawdown_plot.listDataItems()) == 1
    assert (
        page.experiment.body.text(),
        page.portfolio_metrics.body.text(),
        page.bundle_receipt.body.text(),
    ) == old_cards
    assert page.workflow_status.text() == old_status
    assert page.has_accepted_bundle is True
    assert page.nav_plot.accessibleDescription()
    assert page.drawdown_plot.accessibleDescription()
    page.close()
    app.processEvents()


def test_backtest_page_clears_stale_chart_descriptions_for_unavailable_view(
    tmp_path, strict_phase1_bodies,
):
    project_root, output, _written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    accepted = BacktestResultService(
        project_root, output_root=output,
    ).load_validated_bundle()
    page = BacktestPage()
    page.render_validated_bundle(accepted.view)
    assert page.nav_plot.accessibleDescription()
    assert page.drawdown_plot.accessibleDescription()

    unavailable = BacktestResultService(
        tmp_path / "missing-backtest-root",
    ).load()
    page.render(unavailable)

    assert page.nav_plot.accessibleDescription() == ""
    assert page.drawdown_plot.accessibleDescription() == ""
    page.close()


def test_main_window_runs_one_backtest_off_the_gui_thread_and_cleans_up(
    tmp_path, strict_phase1_bodies,
):
    project_root, output, _written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    accepted = BacktestResultService(
        project_root, output_root=output,
    ).load_validated_bundle()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    started = threading.Event()
    release = threading.Event()
    calls = []
    main_thread_id = threading.get_ident()

    def blocking_run_validated():
        calls.append((threading.get_ident(), "RUN"))
        started.set()
        if not release.wait(5.0):
            raise TimeoutError("test runner release was not signalled")
        return accepted

    window = MainWindow(
        project_root,
        account_snapshot_path=tmp_path / "missing-toss.json",
        kb_account_snapshot_path=tmp_path / "missing-kb.json",
        family_account_snapshot_path=tmp_path / "missing-family.json",
        toss_runtime_enabled=False,
        net_worth_history_root=tmp_path / "net-worth",
        dashboard_preferences_path=tmp_path / "preferences.json",
        backtest_output_root=output,
    )
    _stub_fast_startup_local_reads(window)
    window.backtest_service.load_validated_bundle = lambda: accepted
    _drain_backtest_and_local_workers(app, window, timeout=30.0)
    assert window._backtest_thread is None
    assert window.backtest_page.has_accepted_bundle is True

    window.backtest_service.run_validated = blocking_run_validated
    assert window._request_backtest_run() is True
    assert window._request_backtest_run() is False
    assert started.wait(2.0)
    responsive = []
    QtCore.QTimer.singleShot(0, lambda: responsive.append(threading.get_ident()))
    app.processEvents()
    assert responsive == [main_thread_id]
    assert len(calls) == 1
    assert calls[0][0] != main_thread_id
    assert window.backtest_page.run_button.isEnabled() is False

    release.set()
    deadline = time.monotonic() + 30.0
    while window._backtest_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)
    assert window._backtest_thread is None
    assert window._backtest_worker is None
    assert window._backtest_action is None
    assert window.backtest_page.has_accepted_bundle is True
    assert window.backtest_page.run_button.isEnabled() is True

    window.close()
    deadline = time.monotonic() + 5.0
    running = [object()]
    while running and time.monotonic() < deadline:
        app.processEvents()
        running = [
            thread for thread in window.findChildren(QtCore.QThread)
            if thread.isRunning()
        ]
        if running:
            QtTest.QTest.qWait(5)
    owned = {
        name: thread for name, thread in (
            ("local", window._local_read_thread),
            ("current_stage", window._current_stage_thread),
            ("backtest", window._backtest_thread),
            ("account", window._account_thread),
            ("current_observation", window._current_observation_thread),
            ("equity", window._equity_thread),
            ("us_etf", window._us_etf_thread),
        )
        if thread in running
    }
    assert not running, f"running managed threads after close: {tuple(owned)}"


def test_main_window_runs_fixed_scenario_off_gui_thread_and_preserves_responsiveness(
    tmp_path,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    inputs = _gui_scenario_inputs()
    scenario_service = BacktestScenarioService()
    expected = scenario_service.evaluate(inputs)
    started = threading.Event()
    release = threading.Event()
    worker_thread_ids = []
    main_thread_id = threading.get_ident()

    def blocking_evaluate(actual_inputs):
        worker_thread_ids.append(threading.get_ident())
        assert actual_inputs is inputs
        started.set()
        if not release.wait(5.0):
            raise TimeoutError("scenario test release was not signalled")
        return expected

    scenario_service.evaluate = blocking_evaluate
    window = MainWindow(
        tmp_path,
        account_snapshot_path=tmp_path / "missing-toss.json",
        kb_account_snapshot_path=tmp_path / "missing-kb.json",
        family_account_snapshot_path=tmp_path / "missing-family.json",
        toss_runtime_enabled=False,
        net_worth_history_root=tmp_path / "net-worth-scenario",
        dashboard_preferences_path=tmp_path / "preferences-scenario.json",
        backtest_scenario_service=scenario_service,
        backtest_scenario_inputs=inputs,
    )
    _stub_fast_startup_local_reads(window)
    _drain_backtest_and_local_workers(app, window, timeout=30.0)

    assert window.backtest_page.scenario_button.isEnabled()
    assert window._request_backtest_scenario() is True
    assert window._request_backtest_scenario() is False
    assert started.wait(2.0)
    responsive = []
    QtCore.QTimer.singleShot(0, lambda: responsive.append(threading.get_ident()))
    app.processEvents()
    assert responsive == [main_thread_id]
    assert worker_thread_ids == [worker_thread_ids[0]]
    assert worker_thread_ids[0] != main_thread_id
    assert not window.backtest_page.scenario_button.isEnabled()

    release.set()
    deadline = time.monotonic() + 15.0
    while window._backtest_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)
    assert window._backtest_thread is None
    assert "RSI14_LOW_30" in window.backtest_page.scenario_conditions.body.text()
    assert window.backtest_page.scenario_button.isEnabled()

    window.close()
    deadline = time.monotonic() + 5.0
    while (
        any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))
        and time.monotonic() < deadline
    ):
        app.processEvents()
        QtTest.QTest.qWait(5)
    assert not any(
        thread.isRunning() for thread in window.findChildren(QtCore.QThread)
    )


def test_main_window_close_during_backtest_is_nonblocking_and_finishes_cleanup(
    tmp_path, strict_phase1_bodies,
):
    project_root, output, _written = _prepare_strict_phase1_bundle(
        tmp_path, strict_phase1_bodies,
    )
    accepted = BacktestResultService(
        project_root, output_root=output,
    ).load_validated_bundle()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    started = threading.Event()
    release = threading.Event()

    def blocking_run_validated():
        started.set()
        if not release.wait(10.0):
            raise TimeoutError("test runner release was not signalled")
        return accepted

    window = MainWindow(
        project_root,
        account_snapshot_path=tmp_path / "missing-toss.json",
        kb_account_snapshot_path=tmp_path / "missing-kb.json",
        family_account_snapshot_path=tmp_path / "missing-family.json",
        toss_runtime_enabled=False,
        net_worth_history_root=tmp_path / "net-worth-close",
        dashboard_preferences_path=tmp_path / "preferences-close.json",
        backtest_output_root=output,
    )
    _stub_fast_startup_local_reads(window)
    window.backtest_service.load_validated_bundle = lambda: accepted
    _drain_backtest_and_local_workers(app, window, timeout=30.0)
    window.show()
    assert window.backtest_page.has_accepted_bundle
    window.backtest_service.run_validated = blocking_run_validated
    assert window._request_backtest_run() is True
    assert started.wait(2.0)

    before_close = time.monotonic()
    assert window.close() is False
    elapsed = time.monotonic() - before_close
    assert elapsed < 1.0
    assert window.isVisible()
    assert window.current_observation_reload_timer.isActive()
    assert "완료 후" in window.backtest_page.workflow_status.text()

    release.set()
    deadline = time.monotonic() + 30.0
    while (
        (window.isVisible() or window._backtest_thread is not None)
        and time.monotonic() < deadline
    ):
        app.processEvents()
        QtTest.QTest.qWait(5)
    remaining_threads = {
        name: (thread is not None, bool(thread and thread.isRunning()))
        for name, thread in {
            "backtest": window._backtest_thread,
            "account": window._account_thread,
            "current_observation": window._current_observation_thread,
            "equity": window._equity_thread,
            "us_etf": window._us_etf_thread,
        }.items()
    }
    assert not window.isVisible(), remaining_threads
    assert window._backtest_thread is None
    assert window._backtest_worker is None
    assert not any(
        thread.isRunning() for thread in window.findChildren(QtCore.QThread)
    )


def test_main_window_labels_successful_legacy_reload_truthfully(tmp_path):
    _write_result(tmp_path, _payload())
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(
        tmp_path,
        account_snapshot_path=tmp_path / "missing-toss.json",
        kb_account_snapshot_path=tmp_path / "missing-kb.json",
        family_account_snapshot_path=tmp_path / "missing-family.json",
        toss_runtime_enabled=False,
        net_worth_history_root=tmp_path / "net-worth-legacy",
        dashboard_preferences_path=tmp_path / "preferences-legacy.json",
    )
    deadline = time.monotonic() + 5.0
    while (
        window._backtest_thread is not None
        or "설명용" not in window.backtest_page.workflow_status.text()
    ) and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)

    assert "기존 설명용" in window.backtest_page.workflow_status.text()
    assert "그대로 보존" not in window.backtest_page.workflow_status.text()
    assert window._accepted_backtest_bundle is None
    assert window.backtest_page.experiment.body.text().startswith("READY")
    window.close()
    app.processEvents()


def test_dashboard_main_chart_indicators_use_cached_bounded_frame_and_truthful_axes():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.resize(1600, 900)
    page.show()
    app.processEvents()
    page.render({"dashboard_metrics": {
        "KOSPI": _metric("KOSPI", 3000.0, freshness="CURRENT", state=DashboardDisplayState.VALUE),
    }})
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=70),
        "open": range(70), "high": range(1, 71), "low": range(-1, 69),
        "close": range(70), "volume": range(100, 170),
        "ma5": range(1, 71), "ma20": range(2, 72), "ma60": range(10, 80),
        "ma120": range(3, 73), "ema20": range(4, 74),
        "bollinger_upper": range(10, 80), "bollinger_mid": range(5, 75),
        "bollinger_lower": range(0, 70), "rsi14": range(20, 90),
        "disparity60": range(80, 150),
    })
    page.render_market_chart(frame)
    app.processEvents()

    assert not page.market_volume.isHidden()
    candle = next(item for item in page.market_chart.getPlotItem().items if isinstance(item, CandlestickItem))
    assert len(candle._bars) == len(frame)
    assert candle._bars[-1][1:] == (69.0, 70.0, 68.0, 69.0)
    assert len(page.market_chart.listDataItems()) == 1

    panel = page.market_indicator_panel
    panel.volume.setChecked(False)
    for control in panel.ma.values():
        control.setChecked(False)
    panel.extra_upper["ema20"].setChecked(False)
    panel.extra_upper["bollinger_bands"].setChecked(False)

    for key, label in (
        ("ma5", "MA5"), ("ma20", "MA20"), ("ma60", "MA60"),
        ("ma120", "MA120"),
    ):
        panel.ma[key].setChecked(True)
        assert [item.name() for item in page.market_chart.listDataItems()] == [label]
        assert label in page.market_indicator_legend.text()
        panel.ma[key].setChecked(False)
        assert not page.market_chart.listDataItems()
        assert page.market_indicator_legend.isHidden()

    panel.extra_upper["ema20"].setChecked(True)
    assert [item.name() for item in page.market_chart.listDataItems()] == ["EMA20"]
    panel.extra_upper["ema20"].setChecked(False)
    assert not page.market_chart.listDataItems()

    bollinger_names = ["BB 상단", "BB 중심", "BB 하단"]
    panel.extra_upper["bollinger_bands"].setChecked(True)
    assert [item.name() for item in page.market_chart.listDataItems()] == bollinger_names
    panel.extra_upper["bollinger_bands"].setChecked(False)
    assert not page.market_chart.listDataItems()

    panel.volume.setChecked(True)
    assert not page.market_volume.isHidden()
    assert any(isinstance(item, pg.BarGraphItem) for item in page.market_volume.getPlotItem().items)
    panel.volume.setChecked(False)
    assert page.market_volume.isHidden()

    panel.rsi.setCurrentIndex(panel.rsi.findData("Overlay"))
    assert set(page._market_overlay_items) == {"rsi14"}
    assert page._market_rsi_overlay_axis.isVisible()
    panel.rsi.setCurrentIndex(panel.rsi.findData("Off"))
    assert not page._market_overlay_items
    assert not page._market_rsi_overlay_axis.isVisible()

    panel.disparity.setCurrentIndex(panel.disparity.findData("Overlay"))
    assert set(page._market_overlay_items) == {"disparity60"}
    assert page._market_disparity_overlay_axis.isVisible()
    panel.disparity.setCurrentIndex(panel.disparity.findData("Off"))
    assert not page._market_overlay_items
    assert not page._market_disparity_overlay_axis.isVisible()

    for control in panel.ma.values():
        control.setChecked(True)
    panel.extra_upper["ema20"].setChecked(True)
    panel.extra_upper["bollinger_bands"].setChecked(True)
    panel.rsi.setCurrentIndex(panel.rsi.findData("Overlay"))
    panel.disparity.setCurrentIndex(panel.disparity.findData("Overlay"))

    assert page.market_volume.isHidden()
    assert [item.name() for item in page.market_chart.listDataItems()] == [
        "MA5", "MA20", "MA60", "MA120", "EMA20", *bollinger_names,
    ]
    assert any(isinstance(item, CandlestickItem) for item in page.market_chart.getPlotItem().items)
    assert page.market_indicator.isHidden()
    assert set(page._market_overlay_items) == {"rsi14", "disparity60"}
    assert len(page._market_overlay_guides["rsi14"]) == 2
    assert len(page._market_overlay_guides["disparity60"]) == 1
    assert page._market_rsi_overlay_axis.isVisible()
    assert page._market_disparity_overlay_axis.isVisible()
    assert page._market_rsi_overlay_view.viewRange()[1] == [0.0, 100.0]
    assert "MA5" in page.market_indicator_legend.text()
    assert "MA120" in page.market_indicator_legend.text()
    assert "EMA20" in page.market_indicator_legend.text()
    assert "BB(20,2)" in page.market_indicator_legend.text()
    assert "RSI14" in page.market_chart.accessibleName()
    assert "괴리60" in page.market_chart.accessibleName()
    scene_pos = page.market_chart.getPlotItem().vb.mapViewToScene(
        QtCore.QPointF(69.0, 69.0),
    )
    page._mouse_moved((scene_pos,))
    for label in (
        "MA5 70", "MA20 71", "MA60 79", "MA120 72",
        "EMA20 73", "BB 상단 79", "BB 중심 74", "BB 하단 69",
        "RSI14 89", "괴리60 149 (pp 49.00)",
    ):
        assert label in page.market_chart.toolTip()

    for control in panel.ma.values():
        control.setChecked(False)
    panel.extra_upper["ema20"].setChecked(False)
    panel.extra_upper["bollinger_bands"].setChecked(False)
    panel.rsi.setCurrentIndex(panel.rsi.findData("Off"))
    panel.disparity.setCurrentIndex(panel.disparity.findData("Off"))

    assert len(page.market_chart.listDataItems()) == 0
    assert not page._market_overlay_items
    assert not page._market_overlay_guides
    assert not page._market_rsi_overlay_axis.isVisible()
    assert not page._market_disparity_overlay_axis.isVisible()
    assert page.market_indicator_legend.isHidden()
    assert page.market_chart.accessibleName() == "시장 가격 차트"
    assert page.market_chart.toolTip() == ""
    assert page._market_frame.equals(frame)
    page.close()
    app.processEvents()


def test_dashboard_long_chart_is_bounded_preserves_extrema_and_labels_partial_span():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.market_asset.setCurrentText("S&P 500")
    page.market_period.setCurrentText("10Y")
    page.render({"dashboard_metrics": {
        "SP500": _metric(
            "SP500", 6100.0, freshness="CURRENT",
            state=DashboardDisplayState.VALUE,
        ),
    }})
    rows = 2500
    close = np.linspace(5000.0, 6100.0, rows)
    close[311] = 3500.0
    close[1777] = 9000.0
    frame = pd.DataFrame({
        "date": pd.bdate_range("2017-01-02", periods=rows),
        "close": close,
        "volume": np.arange(rows, dtype=float) + 1000.0,
        "ma60": np.linspace(4900.0, 6000.0, rows),
        "rsi14": np.linspace(30.0, 70.0, rows),
        "disparity60": np.linspace(95.0, 105.0, rows),
    })
    frame.attrs[DASHBOARD_CHART_COVERAGE_ATTR] = DashboardChartCoverage(
        period="10Y", requested_sessions=2520, available_sessions=rows,
        available_start=frame.iloc[0]["date"].date().isoformat(),
        available_end=frame.iloc[-1]["date"].date().isoformat(),
        complete=False, dataset_id="global_index_price_daily",
        series_id="SP500",
    )

    direct = _downsample_market_frame(frame)
    assert len(direct) == main_window_module.DASHBOARD_MARKET_RENDER_POINT_BUDGET
    assert direct.iloc[0]["date"] == frame.iloc[0]["date"]
    assert direct.iloc[-1]["date"] == frame.iloc[-1]["date"]
    assert direct["close"].min() == 3500.0
    assert direct["close"].max() == 9000.0
    assert direct.attrs[DASHBOARD_CHART_COVERAGE_ATTR] == frame.attrs[DASHBOARD_CHART_COVERAGE_ATTR]

    page.render_market_chart(frame)
    app.processEvents()
    assert len(page._market_frame) == main_window_module.DASHBOARD_MARKET_RENDER_POINT_BUDGET
    assert page._market_frame["close"].min() == 3500.0
    assert page._market_frame["close"].max() == 9000.0
    assert page._market_session_mapping is not None
    assert len(page._market_session_mapping.dates) == len(page._market_frame)
    assert "보유 구간 일부" in page.market_chart_status.text()
    assert "요청 10Y/2,520거래일" in page.market_chart_status.text()
    assert f"/{rows:,}거래일" in page.market_chart_status.text()
    page.close()
    app.processEvents()


@pytest.mark.parametrize("period", ("3Y", "5Y", "10Y", "MAX"))
def test_dashboard_extended_period_offscreen_smoke_is_provider_free_and_unclipped(period):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.resize(1600, 900)
    page.show()
    page.market_asset.setCurrentText("S&P 500")
    page.market_period.setCurrentText(period)
    page.render({"dashboard_metrics": {
        "SP500": _metric(
            "SP500", 6100.0, freshness="CURRENT",
            state=DashboardDisplayState.VALUE,
        ),
    }})
    frame = pd.DataFrame({
        "date": pd.bdate_range("2026-01-02", periods=120),
        "close": np.linspace(5900.0, 6100.0, 120),
        "volume": np.arange(120, dtype=float) + 1000.0,
    })
    requested = None if period == "MAX" else {
        "3Y": 756, "5Y": 1260, "10Y": 2520,
    }[period]
    frame.attrs[DASHBOARD_CHART_COVERAGE_ATTR] = DashboardChartCoverage(
        period=period, requested_sessions=requested, available_sessions=len(frame),
        available_start=frame.iloc[0]["date"].date().isoformat(),
        available_end=frame.iloc[-1]["date"].date().isoformat(),
        complete=(period == "MAX"), dataset_id="global_index_price_daily",
        series_id="SP500",
    )
    page.render_market_chart(frame)
    app.processEvents()

    position = page.market_period.mapTo(page, QtCore.QPoint(0, 0))
    assert page.market_period.currentText() == period
    assert page.market_period.isVisible() and page.reload_button.isVisible()
    assert position.x() >= 0
    assert position.x() + page.market_period.width() <= page.width()
    assert page.market_chart_status.text()
    if period == "MAX":
        assert "전체 보유" in page.market_chart_status.text()
    else:
        assert f"요청 {period}/{requested:,}거래일" in page.market_chart_status.text()
    page.close()
    app.processEvents()


def test_dashboard_daily_candles_use_session_positions_and_true_date_crosshair():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.resize(1600, 900)
    page.show()
    app.processEvents()
    page.render({"dashboard_metrics": {
        "KOSPI": _metric("KOSPI", 3000.0, freshness="CURRENT", state=DashboardDisplayState.VALUE),
    }})
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-08-14", "2026-08-17"]),
        "open": [3000.0, 3010.0], "high": [3015.0, 3025.0],
        "low": [2990.0, 3005.0], "close": [3010.0, 3020.0],
        "volume": [100.0, 120.0], "rsi14": [50.0, 52.0],
        "disparity60": [100.0, 101.0],
    })

    page.render_market_chart(frame)
    app.processEvents()
    page.market_indicator_panel.rsi.setCurrentIndex(
        page.market_indicator_panel.rsi.findData("Overlay")
    )
    page.market_indicator_panel.disparity.setCurrentIndex(
        page.market_indicator_panel.disparity.findData("Overlay")
    )

    candle = next(item for item in page.market_chart.getPlotItem().items if isinstance(item, CandlestickItem))
    assert [bar[0] for bar in candle._bars] == [0.0, 1.0]
    assert page._market_axis.tickStrings([0.0, 1.0], 1.0, 1.0) == ["2026-08-14", "2026-08-17"]
    scene_pos = page.market_chart.getPlotItem().vb.mapViewToScene(QtCore.QPointF(1.0, 3020.0))
    page._mouse_moved((scene_pos,))
    assert "2026-08-17" in page.market_chart.toolTip()
    assert "거래량 120주" in page.market_chart.toolTip()
    assert "RSI14 52.00" in page.market_chart.toolTip()
    assert "괴리60 101.00 (pp 1.00)" in page.market_chart.toolTip()
    assert page._crosshair.pos().x() == 1.0
    page.close()
    app.processEvents()


def test_chart_reference_converts_retained_intraday_timestamp_across_dst():
    winter = replace(
        _metric(
            "VIX_15M", 15.0, freshness="CURRENT",
            state=DashboardDisplayState.VALUE,
        ),
        source_timestamp="2026-01-15T21:15:00-05:00",
    )
    summer = replace(
        winter,
        source_timestamp="2026-07-15T20:15:00-04:00",
    )

    winter_visible, winter_detail = _chart_reference_metadata(
        winter, daily_session=False, market_label="미국장",
    )
    summer_visible, summer_detail = _chart_reference_metadata(
        summer, daily_session=False, market_label="미국장",
    )

    assert winter_visible == "기준시각 2026-01-16 11:15 KST"
    assert summer_visible == "기준시각 2026-07-16 09:15 KST"
    assert "original_timestamp=2026-01-15T21:15:00-05:00" in winter_detail
    assert "original_timestamp=2026-07-15T20:15:00-04:00" in summer_detail


def test_daily_chart_preserves_us_session_date_and_uses_only_retained_kst_time():
    metric = _metric(
        "SP500", 6000.0, freshness="CURRENT",
        state=DashboardDisplayState.VALUE, as_of="2026-08-18",
    )
    retained = pd.DataFrame({
        "date": pd.to_datetime(["2026-08-18"]),
        "close": [6000.0],
        "retrieved_at": ["2026-08-19T00:30:00-04:00"],
    })

    visible, detail = _chart_reference_metadata(
        metric, retained, daily_session=True, market_label="미국장",
    )
    without_retained_time, _ = _chart_reference_metadata(
        replace(metric, source_timestamp="2026-08-19T04:30:00+00:00"),
        retained[["date", "close"]], daily_session=True, market_label="미국장",
    )

    assert visible == "미국장 기준일 2026-08-18 · 수집 한국시간 2026-08-19 13:30 KST"
    assert "source_market_session_date=2026-08-18" in detail
    assert "original_timestamp=2026-08-19T00:30:00-04:00" in detail
    assert without_retained_time == "미국장 기준일 2026-08-18"


def test_dashboard_chart_header_and_tooltip_move_source_to_data_status():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.market_asset.setCurrentText("S&P 500")
    metric = _metric(
        "SP500", 6000.0, freshness="CURRENT",
        state=DashboardDisplayState.VALUE, as_of="2026-08-18",
    )
    page.render({"dashboard_metrics": {"SP500": metric}})
    page.render_market_chart(pd.DataFrame({
        "date": pd.to_datetime(["2026-08-18"]),
        "close": [6000.0],
        "volume": [100.0],
        "retrieved_at": ["2026-08-19T00:30:00-04:00"],
    }))

    assert "출처" not in page.market_chart_status.text()
    assert "fixture source" not in page.market_chart_status.text()
    assert "미국장 기준일 2026-08-18" in page.market_chart_status.text()
    assert "수집 한국시간 2026-08-19 13:30 KST" in page.market_chart_status.text()
    assert "source=fixture source" not in page.market_chart_status.toolTip()
    assert "Data Status" in page.market_chart_status.toolTip()
    page.close()
    app.processEvents()


def test_dashboard_indicator_button_exposes_truthful_rsi_and_disparity_panels():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render({"dashboard_metrics": {
        "KOSPI": _metric(
            "KOSPI", 3000.0, freshness="CURRENT",
            state=DashboardDisplayState.VALUE,
        ),
    }})
    frame = pd.DataFrame({
        "date": pd.bdate_range("2026-04-01", periods=80),
        "open": np.linspace(100, 120, 80),
        "high": np.linspace(101, 121, 80),
        "low": np.linspace(99, 119, 80),
        "close": np.linspace(100, 120, 80),
        "volume": np.arange(80) + 100,
        "rsi14": np.linspace(35, 65, 80),
        "disparity60": np.linspace(98, 102, 80),
    })
    page.render_market_chart(frame)
    panel = page.market_indicator_panel

    panel.rsi.setCurrentIndex(panel.rsi.findData("Panel"))
    assert not page.market_indicator.isHidden()
    assert page.market_indicator.getAxis("left").labelText == "RSI14"
    assert page.market_indicator.viewRange()[1] == [0.0, 100.0]

    panel.disparity.setCurrentIndex(panel.disparity.findData("Panel"))
    assert panel.rsi.currentData() == "Off"
    assert page.market_indicator.getAxis("left").labelText == "60일선 대비 %"
    page.close()
    app.processEvents()


def test_dashboard_daily_chart_discloses_genuine_source_session_gap():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render({"dashboard_metrics": {
        "SP500": _metric("SP500", 6000.0, freshness="CURRENT", state=DashboardDisplayState.VALUE),
    }})
    page.market_asset.setCurrentText("S&P 500")
    page.render_market_chart(pd.DataFrame({
        "date": pd.to_datetime(["2026-01-20", "2026-01-22"]),
        "close": [6000.0, 6025.0], "volume": [100.0, 120.0],
    }))

    assert "source missing XNYS sessions: 2026-01-21" in page.market_chart_status.text()
    page.close()
    app.processEvents()


def test_dashboard_nq_daily_candles_use_the_same_session_axis():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    metric = _metric(
        "NQ_FUTURES", 21000.0, freshness="CURRENT",
        state=DashboardDisplayState.VALUE, unit="futures price",
    )
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-02-06", "2026-02-09"]),
        "open": [20900.0, 21000.0], "high": [21050.0, 21100.0],
        "low": [20850.0, 20950.0], "close": [21000.0, 21050.0],
    })

    page.render({
        "dashboard_metrics": {"NQ_FUTURES": metric},
        "dashboard_series": {"NQ_FUTURES": DashboardSeriesView(metric, frame)},
    })

    candle = next(item for item in page.nq_chart.getPlotItem().items if isinstance(item, CandlestickItem))
    assert [bar[0] for bar in candle._bars] == [0.0, 1.0]
    assert page._nq_axis.tickStrings([0.0, 1.0], 1.0, 1.0) == ["2026-02-06", "2026-02-09"]
    page.close()
    app.processEvents()


def test_dashboard_kospi_candles_fail_closed_for_missing_duplicate_or_invalid_ohlc():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render({"dashboard_metrics": {
        "KOSPI": _metric("KOSPI", 3000.0, freshness="CURRENT", state=DashboardDisplayState.VALUE),
    }})
    duplicate = pd.DataFrame({
        "date": pd.to_datetime(["2026-08-17", "2026-08-17"]),
        "open": [3000.0, 3001.0], "high": [3010.0, 3011.0],
        "low": [2990.0, 2991.0], "close": [3005.0, 3006.0], "volume": [1, 2],
    })
    page.render_market_chart(duplicate)

    assert page._market_frame.empty
    assert "duplicate sessions" in page.market_chart_status.text()
    assert not any(isinstance(item, CandlestickItem) for item in page.market_chart.getPlotItem().items)

    invalid = duplicate.iloc[:1].copy()
    invalid.loc[:, "high"] = 2999.0
    page.render_market_chart(invalid)
    assert page._market_frame.empty
    assert "inconsistent high/low range" in page.market_chart_status.text()
    page.close()
    app.processEvents()


def test_dashboard_cards_suppress_stale_values_and_keep_current_values_visible():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render({
        "dashboard_metrics": {
            "SP500": _metric("SP500", None, freshness="STALE", state=DashboardDisplayState.REFRESH_REQUIRED, as_of="2026-08-14"),
            "SOXX": _metric("SOXX", 531.39, freshness="CURRENT", state=DashboardDisplayState.VALUE),
        },
    })

    stale = page.market_cards["SP500"].body.text()
    current = page.market_cards["SOXX"].body.text()
    current_meta = page.market_cards["SOXX"].meta.text()
    assert "갱신 필요" in stale
    assert "6,500" not in stale
    assert "531.39" in current
    assert current_meta == "확정·08-18"
    assert not page.market_cards["SOXX"].meta.isHidden()
    assert page.market_cards["SP500"].meta.text() == "갱신·08-14"
    assert page.market_cards["SOXX"].meta.accessibleName() == "확정 · 기준 08-18"
    assert page.market_cards["SP500"].meta.accessibleName() == "갱신 · 기준 08-14"
    assert not page.market_cards["SP500"].meta.isHidden()
    assert "as_of=2026-08-18" in page.market_cards["SOXX"].toolTip()
    assert "source=fixture source" in page.market_cards["SOXX"].toolTip()
    assert "설명용" in page.market_cards["SOXX"].toolTip()
    assert page.market_cards["SOXX"].badge.text() == "확정"
    assert page.market_cards["SP500"].badge.text() == "갱신"
    assert "표시 상태=최신 확정" in page.market_cards["SOXX"].toolTip()
    assert "freshness=CURRENT" in page.market_cards["SOXX"].toolTip()
    page.close()
    app.processEvents()


def test_dashboard_cards_prioritize_daily_5_and_20_day_comparisons_without_signals():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.apply_preferences(replace(main_window_module.DEFAULT_PREFERENCES, density="DETAIL"))
    current = _metric(
        "SOXX", 119.0, freshness="CURRENT", state=DashboardDisplayState.VALUE,
        unit="USD",
    )
    stale = _metric(
        "SP500", None, freshness="STALE",
        state=DashboardDisplayState.REFRESH_REQUIRED,
    )
    page.render({
        "dashboard_metrics": {"SOXX": current, "SP500": stale},
        "daily_average_comparisons": {"SOXX": _comparison("SOXX")},
    })

    soxx = page.market_cards["SOXX"]
    assert soxx.comparison.text() == "5일 평균 +1.2%\n20일 평균 -0.8%"
    assert not soxx.comparison.isHidden()
    assert soxx.badge.text() == "확정"
    assert "coverage_5=2026-08-12..2026-08-18 (5 observations)" in soxx.toolTip()
    assert "comparison_kind=relative_percent" in soxx.toolTip()
    assert "투자 신호" in soxx.comparison.accessibleName()
    assert page.market_cards["SP500"].comparison.text() == "5일 평균 N/A\n20일 평균 N/A"
    assert "119" not in page.market_cards["SP500"].body.text()
    assert page.market_cards["SP500"].badge.text() == "갱신"
    page.close()
    app.processEvents()


def _market_valuation_view(
    market: str, *, state: DashboardDisplayState = DashboardDisplayState.VALUE,
) -> MarketValuationView:
    index_code = "1001" if market == "KOSPI" else "2001"
    available = state is DashboardDisplayState.VALUE
    return MarketValuationView(
        market=market,
        index_code=index_code,
        as_of="2026-08-25",
        expected_as_of="2026-08-25",
        weighted_per=15.25 if available else None,
        weighted_pbr=1.35 if available else None,
        per_mean=13.0 if available else None,
        pbr_mean=1.2 if available else None,
        per_median=12.5 if available else None,
        pbr_median=1.1 if available else None,
        per_percentile=76.0 if available else None,
        pbr_percentile=68.0 if available else None,
        per_observations=6567 if available else 0,
        pbr_observations=6500 if available else 0,
        per_baseline_start="2000-01-04" if available else None,
        per_baseline_end="2026-08-25" if available else None,
        pbr_baseline_start="2000-02-01" if available else None,
        pbr_baseline_end="2026-08-25" if available else None,
        baseline_start="2000-01-04" if available else None,
        baseline_end="2026-08-25" if available else None,
        source="KRX_MDCSTAT00702",
        display_state=state,
        unavailable_reason=(
            None if available else "KR_INDEX_FUNDAMENTAL_STALE"
        ),
        rolling_windows=(
            MarketValuationWindowView(
                window_years=5,
                per_percentile=72.0,
                pbr_percentile=63.0,
                per_observations=1222,
                pbr_observations=1200,
                per_baseline_start="2021-08-25",
                per_baseline_end="2026-08-25",
                pbr_baseline_start="2021-09-01",
                pbr_baseline_end="2026-08-25",
            ),
            MarketValuationWindowView(
                window_years=10,
                per_percentile=76.0,
                pbr_percentile=68.0,
                per_observations=2451,
                pbr_observations=2400,
                per_baseline_start="2016-08-25",
                per_baseline_end="2026-08-25",
                pbr_baseline_start="2016-09-01",
                pbr_baseline_end="2026-08-25",
            ),
        ) if available else (),
    )


def test_dashboard_renders_kospi_kosdaq_valuation_context_and_suppresses_stale():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.resize(1280, 840)
    page.show()
    page.render({
        "market_valuation_views": {
            "KOSPI": _market_valuation_view("KOSPI"),
            "KOSDAQ": _market_valuation_view(
                "KOSDAQ", state=DashboardDisplayState.REFRESH_REQUIRED,
            ),
        },
    })
    app.processEvents()

    kospi = page.market_valuation_labels["KOSPI"]
    kosdaq = page.market_valuation_labels["KOSDAQ"]
    assert "PER 15.25 · 5Y 72% · 10Y 76%" in kospi.text()
    assert "PBR 1.35 · 5Y 63% · 10Y 68%" in kospi.text()
    assert "기준 2026-08-25" in kospi.text()
    assert "중앙 12.50 · 중앙대비 +22% · 역사순위 76%(상위권)" in kospi.toolTip()
    assert "관측 6567 · 2000-01-04~2026-08-25" in kospi.toolTip()
    assert "PER_5Y percentile=72.0; observations=1222" in kospi.toolTip()
    assert "PBR_10Y percentile=68.0; observations=2400" in kospi.toolTip()
    assert "index_code=1001" in kospi.toolTip()
    assert "pit_status=NON_PREDICTIVE" in kospi.toolTip()
    assert "ratio_horizon=PROVIDER_DEFINED_UNRESOLVED_NOT_FORWARD" in kospi.toolTip()
    assert "earnings_momentum=UNSUPPORTED; market_regime=UNAVAILABLE" in kospi.toolTip()
    assert page.market_valuation_charts["KOSPI"]._values == {
        "PER": (72.0, 76.0), "PBR": (63.0, 68.0),
    }
    assert page.market_valuation_charts["KOSDAQ"]._values == {
        "PER": (None, None), "PBR": (None, None),
    }
    assert "Forward EPS·Revision·ROE 미지원" in page.market_valuation_regime_gate.text()
    assert "고점·저점 판정 보류" in page.market_valuation_regime_gate.text()
    assert "시장 국면 근거 1/3 · KOSPI 밸류에이션" in page.market_valuation_regime_gate.text()
    assert "미반영 가격·추세·변동성, Forward EPS·Revision·ROE 미지원" in page.market_valuation_regime_gate.text()
    assert "multiple expansion" in page.market_valuation_regime_gate.toolTip()
    assert "15.25" not in kosdaq.text() and "1.35" not in kosdaq.text()
    assert "현재 표시 불가" in kosdaq.text() or "확인" in kosdaq.text()
    assert "KR_INDEX_FUNDAMENTAL_STALE" in kosdaq.toolTip()
    page.render({
        "market_valuation_views": {
            market: _market_valuation_view(market)
            for market in ("KOSPI", "KOSDAQ")
        },
    })
    app.processEvents()
    for label in page.market_valuation_labels.values():
        assert label.sizeHint().height() <= label.height()
        assert (
            label.geometry().bottom()
            <= page.market_valuation_panel.contentsRect().bottom()
        )
    page.render({
        "market_valuation_views": {
            "KOSPI": _market_valuation_view(
                "KOSPI", state=DashboardDisplayState.REFRESH_REQUIRED,
            ),
        },
    })
    assert page.market_valuation_charts["KOSPI"]._values == {
        "PER": (None, None), "PBR": (None, None),
    }
    assert "15.25" not in page.market_valuation_labels["KOSPI"].text()
    page.close()
    app.processEvents()


def test_dashboard_valuation_does_not_substitute_all_history_for_bad_windows():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    valid = _market_valuation_view("KOSPI")
    duplicate_window = replace(valid.rolling_windows[0], window_years=10)

    page.render({
        "market_valuation_views": {
            "KOSPI": replace(
                valid,
                rolling_windows=(duplicate_window, valid.rolling_windows[1]),
            ),
        },
    })

    text = page.market_valuation_labels["KOSPI"].text()
    assert "PER 15.25 · 5Y N/A · 10Y N/A" in text
    assert "PBR 1.35 · 5Y N/A · 10Y N/A" in text
    assert page.market_valuation_charts["KOSPI"]._values == {
        "PER": (None, None), "PBR": (None, None),
    }
    assert "역사 76%" not in text
    page.close()
    app.processEvents()


def test_dashboard_valuation_suppresses_out_of_range_or_future_window_metadata():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    valid = _market_valuation_view("KOSPI")
    malformed_5y = replace(
        valid.rolling_windows[0],
        per_percentile=999.0,
        per_baseline_end="2099-01-01",
    )

    page.render({
        "market_valuation_views": {
            "KOSPI": replace(
                valid,
                rolling_windows=(malformed_5y, valid.rolling_windows[1]),
            ),
        },
    })

    text = page.market_valuation_labels["KOSPI"].text()
    assert "PER 15.25 · 5Y N/A · 10Y N/A" in text
    assert "999%" not in text
    assert page.market_valuation_charts["KOSPI"]._values["PER"] == (None, None)
    assert "PBR 1.35 · 5Y 63% · 10Y 68%" in text
    assert page.market_valuation_charts["KOSPI"]._values["PBR"] == (63.0, 68.0)

    noncanonical_5y = replace(
        valid.rolling_windows[0],
        per_baseline_start="2021-08-25T00:00:00",
        per_baseline_end="2026-08-25 00:00:00",
    )
    page.render({
        "market_valuation_views": {
            "KOSPI": replace(
                valid,
                rolling_windows=(noncanonical_5y, valid.rolling_windows[1]),
            ),
        },
    })
    text = page.market_valuation_labels["KOSPI"].text()
    assert "PER 15.25 · 5Y N/A · 10Y N/A" in text
    assert page.market_valuation_charts["KOSPI"]._values["PER"] == (None, None)
    page.close()
    app.processEvents()


def test_dashboard_valuation_marks_historically_low_ratios_without_signal_language():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    low = replace(
        _market_valuation_view("KOSPI"),
        weighted_per=8.0,
        per_mean=11.0,
        per_median=10.0,
        per_percentile=8.0,
        weighted_pbr=0.9,
        pbr_mean=1.2,
        pbr_median=1.0,
        pbr_percentile=22.0,
        rolling_windows=(
            MarketValuationWindowView(
                5, 8.0, 22.0, 1222, 1200,
                "2021-08-25", "2026-08-25", "2021-09-01", "2026-08-25",
            ),
            MarketValuationWindowView(
                10, 12.0, 25.0, 2451, 2400,
                "2016-08-25", "2026-08-25", "2016-09-01", "2026-08-25",
            ),
        ),
    )

    page.render({"market_valuation_views": {"KOSPI": low}})
    text = page.market_valuation_labels["KOSPI"].text()

    assert "PER 8.00 · 5Y 8% · 10Y 12%" in text
    assert "PBR 0.90 · 5Y 22% · 10Y 25%" in text
    assert page.market_valuation_charts["KOSPI"]._values == {
        "PER": (8.0, 12.0), "PBR": (22.0, 25.0),
    }
    assert "저평가" not in text and "매수" not in text
    page.close()
    app.processEvents()


def test_rate_rows_use_basis_point_average_differences_and_keep_exact_details():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    metric = _metric(
        "UST10", 4.19, freshness="CURRENT", state=DashboardDisplayState.VALUE,
        unit="percent",
    )
    view = TreasuryRateView(
        view_id="UST10", label="미국 10Y 금리", official_daily=metric,
        intraday_quote=None, official_provider="FRED",
        official_data_type="OFFICIAL_DAILY_YIELD",
        intraday_provider=None, intraday_data_type=None,
    )
    comparison = _comparison(
        "UST10", kind="basis_points", comparison_5=2.0, comparison_20=-9.5,
    )
    page.render({
        "dashboard_metrics": {"UST10": metric},
        "treasury_rate_views": {"UST10": view},
        "daily_average_comparisons": {"UST10": comparison},
    })

    row = page.rate_rows["UST10"]
    assert row.change.text() == "5일 +2.0bp · 20일 -9.5bp"
    assert "%" not in row.change.text()
    assert row.change.property("tone") == "neutral"
    assert "fixture source" in row.toolTip()
    assert "comparison_kind=basis_points" in row.toolTip()
    assert "descriptive comparison only; not a signal" in row.toolTip()
    page.close()
    app.processEvents()


def test_global_chart_keeps_retained_context_while_current_metric_is_gated():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-08-18"]), "close": [1.0]})

    page.market_asset.setCurrentText("S&P 500")
    page.render({"dashboard_metrics": {
        "SP500": _metric(
            "SP500", None, freshness="STALE",
            state=DashboardDisplayState.REFRESH_REQUIRED, as_of="2026-08-14",
        ),
    }})
    page.render_market_chart(frame)
    # A stale retained chart stays visible for context; only a current-data
    # claim/action remains blocked.
    assert not page._market_frame.empty
    assert page.market_chart.listDataItems()

    page.market_asset.setCurrentText("Nasdaq")
    page.render({"dashboard_metrics": {
        "NASDAQ": _metric(
            "NASDAQ", None, freshness="UNKNOWN",
            state=DashboardDisplayState.UNAVAILABLE,
        ),
    }})
    page.render_market_chart(frame)
    assert not page._market_frame.empty
    assert page.market_chart.listDataItems()
    page.close()
    app.processEvents()


def test_global_chart_retained_stale_frame_stays_visible_with_warning():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.market_asset.setCurrentText("S&P 500")
    metric = replace(
        _metric(
            "SP500", 6_400.0, freshness="STALE",
            state=DashboardDisplayState.VALUE, as_of="2026-08-19",
        ),
        expected_as_of="2026-08-20",
        unavailable_reason=(
            "STALE retained history: as_of=2026-08-19, expected=2026-08-20; "
            "current-data claims and actions remain blocked."
        ),
    )
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-08-18", "2026-08-19"]),
        "close": [6_390.0, 6_400.0],
        "volume": [1_000.0, 1_100.0],
    })
    page.render({"dashboard_metrics": {"SP500": metric}})

    page.render_market_chart(frame)

    assert not page._market_frame.empty
    assert page.market_chart_status.text().startswith("STALE RETAINED HISTORY:")
    assert "expected=2026-08-20" in page.market_chart_status.text()
    assert "warning=STALE retained history" in page.market_chart_status.toolTip()
    page.close()
    app.processEvents()


def test_kospi_retained_stale_history_stays_visible_while_current_gauges_are_withheld():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.market_asset.setCurrentText("KOSPI")
    metric = replace(
        _metric(
            "KOSPI", None, freshness="STALE",
            state=DashboardDisplayState.REFRESH_REQUIRED, as_of="2026-08-21",
        ),
        expected_as_of="2026-08-24",
        unavailable_reason=(
            "STALE retained history: as_of=2026-08-21, expected=2026-08-24; "
            "current-data claims and actions remain blocked."
        ),
    )
    frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-08-20", "2026-08-21"]),
        "open": [3090.0, 3100.0], "high": [3105.0, 3115.0],
        "low": [3085.0, 3095.0], "close": [3100.0, 3110.0],
        "volume": [1_000_000.0, 1_100_000.0],
    })
    page.render({"dashboard_metrics": {"KOSPI": metric}})

    page.render_market_chart(frame)

    assert not page._market_frame.empty
    assert any(
        isinstance(item, CandlestickItem)
        for item in page.market_chart.getPlotItem().items
    )
    assert page.market_chart_status.text().startswith("STALE RETAINED HISTORY:")
    assert "한국장 기준일 2026-08-21" in page.market_chart_status.text()
    assert "current gauge withheld" in page.momentum_summary.text()
    assert "warning=STALE retained history" in page.market_chart_status.toolTip()
    page.close()
    app.processEvents()


def test_yahoo_commodity_futures_cards_are_completed_daily_not_realtime():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    metrics = {
        key: _metric(
            key, value, freshness="CURRENT", state=DashboardDisplayState.VALUE,
            dataset_id="global_commodity_futures_daily",
        )
        for key, value in (
            ("NQ_FUTURES", 29_586.0), ("GOLD", 4_366.0), ("WTI", 84.94),
        )
    }

    page.render({"dashboard_metrics": metrics})

    for key in metrics:
        card = page.market_cards[key]
        assert not card.badge.isVisible()
        assert card.meta.text() == "확정·08-18"
        assert card.meta.accessibleName() == "확정 · Yahoo 기준 2026-08-18"
        assert "freshness=CURRENT" in card.toolTip()
    page.close()
    app.processEvents()


def test_dashboard_volume_pcr_preserves_typed_ratio_precision():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render({"dashboard_metrics": {
        "VOLUME_PCR": _metric(
            "VOLUME_PCR", 1.0964197868270018,
            freshness="EXPECTED_LAG", state=DashboardDisplayState.VALUE,
            unit="ratio",
        ),
    }})

    card = page.derivative_cards["VOLUME_PCR"]
    assert card.body.text() == "1.09642"
    assert "value=1.09642" in card.toolTip()
    page.close()
    app.processEvents()


def test_dashboard_us_option_scopes_reject_numeric_fallback_and_preserve_korean_cards():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render({"dashboard_metrics": {
        "US_OPTION_PCR": _metric(
            "US_OPTION_PCR", 9.87654,
            freshness="CURRENT", state=DashboardDisplayState.VALUE,
            unit="ratio", dataset_id="forbidden_fallback_fixture",
        ),
        "VOLUME_PCR": _metric(
            "VOLUME_PCR", 1.0964197868270018,
            freshness="EXPECTED_LAG", state=DashboardDisplayState.VALUE,
            unit="ratio", dataset_id="kr_kospi200_option_pcr_daily",
        ),
        "OI_PCR": _metric(
            "OI_PCR", 1.63642,
            freshness="EXPECTED_LAG", state=DashboardDisplayState.VALUE,
            unit="ratio", dataset_id="kr_kospi200_option_pcr_daily",
        ),
        "CALL_WALL": _metric(
            "CALL_WALL", 1597.5,
            freshness="EXPECTED_LAG", state=DashboardDisplayState.VALUE,
            dataset_id="kr_kospi200_option_wall_daily",
        ),
        "PUT_WALL": _metric(
            "PUT_WALL", 700.0,
            freshness="EXPECTED_LAG", state=DashboardDisplayState.VALUE,
            dataset_id="kr_kospi200_option_wall_daily",
        ),
    }})

    us_card = page.derivative_cards["US_OPTION_PCR"]
    assert "9.87654" not in us_card.body.text()
    assert "9.87654" not in us_card.meta.text()
    assert "9.87654" not in us_card.toolTip()
    assert us_card.badge.text() == "숫자 차단"
    assert page.derivative_cards["VOLUME_PCR"].body.text() == "1.09642"
    assert page.derivative_cards["OI_PCR"].body.text() == "1.63642"
    assert page.derivative_cards["WALL"].body.text() == "C 1,597.50 · P 700.00"
    assert "put_volume / call_volume" in page.derivative_cards["VOLUME_PCR"].toolTip()
    assert "put_open_interest / call_open_interest" in page.derivative_cards["OI_PCR"].toolTip()
    assert "active/gamma wall" in page.derivative_cards["WALL"].toolTip()
    page.close()
    app.processEvents()


def test_dashboard_vix_futures_unavailable_gate_is_distinct_and_numeric_free():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    spot = _metric(
        "VIX", 15.2, freshness="CURRENT", state=DashboardDisplayState.VALUE,
        dataset_id="fred_vix_daily",
    )
    page.render({"dashboard_metrics": {"VIX": spot}})

    assert "VIX" not in page.market_cards
    future_card = page.derivative_cards["VIX_FUTURES"]
    assert future_card.title.text() == "VIX 선물 · CFE VX"
    assert future_card.body.text() == "현재 표시 불가"
    assert future_card.meta.text() == "Yahoo→CFE VX · 식별 검증 필요"
    assert future_card.badge.text() == "미확인"
    tooltip = future_card.toolTip()
    for expected in (
        "route_status=UNAVAILABLE_IDENTITY_UNVERIFIED",
        "exchange=CFE",
        "exchange_product_root=VX",
        "provider_symbol=UNVERIFIED",
        "series_kind=UNVERIFIED",
        "contract_symbol=UNVERIFIED",
        "expiry=UNVERIFIED",
        "roll_policy=UNVERIFIED",
        "YAHOO_PROVIDER_SYMBOL_NOT_EVIDENCED",
        "EXACT_EXPIRY_OR_PROVIDER_ROLL_POLICY_NOT_EVIDENCED",
    ):
        assert expected in tooltip
    assert "^VIX·ETN·ETF·옵션·유사 선물·Yahoo·ORATS 값을 대체하지 않습니다" in tooltip
    assert not any(term in future_card.body.text() + future_card.meta.text() + tooltip
                   for term in ("premium", "discount", "contango", "backwardation"))

    default_gate = build_vix_futures_dashboard_view()
    stale_gate = replace(
        default_gate,
        metric=replace(
            default_gate.metric,
            freshness="STALE",
            display_state=DashboardDisplayState.REFRESH_REQUIRED,
            unavailable_reason="stale fixture",
        ),
    )
    tampered_gate = replace(
        default_gate,
        provider_symbol="^VIX",
        metric=_metric(
            "VIX_FUTURES", 99.87654,
            freshness="CURRENT", state=DashboardDisplayState.VALUE,
            dataset_id="forbidden_lookalike_fixture",
        ),
    )
    for gate in (default_gate, stale_gate, tampered_gate):
        page._render_vix_futures_unavailable(gate)
        assert future_card.body.text() == "현재 표시 불가"
        assert "99.87654" not in future_card.body.text()
        assert "99.87654" not in future_card.meta.text()
        assert "99.87654" not in future_card.toolTip()
        assert "15.20" not in future_card.body.text()

    page.close()
    app.processEvents()


def test_dashboard_kospi200_breadth_is_scoped_and_numeric_free_when_stale():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    current = {
        key: _metric(
            key, value, freshness="CURRENT", state=DashboardDisplayState.VALUE,
            as_of="2026-08-12", unit="constituents",
            dataset_id="kr_kospi200_breadth_daily",
        )
        for key, value in (
            ("KOSPI200_ADVANCING", 81.0),
            ("KOSPI200_DECLINING", 111.0),
            ("KOSPI200_UNCHANGED", 8.0),
        )
    }
    page.render({"dashboard_metrics": current, "dashboard_series": {}})
    page.resize(1600, 840)
    page.show()
    app.processEvents()
    assert "상승 81" in page.kospi200_breadth.body.text()
    assert "하락 111" in page.kospi200_breadth.body.text()
    assert "보합 8" in page.kospi200_breadth.body.text()
    assert "KOSPI200-only" not in page.kospi200_breadth.body.text()
    assert "2026-08-12" in page.kospi200_breadth.body.text()
    assert "scope=KOSPI200-only exact constituent date" in page.kospi200_breadth.toolTip()
    metrics = page.kospi200_breadth.body.fontMetrics()
    two_line_height = max(36, metrics.boundingRect(
        QtCore.QRect(0, 0, 2000, 1000), QtCore.Qt.TextWordWrap, "첫째 줄\n둘째 줄",
    ).height())
    assert page.kospi200_breadth.height() == 90
    assert page.kospi200_breadth.title.height() == metrics.height()
    assert page.kospi200_breadth.body.height() == two_line_height
    assert page.kospi200_breadth.body.geometry().bottom() <= page.kospi200_breadth.contentsRect().bottom()
    assert page.horizontalScrollBar().maximum() == 0
    assert page.verticalScrollBar().maximum() > 0

    stale = {
        key: _metric(
            key, None, freshness="STALE", state=DashboardDisplayState.REFRESH_REQUIRED,
            as_of="2026-08-12", unit="constituents",
            dataset_id="kr_kospi200_breadth_daily",
        )
        for key in current
    }
    page.render({"dashboard_metrics": stale, "dashboard_series": {}})
    assert "81" not in page.kospi200_breadth.body.text()
    assert "111" not in page.kospi200_breadth.body.text()
    assert "시장폭 미반영" in page.kospi200_breadth.body.text()
    assert "fixture unavailable" not in page.kospi200_breadth.body.text()
    assert page.kospi200_breadth.body.height() == two_line_height

    page.resize(2560, 1330)
    app.processEvents()
    assert page.kospi200_breadth.body.geometry().bottom() <= page.kospi200_breadth.contentsRect().bottom()
    assert page.horizontalScrollBar().maximum() == 0
    assert page.verticalScrollBar().maximum() == 0
    page.close()
    app.processEvents()


def test_toss_short_watchlist_local_adapter_is_exact_and_fail_closed(tmp_path):
    _write_toss_short_watchlist_fixture(tmp_path)
    service = DashboardService(tmp_path)

    current = service.toss_short_watchlist_view()
    assert current.displays_values
    assert current.as_of == "2026-08-19"
    assert current.source_scope == "KRX_ONLY_PROVIDER_EOD"
    assert not current.automation_enabled
    assert [(member.symbol, member.market) for member in current.members] == [
        ("005930", "KOSPI"), ("000660", "KOSPI"),
    ]
    assert [member.short_selling_volume for member in current.members] == [
        1_586_828, 248_815,
    ]

    stale = service.toss_short_watchlist_view(expected_date="2026-08-20")
    assert not stale.displays_values
    assert stale.members == ()
    assert stale.display_state is DashboardDisplayState.REFRESH_REQUIRED
    assert stale.freshness == "STALE"
    assert "2026-08-19" in (stale.unavailable_reason or "")
    assert "2026-08-20" in (stale.unavailable_reason or "")

    state_path = tmp_path / "data/state/toss_equity_short_watchlist_daily.json"
    checkpoint = json.loads(state_path.read_text(encoding="utf-8"))
    checkpoint["status"] = "RUNNING"
    state_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    blocked = service.toss_short_watchlist_view()
    assert not blocked.displays_values
    assert blocked.members == ()
    assert blocked.display_state is DashboardDisplayState.PROHIBITED
    assert "완전히 성공한 상태가 아닙니다" in (blocked.unavailable_reason or "")

    missing = DashboardService(tmp_path / "missing").toss_short_watchlist_view()
    assert not missing.displays_values
    assert missing.display_state is DashboardDisplayState.UNAVAILABLE
    assert "보존 데이터와 완료 checkpoint가 없습니다" in (missing.unavailable_reason or "")


def test_dashboard_toss_short_watchlist_is_provider_specific_and_clears_numbers(tmp_path):
    _write_toss_short_watchlist_fixture(tmp_path)
    service = DashboardService(tmp_path)
    current = service.toss_short_watchlist_view()
    stale = service.toss_short_watchlist_view(expected_date="2026-08-20")
    page = DashboardPage()

    page.render({"dashboard_metrics": {}, "toss_short_watchlist": current})
    assert page.toss_short_watchlist.title.text() == "Toss 종목별 EOD · KRX-only"
    assert "삼성전자 1,586,828주 · 396,498,888,250원" in page.toss_short_watchlist.body.text()
    assert "SK하이닉스 248,815주 · 377,019,707,500원" in page.toss_short_watchlist.body.text()
    assert "기준 2026-08-19" in page.toss_short_watchlist.body.text()
    assert "공식 KRX 시장 전체" in page.toss_short_watchlist.toolTip()
    assert "공매도 잔고를 대체·혼합하지 않습니다" in page.toss_short_watchlist.toolTip()

    page.render({"dashboard_metrics": {}, "toss_short_watchlist": stale})
    assert "종목별 EOD 미반영" in page.toss_short_watchlist.body.text()
    assert "1,586,828" not in page.toss_short_watchlist.body.text()
    assert "396,498,888,250" not in page.toss_short_watchlist.body.text()
    assert stale.unavailable_reason not in page.toss_short_watchlist.body.text()
    assert stale.unavailable_reason in page.toss_short_watchlist.toolTip()

    unavailable = replace(
        stale, display_state=DashboardDisplayState.UNAVAILABLE,
        freshness="UNKNOWN", unavailable_reason="fixture local file missing",
    )
    page.render({"dashboard_metrics": {}, "toss_short_watchlist": unavailable})
    assert "종목별 EOD 미반영" in page.toss_short_watchlist.body.text()
    assert "fixture local file missing" not in page.toss_short_watchlist.body.text()
    assert "fixture local file missing" in page.toss_short_watchlist.toolTip()
    assert "248,815" not in page.toss_short_watchlist.body.text()
    page.close()


def test_dashboard_vix_source_view_keeps_fred_primary_and_yahoo_quote_separate():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    official = _metric(
        "VIX", 15.2, freshness="EXPECTED_LAG",
        state=DashboardDisplayState.VALUE, as_of="2026-08-17",
        dataset_id="fred_vix_daily", change_pct=6.6,
    )
    quote = replace(
        _metric(
            "VIX_INTRADAY_15M", 14.91, freshness="CURRENT",
            state=DashboardDisplayState.VALUE,
            as_of="2026-08-20 05:00 KST",
            dataset_id="market_price_15m_observation",
        ),
        source_timestamp="2026-08-19T20:00:00+00:00",
        delay_status="INDICATIVE_DELAYED_NOT_LICENSED_REALTIME",
        completed_bar=True,
    )
    view = VIXSourceView(
        view_id="VIX", label="VIX", official_daily=official,
        intraday_quote=quote, official_provider="FRED / VIXCLS",
        official_data_type="COMPLETED_DAILY_PRIMARY",
        intraday_provider="Yahoo / ^VIX",
        intraday_data_type="INDICATIVE_DELAYED_PROVIDER_SUBSET_15M",
    )
    spark = DashboardSparklineView(
        asset="VIX", lane_id="CBOE_VIX", series_id="^VIX",
        frame=pd.DataFrame({
            "date": pd.date_range("2026-08-19 13:45", periods=26, freq="15min", tz="UTC"),
            "value": np.linspace(14.2, 14.91, 26),
        }),
        interval="15m", session_label="직전 완료장 2026-08-19",
        session_date="2026-08-19",
        visual_window="Cboe/Yahoo 현물 VIX 정규 산출·배포 구간 · 24시간 아님",
        as_of_kst="2026-08-20 05:00 KST",
        source_timestamp="2026-08-19T20:00:00+00:00",
        source="Yahoo ^VIX indicative/delayed provider subset",
        freshness="CURRENT", display_state=DashboardDisplayState.VALUE,
        unavailable_reason=None,
    )

    page.render({
        "dashboard_metrics": {"VIX": official, "VIX_INTRADAY_15M": quote},
        "dashboard_series": {}, "vix_source_views": {"VIX": view},
        "market_card_sparklines": {"VIX": spark},
    })
    page.resize(1600, 900)
    page.show()
    app.processEvents()

    # VIX remains available to typed services/derivatives, but the user-owned
    # nine-card market strip intentionally excludes it.
    assert "VIX" not in page.market_cards
    page.close()
    app.processEvents()
    return

    card = page.market_cards["VIX"]
    assert card.title.text() == "VIX · FRED 일별"
    assert card.body.text() == "15.20"
    assert "Yahoo15m 14.91·08-20" in card.meta.text()
    assert "05:00KST·직전장08-19·지연" in card.meta.text()
    assert "Primary: FRED / VIXCLS · COMPLETED_DAILY_PRIMARY" in card.toolTip()
    assert "Intraday: Yahoo / ^VIX · INDICATIVE_DELAYED_PROVIDER_SUBSET_15M" in card.toolTip()
    assert "source_timestamp=2026-08-19T20:00:00+00:00" in card.toolTip()
    assert "병합하거나 대체하지 않습니다" in card.toolTip()
    assert card.sparkline.isVisible()
    assert card.sparkline.width() > 0
    assert np.allclose(card.sparkline._values, spark.frame["value"])
    assert "15m 직전 완료장 2026-08-19" in card.sparkline.accessibleName()
    assert "24시간 아님" in card.sparkline.toolTip()
    assert "completed native bars only" in card.sparkline.toolTip()
    required = card.meta.fontMetrics().boundingRect(
        QtCore.QRect(0, 0, card.meta.width(), 1000),
        QtCore.Qt.TextWordWrap, card.meta.text(),
    ).height()
    assert required <= card.meta.height()
    assert card.meta.geometry().bottom() <= card.contentsRect().bottom()

    stale_quote = replace(
        quote, value=None, freshness="STALE",
        display_state=DashboardDisplayState.REFRESH_REQUIRED,
        unavailable_reason="checkpoint mismatch",
    )
    stale_view = replace(view, intraday_quote=stale_quote)
    stale_spark = replace(
        spark, frame=pd.DataFrame(columns=["date", "value"]), freshness="STALE",
        display_state=DashboardDisplayState.REFRESH_REQUIRED,
        unavailable_reason="checkpoint mismatch",
    )
    page.render({
        "dashboard_metrics": {"VIX": official, "VIX_INTRADAY_15M": stale_quote},
        "dashboard_series": {}, "vix_source_views": {"VIX": stale_view},
        "market_card_sparklines": {"VIX": stale_spark},
    })
    assert card.body.text() == "15.20"
    assert "14.91" not in card.meta.text()
    assert "Yahoo15m ^VIX·갱신 필요" in card.meta.text()
    assert not card.sparkline.isVisible()

    page.render({
        "dashboard_metrics": {"VIX": official},
        "dashboard_series": {}, "vix_source_views": {},
    })
    assert card.body.text() == "15.20"
    assert "14.91" not in card.meta.text()
    assert "Yahoo15m ^VIX·현재 표시 불가" in card.meta.text()
    assert not card.sparkline.isVisible()
    page.close()
    app.processEvents()


def test_top_market_cards_use_typed_daily_sparklines_and_preserve_gaps():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    metric = _metric(
        "SOXX", 531.39, freshness="CURRENT", state=DashboardDisplayState.VALUE,
    )
    daily = DashboardSeriesView(
        metric, pd.DataFrame({"date": pd.bdate_range("2026-08-01", periods=10),
                              "value": np.linspace(500.0, 531.39, 10)}),
    )
    page.render({"dashboard_metrics": {"SOXX": metric}, "dashboard_series": {"SOXX": daily}})
    assert page.market_cards["SOXX"].body.text() == "531.39"
    assert not page.market_cards["SOXX"].sparkline.isHidden()
    assert np.allclose(
        page.market_cards["SOXX"].sparkline._values,
        daily.frame["value"],
    )
    assert "완료 일봉" in page.market_cards["SOXX"].sparkline.accessibleName()
    assert "장중 시계열 아님" in page.market_cards["SOXX"].sparkline.toolTip()

    spark = page.market_cards["SOXX"].sparkline
    spark.set_values([1.0, np.nan, 3.0])
    assert not spark.isHidden()
    assert np.array_equal(spark._values[[0, 2]], [1.0, 3.0])
    assert np.isnan(spark._values[1])
    page.close()
    app.processEvents()


def test_korean_top_cards_render_current_headlines_with_completed_daily_sparklines():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.resize(1600, 900)
    page.show()
    metrics = {
        key: replace(
            _metric(
                key, value, freshness="CURRENT_RETRIEVAL_TIME",
                state=DashboardDisplayState.VALUE,
            ),
            dataset_id="TOSS_MARKET_PRICE_SNAPSHOT",
            as_of="08-26 10:00 KST",
            route=f"toss-market-price:{key}:snapshot:PROVISIONAL",
            source_timestamp="2026-08-26T01:00:00+00:00",
            timestamp_basis="RETRIEVAL_TIMESTAMP",
        )
        for key, value in (("KOSPI", 2810.25), ("KOSDAQ", 901.75))
    }
    series = {
        key: DashboardSeriesView(
            metric,
            pd.DataFrame({
                "date": pd.bdate_range(end="2026-08-25", periods=10),
                "value": np.linspace(value - 30.0, value - 10.0, 10),
            }),
        )
        for (key, value), metric in zip(
            (("KOSPI", 2810.25), ("KOSDAQ", 901.75)), metrics.values(),
        )
    }

    page.render({"dashboard_metrics": metrics, "dashboard_series": series})
    app.processEvents()

    for key, expected_body in (("KOSPI", "2,810.25"), ("KOSDAQ", "901.75")):
        card = page.market_cards[key]
        assert card.body.text() == expected_body
        assert card.body.isVisible()
        assert card.meta.text() == "확인·08-26"
        assert card.meta.isVisible()
        assert card.sparkline.isVisible()
        assert np.allclose(card.sparkline._values, series[key].frame["value"])
        assert metrics[key].value not in card.sparkline._values
        assert "완료 일봉" in card.sparkline.accessibleName()
        assert "장중 시계열 아님" in card.sparkline.toolTip()

    page.close()
    app.processEvents()


def test_dashboard_visual_acceptance_layout_scrolls_instead_of_clipping():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render({"dashboard_metrics": {}, "dashboard_series": {}})
    page.resize(1600, 900)
    page.show()
    app.processEvents()
    assert all(
        card.meta.isVisible()
        and card.meta.text() == "확인·N/A"
        and card.meta.accessibleName() == "확인 · 기준 N/A"
        and card.meta.font().pixelSize() >= 10
        and card.meta.fontMetrics().horizontalAdvance(card.meta.text())
        <= card.meta.contentsRect().width()
        for card in page.market_cards.values()
    )

    assert page.verticalScrollBar().maximum() > 0
    assert page.horizontalScrollBar().maximum() == 0
    assert page.subtitle.text() == "시장 · 수급 · 밸류에이션 · 파생 · 환율/금리"
    assert page.freshness.text() == "로컬 데이터"
    assert "freshness 검증 실패" in page.freshness.toolTip()
    assert page.kospi_chart_title.text() == "KOSPI 차트 · 불러오는 중…"
    assert page.market_cards["GOLD"].title.text() == "GOLD"
    assert page.market_cards["WTI"].title.text() == "WTI"
    assert "GC=F" in page.market_cards["GOLD"].toolTip()
    assert "CL=F" in page.market_cards["WTI"].toolTip()
    assert page.nq_detail.text() == "연속선물 데이터가 없습니다."
    assert "백테스트 입력을 대체하지" in page.nq_detail.toolTip()
    assert "LOCAL ONLY" not in page.freshness.text()
    assert "network 0" not in page.freshness.text()
    assert page.derivatives_panel.isVisible()
    assert page.derivatives_panel.geometry().bottom() <= page.widget().height()
    assert page.oscillator_panel.geometry().bottom() < page.rates_panel.geometry().top()
    assert page.kospi_panel.geometry().bottom() < page.market_context_tabs.geometry().top()
    assert page.nq_panel.isHidden()
    assert not page.nq_chart.isVisible()
    assert page.nq_chart.accessibleName() == "나스닥100 연속선물 캔들 차트"
    assert [page.nq_interval.itemText(index) for index in range(page.nq_interval.count())] == [
        "일봉", "주봉", "월봉",
    ]
    assert page.account_placeholder.isVisible()
    assert page.account_placeholder.height() <= 60
    assert page.oscillator_panel.height() >= 250
    assert page.oscillator_note.isHidden()
    assert all(gauge.height() >= (54 if key in {"RSI14", "DISPARITY60"} else 32)
               for key, gauge in page.gauges.items())
    assert page.account_placeholder.height() < page.rates_panel.height()
    assert page.account_placeholder.state.text() == "계좌 데이터 없음 · 계좌 보기에서 로컬 상태 확인"
    assert not page.account_placeholder.state.isHidden()
    assert page.account_placeholder.badge.text() == "데이터 없음"
    assert not hasattr(page, "market_regime")
    assert not hasattr(page, "regime_gauges")
    assert set(page.derivative_cards) == {
        "KOSPI200_BASIS", "VOLUME_PCR", "OI_PCR", "VKOSPI", "WALL",
        "SHORT_SELLING_VALUE",
        "VIX_FUTURES", "US_OPTION_PCR",
    }
    assert page.derivative_cards["VOLUME_PCR"].title.text() == "KOSPI200 옵션 거래량 P/C"
    assert page.derivative_cards["OI_PCR"].title.text() == "KOSPI200 옵션 OI P/C"
    assert "put_volume / call_volume" in page.derivative_cards["VOLUME_PCR"].toolTip()
    assert "put_open_interest / call_open_interest" in page.derivative_cards["OI_PCR"].toolTip()
    us_pcr = page.derivative_cards["US_OPTION_PCR"]
    assert us_pcr.body.text() == "Cboe 6종 · 라이선스 필요"
    assert us_pcr.meta.text() == "Nasdaq·QQQ·NDX·SOXX · 소스 없음"
    assert us_pcr.badge.text() == "숫자 차단"
    assert "10개 범위" in us_pcr.accessibleName()
    tooltip = us_pcr.toolTip()
    expected_states = {
        "CBOE_TOTAL": "LICENSE_BLOCKED",
        "CBOE_INDEX": "LICENSE_BLOCKED",
        "CBOE_ETP": "LICENSE_BLOCKED",
        "CBOE_EQUITY": "LICENSE_BLOCKED",
        "CBOE_VIX": "LICENSE_BLOCKED",
        "CBOE_SPX_SPXW": "LICENSE_BLOCKED",
        "NASDAQ": "SOURCE_UNAVAILABLE",
        "QQQ": "SOURCE_UNAVAILABLE",
        "NDX": "SOURCE_UNAVAILABLE",
        "SOXX": "SOURCE_UNAVAILABLE",
    }
    for scope_id, state in expected_states.items():
        assert f"[{scope_id}]" in tooltip
        assert state in tooltip
    assert "value=N/A" in tooltip
    assert "SUM OF ALL PRODUCTS; not the entire U.S. options market" in tooltip
    assert "ETP aggregate is not a QQQ-specific ratio" in tooltip
    assert "SPX+SPXW are not an NDX-specific ratio" in tooltip
    assert "어떤 집계·ETP·지수·OI·가격·한국·Yahoo·ORATS 값도 대체하지 않습니다" in tooltip
    rsi_scale = page.gauges["RSI14"].threshold_scale
    assert rsi_scale.isVisible()
    assert rsi_scale.thresholds == (30.0, 70.0)
    assert rsi_scale.accessibleName() == "기준선 30, 70"
    assert "30 과매도" in rsi_scale.toolTip()
    assert "70 과매수" in rsi_scale.toolTip()
    disparity_scale = page.gauges["DISPARITY60"].threshold_scale
    assert disparity_scale.thresholds == (0.0,)
    assert disparity_scale.minimum == -20.0
    assert disparity_scale.maximum == 20.0
    assert page.account_placeholder.details.isHidden()
    assert page.account_placeholder.asset_chart.isHidden()

    page.close()
    app.processEvents()


def test_dashboard_account_panel_requires_explicit_reveal_for_valid_local_snapshot(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "account.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "state": "LOCAL_MOCK",
        "as_of": "2026-08-19T21:00:00+09:00",
        "last_reconciled_at": "2026-08-19T21:05:00+09:00",
        "currency": "KRW",
        "total_assets": 10_000_000,
        "securities_value": 8_000_000,
        "cash_balance": 2_000_000,
        "available_cash": 1_500_000,
        "realized_pnl": 75_000,
        "unrealized_pnl": -125_000,
        "positions": [{
            "symbol": "TEST1", "name": "Fixture Asset", "quantity": 2,
            "market_value": 1_000_000, "realized_pnl": 10_000,
            "unrealized_pnl": -25_000,
        }],
        "asset_history": [
            {"date": "2026-08-15", "total_assets": 9_500_000},
            {"date": "2026-08-19", "total_assets": 10_000_000},
        ],
    }), encoding="utf-8")
    page = DashboardPage()
    page.render({
        "dashboard_metrics": {}, "dashboard_series": {},
        "account_snapshot": LocalAccountSnapshotService(path).load(),
    })
    app.processEvents()

    assert page.account_placeholder.height() == 66
    assert page.account_placeholder.details.isHidden()
    assert page.account_placeholder.badge.text() == "로컬 snapshot · 준비됨"
    assert "10,000,000" not in "\n".join(_widget_state_strings(page.account_placeholder))
    page.account_placeholder.reveal_button.click()
    assert page.account_placeholder.height() == 122
    assert not page.account_placeholder.details.isHidden()
    assert page.account_placeholder.badge.text() == "로컬 snapshot"
    assert page.account_placeholder.value_labels["total_assets"].text() == "10,000,000 KRW"
    assert "500,000 KRW" in page.account_placeholder.history.text()
    assert "TEST1" in page.account_placeholder.positions.text()
    assert "21:05:00" in page.account_placeholder.reconciled.text()

    page.render({"dashboard_metrics": {}, "dashboard_series": {}})
    assert page.account_placeholder.height() == 60
    assert page.account_placeholder.badge.text() == "데이터 없음"


def test_dashboard_account_panel_labels_toss_read_only_without_invented_cash(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "latest.json"
    _write_empty_toss_account_snapshot(path)
    page = DashboardPage()

    page.render({
        "dashboard_metrics": {}, "dashboard_series": {},
        "account_snapshot": LocalAccountSnapshotService(path).load(),
    })
    app.processEvents()

    assert page.account_placeholder.badge.text() == "Toss · 준비됨"
    assert page.account_placeholder.details.isHidden()
    page.account_placeholder.reveal_button.click()
    assert page.account_placeholder.badge.text() == "Toss · 읽기 전용"
    assert page.account_placeholder.value_labels["total_assets"].text() == "N/A"
    assert page.account_placeholder.value_labels["cash_balance"].text() == "N/A"
    assert page.account_placeholder.value_labels["available_cash"].text() == "N/A"
    assert page.account_placeholder.history.text() == "보유평가 KRW 0"
    assert "KRW/USD 미합산" in page.account_placeholder.toolTip()
    page.close()


def _synthetic_account_portfolio(*, freshness: str = "LOCAL_VALIDATED") -> AccountPortfolioView:
    market_values = (400.0, 250.0, 150.0, 100.0, 80.0, 20.0)
    positions = tuple(
        AccountPositionView(
            symbol=f"SYN{index}", name=f"Synthetic Holding {index}",
            quantity=float(index), market_value=value, realized_pnl=None,
            unrealized_pnl=value * 0.1, purchase_amount=value * 0.9,
            average_purchase_price=value / index * 0.9,
            current_price=value / index, currency="KRW",
            return_pct=value * 0.1 / (value * 0.9) * 100.0,
            unrealized_pnl_after_cost=value * 0.09,
            daily_pnl=value * 0.01,
            return_pct_after_cost=10.0,
            daily_return_pct=1.0,
            commission=value * 0.005,
            tax=value * 0.005,
        )
        for index, value in enumerate(market_values, start=1)
    )
    snapshot = AccountSnapshotView(
        state=AccountSnapshotState.LOCAL_MOCK,
        provider="SYNTHETIC_LOCAL", source_mode="IDENTIFIER_FREE_FIXTURE",
        registered_holder_scope="SELF", economic_attribution_scope="SELF",
        include_in_user_fund_total=True, as_of="2026-08-20T09:00:00+09:00",
        last_reconciled_at="2026-08-20T09:00:00+09:00", currency="KRW",
        total_assets=1_200.0, securities_value=1_000.0, cash_balance=200.0,
        available_cash=150.0, unrealized_pnl=100.0,
        positions=positions,
        currency_summaries=(AccountCurrencySummaryView(
            "KRW", 900.0, 1_000.0, 990.0, 100.0, 90.0, 10.0, 150.0,
        ),),
        asset_history=(
            AccountAssetPoint("2026-08-19", 1_100.0),
            AccountAssetPoint("2026-08-20", 1_200.0),
        ),
        freshness=freshness,
    )
    return AccountPortfolioView(
        entries=(AccountPortfolioEntryView("synthetic", "Synthetic account", snapshot),),
        user_fund_totals=(),
    )


def _synthetic_dual_currency_account_portfolio() -> AccountPortfolioView:
    krw = _synthetic_account_portfolio().entries[0]
    usd_positions = (
        AccountPositionView(
            symbol="USD1", name="Synthetic USD One", quantity=2.0,
            market_value=60.0, realized_pnl=None, unrealized_pnl=6.0,
            purchase_amount=54.0, average_purchase_price=27.0,
            current_price=30.0, currency="USD",
        ),
        AccountPositionView(
            symbol="USD2", name="Synthetic USD Two", quantity=1.0,
            market_value=40.0, realized_pnl=None, unrealized_pnl=4.0,
            purchase_amount=36.0, average_purchase_price=36.0,
            current_price=40.0, currency="USD",
        ),
    )
    usd_snapshot = replace(
        krw.snapshot,
        currency="USD",
        total_assets=120.0,
        securities_value=100.0,
        cash_balance=20.0,
        available_cash=15.0,
        unrealized_pnl=10.0,
        positions=usd_positions,
        asset_history=(
            AccountAssetPoint("2026-08-18", 105.0),
            AccountAssetPoint("2026-08-20", 120.0),
        ),
    )
    return AccountPortfolioView(
        entries=(
            krw,
            AccountPortfolioEntryView("synthetic_usd", "Synthetic USD account", usd_snapshot),
        ),
        user_fund_totals=(),
    )


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
        if isinstance(widget, QtWidgets.QTableWidget):
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    if item is not None:
                        values.extend((item.text(), item.toolTip()))
    return tuple(value for value in values if value)


def test_account_overview_unavailable_scrubs_hidden_private_widget_state():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = main_window_module.AccountOverviewPanel()
    panel.set_portfolio(_synthetic_account_portfolio())
    marker = "PRIVATE_ACCOUNT_MARKER_94D9"
    panel.value_labels["total_assets"].setText(marker)
    panel.history.setToolTip(marker)
    panel.positions.setAccessibleName(marker)
    panel.reconciled.setAccessibleDescription(marker)
    panel.asset_chart.setToolTip(marker)
    panel.asset_chart.setAccessibleName(marker)
    panel.setToolTip(marker)

    panel.set_unavailable(marker)

    state = "\n".join(_widget_state_strings(panel))
    assert marker not in state
    assert "1,200 KRW" not in state and "SYN1" not in state
    assert all(label.text() == "N/A" for label in panel.value_labels.values())
    assert panel.history.text() == "자산 변화 N/A"
    assert panel.positions.text() == "보유 종목 N/A"
    assert panel.reconciled.text() == "대사 시각 N/A"
    assert panel.asset_chart._values.size == 0
    panel.close()
    app.processEvents()


def test_account_page_unavailable_scrubs_tables_dynamic_children_and_metadata():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    page.render(_synthetic_account_portfolio())
    marker = "PRIVATE_ACCOUNT_PAGE_MARKER_94D9"
    detached_card = page.cards_layout.itemAt(0).widget()
    assert detached_card is not None
    page.setToolTip(marker)
    page.setAccessibleDescription(marker)
    page.headline_labels["total_assets"].setText(marker)
    page.headline_labels["cash"].setToolTip(marker)
    page.holdings_table.item(0, 0).setText(marker)
    page.holdings_table.item(0, 1).setToolTip(marker)
    detached_card.setAccessibleName(marker)
    detached_card.findChildren(QtWidgets.QLabel)[0].setText(marker)

    page.render(AccountPortfolioView(entries=(), user_fund_totals=()))

    state = "\n".join(_widget_state_strings(page))
    detached_state = "\n".join(_widget_state_strings(detached_card))
    assert marker not in state and marker not in detached_state
    assert "1,200 KRW" not in state and "SYN1" not in state
    assert page.holdings_table.rowCount() == 0
    assert page.allocation_rows.count() == 0
    assert page.history_rows.count() == 0
    assert page.cards_layout.count() == 0
    assert all(label.text() == "N/A" for label in page.headline_labels.values())
    assert all(meta.text() == "현재 표시 불가" for meta in page.headline_meta.values())
    assert page._portfolio.entries == ()
    page.close()
    app.processEvents()


def test_account_page_renders_useful_identifier_free_portfolio_composition():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    page.configure_source_actions((AccountSourceActionView(
        source_id="synthetic",
        last_accepted_at="2026-08-20T09:00:00+09:00",
        freshness="LOCAL_VALIDATED",
        reason=None,
        refresh_capability="로컬 수동 · 자동 갱신 없음",
        last_outcome="SUCCEEDED",
        last_outcome_at="2026-08-20T09:01:00+09:00",
        next_eligibility="자동 갱신 없음 · 사용자가 저장할 때만 변경",
    ),))
    page.render(_synthetic_account_portfolio())
    page.resize(1600, 900)
    page.show()
    app.processEvents()

    assert not page.empty_state.isVisible()
    assert page.headline_labels["total_assets"].text() == "1,200 KRW"
    assert page.headline_labels["securities_value"].text() == "1,000 KRW"
    assert "예수금 200 KRW" in page.headline_labels["cash"].text()
    assert page.headline_labels["unrealized_pnl"].text() == "100 KRW"
    assert page.holdings_table.rowCount() == 6
    assert page.holdings_table.item(0, 0).text() == "Synthetic Holding 1 (SYN1)"
    assert page.holdings_table.item(0, 2).text() == "360 KRW"
    assert page.holdings_table.item(0, 3).text() == "400 KRW"
    assert page.holdings_table.item(0, 6).text() == "+11.11%"
    assert page.holdings_table.item(0, 7).text() == "40 KRW"
    assert page.holdings_table.item(0, 8).text() == "36 KRW"
    assert page.holdings_table.item(0, 9).text() == "4 KRW"
    assert page.holdings_table.item(0, 10).text() == "40.0%"
    assert page.holdings_table.item(0, 12).text() == "2026-08-20 09:00 KST"
    assert "주문가능수량=N/A" in page.holdings_table.item(0, 0).toolTip()
    assert "비용후수익률=+10.00%" in page.holdings_table.item(0, 6).toolTip()
    assert "KRW 비용후 90 KRW · 당일 10 KRW" in (
        page.headline_meta["unrealized_pnl"].text()
    )
    allocation_text = "\n".join(
        label.text() for label in page.findChildren(QtWidgets.QLabel)
    )
    assert "기타 · KRW" in allocation_text
    history_sparks = [
        child for child in page.findChildren(main_window_module.MiniSparkline)
        if "실제 계좌 가치" in child.accessibleName()
    ]
    assert len(history_sparks) == 1
    assert np.array_equal(history_sparks[0]._values, [1_100.0, 1_200.0])
    assert "순자산 집계와 별도" in page.summary.text()
    source_status = "\n".join(
        label.text() for label in page.findChildren(QtWidgets.QLabel)
    )
    assert "마지막 정상 2026-08-20 09:00 KST · LOCAL_VALIDATED" in source_status
    assert "최근 결과 SUCCEEDED" in source_status
    page.close()
    app.processEvents()


def test_account_page_renders_timestamped_account_scale_history_as_not_return():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    base = _synthetic_account_portfolio()
    entry = replace(
        base.entries[0], snapshot=replace(
            base.entries[0].snapshot,
            asset_history=(),
            as_of="2026-08-27T07:00:00+09:00",
        ),
    )
    portfolio = replace(
        base,
        entries=(entry,),
        value_histories=(AccountValueHistorySeries(
            source_id="synthetic",
            currency="KRW",
            metric="OBSERVABLE_COMPONENT_SUM",
            points=(
                AccountValueHistoryPoint(
                    "2026-08-26T07:00:00+09:00", 1100.0, 1000.0, 100.0,
                ),
                AccountValueHistoryPoint(
                    "2026-08-27T07:00:00+09:00", 1250.0, 1000.0, 250.0,
                ),
            ),
        ),),
    )
    page = AccountPage()
    page.render(portfolio)
    page.resize(1600, 900)
    page.show()
    app.processEvents()

    labels = "\n".join(label.text() for label in page.findChildren(QtWidgets.QLabel))
    assert "관찰 구성합(평가+현금매수가능)" in labels
    assert "입출금/매매 미분리" in page.history_note.text()
    names = [
        series.name() for series in page.account_charts.history_chart_view.chart().series()
    ]
    assert any("관찰 구성합(평가+현금매수가능)" in name for name in names)
    page.close()
    app.processEvents()


def test_account_page_charts_keep_allocation_and_history_currency_scoped():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    page.render(_synthetic_dual_currency_account_portfolio())
    page.resize(1600, 900)
    page.show()
    app.processEvents()

    charts = page.account_charts
    assert [
        charts.currency_selector.itemText(index)
        for index in range(charts.currency_selector.count())
    ] == ["KRW", "USD"]
    allocation = charts.allocation_chart_view.chart().series()
    assert len(allocation) == 1
    assert isinstance(allocation[0], QtCharts.QPieSeries)
    assert sum(slice_.value() for slice_ in allocation[0].slices()) == pytest.approx(1_000.0)
    assert sum(
        float(slice_.label().rsplit(" ", 1)[-1].removesuffix("%"))
        for slice_ in allocation[0].slices()
    ) == pytest.approx(100.0, abs=0.1)
    assert any(slice_.label().startswith("기타 ") for slice_ in allocation[0].slices())
    history = charts.history_chart_view.chart().series()
    assert len(history) == 1
    assert isinstance(history[0], QtCharts.QLineSeries)
    assert [point.y() for point in history[0].points()] == [0.0, 100.0]

    charts.currency_selector.setCurrentText("USD")
    app.processEvents()
    allocation = charts.allocation_chart_view.chart().series()
    assert sum(slice_.value() for slice_ in allocation[0].slices()) == pytest.approx(100.0)
    assert all("SYN" not in slice_.label() for slice_ in allocation[0].slices())
    assert {slice_.label().split()[0] for slice_ in allocation[0].slices()} == {
        "Synthetic",
    }
    history = charts.history_chart_view.chart().series()
    assert len(history) == 1
    expected_dates = [
        int(datetime(2026, 8, 18, tzinfo=timezone.utc).timestamp() * 1000),
        int(datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp() * 1000),
    ]
    assert [round(point.x()) for point in history[0].points()] == expected_dates
    assert [point.y() for point in history[0].points()] == [0.0, 15.0]
    assert page.holdings_table.rowCount() == 8
    progress_rows = page.findChildren(QtWidgets.QProgressBar)
    assert len(progress_rows) == 8
    assert any(bar.accessibleName() == "기타 비중 2.0%" for bar in progress_rows)
    assert len([
        child for child in page.findChildren(main_window_module.MiniSparkline)
        if "실제 계좌 가치" in child.accessibleName()
    ]) == 2
    assert page.horizontalScrollBar().maximum() == 0
    page.close()
    app.processEvents()


def test_account_page_chart_privacy_clears_every_series_and_restores():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    page.render(_synthetic_dual_currency_account_portfolio())
    charts = page.account_charts
    assert charts.allocation_chart_view.chart().series()
    assert charts.history_chart_view.chart().series()
    assert "1,200" in "\n".join(_widget_state_strings(charts))

    page.hide_balances.setChecked(True)
    app.processEvents()

    assert charts.currency_selector.count() == 0
    assert charts.currency_selector.isHidden()
    for view in (charts.allocation_chart_view, charts.history_chart_view):
        assert view.chart().series() == []
        assert view.chart().axes() == []
        assert view.chart().title() == ""
        assert view.toolTip() == ""
        assert view.accessibleName() == ""
        assert view.accessibleDescription() == ""
    private_state = "\n".join(_widget_state_strings(charts))
    assert "1,200" not in private_state
    assert "SYN1" not in private_state
    assert "USD1" not in private_state

    page.hide_balances.setChecked(False)
    app.processEvents()
    assert charts.currency_selector.count() == 2
    assert charts.allocation_chart_view.chart().series()
    assert charts.history_chart_view.chart().series()
    page.close()
    app.processEvents()


def test_account_page_chart_rerender_and_incomplete_states_leave_no_residue():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    page.render(_synthetic_dual_currency_account_portfolio())
    charts = page.account_charts
    charts.currency_selector.setCurrentText("USD")
    app.processEvents()
    assert "USD1" in charts.allocation_chart_view.toolTip()

    current = _synthetic_account_portfolio().entries[0]
    stale = AccountPortfolioEntryView(
        "stale", "Stale synthetic", replace(current.snapshot, freshness="STALE"),
    )
    page.render(AccountPortfolioView(entries=(current, stale), user_fund_totals=()))
    app.processEvents()

    assert charts.currency_selector.count() == 0
    assert charts.allocation_chart_view.chart().series() == []
    assert charts.allocation_empty.text() == "표시 가능한 동일통화 차트 데이터가 없습니다."
    assert charts.history_chart_view.chart().series() == []
    assert "USD1" not in "\n".join(_widget_state_strings(charts))

    page.render(AccountPortfolioView(entries=(), user_fund_totals=()))
    app.processEvents()
    assert charts.currency_selector.count() == 0
    assert charts.allocation_chart_view.chart().series() == []
    assert charts.history_chart_view.chart().series() == []
    assert charts.allocation_empty.text() == "계좌 데이터 없음"
    page.close()
    app.processEvents()


def test_account_page_charts_follow_palette_without_fixed_geometry():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    page.render(_synthetic_account_portfolio())
    palette = QtGui.QPalette(page.palette())
    palette.setColor(QtGui.QPalette.Text, QtGui.QColor("#e6edf3"))
    page.account_charts.setPalette(palette)
    app.processEvents()

    for view in (
        page.account_charts.allocation_chart_view,
        page.account_charts.history_chart_view,
    ):
        assert view.minimumWidth() == 0
        assert view.chart().titleBrush().color() == QtGui.QColor("#e6edf3")
    page.resize(1100, 800)
    page.show()
    app.processEvents()
    assert page.horizontalScrollBar().maximum() == 0
    page.close()
    app.processEvents()


def test_account_page_mask_and_stale_partial_state_are_numeric_free():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    current = _synthetic_account_portfolio().entries[0]
    stale_snapshot = replace(current.snapshot, freshness="STALE")
    portfolio = AccountPortfolioView(
        entries=(
            current,
            AccountPortfolioEntryView("stale", "Stale synthetic", stale_snapshot),
        ),
        user_fund_totals=(),
    )
    page.render(portfolio)

    assert page.headline_labels["total_assets"].text() == "N/A"
    assert page.headline_labels["securities_value"].text() == "N/A"
    assert "KRW 합산 불가" in page.summary.text()
    assert "완전한 동일통화" in "\n".join(
        label.text() for label in page.findChildren(QtWidgets.QLabel)
    )

    page.render(_synthetic_account_portfolio())
    page.hide_balances.setChecked(True)
    app.processEvents()
    all_text = "\n".join(
        label.text() for label in page.findChildren(QtWidgets.QLabel)
    ) + "\n" + "\n".join(
        page.holdings_table.item(row, column).text()
        for row in range(page.holdings_table.rowCount())
        for column in range(page.holdings_table.columnCount())
        if page.holdings_table.item(row, column) is not None
    )
    assert "1,200" not in all_text and "SYN1" not in all_text
    assert MASKED_VALUE in all_text
    assert "보유 정보 숨김" in all_text
    assert page.holdings_table.columnSpan(0, 0) == 13

    page.hide_balances.setChecked(False)
    app.processEvents()

    assert page.holdings_table.columnSpan(0, 0) == 1
    assert page.holdings_table.rowCount() == 6
    assert all(
        page.holdings_table.item(0, column) is not None
        for column in range(page.holdings_table.columnCount())
    )
    page.close()
    app.processEvents()


def test_account_page_and_dashboard_show_intentional_empty_and_open_path():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    page.render(AccountPortfolioView(entries=(), user_fund_totals=()))
    assert not page.empty_state.isHidden()
    assert "계좌 데이터 없음" in page.summary.text()
    assert "0 KRW" not in page.summary.text()
    assert page.holdings_table.isHidden()

    dashboard = DashboardPage()
    dashboard.account_placeholder.set_portfolio(
        AccountPortfolioView(entries=(), user_fund_totals=())
    )
    opened = []
    dashboard.account_placeholder.open_requested.connect(lambda: opened.append(True))
    dashboard.account_placeholder.open_button.click()
    assert opened == [True]
    assert dashboard.account_placeholder.badge.text() == "데이터 없음"
    assert "계좌 데이터 없음" in dashboard.account_placeholder.state.text()
    page.close()
    dashboard.close()
    app.processEvents()


def test_dashboard_account_summary_uses_valid_portfolio_and_masks_all_private_values():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    dashboard = DashboardPage()
    portfolio = _synthetic_account_portfolio()
    dashboard.account_placeholder.set_portfolio(portfolio)

    panel = dashboard.account_placeholder
    collapsed = "\n".join(_widget_state_strings(panel))
    assert panel.badge.text().endswith("준비됨")
    assert panel.reveal_button.text() == "계좌 요약 보기"
    assert panel.details.isHidden()
    assert "1,200" not in collapsed and "SYN1" not in collapsed
    assert "2026-08-20 09:00" not in collapsed
    assert panel.asset_chart._values.size == 0

    QtTest.QTest.mouseClick(panel.reveal_button, QtCore.Qt.LeftButton)
    assert panel.reveal_button.text() == "계좌 요약 숨기기"
    assert panel.value_labels["total_assets"].text() == "1,200 KRW"
    assert panel.value_labels["securities_value"].text() == "1,000 KRW"
    assert panel.value_labels["unrealized_pnl"].text() == "100 KRW"
    assert "2026-08-20 09:00 KST" in panel.reconciled.text()
    assert panel.asset_chart._values.size == 2

    # A refresh preserves the user's in-process reveal, but a hide scrubs all
    # presentation metadata immediately and remains collapsed across refreshes.
    panel.set_portfolio(portfolio)
    assert panel.value_labels["total_assets"].text() == "1,200 KRW"
    marker = "PRIVATE_DASHBOARD_ACCOUNT_MARKER"
    panel.history.setToolTip(marker)
    panel.positions.setAccessibleDescription(marker)
    panel.asset_chart.setAccessibleName(marker)
    QtTest.QTest.keyClick(panel.reveal_button, QtCore.Qt.Key_Space)
    hidden = "\n".join(_widget_state_strings(panel))
    assert marker not in hidden and "1,200" not in hidden and "SYN1" not in hidden
    assert panel.details.isHidden() and panel.asset_chart._values.size == 0
    panel.set_portfolio(portfolio)
    assert panel.details.isHidden()

    panel._set_revealed(True)
    panel.set_unavailable("fixture invalidated")
    panel.set_portfolio(portfolio)
    assert panel.details.isHidden()
    assert panel.reveal_button.text() == "계좌 요약 보기"

    second = main_window_module.AccountOverviewPanel()
    second.set_portfolio(_synthetic_dual_currency_account_portfolio())
    second_state = "\n".join(_widget_state_strings(second))
    assert second.details.isHidden()
    assert "USD1" not in second_state and "1,200" not in second_state
    second.close()
    dashboard.close()
    app.processEvents()


def test_account_page_keeps_provider_holder_attribution_and_subtotals_separate(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    kb_path = tmp_path / "kb.json"
    family_path = tmp_path / "family.json"
    _write_kb_account_snapshot(kb_path)
    _write_family_account_snapshot(family_path)
    portfolio = LocalAccountPortfolioService((
        LocalAccountSourceSpec("kb", "KB Securities · 본인", kb_path),
        LocalAccountSourceSpec(
            "family", "미래에셋 가족 명의 ETF · 로컬 수동", family_path
        ),
    )).load()
    page = AccountPage()

    page.render(portfolio)
    app.processEvents()
    text = "\n".join(label.text() for label in page.findChildren(QtWidgets.QLabel))

    assert "사용자 선택 자금 합계 · KRW 1,566,450 (2개 범위)" in page.summary.text()
    assert "KB Securities · 본인" in text
    assert "KB_SECURITIES · SANITIZED_READ_ONLY · 2026-08-20 10:02 KST · LOCAL_VALIDATED" in text
    assert "미래에셋 가족 명의 ETF · 로컬 수동" in text
    assert "가족 명의 계좌 · 사용자 신고 자금 · 법적 소유 주장 아님" in text
    assert "사용자 선택 자금 합계 포함" in text
    assert "주문·이체 기능 없음" in text
    page.close()


def test_account_page_shows_source_currency_buying_power_while_aggregate_stays_closed():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    toss = AccountSnapshotView(
        state=AccountSnapshotState.TOSS_READ_ONLY,
        provider="TOSS_SECURITIES",
        source_mode="SANITIZED_READ_ONLY",
        registered_holder_scope="SELF",
        economic_attribution_scope="SELF",
        include_in_user_fund_total=True,
        as_of="2026-08-20T01:02:03+00:00",
        currency_summaries=(
            AccountCurrencySummaryView(
                "KRW", 2_000.0, 2_200.0, 2_180.0, 200.0, 180.0, 50.0,
                345_000.0,
            ),
            AccountCurrencySummaryView(
                "USD", 10.0, 11.0, 10.8, 1.0, 0.8, 0.2, 12.34,
            ),
        ),
    )
    unavailable = AccountSnapshotView(
        state=AccountSnapshotState.NOT_AVAILABLE,
        reason="RUNTIME_CONFIG_REQUIRED",
    )
    portfolio = AccountPortfolioView(
        entries=(
            AccountPortfolioEntryView("toss_self", "Toss Securities · 본인", toss),
            AccountPortfolioEntryView("kb_self", "KB Securities · 본인", unavailable),
            AccountPortfolioEntryView(
                "family", "미래에셋 가족 명의 ETF · 로컬 수동", unavailable,
            ),
        ),
        user_fund_totals=(),
    )
    page = AccountPage()

    page.render(portfolio)
    app.processEvents()

    assert [
        page.source_selector.itemText(index)
        for index in range(page.source_selector.count())
    ] == [
        "전체 계좌 (통합)", "Toss Securities · 본인",
        "KB Securities · 본인", "미래에셋 가족 명의 ETF · 로컬 수동",
    ]
    assert "KRW 합산 불가" in page.summary.text()
    assert "USD 합산 불가" in page.summary.text()
    assert page.headline_labels["cash"].text() == "N/A"
    assert page.holdings_table.rowCount() == 0
    source_values = [
        label for label in page.findChildren(QtWidgets.QLabel)
        if label.accessibleName() == "toss_self 통화별 현금 매수가능"
    ]
    assert source_values == []

    page.source_selector.setCurrentIndex(
        page.source_selector.findData("toss_self")
    )
    app.processEvents()
    assert page.source_selector.currentText() == "Toss Securities · 본인"
    assert "Toss Securities · 본인" in page.summary.text()
    assert "주문가능 345,000 KRW" in page.headline_labels["cash"].text()
    assert "주문가능 12.34 USD" in page.headline_labels["cash"].text()
    source_values = [
        label for label in page.findChildren(QtWidgets.QLabel)
        if label.accessibleName() == "toss_self 통화별 현금 매수가능"
    ]
    assert len(source_values) == 1
    assert source_values[0].text() == "현금 매수가능 345,000 KRW / 12.34 USD"

    page.render(portfolio)
    app.processEvents()
    assert page.source_selector.currentData() == "toss_self"

    page.source_selector.setCurrentIndex(
        page.source_selector.findData("kb_self")
    )
    app.processEvents()
    assert not page.empty_state.isHidden()
    assert "RUNTIME_CONFIG_REQUIRED" in page.summary.text()
    assert "345,000" not in "\n".join(_widget_state_strings(page))

    page.source_selector.setCurrentIndex(
        page.source_selector.findData("toss_self")
    )
    app.processEvents()

    page.hide_balances.setChecked(True)
    app.processEvents()
    hidden_values = [
        label.text() for label in page.findChildren(QtWidgets.QLabel)
        if label.accessibleName() == "toss_self 통화별 현금 매수가능"
    ]
    assert hidden_values and "345,000" not in hidden_values[0]
    assert "12.34" not in hidden_values[0] and MASKED_VALUE in hidden_values[0]
    page.close()
    app.processEvents()


def test_account_page_explicit_source_drilldown_preserves_global_fail_closed_and_privacy():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    current = _synthetic_account_portfolio().entries[0]
    unavailable = AccountPortfolioEntryView(
        "kb_unavailable", "KB Securities · 본인",
        AccountSnapshotView(
            state=AccountSnapshotState.NOT_AVAILABLE,
            reason="RUNTIME_CONFIG_REQUIRED", freshness="UNKNOWN",
        ),
    )
    portfolio = AccountPortfolioView(
        entries=(current, unavailable), user_fund_totals=(),
    )
    page = AccountPage()
    page.resize(1600, 900)
    page.show()

    page.render(portfolio)
    app.processEvents()
    combined = "\n".join(_widget_state_strings(page))
    assert page.source_selector.currentData() is None
    assert page.headline_labels["total_assets"].text() == "N/A"
    assert page.holdings_table.rowCount() == 0
    assert "1,200 KRW" not in combined and "SYN1" not in combined

    page.source_selector.setCurrentIndex(
        page.source_selector.findData("synthetic")
    )
    app.processEvents()
    assert page.headline_labels["total_assets"].text() == "1,200 KRW"
    assert page.holdings_table.rowCount() == 6
    assert page.holdings_table.item(0, 0).text() == "Synthetic Holding 1 (SYN1)"
    assert "SYNTHETIC_LOCAL · IDENTIFIER_FREE_FIXTURE" in page.summary.toolTip() or (
        "SYNTHETIC_LOCAL · IDENTIFIER_FREE_FIXTURE" in "\n".join(_widget_state_strings(page))
    )
    assert any(
        "실제 계좌 가치" in child.accessibleName()
        for child in page.findChildren(main_window_module.MiniSparkline)
    )

    page.hide_balances.setChecked(True)
    app.processEvents()
    masked = "\n".join(_widget_state_strings(page))
    assert "1,200" not in masked and "SYN1" not in masked
    assert MASKED_VALUE in masked and "보유 정보 숨김" in masked
    assert page.horizontalScrollBar().maximum() == 0

    page.render(portfolio)
    app.processEvents()
    assert page.source_selector.currentData() == "synthetic"
    assert page.hide_balances.isChecked()
    refreshed = "\n".join(_widget_state_strings(page))
    assert "1,200" not in refreshed and "SYN1" not in refreshed

    page.hide_balances.setChecked(False)
    surviving = AccountPortfolioEntryView(
        "surviving", "남아 있는 계좌 · 본인", current.snapshot,
    )
    page.render(AccountPortfolioView(entries=(surviving,), user_fund_totals=()))
    app.processEvents()

    assert page.source_selector.currentData() == "synthetic"
    assert page.source_selector.currentText() == f"{current.title} · 현재 없음"
    assert not page.empty_state.isHidden()
    assert "선택한 계좌 source가 현재 구성에 없습니다." in page.summary.text()
    assert page.headline_labels["total_assets"].text() == "N/A"
    assert page.holdings_table.rowCount() == 0
    disappeared = "\n".join(_widget_state_strings(page))
    assert "1,200 KRW" not in disappeared and "SYN1" not in disappeared
    assert page.horizontalScrollBar().maximum() == 0

    page.source_selector.setCurrentIndex(
        page.source_selector.findData("surviving")
    )
    app.processEvents()
    assert page.headline_labels["total_assets"].text() == "1,200 KRW"
    assert page.holdings_table.rowCount() == 6
    page.close()
    app.processEvents()


def test_account_page_hide_control_removes_balances_and_position_symbols(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    kb_path = tmp_path / "kb.json"
    _write_kb_account_snapshot(kb_path)
    portfolio = LocalAccountPortfolioService((
        LocalAccountSourceSpec("kb", "KB Securities · 본인", kb_path),
    )).load()
    page = AccountPage()
    page.render(portfolio)
    before = "\n".join(label.text() for label in page.findChildren(QtWidgets.QLabel))
    assert "1,066,450" in before and "005930" in before

    page.hide_balances.setChecked(True)
    app.processEvents()
    hidden = "\n".join(label.text() for label in page.findChildren(QtWidgets.QLabel))

    assert "1,066,450" not in hidden and "005930" not in hidden
    assert "사용자 선택 자금 합계 · 금액 숨김" in hidden
    assert "보유 정보 숨김" in hidden
    page.close()


def test_account_page_exposes_explicit_snapshot_removal_request():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    requested: list[bool] = []
    page.remove_requested.connect(lambda: requested.append(True))

    page.remove_button.click()
    app.processEvents()

    assert requested == [True]
    assert "스냅샷과 계좌 가치 이력 삭제" in page.remove_button.accessibleName()
    page.close()


def test_account_removal_confirmation_discloses_every_deleted_local_scope(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.current_observation_reload_timer.stop()
    _drain_main_window_workers(app, window, timeout=10.0)
    prompt: dict[str, object] = {}

    def reject(_parent, title, text, buttons, default):
        prompt.update(
            title=title, text=text, buttons=buttons, default=default,
        )
        return QtWidgets.QMessageBox.No

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", reject)
    window._confirm_remove_account_snapshots()

    assert prompt["title"] == "계좌 로컬 기록 전체 삭제"
    assert "계좌 스냅샷과 계좌 가치 이력" in prompt["text"]
    assert "관련 임시 기록" in prompt["text"]
    assert "수동 계좌 저장소" in prompt["text"]
    assert prompt["default"] == QtWidgets.QMessageBox.No
    window.close()
    app.processEvents()


def test_account_page_manual_controls_require_an_explicit_manual_selection():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = AccountPage()
    imported: list[bool] = []
    added: list[bool] = []
    edited: list[str] = []
    removed: list[str] = []
    page.import_manual_requested.connect(lambda: imported.append(True))
    page.add_manual_requested.connect(lambda: added.append(True))
    page.edit_manual_requested.connect(edited.append)
    page.remove_manual_requested.connect(removed.append)
    manual = AccountPortfolioEntryView(
        "manual:mirae_isa", "미래에셋 ISA",
        AccountSnapshotView(
            state=AccountSnapshotState.MANUAL_HOLDINGS_BASIS,
            freshness="DATED_MANUAL_BASIS",
        ),
    )
    page.render(AccountPortfolioView(entries=(manual,), user_fund_totals=()))

    assert page.import_manual_button.text() == "아빠 CSV로 계좌 추가·갱신"
    assert "두 아빠 계좌만" in page.import_manual_button.toolTip()
    assert "다른 수동 계좌는 보존" in page.import_manual_button.toolTip()
    assert "현재가 열은 저장하지 않습니다" in page.import_manual_button.toolTip()
    assert not page.edit_manual_button.isEnabled()
    assert not page.remove_manual_button.isEnabled()
    page.import_manual_button.click()
    page.add_manual_button.click()
    page.source_selector.setCurrentIndex(
        page.source_selector.findData("manual:mirae_isa")
    )
    app.processEvents()
    assert page.edit_manual_button.isEnabled()
    assert page.remove_manual_button.isEnabled()
    page.edit_manual_button.click()
    page.remove_manual_button.click()
    page.resize(1600, 900)
    page.show()
    app.processEvents()

    assert imported == [True]
    assert added == [True]
    assert edited == ["manual:mirae_isa"]
    assert removed == ["manual:mirae_isa"]
    assert page.horizontalScrollBar().maximum() == 0
    page.close()


def test_main_window_manual_account_store_supports_import_upsert_and_remove(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store_path = tmp_path / "local_user" / "manual_accounts.json"
    csv_path = tmp_path / "appa.csv"
    csv_path.write_text(
        "창대 주식 계좌 운용 결과(26.2.3)\n\n"
        "아빠 ISA 60%\nEFT,종목 티커,수량,평균단가,현재단가,구매총액,현재총액\n"
        "Fixture A,111111,2,100,999,200,1998\n\n"
        "아빠 종합 40%\nEFT,종목 티커,수량,평균단가,현재단가,구매총액,현재총액\n"
        "Fixture B,222222,1,300,888,300,888\n",
        encoding="utf-8",
    )
    window = MainWindow(
        tmp_path, toss_runtime_enabled=False,
        manual_account_store_path=store_path,
    )
    expected_manual_ids = {
        "manual:mirae_pension", "manual:appa_isa", "manual:appa_general",
    }
    for _ in range(100):
        app.processEvents()
        if window._account_thread is None:
            break
        QtTest.QTest.qWait(5)

    pension = ManualAccountRecord(
        "manual:mirae_pension", "미래에셋 연금", "PENSION",
        "2026-08-26", "KRW",
        (ManualAccountPosition("Fixture Pension", "333333", 3.0, 400.0, 1200.0),),
    )
    window._upsert_manual_account(pension)
    window._import_manual_account_csv(csv_path)
    for _ in range(200):
        app.processEvents()
        if expected_manual_ids.issubset({
            entry.source_id for entry in window._account_portfolio.entries
        }) and window._account_thread is None:
            break
        QtTest.QTest.qWait(5)

    stored = LocalManualAccountStore(store_path).load()
    assert [account.source_id for account in stored.accounts] == [
        "manual:mirae_pension", "manual:appa_isa", "manual:appa_general",
    ]
    assert {entry.source_id for entry in window._account_portfolio.entries}.issuperset(
        expected_manual_ids
    )
    window._remove_manual_account("manual:mirae_pension")
    assert [account.source_id for account in LocalManualAccountStore(store_path).load().accounts] == [
        "manual:appa_isa", "manual:appa_general",
    ]
    for _ in range(200):
        app.processEvents()
        if window._account_thread is None:
            break
        QtTest.QTest.qWait(5)
    window.close()
    QtTest.QTest.qWait(1000)
    app.processEvents()


def test_main_window_account_worker_populates_local_account_page_without_network(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    toss_path = tmp_path / "toss.json"
    kb_path = tmp_path / "kb.json"
    family_path = tmp_path / "family.json"
    _write_empty_toss_account_snapshot(toss_path)
    _write_kb_account_snapshot(kb_path)
    _write_family_account_snapshot(family_path)
    window = MainWindow(
        tmp_path,
        account_snapshot_path=toss_path,
        kb_account_snapshot_path=kb_path,
        family_account_snapshot_path=family_path,
    )
    window.show()
    for _ in range(100):
        app.processEvents()
        if window._account_thread is None and window._account_portfolio.entries:
            break
        QtTest.QTest.qWait(5)

    assert len(window._account_portfolio.entries) == 3
    assert "KRW 합산 불가" in window.account_page.summary.text()
    assert window.dashboard.account_placeholder.badge.text().endswith("준비됨")
    assert window.dashboard.account_placeholder.details.isHidden()
    assert window.account_page.refresh_button.text() == "로컬 새로 읽기"
    assert "외부 공급자를 호출하지 않고" in window.account_page.refresh_button.toolTip()
    assert window.net_worth_page.refresh_button.text() == "로컬 새로 읽기"
    assert "외부 공급자" in window.net_worth_page.empty_detail.text()
    window.account_page.hide_balances.setChecked(True)
    app.processEvents()
    assert window.dashboard.account_placeholder.history.text() == "자산 변화 숨김"
    assert window.dashboard.account_placeholder.positions.text() == "보유 정보 숨김"
    assert window.dashboard.account_placeholder.details.isHidden()
    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_provider_account_refresh_discloses_and_runs_one_manual_call_off_thread(
    tmp_path,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    snapshot_path = tmp_path / "latest.json"
    _write_empty_toss_account_snapshot(snapshot_path)
    calls: list[tuple[AccountRefreshTrigger, object]] = []

    def refresh(trigger):
        calls.append((trigger, QtCore.QThread.currentThread()))

    window = MainWindow(
        tmp_path,
        account_snapshot_path=snapshot_path,
        account_refresher=refresh,
    )
    _stub_fast_startup_local_reads(window)
    _drain_local_read_workers(app, window)
    window.show()
    for _ in range(100):
        app.processEvents()
        if window._account_thread is None and window._account_portfolio.entries:
            break
        QtTest.QTest.qWait(5)

    assert calls == []
    assert "공급자 갱신" in window.account_page.refresh_button.text()
    assert "외부 계좌 공급자" in window.account_page.refresh_button.accessibleName()
    assert "한 번 시도한 뒤" in window.account_page.refresh_button.toolTip()

    calls.clear()
    window.account_page.refresh_button.click()
    for _ in range(100):
        app.processEvents()
        if window._account_thread is None and window._account_portfolio.entries:
            break
        QtTest.QTest.qWait(5)

    assert [trigger for trigger, _thread in calls] == [AccountRefreshTrigger.MANUAL]
    assert calls[0][1] is not app.thread()
    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_account_worker_propagates_failed_refresh_without_replaying_prior_toss_values(
    tmp_path,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    toss_path = tmp_path / "toss.json"
    kb_path = tmp_path / "kb.json"
    _write_empty_toss_account_snapshot(toss_path)
    _write_kb_account_snapshot(kb_path)
    prior_toss = LocalAccountSnapshotService(toss_path).load()
    prior_kb = LocalAccountSnapshotService(kb_path).load()
    assert prior_toss.displays_values and prior_kb.displays_values
    calls = []

    def failed_refresh(trigger):
        calls.append(trigger)
        return SimpleNamespace(
            status="FAILED_PRESERVED_PRIOR",
            reason="ACCOUNT_REFRESH_FAILED_CLOSED",
            account_calls=0,
        )

    worker = main_window_module.AccountSnapshotWorker(
        LocalAccountSnapshotService(toss_path),
        LocalAccountPortfolioService((
            LocalAccountSourceSpec("toss_self", "Toss Securities · 본인", toss_path),
            LocalAccountSourceSpec("kb_self", "KB Securities · 본인", kb_path),
        )),
        AccountRefreshTrigger.MANUAL,
        failed_refresh,
    )
    completed = []
    worker.completed.connect(completed.append)

    worker.run()
    app.processEvents()

    assert calls == [AccountRefreshTrigger.MANUAL]
    assert len(completed) == 1
    result = completed[0]
    assert result.primary.state is AccountSnapshotState.NOT_AVAILABLE
    assert result.primary.freshness == "READ_FAILURE"
    assert result.primary.reason == "ACCOUNT_REFRESH_FAILED_CLOSED"
    assert not result.primary.displays_values
    assert result.primary.total_assets is None and result.primary.positions == ()
    toss_entry = next(
        entry for entry in result.portfolio.entries
        if entry.source_id == "toss_self"
    )
    kb_entry = next(
        entry for entry in result.portfolio.entries
        if entry.source_id == "kb_self"
    )
    assert toss_entry.snapshot == result.primary
    assert not toss_entry.snapshot.displays_values
    assert kb_entry.snapshot == prior_kb
    assert len(result.portfolio.user_fund_totals) == 1
    total = result.portfolio.user_fund_totals[0]
    assert (
        total.currency,
        total.total_assets,
        total.included_accounts,
        total.complete,
    ) == ("KRW", 1_066_450.0, 1, True)


def test_one_click_runs_toss_and_kb_independently_and_keeps_mixed_result(
    tmp_path,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    toss_path = tmp_path / "toss.json"
    kb_path = tmp_path / "kb.json"
    _write_empty_toss_account_snapshot(toss_path)
    _write_kb_account_snapshot(kb_path)
    calls = []

    def toss_refresh(trigger):
        calls.append(("toss", trigger, QtCore.QThread.currentThread()))
        return SimpleNamespace(status="SUCCEEDED")

    def kb_refresh(trigger):
        calls.append(("kb", trigger, QtCore.QThread.currentThread()))
        return SimpleNamespace(
            status="FAILED_PRESERVED_PRIOR",
            reason="KB_ACCOUNT_SUPPLIER_TIMEOUT",
        )

    window = MainWindow(
        tmp_path,
        account_snapshot_path=toss_path,
        kb_account_snapshot_path=kb_path,
        account_refresher=toss_refresh,
        kb_account_refresher=kb_refresh,
    )
    _stub_fast_startup_local_reads(window)
    _drain_local_read_workers(app, window)
    assert calls == []

    window.account_page.refresh_button.click()
    for _ in range(200):
        app.processEvents()
        if len(calls) == 2 and window._account_thread is None:
            break
        QtTest.QTest.qWait(5)

    assert [(name, trigger) for name, trigger, _thread in calls] == [
        ("toss", AccountRefreshTrigger.MANUAL),
        ("kb", AccountRefreshTrigger.MANUAL),
    ]
    assert all(thread is not app.thread() for _name, _trigger, thread in calls)
    toss_entry = next(
        entry for entry in window._account_portfolio.entries
        if entry.source_id == "toss_self"
    )
    kb_entry = next(
        entry for entry in window._account_portfolio.entries
        if entry.source_id == "kb_self"
    )
    assert toss_entry.snapshot.displays_values
    assert kb_entry.snapshot.state is AccountSnapshotState.NOT_AVAILABLE
    assert kb_entry.snapshot.reason == "KB_ACCOUNT_SUPPLIER_TIMEOUT"
    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


@pytest.mark.parametrize(
    "trigger", (AccountRefreshTrigger.STARTUP, AccountRefreshTrigger.PERIODIC),
)
def test_desktop_account_worker_rejects_automatic_provider_triggers(
    tmp_path, trigger,
):
    snapshot_path = tmp_path / "latest.json"
    _write_empty_toss_account_snapshot(snapshot_path)

    with pytest.raises(ValueError, match="only local load or MANUAL"):
        main_window_module.AccountSnapshotWorker(
            LocalAccountSnapshotService(snapshot_path),
            LocalAccountPortfolioService((
                LocalAccountSourceSpec(
                    "toss_self", "Toss Securities · 본인", snapshot_path,
                ),
            )),
            trigger,
            lambda _trigger: pytest.fail("automatic refresh must not run"),
        )


def test_app_missing_toss_runtime_config_is_api_zero_and_not_available(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    retained = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    _write_empty_toss_account_snapshot(retained)

    window = app_module.build_main_window(tmp_path, {})
    _stub_fast_startup_local_reads(window)
    _drain_local_read_workers(app, window)
    window.show()
    for _ in range(100):
        app.processEvents()
        if window._account_thread is None and window._account_portfolio.entries:
            break
        QtTest.QTest.qWait(5)

    assert window.account_refresher is None
    assert window.kb_account_refresher is None
    assert window.account_page.refresh_button.text() == "로컬 새로 읽기"
    assert "외부 공급자를 호출하지 않고" in window.account_page.refresh_button.toolTip()
    assert window.net_worth_page.refresh_button.text() == "로컬 새로 읽기"
    assert "외부 공급자를 호출하지 않고" in window.net_worth_page.refresh_button.toolTip()
    assert window._account_view.state is AccountSnapshotState.NOT_AVAILABLE
    toss_entry = next(
        entry for entry in window._account_portfolio.entries
        if entry.source_id == "toss_self"
    )
    assert toss_entry.snapshot.state is AccountSnapshotState.NOT_AVAILABLE
    assert toss_entry.snapshot.reason == "RUNTIME_CONFIG_REQUIRED"
    assert window.dashboard.account_placeholder.badge.text() == "데이터 없음"
    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_app_complete_toss_runtime_is_local_on_startup_and_manual_on_click(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    retained = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    _write_empty_toss_account_snapshot(retained)
    calls = []

    def refresh(trigger):
        calls.append((trigger, QtCore.QThread.currentThread()))

    monkeypatch.setattr(
        app_module,
        "build_toss_account_runtime",
        lambda _root, _environment: SimpleNamespace(
            refresher=refresh,
            enabled=True,
            reason=None,
        ),
    )
    window = app_module.build_main_window(tmp_path, {})
    _stub_fast_startup_local_reads(window, monkeypatch)
    _drain_local_read_workers(app, window)
    window.show()
    for _ in range(100):
        app.processEvents()
        if window._account_thread is None and window._account_portfolio.entries:
            break
        QtTest.QTest.qWait(5)

    assert calls == []
    assert "공급자 갱신 시도" in window.account_page.refresh_button.text()
    assert "외부 계좌 공급자" in window.account_page.refresh_button.accessibleName()
    assert "읽기 전용 갱신을 한 번 시도" in window.account_page.refresh_button.toolTip()
    assert window.net_worth_page.refresh_button.text() == "로컬 새로 읽기"
    assert "외부 공급자를 호출하지 않고" in window.net_worth_page.refresh_button.toolTip()
    window.account_page.refresh_button.click()
    for _ in range(100):
        app.processEvents()
        if len(calls) == 1 and window._account_thread is None:
            break
        QtTest.QTest.qWait(5)
    QtTest.QTest.qWait(20)
    app.processEvents()

    assert [trigger for trigger, _thread in calls] == [AccountRefreshTrigger.MANUAL]
    assert calls[0][1] is not app.thread()
    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_app_dotenv_loader_accepts_only_named_toss_runtime_values(tmp_path):
    (tmp_path / ".env").write_text(
        "TOSSINVEST_CLIENT_ID=dotenv-id\n"
        "TOSSINVEST_CLIENT_SECRET=dotenv-secret\n"
        "TOSSINVEST_ACCOUNT_SEQ=7\n"
        "KBSEC_BASE_URL=https://kb.example\n"
        "KBSEC_APP_KEY=kb-key\n"
        "KBSEC_APP_SECRET=kb-secret\n"
        "TOSSINVEST_ACCOUNT_REFRESH_SECONDS=0.2\n"
        "UNRELATED_PRIVATE_VALUE=must-not-load\n",
        encoding="utf-8",
    )

    environment = app_module._runtime_environment(
        tmp_path, {"TOSSINVEST_CLIENT_ID": "process-id"}
    )

    assert environment["TOSSINVEST_CLIENT_ID"] == "process-id"
    assert environment["TOSSINVEST_CLIENT_SECRET"] == "dotenv-secret"
    assert environment["TOSSINVEST_ACCOUNT_SEQ"] == "7"
    assert environment["KBSEC_BASE_URL"] == "https://kb.example"
    assert environment["KBSEC_APP_KEY"] == "kb-key"
    assert environment["KBSEC_APP_SECRET"] == "kb-secret"
    assert "TOSSINVEST_ACCOUNT_REFRESH_SECONDS" not in environment
    assert "UNRELATED_PRIVATE_VALUE" not in environment


def test_main_window_local_dashboard_reload_does_not_trigger_account_refresh(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "latest.json"
    _write_empty_toss_account_snapshot(path)
    calls: list[tuple[AccountRefreshTrigger, object]] = []

    def refresh(trigger):
        calls.append((trigger, QtCore.QThread.currentThread()))

    window = MainWindow(
        tmp_path, account_snapshot_path=path, account_refresher=refresh
    )
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.show()
    for _ in range(100):
        app.processEvents()
        if calls and window._account_thread is None:
            break
        QtTest.QTest.qWait(5)

    assert calls == []
    assert window.dashboard.account_placeholder.badge.text().endswith("준비됨")
    assert window.dashboard.account_placeholder.details.isHidden()

    window.reload_dashboard()
    for _ in range(20):
        app.processEvents()
        QtTest.QTest.qWait(5)

    assert calls == []
    window.close()
    deadline = time.monotonic() + 5.0
    while (
        any(thread is not None for thread in window._managed_worker_threads())
        and time.monotonic() < deadline
    ):
        app.processEvents()
        QtTest.QTest.qWait(5)
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_main_window_coalesces_local_startup_manual_and_timer_reload(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path)
    calls: list[object] = []
    window.refresh_dashboard = lambda session="U": calls.append(("dashboard", session))
    window.refresh_market_chart = lambda asset, period: calls.append(("chart", asset, period))

    window._queue_local_dashboard_reload()
    window._queue_local_dashboard_reload()
    window.current_observation_reload_timer.timeout.emit()
    assert window._local_dashboard_reload_queued
    for _ in range(20):
        app.processEvents()
        if not window._local_dashboard_reload_queued:
            break
        QtTest.QTest.qWait(5)

    assert calls == [("dashboard", "U")]
    window.close()
    deadline = time.monotonic() + 5.0
    while (
        window.current_observation_reload_timer.isActive()
        and time.monotonic() < deadline
    ):
        app.processEvents()
        QtTest.QTest.qWait(5)
    assert not window.current_observation_reload_timer.isActive()


def _drain_main_window_workers(app, window, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if (
            not any(thread is not None for thread in window._managed_worker_threads())
            and not window._local_read_pending
        ):
            return
        QtTest.QTest.qWait(5)
    raise AssertionError("managed workers did not drain")


def _drain_local_read_workers(app, window, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if window._local_read_thread is None and not window._local_read_pending:
            return
        QtTest.QTest.qWait(5)
    raise AssertionError("local read worker did not drain")


def _drain_backtest_and_local_workers(app, window, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if (
            window._backtest_thread is None
            and window._local_read_thread is None
            and not window._local_read_pending
        ):
            return
        QtTest.QTest.qWait(5)
    raise AssertionError(
        "backtest and local read workers did not drain: "
        f"backtest={window._backtest_thread is not None}, "
        f"backtest_running={bool(window._backtest_thread and window._backtest_thread.isRunning())}, "
        f"accepted={window.backtest_page.has_accepted_bundle}, "
        f"local={window._local_read_thread is not None}, "
        f"local_running={bool(window._local_read_thread and window._local_read_thread.isRunning())}, "
        f"pending={tuple(window._local_read_pending)}"
    )


def _stub_fast_startup_local_reads(window, monkeypatch=None):
    def assign(target, name, value):
        if monkeypatch is None:
            setattr(target, name, value)
        else:
            monkeypatch.setattr(target, name, value)

    assign(window.service, "snapshot", lambda _session: {})
    assign(window.service, "chart_series", lambda *_args: pd.DataFrame())
    assign(
        window.health_artifact_service,
        "load",
        lambda: HealthArtifactView("READY", "empty fixture health", ()),
    )
    assign(window.service.index, "chart_view", lambda *_args: _index_series_view())
    assign(
        window.current_stage_service,
        "current_card_stage",
        lambda: DashboardCurrentStageView(
            as_of_utc="2026-08-26T00:00:00Z",
            metrics={},
            treasury_rate_views={},
        ),
    )


def test_data_status_refresh_action_enters_only_coalesced_local_dashboard_lane(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    provider_calls: list[object] = []
    window = MainWindow(
        tmp_path,
        toss_runtime_enabled=False,
        current_observation_runner=lambda: provider_calls.append(object()),
    )
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.current_observation_reload_timer.stop()
    _drain_main_window_workers(app, window)
    provider_calls.clear()  # Ignore the separately owned startup acquisition.

    window.data_status_page.refresh_lifecycle_reread.click()
    window.data_status_page.refresh_lifecycle_reread.click()

    assert window._local_dashboard_reload_queued is True
    assert provider_calls == []
    _drain_main_window_workers(app, window)
    assert provider_calls == []
    window.close()
    app.processEvents()


def test_main_window_local_read_lane_coalesces_dashboard_and_keeps_gui_responsive(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.show()
    _drain_main_window_workers(app, window)

    started = threading.Event()
    release = threading.Event()
    calls: list[object] = []
    rendered: list[str] = []
    market_rendered: list[pd.DataFrame] = []
    worker_threads: list[object] = []

    def snapshot(_session):
        worker_threads.append(QtCore.QThread.currentThread())
        calls.append(object())
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
            return {"marker": "old"}
        return {"marker": "new"}

    monkeypatch.setattr(window.service, "snapshot", snapshot)
    monkeypatch.setattr(
        window.service, "chart_series",
        lambda asset, _period: pd.DataFrame({"asset": [asset]}),
    )
    monkeypatch.setattr(
        window.health_artifact_service, "load",
        lambda: HealthArtifactView("READY", "empty fixture health", ()),
    )
    monkeypatch.setattr(
        window, "_render_dashboard_snapshot",
        lambda value: rendered.append(value["marker"]),
    )
    monkeypatch.setattr(
        window.dashboard, "render_market_chart", market_rendered.append,
    )

    window.refresh_dashboard()
    assert started.wait(2)
    window.refresh_dashboard()
    window.refresh_dashboard()
    window.refresh_market_chart("LATEST", "1Y")
    responsive: list[object] = []
    QtCore.QTimer.singleShot(0, lambda: responsive.append(QtCore.QThread.currentThread()))
    for _ in range(20):
        app.processEvents()
        if responsive:
            break
        QtTest.QTest.qWait(5)
    assert responsive == [app.thread()]
    assert len(calls) == 1

    release.set()
    _drain_main_window_workers(app, window)
    assert len(calls) == 2
    assert rendered == ["new"]
    assert len(market_rendered) == 1
    assert market_rendered[0]["asset"].tolist() == ["LATEST"]
    assert all(thread is not app.thread() for thread in worker_threads)
    window.close()
    app.processEvents()


def test_current_card_stage_publishes_while_full_read_is_blocked_and_suppresses_older_full(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.show()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        app.processEvents()
        if (
            window._local_read_thread is None
            and window._current_stage_thread is None
            and not window._local_read_pending
            and window._current_stage_pending is None
        ):
            break
        QtTest.QTest.qWait(5)

    old = DashboardMetricView(
        dataset_id="market_price_60m_current", series_id="USD_KRW_60M",
        label="USD/KRW", value=1380.0, unit="KRW per USD",
        as_of="08-24 16:30 KST", expected_as_of=None, source="old full",
        freshness="CURRENT_COMPLETED_30M", pit_status="PIT_BLOCKED",
        pit_label="display only", automation_policy="EVERY_30_MIN_CURRENT_ONLY",
        automation_enabled=True, display_state=DashboardDisplayState.VALUE,
        unavailable_reason=None, route="yahoo-market-current:GLOBAL_FX:KRW=X",
        source_timestamp="2026-08-24T07:30:00Z",
    )
    newest = replace(
        old, value=1385.18, as_of="08-24 17:00 KST", source="fast stage",
        source_timestamp="2026-08-24T08:00:00Z",
    )
    stage = DashboardCurrentStageView(
        as_of_utc="2026-08-24T08:20:00Z",
        metrics={"USD_KRW_60M": newest},
        treasury_rate_views=DashboardService.treasury_rate_views({
            "USD_KRW_60M": newest,
        }),
    )
    monkeypatch.setattr(
        window.current_stage_service, "current_card_stage", lambda: stage,
    )
    started = threading.Event()
    release = threading.Event()

    def blocked_snapshot(_session):
        started.set()
        assert release.wait(3)
        return {
            "dashboard_metrics": {"USD_KRW_60M": old},
            "treasury_rate_views": DashboardService.treasury_rate_views({
                "USD_KRW_60M": old,
            }),
        }

    monkeypatch.setattr(window.service, "snapshot", blocked_snapshot)
    monkeypatch.setattr(window.service, "chart_series", lambda *_args: pd.DataFrame())
    merged_results: list[dict] = []
    monkeypatch.setattr(window, "_render_dashboard_snapshot", merged_results.append)

    window.refresh_dashboard()
    assert started.wait(2)
    stage_deadline = time.monotonic() + 2.0
    while time.monotonic() < stage_deadline:
        app.processEvents()
        if window.dashboard.market_cards["USD_KRW_60M"].body.text().startswith("1,385.18"):
            break
        QtTest.QTest.qWait(5)
    assert window.dashboard.market_cards["USD_KRW_60M"].body.text().startswith("1,385.18")
    assert window.dashboard.rate_rows["USD_KRW"].value.text().startswith("1,385.18")
    responsive: list[bool] = []
    QtCore.QTimer.singleShot(0, lambda: responsive.append(True))
    app.processEvents()
    assert responsive == [True]

    release.set()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        app.processEvents()
        if window._local_read_thread is None and window._current_stage_thread is None:
            break
        QtTest.QTest.qWait(5)
    assert merged_results
    merged = merged_results[-1]["dashboard_metrics"]["USD_KRW_60M"]
    assert merged.value == pytest.approx(1385.18)
    assert merged.source_timestamp == "2026-08-24T08:00:00Z"

    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_rejected_current_stage_never_suppresses_a_strictly_newer_full_metric(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window, monkeypatch)
    _drain_main_window_workers(app, window)
    full = DashboardMetricView(
        dataset_id="market_price_60m_current", series_id="USD_KRW_60M",
        label="USD/KRW", value=1390.0, unit="KRW per USD",
        as_of="08-24 18:00 KST", expected_as_of=None, source="newer full",
        freshness="CURRENT_COMPLETED_60M", pit_status="PIT_BLOCKED",
        pit_label="display only", automation_policy="EVERY_30_MIN_CURRENT_ONLY",
        automation_enabled=True, display_state=DashboardDisplayState.VALUE,
        unavailable_reason=None, route="yahoo-market-current:GLOBAL_FX:KRW=X",
        source_timestamp="2026-08-24T09:00:00Z",
    )
    older_rejected = replace(
        full, value=None, as_of="08-24 17:00 KST", source="older rejected stage",
        freshness="STALE", display_state=DashboardDisplayState.REFRESH_REQUIRED,
        unavailable_reason="stale current projection",
        source_timestamp="2026-08-24T08:00:00Z",
    )
    window._latest_current_stage = (
        7,
        DashboardCurrentStageView(
            as_of_utc="2026-08-24T09:10:00Z",
            metrics={"USD_KRW_60M": older_rejected},
            treasury_rate_views={},
        ),
    )
    merged = window._merge_latest_current_stage(
        {"dashboard_metrics": {"USD_KRW_60M": full}}, 7,
    )
    assert merged["dashboard_metrics"]["USD_KRW_60M"] is full
    assert merged["dashboard_metrics"]["USD_KRW_60M"].value == 1390.0
    assert merged["dashboard_metrics"]["USD_KRW_60M"].source_timestamp == "2026-08-24T09:00:00Z"

    newer_rejected = replace(
        older_rejected, as_of="08-24 19:00 KST",
        source_timestamp="2026-08-24T10:00:00Z",
    )
    window._latest_current_stage = (
        8,
        DashboardCurrentStageView(
            as_of_utc="2026-08-24T10:10:00Z",
            metrics={"USD_KRW_60M": newer_rejected},
            treasury_rate_views={},
        ),
    )
    merged = window._merge_latest_current_stage(
        {"dashboard_metrics": {"USD_KRW_60M": full}}, 8,
    )
    assert merged["dashboard_metrics"]["USD_KRW_60M"] is newer_rejected
    assert merged["dashboard_metrics"]["USD_KRW_60M"].value is None

    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


@pytest.mark.parametrize(
    ("full_timestamp", "rejected_timestamp"),
    [
        ("2026-08-24T09:00:00Z", "2026-08-24T18:00:00+09:00"),
        ("2026-08-24T09:00:00Z", None),
        ("2026-08-24T09:00:00Z", "2026-08-24T08:00:00"),
        ("2026-08-24T09:00:00Z", "not-a-timestamp"),
        (None, "2026-08-24T08:00:00Z"),
        ("2026-08-24T09:00:00", "2026-08-24T08:00:00Z"),
        ("not-a-timestamp", "2026-08-24T08:00:00Z"),
    ],
    ids=(
        "equal-aware-instants",
        "rejected-time-missing",
        "rejected-time-naive",
        "rejected-time-malformed",
        "full-time-missing",
        "full-time-naive",
        "full-time-malformed",
    ),
)
def test_rejected_current_stage_fails_closed_when_strictly_older_order_is_unproven(
    tmp_path, monkeypatch, full_timestamp, rejected_timestamp,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window, monkeypatch)
    _drain_main_window_workers(app, window)
    full = DashboardMetricView(
        dataset_id="market_price_60m_current", series_id="USD_KRW_60M",
        label="USD/KRW", value=1390.0, unit="KRW per USD",
        as_of="08-24 18:00 KST", expected_as_of=None, source="full snapshot",
        freshness="CURRENT_COMPLETED_60M", pit_status="PIT_BLOCKED",
        pit_label="display only", automation_policy="EVERY_30_MIN_CURRENT_ONLY",
        automation_enabled=True, display_state=DashboardDisplayState.VALUE,
        unavailable_reason=None, route="yahoo-market-current:GLOBAL_FX:KRW=X",
        source_timestamp=full_timestamp,
    )
    rejected = replace(
        full, value=None, source="rejected current stage", freshness="STALE",
        display_state=DashboardDisplayState.REFRESH_REQUIRED,
        unavailable_reason="current projection rejected",
        source_timestamp=rejected_timestamp,
    )
    window._latest_current_stage = (
        9,
        DashboardCurrentStageView(
            as_of_utc="2026-08-24T09:10:00Z",
            metrics={"USD_KRW_60M": rejected},
            treasury_rate_views={},
        ),
    )

    merged = window._merge_latest_current_stage(
        {"dashboard_metrics": {"USD_KRW_60M": full}}, 9,
    )

    assert merged["dashboard_metrics"]["USD_KRW_60M"] is rejected
    assert merged["dashboard_metrics"]["USD_KRW_60M"].value is None
    assert (
        merged["dashboard_metrics"]["USD_KRW_60M"].display_state
        is DashboardDisplayState.REFRESH_REQUIRED
    )
    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_current_card_stage_bursts_keep_latest_pending_and_close_without_orphan_thread(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.show()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        app.processEvents()
        if window._local_read_thread is None and window._current_stage_thread is None:
            break
        QtTest.QTest.qWait(5)

    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []
    stage = DashboardCurrentStageView(
        as_of_utc="2026-08-24T08:20:00Z", metrics={},
        treasury_rate_views={},
    )

    def blocked_stage():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
        return stage

    monkeypatch.setattr(
        window.current_stage_service, "current_card_stage", blocked_stage,
    )
    window.refresh_dashboard()
    assert started.wait(2)
    window.refresh_dashboard()
    window.refresh_dashboard()
    assert window._current_stage_pending == window._current_stage_generation

    before = time.perf_counter()
    window.close()
    elapsed = time.perf_counter() - before
    app.processEvents()
    assert elapsed < 0.1
    assert window.isVisible()
    assert window._current_stage_pending is None

    release.set()
    deadline = time.monotonic() + 5.0
    while window.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)
    assert calls == [1]
    assert not window.isVisible()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_main_window_local_read_lane_keeps_only_latest_index_and_fails_closed(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.show()
    _drain_main_window_workers(app, window)

    started = threading.Event()
    release = threading.Event()
    calls: list[tuple[str, str]] = []
    rendered: list[object] = []

    def chart_view(index, period):
        calls.append((index, period))
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
            return "old"
        return "new"

    monkeypatch.setattr(window.service.index, "chart_view", chart_view)
    monkeypatch.setattr(window.index_page, "render", rendered.append)
    window.refresh_index("KOSPI", "120D")
    assert started.wait(2)
    window.refresh_index("KOSDAQ", "60D")
    window.refresh_index("KOSPI200", "1Y")
    release.set()
    _drain_main_window_workers(app, window)

    assert calls == [("KOSPI", "120D"), ("KOSPI200", "1Y")]
    assert rendered == ["new"]

    monkeypatch.setattr(
        window.service.index, "chart_view",
        lambda *_args: (_ for _ in ()).throw(OSError("private path")),
    )
    window.refresh_index("KOSPI", "120D")
    _drain_main_window_workers(app, window)
    assert isinstance(rendered[-1], pd.DataFrame) and rendered[-1].empty
    window.close()
    app.processEvents()


def test_main_window_local_read_close_is_nonblocking_and_drops_pending(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.show()
    _drain_main_window_workers(app, window)
    started = threading.Event()
    release = threading.Event()

    def snapshot(_session):
        started.set()
        assert release.wait(3)
        return {}

    monkeypatch.setattr(window.service, "snapshot", snapshot)
    monkeypatch.setattr(window.service, "chart_series", lambda *_args: pd.DataFrame())
    monkeypatch.setattr(
        window.health_artifact_service, "load",
        lambda: HealthArtifactView("READY", "empty fixture health", ()),
    )
    window.refresh_dashboard()
    assert started.wait(2)
    window.refresh_index("KOSPI", "120D")

    before = time.perf_counter()
    window.close()
    elapsed = time.perf_counter() - before
    app.processEvents()
    assert elapsed < 0.1
    assert window.isVisible()
    assert window._local_read_pending == {}

    release.set()
    deadline = time.monotonic() + 5.0
    while window.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)
    assert not window.isVisible()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def _write_ur145_current_projection(root: Path) -> str:
    """Create only the accepted local 000660 projection for a fake runner."""
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    path = root / "data/state/current_observations/naver_web_000660_current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": "naver-web-current:XKRX:000660",
            "identity": {
                "dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": "000660",
            },
            "interval": "snapshot", "value": 738_000.0, "unit": "KRW per share",
            "provider": "NAVER_FINANCE_WEB", "upstream_provider": "NAVER_FINANCE_WEB",
            "source_route": "NAVER_WEB:/api/stock/000660/basic",
            "provider_timestamp_utc": timestamp, "retrieved_at_utc": timestamp,
            "finality": "PROVISIONAL", "display_only": True, "pit_safe": False,
        }],
        "circuits": {}, "decisions": {},
    }), encoding="utf-8")
    return timestamp


def test_main_window_current_acquisition_worker_coalesces_and_rereads_ur145_projection(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    started = threading.Event()
    calls: list[object] = []
    timestamps: list[str] = []
    ensure_manifest(tmp_path)
    manifest_now = datetime.fromisoformat("2026-08-21T14:30:10+09:00")

    def fake_runner():
        calls.append(QtCore.QThread.currentThread())
        started.set()
        time.sleep(0.03)
        timestamps.append(_write_ur145_current_projection(tmp_path))
        return {"status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0}

    window = MainWindow(
        tmp_path,
        current_observation_runner_factory=lambda: (
            fake_runner if is_active(tmp_path, now=manifest_now) else None
        ),
    )
    window.show()
    for _ in range(100):
        app.processEvents()
        if started.is_set():
            break
        QtTest.QTest.qWait(5)
    assert started.is_set()

    # Both due notifications arrive while the sole worker owns the request.
    window.current_observation_reload_timer.timeout.emit()
    window.current_observation_reload_timer.timeout.emit()
    for _ in range(150):
        app.processEvents()
        if len(calls) == 1 and window._current_observation_thread is None:
            break
        QtTest.QTest.qWait(5)
    _drain_main_window_workers(app, window, timeout=30.0)

    assert len(calls) == 1
    assert calls[0] is not app.thread()
    assert window._current_observation_last_result == {
        "status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0,
    }
    coverage = window.service.current_observation_coverage()["EQUITY_000660"]
    assert coverage.displays_value and coverage.value == pytest.approx(738_000.0)
    assert coverage.provider_timestamp_utc == timestamps[0]
    assert coverage.unavailable_reason == (
        "Undocumented Naver public-web; local personal display only; redistribution "
        "unverified; PIT-blocked; pilot observation; exact manifest-window collector "
        "enabled; no generic/high-frequency polling."
    )
    tooltip = window.dashboard.current_observation_status.toolTip()
    assert "NAVER_FINANCE_WEB" in tooltip
    assert f"provider_timestamp_utc={timestamps[0]}" in tooltip
    assert "PIT-blocked" in tooltip
    assert window.dashboard.current_observation_strip_cells == []
    assert "국내" in window.dashboard.domestic_market_session.accessibleName()
    assert "미국" in window.dashboard.us_market_session.accessibleName()

    window.close()
    app.processEvents()
    assert not window.current_observation_reload_timer.isActive()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_main_window_current_acquisition_stays_local_only_when_manifest_window_inactive(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ensure_manifest(tmp_path)
    inactive_now = datetime.fromisoformat("2026-08-21T14:00:10+09:00")
    calls: list[object] = []

    def fake_runner():
        calls.append(QtCore.QThread.currentThread())
        return {"status": "MUST_NOT_RUN", "raw_gets": 0, "replay_api_calls": 0}

    window = MainWindow(
        tmp_path,
        current_observation_runner_factory=lambda: (
            fake_runner if is_active(tmp_path, now=inactive_now) else None
        ),
    )
    _stub_fast_startup_local_reads(window)
    _drain_local_read_workers(app, window)
    window.show()
    window.current_observation_reload_timer.timeout.emit()
    for _ in range(20):
        app.processEvents()
        QtTest.QTest.qWait(2)

    assert calls == []
    assert window._current_observation_thread is None
    assert window._current_observation_last_result is None
    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_main_window_current_acquisition_worker_closes_inflight_without_orphan_thread(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    started = threading.Event()
    ensure_manifest(tmp_path)
    manifest_now = datetime.fromisoformat("2026-08-21T14:30:10+09:00")

    def fake_runner():
        started.set()
        time.sleep(0.03)
        return {"status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0}

    window = MainWindow(
        tmp_path,
        current_observation_runner_factory=lambda: (
            fake_runner if is_active(tmp_path, now=manifest_now) else None
        ),
    )
    window.show()
    for _ in range(100):
        app.processEvents()
        if started.is_set():
            break
        QtTest.QTest.qWait(5)
    assert started.is_set()
    thread = window._current_observation_thread
    assert thread is not None
    finished_while_owned = []
    destroyed = []
    thread.finished.connect(
        lambda: finished_while_owned.append(
            window._current_observation_thread is thread
        )
    )
    thread.destroyed.connect(lambda _object: destroyed.append(True))

    window.close()
    deadline = time.monotonic() + 5.0
    while window.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)
    assert not window.isVisible()
    assert finished_while_owned == [True]
    assert destroyed == [True]
    assert window._current_observation_thread is None
    assert not window.current_observation_reload_timer.isActive()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_main_window_current_acquisition_keeps_finished_lane_owned_until_destroyed(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    started = threading.Event()
    release = threading.Event()
    factory_calls = []
    runner_calls = []
    ensure_manifest(tmp_path)
    manifest_now = datetime.fromisoformat("2026-08-21T14:30:10+09:00")

    def fake_runner():
        runner_calls.append("run")
        started.set()
        assert release.wait(timeout=2.0)
        return {"status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0}

    def runner_factory():
        factory_calls.append("factory")
        return fake_runner if is_active(tmp_path, now=manifest_now) else None

    window = MainWindow(
        tmp_path,
        current_observation_runner_factory=runner_factory,
    )
    _stub_fast_startup_local_reads(window)
    _drain_local_read_workers(app, window)
    window.show()
    for _ in range(100):
        app.processEvents()
        if started.is_set():
            break
        QtTest.QTest.qWait(5)
    assert started.is_set()
    thread = window._current_observation_thread
    assert thread is not None
    finished_while_owned = []
    factory_calls_after_finished_request = []
    destroyed = []

    def request_again_from_finished_gap():
        finished_while_owned.append(window._current_observation_thread is thread)
        window._request_current_observation_acquisition()
        factory_calls_after_finished_request.append(len(factory_calls))

    thread.finished.connect(request_again_from_finished_gap)
    thread.destroyed.connect(lambda _object: destroyed.append(True))
    release.set()
    deadline = time.monotonic() + 5.0
    while not destroyed and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)

    assert finished_while_owned == [True]
    assert factory_calls_after_finished_request == [1]
    assert factory_calls == ["factory"]
    assert runner_calls == ["run"]
    assert destroyed == [True]
    assert window._current_observation_thread is None
    _drain_local_read_workers(app, window)
    window.close()
    app.processEvents()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_app_current_observation_runner_requires_active_public_manifest_window(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    inactive = datetime.fromisoformat("2026-08-21T14:00:10+09:00")
    active = datetime.fromisoformat("2026-08-21T14:30:10+09:00")

    assert app_module._ur161_current_observation_runner(tmp_path, now=active) is None
    ensure_manifest(tmp_path)
    assert app_module._ur161_current_observation_runner(tmp_path, now=inactive) is None
    # The callable is bound but never invoked in this test; lifecycle execution
    # remains covered with the injected fake runner above.
    assert callable(app_module._ur161_current_observation_runner(tmp_path, now=active))
    window = app_module.build_main_window(tmp_path, {})
    assert window.current_observation_runner is None
    assert callable(window.current_observation_runner_factory)
    window.close()
    app.processEvents()


def test_app_composes_active_current_routes_serially_and_isolates_failure(tmp_path, monkeypatch):
    now = datetime.fromisoformat("2026-08-21T14:30:10+09:00")
    calls: list[str] = []
    monkeypatch.setattr(app_module, "_ur161_current_observation_runner", lambda _root, *, now: lambda: calls.append("UR161") or {"status": "FAILED_BOUNDED"})
    monkeypatch.setattr(app_module, "_ur167_current_observation_runner", lambda _root, *, now: lambda: (_ for _ in ()).throw(RuntimeError("synthetic")))
    monkeypatch.setattr(app_module, "_ur191_current_observation_runner", lambda _root, *, now: lambda: calls.append("UR191") or {"status": "WINDOW_NOT_MANIFESTED", "raw_gets": 0})
    monkeypatch.setattr(app_module, "_ur193_current_observation_runner", lambda _root, *, now: lambda: calls.append("UR193") or {"status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0})
    monkeypatch.setattr(app_module, "_ur203_current_observation_runner", lambda _root, *, now: lambda: calls.append("UR203") or {"status": "WINDOW_NOT_MANIFESTED", "raw_gets": 0})
    runner = app_module._dashboard_current_observation_runner(tmp_path, now=now)
    assert callable(runner)
    assert runner() == {
        "UR161": {"status": "FAILED_BOUNDED"},
        "UR167": {"status": "FAILED", "safe_code": "RuntimeError"},
        "UR191": {"status": "WINDOW_NOT_MANIFESTED", "raw_gets": 0},
        "UR193": {"status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0},
        "UR203": {"status": "WINDOW_NOT_MANIFESTED", "raw_gets": 0},
    }
    assert calls == ["UR161", "UR191", "UR193", "UR203"]


def test_app_ur203_runner_uses_public_eligibility_and_is_api_zero_when_unavailable(tmp_path, monkeypatch):
    inactive = datetime.fromisoformat("2026-08-21T17:50:00+09:00")
    active = datetime.fromisoformat("2026-08-24T09:30:00+09:00")
    calls: list[tuple[Path, datetime]] = []

    monkeypatch.setattr(app_module, "ur203_eligible_identities", lambda _root, *, now: ())
    assert app_module._ur203_current_observation_runner(tmp_path, now=inactive) is None

    monkeypatch.setattr(app_module, "ur203_eligible_identities", lambda _root, *, now: ("000660", "005930"))
    monkeypatch.setattr(
        app_module,
        "run_naver_equity_ur199",
        lambda root, *, now: calls.append((root, now)) or {"status": "WINDOW_NOT_MANIFESTED", "raw_gets": 0},
    )
    runner = app_module._ur203_current_observation_runner(tmp_path, now=active)
    assert callable(runner)
    assert runner() == {"status": "WINDOW_NOT_MANIFESTED", "raw_gets": 0}
    assert calls == [(tmp_path, active)]

    monkeypatch.setattr(
        app_module,
        "ur203_eligible_identities",
        lambda _root, *, now: (_ for _ in ()).throw(ValueError("malformed manifest")),
    )
    assert app_module._ur203_current_observation_runner(tmp_path, now=active) is None
    assert calls == [(tmp_path, active)]


def test_app_ur208_half_open_naver_preflights_bind_only_the_current_boundary(tmp_path, monkeypatch):
    at_0931 = datetime.fromisoformat("2026-08-24T09:31:00+09:00")
    at_1000 = datetime.fromisoformat("2026-08-24T10:00:00+09:00")
    calls: list[tuple[str, datetime]] = []

    # Missing public manifests never inject a callable, so no collector can run.
    assert app_module._ur191_current_observation_runner(tmp_path, now=at_0931) is None
    assert app_module._ur203_current_observation_runner(tmp_path, now=at_0931) is None

    for relative_path, payload in (
        (UR191_MANIFEST_PATH, ur191_manifest_payload()),
        (UR203_MANIFEST_PATH, ur203_manifest_payload()),
    ):
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    assert ur191_eligible_boundary(tmp_path, now=at_0931) == "2026-08-24T09:30:00+09:00"
    assert ur203_eligible_identities(tmp_path, now=at_0931) == ("000660", "005930")
    def fake_ur191(_root, *, now):
        calls.append(("UR191", now))
        return {"selected_boundary": ur191_eligible_boundary(tmp_path, now=now), "raw_gets": 0}

    def fake_ur203(_root, *, now):
        calls.append(("UR203", now))
        return {"window_id": now.replace(minute=now.minute - now.minute % 30, second=0, microsecond=0).isoformat(), "api_calls": 0}

    monkeypatch.setattr(app_module, "run_naver_mobile_home_ur191", fake_ur191)
    monkeypatch.setattr(app_module, "run_naver_equity_ur199", fake_ur203)
    early = app_module._dashboard_current_observation_runner(tmp_path, now=at_0931)
    assert callable(early)
    assert early() == {
        "UR191": {"selected_boundary": "2026-08-24T09:30:00+09:00", "raw_gets": 0},
        "UR203": {"window_id": "2026-08-24T09:30:00+09:00", "api_calls": 0},
    }
    assert calls == [("UR191", at_0931), ("UR203", at_0931)]

    # The 09:30 boundary is expired at 10:00; both app callables receive only
    # the current 10:00 clock, never a catch-up 09:30 clock.
    assert ur191_eligible_boundary(tmp_path, now=at_1000) == "2026-08-24T10:00:00+09:00"
    assert ur203_eligible_identities(tmp_path, now=at_1000) == ("000660", "005930")
    later = app_module._dashboard_current_observation_runner(tmp_path, now=at_1000)
    assert callable(later)
    assert later() == {
        "UR191": {"selected_boundary": "2026-08-24T10:00:00+09:00", "raw_gets": 0},
        "UR203": {"window_id": "2026-08-24T10:00:00+09:00", "api_calls": 0},
    }
    assert calls[-2:] == [("UR191", at_1000), ("UR203", at_1000)]

    (tmp_path / UR191_STATE_PATH).write_text(json.dumps({
        "schema_version": 1, "operation_id": "UR-191",
        "windows": {"2026-08-24T10:00:00+09:00": {"status": "COMPLETE"}},
    }), encoding="utf-8")
    (tmp_path / UR203_STATE_PATH).write_text(json.dumps({
        "schema_version": 1, "operation_id": "UR-199", "windows": {
            "2026-08-24T10:00:00+09:00": {
                "000660": {"status": "COMPLETE"}, "005930": {"status": "COMPLETE"},
            },
        },
    }), encoding="utf-8")
    assert app_module._ur191_current_observation_runner(tmp_path, now=at_1000) is None
    assert app_module._ur203_current_observation_runner(tmp_path, now=at_1000) is None
    assert calls[-2:] == [("UR191", at_1000), ("UR203", at_1000)]

    # A malformed ledger remains a preflight failure; no callable is injected.
    (tmp_path / UR191_STATE_PATH).write_text("{not-json", encoding="utf-8")
    (tmp_path / UR203_STATE_PATH).write_text("{not-json", encoding="utf-8")
    assert app_module._ur191_current_observation_runner(tmp_path, now=at_1000) is None
    assert app_module._ur203_current_observation_runner(tmp_path, now=at_1000) is None
    assert calls[-2:] == [("UR191", at_1000), ("UR203", at_1000)]


def test_app_ur193_runner_is_current_window_and_terminal_ledger_fail_closed(tmp_path, monkeypatch):
    inactive = datetime.fromisoformat("2026-08-21T17:00:00+09:00")
    active = datetime.fromisoformat("2026-08-21T18:01:00+09:00")
    calls: list[Path] = []
    monkeypatch.setattr(
        app_module, "run_nasdaq_soxx_ur193",
        lambda root: calls.append(root) or {"status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0},
    )

    assert app_module._ur193_current_observation_runner(tmp_path, now=inactive) is None
    runner = app_module._ur193_current_observation_runner(tmp_path, now=active)
    assert callable(runner)
    assert runner() == {"status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0}
    assert calls == [tmp_path]

    state_path = tmp_path / UR193_STATE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema_version": 1, "operation_id": "UR-193",
        "windows": {"2026-08-21T18:00:00+09:00": {"status": "COMPLETE_ACCEPTED"}},
    }), encoding="utf-8")
    assert app_module._ur193_current_observation_runner(tmp_path, now=active) is None
    assert calls == [tmp_path]


def test_main_window_coalesces_active_ur193_composition_and_closes_cleanly(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    now = datetime.fromisoformat("2026-08-21T18:00:00+09:00")
    started = threading.Event()
    calls: list[object] = []

    def fake_ur193():
        calls.append(QtCore.QThread.currentThread())
        started.set()
        time.sleep(0.03)
        return {"status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0}

    monkeypatch.setattr(app_module, "_ur161_current_observation_runner", lambda _root, *, now: None)
    monkeypatch.setattr(app_module, "_ur167_current_observation_runner", lambda _root, *, now: None)
    monkeypatch.setattr(app_module, "_ur191_current_observation_runner", lambda _root, *, now: None)
    monkeypatch.setattr(app_module, "_ur193_current_observation_runner", lambda _root, *, now: fake_ur193)
    window = MainWindow(
        tmp_path,
        current_observation_runner_factory=lambda: app_module._dashboard_current_observation_runner(
            tmp_path, now=now,
        ),
    )
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.show()
    for _ in range(100):
        app.processEvents()
        if started.is_set():
            break
        QtTest.QTest.qWait(5)
    assert started.is_set()
    window.current_observation_reload_timer.timeout.emit()
    window.current_observation_reload_timer.timeout.emit()
    for _ in range(150):
        app.processEvents()
        if len(calls) == 1 and window._current_observation_thread is None:
            break
        QtTest.QTest.qWait(5)
    assert len(calls) == 1 and calls[0] is not app.thread()
    assert window._current_observation_last_result == {
        "UR193": {"status": "NO_REPEAT", "raw_gets": 0, "replay_api_calls": 0},
    }
    _drain_local_read_workers(app, window)
    window.close()
    app.processEvents()
    assert not window.current_observation_reload_timer.isActive()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_main_window_coalesces_active_ur203_and_closes_cleanly(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    now = datetime.fromisoformat("2026-08-24T09:30:00+09:00")
    started = threading.Event()
    calls: list[object] = []

    def fake_ur203():
        calls.append(QtCore.QThread.currentThread())
        started.set()
        time.sleep(0.03)
        return {"status": "WINDOW_NOT_MANIFESTED", "raw_gets": 0}

    monkeypatch.setattr(app_module, "_ur161_current_observation_runner", lambda _root, *, now: None)
    monkeypatch.setattr(app_module, "_ur167_current_observation_runner", lambda _root, *, now: None)
    monkeypatch.setattr(app_module, "_ur191_current_observation_runner", lambda _root, *, now: None)
    monkeypatch.setattr(app_module, "_ur193_current_observation_runner", lambda _root, *, now: None)
    monkeypatch.setattr(app_module, "_ur203_current_observation_runner", lambda _root, *, now: fake_ur203)
    window = MainWindow(
        tmp_path,
        current_observation_runner_factory=lambda: app_module._dashboard_current_observation_runner(
            tmp_path, now=now,
        ),
    )
    _stub_fast_startup_local_reads(window, monkeypatch)
    window.show()
    for _ in range(100):
        app.processEvents()
        if started.is_set():
            break
        QtTest.QTest.qWait(5)
    assert started.is_set()
    window.current_observation_reload_timer.timeout.emit()
    window.current_observation_reload_timer.timeout.emit()
    for _ in range(150):
        app.processEvents()
        if len(calls) == 1 and window._current_observation_thread is None:
            break
        QtTest.QTest.qWait(5)
    assert len(calls) == 1 and calls[0] is not app.thread()
    assert window._current_observation_last_result == {
        "UR203": {"status": "WINDOW_NOT_MANIFESTED", "raw_gets": 0},
    }
    _drain_local_read_workers(app, window)
    window.close()
    app.processEvents()
    assert not window.current_observation_reload_timer.isActive()
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_main_window_busy_manual_account_clicks_coalesce_to_one_further_cycle(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "latest.json"
    _write_empty_toss_account_snapshot(path)
    calls: list[AccountRefreshTrigger] = []
    first_started = threading.Event()
    release_first = threading.Event()

    def refresh(trigger):
        calls.append(trigger)
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(3)

    window = MainWindow(
        tmp_path,
        account_snapshot_path=path,
        account_refresher=refresh,
    )
    _stub_fast_startup_local_reads(window)
    window.show()
    for _ in range(100):
        app.processEvents()
        if window._account_thread is None:
            break
        QtTest.QTest.qWait(5)
    assert calls == []

    window.account_page.refresh_button.click()
    assert first_started.wait(2)
    window.account_page.refresh_button.click()
    window.account_page.refresh_button.click()
    assert calls == [AccountRefreshTrigger.MANUAL]
    release_first.set()
    deadline = time.monotonic() + 5.0
    while window._account_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)
    assert calls == [AccountRefreshTrigger.MANUAL, AccountRefreshTrigger.MANUAL]

    window.close()
    deadline = time.monotonic() + 5.0
    while window.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)

    remaining_threads = {
        name: (thread.isRunning(),)
        for name, thread in {
            "local": window._local_read_thread,
            "current_stage": window._current_stage_thread,
            "backtest": window._backtest_thread,
            "account": window._account_thread,
            "current_observation": window._current_observation_thread,
            "equity": window._equity_thread,
            "us_etf": window._us_etf_thread,
        }.items()
        if thread is not None
    }
    assert not window.isVisible(), remaining_threads
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_account_finished_rechecks_transient_running_wrapper(tmp_path, monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window)
    _drain_local_read_workers(app, window)
    running_checks: list[int] = []
    close_checks: list[bool] = []

    class TransientRunningThread(QtCore.QThread):
        def isRunning(self) -> bool:
            running_checks.append(1)
            return len(running_checks) == 1

    thread = TransientRunningThread(window)
    worker = object()
    window._account_thread = thread
    window._account_worker = worker
    monkeypatch.setattr(
        window,
        "_schedule_pending_close_check",
        lambda: close_checks.append(True),
    )

    window._account_thread_finished(thread)

    assert window._account_thread is thread
    assert window._account_worker is worker
    deadline = time.monotonic() + 1.0
    while window._account_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)

    assert len(running_checks) >= 2
    assert window._account_thread is None
    assert window._account_worker is None
    assert close_checks == [True]
    window.close()
    app.processEvents()


@pytest.mark.parametrize("_attempt", range(3))
def test_main_window_close_during_manual_account_refresh_retires_thread(
    tmp_path, _attempt,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / f"latest-{_attempt}.json"
    _write_empty_toss_account_snapshot(path)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def refresh(trigger):
        calls.append(trigger)
        started.set()
        assert release.wait(3)

    window = MainWindow(
        tmp_path,
        account_snapshot_path=path,
        account_refresher=refresh,
    )
    _stub_fast_startup_local_reads(window)
    window.show()
    deadline = time.monotonic() + 5.0
    while window._account_thread is not None and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)

    window.account_page.refresh_button.click()
    assert started.wait(2)
    assert window.close() is False
    assert window.isVisible()
    release.set()

    deadline = time.monotonic() + 5.0
    while window.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(5)

    assert calls == [AccountRefreshTrigger.MANUAL]
    assert not window.isVisible()
    assert window._account_thread is None
    assert not any(
        thread.isRunning() for thread in window.findChildren(QtCore.QThread)
    )


def test_dashboard_rates_use_official_yields_as_primary_values():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    official = _metric(
        "UST2", 4.19, freshness="EXPECTED_LAG",
        state=DashboardDisplayState.VALUE, unit="percent",
    )
    futures = _metric(
        "UST2_FUTURES_60M", 103.06, freshness="60M_DELAYED",
        state=DashboardDisplayState.VALUE, unit="futures price",
    )

    page.render({"dashboard_metrics": {"UST2": official, "UST2_FUTURES_60M": futures}})

    assert page.rate_rows["UST2"].label.text() == "2Y"
    assert page.rate_rows["UST2"].value.text() == "4.19%"
    assert "103.06" not in page.rate_rows["UST2"].value.text()
    assert page.rate_rows["UST2"].meta.text() == "08-18"
    page.close()
    app.processEvents()


def test_dashboard_treasury_rate_view_keeps_quote_index_and_official_dates_separate():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    official = _metric(
        "UST10", 4.31, freshness="EXPECTED_LAG",
        state=DashboardDisplayState.VALUE, as_of="2026-08-17", unit="percent",
    )
    quote = _metric(
        "^TNX", 4.222, freshness="CURRENT",
        state=DashboardDisplayState.VALUE,
        as_of="2026-08-20 04:05 KST", unit="quote index points",
        dataset_id="market_price_15m_observation",
    )
    view = TreasuryRateView(
        view_id="UST10", label="미국 10Y 금리",
        official_daily=official, intraday_quote=quote,
        official_provider="FRED", official_data_type="OFFICIAL_DAILY_YIELD",
        intraday_provider="Yahoo/Cboe",
        intraday_data_type="INDICATIVE_DELAYED_QUOTE_INDEX",
    )
    quote_series = DashboardSeriesView(quote, pd.DataFrame({
        "date": pd.date_range("2026-08-19 13:20", periods=3, freq="15min", tz="UTC"),
        "value": [4.210, 4.215, 4.222],
    }))
    page.render({
        "dashboard_metrics": {"UST10": official, "UST10_QUOTE_15M": quote},
        "dashboard_series": {"UST10_QUOTE_15M": quote_series},
        "treasury_rate_views": {"UST10": view},
    })

    row = page.rate_rows["UST10"]
    assert row.value.text() == "4.22%"
    assert row.label.text() == "10Y"
    assert "Yahoo" in row.meta.text()
    assert "15분 지연" not in row.meta.text()
    assert "2026-08-20 04:05 KST" in row.toolTip()
    assert "추가 배율 변환을 적용하지 않았습니다" in row.toolTip()
    assert "INDICATIVE_DELAYED_QUOTE_INDEX" in row.toolTip()
    page.close()
    app.processEvents()


def test_derivative_summary_renders_official_short_selling_amount_as_market_scope():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    metric = _metric(
        "SHORT_SELLING_VALUE", 400_000_000_000,
        freshness="CURRENT", state=DashboardDisplayState.VALUE,
        as_of="2026-08-21", unit="KRW",
        dataset_id="kr_short_selling_trading_daily",
    )

    page.render({"dashboard_metrics": {"SHORT_SELLING_VALUE": metric}})

    card = page.derivative_cards["SHORT_SELLING_VALUE"]
    assert card.body.text() == "0.40조원"
    assert card.meta.text() == "KOSPI+KOSDAQ · 2026-08-21 장마감"
    assert card.badge.isHidden()
    assert "공매도 잔고·대차잔고" in card.toolTip()
    page.close()
    app.processEvents()


def test_dashboard_rate_groups_show_typed_changes_and_keep_korean_yields_numeric_free():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    metrics = {
        "USD_KRW": _metric(
            "USD_KRW", 1392.2, freshness="CURRENT",
            state=DashboardDisplayState.VALUE, unit="KRW per USD",
            as_of="2026-08-19", change=-3.4, change_pct=-0.24,
        ),
        "USD_JPY": _metric(
            "USD_JPY", 159.21, freshness="EXPECTED_LAG",
            state=DashboardDisplayState.VALUE, unit="JPY per USD",
            as_of="2026-08-14", change=-0.14, change_pct=-0.08786,
            dataset_id="fred_usd_fx_daily",
        ),
        "UST2": _metric(
            "UST2", 4.19, freshness="EXPECTED_LAG",
            state=DashboardDisplayState.VALUE, unit="percent", change=0.02,
        ),
        "UST10": _metric(
            "UST10", 4.72, freshness="EXPECTED_LAG",
            state=DashboardDisplayState.VALUE, unit="percent", change=-0.01,
        ),
        "UST30": _metric(
            "UST30", 5.31, freshness="EXPECTED_LAG",
            state=DashboardDisplayState.VALUE, unit="percent", change=0.0,
        ),
        "UST10_2_SPREAD": _metric(
            "UST10_2_SPREAD", 0.53, freshness="EXPECTED_LAG",
            state=DashboardDisplayState.VALUE, unit="percentage points", change=-0.03,
            dataset_id="us_treasury_spread_daily",
        ),
    }
    series = {
        key: DashboardSeriesView(metric, pd.DataFrame({"value": [metric.value - 0.1, metric.value]}))
        for key, metric in metrics.items()
    }
    page.render({
        "dashboard_metrics": metrics,
        "dashboard_series": series,
        "daily_average_comparisons": {"USD_JPY": _comparison("USD_JPY")},
    })
    page.resize(1600, 900)
    page.show()
    app.processEvents()

    assert [page.rate_groups[key].accessibleName() for key in ("FX", "KR", "US", "SPREAD")] == [
        "환율", "한국 국채", "미국 국채", "금리차",
    ]
    assert page.rate_rows["USD_JPY"].value.text() == "값 없음"
    assert "평균 비교 없음" == page.rate_rows["USD_JPY"].change.text()
    assert "DEXJPUS · JPY per one USD · 역수/100단위 변환 없음" in page.rate_rows["USD_JPY"].toolTip()
    assert page.rate_rows["UST2"].change.text() == "▲ +2.0bp"
    assert page.rate_rows["UST10"].change.text() == "▼ -1.0bp"
    assert page.rate_rows["UST10_2_SPREAD"].value.text() == "0.53%p"
    assert page.rate_rows["UST10_2_SPREAD"].change.text() == "▼ -3.0bp"
    assert page.rate_rows["KR_TREASURY"].value.text() == "현재 표시 불가"
    assert page.rate_rows["KR_TREASURY"].change.text() == "변동 N/A"
    assert page.rate_rows["KR_TREASURY"].meta.text() == "상세는 Data Status"
    assert page.horizontalScrollBar().maximum() == 0

    page.resize(2560, 1400)
    app.processEvents()
    assert page.horizontalScrollBar().maximum() == 0
    assert all(group.height() == 43 for group in page.rate_groups.values())
    assert page.rate_groups["SPREAD"].geometry().bottom() <= 230
    page.close()
    app.processEvents()


def test_dashboard_rate_group_fail_closed_clears_old_fx_and_yield_numbers():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    current = _metric(
        "USD_JPY", 159.21, freshness="CURRENT",
        state=DashboardDisplayState.VALUE, unit="JPY per USD", change=-0.14,
    )
    page.render({"dashboard_metrics": {"USD_JPY": current}})
    assert page.rate_rows["USD_JPY"].value.text() == "159.21"

    stale = _metric(
        "USD_JPY", None, freshness="STALE",
        state=DashboardDisplayState.REFRESH_REQUIRED, unit="JPY per USD",
    )
    page.render({"dashboard_metrics": {"USD_JPY": stale}})

    assert "159.21" not in page.rate_rows["USD_JPY"].value.text()
    assert page.rate_rows["USD_JPY"].change.text() == "변동 N/A"
    assert not page.rate_rows["USD_JPY"].spark.isVisible()
    page.close()
    app.processEvents()


def test_dashboard_rate_row_does_not_recover_denied_value_from_daily_series():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    denied = replace(
        _metric(
            "USD_JPY", None, freshness="UNKNOWN",
            state=DashboardDisplayState.UNAVAILABLE, unit="JPY per USD",
        ),
        route="NORMALIZED_DAILY",
    )
    series = DashboardSeriesView(
        denied,
        pd.DataFrame({
            "date": pd.to_datetime(["2026-08-13", "2026-08-14"]),
            "value": [159.35, 159.21],
        }),
    )

    page.render({
        "dashboard_metrics": {"USD_JPY": denied},
        "dashboard_series": {"USD_JPY": series},
    })

    assert "159.21" not in page.rate_rows["USD_JPY"].value.text()
    assert page.rate_rows["USD_JPY"].change.text() == "변동 N/A"
    assert not page.rate_rows["USD_JPY"].spark.isVisible()
    page.close()
    app.processEvents()


def test_dashboard_nq_daily_view_is_bounded_without_losing_retained_series():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    metric = _metric(
        "NQ_FUTURES", 30_000.0, freshness="CURRENT",
        state=DashboardDisplayState.VALUE,
        dataset_id="global_commodity_futures_daily",
    )
    dates = pd.bdate_range("2025-08-25", periods=252)
    frame = pd.DataFrame({
        "date": dates,
        "open": range(30_000, 30_252),
        "high": range(30_010, 30_262),
        "low": range(29_990, 30_242),
        "close": range(30_005, 30_257),
        "value": range(30_005, 30_257),
    })
    series = DashboardSeriesView(metric, frame)

    page.render({
        "dashboard_metrics": {"NQ_FUTURES": metric},
        "dashboard_series": {"NQ_FUTURES": series},
    })

    assert page.market_cards["NQ_FUTURES"].title.text() == "Nasdaq 100"
    assert page.market_cards["NQ_FUTURES"].badge.text() == "일봉 완료"
    assert page.market_cards["NQ_FUTURES"].meta.text() == "확정·08-18"
    assert page.market_cards["NQ_FUTURES"].meta.accessibleName() == "확정 · Yahoo 기준 2026-08-18"
    assert not page.market_cards["NQ_FUTURES"].badge.isVisible()
    assert page.nq_detail.text() == "완료 일봉 · 연속선물 · 60분 현재값과 분리"
    assert "60분 현재 관측과 거래일을 혼합하지 않습니다" in page.nq_detail.toolTip()
    assert "Data Status" in page.nq_detail.toolTip()

    daily_item = next(item for item in page.nq_chart.getPlotItem().items if hasattr(item, "_bars"))
    assert len(daily_item._bars) == 120
    assert "최근 120개 표시 · 전체 252개 보유" in page.nq_state.text()
    assert "완료 일봉 기준 2026-08-11" in page.nq_state.text()
    assert "+0.00%" in page.nq_state.text()

    page.nq_interval.setCurrentText("주봉")
    weekly_item = next(item for item in page.nq_chart.getPlotItem().items if hasattr(item, "_bars"))
    assert len(weekly_item._bars) == len(_aggregate_ohlc(frame, "주봉"))
    assert "진행 중 집계 · 2026-08-11까지" in page.nq_state.text()
    assert "진행 중 집계 · 2026-08-11까지" in page.nq_detail.text()
    page.nq_interval.setCurrentText("월봉")
    monthly_item = next(item for item in page.nq_chart.getPlotItem().items if hasattr(item, "_bars"))
    assert len(monthly_item._bars) == len(_aggregate_ohlc(frame, "월봉"))
    assert "진행 중 집계 · 2026-08-11까지" in page.nq_state.text()
    assert "진행 중 집계 · 2026-08-11까지" in page.nq_detail.text()
    page.close()
    app.processEvents()


def test_dashboard_v2_explains_short_and_medium_market_state():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render({"dashboard_metrics": {
        "KOSPI": _metric("KOSPI", 3000.0, freshness="CURRENT", state=DashboardDisplayState.VALUE),
    }})
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=70),
        "open": range(70), "high": range(1, 71), "low": range(-1, 69),
        "close": range(70), "volume": range(100, 170),
        "ma60": range(10, 80), "rsi14": [45.82] * 70,
        "disparity60": [86.14] * 70,
    })

    page.render_market_chart(frame)

    assert page.kospi_chart_title.text() == "KOSPI 캔들 차트"
    assert page.gauges["RSI14"].interpretation.text() == "중립"
    assert page.gauges["RSI14"].value.text() == "45.8 · 30 / 70"
    assert page.gauges["DISPARITY60"].interpretation.text() == "중기 추세 약세"
    assert page.gauges["DISPARITY60"].value.text() == "60일선 대비 -13.9%"
    signed = page.gauges["DISPARITY60"].bar
    assert round(signed.value(), 2) == -13.86
    assert signed.direction() == "left"
    assert tuple(round(value, 3) for value in signed.fill_ratios()) == (0.154, 0.5)
    assert page.momentum_summary.text() == (
        "과매도 강도 산출 보류 · RSI·MA60·변동성 근거가 모두 필요합니다"
    )
    assert page.temperature_coverage.text() == "근거 2/3 · RSI · MA60 · 미반영 변동성"

    page.gauges["DISPARITY60"].set_gauge(
        8.0, minimum=-20, maximum=20, text="60일선 대비 +8.0%",
        interpretation="중기 추세 강세", tone="positive",
    )
    assert signed.direction() == "right"
    assert signed.fill_ratios() == (0.5, 0.7)
    page.gauges["DISPARITY60"].set_gauge(
        0.0, minimum=-20, maximum=20, text="60일선 대비 +0.0%",
    )
    assert signed.direction() == "center"
    assert signed.fill_ratios() == (0.5, 0.5)
    page.close()
    app.processEvents()


def test_dashboard_v2_indicator_categories_are_explicit_and_non_advisory():
    assert DashboardPage._rsi_state(29.9)[0] == "과매도"
    assert DashboardPage._rsi_state(30.0)[0] == "약세"
    assert DashboardPage._rsi_state(45.0)[0] == "중립"
    assert DashboardPage._rsi_state(55.1)[0] == "강세"
    assert DashboardPage._rsi_state(70.1)[0] == "과매수"
    assert [DashboardPage._volatility_state(value)[0] for value in (10, 30, 60, 90)] == [
        "낮음", "보통", "높음", "매우 높음",
    ]


def test_dashboard_vix_temperature_prefers_yahoo_completed_bar_over_fred_headline():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    official = _metric(
        "VIX", 15.2, freshness="EXPECTED_LAG",
        state=DashboardDisplayState.VALUE, as_of="2026-08-25",
        dataset_id="fred_vix_daily",
    )
    intraday = replace(
        _metric(
            "VIX_INTRADAY_15M", 20.0, freshness="CURRENT_COMPLETED_15M",
            state=DashboardDisplayState.VALUE,
            as_of="2026-08-27 03:00 KST",
            dataset_id="market_price_15m_current",
        ),
        series_id="^VIX",
        unit="index points",
        source=(
            "Yahoo ^VIX completed provider-native 15m current projection; "
            "not FRED VIXCLS"
        ),
        route="yahoo-market-current:CBOE:VIX",
        pit_status="PIT_BLOCKED",
        automation_policy="EVERY_30_MIN_CURRENT_ONLY",
        automation_enabled=True,
        completed_bar=True,
        delay_status="DELAYED_COMPLETED_BAR",
        source_timestamp="2026-08-26T18:00:00+00:00",
        retrieved_at_utc="2026-08-26T18:02:00+00:00",
        timestamp_basis="PROVIDER_TIMESTAMP",
    )
    official = replace(
        official, unit="index points", source="fred_vixcls",
        route="NORMALIZED_DAILY",
    )
    history = DashboardSeriesView(
        official,
        pd.DataFrame({
            "date": pd.bdate_range("2025-09-01", periods=250),
            "value": np.arange(1.0, 251.0),
        }),
    )

    page.render({
        "dashboard_metrics": {
            "VIX": official, "VIX_INTRADAY_15M": intraday,
        },
        "dashboard_series": {"VIX": history},
    })

    assert page._temperature_values["VIX"] == 8.0
    assert page.gauges["VIX"].label.text() == (
        "공포 · VIX 현재 / FRED 일봉 250개 중 위치"
    )
    assert page.gauges["VIX"].value.text() == "8% · 20.00"
    assert page.gauges["VIX"].detail.text() == (
        "Yahoo 15분 완료·지연 · 08-27 03:00 KST"
    )
    assert "지연 가능" in page.gauges["VIX"].toolTip()
    assert "백테스트 입력에 혼합하지 않습니다" in page.gauges["VIX"].toolTip()
    page.close()
    app.processEvents()


def test_dashboard_vix_temperature_keeps_fred_daily_fallback_without_current_quote():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    official = _metric(
        "VIX", 20.0, freshness="CURRENT",
        state=DashboardDisplayState.VALUE, dataset_id="fred_vix_daily",
    )
    official = replace(
        official, unit="index points", source="fred_vixcls",
        route="NORMALIZED_DAILY",
    )
    history = DashboardSeriesView(
        official,
        pd.DataFrame({
            "date": pd.bdate_range("2026-01-01", periods=20),
            "value": np.arange(1.0, 21.0),
        }),
    )

    page.render({
        "dashboard_metrics": {"VIX": official},
        "dashboard_series": {"VIX": history},
    })

    assert page._temperature_values["VIX"] == 100.0
    assert page.gauges["VIX"].label.text() == (
        "공포 · VIX FRED 일봉 20개 중 위치"
    )
    assert page.gauges["VIX"].value.text() == "100% · 20.00"
    assert page.gauges["VIX"].detail.text() == "FRED VIXCLS 완료 일봉"
    page.close()
    app.processEvents()


def test_dashboard_vix_temperature_rejects_forged_intraday_identity():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    official = replace(
        _metric(
            "VIX", 20.0, freshness="CURRENT",
            state=DashboardDisplayState.VALUE, dataset_id="fred_vix_daily",
            unit="index points",
        ),
        source="fred_vixcls", route="NORMALIZED_DAILY",
    )
    forged = replace(
        official, dataset_id="fred_vix_daily", series_id="NOT_VIX",
        completed_bar=True, value=99.0,
    )
    history_frame = pd.DataFrame({
        "date": pd.bdate_range("2026-01-01", periods=20),
        "value": np.arange(1.0, 21.0),
    })
    history = DashboardSeriesView(official, history_frame.copy())

    page.render({
        "dashboard_metrics": {
            "VIX": official, "VIX_INTRADAY_15M": forged,
        },
        "dashboard_series": {"VIX": history},
    })

    assert page._temperature_values["VIX"] == 100.0
    assert "Yahoo" not in page.gauges["VIX"].value.text()
    assert "Y15m" not in page.gauges["VIX"].value.text()
    assert "Yahoo" not in page.gauges["VIX"].detail.text()
    assert page.gauges["VIX"].label.text().endswith("FRED 일봉 20개 중 위치")
    pd.testing.assert_frame_equal(history.frame, history_frame)
    page.close()
    app.processEvents()


def test_dashboard_vix_temperature_rejects_spoofed_fred_source():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    spoofed = replace(
        _metric(
            "VIX", 20.0, freshness="CURRENT",
            state=DashboardDisplayState.VALUE, dataset_id="fred_vix_daily",
            unit="index points",
        ),
        source="UNTRUSTED NOT-FRED SUBSTITUTE", route="NORMALIZED_DAILY",
    )
    history = DashboardSeriesView(
        spoofed,
        pd.DataFrame({
            "date": pd.bdate_range("2026-01-01", periods=20),
            "value": np.arange(1.0, 21.0),
        }),
    )

    page.render({
        "dashboard_metrics": {"VIX": spoofed},
        "dashboard_series": {"VIX": history},
    })

    assert "VIX" not in page._temperature_values
    assert page.gauges["VIX"].value.text() == "표시 불가"
    assert not page.gauges["VIX"].detail.isVisible()
    page.close()
    app.processEvents()


def test_market_regime_technical_axis_clears_on_unavailable_and_empty_transition():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    current = _metric(
        "KOSPI", 3000.0, freshness="CURRENT",
        state=DashboardDisplayState.VALUE,
    )
    page.render({"dashboard_metrics": {"KOSPI": current}})
    page._temperature_values["VKOSPI"] = 75.0
    page._valuation_axis_available = True
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=70),
        "open": range(70), "high": range(1, 71), "low": range(-1, 69),
        "close": range(70), "volume": range(100, 170),
        "ma60": range(10, 80), "rsi14": [35.0] * 70,
        "disparity60": [95.0] * 70,
    })
    page.render_market_chart(frame)
    assert "시장 국면 근거 2/3" in page.market_valuation_regime_gate.text()

    page._metrics["KOSPI"] = _metric(
        "KOSPI", None, freshness="UNKNOWN",
        state=DashboardDisplayState.UNAVAILABLE,
    )
    page.render_market_chart(frame)
    assert "RSI14" not in page._temperature_values
    assert "DISPARITY60" not in page._temperature_values
    assert "시장 국면 근거 1/3 · KOSPI 밸류에이션" in (
        page.market_valuation_regime_gate.text()
    )

    page._metrics["KOSPI"] = current
    page.render_market_chart(frame)
    assert "시장 국면 근거 2/3" in page.market_valuation_regime_gate.text()
    page.render_market_chart(pd.DataFrame())
    assert "RSI14" not in page._temperature_values
    assert "DISPARITY60" not in page._temperature_values
    assert "시장 국면 근거 1/3 · KOSPI 밸류에이션" in (
        page.market_valuation_regime_gate.text()
    )
    page.close()
    app.processEvents()


def test_dashboard_oversold_strength_is_bounded_and_requires_every_axis():
    assert DashboardPage._oversold_strength(45.0, -2.0, None) is None

    neutral = DashboardPage._oversold_strength(50.0, 2.0, 40.0)
    assert neutral == (0.0, (("RSI", 0.0), ("이격", 0.0), ("변동성", 0.0)))

    extreme = DashboardPage._oversold_strength(10.0, -15.0, 100.0)
    assert extreme == (10.0, (("RSI", 4.0), ("이격", 3.0), ("변동성", 3.0)))


def test_dashboard_displays_ten_point_oversold_strength_without_double_counting_volatility():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page._temperature_values = {
        "RSI14": 20.0,
        "DISPARITY60": -10.0,
        "VIX": 100.0,
        "VKOSPI": 50.0,
    }

    page._render_market_temperature_summary()

    # VKOSPI is the Korean-market volatility axis; correlated VIX is not added again.
    assert page.momentum_summary.text().startswith("과매도 강도 6.4/10 · 과매도 후보")
    assert "RSI 3.4 · 이격 3.0 · 변동성 0.0" in page.momentum_summary.text()
    assert page.temperature_coverage.text() == "근거 3/3 · RSI · MA60 · VKOSPI"
    page.close()
    app.processEvents()


def test_dashboard_density_stays_wide_and_compacts_for_scaled_display():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    # A 1600x900 main window leaves roughly 840 logical pixels below the tab bar.
    page.resize(1600, 840)
    page.show()
    app.processEvents()

    body_height = page.body_widget.height()
    assert body_height >= 700
    assert page.kospi_panel.height() >= 470
    assert page.nq_panel.isHidden()
    assert page.market_context_tabs.height() >= 230
    assert page.kospi_panel.geometry().bottom() < page.market_context_tabs.geometry().top()
    assert page.verticalScrollBar().maximum() > 0
    assert page.horizontalScrollBar().maximum() == 0
    assert page.account_placeholder.height() <= 60
    assert page.oscillator_panel.height() >= 315
    tenth_card = page.market_cards[page._visible_market_card_ids[9]]
    assert page.top_strip.getItemPosition(page.top_strip.indexOf(tenth_card))[:2] == (0, 9)
    assert page.COMPACT_MARKET_CARD_HEIGHT == 112
    assert page.top_widget.height() == 112
    visible_side = [
        widget for widget in (
            page.oscillator_panel, page.rates_panel, page.account_placeholder,
        ) if not widget.isHidden()
    ]
    assert all(
        first.geometry().bottom() < second.geometry().top()
        for first, second in zip(visible_side, visible_side[1:])
    )
    page.resize(1100, 800)
    app.processEvents()
    assert page.horizontalScrollBar().maximum() == 0
    assert page.verticalScrollBar().maximum() > 0
    fifth_card = page.market_cards[page._visible_market_card_ids[4]]
    assert page.top_strip.getItemPosition(page.top_strip.indexOf(fifth_card))[:2] == (1, 0)
    assert page.body_layout.getItemPosition(
        page.body_layout.indexOf(page.side_widget)
    )[0] == 1
    assert page.derivatives_panel.height() > 104

    page.resize(840, 680)
    app.processEvents()
    third_card = page.market_cards[page._visible_market_card_ids[2]]
    assert page.top_strip.getItemPosition(page.top_strip.indexOf(third_card))[:2] == (1, 0)
    assert page.market_session_strip.height() == 58
    assert page.session_layout.direction() == QtWidgets.QBoxLayout.TopToBottom
    assert page.horizontalScrollBar().maximum() == 0
    assert page.verticalScrollBar().maximum() > 0

    page.resize(2560, 1400)
    app.processEvents()
    assert page.horizontalScrollBar().maximum() == 0
    assert page.verticalScrollBar().maximum() == 0
    assert page.kospi_panel.geometry().bottom() < page.market_context_tabs.geometry().top()
    page.close()
    app.processEvents()


@pytest.mark.parametrize("logical_width", [1280, 1365, 1440])
def test_dashboard_populated_market_values_fit_one_readable_row_at_common_widths(
    logical_width,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.resize(logical_width, 840)
    page.show()
    metrics = {
        identifier: _metric(
            identifier,
            123_456.78,
            change=-12_345.67,
            change_pct=-12.34,
            freshness="CURRENT",
            state=DashboardDisplayState.VALUE,
            as_of="2026-08-25",
        )
        for identifier, _label in page.TOP_METRICS
    }
    for identifier, symbol in (
        ("NQ_FUTURES", "NQ=F"),
        ("GOLD", "GC=F"),
        ("WTI", "CL=F"),
    ):
        metrics[identifier] = replace(
            metrics[identifier],
            dataset_id="global_commodity_futures_daily",
            source=f"Yahoo Finance {symbol} completed daily",
            route=f"yahoo-global-daily:{symbol}",
        )
    page.render({
        "dashboard_metrics": metrics,
        "market_card_sparklines": {
            identifier: DashboardSparklineView(
                asset=identifier,
                lane_id="TEST_COMPLETED_SESSION",
                series_id=identifier,
                frame=pd.DataFrame({
                    "date": pd.date_range(
                        "2026-08-25T00:00:00Z", periods=3, freq="30min",
                    ),
                    "value": [100.0, 101.0, 102.0],
                }),
                interval="30m",
                session_label="완료장 2026-08-25",
                session_date="2026-08-25",
                visual_window="completed bars only",
                as_of_kst="2026-08-25 09:00 KST",
                source_timestamp="2026-08-25T00:00:00+00:00",
                source="fixture",
                freshness="CURRENT",
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=None,
            )
            for identifier, _label in page.TOP_METRICS
        },
    })
    app.processEvents()

    assert page.horizontalScrollBar().maximum() == 0
    tenth_card = page.market_cards[page._visible_market_card_ids[9]]
    assert page.top_strip.getItemPosition(
        page.top_strip.indexOf(tenth_card)
    )[:2] == (0, 9)
    assert page.top_widget.height() == 112
    for identifier in page._visible_market_card_ids:
        card = page.market_cards[identifier]
        assert card.height() == 112
        for child in (card.title, card.body, card.meta, card.sparkline):
            assert child.isVisible(), (identifier, child.objectName())
            assert child.width() > 0 and child.height() > 0
            assert child.geometry().left() >= card.contentsRect().left()
            assert child.geometry().right() <= card.contentsRect().right()
            assert child.geometry().top() >= card.contentsRect().top()
            assert child.geometry().bottom() <= card.contentsRect().bottom()
        assert card.sparkline.height() >= 18
        assert (
            card.title.fontMetrics().horizontalAdvance(card.title.text())
            <= card.title.width()
        )
        assert (
            card.body.sizePolicy().horizontalPolicy()
            != QtWidgets.QSizePolicy.Policy.Ignored
        )
        assert (
            max(
                card.body.fontMetrics().horizontalAdvance(line)
                for line in card.body.text().splitlines()
            ) <= card.body.contentsRect().width()
        ), identifier
        assert card.body.height() >= (
            card.body.fontMetrics().lineSpacing()
            * len(card.body.text().splitlines())
        ), identifier
        assert card.body.font().pixelSize() >= 10
        assert card.meta.font().pixelSize() >= 10
        assert card.meta.text().startswith("확정·")
        if identifier in {"NQ_FUTURES", "GOLD", "WTI"}:
            assert card.meta.text() == "확정·08-25"
            assert card.meta.accessibleName() == "확정 · Yahoo 기준 2026-08-25"
            assert "Yahoo Finance" in card.toolTip()
            assert "as_of=2026-08-25" in card.toolTip()
        else:
            assert card.meta.accessibleName().startswith("확정 · 기준 ")
        assert (
            card.meta.fontMetrics().horizontalAdvance(card.meta.text())
            <= card.meta.contentsRect().width()
        )
        plot_bounds = card.sparkline.rect().adjusted(3, 4, -3, -4)
        assert plot_bounds.height() >= 10

    page.close()
    app.processEvents()


def test_primary_chart_pages_fit_inside_a_1600px_main_window(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(
        tmp_path,
        dashboard_preferences_path=tmp_path / "dashboard-preferences.json",
        toss_runtime_enabled=False,
    )
    window.showNormal()
    window.resize(1600, 900)
    window.show()
    QtTest.QTest.qWait(100)
    window.dashboard.render({
        "dashboard_metrics": {
            identifier: _metric(
                identifier, 123_456.78, change=-12_345.67,
                change_pct=-12.34, freshness="CURRENT",
                state=DashboardDisplayState.VALUE,
            )
            for identifier in window.dashboard.derivative_cards
        },
    })
    app.processEvents()

    assert window.dashboard.horizontalScrollBar().maximum() == 0
    assert window.dashboard.derivative_grid.minimumSize().width() <= (
        window.dashboard.derivatives_panel.contentsRect().width()
    )
    for page in (window.equity_page, window.us_etf_page):
        page._set_chart_workspace_visible(True)
    for page in (window.index_page, window.equity_page, window.us_etf_page):
        window.tabs.setCurrentWidget(page)
        app.processEvents()
        assert page.layout().minimumSize().width() <= page.contentsRect().width()
        if isinstance(page, IndividualEquityPage):
            for button in (
                page.context_watchlist_add, page.context_watchlist_open,
                page.context_watchlist_remove, page.context_watchlist_up,
                page.context_watchlist_down,
            ):
                assert button.isVisible()
                assert button.width() >= button.minimumSizeHint().width()
    for row in (
        window.index_page.controls,
        window.index_page.indicator_controls,
        window.index_page.measurement_controls,
    ):
        assert row.minimumSize().width() <= window.index_page.contentsRect().width()
    for page in (window.equity_page, window.us_etf_page):
        assert page._equity_indicator_row.minimumSize().width() <= page.contentsRect().width()
        assert page._equity_action_row.minimumSize().width() <= page.contentsRect().width()
        assert not page.indicator_panel.isHidden()
        assert not page.reload_button.isHidden()
        assert not page.comparison_toggle.isHidden()

    window.close()
    app.processEvents()


def _assert_lower_left_axis_text_inside_plot(plot: pg.PlotWidget) -> None:
    """Account for QGraphicsView's reserved viewport margin in widget pixels."""

    axis = plot.getAxis("left")
    viewport_origin = plot.viewport().mapTo(plot, QtCore.QPoint(0, 0))
    label_rect = plot.mapFromScene(
        axis.label.sceneBoundingRect(),
    ).boundingRect().translated(viewport_origin)
    assert 0 <= label_rect.left() and label_rect.right() < plot.width(), (
        plot.accessibleName(), plot.rect(), label_rect,
    )
    assert label_rect.height() <= plot.height(), (
        plot.accessibleName(), plot.rect(), label_rect,
    )
    assert plot.viewportMargins().left() >= 6
    assert axis.width() >= 80


def test_primary_chart_lower_panel_axes_fit_at_logical_1600x900():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    pages: list[QtWidgets.QWidget] = []
    try:
        dashboard = DashboardPage()
        dashboard.resize(1600, 840)
        dashboard.show()
        dashboard.render({"dashboard_metrics": {
            "KOSPI": _metric(
                "KOSPI", 3_120.0, freshness="CURRENT",
                state=DashboardDisplayState.VALUE,
            ),
        }})
        dashboard.market_indicator_panel.apply(replace(
            dashboard.market_indicator_panel.settings(),
            volume=True, rsi14_mode="Panel",
        ))
        dashboard.render_market_chart(_index_series_view().frame)
        pages.append(dashboard)

        index = IndexPage()
        index.resize(1600, 840)
        index.show()
        index.rsi.setCurrentText("Panel")
        index.render(_index_series_view())
        pages.append(index)

        equity = IndividualEquityPage()
        equity.resize(1600, 840)
        equity.show()
        equity._selected_identity = _equity_identity()
        equity._apply_indicator_settings(replace(
            equity.indicator_panel.settings(), volume=True,
            rsi14_mode="Panel",
        ))
        equity.render_series(_equity_series_view(equity._selected_identity))
        equity._set_chart_workspace_visible(True)
        pages.append(equity)

        etf = IndividualEquityPage(universe="US_ETF")
        etf.resize(1600, 840)
        etf.show()
        etf._selected_identity = _us_etf_identity("SPY")
        etf._apply_indicator_settings(replace(
            etf.indicator_panel.settings(), volume=True,
            rsi14_mode="Panel",
        ))
        etf.render_series(_equity_series_view(etf._selected_identity))
        etf._set_chart_workspace_visible(True)
        pages.append(etf)

        app.processEvents()
        for surface, volume_plot, indicator_plot in (
            ("Dashboard", dashboard.market_volume, dashboard.market_indicator),
            ("Index Graph", index.volume, index.indicator),
            ("005930", equity.volume, equity.indicator),
            ("SPY", etf.volume, etf.indicator),
        ):
            assert not volume_plot.isHidden(), surface
            assert not indicator_plot.isHidden(), surface
            _assert_lower_left_axis_text_inside_plot(volume_plot)
            _assert_lower_left_axis_text_inside_plot(indicator_plot)
    finally:
        for page in pages:
            page.close()
        app.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-native Qt diagnostic")
def test_windows_full_page_navigation_uses_positive_fonts_without_qt_warning(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    messages: list[str] = []

    def capture_message(_kind, _context, message):
        messages.append(message)

    previous_handler = QtCore.qInstallMessageHandler(capture_message)
    window = None
    try:
        window = MainWindow(
            tmp_path,
            dashboard_preferences_path=tmp_path / "dashboard-preferences.json",
            toss_runtime_enabled=False,
        )
        _stub_fast_startup_local_reads(window, monkeypatch)
        window.current_observation_reload_timer.stop()
        _drain_main_window_workers(app, window, timeout=30.0)
        window.account_page.render(_synthetic_dual_currency_account_portfolio())
        window.resize(1600, 900)
        window.show()
        app.processEvents()

        for index in range(window.tabs.count()):
            tab_bar = window.tabs.tabBar()
            QtTest.QTest.mouseClick(
                tab_bar,
                QtCore.Qt.LeftButton,
                pos=tab_bar.tabRect(index).center(),
            )
            app.processEvents()
        account_index = window.tabs.indexOf(window.account_workspace_page)
        QtTest.QTest.mouseClick(
            window.tabs.tabBar(),
            QtCore.Qt.LeftButton,
            pos=window.tabs.tabBar().tabRect(account_index).center(),
        )
        source_selector = window.account_page.source_selector
        assert source_selector.font().pointSizeF() > 0
        for source_index in range(source_selector.count()):
            QtTest.QTest.mouseClick(
                source_selector,
                QtCore.Qt.LeftButton,
                pos=source_selector.rect().center(),
            )
            QtTest.QTest.keyClick(source_selector, QtCore.Qt.Key_Home)
            for _ in range(source_index):
                QtTest.QTest.keyClick(source_selector, QtCore.Qt.Key_Down)
            QtTest.QTest.keyClick(source_selector, QtCore.Qt.Key_Return)
            app.processEvents()
        for index in range(window.account_workspace_tabs.count()):
            tab_bar = window.account_workspace_tabs.tabBar()
            QtTest.QTest.mouseClick(
                tab_bar,
                QtCore.Qt.LeftButton,
                pos=tab_bar.tabRect(index).center(),
            )
            app.processEvents()

        window.account_workspace_tabs.setCurrentWidget(window.account_page)
        app.processEvents()

        account_charts = (
            window.account_page.account_charts.allocation_chart_view.chart(),
            window.account_page.account_charts.history_chart_view.chart(),
        )
        for chart in account_charts:
            assert chart.titleFont().pointSizeF() > 0
            assert chart.legend().font().pointSizeF() > 0
            for axis in chart.axes():
                assert axis.labelsFont().pointSizeF() > 0
                assert axis.titleFont().pointSizeF() > 0
            for series in chart.series():
                if isinstance(series, QtCharts.QPieSeries):
                    assert all(
                        slice_.labelFont().pointSizeF() > 0
                        for slice_ in series.slices()
                    )
        assert window.grab().save(str(tmp_path / "windows-full-page-navigation.png"))
        assert not any(
            "QFont::setPointSize" in message and "Point size <= 0" in message
            for message in messages
        ), messages
    finally:
        if window is not None:
            window.close()
            app.processEvents()
        QtCore.qInstallMessageHandler(previous_handler)


def test_dashboard_failed_refresh_clears_previous_numbers_metric_by_metric():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DashboardPage()
    page.render({"dashboard_metrics": {
        "KOSPI": _metric("KOSPI", 3000.0, freshness="CURRENT", state=DashboardDisplayState.VALUE),
    }})
    assert "3,000.00" in page.market_cards["KOSPI"].body.text()

    page.render_unavailable("schema error")

    assert "3,000.00" not in page.market_cards["KOSPI"].body.text()
    assert "확인 필요" in page.market_cards["KOSPI"].body.text()
    assert "schema error" in page.freshness.text()
    page.close()
    app.processEvents()


def test_main_window_dashboard_snapshot_failure_clears_data_status_decisions(
    tmp_path, monkeypatch,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path)
    window.data_status_page.render_current_sources({
        "KOSPI": CurrentObservationCoverageView(
            coverage_id="KOSPI", label="KOSPI", value=3000.0,
            unit="index points", provider="KB_SECURITIES",
            route="kbsec:ivu-current:XKRX:KOSPI", interval="snapshot",
            as_of="2026-08-21 14:30 KST",
            retrieved_at_utc="2026-08-21T05:30:00+00:00",
            freshness="CURRENT_RETRIEVAL_TIME", finality="PROVISIONAL",
            display_state=DashboardDisplayState.VALUE,
            unavailable_reason="RETRIEVAL_TIMESTAMP_ACCEPTED",
            timestamp_basis="RETRIEVAL_TIMESTAMP",
        ),
    }, as_of_utc="2026-08-21T05:30:00+00:00")
    window.data_status_page.render_dashboard_decisions({
        "dashboard_metrics": {
            "NQ_FUTURES": _metric(
                "NQ_FUTURES", 30_000.0, freshness="CURRENT",
                state=DashboardDisplayState.VALUE,
            ),
        },
    })
    assert window.data_status_page.current_source_table.rowCount() == 1
    assert any(
        window.data_status_page.dashboard_decision_table.item(row, 1).text()
        == "ACCEPT"
        for row in range(window.data_status_page.dashboard_decision_table.rowCount())
    )

    health_view = object()
    rendered_health: list[object] = []

    def fail_snapshot(_session):
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(window.service, "snapshot", fail_snapshot)
    monkeypatch.setattr(window.health_artifact_service, "load", lambda: health_view)
    monkeypatch.setattr(window.data_status_page, "render_report", rendered_health.append)

    window.refresh_dashboard()
    _drain_main_window_workers(app, window)

    assert window.data_status_page.current_source_table.rowCount() == 0
    decision_text = {
        window.data_status_page.dashboard_decision_table.item(row, column).text()
        for row in range(window.data_status_page.dashboard_decision_table.rowCount())
        for column in range(window.data_status_page.dashboard_decision_table.columnCount())
    }
    assert "ACCEPT" not in decision_text
    assert "fixture source / FIXTURE" not in decision_text
    assert "UNKNOWN" in decision_text
    assert rendered_health == [health_view]
    window.close()
    app.processEvents()


def test_data_status_shell_is_read_only_and_account_page_is_registered(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DataStatusPage()
    page.render({
        "overall": "DEGRADED", "current": 2, "expected_lag": 1,
        "stale": 3, "failed": 0, "operational_blocked": 4,
        "predictive_blocked": 5, "research_only": 1,
        "source": "DailyHealthReport",
    })
    assert "오래됨 3" in page.overall.body.text()
    assert "운영 차단 4" in page.overall.body.text()
    assert "최신 확정 2" in page.freshness.body.text()
    assert "발행 대기 1" in page.eligibility.body.text()
    assert "공급자 일정상 정상" in page.eligibility.body.text()
    assert "로컬 보존 데이터 · 읽기 전용" in page.boundary.body.text()

    health_directory = tmp_path / "artifacts/daily_health"
    health_directory.mkdir(parents=True)
    window = MainWindow(tmp_path)
    _stub_fast_startup_local_reads(window)
    _drain_main_window_workers(app, window)
    tabs = window.centralWidget()
    assert isinstance(tabs, QtWidgets.QTabWidget)
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Dashboard", "Index Graph", "종목 차트", "미국 ETF", "Research Workspace", "관심종목",
        "Data Status", "계좌·순자산", "Backtest",
    ]
    assert tabs.indexOf(window.account_workspace_page) >= 0
    assert tabs.indexOf(window.account_page) == -1
    assert [
        window.account_workspace_tabs.tabText(index)
        for index in range(window.account_workspace_tabs.count())
    ] == ["계좌·보유", "순자산·증감"]
    window.account_workspace_tabs.setCurrentWidget(window.net_worth_page)
    tabs.setCurrentWidget(window.dashboard)
    window.dashboard.account_placeholder.open_button.click()
    assert tabs.currentWidget() is window.account_workspace_page
    assert window.account_workspace_tabs.currentWidget() is window.account_page
    assert window.service.us_etf is not window.service.equity
    assert "SPY" not in [
        window.index_page.index.itemText(index)
        for index in range(window.index_page.index.count())
    ]
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))
    assert window.local_reload_timer.isSingleShot()
    assert window.local_reload_timer.interval() == 900
    assert not window.current_observation_reload_timer.isSingleShot()
    assert window.current_observation_reload_timer.interval() == 30 * 60 * 1000
    assert window.current_observation_reload_timer.isActive()
    assert set(window.local_data_watcher.directories()) == {
        str(health_directory), str(tmp_path),
    }
    page.close()
    window.close()
    app.processEvents()
    assert not window.isVisible()


def test_data_status_issue_cards_search_reset_and_collapsed_drilldowns():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DataStatusPage()
    page.resize(1600, 900)
    page.show()
    rows = (
        HealthDatasetRow(
            "kr_equity_price_daily", "PRIMARY", "DAILY", "2026-08-13",
            "2026-08-24", "STALE", "READY", "N/A", "PIT_LIMITED",
            "SCHEDULED / ENABLED", "DataGoKr exact price", "PASS",
        ),
        HealthDatasetRow(
            "fred_vix_daily", "PRIMARY", "DAILY", "2026-08-24",
            "2026-08-24", "CURRENT", "READY", "N/A", "PIT_LIMITED",
            "SCHEDULED / ENABLED", "FRED unique-source", "PASS",
        ),
        HealthDatasetRow(
            "kr_short_selling_trading_daily", "PRIMARY", "DAILY", "N/A",
            "2026-08-24", "UNKNOWN", "BLOCKED", "exact permission gate",
            "PIT_BLOCKED", "MANUAL / DISABLED", "KRX", "NOT_PROBED",
        ),
        HealthDatasetRow(
            "kr_index_daily", "PRIMARY", "DAILY", "2026-08-23",
            "2026-08-24", "EXPECTED_LAG", "READY", "N/A", "PIT_LIMITED",
            "SCHEDULED / ENABLED", "KRX", "PASS",
        ),
    )
    original = rows
    page.render_report(HealthArtifactView("READY", "fixture health", rows))

    assert page.current_source_table.isHidden()
    assert page.dashboard_decision_table.isHidden()
    page.current_group.setChecked(True)
    page.decision_group.setChecked(True)
    assert not page.current_source_table.isHidden()
    assert not page.dashboard_decision_table.isHidden()

    QtTest.QTest.mouseClick(page.freshness, QtCore.Qt.LeftButton)
    assert page.status_filter.currentData() == "CURRENT"
    assert page.table.rowCount() == 1
    assert "VIX" in page.table.item(0, 0).text()

    page.boundary.setFocus()
    QtTest.QTest.keyClick(page.boundary, QtCore.Qt.Key_Space)
    assert page.status_filter.currentData() == "ALL"
    assert page.table.rowCount() == 4

    page.text_filter.setText("unique-SOURCE")
    assert page.table.rowCount() == 1
    assert "VIX" in page.table.item(0, 0).text()
    page.text_filter.setText("exact permission gate")
    assert page.table.rowCount() == 1
    assert "공매도" in page.table.item(0, 0).text()
    page.text_filter.setText("does-not-exist")
    assert page.table.rowCount() == 0
    assert "해당하는 데이터가 없습니다" in page.detail_text.text()

    QtTest.QTest.mouseClick(page.reset_filters_button, QtCore.Qt.LeftButton)
    assert page.status_filter.currentData() == "ISSUES"
    assert page.area_filter.currentText() == "전체 영역"
    assert page.text_filter.text() == ""
    assert page.table.rowCount() == 2
    assert page._report_rows == original
    assert page.horizontalScrollBar().maximum() == 0
    page.close()
    app.processEvents()


def test_health_watcher_attaches_when_directory_appears_after_startup(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window)
    health_directory = tmp_path / "artifacts/daily_health"
    reloads = []
    window.local_reload_timer.timeout.connect(lambda: reloads.append(True))

    assert str(health_directory) not in window.local_data_watcher.directories()
    assert str(tmp_path) in window.local_data_watcher.directories()
    health_directory.mkdir(parents=True)
    (health_directory / "health.json").write_text("{}", encoding="utf-8")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not reloads:
        app.processEvents()
        QtTest.QTest.qWait(10)

    assert set(window.local_data_watcher.directories()) == {
        str(health_directory), str(tmp_path),
    }
    assert reloads == [True]
    _drain_local_read_workers(app, window)
    window.close()
    app.processEvents()
    assert window.local_data_watcher.directories() == []
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_current_projection_watcher_debounces_atomic_replacements_without_acquisition(
    tmp_path,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    health_directory = tmp_path / "artifacts/daily_health"
    health_directory.mkdir(parents=True)
    current_directory = (
        tmp_path / "data/state/current_observations/global60m_current"
    )
    acquisition_factory_calls: list[bool] = []
    window = MainWindow(
        tmp_path,
        toss_runtime_enabled=False,
        current_observation_runner_factory=lambda: (
            acquisition_factory_calls.append(True) or None
        ),
    )
    _stub_fast_startup_local_reads(window)
    _drain_local_read_workers(app, window)
    for _ in range(20):
        app.processEvents()
        if not window._local_dashboard_reload_queued:
            break
        QtTest.QTest.qWait(5)
    acquisition_factory_calls.clear()
    window.local_reload_timer.stop()
    projection_now = datetime(2026, 8, 25, 17, 2, tzinfo=timezone.utc)
    observed_values: list[float | None] = []
    window.refresh_dashboard = lambda _session="U": observed_values.append(
        window.service.current_observation_coverage(now_utc=projection_now)[
            "USD_KRW_60M"
        ].value
    )
    assert set(window.local_data_watcher.directories()) == {
        str(health_directory), str(tmp_path),
    }

    projection_path = current_directory / "usd_krw_60m.json"

    def replace_projection(payload: dict[str, object]) -> None:
        temporary = current_directory / "usd_krw_60m.json.tmp"
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(projection_path)

    def payload(value: float) -> dict[str, object]:
        return {
            "schema_version": 1,
            "observations": [{
                "route_id": "yahoo-market-current:GLOBAL_FX:KRW=X",
                "identity": {
                    "dataset_id": "MARKET_PRICE_CURRENT",
                    "market": "GLOBAL_FX",
                    "symbol": "KRW=X",
                },
                "interval": "30m",
                "value": value,
                "unit": "KRW per USD",
                "provider": "YAHOO",
                "upstream_provider": "YAHOO_CHART_API",
                "source_route": "YAHOO_CHART_30M:KRW=X",
                "provider_timestamp_utc": "2026-08-25T17:00:00+00:00",
                "retrieved_at_utc": "2026-08-25T17:02:00+00:00",
                "finality": "AS_RETRIEVED",
                "display_only": True,
                "pit_safe": False,
                "timestamp_basis": "PROVIDER_TIMESTAMP",
            }],
            "circuits": {},
            "decisions": {},
        }

    current_directory.mkdir(parents=True)
    replace_projection(payload(1383.13))
    deadline = time.monotonic() + 4.0
    while len(observed_values) < 1 and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(10)
    QtTest.QTest.qWait(1000)
    app.processEvents()

    assert observed_values == [pytest.approx(1383.13)]
    assert acquisition_factory_calls == []
    assert set(window.local_data_watcher.directories()) == {
        str(health_directory), str(current_directory),
    }

    replace_projection(payload(1383.20))
    replace_projection(payload(1383.30))
    replace_projection({"schema_version": 1, "observations": [{"value": "bad"}]})
    deadline = time.monotonic() + 4.0
    while len(observed_values) < 2 and time.monotonic() < deadline:
        app.processEvents()
        QtTest.QTest.qWait(10)
    QtTest.QTest.qWait(1000)
    app.processEvents()

    assert observed_values == [pytest.approx(1383.13), None]
    assert acquisition_factory_calls == []
    assert str(current_directory) in window.local_data_watcher.directories()
    assert window._current_observation_thread is None
    window.close()
    app.processEvents()
    assert not window.local_reload_timer.isActive()
    assert not window.current_observation_reload_timer.isActive()
    assert window.local_data_watcher.directories() == []
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_local_projection_watcher_rejects_external_paths_and_tolerates_watch_errors(
    tmp_path,
):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(tmp_path, toss_runtime_enabled=False)
    _stub_fast_startup_local_reads(window)
    _drain_local_read_workers(app, window)

    assert window._closest_local_watch_path(tmp_path.parent) is None
    real_watcher = window.local_data_watcher

    class BrokenWatcher:
        @staticmethod
        def directories():
            return []

        @staticmethod
        def addPath(_path):
            raise OSError("synthetic watch failure")

        @staticmethod
        def removePath(_path):
            raise OSError("synthetic watch failure")

    window.local_data_watcher = BrokenWatcher()
    window._refresh_local_watch_paths()
    window.local_data_watcher = real_watcher

    window.close()
    app.processEvents()
    assert real_watcher.directories() == []
    assert not any(thread.isRunning() for thread in window.findChildren(QtCore.QThread))


def test_data_status_owns_current_source_time_basis_and_session_detail():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DataStatusPage()
    page.render_current_sources({
        "KOSPI": CurrentObservationCoverageView(
            coverage_id="KOSPI", label="KOSPI", value=3000.0,
            unit="index points", provider="KB_SECURITIES",
            route="kbsec:ivu-current:XKRX:KOSPI", interval="snapshot",
            as_of="2026-08-21 14:30 KST",
            retrieved_at_utc="2026-08-21T05:30:00+00:00",
            freshness="CURRENT_RETRIEVAL_TIME", finality="PROVISIONAL",
            display_state=DashboardDisplayState.VALUE,
            unavailable_reason="RETRIEVAL_TIMESTAMP_ACCEPTED",
            timestamp_basis="RETRIEVAL_TIMESTAMP",
        ),
        "SOXX": CurrentObservationCoverageView(
            coverage_id="SOXX", label="SOXX", value=529.0,
            unit="USD per share", provider="NASDAQ_OFFICIAL",
            route="nasdaq-soxx-info-api:NASDAQ:SOXX", interval="snapshot",
            as_of="2026-08-21 10:01 EDT",
            retrieved_at_utc="2026-08-21T14:02:00+00:00",
            freshness="CURRENT", finality="PROVISIONAL",
            display_state=DashboardDisplayState.VALUE,
            unavailable_reason=None,
            provider_timestamp_utc="2026-08-21T14:01:00+00:00",
        ),
    }, as_of_utc="2026-08-21T14:05:00+00:00")

    assert page.current_source_table.rowCount() == 2
    rows = {
        page.current_source_table.item(row, 0).text(): [
            page.current_source_table.item(row, column).text()
            for column in range(page.current_source_table.columnCount())
        ]
        for row in range(page.current_source_table.rowCount())
    }
    assert rows["KOSPI"][1:] == [
        "ACCEPT", "2026-08-21T05:30:00+00:00", "KB_SECURITIES", "조회시각",
        "KRX 장마감 09:00~15:30 · NXT 장마감 08:00~20:00", "KEEP",
    ]
    assert rows["SOXX"][1:] == [
        "ACCEPT", "2026-08-21T14:01:00+00:00", "NASDAQ_OFFICIAL", "제공시각",
        "미국 정규장 22:30~05:00 KST", "KEEP",
    ]
    assert "timestamp_basis=RETRIEVAL_TIMESTAMP" in page.current_source_table.item(0, 0).toolTip()
    page.close()
    app.processEvents()


def test_data_status_exposes_agent_decisions_and_dashboard_hides_operator_only_blocks():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DataStatusPage()
    daily_metric = _metric(
        "NQ_FUTURES", 30_000.0, freshness="CURRENT",
        state=DashboardDisplayState.VALUE, as_of="2026-08-22 05:00 KST",
    )
    daily_frame = pd.DataFrame({
        "date": pd.to_datetime(["2026-08-19", "2026-08-20"]),
        "open": [29_000.0, 29_100.0], "high": [29_200.0, 29_300.0],
        "low": [28_900.0, 29_000.0], "close": [29_100.0, 29_200.0],
    })
    stale = _metric(
        "VOLUME_PCR", None, freshness="STALE",
        state=DashboardDisplayState.REFRESH_REQUIRED, as_of="2026-08-19",
        automation_enabled=True, automation_policy="SCHEDULED",
    )
    manual_wall = _metric(
        "CALL_WALL", None, freshness="STALE_OR_MISSING",
        state=DashboardDisplayState.UNAVAILABLE, as_of="2026-08-19",
        automation_enabled=False, automation_policy="MANUAL_BOUNDED",
    )
    page.render_dashboard_decisions({
        "dashboard_metrics": {
            "NQ_FUTURES": daily_metric, "VOLUME_PCR": stale,
            "CALL_WALL": manual_wall,
        },
        "dashboard_series": {"NQ_FUTURES": DashboardSeriesView(daily_metric, daily_frame)},
    })
    rows = {
        page.dashboard_decision_table.item(row, 0).text(): [
            page.dashboard_decision_table.item(row, column).text()
            for column in range(page.dashboard_decision_table.columnCount())
        ]
        for row in range(page.dashboard_decision_table.rowCount())
    }
    assert rows["NQ=F 일봉 차트"][1:] == [
        "ACCEPT_DAILY_ONLY", "2026-08-20", "retained daily OHLC",
        "완료 일봉 · 연속선물", "KEEP_SEPARATE_FROM_60M",
    ]
    assert rows["KOSPI200 거래량 P/C"][1] == "STALE"
    assert rows["KOSPI200 거래량 P/C"][-1] == "RUN_AUTHORIZED_LANE"
    assert rows["Call 최대 OI"][1] == "UNAVAILABLE"
    assert rows["Call 최대 OI"][-1] == "VERIFY_SOURCE_OR_CONTRACT"
    defensive_manual_refresh = _metric(
        "PUT_WALL", None, freshness="STALE_OR_MISSING",
        state=DashboardDisplayState.REFRESH_REQUIRED,
        automation_enabled=False, automation_policy="MANUAL_BOUNDED",
    )
    assert page._metric_decision(defensive_manual_refresh) == (
        "UNAVAILABLE", "VERIFY_SOURCE_OR_CONTRACT",
    )
    assert rows["VIX 선물 · CFE VX"][1] == "BLOCKED_IDENTITY"
    assert rows["미국 옵션 P/C"][1] == "BLOCKED_ENTITLEMENT"

    dashboard = DashboardPage()
    dashboard.render({"dashboard_metrics": {"CALL_WALL": manual_wall}})
    wall_card = dashboard.derivative_cards["WALL"]
    assert wall_card.body.text() == "현재 표시 불가"
    assert wall_card.badge.text() == "수동 확인"
    assert "갱신 필요" not in wall_card.body.text()
    assert "갱신 필요" not in wall_card.badge.text()
    assert "2026-08-19" in wall_card.toolTip()
    assert dashboard.rate_groups["FX"].isHidden() is False
    assert dashboard.rate_groups["US"].isHidden() is False
    assert dashboard.rate_groups["KR"].isHidden() is False
    assert dashboard.rate_rows["KR_TREASURY"].value.text() == "현재 표시 불가"
    assert "publication/finality" in dashboard.rate_rows["KR_TREASURY"].toolTip()
    assert dashboard.rate_groups["SPREAD"].isHidden()
    assert dashboard.derivative_cards["VIX_FUTURES"].isHidden()
    assert dashboard.derivative_cards["US_OPTION_PCR"].isHidden()
    page.close()
    dashboard.close()
    app.processEvents()
