"""Execute the one reviewed Toss KOSPI display-only current-observation pilot."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from stock_data.orchestration.toss_market_current_live import execute_toss_kospi_current_pilot
from stock_data.providers.tossinvest import TossInvestClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--expected-market-date", required=True)
    parser.add_argument("--confirm-live-kospi", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_kospi:
        parser.error("--confirm-live-kospi is required for the reviewed one-shot route")
    client = TossInvestClient.from_environment(
        project_root=args.project_root, connect_timeout=10, read_timeout=10,
    )
    try:
        result = execute_toss_kospi_current_pilot(
            args.project_root,
            expected_market_date=args.expected_market_date,
            client=client,
        )
    except Exception as error:
        # Never print provider/authentication detail; the atomic state contains
        # only the sanitized failure class and bounded counts.
        print({"status": "FAILED", "failure_type": type(error).__name__})
        return 1
    print(asdict(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
