"""Pure, fail-closed corporate-action factor selection and price adjustment.

The engine performs no I/O and grants no source or promotion authority.  It
accepts only explicitly verified event versions and provider-native unadjusted
OHLCV.  Its output is a separate derived view; callers retain the native input.
Cash distributions are markers only and total-return calculation is deliberately
out of scope.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Iterable, Sequence


class CorporateActionError(ValueError):
    """Corporate-action evidence is insufficient or contradictory."""


class ActionType(str, Enum):
    SPLIT = "SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    BONUS_ISSUE = "BONUS_ISSUE"
    RIGHTS_ISSUE = "RIGHTS_ISSUE"
    CAPITAL_REDUCTION = "CAPITAL_REDUCTION"
    CASH_DIVIDEND = "CASH_DIVIDEND"
    TICKER_CHANGE = "TICKER_CHANGE"
    MERGER = "MERGER"
    SPINOFF = "SPINOFF"
    DELISTING = "DELISTING"
    RELISTING = "RELISTING"


class RevisionState(str, Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"


VERIFIED_FINAL = "VERIFIED_FINAL"
PROVIDER_NATIVE_UNADJUSTED = "PROVIDER_NATIVE_UNADJUSTED"
FACTOR_CHAIN_VERSION = 1

_FACTOR_ACTIONS = {
    ActionType.SPLIT,
    ActionType.REVERSE_SPLIT,
    ActionType.BONUS_ISSUE,
    ActionType.RIGHTS_ISSUE,
    ActionType.CAPITAL_REDUCTION,
}
_IDENTITY_DISCONTINUITIES = {
    ActionType.MERGER,
    ActionType.SPINOFF,
    ActionType.DELISTING,
    ActionType.RELISTING,
}
_ONE = Decimal("1")


@dataclass(frozen=True)
class CorporateActionVersion:
    event_id: str
    event_version_id: str
    version_number: int
    revises_event_version_id: str | None
    revision_state: RevisionState
    security_id: str
    security_id_scheme: str
    symbol: str
    action_type: ActionType
    announcement_date: date | None
    ex_date: date | None
    effective_date: date
    same_date_sequence: int
    price_factor: Decimal | None
    volume_factor: Decimal | None
    turnover_factor: Decimal | None
    cash_amount: Decimal | None
    currency: str | None
    factor_method: str | None
    predecessor_security_id: str | None
    successor_security_id: str | None
    predecessor_symbol: str | None
    successor_symbol: str | None
    source: str
    source_event_id: str
    source_observation_id: str
    available_at_utc: datetime
    retrieved_at_utc: datetime
    finality: str
    source_filing_date: date | None = None
    decision_date: date | None = None
    record_date: date | None = None
    payment_date: date | None = None
    listing_date: date | None = None
    availability_basis: str = "SOURCE_TIMESTAMP"
    source_revision_indicator: str | None = None
    revision_parent_status: str = "NOT_APPLICABLE"


@dataclass(frozen=True)
class FactorStep:
    event_id: str
    event_version_id: str
    action_type: ActionType
    effective_date: date
    same_date_sequence: int
    price_factor: Decimal
    volume_factor: Decimal
    turnover_factor: Decimal
    factor_method: str
    source_observation_id: str


@dataclass(frozen=True)
class FactorChain:
    factor_chain_id: str
    factor_chain_version: int
    security_id: str
    as_of_knowledge_at_utc: datetime
    through_date: date
    selected_events: tuple[CorporateActionVersion, ...]
    steps: tuple[FactorStep, ...]
    discontinuity_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProviderNativePrice:
    market_date: date
    security_id: str
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal | None
    provider: str
    provider_adjustment_status: str = PROVIDER_NATIVE_UNADJUSTED


@dataclass(frozen=True)
class AdjustedPriceObservation:
    native: ProviderNativePrice
    adjusted_open: Decimal
    adjusted_high: Decimal
    adjusted_low: Decimal
    adjusted_close: Decimal
    adjusted_volume: Decimal
    adjusted_turnover: Decimal | None
    cumulative_price_factor: Decimal
    cumulative_volume_factor: Decimal
    cumulative_turnover_factor: Decimal
    factor_chain_id: str
    applied_event_version_ids: tuple[str, ...]
    adjustment_scope: str = "SPLIT_CAPITAL_ONLY_NOT_TOTAL_RETURN"


def _nonempty(value: str | None, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorporateActionError(f"{field} must be a non-empty string")
    return value


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CorporateActionError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _positive(value: Decimal | None, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise CorporateActionError(f"{field} must be a positive finite Decimal")
    return value


def _no_factors(event: CorporateActionVersion) -> None:
    if any(value is not None for value in (
        event.price_factor, event.volume_factor, event.turnover_factor
    )):
        raise CorporateActionError(f"{event.action_type.value} must not carry price factors")


def validate_event_version(event: CorporateActionVersion) -> None:
    """Validate one version without inferring a missing economic term."""
    if not isinstance(event.action_type, ActionType):
        raise CorporateActionError("action_type must be an ActionType")
    if not isinstance(event.revision_state, RevisionState):
        raise CorporateActionError("revision_state must be a RevisionState")
    for field in (
        "event_id", "event_version_id", "security_id", "security_id_scheme", "symbol",
        "source", "source_event_id", "source_observation_id",
    ):
        _nonempty(getattr(event, field), field)
    if isinstance(event.version_number, bool) or not isinstance(event.version_number, int) or event.version_number < 1:
        raise CorporateActionError("version_number must be a positive integer")
    if event.version_number == 1 and event.revises_event_version_id is not None:
        raise CorporateActionError("version 1 cannot revise an earlier version")
    if event.version_number > 1:
        _nonempty(event.revises_event_version_id, "revises_event_version_id")
    if (
        isinstance(event.same_date_sequence, bool)
        or not isinstance(event.same_date_sequence, int)
        or event.same_date_sequence < 0
    ):
        raise CorporateActionError("same_date_sequence must be a non-negative integer")
    if not isinstance(event.effective_date, date) or isinstance(event.effective_date, datetime):
        raise CorporateActionError("effective_date must be a date")
    for field in (
        "source_filing_date", "announcement_date", "decision_date", "record_date",
        "ex_date", "payment_date", "listing_date",
    ):
        value = getattr(event, field)
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise CorporateActionError(f"{field} must be a date or null")
    if event.availability_basis not in {
        "SOURCE_TIMESTAMP", "SOURCE_FILING_DATE_AND_CAPTURE_FLOOR",
        "CONSERVATIVE_CAPTURE_TIME",
    }:
        raise CorporateActionError("availability_basis is not an approved PIT rule")
    if event.version_number == 1:
        if event.revision_parent_status != "NOT_APPLICABLE":
            raise CorporateActionError("version 1 revision_parent_status must be NOT_APPLICABLE")
    elif event.revision_parent_status != "VERIFIED_EXPLICIT":
        raise CorporateActionError("revised version requires an explicit verified parent edge")
    available = _aware_utc(event.available_at_utc, "available_at_utc")
    retrieved = _aware_utc(event.retrieved_at_utc, "retrieved_at_utc")
    if retrieved < available:
        raise CorporateActionError("retrieved_at_utc precedes available_at_utc")
    if event.action_type in _FACTOR_ACTIONS:
        price = _positive(event.price_factor, "price_factor")
        volume = _positive(event.volume_factor, "volume_factor")
        turnover = _positive(event.turnover_factor, "turnover_factor")
        _nonempty(event.factor_method, "factor_method")
        if event.ex_date is None or event.ex_date != event.effective_date:
            raise CorporateActionError(
                "factor action requires one verified ex_date equal to effective_date"
            )
        if event.cash_amount is not None or event.currency is not None:
            raise CorporateActionError("split/capital factors must not embed cash distributions")
        if event.action_type in {
            ActionType.SPLIT, ActionType.REVERSE_SPLIT, ActionType.BONUS_ISSUE,
        }:
            if price * volume != _ONE or turnover != _ONE:
                raise CorporateActionError("share-count actions require reciprocal price/volume and identity turnover")
            if event.action_type in {ActionType.SPLIT, ActionType.BONUS_ISSUE} and volume <= _ONE:
                raise CorporateActionError("split/bonus volume_factor must exceed one")
            if event.action_type is ActionType.REVERSE_SPLIT and volume >= _ONE:
                raise CorporateActionError("reverse-split volume_factor must be below one")
    elif event.action_type is ActionType.CASH_DIVIDEND:
        _no_factors(event)
        _positive(event.cash_amount, "cash_amount")
        _nonempty(event.currency, "currency")
        if event.ex_date is None:
            raise CorporateActionError("cash dividend requires a verified ex_date")
    elif event.action_type is ActionType.TICKER_CHANGE:
        _no_factors(event)
        previous = _nonempty(event.predecessor_symbol, "predecessor_symbol")
        successor = _nonempty(event.successor_symbol, "successor_symbol")
        if previous == successor:
            raise CorporateActionError("ticker change requires distinct symbols")
        if any(value is not None for value in (event.cash_amount, event.currency)):
            raise CorporateActionError("ticker change must not embed a cash distribution")
    elif event.action_type in _IDENTITY_DISCONTINUITIES:
        _no_factors(event)
        if event.action_type is ActionType.DELISTING:
            _nonempty(event.predecessor_security_id or event.security_id, "predecessor_security_id")
        else:
            predecessor = _nonempty(event.predecessor_security_id, "predecessor_security_id")
            successor = _nonempty(event.successor_security_id, "successor_security_id")
            if predecessor == successor:
                raise CorporateActionError("identity discontinuity requires distinct security identifiers")
    else:  # pragma: no cover - Enum exhaustiveness guard
        raise CorporateActionError("unsupported action type")


def _validate_version_chain(versions: list[CorporateActionVersion]) -> None:
    ordered = sorted(versions, key=lambda event: event.version_number)
    if [event.version_number for event in ordered] != list(range(1, len(ordered) + 1)):
        raise CorporateActionError("event version numbers must be contiguous from one")
    first = ordered[0]
    stable = (first.security_id, first.security_id_scheme, first.action_type, first.source_event_id)
    previous: CorporateActionVersion | None = None
    previous_available: datetime | None = None
    ids: set[str] = set()
    for event in ordered:
        validate_event_version(event)
        if event.event_version_id in ids:
            raise CorporateActionError("duplicate event_version_id")
        ids.add(event.event_version_id)
        if (event.security_id, event.security_id_scheme, event.action_type, event.source_event_id) != stable:
            raise CorporateActionError("revision changes stable event identity or action type")
        available = _aware_utc(event.available_at_utc, "available_at_utc")
        if previous is not None:
            if event.revises_event_version_id != previous.event_version_id:
                raise CorporateActionError("revision lineage does not point to the preceding version")
            if previous_available is not None and available <= previous_available:
                raise CorporateActionError("revision availability must increase strictly")
        previous = event
        previous_available = available


def build_factor_chain(
    events: Iterable[CorporateActionVersion],
    *,
    security_id: str,
    as_of_knowledge_at_utc: datetime,
    through_date: date,
) -> FactorChain:
    """Select the version known at ``as_of`` and build a deterministic chain."""
    _nonempty(security_id, "security_id")
    as_of = _aware_utc(as_of_knowledge_at_utc, "as_of_knowledge_at_utc")
    if not isinstance(through_date, date):
        raise CorporateActionError("through_date must be a date")
    grouped: dict[str, list[CorporateActionVersion]] = {}
    version_ids: set[str] = set()
    for event in events:
        if not isinstance(event, CorporateActionVersion):
            raise CorporateActionError("events must contain CorporateActionVersion values")
        if event.security_id != security_id:
            raise CorporateActionError("factor chain cannot mix security identities")
        if event.event_version_id in version_ids:
            raise CorporateActionError("duplicate event_version_id")
        version_ids.add(event.event_version_id)
        grouped.setdefault(event.event_id, []).append(event)

    selected: list[CorporateActionVersion] = []
    for versions in grouped.values():
        _validate_version_chain(versions)
        eligible = [
            event for event in versions
            if _aware_utc(event.available_at_utc, "available_at_utc") <= as_of
        ]
        if not eligible:
            continue
        latest = max(eligible, key=lambda event: event.version_number)
        if latest.finality != VERIFIED_FINAL:
            raise CorporateActionError("latest known event version is not verified final")
        if latest.revision_state is RevisionState.CANCELLED:
            continue
        if latest.effective_date <= through_date:
            selected.append(latest)

    selected.sort(key=lambda event: (
        event.effective_date, event.same_date_sequence, event.event_id, event.event_version_id
    ))
    positions: set[tuple[date, int]] = set()
    for event in selected:
        position = (event.effective_date, event.same_date_sequence)
        if position in positions:
            raise CorporateActionError("same-date actions require unique explicit sequence values")
        positions.add(position)

    steps = tuple(
        FactorStep(
            event_id=event.event_id,
            event_version_id=event.event_version_id,
            action_type=event.action_type,
            effective_date=event.effective_date,
            same_date_sequence=event.same_date_sequence,
            price_factor=_positive(event.price_factor, "price_factor"),
            volume_factor=_positive(event.volume_factor, "volume_factor"),
            turnover_factor=_positive(event.turnover_factor, "turnover_factor"),
            factor_method=_nonempty(event.factor_method, "factor_method"),
            source_observation_id=event.source_observation_id,
        )
        for event in selected if event.action_type in _FACTOR_ACTIONS
    )
    discontinuities = tuple(
        event.event_id for event in selected if event.action_type in _IDENTITY_DISCONTINUITIES
    )
    digest_payload = {
        "factor_chain_version": FACTOR_CHAIN_VERSION,
        "security_id": security_id,
        "as_of_knowledge_at_utc": as_of.isoformat(),
        "through_date": through_date.isoformat(),
        "selected_events": [
            {
                key: (value.value if isinstance(value, Enum) else value.isoformat()
                      if isinstance(value, (date, datetime)) else str(value)
                      if isinstance(value, Decimal) else value)
                for key, value in asdict(event).items()
            }
            for event in selected
        ],
    }
    factor_chain_id = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return FactorChain(
        factor_chain_id=factor_chain_id,
        factor_chain_version=FACTOR_CHAIN_VERSION,
        security_id=security_id,
        as_of_knowledge_at_utc=as_of,
        through_date=through_date,
        selected_events=tuple(selected),
        steps=steps,
        discontinuity_event_ids=discontinuities,
    )


def adjust_provider_native_prices(
    native_prices: Sequence[ProviderNativePrice], factor_chain: FactorChain,
) -> tuple[AdjustedPriceObservation, ...]:
    """Return a separate split/capital-adjusted view without mutating inputs."""
    if factor_chain.discontinuity_event_ids:
        raise CorporateActionError("identity discontinuity forbids a continuous adjusted series")
    if not native_prices:
        raise CorporateActionError("native price input must not be empty")
    dates: set[date] = set()
    ordered = sorted(native_prices, key=lambda row: row.market_date)
    output: list[AdjustedPriceObservation] = []
    for row in ordered:
        if row.security_id != factor_chain.security_id:
            raise CorporateActionError("native price security differs from factor chain")
        if row.provider_adjustment_status != PROVIDER_NATIVE_UNADJUSTED:
            raise CorporateActionError("provider-pre-adjusted or unknown input would double-adjust")
        if row.market_date in dates:
            raise CorporateActionError("duplicate native price date")
        if row.market_date > factor_chain.through_date:
            raise CorporateActionError("native price exceeds factor-chain through_date")
        dates.add(row.market_date)
        values = (row.open, row.high, row.low, row.close, row.volume)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
            raise CorporateActionError("native OHLCV must use finite Decimal values")
        if min(row.open, row.high, row.low, row.close) <= 0 or row.volume < 0:
            raise CorporateActionError("native price must be positive and volume non-negative")
        if row.high < max(row.open, row.close) or row.low > min(row.open, row.close):
            raise CorporateActionError("native OHLC bounds are invalid")
        if row.turnover is not None and (
            not isinstance(row.turnover, Decimal) or not row.turnover.is_finite() or row.turnover < 0
        ):
            raise CorporateActionError("native turnover must be a non-negative finite Decimal")

        applicable = tuple(step for step in factor_chain.steps if row.market_date < step.effective_date)
        price_factor = _ONE
        volume_factor = _ONE
        turnover_factor = _ONE
        for step in applicable:
            price_factor *= step.price_factor
            volume_factor *= step.volume_factor
            turnover_factor *= step.turnover_factor
        output.append(AdjustedPriceObservation(
            native=row,
            adjusted_open=row.open * price_factor,
            adjusted_high=row.high * price_factor,
            adjusted_low=row.low * price_factor,
            adjusted_close=row.close * price_factor,
            adjusted_volume=row.volume * volume_factor,
            adjusted_turnover=(None if row.turnover is None else row.turnover * turnover_factor),
            cumulative_price_factor=price_factor,
            cumulative_volume_factor=volume_factor,
            cumulative_turnover_factor=turnover_factor,
            factor_chain_id=factor_chain.factor_chain_id,
            applied_event_version_ids=tuple(step.event_version_id for step in applicable),
        ))
    return tuple(output)


__all__ = [
    "ActionType", "AdjustedPriceObservation", "CorporateActionError",
    "CorporateActionVersion", "FACTOR_CHAIN_VERSION", "FactorChain", "FactorStep",
    "PROVIDER_NATIVE_UNADJUSTED", "ProviderNativePrice", "RevisionState",
    "VERIFIED_FINAL", "adjust_provider_native_prices", "build_factor_chain",
    "validate_event_version",
]
