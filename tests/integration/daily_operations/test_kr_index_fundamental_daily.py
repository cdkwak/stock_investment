from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stock_data.contracts.kr_index_fundamental_daily import KR_INDEX_FUNDAMENTAL_DAILY
from stock_data.orchestration.kr_index_fundamental_daily import (
    DEFAULT_RETAINED_ROOT,
    IndexFundamentalDailyError,
    run_index_fundamental_daily,
)
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.kr_index_fundamental_daily import validate_kr_index_fundamental_daily


def _source_row(day: str, close: str = "3,200.0") -> dict[str, str]:
    return {
        "TRD_DD": day, "CLSPRC_IDX": close, "WT_PER": "15.0",
        "WT_STKPRC_NETASST_RTO": "1.2", "DIV_YD": "1.0",
    }


def _baseline(root: Path) -> None:
    retained = root / DEFAULT_RETAINED_ROOT
    retained.mkdir(parents=True)
    completed = {}
    for sequence, identity in enumerate(("kospi", "kosdaq"), 1):
        name = f"index_{identity}_history_01"
        body_file = f"response_{sequence:02d}_{name}.json"
        body = json.dumps({"output": [_source_row("2026/08/12")]}).encode()
        (retained / body_file).write_bytes(body)
        completed[name] = {
            "classification": "SUCCESS", "rows": 1,
            "body_file": body_file, "body_sha256": hashlib.sha256(body).hexdigest(),
        }
    (retained / "checkpoint.json").write_text(json.dumps({
        "status": "COMPLETE", "run_id": "baseline", "completed": completed,
    }), encoding="utf-8")


def _fetcher(calls, *, omit: str | None = None):
    def fetch(index_code, start, end):
        calls.append((index_code, start, end))
        days = ["2026/08/13", "2026/08/14"]
        if omit in days:
            days.remove(omit)
        return json.dumps({"output": [_source_row(day) for day in days]}).encode()
    return fetch


def test_two_market_range_promotes_jointly_and_replay_is_api_zero(tmp_path):
    _baseline(tmp_path)
    calls = []
    now = datetime(2026, 8, 15, 9, 10, tzinfo=ZoneInfo("Asia/Seoul"))
    result = run_index_fundamental_daily(
        tmp_path, target_date=date(2026, 8, 14), now=now,
        body_fetcher=_fetcher(calls),
    )
    assert result.status == "PROMOTED"
    assert result.api_calls == 2 and result.inserted_rows == 4
    assert len(calls) == 2
    production = read_dataset(
        tmp_path / "data/normalized/kr_index_fundamental_daily",
        KR_INDEX_FUNDAMENTAL_DAILY, validate_kr_index_fundamental_daily,
    )
    assert len(production) == 6
    assert set(production.groupby("market")["date"].max()) == {"2026-08-14"}
    state = json.loads((tmp_path / "data/state/kr_index_fundamental_daily.json").read_text())
    assert state["expected_sessions"] == ["2026-08-13", "2026-08-14"]
    assert state["predictive_eligibility"] == "NON_PREDICTIVE"

    replay = run_index_fundamental_daily(
        tmp_path, target_date=date(2026, 8, 14), now=now,
        body_fetcher=lambda *_args: pytest.fail("replay called provider"),
    )
    assert replay.status == "NOOP_IDEMPOTENT" and replay.api_calls == 0


def test_second_incremental_promotion_excludes_rollback_backup_from_readback(
    tmp_path,
):
    _baseline(tmp_path)
    first_calls = []
    run_index_fundamental_daily(
        tmp_path, target_date=date(2026, 8, 14),
        now=datetime(2026, 8, 15, 9, 10, tzinfo=ZoneInfo("Asia/Seoul")),
        body_fetcher=_fetcher(first_calls),
    )

    second_calls = []

    def fetch_next(index_code, start, end):
        second_calls.append((index_code, start, end))
        return json.dumps({"output": [_source_row("2026/08/18")]}).encode()

    result = run_index_fundamental_daily(
        tmp_path, target_date=date(2026, 8, 18),
        now=datetime(2026, 8, 19, 9, 10, tzinfo=ZoneInfo("Asia/Seoul")),
        body_fetcher=fetch_next,
    )

    assert result.status == "PROMOTED"
    assert result.inserted_rows == 2
    assert [item[0] for item in second_calls] == ["1001", "2001"]
    production = read_dataset(
        tmp_path / "data/normalized/kr_index_fundamental_daily",
        KR_INDEX_FUNDAMENTAL_DAILY, validate_kr_index_fundamental_daily,
    )
    assert len(production) == 8
    assert set(production.groupby("market")["date"].max()) == {"2026-08-18"}
    assert not (
        tmp_path / "data/normalized/.kr_index_fundamental_daily.transactions"
    ).exists()


def test_reviewed_20260825_range_requires_exact_eight_xkrx_sessions(tmp_path):
    _baseline(tmp_path)
    expected = [
        "2026-08-13", "2026-08-14", "2026-08-18", "2026-08-19",
        "2026-08-20", "2026-08-21", "2026-08-24", "2026-08-25",
    ]
    calls = []

    def fetch(index_code, start, end):
        calls.append((index_code, start.isoformat(), end.isoformat()))
        return json.dumps({
            "output": [_source_row(day.replace("-", "/")) for day in expected]
        }).encode()

    result = run_index_fundamental_daily(
        tmp_path, target_date=date(2026, 8, 25),
        now=datetime(2026, 8, 26, 9, 10, tzinfo=ZoneInfo("Asia/Seoul")),
        body_fetcher=fetch,
    )

    assert result.api_calls == 2 and result.inserted_rows == 16
    assert calls == [
        ("1001", "2026-08-13", "2026-08-25"),
        ("2001", "2026-08-13", "2026-08-25"),
    ]
    state = json.loads((tmp_path / "data/state/kr_index_fundamental_daily.json").read_text())
    assert state["expected_sessions"] == expected


def test_exact_session_mismatch_preserves_production_and_state(tmp_path):
    _baseline(tmp_path)
    with pytest.raises(IndexFundamentalDailyError, match="sessions differ"):
        run_index_fundamental_daily(
            tmp_path, target_date=date(2026, 8, 14),
            now=datetime(2026, 8, 15, tzinfo=ZoneInfo("Asia/Seoul")),
            body_fetcher=_fetcher([], omit="2026/08/13"),
        )
    assert not (tmp_path / "data/state/kr_index_fundamental_daily.json").exists()
    assert not list((tmp_path / "data/normalized/kr_index_fundamental_daily").glob("market=*"))
    assert len(list((tmp_path / "data/landing/kr_index_fundamental_daily").rglob("*.json"))) == 2


def test_state_failure_rolls_back_both_market_partitions(tmp_path):
    _baseline(tmp_path)

    def fail_state(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        raise OSError("injected state failure")

    with pytest.raises(OSError, match="injected"):
        run_index_fundamental_daily(
            tmp_path, target_date=date(2026, 8, 14),
            now=datetime(2026, 8, 15, tzinfo=ZoneInfo("Asia/Seoul")),
            body_fetcher=_fetcher([]), state_writer=fail_state,
        )
    assert not (tmp_path / "data/state/kr_index_fundamental_daily.json").exists()
    assert not list((tmp_path / "data/normalized/kr_index_fundamental_daily").glob("market=*"))


def test_current_day_target_is_rejected_before_provider_access(tmp_path):
    _baseline(tmp_path)
    with pytest.raises(IndexFundamentalDailyError, match="prior completed"):
        run_index_fundamental_daily(
            tmp_path, target_date=date(2026, 8, 26),
            now=datetime(2026, 8, 26, 20, 30, tzinfo=ZoneInfo("Asia/Seoul")),
            body_fetcher=lambda *_args: pytest.fail("current day reached provider"),
        )
    assert not (tmp_path / "data/landing/kr_index_fundamental_daily").exists()
