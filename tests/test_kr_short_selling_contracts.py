from stock_data.contracts.kr_short_selling import (
    KR_SHORT_SELLING_BALANCE_DAILY, KR_SHORT_SELLING_INVESTOR_DAILY,
    KR_SHORT_SELLING_TRADING_DAILY,
)


def test_short_selling_contracts_remain_separate_and_blocked() -> None:
    contracts = (
        KR_SHORT_SELLING_TRADING_DAILY,
        KR_SHORT_SELLING_BALANCE_DAILY,
        KR_SHORT_SELLING_INVESTOR_DAILY,
    )
    assert all(item.status == "draft_blocked" for item in contracts)
    assert all(item.layer == "normalized" for item in contracts)
    assert all(item.partition_by == ("market", "year") for item in contracts)
    assert len({item.name for item in contracts}) == 3


def test_short_selling_source_and_derived_values_are_not_mixed() -> None:
    assert "short_balance_ratio" in KR_SHORT_SELLING_BALANCE_DAILY.column_names
    assert "metric" in KR_SHORT_SELLING_INVESTOR_DAILY.column_names
    assert "trading_value" not in KR_SHORT_SELLING_BALANCE_DAILY.column_names
