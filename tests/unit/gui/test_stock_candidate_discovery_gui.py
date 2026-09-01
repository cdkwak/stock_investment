from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtTest, QtWidgets

from stock_data.gui.main_window import (
    DecisionCockpitPage,
    MainWindow,
    ResearchWorkspacePage,
)
from stock_data.gui.research_workspace_preferences import (
    DEFAULT_PREFERENCES,
    LocalResearchWorkspacePreferencesStore,
)
from stock_data.gui.services import (
    EquitySearchView,
    RetainedCandidateScanService,
    candidate_recovery_view,
    decision_cockpit_view,
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
    LocalExploratoryCandidateScanner,
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
    assert not hasattr(page, "candidate_select_button")
    assert not hasattr(page, "candidate_data_status_button")
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
            begin_candidate_scan=lambda: began.append("research"),
        ),
        decision_cockpit_page=SimpleNamespace(
            begin_candidate_scan=lambda: began.append("cockpit"),
        ),
        _request_equity_job=lambda *args: requested.append(args),
    )
    MainWindow.refresh_candidate_scan(fake)
    assert began == ["research", "cockpit"]
    assert fake._candidate_scan_pending is True
    assert requested == []

    resumed = []
    fake.refresh_candidate_scan = lambda: resumed.append("candidate")
    fake._schedule_pending_close_check = lambda: None
    MainWindow._equity_thread_destroyed(fake, None)
    app.processEvents()
    assert resumed == ["candidate"]
    assert fake._equity_pending == ("search", "005930")


def exploratory_view(*, as_of: str, with_candidate: bool) -> ExploratoryCandidateView:
    candidates = (
        ExploratoryStockCandidate(
            symbol="005930", name="삼성전자", market="KOSPI",
            as_of=as_of, close=70_000.0, volume=1_000_000,
            rsi14=28.5, disparity60=91.2, technical_state="과매도",
            data_caution=None,
        ),
    ) if with_candidate else ()
    return ExploratoryCandidateView(
        contract_version=EXPLORATORY_SCANNER_VERSION,
        availability="READY",
        as_of=as_of,
        scanned_instruments=1,
        eligible_instruments=len(candidates),
        candidates=candidates,
        criteria="RSI14 <= 30 OR close/SMA60 <= 80%",
        source_note=(
            "kr_equity_price_daily provider-native original price; current dated universe; "
            "optional exact-date KRX MDCSTAT03501 current PER/PBR observation; "
            "descriptive only; forward earnings and relative-value judgment not connected"
        ),
    )


@pytest.mark.parametrize("with_candidate", [True, False])
def test_retained_candidate_scan_service_preserves_valid_and_valid_empty(
    tmp_path, with_candidate,
):
    view = exploratory_view(as_of="2026-08-25", with_candidate=with_candidate)
    service = RetainedCandidateScanService(
        tmp_path,
        scanner=SimpleNamespace(scan=lambda: view),
        now_utc=datetime.fromisoformat("2026-08-26T09:00:00+09:00"),
    )

    result = service.scan()

    assert result == view
    assert result.availability == "READY"
    assert result.unavailable_reason is None
    assert len(result.candidates) == int(with_candidate)


@pytest.mark.parametrize(
    ("scanner_reason", "expected_code", "recovery_fragment"),
    [
        (
            "LOCAL_PRICE_DATASET_MISSING",
            "LOCAL_CANDIDATE_INPUT_MISSING",
            "kr_equity_price_daily",
        ),
        (
            "LOCAL_CANDIDATE_READ_FAILED",
            "LOCAL_CANDIDATE_INPUT_CORRUPT",
            "검증·재생성",
        ),
        (
            "LOCAL_CANDIDATE_INPUT_EMPTY",
            "LOCAL_CANDIDATE_INPUT_EMPTY",
            "최신 파티션",
        ),
    ],
)
def test_retained_candidate_scan_service_returns_typed_input_recovery(
    tmp_path, scanner_reason, expected_code, recovery_fragment,
):
    unavailable = LocalExploratoryCandidateScanner(tmp_path).unavailable(scanner_reason)
    service = RetainedCandidateScanService(
        tmp_path,
        scanner=SimpleNamespace(scan=lambda: unavailable),
        now_utc=datetime.fromisoformat("2026-08-30T09:00:00+09:00"),
    )

    result = service.scan()

    assert result.availability == "UNAVAILABLE"
    assert result.candidates == ()
    assert result.unavailable_reason.startswith(expected_code)
    assert "recovery=" in result.unavailable_reason
    assert recovery_fragment in result.unavailable_reason
    assert str(tmp_path) not in result.unavailable_reason


def test_retained_candidate_scan_service_reports_stale_dates_and_recovery(tmp_path):
    view = exploratory_view(as_of="2026-08-25", with_candidate=True)
    service = RetainedCandidateScanService(
        tmp_path,
        scanner=SimpleNamespace(scan=lambda: view),
        now_utc=datetime.fromisoformat("2026-08-30T09:00:00+09:00"),
    )

    result = service.scan()

    assert result.availability == "UNAVAILABLE"
    assert result.unavailable_reason.startswith("LOCAL_CANDIDATE_INPUT_STALE")
    assert "retained_as_of=2026-08-25" in result.unavailable_reason
    assert "expected_as_of=2026-08-27" in result.unavailable_reason
    assert "recovery=" in result.unavailable_reason


def test_candidate_worker_fallback_keeps_typed_privacy_safe_recovery(tmp_path):
    service = RetainedCandidateScanService(tmp_path)

    result = service.unavailable("LOCAL_CANDIDATE_SCAN_FAILED")

    assert result.availability == "UNAVAILABLE"
    assert result.unavailable_reason.startswith("LOCAL_CANDIDATE_INPUT_CORRUPT")
    assert "recovery=" in result.unavailable_reason
    assert "LOCAL_CANDIDATE_SCAN_FAILED" not in result.unavailable_reason
    assert str(tmp_path) not in result.unavailable_reason


def test_candidate_refresh_repeats_after_typed_failure_and_valid_empty(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = LocalResearchWorkspacePreferencesStore(tmp_path / "preferences.json")
    page = ResearchWorkspacePage(store, DEFAULT_PREFERENCES)
    page.show()
    app.processEvents()
    requests = []

    def begin_refresh():
        requests.append("refresh")
        page.begin_candidate_scan()

    page.candidate_scan_requested.connect(begin_refresh)
    missing = RetainedCandidateScanService(
        tmp_path,
        scanner=SimpleNamespace(
            scan=lambda: LocalExploratoryCandidateScanner(tmp_path).unavailable(
                "LOCAL_PRICE_DATASET_MISSING"
            )
        ),
        now_utc=datetime.fromisoformat("2026-08-30T09:00:00+09:00"),
    ).scan()
    page.render_exploratory_candidates(missing)
    assert page.candidate_scan_button.isEnabled()
    assert "로컬 종목 데이터가 준비되지 않았습니다" in page.candidate_status.text()
    assert "LOCAL_CANDIDATE_INPUT_MISSING" not in page.candidate_status.text()
    assert "recovery=" not in page.candidate_status.text()
    assert "숫자 표시 없음" in page.candidate_status.accessibleName()
    assert "LOCAL_CANDIDATE_INPUT_MISSING" in page.candidate_status.toolTip()

    QtTest.QTest.mouseClick(page.candidate_scan_button, QtCore.Qt.LeftButton)
    assert requests == ["refresh"]
    assert not page.candidate_scan_button.isEnabled()

    valid_empty = exploratory_view(as_of="2026-08-28", with_candidate=False)
    page.render_exploratory_candidates(valid_empty)
    assert page.candidate_scan_button.isEnabled()
    assert page.candidate_table.rowCount() == 0
    assert "정상 완료" in page.candidate_status.text()
    assert "로컬 입력 정상" in page.candidate_status.accessibleName()

    QtTest.QTest.mouseClick(page.candidate_scan_button, QtCore.Qt.LeftButton)
    assert requests == ["refresh", "refresh"]
    assert not page.candidate_scan_button.isEnabled()
    page.close()
    app.processEvents()


def test_decision_cockpit_composes_accepted_candidate_without_advice_or_scores():
    view = decision_cockpit_view(
        exploratory_view(as_of="2026-08-28", with_candidate=True)
    )

    assert view.state == "READY"
    assert view.displays_candidates
    assert view.guided_example == ("KOSPI", "005930")
    assert len(view.rows) == 1
    assert view.rows[0].identity == "삼성전자 · 005930 · KOSPI"
    assert view.rows[0].observed_evidence == "기술 관찰 · 과매도"
    assert view.rows[0].missing_evidence == "실적·상대가치 근거 없음"
    assert "70000" not in repr(view.rows)
    assert "28.5" not in repr(view.rows)


def test_decision_cockpit_unavailable_is_numeric_free_and_human_first(tmp_path):
    unavailable = RetainedCandidateScanService(tmp_path).unavailable(
        "LOCAL_PRICE_DATASET_MISSING"
    )

    view = decision_cockpit_view(unavailable)

    assert view.state == "UNAVAILABLE"
    assert view.rows == ()
    assert view.guided_example is None
    assert "로컬 종목 데이터가 준비되지 않았습니다" in view.headline
    assert "LOCAL_CANDIDATE" not in view.headline + view.detail + view.provenance
    assert view.recovery is not None
    assert view.recovery.technical_detail.startswith("LOCAL_CANDIDATE_INPUT_MISSING")


def test_decision_cockpit_page_opens_catalog_candidate_and_existing_surfaces():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DecisionCockpitPage()
    host = QtWidgets.QMainWindow()
    host.setCentralWidget(page)
    host.resize(900, 640)
    host.show()
    page.render_view(decision_cockpit_view(
        exploratory_view(as_of="2026-08-28", with_candidate=True)
    ))
    app.processEvents()
    candidates = []
    surfaces = []
    page.candidate_requested.connect(
        lambda market, symbol: candidates.append((market, symbol))
    )
    page.surface_requested.connect(surfaces.append)

    QtTest.QTest.mouseClick(page.example_button, QtCore.Qt.LeftButton)
    QtTest.QTest.mouseClick(page.data_status_button, QtCore.Qt.LeftButton)
    page.candidate_table.itemActivated.emit(page.candidate_table.item(0, 0))

    assert candidates == [("KOSPI", "005930"), ("KOSPI", "005930")]
    assert surfaces == ["DATA_STATUS"]
    assert page.candidate_table.rowCount() == 1
    assert page.candidate_table.maximumHeight() > 300
    assert page.candidate_table.height() > 300
    assert page.candidate_table.horizontalScrollBar().maximum() == 0
    assert page.data_status_button.isVisible()
    assert all(
        page.candidate_table.item(0, column).toolTip()
        == page.candidate_table.item(0, column).text()
        for column in range(page.candidate_table.columnCount())
    )
    assert all(
        button.accessibleName().strip()
        for button in (
            page.market_button, page.research_button, page.account_button,
            page.backtest_button, page.example_button, page.select_button,
            page.data_status_button, page.refresh_button,
        )
    )
    host.close()
    app.processEvents()


def test_main_window_candidate_identity_failure_clears_originating_cockpit():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    cockpit = DecisionCockpitPage()
    cockpit.render_view(decision_cockpit_view(
        exploratory_view(as_of="2026-08-28", with_candidate=True)
    ))
    assert cockpit.candidate_table.rowCount() == 1
    assert cockpit.example_button.isEnabled()
    fake = SimpleNamespace(
        _closing=False,
        decision_cockpit_page=cockpit,
        research_workspace_page=SimpleNamespace(
            candidate_status=QtWidgets.QLabel("기존 Research 상태"),
        ),
    )

    MainWindow._equity_loaded(
        fake,
        "candidate_identity",
        ("KOSPI", "005930", "COCKPIT"),
        EquitySearchView(
            "005930", (), "종목 식별정보를 읽거나 검증할 수 없습니다."
        ),
    )

    visible = " ".join((
        cockpit.status_title.text(),
        cockpit.status_detail.text(),
        cockpit.provenance.text(),
    ))
    assert cockpit.candidate_table.rowCount() == 0
    assert not cockpit.example_button.isEnabled()
    assert "정확한 로컬 종목 정보를 확인하지 못했습니다" in visible
    assert "숫자 표시 없음" in visible
    assert "LOCAL_CANDIDATE" not in visible
    assert "LOCAL_CANDIDATE_IDENTITY_UNAVAILABLE" in cockpit.provenance.toolTip()
    assert fake.research_workspace_page.candidate_status.text() == "기존 Research 상태"
    cockpit.close()
    app.processEvents()


def test_candidate_recovery_copy_keeps_technical_id_out_of_primary_text():
    recovery = candidate_recovery_view(
        "LOCAL_CANDIDATE_INPUT_STALE: retained_as_of=2026-08-25"
    )

    assert "기준일" in recovery.title
    assert "LOCAL_CANDIDATE" not in recovery.title + recovery.impact + recovery.next_step
    assert recovery.technical_detail.startswith("LOCAL_CANDIDATE_INPUT_STALE")
