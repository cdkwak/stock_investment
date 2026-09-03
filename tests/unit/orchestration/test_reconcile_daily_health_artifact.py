import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_data.orchestration import runtime_coverage


PATH = Path("scripts/maintenance/reconcile_daily_health_artifact.py")
SPEC = importlib.util.spec_from_file_location("reconcile_daily_health_artifact", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_freshness_is_independent_of_operational_block():
    assert MODULE._freshness({"actual_latest": "2026-08-14", "expected_latest": "2026-08-14", "freshness_status": "BLOCKED"}) == "CURRENT"
    assert MODULE._freshness({"actual_latest": "2026-08-13", "expected_latest": "2026-08-14"}) == "STALE"
    assert MODULE._freshness({"actual_latest": None, "expected_latest": "2026-08-14"}) == "UNKNOWN"


def test_reconcile_rejects_partial_registry():
    with pytest.raises(ValueError, match="42-entry"):
        MODULE.reconcile({"datasets": []}, run_id="x", as_of="2026-08-18T18:00:00+09:00")


def test_reconcile_applies_explicit_live_observation_override():
    rows = []
    for dataset_id in MODULE.DATASET_OPERATIONS:
        rows.append({
            "dataset_id": dataset_id, "actual_latest": None,
            "expected_latest": None, "freshness_status": "UNKNOWN",
        })
    result = MODULE.reconcile(
        {"datasets": rows}, run_id="live", as_of="2026-08-18T19:00:00+09:00",
        overrides={"fred_vix_daily": {
            "actual_latest": "2026-08-14", "expected_latest": "2026-08-14",
            "finality_classification": "AS_RETRIEVED",
        }},
    )
    row = next(item for item in result["datasets"] if item["dataset_id"] == "fred_vix_daily")
    assert row["freshness_classification"] == "CURRENT"
    assert row["finality_classification"] == "AS_RETRIEVED"


def test_universe_health_v2_preserves_all_axes_without_inventing_expected_dates():
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]
    core = MODULE.reconcile(
        {"datasets": rows}, run_id="universe-v2", as_of="2026-08-18T23:00:00+09:00",
    )
    result = MODULE.reconcile_universe(core)
    assert result["dataset_count"] == 85
    assert result["core_operations_count"] == 42
    assert result["automation_enabled_count"] == 41
    assert result["operations_registry_count"] == 42
    assert result["core_operation_missing"] == []
    assert result["generated_at"] == "2026-08-18T23:00:00+09:00"
    assert result["core_reference_time"] == "2026-08-18T23:00:00+09:00"
    assert result["dimension_summary"]["grain"]["DAILY"] == 65
    assert result["dimension_summary"]["operational"]["BLOCKED"] == 8
    assert result["schema_version"] == 2
    assert sum(result["dimension_summary"]["display_consumer_eligibility"].values()) == 85
    assert sum(result["dimension_summary"]["research_consumer_eligibility"].values()) == 85
    assert sum(result["dimension_summary"]["predictive_consumer_eligibility"].values()) == 85
    assert all(
        row["display_consumer_eligibility"]
        and row["display_consumer_reason"]
        and row["research_consumer_eligibility"]
        and row["research_consumer_reason"]
        and row["predictive_consumer_eligibility"]
        and row["predictive_consumer_reason"]
        for row in result["datasets"]
    )
    assert sum(row["gap_status"] == "CALENDAR_RESOLVED" for row in result["datasets"]) > 0
    outside_core = next(row for row in result["datasets"] if row["dataset"] == "kr_equity_foreign_ownership_daily")
    assert outside_core["expected"] == "2026-08-18"
    assert outside_core["freshness"] == "STALE"
    assert outside_core["pit"] == "PIT_BLOCKED"
    assert outside_core["display_consumer_eligibility"] == "BLOCKED"
    assert outside_core["research_consumer_eligibility"] == "LIMITED"
    assert outside_core["predictive_consumer_eligibility"] == "BLOCKED"
    intraday = next(row for row in result["datasets"] if row["dataset"] == "market_price_60m_observation")
    assert intraday["grain"] == "INTRADAY"
    assert intraday["refresh"] == "STATIC_COMPLETE"
    assert intraday["operational"] == "NOT_APPLICABLE"
    assert intraday["automation_policy"] == "NO_REFRESH"
    assert intraday["automation_enabled"] is False
    assert intraday["scheduler_lane"] == "NO_SCHEDULER_LANE"
    assert intraday["scheduler_management"] == "NO_REFRESH"
    assert intraday["latest_complete_session"] is None
    assert intraday["expected_bars"] is None
    assert intraday["provider"] is None

    display_without_predictive = next(
        row for row in result["datasets"]
        if row["dataset"] == "fred_treasury_yield_daily"
    )
    assert display_without_predictive["display_consumer_eligibility"] == "ELIGIBLE"
    assert display_without_predictive["predictive_consumer_eligibility"] == "BLOCKED"
    pit_safe = next(
        row for row in result["datasets"] if row["dataset"] == "kr_kospi200_index_daily"
    )
    assert pit_safe["predictive_consumer_eligibility"] == "ELIGIBLE"


def test_kr_etf_health_rows_use_retained_latest_and_post_close_expectation(
    tmp_path, monkeypatch,
) -> None:
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]
    monkeypatch.setattr(
        MODULE, "validated_runtime_coverage",
        lambda _root: SimpleNamespace(
            latest={
                "kr_etf_master": "2026-09-02",
                "kr_etf_price_daily": "2026-09-02",
            },
            failures={},
        ),
    )

    result = MODULE.reconcile_universe({
        "run_id": "kr-etf-health",
        "as_of": "2026-09-02T20:31:00+09:00",
        "datasets": rows,
    }, project_root=tmp_path)
    etf_rows = {
        row["dataset"]: row for row in result["datasets"]
        if row["dataset"] in {"kr_etf_master", "kr_etf_price_daily"}
    }

    assert set(etf_rows) == {"kr_etf_master", "kr_etf_price_daily"}
    assert all(row["latest"] == "2026-09-02" for row in etf_rows.values())
    assert all(row["expected"] == "2026-09-02" for row in etf_rows.values())
    assert all(row["freshness"] == "CURRENT" for row in etf_rows.values())
    assert all(row["scheduler_lane"] == "KR_ETF_PRICE_DAILY" for row in etf_rows.values())
    assert all(row["automation_enabled"] is True for row in etf_rows.values())
    assert all(row["predictive_consumer_eligibility"] == "BLOCKED" for row in etf_rows.values())


def test_core_health_projection_serializes_consumer_triage_from_typed_universe():
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]

    result = MODULE.reconcile(
        {"datasets": rows}, run_id="consumer-triad",
        as_of="2026-08-18T23:00:00+09:00",
    )

    assert all(
        all(row[field] for field in (
            "display_consumer_eligibility", "display_consumer_reason",
            "research_consumer_eligibility", "research_consumer_reason",
            "predictive_consumer_eligibility", "predictive_consumer_reason",
        ))
        for row in result["datasets"]
    )
    fred = next(
        row for row in result["datasets"] if row["dataset_id"] == "fred_vix_daily"
    )
    assert fred["display_consumer_eligibility"] == "ELIGIBLE"
    assert fred["research_consumer_eligibility"] == "ELIGIBLE"
    assert fred["predictive_consumer_eligibility"] == "BLOCKED"
    assert fred["predictive_consumer_reason"] == "PREDICTIVE_PIT_LIMITED"


def test_universe_health_accepts_historical_core_subset_and_exposes_registry_gap():
    omitted = {
        "kr_index_constituent_daily",
        "kr_kospi200_constituent_price_daily",
        "kr_kospi200_breadth_daily",
    }
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS if dataset_id not in omitted]

    result = MODULE.reconcile_universe({
        "run_id": "historical-subset",
        "as_of": "2026-08-18T23:00:00+09:00",
        "datasets": rows,
    })

    assert result["dataset_count"] == 85
    assert result["core_operations_count"] == 39
    assert result["operations_registry_count"] == 42
    assert result["core_operation_missing"] == sorted(omitted)
    bounded = next(
        row for row in result["datasets"]
        if row["dataset"] == "kr_kospi200_breadth_daily"
    )
    assert bounded["latest"] == "2026-08-25"
    assert bounded["operational"] == "READY_WITH_FINALITY_GATE"


def test_universe_health_rejects_unknown_core_identity():
    with pytest.raises(ValueError, match="outside the operations registry"):
        MODULE.reconcile_universe({
            "as_of": "2026-08-18T23:00:00+09:00",
            "datasets": [{"dataset_id": "unknown_dataset"}],
        })


def test_universe_health_can_recompute_expected_dates_at_execution_time():
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]
    core = MODULE.reconcile(
        {"datasets": rows}, run_id="dynamic-health",
        as_of="2026-08-18T20:00:00+09:00",
    )

    result = MODULE.reconcile_universe(
        core, as_of_override="2026-08-19T19:45:00+09:00",
    )

    assert result["as_of"] == "2026-08-19T19:45:00+09:00"
    kr_index = next(row for row in result["datasets"] if row["dataset"] == "kr_index_daily")
    assert kr_index["latest"] == "2026-08-19"
    assert kr_index["expected"] == "2026-08-19"
    assert kr_index["freshness"] == "CURRENT"


def test_universe_health_treats_one_session_as_expected_lag_before_daily_task():
    rows = [{
        "dataset_id": dataset_id,
        "actual_latest": "2026-08-25" if dataset_id == "global_index_price_daily" else None,
        "expected_latest": None,
        "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]

    before = MODULE.reconcile_universe({
        "run_id": "pre-occurrence", "as_of": "2026-08-27T05:50:00+09:00",
        "datasets": rows,
    })
    after = MODULE.reconcile_universe({
        "run_id": "post-occurrence", "as_of": "2026-08-27T06:21:00+09:00",
        "datasets": rows,
    })

    before_row = next(
        row for row in before["datasets"] if row["dataset"] == "global_index_price_daily"
    )
    after_row = next(
        row for row in after["datasets"] if row["dataset"] == "global_index_price_daily"
    )
    assert before_row["expected"] == "2026-08-26"
    assert before_row["freshness"] == "EXPECTED_LAG"
    assert after_row["freshness"] == "STALE"


def test_kr_post_close_outputs_wait_for_2030_occurrence_before_stale(
    tmp_path, monkeypatch,
):
    cases = {
        "kr_index_constituent_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_constituent_price_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_breadth_daily": ("2026-08-31", "2026-09-01"),
        "kr_credit_balance_daily": ("2026-09-01", "2026-09-02"),
        "kr_kospi200_futures_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_futures_nearest_listed_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_futures_provider_bridge_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_option_pcr_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_option_walls_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_options_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_options_provider_bridge_daily": ("2026-08-31", "2026-09-01"),
        "kr_market_liquidity_daily": ("2026-09-01", "2026-09-02"),
        "kr_short_selling_balance_daily": ("2026-08-28", "2026-08-31"),
        "kr_short_selling_investor_daily": ("2026-09-01", "2026-09-02"),
        "kr_treasury_yield_daily": ("2026-08-31", "2026-09-01"),
    }
    rows = [{
        "dataset_id": dataset_id,
        "actual_latest": cases[dataset_id][0] if dataset_id in cases else None,
        "expected_latest": None,
        "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]
    monkeypatch.setattr(
        MODULE,
        "validated_runtime_coverage",
        lambda _root: SimpleNamespace(
            latest={dataset: actual for dataset, (actual, _expected) in cases.items()},
            failures={},
        ),
    )

    before = MODULE.reconcile_universe({
        "run_id": "pre-kospi200-occurrence",
        "as_of": "2026-09-02T18:15:00+09:00",
        "datasets": rows,
    }, project_root=tmp_path)
    after = MODULE.reconcile_universe({
        "run_id": "post-kospi200-occurrence",
        "as_of": "2026-09-02T20:31:00+09:00",
        "datasets": rows,
    }, project_root=tmp_path)

    before_rows = {
        row["dataset"]: row for row in before["datasets"]
        if row["dataset"] in cases
    }
    after_rows = {
        row["dataset"]: row for row in after["datasets"]
        if row["dataset"] in cases
    }
    assert set(before_rows) == set(cases)
    assert all(
        before_rows[dataset]["expected"] == expected
        for dataset, (_actual, expected) in cases.items()
    )
    assert {
        dataset: (row["latest"], row["freshness"])
        for dataset, row in before_rows.items()
    } == {
        dataset: (actual, "EXPECTED_LAG")
        for dataset, (actual, _expected) in cases.items()
    }
    assert {
        dataset: row["freshness"] for dataset, row in after_rows.items()
    } == {dataset: "STALE" for dataset in cases}


def test_universe_health_prefers_contract_validated_runtime_coverage(tmp_path, monkeypatch):
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]
    core = MODULE.reconcile(
        {"datasets": rows}, run_id="runtime-health",
        as_of="2026-08-18T20:00:00+09:00",
    )
    monkeypatch.setattr(
        MODULE, "validated_runtime_coverage",
        lambda _root: SimpleNamespace(
            latest={
                "global_commodity_futures_daily": "2026-08-18",
                "kr_index_daily": "2026-08-18",
            },
            failures={"global_index_price_daily": "PermissionError"},
        ),
    )

    result = MODULE.reconcile_universe(
        core, as_of_override="2026-08-19T19:45:00+09:00",
        project_root=tmp_path,
    )

    futures = next(
        row for row in result["datasets"]
        if row["dataset"] == "global_commodity_futures_daily"
    )
    blocked_probe = next(
        row for row in result["datasets"]
        if row["dataset"] == "global_index_price_daily"
    )
    regressed = next(
        row for row in result["datasets"] if row["dataset"] == "kr_index_daily"
    )
    assert futures["latest"] == "2026-08-18"
    assert futures["runtime_coverage"] == "VALIDATED"
    assert blocked_probe["runtime_coverage"] == "FAILED:PermissionError"
    assert blocked_probe["freshness"] == "UNKNOWN"
    assert regressed["latest"] == "2026-08-18"
    assert regressed["expected"] == "2026-08-19"
    assert regressed["freshness"] == "STALE"
    assert result["runtime_coverage_validated_count"] == 2
    assert result["runtime_coverage_failure_count"] == 1


def test_scheduled_manual_publication_observation_is_expected_lag_when_validated(
    tmp_path, monkeypatch,
):
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]
    monkeypatch.setattr(
        MODULE, "validated_runtime_coverage",
        lambda _root: SimpleNamespace(
            latest={
                "bok_ecos_kr_treasury_yield_source_observation": "2026-08-13",
            },
            failures={},
        ),
    )

    result = MODULE.reconcile_universe({
        "run_id": "bok-observation", "as_of": "2026-08-27T05:50:00+09:00",
        "datasets": rows,
    }, project_root=tmp_path)
    bok = next(
        row for row in result["datasets"]
        if row["dataset"] == "bok_ecos_kr_treasury_yield_source_observation"
    )

    assert bok["expected"] is None
    assert bok["freshness"] == "EXPECTED_LAG"


def test_runtime_coverage_latest_year_reader_supports_nested_market_partitions(
    tmp_path, monkeypatch,
):
    contract = SimpleNamespace(
        column_names=("date", "market", "value"),
        sort_key=("date", "market"),
    )
    for market, latest in (("KOSPI", "2026-08-20"), ("KOSDAQ", "2026-08-19")):
        target = tmp_path / f"market={market}" / "year=2026"
        target.mkdir(parents=True)
        pd.DataFrame({
            "date": [latest], "market": [market], "value": [1],
        }).to_parquet(target / "data.parquet", index=False)
    monkeypatch.setattr(
        runtime_coverage, "restore_contract_dates", lambda frame, _contract: frame,
    )

    reader = runtime_coverage._latest_year_contract_reader(
        contract, lambda frame: None,
    )
    frame = reader(tmp_path)

    assert set(frame["market"]) == {"KOSPI", "KOSDAQ"}
    assert pd.to_datetime(frame["date"]).max().date().isoformat() == "2026-08-20"


def test_runtime_coverage_counts_valid_empty_finality_observation_without_numeric_row(
    tmp_path, monkeypatch,
):
    normalized = pd.DataFrame({"date": ["2026-08-06"]})
    monkeypatch.setattr(
        runtime_coverage, "_latest_year_contract_reader",
        lambda _contract, _validator: lambda _root: normalized,
    )
    root = tmp_path / "data/normalized/kr_market_liquidity_daily"
    state = tmp_path / "data/state/finality/kr_market_liquidity_daily.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        '{"dataset":"kr_market_liquidity_daily","dates":{'
        '"20260825":{"market_date":"2026-08-25","status":"STABLE",'
        '"observations":[{}]},'
        '"20260826":{"market_date":"2026-08-26","status":"PROVISIONAL",'
        '"observations":[{}]}},"failures":[]}',
        encoding="utf-8",
    )

    reader = runtime_coverage._latest_finality_observation_reader(
        SimpleNamespace(), lambda _frame: None, "kr_market_liquidity_daily",
    )
    frame = reader(root)

    assert frame["date"].iloc[0].isoformat() == "2026-08-26"


def test_runtime_coverage_probes_all_automated_partitioned_daily_outputs():
    probes = {probe.dataset_id: probe.relative_root for probe in runtime_coverage._PROBES}

    assert {
        "kr_equity_price_daily": "data/normalized/kr_equity_price_daily",
        "kr_equity_market_cap_daily": "data/normalized/kr_equity_market_cap_daily",
        "kr_equity_universe_daily": "data/normalized/kr_equity_universe_daily",
        "kr_equity_canonical_universe_daily": (
            "data/published/kr_equity_canonical_universe_daily"
        ),
        "kr_market_breadth_daily": "data/derived/kr_market_breadth_daily",
        "kr_index_fundamental_daily": (
            "data/normalized/kr_index_fundamental_daily"
        ),
        "kr_index_constituent_daily": "data/normalized/kr_index_constituent_daily",
        "kr_kospi200_constituent_price_daily": (
            "data/published/kr_kospi200_constituent_price_daily"
        ),
        "kr_kospi200_breadth_daily": "data/derived/kr_kospi200_breadth_daily",
        "kr_kospi200_futures_daily": (
            "data/normalized/kr_kospi200_futures_daily"
        ),
        "kr_kospi200_options_daily": (
            "data/normalized/kr_kospi200_options_daily"
        ),
        "kr_kospi200_futures_provider_bridge_daily": (
            "data/published/c007_kospi200_derivatives_bridge/"
            "kr_kospi200_futures_provider_bridge_daily"
        ),
        "kr_kospi200_options_provider_bridge_daily": (
            "data/published/c007_kospi200_derivatives_bridge/"
            "kr_kospi200_options_provider_bridge_daily"
        ),
        "kr_kospi200_futures_nearest_listed_daily": (
            "data/derived/kr_kospi200_futures_nearest_listed_daily"
        ),
        "kr_kospi200_option_pcr_daily": (
            "data/derived/kr_kospi200_option_pcr_daily"
        ),
        "kr_kospi200_option_walls_daily": (
            "artifacts/analysis/kospi200_option_wall_recent_250.csv"
        ),
    }.items() <= probes.items()


def test_derivatives_runtime_coverage_requires_checkpoint_exact_latest(
    tmp_path, monkeypatch,
):
    state = tmp_path / "data/state/derivatives_price_daily_live.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        '{"version":1,"dataset":"derivatives_price_daily_live",'
        '"completed_dates":["2026-08-19"],"last_api_calls":0,'
        '"retry_count":0}',
        encoding="utf-8",
    )
    probe = runtime_coverage._CoverageProbe(
        "kr_kospi200_futures_daily", "unused", "date",
        lambda _path: pd.DataFrame({"date": ["2026-08-20"]}),
    )
    monkeypatch.setattr(runtime_coverage, "_PROBES", (probe,))

    result = runtime_coverage.validated_runtime_coverage(tmp_path)

    assert result.latest == {}
    assert result.failures == {"kr_kospi200_futures_daily": "ValueError"}


def test_derivatives_runtime_coverage_fails_closed_before_artifact_read(
    tmp_path, monkeypatch,
):
    state = tmp_path / "data/state/derivatives_price_daily_live.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    probe = runtime_coverage._CoverageProbe(
        "kr_kospi200_option_walls_daily", "unused", "date",
        lambda _path: pytest.fail("invalid checkpoint must gate artifact read"),
    )
    monkeypatch.setattr(runtime_coverage, "_PROBES", (probe,))

    result = runtime_coverage.validated_runtime_coverage(tmp_path)

    assert result.latest == {}
    assert result.failures == {"kr_kospi200_option_walls_daily": "ValueError"}
