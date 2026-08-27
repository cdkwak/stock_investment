"""Raw-only contract and source policy for KRX equity fundamentals.

The official KRX menu identifies PER/PBR/dividend-yield information, but the
reviewed primary material does not establish MDCSTAT03501 publication timing,
revision behavior, or a freeze. Provider duplicates remain distinct source
rows whose ordinal is local to one retained response.
"""

from __future__ import annotations

from dataclasses import dataclass

from stock_data.contracts.base import ColumnContract, DatasetContract


@dataclass(frozen=True)
class FundamentalSourcePolicy:
    observation_calendar: str
    provider_availability_policy: str
    expected_lag_policy: str
    finality_policy: str
    revision_policy: str
    duplicate_policy: str
    finality_evidence_id: str | None
    execution_authorized: bool
    business_calls_max: int
    retries: int


@dataclass(frozen=True)
class BoundedFundamentalObservationPlan:
    completed_sessions: int
    observations_per_session: int
    business_calls_per_observation: int
    retries_per_observation: int
    total_business_calls_max: int
    landing_only: bool
    promotion_authorized: bool
    scheduler_authorized: bool
    compare_duplicate_groups: bool
    purpose: str


KR_EQUITY_FUNDAMENTAL_DAILY_RAW = DatasetContract(
    name="kr_equity_fundamental_daily",
    version=1,
    status="contract_only_raw_publication_revision_review_required",
    description=(
        "Lossless date-row observations from the KRX MDCSTAT03501 ALL-market "
        "response. A security code may occur more than once; source ordinal, "
        "rather than security code, is the retained row identity."
    ),
    source="krx_mdcstat03501_via_pykrx",
    layer="raw",
    storage_format="immutable_landing_json_plus_hash_bound_checkpoint",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("market_date", "source_row_ordinal"),
    sort_key=("market_date", "source_row_ordinal"),
    partition_by=("market_date",),
    columns=(
        ColumnContract("market_date", "date32", False, "XKRX_session_date"),
        ColumnContract("source_row_ordinal", "int32", False, "1_based_response_local"),
        ColumnContract("issue_short_code", "string", False),
        ColumnContract("issue_abbreviated_name", "string", False),
        ColumnContract("close_raw", "string", False, "provider_native_unasserted"),
        ColumnContract("eps_raw", "string", False, "provider_native_unasserted"),
        ColumnContract("per_raw", "string", False, "provider_native_unasserted"),
        ColumnContract("bps_raw", "string", False, "provider_native_unasserted"),
        ColumnContract("pbr_raw", "string", False, "provider_native_unasserted"),
        ColumnContract("dps_raw", "string", False, "provider_native_unasserted"),
        ColumnContract(
            "dividend_yield_raw", "string", False, "provider_native_unasserted"
        ),
        ColumnContract("provider_missing_token", "string", False, "literal_dash"),
        ColumnContract("landing_path", "string", False),
        ColumnContract("landing_sha256", "string", False, "sha256_hex"),
        ColumnContract("captured_at_utc", "timestamp[us, UTC]", False),
        ColumnContract(
            "duplicate_classification",
            "string",
            False,
            None,
            "Distinct provider rows are preserved; no deduplication authority.",
        ),
        ColumnContract(
            "publication_status",
            "string",
            False,
            None,
            "PROVIDER_PUBLICATION_UNDOCUMENTED until primary evidence closes it.",
        ),
        ColumnContract(
            "finality_status",
            "string",
            False,
            None,
            "REVIEW_REQUIRED_NO_OFFICIAL_FREEZE until primary evidence closes it.",
        ),
        ColumnContract(
            "revision_status",
            "string",
            False,
            None,
            "UNRESOLVED_HISTORICAL_VALUE_REVISION until reviewed.",
        ),
    ),
)


SOURCE_POLICY = FundamentalSourcePolicy(
    observation_calendar="XKRX",
    provider_availability_policy="PROVIDER_PUBLICATION_UNDOCUMENTED",
    expected_lag_policy="NOT_DEFINED",
    finality_policy="REVIEW_REQUIRED_NO_OFFICIAL_FREEZE",
    revision_policy="UNRESOLVED_HISTORICAL_VALUE_REVISION",
    duplicate_policy="PRESERVE_RESPONSE_LOCAL_SOURCE_ROW_ORDINAL_NO_COLLAPSE",
    finality_evidence_id=None,
    execution_authorized=False,
    business_calls_max=1,
    retries=0,
)


BOUNDED_OBSERVATION_PLAN = BoundedFundamentalObservationPlan(
    completed_sessions=3,
    observations_per_session=3,
    business_calls_per_observation=1,
    retries_per_observation=0,
    total_business_calls_max=9,
    landing_only=True,
    promotion_authorized=False,
    scheduler_authorized=False,
    compare_duplicate_groups=True,
    purpose=(
        "Compare each exact session after the official post-market boundary, "
        "before the next regular session, and again after five completed XKRX "
        "sessions. These are experimental windows, not availability or freeze claims."
    ),
)


__all__ = [
    "BOUNDED_OBSERVATION_PLAN",
    "BoundedFundamentalObservationPlan",
    "FundamentalSourcePolicy",
    "KR_EQUITY_FUNDAMENTAL_DAILY_RAW",
    "SOURCE_POLICY",
]
