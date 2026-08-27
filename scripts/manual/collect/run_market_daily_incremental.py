"""Explicit exact-date entry point for reviewed market daily wrappers."""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_data.orchestration.market_daily_incremental import (  # noqa: E402
    execute_data_go_kr_daily,
    execute_liquidity_credit_two_pass,
    execute_short_selling_daily,
    plan_data_go_kr_daily,
    plan_liquidity_credit_two_pass,
    plan_short_selling_daily,
    short_selling_raw_call_budget,
)
from stock_data.pipelines.short_selling_backfill import (  # noqa: E402
    AppendOnlyRedactedLedger,
    AuthenticatedPykrxRawClient,
    ConservativeThrottle,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument(
        "--lane", choices=("short", "lending", "liquidity-credit-two-pass"),
        required=True,
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument("--latest-finalized", type=date.fromisoformat, required=True)
    parser.add_argument("--max-api-calls", type=int, required=True)
    parser.add_argument("--max-raw-calls", type=int)
    parser.add_argument("--confirm-reviewed-operation", action="store_true")
    parser.add_argument("--confirm-valid-empty-successor", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_reviewed_operation:
        raise SystemExit("--confirm-reviewed-operation is required")
    if args.max_api_calls < 1:
        raise SystemExit("--max-api-calls must be positive")
    root = args.project_root.resolve()
    common = {
        "project_root": root,
        "dataset": args.dataset,
        "market_date": args.market_date,
        "latest_finalized_market_date": args.latest_finalized,
        "accepted_market_dates": (args.market_date,),
        "operation_reviewed": True,
    }
    if args.lane == "short":
        if args.max_raw_calls is None or args.max_raw_calls < args.max_api_calls:
            raise SystemExit("short lane requires --max-raw-calls >= --max-api-calls")
        plan = plan_short_selling_daily(
            **common,
            valid_empty_successor_reviewed=args.confirm_valid_empty_successor,
        )
        if plan.estimated_api_calls > args.max_api_calls:
            raise SystemExit("exact-date plan exceeds explicit API budget")
        required_raw_calls = short_selling_raw_call_budget(
            args.dataset, plan.estimated_api_calls,
        )
        if required_raw_calls and args.max_raw_calls != required_raw_calls:
            raise SystemExit(
                f"short lane requires exact fresh-session raw budget {required_raw_calls}"
            )

        def client_factory(ledger: AppendOnlyRedactedLedger):
            return AuthenticatedPykrxRawClient(
                project_root=root, ledger=ledger, max_raw_calls=args.max_raw_calls,
            )

        result = execute_short_selling_daily(
            plan, project_root=root, client_factory=client_factory,
            throttle=ConservativeThrottle(min_interval_seconds=8.0, max_jitter_seconds=2.0),
        )
    elif args.lane == "lending":
        plan = plan_data_go_kr_daily(**common, max_api_calls=args.max_api_calls)
        result = execute_data_go_kr_daily(plan, project_root=root)
    else:
        plan = plan_liquidity_credit_two_pass(
            **common, max_api_calls=args.max_api_calls,
        )
        result = execute_liquidity_credit_two_pass(plan, project_root=root)
    payload = asdict(result) if is_dataclass(result) else result
    print(json.dumps({"plan": asdict(plan), "result": payload}, default=str,
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
