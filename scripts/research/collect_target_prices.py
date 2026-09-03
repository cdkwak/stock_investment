"""Collect dated analyst target-price consensus for the local watchlist."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_data.research.target_prices import (  # noqa: E402
    KOREAN_UNAVAILABLE_MESSAGE,
    append_target_price_vintages_atomic,
    build_request_plan,
    collect_yahoo_rows,
    korean_unavailable_row,
    load_watchlist,
    read_target_price_consensus,
    rows_to_frame,
)


KST = ZoneInfo("Asia/Seoul")


def _date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("run date must be YYYY-MM-DD") from error
    if value != parsed.isoformat():
        raise argparse.ArgumentTypeError("run date must be canonical YYYY-MM-DD")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture Yahoo target-price consensus for U.S. watchlist tickers and "
            "record Korean securities as unavailable under the reviewed source policy."
        )
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--watchlist", type=Path)
    parser.add_argument("--run-date", type=_date)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the exact request plan; make no requests and write no files.",
    )
    return parser


def _completed_symbols(normalized_root: Path, run_date: date) -> set[str]:
    try:
        frame = read_target_price_consensus(normalized_root)
    except FileNotFoundError:
        return set()
    selected = frame.loc[frame["date"].eq(run_date.isoformat()), "symbol"]
    return {str(symbol) for symbol in selected}


def _run_id(clock: datetime) -> str:
    return f"target-prices-{clock.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project_root.resolve()
    watchlist_path = (
        args.watchlist.resolve()
        if args.watchlist is not None
        else project_root / "artifacts/local_user/watchlists.json"
    )
    run_date = args.run_date or datetime.now(KST).date()
    normalized_root = project_root / "data/normalized/research_target_price_consensus"
    completed = _completed_symbols(normalized_root, run_date)
    securities = load_watchlist(watchlist_path)
    requests_ = build_request_plan(securities, completed=completed)
    unavailable = [
        {
            "market": security.market,
            "symbol": security.symbol,
            "status": KOREAN_UNAVAILABLE_MESSAGE,
        }
        for security in securities
        if security.region == "KR" and security.symbol not in completed
    ]
    plan = {
        "schema_version": 1,
        "dataset": "research_target_price_consensus",
        "run_date": run_date.isoformat(),
        "watchlist": str(watchlist_path),
        "normalized_root": str(normalized_root),
        "network_call_count": len(requests_),
        "requests": [request.as_dict() for request in requests_],
        "unavailable": unavailable,
        "dry_run": bool(args.dry_run),
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    started_at = datetime.now(timezone.utc)
    run_id = _run_id(started_at)
    landing_run_root = project_root / "data/landing/research/target_prices" / run_id
    with requests.Session() as session:
        yahoo_rows = collect_yahoo_rows(
            requests_,
            run_date=run_date,
            landing_run_root=landing_run_root,
            session=session,
        )
    unavailable_rows = [
        korean_unavailable_row(
            security,
            run_date=run_date,
            retrieved_at=started_at,
        )
        for security in securities
        if security.region == "KR" and security.symbol not in completed
    ]
    new_rows = yahoo_rows + unavailable_rows
    if new_rows:
        combined = append_target_price_vintages_atomic(
            rows_to_frame(new_rows), normalized_root,
        )
        total_rows = len(combined)
    else:
        try:
            total_rows = len(read_target_price_consensus(normalized_root))
        except FileNotFoundError:
            total_rows = 0
    result = {
        **plan,
        "dry_run": False,
        "run_id": run_id,
        "landing_run_root": str(landing_run_root),
        "appended_rows": len(new_rows),
        "normalized_rows": total_rows,
        "status": "PASS" if new_rows else "NOOP_ALREADY_RECORDED",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
