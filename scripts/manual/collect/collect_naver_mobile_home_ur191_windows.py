"""Run one manifest-approved UR-191 Naver mobile-home window after 2026-08-24 09:30 KST."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import requests

from stock_data.orchestration.naver_mobile_home_ur191_windows import collector, eligible_boundary


URL = "https://m.stock.naver.com/"


def run(root: Path, *, now: datetime | None = None, get=requests.get) -> dict[str, object]:
    root = Path(root); now = now or datetime.now(timezone.utc)
    try: boundary = eligible_boundary(root, now=now)
    except (OSError, RuntimeError, ValueError):
        return {"selected_boundary": None, "attempted_at_utc": now.astimezone(timezone.utc).isoformat(), "status": "PREFLIGHT_INVALID_API_ZERO", "raw_gets": 0, "replay_api_calls": 0}
    if boundary is None:
        return {"selected_boundary": None, "attempted_at_utc": now.astimezone(timezone.utc).isoformat(), "status": "WINDOW_NOT_MANIFESTED", "raw_gets": 0, "replay_api_calls": 0}
    result = collector(root).run(
        now=now, response_factory=lambda: get(URL, timeout=10, allow_redirects=False),
        allowed_window_ids=(boundary,),
    )
    return {
        "status": result.status, "window_id": result.window_id, "selected_boundary": boundary, "attempted_at_utc": now.astimezone(timezone.utc).isoformat(), "raw_gets": result.raw_gets,
        "accepted_cids": result.accepted_cids, "rejected": result.rejected,
        "replay_api_calls": result.replay_api_calls,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-ur191-window", action="store_true")
    args = parser.parse_args()
    if not args.confirm_ur191_window:
        parser.error("--confirm-ur191-window is required")
    print(run(args.project_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
