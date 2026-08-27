"""Schema-injected KB intraday capture boundary for display-only observations.

This module contains no KB endpoint or payload-field assumption.  A later
reviewed route contract supplies the exact window IDs and parser.  The runner
is intentionally transport-injected so it neither opens environment variables
nor authenticates on its own; a caller may adapt an existing ``KBSecClient``
response with :func:`transport_result_from_kb_response`.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Mapping, Protocol

from stock_data.orchestration.automatic_fallback import AttemptFailure, FailureKind, SourceObservation
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationInterval,
)
from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.providers.kbsec.client import KBSecResponse


TIMEOUT_SECONDS = 10
RETRY_COUNT = 0
_SCHEMA_VERSION = 1


class KBSecIntradayFoundationError(RuntimeError):
    """Raised for a malformed local contract, ledger, Landing, or projection."""


@dataclass(frozen=True)
class KBSecIntradayTransportResult:
    """A lossless, already-received KB response with its business-call count.

    Authentication remains encapsulated by the existing client.  The later
    route runner is responsible for separately accounting for any authorized
    OAuth call; this generic foundation accounts for exactly one business
    response and never invokes transport itself.
    """

    raw_payload: Mapping[str, object]
    business_requests: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.raw_payload, Mapping):
            raise KBSecIntradayFoundationError("KB intraday Landing payload must be an object")
        if self.business_requests != 1:
            raise KBSecIntradayFoundationError("KB intraday foundation permits exactly one business request")


def transport_result_from_kb_response(response: KBSecResponse) -> KBSecIntradayTransportResult:
    """Adapt a validated ``KBSecClient`` response without accessing credentials."""
    return KBSecIntradayTransportResult(raw_payload=response.raw_payload)


Parser = Callable[[Mapping[str, object], datetime], SourceObservation[CurrentObservation]]
TransportFactory = Callable[[], KBSecIntradayTransportResult]


@dataclass(frozen=True)
class KBSecIntradayContract:
    """Route-neutral contract to be instantiated only after source review.

    No identity, endpoint, unit, session, or parser is encoded here.  The
    injected parser must produce the exact ``CurrentObservationRoute`` selected
    by the future route contract.
    """

    operation_id: str
    route: CurrentObservationRoute
    interval: ObservationInterval
    finality: ObservationFinality
    state_path: Path
    landing_root: Path
    projection_path: Path
    allowed_window_ids: tuple[str, ...]
    parser: Parser
    timeout_seconds: int = TIMEOUT_SECONDS
    retry_count: int = RETRY_COUNT

    def __post_init__(self) -> None:
        if not self.operation_id or self.interval not in {
            ObservationInterval.MINUTES_15,
            ObservationInterval.MINUTES_30,
            ObservationInterval.MINUTES_60,
        }:
            raise KBSecIntradayFoundationError("KB intraday contract requires an exact 15m, 30m, or 60m interval")
        if self.interval not in self.route.interval_precedence:
            raise KBSecIntradayFoundationError("KB intraday interval is absent from route precedence")
        if self.timeout_seconds != TIMEOUT_SECONDS or self.retry_count != RETRY_COUNT:
            raise KBSecIntradayFoundationError("KB intraday timeout/retry policy is fixed")
        if not self.allowed_window_ids or len(set(self.allowed_window_ids)) != len(self.allowed_window_ids):
            raise KBSecIntradayFoundationError("KB intraday contract needs unique exact window IDs")
        if any(not isinstance(window_id, str) or not window_id for window_id in self.allowed_window_ids):
            raise KBSecIntradayFoundationError("KB intraday window IDs must be non-empty strings")
        for path in (self.state_path, self.landing_root, self.projection_path):
            if path.is_absolute() or ".." in path.parts:
                raise KBSecIntradayFoundationError("KB intraday paths must be root-relative")


@dataclass(frozen=True)
class KBSecIntradayRunResult:
    status: str
    window_id: str
    business_requests: int
    landing_sha256: str | None
    replay_api_calls: int
    observation: CurrentObservation | None


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != encoded:
            raise KBSecIntradayFoundationError("KB intraday durable write readback mismatch")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _initial_state(operation_id: str) -> dict[str, object]:
    return {"schema_version": _SCHEMA_VERSION, "operation_id": operation_id, "windows": {}}


def _read_state(path: Path, operation_id: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _initial_state(operation_id)
    except (OSError, json.JSONDecodeError) as error:
        raise KBSecIntradayFoundationError("KB intraday durable ledger is unreadable") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "operation_id", "windows"}
        or payload["schema_version"] != _SCHEMA_VERSION
        or payload["operation_id"] != operation_id
        or not isinstance(payload["windows"], dict)
        or any(not isinstance(key, str) or not isinstance(value, dict) for key, value in payload["windows"].items())
    ):
        raise KBSecIntradayFoundationError("KB intraday durable ledger schema mismatch")
    return payload


class KBSecIntradayWindowedCollector:
    """Durable claim -> Landing hash/readback -> typed atomic projection.

    It does not interpret a KB payload.  The injected parser is invoked only
    after the immutable Landing body has been written and hash-read back.
    """

    def __init__(self, root: Path, contract: KBSecIntradayContract, *, lock: CurrentObservationProcessLock | None = None) -> None:
        self.root = Path(root)
        self.contract = contract
        self.state_path = self.root / contract.state_path
        self.store = CurrentObservationFileStore(self.root / contract.projection_path)
        self.lock = lock or CurrentObservationProcessLock(self.state_path.with_suffix(".lock"))
        self._run_lock = Lock()
        self._running = False

    def replay(self) -> KBSecIntradayRunResult:
        replay = CurrentObservationCoordinator(self.store).replay(self.contract.route)
        return KBSecIntradayRunResult(
            "API_ZERO_REPLAY", "", 0, None, replay.api_calls, replay.observation,
        )

    def is_active(self, *, window_id: str) -> bool:
        if window_id not in self.contract.allowed_window_ids:
            return False
        try:
            state = _read_state(self.state_path, self.contract.operation_id)
        except KBSecIntradayFoundationError:
            return False
        return window_id not in state["windows"]

    def run(self, *, window_id: str, now: datetime, transport_factory: TransportFactory | None) -> KBSecIntradayRunResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise KBSecIntradayFoundationError("KB intraday run clock must be timezone-aware")
        if window_id not in self.contract.allowed_window_ids:
            return self._result("WINDOW_NOT_MANIFESTED", window_id)
        with self._run_lock:
            if self._running:
                return self._result("COALESCED", window_id)
            self._running = True
        try:
            if not self.lock.acquire():
                return self._result("PROCESS_LOCKED", window_id)
            try:
                try:
                    state = _read_state(self.state_path, self.contract.operation_id)
                except KBSecIntradayFoundationError:
                    return self._result("LEDGER_INVALID", window_id)
                windows = dict(state["windows"])
                existing = windows.get(window_id)
                if existing is not None:
                    status = "ORPHAN_ATTEMPTING_NO_REPEAT" if existing.get("status") == "ATTEMPTING" else "NO_REPEAT"
                    return self._result(status, window_id)
                if transport_factory is None:
                    return self._result("NO_TRANSPORT_INJECTED", window_id)

                claim: dict[str, object] = {
                    "status": "ATTEMPTING",
                    "attempted_at_utc": now.astimezone(timezone.utc).isoformat(),
                    "business_requests_reserved": 1,
                    "business_requests_invoked": 0,
                    "business_requests_completed": 0,
                    "retry_count": RETRY_COUNT,
                    "fallback_count": 0,
                }
                windows[window_id] = claim
                state["windows"] = windows
                _atomic_json(self.state_path, state)
                digest: str | None = None
                try:
                    claim["business_requests_invoked"] = 1
                    windows[window_id] = claim
                    state["windows"] = windows
                    _atomic_json(self.state_path, state)
                    response = transport_factory()
                    claim["business_requests_completed"] = response.business_requests
                    body = json.dumps(dict(response.raw_payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    digest = hashlib.sha256(body).hexdigest()
                    landing = self.root / self.contract.landing_root / window_id.replace(":", "") / digest / "body.json"
                    landing.parent.mkdir(parents=True, exist_ok=True)
                    with landing.open("xb") as stream:
                        stream.write(body)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if hashlib.sha256(landing.read_bytes()).hexdigest() != digest:
                        raise KBSecIntradayFoundationError("KB intraday Landing hash readback mismatch")
                    retained = json.loads(landing.read_text(encoding="utf-8"))
                    source = self.contract.parser(retained, now)
                    self._validate_source(source)
                    refreshed = CurrentObservationCoordinator(self.store).refresh(
                        self.contract.route,
                        primary_attempt=lambda: source,
                        fallback_attempt=lambda: (_ for _ in ()).throw(AttemptFailure(
                            FailureKind.SCHEMA_ERROR,
                            safe_code="KB_INTRADAY_NO_FALLBACK",
                            request_count=0,
                        )),
                    )
                    if refreshed.observation is None:
                        raise KBSecIntradayFoundationError("KB intraday projection was not accepted")
                    claim.update({
                        "status": "COMPLETE_ACCEPTED",
                        "business_requests": 1,
                        "landing_file": landing.relative_to(self.root).as_posix(),
                        "landing_sha256": digest,
                        "provider_timestamp_utc": refreshed.observation.provider_timestamp_utc,
                        "projection": self.contract.projection_path.as_posix(),
                    })
                except Exception as error:
                    claim.update({
                        "status": "COMPLETE_FAILURE",
                        "failure_type": type(error).__name__,
                        "business_requests": int(claim["business_requests_invoked"]),
                    })
                    digest = None
                windows[window_id] = claim
                state["windows"] = windows
                _atomic_json(self.state_path, state)
                return self._result(str(claim["status"]), window_id, int(claim["business_requests"]), digest)
            finally:
                self.lock.release()
        finally:
            with self._run_lock:
                self._running = False

    def _validate_source(self, source: SourceObservation[CurrentObservation]) -> None:
        observation = source.value
        if (
            observation.route_id != self.contract.route.route_id
            or observation.identity != self.contract.route.identity
            or observation.interval is not self.contract.interval
            or observation.finality is not self.contract.finality
            or source.provenance.request_count != 1
        ):
            raise KBSecIntradayFoundationError("KB intraday parser output violates its exact route contract")
        observation.validate()

    def _result(self, status: str, window_id: str, business_requests: int = 0, digest: str | None = None) -> KBSecIntradayRunResult:
        replay = CurrentObservationCoordinator(self.store).replay(self.contract.route)
        return KBSecIntradayRunResult(status, window_id, business_requests, digest, replay.api_calls, replay.observation)


__all__ = [
    "KBSecIntradayContract", "KBSecIntradayFoundationError", "KBSecIntradayRunResult",
    "KBSecIntradayTransportResult", "KBSecIntradayWindowedCollector", "RETRY_COUNT",
    "TIMEOUT_SECONDS", "transport_result_from_kb_response",
]
