from collections import Counter
import csv
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import json
from pathlib import Path
import subprocess

import pytest

from stock_data.contracts.registry import CONTRACTS
from stock_data.contracts.kr_index_fundamental_daily import (
    KR_INDEX_FUNDAMENTAL_DAILY,
)
from stock_data.orchestration.daily_operations import (
    AutomationPolicy,
    AuthStatus,
    AuthType,
    Cadence,
    ConsumerEligibility,
    ConsumerReasonCode,
    DAILY_LANE_READINESS,
    DATASET_OPERATIONS,
    DATASET_UNIVERSE,
    DataGrain,
    DataRole,
    DailyRun,
    DailyRunLock,
    DailyRunLockError,
    DailyRunStatus,
    DatasetOperationSpec,
    DatasetOperationsRegistry,
    DatasetRefreshClass,
    DatasetTier,
    FAILURE_POLICIES,
    FailureCode,
    FinalityClassification,
    FinalityEvidence,
    FinalityPolicy,
    FreshnessContext,
    FreshnessPolicy,
    FreshnessClassification,
    FreshnessStatus,
    GuiUse,
    IdempotencyStatus,
    LaneReadinessStatus,
    OperationalEligibility,
    OperationalClassification,
    OperationalBlockerReason,
    OperationalStatus,
    PitStatus,
    PredictivePitStatus,
    PredictiveEligibility,
    PredictiveClassification,
    PROVIDER_AUTH_METADATA,
    ProviderAuthMetadata,
    RegistryDisposition,
    RefreshPolicy,
    SchedulerGroup,
    SchedulerManagement,
    StageStatus,
    UniverseOperationalStatus,
    build_daily_health_report,
    build_daily_universe_gap_status,
    build_daily_operations_dry_run,
    build_dataset_universe,
    dataset_health_from_freshness,
    evaluate_auth_status,
    evaluate_freshness,
    policy_for_failure,
    read_run_checkpoint,
    transition_run,
    write_run_checkpoint,
)
from stock_data.orchestration.dataset_universe import DATASET_SYMBOL_REGISTRY


UTC = timezone.utc
AS_OF = datetime(2026, 8, 17, 5, 0, tzinfo=UTC)
FINAL = datetime(2026, 8, 14, 4, 0, tzinfo=UTC)
REGISTER_TASKS_SCRIPT = Path(__file__).resolve().parents[3] / "scripts/register_data_operations_tasks.ps1"
WINDOWS_POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
KR_EXPECTED_EXECUTE = r"C:\repo\.venv\Scripts\pythonw.exe"
KR_EXPECTED_RUNNER = r'C:\repo\scripts\maintenance\run_provider_scheduler.py'
KR_EXPECTED_WORKING_DIRECTORY = r"C:\repo"


def _kr_expected_arguments(scheduled_slot: str) -> str:
    arguments = (
        f'"{KR_EXPECTED_RUNNER}" --bundle KR_MARKET_DAILY '
        f'--scheduled-slot {scheduled_slot}'
    )
    if scheduled_slot == "20:30":
        arguments += " --allow-latest-occurrence"
    return arguments


def _kr_registered_task_fixture(scheduled_slot: str = "09:10") -> dict[str, object]:
    return {
        "Actions": [{
            "Execute": KR_EXPECTED_EXECUTE,
            "Arguments": _kr_expected_arguments(scheduled_slot),
            "WorkingDirectory": KR_EXPECTED_WORKING_DIRECTORY,
        }],
        "Triggers": [{
            "CimClass": {"CimClassName": "MSFT_TaskDailyTrigger"},
            "Enabled": True,
            "DaysInterval": 1,
            "StartBoundary": f"2026-08-24T{scheduled_slot}:00+09:00",
            "Repetition": {"Interval": None, "Duration": None},
        }],
        "Settings": {
            "StartWhenAvailable": scheduled_slot == "20:30",
            "DisallowStartIfOnBatteries": False,
            "StopIfGoingOnBatteries": False,
            "WakeToRun": True,
            "MultipleInstances": "IgnoreNew",
            "ExecutionTimeLimit": "PT30M",
        },
    }


def _validate_kr_registered_task(
    tmp_path: Path, fixture: dict[str, object], scheduled_slot: str = "09:10",
) -> list[str]:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler definition fixture test")
    fixture_path = tmp_path / "registered-task.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    def quote(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    command = f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    {quote(REGISTER_TASKS_SCRIPT)}, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) {{ throw ($parseErrors -join '; ') }}
$functionAst = $ast.Find({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-KrMarketDailyTaskDefinition'
}}, $true)
if ($null -eq $functionAst) {{ throw 'validator function not found' }}
Invoke-Expression $functionAst.Extent.Text
$registered = Get-Content -Raw -LiteralPath {quote(fixture_path)} | ConvertFrom-Json
$result = @(Test-KrMarketDailyTaskDefinition `
    -Registered $registered `
    -ExpectedExecute {quote(KR_EXPECTED_EXECUTE)} `
    -ExpectedArguments {quote(_kr_expected_arguments(scheduled_slot))} `
    -ExpectedWorkingDirectory {quote(KR_EXPECTED_WORKING_DIRECTORY)} `
    -ExpectedTimes @({quote(scheduled_slot)}))
ConvertTo-Json -InputObject @($result) -Compress
"""
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-Command", command,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    return [payload] if isinstance(payload, str) else payload


def _kr_registration_dry_run() -> dict[str, dict[str, str]]:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler dry-run test")
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "KrMarketDaily",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tasks: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key == "task":
            current = tasks.setdefault(value, {})
        elif current is not None:
            current[key] = value
    return tasks


def _canonical() -> DatasetOperationSpec:
    return DATASET_OPERATIONS["kr_equity_canonical_universe_daily"]


def _fresh(
    spec: DatasetOperationSpec | None = None,
    *,
    market: date = date(2026, 8, 14),
    expected: date | None = date(2026, 8, 14),
    actual: date | None = date(2026, 8, 14),
    final_at: datetime | None = FINAL,
    partial: bool = False,
    blocked: bool = False,
):
    return evaluate_freshness(
        spec or _canonical(),
        as_of=AS_OF,
        context=FreshnessContext(
            market_date=market,
            expected_latest=expected,
            actual_latest=actual,
            provider_final_at=final_at,
            partial=partial,
            blocked=blocked,
        ),
    )


def test_representative_registry_is_typed_unique_and_contract_bound() -> None:
    assert tuple(DATASET_OPERATIONS) == tuple(sorted(DATASET_OPERATIONS))
    assert len(DATASET_OPERATIONS) == 50
    assert {item.dataset_id for item in DATASET_OPERATIONS.select(executable_only=True)} == {
        "fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily",
        "kr_stock_lending_daily", "kr_stock_lending_market_daily",
        "kr_stock_lending_participant_daily", "kr_vkospi_daily",
        "kr_index_daily", "kr_kospi200_index_daily",
        "kr_index_fundamental_daily", "global_etf_price_daily",
        "global_equity_price_daily", "tossinvest_us_quote_30m", "cboe_daily_pcr_daily", "kbsec_transactions_daily", "kr_etf_investor_flow_daily",
        "kr_etf_master", "kr_etf_price_daily",
        "global_index_price_daily",
        "kr_market_investor_net_purchase_bridge_daily",
        "kr_short_selling_trading_daily",
        "kr_equity_canonical_universe_daily", "kr_equity_price_daily",
        "kr_equity_price_provisional_daily",
        "kr_equity_market_cap_daily", "kr_equity_universe_daily",
        "kr_market_breadth_daily",
        "kr_index_constituent_daily", "kr_kospi200_constituent_price_daily",
        "kr_kospi200_breadth_daily",
        "kr_kospi200_futures_provider_bridge_daily",
        "kr_kospi200_options_provider_bridge_daily",
        "kr_kospi200_futures_nearest_listed_daily",
        "kr_kospi200_option_pcr_daily", "kr_kospi200_option_walls_daily",
        "kr_market_liquidity_daily", "kr_credit_balance_daily",
        "kr_short_selling_balance_daily", "kr_short_selling_investor_daily",
        "ls_t8462_daily_raw", "kr_treasury_yield_daily",
        "bok_ecos_kr_treasury_yield_source_observation",
        "bok_ecos_usd_krw_daily",
        "kr_market_investor_trading_daily",
    }
    for spec in DATASET_OPERATIONS.values():
        if spec.contract_id is not None:
            contract = (
                KR_INDEX_FUNDAMENTAL_DAILY
                if spec.contract_id == KR_INDEX_FUNDAMENTAL_DAILY.name
                else CONTRACTS[spec.contract_id]
            )
            assert contract.version == spec.contract_version


def test_full_dataset_universe_reconciles_contracts_retained_research_and_operations() -> None:
    assert tuple(DATASET_UNIVERSE) == tuple(sorted(DATASET_UNIVERSE))
    assert len(DATASET_UNIVERSE) == 95
    assert set(CONTRACTS) <= set(DATASET_UNIVERSE)
    assert set(DATASET_OPERATIONS) <= set(DATASET_UNIVERSE)
    assert Counter(item.data_role for item in DATASET_UNIVERSE.values()) == {
        DataRole.SOURCE: 53,
        DataRole.SOURCE_OBSERVATION: 8,
        DataRole.RAW_OBSERVATION: 12,
        DataRole.DERIVED: 7,
        DataRole.PUBLISHED_BRIDGE: 5,
        DataRole.SNAPSHOT: 7,
        DataRole.HISTORICAL_SEGMENT: 3,
    }
    assert Counter(item.data_grain for item in DATASET_UNIVERSE.values()) == {
        DataGrain.DAILY: 74,
        DataGrain.WEEKLY: 6,
        DataGrain.EVENT_DRIVEN: 5,
        DataGrain.SNAPSHOT: 7,
        DataGrain.INTRADAY: 3,
    }
    assert Counter(item.refresh_policy for item in DATASET_UNIVERSE.values()) == {
        RefreshPolicy.GAP_FILL: 42,
        RefreshPolicy.APPEND_EVENT: 6,
        RefreshPolicy.UPSTREAM_DEPENDENCY: 12,
        RefreshPolicy.SNAPSHOT_CAPTURE: 7,
        RefreshPolicy.STATIC_COMPLETE: 9,
        RefreshPolicy.MANUAL_RESEARCH: 11,
        RefreshPolicy.DISABLED_PENDING_CONTRACT: 8,
    }
    assert Counter(item.operational_status for item in DATASET_UNIVERSE.values()) == {
        UniverseOperationalStatus.READY: 7,
        UniverseOperationalStatus.READY_WITH_FINALITY_GATE: 19,
        UniverseOperationalStatus.READY_WITH_LIMITS: 26,
        UniverseOperationalStatus.MANUAL_ONLY: 21,
        UniverseOperationalStatus.BLOCKED: 8,
        UniverseOperationalStatus.NOT_APPLICABLE: 14,
    }
    assert Counter(item.predictive_pit_status for item in DATASET_UNIVERSE.values()) == {
        PredictivePitStatus.PIT_SAFE: 9,
        PredictivePitStatus.PIT_LIMITED: 10,
        PredictivePitStatus.PIT_BLOCKED: 61,
        PredictivePitStatus.NON_PREDICTIVE: 13,
        PredictivePitStatus.RESEARCH_ONLY: 2,
    }
    assert Counter(item.automation_policy for item in DATASET_UNIVERSE.values()) == {
        AutomationPolicy.MANUAL_GATE: 15,
        AutomationPolicy.DEPENDENCY_DRIVEN: 12,
        AutomationPolicy.NO_REFRESH: 9,
        AutomationPolicy.RESEARCH_ONLY: 11,
        AutomationPolicy.DISABLED: 8,
        AutomationPolicy.AUTO_ELIGIBLE: 40,
    }
    assert all(sum(counts.values()) == 95 for counts in (
        Counter(item.data_role for item in DATASET_UNIVERSE.values()),
        Counter(item.data_grain for item in DATASET_UNIVERSE.values()),
        Counter(item.refresh_policy for item in DATASET_UNIVERSE.values()),
        Counter(item.operational_status for item in DATASET_UNIVERSE.values()),
        Counter(item.predictive_pit_status for item in DATASET_UNIVERSE.values()),
        Counter(item.automation_policy for item in DATASET_UNIVERSE.values()),
    ))
    assert Counter(item.prior_disposition for item in DATASET_UNIVERSE.values()) == {
        RegistryDisposition.REGISTERED: 50,
        RegistryDisposition.INTENTIONALLY_EXCLUDED: 26,
        RegistryDisposition.REGISTRY_MISSING: 19,
    }
    assert {item.dataset_id for item in DATASET_UNIVERSE.values() if item.automation_enabled} == {
        "fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily",
        "us_treasury_spread_daily", "us_vix_term_structure_daily",
        "kr_stock_lending_daily", "kr_stock_lending_market_daily",
        "kr_stock_lending_participant_daily", "kr_vkospi_daily",
        "kr_index_daily", "kr_kospi200_index_daily",
        "kr_index_fundamental_daily", "global_etf_price_daily",
        "global_equity_price_daily", "tossinvest_us_quote_30m", "cboe_daily_pcr_daily", "kbsec_transactions_daily", "kr_etf_investor_flow_daily",
        "kr_etf_master", "kr_etf_price_daily",
        "global_index_price_daily", "global_commodity_futures_daily",
        "kr_market_investor_net_purchase_bridge_daily",
        "kr_short_selling_trading_daily",
        "kr_equity_canonical_universe_daily", "kr_equity_price_daily",
        "kr_equity_price_provisional_daily",
        "kr_equity_investor_flow_daily",
        "kr_equity_market_cap_daily", "kr_equity_universe_daily",
        "kr_market_breadth_daily",
        "kr_index_constituent_daily", "kr_kospi200_constituent_price_daily",
        "kr_kospi200_breadth_daily",
        "kr_kospi200_futures_daily", "kr_kospi200_options_daily",
        "kr_kospi200_futures_provider_bridge_daily",
        "kr_kospi200_options_provider_bridge_daily",
        "kr_kospi200_futures_nearest_listed_daily",
        "kr_kospi200_option_pcr_daily", "kr_kospi200_option_walls_daily",
        "kr_market_liquidity_daily", "kr_credit_balance_daily",
        "kr_short_selling_balance_daily", "kr_short_selling_investor_daily",
        "ls_t8462_daily_raw", "kr_treasury_yield_daily",
        "bok_ecos_kr_treasury_yield_source_observation",
        "bok_ecos_usd_krw_daily",
        "kr_market_investor_trading_daily",
        "kr_corp_code_map", "kr_fundamentals_quarterly",
    }
    assert all(item.registry_present and item.registry_entry == item.dataset_id for item in DATASET_UNIVERSE.values())
    assert sum(item.scheduler_management is SchedulerManagement.NO_REFRESH for item in DATASET_UNIVERSE.values()) == 9
    assert len(set(item.economic_variable for item in DATASET_UNIVERSE.values())) == 62
    assert len({path for item in DATASET_UNIVERSE.values() for path in item.physical_artifacts}) == 99
    assert all(
        item.display_consumer_eligibility is not ConsumerEligibility.UNKNOWN
        and item.research_consumer_eligibility is not ConsumerEligibility.UNKNOWN
        and item.predictive_consumer_eligibility is not ConsumerEligibility.UNKNOWN
        and item.display_consumer_reason is not ConsumerReasonCode.NOT_CLASSIFIED
        and item.research_consumer_reason is not ConsumerReasonCode.NOT_CLASSIFIED
        and item.predictive_consumer_reason is not ConsumerReasonCode.NOT_CLASSIFIED
        for item in DATASET_UNIVERSE.values()
    )


def test_dated_dataset_universe_artifact_remains_a_compatible_snapshot() -> None:
    path = Path("artifacts/data_inventory/full_dataset_universe_multiaxis_20260818.csv")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert len(rows) == 80
    artifact_ids = {row["dataset_id"] for row in rows}
    assert artifact_ids < set(DATASET_UNIVERSE)
    assert set(DATASET_UNIVERSE) - artifact_ids == {
        "bok_ecos_kr_market_rate_daily",
        "bok_ecos_usd_krw_daily",
        "kr_equity_investor_flow_daily",
        "kr_equity_price_provisional_daily",
        "kr_etf_master", "kr_etf_price_daily", "kr_corp_code_map",
        "kr_fundamentals_quarterly", "research_target_price_consensus",
        "us_vix_term_structure_daily",
        "global_equity_price_daily", "tossinvest_us_quote_30m", "cboe_daily_pcr_daily",
        "kbsec_transactions_daily", "kr_etf_investor_flow_daily",
    }
    enabled = {row["dataset_id"] for row in rows if row["automation_enabled"] == "True"}
    assert enabled == {
        item.dataset_id for item in DATASET_UNIVERSE.values()
        if item.automation_enabled
    } - {
        "bok_ecos_usd_krw_daily", "kr_equity_price_provisional_daily",
        "kr_equity_investor_flow_daily",
        "kr_etf_master", "kr_etf_price_daily",
        "kr_corp_code_map", "kr_fundamentals_quarterly",
        "us_vix_term_structure_daily",
        "global_equity_price_daily", "tossinvest_us_quote_30m", "cboe_daily_pcr_daily",
        "kbsec_transactions_daily", "kr_etf_investor_flow_daily",
    }
    assert all(row["data_role"] and row["data_grain"] and row["refresh_policy"] for row in rows)
    def artifact_value(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, tuple):
            return "|".join(
                item.value if isinstance(item, Enum) else str(item) for item in value
            )
        return value.value if isinstance(value, Enum) else str(value)

    evolving_snapshot_fields = {
        "economic_variable", "source", "layer", "contract_version",
        "research_consumer_eligibility", "research_consumer_reason",
        "downstream_dependencies",
    }
    assert all(
        row[field] == artifact_value(getattr(DATASET_UNIVERSE[row["dataset_id"]], field))
        for row in rows
        for field in row
        if field not in {"reason_if_not_registered_before", *evolving_snapshot_fields}
    )
    projected = {row["dataset_id"]: row for row in rows}["market_price_60m_observation"]
    retained = DATASET_UNIVERSE["market_price_60m_observation"]
    assert projected["refresh_policy"] == retained.refresh_policy.value
    assert projected["operational_status"] == retained.operational_status.value
    assert projected["automation_policy"] == retained.automation_policy.value
    assert projected["automation_enabled"] == str(retained.automation_enabled)
    assert projected["scheduler_lane"] == retained.scheduler_lane
    assert projected["scheduler_management"] == retained.scheduler_management.value
    assert projected["reason_if_not_registered_before"]


def test_retained_market_60m_history_is_separate_from_current_display_operation() -> None:
    retained = DATASET_UNIVERSE["market_price_60m_observation"]

    assert "market_price_60m_observation" not in DATASET_OPERATIONS
    assert retained.operations_registry_present_before is False
    assert retained.prior_disposition is RegistryDisposition.INTENTIONALLY_EXCLUDED
    assert retained.refresh_policy is RefreshPolicy.STATIC_COMPLETE
    assert retained.operational_status is UniverseOperationalStatus.NOT_APPLICABLE
    assert retained.automation_policy is AutomationPolicy.NO_REFRESH
    assert retained.automation_enabled is False
    assert retained.scheduler_lane == "NO_SCHEDULER_LANE"
    assert retained.scheduler_management is SchedulerManagement.NO_REFRESH
    assert (
        f"{len(DATASET_OPERATIONS)}-row current dataset operations registry"
        in retained.reason_if_not_registered_before
    )
    assert "GLOBAL_MARKET_CURRENT_60M.md" in retained.reason_if_not_registered_before
    assert "writes no Normalized history" in retained.reason_if_not_registered_before


def test_kr_etf_investor_flow_is_automated_after_the_first_live_receipt() -> None:
    """First live run 2026-09-05 17:25 COMPLETE (10 calls, 80 rows) → automation on."""
    operation = DATASET_OPERATIONS["kr_etf_investor_flow_daily"]
    universe = DATASET_UNIVERSE["kr_etf_investor_flow_daily"]

    assert operation.operational_status is OperationalStatus.AUTO_READY
    assert operation.automation_enabled is True
    assert operation.idempotency_status is IdempotencyStatus.CONFIRMED
    assert universe.automation_policy is AutomationPolicy.AUTO_ELIGIBLE
    assert universe.automation_enabled is True
    assert universe.scheduler_lane == "KR_ETF_INVESTOR_FLOW_DAILY"
    assert universe.gui_use is GuiUse.DIRECT


def test_retained_market_15m_history_is_not_the_30m_polling_scheduler_dataset() -> None:
    retained = DATASET_UNIVERSE["market_price_15m_observation"]

    assert "market_price_15m_observation" not in DATASET_OPERATIONS
    assert retained.prior_disposition is RegistryDisposition.INTENTIONALLY_EXCLUDED
    assert retained.refresh_policy is RefreshPolicy.STATIC_COMPLETE
    assert retained.operational_status is UniverseOperationalStatus.NOT_APPLICABLE
    assert retained.automation_policy is AutomationPolicy.NO_REFRESH
    assert retained.automation_enabled is False
    assert retained.scheduler_lane == "MARKET_15M"
    assert retained.scheduler_management is SchedulerManagement.NO_REFRESH
    assert retained.operational_blocker_reason is None


def test_dataset_universe_axes_enforce_valid_combinations() -> None:
    derived = DATASET_UNIVERSE["kr_market_breadth_daily"]
    with pytest.raises(TypeError, match="data_role"):
        replace(derived, data_role="DERIVED")
    with pytest.raises(ValueError, match="upstream"):
        replace(derived, refresh_policy=RefreshPolicy.GAP_FILL)
    historical = DATASET_UNIVERSE["krx_legacy_kospi200_futures_daily"]
    with pytest.raises(ValueError, match="historical"):
        replace(historical, automation_policy=AutomationPolicy.MANUAL_GATE)
    blocked = DATASET_UNIVERSE["kr_derivatives_futures_daily"]
    with pytest.raises(ValueError, match="blocker"):
        replace(blocked, operational_blocker_reason=None)
    with pytest.raises(ValueError, match="enabled automation"):
        replace(DATASET_UNIVERSE["kr_derivatives_futures_daily"], automation_enabled=True)
    with pytest.raises(ValueError, match="contradicts"):
        replace(
            derived,
            display_consumer_eligibility=ConsumerEligibility.BLOCKED,
        )
    with pytest.raises(ValueError, match="another axis"):
        replace(
            derived,
            research_consumer_reason=ConsumerReasonCode.PREDICTIVE_PIT_BLOCKED,
        )
    with pytest.raises(ValueError, match="matching evidence"):
        replace(
            derived,
            display_consumer_eligibility=ConsumerEligibility.BLOCKED,
            display_consumer_reason=ConsumerReasonCode.DISPLAY_NOT_CONTRACTED,
        )


def test_consumer_eligibility_axes_keep_display_research_and_pit_decisions_distinct() -> None:
    display_without_predictive = DATASET_UNIVERSE["fred_treasury_yield_daily"]
    assert display_without_predictive.display_consumer_eligibility is ConsumerEligibility.ELIGIBLE
    assert display_without_predictive.research_consumer_eligibility is ConsumerEligibility.ELIGIBLE
    assert display_without_predictive.predictive_consumer_eligibility is ConsumerEligibility.BLOCKED

    research_only = DATASET_UNIVERSE["ls_t1633_program_trading_candidate"]
    assert research_only.display_consumer_eligibility is ConsumerEligibility.BLOCKED
    assert research_only.research_consumer_eligibility is ConsumerEligibility.LIMITED
    assert research_only.predictive_consumer_eligibility is ConsumerEligibility.BLOCKED
    assert research_only.predictive_consumer_reason is ConsumerReasonCode.PREDICTIVE_RESEARCH_ONLY

    pit_safe = DATASET_UNIVERSE["kr_kospi200_index_daily"]
    assert pit_safe.predictive_consumer_eligibility is ConsumerEligibility.ELIGIBLE
    assert pit_safe.predictive_consumer_reason is ConsumerReasonCode.PREDICTIVE_PIT_SAFE

    pit_limited = DATASET_UNIVERSE["fred_vix_daily"]
    assert pit_limited.predictive_consumer_eligibility is ConsumerEligibility.BLOCKED
    assert pit_limited.predictive_consumer_reason is ConsumerReasonCode.PREDICTIVE_PIT_LIMITED


def test_operations_registration_cannot_promote_display_consumer_eligibility() -> None:
    dataset_id = "kr_equity_foreign_ownership_daily"
    original = DATASET_UNIVERSE[dataset_id]
    assert original.display_consumer_eligibility is ConsumerEligibility.BLOCKED

    injected = build_dataset_universe({
        **DATASET_OPERATIONS,
        dataset_id: DATASET_OPERATIONS["fred_treasury_yield_daily"],
    })[dataset_id]

    assert injected.gui_use is original.gui_use
    assert injected.display_consumer_eligibility is original.display_consumer_eligibility
    assert injected.display_consumer_reason is original.display_consumer_reason


def test_predictive_block_does_not_imply_operational_block_and_legacy_axis_is_inert() -> None:
    raw = DATASET_UNIVERSE["kr_equity_foreign_ownership_daily"]
    assert raw.predictive_pit_status is PredictivePitStatus.PIT_BLOCKED
    assert raw.operational_status is UniverseOperationalStatus.MANUAL_ONLY
    changed_compatibility_value = replace(raw, primary_classification=DatasetRefreshClass.BLOCKED)
    assert changed_compatibility_value.scheduler_management is raw.scheduler_management
    assert changed_compatibility_value.operational_status is raw.operational_status


def test_important_dataset_reclassifications_preserve_temporal_nature() -> None:
    t8462 = DATASET_UNIVERSE["ls_t8462_daily_raw"]
    assert (t8462.data_role, t8462.data_grain, t8462.refresh_policy) == (
        DataRole.RAW_OBSERVATION, DataGrain.DAILY, RefreshPolicy.GAP_FILL,
    )
    assert t8462.operational_status is UniverseOperationalStatus.READY_WITH_FINALITY_GATE
    assert t8462.predictive_pit_status is PredictivePitStatus.NON_PREDICTIVE
    etf = DATASET_UNIVERSE["global_etf_price_daily"]
    assert (etf.data_role, etf.data_grain, etf.operational_status) == (
        DataRole.SOURCE, DataGrain.DAILY, UniverseOperationalStatus.READY_WITH_LIMITS,
    )
    assert etf.retained_latest == "2026-08-18" and etf.operational_blocker_reason is None
    for dataset_id in (
        "kr_equity_foreign_ownership_daily", "kr_equity_fundamental_daily",
        "kr_etf_ohlcv_daily", "kr_etf_universe_daily", "kr_index_fundamental_daily",
    ):
        assert DATASET_UNIVERSE[dataset_id].data_grain is DataGrain.DAILY


def test_yahoo_daily_dataset_symbol_registry_includes_new_uncollected_scope() -> None:
    assert DATASET_SYMBOL_REGISTRY["global_index_price_daily"] == (
        "SP500", "NASDAQ_COMPOSITE", "NASDAQ100", "SOX", "DOW_JONES",
        "DOLLAR_INDEX", "VIX9D", "VIX3M", "VIX6M", "SKEW",
    )
    assert DATASET_SYMBOL_REGISTRY["global_etf_price_daily"] == (
        "SOXX", "EWY", "SOXL", "TQQQ", "QLD", "TLT", "QQQ", "SPY", "SGOV", "VGLT",
        "VNQ", "IEF", "SHY",
    )
    assert DATASET_SYMBOL_REGISTRY["global_equity_price_daily"] == ("SKHY",)
    assert DATASET_SYMBOL_REGISTRY["tossinvest_us_quote_30m"] == (
        "SKHY", "SOXL", "SOXX", "TQQQ", "QQQ", "EWY", "SGOV", "VGLT",
    )
    assert DATASET_SYMBOL_REGISTRY["global_commodity_futures_daily"] == (
        "NASDAQ100_FUTURES", "GOLD", "WTI_CRUDE_OIL",
        "SP500_FUTURES", "DOW_FUTURES",
    )


def test_daily_gap_status_covers_74_rows_and_never_infers_a_calendar() -> None:
    target = date(2026, 8, 17)
    rows = build_daily_universe_gap_status(
        expected_dates={"fred_vix_daily": (target,)},
        retained_dates={"fred_vix_daily": (target,)},
        finality_by_dataset={"fred_vix_daily": "AS_RETRIEVED"},
    )
    assert len(rows) == 74
    by_id = {row.dataset_id: row for row in rows}
    assert by_id["fred_vix_daily"].plan_status == "NOOP_IDEMPOTENT"
    assert by_id["fred_vix_daily"].pre_network_noop is True
    assert by_id["kr_index_daily"].plan_status == "CALENDAR_UNAVAILABLE"
    assert by_id["kr_index_daily"].expected_latest is None


def test_daily_gap_status_returns_only_explicit_missing_dates() -> None:
    dates = tuple(date(2026, 8, day) for day in (14, 17, 18))
    row = next(item for item in build_daily_universe_gap_status(
        expected_dates={"kr_index_daily": dates},
        retained_dates={"kr_index_daily": dates[:1]},
        finality_by_dataset={"kr_index_daily": "MANUAL_CONFIRMED"},
    ) if item.dataset_id == "kr_index_daily")
    assert row.missing_dates == dates[1:]
    assert row.plan_status == "MISSING_DATES"
    assert row.pre_network_noop is False


def test_registry_rejects_duplicate_id_untyped_cadence_and_missing_provider() -> None:
    spec = _canonical()
    with pytest.raises(ValueError, match="duplicate"):
        DatasetOperationsRegistry((spec, spec), PROVIDER_AUTH_METADATA)
    with pytest.raises(TypeError, match="cadence"):
        replace(spec, cadence="KR_DAILY")
    with pytest.raises(ValueError, match="provider auth metadata missing"):
        DatasetOperationsRegistry((spec,), {})


def test_disabled_sox_like_candidate_onboards_without_core_branch() -> None:
    candidate = DatasetOperationSpec(
        dataset_id="dummy_sox_daily",
        economic_variable="SOX-like global semiconductor index candidate",
        cadence=Cadence.GLOBAL_DAILY,
        tier=DatasetTier.TIER_4_RESEARCH,
        primary_source="candidate_provider",
        contract_id=None,
        contract_version=None,
        operational_status=OperationalStatus.BLOCKED,
        freshness_policy=FreshnessPolicy(
            "dummy_sox_policy", "UTC", "explicit reviewed market date",
            FinalityPolicy(FinalityEvidence.UNKNOWN, "UTC"),
        ),
        pipeline_dependencies=(),
        idempotency_status=IdempotencyStatus.NOT_CONFIRMED,
        pit_status=PitStatus.UNKNOWN,
        automation_enabled=False,
        provider_auth_id="yahoo",
        validation_policy="candidate schema and source-equivalence review required",
        candidate=True,
    )
    extended = DATASET_OPERATIONS.with_spec(candidate)
    assert extended["dummy_sox_daily"] is candidate
    assert {item.dataset_id for item in extended.select(executable_only=True)} == {
        "fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily",
        "kr_stock_lending_daily", "kr_stock_lending_market_daily",
        "kr_stock_lending_participant_daily", "kr_vkospi_daily",
        "kr_index_daily", "kr_kospi200_index_daily",
        "kr_index_fundamental_daily", "global_etf_price_daily",
        "global_equity_price_daily", "tossinvest_us_quote_30m", "cboe_daily_pcr_daily", "kbsec_transactions_daily", "kr_etf_investor_flow_daily",
        "kr_etf_master", "kr_etf_price_daily",
        "global_index_price_daily",
        "kr_market_investor_net_purchase_bridge_daily",
        "kr_short_selling_trading_daily",
        "kr_equity_canonical_universe_daily", "kr_equity_price_daily",
        "kr_equity_price_provisional_daily",
        "kr_equity_market_cap_daily", "kr_equity_universe_daily",
        "kr_market_breadth_daily",
        "kr_index_constituent_daily", "kr_kospi200_constituent_price_daily",
        "kr_kospi200_breadth_daily",
        "kr_kospi200_futures_provider_bridge_daily",
        "kr_kospi200_options_provider_bridge_daily",
        "kr_kospi200_futures_nearest_listed_daily",
        "kr_kospi200_option_pcr_daily", "kr_kospi200_option_walls_daily",
        "kr_market_liquidity_daily", "kr_credit_balance_daily",
        "kr_short_selling_balance_daily", "kr_short_selling_investor_daily",
        "ls_t8462_daily_raw", "kr_treasury_yield_daily",
        "bok_ecos_kr_treasury_yield_source_observation",
        "bok_ecos_usd_krw_daily",
        "kr_market_investor_trading_daily",
    }
    with pytest.raises(ValueError, match="automation requires"):
        replace(candidate, automation_enabled=True)


def test_operational_and_predictive_eligibility_are_independent() -> None:
    fred = DATASET_OPERATIONS["fred_treasury_yield_daily"]
    assert fred.operational_eligibility is OperationalEligibility.ELIGIBLE
    assert fred.predictive_eligibility is PredictiveEligibility.BLOCKED
    candidate = replace(
        fred,
        dataset_id="disabled_candidate_daily",
        candidate=True,
        tier=DatasetTier.TIER_4_RESEARCH,
        operational_status=OperationalStatus.BLOCKED,
        automation_enabled=False,
    )
    assert candidate.operational_eligibility is OperationalEligibility.BLOCKED
    assert candidate.predictive_eligibility is PredictiveEligibility.RESEARCH_ONLY


def test_registry_driven_dry_run_is_offline_and_dependency_aware() -> None:
    canonical = _canonical()
    rows = build_daily_operations_dry_run(
        as_of=AS_OF,
        registry=DATASET_OPERATIONS,
        contexts={
            canonical.dataset_id: FreshnessContext(
                market_date=date(2026, 8, 17),
                expected_latest=date(2026, 8, 14),
                actual_latest=date(2026, 8, 13),
                provider_final_at=FINAL,
            ),
        },
    )
    planned = {row.dataset_id: row for row in rows}
    assert planned[canonical.dataset_id].freshness_status is FreshnessStatus.STALE
    assert planned[canonical.dataset_id].planned_action == "BOUNDED_CATCH_UP_ELIGIBLE"
    assert planned[canonical.dataset_id].estimated_api_calls == 0
    assert planned[canonical.dataset_id].dependencies == tuple(sorted(canonical.pipeline_dependencies))
    # Unknown contexts fail closed and never become implicit collection work.
    assert planned["global_index_price_daily"].planned_action == "REVIEW_REQUIRED"


def test_health_report_separates_operational_pit_and_research_counts() -> None:
    run_id = "health-separation"
    canonical = dataset_health_from_freshness(run_id, _canonical(), _fresh())
    fred = DATASET_OPERATIONS["fred_treasury_yield_daily"]
    fred_health = dataset_health_from_freshness(run_id, fred, _fresh(fred))
    ls = DATASET_OPERATIONS["ls_t8462_daily_raw"]
    ls_health = dataset_health_from_freshness(run_id, ls, _fresh(ls, blocked=True))
    report = build_daily_health_report(
        run_id=run_id,
        as_of=AS_OF,
        datasets=(canonical, fred_health, ls_health),
    )
    assert report.operational_blocked_count == 0
    assert report.predictive_blocked_count == 2
    assert report.research_only_count == 1
    assert report.blocked_count == 1
    payload = json.loads(report.to_json_bytes())
    assert payload["operational_blocked_count"] == 0
    assert payload["predictive_blocked_count"] == 2
    assert payload["research_only_count"] == 1
    fred_payload = next(
        row for row in payload["datasets"] if row["dataset_id"] == fred.dataset_id
    )
    assert fred_payload["display_consumer_eligibility"] == "ELIGIBLE"
    assert fred_payload["research_consumer_eligibility"] == "ELIGIBLE"
    assert fred_payload["predictive_consumer_eligibility"] == "BLOCKED"
    assert fred_payload["predictive_consumer_reason"] == "PREDICTIVE_PIT_BLOCKED"
    with pytest.raises(ValueError, match="contradicts"):
        replace(
            fred_health,
            display_consumer_eligibility=ConsumerEligibility.BLOCKED,
        )
    with pytest.raises(ValueError, match="differs from typed registry"):
        replace(
            fred_health,
            predictive_consumer_eligibility=ConsumerEligibility.ELIGIBLE,
            predictive_consumer_reason=ConsumerReasonCode.PREDICTIVE_PIT_SAFE,
        )
    restored = replace(
        fred_health,
        predictive_consumer_eligibility=ConsumerEligibility.UNKNOWN,
        predictive_consumer_reason=ConsumerReasonCode.NOT_CLASSIFIED,
    )
    assert restored.predictive_consumer_eligibility is ConsumerEligibility.BLOCKED
    assert restored.predictive_consumer_reason is ConsumerReasonCode.PREDICTIVE_PIT_BLOCKED


def test_freshness_current_and_weekend_boundary_use_explicit_calendar() -> None:
    friday = date(2026, 8, 14)
    result = _fresh(market=friday, expected=friday, actual=friday)
    assert result.freshness_status is FreshnessStatus.CURRENT
    assert result.review_required is False


def test_freshness_expected_lag_stale_missing_partial_and_provider_delay() -> None:
    monday = date(2026, 8, 17)
    friday = date(2026, 8, 14)
    assert _fresh(market=monday, expected=friday, actual=friday).freshness_status is FreshnessStatus.EXPECTED_LAG
    assert _fresh(expected=friday, actual=date(2026, 8, 13)).freshness_status is FreshnessStatus.STALE
    assert _fresh(expected=friday, actual=None).freshness_status is FreshnessStatus.MISSING
    assert _fresh(partial=True).freshness_status is FreshnessStatus.PARTIAL
    future = AS_OF + timedelta(hours=1)
    delayed = _fresh(expected=monday, actual=friday, final_at=future)
    assert delayed.freshness_status is FreshnessStatus.PROVIDER_DELAY
    assert delayed.review_required is False


def test_as_retrieved_finality_and_naive_as_of_is_rejected() -> None:
    global_spec = DATASET_OPERATIONS["global_index_price_daily"]
    result = _fresh(global_spec)
    assert result.freshness_status is FreshnessStatus.CURRENT
    assert result.freshness_classification is FreshnessClassification.CURRENT
    assert result.finality_classification is FinalityClassification.AS_RETRIEVED
    assert result.review_required is False
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_freshness(
            _canonical(),
            as_of=datetime(2026, 8, 17, 5),
            context=FreshnessContext(date(2026, 8, 14), date(2026, 8, 14), date(2026, 8, 14)),
        )


def test_four_health_dimensions_are_independent_and_serialized() -> None:
    run_id = "health-four-dimensions"
    canonical = dataset_health_from_freshness(run_id, _canonical(), _fresh())
    kospi200_spec = DATASET_OPERATIONS["kr_kospi200_index_daily"]
    kospi200 = dataset_health_from_freshness(run_id, kospi200_spec, _fresh(kospi200_spec))
    global_spec = DATASET_OPERATIONS["global_index_price_daily"]
    as_retrieved = dataset_health_from_freshness(run_id, global_spec, _fresh(global_spec))
    ls_spec = DATASET_OPERATIONS["ls_t8462_daily_raw"]
    research = dataset_health_from_freshness(run_id, ls_spec, _fresh(ls_spec, blocked=True))
    blocked_spec = DATASET_OPERATIONS["kr_derivatives_futures_daily"]
    blocked = dataset_health_from_freshness(
        run_id, blocked_spec, _fresh(blocked_spec, blocked=True)
    )

    assert canonical.freshness_classification is FreshnessClassification.CURRENT
    assert canonical.finality_classification is FinalityClassification.CONFIRMED
    assert canonical.operational_classification is OperationalClassification.ELIGIBLE
    assert canonical.predictive_classification is PredictiveClassification.BLOCKED
    assert kospi200.predictive_classification is PredictiveClassification.ELIGIBLE
    assert as_retrieved.freshness_classification is FreshnessClassification.CURRENT
    assert as_retrieved.finality_classification is FinalityClassification.AS_RETRIEVED
    assert as_retrieved.operational_classification is OperationalClassification.ELIGIBLE
    assert research.operational_classification is OperationalClassification.ELIGIBLE
    assert research.predictive_classification is PredictiveClassification.RESEARCH_ONLY
    assert blocked.operational_classification is OperationalClassification.BLOCKED

    report = build_daily_health_report(
        run_id=run_id, as_of=AS_OF,
        datasets=(canonical, kospi200, as_retrieved, research, blocked),
    )
    assert report.current_count == 5  # date coverage, not finality/readiness
    assert report.finality_confirmed_count == 1
    assert report.finality_as_retrieved_count == 1
    assert report.finality_unknown_count == 3
    assert report.operational_eligible_count == 4
    assert report.operational_manual_only_count == 0
    assert report.operational_blocked_count == 1
    assert report.predictive_eligible_count == 1
    assert report.research_only_count == 1
    payload = json.loads(report.to_json_bytes())
    assert all(item["freshness_classification"] == "CURRENT" for item in payload["datasets"])
    assert report.dimension_summary()["finality"] == {
        "CONFIRMED": 1, "MANUAL_CONFIRMED": 0, "AS_RETRIEVED": 1, "UNKNOWN": 3,
    }


def test_manual_and_as_retrieved_finality_are_explicit_not_inferred() -> None:
    base = _canonical()
    manual = replace(
        base,
        freshness_policy=replace(
            base.freshness_policy,
            finality=FinalityPolicy(
                FinalityEvidence.MANUAL_CONFIRMATION,
                base.freshness_policy.timezone,
            ),
        ),
    )
    as_retrieved = replace(
        base,
        freshness_policy=replace(
            base.freshness_policy,
            finality=FinalityPolicy(
                FinalityEvidence.AS_RETRIEVED,
                base.freshness_policy.timezone,
            ),
        ),
    )
    assert _fresh(manual).finality_classification is FinalityClassification.MANUAL_CONFIRMED
    retrieved = _fresh(as_retrieved, final_at=None)
    assert retrieved.freshness_classification is FreshnessClassification.CURRENT
    assert retrieved.finality_classification is FinalityClassification.AS_RETRIEVED
    assert retrieved.freshness_status is FreshnessStatus.UNKNOWN  # legacy fail-closed view
    assert {item.value for item in FreshnessClassification} == {
        "CURRENT", "EXPECTED_LAG", "STALE", "UNKNOWN",
    }
    assert {item.value for item in OperationalClassification} == {
        "ELIGIBLE", "MANUAL_ONLY", "BLOCKED",
    }
    assert {item.value for item in PredictiveClassification} == {
        "ELIGIBLE", "BLOCKED", "RESEARCH_ONLY",
    }


def test_failure_taxonomy_is_complete_and_never_allows_fallback() -> None:
    assert set(FAILURE_POLICIES) == set(FailureCode)
    assert all(policy.fallback_allowed is False for policy in FAILURE_POLICIES.values())
    assert FAILURE_POLICIES[FailureCode.NETWORK_FAILURE].retry_allowed is True
    for code in (
        FailureCode.AUTH_FAILURE, FailureCode.SCHEMA_CHANGE,
        FailureCode.KEY_DUPLICATION, FailureCode.UNIT_CHANGE,
        FailureCode.SEMANTIC_CHANGE, FailureCode.CHECKPOINT_CONFLICT,
        FailureCode.VALIDATION_FAILURE,
    ):
        policy = FAILURE_POLICIES[code]
        assert policy.stop_lane and policy.review_required
    code, policy = policy_for_failure("NOT_A_REAL_FAILURE")
    assert code is FailureCode.UNKNOWN_FAILURE and policy.stop_lane


def test_provider_auth_metadata_uses_names_only_and_evaluates_expiry() -> None:
    metadata = ProviderAuthMetadata(
        "provider", AuthType.API_KEY, ("PROVIDER_KEY",),
        expires_at_env_key="PROVIDER_KEY_EXPIRES_AT",
        auth_health_supported=True,
    )
    environment = {
        "PROVIDER_KEY": "not-returned",
        "PROVIDER_KEY_EXPIRES_AT": "2026-09-01T00:00:00+00:00",
    }
    assert evaluate_auth_status(metadata, environment, as_of=AS_OF) is AuthStatus.EXPIRING_SOON
    assert evaluate_auth_status(metadata, {}, as_of=AS_OF) is AuthStatus.REAUTH_REQUIRED
    assert "not-returned" not in repr(metadata)
    assert PROVIDER_AUTH_METADATA["krx_open_api"].expires_at_env_key == "KRX_AUTH_KEY_EXPIRES_AT"
    assert PROVIDER_AUTH_METADATA["bok_ecos"].expires_at_env_key == "BOK_ECOS_API_KEY_EXPIRES_AT"
    assert PROVIDER_AUTH_METADATA["tossinvest"].expires_at_env_key == "TOSSINVEST_EXPIRES_AT"
    assert PROVIDER_AUTH_METADATA["kbsec"].auth_type is AuthType.OAUTH2
    assert PROVIDER_AUTH_METADATA["pykrx_login"].auth_type is AuthType.SESSION_LOGIN


def test_daily_lane_readiness_is_complete_and_fail_closed_for_scheduler() -> None:
    expected = {
        "KR_INDEX_DAILY", "KR_INDEX_FUNDAMENTAL_DAILY", "GLOBAL_INDEX_DAILY",
        "FRED_DAILY", "GLOBAL_ETF_DAILY", "GLOBAL_EQUITY_DAILY",
        "TOSSINVEST_US_QUOTES_30M", "CBOE_DAILY_PCR", "KB_TRANSACTIONS_DAILY",
        "KR_ETF_PRICE_DAILY", "KR_ETF_INVESTOR_FLOW_DAILY",
        "KR_EQUITY_PROVISIONAL_DAILY",
        "GLOBAL_COMMODITY_DAILY",
        "MARKET_INVESTOR_DAILY",
        "VKOSPI_DAILY", "CANONICAL_EQUITY_DAILY", "KOSPI200_BREADTH_DAILY",
        "LS_T8462_DAILY",
        "DERIVATIVES_PRICE_DAILY", "DERIVATIVES_INVESTOR_DAILY",
        "SHORT_SELLING_DAILY", "SHORT_SELLING_BALANCE_DAILY",
        "SHORT_SELLING_INVESTOR_DAILY", "LENDING_DAILY",
        "LIQUIDITY_CREDIT_DAILY", "BOK_TREASURY_OBSERVATION_DAILY",
        "BOK_FX_DAILY",
        "TOSS_KR_TREASURY_DAILY", "BROKER_SNAPSHOT",
    }
    assert {item.lane for item in DAILY_LANE_READINESS} == expected
    assert len({item.lane for item in DAILY_LANE_READINESS}) == len(DAILY_LANE_READINESS)
    assert {item.lane for item in DAILY_LANE_READINESS if item.scheduler_eligible} == {
            "FRED_DAILY", "LENDING_DAILY", "VKOSPI_DAILY", "KR_INDEX_DAILY",
            "KR_INDEX_FUNDAMENTAL_DAILY",
            "GLOBAL_INDEX_DAILY", "GLOBAL_ETF_DAILY", "GLOBAL_EQUITY_DAILY",
            "GLOBAL_COMMODITY_DAILY", "TOSSINVEST_US_QUOTES_30M", "CBOE_DAILY_PCR", "KB_TRANSACTIONS_DAILY", "KR_ETF_INVESTOR_FLOW_DAILY",
            "KR_ETF_PRICE_DAILY", "KR_EQUITY_PROVISIONAL_DAILY",
            "MARKET_INVESTOR_DAILY",
            "SHORT_SELLING_DAILY",
            "SHORT_SELLING_BALANCE_DAILY", "SHORT_SELLING_INVESTOR_DAILY",
            "CANONICAL_EQUITY_DAILY",
            "KOSPI200_BREADTH_DAILY",
            "DERIVATIVES_PRICE_DAILY",
            "LIQUIDITY_CREDIT_DAILY", "LS_T8462_DAILY",
            "TOSS_KR_TREASURY_DAILY",
            "BOK_FX_DAILY",
    }
    assert all(
        item.blocker
        for item in DAILY_LANE_READINESS
        if item.status is not LaneReadinessStatus.READY
    )
    short_lane = next(item for item in DAILY_LANE_READINESS if item.lane == "SHORT_SELLING_DAILY")
    assert short_lane.status is LaneReadinessStatus.READY
    assert short_lane.scheduler_eligible is True
    assert short_lane.blocker is None
    short_dataset = DATASET_OPERATIONS["kr_short_selling_trading_daily"]
    assert short_dataset.operational_status is OperationalStatus.AUTO_READY
    assert short_dataset.idempotency_status is IdempotencyStatus.CONFIRMED
    assert short_dataset.automation_enabled is True
    derivatives_lane = next(
        item for item in DAILY_LANE_READINESS
        if item.lane == "DERIVATIVES_PRICE_DAILY"
    )
    assert derivatives_lane.status is LaneReadinessStatus.READY
    assert derivatives_lane.scheduler_eligible is True
    assert derivatives_lane.blocker is None
    assert "T+1" in derivatives_lane.finality_handling
    bok_lane = next(
        item for item in DAILY_LANE_READINESS
        if item.lane == "BOK_TREASURY_OBSERVATION_DAILY"
    )
    assert bok_lane.status is LaneReadinessStatus.READY_WITH_FINALITY_GATE
    assert bok_lane.scheduler_eligible is False
    assert "retry zero" in bok_lane.api_operation
    assert "publication/revision" in bok_lane.blocker
    toss_lane = next(
        item for item in DAILY_LANE_READINESS
        if item.lane == "TOSS_KR_TREASURY_DAILY"
    )
    assert toss_lane.status is LaneReadinessStatus.READY
    assert toss_lane.scheduler_eligible is True

    us_quotes_lane = next(
        item for item in DAILY_LANE_READINESS
        if item.lane == "TOSSINVEST_US_QUOTES_30M"
    )
    assert us_quotes_lane.status is LaneReadinessStatus.READY
    assert us_quotes_lane.scheduler_eligible is True

    transactions_lane = next(
        item for item in DAILY_LANE_READINESS
        if item.lane == "KB_TRANSACTIONS_DAILY"
    )
    # Automation turned on 2026-09-05 after the first live run (34 calls, 201 rows, COMPLETE).
    assert transactions_lane.status is LaneReadinessStatus.READY
    assert transactions_lane.scheduler_eligible is True
    transactions = DATASET_OPERATIONS["kbsec_transactions_daily"]
    assert transactions.operational_status is OperationalStatus.AUTO_READY
    assert transactions.automation_enabled is True

    etf_flow_lane = next(
        item for item in DAILY_LANE_READINESS
        if item.lane == "KR_ETF_INVESTOR_FLOW_DAILY"
    )
    assert etf_flow_lane.status is LaneReadinessStatus.READY
    assert etf_flow_lane.scheduler_eligible is True
    assert etf_flow_lane.blocker is None
    etf_flow = DATASET_OPERATIONS["kr_etf_investor_flow_daily"]
    assert etf_flow.operational_status is OperationalStatus.AUTO_READY
    assert etf_flow.automation_enabled is True


def test_new_global_price_datasets_have_typed_operation_and_display_routes() -> None:
    equity = DATASET_OPERATIONS["global_equity_price_daily"]
    quotes = DATASET_OPERATIONS["tossinvest_us_quote_30m"]
    equity_universe = DATASET_UNIVERSE[equity.dataset_id]
    quote_universe = DATASET_UNIVERSE[quotes.dataset_id]

    assert equity.cadence is Cadence.GLOBAL_DAILY
    assert equity_universe.scheduler_lane == "GLOBAL_EQUITY_DAILY"
    assert equity_universe.gui_use is GuiUse.DIRECT
    assert equity_universe.retained_latest == "2026-09-03"
    assert quotes.cadence is Cadence.GLOBAL_30M
    assert quote_universe.scheduler_lane == "TOSSINVEST_US_QUOTES_30M"
    assert quote_universe.data_grain is DataGrain.INTRADAY
    assert quote_universe.gui_use is GuiUse.DIRECT
    assert quote_universe.retained_latest == "2026-09-04"
    assert quote_universe.physical_artifacts == (
        "normalized/tossinvest_us_quote_30m",
        "artifacts/intraday/tossinvest_us_quotes_latest.json",
    )


def test_core_registry_covers_each_retained_operations_family_without_enabling_scheduler() -> None:
    families = {
        "canonical": {
            "kr_equity_price_daily", "kr_equity_market_cap_daily",
            "kr_equity_universe_daily", "kr_equity_canonical_universe_daily",
            "kr_market_breadth_daily",
        },
        "short_selling": {
            "kr_short_selling_trading_daily", "kr_short_selling_balance_daily",
            "kr_short_selling_investor_daily",
        },
        "lending_liquidity_credit": {
            "kr_stock_lending_daily", "kr_stock_lending_market_daily",
            "kr_stock_lending_participant_daily", "kr_market_liquidity_daily",
            "kr_credit_balance_daily",
        },
        "derivatives": {
            "kr_derivatives_futures_daily", "kr_derivatives_options_daily",
            "kr_kospi200_futures_provider_bridge_daily",
            "kr_kospi200_options_provider_bridge_daily",
            "kr_kospi200_futures_nearest_listed_daily",
            "kr_kospi200_option_pcr_daily", "kr_kospi200_option_walls_daily",
            "kr_kospi200_futures_investor_net_purchase_daily",
        },
        "market_investor": {"kr_market_investor_net_purchase_bridge_daily"},
        "kospi200_breadth": {
            "kr_index_constituent_daily", "kr_kospi200_constituent_price_daily",
            "kr_kospi200_breadth_daily",
        },
    }
    registered = set(DATASET_OPERATIONS)
    assert all(ids <= registered for ids in families.values())
    assert {item.dataset_id for item in DATASET_OPERATIONS.select(executable_only=True)} == {
        "fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily",
        "kr_stock_lending_daily", "kr_stock_lending_market_daily",
        "kr_stock_lending_participant_daily", "kr_vkospi_daily",
        "kr_index_daily", "kr_kospi200_index_daily",
        "kr_index_fundamental_daily", "global_etf_price_daily",
        "global_equity_price_daily", "tossinvest_us_quote_30m", "cboe_daily_pcr_daily", "kbsec_transactions_daily", "kr_etf_investor_flow_daily",
        "kr_etf_master", "kr_etf_price_daily",
        "global_index_price_daily",
        "kr_market_investor_net_purchase_bridge_daily",
        "kr_short_selling_trading_daily",
        "kr_equity_canonical_universe_daily", "kr_equity_price_daily",
        "kr_equity_price_provisional_daily",
        "kr_equity_market_cap_daily", "kr_equity_universe_daily",
        "kr_market_breadth_daily",
        "kr_index_constituent_daily", "kr_kospi200_constituent_price_daily",
        "kr_kospi200_breadth_daily",
        "kr_kospi200_futures_provider_bridge_daily",
        "kr_kospi200_options_provider_bridge_daily",
        "kr_kospi200_futures_nearest_listed_daily",
        "kr_kospi200_option_pcr_daily", "kr_kospi200_option_walls_daily",
        "kr_market_liquidity_daily", "kr_credit_balance_daily",
        "kr_short_selling_balance_daily", "kr_short_selling_investor_daily",
        "ls_t8462_daily_raw", "kr_treasury_yield_daily",
        "bok_ecos_kr_treasury_yield_source_observation",
        "bok_ecos_usd_krw_daily",
        "kr_market_investor_trading_daily",
    }
    assert DATASET_OPERATIONS["kr_market_breadth_daily"].operational_eligibility is OperationalEligibility.ELIGIBLE
    assert DATASET_OPERATIONS["kr_index_constituent_daily"].idempotency_status is IdempotencyStatus.CONFIRMED
    assert DATASET_OPERATIONS["kr_kospi200_constituent_price_daily"].pit_status is PitStatus.PIT_SAFE
    assert DATASET_OPERATIONS["kr_kospi200_breadth_daily"].dashboard_required is True
    assert DATASET_OPERATIONS["kr_market_investor_net_purchase_bridge_daily"].predictive_eligibility is PredictiveEligibility.BLOCKED


def test_run_checkpoint_round_trip_and_transitions_are_fail_closed(tmp_path) -> None:
    planned = DailyRun(
        run_id="daily-20260817",
        run_date=date(2026, 8, 17),
        cadence_group=Cadence.KR_DAILY,
        status=DailyRunStatus.PLANNED,
        datasets_attempted=("b", "a"),
    )
    running = transition_run(planned, DailyRunStatus.RUNNING, at=AS_OF)
    finished = transition_run(
        running,
        DailyRunStatus.DEGRADED,
        at=AS_OF + timedelta(minutes=1),
        datasets_succeeded=("a",),
        datasets_failed=("b",),
        review_required=True,
    )
    path = tmp_path / "state" / "checkpoint.json"
    write_run_checkpoint(path, planned)
    write_run_checkpoint(path, running, expected_previous=planned)
    write_run_checkpoint(path, finished, expected_previous=running)
    write_run_checkpoint(path, finished)
    assert read_run_checkpoint(path) == finished
    assert path.read_bytes() == path.read_bytes()
    other = replace(planned, run_id="another-run")
    with pytest.raises(DailyRunLockError, match="identity"):
        write_run_checkpoint(path, other)
    with pytest.raises(DailyRunLockError, match="compare-and-swap"):
        write_run_checkpoint(path, running, expected_previous=planned)
    with pytest.raises(ValueError, match="invalid run transition"):
        transition_run(finished, DailyRunStatus.RUNNING, at=AS_OF + timedelta(minutes=2))


def test_run_checkpoint_replace_failure_preserves_previous_bytes(tmp_path, monkeypatch) -> None:
    planned = DailyRun(
        run_id="atomic-checkpoint",
        run_date=date(2026, 8, 17),
        cadence_group=Cadence.GLOBAL_DAILY,
        status=DailyRunStatus.PLANNED,
        datasets_attempted=("global_index_price_daily",),
    )
    path = tmp_path / "checkpoint.json"
    write_run_checkpoint(path, planned)
    before = path.read_bytes()
    running = transition_run(planned, DailyRunStatus.RUNNING, at=AS_OF)
    original_replace = type(path).replace

    def fail_replace(source, target):
        if source.name.endswith(".tmp"):
            raise OSError("injected replace failure")
        return original_replace(source, target)

    monkeypatch.setattr(type(path), "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        write_run_checkpoint(path, running, expected_previous=planned)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".*.tmp"))


def test_daily_run_lock_rejects_overlap_and_never_deletes_unknown_lock(tmp_path) -> None:
    path = tmp_path / "daily.lock"
    lock = DailyRunLock(path, run_id="run-one", acquired_at=AS_OF).acquire()
    with pytest.raises(DailyRunLockError, match="manual recovery"):
        DailyRunLock(path, run_id="run-two", acquired_at=AS_OF).acquire()
    original = path.read_bytes()
    assert json.loads(original)["run_id"] == "run-one"
    lock.release()
    assert not path.exists()
    path.write_text("stale-looking-but-unknown", encoding="utf-8")
    with pytest.raises(DailyRunLockError, match="manual recovery"):
        DailyRunLock(path, run_id="run-three", acquired_at=AS_OF).acquire()
    assert path.read_text(encoding="utf-8") == "stale-looking-but-unknown"


def test_daily_run_lock_refuses_non_owner_removal(tmp_path) -> None:
    path = tmp_path / "daily.lock"
    lock = DailyRunLock(path, run_id="owner", acquired_at=AS_OF).acquire()
    path.write_text("different-owner", encoding="utf-8")
    with pytest.raises(DailyRunLockError, match="ownership differs"):
        lock.release()
    assert path.exists()


def test_health_report_tier1_failure_controls_overall_but_research_block_does_not() -> None:
    run_id = "health-20260817"
    canonical = dataset_health_from_freshness(run_id, _canonical(), _fresh())
    ls_spec = DATASET_OPERATIONS["ls_t8462_daily_raw"]
    ls_blocked = dataset_health_from_freshness(run_id, ls_spec, _fresh(ls_spec, blocked=True))
    healthy = build_daily_health_report(run_id=run_id, as_of=AS_OF, datasets=(ls_blocked, canonical))
    assert healthy.overall_status is DailyRunStatus.SUCCEEDED
    assert healthy.critical_core_ready and healthy.dashboard_ready
    assert healthy.blocked_count == 1

    failed = replace(
        canonical,
        collector_status=StageStatus.FAILED,
        error_code=FailureCode.NETWORK_FAILURE,
        review_required=True,
    )
    report = build_daily_health_report(run_id=run_id, as_of=AS_OF, datasets=(failed, ls_blocked))
    assert report.overall_status is DailyRunStatus.FAILED
    assert report.critical_core_ready is False
    assert report.failed_count == 1


def test_health_serialization_is_deterministic_and_dataset_order_is_canonical() -> None:
    run_id = "health-json"
    canonical = dataset_health_from_freshness(run_id, _canonical(), _fresh())
    ls_spec = DATASET_OPERATIONS["ls_t8462_daily_raw"]
    ls_blocked = dataset_health_from_freshness(run_id, ls_spec, _fresh(ls_spec, blocked=True))
    report = build_daily_health_report(run_id=run_id, as_of=AS_OF, datasets=(ls_blocked, canonical))
    assert report.to_json_bytes() == report.to_json_bytes()
    payload = json.loads(report.to_json_bytes())
    assert [item["dataset_id"] for item in payload["datasets"]] == sorted(
        item["dataset_id"] for item in payload["datasets"]
    )
    with pytest.raises(ValueError, match="duplicate"):
        build_daily_health_report(run_id=run_id, as_of=AS_OF, datasets=(canonical, canonical))
    with pytest.raises(ValueError, match="counts differ"):
        replace(report, current_count=99)


@pytest.mark.parametrize("scheduled_slot", ["09:10", "14:10", "20:30"])
def test_kr_scheduler_definition_validator_accepts_slot_specific_fixture(
    tmp_path: Path, scheduled_slot: str,
) -> None:
    assert _validate_kr_registered_task(
        tmp_path, _kr_registered_task_fixture(scheduled_slot), scheduled_slot,
    ) == []


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("action_execute", "KR_ACTION_EXECUTE"),
        ("action_arguments", "KR_ACTION_ARGUMENTS"),
        ("action_workdir", "KR_ACTION_WORKDIR"),
        ("trigger_type", "KR_TRIGGER_TYPE"),
        ("trigger_disabled", "KR_TRIGGER_DISABLED"),
        ("trigger_days_interval", "KR_TRIGGER_DAYS_INTERVAL"),
        ("trigger_time", "KR_TRIGGER_TIMES"),
        ("trigger_repetition", "KR_TRIGGER_REPETITION"),
        ("settings_start_when_available", "KR_SETTINGS_START_WHEN_AVAILABLE"),
        ("settings_battery_start", "KR_SETTINGS_BATTERY_START"),
        ("settings_battery_stop", "KR_SETTINGS_BATTERY_STOP"),
        ("settings_wake_to_run", "KR_SETTINGS_WAKE_TO_RUN"),
        ("settings_multiple_instances", "KR_SETTINGS_MULTIPLE_INSTANCES"),
        ("settings_execution_time_limit", "KR_SETTINGS_EXECUTION_TIME_LIMIT"),
    ],
)
def test_kr_scheduler_definition_validator_reports_actionable_drift(
    tmp_path: Path, case: str, expected_code: str,
) -> None:
    fixture = _kr_registered_task_fixture()
    action = fixture["Actions"][0]
    trigger = fixture["Triggers"][0]
    settings = fixture["Settings"]
    if case == "action_execute":
        action["Execute"] = r"C:\wrong\python.exe"
    elif case == "action_arguments":
        action["Arguments"] = (
            f'"{KR_EXPECTED_RUNNER}" --bundle KR_MARKET_DAILY '
            '--scheduled-slot 09:10 --allow-latest-occurrence'
        )
    elif case == "action_workdir":
        action["WorkingDirectory"] = r"C:\wrong"
    elif case == "trigger_type":
        trigger["CimClass"]["CimClassName"] = "MSFT_TaskTimeTrigger"
    elif case == "trigger_disabled":
        trigger["Enabled"] = False
    elif case == "trigger_days_interval":
        trigger["DaysInterval"] = 2
    elif case == "trigger_time":
        trigger["StartBoundary"] = "2026-08-24T09:11:00+09:00"
    elif case == "trigger_repetition":
        trigger["Repetition"] = {"Interval": "PT30M", "Duration": "P1D"}
    elif case == "settings_start_when_available":
        settings["StartWhenAvailable"] = not settings["StartWhenAvailable"]
    elif case == "settings_battery_start":
        settings["DisallowStartIfOnBatteries"] = True
    elif case == "settings_battery_stop":
        settings["StopIfGoingOnBatteries"] = True
    elif case == "settings_wake_to_run":
        settings["WakeToRun"] = False
    elif case == "settings_multiple_instances":
        settings["MultipleInstances"] = "Parallel"
    elif case == "settings_execution_time_limit":
        settings["ExecutionTimeLimit"] = "PT15M"

    assert _validate_kr_registered_task(tmp_path, fixture) == [expected_code]


def test_kr_scheduler_validation_precedes_legacy_task_removal() -> None:
    source = REGISTER_TASKS_SCRIPT.read_text(encoding="utf-8")
    validation = source.index("$definitionErrors = @(Test-KrMarketDailyTaskDefinition")
    failure = source.index("throw \"registered Korean daily task failed semantic readback", validation)
    legacy_removal = source.index("foreach ($legacyName in $legacyKrDailyTaskNames)", failure)
    assert validation < failure < legacy_removal


def test_kr_scheduler_dry_run_defines_one_exact_task_per_slot() -> None:
    tasks = _kr_registration_dry_run()
    expected = {
        "STOCK_DATA_KR_MARKET_DAILY_0910": "09:10",
        "STOCK_DATA_KR_MARKET_DAILY_1410": "14:10",
        "STOCK_DATA_KR_MARKET_DAILY_2030": "20:30",
    }
    assert set(tasks) == set(expected)
    for task_name, scheduled_slot in expected.items():
        task = tasks[task_name]
        assert task["execute"].endswith(r"\.venv\Scripts\pythonw.exe")
        expected_suffix = (
            f"--bundle KR_MARKET_DAILY --scheduled-slot {scheduled_slot}"
        )
        if scheduled_slot == "20:30":
            expected_suffix += " --allow-latest-occurrence"
        assert task["arguments"].endswith(expected_suffix)
        assert task["schedule"] == f"daily@{scheduled_slot}"
        assert task["network_calls"] == "bounded_by_lane"
        assert task["power_policy"] == (
            "allow_battery_start,dont_stop_on_battery,wake_to_run"
        )


def test_kr_scheduler_dry_run_rejects_slot_time_override() -> None:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler dry-run test")
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "KrMarketDaily", "-ShortSellingTime", "10:00",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode != 0
    assert "slots are fixed" in completed.stderr


def test_issue_state_scheduler_dry_run_is_local_bounded_and_disabled_by_default() -> None:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler dry-run test")
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "IssueState",
        ],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    values = dict(
        line.partition("=")[::2] for line in completed.stdout.splitlines() if "=" in line
    )
    assert values["task"] == "STOCK_PROJECT_ISSUE_STATE_SYNC"
    assert values["arguments"].endswith('sync_issue_state.py" --project-root "' + str(REGISTER_TASKS_SCRIPT.parents[1]) + '" --enable-discovery')
    assert values["schedule"] == "daily@06:45"
    assert values["network_calls"] == "0"
    assert values["enabled_by_default"] == "False"
    assert values["requested_enabled"] == "False"
    enabled = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "IssueState", "-EnableIssueState",
        ],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    enabled_values = dict(
        line.partition("=")[::2] for line in enabled.stdout.splitlines() if "=" in line
    )
    assert enabled_values["requested_enabled"] == "True"
    assert enabled_values["network_calls"] == "0"
    source = REGISTER_TASKS_SCRIPT.read_text(encoding="utf-8")
    assert "Disable-ScheduledTask -TaskName $name" in source
    assert "Enable-ScheduledTask -TaskName $name" in source
    assert 'ExecutionTimeLimit -ne "PT5M"' in source
    assert '[bool]$registered.Settings.StartWhenAvailable -ne $false' in source
    assert '[bool]$registered.Settings.DisallowStartIfOnBatteries' in source
    assert '[bool]$registered.Settings.StopIfGoingOnBatteries' in source
    assert '-not [bool]$registered.Settings.WakeToRun' in source


def test_toss_account_scheduler_dry_run_is_exact_daily_read_only_route() -> None:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler dry-run test")
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "TossAccount",
        ],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    values = dict(
        line.partition("=")[::2]
        for line in completed.stdout.splitlines() if "=" in line
    )
    assert values["task"] == "STOCK_DATA_TOSS_ACCOUNT_DAILY"
    assert values["arguments"].endswith(
        'run_toss_account_snapshot.py" --project-root "'
        + str(REGISTER_TASKS_SCRIPT.parents[1]) + '"'
    )
    assert values["schedule"] == "daily@07:00"
    assert values["network_calls"] == "bounded_by_lane"
    assert values["enabled_by_default"] == "True"
    assert values["power_policy"] == (
        "allow_battery_start,dont_stop_on_battery,wake_to_run"
    )
    source = REGISTER_TASKS_SCRIPT.read_text(encoding="utf-8")
    assert "Test-AccountTaskDefinition" in source
    assert 'ExecutionTimeLimit -ne "PT5M"' in source
    assert 'MultipleInstances -ne "IgnoreNew"' in source


def test_toss_account_scheduler_rejects_time_override() -> None:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler dry-run test")
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "TossAccount", "-TossAccountTime", "08:00",
        ],
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert completed.returncode != 0
    assert "fixed at 07:00" in completed.stderr


def test_kb_account_scheduler_dry_run_is_exact_daily_read_only_route() -> None:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler dry-run test")
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "KbAccount",
        ],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    values = dict(
        line.partition("=")[::2]
        for line in completed.stdout.splitlines() if "=" in line
    )
    assert values["task"] == "STOCK_DATA_KBSEC_ACCOUNT_DAILY"
    assert values["arguments"].endswith(
        'run_kbsec_account_snapshot.py" --project-root "'
        + str(REGISTER_TASKS_SCRIPT.parents[1]) + '"'
    )
    assert values["schedule"] == "daily@07:10"
    assert values["network_calls"] == "bounded_by_lane"
    assert values["enabled_by_default"] == "True"
    assert values["requested_enabled"] == "True"
    assert values["power_policy"] == (
        "allow_battery_start,dont_stop_on_battery,wake_to_run"
    )
    source = REGISTER_TASKS_SCRIPT.read_text(encoding="utf-8")
    assert "Test-AccountTaskDefinition" in source
    assert 'ExecutionTimeLimit -ne "PT5M"' in source
    assert 'MultipleInstances -ne "IgnoreNew"' in source


def test_kb_account_scheduler_rejects_time_override() -> None:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler dry-run test")
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "KbAccount", "-KbAccountTime", "08:00",
        ],
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert completed.returncode != 0
    assert "fixed at 07:10" in completed.stderr


def test_bok_treasury_scheduler_dry_run_is_exact_window_observation_only() -> None:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler dry-run test")
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "BokTreasury",
        ],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    values = dict(
        line.partition("=")[::2]
        for line in completed.stdout.splitlines() if "=" in line
    )
    assert values["task"] == "STOCK_DATA_BOK_TREASURY_DAILY"
    assert values["arguments"].endswith(
        'run_bok_ecos_treasury_finality_observation.py" --project-root "'
        + str(REGISTER_TASKS_SCRIPT.parents[1]) + '"'
    )
    assert values["schedule"] == "daily@17:10"
    assert values["network_calls"] == "bounded_by_lane"
    assert values["power_policy"] == (
        "allow_battery_start,dont_stop_on_battery,wake_to_run"
    )
    source = REGISTER_TASKS_SCRIPT.read_text(encoding="utf-8")
    assert "$isIssueState -or $isBokTreasury" in source
    assert "three-batch review gate" in source


def test_bok_treasury_scheduler_rejects_time_override() -> None:
    if not WINDOWS_POWERSHELL.is_file():
        pytest.skip("Windows PowerShell is required for the scheduler dry-run test")
    completed = subprocess.run(
        [
            str(WINDOWS_POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-File", str(REGISTER_TASKS_SCRIPT), "-Action", "DryRun",
            "-Target", "BokTreasury", "-BokTreasuryTime", "18:10",
        ],
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert completed.returncode != 0
    assert "fixed at 17:10" in completed.stderr
