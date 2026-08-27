"""Sequential, read-only Toss market schema smoke with a sanitized fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_data.providers.tossinvest import (  # noqa: E402
    DEFAULT_BASE_URL,
    TossInvestClient,
    TossInvestError,
    TossInvestRateLimitError,
)


FIXTURE_PATH = ROOT / "tests" / "fixtures" / "tossinvest_market_live.json"
OPERATIONS = (
    (
        "market_indicator_prices",
        "/api/v1/market-indicators/prices",
        {
            "symbols": (
                "KOSPI,KOSDAQ,KR_BOND_2Y,KR_BOND_3Y,KR_BOND_5Y,"
                "KR_BOND_10Y,KR_BOND_20Y,KR_BOND_30Y"
            )
        },
    ),
    (
        "market_indicator_daily_candles",
        "/api/v1/market-indicators/KOSPI/candles",
        {"interval": "1d", "count": 2},
    ),
    (
        "market_investor_trading",
        "/api/v1/market-indicators/KOSPI/investor-trading",
        {"interval": "1d", "count": 2},
    ),
    (
        "stock_program_trades",
        "/api/v1/stocks/005930/program-trades",
        {"count": 2},
    ),
    (
        "stock_short_selling",
        "/api/v1/stocks/005930/short-selling",
        {"count": 2},
    ),
    (
        "stock_credit_trades",
        "/api/v1/stocks/005930/credit-trades",
        {"count": 2},
    ),
    (
        "stock_securities_lending",
        "/api/v1/stocks/005930/securities-lending",
        {"count": 2},
    ),
)


def _rate_limit_payload(rate_limit) -> dict[str, int | str | None]:
    return {
        "group": rate_limit.group,
        "limit": rate_limit.limit,
        "remaining": rate_limit.remaining,
        "reset_seconds": rate_limit.reset_seconds,
        "retry_after_seconds": rate_limit.retry_after_seconds,
    }


def _row_count(payload: dict[str, object]) -> int | None:
    result = payload.get("result")
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        for field in ("records", "candles"):
            rows = result.get(field)
            if isinstance(rows, list):
                return len(rows)
    return None


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    readiness = {
        "TOSSINVEST_BASE_URL": bool(
            os.getenv("TOSSINVEST_BASE_URL", "").strip() or DEFAULT_BASE_URL
        ),
        "TOSSINVEST_CLIENT_ID": bool(
            os.getenv("TOSSINVEST_CLIENT_ID", "").strip()
        ),
        "TOSSINVEST_CLIENT_SECRET": bool(
            os.getenv("TOSSINVEST_CLIENT_SECRET", "").strip()
        ),
    }
    if not all(readiness.values()):
        print(
            json.dumps(
                {"status": "LIVE_NOT_READY", "credentials": readiness},
                ensure_ascii=False,
            )
        )
        return 2

    client = TossInvestClient.from_environment(project_root=ROOT)
    fixture: dict[str, object] = {
        "fixture_version": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": "toss_securities_open_api",
        "operations": {},
    }
    summaries: list[dict[str, object]] = []
    token_status = "NOT_CALLED"
    try:
        client.access_token()
        token_status = "TOKEN_OK"
    except TossInvestError as error:
        details = error.details
        print(
            json.dumps(
                {
                    "status": "TOKEN_ERROR",
                    "http_status": details.http_status if details else None,
                    "error_code": details.error_code if details else None,
                    "error_message": details.error_message if details else None,
                    "token_calls": client.token_request_count,
                    "market_calls": client.market_request_count,
                },
                ensure_ascii=False,
            )
        )
        return 1

    for name, path, params in OPERATIONS:
        try:
            response = client.get_market_data(path, params=params)
            operation = {
                "endpoint": path,
                "params": params,
                "http_status": response.http_status,
                "rate_limit": _rate_limit_payload(response.rate_limit),
                "response": response.payload,
            }
            fixture["operations"][name] = operation
            summaries.append(
                {
                    "name": name,
                    "endpoint": path,
                    "status": response.http_status,
                    "rows": _row_count(response.payload),
                    "rate_limit": _rate_limit_payload(response.rate_limit),
                }
            )
        except TossInvestError as error:
            details = error.details
            rate_limit = details.rate_limit if details else None
            operation = {
                "endpoint": path,
                "params": params,
                "http_status": details.http_status if details else None,
                "rate_limit": (
                    _rate_limit_payload(rate_limit) if rate_limit else None
                ),
                "error_code": details.error_code if details else None,
                "error_message": details.error_message if details else None,
            }
            fixture["operations"][name] = operation
            summaries.append({"name": name, **operation})
            if isinstance(error, TossInvestRateLimitError):
                break

    _atomic_json(FIXTURE_PATH, fixture)
    print(
        json.dumps(
            {
                "status": "SMOKE_COMPLETE",
                "token_status": token_status,
                "token_calls": client.token_request_count,
                "market_calls": client.market_request_count,
                "fixture": str(FIXTURE_PATH.relative_to(ROOT)),
                "operations": summaries,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
