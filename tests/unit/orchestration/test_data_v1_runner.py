from pathlib import Path

from stock_data.orchestration.data_v1_runner import run_phase


def test_runner_defaults_to_no_live_and_phases_are_independent(tmp_path: Path):
    assert run_phase(tmp_path, 1, live=False, resume=True, max_calls=1)["status"] == "BLOCKED"
    assert run_phase(tmp_path, 6, live=True, resume=True, max_calls=1)["status"] == "BLOCKED"
