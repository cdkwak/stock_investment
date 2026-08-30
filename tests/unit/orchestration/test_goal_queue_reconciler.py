from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from types import MappingProxyType
from uuid import uuid4

import pytest

import stock_data.orchestration.workflow_control.goal_queue_reconciler as reconciler_module

from stock_data.orchestration.workflow_control.goal_queue_reconciler import (
    AmbiguousGoalRevision,
    DuplicateProposalApplication,
    GoalItem,
    GoalQueueReconciler,
    GoalRevision,
    IncompleteQueueSnapshot,
    NonMutatingProposal,
    ProposalApplicationLedger,
    QueueInventory,
    QueueState,
    QueueTaskSnapshot,
    ReconciliationAction,
    ReconciliationValidationError,
    StaleQueueGeneration,
    read_queue_inventory,
)


def _generation(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _task(
    code: str,
    state: QueueState,
    fingerprint: str,
    title: str,
    *,
    links: tuple[str, ...] = (),
) -> QueueTaskSnapshot:
    return QueueTaskSnapshot(
        task_id=f"RQ-20260831T010203-{code}",
        state=state,
        fingerprint=fingerprint,
        generation=_generation(code),
        fields={"lead_owner": "data_lead", "title": title},
        goal_item_ids=links,
        review_generation="review-4" if state is QueueState.REVIEW else None,
    )


def _inventory(*tasks: QueueTaskSnapshot) -> QueueInventory:
    return QueueInventory(tuple(tasks), frozenset(QueueState))


def test_reconcile_proposes_every_action_without_mutating_queue_input() -> None:
    tasks = (
        _task("A001", QueueState.READY, "fp-amend", "old amend", links=("amend",)),
        _task("A002", QueueState.ACTIVE, "fp-replan", "old replan", links=("replan",)),
        _task("A003", QueueState.REVIEW, "fp-review", "old review", links=("review",)),
        _task("A004", QueueState.DONE, "fp-reopen", "old reopen", links=("reopen",)),
        _task("A005", QueueState.WAITING, "fp-link", "link"),
        _task("A006", QueueState.READY, "fp-noop", "noop", links=("noop",)),
    )
    queue = _inventory(*tasks)
    before = queue.generation
    goal = GoalRevision(
        "goal-r7",
        (
            GoalItem("create", "fp-create", "create", {"priority": "P1"}, preferred_lead="data_lead"),
            GoalItem("amend", "fp-amend", "new amend", {}),
            GoalItem("replan", "fp-replan", "new replan", {}),
            GoalItem("review", "fp-review", "new review", {}),
            GoalItem("reopen", "fp-reopen", "new reopen", {}),
            GoalItem("link", "fp-link", "link", {}),
            GoalItem("noop", "fp-noop", "noop", {}),
        ),
    )

    proposals = GoalQueueReconciler().reconcile(
        goal, queue, expected_queue_generation=before
    )
    by_item = {proposal.goal_item_id: proposal for proposal in proposals}
    assert {item: proposal.action for item, proposal in by_item.items()} == {
        "amend": ReconciliationAction.AMEND,
        "create": ReconciliationAction.CREATE,
        "link": ReconciliationAction.LINK,
        "noop": ReconciliationAction.NOOP,
        "reopen": ReconciliationAction.REOPEN,
        "replan": ReconciliationAction.REPLAN,
        "review": ReconciliationAction.INVALIDATE_REVIEW,
    }
    assert by_item["create"].target_queue_id is None
    assert by_item["create"].affected_lead == "data_lead"
    assert by_item["review"].current_task_generation == tasks[2].generation
    assert by_item["link"].changed_fields[0].field == "goal_item_ids"
    assert by_item["noop"].changed_fields == ()
    assert len({proposal.proposal_id for proposal in proposals}) == 7
    assert all(proposal.goal_change_digest == goal.change_digest for proposal in proposals)
    assert queue.generation == before
    assert tuple(task.fields["title"] for task in queue.tasks) == (
        "old amend", "old replan", "old review", "old reopen", "link", "noop"
    )


def test_reconciliation_rejects_ambiguous_goal_and_stale_generations() -> None:
    item = GoalItem("goal-a", "fp-a", "A", {})
    with pytest.raises(AmbiguousGoalRevision, match="explicit user intent"):
        GoalRevision("r1", (item,), explicit_user_intent=False)
    with pytest.raises(AmbiguousGoalRevision, match="repeats an item id"):
        GoalRevision("r1", (item, GoalItem("goal-a", "fp-b", "B", {})))
    with pytest.raises(AmbiguousGoalRevision, match="repeats a Queue fingerprint"):
        GoalRevision("r1", (item, GoalItem("goal-b", "fp-a", "B", {})))
    with pytest.raises(ReconciliationValidationError, match="unsupported Queue fields"):
        GoalItem("goal-a", "fp-a", "A", {"owner": "worker"})
    with pytest.raises(AmbiguousGoalRevision, match="preferred_lead"):
        GoalItem(
            "goal-a",
            "fp-a",
            "A",
            {"lead_owner": "data_lead"},
            preferred_lead="gui_lead",
        )

    queue = _inventory()
    with pytest.raises(StaleQueueGeneration, match="changed before reconciliation"):
        GoalQueueReconciler().reconcile(
            GoalRevision("r1", (item,)),
            queue,
            expected_queue_generation=_generation("stale"),
        )

    linked = GoalItem(
        "goal-a", "fp-a", "A", {}, linked_task_id="RQ-20260831T010203-FFFF"
    )
    with pytest.raises(StaleQueueGeneration, match="absent"):
        GoalQueueReconciler().reconcile(
            GoalRevision("r2", (linked,)),
            queue,
            expected_queue_generation=queue.generation,
        )


@pytest.mark.parametrize(
    "desired_fields",
    [
        {"domain": "unknown"},
        {"domain": []},
        {"priority": "P4"},
        {"lead_owner": ""},
        {"resource_locks": ["z-lock", "a-lock"]},
        {"resource_locks": ["same", "same"]},
        {"writer_lane": "broker"},
        {"depends_on": "not-a-list"},
        {"depends_on": ["not-a-task-id"]},
        {
            "depends_on": [
                "RQ-20260831T010203-E002",
                "RQ-20260831T010203-E001",
            ]
        },
        {"parallelizable": "yes"},
        {"review_required": 1},
        {"risk": "unknown"},
        {"kind": ""},
        {"worker_profile": "unknown"},
        {"reviewer_profile": []},
        {"write_scope": "src/file.py"},
        {"write_scope": ["../escape.py"]},
        {"write_scope": ["src/z.py", "src/a.py"]},
        {"write_scope": ["src/target."]},
        {"write_scope": ["src/target "]},
        {"write_scope": ["src/CON.txt"]},
        {"write_scope": ["src/TARGET", "src/target"]},
    ],
)
def test_goal_rejects_noncanonical_structural_fields(
    desired_fields: dict[str, object],
) -> None:
    with pytest.raises(ReconciliationValidationError):
        GoalItem("goal-a", "fp-a", "A", desired_fields)


def test_application_revalidates_fresh_goal_structural_fields() -> None:
    with _workspace("application-fresh-goal") as root:
        repository = root / "repository"
        repository.mkdir()
        queue = _inventory()
        item = GoalItem("create", "fp-create", "Create", {})
        goal = GoalRevision("r1", (item,))
        reconciler = GoalQueueReconciler(
            ProposalApplicationLedger(
                root / "applications.sqlite3", pm_session_id="pm-session-7"
            )
        )
        proposal = reconciler.reconcile(
            goal, queue, expected_queue_generation=queue.generation
        )[0]
        object.__setattr__(
            item,
            "desired_fields",
            MappingProxyType({"domain": "forged-domain"}),
        )
        with pytest.raises(ReconciliationValidationError, match="domain"):
            reconciler.assert_applicable(
                proposal, queue, goal=goal, repository_root=repository
            )


def test_application_guard_rejects_duplicate_noop_and_changed_queue() -> None:
    with _workspace("application-guard") as root:
        repository = root / "repository"
        repository.mkdir()
        reconciler = GoalQueueReconciler(
            ProposalApplicationLedger(
                root / "applications.sqlite3", pm_session_id="pm-session-7"
            )
        )
        queue = _inventory()
        goal = GoalRevision("r1", (GoalItem("create", "fp-create", "Create", {}),))
        create = reconciler.reconcile(
            goal, queue, expected_queue_generation=queue.generation
        )[0]
        reconciler.assert_applicable(
            create, queue, goal=goal, repository_root=repository
        )
        with pytest.raises(DuplicateProposalApplication):
            reconciler.assert_applicable(
                create, queue, goal=goal, repository_root=repository
            )
        changed = _inventory(_task("B001", QueueState.NEW, "fp-other", "Other"))
        with pytest.raises(StaleQueueGeneration):
            reconciler.assert_applicable(
                create, changed, goal=goal, repository_root=repository
            )

        covered = _inventory(
            _task("B002", QueueState.READY, "fp-create", "Create", links=("create",))
        )
        noop = reconciler.reconcile(
            goal, covered, expected_queue_generation=covered.generation
        )[0]
        assert noop.action is ReconciliationAction.NOOP
        with pytest.raises(NonMutatingProposal):
            reconciler.assert_applicable(
                noop, covered, goal=goal, repository_root=repository
            )


def test_application_guard_is_durable_and_rejects_forged_proposal() -> None:
    with _workspace("application-durable") as root:
        repository = root / "repository"
        repository.mkdir()
        database = root / "applications.sqlite3"
        queue = _inventory()
        goal = GoalRevision("r1", (GoalItem("create", "fp-create", "Create", {}),))
        reconciler = GoalQueueReconciler(
            ProposalApplicationLedger(database, pm_session_id="pm-session-7")
        )
        proposal = reconciler.reconcile(
            goal, queue, expected_queue_generation=queue.generation
        )[0]
        forged = replace(proposal, action=ReconciliationAction.AMEND)
        with pytest.raises(ReconciliationValidationError, match="authentic"):
            reconciler.assert_applicable(
                forged, queue, goal=goal, repository_root=repository
            )
        with pytest.raises(ReconciliationValidationError, match="scope"):
            reconciler.assert_applicable(
                proposal,
                queue,
                goal=goal,
                write_scope=("../escape",),
                repository_root=repository,
            )
        reservation = reconciler.assert_applicable(
            proposal, queue, goal=goal, repository_root=repository
        )
        assert reservation.queue_mutated is False

        restarted = GoalQueueReconciler(
            ProposalApplicationLedger(database, pm_session_id="pm-session-7")
        )
        with pytest.raises(DuplicateProposalApplication):
            restarted.assert_applicable(
                proposal, queue, goal=goal, repository_root=repository
            )


@pytest.mark.parametrize("boundary", ["root", "ancestor"])
def test_application_rejects_junction_repository_root_or_ancestor(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    with _workspace(f"application-junction-{boundary}") as root:
        repository = root / "repository"
        repository.mkdir()
        database = root / "applications.sqlite3"
        queue = _inventory()
        goal = GoalRevision("r1", (GoalItem("create", "fp-create", "Create", {}),))
        reconciler = GoalQueueReconciler(
            ProposalApplicationLedger(database, pm_session_id="pm-session-7")
        )
        proposal = reconciler.reconcile(
            goal, queue, expected_queue_generation=queue.generation
        )[0]
        target = repository if boundary == "root" else repository.parent
        original = Path.is_junction
        monkeypatch.setattr(
            Path,
            "is_junction",
            lambda self: self == target or original(self),
        )
        with pytest.raises(
            ReconciliationValidationError, match="junction|reparse|root"
        ):
            reconciler.assert_applicable(
                proposal, queue, goal=goal, repository_root=repository
            )


def test_application_rejects_existing_windows_case_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _workspace("application-case-alias") as root:
        repository = root / "repository"
        target = repository / "src" / "target"
        target.parent.mkdir(parents=True)
        target.write_text("target\n", encoding="utf-8")
        queue = _inventory()
        goal = GoalRevision(
            "r1",
            (
                GoalItem(
                    "create",
                    "fp-create",
                    "Create",
                    {"write_scope": ["src/TARGET"]},
                ),
            ),
        )
        reconciler = GoalQueueReconciler(
            ProposalApplicationLedger(
                root / "applications.sqlite3", pm_session_id="pm-session-7"
            )
        )
        proposal = reconciler.reconcile(
            goal, queue, expected_queue_generation=queue.generation
        )[0]
        monkeypatch.setattr(
            reconciler_module, "_WINDOWS_PATH_SEMANTICS", True, raising=False
        )

        with pytest.raises(ReconciliationValidationError, match="case|alias"):
            reconciler.assert_applicable(
                proposal,
                queue,
                goal=goal,
                write_scope=("src/TARGET",),
                repository_root=repository,
            )


@pytest.mark.skipif(os.name != "nt", reason="actual NTFS case-alias contract")
def test_application_rejects_actual_windows_samefile_alias() -> None:
    with _workspace("application-actual-case-alias") as root:
        repository = root / "repository"
        target = repository / "src" / "target"
        target.parent.mkdir(parents=True)
        target.write_text("target\n", encoding="utf-8")
        alias = repository / "src" / "TARGET"
        assert os.path.samefile(target, alias)
        queue = _inventory()
        goal = GoalRevision(
            "r1",
            (
                GoalItem(
                    "create",
                    "fp-create",
                    "Create",
                    {"write_scope": ["src/TARGET"]},
                ),
            ),
        )
        reconciler = GoalQueueReconciler(
            ProposalApplicationLedger(
                root / "applications.sqlite3", pm_session_id="pm-session-7"
            )
        )
        proposal = reconciler.reconcile(
            goal, queue, expected_queue_generation=queue.generation
        )[0]

        with pytest.raises(ReconciliationValidationError, match="case|alias"):
            reconciler.assert_applicable(
                proposal,
                queue,
                goal=goal,
                write_scope=("src/TARGET",),
                repository_root=repository,
            )


def test_compacted_done_same_fingerprint_and_title_is_noop_not_reopen() -> None:
    compacted = QueueTaskSnapshot(
        task_id="RQ-20260831T010203-D001",
        state=QueueState.DONE,
        fingerprint="fp-compacted",
        generation=_generation("compacted"),
        fields={},
        compacted_title_key="compacted-result",
    )
    queue = _inventory(compacted)
    proposal = GoalQueueReconciler().reconcile(
        GoalRevision(
            "r1",
            (GoalItem("compacted", "fp-compacted", "Compacted result", {}),),
        ),
        queue,
        expected_queue_generation=queue.generation,
    )[0]
    assert proposal.action is ReconciliationAction.NOOP


def test_compacted_done_uses_canonical_truncated_title_semantics() -> None:
    title = "A title with punctuation! " + "x" * 100
    compacted = QueueTaskSnapshot(
        task_id="RQ-20260831T010203-D002",
        state=QueueState.DONE,
        fingerprint="fp-compacted-long",
        generation=_generation("compacted-long"),
        fields={},
        compacted_title_key=("a-title-with-punctuation-" + "x" * 100)[:72],
    )
    queue = _inventory(compacted)
    proposal = GoalQueueReconciler().reconcile(
        GoalRevision(
            "r1",
            (GoalItem("compacted", "fp-compacted-long", title, {}),),
        ),
        queue,
        expected_queue_generation=queue.generation,
    )[0]
    assert proposal.action is ReconciliationAction.NOOP


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _complete_meta(
    *,
    task_id: str,
    slug: str,
    state: str,
    priority: str,
    title: str,
    fingerprint: str,
    **extra: object,
) -> dict[str, object]:
    meta: dict[str, object] = {
        "assigned_agent": "test-agent",
        "assigned_role": "lead",
        "branch": None,
        "completed_at": None,
        "created_at": "2026-08-31T01:02:03Z",
        "created_by": "test",
        "depends_on": [],
        "fingerprint": fingerprint,
        "heartbeat": "2026-08-31T01:02:03Z",
        "id": task_id,
        "kind": "bug",
        "lease_until": "2026-08-31T02:02:03Z",
        "legacy_id": None,
        "owner": "test-agent",
        "parallelizable": True,
        "parent_task": None,
        "priority": priority,
        "priority_hint": priority,
        "review_required": False,
        "reviewer": None,
        "risk": "high",
        "schema_version": 1,
        "slug": slug,
        "state": state,
        "title": title,
        "updated_at": "2026-08-31T01:02:03Z",
        "worktree": None,
        "write_scope": [],
    }
    meta.update(extra)
    return meta


@contextmanager
def _workspace(prefix: str):
    root = Path(os.environ["TEMP"]) / f"{prefix}-{uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def test_read_queue_inventory_covers_all_states_and_compacted_done_read_only() -> None:
    with _workspace("goal-queue-inventory") as root:
        for _state, parts in (
            ("new", ("inbox", "new")),
            ("ready", ("inbox", "ready")),
            ("waiting", ("waiting",)),
            ("active", ("active",)),
            ("review", ("review",)),
            ("blocked", ("blocked",)),
            ("done", ("done",)),
        ):
            root.joinpath(*parts).mkdir(parents=True, exist_ok=True)
        (root / "waiting" / ".gitkeep").write_text("\n", encoding="utf-8")
        active = root / "active" / "P1-RQ-20260831T010203-C001-example"
        active.mkdir()
        _write_json(
            active / "META.json",
            _complete_meta(
                task_id="RQ-20260831T010203-C001",
                slug="example",
                state="active",
                priority="P1",
                title="Active",
                fingerprint="fp-active",
                goal_item_ids=["active-goal"],
                lead_owner="data_lead",
            ),
        )
        (active / "TASK.md").write_text("# Active\n", encoding="utf-8")
        (active / "HANDOFF.md").write_text("checkpoint\n", encoding="utf-8")
        entries = [
            {
                "completed_at": "2026-08-31T01:02:03Z",
                "directory": "P1-RQ-20260831T010203-C002-compacted",
                "fingerprint": "fp-compacted",
                "id": "RQ-20260831T010203-C002",
                "legacy_id": None,
                "receipt_sha256": _generation("compacted"),
                "result_summary": "Compacted result",
            }
        ]
        _write_json(
            root / "COMPLETED_INDEX.json",
            {
                "entries": entries,
                "entries_sha256": sha256(
                    json.dumps(
                        entries,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "schema_version": 1,
            },
        )
        before = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

        queue = read_queue_inventory(root)

        assert queue.states_present == frozenset(QueueState)
        assert {task.state for task in queue.tasks} == {QueueState.ACTIVE, QueueState.DONE}
        assert len(queue.generation) == 64
        after = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }
        assert after == before


def test_incomplete_queue_snapshot_fails_closed() -> None:
    with pytest.raises(IncompleteQueueSnapshot, match="missing states"):
        QueueInventory((), frozenset({QueueState.NEW}))


def _empty_queue_root(root: Path, *, include_index: bool = True) -> None:
    for parts in (
        ("inbox", "new"),
        ("inbox", "ready"),
        ("waiting",),
        ("active",),
        ("review",),
        ("blocked",),
        ("done",),
    ):
        root.joinpath(*parts).mkdir(parents=True, exist_ok=True)
    if include_index:
        _write_json(
            root / "COMPLETED_INDEX.json",
            {
                "entries": [],
                "entries_sha256": sha256(b"[]").hexdigest(),
                "schema_version": 1,
            },
        )


def _minimal_task(root: Path, *, directory_priority: str = "P1", meta_priority: str = "P1") -> Path:
    task = root / "active" / f"{directory_priority}-RQ-20260831T010203-E001-task"
    task.mkdir()
    _write_json(
        task / "META.json",
        _complete_meta(
            task_id="RQ-20260831T010203-E001",
            slug="task",
            state="active",
            priority=meta_priority,
            title="Task",
            fingerprint="fp-task",
        ),
    )
    return task


def test_queue_loader_rejects_missing_index_lock_and_malformed_task() -> None:
    with _workspace("queue-missing-index") as root:
        _empty_queue_root(root, include_index=False)
        with pytest.raises(IncompleteQueueSnapshot, match="INDEX|index"):
            read_queue_inventory(root)
    with _workspace("queue-lock") as root:
        _empty_queue_root(root)
        (root / ".queue-mutation.lock").write_text("pid=1\n", encoding="utf-8")
        with pytest.raises(IncompleteQueueSnapshot, match="lock"):
            read_queue_inventory(root)
    with _workspace("queue-required-files") as root:
        _empty_queue_root(root)
        _minimal_task(root)
        with pytest.raises(IncompleteQueueSnapshot, match="TASK.md|HANDOFF.md|META"):
            read_queue_inventory(root)
    with _workspace("queue-priority") as root:
        _empty_queue_root(root)
        task = _minimal_task(root, directory_priority="P1", meta_priority="P2")
        (task / "TASK.md").write_text("task\n", encoding="utf-8")
        (task / "HANDOFF.md").write_text("handoff\n", encoding="utf-8")
        with pytest.raises(IncompleteQueueSnapshot, match="priority"):
            read_queue_inventory(root)


def test_queue_loader_rejects_generation_race(monkeypatch: pytest.MonkeyPatch) -> None:
    with _workspace("queue-race") as root:
        _empty_queue_root(root)
        original = reconciler_module._read_bounded_json
        changed = False

        def mutate_after_meta(path: Path, limit: int):
            nonlocal changed
            value = original(path, limit)
            if path.name == "META.json" and not changed:
                changed = True
                rewritten = dict(value)
                rewritten["title"] = "Changed during read"
                _write_json(path, rewritten)
            return value

        task = _minimal_task(root)
        (task / "TASK.md").write_text("task\n", encoding="utf-8")
        (task / "HANDOFF.md").write_text("handoff\n", encoding="utf-8")
        monkeypatch.setattr(reconciler_module, "_read_bounded_json", mutate_after_meta)
        with pytest.raises(IncompleteQueueSnapshot, match="changed|generation"):
            read_queue_inventory(root)


@pytest.mark.parametrize("boundary", ["root", "state", "task", "index"])
def test_queue_loader_rejects_windows_junction_at_every_boundary(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    with _workspace(f"queue-junction-{boundary}") as root:
        _empty_queue_root(root)
        task = _minimal_task(root)
        (task / "TASK.md").write_text("task\n", encoding="utf-8")
        (task / "HANDOFF.md").write_text("handoff\n", encoding="utf-8")
        target = {
            "root": root,
            "state": root / "active",
            "task": task,
            "index": root / "COMPLETED_INDEX.json",
        }[boundary]
        original = Path.is_junction
        monkeypatch.setattr(
            Path,
            "is_junction",
            lambda self: self == target or original(self),
        )
        with pytest.raises(
            IncompleteQueueSnapshot, match="junction|reparse|contain|linked"
        ):
            read_queue_inventory(root)


def test_queue_loader_rejects_resolved_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _workspace("queue-resolved-escape") as root:
        _empty_queue_root(root)
        escaped_state = root / "active"
        outside = root.parent / "outside-queue"
        original = Path.resolve

        def escaped_resolve(self: Path, *args: object, **kwargs: object) -> Path:
            if self == escaped_state:
                return outside
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", escaped_resolve)
        with pytest.raises(IncompleteQueueSnapshot, match="contain|escape"):
            read_queue_inventory(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse attribute contract")
def test_queue_loader_rejects_windows_reparse_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _workspace("queue-reparse-attribute") as root:
        _empty_queue_root(root)
        index = root / "COMPLETED_INDEX.json"
        original_lstat = Path.lstat
        monkeypatch.setattr(Path, "is_junction", lambda _self: False)

        def reparse_lstat(self: Path):
            result = original_lstat(self)
            if self == index:
                class ReparseStat:
                    st_file_attributes = getattr(
                        reconciler_module.stat,
                        "FILE_ATTRIBUTE_REPARSE_POINT",
                        0x400,
                    )

                    def __getattr__(self, name: str) -> object:
                        return getattr(result, name)

                return ReparseStat()
            return result

        monkeypatch.setattr(Path, "lstat", reparse_lstat)
        with pytest.raises(IncompleteQueueSnapshot, match="reparse"):
            read_queue_inventory(root)


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"domain": "unknown"}, "domain"),
        ({"resource_locks": ["z-lock", "a-lock"]}, "resource_locks"),
        ({"writer_lane": "broker"}, "writer_lane"),
        ({"owner": 7}, "owner"),
    ],
)
def test_queue_loader_rejects_invalid_optional_meta(
    extra: dict[str, object],
    match: str,
) -> None:
    with _workspace("queue-invalid-optional-meta") as root:
        _empty_queue_root(root)
        task = _minimal_task(root)
        meta = json.loads((task / "META.json").read_text(encoding="utf-8"))
        meta.update(extra)
        _write_json(task / "META.json", meta)
        (task / "TASK.md").write_text("task\n", encoding="utf-8")
        (task / "HANDOFF.md").write_text("handoff\n", encoding="utf-8")
        with pytest.raises(IncompleteQueueSnapshot, match=match):
            read_queue_inventory(root)


@pytest.mark.parametrize(
    "write_scope",
    [
        ["src/target."],
        ["src/target "],
        ["CON"],
        ["src/PRN.txt"],
        ["src/AUX"],
        ["src/NUL.log"],
        ["src/COM1"],
        ["src/LPT1.md"],
        ["src/TARGET", "src/target"],
    ],
)
def test_queue_loader_rejects_windows_scope_aliases(
    write_scope: list[str],
) -> None:
    with _workspace("queue-windows-scope-alias") as root:
        _empty_queue_root(root)
        task = _minimal_task(root)
        meta = json.loads((task / "META.json").read_text(encoding="utf-8"))
        meta["write_scope"] = write_scope
        _write_json(task / "META.json", meta)
        (task / "TASK.md").write_text("task\n", encoding="utf-8")
        (task / "HANDOFF.md").write_text("handoff\n", encoding="utf-8")

        with pytest.raises(IncompleteQueueSnapshot, match="write_scope"):
            read_queue_inventory(root)


def test_queue_loader_rejects_existing_windows_case_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _workspace("queue-existing-case-alias") as root:
        repository = root / "repository"
        queue_root = repository / "artifacts" / "request_queue"
        target = repository / "src" / "target"
        target.parent.mkdir(parents=True)
        target.write_text("target\n", encoding="utf-8")
        _empty_queue_root(queue_root)
        task = _minimal_task(queue_root)
        meta = json.loads((task / "META.json").read_text(encoding="utf-8"))
        meta["write_scope"] = ["src/TARGET"]
        _write_json(task / "META.json", meta)
        (task / "TASK.md").write_text("task\n", encoding="utf-8")
        (task / "HANDOFF.md").write_text("handoff\n", encoding="utf-8")
        monkeypatch.setattr(
            reconciler_module, "_WINDOWS_PATH_SEMANTICS", True, raising=False
        )

        with pytest.raises(
            IncompleteQueueSnapshot, match="write_scope.*case|alias"
        ):
            read_queue_inventory(queue_root)


def test_queue_loader_rejects_reversed_meta_timestamps() -> None:
    with _workspace("queue-reversed-timestamps") as root:
        _empty_queue_root(root)
        task = _minimal_task(root)
        meta = json.loads((task / "META.json").read_text(encoding="utf-8"))
        meta["created_at"] = "2026-08-31T03:00:00Z"
        meta["updated_at"] = "2026-08-31T02:00:00Z"
        _write_json(task / "META.json", meta)
        (task / "TASK.md").write_text("task\n", encoding="utf-8")
        (task / "HANDOFF.md").write_text("handoff\n", encoding="utf-8")
        with pytest.raises(IncompleteQueueSnapshot, match="timestamp.*order"):
            read_queue_inventory(root)


def _completed_entry(
    *, task_id: str, completed_at: str, legacy_id: str,
) -> dict[str, object]:
    return {
        "completed_at": completed_at,
        "directory": f"P1-{task_id}-compacted",
        "fingerprint": f"fp-{task_id}",
        "id": task_id,
        "legacy_id": legacy_id,
        "receipt_sha256": _generation(task_id),
        "result_summary": "Compacted result",
    }


def _write_completed_entries(root: Path, entries: list[dict[str, object]]) -> None:
    _write_json(
        root / "COMPLETED_INDEX.json",
        {
            "entries": entries,
            "entries_sha256": sha256(
                json.dumps(
                    entries,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "schema_version": 1,
        },
    )


def test_queue_loader_rejects_noncanonical_index_order_and_duplicate_legacy() -> None:
    first = _completed_entry(
        task_id="RQ-20260831T010203-E010",
        completed_at="2026-08-31T01:00:00Z",
        legacy_id="P1-010",
    )
    second = _completed_entry(
        task_id="RQ-20260831T010203-E011",
        completed_at="2026-08-31T02:00:00Z",
        legacy_id="P1-011",
    )
    with _workspace("queue-index-order") as root:
        _empty_queue_root(root)
        _write_completed_entries(root, [second, first])
        with pytest.raises(IncompleteQueueSnapshot, match="order"):
            read_queue_inventory(root)
    with _workspace("queue-index-legacy") as root:
        _empty_queue_root(root)
        second["legacy_id"] = first["legacy_id"]
        _write_completed_entries(root, [first, second])
        with pytest.raises(IncompleteQueueSnapshot, match="legacy"):
            read_queue_inventory(root)


def test_queue_loader_rejects_duplicate_live_legacy_identity() -> None:
    with _workspace("queue-live-legacy") as root:
        _empty_queue_root(root)
        first = _minimal_task(root)
        first_meta = json.loads(
            (first / "META.json").read_text(encoding="utf-8")
        )
        first_meta["legacy_id"] = "P1-SHARED"
        _write_json(first / "META.json", first_meta)
        (first / "TASK.md").write_text("task\n", encoding="utf-8")
        (first / "HANDOFF.md").write_text("handoff\n", encoding="utf-8")

        second = root / "active" / "P1-RQ-20260831T010203-E002-task-two"
        second.mkdir()
        _write_json(
            second / "META.json",
            _complete_meta(
                task_id="RQ-20260831T010203-E002",
                slug="task-two",
                state="active",
                priority="P1",
                title="Task two",
                fingerprint="fp-task-two",
                legacy_id="P1-SHARED",
            ),
        )
        (second / "TASK.md").write_text("task\n", encoding="utf-8")
        (second / "HANDOFF.md").write_text("handoff\n", encoding="utf-8")
        with pytest.raises(IncompleteQueueSnapshot, match="legacy"):
            read_queue_inventory(root)
