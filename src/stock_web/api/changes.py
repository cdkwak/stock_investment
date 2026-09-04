"""Provider-free daily change detection for the web home page."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.dataset as pads

from stock_data.research.condition_backtest import compute_signals
from stock_web.api.stocks_page import evaluate_conditions, load_conditions
from stock_web.api.symbol_resolver import global_equity_identities


CHANGES_CACHE_SCHEMA_VERSION = 1
_PRICE_DATASETS = (
    "data/normalized/kr_equity_price_daily",
    "data/normalized/kr_etf_price_daily",
    "data/normalized/global_equity_price_daily",
    "data/normalized/global_etf_price_daily",
)


def _empty_payload() -> dict[str, object]:
    return {
        "as_of": None,
        "rule_changes": [],
        "condition_entries": [],
        "condition_exits": [],
        "new_highs_52w": 0,
        "new_lows_52w": 0,
        "new_highs_52w_list": [],
        "new_lows_52w_list": [],
        "volume_spikes": [],
        "counts": {
            "rule_changes": 0,
            "condition_entries": 0,
            "condition_exits": 0,
            "new_highs_52w": 0,
            "new_lows_52w": 0,
            "volume_spikes": 0,
        },
    }


def _cache_path(project_root: Path) -> Path:
    return Path(project_root) / "artifacts/local_user/changes_cache.json"


def _hash_paths(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(set(paths)):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(str(path).encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        count += 1
    digest.update(str(count).encode("ascii"))
    return digest.hexdigest()


def _dataset_signature(project_root: Path, *, public_mode: bool) -> str:
    root = Path(project_root)
    paths: list[Path] = []
    for relative in (
        *_PRICE_DATASETS,
        "data/normalized/kr_equity_market_cap_daily",
        "data/published/kr_equity_canonical_universe_daily",
        "data/normalized/kr_index_constituent_daily",
    ):
        dataset = root / relative
        try:
            paths.extend(dataset.rglob("*.parquet"))
        except OSError:
            continue
    paths.append(root / "config/public_watchlist.json")
    if not public_mode:
        paths.extend((
            root / "artifacts/local_user/watchlists.json",
            root / "artifacts/local_user/watchlist_global_equities.json",
            root / "artifacts/local_user/watch_conditions.json",
            root / "artifacts/local_user/manual_accounts.json",
            root / "artifacts/local_user/manual_accounts_web.json",
            root / "data/normalized/toss_account_snapshot/latest.json",
            root / "data/local/account_snapshots/kb_self.json",
        ))
    return _hash_paths(paths)


def _recent_paths(dataset: Path, *, symbols: Iterable[str] | None = None) -> list[Path]:
    year = datetime.now(ZoneInfo("Asia/Seoul")).year
    years = range(year - 2, year + 1)
    paths: list[Path] = []
    if symbols is not None:
        for symbol in sorted(set(symbols)):
            for candidate_year in years:
                paths.extend((dataset / f"symbol={symbol}" / f"year={candidate_year}").glob("*.parquet"))
        return sorted(set(paths))
    for candidate_year in years:
        paths.extend(dataset.glob(f"year={candidate_year}/*.parquet"))
        paths.extend(dataset.glob(f"*/year={candidate_year}/*.parquet"))
    return sorted(set(paths))


def _read_paths(
    paths: Iterable[Path], *, columns: Iterable[str], symbols: set[str] | None = None,
) -> pd.DataFrame:
    existing = [str(path) for path in paths if path.is_file()]
    if not existing:
        return pd.DataFrame(columns=list(columns))
    try:
        dataset = pads.dataset(existing, format="parquet", partitioning=None)
        requested = [column for column in columns if column in dataset.schema.names]
        if "symbol" not in requested:
            return pd.DataFrame(columns=list(columns))
        filter_expr = pads.field("symbol").isin(sorted(symbols)) if symbols else None
        return dataset.to_table(columns=requested, filter=filter_expr).to_pandas()
    except (OSError, PermissionError, TypeError, ValueError):
        return pd.DataFrame(columns=list(columns))


def _latest_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or not {"date", "symbol"}.issubset(frame.columns):
        return pd.DataFrame()
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    work = work.dropna(subset=["date", "symbol"])
    if work.empty:
        return work
    latest = work["date"].max()
    return work.loc[work["date"].eq(latest)].copy()


def _kospi200_members(project_root: Path) -> dict[str, dict[str, str]]:
    """Prefer exact retained membership; the broad canonical schema is checked first."""
    root = Path(project_root)
    candidates = (
        root / "data/published/kr_equity_canonical_universe_daily",
        root / "data/normalized/kr_index_constituent_daily",
    )
    for index, dataset_root in enumerate(candidates):
        paths = _recent_paths(dataset_root)
        if not paths:
            continue
        try:
            dataset = pads.dataset([str(path) for path in paths], format="parquet", partitioning=None)
            schema = set(dataset.schema.names)
        except (OSError, PermissionError, TypeError, ValueError):
            continue
        # Canonical equity membership normally has no index identity. Do not infer it.
        if index == 0 and not ({"index_symbol", "symbol", "date"} <= schema):
            continue
        columns = ["date", "symbol", "name", "market", "index_symbol", "index_ticker"]
        frame = _read_paths(paths, columns=columns)
        if frame.empty:
            continue
        if "index_symbol" in frame:
            frame = frame.loc[frame["index_symbol"].astype(str).eq("KOSPI200")]
        elif "index_ticker" in frame:
            frame = frame.loc[frame["index_ticker"].astype(str).eq("1028")]
        latest = _latest_rows(frame)
        if latest.empty or latest["symbol"].astype(str).duplicated().any():
            continue
        return {
            str(row["symbol"]): {
                "symbol": str(row["symbol"]),
                "name": str(row.get("name") or row["symbol"]),
                "market": str(row.get("market") or "KOSPI"),
            }
            for row in latest.to_dict(orient="records")
        }
    return {}


def _market_cap_proxy(project_root: Path) -> dict[str, dict[str, str]]:
    root = Path(project_root) / "data/normalized/kr_equity_market_cap_daily"
    frame = _read_paths(
        _recent_paths(root), columns=["date", "market", "symbol", "market_cap"],
    )
    if frame.empty or "market_cap" not in frame:
        return {}
    latest = _latest_rows(frame)
    latest["market_cap"] = pd.to_numeric(latest["market_cap"], errors="coerce")
    latest = latest.dropna(subset=["market_cap"])
    latest = latest.loc[latest["market_cap"].gt(0)].nlargest(200, "market_cap", keep="first")
    return {
        str(row["symbol"]): {
            "symbol": str(row["symbol"]), "name": str(row["symbol"]),
            "market": str(row.get("market") or "KOSPI"),
        }
        for row in latest.to_dict(orient="records")
    }


def _json_object(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _public_identities(project_root: Path) -> dict[str, dict[str, str]]:
    payload = _json_object(Path(project_root) / "config/public_watchlist.json")
    items = payload.get("items") if payload.get("schema_version") == 1 else None
    identities: dict[str, dict[str, str]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol:
            identities[symbol] = {
                "symbol": symbol,
                "name": str(item.get("name") or symbol).strip(),
                "market": str(item.get("market") or "").strip(),
            }
    return identities


def _private_watchlist_identities(project_root: Path) -> dict[str, dict[str, str]]:
    root = Path(project_root)
    identities: dict[str, dict[str, str]] = {}
    payload = _json_object(root / "artifacts/local_user/watchlists.json")
    lists = payload.get("lists")
    for watchlist in lists if isinstance(lists, list) else []:
        items = watchlist.get("items") if isinstance(watchlist, Mapping) else None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            if symbol:
                identities[symbol] = {
                    "symbol": symbol,
                    "name": str(item.get("name") or symbol).strip(),
                    "market": str(item.get("market") or "").strip(),
                }
    global_payload = _json_object(root / "artifacts/local_user/watchlist_global_equities.json")
    global_lists = global_payload.get("lists")
    registered = {str(item["symbol"]): item for item in global_equity_identities()}
    if isinstance(global_lists, Mapping):
        for items in global_lists.values():
            for item in items if isinstance(items, list) else []:
                symbol = str(item.get("symbol") or "").strip().upper() if isinstance(item, Mapping) else ""
                if symbol:
                    identity = registered.get(symbol, {})
                    identities[symbol] = {
                        "symbol": symbol,
                        "name": str(identity.get("name") or symbol),
                        "market": "US 주식",
                    }
    return identities


def _registered_us_identities() -> dict[str, dict[str, str]]:
    from stock_web.api.symbol_resolver import _us_etf_identities

    identities = {
        str(item["symbol"]): {
            "symbol": str(item["symbol"]), "name": str(item.get("name") or item["symbol"]),
            "market": str(item.get("market") or "US ETF"),
        }
        for item in _us_etf_identities()
    }
    for item in global_equity_identities():
        identities[str(item["symbol"])] = {
            "symbol": str(item["symbol"]), "name": str(item.get("name") or item["symbol"]),
            "market": "US 주식",
        }
    return identities


def _held_identities(project_root: Path) -> dict[str, dict[str, str]]:
    try:
        from stock_web.api.account_page import build_account_page_data
        from stock_web.api.home_cards import _held_symbols

        kr, us = _held_symbols(Path(project_root), build_account_page_data(Path(project_root)))
    except Exception:
        return {}
    return {
        **{symbol: {"symbol": symbol, "name": symbol, "market": "KRX"} for symbol in kr},
        **{symbol: {"symbol": symbol, "name": symbol, "market": "US"} for symbol in us},
    }


def _master_names(project_root: Path, symbols: set[str]) -> dict[str, str]:
    if not symbols:
        return {}
    frames: list[pd.DataFrame] = []
    for relative in ("data/normalized/kr_equity_master", "data/normalized/kr_etf_master"):
        root = Path(project_root) / relative
        paths = [*root.glob("*.parquet"), *root.glob("*/*.parquet")]
        frame = _read_paths(paths, columns=["date", "symbol", "name"], symbols=symbols)
        if not frame.empty and "name" in frame:
            frames.append(frame)
    if not frames:
        return {}
    work = pd.concat(frames, ignore_index=True, sort=False).dropna(subset=["symbol", "name"])
    return dict(zip(work["symbol"].astype(str), work["name"].astype(str)))


def _collect_context(
    project_root: Path, *, public_mode: bool,
) -> tuple[dict[str, dict[str, str]], list[dict[str, object]], set[str]]:
    if public_mode:
        public = _public_identities(project_root)
        return public, [], set(public)
    watchlist = _private_watchlist_identities(project_root)
    held = _held_identities(project_root)
    kospi = _kospi200_members(project_root) or _market_cap_proxy(project_root)
    identities = {**kospi, **_registered_us_identities(), **held, **watchlist}
    names = _master_names(project_root, set(identities))
    for symbol, name in names.items():
        if symbol in identities:
            identities[symbol]["name"] = name
    conditions = list(load_conditions(Path(project_root)).get("conditions", []))
    return identities, conditions, set(watchlist) | set(held)


def _load_price_frame(project_root: Path, identities: Mapping[str, Mapping[str, str]]) -> pd.DataFrame:
    root = Path(project_root)
    symbols = set(identities)
    if not symbols:
        return pd.DataFrame(columns=["date", "series_id", "basket", "symbol", "close", "volume"])
    # KRX codes are six characters and may contain letters (ETFs like 0015B0); use the
    # identity market first and fall back to the code shape.
    def _is_kr(symbol: str) -> bool:
        market = str(identities.get(symbol, {}).get("market") or "").upper()
        if market in {"KRX", "KOSPI", "KOSDAQ", "KR"}:
            return True
        if market in {"US", "US ETF", "US 주식", "NASDAQ", "NYSE", "AMEX"}:
            return False
        return len(symbol) == 6 and symbol.isalnum() and any(ch.isdigit() for ch in symbol)

    kr = {symbol for symbol in symbols if _is_kr(symbol)}
    us = symbols - kr
    sources = [
        ("KR", _read_paths(
            _recent_paths(root / "data/normalized/kr_equity_price_daily"),
            columns=["date", "market", "symbol", "close", "volume"], symbols=kr,
        )),
        ("KR", _read_paths(
            _recent_paths(root / "data/normalized/kr_etf_price_daily", symbols=kr),
            columns=["date", "market", "symbol", "close", "volume"], symbols=kr,
        )),
        ("US", _read_paths(
            _recent_paths(root / "data/normalized/global_equity_price_daily", symbols=us),
            columns=["date", "market", "symbol", "close", "volume"], symbols=us,
        )),
        ("US", _read_paths(
            _recent_paths(root / "data/normalized/global_etf_price_daily", symbols=us),
            columns=["date", "market", "symbol", "close", "volume"], symbols=us,
        )),
    ]
    frames: list[pd.DataFrame] = []
    for index, (basket, frame) in enumerate(sources):
        if not frame.empty:
            tagged = frame.copy()
            tagged["basket"] = basket
            tagged["source"] = f"{basket}:{index}"
            frames.append(tagged)
    if not frames:
        return pd.DataFrame(columns=["date", "series_id", "basket", "symbol", "close", "volume"])
    price = pd.concat(frames, ignore_index=True, sort=False)
    price["date"] = pd.to_datetime(price["date"], errors="coerce").dt.normalize()
    price["symbol"] = price["symbol"].astype(str).str.upper()
    price["close"] = pd.to_numeric(price["close"], errors="coerce")
    price["volume"] = pd.to_numeric(price.get("volume"), errors="coerce")
    price = price.loc[price["symbol"].isin(symbols)].dropna(subset=["date", "symbol", "close"])
    price = price.loc[np.isfinite(price["close"]) & price["close"].gt(0)]
    price = price.sort_values(["symbol", "date"], kind="stable").drop_duplicates(
        ["symbol", "date"], keep="last",
    )
    price["series_id"] = price["symbol"]
    columns = ["date", "series_id", "basket", "symbol", "close", "volume"]
    if "source" in price.columns:
        columns.append("source")
    return price[columns].reset_index(drop=True)


def _condition_metrics(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "rsi14": row.get("rsi14"),
        "disp60_pct": float(row["disp60"]) * 100.0 if pd.notna(row.get("disp60")) else None,
        "drawdown_pct": float(row["drawdown252"]) * 100.0 if pd.notna(row.get("drawdown252")) else None,
        "ma20_pct": float(row["ma20_pct"]) if pd.notna(row.get("ma20_pct")) else None,
        "change_pct": float(row["change_pct"]) if pd.notna(row.get("change_pct")) else None,
    }


def _matched_condition_ids(
    row: Mapping[str, object], conditions: Iterable[Mapping[str, object]], *, watch_eligible: bool,
) -> set[str]:
    matched: set[str] = set()
    metrics = _condition_metrics(row)
    for condition in conditions:
        scope = str(condition.get("scope") or "")
        if scope == "watchlist" and not watch_eligible:
            continue
        if scope not in {"watchlist", "universe"}:
            continue
        hits = evaluate_conditions(metrics, [condition], scope=scope)
        if hits:
            matched.add(str(condition.get("id") or ""))
    return matched


def _display_name(identity: Mapping[str, object], symbol: str) -> str:
    name = str(identity.get("name") or symbol).strip()
    return symbol if len(name) > 16 and symbol else name


def _rule_changes(
    previous: Mapping[str, str], current: Mapping[str, str],
) -> list[dict[str, str]]:
    return [
        {"rule": rule, "from_level": str(previous[rule]), "to_level": str(current[rule])}
        for rule in sorted(set(previous) & set(current))
        if str(previous[rule]) != str(current[rule])
    ]


def _changes_from_frame(
    price: pd.DataFrame, *, identities: Mapping[str, Mapping[str, str]],
    conditions: Iterable[Mapping[str, object]], watch_scope: set[str],
    previous_rule_levels: Mapping[str, str] | None = None,
    current_rule_levels: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if price.empty:
        return _empty_payload()
    source = price.copy()
    source["date"] = pd.to_datetime(source["date"], errors="coerce").dt.normalize()
    source["series_id"] = source["series_id"].astype(str)
    source["symbol"] = source.get("symbol", source["series_id"]).astype(str)
    source["basket"] = source.get("basket", "CHANGES").astype(str)
    source["close"] = pd.to_numeric(source["close"], errors="coerce")
    source["volume"] = pd.to_numeric(source.get("volume"), errors="coerce")
    source = source.dropna(subset=["date", "series_id", "symbol", "close"])
    source = source.loc[np.isfinite(source["close"]) & source["close"].gt(0)]
    source = source.sort_values(["series_id", "date"], kind="stable").drop_duplicates(
        ["series_id", "date"], keep="last",
    )
    if source.empty:
        return _empty_payload()
    signals = compute_signals(source)
    if "source" not in signals.columns and "source" in source.columns:
        # compute_signals keeps only the contract columns; restore the dataset tag.
        source_by_series = source.drop_duplicates("series_id").set_index("series_id")["source"]
        signals["source"] = signals["series_id"].map(source_by_series)
    grouped = signals.groupby("series_id", sort=False)
    signals["ma20"] = grouped["close"].transform(lambda values: values.rolling(20, min_periods=20).mean())
    signals["ma20_pct"] = (signals["close"] / signals["ma20"] - 1.0) * 100.0
    signals["change_pct"] = grouped["close"].pct_change(fill_method=None) * 100.0
    signals["low252"] = grouped["close"].transform(lambda values: values.rolling(252, min_periods=252).min())

    by_id = {str(condition.get("id") or ""): condition for condition in conditions}
    entries: list[dict[str, str]] = []
    exits: list[dict[str, str]] = []
    new_high_rows: list[dict[str, str]] = []
    new_low_rows: list[dict[str, str]] = []
    spikes: list[dict[str, object]] = []
    latest_dates: list[pd.Timestamp] = []
    # Session pairs are taken per source dataset: KRX equities finalise D+1 while KRX
    # ETFs and US series carry the latest session, so pairing per basket would drop
    # every equity on the day the ETF dataset is one session ahead.
    pair_key = "source" if "source" in signals.columns else "basket"
    session_pairs: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for key, key_frame in signals.groupby(pair_key, sort=False):
        dates = sorted(pd.Timestamp(value) for value in key_frame["date"].dropna().unique())
        if len(dates) >= 2:
            session_pairs[str(key)] = (dates[-2], dates[-1])
    for series_id, group in signals.groupby("series_id", sort=True):
        if len(group) < 2:
            continue
        previous = group.iloc[-2].to_dict()
        current = group.iloc[-1].to_dict()
        basket = str(current.get("basket") or "")
        expected = session_pairs.get(str(current.get(pair_key) or basket))
        if expected is None or (
            pd.Timestamp(previous["date"]) != expected[0]
            or pd.Timestamp(current["date"]) != expected[1]
        ):
            continue
        symbol = str(current.get("symbol") or series_id)
        identity = identities.get(symbol, {"name": symbol})
        latest_dates.append(pd.Timestamp(current["date"]))
        before = _matched_condition_ids(previous, by_id.values(), watch_eligible=symbol in watch_scope)
        after = _matched_condition_ids(current, by_id.values(), watch_eligible=symbol in watch_scope)
        for identifier, target in ((item, entries) for item in sorted(after - before)):
            condition = by_id.get(identifier, {})
            name = str(condition.get("name") or identifier)
            target.append({
                "condition_id": identifier, "name": name, "symbol": symbol,
                "display": f"{_display_name(identity, symbol)} {name}",
            })
        for identifier, target in ((item, exits) for item in sorted(before - after)):
            condition = by_id.get(identifier, {})
            name = str(condition.get("name") or identifier)
            target.append({
                "condition_id": identifier, "name": name, "symbol": symbol,
                "display": f"{_display_name(identity, symbol)} {name}",
            })

        previous_high = pd.notna(previous.get("high252")) and math.isclose(
            float(previous["close"]), float(previous["high252"]), rel_tol=1e-12,
        )
        current_high = pd.notna(current.get("high252")) and math.isclose(
            float(current["close"]), float(current["high252"]), rel_tol=1e-12,
        )
        previous_low = pd.notna(previous.get("low252")) and math.isclose(
            float(previous["close"]), float(previous["low252"]), rel_tol=1e-12,
        )
        current_low = pd.notna(current.get("low252")) and math.isclose(
            float(current["close"]), float(current["low252"]), rel_tol=1e-12,
        )
        if current_high and not previous_high:
            new_high_rows.append({"symbol": symbol, "display": _display_name(identity, symbol)})
        if current_low and not previous_low:
            new_low_rows.append({"symbol": symbol, "display": _display_name(identity, symbol)})

        previous_ratio = previous.get("volume_ratio20")
        current_ratio = current.get("volume_ratio20")
        if (
            pd.notna(current_ratio) and float(current_ratio) >= 3.0
            and (pd.isna(previous_ratio) or float(previous_ratio) < 3.0)
        ):
            spikes.append({
                "symbol": symbol,
                "display": _display_name(identity, symbol),
                "ratio": round(float(current_ratio), 2),
            })

    entries.sort(key=lambda item: (item["name"], item["symbol"]))
    exits.sort(key=lambda item: (item["name"], item["symbol"]))
    new_high_rows.sort(key=lambda item: item["symbol"])
    new_low_rows.sort(key=lambda item: item["symbol"])
    spikes.sort(key=lambda item: (-float(item["ratio"]), str(item["symbol"])))
    rules = _rule_changes(previous_rule_levels or {}, current_rule_levels or {})
    payload = {
        "as_of": max(latest_dates).date().isoformat() if latest_dates else None,
        "rule_changes": rules,
        "condition_entries": entries,
        "condition_exits": exits,
        "new_highs_52w": len(new_high_rows),
        "new_lows_52w": len(new_low_rows),
        "new_highs_52w_list": new_high_rows,
        "new_lows_52w_list": new_low_rows,
        "volume_spikes": spikes[:5],
        "counts": {
            "rule_changes": len(rules),
            "condition_entries": len(entries),
            "condition_exits": len(exits),
            "new_highs_52w": len(new_high_rows),
            "new_lows_52w": len(new_low_rows),
            "volume_spikes": len(spikes),
        },
    }
    return payload


def _rule_levels(project_root: Path, *, public_mode: bool) -> dict[str, str]:
    if public_mode:
        return {}
    try:
        from stock_web.api.home_data import build_account
        from stock_web.api.regime import build_regime, build_rules

        account = build_account(Path(project_root))
        markets = build_regime(Path(project_root), account).get("markets", [])
        rules = build_rules(account, markets, Path(project_root))
        rows = rules.get("rows") if isinstance(rules, Mapping) else None
        if not isinstance(rows, list):
            return {}
        return {
            str(row[0]): str(row[1])
            for row in rows if isinstance(row, list) and len(row) >= 2
        }
    except Exception:
        return {}


def _read_cache(
    path: Path, *, signature: str, public_mode: bool,
) -> tuple[dict[str, object] | None, dict[str, str]]:
    payload = _json_object(path)
    old_levels = payload.get("rule_levels")
    previous_levels = {
        str(key): str(value) for key, value in old_levels.items()
    } if isinstance(old_levels, Mapping) and bool(payload.get("public_mode")) == public_mode else {}
    result = payload.get("result")
    if (
        payload.get("schema_version") == CHANGES_CACHE_SCHEMA_VERSION
        and payload.get("price_signature") == signature
        and bool(payload.get("public_mode")) == public_mode
        and isinstance(result, dict)
        and set(_empty_payload()).issubset(result)
    ):
        return result, previous_levels
    return None, previous_levels


def _write_cache(
    path: Path, result: Mapping[str, object], *, signature: str,
    public_mode: bool, rule_levels: Mapping[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{uuid4().hex}.tmp")
    envelope = {
        "schema_version": CHANGES_CACHE_SCHEMA_VERSION,
        "price_signature": signature,
        "public_mode": public_mode,
        "rule_levels": dict(rule_levels),
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
        temporary.unlink(missing_ok=True)


def build_changes(project_root: Path, public_mode: bool = False) -> dict[str, object]:
    """Return the retained-data T versus T-1 change summary for Home and Telegram."""
    root = Path(project_root)
    try:
        identities, conditions, watch_scope = _collect_context(root, public_mode=public_mode)
        current_rule_levels = _rule_levels(root, public_mode=public_mode)
        base_signature = _dataset_signature(root, public_mode=public_mode)
        identity_signature = [
            (symbol, str(item.get("name") or ""), str(item.get("market") or ""))
            for symbol, item in sorted(identities.items())
        ]
        signature = hashlib.sha256(
            (
                base_signature
                + json.dumps(current_rule_levels, ensure_ascii=False, sort_keys=True)
                + json.dumps(identity_signature, ensure_ascii=False, sort_keys=True)
            ).encode("utf-8")
        ).hexdigest()
        cached, previous_rule_levels = (
            (None, {}) if public_mode else _read_cache(
                _cache_path(root), signature=signature, public_mode=False,
            )
        )
        if cached is not None:
            return cached
        price = _load_price_frame(root, identities)
        if not price.empty and "series_id" in price:
            price = price.loc[price["series_id"].astype(str).isin(identities)].copy()
        result = _changes_from_frame(
            price, identities=identities, conditions=conditions, watch_scope=watch_scope,
            previous_rule_levels=previous_rule_levels,
            current_rule_levels=current_rule_levels,
        )
        if result["as_of"] is not None and not public_mode:
            try:
                _write_cache(
                    _cache_path(root), result, signature=signature, public_mode=public_mode,
                    rule_levels=current_rule_levels,
                )
            except OSError:
                pass
        return result
    except Exception:
        return _empty_payload()


__all__ = ["build_changes"]
