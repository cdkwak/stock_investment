from stock_data.contracts.registry import CONTRACTS
from stock_data.contracts.us_option_pcr import (
    CBOE_DAILY_PCR_DAILY,
    CBOE_DAILY_OPTION_PCR_OBSERVATION,
    CBOE_PUT_CALL_SCOPE_POLICIES,
    DASHBOARD_US_OPTION_PCR_DAILY,
    ORATS_OPTION_CORE_OBSERVATION,
    US_OPTION_PCR_CONTRACTS,
    US_UNDERLYING_OPTION_PCR_DAILY,
)


def test_personal_cboe_daily_contract_is_registered_with_exact_put_call_fields() -> None:
    contract = CBOE_DAILY_PCR_DAILY
    assert CONTRACTS[contract.name] is contract
    assert contract.status == "active_personal_display_only"
    assert contract.primary_key == ("date", "scope")
    assert contract.column_names == (
        "date", "scope", "call_volume", "put_volume", "volume_pcr",
        "call_oi", "put_oi", "oi_pcr", "provider", "retrieved_at",
    )
    columns = {column.name: column for column in contract.columns}
    assert columns["volume_pcr"].nullable is True
    assert columns["oi_pcr"].nullable is True


def test_cboe_daily_scope_contract_is_license_blocked_and_unregistered() -> None:
    contract = CBOE_DAILY_OPTION_PCR_OBSERVATION
    assert contract.name not in CONTRACTS
    assert contract.status == "contract_only_license_blocked"
    assert contract.layer == "normalized"
    assert contract.primary_key == ("trade_date", "scope_id", "captured_at_utc")
    assert "published_volume_pcr" in contract.column_names
    assert "open_interest_pcr" not in contract.column_names
    assert {"source_scope_status", "finality_status", "usage_status", "pit_status"} <= set(
        contract.column_names
    )


def test_cboe_daily_scope_policies_preserve_six_non_substitutable_categories() -> None:
    assert tuple(CBOE_PUT_CALL_SCOPE_POLICIES) == (
        "CBOE_TOTAL", "CBOE_INDEX", "CBOE_ETP", "CBOE_EQUITY", "CBOE_VIX",
        "CBOE_SPX_SPXW",
    )
    assert "not the entire U.S. options market" in (
        CBOE_PUT_CALL_SCOPE_POLICIES["CBOE_TOTAL"].meaning
    )
    assert CBOE_PUT_CALL_SCOPE_POLICIES["CBOE_VIX"].parent_scope_id == "CBOE_INDEX"
    assert CBOE_PUT_CALL_SCOPE_POLICIES["CBOE_SPX_SPXW"].official_page_label == (
        "SPX + SPXW PUT/CALL RATIO"
    )
    etp_meaning = CBOE_PUT_CALL_SCOPE_POLICIES["CBOE_ETP"].meaning
    assert "not QQQ or SOXX specifically" in etp_meaning


def test_us_option_pcr_contracts_remain_unregistered_drafts_without_entitlement() -> None:
    assert len(US_OPTION_PCR_CONTRACTS) == 3
    for contract in US_OPTION_PCR_CONTRACTS:
        assert contract.name not in CONTRACTS
        assert contract.status == "contract_only_no_entitlement"
        assert contract.storage_format == "parquet"
        assert contract.timezone == "America/New_York"


def test_orats_observation_preserves_each_capture_as_normalized_evidence() -> None:
    contract = ORATS_OPTION_CORE_OBSERVATION
    assert contract.layer == "normalized"
    assert contract.primary_key == (
        "trade_date", "provider_ticker", "captured_at_utc",
    )
    assert contract.partition_by == ("provider_ticker", "year")
    assert {
        "call_volume", "put_volume", "call_open_interest", "put_open_interest",
        "provider_updated_at_utc", "provider_snapshot_at_utc", "captured_at_utc",
        "landing_sha256", "observation_status",
    } <= set(contract.column_names)
    assert "volume_pcr" not in contract.column_names
    assert "open_interest_pcr" not in contract.column_names


def test_provider_neutral_pcr_contract_has_explicit_availability_and_pit() -> None:
    contract = US_UNDERLYING_OPTION_PCR_DAILY
    assert contract.layer == "derived"
    assert contract.primary_key == ("trade_date", "underlying", "scope", "session")
    assert "provider" not in contract.primary_key
    assert contract.partition_by == ("underlying", "year")
    assert {
        "provider", "provider_ticker", "volume_pcr", "open_interest_pcr",
        "root_scope_status",
        "volume_finality_status", "open_interest_timing_status",
        "selected_capture_at_utc", "available_at_utc", "revision_status",
        "observation_status", "pit_status", "input_dataset", "landing_sha256",
    } <= set(contract.column_names)
    assert not ({"cVolu", "pVolu", "cOi", "pOi"} & set(contract.column_names))
    columns = {column.name: column for column in contract.columns}
    assert columns["root_scope_status"].dtype == "string"
    assert columns["root_scope_status"].nullable is False
    assert contract.column_names.index("root_scope_status") == (
        contract.column_names.index("scope") + 1
    )


def test_dashboard_projection_keeps_static_evidence_not_dynamic_freshness() -> None:
    contract = DASHBOARD_US_OPTION_PCR_DAILY
    assert contract.layer == "published"
    assert contract.source == US_UNDERLYING_OPTION_PCR_DAILY.name
    assert contract.primary_key == US_UNDERLYING_OPTION_PCR_DAILY.primary_key
    assert {
        "call_volume", "put_volume", "volume_pcr", "call_open_interest",
        "put_open_interest", "open_interest_pcr", "available_at_utc",
        "volume_finality_status", "open_interest_timing_status", "pit_status",
        "provider", "input_dataset", "landing_sha256",
        "root_scope_status",
        "entitlement_status", "display_status", "blocked_reason",
        "projection_version",
    } <= set(contract.column_names)
    assert "freshness" not in contract.column_names
    columns = {column.name: column for column in contract.columns}
    assert columns["root_scope_status"].dtype == "string"
    assert columns["root_scope_status"].nullable is False
    assert contract.column_names.index("root_scope_status") == (
        contract.column_names.index("scope") + 1
    )
    assert columns["entitlement_status"].dtype == "string"
    assert columns["entitlement_status"].nullable is False
    assert columns["display_status"].dtype == "string"
    assert columns["display_status"].nullable is False
    assert columns["blocked_reason"].dtype == "string"
    assert columns["blocked_reason"].nullable is True
    assert columns["projection_version"].dtype == "string"
    assert columns["projection_version"].nullable is False
