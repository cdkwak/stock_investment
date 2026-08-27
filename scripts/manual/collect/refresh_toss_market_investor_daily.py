from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_data.orchestration.toss_market_investor_daily import (  # noqa: E402
    refresh_toss_market_investor_daily,
)
from stock_data.providers.tossinvest import TossInvestClient, TossInvestError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Finalized KRX market date (YYYY-MM-DD)")
    args = parser.parse_args()
    client = TossInvestClient.from_environment(project_root=ROOT)
    try:
        result = refresh_toss_market_investor_daily(
            ROOT, intended_date=args.date, client=client
        )
    except TossInvestError as error:
        details = error.details
        result = {
            "status": "error",
            "error_type": type(error).__name__,
            "http_status": details.http_status if details else None,
            "token_calls": client.token_request_count,
            "market_calls": client.market_request_count,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
