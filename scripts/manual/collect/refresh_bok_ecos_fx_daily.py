"""Bounded Landing-first BOK ECOS USD/KRW daily refresh."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.bok_ecos_fx_daily import refresh_range
from stock_data.providers.bok_ecos_fx_daily import BokEcosFxProviderError


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh official BOK ECOS 731Y001/0000001 USD/KRW daily data",
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--start", type=_date, required=True)
    parser.add_argument("--end", type=_date, required=True)
    parser.add_argument("--confirm-live", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_live:
        raise SystemExit("live BOK ECOS collection requires --confirm-live")
    root = args.project_root.resolve()
    # The credential is loaded into process memory only.  It is never printed,
    # placed in a result object, written to Landing, or included in a request log.
    from dotenv import load_dotenv

    load_dotenv(root / ".env", override=False)
    api_key = os.environ.get("BOK_ECOS_API_KEY", "")
    try:
        result = refresh_range(
            root, start=args.start, end=args.end, api_key=api_key,
        )
    except (BokEcosFxProviderError, OSError, TypeError, ValueError) as error:
        # Adapter errors are deliberately constructed without request URLs or
        # provider exception text, so this cannot disclose the path credential.
        raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
