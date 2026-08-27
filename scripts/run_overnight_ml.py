from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys


# Keep every estimator single-process. The runner itself owns the time budget
# and deterministic walk-forward ordering.
for name in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(name, "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from market_backtest.overnight_ml import (  # noqa: E402
    DEFAULT_OUTPUT_RELATIVE,
    MAX_DURATION_SECONDS,
    OvernightMLRequest,
    read_overnight_ml_status,
    run_overnight_ml,
)
from runtime_diagnostics import (  # noqa: E402
    RuntimeDiagnosticStore,
    artifact_identity,
    new_session_id,
    safe_record_failure,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run or inspect a provider-free, resumable, development-only ML "
            "study over the frozen KOSPI200 input."
        )
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--duration-hours", type=float, default=8.0)
    parser.add_argument("--max-trials", type=int)
    parser.add_argument("--keep-awake", action="store_true")
    parser.add_argument("--status", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_root = (
        args.output_root
        if args.output_root is not None
        else args.project_root / DEFAULT_OUTPUT_RELATIVE
    )
    if args.status:
        try:
            print(json.dumps(
                read_overnight_ml_status(output_root),
                ensure_ascii=False, sort_keys=True, indent=2,
            ))
        except Exception:
            print("Overnight ML state is unavailable.", file=sys.stderr)
            return 1
        return 0
    duration_seconds = int(round(args.duration_hours * 60 * 60))
    if not 1 <= duration_seconds <= MAX_DURATION_SECONDS:
        print("--duration-hours must be greater than 0 and no more than 8.", file=sys.stderr)
        return 2
    session_id = new_session_id()
    run_id = new_session_id()
    try:
        receipt = run_overnight_ml(OvernightMLRequest(
            project_root=args.project_root,
            output_root=args.output_root,
            duration_seconds=duration_seconds,
            max_trials=args.max_trials,
            keep_awake=args.keep_awake,
        ))
    except Exception as error:
        artifacts = tuple(
            identity
            for name in ("config.json", "state.json", "summary.json", "study.sqlite3")
            if (identity := artifact_identity(
                args.project_root, output_root / name,
            )) is not None
        )
        safe_record_failure(
            RuntimeDiagnosticStore(
                args.project_root.resolve() / "artifacts/runtime_logs/application"
            ),
            project_root=args.project_root,
            domain="BACKTEST",
            kind="TERMINAL_FAILURE",
            session_id=session_id,
            run_id=run_id,
            code="OVERNIGHT_ML_FAILED",
            stage="RUNNER",
            error=error,
            artifacts=artifacts,
        )
        print("Overnight ML failed safely.", file=sys.stderr)
        return 1
    print(json.dumps(asdict(receipt), default=str, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
