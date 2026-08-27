import json
from pathlib import Path
import subprocess
import sys


def test_kb_daily_schedule_is_disabled_provider_free_preflight() -> None:
    project_root = Path(__file__).resolve().parents[3]
    script = (project_root / "scripts/register_kbsec_daily_snapshot_task.ps1").read_text(
        encoding="utf-8"
    )
    assert '[string]$Time = "17:00"' in script
    assert "Monday, Tuesday, Wednesday, Thursday, Friday" in script
    assert "--confirm-live-daily" not in script.split("# The retained", 1)[0]
    assert "--confirm-access-restored" not in script
    assert "Disable-ScheduledTask -TaskName $taskName" in script
    assert "installed_disabled=$taskName" in script
    exact_runner = project_root / "scripts/manual/collect/collect_kbsec_daily_snapshot.py"
    wrong_layer_runner = project_root / "scripts/manual/collect_kbsec_daily_snapshot.py"
    assert exact_runner.is_file()
    assert not wrong_layer_runner.exists()
    assert 'manual\\collect\\collect_kbsec_daily_snapshot.py' in script
    assert 'manual\\collect_kbsec_daily_snapshot.py' not in script
    assert not any(word in script.lower() for word in ("order", "trade", "account"))

    completed = subprocess.run(
        [
            sys.executable,
            str(exact_runner),
            "--project-root",
            str(project_root),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {
        "status": "NOT_EXECUTED_CONFIRMATION_REQUIRED",
        "network_calls": 0,
    }
