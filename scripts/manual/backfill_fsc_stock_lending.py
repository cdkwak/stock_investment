from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from stock_data.pipelines.stock_lending_backfill import (  # noqa: E402
    STOCK_LENDING_SPECS,
    collect_stock_lending_history,
    stock_lending_run_lock,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="FSC stock-lending historical backfill")
    parser.add_argument(
        "--dataset", choices=("all", "detail", "market", "participant"), default="all"
    )
    parser.add_argument("--start-date", default="20210401")
    parser.add_argument("--end-date")
    parser.add_argument("--max-calls", type=int, default=1000)
    parser.add_argument("--min-interval", type=float, default=0.5)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    keys = ("market", "participant", "detail") if args.dataset == "all" else (args.dataset,)
    exit_code = 0
    with stock_lending_run_lock(ROOT):
        for key in keys:
            result = collect_stock_lending_history(
                project_root=ROOT,
                spec=STOCK_LENDING_SPECS[key],
                start_date=args.start_date,
                end_date=args.end_date,
                max_calls=args.max_calls,
                min_interval_seconds=args.min_interval,
                resume=not args.no_resume,
            )
            print(json.dumps(asdict(result), ensure_ascii=False))
            if result.status not in {"COMPLETE", "VALID_EMPTY"}:
                exit_code = 2
                break
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
