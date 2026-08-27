from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Callable
from uuid import uuid4

import pandas as pd
from requests import exceptions as requests_exceptions

from stock_data.contracts.market_60m import MARKET_PRICE_60M_OBSERVATION
from stock_data.orchestration.daily_operations import DailyRunLock
from stock_data.providers.yahoo import fetch_global_market_60m
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.market_60m import validate_market_price_60m
from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)


SERIES_IDS = (
    "USD_KRW_60M", "UST2_FUTURES_60M", "UST10_FUTURES_60M", "UST30_FUTURES_60M",
)
CURRENT_SERIES_IDS = SERIES_IDS + (
    "KOSPI_CURRENT_60M", "KOSDAQ_CURRENT_60M",
    "SP500_CURRENT_60M", "NASDAQ_CURRENT_60M", "NQ_FUTURES_CURRENT_60M",
    "SOXX_CURRENT_60M", "GOLD_CURRENT_60M", "WTI_CURRENT_60M",
    "BITCOIN_CURRENT_60M",
)
_VALUE_COLUMNS = ("open", "high", "low", "close", "volume")
CURRENT_UNITS = {
    "KOSPI_CURRENT_60M": "index points",
    "KOSDAQ_CURRENT_60M": "index points",
    "USD_KRW_60M": "KRW per USD",
    "UST2_FUTURES_60M": "provider native continuous futures price",
    "UST10_FUTURES_60M": "provider native continuous futures price",
    "UST30_FUTURES_60M": "provider native continuous futures price",
    "SP500_CURRENT_60M": "index points",
    "NASDAQ_CURRENT_60M": "index points",
    "NQ_FUTURES_CURRENT_60M": "index points",
    "SOXX_CURRENT_60M": "USD per share",
    "GOLD_CURRENT_60M": "provider native continuous futures price",
    "WTI_CURRENT_60M": "provider native continuous futures price",
    "BITCOIN_CURRENT_60M": "USD per BTC",
}

_SESSION_TRACE_RULES = {
    "USD_KRW_60M": ("KST_DAY_0800", "08:00", None),
    "KOSPI_CURRENT_60M": ("CASH_REGULAR", "09:00", "15:30"),
    "KOSDAQ_CURRENT_60M": ("CASH_REGULAR", "09:00", "15:30"),
    "SP500_CURRENT_60M": ("CASH_REGULAR", "09:30", "16:00"),
    "NASDAQ_CURRENT_60M": ("CASH_REGULAR", "09:30", "16:00"),
    "SOXX_CURRENT_60M": ("CASH_REGULAR", "09:30", "16:00"),
    "NQ_FUTURES_CURRENT_60M": ("FUTURES_PROVIDER_SESSION", "18:00", None),
    "GOLD_CURRENT_60M": ("FUTURES_PROVIDER_SESSION", "18:00", None),
    "WTI_CURRENT_60M": ("FUTURES_PROVIDER_SESSION", "18:00", None),
    "BITCOIN_CURRENT_60M": ("UTC_DAY", "00:00", None),
}


def _write_current_session_trace(root: Path, series_id: str, frame: pd.DataFrame) -> None:
    """Persist completed bars from the latest reviewed display session only."""
    if series_id not in _SESSION_TRACE_RULES:
        return
    ordered = frame.sort_values("bar_end", kind="stable").copy()
    rule, start_text, end_text = _SESSION_TRACE_RULES[series_id]
    zone = str(ordered.iloc[-1]["timezone"])
    local_starts = pd.to_datetime(ordered["bar_start"], utc=True).dt.tz_convert(zone)
    start_clock = pd.Timestamp(start_text).time()
    if rule == "FUTURES_PROVIDER_SESSION":
        session_dates = pd.Series(
            [value.date() + timedelta(days=1) if value.time() >= start_clock else value.date()
             for value in local_starts],
            index=ordered.index,
        )
        eligible = pd.Series(True, index=ordered.index)
    elif rule == "KST_DAY_0800":
        session_dates = pd.Series(
            [value.date() if value.time() >= start_clock else value.date() - timedelta(days=1)
             for value in local_starts],
            index=ordered.index,
        )
        eligible = pd.Series(True, index=ordered.index)
    else:
        session_dates = pd.Series([value.date() for value in local_starts], index=ordered.index)
        eligible = local_starts.dt.time >= start_clock
        if end_text is not None:
            eligible &= local_starts.dt.time < pd.Timestamp(end_text).time()
    if not eligible.any():
        return
    latest_session = max(session_dates.loc[eligible])
    selected = ordered.loc[eligible & session_dates.eq(latest_session)].copy()
    if len(selected) < 2:
        return
    values = pd.to_numeric(selected["close"], errors="coerce")
    if values.isna().any() or not (values > 0).all():
        return
    points = [
        {
            "bar_end_utc": pd.Timestamp(row.bar_end).tz_convert("UTC").isoformat(),
            "value": float(row.close),
        }
        for row in selected.itertuples(index=False)
    ]
    payload = {
        "schema_version": 1,
        "series_id": series_id,
        "provider_symbol": str(selected.iloc[-1]["provider_symbol"]),
        "interval": str(selected.iloc[-1]["interval"]),
        "session_date": latest_session.isoformat(),
        "session_semantics": rule,
        "session_start_local": start_text,
        "session_end_local": end_text,
        "source_timezone": zone,
        "completed_bars_only": True,
        "points": points,
    }
    _atomic_json(
        root / "data/state/current_observations/global60m_current"
        / f"{series_id.lower()}.session.json",
        payload,
    )


def _write_current_comparison(root: Path, series_id: str, frame: pd.DataFrame) -> None:
    """Persist a strict previous provider-session close for display comparison."""
    ordered = frame.sort_values("bar_end", kind="stable").copy()
    dates = tuple(dict.fromkeys(ordered["market_date"].astype(str)))
    if len(dates) < 2:
        return
    current_date, previous_date = dates[-1], dates[-2]
    current = ordered.loc[ordered["market_date"].astype(str).eq(current_date)].iloc[-1]
    previous = ordered.loc[ordered["market_date"].astype(str).eq(previous_date)].iloc[-1]
    current_close = float(current["close"])
    previous_close = float(previous["close"])
    if not all(pd.notna(value) and value > 0 for value in (current_close, previous_close)):
        return
    change = current_close - previous_close
    payload = {
        "schema_version": 1,
        "series_id": series_id,
        "provider_symbol": str(current["provider_symbol"]),
        "current_bar_end_utc": pd.Timestamp(current["bar_end"]).tz_convert("UTC").isoformat(),
        "current_close": current_close,
        "previous_session_date": previous_date,
        "previous_session_close": previous_close,
        "change": change,
        "change_pct": change / previous_close * 100.0,
        "basis": "PREVIOUS_PROVIDER_SESSION_CLOSE",
    }
    _atomic_json(
        root / "data/state/current_observations/global60m_current"
        / f"{series_id.lower()}.comparison.json",
        payload,
    )


def _current_route_symbol(provider_symbol: str) -> str:
    """Keep the exact symbol in source_route while making route_id contract-safe."""
    return provider_symbol[1:] if provider_symbol.startswith("^") else provider_symbol


def _project_current_frame(root: Path, series_id: str, frame: pd.DataFrame, clock: datetime) -> None:
    """Persist the latest completed bar independently of historical promotion."""
    validate_market_price_60m(frame)
    ordered = frame.sort_values("bar_end", kind="stable")
    row = ordered.iloc[-1]
    value = float(row["close"])
    if not pd.notna(value) or value <= 0:
        raise GlobalMarket60mError("current close is invalid", reason_code="CURRENT_CLOSE_INVALID")
    provider_timestamp = pd.Timestamp(row["bar_end"])
    if provider_timestamp.tzinfo is None or provider_timestamp > pd.Timestamp(clock):
        raise GlobalMarket60mError("current bar timestamp is invalid", reason_code="CURRENT_TIMESTAMP_INVALID")
    market = str(row["market"])
    provider_symbol = str(row["provider_symbol"])
    route_id = f"yahoo-global60m-current:{market}:{_current_route_symbol(provider_symbol)}"
    source_route = f"YAHOO_CHART_GLOBAL60M:{provider_symbol}"
    observation = CurrentObservation(
        route_id=route_id,
        identity=ObservationIdentity("MARKET_PRICE_60M_CURRENT", market, provider_symbol),
        interval=ObservationInterval.MINUTES_60,
        value=value,
        unit=CURRENT_UNITS[series_id],
        provider="YAHOO",
        upstream_provider="YAHOO_CHART_API",
        source_route=source_route,
        provider_timestamp_utc=provider_timestamp.tz_convert("UTC").isoformat(),
        retrieved_at_utc=clock.astimezone(timezone.utc).isoformat(),
        finality=ObservationFinality.AS_RETRIEVED,
    )
    source = SourceObservation(
        observation,
        SourceProvenance(
            provider=observation.provider,
            upstream_provider=observation.upstream_provider,
            source_route=source_route,
            retrieved_at_utc=observation.retrieved_at_utc,
            request_count=1,
        ),
    )
    route = CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=route_id,
            primary_provider=observation.provider,
            primary_route=source_route,
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=observation.identity,
        interval_precedence=(ObservationInterval.MINUTES_60,),
    )
    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(
        root / "data/state/current_observations/global60m_current" / f"{series_id.lower()}.json"
    ))
    refreshed = coordinator.refresh(
        route, primary_attempt=lambda: source,
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")),
    )
    replay = coordinator.replay(route)
    if refreshed.observation != observation or replay.observation != observation or replay.api_calls != 0:
        raise GlobalMarket60mError("current projection readback mismatch", reason_code="CURRENT_READBACK_MISMATCH")
    _write_current_comparison(root, series_id, ordered)
    _write_current_session_trace(root, series_id, ordered)


class GlobalMarket60mError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "SEMANTIC_FINALITY_UNSPECIFIED",
        series_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.series_ids = series_ids


def _terminal_outcome(error: Exception) -> str:
    """Return a safe failure class without serializing provider error content."""
    if isinstance(error, requests_exceptions.HTTPError):
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if isinstance(status_code, int) and 400 <= status_code <= 499:
            return "HTTP_4XX"
        if isinstance(status_code, int) and 500 <= status_code <= 599:
            return "HTTP_5XX"
        return "HTTP_OTHER_STATUS"
    if isinstance(error, requests_exceptions.RequestException):
        return "TRANSPORT_FAILURE"
    if isinstance(error, ValueError):
        return "SCHEMA_ERROR"

    # The Yahoo adapter exposes these fixed local messages.  They are used only
    # for a safe category; the source message is never retained in telemetry.
    message = str(error)
    if message == "Yahoo global 60m response contains an error":
        return "PROVIDER_CHART_ERROR"
    if message in {
        "Yahoo returned empty global 60m data",
        "Yahoo returned no finalized global 60m bars",
    }:
        return "EMPTY_PAYLOAD"
    if message in {
        "Yahoo global 60m result is missing",
        "Yahoo global 60m timestamp/value lengths differ",
        "60m schema invalid or empty",
    }:
        return "SCHEMA_ERROR"
    if isinstance(error, GlobalMarket60mError) or message in {
        "Yahoo global 60m identity or granularity differs",
        "Yahoo global 60m instrument type differs",
    }:
        return "SEMANTIC_FINALITY_REJECTION"
    return "UNCLASSIFIED_FAILURE"


def _terminal_reason_code(error: Exception) -> str:
    """Return a bounded diagnostic code without retaining source details."""
    if isinstance(error, GlobalMarket60mError):
        return error.reason_code
    if isinstance(error, requests_exceptions.HTTPError):
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if isinstance(status_code, int) and 400 <= status_code <= 499:
            return "HTTP_4XX_STATUS"
        if isinstance(status_code, int) and 500 <= status_code <= 599:
            return "HTTP_5XX_STATUS"
        return "HTTP_OTHER_STATUS"
    if isinstance(error, requests_exceptions.RequestException):
        return "TRANSPORT_REQUEST_EXCEPTION"
    if isinstance(error, ValueError):
        return "SCHEMA_VALIDATION"

    message = str(error)
    if message == "Yahoo global 60m response contains an error":
        return "PROVIDER_CHART_ERROR"
    if message in {
        "Yahoo returned empty global 60m data",
        "Yahoo returned no finalized global 60m bars",
    }:
        return "EMPTY_NO_FINALIZED_BARS"
    if message in {
        "Yahoo global 60m result is missing",
        "Yahoo global 60m timestamp/value lengths differ",
        "60m schema invalid or empty",
    }:
        return "SCHEMA_PAYLOAD_SHAPE"
    if message == "Yahoo global 60m identity or granularity differs":
        return "SEMANTIC_IDENTITY_OR_GRANULARITY"
    if message == "Yahoo global 60m instrument type differs":
        return "SEMANTIC_INSTRUMENT_TYPE"
    return "UNCLASSIFIED"


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _merge_exact(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        result = incoming.copy()
    else:
        keys = list(MARKET_PRICE_60M_OBSERVATION.primary_key)
        overlap = existing.merge(incoming, on=keys, how="inner", suffixes=("_old", "_new"))
        for column in _VALUE_COLUMNS:
            old = pd.to_numeric(overlap[f"{column}_old"], errors="coerce")
            new = pd.to_numeric(overlap[f"{column}_new"], errors="coerce")
            equal = old.eq(new) | (old.isna() & new.isna())
            if not equal.all():
                affected = tuple(sorted(set(overlap.loc[~equal, "symbol"].astype(str))))
                raise GlobalMarket60mError(
                    f"retained overlap differs: {column}",
                    reason_code="SEMANTIC_RETAINED_OVERLAP_CONFLICT",
                    series_ids=affected,
                )
        result = pd.concat([existing, incoming], ignore_index=True)
        result = result.drop_duplicates(keys, keep="first")
    result = result.sort_values(
        list(MARKET_PRICE_60M_OBSERVATION.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_market_price_60m(result)
    return result


def run_global_market_60m(
    project_root: Path,
    *,
    as_of: datetime | None = None,
    lookback_days: int = 7,
    fetcher: Callable[..., pd.DataFrame] = fetch_global_market_60m,
) -> dict[str, object]:
    """Refresh the four reviewed delayed global 60m series with four calls, retry zero."""
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not 1 <= lookback_days <= 9:
        raise ValueError("lookback_days must be between 1 and 9")
    root = Path(project_root).resolve()
    run_id = f"global60m-{clock.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
    landing = root / "data/landing/global_market_60m" / run_id
    production = root / "data/normalized/market_price_60m_observation"
    staging = root / "data/staging/global_market_60m" / run_id / "candidate"
    started = clock.astimezone(timezone.utc)
    base = {
        "schema_version": 1, "run_id": run_id, "dataset_id": MARKET_PRICE_60M_OBSERVATION.name,
        "series_ids": list(SERIES_IDS), "source": "Yahoo delayed chart API",
        "semantics": "FX indicative rate and continuous Treasury futures prices; not Treasury yields",
        "retry_count": 0, "max_api_calls": len(SERIES_IDS),
        "started_at_utc": started.isoformat(),
    }
    log_path = root / "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_MARKET_60M_last.json"
    lock_path = root / "data/state/provider_scheduler/global_market_60m.lock"
    calls = 0
    series_terminal_outcomes: list[dict[str, str]] = []
    fetch_failure = False
    try:
        with DailyRunLock(lock_path, run_id=run_id, acquired_at=clock):
            frames = []
            fetch_errors: list[Exception] = []
            for series_id in SERIES_IDS:
                calls += 1
                try:
                    frame = fetcher(
                        series_id,
                        start=clock - timedelta(days=lookback_days), end=clock,
                        capture_root=landing, retrieved_at=clock,
                    )
                except Exception as error:
                    fetch_errors.append(error)
                    series_terminal_outcomes.append({
                        "series_id": series_id,
                        "outcome": _terminal_outcome(error),
                        "reason_code": _terminal_reason_code(error),
                    })
                    continue
                try:
                    validate_market_price_60m(frame)
                    _project_current_frame(root, series_id, frame, clock)
                except Exception as error:
                    fetch_errors.append(error)
                    series_terminal_outcomes.append({
                        "series_id": series_id,
                        "outcome": _terminal_outcome(error),
                        "reason_code": _terminal_reason_code(error),
                    })
                    continue
                frames.append(frame)
                series_terminal_outcomes.append({
                    "series_id": series_id,
                    "outcome": "FETCH_ACCEPTED_FOR_ATOMIC_VALIDATION",
                    "reason_code": "FETCH_RETURNED_FRAME",
                })
            if fetch_errors:
                fetch_failure = True
                raise fetch_errors[0]
            incoming = pd.concat(frames, ignore_index=True)
            validate_market_price_60m(incoming)
            try:
                existing = read_dataset(
                    production, MARKET_PRICE_60M_OBSERVATION, validate_market_price_60m,
                )
            except FileNotFoundError:
                existing = pd.DataFrame(columns=MARKET_PRICE_60M_OBSERVATION.column_names)
            candidate = _merge_exact(existing, incoming)
            write_dataset_atomic(
                candidate, staging, MARKET_PRICE_60M_OBSERVATION, validate_market_price_60m,
            )
            verified = read_dataset(staging, MARKET_PRICE_60M_OBSERVATION, validate_market_price_60m)
            if len(verified) != len(candidate):
                raise GlobalMarket60mError(
                    "staged row count differs",
                    reason_code="STAGING_READBACK_COUNT_MISMATCH",
                )
            write_dataset_atomic(
                verified, production, MARKET_PRICE_60M_OBSERVATION, validate_market_price_60m,
            )
        latest = {
            series_id: pd.to_datetime(
                candidate.loc[candidate["symbol"].eq(series_id), "bar_end"], utc=True,
            ).max().isoformat()
            for series_id in SERIES_IDS
        }
        report = {
            **base, "status": "PASS", "api_calls": calls,
            "incoming_rows": len(incoming), "retained_rows_before": len(existing),
            "retained_rows_after": len(candidate), "latest_bar_end_utc": latest,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_json(root / "data/state/global_market_60m.json", report)
        _atomic_json(log_path, report)
        return report
    except Exception as error:
        if series_terminal_outcomes and not fetch_failure:
            batch_outcome = _terminal_outcome(error)
            batch_reason = _terminal_reason_code(error)
            affected = set(getattr(error, "series_ids", ()))
            series_terminal_outcomes = [
                (
                    {
                        "series_id": outcome["series_id"],
                        "outcome": batch_outcome,
                        "reason_code": batch_reason,
                    }
                    if not affected or outcome["series_id"] in affected
                    else {
                        "series_id": outcome["series_id"],
                        "outcome": "ATOMIC_BATCH_ABORTED",
                        "reason_code": "BATCH_ABORTED_BY_OTHER_SERIES",
                    }
                )
                for outcome in series_terminal_outcomes
            ]
        failure = {
            **base, "status": "FAIL", "api_calls": calls,
            "error_type": type(error).__name__,
            "series_terminal_outcomes": series_terminal_outcomes,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_json(log_path, failure)
        raise


def run_global_market_current_60m(
    project_root: Path,
    *,
    as_of: datetime | None = None,
    lookback_days: int = 2,
    fetcher: Callable[..., pd.DataFrame] = fetch_global_market_60m,
) -> dict[str, object]:
    """Refresh only independent display projections; never write historical data."""
    clock = as_of or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not 1 <= lookback_days <= 3:
        raise ValueError("current lookback_days must be between 1 and 3")
    root = Path(project_root).resolve()
    run_id = f"global60m-current-{clock.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
    landing = root / "data/landing/global_market_60m_current" / run_id
    log_path = root / "artifacts/scheduler_logs/STOCK_DATA_GLOBAL_MARKET_CURRENT_60M_last.json"
    lock_path = root / "data/state/provider_scheduler/global_market_current_60m.lock"
    outcomes: list[dict[str, str]] = []
    calls = 0
    with DailyRunLock(lock_path, run_id=run_id, acquired_at=clock):
        for series_id in CURRENT_SERIES_IDS:
            calls += 1
            try:
                frame = fetcher(
                    series_id,
                    start=clock - timedelta(days=lookback_days),
                    end=clock,
                    capture_root=landing,
                    retrieved_at=clock,
                )
                _project_current_frame(root, series_id, frame, clock)
            except Exception as error:
                outcomes.append({
                    "series_id": series_id,
                    "outcome": _terminal_outcome(error),
                    "reason_code": _terminal_reason_code(error),
                })
                continue
            outcomes.append({
                "series_id": series_id,
                "outcome": "CURRENT_PROJECTION_ACCEPTED",
                "reason_code": "COMPLETED_BAR_ATOMIC_READBACK",
            })
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "PASS" if all(row["outcome"] == "CURRENT_PROJECTION_ACCEPTED" for row in outcomes) else "PARTIAL_FAILURE",
        "api_calls": calls,
        "max_api_calls": len(CURRENT_SERIES_IDS),
        "retry_count": 0,
        "history_writes": 0,
        "series_terminal_outcomes": outcomes,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(log_path, report)
    return report


__all__ = [
    "CURRENT_SERIES_IDS", "GlobalMarket60mError", "SERIES_IDS", "run_global_market_60m",
    "run_global_market_current_60m",
]
