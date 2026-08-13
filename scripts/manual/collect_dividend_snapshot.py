"""Run a bounded FSC dividend source-snapshot collection batch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from stock_data.providers.data_go_kr.client import service_key_from_environment
from dividend_snapshot_collection import collect_dividend_snapshot


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-date", required=True, help="Explicit YYYYMMDD source snapshot date")
    parser.add_argument("--max-calls", type=int, default=2, choices=(1, 2))
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live:
        print(json.dumps({"status": "NOT_EXECUTED", "reason": "CONFIRM_LIVE_REQUIRED"}))
        return 2
    result = collect_dividend_snapshot(
        project_root=ROOT, snapshot_date=args.snapshot_date,
        service_key=service_key_from_environment(ROOT), max_calls=args.max_calls,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"RUNNING", "COMPLETE", "ALREADY_COMPLETE"} else 3


if __name__ == "__main__":
    sys.exit(main())
