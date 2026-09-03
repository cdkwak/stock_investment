"""Provider-free services for the local ``/stocks`` page."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Iterable, Mapping
from uuid import uuid4

import pandas as pd

from stock_data.gui.services import (
    EquityChartService,
    EquityIdentity,
    US_ETF_CHART_IDENTITIES,
)
from stock_data.gui.watchlist_service import LocalWatchlistService, WatchlistState


CONDITIONS_SCHEMA_VERSION = 1
CONDITION_FIELDS = frozenset({
    "rsi14", "disp60_pct", "drawdown_pct", "ma20_pct", "change_pct",
})
CONDITION_OPS = frozenset({"<=", ">="})
CONDITION_SCOPES = frozenset({"watchlist", "universe"})
_SEARCH_INDEX_CACHE: dict[str, tuple[str, tuple[tuple[EquityIdentity, float | None], ...]]] = {}
_SEARCH_INDEX_LOCK = threading.Lock()


class StocksInputError(ValueError):
    """A local stocks-page mutation failed validation."""


def _watchlist_path(project_root: Path) -> Path:
    return Path(project_root) / "artifacts/local_user/watchlists.json"


def _conditions_path(project_root: Path) -> Path:
    return Path(project_root) / "artifacts/local_user/watch_conditions.json"


def _identity_payload(identity: EquityIdentity) -> dict[str, object]:
    return {
        "market": identity.market,
        "symbol": identity.symbol,
        "name": identity.name,
        "isin": identity.isin,
        "listing_date": identity.listing_date,
        "security_type": identity.security_type,
        "issuer": identity.issuer,
        "exposure": identity.exposure,
        "currency": identity.currency,
        "leverage_style": identity.leverage_style,
        "distribution_style": identity.distribution_style,
        "identity_source": identity.identity_source,
    }


def serialize_watchlists(state: WatchlistState) -> dict[str, object]:
    return {
        "schema_version": 1,
        "revision": state.revision,
        "recovered_from_backup": state.recovered_from_backup,
        "lists": [
            {
                "list_id": watchlist.list_id,
                "name": watchlist.name,
                "items": [
                    {**_identity_payload(item.identity), "added_at_kst": item.added_at_kst}
                    for item in watchlist.items
                ],
            }
            for watchlist in state.lists
        ],
    }


def _clean_condition(raw: object, seen: set[str]) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise StocksInputError("조건 항목은 객체여야 합니다.")
    identifier = str(raw.get("id") or uuid4().hex).strip()
    name = str(raw.get("name") or "").strip()
    field = str(raw.get("field") or "")
    op = str(raw.get("op") or "")
    scope = str(raw.get("scope") or "")
    value = raw.get("value")
    if not identifier or len(identifier) > 80 or any(c in identifier for c in "\r\n\t"):
        raise StocksInputError("조건 ID 형식이 올바르지 않습니다.")
    if identifier in seen:
        raise StocksInputError("조건 ID가 중복되었습니다.")
    if not name or len(name) > 60 or any(c in name for c in "\r\n\t"):
        raise StocksInputError("조건 이름은 1~60자여야 합니다.")
    if field not in CONDITION_FIELDS or op not in CONDITION_OPS or scope not in CONDITION_SCOPES:
        raise StocksInputError("지원하지 않는 조건 필드, 연산자 또는 범위입니다.")
    if isinstance(value, bool):
        raise StocksInputError("조건 값은 유한한 숫자여야 합니다.")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise StocksInputError("조건 값은 유한한 숫자여야 합니다.") from error
    if not math.isfinite(numeric):
        raise StocksInputError("조건 값은 유한한 숫자여야 합니다.")
    seen.add(identifier)
    return {
        "id": identifier, "name": name, "field": field,
        "op": op, "value": numeric, "scope": scope,
    }


def _validated_conditions(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("schema_version") != CONDITIONS_SCHEMA_VERSION:
        raise StocksInputError("조건 파일 schema_version은 1이어야 합니다.")
    raw_conditions = payload.get("conditions")
    if not isinstance(raw_conditions, list):
        raise StocksInputError("conditions는 배열이어야 합니다.")
    seen: set[str] = set()
    return {
        "schema_version": CONDITIONS_SCHEMA_VERSION,
        "conditions": [_clean_condition(item, seen) for item in raw_conditions],
    }


def load_conditions(project_root: Path) -> dict[str, object]:
    path = _conditions_path(project_root)
    if not path.is_file():
        return {"schema_version": CONDITIONS_SCHEMA_VERSION, "conditions": []}
    try:
        return _validated_conditions(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, StocksInputError):
        return {
            "schema_version": CONDITIONS_SCHEMA_VERSION,
            "conditions": [],
            "warning": "저장된 조건 파일 형식이 올바르지 않아 조건을 적용하지 않았습니다.",
        }


def save_conditions(project_root: Path, payload: object) -> dict[str, object]:
    clean = _validated_conditions(payload)
    path = _conditions_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(clean, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return clean


def evaluate_conditions(
    metrics: Mapping[str, object], conditions: Iterable[Mapping[str, object]], *, scope: str,
) -> list[dict[str, object]]:
    """Return every condition that can be evaluated and is reached."""
    matches: list[dict[str, object]] = []
    for condition in conditions:
        if condition.get("scope") != scope:
            continue
        field = str(condition.get("field") or "")
        actual = metrics.get(field)
        if actual is None or isinstance(actual, bool):
            continue
        try:
            observed = float(actual)
            threshold = float(condition["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(observed) or not math.isfinite(threshold):
            continue
        op = condition.get("op")
        reached = observed <= threshold if op == "<=" else observed >= threshold if op == ">=" else False
        if reached:
            matches.append({
                "id": condition.get("id"), "name": condition.get("name"),
                "field": field, "op": op, "value": threshold, "observed": observed,
            })
    return matches


def _master_signature(project_root: Path) -> str:
    """Return a cheap identity-dataset signature used by the process search cache."""
    root = Path(project_root)
    parts: list[str] = []
    for relative in (
        "data/normalized/kr_equity_master",
        "data/normalized/kr_etf_master",
    ):
        dataset = root / relative
        try:
            paths = sorted(dataset.rglob("*.parquet"))
        except OSError:
            paths = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            parts.append(f"{path.relative_to(root).as_posix()}:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(parts) or "MISSING"


def _latest_market_caps(project_root: Path) -> dict[str, float]:
    root = Path(project_root) / "data/normalized/kr_equity_market_cap_daily"
    try:
        paths = sorted(root.rglob("*.parquet"))
        if not paths:
            return {}
        years = {
            int(match.group(1))
            for path in paths for part in path.parts
            if (match := re.fullmatch(r"year=(\d{4})", part))
        }
        if years:
            latest_year = max(years)
            paths = [path for path in paths if f"year={latest_year}" in path.parts]
        frames = [pd.read_parquet(path, columns=["date", "symbol", "market_cap"]) for path in paths]
        frame = pd.concat(frames, ignore_index=True)
        frame["_date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["_cap"] = pd.to_numeric(frame["market_cap"], errors="coerce")
        frame = frame.dropna(subset=["_date", "symbol", "_cap"])
        frame = frame.sort_values(["symbol", "_date"], kind="stable").drop_duplicates("symbol", keep="last")
        return {
            str(row["symbol"]): float(row["_cap"])
            for row in frame.loc[:, ["symbol", "_cap"]].to_dict(orient="records")
        }
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        return {}


def _issued_share_price_caps(project_root: Path) -> dict[str, float]:
    """Use the cheap latest retained snapshot when the market-cap dataset is unavailable."""
    root = Path(project_root)
    try:
        masters = [
            pd.read_parquet(path, columns=["symbol", "issued_shares"])
            for path in sorted((root / "data/normalized/kr_equity_master").rglob("*.parquet"))
        ]
        price_paths = sorted(
            (root / "data/normalized/kr_equity_price_provisional_daily").rglob("*.parquet")
        )
        if not masters or not price_paths:
            return {}
        prices = [pd.read_parquet(path, columns=["date", "symbol", "close"]) for path in price_paths]
        master = pd.concat(masters, ignore_index=True)
        price = pd.concat(prices, ignore_index=True)
        master["_shares"] = pd.to_numeric(master["issued_shares"], errors="coerce")
        price["_date"] = pd.to_datetime(price["date"], errors="coerce")
        price["_close"] = pd.to_numeric(price["close"], errors="coerce")
        price = price.dropna(subset=["symbol", "_date", "_close"])
        price = price.sort_values(["symbol", "_date"], kind="stable").drop_duplicates("symbol", keep="last")
        joined = master.loc[:, ["symbol", "_shares"]].merge(
            price.loc[:, ["symbol", "_close"]], on="symbol", how="inner", validate="one_to_one",
        ).dropna(subset=["_shares", "_close"])
        joined["_cap"] = joined["_shares"] * joined["_close"]
        return {
            str(row["symbol"]): float(row["_cap"])
            for row in joined.loc[joined["_cap"].gt(0), ["symbol", "_cap"]].to_dict(orient="records")
        }
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        return {}


def _search_index(project_root: Path) -> tuple[tuple[EquityIdentity, float | None], ...]:
    root = Path(project_root).resolve()
    key = str(root)
    signature = _master_signature(root)
    cached = _SEARCH_INDEX_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    with _SEARCH_INDEX_LOCK:
        cached = _SEARCH_INDEX_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            catalog = EquityChartService(root)._catalog()
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            catalog = ()
        market_caps = _latest_market_caps(root) if catalog else {}
        if catalog and len(market_caps) < len(catalog):
            market_caps = {**_issued_share_price_caps(root), **market_caps}
        index = tuple(
            (identity, market_caps.get(identity.symbol))
            for identity in (*catalog, *US_ETF_CHART_IDENTITIES)
        )
        _SEARCH_INDEX_CACHE[key] = (signature, index)
        return index


def _search_rank(identity: EquityIdentity, market_cap: float | None, folded: str) -> tuple[object, ...] | None:
    symbol = identity.symbol.casefold()
    name = identity.name.casefold()
    if symbol == folded:
        match_rank = 0
    elif name == folded:
        match_rank = 1
    elif name.startswith(folded):
        match_rank = 2
    elif folded in name:
        match_rank = 3
    elif (
        folded in (identity.issuer or "").casefold()
        or folded in (identity.exposure or "").casefold()
    ):
        match_rank = 4
    else:
        return None
    cap_missing = market_cap is None or not math.isfinite(float(market_cap))
    return (
        match_rank,
        cap_missing,
        -float(market_cap) if not cap_missing else 0.0,
        len(identity.name),
        name,
        identity.market,
        identity.symbol,
    )


def search_stocks(project_root: Path, text: str) -> dict[str, object]:
    query = str(text or "").strip()
    if not query:
        return {"query": query, "matches": [], "reason": "회사명·6자리 코드·미국 ETF 티커를 입력하세요."}
    folded = query.casefold()
    try:
        ranked = [
            (rank, identity)
            for identity, market_cap in _search_index(Path(project_root))
            if (rank := _search_rank(identity, market_cap, folded)) is not None
        ]
        ranked.sort(key=lambda item: item[0])
        combined = [identity for _, identity in ranked[:30]]
        unavailable_reason = None
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        combined = []
        unavailable_reason = "종목 식별정보를 읽거나 검증할 수 없습니다."
    return {
        "query": query,
        "matches": [_identity_payload(item) for item in combined],
        "reason": None if combined else (unavailable_reason or "일치하는 종목이 없습니다."),
    }


def _canonical_identity(project_root: Path, market: object, symbol: object) -> EquityIdentity:
    clean_market = str(market or "").strip()
    clean_symbol = str(symbol or "").strip().upper()
    if clean_market == "US ETF":
        identity = next(
            (item for item in US_ETF_CHART_IDENTITIES if item.symbol == clean_symbol), None,
        )
    else:
        view = EquityChartService(Path(project_root)).search(clean_symbol, limit=30)
        identity = next(
            (item for item in view.matches if item.market == clean_market and item.symbol == clean_symbol), None,
        )
    if identity is None:
        raise StocksInputError("로컬 종목 카탈로그에서 정확한 종목을 확인할 수 없습니다.")
    return identity


def add_watchlist_item(project_root: Path, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise StocksInputError("요청 형식이 올바르지 않습니다.")
    list_id = str(payload.get("list_id") or "favorites")
    identity = _canonical_identity(project_root, payload.get("market"), payload.get("symbol"))
    state = LocalWatchlistService(_watchlist_path(project_root)).add_item(list_id, identity)
    return serialize_watchlists(state)


def remove_watchlist_item(project_root: Path, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise StocksInputError("요청 형식이 올바르지 않습니다.")
    list_id = str(payload.get("list_id") or "favorites")
    market = str(payload.get("market") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not market or not symbol:
        raise StocksInputError("목록, 시장, 종목코드가 필요합니다.")
    state = LocalWatchlistService(_watchlist_path(project_root)).remove_item(
        list_id, (market, symbol),
    )
    return serialize_watchlists(state)


def move_watchlist_item(project_root: Path, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise StocksInputError("요청 형식이 올바르지 않습니다.")
    try:
        offset = int(payload.get("offset"))
    except (TypeError, ValueError) as error:
        raise StocksInputError("이동 방향이 올바르지 않습니다.") from error
    if offset not in {-1, 1}:
        raise StocksInputError("이동 방향은 -1 또는 1이어야 합니다.")
    state = LocalWatchlistService(_watchlist_path(project_root)).move_item(
        str(payload.get("list_id") or "favorites"),
        (str(payload.get("market") or "").strip(), str(payload.get("symbol") or "").strip().upper()),
        offset,
    )
    return serialize_watchlists(state)


def mutate_watchlist(project_root: Path, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise StocksInputError("요청 형식이 올바르지 않습니다.")
    action = payload.get("action")
    service = LocalWatchlistService(_watchlist_path(project_root))
    if action == "create":
        state = service.create_list(str(payload.get("name") or ""))
    elif action == "rename":
        state = service.rename_list(
            str(payload.get("list_id") or ""), str(payload.get("name") or ""),
        )
    else:
        raise StocksInputError("지원하지 않는 관심목록 작업입니다.")
    return serialize_watchlists(state)


def _last_ma(chart: Mapping[str, object], key: str) -> float | None:
    points = (chart.get("ma") or {}).get(key, []) if isinstance(chart.get("ma"), dict) else []
    for point in reversed(points):
        value = point.get("v") if isinstance(point, dict) else None
        if value is not None:
            return float(value)
    return None


def _watchlist_row(
    project_root: Path, list_id: str, list_name: str, identity: EquityIdentity,
    conditions: list[dict[str, object]],
) -> dict[str, object]:
    from stock_web.api.home_data import build_chart_payload

    chart = build_chart_payload(project_root, symbol=identity.symbol, range_key="1Y")
    candles = chart.get("candles") if isinstance(chart, dict) else None
    if not isinstance(candles, list) or not candles:
        return {
            "list_id": list_id, "list_name": list_name, **_identity_payload(identity),
            "price_available": False, "unavailable_reason": "로컬 가격 없음",
            "condition_matches": [], "flag": "",
            "price_basis": None,
        }
    last = candles[-1]
    previous = candles[-2] if len(candles) > 1 else None
    close = float(last["c"])
    stats = chart.get("stats") if isinstance(chart.get("stats"), dict) else {}
    metrics: dict[str, object] = {
        "change_pct": (
            (close / float(previous["c"]) - 1.0) * 100.0
            if previous and previous.get("c") not in {None, 0} else None
        ),
        "rsi14": stats.get("rsi14"),
        "disp60_pct": stats.get("disp60_pct"),
        "drawdown_pct": stats.get("drawdown_pct"),
    }
    for window in (5, 20, 60):
        average = _last_ma(chart, f"ma{window}")
        metrics[f"ma{window}_pct"] = (close / average - 1.0) * 100.0 if average else None
    prior_volumes = [
        float(item["v"]) for item in candles[-21:-1]
        if item.get("v") is not None and float(item["v"]) >= 0
    ]
    baseline = sum(prior_volumes) / len(prior_volumes) if prior_volumes else None
    metrics["volume20_multiple"] = (
        float(last["v"]) / baseline if last.get("v") is not None and baseline else None
    )
    matches = evaluate_conditions(metrics, conditions, scope="watchlist")
    provisional_dates = chart.get("provisional_dates", [])
    price_basis = (
        "provisional"
        if chart.get("as_of") in provisional_dates
        else "canonical"
    )
    return {
        "list_id": list_id, "list_name": list_name, **_identity_payload(identity),
        "price_available": True, "as_of": chart.get("as_of"),
        "provisional_dates": provisional_dates, "price_basis": price_basis,
        "price": close,
        **metrics, "condition_matches": matches,
        "flag": " · ".join(str(match["name"]) for match in matches),
    }


def build_stocks_page_data(project_root: Path) -> dict[str, object]:
    root = Path(project_root)
    state = LocalWatchlistService(_watchlist_path(root)).load()
    conditions_payload = load_conditions(root)
    conditions = list(conditions_payload.get("conditions", []))
    watchlists = serialize_watchlists(state)
    table = [
        _watchlist_row(root, watchlist.list_id, watchlist.name, item.identity, conditions)
        for watchlist in state.lists
        for item in watchlist.items
    ]
    return {
        "watchlists": watchlists,
        "conditions": conditions_payload,
        "table": table,
        "note": "보존된 로컬 종가 기준 · 추천이나 주문 신호가 아닙니다.",
    }


def build_home_watchlist(project_root: Path) -> dict[str, object]:
    data = build_stocks_page_data(project_root)
    rows = []
    for item in data["table"]:
        rows.append({
            "name": item["name"], "symbol": item["symbol"], "held": False,
            "weight_pct": None,
            "price": f"{item['price']:,.2f}" if item.get("price_available") and item.get("market") == "US ETF" else (
                f"{item['price']:,.0f}" if item.get("price_available") else None
            ),
            "change_pct": item.get("change_pct"),
            "drawdown_pct": item.get("drawdown_pct"), "rsi14": item.get("rsi14"),
            "flow_foreign": None, "flow_inst": None, "flow_indiv": None,
            "flag": item.get("flag", ""), "as_of": item.get("as_of"),
            "provisional_dates": item.get("provisional_dates", []),
            "price_basis": item.get("price_basis"),
        })
    return {
        "rows": rows, "held_count": 0, "watch_count": len(rows),
        "note": "보유 비중은 계좌 연결 후 · 조건 표시는 종목 페이지의 사용자 조건 기준",
    }
