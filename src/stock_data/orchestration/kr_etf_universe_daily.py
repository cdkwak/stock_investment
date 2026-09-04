"""Landing-first current-list Korean ETF universe collection and promotion."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping

import pandas as pd

from stock_data.contracts.kr_etf import KR_ETF_UNIVERSE_DAILY
from stock_data.providers.pykrx.kr_etf_universe import (
    KrEtfUniverseProvider,
    PykrxKrEtfUniverseClient,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic


LANDING_ROOT = Path("data/landing/pykrx/kr_etf_universe_daily")
NORMALIZED_ROOT = Path("data/normalized/kr_etf_universe_daily")
SOURCE = "KRX/pykrx"
SOURCE_OPERATION = "ETF_전종목기본종목.fetch"
MARKET = "KRX"
SECURITY_TYPE = "ETF"
LISTING_STATUS = "LISTED_AT_SOURCE_DATE"
_SYMBOL = re.compile(r"[0-9A-Z]{6}\Z")


class KrEtfUniverseDailyError(RuntimeError):
    pass


def _optional_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_date(value: object, field: str) -> date | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.strptime(text, "%Y/%m/%d").date()
    except ValueError as error:
        raise KrEtfUniverseDailyError(f"KRX ETF {field} is invalid: {text}") from error
    return parsed


def normalize_kr_etf_universe(frame: pd.DataFrame, *, source_date: date) -> pd.DataFrame:
    required = {"ISU_SRT_CD", "ISU_ABBRV"}
    if not isinstance(frame, pd.DataFrame) or frame.empty or not required.issubset(frame.columns):
        raise KrEtfUniverseDailyError("KRX ETF universe is empty or missing identity columns")
    rows: list[dict[str, object]] = []
    for raw in frame.to_dict(orient="records"):
        symbol = str(raw.get("ISU_SRT_CD") or "").strip().upper()
        name = str(raw.get("ISU_ABBRV") or "").strip()
        if _SYMBOL.fullmatch(symbol) is None or not name:
            raise KrEtfUniverseDailyError("KRX ETF universe contains a malformed symbol or short name")
        rows.append({
            "source_date": source_date,
            "symbol": symbol,
            "name": name,
            "full_name": _optional_text(raw.get("ISU_NM")),
            "isin": _optional_text(raw.get("ISU_CD")),
            "listing_date": _optional_date(raw.get("LIST_DD"), "listing date"),
            "underlying_index": _optional_text(raw.get("ETF_OBJ_IDX_NM")),
            "market": MARKET,
            "security_type": SECURITY_TYPE,
            "listing_status": LISTING_STATUS,
            "source": SOURCE,
            "source_operation": SOURCE_OPERATION,
        })
    normalized = pd.DataFrame(rows, columns=KR_ETF_UNIVERSE_DAILY.column_names)
    normalized = normalized.sort_values(["source_date", "symbol"], kind="stable").reset_index(drop=True)
    validate_kr_etf_universe_daily(normalized)
    return normalized


def validate_kr_etf_universe_daily(frame: pd.DataFrame) -> None:
    if list(frame.columns) != list(KR_ETF_UNIVERSE_DAILY.column_names) or frame.empty:
        raise ValueError("Korean ETF universe schema is invalid or empty")
    if frame.duplicated(list(KR_ETF_UNIVERSE_DAILY.primary_key)).any():
        raise ValueError("Korean ETF universe contains duplicate source-date symbols")
    if not frame["symbol"].astype(str).str.fullmatch(r"[0-9A-Z]{6}").all():
        raise ValueError("Korean ETF universe symbol differs")
    required = frame[[
        "source_date", "symbol", "name", "market", "security_type",
        "listing_status", "source", "source_operation",
    ]]
    if required.isna().any().any() or frame["name"].astype(str).str.strip().eq("").any():
        raise ValueError("Korean ETF universe identity or provenance is incomplete")
    if not frame["market"].astype(str).eq(MARKET).all():
        raise ValueError("Korean ETF universe market differs")
    if not frame["security_type"].astype(str).eq(SECURITY_TYPE).all():
        raise ValueError("Korean ETF universe security type differs")
    if not frame["listing_status"].astype(str).eq(LISTING_STATUS).all():
        raise ValueError("Korean ETF universe listing status differs")
    if not frame["source"].astype(str).eq(SOURCE).all():
        raise ValueError("Korean ETF universe source differs")
    if not frame["source_operation"].astype(str).eq(SOURCE_OPERATION).all():
        raise ValueError("Korean ETF universe source operation differs")
    source_dates = pd.to_datetime(frame["source_date"], errors="coerce")
    listing_dates = pd.to_datetime(frame["listing_date"], errors="coerce")
    present = frame["listing_date"].notna()
    if source_dates.isna().any() or listing_dates[present].isna().any():
        raise ValueError("Korean ETF universe date differs")
    if (
        listing_dates[present].reset_index(drop=True)
        > source_dates[present].reset_index(drop=True)
    ).any():
        raise ValueError("Korean ETF universe listing date follows its source date")


def _json_scalar(value: object) -> object:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _landing_payload(frame: pd.DataFrame) -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": SOURCE,
        "source_operation": SOURCE_OPERATION,
        "columns": [str(value) for value in frame.columns],
        "index_name": None if frame.index.name is None else str(frame.index.name),
        "index": [_json_scalar(value) for value in frame.index.tolist()],
        "data": [
            [_json_scalar(value) for value in row]
            for row in frame.itertuples(index=False, name=None)
        ],
    }


def _frame_from_landing(payload: object) -> pd.DataFrame:
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise KrEtfUniverseDailyError("KRX ETF universe Landing schema differs")
    columns = payload.get("columns")
    data = payload.get("data")
    index = payload.get("index")
    if not isinstance(columns, list) or not all(isinstance(value, str) for value in columns):
        raise KrEtfUniverseDailyError("KRX ETF universe Landing columns differ")
    if not isinstance(data, list) or not isinstance(index, list) or len(data) != len(index):
        raise KrEtfUniverseDailyError("KRX ETF universe Landing row shape differs")
    if any(not isinstance(row, list) or len(row) != len(columns) for row in data):
        raise KrEtfUniverseDailyError("KRX ETF universe Landing row width differs")
    frame = pd.DataFrame(data, columns=columns, index=index)
    frame.index.name = payload.get("index_name") if isinstance(payload.get("index_name"), str) else None
    return frame


def _atomic_json_new(path: Path, payload: Mapping[str, object]) -> dict[str, object]:
    body = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise KrEtfUniverseDailyError(f"immutable ETF universe Landing exists: {path}")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        if path.exists():
            raise KrEtfUniverseDailyError(f"immutable ETF universe Landing appeared: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    observed = path.read_bytes()
    if observed != body:
        raise KrEtfUniverseDailyError("KRX ETF universe Landing read-back differs")
    return {"path": path.as_posix(), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def _read_landing(path: Path) -> pd.DataFrame:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise KrEtfUniverseDailyError("KRX ETF universe Landing cannot be read") from error
    return _frame_from_landing(payload)


def _retained_for_date(root: Path, source_date: date) -> pd.DataFrame | None:
    if not root.exists() or not any(root.rglob("data.parquet")):
        return None
    retained = read_dataset(root, KR_ETF_UNIVERSE_DAILY, validate_kr_etf_universe_daily)
    mask = pd.to_datetime(retained["source_date"], errors="raise").dt.date.eq(source_date)
    selected = retained.loc[mask].reset_index(drop=True)
    return selected if not selected.empty else None


def _merge_retained(root: Path, incoming: pd.DataFrame) -> pd.DataFrame:
    if not root.exists() or not any(root.rglob("data.parquet")):
        return incoming
    retained = read_dataset(root, KR_ETF_UNIVERSE_DAILY, validate_kr_etf_universe_daily)
    keys = ["source_date", "symbol"]
    if set(map(tuple, retained[keys].to_numpy())) & set(map(tuple, incoming[keys].to_numpy())):
        raise KrEtfUniverseDailyError("Korean ETF universe source date is already retained")
    merged = pd.concat([retained, incoming], ignore_index=True)
    return merged[list(KR_ETF_UNIVERSE_DAILY.column_names)].sort_values(keys, kind="stable").reset_index(drop=True)


def _comparable_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy(deep=True)
    for column in ("source_date", "listing_date"):
        converted = pd.to_datetime(result[column], errors="coerce")
        result[column] = converted.dt.strftime("%Y-%m-%d").where(converted.notna(), None)
    return result.where(pd.notna(result), None)


def run_kr_etf_universe_daily(
    project_root: Path,
    *,
    source_date: date,
    provider: KrEtfUniverseProvider | None = None,
) -> dict[str, object]:
    root = Path(project_root).resolve()
    normalized_root = root / NORMALIZED_ROOT
    retained = _retained_for_date(normalized_root, source_date)
    if retained is not None:
        return {
            "schema_version": 1, "dataset": KR_ETF_UNIVERSE_DAILY.name,
            "status": "ALREADY_CURRENT", "source_date": source_date.isoformat(),
            "rows": len(retained), "api_calls": 0, "retry_count": 0,
            "normalized_write": False,
        }

    landing_path = root / LANDING_ROOT / f"source_date={source_date.isoformat()}" / "response.json"
    api_calls = 0
    if landing_path.is_file():
        raw = _read_landing(landing_path)
        landing = {
            "path": landing_path.as_posix(), "bytes": landing_path.stat().st_size,
            "sha256": hashlib.sha256(landing_path.read_bytes()).hexdigest(),
        }
    else:
        active_provider = provider or PykrxKrEtfUniverseClient(
            manual=True, requested_days=1,
        )
        before = active_provider.request_count
        raw = active_provider.fetch()
        api_calls = active_provider.request_count - before
        if api_calls != 1:
            raise KrEtfUniverseDailyError("KRX ETF universe operation must make exactly one call")
        landing = _atomic_json_new(landing_path, _landing_payload(raw))
        raw = _read_landing(landing_path)

    normalized = normalize_kr_etf_universe(raw, source_date=source_date)
    merged = _merge_retained(normalized_root, normalized)
    write_dataset_atomic(
        merged, normalized_root, KR_ETF_UNIVERSE_DAILY,
        validate_kr_etf_universe_daily,
    )
    read_back = read_dataset(
        normalized_root, KR_ETF_UNIVERSE_DAILY,
        validate_kr_etf_universe_daily,
    )
    selected = read_back.loc[
        pd.to_datetime(read_back["source_date"], errors="raise").dt.date.eq(source_date)
    ].reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            _comparable_frame(normalized), _comparable_frame(selected), check_dtype=False,
        )
    except AssertionError as error:
        raise KrEtfUniverseDailyError("Korean ETF universe Normalized read-back differs") from error
    return {
        "schema_version": 1, "dataset": KR_ETF_UNIVERSE_DAILY.name,
        "status": "SUCCEEDED", "source_date": source_date.isoformat(),
        "rows": len(normalized), "api_calls": api_calls, "retry_count": 0,
        "landing": landing, "normalized_write": True,
        "predictive_use": False,
    }


__all__ = [
    "KrEtfUniverseDailyError", "normalize_kr_etf_universe",
    "run_kr_etf_universe_daily", "validate_kr_etf_universe_daily",
]
