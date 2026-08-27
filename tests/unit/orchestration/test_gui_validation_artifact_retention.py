from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import scripts.maintenance.prune_gui_validation_artifacts as retention
from scripts.maintenance.prune_gui_validation_artifacts import (
    MANIFEST_NAME,
    RetentionSafetyError,
    _has_reparse_attribute,
    build_retention_plan,
    collect_active_references,
    run_retention,
)


def _artifact(project: Path, name: str, *, mtime_ns: int, body: bytes = b"x") -> Path:
    path = project / "artifacts" / "gui_validation" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def _dry_digest(project: Path, *, keep_unreferenced: int) -> str:
    payload = run_retention(project, keep_unreferenced=keep_unreferenced)
    return str(payload["plan_digest_sha256"])


def test_active_reference_parsing_excludes_archive_and_reports_missing(tmp_path: Path) -> None:
    existing = _artifact(tmp_path, "kept.png", mtime_ns=1)
    docs = tmp_path / "docs" / "gui"
    archive = tmp_path / "docs" / "archive"
    docs.mkdir(parents=True)
    archive.mkdir(parents=True)
    (docs / "GUI_STATUS.md").write_text(
        "`artifacts/gui_validation/kept.png` and "
        "artifacts\\gui_validation\\missing.json\\n",
        encoding="utf-8",
    )
    (archive / "OLD.md").write_text(
        "artifacts/gui_validation/archive-only.png\n", encoding="utf-8"
    )

    assert collect_active_references(tmp_path) == ("kept.png", "missing.json")
    plan = build_retention_plan(tmp_path, keep_unreferenced=0)
    assert [item.path for item in plan.referenced] == [existing]
    assert plan.missing_references == ("missing.json",)


def test_current_filename_date_bundle_is_protected_before_recent_limit(tmp_path: Path) -> None:
    for index in range(25):
        _artifact(tmp_path, f"old-{index:02d}-20260825.png", mtime_ns=index + 1)
    for index in range(22):
        _artifact(tmp_path, f"accept-{index:02d}-20260826.png", mtime_ns=100 + index)

    plan = build_retention_plan(tmp_path, keep_unreferenced=20)

    assert plan.current_bundle_id == "FILENAME_DATE:20260826"
    assert len(plan.current_bundle) == 22
    assert len(plan.kept_recent) == 20
    assert [item.relative_path for item in plan.eligible] == [
        f"old-{index:02d}-20260825.png" for index in range(4, -1, -1)
    ]


def test_reference_beats_age_and_order_is_deterministic(tmp_path: Path) -> None:
    for name in ("a-20260825.png", "b-20260825.png", "c-20260825.png"):
        _artifact(tmp_path, name, mtime_ns=10)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=20)
    docs = tmp_path / "docs" / "gui"
    docs.mkdir(parents=True)
    (docs / "GUI_STATUS.md").write_text(
        "artifacts/gui_validation/a-20260825.png\n", encoding="utf-8"
    )

    plan = build_retention_plan(tmp_path, keep_unreferenced=1)

    assert [item.relative_path for item in plan.referenced] == ["a-20260825.png"]
    assert [item.relative_path for item in plan.kept_recent] == ["c-20260825.png"]
    assert [item.relative_path for item in plan.eligible] == ["b-20260825.png"]


def test_dry_run_apply_and_second_apply_are_idempotent(tmp_path: Path) -> None:
    _artifact(tmp_path, "keep-20260826.png", mtime_ns=30, body=b"keep")
    _artifact(tmp_path, "recent-20260825.png", mtime_ns=20, body=b"recent")
    doomed = _artifact(tmp_path, "old-20260824.png", mtime_ns=10, body=b"old")

    dry_run = run_retention(tmp_path, keep_unreferenced=1)
    assert dry_run["status"] == "DRY_RUN"
    assert dry_run["counts"]["eligible"] == 1
    assert doomed.exists()
    assert str(tmp_path) not in json.dumps(dry_run)

    applied = run_retention(
        tmp_path,
        keep_unreferenced=1,
        apply=True,
        reviewed_plan_digest=str(dry_run["plan_digest_sha256"]),
    )
    assert applied["status"] == "APPLIED"
    assert applied["counts"]["deleted"] == 1
    assert applied["reviewed_plan_digest_sha256"] == dry_run["plan_digest_sha256"]
    assert not doomed.exists()
    receipts_root = (
        tmp_path / "artifacts" / "gui_validation" / retention.RECEIPTS_NAME
    )
    first_receipts = list(receipts_root.rglob("APPLIED.json"))
    assert len(first_receipts) == 1
    first_receipt_bytes = first_receipts[0].read_bytes()

    second_digest = _dry_digest(tmp_path, keep_unreferenced=1)
    second = run_retention(
        tmp_path,
        keep_unreferenced=1,
        apply=True,
        reviewed_plan_digest=second_digest,
    )
    assert second["status"] == "APPLIED"
    assert second["counts"]["eligible"] == 0
    assert second["counts"]["deleted"] == 0
    assert second["counts"]["inventory_files"] == 2
    assert len(list(receipts_root.rglob("APPLIED.json"))) == 2
    assert first_receipts[0].read_bytes() == first_receipt_bytes
    manifest = json.loads(
        (tmp_path / "artifacts" / "gui_validation" / MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["counts"] == second["counts"]


def test_artifact_symlink_fails_closed_without_deleting_target(tmp_path: Path) -> None:
    root = tmp_path / "artifacts" / "gui_validation"
    root.mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    link = root / "linked.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable in this Windows environment")

    with pytest.raises(RetentionSafetyError, match="link or reparse"):
        run_retention(tmp_path, keep_unreferenced=0, apply=True)
    assert outside.read_bytes() == b"outside"


def test_windows_reparse_attribute_is_recognized() -> None:
    fake = SimpleNamespace(st_file_attributes=0x400)
    assert _has_reparse_attribute(fake) is True
    assert _has_reparse_attribute(SimpleNamespace(st_file_attributes=0)) is False


def test_real_windows_junction_in_artifact_tree_fails_closed(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction probe")
    root = tmp_path / "artifacts" / "gui_validation"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("preserve", encoding="utf-8")
    junction = root / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")
    try:
        with pytest.raises(RetentionSafetyError, match="link or reparse"):
            build_retention_plan(tmp_path, keep_unreferenced=0)
    finally:
        junction.rmdir()
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_unicode_artifact_reference_is_protected(tmp_path: Path) -> None:
    _artifact(tmp_path, "승인-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    docs = tmp_path / "docs" / "gui"
    docs.mkdir(parents=True)
    (docs / "GUI_STATUS.md").write_text(
        "artifacts/gui_validation/승인-20260825.png\n", encoding="utf-8"
    )

    plan = build_retention_plan(tmp_path, keep_unreferenced=0)

    assert [item.relative_path for item in plan.referenced] == ["승인-20260825.png"]
    assert plan.eligible == ()


def test_windows_case_only_reference_protects_actual_artifact(tmp_path: Path) -> None:
    _artifact(tmp_path, "Keep-20260825.PNG", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    docs = tmp_path / "docs" / "gui"
    docs.mkdir(parents=True)
    (docs / "GUI_STATUS.md").write_text(
        "artifacts/gui_validation/keep-20260825.png\n", encoding="utf-8"
    )

    plan = build_retention_plan(tmp_path, keep_unreferenced=0)

    assert [item.relative_path for item in plan.referenced] == ["Keep-20260825.PNG"]
    assert plan.eligible == ()


def test_apply_rechecks_references_added_after_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed = _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    docs = tmp_path / "docs" / "gui"
    docs.mkdir(parents=True)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    original = retention._atomic_write_manifest

    def write_then_reference(root: Path, payload: dict[str, object]) -> Path:
        result = original(root, payload)
        if payload["status"] == "APPLY_PLANNED":
            (docs / "GUI_STATUS.md").write_text(
                "artifacts/gui_validation/old-20260825.png\n", encoding="utf-8"
            )
        return result

    monkeypatch.setattr(retention, "_atomic_write_manifest", write_then_reference)
    with pytest.raises(RetentionSafetyError, match="became actively referenced"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )
    assert doomed.exists()
    manifest = json.loads(
        (tmp_path / "artifacts" / "gui_validation" / MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "APPLY_ROLLED_BACK"
    assert manifest["counts"]["deleted"] == 0


def test_apply_hash_binds_content_when_size_and_mtime_are_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed = _artifact(tmp_path, "old-20260825.png", mtime_ns=1, body=b"first")
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    original = retention._atomic_write_manifest

    def write_then_replace(root: Path, payload: dict[str, object]) -> Path:
        result = original(root, payload)
        if payload["status"] == "APPLY_PLANNED":
            doomed.write_bytes(b"other")
            os.utime(doomed, ns=(1, 1))
        return result

    monkeypatch.setattr(retention, "_atomic_write_manifest", write_then_replace)
    with pytest.raises(RetentionSafetyError, match="content changed"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )
    assert doomed.read_bytes() == b"other"


def test_inventory_matching_protects_space_name_with_uri_fragment(tmp_path: Path) -> None:
    _artifact(tmp_path, "accepted image-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    docs = tmp_path / "docs" / "gui"
    docs.mkdir(parents=True)
    (docs / "GUI_STATUS.md").write_text(
        "artifacts/gui_validation/accepted image-20260825.png#full\n",
        encoding="utf-8",
    )

    plan = build_retention_plan(tmp_path, keep_unreferenced=0)

    assert [item.relative_path for item in plan.referenced] == [
        "accepted image-20260825.png"
    ]
    assert plan.eligible == ()


def test_post_revalidation_reference_restores_quarantined_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed = _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    docs = tmp_path / "docs" / "gui"
    docs.mkdir(parents=True)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    original = retention._revalidate_before_delete

    def validate_then_reference(root: Path, artifact: retention.Artifact) -> None:
        original(root, artifact)
        if artifact.relative_path == "old-20260825.png":
            (docs / "GUI_STATUS.md").write_text(
                "artifacts/gui_validation/old-20260825.png\n", encoding="utf-8"
            )

    monkeypatch.setattr(retention, "_revalidate_before_delete", validate_then_reference)
    with pytest.raises(RetentionSafetyError, match="before commit"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )

    assert doomed.exists()
    assert not (tmp_path / "artifacts" / "gui_validation" / retention.QUARANTINE_NAME).exists()


def test_post_hash_replacement_is_quarantined_then_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed = _artifact(tmp_path, "old-20260825.png", mtime_ns=1, body=b"first")
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    original = retention._sha256
    calls = 0

    def hash_then_replace(path: Path) -> str:
        nonlocal calls
        digest = original(path)
        if path.name == "old-20260825.png":
            calls += 1
            if calls == 2:
                path.write_bytes(b"other")
                os.utime(path, ns=(1, 1))
        return digest

    monkeypatch.setattr(retention, "_sha256", hash_then_replace)
    with pytest.raises(RetentionSafetyError, match="content changed"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )

    assert doomed.read_bytes() == b"other"
    assert not (tmp_path / "artifacts" / "gui_validation" / retention.QUARANTINE_NAME).exists()


def test_final_manifest_failure_leaves_truthful_purge_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doomed = _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    original = retention._atomic_write_manifest

    def fail_final(root: Path, payload: dict[str, object]) -> Path:
        if payload["status"] == "APPLIED":
            raise OSError("synthetic final manifest failure")
        return original(root, payload)

    monkeypatch.setattr(retention, "_atomic_write_manifest", fail_final)
    with pytest.raises(OSError, match="synthetic final"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )

    assert not doomed.exists()
    manifest = json.loads(
        (tmp_path / "artifacts" / "gui_validation" / MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "PURGE_COMMITTED"
    assert manifest["counts"]["quarantined"] == 1
    assert manifest["transaction"]["id"]
    receipts = list(
        (
            tmp_path
            / "artifacts"
            / "gui_validation"
            / retention.RECEIPTS_NAME
        ).rglob("APPLIED.json")
    )
    assert len(receipts) == 1
    immutable = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert immutable["status"] == "APPLIED"
    assert immutable["counts"]["deleted"] == 1


def test_partial_rollback_receipt_matches_quarantine_and_original_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conflicted = _artifact(
        tmp_path, "b-20260824.png", mtime_ns=2, body=b"aaaa"
    )
    restored = _artifact(
        tmp_path, "a-20260823.png", mtime_ns=1, body=b"bbbb"
    )
    _artifact(tmp_path, "current-20260826.png", mtime_ns=3)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    original = retention._atomic_write_manifest

    def fail_commit_with_conflict(root: Path, payload: dict[str, object]) -> Path:
        if payload["status"] == "PURGE_COMMITTED":
            conflicted.write_bytes(b"new!")
            raise OSError("synthetic commit failure")
        return original(root, payload)

    monkeypatch.setattr(retention, "_atomic_write_manifest", fail_commit_with_conflict)
    with pytest.raises(RetentionSafetyError, match="cannot restore quarantine"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )

    manifest = json.loads(
        (tmp_path / "artifacts" / "gui_validation" / MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    quarantine = (
        tmp_path
        / "artifacts"
        / "gui_validation"
        / retention.QUARANTINE_NAME
        / manifest["transaction"]["id"]
    )
    actual_quarantined = sorted(
        path.relative_to(quarantine).as_posix()
        for path in quarantine.rglob("*")
        if path.is_file()
    )
    states = {
        item["path"]: item["state"] for item in manifest["original_path_states"]
    }

    assert manifest["status"] == "APPLY_RECOVERY_REQUIRED"
    assert manifest["counts"]["quarantined"] == 1
    assert manifest["counts"]["restored"] == 1
    assert [item["path"] for item in manifest["quarantined"]] == actual_quarantined
    assert actual_quarantined == ["b-20260824.png"]
    assert states == {
        "a-20260823.png": "RESTORED_PLANNED",
        "b-20260824.png": "CONFLICT_PRESENT",
    }
    assert conflicted.read_bytes() == b"new!"
    assert restored.read_bytes() == b"bbbb"


def test_apply_requires_explicit_reviewed_dry_run_digest(tmp_path: Path) -> None:
    _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    _dry_digest(tmp_path, keep_unreferenced=0)

    with pytest.raises(RetentionSafetyError, match="reviewed-plan-digest"):
        run_retention(tmp_path, keep_unreferenced=0, apply=True)


def test_immutable_receipt_collision_rejects_before_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    fixed_id = "a" * 32
    receipts = tmp_path / "artifacts" / "gui_validation" / retention.RECEIPTS_NAME
    receipts.mkdir()
    transaction_root = receipts / fixed_id
    transaction_root.mkdir()
    existing = transaction_root / "APPLIED.json"
    existing.write_bytes(b"immutable-existing")
    monkeypatch.setattr(retention.uuid, "uuid4", lambda: SimpleNamespace(hex=fixed_id))

    with pytest.raises(RetentionSafetyError, match="receipt already exists"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )

    assert target.exists()
    assert existing.read_bytes() == b"immutable-existing"


def test_late_receipt_collision_rolls_back_before_purge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    fixed_id = "b" * 32
    monkeypatch.setattr(retention.uuid, "uuid4", lambda: SimpleNamespace(hex=fixed_id))
    original = retention._atomic_write_manifest
    collision: Path | None = None

    def inject_after_commit(root: Path, payload: dict[str, object]) -> Path:
        nonlocal collision
        result = original(root, payload)
        if payload["status"] == "PURGE_COMMITTED":
            collision = (
                root
                / retention.RECEIPTS_NAME
                / fixed_id
                / "APPLIED.json"
            )
            collision.write_bytes(b"late-existing")
        return result

    monkeypatch.setattr(retention, "_atomic_write_manifest", inject_after_commit)
    with pytest.raises(RetentionSafetyError, match="slot was modified"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )

    assert target.exists()
    assert collision is not None
    assert collision.read_bytes() == b"late-existing"
    manifest = json.loads(
        (tmp_path / "artifacts" / "gui_validation" / MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "APPLY_ROLLED_BACK"


def test_tampered_dry_run_manifest_digest_rejects_apply(tmp_path: Path) -> None:
    _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    manifest_path = tmp_path / "artifacts" / "gui_validation" / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["plan_digest_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RetentionSafetyError, match="plan drifted"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )


def test_structurally_incomplete_dry_run_manifest_rejects_apply(tmp_path: Path) -> None:
    _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    manifest_path = tmp_path / "artifacts" / "gui_validation" / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(
            {
                "status": "DRY_RUN",
                "generated_at_utc": "2026-08-26T00:00:00+00:00",
                "plan_digest_sha256": reviewed_digest,
                "policy": {"keep_unreferenced": 0},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RetentionSafetyError, match="incomplete, malformed"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )


def test_add_after_review_rejects_apply_without_deleting_anything(tmp_path: Path) -> None:
    original = _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=3)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    added = _artifact(tmp_path, "added-20260824.png", mtime_ns=2)

    with pytest.raises(RetentionSafetyError, match="plan drifted"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )
    assert original.exists()
    assert added.exists()


def test_modified_after_review_rejects_apply(tmp_path: Path) -> None:
    target = _artifact(tmp_path, "old-20260825.png", mtime_ns=1, body=b"first")
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    target.write_bytes(b"other")
    os.utime(target, ns=(1, 1))

    with pytest.raises(RetentionSafetyError, match="plan drifted"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )
    assert target.read_bytes() == b"other"


def test_byte_identical_file_identity_drift_rejects_apply(tmp_path: Path) -> None:
    target = _artifact(tmp_path, "old-20260825.png", mtime_ns=1, body=b"same")
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    original_metadata = target.stat()
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    replacement = target.with_name("replacement.tmp")
    replacement.write_bytes(b"same")
    os.utime(
        replacement,
        ns=(original_metadata.st_atime_ns, original_metadata.st_mtime_ns),
    )
    os.replace(replacement, target)
    if target.stat().st_ino == original_metadata.st_ino:
        pytest.skip("filesystem did not expose replacement identity drift")

    with pytest.raises(RetentionSafetyError, match="plan drifted"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )
    assert target.read_bytes() == b"same"


def test_reference_drift_rejects_apply(tmp_path: Path) -> None:
    target = _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)
    docs = tmp_path / "docs" / "gui"
    docs.mkdir(parents=True)
    (docs / "GUI_STATUS.md").write_text(
        "artifacts/gui_validation/old-20260825.png\n", encoding="utf-8"
    )

    with pytest.raises(RetentionSafetyError, match="plan drifted"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )
    assert target.exists()


def test_policy_drift_rejects_apply(tmp_path: Path) -> None:
    _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    reviewed_digest = _dry_digest(tmp_path, keep_unreferenced=0)

    with pytest.raises(RetentionSafetyError, match="plan drifted|policy drifted"):
        run_retention(
            tmp_path,
            keep_unreferenced=1,
            apply=True,
            reviewed_plan_digest=reviewed_digest,
        )


def test_malformed_references_are_redacted_and_block_apply(tmp_path: Path) -> None:
    _artifact(tmp_path, "old-20260825.png", mtime_ns=1)
    _artifact(tmp_path, "current-20260826.png", mtime_ns=2)
    docs = tmp_path / "docs" / "gui"
    docs.mkdir(parents=True)
    prefix = "artifacts/gui_" + "validation/"
    sensitive_piece = "C:" + "/Users/private/account.png"
    drive_relative_piece = "C:" + "Users/private/relative.png"
    (docs / "GUI_STATUS.md").write_text(
        "\n".join(
            (
                prefix + sensitive_piece,
                prefix + drive_relative_piece,
                prefix + "/rooted.png",
                prefix + "../traversal.png",
            )
        ),
        encoding="utf-8",
    )

    dry_run = run_retention(tmp_path, keep_unreferenced=0)
    encoded = json.dumps(dry_run)

    assert dry_run["malformed_reference_codes"] == [
        "DRIVE_ABSOLUTE",
        "DRIVE_RELATIVE",
        "ROOTED",
        "TRAVERSAL_OR_NONCANONICAL",
    ]
    assert "Users" not in encoded
    assert "private" not in encoded
    assert dry_run["missing_references"] == []
    with pytest.raises(RetentionSafetyError, match="malformed active"):
        run_retention(
            tmp_path,
            keep_unreferenced=0,
            apply=True,
            reviewed_plan_digest=str(dry_run["plan_digest_sha256"]),
        )
