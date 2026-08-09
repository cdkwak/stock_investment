from __future__ import annotations

import json
from pathlib import Path
import time

import pandas as pd

from stock_data.contracts.kr_equity import (
    KR_EQUITY_CANONICAL_UNIVERSE_DAILY, KR_EQUITY_MARKET_CAP_DAILY,
    KR_EQUITY_MASTER, KR_EQUITY_PRICE_DAILY, KR_EQUITY_UNIVERSE_DAILY,
)
from stock_data.contracts.kr_market import KR_MARKET_BREADTH_DAILY
from stock_data.derived.market_breadth import calculate_market_breadth
from stock_data.pipelines.backfill_state import BackfillState
from stock_data.providers.data_go_kr.client import (
    DataGoKrClient, service_key_from_environment, write_landing_pages_atomic,
)
from stock_data.providers.data_go_kr.stock_price import (
    STOCK_PRICE_ENDPOINT, normalize_stock_price_items,
)
from stock_data.providers.data_go_kr.universe import UNIVERSE_ENDPOINT, normalize_universe_items
from stock_data.published.canonical_equity_universe import (
    build_canonical_universe, price_identity_from_items, validate_canonical_universe,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.validation.kr_equity import (
    validate_equity_market_cap, validate_equity_master, validate_equity_price,
)
from stock_data.validation.kr_market import validate_market_breadth


def _items(path: Path) -> list[dict]:
    pages = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for page in pages:
        raw = page["response"]["body"].get("items", {}).get("item", [])
        rows.extend(raw if isinstance(raw, list) else [raw])
    return rows


def _upsert_batch(frame, root, contract, validator):
    if root.exists():
        existing = read_dataset(root, contract, validator)
        frame = pd.concat([existing, frame], ignore_index=True)
        frame = frame.drop_duplicates(list(contract.primary_key), keep="last")
        frame = frame.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    validator(frame)
    write_dataset_atomic(frame, root, contract, validator)
    restored = read_dataset(root, contract, validator)
    if len(restored) != len(frame):
        raise RuntimeError("batch atomic read-back row count differs")


def _stage_dates(project_root, dates, *, endpoint, landing_root, state, interval, sleep_fn):
    calls = 0
    client = DataGoKrClient(
        endpoint=endpoint, service_key=service_key_from_environment(project_root),
        max_attempts=2, backoff_seconds=2.0, sleep_fn=sleep_fn,
    )
    pending = state.pending(dates)
    for index, date in enumerate(pending):
        landing = landing_root / f"{date}.json"
        if date in (state.staged_partitions or set()) and landing.exists():
            continue
        result = client.fetch_all(filters={"basDt": date}, num_of_rows=9999, max_pages=1)
        calls += len(result.pages)
        if result.total_count == 0:
            state.mark_valid_empty(date)
        else:
            write_landing_pages_atomic(result.pages, landing)
            state.mark_staged(date)
        if index + 1 < len(pending):
            sleep_fn(interval)
    return calls


def collect_equity_batch(project_root: Path, dates, *, chunk_size=200,
                         request_interval=0.5, sleep_fn=time.sleep):
    ordered = sorted(dict.fromkeys(str(value) for value in dates))
    if not ordered or any(len(value) != 8 or not value.isdigit() for value in ordered):
        raise ValueError("dates must contain YYYYMMDD values")
    if chunk_size < 1 or not 0.5 <= request_interval:
        raise ValueError("invalid batch safety settings")
    price_state = BackfillState.load(
        project_root / "data/state/kr_equity_price_cap_daily.json", "kr_equity_price_cap_daily")
    universe_state = BackfillState.load(
        project_root / "data/state/kr_equity_universe_daily.json", "kr_equity_universe_daily")
    calls = {"equity": 0, "universe": 0}
    for offset in range(0, len(ordered), chunk_size):
        chunk = ordered[offset:offset + chunk_size]
        price_landing = project_root / "data/landing/data_go_kr/stock_price"
        universe_landing = project_root / "data/landing/data_go_kr/kr_equity_universe_daily"
        calls["equity"] += _stage_dates(
            project_root, chunk, endpoint=STOCK_PRICE_ENDPOINT, landing_root=price_landing,
            state=price_state, interval=request_interval, sleep_fn=sleep_fn)
        if calls["equity"] and universe_state.pending(chunk):
            sleep_fn(request_interval)
        calls["universe"] += _stage_dates(
            project_root, chunk, endpoint=UNIVERSE_ENDPOINT, landing_root=universe_landing,
            state=universe_state, interval=request_interval, sleep_fn=sleep_fn)

        price_dates = [d for d in chunk if d in (price_state.staged_partitions or set())]
        if price_dates:
            normalized = normalize_stock_price_items(
                [item for d in price_dates for item in _items(price_landing / f"{d}.json")
                 if str(item.get("mrktCtg", "")).strip() in {"KOSPI", "KOSDAQ"}])
            _upsert_batch(normalized.price, project_root / "data/normalized/kr_equity_price_daily",
                          KR_EQUITY_PRICE_DAILY, validate_equity_price)
            _upsert_batch(normalized.market_cap, project_root / "data/normalized/kr_equity_market_cap_daily",
                          KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap)
            for date in price_dates:
                price_state.mark_completed(date)

        universe_dates = [d for d in chunk if d in (universe_state.staged_partitions or set())]
        if universe_dates:
            frame = normalize_universe_items(
                [item for d in universe_dates for item in _items(universe_landing / f"{d}.json")])
            validator = lambda value: validate_data_v1(value, KR_EQUITY_UNIVERSE_DAILY)
            _upsert_batch(frame, project_root / "data/normalized/kr_equity_universe_daily",
                          KR_EQUITY_UNIVERSE_DAILY, validator)
            for date in universe_dates:
                universe_state.mark_completed(date)
    return calls


def rebuild_canonical_and_breadth(project_root: Path, dates):
    ordered = sorted(dict.fromkeys(str(value) for value in dates))
    selected = set(ordered)
    listed = read_dataset(
        project_root / "data/normalized/kr_equity_universe_daily",
        KR_EQUITY_UNIVERSE_DAILY,
        lambda value: validate_data_v1(value, KR_EQUITY_UNIVERSE_DAILY))
    listed = listed[listed.date.str.replace("-", "").isin(selected)].reset_index(drop=True)
    price_landing = project_root / "data/landing/data_go_kr/stock_price"
    identity = price_identity_from_items(
        [item for date in ordered for item in _items(price_landing / f"{date}.json")])
    master = read_dataset(
        project_root / "data/normalized/kr_equity_master", KR_EQUITY_MASTER,
        validate_equity_master)
    canonical = build_canonical_universe(listed, identity, master)
    _upsert_batch(
        canonical, project_root / "data/published/kr_equity_canonical_universe_daily",
        KR_EQUITY_CANONICAL_UNIVERSE_DAILY, validate_canonical_universe)
    canonical_all = read_dataset(
        project_root / "data/published/kr_equity_canonical_universe_daily",
        KR_EQUITY_CANONICAL_UNIVERSE_DAILY, validate_canonical_universe)
    prices = read_dataset(
        project_root / "data/normalized/kr_equity_price_daily",
        KR_EQUITY_PRICE_DAILY, validate_equity_price)
    breadth = calculate_market_breadth(prices, canonical_all)
    breadth = breadth[breadth.date.str.replace("-", "").isin(selected)].reset_index(drop=True)
    _upsert_batch(
        breadth, project_root / "data/derived/kr_market_breadth_daily",
        KR_MARKET_BREADTH_DAILY, validate_market_breadth)
    return {"canonical_rows": len(canonical), "breadth_rows": len(breadth)}
