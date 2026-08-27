from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.storage import backup_restore


def _source_fixture(root: Path, *, suffix: str = "") -> list[backup_restore.BackupItem]:
    parquet = root / "data/normalized/sample/year=2026/data.parquet"
    parquet.parent.mkdir(parents=True)
    pd.DataFrame(
        {"date": ["2026-08-18", "2026-08-19"], "symbol": ["A", "A"], "close": [1.0, 2.0]}
    ).to_parquet(parquet, index=False)
    state = root / "data/state/sample.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"status": "SUCCEEDED", "date": "2026-08-19", "suffix": suffix}), encoding="utf-8")
    landing = root / "data/landing/sample/response.json"
    landing.parent.mkdir(parents=True)
    landing.write_text('{"rows": 2}', encoding="utf-8")
    return [
        backup_restore.BackupItem(
            "data/normalized/sample/year=2026/data.parquet",
            "critical",
            "parquet",
            row_identity_keys=("date", "symbol"),
        ),
        backup_restore.BackupItem(
            "data/state/sample.json", "critical", "json", required_json_keys=("status", "date")
        ),
        backup_restore.BackupItem("data/landing/sample/response.json", "immutable", "json"),
        backup_restore.BackupItem(
            "data/derived/sample/data.parquet", "reproducible", include=False, reason="rebuild"
        ),
        backup_restore.BackupItem(".env", "sensitive", include=False, reason="secret"),
    ]


def test_backup_manifest_is_deterministic_and_restore_is_staging_only(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    items = _source_fixture(source)
    backup_root = tmp_path / "backups"

    first = backup_restore.create_backup(source_root=source, backup_root=backup_root, items=items)
    second = backup_restore.create_backup(source_root=source, backup_root=backup_root, items=items)

    assert second.manifest_sha256 == first.manifest_sha256
    assert second.reused_existing is True
    manifest = backup_restore.verify_backup(backup_root)
    assert manifest["totals"]["files"] == 3
    assert {item["classification"] for item in manifest["excluded"]} == {"reproducible", "sensitive"}
    parquet_entry = next(item for item in manifest["entries"] if item["validation"]["kind"] == "parquet")
    assert parquet_entry["validation"]["rows"] == 2
    assert len(parquet_entry["validation"]["row_identity_sha256"]) == 64

    restored = backup_restore.restore_verified_to_staging(
        backup_root=backup_root, staging_destination=tmp_path / "isolated-restore"
    )
    marker = json.loads((restored / "RESTORE_VERIFIED.json").read_text(encoding="utf-8"))
    assert marker["production_promotion_authorized"] is False
    assert (restored / "data/state/sample.json").read_bytes() == (source / "data/state/sample.json").read_bytes()


def test_corrupt_or_incomplete_backup_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    result = backup_restore.create_backup(
        source_root=source,
        backup_root=tmp_path / "backups",
        items=_source_fixture(source),
    )
    payload = result.version_root / "payload/data/state/sample.json"
    payload.write_text('{"status":"CORRUPT"}', encoding="utf-8")

    with pytest.raises(backup_restore.BackupIntegrityError, match="integrity mismatch"):
        backup_restore.verify_backup(tmp_path / "backups")
    with pytest.raises(backup_restore.BackupIntegrityError):
        backup_restore.restore_verified_to_staging(
            backup_root=tmp_path / "backups", staging_destination=tmp_path / "restore"
        )
    assert not (tmp_path / "restore").exists()

    second_source = tmp_path / "second-source"
    second_source.mkdir()
    second_root = tmp_path / "second-backups"
    incomplete = backup_restore.create_backup(
        source_root=second_source,
        backup_root=second_root,
        items=_source_fixture(second_source),
    )
    (incomplete.version_root / "payload/data/state/sample.json").unlink()
    with pytest.raises(backup_restore.BackupIntegrityError, match="payload missing"):
        backup_restore.verify_backup(second_root)


def test_mismatched_manifest_and_row_identity_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    items = _source_fixture(source)
    with pytest.raises(backup_restore.BackupIntegrityError, match="not unique"):
        duplicate = pd.DataFrame(
            {"date": ["2026-08-18", "2026-08-18"], "symbol": ["A", "A"], "close": [1, 2]}
        )
        duplicate.to_parquet(source / items[0].relative_path, index=False)
        backup_restore.create_backup(source_root=source, backup_root=tmp_path / "backups", items=items)

    duplicate.drop_duplicates(["date", "symbol"]).to_parquet(source / items[0].relative_path, index=False)
    result = backup_restore.create_backup(source_root=source, backup_root=tmp_path / "backups", items=items)
    manifest = result.version_root / "manifest.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["totals"]["files"] = 99
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(backup_restore.BackupIntegrityError, match="manifest digest"):
        backup_restore.verify_backup(tmp_path / "backups", result.manifest_sha256)


def test_interrupted_publication_preserves_last_valid_pointer(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    items = _source_fixture(source)
    backup_root = tmp_path / "backups"
    first = backup_restore.create_backup(source_root=source, backup_root=backup_root, items=items)
    latest_before = (backup_root / "LATEST.json").read_bytes()

    state = source / "data/state/sample.json"
    state.write_text(json.dumps({"status": "SUCCEEDED", "date": "2026-08-20"}), encoding="utf-8")
    monkeypatch.setattr(
        backup_restore,
        "_publish_latest",
        lambda *_: (_ for _ in ()).throw(OSError("simulated pointer interruption")),
    )
    with pytest.raises(OSError, match="interruption"):
        backup_restore.create_backup(source_root=source, backup_root=backup_root, items=items)

    assert (backup_root / "LATEST.json").read_bytes() == latest_before
    manifest = backup_restore.verify_backup(backup_root)
    assert backup_restore._sha256_bytes(backup_restore._canonical_json(manifest)) == first.manifest_sha256


def test_interrupted_restore_does_not_publish_partial_staging(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    backup_root = tmp_path / "backups"
    backup_restore.create_backup(
        source_root=source, backup_root=backup_root, items=_source_fixture(source)
    )
    original_copy = backup_restore.shutil.copy2
    calls = 0

    def interrupted_copy(source_path, target_path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated restore interruption")
        return original_copy(source_path, target_path)

    monkeypatch.setattr(backup_restore.shutil, "copy2", interrupted_copy)
    destination = tmp_path / "isolated-restore"
    with pytest.raises(OSError, match="restore interruption"):
        backup_restore.restore_verified_to_staging(
            backup_root=backup_root, staging_destination=destination
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".restore-stage-*"))


def test_secret_paths_limits_and_existing_restore_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    secret = source / ".env"
    secret.write_text("DO_NOT_READ=1", encoding="utf-8")
    with pytest.raises(backup_restore.BackupIntegrityError, match="sensitive path"):
        backup_restore.create_backup(
            source_root=source,
            backup_root=tmp_path / "backups",
            items=[backup_restore.BackupItem(".env", "critical")],
        )

    items = _source_fixture(source)
    with pytest.raises(backup_restore.BackupIntegrityError, match="exceeds"):
        backup_restore.create_backup(
            source_root=source, backup_root=tmp_path / "backups", items=items, max_files=1
        )
    result = backup_restore.create_backup(source_root=source, backup_root=tmp_path / "backups", items=items)
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(backup_restore.BackupIntegrityError, match="overwrite is forbidden"):
        backup_restore.restore_verified_to_staging(
            backup_root=tmp_path / "backups",
            staging_destination=destination,
            manifest_sha256=result.manifest_sha256,
        )
    with pytest.raises(backup_restore.BackupIntegrityError, match="production data root"):
        backup_restore.restore_verified_to_staging(
            backup_root=tmp_path / "backups",
            staging_destination=backup_restore._PROJECT_DATA_ROOT / "restored-candidate",
            manifest_sha256=result.manifest_sha256,
        )
