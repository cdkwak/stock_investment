from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

import pandas as pd

from .types import FrozenInputManifest


def inspect_frozen_kospi200(root: Path) -> FrozenInputManifest:
    parquet_files = sorted(root.rglob("*.parquet"))
    files = sorted(root.rglob("data.parquet"))
    if not files:
        raise ValueError("frozen dataset has no parquet partitions")
    if parquet_files != files:
        raise ValueError("frozen dataset contains an unexpected parquet path")
    digest = hashlib.sha256(); rows = total_bytes = 0; starts = []; ends = []
    previous_end: str | None = None
    for path in files:
        relative = path.relative_to(root)
        if len(relative.parts) != 2 or not relative.parts[0].startswith("year="):
            raise ValueError("frozen partition topology differs")
        partition_year = relative.parts[0].removeprefix("year=")
        if len(partition_year) != 4 or not partition_year.isdigit():
            raise ValueError("frozen partition year is invalid")
        body_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(relative.as_posix().encode() + b"\0" + body_digest.encode() + b"\n")
        dates = pd.read_parquet(path, columns=["date"])["date"].astype(str).reset_index(drop=True)
        if dates.empty:
            raise ValueError("frozen partition is empty")
        if dates.duplicated().any() or not dates.is_monotonic_increasing:
            raise ValueError("frozen partition dates must be unique and sorted")
        if not dates.str.startswith(partition_year + "-").all():
            raise ValueError("frozen partition year differs from row dates")
        if previous_end is not None and dates.iloc[0] <= previous_end:
            raise ValueError("frozen partition date ranges overlap or are unsorted")
        previous_end = dates.iloc[-1]
        rows += len(dates); total_bytes += path.stat().st_size
        starts.append(dates.min()); ends.append(dates.max())
    return FrozenInputManifest(
        "kr_kospi200_index_daily", 1, min(starts), max(ends), rows, len(files),
        total_bytes, digest.hexdigest(), "T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION",
    )


def verify_frozen_kospi200(
    root: Path, expected: FrozenInputManifest | Mapping[str, object],
) -> FrozenInputManifest:
    expected_manifest = (
        expected if isinstance(expected, FrozenInputManifest) else FrozenInputManifest(**expected)
    )
    observed = inspect_frozen_kospi200(root)
    if asdict(observed) != asdict(expected_manifest):
        differing = [
            key for key, value in asdict(expected_manifest).items()
            if asdict(observed)[key] != value
        ]
        raise ValueError(f"frozen manifest differs: {differing}")
    return observed


__all__ = ["inspect_frozen_kospi200", "verify_frozen_kospi200"]
