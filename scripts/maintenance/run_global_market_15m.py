from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.global_market_15m import (
    LANE_IDS,
    resolve_native_scope,
    run_global_market_15m,
)
from stock_data.orchestration.update_event_log import (
    DEFAULT_RUNTIME_LOG_ROOT,
    LocalUpdateEventLog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh authorized Yahoo native 15m bars.")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--lane", required=True, choices=LANE_IDS)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--session-date",
        type=date.fromisoformat,
        help="Required exact provider-local session date (YYYY-MM-DD).",
    )
    mode.add_argument(
        "--scheduled",
        action="store_true",
        help="Derive one exact completed session after the 30-minute close gate.",
    )
    parser.add_argument("--as-of", help="Timezone-aware ISO timestamp for deterministic QA.")
    args = parser.parse_args()
    clock = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
    try:
        scope = resolve_native_scope(
            clock,
            args.lane,
            required_session_date=args.session_date,
            scheduled=args.scheduled,
        )
        report = run_global_market_15m(
            args.project_root,
            lane_id=scope.lane_id,
            window_start=scope.window_start,
            window_end=scope.window_end,
            expected_bar_starts=scope.expected_bar_starts,
            as_of=clock,
            event_log=(
                LocalUpdateEventLog(args.project_root / DEFAULT_RUNTIME_LOG_ROOT)
                if scope.lane_id == "CBOE_VIX"
                else None
            ),
        )
        report["session_date"] = scope.session_date.isoformat()
        report["execution_mode"] = "SCHEDULED" if args.scheduled else "EXACT_DATE"
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__}, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
