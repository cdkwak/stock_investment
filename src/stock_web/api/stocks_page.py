"""Provider-free services for the local ``/stocks`` page."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from dataclasses import dataclass
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
from stock_web.api.symbol_resolver import global_equity_identities, global_equity_identity


CONDITIONS_SCHEMA_VERSION = 1
CONDITION_FIELDS = frozenset({
    "rsi14", "disp60_pct", "drawdown_pct", "ma20_pct", "change_pct",
})
CONDITION_OPS = frozenset({"<=", ">="})
CONDITION_SCOPES = frozenset({"watchlist", "universe"})
PUBLIC_WATCHLIST_RELATIVE = Path("config/public_watchlist.json")
GLOBAL_EQUITY_WATCHLIST_RELATIVE = Path("artifacts/local_user/watchlist_global_equities.json")


@dataclass(frozen=True)
class _SearchIndexEntry:
    identity: EquityIdentity
    market_cap: float | None
    aliases: tuple[str, ...]
    source: str
    full_name: str | None = None


_SEARCH_INDEX_CACHE: dict[str, tuple[str, tuple[_SearchIndexEntry, ...]]] = {}
_SEARCH_INDEX_LOCK = threading.Lock()


class StocksInputError(ValueError):
    """A local stocks-page mutation failed validation."""


def _watchlist_path(project_root: Path) -> Path:
    return Path(project_root) / "artifacts/local_user/watchlists.json"


def _conditions_path(project_root: Path) -> Path:
    return Path(project_root) / "artifacts/local_user/watch_conditions.json"


def _global_equity_watchlist_path(project_root: Path) -> Path:
    return Path(project_root) / GLOBAL_EQUITY_WATCHLIST_RELATIVE


def _load_global_equity_watchlist(project_root: Path) -> dict[str, list[dict[str, str]]]:
    path = _global_equity_watchlist_path(project_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    raw_lists = payload.get("lists") if isinstance(payload, Mapping) else None
    if payload.get("schema_version") != 1 or not isinstance(raw_lists, Mapping):
        return {}
    cleaned: dict[str, list[dict[str, str]]] = {}
    for list_id, raw_items in raw_lists.items():
        if not isinstance(list_id, str) or not isinstance(raw_items, list):
            continue
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            symbol = str(raw.get("symbol") or "").strip().upper()
            if symbol in seen or global_equity_identity(symbol) is None:
                continue
            seen.add(symbol)
            items.append({
                "symbol": symbol,
                "added_at_kst": str(raw.get("added_at_kst") or "MIGRATED"),
            })
        if items:
            cleaned[list_id] = items
    return cleaned


def _save_global_equity_watchlist(
    project_root: Path, lists: Mapping[str, list[dict[str, str]]],
) -> None:
    path = _global_equity_watchlist_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"schema_version": 1, "lists": lists}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _global_equity_object(item: Mapping[str, object]) -> EquityIdentity:
    return EquityIdentity(
        symbol=str(item["symbol"]), name=str(item["name"]), market="US 주식",
        isin=None, listing_date=None, security_type=str(item["security_type"]),
        issuer=str(item.get("exchange") or "") or None,
        exposure=(f"원주 {item['underlying_kr_symbol']}" if item.get("underlying_kr_symbol") else None),
        currency="USD", identity_source="global_equity_registry",
    )


def _serialize_with_global_equities(project_root: Path, state: WatchlistState) -> dict[str, object]:
    payload = serialize_watchlists(state)
    lists_by_id = {item["list_id"]: item for item in payload["lists"]}
    for list_id, items in _load_global_equity_watchlist(project_root).items():
        target = lists_by_id.get(list_id)
        if target is None:
            continue
        for stored in items:
            identity = global_equity_identity(stored["symbol"])
            if identity is not None:
                target["items"].append({
                    **_identity_payload(_global_equity_object(identity)),
                    "added_at_kst": stored["added_at_kst"],
                })
    return payload


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


def _public_watchlist(project_root: Path) -> tuple[dict[str, object], tuple[EquityIdentity, ...]]:
    path = Path(project_root) / PUBLIC_WATCHLIST_RELATIVE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StocksInputError("공개 관심종목 설정을 읽을 수 없습니다.") from error
    items = payload.get("items") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(items, list)
        or not items
    ):
        raise StocksInputError("공개 관심종목 설정 형식이 올바르지 않습니다.")
    identities: list[EquityIdentity] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            raise StocksInputError("공개 관심종목 항목 형식이 올바르지 않습니다.")
        market = str(item.get("market") or "").strip()
        symbol = str(item.get("symbol") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        security_type = str(item.get("security_type") or "").strip()
        if market == "US 주식":
            registered = global_equity_identity(symbol)
            if registered is None or (market, symbol) in seen:
                raise StocksInputError("공개 관심종목 항목을 검증할 수 없습니다.")
            seen.add((market, symbol))
            identities.append(_global_equity_object(registered))
            continue
        if (
            market not in {"KOSPI", "KOSDAQ", "US ETF"}
            or not symbol or not name or security_type not in {"ETF", "보통주"}
            or (market == "US ETF" and not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol))
            or (market != "US ETF" and not re.fullmatch(r"\d{6}", symbol))
            or (market, symbol) in seen
        ):
            raise StocksInputError("공개 관심종목 항목을 검증할 수 없습니다.")
        seen.add((market, symbol))
        identities.append(EquityIdentity(
            symbol=symbol,
            name=name,
            market=market,
            isin=None,
            listing_date=None,
            security_type=security_type,
            currency="USD" if market == "US ETF" else "KRW",
            leverage_style=(
                str(item.get("leverage_style")).strip()
                if item.get("leverage_style") else None
            ),
            identity_source="tracked public watchlist",
        ))
    watchlists = {
        "schema_version": 1,
        "revision": 0,
        "recovered_from_backup": False,
        "lists": [{
            "list_id": "public",
            "name": "공개 관심종목",
            "items": [
                {**_identity_payload(identity), "added_at_kst": None}
                for identity in identities
            ],
        }],
    }
    return watchlists, tuple(identities)


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
        "data/normalized/kr_etf_universe_daily",
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


def _optional_search_date(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date().isoformat()


def _kr_etf_universe_search_entries(project_root: Path) -> tuple[_SearchIndexEntry, ...]:
    dataset = Path(project_root) / "data/normalized/kr_etf_universe_daily"
    paths = sorted(dataset.rglob("*.parquet"))
    if not paths:
        return ()
    columns = [
        "source_date", "symbol", "name", "full_name", "isin", "listing_date",
        "underlying_index", "market", "security_type", "listing_status",
    ]
    frames = [pd.read_parquet(path, columns=columns) for path in paths]
    frame = pd.concat(frames, ignore_index=True)
    frame["_source_date"] = pd.to_datetime(frame["source_date"], errors="coerce")
    if frame.empty or frame["_source_date"].isna().any():
        raise ValueError("Korean ETF universe source date is invalid")
    latest = frame.loc[frame["_source_date"].eq(frame["_source_date"].max())].copy()
    if (
        latest.empty
        or latest["symbol"].astype(str).duplicated().any()
        or not latest["symbol"].astype(str).str.fullmatch(r"[0-9A-Z]{6}").all()
        or latest[["symbol", "name", "market", "security_type", "listing_status"]].isna().any().any()
        or not latest["market"].astype(str).eq("KRX").all()
        or not latest["security_type"].astype(str).eq("ETF").all()
        or not latest["listing_status"].astype(str).eq("LISTED_AT_SOURCE_DATE").all()
    ):
        raise ValueError("Korean ETF universe identity is invalid")
    entries: list[_SearchIndexEntry] = []
    for row in latest.sort_values(["symbol"], kind="stable").itertuples(index=False):
        name = str(row.name).strip()
        full_name = None if pd.isna(row.full_name) else str(row.full_name).strip() or None
        if not name:
            raise ValueError("Korean ETF universe short name is empty")
        aliases = tuple(dict.fromkeys(value for value in (name, full_name) if value))
        entries.append(_SearchIndexEntry(
            identity=EquityIdentity(
                symbol=str(row.symbol), name=name, market="KRX",
                isin=None if pd.isna(row.isin) else str(row.isin),
                listing_date=_optional_search_date(row.listing_date),
                security_type="ETF",
                exposure=None if pd.isna(row.underlying_index) else str(row.underlying_index),
                currency="KRW", identity_source="kr_etf_universe",
            ),
            market_cap=None,
            aliases=aliases,
            source="kr_etf_universe",
            full_name=full_name,
        ))
    return tuple(entries)


def _global_equity_search_entries() -> tuple[_SearchIndexEntry, ...]:
    entries: list[_SearchIndexEntry] = []
    for item in global_equity_identities():
        identity = _global_equity_object(item)
        entries.append(_SearchIndexEntry(
            identity=identity, market_cap=None,
            aliases=tuple(str(alias) for alias in item.get("aliases", ()) if alias),
            source="global_equity_registry", full_name=str(item["name"]),
        ))
    return tuple(entries)


def _search_index(project_root: Path) -> tuple[_SearchIndexEntry, ...]:
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
        base_entries = tuple(_SearchIndexEntry(
            identity=identity,
            market_cap=market_caps.get(identity.symbol),
            aliases=(identity.name,),
            source=(
                "kr_etf_master" if identity.market == "KRX" and identity.security_type == "ETF"
                else "us_etf_catalog" if identity.market == "US ETF"
                else "kr_equity_master"
            ),
        ) for identity in (*catalog, *US_ETF_CHART_IDENTITIES))
        try:
            universe_entries = _kr_etf_universe_search_entries(root)
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            universe_entries = ()
        universe_keys = {entry.identity.key for entry in universe_entries}
        index = tuple(
            entry for entry in base_entries if entry.identity.key not in universe_keys
        ) + universe_entries + _global_equity_search_entries()
        _SEARCH_INDEX_CACHE[key] = (signature, index)
        return index


def _search_rank(entry: _SearchIndexEntry, folded: str) -> tuple[object, ...] | None:
    identity = entry.identity
    market_cap = entry.market_cap
    symbol = identity.symbol.casefold()
    name = identity.name.casefold()
    aliases = tuple(value.casefold() for value in entry.aliases)
    if symbol == folded:
        match_rank = 0
    elif folded in aliases:
        match_rank = 1
    elif any(value.startswith(folded) for value in aliases):
        match_rank = 2
    elif folded in symbol or any(folded in value for value in aliases):
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


def _search_payload(entry: _SearchIndexEntry) -> dict[str, object]:
    return {
        **_identity_payload(entry.identity),
        "full_name": entry.full_name,
        "source": entry.source,
    }


def search_stocks(project_root: Path, text: str) -> dict[str, object]:
    query = str(text or "").strip()
    if not query:
        return {
            "query": query,
            "matches": [],
            "reason": "회사명·ETF명·6자리 코드·미국 ETF 티커를 입력하세요.",
        }
    folded = query.casefold()
    try:
        ranked = [
            (rank, entry)
            for entry in _search_index(Path(project_root))
            if (rank := _search_rank(entry, folded)) is not None
        ]
        ranked.sort(key=lambda item: item[0])
        combined = [entry for _, entry in ranked[:30]]
        unavailable_reason = None
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        combined = []
        unavailable_reason = "종목 식별정보를 읽거나 검증할 수 없습니다."
    return {
        "query": query,
        "matches": [_search_payload(item) for item in combined],
        "reason": None if combined else (unavailable_reason or "일치하는 종목이 없습니다."),
    }


def _canonical_identity(project_root: Path, market: object, symbol: object) -> EquityIdentity:
    clean_market = str(market or "").strip()
    clean_symbol = str(symbol or "").strip().upper()
    if clean_market == "US ETF":
        identity = next(
            (item for item in US_ETF_CHART_IDENTITIES if item.symbol == clean_symbol), None,
        )
    elif clean_market == "US 주식":
        item = global_equity_identity(clean_symbol)
        identity = None if item is None else _global_equity_object(item)
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
    service = LocalWatchlistService(_watchlist_path(project_root))
    if identity.market == "US 주식":
        state = service.load()
        state.list_by_id(list_id)
        lists = _load_global_equity_watchlist(project_root)
        items = list(lists.get(list_id, []))
        if not any(item["symbol"] == identity.symbol for item in items):
            items.append({"symbol": identity.symbol, "added_at_kst": datetime.now().astimezone().isoformat()})
            lists[list_id] = items
            _save_global_equity_watchlist(project_root, lists)
    else:
        state = service.add_item(list_id, identity)
    return _serialize_with_global_equities(project_root, state)


def remove_watchlist_item(project_root: Path, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise StocksInputError("요청 형식이 올바르지 않습니다.")
    list_id = str(payload.get("list_id") or "favorites")
    market = str(payload.get("market") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not market or not symbol:
        raise StocksInputError("목록, 시장, 종목코드가 필요합니다.")
    service = LocalWatchlistService(_watchlist_path(project_root))
    if market == "US 주식":
        state = service.load()
        state.list_by_id(list_id)
        lists = _load_global_equity_watchlist(project_root)
        lists[list_id] = [item for item in lists.get(list_id, []) if item["symbol"] != symbol]
        if not lists[list_id]:
            lists.pop(list_id, None)
        _save_global_equity_watchlist(project_root, lists)
    else:
        state = service.remove_item(list_id, (market, symbol))
    return _serialize_with_global_equities(project_root, state)


def move_watchlist_item(project_root: Path, payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise StocksInputError("요청 형식이 올바르지 않습니다.")
    try:
        offset = int(payload.get("offset"))
    except (TypeError, ValueError) as error:
        raise StocksInputError("이동 방향이 올바르지 않습니다.") from error
    if offset not in {-1, 1}:
        raise StocksInputError("이동 방향은 -1 또는 1이어야 합니다.")
    list_id = str(payload.get("list_id") or "favorites")
    market = str(payload.get("market") or "").strip()
    symbol = str(payload.get("symbol") or "").strip().upper()
    service = LocalWatchlistService(_watchlist_path(project_root))
    if market == "US 주식":
        state = service.load()
        state.list_by_id(list_id)
        lists = _load_global_equity_watchlist(project_root)
        items = list(lists.get(list_id, []))
        source = next((index for index, item in enumerate(items) if item["symbol"] == symbol), None)
        if source is None:
            raise KeyError((market, symbol))
        target = max(0, min(len(items) - 1, source + offset))
        items.insert(target, items.pop(source))
        lists[list_id] = items
        _save_global_equity_watchlist(project_root, lists)
    else:
        state = service.move_item(list_id, (market, symbol), offset)
    return _serialize_with_global_equities(project_root, state)


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
    return _serialize_with_global_equities(project_root, state)


def _last_ma(chart: Mapping[str, object], key: str) -> float | None:
    points = (chart.get("ma") or {}).get(key, []) if isinstance(chart.get("ma"), dict) else []
    for point in reversed(points):
        value = point.get("v") if isinstance(point, dict) else None
        if value is not None:
            return float(value)
    return None


def _price_display(value: float, market: str) -> str:
    digits = 2 if market in {"US ETF", "US 주식"} else 0
    return f"{value:,.{digits}f}"


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
    short_history_reason = (
        "상장 60일 미만"
        if identity.market == "US 주식" and len(candles) < 60
        else (f"자료 {len(candles)}일치" if len(candles) < 60 else None)
    )
    return {
        "list_id": list_id, "list_name": list_name, **_identity_payload(identity),
        "price_available": True, "as_of": chart.get("as_of"),
        "provisional_dates": provisional_dates, "price_basis": price_basis,
        "price": close, "price_display": _price_display(close, identity.market),
        "ma60_display": (
            f"{float(metrics['ma60_pct']):+.1f}%"
            if metrics.get("ma60_pct") is not None
            else f"— ({short_history_reason})"
        ),
        "ma60_unavailable_reason": short_history_reason,
        "disp60_display": (
            f"{float(metrics['disp60_pct']):+.1f}%"
            if metrics.get("disp60_pct") is not None
            else f"— ({short_history_reason})"
        ),
        **metrics, "condition_matches": matches,
        "flag": " · ".join(str(match["name"]) for match in matches),
    }


def build_stocks_page_data(
    project_root: Path, *, public_mode: bool | None = None,
) -> dict[str, object]:
    root = Path(project_root)
    if public_mode is None:
        public_mode = os.environ.get("STOCK_WEB_PUBLIC_MODE") == "1"
    if public_mode:
        watchlists, identities = _public_watchlist(root)
        table = [
            {
                **_watchlist_row(root, "public", "공개 관심종목", identity, []),
                "held": False,
            }
            for identity in identities
        ]
        return {
            "public": True,
            "watchlists": watchlists,
            "conditions": {"schema_version": CONDITIONS_SCHEMA_VERSION, "conditions": []},
            "table": table,
            "note": "고정 공개 관심종목 · 읽기 전용 · 추천이나 주문 신호가 아닙니다.",
        }
    state = LocalWatchlistService(_watchlist_path(root)).load()
    conditions_payload = load_conditions(root)
    conditions = list(conditions_payload.get("conditions", []))
    watchlists = _serialize_with_global_equities(root, state)
    table = [
        _watchlist_row(root, watchlist.list_id, watchlist.name, item.identity, conditions)
        for watchlist in state.lists
        for item in watchlist.items
    ]
    names_by_id = {watchlist.list_id: watchlist.name for watchlist in state.lists}
    table.extend(
        _watchlist_row(
            root, list_id, names_by_id[list_id], _global_equity_object(identity), conditions,
        )
        for list_id, items in _load_global_equity_watchlist(root).items()
        if list_id in names_by_id
        for stored in items
        if (identity := global_equity_identity(stored["symbol"])) is not None
    )
    return {
        "watchlists": watchlists,
        "conditions": conditions_payload,
        "table": table,
        "note": "보존된 로컬 종가 기준 · 추천이나 주문 신호가 아닙니다.",
    }


def build_home_watchlist(
    project_root: Path, *, public_mode: bool | None = None,
) -> dict[str, object]:
    if public_mode is None:
        public_mode = os.environ.get("STOCK_WEB_PUBLIC_MODE") == "1"
    data = build_stocks_page_data(project_root, public_mode=public_mode)
    rows = []
    for item in data["table"]:
        is_us = item.get("market") in {"US ETF", "US 주식"}
        rows.append({
            "name": item["name"], "symbol": item["symbol"],
            "market": item.get("market"), "currency": item.get("currency"),
            "security_type": item.get("security_type"), "held": False,
            "weight_pct": None,
            "price": f"{item['price']:,.2f}" if item.get("price_available") and is_us else (
                f"{item['price']:,.0f}" if item.get("price_available") else (
                    "자료 없음" if item.get("market") == "US 주식" else None
                )
            ),
            "change_pct": item.get("change_pct"),
            "drawdown_pct": item.get("drawdown_pct"), "rsi14": item.get("rsi14"),
            "flow_foreign": None, "flow_inst": None, "flow_indiv": None,
            "flag": item.get("flag", ""), "as_of": item.get("as_of"),
            "provisional_dates": item.get("provisional_dates", []),
            "price_basis": item.get("price_basis"),
        })
    result: dict[str, object] = {
        "rows": rows, "watch_count": len(rows),
        "note": (
            "고정 공개 관심종목 · 읽기 전용"
            if public_mode else
            "보유 비중은 계좌 연결 후 · 조건 표시는 종목 페이지의 사용자 조건 기준"
        ),
    }
    if not public_mode:
        result["held_count"] = 0
    us_live = _us_live_quotes(Path(project_root), data["table"])
    if us_live is not None:
        result["us_live"] = us_live
    return result


def _us_live_quotes(
    project_root: Path, table: Iterable[Mapping[str, object]],
) -> dict[str, object] | None:
    """Project optional Toss U.S. quotes without exposing their artifact location."""
    path = project_root / "artifacts/intraday/tossinvest_us_quotes_latest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping) or payload.get("provider") != "tossinvest":
        return None
    raw_quotes = payload.get("quotes")
    if not isinstance(raw_quotes, list):
        return None
    quotes_by_symbol = {
        str(quote.get("symbol") or "").strip().upper(): quote
        for quote in raw_quotes if isinstance(quote, Mapping)
    }
    quotes: list[dict[str, object]] = []
    for row in table:
        if row.get("market") not in {"US ETF", "US 주식"}:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        quote = quotes_by_symbol.get(symbol)
        if quote is None:
            continue
        try:
            last_price = float(quote.get("last_price"))
            daily_close = float(row.get("price")) if row.get("price_available") else None
        except (TypeError, ValueError):
            continue
        if not math.isfinite(last_price) or last_price <= 0:
            continue
        change = last_price - daily_close if daily_close not in {None, 0} else None
        quotes.append({
            "symbol": symbol,
            "last_price": last_price,
            "currency": str(quote.get("currency") or row.get("currency") or "USD"),
            "timestamp_kst": str(quote.get("timestamp_kst") or ""),
            "daily_close": daily_close,
            "change": change,
            "change_pct": change / daily_close * 100.0 if change is not None else None,
        })
    if not quotes:
        return None
    session_labels = {
        "pre_market": "프리마켓", "regular": "정규장",
        "after_hours": "애프터마켓", "closed": "휴장",
    }
    session_hint = str(payload.get("session_hint") or "")
    if session_hint not in session_labels:
        return None
    as_of_kst = str(payload.get("as_of_kst") or "")
    match = re.search(r"T([0-2]\d:[0-5]\d)", as_of_kst)
    return {
        "label": "밤사이 미국", "session": session_hint,
        "session_label": session_labels[session_hint],
        "as_of_kst": as_of_kst, "as_of_label": match.group(1) if match else as_of_kst,
        "quotes": quotes,
    }
