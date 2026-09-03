from stock_data.contracts.bok_ecos_fx import BOK_ECOS_USD_KRW_DAILY
from stock_data.contracts.registry import CONTRACTS


def test_bok_ecos_fx_contract_is_normalized_append_only_daily() -> None:
    contract = BOK_ECOS_USD_KRW_DAILY
    assert CONTRACTS[contract.name] is contract
    assert contract.layer == "normalized"
    assert contract.frequency == "daily"
    assert contract.primary_key == ("date",)
    assert contract.partition_by == ("year",)
    assert contract.column_names == (
        "date", "rate_krw_per_usd", "item_code", "stat_code", "unit",
        "source", "source_operation", "retrieved_at",
    )
