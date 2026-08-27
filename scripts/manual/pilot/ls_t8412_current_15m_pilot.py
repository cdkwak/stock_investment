"""One exact current-session LS t8412 pilot; no retry, continuation, or fallback."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from stock_data.orchestration.automatic_fallback import RoutePolicy
from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore, CurrentObservationRoute, ObservationIdentity, ObservationInterval
from stock_data.providers.ls_t8412 import CHART_ENDPOINT, OFFICIAL_BASE_URL, SOURCE_OPERATION, TOKEN_ENDPOINT, TR_CODE, _request_body, normalize_retained_t8412_capture
from stock_data.providers.ls_t8412_current_observation import retained_t8412_current_attempt
from stock_data.providers.fdr_display_daily import FDRDisplayDailyLandingStore

TARGET_DATE, SYMBOL = date(2026, 8, 21), "005930"
STATE = Path("data/state/ls_t8412_current_15m_pilot.json")
STORE = Path("data/state/current_observations/ls_t8412_current.json")
LANDING = Path("data/landing/ls_openapi/t8412_current_15m")


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _route() -> CurrentObservationRoute:
    return CurrentObservationRoute(RoutePolicy("ls-t8412-current:XKRX:005930", "LS_OPENAPI", SOURCE_OPERATION, "none", "none", "none", False), ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", SYMBOL), (ObservationInterval.MINUTES_15,))


def run(root: Path) -> dict[str, object]:
    root = Path(root)
    state_path = root / STATE
    if state_path.exists():
        return {"status": "API_ZERO_REPLAY", "oauth_calls": 0, "data_calls": 0, "replay_api_calls": 0}
    load_dotenv(root / ".env", override=False)
    app_key, app_secret = os.getenv("LS_APP_KEY", ""), os.getenv("LS_APP_SECRET", "")
    oauth_calls = data_calls = 0
    route = _route()
    try:
        if not app_key or not app_secret:
            raise RuntimeError("runtime credentials unavailable")
        oauth_calls = 1
        oauth = requests.post(OFFICIAL_BASE_URL + TOKEN_ENDPOINT, headers={"content-type": "application/x-www-form-urlencoded"}, params={"grant_type": "client_credentials", "appkey": app_key, "appsecretkey": app_secret, "scope": "oob"}, timeout=10)
        token = oauth.json().get("access_token") if oauth.status_code == 200 else None
        if not isinstance(token, str) or not token:
            raise RuntimeError("oauth failed")
        data_calls = 1
        response = requests.post(OFFICIAL_BASE_URL + CHART_ENDPOINT, headers={"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}", "tr_cd": TR_CODE, "tr_cont": "N", "tr_cont_key": ""}, json=_request_body(SYMBOL, TARGET_DATE), timeout=10)
        if response.status_code != 200:
            raise RuntimeError("chart failed")
        body = response.content
        # Successful body is immutable Landing before any parser/adapter work.
        class _Spec: route = "LS_T8412_CURRENT:005930"
        FDRDisplayDailyLandingStore(root / LANDING).retain(_Spec(), body)
        raw = normalize_retained_t8412_capture(body, market_date=TARGET_DATE, membership_observation_date=TARGET_DATE, expected_symbol=SYMBOL, captured_at=datetime.now(timezone.utc), allow_current_session=True)
        if (raw[["open", "high", "low", "close"]] <= 0).any().any():
            raise RuntimeError("nonpositive ohlc")
        coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / STORE))
        result = coordinator.refresh(route, primary_attempt=retained_t8412_current_attempt(raw, route=route, market_date=TARGET_DATE), fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")))
        replay = coordinator.replay(route)
        if result.observation is None or replay.api_calls != 0:
            raise RuntimeError("projection unavailable")
        _write(state_path, {"schema_version": 1, "date": TARGET_DATE.isoformat(), "status": "COMPLETE", "oauth_calls": oauth_calls, "data_calls": data_calls, "replay_api_calls": 0})
        return {"status": "COMPLETE", "oauth_calls": oauth_calls, "data_calls": data_calls, "replay_api_calls": 0}
    except Exception:
        _write(state_path, {"schema_version": 1, "date": TARGET_DATE.isoformat(), "status": "FAILED_BOUNDED", "oauth_calls": oauth_calls, "data_calls": data_calls, "replay_api_calls": 0})
        return {"status": "FAILED_BOUNDED", "oauth_calls": oauth_calls, "data_calls": data_calls, "replay_api_calls": 0}


if __name__ == "__main__":
    print(json.dumps(run(Path(".")), sort_keys=True))
