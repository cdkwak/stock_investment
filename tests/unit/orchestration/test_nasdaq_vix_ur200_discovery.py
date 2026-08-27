from datetime import datetime, timezone
from stock_data.orchestration.nasdaq_vix_ur200_discovery import STATE_PATH, capture_page

def test_ur200_non200_is_terminal_without_landing_or_repeat(tmp_path) -> None:
    class Response: status_code = 302; content = b"redirect"
    first = capture_page(tmp_path, now=datetime(2026, 8, 21, 8, 38, tzinfo=timezone.utc), response_factory=lambda: Response())
    second = capture_page(tmp_path, now=datetime(2026, 8, 21, 8, 39, tzinfo=timezone.utc), response_factory=lambda: (_ for _ in ()).throw(AssertionError("duplicate")))
    assert first.status == "COMPLETE_FAILURE" and first.raw_gets == 1 and first.body_sha256 is None
    assert not (tmp_path / "data/landing/nasdaq/vix_ur200_discovery").exists()
    assert second.status == "NO_REPEAT" and second.raw_gets == 0

def test_ur200_orphaned_html_claim_is_never_reinvoked(tmp_path) -> None:
    path = tmp_path / STATE_PATH; path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":1,"operation_id":"UR-200","operations":{"OFFICIAL_VIX_HTML":{"status":"ATTEMPTING"}}}', encoding="utf-8")
    result = capture_page(tmp_path, now=datetime(2026, 8, 21, 8, 38, tzinfo=timezone.utc), response_factory=lambda: (_ for _ in ()).throw(AssertionError("duplicate")))
    assert result.status == "NO_REPEAT" and result.raw_gets == 0
