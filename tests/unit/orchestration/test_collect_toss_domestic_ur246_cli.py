from __future__ import annotations

import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path
from threading import Event, Thread

import pytest
from scripts.manual.collect import collect_toss_domestic_ur246 as cli


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _result(statuses: dict[str, str]) -> dict[str, object]:
    return {
        "date_kst": "2026-08-24",
        "window_id": "2026-08-24T09:00:00+09:00",
        "statuses": statuses,
        "oauth_calls": 1,
        "business_calls": len(statuses),
    }


class _FakeTransport:
    def __init__(self, *, oauth_calls: int = 1, business_calls: int = 4) -> None:
        self.oauth_calls = oauth_calls
        self.business_calls = business_calls


def _fake_runner(
    statuses: dict[str, str], *, oauth_calls: int = 1, business_calls: int = 4,
):
    class FakeRunner:
        def run(self, *, now, transport_factory):
            transport_factory()
            return type("Result", (), {
                "date_kst": now.astimezone(cli.KST).date().isoformat(),
                "window_id": cli._scheduled_occurrence(now).isoformat(),
                "statuses": statuses,
                "oauth_calls": oauth_calls,
                "business_calls": business_calls,
            })()

    return FakeRunner()


def _complete_statuses() -> dict[str, str]:
    return {
        "000660": "COMPLETE",
        "005930": "COMPLETE",
        "KOSPI": "COMPLETE",
        "KOSDAQ": "COMPLETE",
    }


def test_main_returns_success_only_for_intentional_scheduler_outcomes(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(cli, "run", lambda root: _result({"KOSPI": "COMPLETE"}))
    monkeypatch.setattr(
        sys, "argv",
        ["collect_toss_domestic_ur246.py", "--project-root", str(tmp_path),
         "--confirm-ur246-window"],
    )

    assert cli.main() == 0


def test_main_returns_failure_when_any_claim_has_terminal_failure(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(
        cli, "run",
        lambda root: _result({
            "KOSPI": "COMPLETE",
            "KOSDAQ": "COMPLETE_SEMANTIC_FAILURE",
        }),
    )
    monkeypatch.setattr(
        sys, "argv",
        ["collect_toss_domestic_ur246.py", "--project-root", str(tmp_path),
         "--confirm-ur246-window"],
    )

    assert cli.main() == 1


def test_orphaned_or_locked_windows_are_not_reported_as_scheduler_success() -> None:
    assert cli._scheduler_exit_code({"KOSPI": "ORPHANED_NO_REPEAT"}) == 1
    assert cli._scheduler_exit_code({"operation": "PROCESS_LOCKED"}) == 1
    assert cli._scheduler_exit_code(
        {"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"}
    ) == 0


def test_pre_session_returns_api_zero_before_runner_or_transport(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(
        cli, "runner",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner not eligible")),
    )
    monkeypatch.setattr(
        cli, "_RuntimeTransport",
        lambda _root: (_ for _ in ()).throw(AssertionError("transport not eligible")),
    )

    result = cli.run(tmp_path, now=_at("2026-08-25T08:30:00+09:00"))

    assert result["date_kst"] == "2026-08-25"
    assert result["window_id"] is None
    assert result["statuses"] == {
        "operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"
    }
    assert result["oauth_calls"] == result["business_calls"] == 0
    assert result["occurrence_classification"] == "INELIGIBLE"
    assert result["occurrence_status"] == "TERMINAL_SUCCESS"
    assert result["terminal_exit_code"] == 0


def test_closed_calendar_date_returns_api_zero_before_runner(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(
        cli, "runner",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner not eligible")),
    )

    result = cli.run(tmp_path, now=_at("2026-08-17T09:00:00+09:00"))

    assert result["statuses"] == {
        "operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"
    }
    assert result["oauth_calls"] == result["business_calls"] == 0


def test_first_verified_session_boundary_delegates_all_authorized_routes(
    monkeypatch, tmp_path,
) -> None:
    called = []

    class FakeRunner:
        def run(self, *, now, transport_factory):
            transport = transport_factory()
            called.extend((now, transport))
            return type("Result", (), {
                "date_kst": "2026-08-25",
                "window_id": "2026-08-25T09:00:00+09:00",
                "statuses": {
                    "000660": "COMPLETE", "005930": "COMPLETE",
                    "KOSPI": "COMPLETE", "KOSDAQ": "COMPLETE",
                },
                "oauth_calls": 1,
                "business_calls": 4,
            })()

    now = _at("2026-08-25T09:00:00+09:00")
    scheduled = cli._scheduled_occurrence(now)
    transport = _FakeTransport()

    def build_runner(_root):
        assert cli._claim_path(tmp_path, scheduled).exists()
        return FakeRunner()

    def build_transport(_root):
        assert cli._claim_path(tmp_path, scheduled).exists()
        return transport

    monkeypatch.setattr(cli, "runner", build_runner)
    monkeypatch.setattr(cli, "_RuntimeTransport", build_transport)

    result = cli.run(tmp_path, now=now)

    assert called == [now, transport]
    assert set(result["statuses"]) == {"000660", "005930", "KOSPI", "KOSDAQ"}
    assert result["business_calls"] == 4


def test_eligible_success_persists_sanitized_terminal_and_replays_api_zero(
    monkeypatch, tmp_path,
) -> None:
    now = _at("2026-08-25T10:17:00+09:00")
    transport = _FakeTransport()
    monkeypatch.setattr(
        cli, "runner", lambda _root: _fake_runner(_complete_statuses()),
    )

    first = cli.run(
        tmp_path,
        now=now,
        transport_factory=lambda: transport,
        finished_clock=lambda: _at("2026-08-25T01:17:05+00:00"),
    )
    terminal_path = cli._terminal_path(tmp_path, cli._scheduled_occurrence(now))
    claim_path = cli._claim_path(tmp_path, cli._scheduled_occurrence(now))
    first_bytes = terminal_path.read_bytes()
    terminal = cli._validate_terminal(cli._read_json(terminal_path))

    assert claim_path.exists()
    assert terminal["scheduled_for"] == "2026-08-25T10:00:00+09:00"
    assert terminal["classification"] == "ELIGIBLE"
    assert terminal["terminal_status"] == "TERMINAL_SUCCESS"
    assert terminal["terminal_exit_code"] == 0
    assert terminal["oauth_calls"] == 1
    assert terminal["business_calls"] == 4
    assert terminal["outcomes"] == {
        "DOMESTIC_ROUTE_1": "COMPLETE",
        "DOMESTIC_ROUTE_2": "COMPLETE",
        "DOMESTIC_ROUTE_3": "COMPLETE",
        "DOMESTIC_ROUTE_4": "COMPLETE",
    }
    assert first["replayed"] is False
    pointer_path = tmp_path / cli._LAST_OCCURRENCE_POINTER
    for receipt_path in (claim_path, terminal_path, pointer_path):
        receipt_body = receipt_path.read_text(encoding="utf-8")
        for forbidden in (
            "000660", "005930", "KOSPI", "KOSDAQ", "http://", "https://",
            "access_token", "account_number",
        ):
            assert forbidden not in receipt_body

    monkeypatch.setattr(
        cli, "runner",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner replayed")),
    )
    replay = cli.run(
        tmp_path,
        now=now,
        transport_factory=lambda: (_ for _ in ()).throw(
            AssertionError("transport replayed")
        ),
    )

    assert replay["replayed"] is True
    assert replay["oauth_calls"] == replay["business_calls"] == 0
    assert replay["statuses"] == _complete_statuses()
    assert terminal_path.read_bytes() == first_bytes


def test_ineligible_receipt_is_durable_and_api_zero(monkeypatch, tmp_path) -> None:
    now = _at("2026-08-17T09:12:00+09:00")
    monkeypatch.setattr(
        cli, "runner",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner not eligible")),
    )

    result = cli.run(tmp_path, now=now)
    terminal = cli._validate_terminal(
        cli._read_json(cli._terminal_path(tmp_path, cli._scheduled_occurrence(now)))
    )

    assert result["oauth_calls"] == result["business_calls"] == 0
    assert terminal["classification"] == "INELIGIBLE"
    assert terminal["outcomes"] == {
        "OPERATION": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"
    }


def test_ineligible_post_claim_clock_exception_is_a_bounded_terminal_failure(
    monkeypatch, tmp_path,
) -> None:
    now = _at("2026-08-17T08:30:00+09:00")
    monkeypatch.setattr(
        cli, "runner",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner not eligible")),
    )

    result = cli.run(
        tmp_path,
        now=now,
        finished_clock=lambda: (_ for _ in ()).throw(
            RuntimeError("private clock exception")
        ),
    )
    terminal = cli._validate_terminal(
        cli._read_json(cli._terminal_path(tmp_path, cli._scheduled_occurrence(now)))
    )

    assert result["statuses"] == {"operation": "FAIL_RUNTIME"}
    assert terminal["classification"] == "INELIGIBLE"
    assert terminal["outcomes"] == {"OPERATION": "FAIL_RUNTIME"}
    assert terminal["oauth_calls"] == terminal["business_calls"] == 0


def test_partial_failure_persists_exact_sanitized_outcomes(
    monkeypatch, tmp_path,
) -> None:
    now = _at("2026-08-25T11:00:00+09:00")
    statuses = _complete_statuses()
    statuses["KOSPI"] = "COMPLETE_SEMANTIC_FAILURE"
    monkeypatch.setattr(cli, "runner", lambda _root: _fake_runner(statuses))

    result = cli.run(
        tmp_path, now=now, transport_factory=lambda: _FakeTransport(),
    )
    terminal_path = cli._terminal_path(tmp_path, cli._scheduled_occurrence(now))
    body = terminal_path.read_text(encoding="utf-8")
    terminal = cli._validate_terminal(cli._read_json(terminal_path))

    assert result["terminal_exit_code"] == 1
    assert terminal["terminal_status"] == "TERMINAL_FAILURE"
    assert terminal["failure_reason"] == "OPERATION_OUTCOME_FAILURE"
    assert terminal["outcomes"]["DOMESTIC_ROUTE_3"] == (
        "COMPLETE_SEMANTIC_FAILURE"
    )
    assert all(identity not in body for identity in _complete_statuses())


def test_exception_after_claim_persists_bounded_failure_without_exception_text(
    monkeypatch, tmp_path,
) -> None:
    now = _at("2026-08-25T11:30:00+09:00")
    transport = _FakeTransport(oauth_calls=1, business_calls=2)

    class ExplodingRunner:
        def run(self, *, now, transport_factory):
            transport_factory()
            raise RuntimeError("https://private.invalid account=123456 secret-value")

    monkeypatch.setattr(cli, "runner", lambda _root: ExplodingRunner())
    result = cli.run(
        tmp_path, now=now, transport_factory=lambda: transport,
    )
    scheduled = cli._scheduled_occurrence(now)
    claim_path = cli._claim_path(tmp_path, scheduled)
    terminal_path = cli._terminal_path(tmp_path, scheduled)
    body = terminal_path.read_text(encoding="utf-8")
    terminal = cli._validate_terminal(cli._read_json(terminal_path))

    assert claim_path.exists()
    assert result["statuses"] == {"operation": "FAIL_RUNTIME"}
    assert terminal["outcomes"] == {"OPERATION": "FAIL_RUNTIME"}
    assert terminal["oauth_calls"] == 1
    assert terminal["business_calls"] == 2
    assert terminal["failure_reason"] == "RUNTIME_FAILURE"
    for forbidden in (
        "private.invalid", "123456", "secret-value", "account=", "http://",
        "https://", "000660", "005930", "KOSPI", "KOSDAQ",
    ):
        assert forbidden not in body


def test_last_pointer_never_rewinds(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        cli, "runner", lambda _root: _fake_runner(_complete_statuses()),
    )
    newer = _at("2026-08-25T13:00:00+09:00")
    older = _at("2026-08-25T12:30:00+09:00")

    cli.run(tmp_path, now=newer, transport_factory=lambda: _FakeTransport())
    cli.run(tmp_path, now=older, transport_factory=lambda: _FakeTransport())
    pointer = cli._validate_pointer(tmp_path)

    assert pointer is not None
    assert pointer["scheduled_for"] == newer.isoformat()
    assert pointer["receipt_path"] == cli._terminal_path(
        tmp_path, newer,
    ).relative_to(tmp_path).as_posix()


def test_concurrent_pointer_updates_cannot_rewind_after_newer_publish(
    monkeypatch, tmp_path,
) -> None:
    older = _at("2026-08-25T12:30:00+09:00")
    newer = _at("2026-08-25T13:00:00+09:00")

    def terminal(scheduled_for: datetime) -> dict[str, object]:
        payload = cli._terminal_payload(
            scheduled_for=scheduled_for,
            classification="INELIGIBLE",
            statuses={
                "operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO",
            },
            oauth_calls=0,
            business_calls=0,
            finished_at=_at("2026-08-25T04:00:01+00:00"),
        )
        assert cli._publish_immutable_json(
            tmp_path, cli._terminal_path(tmp_path, scheduled_for), payload,
        )
        return payload

    older_terminal = terminal(older)
    newer_terminal = terminal(newer)
    real_atomic = cli._atomic_pointer
    newer_entered_atomic = Event()
    release_newer = Event()
    older_entered_atomic = Event()
    errors: list[str] = []

    def delayed_atomic(root, path, payload):
        if payload["scheduled_for"] == newer.isoformat():
            newer_entered_atomic.set()
            assert release_newer.wait(timeout=5)
        else:
            older_entered_atomic.set()
        real_atomic(root, path, payload)

    def update(scheduled_for, payload):
        try:
            cli._update_last_pointer(
                tmp_path, cli._terminal_path(tmp_path, scheduled_for), payload,
            )
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(type(error).__name__)

    monkeypatch.setattr(cli, "_atomic_pointer", delayed_atomic)
    newer_thread = Thread(target=update, args=(newer, newer_terminal))
    older_thread = Thread(target=update, args=(older, older_terminal))
    newer_thread.start()
    assert newer_entered_atomic.wait(timeout=5)
    older_thread.start()
    assert not older_entered_atomic.wait(timeout=0.2)
    release_newer.set()
    newer_thread.join(timeout=5)
    older_thread.join(timeout=5)

    pointer = cli._validate_pointer(tmp_path)
    assert errors == []
    assert not newer_thread.is_alive() and not older_thread.is_alive()
    assert not older_entered_atomic.is_set()
    assert pointer is not None
    assert pointer["scheduled_for"] == newer.isoformat()


def test_equal_occurrence_conflict_preserves_exact_valid_pointer(tmp_path) -> None:
    scheduled = _at("2026-08-25T13:00:00+09:00")
    terminal_path = cli._terminal_path(tmp_path, scheduled)
    retained = cli._terminal_payload(
        scheduled_for=scheduled,
        classification="INELIGIBLE",
        statuses={"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"},
        oauth_calls=0,
        business_calls=0,
        finished_at=_at("2026-08-25T04:00:01+00:00"),
    )
    assert cli._publish_immutable_json(tmp_path, terminal_path, retained)
    cli._update_last_pointer(tmp_path, terminal_path, retained)
    pointer_path = tmp_path / cli._LAST_OCCURRENCE_POINTER
    before = pointer_path.read_bytes()
    conflict = {
        **retained,
        "finished_at_utc": "2026-08-25T04:00:02+00:00",
    }

    with pytest.raises(
        cli.OccurrenceReceiptError,
        match="terminal differs from immutable receipt",
    ):
        cli._update_last_pointer(tmp_path, terminal_path, conflict)

    assert pointer_path.read_bytes() == before
    assert cli._validate_pointer(tmp_path)["finished_at_utc"] == retained["finished_at_utc"]


def test_pointer_rejects_wrong_terminal_binding_before_mutation(tmp_path) -> None:
    older = _at("2026-08-25T12:30:00+09:00")
    newer = _at("2026-08-25T13:00:00+09:00")

    def publish(scheduled_for: datetime) -> tuple[Path, dict[str, object]]:
        payload = cli._terminal_payload(
            scheduled_for=scheduled_for,
            classification="INELIGIBLE",
            statuses={"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"},
            oauth_calls=0,
            business_calls=0,
            finished_at=_at("2026-08-25T04:00:01+00:00"),
        )
        path = cli._terminal_path(tmp_path, scheduled_for)
        assert cli._publish_immutable_json(tmp_path, path, payload)
        return path, payload

    older_path, older_terminal = publish(older)
    newer_path, newer_terminal = publish(newer)
    cli._update_last_pointer(tmp_path, newer_path, newer_terminal)
    pointer_path = tmp_path / cli._LAST_OCCURRENCE_POINTER
    before = pointer_path.read_bytes()

    with pytest.raises(cli.OccurrenceReceiptError, match="path is not canonical"):
        cli._update_last_pointer(tmp_path, older_path, newer_terminal)

    assert pointer_path.read_bytes() == before
    assert cli._validate_pointer(tmp_path)["scheduled_for"] == newer.isoformat()


def test_atomic_pointer_failure_preserves_prior_valid_pointer(
    monkeypatch, tmp_path,
) -> None:
    older = _at("2026-08-25T12:30:00+09:00")
    newer = _at("2026-08-25T13:00:00+09:00")

    def publish(scheduled_for: datetime) -> tuple[Path, dict[str, object]]:
        payload = cli._terminal_payload(
            scheduled_for=scheduled_for,
            classification="INELIGIBLE",
            statuses={"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"},
            oauth_calls=0,
            business_calls=0,
            finished_at=_at("2026-08-25T04:00:01+00:00"),
        )
        path = cli._terminal_path(tmp_path, scheduled_for)
        assert cli._publish_immutable_json(tmp_path, path, payload)
        return path, payload

    older_path, older_terminal = publish(older)
    newer_path, newer_terminal = publish(newer)
    cli._update_last_pointer(tmp_path, older_path, older_terminal)
    pointer_path = tmp_path / cli._LAST_OCCURRENCE_POINTER
    before = pointer_path.read_bytes()
    monkeypatch.setattr(
        cli,
        "_atomic_pointer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected")),
    )

    with pytest.raises(OSError, match="injected"):
        cli._update_last_pointer(tmp_path, newer_path, newer_terminal)

    assert pointer_path.read_bytes() == before
    assert cli._validate_pointer(tmp_path)["scheduled_for"] == older.isoformat()


def test_pointer_lock_timeout_preserves_prior_valid_pointer(tmp_path) -> None:
    scheduled = _at("2026-08-25T13:00:00+09:00")
    terminal = cli._terminal_payload(
        scheduled_for=scheduled,
        classification="INELIGIBLE",
        statuses={"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"},
        oauth_calls=0,
        business_calls=0,
        finished_at=_at("2026-08-25T04:00:01+00:00"),
    )
    terminal_path = cli._terminal_path(tmp_path, scheduled)
    assert cli._publish_immutable_json(tmp_path, terminal_path, terminal)
    cli._update_last_pointer(tmp_path, terminal_path, terminal)
    pointer_path = tmp_path / cli._LAST_OCCURRENCE_POINTER
    before = pointer_path.read_bytes()

    with cli._last_pointer_lock(tmp_path):
        with pytest.raises(cli.OccurrenceReceiptError, match="lock timed out"):
            with cli._last_pointer_lock(tmp_path, timeout_seconds=0.02):
                pytest.fail("contending lock unexpectedly acquired")

    assert pointer_path.read_bytes() == before
    assert cli._validate_pointer(tmp_path)["scheduled_for"] == scheduled.isoformat()


def test_process_shared_pointer_lock_finishes_at_newest_exact_receipt(tmp_path) -> None:
    older = _at("2026-08-25T12:30:00+09:00")
    newer = _at("2026-08-25T13:00:00+09:00")
    for scheduled_for in (older, newer):
        terminal = cli._terminal_payload(
            scheduled_for=scheduled_for,
            classification="INELIGIBLE",
            statuses={"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"},
            oauth_calls=0,
            business_calls=0,
            finished_at=_at("2026-08-25T04:00:01+00:00"),
        )
        assert cli._publish_immutable_json(
            tmp_path, cli._terminal_path(tmp_path, scheduled_for), terminal,
        )

    child = (
        "import sys; from pathlib import Path; from datetime import datetime; "
        "from scripts.manual.collect import collect_toss_domestic_ur246 as c; "
        "r=Path(sys.argv[1]); s=datetime.fromisoformat(sys.argv[2]); "
        "p=c._terminal_path(r,s); t=c._validate_terminal(c._read_json(p)); "
        "c._update_last_pointer(r,p,t)"
    )
    with cli._last_pointer_lock(tmp_path):
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", child, str(tmp_path), scheduled.isoformat()],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for scheduled in (newer, older)
        ]
        time.sleep(0.1)
        assert all(process.poll() is None for process in processes)
    completed = [process.communicate(timeout=10) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], completed
    pointer = cli._validate_pointer(tmp_path)
    assert pointer is not None
    assert pointer["scheduled_for"] == newer.isoformat()
    assert pointer["receipt_path"] == cli._terminal_path(
        tmp_path, newer,
    ).relative_to(tmp_path).as_posix()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spawn regression")
def test_windows_spawned_older_updater_rereads_latest_and_cannot_rewind(
    tmp_path,
) -> None:
    older = _at("2026-08-25T12:30:00+09:00")
    newer = _at("2026-08-25T13:00:00+09:00")
    for scheduled_for in (older, newer):
        terminal = cli._terminal_payload(
            scheduled_for=scheduled_for,
            classification="INELIGIBLE",
            statuses={"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"},
            oauth_calls=0,
            business_calls=0,
            finished_at=_at("2026-08-25T04:00:01+00:00"),
        )
        assert cli._publish_immutable_json(
            tmp_path, cli._terminal_path(tmp_path, scheduled_for), terminal,
        )

    child = (
        "import sys; from pathlib import Path; from datetime import datetime; "
        "from scripts.manual.collect import collect_toss_domestic_ur246 as c; "
        "r=Path(sys.argv[1]); s=datetime.fromisoformat(sys.argv[2]); "
        "p=c._terminal_path(r,s); t=c._validate_terminal(c._read_json(p)); "
        "c._update_last_pointer(r,p,t)"
    )
    newer_process = subprocess.run(
        [sys.executable, "-c", child, str(tmp_path), newer.isoformat()],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert newer_process.returncode == 0, (
        newer_process.stdout, newer_process.stderr,
    )
    pointer_path = tmp_path / cli._LAST_OCCURRENCE_POINTER
    newer_bytes = pointer_path.read_bytes()

    older_process = subprocess.run(
        [sys.executable, "-c", child, str(tmp_path), older.isoformat()],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert older_process.returncode == 0, (
        older_process.stdout, older_process.stderr,
    )
    assert pointer_path.read_bytes() == newer_bytes
    pointer = cli._validate_pointer(tmp_path)
    assert pointer is not None
    assert pointer["scheduled_for"] == newer.isoformat()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows lock regression")
def test_windows_os_exit_releases_pointer_lock_for_next_process(tmp_path) -> None:
    older = _at("2026-08-25T12:30:00+09:00")
    newer = _at("2026-08-25T13:00:00+09:00")
    terminals: dict[datetime, tuple[Path, dict[str, object]]] = {}
    for scheduled_for in (older, newer):
        terminal = cli._terminal_payload(
            scheduled_for=scheduled_for,
            classification="INELIGIBLE",
            statuses={"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"},
            oauth_calls=0,
            business_calls=0,
            finished_at=_at("2026-08-25T04:00:01+00:00"),
        )
        terminal_path = cli._terminal_path(tmp_path, scheduled_for)
        assert cli._publish_immutable_json(tmp_path, terminal_path, terminal)
        terminals[scheduled_for] = terminal_path, terminal
    cli._update_last_pointer(tmp_path, *terminals[older])

    marker = tmp_path / "crash-lock-acquired.marker"
    crash_child = (
        "import os,sys; from pathlib import Path; "
        "from scripts.manual.collect import collect_toss_domestic_ur246 as c; "
        "r=Path(sys.argv[1]); m=Path(sys.argv[2]); "
        "\nwith c._last_pointer_lock(r):"
        "\n m.write_text('locked',encoding='utf-8')"
        "\n os._exit(23)"
    )
    crashed = subprocess.run(
        [sys.executable, "-c", crash_child, str(tmp_path), str(marker)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert crashed.returncode == 23, (crashed.stdout, crashed.stderr)
    assert marker.read_text(encoding="utf-8") == "locked"
    update_child = (
        "import sys; from pathlib import Path; from datetime import datetime; "
        "from scripts.manual.collect import collect_toss_domestic_ur246 as c; "
        "r=Path(sys.argv[1]); s=datetime.fromisoformat(sys.argv[2]); "
        "p=c._terminal_path(r,s); t=c._validate_terminal(c._read_json(p)); "
        "c._update_last_pointer(r,p,t)"
    )
    updater = subprocess.run(
        [sys.executable, "-c", update_child, str(tmp_path), newer.isoformat()],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert updater.returncode == 0, (updater.stdout, updater.stderr)
    pointer = cli._validate_pointer(tmp_path)
    assert pointer is not None
    assert pointer["scheduled_for"] == newer.isoformat()
    assert pointer["receipt_path"] == terminals[newer][0].relative_to(
        tmp_path,
    ).as_posix()


def test_malformed_terminal_fails_closed_before_runner_or_transport(
    monkeypatch, tmp_path,
) -> None:
    now = _at("2026-08-25T14:00:00+09:00")
    terminal_path = cli._terminal_path(tmp_path, cli._scheduled_occurrence(now))
    terminal_path.parent.mkdir(parents=True)
    terminal_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        cli, "runner",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner invoked")),
    )

    result = cli.run(
        tmp_path,
        now=now,
        transport_factory=lambda: (_ for _ in ()).throw(
            AssertionError("transport invoked")
        ),
    )

    assert result["statuses"] == {"operation": "FAIL_RECEIPT_INVALID"}
    assert result["oauth_calls"] == result["business_calls"] == 0
    assert terminal_path.read_text(encoding="utf-8") == "{}\n"


def test_malformed_claim_fails_closed_before_runner_or_transport(
    monkeypatch, tmp_path,
) -> None:
    now = _at("2026-08-25T14:30:00+09:00")
    scheduled = cli._scheduled_occurrence(now)
    claim_path = cli._claim_path(tmp_path, scheduled)
    claim_path.parent.mkdir(parents=True)
    claim_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        cli, "runner",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner invoked")),
    )

    result = cli.run(
        tmp_path,
        now=now,
        transport_factory=lambda: (_ for _ in ()).throw(
            AssertionError("transport invoked")
        ),
    )

    assert result["statuses"] == {"operation": "FAIL_RECEIPT_INVALID"}
    assert result["oauth_calls"] == result["business_calls"] == 0
    assert claim_path.read_text(encoding="utf-8") == "{}\n"
    assert not cli._terminal_path(tmp_path, scheduled).exists()


def test_malformed_last_pointer_terminalizes_current_claim_without_transport(
    monkeypatch, tmp_path,
) -> None:
    now = _at("2026-08-25T14:30:00+09:00")
    pointer_path = tmp_path / cli._LAST_OCCURRENCE_POINTER
    pointer_path.parent.mkdir(parents=True)
    pointer_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        cli, "runner",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner invoked")),
    )

    result = cli.run(
        tmp_path,
        now=now,
        transport_factory=lambda: (_ for _ in ()).throw(
            AssertionError("transport invoked")
        ),
    )
    terminal_path = cli._terminal_path(tmp_path, cli._scheduled_occurrence(now))
    terminal = cli._validate_terminal(cli._read_json(terminal_path))

    assert result["statuses"] == {"operation": "FAIL_RECEIPT_INVALID"}
    assert terminal["outcomes"] == {"OPERATION": "FAIL_RECEIPT_INVALID"}
    assert terminal["failure_reason"] == "RECEIPT_VALIDATION_FAILURE"
    assert terminal["oauth_calls"] == terminal["business_calls"] == 0
    assert pointer_path.read_text(encoding="utf-8") == "{}\n"


def test_incomplete_existing_claim_fails_closed_without_transport(
    monkeypatch, tmp_path,
) -> None:
    now = _at("2026-08-25T14:30:00+09:00")
    scheduled = cli._scheduled_occurrence(now)
    claim = cli._claim_payload(scheduled, now)
    assert cli._publish_immutable_json(tmp_path, cli._claim_path(tmp_path, scheduled), claim)
    monkeypatch.setattr(
        cli, "runner",
        lambda _root: (_ for _ in ()).throw(AssertionError("runner invoked")),
    )

    result = cli.run(tmp_path, now=now)

    assert result["statuses"] == {"operation": "FAIL_CLAIM_INCOMPLETE"}
    assert result["oauth_calls"] == result["business_calls"] == 0
    assert not cli._terminal_path(tmp_path, scheduled).exists()


def test_result_and_transport_count_mismatch_is_a_bounded_contract_failure(
    monkeypatch, tmp_path,
) -> None:
    now = _at("2026-08-25T15:00:00+09:00")
    monkeypatch.setattr(
        cli, "runner",
        lambda _root: _fake_runner(
            _complete_statuses(), oauth_calls=1, business_calls=3,
        ),
    )

    result = cli.run(
        tmp_path, now=now, transport_factory=lambda: _FakeTransport(),
    )
    terminal = cli._validate_terminal(
        cli._read_json(cli._terminal_path(tmp_path, cli._scheduled_occurrence(now)))
    )

    assert result["statuses"] == {"operation": "FAIL_RESULT_CONTRACT"}
    assert terminal["outcomes"] == {"OPERATION": "FAIL_RESULT_CONTRACT"}
    assert terminal["failure_reason"] == "RESULT_CONTRACT_FAILURE"
    assert terminal["oauth_calls"] == 1
    assert terminal["business_calls"] == 4


def test_in_session_semantic_failure_remains_scheduler_failure(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(
        cli, "run",
        lambda _root: _result({
            "000660": "COMPLETE_SEMANTIC_FAILURE",
            "005930": "COMPLETE",
            "KOSPI": "COMPLETE",
            "KOSDAQ": "COMPLETE",
        }),
    )
    monkeypatch.setattr(
        sys, "argv",
        ["collect_toss_domestic_ur246.py", "--project-root", str(tmp_path),
         "--confirm-ur246-window"],
    )

    assert cli.main() == 1


@pytest.mark.parametrize(("clock", "eligible"), [
    ("2026-08-25T08:59:59+09:00", False),
    ("2026-08-25T09:00:00+09:00", True),
    ("2026-08-25T15:29:59+09:00", True),
    ("2026-08-25T15:30:00+09:00", False),
])
def test_verified_collection_window_is_half_open(clock, eligible) -> None:
    assert cli._collection_eligible(_at(clock)) is eligible


def test_installed_occurrence_window_matches_verified_collection_session() -> None:
    script = Path("scripts/register_toss_domestic_ur246_task.ps1").read_text(
        encoding="utf-8",
    )

    assert "/ST 09:00 /RI 30 /DU 06:00" in script
    assert '".venv\\Scripts\\pythonw.exe"' in script
    assert "New-ScheduledTaskAction" in script
    assert 'registeredAction.Execute) -ine "pythonw.exe"' in script
    assert "/D MON,TUE,WED,THU,FRI" in script
    assert "schedule=MON-FRI@09:00 repetition=30m duration=6h" in script
    assert "New-TimeSpan -Minutes 25" in script
    assert "-MultipleInstances IgnoreNew" in script
    assert "ExecutionTimeLimit" in script
    assert "-StartWhenAvailable" in script
    assert "-AllowStartIfOnBatteries" in script
    assert "-DontStopIfGoingOnBatteries" in script
    assert "-WakeToRun" in script
    assert "Settings.DisallowStartIfOnBatteries" in script
    assert "Settings.StopIfGoingOnBatteries" in script
    assert "Settings.WakeToRun" in script
    assert "registered.Actions" in script
    assert "registered.Triggers" in script
    assert "Export-ScheduledTask" in script
    assert "taskCommandPath" in script
    assert "/ST 08:00" not in script
    assert "08:00 through 20:00" not in Path(
        "docs/data/operations/TOSS_DOMESTIC_UR246_RECURRING_30M.md"
    ).read_text(encoding="utf-8")
