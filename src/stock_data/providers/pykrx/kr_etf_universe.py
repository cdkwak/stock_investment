"""One-call adapter for KRX's current full Korean ETF identity universe."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import re
from typing import Protocol

import pandas as pd
from dotenv import load_dotenv

from stock_data.providers.pykrx.safety import PykrxRequestPolicy, require_manual_live_access


PROJECT_ROOT = Path(__file__).resolve().parents[4]


class KrEtfUniverseProviderError(RuntimeError):
    pass


class KrEtfUniverseProvider(Protocol):
    @property
    def request_count(self) -> int: ...

    def fetch(self) -> pd.DataFrame: ...


def _sanitize(value: object) -> str:
    text = str(value)
    text = re.sub(
        r"(?i)\b(KRX_ID|KRX_PW|API_KEY|PASSWORD|TOKEN|SECRET)(\s*[:=]\s*)([^\s,;]+)",
        r"\1\2[REDACTED]",
        text,
    )
    return re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)


def _load_operation():
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            load_dotenv(PROJECT_ROOT / ".env", override=False)
            from pykrx.website.krx.etx.core import ETF_전종목기본종목
            return ETF_전종목기본종목()
    except Exception as error:
        raise KrEtfUniverseProviderError(
            f"pykrx ETF universe initialization failed: {type(error).__name__}: {_sanitize(error)}"
        ) from None


class PykrxKrEtfUniverseClient:
    """Retry-zero wrapper around exactly one ``ETF_전종목기본종목.fetch`` call."""

    def __init__(
        self,
        *,
        operation=None,
        policy: PykrxRequestPolicy | None = None,
        manual: bool = False,
        requested_days: int = 1,
    ) -> None:
        if operation is None:
            require_manual_live_access(manual=manual, requested_days=requested_days)
        self._operation = operation if operation is not None else _load_operation()
        self._policy = policy or PykrxRequestPolicy(max_consecutive_requests=1)

    @property
    def request_count(self) -> int:
        return self._policy.request_count

    def fetch(self) -> pd.DataFrame:
        try:
            self._policy.before_request()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                value = self._operation.fetch()
            if not isinstance(value, pd.DataFrame):
                raise KrEtfUniverseProviderError("pykrx ETF universe response type differs")
            self._policy.record_success()
            return value.copy(deep=True)
        except KrEtfUniverseProviderError:
            raise
        except Exception as error:
            raise KrEtfUniverseProviderError(
                f"pykrx ETF universe fetch failed: {type(error).__name__}: {_sanitize(error)}"
            ) from None


__all__ = [
    "KrEtfUniverseProvider", "KrEtfUniverseProviderError",
    "PykrxKrEtfUniverseClient",
]
