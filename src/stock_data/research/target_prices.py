"""Dated target-price consensus collection for a local personal watchlist.

The live transport is deliberately small and manual. Every successful or failed
HTTP response is captured before inspection; only validated observations are
eligible for the atomic Normalized append.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
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

from stock_data.contracts.base import ColumnContract
from stock_data.contracts.research_target_prices import (
    RESEARCH_TARGET_PRICE_CONSENSUS,
)
from stock_data.providers.public_http_capture import capture_public_response
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic


YAHOO_SOURCE = "YAHOO_FINANCE_QUOTE_SUMMARY"
KOREAN_UNAVAILABLE_SOURCE = "NONE_COMPLIANT_KR_CONSENSUS_SOURCE"
YAHOO_TERMS_REF = "docs/data/sources/TARGET_PRICE_CONSENSUS.md#yahoo-finance-us"
KOREAN_TERMS_REF = "docs/data/sources/TARGET_PRICE_CONSENSUS.md#korean-markets"
# Yahoo's crumb endpoint answers 429 to non-browser agents (checked 2026-09-05: the former
# "stock-investment-rev1/0.1" agent got 429, a plain browser string got 200 on the same cookie).
YAHOO_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
YAHOO_TIMEOUT_SECONDS = 30
YAHOO_MIN_REQUEST_INTERVAL_SECONDS = 1.0
YAHOO_OPERATION = "quote_summary_financial_data"
YAHOO_COOKIE_OPERATION = "quote_summary_cookie"
YAHOO_CRUMB_OPERATION = "quote_summary_crumb"
YAHOO_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
AVAILABLE = "AVAILABLE"
NOT_APPLICABLE_ETF = "NOT_APPLICABLE_ETF"
NO_COVERAGE = "NO_COVERAGE"
NOT_COLLECTED = "NOT_COLLECTED"
UNAVAILABLE_SOURCE = "UNAVAILABLE_SOURCE"
TARGET_PRICE_STATUSES = frozenset({
    AVAILABLE, NOT_APPLICABLE_ETF, NO_COVERAGE, NOT_COLLECTED,
    UNAVAILABLE_SOURCE,
})
TARGET_PRICE_CARD_TEXT = {
    AVAILABLE: "참고 · 출처 · 기준일 · 표본 n명 · 현재가 대비 괴리율",
    NOT_APPLICABLE_ETF: "애널리스트 목표가 없음 (ETF)",
    NO_COVERAGE: "커버리지 없음",
    NOT_COLLECTED: "미수집 · 수집기 미실행",
    UNAVAILABLE_SOURCE: "거래소 확인 불가 · 수집 불가",
}
# Backward-compatible import name; only unresolved exchange identity uses it.
KOREAN_UNAVAILABLE_MESSAGE = TARGET_PRICE_CARD_TEXT[UNAVAILABLE_SOURCE]
_KR_MARKETS = frozenset({"KR", "KRX", "KOSPI", "KOSDAQ"})
_US_MARKETS = frozenset({"US", "USA", "US ETF", "NASDAQ", "NYSE", "AMEX"})
_US_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.\-=]{0,19}")
_KR_YAHOO_SUFFIX = {"KOSPI": "KS", "KOSDAQ": "KQ"}
_CONSENSUS_FIELDS = frozenset({
    "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "numberOfAnalystOpinions", "recommendationMean",
})

# The user-authorized scope excludes src/stock_data/contracts. The v1
# declaration remains the backward-read schema; this collector owns the
# additive status-bearing v2 storage view.
_STATUS_COLUMN = ColumnContract(
    "status", "string", False, description="Typed collection/display outcome",
)
_V2_COLUMNS = []
for _column in RESEARCH_TARGET_PRICE_CONSENSUS.columns:
    _V2_COLUMNS.append(_column)
    if _column.name == "source":
        _V2_COLUMNS.append(_STATUS_COLUMN)
TARGET_PRICE_CONSENSUS = replace(
    RESEARCH_TARGET_PRICE_CONSENSUS,
    version=2,
    source="yahoo_finance_quote_summary_or_unresolved_korean_exchange",
    columns=tuple(_V2_COLUMNS),
)


class TargetPriceConsensusError(ValueError):
    """The watchlist, retained response, or Normalized rows violate the contract."""


@dataclass(frozen=True, slots=True)
class WatchlistSecurity:
    market: str
    symbol: str
    name: str | None
    isin: str | None
    currency: str
    is_fund_product: bool

    @property
    def region(self) -> str:
        return _market_region(self.market)


@dataclass(frozen=True, slots=True)
class TargetPriceRequest:
    market: str
    symbol: str
    provider_symbol: str
    currency: str
    is_fund_product: bool
    method: str
    url: str
    params: Mapping[str, str]
    headers: Mapping[str, str]
    timeout_seconds: int

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "symbol": self.symbol,
            "provider_symbol": self.provider_symbol,
            "currency": self.currency,
            "is_fund_product": self.is_fund_product,
            "status": NOT_COLLECTED,
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


def yahoo_provider_symbol(security: WatchlistSecurity) -> str | None:
    """Resolve only exchange-qualified identities retained by the watchlist."""

    if security.region == "US":
        return security.symbol
    suffix = _KR_YAHOO_SUFFIX.get(security.market.strip().upper())
    if suffix is None:
        return None
    return f"{security.symbol}.{suffix}"


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
                is_fund_product=(
                    market.strip().upper() == "US ETF"
                    or str(item.get("security_type", "")).strip().upper()
                    in {"ETF", "ETN"}
                    or item.get("leverage_multiple") not in (None, "")
                ),
            )
            identity = (region, symbol)
            previous = seen.get(identity)
            if previous is not None:
                if (
                    previous.currency != security.currency
                    or previous.market.strip().upper() != security.market.strip().upper()
                    or previous.is_fund_product != security.is_fund_product
                ):
                    raise TargetPriceConsensusError(
                        f"duplicate watchlist identity has conflicting metadata: {symbol}"
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
    """Return exact bounded Yahoo requests for every resolvable exchange identity."""

    completed_symbols = {symbol.upper() for symbol in completed}
    requests_: list[TargetPriceRequest] = []
    for security in securities:
        provider_symbol = yahoo_provider_symbol(security)
        if security.symbol in completed_symbols or provider_symbol is None:
            continue
        requests_.append(TargetPriceRequest(
            market=security.market,
            symbol=security.symbol,
            provider_symbol=provider_symbol,
            currency=security.currency,
            is_fund_product=security.is_fund_product,
            method="GET",
            url=(
                "https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
                + quote(provider_symbol, safe="")
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
    if yahoo_provider_symbol(security) is not None:
        raise TargetPriceConsensusError(
            "Korean unavailable rows require unresolved exchange identity"
        )
    # A KRX-listed fund (ETF/ETN) has no analyst target regardless of exchange identity:
    # report "해당 없음 (ETF)" rather than an unresolved-exchange failure.
    status = NOT_APPLICABLE_ETF if security.is_fund_product else UNAVAILABLE_SOURCE
    return {
        "date": run_date.isoformat(),
        "symbol": security.symbol,
        "market": security.market,
        "source": KOREAN_UNAVAILABLE_SOURCE,
        "status": status,
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


def _empty_yahoo_row(
    *, symbol: str, market: str, currency: str, status: str,
    run_date: date, retrieved_at: datetime, analyst_count: int | None = None,
) -> dict[str, object]:
    region = _market_region(market)
    return {
        "date": run_date.isoformat(),
        "symbol": symbol.upper(),
        "market": market,
        "source": YAHOO_SOURCE,
        "status": status,
        "target_mean": None,
        "target_high": None,
        "target_low": None,
        "analyst_count": analyst_count,
        "recommendation_mean": None,
        "currency": _currency(currency, region=region),
        "retrieved_at": _aware_utc(retrieved_at),
        "terms_ref": KOREAN_TERMS_REF if region == "KR" else YAHOO_TERMS_REF,
    }


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
    is_fund_product: bool = False,
) -> dict[str, object]:
    """Parse one retained ``quoteSummary?modules=financialData`` payload."""

    region = _market_region(market)
    if region == "KR":
        if re.fullmatch(r"\d{6}", symbol.upper()) is None:
            raise TargetPriceConsensusError("Yahoo Korean parsing requires a six-digit code")
    elif _US_SYMBOL.fullmatch(symbol.upper()) is None:
        raise TargetPriceConsensusError("Yahoo target-price parsing requires a valid ticker")
    root = payload.get("quoteSummary")
    if not isinstance(root, Mapping) or root.get("error") is not None:
        raise TargetPriceConsensusError("Yahoo quoteSummary contains an error or is missing")
    result = root.get("result")
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], Mapping):
        raise TargetPriceConsensusError("Yahoo quoteSummary.result must contain one object")
    financial = result[0].get("financialData")
    if not isinstance(financial, Mapping):
        if is_fund_product:
            return _empty_yahoo_row(
                symbol=symbol, market=market, currency=currency,
                status=NOT_APPLICABLE_ETF, run_date=run_date,
                retrieved_at=retrieved_at,
            )
        raise TargetPriceConsensusError("Yahoo financialData module is missing")
    if not (_CONSENSUS_FIELDS & set(financial)):
        if is_fund_product:
            return _empty_yahoo_row(
                symbol=symbol, market=market, currency=currency,
                status=NOT_APPLICABLE_ETF, run_date=run_date,
                retrieved_at=retrieved_at,
            )
        raise TargetPriceConsensusError("Yahoo financialData consensus fields are missing")
    provider_currency = financial.get("financialCurrency")
    normalized_currency = _currency(currency, region=region)
    if region == "KR" and provider_currency in (None, ""):
        raise TargetPriceConsensusError("Yahoo Korean financialCurrency is missing")
    if provider_currency not in (None, ""):
        observed_currency = _currency(provider_currency, region=region)
        if observed_currency != normalized_currency:
            raise TargetPriceConsensusError("Yahoo financialData currency differs from watchlist")
    analyst_count = _raw_number(
        financial.get("numberOfAnalystOpinions"),
        "numberOfAnalystOpinions", integer=True,
    )
    if analyst_count in (None, 0):
        row = _empty_yahoo_row(
            symbol=symbol, market=market, currency=normalized_currency,
            status=NO_COVERAGE, run_date=run_date, retrieved_at=retrieved_at,
            analyst_count=analyst_count,
        )
        validate_target_price_consensus(rows_to_frame([row]))
        return row
    row = {
        "date": run_date.isoformat(),
        "symbol": symbol.upper(),
        "market": market,
        "source": YAHOO_SOURCE,
        "status": AVAILABLE,
        "target_mean": _raw_number(financial.get("targetMeanPrice"), "targetMeanPrice"),
        "target_high": _raw_number(financial.get("targetHighPrice"), "targetHighPrice"),
        "target_low": _raw_number(financial.get("targetLowPrice"), "targetLowPrice"),
        "analyst_count": analyst_count,
        "recommendation_mean": _raw_number(
            financial.get("recommendationMean"), "recommendationMean",
        ),
        "currency": normalized_currency,
        "retrieved_at": _aware_utc(retrieved_at),
        "terms_ref": KOREAN_TERMS_REF if region == "KR" else YAHOO_TERMS_REF,
    }
    frame = rows_to_frame([row])
    validate_target_price_consensus(frame)
    return row


def _legacy_row_status(row: Mapping[str, object]) -> str:
    if row.get("source") == KOREAN_UNAVAILABLE_SOURCE:
        return UNAVAILABLE_SOURCE
    analyst = _raw_number(
        row.get("analyst_count"), "legacy analyst_count", integer=True,
    )
    if analyst is not None and analyst > 0 and row.get("target_mean") is not None:
        return AVAILABLE
    return NO_COVERAGE


def rows_to_frame(rows: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    normalized_rows = []
    for source_row in rows:
        row = dict(source_row)
        if "status" not in row:
            row["status"] = _legacy_row_status(row)
        normalized_rows.append(row)
    frame = pd.DataFrame(
        normalized_rows, columns=TARGET_PRICE_CONSENSUS.column_names,
    )
    if frame.empty:
        return frame
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True, errors="raise")
    frame["analyst_count"] = pd.array(frame["analyst_count"], dtype="Int64")
    for column in ("target_mean", "target_high", "target_low", "recommendation_mean"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    return frame


def validate_target_price_consensus(frame: pd.DataFrame) -> None:
    contract = TARGET_PRICE_CONSENSUS
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
    if not frame["status"].isin(TARGET_PRICE_STATUSES).all():
        raise TargetPriceConsensusError("target-price status is invalid")
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
        currency = _currency(row["currency"], region=region)
        status = _text(row["status"], "status")
        assert status is not None
        if status == AVAILABLE:
            if row["target_mean"] is None or pd.isna(row["target_mean"]):
                raise TargetPriceConsensusError("AVAILABLE rows require target_mean")
            if pd.isna(row["analyst_count"]) or int(row["analyst_count"]) <= 0:
                raise TargetPriceConsensusError("AVAILABLE rows require positive analyst_count")
        elif status == NO_COVERAGE:
            if row[[
                "target_mean", "target_high", "target_low", "recommendation_mean",
            ]].notna().any() or (
                not pd.isna(row["analyst_count"]) and int(row["analyst_count"]) != 0
            ):
                raise TargetPriceConsensusError("NO_COVERAGE rows must be value-free")
        elif row[[
            "target_mean", "target_high", "target_low", "analyst_count",
            "recommendation_mean",
        ]].notna().any():
            raise TargetPriceConsensusError(f"{status} rows must be value-free")
        if region == "KR":
            if re.fullmatch(r"\d{6}", symbol) is None:
                raise TargetPriceConsensusError("Korean symbols must be six digits")
            if currency != "KRW" or terms_ref != KOREAN_TERMS_REF:
                raise TargetPriceConsensusError("Korean rows require KRW and Korean terms")
            if status in {UNAVAILABLE_SOURCE, NOT_APPLICABLE_ETF} and source == KOREAN_UNAVAILABLE_SOURCE:
                pass  # no Yahoo call was made: unresolved exchange, or a KRX fund with no analyst target
            elif status == UNAVAILABLE_SOURCE:
                raise TargetPriceConsensusError("legacy Korean fallback source differs")
            elif source != YAHOO_SOURCE:
                raise TargetPriceConsensusError("collectable Korean rows must identify Yahoo")
        else:
            if _US_SYMBOL.fullmatch(symbol) is None:
                raise TargetPriceConsensusError("U.S. ticker is invalid")
            if source != YAHOO_SOURCE or terms_ref != YAHOO_TERMS_REF:
                raise TargetPriceConsensusError("U.S. rows must identify Yahoo and its terms reference")
            if status == UNAVAILABLE_SOURCE:
                raise TargetPriceConsensusError("U.S. rows cannot use UNAVAILABLE_SOURCE")


def _upgrade_legacy_frame(frame: pd.DataFrame) -> pd.DataFrame:
    upgraded = frame.copy()
    status = []
    for _, row in upgraded.iterrows():
        if row["source"] == KOREAN_UNAVAILABLE_SOURCE:
            status.append(UNAVAILABLE_SOURCE)
        elif not pd.isna(row["analyst_count"]) and int(row["analyst_count"]) > 0:
            status.append(AVAILABLE)
        else:
            status.append(NO_COVERAGE)
    upgraded.insert(upgraded.columns.get_loc("source") + 1, "status", status)
    upgraded = upgraded[list(TARGET_PRICE_CONSENSUS.column_names)]
    validate_target_price_consensus(upgraded)
    return upgraded


def read_target_price_consensus(root: Path) -> pd.DataFrame:
    try:
        return read_dataset(root, TARGET_PRICE_CONSENSUS, validate_target_price_consensus)
    except KeyError as error:
        if "status" not in str(error):
            raise
        legacy = read_dataset(root, RESEARCH_TARGET_PRICE_CONSENSUS, lambda _frame: None)
        return _upgrade_legacy_frame(legacy)


def append_target_price_vintages_atomic(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    """Append new date/security identities and atomically preserve every prior vintage."""

    validate_target_price_consensus(frame)
    try:
        existing = read_target_price_consensus(root)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        keys = list(TARGET_PRICE_CONSENSUS.primary_key)
        overlap = existing[keys].merge(frame[keys], how="inner", on=keys)
        if not overlap.empty:
            raise TargetPriceConsensusError("refusing to overwrite an existing symbol/run-date vintage")
        combined = pd.concat([existing, frame], ignore_index=True)
    else:
        combined = frame.copy()
    combined = combined[list(TARGET_PRICE_CONSENSUS.column_names)].sort_values(
        list(TARGET_PRICE_CONSENSUS.sort_key), kind="stable",
    ).reset_index(drop=True)
    validate_target_price_consensus(combined)
    write_dataset_atomic(
        combined,
        root,
        TARGET_PRICE_CONSENSUS,
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
    if not requests_:
        return []
    rows: list[dict[str, object]] = []
    previous_started: float | None = None

    def get(
        url: str, *, params: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
        timeout_seconds: int = YAHOO_TIMEOUT_SECONDS,
    ):
        nonlocal previous_started
        if previous_started is not None:
            remaining = min_interval_seconds - (time.monotonic() - previous_started)
            if remaining > 0:
                sleep(remaining)
        previous_started = time.monotonic()
        return session.get(
            url, params=dict(params),
            headers=dict(headers or {"User-Agent": YAHOO_USER_AGENT}),
            timeout=timeout_seconds,
        )

    cookie_response = get(YAHOO_COOKIE_URL, params={})
    capture_public_response(
        root=landing_run_root, provider="yahoo",
        operation=YAHOO_COOKIE_OPERATION, request_url=YAHOO_COOKIE_URL,
        request_parameters={"phase": "cookie"}, response=cookie_response,
    )
    cookies = getattr(session, "cookies", ())
    if not any(getattr(cookie, "name", None) == "A3" for cookie in cookies):
        cookie_response.raise_for_status()
        raise TargetPriceConsensusError("Yahoo A3 cookie handshake failed")
    if cookie_response.status_code >= 500:
        cookie_response.raise_for_status()

    crumb_response = get(YAHOO_CRUMB_URL, params={})
    capture_public_response(
        root=landing_run_root, provider="yahoo",
        operation=YAHOO_CRUMB_OPERATION, request_url=YAHOO_CRUMB_URL,
        request_parameters={"phase": "crumb"}, response=crumb_response,
    )
    crumb_response.raise_for_status()
    crumb = crumb_response.text.strip()
    if (
        not crumb or len(crumb) > 256
        or any(character.isspace() or ord(character) < 32 for character in crumb)
    ):
        raise TargetPriceConsensusError("Yahoo crumb response is invalid")

    for request in requests_:
        if request.method != "GET":
            raise TargetPriceConsensusError("unsupported planned method")
        response = get(
            request.url, params={**request.params, "crumb": crumb},
            headers=request.headers, timeout_seconds=request.timeout_seconds,
        )
        receipt = capture_public_response(
            root=landing_run_root,
            provider="yahoo",
            operation=YAHOO_OPERATION,
            request_url=request.url,
            request_parameters={
                "symbol": request.symbol,
                "provider_symbol": request.provider_symbol,
                **request.params,
            },
            response=response,
        )
        retrieved_at = datetime.fromisoformat(
            receipt.captured_at_utc.replace("Z", "+00:00")
        )
        if response.status_code == 404 and request.is_fund_product:
            rows.append(_empty_yahoo_row(
                symbol=request.symbol, market=request.market,
                currency=request.currency, status=NOT_APPLICABLE_ETF,
                run_date=run_date, retrieved_at=retrieved_at,
            ))
            continue
        if response.status_code >= 400:
            raise TargetPriceConsensusError(
                f"Yahoo quoteSummary HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise TargetPriceConsensusError("Yahoo response root must be an object")
        rows.append(parse_yahoo_financial_data(
            payload,
            symbol=request.symbol,
            market=request.market,
            currency=request.currency,
            run_date=run_date,
            retrieved_at=retrieved_at,
            is_fund_product=request.is_fund_product,
        ))
    return rows


__all__ = [
    "AVAILABLE",
    "KOREAN_TERMS_REF",
    "KOREAN_UNAVAILABLE_MESSAGE",
    "KOREAN_UNAVAILABLE_SOURCE",
    "NOT_APPLICABLE_ETF",
    "NOT_COLLECTED",
    "NO_COVERAGE",
    "TARGET_PRICE_CARD_TEXT",
    "TARGET_PRICE_CONSENSUS",
    "TARGET_PRICE_STATUSES",
    "TargetPriceConsensusError",
    "TargetPriceRequest",
    "WatchlistSecurity",
    "YAHOO_MIN_REQUEST_INTERVAL_SECONDS",
    "YAHOO_COOKIE_URL",
    "YAHOO_CRUMB_URL",
    "YAHOO_SOURCE",
    "YAHOO_TERMS_REF",
    "UNAVAILABLE_SOURCE",
    "append_target_price_vintages_atomic",
    "build_request_plan",
    "collect_yahoo_rows",
    "korean_unavailable_row",
    "load_watchlist",
    "parse_yahoo_financial_data",
    "read_target_price_consensus",
    "rows_to_frame",
    "validate_target_price_consensus",
    "yahoo_provider_symbol",
]
