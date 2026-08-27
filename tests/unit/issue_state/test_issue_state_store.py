from __future__ import annotations

import json
import hashlib
from pathlib import Path
from threading import Thread

import pytest

from issue_state.model import IssueEvent
import issue_state.store as store_module
from issue_state.store import IssueStateStore, IssueStoreError


def event(identity: str, second: int) -> IssueEvent:
    return IssueEvent(
        source_schema="runtime-diagnostic/v1", source_event_id=identity,
        occurred_at=f"2026-08-26T00:00:{second:02d}Z", stable_code="GUI_LOAD_FAILED",
        domain="GUI", target_kind="COMPONENT", target_id="gui:dashboard",
        outcome="FAILURE", severity="ERROR", retryability="NOT_RETRYABLE",
    )


def test_store_is_canonical_atomic_and_idempotent(tmp_path: Path) -> None:
    store = IssueStateStore(tmp_path)
    first = store.update((event("event-1", 1),))
    body = store.path.read_bytes()
    assert not body.endswith(b"\n")
    assert json.loads(body)["generation"] == 1
    assert store.update((event("event-1", 1),))[0].occurrence_count == 1
    assert store.path.read_bytes() == body
    assert store.read()[0].fingerprint == first[0].fingerprint
    assert not any(path.name.startswith(".issues") for path in store.root.iterdir())


def test_store_serializes_concurrent_writers(tmp_path: Path) -> None:
    errors: list[Exception] = []
    def write(identity: str, second: int) -> None:
        try:
            IssueStateStore(tmp_path, lock_timeout=5).update((event(identity, second),))
        except Exception as error:  # pragma: no cover - surfaced below
            errors.append(error)
    threads = [Thread(target=write, args=(f"event-{i}", 1)) for i in range(1, 8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [], [repr(error) for error in errors]
    assert IssueStateStore(tmp_path).read()[0].occurrence_count == 7


def test_store_preserves_malformed_transaction_for_review(tmp_path: Path) -> None:
    store = IssueStateStore(tmp_path)
    store.update((event("event-1", 1),))
    store.journal_path.write_text("{}", encoding="utf-8")
    with pytest.raises(IssueStoreError, match="journal"):
        store.update((event("event-2", 2),))
    assert store.path.is_file()
    assert store.journal_path.is_file()


def test_reader_returns_only_journal_bound_prior_generation(tmp_path: Path) -> None:
    store = IssueStateStore(tmp_path)
    store.update((event("event-1", 1),))
    prior = store.path.read_bytes()
    next_body = store._encode(store.read(), 2)
    store.backup_path.write_bytes(prior)
    store.journal_path.write_bytes(store._journal(prior=prior, next_body=next_body, phase="REPLACED"))
    store.path.write_bytes(b"corrupt")
    assert store.read()[0].occurrence_count == 1
    store.journal_path.write_text("{}", encoding="utf-8")
    with pytest.raises(IssueStoreError, match="journal"):
        store.read()


def test_replaced_journal_with_prior_canonical_fails_closed_and_preserves_residue(
    tmp_path: Path,
) -> None:
    store = IssueStateStore(tmp_path)
    store.update((event("event-1", 1),))
    prior = store.path.read_bytes()
    next_body = store._encode(store.read(), 2)
    store.next_path.write_bytes(next_body)
    store.backup_path.write_bytes(prior)
    journal = store._journal(prior=prior, next_body=next_body, phase="REPLACED")
    store.journal_path.write_bytes(journal)

    with pytest.raises(IssueStoreError, match="phase contradicts"):
        store.read()

    assert store.path.read_bytes() == prior
    assert store.next_path.read_bytes() == next_body
    assert store.backup_path.read_bytes() == prior
    assert store.journal_path.read_bytes() == journal


def test_journal_rejects_boolean_byte_counts_without_mutation(tmp_path: Path) -> None:
    store = IssueStateStore(tmp_path)
    store.update((event("event-1", 1),))
    body = store.path.read_bytes()
    journal = {
        "next_bytes": True, "next_sha256": hashlib.sha256(body).hexdigest(),
        "phase": "PREPARED", "prior_bytes": len(body),
        "prior_sha256": hashlib.sha256(body).hexdigest(),
    }
    store.journal_path.write_text(json.dumps(journal, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(IssueStoreError, match="journal"):
        store.update((event("event-2", 2),))
    assert store.path.read_bytes() == body


def test_record_and_byte_retention_limits_fail_before_replacing_prior(
    tmp_path: Path, monkeypatch,
) -> None:
    store = IssueStateStore(tmp_path)
    store.update((event("event-1", 1),))
    prior = store.path.read_bytes()

    monkeypatch.setattr(store_module, "MAX_BYTES", len(prior) + 10)
    with pytest.raises(IssueStoreError, match="byte limit"):
        store.update((event("event-2", 2),))
    assert store.path.read_bytes() == prior
    assert not any(path.name.startswith(".issues") for path in store.root.iterdir())

    other = IssueStateStore(tmp_path / "other")
    monkeypatch.setattr(store_module, "MAX_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(store_module, "MAX_RECORDS", 0)
    with pytest.raises(IssueStoreError, match="record limit"):
        other.update((event("event-3", 3),))
    assert not other.path.exists()
