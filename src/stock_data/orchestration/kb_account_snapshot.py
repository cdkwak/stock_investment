from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any
from uuid import uuid4

from stock_data.providers.kbsec.account import (
    KBSecAccountContractError,
    normalize_domestic_balance_payload,
)
from stock_data.gui.account_value_history import kb_account_value_observation
from stock_data.orchestration.account_privacy import account_snapshot_lifecycle_lock


@dataclass(frozen=True)
class KBAccountRefreshResult:
    status: str
    supplier_calls: int
    reason: str | None = None
    collected_at: str | None = None
    snapshot_path: str | None = None


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
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
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
def _transaction_lock(root: Path, *, timeout_seconds: float) -> Any:
    lock_path = (
        root / "data/state/transactions/kbsec_account_snapshot/.transaction.lock"
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
                    raise TimeoutError("KB account snapshot transaction lock timeout") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            _unlock_stream(stream)


def _rollback(
    stage: Path,
    targets: dict[str, Path],
    *,
    promotion_started: bool,
) -> None:
    targets["landing"].unlink(missing_ok=True)
    if "history" in targets:
        targets["history"].unlink(missing_ok=True)
    for name in ("snapshot", "state"):
        target = targets[name]
        previous = stage / "backup" / f"{name}.json"
        if previous.exists():
            target.unlink(missing_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            restore = stage / "restore" / f"{name}.json"
            restore.parent.mkdir(parents=True, exist_ok=True)
            with previous.open("rb") as source, restore.open("wb") as destination:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(restore, target)
        elif promotion_started:
            target.unlink(missing_ok=True)


def _recover_unlocked(root: Path, *, fail_on_incomplete: bool = False) -> int:
    journal_root = root / "data/state/transactions/kbsec_account_snapshot"
    stage_root = (root / "data/staging/kbsec_account_snapshot").resolve()
    landing_root = (root / "data/landing/kbsec/account_snapshot").resolve()
    expected_snapshot = (root / "data/local/account_snapshots/kb_self.json").resolve()
    expected_state = (root / "data/state/kbsec_account_snapshot.json").resolve()
    expected_history_root = (
        root / "data/local/account_value_history/kb_self"
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
            if set(targets) != {"landing", "snapshot", "state", "history"}:
                continue
            if (
                stage != stage_root / transaction_id
                or targets["snapshot"] != expected_snapshot
                or targets["state"] != expected_state
            ):
                continue
            targets["landing"].relative_to(landing_root)
            if targets["landing"].parent != landing_root:
                continue
            targets["history"].relative_to(expected_history_root)
            if targets["history"].parent != expected_history_root:
                continue
            validated = True
            _rollback(
                stage,
                targets,
                promotion_started=journal["status"] == "PROMOTING",
            )
            _atomic_json(
                journal_path,
                {
                    **journal,
                    "status": "RECOVERED",
                    "recovered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
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
            # An untrusted or corrupt journal never authorizes a guessed path mutation.
            if validated and fail_on_incomplete:
                raise RuntimeError(
                    "KB account snapshot recovery remains incomplete"
                ) from None
            continue
    return recovered


def recover_incomplete_kb_account_transactions(
    project_root: Path,
    *,
    lock_timeout_seconds: float = 10.0,
) -> int:
    root = project_root.resolve()
    with account_snapshot_lifecycle_lock(
        root, timeout_seconds=lock_timeout_seconds,
    ):
        with _transaction_lock(root, timeout_seconds=lock_timeout_seconds):
            return _recover_unlocked(root)


def _persist_unlocked(
    root: Path,
    snapshot: dict[str, Any],
    *,
    step_hook: Callable[[str], None] | None,
) -> str:
    body = _json_bytes(snapshot)
    digest = hashlib.sha256(body).hexdigest()
    collected = datetime.fromisoformat(snapshot["collected_at"])
    stamp = collected.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    transaction_id = f"{stamp}-{uuid4().hex}"
    stage = root / "data/staging/kbsec_account_snapshot" / transaction_id
    landing = (
        root
        / "data/landing/kbsec/account_snapshot"
        / f"{stamp}-{digest[:12]}-{transaction_id[-12:]}.json"
    )
    local_snapshot = root / "data/local/account_snapshots/kb_self.json"
    state = root / "data/state/kbsec_account_snapshot.json"
    history = (
        root / "data/local/account_value_history/kb_self"
        / f"{stamp}-{digest[:12]}-{transaction_id[-12:]}.json"
    )
    journal_path = (
        root
        / "data/state/transactions/kbsec_account_snapshot"
        / f"{transaction_id}.json"
    )
    targets = {
        "landing": landing, "snapshot": local_snapshot, "state": state,
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
        "payload_sha256": digest,
        "landing": _relative(root, landing),
        "snapshot": _relative(root, local_snapshot),
    }
    _atomic_json(candidate / "landing.json", sanitized_landing)
    _atomic_json(candidate / "snapshot.json", snapshot)
    _atomic_json(candidate / "state.json", state_payload)
    _atomic_json(candidate / "history.json", kb_account_value_observation(snapshot))
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
        for name, target in (("snapshot", local_snapshot), ("state", state)):
            if target.exists():
                os.replace(target, backup / f"{name}.json")
        _atomic_json(journal_path, {**journal, "status": "PROMOTING"})
        promotion_started = True
        for name, target in targets.items():
            if target.exists():
                raise FileExistsError(
                    f"KB account snapshot target unexpectedly exists: {name}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(candidate / f"{name}.json", target)
            if step_hook:
                step_hook(f"PROMOTED_{name.upper()}")
        _atomic_json(journal_path, {**journal, "status": "SUCCEEDED"})
        cleanup_stage = True
    except BaseException:
        for _attempt in range(2):
            try:
                _rollback(stage, targets, promotion_started=promotion_started)
                _atomic_json(journal_path, {**journal, "status": "ROLLED_BACK"})
            except BaseException:
                continue
            cleanup_stage = True
            break
        raise
    finally:
        if cleanup_stage:
            shutil.rmtree(stage, ignore_errors=True)
    return _relative(root, local_snapshot)


class KBAccountSnapshotCoordinator:
    """Offline-safe coordinator whose response supplier is always injected."""

    def __init__(
        self,
        *,
        project_root: Path,
        response_supplier: Callable[[], dict[str, Any]],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        lock_timeout_seconds: float = 10.0,
        step_hook: Callable[[str], None] | None = None,
    ) -> None:
        if lock_timeout_seconds < 0:
            raise ValueError("lock_timeout_seconds cannot be negative")
        self.project_root = project_root
        self.response_supplier = response_supplier
        self.clock = clock
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self.step_hook = step_hook

    def refresh_manual(self) -> KBAccountRefreshResult:
        calls = 0
        stage = "LOCK"
        try:
            root = self.project_root.resolve()
            with account_snapshot_lifecycle_lock(
                root, timeout_seconds=self.lock_timeout_seconds,
            ):
                with _transaction_lock(root, timeout_seconds=self.lock_timeout_seconds):
                    _recover_unlocked(root, fail_on_incomplete=True)
                    stage = "SUPPLIER"
                    calls = 1
                    payload = self.response_supplier()
                    stage = "NORMALIZE"
                    collected_at = self.clock()
                    snapshot = normalize_domestic_balance_payload(
                        payload,
                        collected_at=collected_at,
                    )
                    stage = "PERSIST"
                    snapshot_path = _persist_unlocked(
                        root,
                        snapshot,
                        step_hook=self.step_hook,
                    )
        except TimeoutError:
            if stage == "LOCK":
                reason = "KB_ACCOUNT_LOCK_TIMEOUT"
            elif stage == "SUPPLIER":
                reason = "KB_ACCOUNT_SUPPLIER_TIMEOUT"
            else:
                reason = "KB_ACCOUNT_PERSISTENCE_FAILED"
        except PermissionError:
            reason = (
                "KB_ACCOUNT_SUPPLIER_AUTH_FAILED"
                if stage == "SUPPLIER"
                else "KB_ACCOUNT_PERSISTENCE_FAILED"
            )
        except KBSecAccountContractError:
            reason = "KB_ACCOUNT_RESPONSE_REJECTED"
        except Exception:
            reason = (
                "KB_ACCOUNT_SUPPLIER_FAILED"
                if stage == "SUPPLIER"
                else "KB_ACCOUNT_PERSISTENCE_FAILED"
            )
        else:
            return KBAccountRefreshResult(
                status="SUCCEEDED",
                supplier_calls=calls,
                collected_at=snapshot["collected_at"],
                snapshot_path=snapshot_path,
            )
        return KBAccountRefreshResult(
            status="FAILED_PRESERVED_PRIOR",
            supplier_calls=calls,
            reason=reason,
        )


__all__ = [
    "KBAccountRefreshResult",
    "KBAccountSnapshotCoordinator",
    "recover_incomplete_kb_account_transactions",
]
