from stock_data.contracts.registry import CONTRACTS
from stock_data.contracts.market_15m import MARKET_PRICE_15M_OBSERVATION


def test_contract_registry_has_unique_names_and_layer_formats() -> None:
    assert len(CONTRACTS)==len(set(CONTRACTS))
    assert all(contract.storage_format=="parquet" for contract in CONTRACTS.values())
    assert all(contract.layer in {"normalized","derived","published"} for contract in CONTRACTS.values())
    assert not any("label" in contract.column_names for contract in CONTRACTS.values())


def test_market_60m_contract_is_provider_specific_and_session_safe() -> None:
    contract = CONTRACTS["market_price_60m_observation"]
    assert contract.frequency == "intraday"
    assert contract.primary_key == ("provider", "symbol", "bar_start")
    assert {"session", "actual_duration_minutes", "fallback_used", "fallback_reason"} <= set(
        contract.column_names
    )


def test_market_15m_active_contract_is_delayed_and_timezone_explicit() -> None:
    contract = CONTRACTS[MARKET_PRICE_15M_OBSERVATION.name]
    assert contract.status == "active"
    assert contract.frequency == "intraday"
    assert contract.primary_key == ("provider", "series_id", "bar_start")
    assert {"source_timezone", "display_timezone", "data_availability"} <= set(
        contract.column_names
    )
    assert "not licensed realtime" in contract.description
