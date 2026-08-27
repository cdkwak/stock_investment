from __future__ import annotations

from copy import deepcopy

from stock_data.orchestration.kbsec_account_runtime import (
    KBSEC_ACCOUNT_REQUIRED_ENV_NAMES,
    KBSecAccountRuntimeState,
    build_kbsec_account_runtime,
    load_kbsec_account_environment,
)
from stock_data.orchestration.toss_account_snapshot import AccountRefreshTrigger
from stock_data.providers.kbsec.client import KBSecResponse


def _environment() -> dict[str, str]:
    return {
        "KBSEC_BASE_URL": "https://kb.example",
        "KBSEC_APP_KEY": "fixture-key",
        "KBSEC_APP_SECRET": "fixture-secret",
    }


def _empty_account_payload() -> dict:
    return {
        "dataHeader": {
            "resultCode": "200",
            "processCode": "0011",
            "processTime": "20260826144000000",
        },
        "dataBody": {
            "grid_cnt1": "0",
            "tl_data_cnt": "0",
            "nt_asts_val_amt": "0",
            "scrts_nt_val_amt": "0",
            "byng_amt_sum": "0",
            "val_amt_sum": "0",
            "val_pl_sum": "0",
            "Record1": [],
        },
    }


class _Client:
    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls = 0

    def account_snapshot(self) -> KBSecResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        payload = deepcopy(self.payload)
        return KBSecResponse("200", "0011", payload["dataBody"], payload, 200)


def test_missing_config_keeps_runtime_api_zero(tmp_path) -> None:
    factories = []
    wiring = build_kbsec_account_runtime(
        tmp_path, {}, client_factory=lambda **kwargs: factories.append(kwargs)
    )

    assert wiring.state is KBSecAccountRuntimeState.NOT_AVAILABLE_MISSING_CONFIG
    assert wiring.refresher is None and factories == []
    assert wiring.missing_names == (
        "KBSEC_BASE_URL", "KBSEC_APP_KEY", "KBSEC_APP_SECRET"
    )


def test_construction_and_startup_are_api_zero_then_manual_refresh_succeeds(tmp_path) -> None:
    client = _Client(_empty_account_payload())
    wiring = build_kbsec_account_runtime(
        tmp_path, _environment(), client_factory=lambda **kwargs: client
    )

    assert wiring.state is KBSecAccountRuntimeState.ENABLED
    assert client.calls == 0
    startup = wiring.refresher(AccountRefreshTrigger.STARTUP)
    assert startup.status == "FAILED_PRESERVED_PRIOR"
    assert startup.reason == "MANUAL_TRIGGER_REQUIRED"
    assert startup.supplier_calls == 0 and client.calls == 0

    result = wiring.refresher(AccountRefreshTrigger.MANUAL)
    assert result.status == "SUCCEEDED"
    assert result.supplier_calls == 1 and client.calls == 1
    saved = (tmp_path / "data/local/account_snapshots/kb_self.json").read_text(
        encoding="utf-8"
    )
    assert "fixture-key" not in saved
    assert "fixture-secret" not in saved


def test_periodic_refresh_uses_same_single_call_readonly_boundary(tmp_path) -> None:
    client = _Client(_empty_account_payload())
    wiring = build_kbsec_account_runtime(
        tmp_path, _environment(), client_factory=lambda **kwargs: client
    )

    result = wiring.refresher(AccountRefreshTrigger.PERIODIC)

    assert result.status == "SUCCEEDED"
    assert result.supplier_calls == 1 and client.calls == 1


def test_environment_loader_selects_only_approved_names(tmp_path) -> None:
    (tmp_path / ".env").write_text(
        "KBSEC_BASE_URL=https://file.example\n"
        "KBSEC_APP_KEY=file-key\n"
        "KBSEC_APP_SECRET=file-secret\n"
        "UNRELATED_SECRET=must-not-load\n",
        encoding="utf-8",
    )

    result = load_kbsec_account_environment(
        tmp_path, {"KBSEC_APP_KEY": "process-key", "OTHER": "ignored"},
    )

    assert set(result) == set(KBSEC_ACCOUNT_REQUIRED_ENV_NAMES)
    assert result == {
        "KBSEC_BASE_URL": "https://file.example",
        "KBSEC_APP_KEY": "process-key",
        "KBSEC_APP_SECRET": "file-secret",
    }


def test_manual_failure_preserves_prior_snapshot_bytes(tmp_path) -> None:
    first = _Client(_empty_account_payload())
    wiring = build_kbsec_account_runtime(
        tmp_path, _environment(), client_factory=lambda **kwargs: first
    )
    assert wiring.refresher(AccountRefreshTrigger.MANUAL).status == "SUCCEEDED"
    snapshot = tmp_path / "data/local/account_snapshots/kb_self.json"
    prior = snapshot.read_bytes()

    failing = _Client(error=TimeoutError("synthetic timeout"))
    wiring = build_kbsec_account_runtime(
        tmp_path, _environment(), client_factory=lambda **kwargs: failing
    )
    result = wiring.refresher(AccountRefreshTrigger.MANUAL)

    assert result.status == "FAILED_PRESERVED_PRIOR"
    assert result.reason == "KB_ACCOUNT_SUPPLIER_TIMEOUT"
    assert result.supplier_calls == 1 and failing.calls == 1
    assert snapshot.read_bytes() == prior
