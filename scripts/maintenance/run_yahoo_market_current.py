from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.yahoo_market_current import (
    describe_yahoo_market_current,
    replay_yahoo_market_current,
    run_yahoo_market_current,
)
from stock_data.orchestration.update_event_log import (
    CommitResult,
    DEFAULT_RUNTIME_LOG_ROOT,
    EventState,
    FinalityResult,
    LocalUpdateEventLog,
    ReasonCode,
    TriggerType,
    UpdateEvent,
    ValidationResult,
    new_run_id,
)


def _report_exit_code(report: object) -> int:
    if not isinstance(report, dict):
        return 1
    return 0 if report.get("status") == "PASS" and report.get("failed") == 0 else 1


def _append_event_safely(store: LocalUpdateEventLog, event: UpdateEvent) -> bool:
    """Keep diagnostic logging outside the outcome and surface only a safe status."""

    try:
        result = store.append(event)
    except Exception:
        print("runtime_event_log=FAILED error_code=UNEXPECTED", file=sys.stderr)
        return False
    if not result.ok:
        print(
            f"runtime_event_log=FAILED error_code={result.error_code or 'UNKNOWN'}",
            file=sys.stderr,
        )
    return result.ok


def _failure_state(error: Exception) -> tuple[EventState, ReasonCode]:
    if isinstance(error, PermissionError):
        return (
            EventState.AUTH_PERMISSION_FAILURE,
            ReasonCode.AUTHENTICATION_OR_PERMISSION_DENIED,
        )
    if isinstance(error, OSError):
        return EventState.LOCAL_IO_FAILURE, ReasonCode.LOCAL_READ_WRITE_ERROR
    if isinstance(error, (TypeError, ValueError)):
        return EventState.VALIDATION_FAILURE, ReasonCode.VALIDATION_REJECTED
    return EventState.PROVIDER_NETWORK_FAILURE, ReasonCode.PROVIDER_OR_NETWORK_ERROR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the unified Yahoo current operation (30m polling)."
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--replay", action="store_true")
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    if args.dry_run:
        print(json.dumps(describe_yahoo_market_current(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.replay:
        report = replay_yahoo_market_current(project_root)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return _report_exit_code(report)
    started_at = datetime.now(timezone.utc)
    started = UpdateEvent.started(
        run_id=new_run_id("yahoo-market-current", now=started_at),
        job_route="yahoo-market-current",
        logical_dataset="yahoo_market_current",
        trigger_type=TriggerType.SCHEDULED,
        requested_scope={"lanes": ["GLOBAL_30M", "NATIVE_15M"]},
        at=started_at,
        message="scheduled Yahoo current refresh started",
    )
    store = LocalUpdateEventLog(project_root / DEFAULT_RUNTIME_LOG_ROOT)
    _append_event_safely(store, started)
    try:
        report = run_yahoo_market_current(project_root)
    except Exception as error:
        ended_at = datetime.now(timezone.utc)
        state, reason_code = _failure_state(error)
        _append_event_safely(store, started.terminal(
            state=state,
            reason_code=reason_code,
            at=max(ended_at, started_at),
            message=f"Yahoo current refresh failed: {type(error).__name__}",
        ))
        raise
    exit_code = _report_exit_code(report)
    succeeded = exit_code == 0
    _append_event_safely(store, started.terminal(
        state=EventState.SUCCEEDED if succeeded else EventState.PARTIAL_INELIGIBLE,
        reason_code=(
            ReasonCode.COMPLETED if succeeded
            else ReasonCode.PARTIAL_SCOPE_INELIGIBLE
        ),
        at=max(datetime.now(timezone.utc), started_at),
        provider_call_count=(
            int(report.get("api_calls", 0))
            if isinstance(report, dict)
            and isinstance(report.get("api_calls", 0), int)
            and not isinstance(report.get("api_calls", 0), bool)
            and int(report.get("api_calls", 0)) >= 0
            else 0
        ),
        validation_result=(
            ValidationResult.PASSED if succeeded else ValidationResult.PARTIAL
        ),
        promotion_result=CommitResult.NOT_RUN,
        checkpoint_result=CommitResult.SUCCEEDED if succeeded else CommitResult.FAILED,
        finality_result=FinalityResult.AS_RETRIEVED,
        message=(
            "Yahoo current refresh status="
            f"{report.get('status', 'UNKNOWN') if isinstance(report, dict) else 'UNKNOWN'}"
        ),
    ))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
