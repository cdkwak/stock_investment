"""Versioned local-only Dashboard layout preferences.

The schema contains presentation identifiers and logical window geometry only.
It cannot carry credentials, account values, provider payloads, watchlists, or
market observations.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Mapping
from uuid import uuid4


SCHEMA_VERSION = 8
CARD_IDS = (
    "KOSPI", "KOSDAQ", "NQ_FUTURES", "NASDAQ", "SP500", "SOXX", "GOLD", "WTI", "BITCOIN",
    "USD_KRW_60M",
)
SECTION_IDS = (
    "KOSPI_CHART", "NQ_CHART", "MARKET_TEMPERATURE", "MARKET_FLOW",
    "FX_RATES", "ACCOUNT_SUMMARY", "DERIVATIVES",
)
DENSITIES = ("COMPACT", "DETAIL")
MARKET_ASSETS = (
    "KOSPI", "KOSDAQ", "Nasdaq 100", "Nasdaq 100 Futures", "Nasdaq",
    "S&P 500", "SOXX", "GOLD", "WTI",
)
MARKET_PERIODS = ("60D", "120D", "1Y", "3Y", "5Y", "10Y", "MAX")
NQ_INTERVALS = ("일봉", "주봉", "월봉")


class DashboardPreferencesError(RuntimeError):
    """Value-free local preference failure."""


@dataclass(frozen=True, slots=True)
class WindowGeometry:
    x: int
    y: int
    width: int
    height: int
    maximized: bool = False


@dataclass(frozen=True, slots=True)
class ChartIndicatorPreferences:
    """Presentation switches only; indicators remain computed by the chart service."""

    ma5: bool
    ma20: bool
    ma60: bool
    ma120: bool
    volume: bool
    rsi14_mode: str
    disparity60_mode: str
    ema20: bool = False
    bollinger_bands: bool = False
    atr14_mode: str = "Off"
    adx14_mode: str = "Off"
    obv_mode: str = "Off"
    bollinger_bandwidth_mode: str = "Off"


INDICATOR_MODES = ("Off", "Overlay", "Panel")
INDICATOR_CONTEXTS = ("DASHBOARD", "INDEX", "EQUITY", "US_ETF")
_DASHBOARD_INDICATORS = ChartIndicatorPreferences(
    False, False, True, False, True, "Off", "Off",
)
_FULL_CHART_INDICATORS = ChartIndicatorPreferences(
    True, True, True, True, True, "Off", "Off",
)


@dataclass(frozen=True, slots=True)
class DashboardPreferences:
    card_order: tuple[str, ...]
    hidden_cards: frozenset[str]
    pinned_cards: frozenset[str]
    section_order: tuple[str, ...]
    hidden_sections: frozenset[str]
    density: str
    default_market_asset: str
    default_market_period: str
    default_nq_interval: str
    dashboard_indicators: ChartIndicatorPreferences
    index_indicators: ChartIndicatorPreferences
    equity_indicators: ChartIndicatorPreferences
    us_etf_indicators: ChartIndicatorPreferences
    window_geometry: WindowGeometry

    def indicators_for(self, context: str) -> ChartIndicatorPreferences:
        values = {
            "DASHBOARD": self.dashboard_indicators,
            "INDEX": self.index_indicators,
            "EQUITY": self.equity_indicators,
            "US_ETF": self.us_etf_indicators,
        }
        return values[context]

    def with_indicators(
        self, context: str, indicators: ChartIndicatorPreferences,
    ) -> "DashboardPreferences":
        fields = {
            "DASHBOARD": "dashboard_indicators",
            "INDEX": "index_indicators",
            "EQUITY": "equity_indicators",
            "US_ETF": "us_etf_indicators",
        }
        return replace(self, **{fields[context]: indicators})

    @property
    def effective_card_order(self) -> tuple[str, ...]:
        pinned = tuple(item for item in self.card_order if item in self.pinned_cards)
        unpinned = tuple(item for item in self.card_order if item not in self.pinned_cards)
        return pinned + unpinned


@dataclass(frozen=True, slots=True)
class DashboardPreferencesLoadResult:
    preferences: DashboardPreferences
    reason: str


DEFAULT_PREFERENCES = DashboardPreferences(
    card_order=CARD_IDS,
    hidden_cards=frozenset(),
    pinned_cards=frozenset(),
    section_order=SECTION_IDS,
    hidden_sections=frozenset(),
    density="COMPACT",
    default_market_asset="KOSPI",
    default_market_period="120D",
    default_nq_interval="일봉",
    dashboard_indicators=_DASHBOARD_INDICATORS,
    index_indicators=_FULL_CHART_INDICATORS,
    equity_indicators=_FULL_CHART_INDICATORS,
    us_etf_indicators=_FULL_CHART_INDICATORS,
    window_geometry=WindowGeometry(40, 40, 1600, 900, False),
)


_V3_KEYS = {
    "schema_version", "card_order", "hidden_cards", "pinned_cards",
    "section_order", "hidden_sections", "density", "default_market_asset",
    "default_market_period", "default_nq_interval", "chart_indicators", "window_geometry",
}
_V4_KEYS = _V3_KEYS
_V5_KEYS = _V4_KEYS
_V6_KEYS = _V5_KEYS
_V7_KEYS = _V6_KEYS
_V2_KEYS = _V3_KEYS - {"chart_indicators"}
_V1_KEYS = {
    "schema_version", "visible_cards", "card_order", "compact",
    "default_market_asset", "default_market_period", "window_geometry",
}
_GEOMETRY_KEYS = {"x", "y", "width", "height", "maximized"}


class LocalDashboardPreferencesStore:
    """Atomic primary plus last-valid backup for presentation-only settings."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.backup_path = self.path.with_name(
            f"{self.path.stem}.last_valid{self.path.suffix}"
        )

    def load(self) -> DashboardPreferencesLoadResult:
        if not self.path.exists():
            recovered = self._try_load(self.backup_path)
            if recovered is not None:
                return DashboardPreferencesLoadResult(recovered[0], "RECOVERED_LAST_VALID")
            return DashboardPreferencesLoadResult(DEFAULT_PREFERENCES, "DEFAULT_MISSING")
        loaded = self._try_load(self.path)
        if loaded is not None:
            preferences, version = loaded
            if version in {1, 2, 3, 4, 5, 6, 7}:
                try:
                    self.save(preferences)
                    reason = f"MIGRATED_V{version}"
                except DashboardPreferencesError:
                    reason = f"MIGRATED_V{version}_MEMORY_ONLY"
                return DashboardPreferencesLoadResult(preferences, reason)
            return DashboardPreferencesLoadResult(preferences, "LOADED")
        recovered = self._try_load(self.backup_path)
        if recovered is not None:
            return DashboardPreferencesLoadResult(recovered[0], "RECOVERED_LAST_VALID")
        return DashboardPreferencesLoadResult(DEFAULT_PREFERENCES, "DEFAULT_CORRUPT")

    def save(self, preferences: DashboardPreferences) -> None:
        payload = preferences_payload(preferences)
        encoded = _encode(payload)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise DashboardPreferencesError(
                "DASHBOARD_PREFERENCES_WRITE_FAILED"
            ) from None
        current = None
        try:
            if self.path.is_file() and not self.path.is_symlink():
                body = self.path.read_bytes()
                decoded = json.loads(body.decode("utf-8"))
                if isinstance(decoded, dict):
                    _parse_payload(decoded)
                    current = body
        except (OSError, UnicodeError, json.JSONDecodeError, DashboardPreferencesError):
            current = None
        try:
            if current is not None:
                _atomic_replace(self.backup_path, current)
            _atomic_replace(self.path, encoded)
            try:
                _atomic_replace(self.backup_path, encoded)
            except OSError:
                # The newly committed primary remains valid; an older valid
                # backup, when present, must not be destroyed on refresh failure.
                pass
        except OSError:
            raise DashboardPreferencesError("DASHBOARD_PREFERENCES_WRITE_FAILED") from None

    def reset(self) -> DashboardPreferences:
        self.save(DEFAULT_PREFERENCES)
        return DEFAULT_PREFERENCES

    def _try_load(self, path: Path) -> tuple[DashboardPreferences, int] | None:
        try:
            if not path.is_file() or path.is_symlink():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            return _parse_payload(payload)
        except (
            OSError, UnicodeError, json.JSONDecodeError,
            DashboardPreferencesError, TypeError, ValueError,
        ):
            return None


def preferences_payload(preferences: DashboardPreferences) -> dict[str, object]:
    _validate_preferences(preferences)
    geometry = preferences.window_geometry
    return {
        "schema_version": SCHEMA_VERSION,
        "card_order": list(preferences.card_order),
        "hidden_cards": sorted(preferences.hidden_cards),
        "pinned_cards": sorted(preferences.pinned_cards),
        "section_order": list(preferences.section_order),
        "hidden_sections": sorted(preferences.hidden_sections),
        "density": preferences.density,
        "default_market_asset": preferences.default_market_asset,
        "default_market_period": preferences.default_market_period,
        "default_nq_interval": preferences.default_nq_interval,
        "chart_indicators": {
            context: _indicator_payload(preferences.indicators_for(context))
            for context in INDICATOR_CONTEXTS
        },
        "window_geometry": {
            "x": geometry.x, "y": geometry.y,
            "width": geometry.width, "height": geometry.height,
            "maximized": geometry.maximized,
        },
    }


def safe_window_geometry(
    geometry: WindowGeometry,
    available: tuple[int, int, int, int],
) -> WindowGeometry:
    """Clamp logical geometry to the current screen's available rectangle."""

    left, top, available_width, available_height = available
    if available_width <= 0 or available_height <= 0:
        return DEFAULT_PREFERENCES.window_geometry
    minimum_width = min(1200, available_width)
    minimum_height = min(800, available_height)
    width = min(max(geometry.width, minimum_width), available_width)
    height = min(max(geometry.height, minimum_height), available_height)
    x = min(max(geometry.x, left), left + available_width - width)
    y = min(max(geometry.y, top), top + available_height - height)
    return WindowGeometry(x, y, width, height, geometry.maximized)


def with_geometry(
    preferences: DashboardPreferences, geometry: WindowGeometry,
) -> DashboardPreferences:
    updated = replace(preferences, window_geometry=geometry)
    _validate_preferences(updated)
    return updated


def _parse_payload(payload: Mapping[str, object]) -> tuple[DashboardPreferences, int]:
    version = payload.get("schema_version")
    if version == 1:
        return _migrate_v1(payload), 1
    if version == 2:
        return _migrate_v2(payload), 2
    if version == 3:
        return _migrate_v3(payload), 3
    if version == 4:
        return _migrate_v4(payload), 4
    if version == 5:
        return _migrate_v5(payload), 5
    if version == 6:
        return _migrate_v6(payload), 6
    if version == 7:
        return _migrate_v7(payload), 7
    if version != SCHEMA_VERSION or set(payload) != _V7_KEYS:
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    preferences = DashboardPreferences(
        card_order=_ordered(payload["card_order"], CARD_IDS),
        hidden_cards=_subset(payload["hidden_cards"], CARD_IDS),
        pinned_cards=_subset(payload["pinned_cards"], CARD_IDS),
        section_order=_ordered(payload["section_order"], SECTION_IDS),
        hidden_sections=_subset(payload["hidden_sections"], SECTION_IDS),
        density=_choice(payload["density"], DENSITIES),
        default_market_asset=_choice(payload["default_market_asset"], MARKET_ASSETS),
        default_market_period=_choice(payload["default_market_period"], MARKET_PERIODS),
        default_nq_interval=_choice(payload["default_nq_interval"], NQ_INTERVALS),
        dashboard_indicators=_indicators(_indicator_context(payload["chart_indicators"], "DASHBOARD"), "DASHBOARD"),
        index_indicators=_indicators(_indicator_context(payload["chart_indicators"], "INDEX"), "INDEX"),
        equity_indicators=_indicators(_indicator_context(payload["chart_indicators"], "EQUITY"), "EQUITY"),
        us_etf_indicators=_indicators(_indicator_context(payload["chart_indicators"], "US_ETF"), "US_ETF"),
        window_geometry=_geometry(payload["window_geometry"]),
    )
    _validate_preferences(preferences)
    return preferences, SCHEMA_VERSION


def _migrate_v1(payload: Mapping[str, object]) -> DashboardPreferences:
    if set(payload) != _V1_KEYS:
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    order = _ordered(payload["card_order"], CARD_IDS)
    visible = _subset(payload["visible_cards"], CARD_IDS)
    if not isinstance(payload["compact"], bool):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    preferences = replace(
        DEFAULT_PREFERENCES,
        card_order=order,
        hidden_cards=frozenset(CARD_IDS) - visible,
        density="COMPACT" if payload["compact"] else "DETAIL",
        default_market_asset=_choice(payload["default_market_asset"], MARKET_ASSETS),
        default_market_period=_choice(payload["default_market_period"], MARKET_PERIODS),
        window_geometry=_geometry(payload["window_geometry"]),
    )
    _validate_preferences(preferences)
    return preferences


def _migrate_v2(payload: Mapping[str, object]) -> DashboardPreferences:
    if set(payload) != _V2_KEYS:
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    preferences = DashboardPreferences(
        card_order=_ordered(payload["card_order"], CARD_IDS),
        hidden_cards=_subset(payload["hidden_cards"], CARD_IDS),
        pinned_cards=_subset(payload["pinned_cards"], CARD_IDS),
        section_order=_ordered(payload["section_order"], SECTION_IDS),
        hidden_sections=_subset(payload["hidden_sections"], SECTION_IDS),
        density=_choice(payload["density"], DENSITIES),
        default_market_asset=_choice(payload["default_market_asset"], MARKET_ASSETS),
        default_market_period=_choice(payload["default_market_period"], MARKET_PERIODS),
        default_nq_interval=_choice(payload["default_nq_interval"], NQ_INTERVALS),
        dashboard_indicators=_DASHBOARD_INDICATORS,
        index_indicators=_FULL_CHART_INDICATORS,
        equity_indicators=_FULL_CHART_INDICATORS,
        us_etf_indicators=_FULL_CHART_INDICATORS,
        window_geometry=_geometry(payload["window_geometry"]),
    )
    _validate_preferences(preferences)
    return preferences


def _migrate_v3(payload: Mapping[str, object]) -> DashboardPreferences:
    if set(payload) != _V3_KEYS or not isinstance(payload.get("chart_indicators"), Mapping):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    migrated = dict(payload)
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["chart_indicators"] = {
        context: {
            **dict(_indicator_context(payload["chart_indicators"], context)),
            "ema20": False, "bollinger_bands": False,
            "atr14_mode": "Off", "adx14_mode": "Off", "obv_mode": "Off",
            "bollinger_bandwidth_mode": "Off",
        }
        for context in INDICATOR_CONTEXTS
    }
    return _parse_payload(migrated)[0]


def _migrate_v4(payload: Mapping[str, object]) -> DashboardPreferences:
    legacy_cards = (
        "KOSPI", "KOSDAQ", "SOXX", "NQ_FUTURES", "NASDAQ",
        "SP500", "GOLD", "VIX", "WTI",
    )
    if set(payload) != _V4_KEYS or not isinstance(payload.get("chart_indicators"), Mapping):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    _ordered(payload["card_order"], legacy_cards)
    preferences = DashboardPreferences(
        card_order=CARD_IDS,
        hidden_cards=frozenset(_subset(payload["hidden_cards"], legacy_cards)) & frozenset(CARD_IDS),
        pinned_cards=frozenset(_subset(payload["pinned_cards"], legacy_cards)) & frozenset(CARD_IDS),
        section_order=_ordered(payload["section_order"], SECTION_IDS),
        hidden_sections=_subset(payload["hidden_sections"], SECTION_IDS),
        density=_choice(payload["density"], DENSITIES),
        default_market_asset=_choice(payload["default_market_asset"], MARKET_ASSETS),
        default_market_period=_choice(payload["default_market_period"], MARKET_PERIODS),
        default_nq_interval=_choice(payload["default_nq_interval"], NQ_INTERVALS),
        dashboard_indicators=_indicators(_indicator_context(payload["chart_indicators"], "DASHBOARD"), "DASHBOARD"),
        index_indicators=_indicators(_indicator_context(payload["chart_indicators"], "INDEX"), "INDEX"),
        equity_indicators=_indicators(_indicator_context(payload["chart_indicators"], "EQUITY"), "EQUITY"),
        us_etf_indicators=_indicators(_indicator_context(payload["chart_indicators"], "US_ETF"), "US_ETF"),
        window_geometry=_geometry(payload["window_geometry"]),
    )
    _validate_preferences(preferences)
    return preferences


def _migrate_v5(payload: Mapping[str, object]) -> DashboardPreferences:
    legacy_cards = (
        "KOSPI", "KOSDAQ", "SOXX", "NQ_FUTURES", "NASDAQ",
        "SP500", "SPY", "GOLD", "VIX", "WTI",
    )
    if set(payload) != _V5_KEYS or not isinstance(payload.get("chart_indicators"), Mapping):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    _ordered(payload["card_order"], legacy_cards)
    preferences = DashboardPreferences(
        card_order=CARD_IDS,
        hidden_cards=frozenset(_subset(payload["hidden_cards"], legacy_cards)) & frozenset(CARD_IDS),
        pinned_cards=frozenset(_subset(payload["pinned_cards"], legacy_cards)) & frozenset(CARD_IDS),
        section_order=_ordered(payload["section_order"], SECTION_IDS),
        hidden_sections=_subset(payload["hidden_sections"], SECTION_IDS),
        density=_choice(payload["density"], DENSITIES),
        default_market_asset=_choice(payload["default_market_asset"], MARKET_ASSETS),
        default_market_period=_choice(payload["default_market_period"], MARKET_PERIODS),
        default_nq_interval=_choice(payload["default_nq_interval"], NQ_INTERVALS),
        dashboard_indicators=_indicators(_indicator_context(payload["chart_indicators"], "DASHBOARD"), "DASHBOARD"),
        index_indicators=_indicators(_indicator_context(payload["chart_indicators"], "INDEX"), "INDEX"),
        equity_indicators=_indicators(_indicator_context(payload["chart_indicators"], "EQUITY"), "EQUITY"),
        us_etf_indicators=_indicators(_indicator_context(payload["chart_indicators"], "US_ETF"), "US_ETF"),
        window_geometry=_geometry(payload["window_geometry"]),
    )
    _validate_preferences(preferences)
    return preferences


def _migrate_v6(payload: Mapping[str, object]) -> DashboardPreferences:
    legacy_cards = (
        "NQ_FUTURES", "NASDAQ", "SP500", "SOXX", "GOLD", "WTI", "BITCOIN",
    )
    if set(payload) != _V6_KEYS or not isinstance(payload.get("chart_indicators"), Mapping):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    _ordered(payload["card_order"], legacy_cards)
    preferences = DashboardPreferences(
        card_order=CARD_IDS,
        hidden_cards=frozenset(_subset(payload["hidden_cards"], legacy_cards)),
        pinned_cards=frozenset(_subset(payload["pinned_cards"], legacy_cards)),
        section_order=_ordered(payload["section_order"], SECTION_IDS),
        hidden_sections=_subset(payload["hidden_sections"], SECTION_IDS),
        density=_choice(payload["density"], DENSITIES),
        default_market_asset=_choice(payload["default_market_asset"], MARKET_ASSETS),
        default_market_period=_choice(payload["default_market_period"], MARKET_PERIODS),
        default_nq_interval=_choice(payload["default_nq_interval"], NQ_INTERVALS),
        dashboard_indicators=_indicators(_indicator_context(payload["chart_indicators"], "DASHBOARD"), "DASHBOARD"),
        index_indicators=_indicators(_indicator_context(payload["chart_indicators"], "INDEX"), "INDEX"),
        equity_indicators=_indicators(_indicator_context(payload["chart_indicators"], "EQUITY"), "EQUITY"),
        us_etf_indicators=_indicators(_indicator_context(payload["chart_indicators"], "US_ETF"), "US_ETF"),
        window_geometry=_geometry(payload["window_geometry"]),
    )
    _validate_preferences(preferences)
    return preferences


def _migrate_v7(payload: Mapping[str, object]) -> DashboardPreferences:
    legacy_cards = (
        "KOSPI", "KOSDAQ", "NQ_FUTURES", "NASDAQ", "SP500", "SOXX",
        "GOLD", "WTI", "BITCOIN",
    )
    if set(payload) != _V7_KEYS or not isinstance(payload.get("chart_indicators"), Mapping):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    old_order = _ordered(payload["card_order"], legacy_cards)
    preferences = DashboardPreferences(
        card_order=old_order + ("USD_KRW_60M",),
        hidden_cards=frozenset(_subset(payload["hidden_cards"], legacy_cards)),
        pinned_cards=frozenset(_subset(payload["pinned_cards"], legacy_cards)),
        section_order=_ordered(payload["section_order"], SECTION_IDS),
        hidden_sections=_subset(payload["hidden_sections"], SECTION_IDS),
        density=_choice(payload["density"], DENSITIES),
        default_market_asset=_choice(payload["default_market_asset"], MARKET_ASSETS),
        default_market_period=_choice(payload["default_market_period"], MARKET_PERIODS),
        default_nq_interval=_choice(payload["default_nq_interval"], NQ_INTERVALS),
        dashboard_indicators=_indicators(_indicator_context(payload["chart_indicators"], "DASHBOARD"), "DASHBOARD"),
        index_indicators=_indicators(_indicator_context(payload["chart_indicators"], "INDEX"), "INDEX"),
        equity_indicators=_indicators(_indicator_context(payload["chart_indicators"], "EQUITY"), "EQUITY"),
        us_etf_indicators=_indicators(_indicator_context(payload["chart_indicators"], "US_ETF"), "US_ETF"),
        window_geometry=_geometry(payload["window_geometry"]),
    )
    _validate_preferences(preferences)
    return preferences


def _validate_preferences(preferences: DashboardPreferences) -> None:
    if tuple(preferences.card_order) != _ordered(preferences.card_order, CARD_IDS):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    if tuple(preferences.section_order) != _ordered(preferences.section_order, SECTION_IDS):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    if preferences.hidden_cards & preferences.pinned_cards:
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    _subset(preferences.hidden_cards, CARD_IDS)
    _subset(preferences.pinned_cards, CARD_IDS)
    _subset(preferences.hidden_sections, SECTION_IDS)
    _choice(preferences.density, DENSITIES)
    _choice(preferences.default_market_asset, MARKET_ASSETS)
    _choice(preferences.default_market_period, MARKET_PERIODS)
    _choice(preferences.default_nq_interval, NQ_INTERVALS)
    for context in INDICATOR_CONTEXTS:
        _validate_indicators(preferences.indicators_for(context), context)
    _geometry({
        "x": preferences.window_geometry.x,
        "y": preferences.window_geometry.y,
        "width": preferences.window_geometry.width,
        "height": preferences.window_geometry.height,
        "maximized": preferences.window_geometry.maximized,
    })


def _ordered(value: object, allowed: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    result = tuple(value)
    if len(result) != len(allowed) or set(result) != set(allowed):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    return result


def _subset(value: object, allowed: tuple[str, ...]) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)) or any(
        not isinstance(item, str) for item in value
    ):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    result = frozenset(value)
    if not result <= set(allowed):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    return result


def _choice(value: object, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    return value


def _indicator_payload(value: ChartIndicatorPreferences) -> dict[str, object]:
    _validate_indicators(value, "payload")
    return {
        "ma5": value.ma5,
        "ma20": value.ma20,
        "ma60": value.ma60,
        "ma120": value.ma120,
        "volume": value.volume,
        "rsi14_mode": value.rsi14_mode,
        "disparity60_mode": value.disparity60_mode,
        "ema20": value.ema20,
        "bollinger_bands": value.bollinger_bands,
        "atr14_mode": value.atr14_mode,
        "adx14_mode": value.adx14_mode,
        "obv_mode": value.obv_mode,
        "bollinger_bandwidth_mode": value.bollinger_bandwidth_mode,
    }


def _indicators(value: object, context: str) -> ChartIndicatorPreferences:
    fields = {"ma5", "ma20", "ma60", "ma120", "volume", "rsi14_mode", "disparity60_mode", "ema20", "bollinger_bands", "atr14_mode", "adx14_mode", "obv_mode", "bollinger_bandwidth_mode"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    indicators = ChartIndicatorPreferences(
        *(
            value[name] for name in ("ma5", "ma20", "ma60", "ma120", "volume")
        ),
        value["rsi14_mode"], value["disparity60_mode"], value["ema20"],
        value["bollinger_bands"], value["atr14_mode"], value["adx14_mode"],
        value["obv_mode"], value["bollinger_bandwidth_mode"],
    )
    _validate_indicators(indicators, context)
    return indicators


def _indicator_context(value: object, context: str) -> object:
    if not isinstance(value, Mapping) or set(value) != set(INDICATOR_CONTEXTS):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    return value[context]


def _validate_indicators(value: ChartIndicatorPreferences, context: str) -> None:
    if not isinstance(value, ChartIndicatorPreferences):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    if any(not isinstance(item, bool) for item in (
        value.ma5, value.ma20, value.ma60, value.ma120, value.volume,
        value.ema20, value.bollinger_bands,
    )):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    _choice(value.rsi14_mode, INDICATOR_MODES)
    _choice(value.disparity60_mode, INDICATOR_MODES)
    for mode in (value.atr14_mode, value.adx14_mode, value.obv_mode, value.bollinger_bandwidth_mode):
        _choice(mode, ("Off", "Panel"))
    if context == "DASHBOARD" and (
        value.rsi14_mode == "Panel" or value.disparity60_mode == "Panel"
    ):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")


def _geometry(value: object) -> WindowGeometry:
    if not isinstance(value, dict) or set(value) != _GEOMETRY_KEYS:
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    numbers = (value["x"], value["y"], value["width"], value["height"])
    if any(isinstance(item, bool) or not isinstance(item, int) for item in numbers):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    if not isinstance(value["maximized"], bool):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    if not (-100_000 <= value["x"] <= 100_000 and -100_000 <= value["y"] <= 100_000):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    if not (320 <= value["width"] <= 16_384 and 240 <= value["height"] <= 16_384):
        raise DashboardPreferencesError("DASHBOARD_PREFERENCES_SCHEMA_INVALID")
    return WindowGeometry(
        value["x"], value["y"], value["width"], value["height"], value["maximized"]
    )


def _encode(payload: Mapping[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _atomic_replace(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


__all__ = [
    "CARD_IDS", "ChartIndicatorPreferences", "DEFAULT_PREFERENCES", "DENSITIES", "DashboardPreferences",
    "DashboardPreferencesError", "DashboardPreferencesLoadResult",
    "LocalDashboardPreferencesStore", "MARKET_ASSETS", "MARKET_PERIODS",
    "INDICATOR_CONTEXTS", "INDICATOR_MODES", "NQ_INTERVALS", "SCHEMA_VERSION", "SECTION_IDS", "WindowGeometry",
    "preferences_payload", "safe_window_geometry", "with_geometry",
]
