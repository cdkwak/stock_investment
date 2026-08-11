"""Probe bounded Toss market-history anchors without backfill or retries."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_data.providers.tossinvest import (  # noqa: E402
    DEFAULT_BASE_URL,
    TossInvestClient,
    TossInvestError,
    TossInvestRateLimitError,
)


REPORT_PATH = ROOT / "tests" / "fixtures" / "tossinvest_historical_probe.json"
ANCHOR_DATES = (
    "2025-01-02",
    "2023-01-02",
    "2020-01-02",
    "2018-01-02",
    "2015-01-02",
    "2010-01-02",
)
SERIES = (
    ("market_index_kospi", "/api/v1/market-indicators/KOSPI/candles", "before"),
    ("market_index_kosdaq", "/api/v1/market-indicators/KOSDAQ/candles", "before"),
    ("investor_kospi", "/api/v1/market-indicators/KOSPI/investor-trading", "until"),
    ("investor_kosdaq", "/api/v1/market-indicators/KOSDAQ/investor-trading", "until"),
    ("program_005930", "/api/v1/stocks/005930/program-trades", "until"),
    ("short_selling_005930", "/api/v1/stocks/005930/short-selling", "until"),
    ("credit_005930", "/api/v1/stocks/005930/credit-trades", "until"),
    (
        "securities_lending_005930",
        "/api/v1/stocks/005930/securities-lending",
        "until",
    ),
    (
        "treasury_10y",
        "/api/v1/market-indicators/KR_BOND_10Y/candles",
        "before",
    ),
)


def _series_params(name: str, cursor_kind: str, anchor: str) -> dict[str, object]:
    if cursor_kind == "before":
        return {
            "interval": "1d",
            "count": 1,
            "before": f"{anchor}T23:59:59+09:00",
        }
    params: dict[str, object] = {"count": 1, "until": anchor}
    if name.startswith("investor_"):
        params["interval"] = "1d"
    return params


def _extract(payload: dict[str, object]) -> tuple[list[dict[str, object]], object]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return [], None
    rows = result.get("candles", result.get("records", []))
    if not isinstance(rows, list):
        return [], None
    cursor = result.get("nextBefore", result.get("nextUntil"))
    return [row for row in rows if isinstance(row, dict)], cursor


def _row_date(row: dict[str, object]) -> str | None:
    raw = row.get("date", row.get("timestamp"))
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def _rate_payload(rate) -> dict[str, int | str | None]:
    return {
        "group": rate.group,
        "limit": rate.limit,
        "remaining": rate.remaining,
        "reset_seconds": rate.reset_seconds,
        "retry_after_seconds": rate.retry_after_seconds,
    }


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
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
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


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
        print(json.dumps({"status": "LIVE_NOT_READY", "credentials": readiness}))
        return 2

    client = TossInvestClient.from_environment(project_root=ROOT)
    report: dict[str, object] = {
        "report_version": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": "toss_securities_open_api",
        "symbol_note": "005930 is the requested long-listed representative symbol.",
        "anchors": list(ANCHOR_DATES),
        "series": {},
    }
    stopped_on_429 = False

    try:
        client.access_token()
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
                },
                ensure_ascii=False,
            )
        )
        return 1

    for name, path, cursor_kind in SERIES:
        probes: list[dict[str, object]] = []
        report["series"][name] = {
            "endpoint": path,
            "cursor_parameter": cursor_kind,
            "probes": probes,
        }
        for anchor in ANCHOR_DATES:
            params = _series_params(name, cursor_kind, anchor)
            try:
                response = client.get_market_data(path, params=params)
                rows, next_cursor = _extract(response.payload)
                returned_dates = [value for row in rows if (value := _row_date(row))]
                no_future_rows = all(value <= anchor for value in returned_dates)
                probes.append(
                    {
                        "anchor": anchor,
                        "params": params,
                        "http_status": response.http_status,
                        "row_count": len(rows),
                        "returned_dates": returned_dates,
                        "no_future_rows": no_future_rows,
                        "valid_empty": len(rows) == 0,
                        "next_cursor": next_cursor,
                        "rate_limit": _rate_payload(response.rate_limit),
                        "sample": rows[0] if rows else None,
                    }
                )
                # The strictest documented group is 5 TPS. This is pacing, not retry.
                time.sleep(0.25)
            except TossInvestError as error:
                details = error.details
                probes.append(
                    {
                        "anchor": anchor,
                        "params": params,
                        "http_status": details.http_status if details else None,
                        "error_code": details.error_code if details else None,
                        "error_message": details.error_message if details else None,
                        "rate_limit": (
                            _rate_payload(details.rate_limit)
                            if details and details.rate_limit
                            else None
                        ),
                    }
                )
                if isinstance(error, TossInvestRateLimitError):
                    stopped_on_429 = True
                    break
        if stopped_on_429:
            break

    report["token_calls"] = client.token_request_count
    report["market_calls"] = client.market_request_count
    report["stopped_on_429"] = stopped_on_429
    _atomic_json(REPORT_PATH, report)
    print(
        json.dumps(
            {
                "status": "STOPPED_ON_429" if stopped_on_429 else "PROBE_COMPLETE",
                "token_calls": client.token_request_count,
                "market_calls": client.market_request_count,
                "series_completed": len(report["series"]),
                "report": str(REPORT_PATH.relative_to(ROOT)),
            },
            ensure_ascii=False,
        )
    )
    return 3 if stopped_on_429 else 0


if __name__ == "__main__":
    raise SystemExit(main())
