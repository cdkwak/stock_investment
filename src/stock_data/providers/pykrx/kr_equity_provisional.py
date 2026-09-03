from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
import re
from typing import Protocol

import pandas as pd
from dotenv import load_dotenv

from stock_data.contracts.kr_equity_provisional import (
    KR_EQUITY_PRICE_PROVISIONAL_DAILY,
    validate_kr_equity_price_provisional_daily,
)
from stock_data.providers.pykrx.safety import PykrxRequestPolicy, require_manual_live_access


PROJECT_ROOT = Path(__file__).resolve().parents[4]
MARKETS = ("KOSPI", "KOSDAQ")
PYKRX_COLUMNS = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "trading_value",
}


class ProvisionalEquityProviderError(RuntimeError):
    pass


class ProvisionalEquityProvider(Protocol):
    @property
    def request_count(self) -> int: ...

    def get_market_ohlcv_by_ticker(self, source_date: date, market: str) -> pd.DataFrame: ...


def _sanitize(value: object) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)\b(KRX_ID|KRX_PW|API_KEY|PASSWORD|TOKEN|SECRET)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]",
        text,
    )
    return re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)


def _load_stock_module():
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            load_dotenv(PROJECT_ROOT / ".env", override=False)
            from pykrx import stock
        return stock
    except Exception as error:
        raise ProvisionalEquityProviderError(
            f"pykrx initialization failed: {type(error).__name__}: {_sanitize(error)}"
        ) from None


class PykrxProvisionalEquityClient:
    """Two-call adapter for pykrx 1.2.8 market-wide equity OHLCV."""

    def __init__(
        self,
        *,
        stock_module=None,
        policy: PykrxRequestPolicy | None = None,
        manual: bool = False,
        requested_days: int = 1,
    ) -> None:
        if stock_module is None:
            require_manual_live_access(manual=manual, requested_days=requested_days)
        self._stock = stock_module or _load_stock_module()
        self._policy = policy or PykrxRequestPolicy(max_consecutive_requests=2)

    @property
    def request_count(self) -> int:
        return self._policy.request_count

    def get_market_ohlcv_by_ticker(self, source_date: date, market: str) -> pd.DataFrame:
        if market not in MARKETS:
            raise ValueError(f"unsupported Korean equity market: {market}")
        try:
            self._policy.before_request()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                value = self._stock.get_market_ohlcv_by_ticker(
                    source_date.strftime("%Y%m%d"), market,
                )
            self._policy.record_success()
        except Exception as error:
            raise ProvisionalEquityProviderError(
                "pykrx get_market_ohlcv_by_ticker failed: "
                f"{type(error).__name__}: {_sanitize(error)}"
            ) from None
        if not isinstance(value, pd.DataFrame):
            raise ProvisionalEquityProviderError("pykrx market OHLCV type differs")
        return value.copy(deep=True)


def normalize_market_ohlcv(
    raw: pd.DataFrame,
    *,
    market: str,
    source_date: date,
    observed_at: datetime,
) -> pd.DataFrame:
    if market not in MARKETS:
        raise ValueError(f"unsupported Korean equity market: {market}")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    if raw.empty:
        return pd.DataFrame(columns=KR_EQUITY_PRICE_PROVISIONAL_DAILY.column_names)
    if raw.index.has_duplicates:
        raise ProvisionalEquityProviderError("pykrx market OHLCV ticker index has duplicates")
    missing = set(PYKRX_COLUMNS) - set(raw.columns)
    if missing:
        raise ProvisionalEquityProviderError(
            f"pykrx market OHLCV columns are missing: {sorted(missing)}"
        )
    frame = raw.rename(columns=PYKRX_COLUMNS).copy()
    frame.index = frame.index.map(str)
    frame.index.name = "symbol"
    frame = frame.reset_index()[["symbol", *PYKRX_COLUMNS.values()]]
    if not frame["symbol"].str.fullmatch(r"[0-9A-Z]{6}").all():
        raise ProvisionalEquityProviderError("pykrx market OHLCV ticker is malformed")
    for column in PYKRX_COLUMNS.values():
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any() or (numeric % 1 != 0).any():
            raise ProvisionalEquityProviderError(
                f"pykrx market OHLCV integer field differs: {column}"
            )
        frame[column] = numeric.astype("int64")
    frame = frame.assign(
        date=source_date.isoformat(),
        market=market,
        source="pykrx",
        source_operation="stock.get_market_ohlcv_by_ticker",
        source_date=source_date.isoformat(),
        provisional=True,
        observed_at=pd.Timestamp(observed_at.astimezone(timezone.utc)),
    )
    frame = frame[list(KR_EQUITY_PRICE_PROVISIONAL_DAILY.column_names)].sort_values(
        list(KR_EQUITY_PRICE_PROVISIONAL_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_kr_equity_price_provisional_daily(frame)
    return frame


__all__ = [
    "MARKETS",
    "ProvisionalEquityProvider",
    "ProvisionalEquityProviderError",
    "PykrxProvisionalEquityClient",
    "normalize_market_ohlcv",
]
