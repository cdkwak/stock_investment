from datetime import datetime, timezone

from stock_data.orchestration.naver_mobile_home_ur191_windows import (
    CONDITIONAL_WINDOW_IDS,
    WINDOW_IDS,
    collector,
    ensure_manifest,
    is_active,
    read_manifest,
)


def test_ur191_manifest_limits_exact_next_session_windows(tmp_path) -> None:
    ensure_manifest(tmp_path)
    manifest = read_manifest(tmp_path)
    assert manifest["allowed_window_ids"] == list(WINDOW_IDS)
    assert manifest["conditional_window_ids"] == list(CONDITIONAL_WINDOW_IDS)
    assert is_active(tmp_path, now=datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc))
    assert is_active(tmp_path, now=datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc))
    assert not is_active(tmp_path, now=datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc))
    assert not is_active(tmp_path, now=datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc))


def test_ur191_pre_date_window_never_constructs_transport_claim(tmp_path) -> None:
    ensure_manifest(tmp_path)
    seen: list[str] = []
    result = collector(tmp_path).run(
        now=datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc),
        response_factory=lambda: (seen.append("called"), None)[1],
        allowed_window_ids=WINDOW_IDS,
    )
    assert result.status == "WINDOW_NOT_MANIFESTED"
    assert result.raw_gets == 0
    assert seen == []
