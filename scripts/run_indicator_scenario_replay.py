from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from market_backtest.indicator_replay import (  # noqa: E402
    IndicatorReplayRequest,
    run_indicator_scenario_replay,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fixed offline RSI14 30/70 development scenario.",
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = run_indicator_scenario_replay(IndicatorReplayRequest(
            project_root=args.project_root,
            output_root=args.output_root,
        ))
    except Exception:
        print("Indicator scenario replay failed safely.", file=sys.stderr)
        return 1
    print(
        f"{receipt.status} schema={receipt.schema} artifacts={len(receipt.artifacts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
