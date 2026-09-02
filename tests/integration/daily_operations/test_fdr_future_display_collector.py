from __future__ import annotations

import json
import multiprocessing
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from stock_data.orchestration.current_observation import ObservationIdentity
from stock_data.providers.fdr_display_daily import FDRDisplayDailyResponse
from stock_data.providers.fdr_future_display_collector import (
    CHECKPOINT_PATH, FDRFutureCollectorBusy, FDRFutureManifestError, RUNBOOK_PATH, _pid_alive, execute_future_collection, load_future_activation,
)


CLOCK = lambda: datetime(2026, 8, 22, 2, tzinfo=timezone.utc)
NAVER = ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "000660")
YAHOO = ObservationIdentity("DASHBOARD_CURRENT", "XUS", "^GSPC")


def _manifest(tmp_path, *, identities=(NAVER,), date_value="2026-08-22", cap=None, activation_id="FDR-20260822", continue_after_orphan=False):
    payload = {
        "schema_version": 1, "activation_id": activation_id, "runbook": RUNBOOK_PATH, "approved_on": "2026-08-21",
        "source_date": date_value, "identities": [identity.__dict__ for identity in identities],
        "global_request_cap": len(identities) if cap is None else cap, "execution_authorized": True,
        "continue_after_orphan": continue_after_orphan,
    }
    path = tmp_path / "activation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _naver_response(status=200):
    frame = pd.DataFrame({"Open": [100.0], "High": [110.0], "Low": [90.0], "Close": [105.0], "Volume": [1], "Change": [float("nan")]}, index=pd.to_datetime(["2026-08-22"]))
    return FDRDisplayDailyResponse(status, b"naver-body", frame if status == 200 else None)


def _yahoo_response():
    frame = pd.DataFrame({"Open": [5000.0], "High": [5010.0], "Low": [4990.0], "Close": [5005.0], "Adj Close": [5005.0], "Volume": [1]}, index=pd.to_datetime(["2026-08-22"]))
    return FDRDisplayDailyResponse(200, b"yahoo-body", frame)


def _hold_collector_lock(root: str, ready, release) -> None:
    from stock_data.providers.fdr_future_display_collector import _process_lock
    with _process_lock(Path(root)):
        ready.set()
        release.wait(5)


@pytest.mark.skipif(os.name != "nt", reason="Windows process probing contract")
def test_windows_pid_probe_is_non_mutating_and_distinguishes_missing_process() -> None:
    assert _pid_alive(os.getpid())
    assert not _pid_alive(2_147_483_647)


def test_rejects_missing_stale_consumed_and_overbudget_manifests_before_transport(tmp_path) -> None:
    with pytest.raises(FDRFutureManifestError):
        load_future_activation(tmp_path / "absent.json", clock=CLOCK)
    stale = _manifest(tmp_path, date_value="2026-08-21")
    with pytest.raises(FDRFutureManifestError):
        load_future_activation(stale, clock=CLOCK)
    consumed = _manifest(tmp_path, identities=(ObservationIdentity("KR_EQUITY_CURRENT", "XKRX", "005930"),))
    with pytest.raises(FDRFutureManifestError):
        load_future_activation(consumed, clock=CLOCK)
    overbudget = _manifest(tmp_path, identities=(NAVER, YAHOO), cap=1)
    with pytest.raises(FDRFutureManifestError):
        execute_future_collection(tmp_path / "root", overbudget, transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("no factory")), clock=CLOCK)


def test_mixed_success_lands_promotes_serially_and_replays_without_factory(tmp_path) -> None:
    manifest = _manifest(tmp_path, identities=(NAVER, YAHOO))
    calls = []

    def factory(route):
        calls.append(route)
        return (lambda *_: _naver_response()) if route.startswith("NAVER") else (lambda *_: _yahoo_response())

    result = execute_future_collection(tmp_path / "root", manifest, transport_factory=factory, clock=CLOCK)
    assert result.status == "COMPLETE" and result.provider_api_calls == 2 and result.replay_api_calls == 0
    assert calls == ["NAVER:000660", "YAHOO:^GSPC"]
    assert len(list((tmp_path / "root/data/landing/fdr_display_daily").rglob("response.bin"))) == 2
    replay = execute_future_collection(tmp_path / "root", manifest, transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("no replay factory")), clock=CLOCK)
    assert replay.status == "API_ZERO_REPLAY" and replay.provider_api_calls == 0


def test_partial_failure_preserves_independent_success_and_checkpoint_readback(tmp_path) -> None:
    manifest = _manifest(tmp_path, identities=(NAVER, YAHOO))
    result = execute_future_collection(
        tmp_path / "root", manifest,
        transport_factory=lambda route: (lambda *_: _naver_response(500)) if route.startswith("NAVER") else (lambda *_: _yahoo_response()),
        clock=CLOCK,
    )
    assert result.status == "PARTIAL_OR_FAILED" and result.provider_api_calls == 2
    assert result.routes[0].primary_safe_code == "FDR_DISPLAY_HTTP_500"
    assert result.routes[1].primary_safe_code is None
    state = json.loads((tmp_path / "root" / CHECKPOINT_PATH).read_text(encoding="utf-8"))
    assert state["activations"]["FDR-20260822"]["provider_api_calls"] == 2


def test_not_arrived_or_overreported_request_budget_rejects_without_extra_route(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    with pytest.raises(FDRFutureManifestError):
        execute_future_collection(
            tmp_path / "root", manifest, transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("no factory")),
            clock=lambda: datetime(2026, 8, 21, 2, tzinfo=timezone.utc),
        )
    with pytest.raises(RuntimeError, match="primary adapter exceeded policy request budget"):
        execute_future_collection(
            tmp_path / "root", manifest,
            transport_factory=lambda _: lambda *_: FDRDisplayDailyResponse(200, b"one-body", _naver_response().frame, request_count=2),
            clock=CLOCK,
        )


def test_successful_body_lands_before_schema_failure_and_replays_api_zero(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    malformed = pd.DataFrame({"Open": [100.0], "High": [110.0], "Low": [90.0], "Close": [105.0], "Volume": [1]}, index=pd.to_datetime(["2026-08-22"]))
    result = execute_future_collection(
        tmp_path / "root", manifest,
        transport_factory=lambda _: lambda *_: FDRDisplayDailyResponse(200, b"landing-before-schema", malformed),
        clock=CLOCK,
    )
    assert result.status == "PARTIAL_OR_FAILED" and result.routes[0].primary_safe_code == "FDR_DISPLAY_DAILY_SCHEMA"
    landing = list((tmp_path / "root/data/landing/fdr_display_daily").rglob("response.bin"))
    assert len(landing) == 1 and landing[0].read_bytes() == b"landing-before-schema"
    replay = execute_future_collection(tmp_path / "root", manifest, transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("no replay factory")), clock=CLOCK)
    assert replay.status == "API_ZERO_REPLAY" and replay.provider_api_calls == 0


def test_crash_before_response_becomes_orphan_and_never_reconstructs_transport(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    calls = []

    def crash_transport(*_args):
        calls.append("raw-request-started")
        raise KeyboardInterrupt("synthetic crash before response")

    with pytest.raises(KeyboardInterrupt):
        execute_future_collection(tmp_path / "root", manifest, transport_factory=lambda _: crash_transport, clock=CLOCK)
    state = json.loads((tmp_path / "root" / CHECKPOINT_PATH).read_text(encoding="utf-8"))
    assert state["activations"]["FDR-20260822"]["routes"][0]["state"] == "ATTEMPTING"
    resumed = execute_future_collection(
        tmp_path / "root", manifest, transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("no repeat factory")), clock=CLOCK,
    )
    assert calls == ["raw-request-started"] and resumed.status == "ORPHANED_STOP"
    assert resumed.routes[0].outcome == "ORPHANED" and resumed.routes[0].primary_safe_code == "FDR_DISPLAY_ORPHAN_NO_REPEAT"


def test_crash_after_promotion_is_orphaned_without_repeating_promoted_route(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        execute_future_collection(
            tmp_path / "root", manifest, transport_factory=lambda _: lambda *_: _naver_response(), clock=CLOCK,
            after_route_refresh=lambda _: (_ for _ in ()).throw(KeyboardInterrupt("synthetic crash after promotion")),
        )
    resumed = execute_future_collection(
        tmp_path / "root", manifest, transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("no repeated route")), clock=CLOCK,
    )
    assert resumed.status == "ORPHANED_STOP" and resumed.routes[0].outcome == "ORPHANED"


def test_explicit_orphan_continuation_skips_only_claimed_route(tmp_path) -> None:
    manifest = _manifest(tmp_path, identities=(NAVER, YAHOO), continue_after_orphan=True)
    with pytest.raises(KeyboardInterrupt):
        execute_future_collection(
            tmp_path / "root", manifest,
            transport_factory=lambda route: (lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("crash"))) if route.startswith("NAVER") else (lambda *_: _yahoo_response()),
            clock=CLOCK,
        )
    calls = []
    resumed = execute_future_collection(
        tmp_path / "root", manifest,
        transport_factory=lambda route: (calls.append(route) or (lambda *_: _yahoo_response())), clock=CLOCK,
    )
    assert resumed.status == "PARTIAL_OR_FAILED" and calls == ["YAHOO:^GSPC"]
    assert [route.outcome for route in resumed.routes] == ["ORPHANED", "COMPLETE"]


def test_concurrent_process_lock_rejects_second_collector_before_factory(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_collector_lock, args=(str(tmp_path / "root"), ready, release))
    process.start()
    try:
        assert ready.wait(5)
        with pytest.raises(FDRFutureCollectorBusy):
            execute_future_collection(
                tmp_path / "root", manifest, transport_factory=lambda _: (_ for _ in ()).throw(AssertionError("no factory")), clock=CLOCK,
            )
    finally:
        release.set()
        process.join(5)
    assert process.exitcode == 0
