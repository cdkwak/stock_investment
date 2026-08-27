from datetime import datetime, timezone
from stock_data.orchestration.nasdaq_soxx_ur193_windows import STATE_PATH, WINDOW_IDS, collector, expire_window_no_backfill, is_active, window_id

def test_ur193_early_window_never_invokes_transport(tmp_path) -> None:
    seen = []
    result = collector(tmp_path).run(now=datetime(2026, 8, 21, 8, 29, tzinfo=timezone.utc), response_factory=lambda: seen.append("called"), allowed_window_ids=WINDOW_IDS)
    assert result.status == "WINDOW_NOT_MANIFESTED" and result.raw_gets == 0 and seen == []

def test_ur193_orphan_claim_fails_closed_without_duplicate_transport(tmp_path) -> None:
    state = tmp_path / STATE_PATH; state.parent.mkdir(parents=True)
    state.write_text('{"schema_version": 1, "operation_id": "UR-193", "windows": {"2026-08-21T17:30:00+09:00": {"status": "ATTEMPTING"}}}', encoding="utf-8")
    seen = []
    result = collector(tmp_path).run(now=datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc), response_factory=lambda: seen.append("called"), allowed_window_ids=WINDOW_IDS)
    assert result.status == "ORPHAN_ATTEMPTING_NO_REPEAT" and result.raw_gets == 0 and seen == []

def test_ur193_completed_failure_is_not_repeated(tmp_path) -> None:
    class Response: status_code = 503; content = b"unavailable"
    first = collector(tmp_path).run(now=datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc), response_factory=lambda: Response(), allowed_window_ids=WINDOW_IDS)
    second = collector(tmp_path).run(now=datetime(2026, 8, 21, 8, 30, tzinfo=timezone.utc), response_factory=lambda: (_ for _ in ()).throw(AssertionError("duplicate")), allowed_window_ids=WINDOW_IDS)
    assert first.status == "COMPLETE_FAILURE" and first.raw_gets == 1
    assert second.status == "NO_REPEAT" and second.raw_gets == 0

def test_ur193_current_half_open_boundary_selects_only_current_slot(tmp_path) -> None:
    assert window_id(now=datetime(2026, 8, 21, 9, 1, tzinfo=timezone.utc)) == "2026-08-21T18:00:00+09:00"
    assert window_id(now=datetime(2026, 8, 21, 9, 29, 59, tzinfo=timezone.utc)) == "2026-08-21T18:00:00+09:00"
    assert window_id(now=datetime(2026, 8, 21, 9, 30, tzinfo=timezone.utc)) == "2026-08-21T18:30:00+09:00"
    assert not is_active(tmp_path, now=datetime(2026, 8, 21, 8, 29, tzinfo=timezone.utc))

def test_ur193_malformed_ledger_is_api_zero_and_gui_inactive(tmp_path) -> None:
    path = tmp_path / STATE_PATH; path.parent.mkdir(parents=True); path.write_text("not-json", encoding="utf-8")
    result = collector(tmp_path).run(now=datetime(2026, 8, 21, 9, 1, tzinfo=timezone.utc), response_factory=lambda: (_ for _ in ()).throw(AssertionError("transport")))
    assert result.status == "LEDGER_INVALID" and result.raw_gets == 0 and result.replay_api_calls == 0
    assert not is_active(tmp_path, now=datetime(2026, 8, 21, 9, 1, tzinfo=timezone.utc))

def test_ur193_expired_terminal_cannot_revive_inside_its_former_half_open_slot(tmp_path) -> None:
    now = datetime(2026, 8, 21, 9, 8, tzinfo=timezone.utc)
    expired = expire_window_no_backfill(tmp_path, boundary_id="2026-08-21T18:00:00+09:00", decided_at=now)
    assert expired.status == "EXPIRED_API_ZERO_NO_BACKFILL" and expired.raw_gets == 0 and expired.replay_api_calls == 0
    assert not is_active(tmp_path, now=now)
    replay = collector(tmp_path).run(now=now, response_factory=lambda: (_ for _ in ()).throw(AssertionError("transport")))
    assert replay.status == "NO_REPEAT" and replay.raw_gets == 0 and replay.replay_api_calls == 0
    assert expire_window_no_backfill(tmp_path, boundary_id="2026-08-21T18:00:00+09:00", decided_at=now).status == "NO_REPEAT"
