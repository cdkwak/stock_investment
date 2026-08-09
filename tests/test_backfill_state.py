from pathlib import Path
from stock_data.pipelines.backfill_state import BackfillState


def test_checkpoint_resume_distinguishes_empty_and_failure(tmp_path: Path) -> None:
    path=tmp_path/"state.json"; state=BackfillState.load(path,"dataset")
    state.mark_completed("2025"); state.mark_valid_empty("2024"); state.mark_failed("2023","Timeout")
    loaded=BackfillState.load(path,"dataset")
    assert loaded.pending(["2023","2024","2025","2026"])==["2023","2026"]
    assert loaded.failed_partitions=={"2023":"Timeout"}
