"""Strict retained-SSR parser for the Naver mobile-home summary.

It is transport-free.  The undocumented public-web page can only produce local
personal, display-only observations after a caller supplies verified retained
bytes and a recovery clock.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

from stock_data.orchestration.automatic_fallback import RoutePolicy, SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import CurrentObservation, CurrentObservationRoute, ObservationFinality, ObservationIdentity, ObservationInterval

KST = ZoneInfo("Asia/Seoul")
_MAX_AGE = timedelta(minutes=60)
_SPECS = {
    "KOSPI": (ObservationIdentity("KR_INDEX_CURRENT", "XKRX", "KOSPI"), "index points", "/domestic/index/KOSPI/total"),
    "KOSDAQ": (ObservationIdentity("KR_INDEX_CURRENT", "XKRX", "KOSDAQ"), "index points", "/domestic/index/KOSDAQ/total"),
    "FX_USDKRW": (ObservationIdentity("FX_CURRENT", "KRW", "USD_KRW"), "KRW per USD", "/marketindex/exchange/FX_USDKRW"),
    "GCcv1": (ObservationIdentity("COMMODITY_CURRENT", "CMX", "GOLD"), None, "/marketindex/metals/GCcv1"),
    "CLcv1": (ObservationIdentity("COMMODITY_CURRENT", "NYMEX", "WTI"), None, "/marketindex/energy/CLcv1"),
}

class NaverMobileHomeObservationError(ValueError):
    pass

class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True); self.rows: dict[str, list[str]] = {}; self._cid: str | None = None; self._depth = 0
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            data = dict(attrs); cid = data.get("data-nlog-click-cid")
            if cid in _SPECS: self._cid, self._depth, self.rows[cid] = cid, 1, [data.get("href") or ""]
        elif self._cid: self._depth += 1
    def handle_endtag(self, tag: str) -> None:
        if self._cid:
            self._depth -= 1
            if self._depth == 0: self._cid = None
    def handle_data(self, data: str) -> None:
        if self._cid: self.rows[self._cid].append(data.strip())

def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None: raise NaverMobileHomeObservationError("recovery clock must be timezone-aware")
    return value.astimezone(timezone.utc)

def parse_rows(html: bytes, *, recovered_at: datetime, required_status: str = "REALTIME") -> dict[str, dict[str, Any]]:
    """Parse only five known SSR anchors; never infer missing units or times."""
    now = _utc(recovered_at); parser = _AnchorParser(); parser.feed(html.decode("utf-8", errors="strict")); result: dict[str, dict[str, Any]] = {}
    for cid, (identity, unit, href) in _SPECS.items():
        parts = parser.rows.get(cid)
        if not parts or parts[0] != href: result[cid] = {"accepted": False, "reason": "VISIBLE_IDENTITY_OR_HREF_MISSING"}; continue
        text = " ".join(parts[1:]); price = re.search(r"(?<![\d.])([\d,]+(?:\.\d+)?)(?![\d.])", text)
        stamp = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{1,2}):(\d{2})", text)
        if not price or not stamp: result[cid] = {"accepted": False, "reason": "VISIBLE_PRICE_OR_TIMESTAMP_MISSING"}; continue
        try: value = float(price.group(1).replace(",", "")); provider = datetime(now.astimezone(KST).year, int(stamp.group(1)), int(stamp.group(2)), int(stamp.group(3)), int(stamp.group(4)), tzinfo=KST)
        except ValueError: result[cid] = {"accepted": False, "reason": "VISIBLE_VALUE_OR_TIMESTAMP_INVALID"}; continue
        if not math.isfinite(value) or value <= 0: result[cid] = {"accepted": False, "reason": "VISIBLE_VALUE_INVALID"}; continue
        if unit is None: result[cid] = {"accepted": False, "reason": "VISIBLE_CONTRACT_UNIT_MISSING", "value": value, "provider_at": provider}; continue
        realtime = "실시간" in text
        post_close = "장마감" in text
        if required_status == "REALTIME" and not realtime: result[cid] = {"accepted": False, "reason": "VISIBLE_STATUS_NOT_REALTIME", "value": value, "provider_at": provider}; continue
        if required_status == "POST_CLOSE" and not post_close: result[cid] = {"accepted": False, "reason": "VISIBLE_STATUS_NOT_POST_CLOSE", "value": value, "provider_at": provider}; continue
        if provider.date() != now.astimezone(KST).date() or provider.astimezone(timezone.utc) > now or now - provider.astimezone(timezone.utc) > _MAX_AGE: result[cid] = {"accepted": False, "reason": "VISIBLE_TIMESTAMP_STALE", "value": value, "provider_at": provider}; continue
        result[cid] = {"accepted": True, "identity": identity, "unit": unit, "value": value, "provider_at": provider, "source_route": "NAVER_WEB:/", "status": required_status}
    return result

def route_for(cid: str) -> CurrentObservationRoute:
    identity, _, _ = _SPECS[cid]; route_id = f"naver-mobile-home-current:{identity.market}:{identity.symbol}"
    return CurrentObservationRoute(RoutePolicy(route_id=route_id, primary_provider="NAVER_FINANCE_WEB", primary_route="NAVER_WEB:/", fallback_provider="UNAVAILABLE", fallback_upstream_provider="UNAVAILABLE", fallback_route="UNAVAILABLE", fallback_enabled=False), identity, (ObservationInterval.SNAPSHOT,))

def observation_for(cid: str, row: dict[str, Any], *, recovered_at: datetime) -> SourceObservation[CurrentObservation]:
    if not row.get("accepted"): raise NaverMobileHomeObservationError(str(row.get("reason")))
    now = _utc(recovered_at); provider = row["provider_at"].astimezone(timezone.utc)
    finality = ObservationFinality.POST_CLOSE_SNAPSHOT if row.get("status") == "POST_CLOSE" else ObservationFinality.PROVISIONAL
    value = CurrentObservation(route_for(cid).route_id, row["identity"], ObservationInterval.SNAPSHOT, row["value"], row["unit"], "NAVER_FINANCE_WEB", "NAVER_FINANCE_WEB", row["source_route"], provider.isoformat(), now.isoformat(), finality); value.validate()
    # Recovery itself is API-zero, while provenance truthfully binds the one
    # immutable original Landing request.
    return SourceObservation(value, SourceProvenance(provider=value.provider, upstream_provider=value.upstream_provider, source_route=value.source_route, retrieved_at_utc=value.retrieved_at_utc, request_count=1))
