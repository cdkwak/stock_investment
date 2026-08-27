"""Invoke only an active, unclaimed UR-199 Naver equity window."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
import requests
from stock_data.orchestration.naver_equity_ur199_windows import IDENTITIES, eligible_identities, runner
from stock_data.orchestration.naver_mobile_home_windows import window_id

URLS = {symbol: f"https://m.stock.naver.com/api/stock/{symbol}/basic" for symbol in IDENTITIES}

def run(root: Path, *, now: datetime | None = None, get: Callable[..., object] = requests.get) -> dict[str, object]:
    root = Path(root); now = now or datetime.now(timezone.utc); current = runner(root)
    try: eligible = eligible_identities(root, now=now)
    except (OSError, RuntimeError, ValueError):
        return {"window_id": window_id(now=now), "statuses": {identity: "PREFLIGHT_INVALID_API_ZERO" for identity in IDENTITIES}, "api_calls": 0}
    if not eligible:
        result = current.run(now=now)
        return {"window_id": result.window_id, "statuses": dict(result.statuses), "api_calls": result.api_calls}
    factories = {}
    for symbol in IDENTITIES:
        if symbol in eligible:
            factories[symbol] = lambda code=symbol: get(URLS[code], timeout=10, allow_redirects=False)
    result = current.run(now=now, response_factories=factories)
    return {"window_id": result.window_id, "statuses": dict(result.statuses), "api_calls": result.api_calls}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--project-root", type=Path, required=True); parser.add_argument("--confirm-ur199-window", action="store_true"); args = parser.parse_args()
    if not args.confirm_ur199_window: parser.error("--confirm-ur199-window is required")
    print(run(args.project_root)); return 0
if __name__ == "__main__": raise SystemExit(main())
