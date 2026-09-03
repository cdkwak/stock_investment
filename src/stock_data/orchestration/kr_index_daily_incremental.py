"""Offline bounded daily append operations for the two Korean index datasets.

This module deliberately has no provider dependency.  A caller must supply an
already captured Landing artifact and an explicitly reviewed, finalized market
date.  The operation validates that artifact, merges only the supplied date,
and commits the normalized dataset through the existing atomic writers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import base64
import hashlib
import json
from pathlib import Path
import os
import re
import shutil
from typing import Callable

import pandas as pd

from stock_data.contracts.base import DatasetContract
from stock_data.contracts.kr_index_daily import (
    KR_INDEX_DAILY,
    KR_INDEX_MARKETS,
)
from stock_data.contracts.kospi200_index_daily import KR_KOSPI200_INDEX_DAILY
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_index_daily import validate_kr_index_daily
from stock_data.validation.kospi200_index_daily import validate_kospi200_index_daily


class IndexDailyOperationError(RuntimeError):
    """Fail-closed error for an unsafe or incomplete offline append."""


@dataclass(frozen=True)
class IndexDailyRoute:
    dataset: str
    contract: DatasetContract
    validator: Callable[[pd.DataFrame], None]
    reader: Callable[[Path], pd.DataFrame]
    writer: Callable[[pd.DataFrame, Path], None]
    key: tuple[str, ...]


def validate_registered_kr_index_daily(frame: pd.DataFrame) -> None:
    """Extend the legacy broad-index validator with registered sector indices."""
    if tuple(frame.columns) != KR_INDEX_DAILY.column_names:
        raise IndexDailyOperationError("KR index columns do not match the contract")
    if frame.empty:
        raise IndexDailyOperationError("KR index dataset must not be empty")
    symbols = set(frame["symbol"].astype(str)) if "symbol" in frame else set()
    unknown = symbols - set(KR_INDEX_MARKETS)
    if unknown:
        raise IndexDailyOperationError(f"unregistered KR index symbols: {sorted(unknown)}")
    for symbol in sorted(symbols):
        subset = frame.loc[frame["symbol"].astype(str).eq(symbol)].copy()
        expected_market = KR_INDEX_MARKETS[symbol]
        if not subset["market"].astype(str).eq(expected_market).all():
            raise IndexDailyOperationError(f"KR index symbol/market mismatch: {symbol}")
        # Reuse the established schema/numeric/OHLC checks by projecting each
        # registered series to its broad-market validation identity.
        subset["symbol"] = expected_market
        subset["market"] = expected_market
        subset = subset.sort_values(list(KR_INDEX_DAILY.sort_key), kind="stable").reset_index(drop=True)
        validate_kr_index_daily(subset)
    if frame.duplicated(list(KR_INDEX_DAILY.primary_key)).any():
        raise IndexDailyOperationError("date+symbol contains duplicates")
    order = frame.sort_values(list(KR_INDEX_DAILY.sort_key), kind="stable").index
    if not order.equals(frame.index):
        raise IndexDailyOperationError("KR index rows must be sorted by date and symbol")


def read_registered_kr_index_daily(root: Path) -> pd.DataFrame:
    return read_dataset(root, KR_INDEX_DAILY, validate_registered_kr_index_daily)


def write_registered_kr_index_daily(frame: pd.DataFrame, root: Path) -> None:
    write_dataset_atomic(frame, root, KR_INDEX_DAILY, validate_registered_kr_index_daily)


def _read_kospi200(root: Path) -> pd.DataFrame:
    return read_dataset(root, KR_KOSPI200_INDEX_DAILY, validate_kospi200_index_daily)


def _write_kospi200(frame: pd.DataFrame, root: Path) -> None:
    write_dataset_atomic(frame, root, KR_KOSPI200_INDEX_DAILY, validate_kospi200_index_daily)


KR_INDEX_ROUTE = IndexDailyRoute(
    dataset="kr_index_daily",
    contract=KR_INDEX_DAILY,
    validator=validate_registered_kr_index_daily,
    reader=read_registered_kr_index_daily,
    writer=write_registered_kr_index_daily,
    key=KR_INDEX_DAILY.primary_key,
)
KOSPI200_ROUTE = IndexDailyRoute(
    dataset="kr_kospi200_index_daily",
    contract=KR_KOSPI200_INDEX_DAILY,
    validator=validate_kospi200_index_daily,
    reader=_read_kospi200,
    writer=_write_kospi200,
    key=KR_KOSPI200_INDEX_DAILY.primary_key,
)
ROUTES = {KR_INDEX_ROUTE.dataset: KR_INDEX_ROUTE, KOSPI200_ROUTE.dataset: KOSPI200_ROUTE}


@dataclass(frozen=True)
class PreparedIndexAppend:
    route: IndexDailyRoute
    finalized_market_date: date
    landing: pd.DataFrame
    existing: pd.DataFrame | None
    merged: pd.DataFrame
    input_sha256: str
    inserted_rows: int
    replaced_rows: int


@dataclass(frozen=True)
class IndexDailyOperationResult:
    dataset: str
    run_id: str
    status: str
    finalized_market_date: str
    output_path: Path
    checkpoint_path: Path
    journal_path: Path
    input_sha256: str
    retained_latest: str
    inserted_rows: int
    replaced_rows: int
    total_rows: int


@dataclass(frozen=True)
class IndexDailyLaneResult:
    run_id: str
    status: str
    finalized_market_date: str
    checkpoint_path: Path
    journal_path: Path
    datasets: tuple[IndexDailyOperationResult, ...]


def _parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise IndexDailyOperationError("finalized_market_date must use YYYY-MM-DD") from error


def _read_landing(landing: pd.DataFrame | Path, columns: tuple[str, ...]) -> pd.DataFrame:
    if isinstance(landing, pd.DataFrame):
        frame = landing.copy(deep=True)
    elif isinstance(landing, Path):
        if landing.is_dir():
            paths = sorted(landing.rglob("*.parquet"))
            if not paths:
                raise IndexDailyOperationError(f"Landing artifact contains no Parquet: {landing}")
            frame = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
        elif landing.is_file():
            frame = pd.read_parquet(landing)
        else:
            raise IndexDailyOperationError(f"Landing artifact not found: {landing}")
    else:
        raise TypeError("landing must be a pandas DataFrame or Path")
    if list(frame.columns) != list(columns):
        raise IndexDailyOperationError("Landing schema differs from the selected contract")
    return frame.copy(deep=True)


def _stable_hash(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update("\x1f".join(map(str, frame.columns)).encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes())
    return digest.hexdigest()


def _keys(frame: pd.DataFrame, key: tuple[str, ...]) -> set[tuple[object, ...]]:
    return set(map(tuple, frame.loc[:, list(key)].itertuples(index=False, name=None)))


def _same_rows(left: pd.Series, right: pd.Series) -> bool:
    return left.to_dict() == right.to_dict()


def _validate_route_landing(route: IndexDailyRoute, frame: pd.DataFrame, target: date) -> None:
    route.validator(frame)
    target_text = target.isoformat()
    if frame["date"].astype(str).nunique() != 1 or frame["date"].astype(str).iloc[0] != target_text:
        raise IndexDailyOperationError(
            "Landing must contain exactly the explicitly finalized market date"
        )
    if route.dataset == "kr_index_daily":
        keys = {(target_text, symbol) for symbol in KR_INDEX_MARKETS}
        if _keys(frame, route.key) != keys:
            raise IndexDailyOperationError(
                "kr_index_daily Landing must contain every registered KR index"
            )
    elif len(frame) != 1:
        raise IndexDailyOperationError("KOSPI200 Landing must contain exactly one finalized row")


def prepare_daily_append(
    dataset: str,
    landing: pd.DataFrame | Path,
    *,
    finalized_market_date: str | date | datetime,
    production_root: Path,
    finality_confirmed: bool,
) -> PreparedIndexAppend:
    """Prepare a one-date append without writing Landing, production, or state."""
    if dataset not in ROUTES:
        raise IndexDailyOperationError(f"unsupported dataset route: {dataset}")
    if not finality_confirmed:
        raise IndexDailyOperationError("finalized market date requires explicit finality confirmation")
    target = _parse_date(finalized_market_date)
    route = ROUTES[dataset]
    incoming = _read_landing(landing, route.contract.column_names)
    _validate_route_landing(route, incoming, target)

    existing: pd.DataFrame | None
    if production_root.exists():
        existing = route.reader(production_root)
    else:
        existing = None
    merged = incoming.copy(deep=True)
    inserted = len(incoming)
    replaced = 0
    if existing is not None and not existing.empty:
        existing = existing.copy(deep=True)
        latest = _parse_date(str(existing["date"].astype(str).max()))
        if target < latest:
            raise IndexDailyOperationError(
                "historical target precedes retained latest; broad backfill is not permitted"
            )
        existing_keys = _keys(existing, route.key)
        incoming_keys = _keys(incoming, route.key)
        overlap = existing_keys & incoming_keys
        existing_index = {key: index for key, index in _keys_with_index(existing, route.key)}
        incoming_index = {key: index for key, index in _keys_with_index(incoming, route.key)}
        for key in overlap:
            existing_row = existing.iloc[existing_index[key]]
            incoming_row = incoming.iloc[incoming_index[key]]
            if not _same_rows(existing_row, incoming_row):
                raise IndexDailyOperationError(f"finalized key conflicts with retained row: {key}")
        if target == latest and incoming_keys - existing_keys:
            raise IndexDailyOperationError("target date is retained but input keys are incomplete/conflicting")
        new_rows = incoming.loc[
            ~incoming.apply(lambda row: tuple(row[column] for column in route.key) in existing_keys, axis=1)
        ].copy()
        inserted = len(new_rows)
        # Overlapping finalized keys are verified, never silently overwritten.
        replaced = 0
        merged = pd.concat([existing, new_rows], ignore_index=True)
    merged = merged.sort_values(list(route.contract.sort_key), kind="stable").reset_index(drop=True)
    route.validator(merged)
    return PreparedIndexAppend(
        route=route,
        finalized_market_date=target,
        landing=incoming,
        existing=existing,
        merged=merged,
        input_sha256=_stable_hash(incoming),
        inserted_rows=inserted,
        replaced_rows=replaced,
    )


def _keys_with_index(frame: pd.DataFrame, key: tuple[str, ...]) -> list[tuple[tuple[object, ...], int]]:
    return [(tuple(row[column] for column in key), index) for index, row in frame.iterrows()]


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise IndexDailyOperationError(f"temporary state path already exists: {temporary}")
    try:
        body = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise IndexDailyOperationError(f"temporary state path already exists: {temporary}")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _restore_transaction(payload: dict[str, object]) -> None:
    """Restore the pre-transaction directory and checkpoint bytes."""
    production = Path(str(payload["production_root"]))
    previous = Path(str(payload["previous_root"]))
    checkpoint = Path(str(payload["checkpoint_path"]))
    # A crash can occur after the old directory is moved but before the phase
    # record is durable.  The presence of ``previous`` is therefore itself a
    # promotion-in-progress marker.
    promoted = bool(payload.get("production_promoted", False)) or previous.exists()
    if promoted:
        _remove_tree(production)
        if previous.exists():
            previous.replace(production)
    prior = payload.get("prior_checkpoint_b64")
    if prior is None:
        checkpoint.unlink(missing_ok=True)
    else:
        _atomic_bytes(checkpoint, base64.b64decode(str(prior).encode("ascii")))
    stage = Path(str(payload["stage_root"]))
    transaction = Path(str(payload["transaction_root"]))
    _remove_tree(stage)
    _remove_tree(transaction)


def _validate_transaction_payload(journal_path: Path, payload: dict[str, object]) -> None:
    """Validate journal topology before any recovery mutation."""
    journal = journal_path.resolve()
    dataset = payload.get("dataset")
    run_id = payload.get("run_id")
    if dataset not in ROUTES or not isinstance(run_id, str):
        raise IndexDailyOperationError("transaction journal identity is invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise IndexDailyOperationError("transaction journal run_id is invalid")
    if journal.parent.name != "journal" or journal.name != f"{dataset}--{run_id}.json":
        raise IndexDailyOperationError("transaction journal filename identity differs")

    def journal_path_value(key: str) -> Path:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise IndexDailyOperationError(f"transaction journal path missing: {key}")
        return Path(value).resolve()

    checkpoint = journal_path_value("checkpoint_path")
    expected_checkpoint = (journal.parent.parent / f"{dataset}.json").resolve()
    if checkpoint != expected_checkpoint:
        raise IndexDailyOperationError("transaction checkpoint topology differs")
    production = journal_path_value("production_root")
    if production.name != dataset:
        raise IndexDailyOperationError("transaction production identity differs")
    transaction = journal_path_value("transaction_root")
    expected_transaction = (production.parent / f".{dataset}.transactions" / run_id).resolve()
    if transaction != expected_transaction:
        raise IndexDailyOperationError("transaction root topology differs")
    stage = journal_path_value("stage_root")
    previous = journal_path_value("previous_root")
    if stage != (transaction / "stage").resolve() or previous != (transaction / "previous").resolve():
        raise IndexDailyOperationError("transaction child topology differs")


def _cleanup_committed_transaction(payload: dict[str, object]) -> None:
    """Remove only committed transaction remnants; never production/checkpoint."""
    _remove_tree(Path(str(payload["transaction_root"])))


def recover_interrupted_transaction(journal_path: Path) -> str:
    """Recover a non-terminal transaction recorded in the durable phase journal."""
    try:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise IndexDailyOperationError("transaction journal is unreadable") from error
    if not isinstance(payload, dict):
        raise IndexDailyOperationError("transaction journal is invalid")
    _validate_transaction_payload(journal_path, payload)
    status = str(payload.get("status", ""))
    if status == "SUCCEEDED":
        _cleanup_committed_transaction(payload)
        return status
    if status in {"FAILED", "RECOVERED"}:
        return status
    if status not in {"PREPARED", "STAGED", "PROMOTED", "CHECKPOINTED"}:
        raise IndexDailyOperationError(f"unsupported transaction phase: {status}")
    _restore_transaction(payload)
    recovered = {**payload, "status": "RECOVERED", "recovered_from": status}
    _atomic_json(journal_path, recovered)
    return "RECOVERED"


def _read_checkpoint(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise IndexDailyOperationError("checkpoint is unreadable") from error
    if not isinstance(payload, dict) or payload.get("status") != "SUCCEEDED":
        raise IndexDailyOperationError("checkpoint is not a terminal succeeded state")
    return payload


def run_offline_daily_append(
    dataset: str,
    landing: pd.DataFrame | Path,
    *,
    finalized_market_date: str | date | datetime,
    production_root: Path,
    state_root: Path,
    run_id: str,
    finality_confirmed: bool,
    writer: Callable[[pd.DataFrame, Path], None] | None = None,
    checkpoint_writer: Callable[[Path, object], None] | None = None,
) -> IndexDailyOperationResult:
    """Validate and atomically append one explicit final date from Landing.

    The merged dataset is written and read back under a sibling transaction
    directory.  Promotion is a directory rename, and the previous production
    directory remains available until the checkpoint and terminal journal phase
    have both succeeded.  This makes checkpoint/finalize failure recoverable.
    """
    if not run_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise IndexDailyOperationError("run_id is invalid")
    if not isinstance(landing, Path) or not landing.is_file() or landing.suffix.lower() != ".parquet":
        raise IndexDailyOperationError(
            "run boundary requires an existing Parquet Landing artifact Path"
        )
    prepared = prepare_daily_append(
        dataset,
        landing,
        finalized_market_date=finalized_market_date,
        production_root=production_root,
        finality_confirmed=finality_confirmed,
    )
    checkpoint_path = state_root.resolve() / f"{dataset}.json"
    journal_path = state_root.resolve() / "journal" / f"{dataset}--{run_id}.json"
    checkpoint = _read_checkpoint(checkpoint_path)
    target_text = prepared.finalized_market_date.isoformat()
    if checkpoint is not None:
        if (
            checkpoint.get("finalized_market_date") == target_text
            and checkpoint.get("input_sha256") == prepared.input_sha256
            and prepared.existing is not None
            and prepared.inserted_rows == 0
        ):
            return IndexDailyOperationResult(
                dataset=dataset, run_id=run_id, status="NOOP_IDEMPOTENT",
                finalized_market_date=target_text, output_path=production_root,
                checkpoint_path=checkpoint_path, journal_path=journal_path,
                input_sha256=prepared.input_sha256, retained_latest=str(checkpoint["retained_latest"]),
                inserted_rows=0, replaced_rows=0, total_rows=int(checkpoint["total_rows"]),
            )
        if str(checkpoint.get("finalized_market_date", "")) > target_text:
            raise IndexDailyOperationError("checkpoint is ahead of requested target")
        if checkpoint.get("finalized_market_date") == target_text:
            raise IndexDailyOperationError("checkpoint target conflicts with Landing input")

    production_root = production_root.resolve()
    transaction_root = production_root.parent / f".{dataset}.transactions" / run_id
    stage_root = transaction_root / "stage"
    previous_root = transaction_root / "previous"
    if transaction_root.exists():
        raise IndexDailyOperationError("transaction directory already exists; recover it first")
    prior_checkpoint = checkpoint_path.read_bytes() if checkpoint_path.exists() else None
    journal_payload: dict[str, object] = {
        "version": 2,
        "dataset": dataset,
        "run_id": run_id,
        "status": "PREPARED",
        "finalized_market_date": target_text,
        "input_sha256": prepared.input_sha256,
        "inserted_rows": prepared.inserted_rows,
        "replaced_rows": prepared.replaced_rows,
        "production_root": str(production_root),
        "transaction_root": str(transaction_root),
        "stage_root": str(stage_root),
        "previous_root": str(previous_root),
        "checkpoint_path": str(checkpoint_path),
        "prior_checkpoint_b64": (
            base64.b64encode(prior_checkpoint).decode("ascii") if prior_checkpoint is not None else None
        ),
        "production_promoted": False,
    }
    _atomic_json(journal_path, journal_payload)
    try:
        stage_root.parent.mkdir(parents=True, exist_ok=True)
        (writer or prepared.route.writer)(prepared.merged, stage_root)
        staged = prepared.route.reader(stage_root)
        prepared.route.validator(staged)
        if not staged.equals(prepared.merged):
            raise IndexDailyOperationError("staged dataset differs after read-back verification")
        journal_payload = {**journal_payload, "status": "STAGED"}
        _atomic_json(journal_path, journal_payload)

        if production_root.exists():
            production_root.replace(previous_root)
        stage_root.replace(production_root)
        journal_payload = {**journal_payload, "status": "PROMOTED", "production_promoted": True}
        _atomic_json(journal_path, journal_payload)

        retained_latest = str(prepared.merged["date"].astype(str).max())
        payload = {
            **journal_payload,
            "status": "CHECKPOINTED",
            "finalized_market_date": target_text, "retained_latest": retained_latest,
            "input_sha256": prepared.input_sha256, "inserted_rows": prepared.inserted_rows,
            "replaced_rows": prepared.replaced_rows, "total_rows": len(prepared.merged),
        }
        checkpoint_payload = {**payload, "status": "SUCCEEDED"}
        (checkpoint_writer or _atomic_json)(checkpoint_path, checkpoint_payload)
        _atomic_json(journal_path, payload)
        succeeded = {**payload, "status": "SUCCEEDED", "journal": True}
        _atomic_json(journal_path, succeeded)
        _remove_tree(transaction_root)
    except Exception as error:
        rollback_error: Exception | None = None
        try:
            rollback_payload = {**journal_payload, "production_promoted": journal_payload.get("production_promoted", False)}
            _restore_transaction(rollback_payload)
        except Exception as recovery_error:  # pragma: no cover - defensive recovery report
            rollback_error = recovery_error
        failed = {
            **journal_payload,
            "status": "FAILED",
            "error_type": type(error).__name__,
            "error": str(error),
            "rollback_error": str(rollback_error) if rollback_error else None,
        }
        _atomic_json(journal_path, failed)
        raise
    return IndexDailyOperationResult(
        dataset=dataset, run_id=run_id, status="SUCCEEDED", finalized_market_date=target_text,
        output_path=production_root, checkpoint_path=checkpoint_path, journal_path=journal_path,
        input_sha256=prepared.input_sha256, retained_latest=retained_latest,
        inserted_rows=prepared.inserted_rows, replaced_rows=prepared.replaced_rows,
        total_rows=len(prepared.merged),
    )


def run_atomic_lane_append(
    *, kr_index_landing: Path, kospi200_landing: Path,
    finalized_market_date: str | date | datetime, normalized_root: Path,
    state_root: Path, run_id: str, finality_confirmed: bool,
    checkpoint_writer: Callable[[Path, object], None] | None = None,
) -> IndexDailyLaneResult:
    """Promote the two KR index datasets as one recoverable daily unit."""
    if not run_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise IndexDailyOperationError("run_id is invalid")
    target = _parse_date(finalized_market_date)
    inputs = {
        "kr_index_daily": kr_index_landing,
        "kr_kospi200_index_daily": kospi200_landing,
    }
    prepared: dict[str, PreparedIndexAppend] = {}
    for dataset, landing in inputs.items():
        if not landing.is_file() or landing.suffix.lower() != ".parquet":
            raise IndexDailyOperationError("lane boundary requires two Parquet Landing artifacts")
        prepared[dataset] = prepare_daily_append(
            dataset, landing, finalized_market_date=target,
            production_root=normalized_root / dataset,
            finality_confirmed=finality_confirmed,
        )
    inserted = {name: item.inserted_rows for name, item in prepared.items()}
    if set(inserted.values()) == {0}:
        checkpoint = state_root / "kr_index_daily_lane.json"
        return IndexDailyLaneResult(
            run_id, "NOOP_IDEMPOTENT", target.isoformat(), checkpoint,
            state_root / "journal" / f"kr_index_daily_lane--{run_id}.json", (),
        )
    if 0 in inserted.values():
        raise IndexDailyOperationError("split retained state: both index datasets must advance together")

    transaction = normalized_root / ".kr_index_daily_lane.transactions" / run_id
    journal = state_root / "journal" / f"kr_index_daily_lane--{run_id}.json"
    checkpoint = state_root / "kr_index_daily_lane.json"
    if transaction.exists():
        raise IndexDailyOperationError("lane transaction directory already exists")
    prior_checkpoints = {
        path: path.read_bytes() if path.exists() else None
        for path in (checkpoint,) + tuple(state_root / f"{name}.json" for name in inputs)
    }
    payload: dict[str, object] = {
        "version": 1, "lane": "KR_INDEX_DAILY", "run_id": run_id,
        "status": "PREPARED", "finalized_market_date": target.isoformat(),
        "datasets": sorted(inputs), "transaction_root": str(transaction.resolve()),
    }
    _atomic_json(journal, payload)
    promoted: list[str] = []
    try:
        for name, item in prepared.items():
            stage = transaction / name / "stage"
            item.route.writer(item.merged, stage)
            restored = item.route.reader(stage)
            item.route.validator(restored)
            if not restored.equals(item.merged):
                raise IndexDailyOperationError(f"{name} staged read-back differs")
        _atomic_json(journal, {**payload, "status": "STAGED"})
        for name in inputs:
            production = normalized_root / name
            previous = transaction / name / "previous"
            if production.exists():
                production.replace(previous)
            (transaction / name / "stage").replace(production)
            promoted.append(name)
        payload = {**payload, "status": "PROMOTED", "promoted": promoted}
        _atomic_json(journal, payload)
        results: list[IndexDailyOperationResult] = []
        lane_datasets: dict[str, object] = {}
        for name, item in prepared.items():
            latest = str(item.merged["date"].astype(str).max())
            dataset_payload = {
                "version": 3, "dataset": name, "lane": "KR_INDEX_DAILY",
                "run_id": run_id, "status": "SUCCEEDED",
                "finalized_market_date": target.isoformat(), "retained_latest": latest,
                "input_sha256": item.input_sha256, "inserted_rows": item.inserted_rows,
                "replaced_rows": item.replaced_rows, "total_rows": len(item.merged),
            }
            (checkpoint_writer or _atomic_json)(state_root / f"{name}.json", dataset_payload)
            lane_datasets[name] = dataset_payload
            results.append(IndexDailyOperationResult(
                name, run_id, "SUCCEEDED", target.isoformat(), normalized_root / name,
                state_root / f"{name}.json", journal, item.input_sha256, latest,
                item.inserted_rows, item.replaced_rows, len(item.merged),
            ))
        lane_payload = {
            **payload, "status": "SUCCEEDED", "datasets": lane_datasets,
            "api_calls": len(KR_INDEX_MARKETS) + 1, "retry_count": 0,
        }
        (checkpoint_writer or _atomic_json)(checkpoint, lane_payload)
        _atomic_json(journal, lane_payload)
        _remove_tree(transaction)
        return IndexDailyLaneResult(
            run_id, "SUCCEEDED", target.isoformat(), checkpoint, journal, tuple(results),
        )
    except Exception as error:
        for name in reversed(tuple(inputs)):
            production = normalized_root / name
            previous = transaction / name / "previous"
            if name in promoted:
                _remove_tree(production)
            if previous.exists():
                previous.replace(production)
        for path, prior in prior_checkpoints.items():
            if prior is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_bytes(path, prior)
        _remove_tree(transaction)
        _atomic_json(journal, {
            **payload, "status": "FAILED", "error_type": type(error).__name__,
        })
        raise


__all__ = [
    "IndexDailyLaneResult", "IndexDailyOperationError", "IndexDailyOperationResult",
    "PreparedIndexAppend", "prepare_daily_append", "recover_interrupted_transaction",
    "read_registered_kr_index_daily", "run_atomic_lane_append",
    "run_offline_daily_append", "validate_registered_kr_index_daily",
    "write_registered_kr_index_daily",
]
