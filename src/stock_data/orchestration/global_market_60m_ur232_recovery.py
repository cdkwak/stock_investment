"""API-zero, retained-Landing current-display recovery for UR-232 only.

This deliberately does not touch the global-60m normalized/history state.  It
re-parses one immutable Landing run into per-identity display-only envelopes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

from stock_data.orchestration.current_observation import (
    CurrentObservation,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)
from stock_data.providers.yahoo import fetch_global_market_60m


OPERATION_ID = "UR-232"
RUN_ID = "global60m-20260821T121202Z-ee2361078a99446399486fb17359d2a5"
LANDING_ROOT = Path("data/landing/global_market_60m") / RUN_ID
PROJECTION_ROOT = Path("data/state/current_observations/global60m_ur232")
RECOVERY_CLASSIFICATION = "RETAINED_LANDING_API_ZERO_RECOVERY"


@dataclass(frozen=True)
class _Spec:
    series_id: str
    market: str
    provider_symbol: str
    asset_type: str
    timezone: str
    unit: str
    semantics: str


SPECS = {
    "USD_KRW_60M": _Spec("USD_KRW_60M", "GLOBAL_FX", "KRW=X", "FOREX", "Asia/Seoul", "KRW per USD", "FX_INDICATIVE_KRW_PER_USD"),
    "UST2_FUTURES_60M": _Spec("UST2_FUTURES_60M", "CBOT", "ZT=F", "FUTURE_CONTINUOUS", "America/Chicago", "provider native continuous futures price", "CONTINUOUS_FUTURES_PRICE_NOT_YIELD"),
    "UST10_FUTURES_60M": _Spec("UST10_FUTURES_60M", "CBOT", "ZN=F", "FUTURE_CONTINUOUS", "America/Chicago", "provider native continuous futures price", "CONTINUOUS_FUTURES_PRICE_NOT_YIELD"),
    "UST30_FUTURES_60M": _Spec("UST30_FUTURES_60M", "CBOT", "ZB=F", "FUTURE_CONTINUOUS", "America/Chicago", "provider native continuous futures price", "CONTINUOUS_FUTURES_PRICE_NOT_YIELD"),
}


@dataclass(frozen=True)
class RecoveryResult:
    accepted: dict[str, CurrentObservation]
    rejected: dict[str, str]
    replayed: tuple[str, ...]
    api_calls: int = 0


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UR-232 audit clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _projection_path(series_id: str) -> Path:
    if series_id not in SPECS:
        raise ValueError("UR-232 unknown global 60m identity")
    return PROJECTION_ROOT / f"{series_id.lower()}.json"


def _observation_payload(observation: CurrentObservation) -> dict[str, object]:
    return {
        "route_id": observation.route_id,
        "identity": asdict(observation.identity),
        "interval": observation.interval.value,
        "value": observation.value,
        "unit": observation.unit,
        "provider": observation.provider,
        "upstream_provider": observation.upstream_provider,
        "source_route": observation.source_route,
        "provider_timestamp_utc": observation.provider_timestamp_utc,
        "retrieved_at_utc": observation.retrieved_at_utc,
        "finality": observation.finality.value,
        "display_only": observation.display_only,
        "pit_safe": observation.pit_safe,
    }


def _decode_observation(payload: object) -> CurrentObservation:
    if not isinstance(payload, dict):
        raise ValueError("UR-232 observation payload is invalid")
    try:
        observation = CurrentObservation(
            route_id=str(payload["route_id"]),
            identity=ObservationIdentity(**payload["identity"]),
            interval=ObservationInterval(str(payload["interval"])),
            value=float(payload["value"]), unit=str(payload["unit"]),
            provider=str(payload["provider"]), upstream_provider=str(payload["upstream_provider"]),
            source_route=str(payload["source_route"]),
            provider_timestamp_utc=str(payload["provider_timestamp_utc"]),
            retrieved_at_utc=str(payload["retrieved_at_utc"]),
            finality=ObservationFinality(str(payload["finality"])),
            display_only=payload["display_only"], pit_safe=payload["pit_safe"],
        )
        observation.validate()
        return observation
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("UR-232 observation payload is invalid") from error


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _restore(path: Path, prior: bytes | None) -> None:
    if prior is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("xb") as stream:
            stream.write(prior)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_landing(root: Path) -> tuple[dict[str, tuple[bytes, str, str]], dict[str, str]]:
    """Return verified original bytes, hash and immutable relative path by ID."""
    run_root = Path(root) / LANDING_ROOT
    found: dict[str, tuple[bytes, str, str]] = {}
    rejected: dict[str, str] = {}
    for call_path in sorted(run_root.rglob("call.json")):
        try:
            call = json.loads(call_path.read_text(encoding="utf-8"))
            if not isinstance(call, dict) or call.get("provider") != "yahoo" or call.get("operation") != "global_chart_60m" or call.get("http_status") != 200:
                raise ValueError("call metadata")
            parameters = call.get("request_parameters")
            if not isinstance(parameters, dict):
                raise ValueError("parameters")
            series_id = str(parameters.get("series_id"))
            if series_id not in SPECS or series_id in found:
                raise ValueError("series identity")
            body_name = call.get("landing_body_file")
            if body_name != "response.body":
                raise ValueError("body path")
            body_path = call_path.parent / body_name
            body = body_path.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if digest != call.get("response_body_sha256"):
                rejected[series_id] = "LANDING_BODY_HASH_MISMATCH"
                continue
            found[series_id] = (body, digest, body_path.relative_to(Path(root)).as_posix())
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            rejected.setdefault("__run__", "LANDING_METADATA_INVALID")
    for series_id in SPECS:
        if series_id not in found and series_id not in rejected:
            rejected[series_id] = "LANDING_BODY_MISSING"
    return found, rejected


def _parse(series_id: str, body: bytes, audit_at: datetime) -> pd.DataFrame:
    response_type = type("RetainedResponse", (), {
        "content": body, "status_code": 200,
        "raise_for_status": lambda self: None,
        "json": lambda self, body=body: json.loads(body),
    })
    session = type("RetainedSession", (), {
        "get": staticmethod(lambda *args, response_type=response_type, **kwargs: response_type()),
    })
    return fetch_global_market_60m(
        series_id, start=audit_at - timedelta(days=7), end=audit_at,
        session=session, retrieved_at=audit_at,
    )


def _candidate(series_id: str, body: bytes, audit_at: datetime) -> tuple[pd.Series, str] | tuple[None, str]:
    try:
        frame = _parse(series_id, body, audit_at)
    except (TypeError, ValueError, RuntimeError, json.JSONDecodeError):
        return None, "LANDING_SCHEMA_OR_IDENTITY_REJECTED"
    completed = frame.loc[pd.to_datetime(frame["bar_end"], utc=True) <= pd.Timestamp(audit_at)]
    if completed.empty:
        return None, "NO_STRICTLY_COMPLETED_60M_BAR"
    row = completed.sort_values("bar_end", kind="stable").iloc[-1]
    end = pd.Timestamp(row.bar_end).tz_convert("UTC").to_pydatetime()
    age = audit_at - end
    if age < timedelta(0) or age > timedelta(minutes=60):
        return None, "SOURCE_AGE_OVER_60M_OR_FUTURE"
    spec = SPECS[series_id]
    expected_semantics = (
        (spec.unit == "KRW per USD" and spec.semantics == "FX_INDICATIVE_KRW_PER_USD")
        if series_id == "USD_KRW_60M"
        else (spec.unit == "provider native continuous futures price" and spec.semantics == "CONTINUOUS_FUTURES_PRICE_NOT_YIELD")
    )
    exact = (
        str(row.symbol) == series_id and str(row.market) == spec.market
        and str(row.provider_symbol) == spec.provider_symbol and str(row.asset_type) == spec.asset_type
        and str(row.timezone) == spec.timezone and str(row.session) == "GLOBAL_CONTINUOUS"
        and str(row.interval) == "60m" and int(row.actual_duration_minutes) == 60 and expected_semantics
    )
    ohlc = (row.open, row.high, row.low, row.close)
    if not exact or not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in ohlc):
        return None, "UNIT_OR_SEMANTICS_OR_OHLC_REJECTED"
    return row, "PASS"


def _envelope(observation: CurrentObservation, *, series_id: str, body_path: str, digest: str, audit_at: datetime, row: pd.Series) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_id": OPERATION_ID,
        "recovery_classification": RECOVERY_CLASSIFICATION,
        "series_id": series_id,
        "observation": _observation_payload(observation),
        "immutable_landing": {
            "run_id": RUN_ID,
            "body_path": body_path,
            "body_sha256": digest,
            "audit_at_utc": audit_at.isoformat(),
            "bar_start_utc": pd.Timestamp(row.bar_start).tz_convert("UTC").isoformat(),
            "bar_end_utc": pd.Timestamp(row.bar_end).tz_convert("UTC").isoformat(),
            "semantics": SPECS[series_id].semantics,
        },
    }


def read_observation(root: Path, series_id: str) -> CurrentObservation:
    payload = json.loads((Path(root) / _projection_path(series_id)).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("operation_id") != OPERATION_ID or payload.get("recovery_classification") != RECOVERY_CLASSIFICATION or payload.get("series_id") != series_id:
        raise ValueError("UR-232 projection envelope is invalid")
    landing = payload.get("immutable_landing")
    if not isinstance(landing, dict) or landing.get("run_id") != RUN_ID or not isinstance(landing.get("body_path"), str) or not isinstance(landing.get("body_sha256"), str):
        raise ValueError("UR-232 immutable Landing provenance is invalid")
    return _decode_observation(payload.get("observation"))


def recover(root: Path, *, audit_at: datetime) -> RecoveryResult:
    """Read only the fixed immutable run and atomically project passing identities."""
    audit = _utc(audit_at)
    found, rejected = _read_landing(Path(root))
    accepted: dict[str, CurrentObservation] = {}
    replayed: list[str] = []
    for series_id, spec in SPECS.items():
        if series_id not in found:
            continue
        body, digest, body_path = found[series_id]
        row, reason = _candidate(series_id, body, audit)
        if row is None:
            rejected[series_id] = reason
            continue
        identity = ObservationIdentity("MARKET_PRICE_60M_CURRENT", spec.market, spec.provider_symbol)
        route_id = f"yahoo-global60m-ur232:{spec.market}:{spec.provider_symbol}"
        observation = CurrentObservation(
            route_id, identity, ObservationInterval.MINUTES_60, float(row.close), spec.unit,
            "YAHOO", "YAHOO_CHART_API", "YAHOO_CHART_GLOBAL60M_RETAINED_LANDING_API_ZERO_RECOVERY",
            pd.Timestamp(row.bar_end).tz_convert("UTC").isoformat(), audit.isoformat(),
            ObservationFinality.AS_RETRIEVED,
        )
        observation.validate()
        payload = _envelope(observation, series_id=series_id, body_path=body_path, digest=digest, audit_at=audit, row=row)
        path = Path(root) / _projection_path(series_id)
        prior = path.read_bytes() if path.exists() else None
        if prior == json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"):
            replayed.append(series_id)
            accepted[series_id] = observation
            continue
        try:
            _atomic_write(path, payload)
            if read_observation(root, series_id) != observation:
                raise ValueError("UR-232 atomic projection readback differs")
        except Exception:
            _restore(path, prior)
            rejected[series_id] = "ATOMIC_PROJECTION_FAILURE_PRIOR_PRESERVED"
            continue
        accepted[series_id] = observation
    return RecoveryResult(accepted=accepted, rejected=rejected, replayed=tuple(replayed), api_calls=0)


__all__ = [
    "LANDING_ROOT", "OPERATION_ID", "PROJECTION_ROOT", "RECOVERY_CLASSIFICATION", "RUN_ID", "SPECS",
    "RecoveryResult", "read_observation", "recover",
]
