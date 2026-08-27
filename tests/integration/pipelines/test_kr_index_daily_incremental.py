from __future__ import annotations

from pathlib import Path
import base64
import json

import pandas as pd
import pytest

from stock_data.contracts.kr_index_daily import KR_INDEX_DAILY
from stock_data.contracts.kospi200_index_daily import KR_KOSPI200_INDEX_DAILY
from stock_data.orchestration.kr_index_daily_incremental import (
    IndexDailyOperationError,
    recover_interrupted_transaction,
    run_atomic_lane_append,
    run_offline_daily_append,
)
from stock_data.storage.atomic_parquet import read_kr_index_daily, write_kr_index_daily_atomic
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kospi200_index_daily import validate_kospi200_index_daily


def index_rows(day: str, *, offset: float = 0) -> pd.DataFrame:
    rows = []
    for symbol, base in (("KOSDAQ", 900.0), ("KOSPI", 3000.0)):
        rows.append([day, symbol, symbol, base, base + 20, base - 10, base + 10 + offset, 10, 100, 1000, "pykrx"])
    return pd.DataFrame(rows, columns=KR_INDEX_DAILY.column_names)


def kospi200_rows(day: str, *, offset: float = 0) -> pd.DataFrame:
    return pd.DataFrame(
        [[day, "KOSPI200", "1028", 3000.0, 3020.0, 2990.0, 3010.0 + offset, 10, 100, 1000, "VALID", "pykrx", "get_index_ohlcv", "KRX_TRADING_DATE_DAILY_FINAL"]],
        columns=KR_KOSPI200_INDEX_DAILY.column_names,
    )


def landing_file(tmp_path: Path, frame: pd.DataFrame, name: str) -> Path:
    path = tmp_path / "data/landing/kr_index" / f"{name}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def test_index_append_is_bounded_immutable_and_idempotent(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_index_daily"
    state = tmp_path / "data/state"
    landing = index_rows("2026-08-10")
    landing_path = landing_file(tmp_path, landing, "2026-08-10")
    landing_bytes = landing_path.read_bytes()
    result = run_offline_daily_append(
        "kr_index_daily", landing_path,
        finalized_market_date="2026-08-10", production_root=production,
        state_root=state, run_id="run-1", finality_confirmed=True,
    )
    assert landing_path.read_bytes() == landing_bytes
    assert result.inserted_rows == 2
    assert read_kr_index_daily(production)["date"].tolist() == ["2026-08-10", "2026-08-10"]
    again = run_offline_daily_append(
        "kr_index_daily", landing_path,
        finalized_market_date="2026-08-10", production_root=production,
        state_root=state, run_id="run-1-retry", finality_confirmed=True,
    )
    assert again.status == "NOOP_IDEMPOTENT"
    assert landing_path.read_bytes() == landing_bytes
    assert (state / "journal/kr_index_daily--run-1.json").exists()


def test_run_boundary_rejects_dataframe_and_non_parquet_path(tmp_path: Path) -> None:
    with pytest.raises(IndexDailyOperationError, match="run boundary"):
        run_offline_daily_append(
            "kr_index_daily", index_rows("2026-08-10"),
            finalized_market_date="2026-08-10", production_root=tmp_path / "out",
            state_root=tmp_path / "state", run_id="run-dataframe", finality_confirmed=True,
        )
    text_path = tmp_path / "landing.txt"
    text_path.write_text("not parquet", encoding="utf-8")
    with pytest.raises(IndexDailyOperationError, match="run boundary"):
        run_offline_daily_append(
            "kr_index_daily", text_path,
            finalized_market_date="2026-08-10", production_root=tmp_path / "out",
            state_root=tmp_path / "state", run_id="run-text", finality_confirmed=True,
        )


def test_index_append_preserves_history(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_index_daily"
    state = tmp_path / "data/state"
    write_kr_index_daily_atomic(index_rows("2026-08-07"), production)
    landing = landing_file(tmp_path, index_rows("2026-08-10"), "append")
    result = run_offline_daily_append(
        "kr_index_daily", landing,
        finalized_market_date="2026-08-10", production_root=production,
        state_root=state, run_id="run-2", finality_confirmed=True,
    )
    assert result.inserted_rows == 2
    assert set(read_kr_index_daily(production)["date"]) == {"2026-08-07", "2026-08-10"}


def test_conflicting_finalized_duplicate_fails_without_overwrite(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_index_daily"
    state = tmp_path / "data/state"
    existing = index_rows("2026-08-10")
    write_kr_index_daily_atomic(existing, production)
    landing = landing_file(tmp_path, index_rows("2026-08-10", offset=1), "conflict")
    before = {path: path.read_bytes() for path in production.rglob("*.parquet")}
    with pytest.raises(IndexDailyOperationError, match="conflicts"):
        run_offline_daily_append(
            "kr_index_daily", landing,
            finalized_market_date="2026-08-10", production_root=production,
            state_root=state, run_id="run-conflict", finality_confirmed=True,
        )
    assert {path: path.read_bytes() for path in production.rglob("*.parquet")} == before


def test_missing_explicit_finality_and_historical_target_fail_closed(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_index_daily"
    state = tmp_path / "data/state"
    write_kr_index_daily_atomic(index_rows("2026-08-10"), production)
    next_landing = landing_file(tmp_path, index_rows("2026-08-11"), "next")
    prior_landing = landing_file(tmp_path, index_rows("2026-08-09"), "prior")
    with pytest.raises(IndexDailyOperationError, match="finality"):
        run_offline_daily_append(
            "kr_index_daily", next_landing,
            finalized_market_date="2026-08-11", production_root=production,
            state_root=state, run_id="run-no-finality", finality_confirmed=False,
        )
    with pytest.raises(IndexDailyOperationError, match="historical target"):
        run_offline_daily_append(
            "kr_index_daily", prior_landing,
            finalized_market_date="2026-08-09", production_root=production,
            state_root=state, run_id="run-history", finality_confirmed=True,
        )


def test_writer_failure_leaves_production_unchanged_and_journals_failure(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_index_daily"
    state = tmp_path / "data/state"
    write_kr_index_daily_atomic(index_rows("2026-08-07"), production)
    before = {path: path.read_bytes() for path in production.rglob("*.parquet")}
    landing = landing_file(tmp_path, index_rows("2026-08-10"), "writer-fail")
    landing_bytes = landing.read_bytes()

    def fail_writer(frame: pd.DataFrame, root: Path) -> None:
        raise RuntimeError("injected interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        run_offline_daily_append(
            "kr_index_daily", landing,
            finalized_market_date="2026-08-10", production_root=production,
            state_root=state, run_id="run-fail", finality_confirmed=True,
            writer=fail_writer,
        )
    assert {path: path.read_bytes() for path in production.rglob("*.parquet")} == before
    assert landing.read_bytes() == landing_bytes
    journal = (state / "journal/kr_index_daily--run-fail.json").read_text(encoding="utf-8")
    assert '"status": "FAILED"' in journal
    assert not (state / "kr_index_daily.json").exists()


def test_checkpoint_failure_rolls_back_production_and_preserves_prior_checkpoint(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_index_daily"
    state = tmp_path / "data/state"
    prior_landing = landing_file(tmp_path, index_rows("2026-08-07"), "checkpoint-prior")
    run_offline_daily_append(
        "kr_index_daily", prior_landing,
        finalized_market_date="2026-08-07", production_root=production,
        state_root=state, run_id="run-prior", finality_confirmed=True,
    )
    before_files = {path: path.read_bytes() for path in production.rglob("*.parquet")}
    before_checkpoint = (state / "kr_index_daily.json").read_bytes()
    landing = landing_file(tmp_path, index_rows("2026-08-10"), "checkpoint-fail")
    landing_bytes = landing.read_bytes()

    def fail_checkpoint(path: Path, payload: object) -> None:
        raise RuntimeError("injected checkpoint failure")

    with pytest.raises(RuntimeError, match="checkpoint failure"):
        run_offline_daily_append(
            "kr_index_daily", landing,
            finalized_market_date="2026-08-10", production_root=production,
            state_root=state, run_id="run-checkpoint-fail", finality_confirmed=True,
            checkpoint_writer=fail_checkpoint,
        )
    assert {path: path.read_bytes() for path in production.rglob("*.parquet")} == before_files
    assert (state / "kr_index_daily.json").read_bytes() == before_checkpoint
    assert landing.read_bytes() == landing_bytes
    assert '"status": "FAILED"' in (state / "journal/kr_index_daily--run-checkpoint-fail.json").read_text(encoding="utf-8")


def test_recover_promoted_transaction_restores_previous_bytes_and_checkpoint(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_index_daily"
    state = tmp_path / "data/state"
    prior_landing = landing_file(tmp_path, index_rows("2026-08-07"), "recovery-prior")
    run_offline_daily_append(
        "kr_index_daily", prior_landing,
        finalized_market_date="2026-08-07", production_root=production,
        state_root=state, run_id="run-recovery-prior", finality_confirmed=True,
    )
    before_files = {path: path.read_bytes() for path in production.rglob("*.parquet")}
    checkpoint_path = state / "kr_index_daily.json"
    before_checkpoint = checkpoint_path.read_bytes()

    transaction = production.parent / ".kr_index_daily.transactions" / "run-recovery"
    stage = transaction / "stage"
    previous = transaction / "previous"
    transaction.mkdir(parents=True)
    production.replace(previous)
    write_kr_index_daily_atomic(index_rows("2026-08-10"), stage)
    stage.replace(production)
    journal = state / "journal/kr_index_daily--run-recovery.json"
    payload = {
        "version": 2, "dataset": "kr_index_daily", "run_id": "run-recovery",
        "status": "PROMOTED", "production_promoted": True,
        "production_root": str(production.resolve()), "transaction_root": str(transaction.resolve()),
        "stage_root": str(stage.resolve()), "previous_root": str(previous.resolve()),
        "checkpoint_path": str(checkpoint_path.resolve()),
        "prior_checkpoint_b64": base64.b64encode(before_checkpoint).decode("ascii"),
    }
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps(payload), encoding="utf-8")

    assert recover_interrupted_transaction(journal) == "RECOVERED"
    assert {path: path.read_bytes() for path in production.rglob("*.parquet")} == before_files
    assert checkpoint_path.read_bytes() == before_checkpoint
    assert json.loads(journal.read_text(encoding="utf-8"))["status"] == "RECOVERED"
    assert not transaction.exists()


def test_succeeded_recovery_only_cleans_recorded_transaction_remnants(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_index_daily"
    state = tmp_path / "data/state"
    prior_landing = landing_file(tmp_path, index_rows("2026-08-07"), "succeeded-prior")
    run_offline_daily_append(
        "kr_index_daily", prior_landing,
        finalized_market_date="2026-08-07", production_root=production,
        state_root=state, run_id="run-succeeded-cleanup", finality_confirmed=True,
    )
    before_files = {path: path.read_bytes() for path in production.rglob("*.parquet")}
    checkpoint_path = state / "kr_index_daily.json"
    before_checkpoint = checkpoint_path.read_bytes()
    journal = state / "journal/kr_index_daily--run-succeeded-cleanup.json"
    transaction = production.parent / ".kr_index_daily.transactions" / "run-succeeded-cleanup"
    (transaction / "stage").mkdir(parents=True)
    (transaction / "stage/marker").write_text("staged", encoding="utf-8")
    (transaction / "previous").mkdir()
    (transaction / "previous/marker").write_text("previous", encoding="utf-8")

    assert recover_interrupted_transaction(journal) == "SUCCEEDED"
    assert {path: path.read_bytes() for path in production.rglob("*.parquet")} == before_files
    assert checkpoint_path.read_bytes() == before_checkpoint
    assert not transaction.exists()


def test_tampered_transaction_journal_fails_closed_without_touching_targets(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_index_daily"
    state = tmp_path / "data/state"
    prior_landing = landing_file(tmp_path, index_rows("2026-08-07"), "tampered-prior")
    run_offline_daily_append(
        "kr_index_daily", prior_landing,
        finalized_market_date="2026-08-07", production_root=production,
        state_root=state, run_id="run-tampered", finality_confirmed=True,
    )
    journal = state / "journal/kr_index_daily--run-tampered.json"
    payload = json.loads(journal.read_text(encoding="utf-8"))
    transaction = production.parent / ".kr_index_daily.transactions" / "run-tampered"
    (transaction / "stage").mkdir(parents=True)
    marker = transaction / "stage/marker"
    marker.write_text("must-remain", encoding="utf-8")
    payload["production_root"] = str((tmp_path / "attacker" / "kr_index_daily").resolve())
    journal.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(IndexDailyOperationError, match="topology differs"):
        recover_interrupted_transaction(journal)
    assert marker.read_text(encoding="utf-8") == "must-remain"
    assert production.exists()


def test_kospi200_route_uses_contract_and_single_explicit_date(tmp_path: Path) -> None:
    production = tmp_path / "data/normalized/kr_kospi200_index_daily"
    state = tmp_path / "data/state"
    landing = landing_file(tmp_path, kospi200_rows("2026-08-10"), "kospi200")
    result = run_offline_daily_append(
        "kr_kospi200_index_daily", landing,
        finalized_market_date="2026-08-10", production_root=production,
        state_root=state, run_id="run-kospi200", finality_confirmed=True,
    )
    assert result.inserted_rows == 1
    restored = read_dataset(production, KR_KOSPI200_INDEX_DAILY, validate_kospi200_index_daily)
    assert restored["date"].tolist() == ["2026-08-10"]


def test_landing_date_is_not_inferred_from_calendar(tmp_path: Path) -> None:
    landing = landing_file(tmp_path, index_rows("2026-08-10"), "date-mismatch")
    with pytest.raises(IndexDailyOperationError, match="explicitly finalized"):
        run_offline_daily_append(
            "kr_index_daily", landing,
            finalized_market_date="2026-08-11", production_root=tmp_path / "out",
            state_root=tmp_path / "state", run_id="run-mismatch", finality_confirmed=True,
        )


def test_atomic_lane_promotes_both_datasets_and_replays(tmp_path: Path) -> None:
    normalized = tmp_path / "data/normalized"
    state = tmp_path / "data/state"
    kr = landing_file(tmp_path, index_rows("2026-08-10"), "lane-kr")
    k200 = landing_file(tmp_path, kospi200_rows("2026-08-10"), "lane-k200")
    result = run_atomic_lane_append(
        kr_index_landing=kr, kospi200_landing=k200,
        finalized_market_date="2026-08-10", normalized_root=normalized,
        state_root=state, run_id="lane-1", finality_confirmed=True,
    )
    assert result.status == "SUCCEEDED"
    assert {item.dataset for item in result.datasets} == {
        "kr_index_daily", "kr_kospi200_index_daily",
    }
    replay = run_atomic_lane_append(
        kr_index_landing=kr, kospi200_landing=k200,
        finalized_market_date="2026-08-10", normalized_root=normalized,
        state_root=state, run_id="lane-2", finality_confirmed=True,
    )
    assert replay.status == "NOOP_IDEMPOTENT"


def test_atomic_lane_checkpoint_failure_rolls_back_both_datasets(tmp_path: Path) -> None:
    normalized = tmp_path / "data/normalized"
    state = tmp_path / "data/state"
    prior_kr = landing_file(tmp_path, index_rows("2026-08-07"), "prior-kr")
    prior_k200 = landing_file(tmp_path, kospi200_rows("2026-08-07"), "prior-k200")
    run_atomic_lane_append(
        kr_index_landing=prior_kr, kospi200_landing=prior_k200,
        finalized_market_date="2026-08-07", normalized_root=normalized,
        state_root=state, run_id="lane-prior", finality_confirmed=True,
    )
    before = {
        path.relative_to(normalized): path.read_bytes()
        for path in normalized.rglob("*.parquet")
    }
    kr = landing_file(tmp_path, index_rows("2026-08-10"), "next-kr")
    k200 = landing_file(tmp_path, kospi200_rows("2026-08-10"), "next-k200")

    def fail_checkpoint(path: Path, payload: object) -> None:
        raise RuntimeError("injected lane checkpoint failure")

    with pytest.raises(RuntimeError, match="lane checkpoint"):
        run_atomic_lane_append(
            kr_index_landing=kr, kospi200_landing=k200,
            finalized_market_date="2026-08-10", normalized_root=normalized,
            state_root=state, run_id="lane-fail", finality_confirmed=True,
            checkpoint_writer=fail_checkpoint,
        )
    assert {
        path.relative_to(normalized): path.read_bytes()
        for path in normalized.rglob("*.parquet")
    } == before
