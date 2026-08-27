from stock_data.contracts.kr_equity_fundamental_raw import (
    BOUNDED_OBSERVATION_PLAN,
    KR_EQUITY_FUNDAMENTAL_DAILY_RAW,
    SOURCE_POLICY,
)
from stock_data.contracts.registry import CONTRACTS


def test_fundamental_contract_is_raw_only_and_unregistered() -> None:
    contract = KR_EQUITY_FUNDAMENTAL_DAILY_RAW
    assert contract.name not in CONTRACTS
    assert contract.status == "contract_only_raw_publication_revision_review_required"
    assert contract.layer == "raw"
    assert contract.source == "krx_mdcstat03501_via_pykrx"
    assert contract.primary_key == ("market_date", "source_row_ordinal")
    assert contract.partition_by == ("market_date",)


def test_response_local_ordinal_preserves_provider_duplicate_rows() -> None:
    contract = KR_EQUITY_FUNDAMENTAL_DAILY_RAW
    columns = {column.name: column for column in contract.columns}
    assert columns["source_row_ordinal"].unit == "1_based_response_local"
    assert "issue_short_code" not in contract.primary_key
    assert columns["duplicate_classification"].description == (
        "Distinct provider rows are preserved; no deduplication authority."
    )


def test_numeric_text_and_dash_missing_token_are_not_normalized() -> None:
    columns = {
        column.name: column for column in KR_EQUITY_FUNDAMENTAL_DAILY_RAW.columns
    }
    for field in (
        "close_raw",
        "eps_raw",
        "per_raw",
        "bps_raw",
        "pbr_raw",
        "dps_raw",
        "dividend_yield_raw",
    ):
        assert columns[field].dtype == "string"
        assert columns[field].unit == "provider_native_unasserted"
    assert columns["provider_missing_token"].unit == "literal_dash"
    assert not ({"available_at", "revision_id", "normalized_value"} & set(columns))


def test_default_source_policy_is_typed_and_non_executable() -> None:
    assert SOURCE_POLICY.observation_calendar == "XKRX"
    assert SOURCE_POLICY.provider_availability_policy == (
        "PROVIDER_PUBLICATION_UNDOCUMENTED"
    )
    assert SOURCE_POLICY.expected_lag_policy == "NOT_DEFINED"
    assert SOURCE_POLICY.finality_policy == "REVIEW_REQUIRED_NO_OFFICIAL_FREEZE"
    assert SOURCE_POLICY.revision_policy == "UNRESOLVED_HISTORICAL_VALUE_REVISION"
    assert SOURCE_POLICY.duplicate_policy == (
        "PRESERVE_RESPONSE_LOCAL_SOURCE_ROW_ORDINAL_NO_COLLAPSE"
    )
    assert SOURCE_POLICY.finality_evidence_id is None
    assert SOURCE_POLICY.execution_authorized is False
    assert SOURCE_POLICY.business_calls_max == 1
    assert SOURCE_POLICY.retries == 0


def test_observation_plan_is_bounded_and_non_authoritative() -> None:
    plan = BOUNDED_OBSERVATION_PLAN
    assert plan.completed_sessions == 3
    assert plan.observations_per_session == 3
    assert plan.business_calls_per_observation == 1
    assert plan.retries_per_observation == 0
    assert plan.total_business_calls_max == 9
    assert plan.landing_only is True
    assert plan.promotion_authorized is False
    assert plan.scheduler_authorized is False
    assert plan.compare_duplicate_groups is True
    assert "not availability or freeze claims" in plan.purpose
