from datetime import datetime, timezone
from stock_data.orchestration.nasdaq_tnx_ur201_discovery import capture_page

def test_ur201_redirect_is_terminal_and_never_repeated(tmp_path) -> None:
    class Response: status_code = 302; content = b"redirect"
    first = capture_page(tmp_path, now=datetime(2026,8,21,8,42,tzinfo=timezone.utc), response_factory=lambda: Response())
    second = capture_page(tmp_path, now=datetime(2026,8,21,8,43,tzinfo=timezone.utc), response_factory=lambda: (_ for _ in ()).throw(AssertionError("repeat")))
    assert first.status == "COMPLETE_FAILURE" and first.raw_gets == 1 and first.body_sha256 is None
    assert second.status == "NO_REPEAT" and second.raw_gets == 0
