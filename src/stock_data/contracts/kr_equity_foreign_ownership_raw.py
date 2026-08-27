"""Raw-only contract and source policy for KRX foreign ownership.

The KRX menu establishes the economic subject of MDCSTAT03701, but current
official material does not establish the endpoint publication clock, revision
window, or a correction freeze.  Consequently this module is descriptive only:
it grants no provider-call, normalization, promotion, predictive-use, or
scheduler authority.
"""

from __future__ import annotations

from dataclasses import dataclass

from stock_data.contracts.base import ColumnContract, DatasetContract


@dataclass(frozen=True)
class ForeignOwnershipSourcePolicy:
    observation_calendar: str
    provider_availability_policy: str
    expected_lag_policy: str
    finality_policy: str
    revision_policy: str
    finality_evidence_id: str | None
    execution_authorized: bool
    business_calls_max: int
    retries: int


@dataclass(frozen=True)
class BoundedFinalityObservationPlan:
    completed_sessions: int
    observations_per_session: int
    business_calls_per_observation: int
    retries_per_observation: int
    total_business_calls_max: int
    landing_only: bool
    promotion_authorized: bool
    scheduler_authorized: bool
    purpose: str


KR_EQUITY_FOREIGN_OWNERSHIP_DAILY_RAW = DatasetContract(
    name="kr_equity_foreign_ownership_daily",
    version=1,
    status="contract_only_raw_publication_finality_review_required",
    description=(
        "Lossless date-security rows from the KRX MDCSTAT03701 ALL-market "
        "response. Numeric provider text remains unnormalized and its units "
        "are not asserted by this Raw-only boundary."
    ),
    source="krx_mdcstat03701_via_pykrx",
    layer="raw",
    storage_format="immutable_landing_json_plus_hash_bound_checkpoint",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("market_date", "issue_short_code"),
    sort_key=("market_date", "issue_short_code"),
    partition_by=("market_date",),
    columns=(
        ColumnContract("market_date", "date32", False, "XKRX_session_date"),
        ColumnContract("issue_short_code", "string", False),
        ColumnContract("listed_shares_raw", "string", False, "provider_native_unasserted"),
        ColumnContract(
            "foreign_holding_quantity_raw",
            "string",
            False,
            "provider_native_unasserted",
        ),
        ColumnContract(
            "foreign_share_ratio_raw",
            "string",
            False,
            "provider_native_unasserted",
        ),
        ColumnContract(
            "foreign_order_limit_quantity_raw",
            "string",
            False,
            "provider_native_unasserted",
        ),
        ColumnContract(
            "foreign_limit_exhaustion_ratio_raw",
            "string",
            False,
            "provider_native_unasserted",
        ),
        ColumnContract("source_row_ordinal", "int32", False, "1_based_ordinal"),
        ColumnContract("landing_path", "string", False),
        ColumnContract("landing_sha256", "string", False, "sha256_hex"),
        ColumnContract("captured_at_utc", "timestamp[us, UTC]", False),
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
            "UNRESOLVED_NO_OFFICIAL_CORRECTION_WINDOW until reviewed.",
        ),
    ),
)


SOURCE_POLICY = ForeignOwnershipSourcePolicy(
    observation_calendar="XKRX",
    provider_availability_policy="PROVIDER_PUBLICATION_UNDOCUMENTED",
    expected_lag_policy="NOT_DEFINED",
    finality_policy="REVIEW_REQUIRED_NO_OFFICIAL_FREEZE",
    revision_policy="UNRESOLVED_NO_OFFICIAL_CORRECTION_WINDOW",
    finality_evidence_id=None,
    execution_authorized=False,
    business_calls_max=1,
    retries=0,
)


BOUNDED_FINALITY_OBSERVATION_PLAN = BoundedFinalityObservationPlan(
    completed_sessions=3,
    observations_per_session=2,
    business_calls_per_observation=1,
    retries_per_observation=0,
    total_business_calls_max=6,
    landing_only=True,
    promotion_authorized=False,
    scheduler_authorized=False,
    purpose=(
        "Compare separately authorized immutable responses after the official "
        "post-market boundary and before the next regular session; observation "
        "windows are experimental and do not assert provider availability."
    ),
)


__all__ = [
    "BOUNDED_FINALITY_OBSERVATION_PLAN",
    "BoundedFinalityObservationPlan",
    "ForeignOwnershipSourcePolicy",
    "KR_EQUITY_FOREIGN_OWNERSHIP_DAILY_RAW",
    "SOURCE_POLICY",
]
