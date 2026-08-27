from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

import pandas as pd

from stock_data.providers.data_go_kr.client import (
    DataGoKrClient, service_key_from_environment, write_landing_pages_atomic,
)
from stock_data.pipelines.backfill_state import BackfillState
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1


@dataclass(frozen=True)
class CollectionResult:
    dataset: str
    rows: int
    pages: int
    status: str
    minimum_date: str | None
    maximum_date: str | None


def collect_date(
    *, project_root: Path, endpoint: str, contract, normalizer: Callable,
    base_date: str, max_calls: int, resume: bool = True,
    state_path: Path | None = None, normalized_root: Path | None = None,
    landing_path: Path | None = None,
) -> CollectionResult:
    if len(base_date) != 8 or not base_date.isdigit() or max_calls < 1:
        raise ValueError("base_date must be YYYYMMDD and max_calls must be positive")
    state_path = state_path or project_root / "data/state" / f"{contract.name}.json"
    normalized_root = normalized_root or project_root / "data/normalized" / contract.name
    landing_path = landing_path or (
        project_root / "data/landing/data_go_kr" / contract.name / f"{base_date}.json"
    )
    state = BackfillState.load(state_path, contract.name)
    if resume and base_date not in state.pending([base_date]):
        if base_date in state.valid_empty_partitions:
            return CollectionResult(contract.name, 0, 0, "VALID_EMPTY", None, None)
        if not normalized_root.exists():
            raise FileNotFoundError(normalized_root)
        existing = read_dataset(normalized_root, contract, _validator(contract))
        selected = existing[existing["date"] == pd.to_datetime(base_date).strftime("%Y-%m-%d")]
        return CollectionResult(contract.name, len(selected), 0, "COMPLETE", selected.date.min(), selected.date.max())
    try:
        result = DataGoKrClient(endpoint=endpoint, service_key=service_key_from_environment(project_root),
                                max_attempts=1).fetch_all(
            filters={"basDt": base_date}, num_of_rows=9999, max_pages=max_calls,
        )
        _write_immutable_date_landing(result.pages, landing_path)
        if result.total_count == 0:
            state.mark_valid_empty(base_date)
            return CollectionResult(contract.name, 0, len(result.pages), "VALID_EMPTY", None, None)
        frame = normalizer(result.items)
        validate_data_v1(frame, contract, allow_empty=False)
        expected_date = pd.to_datetime(base_date).strftime("%Y-%m-%d")
        if set(frame["date"].astype(str)) != {expected_date}:
            raise ValueError("exact-date response contains another source date")
        if normalized_root.exists():
            existing = read_dataset(normalized_root, contract, _validator(contract))
            frame = pd.concat([existing, frame], ignore_index=True).drop_duplicates(
                list(contract.primary_key), keep="last"
            ).sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        validate_data_v1(frame, contract)
        write_dataset_atomic(frame, normalized_root, contract, _validator(contract))
        restored = read_dataset(normalized_root, contract, _validator(contract))
        state.mark_completed(base_date)
        selected = restored[restored["date"] == pd.to_datetime(base_date).strftime("%Y-%m-%d")]
        return CollectionResult(contract.name, len(selected), len(result.pages), "COMPLETE",
                                selected.date.min(), selected.date.max())
    except Exception as error:
        state.mark_failed(base_date, type(error).__name__)
        raise


def _write_immutable_date_landing(pages, path: Path) -> None:
    """Retain the first exact-date response, including a valid empty response."""
    expected = list(pages)
    if path.exists():
        try:
            retained = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("existing exact-date Landing is unreadable") from error
        if retained != expected:
            raise RuntimeError("existing exact-date Landing differs from provider response")
        return
    write_landing_pages_atomic(tuple(pages), path)


def _validator(contract):
    return lambda frame: validate_data_v1(frame, contract)


def collect_full_history(
    *, project_root: Path, endpoint: str, contract, normalizer: Callable,
    max_calls: int = 1, resume: bool = True,
) -> CollectionResult:
    if max_calls < 1:
        raise ValueError("max_calls must be positive")
    state = BackfillState.load(project_root / "data/state" / f"{contract.name}.json", contract.name)
    partition = "full_history"
    if resume and partition not in state.pending([partition]):
        root = project_root / "data/normalized" / contract.name
        existing = read_dataset(root, contract, _validator(contract))
        return CollectionResult(contract.name, len(existing), 0, "COMPLETE", existing.date.min(), existing.date.max())
    try:
        result = DataGoKrClient(
            endpoint=endpoint, service_key=service_key_from_environment(project_root), max_attempts=1,
        ).fetch_all(filters={"beginBasDt": "19000101"}, num_of_rows=9999, max_pages=max_calls)
        if result.total_count == 0:
            state.mark_valid_empty(partition)
            return CollectionResult(contract.name, 0, len(result.pages), "VALID_EMPTY", None, None)
        frame = normalizer(result.items)
        validate_data_v1(frame, contract, allow_empty=False)
        landing = project_root / "data/landing/data_go_kr" / contract.name / "full_history.json"
        write_landing_pages_atomic(result.pages, landing)
        root = project_root / "data/normalized" / contract.name
        if root.exists():
            existing = read_dataset(root, contract, _validator(contract))
            frame = pd.concat([existing, frame], ignore_index=True).drop_duplicates(
                list(contract.primary_key), keep="last"
            ).sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
            validate_data_v1(frame, contract)
        write_dataset_atomic(frame, root, contract, _validator(contract))
        restored = read_dataset(root, contract, _validator(contract))
        if len(restored) != len(frame):
            raise RuntimeError("stored row count differs after read-back")
        state.mark_completed(partition)
        return CollectionResult(contract.name, len(restored), len(result.pages), "COMPLETE",
                                restored.date.min(), restored.date.max())
    except Exception as error:
        state.mark_failed(partition, type(error).__name__)
        raise
