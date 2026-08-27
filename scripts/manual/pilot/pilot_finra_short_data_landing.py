"""Run exactly one official FINRA request per short-data source family.

Landing-only bounded pilot.  No normalized or canonical output is produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from stock_data.providers.finra import (  # noqa: E402
    FinraSchemaError,
    parse_daily_short_sale_volume,
    parse_short_interest,
    target_daily_rows,
    target_short_interest_rows,
)


TARGET_SYMBOLS = frozenset({"SPY", "QQQ", "TQQQ", "AAPL", "NVDA"})
DAILY_URL_TEMPLATE = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{trade_date}.txt"
SHORT_INTEREST_URL = "https://api.finra.org/data/group/otcMarket/name/EquityShortInterest"
LANDING_ROOT = ROOT / "data" / "landing" / "finra"
STATE_ROOT = ROOT / "data" / "state"
FAMILY_CONFIG = {
    "daily_short_sale_volume": {
        "landing_name": "daily_short_sale_volume_pilot",
        "state_name": "us_finra_daily_short_sale_volume_pilot",
    },
    "short_interest": {
        "landing_name": "short_interest_pilot",
        "state_name": "us_finra_short_interest_pilot",
    },
}


class PilotStopped(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_new(path: Path, content: bytes) -> None:
    if path.exists():
        raise PilotStopped(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _write_json_new(path: Path, payload: dict[str, Any]) -> None:
    _atomic_new(path, (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _request_daily(session: requests.Session, trade_date: str) -> tuple[str, requests.Response]:
    url = DAILY_URL_TEMPLATE.format(trade_date=trade_date)
    response = session.get(url, headers={"User-Agent": "stock-investment-rev1 FINRA landing pilot"}, timeout=30)
    return url, response


def _request_short_interest(session: requests.Session, settlement_date: str) -> tuple[str, requests.Response, dict[str, object]]:
    payload: dict[str, object] = {
        "compareFilters": [{"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": settlement_date}],
        "limit": 5000,
    }
    response = session.post(
        SHORT_INTEREST_URL,
        headers={
            "User-Agent": "stock-investment-rev1 FINRA landing pilot",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=30,
    )
    return SHORT_INTEREST_URL, response, payload


def _capture(
    run_dir: Path,
    family: str,
    raw: bytes,
    response: requests.Response,
    source_url: str,
    request_payload: object | None,
    parsed: object,
    target_rows: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    family_dir = run_dir / family
    body_path = family_dir / "response.body"
    _atomic_new(body_path, raw)
    provenance = {
        "source_family": family,
        "source_operation": response.request.method,
        "source_url": source_url,
        "request_payload": request_payload,
        "http_status": response.status_code,
        "response_content_type": response.headers.get("Content-Type"),
        "response_headers": {key: value for key, value in response.headers.items()},
        "collected_at": _now(),
        "body_bytes": len(raw),
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "schema_sha256": parsed.schema_sha256,
        "source_schema": list(parsed.columns if hasattr(parsed, "columns") else parsed.fields),
        "raw_total_rows": len(parsed.rows),
        "target_symbols": sorted(TARGET_SYMBOLS),
        "target_rows": list(target_rows),
    }
    _write_json_new(family_dir / "provenance.json", provenance)
    return {
        "source_family": family,
        "body_file": body_path.relative_to(run_dir).as_posix(),
        "provenance_file": (family_dir / "provenance.json").relative_to(run_dir).as_posix(),
        "body_sha256": provenance["body_sha256"],
        "schema_sha256": provenance["schema_sha256"],
        "raw_total_rows": provenance["raw_total_rows"],
        "target_row_count": len(target_rows),
        "target_symbols_returned": sorted({
            row.get("Symbol", row.get("issueSymbolIdentifier")) for row in target_rows
        }),
    }


def _capture_unvalidated(
    run_dir: Path,
    family: str,
    raw: bytes,
    response: requests.Response,
    source_url: str,
    request_payload: object | None,
    validation_error: str,
) -> dict[str, Any]:
    """Retain validly received source bytes even when validation fails closed."""
    family_dir = run_dir / family
    body_path = family_dir / "response.body"
    _atomic_new(body_path, raw)
    provenance = {
        "source_family": family,
        "source_operation": response.request.method,
        "source_url": source_url,
        "request_payload": request_payload,
        "http_status": response.status_code,
        "response_content_type": response.headers.get("Content-Type"),
        "response_headers": {key: value for key, value in response.headers.items()},
        "collected_at": _now(),
        "body_bytes": len(raw),
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "validation_status": "FAILED_CLOSED",
        "validation_error": validation_error,
    }
    _write_json_new(family_dir / "provenance.json", provenance)
    return {
        "source_family": family,
        "body_file": body_path.relative_to(run_dir).as_posix(),
        "provenance_file": (family_dir / "provenance.json").relative_to(run_dir).as_posix(),
        "body_sha256": provenance["body_sha256"],
        "validation_status": "FAILED_CLOSED",
    }


def run(
    *, family: str, trade_date: str, settlement_date: str,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    if family not in FAMILY_CONFIG:
        raise PilotStopped(f"unsupported source family: {family}")
    if not (trade_date.isdigit() and len(trade_date) == 8):
        raise PilotStopped("trade_date must be YYYYMMDD")
    if not (len(settlement_date) == 10 and settlement_date[4] == "-" and settlement_date[7] == "-"):
        raise PilotStopped("settlement_date must be YYYY-MM-DD")
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex}"
    family_config = FAMILY_CONFIG[family]
    run_dir = LANDING_ROOT / family_config["landing_name"] / run_id
    if run_dir.exists():
        raise PilotStopped("generated run directory already exists")
    active = session or requests.Session()
    captures: list[dict[str, Any]] = []
    status = "PILOT_COMPLETE_WITH_LIMITS"
    stop_reason: str | None = None
    try:
        if family == "daily_short_sale_volume":
            source_url, response = _request_daily(active, trade_date)
            request_payload = None
            parser = parse_daily_short_sale_volume
            selector = target_daily_rows
        else:
            source_url, response, request_payload = _request_short_interest(active, settlement_date)
            parser = parse_short_interest
            selector = target_short_interest_rows
        if response.status_code != 200:
            captures.append(_capture_unvalidated(
                run_dir, family, response.content, response, source_url, request_payload,
                f"HTTP_{response.status_code}",
            ))
            if response.status_code in {403, 429}:
                raise PilotStopped(f"{family.upper()}_ACCESS_RESTRICTED_HTTP_{response.status_code}")
            raise PilotStopped(f"{family.upper()}_HTTP_{response.status_code}")
        try:
            parsed = parser(response.content)
        except FinraSchemaError as exc:
            captures.append(_capture_unvalidated(
                run_dir, family, response.content, response, source_url, request_payload, str(exc),
            ))
            raise
        target_rows = selector(parsed, set(TARGET_SYMBOLS))
        captures.append(_capture(
            run_dir, family, response.content, response, source_url, request_payload, parsed, target_rows,
        ))
        if not target_rows:
            status = "PILOT_COMPLETE_WITH_LIMITS"
            stop_reason = "TARGET_SYMBOL_SUBSET_INCOMPLETE"
    except FinraSchemaError as exc:
        status = "PILOT_STOPPED_SCHEMA_ANOMALY"
        stop_reason = str(exc)
    except (requests.RequestException, PilotStopped) as exc:
        status = "SOURCE_BLOCKED"
        stop_reason = str(exc)
    manifest = {
        "run_id": run_id,
        "source_family": family,
        "landing_only": True,
        "normalized_or_canonical_created": False,
        "retry_count": 0,
        "target_symbols": sorted(TARGET_SYMBOLS),
        "trade_date": trade_date,
        "settlement_date": settlement_date,
        "status": status,
        "pit_status": "PIT_BLOCKED",
        "stop_reason": stop_reason,
        "captures": captures,
        "created_at": _now(),
    }
    _write_json_new(run_dir / "manifest.json", manifest)
    state_path = STATE_ROOT / family_config["state_name"] / "latest.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_state = state_path.with_name(f".{state_path.name}.{uuid.uuid4().hex}.tmp")
    temp_state.write_text(json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp_state, state_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Landing-only bounded FINRA short-data pilot")
    parser.add_argument("--family", choices=sorted(FAMILY_CONFIG), required=True)
    parser.add_argument("--trade-date", required=True, help="Daily short volume date as YYYYMMDD")
    parser.add_argument("--settlement-date", required=True, help="Short interest settlement date as YYYY-MM-DD")
    parser.add_argument("--confirm-live-landing-only", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_landing_only:
        parser.error("--confirm-live-landing-only is required")
    result = run(family=args.family, trade_date=args.trade_date, settlement_date=args.settlement_date)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result["status"] != "SOURCE_BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
