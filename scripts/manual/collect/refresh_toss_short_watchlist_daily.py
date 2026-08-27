from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_data.orchestration.toss_short_watchlist_daily import (  # noqa: E402
    refresh_toss_short_watchlist_daily,
)
from stock_data.providers.tossinvest import TossInvestClient, TossInvestError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the selected two-symbol Toss short-selling transaction."
    )
    parser.add_argument("--date", required=True, choices=("2026-08-19",))
    args = parser.parse_args()
    try:
        # Preflight with no client guarantees a completed same-date run returns
        # before credentials are loaded or any network call can occur.
        result = refresh_toss_short_watchlist_daily(
            ROOT, intended_date=args.date, client=None
        )
    except ValueError as error:
        if "client is required" not in str(error):
            raise
        client = TossInvestClient.from_environment(project_root=ROOT)
        try:
            result = refresh_toss_short_watchlist_daily(
                ROOT, intended_date=args.date, client=client
            )
        except TossInvestError as provider_error:
            details = provider_error.details
            print(json.dumps({
                "status": "FAILED_PROVIDER",
                "error_type": type(provider_error).__name__,
                "http_status": details.http_status if details else None,
                "token_calls": client.token_request_count,
                "market_calls": client.market_request_count,
            }, ensure_ascii=False))
            return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
