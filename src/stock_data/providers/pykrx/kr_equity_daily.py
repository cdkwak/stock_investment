from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
import json
from pathlib import Path
import re
import tempfile
import time
from typing import Callable, Iterable

import pandas as pd
from dotenv import load_dotenv

from stock_data.contracts.kr_equity import (
    KR_EQUITY_MARKET_CAP_DAILY,
    KR_EQUITY_MASTER,
    KR_EQUITY_PRICE_DAILY,
)
from stock_data.storage.equity_parquet import read_partitioned, write_partitioned_atomic
from stock_data.validation.kr_equity import (
    validate_equity_market_cap,
    validate_equity_master,
    validate_equity_price,
)
from stock_data.providers.pykrx.safety import PykrxRequestPolicy, require_manual_live_access


PROJECT_ROOT = Path(__file__).resolve().parents[4]
NORMALIZED_ROOT = PROJECT_ROOT / "data" / "normalized"
STATE_PATH = PROJECT_ROOT / "data" / "state" / "kr_equity_daily.json"
MARKETS = ("KOSPI", "KOSDAQ")
MAX_RETRIES = 3
RETRY_DELAYS = (1.0, 2.0)

PRICE_MAP = {
    "시가": "open", "고가": "high", "저가": "low", "종가": "close",
    "거래량": "volume", "거래대금": "trading_value",
}
CAP_MAP = {"시가총액": "market_cap", "상장주식수": "shares_outstanding"}
NAME_MAP = {"종목명": "name"}


class EquityCollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DailySourceFrames:
    price: pd.DataFrame
    market_cap: pd.DataFrame
    master: pd.DataFrame


@dataclass(frozen=True)
class EquityCollectionResult:
    requested_dates: int
    completed_dates: int
    price_rows: int
    market_cap_rows: int
    master_rows: int
    price_root: Path
    market_cap_root: Path
    master_root: Path


def _sanitize(value: object) -> str:
    text = str(value)
    return re.sub(
        r"(?i)\b(KRX_ID|KRX_PW|API_KEY|PASSWORD|TOKEN|SECRET)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]", text,
    )


def _stock_module():
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            load_dotenv(PROJECT_ROOT / ".env", override=False)
            from pykrx import stock
        return stock
    except Exception as error:
        raise EquityCollectionError(
            f"pykrx initialization failed: {type(error).__name__}: {_sanitize(error)}"
        ) from None


def _indexed(dataframe: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    if dataframe.empty:
        return pd.DataFrame(columns=["symbol", *mapping.values()])
    normalized = dataframe.rename(columns=mapping).copy()
    missing = set(mapping.values()) - set(normalized.columns)
    if missing:
        raise EquityCollectionError(f"pykrx response columns missing: {sorted(missing)}")
    normalized.index = normalized.index.map(str)
    normalized.index.name = "symbol"
    return normalized.reset_index()[["symbol", *mapping.values()]]


def _fetch_once(
    stock, requested: date, market: str, policy: PykrxRequestPolicy | None = None
) -> DailySourceFrames:
    text = requested.strftime("%Y%m%d")
    policy = policy or PykrxRequestPolicy(min_interval_seconds=0)
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        policy.before_request()
        ohlcv = stock.get_market_ohlcv(text, market=market)
        policy.before_request()
        market_cap = stock.get_market_cap(text, market=market)
        policy.before_request()
        names = stock.get_market_price_change(
            text, text, market=market, adjusted=True, delist=False
        )
    if ohlcv.empty and market_cap.empty and names.empty:
        return DailySourceFrames(
            pd.DataFrame(columns=KR_EQUITY_PRICE_DAILY.column_names),
            pd.DataFrame(columns=KR_EQUITY_MARKET_CAP_DAILY.column_names),
            pd.DataFrame(columns=KR_EQUITY_MASTER.column_names),
        )
    if ohlcv.empty or market_cap.empty or names.empty:
        raise EquityCollectionError(f"{requested} {market} source frames are inconsistently empty")
    prices = _indexed(ohlcv, PRICE_MAP)
    caps = _indexed(market_cap, CAP_MAP)
    identities = _indexed(names, NAME_MAP)
    price_symbols = set(prices["symbol"])
    if set(caps["symbol"]) != price_symbols or not price_symbols.issubset(set(identities["symbol"])):
        raise EquityCollectionError(f"{requested} {market} source symbol sets do not match")
    common = {"date": requested.isoformat(), "market": market,
              "source": "pykrx", "source_operation": "manual_fixture_validation",
              "source_date": requested.isoformat()}
    price = prices.assign(**common)[list(KR_EQUITY_PRICE_DAILY.column_names)]
    cap = caps.assign(**common)[list(KR_EQUITY_MARKET_CAP_DAILY.column_names)]
    master = identities.loc[identities["symbol"].isin(price_symbols)].assign(market=market)
    master = master.assign(
        isin=None, corp_no=None, company_name=None, security_type_code=None,
        security_type_name=None, par_value=None, issued_shares=None,
        listing_date=None, delisting_date=None, deposit_registration_date=None,
        deposit_cancellation_date=None, source="pykrx_observed_identity",
        source_date=requested.isoformat(),
    )
    master = master[list(KR_EQUITY_MASTER.column_names)]
    price = price.sort_values(list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    cap = cap.sort_values(list(KR_EQUITY_MARKET_CAP_DAILY.sort_key), kind="stable").reset_index(drop=True)
    master = master.sort_values(list(KR_EQUITY_MASTER.sort_key), kind="stable").reset_index(drop=True)
    validate_equity_price(price)
    validate_equity_market_cap(cap)
    validate_equity_master(master)
    return DailySourceFrames(price, cap, master)


def fetch_market_day(
    requested: date,
    market: str,
    *,
    stock_module=None,
    sleep_fn: Callable[[float], None] = time.sleep,
    policy: PykrxRequestPolicy | None = None,
    manual: bool = False,
) -> DailySourceFrames:
    if market not in MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if stock_module is None:
        require_manual_live_access(manual=manual, requested_days=1)
    request_policy = policy or PykrxRequestPolicy(sleep_fn=sleep_fn)
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            stock = stock_module or _stock_module()
            result = _fetch_once(stock, requested, market, request_policy)
            request_policy.record_success()
            return result
        except Exception as error:
            last_error = error
            request_policy.record_failure()
    raise EquityCollectionError(
        f"{requested} {market} collection failed after {MAX_RETRIES} attempts: "
        f"{type(last_error).__name__}: {_sanitize(last_error)}"
    ) from None


def _merge(existing: pd.DataFrame | None, incoming: pd.DataFrame, contract) -> pd.DataFrame:
    if existing is None:
        combined = incoming.copy()
    else:
        keys = list(contract.primary_key)
        incoming_keys = set(map(tuple, incoming[keys].astype(str).to_numpy()))
        existing_key_values = existing[keys].astype(str).apply(tuple, axis=1)
        combined = pd.concat(
            [existing.loc[~existing_key_values.isin(incoming_keys)], incoming], ignore_index=True
        )
    return combined.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)


def _read_optional(root: Path, contract, validator) -> pd.DataFrame | None:
    return read_partitioned(root, contract, validator) if root.exists() else None


def _atomic_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json.tmp", prefix=path.stem + "_",
            dir=path.parent, delete=False,
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def collect_equity_dates(
    dates: Iterable[date],
    *,
    normalized_root: Path = NORMALIZED_ROOT,
    state_path: Path = STATE_PATH,
    fetcher: Callable[[date, str], DailySourceFrames] = fetch_market_day,
    skip_existing: bool = False,
) -> EquityCollectionResult:
    requested_dates = tuple(sorted(set(dates)))
    if not requested_dates:
        raise ValueError("at least one verified trading date is required")
    roots = {
        "price": normalized_root / KR_EQUITY_PRICE_DAILY.name,
        "market_cap": normalized_root / KR_EQUITY_MARKET_CAP_DAILY.name,
        "master": normalized_root / KR_EQUITY_MASTER.name,
    }
    existing_price = _read_optional(
        roots["price"], KR_EQUITY_PRICE_DAILY, validate_equity_price
    )
    if skip_existing and existing_price is not None:
        stored_dates = set(existing_price["date"].astype(str))
        requested_dates = tuple(value for value in requested_dates if value.isoformat() not in stored_dates)
        if not requested_dates:
            existing_cap = _read_optional(
                roots["market_cap"], KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap
            )
            existing_master = _read_optional(
                roots["master"], KR_EQUITY_MASTER, validate_equity_master
            )
            return EquityCollectionResult(
                0, 0, len(existing_price),
                0 if existing_cap is None else len(existing_cap),
                0 if existing_master is None else len(existing_master),
                roots["price"], roots["market_cap"], roots["master"],
            )
    price_parts: list[pd.DataFrame] = []
    cap_parts: list[pd.DataFrame] = []
    master_parts: list[pd.DataFrame] = []
    completed: list[str] = []
    policy = PykrxRequestPolicy()
    fetcher_fn = (
        (lambda day, market: fetch_market_day(day, market, policy=policy))
        if fetcher is fetch_market_day
        else fetcher
    )
    for requested in requested_dates:
        day_frames = [fetcher_fn(requested, market) for market in MARKETS]
        if any(frame.price.empty for frame in day_frames):
            raise EquityCollectionError(
                f"{requested} was supplied as a trading date but returned a valid empty response"
            )
        price_parts.extend(frame.price for frame in day_frames)
        cap_parts.extend(frame.market_cap for frame in day_frames)
        master_parts.extend(frame.master for frame in day_frames)
        completed.append(requested.isoformat())
    incoming_price = pd.concat(price_parts, ignore_index=True).sort_values(
        list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    incoming_cap = pd.concat(cap_parts, ignore_index=True).sort_values(
        list(KR_EQUITY_MARKET_CAP_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    incoming_master = pd.concat(master_parts, ignore_index=True)
    incoming_master = incoming_master.drop_duplicates(
        list(KR_EQUITY_MASTER.primary_key), keep="last"
    ).sort_values(list(KR_EQUITY_MASTER.sort_key), kind="stable").reset_index(drop=True)
    price = _merge(
        existing_price,
        incoming_price, KR_EQUITY_PRICE_DAILY,
    )
    cap = _merge(
        _read_optional(roots["market_cap"], KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap),
        incoming_cap, KR_EQUITY_MARKET_CAP_DAILY,
    )
    master = _merge(
        _read_optional(roots["master"], KR_EQUITY_MASTER, validate_equity_master),
        incoming_master, KR_EQUITY_MASTER,
    )
    validate_equity_price(price)
    validate_equity_market_cap(cap)
    validate_equity_master(master)
    write_partitioned_atomic(price, roots["price"], KR_EQUITY_PRICE_DAILY, validate_equity_price)
    write_partitioned_atomic(cap, roots["market_cap"], KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap)
    write_partitioned_atomic(master, roots["master"], KR_EQUITY_MASTER, validate_equity_master)
    _atomic_state(
        state_path,
        {
            "completed_dates": completed,
            "last_completed": completed[-1],
            "price_rows": len(price),
            "market_cap_rows": len(cap),
            "master_rows": len(master),
        },
    )
    return EquityCollectionResult(
        len(requested_dates), len(completed), len(price), len(cap), len(master),
        roots["price"], roots["market_cap"], roots["master"],
    )
