from pathlib import Path


def test_kb_daily_schedule_is_once_weekday_at_1700_and_read_only() -> None:
    script = (Path(__file__).parents[1] / "scripts/register_kbsec_daily_snapshot_task.ps1").read_text(
        encoding="utf-8"
    )
    assert '[string]$Time = "17:00"' in script
    assert "Monday, Tuesday, Wednesday, Thursday, Friday" in script
    assert "--confirm-live-daily" in script
    assert "--confirm-access-restored" not in script
    assert "collect_kbsec_daily_snapshot.py" in script
    assert not any(word in script.lower() for word in ("order", "trade", "account"))
