from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.account_privacy import backfill_positions_history


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill privacy-minimized daily Toss and KB positions history "
            "from retained sanitized Landing snapshots."
        )
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    changed = backfill_positions_history(args.project_root.resolve())
    print(f"positions history files created or updated: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
