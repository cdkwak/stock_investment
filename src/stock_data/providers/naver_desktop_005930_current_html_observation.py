"""Strict body-bound contract for the UR-174 Naver desktop 005930 pilot."""

from __future__ import annotations

import html as html_module
import json
import math
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)


KST = ZoneInfo("Asia/Seoul")
IDENTITY = ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930")
ROUTE_ID = "naver-desktop-html-current:XKRX:005930"
SOURCE_ROUTE = "NAVER_FINANCE_WEB:finance.naver.com/item/main.naver?code=005930"
_SCRIPT_ID = "naver-current-observation"
_SCRIPT_PATTERN = re.compile(
    rf"<script\b[^>]*\bid=[\"']{_SCRIPT_ID}[\"'][^>]*\btype=[\"']application/json[\"'][^>]*>(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)


class NaverDesktopHtmlObservationError(ValueError):
    """The returned HTML did not directly establish the exact quote contract."""


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise NaverDesktopHtmlObservationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _body_payload(body: bytes) -> dict[str, Any]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise NaverDesktopHtmlObservationError("desktop HTML is not UTF-8") from error
    matches = _SCRIPT_PATTERN.findall(text)
    if len(matches) != 1:
        raise NaverDesktopHtmlObservationError("required body-bound observation schema is missing")
    try:
        payload = json.loads(html_module.unescape(matches[0]).strip())
    except json.JSONDecodeError as error:
        raise NaverDesktopHtmlObservationError("body-bound observation schema is malformed") from error
    required = {"symbol", "venue", "price", "unit", "provider_timestamp", "session", "delay_seconds"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise NaverDesktopHtmlObservationError("body-bound observation schema keys differ")
    return payload


def _finite_price(value: Any) -> float:
    if isinstance(value, bool):
        raise NaverDesktopHtmlObservationError("price must be finite")
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError) as error:
        raise NaverDesktopHtmlObservationError("price must be numeric") from error
    if not math.isfinite(parsed):
        raise NaverDesktopHtmlObservationError("price must be finite")
    return parsed


def _provider_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise NaverDesktopHtmlObservationError("provider timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise NaverDesktopHtmlObservationError("provider timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise NaverDesktopHtmlObservationError("provider timestamp must be timezone-aware")
    if parsed.astimezone(KST).utcoffset() != KST.utcoffset(parsed):
        raise NaverDesktopHtmlObservationError("provider timestamp must carry a Korea time-zone offset")
    return parsed.astimezone(timezone.utc)


def naver_desktop_005930_html_quote(body: bytes, *, retrieved_at: datetime) -> SourceObservation[CurrentObservation]:
    """Adapt only a directly bound same-body HTML schema; never infer fields."""
    payload = _body_payload(body)
    if payload["symbol"] != IDENTITY.symbol:
        raise NaverDesktopHtmlObservationError("body symbol differs from exact route")
    if payload["venue"] != "KRX":
        raise NaverDesktopHtmlObservationError("body venue must be KRX")
    if payload["unit"] != "KRW per share":
        raise NaverDesktopHtmlObservationError("body unit must be KRW per share")
    if payload["session"] not in {"OPEN", "REGULAR"}:
        raise NaverDesktopHtmlObservationError("body session must be OPEN or REGULAR")
    if not isinstance(payload["delay_seconds"], int) or isinstance(payload["delay_seconds"], bool):
        raise NaverDesktopHtmlObservationError("body delay_seconds must be an integer")
    if payload["delay_seconds"] != 0:
        raise NaverDesktopHtmlObservationError("body delayed quote is not eligible")
    retrieved_at_utc = _utc(retrieved_at, "retrieved_at")
    provider_at_utc = _provider_timestamp(payload["provider_timestamp"])
    observation = CurrentObservation(
        route_id=ROUTE_ID,
        identity=IDENTITY,
        interval=ObservationInterval.SNAPSHOT,
        value=_finite_price(payload["price"]),
        unit="KRW per share",
        provider="NAVER_FINANCE_WEB",
        upstream_provider="NAVER_FINANCE_WEB",
        source_route=SOURCE_ROUTE,
        provider_timestamp_utc=provider_at_utc.isoformat(),
        retrieved_at_utc=retrieved_at_utc.isoformat(),
        finality=ObservationFinality.PROVISIONAL,
    )
    observation.validate()
    return SourceObservation(observation, SourceProvenance(
        provider=observation.provider,
        upstream_provider=observation.upstream_provider,
        source_route=observation.source_route,
        retrieved_at_utc=observation.retrieved_at_utc,
        request_count=1,
    ))


def naver_desktop_005930_html_route() -> CurrentObservationRoute:
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=ROUTE_ID,
            primary_provider="NAVER_FINANCE_WEB",
            primary_route=SOURCE_ROUTE,
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=IDENTITY,
        interval_precedence=(ObservationInterval.SNAPSHOT,),
    )


__all__ = [
    "IDENTITY", "ROUTE_ID", "SOURCE_ROUTE", "NaverDesktopHtmlObservationError",
    "naver_desktop_005930_html_quote", "naver_desktop_005930_html_route",
]
