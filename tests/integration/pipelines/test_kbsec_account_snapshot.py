from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
from pathlib import Path
import threading

import pytest

from stock_data.gui.account_snapshot_service import (
    AccountSnapshotState,
    LocalAccountSnapshotService,
)
from stock_data.orchestration.kb_account_snapshot import (
    KBAccountSnapshotCoordinator,
    recover_incomplete_kb_account_transactions,
)
from stock_data.orchestration import kb_account_snapshot as subject
from stock_data.orchestration.account_privacy import remove_retained_account_snapshots


COLLECTED_AT = datetime(2026, 8, 25, 1, 2, 3, tzinfo=timezone.utc)


def _payload(*, empty: bool = False) -> dict:
    rows = [] if empty else [{
        "is_cd": "A005930",
        "is_nm": "삼성전자",
        "clsf": "현금",
        "ec_q_p6": "1.000000",
        "ordr_psbl_q_p6": "1.000000",
        "byng_avr_prc": "360050.00",
        "now_prc": "426500.00",
        "byng_amt": "360050",
        "val_amt": "426500",
        "val_pl": "66450",
        "accountNumber": "must-be-dropped",
    }]
    return {
        "dataHeader": {
            "resultCode": "200",
            "processCode": "0011",
            "processTime": "20260622162350500",
            "authorization": "must-be-dropped",
        },
        "dataBody": {
            "grid_cnt1": str(len(rows)),
            "tl_data_cnt": str(len(rows)),
            "nt_asts_val_amt": "0" if empty else "1066450",
            "scrts_nt_val_amt": "0" if empty else "426500",
            "byng_amt_sum": "0" if empty else "360050",
            "val_amt_sum": "0" if empty else "426500",
            "val_pl_sum": "0" if empty else "66450",
            "Record1": rows,
            "rawAccountSelector": "must-be-dropped",
        },
    }


def _coordinator(root: Path, supplier, **kwargs) -> KBAccountSnapshotCoordinator:
    return KBAccountSnapshotCoordinator(
        project_root=root,
        response_supplier=supplier,
        clock=lambda: COLLECTED_AT,
        **kwargs,
    )


def _local(root: Path) -> Path:
    return root / "data/local/account_snapshots/kb_self.json"


def _state(root: Path) -> Path:
    return root / "data/state/kbsec_account_snapshot.json"


def test_privacy_removal_waits_for_inflight_refresh_then_removes_commit(
    tmp_path: Path,
) -> None:
    supplier_started = threading.Event()
    release_supplier = threading.Event()

    def supply() -> dict:
        supplier_started.set()
        assert release_supplier.wait(timeout=5)
        return _payload()

    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(_coordinator(tmp_path, supply).refresh_manual)
        assert supplier_started.wait(timeout=5)
        removal = pool.submit(remove_retained_account_snapshots, tmp_path)
        with pytest.raises(FutureTimeout):
            removal.result(timeout=0.1)
        release_supplier.set()
        assert refresh.result(timeout=5).status == "SUCCEEDED"
        assert removal.result(timeout=5).status == "REMOVED"

    assert not _local(tmp_path).exists()
    assert not _state(tmp_path).exists()
    assert not any((
        tmp_path / "data/local/account_value_history/kb_self"
    ).glob("*.json"))
    assert not any((
        tmp_path / "data/landing/kbsec/account_snapshot"
    ).glob("*.json"))


def test_manual_refresh_commits_only_sanitized_projection_and_value_free_state(
    tmp_path: Path,
) -> None:
    calls = 0

    def supply() -> dict:
        nonlocal calls
        calls += 1
        return _payload()

    result = _coordinator(tmp_path, supply).refresh_manual()

    assert result.status == "SUCCEEDED"
    assert result.supplier_calls == calls == 1
    assert result.snapshot_path == "data/local/account_snapshots/kb_self.json"
    snapshot = json.loads(_local(tmp_path).read_text(encoding="utf-8"))
    assert snapshot["provider"] == "kbsec_open_api"
    assert snapshot["source_operation"] == "SSQM2952"
    assert snapshot["positions"][0]["symbol"] == "A005930"
    assert snapshot["positions"][0]["market_value"] == "426500"

    landing_files = list(
        (tmp_path / "data/landing/kbsec/account_snapshot").glob("*.json")
    )
    assert len(landing_files) == 1
    retained = landing_files[0].read_text(encoding="utf-8")
    assert "accountNumber" not in retained
    assert "rawAccountSelector" not in retained
    assert "authorization" not in retained
    assert "must-be-dropped" not in retained

    state = json.loads(_state(tmp_path).read_text(encoding="utf-8"))
    assert set(state) == {
        "schema_version",
        "status",
        "provider",
        "source_operation",
        "collected_at",
        "payload_sha256",
        "landing",
        "snapshot",
    }
    state_text = _state(tmp_path).read_text(encoding="utf-8")
    assert "positions" not in state_text
    assert "426500" not in state_text
    history_files = list(
        (tmp_path / "data/local/account_value_history/kb_self").glob("*.json")
    )
    assert len(history_files) == 1
    history = json.loads(history_files[0].read_text(encoding="utf-8"))
    assert history["currencies"][0]["metric"] == "TOTAL_ASSETS"
    history_text = json.dumps(history)
    assert "positions" not in history_text and "symbol" not in history_text
    assert "accountNumber" not in history_text and "authorization" not in history_text


def test_valid_empty_refresh_replaces_prior_positions_without_invention(
    tmp_path: Path,
) -> None:
    assert _coordinator(tmp_path, lambda: _payload()).refresh_manual().status == "SUCCEEDED"

    result = _coordinator(tmp_path, lambda: _payload(empty=True)).refresh_manual()

    assert result.status == "SUCCEEDED"
    snapshot = json.loads(_local(tmp_path).read_text(encoding="utf-8"))
    assert snapshot["positions"] == []
    assert snapshot["purchase_amount"] == "0"
    assert snapshot["cash_balance"] is None


def test_committed_projection_is_accepted_by_current_local_gui_reader(
    tmp_path: Path,
) -> None:
    assert _coordinator(tmp_path, lambda: _payload()).refresh_manual().status == "SUCCEEDED"

    view = LocalAccountSnapshotService(_local(tmp_path)).load()

    assert view.state is AccountSnapshotState.KB_READ_ONLY
    assert view.provider == "KB_SECURITIES"
    assert view.total_assets == 1_066_450
    assert view.positions[0].symbol == "A005930"


@pytest.mark.parametrize("failure", ["partial", "duplicate", "reconciliation"])
def test_invalid_provider_response_preserves_prior_snapshot(
    tmp_path: Path,
    failure: str,
) -> None:
    assert _coordinator(tmp_path, lambda: _payload()).refresh_manual().status == "SUCCEEDED"
    before = _local(tmp_path).read_bytes()
    bad = _payload()
    if failure == "partial":
        del bad["dataBody"]["Record1"][0]["now_prc"]
    elif failure == "duplicate":
        bad["dataBody"]["Record1"].append(dict(bad["dataBody"]["Record1"][0]))
        bad["dataBody"]["grid_cnt1"] = "2"
        bad["dataBody"]["tl_data_cnt"] = "2"
        bad["dataBody"]["byng_amt_sum"] = "720100"
        bad["dataBody"]["val_amt_sum"] = "853000"
        bad["dataBody"]["val_pl_sum"] = "132900"
    else:
        bad["dataBody"]["val_amt_sum"] = "426501"

    result = _coordinator(tmp_path, lambda: bad).refresh_manual()

    assert result.status == "FAILED_PRESERVED_PRIOR"
    assert result.reason == "KB_ACCOUNT_RESPONSE_REJECTED"
    assert result.supplier_calls == 1
    assert _local(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TimeoutError("private timeout detail"), "KB_ACCOUNT_SUPPLIER_TIMEOUT"),
        (PermissionError("private auth detail"), "KB_ACCOUNT_SUPPLIER_AUTH_FAILED"),
        (RuntimeError("private response detail"), "KB_ACCOUNT_SUPPLIER_FAILED"),
    ],
)
def test_supplier_failure_is_bounded_redacted_and_preserves_prior(
    tmp_path: Path,
    error: Exception,
    reason: str,
) -> None:
    assert _coordinator(tmp_path, lambda: _payload()).refresh_manual().status == "SUCCEEDED"
    before = _local(tmp_path).read_bytes()

    def fail() -> dict:
        raise error

    result = _coordinator(tmp_path, fail).refresh_manual()

    assert result.status == "FAILED_PRESERVED_PRIOR"
    assert result.reason == reason
    assert result.supplier_calls == 1
    assert _local(tmp_path).read_bytes() == before
    retained = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "data").rglob("*.json")
    )
    assert "private" not in retained


@pytest.mark.parametrize(
    "boundary",
    ["PREPARED", "PROMOTED_LANDING", "PROMOTED_SNAPSHOT", "PROMOTED_STATE"],
)
def test_interrupted_promotion_preserves_last_valid_snapshot(
    tmp_path: Path,
    boundary: str,
) -> None:
    assert _coordinator(tmp_path, lambda: _payload()).refresh_manual().status == "SUCCEEDED"
    before_snapshot = _local(tmp_path).read_bytes()
    before_state = _state(tmp_path).read_bytes()
    changed = _payload()
    changed["dataBody"]["Record1"][0]["now_prc"] = "500000"

    def interrupt(step: str) -> None:
        if step == boundary:
            raise RuntimeError("simulated interruption")

    result = _coordinator(
        tmp_path,
        lambda: changed,
        step_hook=interrupt,
    ).refresh_manual()

    assert result.status == "FAILED_PRESERVED_PRIOR"
    assert result.reason == "KB_ACCOUNT_PERSISTENCE_FAILED"
    assert _local(tmp_path).read_bytes() == before_snapshot
    assert _state(tmp_path).read_bytes() == before_state
    assert recover_incomplete_kb_account_transactions(tmp_path) in {0, 1}
    assert _local(tmp_path).read_bytes() == before_snapshot
    assert _state(tmp_path).read_bytes() == before_state


def test_double_failure_retries_rollback_and_preserves_prior_before_return(
    tmp_path: Path, monkeypatch,
) -> None:
    assert _coordinator(tmp_path, lambda: _payload()).refresh_manual().status == "SUCCEEDED"
    before_snapshot = _local(tmp_path).read_bytes()
    before_state = _state(tmp_path).read_bytes()
    changed = _payload()
    changed["dataBody"]["Record1"][0]["now_prc"] = "500000"
    real_replace = subject.os.replace
    failed_candidate = False
    failed_restore = False

    def fail_two_boundaries(source, destination) -> None:
        nonlocal failed_candidate, failed_restore
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not failed_candidate
            and source_path.name == "landing.json"
            and source_path.parent.name == "candidate"
        ):
            failed_candidate = True
            raise OSError("candidate promotion failed")
        if (
            failed_candidate
            and not failed_restore
            and destination_path == _local(tmp_path)
            and source_path.parent.name == "restore"
        ):
            failed_restore = True
            raise OSError("first rollback restore failed")
        real_replace(source, destination)

    monkeypatch.setattr(subject.os, "replace", fail_two_boundaries)

    result = _coordinator(tmp_path, lambda: changed).refresh_manual()

    assert result.status == "FAILED_PRESERVED_PRIOR"
    assert result.reason == "KB_ACCOUNT_PERSISTENCE_FAILED"
    assert failed_candidate and failed_restore
    assert _local(tmp_path).read_bytes() == before_snapshot
    assert _state(tmp_path).read_bytes() == before_state
    assert not list((tmp_path / "data/staging/kbsec_account_snapshot").iterdir())
    journals = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (
            tmp_path / "data/state/transactions/kbsec_account_snapshot"
        ).glob("*.json")
    ]
    assert any(item.get("status") == "ROLLED_BACK" for item in journals)


def test_persistent_incomplete_rollback_retains_backup_for_later_recovery(
    tmp_path: Path, monkeypatch,
) -> None:
    assert _coordinator(tmp_path, lambda: _payload()).refresh_manual().status == "SUCCEEDED"
    before_snapshot = _local(tmp_path).read_bytes()
    before_state = _state(tmp_path).read_bytes()
    changed = _payload()
    changed["dataBody"]["Record1"][0]["now_prc"] = "500000"
    real_replace = subject.os.replace
    failed_candidate = False
    block_restore = True

    def fail_until_recovery(source, destination) -> None:
        nonlocal failed_candidate
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not failed_candidate
            and source_path.name == "landing.json"
            and source_path.parent.name == "candidate"
        ):
            failed_candidate = True
            raise OSError("candidate promotion failed")
        if (
            block_restore
            and destination_path in {_local(tmp_path), _state(tmp_path)}
            and source_path.parent.name == "restore"
        ):
            raise OSError("rollback restore remains unavailable")
        real_replace(source, destination)

    monkeypatch.setattr(subject.os, "replace", fail_until_recovery)

    result = _coordinator(tmp_path, lambda: changed).refresh_manual()

    assert result.status == "FAILED_PRESERVED_PRIOR"
    stages = list((tmp_path / "data/staging/kbsec_account_snapshot").iterdir())
    assert len(stages) == 1
    journal_paths = list(
        (tmp_path / "data/state/transactions/kbsec_account_snapshot").glob("*.json")
    )
    promoting = [
        path for path in journal_paths
        if json.loads(path.read_text(encoding="utf-8")).get("status") == "PROMOTING"
    ]
    assert len(promoting) == 1

    block_restore = False
    assert recover_incomplete_kb_account_transactions(tmp_path) == 1
    assert _local(tmp_path).read_bytes() == before_snapshot
    assert _state(tmp_path).read_bytes() == before_state
    assert not stages[0].exists()
    assert json.loads(promoting[0].read_text(encoding="utf-8"))["status"] == "RECOVERED"


def test_unrecovered_valid_journal_blocks_new_supplier_and_promotion(
    tmp_path: Path, monkeypatch,
) -> None:
    assert _coordinator(tmp_path, lambda: _payload()).refresh_manual().status == "SUCCEEDED"
    before_snapshot = _local(tmp_path).read_bytes()
    before_state = _state(tmp_path).read_bytes()
    changed = _payload()
    changed["dataBody"]["Record1"][0]["now_prc"] = "500000"
    real_replace = subject.os.replace
    failed_candidate = False
    block_restore = True

    def fail_until_released(source, destination) -> None:
        nonlocal failed_candidate
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            not failed_candidate
            and source_path.name == "landing.json"
            and source_path.parent.name == "candidate"
        ):
            failed_candidate = True
            raise OSError("candidate promotion failed")
        if (
            block_restore
            and destination_path in {_local(tmp_path), _state(tmp_path)}
            and source_path.parent.name == "restore"
        ):
            raise OSError("rollback restore remains unavailable")
        real_replace(source, destination)

    monkeypatch.setattr(subject.os, "replace", fail_until_released)
    assert _coordinator(tmp_path, lambda: changed).refresh_manual().status == (
        "FAILED_PRESERVED_PRIOR"
    )

    supplier_calls = 0

    def newer_supplier() -> dict:
        nonlocal supplier_calls
        supplier_calls += 1
        return _payload()

    blocked = _coordinator(tmp_path, newer_supplier).refresh_manual()

    assert blocked.status == "FAILED_PRESERVED_PRIOR"
    assert blocked.reason == "KB_ACCOUNT_PERSISTENCE_FAILED"
    assert blocked.supplier_calls == supplier_calls == 0

    block_restore = False
    assert recover_incomplete_kb_account_transactions(tmp_path) == 1
    assert _local(tmp_path).read_bytes() == before_snapshot
    assert _state(tmp_path).read_bytes() == before_state


def test_concurrent_refresh_is_excluded_before_second_supplier_call(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    first_result = []
    first_calls = 0
    second_calls = 0

    def first_supplier() -> dict:
        nonlocal first_calls
        first_calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return _payload()

    def first_refresh() -> None:
        first_result.append(_coordinator(tmp_path, first_supplier).refresh_manual())

    worker = threading.Thread(target=first_refresh)
    worker.start()
    assert entered.wait(timeout=5)

    def second_supplier() -> dict:
        nonlocal second_calls
        second_calls += 1
        return _payload()

    second = _coordinator(
        tmp_path,
        second_supplier,
        lock_timeout_seconds=0,
    ).refresh_manual()
    release.set()
    worker.join(timeout=5)

    assert second.status == "FAILED_PRESERVED_PRIOR"
    assert second.reason == "KB_ACCOUNT_LOCK_TIMEOUT"
    assert second.supplier_calls == second_calls == 0
    assert first_calls == 1
    assert first_result[0].status == "SUCCEEDED"


def test_recovery_refuses_corrupt_or_out_of_root_journal(tmp_path: Path) -> None:
    journal_root = tmp_path / "data/state/transactions/kbsec_account_snapshot"
    journal_root.mkdir(parents=True)
    (journal_root / "corrupt.json").write_text("not json", encoding="utf-8")
    (journal_root / "escape.json").write_text(
        json.dumps({
            "status": "PROMOTING",
            "stage": "../outside",
            "targets": {
                "landing": "../outside",
                "snapshot": "../outside",
                "state": "../outside",
            },
        }),
        encoding="utf-8",
    )
    protected = tmp_path / "data/local/do-not-touch.json"
    protected.parent.mkdir(parents=True)
    protected.write_text("protected", encoding="utf-8")
    (journal_root / "malicious.json").write_text(
        json.dumps({
            "transaction_id": "malicious",
            "status": "PROMOTING",
            "stage": "data/staging/kbsec_account_snapshot/malicious",
            "targets": {
                "landing": "data/local/do-not-touch.json",
                "snapshot": "data/local/account_snapshots/kb_self.json",
                "state": "data/state/kbsec_account_snapshot.json",
            },
        }),
        encoding="utf-8",
    )

    assert recover_incomplete_kb_account_transactions(tmp_path) == 0
    assert (journal_root / "corrupt.json").exists()
    assert (journal_root / "escape.json").exists()
    assert protected.read_text(encoding="utf-8") == "protected"
