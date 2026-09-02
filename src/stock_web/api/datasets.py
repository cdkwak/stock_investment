"""Read-only access to retained Parquet datasets (hive partitions) with a tiny cache."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

_CACHE: dict[tuple[str, float, str], pd.DataFrame] = {}


def _root_signature(root: Path) -> float:
    """Newest mtime under the dataset root; cheap enough for a few dozen partitions."""
    newest = 0.0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".parquet"):
                newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
    return newest


def load(project_root: Path, relative_root: str, *, columns: list[str] | None = None,
         filter_expr: ds.Expression | None = None) -> pd.DataFrame | None:
    root = project_root / relative_root
    if not root.is_dir():
        return None
    key = (str(root), _root_signature(root), f"{columns}|{filter_expr}")
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        dataset = ds.dataset(str(root), format="parquet", partitioning="hive")
        table = dataset.to_table(columns=columns, filter=filter_expr)
    except Exception:
        return None
    frame = table.to_pandas()
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values("date").reset_index(drop=True)
    _CACHE[key] = frame
    return frame


def field(name: str) -> ds.Expression:
    return ds.field(name)
