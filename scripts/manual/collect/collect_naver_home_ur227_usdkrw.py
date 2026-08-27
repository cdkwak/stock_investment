"""Run only an eligible UR-227 USD/KRW 19:45 KST urllib window."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from stock_data.orchestration.naver_mobile_home_ur227_window import STATE_PATH, collector, selected_boundary


URL = "https://m.stock.naver.com/"
HEADERS = {
    "User-Agent": "StockInvestmentRev1/UR-227 public-display pilot",
    "Accept": "text/html,application/xhtml+xml",
}


@dataclass(frozen=True)
class UrllibResponse:
    status_code: int
    content: bytes


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _urllib_get(url: str, *, timeout: int) -> UrllibResponse:
    """One direct public request: fixed headers, no proxy discovery or redirects."""
    request = Request(url, headers=HEADERS, method="GET")
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            return UrllibResponse(status_code=int(response.getcode()), content=response.read())
    except HTTPError as error:
        return UrllibResponse(status_code=int(error.code), content=b"")


def _api_zero(*, boundary: str | None, now: datetime) -> dict[str, object]:
    return {
        "selected_boundary": boundary,
        "attempted_at_utc": now.astimezone(timezone.utc).isoformat(),
        "status": "PREFLIGHT_API_ZERO",
        "raw_gets": 0,
    }


def run(root: Path, *, now: datetime | None = None, get=_urllib_get) -> dict[str, object]:
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
                and state.get("operation_id") == "UR-227"
                and isinstance(state.get("windows"), dict)
            )
            if not valid or current is not None:
                return _api_zero(boundary=boundary, now=now)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return _api_zero(boundary=boundary, now=now)
    result = collector(root).run(
        now=now,
        response_factory=lambda: get(URL, timeout=10),
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
    parser.add_argument("--confirm-ur227-window", action="store_true")
    args = parser.parse_args()
    if not args.confirm_ur227_window:
        parser.error("--confirm-ur227-window is required")
    print(run(args.project_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
