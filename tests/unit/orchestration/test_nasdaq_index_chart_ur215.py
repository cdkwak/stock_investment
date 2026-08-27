from datetime import datetime, timezone

from stock_data.orchestration.nasdaq_index_chart_ur215 import (
    capture,
    finalize_numeric_free,
)


def test_ur215_retained_schema_failure_is_terminal_and_never_reinvokes_transport(tmp_path) -> None:
    class Response:
        status_code = 200
        content = b'{"data": {"symbol": "COMP"}}'

    first = capture(
        tmp_path,
        symbol="COMP",
        now=datetime(2026, 8, 21, 9, 43, tzinfo=timezone.utc),
        response_factory=lambda: Response(),
    )
    terminal = finalize_numeric_free(
        tmp_path,
        symbol="COMP",
        failure_type="TEST_RETAINED_SCHEMA_GAP",
    )
    replay = capture(
        tmp_path,
        symbol="COMP",
        now=datetime(2026, 8, 21, 9, 44, tzinfo=timezone.utc),
        response_factory=lambda: (_ for _ in ()).throw(AssertionError("duplicate transport")),
    )

    assert first.status == "COMPLETE_CAPTURED" and first.raw_gets == 1
    assert terminal.status == "COMPLETE_FAILURE" and terminal.raw_gets == 0
    assert replay.status == "NO_REPEAT" and replay.raw_gets == 0
