"""Read-only/pre-transport entrypoint for UR-231's exact current windows."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path: sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.yahoo_native15m_ur231_current import LANES, eligibility, operational_run, selected_boundary


def main() -> int:
    parser = argparse.ArgumentParser(description="UR-231 Yahoo native-15m current-window preflight (no transport).")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--lane", choices=tuple(LANES), required=True)
    parser.add_argument("--as-of", required=True, help="Timezone-aware ISO timestamp.")
    parser.add_argument("--execute", action="store_true", help="Invoke the fixed retry-zero Yahoo transport only after eligibility.")
    args = parser.parse_args(); now = datetime.fromisoformat(args.as_of)
    try:
        if args.execute:
            import requests
            status = operational_run(args.project_root, args.lane, now=now, transport=lambda **kwargs: (lambda response: (response.status_code, bytes(response.content)))(requests.get(**kwargs)))
            result = {"lane_id": args.lane, "status": status, "selected_boundary_kst": selected_boundary(args.lane, now=now), "api_calls": 0 if status != "COMPLETE_ACCEPTED" else len(LANES[args.lane])}
        else:
            result = {"lane_id": args.lane, "status": eligibility(args.project_root, args.lane, now=now), "selected_boundary_kst": selected_boundary(args.lane, now=now), "api_calls": 0}
    except Exception as error:
        result = {"status": "API_ZERO_INVALID_INPUT", "error_type": type(error).__name__, "api_calls": 0}
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
