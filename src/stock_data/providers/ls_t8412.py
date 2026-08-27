"""Offline parser for one retained LS t8412 native 15-minute response."""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import re
import time
from typing import Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from stock_data.contracts.kospi200_intraday_pilot import (
    LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT,
    RAW_BAR_TIME_POLICY,
    RAW_REVISION_POLICY,
)


SOURCE_OPERATION = "LS_OPENAPI:/stock/chart:t8412"
PROVIDER = "LS_OPENAPI"
OFFICIAL_BASE_URL = "https://openapi.ls-sec.co.kr:8080"
TOKEN_ENDPOINT = "/oauth2/token"
CHART_ENDPOINT = "/stock/chart"
TR_CODE = "t8412"
INTERVAL_MINUTES = 15
REGULAR_SESSION_START = "090000"
REGULAR_SESSION_END = "153000"
FINALITY_STATUS = "HISTORICAL_SESSION_COMPLETE_REVISION_FREEZE_UNRESOLVED"
CURRENT_SESSION_FINALITY_STATUS = "CURRENT_SESSION_PROVISIONAL_REVISION_FREEZE_UNRESOLVED"
PIT_STATUS = "PIT_BLOCKED_REVISION_AND_BAR_LABEL_SEMANTICS_UNRESOLVED"
_SYMBOL = re.compile(r"^\d{6}$")
_TIME = re.compile(r"^\d{6}$")


class LST8412PilotError(ValueError):
    """A retained t8412 response violates the bounded pilot contract."""


def _official_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        value != OFFICIAL_BASE_URL
        or parsed.scheme != "https"
        or parsed.hostname != "openapi.ls-sec.co.kr"
        or parsed.port != 8080
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise LST8412PilotError("LS base URL is not the exact official endpoint")
    return value


def _contains_secret(body: bytes, secrets: tuple[str, ...]) -> bool:
    return any(secret and secret.encode("utf-8") in body for secret in secrets)


def _request_body(symbol: str, market_date: date) -> dict[str, object]:
    target = market_date.strftime("%Y%m%d")
    return {
        "t8412InBlock": {
            "shcode": symbol,
            "ncnt": INTERVAL_MINUTES,
            "qrycnt": 500,
            "nday": "1",
            "sdate": target,
            "stime": "",
            "edate": target,
            "etime": "",
            "cts_date": "",
            "cts_time": "",
            "comp_yn": "N",
        }
    }


def _entitlement_response_is_valid(
    *, status_code: int, body: bytes, expected_symbol: str
) -> bool:
    if status_code != 200:
        return False
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or str(payload.get("rsp_cd")) != "00000":
        return False
    header = payload.get("t8412OutBlock")
    rows = payload.get("t8412OutBlock1")
    return bool(
        isinstance(header, dict)
        and str(header.get("shcode", "")).strip() == expected_symbol
        and str(header.get("s_time", "")).strip() == REGULAR_SESSION_START
        and str(header.get("e_time", "")).strip() == REGULAR_SESSION_END
        and str(header.get("dshmin", "")).strip() == "10"
        and isinstance(rows, list)
        and rows
    )


class LST8412ExactPilotCaptureBuilder:
    """Single-use exact-date transport for the authorized two-symbol Raw pilot."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        base_url: str = OFFICIAL_BASE_URL,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not app_key or not app_secret:
            raise LST8412PilotError("LS credentials are missing")
        if app_key != app_key.strip() or app_secret != app_secret.strip():
            raise LST8412PilotError("LS credentials contain surrounding whitespace")
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = _official_base_url(base_url)
        self.session = session or requests.Session()
        self.sleep = sleep
        self.oauth_calls = 0
        self.data_calls = 0

    def _authenticate(self) -> str:
        self.oauth_calls += 1
        response = self.session.post(
            self.base_url + TOKEN_ENDPOINT,
            headers={"content-type": "application/x-www-form-urlencoded"},
            params={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecretkey": self.app_secret,
                "scope": "oob",
            },
            timeout=30,
        )
        try:
            payload = response.json()
        except ValueError as error:
            raise LST8412PilotError("LS OAuth returned non-JSON") from error
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if response.status_code != 200 or not isinstance(token, str) or not token:
            raise LST8412PilotError("LS OAuth failed")
        return token

    def __call__(self, plan: object) -> object:
        # Local import avoids a provider/orchestration import cycle at module load.
        from stock_data.orchestration.kospi200_intraday_pilot import (
            KOSPI200IntradayCaptureBatch,
            PILOT_DATE,
            PILOT_SYMBOLS,
        )

        if self.oauth_calls or self.data_calls:
            raise LST8412PilotError("LS t8412 capture builder is single-use")
        if (
            getattr(plan, "action", None) != "READY"
            or getattr(plan, "market_date", None) != PILOT_DATE
            or getattr(plan, "symbols", None) != PILOT_SYMBOLS
            or getattr(plan, "oauth_calls", None) != 1
            or getattr(plan, "data_calls", None) != 2
            or getattr(plan, "retries", None) != 0
        ):
            raise LST8412PilotError("LS t8412 plan differs from the authorized exact pilot")

        token = self._authenticate()
        responses: dict[str, bytes] = {}
        captured_at = datetime.now(tz=ZoneInfo("UTC"))
        for sequence, symbol in enumerate(PILOT_SYMBOLS):
            if sequence:
                self.sleep(1.0)
            response = self.session.post(
                self.base_url + CHART_ENDPOINT,
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "tr_cd": TR_CODE,
                    "tr_cont": "N",
                    "tr_cont_key": "",
                },
                json=_request_body(symbol, PILOT_DATE),
                timeout=30,
            )
            self.data_calls += 1
            body = response.content
            if _contains_secret(body, (self.app_key, self.app_secret, token)):
                raise LST8412PilotError("LS t8412 response echoed a credential")
            responses[symbol] = body
            captured_at = datetime.now(tz=ZoneInfo("UTC"))
            if not _entitlement_response_is_valid(
                status_code=response.status_code,
                body=body,
                expected_symbol=symbol,
            ):
                break
        return KOSPI200IntradayCaptureBatch(
            responses=responses,
            captured_at=captured_at,
            oauth_calls=self.oauth_calls,
            data_calls=self.data_calls,
            retries=0,
        )


def _integer(row: dict[str, object], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool):
        raise LST8412PilotError(f"invalid t8412 integer: {field}")
    try:
        result = int(str(value).strip().replace(",", ""))
    except (TypeError, ValueError) as error:
        raise LST8412PilotError(f"invalid t8412 integer: {field}") from error
    return result


def _floating(row: dict[str, object], field: str) -> float:
    try:
        result = float(str(row.get(field)).strip())
    except (TypeError, ValueError) as error:
        raise LST8412PilotError(f"invalid t8412 number: {field}") from error
    if not pd.notna(result):
        raise LST8412PilotError(f"invalid t8412 number: {field}")
    return result


def normalize_retained_t8412_capture(
    body: bytes,
    *,
    market_date: date,
    membership_observation_date: date,
    expected_symbol: str,
    captured_at: datetime,
    allow_current_session: bool = False,
) -> pd.DataFrame:
    """Normalize a retained response without inferring provider bar-label meaning."""
    if market_date != membership_observation_date:
        raise LST8412PilotError("membership date differs from intraday market date")
    if not _SYMBOL.fullmatch(expected_symbol):
        raise LST8412PilotError("t8412 symbol must be the exact six-digit member code")
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise LST8412PilotError("captured_at must be timezone-aware")
    captured_kst = pd.Timestamp(captured_at).tz_convert(ZoneInfo("Asia/Seoul"))
    if not allow_current_session and captured_kst.date() <= market_date:
        raise LST8412PilotError("same-day or live-forming t8412 capture is not accepted")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LST8412PilotError("t8412 response is not JSON") from error
    if not isinstance(payload, dict) or str(payload.get("rsp_cd")) != "00000":
        raise LST8412PilotError("t8412 response status is not successful")
    header = payload.get("t8412OutBlock")
    rows = payload.get("t8412OutBlock1")
    if not isinstance(header, dict) or not isinstance(rows, list) or not rows:
        raise LST8412PilotError("t8412 response blocks are missing or empty")
    if str(header.get("shcode", "")).strip() != expected_symbol:
        raise LST8412PilotError("t8412 response symbol differs")
    if (
        str(header.get("s_time", "")).strip() != REGULAR_SESSION_START
        or str(header.get("e_time", "")).strip() != REGULAR_SESSION_END
    ):
        raise LST8412PilotError("t8412 source session differs from reviewed KRX regular session")
    if str(header.get("dshmin", "")).strip() != "10":
        raise LST8412PilotError("t8412 closing-auction duration differs")
    if _integer(header, "rec_count") != len(rows):
        raise LST8412PilotError("t8412 record count differs")

    digest = hashlib.sha256(body).hexdigest()
    normalized: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise LST8412PilotError("t8412 row is not an object")
        if str(row.get("date", "")).strip() != market_date.strftime("%Y%m%d"):
            raise LST8412PilotError("t8412 row date differs from exact membership date")
        provider_time = str(row.get("time", "")).strip()
        if not _TIME.fullmatch(provider_time):
            raise LST8412PilotError("t8412 provider time shape is unresolved")
        hour, minute, second = int(provider_time[:2]), int(provider_time[2:4]), int(provider_time[4:])
        if (
            not (9 <= hour <= 15)
            or minute >= 60
            or second != 0
            or minute % INTERVAL_MINUTES != 0
            or provider_time < REGULAR_SESSION_START
            or provider_time > REGULAR_SESSION_END
        ):
            raise LST8412PilotError("t8412 row is outside the reviewed native 15m grid")
        values = {field: _integer(row, field) for field in ("open", "high", "low", "close")}
        volume = _integer(row, "jdiff_vol")
        if volume < 0:
            raise LST8412PilotError("t8412 volume is negative")
        if (
            min(values.values()) < 0
            or values["high"] < values["low"]
            or not values["low"] <= values["open"] <= values["high"]
            or not values["low"] <= values["close"] <= values["high"]
        ):
            raise LST8412PilotError("t8412 OHLC relationship is invalid")
        normalized.append({
            "market_date": market_date,
            "membership_observation_date": membership_observation_date,
            "market": "KOSPI",
            "symbol": expected_symbol,
            "provider_symbol": expected_symbol,
            "provider_time": provider_time,
            "bar_time_policy": RAW_BAR_TIME_POLICY,
            "interval_minutes": INTERVAL_MINUTES,
            "source_session_start": REGULAR_SESSION_START,
            "source_session_end": REGULAR_SESSION_END,
            **values,
            "volume": volume,
            "adjustment_code": _integer(row, "jongchk"),
            "adjustment_rate": _floating(row, "rate"),
            "provider": PROVIDER,
            "source_operation": SOURCE_OPERATION,
            "captured_at": pd.Timestamp(captured_at).tz_convert("UTC"),
            "source_sha256": digest,
            "revision_policy": RAW_REVISION_POLICY,
            "finality_status": CURRENT_SESSION_FINALITY_STATUS if allow_current_session else FINALITY_STATUS,
            "pit_status": PIT_STATUS,
        })
    frame = pd.DataFrame(
        normalized, columns=LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT.column_names
    ).sort_values(["market_date", "symbol", "provider_time"], kind="stable").reset_index(drop=True)
    if frame.duplicated(list(LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT.primary_key)).any():
        raise LST8412PilotError("duplicate t8412 date-time-symbol key")
    return frame


__all__ = [
    "CHART_ENDPOINT", "FINALITY_STATUS", "INTERVAL_MINUTES",
    "CURRENT_SESSION_FINALITY_STATUS",
    "LST8412ExactPilotCaptureBuilder", "LST8412PilotError", "OFFICIAL_BASE_URL",
    "PIT_STATUS", "PROVIDER", "SOURCE_OPERATION", "TOKEN_ENDPOINT", "TR_CODE",
    "normalize_retained_t8412_capture",
]
