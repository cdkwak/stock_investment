from __future__ import annotations

import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault(
        "QT_QPA_FONTDIR",
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    )

import pytest
from PySide6 import QtCore, QtWidgets

from stock_data.gui.health_service import (
    DailyHealthArtifactService,
    HealthArtifactView,
    HealthDatasetRow,
    summarize_health_artifact,
)
from stock_data.gui.main_window import DataStatusPage, IndicatorControlPanel
from stock_data.gui.refresh_status import project_refresh_status
from stock_data.gui.services import (
    US_ETF_CHART_AUTHORIZED_SYMBOLS,
    US_ETF_CHART_IDENTITIES,
)
from stock_data.orchestration.daily_operations import (
    AutomationPolicy, DATASET_UNIVERSE, DataGrain, UniverseOperationalStatus,
)


def _write_health(tmp_path, rows):
    path = tmp_path / "artifacts/daily_health/universe_data_v2_20260819.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"datasets": rows}), encoding="utf-8")
    return path


def test_backtest_six_etfs_are_in_the_local_chart_catalog() -> None:
    by_symbol = {identity.symbol: identity for identity in US_ETF_CHART_IDENTITIES}

    assert {"VNQ", "IEF", "SHY"} <= US_ETF_CHART_AUTHORIZED_SYMBOLS
    assert {
        symbol: (by_symbol[symbol].name, by_symbol[symbol].listing_date)
        for symbol in ("VNQ", "IEF", "SHY")
    } == {
        "VNQ": ("Vanguard Real Estate ETF", "2004-09-29"),
        "IEF": ("iShares 7-10 Year Treasury Bond ETF", "2002-07-30"),
        "SHY": ("iShares 1-3 Year Treasury Bond ETF", "2002-07-30"),
    }
    assert all(by_symbol[symbol].leverage_multiple == 1 for symbol in ("VNQ", "IEF", "SHY"))


def _row(dataset, freshness, operational="ELIGIBLE", predictive="BLOCKED", *, latest="2026-08-13", expected="2026-08-14", runtime_coverage="NOT_PROBED"):
    return {
        "dataset_id": dataset,
        "actual_latest": latest,
        "expected_latest": expected,
        "freshness_status": freshness,
        "operational_eligibility": operational,
        "predictive_eligibility": predictive,
        "pit_status": "PIT_BLOCKED",
        "primary_source": "official source",
        "cadence": "KR_DAILY",
        "runtime_coverage": runtime_coverage,
    }


def _write_receipt(tmp_path, relative, payload):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _complete_yahoo_receipt():
    routes = (
        *(('GLOBAL_30M', item) for item in (
            'USD_KRW_60M', 'UST2_FUTURES_60M', 'UST10_FUTURES_60M',
            'UST30_FUTURES_60M', 'KOSPI_CURRENT_60M', 'KOSDAQ_CURRENT_60M',
            'SP500_CURRENT_60M', 'NASDAQ_CURRENT_60M', 'NQ_FUTURES_CURRENT_60M',
            'SOXX_CURRENT_60M', 'GOLD_CURRENT_60M', 'WTI_CURRENT_60M',
            'BITCOIN_CURRENT_60M',
            'SP500_FUTURES_CURRENT_60M', 'DOW_FUTURES_CURRENT_60M',
            'SOX_CURRENT_60M', 'DOLLAR_INDEX_CURRENT_60M',
        )),
        *(('NATIVE_15M', item) for item in ('^VIX', '^FVX', '^TNX', '^TYX')),
    )
    outcomes = [
        {
            "lane": lane,
            "series_id": series_id,
            "outcome": (
                "CURRENT_30M_ACCEPTED" if lane == "GLOBAL_30M"
                else "CURRENT_15M_ACCEPTED"
            ),
        }
        for lane, series_id in routes
    ]
    return {
        "status": "PASS", "finished_at_utc": "2026-08-26T07:02:14+00:00",
        "run_id": "yahoo-market-current-safe", "accepted": len(routes),
        "failed": 0, "api_calls": len(routes), "max_api_calls": len(routes),
        "preserved": 0, "series_terminal_outcomes": outcomes,
    }


def test_refresh_status_projects_scheduled_current_manual_and_unsupported_surfaces(tmp_path):
    _write_receipt(
        tmp_path, "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json",
        _complete_yahoo_receipt(),
    )
    _write_receipt(tmp_path, "artifacts/scheduler_logs/STOCK_DATA_DAILY_HEALTH_last.json", {
        "status": "SUCCESS", "finished_at_utc": "2026-08-26T05:10:50+00:00",
        "dataset_count": 80, "runtime_coverage_validated_count": 21,
        "runtime_coverage_failure_count": 0, "api_calls": 0,
    })
    metrics = {"KOSPI": SimpleNamespace(
        dataset_id="market_price_60m_current", displays_value=True,
        source_timestamp="2026-08-26T06:30:00+00:00",
    )}
    account = SimpleNamespace(
        available=True, as_of="2026-08-26T06:00:00+00:00",
        last_reconciled_at="2026-08-26T06:01:00+00:00",
    )

    projection = project_refresh_status(
        tmp_path,
        health={
            "managed_total": 20, "managed_acceptable": 20,
            "managed_current": 18, "managed_expected_lag": 2,
        },
        metrics=metrics,
        account=account,
        generated_at_utc="2026-08-26T07:03:00+00:00",
    )

    assert tuple(row.surface_id for row in projection.surfaces) == (
        "DASHBOARD_CURRENT", "DATA_HEALTH", "ACCOUNT_SNAPSHOT", "US_MARKET_FLOW",
    )
    current = projection.surface("DASHBOARD_CURRENT")
    assert current.cadence_seconds == 1800
    assert current.source_as_of == "2026-08-26T06:30:00+00:00"
    assert current.last_success_receipt_id == "yahoo-market-current-safe"
    assert current.next_eligible_at is None
    assert current.retry_action_id == "dashboard-local-reread"
    health = projection.surface("DATA_HEALTH")
    assert health.freshness_state == "EXPECTED_LAG"
    assert health.retained_value_state == "DISPLAYABLE_WITH_WARNING"
    assert projection.surface("ACCOUNT_SNAPSHOT").next_eligible_basis == "MANUAL_ONLY"
    unsupported = projection.surface("US_MARKET_FLOW")
    assert unsupported.operation_state == "UNSUPPORTED"
    assert unsupported.retry_action_id is None


def test_refresh_status_fails_closed_for_partial_or_malformed_local_metadata(tmp_path):
    _write_receipt(tmp_path, "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json", {
        "status": "PASS", "finished_at_utc": "not-a-time",
        "run_id": "../../unsafe",
    })
    metrics = {
        "accepted": SimpleNamespace(
            dataset_id="market_price_60m_current", displays_value=True,
            source_timestamp="2026-08-26T06:30:00+00:00",
        ),
        "failed": SimpleNamespace(
            dataset_id="market_price_15m_current", displays_value=False,
            source_timestamp="2026-08-26T06:45:00+00:00",
        ),
    }

    projection = project_refresh_status(
        tmp_path, health={}, metrics=metrics,
        generated_at_utc="2026-08-26T07:03:00+00:00",
    )

    current = projection.surface("DASHBOARD_CURRENT")
    assert current.operation_state == "PARTIAL_FAILURE"
    assert current.retained_value_state == "DISPLAYABLE_WITH_WARNING"
    assert current.last_success_at is None
    assert current.last_success_receipt_id is None
    assert projection.surface("DATA_HEALTH").freshness_state == "UNKNOWN"


def test_refresh_status_does_not_accept_partial_pass_receipt_as_last_success(tmp_path):
    _write_receipt(tmp_path, "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json", {
        "status": "PASS", "finished_at_utc": "2026-08-26T07:02:14+00:00",
        "run_id": "partial-fragment",
    })
    projection = project_refresh_status(
        tmp_path, health={}, metrics={"KOSPI": SimpleNamespace(
            dataset_id="market_price_60m_current", displays_value=True,
            source_timestamp="2026-08-26T06:30:00+00:00",
        )}, generated_at_utc="2026-08-26T07:03:00+00:00",
    )

    current = projection.surface("DASHBOARD_CURRENT")
    assert current.operation_state == "SUCCEEDED"
    assert current.last_success_at is None
    assert current.last_success_receipt_id is None


@pytest.mark.parametrize("source_timestamp", [None, "", "not-a-time", "2026-08-26T06:30:00"])
def test_refresh_status_suppresses_display_metric_without_aware_source_timestamp(
    tmp_path, source_timestamp,
):
    projection = project_refresh_status(
        tmp_path, health={}, metrics={"KOSPI": SimpleNamespace(
            dataset_id="market_price_60m_current", displays_value=True,
            source_timestamp=source_timestamp,
        )}, generated_at_utc="2026-08-26T07:03:00+00:00",
    )

    current = projection.surface("DASHBOARD_CURRENT")
    assert current.operation_state == "FAILED"
    assert current.freshness_state == "UNKNOWN"
    assert current.retained_value_state == "SUPPRESSED"
    assert "SOURCE_TIMESTAMP_INVALID" in current.reason_codes


def test_data_status_renders_refresh_lifecycle_and_local_reread_only(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    projection = project_refresh_status(
        tmp_path,
        health={
            "managed_total": 1, "managed_acceptable": 1,
            "managed_current": 1, "managed_expected_lag": 0,
        },
        metrics={},
        generated_at_utc="2026-08-26T07:03:00+00:00",
    )
    page = DataStatusPage()
    requested = []
    page.refresh_status_reread_requested.connect(lambda: requested.append(True))

    page.render_refresh_status(projection)
    page.refresh_lifecycle_reread.click()
    app.processEvents()

    assert page.refresh_lifecycle_table.rowCount() == 4
    assert page.refresh_lifecycle_table.item(0, 0).text() == "현재 시장"
    assert "다음 시각 미확정" in page.refresh_lifecycle_table.item(0, 5).text()
    assert page.refresh_lifecycle_table.item(3, 2).text() == "미지원"
    assert requested == [True]
    assert "API나 예약 작업" in page.refresh_lifecycle_reread.toolTip()


def test_health_artifact_adapter_maps_legacy_fields_from_registry_and_dates(tmp_path):
    _write_health(tmp_path, [
        _row("kr_index_daily", "UNKNOWN", latest="2026-08-14", expected="2026-08-14"),
        _row("fred_vix_daily", "UNKNOWN"),
    ])

    view = DailyHealthArtifactService(tmp_path).load()

    assert view.artifact_state == "READY"
    assert len(view.rows) == 91  # registry: +global equity and Toss U.S. quotes
    by_id = {row.dataset: row for row in view.rows}
    assert by_id["fred_vix_daily"].role == "SOURCE"
    assert by_id["fred_vix_daily"].pit == "PIT_LIMITED"
    assert by_id["kr_index_daily"].freshness == "CURRENT"
    assert by_id["kr_index_daily"].operational == "READY_WITH_FINALITY_GATE"
    assert by_id["kr_index_daily"].runtime_coverage == "NOT_PROBED"
    assert by_id["us_cftc_legacy_futures_only_raw"].freshness == "UNKNOWN"


def test_health_preserves_typed_current_when_retained_date_is_ahead_of_availability(tmp_path):
    _write_health(tmp_path, [
        _row(
            "global_commodity_futures_daily", "CURRENT",
            latest="2026-08-18", expected="2026-08-17",
        ),
    ])

    row = next(
        item for item in DailyHealthArtifactService(tmp_path).load().rows
        if item.dataset == "global_commodity_futures_daily"
    )

    assert row.freshness == "CURRENT"


def test_health_ignores_one_unregistered_artifact_row_and_reports_warning(tmp_path):
    _write_health(tmp_path, [
        _row("kr_index_daily", "CURRENT", latest="2026-09-02", expected="2026-09-02"),
        _row("future_registry_dataset", "UNKNOWN", latest=None, expected=None),
    ])

    view = DailyHealthArtifactService(tmp_path).load()

    assert view.artifact_state == "READY"
    assert len(view.rows) == 91
    assert view.unregistered_dataset_ids == ("future_registry_dataset",)
    assert "future_registry_dataset" in str(view.warning)
    assert next(row for row in view.rows if row.dataset == "kr_index_daily").display_status == "CURRENT"


def test_health_adapter_keeps_pending_gap_current(tmp_path):
    pending = _row(
        "kr_index_daily", "CURRENT", latest="2026-09-03", expected="2026-09-04",
    )
    pending["pending_until"] = "20:45"
    pending["due_at"] = "2026-09-04T20:45:00+09:00"
    _write_health(tmp_path, [pending])

    row = next(
        item for item in DailyHealthArtifactService(tmp_path).load().rows
        if item.dataset == "kr_index_daily"
    )

    assert row.freshness == "CURRENT"
    assert row.display_status == "CURRENT"
    assert row.pending_until == "20:45"
    assert row.display_reason == "수집 예정 시각 전 (20:45)"


def test_health_failure_requires_an_enabled_lane_and_failed_last_run(tmp_path):
    enabled = _row("kr_index_daily", "CURRENT", latest="2026-09-02", expected="2026-09-02")
    enabled["last_run"] = {"status": "FAILED"}
    preserved = _row(
        "kr_equity_foreign_ownership_daily", "STALE",
        latest="2026-08-12", expected="2026-09-02",
    )
    preserved["last_run"] = {"status": "FAILED"}
    _write_health(tmp_path, [enabled, preserved])

    view = DailyHealthArtifactService(tmp_path).load()

    rows = {row.dataset: row for row in view.rows}
    assert rows["kr_index_daily"].display_status == "FAILED"
    assert rows["kr_equity_foreign_ownership_daily"].display_status == "PRESERVED"


def test_current_retained_health_artifact_has_useful_compatibility_view():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    view = DailyHealthArtifactService(root).load()
    assert view.artifact_state == "READY"
    assert len(view.rows) == 91  # registry: +global equity and Toss U.S. quotes
    assert len(DailyHealthArtifactService.filter_rows(view.rows, "DAILY")) == 70
    blocked = DailyHealthArtifactService.filter_rows(view.rows, "BLOCKED")
    assert tuple(row.dataset for row in blocked) == tuple(
        dataset for dataset, spec in DATASET_UNIVERSE.items()
        if spec.operational_status is UniverseOperationalStatus.BLOCKED
    )
    assert all(row.role != "UNKNOWN" for row in view.rows)
    assert all(row.source != "UNKNOWN" for row in view.rows)


def test_health_summary_separates_managed_automation_from_full_inventory():
    statuses = ("CURRENT", "EXPECTED_LAG", "STALE", "UNKNOWN", "NOT_APPLICABLE")
    rows = tuple(
        HealthDatasetRow(
            f"managed_{index}", "SOURCE", "DAILY", "N/A", "N/A", status,
            "READY", "N/A", "PIT_LIMITED", "SCHEDULED / ENABLED",
            "fixture", "NOT_PROBED",
        )
        for index, status in enumerate(statuses)
    ) + (
        HealthDatasetRow(
            "inventory_only", "SOURCE", "DAILY", "N/A", "N/A", "CURRENT",
            "READY", "N/A", "PIT_LIMITED", "MANUAL / DISABLED",
            "fixture", "NOT_PROBED",
        ),
    )

    summary = summarize_health_artifact(
        HealthArtifactView("READY", "fixture", rows)
    )

    assert summary["overall"] == "DEGRADED"
    assert summary["managed_total"] == 5
    assert summary["managed_acceptable"] == 4
    assert summary["managed_current"] == 1
    assert summary["managed_expected_lag"] == 1
    assert summary["managed_stale"] == 1
    assert summary["managed_unknown"] == 1
    assert summary["managed_not_applicable"] == 1
    assert summary["current"] == 2


def test_health_summary_uses_only_stale_or_unknown_kospi200_chain_rows_for_decision_hold():
    chain_rows = tuple(
        HealthDatasetRow(
            dataset, "SOURCE", "DAILY", "2026-08-27", "2026-08-28", freshness,
            "READY", "N/A", "PIT_SAFE", "SCHEDULED / ENABLED", "fixture", "VALIDATED",
        )
        for dataset, freshness in (
            ("kr_index_constituent_daily", "STALE"),
            ("kr_kospi200_constituent_price_daily", "UNKNOWN"),
            ("kr_kospi200_breadth_daily", "STALE"),
        )
    )
    unrelated_stale = HealthDatasetRow(
        "unrelated", "SOURCE", "DAILY", "2026-08-27", "2026-08-28", "STALE",
        "READY", "N/A", "PIT_SAFE", "SCHEDULED / ENABLED", "fixture", "VALIDATED",
    )

    held = summarize_health_artifact(HealthArtifactView("READY", "fixture", chain_rows))
    lifted = summarize_health_artifact(HealthArtifactView(
        "READY", "fixture", tuple(
            HealthDatasetRow(
                row.dataset, row.role, row.cadence, "2026-08-28", "2026-08-28", "CURRENT",
                row.operational, row.blocker, row.pit, row.automation, row.source,
                row.runtime_coverage,
            )
            for row in chain_rows
        ) + (unrelated_stale,),
    ))

    assert held["decision_hold_causes"] == (
        "KOSPI200_BREADTH_DEPENDENCY_FRESHNESS_UNRESOLVED",
    )
    assert lifted["decision_hold_causes"] == ()


def test_data_health_projects_numeric_free_kospi200_decision_hold(tmp_path):
    projection = project_refresh_status(
        tmp_path,
        health={
            "managed_total": 3, "managed_acceptable": 0,
            "managed_current": 0, "managed_expected_lag": 0,
            "decision_hold_causes": (
                "KOSPI200_BREADTH_DEPENDENCY_FRESHNESS_UNRESOLVED",
            ),
        },
        metrics={}, generated_at_utc="2026-08-28T11:00:00+00:00",
    )

    surface = projection.surface("DATA_HEALTH")
    assert surface.operation_state == "PARTIAL_FAILURE"
    assert surface.freshness_state == "STALE"
    assert surface.retained_value_state == "DISPLAYABLE_WITH_WARNING"
    assert surface.reason_codes == (
        "DECISION_HOLD", "KOSPI200_BREADTH_DEPENDENCY_FRESHNESS_UNRESOLVED",
        "RETAINED_VALUE_STALE",
    )


def test_health_summary_and_surface_preserve_unmanaged_stale_rows(tmp_path):
    current = HealthDatasetRow(
        "managed", "SOURCE", "DAILY", "2026-08-26", "2026-08-26", "CURRENT",
        "READY", "N/A", "PIT_LIMITED", "SCHEDULED / ENABLED",
        "fixture", "VALIDATED", "ELIGIBLE", "DISPLAY_DIRECT_CONTRACT",
    )
    stale_display = HealthDatasetRow(
        "manual_display", "SOURCE", "DAILY", "2026-08-20", "2026-08-26", "STALE",
        "MANUAL_READY", "N/A", "PIT_BLOCKED", "MANUAL / DISABLED",
        "fixture", "NOT_PROBED", "LIMITED", "DISPLAY_STATUS_ONLY",
    )
    summary = summarize_health_artifact(
        HealthArtifactView("READY", "fixture", (current, stale_display))
    )

    assert summary["overall"] == "CURRENT"
    assert summary["managed_acceptable"] == summary["managed_total"] == 1
    assert summary["display_total"] == 2
    assert summary["display_stale"] == 0
    assert summary["display_gap"] == 0

    projection = project_refresh_status(
        tmp_path, health=summary, metrics={}, account=None,
        generated_at_utc="2026-08-26T11:40:00+00:00",
    )
    surface = projection.surface("DATA_HEALTH")
    assert surface.operation_state == "SUCCEEDED"
    assert surface.freshness_state == "CURRENT"
    assert "VISIBLE_DATA_GAPS" not in surface.reason_codes


def test_current_retained_health_artifact_managed_automation_regression():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    view = DailyHealthArtifactService(root).load()
    summary = summarize_health_artifact(view)
    managed = [
        row for row in view.rows if row.automation.endswith(" / ENABLED")
    ]
    managed_counts = {
        freshness: sum(row.freshness == freshness for row in managed)
        for freshness in (
            "CURRENT", "EXPECTED_LAG", "STALE", "UNKNOWN", "NOT_APPLICABLE",
        )
    }
    dataset_keys = [row.dataset for row in view.rows]

    assert view.artifact_state == "READY"
    assert len(view.rows) == 91  # registry: +global equity and Toss U.S. quotes
    assert all(dataset and dataset == dataset.strip() for dataset in dataset_keys)
    assert len(set(dataset_keys)) == len(dataset_keys)
    assert summary["managed_total"] == sum(
        spec.automation_enabled for spec in DATASET_UNIVERSE.values()
    )
    assert len(managed) == summary["managed_total"]
    assert summary["managed_current"] == managed_counts["CURRENT"]
    assert summary["managed_expected_lag"] == managed_counts["EXPECTED_LAG"]
    assert summary["managed_stale"] == managed_counts["STALE"]
    assert summary["managed_unknown"] == managed_counts["UNKNOWN"]
    assert summary["managed_not_applicable"] == managed_counts["NOT_APPLICABLE"]
    assert summary["managed_acceptable"] == sum(
        row.display_status not in {"LATE", "FAILED"} for row in managed
    )
    assert sum(managed_counts.values()) == summary["managed_total"]


def test_health_artifact_filters_are_deterministic_and_explicit(tmp_path):
    _write_health(tmp_path, [
        _row("kr_kospi200_index_daily", "UNKNOWN", latest="2026-08-14", expected="2026-08-14"),
        _row("kr_equity_canonical_universe_daily", "EXPECTED_LAG", latest="2026-08-13", expected="2026-08-13"),
        _row("fred_vix_daily", "UNKNOWN"),
        _row("global_etf_price_daily", "BLOCKED", "BLOCKED"),
        _row("ls_t8462_daily_raw", "UNKNOWN", predictive="RESEARCH_ONLY", latest="2026-08-14", expected="2026-08-14"),
    ])
    service = DailyHealthArtifactService(tmp_path)
    rows = service.load().rows
    expected = {
        "ALL": tuple(DATASET_UNIVERSE),
        "DAILY": tuple(
            dataset for dataset, spec in DATASET_UNIVERSE.items()
            if spec.data_grain is DataGrain.DAILY
        ),
        "BLOCKED": tuple(
            dataset for dataset, spec in DATASET_UNIVERSE.items()
            if spec.operational_status is UniverseOperationalStatus.BLOCKED
        ),
        "RESEARCH/STATIC": tuple(
            dataset for dataset, spec in DATASET_UNIVERSE.items()
            if spec.automation_policy in {
                AutomationPolicy.NO_REFRESH, AutomationPolicy.RESEARCH_ONLY,
            }
        ),
        "OPERATIONAL": tuple(
            dataset for dataset, spec in DATASET_UNIVERSE.items()
            if spec.operational_status not in {
                UniverseOperationalStatus.NOT_APPLICABLE,
                UniverseOperationalStatus.BLOCKED,
            }
        ),
    }
    for status_filter, expected_datasets in expected.items():
        assert tuple(
            row.dataset for row in service.filter_rows(rows, status_filter)
        ) == expected_datasets


def test_health_artifact_invalid_or_duplicate_rows_fail_closed(tmp_path):
    _write_health(tmp_path, [_row("same", "CURRENT"), _row("same", "CURRENT")])
    view = DailyHealthArtifactService(tmp_path).load()
    assert view.artifact_state == "REPORT NOT AVAILABLE"
    assert view.rows == ()


def test_data_status_issue_first_layout_preserves_all_typed_detail_and_filters(tmp_path):
    _write_health(tmp_path, [
        _row("kr_kospi200_index_daily", "UNKNOWN", latest="2026-08-14", expected="2026-08-14"),
        _row("global_etf_price_daily", "BLOCKED", "BLOCKED"),
    ])
    view = DailyHealthArtifactService(tmp_path).load()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DataStatusPage()
    page.render_report(view)
    expected_issues = sum(
        row.freshness in {"STALE", "UNKNOWN"} or row.operational == "BLOCKED"
        for row in view.rows
    )
    assert page.status_filter.currentText() == "확인 필요"
    assert page.status_filter.currentData() == "ISSUES"
    assert page.table.columnCount() == 5
    assert page.table.rowCount() == expected_issues
    assert [page.table.horizontalHeaderItem(index).text() for index in range(5)] == [
        "데이터", "상태", "기준일", "예상일", "자동 업데이트",
    ]
    assert page.table.item(0, 1).text().startswith("● ")
    assert page.table.item(0, 1).text()[2:] in {
        "최신 확정", "발행 대기", "갱신 필요", "확인 필요", "해당 없음",
    }
    assert "typed freshness=" in page.table.item(0, 1).toolTip()
    assert "provenance/source=" in page.table.item(0, 1).toolTip()
    assert "automation=" in page.table.item(0, 1).toolTip()
    assert "runtime coverage=" in page.table.item(0, 1).toolTip()
    assert "dataset_id=" in page.detail_text.text()
    assert "blocker=" in page.detail_text.text()
    assert "PIT=" in page.detail_text.text()

    page.status_filter.setCurrentText("전체 데이터")
    assert page.table.rowCount() == 91
    dataset_ids = {
        page.table.item(row, 0).data(QtCore.Qt.UserRole).dataset
        for row in range(page.table.rowCount())
    }
    assert len(dataset_ids) == 91
    assert "자동 운영" in page.overall.body.text()
    assert "갱신 필요" in page.overall.body.text()
    assert "화면 후보" in page.overall.body.text()
    assert "최신 확정" in page.freshness.body.text()
    assert "발행 대기" in page.eligibility.body.text()
    assert "아티팩트 EXPECTED_LAG" in page.eligibility.body.text()
    assert "전체 91" in page.boundary.body.text()

    expected_research_static = sum(
        row.automation.startswith(("RESEARCH_ONLY", "NO_REFRESH"))
        for row in view.rows
    )
    page.status_filter.setCurrentText("연구/정적")
    assert page.table.rowCount() == expected_research_static
    assert f"연구/정적 {page.table.rowCount()}" in page.boundary.body.text()
    assert all(
        row.automation.startswith(("RESEARCH_ONLY", "NO_REFRESH"))
        for row in page._visible_rows
    )
    page.status_filter.setCurrentText("전체 데이터")

    area_total = 0
    for area in page.AREAS[1:]:
        page.area_filter.setCurrentText(area)
        area_total += page.table.rowCount()
    assert area_total == 91
    page.area_filter.setCurrentText("전체 영역")
    page.status_filter.setCurrentText("일별 데이터")
    assert page.table.rowCount() == 70

    page.resize(1600, 900)
    page.show()
    app.processEvents()
    assert page.horizontalScrollBar().maximum() == 0
    assert page.table.horizontalScrollBar().maximum() == 0
    for card in (page.overall, page.freshness, page.eligibility, page.boundary):
        required_height = card.body.fontMetrics().boundingRect(
            QtCore.QRect(0, 0, max(card.body.width(), 1), 1000),
            QtCore.Qt.TextWordWrap,
            card.body.text(),
        ).height()
        assert required_height <= card.body.height()
    assert page.table.isSortingEnabled()
    assert page.status_filter.focusPolicy() != QtCore.Qt.NoFocus
    assert page.area_filter.focusPolicy() != QtCore.Qt.NoFocus
    assert page.table.focusPolicy() != QtCore.Qt.NoFocus
    assert page.detail_panel.isCheckable()
    page.detail_panel.setChecked(False)
    assert not page.detail_text.isVisible()
    page.detail_panel.setChecked(True)
    assert page.detail_text.isVisible()
    page.close()
    app.processEvents()


def test_data_status_summary_cards_fit_complete_wrapped_text_at_1600x900(tmp_path):
    _write_health(tmp_path, [
        _row("kr_kospi200_index_daily", "UNKNOWN"),
        _row("global_etf_price_daily", "BLOCKED", "BLOCKED"),
    ])
    view = DailyHealthArtifactService(tmp_path).load()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page = DataStatusPage()
    page.resize(1600, 840)
    page.show()
    page.render_report(view)
    app.processEvents()

    cards = (page.overall, page.freshness, page.eligibility, page.boundary)
    assert len(cards) == 4
    assert len({card.height() for card in cards}) == 1
    for card in cards:
        required_height = card.body.fontMetrics().boundingRect(
            QtCore.QRect(0, 0, max(card.body.width(), 1), 10_000),
            QtCore.Qt.TextWordWrap,
            card.body.text(),
        ).height()
        assert required_height <= card.body.height()
        assert card.accessibleDescription() == card.body.text().replace("\n", " · ")

    assert "자동 운영" in page.overall.body.text()
    assert "전체 91" in page.boundary.body.text()
    assert "확인 대상 91" in page.boundary.body.text()
    page.close()
    app.processEvents()


@pytest.mark.parametrize("width", (1700, 1270, 1060, 900, 840))
def test_dashboard_indicator_controls_reflow_without_clipping_or_lost_focus(
    width,
):
    qt_messages = []
    previous_handler = QtCore.qInstallMessageHandler(
        lambda message_type, _context, message: qt_messages.append(
            (message_type, message)
        )
    )
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = IndicatorControlPanel(allows_lower_panels=True)
    try:
        panel.resize(width, 200)
        panel.show()
        app.processEvents()
        panel_controls = tuple(panel._control_widgets)
        panel_rects = [
            QtCore.QRect(widget.mapTo(panel, QtCore.QPoint()), widget.size())
            for widget in panel_controls
        ]
        assert all(widget.isVisible() for widget in panel_controls)
        assert all(panel.rect().contains(rect) for rect in panel_rects)
        assert all(
            widget.width() >= widget.sizeHint().width()
            for widget in panel_controls
        )
        for index, left in enumerate(panel_rects):
            assert all(not left.intersects(right) for right in panel_rects[index + 1:])

        interactive = tuple(
            widget for widget in panel_controls
            if isinstance(
                widget,
                (QtWidgets.QCheckBox, QtWidgets.QComboBox, QtWidgets.QPushButton),
            )
        )
        assert all(widget.focusPolicy() & QtCore.Qt.TabFocus for widget in interactive)
        assert all(widget.accessibleName().strip() for widget in interactive)
        focus_order = []
        cursor = interactive[0]
        for _ in range(512):
            if cursor in interactive and cursor not in focus_order:
                focus_order.append(cursor)
            cursor = cursor.nextInFocusChain()
            if cursor is interactive[0]:
                break
        assert tuple(focus_order) == interactive
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()
        QtCore.qInstallMessageHandler(previous_handler)
    assert not qt_messages
