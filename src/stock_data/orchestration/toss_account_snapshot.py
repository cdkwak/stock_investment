from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any
from uuid import uuid4

from stock_data.providers.tossinvest import (
    TossInvestClient,
    attach_buying_power,
    normalize_buying_power_payload,
    normalize_holdings_payload,
)
from stock_data.orchestration.account_privacy import (
    AccountSnapshotRemovalError,
    account_snapshot_privacy_generation,
    account_snapshot_lifecycle_lock,
    prune_account_landing,
)
from stock_data.gui.account_value_history import toss_account_value_observation


class AccountRefreshTrigger(str, Enum):
    STARTUP = "STARTUP"
    MANUAL = "MANUAL"
    PERIODIC = "PERIODIC"


@dataclass(frozen=True)
class TossAccountRefreshResult:
    status: str
    trigger: AccountRefreshTrigger
    account_calls: int
    token_calls: int = 0
    reason: str | None = None
    collected_at: str | None = None
    normalized_path: str | None = None


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _under(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    candidate.relative_to(root.resolve())
    return candidate


def _account_snapshot_digest(root: Path) -> str | None:
    try:
        payload = json.loads(
            (root / "data/state/toss_account_snapshot.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    digest = payload.get("payload_sha256") if isinstance(payload, dict) else None
    return digest if isinstance(digest, str) and len(digest) == 64 else None


def _lock_stream(stream: Any) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_stream(stream: Any) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def _account_transaction_lock(
    root: Path, *, timeout_seconds: float = 10.0,
):
    """Serialize recovery and promotion with a crash-released OS file lock."""

    lock_path = (
        root / "data/state/transactions/toss_account_snapshot/.transaction.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    with lock_path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        acquired = False
        while not acquired:
            try:
                _lock_stream(stream)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "account snapshot transaction lock timeout"
                    ) from None
                time.sleep(0.01)
        try:
            yield
        finally:
            _unlock_stream(stream)


@contextmanager
def _account_refresh_lock(root: Path, *, timeout_seconds: float = 10.0):
    """Coalesce GUI and scheduled refresh calls before any provider request."""

    with account_snapshot_lifecycle_lock(root, timeout_seconds=timeout_seconds):
        yield


def _rollback_account_snapshot_targets(
    stage: Path, targets: dict[str, Path], *, promotion_started: bool,
) -> None:
    targets["landing"].unlink(missing_ok=True)
    if "history" in targets:
        targets["history"].unlink(missing_ok=True)
    for name in ("normalized", "state"):
        target = targets[name]
        previous = stage / "backup" / f"{name}.json"
        if previous.exists():
            target.unlink(missing_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Keep the backup intact until the whole rollback is durable.  A
            # second failure or process interruption can then retry recovery
            # instead of losing the only prior copy after restoring one file.
            restore = stage / "restore" / f"{name}.json"
            restore.parent.mkdir(parents=True, exist_ok=True)
            with previous.open("rb") as source, restore.open("wb") as destination:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(restore, target)
        elif promotion_started:
            target.unlink(missing_ok=True)


def _recover_incomplete_account_transactions_unlocked(root: Path) -> int:
    journal_root = root / "data/state/transactions/toss_account_snapshot"
    stage_root = (root / "data/staging/toss_account_snapshot").resolve()
    landing_root = (
        root / "data/landing/tossinvest/account_snapshot"
    ).resolve()
    expected_normalized = (
        root / "data/normalized/toss_account_snapshot/latest.json"
    ).resolve()
    expected_state = (root / "data/state/toss_account_snapshot.json").resolve()
    expected_history_root = (
        root / "data/local/account_value_history/toss_self"
    ).resolve()
    recovered = 0
    for journal_path in sorted(journal_root.glob("*.json")):
        validated = False
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if journal.get("status") not in {"PREPARED", "PROMOTING"}:
                continue
            transaction_id = journal["transaction_id"]
            if not isinstance(transaction_id, str) or journal_path.stem != transaction_id:
                continue
            stage = _under(root, journal["stage"])
            targets = {
                name: _under(root, relative)
                for name, relative in journal["targets"].items()
            }
            if set(targets) not in (
                {"landing", "normalized", "state"},
                {"landing", "normalized", "state", "history"},
            ):
                continue
            if (
                stage != stage_root / transaction_id
                or targets["normalized"] != expected_normalized
                or targets["state"] != expected_state
            ):
                continue
            targets["landing"].relative_to(landing_root)
            if targets["landing"].parent != landing_root:
                continue
            if "history" in targets:
                targets["history"].relative_to(expected_history_root)
                if targets["history"].parent != expected_history_root:
                    continue
            validated = True
            _rollback_account_snapshot_targets(
                stage, targets,
                promotion_started=journal["status"] == "PROMOTING",
            )
            _atomic_json(journal_path, {
                **journal,
                "status": "RECOVERED",
                "recovered_at": datetime.now(timezone.utc).isoformat(),
            })
            shutil.rmtree(stage, ignore_errors=True)
            recovered += 1
        except (
            AttributeError,
            KeyError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            # Untrusted journals never authorize a guessed path mutation.
            # A validated rollback that failed retains its stage for retry.
            if validated:
                continue
            continue
    return recovered


def recover_incomplete_account_transactions(project_root: Path) -> int:
    """Roll back only abandoned commits after excluding every live owner."""

    root = project_root.resolve()
    with account_snapshot_lifecycle_lock(root):
        with _account_transaction_lock(root):
            return _recover_incomplete_account_transactions_unlocked(root)


def persist_account_snapshot_atomic(
    project_root: Path,
    snapshot: dict[str, Any],
    *,
    trigger: AccountRefreshTrigger,
    step_hook: Callable[[str], None] | None = None,
) -> str:
    """Commit sanitized Landing, Normalized and state as one rollback unit."""
    root = project_root.resolve()
    with account_snapshot_lifecycle_lock(root):
        with _account_transaction_lock(root):
            return _persist_account_snapshot_atomic_unlocked(
                root, snapshot, trigger=trigger, step_hook=step_hook,
            )


def _persist_account_snapshot_atomic_unlocked(
    root: Path,
    snapshot: dict[str, Any],
    *,
    trigger: AccountRefreshTrigger,
    step_hook: Callable[[str], None] | None,
) -> str:
    _recover_incomplete_account_transactions_unlocked(root)
    body = _json_bytes(snapshot)
    digest = hashlib.sha256(body).hexdigest()
    collected = datetime.fromisoformat(snapshot["collected_at"])
    stamp = collected.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    transaction_id = f"{stamp}-{uuid4().hex}"
    stage = root / "data/staging/toss_account_snapshot" / transaction_id
    landing = (
        root / "data/landing/tossinvest/account_snapshot"
        / f"{stamp}-{digest[:12]}-{transaction_id[-12:]}.json"
    )
    normalized = root / "data/normalized/toss_account_snapshot/latest.json"
    state = root / "data/state/toss_account_snapshot.json"
    history = (
        root / "data/local/account_value_history/toss_self"
        / f"{stamp}-{digest[:12]}-{transaction_id[-12:]}.json"
    )
    journal_path = root / "data/state/transactions/toss_account_snapshot" / f"{transaction_id}.json"
    targets = {
        "landing": landing, "normalized": normalized, "state": state,
        "history": history,
    }
    candidate = stage / "candidate"
    backup = stage / "backup"
    candidate.mkdir(parents=True, exist_ok=False)
    sanitized_landing = {
        "schema_version": 1,
        "capture_kind": "SANITIZED_CONTRACT_PROJECTION",
        "payload_sha256": digest,
        "snapshot": snapshot,
    }
    state_payload = {
        "schema_version": 1,
        "status": "SUCCEEDED",
        "provider": snapshot["provider"],
        "source_operation": snapshot["source_operation"],
        "collected_at": snapshot["collected_at"],
        "trigger": trigger.value,
        "payload_sha256": digest,
        "landing": _relative(root, landing),
        "normalized": _relative(root, normalized),
    }
    _atomic_json(candidate / "landing.json", sanitized_landing)
    _atomic_json(candidate / "normalized.json", snapshot)
    _atomic_json(candidate / "state.json", state_payload)
    _atomic_json(candidate / "history.json", toss_account_value_observation(snapshot))
    journal = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "status": "PREPARED",
        "stage": _relative(root, stage),
        "targets": {name: _relative(root, path) for name, path in targets.items()},
        "payload_sha256": digest,
    }
    _atomic_json(journal_path, journal)
    if step_hook:
        step_hook("PREPARED")
    promotion_started = False
    cleanup_stage = False
    try:
        backup.mkdir(parents=True, exist_ok=True)
        for name, target in (("normalized", normalized), ("state", state)):
            if target.exists():
                os.replace(target, backup / f"{name}.json")
        _atomic_json(journal_path, {**journal, "status": "PROMOTING"})
        promotion_started = True
        for name, target in targets.items():
            if target.exists():
                raise FileExistsError(f"account snapshot target unexpectedly exists: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(candidate / f"{name}.json", target)
            if step_hook:
                step_hook(f"PROMOTED_{name.upper()}")
        _atomic_json(journal_path, {**journal, "status": "SUCCEEDED"})
        cleanup_stage = True
    except BaseException:
        for _attempt in range(2):
            try:
                _rollback_account_snapshot_targets(
                    stage, targets, promotion_started=promotion_started,
                )
                _atomic_json(journal_path, {**journal, "status": "ROLLED_BACK"})
            except BaseException:
                continue
            cleanup_stage = True
            break
        raise
    finally:
        if cleanup_stage:
            shutil.rmtree(stage, ignore_errors=True)
    try:
        prune_account_landing(root, keep=1)
    except (OSError, ValueError):
        # Retention cleanup must never invalidate a newly committed snapshot.
        # A later successful refresh or the explicit privacy removal control
        # can retry cleanup without exposing the filesystem error.
        pass
    return _relative(root, normalized)


class TossAccountSnapshotRefresher:
    """Read-only runtime coordinator; credentials and account selectors stay in memory."""

    def __init__(
        self,
        *,
        project_root: Path,
        client: TossInvestClient,
        account_seq: int | None = None,
        periodic_interval_seconds: float = 60.0,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if account_seq is not None and (
            isinstance(account_seq, bool) or not isinstance(account_seq, int) or account_seq <= 0
        ):
            raise ValueError("account_seq must be a positive runtime-only integer")
        # ASSET is 5 TPS; the default is deliberately much slower.  Disallow a
        # policy that could exceed the documented provider group limit.
        if periodic_interval_seconds < 0.2:
            raise ValueError("periodic refresh interval exceeds the ASSET limit")
        self.project_root = project_root
        self.client = client
        self._account_seq = account_seq
        self.periodic_interval_seconds = float(periodic_interval_seconds)
        self.clock = clock
        self.monotonic = monotonic
        self._last_attempt: float | None = None
        self._last_success: float | None = None
        self._privacy_generation = account_snapshot_privacy_generation(
            project_root
        )

    def refresh(self, trigger: AccountRefreshTrigger) -> TossAccountRefreshResult:
        root = self.project_root.resolve()
        try:
            # A second requester must not wait for a failed first request and
            # then make its own provider cycle.  Acquire before even reading
            # client counters so this typed result is API-zero.
            with _account_refresh_lock(root, timeout_seconds=0.0):
                if account_snapshot_privacy_generation(root) != self._privacy_generation:
                    return TossAccountRefreshResult(
                        "NOOP_PRIVACY_REMOVED", trigger, 0,
                        reason="ACCOUNT_SNAPSHOT_REMOVED",
                    )
                calls_before = self.client.account_request_count
                token_before = int(getattr(self.client, "token_request_count", 0))
                digest_before_lock = _account_snapshot_digest(root)
                if _account_snapshot_digest(root) != digest_before_lock:
                    return TossAccountRefreshResult(
                        "NOOP_CONCURRENT_REFRESH", trigger, 0,
                        reason="CONCURRENT_REFRESH_ALREADY_COMMITTED",
                    )
                now_tick = float(self.monotonic())
                if (
                    self._last_attempt is not None
                    and now_tick - self._last_attempt < 0.2
                ):
                    return TossAccountRefreshResult(
                        "NOOP_RATE_GATED", trigger, 0,
                        reason="ASSET_RATE_LIMIT_GUARD",
                    )
                if (
                    trigger is AccountRefreshTrigger.PERIODIC
                    and self._last_success is not None
                    and now_tick - self._last_success
                    < self.periodic_interval_seconds
                ):
                    return TossAccountRefreshResult(
                        "NOOP_INTERVAL_NOT_DUE", trigger, 0,
                        reason="PERIODIC_INTERVAL_GUARD",
                    )
                self._last_attempt = now_tick
                with _account_transaction_lock(root):
                    _recover_incomplete_account_transactions_unlocked(root)
                if self._account_seq is None:
                    self._account_seq = self.client.brokerage_account_seq()
                response = self.client.get_holdings(account_seq=self._account_seq)
                buying_power = [
                    normalize_buying_power_payload(
                        self.client.get_buying_power(
                            account_seq=self._account_seq, currency=currency,
                        ).payload,
                        expected_currency=currency,
                    )
                    for currency in ("KRW", "USD")
                ]
                collected_at = self.clock()
                snapshot = attach_buying_power(
                    normalize_holdings_payload(
                        response.payload, collected_at=collected_at
                    ),
                    buying_power,
                )
                with _account_transaction_lock(root):
                    normalized_path = _persist_account_snapshot_atomic_unlocked(
                        root, snapshot, trigger=trigger, step_hook=None,
                    )
        except TimeoutError:
            return TossAccountRefreshResult(
                "NOOP_CONCURRENT_REFRESH", trigger, 0,
                reason="CONCURRENT_REFRESH_IN_PROGRESS",
            )
        except AccountSnapshotRemovalError:
            return TossAccountRefreshResult(
                "NOOP_PRIVACY_REMOVED", trigger, 0,
                reason="ACCOUNT_SNAPSHOT_REMOVED",
            )
        except Exception:
            return TossAccountRefreshResult(
                "FAILED_PRESERVED_PRIOR",
                trigger,
                self.client.account_request_count - calls_before,
                token_calls=(
                    int(getattr(self.client, "token_request_count", 0))
                    - token_before
                ),
                reason="ACCOUNT_REFRESH_FAILED_CLOSED",
            )
        self._last_success = now_tick
        return TossAccountRefreshResult(
            "SUCCEEDED",
            trigger,
            self.client.account_request_count - calls_before,
            token_calls=(
                int(getattr(self.client, "token_request_count", 0)) - token_before
            ),
            collected_at=snapshot["collected_at"],
            normalized_path=normalized_path,
        )


__all__ = [
    "AccountRefreshTrigger",
    "TossAccountRefreshResult",
    "TossAccountSnapshotRefresher",
    "persist_account_snapshot_atomic",
    "recover_incomplete_account_transactions",
]
