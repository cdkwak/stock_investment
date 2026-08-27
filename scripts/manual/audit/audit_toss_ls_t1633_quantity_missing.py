"""Offline forensic audit of missing Toss responses from one t1633 validation run."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
RUN_ID = "20260816T153912Z_c604e7210367403bbc2fbf2ee06ec183"
TARGET_DATE = date(2025, 1, 2)
RUN_DIR = ROOT / f"data/landing/tossinvest/ls_t1633_quantity_validation/run={RUN_ID}"
OUT_PATH = ROOT / f"data/state/audits/toss_ls_t1633_quantity_validation/{RUN_ID}_missing_forensic.json"


def security_flags(name: str, security_type: str) -> dict[str, bool]:
    text = f"{name} {security_type}".upper()
    return {
        "common": security_type == "보통주",
        "preferred": "우선" in security_type or "PREFERRED" in security_type.upper(),
        "etf": "ETF" in text,
        "etn": "ETN" in text,
        "spac": "스팩" in name or "SPAC" in text,
        "reit": "리츠" in name or "REIT" in text,
    }


def classify(*, http_status: int, closest_date: str | None) -> tuple[str, str]:
    if http_status == 404:
        return "SYMBOL_MAPPING_ISSUE", "HTTP_404_STOCK_NOT_FOUND"
    if closest_date is None:
        return "NO_EXACT_DATE_UNRESOLVED", "EMPTY_RECORDS"
    return "NO_EXACT_DATE_UNRESOLVED", "LAST_RETURNED_BEFORE_TARGET"


def toss_support_status(*, http_status: int, closest_date: str | None) -> str:
    if http_status == 404:
        return "NOT_FOUND_404"
    if closest_date is None:
        return "HTTP_200_EMPTY_RECORDS_UNRESOLVED"
    return "HISTORY_RETURNED_NO_EXACT_DATE"


def _metadata(market: str) -> dict[str, dict[str, Any]]:
    universe_path = ROOT / f"data/published/kr_equity_canonical_universe_daily/market={market}/year=2025/data.parquet"
    price_path = ROOT / f"data/normalized/kr_equity_price_daily/market={market}/year=2025/data.parquet"
    universe = pd.read_parquet(universe_path, filters=[("date", "==", TARGET_DATE)])
    price = pd.read_parquet(price_path, filters=[("date", "==", TARGET_DATE)])
    prices = price.set_index("symbol").to_dict("index")
    rows: dict[str, dict[str, Any]] = {}
    for row in universe.to_dict("records"):
        symbol = str(row["symbol"])
        price_row = prices.get(symbol)
        volume = None if price_row is None else int(price_row["volume"])
        listing = row.get("listing_date")
        delisting = row.get("delisting_date")
        listing_ok = listing is None or str(listing) <= TARGET_DATE.isoformat()
        delisting_ok = delisting is None or str(delisting) >= TARGET_DATE.isoformat()
        rows[symbol] = {
            "market": market,
            "name": str(row.get("name") or ""),
            "security_type": str(row.get("security_type") or "unclassified"),
            "listing_date": listing,
            "delisting_date": delisting,
            "listing_boundary_valid_on_target": bool(listing_ok and delisting_ok),
            "price_present": price_row is not None,
            "trading_volume": volume,
            "trading_suspension_possible_evidence": (
                "VOLUME_ZERO" if volume == 0 else "NO_PRICE_ROW" if price_row is None else "NONE_OBSERVED"
            ),
        }
        rows[symbol]["security_flags"] = security_flags(rows[symbol]["name"], rows[symbol]["security_type"])
    return rows


def _nearest_date(body: bytes) -> str | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    result = payload.get("result") if isinstance(payload, dict) else None
    records = result.get("records") if isinstance(result, dict) else None
    values = [str(row.get("date")) for row in records if isinstance(row, dict) and row.get("date")]
    return max(values) if values else None


def main() -> int:
    records: list[dict[str, Any]] = []
    for market in ("KOSPI", "KOSDAQ"):
        metadata = _metadata(market)
        for body_path in sorted((RUN_DIR / f"market={market}").glob("symbol=*/response.body")):
            symbol = body_path.parent.name.split("=", 1)[1]
            provenance = json.loads(body_path.with_name("provenance.json").read_text(encoding="utf-8"))
            status = int(provenance["http_status"])
            body = body_path.read_bytes()
            body_sha256 = hashlib.sha256(body).hexdigest()
            if body_sha256 != provenance["raw_response_sha256"]:
                raise RuntimeError(f"raw hash mismatch for {market}/{symbol}")
            if status not in {200, 404}:
                raise RuntimeError(f"unexpected retained HTTP status {status} for {market}/{symbol}")
            closest = _nearest_date(body) if status == 200 else None
            if status == 200 and closest == TARGET_DATE.isoformat():
                continue
            classification, pattern = classify(http_status=status, closest_date=closest)
            meta = metadata.get(symbol, {"market": market, "metadata_missing": True})
            records.append({
                "symbol": symbol,
                **meta,
                "toss_http_status": status,
                "toss_support_status": toss_support_status(http_status=status, closest_date=closest),
                "closest_toss_date": closest,
                "exact_date_omission_pattern": pattern,
                "classification": classification,
                "raw_response_sha256": body_sha256,
            })
    if len(records) != 311:
        raise RuntimeError(f"expected 311 incomplete symbols, got {len(records)}")
    summary: dict[str, Any] = {
        "total": len(records),
        "by_market": {},
        "by_classification": {},
        "zero_volume": 0,
        "no_price_row": 0,
        "hash_verified_records": len(records),
    }
    for row in records:
        for key, value in (("by_market", row["market"]), ("by_classification", row["classification"])):
            summary[key][value] = summary[key].get(value, 0) + 1
        summary["zero_volume"] += row.get("trading_suspension_possible_evidence") == "VOLUME_ZERO"
        summary["no_price_row"] += row.get("trading_suspension_possible_evidence") == "NO_PRICE_ROW"
    payload = {"schema": "stock_data.toss_ls_t1633_missing_forensic_v1", "run_id": RUN_ID, "target_date": TARGET_DATE.isoformat(), "network_calls": 0, "records": records, "summary": summary}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "COMPLETE", "output": str(OUT_PATH.relative_to(ROOT)), "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
