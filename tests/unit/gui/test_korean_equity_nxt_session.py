from datetime import datetime, time, timezone

import pytest

from stock_data.gui.korean_equity_nxt_session import classify_korean_equity_nxt_timestamp


SESSION_START = time(8, 0)
INFERRED_LABEL = "NXT \uB9C8\uAC10(\uC2DC\uAC04\uCC3D \uCD94\uB860) 19:59:59"


def _utc(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 21, hour, minute, second, tzinfo=timezone.utc)


def test_active_nxt_session_requires_today_kst_timestamp_no_older_than_60_minutes() -> None:
    accepted = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T00:35:00+00:00", now_utc=_utc(1, 30), session_start_kst=SESSION_START,
    )
    stale = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T00:29:00+00:00", now_utc=_utc(1, 30), session_start_kst=SESSION_START,
    )
    assert accepted.allow_value and accepted.freshness == "CURRENT_NXT_ACTIVE"
    assert accepted.visible_label is None
    assert not stale.allow_value and stale.reason == "NXT_ACTIVE_SOURCE_AGE_OVER_60M"


def test_post_close_accepts_only_same_date_exact_close_window_with_non_realtime_label() -> None:
    close = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T10:57:12+00:00", now_utc=_utc(11, 3), session_start_kst=SESSION_START,
    )
    early = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T10:54:00+00:00", now_utc=_utc(11, 3), session_start_kst=SESSION_START,
    )
    late = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T11:01:00+00:00", now_utc=_utc(11, 3), session_start_kst=SESSION_START,
    )
    assert close.allow_value and close.freshness == "NXT_SESSION_CLOSE"
    assert close.visible_label == "NXT \uB9C8\uAC10 19:57:12"
    assert not early.allow_value and early.reason == "NXT_CLOSE_TIMESTAMP_OUTSIDE_1955_2000_KST"
    assert not late.allow_value and late.reason == "NXT_CLOSE_TIMESTAMP_OUTSIDE_1955_2000_KST"


def test_route_local_exclusive_time_window_inference_is_visible_and_never_live() -> None:
    inferred = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T10:59:59+00:00", now_utc=_utc(11, 3), session_start_kst=SESSION_START,
        venue_inferred=True,
    )
    assert inferred.allow_value
    assert inferred.freshness == "NXT_SESSION_CLOSE_INFERRED"
    assert inferred.reason == "TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW"
    assert inferred.visible_label == INFERRED_LABEL


def test_post_close_only_contract_never_invents_an_active_session_start() -> None:
    close = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T10:59:59+00:00", now_utc=_utc(11, 3), session_start_kst=None,
        venue_inferred=True,
    )
    active = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T01:00:00+00:00", now_utc=_utc(1, 30), session_start_kst=None,
    )
    assert close.allow_value and close.freshness == "NXT_SESSION_CLOSE_INFERRED"
    assert not active.allow_value and active.reason == "NXT_ACTIVE_SESSION_START_UNRESOLVED"


def test_market_closed_date_uses_only_latest_completed_session_close_as_not_live() -> None:
    prior_close = "2026-08-21T10:58:00+00:00"
    saturday = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc=prior_close, now_utc=datetime(2026, 8, 22, 0, 30, tzinfo=timezone.utc), session_start_kst=SESSION_START,
        venue_inferred=True,
    )
    assert saturday.allow_value
    assert saturday.freshness == "MARKET_CLOSED_LAST_FINAL"
    assert saturday.reason == "NXT_MARKET_CLOSED_LAST_FINAL_FROM_VERIFIED_CLOSE_WINDOW"
    assert saturday.visible_label == "장마감 · NXT 마감(시간창 추론) 2026-08-21 19:58:00"


def test_market_closed_date_rejects_nonlatest_or_outside_close_window() -> None:
    nonlatest = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-20T10:58:00+00:00",
        now_utc=datetime(2026, 8, 22, 0, 30, tzinfo=timezone.utc),
        session_start_kst=SESSION_START,
    )
    outside = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T10:54:00+00:00",
        now_utc=datetime(2026, 8, 22, 0, 30, tzinfo=timezone.utc),
        session_start_kst=SESSION_START,
    )
    assert not nonlatest.allow_value and nonlatest.reason == "NXT_SOURCE_DATE_NOT_TODAY_KST"
    assert not outside.allow_value and outside.reason == "NXT_SOURCE_DATE_NOT_TODAY_KST"


def test_next_trading_session_preopen_never_reuses_prior_nxt_close_as_current() -> None:
    prior_close = "2026-08-21T10:58:00+00:00"
    preopen = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc=prior_close,
        now_utc=datetime(2026, 8, 23, 22, 55, tzinfo=timezone.utc),
        session_start_kst=SESSION_START,
    )
    assert not preopen.allow_value and preopen.reason == "NXT_SOURCE_DATE_NOT_TODAY_KST"


def test_rejects_missing_invalid_future_and_unconfigured_session_start() -> None:
    missing = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc=None, now_utc=_utc(1, 30), session_start_kst=SESSION_START,
    )
    future = classify_korean_equity_nxt_timestamp(
        provider_timestamp_utc="2026-08-21T01:31:00+00:00", now_utc=_utc(1, 30), session_start_kst=SESSION_START,
    )
    assert missing.reason == "NXT_PROVIDER_TIMESTAMP_MISSING"
    assert future.reason == "NXT_PROVIDER_TIMESTAMP_FUTURE"
    with pytest.raises(ValueError, match="session start"):
        classify_korean_equity_nxt_timestamp(
            provider_timestamp_utc="2026-08-21T01:00:00+00:00", now_utc=_utc(1, 30), session_start_kst=time(20, 0),
        )
