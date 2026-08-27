"""Run only an eligible UR-214 USD/KRW 19:00 KST window."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from stock_data.orchestration.naver_mobile_home_ur214_window import STATE_PATH, collector, selected_boundary


URL = "https://m.stock.naver.com/"


def _api_zero(*, boundary: str | None, now: datetime) -> dict[str, object]:
    return {
        "selected_boundary": boundary,
        "attempted_at_utc": now.astimezone(timezone.utc).isoformat(),
        "status": "PREFLIGHT_API_ZERO",
        "raw_gets": 0,
    }


def run(root: Path, *, now: datetime | None = None, get=requests.get) -> dict[str, object]:
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    try:
        boundary = selected_boundary(root, now=now)
    except (OSError, RuntimeError, ValueError):
        return _api_zero(boundary=None, now=now)
    if boundary is None:
        return _api_zero(boundary=None, now=now)
    path = root / STATE_PATH
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            current = state["windows"].get(boundary)
            valid = (
                isinstance(state, dict)
                and state.get("schema_version") == 1
                and state.get("operation_id") == "UR-214"
                and isinstance(state.get("windows"), dict)
            )
            if not valid or current is not None:
                return _api_zero(boundary=boundary, now=now)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return _api_zero(boundary=boundary, now=now)
    result = collector(root).run(
        now=now,
        response_factory=lambda: get(URL, timeout=10, allow_redirects=False),
        allowed_window_ids=(boundary,),
    )
    return {
        "selected_boundary": boundary,
        "attempted_at_utc": now.astimezone(timezone.utc).isoformat(),
        "status": result.status,
        "raw_gets": result.raw_gets,
        "replay_api_calls": result.replay_api_calls,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-ur214-window", action="store_true")
    args = parser.parse_args()
    if not args.confirm_ur214_window:
        parser.error("--confirm-ur214-window is required")
    print(run(args.project_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
