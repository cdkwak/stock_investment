from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

from stock_data.orchestration.kr_index_daily_live import capture_one_finalized_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Landing-only KRX index daily capture")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--market-date", required=True)
    parser.add_argument("--finalized-at", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--confirm-finality", action="store_true")
    args = parser.parse_args()
    result = capture_one_finalized_date(
        args.market_date,
        finalized_at=datetime.fromisoformat(args.finalized_at),
        finality_confirmed=args.confirm_finality,
        run_id=args.run_id,
        landing_root=args.project_root / "data/landing/pykrx/index_daily",
        state_root=args.project_root / "data/state",
    )
    print(json.dumps({**asdict(result), **{key: str(value) for key, value in asdict(result).items() if isinstance(value, Path)}}, sort_keys=True))


if __name__ == "__main__":
    main()
