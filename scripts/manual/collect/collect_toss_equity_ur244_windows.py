"""Run exactly one due UR-244 Toss 30-minute window, never a backfill."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from stock_data.orchestration.toss_equity_ur244_windows import IDENTITIES, TossQuoteTransportResult, eligible_identities, runner
from stock_data.providers.tossinvest import TossInvestClient


def _runtime_transport(root: Path, symbol: str) -> TossQuoteTransportResult:
    """Runtime-only client construction; neither this function nor callers inspect configuration."""
    client = TossInvestClient.from_environment(project_root=root, connect_timeout=10, read_timeout=10)
    oauth_before, business_before = client.token_request_count, client.market_request_count
    response = client.get_market_data("/api/v1/prices", params={"symbols": symbol})
    return TossQuoteTransportResult(
        payload=response.payload,
        oauth_calls=client.token_request_count - oauth_before,
        business_calls=client.market_request_count - business_before,
    )


def run(root: Path, *, now: datetime | None = None) -> dict[str, object]:
    root, now = Path(root), now or datetime.now(timezone.utc)
    try:
        eligible = eligible_identities(root, now=now)
    except (OSError, RuntimeError, ValueError):
        return {"window_id": now.astimezone().isoformat(), "statuses": {identity: "PREFLIGHT_INVALID_API_ZERO" for identity in IDENTITIES}, "business_api_calls": 0}
    if not eligible:
        result = runner(root).run(now=now)
        return {"window_id": result.window_id, "statuses": dict(result.statuses), "business_api_calls": result.business_api_calls}
    result = runner(root).run(now=now, transport_factories={symbol: (lambda code=symbol: _runtime_transport(root, code)) for symbol in eligible})
    return {"window_id": result.window_id, "statuses": dict(result.statuses), "business_api_calls": result.business_api_calls}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-ur244-window", action="store_true")
    args = parser.parse_args()
    if not args.confirm_ur244_window:
        parser.error("--confirm-ur244-window is required")
    print(run(args.project_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
