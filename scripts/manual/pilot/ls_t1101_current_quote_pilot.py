"""One durable LS t1101 current-quote pilot for UR-143."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.exchange_calendar import MarketSessionService, MarketVenue, SessionState
from stock_data.providers.ls_t1101_current_observation import t1101_current_quote, t1101_route
from stock_data.providers.ls_t8412 import OFFICIAL_BASE_URL, TOKEN_ENDPOINT


KST = ZoneInfo("Asia/Seoul")
STATE = Path("data/state/ls_t1101_current_quote_ur143.json")
STORE = Path("data/state/current_observations/ls_t1101_current.json")
LANDING = Path("data/landing/ls_openapi/t1101_current_quote_ur143")
TARGET_DATE = date(2026, 8, 21)


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def _landing(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run(root: Path) -> dict[str, object]:
    root = Path(root)
    state_path = root / STATE
    if state_path.exists():
        return {"status": "NO_REPEAT", "oauth_calls": 0, "data_calls": 0, "replay_api_calls": 0}
    attempted_at = _now()
    _atomic_json(state_path, {"schema_version": 1, "status": "ATTEMPTING", "attempted_at_utc": attempted_at.isoformat()})
    oauth_calls = data_calls = 0
    landing_file: str | None = None
    provider_timestamp: str | None = None
    try:
        service = MarketSessionService(MarketVenue.XKRX_CASH)
        if service.state_at(attempted_at) is not SessionState.REGULAR or service.trade_date_at(attempted_at) != TARGET_DATE:
            raise RuntimeError("XKRX regular-session/date gate is closed")
        load_dotenv(root / ".env", override=False)
        app_key, app_secret = os.getenv("LS_APP_KEY", ""), os.getenv("LS_APP_SECRET", "")
        if not app_key or not app_secret:
            raise RuntimeError("runtime credentials unavailable")
        oauth_calls = 1
        oauth = requests.post(
            OFFICIAL_BASE_URL + TOKEN_ENDPOINT,
            headers={"content-type": "application/x-www-form-urlencoded"},
            params={"grant_type": "client_credentials", "appkey": app_key, "appsecretkey": app_secret, "scope": "oob"},
            timeout=10,
        )
        token = oauth.json().get("access_token") if oauth.status_code == 200 else None
        if not isinstance(token, str) or not token:
            raise RuntimeError("oauth failed")
        data_calls = 1
        response = requests.post(
            OFFICIAL_BASE_URL + "/stock/market-data",
            headers={"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}", "tr_cd": "t1101", "tr_cont": "N", "tr_cont_key": ""},
            json={"t1101InBlock": {"shcode": "005930"}}, timeout=10,
        )
        if response.status_code != 200:
            raise RuntimeError("t1101 failed")
        body = response.content
        if any(secret and secret.encode("utf-8") in body for secret in (app_key, app_secret, token)):
            raise RuntimeError("t1101 response echoed secret")
        captured_at = _now()
        relative = LANDING / f"005930_{captured_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        _landing(root / relative, body)
        landing_file = relative.as_posix()
        payload = json.loads(body)
        candidate = t1101_current_quote(payload, retrieved_at=captured_at)
        provider_timestamp = candidate.value.provider_timestamp_utc
        coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / STORE))
        result = coordinator.refresh(t1101_route(), primary_attempt=lambda: candidate, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("no fallback")))
        replay = coordinator.replay(t1101_route())
        if result.observation != candidate.value or replay.observation != candidate.value or replay.api_calls != 0:
            raise RuntimeError("current-observation promotion/readback failed")
        _atomic_json(state_path, {"schema_version": 1, "status": "COMPLETE", "attempted_at_utc": attempted_at.isoformat(), "oauth_calls": oauth_calls, "data_calls": data_calls, "landing_file": landing_file, "provider_timestamp_utc": provider_timestamp, "route_id": candidate.value.route_id})
        return {"status": "COMPLETE", "oauth_calls": oauth_calls, "data_calls": data_calls, "landing_file": landing_file, "provider_timestamp_utc": provider_timestamp, "replay_api_calls": 0}
    except Exception as error:
        _atomic_json(state_path, {"schema_version": 1, "status": "FAILED", "attempted_at_utc": attempted_at.isoformat(), "failure_type": type(error).__name__, "oauth_calls": oauth_calls, "data_calls": data_calls, "landing_file": landing_file, "provider_timestamp_utc": provider_timestamp})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-live-005930", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_005930:
        parser.error("--confirm-live-005930 is required")
    try:
        print(run(args.project_root))
        return 0
    except Exception as error:
        print({"status": "FAILED", "failure_type": type(error).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
