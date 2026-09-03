"""Landing-first BOK ECOS daily USD/KRW provider adapter.

Table ``731Y001``, cycle ``D``, item ``0000001``, ``INFO-200``, and the row
fields parsed below come from the user-supplied, verified 2026-09-03 source
specification. The official guide host was reachable, but its client-rendered
specification could not be opened in this environment, so those guide details,
the publication clock, holiday calendar, revision behavior, and finality are
UNVERIFIED in this implementation session and are not inferred by this adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import pandas as pd
import requests

from stock_data.contracts.bok_ecos_fx import BOK_ECOS_USD_KRW_DAILY
from stock_data.validation.data_v1 import validate_data_v1


BASE_URL = "https://ecos.bok.or.kr/api"
OPERATION = "StatisticSearch"
STAT_CODE = "731Y001"
CYCLE = "D"
ITEM_CODE = "0000001"
ITEM_NAME = "원/미국달러(매매기준율)"
SOURCE = "BOK_ECOS"
MAX_ROWS = 400
MAX_WINDOW_DAYS = 400
TIMEOUT_SECONDS = 30
INFO_NO_DATA = "INFO-200"


class BokEcosFxProviderError(RuntimeError):
    """Fail-closed provider, Landing, or response-contract failure."""


@dataclass(frozen=True)
class ParsedFxResponse:
    frame: pd.DataFrame
    result_code: str

    @property
    def no_data(self) -> bool:
        return self.result_code == INFO_NO_DATA


@dataclass(frozen=True)
class CapturedFxResponse:
    frame: pd.DataFrame
    result_code: str
    run_id: str
    run_dir: Path
    response_sha256: str
    api_calls: int = 1


def _compact_date(value: date) -> str:
    if not isinstance(value, date):
        raise TypeError("request boundary must be a date")
    return value.strftime("%Y%m%d")


def validate_window(start: date, end: date) -> None:
    if start > end:
        raise ValueError("start must not be after end")
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError("BOK ECOS FX window must be at most 400 calendar days")


def request_url(api_key: str, start: date, end: date) -> str:
    """Build the credential-bearing URL; callers must never log this value."""
    validate_window(start, end)
    if not api_key:
        raise BokEcosFxProviderError("BOK_ECOS_API_KEY is required")
    return (
        f"{BASE_URL}/{OPERATION}/{quote(api_key, safe='')}/json/kr/1/{MAX_ROWS}/"
        f"{STAT_CODE}/{CYCLE}/{_compact_date(start)}/{_compact_date(end)}/{ITEM_CODE}"
    )


def redacted_route(start: date, end: date) -> str:
    validate_window(start, end)
    return (
        f"/api/{OPERATION}/<redacted>/json/kr/1/{MAX_ROWS}/{STAT_CODE}/{CYCLE}/"
        f"{_compact_date(start)}/{_compact_date(end)}/{ITEM_CODE}"
    )


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BOK_ECOS_USD_KRW_DAILY.column_names)


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
    start: date,
    end: date,
    retrieved_at: datetime,
) -> ParsedFxResponse:
    """Parse one exact StatisticSearch response without performing I/O."""
    validate_window(start, end)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    if body.lstrip().startswith(b"<"):
        raise BokEcosFxProviderError("ECOS response is HTML, not JSON")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BokEcosFxProviderError("ECOS response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise BokEcosFxProviderError("ECOS response root must be an object")

    result = _result_block(payload)
    if result is not None:
        code = str(result.get("CODE", "")).strip()
        if code == INFO_NO_DATA:
            return ParsedFxResponse(_empty_frame(), code)
        raise BokEcosFxProviderError(f"ECOS returned result code {code or 'UNKNOWN'}")

    block = payload.get(OPERATION)
    if not isinstance(block, dict):
        raise BokEcosFxProviderError("StatisticSearch response block is missing")
    rows = block.get("row")
    if not isinstance(rows, list) or not rows:
        raise BokEcosFxProviderError("StatisticSearch rows are missing")
    try:
        declared = int(block["list_total_count"])
    except (KeyError, TypeError, ValueError) as error:
        raise BokEcosFxProviderError("StatisticSearch total count is invalid") from error
    if declared != len(rows) or declared > MAX_ROWS:
        raise BokEcosFxProviderError("StatisticSearch response is truncated or oversized")

    output: list[dict[str, object]] = []
    seen: set[date] = set()
    required = {
        "TIME", "DATA_VALUE", "ITEM_CODE1", "ITEM_NAME1", "UNIT_NAME",
        "STAT_CODE", "STAT_NAME",
    }
    for row in rows:
        if not isinstance(row, dict) or not required.issubset(row):
            raise BokEcosFxProviderError("StatisticSearch row is malformed")
        if str(row["STAT_CODE"]).strip() != STAT_CODE:
            raise BokEcosFxProviderError("StatisticSearch table identity differs")
        if str(row["ITEM_CODE1"]).strip() != ITEM_CODE:
            raise BokEcosFxProviderError("StatisticSearch item identity differs")
        if str(row["ITEM_NAME1"]).strip() != ITEM_NAME:
            raise BokEcosFxProviderError("StatisticSearch item name differs")
        unit = str(row["UNIT_NAME"]).strip()
        stat_name = str(row["STAT_NAME"]).strip()
        if not unit or not stat_name:
            raise BokEcosFxProviderError("StatisticSearch source labels are empty")
        token = str(row["TIME"]).strip()
        try:
            observed = datetime.strptime(token, "%Y%m%d").date()
        except ValueError as error:
            raise BokEcosFxProviderError("StatisticSearch TIME is not YYYYMMDD") from error
        if observed < start or observed > end or observed in seen:
            raise BokEcosFxProviderError("StatisticSearch date is out of range or duplicated")
        seen.add(observed)
        try:
            rate = float(str(row["DATA_VALUE"]).replace(",", "").strip())
        except ValueError as error:
            raise BokEcosFxProviderError("StatisticSearch rate is not numeric") from error
        if not math.isfinite(rate) or rate <= 0:
            raise BokEcosFxProviderError("StatisticSearch rate is not a finite positive value")
        output.append({
            "date": observed,
            "rate_krw_per_usd": rate,
            "item_code": ITEM_CODE,
            "stat_code": STAT_CODE,
            "unit": unit,
            "source": SOURCE,
            "source_operation": OPERATION,
            "retrieved_at": pd.Timestamp(retrieved_at).tz_convert("UTC"),
        })
    frame = pd.DataFrame(output, columns=BOK_ECOS_USD_KRW_DAILY.column_names)
    frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
    validate_data_v1(frame, BOK_ECOS_USD_KRW_DAILY, allow_empty=False)
    return ParsedFxResponse(frame, "SUCCESS")


def _write_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise BokEcosFxProviderError(f"immutable Landing file already exists: {path.name}") from error


def _json_bytes(payload: dict[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def capture_range(
    project_root: Path,
    *,
    start: date,
    end: date,
    api_key: str,
    session: Any | None = None,
    retrieved_at: datetime | None = None,
) -> CapturedFxResponse:
    """Make one retry-zero request and retain immutable Landing before parsing."""
    validate_window(start, end)
    if not api_key:
        raise BokEcosFxProviderError("BOK_ECOS_API_KEY is required")
    captured_at = retrieved_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    run_id = captured_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = (
        Path(project_root).resolve() / "data/landing/bok_ecos_usd_krw_daily" / f"run_{run_id}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    route = redacted_route(start, end)
    transport = session or requests.Session()
    try:
        response = transport.get(
            request_url(api_key, start, end), timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        _write_new(run_dir / "call_ledger.jsonl", _json_bytes({
            "event": "HTTP_ERROR", "sequence": 1, "operation": OPERATION,
            "route": route, "retry_count": 0,
        }))
        raise BokEcosFxProviderError("ECOS request failed") from error

    body = bytes(response.content)
    if api_key.encode("utf-8") in body:
        _write_new(run_dir / "call_ledger.jsonl", _json_bytes({
            "event": "SECRET_RESPONSE_BLOCKED", "sequence": 1,
            "operation": OPERATION, "route": route, "retry_count": 0,
        }))
        raise BokEcosFxProviderError("ECOS response was blocked by secret-safety validation")
    response_sha256 = hashlib.sha256(body).hexdigest()
    _write_new(run_dir / "response.json", body)
    if hashlib.sha256((run_dir / "response.json").read_bytes()).hexdigest() != response_sha256:
        raise BokEcosFxProviderError("Landing response read-back hash differs")
    _write_new(run_dir / "call_ledger.jsonl", _json_bytes({
        "event": "HTTP_RESPONSE", "sequence": 1, "operation": OPERATION,
        "route": route, "status_code": int(response.status_code),
        "response_bytes": len(body), "response_sha256": response_sha256,
        "retrieved_at": captured_at.astimezone(timezone.utc).isoformat(),
        "retry_count": 0,
    }))
    if int(response.status_code) != 200:
        _write_new(run_dir / "manifest.json", _json_bytes({
            "schema_version": 1, "dataset": BOK_ECOS_USD_KRW_DAILY.name,
            "status": "HTTP_FAILED", "start": start.isoformat(), "end": end.isoformat(),
            "response_sha256": response_sha256, "api_calls": 1, "retry_count": 0,
        }))
        raise BokEcosFxProviderError(f"ECOS HTTP status {int(response.status_code)}")
    try:
        parsed = parse_response(body, start=start, end=end, retrieved_at=captured_at)
    except BokEcosFxProviderError:
        _write_new(run_dir / "manifest.json", _json_bytes({
            "schema_version": 1, "dataset": BOK_ECOS_USD_KRW_DAILY.name,
            "status": "VALIDATION_FAILED", "start": start.isoformat(), "end": end.isoformat(),
            "response_sha256": response_sha256, "api_calls": 1, "retry_count": 0,
        }))
        raise
    manifest = {
        "schema_version": 1,
        "dataset": BOK_ECOS_USD_KRW_DAILY.name,
        "contract_version": BOK_ECOS_USD_KRW_DAILY.version,
        "source_operation": OPERATION,
        "stat_code": STAT_CODE,
        "cycle": CYCLE,
        "item_code": ITEM_CODE,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "status": "NO_DATA" if parsed.no_data else "VALIDATED",
        "result_code": parsed.result_code,
        "rows": len(parsed.frame),
        "response_sha256": response_sha256,
        "api_calls": 1,
        "retry_count": 0,
    }
    _write_new(run_dir / "manifest.json", _json_bytes(manifest))
    if json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")) != manifest:
        raise BokEcosFxProviderError("Landing manifest read-back differs")
    return CapturedFxResponse(
        parsed.frame, parsed.result_code, run_id, run_dir, response_sha256,
    )


__all__ = [
    "BokEcosFxProviderError", "CapturedFxResponse", "ParsedFxResponse",
    "CYCLE", "INFO_NO_DATA", "ITEM_CODE", "ITEM_NAME", "MAX_WINDOW_DAYS",
    "OPERATION", "SOURCE", "STAT_CODE", "capture_range", "parse_response",
    "redacted_route", "request_url", "validate_window",
]
