"""Pure, proposal-only reconciliation of an explicit Goal revision and Queue.

The canonical request Queue remains owned by ``scripts/request_queue.py``.
This module may read a complete Queue snapshot and produce content-addressed
proposals, but it deliberately exposes no Queue writer or lifecycle command.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


_SHA256 = re.compile(r"[0-9a-f]{64}")
_TASK_ID = re.compile(r"RQ-\d{8}T\d{6}-[A-Z0-9]{4}")
_TASK_DIRECTORY = re.compile(
    r"P[012]-(RQ-\d{8}T\d{6}-[A-Z0-9]{4})-([^\\/\s]+)"
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
        object.__setattr__(self, "fields", fields)

    @property
    def lead_owner(self) -> str | None:
        value = self.fields.get("lead_owner")
        return value if isinstance(value, str) and value else None


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


def _field_changes(item: GoalItem, task: QueueTaskSnapshot | None) -> tuple[FieldChange, ...]:
    current_fields = {} if task is None else task.fields
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

    def reconcile(
        self,
        goal: GoalRevision,
        queue: QueueInventory,
        *,
        expected_queue_generation: str,
    ) -> tuple[ReconciliationProposal, ...]:
        if not isinstance(goal, GoalRevision) or not isinstance(queue, QueueInventory):
            raise ReconciliationValidationError("reconcile requires GoalRevision and QueueInventory")
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
        already_applied_proposal_ids: Iterable[str] = (),
    ) -> None:
        """Fence a later PM application without applying or mutating anything."""

        if not isinstance(proposal, ReconciliationProposal):
            raise ReconciliationValidationError("proposal is malformed")
        if proposal.action is ReconciliationAction.NOOP:
            raise NonMutatingProposal("NOOP has no lifecycle application")
        applied = set(already_applied_proposal_ids)
        if proposal.proposal_id in applied:
            raise DuplicateProposalApplication("proposal was already applied")
        if proposal.current_queue_generation != queue.generation:
            raise StaleQueueGeneration("Queue changed after proposal creation")
        if proposal.target_queue_id is None:
            if any(task.fingerprint == proposal.goal_fingerprint for task in queue.tasks):
                raise StaleQueueGeneration("CREATE target now has Queue coverage")
            return
        task = queue.by_id().get(proposal.target_queue_id)
        if task is None or task.generation != proposal.current_task_generation:
            raise StaleQueueGeneration("target Queue generation changed")


def _read_bounded_json(path: Path, limit: int) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
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


def _task_generation(directory: Path) -> str:
    digest = sha256()
    for name in ("META.json", "HANDOFF.md", "ORCA_STATE.json"):
        path = directory / name
        if path.exists():
            if path.is_symlink() or not path.is_file():
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
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _META_LIMIT:
        raise IncompleteQueueSnapshot("Queue review receipt is invalid")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("review_generation:"):
            return _required_text(line.split(":", 1)[1].strip(), "review_generation", limit=128)
    raise IncompleteQueueSnapshot("review task lacks review_generation")


def read_queue_inventory(queue_root: str | Path) -> QueueInventory:
    """Read all canonical Queue states and the compacted Done index without writes."""

    root = Path(queue_root)
    if root.is_symlink() or not root.is_dir():
        raise IncompleteQueueSnapshot("canonical Queue root is missing or linked")
    tasks: list[QueueTaskSnapshot] = []
    states_present: set[QueueState] = set()
    for state_text, parts in _STATE_DIRECTORIES:
        state = QueueState(state_text)
        directory = root.joinpath(*parts)
        if directory.is_symlink() or not directory.is_dir():
            raise IncompleteQueueSnapshot(f"Queue state directory is missing: {state_text}")
        states_present.add(state)
        for task_directory in sorted(directory.iterdir(), key=lambda value: value.name):
            if task_directory.name == ".gitkeep":
                if (
                    task_directory.is_symlink()
                    or not task_directory.is_file()
                    or task_directory.stat().st_size > 2
                    or task_directory.read_bytes() not in {b"", b"\n", b"\r\n"}
                ):
                    raise IncompleteQueueSnapshot("Queue .gitkeep marker is invalid")
                continue
            if task_directory.is_symlink() or not task_directory.is_dir():
                raise IncompleteQueueSnapshot("Queue state contains a non-directory entry")
            directory_match = _TASK_DIRECTORY.fullmatch(task_directory.name)
            if directory_match is None:
                raise IncompleteQueueSnapshot("Queue task directory name is malformed")
            meta = _read_bounded_json(task_directory / "META.json", _META_LIMIT)
            task_id = meta.get("id")
            if (
                meta.get("state") != state.value
                or not isinstance(task_id, str)
                or task_id != directory_match.group(1)
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
    if index_path.exists():
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
            if not isinstance(task_id, str) or match is None or match.group(1) != task_id:
                raise IncompleteQueueSnapshot("completed Queue identity is malformed")
            tasks.append(
                QueueTaskSnapshot(
                    task_id=task_id,
                    state=QueueState.DONE,
                    fingerprint=_required_text(entry.get("fingerprint"), "Queue fingerprint"),
                    generation=_required_text(entry.get("receipt_sha256"), "receipt_sha256"),
                    fields={},
                )
            )
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
