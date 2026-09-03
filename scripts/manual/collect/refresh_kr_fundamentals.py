"""Bounded two-step OpenDART quarterly-fundamentals refresh."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.kr_fundamentals_quarterly import (
    DEFAULT_MAX_CALLS,
    load_universe_symbols,
    load_watchlist_symbols,
    prepare_collection,
    promote_checkpoint,
)


def _years(value: str) -> tuple[int, ...]:
    try:
        years = tuple(dict.fromkeys(int(item.strip()) for item in value.split(",") if item.strip()))
    except ValueError as error:
        raise argparse.ArgumentTypeError("years must be comma-separated integers") from error
    if not years:
        raise argparse.ArgumentTypeError("at least one year is required")
    return years


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Landing-first OpenDART quarterly fundamentals refresh (retry 0, timeout 20s)",
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--symbols", nargs="+", help="Exact six-digit Korean stock codes")
    selector.add_argument("--universe", action="store_true", help="Use all retained listed Korean stocks")
    parser.add_argument("--years", type=_years, default=_years("2024,2025,2026"))
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument("--confirm-live-landing-only", action="store_true")
    parser.add_argument("--promote-checkpoint", type=Path)
    parser.add_argument("--confirm-offline-promotion", action="store_true")
    parser.add_argument("--approval-digest")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    # Same convention as the other manual collectors: the key lives in <root>/.env and is
    # loaded into the process environment only (never printed, never persisted elsewhere).
    from dotenv import load_dotenv

    load_dotenv(root / ".env", override=False)
    if args.promote_checkpoint:
        if not args.confirm_offline_promotion or not args.approval_digest:
            raise SystemExit("promotion requires offline confirmation and the exact approval digest")
        result = promote_checkpoint(
            root, args.promote_checkpoint.resolve(),
            expected_approval_digest=args.approval_digest,
        )
    else:
        if not args.confirm_live_landing_only:
            raise SystemExit("live collection requires --confirm-live-landing-only")
        symbols = (
            tuple(args.symbols) if args.symbols else
            load_universe_symbols(root) if args.universe else
            load_watchlist_symbols(root)
        )
        result = prepare_collection(
            root, symbols=symbols, years=args.years, max_calls=args.max_calls,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
