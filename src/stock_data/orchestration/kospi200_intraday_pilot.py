"""Fail-closed planning and offline validation for UR-014."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Mapping
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq

from stock_data.contracts.kospi200_intraday_pilot import (
    LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT,
    RAW_BAR_TIME_POLICY,
    RAW_REVISION_POLICY,
)
from stock_data.providers.ls_t8412 import normalize_retained_t8412_capture
from stock_data.storage.contract_arrow import (
    dataframe_to_contract_table,
    restore_contract_dates,
)


PILOT_DATE = date(2026, 8, 12)
PILOT_SYMBOLS = ("000660", "005930")


@dataclass(frozen=True)
class KOSPI200IntradayPilotPlan:
    market_date: date
    membership_observation_date: date
    symbols: tuple[str, ...]
    provider: str
    source_operation: str
    interval_minutes: int
    oauth_calls: int
    data_calls: int
    retries: int
    entitlement_verification: str
    bar_time_policy: str
    revision_policy: str
    action: str
    reason: str


@dataclass(frozen=True)
class KOSPI200IntradayCaptureBatch:
    """One injected, already-captured response batch; this module has no transport."""

    responses: Mapping[str, bytes]
    captured_at: datetime
    oauth_calls: int
    data_calls: int
    retries: int = 0


class KOSPI200IntradayTransactionError(RuntimeError):
    """The bounded offline transaction failed before a complete commit."""


def plan_kospi200_intraday_pilot(
    *,
    market_date: date = PILOT_DATE,
    membership_observation_date: date = PILOT_DATE,
    symbols: tuple[str, ...] = PILOT_SYMBOLS,
    data_status_route_selected: bool = False,
    active_runbook_reviewed: bool = False,
    provider_entitlement_verified: bool = False,
    bounded_entitlement_attempt_authorized: bool = False,
    raw_bar_time_policy: str = RAW_BAR_TIME_POLICY,
    raw_revision_policy: str = RAW_REVISION_POLICY,
) -> KOSPI200IntradayPilotPlan:
    """Return an executable plan only after every current authority gate passes.

    A reviewed Raw-only policy is not a claim that the provider's time label or
    historical revision freeze has been resolved.  The first in-budget t8412
    response may establish entitlement; it is never an extra probe call.
    """
    entitlement_ready = (
        provider_entitlement_verified or bounded_entitlement_attempt_authorized
    )
    gates = (
        (market_date == PILOT_DATE, "ONLY_RETAINED_EXACT_20260812_MEMBERSHIP_IS_ACCEPTED"),
        (membership_observation_date == market_date, "MEMBERSHIP_BACKPROJECTION_FORBIDDEN"),
        (symbols == PILOT_SYMBOLS, "ONLY_REVIEWED_TWO_SYMBOL_SAMPLE_IS_ACCEPTED"),
        (data_status_route_selected, "DATA_STATUS_ROUTE_SELECTION_REQUIRED"),
        (active_runbook_reviewed, "ACTIVE_RUNBOOK_REVIEW_REQUIRED"),
        (entitlement_ready, "LS_T8412_BOUNDED_ENTITLEMENT_CHECK_REQUIRED"),
        (
            raw_bar_time_policy == RAW_BAR_TIME_POLICY,
            "RAW_BAR_TIME_POLICY_REVIEW_REQUIRED",
        ),
        (
            raw_revision_policy == RAW_REVISION_POLICY,
            "RAW_REVISION_POLICY_REVIEW_REQUIRED",
        ),
    )
    action, reason = "READY", "EXACT_DATE_TWO_SYMBOL_NATIVE_15M_PILOT"
    for passed, gate_reason in gates:
        if not passed:
            action, reason = "REVIEW_REQUIRED", gate_reason
            break
    return KOSPI200IntradayPilotPlan(
        market_date=market_date,
        membership_observation_date=membership_observation_date,
        symbols=symbols,
        provider="LS_OPENAPI",
        source_operation="LS_OPENAPI:/stock/chart:t8412",
        interval_minutes=15,
        oauth_calls=1 if action == "READY" else 0,
        data_calls=len(symbols) if action == "READY" else 0,
        retries=0,
        entitlement_verification=(
            "VERIFIED_BEFORE_RUN"
            if provider_entitlement_verified
            else (
                "FIRST_IN_BUDGET_T8412_SUCCESS_RESPONSE"
                if bounded_entitlement_attempt_authorized
                else "UNVERIFIED"
            )
        ),
        bar_time_policy=raw_bar_time_policy,
        revision_policy=raw_revision_policy,
        action=action,
        reason=reason,
    )


def validate_retained_pilot_responses(
    plan: KOSPI200IntradayPilotPlan,
    *,
    membership: pd.DataFrame,
    responses: Mapping[str, bytes],
    captured_at: datetime,
) -> pd.DataFrame:
    """Validate all intended captures together; return no partial sample."""
    if plan.action != "READY":
        raise RuntimeError(plan.reason)
    required_membership = {"date", "observation_date", "index_ticker", "symbol"}
    if membership.empty or not required_membership.issubset(membership.columns):
        raise ValueError("exact KOSPI200 membership is missing")
    exact = membership.loc[
        membership["date"].eq(plan.market_date)
        & membership["observation_date"].eq(plan.membership_observation_date)
        & membership["index_ticker"].astype(str).eq("1028")
    ]
    if exact["symbol"].duplicated().any():
        raise ValueError("exact KOSPI200 membership contains duplicate symbols")
    if not set(plan.symbols).issubset(set(exact["symbol"].astype(str))):
        raise ValueError("pilot symbol is not in the same-date KOSPI200 membership")
    if set(responses) != set(plan.symbols):
        raise ValueError("all intended pilot responses must be present together")
    frames = [
        normalize_retained_t8412_capture(
            responses[symbol],
            market_date=plan.market_date,
            membership_observation_date=plan.membership_observation_date,
            expected_symbol=symbol,
            captured_at=captured_at,
        )
        for symbol in plan.symbols
    ]
    combined = pd.concat(frames, ignore_index=True).sort_values(
        ["market_date", "symbol", "provider_time"], kind="stable"
    ).reset_index(drop=True)
    if combined.duplicated(["provider", "symbol", "market_date", "provider_time"]).any():
        raise ValueError("pilot response has duplicate date-time-symbol keys")
    return combined


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    if not path.is_file():
        raise KOSPI200IntradayTransactionError(f"required transaction file is missing: {path}")
    return _sha256_bytes(path.read_bytes())


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise KOSPI200IntradayTransactionError("transaction JSON is not an object")
    return value


def _write_bytes_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise KOSPI200IntradayTransactionError(
                "immutable Landing target already exists"
            ) from error
    finally:
        temporary.unlink(missing_ok=True)


def _transaction_paths(project_root: Path, market_date: date) -> dict[str, Path]:
    day = market_date.strftime("%Y%m%d")
    return {
        "checkpoint": project_root / "data/state/ls_t8412_kospi200_constituent_15m_pilot.json",
        "journal": project_root / (
            "data/state/transactions/"
            f"ls_t8412_kospi200_constituent_15m_pilot_{day}.json"
        ),
        "projection": project_root / (
            "data/raw/ls_t8412_kospi200_constituent_15m_pilot/"
            f"year={market_date.year}/data.parquet"
        ),
    }


def _inside(project_root: Path, relative: object) -> Path:
    root = project_root.resolve()
    path = (root / str(relative)).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise KOSPI200IntradayTransactionError(
            "transaction path escapes project root"
        ) from error
    return path


def _checkpoint(project_root: Path, market_date: date) -> dict[str, object]:
    current = _read_json(_transaction_paths(project_root, market_date)["checkpoint"])
    if not current:
        return {
            "schema": "ls_t8412_kospi200_constituent_15m_pilot.checkpoint.v1",
            "dataset": LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT.name,
            "completed_dates": {},
        }
    if (
        current.get("schema")
        != "ls_t8412_kospi200_constituent_15m_pilot.checkpoint.v1"
        or current.get("dataset") != LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT.name
        or not isinstance(current.get("completed_dates"), dict)
    ):
        raise KOSPI200IntradayTransactionError("pilot checkpoint identity differs")
    return current


def _restore_target(target: Path, backup: Path, existed: object) -> None:
    if existed is True:
        if not backup.is_file():
            raise KOSPI200IntradayTransactionError("transaction backup is missing")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.rollback")
        shutil.copyfile(backup, temporary)
        os.replace(temporary, target)
    elif existed is False:
        target.unlink(missing_ok=True)
    else:
        raise KOSPI200IntradayTransactionError("transaction prior-state flag is invalid")


def recover_offline_kospi200_intraday_pilot(
    project_root: Path, market_date: date = PILOT_DATE,
) -> str | None:
    """Roll back an interrupted projection/checkpoint commit without touching Landing."""
    paths = _transaction_paths(project_root, market_date)
    journal = _read_json(paths["journal"])
    if not journal:
        return None
    status = str(journal.get("status", ""))
    if status in {"SUCCEEDED", "FAILED", "RECOVERED"}:
        return status
    if (
        journal.get("schema") != "ls_t8412_kospi200_intraday.transaction.v1"
        or journal.get("market_date") != market_date.isoformat()
        or journal.get("symbols") != list(PILOT_SYMBOLS)
    ):
        raise KOSPI200IntradayTransactionError("pilot transaction journal identity differs")
    if status in {"STAGED", "PROJECTION_PROMOTED", "CHECKPOINT_PROMOTED"}:
        _restore_target(
            paths["projection"], Path(str(journal["projection_backup_path"])),
            journal.get("projection_existed"),
        )
        _restore_target(
            paths["checkpoint"], Path(str(journal["checkpoint_backup_path"])),
            journal.get("checkpoint_existed"),
        )
    _atomic_json(paths["journal"], {**journal, "status": "RECOVERED"})
    return "RECOVERED"


def _verify_completed(
    project_root: Path, market_date: date, record: Mapping[str, object],
) -> None:
    paths = _transaction_paths(project_root, market_date)
    if record.get("symbols") != list(PILOT_SYMBOLS):
        raise KOSPI200IntradayTransactionError("completed pilot symbol scope differs")
    landing = record.get("landing")
    if not isinstance(landing, dict) or set(landing) != set(PILOT_SYMBOLS):
        raise KOSPI200IntradayTransactionError("completed Landing scope differs")
    for symbol in PILOT_SYMBOLS:
        entry = landing[symbol]
        if not isinstance(entry, dict):
            raise KOSPI200IntradayTransactionError("completed Landing entry is invalid")
        body_path = _inside(project_root, entry.get("path", ""))
        if _sha256_file(body_path) != entry.get("sha256"):
            raise KOSPI200IntradayTransactionError("completed Landing hash differs")
    if _sha256_file(paths["projection"]) != record.get("projection_sha256"):
        raise KOSPI200IntradayTransactionError("completed projection hash differs")
    frame = restore_contract_dates(
        pd.read_parquet(paths["projection"]),
        LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT,
    )
    dated = frame.loc[frame["market_date"].eq(market_date.isoformat())]
    if (
        len(dated) != record.get("rows")
        or set(dated["symbol"].astype(str)) != set(PILOT_SYMBOLS)
        or dated.duplicated(
            list(LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT.primary_key)
        ).any()
    ):
        raise KOSPI200IntradayTransactionError("completed projection scope differs")


def _stage_projection(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        dataframe_to_contract_table(
            frame, LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT
        ),
        path,
    )
    verified = restore_contract_dates(
        pd.read_parquet(path), LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT
    )
    if len(verified) != len(frame):
        raise KOSPI200IntradayTransactionError("staged projection row count differs")


def execute_offline_kospi200_intraday_pilot(
    plan: KOSPI200IntradayPilotPlan,
    *,
    project_root: Path,
    membership: pd.DataFrame,
    capture_builder: Callable[[KOSPI200IntradayPilotPlan], KOSPI200IntradayCaptureBatch] | None,
) -> dict[str, object]:
    """Commit an injected exact-scope capture; performs no OAuth or HTTP itself."""
    paths = _transaction_paths(project_root, plan.market_date)
    recover_offline_kospi200_intraday_pilot(project_root, plan.market_date)
    checkpoint = _checkpoint(project_root, plan.market_date)
    target = plan.market_date.isoformat()
    completed = checkpoint["completed_dates"]
    assert isinstance(completed, dict)
    if target in completed:
        record = completed[target]
        if not isinstance(record, dict):
            raise KOSPI200IntradayTransactionError("completed pilot record is invalid")
        _verify_completed(project_root, plan.market_date, record)
        return {
            "status": "NOOP_ALREADY_SUCCEEDED", "oauth_calls": 0,
            "data_calls": 0, "retries": 0,
        }
    if plan.action != "READY" or capture_builder is None:
        raise KOSPI200IntradayTransactionError(
            plan.reason if plan.action != "READY" else "capture builder is required"
        )

    run_id = uuid4().hex
    stage = project_root / "data/staging/ls_t8412_kospi200_intraday_pilot" / f"run={run_id}"
    projection_stage = stage / "projection.parquet"
    projection_backup = stage / "previous_projection.parquet"
    checkpoint_backup = stage / "previous_checkpoint.json"
    landing_root = (
        project_root / "data/landing/ls_openapi/t8412_kospi200_constituent_15m"
        / f"market_date={plan.market_date:%Y%m%d}" / f"run={run_id}"
    )
    journal: dict[str, object] = {
        "schema": "ls_t8412_kospi200_intraday.transaction.v1",
        "run_id": run_id,
        "market_date": target,
        "symbols": list(plan.symbols),
        "projection_existed": paths["projection"].is_file(),
        "checkpoint_existed": paths["checkpoint"].is_file(),
        "projection_backup_path": str(projection_backup.resolve()),
        "checkpoint_backup_path": str(checkpoint_backup.resolve()),
        "status": "PREPARED",
    }
    _atomic_json(paths["journal"], journal)
    projection_promoted = False
    try:
        capture = capture_builder(plan)
        response_symbols = set(capture.responses)
        if (
            capture.oauth_calls != plan.oauth_calls
            or capture.data_calls != len(response_symbols)
            or capture.data_calls > plan.data_calls
            or capture.retries != plan.retries
            or not response_symbols.issubset(set(plan.symbols))
        ):
            raise KOSPI200IntradayTransactionError("capture scope or call budget differs")
        landing: dict[str, dict[str, str]] = {}
        for symbol in plan.symbols:
            if symbol not in capture.responses:
                continue
            body = capture.responses[symbol]
            body_path = landing_root / f"symbol={symbol}" / "response.json"
            _write_bytes_new(body_path, body)
            landing[symbol] = {
                "path": body_path.relative_to(project_root).as_posix(),
                "sha256": _sha256_bytes(body),
            }
        journal.update({
            "status": "LANDING_RETAINED", "landing": landing,
            "oauth_calls": capture.oauth_calls, "data_calls": capture.data_calls,
            "retries": capture.retries,
        })
        _atomic_json(paths["journal"], journal)
        if response_symbols != set(plan.symbols) or capture.data_calls != plan.data_calls:
            raise KOSPI200IntradayTransactionError("all intended responses were not captured")
        projected = validate_retained_pilot_responses(
            plan, membership=membership, responses=capture.responses,
            captured_at=capture.captured_at,
        )
        journal["status"] = "VALIDATED"
        _atomic_json(paths["journal"], journal)

        if paths["projection"].is_file():
            existing = restore_contract_dates(
                pd.read_parquet(paths["projection"]),
                LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT,
            )
            if existing["market_date"].eq(target).any():
                raise KOSPI200IntradayTransactionError(
                    "projection contains target date without completed checkpoint"
                )
            projected = pd.concat([existing, projected], ignore_index=True)
        projected = projected.sort_values(
            list(LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT.sort_key), kind="stable"
        ).reset_index(drop=True)
        if projected.duplicated(
            list(LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT.primary_key)
        ).any():
            raise KOSPI200IntradayTransactionError("projection has duplicate primary keys")
        _stage_projection(projection_stage, projected)
        if paths["projection"].is_file():
            projection_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(paths["projection"], projection_backup)
        if paths["checkpoint"].is_file():
            checkpoint_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(paths["checkpoint"], checkpoint_backup)
        journal["status"] = "STAGED"
        _atomic_json(paths["journal"], journal)

        paths["projection"].parent.mkdir(parents=True, exist_ok=True)
        os.replace(projection_stage, paths["projection"])
        projection_promoted = True
        journal["status"] = "PROJECTION_PROMOTED"
        _atomic_json(paths["journal"], journal)
        record: dict[str, object] = {
            "symbols": list(plan.symbols), "rows": len(projected.loc[
                projected["market_date"].astype(str).eq(target)
            ]),
            "interval_minutes": plan.interval_minutes,
            "source_operation": plan.source_operation,
            "landing": landing,
            "projection_path": paths["projection"].relative_to(project_root).as_posix(),
            "projection_sha256": _sha256_file(paths["projection"]),
            "oauth_calls": capture.oauth_calls, "data_calls": capture.data_calls,
            "retries": capture.retries,
        }
        completed[target] = record
        _atomic_json(paths["checkpoint"], checkpoint)
        journal["status"] = "CHECKPOINT_PROMOTED"
        _atomic_json(paths["journal"], journal)
        _verify_completed(project_root, plan.market_date, record)
        journal["status"] = "SUCCEEDED"
        _atomic_json(paths["journal"], journal)
        return {
            "status": "SUCCEEDED", "oauth_calls": capture.oauth_calls,
            "data_calls": capture.data_calls, "retries": capture.retries,
            "rows": record["rows"], "landing_responses": len(landing),
        }
    except Exception as error:
        if projection_promoted:
            _restore_target(
                paths["projection"], projection_backup, journal["projection_existed"]
            )
            _restore_target(
                paths["checkpoint"], checkpoint_backup, journal["checkpoint_existed"]
            )
        journal.update({"status": "FAILED", "error_type": type(error).__name__})
        _atomic_json(paths["journal"], journal)
        raise


__all__ = [
    "KOSPI200IntradayCaptureBatch", "KOSPI200IntradayPilotPlan",
    "KOSPI200IntradayTransactionError", "PILOT_DATE", "PILOT_SYMBOLS",
    "execute_offline_kospi200_intraday_pilot", "plan_kospi200_intraday_pilot",
    "recover_offline_kospi200_intraday_pilot", "validate_retained_pilot_responses",
]
