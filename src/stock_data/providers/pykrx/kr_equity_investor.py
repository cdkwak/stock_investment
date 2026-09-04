from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from io import StringIO
import re
from typing import Protocol

import pandas as pd

from stock_data.contracts.kr_equity_investor_flow import (
    KR_EQUITY_INVESTOR_FLOW_DAILY,
)
from stock_data.providers.pykrx.kr_etf import _load_stock_module, _sanitize
from stock_data.providers.pykrx.safety import PykrxRequestPolicy
from stock_data.validation.kr_equity_investor_flow import (
    validate_kr_equity_investor_flow,
)


PYKRX_COLUMNS = {
    "외국인합계": "foreign_net",
    "기관합계": "institution_net",
    "개인": "individual_net",
    "기타법인": "other_corp_net",
    "전체": "total_net",
}
MAX_LIVE_CALENDAR_DAYS = 366
MAX_SYMBOLS_PER_RUN = 40


class KrEquityInvestorProviderError(RuntimeError):
    pass


class KrEquityInvestorProvider(Protocol):
    @property
    def request_count(self) -> int: ...

    def get_market_trading_value_by_date(
        self, start: date, end: date, symbol: str,
    ) -> pd.DataFrame: ...


class PykrxEquityInvestorClient:
    """Bounded adapter for one net-purchase amount call per equity symbol."""

    def __init__(
        self,
        *,
        stock_module=None,
        policy: PykrxRequestPolicy | None = None,
        manual: bool = False,
        requested_days: int = 1,
    ) -> None:
        if stock_module is None:
            if not manual:
                raise ValueError("live pykrx investor-flow access requires explicit manual mode")
            if requested_days < 1 or requested_days > MAX_LIVE_CALENDAR_DAYS:
                raise ValueError("live pykrx investor-flow range must contain 1..366 calendar days")
        self._stock = stock_module or _load_stock_module()
        self._policy = policy or PykrxRequestPolicy(
            max_consecutive_requests=MAX_SYMBOLS_PER_RUN,
        )

    @property
    def request_count(self) -> int:
        return self._policy.request_count

    def _call(self, name: str, *args, **kwargs):
        try:
            self._policy.before_request()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                result = getattr(self._stock, name)(*args, **kwargs)
            self._policy.record_success()
            return result
        except Exception as error:
            raise KrEquityInvestorProviderError(
                f"pykrx {name} failed: {type(error).__name__}: {_sanitize(error)}"
            ) from None

    def get_market_trading_value_by_date(
        self, start: date, end: date, symbol: str,
    ) -> pd.DataFrame:
        if not re.fullmatch(r"[0-9A-Z]{6}", symbol):
            raise ValueError("Korean equity symbol must be a six-character KRX code")
        value = self._call(
            "get_market_trading_value_by_date",
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            symbol,
            on="순매수",
            detail=False,
        )
        if not isinstance(value, pd.DataFrame):
            raise KrEquityInvestorProviderError(
                f"pykrx investor-flow type differs: {symbol}"
            )
        return value.copy(deep=True)


def normalize_investor_flow(
    raw: pd.DataFrame,
    *,
    symbol: str,
    start: date,
    end: date,
    captured_at: datetime,
) -> pd.DataFrame:
    if not re.fullmatch(r"[0-9A-Z]{6}", symbol):
        raise ValueError("Korean equity symbol must be a six-character KRX code")
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("captured_at must be timezone-aware")
    if raw.empty:
        return pd.DataFrame(columns=KR_EQUITY_INVESTOR_FLOW_DAILY.column_names)
    if raw.index.has_duplicates:
        raise KrEquityInvestorProviderError(
            f"pykrx investor-flow date index contains duplicates: {symbol}"
        )
    if list(raw.columns) != ["기관합계", "기타법인", "개인", "외국인합계", "전체"]:
        raise KrEquityInvestorProviderError(
            f"pykrx investor-flow columns differ: {symbol}: {list(raw.columns)}"
        )
    frame = raw.reset_index()
    frame = frame.rename(columns={frame.columns[0]: "date", **PYKRX_COLUMNS})
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    if frame["date"].min() < start or frame["date"].max() > end:
        raise KrEquityInvestorProviderError(
            f"pykrx investor-flow rows exceed the requested range: {symbol}"
        )
    frame.insert(1, "symbol", symbol)
    for column in PYKRX_COLUMNS.values():
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any() or (numeric % 1 != 0).any():
            raise KrEquityInvestorProviderError(
                f"pykrx investor-flow integer field differs: {symbol}/{column}"
            )
        frame[column] = numeric.astype("int64")
    frame["source"] = "pykrx"
    frame["captured_at"] = pd.Timestamp(captured_at.astimezone(timezone.utc))
    frame = frame[list(KR_EQUITY_INVESTOR_FLOW_DAILY.column_names)].sort_values(
        ["date", "symbol"], kind="stable"
    ).reset_index(drop=True)
    validate_kr_equity_investor_flow(frame)
    return frame


__all__ = [
    "KrEquityInvestorProvider",
    "KrEquityInvestorProviderError",
    "MAX_LIVE_CALENDAR_DAYS",
    "MAX_SYMBOLS_PER_RUN",
    "PYKRX_COLUMNS",
    "PykrxEquityInvestorClient",
    "normalize_investor_flow",
]
