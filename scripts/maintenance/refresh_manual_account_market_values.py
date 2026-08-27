"""API-zero manual-account market-value refresh with injected local evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.gui.manual_account_snapshot import parse_manual_account_snapshot
from stock_data.orchestration.manual_account_market_values import (
    refresh_manual_account_market_values,
)
from stock_data.providers.yahoo_account_prices import (
    YahooAccountPriceSymbol,
    yahoo_account_price_unavailable,
)


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _symbol_map(payload: object) -> dict[tuple[str, str], YahooAccountPriceSymbol]:
    if (
        not isinstance(payload, dict) or set(payload) != {"schema_version", "symbols"}
        or payload["schema_version"] != 1 or not isinstance(payload["symbols"], list)
    ):
        raise ValueError("symbol-map fixture differs")
    result = {}
    keys = {"section", "ticker", "provider_symbol", "exchange", "currency"}
    for row in payload["symbols"]:
        if not isinstance(row, dict) or set(row) != keys:
            raise ValueError("symbol-map row differs")
        key = (row["section"], row["ticker"])
        if key in result:
            raise ValueError("symbol-map row is duplicated")
        result[key] = YahooAccountPriceSymbol(**row)
    return result


def _supplier(payload: object, symbols: dict[tuple[str, str], YahooAccountPriceSymbol]):
    if (
        not isinstance(payload, dict) or set(payload) != {"schema_version", "results"}
        or payload["schema_version"] != 1 or not isinstance(payload["results"], list)
    ):
        raise ValueError("observation fixture differs")
    rows = payload["results"]

    def supply(requested):
        requested_keys = {(item.section, item.ticker) for item in requested}
        result = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("observation row differs")
            key = (row.get("section"), row.get("ticker"))
            if key not in requested_keys or key in result:
                raise ValueError("observation identity is unrequested or duplicated")
            status = row.get("status")
            if status == "UNAVAILABLE" and set(row) == {
                "section", "ticker", "status", "reason",
            }:
                result[key] = yahoo_account_price_unavailable(
                    symbols[key], row["reason"],
                )
            elif status == "AVAILABLE" and set(row) == {
                "section", "ticker", "status", "provider", "provider_symbol",
                "exchange", "currency", "unit", "price", "as_of",
                "captured_at", "finality",
            }:
                result[key] = {
                    field: row[field] for field in (
                        "provider", "provider_symbol", "exchange", "currency",
                        "unit", "price", "as_of", "captured_at", "finality",
                    )
                }
            else:
                raise ValueError("observation status/keys differ")
        return result

    return supply


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh a sanitized manual-account value cache from injected local "
            "fixtures. This command performs zero provider/network calls."
        )
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--basis", type=Path, required=True)
    parser.add_argument("--symbol-map", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/local/manual_account_market_values/latest.json"),
    )
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    output = args.output if args.output.is_absolute() else project_root / args.output
    allowed = (project_root / "data/local/manual_account_market_values").resolve()
    try:
        output.resolve().parent.relative_to(allowed)
    except ValueError:
        raise ValueError("--output must remain under data/local/manual_account_market_values") from None
    basis = parse_manual_account_snapshot(_json(args.basis))
    symbols = _symbol_map(_json(args.symbol_map))
    result = refresh_manual_account_market_values(
        basis, symbol_map=symbols,
        supplier=_supplier(_json(args.observations), symbols), cache_path=output,
    )
    print(json.dumps({
        "status": result.status, "requested_symbols": result.requested_symbols,
        "available_rows": result.available_rows,
        "unavailable_rows": result.unavailable_rows,
        "provider_calls": 0, "reason": result.reason,
    }, sort_keys=True, separators=(",", ":")))
    return 0 if result.status == "UPDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
