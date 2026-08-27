"""Transport-free, allowlisted Dashboard current-readiness CSV projection."""
from __future__ import annotations

import csv
import io
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from stock_data.gui.korean_equity_nxt_session import classify_korean_equity_nxt_timestamp


KST = ZoneInfo("Asia/Seoul")
CSV_FIELDS = (
    "surface_id", "section", "label", "description", "gui_used",
    "current_numeric_visible", "provider", "route", "interval",
    "source_timestamp_kst", "age_gate", "status", "unit",
    "display_boundary", "backtest_used", "exact_reason", "evidence_or_followup",
)
CSV_PATH = Path("docs/data/DASHBOARD_64_CURRENT_READINESS.csv")
SOXX_PATH = Path("data/state/current_observations/nasdaq_soxx_info_current.json")
HOME_PATH = Path("data/state/current_observations/naver_mobile_home_current.json")
MOBILE_PATHS = {
    "000660": Path("data/state/current_observations/naver_mobile_basic_000660_ur199.json"),
    "005930": Path("data/state/current_observations/naver_mobile_basic_005930_ur199.json"),
}
TOSS_NXT_CLOSE_PATHS = {
    "000660": Path("data/state/current_observations/toss_000660_nxt_session_close_ur240.json"),
    "005930": Path("data/state/current_observations/toss_005930_nxt_close_ur241.json"),
}
GLOBAL_LOG_PATH = Path("artifacts/scheduler_logs/STOCK_DATA_GLOBAL_MARKET_60M_last.json")
GLOBAL_STATE_PATH = Path("data/state/global_market_60m.json")
GLOBAL_UR232_ROOT = Path("data/state/current_observations/global60m_ur232")
PRODUCTION_MANIFEST_PATH = Path("data/state/dashboard_current_readiness_projector_activation.json")
RUNBOOK_PATH = Path("docs/data/operations/DASHBOARD_CURRENT_READINESS_LOCAL_PROJECTOR.md")
UR233_PRODUCTION_MANIFEST_PATH = Path("data/state/dashboard_current_readiness_projector_ur233_activation.json")
UR233_RUNBOOK_PATH = Path("docs/data/operations/DASHBOARD_CURRENT_READINESS_UR233.md")
UR242_PRODUCTION_MANIFEST_PATH = Path("data/state/dashboard_current_readiness_projector_ur242_activation.json")
UR242_RUNBOOK_PATH = Path("docs/data/operations/DASHBOARD_CURRENT_READINESS_UR242.md")
ACTIVE_PRODUCTION_STATUS = "ACTIVE_EXACT_PRODUCTION"


@dataclass(frozen=True)
class ObservationSpec:
    state_path: Path
    identity: tuple[str, str, str]
    provider: str
    route: str
    source_route: str
    unit: str
    rows: tuple[str, ...]
    finality: str = "PROVISIONAL"
    nxt_session_gate: bool = False
    nxt_venue_inferred: bool = False


OBSERVATIONS = (
    ObservationSpec(SOXX_PATH, ("US_ETF_CURRENT", "NASDAQ", "SOXX"), "NASDAQ_OFFICIAL", "nasdaq-soxx-info-api:NASDAQ:SOXX", "NASDAQ_OFFICIAL:api.nasdaq.com/api/quote/SOXX/info?assetclass=etf", "USD per share", ("tape_soxx", "coverage_fdr_soxx")),
    ObservationSpec(HOME_PATH, ("KR_INDEX_CURRENT", "XKRX", "KOSPI"), "NAVER_FINANCE_WEB", "naver-mobile-home-current:XKRX:KOSPI", "NAVER_WEB:/", "index points", ("tape_kospi",)),
    ObservationSpec(HOME_PATH, ("KR_INDEX_CURRENT", "XKRX", "KOSDAQ"), "NAVER_FINANCE_WEB", "naver-mobile-home-current:XKRX:KOSDAQ", "NAVER_WEB:/", "index points", ("tape_kosdaq",)),
    ObservationSpec(HOME_PATH, ("FX_CURRENT", "KRW", "USD_KRW"), "NAVER_FINANCE_WEB", "naver-mobile-home-current:KRW:USD_KRW", "NAVER_WEB:/", "KRW per USD", ("usd_krw_official_row",)),
    ObservationSpec(MOBILE_PATHS["000660"], ("KR_EQUITY_CURRENT", "XKRX", "000660"), "NAVER_FINANCE_WEB", "naver-mobile-basic-current:XKRX:000660", "NAVER_FINANCE_WEB:m.stock.naver.com/api/stock/000660/basic", "KRW per share", ("coverage_korean_equity_000660",)),
    ObservationSpec(MOBILE_PATHS["005930"], ("KR_EQUITY_CURRENT", "XKRX", "005930"), "NAVER_FINANCE_WEB", "naver-mobile-basic-current:XKRX:005930", "NAVER_FINANCE_WEB:m.stock.naver.com/api/stock/005930/basic", "KRW per share", ("korean_equity_current_header_005930",)),
    ObservationSpec(TOSS_NXT_CLOSE_PATHS["000660"], ("KR_EQUITY_CURRENT", "XKRX", "000660"), "tossinvest_open_api", "toss-stock-price:000660:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW", "/api/v1/prices", "KRW per share", ("coverage_korean_equity_000660",), finality="POST_CLOSE_SNAPSHOT", nxt_session_gate=True, nxt_venue_inferred=True),
    ObservationSpec(TOSS_NXT_CLOSE_PATHS["005930"], ("KR_EQUITY_CURRENT", "XKRX", "005930"), "tossinvest_open_api", "toss-stock-price:005930:snapshot:PROVISIONAL:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW", "/api/v1/prices:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW", "KRW per share", ("korean_equity_current_header_005930",), nxt_session_gate=True, nxt_venue_inferred=True),
)
GLOBAL_ROWS = {
    "USD_KRW_60M": ("usd_krw_60m_detail", "Yahoo", "Yahoo:KRW=X", "KRW per USD"),
    "UST2_FUTURES_60M": ("ust2_futures_60m", "Yahoo", "Yahoo:ZT=F", "provider native continuous futures price; not US 2Y yield"),
    "UST10_FUTURES_60M": ("ust10_futures_60m", "Yahoo", "Yahoo:ZN=F", "provider native continuous futures price; not US 10Y yield"),
    "UST30_FUTURES_60M": ("ust30_futures_60m", "Yahoo", "Yahoo:ZB=F", "provider native continuous futures price; not US 30Y yield"),
}
DYNAMIC_FIELDS = {"current_numeric_visible", "provider", "route", "interval", "source_timestamp_kst", "age_gate", "status", "unit", "exact_reason"}


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("provider source timestamp missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("provider source timestamp is naive")
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("state root is not an object")
    return payload


def _observation(root: Path, spec: ObservationSpec) -> dict[str, object]:
    payload = _read_json(root / spec.state_path)
    rows = payload.get("observations")
    if payload.get("schema_version") != 1 or not isinstance(rows, list):
        raise ValueError("current-observation schema is invalid")
    identity = {"dataset_id": spec.identity[0], "market": spec.identity[1], "symbol": spec.identity[2]}
    matched = [row for row in rows if isinstance(row, dict) and row.get("identity") == identity]
    if len(matched) != 1:
        raise ValueError("exact observation identity missing or duplicated")
    row = matched[0]
    value = row.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("observation value is not finite positive")
    if (
        row.get("route_id") != spec.route or row.get("provider") != spec.provider
        or row.get("upstream_provider") != spec.provider or row.get("source_route") != spec.source_route
        or row.get("interval") != "snapshot" or row.get("unit") != spec.unit
        or row.get("finality") != spec.finality or row.get("display_only") is not True
        or row.get("pit_safe") is not False
    ):
        raise ValueError("observation contract mismatch")
    source = _time(row.get("provider_timestamp_utc"))
    return {
        "source": source, "provider": spec.provider, "route": spec.route,
        "unit": spec.unit, "spec": spec,
    }


def _gate(source: datetime, now: datetime) -> tuple[bool, str]:
    if source > now:
        return False, "CURRENT_SOURCE_TIMESTAMP_FUTURE"
    if source.astimezone(KST).date() != now.astimezone(KST).date():
        return False, "CURRENT_SOURCE_DATE_NOT_TODAY_KST"
    if now - source > timedelta(minutes=60):
        return False, "CURRENT_SOURCE_AGE_OVER_60M"
    return True, "TODAY_KST_SOURCE_AGE_LE_60M"


def _blocked(row: dict[str, str], reason: str) -> dict[str, str]:
    changed = dict(row)
    changed.update({"current_numeric_visible": "false", "age_gate": reason, "status": "CURRENT_GATE_BLOCKED", "exact_reason": reason})
    return changed


def _apply_observation(row: dict[str, str], evidence: dict[str, object], now: datetime) -> dict[str, str]:
    spec = evidence["spec"]
    assert isinstance(spec, ObservationSpec)
    if spec.nxt_session_gate:
        nxt = classify_korean_equity_nxt_timestamp(
            provider_timestamp_utc=evidence["source"].isoformat(),  # type: ignore[union-attr]
            now_utc=now,
            session_start_kst=None,
            venue_inferred=spec.nxt_venue_inferred,
        )
        allowed, gate, status = nxt.allow_value, nxt.reason, nxt.freshness
        reason = (
            f"{gate}; visible label={nxt.visible_label}; route-local inferred close, "
            "not provider-declared venue/session and NOT_LIVE."
            if allowed else gate
        )
    else:
        allowed, gate, status = (*_gate(evidence["source"], now), "CURRENT_PROVISIONAL")  # type: ignore[arg-type]
        reason = "Local exact observation passes today-KST/source-age<=60m gate." if allowed else gate
    changed = dict(row)
    changed.update({
        "current_numeric_visible": "true" if allowed else "false",
        "provider": str(evidence["provider"]), "route": str(evidence["route"]),
        "interval": "snapshot", "source_timestamp_kst": evidence["source"].astimezone(KST).isoformat(),  # type: ignore[union-attr]
        "age_gate": gate, "status": status if allowed else "CURRENT_GATE_BLOCKED",
        "unit": str(evidence["unit"]),
        "exact_reason": reason,
    })
    return changed


def _global_rows(root: Path, rows: dict[str, dict[str, str]], now: datetime) -> dict[str, dict[str, str]]:
    recovery_paths = {series: Path(root) / GLOBAL_UR232_ROOT / f"{series.lower()}.json" for series in GLOBAL_ROWS}
    if any(path.exists() for path in recovery_paths.values()):
        from stock_data.orchestration.global_market_60m_ur232_recovery import read_observation
        recovered: dict[str, dict[str, str]] = {}
        for series, (row_id, _provider, _route, unit) in GLOBAL_ROWS.items():
            try:
                observation = read_observation(root, series)
                expected = {
                    "USD_KRW_60M": ("GLOBAL_FX", "KRW=X", "KRW per USD"),
                    "UST2_FUTURES_60M": ("CBOT", "ZT=F", "provider native continuous futures price"),
                    "UST10_FUTURES_60M": ("CBOT", "ZN=F", "provider native continuous futures price"),
                    "UST30_FUTURES_60M": ("CBOT", "ZB=F", "provider native continuous futures price"),
                }[series]
                if (
                    observation.identity.dataset_id != "MARKET_PRICE_60M_CURRENT"
                    or (observation.identity.market, observation.identity.symbol, observation.unit) != expected
                    or observation.interval.value != "60m" or observation.finality.value != "AS_RETRIEVED"
                    or not observation.display_only or observation.pit_safe
                ):
                    raise ValueError("UR-232 observation contract differs")
                source = _time(observation.provider_timestamp_utc); allowed, gate = _gate(source, now)
                changed = dict(rows[row_id]); changed.update({
                    "current_numeric_visible": "true" if allowed else "false", "provider": "YAHOO retained Landing",
                    "route": observation.route_id, "interval": "60m", "source_timestamp_kst": source.astimezone(KST).isoformat(),
                    "age_gate": gate, "status": "CURRENT_RETAINED_LANDING_API_ZERO" if allowed else "CURRENT_GATE_BLOCKED",
                    "unit": observation.unit,
                    "exact_reason": "UR-232 retained Landing API-zero recovery passes today-KST/source-age<=60m gate." if allowed else gate,
                }); recovered[series] = changed
            except (OSError, ValueError, json.JSONDecodeError):
                recovered[series] = _blocked(rows[row_id], "GLOBAL60M_UR232_CURRENT_UNAVAILABLE_OR_INVALID")
        return recovered
    try:
        log = _read_json(root / GLOBAL_LOG_PATH)
        state = _read_json(root / GLOBAL_STATE_PATH)
        outcomes = log.get("series_terminal_outcomes")
        expected_series = set(GLOBAL_ROWS)
        if (
            log.get("status") == "FAIL" and isinstance(outcomes, list)
            and {(item.get("series_id"), item.get("outcome")) for item in outcomes if isinstance(item, dict)}
            == {(series, "SEMANTIC_FINALITY_REJECTION") for series in expected_series}
        ):
            reason = "GLOBAL60M_SEMANTIC_FINALITY_REJECTION: latest scheduler run was rejected by semantic/finality policy; prior source timestamp preserved."
            return {key: _blocked(rows[row_id], reason) for key, (row_id, _, _, _) in GLOBAL_ROWS.items()}
        if log.get("status") != "PASS" or state.get("status") != "PASS":
            raise ValueError("scheduler/state failure")
        latest_log = log.get("latest_bar_end_utc")
        latest_state = state.get("latest_bar_end_utc")
        if not isinstance(latest_log, dict) or latest_log != latest_state:
            raise ValueError("scheduler/state latest timestamps disagree")
    except (OSError, ValueError, json.JSONDecodeError):
        return {key: _blocked(rows[row_id], "GLOBAL60M_NO_CURRENT_PUBLICATION: last scheduler/state result failed or is invalid; prior source timestamp preserved.") for key, (row_id, _, _, _) in GLOBAL_ROWS.items()}
    result: dict[str, dict[str, str]] = {}
    for series, (row_id, provider, route, unit) in GLOBAL_ROWS.items():
        try:
            source = _time(latest_log.get(series))
            allowed, gate = _gate(source, now)
            changed = dict(rows[row_id])
            changed.update({"current_numeric_visible": "true" if allowed else "false", "provider": provider, "route": route, "interval": "60m", "source_timestamp_kst": source.astimezone(KST).isoformat(), "age_gate": gate, "status": "CURRENT_DELAYED_60M" if allowed else "CURRENT_GATE_BLOCKED", "unit": unit, "exact_reason": "Exact global60m scheduler/state source timestamp passes today-KST/source-age<=60m gate." if allowed else gate})
            result[series] = changed
        except (TypeError, ValueError):
            result[series] = _blocked(rows[row_id], "GLOBAL60M_NO_CURRENT_PUBLICATION: latest source timestamp is invalid; prior source timestamp preserved.")
    return result


def _parse_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise ValueError("readiness CSV header differs from the 17-column contract")
        rows = list(reader)
    if len(rows) != 64 or any(set(row) != set(CSV_FIELDS) for row in rows) or len({row["surface_id"] for row in rows}) != 64:
        raise ValueError("readiness CSV row/ID contract invalid")
    return rows, list(reader.fieldnames or ())


def _render(rows: list[dict[str, str]], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _atomic_write(path: Path, content: bytes, *, replace: Callable[[str, str], None] = os.replace) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        replace(str(temporary), str(path))
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def _require_production_authorization(root: Path, csv_path: Path, *, confirmed: bool) -> None:
    """Fail closed unless a future exact runbook and manifest activate production."""
    if not confirmed:
        raise PermissionError("production projector requires explicit confirm flag")
    if csv_path != CSV_PATH:
        raise PermissionError("production projector requires the owned canonical CSV path")
    canonical = (root / CSV_PATH).resolve()
    if (root / csv_path).resolve() != canonical:
        raise PermissionError("production projector path ownership mismatch")
    try:
        manifest = _read_json(root / PRODUCTION_MANIFEST_PATH)
        runbook = (root / RUNBOOK_PATH).read_text(encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise PermissionError("production projector activation manifest/runbook is unavailable") from error
    expected = {
        "schema_version": 1,
        "operation_id": "UR-225",
        "status": ACTIVE_PRODUCTION_STATUS,
        "owned_csv_path": CSV_PATH.as_posix(),
        "active_runbook": RUNBOOK_PATH.as_posix(),
        "allow_production": True,
    }
    if manifest == expected and f"Status: `{ACTIVE_PRODUCTION_STATUS}`" in runbook:
        return
    try:
        manifest = _read_json(root / UR233_PRODUCTION_MANIFEST_PATH)
        runbook = (root / UR233_RUNBOOK_PATH).read_text(encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise PermissionError("production projector activation is inactive or mismatched") from error
    ur233_expected = {
        "schema_version": 1, "operation_id": "UR-233", "status": ACTIVE_PRODUCTION_STATUS,
        "owned_csv_path": CSV_PATH.as_posix(), "active_runbook": UR233_RUNBOOK_PATH.as_posix(),
        "allow_production": True,
    }
    if manifest != ur233_expected or f"Status: `{ACTIVE_PRODUCTION_STATUS}`" not in runbook:
        try:
            manifest = _read_json(root / UR242_PRODUCTION_MANIFEST_PATH)
            runbook = (root / UR242_RUNBOOK_PATH).read_text(encoding="utf-8")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise PermissionError("production projector activation is inactive or mismatched") from error
        ur242_expected = {
            "schema_version": 1, "operation_id": "UR-242", "status": ACTIVE_PRODUCTION_STATUS,
            "owned_csv_path": CSV_PATH.as_posix(), "active_runbook": UR242_RUNBOOK_PATH.as_posix(),
            "allow_production": True,
        }
        if manifest != ur242_expected or f"Status: `{ACTIVE_PRODUCTION_STATUS}`" not in runbook:
            raise PermissionError("production projector activation is inactive or mismatched")


def project(root: Path, *, now: datetime, csv_path: Path = CSV_PATH, production_confirmed: bool = False, backup_path: Path | None = None, replace: Callable[[str, str], None] = os.replace) -> dict[str, object]:
    """Project only allowlisted local states; caller controls the output path."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("audit clock must be timezone-aware")
    root = Path(root)
    if csv_path == CSV_PATH:
        _require_production_authorization(root, csv_path, confirmed=production_confirmed)
    path = root / csv_path; preimage = path.read_bytes(); rows, fields = _parse_csv(path)
    by_id = {row["surface_id"]: row for row in rows}
    changed: dict[str, dict[str, str]] = {}
    for spec in OBSERVATIONS:
        try:
            evidence = _observation(root, spec)
            updates = [_apply_observation(by_id[row_id], evidence, now) for row_id in spec.rows]
        except (OSError, ValueError, json.JSONDecodeError):
            updates = [_blocked(by_id[row_id], "CURRENT_OBSERVATION_UNAVAILABLE_OR_INVALID") for row_id in spec.rows]
        changed.update(dict(zip(spec.rows, updates)))
    changed.update({value["surface_id"]: value for value in _global_rows(root, by_id, now).values()})
    projected = [changed.get(row["surface_id"], row) for row in rows]
    # Only explicitly dynamic cells may differ; every non-allowlisted row remains exact.
    allowed_ids = set(changed)
    for before, after in zip(rows, projected):
        if before["surface_id"] not in allowed_ids and before != after:
            raise RuntimeError("unallowlisted readiness row changed")
        if before["surface_id"] in allowed_ids and any(before[key] != after[key] for key in before if key not in DYNAMIC_FIELDS):
            raise RuntimeError("static readiness field changed")
    content = _render(projected, fields)
    # Validate proposed bytes before replace, then validate durable readback.
    probe = path.with_name(f".{path.name}.probe.{uuid.uuid4().hex}")
    try:
        probe.write_bytes(content); _parse_csv(probe)
    finally:
        try: probe.unlink()
        except FileNotFoundError: pass
    if backup_path is not None:
        backup = root / backup_path
        if backup.exists():
            raise FileExistsError("readiness CSV preimage backup already exists")
        backup.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(backup, preimage, replace=replace)
    _atomic_write(path, content, replace=replace)
    readback, _ = _parse_csv(path)
    if readback != projected:
        raise RuntimeError("atomic readiness CSV readback differs")
    return {"status": "PROJECTED_API_ZERO", "changed_ids": sorted(allowed_ids), "audit_at_utc": now.astimezone(timezone.utc).isoformat()}


__all__ = ["ACTIVE_PRODUCTION_STATUS", "CSV_PATH", "GLOBAL_LOG_PATH", "GLOBAL_STATE_PATH", "OBSERVATIONS", "PRODUCTION_MANIFEST_PATH", "RUNBOOK_PATH", "UR242_PRODUCTION_MANIFEST_PATH", "UR242_RUNBOOK_PATH", "project"]
