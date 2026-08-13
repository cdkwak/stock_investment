from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_data.contracts.kr_equity import (
    KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
    KR_EQUITY_PRICE_DAILY,
)
from stock_data.contracts.kr_market import KR_MARKET_BREADTH_DAILY
from stock_data.pipelines.market_breadth_rebuild import (
    DATASET,
    MarketBreadthRebuildError,
    rebuild_market_breadth,
)
from stock_data.pipelines import market_breadth_rebuild as breadth_rebuild
from stock_data.storage.contract_arrow import contract_arrow_schema, dataframe_to_contract_table


def _write(root: Path, relative: str, frame: pd.DataFrame, contract) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(dataframe_to_contract_table(frame, contract), path)


def _fixtures(
    root: Path, *, wrong_existing: bool = False, existing_state: bool = False
) -> Path:
    price_rows = []
    universe_rows = []
    closes = {
        "2025-12-30": {"000001": 100, "000002": 50},
        "2026-01-02": {"000001": 110, "000002": 50},
    }
    for day, symbols in closes.items():
        for symbol, close in symbols.items():
            price_rows.append({
                "date": day, "market": "KOSPI", "symbol": symbol,
                "open": close, "high": close, "low": close, "close": close,
                "volume": 1, "trading_value": close,
                "source": "fixture", "source_operation": "fixture", "source_date": day,
            })
            universe_rows.append({
                "date": day, "market": "KOSPI", "symbol": symbol, "isin": None,
                "name": symbol, "listed_info_present": True, "price_present": True,
                "master_present": False, "universe_source": "listed_info+price",
                "security_type": "common", "listing_date": None, "delisting_date": None,
            })
    prices = pd.DataFrame(price_rows, columns=KR_EQUITY_PRICE_DAILY.column_names)
    universe = pd.DataFrame(
        universe_rows, columns=KR_EQUITY_CANONICAL_UNIVERSE_DAILY.column_names
    )
    for year in (2025, 2026):
        _write(
            root,
            f"data/normalized/kr_equity_price_daily/market=KOSPI/year={year}/data.parquet",
            prices[prices.date.str[:4].eq(str(year))].reset_index(drop=True),
            KR_EQUITY_PRICE_DAILY,
        )
        _write(
            root,
            f"data/published/kr_equity_canonical_universe_daily/market=KOSPI/year={year}/data.parquet",
            universe[universe.date.str[:4].eq(str(year))].reset_index(drop=True),
            KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
        )
    existing = pd.DataFrame([{
        "date": "2026-01-02", "market": "KOSPI",
        "advancing": 0 if wrong_existing else 1,
        "declining": 0, "unchanged": 2 if wrong_existing else 1, "total": 2,
    }], columns=KR_MARKET_BREADTH_DAILY.column_names)
    output = root / "data/derived/kr_market_breadth_daily/market=KOSPI/year=2026/data.parquet"
    _write(root, output.relative_to(root).as_posix(), existing, KR_MARKET_BREADTH_DAILY)
    if existing_state:
        state = root / "data/state/kr_market_breadth_daily_rebuild.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text('{"old":true}', encoding="utf-8")
    return output


def _verified_transaction(root: Path, txid: str = "d" * 32):
    output = root / "data/derived/kr_market_breadth_daily/market=KOSPI/year=2026/data.parquet"
    state = root / "data/state/kr_market_breadth_daily_rebuild.json"
    stage, backup, state_backup = breadth_rebuild._transaction_paths(root, txid)
    stage.mkdir()
    import shutil
    output_root = output.parent.parent.parent
    shutil.copytree(output_root, backup)
    state_backup.write_bytes(state.read_bytes())
    output_manifest = breadth_rebuild._manifest(root, output_root)
    payload = {
        "version": 1, "dataset": DATASET, "transaction_id": txid,
        "phase": "VERIFIED", "state_existed": True,
        "stage_relative": stage.relative_to(root).as_posix(),
        "backup_relative": backup.relative_to(root).as_posix(),
        "state_backup_relative": state_backup.relative_to(root).as_posix(),
        "original_output_manifest_sha256": breadth_rebuild._manifest_digest(output_manifest),
        "original_state_sha256": breadth_rebuild._file_digest(state_backup),
        "expected_output_manifest_sha256": breadth_rebuild._manifest_digest(output_manifest),
        "expected_state_sha256": breadth_rebuild._file_digest(state),
    }
    marker = root / breadth_rebuild.MARKER_PATH
    marker.write_text(json.dumps(payload), encoding="utf-8")
    return output_root, state, stage, backup, state_backup, marker, payload


def test_dry_run_preserves_existing_values_and_records_retained_lineage(tmp_path: Path) -> None:
    output = _fixtures(tmp_path)
    original = output.read_bytes()
    result = rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    state = result["state"]
    assert result["status"] == "DRY_RUN_PASS"
    assert state["api_calls"] == 0
    assert state["rows"] == 1
    assert state["coverage_start"] == state["coverage_end"] == "2026-01-02"
    assert state["existing_values_preserved"]["existing_rows"] == 1
    assert set(state["input_manifests"]) == {
        "kr_equity_price_daily", "kr_equity_canonical_universe_daily"
    }
    assert state["output_manifest"][0]["path"] == (
        "data/derived/kr_market_breadth_daily/market=KOSPI/year=2026/data.parquet"
    )
    assert output.read_bytes() == original
    assert not (tmp_path / "data/state/kr_market_breadth_daily_rebuild.json").exists()


def test_rebuild_fails_closed_if_an_existing_value_would_change(tmp_path: Path) -> None:
    output = _fixtures(tmp_path, wrong_existing=True)
    original = output.read_bytes()
    with pytest.raises(MarketBreadthRebuildError, match="changes existing"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    assert output.read_bytes() == original
    assert not list((tmp_path / "data").glob(f".{DATASET}.rebuild.*"))


def test_apply_is_atomic_and_writes_exact_contract_and_state(tmp_path: Path) -> None:
    output = _fixtures(tmp_path)
    with pytest.raises(MarketBreadthRebuildError, match="exact dataset confirmation"):
        rebuild_market_breadth(project_root=tmp_path, mode="apply")
    result = rebuild_market_breadth(
        project_root=tmp_path, mode="apply", confirmation=DATASET
    )
    assert result["status"] == "REBUILT"
    assert pq.ParquetFile(output).schema_arrow.equals(
        contract_arrow_schema(KR_MARKET_BREADTH_DAILY), check_metadata=False
    )
    state_path = tmp_path / "data/state/kr_market_breadth_daily_rebuild.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == result["state"]
    assert state["output_manifest"][0]["bytes"] == output.stat().st_size
    assert not (tmp_path / "data/state/.kr_market_breadth_daily.rebuild.transaction.json").exists()
    assert not list((tmp_path / "data/derived").glob(f".{DATASET}.rebuild.backup.*"))


def test_apply_rolls_back_output_and_state_on_promotion_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _fixtures(tmp_path)
    original = output.read_bytes()
    original_replace = Path.replace

    def fail_state_promotion(path: Path, target: Path):
        if path.name == "state.json":
            raise OSError("injected state promotion failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_state_promotion)
    with pytest.raises(OSError, match="injected"):
        rebuild_market_breadth(
            project_root=tmp_path, mode="apply", confirmation=DATASET
        )
    assert output.read_bytes() == original
    assert not (tmp_path / "data/state/kr_market_breadth_daily_rebuild.json").exists()
    assert not (tmp_path / "data/state/.kr_market_breadth_daily.rebuild.transaction.json").exists()
    assert not list((tmp_path / "data/derived").glob(f".{DATASET}.rebuild.backup.*"))


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("price", "physical schema"),
        ("universe", "row market"),
        ("existing", "row year"),
    ],
)
def test_exact_schema_and_partition_row_identity_are_required(
    tmp_path: Path, source: str, message: str
) -> None:
    output = _fixtures(tmp_path)
    if source == "price":
        path = next((tmp_path / "data/normalized/kr_equity_price_daily").rglob("data.parquet"))
        pd.read_parquet(path).to_parquet(path, index=False)
    elif source == "universe":
        path = next((tmp_path / "data/published/kr_equity_canonical_universe_daily").rglob("data.parquet"))
        frame = pd.read_parquet(path)
        frame["market"] = "KOSDAQ"
        pq.write_table(
            dataframe_to_contract_table(frame, KR_EQUITY_CANONICAL_UNIVERSE_DAILY), path
        )
    else:
        frame = pd.read_parquet(output)
        frame["date"] = pd.to_datetime("2025-12-30")
        pq.write_table(dataframe_to_contract_table(frame, KR_MARKET_BREADTH_DAILY), output)
    with pytest.raises(MarketBreadthRebuildError, match=message):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")


def test_staged_output_requires_exact_arrow_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixtures(tmp_path)
    original = breadth_rebuild.dataframe_to_contract_table

    def drifted(frame: pd.DataFrame, contract):
        if contract is KR_MARKET_BREADTH_DAILY:
            return pa.Table.from_pandas(frame, preserve_index=False)
        return original(frame, contract)

    monkeypatch.setattr(breadth_rebuild, "dataframe_to_contract_table", drifted)
    with pytest.raises(MarketBreadthRebuildError, match="physical schema"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")


@pytest.mark.parametrize("target", ["output", "state"])
def test_existing_output_and_state_use_compare_and_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    output = _fixtures(tmp_path, existing_state=True)
    state = tmp_path / "data/state/kr_market_breadth_daily_rebuild.json"
    original_verify = breadth_rebuild._verify_existing_preserved

    def mutate_after_snapshot(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        if target == "output":
            output.write_bytes(output.read_bytes() + b"changed")
        else:
            state.write_text('{"changed":true}', encoding="utf-8")
        return result

    monkeypatch.setattr(breadth_rebuild, "_verify_existing_preserved", mutate_after_snapshot)
    with pytest.raises(MarketBreadthRebuildError, match=f"existing {target} changed"):
        rebuild_market_breadth(
            project_root=tmp_path, mode="apply", confirmation=DATASET
        )
    assert not (tmp_path / "data/state/.kr_market_breadth_daily.rebuild.transaction.json").exists()


def test_single_writer_lock_fails_closed(tmp_path: Path) -> None:
    _fixtures(tmp_path)
    lock = tmp_path / "data/state/.kr_market_breadth_daily.rebuild.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("active", encoding="utf-8")
    with pytest.raises(MarketBreadthRebuildError, match="lock is active"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    assert lock.read_text(encoding="utf-8") == "active"


@pytest.mark.parametrize(
    ("failure", "after_action"),
    [
        (failure, after)
        for failure in ("root_backup", "root_promote", "state_backup", "state_promote")
        for after in (False, True)
    ] + [("verified_marker", False)],
)
def test_every_promotion_crash_boundary_restores_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, after_action: bool
) -> None:
    output = _fixtures(tmp_path, existing_state=True)
    state = tmp_path / "data/state/kr_market_breadth_daily_rebuild.json"
    original_output = output.read_bytes()
    original_state = state.read_bytes()
    original_replace = Path.replace
    original_write_atomic = breadth_rebuild._write_atomic

    def injected_replace(path: Path, target: Path):
        target_name = Path(target).name
        should_fail = (
            (failure == "root_backup" and path.name == DATASET and "backup" in target_name)
            or (failure == "root_promote" and path.name == DATASET and target_name == DATASET)
            or (failure == "state_backup" and path.name == state.name and "backup" in target_name)
            or (failure == "state_promote" and path.name == "state.json")
        )
        if should_fail:
            if after_action:
                original_replace(path, target)
            raise OSError(f"injected {failure}")
        return original_replace(path, target)

    def injected_marker(path: Path, value: object):
        if failure == "verified_marker" and isinstance(value, dict) and value.get("phase") == "VERIFIED":
            raise OSError("injected verified_marker")
        return original_write_atomic(path, value)

    monkeypatch.setattr(Path, "replace", injected_replace)
    monkeypatch.setattr(breadth_rebuild, "_write_atomic", injected_marker)
    with pytest.raises(OSError, match="injected"):
        rebuild_market_breadth(
            project_root=tmp_path, mode="apply", confirmation=DATASET
        )
    assert output.read_bytes() == original_output
    assert state.read_bytes() == original_state
    assert not (tmp_path / "data/state/.kr_market_breadth_daily.rebuild.transaction.json").exists()


def test_recovery_rejects_orphan_and_marker_path_escape(tmp_path: Path) -> None:
    _fixtures(tmp_path)
    orphan = tmp_path / f"data/.{DATASET}.rebuild.stage.{'a' * 32}"
    orphan.mkdir()
    with pytest.raises(MarketBreadthRebuildError, match="orphan"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    orphan.rmdir()

    txid = "b" * 32
    stage, backup, state_backup = breadth_rebuild._transaction_paths(tmp_path, txid)
    stage.mkdir()
    marker = tmp_path / breadth_rebuild.MARKER_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1, "dataset": DATASET, "transaction_id": txid,
        "phase": "PREPARED", "state_existed": False,
        "stage_relative": "../escape", "backup_relative": backup.relative_to(tmp_path).as_posix(),
        "state_backup_relative": state_backup.relative_to(tmp_path).as_posix(),
        "original_output_manifest_sha256": "0" * 64, "original_state_sha256": None,
        "expected_output_manifest_sha256": "1" * 64, "expected_state_sha256": "2" * 64,
    }
    marker.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketBreadthRebuildError, match="path is unsafe"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")


def test_recovery_rejects_transaction_junction_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixtures(tmp_path)
    txid = "a" * 32
    stage, backup, state_backup = breadth_rebuild._transaction_paths(tmp_path, txid)
    stage.mkdir()
    marker = tmp_path / breadth_rebuild.MARKER_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1, "dataset": DATASET, "transaction_id": txid,
        "phase": "PREPARED", "state_existed": False,
        "stage_relative": stage.relative_to(tmp_path).as_posix(),
        "backup_relative": backup.relative_to(tmp_path).as_posix(),
        "state_backup_relative": state_backup.relative_to(tmp_path).as_posix(),
        "original_output_manifest_sha256": "0" * 64, "original_state_sha256": None,
        "expected_output_manifest_sha256": "1" * 64, "expected_state_sha256": "2" * 64,
    }
    marker.write_text(json.dumps(payload), encoding="utf-8")
    original_resolve = Path.resolve

    def escaped(path: Path, *args, **kwargs):
        if path == stage:
            return Path("C:/outside/rebuild-stage")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", escaped)
    with pytest.raises(MarketBreadthRebuildError, match="escapes project root"):
        breadth_rebuild._recover(tmp_path)
    assert marker.exists() and stage.exists()


def test_verified_corruption_retains_backups(tmp_path: Path) -> None:
    output = _fixtures(tmp_path, existing_state=True)
    state = tmp_path / "data/state/kr_market_breadth_daily_rebuild.json"
    txid = "c" * 32
    stage, backup, state_backup = breadth_rebuild._transaction_paths(tmp_path, txid)
    stage.mkdir()
    output.parent.parent.parent.replace(backup)
    backup.replace(output.parent.parent.parent)
    # Preserve a real backup while leaving canonical output in place.
    import shutil
    shutil.copytree(output.parent.parent.parent, backup)
    state_backup.write_bytes(state.read_bytes())
    output_manifest = breadth_rebuild._manifest(tmp_path, output.parent.parent.parent)
    payload = {
        "version": 1, "dataset": DATASET, "transaction_id": txid,
        "phase": "VERIFIED", "state_existed": True,
        "stage_relative": stage.relative_to(tmp_path).as_posix(),
        "backup_relative": backup.relative_to(tmp_path).as_posix(),
        "state_backup_relative": state_backup.relative_to(tmp_path).as_posix(),
        "original_output_manifest_sha256": breadth_rebuild._manifest_digest(output_manifest),
        "original_state_sha256": breadth_rebuild._file_digest(state_backup),
        "expected_output_manifest_sha256": breadth_rebuild._manifest_digest(output_manifest),
        "expected_state_sha256": "f" * 64,
    }
    marker = tmp_path / breadth_rebuild.MARKER_PATH
    marker.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketBreadthRebuildError, match="verified state digest"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    assert backup.is_dir() and state_backup.is_file() and marker.is_file()


def test_valid_verified_recovery_checks_hashes_then_retires_backups(tmp_path: Path) -> None:
    _fixtures(tmp_path, existing_state=True)
    root, state, stage, backup, state_backup, marker, _ = _verified_transaction(tmp_path)
    assert breadth_rebuild._recover(tmp_path) == "FINALIZED"
    assert root.is_dir() and state.is_file()
    assert not backup.exists() and not state_backup.exists() and not stage.exists()
    assert not marker.exists()


@pytest.mark.parametrize(
    ("failed_phase", "deleted_path"),
    [
        ("STATE_BACKUP_RETIRING", "output_backup"),
        ("CLEANUP_PENDING", "state_backup"),
    ],
)
def test_verified_cleanup_resumes_after_each_backup_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_phase: str,
    deleted_path: str,
) -> None:
    _fixtures(tmp_path, existing_state=True)
    root, state, stage, backup, state_backup, marker, _ = _verified_transaction(tmp_path)
    original_write = breadth_rebuild._write_atomic

    def crash_before_next_journal(path: Path, value: object):
        if isinstance(value, dict) and value.get("phase") == failed_phase:
            raise OSError(f"crash after {deleted_path} deletion")
        return original_write(path, value)

    monkeypatch.setattr(breadth_rebuild, "_write_atomic", crash_before_next_journal)
    with pytest.raises(OSError, match="crash after"):
        breadth_rebuild._recover(tmp_path)
    if deleted_path == "output_backup":
        assert not backup.exists() and state_backup.exists()
        assert json.loads(marker.read_text(encoding="utf-8"))["phase"] == "OUTPUT_BACKUP_RETIRING"
    else:
        assert not backup.exists() and not state_backup.exists()
        assert json.loads(marker.read_text(encoding="utf-8"))["phase"] == "STATE_BACKUP_RETIRING"
    monkeypatch.setattr(breadth_rebuild, "_write_atomic", original_write)
    assert breadth_rebuild._recover(tmp_path) == "FINALIZED"
    assert root.exists() and state.exists()
    assert not stage.exists() and not marker.exists()


@pytest.mark.parametrize(
    "relative",
    [
        breadth_rebuild.PRICE_ROOT,
        breadth_rebuild.UNIVERSE_ROOT,
        breadth_rebuild.OUTPUT_ROOT,
        breadth_rebuild.STATE_PATH,
        breadth_rebuild.MARKER_PATH,
        breadth_rebuild.LOCK_PATH,
    ],
)
def test_fresh_run_rejects_resolved_fixed_path_escape_before_lock_or_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: Path
) -> None:
    _fixtures(tmp_path)
    target = tmp_path / relative
    original_resolve = Path.resolve

    def escaped(path: Path, *args, **kwargs):
        if path == target:
            return Path("C:/outside") / relative.name
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", escaped)
    with pytest.raises(MarketBreadthRebuildError, match="escapes project root"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    assert not (tmp_path / breadth_rebuild.LOCK_PATH).exists()


def test_fresh_run_rejects_resolved_transaction_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixtures(tmp_path)
    original_resolve = Path.resolve

    def escaped(path: Path, *args, **kwargs):
        if path.name.startswith(f".{DATASET}.rebuild.stage."):
            return Path("C:/outside") / path.name
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", escaped)
    with pytest.raises(MarketBreadthRebuildError, match="escapes project root"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    assert not (tmp_path / breadth_rebuild.LOCK_PATH).exists()
    assert not list((tmp_path / "data").glob(f".{DATASET}.rebuild.stage.*"))


@pytest.mark.parametrize("corruption", ["json", "phase", "digest", "extra_key"])
def test_marker_corruption_fails_closed(tmp_path: Path, corruption: str) -> None:
    _fixtures(tmp_path)
    txid = "e" * 32
    stage, backup, state_backup = breadth_rebuild._transaction_paths(tmp_path, txid)
    stage.mkdir()
    marker = tmp_path / breadth_rebuild.MARKER_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1, "dataset": DATASET, "transaction_id": txid,
        "phase": "PREPARED", "state_existed": False,
        "stage_relative": stage.relative_to(tmp_path).as_posix(),
        "backup_relative": backup.relative_to(tmp_path).as_posix(),
        "state_backup_relative": state_backup.relative_to(tmp_path).as_posix(),
        "original_output_manifest_sha256": "0" * 64, "original_state_sha256": None,
        "expected_output_manifest_sha256": "1" * 64, "expected_state_sha256": "2" * 64,
    }
    if corruption == "json":
        marker.write_text("{", encoding="utf-8")
    else:
        if corruption == "phase":
            payload["phase"] = "UNKNOWN"
        elif corruption == "digest":
            payload["expected_state_sha256"] = "nope"
        else:
            payload["unexpected"] = True
        marker.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MarketBreadthRebuildError, match="invalid rebuild transaction marker"):
        rebuild_market_breadth(project_root=tmp_path, mode="dry-run")
    assert marker.exists() and stage.exists()
