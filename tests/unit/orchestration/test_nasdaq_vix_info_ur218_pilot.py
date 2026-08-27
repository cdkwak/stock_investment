from datetime import datetime, timezone

from stock_data.orchestration.nasdaq_vix_info_ur218_pilot import LANDING_ROOT, STATE_PATH, capture


def test_ur218_non_200_is_terminal_and_not_repeated(tmp_path) -> None:
    class Response:
        status_code = 503
        content = b"unavailable"

    first = capture(tmp_path, now=datetime(2026, 8, 21, 10, tzinfo=timezone.utc), response_factory=lambda: Response())
    second = capture(tmp_path, now=datetime(2026, 8, 21, 10, 1, tzinfo=timezone.utc), response_factory=lambda: (_ for _ in ()).throw(AssertionError("repeat")))
    assert (first.status, first.raw_gets) == ("COMPLETE_FAILURE", 1)
    assert (second.status, second.raw_gets) == ("NO_REPEAT", 0)
    assert not (tmp_path / LANDING_ROOT).exists()


def test_ur218_success_is_landing_hash_readback_captured(tmp_path) -> None:
    class Response:
        status_code = 200
        content = b'{"data": {"symbol": "VIX"}}'

    result = capture(tmp_path, now=datetime(2026, 8, 21, 10, tzinfo=timezone.utc), response_factory=lambda: Response())
    assert result.status == "LANDING_CAPTURED_PENDING_STRICT_VALIDATION"
    assert result.raw_gets == 1 and result.body_sha256
    assert result.body_sha256 in (tmp_path / STATE_PATH).read_text(encoding="utf-8")
    assert (tmp_path / LANDING_ROOT / result.body_sha256 / "body.json").read_bytes() == Response.content
