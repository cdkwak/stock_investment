from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Iterator

from .model import IssueEvent, IssueRecord, aggregate_events, canonical_json


STORE_SCHEMA = "issue-store/v1"
MAX_RECORDS = 10_000
MAX_BYTES = 8 * 1024 * 1024


class IssueStoreError(RuntimeError):
    pass


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, body: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _is_reparse(path: Path) -> bool:
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & 0x400)


def _decode_journal(body: bytes) -> dict[str, object]:
    try:
        if not body or body.startswith(b"\xef\xbb\xbf") or body.endswith(b"\n"):
            raise IssueStoreError("issue transaction journal bytes differ")
        journal = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise IssueStoreError("issue transaction journal is invalid") from error
    required = {"next_bytes", "next_sha256", "phase", "prior_bytes", "prior_sha256"}
    if (
        type(journal) is not dict or set(journal) != required
        or body != canonical_json(journal)
        or journal["phase"] not in {"PREPARED", "REPLACED", "VERIFIED"}
        or type(journal["next_bytes"]) is not int
        or not 1 <= journal["next_bytes"] <= MAX_BYTES
        or type(journal["prior_bytes"]) is not int
        or not 0 <= journal["prior_bytes"] <= MAX_BYTES
        or type(journal["next_sha256"]) is not str
        or len(journal["next_sha256"]) != 64
        or any(ch not in "0123456789abcdef" for ch in journal["next_sha256"])
        or (
            journal["prior_sha256"] is not None
            and (
                type(journal["prior_sha256"]) is not str
                or len(journal["prior_sha256"]) != 64
                or any(ch not in "0123456789abcdef" for ch in journal["prior_sha256"])
            )
        )
        or (journal["prior_sha256"] is None) != (journal["prior_bytes"] == 0)
    ):
        raise IssueStoreError("issue transaction journal differs")
    return journal


class IssueStateStore:
    def __init__(self, project_root: Path, *, lock_timeout: float = 2.0) -> None:
        supplied_root = Path(project_root).absolute()
        current = supplied_root
        while True:
            if current.exists() and _is_reparse(current):
                raise ValueError("project root uses redirection")
            if current.parent == current:
                break
            current = current.parent
        self.project_root = supplied_root.resolve()
        self.path = self.project_root / "artifacts/issue_state/v1/issues.json"
        if lock_timeout <= 0 or lock_timeout > 30:
            raise ValueError("lock timeout differs")
        self.root = self.path.parent
        self.lock_path = self.root / ".write.lock"
        self.journal_path = self.root / ".issues.transaction.json"
        self.next_path = self.root / ".issues.next"
        self.backup_path = self.root / ".issues.backup"
        self.lock_timeout = lock_timeout

    def _validate_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        current = self.root
        while True:
            if _is_reparse(current):
                raise IssueStoreError("issue store path uses redirection")
            if current.parent == current:
                break
            current = current.parent
        for path in (
            self.path, self.lock_path, self.journal_path,
            self.next_path, self.backup_path,
        ):
            if _is_reparse(path):
                raise IssueStoreError("issue store control path uses redirection")
        allowed = {
            "issues.json", ".write.lock", ".issues.transaction.json",
            ".issues.next", ".issues.backup",
        }
        if any(path.name not in allowed for path in self.root.iterdir()):
            raise IssueStoreError("issue store contains an unrecognized adjacent path")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self._validate_root()
        token = secrets.token_hex(32)
        body = canonical_json({"pid": os.getpid(), "token": token})
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                _write_exclusive(self.lock_path, body)
                break
            except (FileExistsError, PermissionError):
                self._remove_proven_stale_lock()
                if time.monotonic() >= deadline:
                    raise IssueStoreError("issue store lock timeout") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            self._release_owned_lock(body)

    def _release_owned_lock(self, body: bytes) -> None:
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                retained = self.lock_path.read_bytes()
                if retained != body:
                    raise IssueStoreError("issue store lock ownership differs")
                self.lock_path.unlink()
                return
            except FileNotFoundError:
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise IssueStoreError("owned issue lock could not be released") from None
                time.sleep(0.02)

    def _remove_proven_stale_lock(self) -> None:
        try:
            first = self.lock_path.read_bytes()
            payload = json.loads(first)
            if (
                type(payload) is not dict or set(payload) != {"pid", "token"}
                or first != canonical_json(payload)
                or type(payload.get("pid")) is not int or payload["pid"] <= 0
                or type(payload.get("token")) is not str or len(payload["token"]) != 64
                or any(ch not in "0123456789abcdef" for ch in payload["token"])
            ):
                return
            pid = payload["pid"]
            try:
                os.kill(pid, 0)
                return
            except PermissionError:
                return
            except OSError:
                pass
            if self.lock_path.read_bytes() == first:
                self.lock_path.unlink()
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return

    @staticmethod
    def _encode(records: tuple[IssueRecord, ...], generation: int) -> bytes:
        if len(records) > MAX_RECORDS:
            raise IssueStoreError("issue record limit reached")
        content = {
            "generation": generation, "records": [record.to_dict() for record in records],
            "schema": STORE_SCHEMA,
        }
        content_digest = _digest(canonical_json(content))
        body = canonical_json({**content, "content_sha256": content_digest})
        if len(body) > MAX_BYTES:
            raise IssueStoreError("issue store byte limit reached")
        return body

    @staticmethod
    def _decode(body: bytes) -> tuple[int, tuple[IssueRecord, ...]]:
        if not body or len(body) > MAX_BYTES or body.startswith(b"\xef\xbb\xbf") or body.endswith(b"\n"):
            raise IssueStoreError("issue store bytes differ")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise IssueStoreError("issue store JSON is invalid") from error
        if type(payload) is not dict or set(payload) != {"schema", "generation", "records", "content_sha256"}:
            raise IssueStoreError("issue store envelope differs")
        if payload["schema"] != STORE_SCHEMA or type(payload["generation"]) is not int or payload["generation"] < 1:
            raise IssueStoreError("issue store identity differs")
        if type(payload["records"]) is not list or len(payload["records"]) > MAX_RECORDS:
            raise IssueStoreError("issue store records differ")
        check = {key: payload[key] for key in ("generation", "records", "schema")}
        if payload["content_sha256"] != _digest(canonical_json(check)):
            raise IssueStoreError("issue store content digest differs")
        if body != canonical_json(payload):
            raise IssueStoreError("issue store is not canonical JSON")
        try:
            records = tuple(IssueRecord.from_dict(item) for item in payload["records"])
        except (TypeError, ValueError) as error:
            raise IssueStoreError("issue store record validation failed") from error
        if len({item.fingerprint for item in records}) != len(records):
            raise IssueStoreError("issue store contains duplicate fingerprints")
        return payload["generation"], records

    def read(self) -> tuple[IssueRecord, ...]:
        self._validate_root()
        if not self.path.exists() and not self.journal_path.exists():
            return ()
        if self.journal_path.exists() and self.path.exists():
            journal = _decode_journal(self.journal_path.read_bytes())
            canonical = self.path.read_bytes()
            canonical_is_prior = (
                len(canonical) == journal["prior_bytes"]
                and _digest(canonical) == journal["prior_sha256"]
            )
            if canonical_is_prior and journal["phase"] != "PREPARED":
                raise IssueStoreError(
                    "issue transaction phase contradicts canonical generation"
                )
        try:
            _, records = self._decode(self.path.read_bytes())
            return records
        except (IssueStoreError, ValueError, FileNotFoundError):
            if (
                not self.journal_path.is_file() or not self.backup_path.is_file()
                or _is_reparse(self.journal_path) or _is_reparse(self.backup_path)
            ):
                raise
            journal = _decode_journal(self.journal_path.read_bytes())
            backup = self.backup_path.read_bytes()
            if (
                len(backup) != journal["prior_bytes"]
                or _digest(backup) != journal["prior_sha256"]
            ):
                raise IssueStoreError("prior accepted issue generation differs")
            _, records = self._decode(backup)
            return records

    def update(self, events: tuple[IssueEvent, ...]) -> tuple[IssueRecord, ...]:
        with self._lock():
            self._recover()
            if self.path.exists():
                prior_body = self.path.read_bytes()
                generation, prior = self._decode(prior_body)
            else:
                prior_body = b""
                generation, prior = 0, ()
            updated = aggregate_events(prior, events)
            if [item.to_dict() for item in updated] == [item.to_dict() for item in prior]:
                return updated
            next_body = self._encode(updated, generation + 1)
            self._commit(prior_body, next_body)
            return updated

    def replace_records(self, records: tuple[IssueRecord, ...]) -> tuple[IssueRecord, ...]:
        """Persist a validated lifecycle-only projection without changing identities."""
        normalized = tuple(sorted(
            (IssueRecord.from_dict(record.to_dict()) for record in records),
            key=lambda item: item.fingerprint,
        ))
        with self._lock():
            self._recover()
            if not self.path.exists():
                raise IssueStoreError("issue lifecycle replacement lacks canonical state")
            prior_body = self.path.read_bytes()
            generation, prior = self._decode(prior_body)
            if [item.fingerprint for item in normalized] != [item.fingerprint for item in prior]:
                raise IssueStoreError("issue lifecycle replacement identities differ")
            for before, after in zip(prior, normalized, strict=True):
                if (
                    after.occurrence_count != before.occurrence_count
                    or after.source_event_count != before.source_event_count
                    or after.recovery_count != before.recovery_count
                ):
                    raise IssueStoreError("issue lifecycle replacement changed occurrence truth")
            if [item.to_dict() for item in normalized] == [item.to_dict() for item in prior]:
                return normalized
            self._commit(prior_body, self._encode(normalized, generation + 1))
            return normalized

    def _journal(self, *, prior: bytes, next_body: bytes, phase: str) -> bytes:
        return canonical_json({
            "next_bytes": len(next_body), "next_sha256": _digest(next_body),
            "phase": phase, "prior_bytes": len(prior),
            "prior_sha256": _digest(prior) if prior else None,
        })

    def _replace_journal(self, body: bytes) -> None:
        _write_exclusive(self.next_path, body)
        os.replace(self.next_path, self.journal_path)
        _fsync_directory(self.root)

    def _commit(self, prior: bytes, next_body: bytes) -> None:
        if any(path.exists() for path in (self.next_path, self.backup_path, self.journal_path)):
            raise IssueStoreError("issue transaction residue remains")
        try:
            if prior:
                _write_exclusive(self.backup_path, prior)
            _write_exclusive(self.next_path, next_body)
            _write_exclusive(self.journal_path, self._journal(prior=prior, next_body=next_body, phase="PREPARED"))
            _fsync_directory(self.root)
            os.replace(self.next_path, self.path)
            _fsync_directory(self.root)
            self._replace_journal(self._journal(prior=prior, next_body=next_body, phase="REPLACED"))
            published = self.path.read_bytes()
            self._decode(published)
            if published != next_body:
                raise IssueStoreError("published issue bytes differ")
            self._replace_journal(self._journal(prior=prior, next_body=next_body, phase="VERIFIED"))
            self.backup_path.unlink(missing_ok=True)
            self.journal_path.unlink(missing_ok=True)
            _fsync_directory(self.root)
        except Exception:
            raise

    def _recover(self) -> None:
        self._validate_root()
        residue = [path for path in (self.journal_path, self.next_path, self.backup_path) if path.exists()]
        if not residue:
            return
        if not self.journal_path.exists():
            raise IssueStoreError("issue transaction residue lacks journal")
        try:
            journal = _decode_journal(self.journal_path.read_bytes())
        except OSError as error:
            raise IssueStoreError("issue transaction journal is invalid") from error
        canonical = self.path.read_bytes() if self.path.exists() else b""
        canonical_digest = _digest(canonical) if canonical else None
        if canonical_digest == journal["next_sha256"] and len(canonical) == journal["next_bytes"]:
            self._decode(canonical)
            if journal["prior_bytes"] > 0 and not self.backup_path.exists():
                raise IssueStoreError("prior issue backup is missing")
            if self.backup_path.exists():
                backup = self.backup_path.read_bytes()
                if (
                    len(backup) != journal["prior_bytes"]
                    or _digest(backup) != journal["prior_sha256"]
                ):
                    raise IssueStoreError("prior issue backup differs")
                self._decode(backup)
            self.next_path.unlink(missing_ok=True)
            self.backup_path.unlink(missing_ok=True)
            self.journal_path.unlink(missing_ok=True)
            return
        if canonical_digest == journal["prior_sha256"] and len(canonical) == journal["prior_bytes"]:
            if journal["phase"] != "PREPARED":
                raise IssueStoreError("issue transaction phase contradicts canonical generation")
            if canonical:
                self._decode(canonical)
            if not self.next_path.exists():
                raise IssueStoreError("unpublished next issue bytes are missing")
            unpublished = self.next_path.read_bytes()
            if (
                len(unpublished) != journal["next_bytes"]
                or _digest(unpublished) != journal["next_sha256"]
            ):
                raise IssueStoreError("unpublished next issue bytes differ")
            self._decode(unpublished)
            if journal["prior_bytes"] > 0 and not self.backup_path.exists():
                raise IssueStoreError("prior issue backup is missing")
            if self.backup_path.exists():
                backup = self.backup_path.read_bytes()
                if (
                    len(backup) != journal["prior_bytes"]
                    or _digest(backup) != journal["prior_sha256"]
                    or backup != canonical
                ):
                    raise IssueStoreError("prior issue backup differs")
            self.next_path.unlink(missing_ok=True)
            self.backup_path.unlink(missing_ok=True)
            self.journal_path.unlink(missing_ok=True)
            return
        if self.backup_path.exists():
            backup = self.backup_path.read_bytes()
            if (
                not backup or len(backup) != journal["prior_bytes"]
                or _digest(backup) != journal["prior_sha256"]
            ):
                raise IssueStoreError("prior issue backup differs")
            self._decode(backup)
            os.replace(self.backup_path, self.path)
            if self.path.read_bytes() != backup:
                raise IssueStoreError("restored issue bytes differ")
            self.next_path.unlink(missing_ok=True)
            self.journal_path.unlink(missing_ok=True)
            return
        raise IssueStoreError("issue transaction cannot be recovered safely")
