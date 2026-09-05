from datetime import date, datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stock_web.api.data_page import (
    FILTER_LABELS,
    FRESHNESS,
    _age_badge,
    _age_sessions,
    _display_date,
    _next_collection_hint,
    _result_code,
    build_data_page_context,
    load_credential_expiries,
    load_scheduler_receipts,
)
from tests.unit.web import new_temp_root


def test_data_page_uses_five_truthful_korean_health_classes() -> None:
    assert FRESHNESS == {
        "CURRENT": ("정시", "current"),
        "LATE": ("지연", "late"),
        "FAILED": ("실패", "failed"),
        "PRESERVED": ("수동/보존", "preserved"),
        "REFERENCE": ("참고", "reference"),
    }
    assert FILTER_LABELS == {
        "OPERATIONAL": "운영 데이터", "DAILY": "일별", "BLOCKED": "차단",
        "ALL": "전체", "UNKNOWN": "미확인",
    }
    assert _display_date("N/A") == "해당 없음"
    assert _result_code("—")["label"] == "—"


def test_credential_expiries_read_only_dates_and_flag_soon_or_expired() -> None:
    tmp_path = new_temp_root()
    (tmp_path / ".env").write_text(
        "SOME_API_KEY=secret-value\n"
        "SOME_API_KEY_EXPIRES_AT=2026-09-10\n"
        "OLD_KEY_EXPIRES_AT=2026-01-01\n"
        "FAR_KEY_EXPIRES_AT=2027-01-01T00:00:00\n"
        "BAD_KEY_EXPIRES_AT=soon\n",
        encoding="utf-8",
    )
    rows = load_credential_expiries(tmp_path, today=date(2026, 9, 3))
    by_name = {row["name"]: row for row in rows}
    assert set(by_name) == {"SOME_API_KEY", "OLD_KEY", "FAR_KEY", "BAD_KEY"}
    assert by_name["SOME_API_KEY"]["status"] == "soon" and by_name["SOME_API_KEY"]["days_left"] == 7
    assert by_name["OLD_KEY"]["status"] == "expired"
    assert by_name["FAR_KEY"]["status"] == "ok" and by_name["FAR_KEY"]["expires"] == "2027-01-01"
    assert by_name["BAD_KEY"]["status"] == "unknown"
    assert "secret-value" not in repr(rows)
    assert [row["name"] for row in rows[:2]] == ["OLD_KEY", "SOME_API_KEY"]


def test_credential_expiries_without_env_file() -> None:
    tmp_path = new_temp_root()
    assert load_credential_expiries(tmp_path) == []


def test_data_page_context_survives_unregistered_health_dataset() -> None:
    tmp_path = new_temp_root()
    path = tmp_path / "artifacts/daily_health/universe_data_v2_20260819.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"datasets": [
        {
            "dataset": "kr_index_daily", "latest": "2026-09-02",
            "expected": "2026-09-02", "freshness": "EXPECTED_LAG",
        },
        {"dataset": "future_dataset", "latest": None, "expected": None},
    ]}), encoding="utf-8")

    context = build_data_page_context(tmp_path, "ALL")

    assert context["health_state"] == "READY"
    assert context["unregistered_dataset_ids"] == ("future_dataset",)
    assert "future_dataset" in str(context["health_warning"])
    assert context["health_summary"]["unregistered_count"] == 1
    assert context["show_result_code"] is False
    assert next(
        row for group in context["health_groups"] for row in group["rows"]
        if row["dataset"] == "kr_index_daily"
    )["freshness"]["label"] == "정시"


def test_data_page_prefers_stable_latest_health_pointer() -> None:
    tmp_path = new_temp_root()
    root = tmp_path / "artifacts/daily_health"
    root.mkdir(parents=True)
    old = {"datasets": [{
        "dataset": "kr_index_daily", "latest": "2026-09-01",
        "expected": "2026-09-02", "freshness": "STALE",
    }]}
    latest = {"datasets": [{
        "dataset": "kr_index_daily", "latest": "2026-09-02",
        "expected": "2026-09-02", "freshness": "CURRENT",
        "coverage_source": "static_table",
        "runtime_coverage": "NOT_PROBED",
        "display_reason": "최신일이 예상일 이상 · 표는 손으로 적은 값",
    }]}
    (root / "universe_data_v2_20260819.json").write_text(
        json.dumps(old), encoding="utf-8",
    )
    (root / "universe_data_v2_latest.json").write_text(
        json.dumps(latest), encoding="utf-8",
    )

    context = build_data_page_context(tmp_path, "ALL")
    row = next(
        row for group in context["health_groups"] for row in group["rows"]
        if row["dataset"] == "kr_index_daily"
    )

    assert row["latest"] == "2026-09-02"
    assert row["display_reason"].endswith("표는 손으로 적은 값")


def test_data_page_labels_pending_current_row_with_due_time() -> None:
    tmp_path = new_temp_root()
    root = tmp_path / "artifacts/daily_health"
    root.mkdir(parents=True)
    (root / "universe_data_v2_latest.json").write_text(json.dumps({"datasets": [{
        "dataset": "kr_index_daily", "latest": "2026-09-03",
        "expected": "2026-09-04", "freshness": "STALE",
        "display_status": "CURRENT",
        "due_at": "2026-09-04T20:45:00+09:00", "pending_until": "20:45",
    }]}), encoding="utf-8")

    context = build_data_page_context(
        tmp_path,
        "ALL",
        now=datetime(2026, 9, 4, 20, 20, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    row = next(
        row for group in context["health_groups"] for row in group["rows"]
        if row["dataset"] == "kr_index_daily"
    )

    assert row["freshness"] == {
        "raw": "CURRENT", "label": "정시", "class": "current",
    }
    assert row["age_sessions"] == 1
    assert row["age_label"] == "1일 전"
    assert row["age_class"] == "age-neutral"
    assert row["next_collection"] == "20:30 수집 예정"
    assert row["collection_pending"] is True
    assert context["health_summary"]["display_current"] >= 1
    assert context["health_summary"]["stale"] >= 1


def test_age_sessions_uses_each_exchange_calendar() -> None:
    assert _age_sessions(
        "2026-08-14", today=date(2026, 8, 18), calendar_name="XKRX",
    ) == 1
    assert _age_sessions(
        "2026-08-14", today=date(2026, 8, 18), calendar_name="XNYS",
    ) == 2


@pytest.mark.parametrize(("age", "expected"), [
    (0, ("오늘", "age-neutral")),
    (1, ("1일 전", "age-neutral")),
    (2, ("2일 전", "age-amber")),
    (3, ("3일 전", "age-amber")),
    (4, ("4일 전", "age-red")),
])
def test_age_badge_thresholds(age: int, expected: tuple[str, str]) -> None:
    assert _age_badge(age) == expected


@pytest.mark.parametrize(("metadata", "expected"), [
    ({
        "scheduler_lane": "KR_EQUITY_PROVISIONAL_DAILY",
        "provider_availability_policy": "KRX_POST_CLOSE_2030",
    }, "20:30 수집 예정"),
    ({
        "scheduler_lane": "KR_INDEX_FUNDAMENTAL_DAILY",
        "due_at": "2026-09-04T09:25:00+09:00",
    }, "09:10 수집 예정"),
    ({
        "scheduler_lane": "CANONICAL_EQUITY_DAILY",
        "due_at": "2026-09-04T14:25:00+09:00",
    }, "14:10 수집 예정"),
    ({"scheduler_lane": "GLOBAL_ETF_DAILY"}, "06:10 수집 예정"),
    ({"scheduler_lane": "GLOBAL_INDEX_DAILY"}, "06:20 수집 예정"),
    ({"scheduler_lane": "FRED_DAILY"}, "06:00 수집 예정"),
    ({"pending_until": "14:25"}, "14:10 수집 예정"),
    ({
        "scheduler_lane": "FRED_DAILY",
        "provider_availability_policy": "FRED_H10_WEEKLY_1615_ET",
    }, "매주 월 06:00"),
    ({"scheduler_lane": "NO_SCHEDULER_LANE"}, "수동"),
])
def test_next_collection_hint_uses_lane_and_policy(
    metadata: dict[str, object], expected: str,
) -> None:
    assert _next_collection_hint(metadata) == expected


def test_age_summary_counts_automated_rows_and_groups_oldest_first() -> None:
    tmp_path = new_temp_root()
    root = tmp_path / "artifacts/daily_health"
    root.mkdir(parents=True)
    (root / "universe_data_v2_latest.json").write_text(json.dumps({"datasets": [
        {
            "dataset": "kr_index_daily", "latest": "2026-09-04",
            "expected": "2026-09-04", "freshness": "CURRENT", "calendar": "XKRX",
            "automation_enabled": True, "scheduler_lane": "KR_INDEX_DAILY",
            "due_at": "2026-09-04T20:45:00+09:00",
        },
        {
            "dataset": "global_index_price_daily", "latest": "2026-09-03",
            "expected": "2026-09-03", "freshness": "CURRENT", "calendar": "XNYS",
            "automation_enabled": True, "scheduler_lane": "GLOBAL_INDEX_DAILY",
        },
        {
            "dataset": "fred_usd_fx_daily", "latest": "2026-08-28",
            "expected": "2026-08-28", "freshness": "EXPECTED_LAG", "calendar": "XNYS",
            "automation_enabled": True, "scheduler_lane": "FRED_DAILY",
            "provider_availability_policy": "FRED_H10_WEEKLY_1615_ET",
        },
        {
            "dataset": "kb_domestic_index_snapshot", "latest": "2026-09-01",
            "expected": None, "freshness": "NOT_APPLICABLE",
            "automation_enabled": False, "scheduler_lane": "BROKER_SNAPSHOT",
        },
    ]}), encoding="utf-8")

    context = build_data_page_context(
        tmp_path,
        "ALL",
        now=datetime(2026, 9, 4, 20, 20, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    assert context["age_summary"] == {"today": 1, "yesterday": 1, "older": 1}
    current_rows = next(
        group["rows"] for group in context["health_groups"] if group["raw"] == "CURRENT"
    )
    retained = [
        row["dataset"] for row in current_rows
        if row["dataset"] in {"kr_index_daily", "global_index_price_daily", "fred_usd_fx_daily"}
    ]
    assert retained == ["fred_usd_fx_daily", "global_index_price_daily", "kr_index_daily"]
    manual = next(
        row for group in context["health_groups"] for row in group["rows"]
        if row["dataset"] == "kb_domestic_index_snapshot"
    )
    assert (manual["age_sessions"], manual["age_label"], manual["age_class"]) == (
        3, "3일 전", "age-amber",
    )
    assert manual["next_collection"] == "수동"


def test_old_scheduler_failure_stays_visible_ahead_of_recent_success() -> None:
    tmp_path = new_temp_root()
    root = tmp_path / "artifacts/scheduler_logs"
    root.mkdir(parents=True)
    (root / "OLD_last.json").write_text(json.dumps({
        "task_name": "OLD", "status": "FAIL",
        "finished_at_utc": "2026-08-20T00:00:00+00:00", "result_code": 1,
    }), encoding="utf-8")
    (root / "RECENT_last.json").write_text(json.dumps({
        "task_name": "RECENT", "status": "SUCCESS",
        "finished_at_utc": "2026-09-02T00:00:00+00:00", "result_code": 0,
    }), encoding="utf-8")

    rows = load_scheduler_receipts(
        tmp_path, now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )

    assert [row["task"] for row in rows] == ["OLD", "RECENT"]
    assert rows[0]["result_code_display"]["label"] == "실패 (코드 1)"
    assert rows[1]["result_code_display"]["label"] == "성공"
    assert [row["older_than_7_days"] for row in rows] == [True, False]
    context = build_data_page_context(
        tmp_path, "ALL", now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    assert [row["task"] for row in context["receipts"]] == ["OLD", "RECENT"]
    assert context["older_receipts"] == ()
    assert context["show_result_code"] is True


def test_latest_kr_bundle_terminal_or_stale_claim_is_failed_receipt_and_kpi() -> None:
    tmp_path = new_temp_root()
    occurrence_root = tmp_path / "data/state/provider_scheduler/kr_market_daily_occurrences"
    occurrence_root.mkdir(parents=True)
    fixtures = {
        "20260904T001000Z-0910.json": {
            "scheduled_slot": "09:10", "scheduled_for": "2026-09-04T09:10:00+09:00",
            "claimed_at_utc": "2026-09-04T00:10:01+00:00", "occurrence_status": "TERMINAL_FAILURE",
        },
        "20260905T001000Z-0910.json": {
            "scheduled_slot": "09:10", "scheduled_for": "2026-09-05T09:10:00+09:00",
            "claimed_at_utc": "2026-09-05T00:10:01+00:00", "finished_at_utc": "2026-09-05T00:10:20+00:00",
            "occurrence_status": "TERMINAL_SUCCESS", "terminal_exit_code": 0,
        },
        "20260904T113000Z-2030.json": {
            "scheduled_slot": "20:30", "scheduled_for": "2026-09-04T20:30:00+09:00",
            "claimed_at_utc": "2026-09-04T11:30:02+00:00", "occurrence_status": "TERMINAL_FAILURE",
            "terminal_exit_code": 1, "manual_review": {"note": "bundle receipt missing"},
        },
        "20260905T051000Z-1410.json": {
            "scheduled_slot": "14:10", "scheduled_for": "2026-09-05T14:10:00+09:00",
            "claimed_at_utc": "2026-09-05T05:10:00+00:00", "occurrence_status": "CLAIMED_BEFORE_LANES",
        },
    }
    for name, payload in fixtures.items():
        (occurrence_root / name).write_text(json.dumps(payload), encoding="utf-8")
    health_root = tmp_path / "artifacts/daily_health"
    health_root.mkdir(parents=True)
    (health_root / "universe_data_v2_latest.json").write_text(json.dumps({"datasets": [{
        "dataset": "kr_index_daily", "latest": "2026-09-04", "expected": "2026-09-04",
        "freshness": "CURRENT",
    }]}), encoding="utf-8")

    now = datetime(2026, 9, 5, 7, 0, tzinfo=timezone.utc)
    context = build_data_page_context(tmp_path, "OPERATIONAL", now=now)
    bundle_rows = [row for row in context["receipts"] if row.get("occurrence_source")]

    assert [row["task"] for row in bundle_rows] == [
        "STOCK_DATA_KR_MARKET_DAILY_1410 번들", "STOCK_DATA_KR_MARKET_DAILY_2030 번들",
    ]
    assert bundle_rows[0]["note"] == "번들이 레인 시작 전 점유 상태로 90분 넘게 남아 있습니다."
    assert bundle_rows[1]["note"] == "bundle receipt missing"
    assert all(row["failed"] and row["status"]["label"] == "실패" for row in bundle_rows)
    assert context["health_summary"]["display_failed"] == 2
    assert next(item for item in context["freshness_counts"] if item["raw"] == "FAILED")["count"] == 2
    assert context["selected_filter_label"] == "운영 데이터"
    assert context["kpi_total"] == 94


def test_missing_scheduler_result_code_uses_dash() -> None:
    tmp_path = new_temp_root()
    root = tmp_path / "artifacts/scheduler_logs"
    root.mkdir(parents=True)
    (root / "NO_CODE_last.json").write_text(json.dumps({
        "task_name": "NO_CODE", "status": "SUCCESS",
        "finished_at_utc": "2026-09-02T00:00:00+00:00",
    }), encoding="utf-8")

    row = load_scheduler_receipts(
        tmp_path, now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )[0]

    assert row["result_code_display"]["label"] == "—"
    assert row["has_result_code"] is False


def test_data_template_links_scoped_mobile_css_and_old_receipt_toggle() -> None:
    web = Path(__file__).parents[3] / "src/stock_web"
    template = (web / "templates/data.html").read_text(encoding="utf-8")
    css = (web / "static/data.css").read_text(encoding="utf-8")

    assert 'href="/static/data.css?v={{ static_version }}"' in template
    assert "이전 영수증 보기" in template
    assert "정시 = 정책상 예정된 최신 날짜와 일치" in template
    assert "오늘 {{ age_summary.today }} · 어제 {{ age_summary.yesterday }} · 그 이전 {{ age_summary.older }}" in template
    assert 'id="today-data-filter"' in template
    assert "오늘 데이터만" in template
    assert 'data-age-sessions="{{ row.age_sessions if row.age_sessions is not none else \'\' }}"' in template
    assert 'row.dataset.ageSessions !== "0"' in template
    assert "수동/보존 {{ health_summary.display_preserved }}" in template
    assert "KPI는 전체 {{ kpi_total }}개 기준 · 아래 목록은 필터 적용 ({{ selected_filter_label }})" in template
    assert "{% if show_result_code %}<th>결과 코드</th>{% endif %}" in template
    assert "overflow-x: auto" in css
    assert ".data-page .card-head b { white-space: nowrap; }" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert ".age-badge.age-amber { color: #a8621a; }" in css
    assert ".age-badge.age-red { color: #c0392b; }" in css
