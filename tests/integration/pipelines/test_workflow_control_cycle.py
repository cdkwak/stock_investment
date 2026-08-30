from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from stock_data.orchestration.workflow_control.cycle import (
    CycleScenario,
    OperationalCycleCanary,
)
from stock_data.orchestration.workflow_control.runner import RunnerAction


T0 = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)


def test_ten_orca_free_full_cycles_cover_normal_and_recovery_paths(
    tmp_path: Path,
) -> None:
    scenarios = tuple(CycleScenario)
    assert scenarios == (
        CycleScenario.HAPPY_PATH,
        CycleScenario.DUPLICATE_CLAIM,
        CycleScenario.HEARTBEAT_RENEWAL,
        CycleScenario.LEASE_EXPIRY_RECOVERY,
        CycleScenario.STALE_GENERATION,
        CycleScenario.WORKER_CRASH_RETRY,
        CycleScenario.QUESTION_WAKEUP,
        CycleScenario.REVIEW_SNAPSHOT_ISOLATION,
        CycleScenario.IDEMPOTENT_REPLAY,
        CycleScenario.ORCA_ABSENT,
    )

    canary = OperationalCycleCanary(tmp_path / "control-plane", started_at=T0)
    receipts = [
        canary.run(index, scenario)
        for index, scenario in enumerate(scenarios, start=1)
    ]

    assert len(receipts) == 10
    assert all(receipt.final_state.value == "done" for receipt in receipts)
    assert all(receipt.event_count == 5 for receipt in receipts)
    assert all(receipt.scenario_verified for receipt in receipts)
    assert all(not receipt.orca_used for receipt in receipts)
    assert all(not receipt.production_mutated for receipt in receipts)
    assert receipts[0].role_session_reused is False
    assert all(receipt.role_session_reused for receipt in receipts[1:])
    assert len({receipt.task_id for receipt in receipts}) == 10
    assert len({receipt.receipt_digest for receipt in receipts}) == 10
    assert len({receipt.review_snapshot_digest for receipt in receipts}) == 10

    assert canary.state.event_count() == 50
    ledger_lines = (canary.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 50
    assert all(json.loads(line)["event_id"] for line in ledger_lines)

    projection = (canary.root / "WORKFLOW_STATE.md").read_text(encoding="utf-8")
    assert projection.count("| done |") == 10
    assert "Orca" not in projection

    lead = canary.registry.get("lead_infra")
    assert lead.state.value == "idle"
    assert lead.identity.codex_session_id == "direct-lead-session"
    assert lead.identity.active_task_id is None
    assert lead.identity.active_dispatch_id is None

    worker_retry_receipts = tuple(
        item for item in canary.runner.receipts if item.role_key == "worker_infra"
    )
    assert {item.task_id for item in worker_retry_receipts} == {receipts[5].task_id}
    assert tuple(item.action for item in worker_retry_receipts) == (
        RunnerAction.SETTLE,
        RunnerAction.RESUME,
    )
    all_boundary_receipts = canary.runner.receipts + canary.session_runner.receipts
    assert all(not item.orca_used for item in all_boundary_receipts)
    assert all(not item.production_mutated for item in all_boundary_receipts)
