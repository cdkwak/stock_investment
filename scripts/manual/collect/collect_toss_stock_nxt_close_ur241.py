"""Execute the sole UR-241 Toss 005930 NXT-session-close route."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from pathlib import Path
from stock_data.orchestration.toss_stock_current_live import execute_toss_stock_current_quote
from stock_data.orchestration.toss_stock_nxt_close_ur241 import recover_retained_inferred_close, validate_nxt_session_close
from stock_data.providers.tossinvest import TossInvestClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--expected-market-date", required=True)
    parser.add_argument("--confirm-live-005930-nxt-close", action="store_true")
    parser.add_argument("--recover-retained-api-zero", action="store_true")
    args = parser.parse_args()
    if args.recover_retained_api_zero:
        print(recover_retained_inferred_close(args.project_root, expected_date=args.expected_market_date)); return 0
    if not args.confirm_live_005930_nxt_close:
        parser.error("--confirm-live-005930-nxt-close is required")
    try:
        result = execute_toss_stock_current_quote(
            args.project_root, expected_market_date=args.expected_market_date,
            client_factory=lambda: TossInvestClient.from_environment(project_root=args.project_root, connect_timeout=10, read_timeout=10),
            symbol="005930", state_path=Path("data/state/toss_stock_nxt_close_ur241.json"),
            projection_path=Path("data/state/current_observations/toss_005930_nxt_close_ur241.json"),
            landing_root=Path("data/landing/tossinvest/stock_nxt_close_ur241"),
            acceptance_validator=validate_nxt_session_close,
            route_suffix=":TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
        )
    except Exception as error:
        print({"status": "FAILED", "failure_type": type(error).__name__}); return 1
    print(asdict(result)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
