from datetime import datetime, timezone
import ctypes
import os
import shutil
import subprocess
from ctypes import wintypes

import pytest

from stock_data.pipelines.canonical_equity_incremental import publication_window_passed
from stock_data.pipelines.canonical_equity_incremental import (
    _commit_replacements,
    _copy_windows_security_identity,
    _recover_replacement_transaction,
    _windows_security_information,
)


def _security_identity(path):
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    get_info = advapi32.GetNamedSecurityInfoW
    get_info.argtypes = (
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_info.restype = wintypes.DWORD
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    get_control.restype = wintypes.BOOL
    to_sddl = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
    to_sddl.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD),
    )
    to_sddl.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p

    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    security_information = 0x00000001 | 0x00000004
    assert get_info(
        str(path), 1, security_information, ctypes.byref(owner), None,
        ctypes.byref(dacl), None, ctypes.byref(descriptor),
    ) == 0
    text = wintypes.LPWSTR()
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        assert get_control(
            descriptor, ctypes.byref(control), ctypes.byref(revision),
        )
        assert to_sddl(
            descriptor, 1, security_information, ctypes.byref(text), None,
        )
        return text.value, bool(control.value & 0x1000)
    finally:
        if text:
            kernel32.LocalFree(text)
        kernel32.LocalFree(descriptor)


def _protected_identity(path):
    completed = subprocess.run(
        ["icacls", str(path), "/inheritance:d"],
        check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    identity = _security_identity(path)
    assert identity[1] is True
    return identity


def test_publication_window_is_explicit_and_timezone_aware():
    deadline=datetime(2026,8,14,4,tzinfo=timezone.utc)
    assert publication_window_passed(deadline_kst=deadline,now_kst=datetime(2026,8,14,4,0,1,tzinfo=timezone.utc))
    assert not publication_window_passed(deadline_kst=deadline,now_kst=datetime(2026,8,14,3,59,59,tzinfo=timezone.utc))
    with pytest.raises(ValueError,match="timezone-aware"):
        publication_window_passed(deadline_kst=datetime(2026,8,14,13),now_kst=datetime.now(timezone.utc))


def test_windows_security_information_only_requests_owner_when_it_differs():
    owner = 0x00000001
    dacl = 0x00000004
    protected = 0x80000000
    unprotected = 0x20000000

    assert _windows_security_information(
        owner_matches=True, protected=True,
    ) == dacl | protected
    assert _windows_security_information(
        owner_matches=False, protected=False,
    ) == owner | dacl | unprotected


def test_cross_file_commit_rolls_back_all_targets_on_mid_commit_failure(tmp_path, monkeypatch):
    root = tmp_path
    target_a = root / "data/a.parquet"
    target_b = root / "data/b.parquet"
    staged_a = root / "data/staging/a.parquet"
    staged_b = root / "data/staging/b.parquet"
    for path, body in ((target_a, b"old-a"), (target_b, b"old-b"),
                       (staged_a, b"new-a"), (staged_b, b"new-b")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    import stock_data.pipelines.canonical_equity_incremental as module
    real_replace = module.os.replace

    def fail_second(source, destination):
        if str(destination) == str(target_b) and str(source) == str(staged_b):
            raise OSError("injected replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second)
    with pytest.raises(OSError, match="injected"):
        _commit_replacements(
            root,
            transaction_name="test_atomic",
            replacements=[(target_a, staged_a), (target_b, staged_b)],
        )

    assert target_a.read_bytes() == b"old-a"
    assert target_b.read_bytes() == b"old-b"
    assert not (root / "data/state/test_atomic.transaction.json").exists()


def test_cross_file_commit_prepares_new_target_security_from_parent(tmp_path, monkeypatch):
    root = tmp_path
    target = root / "data/new/target.json"
    staged = root / "data/staging/target.json"
    staged.parent.mkdir(parents=True)
    staged.write_text("new", encoding="utf-8")
    calls = []

    import stock_data.pipelines.canonical_equity_incremental as module
    monkeypatch.setattr(
        module,
        "_copy_windows_security_identity",
        lambda source, destination: calls.append((source, destination)),
    )

    _commit_replacements(
        root,
        transaction_name="test_new_target_acl",
        replacements=[(target, staged)],
    )

    assert target.read_text(encoding="utf-8") == "new"
    assert calls == [(target.parent, staged)]


def test_interrupted_transaction_is_recovered_from_verified_backup(tmp_path):
    import hashlib
    import json

    root = tmp_path
    target = root / "data/target.parquet"
    backup = root / "data/staging/transactions/recover/backups/0.bak"
    journal = root / "data/state/recover.transaction.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    journal.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"partial-new")
    backup.write_bytes(b"known-old")
    journal.write_text(json.dumps({
        "version": 1,
        "status": "COMMITTING",
        "transaction_root": "data/staging/transactions/recover",
        "entries": [{
            "target": "data/target.parquet",
            "staged": "data/staging/new.parquet",
            "backup": "data/staging/transactions/recover/backups/0.bak",
            "before_sha256": hashlib.sha256(b"known-old").hexdigest(),
        }],
    }), encoding="utf-8")

    _recover_replacement_transaction(root, journal)

    assert target.read_bytes() == b"known-old"
    assert not journal.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows security identity boundary")
def test_interrupted_transaction_restores_owner_and_protected_dacl(tmp_path):
    import hashlib
    import json

    root = tmp_path
    target = root / "data/target.parquet"
    backup = root / "data/staging/transactions/recover_acl/backups/0.bak"
    journal = root / "data/state/recover_acl.transaction.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)
    journal.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"known-old")
    expected_identity = _protected_identity(target)
    shutil.copy2(target, backup)
    _copy_windows_security_identity(target, backup)
    assert _security_identity(backup) == expected_identity
    target.write_bytes(b"partial-new")
    journal.write_text(json.dumps({
        "version": 1,
        "status": "COMMITTING",
        "transaction_root": "data/staging/transactions/recover_acl",
        "entries": [{
            "target": "data/target.parquet",
            "staged": "data/staging/new.parquet",
            "backup": "data/staging/transactions/recover_acl/backups/0.bak",
            "before_sha256": hashlib.sha256(b"known-old").hexdigest(),
        }],
    }), encoding="utf-8")

    _recover_replacement_transaction(root, journal)

    assert target.read_bytes() == b"known-old"
    assert _security_identity(target) == expected_identity
    assert not journal.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL boundary")
def test_cross_file_commit_preserves_protected_target_dacl(tmp_path):
    root = tmp_path
    target = root / "data/target.parquet"
    staged = root / "data/staging/target.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    staged.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"old")
    staged.write_bytes(b"new")
    before = _protected_identity(target)

    _commit_replacements(
        root,
        transaction_name="test_acl_commit",
        replacements=[(target, staged)],
    )

    assert target.read_bytes() == b"new"
    assert _security_identity(target) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL boundary")
def test_cross_file_rollback_preserves_protected_target_dacl(tmp_path, monkeypatch):
    root = tmp_path
    target_a = root / "data/a.parquet"
    target_b = root / "data/b.parquet"
    staged_a = root / "data/staging/a.parquet"
    staged_b = root / "data/staging/b.parquet"
    for path, body in ((target_a, b"old-a"), (target_b, b"old-b"),
                       (staged_a, b"new-a"), (staged_b, b"new-b")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    before_a = _protected_identity(target_a)
    before_b = _protected_identity(target_b)

    import stock_data.pipelines.canonical_equity_incremental as module
    real_replace = module.os.replace

    def fail_second(source, destination):
        if str(destination) == str(target_b) and str(source) == str(staged_b):
            raise OSError("injected replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second)
    with pytest.raises(OSError, match="injected"):
        _commit_replacements(
            root,
            transaction_name="test_acl_rollback",
            replacements=[(target_a, staged_a), (target_b, staged_b)],
        )

    assert target_a.read_bytes() == b"old-a"
    assert target_b.read_bytes() == b"old-b"
    assert _security_identity(target_a) == before_a
    assert _security_identity(target_b) == before_b
