from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from stock_data.orchestration.workflow_control.contracts import (
    EventKind,
    EventSource,
    TaskState,
    WorkflowEvent,
)
from stock_data.orchestration.workflow_control.policy import (
    AcceptedWorkflowSnapshot,
    ActionClass,
    AuthorityTier,
    CanaryCriteria,
    CanaryDecision,
    IndependentReviewReceipt,
    PolicyContractError,
    PolicyProposal,
    ReceiptDecision,
    ReviewDecision,
    evaluate_authority,
    evaluate_canary,
    issue_promotion_receipt,
    issue_rollback_receipt,
)
from stock_data.orchestration.workflow_control.replay import replay_policy


UTC = timezone.utc
T0 = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
TASK_ID = "RQ-20260829T003946-70A9"


def event(event_id: str, minutes: int, state: TaskState) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=event_id,
        occurred_at=T0 + timedelta(minutes=minutes),
        kind=EventKind.TASK_TRANSITION,
        source=EventSource.QUEUE,
        task_id=TASK_ID,
        to_state=state,
        domain="infra",
        reason_code="QUEUE_TRANSITION",
    )


def proposal(
    events: tuple[WorkflowEvent, ...],
    *,
    canary: CanaryCriteria | None = None,
) -> PolicyProposal:
    snapshot = AcceptedWorkflowSnapshot.accept(
        events,
        generation=DIGEST_A,
        acceptance_receipt_digest=DIGEST_B,
    )
    return PolicyProposal.create(
        policy_version="workflow-policy/v2",
        base_policy_version="workflow-policy/v1",
        snapshot=snapshot,
        implementation_fingerprint=DIGEST_C,
        canary=canary,
    )


def passing_review(candidate: PolicyProposal) -> IndependentReviewReceipt:
    return IndependentReviewReceipt.issue(
        candidate,
        reviewer_fingerprint="d" * 64,
        decision=ReviewDecision.PASS,
        manifest_digest="e" * 64,
    )


def test_proposal_binds_an_accepted_event_snapshot_and_replays_deterministically() -> None:
    events = (
        event("ready", 0, TaskState.READY),
        event("active", 5, TaskState.ACTIVE),
    )
    candidate = proposal(events)

    first = replay_policy(
        candidate, events, expected_generation=candidate.proposal_generation
    )
    second = replay_policy(
        candidate, reversed(events), expected_generation=candidate.proposal_generation
    )

    assert first == second
    assert first.passed
    assert first.event_count == 2
    assert first.kind_counts == (("TASK_TRANSITION", 2),)
    assert first.events_digest == candidate.snapshot.events_digest


def test_replay_rejects_stale_generation_and_snapshot_substitution() -> None:
    events = (event("ready", 0, TaskState.READY),)
    candidate = proposal(events)

    with pytest.raises(PolicyContractError, match="stale proposal generation"):
        replay_policy(candidate, events, expected_generation="f" * 64)
    with pytest.raises(PolicyContractError, match="accepted snapshot"):
        replay_policy(
            candidate,
            (event("active", 1, TaskState.ACTIVE),),
            expected_generation=candidate.proposal_generation,
        )


def test_proposal_generation_is_immutable_and_content_addressed() -> None:
    candidate = proposal((event("ready", 0, TaskState.READY),))

    with pytest.raises(PolicyContractError, match="immutable content"):
        replace(candidate, policy_version="workflow-policy/v3")


def test_independent_review_rejects_implementation_identity() -> None:
    candidate = proposal((event("ready", 0, TaskState.READY),))

    with pytest.raises(PolicyContractError, match="independent"):
        IndependentReviewReceipt.issue(
            candidate,
            reviewer_fingerprint=candidate.implementation_fingerprint,
            decision=ReviewDecision.PASS,
            manifest_digest="e" * 64,
        )


def test_independent_review_receipt_rejects_post_review_manifest_change() -> None:
    candidate = proposal((event("ready", 0, TaskState.READY),))
    review = passing_review(candidate)

    with pytest.raises(PolicyContractError, match="review receipt digest"):
        replace(review, manifest_digest="f" * 64)


@pytest.mark.parametrize(
    "action",
    (
        ActionClass.BROKER_MUTATION,
        ActionClass.ORDER_MUTATION,
        ActionClass.TRANSFER_WITHDRAWAL,
        ActionClass.FINANCIAL_MUTATION,
        ActionClass.ACCESS_CONTROL,
        ActionClass.SECRET_HANDLING,
        ActionClass.PAID_SERVICE,
        ActionClass.DESTRUCTIVE_MIGRATION,
    ),
)
def test_protected_mutation_boundaries_are_unconditionally_refused(
    action: ActionClass,
) -> None:
    receipt = evaluate_authority(
        action,
        independent_review_passed=True,
        standing_authority=True,
    )

    assert receipt.tier is AuthorityTier.PROHIBITED
    assert receipt.decision is ReceiptDecision.REFUSED
    assert receipt.reason_code == "PROHIBITED_ACTION"


@pytest.mark.parametrize(
    "action",
    (
        ActionClass.ACCOUNT_READ,
        ActionClass.STANDING_AUTHORITY,
        ActionClass.BOUNDED_CANARY,
        ActionClass.PRODUCTION_PROMOTION,
        ActionClass.ROLLBACK,
    ),
)
def test_standing_authority_actions_fail_closed_without_independent_review(
    action: ActionClass,
) -> None:
    unreviewed = evaluate_authority(action, standing_authority=True)
    unapproved = evaluate_authority(action, independent_review_passed=True)
    approved = evaluate_authority(
        action,
        independent_review_passed=True,
        standing_authority=True,
    )

    assert unreviewed.decision is ReceiptDecision.REFUSED
    assert unreviewed.reason_code == "INDEPENDENT_REVIEW_REQUIRED"
    assert unapproved.decision is ReceiptDecision.REFUSED
    assert unapproved.reason_code == "STANDING_AUTHORITY_REQUIRED"
    assert approved.decision is ReceiptDecision.APPROVED


def test_unknown_action_class_fails_closed() -> None:
    with pytest.raises(PolicyContractError, match="unknown action"):
        evaluate_authority("ORDER_MUTATION")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    (("independent_review_passed", 1), ("standing_authority", "yes")),
)
def test_authority_rejects_truthy_non_boolean_evidence(field: str, value: object) -> None:
    kwargs = {"independent_review_passed": True, "standing_authority": True}
    kwargs[field] = value

    with pytest.raises(PolicyContractError, match="must be boolean"):
        evaluate_authority(ActionClass.ACCOUNT_READ, **kwargs)  # type: ignore[arg-type]


def test_canary_is_disabled_by_default_and_bounded_when_enabled() -> None:
    events = (event("ready", 0, TaskState.READY),)
    disabled = proposal(events)
    refusal = evaluate_canary(
        disabled,
        expected_generation=disabled.proposal_generation,
        observed=1,
        failures=0,
    )
    assert refusal.decision is CanaryDecision.REFUSED
    assert refusal.reason_code == "CANARY_DISABLED"

    enabled = proposal(
        events,
        canary=CanaryCriteria(
            enabled=True,
            required_observations=2,
            maximum_observations=3,
            maximum_failures=0,
        ),
    )
    incomplete = evaluate_canary(
        enabled,
        expected_generation=enabled.proposal_generation,
        observed=1,
        failures=0,
    )
    exceeded = evaluate_canary(
        enabled,
        expected_generation=enabled.proposal_generation,
        observed=4,
        failures=0,
    )
    passed = evaluate_canary(
        enabled,
        expected_generation=enabled.proposal_generation,
        observed=2,
        failures=0,
    )
    assert incomplete.reason_code == "CANARY_OBSERVATIONS_INCOMPLETE"
    assert exceeded.reason_code == "CANARY_BOUND_EXCEEDED"
    assert passed.decision is CanaryDecision.PASSED


def test_canary_rejects_failures_greater_than_observations() -> None:
    candidate = proposal(
        (event("ready", 0, TaskState.READY),),
        canary=CanaryCriteria(enabled=True, required_observations=1),
    )

    with pytest.raises(PolicyContractError, match="cannot exceed observations"):
        evaluate_canary(
            candidate,
            expected_generation=candidate.proposal_generation,
            observed=1,
            failures=2,
        )


def test_promotion_refuses_disabled_canary_and_never_mutates_production() -> None:
    events = (event("ready", 0, TaskState.READY),)
    candidate = proposal(events)
    replay = replay_policy(
        candidate, events, expected_generation=candidate.proposal_generation
    )
    review = passing_review(candidate)
    canary = evaluate_canary(
        candidate,
        expected_generation=candidate.proposal_generation,
        observed=1,
        failures=0,
    )

    receipt = issue_promotion_receipt(
        candidate,
        expected_generation=candidate.proposal_generation,
        replay=replay,
        review=review,
        canary=canary,
    )

    assert receipt.decision is ReceiptDecision.REFUSED
    assert receipt.reason_code == "CANARY_NOT_PASSED"
    assert not receipt.production_mutated


def test_explicit_promotion_and_rollback_receipts_are_side_effect_free() -> None:
    events = (event("ready", 0, TaskState.READY),)
    candidate = proposal(
        events,
        canary=CanaryCriteria(
            enabled=True,
            required_observations=2,
            maximum_observations=3,
            maximum_failures=0,
        ),
    )
    replay = replay_policy(
        candidate, events, expected_generation=candidate.proposal_generation
    )
    review = passing_review(candidate)
    canary = evaluate_canary(
        candidate,
        expected_generation=candidate.proposal_generation,
        observed=2,
        failures=0,
    )

    promotion = issue_promotion_receipt(
        candidate,
        expected_generation=candidate.proposal_generation,
        replay=replay,
        review=review,
        canary=canary,
    )
    rollback = issue_rollback_receipt(
        candidate,
        promotion,
        review,
        expected_generation=candidate.proposal_generation,
        reason_code="CANARY_REGRESSION",
    )

    assert promotion.decision is ReceiptDecision.APPROVED
    assert rollback.decision is ReceiptDecision.APPROVED
    assert rollback.target_policy_version == candidate.base_policy_version
    assert not promotion.production_mutated
    assert not rollback.production_mutated


def test_promotion_rejects_stale_review_and_stale_expected_generation() -> None:
    events = (event("ready", 0, TaskState.READY),)
    criteria = CanaryCriteria(enabled=True, required_observations=1)
    candidate = proposal(events, canary=criteria)
    other = PolicyProposal.create(
        policy_version="workflow-policy/v3",
        base_policy_version="workflow-policy/v2",
        snapshot=candidate.snapshot,
        implementation_fingerprint="9" * 64,
        canary=criteria,
    )
    review = passing_review(other)
    canary = evaluate_canary(
        candidate,
        expected_generation=candidate.proposal_generation,
        observed=1,
        failures=0,
    )
    replay = replay_policy(
        candidate, events, expected_generation=candidate.proposal_generation
    )

    refused = issue_promotion_receipt(
        candidate,
        expected_generation=candidate.proposal_generation,
        replay=replay,
        review=review,
        canary=canary,
    )
    assert refused.decision is ReceiptDecision.REFUSED
    assert refused.reason_code == "STALE_REVIEW"
    with pytest.raises(PolicyContractError, match="stale proposal generation"):
        issue_promotion_receipt(
            candidate,
            expected_generation="7" * 64,
            replay=replay,
            review=review,
            canary=canary,
        )


def test_promotion_requires_same_generation_snapshot_bound_replay_receipt() -> None:
    events = (event("ready", 0, TaskState.READY),)
    criteria = CanaryCriteria(enabled=True, required_observations=1)
    candidate = proposal(events, canary=criteria)
    review = passing_review(candidate)
    canary = evaluate_canary(
        candidate,
        expected_generation=candidate.proposal_generation,
        observed=1,
        failures=0,
    )

    with pytest.raises(PolicyContractError, match="ReplayReceipt"):
        issue_promotion_receipt(
            candidate,
            expected_generation=candidate.proposal_generation,
            replay="8" * 64,  # type: ignore[arg-type]
            review=review,
            canary=canary,
        )
