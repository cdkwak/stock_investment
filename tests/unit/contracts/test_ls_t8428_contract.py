from stock_data.contracts.ls_t8428 import LS_T8428_SURROUNDING_FUNDS_SOURCE_OBSERVATION
from stock_data.contracts.registry import CONTRACTS


def test_ls_t8428_contract_is_source_observation_only():
    contract = LS_T8428_SURROUNDING_FUNDS_SOURCE_OBSERVATION
    assert contract.name in CONTRACTS
    assert contract.status == "implementation_ready_source_observation_only"
    assert contract.primary_key == ("capture_id", "date")
    units = {column.name: column.unit for column in contract.columns}
    for name in ("customer_deposits", "receivables", "credit_balance", "futures_deposits"):
        assert units[name] == "krw_100_million"
    assert units["market_volume_source"] is None
    assert units["legacy_short_bond_filler_source"] is None
