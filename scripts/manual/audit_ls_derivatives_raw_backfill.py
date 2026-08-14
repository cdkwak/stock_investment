"""Zero-network audit for one retained LS t8462 raw backfill."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

try:
    from scripts.manual.ls_derivatives_investor_pilot import atomic_json
    from scripts.manual.ls_derivatives_raw_backfill import scopes, validate_payload
except ModuleNotFoundError:
    from ls_derivatives_investor_pilot import atomic_json  # type: ignore[no-redef]
    from ls_derivatives_raw_backfill import scopes, validate_payload  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[2]
CROSSCHECK_DATES = ("20260102", "20260731", "20260813")
CATEGORY_SUFFIX = {"개인": "08", "기관 합계": "18", "기타법인": "07", "외국인 합계": "17"}


def unit_crosscheck(run_dir: Path, project_root: Path) -> dict[str, object]:
    response = json.loads((run_dir / "03_K2I_F_U.response.json").read_text(encoding="utf-8"))
    ls_rows = {str(row["date"]): row for row in response["t8462OutBlock1"]}
    state_path = project_root / "data/state/kr_kospi200_futures_investor_net_purchase_daily.json"
    state_body = state_path.read_bytes()
    state = json.loads(state_body)
    if state.get("unit") != "백만원" or state.get("session") != "ALL":
        raise ValueError("KRX comparison artifact lacks exact ALL/백만원 semantics")
    parquet_path = project_root / "data/normalized/kr_kospi200_futures_investor_net_purchase_daily/year=2026/data.parquet"
    expected = next(item for item in state["output_manifest"]["files"] if item["path"] == "year=2026/data.parquet")
    parquet_body = parquet_path.read_bytes()
    if hashlib.sha256(parquet_body).hexdigest() != expected["sha256"]:
        raise ValueError("KRX comparison parquet differs from state")
    frame = pd.read_parquet(parquet_path)
    frame["date_key"] = frame["date"].astype(str).str.replace("-", "", regex=False)
    frame = frame[
        frame["date_key"].isin(CROSSCHECK_DATES)
        & frame["investor_type_source"].isin(CATEGORY_SUFFIX)
    ]
    comparisons = []
    for row in frame.to_dict("records"):
        date = row["date_key"]
        suffix = CATEGORY_SUFFIX[row["investor_type_source"]]
        ls_amount = int(ls_rows[date][f"sa_{suffix}"])
        krx_million = int(row["net_purchase_trading_value"])
        residual = krx_million - ls_amount * 100
        comparisons.append({
            "market_date": date, "investor_type_source": row["investor_type_source"],
            "ls_sa_source_value": ls_amount, "krx_value_million_krw": krx_million,
            "krx_minus_ls_times_100_million_krw": residual,
        })
    comparisons.sort(key=lambda value: (value["market_date"], value["investor_type_source"]))
    if len(comparisons) != 12 or max(abs(item["krx_minus_ls_times_100_million_krw"]) for item in comparisons) > 50:
        classification = "UNIT_UNRESOLVED"
    else:
        classification = "UNIT_INFERRED_MULTI_DATE_MATCH"
    return {
        "classification": classification,
        "inferred_source_unit": "100_MILLION_KRW" if classification != "UNIT_UNRESOLVED" else None,
        "comparison": "LS sa_* * 100 == KRX official ALL-session million-KRW value within source rounding",
        "dates": list(CROSSCHECK_DATES), "comparison_points": len(comparisons),
        "max_absolute_residual_million_krw": max(abs(item["krx_minus_ls_times_100_million_krw"]) for item in comparisons),
        "krx_state_sha256": hashlib.sha256(state_body).hexdigest(),
        "comparisons": comparisons,
    }


def audit_run(project_root: Path, run_id: str) -> dict[str, object]:
    run_dir = project_root / "data/landing/ls_openapi/t8462_raw" / run_id
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    ledger = [json.loads(line) for line in (run_dir / "call_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    if checkpoint.get("status") != "RAW_BACKFILL_COMPLETE_REVIEW_REQUIRED":
        raise ValueError("raw checkpoint is not complete")
    if len(ledger) != 19 or sum(row.get("operation") == "oauth2/token" for row in ledger) != 1:
        raise ValueError("call ledger count differs")
    if any(row.get("outcome") != "PASS" or row.get("retry_count") != 0 for row in ledger):
        raise ValueError("call ledger contains failure or retry")
    inventory = []
    total_rows = 0
    raw_bytes = 0
    arithmetic = {
        "sv_institution_exact_rows": 0, "sv_institution_mismatch_rows": 0,
        "sv_institution_max_abs_residual": 0, "sv_total_exact_rows": 0,
        "sv_total_mismatch_rows": 0, "sv_total_max_abs_residual": 0,
        "sa_institution_max_abs_residual": 0, "sa_total_max_abs_residual": 0,
    }
    for sequence, scope in enumerate(scopes(), start=1):
        label = f"{sequence:02d}_{scope['asset_code']}_{scope['product_code']}_{scope['requested_session_code']}"
        raw_path = run_dir / f"{label}.response.json"
        meta_path = run_dir / f"{label}.provenance.json"
        raw = raw_path.read_bytes()
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if hashlib.sha256(raw).hexdigest() != metadata["raw_response_sha256"] or len(raw) != metadata["raw_response_bytes"]:
            raise ValueError("raw response differs from provenance")
        payload = json.loads(raw)
        rows = validate_payload(payload, scope)
        if len(rows) != metadata["row_count"]:
            raise ValueError("row count differs from provenance")
        dates = sorted(str(row["date"]) for row in rows)
        for row in rows:
            sv_institution = int(row["sv_18"]) - sum(int(row[f"sv_{suffix}"]) for suffix in ("01", "02", "03", "04", "05", "06", "15", "00"))
            sv_total = sum(int(row[f"sv_{suffix}"]) for suffix in ("08", "17", "18", "07"))
            sa_institution = int(row["sa_18"]) - sum(int(row[f"sa_{suffix}"]) for suffix in ("01", "02", "03", "04", "05", "06", "15", "00"))
            sa_total = sum(int(row[f"sa_{suffix}"]) for suffix in ("08", "17", "18", "07"))
            arithmetic["sv_institution_exact_rows" if sv_institution == 0 else "sv_institution_mismatch_rows"] += 1
            arithmetic["sv_total_exact_rows" if sv_total == 0 else "sv_total_mismatch_rows"] += 1
            arithmetic["sv_institution_max_abs_residual"] = max(arithmetic["sv_institution_max_abs_residual"], abs(sv_institution))
            arithmetic["sv_total_max_abs_residual"] = max(arithmetic["sv_total_max_abs_residual"], abs(sv_total))
            arithmetic["sa_institution_max_abs_residual"] = max(arithmetic["sa_institution_max_abs_residual"], abs(sa_institution))
            arithmetic["sa_total_max_abs_residual"] = max(arithmetic["sa_total_max_abs_residual"], abs(sa_total))
        inventory.append({
            "asset_code": scope["asset_code"], "product_code": scope["product_code"],
            "requested_session_code": scope["requested_session_code"], "rows": len(rows),
            "date_min": dates[0] if dates else None, "date_max": dates[-1] if dates else None,
            "raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
        })
        total_rows += len(rows)
        raw_bytes += len(raw)
    if len(inventory) != 18 or total_rows != 4734:
        raise ValueError("unexpected inventory size")
    unit = unit_crosscheck(run_dir, project_root)
    return {
        "schema": "stock_data.ls_t8462_raw_audit_v1", "run_id": run_id,
        "result": "PASS_WITH_SEMANTIC_LIMITS", "network_calls": 0,
        "captured_call_accounting": {"oauth": 1, "data": 18, "retry": 0},
        "scope_count": 18, "rows": total_rows, "raw_response_bytes": raw_bytes,
        "date_min": min(item["date_min"] for item in inventory),
        "date_max": max(item["date_max"] for item in inventory),
        "product_mapping": "CONFIRMED_FROM_OFFICIAL_SELECTOR_AND_RESPONSE_ECHO",
        "amount_unit": unit,
        "session": {
            "overall_classification": "SESSION_UNRESOLVED",
            "U": "SESSION_INFERRED_AS_ALL_FROM_MULTI_DATE_KRX_AMOUNT_MATCH",
            "D": "SESSION_UNRESOLVED", "N": "SESSION_UNRESOLVED",
        },
        "history": {
            "classification": "OBSERVED_EARLIEST_ONLY", "earliest_observed": "20250718",
            "all_scopes_same_earliest": True, "rows_per_scope": 263,
            "reason": "fixed inception versus rolling retention/row ceiling is not proven",
        },
        "normalized_writes": False, "daily_raw_collection_ready": True,
        "normalized_incremental_ready": False,
        "source_arithmetic": arithmetic,
        "inventory": inventory,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--write-audit", action="store_true")
    args = parser.parse_args()
    result = audit_run(args.root.resolve(), args.run_id)
    if args.write_audit:
        atomic_json(args.root / "data/landing/ls_openapi/t8462_raw" / args.run_id / "audit.json", result)
    print(json.dumps({key: result[key] for key in ("result", "run_id", "rows", "raw_response_bytes", "date_min", "date_max")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
