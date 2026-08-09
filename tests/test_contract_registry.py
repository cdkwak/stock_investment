from stock_data.contracts.registry import CONTRACTS


def test_contract_registry_has_unique_names_and_layer_formats() -> None:
    assert len(CONTRACTS)==len(set(CONTRACTS))
    assert all(contract.storage_format=="parquet" for contract in CONTRACTS.values())
    assert all(contract.layer in {"normalized","derived","published"} for contract in CONTRACTS.values())
    assert not any("label" in contract.column_names for contract in CONTRACTS.values())
