from stock_data.contracts.availability import SNAPSHOT_EVENT_AVAILABILITY
from stock_data.contracts.data_v1 import KR_EQUITY_DIVIDEND, KR_EQUITY_RIGHTS_SCHEDULE
from stock_data.contracts.kr_equity import KR_EQUITY_MASTER


def test_snapshot_event_semantics_cover_risk_datasets_without_schema_changes():
    contracts = {
        contract.name: contract
        for contract in (KR_EQUITY_DIVIDEND, KR_EQUITY_RIGHTS_SCHEDULE, KR_EQUITY_MASTER)
    }
    assert set(SNAPSHOT_EVENT_AVAILABILITY) == set(contracts)
    for name, semantics in SNAPSHOT_EVENT_AVAILABILITY.items():
        contract = contracts[name]
        assert semantics.source_snapshot_field in contract.column_names
        assert set(semantics.event_effective_fields) <= set(contract.column_names)
        assert semantics.announcement_field is None


def test_event_effective_date_is_not_assumed_to_be_knowledge_date():
    for semantics in SNAPSHOT_EVENT_AVAILABILITY.values():
        assert semantics.source_snapshot_field not in semantics.event_effective_fields
        assert "source" in semantics.predictive_available_from
