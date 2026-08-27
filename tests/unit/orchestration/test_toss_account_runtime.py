from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from threading import Event
from time import monotonic
from zoneinfo import ZoneInfo

import pytest

from stock_data.orchestration import toss_account_runtime as runtime
from stock_data.orchestration.toss_account_runtime import (
    TOSS_ACCOUNT_CLIENT_ID_ENV,
    TOSS_ACCOUNT_CLIENT_SECRET_ENV,
    TOSS_ACCOUNT_ENV_NAMES,
    TOSS_ACCOUNT_SEQ_ENV,
    TossAccountRuntimeState,
    TossAccountRecoveryError,
    TossAccountRuntimeWiring,
    TossAccountScheduleError,
    build_toss_account_runtime,
    load_toss_account_environment,
    run_toss_account_daily,
)
from stock_data.orchestration.toss_account_snapshot import TossAccountRefreshResult
from stock_data.orchestration.account_privacy import account_snapshot_lifecycle_lock


NOW = datetime(2026, 8, 26, 7, 0, tzinfo=ZoneInfo("Asia/Seoul"))
CLI = Path(__file__).resolve().parents[3] / "scripts/maintenance/run_toss_account_snapshot.py"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("toss_account_snapshot_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingEnvironment(dict):
    def __init__(self, values):
        super().__init__(values)
        self.names_read: list[str] = []

    def get(self, name, default=None):
        self.names_read.append(name)
        return super().get(name, default)


class FakeClient:
    account_request_count = 0


def test_environment_loader_returns_only_three_approved_names_with_process_precedence(tmp_path):
    (tmp_path / ".env").write_text(
        "TOSSINVEST_CLIENT_ID=file-client\n"
        "TOSSINVEST_CLIENT_SECRET=file-secret\n"
        "TOSSINVEST_ACCOUNT_SEQ=7\n"
        "UNRELATED_SECRET=must-not-return\n",
        encoding="utf-8",
    )
    process = RecordingEnvironment({
        "TOSSINVEST_CLIENT_ID": "process-client",
        "UNRELATED_PROCESS_SECRET": "must-not-read",
    })

    loaded = load_toss_account_environment(tmp_path, process)

    assert set(loaded) == set(TOSS_ACCOUNT_ENV_NAMES)
    assert loaded == {
        TOSS_ACCOUNT_CLIENT_ID_ENV: "process-client",
        TOSS_ACCOUNT_CLIENT_SECRET_ENV: "file-secret",
        TOSS_ACCOUNT_SEQ_ENV: "7",
    }
    assert tuple(process.names_read) == TOSS_ACCOUNT_ENV_NAMES
    assert "UNRELATED_PROCESS_SECRET" not in process.names_read
    assert "must-not-return" not in repr(loaded)


def test_missing_runtime_config_is_api_zero_and_reads_only_named_process_values(tmp_path):
    environment = RecordingEnvironment({
        "UNRELATED_SECRET": "must-not-be-read",
        "TOSSINVEST_ACCOUNT_REFRESH_SECONDS": "0.2",
    })
    factory_calls = []

    wiring = build_toss_account_runtime(
        tmp_path,
        environment,
        client_factory=lambda **kwargs: factory_calls.append(kwargs),
    )

    assert wiring.state is TossAccountRuntimeState.NOT_AVAILABLE_MISSING_CONFIG
    assert wiring.refresher is None
    assert wiring.missing_names == (
        TOSS_ACCOUNT_CLIENT_ID_ENV,
        TOSS_ACCOUNT_CLIENT_SECRET_ENV,
        TOSS_ACCOUNT_SEQ_ENV,
    )
    assert tuple(environment.names_read) == TOSS_ACCOUNT_ENV_NAMES
    assert "UNRELATED_SECRET" not in environment.names_read
    assert "TOSSINVEST_ACCOUNT_REFRESH_SECONDS" not in environment.names_read
    assert factory_calls == []


def test_invalid_selector_never_constructs_client(tmp_path):
    base = {
        TOSS_ACCOUNT_CLIENT_ID_ENV: "fixture-client",
        TOSS_ACCOUNT_CLIENT_SECRET_ENV: "fixture-secret",
        TOSS_ACCOUNT_SEQ_ENV: "ambiguous",
    }
    factory_calls = []

    invalid_selector = build_toss_account_runtime(
        tmp_path, base, client_factory=lambda **kwargs: factory_calls.append(kwargs)
    )
    assert invalid_selector.state is TossAccountRuntimeState.NOT_AVAILABLE_INVALID_CONFIG
    assert invalid_selector.reason == "ACCOUNT_SELECTOR_INVALID"
    assert invalid_selector.refresher is None
    assert factory_calls == []


def test_complete_runtime_config_builds_retry_zero_coordinator_without_calling_it(tmp_path):
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(set(kwargs))
        return FakeClient()

    wiring = build_toss_account_runtime(
        tmp_path,
        {
            TOSS_ACCOUNT_CLIENT_ID_ENV: "fixture-client",
            TOSS_ACCOUNT_CLIENT_SECRET_ENV: "fixture-secret",
            TOSS_ACCOUNT_SEQ_ENV: "7",
        },
        client_factory=factory,
    )

    assert wiring.state is TossAccountRuntimeState.ENABLED
    assert wiring.enabled and wiring.refresher is not None
    assert factory_calls == [{"client_id", "client_secret"}]
    assert FakeClient.account_request_count == 0
    assert "fixture-client" not in repr(wiring)
    assert "fixture-secret" not in repr(wiring)


def test_runtime_factory_failure_is_secret_safe_and_disabled(tmp_path):
    class FactoryErrorClient:
        def __init__(self, **kwargs):
            raise ValueError("do not expose supplied values")

    wiring = build_toss_account_runtime(
        Path(tmp_path),
        {
            TOSS_ACCOUNT_CLIENT_ID_ENV: "fixture-client",
            TOSS_ACCOUNT_CLIENT_SECRET_ENV: "fixture-secret",
            TOSS_ACCOUNT_SEQ_ENV: "7",
        },
        client_factory=FactoryErrorClient,
    )

    assert wiring.state is TossAccountRuntimeState.NOT_AVAILABLE_INVALID_CONFIG
    assert wiring.refresher is None
    assert wiring.reason == "CLIENT_INITIALIZATION_FAILED"
    assert "fixture-client" not in repr(wiring)
    assert "fixture-secret" not in repr(wiring)


def _enabled_wiring(refresher):
    return TossAccountRuntimeWiring(TossAccountRuntimeState.ENABLED, refresher)


def test_daily_missing_config_is_terminal_api_zero_and_replay_constructs_nothing(tmp_path):
    factory_calls = []
    first = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        client_factory=lambda **kwargs: factory_calls.append(kwargs),
    )
    receipt_bytes = (tmp_path / first["receipt"]).read_bytes()
    second = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        client_factory=lambda **kwargs: factory_calls.append(kwargs),
    )
    assert first["status"] == "TERMINAL_INELIGIBLE"
    assert first["account_calls"] == first["token_calls"] == 0
    assert second["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert second["retained_status"] == "TERMINAL_INELIGIBLE"
    assert second["account_calls"] == second["token_calls"] == 0
    assert factory_calls == []
    assert (tmp_path / first["receipt"]).read_bytes() == receipt_bytes


def test_daily_success_requires_exact_calls_and_retains_sanitized_receipt(tmp_path):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_text('{"schema_version":1}\n', encoding="utf-8")
    triggers = []

    def refresh(trigger):
        triggers.append(trigger.value)
        return TossAccountRefreshResult(
            "SUCCEEDED", trigger, 3, token_calls=1,
            normalized_path="data/normalized/toss_account_snapshot/latest.json",
        )

    report = run_toss_account_daily(
        tmp_path,
        {"TOSSINVEST_CLIENT_ID": "fixture-client", "TOSSINVEST_CLIENT_SECRET": "fixture-secret", "TOSSINVEST_ACCOUNT_SEQ": "7654321"},
        now=NOW,
        runtime_builder=lambda *args, **kwargs: _enabled_wiring(refresh),
    )
    assert report["status"] == "TERMINAL_SUCCESS"
    assert report["account_calls"] == 3 and report["token_calls"] == 1
    assert triggers == ["PERIODIC"]
    rendered = (tmp_path / report["receipt"]).read_text(encoding="utf-8")
    for forbidden in ("fixture-client", "fixture-secret", "7654321"):
        assert forbidden not in rendered and forbidden not in repr(report)
    receipt = json.loads(rendered)
    assert receipt["normalized_sha256"]
    assert receipt["status"] == "TERMINAL_SUCCESS"


@pytest.mark.parametrize("account_calls,token_calls", [(4, 1), (3, 2), (2, 1)])
def test_daily_success_with_wrong_call_budget_fails_closed(
    tmp_path, account_calls, token_calls,
):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_text("{}", encoding="utf-8")

    def refresh(trigger):
        return TossAccountRefreshResult(
            "SUCCEEDED", trigger, account_calls, token_calls=token_calls,
            normalized_path="data/normalized/toss_account_snapshot/latest.json",
        )

    report = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        runtime_builder=lambda *args, **kwargs: _enabled_wiring(refresh),
    )
    assert report["status"] == "TERMINAL_FAILURE"
    assert report["outcome"] == "SCHEDULE_INTERNAL_FAILURE"


def test_daily_provider_failure_is_terminal_and_preserves_prior_bytes(tmp_path):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior")

    def refresh(trigger):
        return TossAccountRefreshResult(
            "FAILED_PRESERVED_PRIOR", trigger, 1, token_calls=1,
            reason="ACCOUNT_REFRESH_FAILED_CLOSED",
        )

    report = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        runtime_builder=lambda *args, **kwargs: _enabled_wiring(refresh),
    )
    assert report["status"] == "TERMINAL_FAILURE"
    assert report["outcome"] == "FAILED_PRESERVED_PRIOR"
    assert normalized.read_bytes() == b"prior"


def test_daily_process_base_exception_terminalizes_receipt_and_replay_is_api_zero(tmp_path):
    calls = []

    class Crash(BaseException):
        pass

    def refresh(trigger):
        calls.append(trigger)
        raise Crash()

    first = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        runtime_builder=lambda *args, **kwargs: _enabled_wiring(refresh),
    )
    replay = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        runtime_builder=lambda *args, **kwargs: pytest.fail("must not rebuild"),
    )
    assert first["status"] == "TERMINAL_FAILURE"
    assert first["outcome"] == "SCHEDULE_INTERNAL_FAILURE"
    assert replay["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert replay["retained_status"] == "TERMINAL_FAILURE"
    assert replay["account_calls"] == replay["token_calls"] == 0
    assert len(calls) == 1
    receipt = (tmp_path / first["receipt"]).read_text(encoding="utf-8")
    assert "Crash" not in receipt
    assert json.loads(receipt)["status"] == "TERMINAL_FAILURE"


def test_daily_base_exception_with_partial_projection_restores_exact_prior_bytes(tmp_path):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    normalized.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-normalized")
    state.write_bytes(b"prior-state")

    class Crash(BaseException):
        pass

    def refresh(trigger):
        normalized.write_bytes(b"partial-normalized")
        state.write_bytes(b"partial-state")
        raise Crash()

    report = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        runtime_builder=lambda *args, **kwargs: _enabled_wiring(refresh),
    )

    assert report["status"] == "TERMINAL_FAILURE"
    assert report["token_calls"] is report["account_calls"] is None
    assert normalized.read_bytes() == b"prior-normalized"
    assert state.read_bytes() == b"prior-state"
    receipt = json.loads((tmp_path / report["receipt"]).read_text(encoding="utf-8"))
    assert receipt["normalized"] is receipt["normalized_sha256"] is None


def test_daily_interrupted_partial_restore_retries_exact_map_before_terminalizing(
    tmp_path, monkeypatch,
):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    state = tmp_path / "data/state/toss_account_snapshot.json"
    normalized.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-normalized")
    state.write_bytes(b"prior-state")

    class Crash(BaseException):
        pass

    def refresh(trigger):
        normalized.write_bytes(b"partial-normalized")
        state.write_bytes(b"partial-state")
        raise Crash()

    def partially_restore_then_fail(root, before):
        runtime._atomic_bytes(
            root / runtime._NORMALIZED_ACCOUNT_PATH,
            before[runtime._NORMALIZED_ACCOUNT_PATH],
        )
        raise OSError("injected intermediate restore interruption")

    monkeypatch.setattr(runtime, "_restore_projection_bytes", partially_restore_then_fail)

    first = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        runtime_builder=lambda *args, **kwargs: _enabled_wiring(refresh),
    )
    replay = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        runtime_builder=lambda *args, **kwargs: pytest.fail("must not rebuild"),
    )

    assert first["status"] == "TERMINAL_FAILURE"
    assert first["token_calls"] is first["account_calls"] is None
    assert normalized.read_bytes() == b"prior-normalized"
    assert state.read_bytes() == b"prior-state"
    assert replay["status"] == "NOOP_OCCURRENCE_ALREADY_CLAIMED"
    assert replay["retained_status"] == "TERMINAL_FAILURE"


@pytest.mark.parametrize("failure", ["exact_restore", "exact_readback"])
def test_daily_unverified_exact_recovery_never_writes_terminal_receipt(
    tmp_path, monkeypatch, failure,
):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-normalized")

    class Crash(BaseException):
        pass

    def refresh(trigger):
        normalized.write_bytes(b"partial-normalized")
        raise Crash()

    if failure == "exact_restore":
        def fail_exact_restore(root, before):
            raise OSError("injected persistent exact restore failure")

        monkeypatch.setattr(runtime, "_restore_projection_bytes_exact", fail_exact_restore)
    else:
        original_projection_bytes = runtime._projection_bytes
        read_count = 0

        def fail_exact_readback(root):
            nonlocal read_count
            read_count += 1
            if read_count >= 4:
                raise OSError("injected exact restore readback failure")
            return original_projection_bytes(root)

        monkeypatch.setattr(runtime, "_projection_bytes", fail_exact_readback)

    with pytest.raises(TossAccountRecoveryError):
        run_toss_account_daily(
            tmp_path, {}, now=NOW,
            runtime_builder=lambda *args, **kwargs: _enabled_wiring(refresh),
        )

    receipt = json.loads(
        (tmp_path / "data/state/toss_account_snapshot_occurrences/2026-08-26.json")
        .read_text(encoding="utf-8")
    )
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert "finished_at_utc" not in receipt
    replay = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        runtime_builder=lambda *args, **kwargs: pytest.fail("must not rebuild"),
    )
    assert replay["status"] == replay["retained_status"] == "RECOVERY_REQUIRED"
    assert replay["outcome"] == "SCHEDULE_INTERNAL_FAILURE"
    assert replay["reason"] == "ACCOUNT_PROJECTION_RECOVERY_REQUIRED"
    assert replay["token_calls"] is replay["account_calls"] is None


def test_daily_concurrent_lifecycle_holder_is_immediate_api_zero_and_preserves_prior(
    tmp_path,
):
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-valid")
    entered = Event()
    release = Event()

    def hold_lifecycle():
        with account_snapshot_lifecycle_lock(tmp_path):
            entered.set()
            assert release.wait(timeout=5)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_lifecycle)
        assert entered.wait(timeout=5)
        started = monotonic()
        report = run_toss_account_daily(
            tmp_path, {}, now=NOW,
            runtime_builder=lambda *args, **kwargs: pytest.fail("must not build"),
        )
        assert monotonic() - started < 0.25
        release.set()
        holder.result(timeout=5)

    assert report["status"] == "TERMINAL_FAILURE"
    assert report["outcome"] == "SCHEDULE_CONCURRENT_REFRESH"
    assert report["token_calls"] == report["account_calls"] == 0
    assert normalized.read_bytes() == b"prior-valid"


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version":1,"schema_version":1,"operation":"TOSS_ACCOUNT_READONLY_DAILY","occurrence_date":"2026-08-26","scheduled_for":"2026-08-26T07:00:00+09:00","claimed_at_utc":"2026-08-25T22:00:00+00:00","status":"CLAIMED_BEFORE_PROVIDER"}',
        '{"schema_version":1,"operation":"TOSS_ACCOUNT_READONLY_DAILY","occurrence_date":"2026-08-26","scheduled_for":"NOT_A_TIMESTAMP","claimed_at_utc":"2026-08-25T22:00:00+00:00","status":"CLAIMED_BEFORE_PROVIDER"}',
        '{"schema_version":1,"operation":"TOSS_ACCOUNT_READONLY_DAILY","occurrence_date":"2026-08-26","scheduled_for":"2026-08-26T07:00:00+09:00","claimed_at_utc":"NOT_A_TIMESTAMP","status":"CLAIMED_BEFORE_PROVIDER"}',
    ],
)
def test_daily_replay_rejects_duplicate_keys_and_invalid_clocks(tmp_path, raw):
    receipt = tmp_path / "data/state/toss_account_snapshot_occurrences/2026-08-26.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(raw, encoding="utf-8")
    with pytest.raises(TossAccountScheduleError):
        run_toss_account_daily(
            tmp_path, {}, now=NOW,
            runtime_builder=lambda *args, **kwargs: pytest.fail("must not rebuild"),
        )


def test_daily_success_rejects_noncanonical_identifier_bearing_result_path(tmp_path):
    unsafe_relative = "data/normalized/toss_account_snapshot/account-7654321.json"
    unsafe = tmp_path / unsafe_relative
    unsafe.parent.mkdir(parents=True)
    unsafe.write_text("{}", encoding="utf-8")

    def refresh(trigger):
        return TossAccountRefreshResult(
            "SUCCEEDED", trigger, 3, token_calls=1, normalized_path=unsafe_relative,
        )

    report = run_toss_account_daily(
        tmp_path, {}, now=NOW,
        runtime_builder=lambda *args, **kwargs: _enabled_wiring(refresh),
    )
    assert report["status"] == "TERMINAL_FAILURE"
    rendered = (tmp_path / report["receipt"]).read_text(encoding="utf-8")
    assert "7654321" not in rendered
    receipt = json.loads(rendered)
    assert receipt["normalized"] is None
    assert receipt["normalized_sha256"] is None


def test_daily_dry_run_builds_wiring_without_claim_or_provider_call(tmp_path):
    report = run_toss_account_daily(
        tmp_path, {}, now=NOW, dry_run=True,
        runtime_builder=lambda *args, **kwargs: _enabled_wiring(
            lambda trigger: pytest.fail("provider must not run")
        ),
    )
    assert report["status"] == "DRY_RUN_READY"
    assert not (tmp_path / "data").exists()


def test_daily_cli_dry_run_loads_dotenv_without_echoing_values(tmp_path):
    values = ("fixture-client-unique", "fixture-secret-unique", "7654321")
    (tmp_path / ".env").write_text(
        "TOSSINVEST_CLIENT_ID=" + values[0] + "\n"
        "TOSSINVEST_CLIENT_SECRET=" + values[1] + "\n"
        "TOSSINVEST_ACCOUNT_SEQ=" + values[2] + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable, str(CLI), "--project-root", str(tmp_path),
            "--as-of", NOW.isoformat(), "--dry-run",
        ],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["status"] == "DRY_RUN_READY"
    assert all(value not in completed.stdout + completed.stderr for value in values)
    assert not (
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_TOSS_ACCOUNT_DAILY_last.json"
    ).exists()


def test_daily_cli_rejects_provider_capable_as_of_before_claim(tmp_path):
    completed = subprocess.run(
        [
            sys.executable, str(CLI), "--project-root", str(tmp_path),
            "--as-of", "2099-01-01T07:00:00+09:00",
        ],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {
        "operation": "TOSS_ACCOUNT_READONLY_DAILY",
        "status": "CLI_FAILURE",
        "reason": "SANITIZED_INTERNAL_FAILURE",
        "token_calls": 0,
        "account_calls": 0,
    }
    assert not (tmp_path / "data").exists()


def test_daily_cli_publishes_strict_recovery_receipt_with_unknown_counts(
    tmp_path, monkeypatch, capsys,
):
    cli = _load_cli_module()
    normalized = tmp_path / "data/normalized/toss_account_snapshot/latest.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_bytes(b"prior-normalized")

    class Crash(BaseException):
        pass

    def refresh(trigger):
        normalized.write_bytes(b"partial-normalized")
        raise Crash()

    def fail_exact_restore(root, before):
        raise OSError("injected persistent exact restore failure")

    def run_with_injected_recovery(project_root, environment, **kwargs):
        return run_toss_account_daily(
            project_root, environment, now=NOW,
            runtime_builder=lambda *args, **inner_kwargs: _enabled_wiring(refresh),
        )

    monkeypatch.setattr(runtime, "_restore_projection_bytes_exact", fail_exact_restore)
    monkeypatch.setattr(cli, "load_toss_account_environment", lambda *args: {})
    monkeypatch.setattr(cli, "run_toss_account_daily", run_with_injected_recovery)

    assert cli.main(["--project-root", str(tmp_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    receipt_path = tmp_path / report["receipt"]
    scheduler_path = tmp_path / cli.LAST_RECEIPT
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert report["status"] == "RECOVERY_REQUIRED"
    assert report["retained_status"] is None
    assert report["outcome"] == "SCHEDULE_INTERNAL_FAILURE"
    assert report["reason"] == "ACCOUNT_PROJECTION_RECOVERY_REQUIRED"
    assert report["token_calls"] is report["account_calls"] is None
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert "token_calls" not in receipt and "account_calls" not in receipt
    assert scheduler_path.read_bytes() == receipt_path.read_bytes()
    assert b"partial-normalized" not in receipt_path.read_bytes()
