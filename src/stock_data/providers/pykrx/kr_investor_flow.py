from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
import time
from typing import Iterable

import pandas as pd

from stock_data.contracts.kr_market import KR_INVESTOR_FLOW_DAILY
from stock_data.providers.pykrx.kr_equity_daily import MARKETS, NORMALIZED_ROOT, _stock_module
from stock_data.storage.equity_parquet import read_partitioned, write_partitioned_atomic
from stock_data.validation.kr_market import validate_investor_flow
from stock_data.providers.pykrx.safety import PykrxRequestPolicy, require_manual_live_access


COLUMN_MAP = {
    "기관합계": "institution_net_buy", "기타법인": "other_corporation_net_buy",
    "개인": "individual_net_buy", "외국인합계": "foreign_net_buy",
    "외국인": "foreign_net_buy", "전체": "total_net_buy",
}


def fetch_investor_flow(
    start: date, end: date, market: str, *, stock_module=None,
    policy: PykrxRequestPolicy | None = None, manual: bool = False,
) -> pd.DataFrame:
    if stock_module is None:
        require_manual_live_access(manual=manual, requested_days=(end - start).days + 1)
    request_policy = policy or PykrxRequestPolicy()
    last_error = None
    for attempt in range(3):
        try:
            stock = stock_module or _stock_module()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                request_policy.before_request()
                source = stock.get_market_trading_value_by_date(
                    start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), market,
                    etf=False, etn=False, elw=False, on="순매수",
                )
            break
        except Exception as error:
            last_error = error
            request_policy.record_failure()
    else:
        raise RuntimeError(
            f"{market} investor flow failed after 3 attempts: {type(last_error).__name__}"
        ) from None
    if source.empty:
        raise RuntimeError(f"{market} investor flow returned empty data")
    normalized = source.reset_index().rename(columns=COLUMN_MAP)
    normalized = normalized.rename(columns={normalized.columns[0]: "date"})
    missing = set(KR_INVESTOR_FLOW_DAILY.column_names) - {"market"} - set(normalized.columns)
    if missing:
        raise RuntimeError(f"investor flow response fields missing: {sorted(missing)}")
    normalized.insert(1, "market", market)
    normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.strftime("%Y-%m-%d")
    for column in KR_INVESTOR_FLOW_DAILY.column_names[2:]:
        normalized[column] = pd.to_numeric(normalized[column], errors="raise").astype("int64")
    normalized = normalized[list(KR_INVESTOR_FLOW_DAILY.column_names)].sort_values(
        list(KR_INVESTOR_FLOW_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_investor_flow(normalized)
    request_policy.record_success()
    return normalized


def collect_investor_flow(
    start: date, end: date, *, normalized_root: Path = NORMALIZED_ROOT, stock_module=None
) -> pd.DataFrame:
    incoming = pd.concat(
        [
            fetch_investor_flow(
                start, end, market, stock_module=stock_module,
                policy=(policy := PykrxRequestPolicy()) if index == 0 else policy,
            )
            for index, market in enumerate(MARKETS)
        ],
        ignore_index=True,
    ).sort_values(list(KR_INVESTOR_FLOW_DAILY.sort_key), kind="stable").reset_index(drop=True)
    root = normalized_root / KR_INVESTOR_FLOW_DAILY.name
    if root.exists():
        existing = read_partitioned(root, KR_INVESTOR_FLOW_DAILY, validate_investor_flow)
        keys = set(map(tuple, incoming[["date", "market"]].to_numpy()))
        existing_keys = existing[["date", "market"]].apply(tuple, axis=1)
        incoming = pd.concat([existing.loc[~existing_keys.isin(keys)], incoming], ignore_index=True)
        incoming = incoming.sort_values(list(KR_INVESTOR_FLOW_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_investor_flow(incoming)
    write_partitioned_atomic(incoming, root, KR_INVESTOR_FLOW_DAILY, validate_investor_flow)
    return incoming
