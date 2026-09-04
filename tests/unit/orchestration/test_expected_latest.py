from datetime import date, datetime, timezone

from stock_data.orchestration.expected_latest import (
    ExpectedFreshness, ExpectedLagPolicy, ObservationCalendar,
    ProviderAvailabilityPolicy,
    resolve_expected_latest,
)


AS_OF_BEFORE_H15 = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)


def test_fred_series_have_independent_provider_targets() -> None:
    retained = date(2026, 8, 14)
    yields = resolve_expected_latest(
        dataset="fred_treasury_yield_daily", lane="FRED_DAILY",
        retained_latest=retained, as_of=AS_OF_BEFORE_H15,
    )
    fx = resolve_expected_latest(
        dataset="fred_usd_fx_daily", lane="FRED_DAILY",
        retained_latest=retained, as_of=AS_OF_BEFORE_H15,
    )
    vix = resolve_expected_latest(
        dataset="fred_vix_daily", lane="FRED_DAILY",
        retained_latest=retained, as_of=AS_OF_BEFORE_H15,
    )
    assert yields.expected_available_observation == date(2026, 8, 14)
    assert yields.freshness is ExpectedFreshness.EXPECTED_LAG
    assert yields.observation_calendar is ObservationCalendar.PROVIDER_BUSINESS_DAY
    assert fx.expected_available_observation == date(2026, 8, 14)
    assert fx.provider_availability_policy is ProviderAvailabilityPolicy.FRED_H10_WEEKLY_1615_ET
    assert vix.expected_available_observation == date(2026, 8, 17)
    assert vix.freshness is ExpectedFreshness.STALE


def test_fred_vix_is_pending_until_2300_kst_after_0840_ct_release() -> None:
    after_release_before_next_run = resolve_expected_latest(
        dataset="fred_vix_daily", lane="FRED_DAILY",
        retained_latest=date(2026, 8, 24),
        as_of=datetime(2026, 8, 26, 13, 40, tzinfo=timezone.utc),
    )
    at_next_run = resolve_expected_latest(
        dataset="fred_vix_daily", lane="FRED_DAILY",
        retained_latest=date(2026, 8, 24),
        as_of=datetime(2026, 8, 26, 21, 0, tzinfo=timezone.utc),
    )

    assert after_release_before_next_run.expected_available_observation == date(2026, 8, 25)
    assert after_release_before_next_run.freshness is ExpectedFreshness.CURRENT
    assert after_release_before_next_run.pending_until == "23:00"
    assert at_next_run.expected_available_observation == date(2026, 8, 25)
    assert at_next_run.freshness is ExpectedFreshness.STALE
    assert at_next_run.collection_required


def test_h15_target_advances_only_after_official_release_clock() -> None:
    after = datetime(2026, 8, 18, 20, 16, tzinfo=timezone.utc)
    result = resolve_expected_latest(
        dataset="fred_treasury_yield_daily", lane="FRED_DAILY",
        retained_latest=date(2026, 8, 14), as_of=after,
    )
    assert result.expected_available_observation == date(2026, 8, 17)
    assert result.freshness is ExpectedFreshness.CURRENT
    assert result.pending_until == "06:35"
    assert result.collection_required is False


def test_yahoo_continuous_futures_wait_for_next_business_day_completed_bar() -> None:
    before_release = resolve_expected_latest(
        dataset="global_commodity_futures_daily", lane="GLOBAL_COMMODITY_DAILY",
        retained_latest=date(2026, 8, 17),
        as_of=datetime(2026, 8, 18, 20, 27, tzinfo=timezone.utc),
    )
    assert before_release.expected_available_observation == date(2026, 8, 17)
    assert before_release.freshness is ExpectedFreshness.EXPECTED_LAG
    assert before_release.provider_availability_policy is (
        ProviderAvailabilityPolicy.YAHOO_FUTURES_NEXT_BUSINESS_DAY_0800_ET
    )

    after_release = resolve_expected_latest(
        dataset="global_commodity_futures_daily", lane="GLOBAL_COMMODITY_DAILY",
        retained_latest=date(2026, 8, 17),
        as_of=datetime(2026, 8, 19, 12, 1, tzinfo=timezone.utc),
    )
    assert after_release.expected_available_observation == date(2026, 8, 18)
    assert after_release.freshness is ExpectedFreshness.CURRENT
    assert after_release.pending_until == "22:25"


def test_short_trading_t_plus_one_uses_next_xkrx_session_across_weekend() -> None:
    saturday = datetime(2026, 8, 22, 3, 0, tzinfo=timezone.utc)
    result = resolve_expected_latest(
        dataset="kr_short_selling_trading_daily", lane="SHORT_SELLING_DAILY",
        retained_latest=date(2026, 8, 20), as_of=saturday,
    )
    assert result.expected_available_observation == date(2026, 8, 20)
    monday = resolve_expected_latest(
        dataset="kr_short_selling_trading_daily", lane="SHORT_SELLING_DAILY",
        retained_latest=date(2026, 8, 20),
        as_of=datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc),
    )
    assert monday.expected_available_observation == date(2026, 8, 21)
    assert monday.provider_availability_policy is ProviderAvailabilityPolicy.KRX_SHORT_TRADING_T_PLUS_1


def test_short_trading_t_plus_one_advances_at_the_0910_scheduler_boundary() -> None:
    before = resolve_expected_latest(
        dataset="kr_short_selling_trading_daily", lane="SHORT_SELLING_DAILY",
        retained_latest=date(2026, 8, 21),
        as_of=datetime(2026, 8, 25, 0, 9, 59, tzinfo=timezone.utc),
    )
    assert before.expected_market_date == date(2026, 8, 24)
    assert before.expected_available_observation == date(2026, 8, 21)
    assert before.freshness is ExpectedFreshness.EXPECTED_LAG
    assert before.collection_required is False

    at_release = resolve_expected_latest(
        dataset="kr_short_selling_trading_daily", lane="SHORT_SELLING_DAILY",
        retained_latest=date(2026, 8, 21),
        as_of=datetime(2026, 8, 25, 0, 10, tzinfo=timezone.utc),
    )
    assert at_release.expected_available_observation == date(2026, 8, 24)
    assert at_release.freshness is ExpectedFreshness.CURRENT
    assert at_release.pending_until == "09:25"
    assert at_release.collection_required is False

    after_release = resolve_expected_latest(
        dataset="kr_short_selling_trading_daily", lane="SHORT_SELLING_DAILY",
        retained_latest=date(2026, 8, 21),
        as_of=datetime(2026, 8, 25, 0, 25, tzinfo=timezone.utc),
    )
    assert after_release.expected_available_observation == date(2026, 8, 24)
    assert after_release.freshness is ExpectedFreshness.STALE


def test_short_balance_and_investor_use_distinct_official_release_clocks() -> None:
    as_of = datetime(2026, 8, 27, 11, 30, tzinfo=timezone.utc)
    balance = resolve_expected_latest(
        dataset="kr_short_selling_balance_daily",
        lane="SHORT_SELLING_BALANCE_DAILY",
        retained_latest=date(2026, 8, 22),
        as_of=as_of,
    )
    investor = resolve_expected_latest(
        dataset="kr_short_selling_investor_daily",
        lane="SHORT_SELLING_INVESTOR_DAILY",
        retained_latest=date(2026, 8, 26),
        as_of=as_of,
    )

    assert balance.expected_available_observation == date(2026, 8, 25)
    assert balance.provider_availability_policy is (
        ProviderAvailabilityPolicy.KRX_SHORT_BALANCE_T_PLUS_2_1810
    )
    assert investor.expected_available_observation == date(2026, 8, 27)
    assert investor.provider_availability_policy is (
        ProviderAvailabilityPolicy.KRX_SHORT_INVESTOR_SAME_DAY_1810
    )


def test_credit_balance_uses_two_provider_business_day_expectation() -> None:
    as_of = datetime(2026, 9, 3, 11, 30, tzinfo=timezone.utc)
    expected = resolve_expected_latest(
        dataset="kr_credit_balance_daily",
        lane="LIQUIDITY_CREDIT_DAILY",
        retained_latest=date(2026, 9, 1),
        as_of=as_of,
    )
    late = resolve_expected_latest(
        dataset="kr_credit_balance_daily",
        lane="LIQUIDITY_CREDIT_DAILY",
        retained_latest=date(2026, 8, 31),
        as_of=as_of,
    )

    assert expected.expected_market_date == date(2026, 9, 3)
    assert expected.expected_available_observation == date(2026, 9, 1)
    assert expected.freshness is ExpectedFreshness.EXPECTED_LAG
    assert expected.expected_lag_policy is ExpectedLagPolicy.TWO_PROVIDER_BUSINESS_DAYS
    assert expected.provider_availability_policy is (
        ProviderAvailabilityPolicy.KOFIA_T_PLUS_2_2030
    )
    assert late.freshness is ExpectedFreshness.CURRENT
    assert late.pending_until == "20:45"
    assert late.collection_required is False


def test_market_liquidity_keeps_manual_policy_without_matching_retained_lag() -> None:
    result = resolve_expected_latest(
        dataset="kr_market_liquidity_daily",
        lane="LIQUIDITY_CREDIT_DAILY",
        retained_latest=date(2026, 8, 6),
        as_of=datetime(2026, 9, 3, 11, 30, tzinfo=timezone.utc),
    )

    assert result.expected_lag_policy is ExpectedLagPolicy.MANUAL
    assert result.freshness is ExpectedFreshness.STALE


def test_toss_korean_treasury_uses_completed_successor_session() -> None:
    result = resolve_expected_latest(
        dataset="kr_treasury_yield_daily",
        lane="TOSS_KR_TREASURY_DAILY",
        retained_latest=date(2026, 8, 25),
        as_of=datetime(2026, 8, 27, 11, 30, tzinfo=timezone.utc),
    )

    assert result.expected_available_observation == date(2026, 8, 26)
    assert result.provider_availability_policy is (
        ProviderAvailabilityPolicy.KRX_COMPLETED_SUCCESSOR_SESSION
    )


def test_derivatives_price_waits_for_a_completed_successor_session() -> None:
    result = resolve_expected_latest(
        dataset="kr_kospi200_option_pcr_daily",
        lane="DERIVATIVES_PRICE_DAILY",
        retained_latest=date(2026, 8, 25),
        as_of=datetime(2026, 8, 26, 14, 24, tzinfo=timezone.utc),
    )

    assert result.expected_market_date == date(2026, 8, 26)
    assert result.expected_available_observation == date(2026, 8, 25)
    assert result.freshness is ExpectedFreshness.EXPECTED_LAG
    assert result.collection_required is False
    assert result.provider_availability_policy is (
        ProviderAvailabilityPolicy.KRX_COMPLETED_SUCCESSOR_SESSION
    )


def test_canonical_equity_uses_data_go_kr_d_plus_one_1300_gate() -> None:
    before = resolve_expected_latest(
        dataset="kr_equity_canonical_universe_daily",
        lane="CANONICAL_EQUITY_DAILY",
        retained_latest=date(2026, 8, 13),
        as_of=datetime(2026, 8, 24, 3, 59, 59, tzinfo=timezone.utc),
    )
    at_release = resolve_expected_latest(
        dataset="kr_equity_canonical_universe_daily",
        lane="CANONICAL_EQUITY_DAILY",
        retained_latest=date(2026, 8, 13),
        as_of=datetime(2026, 8, 24, 4, 0, 0, tzinfo=timezone.utc),
    )
    assert before.expected_available_observation == date(2026, 8, 20)
    assert at_release.expected_available_observation == date(2026, 8, 21)
    assert at_release.provider_availability_policy is (
        ProviderAvailabilityPolicy.DATA_GO_KR_D_PLUS_1_1300
    )


def test_kospi200_breadth_follows_canonical_accepted_d_plus_one_gate() -> None:
    result = resolve_expected_latest(
        dataset="kr_kospi200_breadth_daily",
        lane="KOSPI200_BREADTH_DAILY",
        retained_latest=date(2026, 8, 25),
        as_of=datetime(2026, 8, 26, 13, 35, tzinfo=timezone.utc),
    )
    assert result.expected_available_observation == date(2026, 8, 25)
    assert result.freshness is ExpectedFreshness.EXPECTED_LAG
    assert result.provider_availability_policy is (
        ProviderAvailabilityPolicy.CANONICAL_EQUITY_ACCEPTED_D_PLUS_1_1300
    )


def test_bok_treasury_expected_latest_is_unknown_without_publication_evidence() -> None:
    result = resolve_expected_latest(
        dataset="bok_ecos_kr_treasury_yield_source_observation",
        lane="KR_TREASURY_DAILY",
        retained_latest=date(2026, 8, 13),
        as_of=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )
    assert result.observation_calendar is ObservationCalendar.PROVIDER_PUBLICATION
    assert result.expected_market_date is None
    assert result.expected_available_observation is None
    assert result.freshness is ExpectedFreshness.UNKNOWN
    assert result.collection_required is False


def test_bok_fx_uses_1600_kst_target_and_one_day_expected_lag() -> None:
    before = resolve_expected_latest(
        dataset="bok_ecos_usd_krw_daily", lane="BOK_FX_DAILY",
        retained_latest=date(2026, 9, 2),
        as_of=datetime(2026, 9, 3, 6, 59, tzinfo=timezone.utc),
    )
    after = resolve_expected_latest(
        dataset="bok_ecos_usd_krw_daily", lane="BOK_FX_DAILY",
        retained_latest=date(2026, 9, 2),
        as_of=datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc),
    )

    assert before is not None and after is not None
    assert before.expected_available_observation == date(2026, 9, 2)
    assert before.freshness is ExpectedFreshness.CURRENT
    assert after.expected_available_observation == date(2026, 9, 3)
    assert after.freshness is ExpectedFreshness.CURRENT
    assert after.pending_until == "20:45"
    assert after.provider_availability_policy is (
        ProviderAvailabilityPolicy.BOK_ECOS_FX_DAILY_1600_KST
    )


def test_provisional_equity_uses_same_session_only_at_2030() -> None:
    before = resolve_expected_latest(
        dataset="kr_equity_price_provisional_daily",
        lane="KR_EQUITY_PROVISIONAL_DAILY",
        retained_latest=date(2026, 9, 2),
        as_of=datetime(2026, 9, 3, 11, 29, tzinfo=timezone.utc),
    )
    after = resolve_expected_latest(
        dataset="kr_equity_price_provisional_daily",
        lane="KR_EQUITY_PROVISIONAL_DAILY",
        retained_latest=date(2026, 9, 2),
        as_of=datetime(2026, 9, 3, 11, 30, tzinfo=timezone.utc),
    )

    assert before is not None and after is not None
    assert before.expected_available_observation == date(2026, 9, 2)
    assert after.expected_available_observation == date(2026, 9, 3)
    assert after.provider_availability_policy is ProviderAvailabilityPolicy.KRX_POST_CLOSE_2030
    assert after.pending_until == "20:45"


def test_equity_investor_flow_uses_same_session_at_2030() -> None:
    result = resolve_expected_latest(
        dataset="kr_equity_investor_flow_daily",
        lane="KR_EQUITY_INVESTOR_FLOW_DAILY",
        retained_latest=date(2026, 9, 3),
        as_of=datetime.fromisoformat("2026-09-04T20:30:00+09:00"),
    )

    assert result is not None
    assert result.expected_available_observation == date(2026, 9, 4)
    assert result.provider_availability_policy is ProviderAvailabilityPolicy.KRX_POST_CLOSE_2030


def test_kr_daily_gap_is_pending_at_1700_and_late_at_2100_kst() -> None:
    before_due = resolve_expected_latest(
        dataset="kr_kospi200_option_pcr_daily",
        lane="DERIVATIVES_PRICE_DAILY",
        retained_latest=date(2026, 9, 2),
        as_of=datetime.fromisoformat("2026-09-04T17:00:00+09:00"),
    )
    after_due = resolve_expected_latest(
        dataset="kr_kospi200_option_pcr_daily",
        lane="DERIVATIVES_PRICE_DAILY",
        retained_latest=date(2026, 9, 2),
        as_of=datetime.fromisoformat("2026-09-04T21:00:00+09:00"),
    )
    older_backlog = resolve_expected_latest(
        dataset="kr_kospi200_option_pcr_daily",
        lane="DERIVATIVES_PRICE_DAILY",
        retained_latest=date(2026, 9, 1),
        as_of=datetime.fromisoformat("2026-09-04T17:00:00+09:00"),
    )

    assert before_due is not None and after_due is not None and older_backlog is not None
    assert before_due.expected_available_observation == date(2026, 9, 3)
    assert before_due.due_at == datetime.fromisoformat("2026-09-04T20:45:00+09:00")
    assert before_due.freshness is ExpectedFreshness.CURRENT
    assert before_due.pending_until == "20:45"
    assert after_due.freshness is ExpectedFreshness.STALE
    assert after_due.pending_until is None
    assert older_backlog.freshness is ExpectedFreshness.STALE
    assert older_backlog.pending_until is None


def test_us_daily_gap_is_pending_at_0530_and_late_at_0700_kst() -> None:
    before_due = resolve_expected_latest(
        dataset="global_index_price_daily",
        lane="GLOBAL_INDEX_DAILY",
        retained_latest=date(2026, 9, 2),
        as_of=datetime.fromisoformat("2026-09-04T05:30:00+09:00"),
    )
    after_due = resolve_expected_latest(
        dataset="global_index_price_daily",
        lane="GLOBAL_INDEX_DAILY",
        retained_latest=date(2026, 9, 2),
        as_of=datetime.fromisoformat("2026-09-04T07:00:00+09:00"),
    )

    assert before_due is not None and after_due is not None
    assert before_due.expected_available_observation == date(2026, 9, 3)
    assert before_due.due_at == datetime.fromisoformat("2026-09-04T06:35:00+09:00")
    assert before_due.freshness is ExpectedFreshness.CURRENT
    assert before_due.pending_until == "06:35"
    assert after_due.freshness is ExpectedFreshness.STALE


def test_weekend_uses_the_previous_us_session_expectation() -> None:
    saturday = resolve_expected_latest(
        dataset="global_index_price_daily",
        lane="GLOBAL_INDEX_DAILY",
        retained_latest=date(2026, 9, 3),
        as_of=datetime.fromisoformat("2026-09-05T05:30:00+09:00"),
    )

    assert saturday is not None
    assert saturday.expected_available_observation == date(2026, 9, 4)
    assert saturday.freshness is ExpectedFreshness.CURRENT
    assert saturday.pending_until == "06:35"


def test_manual_dataset_has_no_due_time_or_pending_state() -> None:
    result = resolve_expected_latest(
        dataset="kr_equity_foreign_ownership_daily",
        lane="NO_SCHEDULER_LANE",
        retained_latest=date(2026, 9, 2),
        as_of=datetime.fromisoformat("2026-09-04T17:00:00+09:00"),
    )

    assert result is not None
    assert result.freshness is ExpectedFreshness.STALE
    assert result.due_at is None
    assert result.pending_until is None
