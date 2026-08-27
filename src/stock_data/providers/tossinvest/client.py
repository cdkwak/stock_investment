from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import time
from typing import Any

from dotenv import load_dotenv
import requests


DEFAULT_BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
TOKEN_FORM_KEYS = frozenset({"grant_type", "client_id", "client_secret"})
TOKEN_REFRESH_MARGIN_SECONDS = 60
DEFAULT_CONNECT_TIMEOUT = 3.05
DEFAULT_READ_TIMEOUT = 10.0
PROJECT_ROOT = Path(__file__).resolve().parents[4]
RATE_LIMIT_HEADER_NAMES = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "Retry-After",
)
MARKET_INDICATOR_SYMBOL_PATTERN = (
    r"(?:KOSPI|KOSDAQ|KR_BOND_(?:2Y|3Y|5Y|10Y|20Y|30Y))"
)
READ_ONLY_MARKET_PATHS = (
    re.compile(r"^/api/v1/prices$"),
    re.compile(r"^/api/v1/market-indicators/prices$"),
    re.compile(
        rf"^/api/v1/market-indicators/{MARKET_INDICATOR_SYMBOL_PATTERN}/candles$"
    ),
    re.compile(r"^/api/v1/market-indicators/(?:KOSPI|KOSDAQ)/investor-trading$"),
    re.compile(
        r"^/api/v1/stocks/[0-9]{6}/"
        r"(?:program-trades|short-selling|credit-trades|securities-lending)$"
    ),
)


@dataclass(frozen=True)
class TossInvestRateLimit:
    group: str
    limit: int | None = None
    remaining: int | None = None
    reset_seconds: int | None = None
    retry_after_seconds: int | None = None
    raw_headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class TossInvestHTTPDiagnostics:
    http_status: int | None = None
    content_type: str | None = None
    response_is_json: bool | None = None
    error_code: str | None = None
    error_message: str | None = None
    request_id: str | None = None
    www_authenticate: str | None = None
    text_excerpt: str | None = None
    rate_limit: TossInvestRateLimit | None = None


@dataclass(frozen=True)
class TossInvestTokenMetadata:
    http_status: int
    token_type: str
    expires_in: int
    expires_at: datetime
    rate_limit: TossInvestRateLimit


@dataclass(frozen=True)
class TossInvestAPIResponse:
    http_status: int
    payload: dict[str, Any]
    rate_limit: TossInvestRateLimit
    request_id: str | None = None


class TossInvestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        details: TossInvestHTTPDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details


class TossInvestConfigurationError(TossInvestError):
    pass


class TossInvestTimeoutError(TossInvestError):
    pass


class TossInvestHTTPError(TossInvestError):
    pass


class TossInvestAuthenticationError(TossInvestHTTPError):
    pass


class TossInvestRateLimitError(TossInvestHTTPError):
    pass


class TossInvestResponseError(TossInvestError):
    pass


class TossInvestClient:
    """Minimal read-only OAuth client for the Toss Securities Open API."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client_id: str,
        client_secret: str,
        session: requests.Session | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        clock: Callable[[], float] = time.time,
    ) -> None:
        normalized_base_url = base_url.strip().rstrip("/")
        missing = [
            name
            for name, value in (
                ("TOSSINVEST_BASE_URL", normalized_base_url),
                ("TOSSINVEST_CLIENT_ID", client_id),
                ("TOSSINVEST_CLIENT_SECRET", client_secret),
            )
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise TossInvestConfigurationError(
                "missing environment variables: " + ", ".join(missing)
            )
        if connect_timeout <= 0 or read_timeout <= 0:
            raise TossInvestConfigurationError("HTTP timeouts must be positive")

        self.base_url = normalized_base_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session or requests.Session()
        self._timeout = (float(connect_timeout), float(read_timeout))
        self._clock = clock
        self._access_token: str | None = None
        self._expires_at_epoch = 0.0
        self._token_metadata: TossInvestTokenMetadata | None = None
        self._token_request_count = 0
        self._market_request_count = 0
        self._account_request_count = 0

    @classmethod
    def from_environment(
        cls,
        *,
        project_root: Path | None = None,
        session: requests.Session | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        clock: Callable[[], float] = time.time,
    ) -> TossInvestClient:
        root = project_root or PROJECT_ROOT
        load_dotenv(root / ".env", override=False)
        base_url = os.getenv("TOSSINVEST_BASE_URL", "").strip() or DEFAULT_BASE_URL
        return cls(
            base_url=base_url,
            client_id=os.getenv("TOSSINVEST_CLIENT_ID", ""),
            client_secret=os.getenv("TOSSINVEST_CLIENT_SECRET", ""),
            session=session,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            clock=clock,
        )

    @property
    def token_metadata(self) -> TossInvestTokenMetadata | None:
        return self._token_metadata

    @property
    def token_request_count(self) -> int:
        return self._token_request_count

    @property
    def market_request_count(self) -> int:
        return self._market_request_count

    @property
    def account_request_count(self) -> int:
        return self._account_request_count

    def access_token(self) -> str:
        now = float(self._clock())
        if (
            self._access_token
            and now < self._expires_at_epoch - TOKEN_REFRESH_MARGIN_SECONDS
        ):
            return self._access_token
        return self._issue_access_token(now)

    def authorization_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def get_market_data(
        self,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> TossInvestAPIResponse:
        """Call one explicitly allowlisted, read-only market endpoint without retry."""
        if not any(pattern.fullmatch(path) for pattern in READ_ONLY_MARKET_PATHS):
            raise TossInvestConfigurationError(
                "unsupported Toss read-only market endpoint"
            )
        rate_limit_group = self._market_rate_limit_group(path)
        headers = {
            **self.authorization_headers(),
            "Accept": "application/json",
        }
        self._market_request_count += 1
        try:
            response = self._session.get(
                f"{self.base_url}{path}",
                headers=headers,
                params=dict(params or {}),
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            raise TossInvestTimeoutError("Toss market request timed out") from None
        except requests.RequestException:
            raise TossInvestHTTPError("Toss market request failed") from None

        details, payload = self._response_diagnostics(
            response, rate_limit_group=rate_limit_group
        )
        status_code = int(response.status_code)
        if status_code == 429:
            raise TossInvestRateLimitError(
                "Toss market rate limit exceeded", details=details
            )
        if status_code in {401, 403}:
            raise TossInvestAuthenticationError(
                f"Toss market authentication failed (HTTP {status_code})",
                details=details,
            )
        if not 200 <= status_code < 300:
            raise TossInvestHTTPError(
                f"Toss market request failed (HTTP {status_code})",
                details=details,
            )
        if not details.response_is_json or not isinstance(payload, dict):
            raise TossInvestResponseError(
                "Toss market response is not a JSON object", details=details
            )
        return TossInvestAPIResponse(
            http_status=status_code,
            payload=payload,
            rate_limit=details.rate_limit
            or TossInvestRateLimit(group=rate_limit_group),
            request_id=details.request_id,
        )

    def brokerage_account_seq(self) -> int:
        """Return the sole brokerage account selector without exposing its number.

        The selector is intentionally returned only to memory.  Callers must not
        persist or log it.  Multiple brokerage accounts require an explicit
        runtime selector rather than an arbitrary choice.
        """
        response = self._get_account_data(
            "/api/v1/accounts", rate_limit_group="ACCOUNT"
        )
        rows = response.payload.get("result")
        if not isinstance(rows, list):
            raise TossInvestResponseError("Toss accounts result must be an array")
        selectors: list[int] = []
        for row in rows:
            if not isinstance(row, dict):
                raise TossInvestResponseError("Toss account entry must be an object")
            if not {"accountNo", "accountSeq", "accountType"}.issubset(row):
                raise TossInvestResponseError("Toss account entry is incomplete")
            if not isinstance(row["accountNo"], str) or not row["accountNo"]:
                raise TossInvestResponseError("Toss account number is invalid")
            account_type = row["accountType"]
            selector = row["accountSeq"]
            if account_type == "BROKERAGE":
                if isinstance(selector, bool) or not isinstance(selector, int) or selector <= 0:
                    raise TossInvestResponseError("Toss account selector is invalid")
                selectors.append(selector)
        if len(selectors) != 1:
            raise TossInvestResponseError(
                "Toss brokerage account selection is not unambiguous"
            )
        return selectors[0]

    def get_holdings(self, *, account_seq: int) -> TossInvestAPIResponse:
        """Fetch the official read-only holdings view without retry."""
        if isinstance(account_seq, bool) or not isinstance(account_seq, int) or account_seq <= 0:
            raise TossInvestConfigurationError("invalid Toss account selector")
        return self._get_account_data(
            "/api/v1/holdings",
            rate_limit_group="ASSET",
            account_seq=account_seq,
        )

    def get_buying_power(
        self, *, account_seq: int, currency: str,
    ) -> TossInvestAPIResponse:
        """Fetch official cash-only buying power for one exact currency."""
        if isinstance(account_seq, bool) or not isinstance(account_seq, int) or account_seq <= 0:
            raise TossInvestConfigurationError("invalid Toss account selector")
        if currency not in {"KRW", "USD"}:
            raise TossInvestConfigurationError("unsupported Toss buying-power currency")
        return self._get_account_data(
            "/api/v1/buying-power",
            rate_limit_group="ORDER_INFO",
            account_seq=account_seq,
            params={"currency": currency},
        )

    def _get_account_data(
        self,
        path: str,
        *,
        rate_limit_group: str,
        account_seq: int | None = None,
        params: Mapping[str, str] | None = None,
    ) -> TossInvestAPIResponse:
        allowed = {
            "/api/v1/accounts": ("ACCOUNT", False),
            "/api/v1/holdings": ("ASSET", True),
            "/api/v1/buying-power": ("ORDER_INFO", True),
        }
        expected = allowed.get(path)
        if expected != (rate_limit_group, account_seq is not None):
            raise TossInvestConfigurationError(
                "unsupported Toss read-only account endpoint"
            )
        if path == "/api/v1/buying-power":
            if params is None or set(params) != {"currency"} or params["currency"] not in {"KRW", "USD"}:
                raise TossInvestConfigurationError("invalid Toss buying-power request")
        elif params is not None:
            raise TossInvestConfigurationError("unexpected Toss account query parameters")
        headers = {**self.authorization_headers(), "Accept": "application/json"}
        if account_seq is not None:
            headers["X-Tossinvest-Account"] = str(account_seq)
        self._account_request_count += 1
        try:
            response = self._session.get(
                f"{self.base_url}{path}", headers=headers, params=params,
                timeout=self._timeout,
            )
        except requests.Timeout:
            raise TossInvestTimeoutError("Toss account request timed out") from None
        except requests.RequestException:
            raise TossInvestHTTPError("Toss account request failed") from None

        details, payload = self._response_diagnostics(
            response, rate_limit_group=rate_limit_group
        )
        # Account error bodies may contain private provider context.  Preserve
        # only non-sensitive status/code/rate-limit diagnostics.
        details = TossInvestHTTPDiagnostics(
            http_status=details.http_status,
            content_type=details.content_type,
            response_is_json=details.response_is_json,
            error_code=details.error_code,
            request_id=details.request_id,
            rate_limit=details.rate_limit,
        )
        status_code = int(response.status_code)
        if status_code == 429:
            raise TossInvestRateLimitError(
                "Toss account rate limit exceeded", details=details
            )
        if status_code in {401, 403}:
            raise TossInvestAuthenticationError(
                f"Toss account authentication failed (HTTP {status_code})",
                details=details,
            )
        if not 200 <= status_code < 300:
            raise TossInvestHTTPError(
                f"Toss account request failed (HTTP {status_code})", details=details
            )
        if not details.response_is_json or not isinstance(payload, dict):
            raise TossInvestResponseError(
                "Toss account response is not a JSON object", details=details
            )
        return TossInvestAPIResponse(
            http_status=status_code,
            payload=payload,
            rate_limit=details.rate_limit or TossInvestRateLimit(group=rate_limit_group),
            request_id=details.request_id,
        )

    def _issue_access_token(self, issued_at: float) -> str:
        form = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if set(form) != TOKEN_FORM_KEYS:
            raise TossInvestConfigurationError("invalid OAuth token form keys")

        self._token_request_count += 1
        try:
            response = self._session.post(
                f"{self.base_url}{TOKEN_PATH}",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=form,
                timeout=self._timeout,
            )
        except requests.Timeout:
            raise TossInvestTimeoutError("Toss OAuth token request timed out") from None
        except requests.RequestException:
            raise TossInvestHTTPError("Toss OAuth token request failed") from None

        details, payload = self._response_diagnostics(response, rate_limit_group="AUTH")
        status_code = int(response.status_code)
        if status_code == 429:
            raise TossInvestRateLimitError(
                "Toss OAuth token rate limit exceeded", details=details
            )
        if status_code in {401, 403}:
            raise TossInvestAuthenticationError(
                f"Toss OAuth client authentication failed (HTTP {status_code})",
                details=details,
            )
        if not 200 <= status_code < 300:
            raise TossInvestHTTPError(
                f"Toss OAuth token request failed (HTTP {status_code})",
                details=details,
            )
        if not details.response_is_json or not isinstance(payload, dict):
            raise TossInvestResponseError(
                "Toss OAuth token response is not a JSON object", details=details
            )

        access_token = payload.get("access_token")
        token_type = payload.get("token_type")
        if not isinstance(access_token, str) or not access_token:
            raise TossInvestResponseError(
                "Toss OAuth token response has no access_token", details=details
            )
        if token_type != "Bearer":
            raise TossInvestResponseError(
                "Toss OAuth token response has an invalid token_type", details=details
            )
        try:
            raw_expiry = payload.get("expires_in")
            if isinstance(raw_expiry, bool):
                raise ValueError
            expires_in = int(raw_expiry)
        except (TypeError, ValueError, OverflowError):
            raise TossInvestResponseError(
                "Toss OAuth token response has an invalid expires_in", details=details
            ) from None
        if expires_in <= 0:
            raise TossInvestResponseError(
                "Toss OAuth token response has a non-positive expires_in",
                details=details,
            )

        self._access_token = access_token
        self._expires_at_epoch = issued_at + expires_in
        self._token_metadata = TossInvestTokenMetadata(
            http_status=status_code,
            token_type=token_type,
            expires_in=expires_in,
            expires_at=datetime.fromtimestamp(
                self._expires_at_epoch, tz=timezone.utc
            ),
            rate_limit=details.rate_limit
            or TossInvestRateLimit(group="AUTH"),
        )
        return access_token

    @staticmethod
    def _market_rate_limit_group(path: str) -> str:
        if path == "/api/v1/prices":
            return "STOCK_PRICE"
        if path.endswith("/candles"):
            return "MARKET_INDICATOR_CHART"
        if path.startswith("/api/v1/market-indicators/"):
            return "MARKET_INDICATOR"
        return "STOCK_TRADING_TREND"

    def _response_diagnostics(
        self,
        response: requests.Response,
        *,
        rate_limit_group: str,
    ) -> tuple[TossInvestHTTPDiagnostics, Any]:
        headers = response.headers or {}
        content_type_value = self._header_value(headers, "Content-Type")
        content_type = (
            self._sanitize(content_type_value.split(";", 1)[0].strip())
            if content_type_value
            else None
        )
        try:
            payload: Any = response.json()
            response_is_json = True
        except (ValueError, TypeError):
            payload = None
            response_is_json = False

        error_code: str | None = None
        error_message: str | None = None
        request_id = self._header_value(headers, "X-Request-Id")
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, str):
                error_code = self._sanitize(error)
                error_message = self._safe_optional(payload.get("error_description"))
            elif isinstance(error, dict):
                error_code = self._safe_optional(error.get("code"))
                error_message = self._safe_optional(error.get("message"))
                request_id = self._safe_optional(error.get("requestId")) or request_id
            else:
                error_code = self._safe_optional(payload.get("code"))
                error_message = self._safe_optional(payload.get("message"))
                request_id = self._safe_optional(payload.get("requestId")) or request_id

        text_excerpt = None
        if not response_is_json:
            try:
                text_excerpt = self._sanitize(response.text)
            except (AttributeError, TypeError):
                text_excerpt = None
        details = TossInvestHTTPDiagnostics(
            http_status=int(response.status_code),
            content_type=content_type,
            response_is_json=response_is_json,
            error_code=error_code,
            error_message=error_message,
            request_id=self._sanitize(request_id) if request_id else None,
            www_authenticate=self._safe_optional(
                self._header_value(headers, "WWW-Authenticate")
            ),
            text_excerpt=text_excerpt,
            rate_limit=self._rate_limit(headers, group=rate_limit_group),
        )
        return details, payload

    def _rate_limit(
        self,
        headers: Mapping[str, Any],
        *,
        group: str,
    ) -> TossInvestRateLimit:
        raw = {
            name: self._sanitize(value)
            for name in RATE_LIMIT_HEADER_NAMES
            if (value := self._header_value(headers, name)) is not None
        }
        return TossInvestRateLimit(
            group=group,
            limit=self._nonnegative_int(raw.get("X-RateLimit-Limit")),
            remaining=self._nonnegative_int(raw.get("X-RateLimit-Remaining")),
            reset_seconds=self._nonnegative_int(raw.get("X-RateLimit-Reset")),
            retry_after_seconds=self._nonnegative_int(raw.get("Retry-After")),
            raw_headers=tuple((name, raw[name]) for name in RATE_LIMIT_HEADER_NAMES if name in raw),
        )

    @staticmethod
    def _header_value(headers: Mapping[str, Any], name: str) -> str | None:
        for key, value in headers.items():
            if str(key).casefold() == name.casefold() and value is not None:
                return str(value)
        return None

    @staticmethod
    def _nonnegative_int(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if parsed >= 0 else None

    def _safe_optional(self, value: Any) -> str | None:
        if value is None or isinstance(value, (dict, list, tuple, set)):
            return None
        return self._sanitize(str(value)) or None

    def _sanitize(self, value: str) -> str:
        safe_value = str(value)
        for sensitive in (
            self._client_id,
            self._client_secret,
            self._access_token,
        ):
            if sensitive:
                safe_value = safe_value.replace(sensitive, "[REDACTED]")
        safe_value = re.sub(
            r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+",
            "Bearer [REDACTED]",
            safe_value,
        )
        safe_value = re.sub(
            r"(?i)((?:client_id|client_secret|access_token)\s*[=:]\s*)[^&\s,}\"]+",
            r"\1[REDACTED]",
            safe_value,
        )
        return " ".join(safe_value.split())[:300]
