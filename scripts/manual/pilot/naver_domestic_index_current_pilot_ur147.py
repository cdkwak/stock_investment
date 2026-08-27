"""Run UR-147's one-shot Naver domestic-index public-web polling pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.exchange_calendar import MarketSessionService, MarketVenue, SessionState
from stock_data.providers.naver_domestic_index_current_observation import (
    NaverDomesticIndexObservationError,
    naver_domestic_index_row,
    naver_domestic_index_route,
)


KST = ZoneInfo("Asia/Seoul")
TARGET_DATE = date(2026, 8, 21)
URL = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI,KOSDAQ,KPI200"
STATE = Path("data/state/naver_domestic_index_current_ur147_20260821.json")
STORE = Path("data/state/current_observations/naver_domestic_index_current.json")
LANDING = Path("data/landing/naver_domestic_index_current/ur147_20260821")
_CODES = ("KOSPI", "KOSDAQ", "KPI200")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _claim(path: Path, attempted_at: datetime) -> dict[str, object] | None:
    """Create the immutable pre-transport claim; any prior state is no-repeat."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "ATTEMPTING",
        "attempted_at_utc": attempted_at.isoformat(),
        "business_gets_reserved": 0,
        "business_gets_invoked": 0,
        "business_gets_completed": 0,
        "retry_count": 0,
        "redirect_count": 0,
        "fallback_count": 0,
        "auth_cookie_env_calls": 0,
        "route": URL,
        "codes": list(_CODES),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        return None
    return payload


def _landing(root: Path, body: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(body).hexdigest()
    destination = root / LANDING / digest / "response.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Landing hash readback mismatch")
    return destination.relative_to(root).as_posix(), digest


def _rows(body: bytes) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError) as error:
        raise NaverDomesticIndexObservationError("successful body is not JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"datas"} or not isinstance(payload["datas"], list):
        raise NaverDomesticIndexObservationError("combined polling response schema is unexpected")
    mapped: dict[str, dict[str, Any]] = {}
    for row in payload["datas"]:
        if not isinstance(row, dict) or not isinstance(row.get("cd"), str):
            raise NaverDomesticIndexObservationError("combined polling row schema is unexpected")
        code = row["cd"]
        if code in mapped:
            raise NaverDomesticIndexObservationError("combined polling response duplicates an identity")
        mapped[code] = row
    if set(mapped) != set(_CODES):
        raise NaverDomesticIndexObservationError("combined polling response is not the exact three-index scope")
    return mapped


def run(root: Path) -> dict[str, object]:
    """Run exactly once; retain success bytes before local per-index validation."""
    root = Path(root)
    state_path = root / STATE
    attempted_at = _now()
    claim = _claim(state_path, attempted_at)
    if claim is None:
        return {"status": "NO_REPEAT", "business_gets": 0, "replay_api_calls": 0}
    try:
        session = MarketSessionService(MarketVenue.XKRX_CASH)
        if session.state_at(attempted_at) is not SessionState.REGULAR or session.trade_date_at(attempted_at) != TARGET_DATE:
            claim.update({"status": "GATE_CLOSED_NO_CALL", "failure_type": "RuntimeError"})
            _write_atomic(state_path, claim)
            return {"status": "GATE_CLOSED_NO_CALL", "business_gets": 0, "replay_api_calls": 0}
        # Reserve the sole raw operation before transport. A crash after this
        # point is deliberately orphaned/no-repeat rather than retried.
        claim["business_gets_reserved"] = 1
        claim["business_gets_invoked"] = 1
        claim["transport_started_at_utc"] = _now().isoformat()
        _write_atomic(state_path, claim)
        response = requests.get(URL, timeout=10, allow_redirects=False)
        claim["business_gets_completed"] = 1
        captured_at = _now()
        if response.status_code != 200:
            claim.update({"status": "FAILED", "failure_type": "HTTPStatusError"})
            _write_atomic(state_path, claim)
            return {"status": "FAILED", "business_gets": 1, "replay_api_calls": 0}
        landing_file, digest = _landing(root, response.content)
        claim.update({"landing_file": landing_file, "landing_sha256": digest, "landing_bytes": len(response.content)})
        mapped = _rows(response.content)
        store = CurrentObservationFileStore(root / STORE)
        coordinator = CurrentObservationCoordinator(store)
        accepted: list[str] = []
        rejected: dict[str, str] = {}
        for code in _CODES:
            try:
                candidate = naver_domestic_index_row(mapped[code], retrieved_at=captured_at)
                route = naver_domestic_index_route(code)
                result = coordinator.refresh(
                    route,
                    primary_attempt=lambda candidate=candidate: candidate,
                    fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("fallback disabled")),
                )
                replay = coordinator.replay(route)
                if result.observation != candidate.value or replay.observation != candidate.value or replay.api_calls != 0:
                    raise RuntimeError("current-observation atomic readback mismatch")
                accepted.append(code)
            except (NaverDomesticIndexObservationError, RuntimeError, ValueError) as error:
                # A malformed/stale row must not suppress another exact identity.
                rejected[code] = type(error).__name__
        claim.update({
            "status": "COMPLETE" if accepted else "COMPLETE_NUMERIC_FREE",
            "accepted_codes": accepted,
            "rejected_codes": rejected,
            "provider_timestamp_rule": "per-identity dt YYYYMMDDHHMMSS KST",
            "raw_business_gets": 1,
            "replay_api_calls": 0,
        })
        _write_atomic(state_path, claim)
        return {"status": claim["status"], "business_gets": 1, "accepted_codes": accepted, "rejected_codes": rejected, "replay_api_calls": 0}
    except Exception as error:
        # The durable reservation records that the sole provider operation was
        # invoked even when a connection fails before an HTTP response exists.
        raw_gets = int(claim["business_gets_invoked"])
        claim.update({
            "status": "FAILED",
            "failure_type": type(error).__name__,
            "raw_business_gets": raw_gets,
            "replay_api_calls": 0,
        })
        _write_atomic(state_path, claim)
        return {"status": "FAILED", "business_gets": raw_gets, "replay_api_calls": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-live-domestic-indices", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_domestic_indices:
        parser.error("--confirm-live-domestic-indices is required for the reviewed one-shot route")
    print(run(args.project_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
