from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
import re
from typing import Protocol

import pandas as pd
from dotenv import load_dotenv

from stock_data.providers.pykrx.safety import PykrxRequestPolicy, require_manual_live_access


PROJECT_ROOT = Path(__file__).resolve().parents[4]


class KrEtfProviderError(RuntimeError):
    pass


class KrEtfProvider(Protocol):
    @property
    def request_count(self) -> int: ...

    def get_etf_ticker_list(self, source_date: date) -> tuple[str, ...]: ...

    def get_etf_ticker_name(self, symbol: str) -> str: ...

    def get_etf_ohlcv_by_date(
        self, start: date, end: date, symbol: str,
    ) -> pd.DataFrame: ...


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
        raise KrEtfProviderError(
            f"pykrx initialization failed: {type(error).__name__}: {_sanitize(error)}"
        ) from None


class PykrxEtfClient:
    """Bounded adapter around the three pykrx calls used by the ETF operation."""

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
        self._policy = policy or PykrxRequestPolicy(max_consecutive_requests=21)

    @property
    def request_count(self) -> int:
        return self._policy.request_count

    def _call(self, name: str, *args):
        try:
            self._policy.before_request()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                result = getattr(self._stock, name)(*args)
            self._policy.record_success()
            return result
        except Exception as error:
            raise KrEtfProviderError(
                f"pykrx {name} failed: {type(error).__name__}: {_sanitize(error)}"
            ) from None

    def get_etf_ticker_list(self, source_date: date) -> tuple[str, ...]:
        values = self._call("get_etf_ticker_list", source_date.strftime("%Y%m%d"))
        if not isinstance(values, (list, tuple)):
            raise KrEtfProviderError("pykrx ETF ticker list type differs")
        result = tuple(str(value).strip() for value in values)
        if not result or any(not re.fullmatch(r"\d{6}", value) for value in result):
            raise KrEtfProviderError("pykrx ETF ticker list is empty or malformed")
        if len(result) != len(set(result)):
            raise KrEtfProviderError("pykrx ETF ticker list contains duplicates")
        return result

    def get_etf_ticker_name(self, symbol: str) -> str:
        value = str(self._call("get_etf_ticker_name", symbol)).strip()
        if not value:
            raise KrEtfProviderError(f"pykrx ETF name is empty: {symbol}")
        return value

    def get_etf_ohlcv_by_date(
        self, start: date, end: date, symbol: str,
    ) -> pd.DataFrame:
        value = self._call(
            "get_etf_ohlcv_by_date",
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), symbol,
        )
        if not isinstance(value, pd.DataFrame):
            raise KrEtfProviderError(f"pykrx ETF OHLCV type differs: {symbol}")
        return value.copy(deep=True)


__all__ = ["KrEtfProvider", "KrEtfProviderError", "PykrxEtfClient"]
