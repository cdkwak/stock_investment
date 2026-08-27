"""Run UR-148's exactly-once Naver 005930 polling route."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.exchange_calendar import MarketSessionService, MarketVenue, SessionState
from stock_data.providers.naver_domestic_stock_current_observation import naver_domestic_stock_quote, naver_domestic_stock_route


URL = "https://polling.finance.naver.com/api/realtime/domestic/stock/A005930"
STATE = Path("data/state/naver_005930_current_polling_ur148_20260821.json")
STORE = Path("data/state/current_observations/naver_005930_current_polling.json")
LANDING = Path("data/landing/naver_005930_current_polling/ur148_20260821")
TARGET_DATE = date(2026, 8, 21)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(path: Path, payload: dict[str, object], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)); stream.flush(); os.fsync(stream.fileno())
        return
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def _landing(root: Path, body: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(body).hexdigest()
    path = root / LANDING / digest / "response.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno())
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Landing hash readback mismatch")
    return path.relative_to(root).as_posix(), digest


def run(root: Path) -> dict[str, object]:
    root = Path(root); state_path = root / STATE; attempted = _now()
    claim: dict[str, object] = {
        "schema_version": 1, "status": "ATTEMPTING", "attempted_at_utc": attempted.isoformat(),
        "route": URL, "business_gets_reserved": 0, "business_gets_invoked": 0, "business_gets_completed": 0,
        "retry_count": 0, "redirect_count": 0, "fallback_count": 0, "auth_cookie_env_calls": 0,
    }
    try: _write(state_path, claim, exclusive=True)
    except FileExistsError: return {"status": "NO_REPEAT", "business_gets": 0, "replay_api_calls": 0}
    try:
        session = MarketSessionService(MarketVenue.XKRX_CASH)
        if session.state_at(attempted) is not SessionState.REGULAR or session.trade_date_at(attempted) != TARGET_DATE:
            claim.update({"status": "GATE_CLOSED_NO_CALL", "failure_type": "RuntimeError"}); _write(state_path, claim)
            return {"status": "GATE_CLOSED_NO_CALL", "business_gets": 0, "replay_api_calls": 0}
        claim.update({"business_gets_reserved": 1, "business_gets_invoked": 1, "transport_started_at_utc": _now().isoformat()}); _write(state_path, claim)
        response = requests.get(URL, timeout=10, allow_redirects=False)
        claim["business_gets_completed"] = 1
        if response.status_code != 200:
            claim.update({"status": "FAILED", "failure_type": "HTTPStatusError", "raw_business_gets": 1, "replay_api_calls": 0}); _write(state_path, claim)
            return {"status": "FAILED", "business_gets": 1, "replay_api_calls": 0}
        captured = _now(); landing_file, digest = _landing(root, response.content)
        claim.update({"landing_file": landing_file, "landing_sha256": digest, "landing_bytes": len(response.content)})
        payload = json.loads(response.content)
        if not isinstance(payload, dict) or set(payload) != {"datas"} or not isinstance(payload["datas"], list) or len(payload["datas"]) != 1:
            raise ValueError("exact polling envelope schema mismatch")
        candidate = naver_domestic_stock_quote(payload["datas"][0], retrieved_at=captured)
        route = naver_domestic_stock_route(); coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / STORE))
        result = coordinator.refresh(route, primary_attempt=lambda: candidate, fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("fallback disabled")))
        replay = coordinator.replay(route)
        if result.observation != candidate.value or replay.observation != candidate.value or replay.api_calls != 0:
            raise RuntimeError("current-observation atomic readback mismatch")
        claim.update({"status": "COMPLETE", "raw_business_gets": 1, "provider_timestamp_utc": candidate.value.provider_timestamp_utc, "route_id": candidate.value.route_id, "replay_api_calls": 0}); _write(state_path, claim)
        return {"status": "COMPLETE", "business_gets": 1, "replay_api_calls": 0}
    except Exception as error:
        claim.update({"status": "FAILED", "failure_type": type(error).__name__, "raw_business_gets": int(claim["business_gets_invoked"]), "replay_api_calls": 0}); _write(state_path, claim)
        return {"status": "FAILED", "business_gets": int(claim["business_gets_invoked"]), "replay_api_calls": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--project-root", type=Path, required=True); parser.add_argument("--confirm-live-005930", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_005930: parser.error("--confirm-live-005930 is required")
    print(run(args.project_root)); return 0


if __name__ == "__main__": raise SystemExit(main())
