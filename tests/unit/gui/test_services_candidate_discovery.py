"""Retained non-Qt coverage moved from test_stock_candidate_discovery_gui.py."""
from __future__ import annotations
from dataclasses import replace
from datetime import datetime
import os
from types import SimpleNamespace
import pytest
from stock_data.gui.research_workspace_preferences import DEFAULT_PREFERENCES, LocalResearchWorkspacePreferencesStore
from stock_data.gui.services import EquitySearchView, RetainedCandidateScanService, candidate_recovery_view, decision_cockpit_view
from stock_research.candidate_discovery import CandidateAxisEvidence, StockCandidateEvidence, build_unavailable_candidate_view, discover_stock_research_candidates
from stock_research.exploratory_scanner import EXPLORATORY_SCANNER_VERSION, ExploratoryCandidateView, ExploratoryStockCandidate, LocalExploratoryCandidateScanner

def exploratory_view(*, as_of: str, with_candidate: bool) -> ExploratoryCandidateView:
    candidates = (ExploratoryStockCandidate(symbol='005930', name='삼성전자', market='KOSPI', as_of=as_of, close=70000.0, volume=1000000, rsi14=28.5, disparity60=91.2, technical_state='과매도', data_caution=None),) if with_candidate else ()
    return ExploratoryCandidateView(contract_version=EXPLORATORY_SCANNER_VERSION, availability='READY', as_of=as_of, scanned_instruments=1, eligible_instruments=len(candidates), candidates=candidates, criteria='RSI14 <= 30 OR close/SMA60 <= 80%', source_note='kr_equity_price_daily provider-native original price; current dated universe; optional exact-date KRX MDCSTAT03501 current PER/PBR observation; descriptive only; forward earnings and relative-value judgment not connected')

@pytest.mark.parametrize('with_candidate', [True, False])
def test_retained_candidate_scan_service_preserves_valid_and_valid_empty(tmp_path, with_candidate):
    view = exploratory_view(as_of='2026-08-25', with_candidate=with_candidate)
    service = RetainedCandidateScanService(tmp_path, scanner=SimpleNamespace(scan=lambda: view), now_utc=datetime.fromisoformat('2026-08-26T09:00:00+09:00'))
    result = service.scan()
    assert result == view
    assert result.availability == 'READY'
    assert result.unavailable_reason is None
    assert len(result.candidates) == int(with_candidate)

@pytest.mark.parametrize(('scanner_reason', 'expected_code', 'recovery_fragment'), [('LOCAL_PRICE_DATASET_MISSING', 'LOCAL_CANDIDATE_INPUT_MISSING', 'kr_equity_price_daily'), ('LOCAL_CANDIDATE_READ_FAILED', 'LOCAL_CANDIDATE_INPUT_CORRUPT', '검증·재생성'), ('LOCAL_CANDIDATE_INPUT_EMPTY', 'LOCAL_CANDIDATE_INPUT_EMPTY', '최신 파티션')])
def test_retained_candidate_scan_service_returns_typed_input_recovery(tmp_path, scanner_reason, expected_code, recovery_fragment):
    unavailable = LocalExploratoryCandidateScanner(tmp_path).unavailable(scanner_reason)
    service = RetainedCandidateScanService(tmp_path, scanner=SimpleNamespace(scan=lambda: unavailable), now_utc=datetime.fromisoformat('2026-08-30T09:00:00+09:00'))
    result = service.scan()
    assert result.availability == 'UNAVAILABLE'
    assert result.candidates == ()
    assert result.unavailable_reason.startswith(expected_code)
    assert 'recovery=' in result.unavailable_reason
    assert recovery_fragment in result.unavailable_reason
    assert str(tmp_path) not in result.unavailable_reason

def test_retained_candidate_scan_service_reports_stale_dates_and_recovery(tmp_path):
    view = exploratory_view(as_of='2026-08-25', with_candidate=True)
    service = RetainedCandidateScanService(tmp_path, scanner=SimpleNamespace(scan=lambda: view), now_utc=datetime.fromisoformat('2026-08-30T09:00:00+09:00'))
    result = service.scan()
    assert result.availability == 'UNAVAILABLE'
    assert result.unavailable_reason.startswith('LOCAL_CANDIDATE_INPUT_STALE')
    assert 'retained_as_of=2026-08-25' in result.unavailable_reason
    assert 'expected_as_of=2026-08-27' in result.unavailable_reason
    assert 'recovery=' in result.unavailable_reason

def test_candidate_worker_fallback_keeps_typed_privacy_safe_recovery(tmp_path):
    service = RetainedCandidateScanService(tmp_path)
    result = service.unavailable('LOCAL_CANDIDATE_SCAN_FAILED')
    assert result.availability == 'UNAVAILABLE'
    assert result.unavailable_reason.startswith('LOCAL_CANDIDATE_INPUT_CORRUPT')
    assert 'recovery=' in result.unavailable_reason
    assert 'LOCAL_CANDIDATE_SCAN_FAILED' not in result.unavailable_reason
    assert str(tmp_path) not in result.unavailable_reason

def test_decision_cockpit_composes_accepted_candidate_without_advice_or_scores():
    view = decision_cockpit_view(exploratory_view(as_of='2026-08-28', with_candidate=True))
    assert view.state == 'READY'
    assert view.displays_candidates
    assert view.guided_example == ('KOSPI', '005930')
    assert len(view.rows) == 1
    assert view.rows[0].identity == '삼성전자 · 005930 · KOSPI'
    assert view.rows[0].observed_evidence == '기술 관찰 · 과매도'
    assert view.rows[0].missing_evidence == '실적·상대가치 근거 없음'
    assert '70000' not in repr(view.rows)
    assert '28.5' not in repr(view.rows)

def test_decision_cockpit_unavailable_is_numeric_free_and_human_first(tmp_path):
    unavailable = RetainedCandidateScanService(tmp_path).unavailable('LOCAL_PRICE_DATASET_MISSING')
    view = decision_cockpit_view(unavailable)
    assert view.state == 'UNAVAILABLE'
    assert view.rows == ()
    assert view.guided_example is None
    assert '로컬 종목 데이터가 준비되지 않았습니다' in view.headline
    assert 'LOCAL_CANDIDATE' not in view.headline + view.detail + view.provenance
    assert view.recovery is not None
    assert view.recovery.technical_detail.startswith('LOCAL_CANDIDATE_INPUT_MISSING')

def test_candidate_recovery_copy_keeps_technical_id_out_of_primary_text():
    recovery = candidate_recovery_view('LOCAL_CANDIDATE_INPUT_STALE: retained_as_of=2026-08-25')
    assert '기준일' in recovery.title
    assert 'LOCAL_CANDIDATE' not in recovery.title + recovery.impact + recovery.next_step
    assert recovery.technical_detail.startswith('LOCAL_CANDIDATE_INPUT_STALE')

