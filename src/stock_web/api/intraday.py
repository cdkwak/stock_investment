"""Provider-free intraday sparklines from retained current-observation state."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from stock_data.gui.services import (
    _load_yahoo_native15m_current,
    load_global60m_ur232_current_observations,
    load_toss_domestic_ur246_current_observation,
)


KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
_MIN_POINTS = 3


@dataclass(frozen=True)
class _YahooSpec:
    coverage_id: str
    series_id: str
    provider_symbol: str
    file_stem: str
    source: str
    session: str
    window_prefix: str
    disclaimer: str | None = None


_GLOBAL_SPECS = {
    "NASDAQ 100 선물": _YahooSpec(
        "NQ_FUTURES_CURRENT_60M", "NQ_FUTURES_CURRENT_60M", "NQ=F",
        "nq_futures_current_60m", "Yahoo 완료 30분봉 · NQ=F", "futures", "24h 선물",
    ),
    "S&P 500 선물": _YahooSpec(
        "SP500_FUTURES_CURRENT_60M", "SP500_FUTURES_CURRENT_60M", "ES=F",
        "sp500_futures_current_60m", "Yahoo 완료 30분봉 · ES=F", "futures", "24h 선물",
    ),
    "다우 선물": _YahooSpec(
        "DOW_FUTURES_CURRENT_60M", "DOW_FUTURES_CURRENT_60M", "YM=F",
        "dow_futures_current_60m", "Yahoo 완료 30분봉 · YM=F", "futures", "24h 선물",
    ),
    "필라델피아 반도체": _YahooSpec(
        "SOX_CURRENT_60M", "SOX_CURRENT_60M", "^SOX",
        "sox_current_60m", "Yahoo 완료 30분봉 · ^SOX", "us_cash", "최근 24h",
    ),
    "달러 인덱스": _YahooSpec(
        "DOLLAR_INDEX_CURRENT_60M", "DOLLAR_INDEX_CURRENT_60M", "DX-Y.NYB",
        "dollar_index_current_60m", "Yahoo 완료 30분봉 · DX-Y.NYB", "futures", "최근 24h",
    ),
    "WTI": _YahooSpec(
        "WTI_CURRENT_60M", "WTI_CURRENT_60M", "CL=F",
        "wti_current_60m", "Yahoo 완료 30분봉 · CL=F", "futures", "24h 선물",
    ),
    "WTI 선물": _YahooSpec(
        "WTI_CURRENT_60M", "WTI_CURRENT_60M", "CL=F",
        "wti_current_60m", "Yahoo 완료 30분봉 · CL=F", "futures", "24h 선물",
    ),
    "USD/KRW": _YahooSpec(
        "USD_KRW_60M", "USD_KRW_60M", "KRW=X",
        "usd_krw_60m", "Yahoo 완료 30분봉 · KRW=X", "futures", "최근 24h",
    ),
}

_NATIVE_SPECS = {
    "VIX": _YahooSpec(
        "^VIX", "^VIX", "^VIX", "idxvix", "Yahoo 완료 15분봉 · ^VIX",
        "us_cash", "최근 24h",
    ),
    "VIX (FRED 마감)": _YahooSpec(
        "^VIX", "^VIX", "^VIX", "idxvix", "Yahoo 완료 15분봉 · ^VIX",
        "us_cash", "최근 24h",
    ),
    "미국 10Y": _YahooSpec(
        "^TNX", "^TNX", "^TNX", "idxtnx",
        "Yahoo 완료 15분봉 · ^TNX 지수, 공식 수익률 아님", "us_cash", "최근 24h",
        "^TNX 지수, 공식 수익률 아님",
    ),
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _aware(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _iso_kst(value: datetime) -> str:
    return value.astimezone(KST).isoformat(timespec="seconds")


def _dedupe(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    return sorted({stamp.astimezone(timezone.utc): value for stamp, value in points}.items())


def _current_store_points(
    path: Path, *, route_id: str, dataset_id: str, market: str, symbol: str,
    interval: str, provider: str, upstream_provider: str, finality: str,
) -> list[tuple[datetime, float]]:
    payload = _read_json(path)
    if payload is None or payload.get("schema_version") != 1:
        return []
    rows = payload.get("observations")
    if not isinstance(rows, list):
        return []
    points: list[tuple[datetime, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = _aware(row.get("provider_timestamp_utc"))
        value = _number(row.get("value"))
        if (
            stamp is None or value is None
            or row.get("route_id") != route_id
            or row.get("identity") != {
                "dataset_id": dataset_id, "market": market, "symbol": symbol,
            }
            or row.get("interval") != interval
            or row.get("provider") != provider
            or row.get("upstream_provider") != upstream_provider
            or row.get("finality") != finality
            or row.get("display_only") is not True
            or row.get("pit_safe") is not False
        ):
            continue
        points.append((stamp, value))
    return points


def _safe_landing(root: Path, relative: object, digest: object) -> dict[str, Any] | None:
    if not isinstance(relative, str) or not isinstance(digest, str) or len(digest) != 64:
        return None
    path = (root / relative).resolve()
    landing_root = (root / "data/landing/tossinvest/domestic_ur246").resolve()
    try:
        path.relative_to(landing_root)
        body = path.read_bytes()
    except (ValueError, FileNotFoundError, OSError):
        return None
    if hashlib.sha256(body).hexdigest() != digest.lower():
        return None
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _toss_points(root: Path, symbol: str, now: datetime) -> list[tuple[datetime, float]]:
    date_kst = now.astimezone(KST).date().isoformat()
    route_id = f"toss-market-price:{symbol}:snapshot:PROVISIONAL"
    current_path = root / f"data/state/current_observations/toss_{symbol.lower()}_ur246.json"
    points = _current_store_points(
        current_path, route_id=route_id, dataset_id="TOSS_MARKET_PRICE_SNAPSHOT",
        market="XKRX", symbol=symbol, interval="snapshot",
        provider="tossinvest_open_api", upstream_provider="tossinvest_open_api",
        finality="PROVISIONAL",
    )
    state = _read_json(root / f"data/state/toss_domestic_ur246/{date_kst}.json")
    if (
        state is not None and state.get("schema_version") == 1
        and state.get("operation_id") == "UR-246" and state.get("date_kst") == date_kst
        and isinstance(state.get("windows"), dict)
    ):
        for window, claims in state["windows"].items():
            window_time = _aware(window)
            claim = claims.get(symbol) if isinstance(claims, dict) else None
            if (
                window_time is None or window_time.astimezone(KST).date().isoformat() != date_kst
                or not time(9, 0) <= window_time.astimezone(KST).time() <= time(15, 0)
                or not isinstance(claim, dict) or claim.get("status") != "COMPLETE"
                or claim.get("route_id") != route_id
            ):
                continue
            stamp = _aware(claim.get("provider_timestamp_utc"))
            landing = _safe_landing(root, claim.get("landing_file"), claim.get("landing_sha256"))
            rows = landing.get("result") if landing is not None else None
            matches = [
                row for row in rows
                if isinstance(row, dict) and row.get("symbol") == symbol
            ] if isinstance(rows, list) else []
            value = _number(matches[0].get("lastPrice")) if len(matches) == 1 else None
            if stamp is not None and value is not None:
                points.append((stamp, value))
    return [
        point for point in _dedupe(points)
        if point[0].astimezone(KST).date().isoformat() == date_kst
        and point[0].astimezone(KST).time() >= time(9, 0)
    ]


def _session_points(path: Path, spec: _YahooSpec) -> list[tuple[datetime, float]]:
    payload = _read_json(path)
    rows = payload.get("points") if payload is not None else None
    if (
        payload is None or payload.get("schema_version") != 1
        or payload.get("series_id") != spec.series_id
        or payload.get("provider_symbol") != spec.provider_symbol
        or payload.get("interval") not in {"30m", "15m"}
        or payload.get("completed_bars_only") is not True
        or not isinstance(rows, list)
    ):
        return []
    points: list[tuple[datetime, float]] = []
    for row in rows:
        stamp = _aware(row.get("bar_end_utc")) if isinstance(row, dict) else None
        value = _number(row.get("value")) if isinstance(row, dict) else None
        if stamp is not None and value is not None:
            points.append((stamp, value))
    return _dedupe(points)


def _global_points(root: Path, spec: _YahooSpec) -> tuple[list[tuple[datetime, float]], datetime] | None:
    observations, _reason = load_global60m_ur232_current_observations(root)
    accepted = observations.get(spec.coverage_id)
    if accepted is None or accepted.interval.value != "30m":
        return None
    accepted_time = _aware(accepted.provider_timestamp_utc)
    if accepted_time is None:
        return None
    base = root / "data/state/current_observations/global60m_current"
    points = _session_points(base / f"{spec.file_stem}.session.json", spec)
    route_symbol = spec.provider_symbol[1:] if spec.provider_symbol.startswith("^") else spec.provider_symbol
    points.extend(_current_store_points(
        base / f"{spec.file_stem}.json",
        route_id=f"yahoo-market-current:{accepted.identity.market}:{route_symbol}",
        dataset_id="MARKET_PRICE_CURRENT", market=accepted.identity.market,
        symbol=spec.provider_symbol, interval="30m", provider="YAHOO",
        upstream_provider="YAHOO_CHART_API", finality="AS_RETRIEVED",
    ))
    points = [point for point in _dedupe(points) if accepted_time - timedelta(hours=24) <= point[0] <= accepted_time]
    if not points or points[-1][0] != accepted_time or not math.isclose(points[-1][1], accepted.value, rel_tol=0, abs_tol=1e-9):
        return None
    return points, accepted_time


def _native_landing_points(root: Path, spec: _YahooSpec, accepted: object) -> list[tuple[datetime, float]]:
    accepted_time = _aware(getattr(accepted, "provider_timestamp_utc", None))
    retrieved = _aware(getattr(accepted, "retrieved_at_utc", None))
    if accepted_time is None or retrieved is None:
        return []
    run_glob = f"yahoo-market-current-{retrieved:%Y%m%dT%H%M%SZ}-*"
    runs = sorted((root / "data/landing/yahoo_market_current").glob(run_glob), reverse=True)
    for run in runs:
        for call_path in sorted((run / "native_15m").glob("**/call.json")):
            call = _read_json(call_path)
            params = call.get("request_parameters") if call is not None else None
            if (
                call is None or call.get("capture_version") != 1
                or call.get("provider") != "yahoo" or call.get("operation") != "chart_15m"
                or call.get("http_status") != 200 or not isinstance(params, dict)
                or params.get("series_id") != spec.provider_symbol or params.get("interval") != "15m"
                or call.get("landing_body_file") != "response.body"
            ):
                continue
            try:
                body = (call_path.parent / "response.body").read_bytes()
            except OSError:
                continue
            if hashlib.sha256(body).hexdigest() != call.get("response_body_sha256"):
                continue
            try:
                payload = json.loads(body)
                results = payload["chart"]["result"]
                item = results[0] if isinstance(results, list) and len(results) == 1 else None
                meta = item["meta"]
                stamps = item["timestamp"]
                quotes = item["indicators"]["quote"]
                closes = quotes[0]["close"] if isinstance(quotes, list) and len(quotes) == 1 else None
            except (KeyError, TypeError, json.JSONDecodeError, UnicodeError):
                continue
            if (
                not isinstance(item, dict) or meta.get("symbol") != spec.provider_symbol
                or meta.get("dataGranularity") != "15m" or not isinstance(stamps, list)
                or not isinstance(closes, list) or len(stamps) != len(closes)
            ):
                continue
            points: list[tuple[datetime, float]] = []
            for raw_stamp, raw_value in zip(stamps, closes):
                value = _number(raw_value)
                try:
                    bar_end = datetime.fromtimestamp(float(raw_stamp), timezone.utc) + timedelta(minutes=15)
                except (TypeError, ValueError, OSError, OverflowError):
                    continue
                if value is not None and accepted_time - timedelta(hours=24) <= bar_end <= accepted_time:
                    points.append((bar_end, value))
            points = _dedupe(points)
            if points and points[-1][0] == accepted_time and math.isclose(
                points[-1][1], getattr(accepted, "value"), rel_tol=0, abs_tol=1e-9,
            ):
                return points
    return []


def _native_points(root: Path, spec: _YahooSpec) -> tuple[list[tuple[datetime, float]], datetime] | None:
    accepted = _load_yahoo_native15m_current(root, spec.provider_symbol)
    if accepted is None:
        return None
    accepted_time = _aware(accepted.provider_timestamp_utc)
    if accepted_time is None:
        return None
    base = root / "data/state/current_observations/yahoo_native15m_current"
    route_symbol = spec.provider_symbol[1:]
    points = _current_store_points(
        base / f"{spec.file_stem}.json", route_id=f"yahoo-market-current:CBOE:{route_symbol}",
        dataset_id="MARKET_PRICE_CURRENT", market="CBOE", symbol=spec.provider_symbol,
        interval="15m", provider="YAHOO", upstream_provider="YAHOO_CHART_API",
        finality="AS_RETRIEVED",
    )
    points.extend(_session_points(base / f"{spec.file_stem}.session.json", spec))
    if len(_dedupe(points)) < _MIN_POINTS:
        points.extend(_native_landing_points(root, spec, accepted))
    points = [point for point in _dedupe(points) if accepted_time - timedelta(hours=24) <= point[0] <= accepted_time]
    if not points or points[-1][0] != accepted_time or not math.isclose(points[-1][1], accepted.value, rel_tol=0, abs_tol=1e-9):
        return None
    return points, accepted_time


def _market_is_open(session: str, now: datetime) -> bool:
    if session == "kr_cash":
        local = now.astimezone(KST)
        return local.weekday() < 5 and time(9, 0) <= local.time() <= time(15, 30)
    local = now.astimezone(NEW_YORK)
    if session == "us_cash":
        return local.weekday() < 5 and time(9, 30) <= local.time() <= time(16, 0)
    weekday = local.weekday()
    return (
        (weekday == 6 and local.time() >= time(18, 0))
        or weekday in {0, 1, 2, 3}
        and not time(17, 0) <= local.time() < time(18, 0)
        or weekday == 4 and local.time() < time(17, 0)
    )


def _result(
    points: list[tuple[datetime, float]], *, source: str, session: str,
    window_prefix: str, now: datetime, disclaimer: str | None = None,
) -> dict[str, object] | None:
    if len(points) < _MIN_POINTS:
        return None
    latest = points[-1][0]
    stale = now >= latest and now - latest > timedelta(hours=2) and _market_is_open(session, now)
    clock_label = (
        f"{latest.astimezone(KST):%H:%M}"
        if session == "kr_cash" else f"{latest.astimezone(KST):%H:%M} KST"
    )
    window = "장중 · 갱신 지연" if stale else f"{window_prefix} · {clock_label}"
    if disclaimer and not stale:
        window = f"{window} · {disclaimer}"
    return {
        "points": [{"t": _iso_kst(stamp), "v": value} for stamp, value in points],
        "window": window,
        "as_of": _iso_kst(latest),
        "source": source,
    }


def load_intraday_series(project_root: Path, tile_key: str) -> dict[str, object] | None:
    """Return a retained display-only intraday series, or ``None`` without I/O mutation."""
    root = Path(project_root).resolve()
    now = _now_utc()
    if tile_key in {"KOSPI", "KOSDAQ"}:
        accepted, _reason = load_toss_domestic_ur246_current_observation(root, symbol=tile_key)
        if accepted is None:
            return None
        points = _toss_points(root, tile_key, now)
        accepted_time = _aware(accepted.provider_timestamp_utc)
        if (
            accepted_time is None or not points or points[-1][0] != accepted_time
            or not math.isclose(points[-1][1], accepted.value, rel_tol=0, abs_tol=1e-9)
        ):
            return None
        return _result(
            points, source=f"Toss 국내 30분 관측 · {tile_key}", session="kr_cash",
            window_prefix="당일 09:00~", now=now,
        )
    spec = _GLOBAL_SPECS.get(tile_key)
    if spec is not None:
        loaded = _global_points(root, spec)
        return None if loaded is None else _result(
            loaded[0], source=spec.source, session=spec.session,
            window_prefix=spec.window_prefix, now=now, disclaimer=spec.disclaimer,
        )
    spec = _NATIVE_SPECS.get(tile_key)
    if spec is not None:
        loaded = _native_points(root, spec)
        return None if loaded is None else _result(
            loaded[0], source=spec.source, session=spec.session,
            window_prefix=spec.window_prefix, now=now, disclaimer=spec.disclaimer,
        )
    # The retained Yahoo operation has ^GSPC, not ES=F, so the S&P 500
    # futures tile must not silently substitute a cash-index observation.
    return None


__all__ = ["load_intraday_series"]
