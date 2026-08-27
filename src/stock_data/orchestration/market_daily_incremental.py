from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable, Iterable
from uuid import uuid4

from stock_data.contracts.data_v1 import KR_CREDIT_BALANCE_DAILY, KR_MARKET_LIQUIDITY_DAILY
from stock_data.pipelines.data_v1_collection import collect_date
from stock_data.pipelines.short_selling_backfill import plan_scopes, run_short_selling_batch
from stock_data.pipelines.stock_lending_backfill import (
    STOCK_LENDING_SPECS,
    collect_stock_lending_history,
)
from stock_data.providers.data_go_kr.data_v1 import (
    ENDPOINTS,
    normalize_credit_balance,
    normalize_market_liquidity,
)
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.orchestration.daily_operations import (
    AuthStatus, DailyRunLock, DatasetHealth, DatasetOperationSpec,
    FreshnessStatus, OperationalEligibility,
    PredictiveEligibility, StageStatus,
)


class MarketDailyIncrementalError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExactDateDailyPlan:
    lane: str
    dataset: str
    market_date: date
    latest_finalized_market_date: date
    action: str
    reason: str
    estimated_api_calls: int
    retry_count: int = 0


@dataclass(frozen=True)
class TwoPassDailyPlan:
    lane: str
    dataset: str
    market_date: date
    latest_finalized_market_date: date
    action: str
    reason: str
    estimated_api_calls: int
    retry_count: int = 0


@dataclass(frozen=True)
class TwoPassResult:
    dataset: str
    market_date: str
    status: str
    pages: int
    observation_count: int
    stable: bool
    landing_path: str | None


SHORT_SELLING_EXACT_DATE_SCOPE_COUNTS = {
    "trading": 2,
    "balance": 2,
    "investor": 4,
}
SHORT_SELLING_FRESH_SESSION_AUTH_RAW_CALLS = 5
SHORT_SELLING_FINALITY_POLICIES = {
    "trading": "NEXT_XKRX_SESSION_T_PLUS_1",
    "balance": "EXPLICIT_REVIEWED_PROVIDER_PUBLICATION_ONLY",
    "investor": "NEXT_XKRX_SESSION_T_PLUS_1",
}


def short_selling_raw_call_budget(dataset: str, pending_business_calls: int) -> int:
    """Return the exact fresh-session raw budget observed for one exact date."""
    if dataset not in SHORT_SELLING_EXACT_DATE_SCOPE_COUNTS:
        raise ValueError("unsupported short-selling dataset")
    maximum = SHORT_SELLING_EXACT_DATE_SCOPE_COUNTS[dataset]
    if not 0 <= pending_business_calls <= maximum:
        raise ValueError("pending business calls exceed the exact-date scope count")
    if pending_business_calls == 0:
        return 0
    return SHORT_SELLING_FRESH_SESSION_AUTH_RAW_CALLS + pending_business_calls


def _retained_balance_valid_empty_stop(checkpoint: dict) -> tuple[date, str] | None:
    if checkpoint.get("status") != "STOPPED":
        return None
    reason = checkpoint.get("stop_reason")
    prefix = "ANOMALOUS_VALID_EMPTY:"
    if not isinstance(reason, str) or not reason.startswith(prefix):
        return None
    scope = reason.removeprefix(prefix)
    token, separator, market = scope.partition("_")
    if separator != "_" or market not in {"KOSPI", "KOSDAQ"}:
        raise MarketDailyIncrementalError("balance valid-empty stop identity is invalid")
    try:
        stopped_date = datetime.strptime(token, "%Y%m%d").date()
    except ValueError as error:
        raise MarketDailyIncrementalError("balance valid-empty stop date is invalid") from error
    return stopped_date, scope


def _run_lock(plan: ExactDateDailyPlan, project_root: Path) -> DailyRunLock:
    return DailyRunLock(
        project_root / "data/state" / f".{plan.lane.lower()}.lock",
        run_id=f"{plan.dataset}-{plan.market_date:%Y%m%d}-{uuid4().hex}",
    )


def _validate_gate(
    *, target: date, latest_finalized: date, accepted_dates: Iterable[date],
    operation_reviewed: bool,
) -> tuple[str, str] | None:
    accepted = tuple(sorted(set(accepted_dates)))
    if not operation_reviewed:
        return "BLOCKED", "ACTIVE_OPERATION_REVIEW_REQUIRED"
    if target > latest_finalized:
        return "BLOCKED", "SOURCE_DATE_NOT_FINAL"
    if target not in accepted:
        return "BLOCKED", "DATE_NOT_IN_EXPLICIT_ACCEPTED_CALENDAR"
    return None


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MarketDailyIncrementalError(f"checkpoint is unreadable: {path.name}") from error
    if not isinstance(payload, dict):
        raise MarketDailyIncrementalError(f"checkpoint is invalid: {path.name}")
    return payload


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


def _short_selling_transaction_paths(project_root: Path, market_date: date) -> dict[str, Path]:
    token = market_date.strftime("%Y%m%d")
    transaction_root = project_root / "data/staging/kr_short_selling_trading_daily" / token
    return {
        "journal": project_root / "data/state/transactions" / f"kr_short_selling_trading_daily_{token}.json",
        "transaction_root": transaction_root,
        "stage_normalized": transaction_root / "staged_normalized",
        "stage_checkpoint": transaction_root / "staged_checkpoint.json",
        "previous_normalized": transaction_root / "previous_normalized",
        "previous_checkpoint": transaction_root / "previous_checkpoint.json",
        "normalized": project_root / "data/normalized/kr_short_selling_trading_daily",
        "checkpoint": project_root / "data/state/kr_short_selling_trading_daily_v2.json",
    }


def _rollback_short_selling_transaction(
    paths: dict[str, Path], *, normalized_existed: bool, checkpoint_existed: bool,
) -> None:
    normalized = paths["normalized"]
    previous_normalized = paths["previous_normalized"]
    stage_normalized = paths["stage_normalized"]
    if previous_normalized.exists():
        if normalized.exists():
            shutil.rmtree(normalized)
        previous_normalized.replace(normalized)
    elif not normalized_existed and not stage_normalized.exists() and normalized.exists():
        shutil.rmtree(normalized)

    checkpoint = paths["checkpoint"]
    previous_checkpoint = paths["previous_checkpoint"]
    stage_checkpoint = paths["stage_checkpoint"]
    if previous_checkpoint.exists():
        checkpoint.unlink(missing_ok=True)
        previous_checkpoint.replace(checkpoint)
    elif not checkpoint_existed and not stage_checkpoint.exists() and checkpoint.exists():
        checkpoint.unlink()


def _recover_short_selling_transaction(project_root: Path, market_date: date) -> None:
    paths = _short_selling_transaction_paths(project_root, market_date)
    journal = _read_json(paths["journal"])
    if not journal:
        return
    if journal.get("status") in {
        "SUCCEEDED", "RECOVERED", "FAILED", "PROVISIONAL", "REVISED",
    }:
        shutil.rmtree(paths["transaction_root"], ignore_errors=True)
        return
    if journal.get("dataset") != "trading" or journal.get("market_date") != market_date.isoformat():
        raise MarketDailyIncrementalError("short-selling transaction journal identity mismatch")
    _rollback_short_selling_transaction(
        paths,
        normalized_existed=journal.get("normalized_existed") is True,
        checkpoint_existed=journal.get("checkpoint_existed") is True,
    )
    shutil.rmtree(paths["transaction_root"], ignore_errors=True)
    _atomic_json(paths["journal"], {**journal, "status": "RECOVERED"})


def _run_short_selling_trading_atomic(
    plan: ExactDateDailyPlan, *, project_root: Path, client_factory,
    batch_runner: Callable, throttle=None,
):
    """Run the two trading markets as one recoverable exact-date transaction."""
    paths = _short_selling_transaction_paths(project_root, plan.market_date)
    _recover_short_selling_transaction(project_root, plan.market_date)
    scopes = plan_scopes("trading", (plan.market_date,))
    checkpoint = _read_json(paths["checkpoint"])
    completed = checkpoint.get("completed", {})
    if completed and not isinstance(completed, dict):
        raise MarketDailyIncrementalError("short-selling checkpoint completed map is invalid")

    runner_kwargs = {
        "dataset": "trading", "trading_dates": (plan.market_date,),
        "max_business_calls": max(1, plan.estimated_api_calls),
        "project_root": project_root, "client_factory": client_factory,
        "throttle": throttle,
    }
    if all(scope.scope_id in completed for scope in scopes):
        return batch_runner(**runner_kwargs)

    transaction_root = paths["transaction_root"]
    if transaction_root.exists():
        shutil.rmtree(transaction_root)
    transaction_root.mkdir(parents=True)
    normalized_existed = paths["normalized"].exists()
    checkpoint_existed = paths["checkpoint"].exists()
    journal = {
        "contract_version": 1,
        "dataset": "trading",
        "market_date": plan.market_date.isoformat(),
        "scope_ids": [scope.scope_id for scope in scopes],
        "normalized_existed": normalized_existed,
        "checkpoint_existed": checkpoint_existed,
        "status": "PREPARED",
    }
    _atomic_json(paths["journal"], journal)
    if normalized_existed:
        shutil.copytree(paths["normalized"], paths["stage_normalized"])
    else:
        paths["stage_normalized"].mkdir(parents=True)
    if checkpoint_existed:
        shutil.copy2(paths["checkpoint"], paths["stage_checkpoint"])

    try:
        result = batch_runner(
            **runner_kwargs,
            normalized_root=paths["stage_normalized"],
            checkpoint_path=paths["stage_checkpoint"],
        )
        staged_checkpoint = _read_json(paths["stage_checkpoint"])
        staged_completed = staged_checkpoint.get("completed", {})
        if not isinstance(staged_completed, dict) or not all(
            scope.scope_id in staged_completed for scope in scopes
        ):
            raise MarketDailyIncrementalError(
                "both short-selling trading markets must complete before promotion"
            )
        journal["status"] = "STAGED"
        _atomic_json(paths["journal"], journal)

        paths["normalized"].parent.mkdir(parents=True, exist_ok=True)
        if normalized_existed:
            paths["normalized"].replace(paths["previous_normalized"])
        paths["stage_normalized"].replace(paths["normalized"])
        journal["status"] = "NORMALIZED_PROMOTED"
        _atomic_json(paths["journal"], journal)

        if checkpoint_existed:
            paths["checkpoint"].replace(paths["previous_checkpoint"])
        paths["stage_checkpoint"].replace(paths["checkpoint"])
        journal["status"] = "CHECKPOINT_PROMOTED"
        _atomic_json(paths["journal"], journal)
        journal["status"] = "SUCCEEDED"
        _atomic_json(paths["journal"], journal)
        shutil.rmtree(transaction_root, ignore_errors=True)
        return replace(
            result, checkpoint_path=paths["checkpoint"], normalized_root=paths["normalized"]
        )
    except Exception as error:
        _rollback_short_selling_transaction(
            paths, normalized_existed=normalized_existed,
            checkpoint_existed=checkpoint_existed,
        )
        shutil.rmtree(transaction_root, ignore_errors=True)
        journal["status"] = "FAILED"
        journal["error_type"] = type(error).__name__
        _atomic_json(paths["journal"], journal)
        raise


def plan_short_selling_daily(
    *, project_root: Path, dataset: str, market_date: date,
    latest_finalized_market_date: date, accepted_market_dates: Iterable[date],
    operation_reviewed: bool = False,
    valid_empty_successor_reviewed: bool = False,
) -> ExactDateDailyPlan:
    if dataset not in {"trading", "balance", "investor"}:
        raise ValueError("unsupported short-selling dataset")
    gate = _validate_gate(
        target=market_date, latest_finalized=latest_finalized_market_date,
        accepted_dates=accepted_market_dates, operation_reviewed=operation_reviewed,
    )
    scopes = plan_scopes(dataset, (market_date,))
    checkpoint = _read_json(
        project_root / "data/state" / f"kr_short_selling_{dataset}_daily_v2.json"
    )
    completed = checkpoint.get("completed", {})
    if completed and not isinstance(completed, dict):
        raise MarketDailyIncrementalError("short-selling checkpoint completed map is invalid")
    pending = sum(scope.scope_id not in completed for scope in scopes)
    retained_empty = (
        _retained_balance_valid_empty_stop(checkpoint) if dataset == "balance" else None
    )
    if gate:
        action, reason = gate
    elif (
        retained_empty
        and market_date == retained_empty[0]
        and not valid_empty_successor_reviewed
    ):
        action, reason = "BLOCKED", "RETAINED_VALID_EMPTY_STOP_NO_RETRY"
    elif retained_empty and market_date > retained_empty[0] and not valid_empty_successor_reviewed:
        action, reason = "BLOCKED", "VALID_EMPTY_SUCCESSOR_REVIEW_REQUIRED"
    elif retained_empty and market_date < retained_empty[0]:
        action, reason = "BLOCKED", "TARGET_PRECEDES_RETAINED_VALID_EMPTY_STOP"
    elif pending == 0:
        action, reason = "NOOP_IDEMPOTENT", "ALL_EXACT_DATE_SCOPES_CHECKPOINTED"
    else:
        action, reason = "READY", "EXACT_DATE_REVIEWED_AND_FINAL"
    return ExactDateDailyPlan(
        "SHORT_SELLING_DAILY", f"kr_short_selling_{dataset}_daily", market_date,
        latest_finalized_market_date, action, reason, pending,
    )


def execute_short_selling_daily(
    plan: ExactDateDailyPlan, *, project_root: Path, client_factory,
    runner: Callable = run_short_selling_batch, throttle=None,
):
    if plan.lane != "SHORT_SELLING_DAILY":
        raise ValueError("plan belongs to another lane")
    if plan.action == "BLOCKED":
        raise MarketDailyIncrementalError(plan.reason)
    dataset = plan.dataset.removeprefix("kr_short_selling_").removesuffix("_daily")
    with _run_lock(plan, project_root):
        if dataset == "trading" and runner is run_short_selling_batch:
            result = _run_short_selling_trading_atomic(
                plan, project_root=project_root, client_factory=client_factory,
                batch_runner=runner, throttle=throttle,
            )
        else:
            result = runner(
                dataset=dataset, trading_dates=(plan.market_date,),
                max_business_calls=max(1, plan.estimated_api_calls), project_root=project_root,
                client_factory=client_factory, throttle=throttle,
            )
    if plan.action == "NOOP_IDEMPOTENT" and getattr(result, "requested_business_calls", None) != 0:
        raise MarketDailyIncrementalError("idempotent short-selling replay made a provider call")
    return result


_DATA_GO_DAILY = {
    "market_liquidity": (KR_MARKET_LIQUIDITY_DAILY, ENDPOINTS["market_liquidity"], normalize_market_liquidity),
    "credit_balance": (KR_CREDIT_BALANCE_DAILY, ENDPOINTS["credit_balance"], normalize_credit_balance),
}


def _two_pass_state_path(project_root: Path, dataset: str) -> Path:
    return project_root / "data/state/finality" / f"{dataset}.json"


def _frame_fingerprint(frame, contract, market_date: date) -> str:
    selected = frame[
        frame["date"].astype(str) == market_date.isoformat()
    ].sort_values(list(contract.sort_key), kind="stable")
    if selected.empty:
        raise MarketDailyIncrementalError("exact-date normalized observation is missing")
    selected = selected.loc[:, list(contract.column_names)]
    payload = selected.to_json(
        orient="records", date_format="iso", date_unit="ns", double_precision=15,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _landing_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_two_pass_observation(
    project_root: Path, dataset: str, market_date: date, contract,
) -> dict | None:
    token = market_date.strftime("%Y%m%d")
    landing = project_root / "data/landing/data_go_kr" / dataset / f"{token}.json"
    normalized = project_root / "data/normalized" / dataset
    if not landing.exists() or not normalized.exists():
        return None
    frame = read_dataset(
        normalized, contract, lambda value: validate_data_v1(value, contract),
    )
    return {
        "observed_at": datetime.fromtimestamp(
            landing.stat().st_mtime, tz=timezone.utc,
        ).isoformat(),
        "landing_path": landing.relative_to(project_root).as_posix(),
        "landing_sha256": _landing_sha256(landing),
        "response_status": "COMPLETE",
        "normalized_sha256": _frame_fingerprint(frame, contract, market_date),
        "origin": "LEGACY_HISTORICAL_EXACT_DATE_VALIDATION",
    }


def plan_liquidity_credit_two_pass(
    *, project_root: Path, dataset: str, market_date: date,
    latest_finalized_market_date: date, accepted_market_dates: Iterable[date],
    operation_reviewed: bool = False, max_api_calls: int = 1,
) -> TwoPassDailyPlan:
    if dataset not in _DATA_GO_DAILY:
        raise ValueError("two-pass policy supports only liquidity and credit")
    if max_api_calls != 1:
        raise ValueError("two-pass observation budget must be exactly one API call")
    gate = _validate_gate(
        target=market_date, latest_finalized=latest_finalized_market_date,
        accepted_dates=accepted_market_dates, operation_reviewed=operation_reviewed,
    )
    contract = _DATA_GO_DAILY[dataset][0]
    state = _read_json(_two_pass_state_path(project_root, contract.name))
    day = state.get("dates", {}).get(market_date.strftime("%Y%m%d"), {})
    if gate:
        action, reason, calls = gate[0], gate[1], 0
    elif day.get("status") == "STABLE":
        action, reason, calls = "NOOP_STABLE", "TWO_PASS_STABILITY_CONFIRMED", 0
    elif day.get("observations") or _legacy_two_pass_observation(
        project_root, contract.name, market_date, contract
    ):
        action, reason, calls = "CAPTURE_CONFIRMATION", "LATER_OBSERVATION_REQUIRED", 1
    else:
        action, reason, calls = "CAPTURE_PROVISIONAL", "FIRST_OBSERVATION_REQUIRED", 1
    return TwoPassDailyPlan(
        "LIQUIDITY_CREDIT_TWO_PASS", contract.name, market_date,
        latest_finalized_market_date, action, reason, calls,
    )


def plan_data_go_kr_daily(
    *, project_root: Path, dataset: str, market_date: date,
    latest_finalized_market_date: date, accepted_market_dates: Iterable[date],
    operation_reviewed: bool = False, max_api_calls: int = 1,
) -> ExactDateDailyPlan:
    if max_api_calls < 1:
        raise ValueError("max_api_calls must be positive")
    if dataset in STOCK_LENDING_SPECS:
        contract = STOCK_LENDING_SPECS[dataset].contract
        next_date = (market_date + timedelta(days=1)).strftime("%Y%m%d")
        marker = f"range:{market_date:%Y%m%d}:{next_date}"
        checkpoint = _read_json(
            project_root / "data/state" / f"{contract.name}_historical.json"
        )
        done = marker in set(checkpoint.get("completed_partitions", ())) | set(
            checkpoint.get("valid_empty_partitions", ())
        )
        lane = "LENDING_DAILY"
    elif dataset in _DATA_GO_DAILY:
        contract = _DATA_GO_DAILY[dataset][0]
        marker = market_date.strftime("%Y%m%d")
        checkpoint = _read_json(project_root / "data/state" / f"{contract.name}.json")
        done = marker in set(checkpoint.get("completed_partitions", ())) | set(
            checkpoint.get("valid_empty_partitions", ())
        )
        lane = "LIQUIDITY_CREDIT_DAILY"
    else:
        raise ValueError("unsupported data.go.kr daily dataset")
    gate = _validate_gate(
        target=market_date, latest_finalized=latest_finalized_market_date,
        accepted_dates=accepted_market_dates, operation_reviewed=operation_reviewed,
    )
    if gate:
        action, reason = gate
    elif done:
        action, reason = "NOOP_IDEMPOTENT", "EXACT_DATE_CHECKPOINTED"
    else:
        action, reason = "READY", "EXACT_DATE_REVIEWED_AND_FINAL"
    return ExactDateDailyPlan(
        lane, contract.name, market_date, latest_finalized_market_date,
        action, reason, 0 if done else max_api_calls,
    )


def _data_go_transaction_paths(
    project_root: Path, dataset: str, market_date: date,
) -> dict[str, Path]:
    token = market_date.strftime("%Y%m%d")
    transaction_root = project_root / "data/staging" / dataset / token
    return {
        "journal": project_root / "data/state/transactions" / f"{dataset}_{token}.json",
        "transaction_root": transaction_root,
        "stage_normalized": transaction_root / "staged_normalized",
        "stage_checkpoint": transaction_root / "staged_checkpoint.json",
        "previous_normalized": transaction_root / "previous_normalized",
        "previous_checkpoint": transaction_root / "previous_checkpoint.json",
        "normalized": project_root / "data/normalized" / dataset,
        "checkpoint": project_root / "data/state" / f"{dataset}.json",
        "landing": project_root / "data/landing/data_go_kr" / dataset / f"{token}.json",
    }


def _rollback_data_go_transaction(
    paths: dict[str, Path], *, normalized_existed: bool, checkpoint_existed: bool,
) -> None:
    normalized = paths["normalized"]
    if paths["previous_normalized"].exists():
        if normalized.exists():
            shutil.rmtree(normalized)
        paths["previous_normalized"].replace(normalized)
    elif not normalized_existed and normalized.exists():
        shutil.rmtree(normalized)

    checkpoint = paths["checkpoint"]
    if paths["previous_checkpoint"].exists():
        checkpoint.unlink(missing_ok=True)
        paths["previous_checkpoint"].replace(checkpoint)
    elif not checkpoint_existed:
        checkpoint.unlink(missing_ok=True)


def _recover_data_go_transaction(
    project_root: Path, dataset: str, market_date: date,
) -> None:
    paths = _data_go_transaction_paths(project_root, dataset, market_date)
    journal = _read_json(paths["journal"])
    if not journal:
        return
    if journal.get("status") in {"SUCCEEDED", "RECOVERED", "FAILED"}:
        shutil.rmtree(paths["transaction_root"], ignore_errors=True)
        return
    if (
        journal.get("dataset") != dataset
        or journal.get("market_date") != market_date.isoformat()
    ):
        raise MarketDailyIncrementalError("data.go.kr transaction journal identity mismatch")
    _rollback_data_go_transaction(
        paths,
        normalized_existed=journal.get("normalized_existed") is True,
        checkpoint_existed=journal.get("checkpoint_existed") is True,
    )
    shutil.rmtree(paths["transaction_root"], ignore_errors=True)
    _atomic_json(paths["journal"], {**journal, "status": "RECOVERED"})


def _run_data_go_daily_atomic(
    plan: ExactDateDailyPlan, *, project_root: Path, date_runner: Callable,
    endpoint: str, contract, normalizer: Callable, runner_kwargs: dict,
):
    paths = _data_go_transaction_paths(project_root, plan.dataset, plan.market_date)
    _recover_data_go_transaction(project_root, plan.dataset, plan.market_date)
    transaction_root = paths["transaction_root"]
    if transaction_root.exists():
        shutil.rmtree(transaction_root)
    transaction_root.mkdir(parents=True)
    normalized_existed = paths["normalized"].exists()
    checkpoint_existed = paths["checkpoint"].exists()
    journal = {
        "contract_version": 1,
        "dataset": plan.dataset,
        "market_date": plan.market_date.isoformat(),
        "normalized_existed": normalized_existed,
        "checkpoint_existed": checkpoint_existed,
        "status": "PREPARED",
    }
    _atomic_json(paths["journal"], journal)
    if normalized_existed:
        shutil.copytree(paths["normalized"], paths["stage_normalized"])
    if checkpoint_existed:
        shutil.copy2(paths["checkpoint"], paths["stage_checkpoint"])

    try:
        result = date_runner(
            project_root=project_root, endpoint=endpoint, contract=contract,
            normalizer=normalizer, base_date=plan.market_date.strftime("%Y%m%d"),
            max_calls=max(1, plan.estimated_api_calls), resume=True,
            state_path=paths["stage_checkpoint"],
            normalized_root=paths["stage_normalized"],
            landing_path=paths["landing"], **runner_kwargs,
        )
        if getattr(result, "status", None) not in {"COMPLETE", "VALID_EMPTY"}:
            raise MarketDailyIncrementalError("exact-date collector returned an invalid status")
        checkpoint = _read_json(paths["stage_checkpoint"])
        marker = plan.market_date.strftime("%Y%m%d")
        completed = marker in set(checkpoint.get("completed_partitions", ()))
        valid_empty = marker in set(checkpoint.get("valid_empty_partitions", ()))
        if result.status == "COMPLETE" and (not completed or not paths["stage_normalized"].exists()):
            raise MarketDailyIncrementalError("validated exact date was not staged completely")
        if result.status == "VALID_EMPTY" and not valid_empty:
            raise MarketDailyIncrementalError("valid-empty exact date was not staged completely")
        if not paths["landing"].exists():
            raise MarketDailyIncrementalError("exact-date Landing evidence is missing")
        journal["status"] = "STAGED"
        journal["result_status"] = result.status
        _atomic_json(paths["journal"], journal)

        if result.status == "COMPLETE":
            paths["normalized"].parent.mkdir(parents=True, exist_ok=True)
            if normalized_existed:
                paths["normalized"].replace(paths["previous_normalized"])
            paths["stage_normalized"].replace(paths["normalized"])
            journal["status"] = "NORMALIZED_PROMOTED"
            _atomic_json(paths["journal"], journal)

        if checkpoint_existed:
            paths["checkpoint"].replace(paths["previous_checkpoint"])
        paths["stage_checkpoint"].replace(paths["checkpoint"])
        journal["status"] = "CHECKPOINT_PROMOTED"
        _atomic_json(paths["journal"], journal)
        journal["status"] = "SUCCEEDED"
        _atomic_json(paths["journal"], journal)
        shutil.rmtree(transaction_root, ignore_errors=True)
        return result
    except Exception as error:
        _rollback_data_go_transaction(
            paths, normalized_existed=normalized_existed,
            checkpoint_existed=checkpoint_existed,
        )
        shutil.rmtree(transaction_root, ignore_errors=True)
        journal["status"] = "FAILED"
        journal["error_type"] = type(error).__name__
        _atomic_json(paths["journal"], journal)
        raise


def execute_data_go_kr_daily(
    plan: ExactDateDailyPlan, *, project_root: Path,
    date_runner: Callable = collect_date,
    lending_runner: Callable = collect_stock_lending_history,
    **runner_kwargs,
):
    if plan.lane not in {"LENDING_DAILY", "LIQUIDITY_CREDIT_DAILY"}:
        raise ValueError("plan belongs to another lane")
    if plan.action == "BLOCKED":
        raise MarketDailyIncrementalError(plan.reason)
    if plan.lane == "LENDING_DAILY":
        key = next(key for key, spec in STOCK_LENDING_SPECS.items() if spec.contract.name == plan.dataset)
        with _run_lock(plan, project_root):
            result = lending_runner(
                project_root=project_root, spec=STOCK_LENDING_SPECS[key],
                start_date=plan.market_date.strftime("%Y%m%d"),
                end_date=(plan.market_date + timedelta(days=1)).strftime("%Y%m%d"),
                max_calls=max(1, plan.estimated_api_calls), max_attempts=1,
                resume=True, **runner_kwargs,
            )
        if plan.action == "NOOP_IDEMPOTENT" and getattr(result, "api_calls", None) != 0:
            raise MarketDailyIncrementalError("idempotent lending replay made a provider call")
        return result
    key = next(key for key, values in _DATA_GO_DAILY.items() if values[0].name == plan.dataset)
    contract, endpoint, normalizer = _DATA_GO_DAILY[key]
    with _run_lock(plan, project_root):
        if plan.action == "NOOP_IDEMPOTENT":
            result = date_runner(
                project_root=project_root, endpoint=endpoint, contract=contract,
                normalizer=normalizer, base_date=plan.market_date.strftime("%Y%m%d"),
                max_calls=1, resume=True, **runner_kwargs,
            )
        else:
            result = _run_data_go_daily_atomic(
                plan, project_root=project_root, date_runner=date_runner,
                endpoint=endpoint, contract=contract, normalizer=normalizer,
                runner_kwargs=runner_kwargs,
            )
    if plan.action == "NOOP_IDEMPOTENT" and getattr(result, "pages", None) != 0:
        raise MarketDailyIncrementalError("idempotent data.go.kr replay made a provider call")
    return result


def execute_liquidity_credit_two_pass(
    plan: TwoPassDailyPlan, *, project_root: Path,
    date_runner: Callable = collect_date, observed_at: datetime | None = None,
    **runner_kwargs,
) -> TwoPassResult:
    """Capture an immutable observation and publish only after an identical later pass."""
    if plan.lane != "LIQUIDITY_CREDIT_TWO_PASS":
        raise ValueError("plan belongs to another lane")
    if plan.action == "BLOCKED":
        raise MarketDailyIncrementalError(plan.reason)
    token = plan.market_date.strftime("%Y%m%d")
    state_path = _two_pass_state_path(project_root, plan.dataset)
    state = _read_json(state_path) or {
        "contract_version": 1, "dataset": plan.dataset, "dates": {}, "failures": [],
    }
    if state.get("dataset") != plan.dataset or not isinstance(state.get("dates"), dict):
        raise MarketDailyIncrementalError("two-pass finality state is invalid")
    day = state["dates"].setdefault(token, {
        "market_date": plan.market_date.isoformat(), "status": "UNOBSERVED",
        "comparison_fields": [], "observations": [],
    })
    if plan.action == "NOOP_STABLE":
        return TwoPassResult(
            plan.dataset, plan.market_date.isoformat(), "NOOP_STABLE", 0,
            len(day.get("observations", ())), True, None,
        )

    key = next(key for key, values in _DATA_GO_DAILY.items() if values[0].name == plan.dataset)
    contract, endpoint, normalizer = _DATA_GO_DAILY[key]
    if not day.get("observations"):
        legacy = _legacy_two_pass_observation(
            project_root, plan.dataset, plan.market_date, contract,
        )
        if legacy:
            day["comparison_fields"] = list(contract.column_names)
            day["observations"].append(legacy)
            day["anchor_sha256"] = legacy["normalized_sha256"]
            day["status"] = "PROVISIONAL"
            _atomic_json(state_path, state)

    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    paths = _data_go_transaction_paths(project_root, plan.dataset, plan.market_date)
    paths["landing"] = (
        project_root / "data/landing/data_go_kr" / plan.dataset
        / "observations" / token / f"{stamp}.json"
    )
    with _run_lock(
        ExactDateDailyPlan(
            plan.lane, plan.dataset, plan.market_date,
            plan.latest_finalized_market_date, plan.action, plan.reason,
            plan.estimated_api_calls,
        ),
        project_root,
    ):
        _recover_data_go_transaction(project_root, plan.dataset, plan.market_date)
        if paths["transaction_root"].exists():
            shutil.rmtree(paths["transaction_root"])
        paths["transaction_root"].mkdir(parents=True)
        normalized_existed = paths["normalized"].exists()
        checkpoint_existed = paths["checkpoint"].exists()
        if normalized_existed:
            shutil.copytree(paths["normalized"], paths["stage_normalized"])
        if checkpoint_existed:
            shutil.copy2(paths["checkpoint"], paths["stage_checkpoint"])
        journal = {
            "contract_version": 2, "dataset": plan.dataset,
            "market_date": plan.market_date.isoformat(),
            "normalized_existed": normalized_existed,
            "checkpoint_existed": checkpoint_existed,
            "status": "CAPTURING", "observation_landing": paths["landing"].relative_to(project_root).as_posix(),
        }
        _atomic_json(paths["journal"], journal)
        prior_state = json.loads(json.dumps(state))
        try:
            result = date_runner(
                project_root=project_root, endpoint=endpoint, contract=contract,
                normalizer=normalizer, base_date=token, max_calls=1, resume=False,
                state_path=paths["stage_checkpoint"],
                normalized_root=paths["stage_normalized"],
                landing_path=paths["landing"], **runner_kwargs,
            )
            if getattr(result, "status", None) not in {"COMPLETE", "VALID_EMPTY"}:
                raise MarketDailyIncrementalError("two-pass collector returned an invalid status")
            if not paths["landing"].exists():
                raise MarketDailyIncrementalError("two-pass Landing evidence is missing")
            if result.status == "COMPLETE":
                frame = read_dataset(
                    paths["stage_normalized"], contract,
                    lambda value: validate_data_v1(value, contract),
                )
                normalized_sha = _frame_fingerprint(frame, contract, plan.market_date)
            else:
                normalized_sha = hashlib.sha256(b"VALID_EMPTY").hexdigest()
            observation = {
                "observed_at": now.isoformat(),
                "landing_path": paths["landing"].relative_to(project_root).as_posix(),
                "landing_sha256": _landing_sha256(paths["landing"]),
                "response_status": result.status,
                "normalized_sha256": normalized_sha,
                "origin": "TWO_PASS_BOUNDED_CAPTURE",
            }
            observations = day.setdefault("observations", [])
            previous_sha = day.get("anchor_sha256")
            observations.append(observation)
            day["comparison_fields"] = list(contract.column_names)
            day["last_observed_at"] = now.isoformat()
            if previous_sha is None:
                day["anchor_sha256"] = normalized_sha
                day["status"] = "PROVISIONAL"
                journal["status"] = "PROVISIONAL"
                final_status = "PROVISIONAL"
            elif previous_sha != normalized_sha:
                day["anchor_sha256"] = normalized_sha
                day["status"] = "REVISED"
                day["revision_count"] = int(day.get("revision_count", 0)) + 1
                journal["status"] = "REVISED"
                final_status = "REVISED"
            else:
                journal["status"] = "STABLE_MATCH"
                _atomic_json(paths["journal"], journal)
                if result.status == "COMPLETE":
                    paths["normalized"].parent.mkdir(parents=True, exist_ok=True)
                    if normalized_existed:
                        paths["normalized"].replace(paths["previous_normalized"])
                    paths["stage_normalized"].replace(paths["normalized"])
                if checkpoint_existed:
                    paths["checkpoint"].replace(paths["previous_checkpoint"])
                paths["stage_checkpoint"].replace(paths["checkpoint"])
                day["status"] = "STABLE"
                day["stable_at"] = now.isoformat()
                day["stable_response_status"] = result.status
                final_status = "STABLE"
                journal["status"] = "SUCCEEDED"
            _atomic_json(state_path, state)
            _atomic_json(paths["journal"], journal)
            shutil.rmtree(paths["transaction_root"], ignore_errors=True)
            return TwoPassResult(
                plan.dataset, plan.market_date.isoformat(), final_status,
                int(getattr(result, "pages", 0)), len(observations),
                final_status == "STABLE", observation["landing_path"],
            )
        except Exception as error:
            _rollback_data_go_transaction(
                paths, normalized_existed=normalized_existed,
                checkpoint_existed=checkpoint_existed,
            )
            state = prior_state
            state.setdefault("failures", []).append({
                "market_date": plan.market_date.isoformat(),
                "observed_at": now.isoformat(), "error_type": type(error).__name__,
                "landing_path": (
                    paths["landing"].relative_to(project_root).as_posix()
                    if paths["landing"].exists() else None
                ),
            })
            _atomic_json(state_path, state)
            journal["status"] = "FAILED"
            journal["error_type"] = type(error).__name__
            _atomic_json(paths["journal"], journal)
            shutil.rmtree(paths["transaction_root"], ignore_errors=True)
            raise


def health_from_exact_date_plan(
    plan: ExactDateDailyPlan, *, spec: DatasetOperationSpec, run_id: str,
    actual_latest: date | None, collector_status: StageStatus = StageStatus.NOT_RUN,
    validation_status: StageStatus = StageStatus.NOT_RUN,
    downstream_status: StageStatus = StageStatus.NOT_RUN,
    auth_status: AuthStatus = AuthStatus.UNKNOWN,
) -> DatasetHealth:
    """Translate an explicit offline plan to a deterministic health row."""
    if spec.dataset_id != plan.dataset:
        raise ValueError("operation plan and registry dataset differ")
    if plan.action == "BLOCKED":
        freshness = FreshnessStatus.BLOCKED
        review = True
        blocked_reason = plan.reason
    elif actual_latest is None:
        freshness = FreshnessStatus.MISSING
        review = True
        blocked_reason = None
    elif actual_latest >= plan.market_date:
        freshness = FreshnessStatus.CURRENT
        review = False
        blocked_reason = None
    else:
        freshness = FreshnessStatus.STALE
        review = True
        blocked_reason = None
    return DatasetHealth(
        run_id=run_id, dataset_id=spec.dataset_id, cadence=spec.cadence,
        tier=spec.tier, primary_source=spec.primary_source,
        expected_latest=plan.market_date, actual_latest=actual_latest,
        freshness_status=freshness, collector_status=collector_status,
        validation_status=validation_status, downstream_status=downstream_status,
        auth_status=auth_status, error_code=None, review_required=review,
        warnings=(plan.reason,), dashboard_required=spec.dashboard_required,
        model_input_required=spec.model_input_required, pit_status=spec.pit_status,
        operational_eligibility=(
            OperationalEligibility.BLOCKED
            if plan.action == "BLOCKED" else spec.operational_eligibility
        ),
        predictive_eligibility=spec.predictive_eligibility,
        operational_classification=spec.operational_classification,
        predictive_classification=spec.predictive_classification,
        blocked_reason=blocked_reason,
    )


__all__ = [
    "ExactDateDailyPlan", "MarketDailyIncrementalError", "TwoPassDailyPlan",
    "TwoPassResult", "execute_liquidity_credit_two_pass",
    "execute_data_go_kr_daily", "execute_short_selling_daily",
    "health_from_exact_date_plan", "plan_data_go_kr_daily",
    "plan_liquidity_credit_two_pass", "plan_short_selling_daily",
    "short_selling_raw_call_budget", "SHORT_SELLING_EXACT_DATE_SCOPE_COUNTS",
    "SHORT_SELLING_FINALITY_POLICIES",
]
