"""Strict offline parsing for a bounded ORATS cores-style options payload.

This module deliberately contains no HTTP transport, credential handling,
environment access, logging, or persistence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timezone

from stock_data.contracts.us_option_pcr import ORATS_OPTION_CORE_OBSERVATION


ORATS_OPTION_TICKERS = ("SPX", "QQQ", "NDX")
_REQUIRED_FIELDS = frozenset({
    "ticker", "tradeDate", "updatedAt", "cVolu", "pVolu", "cOi", "pOi",
})
_ERROR_FIELDS = frozenset({"error", "errors"})
_ASSET_TYPES = {"SPX": "INDEX", "QQQ": "ETF", "NDX": "INDEX"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ORATSOptionsPayloadError(ValueError):
    """The offline payload cannot be accepted without guessing its meaning."""


def _canonical_date(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ORATSOptionsPayloadError(f"{field} must be a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ORATSOptionsPayloadError(f"{field} must be a canonical ISO date") from None
    if parsed.isoformat() != value:
        raise ORATSOptionsPayloadError(f"{field} must be a canonical ISO date")
    return value


def _canonical_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ORATSOptionsPayloadError(f"{field} must be a timezone-aware ISO timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise ORATSOptionsPayloadError(
            f"{field} must be a timezone-aware ISO timestamp"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ORATSOptionsPayloadError(f"{field} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonnegative_count(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool) or not isinstance(value, int)
        or value < 0 or value > 2**63 - 1
    ):
        raise ORATSOptionsPayloadError(f"{field} must be a non-negative JSON integer")
    return value


def _landing_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ORATSOptionsPayloadError("landing_sha256 must be 64 lowercase hex characters")
    return value


def parse_cores_payload(
    payload: object,
    *,
    captured_at_utc: object,
    landing_sha256: object,
    provider_snapshot_at_utc: object | None = None,
) -> list[dict[str, object]]:
    """Normalize exactly one same-date row for each allowlisted ticker.

    Extra provider fields are ignored because cores responses may contain many
    unrelated measures. Required identities and count fields are never inferred
    or coerced.
    """
    captured_at = _canonical_timestamp(captured_at_utc, field="captured_at_utc")
    snapshot_at = (
        None if provider_snapshot_at_utc is None else
        _canonical_timestamp(provider_snapshot_at_utc, field="provider_snapshot_at_utc")
    )
    landing_hash = _landing_sha256(landing_sha256)
    if not isinstance(payload, Mapping):
        raise ORATSOptionsPayloadError("payload must be a JSON object")
    if any(field in payload for field in _ERROR_FIELDS):
        raise ORATSOptionsPayloadError("provider error payload is not accepted")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise ORATSOptionsPayloadError("payload data must be a non-empty array")

    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    trade_dates: set[str] = set()
    for position, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ORATSOptionsPayloadError(f"data[{position}] must be an object")
        missing = sorted(_REQUIRED_FIELDS.difference(raw))
        if missing:
            raise ORATSOptionsPayloadError(
                f"data[{position}] is missing required fields: {', '.join(missing)}"
            )
        ticker = raw["ticker"]
        if not isinstance(ticker, str) or ticker not in ORATS_OPTION_TICKERS:
            raise ORATSOptionsPayloadError(f"data[{position}] has an unapproved ticker")
        if ticker in seen:
            raise ORATSOptionsPayloadError(f"duplicate ticker row: {ticker}")
        seen.add(ticker)

        trade_date = _canonical_date(raw["tradeDate"], field="tradeDate")
        trade_dates.add(trade_date)
        row = {
            "trade_date": trade_date,
            "provider_ticker": ticker,
            "asset_type": _ASSET_TYPES[ticker],
            "call_volume": _nonnegative_count(raw["cVolu"], field="cVolu"),
            "put_volume": _nonnegative_count(raw["pVolu"], field="pVolu"),
            "call_open_interest": _nonnegative_count(raw["cOi"], field="cOi"),
            "put_open_interest": _nonnegative_count(raw["pOi"], field="pOi"),
            "provider_updated_at_utc": _canonical_timestamp(
                raw["updatedAt"], field="updatedAt",
            ),
            "provider_snapshot_at_utc": snapshot_at,
            "captured_at_utc": captured_at,
            "landing_sha256": landing_hash,
            "observation_status": "OBSERVED",
            "source": "ORATS_DELAYED_CORES",
        }
        if tuple(row) != ORATS_OPTION_CORE_OBSERVATION.column_names:
            raise RuntimeError("ORATS parser output differs from its registered contract")
        normalized.append(row)

    expected = set(ORATS_OPTION_TICKERS)
    if seen != expected:
        missing_tickers = ", ".join(sorted(expected.difference(seen)))
        raise ORATSOptionsPayloadError(f"payload is missing allowlisted tickers: {missing_tickers}")
    if len(trade_dates) != 1:
        raise ORATSOptionsPayloadError("all ticker rows must have the same tradeDate")
    order = {ticker: position for position, ticker in enumerate(ORATS_OPTION_TICKERS)}
    return sorted(normalized, key=lambda row: order[str(row["provider_ticker"])])


__all__ = [
    "ORATS_OPTION_TICKERS", "ORATSOptionsPayloadError", "parse_cores_payload",
]
