from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Iterable, Sequence


STATE_PARTS = {
    "new": ("inbox", "new"),
    "waiting": ("waiting",),
    "ready": ("inbox", "ready"),
    "active": ("active",),
    "review": ("review",),
    "blocked": ("blocked",),
    "done": ("done",),
}
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[0-9A-F]{4}$")
TASK_NAME = re.compile(
    r"^(P[012])-(RQ-\d{8}T\d{6}-[0-9A-F]{4})-([^\\/\s]+)$"
)
REQUIRED_TASK_FILES = ("META.json", "TASK.md", "HANDOFF.md")
META_KEYS = (
    "schema_version", "id", "title", "slug", "legacy_id", "priority",
    "priority_hint", "kind", "risk", "state", "owner", "assigned_role",
    "assigned_agent", "reviewer", "created_by", "created_at", "updated_at",
    "completed_at", "parent_task", "depends_on", "fingerprint",
    "write_scope", "parallelizable", "review_required", "lease_until",
    "heartbeat", "worktree", "branch",
)
HANDOFF_KEYS = (
    "updated_at", "phase", "summary", "completed", "next", "files_touched",
    "tests", "risks", "new_discoveries",
)
WRITER_LIMIT = 3
LEAD_WIP_LIMIT = 3
WRITER_LANES = ("gui", "data", "backtest", "shared")
DOMAINS = ("data", "backtest", "gui", "infra", "broker", "research", "integration", "shared")
WAITING_KEYS = ("reason", "resume_condition", "next_check_at", "waiting_since")
ORCA_STATE_NAME = "ORCA_STATE.json"
ORCA_STATE_SCHEMA = 1
ORCA_STATE_KEYS = (
    "schema_version", "queue_task_id", "run_id", "task_id", "dispatch_id",
    "attempt", "phase", "waiting_for", "next_action", "candidate_commit",
    "diff_digest", "review_generation", "observed_dispatch_status",
    "last_transition_at", "last_reconciled_at", "last_error",
)
ORCA_PHASES = (
    "BOUND", "DISPATCHED", "WAITING_FOR_WORKER_DONE", "RECOVERY_REQUIRED",
    "SUCCEEDED", "REVIEWING",
)
ORCA_OBSERVED_STATUSES = ("pending", "running", "completed", "failed", "cancelled", "blocked")
ORCA_OBSERVATION_RANK = {
    "pending": 0,
    "running": 1,
    "completed": 2,
    "failed": 2,
    "cancelled": 2,
    "blocked": 2,
}
ORCA_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "blocked"}
COMPLETED_INDEX_NAME = "COMPLETED_INDEX.json"
COMPLETED_INDEX_SCHEMA = 1
COMPLETED_ENTRY_KEYS = (
    "id", "legacy_id", "fingerprint", "completed_at", "directory",
    "result_summary", "receipt_sha256",
)


class QueueError(RuntimeError):
    pass


def _claim_token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _authorize_active_claim(
    args: argparse.Namespace, root: Path, task: Path, meta: dict[str, object],
) -> None:
    if meta.get("owner") != args.owner:
        raise QueueError(f"active owner differs: {meta.get('owner')}")
    if meta.get("assigned_role") == "lead":
        if meta.get("lead_owner") != args.owner:
            raise QueueError("Active Lead routing differs from owner")
        generation = getattr(args, "expected_generation", None)
        if generation is None:
            raise QueueError("Active Lead mutation requires --expected-generation")
        if not isinstance(generation, str) or not secrets.compare_digest(
            generation, _queue_generation(task)
        ):
            raise QueueError("Active Lead Queue generation differs")
        return
    token = getattr(args, "claim_token", None)
    expected = meta.get("claim_token_sha256")
    if expected is None:
        if not getattr(args, "adopt_legacy_claim", False):
            raise QueueError(
                "legacy active task has no claim token; use one atomic "
                "--adopt-legacy-claim with --claim-token"
            )
        if not isinstance(token, str) or len(token) < 32:
            raise QueueError("legacy claim adoption requires a strong claim token")
        # Keep adoption in memory until the authorized command performs its
        # own atomic META write.  Persisting here would let a later validation
        # failure (for example an invalid lease) change claimant state even
        # though the requested mutation was rejected.
        meta["claim_token_sha256"] = _claim_token_digest(token)
        meta["updated_at"] = _stamp()
        return
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise QueueError("active claim token digest is invalid")
    if not isinstance(token, str) or not secrets.compare_digest(
        expected, _claim_token_digest(token)
    ):
        raise QueueError("active claim token differs")


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _git(start: Path, *args: str) -> str:
    completed = subprocess.run(
        (
            "git", "-c", f"safe.directory={start.resolve().as_posix()}",
            "-C", str(start), *args,
        ),
        check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    return completed.stdout.strip()


def resolve_queue_root(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).resolve()
    configured = os.environ.get("REQUEST_QUEUE_ROOT")
    if configured:
        return Path(configured).resolve()
    start = Path.cwd().resolve()
    try:
        top = Path(_git(start, "rev-parse", "--show-toplevel")).resolve()
        common_text = _git(top, "rev-parse", "--git-common-dir")
        common = Path(common_text)
        if not common.is_absolute():
            common = (top / common).resolve()
        main_worktree = common.parent if common.name == ".git" else top
        return main_worktree / "artifacts" / "request_queue"
    except (OSError, subprocess.CalledProcessError):
        for candidate in (start, *start.parents):
            marker = candidate / ".git"
            if marker.is_dir():
                return candidate / "artifacts" / "request_queue"
            if marker.is_file():
                try:
                    label, value = marker.read_text(encoding="utf-8").strip().split(":", 1)
                    if label.strip().lower() != "gitdir":
                        continue
                    git_dir = Path(value.strip())
                    if not git_dir.is_absolute():
                        git_dir = (candidate / git_dir).resolve()
                    common_file = git_dir / "commondir"
                    common = (
                        (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
                        if common_file.is_file() else git_dir
                    )
                    main_worktree = common.parent if common.name == ".git" else candidate
                    return main_worktree / "artifacts" / "request_queue"
                except (OSError, ValueError):
                    continue
        for candidate in (start, *start.parents):
            if (candidate / "AGENTS.md").is_file():
                return candidate / "artifacts" / "request_queue"
        raise QueueError(f"cannot resolve the central request queue from {start}")


def _ensure_canonical_manager(root: Path, current: Path | None = None) -> None:
    canonical = (root.parent.parent / "scripts" / "request_queue.py").resolve()
    if not canonical.is_file():
        return
    running = (current or Path(__file__)).resolve()
    if running != canonical:
        raise QueueError(
            f"linked-worktree queue manager is forbidden; run canonical manager: {canonical}"
        )


def _linked_worktree_managers(root: Path) -> list[Path]:
    repository = root.parent.parent.resolve()
    canonical = (repository / "scripts" / "request_queue.py").resolve()
    if not canonical.is_file():
        return []
    try:
        output = _git(repository, "worktree", "list", "--porcelain")
    except (OSError, subprocess.CalledProcessError) as error:
        raise QueueError(f"cannot verify linked worktree managers: {error}") from error
    managers: list[Path] = []
    for line in output.splitlines():
        if not line.startswith("worktree "):
            continue
        candidate = (Path(line.removeprefix("worktree ")) / "scripts" / "request_queue.py")
        if candidate.is_file() and candidate.resolve() != canonical:
            managers.append(candidate.resolve())
    return managers


def state_path(root: Path, state: str) -> Path:
    path = root
    for part in STATE_PARTS[state]:
        path /= part
    return path


def ensure_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for state in STATE_PARTS:
        state_path(root, state).mkdir(parents=True, exist_ok=True)
    (root / "templates").mkdir(parents=True, exist_ok=True)


@contextmanager
def mutation_lock(root: Path) -> Iterable[None]:
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".queue-mutation.lock"
    deadline = time.monotonic() + 5.0
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError as error:
            if time.monotonic() >= deadline:
                raise QueueError(f"queue mutation already in progress: {path}") from error
            time.sleep(0.01)
    try:
        payload = f"pid={os.getpid()} acquired_at={_stamp()}\n".encode("utf-8")
        os.write(descriptor, payload)
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    _atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _completed_entries_digest(entries: list[dict[str, object]]) -> str:
    body = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_completed_index(root: Path) -> list[dict[str, object]]:
    path = root / COMPLETED_INDEX_NAME
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QueueError(f"completed index is unreadable: {error}") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "entries", "entries_sha256",
    }:
        raise QueueError("completed index schema differs")
    entries = payload.get("entries")
    if payload.get("schema_version") != COMPLETED_INDEX_SCHEMA or not isinstance(entries, list):
        raise QueueError("completed index schema differs")
    if payload.get("entries_sha256") != _completed_entries_digest(entries):
        raise QueueError("completed index digest mismatch")
    seen = {key: set() for key in ("id", "legacy_id", "fingerprint", "directory")}
    normalized: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != set(COMPLETED_ENTRY_KEYS):
            raise QueueError("completed index entry schema differs")
        task_id = entry.get("id")
        legacy_id = entry.get("legacy_id")
        fingerprint = entry.get("fingerprint")
        directory = entry.get("directory")
        summary = entry.get("result_summary")
        receipt = entry.get("receipt_sha256")
        if not isinstance(task_id, str) or TASK_ID.fullmatch(task_id) is None:
            raise QueueError("completed index task id is invalid")
        if legacy_id is not None and (not isinstance(legacy_id, str) or not legacy_id):
            raise QueueError("completed index legacy id is invalid")
        match = TASK_NAME.fullmatch(str(directory))
        if match is None or match.group(2) != task_id:
            raise QueueError("completed index directory is invalid")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise QueueError("completed index fingerprint is invalid")
        if _aware(entry.get("completed_at")) is None:
            raise QueueError("completed index completion timestamp is invalid")
        if not isinstance(summary, str) or not summary or len(summary) > 512:
            raise QueueError("completed index result summary is invalid")
        if not isinstance(receipt, str) or re.fullmatch(r"[0-9a-f]{64}", receipt) is None:
            raise QueueError("completed index receipt digest is invalid")
        for key, value in (("id", task_id), ("legacy_id", legacy_id),
                           ("fingerprint", fingerprint), ("directory", directory)):
            if value is None:
                continue
            if value in seen[key]:
                raise QueueError(f"duplicate completed index {key}: {value}")
            seen[key].add(value)
        normalized.append(entry)
    if normalized != sorted(normalized, key=lambda item: (str(item["completed_at"]), str(item["id"]))):
        raise QueueError("completed index entries are not canonically ordered")
    return normalized


def _write_completed_index(root: Path, entries: list[dict[str, object]]) -> None:
    ordered = sorted(entries, key=lambda item: (str(item["completed_at"]), str(item["id"])))
    _atomic_json(root / COMPLETED_INDEX_NAME, {
        "schema_version": COMPLETED_INDEX_SCHEMA,
        "entries": ordered,
        "entries_sha256": _completed_entries_digest(ordered),
    })


def _queue_text_bytes(path: Path) -> bytes:
    """Return receipt bytes normalized across Git LF/CRLF checkouts."""

    return path.read_bytes().replace(b"\r\n", b"\n")


def _queue_generation(task: Path) -> str:
    digest = hashlib.sha256()
    for name in ("META.json", "HANDOFF.md", ORCA_STATE_NAME):
        path = task / name
        if path.is_file():
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(_queue_text_bytes(path))
    return digest.hexdigest()


def _task_receipt_digest(task: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(task.rglob("*"), key=lambda item: item.relative_to(task).as_posix()):
        if path.is_symlink() or not path.is_file():
            raise QueueError(f"completed task contains a non-file entry: {path}")
        digest.update(path.relative_to(task).as_posix().encode("utf-8") + b"\0")
        digest.update(_queue_text_bytes(path))
    return digest.hexdigest()


def _load_meta(task: Path) -> dict[str, object]:
    try:
        value = json.loads((task / "META.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
        raise QueueError(f"invalid META.json: {task}: {error}") from error
    if not isinstance(value, dict):
        raise QueueError(f"META.json must contain an object: {task}")
    return value


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


def _validated_lead_owner(value: object) -> str | None:
    if value is None:
        return None
    owner = _clean(value)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}", owner) is None:
        raise QueueError("Lead owner is invalid")
    return owner


def _handoff_text(values: dict[str, object]) -> str:
    return "\n".join(f"{key}: {_clean(values.get(key))}" for key in HANDOFF_KEYS) + "\n"


def _read_handoff(task: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = (task / "HANDOFF.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key in HANDOFF_KEYS:
            result[key] = value.strip()
    return result


def _waiting_text(values: dict[str, object]) -> str:
    return "\n".join(f"{key}: {_clean(values.get(key))}" for key in WAITING_KEYS) + "\n"


def _read_waiting(task: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = (task / "WAITING.md").read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key in WAITING_KEYS:
            result[key] = value.strip()
    return result


def _validate_orca_identifier(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str) or not value or len(value) > 160
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value) is None
    ):
        raise QueueError(f"Orca {label} is invalid")
    return value


def _load_orca_state(task: Path, *, required: bool = False) -> dict[str, object] | None:
    path = task / ORCA_STATE_NAME
    if not path.exists():
        if required:
            raise QueueError("Orca state is not bound")
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QueueError(f"Orca state is unreadable: {error}") from error
    if not isinstance(value, dict) or set(value) != set(ORCA_STATE_KEYS):
        raise QueueError("Orca state schema differs")
    if value.get("schema_version") != ORCA_STATE_SCHEMA:
        raise QueueError("Orca state schema version differs")
    queue_task_id = value.get("queue_task_id")
    if not isinstance(queue_task_id, str) or TASK_ID.fullmatch(queue_task_id) is None:
        raise QueueError("Orca queue task id is invalid")
    meta = _load_meta(task)
    if queue_task_id != meta.get("id"):
        raise QueueError("Orca queue task id differs")
    _validate_orca_identifier(value.get("run_id"), label="run id")
    _validate_orca_identifier(value.get("task_id"), label="task id")
    dispatch_id = value.get("dispatch_id")
    if dispatch_id is not None:
        _validate_orca_identifier(dispatch_id, label="dispatch id")
    attempt = value.get("attempt")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
        raise QueueError("Orca attempt is invalid")
    phase = value.get("phase")
    if phase not in ORCA_PHASES:
        raise QueueError("Orca phase is invalid")
    if phase == "BOUND":
        if dispatch_id is not None or attempt != 0:
            raise QueueError("bound Orca state cannot carry a dispatch")
    elif dispatch_id is None or attempt < 1:
        raise QueueError("dispatched Orca state has no attempt identity")
    for key in ("waiting_for", "next_action"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip() or len(item) > 240:
            raise QueueError(f"Orca {key} is invalid")
    candidate = value.get("candidate_commit")
    if candidate is not None and (
        not isinstance(candidate, str) or re.fullmatch(r"[0-9a-fA-F]{7,64}", candidate) is None
    ):
        raise QueueError("Orca candidate commit is invalid")
    diff_digest = value.get("diff_digest")
    if diff_digest is not None and (
        not isinstance(diff_digest, str) or re.fullmatch(r"[0-9a-f]{64}", diff_digest) is None
    ):
        raise QueueError("Orca diff digest is invalid")
    if phase in {"SUCCEEDED", "REVIEWING"} and (candidate is None or diff_digest is None):
        raise QueueError("successful Orca state lacks candidate binding")
    review_generation = value.get("review_generation")
    if review_generation is not None and (
        not isinstance(review_generation, str)
        or re.fullmatch(r"[0-9a-f]{32}", review_generation) is None
    ):
        raise QueueError("Orca review generation is invalid")
    if phase == "REVIEWING" and review_generation is None:
        raise QueueError("reviewing Orca state lacks review generation")
    if phase != "REVIEWING" and review_generation is not None:
        raise QueueError("non-reviewing Orca state carries a review generation")
    observed = value.get("observed_dispatch_status")
    if observed is not None and observed not in ORCA_OBSERVED_STATUSES:
        raise QueueError("Orca observed dispatch status is invalid")
    for key in ("last_transition_at", "last_reconciled_at"):
        timestamp = value.get(key)
        if timestamp is not None and _aware(timestamp) is None:
            raise QueueError(f"Orca {key} is invalid")
    if _aware(value.get("last_transition_at")) is None:
        raise QueueError("Orca transition timestamp is missing")
    error = value.get("last_error")
    if error is not None and (not isinstance(error, str) or not error.strip() or len(error) > 512):
        raise QueueError("Orca last error is invalid")
    if phase == "RECOVERY_REQUIRED" and error is None:
        raise QueueError("Orca recovery state lacks an error")
    return value


def _write_orca_state(task: Path, value: dict[str, object]) -> None:
    _atomic_json(task / ORCA_STATE_NAME, value)
    _load_orca_state(task, required=True)


def _handoff_snapshot_digest(task: Path) -> str:
    """Digest the exact canonical HANDOFF bytes used by one review generation."""

    path = task / "HANDOFF.md"
    try:
        body = _queue_text_bytes(path)
    except OSError as error:
        raise QueueError(f"review HANDOFF is unreadable: {task}") from error
    handoff = _read_handoff(task)
    missing = [key for key in HANDOFF_KEYS if key not in handoff]
    if missing:
        raise QueueError(f"review HANDOFF is incomplete: {missing}")
    canonical = _handoff_text(handoff).encode("utf-8")
    if body != canonical:
        raise QueueError("review HANDOFF is not canonical")
    return hashlib.sha256(body).hexdigest()


def iter_tasks(root: Path) -> Iterable[tuple[str, Path]]:
    for state in STATE_PARTS:
        base = state_path(root, state)
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir(), key=lambda item: item.name):
            if child.is_dir() and not child.name.startswith("."):
                yield state, child


def find_task(root: Path, reference: str, allowed: set[str] | None = None) -> tuple[str, Path, dict[str, object]]:
    matches = []
    for state, task in iter_tasks(root):
        if allowed is not None and state not in allowed:
            continue
        try:
            meta = _load_meta(task)
        except QueueError:
            continue
        if reference in {task.name, str(meta.get("id")), str(meta.get("legacy_id"))}:
            matches.append((state, task, meta))
    indexed = [
        entry for entry in _load_completed_index(root)
        if reference in {
            str(entry["id"]), str(entry["directory"]), str(entry.get("legacy_id")),
        }
    ]
    if indexed and not matches:
        raise QueueError(f"task is compacted and has no mutable directory: {reference}")
    if len(matches) + len(indexed) != 1:
        raise QueueError(
            f"task reference must resolve exactly once: {reference!r} "
            f"({len(matches) + len(indexed)} matches)"
        )
    return matches[0]


def _task_sort(item: tuple[str, Path, dict[str, object]]) -> tuple[object, ...]:
    _state, path, meta = item
    return (
        PRIORITY_ORDER.get(str(meta.get("priority")), 99),
        str(meta.get("created_at") or ""),
        path.name,
    )


def _all(root: Path) -> list[tuple[str, Path, dict[str, object]]]:
    result = []
    for state, task in iter_tasks(root):
        try:
            result.append((state, task, _load_meta(task)))
        except QueueError:
            result.append((state, task, {}))
    return result


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    completed_index = root / COMPLETED_INDEX_NAME
    if completed_index.is_file():
        digest.update(COMPLETED_INDEX_NAME.encode("utf-8") + b"\0")
        digest.update(_queue_text_bytes(completed_index))
    for state, task, _meta in sorted(_all(root), key=lambda item: (item[0], item[1].name)):
        digest.update(f"{state}/{task.name}\0".encode())
        for name in ("META.json", "HANDOFF.md", "WAITING.md", ORCA_STATE_NAME, "REVIEW.md"):
            path = task / name
            if path.is_file():
                digest.update(_queue_text_bytes(path))
    return digest.hexdigest()


def _board(root: Path, *, updated_at: str | None = None) -> str:
    tasks = _all(root)
    digest = _digest(root)
    lines = [
        "# Request Queue Board", "", f"updated_at: {updated_at or _stamp()}",
        f"generated_from_digest: {digest}", "mode: domain-parallel",
        f"writer_limit: {WRITER_LIMIT}", "", "## Active",
    ]
    active = sorted((item for item in tasks if item[0] == "active"), key=_task_sort)
    if not active:
        lines.append("- none")
    for _state, path, meta in active:
        handoff = _read_handoff(path)
        orca_state = _load_orca_state(path)
        lines.append(
            f"- {path.name} | owner={meta.get('owner') or '-'} | "
            f"domain={meta.get('domain') or '-'} | lead={meta.get('lead_owner') or '-'} | "
            f"lane={_writer_lane(meta, meta.get('write_scope') or [])} | "
            f"orca={'linked' if orca_state else '-'} | "
            f"phase={handoff.get('phase') or '-'} | heartbeat={meta.get('heartbeat') or '-'} | "
            f"next={handoff.get('next') or '-'}"
        )
    lines.extend(("", "## Review"))
    review = sorted((item for item in tasks if item[0] == "review"), key=_task_sort)
    if not review:
        lines.append("- none")
    for _state, path, meta in review:
        orca_state = _load_orca_state(path)
        lines.append(
            f"- {path.name} | domain={meta.get('domain') or '-'} | "
            f"lead={meta.get('lead_owner') or '-'} | reviewer={meta.get('reviewer') or '-'} | "
            f"orca={'linked' if orca_state else '-'}"
        )
    lines.extend(("", "## Waiting"))
    waiting = sorted((item for item in tasks if item[0] == "waiting"), key=_task_sort)
    if not waiting:
        lines.append("- none")
    for _state, path, meta in waiting:
        receipt = _read_waiting(path)
        lines.append(
            f"- {path.name} | domain={meta.get('domain') or '-'} | "
            f"lead={meta.get('lead_owner') or '-'} | "
            f"next_check_at={receipt.get('next_check_at') or '-'} | "
            f"resume={receipt.get('resume_condition') or '-'}"
        )
    lines.extend(("", "## Ready"))
    ready = sorted((item for item in tasks if item[0] == "ready"), key=_task_sort)
    if not ready:
        lines.append("- none")
    for number, (_state, path, meta) in enumerate(ready, 1):
        raw_dependencies = meta.get("depends_on")
        dependencies = (
            ",".join(str(value) for value in raw_dependencies) or "-"
            if isinstance(raw_dependencies, list) else "<invalid>"
        )
        lines.append(
            f"{number}. {path.name} | domain={meta.get('domain') or '-'} | "
            f"lead={meta.get('lead_owner') or '-'} | depends_on={dependencies}"
        )
    new_count = sum(state == "new" for state, _path, _meta in tasks)
    lines.extend(("", "## New Discoveries", f"- count: {new_count}", "", "## Blocked"))
    blocked = sorted((item for item in tasks if item[0] == "blocked"), key=_task_sort)
    if not blocked:
        lines.append("- none")
    for _state, path, _meta in blocked:
        handoff = _read_handoff(path)
        lines.append(f"- {path.name} | next={handoff.get('next') or '-'}")
    return "\n".join(lines) + "\n"


def write_board(root: Path, *, updated_at: str | None = None) -> None:
    _atomic_text(root / "BOARD.md", _board(root, updated_at=updated_at))


def _new_id(now: datetime | None = None) -> str:
    return f"RQ-{(now or _now()).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2).upper()}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^\w-]+", "-", value.strip().lower(), flags=re.UNICODE).strip("-_")
    return slug[:72] or "task"


def _move(root: Path, state: str, task: Path, meta: dict[str, object]) -> Path:
    destination = state_path(root, state) / task.name
    if destination.exists():
        raise QueueError(f"destination already exists: {destination}")
    os.replace(task, destination)
    meta["state"] = state
    meta["updated_at"] = _stamp()
    _atomic_json(destination / "META.json", meta)
    write_board(root)
    return destination


def _validate_scope(values: Sequence[str]) -> list[str]:
    result = []
    for value in values:
        path = Path(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or any(character in value for character in "*?[],;<>:\"|")
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise QueueError(f"write scope must be an exact repository-relative path: {value}")
        normalized = path.as_posix().strip("/")
        if not normalized or normalized == "." or normalized != value:
            raise QueueError("write scope cannot be empty")
        result.append(normalized)
    return sorted(set(result))


def _validate_resource_locks(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*", value) is None
        ):
            raise QueueError(f"resource lock must be one normalized token: {value}")
        if value not in result:
            result.append(value)
    return sorted(result)


def _scope_domain(path: str) -> str:
    normalized = Path(path).as_posix()
    if (
        normalized.startswith("src/stock_data/gui/")
        or normalized.startswith("tests/unit/gui/")
        or normalized.startswith("tests/integration/gui/")
        or normalized.startswith("docs/gui/")
    ):
        return "gui"
    if (
        normalized.startswith("src/stock_data/backtest/")
        or normalized.startswith("src/stock_data/features/")
        or normalized.startswith("tests/unit/backtest/")
        or normalized.startswith("tests/integration/backtest/")
        or normalized.startswith("docs/backtest/")
    ):
        return "backtest"
    if (
        normalized.startswith("src/stock_data/")
        or normalized.startswith("tests/unit/contracts/")
        or normalized.startswith("tests/unit/providers/")
        or normalized.startswith("tests/unit/storage/")
        or normalized.startswith("tests/unit/validation/")
        or normalized.startswith("tests/unit/derived/")
        or normalized.startswith("tests/integration/pipelines/")
        or normalized.startswith("tests/integration/daily_operations/")
        or normalized.startswith("docs/data/")
    ):
        return "data"
    return "shared"


def _writer_lane(meta: dict[str, object], scope: Sequence[str]) -> str:
    domains = {_scope_domain(path) for path in scope}
    inferred = next(iter(domains)) if len(domains) == 1 else "shared"
    declared = meta.get("writer_lane")
    if declared is not None:
        if declared not in WRITER_LANES:
            raise QueueError(f"invalid writer lane: {declared}")
        if inferred != "shared" and declared not in {inferred, "shared"}:
            raise QueueError(
                f"writer lane {declared} conflicts with inferred {inferred} scope"
            )
        return str(declared)
    return inferred


def _resource_locks(meta: dict[str, object]) -> list[str]:
    raw = meta.get("resource_locks", [])
    if not isinstance(raw, list):
        raise QueueError("resource locks must be a list")
    return _validate_resource_locks(raw)


def _scopes_overlap(left: Sequence[str], right: Sequence[str]) -> bool:
    left_parts = [tuple(part.casefold() for part in Path(value).parts) for value in left]
    right_parts = [tuple(part.casefold() for part in Path(value).parts) for value in right]
    return any(
        first == second[:len(first)] or second == first[:len(second)]
        for first in left_parts for second in right_parts
    )


def _validate_dependencies(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or TASK_ID.fullmatch(value) is None:
            raise QueueError(f"dependency must be one exact task id: {value}")
        if value not in result:
            result.append(value)
    return result


def _split_joined_metadata(values: object, *, label: str) -> tuple[list[str], bool]:
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise QueueError(f"{label} metadata is not a string list")
    result: list[str] = []
    changed = False
    for value in values:
        parts = value.split(",")
        if any(not part or part != part.strip() for part in parts):
            raise QueueError(f"{label} has an empty or padded joined value")
        changed = changed or len(parts) > 1
        result.extend(parts)
    return result, changed


def command_init(args: argparse.Namespace, root: Path) -> None:
    ensure_layout(root)
    readme = root / "README.md"
    if not readme.exists():
        _atomic_text(readme, (
            "# Request Queue\n\n"
            "This queue is managed by `scripts/request_queue.py`. "
            "Use `BOARD.md` for the current snapshot.\n"
        ))
    write_board(root)
    print(root)


def command_status(args: argparse.Namespace, root: Path) -> None:
    tasks = _all(root)
    if args.lead_owner is not None:
        lead_owner = _validated_lead_owner(args.lead_owner)
        assert lead_owner is not None
        routed = sorted(
            (item for item in tasks if item[2].get("lead_owner") == lead_owner),
            key=lambda item: (list(STATE_PARTS).index(item[0]), *_task_sort(item)),
        )
        if not routed:
            print(f"lead={lead_owner} tasks=none")
            return
        for state, path, meta in routed:
            handoff = _read_handoff(path)
            dependencies = meta.get("depends_on")
            depends_on = (
                ",".join(str(value) for value in dependencies) or "-"
                if isinstance(dependencies, list) else "<invalid>"
            )
            print(
                f"state={state} task={path.name} priority={meta.get('priority')} "
                f"domain={meta.get('domain') or '-'} depends_on={depends_on} "
                f"generation={_queue_generation(path)} "
                f"phase={handoff.get('phase') or '-'} next={handoff.get('next') or '-'}"
            )
        return
    if args.compact:
        counts = {state: sum(item[0] == state for item in tasks) for state in STATE_PARTS}
        counts_text = " ".join(f"{state}={counts[state]}" for state in STATE_PARTS)
        print(f"{counts_text} compacted={len(_load_completed_index(root))}")
        active = [item[1].name for item in tasks if item[0] == "active"]
        print(f"active={','.join(active) if active else '-'}")
        return
    print(_board(root), end="")


def command_discover(args: argparse.Namespace, root: Path) -> None:
    ensure_layout(root)
    duplicates = [
        path.name for _state, path, meta in _all(root)
        if meta.get("fingerprint") == args.fingerprint
    ]
    if duplicates:
        raise QueueError(f"fingerprint already exists: {args.fingerprint}: {duplicates}")
    completed_entries = _load_completed_index(root)
    compacted = [
        str(entry["id"]) for entry in completed_entries
        if entry.get("fingerprint") == args.fingerprint
    ]
    if compacted:
        raise QueueError(
            f"fingerprint already exists in completed index: {args.fingerprint}: {compacted}"
        )
    source_task = str(args.source_task).strip().casefold()
    goal_discovery = (
        source_task in {"project_goal", "project-goal"}
        or source_task.startswith("goal:")
    )
    if goal_discovery:
        live = tuple(
            (state, meta) for state, _path, meta in _all(root)
            if state in {"waiting", "ready", "active", "review"}
        )
        p0_live = any(meta.get("priority") == "P0" for _state, meta in live)
        if p0_live or len(live) >= 6:
            reason = "P0 live" if p0_live else f"live backlog={len(live)}"
            raise QueueError(
                "unsolicited Goal discovery paused by backlog throttle: "
                f"{reason}; explicit user intake and task-derived defects remain allowed"
            )
    now = _now()
    task_id = _new_id(now)
    if any(entry.get("id") == task_id for entry in completed_entries):
        raise QueueError(f"task id already exists in completed index: {task_id}")
    slug = _slug(args.title)
    priority = args.priority_hint
    name = f"{priority}-{task_id}-{slug}"
    parent = state_path(root, "new")
    temporary = parent / f".{name}.{secrets.token_hex(4)}.tmp"
    destination = parent / name
    temporary.mkdir()
    try:
        meta: dict[str, object] = {
            "schema_version": 1, "id": task_id, "title": args.title,
            "slug": slug, "legacy_id": None, "priority": priority,
            "priority_hint": priority, "kind": "bug", "risk": "untriaged",
            "state": "new", "owner": None, "assigned_role": None,
            "assigned_agent": None, "reviewer": None,
            "created_by": args.discovered_by, "created_at": _stamp(now),
            "updated_at": _stamp(now), "completed_at": None,
            "parent_task": args.source_task, "depends_on": [],
            "fingerprint": args.fingerprint, "write_scope": [],
            "writer_lane": None, "resource_locks": [],
            "domain": args.domain, "lead_owner": _validated_lead_owner(args.lead_owner),
            "parallelizable": True, "review_required": False,
            "lease_until": None, "heartbeat": None, "worktree": None,
            "branch": None,
        }
        _atomic_json(temporary / "META.json", meta)
        task_text = (
            f"# {args.title}\n\n## Problem\n{args.symptom}\n\n## Evidence\n{args.evidence}\n\n"
            f"## Impact\n{args.impact}\n\n## Scope\nallow:\n- {args.suspected_scope}\n"
            "deny:\n- unrelated files and operations\n\n## Done When\nTriage defines the exact acceptance boundary.\n\n"
            f"## Verify\n{args.reproduce}\n"
        )
        _atomic_text(temporary / "TASK.md", task_text)
        _atomic_text(temporary / "HANDOFF.md", _handoff_text({
            "updated_at": _stamp(now), "phase": "discovered",
            "summary": args.symptom, "completed": "evidence captured",
            "next": "Coordinator triage", "files_touched": "none",
            "tests": args.reproduce, "risks": "untriaged",
            "new_discoveries": "none",
        }))
        os.replace(temporary, destination)
    finally:
        if temporary.is_dir():
            for name in REQUIRED_TASK_FILES:
                try:
                    (temporary / name).unlink()
                except FileNotFoundError:
                    pass
            temporary.rmdir()
    write_board(root)
    print(task_id)


def command_triage(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"new"})
    scope = _validate_scope(args.write_scope)
    dependencies = _validate_dependencies(args.depends_on)
    if args.review_required and not args.reviewer:
        raise QueueError("review-required triage needs --reviewer")
    meta.update({
        "priority": args.priority, "risk": args.risk,
        "write_scope": scope, "depends_on": dependencies,
        "writer_lane": _writer_lane({"writer_lane": args.writer_lane}, scope),
        "resource_locks": _validate_resource_locks(args.resource_lock),
        "parallelizable": args.parallelizable,
        "review_required": args.review_required, "reviewer": args.reviewer,
    })
    if args.domain is not None:
        meta["domain"] = args.domain
    if args.lead_owner is not None:
        meta["lead_owner"] = _validated_lead_owner(args.lead_owner)
    allow = "\n".join(f"- {value}" for value in args.allow)
    deny = "\n".join(f"- {value}" for value in args.deny)
    _atomic_text(task / "TASK.md", (
        f"# {meta.get('title')}\n\n## Problem\n{args.problem}\n\n"
        f"## Evidence\n{args.evidence}\n\n## Scope\nallow:\n{allow}\n\n"
        f"deny:\n{deny}\n\n## Done When\n{args.done_when}\n\n## Verify\n{args.verify}\n"
    ))
    slug = str(meta.get("slug") or _slug(str(meta.get("title") or "task")))
    renamed = task.with_name(f"{args.priority}-{meta['id']}-{slug}")
    if renamed != task:
        if renamed.exists():
            raise QueueError(f"triaged task name exists: {renamed}")
        os.replace(task, renamed)
        task = renamed
    _move(root, "ready", task, meta)
    print(meta["id"])


def command_retarget(args: argparse.Namespace, root: Path) -> None:
    allowed_states = {"new", "ready", "done"} if args.repair_joined else {"new", "ready"}
    _state, task, meta = find_task(root, args.task, allowed_states)
    if args.repair_joined and any(value is not None for value in (
        args.write_scope, args.depends_on, args.writer_lane, args.resource_lock,
    )):
        raise QueueError("joined metadata repair does not accept replacement values")
    if not args.repair_joined and all(value is None for value in (
        args.write_scope, args.depends_on, args.writer_lane, args.resource_lock,
    )):
        raise QueueError("retarget requires write scope, dependencies, writer lane, or resource locks")
    if args.repair_joined:
        scope_values, scope_changed = _split_joined_metadata(
            meta.get("write_scope"), label="write scope"
        )
        dependency_values, dependencies_changed = _split_joined_metadata(
            meta.get("depends_on"), label="dependencies"
        )
        if not scope_changed and not dependencies_changed:
            raise QueueError("task metadata has no joined values to repair")
        scope = _validate_scope(scope_values)
        dependencies = _validate_dependencies(dependency_values)
        resource_locks = None
    else:
        scope = None if args.write_scope is None else _validate_scope(args.write_scope)
        dependencies = (
            None if args.depends_on is None else _validate_dependencies(args.depends_on)
        )
        resource_locks = (
            None if args.resource_lock is None
            else _validate_resource_locks(args.resource_lock)
        )
    if scope is not None and not scope:
        raise QueueError("write scope cannot be empty")
    now = _now()
    if scope is not None:
        meta["write_scope"] = scope
    if dependencies is not None:
        meta["depends_on"] = dependencies
    if args.writer_lane is not None:
        effective_scope = scope if scope is not None else _validate_scope(
            meta.get("write_scope") or []
        )
        meta["writer_lane"] = _writer_lane(
            {"writer_lane": args.writer_lane}, effective_scope
        )
    elif scope is not None:
        meta["writer_lane"] = _writer_lane({}, scope)
    if resource_locks is not None:
        meta["resource_locks"] = resource_locks
    meta["updated_at"] = _stamp(now)
    _atomic_json(task / "META.json", meta)
    handoff = _read_handoff(task)
    handoff["updated_at"] = _stamp(now)
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    write_board(root)
    print(meta["id"])


def command_claim(args: argparse.Namespace, root: Path) -> None:
    if args.lease_minutes <= 0:
        raise QueueError("lease minutes must be positive")
    claim_token = secrets.token_hex(32)
    stale_managers = _linked_worktree_managers(root)
    if stale_managers:
        raise QueueError(
            f"linked-worktree queue managers must be removed before parallel claims: "
            f"{stale_managers}"
        )
    lock_name = hashlib.sha256(args.task.encode("utf-8")).hexdigest()[:20]
    lock_path = state_path(root, "ready") / f".claim-{lock_name}.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise QueueError(f"claim already in progress: {args.task}") from error
    try:
        _state, task, meta = find_task(root, args.task, {"ready"})
        if bool(meta.get("review_required")):
            reviewer = meta.get("reviewer")
            if not reviewer:
                raise QueueError("review-required task has no reviewer")
            if reviewer == args.owner:
                raise QueueError("reviewer must differ from the implementing owner")
        all_tasks = _all(root)
        same_owner = [
            active for state, _path, active in all_tasks
            if state == "active" and active.get("owner") == args.owner
        ]
        routed_lead = _validated_lead_owner(meta.get("lead_owner"))
        if routed_lead is not None:
            if args.role != "lead" or args.owner != routed_lead:
                raise QueueError(f"task is routed to Lead: {routed_lead}")
            if args.domain is not None and args.domain != meta.get("domain"):
                raise QueueError(
                    f"claim domain differs from routed domain: {meta.get('domain')}"
                )
        if args.role == "lead":
            _validated_lead_owner(args.owner)
            if any(active.get("assigned_role") != "lead" for active in same_owner):
                raise QueueError(f"lead owner also holds a non-lead Active task: {args.owner}")
            if len(same_owner) >= LEAD_WIP_LIMIT:
                raise QueueError(f"lead WIP limit reached: {args.owner}")
        elif same_owner:
            raise QueueError(f"owner already has an active task: {args.owner}")
        raw_dependencies = meta.get("depends_on")
        if not isinstance(raw_dependencies, list):
            raise QueueError("ready task dependencies are invalid")
        try:
            dependencies = _validate_dependencies(raw_dependencies)
        except QueueError as error:
            raise QueueError(f"ready task dependencies are invalid: {error}") from error
        completed = {str(item.get("id")) for state, _path, item in all_tasks if state == "done"}
        completed.update(str(entry["id"]) for entry in _load_completed_index(root))
        missing = [value for value in dependencies if value not in completed]
        if missing:
            raise QueueError(f"unfinished dependencies: {missing}")
        raw_scope = meta.get("write_scope")
        if not isinstance(raw_scope, list):
            raise QueueError("ready task write scope is invalid")
        try:
            scope = _validate_scope(raw_scope)
        except (QueueError, TypeError) as error:
            raise QueueError(f"ready task write scope is invalid: {error}") from error
        if not scope:
            raise QueueError("ready task has no production write scope")
        lane = _writer_lane(meta, scope)
        locks = _resource_locks(meta)
        for state, review_path, review_meta in all_tasks:
            if state != "review":
                continue
            review_raw_scope = review_meta.get("write_scope")
            if not isinstance(review_raw_scope, list):
                raise QueueError(
                    f"review reservation scope is invalid: {review_path.name}"
                )
            try:
                review_scope = _validate_scope(review_raw_scope)
            except (QueueError, TypeError) as error:
                raise QueueError(
                    f"review reservation scope is invalid: {review_path.name}: {error}"
                ) from error
            if _scopes_overlap(scope, review_scope):
                raise QueueError(
                    f"write scope reserved by review task: {review_path.name}"
                )
        active_writers = [
            (path, item) for state, path, item in all_tasks
            if state == "active" and item.get("write_scope")
        ]
        if len(active_writers) >= WRITER_LIMIT:
            raise QueueError("production writer limit reached")
        for active_path, active in active_writers:
            active_scope = _validate_scope(active.get("write_scope") or [])
            active_lane = _writer_lane(active, active_scope)
            active_locks = _resource_locks(active)
            if _scopes_overlap(scope, active_scope):
                raise QueueError(f"write scope conflicts with active task: {active_path.name}")
            if lane == "shared" or active_lane == "shared":
                raise QueueError(
                    f"writer lane conflicts with active task: {lane} vs {active_lane}: "
                    f"{active_path.name}"
                )
            shared_locks = sorted(set(locks).intersection(active_locks))
            if shared_locks:
                raise QueueError(
                    f"resource locks conflict with active task {active_path.name}: {shared_locks}"
                )
        destination = state_path(root, "active") / task.name
        if destination.exists():
            raise QueueError(f"active destination exists: {destination}")
        os.replace(task, destination)
        now = _now()
        if args.domain is not None:
            meta["domain"] = args.domain
        if args.role == "lead":
            meta["lead_owner"] = _validated_lead_owner(args.owner)
        meta.update({
            "state": "active", "owner": args.owner, "assigned_role": args.role,
            "assigned_agent": args.owner, "heartbeat": _stamp(now),
            "lease_until": _stamp(now + timedelta(minutes=args.lease_minutes)),
            "claim_token_sha256": _claim_token_digest(claim_token),
            "updated_at": _stamp(now),
        })
        _atomic_json(destination / "META.json", meta)
        handoff = _read_handoff(destination)
        handoff.update({"updated_at": _stamp(now), "phase": "claimed", "next": args.next})
        _atomic_text(destination / "HANDOFF.md", _handoff_text(handoff))
        write_board(root)
        print(meta["id"])
        print(f"claim_token={claim_token}")
    finally:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _validate_task_contract_text(task_text: str, *, expected_title: str) -> None:
    if len(task_text.encode("utf-8")) > 32_768 or "\x00" in task_text:
        raise QueueError("expired active recovery TASK size/content is invalid")
    lines = task_text.splitlines()
    if not lines or lines[0] != f"# {expected_title}":
        raise QueueError("expired active recovery TASK title is invalid")
    for line in lines[1:]:
        commonmark_heading = re.match(r"^ {0,3}(#{1,2})(?:[ \t]+|$)", line)
        if commonmark_heading is None:
            continue
        if commonmark_heading.group(1) == "#":
            raise QueueError("expired active recovery TASK has multiple titles")
        if not line.startswith("## "):
            raise QueueError("expired active recovery TASK sections are invalid")
    required = ("Problem", "Evidence", "Scope", "Done When", "Verify")
    headings = [
        (index, line.removeprefix("## "))
        for index, line in enumerate(lines) if line.startswith("## ")
    ]
    if tuple(name for _index, name in headings) != required:
        raise QueueError("expired active recovery TASK sections are invalid")
    if headings and any(line.strip() for line in lines[1:headings[0][0]]):
        raise QueueError("expired active recovery TASK has unowned pre-section content")
    for position, (start, name) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        body_lines = lines[start + 1:end]
        body = "\n".join(body_lines).strip()
        if not body or len(body.encode("utf-8")) > 8_192:
            raise QueueError(f"expired active recovery TASK {name} body is invalid")
        if name == "Scope":
            scope_lines = [line.strip() for line in body_lines if line.strip()]
            if (
                not scope_lines or scope_lines[0] != "allow:"
                or scope_lines.count("allow:") != 1 or scope_lines.count("deny:") != 1
            ):
                raise QueueError("expired active recovery TASK Scope is invalid")
            deny_index = scope_lines.index("deny:")
            allow_items = scope_lines[1:deny_index]
            deny_items = scope_lines[deny_index + 1:]
            if (
                not allow_items or not deny_items
                or any(not item.startswith("- ") or len(item) <= 2 for item in (*allow_items, *deny_items))
            ):
                raise QueueError("expired active recovery TASK Scope items are invalid")


def _validate_expired_active_receipt(task: Path, meta: dict[str, object]) -> None:
    """Fail closed unless one task is a complete canonical Active receipt."""

    try:
        entries = list(task.iterdir())
    except OSError as error:
        raise QueueError(f"expired active recovery task is unreadable: {task}") from error
    expected_files = set(REQUIRED_TASK_FILES)
    actual_files = {entry.name for entry in entries}
    if (
        frozenset(actual_files) not in {
            frozenset(expected_files), frozenset((*expected_files, ORCA_STATE_NAME)),
        }
        or any(entry.is_symlink() or not entry.is_file() for entry in entries)
    ):
        raise QueueError("expired active recovery task files are invalid")
    if ORCA_STATE_NAME in actual_files:
        _load_orca_state(task, required=True)

    match = TASK_NAME.fullmatch(task.name)
    if match is None:
        raise QueueError("expired active recovery task directory is invalid")
    missing = [key for key in META_KEYS if key not in meta]
    if missing:
        raise QueueError(f"expired active recovery META is incomplete: {missing}")
    if meta.get("schema_version") != 1 or isinstance(meta.get("schema_version"), bool):
        raise QueueError("expired active recovery META schema is invalid")
    if meta.get("state") != "active":
        raise QueueError("expired active recovery META state is invalid")
    if meta.get("id") != match.group(2) or meta.get("priority") != match.group(1):
        raise QueueError("expired active recovery task identity is invalid")

    required_strings = (
        "id", "title", "slug", "priority", "priority_hint", "kind", "risk",
        "state", "owner", "assigned_role", "assigned_agent", "created_by",
        "created_at", "updated_at", "fingerprint", "lease_until", "heartbeat",
    )
    if any(
        not isinstance(meta.get(key), str) or not str(meta.get(key)).strip()
        for key in required_strings
    ):
        raise QueueError("expired active recovery META string field is invalid")
    optional_strings = (
        "legacy_id", "reviewer", "completed_at", "parent_task", "worktree", "branch",
    )
    if any(
        meta.get(key) is not None and not isinstance(meta.get(key), str)
        for key in optional_strings
    ):
        raise QueueError("expired active recovery META optional field is invalid")
    if meta.get("completed_at") is not None:
        raise QueueError("expired active recovery completion state is invalid")
    if any(not isinstance(meta.get(key), bool) for key in ("parallelizable", "review_required")):
        raise QueueError("expired active recovery META boolean field is invalid")
    if meta.get("assigned_agent") != meta.get("owner"):
        raise QueueError("expired active recovery assignment differs from owner")
    if meta.get("reviewer") == meta.get("assigned_agent"):
        raise QueueError("expired active recovery reviewer matches implementing agent")

    dependencies = meta.get("depends_on")
    if not isinstance(dependencies, list):
        raise QueueError("expired active recovery dependencies are invalid")
    _validate_dependencies(dependencies)
    raw_scope = meta.get("write_scope")
    if not isinstance(raw_scope, list) or not all(isinstance(value, str) for value in raw_scope):
        raise QueueError("expired active recovery write scope is invalid")
    scope = _validate_scope(raw_scope)
    if not scope:
        raise QueueError("expired active recovery write scope is empty")
    _writer_lane(meta, scope)
    _resource_locks(meta)

    created = _aware(meta.get("created_at"))
    updated = _aware(meta.get("updated_at"))
    heartbeat = _aware(meta.get("heartbeat"))
    lease = _aware(meta.get("lease_until"))
    if None in {created, updated, heartbeat, lease}:
        raise QueueError("expired active recovery timestamps are invalid")
    if heartbeat != updated or not created <= heartbeat <= lease:
        raise QueueError("expired active recovery timestamp ordering is invalid")
    claim_digest = meta.get("claim_token_sha256")
    if not isinstance(claim_digest, str) or re.fullmatch(r"[0-9a-f]{64}", claim_digest) is None:
        raise QueueError("expired active recovery claim digest is invalid")

    try:
        meta_body = _queue_text_bytes(task / "META.json")
        canonical_meta = (
            json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        task_text = (task / "TASK.md").read_text(encoding="utf-8")
    except OSError as error:
        raise QueueError("expired active recovery receipt is unreadable") from error
    if meta_body != canonical_meta:
        raise QueueError("expired active recovery META is not canonical")
    _validate_task_contract_text(task_text, expected_title=str(meta["title"]))
    _handoff_snapshot_digest(task)
    handoff = _read_handoff(task)
    if handoff.get("updated_at") != meta.get("updated_at"):
        raise QueueError("expired active recovery HANDOFF generation differs")
    if not handoff.get("phase") or not handoff.get("next"):
        raise QueueError("expired active recovery HANDOFF state is invalid")


def command_recover_expired_active(args: argparse.Namespace, root: Path) -> None:
    """Return one exactly pinned expired Active task to Ready."""

    coordinator = _clean(args.coordinator)
    decision_basis = _clean(args.decision_basis)
    next_action = _clean(args.next)
    if not coordinator or not decision_basis or not next_action:
        raise QueueError("expired active recovery fields cannot be empty")
    if len(decision_basis) > 240:
        raise QueueError("expired active recovery basis is too long")
    if re.fullmatch(r"[0-9a-f]{64}", args.expected_claim_token_sha256) is None:
        raise QueueError("expected expired active claim digest is invalid")

    _state, task, meta = find_task(root, args.task, {"active"})
    _validate_expired_active_receipt(task, meta)
    if meta.get("owner") != args.expected_owner:
        raise QueueError(f"expired active recovery owner differs: {meta.get('owner')}")
    if meta.get("updated_at") != args.expected_updated_at:
        raise QueueError("expired active recovery generation timestamp differs")
    if meta.get("lease_until") != args.expected_lease_until:
        raise QueueError("expired active recovery lease identity differs")
    retained_digest = meta.get("claim_token_sha256")
    if not isinstance(retained_digest, str) or not secrets.compare_digest(
        retained_digest, args.expected_claim_token_sha256,
    ):
        raise QueueError("expired active recovery claim digest differs")
    lease = _aware(meta.get("lease_until"))
    assert lease is not None
    now = _now()
    if lease > now:
        raise QueueError("active lease has not expired")

    destination = state_path(root, "ready") / task.name
    if destination.exists():
        raise QueueError(f"expired active recovery destination exists: {destination}")

    meta.pop("claim_token_sha256", None)
    meta.update({
        "owner": None,
        "assigned_role": None,
        "assigned_agent": None,
        "heartbeat": None,
        "lease_until": None,
        "updated_at": _stamp(now),
    })
    handoff = _read_handoff(task)
    handoff.update({
        "updated_at": _stamp(now),
        "phase": "coordinator_recovery",
        "summary": (
            f"Expired Active capability cleared by coordinator {coordinator}: "
            f"{decision_basis}"
        ),
        "next": next_action,
    })
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    try:
        (task / ORCA_STATE_NAME).unlink()
    except FileNotFoundError:
        pass
    _move(root, "ready", task, meta)
    print(meta["id"])


def command_checkpoint(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"active"})
    _authorize_active_claim(args, root, task, meta)
    if args.lease_minutes <= 0:
        raise QueueError("lease minutes must be positive")
    now = _now()
    meta["heartbeat"] = _stamp(now)
    meta["lease_until"] = _stamp(now + timedelta(minutes=args.lease_minutes))
    meta["updated_at"] = _stamp(now)
    if args.require_review:
        reviewer = args.reviewer or meta.get("reviewer")
        if not reviewer:
            raise QueueError("review checkpoint requires --reviewer")
        if reviewer == meta.get("assigned_agent"):
            raise QueueError("reviewer must differ from the implementing agent")
        meta["review_required"] = True
        meta["reviewer"] = reviewer
    elif args.reviewer:
        if args.reviewer == meta.get("assigned_agent"):
            raise QueueError("reviewer must differ from the implementing agent")
        meta["reviewer"] = args.reviewer
    _atomic_json(task / "META.json", meta)
    handoff = _read_handoff(task)
    for key in HANDOFF_KEYS:
        value = getattr(args, key, None)
        if value is not None:
            handoff[key] = value
    handoff["updated_at"] = _stamp(now)
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    write_board(root)
    print(meta["id"])


def _result(task: Path, result: str, changed: str, verified: str, completed_at: str) -> None:
    _atomic_text(task / "RESULT.md", (
        f"result: {_clean(result)}\nchanged: {_clean(changed)}\n"
        f"verified: {_clean(verified)}\ncompleted_at: {completed_at}\n"
    ))


def _review(
    task: Path, result: str, changed: str, verified: str, *,
    review_generation: str, submitted_at: str, handoff_sha256: str,
) -> None:
    body = (
        f"result: {_clean(result)}\nchanged: {_clean(changed)}\n"
        f"verified: {_clean(verified)}\n"
        f"review_generation: {review_generation}\n"
        f"handoff_sha256: {handoff_sha256}\n"
        f"submitted_at: {submitted_at}\n"
    )
    _atomic_text(task / "REVIEW.md", body)


def _read_fields(path: Path, keys: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key, separator, value = line.partition(":")
        if separator and key in keys:
            values[key] = value.strip()
    return values


def command_submit(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"active"})
    _authorize_active_claim(args, root, task, meta)
    needs_review = args.review or bool(meta.get("review_required"))
    reviewer = args.reviewer or meta.get("reviewer")
    if needs_review and not reviewer:
        raise QueueError("review submission requires a reviewer")
    if needs_review and reviewer == meta.get("assigned_agent"):
        raise QueueError("reviewer must differ from the implementing agent")
    orca_state = _load_orca_state(task)
    now = _now()
    if needs_review:
        review_generation = secrets.token_hex(16)
        handoff = _read_handoff(task)
        handoff.update({"updated_at": _stamp(now), "phase": "review", "next": "Independent review"})
        _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
        _review(
            task, args.result, args.changed, args.verified,
            review_generation=review_generation, submitted_at=_stamp(now),
            handoff_sha256=_handoff_snapshot_digest(task),
        )
        meta["reviewer"] = reviewer
        meta["review_required"] = True
        meta.pop("claim_token_sha256", None)
        meta.update({"owner": None, "lease_until": None, "heartbeat": None})
        _move(root, "review", task, meta)
    else:
        completed_at = _stamp(now)
        _result(task, args.result, args.changed, args.verified, completed_at)
        if orca_state is not None:
            (task / ORCA_STATE_NAME).unlink()
        meta.pop("claim_token_sha256", None)
        meta.update({"owner": None, "completed_at": completed_at, "lease_until": None, "heartbeat": None})
        handoff = _read_handoff(task)
        handoff.update({"updated_at": _stamp(now), "phase": "completed", "next": "none"})
        _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
        _move(root, "done", task, meta)
    print(meta["id"])


def command_review_pass(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"review"})
    if meta.get("reviewer") and meta.get("reviewer") != args.reviewer:
        raise QueueError(f"reviewer differs: {meta.get('reviewer')}")
    if args.reviewer == meta.get("assigned_agent"):
        raise QueueError("reviewer must differ from the implementing agent")
    review_keys = (
        "result", "changed", "verified", "review_generation",
        "handoff_sha256", "submitted_at",
    )
    review = _read_fields(task / "REVIEW.md", review_keys)
    required_review_keys = review_keys[:6]
    missing = [key for key in required_review_keys if not review.get(key)]
    if missing:
        raise QueueError(f"review receipt is incomplete: {missing}")
    if not secrets.compare_digest(
        review["review_generation"], args.review_generation,
    ):
        raise QueueError("review generation differs from the current submission")
    if not re.fullmatch(r"[0-9a-f]{64}", review["handoff_sha256"]):
        raise QueueError("review HANDOFF digest is invalid")
    if not secrets.compare_digest(
        review["handoff_sha256"], _handoff_snapshot_digest(task),
    ):
        raise QueueError("review HANDOFF differs from the submitted snapshot")
    decision_basis = _clean(args.decision_basis)
    if not decision_basis:
        raise QueueError("review decision basis cannot be empty")
    now = _now()
    completed_at = _stamp(now)
    verified = (
        f"{review['verified']}; independent review by {args.reviewer}: "
        f"{decision_basis}"
    )
    _result(task, review["result"], review["changed"], verified, completed_at)
    (task / "REVIEW.md").unlink()
    try:
        (task / ORCA_STATE_NAME).unlink()
    except FileNotFoundError:
        pass
    meta.update({"owner": None, "completed_at": completed_at, "lease_until": None, "heartbeat": None})
    handoff = _read_handoff(task)
    handoff.update({"updated_at": _stamp(now), "phase": "completed", "next": "none"})
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    _move(root, "done", task, meta)
    print(meta["id"])


def command_review_fail(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"review"})
    if meta.get("reviewer") and meta.get("reviewer") != args.reviewer:
        raise QueueError(f"reviewer differs: {meta.get('reviewer')}")
    review = _read_fields(
        task / "REVIEW.md", ("review_generation", "handoff_sha256"),
    )
    missing = [key for key in ("review_generation", "handoff_sha256") if not review.get(key)]
    if missing:
        raise QueueError(f"review receipt is incomplete: {missing}")
    if not secrets.compare_digest(
        review["review_generation"], args.review_generation,
    ):
        raise QueueError("review generation differs from the current submission")
    if not re.fullmatch(r"[0-9a-f]{64}", review["handoff_sha256"]):
        raise QueueError("review HANDOFF digest is invalid")
    if not secrets.compare_digest(
        review["handoff_sha256"], _handoff_snapshot_digest(task),
    ):
        raise QueueError("review HANDOFF differs from the submitted snapshot")
    decision_basis = _clean(args.decision_basis)
    if not decision_basis:
        raise QueueError("review decision basis cannot be empty")
    try:
        (task / "REVIEW.md").unlink()
    except FileNotFoundError:
        pass
    try:
        (task / ORCA_STATE_NAME).unlink()
    except FileNotFoundError:
        pass
    meta.update({"owner": None, "assigned_role": None, "assigned_agent": None, "completed_at": None, "lease_until": None, "heartbeat": None})
    handoff = _read_handoff(task)
    handoff.update({
        "updated_at": _stamp(), "phase": "rework",
        "summary": f"Independent review failed: {decision_basis}",
        "next": args.next,
    })
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    _move(root, "ready", task, meta)
    print(meta["id"])


def command_review_recover(args: argparse.Namespace, root: Path) -> None:
    """Return an unverifiable review snapshot to Ready without a verdict."""

    _state, task, meta = find_task(root, args.task, {"review"})
    if meta.get("reviewer") and meta.get("reviewer") != args.reviewer:
        raise QueueError(f"reviewer differs: {meta.get('reviewer')}")
    if args.reviewer == meta.get("assigned_agent"):
        raise QueueError("reviewer must differ from the implementing agent")
    review = _read_fields(
        task / "REVIEW.md", ("review_generation", "handoff_sha256"),
    )
    if not review.get("review_generation"):
        raise QueueError("review receipt has no generation")
    if not secrets.compare_digest(
        review["review_generation"], args.review_generation,
    ):
        raise QueueError("review generation differs from the current submission")
    snapshot_matches = False
    digest = review.get("handoff_sha256", "")
    if re.fullmatch(r"[0-9a-f]{64}", digest):
        try:
            snapshot_matches = secrets.compare_digest(
                digest, _handoff_snapshot_digest(task),
            )
        except QueueError:
            snapshot_matches = False
    if snapshot_matches:
        raise QueueError(
            "review HANDOFF still matches; use review-pass or review-fail"
        )
    decision_basis = _clean(args.decision_basis)
    next_action = _clean(args.next)
    if not decision_basis or not next_action:
        raise QueueError("review recovery basis and next action cannot be empty")
    try:
        (task / "REVIEW.md").unlink()
    except FileNotFoundError:
        pass
    try:
        (task / ORCA_STATE_NAME).unlink()
    except FileNotFoundError:
        pass
    meta.update({
        "owner": None, "assigned_role": None, "assigned_agent": None,
        "completed_at": None, "lease_until": None, "heartbeat": None,
    })
    handoff = _read_handoff(task)
    handoff.update({
        "updated_at": _stamp(), "phase": "rework",
        "summary": f"Review snapshot recovery required: {decision_basis}",
        "next": next_action,
    })
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    _move(root, "ready", task, meta)
    print(meta["id"])


def command_reopen(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"done"})
    reason = _clean(args.reason)
    next_action = _clean(args.next)
    if not reason or not next_action:
        raise QueueError("reopen reason and next action cannot be empty")
    result = _read_fields(
        task / "RESULT.md", ("result", "changed", "verified", "completed_at"),
    )
    if any(not result.get(key) for key in ("result", "changed", "verified", "completed_at")):
        raise QueueError("cannot reopen a Done task without a complete result receipt")
    (task / "RESULT.md").unlink()
    meta.update({
        "owner": None, "assigned_role": None, "assigned_agent": None,
        "completed_at": None, "lease_until": None, "heartbeat": None,
    })
    handoff = _read_handoff(task)
    handoff.update({
        "updated_at": _stamp(), "phase": "rework",
        "summary": f"Invalid Done receipt reopened: {reason}",
        "completed": "stale completion invalidated",
        "next": next_action,
    })
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    _move(root, "ready", task, meta)
    print(meta["id"])


def command_block(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"active"})
    _authorize_active_claim(args, root, task, meta)
    _atomic_text(task / "BLOCKED.md", (
        f"reason: {_clean(args.reason)}\nrequired_action: {_clean(args.required_action)}\n"
        f"resume_condition: {_clean(args.resume_condition)}\n"
    ))
    meta.pop("claim_token_sha256", None)
    meta.update({"owner": None, "lease_until": None, "heartbeat": None})
    handoff = _read_handoff(task)
    handoff.update({"updated_at": _stamp(), "phase": "blocked", "next": args.required_action})
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    try:
        (task / ORCA_STATE_NAME).unlink()
    except FileNotFoundError:
        pass
    _move(root, "blocked", task, meta)
    print(meta["id"])


def command_unblock(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"blocked"})
    meta.update({"owner": None, "assigned_role": None, "assigned_agent": None, "lease_until": None, "heartbeat": None})
    try:
        (task / "BLOCKED.md").unlink()
    except FileNotFoundError:
        pass
    handoff = _read_handoff(task)
    handoff.update({"updated_at": _stamp(), "phase": "ready", "next": args.next})
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    _move(root, "ready", task, meta)
    print(meta["id"])


def command_route(args: argparse.Namespace, root: Path) -> None:
    state, task, meta = find_task(root, args.task, {"new", "waiting", "ready", "active"})
    if state == "active":
        _authorize_active_claim(args, root, task, meta)
        if meta.get("assigned_role") == "lead" and args.lead_owner != meta.get("owner"):
            raise QueueError("an Active Lead task must remain routed to its token owner")
    lead_owner = _validated_lead_owner(args.lead_owner)
    assert lead_owner is not None
    now = _now()
    meta.update({
        "domain": args.domain, "lead_owner": lead_owner, "updated_at": _stamp(now),
    })
    if state == "active":
        meta["heartbeat"] = _stamp(now)
    _atomic_json(task / "META.json", meta)
    handoff = _read_handoff(task)
    handoff.update({"updated_at": _stamp(now), "next": args.next})
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    write_board(root)
    print(meta["id"])


def _clear_active_assignment(meta: dict[str, object]) -> None:
    meta.pop("claim_token_sha256", None)
    meta.update({
        "owner": None, "assigned_role": None, "assigned_agent": None,
        "lease_until": None, "heartbeat": None,
    })


def command_release(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"active"})
    _authorize_active_claim(args, root, task, meta)
    reason = _clean(args.reason)
    next_action = _clean(args.next)
    if not reason or not next_action:
        raise QueueError("release reason and next action cannot be empty")
    _clear_active_assignment(meta)
    handoff = _read_handoff(task)
    handoff.update({
        "updated_at": _stamp(), "phase": "released",
        "summary": f"Lead safely released Active work: {reason}", "next": next_action,
    })
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    try:
        (task / ORCA_STATE_NAME).unlink()
    except FileNotFoundError:
        pass
    _move(root, "ready", task, meta)
    print(meta["id"])


def command_wait(args: argparse.Namespace, root: Path) -> None:
    state, task, meta = find_task(root, args.task, {"ready", "active"})
    if state == "active":
        _authorize_active_claim(args, root, task, meta)
    reason = _clean(args.reason)
    resume_condition = _clean(args.resume_condition)
    next_check_at = _clean(args.next_check_at) or "none"
    if not reason or not resume_condition:
        raise QueueError("waiting reason and resume condition cannot be empty")
    if next_check_at != "none" and _aware(next_check_at) is None:
        raise QueueError("waiting next check timestamp is invalid")
    now = _now()
    _atomic_text(task / "WAITING.md", _waiting_text({
        "reason": reason, "resume_condition": resume_condition,
        "next_check_at": next_check_at, "waiting_since": _stamp(now),
    }))
    if state == "active":
        _clear_active_assignment(meta)
    handoff = _read_handoff(task)
    handoff.update({
        "updated_at": _stamp(now), "phase": "waiting", "summary": reason,
        "next": resume_condition,
    })
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    try:
        (task / ORCA_STATE_NAME).unlink()
    except FileNotFoundError:
        pass
    _move(root, "waiting", task, meta)
    print(meta["id"])


def command_resume_waiting(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"waiting"})
    decision_basis = _clean(args.decision_basis)
    next_action = _clean(args.next)
    if not decision_basis or not next_action:
        raise QueueError("waiting resume basis and next action cannot be empty")
    try:
        (task / "WAITING.md").unlink()
    except FileNotFoundError:
        pass
    handoff = _read_handoff(task)
    handoff.update({
        "updated_at": _stamp(), "phase": "ready",
        "summary": f"Waiting condition cleared: {decision_basis}", "next": next_action,
    })
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    _move(root, "ready", task, meta)
    print(meta["id"])


def command_orca_bind(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"active"})
    _authorize_active_claim(args, root, task, meta)
    if args.lease_minutes <= 0:
        raise QueueError("lease minutes must be positive")
    if meta.get("assigned_role") != "lead":
        raise QueueError("Orca execution binding requires a Lead-owned Queue task")
    if args.domain is not None and args.domain != meta.get("domain"):
        raise QueueError(f"Orca bind domain differs from routed domain: {meta.get('domain')}")
    if _load_orca_state(task) is not None:
        raise QueueError("Orca state is already bound")
    now = _now()
    next_action = _clean(args.next_action)
    if not next_action:
        raise QueueError("Orca next action cannot be empty")
    state: dict[str, object] = {
        "schema_version": ORCA_STATE_SCHEMA,
        "queue_task_id": meta["id"],
        "run_id": _validate_orca_identifier(args.run_id, label="run id"),
        "task_id": _validate_orca_identifier(args.orca_task_id, label="task id"),
        "dispatch_id": None,
        "attempt": 0,
        "phase": "BOUND",
        "waiting_for": "dispatch",
        "next_action": next_action,
        "candidate_commit": None,
        "diff_digest": None,
        "review_generation": None,
        "observed_dispatch_status": None,
        "last_transition_at": _stamp(now),
        "last_reconciled_at": None,
        "last_error": None,
    }
    _write_orca_state(task, state)
    meta.update({
        "lead_owner": args.owner, "domain": meta.get("domain") or "infra",
        "heartbeat": _stamp(now),
        "lease_until": _stamp(now + timedelta(minutes=args.lease_minutes)),
        "updated_at": _stamp(now),
    })
    _atomic_json(task / "META.json", meta)
    handoff = _read_handoff(task)
    handoff.update({
        "updated_at": _stamp(now), "phase": "orca_bound", "next": next_action,
    })
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    write_board(root)
    print(meta["id"])


def _write_orca_reconciliation_receipts(
    root: Path,
    task: Path,
    meta: dict[str, object],
    *,
    dispatch_id: str,
    observed_status: str,
    phase: str,
    next_action: str,
    reconciled_at: datetime,
    lease_minutes: int,
) -> None:
    stamp = _stamp(reconciled_at)
    meta.update({
        "heartbeat": stamp,
        "lease_until": _stamp(reconciled_at + timedelta(minutes=lease_minutes)),
        "updated_at": stamp,
    })
    _atomic_json(task / "META.json", meta)
    handoff = _read_handoff(task)
    handoff.update({
        "updated_at": stamp,
        "phase": f"orca_{phase.casefold()}",
        "summary": f"Orca dispatch {dispatch_id} observed {observed_status}",
        "next": next_action,
    })
    _atomic_text(task / "HANDOFF.md", _handoff_text(handoff))
    write_board(root, updated_at=stamp)


def command_orca_reconcile(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"active"})
    _authorize_active_claim(args, root, task, meta)
    if args.lease_minutes <= 0:
        raise QueueError("lease minutes must be positive")
    state = _load_orca_state(task, required=True)
    assert state is not None
    dispatch_id = _validate_orca_identifier(args.dispatch_id, label="dispatch id")
    if args.attempt < 1:
        raise QueueError("Orca attempt must be positive")
    current_attempt = int(state["attempt"])
    current_dispatch = state.get("dispatch_id")
    if current_dispatch is None:
        if current_attempt != 0 or args.attempt != 1:
            raise QueueError("first Orca dispatch must be attempt 1")
    elif current_dispatch == dispatch_id:
        if args.attempt != current_attempt:
            raise QueueError("Orca dispatch attempt differs")
        current_status = state.get("observed_dispatch_status")
        if current_status not in ORCA_OBSERVATION_RANK:
            raise QueueError("current Orca dispatch status is invalid")
        if ORCA_OBSERVATION_RANK[args.observed_status] < ORCA_OBSERVATION_RANK[current_status]:
            raise QueueError("stale Orca dispatch observation regresses attempt progress")
        if (
            current_status in ORCA_TERMINAL_STATUSES
            and args.observed_status != current_status
        ):
            raise QueueError("terminal Orca dispatch observation differs")
    else:
        if state.get("phase") != "RECOVERY_REQUIRED" or args.attempt != current_attempt + 1:
            raise QueueError("replacement Orca dispatch requires one recovery generation increment")
    next_action = _clean(args.next_action)
    if not next_action:
        raise QueueError("Orca next action cannot be empty")
    phase_for_status = {
        "pending": ("DISPATCHED", "worker_start"),
        "running": ("WAITING_FOR_WORKER_DONE", "worker_done"),
        "completed": ("SUCCEEDED", "queue_submit"),
        "failed": ("RECOVERY_REQUIRED", "lead_recovery"),
        "cancelled": ("RECOVERY_REQUIRED", "lead_recovery"),
        "blocked": ("RECOVERY_REQUIRED", "lead_recovery"),
    }
    phase, waiting_for = phase_for_status[args.observed_status]
    error = _clean(args.error) or None
    if phase == "RECOVERY_REQUIRED" and error is None:
        raise QueueError("failed Orca reconciliation requires --error")
    candidate = args.candidate_commit
    diff_digest = args.diff_digest
    if phase == "SUCCEEDED":
        if candidate is None or re.fullmatch(r"[0-9a-fA-F]{7,64}", candidate) is None:
            raise QueueError("completed Orca dispatch requires a candidate commit")
        if diff_digest is None or re.fullmatch(r"[0-9a-f]{64}", diff_digest) is None:
            raise QueueError("completed Orca dispatch requires a diff digest")
    else:
        candidate = None
        diff_digest = None
    desired = {
        "dispatch_id": dispatch_id, "attempt": args.attempt, "phase": phase,
        "waiting_for": waiting_for, "next_action": next_action,
        "candidate_commit": candidate, "diff_digest": diff_digest,
        "review_generation": None,
        "observed_dispatch_status": args.observed_status, "last_error": error,
    }
    if all(state.get(key) == value for key, value in desired.items()):
        reconciled_at = _aware(state.get("last_reconciled_at"))
        if reconciled_at is None:
            raise QueueError("Orca reconciliation timestamp is missing")
        _write_orca_reconciliation_receipts(
            root, task, meta,
            dispatch_id=dispatch_id,
            observed_status=args.observed_status,
            phase=phase,
            next_action=next_action,
            reconciled_at=reconciled_at,
            lease_minutes=args.lease_minutes,
        )
        print(meta["id"])
        return
    if (
        current_dispatch == dispatch_id
        and state.get("observed_dispatch_status") in ORCA_TERMINAL_STATUSES
    ):
        raise QueueError("terminal Orca dispatch receipt differs")
    now = _now()
    transitioned = (
        state.get("phase") != phase
        or state.get("observed_dispatch_status") != args.observed_status
        or current_dispatch != dispatch_id
    )
    state.update({
        **desired,
        "last_transition_at": _stamp(now) if transitioned else state["last_transition_at"],
        "last_reconciled_at": _stamp(now),
    })
    _write_orca_state(task, state)
    _write_orca_reconciliation_receipts(
        root, task, meta,
        dispatch_id=dispatch_id,
        observed_status=args.observed_status,
        phase=phase,
        next_action=next_action,
        reconciled_at=now,
        lease_minutes=args.lease_minutes,
    )
    print(meta["id"])


def _require_done_receipt_shape(task: Path) -> set[Path]:
    expected_names = {*REQUIRED_TASK_FILES, "RESULT.md"}
    entries = list(task.iterdir())
    if {
        entry.name for entry in entries
    } != expected_names or any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise QueueError("Done task does not contain exactly four regular receipt files")
    return set(entries)


def _require_git_retained_done(
    root: Path, task: Path, *, allow_untracked: bool = False,
) -> None:
    repository = root.parent.parent.resolve()
    try:
        top = Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()
        if top != repository:
            raise QueueError("queue root is not under the expected Git repository")
        relative = task.resolve().relative_to(repository).as_posix()
        tracked = set(_git(repository, "ls-files", "--", relative).splitlines())
        status = _git(repository, "status", "--porcelain=v1", "--", relative)
        actual = {
            entry.resolve().relative_to(repository).as_posix()
            for entry in _require_done_receipt_shape(task)
        }
        if allow_untracked and not tracked:
            return
        for path in sorted(actual):
            _git(repository, "ls-files", "--error-unmatch", "--", path)
            _git(repository, "cat-file", "-e", f"HEAD:{path}")
    except QueueError:
        raise
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise QueueError(f"cannot verify Done Git retention: {error}") from error
    if not actual or tracked != actual or status:
        raise QueueError("Done task is untracked, dirty, or not wholly Git-retained")


def _delete_done_directory(task: Path) -> None:
    """One exact destructive boundary, isolated for failure injection."""

    shutil.rmtree(task)


def _snapshot_done(task: Path) -> dict[str, tuple[bytes, int]]:
    return {
        entry.name: (entry.read_bytes(), entry.stat().st_mode)
        for entry in task.iterdir()
    }


def _restore_done_snapshot(task: Path, snapshot: dict[str, tuple[bytes, int]]) -> None:
    """Restore exact flat task-record bytes even after a partial recursive delete."""

    task.mkdir(parents=False, exist_ok=True)
    for entry in list(task.iterdir()):
        if entry.is_symlink() or not entry.is_file() or entry.name not in snapshot:
            raise QueueError(f"cannot safely restore unexpected partial entry: {entry}")
    for name, (body, mode) in snapshot.items():
        destination = task / name
        _atomic_bytes(destination, body)
        os.chmod(destination, mode)


def _restore_completed_index(index_path: Path, previous: bytes | None) -> None:
    if previous is None:
        try:
            index_path.unlink()
        except FileNotFoundError:
            pass
    else:
        _atomic_text(index_path, previous.decode("utf-8"))


def _live_referenced_done_ids(root: Path) -> set[str]:
    referenced: set[str] = set()
    for state, _task, meta in _all(root):
        if state == "done":
            continue
        dependencies = meta.get("depends_on")
        if isinstance(dependencies, list):
            referenced.update(str(value) for value in dependencies)
    return referenced


def _completed_entry(task: Path, meta: dict[str, object]) -> dict[str, object]:
    task_id = str(meta.get("id") or "")
    result = _read_fields(task / "RESULT.md", ("result", "completed_at"))
    summary = _clean(result.get("result"))[:512]
    if not summary or _aware(meta.get("completed_at")) is None:
        raise QueueError("Done receipt is incomplete")
    return {
        "id": task_id,
        "legacy_id": meta.get("legacy_id"),
        "fingerprint": str(meta.get("fingerprint") or ""),
        "completed_at": str(meta.get("completed_at")),
        "directory": task.name,
        "result_summary": summary,
        "receipt_sha256": _task_receipt_digest(task),
    }


def _compact_done_tasks(
    root: Path,
    tasks: list[tuple[Path, dict[str, object]]],
    *,
    dry_run: bool,
    allow_untracked: bool = False,
) -> None:
    issues = doctor(root, ignore_mutation_lock=True)
    if issues:
        raise QueueError(f"queue must pass Doctor before compaction: {issues}")
    entries = _load_completed_index(root)
    indexed_ids = {str(entry.get("id") or "") for entry in entries}
    new_entries: list[dict[str, object]] = []
    for task, meta in tasks:
        task_id = str(meta.get("id") or "")
        if task_id in indexed_ids:
            raise QueueError(f"Done task already exists in completed index: {task_id}")
        _require_git_retained_done(
            root, task, allow_untracked=allow_untracked,
        )
        new_entries.append(_completed_entry(task, meta))
    if dry_run:
        print(f"DRY_RUN_ELIGIBLE count={len(tasks)}")
        return
    index_path = root / COMPLETED_INDEX_NAME
    previous = index_path.read_bytes() if index_path.is_file() else None
    board_path = root / "BOARD.md"
    previous_board = board_path.read_bytes() if board_path.is_file() else None
    snapshots = {task: _snapshot_done(task) for task, _meta in tasks}
    try:
        _write_completed_index(root, [*entries, *new_entries])
        for task, _meta in tasks:
            _delete_done_directory(task)
        write_board(root)
    except Exception as error:
        try:
            for task, snapshot in snapshots.items():
                _restore_done_snapshot(task, snapshot)
            _restore_completed_index(index_path, previous)
            if previous_board is not None:
                _atomic_bytes(board_path, previous_board)
        except Exception as restore_error:
            raise QueueError(
                f"Done compaction failed and exact rollback failed: {restore_error}"
            ) from error
        raise QueueError(f"Done compaction failed and was rolled back: {error}") from error


def command_compact_done(args: argparse.Namespace, root: Path) -> None:
    _state, task, meta = find_task(root, args.task, {"done"})
    task_id = str(meta.get("id") or "")
    if task_id in _live_referenced_done_ids(root):
        raise QueueError(f"Done task is still referenced by a live task: {task.name}")
    _compact_done_tasks(root, [(task, meta)], dry_run=args.dry_run)
    if not args.dry_run:
        print(task_id)


def command_prune_done(args: argparse.Namespace, root: Path) -> None:
    if args.keep < 0:
        raise QueueError("keep must be zero or greater")
    done = [
        (task, meta) for state, task, meta in _all(root) if state == "done"
    ]
    done.sort(
        key=lambda item: (
            str(item[1].get("completed_at") or ""),
            str(item[1].get("id") or ""),
        ),
        reverse=True,
    )
    newest_ids = {
        str(meta.get("id") or "") for _task, meta in done[:args.keep]
    }
    referenced_ids = _live_referenced_done_ids(root)
    candidates = [
        (task, meta) for task, meta in done
        if str(meta.get("id") or "") not in newest_ids | referenced_ids
    ]
    protected = sum(
        1 for _task, meta in done[args.keep:]
        if str(meta.get("id") or "") in referenced_ids
    )
    _compact_done_tasks(
        root,
        candidates,
        dry_run=args.dry_run,
        allow_untracked=args.allow_untracked,
    )
    retained = len(done) - len(candidates)
    print(
        f"compacted={len(candidates)} retained_done={retained} "
        f"protected_dependencies={protected}"
    )


def _aware(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def doctor(root: Path, *, ignore_mutation_lock: bool = False) -> list[str]:
    issues: list[str] = []
    try:
        stale_managers = _linked_worktree_managers(root)
    except QueueError as error:
        issues.append(str(error))
        stale_managers = []
    if stale_managers:
        issues.append(f"linked-worktree queue managers present: {stale_managers}")
    required_directories = [state_path(root, state) for state in STATE_PARTS]
    required_directories.extend((root / "inbox", root / "templates"))
    for path in required_directories:
        if not path.is_dir():
            issues.append(f"required directory missing: {path}")
    if not (root / "README.md").is_file():
        issues.append("README.md missing")
    allowed_root = {
        "README.md", "BOARD.md", "inbox", "active", "review", "blocked",
        "waiting", "done", "templates", COMPLETED_INDEX_NAME, ".queue-mutation.lock",
    }
    if root.is_dir():
        for child in root.iterdir():
            if child.name not in allowed_root:
                issues.append(f"unexpected queue root entry: {child}")
    mutation_marker = root / ".queue-mutation.lock"
    if mutation_marker.exists() and not ignore_mutation_lock:
        issues.append(f"queue mutation lock present: {mutation_marker}")
    inbox = root / "inbox"
    if inbox.is_dir():
        for child in inbox.iterdir():
            if child.name not in {"new", "ready"}:
                issues.append(f"unexpected inbox entry: {child}")
    for state in STATE_PARTS:
        base = state_path(root, state)
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.name == ".gitkeep" and child.is_file():
                continue
            if not child.is_dir() or child.name.startswith("."):
                issues.append(f"invalid entry in {state}: {child}")
    tasks = _all(root)
    seen: dict[str, dict[str, Path]] = {key: {} for key in ("id", "legacy_id", "fingerprint")}
    owner_active: dict[str, list[tuple[Path, str]]] = {}
    writer_tasks: list[tuple[Path, str, list[str], list[str]]] = []
    review_reservations: list[tuple[Path, list[str]]] = []
    id_states: dict[str, str] = {}
    dependencies: dict[str, list[str]] = {}
    try:
        completed_entries = _load_completed_index(root)
    except QueueError as error:
        issues.append(str(error))
        completed_entries = []
    index_path = root / COMPLETED_INDEX_NAME
    for entry in completed_entries:
        for key in seen:
            value = str(entry.get(key) or "")
            if value:
                seen[key][value] = index_path
        id_states[str(entry["id"])] = "done"
    for state, task, meta in tasks:
        match = TASK_NAME.fullmatch(task.name)
        if match is None:
            issues.append(f"invalid task directory name: {task}")
        allowed_files = set(REQUIRED_TASK_FILES)
        allowed_files.update({
            "review": {"REVIEW.md"},
            "waiting": {"WAITING.md"},
            "blocked": {"BLOCKED.md"},
            "done": {"RESULT.md"},
        }.get(state, set()))
        if state in {"ready", "waiting", "active", "review", "blocked"}:
            allowed_files.add(ORCA_STATE_NAME)
        for entry in task.iterdir():
            if not entry.is_file() or entry.name not in allowed_files:
                issues.append(f"unexpected task entry for {state}: {entry}")
        for name in REQUIRED_TASK_FILES:
            if not (task / name).is_file():
                issues.append(f"missing {name}: {task}")
        if not meta:
            issues.append(f"invalid metadata: {task}")
            continue
        missing_meta = [key for key in META_KEYS if key not in meta]
        if missing_meta:
            issues.append(f"missing META fields {missing_meta}: {task}")
        if not isinstance(meta.get("schema_version"), int) or isinstance(meta.get("schema_version"), bool):
            issues.append(f"invalid META schema_version type: {task}")
        required_strings = (
            "id", "title", "slug", "priority", "priority_hint", "kind", "risk",
            "state", "created_by", "created_at", "updated_at", "fingerprint",
        )
        for key in required_strings:
            if not isinstance(meta.get(key), str) or not str(meta.get(key)).strip():
                issues.append(f"invalid META {key} type/value: {task}")
        optional_strings = (
            "legacy_id", "owner", "assigned_role", "assigned_agent", "reviewer",
            "completed_at", "parent_task", "lease_until", "heartbeat", "worktree",
            "branch", "domain", "lead_owner",
        )
        for key in optional_strings:
            if meta.get(key) is not None and not isinstance(meta.get(key), str):
                issues.append(f"invalid META {key} type: {task}")
        for key in ("parallelizable", "review_required"):
            if not isinstance(meta.get(key), bool):
                issues.append(f"invalid META {key} type: {task}")
        if meta.get("domain") is not None and meta.get("domain") not in DOMAINS:
            issues.append(f"invalid META domain: {task}")
        raw_dependencies = meta.get("depends_on")
        valid_dependencies = isinstance(raw_dependencies, list)
        if not valid_dependencies:
            issues.append(f"invalid META depends_on type/value: {task}")
            task_dependencies: list[str] = []
        else:
            try:
                task_dependencies = _validate_dependencies(raw_dependencies)
            except QueueError as error:
                issues.append(f"{task}: {error}")
                task_dependencies = []
        raw_scope = meta.get("write_scope")
        valid_scope = isinstance(raw_scope, list) and all(
            isinstance(value, str) for value in raw_scope
        )
        if not valid_scope:
            issues.append(f"invalid META write_scope type/value: {task}")
            task_scope: list[str] = []
        else:
            task_scope = list(raw_scope)
        if meta.get("state") != state:
            issues.append(f"state mismatch: {task}: {meta.get('state')} != {state}")
        if match and meta.get("priority") != match.group(1):
            issues.append(f"priority mismatch: {task}")
        if match and meta.get("id") != match.group(2):
            issues.append(f"task id/name mismatch: {task}")
        for key in seen:
            value = str(meta.get(key) or "")
            if not value:
                if key in {"id", "fingerprint"}:
                    issues.append(f"missing {key}: {task}")
                continue
            prior = seen[key].get(value)
            if prior is not None:
                issues.append(f"duplicate {key} {value}: {prior} and {task}")
            seen[key][value] = task
        task_id = str(meta.get("id") or "")
        if task_id:
            id_states[task_id] = state
            dependencies[task_id] = task_dependencies
        if valid_scope:
            try:
                _validate_scope(task_scope)
            except QueueError as error:
                issues.append(f"{task}: {error}")
        try:
            task_lane = _writer_lane(meta, task_scope)
        except QueueError as error:
            issues.append(f"{task}: {error}")
            task_lane = "shared"
        try:
            task_locks = _resource_locks(meta)
        except QueueError as error:
            issues.append(f"{task}: {error}")
            task_locks = []
        handoff = _read_handoff(task)
        try:
            orca_state = _load_orca_state(task)
        except QueueError as error:
            issues.append(f"invalid Orca state: {task}: {error}")
            orca_state = None
        missing_handoff = [key for key in HANDOFF_KEYS if key not in handoff]
        if missing_handoff:
            issues.append(f"missing HANDOFF fields {missing_handoff}: {task}")
        if state == "review" and task_scope:
            review_reservations.append((task, task_scope))
        if state == "active":
            owner = str(meta.get("owner") or "")
            if not owner or not meta.get("assigned_role") or not meta.get("assigned_agent"):
                issues.append(f"active assignment missing: {task}")
            else:
                owner_active.setdefault(owner, []).append((task, str(meta.get("assigned_role"))))
            routed_lead = meta.get("lead_owner")
            if routed_lead is not None and (
                meta.get("assigned_role") != "lead" or routed_lead != owner
            ):
                issues.append(f"Active Lead routing differs from assignment: {task}")
            lease = _aware(meta.get("lease_until"))
            heartbeat = _aware(meta.get("heartbeat"))
            if lease is None or heartbeat is None:
                issues.append(f"active lease/heartbeat invalid: {task}")
            elif lease < _now():
                issues.append(f"stale lease: {task}: {lease.isoformat()}")
            if not handoff.get("next"):
                issues.append(f"active HANDOFF next missing: {task}")
            claim_digest = meta.get("claim_token_sha256")
            if (
                not isinstance(claim_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", claim_digest) is None
            ):
                issues.append(f"active claim token digest invalid: {task}")
            if task_scope:
                writer_tasks.append((task, task_lane, task_scope, task_locks))
            else:
                issues.append(f"active write scope missing: {task}")
        else:
            for key in ("owner", "lease_until", "heartbeat"):
                if meta.get(key) is not None:
                    issues.append(f"non-active {key} must be null: {task}")
            if "claim_token_sha256" in meta:
                issues.append(f"non-active claim token digest must be absent: {task}")
        if bool(meta.get("review_required")):
            if not meta.get("reviewer"):
                issues.append(f"review-required reviewer missing: {task}")
        if (
            meta.get("reviewer") and meta.get("assigned_agent")
            and meta.get("reviewer") == meta.get("assigned_agent")
        ):
            issues.append(f"reviewer matches implementing agent: {task}")
        if state == "review":
            if meta.get("review_required") is not True:
                issues.append(f"review state must require review: {task}")
            if not meta.get("reviewer"):
                issues.append(f"reviewer missing: {task}")
            review_keys = (
                "result", "changed", "verified", "review_generation",
                "handoff_sha256", "submitted_at",
            )
            review = _read_fields(task / "REVIEW.md", review_keys)
            for key in review_keys[:6]:
                if not review.get(key):
                    issues.append(f"review field {key} missing: {task}")
            generation = review.get("review_generation", "")
            if generation and not re.fullmatch(r"[0-9a-f]{32}", generation):
                issues.append(f"review generation invalid: {task}")
            handoff_digest = review.get("handoff_sha256", "")
            if handoff_digest and not re.fullmatch(r"[0-9a-f]{64}", handoff_digest):
                issues.append(f"review HANDOFF digest invalid: {task}")
            elif handoff_digest:
                try:
                    current_digest = _handoff_snapshot_digest(task)
                except QueueError as error:
                    issues.append(f"review HANDOFF invalid: {task}: {error}")
                else:
                    if not secrets.compare_digest(handoff_digest, current_digest):
                        issues.append(f"review HANDOFF digest mismatch: {task}")
            if review.get("submitted_at") and _aware(review["submitted_at"]) is None:
                issues.append(f"review submitted_at invalid: {task}")
            if (task / "RESULT.md").exists() or meta.get("completed_at") is not None:
                issues.append(f"review task is already marked complete: {task}")
        elif state == "waiting":
            waiting = _read_waiting(task)
            for key in WAITING_KEYS:
                if not waiting.get(key):
                    issues.append(f"waiting field {key} missing: {task}")
            next_check_at = waiting.get("next_check_at")
            if next_check_at and next_check_at != "none" and _aware(next_check_at) is None:
                issues.append(f"waiting next_check_at invalid: {task}")
            if waiting.get("waiting_since") and _aware(waiting["waiting_since"]) is None:
                issues.append(f"waiting since invalid: {task}")
        elif state == "blocked":
            blocked = _read_fields(
                task / "BLOCKED.md", ("reason", "required_action", "resume_condition"),
            )
            for key in ("reason", "required_action", "resume_condition"):
                if not blocked.get(key):
                    issues.append(f"blocked field {key} missing: {task}")
        elif state == "done":
            result = _read_fields(
                task / "RESULT.md", ("result", "changed", "verified", "completed_at"),
            )
            for key in ("result", "changed", "verified", "completed_at"):
                if not result.get(key):
                    issues.append(f"done field {key} missing: {task}")
            completed_at = _aware(meta.get("completed_at"))
            result_completed_at = _aware(result.get("completed_at"))
            if completed_at is None or result_completed_at is None:
                issues.append(f"done completed_at invalid: {task}")
            elif completed_at != result_completed_at:
                issues.append(f"done completed_at mismatch: {task}")
            if (task / "REVIEW.md").exists():
                issues.append(f"done review receipt remains: {task}")
    for owner, assignments in owner_active.items():
        if len(assignments) <= 1:
            continue
        paths = [path for path, _role in assignments]
        if any(role != "lead" for _path, role in assignments):
            issues.append(f"non-Lead owner has multiple active tasks: {owner}: {paths}")
        elif len(assignments) > LEAD_WIP_LIMIT:
            issues.append(f"Lead WIP limit exceeded: {owner}: {paths}")
    if len(writer_tasks) > WRITER_LIMIT:
        issues.append(
            f"production writer limit exceeded: {[item[0] for item in writer_tasks]}"
        )
    for index, (task, lane, scope, locks) in enumerate(writer_tasks):
        for other, other_lane, other_scope, other_locks in writer_tasks[index + 1:]:
            if _scopes_overlap(scope, other_scope):
                issues.append(f"active write scope conflict: {task} and {other}")
            if lane == "shared" or other_lane == "shared":
                issues.append(
                    f"active writer lane conflict: {task} and {other}: "
                    f"{lane}/{other_lane}"
                )
            shared_locks = sorted(set(locks).intersection(other_locks))
            if shared_locks:
                issues.append(
                    f"active resource lock conflict: {task} and {other}: {shared_locks}"
                )
        for review_task, review_scope in review_reservations:
            if _scopes_overlap(scope, review_scope):
                issues.append(
                    f"active write scope reserved by review: {task} and {review_task}"
                )
    for task_id, values in dependencies.items():
        for dependency in values:
            if dependency == task_id:
                issues.append(f"self dependency: {task_id}")
            elif dependency not in id_states:
                issues.append(f"missing dependency: {task_id} -> {dependency}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            issues.append(f"dependency cycle includes: {task_id}")
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in dependencies.get(task_id, []):
            if dependency in dependencies:
                visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in dependencies:
        visit(task_id)
    board = root / "BOARD.md"
    if not board.is_file():
        issues.append("BOARD.md missing")
    else:
        board_text = board.read_text(encoding="utf-8")
        updated = re.search(r"^updated_at: (.+)$", board_text, re.MULTILINE)
        try:
            expected_board = _board(root, updated_at=updated.group(1)) if updated else None
        except (OSError, TypeError, ValueError, QueueError) as error:
            issues.append(f"BOARD projection failed: {error}")
            expected_board = None
        if updated is None or expected_board is None or board_text != expected_board:
            issues.append("BOARD.md is stale")
    return issues


def command_doctor(args: argparse.Namespace, root: Path) -> None:
    issues = doctor(root, ignore_mutation_lock=args.fix_board)
    if args.fix_board and any(issue in {"BOARD.md missing", "BOARD.md is stale"} for issue in issues):
        write_board(root)
        issues = doctor(root, ignore_mutation_lock=True)
    if issues:
        for issue in issues:
            print(f"ERROR {issue}")
        raise QueueError(f"doctor found {len(issues)} issue(s)")
    print("OK")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Atomic file-backed request queue manager")
    parser.add_argument("--root", help="explicit request queue root")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    status = commands.add_parser("status")
    status.add_argument("--compact", action="store_true")
    status.add_argument("--lead-owner")
    discover = commands.add_parser("discover")
    for name in ("title", "discovered_by", "source_task", "fingerprint", "symptom", "evidence", "impact", "suspected_scope", "reproduce"):
        discover.add_argument(f"--{name.replace('_', '-')}", required=True)
    discover.add_argument("--priority-hint", choices=tuple(PRIORITY_ORDER), default="P2")
    discover.add_argument("--domain", choices=DOMAINS, default="shared")
    discover.add_argument("--lead-owner")
    triage = commands.add_parser("triage")
    triage.add_argument("task")
    triage.add_argument("--priority", choices=tuple(PRIORITY_ORDER), required=True)
    triage.add_argument("--risk", choices=("low", "medium", "high"), required=True)
    triage.add_argument("--write-scope", action="append", required=True)
    triage.add_argument("--writer-lane", choices=WRITER_LANES)
    triage.add_argument("--resource-lock", action="append", default=[])
    triage.add_argument("--depends-on", action="append", default=[])
    for name in ("problem", "evidence", "done_when", "verify"):
        triage.add_argument(f"--{name.replace('_', '-')}", required=True)
    triage.add_argument("--allow", action="append", required=True)
    triage.add_argument("--deny", action="append", required=True)
    triage.add_argument("--parallelizable", action="store_true")
    triage.add_argument("--review-required", action="store_true")
    triage.add_argument("--reviewer")
    triage.add_argument("--domain", choices=DOMAINS)
    triage.add_argument("--lead-owner")
    retarget = commands.add_parser("retarget")
    retarget.add_argument("task")
    retarget.add_argument("--write-scope", action="append")
    retarget.add_argument("--depends-on", action="append")
    retarget.add_argument("--writer-lane", choices=WRITER_LANES)
    retarget.add_argument("--resource-lock", action="append")
    retarget.add_argument("--repair-joined", action="store_true")
    claim = commands.add_parser("claim")
    claim.add_argument("task")
    claim.add_argument("--owner", required=True)
    claim.add_argument("--role", choices=("worker", "lead"), default="worker")
    claim.add_argument("--domain", choices=DOMAINS)
    claim.add_argument("--lease-minutes", type=int, default=60)
    claim.add_argument("--next", required=True)
    recover_expired_active = commands.add_parser("recover-expired-active")
    recover_expired_active.add_argument("task")
    recover_expired_active.add_argument("--coordinator", required=True)
    recover_expired_active.add_argument("--expected-owner", required=True)
    recover_expired_active.add_argument("--expected-updated-at", required=True)
    recover_expired_active.add_argument("--expected-lease-until", required=True)
    recover_expired_active.add_argument("--expected-claim-token-sha256", required=True)
    recover_expired_active.add_argument("--decision-basis", required=True)
    recover_expired_active.add_argument("--next", required=True)
    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("task")
    checkpoint.add_argument("--owner", required=True)
    checkpoint.add_argument("--claim-token")
    checkpoint.add_argument("--expected-generation")
    checkpoint.add_argument("--adopt-legacy-claim", action="store_true")
    checkpoint.add_argument("--lease-minutes", type=int, default=60)
    checkpoint.add_argument("--require-review", action="store_true")
    checkpoint.add_argument("--reviewer")
    for key in HANDOFF_KEYS:
        if key != "updated_at":
            checkpoint.add_argument(f"--{key.replace('_', '-')}")
    submit = commands.add_parser("submit")
    submit.add_argument("task")
    submit.add_argument("--owner", required=True)
    submit.add_argument("--claim-token")
    submit.add_argument("--expected-generation")
    submit.add_argument("--adopt-legacy-claim", action="store_true")
    submit.add_argument("--result", required=True)
    submit.add_argument("--changed", required=True)
    submit.add_argument("--verified", required=True)
    submit.add_argument("--review", action="store_true")
    submit.add_argument("--reviewer")
    review_pass = commands.add_parser("review-pass")
    review_pass.add_argument("task")
    review_pass.add_argument("--reviewer", required=True)
    review_pass.add_argument("--review-generation", required=True)
    review_pass.add_argument("--decision-basis", required=True)
    review_fail = commands.add_parser("review-fail")
    review_fail.add_argument("task")
    review_fail.add_argument("--reviewer", required=True)
    review_fail.add_argument("--review-generation", required=True)
    review_fail.add_argument("--decision-basis", required=True)
    review_fail.add_argument("--next", required=True)
    review_recover = commands.add_parser("review-recover")
    review_recover.add_argument("task")
    review_recover.add_argument("--reviewer", required=True)
    review_recover.add_argument("--review-generation", required=True)
    review_recover.add_argument("--decision-basis", required=True)
    review_recover.add_argument("--next", required=True)
    reopen = commands.add_parser("reopen")
    reopen.add_argument("task")
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--next", required=True)
    block = commands.add_parser("block")
    block.add_argument("task")
    block.add_argument("--owner", required=True)
    block.add_argument("--claim-token")
    block.add_argument("--expected-generation")
    block.add_argument("--adopt-legacy-claim", action="store_true")
    block.add_argument("--reason", required=True)
    block.add_argument("--required-action", required=True)
    block.add_argument("--resume-condition", required=True)
    unblock = commands.add_parser("unblock")
    unblock.add_argument("task")
    unblock.add_argument("--next", required=True)
    route = commands.add_parser("route")
    route.add_argument("task")
    route.add_argument("--domain", choices=DOMAINS, required=True)
    route.add_argument("--lead-owner", required=True)
    route.add_argument("--owner")
    route.add_argument("--claim-token")
    route.add_argument("--expected-generation")
    route.add_argument("--adopt-legacy-claim", action="store_true")
    route.add_argument("--next", required=True)
    release = commands.add_parser("release")
    release.add_argument("task")
    release.add_argument("--owner", required=True)
    release.add_argument("--claim-token")
    release.add_argument("--expected-generation")
    release.add_argument("--adopt-legacy-claim", action="store_true")
    release.add_argument("--reason", required=True)
    release.add_argument("--next", required=True)
    wait = commands.add_parser("wait")
    wait.add_argument("task")
    wait.add_argument("--owner")
    wait.add_argument("--claim-token")
    wait.add_argument("--expected-generation")
    wait.add_argument("--adopt-legacy-claim", action="store_true")
    wait.add_argument("--reason", required=True)
    wait.add_argument("--resume-condition", required=True)
    wait.add_argument("--next-check-at", default="none")
    resume_waiting = commands.add_parser("resume-waiting")
    resume_waiting.add_argument("task")
    resume_waiting.add_argument("--decision-basis", required=True)
    resume_waiting.add_argument("--next", required=True)
    orca_bind = commands.add_parser("orca-bind")
    orca_bind.add_argument("task")
    orca_bind.add_argument("--owner", required=True)
    orca_bind.add_argument("--claim-token")
    orca_bind.add_argument("--expected-generation")
    orca_bind.add_argument("--adopt-legacy-claim", action="store_true")
    orca_bind.add_argument("--run-id", required=True)
    orca_bind.add_argument("--orca-task-id", required=True)
    orca_bind.add_argument("--domain", choices=DOMAINS)
    orca_bind.add_argument("--next-action", required=True)
    orca_bind.add_argument("--lease-minutes", type=int, default=60)
    orca_reconcile = commands.add_parser("orca-reconcile")
    orca_reconcile.add_argument("task")
    orca_reconcile.add_argument("--owner", required=True)
    orca_reconcile.add_argument("--claim-token")
    orca_reconcile.add_argument("--expected-generation")
    orca_reconcile.add_argument("--adopt-legacy-claim", action="store_true")
    orca_reconcile.add_argument("--dispatch-id", required=True)
    orca_reconcile.add_argument("--attempt", type=int, required=True)
    orca_reconcile.add_argument("--observed-status", choices=ORCA_OBSERVED_STATUSES, required=True)
    orca_reconcile.add_argument("--candidate-commit")
    orca_reconcile.add_argument("--diff-digest")
    orca_reconcile.add_argument("--error")
    orca_reconcile.add_argument("--next-action", required=True)
    orca_reconcile.add_argument("--lease-minutes", type=int, default=60)
    compact_done = commands.add_parser("compact-done")
    compact_done.add_argument("task")
    compact_done.add_argument("--dry-run", action="store_true")
    prune_done = commands.add_parser("prune-done")
    prune_done.add_argument("--keep", type=int, default=20)
    prune_done.add_argument("--dry-run", action="store_true")
    prune_done.add_argument("--allow-untracked", action="store_true")
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--fix-board", action="store_true")
    return parser


COMMANDS = {
    "init": command_init, "status": command_status, "discover": command_discover,
    "triage": command_triage, "retarget": command_retarget,
    "claim": command_claim, "recover-expired-active": command_recover_expired_active,
    "checkpoint": command_checkpoint, "submit": command_submit,
    "review-pass": command_review_pass, "review-fail": command_review_fail,
    "review-recover": command_review_recover,
    "reopen": command_reopen, "block": command_block,
    "unblock": command_unblock, "route": command_route,
    "release": command_release, "wait": command_wait,
    "resume-waiting": command_resume_waiting,
    "orca-bind": command_orca_bind, "orca-reconcile": command_orca_reconcile,
    "compact-done": command_compact_done,
    "prune-done": command_prune_done,
    "doctor": command_doctor,
}

MUTATING_COMMANDS = {
    "init", "discover", "triage", "retarget", "claim", "recover-expired-active",
    "checkpoint", "submit",
    "review-pass", "review-fail", "reopen", "block", "unblock",
    "review-recover", "route", "release", "wait", "resume-waiting",
    "orca-bind", "orca-reconcile", "compact-done", "prune-done",
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = resolve_queue_root(args.root)
        _ensure_canonical_manager(root)
        mutating = (
            args.command in MUTATING_COMMANDS
            and not (
                args.command in {"compact-done", "prune-done"}
                and args.dry_run
            )
        ) or (
            args.command == "doctor" and args.fix_board
        )
        if mutating:
            with mutation_lock(root):
                COMMANDS[args.command](args, root)
        else:
            COMMANDS[args.command](args, root)
    except (OSError, QueueError, ValueError) as error:
        print(f"request_queue: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
