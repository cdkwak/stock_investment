from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.orchestration.naver_mobile_basic_windowed_current import NaverMobileBasicWindowedCollector
from stock_data.orchestration.naver_remaining_session_windows import WINDOW_IDS, ensure_manifest, is_active, manifest_payload
from stock_data.providers.naver_current_web_observation import naver_web_current_route


KST = "+09:00"


@dataclass
class _Response:
    status_code: int
    content: bytes


def _now(hour: int, minute: int) -> datetime:
    return datetime.fromisoformat(f"2026-08-21T{hour:02d}:{minute:02d}:30{KST}")


def _payload(time_text: str) -> bytes:
    return json.dumps({
        "itemCode": "000660", "closePrice": "1,734,000", "marketStatus": "OPEN", "localTradedAt": time_text, "delayTime": 0,
        "stockExchangeType": {"code": "KS", "zoneId": "Asia/Seoul", "nationType": "KOR", "stockType": "domestic", "delayTime": 0, "startTime": "0900", "endTime": "1530"},
    }).encode()


def test_initial_pilot_window_never_constructs_transport(tmp_path) -> None:
    collector = NaverMobileBasicWindowedCollector(tmp_path)
    calls = 0
    def factory():
        nonlocal calls; calls += 1; return _Response(200, _payload("2026-08-21T13:14:00+09:00"))

    result = collector.run(now=_now(13, 15), response_factory=factory)

    assert result.status == "INITIAL_WINDOW_CONSUMED"
    assert result.raw_gets == calls == 0


def test_unmanifested_early_window_never_constructs_transport(tmp_path) -> None:
    collector = NaverMobileBasicWindowedCollector(tmp_path)
    calls = 0
    def factory():
        nonlocal calls; calls += 1; return _Response(200, b"{}")

    result = collector.run(
        now=_now(14, 1), response_factory=factory,
        allowed_window_ids={"2026-08-21T14:30:00+09:00"},
    )

    assert result.status == "WINDOW_NOT_MANIFESTED"
    assert result.raw_gets == calls == 0


def test_later_window_promotes_then_failure_preserves_prior_and_replay_is_zero(tmp_path) -> None:
    collector = NaverMobileBasicWindowedCollector(tmp_path)
    first = collector.run(now=_now(13, 31), response_factory=lambda: _Response(200, _payload("2026-08-21T13:30:00+09:00")))
    assert first.status == "COMPLETE" and first.observation is not None and first.replay_api_calls == 0

    failed = collector.run(now=_now(14, 1), response_factory=lambda: _Response(500, b"not retained"))

    assert failed.status == "COMPLETE_FAILURE"
    assert failed.raw_gets == 1
    assert failed.observation == first.observation
    assert failed.replay_api_calls == 0
    assert collector.store.load(naver_web_current_route().route_id).is_open


def test_orphaned_claim_is_fail_closed_without_transport(tmp_path) -> None:
    collector = NaverMobileBasicWindowedCollector(tmp_path)
    collector.state_path.parent.mkdir(parents=True)
    collector.state_path.write_text(json.dumps({
        "schema_version": 1, "initial_pilot_window": "2026-08-21T13:00:00+09:00",
        "windows": {"2026-08-21T13:30:00+09:00": {"status": "ATTEMPTING"}},
    }), encoding="utf-8")
    calls = 0
    def factory():
        nonlocal calls; calls += 1; return _Response(200, b"{}")

    result = collector.run(now=_now(13, 31), response_factory=factory)

    assert result.status == "ORPHANED_NO_REPEAT"
    assert calls == result.raw_gets == 0


def test_process_lock_prevents_overlapping_transport(tmp_path) -> None:
    lock = CurrentObservationProcessLock(tmp_path / "data/state/naver_mobile_basic_000660_30m_ur153.lock")
    assert lock.acquire()
    try:
        collector = NaverMobileBasicWindowedCollector(tmp_path)
        result = collector.run(now=_now(13, 31), response_factory=lambda: (_ for _ in ()).throw(AssertionError("must not call")))
    finally:
        lock.release()

    assert result.status == "PROCESS_LOCKED"
    assert result.raw_gets == 0


def test_public_activation_manifest_is_immutable_and_secret_free(tmp_path) -> None:
    path = ensure_manifest(tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == manifest_payload()
    assert WINDOW_IDS == (
        "2026-08-21T14:30:00+09:00", "2026-08-21T15:00:00+09:00", "2026-08-21T15:30:00+09:00",
    )
    assert not is_active(tmp_path, now=_now(14, 1))
    assert is_active(tmp_path, now=_now(14, 31))
