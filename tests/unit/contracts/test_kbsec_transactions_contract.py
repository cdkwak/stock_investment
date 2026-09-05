from stock_data.contracts.kbsec_transactions import (
    KBSEC_TRANSACTIONS_DAILY,
    KBSecTransactionCategory,
    KBSecTransactionDirection,
)


def test_kbsec_transaction_contract_is_identifier_free_and_hash_keyed() -> None:
    contract = KBSEC_TRANSACTIONS_DAILY

    assert contract.name == "kbsec_transactions_daily"
    assert contract.layer == "local_user"
    assert contract.primary_key == ("raw_row_sha256",)
    assert contract.column_names == (
        "date", "direction", "category", "amount_krw", "tax_krw",
        "summary_name", "transaction_type_code", "summary_type_code",
        "raw_row_sha256",
    )
    assert not any(
        token in column.lower()
        for column in contract.column_names
        for token in ("account", "counterpart", "cprty", "name_of_holder")
    )
    assert {item.value for item in KBSecTransactionDirection} == {"IN", "OUT"}
    assert {item.value for item in KBSecTransactionCategory} == {
        "DEPOSIT", "WITHDRAWAL", "DIVIDEND", "TAX", "FEE", "OTHER",
    }
