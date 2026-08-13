from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from stock_data.pipelines.kbsec_daily_snapshot import adopt_token_failure, collect_daily_snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--adopt-token-failure-run", type=Path)
    parser.add_argument("--confirm-live-daily", action="store_true")
    parser.add_argument("--confirm-access-restored", action="store_true")
    args = parser.parse_args()
    if args.adopt_token_failure_run:
        result = adopt_token_failure(args.project_root, args.adopt_token_failure_run)
    elif args.confirm_live_daily:
        load_dotenv(args.project_root / ".env", override=False)
        required = ("KBSEC_BASE_URL", "KBSEC_APP_KEY", "KBSEC_APP_SECRET")
        if not all(os.getenv(name, "").strip() for name in required):
            raise SystemExit("KBSEC configuration is incomplete")
        result = collect_daily_snapshot(
            args.project_root,
            known_secrets=tuple(os.getenv(name, "") for name in required),
            confirm_access_restored=args.confirm_access_restored,
        )
    else:
        result = {"status": "NOT_EXECUTED_CONFIRMATION_REQUIRED", "network_calls": 0}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
