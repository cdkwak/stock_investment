from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event
from time import monotonic
from zoneinfo import ZoneInfo

import pytest

from stock_data.orchestration import toss_account_snapshot as module
from stock_data.orchestration.account_privacy import (
    remove_retained_account_snapshots,
)
from stock_data.orchestration.toss_account_runtime import (
    TossAccountRuntimeState,
    TossAccountRuntimeWiring,
    run_toss_account_daily,
)
from stock_data.orchestration.toss_account_snapshot import (
    AccountRefreshTrigger,
    TossAccountSnapshotRefresher,
    persist_account_snapshot_atomic,
    recover_incomplete_account_transactions,
)
from stock_data.providers.tossinvest import (
    TossInvestAPIResponse,
    TossInvestRateLimit,
    TossInvestTimeoutError,
    normalize_holdings_payload,
)


def payload() -> dict:
    return {"result": {
        "totalPurchaseAmount": {"krw": "2000", "usd": None},
        "marketValue": {
            "amount": {"krw": "2200", "usd": None},
            "amountAfterCost": {"krw": "2180", "usd": None},
        },
        "profitLoss": {
            "amount": {"krw": "200", "usd": None},
            "amountAfterCost": {"krw": "180", "usd": None},
            "rate": "0.1", "rateAfterCost": "0.09",
        },
        "dailyProfitLoss": {
            "amount": {"krw": "50", "usd": None}, "rate": "0.02",
        },
        "items": [{
            "symbol": "005930", "name": "Fixture", "marketCountry": "KR",
            "currency": "KRW", "quantity": "2", "lastPrice": "1100",
            "averagePurchasePrice": "1000",
            "marketValue": {"purchaseAmount": "2000", "amount": "2200", "amountAfterCost": "2180"},
            "profitLoss": {"amount": "200", "amountAfterCost": "180", "rate": "0.1", "rateAfterCost": "0.09"},
            "dailyProfitLoss": {"amount": "50", "rate": "0.02"},
            "cost": {"commission": "10", "tax": "10"},
        }],
    }}


class FakeClient:
    def __init__(self, responses=None):
        self.account_request_count = 0
        self.responses = list(responses or [payload()])
        self.selected: list[int] = []

    def brokerage_account_seq(self):
        self.account_request_count += 1
        return 7

    def get_holdings(self, *, account_seq):
        self.account_request_count += 1
        self.selected.append(account_seq)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return TossInvestAPIResponse(
            200, value, TossInvestRateLimit(group="ASSET", limit=5)
        )

    def get_buying_power(self, *, account_seq, currency):
        self.account_request_count += 1
        return TossInvestAPIResponse(
            200,
            {"result": {
                "currency": currency,
                "cashBuyingPower": "5000000" if currency == "KRW" else "3500.5",
            }},
            TossInvestRateLimit(group="ORDER_INFO", limit=5),
        )


def test_refresh_persists_only_sanitized_contract_projection(tmp_path):
    client = FakeClient()
    ticks = iter([10.0])
    refresher = TossAccountSnapshotRefresher(
        project_root=tmp_path, client=client,
        clock=lambda: datetime(2026, 8, 20, 1, 2, 3, tzinfo=timezone.utc),
        monotonic=lambda: next(ticks),
    )

    result = refresher.refresh(AccountRefreshTrigger.STARTUP)

    assert result.status == "SUCCEEDED" and result.account_calls == 4
    assert client.selected == [7]
    normalized = json.loads(
        (tmp_path / "data/normalized/toss_account_snapshot/latest.json").read_text(encoding="utf-8")
    )
    state = json.loads(
        (tmp_path / "data/state/toss_account_snapshot.json").read_text(encoding="utf-8")
    )
    landing = json.loads((tmp_path / state["landing"]).read_text(encoding="utf-8"))
    for document in (normalized, state, landing):
        rendered = json.dumps(document)
        assert "accountNo" not in rendered and "accountSeq" not in rendered
        assert "token" not in rendered and "secret" not in rendered
    assert landing["capture_kind"] == "SANITIZED_CONTRACT_PROJECTION"
    assert normalized["cash_balance"] is None
    assert normalized["buying_power"] == [
        {
            "cash_buying_power": "5000000",
            "currency": "KRW",
            "source_operation": "getBuyingPower",
        },
        {
            "cash_buying_power": "3500.5",
            "currency": "USD",
            "source_operation": "getBuyingPower",
        },
    ]
    history_files = list(
        (tmp_path / "data/local/account_value_history/toss_self").glob("*.json")
    )
    assert len(history_files) == 1
    history = json.loads(history_files[0].read_text(encoding="utf-8"))
    assert {row["metric"] for row in history["currencies"]} == {
        "OBSERVABLE_COMPONENT_SUM"
    }
    history_text = json.dumps(history)
    assert "accountNo" not in history_text and "accountSeq" not in history_text
    assert "positions" not in history_text and "items" not in history_text


def test_valid_empty_holdings_is_a_successful_three_call_snapshot(tmp_path):
    empty = payload()
    result = empty["result"]
    result["totalPurchaseAmount"] = {"krw": "0", "usd": None}
    result["marketValue"] = {
        "amount": {"krw": "0", "usd": None},
        "amountAfterCost": {"krw": "0", "usd": None},
    }
    result["profitLoss"] = {
        "amount": {"krw": "0", "usd": None},
        "amountAfterCost": {"krw": "0", "usd": None},
        "rate": "0", "rateAfterCost": "0",
    }
    result["dailyProfitLoss"] = {
        "amount": {"krw": "0", "usd": None}, "rate": "0",
    }
    result["items"] = []
    client = FakeClient([empty])
    refresher = TossAccountSnapshotRefresher(
        project_root=tmp_path, client=client, account_seq=7,
        clock=lambda: datetime(2026, 8, 26, 1, tzinfo=timezone.utc),
        monotonic=lambda: 10.0,
    )

    refresh = refresher.refresh(AccountRefreshTrigger.PERIODIC)

    assert refresh.status == "SUCCEEDED" and refresh.account_calls == 3
    normalized = json.loads(
        (tmp_path / "data/normalized/toss_account_snapshot/latest.json").read_text(
            encoding="utf-8"
        )
    )
    assert normalized["positions"] == []


def test_network_failure_preserves_prior_snapshot_and_state(tmp_path):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    normalized.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-normalized")
    state.write_bytes(b"prior-state")
    client = FakeClient([TossInvestTimeoutError("safe failure")])
    refresher = TossAccountSnapshotRefresher(
        project_root=tmp_path, client=client, account_seq=7,
        monotonic=lambda: 10.0,
    )

    result = refresher.refresh(AccountRefreshTrigger.MANUAL)

    assert result.status == "FAILED_PRESERVED_PRIOR" and result.account_calls == 1
    assert normalized.read_bytes() == b"prior-normalized"
    assert state.read_bytes() == b"prior-state"
    assert "safe failure" not in repr(result)


def test_concurrent_refreshes_coalesce_before_second_provider_call(tmp_path):
    entered = Event()
    release = Event()

    class BlockingClient(FakeClient):
        def get_holdings(self, *, account_seq):
            entered.set()
            assert release.wait(timeout=5)
            return super().get_holdings(account_seq=account_seq)

    client = BlockingClient()
    refreshers = [
        TossAccountSnapshotRefresher(
            project_root=tmp_path, client=client, account_seq=7,
            clock=lambda: datetime(2026, 8, 26, 1, tzinfo=timezone.utc),
            monotonic=lambda: 10.0,
        )
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(refreshers[0].refresh, AccountRefreshTrigger.MANUAL)
        assert entered.wait(timeout=5)
        second = pool.submit(refreshers[1].refresh, AccountRefreshTrigger.PERIODIC)
        contender = second.result(timeout=0.25)
        assert contender.status == "NOOP_CONCURRENT_REFRESH"
        assert contender.reason == "CONCURRENT_REFRESH_IN_PROGRESS"
        assert contender.account_calls == contender.token_calls == 0
        assert client.account_request_count == 0
        release.set()
        results = [first.result(timeout=5), contender]

    assert sorted(result.status for result in results) == [
        "NOOP_CONCURRENT_REFRESH", "SUCCEEDED",
    ]
    assert sum(result.account_calls for result in results) == 3
    assert client.account_request_count == 3


def test_privacy_removal_waits_for_refresh_then_invalidates_same_refresher(tmp_path):
    entered = Event()
    release = Event()

    class BlockingClient(FakeClient):
        def get_holdings(self, *, account_seq):
            entered.set()
            assert release.wait(timeout=5)
            return super().get_holdings(account_seq=account_seq)

    client = BlockingClient()
    refresher = TossAccountSnapshotRefresher(
        project_root=tmp_path, client=client, account_seq=7,
        clock=lambda: datetime(2026, 8, 26, 1, tzinfo=timezone.utc),
        monotonic=lambda: 10.0,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        refresh = pool.submit(refresher.refresh, AccountRefreshTrigger.MANUAL)
        assert entered.wait(timeout=5)
        removal = pool.submit(remove_retained_account_snapshots, tmp_path)
        with pytest.raises(FutureTimeout):
            removal.result(timeout=0.1)
        release.set()
        assert refresh.result(timeout=5).status == "SUCCEEDED"
        assert removal.result(timeout=5).status == "REMOVED"

    assert not (tmp_path / "data/normalized/toss_account_snapshot/latest.json").exists()
    assert not (tmp_path / "data/state/toss_account_snapshot.json").exists()
    assert not list((tmp_path / "data/landing/tossinvest/account_snapshot").glob("*.json"))
    calls_before = client.account_request_count
    invalidated = refresher.refresh(AccountRefreshTrigger.MANUAL)
    assert invalidated.status == "NOOP_PRIVACY_REMOVED"
    assert invalidated.account_calls == invalidated.token_calls == 0
    assert client.account_request_count == calls_before


def test_failing_leader_returns_concurrent_contender_immediately_without_api(tmp_path):
    entered = Event()
    release = Event()

    class FailingBlockingClient(FakeClient):
        def get_holdings(self, *, account_seq):
            entered.set()
            assert release.wait(timeout=5)
            self.account_request_count += 1
            raise TossInvestTimeoutError("fixture failure")

    client = FailingBlockingClient()
    refreshers = [
        TossAccountSnapshotRefresher(
            project_root=tmp_path, client=client, account_seq=7,
            clock=lambda: datetime(2026, 8, 26, tzinfo=timezone.utc),
            monotonic=lambda: 10.0,
        )
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(refreshers[0].refresh, AccountRefreshTrigger.MANUAL)
        assert entered.wait(timeout=5)
        started = monotonic()
        contender = pool.submit(
            refreshers[1].refresh, AccountRefreshTrigger.PERIODIC,
        ).result(timeout=0.25)
        assert monotonic() - started < 0.25
        assert contender.status == "NOOP_CONCURRENT_REFRESH"
        assert contender.account_calls == contender.token_calls == 0
        assert client.account_request_count == 0
        release.set()
        leader = first.result(timeout=5)

    assert leader.status == "FAILED_PRESERVED_PRIOR"
    assert leader.account_calls == client.account_request_count == 1


def test_daily_base_exception_after_committed_projection_restores_prior_bytes(tmp_path):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-valid")

    def refresh(trigger):
        buying_power = [
            module.normalize_buying_power_payload(
                {"result": {"currency": currency, "cashBuyingPower": "0"}},
                expected_currency=currency,
            )
            for currency in ("KRW", "USD")
        ]
        snapshot = module.attach_buying_power(
            normalize_holdings_payload(
                payload(), collected_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
            ),
            buying_power,
        )
        persist_account_snapshot_atomic(tmp_path, snapshot, trigger=trigger)
        raise _InjectedInterruption()

    class _InjectedInterruption(BaseException):
        pass

    report = run_toss_account_daily(
        tmp_path, {}, now=datetime(2026, 8, 26, 7, tzinfo=ZoneInfo("Asia/Seoul")),
        runtime_builder=lambda *args, **kwargs: TossAccountRuntimeWiring(
            TossAccountRuntimeState.ENABLED, refresh,
        ),
    )

    assert report["status"] == "TERMINAL_FAILURE"
    assert report["reason"] == "SCHEDULE_INTERRUPTED_AFTER_COMMIT"
    assert report["token_calls"] is report["account_calls"] is None
    assert normalized.read_bytes() == b"prior-valid"
    receipt = json.loads((tmp_path / report["receipt"]).read_text(encoding="utf-8"))
    assert receipt["normalized"] is receipt["normalized_sha256"] is None
    assert receipt["token_calls"] is receipt["account_calls"] is None


def test_privacy_removal_waiting_on_interrupted_daily_never_restores_preimage(tmp_path):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-valid")
    entered = Event()
    release = Event()

    class InjectedInterruption(BaseException):
        pass

    def refresh(trigger):
        entered.set()
        assert release.wait(timeout=5)
        normalized.write_bytes(b"partial-new")
        raise InjectedInterruption()

    with ThreadPoolExecutor(max_workers=2) as pool:
        daily = pool.submit(
            run_toss_account_daily,
            tmp_path,
            {},
            now=datetime(2026, 8, 26, 7, tzinfo=ZoneInfo("Asia/Seoul")),
            runtime_builder=lambda *args, **kwargs: TossAccountRuntimeWiring(
                TossAccountRuntimeState.ENABLED, refresh,
            ),
        )
        assert entered.wait(timeout=5)
        removal = pool.submit(remove_retained_account_snapshots, tmp_path)
        with pytest.raises(FutureTimeout):
            removal.result(timeout=0.1)
        release.set()
        assert daily.result(timeout=5)["status"] == "TERMINAL_FAILURE"
        assert removal.result(timeout=5).status == "REMOVED"

    assert not normalized.exists()


def test_promotion_exception_rolls_back_landing_normalized_and_state(tmp_path):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    normalized.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-normalized")
    state.write_bytes(b"prior-state")
    snapshot = normalize_holdings_payload(
        payload(), collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
    )

    def fail(step: str) -> None:
        if step == "PROMOTED_NORMALIZED":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        persist_account_snapshot_atomic(
            tmp_path, snapshot, trigger=AccountRefreshTrigger.MANUAL, step_hook=fail
        )

    assert normalized.read_bytes() == b"prior-normalized"
    assert state.read_bytes() == b"prior-state"
    assert list((tmp_path / "data/landing/tossinvest/account_snapshot").glob("*.json")) == []
    journal = next((tmp_path / "data/state/transactions/toss_account_snapshot").glob("*.json"))
    assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "ROLLED_BACK"


def test_base_exception_after_promotion_restores_prior_and_cleans_stage(tmp_path):
    class InjectedInterruption(BaseException):
        pass

    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    normalized.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-normalized")
    state.write_bytes(b"prior-state")
    snapshot = normalize_holdings_payload(
        payload(), collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
    )

    def interrupt(step: str) -> None:
        if step == "PROMOTED_NORMALIZED":
            raise InjectedInterruption

    with pytest.raises(InjectedInterruption):
        persist_account_snapshot_atomic(
            tmp_path, snapshot, trigger=AccountRefreshTrigger.MANUAL,
            step_hook=interrupt,
        )

    assert normalized.read_bytes() == b"prior-normalized"
    assert state.read_bytes() == b"prior-state"
    assert not any((tmp_path / "data/staging/toss_account_snapshot").glob("*"))
    journal = next((
        tmp_path / "data/state/transactions/toss_account_snapshot"
    ).glob("*.json"))
    assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "ROLLED_BACK"


def test_recovery_refuses_journal_target_outside_exact_toss_boundaries(tmp_path):
    outside = tmp_path / "unrelated.json"
    outside.write_bytes(b"must-remain")
    stage = tmp_path / "data/staging/toss_account_snapshot/txn"
    (stage / "backup").mkdir(parents=True)
    journal_path = tmp_path / "data/state/transactions/toss_account_snapshot/txn.json"
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text(json.dumps({
        "schema_version": 1,
        "transaction_id": "txn",
        "status": "PROMOTING",
        "stage": "data/staging/toss_account_snapshot/txn",
        "targets": {
            "landing": "unrelated.json",
            "normalized": "data/normalized/toss_account_snapshot/latest.json",
            "state": "data/state/toss_account_snapshot.json",
        },
        "payload_sha256": "0" * 64,
    }), encoding="utf-8")

    assert recover_incomplete_account_transactions(tmp_path) == 0
    assert outside.read_bytes() == b"must-remain"
    assert stage.exists()


def test_restart_recovers_an_incomplete_transaction(tmp_path):
    stage = tmp_path / "data/staging/toss_account_snapshot/txn"
    backup = stage / "backup"
    backup.mkdir(parents=True)
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    landing = tmp_path / "data/landing/tossinvest/account_snapshot/new.json"
    normalized.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    landing.parent.mkdir(parents=True)
    normalized.write_bytes(b"partial-new")
    state.write_bytes(b"partial-new-state")
    landing.write_bytes(b"partial-new-landing")
    (backup / "normalized.json").write_bytes(b"prior-normalized")
    (backup / "state.json").write_bytes(b"prior-state")
    journal_path = tmp_path / "data/state/transactions/toss_account_snapshot/txn.json"
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text(json.dumps({
        "schema_version": 1, "transaction_id": "txn", "status": "PROMOTING",
        "stage": "data/staging/toss_account_snapshot/txn",
        "targets": {
            "landing": "data/landing/tossinvest/account_snapshot/new.json",
            "normalized": "data/normalized/toss_account_snapshot/latest.json",
            "state": "data/state/toss_account_snapshot.json",
        },
        "payload_sha256": "0" * 64,
    }), encoding="utf-8")

    assert recover_incomplete_account_transactions(tmp_path) == 1

    assert normalized.read_bytes() == b"prior-normalized"
    assert state.read_bytes() == b"prior-state"
    assert not landing.exists() and not stage.exists()
    assert json.loads(journal_path.read_text(encoding="utf-8"))["status"] == "RECOVERED"


def test_prepared_partial_backup_recovery_preserves_every_prior_target(tmp_path):
    stage = tmp_path / "data/staging/toss_account_snapshot/txn"
    backup = stage / "backup"
    backup.mkdir(parents=True)
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    landing = tmp_path / "data/landing/tossinvest/account_snapshot/new.json"
    state.parent.mkdir(parents=True)
    (backup / "normalized.json").write_bytes(b"prior-normalized")
    state.write_bytes(b"prior-state")
    journal_path = tmp_path / "data/state/transactions/toss_account_snapshot/txn.json"
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text(json.dumps({
        "schema_version": 1, "transaction_id": "txn", "status": "PREPARED",
        "stage": "data/staging/toss_account_snapshot/txn",
        "targets": {
            "landing": "data/landing/tossinvest/account_snapshot/new.json",
            "normalized": "data/normalized/toss_account_snapshot/latest.json",
            "state": "data/state/toss_account_snapshot.json",
        },
        "payload_sha256": "0" * 64,
    }), encoding="utf-8")

    assert recover_incomplete_account_transactions(tmp_path) == 1

    assert normalized.read_bytes() == b"prior-normalized"
    assert state.read_bytes() == b"prior-state"
    assert not landing.exists() and not stage.exists()
    assert json.loads(journal_path.read_text(encoding="utf-8"))["status"] == "RECOVERED"


def test_promoting_without_prior_backups_removes_every_partial_new_target(tmp_path):
    stage = tmp_path / "data/staging/toss_account_snapshot/txn"
    (stage / "backup").mkdir(parents=True)
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    landing = tmp_path / "data/landing/tossinvest/account_snapshot/new.json"
    for target in (normalized, state, landing):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"partial-new")
    journal_path = tmp_path / "data/state/transactions/toss_account_snapshot/txn.json"
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text(json.dumps({
        "schema_version": 1, "transaction_id": "txn", "status": "PROMOTING",
        "stage": "data/staging/toss_account_snapshot/txn",
        "targets": {
            "landing": "data/landing/tossinvest/account_snapshot/new.json",
            "normalized": "data/normalized/toss_account_snapshot/latest.json",
            "state": "data/state/toss_account_snapshot.json",
        },
        "payload_sha256": "0" * 64,
    }), encoding="utf-8")

    assert recover_incomplete_account_transactions(tmp_path) == 1

    assert not normalized.exists() and not state.exists() and not landing.exists()
    assert not stage.exists()
    assert json.loads(journal_path.read_text(encoding="utf-8"))["status"] == "RECOVERED"


def test_overlapping_persistence_never_recovers_a_live_transaction(tmp_path):
    first = normalize_holdings_payload(
        payload(), collected_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc)
    )
    second = normalize_holdings_payload(
        payload(), collected_at=datetime(2026, 8, 20, 2, tzinfo=timezone.utc)
    )
    first_promoted_landing = Event()
    release_first = Event()

    def pause_first(step: str) -> None:
        if step == "PROMOTED_LANDING":
            first_promoted_landing.set()
            assert release_first.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            persist_account_snapshot_atomic,
            tmp_path,
            first,
            trigger=AccountRefreshTrigger.MANUAL,
            step_hook=pause_first,
        )
        assert first_promoted_landing.wait(timeout=5)
        second_future = pool.submit(
            persist_account_snapshot_atomic,
            tmp_path,
            second,
            trigger=AccountRefreshTrigger.MANUAL,
        )
        with pytest.raises(FutureTimeout):
            second_future.result(timeout=0.1)
        release_first.set()
        assert first_future.result(timeout=5).endswith("latest.json")
        assert second_future.result(timeout=5).endswith("latest.json")

    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    assert normalized.is_file() and state.is_file()
    assert json.loads(normalized.read_text(encoding="utf-8"))["collected_at"] == (
        second["collected_at"]
    )
    journals = tmp_path / "data/state/transactions/toss_account_snapshot"
    assert {
        json.loads(path.read_text(encoding="utf-8"))["status"]
        for path in journals.glob("*.json")
    } == {"SUCCEEDED"}


def test_backup_failure_rolls_back_moved_prior_and_preserves_unmoved_prior(
    tmp_path, monkeypatch,
):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    normalized.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-normalized")
    state.write_bytes(b"prior-state")
    snapshot = normalize_holdings_payload(
        payload(), collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
    )
    original_replace = module.os.replace

    def fail_state_backup(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path == state and destination_path.name == "state.json" and (
            destination_path.parent.name == "backup"
        ):
            raise OSError("injected backup failure")
        return original_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_state_backup)
    with pytest.raises(OSError, match="injected backup failure"):
        persist_account_snapshot_atomic(
            tmp_path, snapshot, trigger=AccountRefreshTrigger.MANUAL,
        )

    assert normalized.read_bytes() == b"prior-normalized"
    assert state.read_bytes() == b"prior-state"
    assert list((tmp_path / "data/landing/tossinvest/account_snapshot").glob("*.json")) == []
    journal = next((
        tmp_path / "data/state/transactions/toss_account_snapshot"
    ).glob("*.json"))
    assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "ROLLED_BACK"


def test_repeated_refresh_uses_cached_selector_and_periodic_interval_guard(tmp_path):
    client = FakeClient([payload(), payload()])
    ticks = iter([10.0, 10.1, 11.0, 71.0])
    refresher = TossAccountSnapshotRefresher(
        project_root=tmp_path, client=client, periodic_interval_seconds=60,
        monotonic=lambda: next(ticks),
    )

    first = refresher.refresh(AccountRefreshTrigger.STARTUP)
    rate_gated = refresher.refresh(AccountRefreshTrigger.MANUAL)
    interval_gated = refresher.refresh(AccountRefreshTrigger.PERIODIC)
    periodic = refresher.refresh(AccountRefreshTrigger.PERIODIC)

    assert first.account_calls == 4
    assert rate_gated.status == "NOOP_RATE_GATED"
    assert interval_gated.status == "NOOP_INTERVAL_NOT_DUE"
    assert periodic.status == "SUCCEEDED" and periodic.account_calls == 3
    assert client.selected == [7, 7]
    landing = tmp_path / "data/landing/tossinvest/account_snapshot"
    assert len(list(landing.glob("*.json"))) == 1
