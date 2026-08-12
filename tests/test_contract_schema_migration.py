from pathlib import Path
import shutil

import pandas as pd
import pyarrow.parquet as pq
import pytest

from stock_data.contracts.kr_equity import KR_EQUITY_MASTER
from stock_data.migrations import contract_schema
from stock_data.migrations.contract_schema import (
    MIGRATION_SPECS,
    SchemaMigrationError,
    recover_interrupted_transaction,
    run_schema_migration,
)
from stock_data.storage.contract_arrow import contract_arrow_schema


DATASET = "kr_equity_master"
RELATIVE_ROOT = Path("data/normalized/kr_equity_master")


def _master_rows() -> pd.DataFrame:
    rows = []
    for market, symbol, shares in (
        ("KOSDAQ", "000002", None),
        ("KOSPI", "000001", 1_000),
    ):
        rows.append({
            "symbol": symbol,
            "name": "Fixture " + symbol,
            "market": market,
            "isin": None,
            "corp_no": None,
            "company_name": None,
            "security_type_code": None,
            "security_type_name": None,
            "par_value": None,
            "issued_shares": shares,
            "listing_date": None,
            "delisting_date": None,
            "deposit_registration_date": None,
            "deposit_cancellation_date": None,
            "source": "daily_source_identity",
            "source_date": None,
        })
    return pd.DataFrame(rows, columns=KR_EQUITY_MASTER.column_names).sort_values(
        list(KR_EQUITY_MASTER.sort_key), kind="stable"
    ).reset_index(drop=True)


def _write_legacy_root(project_root: Path, *, duplicate: bool = False) -> Path:
    root = project_root / RELATIVE_ROOT
    frame = _master_rows()
    if duplicate:
        frame = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    for market, group in frame.groupby("market", sort=True):
        path = root / f"market={market}" / "data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        group.reset_index(drop=True).to_parquet(path, index=False)
    return root


def _bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("data.parquet"))
    }


def _crash_paths(root: Path, *, phase: str) -> tuple[Path, Path, Path, Path]:
    transaction_id = "a" * 32
    stage = root.parent / f".{DATASET}.schema-migration.stage.{transaction_id}"
    backup = root.parent / f".{DATASET}.schema-migration.backup.{transaction_id}"
    retired = root.parent / f".{DATASET}.schema-migration.retired.{transaction_id}"
    marker = root.parent / f".{DATASET}.schema-migration.transaction.json"
    contract_schema._write_marker(marker, {
        "transaction_id": transaction_id,
        "dataset": DATASET,
        "root_name": root.name,
        "stage_name": stage.name,
        "backup_name": backup.name,
        "retired_name": retired.name,
        "mode": "apply",
        "phase": phase,
    })
    return stage, backup, retired, marker


def test_dry_run_is_non_mutating_and_apply_preserves_exact_manifest(tmp_path: Path) -> None:
    root = _write_legacy_root(tmp_path)
    before = _bytes(root)
    with pytest.raises(SchemaMigrationError, match="physical schema"):
        run_schema_migration(project_root=tmp_path, dataset=DATASET, mode="verify")

    dry = run_schema_migration(project_root=tmp_path, dataset=DATASET, mode="dry-run")
    assert dry["status"] == "DRY_RUN_PASS"
    assert _bytes(root) == before
    assert dry["pre"] == dry["post"]
    assert dry["pre"]["rows"] == 2
    assert dry["pre"]["partitions"] == 2
    assert dry["pre"]["null_counts"]["delisting_date"] == 2
    assert dry["pre"]["pk_check"] == "PASS_EXACT_PER_DISJOINT_PARTITION"
    assert not list(root.parent.glob(f".{DATASET}.schema-migration.*"))

    with pytest.raises(SchemaMigrationError, match="exact dataset confirmation"):
        run_schema_migration(project_root=tmp_path, dataset=DATASET, mode="apply")
    applied = run_schema_migration(
        project_root=tmp_path, dataset=DATASET, mode="apply", confirmation=DATASET
    )
    assert applied["status"] == "MIGRATED"
    assert applied["pre"] == applied["post"]
    expected = contract_arrow_schema(KR_EQUITY_MASTER)
    assert all(
        pq.ParquetFile(path).schema_arrow.equals(expected, check_metadata=False)
        for path in root.rglob("data.parquet")
    )
    verified = run_schema_migration(project_root=tmp_path, dataset=DATASET, mode="verify")
    assert verified["status"] == "VERIFIED"


def test_migration_rejects_non_allowlisted_and_duplicate_pk(tmp_path: Path) -> None:
    assert set(MIGRATION_SPECS) == {
        "kr_equity_market_cap_daily",
        "kr_equity_master",
        "kr_equity_price_daily",
        "kr_equity_universe_daily",
        "kr_equity_canonical_universe_daily",
        "kr_market_investor_net_purchase_bridge_daily",
    }
    for spec in MIGRATION_SPECS.values():
        contract_schema._verify_disjoint_partition_contract(spec.contract)
    with pytest.raises(SchemaMigrationError, match="not allowlisted"):
        run_schema_migration(project_root=tmp_path, dataset="anything_else", mode="dry-run")
    _write_legacy_root(tmp_path, duplicate=True)
    with pytest.raises(SchemaMigrationError, match="duplicate primary key"):
        run_schema_migration(project_root=tmp_path, dataset=DATASET, mode="dry-run")


def test_migration_refuses_to_drop_unexpected_root_files(tmp_path: Path) -> None:
    root = _write_legacy_root(tmp_path)
    (root / "provenance.txt").write_text("must not be dropped", encoding="utf-8")
    with pytest.raises(SchemaMigrationError, match="unexpected files"):
        run_schema_migration(project_root=tmp_path, dataset=DATASET, mode="dry-run")


@pytest.mark.parametrize("change", ["extra", "missing", "reordered"])
def test_migration_rejects_physical_column_drift_before_projection(
    tmp_path: Path, change: str
) -> None:
    root = _write_legacy_root(tmp_path)
    path = next(root.rglob("data.parquet"))
    frame = pd.read_parquet(path)
    if change == "extra":
        frame["unexpected"] = "x"
    elif change == "missing":
        frame = frame.drop(columns="corp_no")
    else:
        frame = frame[list(reversed(frame.columns))]
    frame.to_parquet(path, index=False)
    with pytest.raises(SchemaMigrationError, match="before projection"):
        run_schema_migration(project_root=tmp_path, dataset=DATASET, mode="dry-run")


def test_startup_restores_root_absent_with_transaction_backup(tmp_path: Path) -> None:
    root = _write_legacy_root(tmp_path)
    before = _bytes(root)
    stage, backup, retired, marker = _crash_paths(root, phase="ROOT_BACKED_UP")
    stage.mkdir()
    root.replace(backup)

    action = recover_interrupted_transaction(project_root=tmp_path, dataset=DATASET)
    assert action == "RESTORED_BACKUP"
    assert _bytes(root) == before
    assert not stage.exists() and not backup.exists() and not retired.exists() and not marker.exists()


def test_startup_discards_marker_backed_unpromoted_stage(tmp_path: Path) -> None:
    root = _write_legacy_root(tmp_path)
    before = _bytes(root)
    stage, backup, retired, marker = _crash_paths(root, phase="STAGED")
    stage.mkdir()

    action = recover_interrupted_transaction(project_root=tmp_path, dataset=DATASET)
    assert action == "DISCARDED_UNPROMOTED_STAGE"
    assert _bytes(root) == before
    assert not stage.exists() and not backup.exists() and not retired.exists() and not marker.exists()


def test_startup_finishes_verified_retired_backup_cleanup(tmp_path: Path) -> None:
    root = _write_legacy_root(tmp_path)
    before = _bytes(root)
    stage, backup, retired, marker = _crash_paths(root, phase="BACKUP_RETIRED")
    shutil.copytree(root, retired)

    action = recover_interrupted_transaction(project_root=tmp_path, dataset=DATASET)
    assert action == "FINALIZED_VERIFIED_ROOT"
    assert _bytes(root) == before
    assert not stage.exists() and not backup.exists() and not retired.exists() and not marker.exists()


def test_startup_clears_root_only_backup_retired_marker(tmp_path: Path) -> None:
    root = _write_legacy_root(tmp_path)
    before = _bytes(root)
    stage, backup, retired, marker = _crash_paths(root, phase="BACKUP_RETIRED")

    action = recover_interrupted_transaction(project_root=tmp_path, dataset=DATASET)
    assert action == "CLEARED_MARKER"
    assert _bytes(root) == before
    assert not stage.exists() and not backup.exists() and not retired.exists() and not marker.exists()


def test_startup_refuses_ambiguous_and_unmarked_orphans(tmp_path: Path) -> None:
    root = _write_legacy_root(tmp_path)
    stage, backup, retired, marker = _crash_paths(root, phase="PROMOTED")
    stage.mkdir()
    shutil.copytree(root, backup)
    with pytest.raises(SchemaMigrationError, match="ambiguous interrupted"):
        recover_interrupted_transaction(project_root=tmp_path, dataset=DATASET)
    assert root.exists() and stage.exists() and backup.exists() and marker.exists()

    marker.unlink()
    with pytest.raises(SchemaMigrationError, match="without a marker"):
        recover_interrupted_transaction(project_root=tmp_path, dataset=DATASET)


def test_startup_never_accepts_missing_canonical_root_without_backup(tmp_path: Path) -> None:
    root = tmp_path / RELATIVE_ROOT
    root.parent.mkdir(parents=True)
    with pytest.raises(SchemaMigrationError, match="canonical dataset root is missing"):
        recover_interrupted_transaction(project_root=tmp_path, dataset=DATASET)


def test_failed_rollback_step_retains_recovery_transaction(monkeypatch, tmp_path: Path) -> None:
    root = _write_legacy_root(tmp_path)
    before = _bytes(root)
    stage, backup, retired, marker = _crash_paths(root, phase="PROMOTED")
    shutil.copytree(root, backup)
    original_replace = Path.replace

    with monkeypatch.context() as scoped:
        def fail_rollback_move(self: Path, target: Path):
            if self == root and Path(target) == stage:
                raise OSError("simulated rollback-step failure")
            return original_replace(self, target)

        scoped.setattr(Path, "replace", fail_rollback_move)
        with pytest.raises(SchemaMigrationError, match="automatic transaction recovery failed"):
            recover_interrupted_transaction(project_root=tmp_path, dataset=DATASET)
    assert root.exists() and backup.exists() and marker.exists() and not stage.exists()

    assert recover_interrupted_transaction(
        project_root=tmp_path, dataset=DATASET
    ) == "ROLLED_BACK_PROMOTED_ROOT"
    assert _bytes(root) == before
    assert not stage.exists() and not backup.exists() and not retired.exists() and not marker.exists()


def test_promotion_failure_restores_original_root(monkeypatch, tmp_path: Path) -> None:
    root = _write_legacy_root(tmp_path)
    before = _bytes(root)
    original_replace = Path.replace

    def fail_stage_promotion(self: Path, target: Path):
        if self.name.startswith(f".{DATASET}.schema-migration.stage.") and Path(target) == root:
            raise OSError("simulated stage promotion failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_stage_promotion)
    with pytest.raises(OSError, match="simulated stage promotion"):
        run_schema_migration(
            project_root=tmp_path, dataset=DATASET, mode="apply", confirmation=DATASET
        )
    assert root.exists()
    assert _bytes(root) == before
    assert not list(root.parent.glob(f".{DATASET}.schema-migration.*"))
