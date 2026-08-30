"""Operate the injected Python PM controller service without an Orca fallback."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from stock_data.orchestration.workflow_control.contracts import WorkflowEvent
from stock_data.orchestration.workflow_control.codex_boundary import CodexBoundaryError
from stock_data.orchestration.workflow_control.controller import WorkflowControllerError
from stock_data.orchestration.workflow_control.production import (
    build_production_service,
    canonical_control_root,
    canonical_repository_root,
)
from stock_data.orchestration.workflow_control.service import (
    ControllerServiceError,
    ServiceMode,
    WorkflowControllerService,
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        repository_root = canonical_repository_root(args.repository_root)
        control_root = canonical_control_root(repository_root)
        if args.mode == ServiceMode.STATUS.value:
            status = WorkflowControllerService.inspect(control_root)
            print(json.dumps(asdict(status), sort_keys=True, default=str))
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
        ValueError,
    ) as error:
        print(f"workflow controller refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
