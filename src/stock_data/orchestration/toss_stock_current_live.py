"""One durable, date-bound Toss stock quote operation for UR-141."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from stock_data.orchestration.current_observation import (
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
)
from stock_data.orchestration.toss_market_current_observation import stock_price_snapshot


KST = ZoneInfo("Asia/Seoul")
QUOTE_PATH = "/api/v1/prices"
QUOTE_PARAMS = {"symbols": "005930"}
STATE_PATH = Path("data/state/toss_stock_current_quote_ur141.json")
PROJECTION_PATH = Path("data/state/current_observations/toss_005930_price_snapshot.json")
LANDING_ROOT = Path("data/landing/tossinvest/stock_current_quote_ur141")
MAX_AGE = timedelta(minutes=60)
AcceptanceValidator = Callable[[Any, dict[str, Any], datetime, str], None]


class TossStockClient(Protocol):
    token_request_count: int
    market_request_count: int

    def get_market_data(self, path: str, *, params: dict[str, object]) -> Any: ...


@dataclass(frozen=True)
class TossStockCurrentResult:
    status: str
    expected_market_date: str
    token_calls: int
    business_calls: int
    landing_file: str | None
    provider_timestamp_utc: str | None
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


def _target_date(value: str | date) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as error:
        raise ValueError("expected_market_date must be an ISO date") from error


def _read_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "attempts": {}}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "attempts"}:
        raise RuntimeError("Toss stock current state schema mismatch")
    if payload["schema_version"] != 1 or not isinstance(payload["attempts"], dict):
        raise RuntimeError("Toss stock current state is invalid")
    return payload


def _counts(client: TossStockClient | None, token_before: int, business_before: int) -> tuple[int, int]:
    if client is None:
        return 0, 0
    token_calls = client.token_request_count - token_before
    business_calls = client.market_request_count - business_before
    if not 0 <= token_calls <= 1 or not 0 <= business_calls <= 1:
        raise RuntimeError("Toss stock current route exceeded its fixed call budget")
    return token_calls, business_calls


def _route_from_state(state: dict[str, Any]):
    from stock_data.orchestration.automatic_fallback import RoutePolicy
    from stock_data.orchestration.current_observation import (
        CurrentObservationRoute,
        ObservationIdentity,
        ObservationInterval,
    )

    identity = state.get("identity")
    if not isinstance(identity, dict) or set(identity) != {"dataset_id", "market", "symbol"}:
        raise RuntimeError("completed Toss stock current identity is invalid")
    return CurrentObservationRoute(
        fallback_policy=RoutePolicy(
            route_id=str(state.get("route_id", "")),
            primary_provider="tossinvest_open_api",
            primary_route=QUOTE_PATH,
            fallback_provider="UNAVAILABLE",
            fallback_upstream_provider="UNAVAILABLE",
            fallback_route="UNAVAILABLE",
            fallback_enabled=False,
        ),
        identity=ObservationIdentity(**identity),
        interval_precedence=(ObservationInterval.SNAPSHOT,),
    )


def execute_toss_stock_current_quote(
    project_root: Path,
    *,
    expected_market_date: str | date,
    client_factory: Callable[[], TossStockClient] | None,
    symbol: str = "005930",
    state_path: Path = STATE_PATH,
    projection_path: Path = PROJECTION_PATH,
    landing_root: Path = LANDING_ROOT,
    acceptance_validator: AcceptanceValidator | None = None,
    route_suffix: str = "",
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> TossStockCurrentResult:
    """Execute one route once, or replay a completed result without a provider call.

    The function creates the durable `ATTEMPTING` claim before it constructs the
    runtime client.  A failure or interruption can therefore never be retried
    from this operation's date key.
    """
    root = Path(project_root)
    target_date = _target_date(expected_market_date)
    if not isinstance(symbol, str) or not symbol.isdigit() or len(symbol) != 6:
        raise ValueError("Toss stock current symbol must be six digits")
    state_file = root / state_path
    state = _read_state(state_file)
    prior = state["attempts"].get(target_date)
    if prior is not None:
        if not isinstance(prior, dict) or prior.get("status") != "COMPLETE":
            raise RuntimeError("Toss stock current route was already attempted; no repeat is allowed")
        route = _route_from_state(prior)
        replay = CurrentObservationCoordinator(CurrentObservationFileStore(root / projection_path)).replay(route)
        if replay.observation is None:
            raise RuntimeError("completed Toss stock current projection is unreadable")
        return TossStockCurrentResult(
            "API_ZERO_REPLAY", target_date, 0, 0, prior.get("landing_file"),
            prior.get("provider_timestamp_utc"), replay.api_calls,
        )
    if client_factory is None:
        raise ValueError("a runtime Toss client factory is required for a new route")

    attempted_at = clock().astimezone(timezone.utc)
    state["attempts"][target_date] = {
        "status": "ATTEMPTING",
        "attempted_at_utc": attempted_at.isoformat(),
        "route": QUOTE_PATH,
        "params": {"symbols": symbol},
    }
    _atomic_json(state_file, state)

    client: TossStockClient | None = None
    token_before = business_before = 0
    landing_relative: Path | None = None
    provider_timestamp: str | None = None
    try:
        client = client_factory()
        token_before = client.token_request_count
        business_before = client.market_request_count
        response = client.get_market_data(QUOTE_PATH, params={"symbols": symbol})
        token_calls, business_calls = _counts(client, token_before, business_before)
        if token_calls != 1 or business_calls != 1:
            raise RuntimeError("Toss stock current route requires exactly one OAuth and one business GET")

        retrieved_at = clock().astimezone(timezone.utc)
        encoded = json.dumps(response.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        landing_relative = landing_root / f"{symbol}_{target_date}_{retrieved_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        _atomic_json(root / landing_relative, {
            "captured_at_utc": retrieved_at.isoformat(),
            "provider": "tossinvest_open_api",
            "endpoint": QUOTE_PATH,
            "params": {"symbols": symbol},
            "expected_market_date": target_date,
            "raw_response": response.payload,
            "raw_sha256": hashlib.sha256(encoded).hexdigest(),
        })
        candidate = stock_price_snapshot(
            response.payload, symbol=symbol, retrieved_at_utc=retrieved_at.isoformat(), route_suffix=route_suffix,
        )
        provider_time = datetime.fromisoformat(candidate.observation.provider_timestamp_utc)
        provider_timestamp = candidate.observation.provider_timestamp_utc
        if acceptance_validator is None:
            if candidate.market_date != target_date:
                raise RuntimeError("Toss stock current provider timestamp has an unexpected KST market date")
            if retrieved_at - provider_time > MAX_AGE:
                raise RuntimeError("Toss stock current provider timestamp exceeds the 60-minute age gate")
        else:
            acceptance_validator(candidate, response.payload, retrieved_at, target_date)

        coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(root / projection_path))
        decision = coordinator.refresh(
            candidate.route(), primary_attempt=lambda: candidate.source,
            fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("Toss stock current has no fallback")),
        )
        if decision.observation != candidate.observation:
            raise RuntimeError("Toss stock current atomic projection readback mismatch")
        replay = coordinator.replay(candidate.route())
        if replay.observation != candidate.observation or replay.api_calls != 0:
            raise RuntimeError("Toss stock current API-zero replay mismatch")
        state["attempts"][target_date] = {
            "status": "COMPLETE",
            "attempted_at_utc": attempted_at.isoformat(),
            "landing_file": landing_relative.as_posix(),
            "route_id": candidate.observation.route_id,
            "identity": asdict(candidate.observation.identity),
            "provider_timestamp_utc": provider_timestamp,
            "interval": candidate.observation.interval.value,
            "token_calls": token_calls,
            "business_calls": business_calls,
        }
        _atomic_json(state_file, state)
        return TossStockCurrentResult(
            "COMPLETE", target_date, token_calls, business_calls,
            landing_relative.as_posix(), provider_timestamp, replay.api_calls,
        )
    except Exception as error:
        token_calls, business_calls = _counts(client, token_before, business_before)
        state["attempts"][target_date] = {
            "status": "FAILED",
            "attempted_at_utc": attempted_at.isoformat(),
            "failure_type": type(error).__name__,
            "landing_file": landing_relative.as_posix() if landing_relative else None,
            "provider_timestamp_utc": provider_timestamp,
            "token_calls": token_calls,
            "business_calls": business_calls,
        }
        _atomic_json(state_file, state)
        raise


__all__ = ["TossStockCurrentResult", "execute_toss_stock_current_quote"]
