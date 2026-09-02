from __future__ import annotations

from datetime import date, datetime, timezone

from stock_web.api.fmt import format_kst


def test_format_kst_converts_timestamps_and_preserves_date_only_values() -> None:
    assert format_kst("2026-09-02T22:00:03.312296+00:00") == "09-03 07:00"
    assert format_kst("2026-09-02T07:00:00+09:00") == "09-02 07:00"
    assert format_kst("2026-09-02") == "09-02"
    assert format_kst(date(2026, 9, 2)) == "09-02"
    assert format_kst(datetime(2026, 9, 2, 22, tzinfo=timezone.utc)) == "09-03 07:00"
    assert "." not in format_kst("2026-09-02T22:00:03.312296+00:00")
    assert "+00:00" not in format_kst("2026-09-02T22:00:03.312296+00:00")
