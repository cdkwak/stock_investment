from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from stock_data.contracts.manual_account_market_values import (
    ManualAccountMarketValueCache,
    ManualAccountMarketValueRow,
    ManualAccountSectionSummary,
    manual_account_basis_sha256,
    manual_account_market_value_cache_payload,
    parse_manual_account_market_value_cache,
)
from stock_data.gui.manual_account_snapshot import (
    ManualAccountSnapshot,
    validate_manual_account_snapshot,
)
from stock_data.providers.yahoo_account_prices import (
    YahooAccountPriceObservation,
    YahooAccountPriceSymbol,
    YahooAccountPriceUnavailable,
    normalize_yahoo_account_price,
    validate_yahoo_account_price_symbol,
)


Supplier = Callable[[tuple[YahooAccountPriceSymbol, ...]], Mapping[tuple[str, str], object]]


@dataclass(frozen=True, slots=True)
class ManualAccountMarketValueRefreshResult:
    status: str
    cache: ManualAccountMarketValueCache | None
    requested_symbols: int
    available_rows: int
    unavailable_rows: int
    reason: str | None = None


def load_manual_account_market_value_cache(
    path: Path,
) -> ManualAccountMarketValueCache | None:
    path = Path(path)
    if not path.is_file():
        return None
    return parse_manual_account_market_value_cache(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _atomic_cache_write(path: Path, cache: ManualAccountMarketValueCache) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        json.dumps(
            manual_account_market_value_cache_payload(cache), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validated_symbol_map(
    snapshot: ManualAccountSnapshot,
    symbol_map: Mapping[tuple[str, str], YahooAccountPriceSymbol],
) -> dict[tuple[str, str], YahooAccountPriceSymbol]:
    if not isinstance(symbol_map, Mapping):
        raise TypeError("explicit symbol map is required")
    holding_keys = {(row.section, row.ticker) for row in snapshot.holdings}
    if any(key not in holding_keys for key in symbol_map):
        raise ValueError("symbol map contains an identity outside the manual basis")
    validated: dict[tuple[str, str], YahooAccountPriceSymbol] = {}
    provider_symbols: dict[tuple[str, str], str] = {}
    for key, value in symbol_map.items():
        if (
            not isinstance(key, tuple) or len(key) != 2
            or not all(isinstance(part, str) for part in key)
        ):
            raise TypeError("symbol map key must be (section, ticker)")
        value = validate_yahoo_account_price_symbol(value)
        if key != (value.section, value.ticker):
            raise ValueError("symbol map key and value identity differ")
        provider_identity = (value.exchange, value.provider_symbol)
        prior_ticker = provider_symbols.get(provider_identity)
        if prior_ticker is not None and prior_ticker != value.ticker:
            raise ValueError("provider symbol maps to multiple local tickers")
        provider_symbols[provider_identity] = value.ticker
        validated[key] = value
    return validated


def _prior_cache(
    path: Path, *, basis_sha256: str,
) -> ManualAccountMarketValueCache | None:
    try:
        prior = load_manual_account_market_value_cache(path)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return prior if prior is not None and prior.basis_sha256 == basis_sha256 else None


def _rejected(
    prior: ManualAccountMarketValueCache | None,
    *, requested: int, reason: str,
) -> ManualAccountMarketValueRefreshResult:
    return ManualAccountMarketValueRefreshResult(
        "REJECTED_PRIOR_PRESERVED" if prior is not None else "REJECTED_NO_PRIOR",
        prior, requested, 0, 0, reason,
    )


def refresh_manual_account_market_values(
    snapshot: ManualAccountSnapshot,
    *,
    symbol_map: Mapping[tuple[str, str], YahooAccountPriceSymbol],
    supplier: Supplier,
    cache_path: Path,
    now: datetime | None = None,
) -> ManualAccountMarketValueRefreshResult:
    """Build an atomic cache from one injected supplier invocation.

    Transport is deliberately absent. An unsupported or explicitly unavailable
    symbol produces a numeric-free row. A malformed/raised supplier result
    rejects the whole refresh before persistence and leaves prior bytes intact.
    """

    snapshot = validate_manual_account_snapshot(snapshot)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("manual market-value clock must be timezone-aware")
    basis_digest = manual_account_basis_sha256(snapshot)
    prior = _prior_cache(Path(cache_path), basis_sha256=basis_digest)
    try:
        mapping = _validated_symbol_map(snapshot, symbol_map)
        requested = tuple(mapping[key] for key in sorted(mapping))
        if not callable(supplier):
            raise TypeError("injected price supplier must be callable")
        supplied = supplier(requested) if requested else {}
        if not isinstance(supplied, Mapping):
            raise TypeError("injected price supplier must return a mapping")
        expected_keys = set(mapping)
        if any(key not in expected_keys for key in supplied):
            raise ValueError("supplier returned an unrequested identity")

        pending: list[tuple[Any, YahooAccountPriceObservation | None, str | None]] = []
        for holding in snapshot.holdings:
            key = (holding.section, holding.ticker)
            specification = mapping.get(key)
            if specification is None:
                pending.append((holding, None, "EXPLICIT_SYMBOL_MAP_MISSING"))
                continue
            result = supplied.get(key)
            if result is None:
                pending.append((holding, None, "SUPPLIER_RESULT_MISSING"))
                continue
            if isinstance(result, YahooAccountPriceUnavailable):
                if (result.section, result.ticker) != key:
                    raise ValueError("unavailable supplier identity differs")
                pending.append((holding, None, result.reason))
                continue
            if isinstance(result, YahooAccountPriceObservation):
                result = {
                    "provider": result.provider,
                    "provider_symbol": result.provider_symbol,
                    "exchange": result.exchange, "currency": result.currency,
                    "unit": result.unit, "price": str(result.price),
                    "as_of": result.as_of, "captured_at": result.captured_at,
                    "finality": result.finality,
                }
            observation = normalize_yahoo_account_price(result, specification)
            if (
                (observation.section, observation.ticker) != key
                or observation.currency != snapshot.currency
            ):
                raise ValueError("accepted supplier observation identity differs")
            captured = datetime.fromisoformat(
                observation.captured_at.replace("Z", "+00:00")
            )
            if captured > clock.astimezone(captured.tzinfo):
                raise ValueError("supplier observation captured_at is in the future")
            pending.append((holding, observation, None))

        denominators: dict[tuple[str, str], Decimal] = {}
        for holding, observation, _reason in pending:
            if observation is not None:
                identity = (holding.section, observation.currency)
                denominators[identity] = denominators.get(identity, Decimal(0)) + (
                    Decimal(str(holding.quantity)) * observation.price
                )

        rows: list[ManualAccountMarketValueRow] = []
        for holding, observation, reason in pending:
            if observation is None:
                rows.append(ManualAccountMarketValueRow(
                    holding.section, holding.ticker, "UNAVAILABLE", snapshot.currency,
                    None, None, None, None, None, None, None, None,
                    None, None, None, None, reason,
                ))
                continue
            market_value = Decimal(str(holding.quantity)) * observation.price
            denominator = denominators[(holding.section, observation.currency)]
            weight = market_value / denominator * Decimal(100)
            purchase = (
                None if holding.purchase_total is None
                else Decimal(str(holding.purchase_total))
            )
            pnl = None if purchase is None else market_value - purchase
            return_pct = (
                None if purchase is None or purchase == 0
                else pnl / purchase * Decimal(100)
            )
            rows.append(ManualAccountMarketValueRow(
                holding.section, holding.ticker, "AVAILABLE", observation.currency,
                observation.provider_symbol, observation.provider,
                observation.exchange, observation.unit, observation.price,
                observation.as_of, observation.captured_at, observation.finality,
                market_value, weight, pnl, return_pct, None,
            ))

        summaries: list[ManualAccountSectionSummary] = []
        for section in dict.fromkeys(row.section for row in snapshot.holdings):
            for currency in dict.fromkeys(
                row.currency for row in rows if row.section == section
            ):
                matching = [
                    row for row in rows
                    if row.section == section and row.currency == currency
                ]
                available = [row for row in matching if row.status == "AVAILABLE"]
                summaries.append(ManualAccountSectionSummary(
                    section, currency, len(matching), len(available),
                    sum((row.market_value or Decimal(0) for row in available), Decimal(0)),
                    len(available) == len(matching),
                ))
        cache = ManualAccountMarketValueCache(
            snapshot.source_sheet, snapshot.snapshot_date, basis_digest,
            clock.isoformat(), tuple(rows), tuple(summaries),
        )
        cache = parse_manual_account_market_value_cache(
            manual_account_market_value_cache_payload(cache)
        )
    except Exception as error:
        return _rejected(
            prior, requested=len(symbol_map) if isinstance(symbol_map, Mapping) else 0,
            reason=type(error).__name__,
        )

    try:
        _atomic_cache_write(Path(cache_path), cache)
    except OSError as error:
        return _rejected(prior, requested=len(requested), reason=type(error).__name__)
    available_rows = sum(row.status == "AVAILABLE" for row in cache.rows)
    return ManualAccountMarketValueRefreshResult(
        "UPDATED", cache, len(requested), available_rows,
        len(cache.rows) - available_rows,
    )


__all__ = [
    "ManualAccountMarketValueRefreshResult", "Supplier",
    "load_manual_account_market_value_cache",
    "refresh_manual_account_market_values",
]
