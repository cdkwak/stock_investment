from io import BytesIO
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from stock_data.providers.opendart_fundamentals import (
    OpenDartDailyLimitError,
    OpenDartFundamentalsError,
    OpenDartPeriodEndError,
    financial_statement_request,
    normalize_quarter,
    parse_corp_code_zip,
    parse_financial_statement,
)


def _corp_zip() -> bytes:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list><corp_code>00126380</corp_code><corp_name>테스트상장</corp_name><corp_eng_name>Test Listed</corp_eng_name><stock_code>005930</stock_code><modify_date>20260901</modify_date></list>
  <list><corp_code>00999999</corp_code><corp_name>테스트비상장</corp_name><corp_eng_name>Test Private</corp_eng_name><stock_code></stock_code><modify_date>20260831</modify_date></list>
</result>""".encode()
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("CORPCODE.xml", xml)
    return output.getvalue()


def _account(
    account_id: str,
    account_nm: str,
    sj_div: str,
    amount: str,
    *,
    add_amount: str = "",
    report: str = "11014",
    receipt: str = "20260901000001",
    bsns_year: str = "2025",
    thstrm_dt: str | None = None,
) -> dict[str, object]:
    return {
        "rcept_no": receipt,
        "reprt_code": report,
        "bsns_year": bsns_year,
        "corp_code": "00126380",
        "sj_div": sj_div,
        "sj_nm": "재무제표",
        "account_id": account_id,
        "account_nm": account_nm,
        "account_detail": "",
        "thstrm_nm": "제 10 기",
        "thstrm_dt": thstrm_dt,
        "thstrm_amount": amount,
        "thstrm_add_amount": add_amount,
        "frmtrm_nm": "제 9 기",
        "frmtrm_amount": "0",
        "frmtrm_q_nm": "제 9 기 3분기",
        "frmtrm_q_amount": "0",
        "frmtrm_add_amount": "0",
        "bfefrmtrm_nm": "",
        "bfefrmtrm_amount": "",
        "ord": "1",
        "currency": "KRW",
    }


def _payload(rows: list[dict[str, object]], status: str = "000") -> bytes:
    return json.dumps(
        {"status": status, "message": "정상" if status == "000" else "조회된 데이타가 없습니다.", "list": rows},
        ensure_ascii=False,
    ).encode()


def test_parse_small_synthetic_corp_code_zip_preserves_unlisted_null():
    rows = parse_corp_code_zip(_corp_zip())

    assert rows == [
        {"corp_code": "00126380", "corp_name": "테스트상장", "stock_code": "005930", "modify_date": "2026-09-01"},
        {"corp_code": "00999999", "corp_name": "테스트비상장", "stock_code": None, "modify_date": "2026-08-31"},
    ]


def test_corp_code_binary_error_status_020_is_a_daily_hard_stop():
    with pytest.raises(OpenDartDailyLimitError, match="status 020"):
        parse_corp_code_zip(b"<result><status>020</status><message>limit</message></result>")


def test_all_accounts_parser_binds_request_fs_div_and_uses_cis_when_is_absent():
    rows = [
        _account("ifrs-full_Revenue", "매출액", "CIS", "120", add_amount="330"),
        _account("dart_OperatingIncomeLoss", "영업이익", "CIS", "12", add_amount="30"),
        _account("ifrs-full_ProfitLoss", "당기순이익", "CIS", "9", add_amount="20"),
        _account("ifrs-full_Liabilities", "부채총계", "BS", "200"),
        _account("ifrs-full_Equity", "자본총계", "BS", "100"),
    ]

    classification, parsed = parse_financial_statement(
        _payload(rows), expected_corp_code="00126380", expected_year=2025,
        expected_report_code="11014", requested_fs_div="CFS",
    )
    normalized = normalize_quarter(
        symbol="005930", rows=parsed, retrieved_at="2026-09-01T00:00:00Z",
    )

    assert classification == "SUCCESS"
    assert {row["fs_div"] for row in parsed} == {"CFS"}
    assert normalized["revenue"] == 120
    assert normalized["operating_income"] == 12
    assert normalized["net_income"] == 9
    assert normalized["debt_ratio_pct"] == 200.0


def test_annual_q4_is_de_cumulated_from_q3_and_zero_equity_stays_null():
    q3 = [
        _account("ifrs-full_Revenue", "매출액", "IS", "120", add_amount="330"),
        _account("dart_OperatingIncomeLoss", "영업이익", "IS", "12", add_amount="30"),
        _account("ifrs-full_ProfitLoss", "당기순이익", "IS", "9", add_amount="20"),
    ]
    annual = [
        _account("ifrs-full_Revenue", "매출액", "IS", "500", report="11011", receipt="20260331000001"),
        _account("dart_OperatingIncomeLoss", "영업이익", "IS", "50", report="11011", receipt="20260331000001"),
        _account("ifrs-full_ProfitLoss", "당기순이익", "IS", "35", report="11011", receipt="20260331000001"),
        _account("ifrs-full_Liabilities", "부채총계", "BS", "200", report="11011", receipt="20260331000001"),
        _account("ifrs-full_Equity", "자본총계", "BS", "0", report="11011", receipt="20260331000001"),
    ]
    _, parsed_q3 = parse_financial_statement(
        _payload(q3), expected_corp_code="00126380", expected_year=2025,
        expected_report_code="11014", requested_fs_div="CFS",
    )
    _, parsed_annual = parse_financial_statement(
        _payload(annual), expected_corp_code="00126380", expected_year=2025,
        expected_report_code="11011", requested_fs_div="CFS",
    )

    normalized = normalize_quarter(
        symbol="005930", rows=parsed_annual, q3_rows=parsed_q3,
        retrieved_at="2026-04-01T00:00:00Z",
    )

    assert normalized["revenue"] == 170
    assert normalized["operating_income"] == 20
    assert normalized["net_income"] == 15
    assert normalized["total_liabilities"] == 200
    assert normalized["total_equity"] == 0
    assert normalized["debt_ratio_pct"] is None


def test_request_validation_is_bounded_to_official_codes():
    assert financial_statement_request("00126380", 2025, "11013", "CFS").public_parameters["fs_div"] == "CFS"
    with pytest.raises(OpenDartFundamentalsError):
        financial_statement_request("00126380", 2014, "11013", "CFS")


def test_period_end_uses_actual_response_period_for_june_fiscal_year_company():
    source = [_account(
        "ifrs-full_Revenue", "매출액", "IS", "120",
        report="11014", receipt="20260515000001", bsns_year="2026",
        thstrm_dt="2025.07.01 ~ 2026.03.31",
    )]
    _, rows = parse_financial_statement(
        _payload(source), expected_corp_code="00126380", expected_year=2026,
        expected_report_code="11014", requested_fs_div="CFS",
    )

    normalized = normalize_quarter(
        symbol="093240", rows=rows, retrieved_at="2026-05-15T00:00:00Z",
    )

    assert normalized["period_end"] == "2026-03-31"


def test_period_end_uses_actual_response_period_for_december_fiscal_year_company():
    source = [_account(
        "ifrs-full_Revenue", "매출액", "IS", "120",
        report="11012", receipt="20260814000001", bsns_year="2026",
        thstrm_dt="2026.01.01 ~ 2026.06.30",
    )]
    _, rows = parse_financial_statement(
        _payload(source), expected_corp_code="00126380", expected_year=2026,
        expected_report_code="11012", requested_fs_div="CFS",
    )

    normalized = normalize_quarter(
        symbol="005930", rows=rows, retrieved_at="2026-08-14T00:00:00Z",
    )

    assert normalized["period_end"] == "2026-06-30"


def test_normalizer_rejects_period_end_later_than_receipt_date():
    source = [_account(
        "ifrs-full_Revenue", "매출액", "IS", "120",
        report="11014", receipt="20260515000001", bsns_year="2026",
        thstrm_dt="제 57 기 3분기말 2026.09.30 현재",
    )]
    _, rows = parse_financial_statement(
        _payload(source), expected_corp_code="00126380", expected_year=2026,
        expected_report_code="11014", requested_fs_div="CFS",
    )

    with pytest.raises(OpenDartPeriodEndError) as raised:
        normalize_quarter(
            symbol="093240", rows=rows, retrieved_at="2026-05-15T00:00:00Z",
        )

    assert raised.value.reason == "PERIOD_END_AFTER_RECEIPT_DATE"
