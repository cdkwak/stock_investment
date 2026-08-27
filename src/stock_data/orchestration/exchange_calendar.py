from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from importlib.metadata import version
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd


class ExchangeMarket(StrEnum):
    KR = "KR"
    US = "US"


class MarketVenue(StrEnum):
    """Stable venue identities for the shared session boundary."""

    XKRX_CASH = "XKRX_CASH"
    XNYS_CASH = "XNYS_CASH"
    CBOE_SPOT_VIX = "CBOE_SPOT_VIX"
    CME_EQUITY_INDEX_FUTURES = "CME_EQUITY_INDEX_FUTURES"
    NYMEX_WTI_FUTURES = "NYMEX_WTI_FUTURES"
    CFE_VIX_FUTURES = "CFE_VIX_FUTURES"


class SessionRuleStatus(StrEnum):
    ACTIVE_REGULAR_ONLY = "ACTIVE_REGULAR_ONLY"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"


class SessionState(StrEnum):
    REGULAR = "REGULAR"
    BREAK = "BREAK"
    CLOSED = "CLOSED"


class UnsupportedMarketSessionError(LookupError):
    """Raised before use when the repository has no accepted session rule."""


class MarketSessionVersionMismatchError(RuntimeError):
    """Raised when a persisted decision requests another calendar version."""


@dataclass(frozen=True)
class MarketSessionContract:
    venue: MarketVenue
    status: SessionRuleStatus
    calendar_name: str | None
    exchange_timezone: str | None
    observation_timezone: str | None
    policy_version: int
    source_package: str | None
    source_version: str | None
    effective_from: date | None
    effective_to: date | None
    regular_session_label: str | None
    extended_session_policy: str
    maintenance_policy: str
    holiday_policy: str
    early_close_policy: str
    trade_date_policy: str
    completed_bar_policy: str
    evidence: tuple[str, ...]
    unresolved_rules: tuple[str, ...] = ()
    ui_anchor_is_exchange_boundary: bool = False
    expiration_policy: str = "NOT_APPLICABLE_OR_UNRESOLVED"
    provider_mapping_policy: str = "CALLER_SPECIFIC_FAIL_CLOSED"

    @property
    def version_key(self) -> str:
        source = (
            f"{self.source_package}={self.source_version}"
            if self.source_package and self.source_version
            else "source=unresolved"
        )
        effective = (
            f"{self.effective_from.isoformat() if self.effective_from else 'unresolved'}"
            f"..{self.effective_to.isoformat() if self.effective_to else 'open'}"
        )
        return f"market-session-v{self.policy_version}|{source}|{effective}"


@dataclass(frozen=True)
class SessionBreak:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class MarketSessionWindow:
    venue: MarketVenue
    trade_date: date
    label: str
    open: datetime
    close: datetime
    breaks: tuple[SessionBreak, ...]
    is_early_close: bool
    contract_version: str


@dataclass(frozen=True)
class CompletedBarWindow:
    venue: MarketVenue
    trade_date: date
    interval: timedelta
    first_start: datetime | None
    last_start: datetime | None
    last_end: datetime | None
    count: int
    contract_version: str


@dataclass(frozen=True)
class CalendarProvenance:
    market: ExchangeMarket
    calendar_name: str
    timezone: str
    source_package: str
    source_version: str


_MARKET_VENUES = {
    ExchangeMarket.KR: MarketVenue.XKRX_CASH,
    ExchangeMarket.US: MarketVenue.XNYS_CASH,
}


_SHARED_VENUES = {
    MarketVenue.XKRX_CASH: ("XKRX", "Asia/Seoul", "Asia/Seoul"),
    MarketVenue.XNYS_CASH: ("XNYS", "America/New_York", "America/New_York"),
}

# ``exchange-calendars==4.13.2`` predates these KRX one-off 2026 closures.
# Keep them as an explicit, evidence-backed overlay instead of treating the
# absent observations as provider gaps.
_OFFICIAL_ONE_OFF_CLOSURES: dict[MarketVenue, frozenset[date]] = {
    MarketVenue.XKRX_CASH: frozenset(
        {
            date(2026, 6, 3),   # nationwide local-election day
            date(2026, 7, 17),  # Constitution Day temporary market holiday
        }
    ),
}

_XKRX_2026_CLOSURE_EVIDENCE = (
    "https://kind.krx.co.kr/external/2026/05/20/000110/20260520000197/32154.htm",
    "https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp",
)

_OFFICIAL_EVIDENCE_REQUIRED = {
    MarketVenue.CME_EQUITY_INDEX_FUTURES: {
        "exchange_timezone": "America/Chicago",
        "observation_timezone": None,
        "policy_version": 2,
        "effective_from": None,
        "effective_to": None,
        "regular_session_label": "GLOBEX_NORMAL_1700_PREVIOUS_DAY_TO_1600_CT",
        "extended_session_policy": "NO_ACCEPTED_NQ_RTH_ETH_SPLIT",
        "maintenance_policy": "DAILY_CLOSED_1600_1700_CT; CURRENT_NQ_INTRADAY_HALT_REQUIRES_EXACT_SCHEDULE",
        "holiday_policy": "CME_DYNAMIC_PRODUCT_SCHEDULE_FINALIZED_NEAR_EACH_HOLIDAY",
        "early_close_policy": "CME_DYNAMIC_PRODUCT_SCHEDULE_FINALIZED_NEAR_EACH_HOLIDAY",
        "trade_date_policy": "NQ_TRADING_DAY_GENERALLY_STARTS_1700_CT_PREVIOUS_EVENING",
        "completed_bar_policy": "YAHOO_CONTINUOUS_TO_LISTED_NQ_MAPPING_UNRESOLVED",
        "expiration_policy": "LISTED_NQ_TERMINATES_AT_NASDAQ_REGULAR_OPEN_ON_FINAL_SETTLEMENT_BUSINESS_DAY; FINAL_SETTLEMENT_NORMALLY_THIRD_FRIDAY",
        "provider_mapping_policy": "YAHOO_NQ_CONTINUOUS_TO_LISTED_CME_NQ_UNVERIFIED",
        "evidence": (
            "https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.contractSpecs.html",
            "https://www.cmegroup.com/content/dam/cmegroup/rulebook/CME/IV/350/359/359.pdf",
            "https://www.cmegroup.com/trading/equity-index/futures-and-etfs-myths-vs-facts.html",
            "https://www.cmegroup.com/content/dam/cmegroup/globex/files/GlobexRefGd.pdf",
            "https://www.cmegroup.com/trading-hours.html",
        ),
        "unresolved_rules": (
            "current_exact_nq_intraday_halt_schedule",
            "exact_date_product_holiday_and_early_close_schedule",
            "yahoo_continuous_front_month_selection_and_roll",
            "yahoo_continuous_symbol_to_exchange_trade_date",
            "completed_bar_boundary",
        ),
    },
    MarketVenue.NYMEX_WTI_FUTURES: {
        "exchange_timezone": "America/Chicago",
        "observation_timezone": None,
        "policy_version": 2,
        "effective_from": None,
        "effective_to": None,
        "regular_session_label": "CME_GLOBEX",
        "extended_session_policy": "NO_RTH_ETH_SPLIT_1700_PREVIOUS_DAY_TO_1600_CT",
        "maintenance_policy": "DAILY_CLOSED_1600_1700_CT",
        "holiday_policy": "CME_DYNAMIC_PRODUCT_SCHEDULE_FINALIZED_NEAR_EACH_HOLIDAY",
        "early_close_policy": "CME_DYNAMIC_PRODUCT_SCHEDULE_FINALIZED_NEAR_EACH_HOLIDAY",
        "trade_date_policy": "GLOBEX_EVENING_STARTS_NEXT_TRADE_DATE_GENERAL",
        "completed_bar_policy": "YAHOO_CONTINUOUS_TO_LISTED_CL_MAPPING_UNRESOLVED",
        "expiration_policy": "LISTED_CL_TERMINATES_THIRD_BUSINESS_DAY_BEFORE_25TH_CALENDAR_DAY_OF_MONTH_PRECEDING_DELIVERY; NONBUSINESS_25TH_USES_LAST_PRIOR_BUSINESS_DAY; LISTED_DATE_SURVIVES_LATER_HOLIDAY_CHANGE",
        "provider_mapping_policy": "YAHOO_CL_CONTINUOUS_TO_LISTED_NYMEX_CL_UNVERIFIED",
        "evidence": (
            "https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.contractSpecs.html",
            "https://www.cmegroup.com/rulebook/NYMEX/2/200.pdf",
            "https://www.cmegroup.com/content/dam/cmegroup/globex/files/GlobexRefGd.pdf",
            "https://www.cmegroup.com/trading-hours.html",
        ),
        "unresolved_rules": (
            "exact_date_product_holiday_and_early_close_schedule",
            "yahoo_continuous_front_month_selection_and_roll",
            "yahoo_continuous_symbol_to_exchange_trade_date",
            "completed_bar_boundary",
        ),
    },
    MarketVenue.CFE_VIX_FUTURES: {
        "exchange_timezone": "America/Chicago",
        "observation_timezone": None,
        "policy_version": 2,
        "effective_from": date(2026, 1, 1),
        "effective_to": date(2026, 12, 31),
        "regular_session_label": "RTH_0830_1500_CT",
        "extended_session_policy": "ETH_1700_PREVIOUS_DAY_0830_CT_AND_1500_1600_CT",
        "maintenance_policy": "DAILY_CLOSED_1600_1700_CT_NOT_LABELLED_MAINTENANCE",
        "holiday_policy": "CFE_OFFICIAL_2026_FUTURES_HOURS_TABLE",
        "early_close_policy": "RTH_0830_1215_CT_20261127_AND_20261224; EXPIRING_VX_CLOSE_0800_CT",
        "trade_date_policy": "PREVIOUS_DAY_1700_ETH_BELONGS_NAMED_MON_FRI_SESSION",
        "completed_bar_policy": "NO_CURRENT_PROVIDER_ROUTE",
        "expiration_policy": "GENERALLY_WEDNESDAY; HOLIDAY_ADJUSTED_TO_PRIOR_BUSINESS_DAY; EXPIRING_VX_CLOSE_0800_CT",
        "provider_mapping_policy": "INTENTIONALLY_UNAVAILABLE_NO_REPOSITORY_PROVIDER_ROUTE",
        "evidence": (
            "https://www.cboe.com/tradable-products/vix/vix-futures/specifications",
            "https://www.cboe.com/about/hours/us-futures",
        ),
        "unresolved_rules": (
            "no_current_repository_provider_route",
            "future_year_holiday_version",
            "completed_bar_boundary",
        ),
    },
    MarketVenue.CBOE_SPOT_VIX: {
        "exchange_timezone": "America/Chicago",
        "observation_timezone": "America/Chicago",
        "policy_version": 3,
        "effective_from": date(2026, 2, 26),
        "effective_to": date(2026, 12, 31),
        "regular_session_label": "RTH_CALCULATION_0831_1515_CT",
        "extended_session_policy": "GTH_CALCULATION_0215_0825_CT",
        "maintenance_policy": "NO_CALCULATION_0825_0831_CT_NOT_EXCHANGE_MAINTENANCE",
        "holiday_policy": "CBOE_2026_OPTIONS_INPUT_SCHEDULE_REQUIRES_INDEX_CONFIRMATION",
        "early_close_policy": "SPOT_VIX_CALCULATION_END_ON_EARLY_CLOSE_UNRESOLVED",
        "trade_date_policy": "INDEX_OBSERVATION_DATE_MAPPING_UNRESOLVED",
        "completed_bar_policy": "OFFICIAL_15_SECOND_VALUES_NOT_YAHOO_15M_BARS",
        "expiration_policy": "NOT_A_FUTURES_CONTRACT; VIX_DERIVATIVE_SOQ_IS_DISTINCT_FROM_SPOT_INDEX",
        "provider_mapping_policy": "YAHOO_VIX_IS_PROVIDER_SUBSET_NOT_OFFICIAL_15_SECOND_SERIES",
        "evidence": (
            "https://www.cboe.com/tradable-products/vix/vix-options/specifications",
            "https://www.cboe.com/about/hours/us-options",
            "https://cdn.cboe.com/resources/indices/Volatility_Index_Methodology_Cboe_Volatility_Index.pdf",
            "market_15m_v2:YAHOO_CBOE_XNYS_ALIGNED_PROVIDER_SUBSET",
        ),
        "unresolved_rules": (
            "spot_index_holiday_and_early_close_calculation_window",
            "official_index_observation_trade_date_mapping",
            "yahoo_15m_to_official_15_second_dissemination_mapping",
            "completed_bar_boundary",
        ),
    },
}


def market_session_contract(venue: MarketVenue | str) -> MarketSessionContract:
    """Return versioned session policy without silently filling evidence gaps."""
    selected = MarketVenue(venue)
    if selected in _OFFICIAL_EVIDENCE_REQUIRED:
        policy = _OFFICIAL_EVIDENCE_REQUIRED[selected]
        return MarketSessionContract(
            venue=selected,
            status=SessionRuleStatus.EVIDENCE_REQUIRED,
            calendar_name=None,
            exchange_timezone=policy["exchange_timezone"],
            observation_timezone=policy["observation_timezone"],
            policy_version=policy["policy_version"],
            source_package="official_venue_documentation",
            source_version="bounded-2026-08-20",
            effective_from=policy["effective_from"],
            effective_to=policy["effective_to"],
            regular_session_label=policy["regular_session_label"],
            extended_session_policy=policy["extended_session_policy"],
            maintenance_policy=policy["maintenance_policy"],
            holiday_policy=policy["holiday_policy"],
            early_close_policy=policy["early_close_policy"],
            trade_date_policy=policy["trade_date_policy"],
            completed_bar_policy=policy["completed_bar_policy"],
            evidence=policy["evidence"],
            unresolved_rules=policy["unresolved_rules"],
            expiration_policy=policy["expiration_policy"],
            provider_mapping_policy=policy["provider_mapping_policy"],
        )

    calendar_name, exchange_timezone, observation_timezone = _SHARED_VENUES[selected]
    calendar = xcals.get_calendar(calendar_name)
    source_version = version("exchange-calendars")
    evidence = [f"exchange-calendars:{calendar_name}:{source_version}"]
    policy_version = 1
    holiday_policy = f"{calendar_name}_VERSIONED_SESSION_INDEX"
    if selected is MarketVenue.XKRX_CASH:
        policy_version = 2
        holiday_policy = (
            "XKRX_VERSIONED_SESSION_INDEX_PLUS_OFFICIAL_2026_ONE_OFF_CLOSURES"
        )
        evidence.extend(_XKRX_2026_CLOSURE_EVIDENCE)
    return MarketSessionContract(
        venue=selected,
        status=SessionRuleStatus.ACTIVE_REGULAR_ONLY,
        calendar_name=calendar_name,
        exchange_timezone=exchange_timezone,
        observation_timezone=observation_timezone,
        policy_version=policy_version,
        source_package="exchange-calendars",
        source_version=source_version,
        effective_from=calendar.first_session.date(),
        effective_to=calendar.last_session.date(),
        regular_session_label="REGULAR",
        extended_session_policy="NOT_OWNED_FAIL_CLOSED",
        maintenance_policy="NO_MAINTENANCE_RULE_OUTSIDE_REGULAR_SESSION",
        holiday_policy=holiday_policy,
        early_close_policy=f"{calendar_name}_VERSIONED_SCHEDULE_CLOSE",
        trade_date_policy=f"{calendar_name}_SESSION_LABEL",
        completed_bar_policy="OPEN_INCLUSIVE_END_LE_SESSION_CLOSE_AND_AS_OF",
        evidence=tuple(evidence),
        expiration_policy="NOT_OWNED_BY_CASH_SESSION_SERVICE",
        provider_mapping_policy="EXCHANGE_CALENDAR_ONLY_CALLER_RETAINS_PROVIDER_IDENTITY",
    )


class MarketSessionService:
    """Shared, deterministic regular-session service for accepted venue rules.

    It intentionally does not treat a Dashboard display anchor as an exchange
    boundary.  Futures venues remain unavailable until their own evidence is
    accepted rather than borrowing XNYS cash hours.
    """

    def __init__(
        self,
        venue: MarketVenue | str,
        *,
        required_contract_version: str | None = None,
    ) -> None:
        self.contract = market_session_contract(venue)
        if self.contract.status is not SessionRuleStatus.ACTIVE_REGULAR_ONLY:
            missing = ", ".join(self.contract.unresolved_rules)
            raise UnsupportedMarketSessionError(
                f"{self.contract.venue.value} session rule is unresolved: {missing}"
            )
        if (
            required_contract_version is not None
            and required_contract_version != self.contract.version_key
        ):
            raise MarketSessionVersionMismatchError(
                "calendar contract differs from the persisted eligibility decision: "
                f"required={required_contract_version}; "
                f"available={self.contract.version_key}"
            )
        assert self.contract.calendar_name is not None
        self._calendar = xcals.get_calendar(self.contract.calendar_name)

    @staticmethod
    def _label(value: date) -> pd.Timestamp:
        return pd.Timestamp(value.isoformat())

    @staticmethod
    def _aware(value: datetime, *, field: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware")
        return value.astimezone(timezone.utc)

    def is_session(self, value: date) -> bool:
        if value in _OFFICIAL_ONE_OFF_CLOSURES.get(self.contract.venue, frozenset()):
            return False
        return bool(self._calendar.is_session(self._label(value)))

    def sessions_in_range(self, start: date, end: date) -> tuple[date, ...]:
        if end < start:
            raise ValueError("end must not precede start")
        sessions = tuple(
            stamp.date()
            for stamp in self._calendar.sessions_in_range(
                self._label(start), self._label(end)
            )
        )
        closures = _OFFICIAL_ONE_OFF_CLOSURES.get(self.contract.venue, frozenset())
        return tuple(value for value in sessions if value not in closures)

    def previous_trade_date(self, value: date) -> date:
        sessions = self.sessions_in_range(
            value - timedelta(days=14), value - timedelta(days=1)
        )
        if not sessions:
            raise LookupError(
                f"no prior {self.contract.venue.value} session found near {value}"
            )
        return sessions[-1]

    def next_trade_date(self, value: date) -> date:
        sessions = self.sessions_in_range(
            value + timedelta(days=1), value + timedelta(days=14)
        )
        if not sessions:
            raise LookupError(
                f"no next {self.contract.venue.value} session found near {value}"
            )
        return sessions[0]

    def session_window(self, trade_date: date) -> MarketSessionWindow:
        if not self.is_session(trade_date):
            raise ValueError(
                f"{trade_date.isoformat()} is not a {self.contract.venue.value} session"
            )
        label = self._label(trade_date)
        start = self._calendar.session_open(label).to_pydatetime()
        end = self._calendar.session_close(label).to_pydatetime()
        break_start = self._calendar.session_break_start(label)
        break_end = self._calendar.session_break_end(label)
        breaks: tuple[SessionBreak, ...] = ()
        if not pd.isna(break_start) and not pd.isna(break_end):
            breaks = (
                SessionBreak(
                    start=break_start.to_pydatetime(),
                    end=break_end.to_pydatetime(),
                ),
            )
        return MarketSessionWindow(
            venue=self.contract.venue,
            trade_date=trade_date,
            label="REGULAR",
            open=start,
            close=end,
            breaks=breaks,
            is_early_close=label in self._calendar.early_closes,
            contract_version=self.contract.version_key,
        )

    def state_at(self, instant: datetime) -> SessionState:
        stamp = self._aware(instant, field="instant")
        local_zone = ZoneInfo(self._calendar.tz.key)
        local_date = stamp.astimezone(local_zone).date()
        for candidate in (local_date - timedelta(days=1), local_date):
            if not self.is_session(candidate):
                continue
            window = self.session_window(candidate)
            if not (window.open <= stamp <= window.close):
                continue
            if any(item.start <= stamp < item.end for item in window.breaks):
                return SessionState.BREAK
            if window.open <= stamp < window.close:
                return SessionState.REGULAR
        return SessionState.CLOSED

    def trade_date_at(self, instant: datetime) -> date:
        stamp = self._aware(instant, field="instant")
        local_zone = ZoneInfo(self._calendar.tz.key)
        local_date = stamp.astimezone(local_zone).date()
        matches = []
        for candidate in (local_date - timedelta(days=1), local_date):
            if not self.is_session(candidate):
                continue
            window = self.session_window(candidate)
            if window.open <= stamp < window.close and not any(
                item.start <= stamp < item.end for item in window.breaks
            ):
                matches.append(candidate)
        if len(matches) != 1:
            raise LookupError(
                f"instant does not map to exactly one regular {self.contract.venue.value} session"
            )
        return matches[0]

    def latest_completed_trade_date(
        self, instant: datetime, *, completion_buffer: timedelta = timedelta(0)
    ) -> date:
        stamp = self._aware(instant, field="instant")
        if completion_buffer < timedelta(0):
            raise ValueError("completion_buffer must not be negative")
        local_zone = ZoneInfo(self._calendar.tz.key)
        local_date = stamp.astimezone(local_zone).date()
        sessions = self.sessions_in_range(
            local_date - timedelta(days=14), local_date
        )
        completed = [
            item
            for item in sessions
            if self.session_window(item).close + completion_buffer <= stamp
        ]
        if not completed:
            raise LookupError(
                f"no completed {self.contract.venue.value} session found near {instant.isoformat()}"
            )
        return completed[-1]

    def expected_bar_starts(
        self,
        trade_date: date,
        interval: timedelta,
        *,
        as_of: datetime | None = None,
    ) -> tuple[datetime, ...]:
        if interval <= timedelta(0):
            raise ValueError("interval must be positive")
        window = self.session_window(trade_date)
        cutoff = window.close
        if as_of is not None:
            cutoff = min(cutoff, self._aware(as_of, field="as_of"))
        spans: list[tuple[datetime, datetime]] = []
        cursor = window.open
        for session_break in window.breaks:
            spans.append((cursor, session_break.start))
            cursor = session_break.end
        spans.append((cursor, window.close))
        starts: list[datetime] = []
        for span_start, span_end in spans:
            start = span_start
            while start + interval <= span_end and start + interval <= cutoff:
                starts.append(start)
                start += interval
        return tuple(starts)

    def completed_bar_window(
        self,
        trade_date: date,
        interval: timedelta,
        *,
        as_of: datetime | None = None,
    ) -> CompletedBarWindow:
        starts = self.expected_bar_starts(trade_date, interval, as_of=as_of)
        return CompletedBarWindow(
            venue=self.contract.venue,
            trade_date=trade_date,
            interval=interval,
            first_start=starts[0] if starts else None,
            last_start=starts[-1] if starts else None,
            last_end=starts[-1] + interval if starts else None,
            count=len(starts),
            contract_version=self.contract.version_key,
        )


class ExchangeTradingCalendar:
    """Backward-compatible cash-market facade over the shared session service.

    Existing collectors, schedulers, freshness logic, and GUI axes keep their
    public methods and provenance shape while all session decisions come from
    the versioned ``MarketSessionService`` contract.  Unsupported futures and
    spot-VIX venues are not reachable through this KR/US cash compatibility
    facade and remain fail-closed in ``MarketSessionService``.
    """

    def __init__(self, market: ExchangeMarket | str) -> None:
        self.market = ExchangeMarket(market)
        self._service = MarketSessionService(_MARKET_VENUES[self.market])
        contract = self._service.contract
        assert contract.calendar_name is not None
        assert contract.exchange_timezone is not None
        assert contract.source_package is not None
        assert contract.source_version is not None
        self.provenance = CalendarProvenance(
            market=self.market,
            calendar_name=contract.calendar_name,
            timezone=contract.exchange_timezone,
            source_package=contract.source_package,
            source_version=contract.source_version,
        )

    def is_trading_day(self, value: date) -> bool:
        return self._service.is_session(value)

    def sessions_in_range(self, start: date, end: date) -> tuple[date, ...]:
        return self._service.sessions_in_range(start, end)

    def previous_trading_day(self, value: date) -> date:
        return self._service.previous_trade_date(value)

    def next_trading_day(self, value: date) -> date:
        return self._service.next_trade_date(value)

    def session_open(self, value: date) -> datetime:
        return self._service.session_window(value).open

    def session_close(self, value: date) -> datetime:
        return self._service.session_window(value).close

    def latest_completed_session(
        self, as_of: datetime, *, completion_buffer: timedelta = timedelta(0)
    ) -> date:
        return self._service.latest_completed_trade_date(
            as_of, completion_buffer=completion_buffer
        )


def is_trading_day(market: ExchangeMarket | str, value: date) -> bool:
    return ExchangeTradingCalendar(market).is_trading_day(value)


def previous_trading_day(market: ExchangeMarket | str, value: date) -> date:
    return ExchangeTradingCalendar(market).previous_trading_day(value)


def next_trading_day(market: ExchangeMarket | str, value: date) -> date:
    return ExchangeTradingCalendar(market).next_trading_day(value)


def latest_completed_session(market: ExchangeMarket | str, as_of: datetime) -> date:
    return ExchangeTradingCalendar(market).latest_completed_session(as_of)


__all__ = [
    "CalendarProvenance", "CompletedBarWindow", "ExchangeMarket",
    "ExchangeTradingCalendar", "MarketSessionContract", "MarketSessionService",
    "MarketSessionVersionMismatchError",
    "MarketSessionWindow", "MarketVenue", "SessionBreak", "SessionRuleStatus",
    "SessionState", "UnsupportedMarketSessionError", "is_trading_day",
    "latest_completed_session", "market_session_contract", "next_trading_day",
    "previous_trading_day",
]
