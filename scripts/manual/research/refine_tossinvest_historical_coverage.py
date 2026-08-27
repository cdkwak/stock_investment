"""Find the first Toss coverage year inside already-confirmed anchor brackets."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
MANUAL = ROOT / "scripts" / "manual" / "research"
for directory in (SRC, MANUAL):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from probe_tossinvest_historical_coverage import (  # noqa: E402
    REPORT_PATH,
    _extract,
    _rate_payload,
    _row_date,
)
from stock_data.providers.tossinvest import (  # noqa: E402
    DEFAULT_BASE_URL,
    TossInvestClient,
    TossInvestError,
    TossInvestRateLimitError,
)


def _year_bounds(probes: list[dict[str, object]]) -> tuple[int, int] | None:
    ordered = sorted(probes, key=lambda item: str(item["anchor"]))
    for index, probe in enumerate(ordered):
        if int(probe.get("row_count", 0)) <= 0:
            continue
        preceding_empty = [
            item
            for item in ordered[:index]
            if item.get("valid_empty") is True
        ]
        if not preceding_empty:
            return None
        return int(str(preceding_empty[-1]["anchor"])[:4]), int(
            str(probe["anchor"])[:4]
        )
    return None


def _params(name: str, cursor_parameter: str, year: int) -> dict[str, object]:
    if cursor_parameter == "before":
        return {
            "interval": "1d",
            "count": 1,
            "before": f"{year}-12-31T23:59:59+09:00",
        }
    params: dict[str, object] = {"count": 1, "until": f"{year}-12-31"}
    if name.startswith("investor_"):
        params["interval"] = "1d"
    return params


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
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
    if not all(readiness.values()) or not REPORT_PATH.exists():
        print(json.dumps({"status": "REFINE_NOT_READY", "credentials": readiness}))
        return 2

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    client = TossInvestClient.from_environment(project_root=ROOT)
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
                    "token_calls": client.token_request_count,
                }
            )
        )
        return 1

    for name, series in report["series"].items():
        bounds = _year_bounds(series["probes"])
        refinements: list[dict[str, object]] = []
        series["refinement_probes"] = refinements
        if bounds is None:
            continue
        left, right = bounds
        while left < right:
            year = (left + right) // 2
            params = _params(name, series["cursor_parameter"], year)
            try:
                response = client.get_market_data(series["endpoint"], params=params)
                rows, next_cursor = _extract(response.payload)
                dates = [value for row in rows if (value := _row_date(row))]
                probe = {
                    "year_end": year,
                    "params": params,
                    "http_status": response.http_status,
                    "row_count": len(rows),
                    "returned_dates": dates,
                    "no_future_rows": all(value[:4] <= str(year) for value in dates),
                    "valid_empty": len(rows) == 0,
                    "next_cursor": next_cursor,
                    "rate_limit": _rate_payload(response.rate_limit),
                    "sample": rows[0] if rows else None,
                }
                refinements.append(probe)
                if rows:
                    right = year
                else:
                    left = year + 1
                time.sleep(0.25)
            except TossInvestError as error:
                details = error.details
                refinements.append(
                    {
                        "year_end": year,
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
        if not stopped_on_429:
            series["first_data_year"] = left
        else:
            break

    report["refined_at"] = datetime.now(timezone.utc).isoformat()
    report["refinement_token_calls"] = client.token_request_count
    report["refinement_market_calls"] = client.market_request_count
    report["total_token_calls"] = report.get("token_calls", 0) + client.token_request_count
    report["total_market_calls"] = report.get("market_calls", 0) + client.market_request_count
    report["stopped_on_429"] = bool(report.get("stopped_on_429")) or stopped_on_429
    _atomic_json(REPORT_PATH, report)
    print(
        json.dumps(
            {
                "status": "STOPPED_ON_429" if stopped_on_429 else "REFINE_COMPLETE",
                "token_calls": client.token_request_count,
                "market_calls": client.market_request_count,
                "report": str(REPORT_PATH.relative_to(ROOT)),
            },
            ensure_ascii=False,
        )
    )
    return 3 if stopped_on_429 else 0


if __name__ == "__main__":
    raise SystemExit(main())
