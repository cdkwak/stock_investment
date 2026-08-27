from __future__ import annotations

import json
from datetime import datetime, timezone

from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.orchestration.toss_equity_ur244_windows import (
    IDENTITIES,
    STATE_PATH,
    TossQuoteTransportResult,
    WINDOW_IDS,
    ensure_manifest,
    is_active,
    runner,
    selected_boundary,
)


def _now(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)  # KST 2026-08-24


def _transport(symbol: str, provider_time: str) -> TossQuoteTransportResult:
    return TossQuoteTransportResult(
        {"result": [{"symbol": symbol, "currency": "KRW", "lastPrice": "100000", "timestamp": provider_time}]},
        oauth_calls=1,
        business_calls=1,
    )


def _factories(provider_time: str):
    calls: list[str] = []
    factories = {
        symbol: (lambda code=symbol: calls.append(code) or _transport(code, provider_time))
        for symbol in IDENTITIES
    }
    return calls, factories


def test_manifest_is_exact_and_pre_date_never_constructs_transport(tmp_path) -> None:
    ensure_manifest(tmp_path)
    calls, factories = _factories("2026-08-24T07:59:00+09:00")
    result = runner(tmp_path).run(now=datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc), transport_factories=factories)

    assert len(WINDOW_IDS) == 25 and WINDOW_IDS[0].endswith("08:00:00+09:00") and WINDOW_IDS[-1].endswith("20:00:00+09:00")
    assert not is_active(tmp_path, now=datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc))
    assert calls == [] and result.business_api_calls == 0


def test_active_window_claims_serial_landing_and_api_zero_replay(tmp_path) -> None:
    ensure_manifest(tmp_path)
    calls, factories = _factories("2026-08-24T07:59:00+09:00")
    now = _now(23, 0)
    result = runner(tmp_path).run(now=now, transport_factories=factories)
    replay = runner(tmp_path).run(now=now, transport_factories={symbol: lambda: (_ for _ in ()).throw(AssertionError("no repeat")) for symbol in IDENTITIES})

    assert selected_boundary(tmp_path, now=now) == WINDOW_IDS[0]
    assert calls == list(IDENTITIES) and result.business_api_calls == 2
    assert set(result.statuses.values()) == {"COMPLETE"}
    assert replay.business_api_calls == 0 and set(replay.statuses.values()) == {"NO_REPEAT"}
    state = json.loads((tmp_path / STATE_PATH).read_text(encoding="utf-8"))
    for identity in IDENTITIES:
        claim = state["windows"][WINDOW_IDS[0]][identity]
        assert claim["oauth_calls"] == claim["business_get_completed"] == 1
        assert claim["replay_api_calls"] == 0 and claim["landing_sha256"]


def test_stale_second_window_preserves_prior_and_other_identity_continues(tmp_path) -> None:
    ensure_manifest(tmp_path)
    first = _now(23, 0)
    _, factories = _factories("2026-08-24T07:59:00+09:00")
    runner(tmp_path).run(now=first, transport_factories=factories)
    prior = (tmp_path / "data/state/current_observations/toss_000660_ur244_30m.json").read_bytes()
    later = _now(23, 30)
    result = runner(tmp_path).run(now=later, transport_factories={
        "000660": lambda: _transport("000660", "2026-08-24T06:00:00+09:00"),
        "005930": lambda: _transport("005930", "2026-08-24T08:29:00+09:00"),
    })

    assert result.statuses == {"000660": "COMPLETE_SEMANTIC_FAILURE", "005930": "COMPLETE"}
    assert (tmp_path / "data/state/current_observations/toss_000660_ur244_30m.json").read_bytes() == prior


def test_final_window_is_only_inferred_nxt_close_and_process_lock_is_api_zero(tmp_path) -> None:
    ensure_manifest(tmp_path)
    now = datetime(2026, 8, 24, 11, 0, tzinfo=timezone.utc)  # 20:00 KST
    calls, factories = _factories("2026-08-24T19:59:59+09:00")
    result = runner(tmp_path).run(now=now, transport_factories=factories)
    state = json.loads((tmp_path / STATE_PATH).read_text(encoding="utf-8"))

    assert result.window_id == WINDOW_IDS[-1] and calls == list(IDENTITIES)
    assert {state["windows"][WINDOW_IDS[-1]][symbol]["classification"] for symbol in IDENTITIES} == {"TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW"}
    lock = CurrentObservationProcessLock((tmp_path / STATE_PATH).with_suffix(".lock"))
    assert lock.acquire()
    locked = runner(tmp_path).run(now=now, transport_factories=factories)
    lock.release()
    assert locked.business_api_calls == 0 and set(locked.statuses.values()) == {"PROCESS_LOCKED"}


def test_final_window_rejects_outside_close_interval_without_projection(tmp_path) -> None:
    ensure_manifest(tmp_path)
    now = datetime(2026, 8, 24, 11, 0, tzinfo=timezone.utc)
    result = runner(tmp_path).run(now=now, transport_factories={
        "000660": lambda: _transport("000660", "2026-08-24T19:54:59+09:00"),
    })

    assert result.statuses["000660"] == "COMPLETE_SEMANTIC_FAILURE"
    assert not (tmp_path / "data/state/current_observations/toss_000660_ur244_30m.json").exists()


def test_durable_preclaim_precedes_transport_and_transport_failure_is_no_repeat(tmp_path) -> None:
    ensure_manifest(tmp_path)
    now = _now(23, 0)

    def fail_after_claim() -> TossQuoteTransportResult:
        state = json.loads((tmp_path / STATE_PATH).read_text(encoding="utf-8"))
        claim = state["windows"][WINDOW_IDS[0]]["000660"]
        assert claim["status"] == "ATTEMPTING" and claim["oauth_reserved"] == claim["business_get_reserved"] == 1
        raise RuntimeError("synthetic sanitized transport failure")

    first = runner(tmp_path).run(now=now, transport_factories={"000660": fail_after_claim})
    second = runner(tmp_path).run(now=now, transport_factories={"000660": lambda: (_ for _ in ()).throw(AssertionError("no repeat"))})

    assert first.statuses["000660"] == "COMPLETE_TRANSPORT_FAILURE" and first.business_api_calls == 1
    assert second.statuses["000660"] == "NO_REPEAT" and second.business_api_calls == 0
