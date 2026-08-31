"""Operate the injected Python PM controller service without an Orca fallback."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from stock_data.orchestration.workflow_control.contracts import WorkflowEvent
from stock_data.orchestration.workflow_control.codex_boundary import (
    CodexBoundaryError,
    CodexCliBoundary,
    CodexProcessEventPins,
)
from stock_data.orchestration.workflow_control.controller import (
    WorkflowController,
    WorkflowControllerError,
)
from stock_data.orchestration.workflow_control.event_runner import EventRunnerError
from stock_data.orchestration.workflow_control.production import (
    build_production_service,
    canonical_control_root,
    canonical_repository_root,
)
from stock_data.orchestration.workflow_control.service import (
    ControllerServiceError,
    ServiceMode,
    WorkflowControllerService,
    verify_phase_a_queue_evidence as _verify_phase_a_queue_evidence,
)
from stock_data.orchestration.workflow_control.registry import (
    RoleIdentity,
    RoleKind,
    RoleRegistry,
    RoleRegistryError,
)
from stock_data.orchestration.workflow_control.queue_adapter import QueueAdapterError
from stock_data.orchestration.workflow_control.runner import (
    InjectedDirectRunner,
    RunnerAction,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed Python PM workflow controller service."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository that owns the single canonical Python-PM state root",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser(ServiceMode.STATUS.value, help="show durable controller status")
    terminal_status = subparsers.add_parser(
        "terminal-operation-status",
        help="show one exact sanitized failed boundary operation",
    )
    terminal_status.add_argument("--boundary-operation-id", required=True)
    process_event_status = subparsers.add_parser(
        "process-event-status",
        help="replay one exact sanitized terminal process-event receipt",
    )
    process_event_status.add_argument("--boundary-operation-id", required=True)
    process_event_status.add_argument("--boundary-request-digest", required=True)
    process_event_status.add_argument("--generation-sequence", required=True, type=int)
    process_event_status.add_argument("--generation-digest", required=True)
    process_event_status.add_argument("--execution-profile-digest", required=True)
    process_event_status.add_argument("--expected-receipt-digest")
    phase_boundary = subparsers.add_parser(
        "mark-task-replan-ready",
        help="PM-only evidence-bound transition from an accepted phase to replan",
    )
    phase_boundary.add_argument("--task-id", required=True)
    phase_boundary.add_argument("--expected-queue-generation", required=True)
    phase_boundary.add_argument("--expected-prior-contract-digest", required=True)
    phase_boundary.add_argument("--expected-phase-a-candidate-digest", required=True)
    phase_boundary.add_argument("--expected-phase-a-review-digest", required=True)
    phase_boundary.add_argument("--expected-prior-state", required=True)
    phase_boundary.add_argument("--reason-code", required=True)
    phase_boundary.add_argument("--pm-role-key", required=True)
    phase_boundary.add_argument("--pm-generation", required=True, type=int)
    phase_boundary_preflight = subparsers.add_parser(
        "preflight-task-replan-ready",
        help="read-only exact preflight for the PM phase-boundary transition",
    )
    phase_boundary_preflight.add_argument("--task-id", required=True)
    phase_boundary_preflight.add_argument("--expected-queue-generation", required=True)
    phase_boundary_preflight.add_argument("--expected-prior-contract-digest", required=True)
    phase_boundary_preflight.add_argument("--expected-phase-a-candidate-digest", required=True)
    phase_boundary_preflight.add_argument("--expected-phase-a-review-digest", required=True)
    phase_boundary_preflight.add_argument("--expected-prior-state", required=True)
    phase_boundary_preflight.add_argument("--reason-code", required=True)
    phase_boundary_preflight.add_argument("--pm-role-key", required=True)
    phase_boundary_preflight.add_argument("--pm-generation", required=True, type=int)
    phase_boundary_status = subparsers.add_parser(
        "phase-boundary-status",
        help="read one exact sanitized PM phase-boundary receipt",
    )
    phase_boundary_status.add_argument("--task-id", required=True)
    for mode in (ServiceMode.CANARY, ServiceMode.RUN):
        command = subparsers.add_parser(mode.value, help=f"execute direct Codex {mode.value} events")
        command.add_argument("--owner-id", required=True)
        command.add_argument("--events", type=Path, required=True, help="sanitized workflow JSON array")
    rollback = subparsers.add_parser(
        ServiceMode.ROLLBACK.value,
        help="fence one exact stale Python-PM writer",
    )
    rollback.add_argument("--owner-id", required=True, help="observed stale writer owner")
    rollback.add_argument("--generation-sequence", required=True, type=int)
    rollback.add_argument("--generation-digest", required=True)
    stranded = subparsers.add_parser(
        "recover-stranded",
        help="preflight or recover one exact dead writer and uncertain boundary operation",
    )
    stranded.add_argument("--owner-id", required=True)
    stranded.add_argument("--generation-sequence", required=True, type=int)
    stranded.add_argument("--generation-digest", required=True)
    stranded.add_argument("--boundary-operation-id", required=True)
    stranded.add_argument("--boundary-request-digest", required=True)
    stranded.add_argument(
        "--preflight-only",
        action="store_true",
        help="prove exact pins and process liveness without mutation",
    )
    terminal = subparsers.add_parser(
        "reconcile-terminal",
        help="preflight or receipt one exact naturally terminal failed writer",
    )
    terminal.add_argument("--owner-id", required=True)
    terminal.add_argument("--generation-sequence", required=True, type=int)
    terminal.add_argument("--generation-digest", required=True)
    terminal.add_argument("--boundary-operation-id", required=True)
    terminal.add_argument("--boundary-request-digest", required=True)
    terminal.add_argument("--boundary-error-code", required=True)
    terminal.add_argument("--release-reason", required=True)
    terminal.add_argument(
        "--preflight-only",
        action="store_true",
        help="prove exact terminal pins and OS liveness without mutation",
    )
    register = subparsers.add_parser(
        "bootstrap-role",
        help="launch and register one CLI-owned persistent PM or Lead session",
    )
    register.add_argument("--owner-id", required=True)
    register.add_argument("--role-key", required=True)
    register.add_argument(
        "--role-kind", required=True,
        choices=(RoleKind.PROJECT_MANAGER.value, RoleKind.DOMAIN_LEAD.value),
    )
    register.add_argument(
        "--binding-task-id",
        required=True,
        help="exact Queue task providing immutable bootstrap context",
    )
    register.add_argument("--parent-role-key")
    register.add_argument("--task-id")
    register.add_argument("--dispatch-id")
    register.add_argument(
        "--expected-coordination-session-sha256",
        help="exact sanitized app-session fingerprint required for migration",
    )
    register.add_argument(
        "--expected-coordination-generation",
        type=int,
        help="exact stored app-role generation required for migration",
    )
    register.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate the exact migration/bootstrap identity without launching",
    )
    register.add_argument(
        "--bootstrap-attempt",
        type=int,
        default=1,
        help="bounded idempotency generation for a failed pre-session launch",
    )
    refresh = subparsers.add_parser(
        "refresh-role",
        help="rotate one exact registered role generation and refresh its lease",
    )
    refresh.add_argument("--owner-id", required=True)
    refresh.add_argument("--role-key", required=True)
    refresh.add_argument("--expected-generation", required=True, type=int)
    replace_lead = subparsers.add_parser(
        "replace-app-coordination-lead",
        help="PM-only CAS replacement of one exact app-owned Lead session",
    )
    replace_lead.add_argument("--owner-id", required=True)
    replace_lead.add_argument("--pm-role-key", required=True)
    replace_lead.add_argument("--expected-pm-generation", required=True, type=int)
    replace_lead.add_argument("--role-key", required=True)
    replace_lead.add_argument("--expected-generation", required=True, type=int)
    replace_lead.add_argument("--expected-session-id", required=True)
    replace_lead.add_argument("--replacement-session-id", required=True)
    replace_lead.add_argument("--expected-task-id", required=True)
    replace_lead.add_argument("--expected-dispatch-id", required=True)
    replace_lead.add_argument("--expected-runtime-id", default="codex-app-local")
    replace_lead.add_argument("--expected-worktree-id", default="stock-investment-rev1-main")
    event_run = subparsers.add_parser(
        "event-run-once",
        help="consume at most one durable material Queue/Listener generation",
    )
    event_run.add_argument("--owner-id", required=True)
    subparsers.add_parser(
        "event-runner-status",
        help="show sanitized unattended event-runner settlement counts",
    )
    event_reconciliation = subparsers.add_parser(
        "event-reconciliation-status",
        help="read one exact failed event-runner generation without mutation",
    )
    event_reconciliation.add_argument("--material-generation", required=True)
    event_reconciliation.add_argument("--attempt-receipt-digest", required=True)
    event_recover = subparsers.add_parser(
        "event-recover-generation",
        help="preserve one failed pending generation and rotate a fresh namespace",
    )
    event_recover.add_argument("--material-generation", required=True)
    event_recover.add_argument("--attempt-receipt-digest", required=True)
    event_recover.add_argument(
        "--recovery-proof", "--stranded-recovery-proof",
        dest="recovery_proof", required=True,
    )
    return parser


def _events(path: Path) -> tuple[WorkflowEvent, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ControllerServiceError("events must be readable JSON") from error
    if not isinstance(payload, list):
        raise ControllerServiceError("events JSON must be an array")
    if not all(isinstance(item, dict) for item in payload):
        raise ControllerServiceError("each event must be a JSON object")
    return tuple(WorkflowEvent.from_dict(item) for item in payload)


def _role_identity(args: argparse.Namespace, session_id: str) -> RoleIdentity:
    role_kind = RoleKind(args.role_kind)
    parent = args.parent_role_key
    if role_kind is RoleKind.PROJECT_MANAGER and parent is not None:
        raise ValueError("project manager cannot have a parent role")
    if role_kind is not RoleKind.PROJECT_MANAGER and parent is None:
        raise ValueError("non-PM role requires a parent role")
    return RoleIdentity(
        role_key=args.role_key,
        role_kind=role_kind,
        codex_session_id=session_id,
        # The schema keeps a legacy transport identity column for compatibility;
        # this value explicitly denies that transport and is never executed.
        orca_run_id="transport-disabled",
        worktree_id="stock-investment-rev1-main",
        terminal_handle=None,
        runtime_id="codex-cli-owned-v1",
        active_task_id=args.task_id,
        active_dispatch_id=args.dispatch_id,
        parent_role_key=parent,
    )


def _bootstrap_generation(args: argparse.Namespace) -> str:
    return hashlib.sha256(json.dumps(
        {
            "binding_task_id": args.binding_task_id,
            "bootstrap_attempt": args.bootstrap_attempt,
            "coordination_session_sha256": args.expected_coordination_session_sha256,
            "coordination_generation": args.expected_coordination_generation,
            "parent_role_key": args.parent_role_key,
            "role_key": args.role_key,
            "role_kind": args.role_kind,
            "schema": "python-cli-role-bootstrap/v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        repository_root = canonical_repository_root(args.repository_root)
        control_root = canonical_control_root(repository_root)
        if args.mode in {
            "event-run-once", "event-runner-status", "event-reconciliation-status",
            "event-recover-generation",
        }:
            from stock_data.orchestration.workflow_control.event_runner import (
                WorkflowEventRunner,
            )

            runner = WorkflowEventRunner(
                repository_root,
                owner_id=(
                    args.owner_id
                    if args.mode == "event-run-once"
                    else (
                        "event-generation-recovery"
                        if args.mode == "event-recover-generation"
                        else "event-reconciliation-status"
                        if args.mode == "event-reconciliation-status"
                        else "event-runner-status"
                    )
                ),
            )
            if args.mode == "event-run-once":
                receipt = runner.run_once()
                print(json.dumps(receipt.to_dict(), sort_keys=True))
                return 0 if receipt.outcome in {
                    "woken", "progressed", "unchanged", "already_running",
                } else 2
            if args.mode == "event-recover-generation":
                receipt = runner.recover_pending_generation(
                    material_generation=args.material_generation,
                    expected_attempt_receipt_digest=args.attempt_receipt_digest,
                    recovery_proof=args.recovery_proof,
                )
                print(json.dumps(receipt.to_dict(), sort_keys=True))
                return 0
            if args.mode == "event-reconciliation-status":
                status = WorkflowControllerService.event_reconciliation_status(
                    repository_root,
                    material_generation=args.material_generation,
                    attempt_receipt_digest=args.attempt_receipt_digest,
                )
                print(json.dumps(asdict(status), sort_keys=True))
                return 0
            print(json.dumps(asdict(runner.status()), sort_keys=True))
            return 0
        if args.mode == ServiceMode.STATUS.value:
            status = WorkflowControllerService.inspect(control_root)
            print(json.dumps(asdict(status), sort_keys=True, default=str))
            return 0
        if args.mode == "terminal-operation-status":
            terminal = CodexCliBoundary.inspect_terminal_operation(
                control_root / "codex_boundary.sqlite3",
                operation_id=args.boundary_operation_id,
            )
            print(json.dumps(asdict(terminal), sort_keys=True))
            return 0
        if args.mode == "process-event-status":
            receipt = CodexCliBoundary.inspect_process_event(
                control_root / "codex_boundary.sqlite3",
                pins=CodexProcessEventPins(
                    operation_id=args.boundary_operation_id,
                    request_digest=args.boundary_request_digest,
                    generation_sequence=args.generation_sequence,
                    generation_digest=args.generation_digest,
                    execution_profile_digest=args.execution_profile_digest,
                ),
                expected_receipt_digest=args.expected_receipt_digest,
            )
            print(json.dumps(receipt.to_dict(), sort_keys=True))
            return 0
        if args.mode in {"preflight-task-replan-ready", "mark-task-replan-ready"}:
            _verify_phase_a_queue_evidence(
                repository_root,
                task_id=args.task_id,
                expected_queue_generation=args.expected_queue_generation,
                expected_candidate_digest=args.expected_phase_a_candidate_digest,
                expected_review_digest=args.expected_phase_a_review_digest,
            )
            if args.mode == "preflight-task-replan-ready":
                receipt = WorkflowControllerService.preflight_task_replan_ready_at(
                    repository_root,
                    control_root / "workflow_controller.sqlite3",
                    task_id=args.task_id,
                    expected_queue_generation=args.expected_queue_generation,
                    expected_prior_contract_digest=args.expected_prior_contract_digest,
                    expected_phase_a_candidate_digest=args.expected_phase_a_candidate_digest,
                    expected_phase_a_review_digest=args.expected_phase_a_review_digest,
                    expected_prior_state=args.expected_prior_state,
                    reason_code=args.reason_code,
                    pm_role_key=args.pm_role_key,
                    pm_generation=args.pm_generation,
                )
                print(json.dumps(receipt.to_dict(), sort_keys=True))
                return 0
            service = build_production_service(
                repository_root,
                "pm-phase-boundary",
                ServiceMode.RUN,
            )
            receipt = service.mark_task_replan_ready(
                repository_root=repository_root,
                task_id=args.task_id,
                expected_queue_generation=args.expected_queue_generation,
                expected_prior_contract_digest=args.expected_prior_contract_digest,
                expected_phase_a_candidate_digest=args.expected_phase_a_candidate_digest,
                expected_phase_a_review_digest=args.expected_phase_a_review_digest,
                expected_prior_state=args.expected_prior_state,
                reason_code=args.reason_code,
                pm_role_key=args.pm_role_key,
                pm_generation=args.pm_generation,
            )
            print(json.dumps(receipt.to_dict(), sort_keys=True))
            return 0
        if args.mode == "phase-boundary-status":
            receipt = WorkflowController.inspect_phase_boundary_receipt_at(
                control_root / "workflow_controller.sqlite3",
                task_id=args.task_id,
            )
            print(json.dumps(receipt.to_dict(), sort_keys=True))
            return 0
        if args.mode == ServiceMode.ROLLBACK.value:
            status = WorkflowControllerService.rollback_stale(
                control_root,
                owner_id=args.owner_id,
                generation_sequence=args.generation_sequence,
                generation_digest=args.generation_digest,
            )
            print(json.dumps(asdict(status), sort_keys=True, default=str))
            return 0
        if args.mode == "recover-stranded":
            pins = {
                "owner_id": args.owner_id,
                "generation_sequence": args.generation_sequence,
                "generation_digest": args.generation_digest,
                "boundary_operation_id": args.boundary_operation_id,
                "boundary_request_digest": args.boundary_request_digest,
            }
            if args.preflight_only:
                receipt = WorkflowControllerService.preflight_stranded_recovery(
                    control_root, **pins,
                )
                print(json.dumps(receipt.to_dict(), sort_keys=True))
                return 0 if receipt.ready else 2
            receipt = WorkflowControllerService.recover_stranded(
                control_root, **pins,
            )
            print(json.dumps(receipt.to_dict(), sort_keys=True))
            return 0
        if args.mode == "reconcile-terminal":
            pins = {
                "owner_id": args.owner_id,
                "generation_sequence": args.generation_sequence,
                "generation_digest": args.generation_digest,
                "boundary_operation_id": args.boundary_operation_id,
                "boundary_request_digest": args.boundary_request_digest,
                "boundary_error_code": args.boundary_error_code,
                "release_reason": args.release_reason,
            }
            if args.preflight_only:
                receipt = WorkflowControllerService.preflight_terminal_reconciliation(
                    control_root, **pins,
                )
            else:
                receipt = WorkflowControllerService.reconcile_terminal(
                    control_root, **pins,
                )
            print(json.dumps(receipt.to_dict(), sort_keys=True))
            return 0
        if args.mode == "bootstrap-role":
            if args.bootstrap_attempt < 1 or args.bootstrap_attempt > 9:
                raise ValueError("bootstrap attempt must be between 1 and 9")
            provisional = _role_identity(args, "bootstrap-session-placeholder")
            if (
                provisional.role_kind is not RoleKind.PROJECT_MANAGER
                and provisional.active_task_id != args.binding_task_id
            ):
                raise ValueError("non-PM binding task must match its active Queue task")
            registry = RoleRegistry(control_root / "role_registry.sqlite3")
            boundary = CodexCliBoundary(
                control_root / "codex_boundary.sqlite3",
                cwd=repository_root,
                sandbox_mode="workspace-write",
            )
            records = {item.identity.role_key: item for item in registry.records()}
            existing = records.get(args.role_key)
            launch_receipt_digest: str | None = None
            migration_proof: str | None = None
            pending_targets_migrated = 0
            if existing is not None and existing.identity.runtime_id == "codex-cli-owned-v1":
                expected = _role_identity(args, existing.identity.codex_session_id)
                if existing.identity != expected:
                    raise ValueError("existing role does not match CLI bootstrap identity")
                if (
                    (args.expected_coordination_session_sha256 is None)
                    != (args.expected_coordination_generation is None)
                    or (
                        args.expected_coordination_session_sha256 is not None
                        and (
                            len(args.expected_coordination_session_sha256) != 64
                            or any(character not in "0123456789abcdef" for character in args.expected_coordination_session_sha256)
                            or args.expected_coordination_generation < 1
                            or existing.generation
                            != args.expected_coordination_generation + 1
                        )
                    )
                ):
                    raise ValueError("coordination migration replay identity is invalid")
                record = existing
            else:
                migration: tuple[int, str] | None = None
                if existing is not None:
                    fingerprint = args.expected_coordination_session_sha256
                    if (
                        not isinstance(fingerprint, str)
                        or args.expected_coordination_generation != existing.generation
                        or len(fingerprint) != 64
                        or any(character not in "0123456789abcdef" for character in fingerprint)
                        or hashlib.sha256(
                            existing.identity.codex_session_id.encode("utf-8")
                        ).hexdigest() != fingerprint
                        or existing.identity.role_kind is not provisional.role_kind
                        or existing.identity.orca_run_id != "transport-disabled"
                        or existing.identity.worktree_id != "stock-investment-rev1-main"
                        or existing.identity.terminal_handle is not None
                        or existing.identity.runtime_id != "codex-app-local"
                        or existing.identity.active_task_id != provisional.active_task_id
                        or existing.identity.active_dispatch_id != provisional.active_dispatch_id
                        or existing.identity.parent_role_key != provisional.parent_role_key
                    ):
                        raise ValueError("coordination role migration identity does not match")
                    migration_proof = boundary.assert_coordination_session(
                        task_id=args.binding_task_id,
                        role_key=args.role_key,
                        session_id=existing.identity.codex_session_id,
                    )
                    migration = (
                        existing.generation, existing.identity.codex_session_id,
                    )
                elif (
                    args.expected_coordination_session_sha256 is not None
                    or args.expected_coordination_generation is not None
                ):
                    raise ValueError("coordination fingerprint supplied without a stored role")
                if provisional.role_kind is RoleKind.DOMAIN_LEAD:
                    parent = records.get(str(provisional.parent_role_key))
                    if (
                        parent is None
                        or parent.identity.role_kind is not RoleKind.PROJECT_MANAGER
                        or parent.identity.runtime_id != "codex-cli-owned-v1"
                    ):
                        raise ValueError("CLI-owned project manager must exist before Lead bootstrap")
                    boundary.assert_cli_owned_session(
                        role_key=parent.identity.role_key,
                        session_id=parent.identity.codex_session_id,
                    )
                if args.preflight_only:
                    print(json.dumps({
                        "migration_proof": migration_proof,
                        "role_key": args.role_key,
                        "role_kind": provisional.role_kind.value,
                        "status": "ready",
                        "transport": "direct",
                    }, sort_keys=True))
                    return 0
                service = build_production_service(
                    repository_root, args.owner_id, ServiceMode.RUN,
                )
                service.start()
                try:
                    direct = InjectedDirectRunner(boundary)
                    launch = direct.run(
                        RunnerAction.LAUNCH,
                        task_id=args.binding_task_id,
                        role_key=args.role_key,
                        generation=_bootstrap_generation(args),
                        source_event_id=f"runtime_bootstrap_v{args.bootstrap_attempt}",
                    )
                    launch_receipt_digest = launch.receipt_digest
                    identity = _role_identity(args, launch.agent_id)
                    observed_at = datetime.now(UTC)
                    if migration is None:
                        record = service.register_role_session(
                            identity,
                            observed_at=observed_at,
                            lease_until=observed_at + timedelta(hours=24),
                        )
                    else:
                        record = service.controller.role_registry.migrate_coordination_session(
                            args.role_key,
                            expected_generation=migration[0],
                            expected_session_id=migration[1],
                            cli_session_id=launch.agent_id,
                            observed_at=observed_at,
                            lease_until=observed_at + timedelta(hours=24),
                        )
                        from stock_data.orchestration.workflow_control.event_runner import (
                            WorkflowEventRunner,
                        )

                        pending_targets_migrated = WorkflowEventRunner(
                            repository_root,
                            owner_id="role-migration-ledger",
                        ).migrate_pending_role_identity(
                            role_key=args.role_key,
                            expected_generation=migration[0],
                            expected_session_fingerprint=str(
                                args.expected_coordination_session_sha256
                            ),
                            cli_record=record,
                        )
                finally:
                    service.close()
            ownership_proof = boundary.assert_cli_owned_session(
                role_key=record.identity.role_key,
                session_id=record.identity.codex_session_id,
            )
            if (
                existing is not None
                and existing.identity.runtime_id == "codex-cli-owned-v1"
                and args.expected_coordination_session_sha256 is not None
            ):
                from stock_data.orchestration.workflow_control.event_runner import (
                    WorkflowEventRunner,
                )

                pending_targets_migrated = WorkflowEventRunner(
                    repository_root,
                    owner_id="role-migration-ledger",
                ).migrate_pending_role_identity(
                    role_key=args.role_key,
                    expected_generation=int(args.expected_coordination_generation),
                    expected_session_fingerprint=args.expected_coordination_session_sha256,
                    cli_record=record,
                )
            print(json.dumps({
                "active_task_id": record.identity.active_task_id,
                "bootstrap_reused": existing is not None and launch_receipt_digest is None,
                "launch_receipt_digest": launch_receipt_digest,
                "migration_proof": migration_proof,
                "generation": record.generation,
                "ownership_proof": ownership_proof,
                "pending_targets_migrated": pending_targets_migrated,
                "role_key": record.identity.role_key,
                "role_kind": record.identity.role_kind.value,
                "session_fingerprint": hashlib.sha256(
                    record.identity.codex_session_id.encode("utf-8")
                ).hexdigest(),
                "state": record.state.value,
                "transport": "direct",
            }, sort_keys=True))
            return 0
        if args.mode == "refresh-role":
            if args.expected_generation < 1:
                raise ValueError("expected generation must be positive")
            service = build_production_service(
                repository_root, args.owner_id, ServiceMode.RUN,
            )
            service.start()
            try:
                observed_at = datetime.now(UTC)
                record = service.controller.role_registry.heartbeat(
                    args.role_key,
                    expected_generation=args.expected_generation,
                    observed_at=observed_at,
                    lease_until=observed_at + timedelta(hours=24),
                )
            finally:
                service.close()
            print(json.dumps({
                "active_task_id": record.identity.active_task_id,
                "generation": record.generation,
                "role_key": record.identity.role_key,
                "role_kind": record.identity.role_kind.value,
                "state": record.state.value,
            }, sort_keys=True))
            return 0
        if args.mode == "replace-app-coordination-lead":
            if args.expected_pm_generation < 1 or args.expected_generation < 1:
                raise ValueError("expected generation must be positive")
            service = build_production_service(
                repository_root, args.owner_id, ServiceMode.RUN,
            )
            service.start()
            try:
                record = service.replace_app_coordination_lead_session(
                    pm_role_key=args.pm_role_key,
                    expected_pm_generation=args.expected_pm_generation,
                    role_key=args.role_key,
                    expected_generation=args.expected_generation,
                    expected_session_id=args.expected_session_id,
                    replacement_session_id=args.replacement_session_id,
                    expected_task_id=args.expected_task_id,
                    expected_dispatch_id=args.expected_dispatch_id,
                    expected_runtime_id=args.expected_runtime_id,
                    expected_worktree_id=args.expected_worktree_id,
                )
            finally:
                service.close()
            print(json.dumps({
                "active_dispatch_id": record.identity.active_dispatch_id,
                "active_task_id": record.identity.active_task_id,
                "generation": record.generation,
                "parent_role_key": record.identity.parent_role_key,
                "replacement_session_fingerprint": hashlib.sha256(
                    record.identity.codex_session_id.encode("utf-8")
                ).hexdigest(),
                "role_key": record.identity.role_key,
                "runtime_id": record.identity.runtime_id,
                "state": record.state.value,
            }, sort_keys=True))
            return 0
        mode = ServiceMode(args.mode)
        service = build_production_service(repository_root, args.owner_id, mode)
        service.start()
        try:
            events = _events(args.events)
            receipt = service.canary(events) if mode is ServiceMode.CANARY else service.run(events)
            print(json.dumps(receipt.to_dict(), sort_keys=True))
            return 0
        finally:
            # A one-shot CLI invocation must never strand its writer lease.
            # A long-running host owns the same service object and closes it
            # explicitly during its controlled shutdown path.
            service.close()
    except (
        ControllerServiceError,
        CodexBoundaryError,
        WorkflowControllerError,
        RoleRegistryError,
        EventRunnerError,
        QueueAdapterError,
        OSError,
        sqlite3.Error,
        ValueError,
    ) as error:
        print(f"workflow controller refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
