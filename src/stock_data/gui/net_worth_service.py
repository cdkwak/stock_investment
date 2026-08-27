"""Typed local-only personal balance-sheet backend.

This module has no provider, credential, account-session, or GUI dependency.
It accepts explicit dated KRW snapshots, calculates only user-attributed claims,
and stores immutable atomic history records.  Missing or stale inputs never
borrow a value from an earlier record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable, Iterable, Mapping
from uuid import uuid4

from stock_data.orchestration.account_privacy import redact_account_text
from stock_data.orchestration.recovery_supervisor import OperationScopeLock


SCHEMA_VERSION = "local-net-worth-snapshot/v1"
HISTORY_VERSION = "local-net-worth-history/v1"
BASE_CURRENCY = "KRW"


class NetWorthValidationError(ValueError):
    """Value-free validation failure safe for local UI diagnostics."""


class NetWorthPersistenceError(RuntimeError):
    """Value-free persistence failure safe for local UI diagnostics."""


class AssetClass(StrEnum):
    CASH = "CASH"
    INVESTMENT = "INVESTMENT"
    REAL_ESTATE = "REAL_ESTATE"
    JEONSE_DEPOSIT = "JEONSE_DEPOSIT"
    OTHER_RECEIVABLE = "OTHER_RECEIVABLE"


class LiabilityClass(StrEnum):
    MORTGAGE = "MORTGAGE"
    JEONSE_LOAN = "JEONSE_LOAN"
    DRAWN_OVERDRAFT = "DRAWN_OVERDRAFT"
    OTHER_DEBT = "OTHER_DEBT"


class HolderRole(StrEnum):
    SELF = "SELF"
    SPOUSE = "SPOUSE"
    FAMILY = "FAMILY"
    JOINT = "JOINT"
    OTHER_DECLARED = "OTHER_DECLARED"


class ValuationStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    MISSING = "MISSING"


class ValuationMethod(StrEnum):
    USER_DECLARED = "USER_DECLARED"
    STATEMENT_VALUE = "STATEMENT_VALUE"
    MARKET_VALUE = "MARKET_VALUE"
    APPRAISAL = "APPRAISAL"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class ValuationSource(StrEnum):
    USER_LOCAL = "USER_LOCAL"
    BROKER_LOCAL_SNAPSHOT = "BROKER_LOCAL_SNAPSHOT"
    OFFICIAL_STATEMENT = "OFFICIAL_STATEMENT"
    APPROVED_LOCAL_SOURCE = "APPROVED_LOCAL_SOURCE"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class ValuationUncertainty(StrEnum):
    EXACT = "EXACT"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class AssetEntry:
    record_id: str
    economic_claim_id: str
    asset_class: AssetClass
    gross_value_krw: int | None
    economic_value_krw: int | None
    registered_holder_role: HolderRole
    economic_owner_role: HolderRole
    valuation_date: date | None
    valuation_method: ValuationMethod
    valuation_source: ValuationSource
    valuation_status: ValuationStatus
    uncertainty: ValuationUncertainty


@dataclass(frozen=True, slots=True)
class LiabilityEntry:
    record_id: str
    economic_claim_id: str
    liability_class: LiabilityClass
    gross_principal_krw: int | None
    economic_principal_krw: int | None
    unused_limit_krw: int
    registered_holder_role: HolderRole
    economic_owner_role: HolderRole
    valuation_date: date | None
    valuation_method: ValuationMethod
    valuation_source: ValuationSource
    valuation_status: ValuationStatus
    uncertainty: ValuationUncertainty


@dataclass(frozen=True, slots=True)
class NetWorthSnapshot:
    snapshot_id: str
    as_of_date: date
    recorded_at_utc: datetime
    base_currency: str
    assets: tuple[AssetEntry, ...]
    liabilities: tuple[LiabilityEntry, ...]


@dataclass(frozen=True, slots=True)
class NetWorthTotals:
    liquid_financial_assets_krw: int | None
    total_assets_krw: int | None
    total_liabilities_krw: int | None
    net_worth_krw: int | None
    unused_credit_limit_krw: int
    complete: bool
    stale_claim_ids: tuple[str, ...]
    missing_claim_ids: tuple[str, ...]
    uncertain_claim_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NetWorthView:
    snapshot: NetWorthSnapshot
    totals: NetWorthTotals


@dataclass(frozen=True, slots=True)
class NetWorthHistoryRecord:
    saved_at_utc: datetime
    snapshot_digest: str
    previous_record_digest: str | None
    record_digest: str
    view: NetWorthView


class NetWorthTimelineDisplayState(StrEnum):
    DISPLAYABLE = "DISPLAYABLE"
    GAP = "GAP"


class NetWorthTimelineDeltaState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class NetWorthTimelinePoint:
    """Identifier-free display projection for one exact snapshot date."""

    as_of_date: date
    base_currency: str
    display_state: NetWorthTimelineDisplayState
    display_reason: str
    net_worth_krw: int | None
    delta_state: NetWorthTimelineDeltaState
    delta_reason: str
    delta_from_previous_complete_krw: int | None
    previous_complete_date: date | None


@dataclass(frozen=True, slots=True)
class NetWorthTimelineView:
    """Chronological, immutable projection of latest exact-date revisions."""

    points: tuple[NetWorthTimelinePoint, ...]


_SNAPSHOT_KEYS = {
    "schema_version",
    "snapshot_id",
    "as_of_date",
    "recorded_at_utc",
    "base_currency",
    "assets",
    "liabilities",
}
_ASSET_KEYS = {
    "record_id",
    "economic_claim_id",
    "asset_class",
    "gross_value_krw",
    "economic_value_krw",
    "registered_holder_role",
    "economic_owner_role",
    "valuation_date",
    "valuation_method",
    "valuation_source",
    "valuation_status",
    "uncertainty",
}
_LIABILITY_KEYS = {
    "record_id",
    "economic_claim_id",
    "liability_class",
    "gross_principal_krw",
    "economic_principal_krw",
    "unused_limit_krw",
    "registered_holder_role",
    "economic_owner_role",
    "valuation_date",
    "valuation_method",
    "valuation_source",
    "valuation_status",
    "uncertainty",
}
_HISTORY_KEYS = {
    "history_version",
    "saved_at_utc",
    "snapshot",
    "totals",
    "snapshot_digest",
    "previous_record_digest",
    "record_digest",
}
_SAFE_ID = re.compile(r"[a-z][a-z0-9_-]{2,63}\Z")
_PRIVATE_ID_TOKEN = re.compile(
    r"(?i)(account|address|resident|registration|phone|email|holder_name|owner_name)"
)
_FORBIDDEN_KEY = re.compile(
    r"(?i)(address|account(?:_?(?:id|no|number|seq))?|resident|registration|"
    r"phone|email|ssn|credential|token|password|secret|holder_name|owner_name)"
)
_LIQUID_CLASSES = frozenset({AssetClass.CASH, AssetClass.INVESTMENT})


def parse_snapshot(payload: Mapping[str, object]) -> NetWorthSnapshot:
    """Parse a strict identifier-free local snapshot mapping."""

    _reject_sensitive(payload)
    if set(payload) != _SNAPSHOT_KEYS:
        raise NetWorthValidationError("NET_WORTH_SCHEMA_REJECTED")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise NetWorthValidationError("NET_WORTH_SCHEMA_REJECTED")
    snapshot_id = _safe_id(payload.get("snapshot_id"))
    as_of = _date(payload.get("as_of_date"))
    recorded = _utc_datetime(payload.get("recorded_at_utc"))
    if payload.get("base_currency") != BASE_CURRENCY:
        raise NetWorthValidationError("NET_WORTH_CURRENCY_REJECTED")
    raw_assets = payload.get("assets")
    raw_liabilities = payload.get("liabilities")
    if not isinstance(raw_assets, list) or not isinstance(raw_liabilities, list):
        raise NetWorthValidationError("NET_WORTH_SCHEMA_REJECTED")
    assets = tuple(_parse_asset(item, as_of=as_of) for item in raw_assets)
    liabilities = tuple(
        _parse_liability(item, as_of=as_of) for item in raw_liabilities
    )
    record_ids = [entry.record_id for entry in (*assets, *liabilities)]
    claim_ids = [entry.economic_claim_id for entry in (*assets, *liabilities)]
    if len(record_ids) != len(set(record_ids)):
        raise NetWorthValidationError("NET_WORTH_DUPLICATE_RECORD_REJECTED")
    if len(claim_ids) != len(set(claim_ids)):
        raise NetWorthValidationError("NET_WORTH_DOUBLE_COUNT_REJECTED")
    return NetWorthSnapshot(
        snapshot_id=snapshot_id,
        as_of_date=as_of,
        recorded_at_utc=recorded,
        base_currency=BASE_CURRENCY,
        assets=assets,
        liabilities=liabilities,
    )


def calculate_net_worth(snapshot: NetWorthSnapshot) -> NetWorthTotals:
    """Calculate exact attributed totals; stale/missing dependencies stay null."""

    stale = tuple(
        entry.economic_claim_id
        for entry in (*snapshot.assets, *snapshot.liabilities)
        if entry.valuation_status is ValuationStatus.STALE
    )
    missing = tuple(
        entry.economic_claim_id
        for entry in (*snapshot.assets, *snapshot.liabilities)
        if entry.valuation_status is ValuationStatus.MISSING
    )
    uncertain = tuple(
        entry.economic_claim_id
        for entry in (*snapshot.assets, *snapshot.liabilities)
        if entry.uncertainty is not ValuationUncertainty.EXACT
    )

    assets_complete = all(
        entry.valuation_status is ValuationStatus.CURRENT for entry in snapshot.assets
    )
    liabilities_complete = all(
        entry.valuation_status is ValuationStatus.CURRENT
        for entry in snapshot.liabilities
    )
    liquid_entries = [
        entry for entry in snapshot.assets if entry.asset_class in _LIQUID_CLASSES
    ]
    liquid_complete = all(
        entry.valuation_status is ValuationStatus.CURRENT for entry in liquid_entries
    )
    liquid = (
        sum(_present_asset(entry) for entry in liquid_entries)
        if liquid_complete
        else None
    )
    total_assets = (
        sum(_present_asset(entry) for entry in snapshot.assets)
        if assets_complete
        else None
    )
    total_liabilities = (
        sum(_present_liability(entry) for entry in snapshot.liabilities)
        if liabilities_complete
        else None
    )
    net_worth = (
        total_assets - total_liabilities
        if total_assets is not None and total_liabilities is not None
        else None
    )
    return NetWorthTotals(
        liquid_financial_assets_krw=liquid,
        total_assets_krw=total_assets,
        total_liabilities_krw=total_liabilities,
        net_worth_krw=net_worth,
        unused_credit_limit_krw=sum(
            entry.unused_limit_krw for entry in snapshot.liabilities
        ),
        complete=assets_complete and liabilities_complete,
        stale_claim_ids=stale,
        missing_claim_ids=missing,
        uncertain_claim_ids=uncertain,
    )


def build_net_worth_timeline(
    records: Iterable[NetWorthHistoryRecord],
) -> NetWorthTimelineView:
    """Project latest exact-date revisions without filling unavailable values.

    Revision selection is independent of input order: the latest aware UTC
    ``saved_at_utc`` wins, with ``record_digest`` as a deterministic tie-break.
    Gaps remain in the chronological result and do not replace their value with
    an earlier snapshot. A later displayable point may compare only with the
    immediately previous displayable complete date of the same currency.
    """

    selected: dict[date, NetWorthHistoryRecord] = {}
    for record in records:
        if type(record) is not NetWorthHistoryRecord:
            raise NetWorthValidationError("NET_WORTH_TIMELINE_INPUT_REJECTED")
        as_of = record.view.snapshot.as_of_date
        if not isinstance(as_of, date):
            raise NetWorthValidationError("NET_WORTH_TIMELINE_INPUT_REJECTED")
        current = selected.get(as_of)
        if current is None or _timeline_revision_key(record) > _timeline_revision_key(
            current
        ):
            selected[as_of] = record

    points: list[NetWorthTimelinePoint] = []
    previous_complete: tuple[date, str, int | None] | None = None
    for as_of in sorted(selected):
        record = selected[as_of]
        state, reason, net_worth = _timeline_display(record)
        currency = record.view.snapshot.base_currency
        if state is not NetWorthTimelineDisplayState.DISPLAYABLE:
            delta_reason = (
                "CURRENCY_MISMATCH"
                if reason == "CURRENCY_MISMATCH"
                else "POINT_NOT_DISPLAYABLE"
            )
            points.append(
                NetWorthTimelinePoint(
                    as_of_date=as_of,
                    base_currency=currency,
                    display_state=state,
                    display_reason=reason,
                    net_worth_krw=None,
                    delta_state=NetWorthTimelineDeltaState.UNAVAILABLE,
                    delta_reason=delta_reason,
                    delta_from_previous_complete_krw=None,
                    previous_complete_date=None,
                )
            )
            if reason == "CURRENCY_MISMATCH":
                # Preserve the immediately previous complete date as a
                # cross-currency barrier without exposing or using its value.
                previous_complete = (as_of, currency, None)
            continue

        assert net_worth is not None
        if previous_complete is None:
            delta_state = NetWorthTimelineDeltaState.UNAVAILABLE
            delta_reason = "NO_PREVIOUS_COMPLETE"
            delta = None
            previous_date = None
        else:
            previous_date, previous_currency, previous_value = previous_complete
            if previous_currency != currency or previous_value is None:
                delta_state = NetWorthTimelineDeltaState.UNAVAILABLE
                delta_reason = "CURRENCY_MISMATCH"
                delta = None
            else:
                delta_state = NetWorthTimelineDeltaState.AVAILABLE
                delta_reason = "AVAILABLE"
                delta = net_worth - previous_value
        points.append(
            NetWorthTimelinePoint(
                as_of_date=as_of,
                base_currency=currency,
                display_state=state,
                display_reason=reason,
                net_worth_krw=net_worth,
                delta_state=delta_state,
                delta_reason=delta_reason,
                delta_from_previous_complete_krw=delta,
                previous_complete_date=previous_date,
            )
        )
        previous_complete = (as_of, currency, net_worth)
    return NetWorthTimelineView(points=tuple(points))


def _timeline_revision_key(record: NetWorthHistoryRecord) -> tuple[datetime, str]:
    saved_at = record.saved_at_utc
    if (
        not isinstance(saved_at, datetime)
        or saved_at.tzinfo is None
        or saved_at.utcoffset() is None
    ):
        raise NetWorthValidationError("NET_WORTH_TIMELINE_INPUT_REJECTED")
    if not isinstance(record.record_digest, str):
        raise NetWorthValidationError("NET_WORTH_TIMELINE_INPUT_REJECTED")
    return saved_at.astimezone(timezone.utc), record.record_digest


def _timeline_display(
    record: NetWorthHistoryRecord,
) -> tuple[
    NetWorthTimelineDisplayState,
    str,
    int | None,
]:
    snapshot = record.view.snapshot
    try:
        calculated = calculate_net_worth(snapshot)
    except (AttributeError, TypeError, NetWorthValidationError):
        return (
            NetWorthTimelineDisplayState.GAP,
            "SNAPSHOT_INVALID",
            None,
        )
    if calculated != record.view.totals:
        return (
            NetWorthTimelineDisplayState.GAP,
            "SNAPSHOT_INVALID",
            None,
        )
    if not calculated.complete or calculated.net_worth_krw is None:
        return (
            NetWorthTimelineDisplayState.GAP,
            "SNAPSHOT_INCOMPLETE",
            None,
        )
    if snapshot.base_currency != BASE_CURRENCY:
        return (
            NetWorthTimelineDisplayState.GAP,
            "CURRENCY_MISMATCH",
            None,
        )
    return (
        NetWorthTimelineDisplayState.DISPLAYABLE,
        "COMPLETE",
        calculated.net_worth_krw,
    )


class LocalNetWorthHistoryStore:
    """Append-only, hash-chained, atomic local history for explicit snapshots."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.root = Path(root)
        self._clock = clock

    def save_snapshot(self, payload: Mapping[str, object]) -> NetWorthHistoryRecord:
        snapshot = parse_snapshot(payload)
        view = NetWorthView(snapshot, calculate_net_worth(snapshot))
        self._validate_root()
        with OperationScopeLock(
            self.root,
            operation="local_net_worth_history",
            datasets=("personal_net_worth",),
            run_id=uuid4().hex,
            clock=self._clock,
        ):
            history = self.load_history()
            snapshot_payload = _snapshot_payload(snapshot)
            snapshot_digest = _digest(snapshot_payload)
            for record in history:
                if record.snapshot_digest == snapshot_digest:
                    return record
            saved_at = _utc_datetime(self._clock())
            previous = history[-1].record_digest if history else None
            core = {
                "history_version": HISTORY_VERSION,
                "saved_at_utc": saved_at.isoformat(),
                "snapshot": snapshot_payload,
                "totals": _totals_payload(view.totals),
                "snapshot_digest": snapshot_digest,
                "previous_record_digest": previous,
            }
            record_digest = _digest(core)
            record_payload = {**core, "record_digest": record_digest}
            stamp = saved_at.strftime("%Y%m%dT%H%M%S%fZ")
            target = self.root / (
                f"record-{len(history):08d}-{stamp}-{snapshot.snapshot_id}-"
                f"{record_digest[:12]}.json"
            )
            _atomic_json_new(target, record_payload)
            return NetWorthHistoryRecord(
                saved_at_utc=saved_at,
                snapshot_digest=snapshot_digest,
                previous_record_digest=previous,
                record_digest=record_digest,
                view=view,
            )

    def load_history(self) -> tuple[NetWorthHistoryRecord, ...]:
        self._validate_root()
        records: list[NetWorthHistoryRecord] = []
        previous: str | None = None
        try:
            paths = sorted(self.root.glob("record-*.json")) if self.root.exists() else []
            for path in paths:
                if path.is_symlink() or path.resolve().parent != self.root.resolve():
                    raise NetWorthPersistenceError("NET_WORTH_HISTORY_SCOPE_REJECTED")
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or set(payload) != _HISTORY_KEYS:
                    raise NetWorthPersistenceError("NET_WORTH_HISTORY_INVALID")
                record_digest = str(payload["record_digest"])
                core = {key: payload[key] for key in _HISTORY_KEYS - {"record_digest"}}
                if _digest(core) != record_digest:
                    raise NetWorthPersistenceError("NET_WORTH_HISTORY_INVALID")
                if payload["previous_record_digest"] != previous:
                    raise NetWorthPersistenceError("NET_WORTH_HISTORY_CHAIN_INVALID")
                snapshot_mapping = payload["snapshot"]
                if not isinstance(snapshot_mapping, dict):
                    raise NetWorthPersistenceError("NET_WORTH_HISTORY_INVALID")
                snapshot = parse_snapshot(snapshot_mapping)
                view = NetWorthView(snapshot, calculate_net_worth(snapshot))
                if (
                    _digest(snapshot_mapping) != payload["snapshot_digest"]
                    or _totals_payload(view.totals) != payload["totals"]
                ):
                    raise NetWorthPersistenceError("NET_WORTH_HISTORY_INVALID")
                saved_at = _utc_datetime(payload["saved_at_utc"])
                records.append(
                    NetWorthHistoryRecord(
                        saved_at_utc=saved_at,
                        snapshot_digest=str(payload["snapshot_digest"]),
                        previous_record_digest=(
                            str(previous) if previous is not None else None
                        ),
                        record_digest=record_digest,
                        view=view,
                    )
                )
                previous = record_digest
        except NetWorthPersistenceError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            raise NetWorthPersistenceError("NET_WORTH_HISTORY_INVALID") from None
        return tuple(records)

    def load_exact(self, as_of_date: date) -> NetWorthView | None:
        """Return only the latest revision for the exact date; never prior data."""

        candidates = [
            record
            for record in self.load_history()
            if record.view.snapshot.as_of_date == as_of_date
        ]
        return candidates[-1].view if candidates else None

    def remove_exact_date(self, as_of_date: date) -> int:
        """Remove only trailing records for one exact date.

        A dated snapshot can have multiple immutable revisions.  They are
        removed newest-first so any interrupted operation leaves a valid chain
        that can be retried.  Removing a date below newer history is rejected
        rather than rewriting or corrupting unrelated audit records.
        """

        if not isinstance(as_of_date, date):
            raise NetWorthPersistenceError("NET_WORTH_REMOVAL_DATE_REJECTED")
        self._validate_root()
        with OperationScopeLock(
            self.root,
            operation="local_net_worth_history_remove_exact",
            datasets=("personal_net_worth",),
            run_id=uuid4().hex,
            clock=self._clock,
        ):
            history = self.load_history()
            indices = [
                index
                for index, record in enumerate(history)
                if record.view.snapshot.as_of_date == as_of_date
            ]
            if not indices:
                return 0
            first = indices[0]
            if any(
                record.view.snapshot.as_of_date != as_of_date
                for record in history[first:]
            ):
                raise NetWorthPersistenceError(
                    "NET_WORTH_REMOVAL_NON_TAIL_REJECTED"
                )
            try:
                paths = sorted(self.root.glob("record-*.json"))
                if len(paths) != len(history):
                    raise NetWorthPersistenceError(
                        "NET_WORTH_HISTORY_INVALID"
                    )
                targets = paths[first:]
                root = self.root.resolve()
                for path in targets:
                    if path.is_symlink() or path.resolve().parent != root:
                        raise NetWorthPersistenceError(
                            "NET_WORTH_HISTORY_SCOPE_REJECTED"
                        )
                removed = 0
                for path in reversed(targets):
                    path.unlink()
                    removed += 1
            except NetWorthPersistenceError:
                raise
            except OSError:
                raise NetWorthPersistenceError(
                    "NET_WORTH_REMOVAL_INCOMPLETE"
                ) from None
            return removed

    def _validate_root(self) -> None:
        if self.root.exists() and (not self.root.is_dir() or self.root.is_symlink()):
            raise NetWorthPersistenceError("NET_WORTH_HISTORY_SCOPE_REJECTED")


def _parse_asset(value: object, *, as_of: date) -> AssetEntry:
    item = _strict_item(value, _ASSET_KEYS)
    common = _common_entry(item, as_of=as_of, gross_key="gross_value_krw", economic_key="economic_value_krw")
    return AssetEntry(
        record_id=common["record_id"],
        economic_claim_id=common["economic_claim_id"],
        asset_class=_enum(AssetClass, item["asset_class"]),
        gross_value_krw=common["gross"],
        economic_value_krw=common["economic"],
        registered_holder_role=common["registered_holder_role"],
        economic_owner_role=common["economic_owner_role"],
        valuation_date=common["valuation_date"],
        valuation_method=common["valuation_method"],
        valuation_source=common["valuation_source"],
        valuation_status=common["valuation_status"],
        uncertainty=common["uncertainty"],
    )


def _parse_liability(value: object, *, as_of: date) -> LiabilityEntry:
    item = _strict_item(value, _LIABILITY_KEYS)
    common = _common_entry(
        item,
        as_of=as_of,
        gross_key="gross_principal_krw",
        economic_key="economic_principal_krw",
    )
    liability_class = _enum(LiabilityClass, item["liability_class"])
    unused = _money(item["unused_limit_krw"], nullable=False)
    assert unused is not None
    if unused and liability_class is not LiabilityClass.DRAWN_OVERDRAFT:
        raise NetWorthValidationError("NET_WORTH_UNUSED_LIMIT_CLASS_REJECTED")
    return LiabilityEntry(
        record_id=common["record_id"],
        economic_claim_id=common["economic_claim_id"],
        liability_class=liability_class,
        gross_principal_krw=common["gross"],
        economic_principal_krw=common["economic"],
        unused_limit_krw=unused,
        registered_holder_role=common["registered_holder_role"],
        economic_owner_role=common["economic_owner_role"],
        valuation_date=common["valuation_date"],
        valuation_method=common["valuation_method"],
        valuation_source=common["valuation_source"],
        valuation_status=common["valuation_status"],
        uncertainty=common["uncertainty"],
    )


def _common_entry(
    item: Mapping[str, object],
    *,
    as_of: date,
    gross_key: str,
    economic_key: str,
) -> dict[str, object]:
    status = _enum(ValuationStatus, item["valuation_status"])
    gross = _money(item[gross_key], nullable=True)
    economic = _money(item[economic_key], nullable=True)
    valuation_date = (
        None if item["valuation_date"] is None else _date(item["valuation_date"])
    )
    method = _enum(ValuationMethod, item["valuation_method"])
    source = _enum(ValuationSource, item["valuation_source"])
    uncertainty = _enum(ValuationUncertainty, item["uncertainty"])
    if status is ValuationStatus.MISSING:
        if (
            gross is not None
            or economic is not None
            or valuation_date is not None
            or method is not ValuationMethod.NOT_AVAILABLE
            or source is not ValuationSource.NOT_AVAILABLE
            or uncertainty is not ValuationUncertainty.UNKNOWN
        ):
            raise NetWorthValidationError("NET_WORTH_MISSING_VALUE_REJECTED")
    else:
        if (
            gross is None
            or economic is None
            or economic > gross
            or valuation_date is None
            or valuation_date > as_of
            or method is ValuationMethod.NOT_AVAILABLE
            or source is ValuationSource.NOT_AVAILABLE
        ):
            raise NetWorthValidationError("NET_WORTH_VALUATION_REJECTED")
    return {
        "record_id": _safe_id(item["record_id"]),
        "economic_claim_id": _safe_id(item["economic_claim_id"]),
        "gross": gross,
        "economic": economic,
        "registered_holder_role": _enum(
            HolderRole, item["registered_holder_role"]
        ),
        "economic_owner_role": _enum(HolderRole, item["economic_owner_role"]),
        "valuation_date": valuation_date,
        "valuation_method": method,
        "valuation_source": source,
        "valuation_status": status,
        "uncertainty": uncertainty,
    }


def _present_asset(entry: AssetEntry) -> int:
    if entry.economic_value_krw is None:
        raise NetWorthValidationError("NET_WORTH_VALUE_NOT_PRESENT")
    return entry.economic_value_krw


def _present_liability(entry: LiabilityEntry) -> int:
    if entry.economic_principal_krw is None:
        raise NetWorthValidationError("NET_WORTH_VALUE_NOT_PRESENT")
    return entry.economic_principal_krw


def _strict_item(value: object, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise NetWorthValidationError("NET_WORTH_SCHEMA_REJECTED")
    return value


def _safe_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise NetWorthValidationError("NET_WORTH_IDENTIFIER_REJECTED")
    if _PRIVATE_ID_TOKEN.search(value) or redact_account_text(value) != value:
        raise NetWorthValidationError("NET_WORTH_IDENTIFIER_REJECTED")
    return value


def _money(value: object, *, nullable: bool) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NetWorthValidationError("NET_WORTH_MONEY_REJECTED")
    return value


def _date(value: object) -> date:
    if not isinstance(value, str):
        raise NetWorthValidationError("NET_WORTH_DATE_REJECTED")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise NetWorthValidationError("NET_WORTH_DATE_REJECTED") from None


def _utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise NetWorthValidationError("NET_WORTH_TIMESTAMP_REJECTED") from None
    else:
        raise NetWorthValidationError("NET_WORTH_TIMESTAMP_REJECTED")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NetWorthValidationError("NET_WORTH_TIMESTAMP_REJECTED")
    return parsed.astimezone(timezone.utc)


def _enum(kind: type[StrEnum], value: object):
    try:
        return kind(str(value))
    except ValueError:
        raise NetWorthValidationError("NET_WORTH_ENUM_REJECTED") from None


def _reject_sensitive(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _FORBIDDEN_KEY.search(str(key)):
                raise NetWorthValidationError("NET_WORTH_PRIVATE_FIELD_REJECTED")
            _reject_sensitive(item)
    elif isinstance(value, list):
        for item in value:
            _reject_sensitive(item)
    elif isinstance(value, str) and redact_account_text(value) != value:
        raise NetWorthValidationError("NET_WORTH_PRIVATE_VALUE_REJECTED")


def _snapshot_payload(snapshot: NetWorthSnapshot) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot.snapshot_id,
        "as_of_date": snapshot.as_of_date.isoformat(),
        "recorded_at_utc": snapshot.recorded_at_utc.isoformat(),
        "base_currency": snapshot.base_currency,
        "assets": [_asset_payload(entry) for entry in snapshot.assets],
        "liabilities": [_liability_payload(entry) for entry in snapshot.liabilities],
    }


def _asset_payload(entry: AssetEntry) -> dict[str, object]:
    return {
        "record_id": entry.record_id,
        "economic_claim_id": entry.economic_claim_id,
        "asset_class": entry.asset_class.value,
        "gross_value_krw": entry.gross_value_krw,
        "economic_value_krw": entry.economic_value_krw,
        "registered_holder_role": entry.registered_holder_role.value,
        "economic_owner_role": entry.economic_owner_role.value,
        "valuation_date": entry.valuation_date.isoformat() if entry.valuation_date else None,
        "valuation_method": entry.valuation_method.value,
        "valuation_source": entry.valuation_source.value,
        "valuation_status": entry.valuation_status.value,
        "uncertainty": entry.uncertainty.value,
    }


def _liability_payload(entry: LiabilityEntry) -> dict[str, object]:
    return {
        "record_id": entry.record_id,
        "economic_claim_id": entry.economic_claim_id,
        "liability_class": entry.liability_class.value,
        "gross_principal_krw": entry.gross_principal_krw,
        "economic_principal_krw": entry.economic_principal_krw,
        "unused_limit_krw": entry.unused_limit_krw,
        "registered_holder_role": entry.registered_holder_role.value,
        "economic_owner_role": entry.economic_owner_role.value,
        "valuation_date": entry.valuation_date.isoformat() if entry.valuation_date else None,
        "valuation_method": entry.valuation_method.value,
        "valuation_source": entry.valuation_source.value,
        "valuation_status": entry.valuation_status.value,
        "uncertainty": entry.uncertainty.value,
    }


def _totals_payload(totals: NetWorthTotals) -> dict[str, object]:
    return {
        "liquid_financial_assets_krw": totals.liquid_financial_assets_krw,
        "total_assets_krw": totals.total_assets_krw,
        "total_liabilities_krw": totals.total_liabilities_krw,
        "net_worth_krw": totals.net_worth_krw,
        "unused_credit_limit_krw": totals.unused_credit_limit_krw,
        "complete": totals.complete,
        "stale_claim_ids": list(totals.stale_claim_ids),
        "missing_claim_ids": list(totals.missing_claim_ids),
        "uncertain_claim_ids": list(totals.uncertain_claim_ids),
    }


def _digest(payload: Mapping[str, object]) -> str:
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _atomic_json_new(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".pending-{uuid4().hex}.tmp"
    body = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise NetWorthPersistenceError("NET_WORTH_HISTORY_CONFLICT")
        os.rename(temporary, path)
    except NetWorthPersistenceError:
        raise
    except OSError:
        raise NetWorthPersistenceError("NET_WORTH_HISTORY_WRITE_FAILED") from None
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "AssetClass",
    "AssetEntry",
    "BASE_CURRENCY",
    "HISTORY_VERSION",
    "HolderRole",
    "LiabilityClass",
    "LiabilityEntry",
    "LocalNetWorthHistoryStore",
    "NetWorthHistoryRecord",
    "NetWorthPersistenceError",
    "NetWorthSnapshot",
    "NetWorthTimelineDeltaState",
    "NetWorthTimelineDisplayState",
    "NetWorthTimelinePoint",
    "NetWorthTimelineView",
    "NetWorthTotals",
    "NetWorthValidationError",
    "NetWorthView",
    "SCHEMA_VERSION",
    "ValuationMethod",
    "ValuationSource",
    "ValuationStatus",
    "ValuationUncertainty",
    "build_net_worth_timeline",
    "calculate_net_worth",
    "parse_snapshot",
]
