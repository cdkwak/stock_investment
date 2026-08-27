"""Fail-closed KST policy for typed Korean-equity NXT observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar


KST = ZoneInfo("Asia/Seoul")
NXT_CLOSE_START = time(19, 55)
NXT_CLOSE_END = time(20, 0)
_NXT_CLOSE = "\uB9C8\uAC10"
_TIME_WINDOW_INFERENCE = "\uC2DC\uAC04\uCC3D \uCD94\uB860"
_PREVIOUS_SESSION_CLOSE = "\uC7A5\uB9C8\uAC10"


@dataclass(frozen=True)
class KoreanEquityNxtGateDecision:
    allow_value: bool
    freshness: str
    reason: str
    visible_label: str | None


def classify_korean_equity_nxt_timestamp(
    *,
    provider_timestamp_utc: str | None,
    now_utc: datetime,
    session_start_kst: time | None,
    venue_inferred: bool = False,
) -> KoreanEquityNxtGateDecision:
    """Classify an exact local observation without inferring an active session.

    A route must explicitly supply an active-session start. A post-close-only
    route may omit it, in which case it is only eligible for the narrow same-day
    19:55--20:00 KST close label and can never be presented as realtime.
    """
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("NXT gate clock must be timezone-aware")
    if session_start_kst is not None and (
        not isinstance(session_start_kst, time) or not session_start_kst < NXT_CLOSE_END
    ):
        raise ValueError("NXT session start must precede the 20:00 close")
    if not provider_timestamp_utc:
        return KoreanEquityNxtGateDecision(False, "CURRENT_GATE_BLOCKED", "NXT_PROVIDER_TIMESTAMP_MISSING", None)
    try:
        source = datetime.fromisoformat(provider_timestamp_utc.replace("Z", "+00:00"))
    except ValueError:
        return KoreanEquityNxtGateDecision(False, "CURRENT_GATE_BLOCKED", "NXT_PROVIDER_TIMESTAMP_INVALID", None)
    if source.tzinfo is None or source.utcoffset() is None:
        return KoreanEquityNxtGateDecision(False, "CURRENT_GATE_BLOCKED", "NXT_PROVIDER_TIMESTAMP_NAIVE", None)

    now = now_utc.astimezone(KST)
    source_kst = source.astimezone(KST)
    if source_kst > now:
        return KoreanEquityNxtGateDecision(False, "CURRENT_GATE_BLOCKED", "NXT_PROVIDER_TIMESTAMP_FUTURE", None)
    source_time = source_kst.timetz().replace(tzinfo=None)
    if source_kst.date() != now.date():
        calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
        if not calendar.is_trading_day(now.date()):
            latest_completed = calendar.latest_completed_session(now)
            if (
                source_kst.date() == latest_completed
                and NXT_CLOSE_START <= source_time <= NXT_CLOSE_END
            ):
                close_label = (
                    f"NXT {_NXT_CLOSE}({_TIME_WINDOW_INFERENCE})"
                    if venue_inferred else f"NXT {_NXT_CLOSE}"
                )
                return KoreanEquityNxtGateDecision(
                    True,
                    "MARKET_CLOSED_LAST_FINAL",
                    "NXT_MARKET_CLOSED_LAST_FINAL_FROM_VERIFIED_CLOSE_WINDOW",
                    f"{_PREVIOUS_SESSION_CLOSE} · {close_label} "
                    f"{source_kst.strftime('%Y-%m-%d %H:%M:%S')}",
                )
        return KoreanEquityNxtGateDecision(False, "CURRENT_GATE_BLOCKED", "NXT_SOURCE_DATE_NOT_TODAY_KST", None)
    now_time = now.timetz().replace(tzinfo=None)
    if now_time < NXT_CLOSE_END:
        if session_start_kst is None:
            return KoreanEquityNxtGateDecision(False, "CURRENT_GATE_BLOCKED", "NXT_ACTIVE_SESSION_START_UNRESOLVED", None)
        if now_time < session_start_kst:
            return KoreanEquityNxtGateDecision(False, "CURRENT_GATE_BLOCKED", "NXT_SESSION_NOT_ACTIVE_YET", None)
        if now - source_kst > timedelta(minutes=60):
            return KoreanEquityNxtGateDecision(False, "CURRENT_GATE_BLOCKED", "NXT_ACTIVE_SOURCE_AGE_OVER_60M", None)
        return KoreanEquityNxtGateDecision(True, "CURRENT_NXT_ACTIVE", "NXT_ACTIVE_TODAY_KST_SOURCE_AGE_LE_60M", None)

    if not NXT_CLOSE_START <= source_time <= NXT_CLOSE_END:
        return KoreanEquityNxtGateDecision(False, "CURRENT_GATE_BLOCKED", "NXT_CLOSE_TIMESTAMP_OUTSIDE_1955_2000_KST", None)
    if venue_inferred:
        return KoreanEquityNxtGateDecision(
            True,
            "NXT_SESSION_CLOSE_INFERRED",
            "TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
            f"NXT {_NXT_CLOSE}({_TIME_WINDOW_INFERENCE}) {source_kst.strftime('%H:%M:%S')}",
        )
    return KoreanEquityNxtGateDecision(
        True,
        "NXT_SESSION_CLOSE",
        "NXT_SESSION_CLOSE_SAME_DATE_1955_2000_KST",
        f"NXT {_NXT_CLOSE} {source_kst.strftime('%H:%M:%S')}",
    )


__all__ = [
    "KST", "KoreanEquityNxtGateDecision", "NXT_CLOSE_END", "NXT_CLOSE_START",
    "classify_korean_equity_nxt_timestamp",
]
