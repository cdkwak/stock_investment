"""Offline readiness manifest for UR-231 Yahoo native-15m current lanes.

This module has no provider client.  A later expressly authorized collector must
inject its transport after the durable manifest/ledger gates below select one
half-open first-completed-bar window.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.contracts.market_15m import MARKET_15M_LANE_SERIES, MARKET_15M_SERIES_POLICIES
from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import CurrentObservation, CurrentObservationCoordinator, CurrentObservationFileStore, CurrentObservationRoute, ObservationFinality, ObservationIdentity, ObservationInterval
from stock_data.validation.market_15m import validate_market_price_15m
from stock_data.validation.market_15m import DELAYED_CLASSIFICATION, YAHOO_15M_IDENTITIES


OPERATION_ID = "UR-231"
KST = ZoneInfo("Asia/Seoul")
TARGET_DATE = date(2026, 8, 21)
MANIFEST_PATH = Path("data/state/yahoo_native15m_ur231_manifest.json")
STATE_ROOT = Path("data/state/yahoo_native15m_ur231")
LANDING_ROOT = Path("data/landing/yahoo_native15m_ur231")
PROJECTION_ROOT = Path("data/state/current_observations/yahoo_native15m_ur231")


@dataclass(frozen=True)
class LanePlan:
    lane_id: str
    series_ids: tuple[str, ...]
    earliest_completed_kst: str
    source_start_utc: str
    source_end_utc: str
    units: tuple[str, ...]


LANES = {
    "CBOE_VIX": LanePlan(
        "CBOE_VIX", ("^VIX",), "2026-08-21T22:45:00+09:00",
        "2026-08-21T13:30:00+00:00", "2026-08-21T13:45:00+00:00", ("index points",),
    ),
    "YAHOO_TREASURY_QUOTE": LanePlan(
        "YAHOO_TREASURY_QUOTE", ("^FVX", "^TNX", "^TYX"), "2026-08-21T22:35:00+09:00",
        "2026-08-21T13:20:00+00:00", "2026-08-21T13:35:00+00:00",
        ("provider native quote index points",) * 3,
    ),
}


def manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1, "operation_id": OPERATION_ID,
        "target_date": TARGET_DATE.isoformat(), "timeout_seconds": 10,
        "retry_count": 0, "redirect_count": 0, "fallback_count": 0,
        "auth": "NONE", "cookie": "NONE", "environment": "NONE",
        "display_only": True, "pit_safe": False,
        "lanes": [
            {
                **asdict(plan),
                "series_ids": list(plan.series_ids),
                "units": list(plan.units),
            }
            for plan in LANES.values()
        ],
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        if json.loads(path.read_text(encoding="utf-8")) != payload:
            raise RuntimeError("UR-231 atomic state readback differs")
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def ensure_manifest(root: Path) -> Path:
    path = Path(root) / MANIFEST_PATH
    expected = manifest_payload()
    if path.exists():
        if read_manifest(root) != expected:
            raise RuntimeError("UR-231 activation manifest differs from approved scope")
        return path
    _write(path, expected)
    return path


def read_manifest(root: Path) -> dict[str, object]:
    try: payload = json.loads((Path(root) / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error: raise RuntimeError("UR-231 activation manifest unreadable") from error
    if payload != manifest_payload(): raise RuntimeError("UR-231 activation manifest differs from approved scope")
    return payload


def state_path(lane_id: str) -> Path:
    if lane_id not in LANES: raise ValueError("UR-231 unknown lane")
    return STATE_ROOT / f"{lane_id.lower()}.json"


def landing_root(lane_id: str) -> Path:
    if lane_id not in LANES: raise ValueError("UR-231 unknown lane")
    return LANDING_ROOT / lane_id.lower()


def projection_path(lane_id: str) -> Path:
    if lane_id not in LANES: raise ValueError("UR-231 unknown lane")
    return PROJECTION_ROOT / f"{lane_id.lower()}.json"


def selected_boundary(lane_id: str, *, now: datetime) -> str | None:
    if now.tzinfo is None or now.utcoffset() is None: raise ValueError("UR-231 clock must be timezone-aware")
    plan = LANES[lane_id]; boundary = datetime.fromisoformat(plan.earliest_completed_kst)
    return boundary.isoformat() if boundary <= now.astimezone(KST) < boundary + timedelta(minutes=15) else None


def _read_ledger(root: Path, lane_id: str) -> dict[str, object]:
    path = Path(root) / state_path(lane_id)
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError: return {"schema_version": 1, "operation_id": OPERATION_ID, "lane_id": lane_id, "windows": {}}
    except (OSError, json.JSONDecodeError) as error: raise RuntimeError("UR-231 durable lane ledger unreadable") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("operation_id") != OPERATION_ID or payload.get("lane_id") != lane_id or not isinstance(payload.get("windows"), dict):
        raise RuntimeError("UR-231 durable lane ledger schema mismatch")
    return payload


def eligibility(root: Path, lane_id: str, *, now: datetime) -> str:
    try: read_manifest(root); ledger = _read_ledger(root, lane_id)
    except RuntimeError: return "API_ZERO_INVALID_MANIFEST_OR_LEDGER"
    boundary = selected_boundary(lane_id, now=now)
    if boundary is None: return "API_ZERO_PREBOUNDARY_OR_WINDOW_CLOSED"
    record = ledger["windows"].get(boundary)
    if record is None: return "ELIGIBLE"
    return "ORPHAN_ATTEMPTING_NO_REPEAT" if isinstance(record, dict) and record.get("status") == "ATTEMPTING" else "NO_REPEAT"


def claim(root: Path, lane_id: str, *, now: datetime) -> str:
    """Persist a future one-shot claim before a separately injected transport."""
    decision = eligibility(root, lane_id, now=now)
    if decision != "ELIGIBLE": return decision
    boundary = selected_boundary(lane_id, now=now); assert boundary is not None
    ledger = _read_ledger(root, lane_id); windows = dict(ledger["windows"])
    windows[boundary] = {"status": "ATTEMPTING", "attempted_at_utc": now.astimezone(timezone.utc).isoformat(), "raw_gets_reserved": len(LANES[lane_id].series_ids), "raw_gets_invoked": 0, "raw_gets_completed": 0, "retry_count": 0, "redirect_count": 0, "fallback_count": 0}
    ledger["windows"] = windows; _write(Path(root) / state_path(lane_id), ledger)
    return "CLAIMED_NO_TRANSPORT"


def validate_first_completed_bar(lane_id: str, frame: pd.DataFrame, *, retrieved_at: datetime) -> None:
    """Strict offline gate used by a future injected collector before projection."""
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None: raise ValueError("retrieved_at must be timezone-aware")
    plan = LANES[lane_id]; validate_market_price_15m(frame)
    if set(frame["series_id"].astype(str)) != set(plan.series_ids): raise ValueError("UR-231 frame identities differ from lane")
    expected_start = pd.Timestamp(plan.source_start_utc)
    for series_id in plan.series_ids:
        rows = frame.loc[frame["series_id"].eq(series_id)]
        if len(rows) != 1 or pd.Timestamp(rows.iloc[0].bar_start).tz_convert("UTC") != expected_start:
            raise ValueError("UR-231 first completed bar start differs")
        if pd.Timestamp(rows.iloc[0].bar_end).tz_convert("UTC") > pd.Timestamp(retrieved_at).tz_convert("UTC"):
            raise ValueError("UR-231 live-forming bar is rejected")
        policy = MARKET_15M_SERIES_POLICIES[series_id]
        if str(rows.iloc[0].source_timezone) != policy.source_timezone or str(rows.iloc[0].session) != "REGULAR":
            raise ValueError("UR-231 source timezone/session differs")


def _projection_file(root: Path, lane_id: str, series_id: str) -> Path:
    return (Path(root) / projection_path(lane_id)).with_name(f"{lane_id.lower()}_{series_id.replace('^', 'idx')}.json")


def _restore(path: Path, prior: bytes | None) -> None:
    if prior is None:
        try: path.unlink()
        except FileNotFoundError: pass
    else: path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(prior)


def execute_injected(root: Path, lane_id: str, *, now: datetime, responses: Mapping[str, Callable[[], tuple[int, bytes]]], parser: Callable[[str, bytes, datetime], pd.DataFrame]) -> str:
    """One future injected path: claim, Landing readback, validate, atomic display projection."""
    if set(responses) != set(LANES[lane_id].series_ids): raise ValueError("UR-231 exact lane callbacks required")
    if claim(root, lane_id, now=now) != "CLAIMED_NO_TRANSPORT": return eligibility(root, lane_id, now=now)
    boundary = selected_boundary(lane_id, now=now); assert boundary is not None
    ledger = _read_ledger(root, lane_id); record = ledger["windows"][boundary]; frames: list[pd.DataFrame] = []
    prior = {s: (_projection_file(root, lane_id, s).read_bytes() if _projection_file(root, lane_id, s).exists() else None) for s in LANES[lane_id].series_ids}
    try:
        import hashlib
        response_failure = False
        for series_id in LANES[lane_id].series_ids:
            record["raw_gets_invoked"] = int(record["raw_gets_invoked"]) + 1; ledger["windows"][boundary] = record; _write(Path(root) / state_path(lane_id), ledger)
            status, body = responses[series_id](); record["raw_gets_completed"] = int(record["raw_gets_completed"]) + 1
            if status != 200:
                response_failure = True
                continue
            digest = hashlib.sha256(body).hexdigest(); landing = Path(root) / landing_root(lane_id) / boundary.replace(":", "") / series_id.replace("^", "idx") / f"{digest}.body"; landing.parent.mkdir(parents=True, exist_ok=True)
            with landing.open("xb") as stream: stream.write(body); stream.flush(); os.fsync(stream.fileno())
            retained = landing.read_bytes()
            if hashlib.sha256(retained).hexdigest() != digest: raise ValueError("UR-231 Landing hash readback differs")
            frames.append(parser(series_id, retained, now))
        if response_failure: raise ValueError("UR-231 HTTP status rejected")
        frame = pd.concat(frames, ignore_index=True); validate_first_completed_bar(lane_id, frame, retrieved_at=now)
        for series_id in LANES[lane_id].series_ids:
            row = frame.loc[frame["series_id"].eq(series_id)].iloc[0]; market = str(row.market).upper(); symbol = series_id.replace("^", "IDX"); route_id = f"yahoo-native15m-ur231:{market}:{symbol}"
            identity = ObservationIdentity("MARKET_PRICE_15M_CURRENT", market, symbol); route = CurrentObservationRoute(RoutePolicy(route_id, "YAHOO", "YAHOO_CHART_15M", "UNAVAILABLE", "UNAVAILABLE", "UNAVAILABLE", False), identity, (ObservationInterval.MINUTES_15,))
            unit = LANES[lane_id].units[LANES[lane_id].series_ids.index(series_id)]
            observation = CurrentObservation(route_id, identity, ObservationInterval.MINUTES_15, float(row.close), unit, "YAHOO", "YAHOO_CHART_API", "YAHOO_CHART_15M", pd.Timestamp(row.bar_end).tz_convert("UTC").isoformat(), now.astimezone(timezone.utc).isoformat(), ObservationFinality.AS_RETRIEVED)
            source = SourceObservation(observation, SourceProvenance("YAHOO", "YAHOO_CHART_API", "YAHOO_CHART_15M", now.astimezone(timezone.utc).isoformat(), 1))
            if CurrentObservationCoordinator(CurrentObservationFileStore(_projection_file(root, lane_id, series_id))).refresh(route, primary_attempt=lambda source=source: source, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("UR-231 no fallback"))).observation is None: raise ValueError("UR-231 projection rejected")
        record.update({"status": "COMPLETE_ACCEPTED", "raw_gets": len(LANES[lane_id].series_ids), "replay_api_calls": 0}); ledger["windows"][boundary] = record; _write(Path(root) / state_path(lane_id), ledger); return "COMPLETE_ACCEPTED"
    except Exception:
        for series_id, bytes_ in prior.items(): _restore(_projection_file(root, lane_id, series_id), bytes_)
        record.update({"status": "COMPLETE_FAILURE", "raw_gets": int(record["raw_gets_invoked"])}); ledger["windows"][boundary] = record; _write(Path(root) / state_path(lane_id), ledger); return "COMPLETE_FAILURE"


def yahoo_chart_request(series_id: str, lane_id: str) -> tuple[str, dict[str, object], dict[str, str]]:
    if series_id not in LANES[lane_id].series_ids: raise ValueError("UR-231 symbol is not in lane")
    plan = LANES[lane_id]
    return (f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(series_id, safe='')}", {"period1": int(pd.Timestamp(plan.source_start_utc).timestamp()), "period2": int(pd.Timestamp(plan.source_end_utc).timestamp()), "interval": "15m", "events": "history", "includeAdjustedClose": "false", "includePrePost": "false"}, {"User-Agent": "stock-investment-rev1/0.1"})


def parse_yahoo_chart_response(series_id: str, body: bytes, retrieved_at: datetime) -> pd.DataFrame:
    payload = json.loads(body.decode("utf-8")); item = payload.get("chart", {}).get("result", [None])[0]
    if not isinstance(item, dict): raise ValueError("UR-231 chart result missing")
    meta = item.get("meta") or {}; timestamps = item.get("timestamp") or []; quote_rows = ((item.get("indicators") or {}).get("quote") or [])
    if str(meta.get("symbol")) != series_id or str(meta.get("dataGranularity")) != "15m" or len(timestamps) != 1 or len(quote_rows) != 1: raise ValueError("UR-231 chart identity/grid differs")
    values = quote_rows[0]; fields = ("open", "high", "low", "close", "volume")
    if any(len(values.get(field) or []) != 1 for field in fields): raise ValueError("UR-231 chart values differ")
    market, instrument, session = YAHOO_15M_IDENTITIES[series_id]; policy = MARKET_15M_SERIES_POLICIES[series_id]; start = pd.to_datetime(timestamps[0], unit="s", utc=True)
    row = {"market_date": start.tz_convert(policy.source_timezone).date(), "market": market, "series_id": series_id, "provider_symbol": series_id, "instrument_type": instrument, "bar_start": start, "bar_end": start + timedelta(minutes=15), "source_timezone": str(meta.get("exchangeTimezoneName") or ""), "display_timezone": "Asia/Seoul", "session": session, "interval": "15m", **{field: values[field][0] for field in fields}, "provider": "yahoo_chart_api", "data_availability": DELAYED_CLASSIFICATION, "retrieved_at": pd.Timestamp(retrieved_at).tz_convert("UTC")}
    frame = pd.DataFrame([row]); frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").astype("Int64")
    return frame


def operational_run(root: Path, lane_id: str, *, now: datetime, transport: Callable[..., tuple[int, bytes]]) -> str:
    """Actual fixed-route CLI path; transport is constructed only after API-zero eligibility."""
    if eligibility(root, lane_id, now=now) != "ELIGIBLE": return eligibility(root, lane_id, now=now)
    callbacks: dict[str, Callable[[], tuple[int, bytes]]] = {}
    for series_id in LANES[lane_id].series_ids:
        url, params, headers = yahoo_chart_request(series_id, lane_id)
        callbacks[series_id] = lambda url=url, params=params, headers=headers: transport(url=url, params=params, headers=headers, timeout=10, allow_redirects=False)
    return execute_injected(root, lane_id, now=now, responses=callbacks, parser=parse_yahoo_chart_response)


__all__ = ["KST", "LANDING_ROOT", "LANES", "MANIFEST_PATH", "OPERATION_ID", "PROJECTION_ROOT", "STATE_ROOT", "TARGET_DATE", "claim", "eligibility", "ensure_manifest", "execute_injected", "landing_root", "manifest_payload", "operational_run", "parse_yahoo_chart_response", "projection_path", "read_manifest", "selected_boundary", "state_path", "validate_first_completed_bar", "yahoo_chart_request"]
