"""Run the provider-free watchlist condition scenario study."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_data.research.condition_backtest import (  # noqa: E402
    DEFAULT_MIN_SCORE_EVENTS,
    run_condition_backtest,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline condition-event and composite oversold-score backtest."
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument(
        "--output-date",
        help="Optional deterministic output folder date in YYYYMMDD form.",
    )
    parser.add_argument(
        "--min-score-events",
        type=int,
        default=DEFAULT_MIN_SCORE_EVENTS,
        help="Minimum fit-window events for a score-grid candidate (default: 30).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_condition_backtest(
        args.project_root,
        output_date=args.output_date,
        min_score_events=args.min_score_events,
    )
    print(result.summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
