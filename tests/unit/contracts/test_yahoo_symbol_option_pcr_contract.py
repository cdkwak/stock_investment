from stock_data.contracts.registry import CONTRACTS
from stock_data.contracts.yahoo_symbol_option_pcr import (
    YAHOO_SYMBOL_OPTION_CONTRACT_OBSERVATION,
    YAHOO_SYMBOL_OPTION_PCR_CONTRACTS,
    YAHOO_SYMBOL_OPTION_VOLUME_PCR_RESEARCH,
)


def test_yahoo_symbol_option_contracts_are_research_only_and_unregistered():
    assert len(YAHOO_SYMBOL_OPTION_PCR_CONTRACTS) == 2
    for contract in YAHOO_SYMBOL_OPTION_PCR_CONTRACTS:
        assert contract.status == "research_only_live_evidence_pending"
        assert contract.name not in CONTRACTS


def test_contract_observation_separates_provider_size_from_multiplier_verification():
    contract = YAHOO_SYMBOL_OPTION_CONTRACT_OBSERVATION
    assert contract.primary_key == (
        "symbol", "expiry_at_utc", "contract_symbol", "captured_at_utc",
    )
    assert {"contract_size", "multiplier_status", "volume", "captured_at_utc"} <= set(
        contract.column_names
    )


def test_derived_contract_is_per_symbol_research_not_market_or_backtest_history():
    contract = YAHOO_SYMBOL_OPTION_VOLUME_PCR_RESEARCH
    assert contract.primary_key[0] == "symbol"
    assert "volume_pcr" in contract.column_names
    assert "backtest_eligible" in contract.column_names
    assert "market_total" not in contract.column_names
    assert contract.source == YAHOO_SYMBOL_OPTION_CONTRACT_OBSERVATION.name
