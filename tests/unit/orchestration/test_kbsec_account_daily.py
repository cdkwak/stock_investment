from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_data.orchestration.kbsec_account_daily import (
    KBSEC_ACCOUNT_DAILY_OPERATION,
    KBSEC_ACCOUNT_SNAPSHOT_PATH,
    run_kbsec_account_daily,
    strict_kbsec_account_daily_receipt,
)
from stock_data.providers.kbsec.client import KBSecResponse


KST = ZoneInfo("Asia/Seoul")
SCRIPT = Path("scripts/maintenance/run_kbsec_account_snapshot.py")
SPEC = importlib.util.spec_from_file_location("run_kbsec_account_snapshot", SCRIPT)
CLI = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CLI)


def _environment() -> dict[str, str]:
    return {
        "KBSEC_BASE_URL": "https://kb.example",
        "KBSEC_APP_KEY": "fixture-key",
        "KBSEC_APP_SECRET": "fixture-secret",
    }


def _payload() -> dict:
    return {
        "dataHeader": {
            "resultCode": "200", "processCode": "0011",
            "processTime": "20260827100000000",
        },
        "dataBody": {
            "grid_cnt1": "0", "tl_data_cnt": "0",
            "nt_asts_val_amt": "0", "scrts_nt_val_amt": "0",
            "byng_amt_sum": "0", "val_amt_sum": "0", "val_pl_sum": "0",
            "Record1": [],
        },
    }


class _Client:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.calls = 0

    def account_snapshot(self) -> KBSecResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        payload = deepcopy(_payload())
        return KBSecResponse("200", "0011", payload["dataBody"], payload, 200)


def test_dry_run_is_provider_free_and_selects_fixed_occurrence(tmp_path) -> None:
    client = _Client()

    report = run_kbsec_account_daily(
        tmp_path, _environment(),
        now=datetime(2026, 8, 27, 7, 10, tzinfo=KST), dry_run=True,
        client_factory=lambda **kwargs: client,
    )

    assert report["status"] == "DRY_RUN_READY"
    assert report["scheduled_for"] == "2026-08-27T07:10:00+09:00"
    assert report["supplier_calls"] == 0 and client.calls == 0
    assert not (tmp_path / "data").exists()


def test_success_is_identifier_free_digest_bound_and_occurrence_idempotent(tmp_path) -> None:
    first = _Client()
    clock = datetime(2026, 8, 27, 7, 10, tzinfo=KST)

    report = run_kbsec_account_daily(
        tmp_path, _environment(), now=clock,
        client_factory=lambda **kwargs: first,
    )

    assert report["status"] == "TERMINAL_SUCCESS"
    assert report["supplier_calls"] == 1 and first.calls == 1
    receipt_path = tmp_path / str(report["receipt"])
    receipt = strict_kbsec_account_daily_receipt(
        receipt_path, occurrence_date=clock.date(),
    )
    snapshot = tmp_path / KBSEC_ACCOUNT_SNAPSHOT_PATH
    assert receipt["operation"] == KBSEC_ACCOUNT_DAILY_OPERATION
    assert receipt["snapshot"] == KBSEC_ACCOUNT_SNAPSHOT_PATH
    assert receipt["snapshot_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    retained = receipt_path.read_text(encoding="utf-8") + snapshot.read_text(encoding="utf-8")
    assert "fixture-key" not in retained and "fixture-secret" not in retained

    duplicate = _Client()
    repeated = run_kbsec_account_daily(
        tmp_path, _environment(), now=clock,
        client_factory=lambda **kwargs: duplicate,
    )
    assert repeated["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert repeated["retained_status"] == "TERMINAL_SUCCESS"
    assert duplicate.calls == 0


def test_failure_preserves_prior_snapshot_and_terminalizes_without_target(tmp_path) -> None:
    first_clock = datetime(2026, 8, 27, 7, 10, tzinfo=KST)
    assert run_kbsec_account_daily(
        tmp_path, _environment(), now=first_clock,
        client_factory=lambda **kwargs: _Client(),
    )["status"] == "TERMINAL_SUCCESS"
    snapshot = tmp_path / KBSEC_ACCOUNT_SNAPSHOT_PATH
    prior = snapshot.read_bytes()
    failing = _Client(error=TimeoutError("must not escape"))

    report = run_kbsec_account_daily(
        tmp_path, _environment(),
        now=datetime(2026, 8, 28, 7, 10, tzinfo=KST),
        client_factory=lambda **kwargs: failing,
    )

    assert report["status"] == "TERMINAL_FAILURE"
    assert report["reason"] == "KB_ACCOUNT_SUPPLIER_TIMEOUT"
    assert report["supplier_calls"] == 1 and failing.calls == 1
    assert snapshot.read_bytes() == prior
    receipt = json.loads((tmp_path / str(report["receipt"])).read_text(encoding="utf-8"))
    assert receipt["snapshot"] is None and receipt["snapshot_sha256"] is None
    assert "must not escape" not in json.dumps(receipt)


def test_missing_configuration_claims_once_and_makes_no_client(tmp_path) -> None:
    factories = []
    clock = datetime(2026, 8, 27, 7, 10, tzinfo=KST)

    report = run_kbsec_account_daily(
        tmp_path, {}, now=clock,
        client_factory=lambda **kwargs: factories.append(kwargs),
    )

    assert report["status"] == "TERMINAL_INELIGIBLE"
    assert report["reason"] == "RUNTIME_CONFIG_REQUIRED"
    assert report["supplier_calls"] == 0 and factories == []


def test_cli_dry_run_rejects_naive_clock_and_prints_only_safe_failure(tmp_path, capsys) -> None:
    assert CLI.main([
        "--project-root", str(tmp_path), "--dry-run", "--as-of", "2026-08-27T07:10:00",
    ]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "operation": KBSEC_ACCOUNT_DAILY_OPERATION,
        "status": "CLI_FAILURE",
        "reason": "SANITIZED_INTERNAL_FAILURE",
        "supplier_calls": 0,
    }
