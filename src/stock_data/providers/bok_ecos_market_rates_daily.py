"""Landing-first BOK ECOS Korean daily market-rate provider.

The requested ``721Y001`` metadata table was inspected on 2026-09-06.  Its
item list exposes only A/M/Q cycles and ``721Y001/D`` returns ``INFO-200``.
The provider therefore uses ECOS's actual daily table ``817Y002`` and validates
the exact item identities returned by its ``StatisticSearch`` responses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pandas as pd
import requests

from stock_data.contracts.bok_ecos_market_rates import (
    BOK_ECOS_KR_MARKET_RATE_DAILY,
)
from stock_data.validation.data_v1 import validate_data_v1


BASE_URL = "https://ecos.bok.or.kr/api"
OPERATION = "StatisticSearch"
REQUESTED_STAT_CODE = "721Y001"
STAT_CODE = "817Y002"
STAT_NAME = "1.3.2.1. 시장금리(일별)"
CYCLE = "D"
SOURCE = "BOK_ECOS"
MAX_ROWS = 400
MAX_WINDOW_DAYS = 400
TIMEOUT_SECONDS = 30
INFO_NO_DATA = "INFO-200"

# Exact public metadata returned by StatisticItemList/.../721Y001.  These codes
# are retained as source evidence only because that table has no daily cycle.
REQUESTED_TABLE_ITEM_LIST = (
    ("7020000", "회사채(3년, AA-)"),
    ("1010000", "무담보콜금리(1일)"),
    ("1020000", "무담보콜금리 전체"),
)

SERIES_SPECS = MappingProxyType({
    "CORP_BOND_3Y_AA_MINUS": MappingProxyType({
        "item_code": "010300000",
        "item_name": "회사채(3년, AA-)",
    }),
    "CALL_RATE_OVERNIGHT": MappingProxyType({
        "item_code": "010101000",
        "item_name": "콜금리(1일, 전체거래)",
    }),
})


class BokEcosMarketRatesProviderError(RuntimeError):
    """Fail-closed transport, Landing, or response-contract failure."""


@dataclass(frozen=True)
class ParsedMarketRateResponse:
    frame: pd.DataFrame
    result_code: str

    @property
    def no_data(self) -> bool:
        return self.result_code == INFO_NO_DATA


@dataclass(frozen=True)
class CapturedMarketRateResponse:
    frame: pd.DataFrame
    result_code: str
    series: str
    run_id: str
    run_dir: Path
    response_sha256: str
    api_calls: int = 1


def _spec(series: str) -> MappingProxyType:
    try:
        return SERIES_SPECS[series]
    except KeyError as error:
        raise ValueError(f"unknown BOK market-rate series: {series}") from error


def _compact_date(value: date) -> str:
    if not isinstance(value, date):
        raise TypeError("request boundary must be a date")
    return value.strftime("%Y%m%d")


def validate_window(start: date, end: date) -> None:
    if start > end:
        raise ValueError("start must not be after end")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError("BOK ECOS market-rate window must be at most 400 calendar days")


def request_url(api_key: str, series: str, start: date, end: date) -> str:
    """Build the credential-bearing URL; callers must never log this value."""
    validate_window(start, end)
    if not api_key:
        raise BokEcosMarketRatesProviderError("BOK_ECOS_API_KEY is required")
    item_code = str(_spec(series)["item_code"])
    return (
        f"{BASE_URL}/{OPERATION}/{quote(api_key, safe='')}/json/kr/1/{MAX_ROWS}/"
        f"{STAT_CODE}/{CYCLE}/{_compact_date(start)}/{_compact_date(end)}/{item_code}"
    )


def redacted_route(series: str, start: date, end: date) -> str:
    validate_window(start, end)
    item_code = str(_spec(series)["item_code"])
    return (
        f"/api/{OPERATION}/<redacted>/json/kr/1/{MAX_ROWS}/{STAT_CODE}/{CYCLE}/"
        f"{_compact_date(start)}/{_compact_date(end)}/{item_code}"
    )


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BOK_ECOS_KR_MARKET_RATE_DAILY.column_names)


def _result_block(payload: dict[str, Any]) -> dict[str, Any] | None:
    direct = payload.get("RESULT")
    if isinstance(direct, dict):
        return direct
    operation = payload.get(OPERATION)
    if isinstance(operation, dict) and isinstance(operation.get("RESULT"), dict):
        return operation["RESULT"]
    return None


def parse_response(
    body: bytes,
    *,
    series: str,
    start: date,
    end: date,
    retrieved_at: datetime,
) -> ParsedMarketRateResponse:
    """Parse one exact-series StatisticSearch response without I/O."""
    spec = _spec(series)
    validate_window(start, end)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    if body.lstrip().startswith(b"<"):
        raise BokEcosMarketRatesProviderError("ECOS response is HTML, not JSON")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BokEcosMarketRatesProviderError("ECOS response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise BokEcosMarketRatesProviderError("ECOS response root must be an object")

    result = _result_block(payload)
    if result is not None:
        code = str(result.get("CODE", "")).strip()
        if code == INFO_NO_DATA:
            return ParsedMarketRateResponse(_empty_frame(), code)
        raise BokEcosMarketRatesProviderError(
            f"ECOS returned result code {code or 'UNKNOWN'}"
        )

    block = payload.get(OPERATION)
    if not isinstance(block, dict):
        raise BokEcosMarketRatesProviderError("StatisticSearch response block is missing")
    rows = block.get("row")
    if not isinstance(rows, list) or not rows:
        raise BokEcosMarketRatesProviderError("StatisticSearch rows are missing")
    try:
        declared = int(block["list_total_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise BokEcosMarketRatesProviderError(
            "StatisticSearch total count is invalid"
        ) from error
    if declared != len(rows) or declared > MAX_ROWS:
        raise BokEcosMarketRatesProviderError(
            "StatisticSearch response is truncated or oversized"
        )

    output: list[dict[str, object]] = []
    seen: set[date] = set()
    required = {
        "TIME", "DATA_VALUE", "ITEM_CODE1", "ITEM_NAME1", "UNIT_NAME",
        "STAT_CODE", "STAT_NAME",
    }
    for row in rows:
        if not isinstance(row, dict) or not required.issubset(row):
            raise BokEcosMarketRatesProviderError("StatisticSearch row is malformed")
        if str(row["STAT_CODE"]).strip() != STAT_CODE:
            raise BokEcosMarketRatesProviderError("StatisticSearch table identity differs")
        if str(row["STAT_NAME"]).strip() != STAT_NAME:
            raise BokEcosMarketRatesProviderError("StatisticSearch table name differs")
        if str(row["ITEM_CODE1"]).strip() != spec["item_code"]:
            raise BokEcosMarketRatesProviderError("StatisticSearch item identity differs")
        if str(row["ITEM_NAME1"]).strip() != spec["item_name"]:
            raise BokEcosMarketRatesProviderError("StatisticSearch item name differs")
        unit = str(row["UNIT_NAME"]).strip()
        if unit != "연%":
            raise BokEcosMarketRatesProviderError("StatisticSearch unit differs")
        token = str(row["TIME"]).strip()
        try:
            observed = datetime.strptime(token, "%Y%m%d").date()
        except ValueError as error:
            raise BokEcosMarketRatesProviderError(
                "StatisticSearch TIME is not YYYYMMDD"
            ) from error
        if observed < start or observed > end or observed in seen:
            raise BokEcosMarketRatesProviderError(
                "StatisticSearch date is out of range or duplicated"
            )
        seen.add(observed)
        try:
            rate = float(str(row["DATA_VALUE"]).replace(",", "").strip())
        except ValueError as error:
            raise BokEcosMarketRatesProviderError(
                "StatisticSearch rate is not numeric"
            ) from error
        if not math.isfinite(rate) or rate < 0:
            raise BokEcosMarketRatesProviderError(
                "StatisticSearch rate is not a finite nonnegative value"
            )
        output.append({
            "date": observed,
            "series": series,
            "rate_percent": rate,
            "item_code": spec["item_code"],
            "stat_code": STAT_CODE,
            "unit": unit,
            "source": SOURCE,
            "source_operation": OPERATION,
            "retrieved_at": pd.Timestamp(retrieved_at).tz_convert("UTC"),
        })
    frame = pd.DataFrame(
        output, columns=BOK_ECOS_KR_MARKET_RATE_DAILY.column_names,
    ).sort_values(["date", "series"], kind="stable").reset_index(drop=True)
    validate_data_v1(frame, BOK_ECOS_KR_MARKET_RATE_DAILY, allow_empty=False)
    return ParsedMarketRateResponse(frame, "SUCCESS")


def _write_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise BokEcosMarketRatesProviderError(
            f"immutable Landing file already exists: {path.name}"
        ) from error


def _json_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def capture_range(
    project_root: Path,
    *,
    series: str,
    start: date,
    end: date,
    api_key: str,
    session: Any | None = None,
    retrieved_at: datetime | None = None,
) -> CapturedMarketRateResponse:
    """Make one retry-zero request and retain immutable Landing before parsing."""
    _spec(series)
    validate_window(start, end)
    if not api_key:
        raise BokEcosMarketRatesProviderError("BOK_ECOS_API_KEY is required")
    captured_at = retrieved_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    run_id = captured_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = (
        Path(project_root).resolve()
        / "data/landing/bok_ecos/kr_market_rates_daily"
        / f"series={series}"
        / f"run_{run_id}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    route = redacted_route(series, start, end)
    transport = session or requests.Session()
    try:
        response = transport.get(
            request_url(api_key, series, start, end), timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        _write_new(run_dir / "call_ledger.jsonl", _json_bytes({
            "event": "HTTP_ERROR", "sequence": 1, "operation": OPERATION,
            "route": route, "retry_count": 0,
        }))
        raise BokEcosMarketRatesProviderError("ECOS request failed") from error

    body = bytes(response.content)
    if api_key.encode("utf-8") in body:
        _write_new(run_dir / "call_ledger.jsonl", _json_bytes({
            "event": "SECRET_RESPONSE_BLOCKED", "sequence": 1,
            "operation": OPERATION, "route": route, "retry_count": 0,
        }))
        raise BokEcosMarketRatesProviderError(
            "ECOS response was blocked by secret-safety validation"
        )
    response_sha256 = hashlib.sha256(body).hexdigest()
    _write_new(run_dir / "response.json", body)
    if hashlib.sha256((run_dir / "response.json").read_bytes()).hexdigest() != response_sha256:
        raise BokEcosMarketRatesProviderError("Landing response read-back hash differs")
    _write_new(run_dir / "call_ledger.jsonl", _json_bytes({
        "event": "HTTP_RESPONSE", "sequence": 1, "operation": OPERATION,
        "route": route, "status_code": int(response.status_code),
        "response_bytes": len(body), "response_sha256": response_sha256,
        "retrieved_at": captured_at.astimezone(timezone.utc).isoformat(),
        "retry_count": 0,
    }))
    manifest_base = {
        "schema_version": 1,
        "dataset": BOK_ECOS_KR_MARKET_RATE_DAILY.name,
        "contract_version": BOK_ECOS_KR_MARKET_RATE_DAILY.version,
        "series": series,
        "source_operation": OPERATION,
        "stat_code": STAT_CODE,
        "cycle": CYCLE,
        "item_code": str(_spec(series)["item_code"]),
        "item_name": str(_spec(series)["item_name"]),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "response_sha256": response_sha256,
        "api_calls": 1,
        "retry_count": 0,
    }
    if int(response.status_code) != 200:
        _write_new(run_dir / "manifest.json", _json_bytes({
            **manifest_base, "status": "HTTP_FAILED",
        }))
        raise BokEcosMarketRatesProviderError(
            f"ECOS HTTP status {int(response.status_code)}"
        )
    try:
        parsed = parse_response(
            body, series=series, start=start, end=end, retrieved_at=captured_at,
        )
    except BokEcosMarketRatesProviderError:
        _write_new(run_dir / "manifest.json", _json_bytes({
            **manifest_base, "status": "VALIDATION_FAILED",
        }))
        raise
    manifest = {
        **manifest_base,
        "status": "NO_DATA" if parsed.no_data else "VALIDATED",
        "result_code": parsed.result_code,
        "rows": len(parsed.frame),
    }
    _write_new(run_dir / "manifest.json", _json_bytes(manifest))
    if json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")) != manifest:
        raise BokEcosMarketRatesProviderError("Landing manifest read-back differs")
    return CapturedMarketRateResponse(
        parsed.frame, parsed.result_code, series, run_id, run_dir, response_sha256,
    )


__all__ = [
    "BokEcosMarketRatesProviderError", "CapturedMarketRateResponse",
    "ParsedMarketRateResponse", "CYCLE", "INFO_NO_DATA", "MAX_WINDOW_DAYS",
    "OPERATION", "REQUESTED_STAT_CODE", "REQUESTED_TABLE_ITEM_LIST",
    "SERIES_SPECS", "SOURCE", "STAT_CODE", "STAT_NAME", "capture_range",
    "parse_response", "redacted_route", "request_url", "validate_window",
]
