from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

import pyarrow.parquet as pq


MANIFEST_VERSION = "stock-data-local-backup/v1"
INCLUDED_CLASSES = frozenset({"critical", "immutable"})
SKIPPED_CLASSES = frozenset({"reproducible", "sensitive", "excluded"})
ALL_CLASSES = INCLUDED_CLASSES | SKIPPED_CLASSES
_SECRET_MARKERS = frozenset(
    {
        ".env",
        "credential",
        "credentials",
        "secret",
        "secrets",
        "token",
        "tokens",
        "private_key",
        "account_snapshot",
        "account_snapshots",
    }
)
_PROJECT_DATA_ROOT = Path(__file__).resolve().parents[3] / "data"


class BackupIntegrityError(RuntimeError):
    """Raised when a backup or restore cannot be proven complete and exact."""


@dataclass(frozen=True)
class BackupItem:
    relative_path: str
    classification: str
    validator: str = "binary"
    row_identity_keys: tuple[str, ...] = ()
    required_json_keys: tuple[str, ...] = ()
    include: bool = True
    reason: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BackupItem":
        return cls(
            relative_path=str(value["relative_path"]),
            classification=str(value["classification"]),
            validator=str(value.get("validator", "binary")),
            row_identity_keys=tuple(str(item) for item in value.get("row_identity_keys", ())),
            required_json_keys=tuple(str(item) for item in value.get("required_json_keys", ())),
            include=bool(value.get("include", True)),
            reason=str(value.get("reason", "")),
        )


@dataclass(frozen=True)
class BackupResult:
    manifest_sha256: str
    version_root: Path
    file_count: int
    total_bytes: int
    reused_existing: bool


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupIntegrityError(f"unsafe relative path: {value!r}")
    return path


def _looks_sensitive(path: PurePosixPath) -> bool:
    for raw_part in path.parts:
        part = raw_part.casefold()
        if part.startswith(".env"):
            return True
        stem = Path(part).stem
        if any(marker in part or marker in stem for marker in _SECRET_MARKERS):
            return True
    return False


def _resolve_source_file(source_root: Path, relative_path: PurePosixPath) -> Path:
    candidate = source_root.joinpath(*relative_path.parts)
    cursor = source_root
    for part in relative_path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BackupIntegrityError(f"symlinks are not accepted: {relative_path.as_posix()}")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BackupIntegrityError(f"required source file is missing: {relative_path.as_posix()}") from exc
    if source_root not in resolved.parents:
        raise BackupIntegrityError(f"source path escapes root: {relative_path.as_posix()}")
    if not resolved.is_file():
        raise BackupIntegrityError(f"only explicit files are accepted: {relative_path.as_posix()}")
    return resolved


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "__NaN__"
        if math.isinf(value):
            return "__Infinity__" if value > 0 else "__-Infinity__"
        return value
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _parquet_validation(path: Path, row_identity_keys: Sequence[str]) -> dict[str, object]:
    parquet_file = pq.ParquetFile(path)
    arrow_schema = parquet_file.schema_arrow
    schema = [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in arrow_schema
    ]
    result: dict[str, object] = {
        "kind": "parquet",
        "rows": parquet_file.metadata.num_rows,
        "schema": schema,
    }
    if row_identity_keys:
        missing = [key for key in row_identity_keys if key not in arrow_schema.names]
        if missing:
            raise BackupIntegrityError(f"parquet row identity columns missing: {missing}")
        rows = [_jsonable(row) for row in pq.read_table(path, columns=list(row_identity_keys)).to_pylist()]
        encoded = [_canonical_json(row) for row in rows]
        if len(set(encoded)) != len(encoded):
            raise BackupIntegrityError("parquet row identity is not unique")
        result["row_identity_keys"] = list(row_identity_keys)
        result["row_identity_sha256"] = _sha256_bytes(b"".join(sorted(encoded)))
    return result


def _validation_metadata(path: Path, item: BackupItem) -> dict[str, object]:
    if item.validator == "binary":
        return {"kind": "binary"}
    if item.validator == "text":
        path.read_text(encoding="utf-8")
        return {"kind": "text", "encoding": "utf-8"}
    if item.validator == "json":
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise BackupIntegrityError(f"required JSON object not found: {item.relative_path}")
        missing = [key for key in item.required_json_keys if key not in value]
        if missing:
            raise BackupIntegrityError(f"required JSON keys missing in {item.relative_path}: {missing}")
        return {"kind": "json", "required_keys": sorted(item.required_json_keys)}
    if item.validator == "parquet":
        return _parquet_validation(path, item.row_identity_keys)
    raise BackupIntegrityError(f"unsupported validator: {item.validator}")


def _entry(source: Path, item: BackupItem) -> dict[str, object]:
    stat = source.stat()
    return {
        "relative_path": _safe_relative_path(item.relative_path).as_posix(),
        "classification": item.classification,
        "size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256(source),
        "validation": _validation_metadata(source, item),
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_latest(backup_root: Path, manifest_sha256: str) -> None:
    _atomic_write(
        backup_root / "LATEST.json",
        _canonical_json({"manifest_sha256": manifest_sha256, "version": MANIFEST_VERSION}),
    )


def _read_manifest(version_root: Path) -> tuple[dict[str, Any], str]:
    manifest_path = version_root / "manifest.json"
    if not manifest_path.is_file():
        raise BackupIntegrityError("backup manifest is missing")
    payload = manifest_path.read_bytes()
    digest = _sha256_bytes(payload)
    declared_path = version_root / "manifest.sha256"
    if not declared_path.is_file() or declared_path.read_text(encoding="ascii").strip() != digest:
        raise BackupIntegrityError("backup manifest digest does not match")
    if version_root.name != digest:
        raise BackupIntegrityError("backup version directory does not match manifest digest")
    value = json.loads(payload)
    if value.get("format") != MANIFEST_VERSION:
        raise BackupIntegrityError("unsupported backup manifest version")
    return value, digest


def _item_from_entry(entry: Mapping[str, Any]) -> BackupItem:
    validation = entry.get("validation") or {}
    return BackupItem(
        relative_path=str(entry["relative_path"]),
        classification=str(entry["classification"]),
        validator=str(validation.get("kind", "binary")),
        row_identity_keys=tuple(str(value) for value in validation.get("row_identity_keys", ())),
        required_json_keys=tuple(str(value) for value in validation.get("required_keys", ())),
    )


def _verify_version(version_root: Path) -> dict[str, Any]:
    manifest, _ = _read_manifest(version_root)
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise BackupIntegrityError("backup manifest has no files")
    expected_paths: set[str] = set()
    observed_total = 0
    for entry in entries:
        relative = _safe_relative_path(str(entry["relative_path"]))
        logical = relative.as_posix()
        if logical in expected_paths:
            raise BackupIntegrityError(f"duplicate backup entry: {logical}")
        expected_paths.add(logical)
        payload = version_root / "payload" / Path(*relative.parts)
        if not payload.is_file() or payload.is_symlink():
            raise BackupIntegrityError(f"backup payload missing: {logical}")
        observed_total += payload.stat().st_size
        if payload.stat().st_size != entry.get("size") or _sha256(payload) != entry.get("sha256"):
            raise BackupIntegrityError(f"backup payload integrity mismatch: {logical}")
        actual_validation = _validation_metadata(payload, _item_from_entry(entry))
        if actual_validation != entry.get("validation"):
            raise BackupIntegrityError(f"backup semantic validation mismatch: {logical}")
    actual_payloads = {
        path.relative_to(version_root / "payload").as_posix()
        for path in (version_root / "payload").rglob("*")
        if path.is_file()
    }
    if actual_payloads != expected_paths:
        raise BackupIntegrityError("backup payload inventory is incomplete or contains undeclared files")
    totals = manifest.get("totals") or {}
    if totals.get("files") != len(entries) or totals.get("bytes") != observed_total:
        raise BackupIntegrityError("backup totals do not match payload")
    return manifest


def verify_backup(backup_root: Path, manifest_sha256: str | None = None) -> dict[str, Any]:
    backup_root = backup_root.resolve()
    if manifest_sha256 is None:
        latest_path = backup_root / "LATEST.json"
        if not latest_path.is_file():
            raise BackupIntegrityError("LATEST pointer is missing")
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        manifest_sha256 = str(latest.get("manifest_sha256", ""))
    if len(manifest_sha256) != 64 or any(character not in "0123456789abcdef" for character in manifest_sha256):
        raise BackupIntegrityError("invalid manifest digest")
    return _verify_version(backup_root / "versions" / manifest_sha256)


def create_backup(
    *,
    source_root: Path,
    backup_root: Path,
    items: Iterable[BackupItem],
    source_label: str = "stock_investment_rev1",
    max_files: int = 500,
    max_bytes: int = 2 * 1024 * 1024 * 1024,
) -> BackupResult:
    source_root = source_root.resolve(strict=True)
    backup_root = backup_root.resolve()
    if source_root == backup_root or source_root in backup_root.parents:
        raise BackupIntegrityError("backup root must be outside the source root")
    if max_files <= 0 or max_bytes <= 0:
        raise BackupIntegrityError("backup limits must be positive")

    included: list[tuple[BackupItem, Path, dict[str, object]]] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    total_bytes = 0
    for item in items:
        relative = _safe_relative_path(item.relative_path)
        logical = relative.as_posix()
        if logical in seen:
            raise BackupIntegrityError(f"duplicate plan path: {logical}")
        seen.add(logical)
        if item.classification not in ALL_CLASSES:
            raise BackupIntegrityError(f"unknown classification: {item.classification}")
        if item.include:
            if item.classification not in INCLUDED_CLASSES:
                raise BackupIntegrityError(f"classification cannot be copied: {item.classification}")
            if _looks_sensitive(relative):
                raise BackupIntegrityError(f"sensitive path cannot be copied: {logical}")
            source = _resolve_source_file(source_root, relative)
            metadata = _entry(source, item)
            total_bytes += int(metadata["size"])
            included.append((item, source, metadata))
        else:
            if item.classification not in SKIPPED_CLASSES:
                raise BackupIntegrityError(f"critical/immutable item cannot be silently skipped: {logical}")
            if not item.reason.strip():
                raise BackupIntegrityError(f"excluded plan item requires a reason: {logical}")
            excluded.append(
                {"relative_path": logical, "classification": item.classification, "reason": item.reason}
            )
    if not included:
        raise BackupIntegrityError("backup plan contains no included files")
    if len(included) > max_files or total_bytes > max_bytes:
        raise BackupIntegrityError("backup plan exceeds its explicit file or byte budget")

    entries = [metadata for _, _, metadata in sorted(included, key=lambda value: value[2]["relative_path"])]
    manifest = {
        "format": MANIFEST_VERSION,
        "source_label": source_label,
        "entries": entries,
        "excluded": sorted(excluded, key=lambda value: value["relative_path"]),
        "totals": {"files": len(entries), "bytes": total_bytes},
    }
    manifest_bytes = _canonical_json(manifest)
    manifest_sha256 = _sha256_bytes(manifest_bytes)
    backup_root.mkdir(parents=True, exist_ok=True)
    versions_root = backup_root / "versions"
    versions_root.mkdir(exist_ok=True)
    version_root = versions_root / manifest_sha256
    if version_root.exists():
        _verify_version(version_root)
        _publish_latest(backup_root, manifest_sha256)
        return BackupResult(manifest_sha256, version_root, len(entries), total_bytes, True)

    stage = Path(tempfile.mkdtemp(prefix=".backup-stage-", dir=backup_root))
    try:
        for _, source, metadata in included:
            relative = PurePosixPath(str(metadata["relative_path"]))
            target = stage / "payload" / Path(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        (stage / "manifest.json").write_bytes(manifest_bytes)
        (stage / "manifest.sha256").write_text(manifest_sha256 + "\n", encoding="ascii")
        stage.rename(version_root)
        _verify_version(version_root)
        _publish_latest(backup_root, manifest_sha256)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return BackupResult(manifest_sha256, version_root, len(entries), total_bytes, False)


def restore_verified_to_staging(
    *,
    backup_root: Path,
    staging_destination: Path,
    manifest_sha256: str | None = None,
) -> Path:
    backup_root = backup_root.resolve()
    staging_destination = staging_destination.resolve()
    project_data_root = _PROJECT_DATA_ROOT.resolve()
    if staging_destination == project_data_root or project_data_root in staging_destination.parents:
        raise BackupIntegrityError("restore into the production data root is not authorized")
    if staging_destination == backup_root or backup_root in staging_destination.parents:
        raise BackupIntegrityError("restore staging must be outside the immutable backup root")
    if staging_destination.exists():
        raise BackupIntegrityError("restore staging destination already exists; overwrite is forbidden")
    manifest = verify_backup(backup_root, manifest_sha256)
    if manifest_sha256 is None:
        latest = json.loads((backup_root / "LATEST.json").read_text(encoding="utf-8"))
        manifest_sha256 = str(latest["manifest_sha256"])
    version_root = backup_root / "versions" / manifest_sha256
    staging_destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=staging_destination.parent))
    try:
        for entry in manifest["entries"]:
            relative = PurePosixPath(str(entry["relative_path"]))
            source = version_root / "payload" / Path(*relative.parts)
            target = temporary / Path(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if _sha256(target) != entry["sha256"]:
                raise BackupIntegrityError(f"restored payload integrity mismatch: {relative.as_posix()}")
            if _validation_metadata(target, _item_from_entry(entry)) != entry["validation"]:
                raise BackupIntegrityError(f"restored semantic validation mismatch: {relative.as_posix()}")
        marker = {
            "format": MANIFEST_VERSION,
            "manifest_sha256": manifest_sha256,
            "production_promotion_authorized": False,
            "verified_files": len(manifest["entries"]),
        }
        (temporary / "RESTORE_VERIFIED.json").write_bytes(_canonical_json(marker))
        temporary.rename(staging_destination)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return staging_destination


def load_plan(path: Path) -> list[BackupItem]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise BackupIntegrityError("backup plan must be an object with an items array")
    return [BackupItem.from_mapping(item) for item in value["items"]]


__all__ = [
    "ALL_CLASSES",
    "BackupIntegrityError",
    "BackupItem",
    "BackupResult",
    "create_backup",
    "load_plan",
    "restore_verified_to_staging",
    "verify_backup",
]
