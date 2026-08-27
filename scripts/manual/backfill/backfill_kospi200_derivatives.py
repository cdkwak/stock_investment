from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.pipelines.derivatives_backfill import (  # noqa: E402
    collect_derivative_dates,
    collect_derivative_ranges,
    local_equity_trading_dates,
)
from stock_data.providers.data_go_kr.derivatives import PRODUCT_SPECS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bounded official-data backfill for KOSPI200 derivatives"
    )
    parser.add_argument("--start", default="20200102", help="YYYYMMDD")
    parser.add_argument("--end", help="YYYYMMDD; defaults to latest local trading date")
    parser.add_argument("--kind", choices=("futures", "options", "both"), default="both")
    parser.add_argument("--include-kosdaq150", action="store_true")
    parser.add_argument("--max-calls", type=int, default=20, help="global HTTP-call cap")
    parser.add_argument("--min-interval", type=float, default=1.0)
    parser.add_argument(
        "--request-mode", choices=("range", "daily"), default="range",
        help="range minimizes official API calls; daily is reserved for single-date smoke tests",
    )
    parser.add_argument("--live", action="store_true", help="explicitly permit official API calls")
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"status": "LIVE_DISABLED", "api_calls": 0}))
        return 2
    if args.max_calls < 1:
        parser.error("--max-calls must be positive")

    prefixes = ["kospi200"]
    if args.include_kosdaq150:
        prefixes.append("kosdaq150")
    kinds = ["futures", "options"] if args.kind == "both" else [args.kind]
    specs = [PRODUCT_SPECS[f"{prefix}_{kind}"] for prefix in prefixes for kind in kinds]
    if args.max_calls < len(specs):
        parser.error("--max-calls must allow at least one call per selected Dataset")
    dates = local_equity_trading_dates(ROOT, start=args.start, end=args.end)
    if not dates:
        parser.error("no local trading dates are available for the requested range")

    per_dataset = args.max_calls // len(specs)
    remainder = args.max_calls % len(specs)
    results = []
    for index, spec in enumerate(specs):
        cap = per_dataset + (1 if index < remainder else 0)
        collector = collect_derivative_ranges if args.request_mode == "range" else collect_derivative_dates
        results.append(asdict(collector(
            project_root=ROOT,
            spec=spec,
            dates=dates,
            max_calls=cap,
            min_interval_seconds=args.min_interval,
        )))
    print(json.dumps({
        "status": "COMPLETE",
        "api_calls": sum(result["api_calls"] for result in results),
        "results": results,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
