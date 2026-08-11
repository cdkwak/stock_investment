"""Checkpointed Toss historical integration. Read-only market endpoints only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_data.contracts.tossinvest_historical import (  # noqa: E402
    KR_EQUITY_CREDIT_TRADING_DAILY,
    KR_EQUITY_PROGRAM_TRADING_DAILY,
    KR_EQUITY_SECURITIES_LENDING_DAILY,
    KR_EQUITY_SHORT_SELLING_DAILY,
    KR_MARKET_INVESTOR_TRADING_DAILY,
    KR_TREASURY_YIELD_DAILY,
)
from stock_data.pipelines.tossinvest_historical import (  # noqa: E402
    _atomic_json,
    _extract,
    _pace,
    _rate_payload,
    backfill_toss_targets,
)
from stock_data.providers.tossinvest import (  # noqa: E402
    TossInvestClient,
    TossInvestError,
    TossInvestHTTPError,
    TossInvestRateLimitError,
    normalize_credit_trading,
    normalize_market_investor,
    normalize_program_trading,
    normalize_securities_lending,
    normalize_short_selling,
    normalize_treasury_yield,
)
from stock_data.providers.tossinvest.historical import (  # noqa: E402
    CREDIT_TRADING_OPERATION,
    MARKET_INVESTOR_OPERATION,
    PROGRAM_TRADING_OPERATION,
    SECURITIES_LENDING_OPERATION,
    SHORT_SELLING_OPERATION,
    TREASURY_YIELD_OPERATION,
)


SURVIVORSHIP_SAMPLES = {
    "003410": "2024-07-08",  # Ssangyong C&E
    "091990": "2024-01-11",  # Celltrion Healthcare
    "115390": "2024-12-06",  # LocknLock
}
TREASURY_INSTRUMENTS = tuple(f"KR_BOND_{tenor}Y" for tenor in (2, 3, 5, 10, 20, 30))
STOCK_SPECS = {
    "short": (KR_EQUITY_SHORT_SELLING_DAILY, "short-selling", SHORT_SELLING_OPERATION, normalize_short_selling, "2019-01-01"),
    "program": (KR_EQUITY_PROGRAM_TRADING_DAILY, "program-trades", PROGRAM_TRADING_OPERATION, normalize_program_trading, "2019-01-01"),
    "lending": (KR_EQUITY_SECURITIES_LENDING_DAILY, "securities-lending", SECURITIES_LENDING_OPERATION, normalize_securities_lending, "2021-01-01"),
    "credit": (KR_EQUITY_CREDIT_TRADING_DAILY, "credit-trades", CREDIT_TRADING_OPERATION, normalize_credit_trading, "2023-01-01"),
}


def _market(client):
    return backfill_toss_targets(
        ROOT, client=client, contract=KR_MARKET_INVESTOR_TRADING_DAILY,
        targets=("KOSPI", "KOSDAQ"),
        endpoint_for_target=lambda market: f"/api/v1/market-indicators/{market}/investor-trading",
        base_params={"interval": "1d", "count": 100}, cursor_parameter="until",
        cursor_key="nextUntil", row_key="records", operation=MARKET_INVESTOR_OPERATION,
        normalize_for_target=lambda market, rows, observed: normalize_market_investor(
            rows, market=market, collected_at=observed
        ), batch_size=2,
    )


def _canonical_symbols(start_date: str) -> list[str]:
    root = ROOT / "data/published/kr_equity_canonical_universe_daily"
    symbols: set[str] = set()
    start_year = int(start_date[:4])
    for path in root.rglob("data.parquet"):
        year = next((part.split("=", 1)[1] for part in path.parts if part.startswith("year=")), None)
        if year is not None and int(year) < start_year:
            continue
        frame = pd.read_parquet(path, columns=["date", "symbol"])
        frame["date"] = pd.to_datetime(frame["date"], errors="raise")
        values = frame.loc[frame["date"] >= pd.Timestamp(start_date), "symbol"].astype(str)
        symbols.update(value for value in values if len(value) == 6 and value.isdigit())
    return sorted(symbols)


def _survivorship(client: TossInvestClient, dataset_key: str) -> dict[str, object]:
    contract, endpoint, operation, _, _ = STOCK_SPECS[dataset_key]
    state_path = ROOT / "data/state/toss_survivorship.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
        "source": "tossinvest_open_api", "results": {}, "market_calls": 0, "token_calls": 0,
    }
    operation_results = state["results"].setdefault(operation, {})
    stopped_429 = False
    for symbol, last_date in SURVIVORSHIP_SAMPLES.items():
        if any(
            item.get("pass") is False
            for key, item in operation_results.items()
            if key != "_status" and isinstance(item, dict)
        ):
            break
        if symbol in operation_results:
            continue
        try:
            response = client.get_market_data(
                f"/api/v1/stocks/{symbol}/{endpoint}", params={"count": 1, "until": last_date}
            )
            rows, cursor = _extract(response.payload, "records", "nextUntil")
            observed = datetime.now(timezone.utc)
            relative = Path("data/landing/tossinvest/survivorship") / operation / symbol / f"{observed.strftime('%Y%m%dT%H%M%S%fZ')}.json"
            _atomic_json(ROOT / relative, {
                "collected_at": observed.isoformat(), "source": "tossinvest_open_api",
                "operation": operation, "target": symbol, "until": last_date,
                "rate_limit": _rate_payload(response.rate_limit), "raw_response": response.payload,
            })
            dates = [str(row.get("date", "")) for row in rows]
            operation_results[symbol] = {
                "canonical_last_date": last_date,
                "rows": len(rows),
                "returned_dates": dates,
                "next_until": cursor,
                "pass": bool(rows) and all(value <= last_date for value in dates),
            }
            state["market_calls"] = int(state.get("market_calls", 0)) + 1
            _atomic_json(state_path, state)
            _pace(response.rate_limit)
        except TossInvestRateLimitError:
            stopped_429 = True
            state["stopped_on_429"] = True
            _atomic_json(state_path, state)
            break
        except TossInvestHTTPError as error:
            details = error.details
            state["market_calls"] = int(state.get("market_calls", 0)) + 1
            operation_results[symbol] = {
                "canonical_last_date": last_date,
                "rows": 0,
                "pass": False,
                "http_status": details.http_status if details else None,
                "error_code": details.error_code if details else None,
            }
            _atomic_json(state_path, state)
            break
    sample_results = {
        symbol: item for symbol, item in operation_results.items() if symbol != "_status"
    }
    passed = len(sample_results) == len(SURVIVORSHIP_SAMPLES) and all(
        item.get("pass") is True for item in sample_results.values()
    )
    state["results"][operation]["_status"] = "PASS" if passed else (
        "STOPPED_429" if stopped_429 else "NOT_SURVIVORSHIP_SAFE"
    )
    _atomic_json(state_path, state)
    return {"dataset": contract.name, "survivorship": state["results"][operation]["_status"],
            "samples": len(sample_results)}


def _stock(client: TossInvestClient, dataset_key: str):
    contract, endpoint, operation, normalizer, start_date = STOCK_SPECS[dataset_key]
    state_path = ROOT / "data/state/toss_survivorship.json"
    if not state_path.exists():
        return {"dataset": contract.name, "status": "SURVIVORSHIP_NOT_TESTED"}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    status = state.get("results", {}).get(operation, {}).get("_status")
    if status != "PASS":
        return {"dataset": contract.name, "status": status or "SURVIVORSHIP_NOT_TESTED"}
    symbols = _canonical_symbols(start_date)
    result = backfill_toss_targets(
        ROOT, client=client, contract=contract, targets=symbols,
        endpoint_for_target=lambda symbol: f"/api/v1/stocks/{symbol}/{endpoint}",
        base_params={"count": 100}, cursor_parameter="until", cursor_key="nextUntil",
        row_key="records", operation=operation,
        normalize_for_target=lambda symbol, rows, observed: normalizer(
            rows, symbol=symbol, collected_at=observed
        ), batch_size=25,
    )
    return {**result.__dict__, "target_symbols": len(symbols)}


def _treasury(client):
    return backfill_toss_targets(
        ROOT, client=client, contract=KR_TREASURY_YIELD_DAILY,
        targets=TREASURY_INSTRUMENTS,
        endpoint_for_target=lambda instrument: f"/api/v1/market-indicators/{instrument}/candles",
        base_params={"interval": "1d", "count": 200}, cursor_parameter="before",
        cursor_key="nextBefore", row_key="candles", operation=TREASURY_YIELD_OPERATION,
        normalize_for_target=lambda instrument, rows, observed: normalize_treasury_yield(
            rows, instrument=instrument, collected_at=observed
        ), batch_size=6,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("market", "survivorship", "short", "program", "lending", "credit", "treasury", "all"))
    args = parser.parse_args()
    client = TossInvestClient.from_environment(project_root=ROOT)
    report: dict[str, object] = {"phase": args.phase, "results": []}
    try:
        if args.phase in {"market", "all"}:
            report["results"].append(_market(client).__dict__)
        stock_order = ("short", "program", "lending", "credit")
        if args.phase in {"survivorship", "all"}:
            for key in stock_order:
                report["results"].append(_survivorship(client, key))
        if args.phase in stock_order:
            report["results"].append(_stock(client, args.phase))
        elif args.phase == "all":
            for key in stock_order:
                report["results"].append(_stock(client, key))
        if args.phase in {"treasury", "all"}:
            report["results"].append(_treasury(client).__dict__)
    except TossInvestError as error:
        details = error.details
        report["error"] = {
            "type": type(error).__name__,
            "http_status": details.http_status if details else None,
            "error_code": details.error_code if details else None,
        }
    report["token_calls"] = client.token_request_count
    report["market_calls"] = client.market_request_count
    print(json.dumps(report, ensure_ascii=False))
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    raise SystemExit(main())
