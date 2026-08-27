from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class FeatureFamilyStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True)
class FeatureFamilyStep:
    family: str
    included_families: tuple[str, ...]
    status: FeatureFamilyStatus
    reason: str


FAMILY_ORDER = ("PRICE", "VOLATILITY", "FX", "BREADTH", "FLOW", "DERIVATIVES")


def build_ablation_plan(
    family_status: Mapping[str, FeatureFamilyStatus],
) -> tuple[FeatureFamilyStep, ...]:
    """Create the frozen cumulative family order without substituting blocked inputs."""
    unknown = set(family_status) - set(FAMILY_ORDER)
    if unknown:
        raise ValueError(f"unknown feature families: {sorted(unknown)}")
    steps: list[FeatureFamilyStep] = []
    included: list[str] = []
    blocked_upstream = False
    for family in FAMILY_ORDER:
        status = family_status.get(family, FeatureFamilyStatus.NOT_AVAILABLE)
        if family == "PRICE" and status is not FeatureFamilyStatus.AVAILABLE:
            raise ValueError("PRICE baseline must be available")
        if status is FeatureFamilyStatus.AVAILABLE and not blocked_upstream:
            included.append(family)
            reason = "EVALUATION_ELIGIBLE"
        else:
            blocked_upstream = True
            reason = (
                "PIT_OR_INPUT_GATE_OPEN"
                if status is FeatureFamilyStatus.BLOCKED
                else "INPUT_NOT_AVAILABLE"
            )
        steps.append(FeatureFamilyStep(family, tuple(included), status, reason))
    return tuple(steps)


__all__ = ["FAMILY_ORDER", "FeatureFamilyStatus", "FeatureFamilyStep", "build_ablation_plan"]
