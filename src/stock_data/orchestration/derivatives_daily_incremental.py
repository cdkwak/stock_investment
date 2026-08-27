from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable, Mapping
from uuid import uuid4

from stock_data.orchestration.daily_operations import DailyRunLock


STAGE_ORDER = ("source", "bridge", "basis", "pcr", "wall")


class DerivativesDailyIncrementalError(RuntimeError):
    pass


@dataclass(frozen=True)
class StageScope:
    input_dates: tuple[date, ...]
    output_dates: tuple[date, ...]


@dataclass(frozen=True)
class DerivativesDailyPlan:
    market_date: date
    latest_finalized_market_date: date
    action: str
    reason: str
    scopes: Mapping[str, StageScope]
    retry_count: int = 0

    @property
    def affected_dates(self) -> tuple[date, ...]:
        """Ordered union of dates that the transaction must replace."""
        return tuple(dict.fromkeys(
            value for stage in STAGE_ORDER for value in self.scopes[stage].output_dates
        ))


@dataclass(frozen=True)
class StageCandidate:
    stage: str
    root: Path
    output_dates: tuple[date, ...]
    validated: bool
    history_preserved: bool


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _checkpoint_state(
    path: Path,
) -> tuple[set[date], dict[date, tuple[date, ...]]]:
    if not path.exists():
        return set(), {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("dataset") != "derivatives_price_daily_dag":
            raise ValueError("identity")
        completed = {date.fromisoformat(value) for value in payload["completed_dates"]}
        raw_scopes = payload.get("affected_dates_by_target", {})
        if not isinstance(raw_scopes, dict):
            raise ValueError("affected scopes")
        scopes = {
            date.fromisoformat(target): tuple(date.fromisoformat(value) for value in values)
            for target, values in raw_scopes.items()
        }
        return completed, scopes
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise DerivativesDailyIncrementalError("derivatives checkpoint is invalid") from error


def plan_derivatives_daily(
    *, project_root: Path, market_date: date, latest_finalized_market_date: date,
    accepted_market_dates: tuple[date, ...], source_operation_reviewed: bool = False,
    prior_option_observation_date: date | None = None,
    next_option_observation_date: date | None = None,
    next_option_observation_reviewed: bool = False,
) -> DerivativesDailyPlan:
    checkpoint = project_root / "data/state/derivatives_price_daily_dag.json"
    completed, completed_scopes = _checkpoint_state(checkpoint)
    if not source_operation_reviewed:
        action, reason = "BLOCKED", "AUTHORITATIVE_SOURCE_OPERATION_REVIEW_REQUIRED"
    elif market_date > latest_finalized_market_date:
        action, reason = "BLOCKED", "SOURCE_DATE_NOT_FINAL"
    elif market_date not in set(accepted_market_dates):
        action, reason = "BLOCKED", "DATE_NOT_IN_EXPLICIT_ACCEPTED_CALENDAR"
    elif prior_option_observation_date is None or prior_option_observation_date >= market_date:
        action, reason = "BLOCKED", "PRIOR_OPTION_OBSERVATION_REQUIRED_FOR_WALL_CHANGE"
    elif not next_option_observation_reviewed:
        action, reason = "BLOCKED", "NEXT_OPTION_OBSERVATION_REVIEW_REQUIRED_FOR_WALL_SCOPE"
    elif (
        next_option_observation_date is not None
        and next_option_observation_date <= market_date
    ):
        action, reason = "BLOCKED", "NEXT_OPTION_OBSERVATION_MUST_FOLLOW_TARGET"
    else:
        action, reason = "READY", "EXACT_DATE_SOURCE_AND_DEPENDENCY_SCOPE_ACCEPTED"
    exact = (market_date,)
    wall_input = tuple(value for value in (
        prior_option_observation_date, market_date, next_option_observation_date,
    ) if value is not None)
    wall_output = (
        (market_date, next_option_observation_date)
        if next_option_observation_date is not None and next_option_observation_date > market_date
        else exact
    )
    expected_affected = tuple(dict.fromkeys(exact + wall_output))
    if action == "READY" and market_date in completed:
        if completed_scopes.get(market_date) != expected_affected:
            action, reason = "BLOCKED", "CHECKPOINT_AFFECTED_DATE_SCOPE_CONFLICT"
        else:
            action, reason = "NOOP_IDEMPOTENT", "AFFECTED_DATE_TRANSACTION_CHECKPOINTED"
    return DerivativesDailyPlan(
        market_date, latest_finalized_market_date, action, reason,
        {
            "source": StageScope(exact, exact),
            "bridge": StageScope(exact, exact),
            "basis": StageScope(exact, exact),
            "pcr": StageScope(exact, exact),
            # Wall change columns are first differences by maturity/provider/session.
            # A change at T can therefore alter T and only the immediate next
            # option observation, never every later date.
            "wall": StageScope(wall_input, wall_output),
        },
    )


def _manifest(root: Path) -> tuple[tuple[str, int, str], ...]:
    if not root.is_dir():
        raise DerivativesDailyIncrementalError(f"candidate root missing: {root}")
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        body = path.read_bytes()
        rows.append((path.relative_to(root).as_posix(), len(body), hashlib.sha256(body).hexdigest()))
    if not rows:
        raise DerivativesDailyIncrementalError("candidate root is empty")
    return tuple(rows)


def _validate_output_roots(output_roots: Mapping[str, Path], project_root: Path) -> None:
    if set(output_roots) != set(STAGE_ORDER):
        raise DerivativesDailyIncrementalError("DAG output topology differs")
    resolved = []
    base = project_root.resolve()
    for stage in STAGE_ORDER:
        target = output_roots[stage].resolve()
        try:
            relative = target.relative_to(base)
        except ValueError as error:
            raise DerivativesDailyIncrementalError("DAG output escapes project root") from error
        if not relative.parts:
            raise DerivativesDailyIncrementalError("project root cannot be a DAG output")
        resolved.append(target)
    if len(resolved) != len(set(resolved)):
        raise DerivativesDailyIncrementalError("DAG outputs must be distinct")


def execute_derivatives_daily(
    plan: DerivativesDailyPlan, *, project_root: Path,
    builders: Mapping[str, Callable[[Path, StageScope, Mapping[str, StageCandidate]], StageCandidate]],
    output_roots: Mapping[str, Path],
) -> dict[str, object]:
    _validate_output_roots(output_roots, project_root)
    if plan.action == "BLOCKED":
        raise DerivativesDailyIncrementalError(plan.reason)
    if plan.action == "NOOP_IDEMPOTENT":
        for stage in STAGE_ORDER:
            _manifest(output_roots[stage])
        return {
            "status": plan.action,
            "affected_dates": [value.isoformat() for value in plan.affected_dates],
        }
    if tuple(builders) != STAGE_ORDER or tuple(output_roots) != STAGE_ORDER:
        raise DerivativesDailyIncrementalError("DAG stages must exactly match ordered Source-to-Wall topology")
    transaction_id = f"{plan.market_date:%Y%m%d}-{uuid4().hex}"
    transaction_root = project_root / "data/staging/derivatives_daily_transactions" / transaction_id
    journal_path = project_root / "data/state/derivatives_price_daily_dag.transaction.json"
    checkpoint_path = project_root / "data/state/derivatives_price_daily_dag.json"
    lock_path = project_root / "data/state/.derivatives_price_daily.lock"
    if journal_path.exists():
        raise DerivativesDailyIncrementalError(
            "existing derivatives transaction journal requires reviewed recovery"
        )
    candidates: dict[str, StageCandidate] = {}
    backups: dict[str, Path | None] = {}
    promoted: list[str] = []
    transaction_root.mkdir(parents=True, exist_ok=False)
    try:
        with DailyRunLock(lock_path, run_id=f"derivatives-{transaction_id}"):
            for stage in STAGE_ORDER:
                candidate = builders[stage](transaction_root / stage, plan.scopes[stage], candidates)
                if candidate.stage != stage or candidate.root.resolve() != (transaction_root / stage).resolve():
                    raise DerivativesDailyIncrementalError(f"{stage} candidate identity differs")
                if tuple(candidate.output_dates) != plan.scopes[stage].output_dates:
                    raise DerivativesDailyIncrementalError(f"{stage} affected dates differ")
                if not candidate.validated or not candidate.history_preserved:
                    raise DerivativesDailyIncrementalError(f"{stage} candidate is not promotable")
                _manifest(candidate.root)
                candidates[stage] = candidate
            journal = {
                "version": 1, "transaction_id": transaction_id, "phase": "PREPARED",
                "market_date": plan.market_date.isoformat(), "promoted": [],
                "candidate_manifests": {stage: _manifest(value.root) for stage, value in candidates.items()},
            }
            _atomic_json(journal_path, journal)
            for stage in STAGE_ORDER:
                target = output_roots[stage]
                target.parent.mkdir(parents=True, exist_ok=True)
                backup = transaction_root / "backups" / stage
                if target.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    target.replace(backup)
                    backups[stage] = backup
                else:
                    backups[stage] = None
                try:
                    candidates[stage].root.replace(target)
                except Exception:
                    if backups[stage] is not None and backups[stage].exists():
                        backups[stage].replace(target)
                    raise
                promoted.append(stage)
                journal.update({"phase": f"PROMOTED_{stage.upper()}", "promoted": promoted.copy()})
                _atomic_json(journal_path, journal)
            completed, completed_scopes = _checkpoint_state(checkpoint_path)
            completed.add(plan.market_date)
            completed_scopes[plan.market_date] = plan.affected_dates
            _atomic_json(checkpoint_path, {
                "version": 1, "dataset": "derivatives_price_daily_dag",
                "completed_dates": sorted(value.isoformat() for value in completed),
                "affected_dates_by_target": {
                    target.isoformat(): [value.isoformat() for value in values]
                    for target, values in sorted(completed_scopes.items())
                },
                "last_transaction_id": transaction_id,
            })
            journal_path.unlink(missing_ok=True)
        return {
            "status": "AFFECTED_DATE_COMPLETE",
            "affected_dates": [value.isoformat() for value in plan.affected_dates],
            "stages": list(STAGE_ORDER), "retry_count": 0,
        }
    except Exception:
        for stage in reversed(promoted):
            target = output_roots[stage]
            if target.exists():
                shutil.rmtree(target)
            backup = backups[stage]
            if backup is not None and backup.exists():
                backup.replace(target)
        journal_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(transaction_root, ignore_errors=True)


__all__ = [
    "DerivativesDailyIncrementalError", "DerivativesDailyPlan", "StageCandidate",
    "StageScope", "STAGE_ORDER", "execute_derivatives_daily", "plan_derivatives_daily",
]
