from datetime import date, datetime, timedelta, timezone
import json

import pandas as pd
import pytest

from stock_data.contracts.market_15m import (
    MARKET_15M_LANE_SERIES,
    MARKET_15M_SERIES_POLICIES,
    MARKET_PRICE_15M_OBSERVATION,
)
from stock_data.orchestration.global_market_15m import (
    GlobalMarket15mError,
    LANE_IDS,
    reviewed_native_scope,
    resolve_native_scope,
    run_global_market_15m,
)
from stock_data.orchestration.recovery_supervisor import OperationScopeLock
from stock_data.orchestration.update_event_log import (
    EventState,
    EventWriteResult,
    LocalUpdateEventLog,
    TriggerType,
)
from stock_data.storage.market_15m import read_market_15m
from stock_data.validation.market_15m import YAHOO_15M_IDENTITIES


def _frame(series_id: str, start: datetime) -> pd.DataFrame:
    market, instrument, session = YAHOO_15M_IDENTITIES[series_id]
    policy = MARKET_15M_SERIES_POLICIES[series_id]
    stamp = pd.Timestamp(start).tz_convert("UTC")
    row = {
        "market_date": stamp.tz_convert(policy.source_timezone).date(),
        "market": market,
        "series_id": series_id,
        "provider_symbol": series_id,
        "instrument_type": instrument,
        "bar_start": stamp,
        "bar_end": stamp + timedelta(minutes=15),
        "source_timezone": policy.source_timezone,
        "display_timezone": "Asia/Seoul",
        "session": session,
        "interval": "15m",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1_000,
        "provider": "yahoo_chart_api",
        "data_availability": "INDICATIVE_DELAYED_NOT_LICENSED_REALTIME",
        "retrieved_at": stamp + timedelta(hours=1),
    }
    return pd.DataFrame([row], columns=MARKET_PRICE_15M_OBSERVATION.column_names)


def _expected(lane_id: str, start: datetime):
    return {
        series_id: (start,) for series_id in MARKET_15M_LANE_SERIES[lane_id]
    }


def _tree_bytes(root) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("lane_id", LANE_IDS)
def test_native_lane_promotes_then_replays_api_zero(tmp_path, lane_id) -> None:
    calls = []
    minute = 35 if lane_id == "YAHOO_TREASURY_QUOTE" else 30
    start = datetime(2026, 8, 19, 13, minute, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)

    def fetcher(series_id, **_kwargs):
        calls.append(series_id)
        return _frame(series_id, start)

    first = run_global_market_15m(
        tmp_path,
        lane_id=lane_id,
        window_start=start,
        window_end=end,
        expected_bar_starts=_expected(lane_id, start),
        as_of=start + timedelta(hours=1),
        fetcher=fetcher,
    )
    second = run_global_market_15m(
        tmp_path,
        lane_id=lane_id,
        window_start=start,
        window_end=end,
        expected_bar_starts=_expected(lane_id, start),
        as_of=start + timedelta(hours=2),
        fetcher=fetcher,
    )
    retained = read_market_15m(
        tmp_path / "data/normalized/market_price_15m_observation"
    )
    assert first["status"] == "PASS"
    assert first["api_calls"] == len(MARKET_15M_LANE_SERIES[lane_id])
    assert second["status"] == "NOOP_ALREADY_ACCEPTED"
    assert second["api_calls"] == 0
    log = json.loads(
        (
            tmp_path
            / "artifacts/scheduler_logs"
            / f"STOCK_DATA_GLOBAL_MARKET_15M_{lane_id}_last.json"
        ).read_text(encoding="utf-8")
    )
    assert log["status"] == "NOOP_ALREADY_ACCEPTED"
    assert log["api_calls"] == 0
    assert calls == list(MARKET_15M_LANE_SERIES[lane_id])
    assert set(retained["series_id"]) == set(MARKET_15M_LANE_SERIES[lane_id])


def test_cboe_vix_api_zero_replay_emits_recovery_events_without_changing_data_state(
    tmp_path,
) -> None:
    lane_id = "CBOE_VIX"
    start = datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    calls = []

    def fetcher(series_id, **_kwargs):
        calls.append(series_id)
        return _frame(series_id, start)

    seeded = run_global_market_15m(
        tmp_path,
        lane_id=lane_id,
        window_start=start,
        window_end=end,
        expected_bar_starts=_expected(lane_id, start),
        as_of=start + timedelta(hours=1),
        fetcher=fetcher,
    )
    production = tmp_path / "data/normalized/market_price_15m_observation"
    state = tmp_path / "data/state/global_market_15m/cboe_vix.json"
    accepted_tree = _tree_bytes(production)
    accepted_state = state.read_bytes()
    event_log = LocalUpdateEventLog(tmp_path / "artifacts/runtime_logs/data_updates")

    first_replay = run_global_market_15m(
        tmp_path,
        lane_id=lane_id,
        window_start=start,
        window_end=end,
        expected_bar_starts=_expected(lane_id, start),
        as_of=start + timedelta(hours=2),
        fetcher=fetcher,
        event_log=event_log,
    )
    duplicate_replay = run_global_market_15m(
        tmp_path,
        lane_id=lane_id,
        window_start=start,
        window_end=end,
        expected_bar_starts=_expected(lane_id, start),
        as_of=start + timedelta(hours=2),
        fetcher=fetcher,
        event_log=event_log,
    )

    assert seeded["status"] == "PASS"
    assert first_replay["status"] == duplicate_replay["status"] == "NOOP_ALREADY_ACCEPTED"
    assert first_replay["api_calls"] == duplicate_replay["api_calls"] == 0
    assert first_replay["event_log_status"] == duplicate_replay["event_log_status"] == "PASS"
    assert calls == ["^VIX"]
    assert _tree_bytes(production) == accepted_tree
    assert state.read_bytes() == accepted_state

    events = event_log.read_events()
    assert sum(event.state is EventState.STARTED for event in events) == 2
    assert sum(event.state is EventState.API_ZERO_NOOP for event in events) == 2
    assert len({event.run_id for event in events}) == 2
    grouped = {
        run_id: tuple(event for event in events if event.run_id == run_id)
        for run_id in {event.run_id for event in events}
    }
    for started, terminal in grouped.values():
        if started.state is not EventState.STARTED:
            started, terminal = terminal, started
        assert terminal.run_id == started.run_id
        assert terminal.trigger_type is TriggerType.API_ZERO_REPLAY
        assert terminal.provider_call_count == 0
        assert terminal.requested_scope["recovery_classification"] == "RETAINED_SUCCESS"
        assert terminal.requested_scope["recovery_action"] == "API_ZERO_REPLAY"
        assert terminal.requested_scope["missing_scopes"] == []


def test_cboe_vix_duplicate_active_lock_and_logging_failure_preserve_api_zero_outcome(
    tmp_path,
) -> None:
    lane_id = "CBOE_VIX"
    start = datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    calls = []

    def fetcher(series_id, **_kwargs):
        calls.append(series_id)
        return _frame(series_id, start)

    run_global_market_15m(
        tmp_path,
        lane_id=lane_id,
        window_start=start,
        window_end=end,
        expected_bar_starts=_expected(lane_id, start),
        as_of=start + timedelta(hours=1),
        fetcher=fetcher,
    )
    production = tmp_path / "data/normalized/market_price_15m_observation"
    state = tmp_path / "data/state/global_market_15m/cboe_vix.json"
    accepted_tree = _tree_bytes(production)
    accepted_state = state.read_bytes()
    event_log = LocalUpdateEventLog(tmp_path / "events")
    lock_root = tmp_path / "artifacts/runtime_locks/data_updates"

    with OperationScopeLock(
        lock_root,
        operation="global-market-15m-cboe-vix",
        datasets=("^VIX",),
        run_id="existing-replay",
    ):
        duplicate = run_global_market_15m(
            tmp_path,
            lane_id=lane_id,
            window_start=start,
            window_end=end,
            expected_bar_starts=_expected(lane_id, start),
            as_of=start + timedelta(hours=2),
            fetcher=fetcher,
            event_log=event_log,
        )
    assert duplicate["status"] == "NOOP_ALREADY_ACCEPTED"
    assert duplicate["api_calls"] == 0
    assert duplicate["event_log_status"] == "SKIPPED_ACTIVE_WRITER"
    assert event_log.read_events() == ()

    class FailingEventLog:
        def append(self, _event):
            return EventWriteResult(
                persisted=False,
                error_code="LOCAL_LOG_WRITE_FAILED",
                safe_message="injected",
            )

    failed_log = run_global_market_15m(
        tmp_path,
        lane_id=lane_id,
        window_start=start,
        window_end=end,
        expected_bar_starts=_expected(lane_id, start),
        as_of=start + timedelta(hours=2),
        fetcher=fetcher,
        event_log=FailingEventLog(),  # type: ignore[arg-type]
    )
    assert failed_log["status"] == "NOOP_ALREADY_ACCEPTED"
    assert failed_log["api_calls"] == 0
    assert failed_log["event_log_status"] == "FAILED"
    assert calls == ["^VIX"]
    assert _tree_bytes(production) == accepted_tree
    assert state.read_bytes() == accepted_state


def test_failed_lane_preserves_previously_promoted_independent_lane(tmp_path) -> None:
    start = datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    run_global_market_15m(
        tmp_path,
        lane_id="XNYS_MARKET_INDEX",
        window_start=start,
        window_end=end,
        expected_bar_starts=_expected("XNYS_MARKET_INDEX", start),
        as_of=start + timedelta(hours=1),
        fetcher=lambda series_id, **_: _frame(series_id, start),
    )
    before = read_market_15m(
        tmp_path / "data/normalized/market_price_15m_observation"
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        run_global_market_15m(
            tmp_path,
            lane_id="CBOE_VIX",
            window_start=start,
            window_end=end,
            expected_bar_starts=_expected("CBOE_VIX", start),
            as_of=start + timedelta(hours=1),
            fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("provider unavailable")
            ),
        )
    after = read_market_15m(
        tmp_path / "data/normalized/market_price_15m_observation"
    )
    pd.testing.assert_frame_equal(before, after)
    assert (
        tmp_path / "data/state/global_market_15m/xnys_market_index.json"
    ).exists()
    assert not (tmp_path / "data/state/global_market_15m/cboe_vix.json").exists()


def test_native_lane_requires_exact_identities_and_provider_timezone(tmp_path) -> None:
    start = datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    incomplete = {"NQ=F": (start,)}
    with pytest.raises(ValueError, match="exact lane"):
        run_global_market_15m(
            tmp_path,
            lane_id="XNYS_MARKET_INDEX",
            window_start=start,
            window_end=end,
            expected_bar_starts=incomplete,
            as_of=start + timedelta(hours=1),
        )

    def wrong_timezone(series_id, **_kwargs):
        frame = _frame(series_id, start)
        frame["source_timezone"] = "America/New_York"
        return frame

    with pytest.raises(GlobalMarket15mError, match="provider timezone differs"):
        run_global_market_15m(
            tmp_path,
            lane_id="CBOE_VIX",
            window_start=start,
            window_end=end,
            expected_bar_starts=_expected("CBOE_VIX", start),
            as_of=start + timedelta(hours=1),
            fetcher=wrong_timezone,
        )
    assert not (tmp_path / "data/normalized/market_price_15m_observation").exists()


@pytest.mark.parametrize("lane_id", LANE_IDS)
def test_native_lane_market_closed_scope_is_pre_network_noop(tmp_path, lane_id) -> None:
    start = datetime(2026, 8, 23, 13, 30, tzinfo=timezone.utc)
    calls = []
    report = run_global_market_15m(
        tmp_path,
        lane_id=lane_id,
        window_start=start,
        window_end=start + timedelta(minutes=15),
        expected_bar_starts={
            series_id: () for series_id in MARKET_15M_LANE_SERIES[lane_id]
        },
        as_of=start + timedelta(hours=1),
        fetcher=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    assert report["status"] == "NOOP_MARKET_CLOSED"
    assert report["api_calls"] == 0 and calls == []


def test_reviewed_native_scopes_match_retained_provider_boundaries() -> None:
    clock = datetime(2026, 8, 20, 2, 0, tzinfo=timezone.utc)
    index_scope = reviewed_native_scope(clock, "XNYS_MARKET_INDEX")
    vix_scope = reviewed_native_scope(clock, "CBOE_VIX")
    treasury_scope = reviewed_native_scope(clock, "YAHOO_TREASURY_QUOTE")

    assert index_scope.session_date.isoformat() == "2026-08-19"
    assert index_scope.window_start == datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)
    assert index_scope.window_end == datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
    assert set(index_scope.expected_bar_starts) == {"NQ=F", "^IXIC", "^GSPC"}
    assert all(len(starts) == 26 for starts in index_scope.expected_bar_starts.values())

    assert vix_scope.window_start == index_scope.window_start
    assert vix_scope.window_end == index_scope.window_end
    assert len(vix_scope.expected_bar_starts["^VIX"]) == 26
    assert MARKET_15M_SERIES_POLICIES["^VIX"].source_timezone == "America/Chicago"
    assert "BOUNDARY_OBSERVATION_EXCLUDED" in (
        MARKET_15M_SERIES_POLICIES["^VIX"].session_boundary_policy
    )

    assert treasury_scope.window_start.astimezone(timezone.utc) == datetime(
        2026, 8, 19, 13, 20, tzinfo=timezone.utc
    )
    assert treasury_scope.window_end.astimezone(timezone.utc) == datetime(
        2026, 8, 19, 19, 5, tzinfo=timezone.utc
    )
    assert all(
        len(starts) == 23 for starts in treasury_scope.expected_bar_starts.values()
    )
    assert all(
        MARKET_15M_SERIES_POLICIES[series_id].source_timezone == "America/Chicago"
        for series_id in ("^FVX", "^TNX", "^TYX")
    )


def test_treasury_native_lane_rejects_unreviewed_early_close_grid() -> None:
    with pytest.raises(GlobalMarket15mError, match="early-close"):
        reviewed_native_scope(
            datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc),
            "YAHOO_TREASURY_QUOTE",
        )


def test_reviewed_native_scope_fails_before_network_for_wrong_exact_date() -> None:
    with pytest.raises(GlobalMarket15mError, match="required exact date"):
        reviewed_native_scope(
            datetime(2026, 8, 19, 20, 20, tzinfo=timezone.utc),
            "XNYS_MARKET_INDEX",
            required_session_date=date(2026, 8, 19),
        )

    scope = reviewed_native_scope(
        datetime(2026, 8, 19, 20, 31, tzinfo=timezone.utc),
        "XNYS_MARKET_INDEX",
        required_session_date=date(2026, 8, 19),
    )
    assert scope.session_date == date(2026, 8, 19)


def test_scope_modes_are_mutually_exclusive_and_scheduled_is_post_close() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        resolve_native_scope(
            datetime(2026, 8, 19, 20, 31, tzinfo=timezone.utc),
            "CBOE_VIX",
        )
    with pytest.raises(ValueError, match="exactly one"):
        resolve_native_scope(
            datetime(2026, 8, 19, 20, 31, tzinfo=timezone.utc),
            "CBOE_VIX",
            required_session_date=date(2026, 8, 19),
            scheduled=True,
        )
    with pytest.raises(GlobalMarket15mError, match="post-close"):
        resolve_native_scope(
            datetime(2026, 8, 19, 20, 20, tzinfo=timezone.utc),
            "CBOE_VIX",
            scheduled=True,
        )

    scope = resolve_native_scope(
        datetime(2026, 8, 19, 20, 31, tzinfo=timezone.utc),
        "CBOE_VIX",
        scheduled=True,
    )
    assert scope.session_date == date(2026, 8, 19)


def test_every_symbol_has_explicit_native_session_contract() -> None:
    assert set(MARKET_15M_SERIES_POLICIES) == {
        "NQ=F", "^IXIC", "^GSPC", "^VIX", "^FVX", "^TNX", "^TYX"
    }
    for policy in MARKET_15M_SERIES_POLICIES.values():
        assert policy.source_timezone
        assert policy.expected_start_policy
        assert policy.session_boundary_policy
        assert policy.market_closed_policy
        assert policy.completed_bar_rule == "BAR_END_LE_RETRIEVED_AT"
