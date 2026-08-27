"""Run only a manifest-approved UR-161 Naver 000660 KST window."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import requests

from stock_data.orchestration.naver_mobile_basic_windowed_current import NaverMobileBasicWindowedCollector
from stock_data.orchestration.naver_remaining_session_windows import ensure_manifest, read_manifest


URL = "https://m.stock.naver.com/api/stock/000660/basic"


def run(root: Path) -> dict[str, object]:
    root = Path(root)
    ensure_manifest(root)
    manifest = read_manifest(root)
    allowed = manifest["allowed_window_ids"]
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise RuntimeError("UR-161 public manifest has invalid allowed windows")
    result = NaverMobileBasicWindowedCollector(root).run(
        now=datetime.now(timezone.utc),
        response_factory=lambda: requests.get(URL, timeout=10, allow_redirects=False),
        allowed_window_ids=allowed,
    )
    return {"status": result.status, "window_id": result.window_id, "raw_gets": result.raw_gets, "replay_api_calls": result.replay_api_calls}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-ur161-window", action="store_true")
    args = parser.parse_args()
    if not args.confirm_ur161_window:
        parser.error("--confirm-ur161-window is required")
    print(run(args.project_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
