from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.maintenance import run_yahoo_market_current as yahoo_runner
import stock_data.orchestration.yahoo_market_current as yahoo_current_module
from stock_data.orchestration.automatic_fallback import FallbackInvariantError
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.yahoo_market_current import (
    NATIVE_15M_SERIES,
    YAHOO_CURRENT_30M_SERIES_IDS,
    describe_yahoo_market_current,
    fetch_global_market_30m_current,
    replay_yahoo_market_current,
    run_yahoo_market_current,
)
from stock_data.providers.yahoo import GLOBAL_MARKET_60M_REGISTRY
from stock_data.providers.yahoo_15m import fetch_market_15m
from stock_data.gui.services import DashboardService
from stock_data.orchestration.update_event_log import EventState, LocalUpdateEventLog


def _global_frame(series_id: str) -> pd.DataFrame:
    spec = GLOBAL_MARKET_60M_REGISTRY[series_id]
    return pd.DataFrame([{
        "market_date": pd.Timestamp("2026-08-22"),
        "market": spec["market"],
        "symbol": series_id,
        "asset_type": "INDEX",
        "bar_start": pd.Timestamp("2026-08-22T02:00:00Z"),
        "bar_end": pd.Timestamp("2026-08-22T02:30:00Z"),
        "timezone": spec["timezone"],
        "session": "GLOBAL_CONTINUOUS",
        "interval": "30m",
        "actual_duration_minutes": 30,
        "open": 99.0,
        "high": 101.0,
        "low": 98.0,
        "close": 100.0,
        "volume": 1,
        "provider": "yahoo_chart_api",
        "provider_symbol": spec["provider_symbol"],
        "retrieved_at": pd.Timestamp("2026-08-22T03:00:00Z"),
    }])


def _native_frame(series_id: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "market": "CBOE",
        "provider_symbol": series_id,
        "bar_end": pd.Timestamp("2026-08-22T02:45:00Z"),
        "close": 25.0,
    }])


def _native_response(
    *, open_values: list[float | None], high_values: list[float | None],
    low_values: list[float | None], close_values: list[float | None],
) -> SimpleNamespace:
    start = pd.Timestamp("2026-08-24T13:00:00Z")
    timestamps = [int((start + timedelta(minutes=15 * index)).timestamp()) for index in range(3)]
    body = {
        "chart": {"error": None, "result": [{
            "meta": {
                "symbol": "^VIX", "dataGranularity": "15m",
                "exchangeTimezoneName": "America/Chicago",
            },
            "timestamp": timestamps,
            "indicators": {"quote": [{
                "open": open_values, "high": high_values,
                "low": low_values, "close": close_values,
                "volume": [0, None, 0],
            }]},
        }]},
    }
    response = SimpleNamespace(json=lambda: body, raise_for_status=lambda: None)
    return SimpleNamespace(get=lambda *_args, **_kwargs: response)


FUTURES_30M_SERIES = (
    "UST2_FUTURES_60M", "UST10_FUTURES_60M", "UST30_FUTURES_60M",
    "NQ_FUTURES_CURRENT_60M", "GOLD_CURRENT_60M", "WTI_CURRENT_60M",
    "SP500_FUTURES_CURRENT_60M", "DOW_FUTURES_CURRENT_60M",
)
YAHOO_ROUTE_COUNT = len(YAHOO_CURRENT_30M_SERIES_IDS) + len(NATIVE_15M_SERIES)


def _global_response(
    series_id: str, *, newest_ohlc: tuple[float | None, ...],
    meta_updates: dict[str, object] | None = None,
) -> SimpleNamespace:
    spec = GLOBAL_MARKET_60M_REGISTRY[series_id]
    starts = (
        pd.Timestamp("2026-08-22T01:00:00Z"),
        pd.Timestamp("2026-08-22T01:30:00Z"),
        pd.Timestamp("2026-08-22T01:37:00Z"),
    )
    first = (99.0, 101.0, 98.0, 100.0)
    quote = (123.0, 124.0, 122.0, 123.5)
    rows = (first, newest_ohlc, quote)
    meta = {
            "symbol": spec["provider_symbol"], "dataGranularity": "30m",
            "instrumentType": spec["instrument_type"],
            "regularMarketTime": int(pd.Timestamp("2026-08-22T02:10:00Z").timestamp()),
    }
    if spec.get("expected_currency") is not None:
        meta["currency"] = spec["expected_currency"]
    if spec.get("accepted_yahoo_exchanges"):
        meta["exchangeName"] = spec["accepted_yahoo_exchanges"][0]
    meta.update(meta_updates or {})
    body = {"chart": {"error": None, "result": [{
        "meta": meta,
        "timestamp": [int(value.timestamp()) for value in starts],
        "indicators": {"quote": [{
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "volume": [1, None, 1],
        }]},
    }]}}
    response = SimpleNamespace(json=lambda: body, raise_for_status=lambda: None)
    return SimpleNamespace(get=lambda *_args, **_kwargs: response)


@pytest.mark.parametrize(
    "series_id",
    (
        "SP500_FUTURES_CURRENT_60M", "DOW_FUTURES_CURRENT_60M",
        "SOX_CURRENT_60M", "DOLLAR_INDEX_CURRENT_60M",
    ),
)
@pytest.mark.parametrize(
    ("meta_updates", "message"),
    [
        ({"currency": "KRW"}, "currency identity differs"),
        ({"exchangeName": "WRONG"}, "exchange identity differs"),
    ],
)
def test_new_global_30m_routes_fail_closed_on_currency_or_exchange_identity(
    series_id: str, meta_updates: dict[str, object], message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        fetch_global_market_30m_current(
            series_id,
            start=datetime(2026, 8, 22, 0, tzinfo=timezone.utc),
            end=datetime(2026, 8, 22, 3, tzinfo=timezone.utc),
            retrieved_at=datetime(2026, 8, 22, 2, 10, tzinfo=timezone.utc),
            session=_global_response(
                series_id, newest_ohlc=(100.0, 102.0, 99.0, 101.0),
                meta_updates=meta_updates,
            ),
        )


def test_yahoo_current_dry_run_lists_new_symbols_and_21_call_budget() -> None:
    report = describe_yahoo_market_current()

    assert report["status"] == "DRY_RUN_PASS"
    assert report["api_calls"] == 0
    assert report["max_api_calls"] == YAHOO_ROUTE_COUNT == 21
    assert {
        row["provider_symbol"] for row in report["routes"]
    } >= {"ES=F", "YM=F", "^SOX", "DX-Y.NYB"}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "atomic promotion did not persist circuit state",
            "FAIL_FALLBACK_INVARIANT_PROMOTION_STATE_NOT_PERSISTED",
        ),
        (
            "atomic promotion rollback failed",
            "FAIL_FALLBACK_INVARIANT_PROMOTION_ROLLBACK_FAILED",
        ),
        (
            "unrecognized invariant",
            "FAIL_FALLBACK_INVARIANT_UNCLASSIFIED",
        ),
    ],
)
def test_yahoo_failure_outcome_retains_bounded_fallback_invariant_reason(
    message: str, expected: str,
) -> None:
    assert yahoo_current_module._safe_failure_outcome(
        FallbackInvariantError(message)
    ) == expected


@pytest.mark.parametrize("series_id", FUTURES_30M_SERIES)
def test_global_futures_fetch_rejects_newest_completed_grid_null_or_partial_ohlc(
    series_id: str,
) -> None:
    position = FUTURES_30M_SERIES.index(series_id)
    newest = (
        (None, None, None, None)
        if position < 3 else (100.0, 102.0, None, 101.0)
    )

    with pytest.raises(
        yahoo_current_module.CompletedGridOHLCUnavailableError,
        match="newest completed 30m grid row",
    ):
        fetch_global_market_30m_current(
            series_id,
            start=datetime(2026, 8, 22, 0, tzinfo=timezone.utc),
            end=datetime(2026, 8, 22, 3, tzinfo=timezone.utc),
            retrieved_at=datetime(2026, 8, 22, 2, 10, tzinfo=timezone.utc),
            session=_global_response(series_id, newest_ohlc=newest),
        )


@pytest.mark.parametrize("series_id", FUTURES_30M_SERIES)
def test_global_futures_fetch_accepts_newer_numeric_grid_and_excludes_quote_time_row(
    series_id: str,
) -> None:
    frame = fetch_global_market_30m_current(
        series_id,
        start=datetime(2026, 8, 22, 0, tzinfo=timezone.utc),
        end=datetime(2026, 8, 22, 3, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 8, 22, 2, 10, tzinfo=timezone.utc),
        session=_global_response(
            series_id, newest_ohlc=(100.0, 102.0, 99.0, 101.0),
        ),
    )

    assert frame["bar_start"].tolist() == [
        pd.Timestamp("2026-08-22T01:00:00Z"),
        pd.Timestamp("2026-08-22T01:30:00Z"),
    ]
    assert frame.iloc[-1]["bar_end"] == pd.Timestamp("2026-08-22T02:00:00Z")


def test_unified_yahoo_types_completed_grid_null_by_exact_prior_presence(
    tmp_path,
) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)

    def unavailable_futures(series_id: str, **_kwargs) -> pd.DataFrame:
        if series_id in FUTURES_30M_SERIES:
            raise yahoo_current_module.CompletedGridOHLCUnavailableError("sanitized")
        return _global_frame(series_id)

    absent = run_yahoo_market_current(
        tmp_path / "absent", as_of=clock,
        global_fetcher=unavailable_futures,
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    absent_outcomes = {
        row["series_id"]: row["outcome"]
        for row in absent["series_terminal_outcomes"]
    }
    assert absent["status"] == "PARTIAL_FAILURE"
    assert absent["failed"] == len(FUTURES_30M_SERIES)
    assert all(
        absent_outcomes[series_id] == "FAIL_COMPLETED_GRID_OHLC_UNAVAILABLE"
        for series_id in FUTURES_30M_SERIES
    )

    retained_root = tmp_path / "retained"
    run_yahoo_market_current(
        retained_root, as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    observation_root = retained_root / "data/state/current_observations"
    before = {
        path.relative_to(observation_root): path.read_bytes()
        for path in observation_root.rglob("*.json")
    }

    retained = run_yahoo_market_current(
        retained_root, as_of=clock + timedelta(minutes=30),
        global_fetcher=unavailable_futures,
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    retained_outcomes = {
        row["series_id"]: row["outcome"]
        for row in retained["series_terminal_outcomes"]
    }
    assert retained["status"] == "PARTIAL_FAILURE"
    assert retained["accepted"] == YAHOO_ROUTE_COUNT - len(FUTURES_30M_SERIES)
    assert retained["failed"] == len(FUTURES_30M_SERIES)
    assert retained["preserved"] == YAHOO_ROUTE_COUNT
    assert all(
        retained_outcomes[series_id]
        == "FAIL_COMPLETED_GRID_OHLC_UNAVAILABLE_PRIOR_VALUE_PRESERVED"
        for series_id in FUTURES_30M_SERIES
    )
    assert {
        path.relative_to(observation_root): path.read_bytes()
        for path in observation_root.rglob("*.json")
    } == before


def test_unified_yahoo_never_preserves_completed_grid_null_for_nonfuture(
    tmp_path,
) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    run_yahoo_market_current(
        tmp_path, as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    projection = (
        tmp_path / "data/state/current_observations/global60m_current"
        / "usd_krw_60m.json"
    )
    before = projection.read_bytes()

    def injected_nonfuture(series_id: str, **_kwargs) -> pd.DataFrame:
        if series_id == "USD_KRW_60M":
            raise yahoo_current_module.CompletedGridOHLCUnavailableError("sanitized")
        return _global_frame(series_id)

    report = run_yahoo_market_current(
        tmp_path, as_of=clock + timedelta(minutes=30),
        global_fetcher=injected_nonfuture,
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    outcome = next(
        row["outcome"] for row in report["series_terminal_outcomes"]
        if row["series_id"] == "USD_KRW_60M"
    )
    assert report["status"] == "PARTIAL_FAILURE" and report["failed"] == 1
    assert outcome == "FAIL_COMPLETED_GRID_OHLC_UNAVAILABLE"
    assert projection.read_bytes() == before


def test_native_fetch_omits_fully_null_yahoo_gap_before_strict_validation() -> None:
    frame = fetch_market_15m(
        "^VIX",
        start=datetime(2026, 8, 24, 13, tzinfo=timezone.utc),
        end=datetime(2026, 8, 24, 14, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 8, 24, 14, tzinfo=timezone.utc),
        session=_native_response(
            open_values=[16.0, None, 15.8], high_values=[16.1, None, 15.9],
            low_values=[15.9, None, 15.7], close_values=[16.0, None, 15.85],
        ),
    )

    assert frame["bar_start"].tolist() == [
        pd.Timestamp("2026-08-24T13:00:00Z"),
        pd.Timestamp("2026-08-24T13:30:00Z"),
    ]
    assert not frame[["open", "high", "low", "close"]].isna().any().any()


def test_native_fetch_still_rejects_partially_null_ohlc_row() -> None:
    with pytest.raises(ValueError, match="15m OHLC contains missing"):
        fetch_market_15m(
            "^VIX",
            start=datetime(2026, 8, 24, 13, tzinfo=timezone.utc),
            end=datetime(2026, 8, 24, 14, tzinfo=timezone.utc),
            retrieved_at=datetime(2026, 8, 24, 14, tzinfo=timezone.utc),
            session=_native_response(
                open_values=[16.0, None, 15.8], high_values=[16.1, 16.0, 15.9],
                low_values=[15.9, 15.7, 15.7], close_values=[16.0, 15.8, 15.85],
            ),
        )


def test_unified_yahoo_operation_projects_30m_and_native_15m_under_one_report(tmp_path) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    report = run_yahoo_market_current(
        tmp_path,
        as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )

    assert report["status"] == "PASS"
    assert report["api_calls"] == report["max_api_calls"] == YAHOO_ROUTE_COUNT
    assert report["schedule_interval"] == report["global_bar_interval"] == "30m"
    assert report["native_bar_interval"] == "15m" and report["history_writes"] == 0
    for series_id in YAHOO_CURRENT_30M_SERIES_IDS:
        payload = json.loads((
            tmp_path / "data/state/current_observations/global60m_current"
            / f"{series_id.lower()}.json"
        ).read_text(encoding="utf-8"))
        assert payload["observations"][0]["interval"] == "30m"
    for series_id in NATIVE_15M_SERIES:
        payload = json.loads((
            tmp_path / "data/state/current_observations/yahoo_native15m_current"
            / f"{series_id.replace('^', 'idx').lower()}.json"
        ).read_text(encoding="utf-8"))
        assert payload["observations"][0]["interval"] == "15m"
    coverage = DashboardService(tmp_path).current_observation_coverage(now_utc=clock)
    assert coverage["NQ_FUTURES_CURRENT_60M"].interval == "30m"
    assert coverage["NQ_FUTURES_CURRENT_60M"].freshness == "CURRENT_COMPLETED_30M"

    replay = replay_yahoo_market_current(tmp_path)
    assert replay["status"] == "PASS"
    assert replay["api_calls"] == replay["max_api_calls"] == 0
    assert replay["replayed"] == YAHOO_ROUTE_COUNT and replay["missing"] == 0


def test_unified_yahoo_operation_preserves_other_lanes_when_one_identity_fails(tmp_path) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)

    def native_fetcher(series_id: str, **_kwargs) -> pd.DataFrame:
        if series_id == "^TNX":
            raise RuntimeError("provider unavailable")
        return _native_frame(series_id)

    report = run_yahoo_market_current(
        tmp_path,
        as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=native_fetcher,
    )

    assert report["status"] == "PARTIAL_FAILURE"
    assert report["accepted"] == YAHOO_ROUTE_COUNT - 1 and report["failed"] == 1
    assert not (tmp_path / "data/state/current_observations/yahoo_native15m_current/idxtnx.json").exists()
    assert (tmp_path / "data/state/current_observations/yahoo_native15m_current/idxvix.json").exists()


def test_unified_yahoo_operation_uses_latest_completed_cash_session_on_weekend_and_holiday(tmp_path) -> None:
    kr_clock = datetime(2026, 8, 23, 14, 2, tzinfo=timezone.utc)
    kr_starts: dict[str, datetime] = {}

    run_yahoo_market_current(
        tmp_path / "kr",
        as_of=kr_clock,
        global_fetcher=lambda series_id, **kwargs: (
            kr_starts.setdefault(series_id, kwargs["start"]) and _global_frame(series_id)
        ),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )

    kr_calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    expected_kr_start = kr_calendar.session_open(date(2026, 8, 21)).astimezone(timezone.utc)
    assert kr_starts["KOSPI_CURRENT_60M"] == expected_kr_start
    assert kr_starts["KOSDAQ_CURRENT_60M"] == expected_kr_start
    assert kr_clock - expected_kr_start > timedelta(days=2)
    assert kr_starts["USD_KRW_60M"] == kr_clock - timedelta(days=2)

    us_clock = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    us_starts: dict[str, datetime] = {}
    run_yahoo_market_current(
        tmp_path / "us",
        as_of=us_clock,
        global_fetcher=lambda series_id, **kwargs: (
            us_starts.setdefault(series_id, kwargs["start"]) and _global_frame(series_id)
        ),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    us_calendar = ExchangeTradingCalendar(ExchangeMarket.US)
    expected_us_start = us_calendar.session_open(date(2026, 9, 4)).astimezone(timezone.utc)
    assert us_starts["SP500_CURRENT_60M"] == expected_us_start
    assert us_starts["NASDAQ_CURRENT_60M"] == expected_us_start
    assert us_starts["SOXX_CURRENT_60M"] == expected_us_start
    assert us_clock - expected_us_start > timedelta(days=2)


def test_unified_yahoo_operation_preserves_unchanged_completed_bars_as_success(tmp_path) -> None:
    first_clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    fetch_global = lambda series_id, **_kwargs: _global_frame(series_id)
    fetch_native = lambda series_id, **_kwargs: _native_frame(series_id)
    first = run_yahoo_market_current(
        tmp_path, as_of=first_clock, global_fetcher=fetch_global, native_fetcher=fetch_native,
    )
    paths = tuple((tmp_path / "data/state/current_observations").rglob("*.json"))
    before = {path: path.read_bytes() for path in paths if not path.name.endswith((".comparison.json", ".session.json"))}

    second = run_yahoo_market_current(
        tmp_path, as_of=first_clock + timedelta(minutes=30),
        global_fetcher=fetch_global, native_fetcher=fetch_native,
    )

    assert first["status"] == second["status"] == "PASS"
    assert second["accepted"] == second["preserved"] == YAHOO_ROUTE_COUNT
    assert second["failed"] == 0
    assert all(row["outcome"].endswith("PRESERVED") for row in second["series_terminal_outcomes"])
    assert {path: path.read_bytes() for path in before} == before


def test_unified_yahoo_operation_types_older_30m_bar_as_prior_value_preserved(
    tmp_path, monkeypatch,
) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    run_yahoo_market_current(
        tmp_path, as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    projection = (
        tmp_path / "data/state/current_observations/global60m_current"
        / "kospi_current_60m.json"
    )
    before = projection.read_bytes()
    comparison_calls: list[str] = []
    trace_calls: list[str] = []
    original_comparison = yahoo_current_module._write_current_comparison
    original_trace = yahoo_current_module._write_current_session_trace

    def record_comparison(root, series_id, frame):
        comparison_calls.append(series_id)
        return original_comparison(root, series_id, frame)

    def record_trace(root, series_id, frame):
        trace_calls.append(series_id)
        return original_trace(root, series_id, frame)

    monkeypatch.setattr(yahoo_current_module, "_write_current_comparison", record_comparison)
    monkeypatch.setattr(yahoo_current_module, "_write_current_session_trace", record_trace)

    def older_global(series_id: str, **_kwargs) -> pd.DataFrame:
        frame = _global_frame(series_id)
        if series_id == "KOSPI_CURRENT_60M":
            frame.loc[0, "bar_start"] = pd.Timestamp("2026-08-22T01:30:00Z")
            frame.loc[0, "bar_end"] = pd.Timestamp("2026-08-22T02:00:00Z")
            frame.loc[0, "open"] = frame.loc[0, "high"] = 91.0
            frame.loc[0, "low"] = frame.loc[0, "close"] = 90.0
        return frame

    report = run_yahoo_market_current(
        tmp_path, as_of=clock + timedelta(minutes=30),
        global_fetcher=older_global,
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )

    target = next(
        row for row in report["series_terminal_outcomes"]
        if row["series_id"] == "KOSPI_CURRENT_60M"
    )
    assert report["status"] == "PASS" and report["failed"] == 0
    assert target["outcome"] == "OLDER_30M_BAR_PRIOR_VALUE_PRESERVED"
    assert projection.read_bytes() == before
    assert "KOSPI_CURRENT_60M" not in comparison_calls
    assert "KOSPI_CURRENT_60M" not in trace_calls


def test_unified_yahoo_operation_types_older_native_15m_bar_as_prior_value_preserved(
    tmp_path,
) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    run_yahoo_market_current(
        tmp_path, as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    projection = (
        tmp_path / "data/state/current_observations/yahoo_native15m_current"
        / "idxtnx.json"
    )
    before = projection.read_bytes()

    def older_native(series_id: str, **_kwargs) -> pd.DataFrame:
        frame = _native_frame(series_id)
        if series_id == "^TNX":
            frame.loc[0, "bar_end"] = pd.Timestamp("2026-08-22T02:30:00Z")
            frame.loc[0, "close"] = 24.0
        return frame

    report = run_yahoo_market_current(
        tmp_path, as_of=clock + timedelta(minutes=30),
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=older_native,
    )

    target = next(
        row for row in report["series_terminal_outcomes"]
        if row["series_id"] == "^TNX"
    )
    assert report["status"] == "PASS" and report["failed"] == 0
    assert target["outcome"] == "OLDER_15M_BAR_PRIOR_VALUE_PRESERVED"
    assert projection.read_bytes() == before


def test_unified_yahoo_operation_preserves_prior_for_native_same_timestamp_revision(
    tmp_path,
) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    run_yahoo_market_current(
        tmp_path, as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    projection = (
        tmp_path / "data/state/current_observations/yahoo_native15m_current"
        / "idxtnx.json"
    )
    before = projection.read_bytes()

    def changed_native(series_id: str, **_kwargs) -> pd.DataFrame:
        frame = _native_frame(series_id)
        if series_id == "^TNX":
            frame.loc[0, "close"] = 24.0
        return frame

    report = run_yahoo_market_current(
        tmp_path, as_of=clock + timedelta(minutes=30),
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=changed_native,
    )

    target = next(
        row for row in report["series_terminal_outcomes"]
        if row["series_id"] == "^TNX"
    )
    assert report["status"] == "PASS" and report["failed"] == 0
    assert target["outcome"] == "REVISED_15M_BAR_PRIOR_VALUE_PRESERVED"
    assert projection.read_bytes() == before


def test_unified_yahoo_operation_preserves_prior_and_traces_for_30m_same_timestamp_revision(
    tmp_path, monkeypatch,
) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    run_yahoo_market_current(
        tmp_path, as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    projection = (
        tmp_path / "data/state/current_observations/global60m_current"
        / "kospi_current_60m.json"
    )
    before = projection.read_bytes()
    comparison_calls: list[str] = []
    trace_calls: list[str] = []
    monkeypatch.setattr(
        yahoo_current_module, "_write_current_comparison",
        lambda _root, series_id, _frame: comparison_calls.append(series_id),
    )
    monkeypatch.setattr(
        yahoo_current_module, "_write_current_session_trace",
        lambda _root, series_id, _frame: trace_calls.append(series_id),
    )

    def changed_global(series_id: str, **_kwargs) -> pd.DataFrame:
        frame = _global_frame(series_id)
        if series_id == "KOSPI_CURRENT_60M":
            frame.loc[0, "close"] = 100.5
        return frame

    report = run_yahoo_market_current(
        tmp_path, as_of=clock + timedelta(minutes=30),
        global_fetcher=changed_global,
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )

    target = next(
        row for row in report["series_terminal_outcomes"]
        if row["series_id"] == "KOSPI_CURRENT_60M"
    )
    assert report["status"] == "PASS" and report["failed"] == 0
    assert target["outcome"] == "REVISED_30M_BAR_PRIOR_VALUE_PRESERVED"
    assert projection.read_bytes() == before
    assert "KOSPI_CURRENT_60M" not in comparison_calls
    assert "KOSPI_CURRENT_60M" not in trace_calls


def test_same_timestamp_revision_requires_an_exact_route_prior(tmp_path) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    run_yahoo_market_current(
        tmp_path, as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    projection = (
        tmp_path / "data/state/current_observations/yahoo_native15m_current"
        / "idxtnx.json"
    )
    payload = json.loads(projection.read_text(encoding="utf-8"))
    payload["observations"][0]["route_id"] = "yahoo-market-current:CBOE:OTHER"
    projection.write_text(json.dumps(payload), encoding="utf-8")

    def changed_native(series_id: str, **_kwargs) -> pd.DataFrame:
        frame = _native_frame(series_id)
        if series_id == "^TNX":
            frame.loc[0, "close"] = 24.0
        return frame

    report = run_yahoo_market_current(
        tmp_path, as_of=clock + timedelta(minutes=30),
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=changed_native,
    )
    target = next(
        row for row in report["series_terminal_outcomes"]
        if row["series_id"] == "^TNX"
    )
    assert target["outcome"] == "FAIL_RUNTIMEERROR"
    assert report["status"] == "PARTIAL_FAILURE"


@pytest.mark.parametrize("invalid_value", [0.0, -1.0, float("nan"), float("inf")])
def test_same_timestamp_invalid_revision_values_fail_for_30m_and_native15m(
    tmp_path, monkeypatch, invalid_value: float,
) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    run_yahoo_market_current(
        tmp_path, as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    projections = (
        tmp_path / "data/state/current_observations/global60m_current/kospi_current_60m.json",
        tmp_path / "data/state/current_observations/yahoo_native15m_current/idxtnx.json",
    )
    before = {path: path.read_bytes() for path in projections}
    comparison_calls: list[str] = []
    trace_calls: list[str] = []
    monkeypatch.setattr(
        yahoo_current_module, "_write_current_comparison",
        lambda _root, series_id, _frame: comparison_calls.append(series_id),
    )
    monkeypatch.setattr(
        yahoo_current_module, "_write_current_session_trace",
        lambda _root, series_id, _frame: trace_calls.append(series_id),
    )

    def invalid_global(series_id: str, **_kwargs) -> pd.DataFrame:
        frame = _global_frame(series_id)
        if series_id == "KOSPI_CURRENT_60M":
            frame.loc[0, "close"] = invalid_value
        return frame

    def invalid_native(series_id: str, **_kwargs) -> pd.DataFrame:
        frame = _native_frame(series_id)
        if series_id == "^TNX":
            frame.loc[0, "close"] = invalid_value
        return frame

    report = run_yahoo_market_current(
        tmp_path, as_of=clock + timedelta(minutes=30),
        global_fetcher=invalid_global, native_fetcher=invalid_native,
    )
    outcomes = {
        row["series_id"]: row["outcome"]
        for row in report["series_terminal_outcomes"]
    }
    assert outcomes["KOSPI_CURRENT_60M"] == "FAIL_VALUEERROR"
    assert outcomes["^TNX"] == "FAIL_VALUEERROR"
    assert report["status"] == "PARTIAL_FAILURE" and report["failed"] == 2
    assert {path: path.read_bytes() for path in projections} == before
    assert "KOSPI_CURRENT_60M" not in comparison_calls
    assert "KOSPI_CURRENT_60M" not in trace_calls


@pytest.mark.parametrize(
    "candidate_time",
    [pd.Timestamp("2026-08-22T02:30:00"), pd.Timestamp("2026-08-22T02:30:00Z")],
)
def test_preservation_disposition_rejects_naive_or_future_candidate_time(
    tmp_path, candidate_time: pd.Timestamp,
) -> None:
    clock = datetime(2026, 8, 22, 3, tzinfo=timezone.utc)
    run_yahoo_market_current(
        tmp_path, as_of=clock,
        global_fetcher=lambda series_id, **_kwargs: _global_frame(series_id),
        native_fetcher=lambda series_id, **_kwargs: _native_frame(series_id),
    )
    validation_clock = (
        clock if candidate_time.tzinfo is None
        else datetime(2026, 8, 22, 2, 29, tzinfo=timezone.utc)
    )
    with pytest.raises(ValueError, match="candidate is invalid"):
        yahoo_current_module._projection_disposition(
            tmp_path, market="KR_INDEX", provider_symbol="^KS11", value=100.0,
            bar_end=candidate_time, clock=validation_clock,
            interval=yahoo_current_module.ObservationInterval.MINUTES_30,
            output=(
                Path("data/state/current_observations/global60m_current")
                / "kospi_current_60m.json"
            ),
        )


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ({"status": "PASS", "failed": 0, "preserved": YAHOO_ROUTE_COUNT}, 0),
        ({"status": "PARTIAL_FAILURE", "failed": 1}, 1),
        ({"status": "PASS"}, 1),
    ],
)
def test_yahoo_runner_exit_code_fails_closed(
    tmp_path, monkeypatch, capsys, report: dict[str, object], expected: int,
) -> None:
    monkeypatch.setattr(yahoo_runner, "run_yahoo_market_current", lambda _root: report)

    assert yahoo_runner.main(["--project-root", str(tmp_path)]) == expected
    assert json.loads(capsys.readouterr().out) == report


def test_yahoo_runner_emits_one_bound_started_and_terminal_event(
    tmp_path, monkeypatch, capsys,
) -> None:
    report = {
        "status": "PASS", "failed": 0, "accepted": YAHOO_ROUTE_COUNT,
        "preserved": 2, "api_calls": YAHOO_ROUTE_COUNT,
    }
    monkeypatch.setattr(yahoo_runner, "run_yahoo_market_current", lambda _root: report)

    assert yahoo_runner.main(["--project-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == report
    events = LocalUpdateEventLog(
        tmp_path / "artifacts/runtime_logs/data_updates"
    ).read_events()
    assert [event.state for event in events] == [EventState.STARTED, EventState.SUCCEEDED]
    assert len({event.run_id for event in events}) == 1
    assert events[-1].provider_call_count == YAHOO_ROUTE_COUNT
    encoded = json.dumps([event.to_dict() for event in events], ensure_ascii=False)
    assert "http://" not in encoded and "https://" not in encoded
    assert "api_key" not in encoded.lower() and "payload" not in encoded.lower()


def test_yahoo_runner_failure_is_typed_and_logger_failure_is_non_authoritative(
    tmp_path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(
        yahoo_runner, "run_yahoo_market_current",
        lambda _root: (_ for _ in ()).throw(ValueError("payload=https://secret.invalid token=abc")),
    )
    with pytest.raises(ValueError):
        yahoo_runner.main(["--project-root", str(tmp_path / "typed")])
    events = LocalUpdateEventLog(
        tmp_path / "typed/artifacts/runtime_logs/data_updates"
    ).read_events()
    assert [event.state for event in events] == [
        EventState.STARTED, EventState.VALIDATION_FAILURE,
    ]
    assert len({event.run_id for event in events}) == 1
    assert "secret.invalid" not in json.dumps(events[-1].to_dict())

    class FailingLog:
        def append(self, _event):
            raise OSError("logger unavailable")

    report = {"status": "PASS", "failed": 0, "api_calls": YAHOO_ROUTE_COUNT}
    monkeypatch.setattr(yahoo_runner, "run_yahoo_market_current", lambda _root: report)
    monkeypatch.setattr(yahoo_runner, "LocalUpdateEventLog", lambda _path: FailingLog())
    assert yahoo_runner.main(["--project-root", str(tmp_path / "logger")]) == 0
    streams = capsys.readouterr()
    assert json.loads(streams.out) == report
    assert streams.err.count("runtime_event_log=FAILED") == 2
    assert "logger unavailable" not in streams.err
