from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import argparse
import json
import os
from pathlib import Path
import shutil
from typing import Callable, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from stock_data.contracts.ls_t1633 import LS_T1633_PROGRAM_TRADING_DAILY
from stock_data.orchestration.daily_operations import DailyRunLock
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.ls_t1633 import (
    validate_ls_t1633_exact_date_pair,
    validate_ls_t1633_program_trading,
)


SEOUL = ZoneInfo("Asia/Seoul")


class LST1633DailyIncrementalError(RuntimeError):
    pass


@dataclass(frozen=True)
class LST1633DailyPlan:
    market_date: date
    latest_finalized_market_date: date
    action: str
    reason: str
    estimated_business_calls: int = 4
    retry_count: int = 0


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LST1633DailyIncrementalError(f"invalid LS t1633 state: {path.name}") from error
    if not isinstance(value, dict):
        raise LST1633DailyIncrementalError(f"invalid LS t1633 state: {path.name}")
    return value


def _paths(project_root: Path, market_date: date) -> dict[str, Path]:
    token = market_date.strftime("%Y%m%d")
    transaction = project_root / "data/staging/ls_t1633_program_trading_daily" / token
    return {
        "transaction": transaction,
        "stage": transaction / "staged_normalized",
        "previous": transaction / "previous_normalized",
        "stage_checkpoint": transaction / "staged_checkpoint.json",
        "previous_checkpoint": transaction / "previous_checkpoint.json",
        "normalized": project_root / "data/normalized" / LS_T1633_PROGRAM_TRADING_DAILY.name,
        "checkpoint": project_root / "data/state/ls_t1633_program_trading_daily.json",
        "journal": project_root / "data/state/transactions" / f"ls_t1633_program_trading_daily_{token}.json",
        "lock": project_root / "data/state/.ls_t1633_program_trading_daily.lock",
    }


def _checkpoint(path: Path) -> dict[str, object]:
    payload = _read_json(path)
    if not payload:
        return {"dataset": LS_T1633_PROGRAM_TRADING_DAILY.name, "completed_dates": []}
    if payload.get("dataset") != LS_T1633_PROGRAM_TRADING_DAILY.name:
        raise LST1633DailyIncrementalError("LS t1633 checkpoint identity differs")
    completed = payload.get("completed_dates")
    if not isinstance(completed, list) or any(not isinstance(value, str) for value in completed):
        raise LST1633DailyIncrementalError("LS t1633 completed dates are invalid")
    return payload


def _rollback(paths: dict[str, Path], *, normalized_existed: bool, checkpoint_existed: bool) -> None:
    if paths["previous"].exists():
        if paths["normalized"].exists():
            shutil.rmtree(paths["normalized"])
        paths["previous"].replace(paths["normalized"])
    elif not normalized_existed and not paths["stage"].exists() and paths["normalized"].exists():
        shutil.rmtree(paths["normalized"])
    if paths["previous_checkpoint"].exists():
        paths["checkpoint"].unlink(missing_ok=True)
        paths["previous_checkpoint"].replace(paths["checkpoint"])
    elif not checkpoint_existed and not paths["stage_checkpoint"].exists():
        paths["checkpoint"].unlink(missing_ok=True)


def _recover(project_root: Path, market_date: date) -> None:
    paths = _paths(project_root, market_date)
    journal = _read_json(paths["journal"])
    if not journal:
        return
    if journal.get("status") in {"SUCCEEDED", "FAILED", "RECOVERED"}:
        shutil.rmtree(paths["transaction"], ignore_errors=True)
        return
    if (
        journal.get("dataset") != LS_T1633_PROGRAM_TRADING_DAILY.name
        or journal.get("market_date") != market_date.isoformat()
    ):
        raise LST1633DailyIncrementalError("LS t1633 journal identity differs")
    _rollback(
        paths,
        normalized_existed=journal.get("normalized_existed") is True,
        checkpoint_existed=journal.get("checkpoint_existed") is True,
    )
    shutil.rmtree(paths["transaction"], ignore_errors=True)
    _atomic_json(paths["journal"], {**journal, "status": "RECOVERED"})


def _verify_date(root: Path, market_date: str) -> None:
    frame = read_dataset(root, LS_T1633_PROGRAM_TRADING_DAILY, validate_ls_t1633_program_trading)
    validate_ls_t1633_exact_date_pair(frame, market_date)


def _xrkr_dates(project_root: Path) -> tuple[date, ...]:
    paths = sorted((project_root / "data/normalized/kr_kospi200_index_daily").glob("year=*/data.parquet"))
    if not paths:
        raise LST1633DailyIncrementalError("XKRX calendar source is unavailable")
    frame = pd.concat([pd.read_parquet(path, columns=["date"]) for path in paths], ignore_index=True)
    return tuple(sorted(set(pd.to_datetime(frame["date"], errors="raise").dt.date)))


def latest_t_plus_one_market_date(
    project_root: Path, *, now: datetime | None = None,
) -> date:
    """Return the latest retained XKRX session strictly before the operation date."""
    local_now = now.astimezone(SEOUL) if now is not None else datetime.now(SEOUL)
    eligible = [value for value in _xrkr_dates(project_root) if value < local_now.date()]
    if not eligible:
        raise LST1633DailyIncrementalError("no prior XKRX session is available")
    return max(eligible)


def plan_ls_t1633_daily(
    *, project_root: Path, market_date: date, latest_finalized_market_date: date,
    accepted_market_dates: Iterable[date], source_operation_reviewed: bool = False,
    source_finality_reviewed: bool = False,
) -> LST1633DailyPlan:
    _recover(project_root, market_date)
    target = market_date.isoformat()
    completed = set(map(str, _checkpoint(_paths(project_root, market_date)["checkpoint"])["completed_dates"]))
    if not source_operation_reviewed:
        action, reason = "BLOCKED", "ACTIVE_EXACT_DATE_OPERATION_REVIEW_REQUIRED"
    elif not source_finality_reviewed:
        action, reason = "BLOCKED", "PUBLICATION_AND_REVISION_FINALITY_REQUIRED"
    elif market_date > latest_finalized_market_date:
        action, reason = "BLOCKED", "SOURCE_DATE_NOT_FINAL"
    elif market_date not in set(accepted_market_dates):
        action, reason = "BLOCKED", "DATE_NOT_IN_EXPLICIT_ACCEPTED_CALENDAR"
    elif target in completed:
        _verify_date(_paths(project_root, market_date)["normalized"], target)
        action, reason = "NOOP_IDEMPOTENT", "BOTH_MARKETS_CHECKPOINTED_AND_PRESENT"
    else:
        action, reason = "READY", "EXACT_DATE_FOUR_SCOPE_TRANSACTION_ACCEPTED"
    return LST1633DailyPlan(market_date, latest_finalized_market_date, action, reason)


def execute_ls_t1633_daily(
    plan: LST1633DailyPlan, *, project_root: Path,
    candidate_builder: Callable[[date], pd.DataFrame] | None,
) -> dict[str, object]:
    if plan.action == "BLOCKED":
        raise LST1633DailyIncrementalError(plan.reason)
    paths = _paths(project_root, plan.market_date)
    target = plan.market_date.isoformat()
    if plan.action == "NOOP_IDEMPOTENT":
        _verify_date(paths["normalized"], target)
        return {"status": "NOOP_IDEMPOTENT", "business_calls": 0, "promoted_rows": 0}
    if candidate_builder is None:
        raise LST1633DailyIncrementalError("candidate builder is required")
    if paths["transaction"].exists():
        shutil.rmtree(paths["transaction"])
    paths["transaction"].mkdir(parents=True)
    normalized_existed = paths["normalized"].exists()
    checkpoint_existed = paths["checkpoint"].exists()
    journal: dict[str, object] = {
        "contract_version": 1,
        "dataset": LS_T1633_PROGRAM_TRADING_DAILY.name,
        "market_date": target,
        "required_scopes": ["KOSPI_AMOUNT", "KOSPI_QUANTITY", "KOSDAQ_AMOUNT", "KOSDAQ_QUANTITY"],
        "normalized_existed": normalized_existed,
        "checkpoint_existed": checkpoint_existed,
        "status": "PREPARED",
    }
    _atomic_json(paths["journal"], journal)
    try:
        with DailyRunLock(paths["lock"], run_id=f"ls-t1633-{target}-{uuid4().hex}"):
            incoming = candidate_builder(plan.market_date).copy()
            incoming = incoming.sort_values(
                list(LS_T1633_PROGRAM_TRADING_DAILY.sort_key), kind="stable"
            ).reset_index(drop=True)
            validate_ls_t1633_exact_date_pair(incoming, target)
            if normalized_existed:
                existing = read_dataset(
                    paths["normalized"], LS_T1633_PROGRAM_TRADING_DAILY,
                    validate_ls_t1633_program_trading,
                )
                keep = ~(
                    existing["date"].astype(str).eq(target)
                    & existing["market"].astype(str).isin(("KOSPI", "KOSDAQ"))
                )
                combined = pd.concat([existing.loc[keep], incoming], ignore_index=True)
            else:
                combined = incoming
            combined = combined.sort_values(
                list(LS_T1633_PROGRAM_TRADING_DAILY.sort_key), kind="stable"
            ).reset_index(drop=True)
            validate_ls_t1633_program_trading(combined)
            write_dataset_atomic(
                combined, paths["stage"], LS_T1633_PROGRAM_TRADING_DAILY,
                validate_ls_t1633_program_trading,
            )
            _verify_date(paths["stage"], target)
            checkpoint = _checkpoint(paths["checkpoint"])
            checkpoint["completed_dates"] = sorted(
                set(map(str, checkpoint["completed_dates"])) | {target}
            )
            checkpoint.update({"contract_version": 1, "latest_date": target})
            _atomic_json(paths["stage_checkpoint"], checkpoint)
            journal["status"] = "STAGED"
            _atomic_json(paths["journal"], journal)

            paths["normalized"].parent.mkdir(parents=True, exist_ok=True)
            if normalized_existed:
                paths["normalized"].replace(paths["previous"])
            paths["stage"].replace(paths["normalized"])
            journal["status"] = "NORMALIZED_PROMOTED"
            _atomic_json(paths["journal"], journal)
            if checkpoint_existed:
                paths["checkpoint"].replace(paths["previous_checkpoint"])
            paths["stage_checkpoint"].replace(paths["checkpoint"])
            journal["status"] = "CHECKPOINT_PROMOTED"
            _atomic_json(paths["journal"], journal)
            _verify_date(paths["normalized"], target)
            journal["status"] = "SUCCEEDED"
            _atomic_json(paths["journal"], journal)
        shutil.rmtree(paths["transaction"], ignore_errors=True)
        return {"status": "COMPLETE", "business_calls": 4, "promoted_rows": 2}
    except Exception as error:
        _rollback(
            paths, normalized_existed=normalized_existed,
            checkpoint_existed=checkpoint_existed,
        )
        shutil.rmtree(paths["transaction"], ignore_errors=True)
        journal.update({"status": "FAILED", "error_type": type(error).__name__})
        _atomic_json(paths["journal"], journal)
        raise


def run_ls_t1633_daily(
    project_root: Path,
    *,
    market_date: date,
    now: datetime | None = None,
    candidate_builder_factory: Callable[[Path], Callable[[date], pd.DataFrame]] | None = None,
) -> dict[str, object]:
    finalized = latest_t_plus_one_market_date(project_root, now=now)
    plan = plan_ls_t1633_daily(
        project_root=project_root,
        market_date=market_date,
        latest_finalized_market_date=finalized,
        accepted_market_dates=(finalized,),
        source_operation_reviewed=True,
        source_finality_reviewed=True,
    )
    if plan.action == "NOOP_IDEMPOTENT":
        return execute_ls_t1633_daily(plan, project_root=project_root, candidate_builder=None)
    if plan.action != "READY":
        raise LST1633DailyIncrementalError(plan.reason)
    if candidate_builder_factory is None:
        load_dotenv(project_root / ".env", override=False)
        from stock_data.providers.ls_t1633 import LST1633DailyCandidateBuilder

        def candidate_builder_factory(root: Path) -> Callable[[date], pd.DataFrame]:
            return LST1633DailyCandidateBuilder(
                project_root=root,
                app_key=os.environ.get("LS_APP_KEY", ""),
                app_secret=os.environ.get("LS_APP_SECRET", ""),
                base_url=os.environ.get("LS_BASE_URL", ""),
            )
    builder = candidate_builder_factory(project_root)
    result = execute_ls_t1633_daily(plan, project_root=project_root, candidate_builder=builder)
    result.update({
        "oauth_calls": getattr(builder, "oauth_calls", None),
        "retry_count": getattr(builder, "retry_count", 0),
        "landing_run": str(getattr(builder, "run_dir", "") or ""),
    })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded atomic LS t1633 daily operation")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    finalized = latest_t_plus_one_market_date(args.project_root)
    plan = plan_ls_t1633_daily(
        project_root=args.project_root,
        market_date=args.market_date,
        latest_finalized_market_date=finalized,
        accepted_market_dates=(finalized,),
        source_operation_reviewed=True,
        source_finality_reviewed=True,
    )
    if not args.live:
        print(json.dumps({
            "status": "DRY_RUN",
            "action": plan.action,
            "reason": plan.reason,
            "market_date": args.market_date.isoformat(),
            "latest_finalized_market_date": finalized.isoformat(),
            "estimated_business_calls": 0 if plan.action == "NOOP_IDEMPOTENT" else 4,
            "retry_count": 0,
        }, sort_keys=True))
        return 0
    result = run_ls_t1633_daily(args.project_root, market_date=args.market_date)
    print(json.dumps(result, sort_keys=True))
    return 0


__all__ = [
    "LST1633DailyIncrementalError", "LST1633DailyPlan",
    "execute_ls_t1633_daily", "latest_t_plus_one_market_date", "plan_ls_t1633_daily",
    "run_ls_t1633_daily",
]


if __name__ == "__main__":
    raise SystemExit(main())
