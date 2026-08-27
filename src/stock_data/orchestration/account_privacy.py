"""Local-only privacy controls for retained read-only account snapshots.

This module deliberately does not handle credentials or provider sessions.  It
only masks identifiers in presentation/diagnostics and removes the exact local
account projection paths owned by the accepted account boundary.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
from uuid import uuid4


MASKED_VALUE = "••••"
_PRIVACY_GENERATION_PATH = "data/state/toss_account_snapshot_privacy.json"
_PRIVACY_GENERATION_SCHEMA_VERSION = 1
_LIFECYCLE_GUARDS_LOCK = threading.Lock()
_LIFECYCLE_GUARDS: dict[str, threading.RLock] = {}
_LIFECYCLE_DEPTH = threading.local()
_ACCOUNT_STRUCTURED_VALUE = re.compile(
    r"(?i)(?<![\w])(?P<key_open>[\"']?)(?P<key>"
    r"account(?:[_ -]?(?:id|no|number|seq))?|계좌(?:번호)?|"
    r"balance|cash|holdings?|positions?|valuation|profit[_ -]?loss|"
    r"pnl|잔액|예수금|보유(?:종목)?|평가(?:금액|손익)?)"
    r"(?P<key_close>[\"']?)\s*(?::\s*=|:(?!\s*=)|=)\s*"
    r"(?P<value>"
    r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|"
    r"\[[^\]\r\n]*\]|\{[^}\r\n]*\}|[^\s,;&}\]]+)"
)
_ACCOUNT_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){9,13}\d(?!\d)")
_ACCOUNT_BUYING_POWER_VALUE = re.compile(
    r"(?i)(?<![\w])(?P<key_open>[\"']?)(?P<key>"
    r"(?:(?:cash[_ -]?)?buying[_ -]?power)(?:[_ -]?amount)?|"
    r"order[_ -]?available(?:[_ -]?amount)?|"
    r"현금[ _-]*매수가능(?:금액)?|매수가능(?:금액)?|주문가능(?:금액)?)"
    r"(?P<key_close>[\"']?)\s*(?::\s*=|:(?!\s*=)|=)\s*"
    r"(?P<value_open>[\"']?)"
    r"(?:(?:KRW|USD|[$₩])\s*)?[+-]?"
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?:[eE][+-]?\d+)?(?:\s*(?:KRW|USD|원|달러))?"
    r"(?P<value_close>[\"']?)(?!\w|[.,](?=\d))"
)
_ACCOUNT_BUYING_POWER_TOKEN_VALUE = re.compile(
    r"(?i)(?<![\w])(?P<key_open>[\"']?)(?P<key>"
    r"(?:(?:cash[_ -]?)?buying[_ -]?power)(?:[_ -]?amount)?|"
    r"order[_ -]?available(?:[_ -]?amount)?|"
    r"현금[ _-]*매수가능(?:금액)?|매수가능(?:금액)?|주문가능(?:금액)?)"
    r"(?P<key_close>[\"']?)\s*(?::\s*=|:(?!\s*=)|=)\s*"
    r"(?![\"']?\[REDACTED_ACCOUNT_VALUE\])"
    r"(?![\"']?(?:(?:KRW|USD|[$₩])\s*)?[+-]?(?:\d|\.\d))"
    r"[\"']?[^\s,;&}\]]+"
)
class AccountSnapshotRemovalError(RuntimeError):
    """Safe, value-free failure raised by the exact-scope removal control."""


@dataclass(frozen=True, slots=True)
class AccountSnapshotRemovalResult:
    removed_files: int
    status: str = "REMOVED"


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(
                (json.dumps(
                    value, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                ) + "\n").encode("utf-8")
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def account_snapshot_privacy_generation(project_root: Path) -> int:
    """Return the fail-closed generation invalidating removed refreshers."""

    root = Path(project_root).resolve()
    path = root / _PRIVACY_GENERATION_PATH
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AccountSnapshotRemovalError(
            "ACCOUNT_SNAPSHOT_PRIVACY_STATE_INVALID"
        ) from None
    generation = payload.get("generation") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "generation"}
        or payload.get("schema_version") != _PRIVACY_GENERATION_SCHEMA_VERSION
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
    ):
        raise AccountSnapshotRemovalError("ACCOUNT_SNAPSHOT_PRIVACY_STATE_INVALID")
    return generation


def _advance_account_snapshot_privacy_generation(root: Path) -> int:
    generation = account_snapshot_privacy_generation(root) + 1
    _atomic_json(root / _PRIVACY_GENERATION_PATH, {
        "schema_version": _PRIVACY_GENERATION_SCHEMA_VERSION,
        "generation": generation,
    })
    return generation


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
def account_snapshot_lifecycle_lock(
    project_root: Path, *, timeout_seconds: float = 10.0,
):
    """Exclude every account refresh from local privacy removal.

    The lock spans provider/supplier work as well as persistence so a refresh
    that was already in flight cannot repopulate data after removal returns.
    """

    root = Path(project_root).resolve()
    root_key = str(root)
    with _LIFECYCLE_GUARDS_LOCK:
        guard = _LIFECYCLE_GUARDS.setdefault(root_key, threading.RLock())
    if not guard.acquire(timeout=max(0.0, timeout_seconds)):
        raise TimeoutError("account snapshot lifecycle lock timeout")
    depths = getattr(_LIFECYCLE_DEPTH, "depths", None)
    if depths is None:
        depths = {}
        _LIFECYCLE_DEPTH.depths = depths
    depth = depths.get(root_key, 0)
    depths[root_key] = depth + 1
    if depth:
        try:
            yield
        finally:
            depths[root_key] -= 1
            guard.release()
        return
    lock_path = root / "data/state/transactions/.account_snapshot_lifecycle.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    try:
        with lock_path.open("a+b") as stream:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
            while True:
                try:
                    _lock_stream(stream)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("account snapshot lifecycle lock timeout") from None
                    time.sleep(0.01)
            try:
                yield
            finally:
                _unlock_stream(stream)
    finally:
        depths[root_key] -= 1
        guard.release()


def mask_account_identifier(value: object) -> str:
    """Return a consistent last-four-only account identifier for local UI use."""

    compact = "".join(character for character in str(value) if character.isalnum())
    if not compact:
        return MASKED_VALUE
    if len(compact) < 7:
        return MASKED_VALUE
    return f"{MASKED_VALUE}-{compact[-4:]}"


def redact_account_text(value: object, *, limit: int = 500) -> str:
    """Remove account identifiers and account values from bounded diagnostics."""

    text = str(value).replace("\r", " ").replace("\n", " ")
    def _redact_structured(match: re.Match[str]) -> str:
        key = match.group("key")
        normalized_key = re.sub(r"[_ -]", "", key).casefold()
        marker = (
            "[REDACTED_ACCOUNT]"
            if normalized_key.startswith("account") or normalized_key.startswith("계좌")
            else "[REDACTED_ACCOUNT_VALUE]"
        )
        return (
            f"{match.group('key_open')}{key}{match.group('key_close')}={marker}"
        )

    text = _ACCOUNT_STRUCTURED_VALUE.sub(_redact_structured, text)
    text = _ACCOUNT_BUYING_POWER_VALUE.sub(
        lambda match: (
            f"{match.group('key_open')}{match.group('key')}"
            f"{match.group('key_close')}=[REDACTED_ACCOUNT_VALUE]"
        ),
        text,
    )
    text = _ACCOUNT_BUYING_POWER_TOKEN_VALUE.sub(
        lambda match: (
            f"{match.group('key_open')}{match.group('key')}"
            f"{match.group('key_close')}=[REDACTED_ACCOUNT_VALUE]"
        ),
        text,
    )
    text = _ACCOUNT_NUMBER.sub("[REDACTED_ACCOUNT]", text)
    return text[:limit]


def prune_account_landing(project_root: Path, *, keep: int = 1) -> int:
    """Keep only the newest sanitized Toss Landing projections.

    Cleanup is deliberately limited to direct ``*.json`` children of the exact
    account Landing directory.  It never traverses another dataset.
    """

    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError("keep must be a positive integer")
    root = project_root.resolve()
    landing_root = (root / "data/landing/tossinvest/account_snapshot").resolve()
    landing_root.relative_to(root)
    files = sorted(
        (path for path in landing_root.glob("*.json") if path.is_file()),
        key=lambda path: path.name,
    )
    removed = 0
    for path in files[:-keep]:
        path.resolve().relative_to(landing_root)
        path.unlink()
        removed += 1
    return removed


def _remove_retained_account_snapshots_unlocked(
    project_root: Path,
) -> AccountSnapshotRemovalResult:
    """Remove only accepted local account projections, never credentials/data.

    Incomplete account promotions block removal so the operation cannot race a
    commit.  The allowlist is intentionally literal; no broad directory delete
    or recursive glob is used.
    """

    try:
        root = project_root.resolve()
    except (OSError, RuntimeError):
        raise AccountSnapshotRemovalError(
            "ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED"
        ) from None
    if not root.is_dir():
        raise AccountSnapshotRemovalError(
            "ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED"
        )

    def reject_scope() -> None:
        raise AccountSnapshotRemovalError(
            "ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED"
        )

    def reject_link_or_junction(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_junction():
                reject_scope()
        except OSError:
            reject_scope()

    def reject_non_directory_ancestors(path: Path) -> None:
        for parent in path.parents:
            if parent == root:
                break
            if parent.exists() and not parent.is_dir():
                reject_scope()

    def resolve_owned_root(path: Path) -> Path:
        reject_non_directory_ancestors(path)
        reject_link_or_junction(path)
        if path.exists() and not path.is_dir():
            reject_scope()
        try:
            lexical = path.absolute()
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            reject_scope()
        if resolved != lexical:
            reject_scope()
        return resolved

    def resolve_under_project(path: Path) -> Path:
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            reject_scope()
        return resolved

    def resolve_exact_target(path: Path) -> Path:
        reject_non_directory_ancestors(path)
        reject_link_or_junction(path)
        if path.exists() and not path.is_file():
            reject_scope()
        resolved = resolve_under_project(path)
        if resolved != path.absolute():
            reject_scope()
        return resolved

    def resolve_under_boundary(path: Path, boundary: Path) -> Path:
        resolved = resolve_under_project(path)
        if resolved != boundary and boundary not in resolved.parents:
            reject_scope()
        return resolved

    journal_root = resolve_owned_root(
        root / "data/state/transactions/toss_account_snapshot"
    )
    kb_journal_root = resolve_owned_root(
        root / "data/state/transactions/kbsec_account_snapshot"
    )
    landing_root = resolve_owned_root(
        root / "data/landing/tossinvest/account_snapshot"
    )
    kb_landing_root = resolve_owned_root(
        root / "data/landing/kbsec/account_snapshot"
    )
    local_root = resolve_owned_root(root / "data/local/account_snapshots")
    history_root = resolve_owned_root(root / "data/local/account_value_history")
    history_source_roots = tuple(
        resolve_owned_root(history_root / source_id)
        for source_id in ("toss_self", "kb_self")
    )
    staging_root = resolve_owned_root(
        root / "data/staging/toss_account_snapshot"
    )
    kb_staging_root = resolve_owned_root(
        root / "data/staging/kbsec_account_snapshot"
    )
    history_staging_root = resolve_owned_root(
        root / "data/staging/account_value_history"
    )
    fixed = (
        root / "data/normalized/toss_account_snapshot/latest.json",
        root / "data/state/toss_account_snapshot.json",
        root / "data/state/kbsec_account_snapshot.json",
        local_root / "kb_self.json",
        local_root / "family_mirae_etf.json",
    )
    fixed_resolved = tuple(resolve_exact_target(path) for path in fixed)

    try:
        journal_specs = [
            *((path, journal_root) for path in sorted(journal_root.glob("*.json"))),
            *((path, kb_journal_root) for path in sorted(kb_journal_root.glob("*.json"))),
        ]
        for journal, boundary in journal_specs:
            resolve_under_boundary(journal, boundary)
            payload = json.loads(journal.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("journal must be an object")
            if payload.get("status") in {"PREPARED", "PROMOTING"}:
                raise AccountSnapshotRemovalError("ACCOUNT_SNAPSHOT_OPERATION_BUSY")
    except AccountSnapshotRemovalError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise AccountSnapshotRemovalError("ACCOUNT_SNAPSHOT_REMOVAL_PREFLIGHT_FAILED") from None

    def preflight_staging(
        boundary: Path,
    ) -> tuple[list[tuple[Path, Path]], list[tuple[Path, Path]], list[tuple[Path, Path]]]:
        staged_files: list[tuple[Path, Path]] = []
        leaf_directories: list[tuple[Path, Path]] = []
        run_directories: list[tuple[Path, Path]] = []
        if not boundary.exists():
            return staged_files, leaf_directories, run_directories
        try:
            runs = sorted(boundary.iterdir(), key=lambda path: path.name)
            for run in runs:
                reject_link_or_junction(run)
                resolve_under_boundary(run, boundary)
                if not run.is_dir():
                    reject_scope()
                run_directories.append((run, boundary))
                leaves = sorted(run.iterdir(), key=lambda path: path.name)
                for leaf in leaves:
                    if leaf.name not in {"candidate", "backup"}:
                        reject_scope()
                    reject_link_or_junction(leaf)
                    resolve_under_boundary(leaf, boundary)
                    if not leaf.is_dir():
                        reject_scope()
                    leaf_directories.append((leaf, boundary))
                    for staged_file in sorted(
                        leaf.iterdir(), key=lambda path: path.name,
                    ):
                        reject_link_or_junction(staged_file)
                        resolve_under_boundary(staged_file, boundary)
                        if (
                            staged_file.suffix != ".json"
                            or not staged_file.is_file()
                        ):
                            reject_scope()
                        staged_files.append((staged_file, boundary))
        except AccountSnapshotRemovalError:
            raise
        except OSError:
            reject_scope()
        leaf_directories.sort(key=lambda item: len(item[0].parts), reverse=True)
        return staged_files, leaf_directories, run_directories

    (
        staged_files,
        staged_leaf_directories,
        staged_run_directories,
    ) = preflight_staging(staging_root)
    (
        kb_staged_files,
        kb_staged_leaf_directories,
        kb_staged_run_directories,
    ) = preflight_staging(kb_staging_root)
    history_staged_files: list[tuple[Path, Path]] = []
    history_staged_run_directories: list[tuple[Path, Path]] = []
    try:
        history_runs = (
            sorted(history_staging_root.iterdir(), key=lambda path: path.name)
            if history_staging_root.exists() else []
        )
        for run in history_runs:
            reject_link_or_junction(run)
            resolve_under_boundary(run, history_staging_root)
            if not run.is_dir():
                reject_scope()
            history_staged_run_directories.append((run, history_staging_root))
            for staged_file in sorted(run.iterdir(), key=lambda path: path.name):
                reject_link_or_junction(staged_file)
                resolve_under_boundary(staged_file, history_staging_root)
                if staged_file.name != "observation.json" or not staged_file.is_file():
                    reject_scope()
                history_staged_files.append((staged_file, history_staging_root))
    except AccountSnapshotRemovalError:
        raise
    except OSError:
        reject_scope()
    landing_specs = [
        *((path, landing_root) for path in sorted(landing_root.glob("*.json"))),
        *((path, kb_landing_root) for path in sorted(kb_landing_root.glob("*.json"))),
    ]
    history_files = sorted(
        path
        for source_root in history_source_roots
        for path in source_root.glob("*.json")
    )
    candidate_specs = [
        *((path, None, expected) for path, expected in zip(fixed, fixed_resolved, strict=True)),
        *((path, boundary, None) for path, boundary in landing_specs),
        *((path, boundary, None) for path, boundary in journal_specs),
        *((path, boundary, None) for path, boundary in (
            *staged_files, *kb_staged_files, *history_staged_files,
        )),
        *((path, path.parent.resolve(), None) for path in history_files),
    ]

    def validate_candidate(
        path: Path, boundary: Path | None, expected: Path | None,
    ) -> None:
        reject_link_or_junction(path)
        resolved = (
            resolve_under_project(path)
            if boundary is None
            else resolve_under_boundary(path, boundary)
        )
        if expected is not None and resolved != expected:
            reject_scope()
        if path.exists() and not path.is_file():
            reject_scope()

    def validate_candidates() -> None:
        for path, boundary, expected in candidate_specs:
            validate_candidate(path, boundary, expected)

    validate_candidates()
    staged_directory_specs = (
        *staged_leaf_directories, *kb_staged_leaf_directories,
        *staged_run_directories, *kb_staged_run_directories,
        *history_staged_run_directories,
    )
    for directory, boundary in staged_directory_specs:
        reject_link_or_junction(directory)
        resolve_under_boundary(directory, boundary)
        if not directory.is_dir():
            reject_scope()

    try:
        _advance_account_snapshot_privacy_generation(root)
    except (AccountSnapshotRemovalError, OSError, TypeError, ValueError):
        raise AccountSnapshotRemovalError(
            "ACCOUNT_SNAPSHOT_REMOVAL_PREFLIGHT_FAILED"
        ) from None

    removed = 0
    try:
        for path, boundary, expected in candidate_specs:
            validate_candidate(path, boundary, expected)
            if path.exists():
                path.unlink()
                removed += 1
        for directory, boundary in (
            *staged_leaf_directories, *kb_staged_leaf_directories,
        ):
            reject_link_or_junction(directory)
            resolve_under_boundary(directory, boundary)
            if not directory.is_dir():
                reject_scope()
            directory.rmdir()
        for directory, boundary in (
            *staged_run_directories, *kb_staged_run_directories,
            *history_staged_run_directories,
        ):
            reject_link_or_junction(directory)
            resolve_under_boundary(directory, boundary)
            if not directory.is_dir():
                reject_scope()
            directory.rmdir()
    except OSError:
        raise AccountSnapshotRemovalError("ACCOUNT_SNAPSHOT_REMOVAL_INCOMPLETE") from None
    return AccountSnapshotRemovalResult(removed_files=removed)


def remove_retained_account_snapshots(project_root: Path) -> AccountSnapshotRemovalResult:
    """Remove retained projections after excluding all account refreshers."""

    try:
        root = Path(project_root).resolve()
    except (OSError, RuntimeError):
        raise AccountSnapshotRemovalError(
            "ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED"
        ) from None
    if not root.is_dir():
        raise AccountSnapshotRemovalError(
            "ACCOUNT_SNAPSHOT_REMOVAL_SCOPE_REJECTED"
        )
    try:
        with account_snapshot_lifecycle_lock(root):
            return _remove_retained_account_snapshots_unlocked(root)
    except TimeoutError:
        raise AccountSnapshotRemovalError("ACCOUNT_SNAPSHOT_OPERATION_BUSY") from None


__all__ = [
    "AccountSnapshotRemovalError",
    "AccountSnapshotRemovalResult",
    "account_snapshot_privacy_generation",
    "account_snapshot_lifecycle_lock",
    "MASKED_VALUE",
    "mask_account_identifier",
    "prune_account_landing",
    "redact_account_text",
    "remove_retained_account_snapshots",
]
