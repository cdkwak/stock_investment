from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Callable
from urllib.parse import quote
from uuid import uuid4

import numpy as np
import pandas as pd
import requests

from stock_data.orchestration.automatic_fallback import (
    FallbackInvariantError,
    RoutePolicy,
    SourceObservation,
    SourceProvenance,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)
from stock_data.orchestration.daily_operations import DailyRunLock
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.global_market_60m import (
    CURRENT_SERIES_IDS,
    CURRENT_UNITS,
    _write_current_comparison,
    _write_current_session_trace,
)
from stock_data.providers.public_http_capture import capture_public_response
from stock_data.providers.yahoo import GLOBAL_MARKET_60M_REGISTRY
from stock_data.providers.yahoo_15m import fetch_market_15m


NATIVE_15M_SERIES = ("^VIX", "^FVX", "^TNX", "^TYX")
FUTURES_30M_SERIES = frozenset({
    "UST2_FUTURES_60M", "UST10_FUTURES_60M", "UST30_FUTURES_60M",
    "NQ_FUTURES_CURRENT_60M", "GOLD_CURRENT_60M", "WTI_CURRENT_60M",
})
NATIVE_15M_UNITS = {
    "^VIX": "index points",
    "^FVX": "provider native quote index points",
    "^TNX": "provider native quote index points",
    "^TYX": "provider native quote index points",
}
_DEFAULT_LOOKBACK = timedelta(days=2)
_MAX_GLOBAL_CASH_LOOKBACK = timedelta(days=14)
_MAX_NATIVE_CASH_LOOKBACK = timedelta(days=8)
_GLOBAL_CASH_MARKETS = {
    "KOSPI_CURRENT_60M": ExchangeMarket.KR,
    "KOSDAQ_CURRENT_60M": ExchangeMarket.KR,
    "SP500_CURRENT_60M": ExchangeMarket.US,
    "NASDAQ_CURRENT_60M": ExchangeMarket.US,
    "SOXX_CURRENT_60M": ExchangeMarket.US,
}
_NATIVE_CASH_MARKETS = {series_id: ExchangeMarket.US for series_id in NATIVE_15M_SERIES}


class CompletedGridOHLCUnavailableError(RuntimeError):
    """The newest completed provider-grid row cannot form an OHLC bar."""


_FALLBACK_INVARIANT_REASON_CODES = {
    "automatic fallback requires retry_count=0": "RETRY_NONZERO",
    "accepted provider observation must use retry_count=0": "ACCEPTED_RETRY_NONZERO",
    "atomic promotion did not persist circuit state": "PROMOTION_STATE_NOT_PERSISTED",
    "atomic promotion rollback failed": "PROMOTION_ROLLBACK_FAILED",
    "primary adapter exceeded policy request budget": "PRIMARY_REQUEST_BUDGET_EXCEEDED",
    "fallback adapter exceeded policy request budget": "FALLBACK_REQUEST_BUDGET_EXCEEDED",
}


def _safe_failure_outcome(error: Exception) -> str:
    """Retain a bounded diagnostic code without serializing exception details."""

    if isinstance(error, FallbackInvariantError):
        reason = _FALLBACK_INVARIANT_REASON_CODES.get(
            str(error), "UNCLASSIFIED",
        )
        return f"FAIL_FALLBACK_INVARIANT_{reason}"
    return f"FAIL_{type(error).__name__.upper()}"


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def fetch_global_market_30m_current(
    series_id: str,
    *,
    start: datetime,
    end: datetime,
    session=requests,
    capture_root: Path | None = None,
    retrieved_at: datetime | None = None,
) -> pd.DataFrame:
    """Fetch completed Yahoo 30m bars for one current-display identity."""
    if series_id not in GLOBAL_MARKET_60M_REGISTRY:
        raise ValueError(f"unregistered Yahoo current identity: {series_id}")
    if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end)):
        raise ValueError("Yahoo current bounds must be timezone-aware")
    if start >= end or end - start > _MAX_GLOBAL_CASH_LOOKBACK:
        raise ValueError("Yahoo current bounds must be ordered and at most 14 days")
    observed_at = (retrieved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    spec = GLOBAL_MARKET_60M_REGISTRY[series_id]
    ticker = str(spec["provider_symbol"])
    params = {
        "period1": int(start.timestamp()),
        "period2": int(end.timestamp()),
        "interval": "30m",
        "events": "history",
        "includeAdjustedClose": "false",
        "includePrePost": "true",
    }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe='')}"
    response = session.get(
        url,
        params=params,
        headers={"User-Agent": "stock-investment-rev1/0.1"},
        timeout=30,
    )
    if capture_root is not None:
        capture_public_response(
            root=capture_root,
            provider="yahoo",
            operation="global_chart_30m_current",
            request_url=url,
            request_parameters={"series_id": series_id, **params},
            response=response,
        )
    response.raise_for_status()
    chart = response.json().get("chart")
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise RuntimeError("Yahoo current 30m response contains an error")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1:
        raise RuntimeError("Yahoo current 30m result is missing")
    item = results[0]
    meta = item.get("meta") or {}
    accepted_symbols = {ticker, "USDKRW=X"} if series_id == "USD_KRW_60M" else {ticker}
    if str(meta.get("symbol")) not in accepted_symbols or str(meta.get("dataGranularity")) != "30m":
        raise RuntimeError("Yahoo current 30m identity or granularity differs")
    if str(meta.get("instrumentType")) != str(spec["instrument_type"]):
        raise RuntimeError("Yahoo current 30m instrument type differs")
    timestamps = item.get("timestamp") or []
    quote_rows = ((item.get("indicators") or {}).get("quote") or [])
    if not timestamps or len(quote_rows) != 1:
        raise RuntimeError("Yahoo returned empty current 30m data")
    values = quote_rows[0]
    columns = ("open", "high", "low", "close")
    if any(len(values.get(column) or []) != len(timestamps) for column in columns):
        raise RuntimeError("Yahoo current 30m timestamp/value lengths differ")
    volumes = values.get("volume")
    if volumes is None:
        volumes = [None] * len(timestamps)
    if len(volumes) != len(timestamps):
        raise RuntimeError("Yahoo current 30m timestamp/value lengths differ")
    cutoff = pd.Timestamp(observed_at)
    provider_time = pd.to_datetime(meta.get("regularMarketTime"), unit="s", utc=True, errors="coerce")
    if not pd.isna(provider_time):
        cutoff = min(cutoff, provider_time)
    lower, upper = pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")
    rows: list[dict[str, object]] = []
    newest_completed_grid_start: pd.Timestamp | None = None
    newest_completed_grid_has_missing_ohlc = False
    for index, bar_start in enumerate(pd.to_datetime(timestamps, unit="s", utc=True)):
        bar_end = bar_start + timedelta(minutes=30)
        session_close = spec.get("regular_session_close")
        if session_close is not None:
            local_start = bar_start.tz_convert(str(spec["timezone"]))
            close_clock = datetime.strptime(str(session_close), "%H:%M").time()
            close_utc = pd.Timestamp(datetime.combine(local_start.date(), close_clock), tz=str(spec["timezone"])).tz_convert("UTC")
            if bar_start >= close_utc:
                continue
            bar_end = min(bar_end, close_utc)
        if bar_start < lower or bar_start >= upper or bar_end > cutoff:
            continue
        is_future = str(spec["instrument_type"]) == "FUTURE"
        is_provider_grid_row = bar_start == bar_start.floor("30min")
        if is_future and not is_provider_grid_row:
            # Yahoo can append a quote-time row. It is not a contracted 30m bar.
            continue
        prices = pd.to_numeric(pd.Series([values[column][index] for column in columns]), errors="coerce")
        has_missing_ohlc = prices.isna().any() or not np.isfinite(prices.to_numpy()).all()
        if is_future and (
            newest_completed_grid_start is None or bar_start > newest_completed_grid_start
        ):
            newest_completed_grid_start = bar_start
            newest_completed_grid_has_missing_ohlc = has_missing_ohlc
        if has_missing_ohlc:
            continue
        if prices.iloc[1] < prices.iloc[2] or not prices.iloc[0] >= prices.iloc[2] or not prices.iloc[0] <= prices.iloc[1] or not prices.iloc[3] >= prices.iloc[2] or not prices.iloc[3] <= prices.iloc[1]:
            continue
        rows.append({
            "market_date": bar_start.tz_convert(str(spec["timezone"])).date(),
            "market": spec["market"],
            "symbol": series_id,
            "asset_type": spec["asset_type"],
            "bar_start": bar_start,
            "bar_end": bar_end,
            "timezone": spec["timezone"],
            "session": "GLOBAL_CONTINUOUS",
            "interval": "30m",
            "actual_duration_minutes": int((bar_end - bar_start).total_seconds() // 60),
            **{column: values[column][index] for column in columns},
            "volume": volumes[index],
            "provider": "yahoo_chart_api",
            "provider_symbol": ticker,
            "retrieved_at": observed_at,
        })
    if newest_completed_grid_has_missing_ohlc:
        raise CompletedGridOHLCUnavailableError(
            "Yahoo newest completed 30m grid row has unavailable OHLC"
        )
    if not rows:
        raise RuntimeError("Yahoo returned no completed current 30m bars")
    return pd.DataFrame(rows)


def _fetch_start(series_id: str, clock: datetime, *, native: bool) -> datetime:
    """Include the last completed accepted cash session without guessing holidays."""
    market = (_NATIVE_CASH_MARKETS if native else _GLOBAL_CASH_MARKETS).get(series_id)
    if market is None:
        return clock - _DEFAULT_LOOKBACK
    calendar = ExchangeTradingCalendar(market)
    completed = calendar.latest_completed_session(clock)
    start = calendar.session_open(completed).astimezone(timezone.utc)
    maximum = _MAX_NATIVE_CASH_LOOKBACK if native else _MAX_GLOBAL_CASH_LOOKBACK
    elapsed = clock.astimezone(timezone.utc) - start
    if elapsed <= timedelta(0) or elapsed > maximum:
        raise RuntimeError("latest completed cash session exceeds the bounded Yahoo window")
    return start


def _current_route(
    *, market: str, provider_symbol: str, interval: ObservationInterval,
) -> CurrentObservationRoute:
    symbol = provider_symbol[1:] if provider_symbol.startswith("^") else provider_symbol
    route_id = f"yahoo-market-current:{market}:{symbol}"
    source_route = f"YAHOO_CHART_{interval.value.upper()}:{provider_symbol}"
    return CurrentObservationRoute(
        RoutePolicy(route_id, "YAHOO", source_route, "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE", False),
        ObservationIdentity("MARKET_PRICE_CURRENT", market, provider_symbol),
        (interval,),
    )


def _projection_disposition(
    root: Path,
    *,
    market: str,
    provider_symbol: str,
    value: float,
    bar_end: pd.Timestamp,
    clock: datetime,
    interval: ObservationInterval,
    output: Path,
) -> str:
    candidate_time = pd.Timestamp(bar_end)
    if (
        not np.isfinite(value) or value <= 0
        or candidate_time.tzinfo is None
        or candidate_time > pd.Timestamp(clock)
    ):
        raise ValueError("current completed-bar candidate is invalid")
    route = _current_route(
        market=market, provider_symbol=provider_symbol, interval=interval,
    )
    prior = CurrentObservationFileStore(root / output).select(route)
    if prior is None:
        if (root / output).exists():
            raise RuntimeError("Yahoo prior projection does not match the exact route")
        return "ACCEPT"
    candidate_time = candidate_time.tz_convert("UTC")
    prior_time = pd.Timestamp(prior.provider_timestamp_utc).tz_convert("UTC")
    if candidate_time < prior_time:
        return "PRIOR_VALUE_PRESERVED"
    if candidate_time > prior_time:
        return "ACCEPT"
    if value != prior.value:
        return "REVISION_PRIOR_PRESERVED"
    return "UNCHANGED_PRESERVED"


def _project(
    root: Path,
    *,
    series_id: str,
    market: str,
    provider_symbol: str,
    value: float,
    bar_end: pd.Timestamp,
    clock: datetime,
    interval: ObservationInterval,
    unit: str,
    output: Path,
) -> None:
    if not np.isfinite(value) or value <= 0 or bar_end.tzinfo is None or bar_end > pd.Timestamp(clock):
        raise ValueError("current completed-bar projection is invalid")
    route = _current_route(
        market=market, provider_symbol=provider_symbol, interval=interval,
    )
    route_id = route.fallback_policy.route_id
    source_route = route.fallback_policy.primary_route
    identity = route.identity
    observation = CurrentObservation(
        route_id, identity, interval, value, unit, "YAHOO", "YAHOO_CHART_API",
        source_route, bar_end.tz_convert("UTC").isoformat(),
        clock.astimezone(timezone.utc).isoformat(), ObservationFinality.AS_RETRIEVED,
    )
    source = SourceObservation(observation, SourceProvenance(
        "YAHOO", "YAHOO_CHART_API", source_route,
        observation.retrieved_at_utc, 1,
    ))
    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / output))
    refreshed = coordinator.refresh(
        route,
        primary_attempt=lambda: source,
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")),
    )
    if refreshed.observation != observation or coordinator.replay(route).observation != observation:
        raise RuntimeError("current projection readback mismatch")


def run_yahoo_market_current(
    project_root: Path,
    *,
    as_of: datetime | None = None,
    global_fetcher: Callable[..., pd.DataFrame] = fetch_global_market_30m_current,
    native_fetcher: Callable[..., pd.DataFrame] = fetch_market_15m,
) -> dict[str, object]:
    """Run both Yahoo current lanes under one lock; failures remain per identity."""
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    root = Path(project_root).resolve()
    run_id = f"yahoo-market-current-{clock.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
    landing = root / "data/landing/yahoo_market_current" / run_id
    outcomes: list[dict[str, str]] = []
    with DailyRunLock(root / "data/state/provider_scheduler/yahoo_market_current.lock", run_id=run_id, acquired_at=clock):
        for series_id in CURRENT_SERIES_IDS:
            spec = GLOBAL_MARKET_60M_REGISTRY[series_id]
            output = Path("data/state/current_observations/global60m_current") / f"{series_id.lower()}.json"
            try:
                frame = global_fetcher(
                    series_id, start=_fetch_start(series_id, clock, native=False), end=clock,
                    capture_root=landing / "global_30m", retrieved_at=clock,
                ).sort_values("bar_end", kind="stable")
                row = frame.iloc[-1]
                disposition = _projection_disposition(
                    root, market=str(row["market"]), provider_symbol=str(row["provider_symbol"]),
                    value=float(row["close"]), bar_end=pd.Timestamp(row["bar_end"]),
                    clock=clock,
                    interval=ObservationInterval.MINUTES_30, output=output,
                )
                if disposition == "ACCEPT":
                    _project(
                        root, series_id=series_id, market=str(row["market"]),
                        provider_symbol=str(row["provider_symbol"]), value=float(row["close"]),
                        bar_end=pd.Timestamp(row["bar_end"]), clock=clock,
                        interval=ObservationInterval.MINUTES_30, unit=CURRENT_UNITS[series_id],
                        output=output,
                    )
                if disposition not in {
                    "PRIOR_VALUE_PRESERVED", "REVISION_PRIOR_PRESERVED",
                }:
                    _write_current_comparison(root, series_id, frame)
                    _write_current_session_trace(root, series_id, frame)
                outcome = {
                    "ACCEPT": "CURRENT_30M_ACCEPTED",
                    "UNCHANGED_PRESERVED": "NO_NEW_30M_BAR_PRESERVED",
                    "PRIOR_VALUE_PRESERVED": "OLDER_30M_BAR_PRIOR_VALUE_PRESERVED",
                    "REVISION_PRIOR_PRESERVED": "REVISED_30M_BAR_PRIOR_VALUE_PRESERVED",
                }[disposition]
            except CompletedGridOHLCUnavailableError:
                if series_id not in FUTURES_30M_SERIES:
                    outcome = "FAIL_COMPLETED_GRID_OHLC_UNAVAILABLE"
                else:
                    try:
                        route = _current_route(
                            market=str(spec["market"]),
                            provider_symbol=str(spec["provider_symbol"]),
                            interval=ObservationInterval.MINUTES_30,
                        )
                        prior = CurrentObservationFileStore(root / output).select(route)
                        if prior is None:
                            if (root / output).exists():
                                raise RuntimeError(
                                    "Yahoo prior projection does not match the exact route"
                                )
                            outcome = "FAIL_COMPLETED_GRID_OHLC_UNAVAILABLE"
                        else:
                            outcome = (
                                "FAIL_COMPLETED_GRID_OHLC_UNAVAILABLE_"
                                "PRIOR_VALUE_PRESERVED"
                            )
                    except Exception as error:
                        outcome = _safe_failure_outcome(error)
            except Exception as error:
                outcome = _safe_failure_outcome(error)
            outcomes.append({"series_id": series_id, "lane": "GLOBAL_30M", "outcome": outcome})
        for series_id in NATIVE_15M_SERIES:
            try:
                frame = native_fetcher(
                    series_id, start=_fetch_start(series_id, clock, native=True), end=clock,
                    capture_root=landing / "native_15m", retrieved_at=clock,
                ).sort_values("bar_end", kind="stable")
                row = frame.iloc[-1]
                output = Path("data/state/current_observations/yahoo_native15m_current") / f"{series_id.replace('^', 'idx').lower()}.json"
                disposition = _projection_disposition(
                    root, market=str(row["market"]), provider_symbol=str(row["provider_symbol"]),
                    value=float(row["close"]), bar_end=pd.Timestamp(row["bar_end"]),
                    clock=clock,
                    interval=ObservationInterval.MINUTES_15, output=output,
                )
                if disposition == "ACCEPT":
                    _project(
                        root, series_id=series_id, market=str(row["market"]),
                        provider_symbol=str(row["provider_symbol"]), value=float(row["close"]),
                        bar_end=pd.Timestamp(row["bar_end"]), clock=clock,
                        interval=ObservationInterval.MINUTES_15, unit=NATIVE_15M_UNITS[series_id],
                        output=output,
                    )
                outcome = {
                    "ACCEPT": "CURRENT_15M_ACCEPTED",
                    "UNCHANGED_PRESERVED": "NO_NEW_15M_BAR_PRESERVED",
                    "PRIOR_VALUE_PRESERVED": "OLDER_15M_BAR_PRIOR_VALUE_PRESERVED",
                    "REVISION_PRIOR_PRESERVED": "REVISED_15M_BAR_PRIOR_VALUE_PRESERVED",
                }[disposition]
            except Exception as error:
                outcome = _safe_failure_outcome(error)
            outcomes.append({"series_id": series_id, "lane": "NATIVE_15M", "outcome": outcome})
    passed = sum(
        not row["outcome"].startswith("FAIL_")
        and row["outcome"].endswith(("ACCEPTED", "PRESERVED"))
        for row in outcomes
    )
    preserved = sum(row["outcome"].endswith("PRESERVED") for row in outcomes)
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "PASS" if passed == len(outcomes) else "PARTIAL_FAILURE",
        "schedule_interval": "30m",
        "global_bar_interval": "30m",
        "native_bar_interval": "15m",
        "api_calls": len(outcomes),
        "max_api_calls": len(outcomes),
        "retry_count": 0,
        "history_writes": 0,
        "accepted": passed,
        "preserved": preserved,
        "failed": len(outcomes) - passed,
        "series_terminal_outcomes": outcomes,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(root / "artifacts/scheduler_logs/STOCK_DATA_YAHOO_MARKET_30M_last.json", report)
    return report


__all__ = [
    "CompletedGridOHLCUnavailableError",
    "FUTURES_30M_SERIES",
    "NATIVE_15M_SERIES",
    "fetch_global_market_30m_current",
    "run_yahoo_market_current",
]
