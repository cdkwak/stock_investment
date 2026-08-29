"""Immutable, offline workflow-policy lifecycle contracts.

This module evaluates evidence and produces receipts only.  It intentionally
contains no scheduler, Queue, provider, account, or production mutation path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from stock_data.orchestration.workflow_control.contracts import WorkflowEvent
from stock_data.orchestration.workflow_control.events import canonical_event_json

if TYPE_CHECKING:
    from stock_data.orchestration.workflow_control.replay import ReplayReceipt


POLICY_SCHEMA_VERSION = "workflow-policy/v1"
SNAPSHOT_SCHEMA_VERSION = "workflow-policy-snapshot/v1"
REVIEW_SCHEMA_VERSION = "workflow-policy-review/v1"
CANARY_SCHEMA_VERSION = "workflow-policy-canary/v1"
PROMOTION_SCHEMA_VERSION = "workflow-policy-promotion/v1"
ROLLBACK_SCHEMA_VERSION = "workflow-policy-rollback/v1"

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[a-z][a-z0-9-]{1,31}/v[1-9][0-9]*(?:\.[0-9]+){0,2}$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class PolicyContractError(ValueError):
    """Raised when policy evidence is malformed, stale, or inconsistent."""


class AuthorityTier(StrEnum):
    LOCAL = "LOCAL"
    REVIEWED_STANDING = "REVIEWED_STANDING"
    PROHIBITED = "PROHIBITED"


class ActionClass(StrEnum):
    OFFLINE_REPLAY = "OFFLINE_REPLAY"
    POLICY_PROPOSAL = "POLICY_PROPOSAL"
    BOUNDED_CANARY = "BOUNDED_CANARY"
    PRODUCTION_PROMOTION = "PRODUCTION_PROMOTION"
    ROLLBACK = "ROLLBACK"
    ACCOUNT_READ = "ACCOUNT_READ"
    STANDING_AUTHORITY = "STANDING_AUTHORITY"
    BROKER_MUTATION = "BROKER_MUTATION"
    ORDER_MUTATION = "ORDER_MUTATION"
    TRANSFER_WITHDRAWAL = "TRANSFER_WITHDRAWAL"
    FINANCIAL_MUTATION = "FINANCIAL_MUTATION"
    ACCESS_CONTROL = "ACCESS_CONTROL"
    SECRET_HANDLING = "SECRET_HANDLING"
    PAID_SERVICE = "PAID_SERVICE"
    DESTRUCTIVE_MIGRATION = "DESTRUCTIVE_MIGRATION"


class ReceiptDecision(StrEnum):
    APPROVED = "APPROVED"
    REFUSED = "REFUSED"


class ReviewDecision(StrEnum):
    PASS = "PASS"
    FIX = "FIX"


class CanaryDecision(StrEnum):
    PASSED = "PASSED"
    REFUSED = "REFUSED"


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise PolicyContractError(f"{name} must be a SHA-256 digest")
    return value


def _require_version(value: object, name: str) -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise PolicyContractError(f"{name} must be a versioned identifier")
    return value


def _require_reason(value: object) -> str:
    if not isinstance(value, str) or _REASON.fullmatch(value) is None:
        raise PolicyContractError("reason_code must be a bounded symbolic code")
    return value


def canonical_event_snapshot(events: Iterable[WorkflowEvent]) -> tuple[tuple[str, ...], str]:
    """Return stable event IDs and a digest independent of input order."""

    ordered = sorted(events, key=lambda event: event.sort_key)
    event_ids = tuple(event.event_id for event in ordered)
    if len(event_ids) != len(set(event_ids)):
        raise PolicyContractError("accepted snapshot contains duplicate event ids")
    body = "".join(f"{canonical_event_json(event)}\n" for event in ordered)
    return event_ids, _digest_text(body)


@dataclass(frozen=True, slots=True)
class AcceptedWorkflowSnapshot:
    """Digest-bound evidence explicitly accepted before proposal creation."""

    generation: str
    acceptance_receipt_digest: str
    event_ids: tuple[str, ...]
    events_digest: str
    event_count: int
    schema_version: str = SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise PolicyContractError("unsupported workflow snapshot schema")
        _require_digest(self.generation, "snapshot generation")
        _require_digest(self.acceptance_receipt_digest, "snapshot acceptance receipt")
        _require_digest(self.events_digest, "snapshot events digest")
        if not isinstance(self.event_ids, tuple) or any(
            not isinstance(item, str) or not item for item in self.event_ids
        ):
            raise PolicyContractError("snapshot event_ids must be non-empty strings")
        if len(self.event_ids) != len(set(self.event_ids)):
            raise PolicyContractError("snapshot event_ids must be unique")
        if not isinstance(self.event_count, int) or isinstance(self.event_count, bool):
            raise PolicyContractError("snapshot event_count must be an integer")
        if self.event_count != len(self.event_ids):
            raise PolicyContractError("snapshot event_count does not match event_ids")

    @classmethod
    def accept(
        cls,
        events: Iterable[WorkflowEvent],
        *,
        generation: str,
        acceptance_receipt_digest: str,
    ) -> "AcceptedWorkflowSnapshot":
        event_ids, events_digest = canonical_event_snapshot(events)
        return cls(
            generation=generation,
            acceptance_receipt_digest=acceptance_receipt_digest,
            event_ids=event_ids,
            events_digest=events_digest,
            event_count=len(event_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "acceptance_receipt_digest": self.acceptance_receipt_digest,
            "event_ids": list(self.event_ids),
            "events_digest": self.events_digest,
            "event_count": self.event_count,
        }


@dataclass(frozen=True, slots=True)
class CanaryCriteria:
    """A bounded canary is disabled unless a proposal explicitly enables it."""

    enabled: bool = False
    required_observations: int = 1
    maximum_observations: int = 100
    maximum_failures: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise PolicyContractError("canary enabled must be boolean")
        for name in ("required_observations", "maximum_observations", "maximum_failures"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise PolicyContractError(f"canary {name} must be a non-negative integer")
        if not 1 <= self.required_observations <= self.maximum_observations <= 10_000:
            raise PolicyContractError("canary observation bounds are invalid")
        if self.maximum_failures > self.maximum_observations:
            raise PolicyContractError("canary maximum_failures exceeds its bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "required_observations": self.required_observations,
            "maximum_observations": self.maximum_observations,
            "maximum_failures": self.maximum_failures,
        }


@dataclass(frozen=True, slots=True)
class PolicyProposal:
    policy_version: str
    base_policy_version: str
    snapshot: AcceptedWorkflowSnapshot
    canary: CanaryCriteria
    implementation_fingerprint: str
    proposal_generation: str
    schema_version: str = POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != POLICY_SCHEMA_VERSION:
            raise PolicyContractError("unsupported policy proposal schema")
        _require_version(self.policy_version, "policy_version")
        _require_version(self.base_policy_version, "base_policy_version")
        if self.policy_version == self.base_policy_version:
            raise PolicyContractError("proposal must advance the policy version")
        if not isinstance(self.snapshot, AcceptedWorkflowSnapshot):
            raise PolicyContractError("proposal requires an accepted workflow snapshot")
        if not isinstance(self.canary, CanaryCriteria):
            raise PolicyContractError("proposal requires bounded canary criteria")
        _require_digest(self.implementation_fingerprint, "implementation fingerprint")
        _require_digest(self.proposal_generation, "proposal generation")
        if self.proposal_generation != self.computed_generation():
            raise PolicyContractError("proposal generation does not match immutable content")

    @classmethod
    def create(
        cls,
        *,
        policy_version: str,
        base_policy_version: str,
        snapshot: AcceptedWorkflowSnapshot,
        implementation_fingerprint: str,
        canary: CanaryCriteria | None = None,
    ) -> "PolicyProposal":
        criteria = canary or CanaryCriteria()
        material = {
            "schema_version": POLICY_SCHEMA_VERSION,
            "policy_version": policy_version,
            "base_policy_version": base_policy_version,
            "snapshot": snapshot.to_dict(),
            "canary": criteria.to_dict(),
            "implementation_fingerprint": implementation_fingerprint,
        }
        return cls(
            policy_version=policy_version,
            base_policy_version=base_policy_version,
            snapshot=snapshot,
            canary=criteria,
            implementation_fingerprint=implementation_fingerprint,
            proposal_generation=_digest_text(_canonical_json(material)),
        )

    def generation_material(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "base_policy_version": self.base_policy_version,
            "snapshot": self.snapshot.to_dict(),
            "canary": self.canary.to_dict(),
            "implementation_fingerprint": self.implementation_fingerprint,
        }

    def computed_generation(self) -> str:
        return _digest_text(_canonical_json(self.generation_material()))


@dataclass(frozen=True, slots=True)
class IndependentReviewReceipt:
    proposal_generation: str
    implementation_fingerprint: str
    reviewer_fingerprint: str
    decision: ReviewDecision
    manifest_digest: str
    receipt_digest: str
    schema_version: str = REVIEW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REVIEW_SCHEMA_VERSION:
            raise PolicyContractError("unsupported review receipt schema")
        for name in (
            "proposal_generation", "implementation_fingerprint",
            "reviewer_fingerprint", "manifest_digest", "receipt_digest",
        ):
            _require_digest(getattr(self, name), name)
        if self.implementation_fingerprint == self.reviewer_fingerprint:
            raise PolicyContractError("reviewer must be independent from implementation")
        if not isinstance(self.decision, ReviewDecision):
            raise PolicyContractError("review decision must use ReviewDecision")
        if self.receipt_digest != self.computed_digest():
            raise PolicyContractError("review receipt digest does not match content")

    @classmethod
    def issue(
        cls,
        proposal: PolicyProposal,
        *,
        reviewer_fingerprint: str,
        decision: ReviewDecision,
        manifest_digest: str,
    ) -> "IndependentReviewReceipt":
        material = {
            "schema_version": REVIEW_SCHEMA_VERSION,
            "proposal_generation": proposal.proposal_generation,
            "implementation_fingerprint": proposal.implementation_fingerprint,
            "reviewer_fingerprint": reviewer_fingerprint,
            "decision": decision.value,
            "manifest_digest": manifest_digest,
        }
        return cls(receipt_digest=_digest_text(_canonical_json(material)), **{
            **material,
            "decision": decision,
        })

    def computed_digest(self) -> str:
        return _digest_text(_canonical_json({
            "schema_version": self.schema_version,
            "proposal_generation": self.proposal_generation,
            "implementation_fingerprint": self.implementation_fingerprint,
            "reviewer_fingerprint": self.reviewer_fingerprint,
            "decision": self.decision.value,
            "manifest_digest": self.manifest_digest,
        }))


@dataclass(frozen=True, slots=True)
class AuthorityDecisionReceipt:
    action: ActionClass
    tier: AuthorityTier
    decision: ReceiptDecision
    reason_code: str


_AUTHORITY_TIERS: Mapping[ActionClass, AuthorityTier] = {
    ActionClass.OFFLINE_REPLAY: AuthorityTier.LOCAL,
    ActionClass.POLICY_PROPOSAL: AuthorityTier.LOCAL,
    ActionClass.BOUNDED_CANARY: AuthorityTier.REVIEWED_STANDING,
    ActionClass.PRODUCTION_PROMOTION: AuthorityTier.REVIEWED_STANDING,
    ActionClass.ROLLBACK: AuthorityTier.REVIEWED_STANDING,
    ActionClass.ACCOUNT_READ: AuthorityTier.REVIEWED_STANDING,
    ActionClass.STANDING_AUTHORITY: AuthorityTier.REVIEWED_STANDING,
    ActionClass.BROKER_MUTATION: AuthorityTier.PROHIBITED,
    ActionClass.ORDER_MUTATION: AuthorityTier.PROHIBITED,
    ActionClass.TRANSFER_WITHDRAWAL: AuthorityTier.PROHIBITED,
    ActionClass.FINANCIAL_MUTATION: AuthorityTier.PROHIBITED,
    ActionClass.ACCESS_CONTROL: AuthorityTier.PROHIBITED,
    ActionClass.SECRET_HANDLING: AuthorityTier.PROHIBITED,
    ActionClass.PAID_SERVICE: AuthorityTier.PROHIBITED,
    ActionClass.DESTRUCTIVE_MIGRATION: AuthorityTier.PROHIBITED,
}


def evaluate_authority(
    action: ActionClass,
    *,
    independent_review_passed: bool = False,
    standing_authority: bool = False,
) -> AuthorityDecisionReceipt:
    """Evaluate one action; unknown input and unreviewed standing work fail closed."""

    if not isinstance(action, ActionClass):
        raise PolicyContractError("unknown action class fails closed")
    if not isinstance(independent_review_passed, bool):
        raise PolicyContractError("independent_review_passed must be boolean")
    if not isinstance(standing_authority, bool):
        raise PolicyContractError("standing_authority must be boolean")
    tier = _AUTHORITY_TIERS[action]
    if tier is AuthorityTier.PROHIBITED:
        return AuthorityDecisionReceipt(action, tier, ReceiptDecision.REFUSED, "PROHIBITED_ACTION")
    if tier is AuthorityTier.REVIEWED_STANDING and not independent_review_passed:
        return AuthorityDecisionReceipt(action, tier, ReceiptDecision.REFUSED, "INDEPENDENT_REVIEW_REQUIRED")
    if tier is AuthorityTier.REVIEWED_STANDING and not standing_authority:
        return AuthorityDecisionReceipt(action, tier, ReceiptDecision.REFUSED, "STANDING_AUTHORITY_REQUIRED")
    return AuthorityDecisionReceipt(action, tier, ReceiptDecision.APPROVED, "AUTHORITY_CONFIRMED")


@dataclass(frozen=True, slots=True)
class CanaryReceipt:
    proposal_generation: str
    decision: CanaryDecision
    observed: int
    failures: int
    reason_code: str
    receipt_digest: str
    schema_version: str = CANARY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CANARY_SCHEMA_VERSION:
            raise PolicyContractError("unsupported canary receipt schema")
        _require_digest(self.proposal_generation, "proposal generation")
        _require_digest(self.receipt_digest, "canary receipt")
        _require_reason(self.reason_code)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (self.observed, self.failures)):
            raise PolicyContractError("canary counts must be non-negative integers")
        if self.failures > self.observed:
            raise PolicyContractError("canary failures cannot exceed observations")
        material = {
            "schema_version": self.schema_version,
            "proposal_generation": self.proposal_generation,
            "decision": self.decision.value,
            "observed": self.observed,
            "failures": self.failures,
            "reason_code": self.reason_code,
        }
        if self.receipt_digest != _digest_text(_canonical_json(material)):
            raise PolicyContractError("canary receipt digest does not match content")


def evaluate_canary(
    proposal: PolicyProposal,
    *,
    expected_generation: str,
    observed: int,
    failures: int,
) -> CanaryReceipt:
    if expected_generation != proposal.proposal_generation:
        raise PolicyContractError("stale proposal generation")
    criteria = proposal.canary
    if not isinstance(observed, int) or isinstance(observed, bool) or observed < 0:
        raise PolicyContractError("observed must be a non-negative integer")
    if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
        raise PolicyContractError("failures must be a non-negative integer")
    if failures > observed:
        raise PolicyContractError("canary failures cannot exceed observations")
    if not criteria.enabled:
        decision, reason = CanaryDecision.REFUSED, "CANARY_DISABLED"
    elif observed > criteria.maximum_observations:
        decision, reason = CanaryDecision.REFUSED, "CANARY_BOUND_EXCEEDED"
    elif observed < criteria.required_observations:
        decision, reason = CanaryDecision.REFUSED, "CANARY_OBSERVATIONS_INCOMPLETE"
    elif failures > criteria.maximum_failures:
        decision, reason = CanaryDecision.REFUSED, "CANARY_FAILURE_LIMIT"
    else:
        decision, reason = CanaryDecision.PASSED, "CANARY_CRITERIA_MET"
    material = {
        "schema_version": CANARY_SCHEMA_VERSION,
        "proposal_generation": proposal.proposal_generation,
        "decision": decision.value,
        "observed": observed,
        "failures": failures,
        "reason_code": reason,
    }
    return CanaryReceipt(receipt_digest=_digest_text(_canonical_json(material)), **{
        **material,
        "decision": decision,
    })


@dataclass(frozen=True, slots=True)
class PromotionReceipt:
    proposal_generation: str
    policy_version: str
    decision: ReceiptDecision
    reason_code: str
    replay_receipt_digest: str
    review_receipt_digest: str
    canary_receipt_digest: str
    receipt_digest: str
    production_mutated: bool = False
    schema_version: str = PROMOTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROMOTION_SCHEMA_VERSION:
            raise PolicyContractError("unsupported promotion receipt schema")
        for name in (
            "proposal_generation", "replay_receipt_digest", "review_receipt_digest",
            "canary_receipt_digest", "receipt_digest",
        ):
            _require_digest(getattr(self, name), name)
        _require_version(self.policy_version, "policy_version")
        _require_reason(self.reason_code)
        if self.production_mutated:
            raise PolicyContractError("policy evaluation cannot mutate production")
        material = {
            "schema_version": self.schema_version,
            "proposal_generation": self.proposal_generation,
            "policy_version": self.policy_version,
            "decision": self.decision.value,
            "reason_code": self.reason_code,
            "replay_receipt_digest": self.replay_receipt_digest,
            "review_receipt_digest": self.review_receipt_digest,
            "canary_receipt_digest": self.canary_receipt_digest,
            "production_mutated": self.production_mutated,
        }
        if self.receipt_digest != _digest_text(_canonical_json(material)):
            raise PolicyContractError("promotion receipt digest does not match content")


def issue_promotion_receipt(
    proposal: PolicyProposal,
    *,
    expected_generation: str,
    replay: "ReplayReceipt",
    review: IndependentReviewReceipt,
    canary: CanaryReceipt,
) -> PromotionReceipt:
    from stock_data.orchestration.workflow_control.replay import ReplayReceipt

    if expected_generation != proposal.proposal_generation:
        raise PolicyContractError("stale proposal generation")
    if not isinstance(replay, ReplayReceipt):
        raise PolicyContractError("promotion requires a ReplayReceipt")
    failures: list[str] = []
    if review.proposal_generation != proposal.proposal_generation:
        failures.append("STALE_REVIEW")
    elif review.decision is not ReviewDecision.PASS:
        failures.append("REVIEW_NOT_PASSED")
    if replay.proposal_generation != proposal.proposal_generation:
        failures.append("STALE_REPLAY")
    elif (
        replay.snapshot_generation != proposal.snapshot.generation
        or replay.events_digest != proposal.snapshot.events_digest
        or replay.event_count != proposal.snapshot.event_count
    ):
        failures.append("REPLAY_SNAPSHOT_MISMATCH")
    elif not replay.passed:
        failures.append("REPLAY_NOT_PASSED")
    if canary.proposal_generation != proposal.proposal_generation:
        failures.append("STALE_CANARY")
    elif canary.decision is not CanaryDecision.PASSED:
        failures.append("CANARY_NOT_PASSED")
    decision = ReceiptDecision.REFUSED if failures else ReceiptDecision.APPROVED
    reason = failures[0] if failures else "PROMOTION_CRITERIA_MET"
    material = {
        "schema_version": PROMOTION_SCHEMA_VERSION,
        "proposal_generation": proposal.proposal_generation,
        "policy_version": proposal.policy_version,
        "decision": decision.value,
        "reason_code": reason,
        "replay_receipt_digest": replay.receipt_digest,
        "review_receipt_digest": review.receipt_digest,
        "canary_receipt_digest": canary.receipt_digest,
        "production_mutated": False,
    }
    return PromotionReceipt(receipt_digest=_digest_text(_canonical_json(material)), **{
        **material,
        "decision": decision,
    })


@dataclass(frozen=True, slots=True)
class RollbackReceipt:
    promoted_policy_version: str
    target_policy_version: str
    proposal_generation: str
    decision: ReceiptDecision
    reason_code: str
    promotion_receipt_digest: str
    review_receipt_digest: str
    receipt_digest: str
    production_mutated: bool = False
    schema_version: str = ROLLBACK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ROLLBACK_SCHEMA_VERSION:
            raise PolicyContractError("unsupported rollback receipt schema")
        _require_version(self.promoted_policy_version, "promoted policy version")
        _require_version(self.target_policy_version, "target policy version")
        for name in (
            "proposal_generation", "promotion_receipt_digest",
            "review_receipt_digest", "receipt_digest",
        ):
            _require_digest(getattr(self, name), name)
        _require_reason(self.reason_code)
        if self.production_mutated:
            raise PolicyContractError("rollback evaluation cannot mutate production")
        material = {
            "schema_version": self.schema_version,
            "promoted_policy_version": self.promoted_policy_version,
            "target_policy_version": self.target_policy_version,
            "proposal_generation": self.proposal_generation,
            "decision": self.decision.value,
            "reason_code": self.reason_code,
            "promotion_receipt_digest": self.promotion_receipt_digest,
            "review_receipt_digest": self.review_receipt_digest,
            "production_mutated": self.production_mutated,
        }
        if self.receipt_digest != _digest_text(_canonical_json(material)):
            raise PolicyContractError("rollback receipt digest does not match content")


def issue_rollback_receipt(
    proposal: PolicyProposal,
    promotion: PromotionReceipt,
    review: IndependentReviewReceipt,
    *,
    expected_generation: str,
    reason_code: str,
) -> RollbackReceipt:
    if expected_generation != proposal.proposal_generation:
        raise PolicyContractError("stale proposal generation")
    _require_reason(reason_code)
    approved = (
        promotion.proposal_generation == proposal.proposal_generation
        and promotion.decision is ReceiptDecision.APPROVED
        and review.proposal_generation == proposal.proposal_generation
        and review.decision is ReviewDecision.PASS
    )
    decision = ReceiptDecision.APPROVED if approved else ReceiptDecision.REFUSED
    decision_reason = reason_code if approved else "ROLLBACK_EVIDENCE_INCOMPLETE"
    material = {
        "schema_version": ROLLBACK_SCHEMA_VERSION,
        "promoted_policy_version": proposal.policy_version,
        "target_policy_version": proposal.base_policy_version,
        "proposal_generation": proposal.proposal_generation,
        "decision": decision.value,
        "reason_code": decision_reason,
        "promotion_receipt_digest": promotion.receipt_digest,
        "review_receipt_digest": review.receipt_digest,
        "production_mutated": False,
    }
    return RollbackReceipt(receipt_digest=_digest_text(_canonical_json(material)), **{
        **material,
        "decision": decision,
    })
