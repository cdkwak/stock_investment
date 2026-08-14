from pathlib import Path

from scripts.manual.audit_ls_t8462_semantics import INSTITUTION_DETAIL_FIELDS, RUN_ID, difference_rows


def test_actual_option_u_differences_are_complete_and_not_fixed_by_sv15():
    root = Path(__file__).resolve().parents[1]
    rows = difference_rows(root / "data/landing/ls_openapi/t8462_raw" / RUN_ID)
    assert INSTITUTION_DETAIL_FIELDS == (
        "sv_01", "sv_03", "sv_04", "sv_02", "sv_05", "sv_06", "sv_15", "sv_00"
    )
    assert len(rows) == 202
    assert sum(row["product_scope"] == "KOSPI200_CALL_U" for row in rows) == 100
    assert sum(row["product_scope"] == "KOSPI200_PUT_U" for row in rows) == 102
    assert all(row["sv_15_futures_quantity"] == 0 for row in rows)
    assert min(row["market_date"] for row in rows) == "20250718"
    assert max(row["market_date"] for row in rows) == "20251223"
