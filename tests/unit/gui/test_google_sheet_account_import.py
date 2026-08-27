import pytest

from stock_data.gui.google_sheet_account_import import parse_appa_sheet_csv


def _csv() -> str:
    rows = [
        "창대 주식 계좌 운용 결과(26.2.3)",
        "",
        "아빠 ISA 60%",
        "EFT,종목 티커,수량,평균단가,현재단가,구매총액,현재총액",
        "Fixture A,111111,2,100,999,200,1998",
        "",
        "아빠 종합 40%",
        "EFT,종목 티커,수량,평균단가,현재단가,구매총액,현재총액",
        "Fixture B,222222,1,,,",
    ]
    return "\n".join(rows) + "\n"


def test_appa_csv_maps_two_sections_and_ignores_current_price_columns():
    registry = parse_appa_sheet_csv(_csv())
    assert [(row.source_id, row.account_kind, row.snapshot_date) for row in registry.accounts] == [
        ("manual:appa_isa", "ISA", "2026-02-03"),
        ("manual:appa_general", "GENERAL", "2026-02-03"),
    ]
    first = registry.accounts[0].positions[0]
    assert (first.ticker, first.quantity, first.average_cost, first.purchase_total) == (
        "111111", 2.0, 100.0, 200.0,
    )
    assert not hasattr(first, "current_price")


def test_appa_csv_accepts_corrected_etf_header_spelling():
    registry = parse_appa_sheet_csv(_csv().replace("EFT,", "ETF,"))
    assert {account.source_id for account in registry.accounts} == {
        "manual:appa_isa", "manual:appa_general",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda text: text.replace("종목 티커", "계좌번호", 1),
        lambda text: text.replace("아빠 종합 40%", "다른 계좌"),
        lambda text: text.replace("Fixture A,111111,2,100,999,200", "Fixture A,111111,2,100,999,201"),
    ],
)
def test_appa_csv_fails_closed_on_schema_section_or_reconciliation(mutation):
    with pytest.raises(ValueError):
        parse_appa_sheet_csv(mutation(_csv()))
