"""Metadata-only storage inventory. Never reads retained data payloads."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


POLICY = {
    "data/landing": ("KEEP", "immutable source evidence"),
    "data/staging": ("REVIEW_DELETE", "may contain recoverable transaction evidence"),
    "artifacts": ("KEEP", "bounded generated review/results referenced by current work"),
    ".worktrees": ("REVIEW_DELETE", "Git worktree liveness must be resolved through Git"),
    ".venv": ("KEEP", "active reproducible environment"),
    ".git": ("KEEP", "repository authority"),
}


def inspect(root: Path, relative: str, now: float) -> dict:
    path = root / relative
    files = total = 0; oldest = newest = None; denied = 0
    buckets = {"lt_1d": 0, "1_to_7d": 0, "8_to_30d": 0, "gt_30d": 0}
    if path.exists():
        def onerror(_error):
            nonlocal denied
            denied += 1
        for directory, _, names in os.walk(path, onerror=onerror):
            for name in names:
                candidate = Path(directory) / name
                try:
                    stat = candidate.stat()
                except OSError:
                    denied += 1; continue
                files += 1; total += stat.st_size
                oldest = stat.st_mtime if oldest is None else min(oldest, stat.st_mtime)
                newest = stat.st_mtime if newest is None else max(newest, stat.st_mtime)
                age = (now - stat.st_mtime) / 86400
                buckets["lt_1d" if age < 1 else "1_to_7d" if age <= 7 else "8_to_30d" if age <= 30 else "gt_30d"] += 1
    classification, reason = POLICY[relative]
    return {
        "path": relative, "classification": classification, "reason": reason,
        "files": files, "bytes": total, "denied_entries": denied,
        "oldest_utc": None if oldest is None else datetime.fromtimestamp(oldest, timezone.utc).isoformat(),
        "newest_utc": None if newest is None else datetime.fromtimestamp(newest, timezone.utc).isoformat(),
        "age_distribution": buckets,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(); root = args.project_root.resolve()
    output = (args.output or root / "artifacts/agent_runs/storage_inventory_20260818.json").resolve()
    now = datetime.now(timezone.utc).timestamp()
    rows = [inspect(root, relative, now) for relative in POLICY]
    pytest_rows = []
    for path in sorted(root.glob(".pytest*")):
        if path.is_dir():
            POLICY[path.name] = ("SAFE_DELETE", "generated pytest cache")
            pytest_rows.append(inspect(root, path.name, now))
    payload = {"status": "METADATA_ONLY_NOT_DATA_AUTHORITY", "roots": rows + pytest_rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
