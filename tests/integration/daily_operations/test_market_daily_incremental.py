from datetime import date, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from zoneinfo import ZoneInfo

from scripts.manual.collect import run_market_daily_incremental as market_daily_cli
import stock_data.orchestration.market_daily_incremental as market_daily
from stock_data.orchestration.market_daily_incremental import (
    MarketDailyIncrementalError,
    SHORT_SELLING_FINALITY_POLICIES,
    _run_short_selling_trading_atomic,
    _short_selling_transaction_paths,
    execute_data_go_kr_daily,
    execute_liquidity_credit_two_pass,
    execute_short_selling_daily,
    health_from_exact_date_plan,
    plan_data_go_kr_daily,
    plan_liquidity_credit_two_pass,
    plan_short_selling_daily,
    select_credit_balance_fallback_date,
    short_selling_raw_call_budget,
)
from stock_data.orchestration.daily_operations import (
    DATASET_OPERATIONS, FreshnessStatus, OperationalEligibility,
)
from stock_data.pipelines.data_v1_collection import CollectionResult
from stock_data.pipelines.short_selling_backfill import BatchResult, plan_scopes


TARGET = date(2026, 8, 13)


def short_plan(root, dataset="trading", **overrides):
    values = {
        "project_root": root,
        "dataset": dataset,
        "market_date": TARGET,
        "latest_finalized_market_date": TARGET,
        "accepted_market_dates": (TARGET,),
        "operation_reviewed": True,
    }
    values.update(overrides)
    return plan_short_selling_daily(**values)


def data_plan(root, dataset="detail", **overrides):
    values = {
        "project_root": root,
        "dataset": dataset,
        "market_date": TARGET,
        "latest_finalized_market_date": TARGET,
        "accepted_market_dates": (TARGET,),
        "operation_reviewed": True,
    }
    values.update(overrides)
    return plan_data_go_kr_daily(**values)


def two_pass_plan(root, dataset="market_liquidity", **overrides):
    values = {
        "project_root": root, "dataset": dataset, "market_date": TARGET,
        "latest_finalized_market_date": TARGET,
        "accepted_market_dates": (TARGET,), "operation_reviewed": True,
        "max_api_calls": 1,
    }
    values.update(overrides)
    return plan_liquidity_credit_two_pass(**values)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _successful_staged_date_runner(calls, *, status="COMPLETE"):
    def run(**kwargs):
        calls.append(kwargs)
        _write_json(kwargs["landing_path"], [{"response": "fixture"}])
        marker = kwargs["base_date"]
        _write_json(kwargs["state_path"], {
            "dataset": kwargs["contract"].name,
            "completed_partitions": [marker] if status == "COMPLETE" else [],
            "valid_empty_partitions": [marker] if status == "VALID_EMPTY" else [],
            "failed_partitions": {}, "staged_partitions": [],
        })
        if status == "COMPLETE":
            kwargs["normalized_root"].mkdir(parents=True, exist_ok=True)
            (kwargs["normalized_root"] / "fixture").write_text("new", encoding="utf-8")
        return CollectionResult(
            kwargs["contract"].name, 1 if status == "COMPLETE" else 0, 1,
            status, TARGET.isoformat() if status == "COMPLETE" else None,
            TARGET.isoformat() if status == "COMPLETE" else None,
        )
    return run


def _two_pass_runner(values, calls, *, status="COMPLETE", fail=False):
    def run(**kwargs):
        calls.append(kwargs)
        _write_json(kwargs["landing_path"], [{"source": values, "status": status}])
        if fail:
            raise ValueError("injected observation failure")
        marker = kwargs["base_date"]
        _write_json(kwargs["state_path"], {
            "dataset": kwargs["contract"].name,
            "completed_partitions": [marker] if status == "COMPLETE" else [],
            "valid_empty_partitions": [marker] if status == "VALID_EMPTY" else [],
            "failed_partitions": {}, "staged_partitions": [],
        })
        if status == "COMPLETE":
            kwargs["normalized_root"].mkdir(parents=True, exist_ok=True)
            _write_json(kwargs["normalized_root"] / "row.json", values)
        return CollectionResult(
            kwargs["contract"].name, int(status == "COMPLETE"), 1, status,
            TARGET.isoformat() if status == "COMPLETE" else None,
            TARGET.isoformat() if status == "COMPLETE" else None,
        )
    return run


def _fake_two_pass_read(root, contract, _validator):
    row = json.loads((root / "row.json").read_text(encoding="utf-8"))
    return pd.DataFrame([row], columns=contract.column_names)


def _liquidity_row(deposits=100):
    return {
        "date": TARGET.isoformat(), "investor_deposits": deposits,
        "exchange_derivatives_deposits": 200, "customer_rp_sell_balance": 300,
        "brokerage_receivables": 400, "forced_sale_amount": 500,
        "forced_sale_ratio": 1.5,
    }


def _credit_row(total=100):
    return {
        "date": TARGET.isoformat(), "credit_financing_total": total,
        "credit_financing_kospi": 40, "credit_financing_kosdaq": 60,
        "credit_stock_lending_total": 20, "credit_stock_lending_kospi": 8,
        "credit_stock_lending_kosdaq": 12, "subscription_loan": 5,
        "securities_collateral_loan": 30,
    }


def _seed_short_production(root):
    normalized = root / "data/normalized/kr_short_selling_trading_daily"
    normalized.mkdir(parents=True)
    (normalized / "retained.txt").write_text("valid-before", encoding="utf-8")
    checkpoint = root / "data/state/kr_short_selling_trading_daily_v2.json"
    _write_json(checkpoint, {
        "dataset": "trading", "contract_version": 2,
        "completed": {"older-scope": {"classification": "SUCCESS"}},
    })
    return normalized, checkpoint


def _atomic_fixture_runner(*, fail_at=None, incomplete=False, calls=2, assertions=None):
    def run(**kwargs):
        normalized = kwargs.get(
            "normalized_root",
            kwargs["project_root"] / "data/normalized/kr_short_selling_trading_daily",
        )
        checkpoint = kwargs.get(
            "checkpoint_path",
            kwargs["project_root"] / "data/state/kr_short_selling_trading_daily_v2.json",
        )
        if assertions:
            assertions(normalized, checkpoint, kwargs)
        completed = json.loads(checkpoint.read_text(encoding="utf-8")).get("completed", {}) \
            if checkpoint.exists() else {}
        completed_now = 0
        for scope in plan_scopes("trading", (TARGET,)):
            if fail_at == scope.market:
                raise RuntimeError(f"injected {scope.market} failure")
            normalized.mkdir(parents=True, exist_ok=True)
            (normalized / f"{scope.market}.txt").write_text("new", encoding="utf-8")
            completed[scope.scope_id] = {"classification": "SUCCESS"}
            _write_json(checkpoint, {
                "dataset": "trading", "contract_version": 2,
                "completed": completed, "status": "IN_PROGRESS",
            })
            completed_now += 1
            if incomplete:
                break
        return BatchResult(
            dataset="trading", planned_scopes=2, previously_completed_scopes=0,
            recovered_scopes=0, requested_business_calls=calls,
            completed_now=completed_now, normalized_rows=completed_now,
            raw_http_requests=calls, checkpoint_path=checkpoint,
            normalized_root=normalized,
        )
    return run


def _run_atomic(root, runner):
    return _run_short_selling_trading_atomic(
        short_plan(root), project_root=root, client_factory=object(),
        batch_runner=runner,
    )


def test_short_trading_atomic_promotes_both_markets_together(tmp_path):
    normalized, checkpoint = _seed_short_production(tmp_path)
    result = _run_atomic(tmp_path, _atomic_fixture_runner())
    assert (normalized / "KOSPI.txt").read_text(encoding="utf-8") == "new"
    assert (normalized / "KOSDAQ.txt").read_text(encoding="utf-8") == "new"
    completed = json.loads(checkpoint.read_text(encoding="utf-8"))["completed"]
    assert all(scope.scope_id in completed for scope in plan_scopes("trading", (TARGET,)))
    assert result.normalized_root == normalized and result.checkpoint_path == checkpoint


def test_short_trading_atomic_kosdaq_failure_does_not_publish_kospi(tmp_path):
    normalized, checkpoint = _seed_short_production(tmp_path)
    before_checkpoint = checkpoint.read_bytes()
    with pytest.raises(RuntimeError, match="KOSDAQ"):
        _run_atomic(tmp_path, _atomic_fixture_runner(fail_at="KOSDAQ"))
    assert not (normalized / "KOSPI.txt").exists()
    assert checkpoint.read_bytes() == before_checkpoint


def test_short_trading_atomic_first_scope_failure_changes_no_production(tmp_path):
    normalized, checkpoint = _seed_short_production(tmp_path)
    before_checkpoint = checkpoint.read_bytes()
    with pytest.raises(RuntimeError, match="KOSPI"):
        _run_atomic(tmp_path, _atomic_fixture_runner(fail_at="KOSPI"))
    assert sorted(path.name for path in normalized.iterdir()) == ["retained.txt"]
    assert checkpoint.read_bytes() == before_checkpoint


def test_short_trading_atomic_promotion_exception_rolls_back_both_artifacts(
    tmp_path, monkeypatch,
):
    normalized, checkpoint = _seed_short_production(tmp_path)
    before_checkpoint = checkpoint.read_bytes()
    original_replace = type(checkpoint).replace
    failed = False

    def fail_checkpoint_promotion(path, target):
        nonlocal failed
        if path.name == "staged_checkpoint.json" and not failed:
            failed = True
            raise OSError("injected promotion failure")
        return original_replace(path, target)

    monkeypatch.setattr(type(checkpoint), "replace", fail_checkpoint_promotion)
    with pytest.raises(OSError, match="promotion failure"):
        _run_atomic(tmp_path, _atomic_fixture_runner())
    assert sorted(path.name for path in normalized.iterdir()) == ["retained.txt"]
    assert checkpoint.read_bytes() == before_checkpoint


def test_short_trading_atomic_restart_recovers_incomplete_journal(tmp_path):
    paths = _short_selling_transaction_paths(tmp_path, TARGET)
    paths["previous_normalized"].mkdir(parents=True)
    (paths["previous_normalized"] / "retained.txt").write_text("valid-before", encoding="utf-8")
    paths["normalized"].mkdir(parents=True)
    (paths["normalized"] / "KOSPI.txt").write_text("interrupted", encoding="utf-8")
    _write_json(paths["previous_checkpoint"], {
        "dataset": "trading", "contract_version": 2, "completed": {"older-scope": {}},
    })
    _write_json(paths["checkpoint"], {
        "dataset": "trading", "contract_version": 2,
        "completed": {scope.scope_id: {} for scope in plan_scopes("trading", (TARGET,))},
    })
    _write_json(paths["journal"], {
        "contract_version": 1, "dataset": "trading", "market_date": TARGET.isoformat(),
        "scope_ids": [scope.scope_id for scope in plan_scopes("trading", (TARGET,))],
        "normalized_existed": True, "checkpoint_existed": True,
        "status": "CHECKPOINT_PROMOTED",
    })

    def assert_recovered(normalized, checkpoint, _kwargs):
        assert (normalized / "retained.txt").read_text(encoding="utf-8") == "valid-before"
        assert "older-scope" in json.loads(checkpoint.read_text(encoding="utf-8"))["completed"]

    _run_atomic(tmp_path, _atomic_fixture_runner(assertions=assert_recovered))
    assert (paths["normalized"] / "KOSPI.txt").exists()
    assert (paths["normalized"] / "KOSDAQ.txt").exists()


def test_short_trading_atomic_successful_date_rerun_is_pre_network_noop(tmp_path):
    _seed_short_production(tmp_path)
    _run_atomic(tmp_path, _atomic_fixture_runner())
    observed = []

    def noop(**kwargs):
        observed.append(kwargs)
        return BatchResult(
            dataset="trading", planned_scopes=2, previously_completed_scopes=2,
            recovered_scopes=0, requested_business_calls=0, completed_now=0,
            normalized_rows=0, raw_http_requests=0,
            checkpoint_path=tmp_path / "data/state/kr_short_selling_trading_daily_v2.json",
            normalized_root=tmp_path / "data/normalized/kr_short_selling_trading_daily",
        )

    result = _run_atomic(tmp_path, noop)
    assert result.requested_business_calls == 0
    assert len(observed) == 1 and "normalized_root" not in observed[0]


def test_short_trading_atomic_checkpoint_requires_both_markets(tmp_path):
    normalized, checkpoint = _seed_short_production(tmp_path)
    before_checkpoint = checkpoint.read_bytes()
    with pytest.raises(MarketDailyIncrementalError, match="both short-selling"):
        _run_atomic(tmp_path, _atomic_fixture_runner(incomplete=True))
    assert not (normalized / "KOSPI.txt").exists()
    assert checkpoint.read_bytes() == before_checkpoint


def test_short_trading_atomic_failure_preserves_existing_valid_data(tmp_path):
    normalized, checkpoint = _seed_short_production(tmp_path)
    before_normalized = (normalized / "retained.txt").read_bytes()
    before_checkpoint = checkpoint.read_bytes()
    with pytest.raises(RuntimeError):
        _run_atomic(tmp_path, _atomic_fixture_runner(fail_at="KOSDAQ"))
    assert (normalized / "retained.txt").read_bytes() == before_normalized
    assert checkpoint.read_bytes() == before_checkpoint


def test_short_dry_run_fails_closed_before_any_runner(tmp_path):
    blocked = short_plan(tmp_path, operation_reviewed=False)
    assert (blocked.action, blocked.reason, blocked.retry_count) == (
        "BLOCKED", "ACTIVE_OPERATION_REVIEW_REQUIRED", 0,
    )
    not_final = short_plan(
        tmp_path, latest_finalized_market_date=date(2026, 8, 12)
    )
    assert (not_final.action, not_final.reason) == (
        "BLOCKED", "SOURCE_DATE_NOT_FINAL",
    )
    called = []
    with pytest.raises(MarketDailyIncrementalError, match="SOURCE_DATE_NOT_FINAL"):
        execute_short_selling_daily(
            not_final, project_root=tmp_path, client_factory=object(),
            runner=lambda **kwargs: called.append(kwargs),
        )
    assert called == []


def test_short_exact_date_plan_uses_bounded_scopes_and_delegates_retry_zero(tmp_path):
    trading = short_plan(tmp_path)
    investor = short_plan(tmp_path, "investor")
    assert trading.action == investor.action == "READY"
    assert trading.estimated_api_calls == 2
    assert investor.estimated_api_calls == 4
    calls = []
    result = execute_short_selling_daily(
        trading, project_root=tmp_path, client_factory="factory", throttle="throttle",
        runner=lambda **kwargs: calls.append(kwargs) or "done",
    )
    assert result == "done"
    assert calls == [{
        "dataset": "trading", "trading_dates": (TARGET,),
        "max_business_calls": 2, "project_root": tmp_path,
        "client_factory": "factory", "throttle": "throttle",
    }]
    assert not (tmp_path / "data/state/.short_selling_daily.lock").exists()


def test_short_investor_exact_date_has_four_business_and_nine_raw_calls(tmp_path):
    plan = short_plan(tmp_path, "investor")
    assert plan.estimated_api_calls == 4
    assert short_selling_raw_call_budget("investor", plan.estimated_api_calls) == 9
    assert SHORT_SELLING_FINALITY_POLICIES["investor"] == (
        "SAME_XKRX_SESSION_AFTER_1810_AS_RETRIEVED"
    )


def test_short_investor_allows_same_day_scope_while_trading_remains_t_plus_1() -> None:
    korea_today = datetime.now(ZoneInfo("Asia/Seoul")).date()

    assert len(plan_scopes("investor", (korea_today,))) == 4
    with pytest.raises(ValueError, match="trading enforces a T\\+1 minimum"):
        plan_scopes("trading", (korea_today,))


def test_short_investor_cli_rejects_non_exact_raw_budget_before_network(tmp_path):
    with pytest.raises(SystemExit, match="exact fresh-session raw budget 9"):
        market_daily_cli.main([
            "--project-root", str(tmp_path), "--lane", "short",
            "--dataset", "investor", "--market-date", TARGET.isoformat(),
            "--latest-finalized", TARGET.isoformat(), "--max-api-calls", "4",
            "--max-raw-calls", "8", "--confirm-reviewed-operation",
        ])


def test_short_balance_retained_valid_empty_requires_a_later_reviewed_successor(tmp_path):
    checkpoint = tmp_path / "data/state/kr_short_selling_balance_daily_v2.json"
    _write_json(checkpoint, {
        "dataset": "balance", "contract_version": 2, "completed": {},
        "status": "STOPPED",
        "stop_reason": "ANOMALOUS_VALID_EMPTY:20260814_KOSPI",
    })
    stopped = short_plan(
        tmp_path, "balance", market_date=date(2026, 8, 14),
        latest_finalized_market_date=date(2026, 8, 14),
        accepted_market_dates=(date(2026, 8, 14),),
    )
    assert (stopped.action, stopped.reason, stopped.estimated_api_calls) == (
        "BLOCKED", "RETAINED_VALID_EMPTY_STOP_NO_RETRY", 2,
    )

    successor_date = date(2026, 8, 18)
    unreviewed = short_plan(
        tmp_path, "balance", market_date=successor_date,
        latest_finalized_market_date=successor_date,
        accepted_market_dates=(successor_date,),
    )
    assert (unreviewed.action, unreviewed.reason) == (
        "BLOCKED", "VALID_EMPTY_SUCCESSOR_REVIEW_REQUIRED",
    )
    reviewed = short_plan(
        tmp_path, "balance", market_date=successor_date,
        latest_finalized_market_date=successor_date,
        accepted_market_dates=(successor_date,), valid_empty_successor_reviewed=True,
    )
    assert (reviewed.action, reviewed.reason, reviewed.estimated_api_calls) == (
        "READY", "EXACT_DATE_REVIEWED_AND_FINAL", 2,
    )
    assert short_selling_raw_call_budget("balance", reviewed.estimated_api_calls) == 7
    assert SHORT_SELLING_FINALITY_POLICIES["balance"] == (
        "EXPLICIT_REVIEWED_PROVIDER_PUBLICATION_ONLY"
    )


def test_short_checkpoint_produces_no_call_idempotent_plan(tmp_path):
    scopes = plan_scopes("trading", (TARGET,))
    path = tmp_path / "data/state/kr_short_selling_trading_daily_v2.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "dataset": "trading", "contract_version": 2,
        "completed": {scope.scope_id: {} for scope in scopes},
    }), encoding="utf-8")
    plan = short_plan(tmp_path)
    assert (plan.action, plan.estimated_api_calls) == ("NOOP_IDEMPOTENT", 0)
    calls = []
    result = execute_short_selling_daily(
        plan, project_root=tmp_path, client_factory=object(),
        runner=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(requested_business_calls=0),
    )
    assert result.requested_business_calls == 0 and len(calls) == 1


@pytest.mark.parametrize(
    ("dataset", "lane", "expected_name"),
    [
        ("detail", "LENDING_DAILY", "kr_stock_lending_daily"),
        ("market", "LENDING_DAILY", "kr_stock_lending_market_daily"),
        ("participant", "LENDING_DAILY", "kr_stock_lending_participant_daily"),
        ("market_liquidity", "LIQUIDITY_CREDIT_DAILY", "kr_market_liquidity_daily"),
        ("credit_balance", "LIQUIDITY_CREDIT_DAILY", "kr_credit_balance_daily"),
    ],
)
def test_data_go_registry_has_exact_date_manual_finality_gate(
    tmp_path, dataset, lane, expected_name,
):
    blocked = data_plan(tmp_path, dataset, operation_reviewed=False)
    assert blocked.action == "BLOCKED"
    ready = data_plan(tmp_path, dataset, max_api_calls=1)
    assert (ready.lane, ready.dataset, ready.action, ready.retry_count) == (
        lane, expected_name, "READY", 0,
    )


def test_lending_daily_delegates_half_open_one_date_range_and_resumes(tmp_path):
    calls = []
    plan = data_plan(tmp_path, "detail", max_api_calls=2)
    result = execute_data_go_kr_daily(
        plan, project_root=tmp_path,
        lending_runner=lambda **kwargs: calls.append(kwargs) or "lending",
        service_key="fixture", min_interval_seconds=0,
    )
    assert result == "lending"
    assert calls[0]["start_date"] == "20260813"
    assert calls[0]["end_date"] == "20260814"
    assert calls[0]["max_calls"] == 2
    assert calls[0]["max_attempts"] == 1
    assert calls[0]["resume"] is True
    assert not (tmp_path / "data/state/.lending_daily.lock").exists()


def test_liquidity_daily_delegates_exact_date_and_single_attempt_collector(tmp_path):
    calls = []
    plan = data_plan(tmp_path, "market_liquidity")
    result = execute_data_go_kr_daily(
        plan, project_root=tmp_path,
        date_runner=_successful_staged_date_runner(calls),
    )
    assert result.status == "COMPLETE"
    assert calls[0]["base_date"] == "20260813"
    assert calls[0]["max_calls"] == 1
    assert calls[0]["resume"] is True
    assert calls[0]["state_path"] != tmp_path / "data/state/kr_market_liquidity_daily.json"
    assert (tmp_path / "data/normalized/kr_market_liquidity_daily/fixture").read_text() == "new"
    checkpoint = json.loads(
        (tmp_path / "data/state/kr_market_liquidity_daily.json").read_text()
    )
    assert checkpoint["completed_partitions"] == ["20260813"]
    assert not (tmp_path / "data/state/.liquidity_credit_daily.lock").exists()


def test_liquidity_valid_empty_promotes_checkpoint_and_preserves_existing_data(tmp_path):
    normalized = tmp_path / "data/normalized/kr_market_liquidity_daily"
    normalized.mkdir(parents=True)
    (normalized / "fixture").write_text("old", encoding="utf-8")
    calls = []
    result = execute_data_go_kr_daily(
        data_plan(tmp_path, "market_liquidity"), project_root=tmp_path,
        date_runner=_successful_staged_date_runner(calls, status="VALID_EMPTY"),
    )
    assert result.status == "VALID_EMPTY"
    assert (normalized / "fixture").read_text(encoding="utf-8") == "old"
    checkpoint = json.loads(
        (tmp_path / "data/state/kr_market_liquidity_daily.json").read_text()
    )
    assert checkpoint["valid_empty_partitions"] == ["20260813"]
    assert (tmp_path / "data/landing/data_go_kr/kr_market_liquidity_daily/20260813.json").exists()


def test_liquidity_failed_stage_preserves_prior_normalized_and_checkpoint(tmp_path):
    normalized = tmp_path / "data/normalized/kr_market_liquidity_daily"
    normalized.mkdir(parents=True)
    (normalized / "fixture").write_text("old", encoding="utf-8")
    checkpoint_path = tmp_path / "data/state/kr_market_liquidity_daily.json"
    prior = {
        "dataset": "kr_market_liquidity_daily", "completed_partitions": ["20260812"],
        "valid_empty_partitions": [], "failed_partitions": {}, "staged_partitions": [],
    }
    _write_json(checkpoint_path, prior)

    def failed(**kwargs):
        _write_json(kwargs["landing_path"], [{"response": "malformed-fixture"}])
        raise ValueError("malformed fixture")

    with pytest.raises(ValueError, match="malformed fixture"):
        execute_data_go_kr_daily(
            data_plan(tmp_path, "market_liquidity"), project_root=tmp_path,
            date_runner=failed,
        )
    assert (normalized / "fixture").read_text(encoding="utf-8") == "old"
    assert json.loads(checkpoint_path.read_text(encoding="utf-8")) == prior
    assert (tmp_path / "data/landing/data_go_kr/kr_market_liquidity_daily/20260813.json").exists()


def test_liquidity_checkpoint_promotion_failure_rolls_back_normalized(tmp_path, monkeypatch):
    normalized = tmp_path / "data/normalized/kr_market_liquidity_daily"
    normalized.mkdir(parents=True)
    (normalized / "fixture").write_text("old", encoding="utf-8")
    checkpoint_path = tmp_path / "data/state/kr_market_liquidity_daily.json"
    prior = {
        "dataset": "kr_market_liquidity_daily", "completed_partitions": ["20260812"],
        "valid_empty_partitions": [], "failed_partitions": {}, "staged_partitions": [],
    }
    _write_json(checkpoint_path, prior)
    original_replace = Path.replace
    injected = False

    def fail_checkpoint_once(self, target):
        nonlocal injected
        if self.name == "staged_checkpoint.json" and not injected:
            injected = True
            raise OSError("injected checkpoint promotion failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_checkpoint_once)
    with pytest.raises(OSError, match="checkpoint promotion failure"):
        execute_data_go_kr_daily(
            data_plan(tmp_path, "market_liquidity"), project_root=tmp_path,
            date_runner=_successful_staged_date_runner([]),
        )
    assert injected is True
    assert (normalized / "fixture").read_text(encoding="utf-8") == "old"
    assert json.loads(checkpoint_path.read_text(encoding="utf-8")) == prior
    journal = json.loads((
        tmp_path / "data/state/transactions/kr_market_liquidity_daily_20260813.json"
    ).read_text(encoding="utf-8"))
    assert journal["status"] == "FAILED"


def test_liquidity_two_pass_first_capture_is_provisional_and_not_promoted(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(market_daily, "read_dataset", _fake_two_pass_read)
    calls = []
    result = execute_liquidity_credit_two_pass(
        two_pass_plan(tmp_path), project_root=tmp_path,
        date_runner=_two_pass_runner(_liquidity_row(), calls),
    )
    assert (result.status, result.pages, result.stable) == ("PROVISIONAL", 1, False)
    assert len(calls) == 1 and calls[0]["resume"] is False
    assert not (tmp_path / "data/normalized/kr_market_liquidity_daily").exists()
    state = json.loads((
        tmp_path / "data/state/finality/kr_market_liquidity_daily.json"
    ).read_text(encoding="utf-8"))
    assert state["dates"]["20260813"]["status"] == "PROVISIONAL"
    assert len(state["dates"]["20260813"]["observations"]) == 1


def test_liquidity_two_pass_identical_confirmation_promotes_then_replays_api_zero(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(market_daily, "read_dataset", _fake_two_pass_read)
    row = _liquidity_row()
    calls = []
    execute_liquidity_credit_two_pass(
        two_pass_plan(tmp_path), project_root=tmp_path,
        date_runner=_two_pass_runner(row, calls),
    )
    confirmation = two_pass_plan(tmp_path)
    assert confirmation.action == "CAPTURE_CONFIRMATION"
    result = execute_liquidity_credit_two_pass(
        confirmation, project_root=tmp_path,
        date_runner=_two_pass_runner(row, calls),
    )
    assert (result.status, result.observation_count, result.stable) == ("STABLE", 2, True)
    assert (tmp_path / "data/normalized/kr_market_liquidity_daily/row.json").exists()
    checkpoint = json.loads((
        tmp_path / "data/state/kr_market_liquidity_daily.json"
    ).read_text(encoding="utf-8"))
    assert checkpoint["completed_partitions"] == ["20260813"]
    replay_plan = two_pass_plan(tmp_path)
    assert (replay_plan.action, replay_plan.estimated_api_calls) == ("NOOP_STABLE", 0)
    replay = execute_liquidity_credit_two_pass(
        replay_plan, project_root=tmp_path,
        date_runner=lambda **kwargs: pytest.fail("stable replay called provider"),
    )
    assert (replay.status, replay.pages) == ("NOOP_STABLE", 0)
    assert len(calls) == 2


def test_liquidity_two_pass_checkpoint_failure_restores_prior_artifacts(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(market_daily, "read_dataset", _fake_two_pass_read)
    production = tmp_path / "data/normalized/kr_market_liquidity_daily"
    production.mkdir(parents=True)
    _write_json(production / "row.json", _liquidity_row(99))
    checkpoint = tmp_path / "data/state/kr_market_liquidity_daily.json"
    _write_json(checkpoint, {
        "dataset": "kr_market_liquidity_daily",
        "completed_partitions": ["20260812"],
        "valid_empty_partitions": [],
    })
    execute_liquidity_credit_two_pass(
        two_pass_plan(tmp_path), project_root=tmp_path,
        date_runner=_two_pass_runner(_liquidity_row(100), []),
    )
    before_production = (production / "row.json").read_bytes()
    before_checkpoint = checkpoint.read_bytes()
    original_replace = Path.replace
    injected = False

    def fail_staged_checkpoint_once(self, target):
        nonlocal injected
        if self.name == "staged_checkpoint.json" and not injected:
            injected = True
            raise OSError("injected two-pass checkpoint promotion failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_staged_checkpoint_once)
    with pytest.raises(OSError, match="two-pass checkpoint promotion failure"):
        execute_liquidity_credit_two_pass(
            two_pass_plan(tmp_path), project_root=tmp_path,
            date_runner=_two_pass_runner(_liquidity_row(100), []),
        )

    assert injected is True
    assert (production / "row.json").read_bytes() == before_production
    assert checkpoint.read_bytes() == before_checkpoint
    journal = json.loads((
        tmp_path / "data/state/transactions/kr_market_liquidity_daily_20260813.json"
    ).read_text(encoding="utf-8"))
    assert journal["status"] == "FAILED"


def test_liquidity_two_pass_revision_resets_confirmation_without_promotion(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(market_daily, "read_dataset", _fake_two_pass_read)
    execute_liquidity_credit_two_pass(
        two_pass_plan(tmp_path), project_root=tmp_path,
        date_runner=_two_pass_runner(_liquidity_row(100), []),
    )
    revised = execute_liquidity_credit_two_pass(
        two_pass_plan(tmp_path), project_root=tmp_path,
        date_runner=_two_pass_runner(_liquidity_row(101), []),
    )
    assert revised.status == "REVISED" and revised.stable is False
    assert not (tmp_path / "data/normalized/kr_market_liquidity_daily").exists()
    state = json.loads((
        tmp_path / "data/state/finality/kr_market_liquidity_daily.json"
    ).read_text(encoding="utf-8"))
    day = state["dates"]["20260813"]
    assert day["status"] == "REVISED" and day["revision_count"] == 1
    assert two_pass_plan(tmp_path).action == "CAPTURE_CONFIRMATION"


def test_credit_two_pass_valid_empty_requires_two_observations(tmp_path):
    first = execute_liquidity_credit_two_pass(
        two_pass_plan(tmp_path, "credit_balance"), project_root=tmp_path,
        date_runner=_two_pass_runner({}, [], status="VALID_EMPTY"),
    )
    second = execute_liquidity_credit_two_pass(
        two_pass_plan(tmp_path, "credit_balance"), project_root=tmp_path,
        date_runner=_two_pass_runner({}, [], status="VALID_EMPTY"),
    )
    assert first.status == "PROVISIONAL"
    assert second.status == "STABLE" and second.stable is True
    assert not (tmp_path / "data/normalized/kr_credit_balance_daily").exists()
    checkpoint = json.loads((
        tmp_path / "data/state/kr_credit_balance_daily.json"
    ).read_text(encoding="utf-8"))
    assert checkpoint["valid_empty_partitions"] == ["20260813"]


def test_market_liquidity_stable_empty_is_rechecked_for_late_publication(tmp_path):
    for _ in range(2):
        execute_liquidity_credit_two_pass(
            two_pass_plan(tmp_path, "market_liquidity"), project_root=tmp_path,
            date_runner=_two_pass_runner({}, [], status="VALID_EMPTY"),
        )

    replay = two_pass_plan(tmp_path, "market_liquidity")
    assert (replay.action, replay.estimated_api_calls) == (
        "CAPTURE_RECHECK_EMPTY", 1,
    )


def test_credit_two_pass_rechecks_stable_empty_and_promotes_lagged_row(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(market_daily, "read_dataset", _fake_two_pass_read)
    for _ in range(2):
        execute_liquidity_credit_two_pass(
            two_pass_plan(tmp_path, "credit_balance"), project_root=tmp_path,
            date_runner=_two_pass_runner({}, [], status="VALID_EMPTY"),
        )

    recheck = two_pass_plan(tmp_path, "credit_balance")
    assert (recheck.action, recheck.estimated_api_calls) == (
        "CAPTURE_RECHECK_EMPTY", 1,
    )
    revised = execute_liquidity_credit_two_pass(
        recheck, project_root=tmp_path,
        date_runner=_two_pass_runner(_credit_row(), []),
    )
    assert (revised.status, revised.response_status) == ("REVISED", "COMPLETE")
    assert not (tmp_path / "data/normalized/kr_credit_balance_daily").exists()

    confirmed = execute_liquidity_credit_two_pass(
        two_pass_plan(tmp_path, "credit_balance"), project_root=tmp_path,
        date_runner=_two_pass_runner(_credit_row(), []),
    )
    assert (confirmed.status, confirmed.response_status, confirmed.stable) == (
        "STABLE", "COMPLETE", True,
    )
    assert (tmp_path / "data/normalized/kr_credit_balance_daily/row.json").exists()


def test_credit_fallback_prefers_pending_complete_then_oldest_unretained(tmp_path):
    candidates = (date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12))
    state = tmp_path / "data/state/finality/kr_credit_balance_daily.json"
    _write_json(state, {
        "dataset": "kr_credit_balance_daily", "dates": {
            "20260810": {
                "status": "REVISED",
                "observations": [{"response_status": "COMPLETE"}],
            },
            "20260811": {
                "status": "STABLE", "stable_response_status": "VALID_EMPTY",
                "observations": [{"response_status": "VALID_EMPTY"}],
            },
        },
    })
    _write_json(tmp_path / "data/state/kr_credit_balance_daily.json", {
        "completed_partitions": ["20260812"],
    })

    assert select_credit_balance_fallback_date(
        project_root=tmp_path, market_date=TARGET, candidate_dates=candidates,
    ) == date(2026, 8, 10)


def test_liquidity_two_pass_failed_confirmation_preserves_production_and_evidence(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(market_daily, "read_dataset", _fake_two_pass_read)
    production = tmp_path / "data/normalized/kr_market_liquidity_daily"
    production.mkdir(parents=True)
    _write_json(production / "row.json", _liquidity_row(99))
    before = (production / "row.json").read_bytes()
    with pytest.raises(ValueError, match="observation failure"):
        execute_liquidity_credit_two_pass(
            two_pass_plan(tmp_path), project_root=tmp_path,
            date_runner=_two_pass_runner(_liquidity_row(100), [], fail=True),
        )
    assert (production / "row.json").read_bytes() == before
    state = json.loads((
        tmp_path / "data/state/finality/kr_market_liquidity_daily.json"
    ).read_text(encoding="utf-8"))
    assert state["failures"][-1]["error_type"] == "ValueError"
    assert state["failures"][-1]["landing_path"] is not None


@pytest.mark.parametrize(
    ("dataset", "state_name", "marker"),
    [
        ("detail", "kr_stock_lending_daily_historical.json", "range:20260813:20260814"),
        ("credit_balance", "kr_credit_balance_daily.json", "20260813"),
    ],
)
def test_data_go_checkpoint_makes_same_date_noop_without_runner(
    tmp_path, dataset, state_name, marker,
):
    path = tmp_path / "data/state" / state_name
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "completed_partitions": [marker], "valid_empty_partitions": [],
    }), encoding="utf-8")
    plan = data_plan(tmp_path, dataset)
    assert (plan.action, plan.estimated_api_calls) == ("NOOP_IDEMPOTENT", 0)
    calls = []
    result = execute_data_go_kr_daily(
        plan, project_root=tmp_path,
        date_runner=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(pages=0),
        lending_runner=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(api_calls=0),
    )
    assert len(calls) == 1
    assert getattr(result, "api_calls", getattr(result, "pages", None)) == 0


def test_unreadable_checkpoint_fails_closed(tmp_path):
    path = tmp_path / "data/state/kr_credit_balance_daily.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(MarketDailyIncrementalError, match="checkpoint is unreadable"):
        data_plan(tmp_path, "credit_balance")


def test_runner_failure_releases_lane_lock_and_does_not_hide_error(tmp_path):
    plan = data_plan(tmp_path, "market_liquidity")
    with pytest.raises(RuntimeError, match="injected collector failure"):
        execute_data_go_kr_daily(
            plan, project_root=tmp_path,
            date_runner=lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("injected collector failure")
            ),
        )
    assert not (tmp_path / "data/state/.liquidity_credit_daily.lock").exists()


def test_freshness_adapter_separates_operational_block_from_retained_staleness(tmp_path):
    blocked_plan = data_plan(tmp_path, "credit_balance", operation_reviewed=False)
    spec = DATASET_OPERATIONS[blocked_plan.dataset]
    blocked = health_from_exact_date_plan(
        blocked_plan, spec=spec, run_id="health-wrapper",
        actual_latest=date(2026, 8, 5),
    )
    assert blocked.freshness_status is FreshnessStatus.BLOCKED
    assert blocked.operational_eligibility is OperationalEligibility.BLOCKED
    assert blocked.blocked_reason == "ACTIVE_OPERATION_REVIEW_REQUIRED"

    ready_plan = data_plan(tmp_path, "credit_balance")
    stale = health_from_exact_date_plan(
        ready_plan, spec=spec, run_id="health-wrapper",
        actual_latest=date(2026, 8, 5),
    )
    assert stale.freshness_status is FreshnessStatus.STALE
    assert stale.operational_eligibility is spec.operational_eligibility
