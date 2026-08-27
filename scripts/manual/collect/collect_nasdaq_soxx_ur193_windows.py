"""Run exactly one due, manifest-approved UR-193 Nasdaq SOXX observation window."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import requests
from stock_data.orchestration.nasdaq_soxx_ur193_windows import PUBLIC_HEADERS, URL, WINDOW_IDS, collector

def run(root: Path, *, now: datetime | None = None, response_factory=None) -> dict[str, object]:
    actual_now = now or datetime.now(timezone.utc)
    factory = response_factory or (lambda: requests.get(URL, headers=PUBLIC_HEADERS, timeout=10, allow_redirects=False))
    result = collector(root).run(now=actual_now, response_factory=factory, allowed_window_ids=WINDOW_IDS)
    return {"status": result.status, "window_id": result.window_id, "raw_gets": result.raw_gets, "landing_sha256": result.landing_sha256, "replay_api_calls": result.replay_api_calls}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--project-root", type=Path, required=True); parser.add_argument("--confirm-ur193-window", action="store_true")
    args = parser.parse_args()
    if not args.confirm_ur193_window: parser.error("--confirm-ur193-window is required")
    print(run(args.project_root)); return 0
if __name__ == "__main__": raise SystemExit(main())
