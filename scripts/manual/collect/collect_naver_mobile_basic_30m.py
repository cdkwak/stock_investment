"""Run one authorized Naver 000660 mobile-basic 30-minute window locally."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import requests

from stock_data.orchestration.naver_mobile_basic_windowed_current import NaverMobileBasicWindowedCollector


URL = "https://m.stock.naver.com/api/stock/000660/basic"


def run(root: Path) -> dict[str, object]:
    """Reusable acquisition-supervisor boundary; it installs no scheduler."""
    result = NaverMobileBasicWindowedCollector(root).run(
        now=datetime.now(timezone.utc),
        response_factory=lambda: requests.get(URL, timeout=10, allow_redirects=False),
    )
    return {"status": result.status, "window_id": result.window_id, "raw_gets": result.raw_gets, "replay_api_calls": result.replay_api_calls}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-live-000660-window", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_000660_window:
        parser.error("--confirm-live-000660-window is required")
    print(run(args.project_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
