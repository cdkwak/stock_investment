"""Execute the sole UR-239 Toss 000660 current-price route."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from stock_data.orchestration.toss_stock_current_live import execute_toss_stock_current_quote
from stock_data.providers.tossinvest import TossInvestClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--expected-market-date", required=True)
    parser.add_argument("--confirm-live-000660", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_000660:
        parser.error("--confirm-live-000660 is required for the one-shot route")
    try:
        result = execute_toss_stock_current_quote(
            args.project_root, expected_market_date=args.expected_market_date,
            client_factory=lambda: TossInvestClient.from_environment(
                project_root=args.project_root, connect_timeout=10, read_timeout=10,
            ), symbol="000660",
            state_path=Path("data/state/toss_stock_current_quote_ur239.json"),
            projection_path=Path("data/state/current_observations/toss_000660_price_snapshot_ur239.json"),
            landing_root=Path("data/landing/tossinvest/stock_current_quote_ur239"),
        )
    except Exception as error:
        print({"status": "FAILED", "failure_type": type(error).__name__})
        return 1
    print(asdict(result)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
