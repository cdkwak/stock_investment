from __future__ import annotations

from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

from stock_data.gui.main_window import MainWindow, ResearchWorkspacePage
from stock_data.gui.research_workspace_preferences import (
    DEFAULT_PREFERENCES,
    LocalResearchWorkspacePreferencesStore,
)
from stock_research.candidate_discovery import (
    CandidateAxisEvidence,
    StockCandidateEvidence,
    build_unavailable_candidate_view,
    discover_stock_research_candidates,
)
from stock_research.exploratory_scanner import (
    EXPLORATORY_SCANNER_VERSION,
    ExploratoryCandidateView,
    ExploratoryStockCandidate,
)


def axis(role: str):
    binding = {
        "oversold": (
            "kr_equity_adjusted_price_daily", "stock-oversold-axis/v1",
            "stock-oversold-definition/v1",
        ),
        "earnings": (
            "kr_forward_earnings_vintage", "forward-earnings-revision-axis/v1",
            "forward-earnings-revision-definition/v1",
        ),
        "relative_value": (
            "kr_stock_relative_value_daily", "stock-relative-value-axis/v1",
            "stock-relative-value-definition/v1",
        ),
    }[role]
    return CandidateAxisEvidence(
        evidence_id=f"{role}-005930",
        state="MATCH",
        reason_code=None,
        source_dataset=binding[0],
        source_contract=binding[1],
        source_version="1",
        input_digest="a" * 64,
        observation_date="2026-08-26",
        provider_published_at_utc="2026-08-26T16:00:00+09:00",
        retrieved_at_utc="2026-08-26T16:01:00+09:00",
        available_at_utc="2026-08-26T16:01:00+09:00",
        usable_from="2026-08-27T09:00:00+09:00",
        pit_status="PIT_SAFE_AS_OF_DECISION",
        freshness_state="CURRENT_AT_DECISION",
        definition_id=binding[2],
        unit="typed_state",
    )


def complete_view():
    row = StockCandidateEvidence(
        symbol="005930",
        name="삼성전자",
        market="KOSPI",
        isin="KR7005930003",
        security_type="COMMON_STOCK",
        decision_at="2026-08-27T09:05:00+09:00",
        decision_session="2026-08-27",
        universe_date="2026-08-26",
        universe_dataset="kr_equity_canonical_universe_daily",
        universe_version="v1",
        universe_digest="b" * 64,
        universe_pit_status="PIT_SAFE_AS_OF_DECISION",
        oversold=axis("oversold"),
        earnings_revision=axis("earnings"),
        relative_value=axis("relative_value"),
    )
    return discover_stock_research_candidates((row,))


def test_research_workspace_starts_with_nonblocking_partial_axis_loading(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = LocalResearchWorkspacePreferencesStore(tmp_path / "preferences.json")
    page = ResearchWorkspacePage(store, DEFAULT_PREFERENCES)
    page.show()
    app.processEvents()

    assert "기술 축 불러오는 중" in page.candidate_axis_status.text()
    assert "실적 축 N/A" in page.candidate_axis_status.text()
    assert page.candidate_table.rowCount() == 0
    assert page.candidate_scan_button.isEnabled() is False
    page.close()


def test_practical_partial_axis_view_shows_technical_rows_with_other_axes_na(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = LocalResearchWorkspacePreferencesStore(tmp_path / "preferences.json")
    page = ResearchWorkspacePage(store, DEFAULT_PREFERENCES)
    view = ExploratoryCandidateView(
        contract_version=EXPLORATORY_SCANNER_VERSION,
        availability="READY",
        as_of="2026-08-25",
        scanned_instruments=2500,
        eligible_instruments=200,
        candidates=(ExploratoryStockCandidate(
            symbol="005930", name="삼성전자", market="KOSPI",
            as_of="2026-08-25", close=70000.0, volume=1000000,
            rsi14=28.5, disparity60=91.2, technical_state="과매도",
            data_caution=None, valuation_state="AVAILABLE_CURRENT_TRAILING",
            per=14.0, pbr=1.27, valuation_as_of="2026-08-25",
        ),),
        criteria="RSI14 <= 30 OR close/SMA60 <= 80%",
        source_note=(
            "kr_equity_price_daily provider-native original price; current dated universe; "
            "optional exact-date KRX MDCSTAT03501 current PER/PBR observation; "
            "descriptive only; forward earnings and relative-value judgment not connected"
        ),
    )
    page.render_exploratory_candidates(view)

    assert "기술 축 사용 가능" in page.candidate_axis_status.text()
    assert "실적 상향 N/A" in page.candidate_axis_status.text()
    assert page.candidate_table.rowCount() == 1
    assert "RSI 28.5" in page.candidate_table.item(0, 1).text()
    assert page.candidate_table.item(0, 2).text() == "N/A · 미연결"
    assert page.candidate_table.item(0, 3).text() == "PER 14.00 · PBR 1.27"
    assert "부분 축 허용" in page.candidate_status.text()
    assert page.candidate_scan_button.isEnabled() is True
    requested = []
    page.candidate_symbol_requested.connect(
        lambda market, symbol: requested.append((market, symbol))
    )
    page.candidate_table.itemActivated.emit(page.candidate_table.item(0, 0))
    assert requested == [("KOSPI", "005930")]
    page.close()


def test_unavailable_transition_clears_prior_candidate_rows(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = LocalResearchWorkspacePreferencesStore(tmp_path / "preferences.json")
    page = ResearchWorkspacePage(store, DEFAULT_PREFERENCES)
    page.render_candidate_discovery(complete_view())
    assert page.candidate_table.rowCount() == 1
    assert "점수/순위/매매 추천 없음" in page.candidate_status.text()

    page.render_candidate_discovery(build_unavailable_candidate_view((
        "FORWARD_EARNINGS_RIGHTS_BLOCKED",
    )))
    assert page.candidate_table.rowCount() == 0
    assert "평가 가능 0/3" in page.candidate_axis_status.text()
    assert "결론 보류" in page.candidate_status.text()
    page.close()


def test_forged_complete_strict_view_is_cleared_before_render(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = LocalResearchWorkspacePreferencesStore(tmp_path / "preferences.json")
    page = ResearchWorkspacePage(store, DEFAULT_PREFERENCES)
    accepted = complete_view()
    forged_row = replace(accepted.candidates[0], earnings_revision_state="INVALID")
    forged = replace(accepted, evaluated_instruments=0, candidates=(forged_row,))

    page.render_candidate_discovery(forged)
    assert page.candidate_table.rowCount() == 0
    assert "계약 검증 실패" in page.candidate_status.text()
    page.close()


def test_malformed_or_nonmatching_exploratory_rows_are_cleared(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = LocalResearchWorkspacePreferencesStore(tmp_path / "preferences.json")
    page = ResearchWorkspacePage(store, DEFAULT_PREFERENCES)
    valid = ExploratoryCandidateView(
        contract_version=EXPLORATORY_SCANNER_VERSION,
        availability="READY", as_of="2026-08-25",
        scanned_instruments=1, eligible_instruments=1,
        candidates=(ExploratoryStockCandidate(
            symbol="005930", name="삼성전자", market="KOSPI",
            as_of="2026-08-25", close=70000.0, volume=1,
            rsi14=28.0, disparity60=90.0, technical_state="과매도",
            data_caution=None,
        ),),
        criteria="RSI14 <= 30 OR close/SMA60 <= 80%",
        source_note=(
            "kr_equity_price_daily provider-native original price; current dated universe; "
            "optional exact-date KRX MDCSTAT03501 current PER/PBR observation; "
            "descriptive only; forward earnings and relative-value judgment not connected"
        ),
    )
    page.render_exploratory_candidates(replace(valid, candidates=(object(),)))
    assert page.candidate_table.rowCount() == 0
    assert "검증 실패" in page.candidate_status.text()

    nonmatch = replace(valid.candidates[0], rsi14=80.0, disparity60=110.0)
    page.render_exploratory_candidates(replace(valid, candidates=(nonmatch,)))
    assert page.candidate_table.rowCount() == 0
    page.render_exploratory_candidates(replace(
        valid, scanned_instruments=0, eligible_instruments=0,
    ))
    assert page.candidate_table.rowCount() == 0
    page.render_exploratory_candidates(replace(
        valid, ranking_basis="EXPECTED_RETURN_DESC", source_note="LIVE_PROVIDER_FORECAST",
    ))
    assert page.candidate_table.rowCount() == 0
    wrong_state = replace(valid.candidates[0], technical_state="60일선 큰 폭 하회")
    page.render_exploratory_candidates(replace(valid, candidates=(wrong_state,)))
    assert page.candidate_table.rowCount() == 0
    forged_valuation = replace(
        valid.candidates[0], valuation_state="AVAILABLE_CURRENT_TRAILING",
    )
    page.render_exploratory_candidates(replace(
        valid, candidates=(forged_valuation,),
    ))
    assert page.candidate_table.rowCount() == 0
    assert page.candidate_table.accessibleName() == "현재 데이터 종목 관찰 후보"
    page.close()


def test_candidate_scan_pending_is_not_overwritten_by_general_equity_request():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    began = []
    requested = []
    fake = SimpleNamespace(
        _closing=False,
        _candidate_scan_started=False,
        _candidate_scan_pending=False,
        _equity_thread=object(),
        _equity_worker=object(),
        _equity_pending=("search", "005930"),
        research_workspace_page=SimpleNamespace(
            begin_candidate_scan=lambda: began.append(True),
        ),
        _request_equity_job=lambda *args: requested.append(args),
    )
    MainWindow.refresh_candidate_scan(fake)
    assert began == [True]
    assert fake._candidate_scan_pending is True
    assert requested == []

    resumed = []
    fake.refresh_candidate_scan = lambda: resumed.append("candidate")
    fake._schedule_pending_close_check = lambda: None
    MainWindow._equity_thread_destroyed(fake, None)
    app.processEvents()
    assert resumed == ["candidate"]
    assert fake._equity_pending == ("search", "005930")
