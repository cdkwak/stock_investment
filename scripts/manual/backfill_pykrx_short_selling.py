"""Explicit, bounded authenticated KRX short-selling collector.

This is deliberately a manual entry point. Importing it performs no I/O and it
has no unbounded/default live mode.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

from stock_data.pipelines.short_selling_backfill import (
    AppendOnlyRedactedLedger,
    AuthenticatedPykrxRawClient,
    ConservativeThrottle,
    calculate_backfill_estimate,
    load_canonical_trading_dates,
    run_short_selling_batch,
)


def _day(value: str) -> date:
    return date.fromisoformat(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--dataset", choices=("trading", "balance", "investor"))
    parser.add_argument("--start", type=_day)
    parser.add_argument("--end", type=_day)
    parser.add_argument("--max-business-calls", type=int)
    parser.add_argument("--max-raw-calls", type=int)
    parser.add_argument("--min-interval-seconds", type=float, default=8.0)
    parser.add_argument("--max-jitter-seconds", type=float, default=2.0)
    parser.add_argument("--confirm-live-collection", action="store_true")
    parser.add_argument("--estimate-only", action="store_true")
    parser.add_argument("--pilot-run", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.project_root.resolve()
    canonical = root / "data/normalized/kr_equity_universe_daily"
    if args.estimate_only:
        if args.end is None or args.pilot_run is None:
            raise SystemExit("--estimate-only requires --end and --pilot-run")
        estimate = calculate_backfill_estimate(
            canonical_root=canonical, pilot_run=args.pilot_run.resolve(), through_date=args.end
        )
        print(json.dumps(asdict(estimate), indent=2, sort_keys=True))
        return 0
    required = {
        "--dataset": args.dataset, "--start": args.start, "--end": args.end,
        "--max-business-calls": args.max_business_calls,
        "--max-raw-calls": args.max_raw_calls,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing or not args.confirm_live_collection:
        suffix = ", ".join(missing) if missing else "--confirm-live-collection"
        raise SystemExit(f"bounded live collection is disabled without explicit {suffix}")
    if args.max_business_calls < 1 or args.max_raw_calls < args.max_business_calls:
        raise SystemExit("request budgets are invalid")
    try:
        throttle = ConservativeThrottle(
            min_interval_seconds=args.min_interval_seconds,
            max_jitter_seconds=args.max_jitter_seconds,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    trading_dates = load_canonical_trading_dates(canonical, start=args.start, end=args.end)

    def client_factory(ledger: AppendOnlyRedactedLedger):
        return AuthenticatedPykrxRawClient(
            project_root=root, ledger=ledger, max_raw_calls=args.max_raw_calls
        )

    result = run_short_selling_batch(
        dataset=args.dataset, trading_dates=trading_dates,
        max_business_calls=args.max_business_calls, project_root=root,
        client_factory=client_factory,
        throttle=throttle,
    )
    print(json.dumps(asdict(result), indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
