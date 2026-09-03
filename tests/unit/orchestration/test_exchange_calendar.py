from datetime import date, datetime, timedelta, timezone

import pytest

from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket,
    ExchangeTradingCalendar,
    MarketSessionService,
    MarketSessionVersionMismatchError,
    MarketVenue,
    SessionRuleStatus,
    SessionState,
    UnsupportedMarketSessionError,
    market_session_contract,
)
from stock_data.orchestration.expected_latest import (
    ExpectedFreshness, ProviderAvailability, ProviderFinality, resolve_expected_latest,
)


def test_kr_calendar_holiday_and_adjacent_sessions_are_not_weekday_inference() -> None:
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    assert calendar.is_trading_day(date(2026, 8, 14))
    assert not calendar.is_trading_day(date(2026, 8, 17))
    assert calendar.previous_trading_day(date(2026, 8, 17)) == date(2026, 8, 14)
    assert calendar.next_trading_day(date(2026, 8, 17)) == date(2026, 8, 18)


def test_kr_calendar_applies_official_2026_one_off_closures() -> None:
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)

    assert not calendar.is_trading_day(date(2026, 6, 3))
    assert not calendar.is_trading_day(date(2026, 7, 17))
    sessions = calendar.sessions_in_range(date(2026, 6, 2), date(2026, 6, 4))
    assert sessions == (date(2026, 6, 2), date(2026, 6, 4))
    assert calendar.previous_trading_day(date(2026, 6, 4)) == date(2026, 6, 2)


def test_us_calendar_holiday_dst_and_early_close() -> None:
    calendar = ExchangeTradingCalendar(ExchangeMarket.US)
    assert not calendar.is_trading_day(date(2026, 7, 3))
    assert calendar.session_open(date(2026, 3, 6)).hour == 14
    assert calendar.session_open(date(2026, 3, 9)).hour == 13
    assert calendar.session_close(date(2026, 11, 27)).hour == 18


def test_latest_completed_session_respects_market_timezone_and_close() -> None:
    calendar = ExchangeTradingCalendar(ExchangeMarket.US)
    before_close = datetime(2026, 8, 19, 4, 30, tzinfo=timezone.utc).astimezone()
    # 04:30 UTC is after the prior session, independent of the host timezone.
    assert calendar.latest_completed_session(before_close) == date(2026, 8, 18)
    at_2026_08_18_close = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)
    assert calendar.latest_completed_session(at_2026_08_18_close) == date(2026, 8, 18)
    with pytest.raises(ValueError, match="timezone-aware"):
        calendar.latest_completed_session(datetime(2026, 8, 18, 20, 0))


def test_expected_latest_separates_session_availability_finality_and_retention() -> None:
    result = resolve_expected_latest(
        dataset="fred_vix_daily", lane="FRED_DAILY",
        retained_latest=date(2026, 8, 14),
        as_of=datetime(2026, 8, 18, 20, 1, tzinfo=timezone.utc),
        availability=ProviderAvailability.UNKNOWN,
    )
    assert result is not None
    assert result.expected_market_date == date(2026, 8, 18)
    assert result.freshness is ExpectedFreshness.EXPECTED_LAG
    assert result.finality is ProviderFinality.AS_RETRIEVED
    assert result.collection_required is False
    assert result.calendar == "XNYS"
    assert result.calendar_version == "4.13.2"


def test_expected_latest_requires_explicit_provider_availability_for_collection() -> None:
    result = resolve_expected_latest(
        dataset="fred_vix_daily", lane="FRED_DAILY",
        retained_latest=date(2026, 8, 14),
        as_of=datetime(2026, 8, 18, 20, 1, tzinfo=timezone.utc),
        availability=ProviderAvailability.AVAILABLE,
    )
    assert result is not None and not result.collection_required
    assert resolve_expected_latest(
        dataset="weekly", lane="WEEKLY", retained_latest=None,
        as_of=datetime(2026, 8, 18, tzinfo=timezone.utc),
    ) is None


def test_index_fundamental_expected_latest_matches_the_0910_prior_session_route() -> None:
    kst = timezone(timedelta(hours=9))
    before_next_run = resolve_expected_latest(
        dataset="kr_index_fundamental_daily", lane="KR_INDEX_FUNDAMENTAL_DAILY",
        retained_latest=date(2026, 8, 25),
        as_of=datetime(2026, 8, 26, 20, 31, tzinfo=kst),
    )
    assert before_next_run is not None
    assert before_next_run.expected_market_date == date(2026, 8, 26)
    assert before_next_run.expected_available_observation == date(2026, 8, 25)
    assert before_next_run.freshness is ExpectedFreshness.EXPECTED_LAG
    assert before_next_run.collection_required is False

    at_next_run = resolve_expected_latest(
        dataset="kr_index_fundamental_daily", lane="KR_INDEX_FUNDAMENTAL_DAILY",
        retained_latest=date(2026, 8, 25),
        as_of=datetime(2026, 8, 27, 9, 10, tzinfo=kst),
    )
    assert at_next_run is not None
    assert at_next_run.expected_available_observation == date(2026, 8, 26)
    assert at_next_run.freshness is ExpectedFreshness.STALE
    assert at_next_run.collection_required is True


@pytest.mark.parametrize(
    ("venue", "calendar_name", "timezone_name"),
    (
        (MarketVenue.XKRX_CASH, "XKRX", "Asia/Seoul"),
        (MarketVenue.XNYS_CASH, "XNYS", "America/New_York"),
    ),
)
def test_shared_contract_versions_only_evidenced_regular_sessions(
    venue, calendar_name, timezone_name
) -> None:
    contract = market_session_contract(venue)
    assert contract.status is SessionRuleStatus.ACTIVE_REGULAR_ONLY
    assert contract.calendar_name == calendar_name
    assert contract.exchange_timezone == timezone_name
    assert contract.policy_version == (
        2 if venue is MarketVenue.XKRX_CASH else 1
    )
    assert contract.source_package == "exchange-calendars"
    assert contract.source_version == "4.13.2"
    # exchange-calendars' default schedule horizon rolls with the host date;
    # assert the supported decision date is covered rather than pinning that
    # moving cache boundary.
    assert contract.effective_from <= date(2026, 1, 1)
    assert contract.effective_to >= date(2026, 12, 31)
    expected_policy_version = 2 if venue is MarketVenue.XKRX_CASH else 1
    assert contract.version_key.startswith(
        f"market-session-v{expected_policy_version}|exchange-calendars=4.13.2|"
    )
    assert contract.extended_session_policy == "NOT_OWNED_FAIL_CLOSED"
    assert not contract.ui_anchor_is_exchange_boundary


def test_shared_xnys_boundaries_cover_dst_holiday_early_close_and_year_end() -> None:
    service = MarketSessionService(MarketVenue.XNYS_CASH)
    before_dst = service.session_window(date(2026, 3, 6))
    after_dst = service.session_window(date(2026, 3, 9))
    early_close = service.session_window(date(2026, 11, 27))

    assert before_dst.open == datetime(2026, 3, 6, 14, 30, tzinfo=timezone.utc)
    assert after_dst.open == datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc)
    assert early_close.close == datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
    assert early_close.is_early_close
    assert not service.is_session(date(2026, 7, 3))
    assert not service.is_session(date(2026, 12, 25))
    with pytest.raises(ValueError, match="not a XNYS_CASH session"):
        service.session_window(date(2026, 12, 25))


def test_shared_completed_bar_window_is_close_and_asof_bounded() -> None:
    service = MarketSessionService(MarketVenue.XNYS_CASH)
    complete = service.completed_bar_window(
        date(2026, 8, 19), timedelta(minutes=15)
    )
    partial = service.completed_bar_window(
        date(2026, 8, 19),
        timedelta(minutes=15),
        as_of=datetime(2026, 8, 19, 14, 7, tzinfo=timezone.utc),
    )

    assert complete.count == 26
    assert complete.first_start == datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)
    assert complete.last_start == datetime(2026, 8, 19, 19, 45, tzinfo=timezone.utc)
    assert complete.last_end == datetime(2026, 8, 19, 20, 0, tzinfo=timezone.utc)
    assert partial.count == 2
    assert partial.last_end == datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)


def test_shared_trade_date_and_state_are_unambiguous_and_timezone_aware() -> None:
    service = MarketSessionService(MarketVenue.XKRX_CASH)
    during = datetime(2026, 8, 19, 2, 0, tzinfo=timezone.utc)
    at_close = datetime(2026, 8, 19, 6, 30, tzinfo=timezone.utc)

    assert service.state_at(during) is SessionState.REGULAR
    assert service.trade_date_at(during) == date(2026, 8, 19)
    assert service.state_at(at_close) is SessionState.CLOSED
    with pytest.raises(LookupError, match="exactly one regular"):
        service.trade_date_at(at_close)
    with pytest.raises(ValueError, match="timezone-aware"):
        service.state_at(datetime(2026, 8, 19, 2, 0))


def test_spot_vix_official_windows_do_not_borrow_xnys_or_yahoo_boundaries() -> None:
    contract = market_session_contract(MarketVenue.CBOE_SPOT_VIX)
    assert contract.status is SessionRuleStatus.EVIDENCE_REQUIRED
    assert contract.exchange_timezone == "America/Chicago"
    assert contract.extended_session_policy == "GTH_CALCULATION_0215_0825_CT"
    assert contract.regular_session_label == "RTH_CALCULATION_0831_1515_CT"
    assert contract.policy_version == 3
    assert contract.expiration_policy.startswith("NOT_A_FUTURES_CONTRACT")
    assert contract.provider_mapping_policy == (
        "YAHOO_VIX_IS_PROVIDER_SUBSET_NOT_OFFICIAL_15_SECOND_SERIES"
    )
    assert "YAHOO_CBOE_XNYS_ALIGNED_PROVIDER_SUBSET" in contract.evidence[-1]
    assert "yahoo_15m_to_official_15_second_dissemination_mapping" in (
        contract.unresolved_rules
    )
    with pytest.raises(UnsupportedMarketSessionError, match="session rule is unresolved"):
        MarketSessionService(MarketVenue.CBOE_SPOT_VIX)


@pytest.mark.parametrize(
    "venue",
    (
        MarketVenue.CME_EQUITY_INDEX_FUTURES,
        MarketVenue.NYMEX_WTI_FUTURES,
        MarketVenue.CFE_VIX_FUTURES,
    ),
)
def test_unproven_futures_boundaries_fail_closed_including_sunday(venue) -> None:
    contract = market_session_contract(venue)
    assert contract.status is SessionRuleStatus.EVIDENCE_REQUIRED
    assert contract.exchange_timezone == "America/Chicago"
    assert contract.evidence
    with pytest.raises(UnsupportedMarketSessionError, match="session rule is unresolved"):
        MarketSessionService(venue)


def test_wti_and_cfe_contracts_retain_only_officially_explicit_boundaries() -> None:
    wti = market_session_contract(MarketVenue.NYMEX_WTI_FUTURES)
    assert wti.extended_session_policy == "NO_RTH_ETH_SPLIT_1700_PREVIOUS_DAY_TO_1600_CT"
    assert wti.maintenance_policy == "DAILY_CLOSED_1600_1700_CT"
    assert wti.expiration_policy.startswith(
        "LISTED_CL_TERMINATES_THIRD_BUSINESS_DAY_BEFORE_25TH"
    )
    assert wti.provider_mapping_policy.endswith("UNVERIFIED")
    assert "listed_contract_expiration_boundary" not in wti.unresolved_rules
    assert "yahoo_continuous_symbol_to_exchange_trade_date" in wti.unresolved_rules

    vx = market_session_contract(MarketVenue.CFE_VIX_FUTURES)
    assert vx.effective_from == date(2026, 1, 1)
    assert vx.effective_to == date(2026, 12, 31)
    assert vx.regular_session_label == "RTH_0830_1500_CT"
    assert vx.extended_session_policy == "ETH_1700_PREVIOUS_DAY_0830_CT_AND_1500_1600_CT"
    assert vx.early_close_policy == (
        "RTH_0830_1215_CT_20261127_AND_20261224; EXPIRING_VX_CLOSE_0800_CT"
    )
    assert vx.expiration_policy.endswith("EXPIRING_VX_CLOSE_0800_CT")
    assert vx.provider_mapping_policy.startswith("INTENTIONALLY_UNAVAILABLE")
    assert "expiration_day_shortened_session" not in vx.unresolved_rules
    assert "no_current_repository_provider_route" in vx.unresolved_rules


def test_nq_general_globex_evidence_does_not_create_an_exact_product_calendar() -> None:
    nq = market_session_contract(MarketVenue.CME_EQUITY_INDEX_FUTURES)
    assert nq.regular_session_label == "GLOBEX_NORMAL_1700_PREVIOUS_DAY_TO_1600_CT"
    assert nq.expiration_policy.startswith(
        "LISTED_NQ_TERMINATES_AT_NASDAQ_REGULAR_OPEN"
    )
    assert nq.provider_mapping_policy.endswith("UNVERIFIED")
    assert "current_exact_nq_intraday_halt_schedule" in nq.unresolved_rules
    assert "listed_contract_expiration_boundary" not in nq.unresolved_rules
    assert "yahoo_continuous_symbol_to_exchange_trade_date" in nq.unresolved_rules


def test_unsupported_provider_routes_never_borrow_cash_or_ui_boundaries() -> None:
    for venue in (
        MarketVenue.CME_EQUITY_INDEX_FUTURES,
        MarketVenue.NYMEX_WTI_FUTURES,
        MarketVenue.CFE_VIX_FUTURES,
        MarketVenue.CBOE_SPOT_VIX,
    ):
        contract = market_session_contract(venue)
        assert contract.status is SessionRuleStatus.EVIDENCE_REQUIRED
        assert contract.calendar_name is None
        assert not contract.ui_anchor_is_exchange_boundary
        assert contract.provider_mapping_policy != "CALLER_SPECIFIC_FAIL_CLOSED"
        with pytest.raises(UnsupportedMarketSessionError):
            MarketSessionService(venue)

    assert "XNYS" not in market_session_contract(
        MarketVenue.CME_EQUITY_INDEX_FUTURES
    ).provider_mapping_policy
    assert "XNYS" not in market_session_contract(
        MarketVenue.NYMEX_WTI_FUTURES
    ).provider_mapping_policy


def test_legacy_checkpoint_session_identity_matches_shared_regular_service() -> None:
    legacy_kr = ExchangeTradingCalendar(ExchangeMarket.KR)
    shared_kr = MarketSessionService(MarketVenue.XKRX_CASH)
    legacy_us = ExchangeTradingCalendar(ExchangeMarket.US)
    shared_us = MarketSessionService(MarketVenue.XNYS_CASH)
    clock = datetime(2026, 8, 20, 2, 0, tzinfo=timezone.utc)

    assert legacy_kr.latest_completed_session(clock) == shared_kr.latest_completed_trade_date(clock)
    assert legacy_us.latest_completed_session(clock) == shared_us.latest_completed_trade_date(clock)


def test_persisted_calendar_version_mismatch_fails_before_recomputing_identity() -> None:
    current = MarketSessionService(MarketVenue.XNYS_CASH)
    same = MarketSessionService(
        MarketVenue.XNYS_CASH,
        required_contract_version=current.contract.version_key,
    )
    assert same.contract.version_key == current.contract.version_key
    with pytest.raises(MarketSessionVersionMismatchError, match="persisted eligibility"):
        MarketSessionService(
            MarketVenue.XNYS_CASH,
            required_contract_version="market-session-v0|obsolete",
        )


def test_cash_compatibility_facade_delegates_every_session_decision() -> None:
    legacy = ExchangeTradingCalendar(ExchangeMarket.US)
    shared = legacy._service
    assert isinstance(shared, MarketSessionService)
    assert not hasattr(legacy, "_calendar")
    assert shared.contract.venue is MarketVenue.XNYS_CASH
    assert legacy.provenance.calendar_name == shared.contract.calendar_name
    assert legacy.provenance.source_version == shared.contract.source_version

    start, end = date(2026, 7, 1), date(2026, 7, 8)
    assert legacy.sessions_in_range(start, end) == shared.sessions_in_range(start, end)
    assert legacy.previous_trading_day(start) == shared.previous_trade_date(start)
    assert legacy.next_trading_day(end) == shared.next_trade_date(end)

    session = date(2026, 7, 2)
    window = shared.session_window(session)
    assert legacy.session_open(session) == window.open
    assert legacy.session_close(session) == window.close
    clock = datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc)
    assert legacy.latest_completed_session(clock) == shared.latest_completed_trade_date(clock)
