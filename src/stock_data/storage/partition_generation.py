from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from typing import BinaryIO


_SCHEMA = "stock-data-partition-generation/v1"
_TRANSACTION_DIR = ".partition-generation"
_JOURNAL = ".partition-generation.json"
_JOURNAL_TEMPORARY = ".partition-generation.journal.tmp"
_LOCK_REGISTRY = "stock-data-partition-generation-locks-v1"
_PHASES = frozenset({"PREPARED", "PROMOTING", "COMMITTED"})


class PartitionGenerationError(RuntimeError):
    """Raised when a partition generation cannot be recovered safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _root_path(root: Path) -> Path:
    return Path(os.path.abspath(Path(root)))


def _transaction_path(root: Path) -> Path:
    return root / _TRANSACTION_DIR


def _journal_path(root: Path) -> Path:
    return root / _JOURNAL


def _journal_temporary_path(root: Path) -> Path:
    return root / _JOURNAL_TEMPORARY


def _lock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _generation_lock_identity(root: Path) -> str:
    try:
        canonical = root.resolve(strict=False)
    except OSError as error:
        raise PartitionGenerationError("partition root identity is unavailable") from error
    return hashlib.sha256(
        os.path.normcase(str(canonical)).encode("utf-8")
    ).hexdigest()


def _generation_lock_attempt() -> None:
    """Test seam immediately before the process-shared lock acquisition."""


@contextmanager
def _windows_generation_lock(identity: str) -> Iterator[None]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    wait = kernel32.WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    release = kernel32.ReleaseMutex
    release.argtypes = (wintypes.HANDLE,)
    release.restype = wintypes.BOOL
    close = kernel32.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL

    handle = create_mutex(None, False, f"Global\\StockDataPartitionGeneration-{identity}")
    if not handle:
        raise PartitionGenerationError("partition generation mutex is unavailable")
    acquired = False
    try:
        outcome = wait(handle, 0xFFFFFFFF)
        if outcome not in {0x00000000, 0x00000080}:
            raise PartitionGenerationError("partition generation mutex wait failed")
        acquired = True
        yield
    finally:
        if acquired:
            release(handle)
        close(handle)


def _posix_generation_lock_path(identity: str) -> Path:
    base = Path("/var/tmp") if Path("/var/tmp").is_dir() else Path("/tmp")
    registry = base / f"{_LOCK_REGISTRY}-{os.geteuid()}"
    registry.mkdir(mode=0o700, exist_ok=True)
    metadata = registry.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise PartitionGenerationError("partition lock registry is unsafe")
    return registry / f"{identity}.lock"


@contextmanager
def _posix_generation_lock(identity: str) -> Iterator[None]:
    lock_path = _posix_generation_lock_path(identity)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise PartitionGenerationError("partition generation lock is unsafe") from error
    with os.fdopen(descriptor, "r+b", buffering=0) as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise PartitionGenerationError("partition generation lock is unsafe")
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            os.fsync(stream.fileno())
        _lock_stream(stream)
        try:
            yield
        finally:
            _unlock_stream(stream)


@contextmanager
def _exclusive_generation(root: Path) -> Iterator[None]:
    identity = _generation_lock_identity(root)
    _generation_lock_attempt()
    lock = _windows_generation_lock if os.name == "nt" else _posix_generation_lock
    with lock(identity):
        yield


def _relative_target(root: Path, target: Path) -> str:
    absolute = _root_path(target)
    try:
        relative = absolute.relative_to(root)
    except ValueError as error:
        raise PartitionGenerationError("partition target is outside its root") from error
    if (
        relative.name != "data.parquet"
        or len(relative.parts) < 2
        or any(part in {"", ".", "..", _TRANSACTION_DIR} for part in relative.parts)
    ):
        raise PartitionGenerationError("partition target path is invalid")
    return relative.as_posix()


def _owned_transaction_file(root: Path, relative: object, suffix: str) -> Path:
    if type(relative) is not str:
        raise PartitionGenerationError("generation transaction path is invalid")
    path = Path(relative)
    if (
        path.is_absolute()
        or len(path.parts) != 2
        or path.parts[0] != _TRANSACTION_DIR
        or not path.name.endswith(suffix)
    ):
        raise PartitionGenerationError("generation transaction path is invalid")
    return root / path


def _target_from_record(root: Path, value: object) -> Path:
    if type(value) is not str:
        raise PartitionGenerationError("generation target is invalid")
    path = Path(value)
    if path.is_absolute() or path.as_posix() != value:
        raise PartitionGenerationError("generation target is invalid")
    target = root / path
    if _relative_target(root, target) != value:
        raise PartitionGenerationError("generation target is invalid")
    return target


def _canonical_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_journal(root: Path, payload: dict[str, object]) -> None:
    temporary = _journal_temporary_path(root)
    journal = _journal_path(root)
    body = _canonical_bytes(payload)
    with temporary.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(journal)


def _replace_journal(root: Path, payload: dict[str, object], phase: str) -> None:
    if phase not in _PHASES:
        raise PartitionGenerationError("generation phase is invalid")
    updated = dict(payload)
    updated["phase"] = phase
    _write_journal(root, updated)


def _cleanup_transaction_directory(root: Path) -> None:
    transaction = _transaction_path(root)
    if not os.path.lexists(transaction):
        return
    if transaction.is_symlink() or not transaction.is_dir():
        raise PartitionGenerationError("generation transaction root is unsafe")
    for path in tuple(transaction.iterdir()):
        if (
            path.is_symlink()
            or not path.is_file()
            or not (
                path.name.startswith("stage-") and path.name.endswith(".parquet.tmp")
                or path.name.startswith("backup-") and path.name.endswith(".parquet.bak")
            )
        ):
            raise PartitionGenerationError("generation transaction contains an unowned entry")
        path.unlink()
    transaction.rmdir()


def _cleanup_generation(root: Path) -> None:
    _cleanup_transaction_directory(root)
    temporary = _journal_temporary_path(root)
    journal = _journal_path(root)
    if os.path.lexists(temporary):
        if not _plain_file(temporary):
            raise PartitionGenerationError("generation journal temporary is unsafe")
        temporary.unlink()
    if os.path.lexists(journal):
        if not _plain_file(journal):
            raise PartitionGenerationError("generation journal is unsafe")
        journal.unlink()


def _validated_journal(root: Path) -> dict[str, object] | None:
    journal = _journal_path(root)
    temporary = _journal_temporary_path(root)
    if not os.path.lexists(journal):
        if os.path.lexists(temporary):
            if not _plain_file(temporary):
                raise PartitionGenerationError("generation journal temporary is unsafe")
            temporary.unlink()
        _cleanup_transaction_directory(root)
        return None
    if not _plain_file(journal):
        raise PartitionGenerationError("generation journal is unsafe")
    try:
        body = journal.read_bytes()
        payload = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PartitionGenerationError("generation journal is unreadable") from error
    if body != _canonical_bytes(payload):
        raise PartitionGenerationError("generation journal is not canonical")
    if (
        type(payload) is not dict
        or set(payload) != {"schema", "owner", "phase", "entries"}
        or payload.get("schema") != _SCHEMA
        or type(payload.get("owner")) is not str
        or not payload.get("owner")
        or payload.get("phase") not in _PHASES
        or type(payload.get("entries")) is not list
        or not payload["entries"]
    ):
        raise PartitionGenerationError("generation journal schema differs")
    if os.path.lexists(temporary):
        if not _plain_file(temporary):
            raise PartitionGenerationError("generation journal temporary is unsafe")
        temporary.unlink()
    return payload


def _validated_entries(root: Path, payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    targets: set[str] = set()
    for value in payload["entries"]:
        if (
            type(value) is not dict
            or set(value) != {
                "target", "stage", "backup", "prior_exists",
                "prior_sha256", "candidate_sha256",
            }
            or type(value.get("prior_exists")) is not bool
            or type(value.get("candidate_sha256")) is not str
            or len(value["candidate_sha256"]) != 64
        ):
            raise PartitionGenerationError("generation entry schema differs")
        target = _target_from_record(root, value["target"])
        stage = _owned_transaction_file(root, value["stage"], ".parquet.tmp")
        prior_exists = value["prior_exists"]
        prior_sha256 = value["prior_sha256"]
        backup_value = value["backup"]
        if prior_exists:
            if type(prior_sha256) is not str or len(prior_sha256) != 64:
                raise PartitionGenerationError("generation prior digest is invalid")
            backup = _owned_transaction_file(root, backup_value, ".parquet.bak")
        elif prior_sha256 is not None or backup_value is not None:
            raise PartitionGenerationError("generation missing-target entry differs")
        else:
            backup = None
        relative = target.relative_to(root).as_posix()
        if relative in targets:
            raise PartitionGenerationError("generation target is duplicated")
        targets.add(relative)
        result.append({
            **value,
            "_target": target,
            "_stage": stage,
            "_backup": backup,
        })
    return tuple(result)


def _recover_locked(root: Path) -> None:
    payload = _validated_journal(root)
    if payload is None:
        return
    entries = _validated_entries(root, payload)
    if payload["phase"] == "COMMITTED":
        for entry in entries:
            target = entry["_target"]
            if not _plain_file(target) or _sha256(target) != entry["candidate_sha256"]:
                raise PartitionGenerationError("committed partition generation changed")
        _cleanup_generation(root)
        return

    for entry in reversed(entries):
        target = entry["_target"]
        if entry["prior_exists"]:
            backup = entry["_backup"]
            if _plain_file(backup):
                if _sha256(backup) != entry["prior_sha256"]:
                    raise PartitionGenerationError("partition backup changed")
                target.parent.mkdir(parents=True, exist_ok=True)
                backup.replace(target)
            elif not _plain_file(target) or _sha256(target) != entry["prior_sha256"]:
                raise PartitionGenerationError("prior partition generation is unavailable")
        elif os.path.lexists(target):
            if not _plain_file(target) or _sha256(target) != entry["candidate_sha256"]:
                raise PartitionGenerationError("new partition target changed during recovery")
            target.unlink()
    for entry in entries:
        target = entry["_target"]
        if entry["prior_exists"]:
            if not _plain_file(target) or _sha256(target) != entry["prior_sha256"]:
                raise PartitionGenerationError("partition rollback readback differs")
        elif os.path.lexists(target):
            raise PartitionGenerationError("new partition remained after rollback")
    _cleanup_generation(root)


@contextmanager
def readable_generation(root: Path) -> Iterator[None]:
    """Hold one dataset generation stable and recover an interrupted writer."""

    root = _root_path(root)
    with _exclusive_generation(root):
        if not os.path.lexists(root):
            yield
            return
        if root.is_symlink() or not root.is_dir():
            raise PartitionGenerationError("partition root is unsafe")
        _recover_locked(root)
        yield


class PartitionGeneration:
    def __init__(self, root: Path, owner: str) -> None:
        self.root = root
        self.owner = owner
        self._staged: dict[Path, Path] = {}
        self._published = False

    def stage_path(self, target: Path) -> Path:
        relative = _relative_target(self.root, target)
        absolute = self.root / relative
        if absolute in self._staged:
            raise PartitionGenerationError("partition target is staged twice")
        transaction = _transaction_path(self.root)
        path = transaction / f"stage-{len(self._staged):04d}.parquet.tmp"
        self._staged[absolute] = path
        return path

    def publish(self) -> None:
        if self._published or not self._staged:
            raise PartitionGenerationError("partition generation has no staged files")
        entries: list[dict[str, object]] = []
        for index, (target, stage) in enumerate(self._staged.items()):
            if not _plain_file(stage):
                raise PartitionGenerationError("staged partition is unavailable")
            target.parent.mkdir(parents=True, exist_ok=True)
            prior_exists = os.path.lexists(target)
            if prior_exists and not _plain_file(target):
                raise PartitionGenerationError("partition target is unsafe")
            backup = (
                _transaction_path(self.root) / f"backup-{index:04d}.parquet.bak"
                if prior_exists else None
            )
            prior_sha256 = _sha256(target) if prior_exists else None
            if backup is not None:
                shutil.copy2(target, backup)
                if _sha256(backup) != prior_sha256:
                    raise PartitionGenerationError("partition backup readback differs")
            entries.append({
                "target": target.relative_to(self.root).as_posix(),
                "stage": stage.relative_to(self.root).as_posix(),
                "backup": backup.relative_to(self.root).as_posix() if backup else None,
                "prior_exists": prior_exists,
                "prior_sha256": prior_sha256,
                "candidate_sha256": _sha256(stage),
            })
        payload: dict[str, object] = {
            "schema": _SCHEMA,
            "owner": self.owner,
            "phase": "PREPARED",
            "entries": entries,
        }
        _write_journal(self.root, payload)
        _replace_journal(self.root, payload, "PROMOTING")
        try:
            for entry in entries:
                stage = self.root / Path(str(entry["stage"]))
                target = self.root / Path(str(entry["target"]))
                stage.replace(target)
            for entry in entries:
                target = self.root / Path(str(entry["target"]))
                if not _plain_file(target) or _sha256(target) != entry["candidate_sha256"]:
                    raise PartitionGenerationError("promoted partition readback differs")
            _replace_journal(self.root, payload, "COMMITTED")
            _cleanup_generation(self.root)
            self._published = True
        except Exception:
            _recover_locked(self.root)
            raise

    def discard_unprepared(self) -> None:
        if not self._published and not os.path.lexists(_journal_path(self.root)):
            _cleanup_generation(self.root)


@contextmanager
def writable_generation(root: Path, owner: str) -> Iterator[PartitionGeneration]:
    """Stage and publish one crash-recoverable partition generation."""

    root = _root_path(root)
    if type(owner) is not str or not owner:
        raise ValueError("partition generation owner is required")
    with _exclusive_generation(root):
        if os.path.lexists(root) and (root.is_symlink() or not root.is_dir()):
            raise PartitionGenerationError("partition root is unsafe")
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise PartitionGenerationError("partition root is unsafe")
        _recover_locked(root)
        transaction = _transaction_path(root)
        if os.path.lexists(transaction):
            raise PartitionGenerationError("partition transaction root already exists")
        transaction.mkdir()
        generation = PartitionGeneration(root, owner)
        try:
            yield generation
        finally:
            generation.discard_unprepared()


__all__ = [
    "PartitionGeneration",
    "PartitionGenerationError",
    "readable_generation",
    "writable_generation",
]
