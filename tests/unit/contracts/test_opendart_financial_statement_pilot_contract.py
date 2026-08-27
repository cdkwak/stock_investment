from stock_data.contracts.opendart_financial_statement_pilot import (
    KR_OPENDART_FINANCIAL_STATEMENT_FILING_OBSERVATION,
)


def test_financial_statement_pilot_is_raw_only_and_pit_blocked():
    contract = KR_OPENDART_FINANCIAL_STATEMENT_FILING_OBSERVATION
    columns = {column.name: column for column in contract.columns}
    assert contract.status == "raw_only_approval_gated_pilot"
    assert contract.layer == "raw"
    assert contract.primary_key == (
        "source_operation", "landing_response_body_sha256", "source_item_ordinal",
    )
    assert columns["current_term_amount_raw"].dtype == "string"
    assert columns["provider_published_at_utc"].nullable is True
    assert columns["usable_from"].nullable is True
    assert {"pit_status", "redistribution_status", "revision_status"} <= set(columns)
