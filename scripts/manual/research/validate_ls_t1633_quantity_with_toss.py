"""One-date, Landing-first Toss validation for LS t1633 quantity units.

The universe is the retained PIT-safe canonical universe, never a current-symbol
fan-out.  This is a strict all-symbol comparison: a missing, failed, malformed,
or wrong-date Toss response makes that market's aggregate non-authoritative.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any
from uuid import uuid4
import re

from dotenv import load_dotenv
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_data.providers.tossinvest import (  # noqa: E402
    TossInvestClient,
)


TARGET_DATE = "2025-01-02"  # Before NXT trading; both sources are KRX-only.
MARKETS = ("KOSPI", "KOSDAQ")
REQUEST_INTERVAL_SECONDS = 0.12  # Below retained STOCK_TRADING_TREND 10 TPS.
LANDING_ROOT = ROOT / "data/landing/tossinvest/ls_t1633_quantity_validation"
STATE_ROOT = ROOT / "data/state/toss_ls_t1633_quantity_validation"
LOCK_PATH = STATE_ROOT / "active.lock"
FIELDS = (
    ("arbitrage", "buyVolume", "cha1"),
    ("arbitrage", "sellVolume", "cha2"),
    ("arbitrage", "netBuyVolume", "cha3"),
    ("nonArbitrage", "buyVolume", "bcha1"),
    ("nonArbitrage", "sellVolume", "bcha2"),
    ("nonArbitrage", "netBuyVolume", "bcha3"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"invalid integer {field}")
    raw = str(value).strip().replace(",", "")
    if not raw or raw.lstrip("+-").isdigit() is False:
        raise ValueError(f"invalid integer {field}")
    return int(raw)


def validate_exact_record(payload: dict[str, Any], target_date: str) -> dict[str, int]:
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("records"), list):
        raise ValueError("unexpected Toss program response shape")
    records = result["records"]
    if len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("expected exactly one Toss program record")
    record = records[0]
    if record.get("date") != target_date:
        raise ValueError("Toss record date differs from requested date")
    output: dict[str, int] = {}
    for group, field, target in FIELDS:
        node = record.get(group)
        if not isinstance(node, dict):
            raise ValueError(f"missing group {group}")
        output[target] = _as_int(node.get(field), f"{group}.{field}")
    for prefix in ("cha", "bcha"):
        if output[prefix + "3"] != output[prefix + "1"] - output[prefix + "2"]:
            raise ValueError(f"{prefix} buy-sell-net invariant failed")
    return output


def exact_multiplier(toss: dict[str, int], ls: dict[str, int]) -> int | None:
    """Return a multiplier only when every non-zero comparable field agrees exactly."""
    ratios: set[Fraction] = set()
    for _, _, field in FIELDS:
        left, right = toss[field], ls[field]
        if left == right == 0:
            continue
        if right == 0:
            return None
        ratios.add(Fraction(left, right))
    if len(ratios) != 1:
        return None
    ratio = ratios.pop()
    return ratio.numerator if ratio.denominator == 1 else None


def _universe(market: str, target_date: str) -> list[str]:
    path = ROOT / f"data/published/kr_equity_canonical_universe_daily/market={market}/year={target_date[:4]}/data.parquet"
    frame = pd.read_parquet(path, filters=[("date", "==", date.fromisoformat(target_date))])
    symbols = sorted(set(frame["symbol"].astype(str)))
    # Retained canonical KRX symbols include a small number of six-character
    # alphanumeric listing codes (for example, 00088K).  Preserve those source
    # identifiers rather than silently dropping them or reverting to a current
    # Toss universe.
    if not symbols or any(re.fullmatch(r"[0-9A-Z]{6}", symbol) is None for symbol in symbols):
        raise ValueError(f"invalid canonical universe for {market} {target_date}")
    return symbols


def _ls_row(market: str, target_date: str) -> dict[str, int]:
    candidates = list((ROOT / "data/landing/ls/t1633_program_trading_raw").rglob(
        f"*{market.lower()}_quantity*.response.json"
    ))
    found: dict[str, int] | None = None
    for path in candidates:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for value in payload.values():
            if not isinstance(value, list):
                continue
            for row in value:
                if isinstance(row, dict) and row.get("date") == target_date.replace("-", ""):
                    parsed = {name: _as_int(row.get(name), "LS." + name) for _, _, name in FIELDS}
                    if found is not None and found != parsed:
                        raise ValueError(f"conflicting LS duplicate {market} {target_date}")
                    found = parsed
    if found is None:
        raise ValueError(f"missing LS t1633 quantity row {market} {target_date}")
    return found


def _headers(response) -> dict[str, str | None]:
    return {
        "content_type": response.headers.get("Content-Type"),
        "x_rate_limit_limit": response.headers.get("X-RateLimit-Limit"),
        "x_rate_limit_remaining": response.headers.get("X-RateLimit-Remaining"),
        "x_rate_limit_reset": response.headers.get("X-RateLimit-Reset"),
        "retry_after": response.headers.get("Retry-After"),
        "request_id": response.headers.get("X-Request-Id"),
    }


def _fetch_once(client: TossInvestClient, symbol: str, run_dir: Path) -> tuple[int, dict[str, Any] | None, dict[str, Any]]:
    path = f"/api/v1/stocks/{symbol}/program-trades"
    params = {"count": 1, "until": TARGET_DATE}
    collected_at = _utc_now()
    response = client._session.get(  # noqa: SLF001 - Landing-first raw capture needs bytes.
        client.base_url + path,
        headers={**client.authorization_headers(), "Accept": "application/json"},
        params=params,
        timeout=client._timeout,  # noqa: SLF001
    )
    client._market_request_count += 1  # noqa: SLF001
    body = response.content
    body_path = run_dir / f"symbol={symbol}" / "response.body"
    _atomic_bytes(body_path, body)  # Persist before JSON parsing.
    provenance = {
        "provider": "toss_securities_open_api",
        "source_operation": "getStockProgramTrades",
        "requested_url": client.base_url + path,
        "request_parameters": params,
        "canonical_market_date": TARGET_DATE,
        "canonical_symbol": symbol,
        "collected_at_utc": collected_at,
        "http_status": response.status_code,
        "http_metadata": _headers(response),
        "raw_response_bytes": len(body),
        "raw_response_sha256": hashlib.sha256(body).hexdigest(),
    }
    _atomic_json(body_path.with_name("provenance.json"), provenance)
    if response.status_code in {403, 429}:
        raise RuntimeError(f"hard_stop_http_{response.status_code}")
    if not 200 <= response.status_code < 300:
        return response.status_code, None, provenance
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return response.status_code, None, provenance
    return response.status_code, parsed if isinstance(parsed, dict) else None, provenance


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    if not os.getenv("TOSSINVEST_CLIENT_ID", "").strip() or not os.getenv("TOSSINVEST_CLIENT_SECRET", "").strip():
        print(json.dumps({"status": "LIVE_NOT_READY"}))
        return 2
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        handle = LOCK_PATH.open("x", encoding="utf-8")
    except FileExistsError:
        print(json.dumps({"status": "OWN_NAMESPACE_LOCK_EXISTS"}))
        return 2
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = LANDING_ROOT / f"run={run_id}"
    manifest: dict[str, Any] = {"status": "CAPTURING", "run_id": run_id, "target_date": TARGET_DATE, "markets": {}, "retry_count": 0, "normalized_writes": False}
    try:
        handle.write(run_id + "\n"); handle.close()
        client = TossInvestClient.from_environment(project_root=ROOT)
        client.access_token()
        for market in MARKETS:
            symbols = _universe(market, TARGET_DATE)
            ls = _ls_row(market, TARGET_DATE)
            summary: dict[str, Any] = {"canonical_symbol_count": len(symbols), "ls_quantity": ls, "responses": 0, "missing": [], "date_mismatch": [], "schema_errors": [], "partial_sums": {field: 0 for _, _, field in FIELDS}}
            for index, symbol in enumerate(symbols, start=1):
                status, payload, _ = _fetch_once(client, symbol, run_dir / f"market={market}")
                summary["responses"] += 1
                if status == 404:
                    summary["missing"].append(symbol)
                elif status != 200 or payload is None:
                    summary["schema_errors"].append({"symbol": symbol, "http_status": status})
                else:
                    try:
                        row = validate_exact_record(payload, TARGET_DATE)
                    except ValueError as error:
                        bucket = "date_mismatch" if "date differs" in str(error) else "schema_errors"
                        summary[bucket].append({"symbol": symbol, "reason": str(error)})
                    else:
                        for field, value in row.items():
                            summary["partial_sums"][field] += value
                if index < len(symbols):
                    time.sleep(REQUEST_INTERVAL_SECONDS)
            complete = not summary["missing"] and not summary["date_mismatch"] and not summary["schema_errors"]
            summary["aggregate_status"] = "COMPLETE" if complete else "PARTIAL_NOT_COMPARATOR"
            summary["exact_multiplier"] = exact_multiplier(summary["partial_sums"], ls) if complete else None
            manifest["markets"][market] = summary
            _atomic_json(run_dir / f"market={market}" / "summary.json", summary)
        manifest["status"] = "COMPLETE"
        manifest["token_calls"] = client.token_request_count
        manifest["market_calls"] = client.market_request_count
        _atomic_json(run_dir / "manifest.json", manifest)
        _atomic_json(STATE_ROOT / "latest.json", {"status": "COMPLETE", "run_id": run_id, "landing_run": str(run_dir.relative_to(ROOT)), "target_date": TARGET_DATE, "normalized_writes": False})
        print(json.dumps({"status": "COMPLETE", "run_id": run_id, "market_calls": client.market_request_count}, ensure_ascii=False))
        return 0
    except Exception as error:
        manifest["status"] = "STOPPED"; manifest["stopped_reason"] = str(error)
        _atomic_json(run_dir / "manifest.json", manifest)
        _atomic_json(STATE_ROOT / "latest.json", {"status": "STOPPED", "run_id": run_id, "landing_run": str(run_dir.relative_to(ROOT)), "reason": str(error), "normalized_writes": False})
        print(json.dumps({"status": "STOPPED", "run_id": run_id, "reason": str(error)}, ensure_ascii=False))
        return 1
    finally:
        if LOCK_PATH.exists():
            LOCK_PATH.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
