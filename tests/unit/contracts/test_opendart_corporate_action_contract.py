from stock_data.contracts.opendart_corporate_action_intake import (
    KR_OPENDART_CORPORATE_ACTION_FILING_VERSION_OBSERVATION,
    KR_OPENDART_SECURITY_IDENTITY_OBSERVATION,
)


def test_identity_contract_keeps_effective_security_edges_nullable():
    contract = KR_OPENDART_SECURITY_IDENTITY_OBSERVATION
    assert contract.status == "contract_only_source_observation_pilot"
    columns = {column.name: column for column in contract.columns}
    for name in (
        "market", "security_class", "isin", "valid_from", "valid_to",
        "predecessor_security_id", "successor_security_id",
    ):
        assert columns[name].nullable is True


def test_filing_contract_requires_pit_fields_and_never_uses_receipt_as_event_id():
    contract = KR_OPENDART_CORPORATE_ACTION_FILING_VERSION_OBSERVATION
    names = {column.name for column in contract.columns}
    assert {"observation_time_utc", "available_at_utc", "usable_from"} <= names
    assert {"original_receipt_no", "revises_receipt_no", "revision_parent_status"} <= names
    assert "event_id" not in names
    assert contract.primary_key == (
        "source_operation", "landing_response_body_sha256", "source_item_ordinal",
    )
