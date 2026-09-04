from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from stock_data.orchestration.kr_equity_investor_flow_daily import (  # noqa: E402
    normalize_symbols,
    run_kr_equity_investor_flow_daily,
    validate_window,
)
from stock_data.providers.pykrx.kr_equity_investor import (  # noqa: E402
    PykrxEquityInvestorClient,
)


def _symbol_values(values: list[str]) -> tuple[str, ...]:
    expanded = [item.strip() for value in values for item in value.split(",") if item.strip()]
    return normalize_symbols(expanded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Landing-first pykrx per-stock investor net-purchase refresh "
            "(one call per symbol, at most one calendar year)"
        )
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)

    symbols = _symbol_values(args.symbols)
    requested_days = validate_window(args.start, args.end)
    provider = PykrxEquityInvestorClient(
        manual=True,
        requested_days=requested_days,
    )
    result = run_kr_equity_investor_flow_daily(
        args.project_root,
        symbols=symbols,
        start=args.start,
        end=args.end,
        provider=provider,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
