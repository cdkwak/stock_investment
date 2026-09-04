from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Protocol
from zoneinfo import ZoneInfo

from stock_data.providers.tossinvest.client import (
    TossInvestAPIResponse,
    TossInvestRateLimitError,
    TossInvestResponseError,
)


KST = ZoneInfo("Asia/Seoul")
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}")


class TossInvestUSQuoteRateLimited(TossInvestResponseError):
    def __init__(self, retry_after_seconds: int | None) -> None:
        super().__init__("Toss U.S. quote run is rate limited")
        self.retry_after_seconds = retry_after_seconds


class USQuoteClient(Protocol):
    def get_market_data(
        self, path: str, *, params: dict[str, object] | None = None,
    ) -> TossInvestAPIResponse: ...


def _rate_limited(error: TossInvestRateLimitError) -> TossInvestUSQuoteRateLimited:
    details = error.details
    rate_limit = details.rate_limit if details is not None else None
    return TossInvestUSQuoteRateLimited(
        rate_limit.retry_after_seconds if rate_limit is not None else None
    )


def fetch_us_quotes(client: USQuoteClient, symbols: tuple[str, ...]) -> list[dict[str, object]]:
    """Fetch an exact U.S. watchlist in one STOCK_PRICE request and never retry."""
    requested = tuple(str(symbol).strip().upper() for symbol in symbols)
    if not requested or len(requested) > 200:
        raise ValueError("Toss U.S. quote request requires 1..200 symbols")
    if len(requested) != len(set(requested)) or any(
        not _SYMBOL.fullmatch(symbol) for symbol in requested
    ):
        raise ValueError("Toss U.S. quote symbols are invalid or duplicated")
    try:
        response = client.get_market_data(
            "/api/v1/prices", params={"symbols": ",".join(requested)},
        )
    except TossInvestRateLimitError as error:
        raise _rate_limited(error) from error
    if response.http_status == 429 or response.rate_limit.retry_after_seconds is not None:
        raise TossInvestUSQuoteRateLimited(response.rate_limit.retry_after_seconds)
    if response.http_status != 200 or response.rate_limit.group != "STOCK_PRICE":
        raise TossInvestResponseError("Toss U.S. quote response identity differs")
    result = response.payload.get("result")
    if not isinstance(result, list):
        raise TossInvestResponseError("Toss U.S. quote result must be an array")
    if not result:
        return []

    retrieved_at = datetime.now(timezone.utc)
    rows: dict[str, dict[str, object]] = {}
    for item in result:
        if not isinstance(item, dict):
            raise TossInvestResponseError("Toss U.S. quote row must be an object")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or symbol not in requested or symbol in rows:
            raise TossInvestResponseError("Toss U.S. quote symbol identity differs")
        if item.get("currency") != "USD":
            raise TossInvestResponseError("Toss U.S. quote currency must be USD")
        try:
            timestamp = datetime.fromisoformat(str(item.get("timestamp")).replace("Z", "+00:00"))
        except ValueError:
            raise TossInvestResponseError("Toss U.S. quote timestamp is invalid") from None
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise TossInvestResponseError("Toss U.S. quote timestamp must be timezone-aware")
        try:
            price = float(str(item.get("lastPrice")).replace(",", ""))
        except (TypeError, ValueError, OverflowError):
            raise TossInvestResponseError("Toss U.S. quote lastPrice is invalid") from None
        if not math.isfinite(price) or price <= 0:
            raise TossInvestResponseError("Toss U.S. quote lastPrice must be positive and finite")
        rows[symbol] = {
            "symbol": symbol,
            "timestamp_kst": timestamp.astimezone(KST).isoformat(timespec="milliseconds"),
            "last_price": price,
            "currency": "USD",
            "retrieved_at_utc": retrieved_at.isoformat(),
        }
    missing = set(requested).difference(rows)
    if missing:
        raise TossInvestResponseError(
            f"Toss U.S. quote result omitted requested symbols: {sorted(missing)}"
        )
    return [rows[symbol] for symbol in requested]


__all__ = ["TossInvestUSQuoteRateLimited", "fetch_us_quotes"]
