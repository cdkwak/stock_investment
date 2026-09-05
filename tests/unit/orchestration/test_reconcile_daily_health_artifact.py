import importlib.util
from datetime import date
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest

from stock_data.orchestration import runtime_coverage


PATH = Path("scripts/maintenance/reconcile_daily_health_artifact.py")
SPEC = importlib.util.spec_from_file_location("reconcile_daily_health_artifact", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def tmp_path() -> Path:
    """Avoid Python 3.13's Windows 0700 pytest temporary ACL."""

    root = (
        Path(__file__).parents[3]
        / ".tmp/agents/health-retained-coverage-20260905/fixtures"
        / uuid4().hex
    )
    root.mkdir(parents=True)
    return root


def test_universe_writer_also_updates_stable_latest_pointer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        MODULE, "probe_retained_coverage",
        lambda _root, _dataset_id: None,
    )
    core = tmp_path / "artifacts/daily_health/core.json"
    core.parent.mkdir(parents=True)
    core.write_text(json.dumps({
        "run_id": "stable-latest",
        "as_of": "2026-09-04T20:30:00+09:00",
        "datasets": [],
    }), encoding="utf-8")
    compatibility = core.parent / "universe_data_v2_20260819.json"

    MODULE.write_universe_health_artifact(
        project_root=tmp_path, core_artifact=core,
        universe_output=compatibility,
        as_of="2026-09-04T20:30:00+09:00",
    )

    latest = core.parent / "universe_data_v2_latest.json"
    assert latest.read_bytes() == compatibility.read_bytes()
    assert json.loads(latest.read_text(encoding="utf-8"))["run_id"] == "stable-latest"


def test_freshness_is_independent_of_operational_block():
    assert MODULE._freshness({"actual_latest": "2026-08-14", "expected_latest": "2026-08-14", "freshness_status": "BLOCKED"}) == "CURRENT"
    assert MODULE._freshness({"actual_latest": "2026-08-13", "expected_latest": "2026-08-14"}) == "STALE"
    assert MODULE._freshness({"actual_latest": None, "expected_latest": "2026-08-14"}) == "UNKNOWN"


def test_reconcile_rejects_partial_registry():
    with pytest.raises(ValueError, match="50-entry"):
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
    assert result["dataset_count"] == 94
    assert result["core_operations_count"] == 50
    assert result["automation_enabled_count"] == 51
    assert result["operations_registry_count"] == 50
    assert result["core_operation_missing"] == []
    assert result["generated_at"] == "2026-08-18T23:00:00+09:00"
    assert result["core_reference_time"] == "2026-08-18T23:00:00+09:00"
    assert result["dimension_summary"]["grain"]["DAILY"] == 73
    assert result["dimension_summary"]["operational"]["BLOCKED"] == 8
    assert result["schema_version"] == 2
    assert sum(result["dimension_summary"]["display_consumer_eligibility"].values()) == 94
    assert sum(result["dimension_summary"]["research_consumer_eligibility"].values()) == 94
    assert sum(result["dimension_summary"]["predictive_consumer_eligibility"].values()) == 94
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
    assert outside_core["freshness"] == "NOT_APPLICABLE"
    assert outside_core["display_status"] == "PRESERVED"
    assert outside_core["display_reason"] == "수동 수집 전용 · 표는 손으로 적은 값"
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
        MODULE, "probe_retained_coverage",
        lambda _root, dataset_id: {
                "kr_etf_master": "2026-09-02",
                "kr_etf_price_daily": "2026-09-02",
            }.get(dataset_id) and ("2026-08-24", "2026-09-02"),
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


def test_new_global_price_rows_project_current_at_the_retained_2221_kst_run() -> None:
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]

    result = MODULE.reconcile_universe({
        "run_id": "global-price-health",
        "as_of": "2026-09-04T22:21:56+09:00",
        "datasets": rows,
    })
    projected = {row["dataset"]: row for row in result["datasets"]}
    equity = projected["global_equity_price_daily"]
    quotes = projected["tossinvest_us_quote_30m"]

    assert equity["latest"] == equity["expected"] == "2026-09-03"
    assert equity["display_status"] == "CURRENT"
    assert equity["scheduler_lane"] == "GLOBAL_EQUITY_DAILY"
    assert quotes["latest"] == "2026-09-04"
    assert quotes["grain"] == "INTRADAY"
    assert quotes["display_status"] == "CURRENT"
    assert quotes["scheduler_lane"] == "TOSSINVEST_US_QUOTES_30M"


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

    assert result["dataset_count"] == 94
    assert result["core_operations_count"] == 47
    assert result["operations_registry_count"] == 50
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


def test_universe_health_treats_one_session_as_pending_until_daily_task_grace():
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
        "run_id": "post-occurrence", "as_of": "2026-08-27T06:36:00+09:00",
        "datasets": rows,
    })

    before_row = next(
        row for row in before["datasets"] if row["dataset"] == "global_index_price_daily"
    )
    after_row = next(
        row for row in after["datasets"] if row["dataset"] == "global_index_price_daily"
    )
    assert before_row["expected"] == "2026-08-26"
    assert before_row["freshness"] == "STALE"
    assert before_row["display_status"] == "CURRENT"
    assert before_row["pending_until"] == "06:35"
    assert before_row["due_at"] == "2026-08-27T06:35:00+09:00"
    assert after_row["freshness"] == "STALE"
    assert after_row["pending_until"] is None


def test_kr_post_close_outputs_wait_for_2030_occurrence_before_stale(
    tmp_path, monkeypatch,
):
    cases = {
        "kr_index_constituent_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_constituent_price_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_breadth_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_futures_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_futures_nearest_listed_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_futures_provider_bridge_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_option_pcr_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_option_walls_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_options_daily": ("2026-08-31", "2026-09-01"),
        "kr_kospi200_options_provider_bridge_daily": ("2026-08-31", "2026-09-01"),
        "kr_market_liquidity_daily": ("2026-09-01", "2026-09-01"),
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
        "probe_retained_coverage",
        lambda _root, dataset_id: (
            ("2026-08-01", cases[dataset_id][0]) if dataset_id in cases else None
        ),
    )

    before = MODULE.reconcile_universe({
        "run_id": "pre-kospi200-occurrence",
        "as_of": "2026-09-02T18:15:00+09:00",
        "datasets": rows,
    }, project_root=tmp_path)
    after = MODULE.reconcile_universe({
        "run_id": "post-kospi200-occurrence",
        "as_of": "2026-09-02T21:00:00+09:00",
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
    delayed_publication = "kr_market_liquidity_daily"
    ordinary_cases = set(cases) - {delayed_publication}
    assert {
        dataset: (row["latest"], row["freshness"])
        for dataset, row in before_rows.items()
        if dataset in ordinary_cases
    } == {
        dataset: (actual, "STALE")
        for dataset, (actual, _expected) in cases.items()
        if dataset in ordinary_cases
    }
    assert before_rows[delayed_publication]["freshness"] == "EXPECTED_LAG"
    assert all(row["display_status"] == "CURRENT" for row in before_rows.values())
    assert all(
        before_rows[dataset]["pending_until"] == "20:45"
        for dataset in ordinary_cases
    )
    assert before_rows[delayed_publication]["pending_until"] is None
    assert {
        dataset: row["freshness"] for dataset, row in after_rows.items()
        if dataset in ordinary_cases
    } == {dataset: "STALE" for dataset in ordinary_cases}
    assert after_rows[delayed_publication]["freshness"] == "EXPECTED_LAG"


def test_universe_health_prefers_retained_coverage_probe(tmp_path, monkeypatch):
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]
    core = MODULE.reconcile(
        {"datasets": rows}, run_id="runtime-health",
        as_of="2026-08-18T20:00:00+09:00",
    )
    def probe(_root, dataset_id):
        if dataset_id == "global_index_price_daily":
            raise PermissionError(dataset_id)
        latest = {
            "global_commodity_futures_daily": "2026-08-18",
            "kr_index_daily": "2026-08-18",
        }.get(dataset_id)
        return ("2026-08-01", latest) if latest else None

    monkeypatch.setattr(MODULE, "probe_retained_coverage", probe)

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
    assert futures["runtime_coverage"] == "PROBED"
    assert futures["coverage_source"] == "probe"
    assert blocked_probe["runtime_coverage"] == "FAILED:PermissionError"
    assert blocked_probe["coverage_source"] == "static_table"
    assert blocked_probe["freshness"] == "UNKNOWN"
    assert regressed["latest"] == "2026-08-18"
    assert regressed["expected"] == "2026-08-19"
    assert regressed["freshness"] == "STALE"
    assert regressed["display_status"] == "CURRENT"
    assert regressed["pending_until"] == "20:45"
    assert result["runtime_coverage_validated_count"] == 2
    assert result["runtime_coverage_failure_count"] == 1


def test_coverage_resolution_marks_probe_static_and_none_and_warns(
    tmp_path, monkeypatch, caplog,
) -> None:
    def probe(_root, dataset_id):
        if dataset_id == "global_equity_price_daily":
            return "2026-07-13", "2026-09-04"
        return None

    monkeypatch.setattr(MODULE, "probe_retained_coverage", probe)
    with caplog.at_level(logging.WARNING):
        result = MODULE.reconcile_universe({
            "run_id": "coverage-order",
            "as_of": "2026-09-05T12:50:00+09:00",
            "datasets": [{
                "dataset_id": "global_equity_price_daily",
                "actual_latest": "2026-08-18",
                "expected_latest": "2026-08-18",
                "freshness_status": "CURRENT",
            }],
        }, project_root=tmp_path)

    by_id = {row["dataset"]: row for row in result["datasets"]}
    probed = by_id["global_equity_price_daily"]
    static = by_id["kr_index_daily"]
    absent = by_id["cboe_daily_pcr_daily"]

    assert (probed["latest"], probed["coverage_source"]) == (
        "2026-09-04", "probe",
    )
    assert (static["latest"], static["coverage_source"]) == (
        MODULE.DATASET_UNIVERSE["kr_index_daily"].retained_latest,
        "static_table",
    )
    assert "표는 손으로 적은 값" in static["display_reason"]
    assert (absent["latest"], absent["coverage_source"]) == (None, "none")
    assert result["coverage_source_summary"] == {
        "none": 19, "probe": 1, "static_table": 74,
    }
    assert result["coverage_warnings"] == [{
        "level": "WARN",
        "dataset": "global_equity_price_daily",
        "static_end": "2026-09-03",
        "probed_end": "2026-09-04",
    }]
    assert "dataset=global_equity_price_daily" in caplog.text
    assert "static_end=2026-09-03" in caplog.text
    assert "probed_end=2026-09-04" in caplog.text


def test_execution_log_retains_static_probe_contradiction(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        MODULE, "probe_retained_coverage",
        lambda _root, dataset_id: (
            ("2026-07-13", "2026-09-04")
            if dataset_id == "global_equity_price_daily" else None
        ),
    )
    core = tmp_path / "artifacts/daily_health/core.json"
    output = tmp_path / "artifacts/daily_health/universe.json"
    execution_log = tmp_path / "artifacts/scheduler_logs/health.json"
    core.parent.mkdir(parents=True)
    core.write_text(json.dumps({
        "run_id": "warn-log",
        "as_of": "2026-09-05T12:50:00+09:00",
        "datasets": [],
    }), encoding="utf-8")

    MODULE.write_universe_health_artifact(
        project_root=tmp_path,
        core_artifact=core,
        universe_output=output,
        execution_log=execution_log,
        as_of="2026-09-05T12:50:00+09:00",
    )

    log = json.loads(execution_log.read_text(encoding="utf-8"))
    assert log["coverage_warnings"] == [{
        "level": "WARN",
        "dataset": "global_equity_price_daily",
        "static_end": "2026-09-03",
        "probed_end": "2026-09-04",
    }]


def test_scheduled_manual_publication_observation_is_preserved_when_validated(
    tmp_path, monkeypatch,
):
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]
    monkeypatch.setattr(
        MODULE, "probe_retained_coverage",
        lambda _root, dataset_id: (
            ("1998-11-13", "2026-08-13")
            if dataset_id == "bok_ecos_kr_treasury_yield_source_observation"
            else None
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
    assert bok["freshness"] == "NOT_APPLICABLE"
    assert bok["display_status"] == "PRESERVED"


def test_vix_health_waits_for_the_next_morning_lane_after_evening_release() -> None:
    rows = [{
        "dataset_id": dataset_id,
        "actual_latest": "2026-09-01" if dataset_id == "fred_vix_daily" else None,
        "expected_latest": None,
        "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]

    result = MODULE.reconcile_universe({
        "run_id": "vix-morning-policy",
        "as_of": "2026-09-03T22:53:07+09:00",
        "datasets": rows,
    })
    vix = next(row for row in result["datasets"] if row["dataset"] == "fred_vix_daily")

    assert vix["expected"] == "2026-09-01"
    assert vix["freshness"] == "EXPECTED_LAG"
    assert vix["display_status"] == "CURRENT"
    assert vix["pending_until"] is None


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


def test_credit_runtime_coverage_uses_real_normalized_latest_not_empty_watermark(
    tmp_path, monkeypatch,
):
    normalized = pd.DataFrame({"date": ["2026-08-06"]})
    monkeypatch.setattr(
        runtime_coverage, "_latest_year_contract_reader",
        lambda _contract, _validator: lambda _root: normalized,
    )
    root = tmp_path / "data/normalized/kr_credit_balance_daily"
    state = tmp_path / "data/state/finality/kr_credit_balance_daily.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        '{"dataset":"kr_credit_balance_daily","dates":{'
        '"20260903":{"market_date":"2026-09-03","status":"PROVISIONAL",'
        '"observations":[{"response_status":"VALID_EMPTY"}]}} ,"failures":[]}',
        encoding="utf-8",
    )

    reader = runtime_coverage._latest_finality_observation_reader(
        SimpleNamespace(), lambda _frame: None, "kr_credit_balance_daily",
        include_observation_dates=False,
    )

    assert reader(root)["date"].iloc[0].isoformat() == "2026-08-06"


def test_credit_stale_normalized_latest_degrades_health_after_lane_cutoff(
    tmp_path, monkeypatch,
):
    rows = [{
        "dataset_id": dataset_id, "actual_latest": None,
        "expected_latest": None, "freshness_status": "UNKNOWN",
    } for dataset_id in MODULE.DATASET_OPERATIONS]
    monkeypatch.setattr(
        MODULE, "probe_retained_coverage",
        lambda _root, dataset_id: (
            ("2021-11-09", "2026-08-06")
            if dataset_id == "kr_credit_balance_daily" else None
        ),
    )

    result = MODULE.reconcile_universe({
        "run_id": "stale-credit",
        "as_of": "2026-09-03T22:40:00+09:00",
        "datasets": rows,
    }, project_root=tmp_path)
    credit = next(
        row for row in result["datasets"]
        if row["dataset"] == "kr_credit_balance_daily"
    )

    assert credit["latest"] == "2026-08-06"
    assert credit["expected"] == "2026-09-01"
    assert credit["freshness"] == "STALE"
    assert result["actionable_incident_count"] >= 1


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
        "bok_ecos_usd_krw_daily": "data/normalized/bok_ecos_usd_krw_daily",
        "kr_equity_price_provisional_daily": (
            "data/normalized/kr_equity_price_provisional_daily"
        ),
        "kr_corp_code_map": "data/normalized/kr_corp_code_map",
        "kr_fundamentals_quarterly": "data/normalized/kr_fundamentals_quarterly",
    }.items() <= probes.items()


def test_opendart_runtime_coverage_uses_latest_source_and_period_dates(
    tmp_path, monkeypatch,
) -> None:
    probes = {probe.dataset_id: probe for probe in runtime_coverage._PROBES}
    corp = probes["kr_corp_code_map"]
    fundamentals = probes["kr_fundamentals_quarterly"]
    monkeypatch.setattr(runtime_coverage, "_PROBES", (
        runtime_coverage._CoverageProbe(
            corp.dataset_id, corp.relative_root, corp.date_column,
            lambda _root: pd.DataFrame({"modify_date": ["2026-08-29", "2026-09-02"]}),
        ),
        runtime_coverage._CoverageProbe(
            fundamentals.dataset_id, fundamentals.relative_root, fundamentals.date_column,
            lambda _root: pd.DataFrame({"period_end": ["2026-03-31", "2026-06-30"]}),
        ),
    ))

    result = runtime_coverage.validated_runtime_coverage(
        tmp_path, as_of=date(2026, 9, 4),
    )

    assert corp.date_column == "modify_date"
    assert fundamentals.date_column == "period_end"
    assert result.latest == {
        "kr_corp_code_map": "2026-09-02",
        "kr_fundamentals_quarterly": "2026-06-30",
    }


def test_opendart_runtime_coverage_never_exposes_future_period_end(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr(runtime_coverage, "_PROBES", (
        runtime_coverage._CoverageProbe(
            "kr_fundamentals_quarterly", "unused", "period_end",
            lambda _root: pd.DataFrame({
                "period_end": ["2026-06-30", "2026-09-30", "2026-12-31"],
                "rcept_no": ["20260813000001", "20260515000001", "20260813000002"],
            }),
        ),
    ))

    result = runtime_coverage.validated_runtime_coverage(
        tmp_path, as_of=date(2026, 9, 4),
    )

    assert result.latest == {"kr_fundamentals_quarterly": "2026-06-30"}


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
