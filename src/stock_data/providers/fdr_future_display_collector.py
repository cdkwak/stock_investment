"""Durably claimed, manifest-gated future-date FDR display collector.

There is deliberately no FinanceDataReader import and no default HTTP
transport. A separately authorized caller injects a bounded transport only
after this module has atomically claimed the exact route in its journal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Sequence

from stock_data.orchestration.current_observation import CurrentObservationFileStore, ObservationIdentity
from stock_data.providers.fdr_display_daily import FDRDisplayDailyLandingStore, FDRDisplayDailyRefreshResult, FDRDisplayDailyResponse, FDRDisplayDailyRefresher

RUNBOOK_PATH = "docs/data/operations/FDR_FUTURE_DISPLAY_DAILY.md"
CHECKPOINT_PATH = Path("data/state/fdr_future_display_collector.json")
LOCK_PATH = Path("data/state/fdr_future_display_collector.lock")
PROJECTION_PATH = Path("data/state/current_observations/fdr_display_daily.json")
LANDING_ROOT = Path("data/landing/fdr_display_daily")
TIMEOUT_SECONDS, RETRY_COUNT = 10, 0
_CONSUMED_REPLAY_ONLY = {ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930")}
_ACTIVATION_ID = re.compile(r"[A-Z0-9][A-Z0-9_.-]{0,63}")
_TERMINAL_ROUTE_STATES = frozenset(("COMPLETE", "FAILED", "ORPHANED"))


class FDRFutureManifestError(ValueError):
    """The activation scope is invalid before any transport construction."""


class FDRFutureCollectorBusy(RuntimeError):
    """Another live process owns the collector lock."""


@dataclass(frozen=True)
class FDRFutureActivation:
    activation_id: str
    source_date: date
    identities: tuple[ObservationIdentity, ...]
    global_request_cap: int
    continue_after_orphan: bool
    manifest_sha256: str


@dataclass(frozen=True)
class FDRFutureCollectionRouteResult:
    identity: ObservationIdentity
    route: str
    outcome: str
    api_calls: int | None
    primary_safe_code: str | None


@dataclass(frozen=True)
class FDRFutureCollectionResult:
    status: str
    activation_id: str
    source_date: str
    provider_api_calls: int
    replay_api_calls: int
    routes: tuple[FDRFutureCollectionRouteResult, ...]


TransportFactory = Callable[[str], Callable[[str, date, date, int, int], FDRDisplayDailyResponse]]
AfterRefresh = Callable[[FDRFutureCollectionRouteResult], None]


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        wait_object_0 = 0x00000000
        error_invalid_parameter = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait_for_single_object.restype = wintypes.DWORD
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        handle = open_process(synchronize, False, pid)
        if not handle:
            if ctypes.get_last_error() == error_invalid_parameter:
                return False
            return True
        try:
            return wait_for_single_object(handle, 0) != wait_object_0
        finally:
            close_handle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def _process_lock(root: Path) -> Iterator[None]:
    """Exclusive same-volume lock; a dead owner leaves a safely reclaimable file."""
    path = root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = json.dumps({"pid": os.getpid()}).encode("utf-8")
    for _ in range(2):
        try:
            with path.open("xb") as stream:
                stream.write(owner)
                stream.flush()
                os.fsync(stream.fileno())
            break
        except FileExistsError:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise FDRFutureCollectorBusy("FDR future-display lock is unreadable")
            if not isinstance(existing, dict) or _pid_alive(existing.get("pid")):
                raise FDRFutureCollectorBusy("FDR future-display collector is already running")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    else:
        raise FDRFutureCollectorBusy("FDR future-display collector lock contention")
    try:
        yield
    finally:
        try:
            if path.read_bytes() == owner:
                path.unlink()
        except FileNotFoundError:
            pass


def _load_checkpoint(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 2, "activations": {}}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "activations"}:
        raise RuntimeError("FDR future-display checkpoint schema mismatch")
    if payload["schema_version"] not in (1, 2) or not isinstance(payload["activations"], dict):
        raise RuntimeError("FDR future-display checkpoint is invalid")
    return payload


def _migrate_checkpoint(checkpoint: dict[str, object]) -> dict[str, object]:
    """Keep old completed activations terminal; never make them callable."""
    if checkpoint["schema_version"] == 2:
        return checkpoint
    activations: dict[str, object] = {}
    for activation_id, raw in checkpoint["activations"].items():
        if not isinstance(raw, dict) or not isinstance(raw.get("routes"), list):
            raise RuntimeError("legacy FDR future-display activation is invalid")
        routes = []
        for item in raw["routes"]:
            if not isinstance(item, dict) or not isinstance(item.get("identity"), dict) or not isinstance(item.get("route"), str):
                raise RuntimeError("legacy FDR future-display route is invalid")
            routes.append({"identity": item["identity"], "route": item["route"],
                           "state": "COMPLETE" if item.get("primary_safe_code") is None else "FAILED",
                           "api_calls": item.get("api_calls"), "primary_safe_code": item.get("primary_safe_code")})
        activations[activation_id] = {"manifest_sha256": raw.get("manifest_sha256"), "source_date": raw.get("source_date"),
                                      "continue_after_orphan": False, "state": "TERMINAL", "routes": routes,
                                      "provider_api_calls": raw.get("provider_api_calls", 0)}
    return {"schema_version": 2, "activations": activations}


def _manifest_payload(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError as error:
        raise FDRFutureManifestError("activation manifest is required") from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FDRFutureManifestError("activation manifest must be JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "activation_id", "runbook", "approved_on", "source_date", "identities", "global_request_cap", "execution_authorized", "continue_after_orphan"}:
        raise FDRFutureManifestError("activation manifest schema mismatch")
    return payload, raw


def load_future_activation(manifest_path: Path, *, clock: Callable[[], datetime]) -> FDRFutureActivation:
    """Validate the whole scope before a caller can create transport."""
    payload, raw = _manifest_payload(manifest_path)
    if payload["schema_version"] != 1 or payload["runbook"] != RUNBOOK_PATH or payload["execution_authorized"] is not True:
        raise FDRFutureManifestError("activation manifest is not externally authorized for this runbook")
    if not isinstance(payload["continue_after_orphan"], bool):
        raise FDRFutureManifestError("continue_after_orphan must be boolean")
    activation_id = payload["activation_id"]
    if not isinstance(activation_id, str) or not _ACTIVATION_ID.fullmatch(activation_id):
        raise FDRFutureManifestError("activation_id is invalid")
    try:
        approved_on, source_date = date.fromisoformat(str(payload["approved_on"])), date.fromisoformat(str(payload["source_date"]))
    except ValueError as error:
        raise FDRFutureManifestError("approved_on and source_date must be ISO dates") from error
    if source_date <= approved_on:
        raise FDRFutureManifestError("activation source_date must be strictly future of approval")
    if not isinstance(payload["identities"], list) or not payload["identities"]:
        raise FDRFutureManifestError("activation requires at least one identity")
    identities: list[ObservationIdentity] = []
    for raw_identity in payload["identities"]:
        if not isinstance(raw_identity, dict) or set(raw_identity) != {"dataset_id", "market", "symbol"}:
            raise FDRFutureManifestError("activation identity schema mismatch")
        try:
            identity = ObservationIdentity(**raw_identity)
            identity.validate()
            FDRDisplayDailyRefresher.spec_for(identity)
        except (TypeError, ValueError) as error:
            raise FDRFutureManifestError("activation identity is not allowlisted") from error
        if identity in _CONSUMED_REPLAY_ONLY:
            raise FDRFutureManifestError("consumed NAVER:005930 scope is replay-only")
        identities.append(identity)
    if len(set(identities)) != len(identities):
        raise FDRFutureManifestError("activation identities must be unique")
    cap = payload["global_request_cap"]
    if not isinstance(cap, int) or isinstance(cap, bool) or cap != len(identities):
        raise FDRFutureManifestError("global_request_cap must equal one request per selected identity")
    return FDRFutureActivation(activation_id, source_date, tuple(identities), cap, payload["continue_after_orphan"], hashlib.sha256(raw).hexdigest())


def _new_activation(activation: FDRFutureActivation) -> dict[str, object]:
    return {"manifest_sha256": activation.manifest_sha256, "source_date": activation.source_date.isoformat(),
            "continue_after_orphan": activation.continue_after_orphan, "state": "IN_PROGRESS", "provider_api_calls": 0,
            "routes": [{"identity": asdict(identity), "route": FDRDisplayDailyRefresher.spec_for(identity).route,
                        "state": "PENDING", "api_calls": None, "primary_safe_code": None} for identity in activation.identities]}


def _assert_activation_matches(raw: object, activation: FDRFutureActivation) -> dict[str, object]:
    if not isinstance(raw, dict) or raw.get("manifest_sha256") != activation.manifest_sha256:
        raise FDRFutureManifestError("activation_id was already used with a different manifest")
    if raw.get("source_date") != activation.source_date.isoformat() or raw.get("continue_after_orphan") is not activation.continue_after_orphan:
        raise FDRFutureManifestError("activation checkpoint scope mismatch")
    if raw.get("state") not in ("IN_PROGRESS", "TERMINAL", "STOPPED_ORPHAN") or not isinstance(raw.get("routes"), list) or not isinstance(raw.get("provider_api_calls"), int):
        raise RuntimeError("FDR future-display activation journal is invalid")
    return raw


def _route_result(raw: object) -> FDRFutureCollectionRouteResult:
    if not isinstance(raw, dict):
        raise RuntimeError("FDR future-display route journal is invalid")
    try:
        identity = ObservationIdentity(**raw["identity"])
    except (TypeError, ValueError) as error:
        raise RuntimeError("FDR future-display route identity is invalid") from error
    state = raw.get("state")
    if state not in {"PENDING", "ATTEMPTING", *_TERMINAL_ROUTE_STATES}:
        raise RuntimeError("FDR future-display route state is invalid")
    return FDRFutureCollectionRouteResult(identity, str(raw.get("route", "")), str(state), raw.get("api_calls"), raw.get("primary_safe_code"))


def _replay(activation: FDRFutureActivation, refresher: FDRDisplayDailyRefresher) -> FDRFutureCollectionResult:
    routes = []
    for identity in activation.identities:
        if refresher.replay(identity).api_calls != 0:
            raise RuntimeError("FDR future-display replay attempted provider access")
        routes.append(FDRFutureCollectionRouteResult(identity, FDRDisplayDailyRefresher.spec_for(identity).route, "API_ZERO_REPLAY", 0, None))
    return FDRFutureCollectionResult("API_ZERO_REPLAY", activation.activation_id, activation.source_date.isoformat(), 0, 0, tuple(routes))


def _result_from_journal(activation: FDRFutureActivation, raw: dict[str, object], provider_calls: int, status: str) -> FDRFutureCollectionResult:
    return FDRFutureCollectionResult(status, activation.activation_id, activation.source_date.isoformat(), provider_calls, 0, tuple(_route_result(item) for item in raw["routes"]))


def execute_future_collection(project_root: Path, manifest_path: Path, *, transport_factory: TransportFactory,
                              clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                              after_route_refresh: AfterRefresh = lambda _result: None) -> FDRFutureCollectionResult:
    """Claim each route before transport; orphan claims are never retried."""
    activation = load_future_activation(manifest_path, clock=clock)
    if activation.source_date > clock().astimezone(timezone.utc).date():
        raise FDRFutureManifestError("activation source_date has not arrived")
    root, checkpoint_path = Path(project_root), Path(project_root) / CHECKPOINT_PATH
    with _process_lock(root):
        checkpoint = _migrate_checkpoint(_load_checkpoint(checkpoint_path))
        raw = checkpoint["activations"].get(activation.activation_id)
        if raw is None:
            raw = _new_activation(activation)
            checkpoint["activations"][activation.activation_id] = raw
            _atomic_json(checkpoint_path, checkpoint)  # durable before any factory call
        raw = _assert_activation_matches(raw, activation)
        refresher = FDRDisplayDailyRefresher(store=CurrentObservationFileStore(root / PROJECTION_PATH),
            landing=FDRDisplayDailyLandingStore(root / LANDING_ROOT), now=clock)
        if raw["state"] in ("TERMINAL", "STOPPED_ORPHAN"):
            return _replay(activation, refresher)
        orphaned = False
        for route_raw in raw["routes"]:
            if route_raw.get("state") == "ATTEMPTING":
                route_raw.update({"state": "ORPHANED", "api_calls": None, "primary_safe_code": "FDR_DISPLAY_ORPHAN_NO_REPEAT"})
                orphaned = True
        if orphaned:
            if not activation.continue_after_orphan:
                raw["state"] = "STOPPED_ORPHAN"
                _atomic_json(checkpoint_path, checkpoint)
                return _result_from_journal(activation, raw, 0, "ORPHANED_STOP")
            _atomic_json(checkpoint_path, checkpoint)
        total_calls = 0
        for route_raw in raw["routes"]:
            if route_raw.get("state") in _TERMINAL_ROUTE_STATES:
                continue
            if route_raw.get("state") != "PENDING":
                raise RuntimeError("FDR future-display route is not claimable")
            if total_calls >= activation.global_request_cap:
                raise RuntimeError("FDR future-display global request cap exhausted")
            route_raw["state"] = "ATTEMPTING"
            _atomic_json(checkpoint_path, checkpoint)  # claim before factory construction/invocation
            identity = ObservationIdentity(**route_raw["identity"])
            spec = FDRDisplayDailyRefresher.spec_for(identity)
            result: FDRDisplayDailyRefreshResult = refresher.refresh(identity=identity, start=activation.source_date, end=activation.source_date,
                transport=transport_factory(spec.route))
            if result.api_calls != 1 or total_calls + result.api_calls > activation.global_request_cap:
                raise RuntimeError("FDR future-display global request cap exceeded")
            total_calls += result.api_calls
            route_result = FDRFutureCollectionRouteResult(identity, spec.route, result.outcome.value, result.api_calls, result.primary_safe_code)
            after_route_refresh(route_result)  # test seam: crash here leaves durable ATTEMPTING claim.
            route_raw.update({"state": "COMPLETE" if result.observation is not None and result.primary_safe_code is None else "FAILED",
                              "api_calls": result.api_calls, "primary_safe_code": result.primary_safe_code})
            raw["provider_api_calls"] += result.api_calls
            _atomic_json(checkpoint_path, checkpoint)
        raw["state"] = "TERMINAL"
        _atomic_json(checkpoint_path, checkpoint)
        status = "COMPLETE" if all(item.get("state") == "COMPLETE" for item in raw["routes"]) else "PARTIAL_OR_FAILED"
        return _result_from_journal(activation, raw, total_calls, status)


def main(argv: Sequence[str] | None = None) -> int:
    """Safe CLI: validate an activation, or replay retained state; never fetch."""
    parser = argparse.ArgumentParser(description="Validate/replay a future FDR display activation (network disabled).")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--project-root", default=Path("."), type=Path)
    parser.add_argument("--replay", action="store_true", help="read retained projection only; still no provider transport")
    args = parser.parse_args(argv)
    activation = load_future_activation(args.manifest, clock=lambda: datetime.now(timezone.utc))
    if args.replay:
        with _process_lock(args.project_root):
            checkpoint = _migrate_checkpoint(_load_checkpoint(args.project_root / CHECKPOINT_PATH))
            raw = _assert_activation_matches(checkpoint["activations"].get(activation.activation_id), activation)
            if raw["state"] not in ("TERMINAL", "STOPPED_ORPHAN"):
                raise SystemExit("activation is not terminal; replay is fail-closed")
            refresher = FDRDisplayDailyRefresher(store=CurrentObservationFileStore(args.project_root / PROJECTION_PATH),
                landing=FDRDisplayDailyLandingStore(args.project_root / LANDING_ROOT), now=lambda: datetime.now(timezone.utc))
            print(json.dumps(asdict(_replay(activation, refresher)), default=str, sort_keys=True))
    else:
        print(json.dumps({"status": "MANIFEST_VALIDATED_API_0", "activation_id": activation.activation_id,
                          "source_date": activation.source_date.isoformat(), "route_count": len(activation.identities)}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["CHECKPOINT_PATH", "FDRFutureActivation", "FDRFutureCollectionResult", "FDRFutureCollectorBusy", "FDRFutureManifestError",
           "LANDING_ROOT", "LOCK_PATH", "PROJECTION_PATH", "RUNBOOK_PATH", "execute_future_collection", "load_future_activation", "main"]
