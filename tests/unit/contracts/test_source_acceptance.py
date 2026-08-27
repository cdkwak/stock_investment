from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import pytest

from stock_data.orchestration.recovery_supervisor import (
    PromotionStatus, RecoverySupervisorError,
)
from stock_data.orchestration.source_acceptance import (
    BonusFreeIssueFactorEvidence, CapitalReductionFactorEvidence,
    CompanyDivisionIdentityEvidence,
    CorporateActionSourceFamily,
    MergerIdentityEvidence,
    RightsIssueFactorEvidence,
    SOXX_OFFICIAL_IDENTITY, SourceAcceptanceError,
    evaluate_bonus_free_issue_factor_evidence,
    evaluate_capital_reduction_factor_evidence,
    evaluate_company_division_identity_evidence, evaluate_corporate_action_pilot,
    evaluate_fred_observation, evaluate_merger_identity_evidence,
    evaluate_rights_issue_factor_evidence,
    evaluate_ls_t8462_first_live, evaluate_soxx_onboarding,
    promote_corporate_action_acceptance_manifest,
)
from stock_data.providers.opendart_free_issue import PIFRIC_FIELDS, parse_observations


def _fred(**updates):
    row = {
        "series_id": "DGS10", "observation_date": "2026-08-13", "value": 4.2,
        "retrieved_at": "2026-08-18T01:00:00Z", "realtime_start": None,
        "realtime_end": None, "series_last_updated": None,
        "vintage_metadata_status": "UNAVAILABLE_FROM_FREDGRAPH_CSV",
        "operational_status": "CURRENT_AS_RETRIEVED",
    }
    row.update(updates)
    return row


def test_fred_as_retrieved_is_operational_but_predictive_blocked():
    decision = evaluate_fred_observation(
        _fred(), decision_time=datetime(2026, 8, 18, 2, tzinfo=timezone.utc)
    )
    assert decision.operational_status == "CURRENT_AS_RETRIEVED"
    assert decision.predictive_status == "PIT_BLOCKED_PENDING_VINTAGE_RESOLVER"
    assert decision.authorized_for_live_call is False


def test_fred_retained_realtime_period_can_be_resolved_at_decision_time():
    decision = evaluate_fred_observation(
        _fred(
            realtime_start="2026-08-14", realtime_end="2026-08-20",
            series_last_updated="2026-08-14T15:00:00-05:00",
            vintage_metadata_status="FRED_API_REALTIME_PERIOD_RETAINED",
        ),
        decision_time=datetime(2026, 8, 18, 2, tzinfo=timezone.utc),
    )
    assert decision.predictive_status == "PIT_ELIGIBLE_RETAINED_FRED_VINTAGE"


def test_soxx_identity_does_not_silently_authorize_onboarding():
    meta = {"symbol": "SOXX", "instrumentType": "ETF", "exchangeName": "NMS",
            "currency": "USD", "dataGranularity": "1d"}
    blocked = evaluate_soxx_onboarding(
        meta, official_identity=SOXX_OFFICIAL_IDENTITY,
        operation_reviewed=False, finality_reviewed=False, retention_reviewed=False,
    )
    assert blocked.operational_status == "IDENTITY_ACCEPTED_LIVE_ONBOARDING_BLOCKED"
    assert blocked.authorized_for_live_call is False
    ready = evaluate_soxx_onboarding(
        meta, official_identity=SOXX_OFFICIAL_IDENTITY,
        operation_reviewed=True, finality_reviewed=True, retention_reviewed=True,
    )
    assert ready.operational_status == "READY_FOR_EXPLICIT_APPROVAL"
    assert ready.predictive_status.startswith("PIT_BLOCKED")
    assert ready.authorized_for_live_call is False
    with pytest.raises(SourceAcceptanceError, match="not ETF"):
        evaluate_soxx_onboarding(
            {**meta, "instrumentType": "INDEX"}, official_identity=SOXX_OFFICIAL_IDENTITY,
            operation_reviewed=True, finality_reviewed=True, retention_reviewed=True,
        )


def test_ls_first_live_acceptance_stays_raw_descriptive_and_requires_no_call_replay(tmp_path: Path):
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "status": "DAILY_COLLECTION_COMPLETE", "oauth_calls": 1, "data_calls": 18,
        "retry_count": 0, "artifact_counts": {
            "raw_responses": 18, "provenance_sidecars": 18, "ledger_events": 19,
        }, "secret_scan": "PASS", "normalized_writes": False,
    }), encoding="utf-8")
    paths = []
    for index in range(18):
        path = tmp_path / f"{index:02d}.provenance.json"
        path.write_text(json.dumps({
            "target_market_date_present": True, "normalized_writes": False,
            "semantic_status": {"session_finality": "UNRESOLVED", "predictive_pit": "BLOCKED"},
        }), encoding="utf-8")
        paths.append(path)
    decision = evaluate_ls_t8462_first_live(
        checkpoint, paths, same_date_replay_status="NOT_EXECUTED_ALREADY_ATTEMPTED"
    )
    assert decision.operational_status == "FIRST_LIVE_RAW_ACCEPTED_DESCRIPTIVE_ONLY"
    assert decision.predictive_status.startswith("PIT_BLOCKED")
    with pytest.raises(SourceAcceptanceError, match="no-call proof"):
        evaluate_ls_t8462_first_live(checkpoint, paths, same_date_replay_status="EXECUTED")


def _write_corporate_action_pilot(root: Path) -> None:
    fixtures = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
    pifric_item = {field: "" for field in PIFRIC_FIELDS}
    pifric_item.update({
        "rcept_no": "20240102000001", "corp_code": "00123456",
        "corp_cls": "Y", "corp_name": "Fixture Corp",
    })
    bodies = {
        "list": (fixtures / "fixture_opendart_list_revision.json").read_bytes(),
        "fricDecsn": (fixtures / "fixture_opendart_fric_success.json").read_bytes(),
        "pifricDecsn": json.dumps(
            {"status": "000", "message": "ok", "list": [pifric_item]}
        ).encode(),
    }
    operations = ("list", "fricDecsn", "pifricDecsn")
    captured = "2026-08-20T00:00:00+00:00"
    completed = {}
    ledger = []
    requests = []
    import hashlib

    for sequence, operation in enumerate(operations, start=1):
        body = bodies[operation]
        name = f"response_{sequence:02d}_{operation}.json"
        (root / name).write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        classification, rows = parse_observations(
            operation, body, captured_at_utc=captured,
        )
        completed[operation] = {
            "classification": classification, "rows": len(rows),
            "body_file": name, "body_sha256": digest,
        }
        requests.append({"sequence": sequence, "operation": operation})
        ledger.extend((
            {"event": "REQUEST_STARTED", "operation": operation},
            {"event": "HTTP_RESPONSE", "operation": operation,
             "raw_sequence": sequence, "status_code": 200,
             "response_sha256": digest, "body_file": name},
            {"event": "REQUEST_COMPLETED", "operation": operation},
        ))
    ledger.append({"event": "RUN_COMPLETED", "business_requests": 3,
                   "raw_http_requests": 3})
    (root / "manifest.json").write_text(json.dumps({
        "run_id": "fixture-run", "dataset": "opendart_free_issue_source_pilot",
        "retry_count": 0, "business_request_limit": 3,
        "raw_http_request_limit": 3, "normalized_writes": False,
        "scope": {"corp_code": "00123456", "begin_date": "20240101",
                  "end_date": "20240131"},
        "requests": requests,
    }), encoding="utf-8")
    (root / "checkpoint.json").write_text(json.dumps({
        "run_id": "fixture-run", "status": "COMPLETE", "raw_http_requests": 3,
        "completed": completed, "updated_at_utc": captured,
    }), encoding="utf-8")
    (root / "call_ledger.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in ledger), encoding="utf-8"
    )


def test_corporate_action_pilot_accepts_only_immutable_source_observations(tmp_path: Path):
    _write_corporate_action_pilot(tmp_path)
    decision = evaluate_corporate_action_pilot(
        tmp_path, family=CorporateActionSourceFamily.BONUS_PAID_FREE,
    )
    assert decision.source_status == "IMMUTABLE_SOURCE_OBSERVATION_ACCEPTED"
    assert decision.canonical_status == "CANONICAL_IDENTITY_BLOCKED"
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert decision.api_zero_replay is True
    assert decision.authorized_for_live_call is False
    with pytest.raises(SourceAcceptanceError, match="no accepted immutable"):
        evaluate_corporate_action_pilot(
            tmp_path, family=CorporateActionSourceFamily.CAPITAL_REDUCTION,
        )


def test_corporate_action_pilot_fails_closed_on_hash_or_call_topology_change(tmp_path: Path):
    _write_corporate_action_pilot(tmp_path)
    response = tmp_path / "response_02_fricDecsn.json"
    response.write_bytes(response.read_bytes() + b" ")
    with pytest.raises(SourceAcceptanceError, match="hash differs"):
        evaluate_corporate_action_pilot(
            tmp_path, family=CorporateActionSourceFamily.BONUS_PAID_FREE,
        )


def test_corporate_action_source_acceptance_manifest_replays_api_zero(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    _write_corporate_action_pilot(run)
    decision = evaluate_corporate_action_pilot(
        run, family=CorporateActionSourceFamily.BONUS_PAID_FREE,
    )
    output = tmp_path / "accepted" / "manifest.json"
    checkpoint = tmp_path / "state" / "checkpoint.json"
    journal = tmp_path / "state" / "journal.json"
    first = promote_corporate_action_acceptance_manifest(
        decision, acceptance_path=output, checkpoint_path=checkpoint,
        journal_path=journal,
    )
    before = output.read_bytes()
    replay = promote_corporate_action_acceptance_manifest(
        decision, acceptance_path=output, checkpoint_path=checkpoint,
        journal_path=journal,
    )
    assert first.status is PromotionStatus.COMMITTED
    assert replay.status is PromotionStatus.API_ZERO_NOOP
    assert replay.provider_call_count == 0
    assert output.read_bytes() == before


def test_corporate_action_source_acceptance_failure_restores_prior_bytes(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    _write_corporate_action_pilot(run)
    decision = evaluate_corporate_action_pilot(
        run, family=CorporateActionSourceFamily.BONUS_PAID_FREE,
    )
    output = tmp_path / "accepted" / "manifest.json"
    checkpoint = tmp_path / "state" / "checkpoint.json"
    journal = tmp_path / "state" / "journal.json"
    output.parent.mkdir(parents=True)
    checkpoint.parent.mkdir(parents=True)
    output.write_bytes(b"prior-valid-acceptance")
    checkpoint.write_bytes(b'{"prior":"checkpoint"}')
    before_output, before_checkpoint = output.read_bytes(), checkpoint.read_bytes()

    def fail_after_output(_: int) -> None:
        raise OSError("injected acceptance failure")

    with pytest.raises(RecoverySupervisorError, match="prior accepted bytes were restored"):
        promote_corporate_action_acceptance_manifest(
            decision, acceptance_path=output, checkpoint_path=checkpoint,
            journal_path=journal, after_output=fail_after_output,
        )
    assert output.read_bytes() == before_output
    assert checkpoint.read_bytes() == before_checkpoint


def _bonus_free_issue_factor_evidence(**updates) -> BonusFreeIssueFactorEvidence:
    values = {
        "source_event_version_id": "receipt-v2",
        "source_version_number": 2,
        "revision_parent_source_event_version_id": "receipt-v1",
        "revision_parent_status": "VERIFIED_EXPLICIT",
        "source_revision_indicator": "CORRECTION",
        "security_id": "KR7000000000",
        "security_id_scheme": "ISIN",
        "security_class": "ORDINARY_REGISTERED",
        "record_date": date(2026, 8, 28),
        "ex_date": date(2026, 8, 27),
        "effective_date": date(2026, 8, 27),
        "effective_date_rule_status": "VERIFIED_OFFICIAL_ACTION_SPECIFIC",
        "finality": "VERIFIED_FINAL",
        "new_shares": 300,
        "pre_issue_shares": 110,
        "eligible_existing_shares": 100,
        "allocation_per_existing_share": Decimal("3"),
        "par_value_krw": Decimal("500"),
        "fractional_share_policy": "CASH_IN_LIEU_OFFICIAL",
        "action_scope": "COMBINED_PAID_FREE",
        "combined_paid_issue_terms_complete": True,
        "combined_sequence_status": "VERIFIED_EXPLICIT",
    }
    values.update(updates)
    return BonusFreeIssueFactorEvidence(**values)


def test_bonus_free_issue_factor_evidence_keeps_retained_positive_source_only():
    decision = evaluate_bonus_free_issue_factor_evidence(
        _bonus_free_issue_factor_evidence(
            source_version_number=None,
            revision_parent_source_event_version_id=None,
            revision_parent_status="UNRESOLVED",
            security_id=None,
            security_id_scheme=None,
            security_class=None,
            ex_date=None,
            effective_date=None,
            effective_date_rule_status="UNRESOLVED",
            finality="UNRESOLVED",
            new_shares=73_351_008,
            pre_issue_shares=24_530_810,
            eligible_existing_shares=None,
            allocation_per_existing_share=Decimal("3"),
            fractional_share_policy=None,
            combined_paid_issue_terms_complete=False,
            combined_sequence_status="UNRESOLVED",
        )
    )
    assert decision.evidence_status == "SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE"
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert decision.authorized_for_promotion is False
    assert {
        "explicit_source_version_number", "exact_security_identifier",
        "exact_security_class", "ex_date", "effective_date",
        "verified_ex_effective_date_rule", "verified_finality",
        "eligible_existing_shares", "fractional_share_policy",
        "complete_paid_issue_terms", "explicit_paid_free_sequence",
    }.issubset(decision.missing_evidence)


def test_bonus_free_issue_factor_evidence_only_enters_canonical_review_when_complete():
    decision = evaluate_bonus_free_issue_factor_evidence(
        _bonus_free_issue_factor_evidence()
    )
    assert decision.evidence_status == "FACTOR_SOURCE_EVIDENCE_COMPLETE"
    assert decision.factor_status == "ELIGIBLE_FOR_CANONICAL_EVENT_REVIEW_ONLY"
    assert decision.missing_evidence == ()
    assert decision.authorized_for_promotion is False


def test_bonus_free_issue_factor_evidence_rejects_unreconciled_economic_terms():
    decision = evaluate_bonus_free_issue_factor_evidence(
        _bonus_free_issue_factor_evidence(new_shares=299)
    )
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert "eligible_share_reconciliation" in decision.missing_evidence


def _capital_reduction_factor_evidence(**updates) -> CapitalReductionFactorEvidence:
    values = {
        "immutable_source_observation_status": "ACCEPTED_POSITIVE",
        "source_event_version_id": "receipt-v2",
        "source_version_number": 2,
        "revision_parent_source_event_version_id": "receipt-v1",
        "revision_parent_status": "VERIFIED_EXPLICIT",
        "source_revision_indicator": "CORRECTION",
        "security_id": "KR7000000000",
        "security_id_scheme": "ISIN",
        "security_class": "ORDINARY_REGISTERED",
        "before_shares": 400,
        "after_shares": 100,
        "reduced_shares": 300,
        "reduction_method": "FOUR_TO_ONE_SHARE_CONSOLIDATION",
        "method_terms_status": "VERIFIED_COMPLETE",
        "holder_treatment_status": "VERIFIED_EQUAL_PRO_RATA",
        "consideration_type": "NONE_CONFIRMED",
        "consideration_per_pre_share": None,
        "consideration_currency": None,
        "consideration_terms_status": "VERIFIED_COMPLETE",
        "fractional_share_policy": "CASH_IN_LIEU_OFFICIAL",
        "record_date": date(2026, 8, 28),
        "ex_date": date(2026, 9, 15),
        "effective_date": date(2026, 9, 15),
        "effective_date_rule_status": "VERIFIED_OFFICIAL_ACTION_SPECIFIC",
        "finality": "VERIFIED_FINAL",
    }
    values.update(updates)
    return CapitalReductionFactorEvidence(**values)


def test_capital_reduction_factor_evidence_requires_retained_positive_observation():
    decision = evaluate_capital_reduction_factor_evidence(
        _capital_reduction_factor_evidence(
            immutable_source_observation_status="NOT_RETAINED",
            source_event_version_id=None,
            source_version_number=None,
            revision_parent_source_event_version_id=None,
            revision_parent_status="UNRESOLVED",
            security_id=None,
            security_id_scheme=None,
            security_class=None,
            ex_date=None,
            effective_date=None,
            effective_date_rule_status="UNRESOLVED",
            finality="UNRESOLVED",
        )
    )
    assert decision.evidence_status == "SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE"
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert decision.authorized_for_promotion is False
    assert {
        "accepted_positive_immutable_source_observation",
        "explicit_source_version_number", "exact_security_identifier",
        "exact_security_class", "ex_date", "effective_date",
        "verified_ex_effective_date_rule", "verified_finality",
    }.issubset(decision.missing_evidence)


def test_capital_reduction_factor_evidence_counts_alone_never_authorize_factor():
    decision = evaluate_capital_reduction_factor_evidence(
        _capital_reduction_factor_evidence(
            reduction_method=None,
            method_terms_status="UNRESOLVED",
            holder_treatment_status="UNRESOLVED",
            consideration_type=None,
            consideration_terms_status="UNRESOLVED",
            fractional_share_policy=None,
        )
    )
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert {
        "explicit_reduction_method", "complete_method_terms",
        "class_wide_holder_treatment", "explicit_consideration_type",
        "fractional_share_policy",
    }.issubset(decision.missing_evidence)


def test_capital_reduction_factor_evidence_only_enters_canonical_review_when_complete():
    decision = evaluate_capital_reduction_factor_evidence(
        _capital_reduction_factor_evidence()
    )
    assert decision.evidence_status == "FACTOR_SOURCE_EVIDENCE_COMPLETE"
    assert decision.factor_status == "ELIGIBLE_FOR_CANONICAL_EVENT_REVIEW_ONLY"
    assert decision.missing_evidence == ()
    assert decision.authorized_for_promotion is False


def test_capital_reduction_factor_evidence_rejects_unreconciled_share_counts():
    decision = evaluate_capital_reduction_factor_evidence(
        _capital_reduction_factor_evidence(reduced_shares=299)
    )
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert "share_count_reconciliation" in decision.missing_evidence


def _merger_identity_evidence(**updates) -> MergerIdentityEvidence:
    values = {
        "immutable_source_observation_status": "ACCEPTED_POSITIVE",
        "source_event_version_id": "receipt-v2",
        "source_version_number": 2,
        "revision_parent_source_event_version_id": "receipt-v1",
        "revision_parent_status": "VERIFIED_EXPLICIT",
        "source_revision_indicator": "CORRECTION",
        "merger_method": "ABSORPTION",
        "merger_form": "LISTED_PREDECESSOR_INTO_LISTED_SUCCESSOR",
        "predecessor_security_id": "KR7000000001",
        "predecessor_security_id_scheme": "ISIN",
        "predecessor_security_class": "ORDINARY_REGISTERED",
        "successor_security_id": "KR7000000002",
        "successor_security_id_scheme": "ISIN",
        "successor_security_class": "ORDINARY_REGISTERED",
        "consideration_type": "STOCK",
        "exchange_ratio": Decimal("0.75"),
        "exchange_ratio_basis_status": "VERIFIED_COMPLETE",
        "cash_consideration_per_pre_share": None,
        "cash_consideration_currency": None,
        "consideration_terms_status": "VERIFIED_COMPLETE",
        "merger_effective_date": date(2026, 8, 28),
        "predecessor_last_trading_date": date(2026, 8, 20),
        "successor_listing_date": date(2026, 9, 15),
        "successor_first_trading_date": date(2026, 9, 15),
        "effective_listing_rule_status": "VERIFIED_OFFICIAL_ACTION_SPECIFIC",
        "finality": "VERIFIED_FINAL",
        "listing_finality": "VERIFIED_FINAL_LISTING",
        "successor_mapping_contract_status": "SEPARATELY_ACCEPTED_EXACT_IDS",
    }
    values.update(updates)
    return MergerIdentityEvidence(**values)


def test_merger_identity_evidence_requires_positive_source_and_exact_security_ids():
    decision = evaluate_merger_identity_evidence(
        _merger_identity_evidence(
            immutable_source_observation_status="NOT_RETAINED",
            source_event_version_id=None,
            source_version_number=None,
            revision_parent_source_event_version_id=None,
            revision_parent_status="UNRESOLVED",
            predecessor_security_id=None,
            predecessor_security_id_scheme=None,
            predecessor_security_class=None,
            successor_security_id=None,
            successor_security_id_scheme=None,
            successor_security_class=None,
            finality="UNRESOLVED",
            listing_finality="UNRESOLVED",
            successor_mapping_contract_status="NOT_ACCEPTED",
        )
    )
    assert decision.identity_status == "IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE"
    assert decision.continuous_price_chain_status == "FORBIDDEN"
    assert decision.authorized_for_promotion is False
    assert {
        "accepted_positive_immutable_source_observation",
        "explicit_source_version_number",
        "exact_predecessor_security_identifier",
        "exact_successor_security_identifier",
        "verified_finality", "verified_listing_finality",
        "separate_successor_mapping_contract",
    }.issubset(decision.missing_evidence)


def test_merger_identity_evidence_rejects_names_or_current_code_substitution():
    decision = evaluate_merger_identity_evidence(
        _merger_identity_evidence(
            predecessor_security_id=None,
            predecessor_security_id_scheme=None,
            successor_security_id=None,
            successor_security_id_scheme=None,
        )
    )
    assert decision.identity_status == "IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE"
    assert "exact_predecessor_security_identifier" in decision.missing_evidence
    assert "exact_successor_security_identifier" in decision.missing_evidence
    assert decision.continuous_price_chain_status == "FORBIDDEN"


def test_merger_identity_evidence_complete_still_forbids_continuous_price_chain():
    decision = evaluate_merger_identity_evidence(_merger_identity_evidence())
    assert decision.identity_status == "IDENTITY_DISCONTINUITY_READY_FOR_MAPPING_REVIEW_ONLY"
    assert decision.continuous_price_chain_status == "FORBIDDEN"
    assert decision.missing_evidence == ()
    assert decision.authorized_for_promotion is False


def test_merger_identity_evidence_requires_complete_consideration_ratio():
    decision = evaluate_merger_identity_evidence(
        _merger_identity_evidence(
            exchange_ratio=None,
            exchange_ratio_basis_status="UNRESOLVED",
        )
    )
    assert decision.identity_status == "IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE"
    assert "complete_stock_exchange_terms" in decision.missing_evidence
    assert decision.continuous_price_chain_status == "FORBIDDEN"


def _company_division_identity_evidence(**updates) -> CompanyDivisionIdentityEvidence:
    values = {
        "immutable_source_observation_status": "ACCEPTED_POSITIVE",
        "source_event_version_id": "receipt-v2",
        "source_version_number": 2,
        "revision_parent_source_event_version_id": "receipt-v1",
        "revision_parent_status": "VERIFIED_EXPLICIT",
        "source_revision_indicator": "CORRECTION",
        "action_classification": "COMPANY_DIVISION_NOT_SHARE_SPLIT",
        "division_method": "PERSONNEL_SPINOFF",
        "division_ratio": Decimal("0.25"),
        "division_ratio_basis_status": "VERIFIED_COMPLETE",
        "transferred_business_property_status": "VERIFIED_COMPLETE",
        "division_terms_status": "VERIFIED_COMPLETE",
        "surviving_security_id": "KR7000000001",
        "surviving_security_id_scheme": "ISIN",
        "surviving_security_class": "ORDINARY_REGISTERED",
        "new_company_security_id": "KR7000000002",
        "new_company_security_id_scheme": "ISIN",
        "new_company_security_class": "ORDINARY_REGISTERED",
        "surviving_listing_relation": "CHANGE_LISTED_FINAL",
        "new_company_listing_relation": "RELISTED_FINAL",
        "division_effective_date": date(2026, 8, 28),
        "division_registration_date": date(2026, 8, 29),
        "surviving_listing_effective_date": date(2026, 9, 15),
        "new_company_listing_effective_date": date(2026, 9, 15),
        "lifecycle_rule_status": "VERIFIED_OFFICIAL_ACTION_SPECIFIC",
        "event_finality": "VERIFIED_FINAL",
        "surviving_listing_finality": "VERIFIED_FINAL_LISTING",
        "new_company_listing_finality": "VERIFIED_FINAL_LISTING",
        "successor_mapping_contract_status": "SEPARATELY_ACCEPTED_EXACT_IDS",
    }
    values.update(updates)
    return CompanyDivisionIdentityEvidence(**values)


def test_company_division_identity_evidence_requires_positive_source_and_exact_ids():
    decision = evaluate_company_division_identity_evidence(
        _company_division_identity_evidence(
            immutable_source_observation_status="NOT_RETAINED",
            source_event_version_id=None,
            source_version_number=None,
            revision_parent_source_event_version_id=None,
            revision_parent_status="UNRESOLVED",
            surviving_security_id=None,
            surviving_security_id_scheme=None,
            new_company_security_id=None,
            new_company_security_id_scheme=None,
            event_finality="UNRESOLVED",
            successor_mapping_contract_status="NOT_ACCEPTED",
        )
    )
    assert decision.identity_status == "IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE"
    assert decision.action_classification_status == "COMPANY_DIVISION_NOT_SHARE_SPLIT"
    assert decision.continuous_price_chain_status == "FORBIDDEN"
    assert {
        "accepted_positive_immutable_source_observation",
        "explicit_source_version_number",
        "exact_surviving_security_identifier",
        "exact_new_company_security_identifier",
        "verified_event_finality", "separate_successor_mapping_contract",
    }.issubset(decision.missing_evidence)


def test_company_division_identity_evidence_never_accepts_share_split_classification():
    decision = evaluate_company_division_identity_evidence(
        _company_division_identity_evidence(action_classification="SHARE_SPLIT")
    )
    assert decision.identity_status == "IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE"
    assert "explicit_company_division_classification" in decision.missing_evidence
    assert decision.continuous_price_chain_status == "FORBIDDEN"


def test_company_division_identity_evidence_rejects_name_or_ticker_substitution():
    decision = evaluate_company_division_identity_evidence(
        _company_division_identity_evidence(
            surviving_security_id=None,
            surviving_security_id_scheme=None,
            new_company_security_id=None,
            new_company_security_id_scheme=None,
        )
    )
    assert "exact_surviving_security_identifier" in decision.missing_evidence
    assert "exact_new_company_security_identifier" in decision.missing_evidence
    assert decision.continuous_price_chain_status == "FORBIDDEN"


def test_company_division_identity_evidence_complete_still_forbids_continuous_chain():
    decision = evaluate_company_division_identity_evidence(
        _company_division_identity_evidence()
    )
    assert decision.identity_status == "IDENTITY_DISCONTINUITY_READY_FOR_MAPPING_REVIEW_ONLY"
    assert decision.action_classification_status == "COMPANY_DIVISION_NOT_SHARE_SPLIT"
    assert decision.continuous_price_chain_status == "FORBIDDEN"
    assert decision.missing_evidence == ()
    assert decision.authorized_for_promotion is False


def test_company_division_identity_evidence_requires_final_listing_relation():
    decision = evaluate_company_division_identity_evidence(
        _company_division_identity_evidence(
            new_company_listing_relation=None,
            new_company_listing_finality="UNRESOLVED",
        )
    )
    assert decision.identity_status == "IDENTITY_DISCONTINUITY_EVIDENCE_INCOMPLETE"
    assert "exact_new_company_listing_relation" in decision.missing_evidence
    assert "verified_new_company_listing_finality" in decision.missing_evidence
    assert decision.continuous_price_chain_status == "FORBIDDEN"


def _rights_issue_factor_evidence(**updates) -> RightsIssueFactorEvidence:
    values = {
        "immutable_source_observation_status": "ACCEPTED_POSITIVE",
        "source_event_family": "OFFICIAL_RIGHTS_ISSUE_EVENT",
        "source_event_version_id": "receipt-v2",
        "source_version_number": 2,
        "revision_parent_source_event_version_id": "receipt-v1",
        "revision_parent_status": "VERIFIED_EXPLICIT",
        "source_revision_indicator": "CORRECTION",
        "security_id": "KR7000000001",
        "security_id_scheme": "ISIN",
        "security_class": "ORDINARY_REGISTERED",
        "entitlement_new_shares": Decimal("1"),
        "entitlement_existing_shares": Decimal("4"),
        "entitlement_ratio_basis_status": "VERIFIED_COMPLETE",
        "subscription_price": Decimal("5000"),
        "subscription_currency": "KRW",
        "subscription_price_status": "VERIFIED_FINAL_SUBSCRIPTION_PRICE",
        "rights_instrument_treatment": "TRADABLE",
        "rights_instrument_security_id": "KRA000000001",
        "rights_instrument_security_id_scheme": "ISIN",
        "exercise_treatment_status": "VERIFIED_COMPLETE",
        "unsubscribed_shares_treatment_status": "VERIFIED_COMPLETE",
        "fractional_share_policy": "CASH_IN_LIEU_FINAL",
        "record_date": date(2026, 8, 21),
        "ex_right_date": date(2026, 8, 20),
        "factor_effective_date": date(2026, 8, 20),
        "subscription_start_date": date(2026, 9, 1),
        "subscription_end_date": date(2026, 9, 3),
        "payment_date": date(2026, 9, 4),
        "effective_date_rule_status": "VERIFIED_OFFICIAL_ACTION_SPECIFIC",
        "schedule_role_status": "NOT_USED_EVENT_NATIVE_DATES",
        "finality": "VERIFIED_FINAL_NO_SUPERSEDING_OR_CANCELLATION",
    }
    values.update(updates)
    return RightsIssueFactorEvidence(**values)


def test_rights_issue_schedule_alone_never_authorizes_factor():
    decision = evaluate_rights_issue_factor_evidence(
        _rights_issue_factor_evidence(
            immutable_source_observation_status="SCHEDULE_ONLY",
            source_event_family="RETAINED_RIGHTS_SCHEDULE",
            source_event_version_id=None,
            source_version_number=None,
            revision_parent_source_event_version_id=None,
            revision_parent_status="UNRESOLVED",
            source_revision_indicator=None,
            security_id=None,
            security_id_scheme=None,
            security_class=None,
            entitlement_new_shares=None,
            entitlement_existing_shares=None,
            entitlement_ratio_basis_status="UNRESOLVED",
            subscription_price=None,
            subscription_currency=None,
            subscription_price_status="UNRESOLVED",
            record_date=None,
            ex_right_date=None,
            factor_effective_date=None,
            schedule_role_status="RETAINED_SCHEDULE_ONLY",
            finality="UNRESOLVED",
        )
    )
    assert decision.evidence_status == "SOURCE_OBSERVATION_ONLY_EVIDENCE_INCOMPLETE"
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert decision.authorized_for_promotion is False
    assert {
        "accepted_positive_immutable_source_observation",
        "official_rights_issue_event_family", "exact_security_identifier",
        "exact_security_class", "complete_entitlement_ratio_and_basis",
        "positive_subscription_price_and_currency",
        "schedule_not_used_as_standalone_event", "verified_event_finality",
    }.issubset(decision.missing_evidence)


def test_rights_issue_requires_final_subscription_price():
    decision = evaluate_rights_issue_factor_evidence(
        _rights_issue_factor_evidence(
            subscription_price_status="PRELIMINARY_EX_RIGHT_REFERENCE_PRICE"
        )
    )
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert "verified_final_subscription_price" in decision.missing_evidence


def test_rights_issue_requires_tradability_exercise_and_fraction_terms():
    decision = evaluate_rights_issue_factor_evidence(
        _rights_issue_factor_evidence(
            rights_instrument_treatment="UNRESOLVED",
            rights_instrument_security_id=None,
            rights_instrument_security_id_scheme=None,
            exercise_treatment_status="UNRESOLVED",
            unsubscribed_shares_treatment_status="UNRESOLVED",
            fractional_share_policy=None,
        )
    )
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert {
        "verified_rights_tradability_treatment", "complete_exercise_treatment",
        "complete_unsubscribed_shares_treatment", "fractional_share_policy",
    }.issubset(decision.missing_evidence)


def test_rights_issue_requires_ex_right_effective_date_alignment():
    decision = evaluate_rights_issue_factor_evidence(
        _rights_issue_factor_evidence(factor_effective_date=date(2026, 8, 19))
    )
    assert decision.factor_status == "FACTOR_BLOCKED"
    assert "ex_right_factor_effective_date_alignment" in decision.missing_evidence


def test_rights_issue_complete_evidence_only_enters_canonical_review():
    decision = evaluate_rights_issue_factor_evidence(
        _rights_issue_factor_evidence()
    )
    assert decision.evidence_status == "FACTOR_SOURCE_EVIDENCE_COMPLETE"
    assert decision.factor_status == "ELIGIBLE_FOR_CANONICAL_EVENT_REVIEW_ONLY"
    assert decision.missing_evidence == ()
    assert decision.authorized_for_promotion is False
