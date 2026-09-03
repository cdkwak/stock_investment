"""Dated target-price consensus collection for a local personal watchlist.

The live transport is deliberately small and manual. Every successful or failed
HTTP response is captured before inspection; only validated observations are
eligible for the atomic Normalized append.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from math import isfinite
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from stock_data.contracts.research_target_prices import (
    RESEARCH_TARGET_PRICE_CONSENSUS,
)
from stock_data.providers.public_http_capture import capture_public_response
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic


YAHOO_SOURCE = "YAHOO_FINANCE_QUOTE_SUMMARY"
KOREAN_UNAVAILABLE_SOURCE = "NONE_COMPLIANT_KR_CONSENSUS_SOURCE"
YAHOO_TERMS_REF = "docs/data/sources/TARGET_PRICE_CONSENSUS.md#yahoo-finance-us"
KOREAN_TERMS_REF = "docs/data/sources/TARGET_PRICE_CONSENSUS.md#korean-markets"
KOREAN_UNAVAILABLE_MESSAGE = "출처 없음 — 표시 불가"
YAHOO_USER_AGENT = "stock-investment-rev1/0.1"
YAHOO_TIMEOUT_SECONDS = 30
YAHOO_MIN_REQUEST_INTERVAL_SECONDS = 1.0
YAHOO_OPERATION = "quote_summary_financial_data"
_KR_MARKETS = frozenset({"KR", "KRX", "KOSPI", "KOSDAQ"})
_US_MARKETS = frozenset({"US", "USA", "US ETF", "NASDAQ", "NYSE", "AMEX"})
_US_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-=]{0,19}")


class TargetPriceConsensusError(ValueError):
    """The watchlist, retained response, or Normalized rows violate the contract."""


@dataclass(frozen=True, slots=True)
class WatchlistSecurity:
    market: str
    symbol: str
    name: str | None
    isin: str | None
    currency: str

    @property
    def region(self) -> str:
        return _market_region(self.market)


@dataclass(frozen=True, slots=True)
class TargetPriceRequest:
    market: str
    symbol: str
    currency: str
    method: str
    url: str
    params: Mapping[str, str]
    headers: Mapping[str, str]
    timeout_seconds: int

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "symbol": self.symbol,
            "currency": self.currency,
            "method": self.method,
            "url": self.url,
            "params": dict(self.params),
            "headers": dict(self.headers),
            "timeout_seconds": self.timeout_seconds,
        }


def _market_region(market: str) -> str:
    normalized = market.strip().upper()
    if normalized in _KR_MARKETS:
        return "KR"
    if normalized in _US_MARKETS or normalized.startswith("US "):
        return "US"
    raise TargetPriceConsensusError(f"unsupported watchlist market: {market!r}")


def _text(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise TargetPriceConsensusError(f"{field} must be a non-empty trimmed string")
    return value


def _currency(value: object, *, region: str) -> str:
    if value in (None, ""):
        return "KRW" if region == "KR" else "USD"
    result = _text(value, "currency")
    assert result is not None
    result = result.upper()
    if re.fullmatch(r"[A-Z]{3}", result) is None:
        raise TargetPriceConsensusError("currency must be an ISO-style three-letter code")
    return result


def load_watchlist(path: Path) -> tuple[WatchlistSecurity, ...]:
    """Load and de-duplicate ``lists[].items[]`` without exposing other fields."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TargetPriceConsensusError(f"watchlist is unreadable: {path}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("lists"), list):
        raise TargetPriceConsensusError("watchlist must contain lists[]")
    result: list[WatchlistSecurity] = []
    seen: dict[tuple[str, str], WatchlistSecurity] = {}
    for list_index, watchlist in enumerate(payload["lists"]):
        if not isinstance(watchlist, dict) or not isinstance(watchlist.get("items"), list):
            raise TargetPriceConsensusError(f"lists[{list_index}].items must be a list")
        for item_index, item in enumerate(watchlist["items"]):
            if not isinstance(item, dict):
                raise TargetPriceConsensusError(
                    f"lists[{list_index}].items[{item_index}] must be an object"
                )
            market = _text(item.get("market"), "market")
            symbol = _text(item.get("symbol"), "symbol")
            assert market is not None and symbol is not None
            region = _market_region(market)
            symbol = symbol.upper()
            if region == "KR" and re.fullmatch(r"\d{6}", symbol) is None:
                raise TargetPriceConsensusError("Korean watchlist symbols must be six digits")
            if region == "US" and _US_SYMBOL.fullmatch(symbol) is None:
                raise TargetPriceConsensusError(f"invalid U.S. watchlist ticker: {symbol!r}")
            security = WatchlistSecurity(
                market=market,
                symbol=symbol,
                name=_text(item.get("name"), "name", nullable=True),
                isin=_text(item.get("isin"), "isin", nullable=True),
                currency=_currency(item.get("currency"), region=region),
            )
            identity = (region, symbol)
            previous = seen.get(identity)
            if previous is not None:
                if previous.currency != security.currency:
                    raise TargetPriceConsensusError(
                        f"duplicate watchlist identity has conflicting currency: {symbol}"
                    )
                continue
            seen[identity] = security
            result.append(security)
    return tuple(result)


def build_request_plan(
    securities: Iterable[WatchlistSecurity],
    *,
    completed: Iterable[str] = (),
) -> tuple[TargetPriceRequest, ...]:
    """Return the exact bounded Yahoo requests; Korean rows never create requests."""

    completed_symbols = {symbol.upper() for symbol in completed}
    requests_: list[TargetPriceRequest] = []
    for security in securities:
        if security.symbol in completed_symbols or security.region == "KR":
            continue
        requests_.append(TargetPriceRequest(
            market=security.market,
            symbol=security.symbol,
            currency=security.currency,
            method="GET",
            url=(
                "https://query1.finance.yahoo.com/v10/finance/quoteSummary/"
                + quote(security.symbol, safe="")
            ),
            params={"modules": "financialData"},
            headers={"User-Agent": YAHOO_USER_AGENT},
            timeout_seconds=YAHOO_TIMEOUT_SECONDS,
        ))
    return tuple(requests_)


def korean_unavailable_row(
    security: WatchlistSecurity, *, run_date: date, retrieved_at: datetime,
) -> dict[str, object]:
    if security.region != "KR":
        raise TargetPriceConsensusError("Korean unavailable rows require a Korean security")
    return {
        "date": run_date.isoformat(),
        "symbol": security.symbol,
        "market": security.market,
        "source": KOREAN_UNAVAILABLE_SOURCE,
        "target_mean": None,
        "target_high": None,
        "target_low": None,
        "analyst_count": None,
        "recommendation_mean": None,
        "currency": security.currency,
        "retrieved_at": _aware_utc(retrieved_at),
        "terms_ref": KOREAN_TERMS_REF,
    }


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TargetPriceConsensusError("retrieved_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _raw_number(
    value: object, field: str, *, integer: bool = False,
) -> float | int | None:
    if value is None:
        return None
    raw = value.get("raw") if isinstance(value, Mapping) else value
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not isfinite(raw):
        raise TargetPriceConsensusError(f"Yahoo {field} must contain a finite raw number or null")
    if integer:
        parsed = int(raw)
        if parsed != raw or parsed < 0:
            raise TargetPriceConsensusError(f"Yahoo {field} must be a non-negative integer")
        return parsed
    parsed_float = float(raw)
    if parsed_float < 0:
        raise TargetPriceConsensusError(f"Yahoo {field} must be non-negative")
    return parsed_float


def parse_yahoo_financial_data(
    payload: Mapping[str, Any],
    *,
    symbol: str,
    market: str,
    currency: str,
    run_date: date,
    retrieved_at: datetime,
) -> dict[str, object]:
    """Parse one retained ``quoteSummary?modules=financialData`` payload."""

    if _market_region(market) != "US" or _US_SYMBOL.fullmatch(symbol.upper()) is None:
        raise TargetPriceConsensusError("Yahoo target-price parsing requires a U.S. ticker")
    root = payload.get("quoteSummary")
    if not isinstance(root, Mapping) or root.get("error") is not None:
        raise TargetPriceConsensusError("Yahoo quoteSummary contains an error or is missing")
    result = root.get("result")
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], Mapping):
        raise TargetPriceConsensusError("Yahoo quoteSummary.result must contain one object")
    financial = result[0].get("financialData")
    if not isinstance(financial, Mapping):
        raise TargetPriceConsensusError("Yahoo financialData module is missing")
    provider_currency = financial.get("financialCurrency")
    normalized_currency = _currency(currency, region="US")
    if provider_currency not in (None, ""):
        observed_currency = _currency(provider_currency, region="US")
        if observed_currency != normalized_currency:
            raise TargetPriceConsensusError("Yahoo financialData currency differs from watchlist")
    row = {
        "date": run_date.isoformat(),
        "symbol": symbol.upper(),
        "market": market,
        "source": YAHOO_SOURCE,
        "target_mean": _raw_number(financial.get("targetMeanPrice"), "targetMeanPrice"),
        "target_high": _raw_number(financial.get("targetHighPrice"), "targetHighPrice"),
        "target_low": _raw_number(financial.get("targetLowPrice"), "targetLowPrice"),
        "analyst_count": _raw_number(
            financial.get("numberOfAnalystOpinions"),
            "numberOfAnalystOpinions",
            integer=True,
        ),
        "recommendation_mean": _raw_number(
            financial.get("recommendationMean"), "recommendationMean",
        ),
        "currency": normalized_currency,
        "retrieved_at": _aware_utc(retrieved_at),
        "terms_ref": YAHOO_TERMS_REF,
    }
    frame = rows_to_frame([row])
    validate_target_price_consensus(frame)
    return row


def rows_to_frame(rows: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=RESEARCH_TARGET_PRICE_CONSENSUS.column_names)
    if frame.empty:
        return frame
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True, errors="raise")
    frame["analyst_count"] = pd.array(frame["analyst_count"], dtype="Int64")
    for column in ("target_mean", "target_high", "target_low", "recommendation_mean"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    return frame


def validate_target_price_consensus(frame: pd.DataFrame) -> None:
    contract = RESEARCH_TARGET_PRICE_CONSENSUS
    if tuple(frame.columns) != contract.column_names:
        raise TargetPriceConsensusError("research target-price columns differ from contract")
    if frame.empty:
        raise TargetPriceConsensusError("research target-price dataset must not be empty")
    parsed_dates = pd.to_datetime(frame["date"], format="%Y-%m-%d", errors="coerce")
    if parsed_dates.isna().any() or not frame["date"].eq(parsed_dates.dt.strftime("%Y-%m-%d")).all():
        raise TargetPriceConsensusError("date must be canonical YYYY-MM-DD")
    if frame.duplicated(list(contract.primary_key)).any():
        raise TargetPriceConsensusError("research target-price primary key is duplicated")
    for value in frame["retrieved_at"]:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise TargetPriceConsensusError("retrieved_at must be timezone-aware")
    retrieved = pd.to_datetime(frame["retrieved_at"], utc=True, errors="coerce")
    if retrieved.isna().any():
        raise TargetPriceConsensusError("retrieved_at must be a valid UTC timestamp")
    for column in ("target_mean", "target_high", "target_low", "recommendation_mean"):
        values = pd.to_numeric(frame[column], errors="coerce")
        invalid = frame[column].notna() & (values.isna() | ~values.map(isfinite) | (values < 0))
        if invalid.any():
            raise TargetPriceConsensusError(f"{column} must be finite, non-negative, or null")
    recommendation = pd.to_numeric(frame["recommendation_mean"], errors="coerce")
    if ((recommendation.notna()) & ~recommendation.between(1, 5)).any():
        raise TargetPriceConsensusError("recommendation_mean must use Yahoo's 1-5 scale")
    analyst = pd.to_numeric(frame["analyst_count"], errors="coerce")
    if (
        (frame["analyst_count"].notna() & analyst.isna()).any()
        or (analyst.dropna() < 0).any()
        or not analyst.dropna().map(lambda value: float(value).is_integer()).all()
    ):
        raise TargetPriceConsensusError("analyst_count must be a non-negative integer or null")
    target_present = frame[["target_mean", "target_high", "target_low"]].notna().any(axis=1)
    if ((analyst.fillna(0) == 0) & target_present).any():
        raise TargetPriceConsensusError("target prices require a positive analyst_count")
    low, mean, high = (pd.to_numeric(frame[name], errors="coerce") for name in (
        "target_low", "target_mean", "target_high",
    ))
    if ((low.notna() & mean.notna() & (low > mean)) | (mean.notna() & high.notna() & (mean > high))).any():
        raise TargetPriceConsensusError("target price ordering must be low <= mean <= high")
    for index, row in frame.iterrows():
        market = _text(row["market"], "market")
        symbol = _text(row["symbol"], "symbol")
        source = _text(row["source"], "source")
        terms_ref = _text(row["terms_ref"], "terms_ref")
        assert market is not None and symbol is not None and source is not None and terms_ref is not None
        region = _market_region(market)
        _currency(row["currency"], region=region)
        if region == "KR":
            if re.fullmatch(r"\d{6}", symbol) is None:
                raise TargetPriceConsensusError("Korean symbols must be six digits")
            if source != KOREAN_UNAVAILABLE_SOURCE or terms_ref != KOREAN_TERMS_REF:
                raise TargetPriceConsensusError("Korean rows must identify the unavailable source decision")
            if row[[
                "target_mean", "target_high", "target_low", "analyst_count",
                "recommendation_mean",
            ]].notna().any():
                raise TargetPriceConsensusError("Korean unavailable rows cannot contain consensus values")
        else:
            if _US_SYMBOL.fullmatch(symbol) is None:
                raise TargetPriceConsensusError("U.S. ticker is invalid")
            if source != YAHOO_SOURCE or terms_ref != YAHOO_TERMS_REF:
                raise TargetPriceConsensusError("U.S. rows must identify Yahoo and its terms reference")


def read_target_price_consensus(root: Path) -> pd.DataFrame:
    return read_dataset(root, RESEARCH_TARGET_PRICE_CONSENSUS, validate_target_price_consensus)


def append_target_price_vintages_atomic(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    """Append new date/security identities and atomically preserve every prior vintage."""

    validate_target_price_consensus(frame)
    try:
        existing = read_target_price_consensus(root)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        keys = list(RESEARCH_TARGET_PRICE_CONSENSUS.primary_key)
        overlap = existing[keys].merge(frame[keys], how="inner", on=keys)
        if not overlap.empty:
            raise TargetPriceConsensusError("refusing to overwrite an existing symbol/run-date vintage")
        combined = pd.concat([existing, frame], ignore_index=True)
    else:
        combined = frame.copy()
    combined = combined[list(RESEARCH_TARGET_PRICE_CONSENSUS.column_names)].sort_values(
        list(RESEARCH_TARGET_PRICE_CONSENSUS.sort_key), kind="stable",
    ).reset_index(drop=True)
    validate_target_price_consensus(combined)
    write_dataset_atomic(
        combined,
        root,
        RESEARCH_TARGET_PRICE_CONSENSUS,
        validate_target_price_consensus,
    )
    return combined


def collect_yahoo_rows(
    requests_: Sequence[TargetPriceRequest],
    *,
    run_date: date,
    landing_run_root: Path,
    session: requests.Session,
    sleep: Callable[[float], None] = time.sleep,
    min_interval_seconds: float = YAHOO_MIN_REQUEST_INTERVAL_SECONDS,
) -> list[dict[str, object]]:
    """Execute each planned request once, capture first, and stop on any failure."""

    if min_interval_seconds < 0:
        raise ValueError("minimum request interval must be non-negative")
    rows: list[dict[str, object]] = []
    previous_started: float | None = None
    for request in requests_:
        if request.method != "GET":
            raise TargetPriceConsensusError("unsupported planned method")
        if previous_started is not None:
            remaining = min_interval_seconds - (time.monotonic() - previous_started)
            if remaining > 0:
                sleep(remaining)
        previous_started = time.monotonic()
        response = session.get(
            request.url,
            params=dict(request.params),
            headers=dict(request.headers),
            timeout=request.timeout_seconds,
        )
        receipt = capture_public_response(
            root=landing_run_root,
            provider="yahoo",
            operation=YAHOO_OPERATION,
            request_url=request.url,
            request_parameters={"symbol": request.symbol, **request.params},
            response=response,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise TargetPriceConsensusError("Yahoo response root must be an object")
        rows.append(parse_yahoo_financial_data(
            payload,
            symbol=request.symbol,
            market=request.market,
            currency=request.currency,
            run_date=run_date,
            retrieved_at=datetime.fromisoformat(
                receipt.captured_at_utc.replace("Z", "+00:00")
            ),
        ))
    return rows


__all__ = [
    "KOREAN_TERMS_REF",
    "KOREAN_UNAVAILABLE_MESSAGE",
    "KOREAN_UNAVAILABLE_SOURCE",
    "TargetPriceConsensusError",
    "TargetPriceRequest",
    "WatchlistSecurity",
    "YAHOO_MIN_REQUEST_INTERVAL_SECONDS",
    "YAHOO_SOURCE",
    "YAHOO_TERMS_REF",
    "append_target_price_vintages_atomic",
    "build_request_plan",
    "collect_yahoo_rows",
    "korean_unavailable_row",
    "load_watchlist",
    "parse_yahoo_financial_data",
    "read_target_price_consensus",
    "rows_to_frame",
    "validate_target_price_consensus",
]
