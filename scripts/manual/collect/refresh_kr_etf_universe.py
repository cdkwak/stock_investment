from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_data.orchestration.kr_etf_universe_daily import (  # noqa: E402
    run_kr_etf_universe_daily,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-call Landing-first refresh of the current full KRX ETF universe",
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--source-date", type=date.fromisoformat,
        default=datetime.now(ZoneInfo("Asia/Seoul")).date(),
        help="KST observation date (default: today)",
    )
    args = parser.parse_args(argv)
    result = run_kr_etf_universe_daily(
        args.project_root,
        source_date=args.source_date,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
