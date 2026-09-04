"""Retained non-Qt coverage moved from test_gui_backtest.py."""
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
import numpy as np
import pandas as pd
import pytest
import market_backtest.phase1_replay as phase1_replay
import stock_data.gui.backtest_service as backtest_service_module
import stock_data.gui.research_workspace_preferences as research_preferences_module
from stock_data.gui.account_value_history import AccountValueHistoryPoint, AccountValueHistorySeries
from stock_data.gui.account_snapshot_service import AccountAssetPoint, AccountCurrencySummaryView, AccountPortfolioEntryView, AccountPortfolioView, AccountPositionView, AccountSnapshotState, AccountSnapshotView, AccountSourceActionView, LocalAccountPortfolioService, LocalAccountSnapshotService, LocalAccountSourceSpec
from stock_data.gui.backtest_service import BacktestResultService, BacktestWorkflowError
from stock_data.gui.backtest_scenario_service import SCENARIO_ID, SCENARIO_INPUT_VERSION, BacktestScenarioInputs, BacktestScenarioService
from market_backtest.portfolio import KOSPI200_FROZEN_HOLDOUT_V1
from stock_data.gui.health_service import HealthArtifactView, HealthDatasetRow
from stock_data.gui.manual_account_store import LocalManualAccountStore, ManualAccountPosition, ManualAccountRecord
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.naver_remaining_session_windows import ensure_manifest, is_active
from stock_data.orchestration.account_privacy import MASKED_VALUE
from stock_data.orchestration.toss_account_snapshot import AccountRefreshTrigger
from stock_data.providers.tossinvest import normalize_holdings_payload
from stock_data.providers.kbsec import normalize_domestic_balance_payload
from stock_data.gui.services import CurrentObservationCoverageView, DashboardAverageComparisonView, DashboardChartCoverage, DASHBOARD_CHART_COVERAGE_ATTR, DashboardCurrentStageView, DashboardDisplayState, DashboardMetricView, DashboardSeriesView, MarketValuationView, MarketValuationWindowView, DashboardSparklineView, DashboardService, EquityIdentity, EquitySearchView, EquitySeriesView, IndexSeriesView, NormalizedBenchmarkComparisonView, US_ETF_CHART_IDENTITIES, MarketFundingValue, MarketFundingView, MarketInvestorFlowValue, MarketInvestorFlowView, TossShortWatchlistView, TreasuryRateView, VIXSourceView
from stock_data.contracts.toss_short_watchlist import TOSS_EQUITY_SHORT_WATCHLIST_DAILY
from stock_data.orchestration.toss_short_watchlist_daily import validate_watchlist_dataset
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.gui.vix_futures_adapter import build_vix_futures_dashboard_view
from stock_data.gui.watchlist_service import DEFAULT_LIST_ID, NamedWatchlist, WatchlistItem, WatchlistQuote, WatchlistState

def _write_empty_toss_account_snapshot(path: Path) -> None:
    snapshot = normalize_holdings_payload({'result': {'totalPurchaseAmount': {'krw': '0', 'usd': None}, 'marketValue': {'amount': {'krw': '0', 'usd': None}, 'amountAfterCost': {'krw': '0', 'usd': None}}, 'profitLoss': {'amount': {'krw': '0', 'usd': None}, 'amountAfterCost': {'krw': '0', 'usd': None}, 'rate': '0', 'rateAfterCost': '0'}, 'dailyProfitLoss': {'amount': {'krw': '0', 'usd': None}, 'rate': '0'}, 'items': []}}, collected_at=datetime(2026, 8, 20, 1, 2, 3, tzinfo=timezone.utc))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot), encoding='utf-8')

def _write_toss_account_snapshot_with_position(path: Path) -> None:
    _write_empty_toss_account_snapshot(path)
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['positions'] = [{'symbol': '005930', 'name': 'Fixture Equity', 'market_country': 'KR', 'currency': 'KRW', 'quantity': '0', 'last_price': '0', 'average_purchase_price': '0', 'purchase_amount': '0', 'market_value': '0', 'market_value_after_cost': '0', 'profit_loss': '0', 'profit_loss_after_cost': '0', 'profit_loss_rate': '0', 'profit_loss_rate_after_cost': '0', 'daily_profit_loss': '0', 'daily_profit_loss_rate': '0', 'commission': '0', 'tax': None}]
    path.write_text(json.dumps(payload), encoding='utf-8')

def _write_toss_short_watchlist_fixture(root: Path) -> None:
    frame = pd.DataFrame({'date': [pd.Timestamp('2026-08-19'), pd.Timestamp('2026-08-19')], 'market': ['KOSPI', 'KOSPI'], 'symbol': ['000660', '005930'], 'short_selling_volume': [248815, 1586828], 'short_selling_amount': [377019707500, 396498888250], 'short_selling_volume_rate': [0.012, 0.034], 'short_selling_amount_rate': [0.013, 0.035], 'source_scope': ['KRX_ONLY_PROVIDER_EOD'] * 2, 'watchlist_version': ['2026-08-20-v1'] * 2, 'source': ['tossinvest_open_api'] * 2, 'source_operation': ['getStockShortSelling'] * 2, 'source_date': ['2026-08-19'] * 2, 'collected_at': [pd.Timestamp('2026-08-20T00:07:33Z')] * 2, 'updated_at': [pd.Timestamp('2026-08-19T09:13:47Z'), pd.Timestamp('2026-08-19T09:14:07Z')], 'availability_date': ['2026-08-19'] * 2})
    write_dataset_atomic(frame, root / 'data/normalized/toss_equity_short_watchlist_daily', TOSS_EQUITY_SHORT_WATCHLIST_DAILY, validate_watchlist_dataset)
    state = root / 'data/state/toss_equity_short_watchlist_daily.json'
    journal = root / 'data/state/toss_equity_short_watchlist_daily_journal.json'
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({'dataset': 'toss_equity_short_watchlist_daily', 'watchlist_version': '2026-08-20-v1', 'status': 'SUCCEEDED', 'completed_date': '2026-08-19', 'completed_symbols': ['000660', '005930'], 'landing_files': ['landing-005930', 'landing-000660'], 'token_calls': 1, 'market_calls': 2, 'retained_rows': 2}), encoding='utf-8')
    journal.write_text(json.dumps({'dataset': 'toss_equity_short_watchlist_daily', 'status': 'SUCCEEDED', 'target_date': '2026-08-19'}), encoding='utf-8')

def _write_kb_account_snapshot(path: Path) -> None:
    payload = normalize_domestic_balance_payload({'dataHeader': {'resultCode': '200', 'processCode': '0011', 'processTime': '20260622162350500'}, 'dataBody': {'grid_cnt1': '0001', 'tl_data_cnt': '0001', 'nt_asts_val_amt': '000000000001066450', 'scrts_nt_val_amt': '000000000000426500', 'byng_amt_sum': '000000000000360050', 'val_amt_sum': '000000000000426500', 'val_pl_sum': '000000000000066450', 'Record1': [{'is_cd': 'A005930', 'is_nm': 'Fixture Equity', 'clsf': '현금', 'ec_q_p6': '000000001.000000', 'ordr_psbl_q_p6': '000000001.000000', 'byng_avr_prc': '000000360050.00', 'now_prc': '000000426500.00', 'byng_amt': '000000000000360050', 'val_amt': '000000000000426500', 'val_pl': '000000000000066450'}]}}, collected_at=datetime(2026, 8, 20, 1, 2, 3, tzinfo=timezone.utc))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')

def _write_family_account_snapshot(path: Path) -> None:
    payload = {'schema_version': 3, 'state': 'FAMILY_LOCAL_MANUAL', 'provider': 'MIRAE_ASSET_LOCAL_MANUAL', 'source_mode': 'LOCAL_MANUAL', 'as_of': '2026-08-19T21:00:00+09:00', 'last_reconciled_at': '2026-08-19T21:05:00+09:00', 'registered_holder_scope': 'FAMILY_MEMBER', 'economic_attribution_scope': 'USER_DECLARED_FUNDS', 'legal_ownership_claimed': False, 'include_in_user_fund_total': True, 'currency': 'KRW', 'total_assets': 500000, 'securities_value': 500000, 'cash_balance': None, 'available_cash': None, 'realized_pnl': None, 'unrealized_pnl': 20000, 'positions': [{'symbol': 'ETF1', 'name': 'Fixture ETF', 'quantity': 2, 'market_value': 500000, 'realized_pnl': None, 'unrealized_pnl': 20000}], 'asset_history': []}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')

def test_all_local_account_sources_reject_private_position_text_value_free(tmp_path):
    writers = (_write_toss_account_snapshot_with_position, _write_kb_account_snapshot, _write_family_account_snapshot)
    private_cases = (('symbol', '123456789012'), ('name', 'accountNo=123456789012'))
    for source_index, writer in enumerate(writers):
        for case_index, (field, private_text) in enumerate(private_cases):
            path = tmp_path / f'source-{source_index}-case-{case_index}.json'
            writer(path)
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['positions'][0][field] = private_text
            path.write_text(json.dumps(payload), encoding='utf-8')
            view = LocalAccountSnapshotService(path).load()
            assert view.state is AccountSnapshotState.NOT_AVAILABLE
            assert view.reason == 'ACCOUNT_SNAPSHOT_INVALID'
            assert private_text not in repr(view)
    for source_index, writer in enumerate(writers):
        path = tmp_path / f'valid-source-{source_index}.json'
        writer(path)
        view = LocalAccountSnapshotService(path).load()
        assert view.displays_values
        assert view.positions[0].symbol in {'005930', 'A005930', 'ETF1'}
        assert view.positions[0].name in {'Fixture Equity', 'Fixture ETF'}

def test_dashboard_health_summary_uses_all_retained_rows_and_fails_closed(tmp_path):
    freshness = ['CURRENT'] * 7 + ['EXPECTED_LAG'] * 7 + ['STALE'] * 5 + ['EXPECTED_LAG'] + ['STALE'] * 14 + ['UNKNOWN'] * 17 + ['NOT_APPLICABLE'] * 29
    rows = tuple((HealthDatasetRow(dataset=f'dataset_{index}', role='SOURCE', cadence='DAILY', latest='N/A', expected='N/A', freshness=status, operational='BLOCKED' if index < 4 else 'READY', blocker='N/A', pit='PIT_BLOCKED' if index < 5 else 'PIT_SAFE', automation='SCHEDULED / ENABLED' if index < 19 else 'NO_REFRESH / DISABLED', source='fixture', runtime_coverage='NOT_PROBED') for index, status in enumerate(freshness)))
    service = DashboardService(tmp_path)
    summary = service.data_health(health=HealthArtifactView('READY', 'retained 80-row health', rows))
    assert summary == {'overall': 'DEGRADED', 'current': 7, 'expected_lag': 8, 'stale': 19, 'operational_blocked': 4, 'predictive_blocked': 5, 'research_only': 0, 'failed': 0, 'managed_total': 19, 'managed_acceptable': 14, 'managed_current': 7, 'managed_expected_lag': 7, 'managed_stale': 5, 'managed_unknown': 0, 'managed_not_applicable': 0, 'display_total': 0, 'display_stale': 0, 'display_unknown': 0, 'display_gap': 0, 'display_current': 14, 'display_late': 5, 'display_failed': 0, 'display_preserved': 61, 'display_reference': 0, 'unregistered_count': 0, 'decision_hold_causes': (), 'source': 'retained 80-row health'}
    unavailable = service.data_health(health=HealthArtifactView('REPORT NOT AVAILABLE', 'missing health', (), 'missing'))
    assert unavailable['overall'] == 'UNKNOWN'
    assert unavailable['current'] == 0 and unavailable['failed'] == 1

def _payload() -> dict:
    return {'status': 'DESCRIPTIVE_SIGNAL_REPLAY_NOT_PORTFOLIO_BACKTEST', 'frozen_manifest': {'dataset': 'kr_kospi200_index_daily', 'contract_version': 1, 'coverage_start': '1990-01-03', 'coverage_end': '2026-08-14', 'rows': 9447, 'files': 37, 'bytes': 738068, 'root_manifest_sha256': phase1_replay.EXPECTED_FROZEN_DIGEST, 'decision_rule': 'T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION'}, 'thresholds': {'realized_volatility_20d': 0.25, 'rolling_drawdown_60d': -0.1, 'ma_distance_60d': -0.08, 'return_20d': -0.05, 'minimum_conditions': 2}, 'metrics': {'observations': 10, 'true_positive': 2, 'false_positive': 3, 'false_negative': 2, 'true_negative': 3, 'precision': 0.4, 'recall': 0.5, 'false_positive_rate': 0.5, 'event_prevalence': 0.4, 'pr_auc_average_precision': 0.4, 'mean_forward_return_20d': 0.01, 'mean_forward_max_drawdown_20d': -0.1, 'mean_mae_20d': -0.1, 'mean_mfe_20d': 0.1}, 'crisis_replay': [{'event': 'development_event', 'start': '2020-01-01', 'end': '2020-06-30', 'status': 'DIAGNOSTIC_ONLY', 'observations': 50, 'risk_off_observations': 10, 'mean_forward_20d_return': -0.01, 'worst_forward_20d_drawdown': -0.2}]}

def _write_result(root: Path, payload: dict) -> Path:
    path = root / 'artifacts/backtest/phase1_signal_replay/result.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path

@pytest.fixture(scope='module')
def strict_phase1_bodies() -> dict[str, bytes]:
    """Build the real strict producer payload once, without publishing it."""
    project_root = Path(__file__).resolve().parents[3]
    bundle = phase1_replay._build_replay_bundle(project_root)
    return dict(bundle.bodies)

def _write_strict_phase1_bundle(root: Path, bodies: dict[str, bytes]) -> Path:
    output = root / phase1_replay.DEFAULT_OUTPUT_RELATIVE
    output.mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (output / name).write_bytes(body)
    return output

def _retarget_strict_phase1_bodies(original: dict[str, bytes], project_root: Path, output: Path) -> dict[str, bytes]:
    base = {name: original[name] for name in ('signals.csv', 'result.json', 'experiments.json', 'portfolio_ledger.json')}
    registry = json.loads(base['experiments.json'])
    result_path = output.resolve() / 'result.json'
    try:
        registered = result_path.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        registered = result_path.as_posix()
    registry['experiments'][0]['result_artifact'] = registered
    base['experiments.json'] = (json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    return dict(phase1_replay._bind_bundle(base, frozen_input_digest=phase1_replay.EXPECTED_FROZEN_DIGEST).bodies)

def _prepare_strict_phase1_bundle(root: Path, original: dict[str, bytes]) -> tuple[Path, Path, dict[str, bytes]]:
    project_root = Path(__file__).resolve().parents[3]
    output = root / phase1_replay.DEFAULT_OUTPUT_RELATIVE
    bodies = _retarget_strict_phase1_bodies(original, project_root, output)
    _write_strict_phase1_bundle(root, bodies)
    return (project_root, output, bodies)

def _rebind_strict_phase1_bodies(original: dict[str, bytes], *, mutate_result=None, mutate_ledger=None, mutate_experiment=None, mutate_signals=None) -> dict[str, bytes]:
    base = {name: original[name] for name in ('signals.csv', 'result.json', 'experiments.json', 'portfolio_ledger.json')}
    result = json.loads(base['result.json'])
    ledger = json.loads(base['portfolio_ledger.json'])
    if mutate_ledger is not None:
        mutate_ledger(ledger)
        base['portfolio_ledger.json'] = phase1_replay._json_bytes(ledger)
        result['portfolio_foundation']['ledger_artifact_digest'] = phase1_replay.artifact_bytes_digest(base['portfolio_ledger.json'])
    if mutate_result is not None:
        mutate_result(result)
    base['result.json'] = phase1_replay._json_bytes(result, pretty=True)
    if mutate_signals is not None:
        base['signals.csv'] = mutate_signals(base['signals.csv'])
    registry = json.loads(base['experiments.json'])
    experiment = registry['experiments'][0]
    experiment['signals_artifact_digest'] = phase1_replay.artifact_bytes_digest(base['signals.csv'])
    experiment['result_artifact_digest'] = phase1_replay.artifact_bytes_digest(base['result.json'])
    if mutate_experiment is not None:
        mutate_experiment(experiment)
    base['experiments.json'] = (json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    return dict(phase1_replay._bind_bundle(base, frozen_input_digest=phase1_replay.EXPECTED_FROZEN_DIGEST).bodies)

def test_backtest_result_service_reads_typed_local_view_without_mutation(tmp_path):
    path = _write_result(tmp_path, _payload())
    before = path.read_bytes()
    view = BacktestResultService(tmp_path).load()
    assert path.read_bytes() == before
    assert view.artifact_state == 'READY'
    assert view.input_coverage is not None
    assert view.input_coverage.coverage_end == '2026-08-14'
    assert dict(((item.name, item.value) for item in view.metrics))['precision'] == 0.4
    assert view.horizons == ('forward return: 20 trading days', 'forward max drawdown: 20 trading days')
    assert 'EQUITY CURVE UNAVAILABLE' in view.portfolio_scope

def test_backtest_result_service_fails_closed_for_missing_or_unknown_result(tmp_path):
    missing = BacktestResultService(tmp_path).load()
    assert missing.artifact_state == 'RESULT NOT AVAILABLE'
    assert missing.metrics == ()
    payload = _payload()
    payload['status'] = 'PORTFOLIO_BACKTEST'
    _write_result(tmp_path, payload)
    rejected = BacktestResultService(tmp_path).load()
    assert rejected.artifact_state == 'RESULT NOT AVAILABLE'
    assert 'not the accepted non-portfolio experiment' in (rejected.warning or '')

def test_backtest_result_service_fails_closed_for_unaccepted_input_boundary(tmp_path):
    for field, value, warning in (('dataset', 'other_dataset', 'dataset is not the accepted'), ('contract_version', 2, 'contract version is not accepted'), ('decision_rule', 'SAME_DAY_DECISION', 'decision rule is not accepted'), ('coverage_start', '2026-08-15', 'coverage is reversed'), ('coverage_end', '2026/08/14', 'must be an ISO date')):
        payload = _payload()
        payload['frozen_manifest'][field] = value
        _write_result(tmp_path, payload)
        rejected = BacktestResultService(tmp_path).load()
        assert rejected.artifact_state == 'RESULT NOT AVAILABLE'
        assert warning in (rejected.warning or '')

def test_backtest_legacy_result_rejects_unknown_metrics_and_holdout_diagnostics(tmp_path):
    payload = _payload()
    payload['metrics'] = {'made_up': -999}
    _write_result(tmp_path, payload)
    assert BacktestResultService(tmp_path).load().artifact_state == 'RESULT NOT AVAILABLE'
    payload = _payload()
    payload['crisis_replay'] = [{'event': 'leaked_holdout', 'start': '2022-01-01', 'end': '2022-12-31', 'status': 'DIAGNOSTIC_ONLY', 'observations': 1, 'risk_off_observations': 1, 'mean_forward_20d_return': 0.5, 'worst_forward_20d_drawdown': -0.2}]
    _write_result(tmp_path, payload)
    assert BacktestResultService(tmp_path).load().artifact_state == 'RESULT NOT AVAILABLE'

def test_backtest_service_accepts_current_retained_artifact():
    root = Path(__file__).resolve().parents[3]
    artifact_root = root / phase1_replay.DEFAULT_OUTPUT_RELATIVE
    before = {name: (artifact_root / name).read_bytes() for name in phase1_replay._OWNED_FILES}
    view = BacktestResultService(root).load()
    after = {name: (artifact_root / name).read_bytes() for name in phase1_replay._OWNED_FILES}
    assert view.artifact_state == 'READY'
    assert before == after
    assert view.holdout is not None
    assert view.holdout.results_reviewed is False
    assert any((row.status == 'DIAGNOSTIC_ONLY' for row in view.crises))
    assert any((row.status == 'UNTOUCHED_HOLDOUT' for row in view.crises))

def test_backtest_service_rejects_changed_explicit_phase1_dependency(tmp_path, strict_phase1_bodies, monkeypatch):
    project_root, output, _written = _prepare_strict_phase1_bundle(tmp_path, strict_phase1_bodies)
    accepted_digest = phase1_replay.phase1_code_digest(project_root)
    assert BacktestResultService(project_root, output_root=output).load_validated_bundle().receipt.status == 'READY'
    monkeypatch.setattr(backtest_service_module, 'phase1_code_digest', lambda _root: '0' * 64 if accepted_digest != '0' * 64 else '1' * 64)
    with pytest.raises(BacktestWorkflowError):
        BacktestResultService(project_root, output_root=output).load_validated_bundle()

def test_backtest_service_accepts_only_the_bound_five_file_generation(tmp_path, strict_phase1_bodies):
    project_root, output, expected_bodies = _prepare_strict_phase1_bundle(tmp_path, strict_phase1_bodies)
    accepted = BacktestResultService(project_root, output_root=output).load_validated_bundle()
    assert accepted.receipt.status == 'READY'
    assert accepted.receipt.frozen_input_digest == phase1_replay.EXPECTED_FROZEN_DIGEST
    assert tuple(accepted.artifact_bodies) == tuple(sorted(expected_bodies))
    assert dict(accepted.artifact_bodies) == expected_bodies
    assert accepted.view.artifact_state == 'READY'
    assert accepted.view.holdout is not None
    assert accepted.view.holdout.development_observations == 8225
    assert accepted.view.holdout.holdout_observations == 1222
    assert accepted.view.holdout.results_reviewed is False
    assert accepted.view.portfolio is not None
    assert accepted.view.portfolio.status == 'DEVELOPMENT_ONLY_CLOSE_PROXY'
    assert accepted.view.portfolio.instrument_claim == 'NOT_EXECUTABLE_INSTRUMENT'
    assert accepted.view.portfolio.assumptions['one_way_transaction_cost_rate'] == 0.001
    assert accepted.view.portfolio.assumptions['leverage_allowed'] is False
    assert accepted.view.portfolio.curve
    assert accepted.view.bundle_receipt == accepted.receipt

@pytest.mark.parametrize('artifact_name', ('bundle.json', 'experiments.json', 'portfolio_ledger.json', 'result.json', 'signals.csv'))
def test_backtest_service_rejects_each_tampered_artifact_without_legacy_fallback(tmp_path, strict_phase1_bodies, artifact_name):
    project_root, output, bodies = _prepare_strict_phase1_bundle(tmp_path, strict_phase1_bodies)
    (output / artifact_name).write_bytes(bodies[artifact_name] + b' ')
    service = BacktestResultService(project_root, output_root=output)
    with pytest.raises(BacktestWorkflowError):
        service.load_validated_bundle()
    unavailable = service.load()
    assert unavailable.artifact_state == 'RESULT NOT AVAILABLE'
    assert 'strict local backtest bundle is invalid' in (unavailable.warning or '')

def test_backtest_service_never_downgrades_a_strict_result_to_legacy(tmp_path, strict_phase1_bodies):
    project_root, output, _written = _prepare_strict_phase1_bundle(tmp_path, strict_phase1_bodies)
    (output / 'bundle.json').unlink()
    (output / 'portfolio_ledger.json').unlink()
    view = BacktestResultService(project_root, output_root=output).load()
    assert view.artifact_state == 'RESULT NOT AVAILABLE'
    assert 'strict local backtest bundle is invalid' in (view.warning or '')

@pytest.mark.parametrize('case', ('scope', 'holdout', 'ledger', 'threshold', 'holdout_diagnostic', 'split', 'result_artifact', 'foundation_bool', 'metrics', 'signal_flag', 'grid', 'code_digest', 'feature_versions', 'threshold_digest'))
def test_backtest_service_rejects_rebound_semantic_tampering(tmp_path, strict_phase1_bodies, case):

    def mutate_result(result):
        if case == 'scope':
            result['metrics_scope'] = 'FULL_SAMPLE'
        elif case == 'holdout':
            result['untouched_holdout_policy']['results_reviewed'] = True
        elif case == 'threshold':
            result['thresholds']['realized_volatility_20d'] = 0.99
        elif case == 'foundation_bool':
            result['portfolio_foundation']['metrics']['initial_nav'] = True
        elif case == 'metrics':
            result['metrics'] = {'made_up': -999}
            result['development_metrics'] = {'made_up': -999}
        elif case == 'grid':
            result['predefined_small_grid'][0]['metrics'] = {'made_up': -999}
        elif case == 'holdout_diagnostic':
            leaked = {'event': 'bear_market_2022', 'start': '2022-01-01', 'end': '2022-12-31', 'status': 'DIAGNOSTIC_ONLY', 'observations': 1, 'risk_off_observations': 1, 'adverse_observations': 1, 'event_precision': 1.0, 'event_recall': 1.0, 'first_risk_off_date': '2022-01-03', 'worst_forward_20d_drawdown': -0.2, 'mean_forward_20d_return': 0.123, 'holdout_observations_excluded': 0}
            result['crisis_replay'][3] = leaked
            result['crisis_replay_development_only'][3] = dict(leaked)

    def mutate_experiment(experiment):
        if case == 'holdout':
            experiment['holdout_results_reviewed'] = True
        elif case == 'split':
            experiment['purge'] = 999
            experiment['embargo'] = 999
            experiment['label_horizon_trading_days'] = 1
        elif case == 'result_artifact':
            experiment['result_artifact'] = 'result.json'
        elif case == 'code_digest':
            experiment['code_tree_digest'] = 'b' * 64
        elif case == 'feature_versions':
            experiment['feature_versions'] = ['made_up:v999']
        elif case == 'threshold_digest':
            experiment['threshold_values_digest'] = 'c' * 64

    def mutate_ledger(ledger):
        if case == 'ledger':
            ledger['simulation']['metrics']['ending_nav'] += 0.01

    def mutate_signals(body):
        if case != 'signal_flag':
            return body
        text = body.decode('utf-8')
        return text.replace(',False,False,False,False,0,False,1\n', ',NOT_BOOLEAN,False,False,False,0,False,1\n', 1).encode('utf-8')
    bodies = _rebind_strict_phase1_bodies(strict_phase1_bodies, mutate_result=mutate_result, mutate_ledger=mutate_ledger if case == 'ledger' else None, mutate_experiment=mutate_experiment if case != 'result_artifact' else None, mutate_signals=mutate_signals if case == 'signal_flag' else None)
    project_root, output, written = _prepare_strict_phase1_bundle(tmp_path, bodies)
    if case == 'result_artifact':
        written = _rebind_strict_phase1_bodies(written, mutate_experiment=mutate_experiment)
        _write_strict_phase1_bundle(tmp_path, written)
    with pytest.raises(BacktestWorkflowError):
        BacktestResultService(project_root, output_root=output).load_validated_bundle()

def test_backtest_service_binds_runner_receipt_and_rejects_post_run_tamper(tmp_path, strict_phase1_bodies):
    project_root, output, written = _prepare_strict_phase1_bundle(tmp_path, strict_phase1_bodies)
    accepted = BacktestResultService(project_root, output_root=output).load_validated_bundle()
    requests = []

    def runner(request):
        requests.append(request)
        return accepted.receipt
    service = BacktestResultService(project_root, output_root=output, runner=runner)
    rerun = service.run_validated()
    assert rerun.receipt == accepted.receipt
    assert len(requests) == 1
    assert requests[0].output_root == output
    (output / 'signals.csv').write_bytes(written['signals.csv'] + b'tampered')
    with pytest.raises(BacktestWorkflowError):
        service.run_validated()

def test_backtest_exact_export_uses_only_the_accepted_immutable_bytes(tmp_path, strict_phase1_bodies):
    project_root, output, written = _prepare_strict_phase1_bundle(tmp_path, strict_phase1_bodies)
    service = BacktestResultService(project_root, output_root=output)
    accepted = service.load_validated_bundle()
    (output / 'result.json').write_bytes(b'source generation changed')
    destination = tmp_path / 'exported-backtest'
    receipt = service.export_exact_bundle(accepted, destination)
    assert receipt.status == 'EXPORTED'
    assert receipt.bundle_digest == accepted.receipt.bundle_digest
    assert tuple((path.name for path in sorted(destination.iterdir()))) == tuple(sorted(written))
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == written
    with pytest.raises(BacktestWorkflowError):
        service.export_exact_bundle(accepted, destination)

def test_backtest_exact_export_failure_never_publishes_partial_destination(tmp_path, strict_phase1_bodies, monkeypatch):
    project_root, output, _written = _prepare_strict_phase1_bundle(tmp_path, strict_phase1_bodies)
    service = BacktestResultService(project_root, output_root=output)
    accepted = service.load_validated_bundle()
    destination = tmp_path / 'interrupted-export'

    def fail_fsync(_descriptor):
        raise OSError('injected export sync failure')
    monkeypatch.setattr(backtest_service_module.os, 'fsync', fail_fsync)
    with pytest.raises(BacktestWorkflowError):
        service.export_exact_bundle(accepted, destination)
    assert not destination.exists()
    assert not tuple(tmp_path.glob(f'.{destination.name}.backtest-export-*.stage'))

def test_backtest_exact_export_rejects_redirected_parent(tmp_path, strict_phase1_bodies):
    project_root, output, _written = _prepare_strict_phase1_bundle(tmp_path, strict_phase1_bodies)
    service = BacktestResultService(project_root, output_root=output)
    accepted = service.load_validated_bundle()
    real_parent = tmp_path / 'real-export-parent'
    redirected_parent = tmp_path / 'redirected-export-parent'
    real_parent.mkdir()
    try:
        redirected_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError as error:
        pytest.skip(f'directory symlink unavailable on this host: {error}')
    with pytest.raises(BacktestWorkflowError):
        service.export_exact_bundle(accepted, redirected_parent / 'should-not-be-created')
    assert not (real_parent / 'should-not-be-created').exists()

def test_toss_short_watchlist_local_adapter_is_exact_and_fail_closed(tmp_path):
    _write_toss_short_watchlist_fixture(tmp_path)
    service = DashboardService(tmp_path)
    current = service.toss_short_watchlist_view()
    assert current.displays_values
    assert current.as_of == '2026-08-19'
    assert current.source_scope == 'KRX_ONLY_PROVIDER_EOD'
    assert not current.automation_enabled
    assert [(member.symbol, member.market) for member in current.members] == [('005930', 'KOSPI'), ('000660', 'KOSPI')]
    assert [member.short_selling_volume for member in current.members] == [1586828, 248815]
    stale = service.toss_short_watchlist_view(expected_date='2026-08-20')
    assert not stale.displays_values
    assert stale.members == ()
    assert stale.display_state is DashboardDisplayState.REFRESH_REQUIRED
    assert stale.freshness == 'STALE'
    assert '2026-08-19' in (stale.unavailable_reason or '')
    assert '2026-08-20' in (stale.unavailable_reason or '')
    state_path = tmp_path / 'data/state/toss_equity_short_watchlist_daily.json'
    checkpoint = json.loads(state_path.read_text(encoding='utf-8'))
    checkpoint['status'] = 'RUNNING'
    state_path.write_text(json.dumps(checkpoint), encoding='utf-8')
    blocked = service.toss_short_watchlist_view()
    assert not blocked.displays_values
    assert blocked.members == ()
    assert blocked.display_state is DashboardDisplayState.PROHIBITED
    assert '완전히 성공한 상태가 아닙니다' in (blocked.unavailable_reason or '')
    missing = DashboardService(tmp_path / 'missing').toss_short_watchlist_view()
    assert not missing.displays_values
    assert missing.display_state is DashboardDisplayState.UNAVAILABLE
    assert '보존 데이터와 완료 checkpoint가 없습니다' in (missing.unavailable_reason or '')
