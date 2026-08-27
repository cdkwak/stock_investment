"""Single-use, Landing-first UR-174 desktop HTML current-observation pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.providers.naver_desktop_005930_current_html_observation import (
    naver_desktop_005930_html_quote,
    naver_desktop_005930_html_route,
)


KST = ZoneInfo("Asia/Seoul")
URL = "https://finance.naver.com/item/main.naver?code=005930"
TIMEOUT_SECONDS = 10
STATE_PATH = Path("data/state/naver_desktop_005930_current_html_ur174.json")
LOCK_PATH = Path("data/state/naver_desktop_005930_current_html_ur174.lock")
PROJECTION_PATH = Path("data/state/current_observations/naver_desktop_005930_current.json")
LANDING_ROOT = Path("data/landing/naver_desktop_005930_current_html/ur174")
MAX_AGE = timedelta(minutes=60)


class PublicHtmlResponse(Protocol):
    status_code: int
    body: bytes


@dataclass(frozen=True)
class NaverDesktopHtmlResponse:
    status_code: int
    body: bytes


@dataclass(frozen=True)
class NaverDesktopPilotResult:
    status: str
    expected_market_date: str
    raw_gets: int
    landing_file: str | None
    landing_sha256: str | None
    provider_timestamp_utc: str | None
    replay_api_calls: int
    safe_code: str | None


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
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


def _read_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "attempts": {}}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "attempts"}:
        raise RuntimeError("Naver desktop pilot state schema mismatch")
    if payload["schema_version"] != 1 or not isinstance(payload["attempts"], dict):
        raise RuntimeError("Naver desktop pilot state is invalid")
    return payload


def _target_date(value: str | date) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as error:
        raise ValueError("expected_market_date must be an ISO date") from error


def _acquire_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError("Naver desktop pilot lock exists; fail closed without repeat") from error


def _release_lock(path: Path, descriptor: int) -> None:
    os.close(descriptor)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _landing_path(expected_date: str, retrieved_at: datetime, digest: str) -> Path:
    return LANDING_ROOT / expected_date / f"{retrieved_at.strftime('%Y%m%dT%H%M%S%fZ')}_{digest}" / "response.html"


def _write_landing(root: Path, relative: Path, body: bytes) -> str:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    digest = hashlib.sha256(body).hexdigest()
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Naver desktop Landing readback hash mismatch")
    return digest


def _route_from_attempt(attempt: dict[str, object]):
    route = naver_desktop_005930_html_route()
    if attempt.get("route_id") != route.route_id or attempt.get("identity") != asdict(route.identity):
        raise RuntimeError("completed Naver desktop pilot identity is invalid")
    return route


def _failure_code(error: Exception) -> str:
    return f"NAVER_DESKTOP_005930_{type(error).__name__.upper()}"


def execute_naver_desktop_005930_current_html(
    project_root: Path,
    *,
    expected_market_date: str | date,
    transport: Callable[[str, int], PublicHtmlResponse] | None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> NaverDesktopPilotResult:
    """Run once under a durable preclaim, or deterministically replay locally."""
    root = Path(project_root)
    target_date = _target_date(expected_market_date)
    state_path = root / STATE_PATH
    lock_path = root / LOCK_PATH
    descriptor = _acquire_lock(lock_path)
    try:
        state = _read_state(state_path)
        attempts = state["attempts"]
        assert isinstance(attempts, dict)
        prior = attempts.get(target_date)
        if prior is not None:
            if not isinstance(prior, dict):
                raise RuntimeError("Naver desktop pilot attempt state is invalid")
            if prior.get("status") == "COMPLETE":
                route = _route_from_attempt(prior)
                replay = CurrentObservationCoordinator(CurrentObservationFileStore(root / PROJECTION_PATH)).replay(route)
                if replay.observation is None:
                    raise RuntimeError("completed Naver desktop projection is unreadable")
                return NaverDesktopPilotResult("API_ZERO_REPLAY", target_date, 0, str(prior.get("landing_file")),
                    str(prior.get("landing_sha256")), replay.observation.provider_timestamp_utc, replay.api_calls, None)
            return NaverDesktopPilotResult("API_ZERO_REPLAY_FAILURE", target_date, 0, None, None, None, 0,
                str(prior.get("safe_code", "NAVER_DESKTOP_005930_PREVIOUS_FAILURE")))
        if transport is None:
            raise ValueError("a public HTML transport is required for a new route")

        attempted_at = clock().astimezone(timezone.utc)
        attempts[target_date] = {
            "status": "ATTEMPTING", "attempted_at_utc": attempted_at.isoformat(), "url": URL,
            "timeout_seconds": TIMEOUT_SECONDS, "retry_count": 0, "redirect_count": 0, "fallback_count": 0,
        }
        _atomic_json(state_path, state)

        raw_gets = 0
        landing_relative: Path | None = None
        landing_sha256: str | None = None
        provider_timestamp: str | None = None
        try:
            raw_gets = 1
            response = transport(URL, TIMEOUT_SECONDS)
            if response.status_code != 200:
                raise RuntimeError(f"Naver desktop HTML returned HTTP {response.status_code}")
            if not isinstance(response.body, bytes) or not response.body:
                raise RuntimeError("Naver desktop HTML body is empty")
            retrieved_at = clock().astimezone(timezone.utc)
            digest = hashlib.sha256(response.body).hexdigest()
            landing_relative = _landing_path(target_date, retrieved_at, digest)
            landing_sha256 = _write_landing(root, landing_relative, response.body)
            # Parse only the immutable Landing readback, never the transport response.
            source = naver_desktop_005930_html_quote((root / landing_relative).read_bytes(), retrieved_at=retrieved_at)
            provider_at = datetime.fromisoformat(source.value.provider_timestamp_utc)
            provider_timestamp = source.value.provider_timestamp_utc
            if provider_at.astimezone(KST).date().isoformat() != target_date:
                raise RuntimeError("Naver desktop provider timestamp has an unexpected KST date")
            if provider_at > retrieved_at or retrieved_at - provider_at > MAX_AGE:
                raise RuntimeError("Naver desktop provider timestamp fails the 60-minute age gate")
            route = naver_desktop_005930_html_route()
            coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / PROJECTION_PATH))
            decision = coordinator.refresh(
                route,
                primary_attempt=lambda: source,
                fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("Naver desktop pilot has no fallback")),
            )
            if decision.observation != source.value or decision.api_calls != 1:
                raise RuntimeError("Naver desktop atomic projection readback mismatch")
            replay = coordinator.replay(route)
            if replay.observation != source.value or replay.api_calls != 0:
                raise RuntimeError("Naver desktop API-zero replay mismatch")
            attempts[target_date] = {
                "status": "COMPLETE", "attempted_at_utc": attempted_at.isoformat(),
                "landing_file": landing_relative.as_posix(), "landing_sha256": landing_sha256,
                "route_id": source.value.route_id, "identity": asdict(source.value.identity),
                "provider_timestamp_utc": provider_timestamp, "interval": source.value.interval.value,
                "raw_gets": raw_gets, "retry_count": 0, "redirect_count": 0, "fallback_count": 0,
            }
            _atomic_json(state_path, state)
            return NaverDesktopPilotResult("COMPLETE", target_date, raw_gets, landing_relative.as_posix(),
                landing_sha256, provider_timestamp, replay.api_calls, None)
        except Exception as error:
            attempts[target_date] = {
                "status": "FAILED", "attempted_at_utc": attempted_at.isoformat(), "url": URL,
                "raw_gets": raw_gets, "retry_count": 0, "redirect_count": 0, "fallback_count": 0,
                "landing_file": landing_relative.as_posix() if landing_relative else None,
                "landing_sha256": landing_sha256, "provider_timestamp_utc": provider_timestamp,
                "safe_code": _failure_code(error),
            }
            _atomic_json(state_path, state)
            raise
    finally:
        _release_lock(lock_path, descriptor)


def _requests_transport(url: str, timeout_seconds: int) -> NaverDesktopHtmlResponse:
    import requests

    response = requests.get(url, timeout=timeout_seconds, allow_redirects=False)
    return NaverDesktopHtmlResponse(response.status_code, response.content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UR-174 single-use Naver desktop HTML pilot")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--expected-market-date", required=True)
    arguments = parser.parse_args(argv)
    result = execute_naver_desktop_005930_current_html(
        arguments.project_root, expected_market_date=arguments.expected_market_date, transport=_requests_transport,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "NaverDesktopHtmlResponse", "NaverDesktopPilotResult", "execute_naver_desktop_005930_current_html",
]
