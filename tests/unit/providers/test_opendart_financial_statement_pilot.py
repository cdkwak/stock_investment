from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_data.providers.opendart_financial_statement_pilot import (
    FinancialStatementPilotScope,
    OpenDartFinancialStatementError,
    _landing_path,
    parse_single_account_response,
    run_landing_first_financial_statement_pilot,
)


def _body(**overrides: object) -> bytes:
    item = {
        "rcept_no": "20220322001234", "reprt_code": "11011", "bsns_year": "2021",
        "corp_code": "01160363", "sj_div": "BS", "account_id": "ifrs-full_Assets",
        "account_nm": "Assets", "account_detail": "", "thstrm_nm": "2021",
        "thstrm_amount": "100", "thstrm_add_amount": "", "frmtrm_nm": "2020",
        "frmtrm_amount": "90", "currency": "KRW", "ord": "1", "fs_div": "CFS",
    }
    item.update(overrides)
    return json.dumps({"status": "000", "message": "ok", "list": [item]}).encode()


def test_scope_is_exact_and_public_parameters_exclude_credentials():
    scope = FinancialStatementPilotScope()
    assert scope.public_parameters() == {
        "corp_code": "01160363", "bsns_year": "2021", "reprt_code": "11011", "fs_div": "CFS",
    }
    with pytest.raises(OpenDartFinancialStatementError, match="frozen annual"):
        FinancialStatementPilotScope(financial_statement_division="OFS")


def test_parser_preserves_raw_amounts_and_blocks_pit_and_redistribution():
    result = parse_single_account_response(
        _body(), scope=FinancialStatementPilotScope(), captured_at_utc="2026-08-20T14:30:00Z",
    )
    row = result.rows[0]
    assert len(result.body_sha256) == 64
    assert row["current_term_amount_raw"] == "100"
    assert row["provider_published_at_utc"] is None
    assert row["available_at_utc"] is None
    assert row["usable_from"] is None
    assert row["revision_parent_receipt_no"] is None
    assert row["pit_status"] == "PIT_BLOCKED_PUBLICATION_AND_REVISION_UNVERIFIED"
    assert row["redistribution_status"] == "RIGHTS_AND_REDISTRIBUTION_UNVERIFIED"


def test_parser_rejects_scope_drift_empty_and_missing_schema():
    scope = FinancialStatementPilotScope()
    with pytest.raises(OpenDartFinancialStatementError, match="scope"):
        parse_single_account_response(_body(corp_code="00126380"), scope=scope, captured_at_utc="2026-08-20T14:30:00Z")
    with pytest.raises(OpenDartFinancialStatementError, match="no financial rows"):
        parse_single_account_response(b'{"status":"000","list":[]}', scope=scope, captured_at_utc="2026-08-20T14:30:00Z")
    with pytest.raises(OpenDartFinancialStatementError, match="account name is invalid"):
        parse_single_account_response(_body(account_nm=None), scope=scope, captured_at_utc="2026-08-20T14:30:00Z")


def test_precredential_gate_makes_zero_provider_calls_without_approval(tmp_path: Path):
    calls = 0

    def fetch() -> bytes:
        nonlocal calls
        calls += 1
        return _body()

    with pytest.raises(OpenDartFinancialStatementError, match="EXPLICIT_LIVE_APPROVAL_REQUIRED"):
        run_landing_first_financial_statement_pilot(
            tmp_path, approved=False, fetch=fetch, scope=FinancialStatementPilotScope(),
            captured_at_utc="2026-08-20T14:30:00Z",
        )
    assert calls == 0


def test_landing_first_runner_commits_readback_then_replays_with_zero_fetches(tmp_path: Path):
    body = _body()
    calls = 0
    scope = FinancialStatementPilotScope()
    landing = _landing_path(tmp_path, scope)
    parser_bodies: list[bytes] = []

    def fetch() -> bytes:
        nonlocal calls
        calls += 1
        return body

    def parser(readback: bytes, selected_scope: FinancialStatementPilotScope, captured: str):
        assert landing.is_file()
        assert readback == landing.read_bytes()
        parser_bodies.append(readback)
        return parse_single_account_response(
            readback, scope=selected_scope, captured_at_utc=captured,
        )

    completed = run_landing_first_financial_statement_pilot(
        tmp_path, approved=True, fetch=fetch, scope=scope,
        captured_at_utc="2026-08-20T14:30:00Z", parser=parser,
    )
    assert completed.typed_outcome == "COMPLETED"
    assert completed.call_count == calls == 1
    assert landing.read_bytes() == body
    checkpoint = next((tmp_path / "data" / "state").rglob("*.json"))
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert set(saved) == {
        "public_scope", "call_count", "response_bytes", "response_body_sha256",
        "captured_at_utc", "typed_outcome",
    }
    assert saved["typed_outcome"] == "COMPLETED"
    assert saved["captured_at_utc"] == "2026-08-20T14:30:00Z"
    assert parser_bodies == [body]

    def must_not_fetch() -> bytes:
        raise AssertionError("completed scope must replay before fetch")

    replay = run_landing_first_financial_statement_pilot(
        tmp_path, approved=True, fetch=must_not_fetch, scope=scope,
        captured_at_utc="2040-01-01T00:00:00+09:00", parser=parser,
    )
    assert replay.typed_outcome == "NOOP_API_ZERO_REPLAY"
    assert replay.call_count == 0
    assert calls == 1
    assert parser_bodies == [body, body]
    assert completed.parsed.rows == replay.parsed.rows


def test_parse_failure_keeps_immutable_landing_and_records_only_sanitized_failure(tmp_path: Path):
    calls = 0
    body = b'{"status":"000","list":[]}'

    def fetch() -> bytes:
        nonlocal calls
        calls += 1
        return body

    with pytest.raises(OpenDartFinancialStatementError, match="PARSE_FAILED"):
        run_landing_first_financial_statement_pilot(
            tmp_path, approved=True, fetch=fetch, scope=FinancialStatementPilotScope(),
            captured_at_utc="2026-08-20T14:30:00Z",
        )
    assert calls == 1
    landing = _landing_path(tmp_path, FinancialStatementPilotScope())
    assert landing.read_bytes() == body
    failure = next((tmp_path / "data" / "state" / "opendart_financial_statement_pilot" / "failures").glob("*.json"))
    saved = json.loads(failure.read_text(encoding="utf-8"))
    assert saved["typed_outcome"] == "PARSE_FAILED"
    assert set(saved) == {
        "public_scope", "call_count", "response_bytes", "response_body_sha256",
        "captured_at_utc", "typed_outcome",
    }

    def must_not_fetch() -> bytes:
        raise AssertionError("immutable failed Landing must not be overwritten")

    with pytest.raises(OpenDartFinancialStatementError, match="ORPHANED_IMMUTABLE_LANDING_REVIEW_REQUIRED"):
        run_landing_first_financial_statement_pilot(
            tmp_path, approved=True, fetch=must_not_fetch, scope=FinancialStatementPilotScope(),
            captured_at_utc="2026-08-20T14:30:00Z",
        )


def test_landing_commit_failure_is_typed_and_counts_only_one_fetch(tmp_path: Path, monkeypatch):
    from stock_data.providers import opendart_financial_statement_pilot as module

    calls = 0

    def fetch() -> bytes:
        nonlocal calls
        calls += 1
        return _body()

    def fail_commit(path: Path, body: bytes) -> None:
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(module, "_commit_immutable_bytes", fail_commit)
    with pytest.raises(OpenDartFinancialStatementError, match="LANDING_COMMIT_FAILED"):
        run_landing_first_financial_statement_pilot(
            tmp_path, approved=True, fetch=fetch, scope=FinancialStatementPilotScope(),
            captured_at_utc="2026-08-20T14:30:00Z",
        )
    assert calls == 1
    assert not _landing_path(tmp_path, FinancialStatementPilotScope()).exists()


def test_initial_capture_time_must_be_timezone_aware_before_fetch(tmp_path: Path):
    calls = 0

    def fetch() -> bytes:
        nonlocal calls
        calls += 1
        return _body()

    with pytest.raises(OpenDartFinancialStatementError, match="CAPTURE_TIME_TIMEZONE_REQUIRED"):
        run_landing_first_financial_statement_pilot(
            tmp_path, approved=True, fetch=fetch, scope=FinancialStatementPilotScope(),
            captured_at_utc="2026-08-20T14:30:00",
        )
    assert calls == 0
