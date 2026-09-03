from datetime import date
from pathlib import Path

from stock_web.api.data_page import FRESHNESS, load_credential_expiries


def test_stale_data_page_rows_use_delay_warning_label() -> None:
    assert FRESHNESS["STALE"] == ("지연/경고", "stale")


def test_credential_expiries_read_only_dates_and_flag_soon_or_expired(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "SOME_API_KEY=secret-value\n"
        "SOME_API_KEY_EXPIRES_AT=2026-09-10\n"
        "OLD_KEY_EXPIRES_AT=2026-01-01\n"
        "FAR_KEY_EXPIRES_AT=2027-01-01T00:00:00\n"
        "BAD_KEY_EXPIRES_AT=soon\n",
        encoding="utf-8",
    )
    rows = load_credential_expiries(tmp_path, today=date(2026, 9, 3))
    by_name = {row["name"]: row for row in rows}
    assert set(by_name) == {"SOME_API_KEY", "OLD_KEY", "FAR_KEY", "BAD_KEY"}
    assert by_name["SOME_API_KEY"]["status"] == "soon" and by_name["SOME_API_KEY"]["days_left"] == 7
    assert by_name["OLD_KEY"]["status"] == "expired"
    assert by_name["FAR_KEY"]["status"] == "ok" and by_name["FAR_KEY"]["expires"] == "2027-01-01"
    assert by_name["BAD_KEY"]["status"] == "unknown"
    assert "secret-value" not in repr(rows)
    assert [row["name"] for row in rows[:2]] == ["OLD_KEY", "SOME_API_KEY"]


def test_credential_expiries_without_env_file(tmp_path: Path) -> None:
    assert load_credential_expiries(tmp_path) == []
