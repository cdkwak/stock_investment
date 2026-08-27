from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from stock_data.gui.services import (
    EquityIdentity,
    EquitySeriesView,
    US_ETF_CHART_IDENTITIES,
)


WATCHLIST_SCHEMA_VERSION = 1
DEFAULT_LIST_ID = "favorites"
DEFAULT_LIST_NAME = "관심종목"


@dataclass(frozen=True)
class WatchlistItem:
    identity: EquityIdentity
    added_at_kst: str

    @property
    def key(self) -> tuple[str, str]:
        return self.identity.key


@dataclass(frozen=True)
class NamedWatchlist:
    list_id: str
    name: str
    items: tuple[WatchlistItem, ...] = ()


@dataclass(frozen=True)
class WatchlistState:
    lists: tuple[NamedWatchlist, ...]
    revision: int = 0
    recovered_from_backup: bool = False
    migration_required: bool = False

    @property
    def default_list(self) -> NamedWatchlist:
        return next(item for item in self.lists if item.list_id == DEFAULT_LIST_ID)

    def list_by_id(self, list_id: str) -> NamedWatchlist:
        return next(item for item in self.lists if item.list_id == list_id)


@dataclass(frozen=True)
class WatchlistQuote:
    identity: EquityIdentity
    price: float | None
    change: float | None
    change_pct: float | None
    reference_kst: str | None
    freshness: str
    unavailable_reason: str | None = None
    five_session_pct: float | None = None
    recent_period_pct: float | None = None
    recent_closes: tuple[float, ...] = ()

    @property
    def displays_values(self) -> bool:
        return self.price is not None and self.unavailable_reason is None


class LocalWatchlistService:
    """Atomic local user configuration, separate from every market-data layer."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.backup_path = self.path.with_suffix(self.path.suffix + ".bak")
        self._clock = clock or (lambda: datetime.now(ZoneInfo("Asia/Seoul")))

    def empty_state(self) -> WatchlistState:
        return WatchlistState((NamedWatchlist(DEFAULT_LIST_ID, DEFAULT_LIST_NAME),))

    def load(self) -> WatchlistState:
        if not self.path.exists():
            return self.empty_state()
        try:
            return self._decode(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            try:
                recovered = self._decode(self.backup_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                return replace(self.empty_state(), recovered_from_backup=True)
            return replace(recovered, recovered_from_backup=True)

    def create_list(self, name: str) -> WatchlistState:
        state = self.load()
        clean = self._valid_name(name)
        self._ensure_unique_name(state, clean)
        item = NamedWatchlist(uuid.uuid4().hex, clean)
        return self._commit(replace(state, lists=state.lists + (item,)))

    def rename_list(self, list_id: str, name: str) -> WatchlistState:
        state = self.load()
        clean = self._valid_name(name)
        self._require_list(state, list_id)
        self._ensure_unique_name(state, clean, excluding=list_id)
        lists = tuple(replace(item, name=clean) if item.list_id == list_id else item for item in state.lists)
        return self._commit(replace(state, lists=lists))

    def remove_list(self, list_id: str) -> WatchlistState:
        if list_id == DEFAULT_LIST_ID:
            raise ValueError("default watchlist cannot be removed")
        state = self.load()
        self._require_list(state, list_id)
        return self._commit(replace(state, lists=tuple(item for item in state.lists if item.list_id != list_id)))

    def move_list(self, list_id: str, offset: int) -> WatchlistState:
        state = self.load()
        lists = list(state.lists)
        source = next((index for index, item in enumerate(lists) if item.list_id == list_id), None)
        if source is None:
            raise KeyError(list_id)
        target = max(0, min(len(lists) - 1, source + int(offset)))
        if target == source:
            return state
        lists.insert(target, lists.pop(source))
        return self._commit(replace(state, lists=tuple(lists)))

    def add_item(self, list_id: str, identity: EquityIdentity) -> WatchlistState:
        self._validate_identity(identity)
        state = self.load()
        target = self._require_list(state, list_id)
        if any(item.key == identity.key for item in target.items):
            return state
        added = WatchlistItem(identity, self._now())
        lists = tuple(replace(item, items=item.items + (added,)) if item.list_id == list_id else item for item in state.lists)
        return self._commit(replace(state, lists=lists))

    def remove_item(self, list_id: str, key: tuple[str, str]) -> WatchlistState:
        state = self.load()
        target = self._require_list(state, list_id)
        items = tuple(item for item in target.items if item.key != key)
        if items == target.items:
            return state
        lists = tuple(replace(item, items=items) if item.list_id == list_id else item for item in state.lists)
        return self._commit(replace(state, lists=lists))

    def move_item(self, list_id: str, key: tuple[str, str], offset: int) -> WatchlistState:
        state = self.load()
        target_list = self._require_list(state, list_id)
        items = list(target_list.items)
        source = next((index for index, item in enumerate(items) if item.key == key), None)
        if source is None:
            raise KeyError(key)
        target = max(0, min(len(items) - 1, source + int(offset)))
        if target == source:
            return state
        items.insert(target, items.pop(source))
        lists = tuple(replace(item, items=tuple(items)) if item.list_id == list_id else item for item in state.lists)
        return self._commit(replace(state, lists=lists))

    def _commit(self, state: WatchlistState) -> WatchlistState:
        committed = replace(
            state,
            revision=state.revision + 1,
            recovered_from_backup=False,
            migration_required=False,
        )
        payload = self._encode(committed)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + f".{uuid.uuid4().hex}.tmp")
        backup_temp = self.backup_path.with_suffix(self.backup_path.suffix + f".{uuid.uuid4().hex}.tmp")
        try:
            if self.path.exists():
                try:
                    current = self._decode(self.path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                    current = None
                if current is not None:
                    self._write_fsynced(backup_temp, self._encode(current))
                    os.replace(backup_temp, self.backup_path)
            self._write_fsynced(temp, payload)
            os.replace(temp, self.path)
        finally:
            for candidate in (temp, backup_temp):
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
        return committed

    @staticmethod
    def _write_fsynced(path: Path, text: str) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())

    def _encode(self, state: WatchlistState) -> str:
        payload = {
            "schema_version": WATCHLIST_SCHEMA_VERSION,
            "revision": state.revision,
            "updated_at_kst": self._now(),
            "default_list_id": DEFAULT_LIST_ID,
            "lists": [
                {
                    "list_id": watchlist.list_id,
                    "name": watchlist.name,
                    "items": [
                        {
                            "market": item.identity.market,
                            "symbol": item.identity.symbol,
                            "name": item.identity.name,
                            "isin": item.identity.isin,
                            "listing_date": item.identity.listing_date,
                            "security_type": item.identity.security_type,
                            "issuer": item.identity.issuer,
                            "exposure": item.identity.exposure,
                            "currency": item.identity.currency,
                            "leverage_style": item.identity.leverage_style,
                            "distribution_style": item.identity.distribution_style,
                            "identity_source": item.identity.identity_source,
                            "added_at_kst": item.added_at_kst,
                        }
                        for item in watchlist.items
                    ],
                }
                for watchlist in state.lists
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _decode(self, text: str) -> WatchlistState:
        payload = json.loads(text)
        version = payload.get("schema_version")
        if version not in {0, WATCHLIST_SCHEMA_VERSION}:
            raise ValueError("unsupported watchlist schema")
        raw_lists = payload.get("lists")
        if not isinstance(raw_lists, list) or not raw_lists:
            raise ValueError("watchlists must be a non-empty list")
        lists: list[NamedWatchlist] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for position, raw_list in enumerate(raw_lists):
            list_id = str(raw_list.get("list_id") or (DEFAULT_LIST_ID if position == 0 else f"migrated-{position}"))
            name = self._valid_name(raw_list.get("name"))
            if list_id in seen_ids or name.casefold() in seen_names:
                raise ValueError("duplicate watchlist identity")
            seen_ids.add(list_id)
            seen_names.add(name.casefold())
            items: list[WatchlistItem] = []
            item_keys: set[tuple[str, str]] = set()
            for raw in raw_list.get("items", []):
                identity = EquityIdentity(
                    symbol=str(raw["symbol"]), name=str(raw["name"]), market=str(raw["market"]),
                    isin=self._optional_text(raw.get("isin")),
                    listing_date=self._optional_text(raw.get("listing_date")),
                    security_type=str(raw["security_type"]),
                    issuer=self._optional_text(raw.get("issuer")),
                    exposure=self._optional_text(raw.get("exposure")),
                    currency=self._optional_text(raw.get("currency")),
                    leverage_style=self._optional_text(raw.get("leverage_style")),
                    distribution_style=self._optional_text(raw.get("distribution_style")),
                    identity_source=self._optional_text(raw.get("identity_source")),
                )
                self._validate_identity(identity)
                if identity.key in item_keys:
                    raise ValueError("duplicate watchlist item")
                item_keys.add(identity.key)
                items.append(WatchlistItem(identity, str(raw.get("added_at_kst") or "MIGRATED")))
            lists.append(NamedWatchlist(list_id, name, tuple(items)))
        if DEFAULT_LIST_ID not in seen_ids:
            lists.insert(0, NamedWatchlist(DEFAULT_LIST_ID, DEFAULT_LIST_NAME))
        revision = int(payload.get("revision", 0))
        if revision < 0:
            raise ValueError("invalid watchlist revision")
        return WatchlistState(tuple(lists), revision, migration_required=version == 0)

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _valid_name(value: object) -> str:
        name = str(value or "").strip()
        if not name or len(name) > 40 or any(character in name for character in "\r\n\t"):
            raise ValueError("watchlist name must be 1-40 characters")
        return name

    @staticmethod
    def _validate_identity(identity: EquityIdentity) -> None:
        if identity.market not in {"KOSPI", "KOSDAQ", "US ETF"}:
            raise ValueError("unsupported watchlist market")
        if identity.market == "US ETF":
            if not identity.is_us_etf or not identity.symbol.isalpha() or not 1 <= len(identity.symbol) <= 5:
                raise ValueError("invalid U.S. ETF watchlist identity")
            if identity.currency != "USD" or not all((
                identity.issuer, identity.exposure, identity.listing_date,
                identity.leverage_style, identity.distribution_style,
            )):
                raise ValueError("incomplete U.S. ETF watchlist identity")
            canonical = next(
                (item for item in US_ETF_CHART_IDENTITIES if item.key == identity.key),
                None,
            )
            if canonical is None or canonical != identity:
                raise ValueError("U.S. ETF watchlist identity does not match the accepted catalog")
        elif len(identity.symbol) != 6 or not identity.symbol.isdigit():
            raise ValueError("watchlist ticker must be six digits")
        if not identity.name.strip() or not identity.security_type.strip():
            raise ValueError("incomplete watchlist identity")

    @staticmethod
    def _require_list(state: WatchlistState, list_id: str) -> NamedWatchlist:
        try:
            return state.list_by_id(list_id)
        except StopIteration as error:
            raise KeyError(list_id) from error

    @staticmethod
    def _ensure_unique_name(state: WatchlistState, name: str, *, excluding: str | None = None) -> None:
        if any(item.list_id != excluding and item.name.casefold() == name.casefold() for item in state.lists):
            raise ValueError("watchlist name already exists")

    def _now(self) -> str:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=ZoneInfo("Asia/Seoul"))
        return value.astimezone(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def quote_from_series(view: EquitySeriesView) -> WatchlistQuote:
    if not view.displays_values:
        return WatchlistQuote(
            identity=view.identity,
            price=None,
            change=None,
            change_pct=None,
            reference_kst=None,
            freshness=view.freshness,
            unavailable_reason=view.unavailable_reason or "현재 표시할 수 없습니다.",
        )
    recent_closes = tuple(float(value) for value in view.frame["close"].tail(20))
    five_session_pct = (
        (recent_closes[-1] / recent_closes[-6] - 1.0) * 100.0
        if len(recent_closes) >= 6 and recent_closes[-6] != 0
        else None
    )
    recent_period_pct = (
        (recent_closes[-1] / recent_closes[0] - 1.0) * 100.0
        if len(recent_closes) >= 2 and recent_closes[0] != 0
        else None
    )
    return WatchlistQuote(
        identity=view.identity,
        price=float(view.frame.iloc[-1]["close"]),
        change=view.change,
        change_pct=view.change_pct,
        reference_kst=view.reference_kst,
        freshness=view.freshness,
        five_session_pct=five_session_pct,
        recent_period_pct=recent_period_pct,
        recent_closes=recent_closes,
    )
