from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import time

from dotenv import load_dotenv
import pandas as pd
import requests

from stock_data.contracts.kr_equity import (
    KR_EQUITY_CANONICAL_UNIVERSE_DAILY, KR_EQUITY_MARKET_CAP_DAILY,
    KR_EQUITY_MASTER, KR_EQUITY_PRICE_DAILY, KR_EQUITY_UNIVERSE_DAILY,
)
from stock_data.contracts.kr_market import KR_MARKET_BREADTH_DAILY
from stock_data.derived.market_breadth import calculate_market_breadth
from stock_data.pipelines.backfill_state import BackfillState
from stock_data.pipelines.equity_batch_collection import _mark_completed_batch
from stock_data.published.canonical_equity_universe import (
    build_canonical_universe, validate_canonical_universe,
)
from stock_data.providers.krx_open_api.equity import normalize_basic_info, normalize_daily_trade
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.validation.kr_equity import (
    validate_equity_market_cap, validate_equity_master, validate_equity_price,
)
from stock_data.validation.kr_market import validate_market_breadth


APIS = ("stk_bydd_trd", "ksq_bydd_trd", "stk_isu_base_info", "ksq_isu_base_info")
MARKET_APIS = {
    "KOSPI": ("stk_bydd_trd", "stk_isu_base_info"),
    "KOSDAQ": ("ksq_bydd_trd", "ksq_isu_base_info"),
}


class KrxStop(RuntimeError):
    pass


@dataclass
class CallLedger:
    path: Path
    calendar_date: str
    calls: int
    safety_cap: int = 8000

    @classmethod
    def load(cls, path: Path, *, observed_calls=0, safety_cap=8000):
        today = datetime.now().astimezone().date().isoformat()
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("calendar_date") == today:
                return cls(path, today, max(int(payload.get("calls", 0)), observed_calls), safety_cap)
        ledger = cls(path, today, observed_calls, safety_cap)
        ledger.save()
        return ledger

    @property
    def remaining(self):
        return max(0, self.safety_cap - self.calls)

    def record_request(self):
        if self.remaining < 1:
            raise KrxStop("project daily KRX call cap reached")
        self.calls += 1
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json.tmp",
                                             prefix=self.path.stem + "_", dir=self.path.parent,
                                             delete=False) as handle:
                json.dump({"calendar_date": self.calendar_date, "calls": self.calls,
                           "project_safety_cap": self.safety_cap}, handle, indent=2)
                temporary = Path(handle.name)
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _request(api, date, key, ledger, session=requests, sleep_fn=time.sleep):
    url = f"https://data-dbg.krx.co.kr/svc/apis/sto/{api}"
    for attempt in range(2):
        ledger.record_request()
        try:
            response = session.get(url, headers={"AUTH_KEY": key, "User-Agent": "stock-investment-rev1/0.1"},
                                   params={"basDd": date}, timeout=20, allow_redirects=False)
        except requests.RequestException as error:
            if attempt == 0:
                sleep_fn(1.0)
                continue
            raise KrxStop(f"KRX transport failure: {type(error).__name__}") from None
        content_type = response.headers.get("content-type", "")
        if response.status_code in {401, 403, 429} or "html" in content_type.lower():
            raise KrxStop(f"KRX immediate-stop response: HTTP {response.status_code}")
        if response.status_code >= 500:
            if attempt == 0:
                sleep_fn(1.0)
                continue
            raise KrxStop(f"KRX server failure: HTTP {response.status_code}")
        if response.status_code != 200 or "json" not in content_type.lower():
            raise KrxStop(f"KRX unexpected response: HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("OutBlock_1"), list):
            raise KrxStop("KRX response schema is invalid")
        return payload
    raise AssertionError("unreachable")


def _write_json_atomic(payload, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json.tmp",
                                         prefix=path.stem + "_", dir=path.parent,
                                         delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            temporary = Path(handle.name)
        verified = json.loads(temporary.read_text(encoding="utf-8"))
        if verified != payload:
            raise RuntimeError("KRX landing read-back differs")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _partition_upsert(incoming, root, contract, validator):
    working = incoming.copy()
    working["year"] = pd.to_datetime(working["date"]).dt.year
    for (market, year), group in working.groupby(["market", "year"], sort=True):
        group = group.drop(columns="year").reset_index(drop=True)
        path = root / f"market={market}" / f"year={year}" / "data.parquet"
        if path.exists():
            existing = pd.read_parquet(path)
            existing["date"] = pd.to_datetime(existing["date"]).dt.strftime("%Y-%m-%d")
            group = pd.concat([existing[list(contract.column_names)], group], ignore_index=True)
            group = group.drop_duplicates(list(contract.primary_key), keep="last")
        group = group.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        validator(group)
        write_dataset_atomic(group, root, contract, validator)
        restored = pd.read_parquet(path)
        restored["date"] = pd.to_datetime(restored["date"]).dt.strftime("%Y-%m-%d")
        validator(restored[list(contract.column_names)].sort_values(
            list(contract.sort_key), kind="stable").reset_index(drop=True))


def rebuild_krx_historical_derived(project_root: Path, dates) -> dict[str, int]:
    """Build downstream rows only for committed KRX historical dates."""
    selected = {str(value) for value in dates}
    universe = read_dataset(
        project_root / "data/normalized/kr_equity_universe_daily",
        KR_EQUITY_UNIVERSE_DAILY,
        lambda value: validate_data_v1(value, KR_EQUITY_UNIVERSE_DAILY),
    )
    historical = universe[
        universe["date"].str.replace("-", "").isin(selected)
        & universe["source"].eq("krx_open_api")
    ].reset_index(drop=True)
    if historical.empty:
        raise ValueError("no committed KRX historical universe rows")
    master = read_dataset(
        project_root / "data/normalized/kr_equity_master",
        KR_EQUITY_MASTER,
        validate_equity_master,
    )
    identity = historical[["date", "market", "symbol", "isin", "name"]].copy()
    canonical = build_canonical_universe(historical, identity, master)
    _partition_upsert(
        canonical,
        project_root / "data/published/kr_equity_canonical_universe_daily",
        KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
        validate_canonical_universe,
    )

    canonical_all = read_dataset(
        project_root / "data/published/kr_equity_canonical_universe_daily",
        KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
        validate_canonical_universe,
    )
    prices = read_dataset(
        project_root / "data/normalized/kr_equity_price_daily",
        KR_EQUITY_PRICE_DAILY,
        validate_equity_price,
    )
    breadth = calculate_market_breadth(prices, canonical_all)
    breadth = breadth[breadth["date"].str.replace("-", "").isin(selected)].reset_index(drop=True)
    _partition_upsert(
        breadth,
        project_root / "data/derived/kr_market_breadth_daily",
        KR_MARKET_BREADTH_DAILY,
        validate_market_breadth,
    )
    return {
        "canonical_rows": len(canonical),
        "breadth_rows": len(breadth),
        "master_missing_rows": int((~canonical["master_present"]).sum()),
    }


def run_krx_historical_backfill(project_root: Path, dates, *, observed_calls=0,
                                batch_size=200, session=requests, sleep_fn=time.sleep):
    load_dotenv(project_root / ".env", override=False)
    key = os.getenv("KRX_AUTH_KEY", "").strip()
    if not key:
        raise RuntimeError("KRX_AUTH_KEY is not configured")
    ledger = CallLedger.load(project_root / "data/state/krx_open_api_call_ledger.json",
                             observed_calls=observed_calls)
    state = BackfillState.load(project_root / "data/state/krx_equity_historical.json",
                               "krx_equity_historical")
    ordered = state.pending(sorted(dict.fromkeys(dates)))
    allowed_dates = min(len(ordered), ledger.remaining // 4)
    ordered = ordered[:allowed_dates]
    started = time.monotonic()
    completed_now = 0
    for offset in range(0, len(ordered), batch_size):
        chunk = ordered[offset:offset + batch_size]
        ready = []
        try:
            for date in chunk:
                payloads = {}
                for api in APIS:
                    landing = project_root / "data/landing/krx_open_api" / api / f"{date}.json"
                    if landing.exists():
                        payload = json.loads(landing.read_text(encoding="utf-8"))
                        if not isinstance(payload, dict) or not isinstance(payload.get("OutBlock_1"), list):
                            raise KrxStop("stored KRX landing schema is invalid")
                    else:
                        payload = _request(api, date, key, ledger, session=session, sleep_fn=sleep_fn)
                        _write_json_atomic(payload, landing)
                    payloads[api] = payload["OutBlock_1"]
                for market, (trade_api, basic_api) in MARKET_APIS.items():
                    trade_symbols = {str(row["ISU_CD"]).strip().removeprefix("A").zfill(6)
                                     for row in payloads[trade_api]}
                    basic_symbols = {str(row["ISU_SRT_CD"]).strip().removeprefix("A").zfill(6)
                                     for row in payloads[basic_api]}
                    if trade_symbols != basic_symbols:
                        raise KrxStop(f"KRX price/basic universe mismatch for {date} {market}")
                ready.append((date, payloads))
                if (completed_now + len(ready)) % 50 == 0:
                    print("KRX_PROGRESS", completed_now + len(ready), ledger.calls, flush=True)
        except Exception:
            if ready:
                _commit(project_root, ready, state)
                completed_now += len(ready)
            raise
        if ready:
            _commit(project_root, ready, state)
            completed_now += len(ready)
    elapsed = time.monotonic() - started
    return {"completed_dates": completed_now, "calls_today": ledger.calls,
            "calls_this_run": ledger.calls - observed_calls, "elapsed_seconds": elapsed,
            "daily_cap_reached": ledger.remaining < 4}


def _commit(project_root, ready, state):
    prices, caps, universes = [], [], []
    for date, payloads in ready:
        for market, (trade_api, basic_api) in MARKET_APIS.items():
            trade = normalize_daily_trade(payloads[trade_api], market)
            prices.append(trade.price)
            caps.append(trade.market_cap)
            universes.append(normalize_basic_info(payloads[basic_api], market, date))
    price = pd.concat(prices, ignore_index=True).sort_values(
        list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    cap = pd.concat(caps, ignore_index=True).sort_values(
        list(KR_EQUITY_MARKET_CAP_DAILY.sort_key), kind="stable").reset_index(drop=True)
    universe = pd.concat(universes, ignore_index=True).sort_values(
        list(KR_EQUITY_UNIVERSE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    _partition_upsert(price, project_root / "data/normalized/kr_equity_price_daily",
                      KR_EQUITY_PRICE_DAILY, validate_equity_price)
    _partition_upsert(cap, project_root / "data/normalized/kr_equity_market_cap_daily",
                      KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap)
    validator = lambda value: validate_data_v1(value, KR_EQUITY_UNIVERSE_DAILY)
    _partition_upsert(universe, project_root / "data/normalized/kr_equity_universe_daily",
                      KR_EQUITY_UNIVERSE_DAILY, validator)
    _mark_completed_batch(state, [date for date, _ in ready])
