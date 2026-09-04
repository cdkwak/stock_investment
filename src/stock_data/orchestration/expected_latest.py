from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar


class ObservationCalendar(StrEnum):
    XKRX = "XKRX"
    XNYS = "XNYS"
    PROVIDER_BUSINESS_DAY = "PROVIDER_BUSINESS_DAY"
    PROVIDER_PUBLICATION = "PROVIDER_PUBLICATION"
    EVENT_CALENDAR = "EVENT_CALENDAR"
    NO_MARKET_CALENDAR_REQUIRED = "NO_MARKET_CALENDAR_REQUIRED"


class ProviderAvailabilityPolicy(StrEnum):
    MARKET_SESSION_COMPLETE = "MARKET_SESSION_COMPLETE"
    FRED_H15_NEXT_BUSINESS_DAY_1615_ET = "FRED_H15_NEXT_BUSINESS_DAY_1615_ET"
    FRED_H10_WEEKLY_1615_ET = "FRED_H10_WEEKLY_1615_ET"
    FRED_VIX_NEXT_BUSINESS_DAY_0840_CT = "FRED_VIX_NEXT_BUSINESS_DAY_0840_CT"
    YAHOO_FUTURES_NEXT_BUSINESS_DAY_0800_ET = "YAHOO_FUTURES_NEXT_BUSINESS_DAY_0800_ET"
    DATA_GO_KR_D_PLUS_1_1300 = "DATA_GO_KR_D_PLUS_1_1300"
    CANONICAL_EQUITY_ACCEPTED_D_PLUS_1_1300 = (
        "CANONICAL_EQUITY_ACCEPTED_D_PLUS_1_1300"
    )
    KRX_POST_CLOSE_1830 = "KRX_POST_CLOSE_1830"
    KRX_POST_CLOSE_2030 = "KRX_POST_CLOSE_2030"
    KRX_NEXT_TRADING_DAY_0910 = "KRX_NEXT_TRADING_DAY_0910"
    KRX_SHORT_TRADING_T_PLUS_1 = "KRX_SHORT_TRADING_T_PLUS_1"
    KRX_SHORT_BALANCE_T_PLUS_2_1810 = "KRX_SHORT_BALANCE_T_PLUS_2_1810"
    KRX_SHORT_INVESTOR_SAME_DAY_1810 = "KRX_SHORT_INVESTOR_SAME_DAY_1810"
    KOFIA_T_PLUS_2_2030 = "KOFIA_T_PLUS_2_2030"
    KRX_COMPLETED_SUCCESSOR_SESSION = "KRX_COMPLETED_SUCCESSOR_SESSION"
    BOK_ECOS_FX_DAILY_1600_KST = "BOK_ECOS_FX_DAILY_1600_KST"
    MANUAL_OBSERVATION = "MANUAL_OBSERVATION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ExpectedLagPolicy(StrEnum):
    NONE = "NONE"
    NEXT_PROVIDER_BUSINESS_DAY = "NEXT_PROVIDER_BUSINESS_DAY"
    TWO_PROVIDER_BUSINESS_DAYS = "TWO_PROVIDER_BUSINESS_DAYS"
    PREVIOUS_BUSINESS_WEEK = "PREVIOUS_BUSINESS_WEEK"
    MANUAL = "MANUAL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ProviderAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ProviderFinality(StrEnum):
    CONFIRMED = "CONFIRMED"
    MANUAL_CONFIRMED = "MANUAL_CONFIRMED"
    AS_RETRIEVED = "AS_RETRIEVED"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ExpectedFreshness(StrEnum):
    CURRENT = "CURRENT"
    EXPECTED_LAG = "EXPECTED_LAG"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ExpectedLatestPolicy:
    observation_calendar: ObservationCalendar
    provider_availability_policy: ProviderAvailabilityPolicy
    expected_lag_policy: ExpectedLagPolicy
    finality_policy: ProviderFinality
    exchange_market: ExchangeMarket | None = None


@dataclass(frozen=True)
class ExpectedLatestResult:
    dataset: str
    calendar: str
    expected_market_date: date | None
    expected_available_observation: date | None
    retained_latest: date | None
    freshness: ExpectedFreshness
    availability: ProviderAvailability
    finality: ProviderFinality
    collection_required: bool
    observation_calendar: ObservationCalendar
    provider_availability_policy: ProviderAvailabilityPolicy
    expected_lag_policy: ExpectedLagPolicy
    calendar_source: str
    calendar_version: str
    due_at: datetime | None = None
    pending_until: str | None = None


KR_DAILY_LANES = frozenset({
    "KR_INDEX_DAILY", "VKOSPI_DAILY", "CANONICAL_EQUITY_DAILY",
    "DERIVATIVES_PRICE_DAILY", "DERIVATIVES_INVESTOR_DAILY",
    "SHORT_SELLING_DAILY", "LENDING_DAILY", "LIQUIDITY_CREDIT_DAILY",
    "SHORT_SELLING_BALANCE_DAILY", "SHORT_SELLING_INVESTOR_DAILY",
    "MARKET_INVESTOR_DAILY", "TOSS_KR_TREASURY_DAILY", "LS_T8462_DAILY",
    "KR_ETF_PRICE_DAILY", "KR_EQUITY_PROVISIONAL_DAILY",
})
US_DAILY_LANES = frozenset({
    "GLOBAL_INDEX_DAILY", "GLOBAL_ETF_DAILY", "GLOBAL_COMMODITY_DAILY",
})

_FRED_H15 = frozenset({"fred_treasury_yield_daily", "us_treasury_spread_daily"})
_FRED_H10 = frozenset({"fred_usd_fx_daily"})
_FRED_VIX = frozenset({"fred_vix_daily"})
_KST = ZoneInfo("Asia/Seoul")
_XKRX_MANUAL_DATASETS = frozenset({
    "kr_equity_foreign_ownership_daily", "kr_equity_fundamental_daily",
    "kr_equity_program_trading_daily", "kr_equity_sector_classification",
    "kr_etf_ohlcv_daily", "kr_etf_universe_daily", "kr_index_constituent_daily",
    "ls_t1633_program_trading_candidate",
    "ls_t8428_surrounding_funds_source_observation",
})
_NO_MARKET_CALENDAR_DATASETS = frozenset({
    "kr_credit_benchmark_yield_daily", "krx_legacy_kospi200_futures_daily",
    "krx_legacy_kospi200_options_daily",
})


def policy_for_dataset(dataset: str, lane: str) -> ExpectedLatestPolicy | None:
    if dataset in _NO_MARKET_CALENDAR_DATASETS:
        return ExpectedLatestPolicy(
            ObservationCalendar.NO_MARKET_CALENDAR_REQUIRED,
            ProviderAvailabilityPolicy.NOT_APPLICABLE,
            ExpectedLagPolicy.NOT_APPLICABLE,
            ProviderFinality.NOT_APPLICABLE,
        )
    if dataset == "kr_index_fundamental_daily" and lane == "KR_INDEX_FUNDAMENTAL_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_NEXT_TRADING_DAY_0910,
            ExpectedLagPolicy.NEXT_PROVIDER_BUSINESS_DAY,
            ProviderFinality.UNKNOWN,
            ExchangeMarket.KR,
        )
    if dataset == "bok_ecos_usd_krw_daily" and lane == "BOK_FX_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.PROVIDER_BUSINESS_DAY,
            ProviderAvailabilityPolicy.BOK_ECOS_FX_DAILY_1600_KST,
            ExpectedLagPolicy.NONE,
            ProviderFinality.UNKNOWN,
        )
    if lane == "KOSPI200_BREADTH_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.PROVIDER_PUBLICATION,
            ProviderAvailabilityPolicy.CANONICAL_EQUITY_ACCEPTED_D_PLUS_1_1300,
            ExpectedLagPolicy.NEXT_PROVIDER_BUSINESS_DAY,
            ProviderFinality.CONFIRMED,
            ExchangeMarket.KR,
        )
    if dataset in _XKRX_MANUAL_DATASETS:
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX, ProviderAvailabilityPolicy.MANUAL_OBSERVATION,
            ExpectedLagPolicy.MANUAL, ProviderFinality.UNKNOWN, ExchangeMarket.KR,
        )
    if dataset in _FRED_H15:
        return ExpectedLatestPolicy(
            ObservationCalendar.PROVIDER_BUSINESS_DAY,
            ProviderAvailabilityPolicy.FRED_H15_NEXT_BUSINESS_DAY_1615_ET,
            ExpectedLagPolicy.NEXT_PROVIDER_BUSINESS_DAY,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.US,
        )
    if dataset in _FRED_H10:
        return ExpectedLatestPolicy(
            ObservationCalendar.PROVIDER_PUBLICATION,
            ProviderAvailabilityPolicy.FRED_H10_WEEKLY_1615_ET,
            ExpectedLagPolicy.PREVIOUS_BUSINESS_WEEK,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.US,
        )
    if dataset in _FRED_VIX:
        return ExpectedLatestPolicy(
            ObservationCalendar.XNYS,
            ProviderAvailabilityPolicy.FRED_VIX_NEXT_BUSINESS_DAY_0840_CT,
            ExpectedLagPolicy.NEXT_PROVIDER_BUSINESS_DAY,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.US,
        )
    if dataset == "global_commodity_futures_daily":
        return ExpectedLatestPolicy(
            ObservationCalendar.XNYS,
            ProviderAvailabilityPolicy.YAHOO_FUTURES_NEXT_BUSINESS_DAY_0800_ET,
            ExpectedLagPolicy.NEXT_PROVIDER_BUSINESS_DAY,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.US,
        )
    if lane in {"CANONICAL_EQUITY_DAILY", "LENDING_DAILY"}:
        return ExpectedLatestPolicy(
            ObservationCalendar.PROVIDER_PUBLICATION,
            ProviderAvailabilityPolicy.DATA_GO_KR_D_PLUS_1_1300,
            ExpectedLagPolicy.NEXT_PROVIDER_BUSINESS_DAY,
            ProviderFinality.CONFIRMED,
            ExchangeMarket.KR,
        )
    if dataset == "kr_short_selling_trading_daily" and lane == "SHORT_SELLING_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_SHORT_TRADING_T_PLUS_1,
            ExpectedLagPolicy.NEXT_PROVIDER_BUSINESS_DAY,
            ProviderFinality.CONFIRMED,
            ExchangeMarket.KR,
        )
    if dataset == "kr_short_selling_balance_daily" and lane == "SHORT_SELLING_BALANCE_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_SHORT_BALANCE_T_PLUS_2_1810,
            ExpectedLagPolicy.TWO_PROVIDER_BUSINESS_DAYS,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.KR,
        )
    if dataset == "kr_short_selling_investor_daily" and lane == "SHORT_SELLING_INVESTOR_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_SHORT_INVESTOR_SAME_DAY_1810,
            ExpectedLagPolicy.NONE,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.KR,
        )
    if dataset == "kr_credit_balance_daily" and lane == "LIQUIDITY_CREDIT_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KOFIA_T_PLUS_2_2030,
            ExpectedLagPolicy.TWO_PROVIDER_BUSINESS_DAYS,
            ProviderFinality.UNKNOWN,
            ExchangeMarket.KR,
        )
    if lane == "DERIVATIVES_PRICE_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_COMPLETED_SUCCESSOR_SESSION,
            ExpectedLagPolicy.NEXT_PROVIDER_BUSINESS_DAY,
            ProviderFinality.CONFIRMED,
            ExchangeMarket.KR,
        )
    if lane in {"KR_INDEX_DAILY", "VKOSPI_DAILY", "MARKET_INVESTOR_DAILY"}:
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_POST_CLOSE_1830,
            ExpectedLagPolicy.NONE,
            ProviderFinality.CONFIRMED,
            ExchangeMarket.KR,
        )
    if lane == "KR_ETF_PRICE_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_POST_CLOSE_2030,
            ExpectedLagPolicy.NONE,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.KR,
        )
    if lane == "KR_EQUITY_PROVISIONAL_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_POST_CLOSE_2030,
            ExpectedLagPolicy.NONE,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.KR,
        )
    if lane == "TOSS_KR_TREASURY_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_COMPLETED_SUCCESSOR_SESSION,
            ExpectedLagPolicy.NEXT_PROVIDER_BUSINESS_DAY,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.KR,
        )
    if (
        dataset == "bok_ecos_kr_treasury_yield_source_observation"
        and lane in {"BOK_TREASURY_OBSERVATION_DAILY", "KR_TREASURY_DAILY"}
    ):
        return ExpectedLatestPolicy(
            ObservationCalendar.PROVIDER_PUBLICATION,
            ProviderAvailabilityPolicy.MANUAL_OBSERVATION,
            ExpectedLagPolicy.MANUAL,
            ProviderFinality.UNKNOWN,
        )
    if lane == "LS_T8462_DAILY":
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX,
            ProviderAvailabilityPolicy.KRX_POST_CLOSE_1830,
            ExpectedLagPolicy.NONE,
            ProviderFinality.AS_RETRIEVED,
            ExchangeMarket.KR,
        )
    if lane in KR_DAILY_LANES:
        return ExpectedLatestPolicy(
            ObservationCalendar.XKRX, ProviderAvailabilityPolicy.MANUAL_OBSERVATION,
            ExpectedLagPolicy.MANUAL, ProviderFinality.UNKNOWN, ExchangeMarket.KR,
        )
    if lane in US_DAILY_LANES:
        return ExpectedLatestPolicy(
            ObservationCalendar.XNYS, ProviderAvailabilityPolicy.MARKET_SESSION_COMPLETE,
            ExpectedLagPolicy.NONE, ProviderFinality.AS_RETRIEVED, ExchangeMarket.US,
        )
    return None


def policy_for_lane(lane: str) -> ExpectedLatestPolicy | None:
    """Compatibility view for homogeneous lanes; FRED is dataset-specific."""
    return policy_for_dataset("", lane)


def _at(day: date, clock: time, zone: str) -> datetime:
    return datetime.combine(day, clock, ZoneInfo(zone))


def _recent_sessions(calendar: ExchangeTradingCalendar, as_of: datetime) -> tuple[date, ...]:
    last = calendar.latest_completed_session(as_of)
    start = last
    for _ in range(25):
        start = calendar.previous_trading_day(start)
    return tuple(calendar.sessions_in_range(start, last))


def _previous_weekday(day: date) -> date:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _bok_fx_target(as_of: datetime) -> date:
    local = as_of.astimezone(ZoneInfo("Asia/Seoul"))
    candidate = local.date() if local.time() >= time(16, 0) else local.date() - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _provider_target(
    policy: ExpectedLatestPolicy, calendar: ExchangeTradingCalendar, as_of: datetime,
) -> tuple[date, ProviderAvailability]:
    sessions = _recent_sessions(calendar, as_of)
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.MARKET_SESSION_COMPLETE:
        return sessions[-1], ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.MANUAL_OBSERVATION:
        return sessions[-1], ProviderAvailability.UNKNOWN
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.FRED_H15_NEXT_BUSINESS_DAY_1615_ET:
        releases = [day for day in sessions if _at(day, time(16, 15), "America/New_York") <= as_of]
        release = releases[-1]
        return calendar.previous_trading_day(release), ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.FRED_VIX_NEXT_BUSINESS_DAY_0840_CT:
        available = []
        for observation in sessions:
            release = calendar.next_trading_day(observation)
            if _at(release, time(8, 40), "America/Chicago") <= as_of:
                available.append(observation)
        return available[-1], ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.YAHOO_FUTURES_NEXT_BUSINESS_DAY_0800_ET:
        available = []
        for observation in sessions:
            release = calendar.next_trading_day(observation)
            if _at(release, time(8, 0), "America/New_York") <= as_of:
                available.append(observation)
        return available[-1], ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.FRED_H10_WEEKLY_1615_ET:
        releases = []
        for day in sessions:
            week = day.isocalendar()[:2]
            first = next(candidate for candidate in sessions if candidate.isocalendar()[:2] == week)
            if day == first and _at(day, time(16, 15), "America/New_York") <= as_of:
                releases.append(day)
        release = releases[-1]
        return max(day for day in sessions if day < release), ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy in {
        ProviderAvailabilityPolicy.DATA_GO_KR_D_PLUS_1_1300,
        ProviderAvailabilityPolicy.CANONICAL_EQUITY_ACCEPTED_D_PLUS_1_1300,
    }:
        latest_completed = sessions[-1]
        next_release = calendar.next_trading_day(latest_completed)
        if _at(next_release, time(13, 0), "Asia/Seoul") <= as_of:
            return latest_completed, ProviderAvailability.AVAILABLE
        return calendar.previous_trading_day(latest_completed), ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.KRX_POST_CLOSE_1830:
        latest_completed = sessions[-1]
        if _at(latest_completed, time(18, 30), "Asia/Seoul") <= as_of:
            return latest_completed, ProviderAvailability.AVAILABLE
        return calendar.previous_trading_day(latest_completed), ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.KRX_POST_CLOSE_2030:
        latest_completed = sessions[-1]
        if _at(latest_completed, time(20, 30), "Asia/Seoul") <= as_of:
            return latest_completed, ProviderAvailability.AVAILABLE
        return calendar.previous_trading_day(latest_completed), ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.KRX_SHORT_INVESTOR_SAME_DAY_1810:
        latest_completed = sessions[-1]
        if _at(latest_completed, time(18, 10), "Asia/Seoul") <= as_of:
            return latest_completed, ProviderAvailability.AVAILABLE
        return calendar.previous_trading_day(latest_completed), ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.KRX_SHORT_BALANCE_T_PLUS_2_1810:
        eligible = []
        for observation in sessions:
            first = calendar.next_trading_day(observation)
            second = calendar.next_trading_day(first)
            if _at(second, time(18, 10), "Asia/Seoul") <= as_of:
                eligible.append(observation)
        return eligible[-1], ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.KOFIA_T_PLUS_2_2030:
        eligible = []
        for observation in sessions:
            first = calendar.next_trading_day(observation)
            second = calendar.next_trading_day(first)
            if _at(second, time(20, 30), "Asia/Seoul") <= as_of:
                eligible.append(observation)
        return eligible[-1], ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.KRX_COMPLETED_SUCCESSOR_SESSION:
        return calendar.previous_trading_day(sessions[-1]), ProviderAvailability.AVAILABLE
    if policy.provider_availability_policy in {
        ProviderAvailabilityPolicy.KRX_NEXT_TRADING_DAY_0910,
        ProviderAvailabilityPolicy.KRX_SHORT_TRADING_T_PLUS_1,
    }:
        eligible = [
            observation for observation in sessions
            if _at(
                calendar.next_trading_day(observation), time(9, 10), "Asia/Seoul",
            ) <= as_of
        ]
        return eligible[-1], ProviderAvailability.AVAILABLE
    raise ValueError(f"unsupported availability policy: {policy.provider_availability_policy}")


def _trading_day_after(
    calendar: ExchangeTradingCalendar, day: date, count: int = 1,
) -> date:
    result = day
    for _ in range(count):
        result = calendar.next_trading_day(result)
    return result


def _due_at(
    *, dataset: str, lane: str, policy: ExpectedLatestPolicy, target: date,
    calendar: ExchangeTradingCalendar | None,
) -> datetime | None:
    availability = policy.provider_availability_policy
    if availability in {
        ProviderAvailabilityPolicy.MANUAL_OBSERVATION,
        ProviderAvailabilityPolicy.NOT_APPLICABLE,
    }:
        if dataset == "kr_market_liquidity_daily" and lane == "LIQUIDITY_CREDIT_DAILY":
            return datetime.combine(target, time(20, 45), _KST)
        return None
    if availability is ProviderAvailabilityPolicy.BOK_ECOS_FX_DAILY_1600_KST:
        return datetime.combine(target, time(20, 45), _KST)
    if calendar is None:
        return None
    if availability in {
        ProviderAvailabilityPolicy.FRED_H15_NEXT_BUSINESS_DAY_1615_ET,
        ProviderAvailabilityPolicy.FRED_H10_WEEKLY_1615_ET,
    }:
        release = calendar.next_trading_day(target)
        return datetime.combine(release + timedelta(days=1), time(6, 35), _KST)
    if availability is ProviderAvailabilityPolicy.FRED_VIX_NEXT_BUSINESS_DAY_0840_CT:
        release = calendar.next_trading_day(target)
        return datetime.combine(release, time(23, 0), _KST)
    if availability is ProviderAvailabilityPolicy.MARKET_SESSION_COMPLETE:
        return datetime.combine(target + timedelta(days=1), time(6, 35), _KST)
    if availability is ProviderAvailabilityPolicy.YAHOO_FUTURES_NEXT_BUSINESS_DAY_0800_ET:
        release = calendar.next_trading_day(target)
        return datetime.combine(release, time(22, 25), _KST)
    if availability in {
        ProviderAvailabilityPolicy.DATA_GO_KR_D_PLUS_1_1300,
        ProviderAvailabilityPolicy.CANONICAL_EQUITY_ACCEPTED_D_PLUS_1_1300,
    }:
        due_time = time(14, 25) if lane in {"CANONICAL_EQUITY_DAILY", "LENDING_DAILY"} else time(20, 45)
        return datetime.combine(calendar.next_trading_day(target), due_time, _KST)
    if availability in {
        ProviderAvailabilityPolicy.KRX_POST_CLOSE_1830,
        ProviderAvailabilityPolicy.KRX_POST_CLOSE_2030,
        ProviderAvailabilityPolicy.KRX_SHORT_INVESTOR_SAME_DAY_1810,
    }:
        return datetime.combine(target, time(20, 45), _KST)
    if availability in {
        ProviderAvailabilityPolicy.KRX_NEXT_TRADING_DAY_0910,
        ProviderAvailabilityPolicy.KRX_SHORT_TRADING_T_PLUS_1,
    }:
        return datetime.combine(calendar.next_trading_day(target), time(9, 25), _KST)
    if availability in {
        ProviderAvailabilityPolicy.KRX_SHORT_BALANCE_T_PLUS_2_1810,
        ProviderAvailabilityPolicy.KOFIA_T_PLUS_2_2030,
    }:
        return datetime.combine(_trading_day_after(calendar, target, 2), time(20, 45), _KST)
    if availability is ProviderAvailabilityPolicy.KRX_COMPLETED_SUCCESSOR_SESSION:
        return datetime.combine(calendar.next_trading_day(target), time(20, 45), _KST)
    return None


def _previous_expected_observation(
    policy: ExpectedLatestPolicy, calendar: ExchangeTradingCalendar, target: date,
) -> date:
    if policy.provider_availability_policy is ProviderAvailabilityPolicy.FRED_H10_WEEKLY_1615_ET:
        target_week = target.isocalendar()[:2]
        previous = calendar.previous_trading_day(target)
        while previous.isocalendar()[:2] == target_week:
            previous = calendar.previous_trading_day(previous)
        return previous
    return calendar.previous_trading_day(target)


def _pending_until(
    *, retained_latest: date | None, target: date, due_at: datetime | None,
    as_of: datetime, previous_target: date,
) -> str | None:
    if (
        retained_latest is not None
        and previous_target <= retained_latest < target
        and due_at is not None
        and as_of < due_at
    ):
        return due_at.astimezone(_KST).strftime("%H:%M")
    return None


def resolve_expected_latest(
    *, dataset: str, lane: str, retained_latest: date | None, as_of: datetime,
    availability: ProviderAvailability | None = None,
) -> ExpectedLatestResult | None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    policy = policy_for_dataset(dataset, lane)
    if policy is None:
        return None
    if (
        policy.provider_availability_policy
        is ProviderAvailabilityPolicy.BOK_ECOS_FX_DAILY_1600_KST
    ):
        target = _bok_fx_target(as_of)
        due_at = _due_at(
            dataset=dataset, lane=lane, policy=policy, target=target, calendar=None,
        )
        pending_until = _pending_until(
            retained_latest=retained_latest,
            target=target,
            due_at=due_at,
            as_of=as_of,
            previous_target=_previous_weekday(target),
        )
        effective_availability = availability or ProviderAvailability.AVAILABLE
        if retained_latest is None:
            freshness = ExpectedFreshness.UNKNOWN
        elif retained_latest >= target:
            freshness = ExpectedFreshness.CURRENT
        elif pending_until is not None:
            freshness = ExpectedFreshness.CURRENT
        elif retained_latest >= _previous_weekday(target):
            # The 16:00 clock is operational, not a verified publication SLA.
            # One absent target row is expected provider lag, not stale/failure.
            freshness = ExpectedFreshness.EXPECTED_LAG
        else:
            freshness = ExpectedFreshness.STALE
        return ExpectedLatestResult(
            dataset=dataset,
            calendar="BOK_ECOS_PROVIDER_WEEKDAY",
            expected_market_date=target,
            expected_available_observation=target,
            retained_latest=retained_latest,
            freshness=freshness,
            availability=effective_availability,
            finality=policy.finality_policy,
            collection_required=(retained_latest is None or retained_latest < target)
            and pending_until is None,
            observation_calendar=policy.observation_calendar,
            provider_availability_policy=policy.provider_availability_policy,
            expected_lag_policy=policy.expected_lag_policy,
            calendar_source="project-weekday-operating-rule",
            calendar_version="1",
            due_at=due_at,
            pending_until=pending_until,
        )
    if policy.exchange_market is None:
        unavailable = (
            policy.provider_availability_policy
            is ProviderAvailabilityPolicy.MANUAL_OBSERVATION
        )
        return ExpectedLatestResult(
            dataset=dataset, calendar=policy.observation_calendar.value,
            expected_market_date=None, expected_available_observation=None,
            retained_latest=retained_latest,
            freshness=(ExpectedFreshness.UNKNOWN if unavailable else ExpectedFreshness.NOT_APPLICABLE),
            availability=(ProviderAvailability.UNKNOWN if unavailable else ProviderAvailability.NOT_APPLICABLE),
            finality=policy.finality_policy, collection_required=False,
            observation_calendar=policy.observation_calendar,
            provider_availability_policy=policy.provider_availability_policy,
            expected_lag_policy=policy.expected_lag_policy,
            calendar_source="typed-dataset-policy", calendar_version="1",
        )
    calendar = ExchangeTradingCalendar(policy.exchange_market)
    market_date = calendar.latest_completed_session(as_of)
    provider_target, derived_availability = _provider_target(policy, calendar, as_of)
    due_at = _due_at(
        dataset=dataset, lane=lane, policy=policy, target=provider_target,
        calendar=calendar,
    )
    pending_until = _pending_until(
        retained_latest=retained_latest,
        target=provider_target,
        due_at=due_at,
        as_of=as_of,
        previous_target=_previous_expected_observation(policy, calendar, provider_target),
    )
    effective_availability = availability or derived_availability
    if retained_latest is None:
        freshness = ExpectedFreshness.UNKNOWN
    elif retained_latest >= market_date:
        freshness = ExpectedFreshness.CURRENT
    elif retained_latest >= provider_target:
        freshness = ExpectedFreshness.EXPECTED_LAG
    elif pending_until is not None:
        freshness = ExpectedFreshness.CURRENT
    else:
        freshness = ExpectedFreshness.STALE
    return ExpectedLatestResult(
        dataset=dataset,
        calendar=calendar.provenance.calendar_name,
        expected_market_date=market_date,
        expected_available_observation=provider_target,
        retained_latest=retained_latest,
        freshness=freshness,
        availability=effective_availability,
        finality=policy.finality_policy,
        collection_required=(retained_latest is None or retained_latest < provider_target)
        and effective_availability is ProviderAvailability.AVAILABLE
        and pending_until is None,
        observation_calendar=policy.observation_calendar,
        provider_availability_policy=policy.provider_availability_policy,
        expected_lag_policy=policy.expected_lag_policy,
        calendar_source=calendar.provenance.source_package,
        calendar_version=calendar.provenance.source_version,
        due_at=due_at,
        pending_until=pending_until,
    )


__all__ = [
    "ExpectedFreshness", "ExpectedLagPolicy", "ExpectedLatestPolicy", "ExpectedLatestResult",
    "ObservationCalendar", "ProviderAvailability", "ProviderAvailabilityPolicy",
    "ProviderFinality", "policy_for_dataset", "policy_for_lane", "resolve_expected_latest",
]
