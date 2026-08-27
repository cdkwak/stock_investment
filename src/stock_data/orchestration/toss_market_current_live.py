"""One bounded Toss KOSPI current-display pilot with no fallback or retry."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from stock_data.orchestration.current_observation import (
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
)
from stock_data.orchestration.toss_market_current_observation import (
    TossCurrentObservation,
    market_price_snapshot,
)


PILOT_ROUTE = "/api/v1/market-indicators/prices"
PILOT_PARAMS = {"symbols": "KOSPI"}
PILOT_STATE_PATH = Path("data/state/toss_market_current_observation_pilot.json")
PILOT_PROJECTION_PATH = Path("data/state/current_observations/toss_kospi_price_snapshot.json")


class TossMarketClient(Protocol):
    token_request_count: int
    market_request_count: int

    def get_market_data(self, path: str, *, params: dict[str, object]) -> Any: ...


@dataclass(frozen=True)
class TossCurrentPilotResult:
    status: str
    expected_market_date: str
    token_calls: int
    market_calls: int
    landing_file: str | None
    replay_api_calls: int


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
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


def _expected_date(value: str | date) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as error:
        raise ValueError("expected_market_date must be an ISO date") from error


def _read_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "attempted_dates": {}}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "attempted_dates"}:
        raise RuntimeError("Toss current pilot state schema mismatch")
    if payload["schema_version"] != 1 or not isinstance(payload["attempted_dates"], dict):
        raise RuntimeError("Toss current pilot state is invalid")
    return payload


def _counts(client: TossMarketClient, token_before: int, market_before: int) -> tuple[int, int]:
    token_calls = client.token_request_count - token_before
    market_calls = client.market_request_count - market_before
    if not 0 <= token_calls <= 1 or not 0 <= market_calls <= 1:
        raise RuntimeError("Toss current pilot exceeded its fixed call budget")
    return token_calls, market_calls


def execute_toss_kospi_current_pilot(
    project_root: Path,
    *,
    expected_market_date: str | date,
    client: TossMarketClient | None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> TossCurrentPilotResult:
    """Execute/replay the one authorized route; all other routes are out of scope.

    The caller owns runtime-only client construction.  This function never reads
    environment variables and never serializes client/authentication material.
    """
    root = Path(project_root)
    target_date = _expected_date(expected_market_date)
    state_path = root / PILOT_STATE_PATH
    state = _read_state(state_path)
    prior = state["attempted_dates"].get(target_date)
    if prior is not None:
        if not isinstance(prior, dict) or prior.get("status") != "COMPLETE":
            raise RuntimeError("Toss current pilot date was already attempted; no repeat is allowed")
        coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / PILOT_PROJECTION_PATH))
        route = _route_from_state(prior)
        replay = coordinator.replay(route)
        if replay.observation is None:
            raise RuntimeError("completed Toss current pilot projection is unreadable")
        return TossCurrentPilotResult("API_ZERO_REPLAY", target_date, 0, 0, prior.get("landing_file"), replay.api_calls)
    if client is None:
        raise ValueError("a runtime Toss market client is required for a new pilot date")

    token_before = client.token_request_count
    market_before = client.market_request_count
    captured_at = clock().astimezone(timezone.utc)
    landing_relative: Path | None = None
    try:
        response = client.get_market_data(PILOT_ROUTE, params=PILOT_PARAMS)
        token_calls, market_calls = _counts(client, token_before, market_before)
        if token_calls != 1 or market_calls != 1:
            raise RuntimeError("Toss current pilot requires exactly one OAuth and one market GET")
        landing_relative = Path("data/landing/tossinvest/current_observation") / f"kospi_{target_date}_{captured_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        _atomic_json(root / landing_relative, {
            "captured_at_utc": captured_at.isoformat(),
            "provider": "tossinvest_open_api",
            "endpoint": PILOT_ROUTE,
            "params": PILOT_PARAMS,
            "expected_market_date": target_date,
            "raw_response": response.payload,
        })
        candidate = market_price_snapshot(
            response.payload, market="KOSPI", retrieved_at_utc=captured_at.isoformat(),
        )
        if candidate.market_date != target_date:
            raise RuntimeError("Toss current pilot provider timestamp has an unexpected KST market date")
        coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / PILOT_PROJECTION_PATH))
        decision = coordinator.refresh(
            candidate.route(), primary_attempt=lambda: candidate.source,
            fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("Toss pilot has no fallback")),
        )
        if decision.observation != candidate.observation:
            raise RuntimeError("Toss current pilot atomic projection readback mismatch")
        replay = coordinator.replay(candidate.route())
        if replay.observation != candidate.observation or replay.api_calls != 0:
            raise RuntimeError("Toss current pilot API-zero replay mismatch")
        state["attempted_dates"][target_date] = {
            "status": "COMPLETE",
            "landing_file": landing_relative.as_posix(),
            "route_id": candidate.observation.route_id,
            "identity": asdict(candidate.observation.identity),
            "interval": candidate.observation.interval.value,
        }
        _atomic_json(state_path, state)
        return TossCurrentPilotResult("COMPLETE", target_date, token_calls, market_calls, landing_relative.as_posix(), replay.api_calls)
    except Exception as error:
        token_calls, market_calls = _counts(client, token_before, market_before)
        state["attempted_dates"][target_date] = {
            "status": "FAILED",
            "failure_type": type(error).__name__,
            "landing_file": landing_relative.as_posix() if landing_relative else None,
            "token_calls": token_calls,
            "market_calls": market_calls,
        }
        _atomic_json(state_path, state)
        raise


def _route_from_state(state: dict[str, Any]):
    from stock_data.orchestration.automatic_fallback import RoutePolicy
    from stock_data.orchestration.current_observation import (
        CurrentObservationRoute, ObservationIdentity, ObservationInterval,
    )

    identity = state.get("identity")
    if not isinstance(identity, dict) or set(identity) != {"dataset_id", "market", "symbol"}:
        raise RuntimeError("completed Toss current pilot identity is invalid")
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=str(state.get("route_id", "")),
            primary_provider="tossinvest_open_api",
            primary_route=PILOT_ROUTE,
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=ObservationIdentity(**identity),
        interval_precedence=(ObservationInterval(str(state.get("interval", ""))),),
    )


__all__ = ["TossCurrentPilotResult", "execute_toss_kospi_current_pilot"]
