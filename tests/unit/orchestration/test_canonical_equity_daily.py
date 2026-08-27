from datetime import date, datetime, timezone
import json
from pathlib import Path

import pytest

import stock_data.orchestration.canonical_equity_daily as canonical
from stock_data.providers.data_go_kr.client import DataGoKrResult


TARGET = date(2026, 8, 14)
NOW = datetime(2026, 8, 17, 5, 0, tzinfo=timezone.utc)


def _accepted(root: Path, latest: date = date(2026, 8, 13)) -> None:
    path = root / "data/state/canonical_equity_accepted_dates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "accepted_dates": [latest.isoformat()],
        "latest_accepted_date": latest.isoformat(),
    }), encoding="utf-8")


def _result(total: int = 1) -> DataGoKrResult:
    payload = {"response": {"body": {"totalCount": total, "pageNo": 1}}}
    items = ({"basDt": TARGET.strftime("%Y%m%d")},) if total else ()
    return DataGoKrResult(items=items, pages=(payload,), total_count=total)


def test_oldest_missing_session_is_bounded_to_one_xkrx_date(tmp_path: Path) -> None:
    _accepted(tmp_path)
    assert canonical.oldest_missing_session(
        tmp_path, available_through=date(2026, 8, 24),
    ) == TARGET


def test_live_supplier_uses_one_bounded_transport_retry(
    tmp_path: Path, monkeypatch,
) -> None:
    configured: list[dict[str, object]] = []

    class Client:
        def __init__(self, **kwargs):
            configured.append(kwargs)

        def fetch_all(self, **_kwargs):
            return _result()

    monkeypatch.setattr(canonical, "service_key_from_environment", lambda _root: "injected")
    monkeypatch.setattr(canonical, "DataGoKrClient", Client)
    supplier = canonical._live_stream_supplier(tmp_path)

    assert supplier(TARGET, "price_cap").total_count == 1
    assert configured[0]["max_attempts"] == 2
    assert configured[0]["backoff_seconds"] == 1.0


def test_each_successful_stream_is_captured_before_next_call(
    tmp_path: Path,
) -> None:
    _accepted(tmp_path)
    calls: list[str] = []

    def supplier(_target: date, stream: str) -> DataGoKrResult:
        calls.append(stream)
        if stream == "universe":
            raise RuntimeError("injected second-stream failure")
        return _result()

    with pytest.raises(RuntimeError, match="second-stream"):
        canonical.run_canonical_equity_daily(
            tmp_path, available_through=TARGET,
            stream_supplier=supplier, now=NOW,
        )

    captures = list((
        tmp_path / "data/landing/data_go_kr/canonical_equity_daily"
    ).glob("*/price_cap.json"))
    assert calls == ["price_cap", "universe"]
    assert len(captures) == 1
    assert not (captures[0].parent / "universe.json").exists()
    assert json.loads((
        tmp_path / "data/state/canonical_equity_accepted_dates.json"
    ).read_text(encoding="utf-8"))["latest_accepted_date"] == "2026-08-13"


def test_valid_empty_pair_preserves_production_and_reports_two_calls(
    tmp_path: Path,
) -> None:
    _accepted(tmp_path)
    result = canonical.run_canonical_equity_daily(
        tmp_path, available_through=TARGET,
        stream_supplier=lambda _target, _stream: _result(0), now=NOW,
    )
    assert result.status == "DEGRADED_VALID_EMPTY_PRESERVED"
    assert result.api_calls == 2
    assert result.latest_after == date(2026, 8, 13)
    receipts = list((
        tmp_path / "data/landing/data_go_kr/canonical_equity_daily"
    ).glob("*/receipt.json"))
    assert len(receipts) == 1


def test_nonempty_pair_promotes_canonical_then_breadth(
    tmp_path: Path, monkeypatch,
) -> None:
    _accepted(tmp_path)
    order: list[str] = []
    monkeypatch.setattr(canonical, "build_date_frames", lambda *_args, **_kwargs: object())

    def promote(*_args, **_kwargs):
        order.append("canonical")
        _accepted(tmp_path, TARGET)
        return {"status": "CANONICAL_ACCEPTED_DATE"}

    def breadth(*_args, **_kwargs):
        order.append("breadth")
        return {"status": "AFFECTED_BREADTH_COMPLETE"}

    monkeypatch.setattr(canonical, "promote_date_atomic", promote)
    monkeypatch.setattr(canonical, "refresh_breadth_date_atomic", breadth)
    result = canonical.run_canonical_equity_daily(
        tmp_path, available_through=TARGET,
        stream_supplier=lambda _target, _stream: _result(), now=NOW,
    )
    assert result.status == "CANONICAL_ACCEPTED_DATE"
    assert result.api_calls == 2
    assert result.latest_after == TARGET
    assert order == ["canonical", "breadth"]


def test_already_accepted_target_is_api_zero(tmp_path: Path) -> None:
    _accepted(tmp_path, TARGET)
    calls: list[str] = []
    result = canonical.run_canonical_equity_daily(
        tmp_path, available_through=TARGET,
        stream_supplier=lambda *_args: calls.append("called"), now=NOW,
    )
    assert result.status == "NOOP_IDEMPOTENT"
    assert result.api_calls == 0
    assert calls == []

    catchup = canonical.run_canonical_equity_catchup(
        tmp_path, available_through=TARGET,
        stream_supplier=lambda *_args: calls.append("called"), now=NOW,
    )
    assert catchup.status == "NOOP_IDEMPOTENT"
    assert catchup.api_calls == 0
    assert catchup.attempted_dates == ()
    assert catchup.accepted_dates == ()
    assert calls == []


def test_catchup_advances_three_consecutive_sessions_within_budget(
    tmp_path: Path, monkeypatch,
) -> None:
    _accepted(tmp_path)
    available = date(2026, 8, 19)
    promoted: list[date] = []

    def one_day(root: Path, *, available_through: date, **_kwargs):
        selected = canonical.oldest_missing_session(root, available_through=available_through)
        assert selected is not None
        before, _ = canonical._accepted_state(root)
        _accepted(root, selected)
        promoted.append(selected)
        return canonical.CanonicalEquityDailyResult(
            "CANONICAL_ACCEPTED_DATE", available_through, selected, 2,
            f"run-{selected:%Y%m%d}", before, selected,
            "ATOMIC_FOUR_DATASET_AND_BREADTH_PROMOTION",
        )

    monkeypatch.setattr(canonical, "run_canonical_equity_daily", one_day)
    result = canonical.run_canonical_equity_catchup(
        tmp_path, available_through=available,
        max_sessions=3, max_api_calls=6, max_elapsed_seconds=60,
    )

    assert promoted == [date(2026, 8, 14), date(2026, 8, 18), date(2026, 8, 19)]
    assert result.status == "CANONICAL_ACCEPTED_DATE"
    assert result.api_calls == 6
    assert result.latest_before == date(2026, 8, 13)
    assert result.latest_after == available
    assert result.selected_dates == tuple(promoted)
    assert result.attempted_dates == tuple(promoted)
    assert result.accepted_dates == tuple(promoted)


def test_catchup_stops_after_first_degraded_session(tmp_path: Path, monkeypatch) -> None:
    _accepted(tmp_path)
    calls = 0

    def degraded(_root: Path, *, available_through: date, **_kwargs):
        nonlocal calls
        calls += 1
        return canonical.CanonicalEquityDailyResult(
            "DEGRADED_VALID_EMPTY_PRESERVED", available_through, TARGET, 2,
            "run-empty", date(2026, 8, 13), date(2026, 8, 13),
            "BOTH_EXACT_DATE_STREAMS_MUST_BE_NON_EMPTY",
        )

    monkeypatch.setattr(canonical, "run_canonical_equity_daily", degraded)
    result = canonical.run_canonical_equity_catchup(
        tmp_path, available_through=date(2026, 8, 19),
    )

    assert calls == 1
    assert result.status == "DEGRADED_VALID_EMPTY_PRESERVED"
    assert result.latest_after == date(2026, 8, 13)
    assert result.attempted_dates == (TARGET,)
    assert result.accepted_dates == ()


def test_catchup_stops_before_call_budget_without_skipping(
    tmp_path: Path, monkeypatch,
) -> None:
    _accepted(tmp_path)

    def one_day(root: Path, *, available_through: date, **_kwargs):
        selected = canonical.oldest_missing_session(root, available_through=available_through)
        assert selected is not None
        before, _ = canonical._accepted_state(root)
        _accepted(root, selected)
        # Exercise the metered supplier that owns the aggregate call budget.
        _kwargs["stream_supplier"](selected, "price_cap")
        _kwargs["stream_supplier"](selected, "universe")
        return canonical.CanonicalEquityDailyResult(
            "CANONICAL_ACCEPTED_DATE", available_through, selected, 2,
            f"run-{selected:%Y%m%d}", before, selected, "PROMOTED",
        )

    monkeypatch.setattr(canonical, "run_canonical_equity_daily", one_day)
    result = canonical.run_canonical_equity_catchup(
        tmp_path, available_through=date(2026, 8, 24),
        max_sessions=3, max_api_calls=4, max_elapsed_seconds=60,
        stream_supplier=lambda *_args: _result(),
    )

    assert result.status == "CANONICAL_BOUNDED_CATCHUP"
    assert result.api_calls == 4
    assert result.attempted_dates == (date(2026, 8, 14), date(2026, 8, 18))
    assert result.accepted_dates == result.attempted_dates


def test_catchup_stops_before_second_date_when_time_budget_is_exhausted(
    tmp_path: Path, monkeypatch,
) -> None:
    _accepted(tmp_path)
    ticks = iter((0.0, 0.0, 61.0))

    def one_day(root: Path, *, available_through: date, **_kwargs):
        selected = canonical.oldest_missing_session(root, available_through=available_through)
        assert selected is not None
        before, _ = canonical._accepted_state(root)
        _accepted(root, selected)
        _kwargs["stream_supplier"](selected, "price_cap")
        _kwargs["stream_supplier"](selected, "universe")
        return canonical.CanonicalEquityDailyResult(
            "CANONICAL_ACCEPTED_DATE", available_through, selected, 2,
            "run-one", before, selected, "PROMOTED",
        )

    monkeypatch.setattr(canonical, "run_canonical_equity_daily", one_day)
    result = canonical.run_canonical_equity_catchup(
        tmp_path, available_through=date(2026, 8, 24),
        max_elapsed_seconds=60, monotonic_fn=lambda: next(ticks),
        stream_supplier=lambda *_args: _result(),
    )

    assert result.reason == "BOUNDED_CATCHUP_BUDGET_EXHAUSTED"
    assert result.attempted_dates == (TARGET,)
    assert result.accepted_dates == (TARGET,)


def test_catchup_exhausted_before_first_date_is_not_reported_as_current(
    tmp_path: Path, monkeypatch,
) -> None:
    _accepted(tmp_path)
    ticks = iter((0.0, 601.0))
    called = False

    def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider phase must not start after the time budget")

    monkeypatch.setattr(canonical, "run_canonical_equity_daily", should_not_run)
    result = canonical.run_canonical_equity_catchup(
        tmp_path, available_through=date(2026, 8, 24),
        max_elapsed_seconds=600, monotonic_fn=lambda: next(ticks),
    )

    assert result.status == "CANONICAL_BOUNDED_CATCHUP"
    assert result.reason == "BOUNDED_CATCHUP_BUDGET_EXHAUSTED"
    assert result.api_calls == 0
    assert result.attempted_dates == ()
    assert result.accepted_dates == ()
    assert result.latest_after == date(2026, 8, 13)
    assert called is False


def test_second_date_failure_reports_order_and_preserves_first_acceptance(
    tmp_path: Path, monkeypatch,
) -> None:
    _accepted(tmp_path)
    calls = 0

    def one_day(root: Path, *, available_through: date, **kwargs):
        nonlocal calls
        selected = canonical.oldest_missing_session(root, available_through=available_through)
        assert selected is not None
        kwargs["stream_supplier"](selected, "price_cap")
        calls += 1
        if calls == 2:
            raise canonical.CanonicalEquityDailyError("injected validation failure")
        kwargs["stream_supplier"](selected, "universe")
        before, _ = canonical._accepted_state(root)
        _accepted(root, selected)
        return canonical.CanonicalEquityDailyResult(
            "CANONICAL_ACCEPTED_DATE", available_through, selected, 2,
            "run-first", before, selected, "PROMOTED",
        )

    monkeypatch.setattr(canonical, "run_canonical_equity_daily", one_day)
    result = canonical.run_canonical_equity_catchup(
        tmp_path, available_through=date(2026, 8, 19),
        stream_supplier=lambda *_args: _result(),
    )

    assert result.status == "FAILED_PRESERVED"
    assert result.api_calls == 3
    assert result.attempted_dates == (date(2026, 8, 14), date(2026, 8, 18))
    assert result.accepted_dates == (date(2026, 8, 14),)
    assert result.latest_after == date(2026, 8, 14)


def test_accepted_date_with_failed_breadth_is_reported_from_retained_state(
    tmp_path: Path, monkeypatch,
) -> None:
    _accepted(tmp_path)

    def canonical_accepted_then_breadth_fails(
        root: Path, *, available_through: date, **kwargs,
    ):
        selected = canonical.oldest_missing_session(root, available_through=available_through)
        assert selected is not None
        kwargs["stream_supplier"](selected, "price_cap")
        kwargs["stream_supplier"](selected, "universe")
        _accepted(root, selected)
        breadth_state = root / "data/state/canonical_equity_breadth_status.json"
        breadth_state.write_text(json.dumps({
            "completed_dates": [], "latest_completed_date": None,
            "pending_date": selected.isoformat(), "status": "PENDING",
        }), encoding="utf-8")
        raise canonical.CanonicalEquityDailyError("injected breadth failure")

    monkeypatch.setattr(
        canonical, "run_canonical_equity_daily",
        canonical_accepted_then_breadth_fails,
    )
    result = canonical.run_canonical_equity_catchup(
        tmp_path, available_through=date(2026, 8, 19),
        stream_supplier=lambda *_args: _result(),
    )

    assert result.status == "FAILED_PRESERVED"
    assert result.reason == "CANONICAL_ACCEPTED_BREADTH_PENDING"
    assert result.api_calls == 2
    assert result.attempted_dates == (TARGET,)
    assert result.accepted_dates == (TARGET,)
    assert result.latest_after == TARGET
