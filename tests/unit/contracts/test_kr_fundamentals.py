from stock_data.contracts.kr_fundamentals import (
    KR_CORP_CODE_MAP,
    KR_FUNDAMENTALS_QUARTERLY,
    OPEN_DART_TERMS_URL,
)


def test_corp_code_map_contract_has_exact_identity_schema():
    assert KR_CORP_CODE_MAP.column_names == (
        "corp_code", "corp_name", "stock_code", "modify_date",
    )
    assert KR_CORP_CODE_MAP.primary_key == ("corp_code",)
    assert KR_CORP_CODE_MAP.frequency == "weekly"


def test_quarterly_contract_preserves_receipt_vintages_and_terms_reference():
    assert KR_FUNDAMENTALS_QUARTERLY.primary_key == (
        "symbol", "bsns_year", "reprt_code", "fs_div", "rcept_no",
    )
    assert "is_latest" not in KR_FUNDAMENTALS_QUARTERLY.column_names
    assert KR_FUNDAMENTALS_QUARTERLY.column_names[-2:] == (
        "retrieved_at", "source_terms_ref",
    )
    assert OPEN_DART_TERMS_URL == "https://opendart.fss.or.kr/intro/terms.do"
