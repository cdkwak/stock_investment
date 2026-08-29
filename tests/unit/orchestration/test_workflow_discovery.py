from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from stock_data.orchestration.workflow_control.discovery import (
    DiscoveryError,
    DiscoveryRegistrar,
    LocalNewDiscoverySink,
    ReportedFinding,
    RequestQueueNewDiscoverySink,
    validate_finding,
)


TASK = "RQ-20260829T093730-C118"
LEAD = "workflow_cutover_lead_20260829"
GENERATION = "generation-a"
ACTIVE_SCOPE = (
    "src/stock_data/orchestration/workflow_control/controller.py",
    "src/stock_data/orchestration/workflow_control/discovery.py",
)


def finding(
    role: str = "worker",
    *,
    scope: str = "src/stock_data/new_boundary.py",
    fingerprint: str = "workflow-discovery:separate-boundary:v1",
) -> ReportedFinding:
    return ReportedFinding(
        source_task=TASK,
        reported_by_role=role,
        lead_owner=LEAD,
        lead_generation=GENERATION,
        title="Separate reproducible workflow boundary defect",
        fingerprint=fingerprint,
        symptom="A disjoint boundary rejects a canonical fixture.",
        evidence="The same fixture fails twice under deterministic replay.",
        impact="The separate boundary remains unavailable.",
        suspected_scope=scope,
        reproduce="Run the owning focused fixture twice.",
    )


@pytest.mark.parametrize("reported_by", ("worker", "reviewer", "lead"))
def test_lead_validation_preserves_reporter_and_keeps_candidate_non_executable(
    reported_by: str,
) -> None:
    candidate = validate_finding(
        finding(reported_by),
        validated_by=LEAD,
        expected_generation=GENERATION,
        active_write_scope=ACTIVE_SCOPE,
    )

    assert candidate.intake_role == "lead"
    assert candidate.finding.reported_by_role == reported_by
    assert candidate.state == "new"
    assert not candidate.executable


def test_validation_rejects_stale_wrong_lead_and_in_scope_expansion() -> None:
    with pytest.raises(DiscoveryError, match="stale"):
        validate_finding(
            finding(), validated_by=LEAD, expected_generation="older",
            active_write_scope=ACTIVE_SCOPE,
        )
    with pytest.raises(DiscoveryError, match="routed Lead"):
        validate_finding(
            finding(), validated_by="different_lead", expected_generation=GENERATION,
            active_write_scope=ACTIVE_SCOPE,
        )
    with pytest.raises(DiscoveryError, match="rework"):
        validate_finding(
            finding(scope="src/stock_data/orchestration/workflow_control/controller.py"),
            validated_by=LEAD, expected_generation=GENERATION,
            active_write_scope=ACTIVE_SCOPE,
        )


def test_local_registration_is_idempotent_and_never_promotes() -> None:
    candidate = validate_finding(
        finding(), validated_by=LEAD, expected_generation=GENERATION,
        active_write_scope=ACTIVE_SCOPE,
    )
    registrar = DiscoveryRegistrar(LocalNewDiscoverySink())

    first = registrar.register(candidate)
    duplicate = registrar.register(candidate)

    assert first.state == duplicate.state == "new"
    assert not first.executable and not duplicate.executable
    assert first.reported_by_role == "worker"
    assert not first.duplicate and duplicate.duplicate
    assert first.receipt_digest == duplicate.receipt_digest


def test_conflicting_duplicate_fingerprint_is_rejected() -> None:
    sink = LocalNewDiscoverySink()
    first = validate_finding(
        finding(), validated_by=LEAD, expected_generation=GENERATION,
        active_write_scope=ACTIVE_SCOPE,
    )
    second = validate_finding(
        finding(scope="src/stock_data/another_boundary.py"),
        validated_by=LEAD, expected_generation=GENERATION,
        active_write_scope=ACTIVE_SCOPE,
    )
    registrar = DiscoveryRegistrar(sink)
    registrar.register(first)

    with pytest.raises(DiscoveryError, match="conflicts"):
        registrar.register(second)


@pytest.mark.parametrize("reported_by", ("reviewer", "lead"))
def test_canonical_sink_uses_only_discover_with_exact_provenance(
    tmp_path: Path, reported_by: str,
) -> None:
    script = tmp_path / "scripts" / "request_queue.py"
    script.parent.mkdir(parents=True)
    script.write_text("# fixture\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "RQ-NEW\n", "")

    candidate = validate_finding(
        finding(reported_by), validated_by=LEAD, expected_generation=GENERATION,
        active_write_scope=ACTIVE_SCOPE,
    )
    receipt = DiscoveryRegistrar(
        RequestQueueNewDiscoverySink(tmp_path, run=fake_run)
    ).register(candidate)

    command = calls[0]
    assert command[2] == "discover"
    assert command[command.index("--intake-role") + 1] == "lead"
    assert command[command.index("--reported-by-role") + 1] == reported_by
    assert "triage" not in command and "claim" not in command
    assert receipt.state == "new" and not receipt.executable
