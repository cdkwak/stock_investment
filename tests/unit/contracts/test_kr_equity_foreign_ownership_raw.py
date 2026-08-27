from stock_data.contracts.kr_equity_foreign_ownership_raw import (
    BOUNDED_FINALITY_OBSERVATION_PLAN,
    KR_EQUITY_FOREIGN_OWNERSHIP_DAILY_RAW,
    SOURCE_POLICY,
)
from stock_data.contracts.registry import CONTRACTS


def test_foreign_ownership_contract_is_raw_only_and_unregistered() -> None:
    contract = KR_EQUITY_FOREIGN_OWNERSHIP_DAILY_RAW
    assert contract.name not in CONTRACTS
    assert contract.status == "contract_only_raw_publication_finality_review_required"
    assert contract.layer == "raw"
    assert contract.source == "krx_mdcstat03701_via_pykrx"
    assert contract.primary_key == ("market_date", "issue_short_code")
    assert contract.partition_by == ("market_date",)


def test_numeric_provider_text_has_no_inferred_unit_or_normalized_field() -> None:
    contract = KR_EQUITY_FOREIGN_OWNERSHIP_DAILY_RAW
    columns = {column.name: column for column in contract.columns}
    raw_numeric_fields = {
        "listed_shares_raw",
        "foreign_holding_quantity_raw",
        "foreign_share_ratio_raw",
        "foreign_order_limit_quantity_raw",
        "foreign_limit_exhaustion_ratio_raw",
    }
    for field in raw_numeric_fields:
        assert columns[field].dtype == "string"
        assert columns[field].unit == "provider_native_unasserted"
    assert not ({"available_at", "revision_id", "normalized_value"} & set(columns))


def test_default_source_policy_cannot_authorize_a_call() -> None:
    assert SOURCE_POLICY.observation_calendar == "XKRX"
    assert SOURCE_POLICY.provider_availability_policy == (
        "PROVIDER_PUBLICATION_UNDOCUMENTED"
    )
    assert SOURCE_POLICY.expected_lag_policy == "NOT_DEFINED"
    assert SOURCE_POLICY.finality_policy == "REVIEW_REQUIRED_NO_OFFICIAL_FREEZE"
    assert SOURCE_POLICY.finality_evidence_id is None
    assert SOURCE_POLICY.execution_authorized is False
    assert SOURCE_POLICY.business_calls_max == 1
    assert SOURCE_POLICY.retries == 0


def test_observation_plan_is_bounded_and_grants_no_operation_authority() -> None:
    plan = BOUNDED_FINALITY_OBSERVATION_PLAN
    assert plan.completed_sessions == 3
    assert plan.observations_per_session == 2
    assert plan.business_calls_per_observation == 1
    assert plan.retries_per_observation == 0
    assert plan.total_business_calls_max == 6
    assert plan.landing_only is True
    assert plan.promotion_authorized is False
    assert plan.scheduler_authorized is False
    assert "do not assert provider availability" in plan.purpose
