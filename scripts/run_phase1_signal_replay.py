from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from market_backtest.phase1_replay import (  # noqa: E402
    EXPECTED_FROZEN_DIGEST,
    Phase1ReplayRequest,
    run_phase1_replay,
)
from runtime_diagnostics import (  # noqa: E402
    RuntimeDiagnosticStore,
    artifact_identity,
    new_session_id,
    safe_record_failure,
)


EXPECTED_DIGEST = EXPECTED_FROZEN_DIGEST


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    session_id = new_session_id()
    run_id = new_session_id()
    try:
        run_phase1_replay(Phase1ReplayRequest(
            project_root=args.project_root,
            output_root=args.output_root,
        ))
    except Exception as error:
        output_root = (
            args.output_root.resolve()
            if args.output_root is not None
            else args.project_root.resolve() / "artifacts/backtest/phase1_signal_replay"
        )
        artifacts = tuple(
            identity
            for name in (
                "bundle.json", "experiments.json", "portfolio_ledger.json",
                "result.json", "signals.csv",
            )
            if (identity := artifact_identity(
                args.project_root, output_root / name
            )) is not None
        )
        safe_record_failure(
            RuntimeDiagnosticStore(
                args.project_root.resolve() / "artifacts/runtime_logs/application"
            ),
            project_root=args.project_root, domain="BACKTEST",
            kind="TERMINAL_FAILURE", session_id=session_id, run_id=run_id,
            code="PHASE1_REPLAY_FAILED", stage="RUNNER", error=error,
            artifacts=artifacts,
        )
        print("Phase-1 replay failed safely.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
