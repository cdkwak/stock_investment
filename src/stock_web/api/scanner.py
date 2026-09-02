"""Full provider-free Korean equity scanner for the local web dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads

from stock_web.api.stocks_page import evaluate_conditions, load_conditions


SCANNER_CACHE_SCHEMA_VERSION = 1
BASE_RULE = "RSI14 ≤ 30 또는 60일선 대비 -20% 이하"


def _cache_path(project_root: Path) -> Path:
    return Path(project_root) / "artifacts/local_user/scanner_cache.json"


def _dataset_signature(root: Path) -> str:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.glob("market=*/year=*/*.parquet")):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        count += 1
    digest.update(str(count).encode("ascii"))
    return digest.hexdigest()


def _conditions_key(conditions: Iterable[dict[str, object]]) -> str:
    encoded = json.dumps(list(conditions), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_cache(path: Path, *, price_signature: str, conditions_key: str) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    result = payload.get("result") if isinstance(payload, dict) else None
    if (
        payload.get("schema_version") != SCANNER_CACHE_SCHEMA_VERSION
        or payload.get("price_signature") != price_signature
        or payload.get("conditions_key") != conditions_key
        or not isinstance(payload.get("latest_price_date"), str)
        or not isinstance(result, dict)
        or result.get("as_of") != payload.get("latest_price_date")
        or not isinstance(result.get("candidates"), list)
    ):
        return None
    return result


def _write_cache(
    path: Path, result: dict[str, object], *, price_signature: str, conditions_key: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
    envelope = {
        "schema_version": SCANNER_CACHE_SCHEMA_VERSION,
        "latest_price_date": result["as_of"],
        "price_signature": price_signature,
        "conditions_key": conditions_key,
        "result": result,
    }
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(envelope, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _recent_price_frame(price_root: Path) -> pd.DataFrame:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    boundary = today - timedelta(days=450)
    paths = [
        str(path) for year in range(boundary.year, today.year + 1)
        for path in price_root.glob(f"market=*/year={year}/*.parquet")
    ]
    if not paths:
        raise ValueError("LOCAL_PRICE_DATASET_MISSING")
    dataset = pads.dataset(
        paths, format="parquet", partitioning="hive", partition_base_dir=str(price_root),
    )
    required = {"date", "market", "symbol", "close", "volume"}
    if not required.issubset(dataset.schema.names):
        raise ValueError("LOCAL_PRICE_SCHEMA_INVALID")
    date_field = dataset.schema.field("date")
    if pa.types.is_timestamp(date_field.type):
        threshold: object = datetime.combine(boundary, datetime.min.time())
    elif pa.types.is_date(date_field.type):
        threshold = boundary
    else:
        raise ValueError("LOCAL_PRICE_DATE_SCHEMA_INVALID")
    table = dataset.to_table(
        columns=["date", "market", "symbol", "close", "volume"],
        filter=pads.field("date") >= threshold,
    )
    return table.to_pandas()


def _latest_universe(project_root: Path, latest: pd.Timestamp) -> set[tuple[str, str]]:
    root = Path(project_root) / "data/published/kr_equity_canonical_universe_daily"
    paths = [str(path) for path in root.glob(f"market=*/year={latest.year}/*.parquet")]
    if not paths:
        raise ValueError("CURRENT_UNIVERSE_MISSING")
    dataset = pads.dataset(
        paths, format="parquet", partitioning="hive", partition_base_dir=str(root),
    )
    required = {"date", "market", "symbol", "listed_info_present", "price_present"}
    if not required.issubset(dataset.schema.names):
        raise ValueError("CURRENT_UNIVERSE_SCHEMA_INVALID")
    date_field = dataset.schema.field("date")
    exact: object = latest.to_pydatetime() if pa.types.is_timestamp(date_field.type) else latest.date()
    frame = dataset.to_table(
        columns=list(required), filter=pads.field("date") == exact,
    ).to_pandas()
    frame = frame[
        frame["listed_info_present"].eq(True)
        & frame["price_present"].eq(True)
        & frame["market"].isin(("KOSPI", "KOSDAQ"))
    ]
    if frame.empty or set(frame["market"].astype(str)) != {"KOSPI", "KOSDAQ"}:
        raise ValueError("CURRENT_UNIVERSE_NOT_ALIGNED")
    if frame.duplicated(["market", "symbol"]).any():
        raise ValueError("CURRENT_UNIVERSE_DUPLICATE_IDENTITY")
    return set(zip(frame["market"].astype(str), frame["symbol"].astype(str)))


def _master_names(project_root: Path) -> dict[tuple[str, str], str]:
    root = Path(project_root) / "data/normalized/kr_equity_master"
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        path = root / f"market={market}/data.parquet"
        frames.append(pd.read_parquet(path, columns=["market", "symbol", "name"]))
    frame = pd.concat(frames, ignore_index=True)
    if frame.duplicated(["market", "symbol"]).any() or frame[["market", "symbol", "name"]].isna().any().any():
        raise ValueError("EQUITY_MASTER_INVALID")
    return {
        (str(row.market), str(row.symbol)): str(row.name).strip()
        for row in frame.itertuples(index=False)
        if str(row.name).strip()
    }


def _wilder_rsi_last(values: np.ndarray, period: int = 14) -> float | None:
    if len(values) <= period or not np.isfinite(values).all() or (values <= 0).any():
        return None
    delta = np.diff(values)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    average_gain = float(gains[:period].mean())
    average_loss = float(losses[:period].mean())
    for offset in range(period, len(delta)):
        average_gain = (average_gain * (period - 1) + float(gains[offset])) / period
        average_loss = (average_loss * (period - 1) + float(losses[offset])) / period
    if average_gain == average_loss == 0.0:
        return 50.0
    if average_loss == 0.0:
        return 100.0
    if average_gain == 0.0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _fundamental_dataset(project_root: Path) -> Path | None:
    """Return only a clearly named Normalized per-stock current dataset."""
    normalized = Path(project_root) / "data/normalized"
    for name in (
        "kr_equity_fundamental_current", "kr_equity_fundamental_current_observation",
    ):
        candidate = normalized / name
        if candidate.is_dir():
            return candidate
    return None


def _current_fundamentals(
    project_root: Path, *, as_of: pd.Timestamp,
) -> tuple[dict[str, tuple[float | None, float | None]], list[str]]:
    root = _fundamental_dataset(project_root)
    if root is None:
        return {}, []
    try:
        dataset = pads.dataset(str(root), format="parquet", partitioning="hive")
        required = {"date", "symbol"}
        available = [column for column in ("per", "pbr") if column in dataset.schema.names]
        if not required.issubset(dataset.schema.names) or not available:
            return {}, []
        date_field = dataset.schema.field("date")
        exact: object = as_of.to_pydatetime() if pa.types.is_timestamp(date_field.type) else as_of.date()
        frame = dataset.to_table(
            columns=["date", "symbol", *available], filter=pads.field("date") == exact,
        ).to_pandas()
        if frame.empty or frame.duplicated("symbol").any():
            return {}, []
        values: dict[str, tuple[float | None, float | None]] = {}
        for row in frame.to_dict(orient="records"):
            numbers = []
            for column in ("per", "pbr"):
                raw = row.get(column)
                numeric = float(raw) if raw is not None and not pd.isna(raw) else None
                numbers.append(numeric if numeric is None or math.isfinite(numeric) else None)
            values[str(row["symbol"])] = (numbers[0], numbers[1])
        return values, available
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        return {}, []


def _unavailable(reason: str) -> dict[str, object]:
    return {
        "status": "UNAVAILABLE", "as_of": None, "count": 0,
        "scanned_instruments": 0, "rule": BASE_RULE, "candidates": [], "top": [],
        "reason": reason, "recommendation_state": "DESCRIPTIVE_NOT_A_RECOMMENDATION",
        "fundamental_columns": [],
        "fundamentals_note": "종목별 현재 PER/PBR Normalized 데이터가 없어 표시하지 않습니다.",
    }


def build_scanner(project_root: Path) -> dict[str, object]:
    """Compute or load today's full descriptive scanner result."""
    root = Path(project_root)
    price_root = root / "data/normalized/kr_equity_price_daily"
    conditions = list(load_conditions(root).get("conditions", []))
    universe_conditions = [item for item in conditions if item.get("scope") == "universe"]
    price_signature = _dataset_signature(price_root)
    conditions_key = _conditions_key(universe_conditions)
    cached = _read_cache(
        _cache_path(root), price_signature=price_signature, conditions_key=conditions_key,
    )
    if cached is not None:
        return cached
    try:
        price = _recent_price_frame(price_root)
        price["date"] = pd.to_datetime(price["date"], errors="coerce")
        price["close"] = pd.to_numeric(price["close"], errors="coerce")
        price["volume"] = pd.to_numeric(price["volume"], errors="coerce")
        price = price.dropna(subset=["date", "market", "symbol", "close", "volume"])
        price = price[
            price["market"].isin(("KOSPI", "KOSDAQ"))
            & price["close"].gt(0) & price["volume"].ge(0)
        ]
        if price.empty:
            raise ValueError("LOCAL_CANDIDATE_INPUT_EMPTY")
        latest = pd.Timestamp(price["date"].max()).normalize()
        if latest.date() > datetime.now(ZoneInfo("Asia/Seoul")).date():
            raise ValueError("FUTURE_DATED_INPUT")
        universe = _latest_universe(root, latest)
        names = _master_names(root)
        valuations, fundamental_columns = _current_fundamentals(root, as_of=latest)
        price["market"] = price["market"].astype(str)
        price["symbol"] = price["symbol"].astype(str)
        price = price[
            pd.MultiIndex.from_frame(price[["market", "symbol"]]).isin(universe)
        ].sort_values(["market", "symbol", "date"])
    except PermissionError:
        return _unavailable("LOCAL_CANDIDATE_READ_LOCKED")
    except OSError:
        return _unavailable("LOCAL_CANDIDATE_READ_FAILED")
    except (KeyError, TypeError, ValueError) as error:
        return _unavailable(str(error) or "LOCAL_CANDIDATE_INPUT_INVALID")

    candidates: list[dict[str, object]] = []
    scanned = 0
    for (market, symbol), group in price.groupby(["market", "symbol"], sort=False):
        if group["date"].iloc[-1] != latest or group["date"].duplicated().any():
            continue
        tail = group.tail(300)
        if len(tail) < 60:
            continue
        close = tail["close"].to_numpy(dtype="float64")
        if not np.isfinite(close).all() or (close <= 0).any():
            continue
        rsi14 = _wilder_rsi_last(close)
        ma20 = float(close[-20:].mean())
        ma60 = float(close[-60:].mean())
        if rsi14 is None or ma20 <= 0 or ma60 <= 0:
            continue
        scanned += 1
        metrics = {
            "rsi14": rsi14,
            "disp60_pct": (float(close[-1]) / ma60 - 1.0) * 100.0,
            "drawdown_pct": (float(close[-1]) / float(close[-252:].max()) - 1.0) * 100.0,
            "ma20_pct": (float(close[-1]) / ma20 - 1.0) * 100.0,
            "change_pct": (
                (float(close[-1]) / float(close[-2]) - 1.0) * 100.0 if len(close) > 1 else None
            ),
        }
        matches = evaluate_conditions(metrics, universe_conditions, scope="universe")
        base_reasons = []
        if rsi14 <= 30.0:
            base_reasons.append(f"RSI14 {rsi14:.1f}")
        if metrics["disp60_pct"] <= -20.0:
            base_reasons.append(f"60일선 대비 {metrics['disp60_pct']:.1f}%")
        if not base_reasons and not matches:
            continue
        recent_returns = close[-60:][1:] / close[-60:][:-1] - 1.0
        per, pbr = valuations.get(str(symbol), (None, None))
        candidate: dict[str, object] = {
            "market": str(market), "symbol": str(symbol),
            "name": names.get((str(market), str(symbol)), str(symbol)),
            "price": float(close[-1]), "change_pct": metrics["change_pct"],
            "rsi14": rsi14, "disp60_pct": metrics["disp60_pct"],
            "drawdown_pct": metrics["drawdown_pct"], "ma20_pct": metrics["ma20_pct"],
            "condition_matches": matches,
            "why": " · ".join([*base_reasons, *(str(item["name"]) for item in matches)]),
            "data_caution": (
                "원가격 급변/분할 영향 가능"
                if len(recent_returns) and np.max(np.abs(recent_returns)) >= 0.5 else None
            ),
        }
        if "per" in fundamental_columns:
            candidate["per"] = per
        if "pbr" in fundamental_columns:
            candidate["pbr"] = pbr
        candidates.append(candidate)

    candidates.sort(key=lambda item: (
        float(item["rsi14"]), float(item["disp60_pct"]), str(item["market"]), str(item["symbol"]),
    ))
    condition_rule = " · ".join(str(item["name"]) for item in universe_conditions)
    rule = BASE_RULE + (f" · 사용자 전체시장 조건: {condition_rule}" if condition_rule else "")
    result: dict[str, object] = {
        "status": "READY", "as_of": latest.date().isoformat(), "count": len(candidates),
        "scanned_instruments": scanned, "rule": rule, "candidates": candidates,
        "top": [{"name": item["name"], "why": item["why"]} for item in candidates[:5]],
        "recommendation_state": "DESCRIPTIVE_NOT_A_RECOMMENDATION",
        "fundamental_columns": fundamental_columns,
        "fundamentals_note": (
            "동일 기준일의 Normalized 종목별 현재 PER/PBR · 후보 포함·정렬에는 미사용"
            if fundamental_columns else
            "종목별 현재 PER/PBR Normalized 데이터가 없어 표시하지 않습니다."
        ),
    }
    try:
        _write_cache(
            _cache_path(root), result,
            price_signature=price_signature, conditions_key=conditions_key,
        )
    except OSError:
        pass
    return result
