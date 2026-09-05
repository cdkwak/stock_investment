from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pandas as pd

import stock_data.orchestration.market_daily_incremental as subject
from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket, ExchangeTradingCalendar,
)


def _write_state(root, market_date: date, **values) -> None:
    path = root / "data/state/finality/kr_market_liquidity_daily.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    day = {
        "market_date": market_date.isoformat(),
        "status": "STABLE",
        "stable_response_status": "VALID_EMPTY",
        "availability_status": "publisher_not_yet_available",
        "observations": [{"response_status": "VALID_EMPTY"}] * 2,
        **values,
    }
    path.write_text(json.dumps({
        "dataset": "kr_market_liquidity_daily", "dates": {
            market_date.strftime("%Y%m%d"): day,
        },
    }), encoding="utf-8")


def test_liquidity_gap_plan_uses_oldest_xkrx_sessions_and_caps_at_twenty(
    tmp_path, monkeypatch,
) -> None:
    retained = date(2026, 7, 31)
    target = date(2026, 9, 4)
    normalized = tmp_path / "data/normalized/kr_market_liquidity_daily"
    normalized.mkdir(parents=True)
    frame = pd.DataFrame({"date": [retained]})
    monkeypatch.setattr(subject, "read_dataset", lambda *_args: frame)
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)

    planned = subject.plan_liquidity_gap_dates(
        project_root=tmp_path, target_date=target, calendar=calendar,
    )
    first = calendar.next_trading_day(retained)
    expected = calendar.sessions_in_range(first, target)

    assert len(planned) == subject.MAX_GAP_CALLS == 20
    assert planned == expected[:20]
    assert planned == tuple(sorted(planned))


def test_liquidity_gap_plan_keeps_an_earlier_provider_empty_hole(
    tmp_path, monkeypatch,
) -> None:
    pending = date(2026, 8, 10)
    retained = date(2026, 8, 20)
    normalized = tmp_path / "data/normalized/kr_market_liquidity_daily"
    normalized.mkdir(parents=True)
    monkeypatch.setattr(
        subject, "read_dataset",
        lambda *_args: pd.DataFrame({"date": [retained]}),
    )
    _write_state(tmp_path, pending)

    planned = subject.plan_liquidity_gap_dates(
        project_root=tmp_path, target_date=date(2026, 8, 21),
    )

    assert planned[0] == pending


def test_liquidity_valid_empty_retries_through_day_45_then_confirms_gap(
    tmp_path,
) -> None:
    market_date = date(2026, 7, 20)
    _write_state(tmp_path, market_date)
    base = {
        "project_root": tmp_path,
        "dataset": "market_liquidity",
        "market_date": market_date,
        "accepted_market_dates": (market_date,),
        "operation_reviewed": True,
        "max_api_calls": 1,
    }
    day_45 = subject.plan_liquidity_credit_two_pass(
        **base, latest_finalized_market_date=market_date + timedelta(days=45),
    )
    day_46 = subject.plan_liquidity_credit_two_pass(
        **base, latest_finalized_market_date=market_date + timedelta(days=46),
    )

    assert day_45.action == "CAPTURE_RECHECK_EMPTY"
    assert day_46.action == "CONFIRM_PUBLISHER_GAP"
    result = subject.execute_liquidity_credit_two_pass(
        day_46, project_root=tmp_path,
        date_runner=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("confirmed gap must not call the provider")
        ),
    )
    state = json.loads((
        tmp_path / "data/state/finality/kr_market_liquidity_daily.json"
    ).read_text(encoding="utf-8"))

    assert result.pages == 0
    assert result.availability_status == "publisher_gap_confirmed"
    assert state["dates"]["20260720"]["availability_status"] == (
        "publisher_gap_confirmed"
    )
    assert subject.plan_liquidity_credit_two_pass(
        **base, latest_finalized_market_date=market_date + timedelta(days=47),
    ).action == "NOOP_STABLE"


def test_liquidity_valid_empty_result_is_typed_as_not_yet_available(
    tmp_path,
) -> None:
    market_date = date(2026, 8, 20)

    def empty_runner(**kwargs):
        kwargs["landing_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["landing_path"].write_text("[]", encoding="utf-8")
        kwargs["state_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["state_path"].write_text(json.dumps({
            "dataset": "kr_market_liquidity_daily",
            "completed_partitions": [],
            "valid_empty_partitions": [market_date.strftime("%Y%m%d")],
        }), encoding="utf-8")
        return SimpleNamespace(status="VALID_EMPTY", pages=1)

    plan_values = {
        "project_root": tmp_path,
        "dataset": "market_liquidity",
        "market_date": market_date,
        "latest_finalized_market_date": market_date,
        "accepted_market_dates": (market_date,),
        "operation_reviewed": True,
        "max_api_calls": 1,
    }
    first = subject.execute_liquidity_credit_two_pass(
        subject.plan_liquidity_credit_two_pass(**plan_values),
        project_root=tmp_path, date_runner=empty_runner,
        observed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    second = subject.execute_liquidity_credit_two_pass(
        subject.plan_liquidity_credit_two_pass(**plan_values),
        project_root=tmp_path, date_runner=empty_runner,
        observed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )

    assert first.availability_status == "publisher_not_yet_available"
    assert second.status == "STABLE"
    assert second.availability_status == "publisher_not_yet_available"
    assert subject.plan_liquidity_credit_two_pass(**plan_values).action == (
        "CAPTURE_RECHECK_EMPTY"
    )
