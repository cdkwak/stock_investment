from datetime import datetime, time, timezone
import json

import pandas as pd
import pytest
from requests import Response
from requests import exceptions as requests_exceptions

from stock_data.contracts.market_60m import MARKET_PRICE_60M_OBSERVATION
from stock_data.orchestration.global_market_60m import (
    CURRENT_SERIES_IDS,
    SERIES_IDS,
    _terminal_outcome,
    _terminal_reason_code,
    run_global_market_60m,
    run_global_market_current_60m,
)
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.market_60m import validate_market_price_60m
from stock_data.gui.services import DashboardService


PROVIDERS = {
    "KOSPI_CURRENT_60M": ("XKRX", "INDEX", "^KS11", "Asia/Seoul"),
    "KOSDAQ_CURRENT_60M": ("XKRX", "INDEX", "^KQ11", "Asia/Seoul"),
    "USD_KRW_60M": ("GLOBAL_FX", "FOREX", "KRW=X", "Asia/Seoul"),
    "UST2_FUTURES_60M": ("CBOT", "FUTURE_CONTINUOUS", "ZT=F", "America/Chicago"),
    "UST10_FUTURES_60M": ("CBOT", "FUTURE_CONTINUOUS", "ZN=F", "America/Chicago"),
    "UST30_FUTURES_60M": ("CBOT", "FUTURE_CONTINUOUS", "ZB=F", "America/Chicago"),
    "SP500_CURRENT_60M": ("XNYS", "INDEX", "^GSPC", "America/New_York"),
    "NASDAQ_CURRENT_60M": ("XNAS", "INDEX", "^IXIC", "America/New_York"),
    "NQ_FUTURES_CURRENT_60M": ("CME", "FUTURE_CONTINUOUS", "NQ=F", "America/New_York"),
    "SOXX_CURRENT_60M": ("XNAS", "ETF", "SOXX", "America/New_York"),
    "GOLD_CURRENT_60M": ("COMEX", "FUTURE_CONTINUOUS", "GC=F", "America/New_York"),
    "WTI_CURRENT_60M": ("NYMEX", "FUTURE_CONTINUOUS", "CL=F", "America/New_York"),
    "BITCOIN_CURRENT_60M": ("CRYPTO", "CRYPTOCURRENCY", "BTC-USD", "UTC"),
}


def _frame(series_id: str, close: float = 100.5) -> pd.DataFrame:
    market, asset_type, provider_symbol, zone = PROVIDERS[series_id]
    row = {
        "market_date": pd.Timestamp("2026-08-19"), "market": market, "symbol": series_id,
        "asset_type": asset_type, "bar_start": pd.Timestamp("2026-08-19T10:00:00Z"),
        "bar_end": pd.Timestamp("2026-08-19T11:00:00Z"), "timezone": zone,
        "session": "GLOBAL_CONTINUOUS", "interval": "60m", "actual_duration_minutes": 60,
        "open": 100.0, "high": 101.0, "low": 99.0, "close": close, "volume": None,
        "provider": "yahoo_chart_api", "provider_symbol": provider_symbol,
        "adjustment_status": "PROVIDER_UNADJUSTED_INTRADAY_DELAYED",
        "retrieved_at": pd.Timestamp("2026-08-19T12:00:00Z"),
        "fallback_used": False, "fallback_reason": None,
    }
    frame = pd.DataFrame([row], columns=MARKET_PRICE_60M_OBSERVATION.column_names)
    frame["volume"] = frame["volume"].astype("Int64")
    return frame


def _two_session_frame(series_id: str) -> pd.DataFrame:
    previous = _frame(series_id, close=100.0)
    previous.loc[:, "market_date"] = pd.Timestamp("2026-08-18")
    previous.loc[:, "bar_start"] = pd.Timestamp("2026-08-18T10:00:00Z")
    previous.loc[:, "bar_end"] = pd.Timestamp("2026-08-18T11:00:00Z")
    return pd.concat([previous, _frame(series_id, close=100.5)], ignore_index=True)


def test_global_60m_promotes_all_four_and_replays_exactly(tmp_path) -> None:
    calls = []

    def fetcher(series_id, **_kwargs):
        calls.append(series_id)
        return _frame(series_id)

    clock = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    first = run_global_market_60m(tmp_path, as_of=clock, fetcher=fetcher)
    second = run_global_market_60m(tmp_path, as_of=clock, fetcher=fetcher)
    stored = read_dataset(
        tmp_path / "data/normalized/market_price_60m_observation",
        MARKET_PRICE_60M_OBSERVATION, validate_market_price_60m,
    )
    assert first["status"] == second["status"] == "PASS"
    assert first["api_calls"] == second["api_calls"] == 4
    assert len(stored) == 4 and set(stored["symbol"]) == set(SERIES_IDS)
    assert calls == list(SERIES_IDS) * 2


def test_global_current_60m_projects_independently_without_history_write(tmp_path) -> None:
    clock = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)

    def fetcher(series_id, **_kwargs):
        if series_id == "UST10_FUTURES_60M":
            raise requests_exceptions.ConnectionError("sanitized")
        return _frame(series_id)

    report = run_global_market_current_60m(tmp_path, as_of=clock, fetcher=fetcher)

    assert report["status"] == "PARTIAL_FAILURE"
    assert report["api_calls"] == len(CURRENT_SERIES_IDS) and report["history_writes"] == 0
    assert not (tmp_path / "data/normalized/market_price_60m_observation").exists()
    assert not (
        tmp_path / "data/state/current_observations/global60m_current/ust10_futures_60m.json"
    ).exists()
    for series_id in set(CURRENT_SERIES_IDS) - {"UST10_FUTURES_60M"}:
        assert (
            tmp_path / "data/state/current_observations/global60m_current" / f"{series_id.lower()}.json"
        ).exists()


def test_global_current_60m_keeps_index_etf_and_future_identities_distinct_in_dashboard(
    tmp_path,
) -> None:
    clock = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    report = run_global_market_current_60m(
        tmp_path, as_of=clock, fetcher=lambda series_id, **_kwargs: _frame(series_id),
    )
    metrics = DashboardService(tmp_path).dashboard_metrics(now_utc=clock)

    assert report["status"] == "PASS" and report["api_calls"] == 13
    assert {
        key: (metrics[key].label, metrics[key].unit, metrics[key].route)
        for key in ("NQ_FUTURES", "NASDAQ", "SP500", "SOXX", "GOLD", "WTI", "BITCOIN")
    } == {
        "NQ_FUTURES": ("Nasdaq 100", "index points", "yahoo-global60m-current:CME:NQ=F"),
        "NASDAQ": ("Nasdaq", "index points", "yahoo-global60m-current:XNAS:IXIC"),
        "SP500": ("S&P 500", "index points", "yahoo-global60m-current:XNYS:GSPC"),
        "SOXX": ("SOXX", "USD per share", "yahoo-global60m-current:XNAS:SOXX"),
        "GOLD": ("GOLD", "provider native continuous futures price", "yahoo-global60m-current:COMEX:GC=F"),
        "WTI": ("WTI", "provider native continuous futures price", "yahoo-global60m-current:NYMEX:CL=F"),
        "BITCOIN": ("BITCOIN", "USD per BTC", "yahoo-global60m-current:CRYPTO:BTC-USD"),
    }
    assert all(
        metrics[key].freshness == "CURRENT_COMPLETED_60M" and metrics[key].displays_value
        for key in ("NQ_FUTURES", "NASDAQ", "SP500", "SOXX", "GOLD", "WTI", "BITCOIN")
    )
    assert not (tmp_path / "data/normalized/market_price_60m_observation").exists()


def test_global_current_60m_persists_exact_previous_provider_session_comparison(tmp_path) -> None:
    clock = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    run_global_market_current_60m(
        tmp_path, as_of=clock,
        fetcher=lambda series_id, **_kwargs: _two_session_frame(series_id),
    )
    metrics = DashboardService(tmp_path).dashboard_metrics(now_utc=clock)

    for key in ("USD_KRW_60M", "NQ_FUTURES", "NASDAQ", "SP500", "SOXX", "GOLD", "WTI", "BITCOIN"):
        assert metrics[key].change == pytest.approx(0.5)
        assert metrics[key].change_pct == pytest.approx(0.5)


def test_global_current_60m_persists_completed_session_traces_for_nine_cards(tmp_path) -> None:
    clock = datetime(2026, 8, 19, 17, tzinfo=timezone.utc)

    def trace_frame(series_id: str) -> pd.DataFrame:
        base = _frame(series_id, close=100.0)
        market = PROVIDERS[series_id][0]
        if market == "XKRX":
            start = pd.Timestamp("2026-08-19T00:00:00Z")
        elif market in {"XNYS", "XNAS"}:
            start = pd.Timestamp("2026-08-19T14:00:00Z")
        elif series_id in {"NQ_FUTURES_CURRENT_60M", "BITCOIN_CURRENT_60M"}:
            start = pd.Timestamp("2026-08-19T13:00:00Z")
        else:
            start = pd.Timestamp("2026-08-19T00:00:00Z")
        previous = base.iloc[0].copy()
        previous["bar_start"] = start - pd.Timedelta(days=1)
        previous["bar_end"] = previous["bar_start"] + pd.Timedelta(hours=1)
        previous["market_date"] = previous["bar_start"].tz_convert(
            previous["timezone"]
        ).date()
        previous["open"] = previous["high"] = previous["low"] = previous["close"] = 99.0
        rows = [previous]
        for offset, close in enumerate((100.0, 101.0, 100.5)):
            row = base.iloc[0].copy()
            row["bar_start"] = start + pd.Timedelta(hours=offset)
            row["bar_end"] = start + pd.Timedelta(hours=offset + 1)
            row["market_date"] = row["bar_start"].tz_convert(row["timezone"]).date()
            row["open"] = close
            row["high"] = close + 1
            row["low"] = close - 1
            row["close"] = close
            rows.append(row)
        frame = pd.DataFrame(rows, columns=MARKET_PRICE_60M_OBSERVATION.column_names)
        frame["retrieved_at"] = pd.Timestamp(clock)
        frame["volume"] = frame["volume"].astype("Int64")
        return frame

    report = run_global_market_current_60m(
        tmp_path, as_of=clock, fetcher=lambda series_id, **_kwargs: trace_frame(series_id),
    )

    assert report["status"] == "PASS"
    for series_id in (
        "USD_KRW_60M",
        "KOSPI_CURRENT_60M", "KOSDAQ_CURRENT_60M", "NQ_FUTURES_CURRENT_60M",
        "NASDAQ_CURRENT_60M", "SP500_CURRENT_60M", "SOXX_CURRENT_60M",
        "GOLD_CURRENT_60M", "WTI_CURRENT_60M", "BITCOIN_CURRENT_60M",
    ):
        payload = json.loads((
            tmp_path / "data/state/current_observations/global60m_current"
            / f"{series_id.lower()}.session.json"
        ).read_text(encoding="utf-8"))
        assert payload["interval"] == "60m"
        assert payload["completed_bars_only"] is True
        assert len(payload["points"]) >= 2

    service = DashboardService(tmp_path)
    metrics = service.dashboard_metrics(now_utc=pd.Timestamp(clock))
    views = service.current_session_card_sparklines(metrics)
    us_open = pd.Timestamp("2026-08-19T13:30:00Z")
    for asset in ("NQ_FUTURES", "BITCOIN"):
        assert asset in views
        assert pd.to_datetime(views[asset].frame["date"], utc=True).min() >= us_open
        assert views[asset].reference_value == pytest.approx(
            metrics[asset].value - metrics[asset].change
        )
    fx_metrics = service.dashboard_metrics(
        now_utc=pd.Timestamp("2026-08-19T03:05:00Z")
    )
    fx_views = service.current_session_card_sparklines(fx_metrics)
    assert "USD_KRW_60M" in fx_views
    fx_times = pd.to_datetime(fx_views["USD_KRW_60M"].frame["date"], utc=True)
    assert fx_times.min().tz_convert("Asia/Seoul").time() >= time(8, 0)
    assert fx_views["USD_KRW_60M"].reference_value == pytest.approx(
        fx_metrics["USD_KRW_60M"].value - fx_metrics["USD_KRW_60M"].change
    )


def test_global_60m_failure_keeps_previous_production(tmp_path) -> None:
    clock = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    run_global_market_60m(tmp_path, as_of=clock, fetcher=lambda series_id, **_: _frame(series_id))
    production = tmp_path / "data/normalized/market_price_60m_observation"
    prior_bytes = {
        path.relative_to(production): path.read_bytes()
        for path in production.rglob("*") if path.is_file()
    }
    calls = []

    def failing(series_id, **_kwargs):
        calls.append(series_id)
        if series_id == "UST10_FUTURES_60M":
            raise RuntimeError("provider unavailable")
        return _frame(series_id)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        run_global_market_60m(tmp_path, as_of=clock, fetcher=failing)
    stored = read_dataset(production, MARKET_PRICE_60M_OBSERVATION, validate_market_price_60m)
    assert len(stored) == 4
    assert calls == list(SERIES_IDS)
    assert {
        path.relative_to(production): path.read_bytes()
        for path in production.rglob("*") if path.is_file()
    } == prior_bytes


def test_global_60m_mixed_fetch_failures_are_sanitized_and_never_promote_partial(tmp_path) -> None:
    clock = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    run_global_market_60m(tmp_path, as_of=clock, fetcher=lambda series_id, **_: _frame(series_id))
    production = tmp_path / "data/normalized/market_price_60m_observation"
    prior_bytes = {
        path.relative_to(production): path.read_bytes()
        for path in production.rglob("*") if path.is_file()
    }
    calls = []
    response = Response()
    response.status_code = 503

    def mixed(series_id, **_kwargs):
        calls.append(series_id)
        if series_id == "UST2_FUTURES_60M":
            raise requests_exceptions.ConnectionError("cookie=do-not-retain")
        if series_id == "UST10_FUTURES_60M":
            raise requests_exceptions.HTTPError("opaque-url?token=do-not-retain", response=response)
        if series_id == "UST30_FUTURES_60M":
            raise RuntimeError("Yahoo global 60m response contains an error")
        return _frame(series_id)

    with pytest.raises(requests_exceptions.ConnectionError):
        run_global_market_60m(tmp_path, as_of=clock, fetcher=mixed)

    log = json.loads((
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_MARKET_60M_last.json"
    ).read_text(encoding="utf-8"))
    assert calls == list(SERIES_IDS)
    assert log["status"] == "FAIL" and log["api_calls"] == 4 and log["retry_count"] == 0
    assert log["series_terminal_outcomes"] == [
        {
            "series_id": "USD_KRW_60M",
            "outcome": "FETCH_ACCEPTED_FOR_ATOMIC_VALIDATION",
            "reason_code": "FETCH_RETURNED_FRAME",
        },
        {
            "series_id": "UST2_FUTURES_60M",
            "outcome": "TRANSPORT_FAILURE",
            "reason_code": "TRANSPORT_REQUEST_EXCEPTION",
        },
        {
            "series_id": "UST10_FUTURES_60M",
            "outcome": "HTTP_5XX",
            "reason_code": "HTTP_5XX_STATUS",
        },
        {
            "series_id": "UST30_FUTURES_60M",
            "outcome": "PROVIDER_CHART_ERROR",
            "reason_code": "PROVIDER_CHART_ERROR",
        },
    ]
    serialized = json.dumps(log, sort_keys=True)
    assert "cookie=" not in serialized and "token=" not in serialized and "opaque-url" not in serialized
    assert {
        path.relative_to(production): path.read_bytes()
        for path in production.rglob("*") if path.is_file()
    } == prior_bytes


def test_global_60m_batch_schema_failure_is_recorded_per_series(tmp_path) -> None:
    clock = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="60m schema invalid or empty"):
        run_global_market_60m(
            tmp_path,
            as_of=clock,
            fetcher=lambda _series_id, **_kwargs: pd.DataFrame(),
        )

    log = json.loads((
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_MARKET_60M_last.json"
    ).read_text(encoding="utf-8"))
    assert log["api_calls"] == 4
    assert log["series_terminal_outcomes"] == [
        {
            "series_id": series_id,
            "outcome": "SCHEMA_ERROR",
            "reason_code": "SCHEMA_VALIDATION",
        }
        for series_id in SERIES_IDS
    ]
    assert not (tmp_path / "data/normalized/market_price_60m_observation").exists()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("Yahoo returned empty global 60m data"), "EMPTY_PAYLOAD"),
        (RuntimeError("Yahoo global 60m result is missing"), "SCHEMA_ERROR"),
        (RuntimeError("Yahoo global 60m identity or granularity differs"), "SEMANTIC_FINALITY_REJECTION"),
        (ValueError("untrusted provider payload"), "SCHEMA_ERROR"),
    ],
)
def test_global_60m_terminal_outcome_categories_are_safe(error, expected) -> None:
    assert _terminal_outcome(error) == expected


def test_global_60m_overlap_reason_identifies_only_affected_series(tmp_path) -> None:
    clock = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    run_global_market_60m(tmp_path, as_of=clock, fetcher=lambda series_id, **_: _frame(series_id))

    with pytest.raises(RuntimeError, match="retained overlap differs"):
        run_global_market_60m(
            tmp_path,
            as_of=clock,
            fetcher=lambda series_id, **_: _frame(
                series_id,
                close=101.0 if series_id == "UST10_FUTURES_60M" else 100.5,
            ),
        )

    log = json.loads((
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_MARKET_60M_last.json"
    ).read_text(encoding="utf-8"))
    assert log["series_terminal_outcomes"] == [
        {
            "series_id": series_id,
            "outcome": "SEMANTIC_FINALITY_REJECTION",
            "reason_code": "SEMANTIC_RETAINED_OVERLAP_CONFLICT",
        }
        if series_id == "UST10_FUTURES_60M"
        else {
            "series_id": series_id,
            "outcome": "ATOMIC_BATCH_ABORTED",
            "reason_code": "BATCH_ABORTED_BY_OTHER_SERIES",
        }
        for series_id in SERIES_IDS
    ]
    assert "100.5" not in json.dumps(log, sort_keys=True)

    # The revised historical overlap remains rejected, while the independently
    # validated completed bar is still available to the current-only GUI gate.
    coverage = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-19T12:00:00+00:00",
    )
    assert coverage["UST10_FUTURES_60M"].displays_value
    assert coverage["UST10_FUTURES_60M"].value == pytest.approx(101.0)
    assert coverage["UST10_FUTURES_60M"].route == "yahoo-global60m-current:CBOT:ZN=F"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("Yahoo returned no finalized global 60m bars"), "EMPTY_NO_FINALIZED_BARS"),
        (RuntimeError("Yahoo global 60m instrument type differs"), "SEMANTIC_INSTRUMENT_TYPE"),
        (ValueError("untrusted provider payload"), "SCHEMA_VALIDATION"),
    ],
)
def test_global_60m_terminal_reason_codes_are_safe(error, expected) -> None:
    assert _terminal_reason_code(error) == expected


def test_global_60m_revision_is_rejected(tmp_path) -> None:
    clock = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
    run_global_market_60m(tmp_path, as_of=clock, fetcher=lambda series_id, **_: _frame(series_id))
    with pytest.raises(RuntimeError, match="retained overlap differs"):
        run_global_market_60m(
            tmp_path, as_of=clock,
            fetcher=lambda series_id, **_: _frame(series_id, close=101.0),
        )
