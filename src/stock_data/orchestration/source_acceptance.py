"""Offline acceptance gates for source-closure work.

These functions classify retained evidence.  They contain no network client,
do not write state, and never turn source identity into operation authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping, Sequence

from stock_data.orchestration.recovery_supervisor import (
    PromotionOutcome,
    promote_outputs_atomically,
)
from stock_data.providers.opendart_free_issue import parse_observations


class SourceAcceptanceError(ValueError):
    """Raised when retained evidence cannot satisfy a fail-closed contract."""


@dataclass(frozen=True)
class SourceAcceptanceDecision:
    operational_status: str
    predictive_status: str
    authorized_for_live_call: bool
    reason: str


class CorporateActionSourceFamily(str, Enum):
    """Semantically independent corporate-action source families."""

    BONUS_PAID_FREE = "BONUS_PAID_FREE"
    CAPITAL_REDUCTION = "CAPITAL_REDUCTION"
    MERGER = "MERGER"
    COMPANY_DIVISION = "COMPANY_DIVISION"
    SHARE_SPLIT = "SHARE_SPLIT"
    TICKER_IDENTITY = "TICKER_IDENTITY"
    CASH_DIVIDEND = "CASH_DIVIDEND"


@dataclass(frozen=True)
class CorporateActionSourceAcceptance:
    """Acceptance of immutable source observations, never canonical events."""

    family: CorporateActionSourceFamily
    scope_id: str
    scope_end: date
    response_sha256: Mapping[str, str]
    source_status: str
    canonical_status: str
    factor_status: str
    api_zero_replay: bool
    authorized_for_live_call: bool = False


@dataclass(frozen=True)
class BonusFreeIssueFactorEvidence:
    """Source evidence required before a bonus issue may enter factor review.

    This is deliberately stricter than the OpenDART response schema. Provider
    buckets such as ``ordinary``/``other`` and a filing correction marker are
    observations, not an exact security class or an explicit revision edge.
    """

    source_event_version_id: str
    source_version_number: int | None
    revision_parent_source_event_version_id: str | None
    revision_parent_status: str
    source_revision_indicator: str | None
    security_id: str | None
    security_id_scheme: str | None
    security_class: str | None
    record_date: date | None
    ex_date: date | None
    effective_date: date | None
    effective_date_rule_status: str
    finality: str
    new_shares: int | None
    pre_issue_shares: int | None
    eligible_existing_shares: int | None
    allocation_per_existing_share: Decimal | None
    par_value_krw: Decimal | None
    fractional_share_policy: str | None
    action_scope: str
    combined_paid_issue_terms_complete: bool
    combined_sequence_status: str


@dataclass(frozen=True)
class BonusFreeIssueFactorDecision:
    """Fail-closed source-semantic decision; never a computed factor."""

    evidence_status: str
    factor_status: str
    missing_evidence: tuple[str, ...]
    authorized_for_promotion: bool = False


def evaluate_bonus_free_issue_factor_evidence(
    evidence: BonusFreeIssueFactorEvidence,
) -> BonusFreeIssueFactorDecision:
    """Require explicit revision, class, date, finality, and economic evidence.

    A passing result only permits later canonical-event review. This function
    neither calculates a factor nor grants normalization/promotion authority.
    """

    missing: list[str] = []

    if (
        not isinstance(evidence.source_event_version_id, str)
        or not evidence.source_event_version_id.strip()
    ):
        missing.append("source_event_version_id")
    version = evidence.source_version_number
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        missing.append("explicit_source_version_number")
    elif version == 1:
        if (
            evidence.revision_parent_source_event_version_id is not None
            or evidence.revision_parent_status != "NOT_APPLICABLE_CONFIRMED_ORIGINAL"
            or evidence.source_revision_indicator not in {None, ""}
        ):
            missing.append("confirmed_original_revision_state")
    elif (
        not isinstance(evidence.revision_parent_source_event_version_id, str)
        or not evidence.revision_parent_source_event_version_id.strip()
        or evidence.revision_parent_status != "VERIFIED_EXPLICIT"
    ):
        missing.append("explicit_revision_parent")

    if not evidence.security_id or not evidence.security_id_scheme:
        missing.append("exact_security_identifier")
    if not evidence.security_class:
        missing.append("exact_security_class")

    for field in ("record_date", "ex_date", "effective_date"):
        value = getattr(evidence, field)
        if not isinstance(value, date) or isinstance(value, datetime):
            missing.append(field)
    if evidence.effective_date_rule_status != "VERIFIED_OFFICIAL_ACTION_SPECIFIC":
        missing.append("verified_ex_effective_date_rule")
    if (
        isinstance(evidence.ex_date, date)
        and not isinstance(evidence.ex_date, datetime)
        and isinstance(evidence.effective_date, date)
        and not isinstance(evidence.effective_date, datetime)
        and evidence.ex_date != evidence.effective_date
    ):
        missing.append("ex_effective_date_mismatch")
    if evidence.finality != "VERIFIED_FINAL":
        missing.append("verified_finality")

    share_terms = {
        "new_shares": evidence.new_shares,
        "pre_issue_shares": evidence.pre_issue_shares,
        "eligible_existing_shares": evidence.eligible_existing_shares,
    }
    for field, value in share_terms.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            missing.append(field)
    ratio = evidence.allocation_per_existing_share
    if not isinstance(ratio, Decimal) or not ratio.is_finite() or ratio <= 0:
        missing.append("allocation_per_existing_share")
    par = evidence.par_value_krw
    if not isinstance(par, Decimal) or not par.is_finite() or par <= 0:
        missing.append("par_value_krw")
    if not evidence.fractional_share_policy:
        missing.append("fractional_share_policy")
    if (
        isinstance(evidence.new_shares, int)
        and not isinstance(evidence.new_shares, bool)
        and isinstance(evidence.eligible_existing_shares, int)
        and not isinstance(evidence.eligible_existing_shares, bool)
        and isinstance(ratio, Decimal)
        and ratio.is_finite()
        and Decimal(evidence.eligible_existing_shares) * ratio
        != Decimal(evidence.new_shares)
    ):
        missing.append("eligible_share_reconciliation")

    if evidence.action_scope not in {"STANDALONE_BONUS", "COMBINED_PAID_FREE"}:
        missing.append("action_scope")
    elif evidence.action_scope == "COMBINED_PAID_FREE":
        if not evidence.combined_paid_issue_terms_complete:
            missing.append("complete_paid_issue_terms")
        if evidence.combined_sequence_status != "VERIFIED_EXPLICIT":
            missing.append("explicit_paid_free_sequence")
    elif evidence.combined_sequence_status != "NOT_APPLICABLE":
        missing.append("standalone_sequence_status")

    blockers = tuple(dict.fromkeys(missing))
    if blockers:
        return BonusFreeIssueFactorDecision(
            evidence_status="SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE",
            factor_status="FACTOR_BLOCKED",
            missing_evidence=blockers,
        )
    return BonusFreeIssueFactorDecision(
        evidence_status="FACTOR_SOURCE_EVIDENCE_COMPLETE",
        factor_status="ELIGIBLE_FOR_CANONICAL_EVENT_REVIEW_ONLY",
        missing_evidence=(),
    )


@dataclass(frozen=True)
class CapitalReductionFactorEvidence:
    """Evidence required before one capital reduction may enter factor review."""

    immutable_source_observation_status: str
    source_event_version_id: str | None
    source_version_number: int | None
    revision_parent_source_event_version_id: str | None
    revision_parent_status: str
    source_revision_indicator: str | None
    security_id: str | None
    security_id_scheme: str | None
    security_class: str | None
    before_shares: int | None
    after_shares: int | None
    reduced_shares: int | None
    reduction_method: str | None
    method_terms_status: str
    holder_treatment_status: str
    consideration_type: str | None
    consideration_per_pre_share: Decimal | None
    consideration_currency: str | None
    consideration_terms_status: str
    fractional_share_policy: str | None
    record_date: date | None
    ex_date: date | None
    effective_date: date | None
    effective_date_rule_status: str
    finality: str


@dataclass(frozen=True)
class CapitalReductionFactorDecision:
    """Fail-closed capital-reduction source decision; never a factor."""

    evidence_status: str
    factor_status: str
    missing_evidence: tuple[str, ...]
    authorized_for_promotion: bool = False


def evaluate_capital_reduction_factor_evidence(
    evidence: CapitalReductionFactorEvidence,
) -> CapitalReductionFactorDecision:
    """Keep counts descriptive until every action-specific term is verified."""

    missing: list[str] = []
    if evidence.immutable_source_observation_status != "ACCEPTED_POSITIVE":
        missing.append("accepted_positive_immutable_source_observation")
    if (
        not isinstance(evidence.source_event_version_id, str)
        or not evidence.source_event_version_id.strip()
    ):
        missing.append("source_event_version_id")
    version = evidence.source_version_number
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        missing.append("explicit_source_version_number")
    elif version == 1:
        if (
            evidence.revision_parent_source_event_version_id is not None
            or evidence.revision_parent_status != "NOT_APPLICABLE_CONFIRMED_ORIGINAL"
            or evidence.source_revision_indicator not in {None, ""}
        ):
            missing.append("confirmed_original_revision_state")
    elif (
        not isinstance(evidence.revision_parent_source_event_version_id, str)
        or not evidence.revision_parent_source_event_version_id.strip()
        or evidence.revision_parent_status != "VERIFIED_EXPLICIT"
    ):
        missing.append("explicit_revision_parent")

    if not evidence.security_id or not evidence.security_id_scheme:
        missing.append("exact_security_identifier")
    if not evidence.security_class:
        missing.append("exact_security_class")

    counts = {
        "before_shares": evidence.before_shares,
        "after_shares": evidence.after_shares,
        "reduced_shares": evidence.reduced_shares,
    }
    for field, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            missing.append(field)
    if isinstance(evidence.before_shares, int) and evidence.before_shares <= 0:
        missing.append("before_shares_positive")
    if (
        all(isinstance(value, int) and not isinstance(value, bool) for value in counts.values())
        and evidence.before_shares - evidence.after_shares != evidence.reduced_shares
    ):
        missing.append("share_count_reconciliation")
    if not evidence.reduction_method:
        missing.append("explicit_reduction_method")
    if evidence.method_terms_status != "VERIFIED_COMPLETE":
        missing.append("complete_method_terms")
    if evidence.holder_treatment_status != "VERIFIED_EQUAL_PRO_RATA":
        missing.append("class_wide_holder_treatment")

    if evidence.consideration_type == "NONE_CONFIRMED":
        if (
            evidence.consideration_per_pre_share is not None
            or evidence.consideration_currency is not None
            or evidence.consideration_terms_status != "VERIFIED_COMPLETE"
        ):
            missing.append("verified_no_consideration_terms")
    elif evidence.consideration_type == "CASH":
        value = evidence.consideration_per_pre_share
        if (
            not isinstance(value, Decimal)
            or not value.is_finite()
            or value <= 0
            or not evidence.consideration_currency
            or evidence.consideration_terms_status != "VERIFIED_COMPLETE"
        ):
            missing.append("complete_cash_consideration_terms")
    else:
        missing.append("explicit_consideration_type")
    if not evidence.fractional_share_policy:
        missing.append("fractional_share_policy")

    for field in ("record_date", "ex_date", "effective_date"):
        value = getattr(evidence, field)
        if not isinstance(value, date) or isinstance(value, datetime):
            missing.append(field)
    if evidence.effective_date_rule_status != "VERIFIED_OFFICIAL_ACTION_SPECIFIC":
        missing.append("verified_ex_effective_date_rule")
    if (
        isinstance(evidence.ex_date, date)
        and not isinstance(evidence.ex_date, datetime)
        and isinstance(evidence.effective_date, date)
        and not isinstance(evidence.effective_date, datetime)
        and evidence.ex_date != evidence.effective_date
    ):
        missing.append("ex_effective_date_mismatch")
    if evidence.finality != "VERIFIED_FINAL":
        missing.append("verified_finality")

    blockers = tuple(dict.fromkeys(missing))
    if blockers:
        return CapitalReductionFactorDecision(
            evidence_status="SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE",
            factor_status="FACTOR_BLOCKED",
            missing_evidence=blockers,
        )
    return CapitalReductionFactorDecision(
        evidence_status="FACTOR_SOURCE_EVIDENCE_COMPLETE",
        factor_status="ELIGIBLE_FOR_CANONICAL_EVENT_REVIEW_ONLY",
        missing_evidence=(),
    )


@dataclass(frozen=True)
class MergerIdentityEvidence:
    """Evidence required to map a merger as an identity discontinuity."""

    immutable_source_observation_status: str
    source_event_version_id: str | None
    source_version_number: int | None
    revision_parent_source_event_version_id: str | None
    revision_parent_status: str
    source_revision_indicator: str | None
    merger_method: str | None
    merger_form: str | None
    predecessor_security_id: str | None
    predecessor_security_id_scheme: str | None
    predecessor_security_class: str | None
    successor_security_id: str | None
    successor_security_id_scheme: str | None
    successor_security_class: str | None
    consideration_type: str | None
    exchange_ratio: Decimal | None
    exchange_ratio_basis_status: str
    cash_consideration_per_pre_share: Decimal | None
    cash_consideration_currency: str | None
    consideration_terms_status: str
    merger_effective_date: date | None
    predecessor_last_trading_date: date | None
    successor_listing_date: date | None
    successor_first_trading_date: date | None
    effective_listing_rule_status: str
    finality: str
    listing_finality: str
    successor_mapping_contract_status: str


@dataclass(frozen=True)
class MergerIdentityDecision:
    """Merger identity decision that can never bridge a price series."""

    identity_status: str
    continuous_price_chain_status: str
    missing_evidence: tuple[str, ...]
    authorized_for_promotion: bool = False


def evaluate_merger_identity_evidence(
    evidence: MergerIdentityEvidence,
) -> MergerIdentityDecision:
    """Require exact security identities and complete merger lifecycle terms."""

    missing: list[str] = []
    if evidence.immutable_source_observation_status != "ACCEPTED_POSITIVE":
        missing.append("accepted_positive_immutable_source_observation")
    if (
        not isinstance(evidence.source_event_version_id, str)
        or not evidence.source_event_version_id.strip()
    ):
        missing.append("source_event_version_id")
    version = evidence.source_version_number
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        missing.append("explicit_source_version_number")
    elif version == 1:
        if (
            evidence.revision_parent_source_event_version_id is not None
            or evidence.revision_parent_status != "NOT_APPLICABLE_CONFIRMED_ORIGINAL"
            or evidence.source_revision_indicator not in {None, ""}
        ):
            missing.append("confirmed_original_revision_state")
    elif (
        not isinstance(evidence.revision_parent_source_event_version_id, str)
        or not evidence.revision_parent_source_event_version_id.strip()
        or evidence.revision_parent_status != "VERIFIED_EXPLICIT"
    ):
        missing.append("explicit_revision_parent")

    if not evidence.merger_method or not evidence.merger_form:
        missing.append("explicit_merger_method_and_form")
    predecessor = evidence.predecessor_security_id
    successor = evidence.successor_security_id
    if not predecessor or not evidence.predecessor_security_id_scheme:
        missing.append("exact_predecessor_security_identifier")
    if not evidence.predecessor_security_class:
        missing.append("exact_predecessor_security_class")
    if not successor or not evidence.successor_security_id_scheme:
        missing.append("exact_successor_security_identifier")
    if not evidence.successor_security_class:
        missing.append("exact_successor_security_class")
    if predecessor and successor and predecessor == successor:
        missing.append("distinct_predecessor_successor_security_ids")

    ratio = evidence.exchange_ratio
    cash = evidence.cash_consideration_per_pre_share
    if evidence.consideration_type == "STOCK":
        if (
            not isinstance(ratio, Decimal)
            or not ratio.is_finite()
            or ratio <= 0
            or evidence.exchange_ratio_basis_status != "VERIFIED_COMPLETE"
            or cash is not None
            or evidence.cash_consideration_currency is not None
        ):
            missing.append("complete_stock_exchange_terms")
    elif evidence.consideration_type == "CASH":
        if (
            not isinstance(cash, Decimal)
            or not cash.is_finite()
            or cash <= 0
            or not evidence.cash_consideration_currency
            or ratio is not None
        ):
            missing.append("complete_cash_merger_terms")
    elif evidence.consideration_type == "MIXED":
        if (
            not isinstance(ratio, Decimal)
            or not ratio.is_finite()
            or ratio <= 0
            or evidence.exchange_ratio_basis_status != "VERIFIED_COMPLETE"
            or not isinstance(cash, Decimal)
            or not cash.is_finite()
            or cash <= 0
            or not evidence.cash_consideration_currency
        ):
            missing.append("complete_mixed_merger_terms")
    elif evidence.consideration_type == "NONE_CONFIRMED":
        if ratio is not None or cash is not None or evidence.cash_consideration_currency is not None:
            missing.append("verified_no_consideration_terms")
    else:
        missing.append("explicit_consideration_type")
    if evidence.consideration_terms_status != "VERIFIED_COMPLETE":
        missing.append("complete_consideration_terms")

    for field in (
        "merger_effective_date", "predecessor_last_trading_date",
        "successor_listing_date", "successor_first_trading_date",
    ):
        value = getattr(evidence, field)
        if not isinstance(value, date) or isinstance(value, datetime):
            missing.append(field)
    if evidence.effective_listing_rule_status != "VERIFIED_OFFICIAL_ACTION_SPECIFIC":
        missing.append("verified_effective_listing_rule")
    if evidence.finality != "VERIFIED_FINAL":
        missing.append("verified_finality")
    if evidence.listing_finality != "VERIFIED_FINAL_LISTING":
        missing.append("verified_listing_finality")
    if evidence.successor_mapping_contract_status != "SEPARATELY_ACCEPTED_EXACT_IDS":
        missing.append("separate_successor_mapping_contract")

    blockers = tuple(dict.fromkeys(missing))
    if blockers:
        return MergerIdentityDecision(
            identity_status="IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE",
            continuous_price_chain_status="FORBIDDEN",
            missing_evidence=blockers,
        )
    return MergerIdentityDecision(
        identity_status="IDENTITY_DISCONTINUITY_READY_FOR_MAPPING_REVIEW_ONLY",
        continuous_price_chain_status="FORBIDDEN",
        missing_evidence=(),
    )


@dataclass(frozen=True)
class CompanyDivisionIdentityEvidence:
    """Evidence required to map a company division as an identity discontinuity."""

    immutable_source_observation_status: str
    source_event_version_id: str | None
    source_version_number: int | None
    revision_parent_source_event_version_id: str | None
    revision_parent_status: str
    source_revision_indicator: str | None
    action_classification: str
    division_method: str | None
    division_ratio: Decimal | None
    division_ratio_basis_status: str
    transferred_business_property_status: str
    division_terms_status: str
    surviving_security_id: str | None
    surviving_security_id_scheme: str | None
    surviving_security_class: str | None
    new_company_security_id: str | None
    new_company_security_id_scheme: str | None
    new_company_security_class: str | None
    surviving_listing_relation: str | None
    new_company_listing_relation: str | None
    division_effective_date: date | None
    division_registration_date: date | None
    surviving_listing_effective_date: date | None
    new_company_listing_effective_date: date | None
    lifecycle_rule_status: str
    event_finality: str
    surviving_listing_finality: str
    new_company_listing_finality: str
    successor_mapping_contract_status: str


@dataclass(frozen=True)
class CompanyDivisionIdentityDecision:
    """Company-division decision that never reclassifies as a share split."""

    identity_status: str
    action_classification_status: str
    continuous_price_chain_status: str
    missing_evidence: tuple[str, ...]
    authorized_for_promotion: bool = False


def evaluate_company_division_identity_evidence(
    evidence: CompanyDivisionIdentityEvidence,
) -> CompanyDivisionIdentityDecision:
    """Require exact surviving/new identities and final listing relationships."""

    missing: list[str] = []
    if evidence.immutable_source_observation_status != "ACCEPTED_POSITIVE":
        missing.append("accepted_positive_immutable_source_observation")
    if (
        not isinstance(evidence.source_event_version_id, str)
        or not evidence.source_event_version_id.strip()
    ):
        missing.append("source_event_version_id")
    version = evidence.source_version_number
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        missing.append("explicit_source_version_number")
    elif version == 1:
        if (
            evidence.revision_parent_source_event_version_id is not None
            or evidence.revision_parent_status != "NOT_APPLICABLE_CONFIRMED_ORIGINAL"
            or evidence.source_revision_indicator not in {None, ""}
        ):
            missing.append("confirmed_original_revision_state")
    elif (
        not isinstance(evidence.revision_parent_source_event_version_id, str)
        or not evidence.revision_parent_source_event_version_id.strip()
        or evidence.revision_parent_status != "VERIFIED_EXPLICIT"
    ):
        missing.append("explicit_revision_parent")

    if evidence.action_classification != "COMPANY_DIVISION_NOT_SHARE_SPLIT":
        missing.append("explicit_company_division_classification")
    if not evidence.division_method:
        missing.append("explicit_division_method")
    ratio = evidence.division_ratio
    if (
        not isinstance(ratio, Decimal)
        or not ratio.is_finite()
        or ratio <= 0
        or evidence.division_ratio_basis_status != "VERIFIED_COMPLETE"
    ):
        missing.append("complete_division_ratio_and_basis")
    if evidence.transferred_business_property_status != "VERIFIED_COMPLETE":
        missing.append("complete_transferred_business_and_property")
    if evidence.division_terms_status != "VERIFIED_COMPLETE":
        missing.append("complete_division_terms")

    surviving = evidence.surviving_security_id
    new_company = evidence.new_company_security_id
    if not surviving or not evidence.surviving_security_id_scheme:
        missing.append("exact_surviving_security_identifier")
    if not evidence.surviving_security_class:
        missing.append("exact_surviving_security_class")
    if not new_company or not evidence.new_company_security_id_scheme:
        missing.append("exact_new_company_security_identifier")
    if not evidence.new_company_security_class:
        missing.append("exact_new_company_security_class")
    if surviving and new_company and surviving == new_company:
        missing.append("distinct_surviving_new_company_security_ids")

    if evidence.surviving_listing_relation not in {
        "LISTING_MAINTAINED_FINAL", "CHANGE_LISTED_FINAL",
    }:
        missing.append("exact_surviving_listing_relation")
    if evidence.new_company_listing_relation not in {
        "RELISTED_FINAL", "NEW_LISTED_FINAL",
    }:
        missing.append("exact_new_company_listing_relation")
    for field in (
        "division_effective_date", "division_registration_date",
        "surviving_listing_effective_date", "new_company_listing_effective_date",
    ):
        value = getattr(evidence, field)
        if not isinstance(value, date) or isinstance(value, datetime):
            missing.append(field)
    if evidence.lifecycle_rule_status != "VERIFIED_OFFICIAL_ACTION_SPECIFIC":
        missing.append("verified_division_listing_lifecycle_rule")
    if evidence.event_finality != "VERIFIED_FINAL":
        missing.append("verified_event_finality")
    if evidence.surviving_listing_finality != "VERIFIED_FINAL_LISTING":
        missing.append("verified_surviving_listing_finality")
    if evidence.new_company_listing_finality != "VERIFIED_FINAL_LISTING":
        missing.append("verified_new_company_listing_finality")
    if evidence.successor_mapping_contract_status != "SEPARATELY_ACCEPTED_EXACT_IDS":
        missing.append("separate_successor_mapping_contract")

    blockers = tuple(dict.fromkeys(missing))
    if blockers:
        return CompanyDivisionIdentityDecision(
            identity_status="IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE",
            action_classification_status="COMPANY_DIVISION_NOT_SHARE_SPLIT",
            continuous_price_chain_status="FORBIDDEN",
            missing_evidence=blockers,
        )
    return CompanyDivisionIdentityDecision(
        identity_status="IDENTITY_DISCONTINUITY_READY_FOR_MAPPING_REVIEW_ONLY",
        action_classification_status="COMPANY_DIVISION_NOT_SHARE_SPLIT",
        continuous_price_chain_status="FORBIDDEN",
        missing_evidence=(),
    )


@dataclass(frozen=True)
class RightsIssueFactorEvidence:
    """Official event evidence required before a rights factor can be reviewed."""

    immutable_source_observation_status: str
    source_event_family: str
    source_event_version_id: str | None
    source_version_number: int | None
    revision_parent_source_event_version_id: str | None
    revision_parent_status: str
    source_revision_indicator: str | None
    security_id: str | None
    security_id_scheme: str | None
    security_class: str | None
    entitlement_new_shares: Decimal | None
    entitlement_existing_shares: Decimal | None
    entitlement_ratio_basis_status: str
    subscription_price: Decimal | None
    subscription_currency: str | None
    subscription_price_status: str
    rights_instrument_treatment: str
    rights_instrument_security_id: str | None
    rights_instrument_security_id_scheme: str | None
    exercise_treatment_status: str
    unsubscribed_shares_treatment_status: str
    fractional_share_policy: str | None
    record_date: date | None
    ex_right_date: date | None
    factor_effective_date: date | None
    subscription_start_date: date | None
    subscription_end_date: date | None
    payment_date: date | None
    effective_date_rule_status: str
    schedule_role_status: str
    finality: str


@dataclass(frozen=True)
class RightsIssueFactorDecision:
    """Fail-closed rights evidence decision; never authorizes promotion itself."""

    evidence_status: str
    factor_status: str
    missing_evidence: tuple[str, ...]
    authorized_for_promotion: bool = False


def evaluate_rights_issue_factor_evidence(
    evidence: RightsIssueFactorEvidence,
) -> RightsIssueFactorDecision:
    """Require final event economics; retained schedule rows are insufficient."""

    missing: list[str] = []
    if evidence.immutable_source_observation_status != "ACCEPTED_POSITIVE":
        missing.append("accepted_positive_immutable_source_observation")
    if evidence.source_event_family != "OFFICIAL_RIGHTS_ISSUE_EVENT":
        missing.append("official_rights_issue_event_family")
    if (
        not isinstance(evidence.source_event_version_id, str)
        or not evidence.source_event_version_id.strip()
    ):
        missing.append("source_event_version_id")
    version = evidence.source_version_number
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        missing.append("explicit_source_version_number")
    elif version == 1:
        if (
            evidence.revision_parent_source_event_version_id is not None
            or evidence.revision_parent_status != "NOT_APPLICABLE_CONFIRMED_ORIGINAL"
            or evidence.source_revision_indicator not in {None, ""}
        ):
            missing.append("confirmed_original_revision_state")
    elif (
        not isinstance(evidence.revision_parent_source_event_version_id, str)
        or not evidence.revision_parent_source_event_version_id.strip()
        or evidence.revision_parent_status != "VERIFIED_EXPLICIT"
    ):
        missing.append("explicit_revision_parent")

    if not evidence.security_id or not evidence.security_id_scheme:
        missing.append("exact_security_identifier")
    if not evidence.security_class:
        missing.append("exact_security_class")
    ratio_terms = (evidence.entitlement_new_shares, evidence.entitlement_existing_shares)
    if (
        any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            for value in ratio_terms
        )
        or evidence.entitlement_ratio_basis_status != "VERIFIED_COMPLETE"
    ):
        missing.append("complete_entitlement_ratio_and_basis")
    price = evidence.subscription_price
    if (
        not isinstance(price, Decimal)
        or not price.is_finite()
        or price <= 0
        or not evidence.subscription_currency
    ):
        missing.append("positive_subscription_price_and_currency")
    if evidence.subscription_price_status != "VERIFIED_FINAL_SUBSCRIPTION_PRICE":
        missing.append("verified_final_subscription_price")

    treatment = evidence.rights_instrument_treatment
    if treatment == "TRADABLE":
        if (
            not evidence.rights_instrument_security_id
            or not evidence.rights_instrument_security_id_scheme
        ):
            missing.append("exact_tradable_rights_instrument_identifier")
    elif treatment == "NON_TRADABLE_VERIFIED":
        if evidence.rights_instrument_security_id is not None:
            missing.append("non_tradable_rights_instrument_state")
    else:
        missing.append("verified_rights_tradability_treatment")
    if evidence.exercise_treatment_status != "VERIFIED_COMPLETE":
        missing.append("complete_exercise_treatment")
    if evidence.unsubscribed_shares_treatment_status != "VERIFIED_COMPLETE":
        missing.append("complete_unsubscribed_shares_treatment")
    if not evidence.fractional_share_policy:
        missing.append("fractional_share_policy")

    for field in (
        "record_date", "ex_right_date", "factor_effective_date",
        "subscription_start_date", "subscription_end_date", "payment_date",
    ):
        value = getattr(evidence, field)
        if not isinstance(value, date) or isinstance(value, datetime):
            missing.append(field)
    if (
        isinstance(evidence.ex_right_date, date)
        and not isinstance(evidence.ex_right_date, datetime)
        and isinstance(evidence.factor_effective_date, date)
        and not isinstance(evidence.factor_effective_date, datetime)
        and evidence.ex_right_date != evidence.factor_effective_date
    ):
        missing.append("ex_right_factor_effective_date_alignment")
    if (
        isinstance(evidence.subscription_start_date, date)
        and not isinstance(evidence.subscription_start_date, datetime)
        and isinstance(evidence.subscription_end_date, date)
        and not isinstance(evidence.subscription_end_date, datetime)
        and evidence.subscription_start_date > evidence.subscription_end_date
    ):
        missing.append("subscription_window_chronology")
    if (
        isinstance(evidence.subscription_end_date, date)
        and not isinstance(evidence.subscription_end_date, datetime)
        and isinstance(evidence.payment_date, date)
        and not isinstance(evidence.payment_date, datetime)
        and evidence.payment_date < evidence.subscription_end_date
    ):
        missing.append("payment_date_chronology")
    if evidence.effective_date_rule_status != "VERIFIED_OFFICIAL_ACTION_SPECIFIC":
        missing.append("verified_ex_right_effective_date_rule")
    if evidence.schedule_role_status not in {
        "NOT_USED_EVENT_NATIVE_DATES", "EXPLICITLY_LINKED_SUPPLEMENTARY_SCHEDULE",
    }:
        missing.append("schedule_not_used_as_standalone_event")
    if evidence.finality != "VERIFIED_FINAL_NO_SUPERSEDING_OR_CANCELLATION":
        missing.append("verified_event_finality")

    blockers = tuple(dict.fromkeys(missing))
    if blockers:
        return RightsIssueFactorDecision(
            evidence_status="SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE",
            factor_status="FACTOR_BLOCKED",
            missing_evidence=blockers,
        )
    return RightsIssueFactorDecision(
        evidence_status="FACTOR_SOURCE_EVIDENCE_COMPLETE",
        factor_status="ELIGIBLE_FOR_CANONICAL_EVENT_REVIEW_ONLY",
        missing_evidence=(),
    )


_OPENDART_BONUS_OPERATIONS = ("list", "fricDecsn", "pifricDecsn")
_SOURCE_ACCEPTANCE_OPERATION = "corporate-action-source-observation-acceptance"


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceAcceptanceError(f"retained JSON is unreadable: {path.name}") from error
    if not isinstance(value, dict):
        raise SourceAcceptanceError(f"retained JSON root must be an object: {path.name}")
    return value


def _read_ledger(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        values = [json.loads(line) for line in lines if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceAcceptanceError("retained call ledger is unreadable") from error
    if not values or any(not isinstance(value, dict) for value in values):
        raise SourceAcceptanceError("retained call ledger is invalid")
    return values


def evaluate_corporate_action_pilot(
    run_dir: Path, *, family: CorporateActionSourceFamily,
) -> CorporateActionSourceAcceptance:
    """Validate one completed immutable pilot without granting factor authority.

    Only the already documented three-operation OpenDART bonus/paid-free pilot
    has a retained response shape. Other action families deliberately require
    independent source acceptance instead of borrowing these semantics.
    """

    if family is not CorporateActionSourceFamily.BONUS_PAID_FREE:
        raise SourceAcceptanceError(
            f"{family.value} has no accepted immutable source-operation shape"
        )
    manifest = _read_json_object(run_dir / "manifest.json")
    checkpoint = _read_json_object(run_dir / "checkpoint.json")
    if (
        manifest.get("dataset") != "opendart_free_issue_source_pilot"
        or manifest.get("retry_count") != 0
        or manifest.get("business_request_limit") != 3
        or manifest.get("raw_http_request_limit") != 3
        or manifest.get("normalized_writes") is not False
    ):
        raise SourceAcceptanceError("pilot manifest authority or fixed budget differs")
    requests = manifest.get("requests")
    if not isinstance(requests, list) or len(requests) != 3:
        raise SourceAcceptanceError("pilot request matrix must contain exactly three operations")
    request_operations = tuple(
        str(item.get("operation")) if isinstance(item, dict) else "" for item in requests
    )
    request_sequences = tuple(
        item.get("sequence") if isinstance(item, dict) else None for item in requests
    )
    if request_operations != _OPENDART_BONUS_OPERATIONS or request_sequences != (1, 2, 3):
        raise SourceAcceptanceError("pilot request order differs from the reviewed source shape")
    scope = manifest.get("scope")
    if not isinstance(scope, dict):
        raise SourceAcceptanceError("pilot scope is missing")
    corp_code = str(scope.get("corp_code", ""))
    begin_date = str(scope.get("begin_date", ""))
    end_date = str(scope.get("end_date", ""))
    if len(corp_code) != 8 or not corp_code.isdigit():
        raise SourceAcceptanceError("pilot corp_code identity is invalid")
    try:
        begin = datetime.strptime(begin_date, "%Y%m%d").date()
        end = datetime.strptime(end_date, "%Y%m%d").date()
    except ValueError as error:
        raise SourceAcceptanceError("pilot scope dates are invalid") from error
    if begin < date(2015, 1, 1) or end < begin or (end - begin).days > 31:
        raise SourceAcceptanceError("pilot scope exceeds the reviewed bounded window")

    completed = checkpoint.get("completed")
    if (
        checkpoint.get("run_id") != manifest.get("run_id")
        or checkpoint.get("status") != "COMPLETE"
        or checkpoint.get("raw_http_requests") != 3
        or not isinstance(completed, dict)
        or tuple(completed) != _OPENDART_BONUS_OPERATIONS
    ):
        raise SourceAcceptanceError("pilot checkpoint is not the completed fixed scope")

    expected_files: set[str] = set()
    response_hashes: dict[str, str] = {}
    for sequence, operation in enumerate(_OPENDART_BONUS_OPERATIONS, start=1):
        record = completed.get(operation)
        if not isinstance(record, dict):
            raise SourceAcceptanceError(f"checkpoint record is invalid: {operation}")
        expected_name = f"response_{sequence:02d}_{operation}.json"
        if record.get("body_file") != expected_name:
            raise SourceAcceptanceError(f"Landing filename differs: {operation}")
        body_path = run_dir / expected_name
        try:
            body = body_path.read_bytes()
        except OSError as error:
            raise SourceAcceptanceError(f"immutable response is unreadable: {operation}") from error
        digest = hashlib.sha256(body).hexdigest()
        if record.get("body_sha256") != digest:
            raise SourceAcceptanceError(f"immutable response hash differs: {operation}")
        captured_at = checkpoint.get("updated_at_utc")
        if not isinstance(captured_at, str):
            raise SourceAcceptanceError("checkpoint completion timestamp is missing")
        try:
            classification, parsed_rows = parse_observations(
                operation, body, captured_at_utc=captured_at,
            )
        except ValueError as error:
            raise SourceAcceptanceError(
                f"retained response status/schema differs: {operation}"
            ) from error
        row_count = len(parsed_rows)
        if record.get("classification") != classification or record.get("rows") != row_count:
            raise SourceAcceptanceError(f"checkpoint classification differs: {operation}")
        expected_files.add(expected_name)
        response_hashes[operation] = digest
    actual_files = {path.name for path in run_dir.glob("response_*.json") if path.is_file()}
    if actual_files != expected_files:
        raise SourceAcceptanceError("retained response topology exceeds the fixed call scope")

    ledger = _read_ledger(run_dir / "call_ledger.jsonl")
    starts = [item for item in ledger if item.get("event") == "REQUEST_STARTED"]
    responses = [item for item in ledger if item.get("event") == "HTTP_RESPONSE"]
    completions = [item for item in ledger if item.get("event") == "REQUEST_COMPLETED"]
    terminal = [item for item in ledger if item.get("event") == "RUN_COMPLETED"]
    stopped = [item for item in ledger if item.get("event") == "RUN_STOPPED"]
    if not (
        len(starts) == len(responses) == len(completions) == 3
        and len(terminal) == 1
        and not stopped
    ):
        raise SourceAcceptanceError("call ledger does not prove one retry-zero three-call run")
    if tuple(item.get("operation") for item in starts) != _OPENDART_BONUS_OPERATIONS:
        raise SourceAcceptanceError("call ledger operation order differs")
    if tuple(item.get("operation") for item in completions) != _OPENDART_BONUS_OPERATIONS:
        raise SourceAcceptanceError("call ledger completion order differs")
    if tuple(item.get("raw_sequence") for item in responses) != (1, 2, 3):
        raise SourceAcceptanceError("call ledger raw sequence differs")
    for item, operation in zip(responses, _OPENDART_BONUS_OPERATIONS, strict=True):
        if (
            item.get("operation") != operation
            or item.get("status_code") != 200
            or item.get("response_sha256") != response_hashes[operation]
            or item.get("body_file") not in expected_files
        ):
            raise SourceAcceptanceError(f"call ledger response differs: {operation}")
    if terminal[0].get("business_requests") != 3 or terminal[0].get("raw_http_requests") != 3:
        raise SourceAcceptanceError("terminal call accounting differs")

    scope_id = f"{family.value}:{corp_code}:{begin_date}:{end_date}"
    return CorporateActionSourceAcceptance(
        family=family,
        scope_id=scope_id,
        scope_end=end,
        response_sha256=response_hashes,
        source_status="IMMUTABLE_SOURCE_OBSERVATION_ACCEPTED",
        canonical_status="CANONICAL_IDENTITY_BLOCKED",
        factor_status="FACTOR_BLOCKED",
        api_zero_replay=True,
    )


def promote_corporate_action_acceptance_manifest(
    decision: CorporateActionSourceAcceptance,
    *,
    acceptance_path: Path,
    checkpoint_path: Path,
    journal_path: Path,
    after_output: Callable[[int], None] | None = None,
) -> PromotionOutcome:
    """Atomically retain a source-only acceptance manifest and API-zero replay it."""

    if (
        decision.source_status != "IMMUTABLE_SOURCE_OBSERVATION_ACCEPTED"
        or decision.canonical_status != "CANONICAL_IDENTITY_BLOCKED"
        or decision.factor_status != "FACTOR_BLOCKED"
        or not decision.api_zero_replay
        or decision.authorized_for_live_call
    ):
        raise SourceAcceptanceError("decision exceeds source-observation-only authority")
    body = json.dumps(
        {
            "schema_version": 1,
            "family": decision.family.value,
            "scope_id": decision.scope_id,
            "scope_end": decision.scope_end.isoformat(),
            "response_sha256": dict(sorted(decision.response_sha256.items())),
            "source_status": decision.source_status,
            "canonical_status": decision.canonical_status,
            "factor_status": decision.factor_status,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return promote_outputs_atomically(
        operation=_SOURCE_ACCEPTANCE_OPERATION,
        datasets=(decision.family.value,),
        target_date=decision.scope_end,
        outputs={acceptance_path: body},
        checkpoint_path=checkpoint_path,
        journal_path=journal_path,
        after_output=after_output,
    )


@dataclass(frozen=True)
class SOXXIdentity:
    symbol: str = "SOXX"
    issuer: str = "iShares"
    fund_name: str = "iShares Semiconductor ETF"
    instrument_type: str = "ETF"
    exchange: str = "NASDAQ"
    cusip: str = "464287523"
    official_product_url: str = "https://www.ishares.com/us/products/239705/SOXX"


SOXX_OFFICIAL_IDENTITY = SOXXIdentity()


def _aware_timestamp(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise SourceAcceptanceError(f"{field} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceAcceptanceError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _iso_date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise SourceAcceptanceError(f"{field} must use YYYY-MM-DD") from error


def evaluate_fred_observation(
    observation: Mapping[str, object], *, decision_time: datetime
) -> SourceAcceptanceDecision:
    """Separate an as-retrieved FRED observation from vintage-safe evidence.

    A fredgraph CSV row is operationally descriptive when its immutable retrieval
    timestamp and observation date are retained.  Predictive use additionally
    requires FRED API real-time bounds and a source ``last_updated`` timestamp
    that was no later than the decision time.
    """

    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise SourceAcceptanceError("decision_time must be timezone-aware")
    if not str(observation.get("series_id", "")).strip():
        raise SourceAcceptanceError("series_id is required")
    observation_date = _iso_date(observation.get("observation_date"), "observation_date")
    retrieved_at = _aware_timestamp(observation.get("retrieved_at"), "retrieved_at")
    if observation_date > retrieved_at.date():
        raise SourceAcceptanceError("observation date cannot be after retrieval date")
    if str(observation.get("operational_status")) != "CURRENT_AS_RETRIEVED":
        raise SourceAcceptanceError("operational as-retrieved status is required")

    realtime_start = observation.get("realtime_start")
    realtime_end = observation.get("realtime_end")
    last_updated = observation.get("series_last_updated")
    if not realtime_start or not realtime_end or not last_updated:
        return SourceAcceptanceDecision(
            "CURRENT_AS_RETRIEVED",
            "PIT_BLOCKED_PENDING_VINTAGE_RESOLVER",
            False,
            "FRED real-time interval or series last_updated is absent",
        )

    start = _iso_date(realtime_start, "realtime_start")
    end = _iso_date(realtime_end, "realtime_end")
    if start > end:
        raise SourceAcceptanceError("FRED real-time interval is inverted")
    cutoff = decision_time.astimezone(timezone.utc)
    updated_at = _aware_timestamp(last_updated, "series_last_updated")
    if not (start <= cutoff.date() <= end) or updated_at > cutoff:
        return SourceAcceptanceDecision(
            "CURRENT_AS_RETRIEVED", "PIT_BLOCKED_NOT_AVAILABLE_AT_DECISION", False,
            "retained vintage was not valid and released by the decision time",
        )
    if str(observation.get("vintage_metadata_status")) != "FRED_API_REALTIME_PERIOD_RETAINED":
        return SourceAcceptanceDecision(
            "CURRENT_AS_RETRIEVED", "PIT_BLOCKED_UNVERIFIED_VINTAGE_METADATA", False,
            "real-time fields are not bound to an accepted FRED API capture",
        )
    return SourceAcceptanceDecision(
        "CURRENT_AS_RETRIEVED",
        "PIT_ELIGIBLE_RETAINED_FRED_VINTAGE",
        False,
        "real-time interval and last_updated satisfy the supplied decision time",
    )


def evaluate_soxx_onboarding(
    provider_meta: Mapping[str, object], *, official_identity: SOXXIdentity,
    operation_reviewed: bool, finality_reviewed: bool, retention_reviewed: bool,
) -> SourceAcceptanceDecision:
    """Validate SOXX ETF identity while keeping operation authority separate."""

    if official_identity != SOXX_OFFICIAL_IDENTITY:
        raise SourceAcceptanceError("SOXX official identity evidence differs")
    if str(provider_meta.get("symbol")) != "SOXX":
        raise SourceAcceptanceError("SOXX provider ticker differs")
    if str(provider_meta.get("instrumentType", "")).upper() != "ETF":
        raise SourceAcceptanceError("SOXX provider instrument is not ETF")
    exchange = str(provider_meta.get("exchangeName") or provider_meta.get("fullExchangeName") or "").upper()
    if exchange not in {"NMS", "NASDAQ", "NASDAQGS"}:
        raise SourceAcceptanceError("SOXX provider exchange is not recognized as NASDAQ")
    if str(provider_meta.get("currency", "")).upper() != "USD":
        raise SourceAcceptanceError("SOXX provider currency differs")
    if str(provider_meta.get("dataGranularity")) != "1d":
        raise SourceAcceptanceError("SOXX provider data is not daily")

    gates = {
        "operation_reviewed": operation_reviewed,
        "finality_reviewed": finality_reviewed,
        "retention_reviewed": retention_reviewed,
    }
    missing = [name for name, accepted in gates.items() if not accepted]
    if missing:
        return SourceAcceptanceDecision(
            "IDENTITY_ACCEPTED_LIVE_ONBOARDING_BLOCKED",
            "PIT_BLOCKED_PENDING_FINALITY_REVISION_POLICY",
            False,
            "missing onboarding gates: " + ", ".join(missing),
        )
    return SourceAcceptanceDecision(
        "READY_FOR_EXPLICIT_APPROVAL",
        "PIT_BLOCKED_PENDING_VINTAGE_POLICY",
        False,
        "offline gates are reviewed; Data Status and an active operation must still authorize a live call",
    )


def evaluate_ls_t8462_first_live(
    checkpoint_path: Path, provenance_paths: Sequence[Path], *,
    same_date_replay_status: str,
) -> SourceAcceptanceDecision:
    """Classify a retained first-live checkpoint without reading credentials/calling LS."""

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    expected_counts = {"raw_responses": 18, "provenance_sidecars": 18, "ledger_events": 19}
    if checkpoint.get("status") != "DAILY_COLLECTION_COMPLETE":
        raise SourceAcceptanceError("LS first-live checkpoint is not complete")
    if checkpoint.get("oauth_calls") != 1 or checkpoint.get("data_calls") != 18:
        raise SourceAcceptanceError("LS first-live call accounting differs")
    if checkpoint.get("retry_count") != 0 or checkpoint.get("artifact_counts") != expected_counts:
        raise SourceAcceptanceError("LS retry or artifact accounting differs")
    if checkpoint.get("secret_scan") != "PASS" or checkpoint.get("normalized_writes") is not False:
        raise SourceAcceptanceError("LS secret scan or Raw-only boundary differs")
    if len(provenance_paths) != 18:
        raise SourceAcceptanceError("LS requires exactly 18 provenance sidecars")
    for path in provenance_paths:
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("target_market_date_present") is not True:
            raise SourceAcceptanceError("LS target market date is missing from a scope")
        semantics = item.get("semantic_status") or {}
        if semantics.get("session_finality") != "UNRESOLVED":
            raise SourceAcceptanceError("LS session-finality evidence was silently changed")
        if semantics.get("predictive_pit") != "BLOCKED" or item.get("normalized_writes") is not False:
            raise SourceAcceptanceError("LS Raw/PIT boundary differs")
    if same_date_replay_status != "NOT_EXECUTED_ALREADY_ATTEMPTED":
        raise SourceAcceptanceError("LS same-date pre-network no-call proof is absent")
    return SourceAcceptanceDecision(
        "FIRST_LIVE_RAW_ACCEPTED_DESCRIPTIVE_ONLY",
        "PIT_BLOCKED_SESSION_FINALITY_REVISION_UNRESOLVED",
        False,
        "complete 18-scope Raw capture and same-date no-call pass; Normalized remains forbidden",
    )


__all__ = [
    "BonusFreeIssueFactorDecision", "BonusFreeIssueFactorEvidence",
    "CapitalReductionFactorDecision", "CapitalReductionFactorEvidence",
    "CompanyDivisionIdentityDecision", "CompanyDivisionIdentityEvidence",
    "CorporateActionSourceAcceptance", "CorporateActionSourceFamily",
    "MergerIdentityDecision", "MergerIdentityEvidence",
    "RightsIssueFactorDecision", "RightsIssueFactorEvidence",
    "SOXXIdentity", "SOXX_OFFICIAL_IDENTITY", "SourceAcceptanceDecision",
    "SourceAcceptanceError", "evaluate_fred_observation", "evaluate_ls_t8462_first_live",
    "evaluate_bonus_free_issue_factor_evidence", "evaluate_capital_reduction_factor_evidence",
    "evaluate_company_division_identity_evidence", "evaluate_corporate_action_pilot",
    "evaluate_merger_identity_evidence",
    "evaluate_rights_issue_factor_evidence",
    "evaluate_soxx_onboarding",
    "promote_corporate_action_acceptance_manifest",
]
