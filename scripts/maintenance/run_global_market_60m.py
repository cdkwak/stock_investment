from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.global_market_60m import (
    run_global_market_60m,
    run_global_market_current_60m,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh reviewed delayed global 60m bars.")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--as-of", help="Timezone-aware ISO timestamp for deterministic QA.")
    parser.add_argument(
        "--current-only", action="store_true",
        help="Write display-only current projections and never historical data.",
    )
    args = parser.parse_args()
    try:
        operation = run_global_market_current_60m if args.current_only else run_global_market_60m
        report = operation(
            args.project_root,
            as_of=datetime.fromisoformat(args.as_of) if args.as_of else None,
        )
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__}, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
