from datetime import date
from pathlib import Path

import pytest

import stock_data.orchestration.derivatives_daily_incremental as module
from stock_data.contracts.derivatives_investor_authority import (
    DERIVATIVES_INVESTOR_AUTHORITIES,
    InvestorDataRole,
    LS_T8462_DERIVATIVES_INVESTOR,
    OFFICIAL_KRX_DERIVATIVES_INVESTOR,
)
from stock_data.contracts.derivatives_price_authority import (
    DATA_GO_KR_KOSPI200_DERIVATIVES_PRICE,
)
from stock_data.orchestration.derivatives_daily_incremental import (
    DerivativesDailyIncrementalError,
    STAGE_ORDER,
    StageCandidate,
    execute_derivatives_daily,
    plan_derivatives_daily,
)


TARGET = date(2026, 8, 13)
PRIOR = date(2026, 8, 12)


def plan(root: Path, **overrides):
    values = {
        "project_root": root, "market_date": TARGET,
        "latest_finalized_market_date": TARGET,
        "accepted_market_dates": (TARGET,), "source_operation_reviewed": True,
        "prior_option_observation_date": PRIOR,
        "next_option_observation_reviewed": True,
    }
    values.update(overrides)
    return plan_derivatives_daily(**values)


def topology(root: Path):
    outputs = {stage: root / "production" / stage for stage in STAGE_ORDER}
    for stage, path in outputs.items():
        path.mkdir(parents=True)
        (path / "history.txt").write_text(f"old-{stage}", encoding="utf-8")

    calls = []
    builders = {}
    for index, stage in enumerate(STAGE_ORDER):
        def build(candidate_root, scope, prior, *, stage=stage, index=index):
            assert tuple(prior) == STAGE_ORDER[:index]
            candidate_root.mkdir(parents=True)
            (candidate_root / "history.txt").write_text(f"old-{stage}", encoding="utf-8")
            (candidate_root / "affected.txt").write_text(TARGET.isoformat(), encoding="utf-8")
            calls.append((stage, scope.input_dates, scope.output_dates))
            return StageCandidate(stage, candidate_root, scope.output_dates, True, True)
        builders[stage] = build
    return builders, outputs, calls


def test_plan_fails_closed_and_wall_scope_covers_target_and_immediate_successor(tmp_path):
    blocked = plan(tmp_path, source_operation_reviewed=False)
    assert (blocked.action, blocked.reason) == (
        "BLOCKED", "AUTHORITATIVE_SOURCE_OPERATION_REVIEW_REQUIRED",
    )
    no_prior = plan(tmp_path, prior_option_observation_date=None)
    assert no_prior.reason == "PRIOR_OPTION_OBSERVATION_REQUIRED_FOR_WALL_CHANGE"
    unreviewed_next = plan(tmp_path, next_option_observation_reviewed=False)
    assert unreviewed_next.reason == "NEXT_OPTION_OBSERVATION_REVIEW_REQUIRED_FOR_WALL_SCOPE"
    successor = date(2026, 8, 14)
    ready = plan(tmp_path, next_option_observation_date=successor)
    assert ready.action == "READY" and ready.retry_count == 0
    assert ready.scopes["source"].input_dates == (TARGET,)
    assert ready.scopes["wall"].input_dates == (PRIOR, TARGET, successor)
    assert ready.scopes["wall"].output_dates == (TARGET, successor)
    assert ready.affected_dates == (TARGET, successor)
    assert all(
        scope.output_dates == (TARGET,)
        for stage, scope in ready.scopes.items() if stage != "wall"
    )


def test_plan_latest_append_has_no_successor_only_after_explicit_review(tmp_path):
    ready = plan(tmp_path, next_option_observation_date=None)
    assert ready.action == "READY"
    assert ready.scopes["wall"].input_dates == (PRIOR, TARGET)
    assert ready.scopes["wall"].output_dates == (TARGET,)
    assert ready.affected_dates == (TARGET,)


def test_derivatives_price_authority_approves_completed_successor_session_rule():
    authority = DATA_GO_KR_KOSPI200_DERIVATIVES_PRICE
    assert authority.operations == (
        "getStockFuturesPriceInfo", "getOptionsPriceInfo",
    )
    assert authority.session == "KRX_REGULAR_SESSION"
    assert authority.source_date_field == "basDt"
    assert authority.source_date_semantics == "EXACT_REQUESTED_TRADING_DATE_ONLY"
    assert authority.permission_status == "ACTIVE_EXACT_DATE_OPERATION_APPROVED"
    assert authority.finality_status == "EXPLICIT_FINAL_DATE_RULE_APPROVED"
    assert authority.observation_calendar == "XKRX"
    assert authority.provider_availability_policy == "EXACT_BASDT_AFTER_COMPLETED_SUCCESSOR_XKRX_SESSION"
    assert authority.expected_lag_policy == "T_PLUS_1_COMPLETED_XKRX_SESSION"
    assert authority.finality_policy == "TARGET_REQUIRES_A_LATER_COMPLETED_XKRX_SESSION"
    assert authority.live_validation_ready
    assert not authority.fallback_allowed and not authority.silent_merge_allowed


def test_source_to_wall_transaction_promotes_all_stages_and_replay_is_noop(tmp_path):
    current = plan(tmp_path)
    builders, outputs, calls = topology(tmp_path)
    result = execute_derivatives_daily(
        current, project_root=tmp_path, builders=builders, output_roots=outputs,
    )
    assert result["status"] == "AFFECTED_DATE_COMPLETE"
    assert [value[0] for value in calls] == list(STAGE_ORDER)
    assert all((outputs[stage] / "affected.txt").read_text() == TARGET.isoformat() for stage in STAGE_ORDER)
    assert not (tmp_path / "data/state/.derivatives_price_daily.lock").exists()
    assert not (tmp_path / "data/state/derivatives_price_daily_dag.transaction.json").exists()

    repeated = plan(tmp_path)
    assert repeated.action == "NOOP_IDEMPOTENT"
    noop = execute_derivatives_daily(
        repeated, project_root=tmp_path,
        builders={stage: lambda *args: pytest.fail("builder must not run") for stage in STAGE_ORDER},
        output_roots=outputs,
    )
    assert noop["status"] == "NOOP_IDEMPOTENT"

    conflict = plan(tmp_path, next_option_observation_date=date(2026, 8, 14))
    assert (conflict.action, conflict.reason) == (
        "BLOCKED", "CHECKPOINT_AFFECTED_DATE_SCOPE_CONFLICT",
    )


def test_failure_after_partial_promotion_rolls_back_every_output(tmp_path, monkeypatch):
    current = plan(tmp_path)
    builders, outputs, _ = topology(tmp_path)
    original = module._atomic_json

    def fail_after_bridge(path, payload):
        original(path, payload)
        if isinstance(payload, dict) and payload.get("phase") == "PROMOTED_BRIDGE":
            raise OSError("injected promotion failure")

    monkeypatch.setattr(module, "_atomic_json", fail_after_bridge)
    with pytest.raises(OSError, match="injected promotion failure"):
        execute_derivatives_daily(
            current, project_root=tmp_path, builders=builders, output_roots=outputs,
        )
    assert all((outputs[stage] / "history.txt").read_text() == f"old-{stage}" for stage in STAGE_ORDER)
    assert all(not (outputs[stage] / "affected.txt").exists() for stage in STAGE_ORDER)
    assert not (tmp_path / "data/state/derivatives_price_daily_dag.json").exists()
    assert not (tmp_path / "data/state/derivatives_price_daily_dag.transaction.json").exists()


def test_candidate_validation_or_affected_date_mismatch_stops_before_promotion(tmp_path):
    current = plan(tmp_path)
    builders, outputs, _ = topology(tmp_path)
    builders["pcr"] = lambda root, scope, prior: StageCandidate(
        "pcr", root, (PRIOR,), False, True,
    )
    with pytest.raises(DerivativesDailyIncrementalError, match="affected dates differ"):
        execute_derivatives_daily(
            current, project_root=tmp_path, builders=builders, output_roots=outputs,
        )
    assert all((outputs[stage] / "history.txt").read_text() == f"old-{stage}" for stage in STAGE_ORDER)


def test_existing_transaction_journal_fails_closed_without_touching_outputs(tmp_path):
    current = plan(tmp_path)
    builders, outputs, _ = topology(tmp_path)
    journal = tmp_path / "data/state/derivatives_price_daily_dag.transaction.json"
    journal.parent.mkdir(parents=True)
    journal.write_text('{"phase":"UNKNOWN"}', encoding="utf-8")
    with pytest.raises(DerivativesDailyIncrementalError, match="reviewed recovery"):
        execute_derivatives_daily(
            current, project_root=tmp_path, builders=builders, output_roots=outputs,
        )
    assert journal.exists()
    assert all((outputs[stage] / "history.txt").read_text() == f"old-{stage}" for stage in STAGE_ORDER)


def test_output_topology_cannot_escape_project_or_alias_targets(tmp_path):
    current = plan(tmp_path)
    builders, outputs, _ = topology(tmp_path)
    outputs["wall"] = tmp_path.parent / "outside-wall"
    with pytest.raises(DerivativesDailyIncrementalError, match="escapes project"):
        execute_derivatives_daily(
            current, project_root=tmp_path, builders=builders, output_roots=outputs,
        )


def test_official_canonical_and_ls_descriptive_authorities_cannot_merge_or_fallback():
    assert len(DERIVATIVES_INVESTOR_AUTHORITIES) == 2
    assert OFFICIAL_KRX_DERIVATIVES_INVESTOR.role is InvestorDataRole.OFFICIAL_CANONICAL
    assert LS_T8462_DERIVATIVES_INVESTOR.role is InvestorDataRole.PROVIDER_DESCRIPTIVE_CROSS_CHECK
    assert OFFICIAL_KRX_DERIVATIVES_INVESTOR.permitted_layer != LS_T8462_DERIVATIVES_INVESTOR.permitted_layer
    assert LS_T8462_DERIVATIVES_INVESTOR.predictive_use == "RESEARCH_ONLY_NON_PREDICTIVE"
    assert all(not item.fallback_allowed and not item.silent_merge_allowed for item in DERIVATIVES_INVESTOR_AUTHORITIES)
