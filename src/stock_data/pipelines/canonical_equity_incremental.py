from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
import hashlib
from typing import Callable

import pandas as pd
import pyarrow.parquet as pq

from stock_data.contracts.kr_equity import (
    KR_EQUITY_CANONICAL_UNIVERSE_DAILY, KR_EQUITY_MARKET_CAP_DAILY,
    KR_EQUITY_MASTER, KR_EQUITY_PRICE_DAILY, KR_EQUITY_UNIVERSE_DAILY,
)
from stock_data.contracts.kr_market import KR_MARKET_BREADTH_DAILY
from stock_data.derived.market_breadth import calculate_market_breadth
from stock_data.pipelines.backfill_state import BackfillState
from stock_data.providers.data_go_kr.stock_price import normalize_stock_price_items
from stock_data.providers.data_go_kr.universe import normalize_universe_items
from stock_data.published.canonical_equity_universe import (
    build_canonical_universe, price_identity_from_items, validate_canonical_universe,
)
from stock_data.storage.contract_arrow import dataframe_to_contract_table, restore_contract_dates
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.validation.kr_equity import (
    validate_equity_market_cap, validate_equity_master, validate_equity_price,
)
from stock_data.validation.kr_market import validate_market_breadth


class CanonicalEquityIncrementalError(RuntimeError):
    pass


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _windows_security_information(*, owner_matches: bool, protected: bool) -> int:
    """Return the exact Windows security fields required for one replacement."""

    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    protected_dacl_security_information = 0x80000000
    unprotected_dacl_security_information = 0x20000000
    return (
        dacl_security_information
        | (
            protected_dacl_security_information
            if protected else unprotected_dacl_security_information
        )
        | (0 if owner_matches else owner_security_information)
    )


def _copy_windows_security_identity(source: Path, destination: Path) -> None:
    """Copy one target owner and DACL, including inheritance protection."""

    if os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    se_file_object = 1
    owner_security_information = 0x00000001
    dacl_security_information = 0x00000004
    se_dacl_protected = 0x1000

    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    get_named_security_info = advapi32.GetNamedSecurityInfoW
    get_named_security_info.argtypes = (
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_named_security_info.restype = wintypes.DWORD
    set_named_security_info = advapi32.SetNamedSecurityInfoW
    set_named_security_info.argtypes = (
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    )
    set_named_security_info.restype = wintypes.DWORD
    get_security_descriptor_control = advapi32.GetSecurityDescriptorControl
    get_security_descriptor_control.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    get_security_descriptor_control.restype = wintypes.BOOL
    equal_sid = advapi32.EqualSid
    equal_sid.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    equal_sid.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p

    source_owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    source_descriptor = ctypes.c_void_p()
    result = get_named_security_info(
        str(source), se_file_object,
        owner_security_information | dacl_security_information,
        ctypes.byref(source_owner), None, ctypes.byref(dacl), None,
        ctypes.byref(source_descriptor),
    )
    if result:
        raise CanonicalEquityIncrementalError(
            f"target security identity read failed with Windows error {result}"
        )
    destination_owner = ctypes.c_void_p()
    destination_descriptor = ctypes.c_void_p()
    try:
        result = get_named_security_info(
            str(destination), se_file_object, owner_security_information,
            ctypes.byref(destination_owner), None, None, None,
            ctypes.byref(destination_descriptor),
        )
        if result:
            raise CanonicalEquityIncrementalError(
                "replacement owner read failed with Windows error "
                f"{result}"
            )
        owner_matches = bool(equal_sid(source_owner, destination_owner))
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not get_security_descriptor_control(
            source_descriptor, ctypes.byref(control), ctypes.byref(revision),
        ):
            raise CanonicalEquityIncrementalError(
                "target DACL protection read failed"
            )
        security_information = _windows_security_information(
            owner_matches=owner_matches,
            protected=bool(control.value & se_dacl_protected),
        )
        result = set_named_security_info(
            str(destination), se_file_object, security_information,
            None if owner_matches else source_owner, None, dacl, None,
        )
        if result:
            raise CanonicalEquityIncrementalError(
                "target security identity preservation failed with Windows "
                f"error {result}"
            )
    finally:
        if destination_descriptor:
            kernel32.LocalFree(destination_descriptor)
        kernel32.LocalFree(source_descriptor)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _recover_replacement_transaction(project_root: Path, journal: Path) -> None:
    """Roll an interrupted prepared/committing transaction back fail-closed."""
    if not journal.exists():
        return
    payload = json.loads(journal.read_text(encoding="utf-8"))
    if payload.get("status") not in {"PREPARED", "COMMITTING"}:
        raise CanonicalEquityIncrementalError("unknown replacement transaction state")
    for entry in reversed(payload.get("entries", [])):
        target = project_root / entry["target"]
        backup = project_root / entry["backup"] if entry.get("backup") else None
        before = entry.get("before_sha256")
        if backup is None:
            if before is not None:
                raise CanonicalEquityIncrementalError("transaction journal is missing a required backup")
            target.unlink(missing_ok=True)
        else:
            if not backup.exists() or _sha256(backup) != before:
                raise CanonicalEquityIncrementalError("transaction backup is missing or corrupt")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, target)
    transaction_root = project_root / payload["transaction_root"]
    journal.unlink()
    shutil.rmtree(transaction_root, ignore_errors=True)


def _commit_replacements(project_root: Path, *, transaction_name: str,
                         replacements: list[tuple[Path, Path]]) -> int:
    """Durably journal a cross-file replacement and recover by rollback."""
    state_root = project_root / "data/state"
    journal = state_root / f"{transaction_name}.transaction.json"
    _recover_replacement_transaction(project_root, journal)
    transaction_root = project_root / "data/staging/transactions" / transaction_name
    if transaction_root.exists():
        raise CanonicalEquityIncrementalError("untracked transaction staging exists")
    backup_root = transaction_root / "backups"
    backup_root.mkdir(parents=True, exist_ok=False)
    entries = []
    for index, (target, staged) in enumerate(replacements):
        before = _sha256(target)
        backup = None
        if before is not None:
            backup = backup_root / f"{index}.bak"
            shutil.copy2(target, backup)
            if _sha256(backup) != before:
                raise CanonicalEquityIncrementalError("transaction backup verification failed")
            _copy_windows_security_identity(target, backup)
            _copy_windows_security_identity(target, staged)
        entries.append({
            "target": str(target.relative_to(project_root)),
            "staged": str(staged.relative_to(project_root)),
            "backup": str(backup.relative_to(project_root)) if backup else None,
            "before_sha256": before,
        })
    payload = {
        "version": 1,
        "status": "PREPARED",
        "transaction_root": str(transaction_root.relative_to(project_root)),
        "entries": entries,
    }
    _write_json_atomic(journal, payload)
    payload["status"] = "COMMITTING"
    _write_json_atomic(journal, payload)
    try:
        for target, staged in replacements:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
    except Exception:
        _recover_replacement_transaction(project_root, journal)
        raise
    journal.unlink()
    shutil.rmtree(transaction_root, ignore_errors=True)
    return len(replacements)


@dataclass(frozen=True)
class DateFrames:
    price: pd.DataFrame
    market_cap: pd.DataFrame
    universe: pd.DataFrame
    canonical: pd.DataFrame


def publication_window_passed(*, deadline_kst: datetime, now_kst: datetime) -> bool:
    if deadline_kst.tzinfo is None or now_kst.tzinfo is None:
        raise ValueError("publication timestamps must be timezone-aware")
    return now_kst >= deadline_kst


def _items(path: Path) -> list[dict]:
    pages = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for page in pages:
        raw = page["response"]["body"].get("items", {}).get("item", [])
        rows.extend(raw if isinstance(raw, list) else [raw])
    return rows


def build_date_frames(project_root: Path, *, base_date: str, price_landing: Path,
                      universe_landing: Path) -> DateFrames:
    expected = datetime.strptime(base_date, "%Y%m%d").strftime("%Y-%m-%d")
    price_items = _items(price_landing)
    universe_items = _items(universe_landing)
    scoped_price = [r for r in price_items if str(r.get("mrktCtg", "")).strip() in {"KOSPI", "KOSDAQ"}]
    scoped_universe = [r for r in universe_items if str(r.get("mrktCtg", "")).strip() in {"KOSPI", "KOSDAQ"}]
    if not scoped_price or not scoped_universe:
        raise CanonicalEquityIncrementalError("both exact-date source streams must be non-empty")
    normalized = normalize_stock_price_items(scoped_price)
    universe = normalize_universe_items(scoped_universe)
    for frame in (normalized.price, normalized.market_cap, universe):
        if set(frame["date"]) != {expected}:
            raise CanonicalEquityIncrementalError("source row date differs from exact basDt")
    validate_equity_price(normalized.price)
    validate_equity_market_cap(normalized.market_cap)
    validate_data_v1(universe, KR_EQUITY_UNIVERSE_DAILY, allow_empty=False)
    master = read_dataset(project_root / "data/normalized/kr_equity_master", KR_EQUITY_MASTER,
                          validate_equity_master)
    canonical = build_canonical_universe(universe, price_identity_from_items(scoped_price), master)
    return DateFrames(normalized.price, normalized.market_cap, universe, canonical)


def _candidate_partition(target: Path, incoming: pd.DataFrame, contract, validator: Callable) -> pd.DataFrame:
    if target.exists():
        existing = restore_contract_dates(pd.read_parquet(target), contract)
        overlap = existing.merge(incoming, on=list(contract.primary_key), how="inner")
        if not overlap.empty:
            old = existing.set_index(list(contract.primary_key)).loc[incoming.set_index(list(contract.primary_key)).index]
            retained_values=old.reset_index()[list(contract.column_names)].astype("string").fillna("<NULL>")
            incoming_values=incoming.reset_index(drop=True)[list(contract.column_names)].astype("string").fillna("<NULL>")
            if not retained_values.equals(incoming_values):
                raise CanonicalEquityIncrementalError("accepted key conflicts with retained partition")
            return existing[list(contract.column_names)].sort_values(list(contract.sort_key)).reset_index(drop=True)
        result = pd.concat([existing, incoming], ignore_index=True)
    else:
        result = incoming.copy()
    result = result[list(contract.column_names)].sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    validator(result)
    return result


def _write_candidate(path: Path, frame: pd.DataFrame, contract) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(dataframe_to_contract_table(frame, contract), path)


def promote_date_atomic(project_root: Path, *, base_date: str, frames: DateFrames,
                        landing_manifest_sha256: str) -> dict:
    iso = datetime.strptime(base_date, "%Y%m%d").strftime("%Y-%m-%d")
    specs = (
        ("price", frames.price, project_root/"data/normalized/kr_equity_price_daily", KR_EQUITY_PRICE_DAILY, validate_equity_price),
        ("market_cap", frames.market_cap, project_root/"data/normalized/kr_equity_market_cap_daily", KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap),
        ("universe", frames.universe, project_root/"data/normalized/kr_equity_universe_daily", KR_EQUITY_UNIVERSE_DAILY, lambda x: validate_data_v1(x, KR_EQUITY_UNIVERSE_DAILY, allow_empty=False)),
        ("canonical", frames.canonical, project_root/"data/published/kr_equity_canonical_universe_daily", KR_EQUITY_CANONICAL_UNIVERSE_DAILY, validate_canonical_universe),
    )
    accepted = project_root/"data/state/canonical_equity_accepted_dates.json"
    transaction = project_root/"data/state/canonical_equity_daily_incremental.transaction.json"
    _recover_replacement_transaction(project_root, transaction)
    if accepted.exists():
        retained_acceptance=json.loads(accepted.read_text(encoding="utf-8"))
        if iso in retained_acceptance.get("accepted_dates",[]):
            for _,frame,root,contract,validator in specs:
                for market,incoming in frame.groupby("market",sort=True):
                    target=root/f"market={market}"/f"year={iso[:4]}"/"data.parquet"
                    if not target.exists():
                        raise CanonicalEquityIncrementalError("accepted date target is missing")
                    _candidate_partition(target,incoming.reset_index(drop=True),contract,validator)
            return {"status":"ALREADY_ACCEPTED_IDEMPOTENT","date":iso,"targets":0,"price_rows":0,"market_cap_rows":0,"universe_rows":0,"canonical_rows":0}
    stage = Path(tempfile.mkdtemp(prefix=f"canonical_equity_{base_date}_", dir=project_root/"data/staging"))
    replacements: list[tuple[Path, Path]] = []
    try:
        for name, frame, root, contract, validator in specs:
            for market, incoming in frame.groupby("market", sort=True):
                target=root/f"market={market}"/f"year={iso[:4]}"/"data.parquet"
                candidate=_candidate_partition(target, incoming.reset_index(drop=True), contract, validator)
                staged=stage/name/f"market={market}"/f"year={iso[:4]}"/"data.parquet"
                _write_candidate(staged,candidate,contract); replacements.append((target,staged))
        state_specs=((project_root/"data/state/kr_equity_price_cap_daily.json","kr_equity_price_cap_daily"),(project_root/"data/state/kr_equity_universe_daily.json","kr_equity_universe_daily"))
        for path,dataset in state_specs:
            state=BackfillState.load(path,dataset); state.completed_partitions.add(base_date); state.valid_empty_partitions.discard(base_date); (state.staged_partitions or set()).discard(base_date); state.failed_partitions.pop(base_date,None)
            staged=stage/"state"/path.name; staged.parent.mkdir(parents=True,exist_ok=True)
            staged.write_text(json.dumps({"dataset":dataset,"completed_partitions":sorted(state.completed_partitions),"valid_empty_partitions":sorted(state.valid_empty_partitions),"failed_partitions":state.failed_partitions,"staged_partitions":sorted(state.staged_partitions or set())},ensure_ascii=False,indent=2),encoding="utf-8")
            replacements.append((path,staged))
        payload=json.loads(accepted.read_text(encoding="utf-8")) if accepted.exists() else {"accepted_dates":[]}
        if iso not in payload["accepted_dates"]: payload["accepted_dates"].append(iso)
        payload.update({"accepted_dates":sorted(payload["accepted_dates"]),"latest_accepted_date":max(payload["accepted_dates"]),"publication_policy":"D_PLUS_1_BUSINESS_DAY_AFTER_13_KST","revision_policy":"UNRESOLVED","last_landing_manifest_sha256":landing_manifest_sha256})
        staged=stage/"state"/accepted.name; staged.parent.mkdir(parents=True,exist_ok=True); staged.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8"); replacements.append((accepted,staged))
        breadth_state = project_root/"data/state/canonical_equity_breadth_status.json"
        breadth_payload = json.loads(breadth_state.read_text(encoding="utf-8")) if breadth_state.exists() else {"completed_dates": []}
        breadth_payload["pending_date"] = iso
        breadth_payload["status"] = "PENDING"
        staged = stage/"state"/breadth_state.name
        staged.write_text(json.dumps(breadth_payload,ensure_ascii=False,indent=2),encoding="utf-8")
        replacements.append((breadth_state, staged))
        _commit_replacements(project_root, transaction_name="canonical_equity_daily_incremental", replacements=replacements)
        return {"status":"CANONICAL_ACCEPTED_DATE","date":iso,"targets":len(replacements),"price_rows":len(frames.price),"market_cap_rows":len(frames.market_cap),"universe_rows":len(frames.universe),"canonical_rows":len(frames.canonical)}
    finally:
        shutil.rmtree(stage,ignore_errors=True)


def refresh_breadth_date_atomic(project_root: Path, *, base_date: str) -> dict:
    iso=datetime.strptime(base_date,"%Y%m%d").strftime("%Y-%m-%d"); year=iso[:4]
    accepted=project_root/"data/state/canonical_equity_accepted_dates.json"
    payload=json.loads(accepted.read_text(encoding="utf-8"))
    if iso not in payload.get("accepted_dates",[]): raise CanonicalEquityIncrementalError("breadth date is not canonical accepted")
    price=[]; canonical=[]
    for market in ("KOSPI","KOSDAQ"):
        pp=project_root/f"data/normalized/kr_equity_price_daily/market={market}/year={year}/data.parquet"
        cp=project_root/f"data/published/kr_equity_canonical_universe_daily/market={market}/year={year}/data.parquet"
        price.append(restore_contract_dates(pd.read_parquet(pp),KR_EQUITY_PRICE_DAILY))
        canonical.append(restore_contract_dates(pd.read_parquet(cp),KR_EQUITY_CANONICAL_UNIVERSE_DAILY))
    breadth=calculate_market_breadth(pd.concat(price,ignore_index=True).sort_values(list(KR_EQUITY_PRICE_DAILY.sort_key)).reset_index(drop=True),pd.concat(canonical,ignore_index=True))
    breadth=breadth[breadth.date.eq(iso)].reset_index(drop=True)
    if set(breadth.market)!={"KOSPI","KOSDAQ"}: raise CanonicalEquityIncrementalError("affected breadth markets incomplete")
    breadth_state = project_root/"data/state/canonical_equity_breadth_status.json"
    transaction = project_root/"data/state/canonical_equity_breadth.transaction.json"
    _recover_replacement_transaction(project_root, transaction)
    stage=Path(tempfile.mkdtemp(prefix=f"breadth_{base_date}_",dir=project_root/"data/staging"))
    replacements=[]
    try:
        for market,incoming in breadth.groupby("market",sort=True):
            target=project_root/f"data/derived/kr_market_breadth_daily/market={market}/year={year}/data.parquet"
            candidate=_candidate_partition(target,incoming.reset_index(drop=True),KR_MARKET_BREADTH_DAILY,validate_market_breadth)
            staged=stage/market/"data.parquet"; _write_candidate(staged,candidate,KR_MARKET_BREADTH_DAILY)
            replacements.append((target, staged))
        state_payload = json.loads(breadth_state.read_text(encoding="utf-8")) if breadth_state.exists() else {"completed_dates": []}
        completed = set(state_payload.get("completed_dates", [])); completed.add(iso)
        state_payload.update({"completed_dates": sorted(completed), "latest_completed_date": max(completed), "pending_date": None, "status": "COMPLETE"})
        staged_state = stage/"state"/breadth_state.name
        staged_state.parent.mkdir(parents=True, exist_ok=True)
        staged_state.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        replacements.append((breadth_state, staged_state))
        _commit_replacements(project_root, transaction_name="canonical_equity_breadth", replacements=replacements)
        return {"status":"AFFECTED_BREADTH_COMPLETE","date":iso,"rows":len(breadth)}
    finally: shutil.rmtree(stage,ignore_errors=True)
