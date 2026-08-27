from pathlib import Path
from stock_data.pipelines.backfill_state import BackfillState


def test_checkpoint_resume_distinguishes_empty_and_failure(tmp_path: Path) -> None:
    path=tmp_path/"state.json"; state=BackfillState.load(path,"dataset")
    state.mark_completed("2025"); state.mark_valid_empty("2024"); state.mark_failed("2023","Timeout")
    loaded=BackfillState.load(path,"dataset")
    assert loaded.pending(["2023","2024","2025","2026"])==["2023","2026"]
    assert loaded.failed_partitions=={"2023":"Timeout"}


def test_checkpoint_batch_transitions_are_atomic_at_call_boundary(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = BackfillState.load(path, "dataset")
    state.mark_staged_many(["20240101", "20240102"])
    state.mark_failed_many(["20240103", "20240104"], "Timeout")
    state.mark_completed_many(["20240101", "20240103"])
    state.mark_valid_empty_many(["20240102"])
    loaded = BackfillState.load(path, "dataset")
    assert loaded.completed_partitions == {"20240101", "20240103"}
    assert loaded.valid_empty_partitions == {"20240102"}
    assert loaded.failed_partitions == {"20240104": "Timeout"}
    assert loaded.staged_partitions == set()
