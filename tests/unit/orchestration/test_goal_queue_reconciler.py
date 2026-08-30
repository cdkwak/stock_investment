from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from stock_data.orchestration.workflow_control.goal_queue_reconciler import (
    AmbiguousGoalRevision,
    DuplicateProposalApplication,
    GoalItem,
    GoalQueueReconciler,
    GoalRevision,
    IncompleteQueueSnapshot,
    NonMutatingProposal,
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


def test_application_guard_rejects_duplicate_noop_and_changed_queue() -> None:
    reconciler = GoalQueueReconciler()
    queue = _inventory()
    goal = GoalRevision("r1", (GoalItem("create", "fp-create", "Create", {}),))
    create = reconciler.reconcile(
        goal, queue, expected_queue_generation=queue.generation
    )[0]
    reconciler.assert_applicable(create, queue)
    with pytest.raises(DuplicateProposalApplication):
        reconciler.assert_applicable(
            create, queue, already_applied_proposal_ids=(create.proposal_id,)
        )
    changed = _inventory(_task("B001", QueueState.NEW, "fp-other", "Other"))
    with pytest.raises(StaleQueueGeneration):
        reconciler.assert_applicable(create, changed)

    covered = _inventory(
        _task("B002", QueueState.READY, "fp-create", "Create", links=("create",))
    )
    noop = reconciler.reconcile(
        goal, covered, expected_queue_generation=covered.generation
    )[0]
    assert noop.action is ReconciliationAction.NOOP
    with pytest.raises(NonMutatingProposal):
        reconciler.assert_applicable(noop, covered)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


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
            {
                "fingerprint": "fp-active",
                "goal_item_ids": ["active-goal"],
                "id": "RQ-20260831T010203-C001",
                "lead_owner": "data_lead",
                "state": "active",
                "title": "Active",
            },
        )
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
