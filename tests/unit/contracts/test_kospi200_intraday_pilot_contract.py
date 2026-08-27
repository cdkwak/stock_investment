from stock_data.contracts.kospi200_intraday_pilot import (
    LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT,
    RAW_BAR_TIME_POLICY,
    RAW_REVISION_POLICY,
)


def test_t8412_pilot_contract_preserves_unresolved_provider_time_semantics() -> None:
    contract = LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT
    assert contract.status == "review_required_not_registered"
    assert contract.layer == "raw"
    assert contract.primary_key == ("provider", "symbol", "market_date", "provider_time")
    assert "bar_start" not in contract.column_names
    provider_time = next(column for column in contract.columns if column.name == "provider_time")
    assert "start/end-bar meaning remains unresolved" in provider_time.description
    assert "bar_time_policy" in contract.column_names
    assert "revision_policy" in contract.column_names
    assert RAW_BAR_TIME_POLICY.endswith("START_END_UNKNOWN")
    assert RAW_REVISION_POLICY.endswith("REVISION_FREEZE_UNKNOWN")


def test_t8412_pilot_contract_does_not_claim_unverified_volume_units() -> None:
    volume = next(
        column
        for column in LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT.columns
        if column.name == "volume"
    )
    assert volume.unit == "provider_native_volume"
