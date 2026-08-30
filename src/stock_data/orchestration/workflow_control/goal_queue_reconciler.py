"""Pure, proposal-only reconciliation of an explicit Goal revision and Queue.

The canonical request Queue remains owned by ``scripts/request_queue.py``.
This module may read a complete Queue snapshot and produce content-addressed
proposals, but it deliberately exposes no Queue writer or lifecycle command.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
from types import MappingProxyType
from typing import Any, Mapping, Sequence


_SHA256 = re.compile(r"[0-9a-f]{64}")
_TASK_ID = re.compile(r"RQ-\d{8}T\d{6}-[0-9A-F]{4}")
_TASK_DIRECTORY = re.compile(
    r"(P[012])-(RQ-\d{8}T\d{6}-[0-9A-F]{4})-([^\\/\s]+)"
)
_GOAL_ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_TEXT_LIMIT = 512
_META_LIMIT = 128 * 1024
_COMPLETED_INDEX_LIMIT = 8 * 1024 * 1024
_STATE_DIRECTORIES = (
    ("new", ("inbox", "new")),
    ("ready", ("inbox", "ready")),
    ("waiting", ("waiting",)),
    ("active", ("active",)),
    ("review", ("review",)),
    ("blocked", ("blocked",)),
    ("done", ("done",)),
)
_STRUCTURAL_FIELDS = frozenset(
    {
        "depends_on",
        "domain",
        "kind",
        "lead_owner",
        "parallelizable",
        "priority",
        "resource_locks",
        "review_required",
        "risk",
        "title",
        "worker_profile",
        "reviewer_profile",
        "write_scope",
        "writer_lane",
    }
)
_COMPLETED_ENTRY_FIELDS = frozenset(
    {
        "completed_at",
        "directory",
        "fingerprint",
        "id",
        "legacy_id",
        "receipt_sha256",
        "result_summary",
    }
)
_REQUIRED_META_FIELDS = frozenset(
    {
        "assigned_agent",
        "assigned_role",
        "branch",
        "completed_at",
        "created_at",
        "created_by",
        "depends_on",
        "fingerprint",
        "heartbeat",
        "id",
        "kind",
        "lease_until",
        "legacy_id",
        "owner",
        "parallelizable",
        "parent_task",
        "priority",
        "priority_hint",
        "review_required",
        "reviewer",
        "risk",
        "schema_version",
        "slug",
        "state",
        "title",
        "updated_at",
        "worktree",
        "write_scope",
    }
)
_TASK_REQUIRED_FILES = frozenset({"META.json", "TASK.md", "HANDOFF.md"})
_TASK_STATE_FILES = {
    "review": {"REVIEW.md"},
    "waiting": {"WAITING.md"},
    "blocked": {"BLOCKED.md"},
    "done": {"RESULT.md"},
}
_DOMAINS = frozenset(
    {
        "data",
        "backtest",
        "gui",
        "infra",
        "broker",
        "research",
        "integration",
        "shared",
    }
)
_GOAL_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
_QUEUE_PRIORITIES = frozenset({"P0", "P1", "P2"})
_WRITER_LANES = frozenset({"gui", "data", "backtest", "shared"})
_DISCOVERY_INTAKE_ROLES = frozenset(
    {"coordinator", "lead", "goal_planner", "runtime_monitor"}
)
_DISCOVERY_REPORTER_ROLES = frozenset(
    {"user", "worker", "reviewer", "lead", "goal_planner", "runtime_monitor"}
)
_COMPLEXITIES = frozenset({"small", "standard", "complex", "critical"})
_RISKS = frozenset({"untriaged", "low", "medium", "high", "critical"})
_MODEL_PROFILES = frozenset({"fast", "balanced", "strong", "critical"})
_RESOURCE_LOCK = re.compile(r"[a-z0-9][a-z0-9._:/-]*")
_WINDOWS_PATH_SEMANTICS = os.name == "nt"
_WINDOWS_RESERVED_STEMS = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
    }
)
_WINDOWS_NUMBERED_DEVICE = re.compile(
    r"(?:com|lpt)[1-9\u00b9\u00b2\u00b3]", re.IGNORECASE
)


class ReconciliationError(RuntimeError):
    """Base class for fail-closed reconciliation failures."""


class ReconciliationValidationError(ReconciliationError, ValueError):
    """A Goal, Queue snapshot, or proposal is malformed."""


class AmbiguousGoalRevision(ReconciliationValidationError):
    """The caller did not provide one explicit, unambiguous Goal meaning."""


class IncompleteQueueSnapshot(ReconciliationValidationError):
    """The Queue view does not cover every canonical live and historical state."""


class StaleQueueGeneration(ReconciliationError):
    """The proposal was calculated from a Queue generation that is no longer current."""


class DuplicateProposalApplication(ReconciliationError):
    """The same content-addressed proposal was already applied."""


class NonMutatingProposal(ReconciliationError):
    """A NOOP proposal was incorrectly presented for lifecycle application."""


class QueueState(str, Enum):
    NEW = "new"
    READY = "ready"
    WAITING = "waiting"
    ACTIVE = "active"
    REVIEW = "review"
    BLOCKED = "blocked"
    DONE = "done"


class ReconciliationAction(str, Enum):
    CREATE = "CREATE"
    AMEND = "AMEND"
    REPLAN = "REPLAN"
    INVALIDATE_REVIEW = "INVALIDATE_REVIEW"
    REOPEN = "REOPEN"
    LINK = "LINK"
    NOOP = "NOOP"


def _required_text(value: object, field: str, *, limit: int = _TEXT_LIMIT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReconciliationValidationError(f"{field} must be a non-empty string")
    if len(value) > limit or "\x00" in value:
        raise ReconciliationValidationError(f"{field} is outside its bounded text contract")
    return value


def _canonical_json(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ReconciliationValidationError("value is not canonical JSON data") from error
    return encoded


def _canonical_mapping(value: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReconciliationValidationError(f"{field} must be a mapping")
    decoded = json.loads(_canonical_json(dict(value)))
    if not isinstance(decoded, dict):
        raise ReconciliationValidationError(f"{field} must encode an object")
    return MappingProxyType(decoded)


def _validated_scope_entries(
    values: Sequence[str],
    *,
    require_sorted: bool,
) -> tuple[str, ...]:
    """Validate repository-relative scopes against Windows alias semantics."""

    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ReconciliationValidationError("write_scope must be a sequence")
    normalized: list[str] = []
    windows_keys: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ReconciliationValidationError("write_scope entry is not text")
        pure = PurePosixPath(value)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or "\\" in value
            or any(character in value for character in '*?[],;<>":|')
            or any(ord(character) < 32 for character in value)
        ):
            raise ReconciliationValidationError(
                "write_scope must contain exact repository-relative paths"
            )
        canonical = pure.as_posix().strip("/")
        if not canonical or canonical == "." or canonical != value:
            raise ReconciliationValidationError(
                "write_scope entry is not canonical"
            )
        for component in pure.parts:
            if component.endswith((".", " ")):
                raise ReconciliationValidationError(
                    "write_scope component has a Windows trailing-dot/space alias"
                )
            device_stem = component.split(".", 1)[0].casefold()
            if (
                device_stem in _WINDOWS_RESERVED_STEMS
                or _WINDOWS_NUMBERED_DEVICE.fullmatch(device_stem) is not None
            ):
                raise ReconciliationValidationError(
                    "write_scope component is a reserved Windows device name"
                )
        windows_key = "/".join(part.casefold() for part in pure.parts)
        if windows_key in windows_keys:
            raise ReconciliationValidationError(
                "write_scope contains a Windows case alias collision"
            )
        windows_keys.add(windows_key)
        normalized.append(canonical)
    if require_sorted and normalized != sorted(normalized):
        raise ReconciliationValidationError(
            "write_scope must be sorted and unique"
        )
    return tuple(normalized)


def _validate_existing_windows_scope_case(
    repository_root: Path,
    values: Sequence[str],
) -> None:
    """Reject lexical aliases of existing names on case-insensitive Windows."""

    if not _WINDOWS_PATH_SEMANTICS:
        return
    for value in values:
        current = repository_root
        for component in PurePosixPath(value).parts:
            if not current.is_dir():
                break
            try:
                matches = [
                    entry.name
                    for entry in os.scandir(current)
                    if entry.name.casefold() == component.casefold()
                ]
            except OSError as error:
                raise ReconciliationValidationError(
                    "write_scope existing path case cannot be verified"
                ) from error
            if not matches:
                break
            if len(matches) != 1 or matches[0] != component:
                raise ReconciliationValidationError(
                    "write_scope uses a Windows case alias of an existing path"
                )
            current = current / matches[0]


def _validate_structural_fields(
    fields: Mapping[str, Any],
    *,
    queue_meta: bool = False,
) -> None:
    domain = fields.get("domain")
    if domain is not None and (
        not isinstance(domain, str) or domain not in _DOMAINS
    ):
        raise ReconciliationValidationError("domain is not canonical")
    if "priority" in fields:
        allowed = _QUEUE_PRIORITIES if queue_meta else _GOAL_PRIORITIES
        if (
            not isinstance(fields["priority"], str)
            or fields["priority"] not in allowed
        ):
            raise ReconciliationValidationError("priority is not canonical")
    if "depends_on" in fields:
        dependencies = fields["depends_on"]
        if not isinstance(dependencies, list) or any(
            not isinstance(value, str) or _TASK_ID.fullmatch(value) is None
            for value in dependencies
        ):
            raise ReconciliationValidationError(
                "depends_on must contain exact Queue task ids"
            )
        if len(dependencies) != len(set(dependencies)):
            raise ReconciliationValidationError("depends_on must be unique")
        if not queue_meta and dependencies != sorted(dependencies):
            raise ReconciliationValidationError(
                "Goal depends_on must be canonically sorted"
            )
    lead_owner = fields.get("lead_owner")
    if lead_owner is not None:
        _required_text(lead_owner, "lead_owner", limit=128)
    if "resource_locks" in fields:
        locks = fields["resource_locks"]
        if not isinstance(locks, list) or any(
            not isinstance(value, str) or _RESOURCE_LOCK.fullmatch(value) is None
            for value in locks
        ):
            raise ReconciliationValidationError("resource_locks are malformed")
        if locks != sorted(set(locks)):
            raise ReconciliationValidationError(
                "resource_locks must be sorted and unique"
            )
    for name in ("parallelizable", "review_required"):
        if name in fields and not isinstance(fields[name], bool):
            raise ReconciliationValidationError(f"{name} must be boolean")
    if "risk" in fields and (
        not isinstance(fields["risk"], str) or fields["risk"] not in _RISKS
    ):
        raise ReconciliationValidationError("risk is not canonical")
    if "kind" in fields:
        _required_text(fields["kind"], "kind", limit=128)
    if "title" in fields:
        _required_text(fields["title"], "title")
    for name in ("worker_profile", "reviewer_profile"):
        value = fields.get(name)
        if value is not None and (
            not isinstance(value, str) or value not in _MODEL_PROFILES
        ):
            raise ReconciliationValidationError(f"{name} is not canonical")
    if "write_scope" in fields:
        values = fields["write_scope"]
        if not isinstance(values, list):
            raise ReconciliationValidationError("write_scope must be a list")
        _validated_scope_entries(values, require_sorted=True)
    writer_lane = fields.get("writer_lane")
    if writer_lane is not None and (
        not isinstance(writer_lane, str) or writer_lane not in _WRITER_LANES
    ):
        raise ReconciliationValidationError("writer_lane is not canonical")


def _digest(namespace: str, value: object) -> str:
    return sha256(f"{namespace}\n{_canonical_json(value)}".encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GoalItem:
    item_id: str
    fingerprint: str
    title: str
    desired_fields: Mapping[str, Any]
    linked_task_id: str | None = None
    preferred_lead: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or _GOAL_ITEM_ID.fullmatch(self.item_id) is None:
            raise ReconciliationValidationError("Goal item id is malformed")
        _required_text(self.fingerprint, "Goal fingerprint")
        _required_text(self.title, "Goal item title")
        fields = _canonical_mapping(self.desired_fields, "desired_fields")
        unknown = set(fields) - _STRUCTURAL_FIELDS
        if unknown:
            raise ReconciliationValidationError(
                f"desired_fields contains unsupported Queue fields: {sorted(unknown)}"
            )
        _validate_structural_fields(fields)
        if "title" in fields and fields["title"] != self.title:
            raise AmbiguousGoalRevision("title and desired_fields.title disagree")
        if self.linked_task_id is not None and _TASK_ID.fullmatch(self.linked_task_id) is None:
            raise ReconciliationValidationError("linked_task_id is malformed")
        if self.preferred_lead is not None:
            _required_text(self.preferred_lead, "preferred_lead", limit=128)
        object.__setattr__(self, "desired_fields", fields)
        _ = self.effective_fields

    @property
    def effective_fields(self) -> Mapping[str, Any]:
        values = dict(self.desired_fields)
        values.setdefault("title", self.title)
        if self.preferred_lead is not None:
            existing = values.get("lead_owner", self.preferred_lead)
            if existing != self.preferred_lead:
                raise AmbiguousGoalRevision("preferred_lead and desired lead_owner disagree")
            values["lead_owner"] = self.preferred_lead
        return MappingProxyType(values)


@dataclass(frozen=True, slots=True)
class GoalRevision:
    revision: str
    items: tuple[GoalItem, ...]
    explicit_user_intent: bool = True

    def __post_init__(self) -> None:
        _required_text(self.revision, "Goal revision", limit=128)
        if self.explicit_user_intent is not True:
            raise AmbiguousGoalRevision("Goal edits require explicit user intent")
        if not isinstance(self.items, tuple) or not self.items:
            raise AmbiguousGoalRevision("Goal revision must contain at least one explicit item")
        if any(not isinstance(item, GoalItem) for item in self.items):
            raise ReconciliationValidationError("Goal revision items are malformed")
        ids = [item.item_id for item in self.items]
        fingerprints = [item.fingerprint for item in self.items]
        if len(ids) != len(set(ids)):
            raise AmbiguousGoalRevision("Goal revision repeats an item id")
        if len(fingerprints) != len(set(fingerprints)):
            raise AmbiguousGoalRevision("Goal revision repeats a Queue fingerprint")

    @property
    def change_digest(self) -> str:
        return _digest(
            "goal-change/v1",
            {
                "items": [
                    {
                        "desired_fields": dict(item.effective_fields),
                        "fingerprint": item.fingerprint,
                        "item_id": item.item_id,
                        "linked_task_id": item.linked_task_id,
                    }
                    for item in sorted(self.items, key=lambda value: value.item_id)
                ],
                "revision": self.revision,
            },
        )


@dataclass(frozen=True, slots=True)
class QueueTaskSnapshot:
    task_id: str
    state: QueueState
    fingerprint: str
    generation: str
    fields: Mapping[str, Any]
    goal_item_ids: tuple[str, ...] = ()
    review_generation: str | None = None
    compacted_title_key: str | None = None

    def __post_init__(self) -> None:
        if _TASK_ID.fullmatch(self.task_id) is None:
            raise ReconciliationValidationError("Queue task id is malformed")
        if not isinstance(self.state, QueueState):
            raise ReconciliationValidationError("Queue task state is malformed")
        _required_text(self.fingerprint, "Queue fingerprint")
        if _SHA256.fullmatch(self.generation) is None:
            raise ReconciliationValidationError("Queue task generation is malformed")
        fields = _canonical_mapping(self.fields, "Queue fields")
        unknown = set(fields) - _STRUCTURAL_FIELDS
        if unknown:
            raise ReconciliationValidationError(
                f"Queue fields contain unsupported values: {sorted(unknown)}"
            )
        if tuple(sorted(set(self.goal_item_ids))) != self.goal_item_ids:
            raise ReconciliationValidationError("goal_item_ids must be unique and sorted")
        if any(_GOAL_ITEM_ID.fullmatch(value) is None for value in self.goal_item_ids):
            raise ReconciliationValidationError("Queue Goal linkage is malformed")
        if self.review_generation is not None:
            _required_text(self.review_generation, "review_generation", limit=128)
        if self.compacted_title_key is not None:
            if self.state is not QueueState.DONE:
                raise ReconciliationValidationError(
                    "only Done tasks may carry compacted title semantics"
                )
            _required_text(self.compacted_title_key, "compacted_title_key")
        object.__setattr__(self, "fields", fields)

    @property
    def lead_owner(self) -> str | None:
        value = self.fields.get("lead_owner")
        return value if isinstance(value, str) and value else None

    @property
    def is_compacted(self) -> bool:
        return self.compacted_title_key is not None


@dataclass(frozen=True, slots=True)
class QueueInventory:
    tasks: tuple[QueueTaskSnapshot, ...]
    states_present: frozenset[QueueState]

    def __post_init__(self) -> None:
        if self.states_present != frozenset(QueueState):
            missing = sorted(state.value for state in frozenset(QueueState) - self.states_present)
            raise IncompleteQueueSnapshot(f"Queue snapshot is incomplete; missing states: {missing}")
        if any(not isinstance(task, QueueTaskSnapshot) for task in self.tasks):
            raise ReconciliationValidationError("Queue inventory contains malformed tasks")
        ids = [task.task_id for task in self.tasks]
        fingerprints = [task.fingerprint for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise IncompleteQueueSnapshot("Queue snapshot repeats a task id")
        if len(fingerprints) != len(set(fingerprints)):
            raise IncompleteQueueSnapshot("Queue snapshot repeats a task fingerprint")

    @property
    def generation(self) -> str:
        return _digest(
            "queue-inventory/v1",
            [
                {
                    "fields": dict(task.fields),
                    "fingerprint": task.fingerprint,
                    "generation": task.generation,
                    "goal_item_ids": task.goal_item_ids,
                    "review_generation": task.review_generation,
                    "compacted_title_key": task.compacted_title_key,
                    "state": task.state.value,
                    "task_id": task.task_id,
                }
                for task in sorted(self.tasks, key=lambda value: value.task_id)
            ],
        )

    def by_id(self) -> dict[str, QueueTaskSnapshot]:
        return {task.task_id: task for task in self.tasks}


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    current: Any
    desired: Any


@dataclass(frozen=True, slots=True)
class ReconciliationProposal:
    goal_revision: str
    goal_item_id: str
    goal_fingerprint: str
    target_queue_id: str | None
    action: ReconciliationAction
    reason: str
    changed_fields: tuple[FieldChange, ...]
    affected_lead: str | None
    current_queue_generation: str
    current_task_generation: str | None
    goal_change_digest: str

    @property
    def proposal_id(self) -> str:
        return _digest(
            "goal-queue-proposal/v1",
            {
                "action": self.action.value,
                "affected_lead": self.affected_lead,
                "changed_fields": [
                    {"current": item.current, "desired": item.desired, "field": item.field}
                    for item in self.changed_fields
                ],
                "current_queue_generation": self.current_queue_generation,
                "current_task_generation": self.current_task_generation,
                "goal_change_digest": self.goal_change_digest,
                "goal_fingerprint": self.goal_fingerprint,
                "goal_item_id": self.goal_item_id,
                "goal_revision": self.goal_revision,
                "target_queue_id": self.target_queue_id,
            },
        )


@dataclass(frozen=True, slots=True)
class ProposalApplicationReservation:
    proposal_id: str
    reservation_id: str
    pm_session_id: str
    queue_generation: str
    goal_change_digest: str
    write_scope: tuple[str, ...]
    queue_mutated: bool = False


class ProposalApplicationLedger:
    """Durable PM-owned CAS ledger; it never writes the canonical Queue."""

    def __init__(self, database_path: str | Path, *, pm_session_id: str) -> None:
        _required_text(pm_session_id, "pm_session_id", limit=128)
        self.database_path = Path(database_path)
        self.pm_session_id = pm_session_id
        if str(self.database_path) == ":memory:":
            raise ReconciliationValidationError(
                "proposal application ledger must be durable"
            )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS proposal_application_reservations (
                    proposal_id TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL UNIQUE,
                    pm_session_id TEXT NOT NULL,
                    queue_generation TEXT NOT NULL,
                    goal_change_digest TEXT NOT NULL,
                    write_scope_json TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    queue_mutated INTEGER NOT NULL CHECK (queue_mutated = 0)
                ) STRICT
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def reserve(
        self,
        proposal: ReconciliationProposal,
        *,
        proposal_json: str,
        write_scope: tuple[str, ...],
    ) -> ProposalApplicationReservation:
        scope_json = _canonical_json(write_scope)
        reservation_id = _digest(
            "goal-queue-application-reservation/v1",
            {
                "pm_session_id": self.pm_session_id,
                "proposal_id": proposal.proposal_id,
                "write_scope": write_scope,
            },
        )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO proposal_application_reservations (
                        proposal_id, reservation_id, pm_session_id,
                        queue_generation, goal_change_digest, write_scope_json,
                        proposal_json, queue_mutated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        proposal.proposal_id,
                        reservation_id,
                        self.pm_session_id,
                        proposal.current_queue_generation,
                        proposal.goal_change_digest,
                        scope_json,
                        proposal_json,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise DuplicateProposalApplication(
                    "proposal already has a durable application reservation"
                ) from error
        return ProposalApplicationReservation(
            proposal_id=proposal.proposal_id,
            reservation_id=reservation_id,
            pm_session_id=self.pm_session_id,
            queue_generation=proposal.current_queue_generation,
            goal_change_digest=proposal.goal_change_digest,
            write_scope=write_scope,
        )


def _proposal_json(proposal: ReconciliationProposal) -> str:
    return _canonical_json(
        {
            "action": proposal.action.value,
            "affected_lead": proposal.affected_lead,
            "changed_fields": [
                {"current": item.current, "desired": item.desired, "field": item.field}
                for item in proposal.changed_fields
            ],
            "current_queue_generation": proposal.current_queue_generation,
            "current_task_generation": proposal.current_task_generation,
            "goal_change_digest": proposal.goal_change_digest,
            "goal_fingerprint": proposal.goal_fingerprint,
            "goal_item_id": proposal.goal_item_id,
            "goal_revision": proposal.goal_revision,
            "proposal_id": proposal.proposal_id,
            "reason": proposal.reason,
            "target_queue_id": proposal.target_queue_id,
        }
    )


def _canonical_write_scope(
    values: Sequence[str], *, repository_root: str | Path,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ReconciliationValidationError("write scope must be a sequence")
    root = Path(repository_root).absolute()
    if not root.is_dir():
        raise ReconciliationValidationError("write scope repository root is invalid")
    for boundary in (root, *root.parents):
        try:
            reparse = _is_reparse_path(boundary)
        except IncompleteQueueSnapshot as error:
            raise ReconciliationValidationError(
                "write scope repository root ancestry is unreadable"
            ) from error
        if reparse:
            raise ReconciliationValidationError(
                "write scope repository root ancestry contains a junction/reparse point"
            )
    try:
        canonical_root = root.resolve(strict=True)
    except OSError as error:
        raise ReconciliationValidationError(
            "write scope repository root is invalid"
        ) from error
    normalized = _validated_scope_entries(values, require_sorted=False)
    _validate_existing_windows_scope_case(canonical_root, normalized)
    for canonical in normalized:
        pure = PurePosixPath(canonical)
        candidate = canonical_root.joinpath(*pure.parts).resolve(strict=False)
        try:
            candidate.relative_to(canonical_root)
        except ValueError as error:
            raise ReconciliationValidationError(
                "write scope escapes the repository root"
            ) from error
    return tuple(sorted(normalized))


def _aware_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _is_aware_timestamp(value: object) -> bool:
    return _aware_timestamp(value) is not None


def _title_semantic_key(value: str) -> str:
    slug = re.sub(
        r"[^\w-]+", "-", value.strip().lower(), flags=re.UNICODE
    ).strip("-_")
    return slug[:72] or "task"


def _field_changes(item: GoalItem, task: QueueTaskSnapshot | None) -> tuple[FieldChange, ...]:
    current_fields = {} if task is None else task.fields
    if task is not None and task.is_compacted:
        changes: list[FieldChange] = []
        for name, desired in sorted(item.effective_fields.items()):
            if name == "title":
                if _title_semantic_key(str(desired)) != task.compacted_title_key:
                    changes.append(
                        FieldChange(name, task.compacted_title_key, desired)
                    )
            else:
                changes.append(FieldChange(name, None, desired))
        return tuple(changes)
    return tuple(
        FieldChange(name, current_fields.get(name), desired)
        for name, desired in sorted(item.effective_fields.items())
        if current_fields.get(name) != desired
    )


def _action_for(task: QueueTaskSnapshot, changes: tuple[FieldChange, ...]) -> ReconciliationAction:
    if not changes:
        return ReconciliationAction.LINK
    if task.state is QueueState.REVIEW:
        return ReconciliationAction.INVALIDATE_REVIEW
    if task.state is QueueState.DONE:
        return ReconciliationAction.REOPEN
    if task.state is QueueState.ACTIVE:
        return ReconciliationAction.REPLAN
    return ReconciliationAction.AMEND


_REASONS = {
    ReconciliationAction.CREATE: "no Queue task in any state covers this Goal fingerprint",
    ReconciliationAction.AMEND: "a pre-execution Queue contract differs from the Goal revision",
    ReconciliationAction.REPLAN: "active work differs from the Goal revision and requires PM replan",
    ReconciliationAction.INVALIDATE_REVIEW: "the reviewed candidate no longer matches the Goal revision",
    ReconciliationAction.REOPEN: "completed work no longer satisfies the revised Goal contract",
    ReconciliationAction.LINK: "matching Queue work exists but lacks explicit Goal linkage",
    ReconciliationAction.NOOP: "Queue coverage and explicit Goal linkage already match",
}


class GoalQueueReconciler:
    """Compare one explicit Goal revision with one complete immutable Queue view."""

    def __init__(
        self, application_ledger: ProposalApplicationLedger | None = None,
    ) -> None:
        self._application_ledger = application_ledger

    def reconcile(
        self,
        goal: GoalRevision,
        queue: QueueInventory,
        *,
        expected_queue_generation: str,
    ) -> tuple[ReconciliationProposal, ...]:
        if not isinstance(goal, GoalRevision) or not isinstance(queue, QueueInventory):
            raise ReconciliationValidationError("reconcile requires GoalRevision and QueueInventory")
        for item in goal.items:
            _validate_structural_fields(item.desired_fields)
        if expected_queue_generation != queue.generation:
            raise StaleQueueGeneration("Queue changed before reconciliation")
        by_id = queue.by_id()
        by_fingerprint = {task.fingerprint: task for task in queue.tasks}
        proposals: list[ReconciliationProposal] = []
        for item in sorted(goal.items, key=lambda value: value.item_id):
            task = by_id.get(item.linked_task_id) if item.linked_task_id else by_fingerprint.get(item.fingerprint)
            if item.linked_task_id is not None and task is None:
                raise StaleQueueGeneration("Goal links a Queue task absent from this generation")
            if task is not None and task.fingerprint != item.fingerprint:
                raise AmbiguousGoalRevision("linked Queue task and Goal fingerprint disagree")
            changes = _field_changes(item, task)
            if task is None:
                action = ReconciliationAction.CREATE
            elif changes:
                action = _action_for(task, changes)
            elif task.is_compacted:
                action = ReconciliationAction.NOOP
            elif item.item_id not in task.goal_item_ids:
                action = ReconciliationAction.LINK
                changes = (
                    FieldChange(
                        "goal_item_ids",
                        task.goal_item_ids,
                        tuple(sorted((*task.goal_item_ids, item.item_id))),
                    ),
                )
            else:
                action = ReconciliationAction.NOOP
            proposals.append(
                ReconciliationProposal(
                    goal_revision=goal.revision,
                    goal_item_id=item.item_id,
                    goal_fingerprint=item.fingerprint,
                    target_queue_id=None if task is None else task.task_id,
                    action=action,
                    reason=_REASONS[action],
                    changed_fields=changes,
                    affected_lead=(
                        task.lead_owner
                        if task is not None and task.lead_owner is not None
                        else item.effective_fields.get("lead_owner")
                    ),
                    current_queue_generation=queue.generation,
                    current_task_generation=None if task is None else task.generation,
                    goal_change_digest=goal.change_digest,
                )
            )
        return tuple(proposals)

    def assert_applicable(
        self,
        proposal: ReconciliationProposal,
        queue: QueueInventory,
        *,
        goal: GoalRevision | None = None,
        write_scope: Sequence[str] = (),
        repository_root: str | Path = ".",
    ) -> ProposalApplicationReservation:
        """Reserve one authentic PM application without mutating the Queue."""

        if not isinstance(proposal, ReconciliationProposal):
            raise ReconciliationValidationError("proposal is malformed")
        if not isinstance(goal, GoalRevision):
            raise ReconciliationValidationError(
                "proposal authenticity requires the fresh Goal revision"
            )
        if self._application_ledger is None:
            raise ReconciliationValidationError(
                "proposal application requires a durable PM application ledger"
            )
        if proposal.action is ReconciliationAction.NOOP:
            raise NonMutatingProposal("NOOP has no lifecycle application")
        if proposal.current_queue_generation != queue.generation:
            raise StaleQueueGeneration("Queue changed after proposal creation")
        fresh = self.reconcile(
            goal,
            queue,
            expected_queue_generation=queue.generation,
        )
        matching = [item for item in fresh if item.goal_item_id == proposal.goal_item_id]
        if len(matching) != 1 or _proposal_json(matching[0]) != _proposal_json(proposal):
            raise ReconciliationValidationError(
                "proposal is not authentic for the fresh Goal and Queue"
            )
        canonical_scope = _canonical_write_scope(
            write_scope, repository_root=repository_root
        )
        goal_item = next(
            item for item in goal.items if item.item_id == proposal.goal_item_id
        )
        target = (
            queue.by_id().get(proposal.target_queue_id)
            if proposal.target_queue_id is not None
            else None
        )
        expected_scope = goal_item.effective_fields.get("write_scope")
        if expected_scope is None and target is not None:
            expected_scope = target.fields.get("write_scope", ())
        if expected_scope is None:
            expected_scope = ()
        expected_canonical_scope = _canonical_write_scope(
            expected_scope, repository_root=repository_root
        )
        if canonical_scope != expected_canonical_scope:
            raise ReconciliationValidationError(
                "write scope differs from the authentic Goal/Queue proposal scope"
            )
        return self._application_ledger.reserve(
            proposal,
            proposal_json=_proposal_json(proposal),
            write_scope=canonical_scope,
        )


def _read_bounded_json(path: Path, limit: int) -> Mapping[str, Any]:
    if _is_reparse_path(path) or not path.is_file():
        raise IncompleteQueueSnapshot(f"Queue file is missing or linked: {path.name}")
    if path.stat().st_size > limit:
        raise IncompleteQueueSnapshot(f"Queue file exceeds its size limit: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IncompleteQueueSnapshot(f"Queue file is unreadable: {path.name}") from error
    if not isinstance(value, dict):
        raise IncompleteQueueSnapshot(f"Queue file is not an object: {path.name}")
    return value


def _read_bounded_bytes(path: Path, limit: int, field: str) -> bytes:
    if _is_reparse_path(path) or not path.is_file():
        raise IncompleteQueueSnapshot(f"required {field} is missing or linked")
    if path.stat().st_size > limit:
        raise IncompleteQueueSnapshot(f"required {field} exceeds its size limit")
    try:
        return path.read_bytes()
    except OSError as error:
        raise IncompleteQueueSnapshot(f"required {field} is unreadable") from error


def _is_reparse_path(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError as error:
        raise IncompleteQueueSnapshot(
            f"Queue path metadata is unreadable: {path.name}"
        ) from error
    return bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _assert_queue_path(
    path: Path,
    *,
    canonical_root: Path,
    label: str,
    directory: bool,
) -> Path:
    if _is_reparse_path(path):
        raise IncompleteQueueSnapshot(f"Queue {label} is a junction/reparse point")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(canonical_root)
    except (OSError, ValueError) as error:
        raise IncompleteQueueSnapshot(
            f"Queue {label} escapes canonical root containment"
        ) from error
    if directory and not path.is_dir():
        raise IncompleteQueueSnapshot(f"Queue {label} directory is missing")
    if not directory and not path.is_file():
        raise IncompleteQueueSnapshot(f"Queue {label} file is missing")
    return resolved


def _queue_tree_generation(root: Path, canonical_root: Path) -> str:
    digest = sha256()
    index_path = root / "COMPLETED_INDEX.json"
    _assert_queue_path(
        index_path,
        canonical_root=canonical_root,
        label="COMPLETED_INDEX",
        directory=False,
    )
    index_body = _read_bounded_bytes(
        index_path, _COMPLETED_INDEX_LIMIT, "COMPLETED_INDEX.json"
    )
    digest.update(b"COMPLETED_INDEX.json\0" + index_body)
    for state_text, parts in _STATE_DIRECTORIES:
        directory = root.joinpath(*parts)
        _assert_queue_path(
            directory,
            canonical_root=canonical_root,
            label=f"state {state_text}",
            directory=True,
        )
        for entry in sorted(directory.iterdir(), key=lambda value: value.name):
            _assert_queue_path(
                entry,
                canonical_root=canonical_root,
                label=f"state entry {entry.name}",
                directory=entry.is_dir(),
            )
            relative = entry.relative_to(root).as_posix().encode("utf-8")
            if entry.is_file():
                body = _read_bounded_bytes(entry, _META_LIMIT, entry.name)
                digest.update(b"F\0" + relative + b"\0" + body)
                continue
            if not entry.is_dir():
                raise IncompleteQueueSnapshot("Queue tree contains an invalid entry")
            digest.update(b"D\0" + relative + b"\0")
            for child in sorted(entry.iterdir(), key=lambda value: value.name):
                _assert_queue_path(
                    child,
                    canonical_root=canonical_root,
                    label=f"task file {child.name}",
                    directory=False,
                )
                child_relative = child.relative_to(root).as_posix().encode("utf-8")
                body = _read_bounded_bytes(child, _META_LIMIT, child.name)
                digest.update(b"F\0" + child_relative + b"\0" + body)
    return digest.hexdigest()


def _validate_full_meta(
    meta: Mapping[str, Any],
    *,
    state: QueueState,
    directory_match: re.Match[str],
    repository_root: Path | None,
) -> None:
    missing = sorted(_REQUIRED_META_FIELDS - set(meta))
    if missing:
        raise IncompleteQueueSnapshot(f"META is missing required fields: {missing}")
    if meta.get("schema_version") != 1 or isinstance(
        meta.get("schema_version"), bool
    ):
        raise IncompleteQueueSnapshot("META schema_version is invalid")
    for name in (
        "id",
        "title",
        "slug",
        "priority",
        "priority_hint",
        "kind",
        "risk",
        "state",
        "created_by",
        "created_at",
        "updated_at",
        "fingerprint",
    ):
        try:
            _required_text(meta.get(name), f"META {name}")
        except ReconciliationValidationError as error:
            raise IncompleteQueueSnapshot(f"META {name} is invalid") from error
    if not isinstance(meta.get("depends_on"), list) or not all(
        isinstance(value, str) and _TASK_ID.fullmatch(value) is not None
        for value in meta["depends_on"]
    ):
        raise IncompleteQueueSnapshot("META depends_on is invalid")
    if not isinstance(meta.get("write_scope"), list) or not all(
        isinstance(value, str) for value in meta["write_scope"]
    ):
        raise IncompleteQueueSnapshot("META write_scope is invalid")
    for name in ("parallelizable", "review_required"):
        if not isinstance(meta.get(name), bool):
            raise IncompleteQueueSnapshot(f"META {name} is invalid")
    for name in (
        "legacy_id",
        "owner",
        "assigned_role",
        "assigned_agent",
        "reviewer",
        "completed_at",
        "parent_task",
        "lease_until",
        "heartbeat",
        "worktree",
        "branch",
    ):
        if meta.get(name) is not None and not isinstance(meta.get(name), str):
            raise IncompleteQueueSnapshot(f"META {name} is invalid")
    for name in (
        "domain",
        "lead_owner",
        "intake_role",
        "reported_by_role",
        "complexity",
        "worker_profile",
        "reviewer_profile",
        "writer_lane",
    ):
        if meta.get(name) is not None and not isinstance(meta.get(name), str):
            raise IncompleteQueueSnapshot(f"META {name} is invalid")
    if meta.get("domain") is not None and meta.get("domain") not in _DOMAINS:
        raise IncompleteQueueSnapshot("META domain is invalid")
    if (
        meta.get("intake_role") is not None
        and meta.get("intake_role") not in _DISCOVERY_INTAKE_ROLES
    ):
        raise IncompleteQueueSnapshot("META intake_role is invalid")
    if (
        meta.get("reported_by_role") is not None
        and meta.get("reported_by_role") not in _DISCOVERY_REPORTER_ROLES
    ):
        raise IncompleteQueueSnapshot("META reported_by_role is invalid")
    if (
        meta.get("complexity") is not None
        and meta.get("complexity") not in _COMPLEXITIES
    ):
        raise IncompleteQueueSnapshot("META complexity is invalid")
    for name in ("worker_profile", "reviewer_profile"):
        if meta.get(name) is not None and meta.get(name) not in _MODEL_PROFILES:
            raise IncompleteQueueSnapshot(f"META {name} is invalid")
    if meta.get("risk") not in _RISKS:
        raise IncompleteQueueSnapshot("META risk is invalid")
    try:
        _validate_structural_fields(meta, queue_meta=True)
        if repository_root is not None:
            _validate_existing_windows_scope_case(
                repository_root, meta["write_scope"]
            )
    except ReconciliationValidationError as error:
        raise IncompleteQueueSnapshot(f"META {error}") from error
    if meta.get("priority") not in _QUEUE_PRIORITIES or meta.get(
        "priority_hint"
    ) not in _QUEUE_PRIORITIES:
        raise IncompleteQueueSnapshot("META priority is invalid")
    created = _aware_timestamp(meta.get("created_at"))
    updated = _aware_timestamp(meta.get("updated_at"))
    if created is None or updated is None:
        raise IncompleteQueueSnapshot("META timestamp is invalid")
    if created > updated:
        raise IncompleteQueueSnapshot("META timestamp order is invalid")
    for name in ("completed_at", "lease_until", "heartbeat"):
        value = meta.get(name)
        if value is not None and not _is_aware_timestamp(value):
            raise IncompleteQueueSnapshot(f"META {name} is invalid")
    completed = _aware_timestamp(meta.get("completed_at"))
    heartbeat = _aware_timestamp(meta.get("heartbeat"))
    lease = _aware_timestamp(meta.get("lease_until"))
    if completed is not None and not created <= completed <= updated:
        raise IncompleteQueueSnapshot("META completion timestamp order is invalid")
    if heartbeat is not None and not created <= heartbeat <= updated:
        raise IncompleteQueueSnapshot("META heartbeat timestamp order is invalid")
    if lease is not None and (heartbeat is None or heartbeat > lease):
        raise IncompleteQueueSnapshot("META lease timestamp order is invalid")
    if meta.get("state") != state.value:
        raise IncompleteQueueSnapshot("Queue metadata state differs from its directory")
    if meta.get("priority") != directory_match.group(1):
        raise IncompleteQueueSnapshot("Queue priority differs from its directory")
    if meta.get("id") != directory_match.group(2):
        raise IncompleteQueueSnapshot("Queue task id differs from its directory")
    if meta.get("slug") != directory_match.group(3):
        raise IncompleteQueueSnapshot("Queue slug differs from its directory")


def _task_generation(directory: Path) -> str:
    digest = sha256()
    for name in ("META.json", "HANDOFF.md", "ORCA_STATE.json"):
        path = directory / name
        if path.exists():
            if _is_reparse_path(path) or not path.is_file():
                raise IncompleteQueueSnapshot("Queue generation input is not a regular file")
            if path.stat().st_size > _META_LIMIT:
                raise IncompleteQueueSnapshot("Queue generation input exceeds its size limit")
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def _review_generation(directory: Path) -> str | None:
    path = directory / "REVIEW.md"
    if not path.exists():
        return None
    if _is_reparse_path(path) or not path.is_file() or path.stat().st_size > _META_LIMIT:
        raise IncompleteQueueSnapshot("Queue review receipt is invalid")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("review_generation:"):
            return _required_text(line.split(":", 1)[1].strip(), "review_generation", limit=128)
    raise IncompleteQueueSnapshot("review task lacks review_generation")


def _repository_root_for_queue(canonical_queue_root: Path) -> Path | None:
    if (
        canonical_queue_root.name.casefold() == "request_queue"
        and canonical_queue_root.parent.name.casefold() == "artifacts"
    ):
        return canonical_queue_root.parent.parent
    return None


def read_queue_inventory(queue_root: str | Path) -> QueueInventory:
    """Read all canonical Queue states and the compacted Done index without writes."""

    root = Path(queue_root)
    if _is_reparse_path(root) or not root.is_dir():
        raise IncompleteQueueSnapshot("canonical Queue root is missing or linked")
    try:
        canonical_root = root.resolve(strict=True)
    except OSError as error:
        raise IncompleteQueueSnapshot("canonical Queue root is unreadable") from error
    repository_root = _repository_root_for_queue(canonical_root)
    mutation_lock = root / ".queue-mutation.lock"
    if mutation_lock.exists() or mutation_lock.is_symlink():
        raise IncompleteQueueSnapshot("Queue mutation lock is present")
    start_generation = _queue_tree_generation(root, canonical_root)
    tasks: list[QueueTaskSnapshot] = []
    states_present: set[QueueState] = set()
    seen_identity_values: dict[str, set[object]] = {
        name: set() for name in ("id", "legacy_id", "fingerprint", "directory")
    }
    for state_text, parts in _STATE_DIRECTORIES:
        state = QueueState(state_text)
        directory = root.joinpath(*parts)
        _assert_queue_path(
            directory,
            canonical_root=canonical_root,
            label=f"state {state_text}",
            directory=True,
        )
        states_present.add(state)
        for task_directory in sorted(directory.iterdir(), key=lambda value: value.name):
            if task_directory.name == ".gitkeep":
                if (
                    _is_reparse_path(task_directory)
                    or not task_directory.is_file()
                    or task_directory.stat().st_size > 2
                    or task_directory.read_bytes() not in {b"", b"\n", b"\r\n"}
                ):
                    raise IncompleteQueueSnapshot("Queue .gitkeep marker is invalid")
                continue
            _assert_queue_path(
                task_directory,
                canonical_root=canonical_root,
                label=f"task {task_directory.name}",
                directory=True,
            )
            directory_match = _TASK_DIRECTORY.fullmatch(task_directory.name)
            if directory_match is None:
                raise IncompleteQueueSnapshot("Queue task directory name is malformed")
            allowed_files = set(_TASK_REQUIRED_FILES)
            allowed_files.update(_TASK_STATE_FILES.get(state.value, set()))
            if state in {
                QueueState.READY,
                QueueState.WAITING,
                QueueState.ACTIVE,
                QueueState.REVIEW,
                QueueState.BLOCKED,
            }:
                allowed_files.add("ORCA_STATE.json")
            present_files: set[str] = set()
            for child in task_directory.iterdir():
                _assert_queue_path(
                    child,
                    canonical_root=canonical_root,
                    label=f"task file {child.name}",
                    directory=False,
                )
                present_files.add(child.name)
                if child.name not in allowed_files:
                    raise IncompleteQueueSnapshot(
                        f"Queue task contains an unexpected file: {child.name}"
                    )
            missing_files = sorted(_TASK_REQUIRED_FILES - present_files)
            if missing_files:
                raise IncompleteQueueSnapshot(
                    f"Queue task is missing required TASK.md/HANDOFF.md/META files: {missing_files}"
                )
            _read_bounded_bytes(task_directory / "TASK.md", _META_LIMIT, "TASK.md")
            _read_bounded_bytes(
                task_directory / "HANDOFF.md", _META_LIMIT, "HANDOFF.md"
            )
            meta = _read_bounded_json(task_directory / "META.json", _META_LIMIT)
            _validate_full_meta(
                meta,
                state=state,
                directory_match=directory_match,
                repository_root=repository_root,
            )
            for name, value in (
                ("id", meta.get("id")),
                ("legacy_id", meta.get("legacy_id")),
                ("fingerprint", meta.get("fingerprint")),
                ("directory", task_directory.name),
            ):
                if value is None:
                    continue
                if value in seen_identity_values[name]:
                    raise IncompleteQueueSnapshot(
                        f"duplicate Queue {name}"
                    )
                seen_identity_values[name].add(value)
            task_id = meta.get("id")
            if (
                meta.get("state") != state.value
                or not isinstance(task_id, str)
                or task_id != directory_match.group(2)
            ):
                raise IncompleteQueueSnapshot("Queue metadata does not match its state directory")
            fields = {name: meta[name] for name in _STRUCTURAL_FIELDS if name in meta}
            raw_links = meta.get("goal_item_ids", [])
            if not isinstance(raw_links, list) or any(not isinstance(value, str) for value in raw_links):
                raise IncompleteQueueSnapshot("Queue Goal linkage is malformed")
            tasks.append(
                QueueTaskSnapshot(
                    task_id=task_id,
                    state=state,
                    fingerprint=_required_text(meta.get("fingerprint"), "Queue fingerprint"),
                    generation=_task_generation(task_directory),
                    fields=fields,
                    goal_item_ids=tuple(sorted(raw_links)),
                    review_generation=_review_generation(task_directory) if state is QueueState.REVIEW else None,
                )
            )
    index_path = root / "COMPLETED_INDEX.json"
    _assert_queue_path(
        index_path,
        canonical_root=canonical_root,
        label="COMPLETED_INDEX",
        directory=False,
    )
    index = _read_bounded_json(index_path, _COMPLETED_INDEX_LIMIT)
    if set(index) != {"entries", "entries_sha256", "schema_version"}:
        raise IncompleteQueueSnapshot("completed Queue index schema differs")
    entries = index.get("entries")
    if index.get("schema_version") != 1 or not isinstance(entries, list):
        raise IncompleteQueueSnapshot("completed Queue index has no entries")
    entries_digest = sha256(_canonical_json(entries).encode("utf-8")).hexdigest()
    if index.get("entries_sha256") != entries_digest:
        raise IncompleteQueueSnapshot("completed Queue index digest differs")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _COMPLETED_ENTRY_FIELDS:
            raise IncompleteQueueSnapshot("completed Queue index entry is malformed")
        task_id = entry.get("id")
        directory_name = entry.get("directory")
        match = (
            _TASK_DIRECTORY.fullmatch(directory_name)
            if isinstance(directory_name, str)
            else None
        )
        if not isinstance(task_id, str) or match is None or match.group(2) != task_id:
            raise IncompleteQueueSnapshot("completed Queue identity is malformed")
        if entry.get("legacy_id") is not None and (
            not isinstance(entry.get("legacy_id"), str)
            or not entry["legacy_id"]
        ):
            raise IncompleteQueueSnapshot("completed Queue legacy identity is malformed")
        if not _is_aware_timestamp(entry.get("completed_at")):
            raise IncompleteQueueSnapshot("completed Queue timestamp is malformed")
        if (
            not isinstance(entry.get("result_summary"), str)
            or not entry["result_summary"].strip()
            or len(entry["result_summary"]) > _TEXT_LIMIT
        ):
            raise IncompleteQueueSnapshot("completed Queue summary is malformed")
        for name in seen_identity_values:
            value = entry.get(name)
            if value is None:
                continue
            if value in seen_identity_values[name]:
                raise IncompleteQueueSnapshot(
                    f"duplicate completed Queue {name}"
                )
            seen_identity_values[name].add(value)
        tasks.append(
            QueueTaskSnapshot(
                task_id=task_id,
                state=QueueState.DONE,
                fingerprint=_required_text(entry.get("fingerprint"), "Queue fingerprint"),
                generation=_required_text(entry.get("receipt_sha256"), "receipt_sha256"),
                fields={},
                compacted_title_key=match.group(3),
            )
        )
    if entries != sorted(
        entries,
        key=lambda value: (str(value["completed_at"]), str(value["id"])),
    ):
        raise IncompleteQueueSnapshot(
            "completed Queue entries are not in canonical order"
        )
    if mutation_lock.exists() or mutation_lock.is_symlink():
        raise IncompleteQueueSnapshot("Queue mutation lock appeared during read")
    end_generation = _queue_tree_generation(root, canonical_root)
    if mutation_lock.exists() or mutation_lock.is_symlink():
        raise IncompleteQueueSnapshot("Queue mutation lock appeared during read")
    if start_generation != end_generation:
        raise IncompleteQueueSnapshot("Queue generation changed during read")
    return QueueInventory(tuple(tasks), frozenset(states_present))


__all__ = [
    "AmbiguousGoalRevision",
    "DuplicateProposalApplication",
    "FieldChange",
    "GoalItem",
    "GoalQueueReconciler",
    "GoalRevision",
    "IncompleteQueueSnapshot",
    "NonMutatingProposal",
    "ProposalApplicationLedger",
    "ProposalApplicationReservation",
    "QueueInventory",
    "QueueState",
    "QueueTaskSnapshot",
    "ReconciliationAction",
    "ReconciliationError",
    "ReconciliationProposal",
    "ReconciliationValidationError",
    "StaleQueueGeneration",
    "read_queue_inventory",
]
