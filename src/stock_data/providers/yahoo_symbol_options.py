"""Offline parser and per-symbol volume P/C projection for Yahoo option evidence.

This module intentionally has no HTTP client.  Callers must first retain a bounded
Yahoo response and pass the decoded payload plus the capture timestamp here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
INITIAL_OPTION_SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "SOXX", "SOXL", "TQQQ")
CONDITIONAL_OPTION_SYMBOLS = ("EWY", "KORU", "QLD")
PRICE_ONLY_SYMBOLS = ("DRAM", "SKHY")
ALL_PILOT_SYMBOLS = INITIAL_OPTION_SYMBOLS + CONDITIONAL_OPTION_SYMBOLS + PRICE_ONLY_SYMBOLS


class YahooSymbolOptionError(ValueError):
    """Retained Yahoo option evidence is malformed or outside the pilot scope."""


class SymbolOptionPCRStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    NO_RETAINED_CHAIN = "NO_RETAINED_CHAIN"
    PRICE_ONLY = "PRICE_ONLY"
    MULTIPLIER_UNVERIFIED = "MULTIPLIER_UNVERIFIED"
    NONSTANDARD_ONLY = "NONSTANDARD_ONLY"
    MISSING_VOLUME = "MISSING_VOLUME"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    STALE = "STALE"


@dataclass(frozen=True)
class YahooOptionContract:
    contract_symbol: str
    side: str
    expiry_at_utc: datetime
    last_trade_at_utc: datetime | None
    strike: float
    volume: int | None
    open_interest: int | None
    contract_size: str


@dataclass(frozen=True)
class YahooOptionChainSnapshot:
    symbol: str
    expiry_at_utc: datetime
    captured_at_utc: datetime
    underlying_quote_at_utc: datetime | None
    contracts: tuple[YahooOptionContract, ...]


@dataclass(frozen=True)
class YahooSymbolVolumePCR:
    symbol: str
    value: float | None
    status: SymbolOptionPCRStatus
    reason: str
    call_volume: int | None
    put_volume: int | None
    expiry_count: int
    standard_contract_count: int
    excluded_nonstandard_count: int
    captured_at_utc: datetime | None
    captured_at_kst: datetime | None
    latest_contract_trade_at_utc: datetime | None
    latest_contract_trade_at_kst: datetime | None
    source: str = "YAHOO_UNOFFICIAL_OPTION_CHAIN_RESEARCH"
    provider_timestamp_status: str = "CONTRACT_LAST_TRADE_ONLY"
    backtest_eligible: bool = False

    @property
    def displays_value(self) -> bool:
        return self.status is SymbolOptionPCRStatus.AVAILABLE and self.value is not None


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise YahooSymbolOptionError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _unix_time(value: object, name: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise YahooSymbolOptionError(f"{name} must be a finite unix timestamp")
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


def _nonnegative_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise YahooSymbolOptionError(f"{name} must be a non-negative integer or null")
    integer = int(value)
    if integer != value or integer < 0:
        raise YahooSymbolOptionError(f"{name} must be a non-negative integer or null")
    return integer


def _contract(row: Mapping[str, Any], side: str, expiry_at_utc: datetime) -> YahooOptionContract:
    symbol = row.get("contractSymbol")
    size = row.get("contractSize")
    strike = row.get("strike")
    if not isinstance(symbol, str) or not symbol:
        raise YahooSymbolOptionError("contractSymbol must be a non-empty string")
    if not isinstance(size, str) or not size:
        raise YahooSymbolOptionError(f"{symbol} contractSize must be a non-empty string")
    if isinstance(strike, bool) or not isinstance(strike, (int, float)) or not isfinite(strike) or strike < 0:
        raise YahooSymbolOptionError(f"{symbol} strike must be finite and non-negative")
    return YahooOptionContract(
        contract_symbol=symbol,
        side=side,
        expiry_at_utc=expiry_at_utc,
        last_trade_at_utc=_unix_time(row.get("lastTradeDate"), f"{symbol}.lastTradeDate", nullable=True),
        strike=float(strike),
        volume=_nonnegative_int(row.get("volume"), f"{symbol}.volume"),
        open_interest=_nonnegative_int(row.get("openInterest"), f"{symbol}.openInterest"),
        contract_size=size.upper(),
    )


def parse_yahoo_option_chain(
    payload: Mapping[str, Any], *, symbol: str, captured_at_utc: datetime,
) -> YahooOptionChainSnapshot:
    """Parse one retained Yahoo expiry response without network access."""
    requested = symbol.upper()
    if requested not in ALL_PILOT_SYMBOLS or requested in PRICE_ONLY_SYMBOLS:
        raise YahooSymbolOptionError(f"symbol is outside option-chain pilot scope: {requested}")
    captured = _aware_utc(captured_at_utc, "captured_at_utc")
    chain = payload.get("optionChain")
    if not isinstance(chain, Mapping) or chain.get("error") is not None:
        raise YahooSymbolOptionError("optionChain error or missing object")
    result = chain.get("result")
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], Mapping):
        raise YahooSymbolOptionError("optionChain.result must contain exactly one object")
    root = result[0]
    quote = root.get("quote")
    if not isinstance(quote, Mapping) or str(quote.get("symbol", "")).upper() != requested:
        raise YahooSymbolOptionError("provider symbol does not match requested symbol")
    options = root.get("options")
    if not isinstance(options, list) or len(options) != 1 or not isinstance(options[0], Mapping):
        raise YahooSymbolOptionError("retained response must contain exactly one expiry")
    option = options[0]
    expiry = _unix_time(option.get("expirationDate"), "expirationDate")
    contracts: list[YahooOptionContract] = []
    for provider_key, side in (("calls", "CALL"), ("puts", "PUT")):
        rows = option.get(provider_key)
        if not isinstance(rows, list):
            raise YahooSymbolOptionError(f"{provider_key} must be a list")
        for row in rows:
            if not isinstance(row, Mapping):
                raise YahooSymbolOptionError(f"{provider_key} contains a non-object")
            contracts.append(_contract(row, side, expiry))
    identities = [row.contract_symbol for row in contracts]
    if len(identities) != len(set(identities)):
        raise YahooSymbolOptionError("duplicate contractSymbol in retained expiry")
    return YahooOptionChainSnapshot(
        symbol=requested,
        expiry_at_utc=expiry,
        captured_at_utc=captured,
        underlying_quote_at_utc=_unix_time(
            quote.get("regularMarketTime"), "quote.regularMarketTime", nullable=True,
        ),
        contracts=tuple(contracts),
    )


def _empty_result(symbol: str, status: SymbolOptionPCRStatus, reason: str) -> YahooSymbolVolumePCR:
    return YahooSymbolVolumePCR(
        symbol=symbol, value=None, status=status, reason=reason, call_volume=None,
        put_volume=None, expiry_count=0, standard_contract_count=0,
        excluded_nonstandard_count=0, captured_at_utc=None, captured_at_kst=None,
        latest_contract_trade_at_utc=None, latest_contract_trade_at_kst=None,
    )


def derive_yahoo_symbol_volume_pcr(
    symbol: str,
    snapshots: Iterable[YahooOptionChainSnapshot],
    *,
    multiplier_verified_symbols: frozenset[str] = frozenset(),
    now_utc: datetime | None = None,
    max_capture_age_seconds: int = 6 * 60 * 60,
) -> YahooSymbolVolumePCR:
    """Derive one symbol only; never aggregate or average across underlyings."""
    requested = symbol.upper()
    if requested in PRICE_ONLY_SYMBOLS:
        return _empty_result(
            requested, SymbolOptionPCRStatus.PRICE_ONLY,
            "Option availability and a standard multiplier are not independently verified; price/volume comparison only.",
        )
    if requested not in INITIAL_OPTION_SYMBOLS + CONDITIONAL_OPTION_SYMBOLS:
        raise YahooSymbolOptionError(f"symbol is outside pilot scope: {requested}")
    rows = tuple(snapshots)
    if not rows:
        return _empty_result(requested, SymbolOptionPCRStatus.NO_RETAINED_CHAIN, "No retained populated Yahoo chain evidence.")
    if any(row.symbol != requested for row in rows):
        raise YahooSymbolOptionError("cross-symbol aggregation is forbidden")
    expiries = [row.expiry_at_utc for row in rows]
    if len(expiries) != len(set(expiries)):
        raise YahooSymbolOptionError("duplicate expiry snapshots")
    captured = max(row.captured_at_utc for row in rows)
    if requested not in {value.upper() for value in multiplier_verified_symbols}:
        return _result_from_snapshots(
            requested, rows, SymbolOptionPCRStatus.MULTIPLIER_UNVERIFIED,
            "Yahoo REGULAR classification is retained, but the standard contract multiplier is not independently verified.",
        )
    now = _aware_utc(now_utc or datetime.now(timezone.utc), "now_utc")
    if max_capture_age_seconds < 0:
        raise YahooSymbolOptionError("max_capture_age_seconds must be non-negative")
    if (now - captured).total_seconds() > max_capture_age_seconds:
        return _result_from_snapshots(requested, rows, SymbolOptionPCRStatus.STALE, "Retained chain capture exceeded the display freshness limit.")
    standard = tuple(contract for row in rows for contract in row.contracts if contract.contract_size == "REGULAR")
    if not standard:
        return _result_from_snapshots(requested, rows, SymbolOptionPCRStatus.NONSTANDARD_ONLY, "No provider-classified REGULAR contracts in retained expiries.")
    if any(contract.volume is None for contract in standard):
        return _result_from_snapshots(requested, rows, SymbolOptionPCRStatus.MISSING_VOLUME, "At least one REGULAR contract has missing volume; full-expiry aggregation is suppressed.")
    call_volume = sum(contract.volume or 0 for contract in standard if contract.side == "CALL")
    put_volume = sum(contract.volume or 0 for contract in standard if contract.side == "PUT")
    if call_volume <= 0 or put_volume <= 0:
        return _result_from_snapshots(
            requested, rows, SymbolOptionPCRStatus.INSUFFICIENT_LIQUIDITY,
            "Positive observed call and put volume are both required; missing sides are never converted to zero P/C.",
            call_volume=call_volume, put_volume=put_volume,
        )
    return _result_from_snapshots(
        requested, rows, SymbolOptionPCRStatus.AVAILABLE,
        "Separate per-symbol Yahoo research ratio: summed REGULAR put volume / summed REGULAR call volume.",
        call_volume=call_volume, put_volume=put_volume, value=put_volume / call_volume,
    )


def _result_from_snapshots(
    symbol: str,
    snapshots: tuple[YahooOptionChainSnapshot, ...],
    status: SymbolOptionPCRStatus,
    reason: str,
    *,
    call_volume: int | None = None,
    put_volume: int | None = None,
    value: float | None = None,
) -> YahooSymbolVolumePCR:
    contracts = tuple(contract for row in snapshots for contract in row.contracts)
    standard = tuple(contract for contract in contracts if contract.contract_size == "REGULAR")
    trades = tuple(contract.last_trade_at_utc for contract in contracts if contract.last_trade_at_utc is not None)
    captured = max(row.captured_at_utc for row in snapshots)
    latest_trade = max(trades) if trades else None
    return YahooSymbolVolumePCR(
        symbol=symbol, value=value, status=status, reason=reason,
        call_volume=call_volume, put_volume=put_volume,
        expiry_count=len(snapshots), standard_contract_count=len(standard),
        excluded_nonstandard_count=len(contracts) - len(standard),
        captured_at_utc=captured, captured_at_kst=captured.astimezone(KST),
        latest_contract_trade_at_utc=latest_trade,
        latest_contract_trade_at_kst=latest_trade.astimezone(KST) if latest_trade else None,
    )


__all__ = [
    "ALL_PILOT_SYMBOLS", "CONDITIONAL_OPTION_SYMBOLS", "INITIAL_OPTION_SYMBOLS",
    "PRICE_ONLY_SYMBOLS", "SymbolOptionPCRStatus", "YahooOptionChainSnapshot",
    "YahooOptionContract", "YahooSymbolOptionError", "YahooSymbolVolumePCR",
    "derive_yahoo_symbol_volume_pcr", "parse_yahoo_option_chain",
]
