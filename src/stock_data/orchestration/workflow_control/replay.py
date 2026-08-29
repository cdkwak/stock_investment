"""Deterministic offline replay for immutable workflow-policy proposals."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from stock_data.orchestration.workflow_control.contracts import EventKind, WorkflowEvent
from stock_data.orchestration.workflow_control.policy import (
    AcceptedWorkflowSnapshot,
    PolicyContractError,
    PolicyProposal,
    canonical_event_snapshot,
)


REPLAY_SCHEMA_VERSION = "workflow-policy-replay/v1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayReceipt:
    proposal_generation: str
    snapshot_generation: str
    events_digest: str
    event_count: int
    kind_counts: tuple[tuple[str, int], ...]
    passed: bool
    reason_code: str
    receipt_digest: str
    schema_version: str = REPLAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REPLAY_SCHEMA_VERSION:
            raise PolicyContractError("unsupported replay receipt schema")
        for name in (
            "proposal_generation", "snapshot_generation", "events_digest",
            "receipt_digest",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
                raise PolicyContractError(f"{name} must be a SHA-256 digest")
        if not isinstance(self.event_count, int) or isinstance(self.event_count, bool) or self.event_count < 0:
            raise PolicyContractError("replay event_count must be a non-negative integer")
        if not isinstance(self.passed, bool):
            raise PolicyContractError("replay passed must be boolean")
        if sum(count for _, count in self.kind_counts) != self.event_count:
            raise PolicyContractError("replay kind counts do not match event_count")
        if self.receipt_digest != _digest(
            self.to_dict(include_receipt_digest=False)
        ):
            raise PolicyContractError("replay receipt digest does not match content")

    def to_dict(self, *, include_receipt_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "proposal_generation": self.proposal_generation,
            "snapshot_generation": self.snapshot_generation,
            "events_digest": self.events_digest,
            "event_count": self.event_count,
            "kind_counts": [list(item) for item in self.kind_counts],
            "passed": self.passed,
            "reason_code": self.reason_code,
        }
        if include_receipt_digest:
            result["receipt_digest"] = self.receipt_digest
        return result


def replay_policy(
    proposal: PolicyProposal,
    events: Iterable[WorkflowEvent],
    *,
    expected_generation: str,
) -> ReplayReceipt:
    """Replay only the proposal's accepted snapshot and return a pure receipt."""

    if expected_generation != proposal.proposal_generation:
        raise PolicyContractError("stale proposal generation")
    materialized = tuple(events)
    event_ids, events_digest = canonical_event_snapshot(materialized)
    accepted: AcceptedWorkflowSnapshot = proposal.snapshot
    if event_ids != accepted.event_ids or events_digest != accepted.events_digest:
        raise PolicyContractError("replay events do not match accepted snapshot")

    counts = {kind.value: 0 for kind in EventKind}
    for event in materialized:
        counts[event.kind.value] += 1
    kind_counts = tuple(sorted((key, value) for key, value in counts.items() if value))
    material: dict[str, Any] = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "proposal_generation": proposal.proposal_generation,
        "snapshot_generation": accepted.generation,
        "events_digest": events_digest,
        "event_count": len(event_ids),
        "kind_counts": [list(item) for item in kind_counts],
        "passed": True,
        "reason_code": "ACCEPTED_SNAPSHOT_REPLAYED",
    }
    return ReplayReceipt(
        proposal_generation=proposal.proposal_generation,
        snapshot_generation=accepted.generation,
        events_digest=events_digest,
        event_count=len(event_ids),
        kind_counts=kind_counts,
        passed=True,
        reason_code="ACCEPTED_SNAPSHOT_REPLAYED",
        receipt_digest=_digest(material),
    )
