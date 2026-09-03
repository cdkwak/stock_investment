from datetime import date, datetime, timezone
import json
from pathlib import Path

from stock_web.api.data_page import (
    FILTER_LABELS,
    FRESHNESS,
    build_data_page_context,
    load_credential_expiries,
    load_scheduler_receipts,
)


def test_data_page_uses_five_truthful_korean_health_classes() -> None:
    assert FRESHNESS == {
        "CURRENT": ("정상", "current"),
        "LATE": ("지연", "late"),
        "FAILED": ("실패", "failed"),
        "PRESERVED": ("수동/보존", "preserved"),
        "REFERENCE": ("참고", "reference"),
    }
    assert FILTER_LABELS == {
        "OPERATIONAL": "운영 데이터", "DAILY": "일별", "BLOCKED": "차단",
        "ALL": "전체", "UNKNOWN": "미확인",
    }


def test_credential_expiries_read_only_dates_and_flag_soon_or_expired(tmp_path: Path) -> None:
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


def test_credential_expiries_without_env_file(tmp_path: Path) -> None:
    assert load_credential_expiries(tmp_path) == []


def test_data_page_context_survives_unregistered_health_dataset(tmp_path: Path) -> None:
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
    assert next(
        row for group in context["health_groups"] for row in group["rows"]
        if row["dataset"] == "kr_index_daily"
    )["freshness"]["label"] == "정상"


def test_old_scheduler_failure_is_separated_from_recent_receipts(tmp_path: Path) -> None:
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

    assert [row["task"] for row in rows] == ["RECENT", "OLD"]
    assert rows[0]["result_code_display"]["label"] == "성공"
    assert rows[1]["result_code_display"]["label"] == "실패 (코드 1)"
    assert [row["older_than_7_days"] for row in rows] == [False, True]


def test_data_template_links_scoped_mobile_css_and_old_receipt_toggle() -> None:
    web = Path(__file__).parents[3] / "src/stock_web"
    template = (web / "templates/data.html").read_text(encoding="utf-8")
    css = (web / "static/data.css").read_text(encoding="utf-8")

    assert 'href="/static/data.css?v={{ static_version }}"' in template
    assert "이전 영수증 보기" in template
    assert "정상: 최신 ≥ 예상" in template
    assert "수동/보존 {{ health_summary.display_preserved }}" in template
    assert "overflow-x: auto" in css
    assert ".data-page .card-head b { white-space: nowrap; }" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
