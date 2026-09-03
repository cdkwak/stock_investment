from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.orchestration.bok_ecos_fx_daily import (
    BokFxPlanAction,
    plan_daily_operation,
    refresh_range,
    run_daily_lane,
    target_session,
)


KST = ZoneInfo("Asia/Seoul")


def _temp_root() -> Path:
    root = Path(__file__).parents[3] / ".tmp/agents/bok_fx_daily_20260903/fixtures" / uuid4().hex
    root.mkdir(parents=True)
    return root


def _body(*dates: str) -> bytes:
    rows = [{
        "STAT_CODE": "731Y001", "STAT_NAME": "주요국 통화의 대원화환율",
        "ITEM_CODE1": "0000001", "ITEM_NAME1": "원/미국달러(매매기준율)",
        "UNIT_NAME": "원", "TIME": value, "DATA_VALUE": "1337.50",
    } for value in dates]
    return json.dumps({
        "StatisticSearch": {"list_total_count": len(rows), "row": rows},
    }, ensure_ascii=False).encode("utf-8")


class _Session:
    def __init__(self, body: bytes):
        self.body = body
        self.calls = 0

    def get(self, _url: str, *, timeout: int):
        self.calls += 1
        assert timeout == 30
        return type("Response", (), {"status_code": 200, "content": self.body})()


def test_target_uses_1700_kst_and_previous_weekday() -> None:
    assert target_session(datetime(2026, 9, 3, 16, 59, tzinfo=KST)) == date(2026, 9, 2)
    assert target_session(datetime(2026, 9, 3, 17, 0, tzinfo=KST)) == date(2026, 9, 3)
    assert target_session(datetime(2026, 9, 7, 16, 0, tzinfo=KST)) == date(2026, 9, 4)


def test_daily_window_is_oldest_first_and_capped_at_30_sessions() -> None:
    plan = plan_daily_operation(
        retained_latest=date(2026, 7, 1), target=date(2026, 9, 3),
    )
    assert plan.action is BokFxPlanAction.COLLECT
    assert plan.start == date(2026, 7, 2)
    assert len(plan.sessions) == 30
    assert plan.end == plan.sessions[-1]
    assert all(value.weekday() < 5 for value in plan.sessions)


def test_current_plan_and_lane_make_zero_provider_calls() -> None:
    tmp_path = _temp_root()
    session = _Session(_body("20260903"))
    refresh_range(
        tmp_path, start=date(2026, 9, 3), end=date(2026, 9, 3),
        api_key="test-key", session=session,
        retrieved_at=datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc),
    )
    result = run_daily_lane(
        tmp_path, target=date(2026, 9, 3), api_key="test-key", session=session,
    )
    assert result["status"] == "NOOP_IDEMPOTENT"
    assert result["api_calls"] == 0
    assert session.calls == 1


def test_info_200_target_absence_is_expected_provider_lag() -> None:
    tmp_path = _temp_root()
    session = _Session(json.dumps({
        "RESULT": {"CODE": "INFO-200", "MESSAGE": "no data"},
    }).encode("utf-8"))
    result = run_daily_lane(
        tmp_path, target=date(2026, 9, 3), api_key="test-key", session=session,
        retrieved_at=datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc),
    )
    assert result["status"] == "EXPECTED_PROVIDER_LAG"
    assert result["api_calls"] == 1
    assert not (tmp_path / "data/normalized/bok_ecos_usd_krw_daily").exists()


def test_append_only_rerun_is_idempotent() -> None:
    tmp_path = _temp_root()
    first = _Session(_body("20260902", "20260903"))
    result = refresh_range(
        tmp_path, start=date(2026, 9, 2), end=date(2026, 9, 3),
        api_key="test-key", session=first,
        retrieved_at=datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc),
    )
    second = _Session(_body("20260902", "20260903"))
    replay = refresh_range(
        tmp_path, start=date(2026, 9, 2), end=date(2026, 9, 3),
        api_key="test-key", session=second,
        retrieved_at=datetime(2026, 9, 3, 9, 10, tzinfo=timezone.utc),
    )

    assert result["status"] == "PROMOTED" and result["rows_added"] == 2
    assert replay["status"] == "NOOP_IDEMPOTENT" and replay["rows_added"] == 0
    files = list((tmp_path / "data/normalized/bok_ecos_usd_krw_daily").rglob("data.parquet"))
    retained = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    assert len(retained) == 2
