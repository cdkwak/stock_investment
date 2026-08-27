import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[3] / "scripts/manual/audit/audit_toss_ls_t1633_quantity_missing.py"
SPEC = importlib.util.spec_from_file_location("toss_missing_forensic", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_404_and_empty_history_stay_nonzero_unresolved_categories():
    assert MODULE.classify(http_status=404, closest_date=None) == (
        "SYMBOL_MAPPING_ISSUE", "HTTP_404_STOCK_NOT_FOUND"
    )


def test_last_returned_date_stays_unresolved():
    assert MODULE.classify(http_status=200, closest_date="2024-12-30") == (
        "NO_EXACT_DATE_UNRESOLVED", "LAST_RETURNED_BEFORE_TARGET"
    )


def test_200_empty_response_is_not_claimed_as_supported_history():
    assert MODULE.toss_support_status(http_status=200, closest_date=None) == "HTTP_200_EMPTY_RECORDS_UNRESOLVED"
    assert MODULE.classify(http_status=200, closest_date=None) == (
        "NO_EXACT_DATE_UNRESOLVED", "EMPTY_RECORDS"
    )


def test_security_flags_do_not_infer_etf_or_reit_without_metadata_text():
    flags = MODULE.security_flags("테스트", "보통주")
    assert flags == {"common": True, "preferred": False, "etf": False, "etn": False, "spac": False, "reit": False}
