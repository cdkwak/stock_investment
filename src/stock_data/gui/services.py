from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from stock_data.contracts.market_60m import MARKET_PRICE_60M_OBSERVATION
from stock_data.contracts.market_15m import (
    MARKET_15M_SERIES_POLICIES,
    MARKET_PRICE_15M_OBSERVATION,
)
from stock_data.contracts.kospi200_constituent_breadth import KR_KOSPI200_BREADTH_DAILY
from stock_data.contracts.kr_index_fundamental_daily import (
    KR_INDEX_FUNDAMENTAL_DAILY,
)
from stock_data.contracts.ls_t1633 import LS_T1633_PROGRAM_TRADING_DAILY
from stock_data.contracts.toss_short_watchlist import (
    TOSS_EQUITY_SHORT_WATCHLIST_DAILY,
    TOSS_SHORT_SOURCE_SCOPE,
    TOSS_SHORT_WATCHLIST,
    TOSS_SHORT_WATCHLIST_VERSION,
)
from stock_data.contracts.global_market import FRED_DEXJPUS_IDENTITY
from stock_data.gui.query import LocalParquetQuery
from stock_data.gui.korean_equity_nxt_session import classify_korean_equity_nxt_timestamp
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.expected_latest import resolve_expected_latest
from stock_data.orchestration.automatic_fallback import RoutePolicy
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationError,
    CurrentObservationFileStore,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
    ObservationTimestampBasis,
)
from stock_data.orchestration.global_market_15m import reviewed_native_scope
from stock_data.orchestration.toss_short_watchlist_daily import (
    validate_staged_watchlist,
    validate_watchlist_dataset,
)
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.kospi200_constituent_breadth import validate_kospi200_breadth_daily
from stock_data.validation.kr_index_fundamental_daily import (
    validate_kr_index_fundamental_daily,
)
from stock_data.validation.ls_t1633 import validate_ls_t1633_program_trading
from stock_data.validation.market_15m import audit_market_15m_bars, validate_market_price_15m
from stock_data.validation.market_60m import validate_market_price_60m


PERIOD_ROWS = {
    "20D": 20,
    "60D": 60,
    "120D": 120,
    "1Y": 252,
    "3Y": 756,
    "5Y": 1260,
    "10Y": 2520,
}
DASHBOARD_CHART_COVERAGE_ATTR = "dashboard_chart_coverage"
# Dashboard labels deliberately map to retained provider identifiers.  SOXX is
# an independent ETF series; it must remain unavailable when the symbol is not
# retained rather than being substituted with SOX or another semiconductor
# index.
DASHBOARD_ASSETS = {
    "KOSPI": {"kind": "kr", "symbol": "KOSPI", "label": "KOSPI"},
    "KOSDAQ": {"kind": "kr", "symbol": "KOSDAQ", "label": "KOSDAQ"},
    "SP500": {"kind": "global", "symbol": "SP500", "label": "S&P 500"},
    "NASDAQ": {"kind": "global", "symbol": "NASDAQ_COMPOSITE", "label": "NASDAQ"},
    "NDX": {"kind": "global", "symbol": "NASDAQ100", "label": "NDX / NASDAQ-100"},
    "SOXX": {"kind": "etf", "symbol": "SOXX", "label": "SOXX"},
    "NQ_FUTURES": {
        "kind": "futures", "symbol": "NASDAQ100_FUTURES",
        "label": "나스닥100 선물 (Yahoo 연속)",
    },
    "GOLD": {"kind": "futures", "symbol": "GOLD", "label": "금 (Yahoo 연속)"},
    "WTI": {"kind": "futures", "symbol": "WTI_CRUDE_OIL", "label": "WTI (Yahoo 연속)"},
}
ASSET_HEALTH_DATASETS = {
    "KOSPI": "kr_index_daily", "KOSDAQ": "kr_index_daily",
    "SP500": "global_index_price_daily", "NASDAQ": "global_index_price_daily",
    "NDX": "global_index_price_daily", "SOXX": "global_etf_price_daily",
    "NQ_FUTURES": "global_commodity_futures_daily",
    "GOLD": "global_commodity_futures_daily", "WTI": "global_commodity_futures_daily",
}
DISPLAYABLE_FRESHNESS = frozenset({"CURRENT", "EXPECTED_LAG"})

# This GUI reader has no adapter or transport.  It only selects the exact
# UR-118 local projection which a separately authorized LS operation may have
# atomically retained.
LS_T8412_CURRENT_OBSERVATION_PATH = Path("data/state/current_observations/ls_t8412_current.json")
LS_T8412_CURRENT_ROUTE = CurrentObservationRoute(
    fallback_policy=RoutePolicy(
        route_id="ls-t8412-current:XKRX:005930",
        primary_provider="LS_OPENAPI",
        primary_route="LS_OPENAPI:/stock/chart:t8412",
        fallback_provider="UNAVAILABLE",
        fallback_upstream_provider="UNAVAILABLE",
        fallback_route="UNAVAILABLE",
        fallback_enabled=False,
    ),
    identity=ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930"),
    interval_precedence=(ObservationInterval.MINUTES_15,),
)
LS_T8412_CURRENT_SAFE_REASON = "LS_T8412_CURRENT_15M_UNAVAILABLE"
_LS_T8412_CURRENT_SOURCE_DATE = "2026-08-21"
TOSS_DOMESTIC_UR246_PROVENANCE = (
    "Toss domestic recurring 30-minute operation; display-only; PIT-blocked."
)
TOSS_DOMESTIC_UR246_SAFE_REASON = "TOSS_DOMESTIC_UR246_CURRENT_UNAVAILABLE"


def _toss_domestic_ur246_path(symbol: str) -> Path:
    return Path(f"data/state/current_observations/toss_{symbol.lower()}_ur246.json")


def _toss_domestic_ur246_route(symbol: str) -> CurrentObservationRoute:
    is_index = symbol in {"KOSPI", "KOSDAQ"}
    route_id = (
        f"toss-market-price:{symbol}:snapshot:PROVISIONAL"
        if is_index else
        f"toss-stock-price:{symbol}:snapshot:PROVISIONAL:TOSS_ACTIVE_SESSION_60M"
    )
    source_route = (
        "/api/v1/market-indicators/prices"
        if is_index else "/api/v1/prices:TOSS_ACTIVE_SESSION_60M"
    )
    identity = (
        ObservationIdentity("TOSS_MARKET_PRICE_SNAPSHOT", "XKRX", symbol)
        if is_index else ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", symbol)
    )
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=route_id,
            primary_provider="tossinvest_open_api",
            primary_route=source_route,
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=identity,
        interval_precedence=(ObservationInterval.SNAPSHOT,),
    )


def load_toss_domestic_ur246_current_observation(
    root: Path, *, symbol: str,
) -> tuple[CurrentObservation | None, str]:
    """Read one exact recurring Toss projection without invoking the provider."""
    if symbol not in {"KOSPI", "KOSDAQ", "000660", "005930"}:
        return None, f"{TOSS_DOMESTIC_UR246_SAFE_REASON}: unsupported identity."
    route = _toss_domestic_ur246_route(symbol)
    try:
        observation = CurrentObservationFileStore(
            Path(root) / _toss_domestic_ur246_path(symbol)
        ).select(route)
    except CurrentObservationError:
        return None, f"{TOSS_DOMESTIC_UR246_SAFE_REASON}: local typed state is malformed."
    if observation is None:
        return None, f"{TOSS_DOMESTIC_UR246_SAFE_REASON}: no accepted {symbol} observation was retained."
    expected_unit = "index points" if symbol in {"KOSPI", "KOSDAQ"} else "KRW per share"
    try:
        provider_time = pd.Timestamp(observation.provider_timestamp_utc)
    except (TypeError, ValueError):
        return None, f"{TOSS_DOMESTIC_UR246_SAFE_REASON}: provider timestamp is invalid."
    if (
        observation.route_id != route.route_id
        or observation.identity != route.identity
        or observation.interval is not ObservationInterval.SNAPSHOT
        or observation.unit != expected_unit
        or observation.provider != "tossinvest_open_api"
        or observation.upstream_provider != "tossinvest_open_api"
        or observation.source_route != route.fallback_policy.primary_route
        or observation.finality is not ObservationFinality.PROVISIONAL
        or not observation.display_only
        or observation.pit_safe
        or (
            symbol == "005930"
            and observation.timestamp_basis
            is not ObservationTimestampBasis.PROVIDER_TIMESTAMP
        )
        or not np.isfinite(observation.value)
        or observation.value <= 0
        or provider_time.tzinfo is None
    ):
        return None, (
            f"{TOSS_DOMESTIC_UR246_SAFE_REASON}: retained state does not match "
            f"the exact {symbol} recurring contract."
        )
    return observation, TOSS_DOMESTIC_UR246_PROVENANCE
NAVER_WEB_000660_CURRENT_OBSERVATION_PATH = Path(
    "data/state/current_observations/naver_web_000660_current.json"
)
NAVER_WEB_000660_CURRENT_ROUTE = CurrentObservationRoute(
    fallback_policy=RoutePolicy(
        route_id="naver-web-current:XKRX:000660",
        primary_provider="NAVER_FINANCE_WEB",
        primary_route="NAVER_WEB:/api/stock/000660/basic",
        fallback_provider="UNAVAILABLE",
        fallback_upstream_provider="UNAVAILABLE",
        fallback_route="UNAVAILABLE",
        fallback_enabled=False,
    ),
    identity=ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "000660"),
    interval_precedence=(ObservationInterval.SNAPSHOT,),
)
NAVER_WEB_000660_CURRENT_SAFE_REASON = "NAVER_WEB_000660_CURRENT_UNAVAILABLE"
NAVER_WEB_000660_PROVENANCE_WARNING = (
    "Undocumented Naver public-web; local personal display only; redistribution "
    "unverified; PIT-blocked; pilot observation; exact manifest-window collector "
    "enabled; no generic/high-frequency polling."
)
NASDAQ_SOXX_INFO_CURRENT_OBSERVATION_PATH = Path(
    "data/state/current_observations/nasdaq_soxx_info_current.json"
)
NASDAQ_SOXX_INFO_CURRENT_ROUTE = CurrentObservationRoute(
    fallback_policy=RoutePolicy(
        route_id="nasdaq-soxx-info-api:NASDAQ:SOXX",
        primary_provider="NASDAQ_OFFICIAL",
        primary_route="NASDAQ_OFFICIAL:api.nasdaq.com/api/quote/SOXX/info?assetclass=etf",
        fallback_provider="UNAVAILABLE",
        fallback_upstream_provider="UNAVAILABLE",
        fallback_route="UNAVAILABLE",
        fallback_enabled=False,
    ),
    identity=ObservationIdentity("US_ETF_CURRENT", "NASDAQ", "SOXX"),
    interval_precedence=(ObservationInterval.SNAPSHOT,),
)
NASDAQ_SOXX_INFO_CURRENT_SAFE_REASON = "NASDAQ_SOXX_INFO_CURRENT_UNAVAILABLE"
NASDAQ_SOXX_INFO_CURRENT_PROVENANCE = (
    "Nasdaq official retained current snapshot; route-local USD-per-ETF-share "
    "contract; display-only; PIT-blocked."
)
NAVER_MOBILE_BASIC_000660_UR199_OBSERVATION_PATH = Path(
    "data/state/current_observations/naver_mobile_basic_000660_ur199.json"
)
NAVER_MOBILE_BASIC_005930_UR199_OBSERVATION_PATH = Path(
    "data/state/current_observations/naver_mobile_basic_005930_ur199.json"
)
NAVER_MOBILE_BASIC_UR199_PROVENANCE_WARNING = (
    "Naver mobile-basic public manifest-window observation; display-only; PIT-blocked; "
    "no generic/high-frequency polling."
)


def _naver_mobile_basic_ur199_route(symbol: str) -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=f"naver-mobile-basic-current:XKRX:{symbol}",
            primary_provider="NAVER_FINANCE_WEB",
            primary_route=f"NAVER_FINANCE_WEB:m.stock.naver.com/api/stock/{symbol}/basic",
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", symbol),
        interval_precedence=(ObservationInterval.SNAPSHOT,),
    )


NAVER_MOBILE_BASIC_000660_UR199_ROUTE = _naver_mobile_basic_ur199_route("000660")
NAVER_MOBILE_BASIC_005930_UR199_ROUTE = _naver_mobile_basic_ur199_route("005930")
NAVER_MOBILE_BASIC_UR199_SAFE_REASON = "NAVER_MOBILE_BASIC_UR199_CURRENT_UNAVAILABLE"
TOSS_000660_NXT_CLOSE_UR240_OBSERVATION_PATH = Path(
    "data/state/current_observations/toss_000660_nxt_session_close_ur240.json"
)
TOSS_000660_NXT_CLOSE_UR240_ROUTE = CurrentObservationRoute(
    fallback_policy=RoutePolicy(
        route_id="toss-stock-price:000660:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
        primary_provider="tossinvest_open_api",
        primary_route="/api/v1/prices",
        fallback_provider="UNAVAILABLE",
        fallback_upstream_provider="UNAVAILABLE",
        fallback_route="UNAVAILABLE",
        fallback_enabled=False,
    ),
    identity=ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "000660"),
    interval_precedence=(ObservationInterval.SNAPSHOT,),
)
TOSS_NXT_CLOSE_UR240_PROVENANCE_WARNING = (
    "Toss NXT close inferred only from the route-local exclusive 19:55-20:00 KST "
    "time window; venue/session is not provider-declared; NOT_LIVE; display-only; PIT-blocked."
)
TOSS_NXT_CLOSE_UR240_SAFE_REASON = "TOSS_NXT_CLOSE_UR240_CURRENT_UNAVAILABLE"
TOSS_005930_NXT_CLOSE_UR241_OBSERVATION_PATH = Path(
    "data/state/current_observations/toss_005930_nxt_close_ur241.json"
)
TOSS_005930_NXT_CLOSE_UR241_ROUTE = CurrentObservationRoute(
    fallback_policy=RoutePolicy(
        route_id="toss-stock-price:005930:snapshot:PROVISIONAL:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
        primary_provider="tossinvest_open_api",
        primary_route="/api/v1/prices:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
        fallback_provider="UNAVAILABLE",
        fallback_upstream_provider="UNAVAILABLE",
        fallback_route="UNAVAILABLE",
        fallback_enabled=False,
    ),
    identity=ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930"),
    interval_precedence=(ObservationInterval.SNAPSHOT,),
)
TOSS_NXT_CLOSE_UR241_SAFE_REASON = "TOSS_NXT_CLOSE_UR241_CURRENT_UNAVAILABLE"
NAVER_MOBILE_HOME_CURRENT_OBSERVATION_PATH = Path(
    "data/state/current_observations/naver_mobile_home_current.json"
)
NAVER_MOBILE_HOME_CURRENT_PROVENANCE_WARNING = (
    "Undocumented Naver public-web retained recovery; local personal display only; "
    "redistribution unverified; PIT-blocked."
)
_NAVER_MOBILE_HOME_CURRENT_SPECS = {
    "KOSPI": ("KR_INDEX_CURRENT", "XKRX", "KOSPI", "index points"),
    "KOSDAQ": ("KR_INDEX_CURRENT", "XKRX", "KOSDAQ", "index points"),
    "USD_KRW": ("FX_CURRENT", "KRW", "USD_KRW", "KRW per USD"),
}
GLOBAL60M_UR232_CURRENT_SPECS = {
    "KOSPI_CURRENT_60M": ("XKRX", "^KS11", "index points", "KOSPI index"),
    "KOSDAQ_CURRENT_60M": ("XKRX", "^KQ11", "index points", "KOSDAQ index"),
    "USD_KRW_60M": ("GLOBAL_FX", "KRW=X", "KRW per USD", "FX indicative KRW per USD"),
    "UST2_FUTURES_60M": ("CBOT", "ZT=F", "provider native continuous futures price", "US 2Y continuous futures price; never a yield"),
    "UST10_FUTURES_60M": ("CBOT", "ZN=F", "provider native continuous futures price", "US 10Y continuous futures price; never a yield"),
    "UST30_FUTURES_60M": ("CBOT", "ZB=F", "provider native continuous futures price", "US 30Y continuous futures price; never a yield"),
    "SP500_CURRENT_60M": ("XNYS", "^GSPC", "index points", "S&P 500 index"),
    "NASDAQ_CURRENT_60M": ("XNAS", "^IXIC", "index points", "Nasdaq Composite index"),
    "NQ_FUTURES_CURRENT_60M": ("CME", "NQ=F", "index points", "Nasdaq-100 continuous futures"),
    "SOXX_CURRENT_60M": ("XNAS", "SOXX", "USD per share", "SOXX semiconductor ETF"),
    "GOLD_CURRENT_60M": ("COMEX", "GC=F", "provider native continuous futures price", "GOLD"),
    "WTI_CURRENT_60M": ("NYMEX", "CL=F", "provider native continuous futures price", "WTI"),
    "BITCOIN_CURRENT_60M": ("CRYPTO", "BTC-USD", "USD per BTC", "BITCOIN"),
}
GLOBAL60M_UR232_PROVENANCE = "Yahoo retained Landing API-zero recovery; display-only; PIT-blocked."
GLOBAL60M_UR232_SAFE_REASON = "GLOBAL60M_UR232_CURRENT_UNAVAILABLE"
GLOBAL60M_CARD_SESSION_SPECS = {
    "KOSPI": ("KOSPI_CURRENT_60M", "^KS11"),
    "KOSDAQ": ("KOSDAQ_CURRENT_60M", "^KQ11"),
    "NQ_FUTURES": ("NQ_FUTURES_CURRENT_60M", "NQ=F"),
    "NASDAQ": ("NASDAQ_CURRENT_60M", "^IXIC"),
    "SP500": ("SP500_CURRENT_60M", "^GSPC"),
    "SOXX": ("SOXX_CURRENT_60M", "SOXX"),
    "GOLD": ("GOLD_CURRENT_60M", "GC=F"),
    "WTI": ("WTI_CURRENT_60M", "CL=F"),
    "BITCOIN": ("BITCOIN_CURRENT_60M", "BTC-USD"),
    "USD_KRW_60M": ("USD_KRW_60M", "KRW=X"),
}


def _global60m_scheduled_current_route(coverage_id: str) -> CurrentObservationRoute:
    market, symbol, _unit, _label = GLOBAL60M_UR232_CURRENT_SPECS[coverage_id]
    route_symbol = symbol[1:] if symbol.startswith("^") else symbol
    route_id = f"yahoo-market-current:{market}:{route_symbol}"
    source_route = f"YAHOO_CHART_30M:{symbol}"
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=route_id,
            primary_provider="YAHOO",
            primary_route=source_route,
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=ObservationIdentity("MARKET_PRICE_CURRENT", market, symbol),
        interval_precedence=(ObservationInterval.MINUTES_30,),
    )


def _load_global60m_scheduled_current(root: Path, coverage_id: str) -> CurrentObservation | None:
    market, symbol, _unit, _label = GLOBAL60M_UR232_CURRENT_SPECS[coverage_id]
    route_symbol = symbol[1:] if symbol.startswith("^") else symbol
    routes = (
        _global60m_scheduled_current_route(coverage_id),
        CurrentObservationRoute(
            fallback_policy=RoutePolicy(
                route_id=f"yahoo-global60m-current:{market}:{route_symbol}",
                primary_provider="YAHOO",
                primary_route=f"YAHOO_CHART_GLOBAL60M:{symbol}",
                fallback_provider="UNAVAILABLE",
                fallback_upstream_provider="UNAVAILABLE",
                fallback_route="UNAVAILABLE",
                fallback_enabled=False,
            ),
            identity=ObservationIdentity("MARKET_PRICE_60M_CURRENT", market, symbol),
            interval_precedence=(ObservationInterval.MINUTES_60,),
        ),
    )
    store = CurrentObservationFileStore(
        Path(root) / "data/state/current_observations/global60m_current" / f"{coverage_id.lower()}.json"
    )
    observation = None
    route = routes[0]
    try:
        for candidate in routes:
            selected = store.select(candidate)
            if selected is not None:
                route, observation = candidate, selected
                break
    except CurrentObservationError:
        return None
    if observation is None:
        return None
    _market, _symbol, unit, _label = GLOBAL60M_UR232_CURRENT_SPECS[coverage_id]
    if (
        observation.route_id != route.route_id
        or observation.identity != route.identity
        or observation.interval not in {ObservationInterval.MINUTES_30, ObservationInterval.MINUTES_60}
        or observation.unit != unit
        or observation.provider != "YAHOO"
        or observation.upstream_provider != "YAHOO_CHART_API"
        or observation.source_route != route.fallback_policy.primary_route
        or observation.finality is not ObservationFinality.AS_RETRIEVED
        or not observation.display_only or observation.pit_safe
        or not np.isfinite(observation.value) or observation.value <= 0
    ):
        return None
    return observation


def _load_yahoo_native15m_current(root: Path, series_id: str) -> CurrentObservation | None:
    """Read one typed Yahoo native-15m projection written by the unified task."""
    if series_id not in {"^FVX", "^TNX", "^TYX", "^VIX"}:
        return None
    unit = (
        "index points" if series_id == "^VIX"
        else "provider native quote index points"
    )
    market = "CBOE"
    symbol = series_id[1:]
    route_id = f"yahoo-market-current:{market}:{symbol}"
    source_route = f"YAHOO_CHART_15M:{series_id}"
    route = CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=route_id,
            primary_provider="YAHOO",
            primary_route=source_route,
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=ObservationIdentity("MARKET_PRICE_CURRENT", market, series_id),
        interval_precedence=(ObservationInterval.MINUTES_15,),
    )
    path = (
        Path(root) / "data/state/current_observations/yahoo_native15m_current"
        / f"{series_id.replace('^', 'idx').lower()}.json"
    )
    try:
        observation = CurrentObservationFileStore(path).select(route)
    except CurrentObservationError:
        return None
    if observation is None or (
        observation.route_id != route_id
        or observation.identity != route.identity
        or observation.interval is not ObservationInterval.MINUTES_15
        or observation.unit != unit
        or observation.provider != "YAHOO"
        or observation.upstream_provider != "YAHOO_CHART_API"
        or observation.source_route != source_route
        or observation.finality is not ObservationFinality.AS_RETRIEVED
        or not observation.display_only
        or observation.pit_safe
        or not np.isfinite(observation.value)
        or observation.value <= 0
    ):
        return None
    return observation


def _load_global60m_current_comparison(
    root: Path, coverage_id: str, observation: CurrentObservation,
) -> tuple[float | None, float | None]:
    """Read a comparison only when it exactly matches the displayed observation."""
    try:
        payload = json.loads((
            Path(root) / "data/state/current_observations/global60m_current"
            / f"{coverage_id.lower()}.comparison.json"
        ).read_text(encoding="utf-8"))
        current = float(payload["current_close"])
        previous = float(payload["previous_session_close"])
        change = float(payload["change"])
        change_pct = float(payload["change_pct"])
        if (
            payload.get("schema_version") != 1
            or payload.get("series_id") != coverage_id
            or payload.get("provider_symbol") != observation.identity.symbol
            or payload.get("basis") != "PREVIOUS_PROVIDER_SESSION_CLOSE"
            or pd.Timestamp(payload["current_bar_end_utc"]) != pd.Timestamp(observation.provider_timestamp_utc)
            or not np.isclose(current, observation.value, rtol=0, atol=1e-10)
            or not all(np.isfinite(value) for value in (previous, change, change_pct))
            or previous <= 0
            or not np.isclose(change, current - previous, rtol=0, atol=1e-10)
            or not np.isclose(change_pct, change / previous * 100, rtol=0, atol=1e-10)
        ):
            raise ValueError("comparison mismatch")
        return change, change_pct
    except (FileNotFoundError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None, None


@dataclass(frozen=True)
class NaverMobileHomeCurrentObservation:
    """Typed local-only record from UR-166's retained recovery state."""

    coverage_id: str
    dataset_id: str
    market: str
    symbol: str
    value: float
    unit: str
    provider_timestamp_utc: str
    retrieved_at_utc: str
    route_id: str
    source_route: str


def load_naver_mobile_home_current_observations(
    root: Path,
) -> tuple[dict[str, NaverMobileHomeCurrentObservation], str]:
    """Read UR-166's exact retained rows; malformed state produces no values.

    This is deliberately a GUI local-state reader. It neither imports the
    provider adapter nor invokes transport.
    """
    try:
        payload = json.loads(
            (Path(root) / NAVER_MOBILE_HOME_CURRENT_OBSERVATION_PATH).read_text(
                encoding="utf-8"
            )
        )
        rows = payload["observations"]
        if payload.get("schema_version") != 1 or not isinstance(rows, list):
            raise ValueError("unexpected state schema")
        selected: dict[str, NaverMobileHomeCurrentObservation] = {}
        for coverage_id, (dataset_id, market, symbol, unit) in _NAVER_MOBILE_HOME_CURRENT_SPECS.items():
            matches = [
                row for row in rows
                if isinstance(row, dict)
                and row.get("identity") == {
                    "dataset_id": dataset_id, "market": market, "symbol": symbol,
                }
            ]
            if len(matches) != 1:
                raise ValueError(f"missing or duplicate {coverage_id} identity")
            row = matches[0]
            value = float(row["value"])
            timestamp = str(row["provider_timestamp_utc"])
            retrieved = str(row["retrieved_at_utc"])
            if (
                not np.isfinite(value)
                or value <= 0
                or row.get("route_id") != f"naver-mobile-home-current:{market}:{symbol}"
                or row.get("interval") != "snapshot"
                or row.get("unit") != unit
                or row.get("provider") != "NAVER_FINANCE_WEB"
                or row.get("upstream_provider") != "NAVER_FINANCE_WEB"
                or row.get("source_route") != "NAVER_WEB:/"
                or row.get("finality") != "PROVISIONAL"
                or row.get("display_only") is not True
                or row.get("pit_safe") is not False
                or pd.Timestamp(timestamp).tzinfo is None
                or pd.Timestamp(retrieved).tzinfo is None
            ):
                raise ValueError(f"invalid {coverage_id} contract")
            selected[coverage_id] = NaverMobileHomeCurrentObservation(
                coverage_id=coverage_id, dataset_id=dataset_id, market=market,
                symbol=symbol, value=value, unit=unit,
                provider_timestamp_utc=timestamp, retrieved_at_utc=retrieved,
                route_id=str(row["route_id"]), source_route=str(row["source_route"]),
            )
        return selected, NAVER_MOBILE_HOME_CURRENT_PROVENANCE_WARNING
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
        return {}, "NAVER_MOBILE_HOME_CURRENT_UNAVAILABLE: no exact valid retained recovery state."


def load_global60m_ur232_current_observations(root: Path) -> tuple[dict[str, CurrentObservation], str]:
    """Read only UR-232's exact local recovery envelopes; never Yahoo transport."""
    scheduled = {
        coverage_id: observation
        for coverage_id in GLOBAL60M_UR232_CURRENT_SPECS
        if (observation := _load_global60m_scheduled_current(root, coverage_id)) is not None
    }
    try:
        from stock_data.orchestration.global_market_60m_ur232_recovery import (
            RECOVERY_CLASSIFICATION, RUN_ID, read_observation,
        )
        values: dict[str, CurrentObservation] = {}
        for coverage_id, (market, symbol, unit, _label) in GLOBAL60M_UR232_CURRENT_SPECS.items():
            envelope_path = Path(root) / "data/state/current_observations/global60m_ur232" / f"{coverage_id.lower()}.json"
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
            landing = envelope.get("immutable_landing") if isinstance(envelope, dict) else None
            observation = read_observation(root, coverage_id)
            if (
                observation.identity != ObservationIdentity("MARKET_PRICE_60M_CURRENT", market, symbol)
                or observation.route_id != f"yahoo-global60m-ur232:{market}:{symbol}"
                or observation.interval is not ObservationInterval.MINUTES_60
                or observation.unit != unit or observation.provider != "YAHOO"
                or observation.upstream_provider != "YAHOO_CHART_API"
                or observation.source_route != "YAHOO_CHART_GLOBAL60M_RETAINED_LANDING_API_ZERO_RECOVERY"
                or observation.finality is not ObservationFinality.AS_RETRIEVED
                or not observation.display_only or observation.pit_safe or not np.isfinite(observation.value)
                or observation.value <= 0 or not isinstance(landing, dict)
                or envelope.get("recovery_classification") != RECOVERY_CLASSIFICATION
                or landing.get("run_id") != RUN_ID or not isinstance(landing.get("body_path"), str)
                or not isinstance(landing.get("body_sha256"), str) or len(landing["body_sha256"]) != 64
            ):
                raise ValueError("UR-232 global 60m envelope contract mismatch")
            values.setdefault(coverage_id, observation)
        values.update(scheduled)
        return values, (
            "Yahoo scheduled completed-30m-bar current projection; independent from historical promotion; "
            "display-only; PIT-blocked."
            if scheduled else GLOBAL60M_UR232_PROVENANCE
        )
    except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        if scheduled:
            return scheduled, (
                "Yahoo scheduled completed-30m-bar current projection; independent from historical promotion; "
                "display-only; PIT-blocked."
            )
        return {}, f"{GLOBAL60M_UR232_SAFE_REASON}: no exact valid retained-Landing local observation."


def _ls_t8412_current_source_date(observation: CurrentObservation) -> str:
    """Return the provider KST label date without treating it as availability."""
    return datetime.fromisoformat(observation.provider_timestamp_utc).astimezone(
        ZoneInfo("Asia/Seoul")
    ).date().isoformat()


def load_ls_t8412_current_observation(root: Path) -> tuple[CurrentObservation | None, str]:
    """Read one exact retained LS current observation, fail-closed otherwise."""
    try:
        observation = CurrentObservationFileStore(
            Path(root) / LS_T8412_CURRENT_OBSERVATION_PATH
        ).select(LS_T8412_CURRENT_ROUTE)
    except CurrentObservationError:
        return None, f"{LS_T8412_CURRENT_SAFE_REASON}: local typed state is malformed."
    if observation is None:
        return None, f"{LS_T8412_CURRENT_SAFE_REASON}: no accepted typed observation was retained."
    try:
        source_date = _ls_t8412_current_source_date(observation)
    except (TypeError, ValueError):
        return None, f"{LS_T8412_CURRENT_SAFE_REASON}: provider timestamp is invalid."
    if (
        observation.route_id != LS_T8412_CURRENT_ROUTE.route_id
        or observation.identity != LS_T8412_CURRENT_ROUTE.identity
        or observation.interval is not ObservationInterval.MINUTES_15
        or observation.unit != "provider_native_price"
        or observation.provider != "LS_OPENAPI"
        or observation.upstream_provider != "LS_OPENAPI"
        or observation.source_route != "LS_OPENAPI:/stock/chart:t8412"
        or observation.finality is not ObservationFinality.AS_RETRIEVED
        or not observation.display_only
        or observation.pit_safe
        or observation.value <= 0
        or source_date != _LS_T8412_CURRENT_SOURCE_DATE
    ):
        return None, f"{LS_T8412_CURRENT_SAFE_REASON}: retained state does not match the exact 005930 15-minute contract."
    return observation, ""


def load_naver_web_000660_current_observation(root: Path) -> tuple[CurrentObservation | None, str]:
    """Read only UR-145's exact retained web observation, never its endpoint."""
    try:
        observation = CurrentObservationFileStore(
            Path(root) / NAVER_WEB_000660_CURRENT_OBSERVATION_PATH
        ).select(NAVER_WEB_000660_CURRENT_ROUTE)
    except CurrentObservationError:
        return None, f"{NAVER_WEB_000660_CURRENT_SAFE_REASON}: local typed state is malformed."
    if observation is None:
        return None, f"{NAVER_WEB_000660_CURRENT_SAFE_REASON}: no accepted typed observation was retained."
    if (
        observation.route_id != NAVER_WEB_000660_CURRENT_ROUTE.route_id
        or observation.identity != NAVER_WEB_000660_CURRENT_ROUTE.identity
        or observation.interval is not ObservationInterval.SNAPSHOT
        or observation.unit != "KRW per share"
        or observation.provider != "NAVER_FINANCE_WEB"
        or observation.upstream_provider != "NAVER_FINANCE_WEB"
        or observation.source_route != "NAVER_WEB:/api/stock/000660/basic"
        or observation.finality is not ObservationFinality.PROVISIONAL
        or not observation.display_only
        or observation.pit_safe
        or observation.value <= 0
    ):
        return None, f"{NAVER_WEB_000660_CURRENT_SAFE_REASON}: retained state does not match the exact domestic web contract."
    return observation, NAVER_WEB_000660_PROVENANCE_WARNING


def load_nasdaq_soxx_info_current_observation(root: Path) -> tuple[CurrentObservation | None, str]:
    """Read only UR-190's exact retained SOXX observation, never its endpoint."""
    try:
        observation = CurrentObservationFileStore(
            Path(root) / NASDAQ_SOXX_INFO_CURRENT_OBSERVATION_PATH
        ).select(NASDAQ_SOXX_INFO_CURRENT_ROUTE)
    except CurrentObservationError:
        return None, f"{NASDAQ_SOXX_INFO_CURRENT_SAFE_REASON}: local typed state is malformed."
    if observation is None:
        return None, f"{NASDAQ_SOXX_INFO_CURRENT_SAFE_REASON}: no accepted typed observation was retained."
    try:
        provider_time = pd.Timestamp(observation.provider_timestamp_utc)
    except (TypeError, ValueError):
        return None, f"{NASDAQ_SOXX_INFO_CURRENT_SAFE_REASON}: provider timestamp is invalid."
    if (
        observation.route_id != NASDAQ_SOXX_INFO_CURRENT_ROUTE.route_id
        or observation.identity != NASDAQ_SOXX_INFO_CURRENT_ROUTE.identity
        or observation.interval is not ObservationInterval.SNAPSHOT
        or observation.unit != "USD per share"
        or observation.provider != "NASDAQ_OFFICIAL"
        or observation.upstream_provider != "NASDAQ_OFFICIAL"
        or observation.source_route != "NASDAQ_OFFICIAL:api.nasdaq.com/api/quote/SOXX/info?assetclass=etf"
        or observation.finality is not ObservationFinality.PROVISIONAL
        or not observation.display_only
        or observation.pit_safe
        or not np.isfinite(observation.value)
        or observation.value <= 0
        or provider_time.tzinfo is None
    ):
        return None, (
            f"{NASDAQ_SOXX_INFO_CURRENT_SAFE_REASON}: retained state does not match "
            "the exact SOXX ETF Nasdaq current contract."
        )
    return observation, NASDAQ_SOXX_INFO_CURRENT_PROVENANCE


def load_naver_mobile_basic_ur199_current_observation(
    root: Path, *, symbol: str,
) -> tuple[CurrentObservation | None, str]:
    """Read one exact UR-199 future-session projection with no provider access."""
    specs = {
        "000660": (NAVER_MOBILE_BASIC_000660_UR199_OBSERVATION_PATH, NAVER_MOBILE_BASIC_000660_UR199_ROUTE),
        "005930": (NAVER_MOBILE_BASIC_005930_UR199_OBSERVATION_PATH, NAVER_MOBILE_BASIC_005930_UR199_ROUTE),
    }
    spec = specs.get(symbol)
    if spec is None:
        return None, f"{NAVER_MOBILE_BASIC_UR199_SAFE_REASON}: symbol is outside the exact future-session scope."
    path, route = spec
    try:
        observation = CurrentObservationFileStore(Path(root) / path).select(route)
    except CurrentObservationError:
        return None, f"{NAVER_MOBILE_BASIC_UR199_SAFE_REASON}: local typed state is malformed."
    if observation is None:
        return None, f"{NAVER_MOBILE_BASIC_UR199_SAFE_REASON}: no accepted {symbol} observation was retained."
    try:
        provider_time = pd.Timestamp(observation.provider_timestamp_utc)
    except (TypeError, ValueError):
        return None, f"{NAVER_MOBILE_BASIC_UR199_SAFE_REASON}: provider timestamp is invalid."
    if (
        observation.route_id != route.route_id
        or observation.identity != route.identity
        or observation.interval is not ObservationInterval.SNAPSHOT
        or observation.unit != "KRW per share"
        or observation.provider != "NAVER_FINANCE_WEB"
        or observation.upstream_provider != "NAVER_FINANCE_WEB"
        or observation.source_route != route.fallback_policy.primary_route
        or observation.finality is not ObservationFinality.PROVISIONAL
        or not observation.display_only
        or observation.pit_safe
        or not np.isfinite(observation.value)
        or observation.value <= 0
        or provider_time.tzinfo is None
    ):
        return None, (
            f"{NAVER_MOBILE_BASIC_UR199_SAFE_REASON}: retained state does not match "
            f"the exact {symbol} mobile-basic current contract."
        )
    return observation, NAVER_MOBILE_BASIC_UR199_PROVENANCE_WARNING


def load_toss_000660_nxt_close_ur240_observation(
    root: Path,
) -> tuple[CurrentObservation | None, str]:
    """Read UR-240's exact inferred NXT-close state; never Toss transport."""
    try:
        observation = CurrentObservationFileStore(
            Path(root) / TOSS_000660_NXT_CLOSE_UR240_OBSERVATION_PATH
        ).select(TOSS_000660_NXT_CLOSE_UR240_ROUTE)
    except CurrentObservationError:
        return None, f"{TOSS_NXT_CLOSE_UR240_SAFE_REASON}: local typed state is malformed."
    if observation is None:
        return None, f"{TOSS_NXT_CLOSE_UR240_SAFE_REASON}: no accepted typed close observation was retained."
    try:
        provider_time = pd.Timestamp(observation.provider_timestamp_utc)
    except (TypeError, ValueError):
        return None, f"{TOSS_NXT_CLOSE_UR240_SAFE_REASON}: provider timestamp is invalid."
    if (
        observation.route_id != TOSS_000660_NXT_CLOSE_UR240_ROUTE.route_id
        or observation.identity != TOSS_000660_NXT_CLOSE_UR240_ROUTE.identity
        or observation.interval is not ObservationInterval.SNAPSHOT
        or observation.unit != "KRW per share"
        or observation.provider != "tossinvest_open_api"
        or observation.upstream_provider != "tossinvest_open_api"
        or observation.source_route != "/api/v1/prices"
        or observation.finality is not ObservationFinality.POST_CLOSE_SNAPSHOT
        or not observation.display_only
        or observation.pit_safe
        or not np.isfinite(observation.value)
        or observation.value <= 0
        or provider_time.tzinfo is None
    ):
        return None, (
            f"{TOSS_NXT_CLOSE_UR240_SAFE_REASON}: retained state does not match "
            "the exact inferred 000660 NXT-close contract."
        )
    return observation, TOSS_NXT_CLOSE_UR240_PROVENANCE_WARNING


def load_toss_005930_nxt_close_ur241_observation(
    root: Path,
) -> tuple[CurrentObservation | None, str]:
    """Read UR-241's exact inferred NXT-close state; never Toss transport."""
    try:
        observation = CurrentObservationFileStore(
            Path(root) / TOSS_005930_NXT_CLOSE_UR241_OBSERVATION_PATH
        ).select(TOSS_005930_NXT_CLOSE_UR241_ROUTE)
    except CurrentObservationError:
        return None, f"{TOSS_NXT_CLOSE_UR241_SAFE_REASON}: local typed state is malformed."
    if observation is None:
        return None, f"{TOSS_NXT_CLOSE_UR241_SAFE_REASON}: no accepted typed close observation was retained."
    try:
        provider_time = pd.Timestamp(observation.provider_timestamp_utc)
    except (TypeError, ValueError):
        return None, f"{TOSS_NXT_CLOSE_UR241_SAFE_REASON}: provider timestamp is invalid."
    if (
        observation.route_id != TOSS_005930_NXT_CLOSE_UR241_ROUTE.route_id
        or observation.identity != TOSS_005930_NXT_CLOSE_UR241_ROUTE.identity
        or observation.interval is not ObservationInterval.SNAPSHOT
        or observation.unit != "KRW per share"
        or observation.provider != "tossinvest_open_api"
        or observation.upstream_provider != "tossinvest_open_api"
        or observation.source_route != "/api/v1/prices:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW"
        or observation.finality is not ObservationFinality.PROVISIONAL
        or not observation.display_only
        or observation.pit_safe
        or not np.isfinite(observation.value)
        or observation.value <= 0
        or provider_time.tzinfo is None
    ):
        return None, (
            f"{TOSS_NXT_CLOSE_UR241_SAFE_REASON}: retained state does not match "
            "the exact inferred 005930 NXT-close contract."
        )
    return observation, TOSS_NXT_CLOSE_UR240_PROVENANCE_WARNING


class DashboardDisplayState(str, Enum):
    VALUE = "VALUE"
    REFRESH_REQUIRED = "REFRESH_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    PROHIBITED = "PROHIBITED"


@dataclass(frozen=True)
class DashboardMetricView:
    """One fail-closed, local-only Dashboard value and its display authority."""

    dataset_id: str | None
    series_id: str
    label: str
    value: float | None
    unit: str
    as_of: str | None
    expected_as_of: str | None
    source: str
    freshness: str
    pit_status: str
    pit_label: str
    automation_policy: str
    automation_enabled: bool
    display_state: DashboardDisplayState
    unavailable_reason: str | None
    route: str
    change: float | None = None
    change_pct: float | None = None
    source_timestamp: str | None = None
    delay_status: str | None = None
    completed_bar: bool | None = None
    retrieved_at_utc: str | None = None
    timestamp_basis: str = "PROVIDER_TIMESTAMP"

    @property
    def displays_value(self) -> bool:
        return self.display_state is DashboardDisplayState.VALUE and self.value is not None


@dataclass(frozen=True)
class CurrentObservationCoverageView:
    """One explicit current-observation route shown by the local Dashboard.

    This is a display ledger, not a refresh request.  It deliberately keeps
    provider candidates with no accepted projection numeric-free, so a route's
    priority cannot be mistaken for evidence that it supplied the card value.
    """

    coverage_id: str
    label: str
    value: float | None
    unit: str | None
    provider: str
    route: str
    interval: str
    as_of: str | None
    retrieved_at_utc: str | None
    freshness: str
    finality: str
    display_state: DashboardDisplayState
    unavailable_reason: str | None
    provider_timestamp_utc: str | None = None
    source_route: str | None = None
    display_only: bool | None = None
    pit_safe: bool | None = None
    nxt_session_gate: bool = False
    nxt_session_start_kst: time | None = None
    nxt_venue_inferred: bool = False
    visible_label: str | None = None
    timestamp_basis: str = "PROVIDER_TIMESTAMP"

    @property
    def displays_value(self) -> bool:
        return self.display_state is DashboardDisplayState.VALUE and self.value is not None


@dataclass(frozen=True)
class DashboardSeriesView:
    metric: DashboardMetricView
    frame: pd.DataFrame


@dataclass(frozen=True)
class DashboardSparklineView:
    """One independently gated completed-session card sparkline."""

    asset: str
    lane_id: str | None
    series_id: str | None
    frame: pd.DataFrame
    interval: str
    session_label: str
    session_date: str | None
    visual_window: str
    as_of_kst: str | None
    source_timestamp: str | None
    source: str
    freshness: str
    display_state: DashboardDisplayState
    unavailable_reason: str | None
    reference_value: float | None = None

    @property
    def displays_values(self) -> bool:
        if (
            self.display_state is not DashboardDisplayState.VALUE
            or self.freshness not in DISPLAYABLE_FRESHNESS | {
                "CURRENT_COMPLETED_60M", "CURRENT_COMPLETED_30M", "MARKET_CLOSED_LAST_FINAL",
            }
            or self.interval not in {"15m", "30m", "60m"}
            or len(self.frame) < 2
            or "value" not in self.frame
        ):
            return False
        values = pd.to_numeric(self.frame["value"], errors="coerce")
        return bool(values.notna().sum() >= 2)


@dataclass(frozen=True)
class DashboardAverageComparisonView:
    """Exact completed-daily mean comparisons for one displayed series."""

    series_id: str
    comparison_kind: str
    interval: str
    as_of: str | None
    latest_value: float | None
    mean_5: float | None
    mean_20: float | None
    comparison_5: float | None
    comparison_20: float | None
    coverage_5: tuple[str, str, int] | None
    coverage_20: tuple[str, str, int] | None
    display_state: DashboardDisplayState
    unavailable_reason: str | None
    reason_5: str | None = None
    reason_20: str | None = None

    @property
    def displays_5(self) -> bool:
        return (
            self.display_state is DashboardDisplayState.VALUE
            and self.comparison_5 is not None
            and np.isfinite(self.comparison_5)
        )

    @property
    def displays_20(self) -> bool:
        return (
            self.display_state is DashboardDisplayState.VALUE
            and self.comparison_20 is not None
            and np.isfinite(self.comparison_20)
        )


@dataclass(frozen=True)
class MarketValuationWindowView:
    """One as-of-only calendar-year window for descriptive ratio ranks."""

    window_years: int
    per_percentile: float | None
    pbr_percentile: float | None
    per_observations: int
    pbr_observations: int
    per_baseline_start: str | None
    per_baseline_end: str | None
    pbr_baseline_start: str | None
    pbr_baseline_end: str | None


@dataclass(frozen=True)
class MarketValuationView:
    """Descriptive broad-market valuation at one accepted KRX session."""

    market: str
    index_code: str
    as_of: str | None
    expected_as_of: str | None
    weighted_per: float | None
    weighted_pbr: float | None
    per_mean: float | None
    pbr_mean: float | None
    per_median: float | None
    pbr_median: float | None
    per_percentile: float | None
    pbr_percentile: float | None
    per_observations: int
    pbr_observations: int
    per_baseline_start: str | None
    per_baseline_end: str | None
    pbr_baseline_start: str | None
    pbr_baseline_end: str | None
    baseline_start: str | None
    baseline_end: str | None
    source: str
    display_state: DashboardDisplayState
    unavailable_reason: str | None
    pit_status: str = "NON_PREDICTIVE"
    rolling_windows: tuple[MarketValuationWindowView, ...] = ()

    @property
    def displays_per(self) -> bool:
        return (
            self.display_state is DashboardDisplayState.VALUE
            and self.weighted_per is not None
            and self.per_mean is not None
            and self.per_median is not None
            and self.per_percentile is not None
            and self.per_observations > 0
            and self.per_baseline_start is not None
            and self.per_baseline_end is not None
        )

    @property
    def displays_pbr(self) -> bool:
        return (
            self.display_state is DashboardDisplayState.VALUE
            and self.weighted_pbr is not None
            and self.pbr_mean is not None
            and self.pbr_median is not None
            and self.pbr_percentile is not None
            and self.pbr_observations > 0
            and self.pbr_baseline_start is not None
            and self.pbr_baseline_end is not None
        )


@dataclass(frozen=True)
class MarketInvestorFlowValue:
    """One signed investor-class amount, kept separate by market and period."""

    investor_id: str
    label: str
    latest_value: int | None
    week_to_date_value: int | None


@dataclass(frozen=True)
class MarketInvestorFlowView:
    """Fail-closed local daily/weekly view for exactly one Korean market."""

    dataset_id: str
    market: str
    values: tuple[MarketInvestorFlowValue, ...]
    as_of: str | None
    expected_as_of: str | None
    value_unit: str
    source: str
    source_operation: str | None
    provider_segment: str | None
    freshness: str
    finality: str
    display_state: DashboardDisplayState
    unavailable_reason: str | None
    weekly_unavailable_reason: str | None
    covered_sessions: tuple[str, ...]
    required_sessions: tuple[str, ...]
    missing_sessions: tuple[str, ...]
    partial_week: bool

    @property
    def displays_values(self) -> bool:
        return (
            self.display_state is DashboardDisplayState.VALUE
            and bool(self.values)
            and all(value.latest_value is not None for value in self.values)
        )

    @property
    def weekly_complete_through_as_of(self) -> bool:
        return self.displays_values and not self.missing_sessions and all(
            value.week_to_date_value is not None for value in self.values
        )


@dataclass(frozen=True)
class MarketFundingValue:
    """One retained funding aggregate with its own date and source boundary."""

    value_id: str
    label: str
    value: int | float | None
    unit: str
    as_of: str | None
    source: str
    freshness: str
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class MarketFundingView:
    """Local-only credit/liquidity tab; unlike sources are never merged."""

    values: tuple[MarketFundingValue, ...]


@dataclass(frozen=True)
class TossShortSymbolEODView:
    """One exact provider-specific symbol row; never a market aggregate."""

    symbol: str
    name: str
    market: str
    market_date: str
    short_selling_volume: int
    short_selling_amount: int
    short_selling_volume_rate: float | None
    short_selling_amount_rate: float | None


@dataclass(frozen=True)
class TossShortWatchlistView:
    """Fail-closed local view over the fixed two-symbol retained transaction."""

    dataset_id: str
    label: str
    members: tuple[TossShortSymbolEODView, ...]
    as_of: str | None
    expected_as_of: str | None
    source: str
    source_scope: str
    freshness: str
    pit_label: str
    automation_enabled: bool
    display_state: DashboardDisplayState
    unavailable_reason: str | None
    route: str

    @property
    def displays_values(self) -> bool:
        return (
            self.display_state is DashboardDisplayState.VALUE
            and len(self.members) == len(TOSS_SHORT_WATCHLIST)
        )


@dataclass(frozen=True)
class TreasuryRateView:
    """Keep official daily yields and delayed quote indices semantically separate."""

    view_id: str
    label: str
    official_daily: DashboardMetricView | None
    intraday_quote: DashboardMetricView | None
    official_provider: str | None
    official_data_type: str | None
    intraday_provider: str | None
    intraday_data_type: str | None


@dataclass(frozen=True)
class DashboardCurrentStageView:
    """Provider-free current projections that can publish before a full read.

    This stage intentionally owns only current-card and current quote surfaces.
    It never reads Health, Parquet history, account state, or a provider. A
    rejected projection remains an explicit typed unavailable value so an
    older current-only result cannot be retained accidentally.
    """

    as_of_utc: str
    metrics: dict[str, DashboardMetricView]
    treasury_rate_views: dict[str, TreasuryRateView]
    degraded_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class VIXSourceView:
    """Keep the official completed-daily and provider-subset intraday views distinct."""

    view_id: str
    label: str
    official_daily: DashboardMetricView | None
    intraday_quote: DashboardMetricView | None
    official_provider: str
    official_data_type: str
    intraday_provider: str
    intraday_data_type: str


@dataclass(frozen=True)
class IntradayFreshnessDecision:
    """Pure display decision for one finalized delayed intraday bar."""

    allow_value: bool
    freshness: str
    reason: str | None


@dataclass(frozen=True)
class CurrentDisplayGateDecision:
    """Whether one source timestamp can support a Dashboard current numeric."""

    allow_value: bool
    reason: str | None
    source_timestamp_utc: str | None
    freshness: str | None = None


def classify_current_display_timestamp(
    *, source_timestamp: object | None, now_utc: object,
    allow_kr_market_closed_last_verified: bool = False,
    retrieved_at: object | None = None,
    timestamp_basis: str = "PROVIDER_TIMESTAMP",
) -> CurrentDisplayGateDecision:
    """Gate one explicitly labelled provider- or retrieval-time observation.

    Retrieval time is accepted only when the route explicitly labels it as its
    time basis.  It remains display-only evidence of when a broker snapshot was
    received; it is never rewritten as a provider event timestamp.
    """
    now = pd.Timestamp(now_utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    now = now.tz_convert("UTC")
    if timestamp_basis not in {"PROVIDER_TIMESTAMP", "RETRIEVAL_TIMESTAMP"}:
        return CurrentDisplayGateDecision(
            False, "CURRENT_TIMESTAMP_BASIS_UNSUPPORTED", None,
        )
    effective_timestamp = (
        retrieved_at if timestamp_basis == "RETRIEVAL_TIMESTAMP" else source_timestamp
    )
    if effective_timestamp is None:
        return CurrentDisplayGateDecision(
            False,
            (
                "CURRENT_RETRIEVAL_TIMESTAMP_REQUIRED"
                if timestamp_basis == "RETRIEVAL_TIMESTAMP" else
                "CURRENT_SOURCE_TIMESTAMP_REQUIRED: a daily source-date label cannot prove a current value."
            ),
            None,
        )
    try:
        source = pd.Timestamp(effective_timestamp)
    except (TypeError, ValueError):
        source = pd.NaT
    if pd.isna(source) or source.tzinfo is None or source.utcoffset() is None:
        return CurrentDisplayGateDecision(
            False,
            (
                "CURRENT_RETRIEVAL_TIMESTAMP_INVALID_OR_NAIVE"
                if timestamp_basis == "RETRIEVAL_TIMESTAMP" else
                "CURRENT_SOURCE_TIMESTAMP_INVALID_OR_NAIVE: a timezone-aware provider source timestamp is required."
            ),
            None,
        )
    source = source.tz_convert("UTC")
    source_text = source.isoformat()
    if source > now:
        return CurrentDisplayGateDecision(
            False,
            (
                "CURRENT_RETRIEVAL_TIMESTAMP_FUTURE"
                if timestamp_basis == "RETRIEVAL_TIMESTAMP" else
                "CURRENT_SOURCE_TIMESTAMP_FUTURE: provider source timestamp is after the injected current clock."
            ),
            source_text,
        )
    source_kst = source.tz_convert("Asia/Seoul").date()
    now_kst = now.tz_convert("Asia/Seoul").date()
    if source_kst != now_kst:
        if (
            allow_kr_market_closed_last_verified
            and timestamp_basis == "PROVIDER_TIMESTAMP"
        ):
            calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
            if (
                not calendar.is_trading_day(now_kst)
                and source_kst == calendar.latest_completed_session(now.to_pydatetime())
            ):
                return CurrentDisplayGateDecision(
                    True,
                    "KR_MARKET_CLOSED_LAST_VERIFIED: latest verified observation from the most recent completed KRX session; not live.",
                    source_text,
                    "MARKET_CLOSED_LAST_VERIFIED",
                )
        return CurrentDisplayGateDecision(
            False,
            (
                "CURRENT_RETRIEVAL_DATE_NOT_TODAY_KST"
                if timestamp_basis == "RETRIEVAL_TIMESTAMP" else
                "CURRENT_SOURCE_DATE_NOT_TODAY_KST: provider source date does not match today in KST."
            ),
            source_text,
        )
    if now - source > timedelta(minutes=60):
        return CurrentDisplayGateDecision(
            False,
            (
                "CURRENT_RETRIEVAL_AGE_OVER_60M"
                if timestamp_basis == "RETRIEVAL_TIMESTAMP" else
                "CURRENT_SOURCE_AGE_OVER_60M: provider source timestamp is older than 60 minutes."
            ),
            source_text,
        )
    if timestamp_basis == "RETRIEVAL_TIMESTAMP":
        return CurrentDisplayGateDecision(
            True,
            "RETRIEVAL_TIMESTAMP_ACCEPTED: broker snapshot received within 60 minutes; provider event time unavailable.",
            source_text,
            "CURRENT_RETRIEVAL_TIME",
        )
    return CurrentDisplayGateDecision(True, None, source_text)


def classify_intraday_60m_freshness(
    *, bar_end: object, now_utc: object,
) -> IntradayFreshnessDecision:
    """Classify a finalized 60m bar using one conservative weekly closure.

    Friday after 21:00 UTC through Sunday before 21:00 UTC is the conservative
    shared futures closure accepted here. Exact holiday and ad-hoc closures are
    deliberately not inferred.
    """
    end = pd.Timestamp(bar_end)
    now = pd.Timestamp(now_utc)
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("bar_end must be timezone-aware")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    end = end.tz_convert("UTC")
    now = now.tz_convert("UTC")
    age = now - end
    if age < timedelta(0):
        return IntradayFreshnessDecision(
            False, "STALE_OR_MISSING", "최종 확정 60분 봉의 종료시각이 현재보다 미래입니다.",
        )
    in_shared_weekly_closure = (
        (now.dayofweek == 4 and now.hour >= 21)
        or now.dayofweek == 5
        or (now.dayofweek == 6 and now.hour < 21)
    )
    follows_friday_bar = end.dayofweek == 4
    within_reviewed_weekend_gap = age <= timedelta(hours=72)
    if in_shared_weekly_closure and follows_friday_bar and within_reviewed_weekend_gap:
        return IntradayFreshnessDecision(
            True,
            "MARKET_CLOSED_LAST_FINAL",
            "공통 주말 휴장 구간의 최근 금요일 확정봉입니다. 공휴일 인식 정책은 아닙니다.",
        )
    if age <= timedelta(hours=4):
        return IntradayFreshnessDecision(True, "60M_DELAYED", None)
    return IntradayFreshnessDecision(
        False, "STALE_OR_MISSING", "최종 확정 60분 봉이 장중 기준 4시간 이상 지연되었습니다.",
    )


def _pit_label(status: str) -> str:
    if status.startswith("PIT_SAFE"):
        return "백테스트 가능"
    if status == "PIT_LIMITED":
        return "설명용"
    if status in {"PIT_BLOCKED", "NON_PREDICTIVE", "RESEARCH_ONLY"}:
        return "예측 사용 불가"
    return "예측 사용 여부 확인 필요"
SHORT_OFFICIAL_SOURCE = "KRX MDCSTAT30101 via pykrx (normalized)"
SHORT_PROVIDER_SCOPE = "KRX_ONLY_EMPIRICALLY_CONFIRMED"
SHORT_REGIME_BOUNDARY = pd.Timestamp("2025-03-04")


def _to_float(value: object) -> float | None:
    value_numeric = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(value_numeric) else float(value_numeric)


def _latest(frame: pd.DataFrame) -> dict:
    return {} if frame.empty else frame.sort_values("date").iloc[-1].to_dict()


def _pct_change(values: pd.Series, periods: int = 1) -> float | None:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if len(clean) <= periods or clean.iloc[-periods - 1] == 0:
        return None
    return float((clean.iloc[-1] / clean.iloc[-periods - 1] - 1) * 100)


def _wilder_rsi(values: pd.Series, period: int = 14) -> pd.Series:
    """Return Wilder RSI with the original SMA seed and recursive smoothing."""
    close = pd.to_numeric(values, errors="coerce")
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = pd.Series(np.nan, index=close.index, dtype="float64")
    average_loss = pd.Series(np.nan, index=close.index, dtype="float64")
    if len(close) <= period:
        return average_gain

    seed = close.index[period]
    average_gain.loc[seed] = gains.iloc[1 : period + 1].mean()
    average_loss.loc[seed] = losses.iloc[1 : period + 1].mean()
    for offset in range(period + 1, len(close)):
        current = close.index[offset]
        previous = close.index[offset - 1]
        average_gain.loc[current] = (
            average_gain.loc[previous] * (period - 1) + gains.iloc[offset]
        ) / period
        average_loss.loc[current] = (
            average_loss.loc[previous] * (period - 1) + losses.iloc[offset]
        ) / period

    relative_strength = average_gain / average_loss.replace(0, np.nan)
    result = 100 - 100 / (1 + relative_strength)
    result = result.mask((average_loss == 0) & (average_gain > 0), 100.0)
    result = result.mask((average_gain == 0) & (average_loss > 0), 0.0)
    return result.mask((average_gain == 0) & (average_loss == 0), 50.0)


def _wilder_smooth(values: pd.Series, period: int) -> pd.Series:
    """Wilder average with a finite, contiguous SMA seed; never fills gaps."""
    source = pd.to_numeric(values, errors="coerce").astype("float64")
    source = source.where(np.isfinite(source))
    result = pd.Series(np.nan, index=source.index, dtype="float64")
    first_valid = np.flatnonzero(source.notna().to_numpy())
    if not len(first_valid):
        return result
    start = int(first_valid[0])
    if len(source) < start + period:
        return result
    seed = source.iloc[start : start + period]
    if seed.isna().any():
        return result
    result.iloc[start + period - 1] = float(seed.mean())
    for position in range(start + period, len(source)):
        previous, current = result.iloc[position - 1], source.iloc[position]
        if not np.isfinite(previous) or not np.isfinite(current):
            continue
        result.iloc[position] = (previous * (period - 1) + current) / period
    return result


def technical_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Return local descriptive indicators without changing source observations.

    EMA20 and Bollinger values require finite close observations. ATR14 and
    ADX14 require a valid finite high/low/close range; OBV additionally
    requires finite non-negative volume. A gap, duplicate date, infinite value,
    or insufficient warm-up remains NaN instead of being repaired or filled.
    """
    result = frame.copy()
    columns = (
        "ema20", "atr14", "adx14", "obv", "bollinger_mid", "bollinger_upper",
        "bollinger_lower", "bollinger_bandwidth",
    )
    for name in columns:
        result[name] = np.nan
    if result.empty:
        return result
    if "date" in result:
        dates = pd.to_datetime(result["date"], errors="coerce")
        if dates.isna().any() or dates.duplicated().any():
            return result

    close = pd.to_numeric(result.get("close"), errors="coerce").astype("float64")
    close = close.where(np.isfinite(close))
    result["ema20"] = close.ewm(span=20, adjust=False, min_periods=20).mean().where(close.notna())
    middle = close.rolling(20, min_periods=20).mean()
    deviation = close.rolling(20, min_periods=20).std(ddof=0)
    result["bollinger_mid"] = middle
    result["bollinger_upper"] = middle + 2.0 * deviation
    result["bollinger_lower"] = middle - 2.0 * deviation
    result["bollinger_bandwidth"] = ((result["bollinger_upper"] - result["bollinger_lower"]) / middle * 100.0).where(middle.ne(0))

    if not {"high", "low", "close"}.issubset(result.columns):
        return result
    high = pd.to_numeric(result["high"], errors="coerce").astype("float64")
    low = pd.to_numeric(result["low"], errors="coerce").astype("float64")
    valid_hlc = (
        np.isfinite(high) & np.isfinite(low) & close.notna()
        & high.ge(low) & high.ge(close) & low.le(close)
    )
    high, low, valid_close = high.where(valid_hlc), low.where(valid_hlc), close.where(valid_hlc)
    previous_close = valid_close.shift(1)
    true_range = pd.concat((high - low, (high - previous_close).abs(), (low - previous_close).abs()), axis=1).max(axis=1, skipna=False)
    true_range.iloc[0] = (high - low).iloc[0]
    atr = _wilder_smooth(true_range, 14)
    result["atr14"] = atr
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=result.index).where(valid_hlc & valid_hlc.shift(1, fill_value=False))
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=result.index).where(valid_hlc & valid_hlc.shift(1, fill_value=False))
    if len(result) and bool(valid_hlc.iloc[0]):
        plus_dm.iloc[0] = 0.0
        minus_dm.iloc[0] = 0.0
    plus_di = (100.0 * _wilder_smooth(plus_dm, 14) / atr).where(atr.gt(0))
    minus_di = (100.0 * _wilder_smooth(minus_dm, 14) / atr).where(atr.gt(0))
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)).where((plus_di + minus_di).gt(0))
    result["adx14"] = _wilder_smooth(dx, 14)

    if "volume" not in result:
        return result
    volume = pd.to_numeric(result["volume"], errors="coerce").astype("float64")
    valid_obv = close.notna() & np.isfinite(volume) & volume.ge(0)
    obv = pd.Series(np.nan, index=result.index, dtype="float64")
    running = 0.0
    for position in range(len(result)):
        if not bool(valid_obv.iloc[position]) or (position and not np.isfinite(obv.iloc[position - 1])):
            continue
        if position:
            movement = np.sign(close.iloc[position] - close.iloc[position - 1])
            running += float(movement * volume.iloc[position])
        obv.iloc[position] = running
    result["obv"] = obv
    return result


def short_selling_scope_regime(market_date: object) -> str | None:
    """Return the disclosed KRX venue regime for a trading date."""
    timestamp = pd.to_datetime(market_date, errors="coerce")
    if pd.isna(timestamp):
        return None
    return "KRX_ONLY" if timestamp < SHORT_REGIME_BOUNDARY else "KRX_NXT_COMBINED"


@dataclass(frozen=True)
class IndexSeriesView:
    index: str
    name: str
    exact_identity: str
    period: str
    dataset_id: str
    frame: pd.DataFrame
    display_state: DashboardDisplayState
    freshness: str
    as_of: str | None
    expected_as_of: str | None
    source: str
    reference_kst: str | None
    price_basis: str = "원지수 일봉 OHLCV · 수정지수/총수익지수 아님"
    unavailable_reason: str | None = None
    change: float | None = None
    change_pct: float | None = None
    period_high: float | None = None
    period_low: float | None = None

    @property
    def displays_values(self) -> bool:
        return self.display_state is DashboardDisplayState.VALUE and not self.frame.empty

    @classmethod
    def unavailable(
        cls,
        index: str,
        name: str,
        exact_identity: str,
        period: str,
        dataset_id: str,
        reason: str,
        *,
        freshness: str = "UNKNOWN",
        expected_as_of: str | None = None,
        source: str = "local retained data",
        state: DashboardDisplayState = DashboardDisplayState.UNAVAILABLE,
    ) -> "IndexSeriesView":
        return cls(
            index=index, name=name, exact_identity=exact_identity, period=period,
            dataset_id=dataset_id, frame=pd.DataFrame(), display_state=state,
            freshness=freshness, as_of=None, expected_as_of=expected_as_of,
            source=source, reference_kst=None, unavailable_reason=reason,
        )


@dataclass(frozen=True, slots=True)
class DashboardChartCoverage:
    """Typed retained-coverage disclosure for one Dashboard chart request."""

    period: str
    requested_sessions: int | None
    available_sessions: int
    available_start: str | None
    available_end: str | None
    complete: bool
    dataset_id: str
    series_id: str
    retained_scope: str = "SELECTED_CONTRACTED_LOCAL_DATASET"


def _attach_dashboard_chart_coverage(
    frame: pd.DataFrame,
    *,
    period: str,
    dataset_id: str,
    series_id: str,
) -> pd.DataFrame:
    """Attach presentation metadata without changing any retained observation."""
    result = frame.copy()
    requested = None if period == "MAX" else PERIOD_ROWS[period]
    dates = (
        pd.to_datetime(result["date"], errors="coerce")
        if not result.empty and "date" in result else pd.Series(dtype="datetime64[ns]")
    )
    available_start = None if dates.empty or dates.isna().any() else dates.iloc[0].date().isoformat()
    available_end = None if dates.empty or dates.isna().any() else dates.iloc[-1].date().isoformat()
    result.attrs[DASHBOARD_CHART_COVERAGE_ATTR] = DashboardChartCoverage(
        period=period,
        requested_sessions=requested,
        available_sessions=len(result),
        available_start=available_start,
        available_end=available_end,
        complete=(bool(result.empty) is False if requested is None else len(result) >= requested),
        dataset_id=dataset_id,
        series_id=series_id,
    )
    return result


@dataclass
class IndexQueryService:
    query: LocalParquetQuery
    project_root: Path | None = None

    INDEX_IDENTITIES = {
        "KOSPI": ("코스피 종합지수", "KRX:KOSPI"),
        "KOSDAQ": ("코스닥 종합지수", "KRX:KOSDAQ"),
        "KOSPI200": ("코스피 200", "KRX:KOSPI200 · 업종코드 1028"),
    }

    def series(
        self, index: str, period: str = "120D", *, as_of: object | None = None,
    ) -> pd.DataFrame:
        if index not in {"KOSPI", "KOSDAQ", "KOSPI200"}:
            raise ValueError(f"unsupported index: {index}")
        root = "normalized/kr_kospi200_index_daily" if index == "KOSPI200" else "normalized/kr_index_daily"
        partitions = {} if index == "KOSPI200" else {"market": index}
        requested = 999999999 if period == "MAX" else PERIOD_ROWS[period] + 130
        # ``kr_index_daily`` contracts retained OHLCV.  Keep the source OHLC
        # fields available to the read-only Dashboard; candle validation and
        # fail-closed rendering remain at the GUI boundary.
        columns = ["date", "symbol", "open", "high", "low", "close", "volume"]
        frame = (
            self.query.read(root, end=as_of, columns=columns, partitions=partitions)
            if period == "MAX"
            else self.query.tail(
                root, rows=requested, end=as_of, columns=columns,
                partitions=partitions,
            )
        )
        frame = frame[frame["symbol"].eq(index)].sort_values("date")
        close = pd.to_numeric(frame["close"], errors="coerce")
        for window in (5, 20, 60, 120):
            frame[f"ma{window}"] = close.rolling(window).mean()
        frame["rsi14"] = _wilder_rsi(close)
        frame["disparity60"] = close / close.rolling(60).mean() * 100
        frame = technical_indicators(frame)
        if period != "MAX":
            frame = frame.tail(PERIOD_ROWS[period])
        return frame.reset_index(drop=True)

    def chart_view(
        self, index: str, period: str = "120D", *, health: object | None = None,
    ) -> "IndexSeriesView":
        if index not in self.INDEX_IDENTITIES:
            raise ValueError(f"unsupported index: {index}")
        if period not in PERIOD_ROWS and period != "MAX":
            raise ValueError(f"unsupported index period: {period}")
        name, identity = self.INDEX_IDENTITIES[index]
        dataset_id = "kr_kospi200_index_daily" if index == "KOSPI200" else "kr_index_daily"
        if health is None and self.project_root is not None:
            from stock_data.gui.health_service import DailyHealthArtifactService

            health = DailyHealthArtifactService(self.project_root).load()
        rows = {getattr(row, "dataset", None): row for row in getattr(health, "rows", ())}
        row = rows.get(dataset_id)
        if getattr(health, "artifact_state", None) != "READY" or row is None:
            return IndexSeriesView.unavailable(
                index, name, identity, period, dataset_id,
                "지수 데이터의 Health 상태를 확인할 수 없습니다.",
            )
        freshness = str(getattr(row, "freshness", "UNKNOWN"))
        expected = self._date_text(getattr(row, "expected", None))
        retained = self._date_text(getattr(row, "latest", None))
        source = str(getattr(row, "source", "local retained data"))
        if getattr(row, "operational", None) == "BLOCKED":
            return IndexSeriesView.unavailable(
                index, name, identity, period, dataset_id,
                str(getattr(row, "blocker", "운영 차단 상태입니다.")),
                freshness=freshness, expected_as_of=expected, source=source,
                state=DashboardDisplayState.PROHIBITED,
            )
        if str(getattr(row, "runtime_coverage", "")).startswith("FAILED:"):
            return IndexSeriesView.unavailable(
                index, name, identity, period, dataset_id,
                "지수 데이터의 로컬 계약 검증에 실패했습니다.",
                freshness=freshness, expected_as_of=expected, source=source,
            )
        if freshness not in DISPLAYABLE_FRESHNESS and freshness != "STALE":
            reason = (
                f"지수 기준일 {retained or '미확인'}이 기대 완료일 {expected or '미확인'}보다 오래되었습니다."
                if freshness == "STALE" else "지수 데이터의 최신 상태를 확인할 수 없습니다."
            )
            return IndexSeriesView.unavailable(
                index, name, identity, period, dataset_id, reason,
                freshness=freshness, expected_as_of=expected, source=source,
                state=(DashboardDisplayState.REFRESH_REQUIRED
                       if freshness == "STALE" else DashboardDisplayState.UNAVAILABLE),
            )
        try:
            frame = self.series(index, period, as_of=retained)
            frame = self._validated_chart_frame(frame, index)
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            frame = pd.DataFrame()
        if frame.empty:
            return IndexSeriesView.unavailable(
                index, name, identity, period, dataset_id,
                "지수 원본 일봉을 읽거나 검증할 수 없습니다.",
                freshness=freshness, expected_as_of=expected, source=source,
            )
        as_of = frame["date"].iloc[-1].date().isoformat()
        if retained is None or as_of != retained:
            return IndexSeriesView.unavailable(
                index, name, identity, period, dataset_id,
                f"지수 기준일 {as_of}이 검증된 데이터 기준일 {retained or '미확인'}과 다릅니다.",
                freshness="STALE", expected_as_of=expected, source=source,
                state=DashboardDisplayState.REFRESH_REQUIRED,
            )
        close = pd.to_numeric(frame["close"], errors="coerce")
        change = float(close.iloc[-1] - close.iloc[-2]) if len(close) > 1 else None
        previous = float(close.iloc[-2]) if len(close) > 1 else None
        change_pct = change / previous * 100 if change is not None and previous else None
        high_values = pd.to_numeric(frame.get("high"), errors="coerce").dropna()
        low_values = pd.to_numeric(frame.get("low"), errors="coerce").dropna()
        return IndexSeriesView(
            index=index, name=name, exact_identity=identity, period=period,
            dataset_id=dataset_id, frame=frame,
            display_state=DashboardDisplayState.VALUE, freshness=freshness,
            as_of=as_of, expected_as_of=expected, source=source,
            reference_kst=f"{as_of} KST 일봉 · 일중 기준시각 미보존",
            change=change, change_pct=change_pct,
            period_high=(float(high_values.max()) if not high_values.empty else None),
            period_low=(float(low_values.min()) if not low_values.empty else None),
            unavailable_reason=(
                f"STALE retained history: as_of={as_of}, expected={expected or 'UNKNOWN'}; "
                "current-data claims and actions remain blocked."
                if freshness == "STALE" else None
            ),
        )

    @staticmethod
    def _validated_chart_frame(frame: pd.DataFrame, index: str) -> pd.DataFrame:
        required = ("date", "symbol", "close")
        if frame.empty or any(column not in frame for column in required):
            return pd.DataFrame()
        result = frame.copy()
        if set(result["symbol"].astype(str)) != {index}:
            raise ValueError("index identity differs")
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        for column in ("open", "high", "low", "close", "volume"):
            if column in result:
                result[column] = pd.to_numeric(result[column], errors="coerce")
        if result[["date", "close"]].isna().any().any():
            raise ValueError("index close contains missing values")
        if result["date"].duplicated().any() or not result["date"].is_monotonic_increasing:
            raise ValueError("index sessions are duplicate or unordered")
        return result.reset_index(drop=True)

    @staticmethod
    def _date_text(value: object) -> str | None:
        if value in {None, "N/A"}:
            return None
        parsed = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(parsed) else parsed.date().isoformat()

    def overview(self) -> list[dict]:
        rows = []
        for name in ("KOSPI", "KOSDAQ", "KOSPI200"):
            frame = self.series(name, "20D")
            last = _latest(frame)
            rows.append({"name": name, "value": last.get("close"), "change_1d": _pct_change(frame.get("close", pd.Series(dtype=float))), "date": last.get("date"), "spark": frame.get("close", pd.Series(dtype=float)).tolist(), "status": "PIT_LIMITED" if name == "KOSPI200" else "STALE"})
        return rows

    def asset_series(
        self, asset: str, period: str = "120D", *, as_of: object | None = None,
    ) -> pd.DataFrame:
        """Return one bounded dashboard series using only retained local data."""
        if period not in PERIOD_ROWS and period != "MAX":
            raise ValueError(f"unsupported dashboard period: {period}")
        definition = DASHBOARD_ASSETS.get(asset)
        if definition is None:
            raise ValueError(f"unsupported dashboard asset: {asset}")
        if definition["kind"] == "kr":
            return self.series(definition["symbol"], period, as_of=as_of)
        rows = 999999999 if period == "MAX" else PERIOD_ROWS[period] + 130
        columns = (
            ["date", "symbol", "open", "high", "low", "close", "volume"]
            if definition["kind"] == "futures"
            else ["date", "symbol", "close", "volume"]
        )
        dataset = (
            "normalized/global_etf_price_daily" if definition["kind"] == "etf" else
            "normalized/global_commodity_futures_daily" if definition["kind"] == "futures" else
            "normalized/global_index_price_daily"
        )
        exact_filter = {"symbol": (str(definition["symbol"]),)}
        frame = (self.query.read(
                     dataset, end=as_of, columns=columns,
                     filters=exact_filter,
                 )
                 if period == "MAX" else
                 self.query.tail(
                     dataset, rows=rows, end=as_of, columns=columns,
                     filters=exact_filter,
                 ))
        if frame.empty or "symbol" not in frame:
            return pd.DataFrame(columns=columns)
        frame = frame[frame["symbol"].astype(str).eq(definition["symbol"])].copy()
        if frame.empty:
            return pd.DataFrame(columns=columns)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["date", "close"]).sort_values("date")
        if definition["kind"] == "futures":
            for column in ("open", "high", "low"):
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frame = frame.dropna(subset=["open", "high", "low"])
            valid_ohlc = (
                frame["high"].ge(frame[["open", "close"]].max(axis=1))
                & frame["low"].le(frame[["open", "close"]].min(axis=1))
                & frame["low"].le(frame["high"])
            )
            frame = frame.loc[valid_ohlc]
        close = frame["close"]
        for window in (5, 20, 60, 120):
            frame[f"ma{window}"] = close.rolling(window).mean()
        frame["rsi14"] = _wilder_rsi(close)
        frame["disparity60"] = close / close.rolling(60).mean() * 100
        frame = technical_indicators(frame)
        if period != "MAX":
            frame = frame.tail(PERIOD_ROWS[period])
        return frame.reset_index(drop=True)

    def asset_snapshot(self, asset: str, *, as_of: object | None = None) -> dict:
        """Return a conservative card payload; missing data stays missing."""
        definition = DASHBOARD_ASSETS[asset]
        frame = self.asset_series(asset, "20D", as_of=as_of)
        if frame.empty:
            return {"asset": asset, "label": definition["label"], "value": None,
                    "change": None, "change_pct": None, "date": None,
                    "status": "DATA NOT AVAILABLE", "source": "retained local data"}
        row = frame.iloc[-1]
        change = _to_float(frame["close"].diff().iloc[-1])
        return {"asset": asset, "label": definition["label"], "value": _to_float(row.get("close")),
                "change": change, "change_pct": _pct_change(frame["close"]),
                "date": row.get("date"), "status": "STALE", "source": "retained local data"}


@dataclass(frozen=True)
class EquityIdentity:
    """One exact chart identity; optional fields describe accepted fund metadata."""

    symbol: str
    name: str
    market: str
    isin: str | None
    listing_date: str | None
    security_type: str
    issuer: str | None = None
    exposure: str | None = None
    currency: str | None = None
    leverage_style: str | None = None
    distribution_style: str | None = None
    identity_source: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.market, self.symbol

    @property
    def display_label(self) -> str:
        return f"{self.name} · {self.symbol} · {self.market} · {self.security_type}"

    @property
    def is_us_etf(self) -> bool:
        return self.market == "US ETF" and self.security_type == "ETF"


US_ETF_CHART_IDENTITIES = (
    EquityIdentity(
        "SOXL", "Direxion Daily Semiconductor Bull 3X Shares", "US ETF", None,
        "2010-03-11", "ETF", "Direxion", "NYSE Semiconductor Index 일간 300%",
        "USD", "일간 3배·매일 재설정", "분기 분배", "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "TQQQ", "ProShares UltraPro QQQ", "US ETF", None, "2010-02-09", "ETF",
        "ProShares", "Nasdaq-100 일간 300%", "USD", "일간 3배·매일 재설정",
        "분기 분배", "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "QLD", "ProShares Ultra QQQ", "US ETF", None, "2006-06-19", "ETF",
        "ProShares", "Nasdaq-100 일간 200%", "USD", "일간 2배·매일 재설정",
        "분기 분배", "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "KORU", "Direxion Daily MSCI South Korea Bull 3X Shares", "US ETF", None,
        "2013-04-10", "ETF", "Direxion", "MSCI Korea 25/50 Index 일간 300%",
        "USD", "일간 3배·매일 재설정", "분기 분배",
        "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "TLT", "iShares 20+ Year Treasury Bond ETF", "US ETF", None,
        "2002-07-22", "ETF", "iShares", "ICE U.S. Treasury 20+ Year Bond Index",
        "USD", "비레버리지 장기 국채 ETF", "월 분배",
        "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "TLTW", "iShares 20+ Year Treasury Bond BuyWrite Strategy ETF", "US ETF",
        None, "2022-08-18", "ETF", "iShares", "TLT 보유 + 콜옵션 매도 전략",
        "USD", "비레버리지·바이라이트", "월 분배·옵션 프리미엄",
        "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "QQQ", "Invesco QQQ Trust, Series 1", "US ETF", None, "1999-03-10",
        "ETF", "Invesco", "Nasdaq-100 Index", "USD", "비레버리지 지수 추종",
        "분기 분배", "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "SPY", "SPDR S&P 500 ETF Trust", "US ETF", None, "1993-01-22", "ETF",
        "State Street SPDR", "S&P 500 Index", "USD", "비레버리지 지수 추종",
        "분기 분배", "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "QQQI", "NEOS Nasdaq-100 High Income ETF", "US ETF", None,
        "2024-01-29", "ETF", "NEOS", "Nasdaq-100 주식 + 콜옵션 전략", "USD",
        "비레버리지·옵션 인컴", "월 분배·옵션 프리미엄",
        "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "QDVO", "Amplify CWP Growth & Income ETF", "US ETF", None,
        "2024-08-22", "ETF", "Amplify", "미국 성장주 + 전술적 커버드콜", "USD",
        "비레버리지·액티브 옵션 인컴", "월 분배·옵션 프리미엄",
        "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "GPIQ", "Goldman Sachs Nasdaq-100 Premium Income ETF", "US ETF", None,
        "2023-10-24", "ETF", "Goldman Sachs Asset Management",
        "Nasdaq-100 주식 + 동적 옵션 오버레이", "USD",
        "비레버리지·옵션 인컴", "월 분배·옵션 프리미엄",
        "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "JEPQ", "JPMorgan Nasdaq Equity Premium Income ETF", "US ETF", None,
        "2022-05-03", "ETF", "J.P. Morgan Asset Management",
        "미국 대형 성장주 + 주가지수연계증권(ELN)", "USD",
        "비레버리지·액티브 옵션 인컴", "월 분배·ELN 프리미엄",
        "UR-054 accepted chart-only catalog",
    ),
    EquityIdentity(
        "JEPI", "JPMorgan Equity Premium Income ETF", "US ETF", None,
        "2020-05-20", "ETF", "J.P. Morgan Asset Management",
        "미국 대형주 + 주가지수연계증권(ELN)", "USD",
        "비레버리지·액티브 옵션 인컴", "월 분배·ELN 프리미엄",
        "UR-054 accepted chart-only catalog",
    ),
)


# Data Status currently authorizes and validates only the symbol-bound SOXX
# global-ETF lane. SOXX is intentionally not part of this thirteen-fund seed
# universe, so production remains numeric-free until Data explicitly expands
# the accepted symbol scope.
US_ETF_OFFICIAL_IDENTITY_SOURCES = {
    "SOXL": "https://www.direxion.com/product/daily-semiconductor-bull-bear-3x-etfs",
    "TQQQ": "https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq",
    "QLD": "https://www.proshares.com/our-etfs/leveraged-and-inverse/qld",
    "KORU": "https://www.direxion.com/product/daily-msci-south-korea-bull-3x-etf",
    "TLT": "https://www.ishares.com/us/products/239454/ishares-20-year-treasury-bond-etf",
    "TLTW": "https://www.ishares.com/us/products/329118/ishares-20-year-treasury-bond-buywrite-strategy-etf",
    "QQQ": "https://www.invesco.com/qqq-etf/en/home.html",
    "SPY": "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy",
    "QQQI": "https://neosfunds.com/qqqi/",
    "QDVO": "https://amplifyetfs.com/qdvo/",
    "GPIQ": "https://am.gs.com/en-us/advisors/funds/detail/PV105234/38149W861/goldman-sachs-nasdaq-100-premium-income-etf",
    "JEPQ": "https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-nasdaq-equity-premium-income-etf-etf-shares-46654q203",
    "JEPI": "https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-equity-premium-income-etf-etf-shares-46641q332",
}
US_ETF_CHART_IDENTITIES = tuple(
    replace(identity, identity_source=US_ETF_OFFICIAL_IDENTITY_SOURCES[identity.symbol])
    for identity in US_ETF_CHART_IDENTITIES
)
US_ETF_CHART_AUTHORIZED_SYMBOLS: frozenset[str] = frozenset()


@dataclass(frozen=True)
class EquitySearchView:
    query: str
    matches: tuple[EquityIdentity, ...]
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class EquitySeriesView:
    identity: EquityIdentity
    period: str
    frame: pd.DataFrame
    display_state: DashboardDisplayState
    freshness: str
    as_of: str | None
    expected_as_of: str | None
    source: str
    reference_kst: str | None
    price_mode: str = "원본(미조정) OHLCV"
    price_basis: str = "PROVIDER_NATIVE_ORIGINAL_PRICE"
    unavailable_reason: str | None = None
    change: float | None = None
    change_pct: float | None = None
    period_high: float | None = None
    period_low: float | None = None
    current_value: float | None = None
    current_unit: str | None = None
    current_source_date: str | None = None
    current_retrieved_at_utc: str | None = None
    current_provider: str | None = None
    current_refresh_status: str | None = None
    current_route: str | None = None
    current_interval: str | None = None
    current_finality: str | None = None
    current_provider_timestamp_utc: str | None = None
    current_source_route: str | None = None
    current_display_only: bool | None = None
    current_pit_safe: bool | None = None
    current_unavailable_reason: str | None = None
    current_visible_label: str | None = None

    @property
    def displays_values(self) -> bool:
        return self.display_state is DashboardDisplayState.VALUE and not self.frame.empty


@dataclass(frozen=True)
class InstrumentFactsView:
    """Source-safe, non-deriving context for one exact chart identity."""

    identity_line: str
    market_line: str
    source_line: str
    risk_line: str
    unsupported_line: str
    displays_price_facts: bool


def instrument_facts_view(view: EquitySeriesView) -> InstrumentFactsView:
    """Project accepted chart metadata without inventing financial facts."""

    if not isinstance(view, EquitySeriesView):
        raise TypeError("equity series view is required")
    identity = view.identity
    if not all(
        isinstance(value, str) and value.strip() and value == value.strip()
        for value in (
            identity.symbol, identity.name, identity.market, identity.security_type,
            view.source, view.price_basis,
        )
    ):
        raise ValueError("instrument facts require complete accepted identity metadata")
    if identity.is_us_etf and identity not in US_ETF_CHART_IDENTITIES:
        raise ValueError("instrument facts reject an unaccepted U.S. ETF identity")
    if identity.currency is not None and (
        not isinstance(identity.currency, str)
        or not identity.currency.strip()
        or identity.currency != identity.currency.strip()
    ):
        raise ValueError("instrument facts currency is invalid")
    currency = identity.currency or "미보존"
    identity_line = (
        f"{identity.name} · {identity.symbol} · {identity.market} · "
        f"{identity.security_type}"
    )
    market_line = (
        f"통화 {currency} · 세션 {view.reference_kst or '미확인'} · "
        f"상태 {view.freshness}"
    )
    source_line = (
        f"출처 {view.source} · 기준 {view.as_of or '미확인'} · "
        f"가격기준 {view.price_basis}"
    )
    if identity.is_us_etf:
        risk_line = (
            f"{identity.issuer or '발행사 미보존'} · "
            f"{identity.exposure or '노출 미보존'} · "
            f"{identity.leverage_style or '레버리지 특성 미보존'} · "
            f"{identity.distribution_style or '분배 특성 미보존'}"
        )
    else:
        risk_line = "ETF 레버리지·분배 특성 해당 없음"
    return InstrumentFactsView(
        identity_line=identity_line,
        market_line=market_line,
        source_line=source_line,
        risk_line=risk_line,
        unsupported_line=(
            "보수·분배율·유동성 순위·52주 범위·KRW 환산: 승인된 로컬 필드 없음"
        ),
        displays_price_facts=view.displays_values,
    )


@dataclass(frozen=True)
class NormalizedBenchmarkComparisonView:
    """One exact-session, common-base descriptive security comparison."""

    target: EquityIdentity
    benchmark_id: str
    benchmark_label: str
    period: str
    frame: pd.DataFrame
    common_start: str | None
    target_as_of: str | None
    benchmark_as_of: str | None
    target_freshness: str
    benchmark_freshness: str
    currency: str
    target_price_basis: str
    benchmark_price_basis: str
    display_state: DashboardDisplayState
    unavailable_reason: str | None = None

    @property
    def displays_values(self) -> bool:
        required = {"date", "target_position", "target_normalized", "benchmark_normalized"}
        return (
            self.display_state is DashboardDisplayState.VALUE
            and bool(required.issubset(self.frame.columns))
            and not self.frame.empty
        )

    @classmethod
    def unavailable(
        cls,
        target: EquityIdentity,
        period: str,
        reason: str,
        *,
        benchmark_id: str,
        benchmark_label: str,
        currency: str,
        target_freshness: str = "UNKNOWN",
        benchmark_freshness: str = "UNKNOWN",
        target_as_of: str | None = None,
        benchmark_as_of: str | None = None,
    ) -> "NormalizedBenchmarkComparisonView":
        return cls(
            target=target, benchmark_id=benchmark_id, benchmark_label=benchmark_label,
            period=period, frame=pd.DataFrame(), common_start=None,
            target_as_of=target_as_of, benchmark_as_of=benchmark_as_of,
            target_freshness=target_freshness, benchmark_freshness=benchmark_freshness,
            currency=currency, target_price_basis="PROVIDER_NATIVE_ORIGINAL_PRICE",
            benchmark_price_basis="INDEX_LEVEL", display_state=DashboardDisplayState.UNAVAILABLE,
            unavailable_reason=reason,
        )

    @classmethod
    def from_exact_common_sessions(
        cls,
        target: EquitySeriesView,
        benchmark: "IndexSeriesView",
    ) -> "NormalizedBenchmarkComparisonView":
        """Rebase original price and exact benchmark level on their first shared date."""
        benchmark_id = benchmark.index
        benchmark_label = f"{benchmark.name} ({benchmark.exact_identity})"
        currency = target.identity.currency or "KRW"
        if not target.displays_values:
            return cls.unavailable(
                target.identity, target.period,
                target.unavailable_reason or "Target price is not displayable.",
                benchmark_id=benchmark_id, benchmark_label=benchmark_label, currency=currency,
                target_freshness=target.freshness, benchmark_freshness=benchmark.freshness,
                target_as_of=target.as_of, benchmark_as_of=benchmark.as_of,
            )
        if target.price_basis != "PROVIDER_NATIVE_ORIGINAL_PRICE":
            return cls.unavailable(
                target.identity, target.period, "Only provider-native original-price comparisons are available.",
                benchmark_id=benchmark_id, benchmark_label=benchmark_label, currency=currency,
                target_freshness=target.freshness, benchmark_freshness=benchmark.freshness,
                target_as_of=target.as_of, benchmark_as_of=benchmark.as_of,
            )
        if not benchmark.displays_values or benchmark.period != target.period:
            return cls.unavailable(
                target.identity, target.period,
                benchmark.unavailable_reason or "The exact benchmark is not displayable for the selected period.",
                benchmark_id=benchmark_id, benchmark_label=benchmark_label, currency=currency,
                target_freshness=target.freshness, benchmark_freshness=benchmark.freshness,
                target_as_of=target.as_of, benchmark_as_of=benchmark.as_of,
            )

        def prepared(frame: pd.DataFrame, value_name: str) -> pd.DataFrame:
            required = {"date", "close"}
            if not required.issubset(frame.columns):
                raise ValueError("comparison input lacks date/close")
            result = frame.loc[:, ["date", "close"]].copy()
            result["date"] = pd.to_datetime(result["date"], errors="coerce")
            result[value_name] = pd.to_numeric(result.pop("close"), errors="coerce")
            if result[["date", value_name]].isna().any().any() or result["date"].duplicated().any():
                raise ValueError("comparison input has invalid or duplicate sessions")
            if not result[value_name].gt(0).all():
                raise ValueError("comparison input has non-positive values")
            return result.sort_values("date").reset_index(drop=True)

        try:
            target_frame = prepared(target.frame, "target_close")
            target_frame["target_position"] = np.arange(len(target_frame), dtype=float)
            benchmark_frame = prepared(benchmark.frame, "benchmark_close")
            # This is deliberately an inner join: neither holidays nor missing
            # observations may be forward-filled to manufacture a common date.
            common = target_frame.merge(benchmark_frame, on="date", how="inner", validate="one_to_one")
            if common.empty:
                raise ValueError("no exact common eligible sessions")
            target_base = float(common["target_close"].iloc[0])
            benchmark_base = float(common["benchmark_close"].iloc[0])
            common["target_normalized"] = common["target_close"] / target_base * 100.0
            common["benchmark_normalized"] = common["benchmark_close"] / benchmark_base * 100.0
            normalized = common.loc[:, [
                "date", "target_position", "target_normalized", "benchmark_normalized",
            ]].reset_index(drop=True)
            if not np.isfinite(normalized[["target_normalized", "benchmark_normalized"]].to_numpy()).all():
                raise ValueError("comparison normalization is non-finite")
        except (KeyError, TypeError, ValueError):
            return cls.unavailable(
                target.identity, target.period,
                "The selected price and exact benchmark have no valid common eligible sessions.",
                benchmark_id=benchmark_id, benchmark_label=benchmark_label, currency=currency,
                target_freshness=target.freshness, benchmark_freshness=benchmark.freshness,
                target_as_of=target.as_of, benchmark_as_of=benchmark.as_of,
            )
        return cls(
            target=target.identity, benchmark_id=benchmark_id, benchmark_label=benchmark_label,
            period=target.period, frame=normalized,
            common_start=normalized["date"].iloc[0].date().isoformat(),
            target_as_of=target.as_of, benchmark_as_of=benchmark.as_of,
            target_freshness=target.freshness, benchmark_freshness=benchmark.freshness,
            currency=currency, target_price_basis=target.price_basis,
            benchmark_price_basis="KRX_INDEX_LEVEL",
            display_state=DashboardDisplayState.VALUE,
        )


@dataclass
class EquityChartService:
    """Fail-closed local search and provider-native Korean-equity daily view."""

    root: Path

    def __post_init__(self) -> None:
        self.query = LocalParquetQuery(self.root / "data")
        self._catalog_cache: tuple[EquityIdentity, ...] | None = None

    def search(self, text: str, *, limit: int = 30) -> EquitySearchView:
        query = str(text or "").strip()
        if not query:
            return EquitySearchView(query, (), "회사명 또는 6자리 종목코드를 입력하세요.")
        if limit < 1:
            raise ValueError("search limit must be positive")
        try:
            catalog = self._catalog()
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            return EquitySearchView(query, (), "종목 식별정보를 읽거나 검증할 수 없습니다.")

        folded = query.casefold()
        exact_ticker = [item for item in catalog if item.symbol.casefold() == folded]
        exact_name = [item for item in catalog if item.name.casefold() == folded]
        partial_name = [
            item for item in catalog
            if folded in item.name.casefold()
            and item not in exact_name
            and item not in exact_ticker
        ]
        matches = tuple((exact_ticker + exact_name + partial_name)[:limit])
        reason = None if matches else "일치하는 KOSPI/KOSDAQ 보통주를 찾지 못했습니다."
        return EquitySearchView(query, matches, reason)

    def series(
        self, identity: EquityIdentity, period: str = "120D", *, health: object | None = None,
        now_utc: object | None = None,
    ) -> EquitySeriesView:
        if period not in PERIOD_ROWS and period != "MAX":
            raise ValueError(f"unsupported equity period: {period}")
        try:
            canonical = next(item for item in self._catalog() if item.key == identity.key)
        except (KeyError, OSError, PermissionError, StopIteration, TypeError, ValueError):
            return self._unavailable(identity, period, "종목 식별정보를 다시 확인해야 합니다.")
        if canonical != identity:
            return self._unavailable(
                identity, period, "선택한 종목의 이름·시장·종목코드 식별정보가 일치하지 않습니다.",
            )

        from stock_data.gui.current_display import load_current_display

        current = load_current_display(self.root)
        if current is not None and current.symbol != canonical.symbol:
            current = None
        naver_current, _ = load_naver_web_000660_current_observation(self.root)
        ur199_000660, _ = load_naver_mobile_basic_ur199_current_observation(
            self.root, symbol="000660"
        )
        toss_000660, _ = load_toss_domestic_ur246_current_observation(
            self.root, symbol="000660"
        )
        if canonical.market == "KOSPI" and canonical.symbol == "000660" and naver_current is not None:
            current = naver_current
        if canonical.market == "KOSPI" and canonical.symbol == "000660" and ur199_000660 is not None:
            current = ur199_000660
        if canonical.market == "KOSPI" and canonical.symbol == "000660" and toss_000660 is not None:
            current = toss_000660
        ls_current, ls_reason = load_ls_t8412_current_observation(self.root)
        ur199_005930, ur199_005930_reason = load_naver_mobile_basic_ur199_current_observation(
            self.root, symbol="005930"
        )
        toss_005930, toss_005930_reason = load_toss_domestic_ur246_current_observation(
            self.root, symbol="005930"
        )
        toss_005930_nxt_close, toss_005930_nxt_reason = load_toss_005930_nxt_close_ur241_observation(
            self.root
        )
        if canonical.market == "KOSPI" and canonical.symbol == "005930" and ls_current is not None:
            current = ls_current
        if canonical.market == "KOSPI" and canonical.symbol == "005930" and ur199_005930 is not None:
            current = ur199_005930
        if canonical.market == "KOSPI" and canonical.symbol == "005930" and toss_005930_nxt_close is not None:
            current = toss_005930_nxt_close
        if canonical.market == "KOSPI" and canonical.symbol == "005930" and toss_005930 is not None:
            current = toss_005930
        current_absence_reason = (
            "TOSS_STOCK_CURRENT_QUOTE_UR141_UNAVAILABLE: OAuth stopped before the "
            "exact 005930 business GET; no Toss projection is read. "
            + ls_reason + " " + ur199_005930_reason + " " + toss_005930_nxt_reason
            + " " + toss_005930_reason
            if canonical.market == "KOSPI" and canonical.symbol == "005930" and current is None
            else None
        )

        if health is None:
            from stock_data.gui.health_service import DailyHealthArtifactService

            health = DailyHealthArtifactService(self.root).load()
        rows = {getattr(row, "dataset", None): row for row in getattr(health, "rows", ())}
        row = rows.get("kr_equity_price_daily")
        if getattr(health, "artifact_state", None) != "READY" or row is None:
            return self._unavailable(
                canonical, period, "가격 데이터의 Health 상태를 확인할 수 없습니다.",
            )
        freshness = str(getattr(row, "freshness", "UNKNOWN"))
        expected = self._optional_date(getattr(row, "expected", None))
        retained = self._optional_date(getattr(row, "latest", None))
        source = str(getattr(row, "source", "local retained data"))
        if getattr(row, "operational", None) == "BLOCKED":
            return self._unavailable(
                canonical, period, str(getattr(row, "blocker", "운영 차단 상태입니다.")),
                freshness=freshness, expected=expected, source=source,
                state=DashboardDisplayState.PROHIBITED,
            )
        if str(getattr(row, "runtime_coverage", "")).startswith("FAILED:"):
            return self._unavailable(
                canonical, period, "가격 데이터의 로컬 계약 검증에 실패했습니다.",
                freshness=freshness, expected=expected, source=source,
            )
        if freshness not in DISPLAYABLE_FRESHNESS and freshness != "STALE":
            reason = (
                f"가격 기준일 {retained or '미확인'}이 기대 완료일 {expected or '미확인'}보다 오래되었습니다."
                if freshness == "STALE" else "가격 데이터의 최신 상태를 확인할 수 없습니다."
            )
            return self._unavailable(
                canonical, period, reason, freshness=freshness, expected=expected,
                source=source,
                state=(DashboardDisplayState.REFRESH_REQUIRED
                       if freshness == "STALE" else DashboardDisplayState.UNAVAILABLE),
            )

        requested = 999999999 if period == "MAX" else PERIOD_ROWS[period] + 130
        columns = [
            "date", "market", "symbol", "open", "high", "low", "close", "volume",
            "source", "source_operation", "source_date",
        ]
        try:
            frame = (
                self.query.read(
                    "normalized/kr_equity_price_daily", columns=columns,
                    partitions={"market": canonical.market},
                    filters={"symbol": (canonical.symbol,)},
                )
                if period == "MAX" else
                self.query.tail(
                    "normalized/kr_equity_price_daily", rows=requested, columns=columns,
                    partitions={"market": canonical.market},
                    filters={"symbol": (canonical.symbol,)},
                )
            )
            frame = self._validated_original_frame(frame, canonical)
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            frame = pd.DataFrame()
        if frame.empty and current is not None:
            return self._attach_current(self._unavailable(
                canonical, period, "Finalized daily OHLCV is unavailable; current display is separate.",
                freshness=freshness, expected=expected, source=source,
            ), current, now_utc=now_utc, absence_reason=current_absence_reason)
        if frame.empty:
            return self._unavailable(
                canonical, period, "선택한 종목의 원본 일봉 OHLCV를 읽거나 검증할 수 없습니다.",
                freshness=freshness, expected=expected, source=source,
            )

        as_of = frame["date"].iloc[-1].date().isoformat()
        if (retained is None or as_of != retained) and current is not None:
            return self._attach_current(self._unavailable(
                canonical, period,
                f"Finalized daily date {as_of} differs from verified date {retained or 'UNKNOWN'}.",
                freshness="STALE", expected=expected, source=source,
                state=DashboardDisplayState.REFRESH_REQUIRED,
            ), current, now_utc=now_utc, absence_reason=current_absence_reason)
        if retained is None or as_of != retained:
            return self._unavailable(
                canonical, period,
                f"종목 기준일 {as_of}이 검증된 데이터 기준일 {retained or '미확인'}과 다릅니다.",
                freshness="STALE", expected=expected, source=source,
                state=DashboardDisplayState.REFRESH_REQUIRED,
            )
        close = frame["close"]
        for window in (5, 20, 60, 120):
            frame[f"ma{window}"] = close.rolling(window).mean()
        frame["rsi14"] = _wilder_rsi(close)
        frame["disparity60"] = close / close.rolling(60).mean() * 100
        frame = technical_indicators(frame)
        if period != "MAX":
            frame = frame.tail(PERIOD_ROWS[period]).reset_index(drop=True)
        change = float(frame["close"].iloc[-1] - frame["close"].iloc[-2]) if len(frame) > 1 else None
        previous = float(frame["close"].iloc[-2]) if len(frame) > 1 else None
        change_pct = change / previous * 100 if change is not None and previous else None
        return self._attach_current(EquitySeriesView(
            identity=canonical, period=period, frame=frame,
            display_state=DashboardDisplayState.VALUE, freshness=freshness,
            as_of=as_of, expected_as_of=expected, source=source,
            reference_kst=f"{as_of} KST 일봉 · 정확한 시각 미보존",
            change=change, change_pct=change_pct,
            period_high=float(frame["high"].max()), period_low=float(frame["low"].min()),
            unavailable_reason=(
                f"STALE retained history: as_of={as_of}, expected={expected or 'UNKNOWN'}; "
                "current-data claims and actions remain blocked."
                if freshness == "STALE" else None
            ),
        ), current, now_utc=now_utc, absence_reason=current_absence_reason)

    def _catalog(self) -> tuple[EquityIdentity, ...]:
        if self._catalog_cache is not None:
            return self._catalog_cache
        required = [
            "symbol", "name", "market", "isin", "listing_date", "delisting_date",
            "security_type_name",
        ]
        frames = []
        for market in ("KOSPI", "KOSDAQ"):
            path = self.root / "data/normalized/kr_equity_master" / f"market={market}" / "data.parquet"
            frame = pd.read_parquet(path, columns=required)
            if frame.empty or set(frame["market"].astype(str)) != {market}:
                raise ValueError(f"invalid equity master market: {market}")
            frames.append(frame)
        catalog = pd.concat(frames, ignore_index=True)
        if catalog.duplicated(["market", "symbol"]).any():
            raise ValueError("duplicate equity master identity")
        active = catalog.loc[
            catalog["delisting_date"].isna()
            & catalog["security_type_name"].astype(str).eq("보통주")
        ].copy()
        if active.empty or active[["symbol", "name", "market"]].isna().any().any():
            raise ValueError("empty or incomplete equity catalog")
        identities = []
        for row in active.sort_values(["market", "symbol"]).itertuples(index=False):
            identities.append(EquityIdentity(
                symbol=str(row.symbol), name=str(row.name), market=str(row.market),
                isin=self._optional_text(row.isin),
                listing_date=self._optional_date(row.listing_date),
                security_type=str(row.security_type_name),
            ))
        self._catalog_cache = tuple(identities)
        return self._catalog_cache

    @staticmethod
    def _validated_original_frame(
        frame: pd.DataFrame, identity: EquityIdentity,
    ) -> pd.DataFrame:
        if frame.empty:
            return frame
        result = frame.copy()
        if set(result["market"].astype(str)) != {identity.market}:
            raise ValueError("equity market identity differs")
        if set(result["symbol"].astype(str)) != {identity.symbol}:
            raise ValueError("equity symbol identity differs")
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        for column in ("open", "high", "low", "close", "volume"):
            result[column] = pd.to_numeric(result[column], errors="coerce")
        if result[["date", "open", "high", "low", "close", "volume"]].isna().any().any():
            raise ValueError("equity OHLCV contains missing values")
        if result["date"].duplicated().any() or not result["date"].is_monotonic_increasing:
            result = result.sort_values("date")
            if result["date"].duplicated().any():
                raise ValueError("duplicate equity session")
        valid_ohlc = (
            result["high"].ge(result[["open", "close"]].max(axis=1))
            & result["low"].le(result[["open", "close"]].min(axis=1))
            & result["low"].le(result["high"])
            & result["volume"].ge(0)
        )
        if not valid_ohlc.all():
            raise ValueError("invalid provider-native equity OHLCV")
        if identity.listing_date is not None:
            result = result.loc[result["date"].ge(pd.Timestamp(identity.listing_date))]
        return result.reset_index(drop=True)

    @staticmethod
    def _attach_current(
        view: EquitySeriesView, current: object | None, *, now_utc: object | None = None,
        absence_reason: str | None = None,
    ) -> EquitySeriesView:
        if current is None:
            return (
                replace(
                    view,
                    current_refresh_status="CURRENT_UNAVAILABLE",
                    current_unavailable_reason=absence_reason,
                )
                if absence_reason else view
            )
        now = now_utc if now_utc is not None else pd.Timestamp.now(tz="UTC")
        if isinstance(current, CurrentObservation) and current.route_id in {
            TOSS_000660_NXT_CLOSE_UR240_ROUTE.route_id,
            TOSS_005930_NXT_CLOSE_UR241_ROUTE.route_id,
        }:
            decision = classify_korean_equity_nxt_timestamp(
                provider_timestamp_utc=current.provider_timestamp_utc,
                now_utc=pd.Timestamp(now).to_pydatetime(),
                session_start_kst=None,
                venue_inferred=True,
            )
        else:
            decision = classify_current_display_timestamp(
                source_timestamp=(
                    current.provider_timestamp_utc if isinstance(current, CurrentObservation) else None
                ),
                now_utc=now,
            )
        if not decision.allow_value:
            return replace(
                view,
                current_source_date=(
                    _ls_t8412_current_source_date(current)
                    if isinstance(current, CurrentObservation) else current.source_date
                ),
                current_retrieved_at_utc=current.retrieved_at_utc,
                current_provider=current.provider,
                current_refresh_status="CURRENT_GATE_BLOCKED",
                current_route=(current.route_id if isinstance(current, CurrentObservation) else None),
                current_interval=(current.interval.value if isinstance(current, CurrentObservation) else None),
                current_finality=(current.finality.value if isinstance(current, CurrentObservation) else None),
                current_provider_timestamp_utc=(
                    current.provider_timestamp_utc if isinstance(current, CurrentObservation) else None
                ),
                current_source_route=(current.source_route if isinstance(current, CurrentObservation) else None),
                current_display_only=(current.display_only if isinstance(current, CurrentObservation) else None),
                current_pit_safe=(current.pit_safe if isinstance(current, CurrentObservation) else None),
                current_unavailable_reason=decision.reason,
            )
        if isinstance(current, CurrentObservation):
            return replace(
                view,
                current_value=current.value,
                current_unit=current.unit,
                current_source_date=_ls_t8412_current_source_date(current),
                current_retrieved_at_utc=current.retrieved_at_utc,
                current_provider=current.provider,
                current_refresh_status=(
                    decision.freshness
                    if current.route_id in {
                        TOSS_000660_NXT_CLOSE_UR240_ROUTE.route_id,
                        TOSS_005930_NXT_CLOSE_UR241_ROUTE.route_id,
                    }
                    else "CURRENT_SOURCE_TIMESTAMP_VALID"
                ),
                current_route=current.route_id,
                current_interval=current.interval.value,
                current_finality=current.finality.value,
                current_provider_timestamp_utc=current.provider_timestamp_utc,
                current_source_route=current.source_route,
                current_display_only=current.display_only,
                current_pit_safe=current.pit_safe,
                current_unavailable_reason=(
                    TOSS_NXT_CLOSE_UR240_PROVENANCE_WARNING + "; " + decision.reason
                    if current.route_id in {
                        TOSS_000660_NXT_CLOSE_UR240_ROUTE.route_id,
                        TOSS_005930_NXT_CLOSE_UR241_ROUTE.route_id,
                    }
                    else
                    NAVER_MOBILE_BASIC_UR199_PROVENANCE_WARNING
                    if current.route_id in {
                        NAVER_MOBILE_BASIC_000660_UR199_ROUTE.route_id,
                        NAVER_MOBILE_BASIC_005930_UR199_ROUTE.route_id,
                    }
                    else NAVER_WEB_000660_PROVENANCE_WARNING
                    if current.provider == "NAVER_FINANCE_WEB" else None
                ),
                current_visible_label=(
                    decision.visible_label
                    if current.route_id in {
                        TOSS_000660_NXT_CLOSE_UR240_ROUTE.route_id,
                        TOSS_005930_NXT_CLOSE_UR241_ROUTE.route_id,
                    }
                    else None
                ),
            )
        return replace(
            view,
            current_value=current.value,
            current_unit=current.unit,
            current_source_date=current.source_date,
            current_retrieved_at_utc=current.retrieved_at_utc,
            current_provider=current.provider,
            current_refresh_status=current.refresh_status,
        )

    @staticmethod
    def _optional_text(value: object) -> str | None:
        return None if pd.isna(value) or not str(value).strip() else str(value).strip()

    @classmethod
    def _optional_date(cls, value: object) -> str | None:
        text = cls._optional_text(value)
        if text in {None, "N/A"}:
            return None
        parsed = pd.to_datetime(text, errors="coerce")
        return None if pd.isna(parsed) else parsed.date().isoformat()

    @staticmethod
    def _unavailable(
        identity: EquityIdentity, period: str, reason: str, *,
        freshness: str = "UNKNOWN", expected: str | None = None,
        source: str = "local retained data",
        state: DashboardDisplayState = DashboardDisplayState.UNAVAILABLE,
    ) -> EquitySeriesView:
        return EquitySeriesView(
            identity=identity, period=period, frame=pd.DataFrame(),
            display_state=state, freshness=freshness, as_of=None,
            expected_as_of=expected, source=source, reference_kst=None,
            unavailable_reason=reason,
        )


@dataclass
class USEtfChartService:
    """Chart-only, local and provider-native view for the accepted 13-ETF seed set."""

    root: Path
    authorized_symbols: frozenset[str] = field(
        default_factory=lambda: US_ETF_CHART_AUTHORIZED_SYMBOLS,
    )

    def __post_init__(self) -> None:
        self.query = LocalParquetQuery(self.root / "data")
        self._catalog_by_symbol = {item.symbol: item for item in US_ETF_CHART_IDENTITIES}
        self.authorized_symbols = frozenset(str(item).upper() for item in self.authorized_symbols)
        unknown = self.authorized_symbols.difference(self._catalog_by_symbol)
        if unknown:
            raise ValueError(f"unknown authorized U.S. ETF symbols: {sorted(unknown)}")

    def search(self, text: str, *, limit: int = 30) -> EquitySearchView:
        query = str(text or "").strip()
        if not query:
            return EquitySearchView(query, (), "ETF 이름 또는 티커를 입력하세요.")
        if limit < 1:
            raise ValueError("search limit must be positive")
        folded = query.casefold()
        catalog = US_ETF_CHART_IDENTITIES
        exact_ticker = [item for item in catalog if item.symbol.casefold() == folded]
        exact_name = [item for item in catalog if item.name.casefold() == folded]
        partial = [
            item for item in catalog
            if (
                folded in item.name.casefold()
                or folded in (item.issuer or "").casefold()
                or folded in (item.exposure or "").casefold()
            )
            and item not in exact_ticker
            and item not in exact_name
        ]
        matches = tuple((exact_ticker + exact_name + partial)[:limit])
        return EquitySearchView(
            query, matches,
            None if matches else "승인된 13개 미국 ETF 범위에서 일치 항목을 찾지 못했습니다.",
        )

    def series(
        self, identity: EquityIdentity, period: str = "120D", *, health: object | None = None,
    ) -> EquitySeriesView:
        if period not in PERIOD_ROWS and period != "MAX":
            raise ValueError(f"unsupported ETF period: {period}")
        canonical = self._catalog_by_symbol.get(identity.symbol)
        if canonical is None or not identity.is_us_etf:
            return self._unavailable(identity, period, "승인된 미국 ETF 식별정보가 아닙니다.")
        if canonical != identity:
            return self._unavailable(
                identity, period, "선택한 ETF의 이름·티커·발행사·노출 식별정보가 일치하지 않습니다.",
            )

        if canonical.symbol not in self.authorized_symbols:
            return self._unavailable(
                canonical,
                period,
                (
                    f"{canonical.symbol} is outside the Data Status-authorized local "
                    "global_etf_price_daily symbol scope. The current accepted lane is "
                    "SOXX-only; no external lookup, substitution, collection, or numeric "
                    "display was performed."
                ),
                freshness="BLOCKED",
                source="yahoo_chart_api; accepted local scope: SOXX only",
                state=DashboardDisplayState.PROHIBITED,
            )

        if health is None:
            from stock_data.gui.health_service import DailyHealthArtifactService

            health = DailyHealthArtifactService(self.root).load()
        rows = {getattr(row, "dataset", None): row for row in getattr(health, "rows", ())}
        row = rows.get("global_etf_price_daily")
        if getattr(health, "artifact_state", None) != "READY" or row is None:
            return self._unavailable(
                canonical, period, "미국 ETF 가격 데이터의 Health 상태를 확인할 수 없습니다.",
            )
        freshness = str(getattr(row, "freshness", "UNKNOWN"))
        expected = EquityChartService._optional_date(getattr(row, "expected", None))
        retained = EquityChartService._optional_date(getattr(row, "latest", None))
        source = str(getattr(row, "source", "local retained provider-native data"))
        if getattr(row, "operational", None) == "BLOCKED":
            return self._unavailable(
                canonical, period, str(getattr(row, "blocker", "운영 차단 상태입니다.")),
                freshness=freshness, expected=expected, source=source,
                state=DashboardDisplayState.PROHIBITED,
            )
        if str(getattr(row, "runtime_coverage", "")).startswith("FAILED:"):
            return self._unavailable(
                canonical, period, "미국 ETF 가격의 로컬 계약 검증에 실패했습니다.",
                freshness=freshness, expected=expected, source=source,
            )
        if freshness not in DISPLAYABLE_FRESHNESS and freshness != "STALE":
            reason = (
                f"가격 기준일 {retained or '미확인'}이 기대 완료일 {expected or '미확인'}보다 오래되었습니다."
                if freshness == "STALE" else "미국 ETF 가격의 최신 상태를 확인할 수 없습니다."
            )
            return self._unavailable(
                canonical, period, reason, freshness=freshness, expected=expected,
                source=source,
                state=(DashboardDisplayState.REFRESH_REQUIRED
                       if freshness == "STALE" else DashboardDisplayState.UNAVAILABLE),
            )

        requested = 999999999 if period == "MAX" else PERIOD_ROWS[period] + 130
        columns = [
            "date", "symbol", "source_ticker", "open", "high", "low", "close",
            "volume", "currency", "exchange", "provider", "adjustment_status",
        ]
        try:
            read = self.query.read if period == "MAX" else self.query.tail
            kwargs = {
                "columns": columns,
                "partitions": {"symbol": canonical.symbol},
                "filters": {"symbol": (canonical.symbol,)},
            }
            frame = (
                read("normalized/global_etf_price_daily", **kwargs)
                if period == "MAX" else
                read("normalized/global_etf_price_daily", rows=requested, **kwargs)
            )
            frame = self._validated_original_frame(frame, canonical)
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            frame = pd.DataFrame()
        if frame.empty:
            return self._unavailable(
                canonical, period,
                "이 ETF의 승인된 로컬 원본 일봉 OHLCV가 없습니다. 외부 조회나 대체 시계열은 실행하지 않습니다.",
                freshness=freshness, expected=expected, source=source,
            )

        as_of = frame["date"].iloc[-1].date().isoformat()
        if retained is None or as_of != retained:
            return self._unavailable(
                canonical, period,
                f"ETF 기준일 {as_of}이 검증된 데이터 기준일 {retained or '미확인'}과 다릅니다.",
                freshness="STALE", expected=expected, source=source,
                state=DashboardDisplayState.REFRESH_REQUIRED,
            )
        close = frame["close"]
        for window in (5, 20, 60, 120):
            frame[f"ma{window}"] = close.rolling(window).mean()
        frame["rsi14"] = _wilder_rsi(close)
        frame["disparity60"] = close / close.rolling(60).mean() * 100
        frame = technical_indicators(frame)
        if period != "MAX":
            frame = frame.tail(PERIOD_ROWS[period]).reset_index(drop=True)
        change = float(frame["close"].iloc[-1] - frame["close"].iloc[-2]) if len(frame) > 1 else None
        previous = float(frame["close"].iloc[-2]) if len(frame) > 1 else None
        change_pct = change / previous * 100 if change is not None and previous else None
        return EquitySeriesView(
            identity=canonical, period=period, frame=frame,
            display_state=DashboardDisplayState.VALUE, freshness=freshness,
            as_of=as_of, expected_as_of=expected, source=source,
            reference_kst=f"{as_of} 미국 거래일 · KST 표시 기준 · 정확한 게시시각 미보존",
            price_mode="공급자 원본(미조정) OHLCV · USD",
            change=change, change_pct=change_pct,
            period_high=float(frame["high"].max()), period_low=float(frame["low"].min()),
            unavailable_reason=(
                f"STALE retained history: as_of={as_of}, expected={expected or 'UNKNOWN'}; "
                "current-data claims and actions remain blocked."
                if freshness == "STALE" else None
            ),
        )

    @staticmethod
    def _validated_original_frame(
        frame: pd.DataFrame, identity: EquityIdentity,
    ) -> pd.DataFrame:
        if frame.empty:
            return frame
        result = frame.copy()
        if set(result["symbol"].astype(str)) != {identity.symbol}:
            raise ValueError("ETF symbol identity differs")
        if set(result["source_ticker"].astype(str)) != {identity.symbol}:
            raise ValueError("ETF provider ticker identity differs")
        if set(result["currency"].astype(str)) != {"USD"}:
            raise ValueError("ETF currency must remain USD")
        if set(result["provider"].astype(str)) != {"yahoo_chart_api"}:
            raise ValueError("ETF source is not the accepted retained provider lane")
        if set(result["adjustment_status"].astype(str)) != {
            "SOURCE_ADJUSTED_CLOSE_RETAINED_SEPARATELY"
        }:
            raise ValueError("ETF provider-native and adjusted-price semantics are not separated")
        if result["exchange"].isna().any() or result["exchange"].astype(str).str.strip().eq("").any():
            raise ValueError("ETF exchange identity is missing")
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        for column in ("open", "high", "low", "close", "volume"):
            result[column] = pd.to_numeric(result[column], errors="coerce")
        required = ["date", "open", "high", "low", "close", "volume"]
        if result[required].isna().any().any():
            raise ValueError("ETF OHLCV contains missing values")
        result = result.sort_values("date")
        if result["date"].duplicated().any():
            raise ValueError("duplicate ETF session")
        valid_ohlc = (
            result["high"].ge(result[["open", "close"]].max(axis=1))
            & result["low"].le(result[["open", "close"]].min(axis=1))
            & result["low"].le(result["high"])
            & result[["open", "high", "low", "close"]].gt(0).all(axis=1)
            & result["volume"].ge(0)
            & result["volume"].mod(1).eq(0)
        )
        if not valid_ohlc.all():
            raise ValueError("invalid provider-native ETF OHLCV")
        if identity.listing_date is not None:
            if result["date"].lt(pd.Timestamp(identity.listing_date)).any():
                raise ValueError("ETF OHLCV predates the exact fund inception")
        return result.reset_index(drop=True)

    @staticmethod
    def _unavailable(
        identity: EquityIdentity, period: str, reason: str, *,
        freshness: str = "UNKNOWN", expected: str | None = None,
        source: str = "local retained data",
        state: DashboardDisplayState = DashboardDisplayState.UNAVAILABLE,
    ) -> EquitySeriesView:
        return EquitySeriesView(
            identity=identity, period=period, frame=pd.DataFrame(),
            display_state=state, freshness=freshness, as_of=None,
            expected_as_of=expected, source=source, reference_kst=None,
            price_mode="공급자 원본(미조정) OHLCV · USD",
            unavailable_reason=reason,
        )

@dataclass
class DerivativesDashboardService:
    project_root: Path
    query: LocalParquetQuery

    def option_wall(self) -> tuple[pd.DataFrame, dict]:
        path = self.project_root / "artifacts/analysis/kospi200_option_wall_recent_250.csv"
        if not path.exists():
            return pd.DataFrame(), {"status": "N/A", "reason": "retained Wall review artifact missing"}
        frame = pd.read_csv(path, parse_dates=["date"])
        for side in ("call", "put"):
            frame[f"{side}_distance_percentile_250d"] = frame[f"{side}_wall_distance_pct"].rank(pct=True) * 100
        return frame, {"status": "RAW", "as_of": frame["date"].max(), "pit": "PIT_SAFE_EOD_T_PLUS_1"}

    def pcr(self, days: int = 60) -> pd.DataFrame:
        frame = self.query.tail("derived/kr_kospi200_option_pcr_daily", rows=days, columns=["date", "volume_pcr", "open_interest_pcr", "observation_status"])
        frame = frame.sort_values("date").tail(days).copy()
        for col in ("volume_pcr", "open_interest_pcr"):
            frame[f"{col}_percentile"] = frame[col].rank(pct=True) * 100
        return frame

    def ls_flow(self, session: str = "U") -> dict:
        daily = sorted((self.project_root / "data/landing/ls_openapi/t8462_daily_raw").rglob(f"*_K2I_F_{session}.response.json"))
        historical = sorted((self.project_root / "data/landing/ls_openapi/t8462_raw").rglob(f"*_K2I_F_{session}.response.json"))
        candidates = daily or historical
        if not candidates:
            return {"status": "N/A", "reason": "retained LS session response missing"}
        selected = candidates[-1]
        payload = json.loads(selected.read_text(encoding="utf-8"))
        row = payload.get("t8462OutBlock1", [{}])[0]
        provenance_path = selected.with_name(selected.name.replace(".response.json", ".provenance.json"))
        provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.exists() else {}
        institution = pd.to_numeric(row.get("sv_18"), errors="coerce")
        other = pd.to_numeric(row.get("sv_07"), errors="coerce")
        institution_amount = pd.to_numeric(row.get("sa_18"), errors="coerce")
        other_amount = pd.to_numeric(row.get("sa_07"), errors="coerce")
        return {"date": pd.to_datetime(row.get("date"), format="%Y%m%d", errors="coerce"), "individual_contracts": pd.to_numeric(row.get("sv_08"), errors="coerce"), "foreign_contracts": pd.to_numeric(row.get("sv_17"), errors="coerce"), "institutional_complex_contracts": None if pd.isna(institution) or pd.isna(other) else institution + other, "foreign_amount_100m_krw": pd.to_numeric(row.get("sa_17"), errors="coerce"), "institutional_complex_amount_100m_krw": None if pd.isna(institution_amount) or pd.isna(other_amount) else institution_amount + other_amount, "status": "RAW_DESCRIPTIVE_ONLY", "source": "LS_OPENAPI:t8462", "route": "DAILY_RAW" if daily else "HISTORICAL_RESEARCH_RAW", "session_code": session, "availability_at": provenance.get("captured_at"), "predictive_status": "PIT_BLOCKED_SESSION_FINALITY_REVISION_UNRESOLVED", "warning": "Raw provider observation; no Normalized/PIT-safe claim"}


@dataclass
class MarketMicrostructureService:
    project_root: Path
    query: LocalParquetQuery

    def breadth(self) -> list[dict]:
        frame = self.query.tail("derived/kr_market_breadth_daily", rows=80, columns=["date", "market", "advancing", "declining", "unchanged", "total"])
        out = []
        for market, group in frame.groupby("market"):
            row = _latest(group)
            row["ad_ratio"] = row["advancing"] / row["declining"] if row.get("declining") else None
            out.append(row)
        return out

    def short_selling(self, symbol: str = "005930", market_date: object | None = None) -> dict:
        """Serve official and provider EOD views without merging their scopes.

        The provider slot is intentionally restricted to the retained LS t1716
        Samsung evidence.  It is a per-symbol, provider-EOD observation, never a
        fallback for the official KRX aggregate.  Passing ``market_date`` requests
        an exact date; missing data stays missing instead of falling back.
        """
        columns = [
                "date", "market", "symbol", "source_name", "short_volume",
                "short_trading_value", "short_volume_ratio", "short_trading_value_ratio",
            ]
        requested_date = pd.to_datetime(market_date, errors="coerce") if market_date is not None else pd.NaT
        query_options = {
            "columns": columns,
            "partitions": {"market": "KOSPI"},
            "filters": {"symbol": (symbol,)},
        }
        official_raw = (
            self.query.read(
                "normalized/kr_short_selling_trading_daily",
                start=requested_date, end=requested_date, **query_options,
            )
            if not pd.isna(requested_date)
            else self.query.tail(
                "normalized/kr_short_selling_trading_daily", rows=30, **query_options,
            )
        )
        if not official_raw.empty:
            official_raw = official_raw[official_raw["symbol"].astype(str).eq(symbol)].copy()
            official_raw["date"] = pd.to_datetime(official_raw["date"], errors="coerce")
        if not official_raw.empty and not pd.isna(requested_date):
            official_raw = official_raw[official_raw["date"].eq(requested_date)]
        official_row = _latest(official_raw)
        official = None
        if official_row:
            date = pd.Timestamp(official_row["date"])
            official = {
                "symbol": symbol,
                "market": official_row.get("market"),
                "market_date": date,
                "volume": _to_float(official_row.get("short_volume")),
                "value": _to_float(official_row.get("short_trading_value")),
                "volume_ratio": _to_float(official_row.get("short_volume_ratio")),
                "value_ratio": _to_float(official_row.get("short_trading_value_ratio")),
                "volume_unit": "shares",
                "value_unit": "KRW",
                "amount_precision": "EXACT_KRW",
                "source": official_row.get("source_name") or SHORT_OFFICIAL_SOURCE,
                "scope": short_selling_scope_regime(date),
                "status": "LATEST_CONFIRMED",
                "semantic_confidence": "CONFIRMED_OFFICIAL",
            }

        provider = self._retained_ls_t1716_short_selling(symbol=symbol, market_date=requested_date)
        inferred = None
        if official and provider and pd.Timestamp(official["market_date"]).date() == pd.Timestamp(provider["market_date"]).date():
            amount_is_exact = provider.get("amount_precision") == "EXACT_KRW"
            inferred = {
                "market_date": official["market_date"],
                "volume": official["volume"] - provider["volume"],
                "volume_precision": "EXACT",
                "value": official["value"] - provider["value"] if amount_is_exact else None,
                "amount_precision": "EXACT" if amount_is_exact else "APPROXIMATE_FROM_TRUNCATED_PROVIDER_AMOUNT",
                "volume_unit": "shares",
                "value_unit": "KRW",
                "status": "AGGREGATE_MINUS_KRX_ONLY_INFERRED",
                "display_name": "Additional venue inferred",
            }
        return {
            "symbol": symbol,
            "official": official,
            "provider": provider,
            "inferred_additional_venue": inferred,
        }

    def _retained_ls_t1716_short_selling(self, *, symbol: str, market_date: pd.Timestamp) -> dict | None:
        if symbol != "005930":
            return None
        candidates = sorted((self.project_root / "data/landing/diagnostics/ls_openapi_source_inventory").glob("**/10_samsung_foreign_holding.response.json"))
        if not candidates:
            return None
        try:
            rows = json.loads(candidates[-1].read_text(encoding="utf-8")).get("t1716OutBlock", [])
        except (OSError, json.JSONDecodeError):
            return None
        frame = pd.DataFrame(rows)
        if frame.empty or not {"date", "gm_volume", "gm_value"}.issubset(frame.columns):
            return None
        frame["market_date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="coerce")
        if not pd.isna(market_date):
            frame = frame[frame["market_date"].eq(market_date)]
        row = _latest(frame)
        if not row:
            return None
        return {
            "symbol": symbol,
            "market": "KOSPI",
            "market_date": pd.Timestamp(row["market_date"]),
            "volume": _to_float(row.get("gm_volume")),
            "value": _to_float(row.get("gm_value")) * 1_000_000,
            "volume_ratio": None,
            "value_ratio": None,
            "volume_unit": "shares",
            "value_unit": "KRW",
            "source_value_unit": "million_KRW",
            "amount_precision": "TRUNCATED_TO_MILLION_KRW",
            "source": "LS OpenAPI t1716 (retained EOD evidence)",
            "scope": SHORT_PROVIDER_SCOPE,
            "status": "PROVIDER_EOD",
            "semantic_confidence": "CONFIRMED_EMPIRICAL_MULTI_DATE",
        }

    def lending_market(self) -> dict:
        frame = self.query.tail("normalized/kr_stock_lending_market_daily", rows=30, columns=["date", "balance_shares", "balance_amount"])
        frame = frame.sort_values("date")
        row = _latest(frame)
        if row:
            row.update({"change_1d": frame["balance_amount"].diff().iloc[-1], "change_5d": frame["balance_amount"].diff(5).iloc[-1]})
        return row

    def stocks(self, symbols: tuple[str, ...] = ("005930", "000660")) -> list[dict]:
        entity_filter = {"symbol": symbols}
        market_partition = {"market": "KOSPI"}
        price = self.query.tail("normalized/kr_equity_price_daily", rows=100, columns=["date", "market", "symbol", "close"], partitions=market_partition, filters=entity_filter)
        lending = self.query.tail("normalized/kr_stock_lending_daily", rows=100, columns=["date", "market", "symbol", "balance_shares", "balance_amount"], partitions=market_partition, filters=entity_filter)
        balance = self.query.tail("normalized/kr_short_selling_balance_daily", rows=100, columns=["date", "market", "symbol", "short_balance", "short_balance_value", "short_balance_ratio"], partitions=market_partition, filters=entity_filter)
        trading = self.query.tail("normalized/kr_short_selling_trading_daily", rows=100, columns=["date", "market", "symbol", "short_trading_value_ratio"], partitions=market_partition, filters=entity_filter)
        result = []
        for symbol in symbols:
            p = price[price.symbol.eq(symbol)].sort_values("date")
            l = lending[lending.symbol.eq(symbol)].sort_values("date")
            b = balance[balance.symbol.eq(symbol)].sort_values("date")
            t = trading[trading.symbol.eq(symbol)].sort_values("date")
            result.append({"symbol": symbol, "name": {"005930": "삼성전자", "000660": "SK하이닉스"}.get(symbol, symbol), "date": _latest(p).get("date"), "close": _latest(p).get("close"), "return_1d": _pct_change(p.get("close", pd.Series(dtype=float))), "return_5d": _pct_change(p.get("close", pd.Series(dtype=float)), 5), "lending_balance": _latest(l).get("balance_shares"), "lending_change_1d": l["balance_shares"].diff().iloc[-1] if len(l) > 1 else None, "short_balance": _latest(b).get("short_balance"), "short_balance_ratio": _latest(b).get("short_balance_ratio"), "short_trading_ratio": _latest(t).get("short_trading_value_ratio")})
        return result

    def program(
        self, *, expected_date: object | None = None, finality_accepted: bool = False,
    ) -> dict:
        """Read only the contract-shaped Normalized program summary.

        Raw/Landing evidence is deliberately never a GUI fallback. A caller must
        supply the expected completed market date and accepted finality before any
        number can be returned.
        """
        columns = list(LS_T1633_PROGRAM_TRADING_DAILY.column_names)
        frame = self.query.tail(
            f"normalized/{LS_T1633_PROGRAM_TRADING_DAILY.name}",
            rows=4, columns=columns,
        )
        if frame.empty:
            return {"status": "NOT_AVAILABLE", "reason": "NORMALIZED_DATASET_MISSING"}
        try:
            validate_ls_t1633_program_trading(frame)
        except (KeyError, TypeError, ValueError):
            return {"status": "NOT_AVAILABLE", "reason": "NORMALIZED_SCHEMA_INVALID"}
        latest = pd.to_datetime(frame["date"], errors="coerce").max()
        if pd.isna(latest):
            return {"status": "NOT_AVAILABLE", "reason": "NORMALIZED_DATE_INVALID"}
        latest_date = latest.date().isoformat()
        if expected_date is None:
            return {
                "status": "NOT_AVAILABLE", "date": latest_date,
                "reason": "EXPECTED_DATE_REQUIRED",
            }
        expected = pd.Timestamp(expected_date).date().isoformat()
        if latest_date != expected:
            return {
                "status": "REFRESH_REQUIRED", "date": latest_date,
                "expected_date": expected, "reason": "LATEST_DATE_DIFFERS",
            }
        if not finality_accepted:
            return {
                "status": "NOT_AVAILABLE", "date": latest_date,
                "reason": "PUBLICATION_AND_REVISION_FINALITY_REQUIRED",
            }
        exact = frame.loc[frame["date"].astype(str).eq(expected)]
        if len(exact) != 2 or set(exact["market"].astype(str)) != {"KOSPI", "KOSDAQ"}:
            return {
                "status": "NOT_AVAILABLE", "date": latest_date,
                "reason": "BOTH_MARKETS_REQUIRED",
            }
        markets = {}
        for _, row in exact.iterrows():
            markets[str(row["market"])] = {
                "total_net_amount_krw": int(row["total_net_amount"]),
                "arbitrage_net_amount_krw": int(row["arbitrage_net_amount"]),
                "non_arbitrage_net_amount_krw": int(row["non_arbitrage_net_amount"]),
            }
        return {"status": "CURRENT", "date": latest_date, "markets": markets}


@dataclass
class DashboardService:
    root: Path
    health_report: object | None = None

    def __post_init__(self) -> None:
        self.query = LocalParquetQuery(self.root / "data")
        self.index = IndexQueryService(self.query, self.root)
        self.equity = EquityChartService(self.root)
        self.us_etf = USEtfChartService(self.root)
        self.derivatives = DerivativesDashboardService(self.root, self.query)
        self.micro = MarketMicrostructureService(self.root, self.query)

    @staticmethod
    def _unavailable_market_valuations(
        reason: str,
        *,
        expected_as_of: str | None = None,
        as_of: str | None = None,
        state: DashboardDisplayState = DashboardDisplayState.UNAVAILABLE,
    ) -> dict[str, MarketValuationView]:
        return {
            market: MarketValuationView(
                market=market,
                index_code=index_code,
                as_of=as_of,
                expected_as_of=expected_as_of,
                weighted_per=None,
                weighted_pbr=None,
                per_mean=None,
                pbr_mean=None,
                per_median=None,
                pbr_median=None,
                per_percentile=None,
                pbr_percentile=None,
                per_observations=0,
                pbr_observations=0,
                per_baseline_start=None,
                per_baseline_end=None,
                pbr_baseline_start=None,
                pbr_baseline_end=None,
                baseline_start=None,
                baseline_end=None,
                source="KRX_MDCSTAT00702",
                display_state=state,
                unavailable_reason=reason,
            )
            for market, index_code in (("KOSPI", "1001"), ("KOSDAQ", "2001"))
        }

    @classmethod
    def build_market_valuation_views(
        cls,
        frame: pd.DataFrame,
        *,
        as_of: str,
        expected_as_of: str,
    ) -> dict[str, MarketValuationView]:
        """Build exact-date valuation context without using future observations."""

        try:
            if list(frame.columns) != list(KR_INDEX_FUNDAMENTAL_DAILY.column_names):
                raise ValueError("market valuation schema differs")
            candidate = frame.copy()
            dates = pd.to_datetime(
                candidate["date"], format="%Y-%m-%d", errors="coerce",
            )
            if dates.isna().any():
                raise ValueError("market valuation date is invalid")
            candidate["date"] = dates.dt.strftime("%Y-%m-%d")
            accepted_date = pd.Timestamp(as_of)
            expected_date = pd.Timestamp(expected_as_of)
            if (
                accepted_date.tzinfo is not None
                or expected_date.tzinfo is not None
                or accepted_date != accepted_date.normalize()
                or expected_date != expected_date.normalize()
            ):
                raise ValueError("market valuation boundary must be an exact date")
            if accepted_date != expected_date:
                return cls._unavailable_market_valuations(
                    "KR_INDEX_FUNDAMENTAL_STALE: accepted date differs from the latest completed XKRX session.",
                    expected_as_of=expected_as_of,
                    as_of=as_of,
                    state=DashboardDisplayState.REFRESH_REQUIRED,
                )
            eligible = candidate.loc[
                pd.to_datetime(candidate["date"]).le(accepted_date)
            ].copy()
            # Future observations are outside the as-of view.  Validate only
            # the eligible slice so malformed future values cannot alter a
            # previously accepted current view.
            validate_kr_index_fundamental_daily(eligible)
            if eligible.groupby("date")["index_code"].nunique().ne(2).any():
                raise ValueError("market valuation dates do not contain both identities")
            exact = eligible.loc[eligible["date"].eq(as_of)]
            if (
                len(exact) != 2
                or set(exact["index_code"].astype(str)) != {"1001", "2001"}
                or set(exact["market"].astype(str)) != {"KOSPI", "KOSDAQ"}
            ):
                raise ValueError("market valuation accepted date identity differs")
        except (KeyError, TypeError, ValueError, OverflowError):
            return cls._unavailable_market_valuations(
                "KR_INDEX_FUNDAMENTAL_INVALID: schema, identity, date, or value validation failed.",
                expected_as_of=expected_as_of,
                as_of=as_of,
            )

        result: dict[str, MarketValuationView] = {}
        for market, index_code in (("KOSPI", "1001"), ("KOSDAQ", "2001")):
            history = eligible.loc[
                eligible["index_code"].astype(str).eq(index_code)
            ].sort_values("date", kind="stable")
            latest = history.loc[history["date"].eq(as_of)].iloc[0]

            def context(
                column: str,
            ) -> tuple[
                float | None, float | None, float | None, float | None,
                int, str | None, str | None,
            ]:
                numeric = pd.to_numeric(history[column], errors="coerce")
                valid = numeric.notna()
                values = numeric.loc[valid]
                baseline_start = (
                    str(history.loc[valid, "date"].iloc[0])
                    if not values.empty else None
                )
                baseline_end = (
                    str(history.loc[valid, "date"].iloc[-1])
                    if not values.empty else None
                )
                latest_value = pd.to_numeric(
                    pd.Series([latest[column]]), errors="coerce",
                ).iloc[0]
                if pd.isna(latest_value) or values.empty:
                    return (
                        None, None, None, None, int(len(values)),
                        baseline_start, baseline_end,
                    )
                value = float(latest_value)
                mean = float(values.mean())
                median = float(values.median())
                percentile = float(values.le(value).mean() * 100.0)
                if not all(
                    np.isfinite(item) and item > 0.0
                    for item in (value, mean, median)
                ) or not np.isfinite(percentile):
                    return None, None, None, None, int(len(values)), None, None
                return (
                    value, mean, median, percentile, int(len(values)),
                    baseline_start, baseline_end,
                )

            (
                per, per_mean, per_median, per_percentile, per_count,
                per_start, per_end,
            ) = context("weighted_per")
            (
                pbr, pbr_mean, pbr_median, pbr_percentile, pbr_count,
                pbr_start, pbr_end,
            ) = context("weighted_pbr")

            def rolling_percentile(
                column: str, years: int,
            ) -> tuple[float | None, int, str | None, str | None]:
                window_start = accepted_date - pd.DateOffset(years=years)
                window = history.loc[
                    pd.to_datetime(history["date"]).ge(window_start)
                ]
                numeric = pd.to_numeric(window[column], errors="coerce")
                valid = numeric.notna()
                values = numeric.loc[valid]
                start = (
                    str(window.loc[valid, "date"].iloc[0])
                    if not values.empty else None
                )
                end = (
                    str(window.loc[valid, "date"].iloc[-1])
                    if not values.empty else None
                )
                latest_value = pd.to_numeric(
                    pd.Series([latest[column]]), errors="coerce",
                ).iloc[0]
                if pd.isna(latest_value) or values.empty:
                    return None, int(len(values)), start, end
                percentile = float(values.le(float(latest_value)).mean() * 100.0)
                if not np.isfinite(percentile):
                    return None, int(len(values)), start, end
                return percentile, int(len(values)), start, end

            rolling_windows = []
            for years in (5, 10):
                per_window = rolling_percentile("weighted_per", years)
                pbr_window = rolling_percentile("weighted_pbr", years)
                rolling_windows.append(MarketValuationWindowView(
                    window_years=years,
                    per_percentile=per_window[0],
                    pbr_percentile=pbr_window[0],
                    per_observations=per_window[1],
                    pbr_observations=pbr_window[1],
                    per_baseline_start=per_window[2],
                    per_baseline_end=per_window[3],
                    pbr_baseline_start=pbr_window[2],
                    pbr_baseline_end=pbr_window[3],
                ))
            result[market] = MarketValuationView(
                market=market,
                index_code=index_code,
                as_of=as_of,
                expected_as_of=expected_as_of,
                weighted_per=per,
                weighted_pbr=pbr,
                per_mean=per_mean,
                pbr_mean=pbr_mean,
                per_median=per_median,
                pbr_median=pbr_median,
                per_percentile=per_percentile,
                pbr_percentile=pbr_percentile,
                per_observations=per_count,
                pbr_observations=pbr_count,
                per_baseline_start=per_start,
                per_baseline_end=per_end,
                pbr_baseline_start=pbr_start,
                pbr_baseline_end=pbr_end,
                baseline_start=str(history["date"].iloc[0]),
                baseline_end=as_of,
                source="KRX_MDCSTAT00702",
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=(
                    "Provider missing value retained as unavailable."
                    if per is None or pbr is None else None
                ),
                rolling_windows=tuple(rolling_windows),
            )
        return result

    def market_valuation_views(
        self, *, now_utc: object | None = None,
    ) -> dict[str, MarketValuationView]:
        """Read the accepted local KRX valuation contract and state, API zero."""

        now = pd.Timestamp(now_utc if now_utc is not None else pd.Timestamp.now(tz="UTC"))
        if now.tzinfo is None:
            return self._unavailable_market_valuations(
                "KR_INDEX_FUNDAMENTAL_CLOCK_INVALID: an aware readback clock is required."
            )
        expected_as_of: str | None = None
        state_path = self.root / "data/state/kr_index_fundamental_daily.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            accepted_as_of = state["last_accepted_market_date"]
            if (
                state.get("schema_version") != 1
                or state.get("status") != "ACCEPTED_DESCRIPTIVE_NON_PREDICTIVE"
                or state.get("predictive_eligibility") != "NON_PREDICTIVE"
                or not isinstance(accepted_as_of, str)
                or not isinstance(state.get("rows"), int)
            ):
                raise ValueError("market valuation state differs")
            accepted_date = datetime.strptime(
                accepted_as_of, "%Y-%m-%d",
            ).date()
            if accepted_date.isoformat() != accepted_as_of:
                raise ValueError("market valuation accepted date is not canonical")
            expected = resolve_expected_latest(
                dataset="kr_index_fundamental_daily",
                lane="KR_INDEX_FUNDAMENTAL_DAILY",
                retained_latest=accepted_date,
                as_of=now.to_pydatetime(),
            )
            if expected is None or expected.expected_available_observation is None:
                raise ValueError("market valuation availability policy is unavailable")
            expected_as_of = expected.expected_available_observation.isoformat()
            if accepted_as_of != expected_as_of:
                return self._unavailable_market_valuations(
                    "KR_INDEX_FUNDAMENTAL_STALE: accepted date differs from the latest completed XKRX session.",
                    expected_as_of=expected_as_of,
                    as_of=accepted_as_of,
                    state=DashboardDisplayState.REFRESH_REQUIRED,
                )
            frame = self.query.read(
                "normalized/kr_index_fundamental_daily",
                columns=list(KR_INDEX_FUNDAMENTAL_DAILY.column_names),
            )
            if len(frame) != state["rows"]:
                raise ValueError("market valuation state row count differs")
            frame = frame.copy()
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime(
                "%Y-%m-%d"
            )
            frame = frame.sort_values(
                list(KR_INDEX_FUNDAMENTAL_DAILY.sort_key), kind="stable",
            ).reset_index(drop=True)
            latest_by_market = frame.groupby("market")["date"].max().to_dict()
            if latest_by_market != {"KOSDAQ": accepted_as_of, "KOSPI": accepted_as_of}:
                raise ValueError("market valuation latest dates differ from state")
            return self.build_market_valuation_views(
                frame, as_of=accepted_as_of, expected_as_of=expected_as_of,
            )
        except (
            FileNotFoundError, json.JSONDecodeError, KeyError, OSError,
            PermissionError, TypeError, ValueError,
        ):
            return self._unavailable_market_valuations(
                "KR_INDEX_FUNDAMENTAL_UNAVAILABLE: local contract or state readback failed.",
                expected_as_of=expected_as_of,
            )

    @staticmethod
    def _gate_current_metric(
        metric: DashboardMetricView, *, now_utc: object,
        allow_kr_market_closed_last_verified: bool = False,
    ) -> DashboardMetricView:
        """Remove a headline/card numeric that lacks an eligible source time."""
        if not metric.displays_value:
            return metric
        # These values are explicitly finalized daily observations. Their
        # source date, rather than an invented intraday timestamp, is the
        # display boundary. Current-only projections continue through the
        # timestamp gate below.
        if not (
            allow_kr_market_closed_last_verified
            and metric.dataset_id == "kr_index_daily"
        ) and metric.route in {
            "DERIVED_DAILY_T_PLUS_1",
            "OFFICIAL_DAILY_MARKET_AGGREGATE",
            "NORMALIZED_DAILY",
            "DERIVED_CONTRACT",
            "DERIVED_EXACT_DATE_CONTRACT",
        } and metric.freshness in {
            "CURRENT", "EXPECTED_LAG", "MARKET_CLOSED_LAST_VERIFIED",
        }:
            return metric
        if (
            metric.route.startswith("yahoo-market-current:CBOE:")
            and metric.series_id in {"^FVX", "^TNX", "^TYX"}
            and metric.source_timestamp is not None
        ):
            try:
                now = pd.Timestamp(now_utc)
                source = pd.Timestamp(metric.source_timestamp)
                latest_us_session = ExchangeTradingCalendar(
                    ExchangeMarket.US
                ).latest_completed_session(now.to_pydatetime())
                if source.tz_convert("America/New_York").date() == latest_us_session:
                    return replace(
                        metric,
                        freshness="MARKET_CLOSED_LAST_VERIFIED",
                        unavailable_reason=(
                            "YAHOO_CBOE_LAST_COMPLETED_SESSION: latest retained "
                            "indicative quote index; not an official Treasury yield."
                        ),
                    )
            except (TypeError, ValueError):
                pass
        if (
            metric.dataset_id == "market_price_60m_current"
            and metric.source_timestamp is not None
        ):
            try:
                now = pd.Timestamp(now_utc)
                source = pd.Timestamp(metric.source_timestamp)
                if now.tzinfo is None or source.tzinfo is None:
                    raise ValueError("aware current-only timestamps required")
                now = now.tz_convert("UTC")
                source = source.tz_convert("UTC")
                if metric.series_id in {"KOSPI", "KOSDAQ"}:
                    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
                    completed_session = calendar.latest_completed_session(
                        now.to_pydatetime()
                    )
                    if (
                        source.tz_convert("Asia/Seoul").date() == completed_session
                        and source >= pd.Timestamp(
                            calendar.session_close(completed_session)
                        ).tz_convert("UTC")
                    ):
                        return replace(
                            metric,
                            freshness="MARKET_CLOSED_LAST_FINAL",
                            unavailable_reason=(
                                "YAHOO_KRX_FINAL_COMPLETED_BAR: latest completed KRX "
                                "cash-session close; display-only and fixed until a "
                                "newer accepted observation."
                            ),
                        )
                if metric.series_id in {"NASDAQ", "SP500", "SOXX"}:
                    latest_us_session = ExchangeTradingCalendar(
                        ExchangeMarket.US
                    ).latest_completed_session(now.to_pydatetime())
                    source_ny = source.tz_convert("America/New_York")
                    if (
                        source_ny.date() == latest_us_session
                        and source_ny.time() >= time(16, 0)
                    ):
                        return replace(
                            metric,
                            freshness="MARKET_CLOSED_LAST_FINAL",
                            unavailable_reason=(
                                "XNYS_FINAL_COMPLETED_60M: latest completed U.S. "
                                "cash-session close; fixed until the next session."
                            ),
                        )
                if metric.series_id in {"NQ_FUTURES", "GOLD", "WTI", "USD_KRW_60M"}:
                    closure = classify_intraday_60m_freshness(
                        bar_end=source, now_utc=now,
                    )
                    if closure.freshness == "MARKET_CLOSED_LAST_FINAL":
                        return replace(
                            metric,
                            freshness=closure.freshness,
                            unavailable_reason=(
                                "PROVIDER_FX_WEEKEND_CLOSE: latest completed provider "
                                "FX-session bar."
                                if metric.series_id == "USD_KRW_60M" else
                                "PROVIDER_FUTURES_WEEKEND_CLOSE: latest completed "
                                "provider-session bar; not an official settlement."
                            ),
                        )
            except (TypeError, ValueError):
                pass
        if (
            allow_kr_market_closed_last_verified
            and metric.dataset_id == "kr_index_daily"
            and metric.route == "NORMALIZED_DAILY"
            and metric.as_of is not None
        ):
            try:
                now = pd.Timestamp(now_utc)
                if now.tzinfo is None:
                    raise ValueError("aware clock required")
                completed = ExchangeTradingCalendar(
                    ExchangeMarket.KR
                ).latest_completed_session(now.to_pydatetime())
                if metric.as_of == completed.isoformat():
                    return replace(
                        metric,
                        freshness="MARKET_CLOSED_LAST_FINAL",
                        unavailable_reason=(
                            "KRX_FINAL_DAILY_CLOSE: latest completed KRX session close; "
                            "fixed until the next session is finalized."
                        ),
                    )
            except (TypeError, ValueError):
                pass
        decision = classify_current_display_timestamp(
            source_timestamp=metric.source_timestamp, now_utc=now_utc,
            allow_kr_market_closed_last_verified=allow_kr_market_closed_last_verified,
            retrieved_at=metric.retrieved_at_utc,
            timestamp_basis=metric.timestamp_basis,
        )
        if decision.allow_value:
            return replace(
                metric,
                freshness=decision.freshness or metric.freshness,
                unavailable_reason=(
                    decision.reason or metric.unavailable_reason
                ),
            )
        return replace(
            metric,
            value=None,
            change=None,
            change_pct=None,
            freshness="CURRENT_GATE_BLOCKED",
            display_state=DashboardDisplayState.REFRESH_REQUIRED,
            unavailable_reason=decision.reason,
        )

    @staticmethod
    def _gate_current_coverage(
        view: CurrentObservationCoverageView, *, now_utc: object,
    ) -> CurrentObservationCoverageView:
        if not view.displays_value:
            return view
        if view.nxt_session_gate:
            now = pd.Timestamp(now_utc).to_pydatetime()
            decision = classify_korean_equity_nxt_timestamp(
                provider_timestamp_utc=view.provider_timestamp_utc,
                now_utc=now,
                session_start_kst=view.nxt_session_start_kst,
                venue_inferred=view.nxt_venue_inferred,
            )
            if decision.allow_value:
                return replace(
                    view,
                    freshness=decision.freshness,
                    unavailable_reason=(
                        (view.unavailable_reason + "; " if view.unavailable_reason else "")
                        + decision.reason
                    ),
                    visible_label=decision.visible_label,
                )
            return replace(
                view,
                value=None,
                freshness="CURRENT_GATE_BLOCKED",
                display_state=DashboardDisplayState.REFRESH_REQUIRED,
                unavailable_reason=decision.reason,
                visible_label=None,
            )
        decision = classify_current_display_timestamp(
            source_timestamp=view.provider_timestamp_utc, now_utc=now_utc,
            retrieved_at=view.retrieved_at_utc,
            timestamp_basis=view.timestamp_basis,
            allow_kr_market_closed_last_verified=(
                view.coverage_id in {"KOSPI", "KOSDAQ"}
            ),
        )
        if decision.allow_value:
            return replace(
                view,
                freshness=decision.freshness or view.freshness,
                unavailable_reason=(
                    decision.reason or view.unavailable_reason
                ),
            )
        return replace(
            view,
            value=None,
            freshness="CURRENT_GATE_BLOCKED",
            display_state=DashboardDisplayState.REFRESH_REQUIRED,
            unavailable_reason=decision.reason,
        )

    @staticmethod
    def _gate_market_flow(
        view: MarketInvestorFlowView, *, now_utc: object,
    ) -> MarketInvestorFlowView:
        """Keep an exact finalized latest KRX session as a market-close view."""
        if view.display_state is not DashboardDisplayState.VALUE:
            return view
        try:
            now = pd.Timestamp(now_utc)
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("now_utc must be timezone-aware")
            calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
            latest = calendar.latest_completed_session(now.tz_convert("Asia/Seoul"))
            if (
                view.finality == "DAILY_FINAL"
                and view.as_of == pd.Timestamp(latest).date().isoformat()
            ):
                return replace(
                    view,
                    freshness="MARKET_CLOSED_LAST_FINAL",
                    unavailable_reason=None,
                )
        except (TypeError, ValueError):
            pass
        decision = classify_current_display_timestamp(source_timestamp=None, now_utc=now_utc)
        return replace(
            view,
            values=tuple(
                replace(value, latest_value=None, week_to_date_value=None)
                for value in view.values
            ),
            freshness="CURRENT_GATE_BLOCKED",
            display_state=DashboardDisplayState.REFRESH_REQUIRED,
            unavailable_reason=decision.reason,
            weekly_unavailable_reason=decision.reason,
        )

    @staticmethod
    def _gate_toss_short_watchlist(
        view: TossShortWatchlistView, *, now_utc: object,
    ) -> TossShortWatchlistView:
        """Retained EOD rows remain contextual only without an intraday source time."""
        if not view.displays_values:
            return view
        decision = classify_current_display_timestamp(source_timestamp=None, now_utc=now_utc)
        return replace(
            view,
            members=(),
            freshness="CURRENT_GATE_BLOCKED",
            display_state=DashboardDisplayState.REFRESH_REQUIRED,
            unavailable_reason=decision.reason,
        )

    def benchmark_comparison(
        self, target: EquitySeriesView,
    ) -> NormalizedBenchmarkComparisonView:
        """Build one optional comparison without broadening either chart's read gate."""
        if target.identity.is_us_etf:
            # UR-054's production seed-fund gate is checked by the ETF service
            # before a price read. Do not open an ETF or global-index file here:
            # USD benchmarks remain named but numeric-free until that gate and an
            # exact benchmark route are independently accepted.
            return NormalizedBenchmarkComparisonView.unavailable(
                target.identity, target.period,
                target.unavailable_reason or (
                    "U.S. ETF comparison is unavailable until the approved ETF and exact "
                    "S&P 500/Nasdaq-100 benchmark lanes are displayable."
                ),
                benchmark_id="SP500_OR_NASDAQ100",
                benchmark_label="S&P 500 (SP500) or Nasdaq-100 (NASDAQ100)",
                currency="USD", target_freshness=target.freshness,
                target_as_of=target.as_of,
            )
        if target.identity.market not in {"KOSPI", "KOSDAQ"}:
            return NormalizedBenchmarkComparisonView.unavailable(
                target.identity, target.period, "No exact Korean-market benchmark is configured.",
                benchmark_id="UNAVAILABLE", benchmark_label="No benchmark", currency="KRW",
                target_freshness=target.freshness, target_as_of=target.as_of,
            )
        if not target.displays_values:
            return NormalizedBenchmarkComparisonView.unavailable(
                target.identity, target.period,
                target.unavailable_reason or "The selected original-price series is not displayable.",
                benchmark_id=target.identity.market,
                benchmark_label=f"{target.identity.market} (KRX:{target.identity.market})",
                currency=target.identity.currency or "KRW", target_freshness=target.freshness,
                target_as_of=target.as_of,
            )
        benchmark = self.index.chart_view(target.identity.market, target.period)
        return NormalizedBenchmarkComparisonView.from_exact_common_sessions(target, benchmark)

    @staticmethod
    def _current_projection_unavailable(
        series_id: str, label: str, unit: str, reason: str,
    ) -> DashboardMetricView:
        return DashboardMetricView(
            dataset_id="market_price_current", series_id=series_id, label=label,
            value=None, unit=unit, as_of=None, expected_as_of=None,
            source="accepted local current projection", freshness="UNKNOWN",
            pit_status="PIT_BLOCKED", pit_label="표시 전용 · Backtest 사용 불가",
            automation_policy="EVERY_30_MIN_CURRENT_ONLY", automation_enabled=True,
            display_state=DashboardDisplayState.UNAVAILABLE,
            unavailable_reason=reason, route="LOCAL_CURRENT_PROJECTION_UNAVAILABLE",
        )

    def current_card_stage(
        self, *, now_utc: object | None = None,
    ) -> DashboardCurrentStageView:
        """Read only accepted current JSON projections; provider and Parquet zero."""

        now = pd.Timestamp(
            now_utc if now_utc is not None else pd.Timestamp.now(tz="UTC")
        )
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("current-card stage clock must be timezone-aware")
        now = now.tz_convert("UTC")
        global_current, global_reason = load_global60m_ur232_current_observations(
            self.root
        )
        metrics: dict[str, DashboardMetricView] = {}
        definitions = (
            ("KOSPI_CURRENT_60M", "KOSPI", "KOSPI", "index points"),
            ("KOSDAQ_CURRENT_60M", "KOSDAQ", "KOSDAQ", "index points"),
            ("NQ_FUTURES_CURRENT_60M", "NQ_FUTURES", "Nasdaq 100", "index points"),
            ("NASDAQ_CURRENT_60M", "NASDAQ", "Nasdaq", "index points"),
            ("SP500_CURRENT_60M", "SP500", "S&P 500", "index points"),
            ("SOXX_CURRENT_60M", "SOXX", "SOXX", "USD per share"),
            ("GOLD_CURRENT_60M", "GOLD", "GOLD", "provider native continuous futures price"),
            ("WTI_CURRENT_60M", "WTI", "WTI", "provider native continuous futures price"),
            ("BITCOIN_CURRENT_60M", "BITCOIN", "BITCOIN", "USD per BTC"),
            ("USD_KRW_60M", "USD_KRW_60M", "USD/KRW", "KRW per USD"),
        )
        for coverage_id, metric_id, label, unit in definitions:
            observation = global_current.get(coverage_id)
            if observation is None:
                metrics[metric_id] = self._current_projection_unavailable(
                    metric_id, label, unit,
                    f"{GLOBAL60M_UR232_SAFE_REASON}: {coverage_id}; {global_reason}",
                )
                continue
            change, change_pct = _load_global60m_current_comparison(
                self.root, coverage_id, observation,
            )
            metric = DashboardMetricView(
                dataset_id="market_price_60m_current", series_id=metric_id,
                label=label, value=float(observation.value), unit=observation.unit,
                as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                    "Asia/Seoul"
                ).strftime("%m-%d %H:%M KST"),
                expected_as_of=None,
                source=f"Yahoo completed {observation.interval.value} current-only projection",
                freshness=f"CURRENT_COMPLETED_{observation.interval.value.upper()}",
                pit_status="PIT_BLOCKED", pit_label="표시 전용 · Backtest 사용 불가",
                automation_policy="EVERY_30_MIN_CURRENT_ONLY", automation_enabled=True,
                display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
                route=observation.route_id, change=change, change_pct=change_pct,
                source_timestamp=observation.provider_timestamp_utc,
                retrieved_at_utc=observation.retrieved_at_utc,
                delay_status="DELAYED_COMPLETED_BAR", completed_bar=True,
                timestamp_basis=observation.timestamp_basis.value,
            )
            metrics[metric_id] = self._gate_current_metric(
                metric, now_utc=now,
                allow_kr_market_closed_last_verified=(metric_id in {"KOSPI", "KOSDAQ"}),
            )

        mobile_home, mobile_reason = load_naver_mobile_home_current_observations(
            self.root
        )
        for coverage_id, metric_id, label in (
            ("KOSPI", "KOSPI", "KOSPI"),
            ("KOSDAQ", "KOSDAQ", "KOSDAQ"),
            ("USD_KRW", "USD_KRW_60M", "USD/KRW"),
        ):
            observation = mobile_home.get(coverage_id)
            existing = metrics.get(metric_id)
            if observation is None or (existing is not None and existing.displays_value):
                continue
            candidate = DashboardMetricView(
                dataset_id=observation.dataset_id, series_id=metric_id,
                label=label, value=float(observation.value), unit=observation.unit,
                as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                    "Asia/Seoul"
                ).strftime("%m-%d %H:%M KST"),
                expected_as_of=None,
                source="NAVER_FINANCE_WEB retained current snapshot",
                freshness="CURRENT_PROVISIONAL", pit_status="PIT_BLOCKED",
                pit_label="표시 전용 · Backtest 사용 불가",
                automation_policy="RETAINED_RECOVERY_ONLY", automation_enabled=False,
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=mobile_reason, route=observation.route_id,
                source_timestamp=observation.provider_timestamp_utc,
                retrieved_at_utc=observation.retrieved_at_utc,
            )
            metrics[metric_id] = self._gate_current_metric(
                candidate, now_utc=now,
                allow_kr_market_closed_last_verified=(metric_id in {"KOSPI", "KOSDAQ"}),
            )

        # Toss owns a Korean cash-index card only when its exact retained route
        # and source-time gate pass. A rejected Toss row never displaces a
        # separately valid completed Yahoo bar.
        for metric_id in ("KOSPI", "KOSDAQ"):
            observation, toss_reason = load_toss_domestic_ur246_current_observation(
                self.root, symbol=metric_id,
            )
            if observation is None:
                continue
            candidate = DashboardMetricView(
                dataset_id=observation.identity.dataset_id, series_id=metric_id,
                label=metric_id, value=float(observation.value), unit=observation.unit,
                as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                    "Asia/Seoul"
                ).strftime("%m-%d %H:%M KST"),
                expected_as_of=None,
                source="tossinvest_open_api retained current snapshot",
                freshness="CURRENT_PROVISIONAL", pit_status="PIT_BLOCKED",
                pit_label="표시 전용 · Backtest 사용 불가",
                automation_policy="EVERY_30_MIN_CURRENT_ONLY", automation_enabled=True,
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=toss_reason, route=observation.route_id,
                source_timestamp=observation.provider_timestamp_utc,
                retrieved_at_utc=observation.retrieved_at_utc,
                timestamp_basis=observation.timestamp_basis.value,
            )
            candidate = self._gate_current_metric(candidate, now_utc=now)
            if candidate.displays_value:
                metrics[metric_id] = candidate

        # Do not call _treasury_quote_metric here: its fallback reads the full
        # normalized 15-minute Parquet dataset.
        for key, series_id, label in (
            ("UST5_QUOTE_15M", "^FVX", "미국 5Y quote"),
            ("UST10_QUOTE_15M", "^TNX", "미국 10Y quote"),
            ("UST30_QUOTE_15M", "^TYX", "미국 30Y quote"),
        ):
            observation = _load_yahoo_native15m_current(self.root, series_id)
            if observation is None:
                metrics[key] = self._current_projection_unavailable(
                    key, label, "quote index points",
                    f"YAHOO_NATIVE15M_CURRENT_UNAVAILABLE: {series_id}",
                )
                continue
            candidate = DashboardMetricView(
                dataset_id="market_price_15m_current", series_id=series_id,
                label=label, value=float(observation.value), unit="quote index points",
                as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                    "Asia/Seoul"
                ).strftime("%Y-%m-%d %H:%M KST"),
                expected_as_of=None,
                source=(
                    f"Yahoo/Cboe {series_id} completed provider-native 15m "
                    "indicative quote index; not an official Treasury yield"
                ),
                freshness="CURRENT_COMPLETED_15M", pit_status="PIT_BLOCKED",
                pit_label="표시 전용 · Backtest 사용 불가",
                automation_policy="EVERY_30_MIN_CURRENT_ONLY", automation_enabled=True,
                display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
                route=observation.route_id,
                source_timestamp=observation.provider_timestamp_utc,
                retrieved_at_utc=observation.retrieved_at_utc,
                delay_status="DELAYED_COMPLETED_BAR", completed_bar=True,
                timestamp_basis=observation.timestamp_basis.value,
            )
            metrics[key] = self._gate_current_metric(candidate, now_utc=now)

        return DashboardCurrentStageView(
            as_of_utc=now.isoformat(), metrics=metrics,
            treasury_rate_views=self.treasury_rate_views(metrics),
            degraded_reasons=tuple(
                key for key, metric in metrics.items() if not metric.displays_value
            ),
        )

    def snapshot(
        self, session: str = "U", *, now_utc: object | None = None,
        max_seconds: float = 10.0,
    ) -> dict:
        from stock_data.gui.health_service import DailyHealthArtifactService

        if max_seconds <= 0:
            raise ValueError("snapshot max_seconds must be positive")
        now = now_utc if now_utc is not None else pd.Timestamp.now(tz="UTC")
        started = monotonic()
        deadline = started + float(max_seconds)
        degraded: list[str] = []

        def permitted(section: str) -> bool:
            if monotonic() < deadline:
                return True
            degraded.append(f"{section}: SNAPSHOT_TIME_BUDGET_EXHAUSTED")
            return False

        current_stage = self.current_card_stage(now_utc=now)
        health_view = (
            DailyHealthArtifactService(self.root).load()
            if permitted("data_health") else None
        )
        if health_view is not None and permitted("dashboard_metrics"):
            metrics = self.dashboard_metrics(health_view, now_utc=now)
            for key, staged in current_stage.metrics.items():
                existing = metrics.get(key)
                if staged.displays_value or (
                    existing is not None
                    and existing.dataset_id in {
                        "market_price_60m_current", "market_price_15m_current",
                        "KR_INDEX_CURRENT",
                    }
                ):
                    metrics[key] = staged
        else:
            metrics = dict(current_stage.metrics)
        series = self.dashboard_series(metrics) if permitted("dashboard_series") else {}
        card_sparklines = (
            self.market_card_sparklines(metrics, now_utc=now)
            if permitted("market_card_sparklines") else {}
        )
        if permitted("current_session_card_sparklines"):
            card_sparklines.update(self.current_session_card_sparklines(metrics))
        average_comparisons = (
            self.daily_average_comparisons(metrics, series)
            if permitted("daily_average_comparisons") else {}
        )
        market_valuations = (
            self.market_valuation_views(now_utc=now)
            if permitted("market_valuation_views") else {}
        )
        treasury_rates = self.treasury_rate_views(metrics)
        vix_sources = self.vix_source_views(metrics)
        cards = (
            self.market_cards(health_view)
            if health_view is not None and permitted("market_cards") else []
        )
        result = {
                "overview": self.index.overview() if permitted("overview") else {},
                "market_cards": cards,
                "market_session_as_of_utc": pd.Timestamp(now).isoformat(),
                "dashboard_metrics": metrics,
                "dashboard_series": series,
                "market_card_sparklines": card_sparklines,
                "daily_average_comparisons": average_comparisons,
                "market_valuation_views": market_valuations,
                "market_flow_views": ({
                    key: self._gate_market_flow(view, now_utc=now)
                    for key, view in self.market_investor_flow_views(health_view).items()
                } if health_view is not None and permitted("market_flow_views") else {}),
                "market_funding_view": (
                    self.market_funding_view(health_view)
                    if health_view is not None and permitted("market_funding_view") else None
                ),
                "treasury_rate_views": treasury_rates,
                "vix_source_views": vix_sources,
                "toss_short_watchlist": (
                    self._gate_toss_short_watchlist(
                        self.toss_short_watchlist_view(), now_utc=now,
                    ) if permitted("toss_short_watchlist") else None
                ),
                "current_observation_coverage": (
                    self.current_observation_coverage(now_utc=now)
                    if permitted("current_observation_coverage") else {}
                ),
                "health_rows": ({
                    row.dataset: {
                        "latest": row.latest, "expected": row.expected,
                        "freshness": row.freshness, "source": row.source,
                        "pit": row.pit, "blocker": row.blocker,
                    }
                    for row in health_view.rows
                } if health_view is not None else {}),
                "data_health": (
                    self.data_health(cards, health=health_view)
                    if health_view is not None else {
                        "overall": "READ_FAILURE",
                        "reason": "SNAPSHOT_TIME_BUDGET_EXHAUSTED",
                    }
                ),
        }
        elapsed = monotonic() - started
        if elapsed > max_seconds:
            degraded.append("snapshot: SNAPSHOT_TIME_BUDGET_EXCEEDED_AFTER_SECTION")
        result["snapshot_state"] = "DEGRADED_BOUNDED" if degraded else "COMPLETE"
        result["snapshot_degraded_reasons"] = tuple(dict.fromkeys(degraded))
        result["snapshot_elapsed_seconds"] = elapsed
        return result

    def current_observation_coverage(
        self, *, now_utc: object | None = None,
    ) -> dict[str, CurrentObservationCoverageView]:
        """Return the accepted current-display matrix without invoking providers.

        FDR/Yahoo rows are retained daily observations from UR-115/116.  KB,
        Toss, and LS rows describe the accepted adapter boundary but remain
        numeric-free until an independently authorized, timestamp-valid local
        projection exists.  This is intentionally separate from finalized
        history and from the dashboard metric selection.
        """
        from stock_data.gui.current_display import (
            load_current_display,
            load_dashboard_current,
        )

        dashboard = load_dashboard_current(self.root)
        equity = load_current_display(self.root)
        naver_current, naver_reason = load_naver_web_000660_current_observation(self.root)
        ur199_000660, ur199_000660_reason = load_naver_mobile_basic_ur199_current_observation(
            self.root, symbol="000660"
        )
        toss_000660_nxt_close, toss_000660_nxt_reason = load_toss_000660_nxt_close_ur240_observation(
            self.root
        )
        toss_005930_nxt_close, toss_005930_nxt_reason = load_toss_005930_nxt_close_ur241_observation(
            self.root
        )
        soxx_current, soxx_reason = load_nasdaq_soxx_info_current_observation(self.root)
        mobile_home, mobile_home_provenance = load_naver_mobile_home_current_observations(
            self.root
        )
        global60m, global60m_provenance = load_global60m_ur232_current_observations(
            self.root
        )
        toss_domestic = {
            symbol: load_toss_domestic_ur246_current_observation(self.root, symbol=symbol)
            for symbol in ("KOSPI", "KOSDAQ", "000660", "005930")
        }
        result: dict[str, CurrentObservationCoverageView] = {}
        for coverage_id, label in (
            ("KOSPI", "KOSPI current index"),
            ("KOSDAQ", "KOSDAQ current index"),
            ("USD_KRW", "USD/KRW current FX"),
        ):
            toss_observation, toss_reason = toss_domestic.get(
                coverage_id, (None, "TOSS_DOMESTIC_UR246_NOT_APPLICABLE")
            )
            observation = toss_observation or mobile_home.get(coverage_id)
            if observation is None:
                result[coverage_id] = CurrentObservationCoverageView(
                    coverage_id=coverage_id, label=label, value=None, unit=None,
                    provider="NAVER_FINANCE_WEB", route=(
                        f"naver-mobile-home-current:XKRX:{coverage_id}"
                        if coverage_id != "USD_KRW" else "naver-mobile-home-current:KRW:USD_KRW"
                    ),
                    interval="snapshot", as_of=None, retrieved_at_utc=None,
                    freshness="UNAVAILABLE", finality="PROVISIONAL",
                    display_state=DashboardDisplayState.UNAVAILABLE,
                    unavailable_reason=f"{toss_reason} {mobile_home_provenance}",
                    display_only=True, pit_safe=False,
                )
                continue
            if isinstance(observation, CurrentObservation):
                result[coverage_id] = CurrentObservationCoverageView(
                    coverage_id=coverage_id, label=label, value=observation.value,
                    unit=observation.unit, provider=observation.provider,
                    route=observation.route_id, interval=observation.interval.value,
                    as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                        "Asia/Seoul"
                    ).strftime("%Y-%m-%d %H:%M KST"),
                    retrieved_at_utc=observation.retrieved_at_utc, freshness="CURRENT",
                    finality=observation.finality.value,
                    display_state=DashboardDisplayState.VALUE,
                    unavailable_reason=toss_reason,
                    provider_timestamp_utc=observation.provider_timestamp_utc,
                    source_route=observation.source_route,
                    display_only=observation.display_only, pit_safe=observation.pit_safe,
                    timestamp_basis=observation.timestamp_basis.value,
                )
            else:
                result[coverage_id] = CurrentObservationCoverageView(
                    coverage_id=coverage_id, label=label, value=observation.value,
                    unit=observation.unit, provider="NAVER_FINANCE_WEB",
                    route=observation.route_id, interval="snapshot",
                    as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                        "Asia/Seoul"
                    ).strftime("%Y-%m-%d %H:%M KST"),
                    retrieved_at_utc=observation.retrieved_at_utc, freshness="CURRENT",
                    finality="PROVISIONAL", display_state=DashboardDisplayState.VALUE,
                    unavailable_reason=mobile_home_provenance,
                    provider_timestamp_utc=observation.provider_timestamp_utc,
                    source_route=observation.source_route, display_only=True, pit_safe=False,
                )
        for coverage_id, (_market, _symbol, _unit, label) in GLOBAL60M_UR232_CURRENT_SPECS.items():
            observation = global60m.get(coverage_id)
            if observation is None:
                result[coverage_id] = CurrentObservationCoverageView(
                    coverage_id=coverage_id, label=label, value=None, unit=_unit,
                    provider="YAHOO retained Landing", route=f"yahoo-global60m-ur232:{_market}:{_symbol}",
                    interval="60m", as_of=None, retrieved_at_utc=None, freshness="UNAVAILABLE",
                    finality="AS_RETRIEVED", display_state=DashboardDisplayState.UNAVAILABLE,
                    unavailable_reason=global60m_provenance, display_only=True, pit_safe=False,
                )
                continue
            scheduled_current = observation.route_id.startswith((
                "yahoo-market-current:", "yahoo-global60m-current:",
            ))
            result[coverage_id] = CurrentObservationCoverageView(
                coverage_id=coverage_id, label=label, value=observation.value, unit=observation.unit,
                provider=("YAHOO current completed bar" if scheduled_current else "YAHOO retained Landing"),
                route=observation.route_id, interval=observation.interval.value,
                as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M KST"),
                retrieved_at_utc=observation.retrieved_at_utc,
                freshness=(
                    f"CURRENT_COMPLETED_{observation.interval.value.upper()}"
                    if scheduled_current else "RETAINED_LANDING_API_ZERO_RECOVERY"
                ),
                finality=observation.finality.value, display_state=DashboardDisplayState.VALUE,
                unavailable_reason=global60m_provenance, provider_timestamp_utc=observation.provider_timestamp_utc,
                source_route=observation.source_route, display_only=True, pit_safe=False,
                timestamp_basis=observation.timestamp_basis.value,
            )
        for identity, label in (
            ("SP500", "S&P 500"), ("NASDAQ", "NASDAQ"), ("SOXX", "SOXX"),
            ("NQ_FUTURES", "NQ continuous futures"), ("GOLD", "Gold futures"),
            ("WTI", "WTI futures"),
        ):
            observation = dashboard.get(identity)
            if observation is None:
                result[identity] = CurrentObservationCoverageView(
                    coverage_id=identity, label=label, value=None, unit=None,
                    provider="FinanceDataReader / Yahoo daily",
                    route="FDR_DAILY_CURRENT_DISPLAY_FALLBACK", interval="1d",
                    as_of=None, retrieved_at_utc=None, freshness="UNAVAILABLE",
                    finality="AS_RETRIEVED_DAILY", display_state=DashboardDisplayState.UNAVAILABLE,
                    unavailable_reason="No accepted retained FDR daily current-display observation.",
                )
                continue
            result[identity] = CurrentObservationCoverageView(
                coverage_id=identity, label=label, value=observation.value,
                unit=observation.unit, provider=observation.provider,
                route=observation.route, interval=observation.interval,
                as_of=observation.source_date, retrieved_at_utc=observation.retrieved_at_utc,
                freshness="RETAINED_AS_RETRIEVED", finality=observation.finality,
                display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
            )

        if soxx_current is None:
            result["SOXX"] = CurrentObservationCoverageView(
                coverage_id="SOXX", label="SOXX current ETF quote", value=None,
                unit="USD per share", provider="NASDAQ_OFFICIAL",
                route=NASDAQ_SOXX_INFO_CURRENT_ROUTE.route_id, interval="snapshot",
                as_of=None, retrieved_at_utc=None, freshness="UNAVAILABLE",
                finality="PROVISIONAL", display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason=soxx_reason, display_only=True, pit_safe=False,
            )
        else:
            result["SOXX"] = CurrentObservationCoverageView(
                coverage_id="SOXX", label="SOXX current ETF quote", value=soxx_current.value,
                unit=soxx_current.unit, provider=soxx_current.provider,
                route=soxx_current.route_id, interval=soxx_current.interval.value,
                as_of=pd.Timestamp(soxx_current.provider_timestamp_utc).tz_convert(
                    "Asia/Seoul"
                ).strftime("%Y-%m-%d %H:%M KST"),
                retrieved_at_utc=soxx_current.retrieved_at_utc, freshness="CURRENT_PROVISIONAL",
                finality=soxx_current.finality.value, display_state=DashboardDisplayState.VALUE,
                unavailable_reason=soxx_reason,
                provider_timestamp_utc=soxx_current.provider_timestamp_utc,
                source_route=soxx_current.source_route,
                display_only=soxx_current.display_only, pit_safe=soxx_current.pit_safe,
                timestamp_basis=soxx_current.timestamp_basis.value,
            )

        toss_000660, toss_000660_reason = toss_domestic["000660"]
        current_000660 = toss_000660 or ur199_000660 or naver_current or equity
        if current_000660 is None:
            result["EQUITY_000660"] = CurrentObservationCoverageView(
                coverage_id="EQUITY_000660", label="000660 equity",
                value=None, unit=None, provider="FinanceDataReader / Naver daily",
                route="FDR_NAVER_CURRENT_DISPLAY", interval="1d", as_of=None,
                retrieved_at_utc=None, freshness="UNAVAILABLE",
                finality="AS_RETRIEVED_DAILY", display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason=f"{toss_000660_reason} {ur199_000660_reason}",
            )
        else:
            if isinstance(current_000660, CurrentObservation):
                result["EQUITY_000660"] = CurrentObservationCoverageView(
                    coverage_id="EQUITY_000660", label="000660 equity",
                    value=current_000660.value, unit=current_000660.unit,
                    provider=current_000660.provider,
                    route=current_000660.route_id, interval=current_000660.interval.value,
                    as_of=_ls_t8412_current_source_date(current_000660),
                    retrieved_at_utc=current_000660.retrieved_at_utc,
                    freshness="RETAINED_AS_RETRIEVED", finality=current_000660.finality.value,
                    display_state=DashboardDisplayState.VALUE,
                    unavailable_reason=(
                        TOSS_DOMESTIC_UR246_PROVENANCE if current_000660 is toss_000660 else
                        NAVER_MOBILE_BASIC_UR199_PROVENANCE_WARNING
                        if current_000660 is ur199_000660 else naver_reason
                    ),
                    provider_timestamp_utc=current_000660.provider_timestamp_utc,
                    source_route=current_000660.source_route,
                    display_only=current_000660.display_only,
                    pit_safe=current_000660.pit_safe,
                    timestamp_basis=current_000660.timestamp_basis.value,
                )
            else:
                result["EQUITY_000660"] = CurrentObservationCoverageView(
                    coverage_id="EQUITY_000660", label=current_000660.symbol, value=current_000660.value,
                    unit=current_000660.unit, provider=current_000660.provider,
                    route="FDR_NAVER_CURRENT_DISPLAY", interval=current_000660.interval,
                    as_of=current_000660.source_date, retrieved_at_utc=current_000660.retrieved_at_utc,
                    freshness="RETAINED_AS_RETRIEVED", finality=current_000660.finality,
                    display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
                )

        toss_005930, toss_005930_reason = toss_domestic["005930"]
        if toss_005930 is None:
            result["EQUITY_005930"] = CurrentObservationCoverageView(
                coverage_id="EQUITY_005930", label="005930 equity",
                value=None, unit="KRW per share",
                provider="tossinvest_open_api",
                route=_toss_domestic_ur246_route("005930").route_id,
                interval="snapshot", as_of=None, retrieved_at_utc=None,
                freshness="UNAVAILABLE", finality="PROVISIONAL",
                display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason=toss_005930_reason,
                display_only=True, pit_safe=False,
                timestamp_basis="PROVIDER_TIMESTAMP",
            )
        else:
            result["EQUITY_005930"] = CurrentObservationCoverageView(
                coverage_id="EQUITY_005930", label="005930 equity",
                value=toss_005930.value, unit=toss_005930.unit,
                provider=toss_005930.provider, route=toss_005930.route_id,
                interval=toss_005930.interval.value,
                as_of=_ls_t8412_current_source_date(toss_005930),
                retrieved_at_utc=toss_005930.retrieved_at_utc,
                freshness="RETAINED_AS_RETRIEVED",
                finality=toss_005930.finality.value,
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=TOSS_DOMESTIC_UR246_PROVENANCE,
                provider_timestamp_utc=toss_005930.provider_timestamp_utc,
                source_route=toss_005930.source_route,
                display_only=toss_005930.display_only,
                pit_safe=toss_005930.pit_safe,
                timestamp_basis=toss_005930.timestamp_basis.value,
            )

        for coverage_id, label, observation, reason in (
            ("EQUITY_000660_NXT_CLOSE", "000660 inferred NXT close", toss_000660_nxt_close, toss_000660_nxt_reason),
            ("EQUITY_005930_NXT_CLOSE", "005930 inferred NXT close", toss_005930_nxt_close, toss_005930_nxt_reason),
        ):
            if observation is None:
                result[coverage_id] = CurrentObservationCoverageView(
                    coverage_id=coverage_id, label=label, value=None, unit="KRW per share",
                    provider="tossinvest_open_api", route="TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
                    interval="snapshot", as_of=None, retrieved_at_utc=None,
                    freshness="UNAVAILABLE", finality="POST_CLOSE_OR_PROVISIONAL",
                    display_state=DashboardDisplayState.UNAVAILABLE, unavailable_reason=reason,
                    display_only=True, pit_safe=False, nxt_session_gate=True, nxt_venue_inferred=True,
                )
                continue
            result[coverage_id] = CurrentObservationCoverageView(
                coverage_id=coverage_id, label=label, value=observation.value,
                unit=observation.unit, provider=observation.provider, route=observation.route_id,
                interval=observation.interval.value,
                as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M:%S KST"),
                retrieved_at_utc=observation.retrieved_at_utc,
                freshness="NXT_SESSION_CLOSE_CANDIDATE", finality=observation.finality.value,
                display_state=DashboardDisplayState.VALUE, unavailable_reason=reason,
                provider_timestamp_utc=observation.provider_timestamp_utc,
                source_route=observation.source_route, display_only=True, pit_safe=False,
                nxt_session_gate=True, nxt_session_start_kst=None, nxt_venue_inferred=True,
                timestamp_basis=observation.timestamp_basis.value,
            )

        ls_observation, ls_reason = load_ls_t8412_current_observation(self.root)
        result.update({
            "KB_IVSA0070": CurrentObservationCoverageView(
                coverage_id="KB_IVSA0070", label="KB market snapshot", value=None,
                unit=None, provider="KB Securities IVSA0070",
                route="KBSEC_IVSA0070_CURRENT_SNAPSHOT", interval="snapshot",
                as_of=None, retrieved_at_utc=None, freshness="UNAVAILABLE",
                finality="PROVISIONAL", display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason=(
                    "No accepted current projection: the 2026-08-21 KB capture window "
                    "was closed and no timestamp-valid slice was retained."
                ),
            ),
            "TOSS_KOSPI": CurrentObservationCoverageView(
                coverage_id="TOSS_KOSPI", label="Toss KOSPI snapshot", value=None,
                unit="index points", provider="Toss Invest market indicators",
                route="TOSS_MARKET_PRICE_SNAPSHOT", interval="snapshot",
                as_of=None, retrieved_at_utc=None, freshness="UNAVAILABLE",
                finality="PROVISIONAL", display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason=toss_domestic["KOSPI"][1],
            ),
            "TOSS_KOSDAQ": CurrentObservationCoverageView(
                coverage_id="TOSS_KOSDAQ", label="Toss KOSDAQ snapshot", value=None,
                unit="index points", provider="Toss Invest market indicators",
                route="TOSS_MARKET_PRICE_SNAPSHOT", interval="snapshot",
                as_of=None, retrieved_at_utc=None, freshness="UNAVAILABLE",
                finality="PROVISIONAL", display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason=toss_domestic["KOSDAQ"][1],
            ),
            "LS_T8412": CurrentObservationCoverageView(
                coverage_id="LS_T8412", label="LS 005930 current observation",
                value=ls_observation.value if ls_observation is not None else None,
                unit=ls_observation.unit if ls_observation is not None else None,
                provider=("LS Securities t8412 / LS_OPENAPI" if ls_observation is not None else "LS Securities t8412"),
                route=LS_T8412_CURRENT_ROUTE.route_id, interval="15m",
                as_of=(_ls_t8412_current_source_date(ls_observation) if ls_observation is not None else None),
                retrieved_at_utc=(ls_observation.retrieved_at_utc if ls_observation is not None else None),
                freshness=("RETAINED_AS_RETRIEVED" if ls_observation is not None else "UNAVAILABLE"),
                finality=(ls_observation.finality.value if ls_observation is not None else "AS_RETRIEVED"),
                display_state=(DashboardDisplayState.VALUE if ls_observation is not None else DashboardDisplayState.UNAVAILABLE),
                unavailable_reason=(None if ls_observation is not None else ls_reason),
                provider_timestamp_utc=(ls_observation.provider_timestamp_utc if ls_observation is not None else None),
                source_route=(ls_observation.source_route if ls_observation is not None else None),
                display_only=(ls_observation.display_only if ls_observation is not None else True),
                pit_safe=(ls_observation.pit_safe if ls_observation is not None else False),
                timestamp_basis=(
                    ls_observation.timestamp_basis.value
                    if ls_observation is not None else "PROVIDER_TIMESTAMP"
                ),
            ),
        })
        for symbol in ("KOSPI", "KOSDAQ"):
            observation, reason = toss_domestic[symbol]
            if observation is None:
                continue
            result[f"TOSS_{symbol}"] = CurrentObservationCoverageView(
                coverage_id=f"TOSS_{symbol}", label=f"Toss {symbol} snapshot",
                value=observation.value, unit=observation.unit,
                provider=observation.provider, route=observation.route_id,
                interval=observation.interval.value,
                as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                    "Asia/Seoul"
                ).strftime("%Y-%m-%d %H:%M KST"),
                retrieved_at_utc=observation.retrieved_at_utc,
                freshness="CURRENT", finality=observation.finality.value,
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=reason,
                provider_timestamp_utc=observation.provider_timestamp_utc,
                source_route=observation.source_route,
                display_only=observation.display_only, pit_safe=observation.pit_safe,
                timestamp_basis=observation.timestamp_basis.value,
            )
        now = now_utc if now_utc is not None else pd.Timestamp.now(tz="UTC")
        return {
            key: self._gate_current_coverage(view, now_utc=now)
            for key, view in result.items()
        }

    def toss_short_watchlist_view(
        self, *, expected_date: str | None = None,
    ) -> TossShortWatchlistView:
        """Read only the accepted Toss two-symbol checkpoint and contract root."""
        dataset_id = TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name

        def unavailable(
            state: DashboardDisplayState, reason: str, *,
            as_of: str | None = None, freshness: str = "UNKNOWN",
        ) -> TossShortWatchlistView:
            return TossShortWatchlistView(
                dataset_id=dataset_id, label="Toss 종목별 EOD",
                members=(), as_of=as_of, expected_as_of=expected_date,
                source="tossinvest_open_api", source_scope=TOSS_SHORT_SOURCE_SCOPE,
                freshness=freshness, pit_label="설명용 · 예측 사용 불가",
                automation_enabled=False, display_state=state,
                unavailable_reason=reason, route="NORMALIZED_TOSS_PROVIDER_EOD",
            )

        state_path = self.root / "data/state/toss_equity_short_watchlist_daily.json"
        journal_path = self.root / "data/state/toss_equity_short_watchlist_daily_journal.json"
        live_root = self.root / "data/normalized" / dataset_id
        try:
            if not state_path.exists() or not live_root.exists():
                return unavailable(
                    DashboardDisplayState.UNAVAILABLE,
                    "검증된 Toss 종목별 EOD 보존 데이터와 완료 checkpoint가 없습니다.",
                )
            checkpoint = json.loads(state_path.read_text(encoding="utf-8"))
            journal = (
                json.loads(journal_path.read_text(encoding="utf-8"))
                if journal_path.exists() else None
            )
            if not isinstance(checkpoint, dict):
                raise ValueError("checkpoint is not an object")
            completed_date = str(checkpoint.get("completed_date") or "")
            identity_ok = (
                checkpoint.get("dataset") == dataset_id
                and checkpoint.get("watchlist_version") == TOSS_SHORT_WATCHLIST_VERSION
                and checkpoint.get("completed_symbols")
                == sorted(symbol for symbol, _name, _market in TOSS_SHORT_WATCHLIST)
            )
            if not identity_ok:
                return unavailable(
                    DashboardDisplayState.PROHIBITED,
                    "Toss 종목별 EOD checkpoint의 dataset·버전·종목 범위가 계약과 다릅니다.",
                    as_of=completed_date or None,
                )
            if checkpoint.get("status") != "SUCCEEDED" or not (
                isinstance(journal, dict)
                and journal.get("status") in {"SUCCEEDED", "SUCCEEDED_RECOVERED"}
                and journal.get("target_date") == completed_date
            ):
                return unavailable(
                    DashboardDisplayState.PROHIBITED,
                    "Toss 종목별 EOD transaction이 완전히 성공한 상태가 아닙니다.",
                    as_of=completed_date or None,
                )
            if (
                checkpoint.get("token_calls") != 1
                or checkpoint.get("market_calls") != len(TOSS_SHORT_WATCHLIST)
                or len(checkpoint.get("landing_files") or ()) != len(TOSS_SHORT_WATCHLIST)
            ):
                return unavailable(
                    DashboardDisplayState.PROHIBITED,
                    "Toss 종목별 EOD 호출 예산 또는 Landing 범위가 승인된 transaction과 다릅니다.",
                    as_of=completed_date or None,
                )
            retained = read_dataset(
                live_root, TOSS_EQUITY_SHORT_WATCHLIST_DAILY,
                validate_watchlist_dataset,
            )
            exact = retained.loc[retained["date"].astype(str).eq(completed_date)].copy()
            validate_staged_watchlist(exact, target_date=completed_date)
            if len(retained) != int(checkpoint.get("retained_rows", -1)):
                return unavailable(
                    DashboardDisplayState.PROHIBITED,
                    "Toss 종목별 EOD checkpoint와 보존 행 수가 일치하지 않습니다.",
                    as_of=completed_date,
                )
            if expected_date is not None and pd.Timestamp(completed_date) < pd.Timestamp(expected_date):
                return unavailable(
                    DashboardDisplayState.REFRESH_REQUIRED,
                    f"Toss 종목별 EOD 기준일 {completed_date}이 명시된 기대일 {expected_date}보다 이전입니다.",
                    as_of=completed_date, freshness="STALE",
                )
            rows = {str(row.symbol): row for row in exact.itertuples(index=False)}
            members = tuple(
                TossShortSymbolEODView(
                    symbol=symbol, name=name, market=market,
                    market_date=completed_date,
                    short_selling_volume=int(rows[symbol].short_selling_volume),
                    short_selling_amount=int(rows[symbol].short_selling_amount),
                    short_selling_volume_rate=(
                        None if pd.isna(rows[symbol].short_selling_volume_rate)
                        else float(rows[symbol].short_selling_volume_rate)
                    ),
                    short_selling_amount_rate=(
                        None if pd.isna(rows[symbol].short_selling_amount_rate)
                        else float(rows[symbol].short_selling_amount_rate)
                    ),
                )
                for symbol, name, market in TOSS_SHORT_WATCHLIST
            )
            return TossShortWatchlistView(
                dataset_id=dataset_id, label="Toss 종목별 EOD",
                members=members, as_of=completed_date, expected_as_of=expected_date,
                source="tossinvest_open_api", source_scope=TOSS_SHORT_SOURCE_SCOPE,
                freshness="CURRENT", pit_label="설명용 · 예측 사용 불가",
                automation_enabled=False, display_state=DashboardDisplayState.VALUE,
                unavailable_reason=None, route="NORMALIZED_TOSS_PROVIDER_EOD",
            )
        except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError,
                PermissionError, TypeError, ValueError):
            return unavailable(
                DashboardDisplayState.UNAVAILABLE,
                "Toss 종목별 EOD 로컬 계약 또는 transaction 검증에 실패했습니다.",
            )

    @staticmethod
    def _completed_daily_series_cutoff(
        key: str, metric: DashboardMetricView,
    ) -> tuple[object | None, bool]:
        """Return a daily-history cutoff without reusing a display timestamp.

        Exact Toss domestic-index snapshots expose an intraday KST label in
        ``as_of``.  That label is presentation metadata, not a valid daily
        partition bound.  Use only its already-validated, aware source instant
        to derive the KST calendar date and keep every other route unchanged.
        """
        if metric.route.startswith("yahoo-market-current:"):
            return None, False
        if key not in {"KOSPI", "KOSDAQ"}:
            return metric.as_of, False
        exact_route = _toss_domestic_ur246_route(key).route_id
        if metric.route != exact_route:
            return metric.as_of, False
        if (
            metric.series_id != key
            or metric.dataset_id != "TOSS_MARKET_PRICE_SNAPSHOT"
            or metric.source_timestamp is None
            or metric.timestamp_basis != "RETRIEVAL_TIMESTAMP"
            or metric.retrieved_at_utc is None
        ):
            raise ValueError("Toss domestic index metric identity is incomplete")
        source = pd.Timestamp(metric.source_timestamp)
        retrieved = pd.Timestamp(metric.retrieved_at_utc)
        if (
            source.tzinfo is None
            or source.utcoffset() is None
            or retrieved.tzinfo is None
            or retrieved.utcoffset() is None
            or source.tz_convert("UTC") != retrieved.tz_convert("UTC")
        ):
            raise ValueError("Toss domestic index retrieval timestamp differs")
        source_kst = retrieved.tz_convert("Asia/Seoul")
        accepted_labels = {
            source_kst.strftime("%m-%d %H:%M KST"),
            source_kst.strftime("%Y-%m-%d %H:%M KST"),
        }
        if metric.as_of not in accepted_labels:
            raise ValueError("Toss domestic index display timestamp differs")
        return source_kst.date().isoformat(), True

    @staticmethod
    def _validated_completed_daily_index_frame(
        frame: pd.DataFrame, *, key: str, cutoff: object,
    ) -> pd.DataFrame:
        required = {"date", "symbol", "close"}
        if frame.empty or not required.issubset(frame.columns):
            raise ValueError("completed daily index frame is incomplete")
        result = frame.copy()
        if set(result["symbol"].astype(str)) != {key}:
            raise ValueError("completed daily index identity differs")
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        result["close"] = pd.to_numeric(result["close"], errors="coerce")
        cutoff_date = pd.Timestamp(cutoff)
        if (
            result[["date", "close"]].isna().any().any()
            or not np.isfinite(result["close"]).all()
            or result["date"].duplicated().any()
            or not result["date"].is_monotonic_increasing
            or result["date"].gt(cutoff_date).any()
        ):
            raise ValueError("completed daily index frame failed validation")
        return result

    def dashboard_series(
        self, metrics: dict[str, DashboardMetricView], days: int = 20,
    ) -> dict[str, DashboardSeriesView]:
        """Return compact local series only for metrics already allowed to display."""
        result: dict[str, DashboardSeriesView] = {}
        for key in (
            "KOSPI", "KOSDAQ", "SOXX", "NQ_FUTURES", "NASDAQ", "SP500",
            "GOLD", "WTI",
        ):
            metric = metrics.get(key)
            if metric is None:
                continue
            try:
                series_as_of, validate_toss_daily = self._completed_daily_series_cutoff(
                    key, metric,
                )
                if key == "NQ_FUTURES":
                    frame = self.index.asset_series(key, "MAX", as_of=series_as_of)[
                        ["date", "open", "high", "low", "close"]
                    ].copy()
                    frame["value"] = frame["close"]
                else:
                    raw = self.index.asset_series(key, "20D", as_of=series_as_of)
                    if validate_toss_daily:
                        raw = self._validated_completed_daily_index_frame(
                            raw, key=key, cutoff=series_as_of,
                        )
                    frame = raw[["date", "close"]].rename(
                        columns={"close": "value"}
                    )
            except (KeyError, OSError, PermissionError, TypeError, ValueError):
                frame = pd.DataFrame(columns=["date", "value"])
            if not frame.empty:
                retained_rows = 252 if key == "NQ_FUTURES" else days
                result[key] = DashboardSeriesView(
                    metric, frame.tail(retained_rows).reset_index(drop=True)
                )

        definitions = {
            "VIX": ("normalized/fred_vix_daily", "date", "vixcls"),
            "VKOSPI": ("normalized/kr_vkospi_daily", "market_date", "close"),
            "USD_KRW": ("normalized/fred_usd_fx_daily", "date", "dexkous"),
            "USD_JPY": ("normalized/fred_usd_fx_daily", "date", FRED_DEXJPUS_IDENTITY.column),
            "UST2": ("normalized/fred_treasury_yield_daily", "date", "dgs2"),
            "UST10": ("normalized/fred_treasury_yield_daily", "date", "dgs10"),
            "UST30": ("normalized/fred_treasury_yield_daily", "date", "dgs30"),
            "UST10_2_SPREAD": ("derived/us_treasury_spread_daily", "date", "spread_10y_2y"),
        }
        for key, (dataset, date_column, value_column) in definitions.items():
            metric = metrics.get(key)
            if metric is None:
                continue
            requested_rows = 250 if key in {"VIX", "VKOSPI"} else days
            source_rows = requested_rows + 64 if key in {"VIX", "VKOSPI"} else requested_rows
            try:
                frame = self.query.tail(
                    dataset, rows=source_rows, columns=[date_column, value_column],
                ).rename(columns={date_column: "date", value_column: "value"})
                frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
                frame = frame.dropna(subset=["date", "value"]).tail(requested_rows).reset_index(drop=True)
            except (KeyError, OSError, PermissionError, TypeError, ValueError):
                frame = pd.DataFrame(columns=["date", "value"])
            if not frame.empty:
                result[key] = DashboardSeriesView(metric, frame)
        for key in (
            "USD_KRW_60M", "UST2_FUTURES_60M", "UST10_FUTURES_60M", "UST30_FUTURES_60M",
        ):
            metric = metrics.get(key)
            if metric is None:
                continue
            try:
                frame = self._read_intraday_frame(key).tail(48)
                frame = frame[["bar_start", "close"]].rename(
                    columns={"bar_start": "date", "close": "value"}
                )
            except (FileNotFoundError, KeyError, OSError, PermissionError, TypeError, ValueError):
                frame = pd.DataFrame(columns=["date", "value"])
            if not frame.empty:
                result[key] = DashboardSeriesView(metric, frame.reset_index(drop=True))
        for key in ("UST5_QUOTE_15M", "UST10_QUOTE_15M", "UST30_QUOTE_15M"):
            metric = metrics.get(key)
            if metric is None:
                continue
            try:
                frame = self._read_treasury_quote_frame(metric.series_id)
                frame = frame[["bar_end", "close"]].rename(
                    columns={"bar_end": "date", "close": "value"}
                )
            except (FileNotFoundError, KeyError, OSError, PermissionError, TypeError, ValueError):
                frame = pd.DataFrame(columns=["date", "value"])
            if not frame.empty:
                result[key] = DashboardSeriesView(metric, frame.tail(23).reset_index(drop=True))
        metric = metrics.get("VIX_INTRADAY_15M")
        if metric is not None:
            try:
                frame = self._read_vix_intraday_frame()
                retained_date = pd.to_datetime(frame["market_date"], errors="coerce").max()
                frame = frame.loc[
                    pd.to_datetime(frame["market_date"], errors="coerce").eq(retained_date)
                ][["bar_end", "close"]].rename(
                    columns={"bar_end": "date", "close": "value"}
                )
            except (FileNotFoundError, KeyError, OSError, PermissionError, TypeError, ValueError):
                frame = pd.DataFrame(columns=["date", "value"])
            if not frame.empty:
                result["VIX_INTRADAY_15M"] = DashboardSeriesView(
                    metric, frame.tail(26).reset_index(drop=True)
                )
        return result

    @staticmethod
    def _unavailable_average_comparison(
        series_id: str,
        comparison_kind: str,
        reason: str,
        *,
        state: DashboardDisplayState = DashboardDisplayState.UNAVAILABLE,
        as_of: str | None = None,
    ) -> DashboardAverageComparisonView:
        return DashboardAverageComparisonView(
            series_id=series_id, comparison_kind=comparison_kind,
            interval="completed daily", as_of=as_of, latest_value=None,
            mean_5=None, mean_20=None, comparison_5=None, comparison_20=None,
            coverage_5=None, coverage_20=None, display_state=state,
            unavailable_reason=reason, reason_5=reason, reason_20=reason,
        )

    @classmethod
    def build_daily_average_comparison(
        cls,
        metric: DashboardMetricView | None,
        frame: pd.DataFrame | None,
        *,
        comparison_kind: str,
        require_metric_match: bool = True,
        comparison_cutoff: object | None = None,
    ) -> DashboardAverageComparisonView:
        """Compare a gated latest value with exact eligible daily observations.

        ``relative_percent`` is reserved for price-like observations. ``basis_points``
        is an absolute difference for yields and spreads expressed in percent or
        percentage points. The frame is never filled, resampled, or deduplicated.
        """
        if comparison_kind not in {"relative_percent", "basis_points"}:
            raise ValueError("unsupported daily-average comparison kind")
        series_id = metric.series_id if metric is not None else "UNKNOWN"
        if metric is None or not metric.displays_value:
            state = (
                metric.display_state if metric is not None
                else DashboardDisplayState.UNAVAILABLE
            )
            return cls._unavailable_average_comparison(
                series_id, comparison_kind,
                metric.unavailable_reason if metric is not None
                and metric.unavailable_reason else "현재 값이 표시 가능하지 않습니다.",
                state=state, as_of=metric.as_of if metric is not None else None,
            )
        if not metric.displays_value:
            return cls._unavailable_average_comparison(
                series_id, comparison_kind,
                "최신성 상태가 일일 평균 비교를 허용하지 않습니다.",
                state=DashboardDisplayState.REFRESH_REQUIRED,
                as_of=metric.as_of,
            )
        if frame is None or frame.empty or not {"date", "value"}.issubset(frame.columns):
            return cls._unavailable_average_comparison(
                series_id, comparison_kind,
                "완료 일봉 평균에 필요한 로컬 이력이 없습니다.", as_of=metric.as_of,
            )
        try:
            candidate = frame.copy()
            dates = pd.to_datetime(candidate["date"], errors="coerce")
            if dates.isna().any():
                raise ValueError("daily date is invalid")
            normalized_dates = dates.dt.normalize()
            if not dates.eq(normalized_dates).all():
                raise ValueError("intraday timestamp found in daily comparison")
            if normalized_dates.duplicated().any():
                raise ValueError("duplicate daily date")
            for column in ("is_partial", "partial", "incomplete"):
                if column in candidate:
                    flags = candidate[column].fillna(False)
                    if flags.astype(bool).any():
                        raise ValueError("partial daily observation")
            for column in ("aggregation_status", "bar_status"):
                if column in candidate and candidate[column].astype(str).str.upper().str.contains(
                    "PARTIAL|INCOMPLETE|LIVE|FORMING", regex=True
                ).any():
                    raise ValueError("partial daily observation")
            values = pd.to_numeric(candidate["value"], errors="coerce")
            finite = np.isfinite(values.to_numpy(dtype=float, na_value=np.nan))
            eligible = pd.DataFrame({
                "date": normalized_dates.loc[finite],
                "value": values.loc[finite].astype(float),
            }).sort_values("date", kind="stable").reset_index(drop=True)
            if eligible.empty:
                raise ValueError("no finite eligible daily observation")
            latest = eligible.iloc[-1]
            if comparison_cutoff is not None:
                cutoff = pd.Timestamp(comparison_cutoff)
                if (
                    cutoff.tzinfo is not None
                    or cutoff != cutoff.normalize()
                    or latest["date"] > cutoff
                ):
                    raise ValueError("daily comparison exceeds its typed cutoff")
            if require_metric_match:
                as_of = pd.Timestamp(metric.as_of)
                if as_of.tzinfo is not None:
                    as_of = as_of.tz_localize(None)
                as_of = as_of.normalize()
                metric_value = float(metric.value)
                if (
                    latest["date"] != as_of
                    or not np.isfinite(metric_value)
                    or not np.isclose(
                        float(latest["value"]), metric_value, rtol=0.0, atol=1e-12
                    )
                ):
                    raise ValueError("latest daily observation differs from gated metric")
        except (TypeError, ValueError, OverflowError):
            return cls._unavailable_average_comparison(
                series_id, comparison_kind,
                "일일 날짜·단위·완료 상태 또는 최신값 계약을 검증할 수 없습니다.",
                as_of=metric.as_of,
            )

        def calculate(window: int) -> tuple[
            float | None, float | None, tuple[str, str, int] | None, str | None,
        ]:
            if len(eligible) < window:
                return None, None, None, f"실제 완료 일봉 {window}개가 필요합니다."
            sample = eligible.tail(window)
            mean = float(sample["value"].mean())
            if not np.isfinite(mean):
                return None, None, None, "유한한 산술평균을 계산할 수 없습니다."
            if comparison_kind == "relative_percent":
                if mean == 0:
                    return None, None, None, "평균이 0이라 상대 비교를 계산할 수 없습니다."
                comparison = (float(latest["value"]) / mean - 1.0) * 100.0
            else:
                comparison = (float(latest["value"]) - mean) * 100.0
            coverage = (
                sample.iloc[0]["date"].date().isoformat(),
                sample.iloc[-1]["date"].date().isoformat(),
                len(sample),
            )
            return mean, float(comparison), coverage, None

        mean_5, comparison_5, coverage_5, reason_5 = calculate(5)
        mean_20, comparison_20, coverage_20, reason_20 = calculate(20)
        return DashboardAverageComparisonView(
            series_id=series_id, comparison_kind=comparison_kind,
            interval="completed daily", as_of=latest["date"].date().isoformat(),
            latest_value=float(latest["value"]), mean_5=mean_5, mean_20=mean_20,
            comparison_5=comparison_5, comparison_20=comparison_20,
            coverage_5=coverage_5, coverage_20=coverage_20,
            display_state=DashboardDisplayState.VALUE,
            unavailable_reason=None, reason_5=reason_5, reason_20=reason_20,
        )

    def daily_average_comparisons(
        self,
        metrics: dict[str, DashboardMetricView],
        series: dict[str, DashboardSeriesView] | None = None,
    ) -> dict[str, DashboardAverageComparisonView]:
        """Build independent completed-daily comparisons for cards and rates."""
        series = series if series is not None else self.dashboard_series(metrics)
        price_like = (
            "KOSPI", "KOSDAQ", "SOXX", "NQ_FUTURES", "NASDAQ", "SP500",
            "GOLD", "VIX", "WTI", "USD_KRW", "USD_JPY",
        )
        basis_points = ("UST2", "UST10", "UST30", "UST10_2_SPREAD")
        result: dict[str, DashboardAverageComparisonView] = {}
        for key in price_like + basis_points:
            metric = metrics.get(key)
            view = series.get(key)
            comparison_cutoff = None
            require_metric_match = not bool(
                metric and metric.route.startswith("yahoo-market-current:")
            )
            if metric is not None and key in {"KOSPI", "KOSDAQ"}:
                exact_route = _toss_domestic_ur246_route(key).route_id
                if metric.route == exact_route:
                    try:
                        comparison_cutoff, is_exact_toss = (
                            self._completed_daily_series_cutoff(key, metric)
                        )
                        if not is_exact_toss:
                            raise ValueError("Toss daily comparison route differs")
                    except (TypeError, ValueError, OverflowError):
                        result[key] = self._unavailable_average_comparison(
                            key,
                            "relative_percent",
                            "Toss 현재 시각과 완료 일봉 비교 경계를 검증할 수 없습니다.",
                            as_of=metric.as_of,
                        )
                        continue
                    require_metric_match = False
            result[key] = self.build_daily_average_comparison(
                metric, view.frame if view is not None else None,
                comparison_kind=(
                    "basis_points" if key in basis_points else "relative_percent"
                ),
                require_metric_match=require_metric_match,
                comparison_cutoff=comparison_cutoff,
            )
        return result

    def current_session_card_sparklines(
        self, metrics: dict[str, DashboardMetricView],
    ) -> dict[str, DashboardSparklineView]:
        """Read strict display-only completed 60m traces for the top cards."""
        result: dict[str, DashboardSparklineView] = {}
        root = self.root / "data/state/current_observations/global60m_current"
        required_keys = {
            "schema_version", "series_id", "provider_symbol", "interval",
            "session_date", "session_semantics", "session_start_local",
            "session_end_local", "source_timezone", "completed_bars_only", "points",
        }
        for asset, (series_id, provider_symbol) in GLOBAL60M_CARD_SESSION_SPECS.items():
            metric = metrics.get(asset)
            try:
                if metric is None or not metric.displays_value:
                    raise ValueError("headline is not displayable")
                payload = json.loads(
                    (root / f"{series_id.lower()}.session.json").read_text(encoding="utf-8")
                )
                if (
                    not isinstance(payload, dict)
                    or set(payload) != required_keys
                    or payload["schema_version"] != 1
                    or payload["series_id"] != series_id
                    or payload["provider_symbol"] != provider_symbol
                    or payload["interval"] not in {"30m", "60m"}
                    or payload["completed_bars_only"] is not True
                    or payload["session_semantics"] not in {
                        "CASH_REGULAR", "FUTURES_PROVIDER_SESSION", "UTC_DAY",
                        "KST_DAY_0800",
                    }
                    or not isinstance(payload["points"], list)
                    or not 2 <= len(payload["points"]) <= 48
                ):
                    raise ValueError("session trace contract mismatch")
                points = pd.DataFrame(payload["points"])
                if list(points.columns) != ["bar_end_utc", "value"]:
                    raise ValueError("session point schema mismatch")
                timestamps = pd.to_datetime(points["bar_end_utc"], utc=True, errors="coerce")
                values = pd.to_numeric(points["value"], errors="coerce")
                if (
                    timestamps.isna().any() or not timestamps.is_monotonic_increasing
                    or timestamps.duplicated().any() or values.isna().any()
                    or not np.isfinite(values.to_numpy()).all() or not (values > 0).all()
                ):
                    raise ValueError("session points are invalid")
                session_date = pd.Timestamp(payload["session_date"]).date().isoformat()
                if asset in {"KOSPI", "KOSDAQ"}:
                    if metric.as_of != session_date:
                        raise ValueError("official close and trace session differ")
                elif (
                    metric.source_timestamp is None
                    or timestamps.iloc[-1] != pd.Timestamp(metric.source_timestamp)
                ):
                    raise ValueError("headline and trace endpoint differ")
                if asset in {"NQ_FUTURES", "BITCOIN"}:
                    us_calendar = ExchangeTradingCalendar(ExchangeMarket.US)
                    source_ny_date = timestamps.iloc[-1].tz_convert(
                        "America/New_York"
                    ).date()
                    if us_calendar.is_trading_day(source_ny_date):
                        us_open = pd.Timestamp(
                            us_calendar.session_open(source_ny_date)
                        ).tz_convert("UTC")
                        if timestamps.iloc[-1] >= us_open:
                            keep = timestamps >= us_open
                            timestamps = timestamps[keep].reset_index(drop=True)
                            values = values[keep].reset_index(drop=True)
                    if len(timestamps) < 2:
                        raise ValueError("U.S.-open reset leaves fewer than two points")
                elif asset == "USD_KRW_60M":
                    latest_kst = timestamps.iloc[-1].tz_convert("Asia/Seoul")
                    reset_date = latest_kst.date()
                    if latest_kst.time() < time(8, 0):
                        reset_date -= timedelta(days=1)
                    reset = pd.Timestamp(
                        f"{reset_date.isoformat()} 08:00", tz="Asia/Seoul"
                    ).tz_convert("UTC")
                    keep = timestamps >= reset
                    timestamps = timestamps[keep].reset_index(drop=True)
                    values = values[keep].reset_index(drop=True)
                    if len(timestamps) < 2:
                        raise ValueError("08:00 KST reset leaves fewer than two points")
                frame = pd.DataFrame({"date": timestamps, "value": values})
                end_text = payload["session_end_local"] or "session end"
                result[asset] = DashboardSparklineView(
                    asset=asset,
                    lane_id="YAHOO_GLOBAL60M_CURRENT_SESSION",
                    series_id=provider_symbol,
                    frame=frame,
                    interval=payload["interval"],
                    session_label=f"장 시작 후 {session_date}",
                    session_date=session_date,
                    visual_window=(
                        f"{payload['source_timezone']} "
                        f"{payload['session_start_local']}–{end_text}; completed bars only"
                    ),
                    as_of_kst=timestamps.iloc[-1].tz_convert("Asia/Seoul").strftime(
                        "%Y-%m-%d %H:%M KST"
                    ),
                    source_timestamp=timestamps.iloc[-1].isoformat(),
                    source=(
                        f"Yahoo completed {payload['interval']} session trace; display-only"
                    ),
                    freshness=metric.freshness,
                    display_state=DashboardDisplayState.VALUE,
                    unavailable_reason=None,
                    reference_value=(
                        float(metric.value - metric.change)
                        if metric.value is not None and metric.change is not None
                        else None
                    ),
                )
            except (
                FileNotFoundError, json.JSONDecodeError, KeyError, OSError,
                TypeError, ValueError,
            ):
                continue
        return result

    @staticmethod
    def _unavailable_card_sparkline(
        asset: str,
        *,
        lane_id: str | None,
        series_id: str | None,
        visual_window: str,
        reason: str,
        freshness: str = "UNKNOWN",
        state: DashboardDisplayState = DashboardDisplayState.UNAVAILABLE,
        source: str = "no accepted local native-15m lane",
        session_date: str | None = None,
        as_of_kst: str | None = None,
        source_timestamp: str | None = None,
    ) -> DashboardSparklineView:
        return DashboardSparklineView(
            asset=asset, lane_id=lane_id, series_id=series_id,
            frame=pd.DataFrame(columns=["date", "value"]), interval="15m",
            session_label="15분 스파크라인 미표시", session_date=session_date,
            visual_window=visual_window, as_of_kst=as_of_kst,
            source_timestamp=source_timestamp, source=source, freshness=freshness,
            display_state=state, unavailable_reason=reason,
        )

    def market_card_sparklines(
        self,
        metrics: dict[str, DashboardMetricView],
        *,
        now_utc: object | None = None,
    ) -> dict[str, DashboardSparklineView]:
        """Expose only locally accepted completed native 15-minute card lanes.

        Daily series are intentionally not fallbacks.  UR-030 has accepted only
        CBOE_VIX and Treasury-quote production lanes; the Treasury identities are
        not top market cards, so ^VIX is currently the sole eligible card spark.
        """
        unsupported = {
            "KOSPI": (
                "XKRX_CASH_UNACCEPTED", "KOSPI",
                "XKRX 정규장 · 세션별 reset",
                "승인된 KOSPI native 15분 로컬 레인이 없습니다.",
            ),
            "KOSDAQ": (
                "XKRX_CASH_UNACCEPTED", "KOSDAQ",
                "XKRX 정규장 · 세션별 reset",
                "승인된 KOSDAQ native 15분 로컬 레인이 없습니다.",
            ),
            "SOXX": (
                "XNYS_MARKET_INDEX", "SOXX",
                "XNYS 정규장 · DST/조기폐장 적용",
                "SOXX provider-native 15분 레인은 검증·승인되지 않았습니다.",
            ),
            "NQ_FUTURES": (
                "XNYS_MARKET_INDEX", "NQ=F",
                "09:00 KST 시각창은 native session/roll 검증 후에만 사용",
                "UR-030 XNYS lane의 새 날짜 검증 전이라 NQ=F 15분 시각창을 활성화하지 않습니다.",
            ),
            "NASDAQ": (
                "XNYS_MARKET_INDEX", "^IXIC",
                "XNYS 정규장 · DST/조기폐장 적용",
                "UR-030 XNYS lane의 새 날짜 검증 전이라 Nasdaq 15분봉을 활성화하지 않습니다.",
            ),
            "SP500": (
                "XNYS_MARKET_INDEX", "^GSPC",
                "XNYS 정규장 · DST/조기폐장 적용",
                "UR-030 XNYS lane의 새 날짜 검증 전이라 S&P 500 15분봉을 활성화하지 않습니다.",
            ),
            "GOLD": (
                "YAHOO_FUTURES_UNACCEPTED", "GC=F",
                "native futures session/roll boundary 미검증",
                "GC=F provider-native 15분 레인은 검증·승인되지 않았습니다.",
            ),
            "WTI": (
                "YAHOO_FUTURES_UNACCEPTED", "CL=F",
                "09:00 KST 시각창은 native session/roll 검증 후에만 사용",
                "CL=F provider-native 15분 레인과 roll/maintenance 경계가 승인되지 않았습니다.",
            ),
        }
        result = {
            asset: self._unavailable_card_sparkline(
                asset, lane_id=lane, series_id=series_id,
                visual_window=window, reason=reason,
            )
            for asset, (lane, series_id, window, reason) in unsupported.items()
        }

        # The headline may use one fresh, completed current projection.  The
        # sparkline remains a distinct complete-session artifact and must not
        # turn that single point into an invented session series.
        quote = self._vix_completed_session_metric(now_utc=now_utc)
        if quote is None or not quote.displays_value or quote.completed_bar is not True:
            result["VIX"] = self._unavailable_card_sparkline(
                "VIX", lane_id="CBOE_VIX", series_id="^VIX",
                visual_window="Cboe/Yahoo 현물 VIX 정규 산출·배포 구간 · 24시간 아님",
                reason=(
                    quote.unavailable_reason if quote is not None
                    else "검증된 Yahoo ^VIX completed native 15분 metric이 없습니다."
                ) or "Yahoo ^VIX 15분 metric은 현재 표시할 수 없습니다.",
                freshness=quote.freshness if quote is not None else "UNKNOWN",
                state=quote.display_state if quote is not None else DashboardDisplayState.UNAVAILABLE,
                source=quote.source if quote is not None else "Yahoo / ^VIX",
                session_date=quote.expected_as_of if quote is not None else None,
                as_of_kst=quote.as_of if quote is not None else None,
                source_timestamp=quote.source_timestamp if quote is not None else None,
            )
            return result

        try:
            now = pd.Timestamp(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("now_utc must be timezone-aware")
            scope = reviewed_native_scope(now.to_pydatetime(), "CBOE_VIX")
            session_date = scope.session_date.isoformat()
            if quote.expected_as_of != session_date:
                raise ValueError("VIX metric expected session differs from reviewed scope")
            retained = self._read_vix_intraday_frame()
            exact = retained.loc[
                retained["market_date"].dt.date.eq(scope.session_date)
            ].copy()
            self._verify_vix_intraday_checkpoint(exact)
            policy = MARKET_15M_SERIES_POLICIES["^VIX"]
            starts = pd.to_datetime(exact["bar_start"], utc=True, errors="coerce")
            ends = pd.to_datetime(exact["bar_end"], utc=True, errors="coerce")
            retrieved = pd.to_datetime(exact["retrieved_at"], utc=True, errors="coerce")
            durations = ends - starts
            if (
                exact.empty
                or not exact["series_id"].astype(str).eq(policy.series_id).all()
                or not exact["interval"].astype(str).eq("15m").all()
                or not exact["session"].astype(str).eq("REGULAR").all()
                or not exact["source_timezone"].astype(str).eq(policy.source_timezone).all()
                or starts.isna().any() or ends.isna().any() or retrieved.isna().any()
                or not durations.eq(timedelta(minutes=15)).all()
                or not ends.le(retrieved).all()
                or audit_market_15m_bars(
                    starts.tolist(), scope.expected_bar_starts["^VIX"]
                ).status != "COMPLETE"
            ):
                raise ValueError("VIX retained bars differ from accepted completed native lane")
            exact = exact.assign(
                value=pd.to_numeric(exact["close"], errors="coerce"),
                date=ends,
            ).sort_values("date", kind="stable")
            if exact["value"].isna().any() or not np.isfinite(exact["value"]).all():
                raise ValueError("VIX sparkline close is not finite")
            last_end = pd.Timestamp(exact["date"].iloc[-1]).tz_convert("UTC")
            if quote.source_timestamp is None or pd.Timestamp(quote.source_timestamp).tz_convert("UTC") != last_end:
                raise ValueError("VIX metric source timestamp differs from retained lane")
            expected_as_of = last_end.tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M KST")
            if quote.as_of != expected_as_of:
                raise ValueError("VIX metric KST reference differs from retained lane")
            calendar = ExchangeTradingCalendar(ExchangeMarket.US)
            new_york_date = now.tz_convert("America/New_York").date()
            current_completed = (
                new_york_date == scope.session_date
                and now.tz_convert("UTC") >= calendar.session_close(scope.session_date) + timedelta(minutes=30)
            )
            session_label = (
                f"완료장 {session_date}" if current_completed
                else f"직전 완료장 {session_date}"
            )
            result["VIX"] = DashboardSparklineView(
                asset="VIX", lane_id="CBOE_VIX", series_id="^VIX",
                frame=exact[["date", "value"]].reset_index(drop=True),
                interval="15m", session_label=session_label,
                session_date=session_date,
                visual_window="Cboe/Yahoo 현물 VIX 정규 산출·배포 구간 · 24시간 아님",
                as_of_kst=quote.as_of, source_timestamp=quote.source_timestamp,
                source=quote.source, freshness=quote.freshness,
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=None,
            )
        except (FileNotFoundError, KeyError, OSError, PermissionError, TypeError, ValueError):
            result["VIX"] = self._unavailable_card_sparkline(
                "VIX", lane_id="CBOE_VIX", series_id="^VIX",
                visual_window="Cboe/Yahoo 현물 VIX 정규 산출·배포 구간 · 24시간 아님",
                reason="승인된 CBOE_VIX checkpoint와 completed native 15분 세션을 일치시킬 수 없습니다.",
                freshness="UNKNOWN", source=quote.source,
                session_date=quote.expected_as_of, as_of_kst=quote.as_of,
                source_timestamp=quote.source_timestamp,
            )
        return result

    def dashboard_metrics(
        self, health: object | None = None, *, now_utc: object | None = None,
    ) -> dict[str, DashboardMetricView]:
        """Build every Phase-1 metric through one retained Health boundary.

        Readers are invoked only when Health permits a descriptive value.  A
        missing file, schema mismatch, retained-date mismatch, or reader error
        affects only that metric and never reuses a previous value.
        """
        from stock_data.gui.health_service import DailyHealthArtifactService

        health = health or DailyHealthArtifactService(self.root).load()
        rows = {row.dataset: row for row in getattr(health, "rows", ())}
        metrics: dict[str, DashboardMetricView] = {}

        for key, asset, dataset_id, unit in (
            ("KOSPI", "KOSPI", "kr_index_daily", "index points"),
            ("KOSDAQ", "KOSDAQ", "kr_index_daily", "index points"),
            ("SOXX", "SOXX", "global_etf_price_daily", "USD"),
            ("NASDAQ", "NASDAQ", "global_index_price_daily", "index points"),
            ("SP500", "SP500", "global_index_price_daily", "index points"),
            ("NQ_FUTURES", "NQ_FUTURES", "global_commodity_futures_daily", "index points"),
            ("GOLD", "GOLD", "global_commodity_futures_daily", "USD"),
            ("WTI", "WTI", "global_commodity_futures_daily", "USD"),
        ):
            metrics[key] = self._guarded_metric(
                rows, dataset_id=dataset_id, series_id=key,
                label=DASHBOARD_ASSETS[asset]["label"], unit=unit,
                reader=lambda asset=asset, dataset_id=dataset_id: self._read_asset_metric(
                    asset, as_of=getattr(rows.get(dataset_id), "latest", None),
                ),
                route="NORMALIZED_DAILY",
            )
        metrics["BITCOIN"] = DashboardMetricView(
            dataset_id="market_price_60m_current", series_id="BITCOIN", label="BITCOIN",
            value=None, unit="USD per BTC", as_of=None, expected_as_of=None,
            source="Yahoo completed 60-minute chart", freshness="CURRENT_GATE_BLOCKED",
            pit_status="PIT_BLOCKED", pit_label="표시 전용 · Backtest 사용 불가",
            automation_policy="EVERY_30_MIN_CURRENT_ONLY", automation_enabled=True,
            display_state=DashboardDisplayState.REFRESH_REQUIRED,
            unavailable_reason="CURRENT_SOURCE_TIMESTAMP_REQUIRED: no accepted BTC-USD completed-bar projection.",
            route="YAHOO_CHART_GLOBAL60M:BTC-USD",
        )

        metrics["VIX"] = self._guarded_metric(
            rows, dataset_id="fred_vix_daily", series_id="VIX", label="VIX",
            unit="index points", reader=lambda: self._read_latest_metric(
                "normalized/fred_vix_daily", "date", "vixcls"
            ), route="NORMALIZED_DAILY",
        )
        metrics["VIX_INTRADAY_15M"] = self._vix_intraday_metric(now_utc=now_utc)
        metrics["VKOSPI"] = self._guarded_metric(
            rows, dataset_id="kr_vkospi_daily", series_id="VKOSPI", label="VKOSPI",
            unit="index points", reader=lambda: self._read_latest_metric(
                "normalized/kr_vkospi_daily", "market_date", "close"
            ), route="NORMALIZED_DAILY",
        )
        metrics["USD_KRW"] = self._guarded_metric(
            rows, dataset_id="fred_usd_fx_daily", series_id="USD_KRW", label="USD/KRW",
            unit="KRW per USD", reader=lambda: self._read_latest_metric(
                "normalized/fred_usd_fx_daily", "date", "dexkous"
            ), route="NORMALIZED_DAILY",
        )
        metrics["USD_JPY"] = self._guarded_metric(
            rows, dataset_id="fred_usd_fx_daily", series_id="USD_JPY",
            label=FRED_DEXJPUS_IDENTITY.display_pair, unit="JPY per USD",
            reader=lambda: self._read_latest_metric(
                "normalized/fred_usd_fx_daily", "date", FRED_DEXJPUS_IDENTITY.column
            ), route="NORMALIZED_DAILY",
        )
        for key, column, label in (
            ("UST2", "dgs2", "미국 2Y"),
            ("UST10", "dgs10", "미국 10Y"),
            ("UST30", "dgs30", "미국 30Y"),
        ):
            metrics[key] = self._guarded_metric(
                rows, dataset_id="fred_treasury_yield_daily", series_id=key,
                label=label, unit="percent", reader=lambda column=column: self._read_latest_metric(
                    "normalized/fred_treasury_yield_daily", "date", column
                ), route="NORMALIZED_DAILY",
            )
        for key, label, unit in (
            ("USD_KRW_60M", "USD/KRW 60M", "KRW per USD"),
            ("UST2_FUTURES_60M", "미국 2Y 선물 60M", "futures price"),
            ("UST10_FUTURES_60M", "미국 10Y 선물 60M", "futures price"),
            ("UST30_FUTURES_60M", "미국 30Y 선물 60M", "futures price"),
        ):
            metrics[key] = self._intraday_metric(key, label, unit, now_utc=now_utc)
        for key, series_id, label in (
            ("UST5_QUOTE_15M", "^FVX", "미국 5Y quote"),
            ("UST10_QUOTE_15M", "^TNX", "미국 10Y quote"),
            ("UST30_QUOTE_15M", "^TYX", "미국 30Y quote"),
        ):
            metrics[key] = self._treasury_quote_metric(
                series_id, label, now_utc=now_utc,
            )
        metrics["UST10_2_SPREAD"] = self._guarded_metric(
            rows, dataset_id="us_treasury_spread_daily", series_id="UST10_2_SPREAD",
            label="10Y−2Y", unit="percentage points",
            reader=lambda: self._read_latest_metric(
                "derived/us_treasury_spread_daily", "date", "spread_10y_2y"
            ), route="DERIVED_CONTRACT",
        )
        metrics.update(self._kospi200_breadth_metrics(rows))

        def health_expected(dataset_id: str) -> str | None:
            row = rows.get(dataset_id)
            expected = getattr(row, "expected", None) if row is not None else None
            return expected if isinstance(expected, str) and expected != "N/A" else None

        metrics.update({
            "KOSPI200_BASIS": self._local_derivative_metric(
                "KOSPI200_BASIS", "KOSPI200 선물 Basis",
                "kr_kospi200_futures_nearest_listed_daily", "source-native difference",
                self._read_basis_metric,
                automation_policy="DEPENDENCY_DRIVEN", automation_enabled=True,
                expected_as_of=health_expected(
                    "kr_kospi200_futures_nearest_listed_daily"
                ),
                require_expected_as_of=True,
            ),
            "VOLUME_PCR": self._local_derivative_metric(
                "VOLUME_PCR", "KOSPI200 옵션 거래량 P/C",
                "kr_kospi200_option_pcr_daily", "ratio",
                self._read_volume_pcr_metric,
                automation_policy="DEPENDENCY_DRIVEN", automation_enabled=True,
                expected_as_of=health_expected("kr_kospi200_option_pcr_daily"),
                require_expected_as_of=True,
            ),
            "OI_PCR": self._local_derivative_metric(
                "OI_PCR", "KOSPI200 옵션 OI P/C",
                "kr_kospi200_option_pcr_daily", "ratio",
                self._read_oi_pcr_metric,
                automation_policy="DEPENDENCY_DRIVEN", automation_enabled=True,
                expected_as_of=health_expected("kr_kospi200_option_pcr_daily"),
                require_expected_as_of=True,
            ),
            "LS_FUTURES_FOREIGN_NET": self._local_derivative_metric(
                "LS_FUTURES_FOREIGN_NET", "LS 선물 외국인 순계약",
                "ls_t8462_daily_raw", "contracts",
                self._read_ls_futures_foreign_net_metric,
            ),
            "SHORT_SELLING_VALUE": self._guarded_metric(
                rows, dataset_id="kr_short_selling_trading_daily",
                series_id="SHORT_SELLING_VALUE", label="공매도 거래대금",
                unit="KRW", reader=self._read_short_selling_market_value,
                route="OFFICIAL_DAILY_MARKET_AGGREGATE",
            ),
            "US_OPTION_PCR": self._unavailable_metric(
                "US_OPTION_PCR", "미국 옵션 P/C", DashboardDisplayState.PROHIBITED,
                "ORATS contract-only 경로는 준비됐지만 구독·이용권·최종성·root 범위가 승인되지 않았습니다.",
            ),
            "CALL_WALL": self._local_derivative_metric(
                "CALL_WALL", "Call 최대 OI 행사가", "kr_kospi200_option_walls_daily",
                "strike", lambda: self._read_wall_metric("call"),
                automation_policy="DEPENDENCY_DRIVEN", automation_enabled=True,
                expected_as_of=health_expected("kr_kospi200_option_walls_daily"),
                require_expected_as_of=True,
            ),
            "PUT_WALL": self._local_derivative_metric(
                "PUT_WALL", "Put 최대 OI 행사가", "kr_kospi200_option_walls_daily",
                "strike", lambda: self._read_wall_metric("put"),
                automation_policy="DEPENDENCY_DRIVEN", automation_enabled=True,
                expected_as_of=health_expected("kr_kospi200_option_walls_daily"),
                require_expected_as_of=True,
            ),
            "ACCOUNT": self._unavailable_metric(
                "ACCOUNT", "계좌 상태", DashboardDisplayState.UNAVAILABLE,
                "연동 전 / NOT_AVAILABLE",
            ),
            "ASSETS": self._unavailable_metric(
                "ASSETS", "자산 누적 증감", DashboardDisplayState.UNAVAILABLE,
                "계좌 연동 전 / NOT_AVAILABLE",
            ),
        })
        from stock_data.gui.current_display import load_dashboard_current

        current = load_dashboard_current(self.root)
        for identity in ("SP500", "NASDAQ", "SOXX", "NQ_FUTURES", "GOLD", "WTI"):
            observation = current.get(identity)
            metric = metrics.get(identity)
            if observation is None or metric is None:
                continue
            if metric.display_state is DashboardDisplayState.PROHIBITED:
                continue
            if metric.as_of is not None and metric.as_of >= observation.source_date:
                continue
            metrics[identity] = replace(
                metric,
                value=observation.value,
                unit=observation.unit,
                as_of=observation.source_date,
                source=f"{observation.provider} · FDR 조회 시점 일봉",
                freshness="CURRENT",
                pit_status="PIT_BLOCKED",
                pit_label="표시 전용 · Backtest 사용 불가",
                automation_policy="MANUAL_30M_BOUNDED",
                automation_enabled=False,
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=(
                    metric.unavailable_reason
                    or "PRIMARY_ROUTE_UNAVAILABLE; FDR daily display fallback active"
                ),
                route="FDR_DAILY_CURRENT_DISPLAY_FALLBACK",
            )
        soxx_current, soxx_reason = load_nasdaq_soxx_info_current_observation(self.root)
        if soxx_current is not None:
            metric = metrics.get("SOXX")
            if metric is not None and metric.display_state is not DashboardDisplayState.PROHIBITED:
                metrics["SOXX"] = replace(
                    metric,
                    dataset_id=soxx_current.identity.dataset_id,
                    value=soxx_current.value,
                    unit=soxx_current.unit,
                    as_of=pd.Timestamp(soxx_current.provider_timestamp_utc).tz_convert(
                        "Asia/Seoul"
                    ).date().isoformat(),
                    expected_as_of=None,
                    source="NASDAQ_OFFICIAL retained current ETF snapshot",
                    freshness="CURRENT_PROVISIONAL",
                    pit_status="PIT_BLOCKED",
                    pit_label="display-only current snapshot; Backtest use prohibited",
                    automation_policy="RETAINED_ONE_SHOT_RECOVERY_ONLY",
                    automation_enabled=False,
                    display_state=DashboardDisplayState.VALUE,
                    unavailable_reason=soxx_reason,
                    route=soxx_current.route_id,
                    change=None,
                    change_pct=None,
                    source_timestamp=soxx_current.provider_timestamp_utc,
                )
        mobile_home, mobile_home_provenance = load_naver_mobile_home_current_observations(
            self.root
        )
        for series_id, observation in mobile_home.items():
            metric = metrics.get(series_id)
            if metric is None:
                continue
            source_date_kst = pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                "Asia/Seoul"
            ).date().isoformat()
            # A finalized KRX daily close owns the post-close display.  The
            # retained 15:01 web observation is an intraday snapshot and must
            # never replace a same-date completed close.
            if (
                series_id in {"KOSPI", "KOSDAQ"}
                and metric.displays_value
                and metric.as_of is not None
                and metric.as_of >= source_date_kst
            ):
                continue
            metrics[series_id] = replace(
                metric,
                dataset_id=observation.dataset_id,
                value=observation.value,
                unit=observation.unit,
                # The existing daily-card field retains only the KST source
                # date. The precise provider time remains in source_timestamp
                # and in the visible current-observation strip.
                as_of=source_date_kst,
                expected_as_of=None,
                source="NAVER_FINANCE_WEB retained current snapshot",
                freshness="CURRENT_PROVISIONAL",
                pit_status="PIT_BLOCKED",
                pit_label="display-only current snapshot; Backtest use prohibited",
                automation_policy="RETAINED_RECOVERY_ONLY",
                automation_enabled=False,
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=mobile_home_provenance,
                route=observation.route_id,
                change=None,
                change_pct=None,
                source_timestamp=observation.provider_timestamp_utc,
            )
        now = now_utc if now_utc is not None else pd.Timestamp.now(tz="UTC")
        for series_id in ("KOSPI", "KOSDAQ"):
            observation, toss_reason = load_toss_domestic_ur246_current_observation(
                self.root, symbol=series_id,
            )
            if observation is None:
                continue
            decision = classify_current_display_timestamp(
                source_timestamp=observation.provider_timestamp_utc,
                retrieved_at=observation.retrieved_at_utc,
                timestamp_basis=observation.timestamp_basis.value,
                now_utc=now,
            )
            if not decision.allow_value:
                continue
            metric = metrics.get(series_id)
            if metric is None:
                continue
            source_date_kst = pd.Timestamp(
                observation.provider_timestamp_utc
            ).tz_convert("Asia/Seoul").date().isoformat()
            if (
                metric.dataset_id == "kr_index_daily"
                and metric.displays_value
                and metric.as_of is not None
                and metric.as_of >= source_date_kst
            ):
                continue
            retrieval_basis = observation.timestamp_basis.value == "RETRIEVAL_TIMESTAMP"
            metrics[series_id] = replace(
                metric,
                dataset_id=observation.identity.dataset_id,
                value=observation.value,
                unit=observation.unit,
                as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                    "Asia/Seoul"
                ).strftime("%m-%d %H:%M KST"),
                expected_as_of=None,
                source=(
                    "tossinvest_open_api retained current snapshot · "
                    "provider event time unavailable; retrieval-time basis"
                    if retrieval_basis else
                    "tossinvest_open_api retained current snapshot"
                ),
                freshness=decision.freshness or "CURRENT_PROVISIONAL",
                pit_status="PIT_BLOCKED",
                pit_label="display-only current snapshot; Backtest use prohibited",
                automation_policy="EVERY_30_MIN_CURRENT_ONLY",
                automation_enabled=True,
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=decision.reason or toss_reason,
                route=observation.route_id,
                change=None,
                change_pct=None,
                source_timestamp=observation.provider_timestamp_utc,
                retrieved_at_utc=observation.retrieved_at_utc,
                timestamp_basis=observation.timestamp_basis.value,
            )
        global_current, _global_current_reason = load_global60m_ur232_current_observations(
            self.root
        )
        for coverage_id, metric_id, label in (
            ("KOSPI_CURRENT_60M", "KOSPI", "KOSPI"),
            ("KOSDAQ_CURRENT_60M", "KOSDAQ", "KOSDAQ"),
            ("USD_KRW_60M", "USD_KRW_60M", "USD/KRW"),
            ("NQ_FUTURES_CURRENT_60M", "NQ_FUTURES", "Nasdaq 100"),
            ("NASDAQ_CURRENT_60M", "NASDAQ", "Nasdaq"),
            ("SP500_CURRENT_60M", "SP500", "S&P 500"),
            ("SOXX_CURRENT_60M", "SOXX", "SOXX"),
            ("GOLD_CURRENT_60M", "GOLD", "GOLD"),
            ("WTI_CURRENT_60M", "WTI", "WTI"),
            ("BITCOIN_CURRENT_60M", "BITCOIN", "BITCOIN"),
            ("UST2_FUTURES_60M", "UST2_FUTURES_60M", "미국 2Y 선물"),
            ("UST10_FUTURES_60M", "UST10_FUTURES_60M", "미국 10Y 선물"),
            ("UST30_FUTURES_60M", "UST30_FUTURES_60M", "미국 30Y 선물"),
        ):
            observation = global_current.get(coverage_id)
            if observation is None:
                continue
            if (
                metric_id in {"KOSPI", "KOSDAQ"}
                and metrics[metric_id].route.startswith("toss-market-price:")
            ):
                continue
            change, change_pct = _load_global60m_current_comparison(
                self.root, coverage_id, observation,
            )
            metrics[metric_id] = DashboardMetricView(
                dataset_id="market_price_60m_current", series_id=metric_id,
                label=label, value=observation.value, unit=observation.unit,
                as_of=pd.Timestamp(observation.provider_timestamp_utc).tz_convert(
                    "Asia/Seoul"
                ).strftime("%m-%d %H:%M KST"),
                expected_as_of=None,
                source=(
                    f"Yahoo completed {observation.interval.value} current-only projection"
                ),
                freshness=f"CURRENT_COMPLETED_{observation.interval.value.upper()}",
                pit_status="PIT_BLOCKED",
                pit_label="표시 전용 · Backtest 사용 불가",
                automation_policy="EVERY_30_MIN_CURRENT_ONLY", automation_enabled=True,
                display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
                route=observation.route_id, change=change, change_pct=change_pct,
                source_timestamp=observation.provider_timestamp_utc,
                delay_status="DELAYED_COMPLETED_BAR", completed_bar=True,
            )
        return {
            key: self._gate_current_metric(
                metric,
                now_utc=now,
                allow_kr_market_closed_last_verified=(key in {"KOSPI", "KOSDAQ"}),
            )
            for key, metric in metrics.items()
        }

    @staticmethod
    def treasury_rate_views(
        metrics: dict[str, DashboardMetricView],
    ) -> dict[str, TreasuryRateView]:
        """Compose rate rows without treating unlike tenors or timestamps as one value."""
        return {
            "UST2": TreasuryRateView(
                view_id="UST2", label="미국 2Y 금리",
                official_daily=metrics.get("UST2"), intraday_quote=None,
                official_provider="FRED", official_data_type="OFFICIAL_DAILY_YIELD",
                intraday_provider=None, intraday_data_type=None,
            ),
            "UST5_QUOTE": TreasuryRateView(
                view_id="UST5_QUOTE", label="미국 5Y quote",
                official_daily=None, intraday_quote=metrics.get("UST5_QUOTE_15M"),
                official_provider=None, official_data_type=None,
                intraday_provider="Yahoo/Cboe",
                intraday_data_type="INDICATIVE_DELAYED_QUOTE_INDEX",
            ),
            "UST10": TreasuryRateView(
                view_id="UST10", label="미국 10Y 금리",
                official_daily=metrics.get("UST10"),
                intraday_quote=metrics.get("UST10_QUOTE_15M"),
                official_provider="FRED", official_data_type="OFFICIAL_DAILY_YIELD",
                intraday_provider="Yahoo/Cboe",
                intraday_data_type="INDICATIVE_DELAYED_QUOTE_INDEX",
            ),
            "UST30": TreasuryRateView(
                view_id="UST30", label="미국 30Y 금리",
                official_daily=metrics.get("UST30"),
                intraday_quote=metrics.get("UST30_QUOTE_15M"),
                official_provider="FRED", official_data_type="OFFICIAL_DAILY_YIELD",
                intraday_provider="Yahoo/Cboe",
                intraday_data_type="INDICATIVE_DELAYED_QUOTE_INDEX",
            ),
            "UST10_2_SPREAD": TreasuryRateView(
                view_id="UST10_2_SPREAD", label="10Y−2Y 공식",
                official_daily=metrics.get("UST10_2_SPREAD"), intraday_quote=None,
                official_provider="derived from FRED contracted yields",
                official_data_type="CONTRACTED_DERIVED_DAILY_SPREAD",
                intraday_provider=None, intraday_data_type=None,
            ),
        }

    @staticmethod
    def vix_source_views(
        metrics: dict[str, DashboardMetricView],
    ) -> dict[str, VIXSourceView]:
        """Expose two explicit VIX roles without merging their values or dates."""
        return {
            "VIX": VIXSourceView(
                view_id="VIX", label="VIX",
                official_daily=metrics.get("VIX"),
                intraday_quote=metrics.get("VIX_INTRADAY_15M"),
                official_provider="FRED / VIXCLS",
                official_data_type="COMPLETED_DAILY_PRIMARY",
                intraday_provider="Yahoo / ^VIX",
                intraday_data_type="INDICATIVE_DELAYED_PROVIDER_SUBSET_15M",
            )
        }

    def _kospi200_breadth_metrics(self, rows: dict) -> dict[str, DashboardMetricView]:
        """Expose only the contract-validated, exact-date KOSPI200 scope.

        This is intentionally separate from the broad KOSPI/KOSDAQ market
        breadth dataset.  The retained contract permits no interval inference:
        every displayed component has one identical Health and membership date.
        """
        dataset_id = "kr_kospi200_breadth_daily"
        definitions = (
            ("KOSPI200_ADVANCING", "KOSPI200 상승", "advancing"),
            ("KOSPI200_DECLINING", "KOSPI200 하락", "declining"),
            ("KOSPI200_UNCHANGED", "KOSPI200 보합", "unchanged"),
        )
        row = rows.get(dataset_id)
        if row is None:
            return {
                key: self._unavailable_metric(
                    key, label, DashboardDisplayState.UNAVAILABLE,
                    "KOSPI200 breadth Health V2 row를 읽을 수 없습니다.", dataset_id=dataset_id,
                )
                for key, label, _column in definitions
            }
        if row.operational == "BLOCKED":
            return {
                key: self._metric_without_value(
                    row, key, label, "constituents", DashboardDisplayState.PROHIBITED,
                    row.blocker if row.blocker != "N/A" else "운영 차단 상태입니다.",
                    "DERIVED_EXACT_DATE_CONTRACT",
                )
                for key, label, _column in definitions
            }
        if getattr(row, "runtime_coverage", "NOT_PROBED").startswith("FAILED:"):
            return {
                key: self._metric_without_value(
                    row, key, label, "constituents", DashboardDisplayState.UNAVAILABLE,
                    "KOSPI200 breadth 로컬 계약 검증에 실패했습니다.",
                    "DERIVED_EXACT_DATE_CONTRACT",
                )
                for key, label, _column in definitions
            }
        if row.freshness not in DISPLAYABLE_FRESHNESS:
            state = (
                DashboardDisplayState.REFRESH_REQUIRED
                if row.freshness == "STALE" else DashboardDisplayState.UNAVAILABLE
            )
            reason = "데이터 갱신 필요" if state is DashboardDisplayState.REFRESH_REQUIRED else "현재 표시 불가"
            return {
                key: self._metric_without_value(
                    row, key, label, "constituents", state, reason,
                    "DERIVED_EXACT_DATE_CONTRACT",
                )
                for key, label, _column in definitions
            }
        try:
            breadth = self._read_kospi200_breadth()
            as_of = self._iso_date(breadth.get("date"))
            values = {
                column: _to_float(breadth.get(column))
                for _key, _label, column in definitions
            }
        except (FileNotFoundError, IndexError, KeyError, OSError, PermissionError, TypeError, ValueError):
            as_of = None
            values = {}
        if as_of is None or (row.latest != "N/A" and as_of != row.latest) or any(
            value is None for value in values.values()
        ):
            return {
                key: self._metric_without_value(
                    row, key, label, "constituents", DashboardDisplayState.UNAVAILABLE,
                    "KOSPI200-only 로컬 계약 오류 또는 Health 기준일 불일치",
                    "DERIVED_EXACT_DATE_CONTRACT",
                )
                for key, label, _column in definitions
            }
        return {
            key: DashboardMetricView(
                dataset_id=dataset_id, series_id=key, label=label,
                value=values[column], unit="constituents", as_of=as_of,
                expected_as_of=row.expected, source=row.source, freshness=row.freshness,
                pit_status=row.pit, pit_label=_pit_label(row.pit),
                automation_policy=row.automation.split(" / ", 1)[0],
                automation_enabled=row.automation.endswith("ENABLED"),
                display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
                route="DERIVED_EXACT_DATE_CONTRACT",
            )
            for key, label, column in definitions
        }

    def _read_kospi200_breadth(self) -> dict:
        frame = read_dataset(
            self.root / "data/derived/kr_kospi200_breadth_daily",
            KR_KOSPI200_BREADTH_DAILY,
            validate_kospi200_breadth_daily,
        )
        latest = pd.to_datetime(frame["date"], errors="raise").max().date().isoformat()
        scope = frame.loc[frame["date"].astype(str).eq(latest)].reset_index(drop=True)
        if len(scope) != 1:
            raise ValueError("KOSPI200 breadth latest exact-date scope must contain one row")
        return scope.iloc[0].to_dict()

    def _expected_derivative_date(self) -> str | None:
        try:
            frame = self.query.tail(
                "normalized/kr_kospi200_index_daily", rows=10, columns=["date"]
            )
            dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
            today = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None).normalize()
            completed = dates.loc[dates.lt(today)]
            return completed.max().date().isoformat() if not completed.empty else None
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            return None

    def _local_derivative_metric(
        self, series_id: str, label: str, dataset_id: str, unit: str, reader,
        *, automation_policy: str = "MANUAL_BOUNDED",
        automation_enabled: bool = False,
        expected_as_of: str | None = None,
        require_expected_as_of: bool = False,
    ) -> DashboardMetricView:
        expected = expected_as_of
        if expected is None and not require_expected_as_of:
            expected = self._expected_derivative_date()
        try:
            value, as_of, source = reader()
        except (IndexError, KeyError, OSError, PermissionError, TypeError, ValueError):
            value, as_of, source = None, None, "local persisted data"
        if value is None or as_of is None or expected is None:
            reason = (
                "Health V2의 완료 거래일을 확인할 수 없어 자동 관리 파생지표를 "
                "표시할 수 없습니다."
                if require_expected_as_of and expected is None
                else (
                    f"완료 거래일 {expected or 'N/A'}와 파생 기준일 "
                    f"{as_of or 'N/A'}가 일치하지 않습니다."
                )
            )
            return DashboardMetricView(
                dataset_id=dataset_id, series_id=series_id, label=label,
                value=None, unit=unit, as_of=as_of, expected_as_of=expected,
                source=source, freshness="STALE_OR_MISSING", pit_status="PIT_BLOCKED",
                pit_label="예측 사용 불가", automation_policy=automation_policy,
                automation_enabled=automation_enabled,
                display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason=reason, route="DERIVED_DAILY_T_PLUS_1",
            )
        if as_of != expected:
            pending_reason = (
                "20:30 KST 자동 복구 대상이며 기준일이 일치하기 전에는 "
                "표시할 수 없습니다."
                if automation_enabled
                else "수동 검증 전에는 표시할 수 없습니다."
            )
            return DashboardMetricView(
                dataset_id=dataset_id, series_id=series_id, label=label,
                value=None, unit=unit, as_of=as_of, expected_as_of=expected,
                source=source, freshness="STALE",
                pit_status="PIT_BLOCKED", pit_label="예측 사용 불가",
                automation_policy=automation_policy,
                automation_enabled=automation_enabled,
                display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason=(
                    f"최근 검증 장마감 {as_of}; 완료 거래일 {expected} 데이터는 아직 "
                    f"없으며 {pending_reason}"
                ),
                route="DERIVED_DAILY_T_PLUS_1",
            )
        return DashboardMetricView(
            dataset_id=dataset_id, series_id=series_id, label=label,
            value=float(value), unit=unit, as_of=as_of, expected_as_of=expected,
            source=source, freshness="EXPECTED_LAG", pit_status="PIT_LIMITED",
            pit_label="설명용", automation_policy=automation_policy,
            automation_enabled=automation_enabled,
            display_state=DashboardDisplayState.VALUE,
            unavailable_reason=None, route="DERIVED_DAILY_T_PLUS_1",
        )

    def _read_basis_metric(self) -> tuple[float | None, str | None, str]:
        frame = self.query.tail(
            "derived/kr_kospi200_futures_nearest_listed_daily", rows=20,
            columns=["date", "session", "settlement_basis", "basis_status", "source"],
        )
        frame = frame.loc[
            frame["session"].astype(str).eq("REGULAR_DAY")
            & frame["basis_status"].astype(str).eq(
                "SAME_ROW_REGULAR_SESSION_SOURCE_NATIVE_DIFFERENCE"
            )
        ].sort_values("date")
        row = frame.iloc[-1]
        return _to_float(row["settlement_basis"]), self._iso_date(row["date"]), str(row["source"])

    def _read_volume_pcr_metric(self) -> tuple[float | None, str | None, str]:
        frame = self.query.tail(
            "derived/kr_kospi200_option_pcr_daily", rows=2,
            columns=["date", "volume_pcr", "observation_status", "source"],
        ).sort_values("date")
        frame = frame.loc[frame["observation_status"].astype(str).str.lower().eq("observed")]
        row = frame.iloc[-1]
        return _to_float(row["volume_pcr"]), self._iso_date(row["date"]), str(row["source"])

    def _read_oi_pcr_metric(self) -> tuple[float | None, str | None, str]:
        frame = self.query.tail(
            "derived/kr_kospi200_option_pcr_daily", rows=2,
            columns=["date", "open_interest_pcr", "observation_status", "source"],
        ).sort_values("date")
        frame = frame.loc[frame["observation_status"].astype(str).str.lower().eq("observed")]
        row = frame.iloc[-1]
        return (
            _to_float(row["open_interest_pcr"]),
            self._iso_date(row["date"]),
            str(row["source"]),
        )

    def _read_ls_futures_foreign_net_metric(
        self,
    ) -> tuple[float | None, str | None, str]:
        row = self.derivatives.ls_flow("U")
        value = _to_float(row.get("foreign_contracts"))
        as_of = self._iso_date(row.get("date"))
        if value is None or as_of is None:
            raise ValueError(str(row.get("reason") or "LS t8462 row unavailable"))
        return value, as_of, (
            "LS OpenAPI t8462 · KOSPI200 futures · session U · "
            "Raw descriptive investor net contracts"
        )

    def _read_wall_metric(self, side: str) -> tuple[float | None, str | None, str]:
        frame, _metadata = self.derivatives.option_wall()
        if frame.empty:
            raise ValueError("retained wall artifact is empty")
        row = frame.sort_values("date").iloc[-1]
        return (
            _to_float(row[f"{side}_wall_strike"]), self._iso_date(row["date"]),
            "data.go.kr option OI · front retained maturity",
        )

    def _read_short_selling_market_value(
        self,
    ) -> tuple[float | None, str | None, float | None, float | None]:
        """Sum the exact latest official KOSPI/KOSDAQ short-trading amount."""
        latest_dates: list[pd.Timestamp] = []
        totals: list[float] = []
        for market in ("KOSPI", "KOSDAQ"):
            frame = self.query.tail(
                "normalized/kr_short_selling_trading_daily", rows=6000,
                columns=["date", "market", "short_trading_value"],
                partitions={"market": market},
            )
            frame = frame.loc[frame["market"].astype(str).eq(market)].copy()
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame["short_trading_value"] = pd.to_numeric(
                frame["short_trading_value"], errors="coerce"
            )
            frame = frame.dropna(subset=["date", "short_trading_value"])
            if frame.empty:
                raise ValueError(f"official short-selling rows are absent: {market}")
            latest = frame["date"].max().normalize()
            exact = frame.loc[frame["date"].dt.normalize().eq(latest)]
            amount = float(exact["short_trading_value"].sum())
            if not np.isfinite(amount) or amount < 0:
                raise ValueError("official short-selling aggregate is invalid")
            latest_dates.append(latest)
            totals.append(amount)
        if len(set(latest_dates)) != 1:
            raise ValueError("KOSPI/KOSDAQ short-selling dates differ")
        return sum(totals), latest_dates[0].date().isoformat(), None, None

    def _read_intraday_frame(self, series_id: str) -> pd.DataFrame:
        frame = read_dataset(
            self.root / "data/normalized/market_price_60m_observation",
            MARKET_PRICE_60M_OBSERVATION, validate_market_price_60m,
        )
        frame = frame.loc[frame["symbol"].astype(str).eq(series_id)].copy()
        if frame.empty:
            raise ValueError(f"intraday series is absent: {series_id}")
        frame["bar_start"] = pd.to_datetime(frame["bar_start"], utc=True, errors="coerce")
        frame["bar_end"] = pd.to_datetime(frame["bar_end"], utc=True, errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["bar_start", "bar_end", "close"]).sort_values("bar_start")
        if frame.empty:
            raise ValueError(f"intraday series is invalid: {series_id}")
        return frame

    def _intraday_metric(
        self,
        series_id: str,
        label: str,
        unit: str,
        *,
        now_utc: object | None = None,
    ) -> DashboardMetricView:
        try:
            frame = self._read_intraday_frame(series_id)
            latest = frame.iloc[-1]
            bar_end = pd.Timestamp(latest["bar_end"]).tz_convert("UTC")
            decision = classify_intraday_60m_freshness(
                bar_end=bar_end,
                now_utc=now_utc if now_utc is not None else pd.Timestamp.now(tz="UTC"),
            )
            if not decision.allow_value:
                return DashboardMetricView(
                    dataset_id="market_price_60m_observation", series_id=series_id,
                    label=label, value=None, unit=unit, as_of=None,
                    expected_as_of=None, source="Yahoo delayed chart API",
                    freshness=decision.freshness, pit_status="PIT_BLOCKED",
                    pit_label="예측 사용 불가", automation_policy="HOURLY_BOUNDED",
                    automation_enabled=True,
                    display_state=DashboardDisplayState.REFRESH_REQUIRED,
                    unavailable_reason=decision.reason,
                    route="NORMALIZED_60M_DELAYED",
                )
            values = frame["close"]
            value = float(values.iloc[-1])
            change = float(values.diff().iloc[-1]) if len(values) > 1 else None
            previous = float(values.iloc[-2]) if len(values) > 1 else None
            change_pct = change / previous * 100 if change is not None and previous else None
            as_of = bar_end.tz_convert("Asia/Seoul").strftime("%m-%d %H:%M KST")
        except (FileNotFoundError, KeyError, OSError, PermissionError, TypeError, ValueError):
            return DashboardMetricView(
                dataset_id="market_price_60m_observation", series_id=series_id, label=label,
                value=None, unit=unit, as_of=None, expected_as_of=None,
                source="Yahoo delayed chart API", freshness="STALE_OR_MISSING",
                pit_status="PIT_BLOCKED", pit_label="예측 사용 불가",
                automation_policy="HOURLY_BOUNDED", automation_enabled=True,
                display_state=DashboardDisplayState.REFRESH_REQUIRED,
                unavailable_reason="최종 확정 60분 봉을 검증할 수 없습니다.",
                route="NORMALIZED_60M_DELAYED",
            )
        source = (
            "Yahoo delayed FX · KRW=X" if series_id == "USD_KRW_60M" else
            "Yahoo delayed continuous Treasury future · price, not yield"
        )
        if decision.reason:
            source = f"{source} · {decision.reason}"
        return DashboardMetricView(
            dataset_id="market_price_60m_observation", series_id=series_id, label=label,
            value=value, unit=unit, as_of=as_of, expected_as_of=None,
            source=source, freshness=decision.freshness, pit_status="PIT_BLOCKED",
            pit_label="예측 사용 불가", automation_policy="HOURLY_BOUNDED",
            automation_enabled=True, display_state=DashboardDisplayState.VALUE,
            unavailable_reason=None, route="NORMALIZED_60M_DELAYED",
            change=change, change_pct=change_pct,
            source_timestamp=bar_end.isoformat(),
        )

    def _read_treasury_quote_frame(self, series_id: str) -> pd.DataFrame:
        if series_id not in {"^FVX", "^TNX", "^TYX"}:
            raise ValueError(f"not a contracted Treasury quote identity: {series_id}")
        frame = read_dataset(
            self.root / "data/normalized/market_price_15m_observation",
            MARKET_PRICE_15M_OBSERVATION, validate_market_price_15m,
        )
        frame = frame.loc[frame["series_id"].astype(str).eq(series_id)].copy()
        if frame.empty:
            raise ValueError(f"Treasury quote identity is absent: {series_id}")
        frame["bar_start"] = pd.to_datetime(frame["bar_start"], utc=True, errors="coerce")
        frame["bar_end"] = pd.to_datetime(frame["bar_end"], utc=True, errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(subset=["market_date", "bar_start", "bar_end", "close"])
        if frame.empty:
            raise ValueError(f"Treasury quote identity is invalid: {series_id}")
        return frame.sort_values("bar_start", kind="stable").reset_index(drop=True)

    def _treasury_quote_metric(
        self,
        series_id: str,
        label: str,
        *,
        now_utc: object | None = None,
    ) -> DashboardMetricView:
        """Read one exact accepted native lane as a quote index, never as a yield."""
        expected_date: str | None = None
        retained_date: str | None = None
        try:
            now = pd.Timestamp(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("now_utc must be timezone-aware")
            current = _load_yahoo_native15m_current(self.root, series_id)
            if current is not None:
                source_time = pd.Timestamp(
                    current.provider_timestamp_utc
                ).tz_convert("Asia/Seoul")
                return DashboardMetricView(
                    dataset_id="market_price_15m_current", series_id=series_id,
                    label=label, value=float(current.value),
                    unit="quote index points",
                    as_of=source_time.strftime("%Y-%m-%d %H:%M KST"),
                    expected_as_of=None,
                    source=(
                        f"Yahoo/Cboe {series_id} completed provider-native 15m "
                        "indicative quote index; not an official Treasury yield"
                    ),
                    freshness="CURRENT_COMPLETED_15M", pit_status="PIT_BLOCKED",
                    pit_label="표시 전용 · Backtest 사용 불가",
                    automation_policy="EVERY_30_MIN_CURRENT_ONLY",
                    automation_enabled=True,
                    display_state=DashboardDisplayState.VALUE,
                    unavailable_reason=None, route=current.route_id,
                    source_timestamp=current.provider_timestamp_utc,
                    delay_status="DELAYED_COMPLETED_BAR", completed_bar=True,
                )
            scope = reviewed_native_scope(now.to_pydatetime(), "YAHOO_TREASURY_QUOTE")
            expected_date = scope.session_date.isoformat()
            frame = self._read_treasury_quote_frame(series_id)
            retained_date = pd.to_datetime(frame["market_date"], errors="coerce").max().date().isoformat()
            exact = frame.loc[
                pd.to_datetime(frame["market_date"], errors="coerce").dt.date.eq(scope.session_date)
            ]
            completeness = audit_market_15m_bars(
                exact["bar_start"].tolist(), scope.expected_bar_starts[series_id]
            )
            if retained_date != expected_date or completeness.status != "COMPLETE":
                return DashboardMetricView(
                    dataset_id="market_price_15m_observation", series_id=series_id,
                    label=label, value=None, unit="quote index points",
                    as_of=None, expected_as_of=expected_date,
                    source=f"Yahoo/Cboe {series_id} indicative delayed quote index",
                    freshness="STALE", pit_status="PIT_BLOCKED",
                    pit_label="예측 사용 불가", automation_policy="DAILY_NATIVE_15M",
                    automation_enabled=True,
                    display_state=DashboardDisplayState.REFRESH_REQUIRED,
                    unavailable_reason=(
                        f"Yahoo 15분 quote 기준일 {retained_date or 'N/A'}와 "
                        f"완료 세션 {expected_date} 또는 23개 native bar가 일치하지 않습니다."
                    ),
                    route="NORMALIZED_NATIVE_15M_TREASURY_QUOTE",
                )
            latest = exact.sort_values("bar_start", kind="stable").iloc[-1]
            value = float(latest["close"])
            if not np.isfinite(value):
                raise ValueError("Treasury quote close is not finite")
            bar_end = pd.Timestamp(latest["bar_end"]).tz_convert("Asia/Seoul")
            return DashboardMetricView(
                dataset_id="market_price_15m_observation", series_id=series_id,
                label=label, value=value, unit="quote index points",
                as_of=bar_end.strftime("%Y-%m-%d %H:%M KST"),
                expected_as_of=expected_date,
                source=(
                    f"Yahoo/Cboe {series_id} indicative delayed provider-native 15m quote index; "
                    "not an official Treasury yield"
                ),
                freshness="CURRENT", pit_status="PIT_BLOCKED",
                pit_label="예측 사용 불가", automation_policy="DAILY_NATIVE_15M",
                automation_enabled=True, display_state=DashboardDisplayState.VALUE,
                unavailable_reason=None,
                route="NORMALIZED_NATIVE_15M_TREASURY_QUOTE",
                source_timestamp=bar_end.tz_convert("UTC").isoformat(),
            )
        except (FileNotFoundError, KeyError, OSError, PermissionError, TypeError, ValueError):
            return DashboardMetricView(
                dataset_id="market_price_15m_observation", series_id=series_id,
                label=label, value=None, unit="quote index points",
                as_of=None, expected_as_of=expected_date,
                source=f"Yahoo/Cboe {series_id} indicative delayed quote index",
                freshness="UNKNOWN", pit_status="PIT_BLOCKED",
                pit_label="예측 사용 불가", automation_policy="DAILY_NATIVE_15M",
                automation_enabled=True, display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason="검증된 provider-native 15분 quote index를 읽을 수 없습니다.",
                route="NORMALIZED_NATIVE_15M_TREASURY_QUOTE",
            )

    def _read_vix_intraday_frame(self) -> pd.DataFrame:
        """Read only the contracted Yahoo ^VIX provider subset from local storage."""
        frame = read_dataset(
            self.root / "data/normalized/market_price_15m_observation",
            MARKET_PRICE_15M_OBSERVATION, validate_market_price_15m,
        )
        frame = frame.loc[frame["series_id"].astype(str).eq("^VIX")].copy()
        if frame.empty:
            raise ValueError("Yahoo ^VIX native-15m observations are absent")
        frame["market_date"] = pd.to_datetime(frame["market_date"], errors="coerce")
        for column in ("bar_start", "bar_end", "retrieved_at"):
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna(
            subset=["market_date", "bar_start", "bar_end", "retrieved_at", "close"]
        ).sort_values("bar_start", kind="stable").reset_index(drop=True)
        if frame.empty:
            raise ValueError("Yahoo ^VIX native-15m observations are invalid")
        return frame

    def _verify_vix_intraday_checkpoint(self, latest: pd.DataFrame) -> None:
        """Bind the local provider subset to the accepted CBOE_VIX checkpoint."""
        path = self.root / "data/state/global_market_15m/cboe_vix.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("CBOE_VIX checkpoint is not an object")
        expected_bars = value.get("expected_bars")
        latest_ends = value.get("latest_bar_end_utc")
        scope = value.get("scope_utc")
        if (
            value.get("status") != "PASS"
            or value.get("dataset_id") != MARKET_PRICE_15M_OBSERVATION.name
            or value.get("lane_id") != "CBOE_VIX"
            or value.get("series_ids") != ["^VIX"]
            or not isinstance(expected_bars, dict)
            or set(expected_bars) != {"^VIX"}
            or not isinstance(latest_ends, dict)
            or set(latest_ends) != {"^VIX"}
            or not isinstance(scope, list)
            or len(scope) != 2
        ):
            raise ValueError("CBOE_VIX checkpoint identity differs")
        start, end = (pd.Timestamp(item) for item in scope)
        if any(item.tzinfo is None or item.utcoffset() is None for item in (start, end)):
            raise ValueError("CBOE_VIX checkpoint scope is timezone-naive")
        start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
        expected = pd.date_range(start, end, freq="15min", inclusive="left").tolist()
        completeness = audit_market_15m_bars(latest["bar_start"].tolist(), expected)
        retained_end = pd.Timestamp(latest["bar_end"].max()).tz_convert("UTC")
        checkpoint_end = pd.Timestamp(latest_ends["^VIX"])
        if checkpoint_end.tzinfo is None:
            raise ValueError("CBOE_VIX checkpoint latest bar is timezone-naive")
        if (
            int(expected_bars["^VIX"]) != len(expected)
            or completeness.status != "COMPLETE"
            or retained_end != end
            or checkpoint_end.tz_convert("UTC") != retained_end
        ):
            raise ValueError("CBOE_VIX checkpoint and retained native bars differ")

    def _vix_intraday_metric(
        self, *, now_utc: object | None = None,
    ) -> DashboardMetricView:
        """Prefer one accepted current VIX bar without changing session history."""
        current = _load_yahoo_native15m_current(self.root, "^VIX")
        if current is not None:
            source_time = pd.Timestamp(current.provider_timestamp_utc).tz_convert(
                "Asia/Seoul"
            )
            return DashboardMetricView(
                dataset_id="market_price_15m_current", series_id="^VIX",
                label="VIX intraday (Yahoo ^VIX)", value=float(current.value),
                unit="index points",
                as_of=source_time.strftime("%Y-%m-%d %H:%M KST"),
                expected_as_of=None,
                source="Yahoo ^VIX completed provider-native 15m current projection; not FRED VIXCLS",
                freshness="CURRENT_COMPLETED_15M", pit_status="PIT_BLOCKED",
                pit_label="표시 전용 · Backtest 사용 불가",
                automation_policy="EVERY_30_MIN_CURRENT_ONLY",
                automation_enabled=True,
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=None, route=current.route_id,
                source_timestamp=current.provider_timestamp_utc,
                retrieved_at_utc=current.retrieved_at_utc,
                delay_status="DELAYED_COMPLETED_BAR", completed_bar=True,
                timestamp_basis=current.timestamp_basis.value,
            )
        return self._vix_completed_session_metric(now_utc=now_utc)

    def _vix_completed_session_metric(
        self, *, now_utc: object | None = None,
    ) -> DashboardMetricView:
        """Serve only the independently verified complete VIX session."""
        expected_date: str | None = None
        retained_date: str | None = None
        source_timestamp: str | None = None
        delay_status: str | None = None
        completed_bar: bool | None = None
        try:
            now = pd.Timestamp(now_utc) if now_utc is not None else pd.Timestamp.now(tz="UTC")
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("now_utc must be timezone-aware")
            scope = reviewed_native_scope(now.to_pydatetime(), "CBOE_VIX")
            expected_date = scope.session_date.isoformat()
            frame = self._read_vix_intraday_frame()
            retained_day = frame["market_date"].max().date()
            retained_date = retained_day.isoformat()
            latest = frame.loc[frame["market_date"].dt.date.eq(retained_day)].copy()
            self._verify_vix_intraday_checkpoint(latest)
            last = latest.sort_values("bar_start", kind="stable").iloc[-1]
            bar_end = pd.Timestamp(last["bar_end"]).tz_convert("UTC")
            retrieved_at = pd.Timestamp(last["retrieved_at"]).tz_convert("UTC")
            source_timestamp = bar_end.isoformat()
            delay_status = str(last["data_availability"])
            completed_bar = bool(bar_end <= retrieved_at)
            expected_audit = audit_market_15m_bars(
                latest["bar_start"].tolist(), scope.expected_bar_starts["^VIX"]
            )
            if (
                retained_date != expected_date
                or expected_audit.status != "COMPLETE"
                or not completed_bar
            ):
                return DashboardMetricView(
                    dataset_id=MARKET_PRICE_15M_OBSERVATION.name,
                    series_id="^VIX", label="VIX intraday (Yahoo ^VIX)",
                    value=None, unit="index points", as_of=None,
                    expected_as_of=expected_date,
                    source="Yahoo ^VIX indicative/delayed provider subset; not FRED VIXCLS",
                    freshness="STALE", pit_status="PIT_BLOCKED",
                    pit_label="prediction prohibited",
                    automation_policy="DAILY_NATIVE_15M", automation_enabled=True,
                    display_state=DashboardDisplayState.REFRESH_REQUIRED,
                    unavailable_reason=(
                        "Retained Yahoo ^VIX native bars do not match the expected "
                        f"completed session: retained={retained_date or 'N/A'}, "
                        f"expected={expected_date or 'N/A'}."
                    ),
                    route="NORMALIZED_NATIVE_15M_CBOE_VIX",
                    source_timestamp=source_timestamp,
                    delay_status=delay_status,
                    completed_bar=completed_bar,
                )
            values = latest["close"].astype(float)
            value = float(values.iloc[-1])
            if not np.isfinite(value):
                raise ValueError("Yahoo ^VIX close is not finite")
            change = float(values.diff().iloc[-1]) if len(values) > 1 else None
            previous = float(values.iloc[-2]) if len(values) > 1 else None
            change_pct = change / previous * 100 if change is not None and previous else None
            return DashboardMetricView(
                dataset_id=MARKET_PRICE_15M_OBSERVATION.name,
                series_id="^VIX", label="VIX intraday (Yahoo ^VIX)", value=value,
                unit="index points",
                as_of=bar_end.tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M KST"),
                expected_as_of=expected_date,
                source="Yahoo ^VIX indicative/delayed provider subset; not FRED VIXCLS",
                freshness="CURRENT", pit_status="PIT_BLOCKED",
                pit_label="prediction prohibited",
                automation_policy="DAILY_NATIVE_15M", automation_enabled=True,
                display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
                route="NORMALIZED_NATIVE_15M_CBOE_VIX",
                change=change, change_pct=change_pct,
                source_timestamp=source_timestamp,
                delay_status=delay_status,
                completed_bar=completed_bar,
            )
        except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError,
                PermissionError, TypeError, ValueError):
            return DashboardMetricView(
                dataset_id=MARKET_PRICE_15M_OBSERVATION.name,
                series_id="^VIX", label="VIX intraday (Yahoo ^VIX)", value=None,
                unit="index points", as_of=None, expected_as_of=expected_date,
                source="Yahoo ^VIX indicative/delayed provider subset; not FRED VIXCLS",
                freshness="UNKNOWN", pit_status="PIT_BLOCKED",
                pit_label="prediction prohibited",
                automation_policy="DAILY_NATIVE_15M", automation_enabled=True,
                display_state=DashboardDisplayState.UNAVAILABLE,
                unavailable_reason=(
                    "Contract-valid Yahoo ^VIX native bars and their accepted "
                    "CBOE_VIX checkpoint could not be verified."
                ),
                route="NORMALIZED_NATIVE_15M_CBOE_VIX",
                source_timestamp=source_timestamp,
                delay_status=delay_status,
                completed_bar=completed_bar,
            )

    @staticmethod
    def _unavailable_market_flow(
        market: str,
        *,
        dataset_id: str,
        state: DashboardDisplayState,
        reason: str,
        expected_as_of: str | None = None,
        source: str = "N/A",
        freshness: str = "UNKNOWN",
    ) -> MarketInvestorFlowView:
        labels = (
            ("FOREIGN", "외국인"),
            ("INSTITUTION", "기관"),
            ("INDIVIDUAL", "개인"),
        )
        return MarketInvestorFlowView(
            dataset_id=dataset_id,
            market=market,
            values=tuple(
                MarketInvestorFlowValue(key, label, None, None)
                for key, label in labels
            ),
            as_of=None,
            expected_as_of=expected_as_of,
            value_unit="UNKNOWN",
            source=source,
            source_operation=None,
            provider_segment=None,
            freshness=freshness,
            finality="UNVERIFIED",
            display_state=state,
            unavailable_reason=reason,
            weekly_unavailable_reason=reason,
            covered_sessions=(),
            required_sessions=(),
            missing_sessions=(),
            partial_week=False,
        )

    def market_funding_view(self, health: object) -> MarketFundingView:
        """Expose retained credit/liquidity aggregates with per-value provenance.

        Stale retained values remain visible only with their exact source date and
        freshness label. Missing credit data is explicit and is never inferred
        from a broker snapshot, lending balance, or another liquidity field.
        """
        rows = {item.dataset: item for item in getattr(health, "rows", ())}
        definitions = (
            (
                "CREDIT_FINANCING", "신용융자 잔고", "kr_credit_balance_daily",
                "credit_financing_total", "provider-native",
            ),
            (
                "INVESTOR_DEPOSITS", "투자자 예탁금", "kr_market_liquidity_daily",
                "investor_deposits", "KRW",
            ),
            (
                "RECEIVABLES", "위탁매매 미수금", "kr_market_liquidity_daily",
                "brokerage_receivables", "KRW",
            ),
            (
                "FORCED_SALE", "반대매매 금액", "kr_market_liquidity_daily",
                "forced_sale_amount", "KRW",
            ),
        )
        values: list[MarketFundingValue] = []
        for value_id, label, dataset_id, column, unit in definitions:
            row = rows.get(dataset_id)
            reason: str | None = None
            value: int | float | None = None
            as_of = None
            if row is None:
                source, freshness = "N/A", "UNKNOWN"
                reason = "Health V2 row가 없습니다."
            else:
                source, freshness = str(row.source), str(row.freshness)
                as_of = None if row.latest == "N/A" else str(row.latest)
                runtime_failed = str(
                    getattr(row, "runtime_coverage", "NOT_PROBED")
                ).startswith("FAILED:")
                if row.operational == "BLOCKED" or runtime_failed:
                    reason = "로컬 데이터 계약 또는 운영 상태가 차단되었습니다."
                else:
                    try:
                        frame = self.query.tail(
                            f"normalized/{dataset_id}", rows=2,
                            columns=["date", column],
                        ).sort_values("date")
                        latest = frame.iloc[-1]
                        retained_as_of = self._iso_date(latest["date"])
                        number = pd.to_numeric(latest[column], errors="coerce")
                        if (
                            retained_as_of is None or pd.isna(number)
                            or not np.isfinite(float(number))
                            or (as_of is not None and retained_as_of != as_of)
                        ):
                            raise ValueError("funding row differs from Health")
                        value = int(number) if float(number).is_integer() else float(number)
                        as_of = retained_as_of
                        if freshness not in DISPLAYABLE_FRESHNESS:
                            reason = "최신값이 아닌 보존값입니다."
                    except (IndexError, KeyError, OSError, PermissionError, TypeError, ValueError):
                        value = None
                        reason = "계약에 맞는 로컬 보존값이 없습니다."
            values.append(MarketFundingValue(
                value_id=value_id, label=label, value=value, unit=unit,
                as_of=as_of, source=source, freshness=freshness,
                unavailable_reason=reason,
            ))
        return MarketFundingView(tuple(values))

    def market_investor_flow_views(
        self, health: object,
    ) -> dict[str, MarketInvestorFlowView]:
        """Build separate KOSPI/KOSDAQ daily and week-to-date flow views.

        Weekly sums use only retained XKRX sessions from Monday through the
        accepted latest date.  A missing session or provider/unit boundary
        suppresses the weekly numbers instead of filling or mixing them.
        """
        dataset_id = "kr_market_investor_net_purchase_bridge_daily"
        rows = {item.dataset: item for item in getattr(health, "rows", ())}
        health_row = rows.get(dataset_id)
        runtime_failed = bool(
            health_row
            and getattr(health_row, "runtime_coverage", "NOT_PROBED").startswith("FAILED:")
        )
        if (
            health_row is None
            or health_row.freshness not in DISPLAYABLE_FRESHNESS
            or health_row.operational == "BLOCKED"
            or runtime_failed
        ):
            state = (
                DashboardDisplayState.REFRESH_REQUIRED
                if health_row is not None and health_row.freshness == "STALE"
                else DashboardDisplayState.UNAVAILABLE
            )
            reason = "양 시장의 최신 확정 수급 데이터가 없습니다."
            return {
                market: self._unavailable_market_flow(
                    market,
                    dataset_id=dataset_id,
                    state=state,
                    reason=reason,
                    expected_as_of=getattr(health_row, "expected", None),
                    source=getattr(health_row, "source", "N/A"),
                    freshness=getattr(health_row, "freshness", "UNKNOWN"),
                )
                for market in ("KOSPI", "KOSDAQ")
            }

        retained_date = str(health_row.latest)
        columns = [
            "date", "market", "value_unit",
            "foreign_net_purchase", "institution_net_purchase",
            "individual_net_purchase", "source_provider", "source_operation",
            "provider_segment", "availability_date",
        ]
        value_columns = (
            ("FOREIGN", "외국인", "foreign_net_purchase"),
            ("INSTITUTION", "기관", "institution_net_purchase"),
            ("INDIVIDUAL", "개인", "individual_net_purchase"),
        )
        result: dict[str, MarketInvestorFlowView] = {}
        for market in ("KOSPI", "KOSDAQ"):
            try:
                frame = self.query.tail(
                    "published/kr_market_investor_net_purchase_bridge_daily",
                    rows=8,
                    columns=columns,
                    partitions={"market": market},
                )
                frame = frame.loc[frame["market"].astype(str).eq(market)].copy()
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
                frame = frame.dropna(subset=["date"]).sort_values("date")
                accepted = pd.Timestamp(retained_date).normalize()
                latest_rows = frame.loc[frame["date"].eq(accepted)]
                if len(latest_rows) != 1:
                    raise ValueError("accepted market row is missing or duplicated")
                latest = latest_rows.iloc[0]
                unit = str(latest["value_unit"])
                operation = str(latest["source_operation"])
                provider = str(latest["source_provider"])
                provider_segment = str(latest["provider_segment"])
                if unit != "KRW" or operation != "getMarketIndicatorInvestorTrading":
                    raise ValueError("latest row is outside the accepted KRW daily-final route")

                latest_values: dict[str, int] = {}
                for investor_id, _label, column in value_columns:
                    number = float(latest[column])
                    if not np.isfinite(number) or not number.is_integer():
                        raise ValueError("latest flow value is not a finite KRW integer")
                    latest_values[investor_id] = int(number)

                monday = accepted - timedelta(days=int(accepted.weekday()))
                sunday = monday + timedelta(days=6)
                calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
                all_week_sessions = tuple(
                    pd.Timestamp(day).normalize()
                    for day in calendar.sessions_in_range(monday.date(), sunday.date())
                )
                required = tuple(day for day in all_week_sessions if day <= accepted)
                week = frame.loc[frame["date"].isin(required)].copy()
                covered = tuple(sorted(set(week["date"])))
                missing = tuple(day for day in required if day not in set(covered))
                partial_week = bool(all_week_sessions and accepted < all_week_sessions[-1])

                weekly_reason: str | None = None
                weekly_values: dict[str, int | None] = {
                    investor_id: None for investor_id, _label, _column in value_columns
                }
                if missing:
                    weekly_reason = "이번 주 보존 세션이 누락되어 누계를 표시하지 않습니다."
                elif not required:
                    weekly_reason = "이번 주의 확인된 거래 세션이 없습니다."
                else:
                    boundary_columns = (
                        "value_unit", "source_provider", "source_operation", "provider_segment",
                    )
                    if any(week[column].astype(str).nunique(dropna=False) != 1 for column in boundary_columns):
                        weekly_reason = "이번 주에 단위 또는 공급자 경계가 달라 누계를 표시하지 않습니다."
                    elif (
                        str(week.iloc[0]["value_unit"]) != unit
                        or str(week.iloc[0]["source_provider"]) != provider
                        or str(week.iloc[0]["source_operation"]) != operation
                        or str(week.iloc[0]["provider_segment"]) != provider_segment
                    ):
                        weekly_reason = "최신 행과 주간 행의 공급자 경계가 달라 누계를 표시하지 않습니다."
                    else:
                        for investor_id, _label, column in value_columns:
                            numbers = pd.to_numeric(week[column], errors="coerce")
                            if numbers.isna().any() or not np.isfinite(numbers.to_numpy(dtype=float)).all():
                                weekly_reason = "이번 주 수급 값이 유효하지 않아 누계를 표시하지 않습니다."
                                weekly_values = {
                                    key: None for key, _label, _column in value_columns
                                }
                                break
                            weekly_values[investor_id] = int(numbers.sum())

                result[market] = MarketInvestorFlowView(
                    dataset_id=dataset_id,
                    market=market,
                    values=tuple(
                        MarketInvestorFlowValue(
                            investor_id=investor_id,
                            label=label,
                            latest_value=latest_values[investor_id],
                            week_to_date_value=weekly_values[investor_id],
                        )
                        for investor_id, label, _column in value_columns
                    ),
                    as_of=accepted.date().isoformat(),
                    expected_as_of=str(health_row.expected),
                    value_unit=unit,
                    source=provider,
                    source_operation=operation,
                    provider_segment=provider_segment,
                    freshness=str(health_row.freshness),
                    finality="DAILY_FINAL",
                    display_state=DashboardDisplayState.VALUE,
                    unavailable_reason=None,
                    weekly_unavailable_reason=weekly_reason,
                    covered_sessions=tuple(day.date().isoformat() for day in covered),
                    required_sessions=tuple(day.date().isoformat() for day in required),
                    missing_sessions=tuple(day.date().isoformat() for day in missing),
                    partial_week=partial_week,
                )
            except (IndexError, KeyError, OSError, PermissionError, TypeError, ValueError):
                result[market] = self._unavailable_market_flow(
                    market,
                    dataset_id=dataset_id,
                    state=DashboardDisplayState.UNAVAILABLE,
                    reason="최신 KRW 일일 확정 수급 행의 계약을 검증할 수 없습니다.",
                    expected_as_of=str(health_row.expected),
                    source=str(health_row.source),
                    freshness=str(health_row.freshness),
                )
        return result

    def kospi_investor_flow_metrics(
        self,
        health: object,
        kospi: DashboardMetricView,
    ) -> dict[str, DashboardMetricView]:
        dataset_id = "kr_market_investor_net_purchase_bridge_daily"
        rows = {row.dataset: row for row in getattr(health, "rows", ())}
        row = rows.get(dataset_id)
        labels = {
            "FOREIGN": ("외국인 순매수", "foreign_net_purchase"),
            "INSTITUTION": ("기관 순매수", "institution_net_purchase"),
            "INDIVIDUAL": ("개인 순매수", "individual_net_purchase"),
        }
        runtime_failed = bool(
            row and getattr(row, "runtime_coverage", "NOT_PROBED").startswith("FAILED:")
        )
        if (
            row is None or row.freshness not in DISPLAYABLE_FRESHNESS
            or row.operational == "BLOCKED" or runtime_failed
        ):
            state = DashboardDisplayState.REFRESH_REQUIRED if row and row.freshness == "STALE" else DashboardDisplayState.UNAVAILABLE
            return {
                key: self._unavailable_metric(
                    f"KOSPI_{key}_FLOW", label, state,
                    "KOSPI 가격과 동일한 최신 market_date의 수급 데이터가 없습니다.",
                    dataset_id=dataset_id,
                )
                for key, (label, _column) in labels.items()
            }
        try:
            frame = self.query.tail(
                "published/kr_market_investor_net_purchase_bridge_daily", rows=2,
                columns=["date", "market", "value_unit", *(column for _label, column in labels.values())],
                partitions={"market": "KOSPI"},
            )
            frame = frame[frame["market"].astype(str).eq("KOSPI")].sort_values("date")
            latest = frame.iloc[-1]
            flow_date = self._iso_date(latest.get("date"))
        except (IndexError, KeyError, OSError, PermissionError, TypeError, ValueError):
            frame = pd.DataFrame()
            latest = {}
            flow_date = None
        if not kospi.displays_value or flow_date is None or flow_date != kospi.as_of:
            return {
                key: self._unavailable_metric(
                    f"KOSPI_{key}_FLOW", label, DashboardDisplayState.REFRESH_REQUIRED,
                    f"가격 기준일 {kospi.as_of or 'N/A'}와 수급 기준일 {flow_date or 'N/A'}가 일치하지 않습니다.",
                    dataset_id=dataset_id,
                )
                for key, (label, _column) in labels.items()
            }
        return {
            key: DashboardMetricView(
                dataset_id=dataset_id, series_id=f"KOSPI_{key}_FLOW", label=label,
                value=_to_float(latest.get(column)), unit=str(latest.get("value_unit") or "UNKNOWN"),
                as_of=flow_date, expected_as_of=row.expected, source=row.source,
                freshness=row.freshness, pit_status=row.pit, pit_label=_pit_label(row.pit),
                automation_policy=row.automation.split(" / ", 1)[0],
                automation_enabled=row.automation.endswith("ENABLED"),
                display_state=DashboardDisplayState.VALUE,
                unavailable_reason=None, route="PUBLISHED_EXACT_DATE_JOIN",
            )
            for key, (label, column) in labels.items()
        }

    def _guarded_metric(
        self,
        rows: dict,
        *,
        dataset_id: str,
        series_id: str,
        label: str,
        unit: str,
        reader,
        route: str,
    ) -> DashboardMetricView:
        row = rows.get(dataset_id)
        if row is None:
            return self._unavailable_metric(
                series_id, label, DashboardDisplayState.UNAVAILABLE,
                "Health V2 row를 읽을 수 없습니다.", dataset_id=dataset_id,
            )
        if row.operational == "BLOCKED":
            return self._metric_without_value(
                row, series_id, label, unit, DashboardDisplayState.PROHIBITED,
                row.blocker if row.blocker != "N/A" else "운영 차단 상태입니다.", route,
            )
        if getattr(row, "runtime_coverage", "NOT_PROBED").startswith("FAILED:"):
            return self._metric_without_value(
                row, series_id, label, unit, DashboardDisplayState.UNAVAILABLE,
                "런타임 로컬 데이터 계약 검증에 실패했습니다.", route,
            )
        if row.freshness not in DISPLAYABLE_FRESHNESS:
            state = DashboardDisplayState.REFRESH_REQUIRED if row.freshness == "STALE" else DashboardDisplayState.UNAVAILABLE
            reason = "데이터 갱신 필요" if state is DashboardDisplayState.REFRESH_REQUIRED else "현재 표시 불가"
            return self._metric_without_value(row, series_id, label, unit, state, reason, route)
        try:
            value, as_of, change, change_pct = reader()
        except (IndexError, KeyError, OSError, PermissionError, TypeError, ValueError):
            value, as_of, change, change_pct = None, None, None, None
        if value is None or as_of is None or (row.latest != "N/A" and as_of != row.latest):
            return self._metric_without_value(
                row, series_id, label, unit, DashboardDisplayState.UNAVAILABLE,
                "로컬 파일 누락·스키마 오류 또는 Health 기준일 불일치", route,
            )
        return DashboardMetricView(
            dataset_id=dataset_id, series_id=series_id, label=label,
            value=value, unit=unit, as_of=as_of, expected_as_of=row.expected,
            source=row.source, freshness=row.freshness, pit_status=row.pit,
            pit_label=_pit_label(row.pit), automation_policy=row.automation.split(" / ", 1)[0],
            automation_enabled=row.automation.endswith("ENABLED"),
            display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
            route=route, change=change, change_pct=change_pct,
        )

    @staticmethod
    def _metric_without_value(row, series_id, label, unit, state, reason, route) -> DashboardMetricView:
        return DashboardMetricView(
            dataset_id=row.dataset, series_id=series_id, label=label,
            value=None, unit=unit, as_of=row.latest if row.latest != "N/A" else None,
            expected_as_of=row.expected if row.expected != "N/A" else None,
            source=row.source, freshness=row.freshness, pit_status=row.pit,
            pit_label=_pit_label(row.pit), automation_policy=row.automation.split(" / ", 1)[0],
            automation_enabled=row.automation.endswith("ENABLED"), display_state=state,
            unavailable_reason=reason, route=route,
        )

    @staticmethod
    def _unavailable_metric(
        series_id: str,
        label: str,
        state: DashboardDisplayState,
        reason: str,
        *,
        dataset_id: str | None = None,
    ) -> DashboardMetricView:
        return DashboardMetricView(
            dataset_id=dataset_id, series_id=series_id, label=label, value=None,
            unit="N/A", as_of=None, expected_as_of=None, source="local persisted data",
            freshness="UNKNOWN", pit_status="NON_PREDICTIVE", pit_label="예측 사용 불가",
            automation_policy="NOT_APPLICABLE", automation_enabled=False,
            display_state=state, unavailable_reason=reason, route="NOT_AVAILABLE",
        )

    def _read_asset_metric(
        self, asset: str, *, as_of: object | None = None,
    ) -> tuple[float | None, str | None, float | None, float | None]:
        snapshot = self.index.asset_snapshot(asset, as_of=as_of)
        return (
            _to_float(snapshot.get("value")), self._iso_date(snapshot.get("date")),
            _to_float(snapshot.get("change")), _to_float(snapshot.get("change_pct")),
        )

    def _read_latest_metric(
        self, dataset: str, date_column: str, value_column: str,
    ) -> tuple[float | None, str | None, float | None, float | None]:
        frame = self.query.tail(dataset, rows=3, columns=[date_column, value_column])
        if frame.empty or value_column not in frame:
            return None, None, None, None
        frame = frame.sort_values(date_column)
        values = pd.to_numeric(frame[value_column], errors="coerce")
        valid = frame.loc[values.notna(), [date_column]].copy()
        valid["value"] = values.dropna().to_numpy()
        if valid.empty:
            return None, None, None, None
        latest = valid.iloc[-1]
        change = float(valid["value"].diff().iloc[-1]) if len(valid) > 1 else None
        previous = float(valid["value"].iloc[-2]) if len(valid) > 1 else None
        change_pct = change / previous * 100 if change is not None and previous else None
        return float(latest["value"]), self._iso_date(latest[date_column]), change, change_pct

    @staticmethod
    def _iso_date(value: object) -> str | None:
        parsed = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(parsed) else pd.Timestamp(parsed).date().isoformat()

    def set_health_report(self, report: object | None) -> None:
        """Attach the already-created DailyHealthReport without recomputing it."""
        self.health_report = report

    def market_cards(self, health: object | None = None) -> list[dict]:
        cards = [self.index.asset_snapshot(asset) for asset in DASHBOARD_ASSETS]
        # Freshness is owned by the retained health artifact, not by wall-clock
        # date arithmetic in the GUI process.
        from stock_data.gui.health_service import DailyHealthArtifactService
        health = health or DailyHealthArtifactService(self.root).load()
        health_by_dataset = {row.dataset: row for row in health.rows}
        for card in cards:
            dataset = ASSET_HEALTH_DATASETS.get(str(card.get("asset")))
            row = health_by_dataset.get(dataset)
            if row is None:
                # A missing or malformed health artifact is not permission to
                # present an old retained value as current.
                card.update(status="UNKNOWN", pit="UNKNOWN", expected="N/A")
                continue
            card.update(
                status=row.freshness,
                pit=row.pit,
                expected=row.expected,
                source=row.source,
            )
        return cards

    def chart_series(self, asset: str, period: str = "120D") -> pd.DataFrame:
        """Read domestic chart data only through its verified Health date."""
        if asset not in {"KOSPI", "KOSDAQ"}:
            frame = self.index.asset_series(asset, period)
            definition = DASHBOARD_ASSETS[asset]
            dataset_id = (
                "global_etf_price_daily" if definition["kind"] == "etf" else
                "global_commodity_futures_daily" if definition["kind"] == "futures" else
                "global_index_price_daily"
            )
            return _attach_dashboard_chart_coverage(
                frame,
                period=period,
                dataset_id=dataset_id,
                series_id=str(definition["symbol"]),
            )
        from stock_data.gui.health_service import DailyHealthArtifactService

        health = DailyHealthArtifactService(self.root).load()
        view = self.index.chart_view(asset, period, health=health)
        if not view.displays_values:
            return pd.DataFrame()
        return _attach_dashboard_chart_coverage(
            view.frame,
            period=period,
            dataset_id=view.dataset_id,
            series_id=asset,
        )

    def data_health(
        self, cards: list[dict] | None = None, *, health: object | None = None,
    ) -> dict:
        if health is not None:
            from stock_data.gui.health_service import summarize_health_artifact
            return summarize_health_artifact(health)
        report = self.health_report
        if report is not None:
            dimensions = (report.dimension_summary() if callable(getattr(report, "dimension_summary", None)) else {})
            return {"overall": getattr(getattr(report, "overall_status", None), "value", getattr(report, "overall_status", "UNKNOWN")),
                    "current": int(getattr(report, "current_count", 0)),
                    "expected_lag": int(getattr(report, "expected_lag_count", 0)),
                    "stale": int(getattr(report, "stale_count", 0)),
                    "operational_blocked": int(getattr(report, "operational_blocked_count", 0)),
                    "predictive_blocked": int(getattr(report, "predictive_blocked_count", 0)),
                    "research_only": int(getattr(report, "research_only_count", 0)),
                    "failed": int(getattr(report, "failed_count", 0)),
                    "dimensions": dimensions,
                    "source": "DailyHealthReport"}
        cards = cards or self.market_cards()
        statuses = [str(item.get("status")) for item in cards]
        return {"overall": "DEGRADED" if any(s != "CURRENT" for s in statuses) else "CURRENT",
                "current": statuses.count("CURRENT"), "expected_lag": statuses.count("EXPECTED_LAG"),
                "stale": statuses.count("STALE"), "operational_blocked": statuses.count("BLOCKED"),
                "predictive_blocked": 0, "research_only": statuses.count("RESEARCH_ONLY"),
                "failed": 0, "source": "retained local data (fallback summary)"}

    def sections(self) -> dict:
        """Compatibility payload built only from typed, Health-gated metrics."""
        metrics = self.dashboard_metrics()
        return {
            "fx": {
                "USD/KRW": metrics["USD_KRW"],
                "USD/JPY": metrics["USD_JPY"],
                "JPY/KRW": None,
            },
            "rates": {
                "US Treasury 2Y": metrics["UST2"],
                "US Treasury 10Y": metrics["UST10"],
                "US Treasury 30Y": metrics["UST30"],
                "10Y-2Y": metrics["UST10_2_SPREAD"],
            },
            "volatility": {"VIX": metrics["VIX"], "VKOSPI": metrics["VKOSPI"]},
            "commodities": {"Gold": metrics["GOLD"], "WTI": metrics["WTI"]},
        }

    def _global_risk(self) -> dict:
        """Compatibility view with no independent or Raw fallback reads."""
        metrics = self.dashboard_metrics()
        return {
            label: metrics[key]
            for label, key in (
                ("USD/KRW", "USD_KRW"), ("US 10Y", "UST10"),
                ("S&P 500", "SP500"), ("NASDAQ", "NASDAQ"),
                ("Gold", "GOLD"), ("WTI", "WTI"), ("VIX", "VIX"),
            )
        }

    def volatility(self, days: int = 250) -> dict[str, dict]:
        """Serve VIX and VKOSPI independently; never substitute one for the other."""
        definitions = {
            "VIX": ("normalized/fred_vix_daily", "date", "vixcls", "FRED VIXCLS", "PIT_LIMITED"),
            "VKOSPI": ("normalized/kr_vkospi_daily", "market_date", "close", "KRX MDCSTAT01201 / 1300", "PIT_LIMITED"),
        }
        output = {}
        for name, (dataset, date_column, column, source, status) in definitions.items():
            # FRED may retain valid blank holiday observations.  Read bounded
            # headroom so a "250d" percentile is based on 250 valid sessions,
            # rather than silently shrinking the sample after null removal.
            frame = self.query.tail(
                dataset, rows=max(days + 64, 251), columns=[date_column, column]
            )
            if frame.empty or column not in frame:
                output[name] = {"value": None, "date": None, "market_date": None, "change_1d": None, "change_1d_pct": None, "percentile_20d": None, "percentile_60d": None, "percentile_250d": None, "source": source, "route": "N/A", "freshness": "DATA_MISSING", "status": "DATA_MISSING" if name == "VKOSPI" else "NOT_COLLECTED"}
                continue
            values = pd.to_numeric(frame[column], errors="coerce")
            valid = frame.loc[values.notna(), [date_column]].copy().rename(columns={date_column:"date"}); valid["value"] = values.dropna().to_numpy()
            latest = valid.iloc[-1]
            def percentile(window: int) -> float | None:
                sample = valid["value"].tail(window)
                return float(sample.rank(pct=True).iloc[-1] * 100) if len(sample) else None
            change=float(valid.value.diff().iloc[-1]) if len(valid)>1 else None
            output[name] = {"value": float(latest.value), "date": latest.date, "market_date": latest.date, "change_1d": change, "change_1d_pct": (change/float(valid.value.iloc[-2])*100 if change is not None and valid.value.iloc[-2] else None), "percentile_20d": percentile(20), "percentile_60d": percentile(60), "percentile_250d": percentile(250), "source": source, "route": "LATEST_FINAL_DAILY", "freshness": f"AS_OF_{pd.Timestamp(latest.date).date().isoformat()}", "status": status}
        return output

    def _commodity_raw_latest(self, symbol: str) -> dict:
        calls = sorted((self.root / "data/landing/yahoo/global_commodity_futures_daily").rglob("call.json"))
        for call_path in reversed(calls):
            try:
                call = json.loads(call_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if call.get("request_parameters", {}).get("symbol") != symbol:
                continue
            body_file = call.get("landing_body_file")
            if not isinstance(body_file, str) or not body_file:
                continue
            try:
                body = json.loads((call_path.parent / body_file).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            result = body.get("chart", {}).get("result", [None])[0]
            if not result:
                break
            timestamps = result.get("timestamp", [])
            closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            valid = [(ts, close) for ts, close in zip(timestamps, closes) if close is not None]
            if valid:
                ts, close = valid[-1]
                return {"value": close, "date": pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Seoul").date(), "source": f"Yahoo {symbol} provider Raw", "route": "PROVIDER_RAW_VIEW", "status": "NORMALIZED_REVIEW_REQUIRED"}
        return {"value": None, "date": None, "source": f"Yahoo {symbol}", "route": "N/A", "status": "RAW_MISSING"}
