"""Supported provider-free daily release-readiness smoke entry point."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.release_readiness import run_release_readiness


def _atomic_report(path: Path, report: dict[str, object], project_root: Path) -> None:
    resolved_parent = path.parent.resolve()
    allowed = (project_root / "artifacts/release_readiness").resolve()
    try:
        resolved_parent.relative_to(allowed)
    except ValueError:
        raise ValueError("--output must be under artifacts/release_readiness") from None
    resolved_parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    temporary = resolved_parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the bounded offline/read-only release smoke. Exit 0=PASS, "
            "2=DEGRADED, 1=FAIL. No provider or scheduler mutation is performed."
        )
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument(
        "--output", type=Path,
        help="Optional atomic JSON artifact under artifacts/release_readiness/.",
    )
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    report = run_release_readiness(project_root)
    if args.output is not None:
        output = args.output if args.output.is_absolute() else project_root / args.output
        _atomic_report(output, report, project_root)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return {"PASS": 0, "DEGRADED": 2, "FAIL": 1}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
