from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread

import pandas as pd

from stock_data.orchestration.current_observation import CurrentObservationFileStore, ObservationIdentity
from stock_data.providers.fdr_display_daily import (
    COOLDOWN, FDRDisplayDailyLandingStore, FDRDisplayDailyOutcome,
    FDRDisplayDailyResponse, FDRDisplayDailyRefresher,
)


IDENTITY = ObservationIdentity("DASHBOARD_CURRENT", "XUS", "^GSPC")
NAVER_IDENTITY = ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "035420")
START, END = date(2026, 8, 19), date(2026, 8, 20)


def _frame(close: float = 6500.0) -> pd.DataFrame:
    return pd.DataFrame({
        "Open": [6400.0, 6450.0], "High": [6500.0, 6550.0], "Low": [6350.0, 6400.0],
        "Close": [6450.0, close], "Adj Close": [6450.0, close], "Volume": [1, 2],
    }, index=pd.to_datetime(["2026-08-19", "2026-08-20"]))


def _naver_frame(close: float = 210000.0) -> pd.DataFrame:
    return pd.DataFrame({
        "Open": [200000.0, 205000.0], "High": [211000.0, 212000.0], "Low": [199000.0, 204000.0],
        "Close": [205000.0, close], "Volume": [1, 2], "Change": [0.1, 0.2],
    }, index=pd.to_datetime(["2026-08-19", "2026-08-20"]))


def _response(frame: pd.DataFrame | None = None, *, status: int = 200) -> FDRDisplayDailyResponse:
    return FDRDisplayDailyResponse(status, b"synthetic-fdr-daily-body", _frame() if frame is None else frame)


def _refresher(tmp_path, clock) -> FDRDisplayDailyRefresher:
    return FDRDisplayDailyRefresher(
        store=CurrentObservationFileStore(tmp_path / "observations.json"),
        landing=FDRDisplayDailyLandingStore(tmp_path / "landing"), now=lambda: clock[0],
    )


def test_allowlisted_daily_refresh_counts_one_request_retains_landing_and_replays_api_zero(tmp_path) -> None:
    clock = [datetime(2026, 8, 21, 1, tzinfo=timezone.utc)]
    refresher = _refresher(tmp_path, clock)
    calls = []

    def transport(route, start, end, timeout, retry):
        calls.append((route, start, end, timeout, retry))
        return _response()

    result = refresher.refresh(identity=IDENTITY, start=START, end=END, transport=transport)

    assert result.outcome is FDRDisplayDailyOutcome.DECIDED
    assert result.api_calls == 1 and result.observation is not None
    assert result.observation.interval.value == "1d"
    assert result.observation.unit == "index points"
    assert result.observation.upstream_provider == "YAHOO"
    assert result.observation.display_only and not result.observation.pit_safe
    assert calls == [("YAHOO:^GSPC", START, END, 10, 0)]
    assert result.observation.provider_timestamp_utc == "2026-08-20T00:00:00+00:00"
    assert len(list((tmp_path / "landing").rglob("response.bin"))) == 1
    replay = refresher.replay(IDENTITY)
    assert replay.outcome is FDRDisplayDailyOutcome.API_ZERO_REPLAY and replay.api_calls == 0
    assert replay.observation == result.observation


def test_failed_daily_refresh_opens_route_circuit_and_preserves_prior_valid(tmp_path) -> None:
    clock = [datetime(2026, 8, 21, 1, tzinfo=timezone.utc)]
    refresher = _refresher(tmp_path, clock)
    first = refresher.refresh(identity=IDENTITY, start=START, end=END, transport=lambda *_: _response())
    clock[0] += COOLDOWN
    failed = refresher.refresh(identity=IDENTITY, start=START, end=END, transport=lambda *_: _response(status=500))

    assert first.observation is not None
    assert failed.observation == first.observation
    assert failed.api_calls == 1
    route = refresher.spec_for(IDENTITY)
    stored = refresher._store.load(f"fdr-display-daily:{route.route.replace('^', 'IDX')}")
    assert stored.is_open and stored.safe_code == "FDR_DISPLAY_NO_ALTERNATE_ROUTE"
    assert not list((tmp_path / "landing").rglob("response.bin"))[1:]


def test_cooldown_and_coalescing_issue_no_second_transport_request(tmp_path) -> None:
    clock = [datetime(2026, 8, 21, 1, tzinfo=timezone.utc)]
    refresher = _refresher(tmp_path, clock)
    calls = []
    entered, release = Event(), Event()

    def blocked_transport(*_args):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=2)
        return _response()

    thread = Thread(target=lambda: refresher.refresh(identity=IDENTITY, start=START, end=END, transport=blocked_transport))
    thread.start()
    assert entered.wait(timeout=2)
    coalesced = refresher.refresh(identity=IDENTITY, start=START, end=END, transport=lambda *_: (_ for _ in ()).throw(AssertionError("must not call")))
    release.set()
    thread.join(timeout=2)
    assert coalesced.outcome is FDRDisplayDailyOutcome.COALESCED and coalesced.api_calls == 0
    assert calls == [1]
    cooldown = refresher.refresh(identity=IDENTITY, start=START, end=END, transport=lambda *_: (_ for _ in ()).throw(AssertionError("must not call")))
    assert cooldown.outcome is FDRDisplayDailyOutcome.COOLDOWN and cooldown.api_calls == 0


def test_schema_failure_retains_successful_landing_then_fails_numeric_free(tmp_path) -> None:
    clock = [datetime(2026, 8, 21, 1, tzinfo=timezone.utc)]
    refresher = _refresher(tmp_path, clock)
    malformed = _frame().drop(columns=["Adj Close"])

    result = refresher.refresh(identity=IDENTITY, start=START, end=END, transport=lambda *_: _response(malformed))

    assert result.observation is None and result.api_calls == 1
    assert len(list((tmp_path / "landing").rglob("response.bin"))) == 1


def test_naver_schema_and_validated_second_domestic_identity_use_close_without_yahoo_adj_close(tmp_path) -> None:
    clock = [datetime(2026, 8, 21, 1, tzinfo=timezone.utc)]
    refresher = _refresher(tmp_path, clock)
    calls = []

    def transport(route, start, end, timeout, retry):
        calls.append((route, timeout, retry))
        return _response(_naver_frame())

    result = refresher.refresh(identity=NAVER_IDENTITY, start=START, end=END, transport=transport)

    assert result.observation is not None
    assert result.observation.value == 210000.0
    assert result.observation.unit == "KRW"
    assert result.observation.upstream_provider == "NAVER"
    assert calls == [("NAVER:035420", 10, 0)]


def test_yahoo_adjusted_close_must_be_positive_and_finite(tmp_path) -> None:
    clock = [datetime(2026, 8, 21, 1, tzinfo=timezone.utc)]
    refresher = _refresher(tmp_path, clock)
    bad = _frame().assign(**{"Adj Close": [6450.0, float("nan")]})

    result = refresher.refresh(identity=IDENTITY, start=START, end=END, transport=lambda *_: _response(bad))

    assert result.observation is None and result.api_calls == 1


def test_atomic_promotion_failure_preserves_prior_valid_daily_observation(tmp_path) -> None:
    class _FailOnceStore(CurrentObservationFileStore):
        fail_next_write = False

        def _write_state(self, state):
            if self.fail_next_write:
                self.fail_next_write = False
                raise OSError("synthetic write failure")
            super()._write_state(state)

    clock = [datetime(2026, 8, 21, 1, tzinfo=timezone.utc)]
    store = _FailOnceStore(tmp_path / "observations.json")
    refresher = FDRDisplayDailyRefresher(
        store=store, landing=FDRDisplayDailyLandingStore(tmp_path / "landing"), now=lambda: clock[0],
    )
    first = refresher.refresh(identity=IDENTITY, start=START, end=END, transport=lambda *_: _response())
    clock[0] += COOLDOWN
    store.fail_next_write = True
    failed = refresher.refresh(identity=IDENTITY, start=START, end=END, transport=lambda *_: _response(_frame(6600.0)))

    assert first.observation is not None
    assert failed.observation == first.observation
    assert failed.api_calls == 1
    assert refresher.replay(IDENTITY).observation == first.observation


def test_identity_outside_success_allowlist_is_rejected_before_transport(tmp_path) -> None:
    clock = [datetime(2026, 8, 21, 1, tzinfo=timezone.utc)]
    refresher = _refresher(tmp_path, clock)
    blocked = ObservationIdentity("DASHBOARD_CURRENT", "XUS", "^VIX")

    try:
        refresher.refresh(identity=blocked, start=START, end=END, transport=lambda *_: (_ for _ in ()).throw(AssertionError("must not call")))
    except ValueError as error:
        assert "allowlist" in str(error)
    else:
        raise AssertionError("non-allowlisted identity must fail")
