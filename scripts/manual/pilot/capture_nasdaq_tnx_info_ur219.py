"""Capture UR-219's one allowed Nasdaq-hosted TNX information response."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import requests

from stock_data.orchestration.nasdaq_tnx_info_ur219 import PUBLIC_HEADERS, URL, capture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-ur219-tnx-info", action="store_true")
    args = parser.parse_args()
    if not args.confirm_ur219_tnx_info:
        parser.error("--confirm-ur219-tnx-info is required")
    result = capture(
        args.project_root,
        now=datetime.now(timezone.utc),
        response_factory=lambda: requests.get(URL, headers=PUBLIC_HEADERS, timeout=10, allow_redirects=False),
    )
    print({"status": result.status, "raw_gets": result.raw_gets, "landing_sha256": result.landing_sha256})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
