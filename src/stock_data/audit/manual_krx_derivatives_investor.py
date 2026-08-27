from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq


EXPECTED_HEADER = ("일자", "기관 합계", "기타법인", "개인", "외국인 합계", "전체")


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _assert_plain_existing_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if not current.exists():
            continue
        info = os.lstat(current)
        if current.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValueError(f"redirected path component: {current}")


def _parse_csv(body: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        text = body.decode("cp949")
    except UnicodeDecodeError as exc:
        raise ValueError("manual KRX CSV is not strict CP949") from exc
    rows = list(csv.reader(text.splitlines()))
    if not rows or tuple(rows[0]) != EXPECTED_HEADER:
        raise ValueError("unexpected manual KRX CSV header")
    if any(len(row) != len(EXPECTED_HEADER) for row in rows[1:]):
        raise ValueError("malformed manual KRX CSV row")
    if not rows[1:]:
        raise ValueError("empty manual KRX CSV")
    return rows[0], rows[1:]


def build_inventory(project_root: Path) -> dict[str, object]:
    project_root = project_root.resolve()
    inbox = project_root / "docs" / "data" / "sources" / "krx" / "manual_inbox"
    _assert_plain_existing_components(inbox)
    if not inbox.is_dir() or inbox.is_symlink():
        raise ValueError("manual KRX inbox is missing or redirected")
    paths = sorted(path for path in inbox.iterdir() if path.is_file())
    if not paths:
        raise ValueError("manual KRX inbox has no files")

    records: list[dict[str, object]] = []
    values_by_date: dict[str, tuple[str, ...]] = {}
    appearances: defaultdict[str, list[str]] = defaultdict(list)
    whole_hashes: defaultdict[str, list[str]] = defaultdict(list)
    conflicting_dates: list[str] = []
    for path in paths:
        if path.is_symlink() or path.suffix.lower() != ".csv":
            raise ValueError(f"unsupported or redirected inbox entry: {path.name}")
        body = path.read_bytes()
        _, rows = _parse_csv(body)
        dates: list[str] = []
        for row in rows:
            date = row[0]
            try:
                year, month, day = (int(part) for part in date.split("/"))
                normalized = dt.date(year, month, day).isoformat()
            except Exception as exc:
                raise ValueError(f"invalid date in {path.name}: {date}") from exc
            for token in row[1:]:
                try:
                    number = float(token)
                except ValueError as exc:
                    raise ValueError(f"invalid numeric token in {path.name}") from exc
                if not (number == number and abs(number) != float("inf")):
                    raise ValueError(f"non-finite numeric token in {path.name}")
            logical = tuple(row[1:])
            if normalized in values_by_date and values_by_date[normalized] != logical:
                conflicting_dates.append(normalized)
            values_by_date.setdefault(normalized, logical)
            appearances[normalized].append(path.name)
            dates.append(normalized)
        if len(dates) != len(set(dates)):
            raise ValueError(f"duplicate date inside {path.name}")
        digest = _sha256(body)
        whole_hashes[digest].append(path.name)
        records.append(
            {
                "original_filename": path.name,
                "bytes": len(body),
                "sha256": digest,
                "format": "csv",
                "encoding": "cp949",
                "header": list(EXPECTED_HEADER),
                "physical_rows": len(rows),
                "date_min": min(dates),
                "date_max": max(dates),
            }
        )
    if conflicting_dates:
        raise ValueError(f"conflicting overlapping dates: {sorted(set(conflicting_dates))[:5]}")

    overlap_dates = sorted(date for date, names in appearances.items() if len(names) > 1)
    duplicate_downloads = [names for names in whole_hashes.values() if len(names) > 1]
    inventory_core = {
        "schema_version": 1,
        "source": "user_provided_official_krx_basic_statistics_csv",
        "source_screen": "KRX Basic Statistics 15007",
        "files": records,
    }
    inventory_sha256 = _sha256(_canonical(inventory_core))
    return {
        **inventory_core,
        "inventory_sha256": inventory_sha256,
        "file_count": len(records),
        "physical_rows": sum(int(record["physical_rows"]) for record in records),
        "unique_dates": len(values_by_date),
        "date_min": min(values_by_date),
        "date_max": max(values_by_date),
        "overlap_date_count": len(overlap_dates),
        "overlap_dates": overlap_dates,
        "conflicting_overlap_count": 0,
        "duplicate_download_groups": duplicate_downloads,
        "classification": {
            "product": "KOSPI200_FUTURES_FROM_FILENAME_HINT",
            "side": "NET_BUY_FROM_FILENAME_HINT",
            "measure": "UNRESOLVED_NOT_ENCODED_IN_CSV",
            "option_right": "NOT_APPLICABLE",
            "session": "UNRESOLVED_NOT_ENCODED_IN_CSV",
            "volume_unit_source": "UNRESOLVED_NOT_ENCODED_IN_CSV",
            "trading_value_unit_source": "UNRESOLVED_NOT_ENCODED_IN_CSV",
            "investor_categories": list(EXPECTED_HEADER[1:]),
        },
        "target_assessment": {
            "kr_kospi200_futures_investor_trading_daily": "NOT_BUILDABLE_INCOMPLETE_MEASURES_AND_SETTINGS",
            "kr_kospi200_options_investor_trading_daily": "NOT_BUILDABLE_NO_SOURCE_FILES",
            "normalized_writes": False,
            "reason": (
                "Files contain one net series only. Sell, buy, volume/value identity, "
                "session, and source units are not encoded; no options files are present."
            ),
        },
    }


def retain_inventory(project_root: Path) -> dict[str, object]:
    project_root = project_root.resolve()
    report = build_inventory(project_root)
    digest = str(report["inventory_sha256"])
    inbox = project_root / "docs" / "data" / "sources" / "krx" / "manual_inbox"
    target = project_root / "data" / "landing" / "manual" / "krx_basic_statistics" / "derivatives_investor" / digest
    _assert_plain_existing_components(target.parent)
    originals = target / "originals"
    target.mkdir(parents=True, exist_ok=True)
    originals.mkdir(parents=True, exist_ok=True)
    _assert_plain_existing_components(originals)
    if target.is_symlink() or originals.is_symlink():
        raise ValueError("manual Landing path is redirected")

    for record in report["files"]:
        source = inbox / str(record["original_filename"])
        body = source.read_bytes()
        if _sha256(body) != record["sha256"]:
            raise RuntimeError("manual inbox changed during retention")
        destination = originals / source.name
        if destination.exists():
            if destination.is_symlink() or destination.read_bytes() != body:
                raise FileExistsError(destination)
        else:
            with tempfile.NamedTemporaryFile(dir=originals, prefix=".copy-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        if _sha256(destination.read_bytes()) != record["sha256"]:
            raise RuntimeError("Landing copy hash differs")
        record["landing_file"] = destination.relative_to(project_root).as_posix()

    manifest = target / "manifest.json"
    payload = _canonical(report)
    if manifest.exists():
        if manifest.is_symlink() or manifest.read_bytes() != payload:
            raise FileExistsError(manifest)
        status = "ALREADY_RECORDED"
    else:
        with tempfile.NamedTemporaryFile(dir=target, prefix=".manifest-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, manifest)
        finally:
            temporary.unlink(missing_ok=True)
        status = "CREATED"
    rebuilt = build_inventory(project_root)
    if rebuilt["inventory_sha256"] != digest:
        raise RuntimeError("manual inbox changed after retention")
    return {
        "status": status,
        "manifest": manifest.relative_to(project_root).as_posix(),
        "manifest_sha256": _sha256(payload),
        "inventory_sha256": digest,
        "file_count": report["file_count"],
        "physical_rows": report["physical_rows"],
        "unique_dates": report["unique_dates"],
        "date_min": report["date_min"],
        "date_max": report["date_max"],
        "normalized_writes": False,
    }


def audit_retained_inventory(project_root: Path, inventory_sha256: str, *, write: bool = False) -> dict[str, object]:
    project_root = project_root.resolve()
    if len(inventory_sha256) != 64 or any(char not in "0123456789abcdef" for char in inventory_sha256):
        raise ValueError("invalid inventory digest")
    root = project_root / "data/landing/manual/krx_basic_statistics/derivatives_investor" / inventory_sha256
    _assert_plain_existing_components(root)
    manifest_path = root / "manifest.json"
    if root.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("retained manual Landing is missing or redirected")
    manifest_body = manifest_path.read_bytes()
    manifest = json.loads(manifest_body)
    current = build_inventory(project_root)
    if current["inventory_sha256"] != inventory_sha256:
        raise RuntimeError("manual inbox no longer matches retained inventory")
    if manifest["inventory_sha256"] != inventory_sha256:
        raise RuntimeError("manifest inventory digest differs")
    expected = json.loads(json.dumps(current, ensure_ascii=False))
    for record in expected["files"]:
        record["landing_file"] = (
            root / "originals" / str(record["original_filename"])
        ).relative_to(project_root).as_posix()
    if manifest != expected:
        raise RuntimeError("manifest differs from independently rebuilt inventory")
    verified_files = []
    logical_rows: dict[str, tuple[float, ...]] = {}
    for record in manifest["files"]:
        landing = project_root / record["landing_file"]
        if landing.is_symlink() or not landing.is_file():
            raise ValueError("retained original is missing or redirected")
        body = landing.read_bytes()
        if len(body) != record["bytes"] or _sha256(body) != record["sha256"]:
            raise RuntimeError("retained original hash/bytes differ")
        _, rows = _parse_csv(body)
        for row in rows:
            date = row[0].replace("/", "-")
            values = tuple(float(token) for token in row[1:])
            if date in logical_rows and logical_rows[date] != values:
                raise RuntimeError("retained overlap values conflict")
            logical_rows.setdefault(date, values)
        verified_files.append(record["landing_file"])
    residuals = [sum(values[:4]) - values[4] for values in logical_rows.values()]
    if any(abs(value) > 1 for value in residuals):
        raise RuntimeError("investor category total residual exceeds source rounding boundary")
    calendar_result: dict[str, object] = {"status": "NOT_AVAILABLE"}
    canonical_root = project_root / "data/published/kr_equity_canonical_universe_daily"
    canonical_paths = sorted(canonical_root.rglob("data.parquet")) if canonical_root.is_dir() else []
    if canonical_paths:
        canonical_dates: set[str] = set()
        for path in canonical_paths:
            table = pq.read_table(path, columns=["date"])
            canonical_dates.update(str(value) for value in table.column("date").to_pylist())
        source_dates = set(logical_rows)
        comparable = {
            date for date in canonical_dates
            if manifest["date_min"] <= date <= min(manifest["date_max"], max(canonical_dates))
        }
        missing = sorted(comparable - source_dates)
        extras = sorted(source_dates - canonical_dates)
        calendar_result = {
            "status": "PASS" if not missing else "FAIL",
            "missing_retained_trading_dates": len(missing),
            "extra_source_dates": extras,
            "canonical_max": max(canonical_dates),
        }
        if missing:
            raise RuntimeError("manual series misses retained canonical trading dates")
    report = {
        "report_schema": "manual_krx_derivatives_investor_landing_audit_v1",
        "result": "PASS_LANDING_ONLY_TARGET_INPUT_INCOMPLETE",
        "inventory_sha256": inventory_sha256,
        "manifest_sha256": _sha256(manifest_body),
        "verified_file_count": len(verified_files),
        "physical_rows": manifest["physical_rows"],
        "unique_dates": manifest["unique_dates"],
        "date_min": manifest["date_min"],
        "date_max": manifest["date_max"],
        "overlap_date_count": manifest["overlap_date_count"],
        "duplicate_download_groups": manifest["duplicate_download_groups"],
        "category_total_residual_counts": {
            "minus_one": sum(value == -1 for value in residuals),
            "zero": sum(value == 0 for value in residuals),
            "plus_one": sum(value == 1 for value in residuals),
            "absolute_gt_one": sum(abs(value) > 1 for value in residuals),
        },
        "trading_calendar_comparison": calendar_result,
        "target_assessment": manifest["target_assessment"],
        "network_calls": 0,
    }
    report_body = _canonical(report)
    report_sha256 = _sha256(report_body)
    if write:
        audit_root = project_root / "data/state/audits/krx_derivatives_investor_manual"
        _assert_plain_existing_components(audit_root.parent)
        audit_root.mkdir(parents=True, exist_ok=True)
        _assert_plain_existing_components(audit_root)
        target = audit_root / f"{report_sha256}.json"
        if target.exists():
            if target.is_symlink() or target.read_bytes() != report_body:
                raise FileExistsError(target)
        else:
            with tempfile.NamedTemporaryFile(dir=audit_root, prefix=".audit-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(report_body)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        report["audit_path"] = target.relative_to(project_root).as_posix()
    report["audit_sha256"] = report_sha256
    return report
