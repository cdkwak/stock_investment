"""Offline-only semantic audit for retained LS t8462 option-U rows."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = "20260814T165922Z_da488bc5fd024f559b0ef70f6d340e1f"
INSTITUTION_DETAIL_FIELDS = (
    "sv_01", "sv_03", "sv_04", "sv_02", "sv_05", "sv_06", "sv_15", "sv_00"
)
TARGETS = (
    ("KOSPI200_CALL_U", "06_K2I_C_U.response.json"),
    ("KOSPI200_PUT_U", "09_K2I_P_U.response.json"),
)


def difference_rows(run_dir: Path) -> list[dict[str, object]]:
    output = []
    for product, filename in TARGETS:
        payload = json.loads((run_dir / filename).read_text(encoding="utf-8"))
        for row in payload["t8462OutBlock1"]:
            detail_values = {field: int(row[field]) for field in INSTITUTION_DETAIL_FIELDS}
            detail_sum = sum(detail_values.values())
            institution_total = int(row["sv_18"])
            difference = institution_total - detail_sum
            if difference:
                output.append({
                    "product_scope": product,
                    "market_date": str(row["date"]),
                    "institution_total_sv_18": institution_total,
                    "detail_fields": "|".join(INSTITUTION_DETAIL_FIELDS),
                    "detail_values": "|".join(str(detail_values[field]) for field in INSTITUTION_DETAIL_FIELDS),
                    "detail_sum": detail_sum,
                    "difference_sv_18_minus_detail_sum": difference,
                    "sv_15_futures_quantity": int(row["sv_15"]),
                    "classification": "OPTION_SPECIFIC_SEMANTICS",
                })
    output.sort(key=lambda value: (value["product_scope"], value["market_date"]))
    return output


def write_report(project_root: Path, rows: list[dict[str, object]]) -> Path:
    target = project_root / "docs/providers/LS_T8462_OPTION_U_INSTITUTION_DIFFERENCES.csv"
    fields = list(rows[0])
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    run_dir = args.root / "data/landing/ls_openapi/t8462_raw" / args.run_id
    rows = difference_rows(run_dir)
    if len(rows) != 202 or any(row["sv_15_futures_quantity"] != 0 for row in rows):
        raise ValueError("retained mismatch evidence differs")
    if args.write_report:
        write_report(args.root, rows)
    print(json.dumps({
        "classification": "OPTION_SPECIFIC_SEMANTICS", "mismatch_rows": len(rows),
        "call_rows": sum(row["product_scope"] == "KOSPI200_CALL_U" for row in rows),
        "put_rows": sum(row["product_scope"] == "KOSPI200_PUT_U" for row in rows),
        "sv_15_nonzero_rows": sum(row["sv_15_futures_quantity"] != 0 for row in rows),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
