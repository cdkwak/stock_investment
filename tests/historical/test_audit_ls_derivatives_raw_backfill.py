from pathlib import Path

from scripts.manual.audit.audit_ls_derivatives_raw_backfill import audit_run, unit_crosscheck


RUN_ID = "20260814T165922Z_da488bc5fd024f559b0ef70f6d340e1f"


def test_actual_retained_raw_backfill_audits_without_network():
    root = Path(__file__).resolve().parents[2]
    result = audit_run(root, RUN_ID)
    assert result["result"] == "PASS_WITH_SEMANTIC_LIMITS"
    assert result["scope_count"] == 18
    assert result["rows"] == 4734
    assert result["date_min"] == "20250718"
    assert result["date_max"] == "20260814"
    assert result["history"]["classification"] == "OBSERVED_EARLIEST_ONLY"
    assert result["session"]["overall_classification"] == "SESSION_UNRESOLVED"
    assert result["source_arithmetic"]["sv_institution_exact_rows"] == 4532
    assert result["source_arithmetic"]["sv_institution_mismatch_rows"] == 202
    assert result["source_arithmetic"]["sv_institution_max_abs_residual"] == 4004


def test_unit_is_inferred_from_12_official_krx_comparisons():
    root = Path(__file__).resolve().parents[2]
    result = unit_crosscheck(root / "data/landing/ls_openapi/t8462_raw" / RUN_ID, root)
    assert result["classification"] == "UNIT_INFERRED_MULTI_DATE_MATCH"
    assert result["inferred_source_unit"] == "100_MILLION_KRW"
    assert result["comparison_points"] == 12
    assert result["max_absolute_residual_million_krw"] <= 50
