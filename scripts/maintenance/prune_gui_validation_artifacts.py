"""Plan or apply bounded retention for generated GUI validation artifacts.

The command is dry-run by default.  It protects artifacts named by active
documentation/code/tests, protects the newest filename-date acceptance bundle,
and keeps the newest 20 remaining artifacts.  ``--apply`` requires the exact
digest from a reviewed dry-run and removes only that unchanged plan's eligible
paths.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import tempfile
from typing import Iterable
import uuid


ARTIFACT_RELATIVE_ROOT = Path("artifacts/gui_validation")
MANIFEST_NAME = ".retention_manifest.json"
MANIFEST_TEMP_PREFIX = f".{MANIFEST_NAME}."
QUARANTINE_NAME = ".retention_quarantine"
RECEIPTS_NAME = ".retention_receipts"
REFERENCE_PREFIX = "artifacts/gui_validation/"
ACTIVE_ROOTS = ("docs", "src", "scripts", "tests")
ACTIVE_ROOT_FILES = ("AGENTS.md", "README.md", "app.py", "pyproject.toml")
TEXT_SUFFIXES = {
    ".cfg",
    ".cmd",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
EXCLUDED_ACTIVE_PARTS = {
    ".git",
    ".pytest_cache",
    ".tmp",
    ".venv",
    "__pycache__",
}
DATE_TOKEN = re.compile(r"(?<!\d)(20\d{6})(?!\d)")
REFERENCE = re.compile(r"artifacts/gui_validation/[^\s`'\"<>\[\]{}(),;]+")


class RetentionSafetyError(RuntimeError):
    """The artifact tree cannot be cleaned without crossing a safety boundary."""


@dataclass(frozen=True)
class Artifact:
    relative_path: str
    path: Path
    size_bytes: int
    mtime_ns: int
    sha256: str
    device: int
    inode: int

    def manifest_entry(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "sha256": self.sha256,
            "device": self.device,
            "inode": self.inode,
        }


@dataclass(frozen=True)
class RetentionPlan:
    artifacts: tuple[Artifact, ...]
    referenced: tuple[Artifact, ...]
    missing_references: tuple[str, ...]
    malformed_reference_codes: tuple[str, ...]
    current_bundle: tuple[Artifact, ...]
    current_bundle_id: str
    kept_recent: tuple[Artifact, ...]
    eligible: tuple[Artifact, ...]
    keep_unreferenced: int


def _has_reparse_attribute(value: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(value, "st_file_attributes", 0) & flag)


def _is_link_or_reparse(path: Path) -> bool:
    return path.is_symlink() or _has_reparse_attribute(path.lstat())


def _assert_contained(root: Path, path: Path) -> None:
    try:
        path.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise RetentionSafetyError(
            f"artifact path is missing or escapes the managed root: {path.name}"
        ) from exc


def _walk_regular_files(root: Path) -> Iterable[Path]:
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda item: item.name.casefold())
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink() or _has_reparse_attribute(entry.stat(follow_symlinks=False)):
                raise RetentionSafetyError(
                    f"link or reparse point is not allowed in the managed tree: {entry.name}"
                )
            if directory == root and entry.name == RECEIPTS_NAME:
                if not entry.is_dir(follow_symlinks=False):
                    raise RetentionSafetyError("retention receipts root must be a directory")
                continue
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(project_root: Path) -> tuple[Path, tuple[Artifact, ...]]:
    resolved_project = project_root.resolve(strict=True)
    artifact_root = resolved_project / ARTIFACT_RELATIVE_ROOT
    if not artifact_root.is_dir() or _is_link_or_reparse(artifact_root):
        raise RetentionSafetyError("artifacts/gui_validation must be a real directory")
    resolved_artifact_root = artifact_root.resolve(strict=True)
    resolved_artifact_root.relative_to(resolved_project)
    if (resolved_artifact_root / QUARANTINE_NAME).exists():
        raise RetentionSafetyError(
            "an unfinished retention quarantine requires recovery before a new plan"
        )

    artifacts: list[Artifact] = []
    for path in _walk_regular_files(resolved_artifact_root):
        relative = path.relative_to(resolved_artifact_root).as_posix()
        if relative == MANIFEST_NAME or relative.startswith(MANIFEST_TEMP_PREFIX):
            continue
        _assert_contained(resolved_artifact_root, path)
        metadata_before = path.stat()
        if not stat.S_ISREG(metadata_before.st_mode):
            raise RetentionSafetyError(f"non-regular artifact is not allowed: {relative}")
        sha256 = _sha256(path)
        metadata_after = path.stat()
        if (
            metadata_before.st_size != metadata_after.st_size
            or metadata_before.st_mtime_ns != metadata_after.st_mtime_ns
        ):
            raise RetentionSafetyError(f"artifact changed during planning: {relative}")
        artifacts.append(
            Artifact(
                relative_path=relative,
                path=path,
                size_bytes=metadata_after.st_size,
                mtime_ns=metadata_after.st_mtime_ns,
                sha256=sha256,
                device=metadata_after.st_dev,
                inode=metadata_after.st_ino,
            )
        )
    artifacts.sort(key=lambda item: (item.relative_path.casefold(), item.relative_path))
    return resolved_artifact_root, tuple(artifacts)


def _iter_active_text_files(project_root: Path) -> Iterable[Path]:
    for name in ACTIVE_ROOT_FILES:
        path = project_root / name
        if not path.exists():
            continue
        if _is_link_or_reparse(path):
            raise RetentionSafetyError(f"active reference file is a link: {name}")
        if path.is_file():
            yield path

    for root_name in ACTIVE_ROOTS:
        root = project_root / root_name
        if not root.exists():
            continue
        if not root.is_dir() or _is_link_or_reparse(root):
            raise RetentionSafetyError(f"active reference root is unsafe: {root_name}")
        pending = [root]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name.casefold())
            for entry in entries:
                path = Path(entry.path)
                relative_parts = path.relative_to(project_root).parts
                if any(part in EXCLUDED_ACTIVE_PARTS for part in relative_parts):
                    continue
                if relative_parts[:2] == ("docs", "archive"):
                    continue
                if entry.is_symlink() or _has_reparse_attribute(entry.stat(follow_symlinks=False)):
                    raise RetentionSafetyError(
                        f"active reference tree contains a link: {path.name}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False) and path.suffix.casefold() in TEXT_SUFFIXES:
                    yield path


def _canonical_reference_or_code(relative: str) -> tuple[str | None, str | None]:
    candidate = relative.rstrip(".,;:)")
    if candidate.endswith(("/n", "/r", "/t")):
        candidate = candidate[:-2]
    candidate = candidate.split("#", 1)[0].split("?", 1)[0]
    if not candidate:
        return None, "EMPTY"
    if candidate.startswith("/"):
        return None, "ROOTED"
    if re.match(r"^[A-Za-z]:/", candidate):
        return None, "DRIVE_ABSOLUTE"
    if re.match(r"^[A-Za-z]:", candidate):
        return None, "DRIVE_RELATIVE"
    if "%2e" in candidate.casefold() or "%2f" in candidate.casefold():
        return None, "ENCODED_PATH"
    pure = PurePosixPath(candidate)
    if any(part in {"", ".", ".."} for part in pure.parts):
        return None, "TRAVERSAL_OR_NONCANONICAL"
    canonical = pure.as_posix()
    if canonical != candidate or "\\" in canonical:
        return None, "NONCANONICAL"
    return canonical, None


def _collect_active_reference_evidence(
    project_root: Path,
    *,
    known_artifact_paths: Iterable[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    references: set[str] = set()
    malformed_codes: set[str] = set()
    known = tuple(known_artifact_paths)
    for path in _iter_active_text_files(project_root.resolve(strict=True)):
        text = path.read_bytes().decode("utf-8", errors="replace")
        normalized = text.replace("\\", "/")
        normalized_casefold = normalized.casefold()
        for artifact_path in known:
            if f"{REFERENCE_PREFIX}{artifact_path}".casefold() in normalized_casefold:
                references.add(artifact_path)
        for match in REFERENCE.findall(normalized):
            relative = match[len(REFERENCE_PREFIX) :].rstrip(".,;:)")
            canonical, malformed = _canonical_reference_or_code(relative)
            if malformed is not None:
                malformed_codes.add(malformed)
            elif canonical is not None:
                references.add(canonical)
    return (
        tuple(sorted(references, key=lambda value: (value.casefold(), value))),
        tuple(sorted(malformed_codes)),
    )


def collect_active_references(
    project_root: Path,
    *,
    known_artifact_paths: Iterable[str] = (),
) -> tuple[str, ...]:
    references, _ = _collect_active_reference_evidence(
        project_root, known_artifact_paths=known_artifact_paths
    )
    return references


def _select_current_bundle(artifacts: tuple[Artifact, ...]) -> tuple[str, tuple[Artifact, ...]]:
    if not artifacts:
        return "EMPTY", ()
    dated: list[tuple[str, Artifact]] = []
    for artifact in artifacts:
        valid_dates: list[str] = []
        for match in DATE_TOKEN.finditer(artifact.relative_path):
            value = match.group(1)
            try:
                datetime.strptime(value, "%Y%m%d")
            except ValueError:
                continue
            valid_dates.append(value)
        if valid_dates:
            dated.append((max(valid_dates), artifact))
    if dated:
        newest_date = max(date for date, _ in dated)
        selected = tuple(artifact for date, artifact in dated if date == newest_date)
        return f"FILENAME_DATE:{newest_date}", selected

    newest_day = max(
        datetime.fromtimestamp(artifact.mtime_ns / 1_000_000_000, timezone.utc).date()
        for artifact in artifacts
    )
    selected = tuple(
        artifact
        for artifact in artifacts
        if datetime.fromtimestamp(
            artifact.mtime_ns / 1_000_000_000, timezone.utc
        ).date()
        == newest_day
    )
    return f"MTIME_UTC_DATE:{newest_day.isoformat()}", selected


def build_retention_plan(project_root: Path, *, keep_unreferenced: int = 20) -> RetentionPlan:
    if keep_unreferenced < 0:
        raise ValueError("keep_unreferenced must be non-negative")
    _, artifacts = _inventory(project_root)
    references, malformed_reference_codes = _collect_active_reference_evidence(
        project_root,
        known_artifact_paths=(artifact.relative_path for artifact in artifacts),
    )
    by_casefold: dict[str, Artifact] = {}
    for artifact in artifacts:
        key = artifact.relative_path.casefold()
        if key in by_casefold:
            raise RetentionSafetyError(
                "case-insensitive artifact path collision prevents safe reference matching"
            )
        by_casefold[key] = artifact
    selected_references = {
        by_casefold[path.casefold()].relative_path: by_casefold[path.casefold()]
        for path in references
        if path.casefold() in by_casefold
    }
    referenced = tuple(
        selected_references[path]
        for path in sorted(
            selected_references, key=lambda value: (value.casefold(), value)
        )
    )
    missing = tuple(path for path in references if path.casefold() not in by_casefold)
    current_bundle_id, current_bundle = _select_current_bundle(artifacts)

    protected = {artifact.relative_path for artifact in referenced}
    protected.update(artifact.relative_path for artifact in current_bundle)
    remaining = [artifact for artifact in artifacts if artifact.relative_path not in protected]
    remaining.sort(
        key=lambda item: (item.mtime_ns, item.relative_path.casefold(), item.relative_path),
        reverse=True,
    )
    kept_recent = tuple(remaining[:keep_unreferenced])
    eligible = tuple(remaining[keep_unreferenced:])
    return RetentionPlan(
        artifacts=artifacts,
        referenced=referenced,
        missing_references=missing,
        malformed_reference_codes=malformed_reference_codes,
        current_bundle=current_bundle,
        current_bundle_id=current_bundle_id,
        kept_recent=kept_recent,
        eligible=eligible,
        keep_unreferenced=keep_unreferenced,
    )


def _plan_digest_payload(plan: RetentionPlan) -> dict[str, object]:
    return {
        "schema_version": 1,
        "keep_unreferenced": plan.keep_unreferenced,
        "current_bundle_id": plan.current_bundle_id,
        "artifacts": [item.manifest_entry() for item in plan.artifacts],
        "referenced": [item.relative_path for item in plan.referenced],
        "missing_references": list(plan.missing_references),
        "malformed_reference_codes": list(plan.malformed_reference_codes),
        "current_bundle": [item.relative_path for item in plan.current_bundle],
        "kept_recent": [item.relative_path for item in plan.kept_recent],
        "eligible": [item.relative_path for item in plan.eligible],
    }


def retention_plan_digest(plan: RetentionPlan) -> str:
    encoded = json.dumps(
        _plan_digest_payload(plan),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_payload(
    plan: RetentionPlan,
    *,
    status: str,
    deleted: tuple[Artifact, ...] = (),
    quarantined: tuple[Artifact, ...] = (),
    restored: tuple[Artifact, ...] = (),
    original_path_states: tuple[dict[str, str], ...] = (),
    transaction_id: str | None = None,
    reviewed_plan_digest: str | None = None,
) -> dict[str, object]:
    referenced_paths = {item.relative_path for item in plan.referenced}
    current_only = tuple(
        item for item in plan.current_bundle if item.relative_path not in referenced_paths
    )
    return {
        "schema_version": 1,
        "classification": "GENERATED_GUI_VALIDATION_RETENTION_MANIFEST_NOT_STATUS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "managed_root": ARTIFACT_RELATIVE_ROOT.as_posix(),
        "status": status,
        "plan_digest_sha256": retention_plan_digest(plan),
        "reviewed_plan_digest_sha256": reviewed_plan_digest,
        "policy": {
            "default_mode": "DRY_RUN",
            "keep_unreferenced": plan.keep_unreferenced,
            "current_bundle_id": plan.current_bundle_id,
        },
        "counts": {
            "inventory_files": len(plan.artifacts),
            "referenced": len(plan.referenced),
            "missing_references": len(plan.missing_references),
            "malformed_references": len(plan.malformed_reference_codes),
            "current_bundle": len(plan.current_bundle),
            "current_bundle_only": len(current_only),
            "kept_recent": len(plan.kept_recent),
            "eligible": len(plan.eligible),
            "quarantined": len(quarantined),
            "restored": len(restored),
            "original_path_conflicts": sum(
                state["state"] == "CONFLICT_PRESENT" for state in original_path_states
            ),
            "deleted": len(deleted),
        },
        "bytes": {
            "inventory": sum(item.size_bytes for item in plan.artifacts),
            "eligible": sum(item.size_bytes for item in plan.eligible),
            "quarantined": sum(item.size_bytes for item in quarantined),
            "deleted": sum(item.size_bytes for item in deleted),
        },
        "referenced": [item.manifest_entry() for item in plan.referenced],
        "missing_references": list(plan.missing_references),
        "malformed_reference_codes": list(plan.malformed_reference_codes),
        "current_bundle": [item.manifest_entry() for item in plan.current_bundle],
        "kept_recent": [item.manifest_entry() for item in plan.kept_recent],
        "eligible": [item.manifest_entry() for item in plan.eligible],
        "transaction": {
            "id": transaction_id,
            "quarantine_root": (
                f"{ARTIFACT_RELATIVE_ROOT.as_posix()}/{QUARANTINE_NAME}/{transaction_id}"
                if transaction_id is not None
                else None
            ),
            "contract": (
                "PURGE_COMMITTED means original paths were atomically quarantined, "
                "validated, and authorized for exact purge; purge may be pending or complete"
            ),
        },
        "quarantined": [item.manifest_entry() for item in quarantined],
        "restored": [item.manifest_entry() for item in restored],
        "original_path_states": list(original_path_states),
        "deleted": [item.manifest_entry() for item in deleted],
    }


def _atomic_write_manifest(artifact_root: Path, payload: dict[str, object]) -> Path:
    manifest = artifact_root / MANIFEST_NAME
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=artifact_root,
            prefix=MANIFEST_TEMP_PREFIX,
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, manifest)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return manifest


def _receipt_path(artifact_root: Path, transaction_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise RetentionSafetyError("retention transaction id is invalid")
    receipts_root = artifact_root / RECEIPTS_NAME
    if receipts_root.exists() and (
        not receipts_root.is_dir() or _is_link_or_reparse(receipts_root)
    ):
        raise RetentionSafetyError("retention receipts root is unsafe")
    receipts_root.mkdir(exist_ok=True)
    transaction_root = receipts_root / transaction_id
    try:
        transaction_root.mkdir()
    except FileExistsError as exc:
        raise RetentionSafetyError("immutable apply receipt already exists") from exc
    return transaction_root / "APPLIED.json"


def _require_new_receipt_path(artifact_root: Path, transaction_id: str) -> Path:
    receipt = _receipt_path(artifact_root, transaction_id)
    return receipt


def _write_immutable_receipt(
    receipt: Path,
    payload: dict[str, object],
    *,
    receipt_kind: str = "IMMUTABLE_GUI_RETENTION_APPLY_RESULT",
) -> None:
    encoded = json.dumps(
        {**payload, "receipt_kind": receipt_kind},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=receipt.parent,
            prefix=f".{receipt.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, receipt)
        except FileExistsError as exc:
            raise RetentionSafetyError("immutable apply receipt collision") from exc
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _require_empty_final_receipt_slot(receipt: Path) -> None:
    if receipt.exists() or receipt.is_symlink():
        raise RetentionSafetyError("immutable apply receipt slot was modified before purge")


def _require_reviewed_dry_run(
    artifact_root: Path,
    plan: RetentionPlan,
    reviewed_plan_digest: str | None,
) -> str:
    if reviewed_plan_digest is None or not re.fullmatch(
        r"[0-9a-f]{64}", reviewed_plan_digest
    ):
        raise RetentionSafetyError(
            "--apply requires the exact lowercase --reviewed-plan-digest from a dry-run manifest"
        )
    manifest_path = artifact_root / MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetentionSafetyError("reviewed dry-run manifest is missing or invalid") from exc
    if not isinstance(payload, dict) or payload.get("status") != "DRY_RUN":
        raise RetentionSafetyError("apply requires the immediately preceding DRY_RUN manifest")
    generated_at = payload.get("generated_at_utc")
    try:
        generated_at_value = datetime.fromisoformat(str(generated_at))
    except ValueError as exc:
        raise RetentionSafetyError("reviewed dry-run manifest timestamp is invalid") from exc
    if generated_at_value.tzinfo is None:
        raise RetentionSafetyError("reviewed dry-run manifest timestamp must be timezone-aware")
    manifest_digest = payload.get("plan_digest_sha256")
    current_digest = retention_plan_digest(plan)
    if manifest_digest != reviewed_plan_digest or current_digest != reviewed_plan_digest:
        raise RetentionSafetyError(
            "reviewed dry-run plan drifted; run and review a new dry-run"
        )
    expected_payload = _manifest_payload(plan, status="DRY_RUN")
    actual_receipt = dict(payload)
    expected_receipt = dict(expected_payload)
    actual_receipt.pop("generated_at_utc", None)
    expected_receipt.pop("generated_at_utc", None)
    if actual_receipt != expected_receipt:
        raise RetentionSafetyError(
            "reviewed dry-run manifest is incomplete, malformed, or does not match the current plan"
        )
    policy = payload.get("policy")
    if not isinstance(policy, dict) or policy.get("keep_unreferenced") != plan.keep_unreferenced:
        raise RetentionSafetyError("reviewed dry-run retention policy drifted")
    if plan.malformed_reference_codes:
        raise RetentionSafetyError(
            "malformed active GUI validation references must be corrected before apply: "
            + ", ".join(plan.malformed_reference_codes)
        )
    return reviewed_plan_digest


def _revalidate_artifact_at(
    artifact_root: Path,
    path: Path,
    artifact: Artifact,
    *,
    require_identity: bool,
) -> None:
    _assert_contained(artifact_root, path)
    if _is_link_or_reparse(path):
        raise RetentionSafetyError(f"eligible artifact became a link: {artifact.relative_path}")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise RetentionSafetyError(f"eligible artifact is no longer regular: {artifact.relative_path}")
    if metadata.st_size != artifact.size_bytes or metadata.st_mtime_ns != artifact.mtime_ns:
        raise RetentionSafetyError(f"eligible artifact changed after planning: {artifact.relative_path}")
    if require_identity and artifact.inode and (
        metadata.st_dev != artifact.device or metadata.st_ino != artifact.inode
    ):
        raise RetentionSafetyError(
            f"eligible artifact identity changed after planning: {artifact.relative_path}"
        )
    if _sha256(path) != artifact.sha256:
        raise RetentionSafetyError(
            f"eligible artifact content changed after planning: {artifact.relative_path}"
        )


def _revalidate_before_delete(artifact_root: Path, artifact: Artifact) -> None:
    _revalidate_artifact_at(
        artifact_root,
        artifact_root / Path(artifact.relative_path),
        artifact,
        require_identity=True,
    )


def _remove_empty_quarantine_dirs(
    quarantine_base: Path,
    quarantine_root: Path,
    moved: Iterable[tuple[Artifact, Path]],
) -> None:
    directories = {path.parent for _, path in moved}
    directories.add(quarantine_root)
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        if directory.exists():
            directory.rmdir()
    if quarantine_base.exists():
        quarantine_base.rmdir()


def _restore_quarantined(
    artifact_root: Path,
    quarantine_base: Path,
    quarantine_root: Path,
    moved: list[tuple[Artifact, Path]],
    restored: list[Artifact],
) -> None:
    for artifact, quarantined_path in reversed(moved):
        original = artifact_root / Path(artifact.relative_path)
        if original.exists() or original.is_symlink():
            raise RetentionSafetyError(
                f"cannot restore quarantine over a new path: {artifact.relative_path}"
            )
        original.parent.mkdir(parents=True, exist_ok=True)
        os.replace(quarantined_path, original)
        restored.append(artifact)
    _remove_empty_quarantine_dirs(quarantine_base, quarantine_root, moved)


def _original_path_states(
    artifact_root: Path,
    moved: Iterable[tuple[Artifact, Path]],
    restored: Iterable[Artifact],
) -> tuple[dict[str, str], ...]:
    restored_paths = {artifact.relative_path for artifact in restored}
    states: list[dict[str, str]] = []
    for artifact, quarantined_path in moved:
        original = artifact_root / Path(artifact.relative_path)
        if artifact.relative_path in restored_paths:
            state = "RESTORED_PLANNED"
        elif quarantined_path.exists() and (original.exists() or original.is_symlink()):
            state = "CONFLICT_PRESENT"
        elif quarantined_path.exists():
            state = "MISSING_QUARANTINED"
        elif original.exists() or original.is_symlink():
            state = "PRESENT_UNCLASSIFIED"
        else:
            state = "MISSING_PURGED"
        states.append({"path": artifact.relative_path, "state": state})
    return tuple(states)


def _live_reference_keys(project_root: Path, plan: RetentionPlan) -> set[str]:
    return {
        path.casefold()
        for path in collect_active_references(
            project_root,
            known_artifact_paths=(artifact.relative_path for artifact in plan.artifacts),
        )
    }


def run_retention(
    project_root: Path,
    *,
    keep_unreferenced: int = 20,
    apply: bool = False,
    reviewed_plan_digest: str | None = None,
) -> dict[str, object]:
    plan = build_retention_plan(project_root, keep_unreferenced=keep_unreferenced)
    artifact_root = (project_root.resolve(strict=True) / ARTIFACT_RELATIVE_ROOT).resolve(strict=True)
    if not apply:
        payload = _manifest_payload(plan, status="DRY_RUN")
        _atomic_write_manifest(artifact_root, payload)
        return payload

    reviewed_digest = _require_reviewed_dry_run(
        artifact_root, plan, reviewed_plan_digest
    )
    transaction_id = uuid.uuid4().hex
    receipt_path = _require_new_receipt_path(artifact_root, transaction_id)
    planned_payload = _manifest_payload(
        plan,
        status="APPLY_PLANNED",
        transaction_id=transaction_id,
        reviewed_plan_digest=reviewed_digest,
    )
    _atomic_write_manifest(artifact_root, planned_payload)
    if not plan.eligible:
        payload = _manifest_payload(
            plan,
            status="APPLIED",
            transaction_id=transaction_id,
            reviewed_plan_digest=reviewed_digest,
        )
        _write_immutable_receipt(receipt_path, payload)
        _atomic_write_manifest(artifact_root, payload)
        return payload

    quarantine_base = artifact_root / QUARANTINE_NAME
    quarantine_root = quarantine_base / transaction_id
    quarantine_root.mkdir(parents=True, exist_ok=False)
    moved: list[tuple[Artifact, Path]] = []
    restored: list[Artifact] = []
    committed = False
    try:
        live_references = _live_reference_keys(project_root, plan)
        newly_referenced = [
            artifact.relative_path
            for artifact in plan.eligible
            if artifact.relative_path.casefold() in live_references
        ]
        if newly_referenced:
            raise RetentionSafetyError(
                "eligible artifact became actively referenced after planning: "
                + ", ".join(newly_referenced)
            )
        for artifact in plan.eligible:
            if artifact.relative_path.casefold() in _live_reference_keys(project_root, plan):
                raise RetentionSafetyError(
                    "eligible artifact became actively referenced before quarantine: "
                    f"{artifact.relative_path}"
                )
            _revalidate_before_delete(artifact_root, artifact)
            quarantined_path = quarantine_root / Path(artifact.relative_path)
            quarantined_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(artifact.path, quarantined_path)
            moved.append((artifact, quarantined_path))
            _revalidate_artifact_at(
                artifact_root,
                quarantined_path,
                artifact,
                require_identity=True,
            )

        late_references = _live_reference_keys(project_root, plan)
        newly_referenced = [
            artifact.relative_path
            for artifact, _ in moved
            if artifact.relative_path.casefold() in late_references
        ]
        if newly_referenced:
            raise RetentionSafetyError(
                "quarantined artifact became actively referenced before commit: "
                + ", ".join(newly_referenced)
            )
        commit_payload = _manifest_payload(
            plan,
            status="PURGE_COMMITTED",
            quarantined=tuple(artifact for artifact, _ in moved),
            transaction_id=transaction_id,
            reviewed_plan_digest=reviewed_digest,
        )
        _atomic_write_manifest(artifact_root, commit_payload)
        _require_empty_final_receipt_slot(receipt_path)
        _write_immutable_receipt(
            receipt_path.parent / "PURGE_COMMITTED.json",
            commit_payload,
            receipt_kind="IMMUTABLE_GUI_RETENTION_PURGE_COMMIT",
        )
        _require_empty_final_receipt_slot(receipt_path)
        committed = True
    except BaseException:
        if not committed:
            try:
                _restore_quarantined(
                    artifact_root,
                    quarantine_base,
                    quarantine_root,
                    moved,
                    restored,
                )
            except BaseException:
                still_quarantined = tuple(
                    artifact for artifact, path in moved if path.exists()
                )
                _atomic_write_manifest(
                    artifact_root,
                    _manifest_payload(
                        plan,
                        status="APPLY_RECOVERY_REQUIRED",
                        quarantined=still_quarantined,
                        restored=tuple(restored),
                        original_path_states=_original_path_states(
                            artifact_root, moved, restored
                        ),
                        transaction_id=transaction_id,
                        reviewed_plan_digest=reviewed_digest,
                    ),
                )
                raise
            _atomic_write_manifest(
                artifact_root,
                _manifest_payload(
                    plan,
                    status="APPLY_ROLLED_BACK",
                    reviewed_plan_digest=reviewed_digest,
                ),
            )
        raise

    deleted: list[Artifact] = []
    try:
        for artifact, quarantined_path in moved:
            _revalidate_artifact_at(
                artifact_root,
                quarantined_path,
                artifact,
                require_identity=True,
            )
            quarantined_path.unlink()
            deleted.append(artifact)
    except BaseException:
        _atomic_write_manifest(
            artifact_root,
            _manifest_payload(
                plan,
                status="PURGE_PARTIAL_FAILURE",
                deleted=tuple(deleted),
                quarantined=tuple(
                    artifact for artifact, path in moved if path.exists()
                ),
                original_path_states=_original_path_states(
                    artifact_root, moved, restored
                ),
                transaction_id=transaction_id,
                reviewed_plan_digest=reviewed_digest,
            ),
        )
        raise
    _remove_empty_quarantine_dirs(quarantine_base, quarantine_root, moved)
    payload = _manifest_payload(
        plan,
        status="APPLIED",
        deleted=tuple(deleted),
        transaction_id=transaction_id,
        reviewed_plan_digest=reviewed_digest,
    )
    _write_immutable_receipt(receipt_path, payload)
    _atomic_write_manifest(artifact_root, payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--keep-unreferenced", type=int, default=20)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete only an unchanged reviewed dry-run plan's eligible paths",
    )
    parser.add_argument(
        "--reviewed-plan-digest",
        help="exact lowercase plan_digest_sha256 from the reviewed DRY_RUN manifest",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = run_retention(
        args.project_root,
        keep_unreferenced=args.keep_unreferenced,
        apply=args.apply,
        reviewed_plan_digest=args.reviewed_plan_digest,
    )
    print(json.dumps({"status": payload["status"], **payload["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
