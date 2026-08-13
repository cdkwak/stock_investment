from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from stock_data.audit.manual_krx_derivatives_investor import (
    _canonical,
    _parse_csv,
    _sha256,
    audit_retained_inventory,
)
from stock_data.contracts.krx_derivatives_investor import (
    KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.krx_derivatives_investor import validate_futures_investor_net_purchase


SETTINGS_EVIDENCE = {
    "evidence_schema": "krx_15007_manual_download_settings_v1",
    "reviewed_date": "2026-08-14",
    "screen_number": "15007",
    "screen_name": "투자자별 거래실적",
    "screen_url": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201050302",
    "query_type": {"label": "일별추이", "code": "2"},
    "measure": {"label": "거래대금", "code": "AMT"},
    "side": {"label": "순매수", "code": "SUN"},
    "session": {"label": "전체", "code": ""},
    "unit": {"label": "백만원", "code": "3"},
    "product": {
        "normalized": "KOSPI200_FUTURES",
        "source_label": "코스피200 선물",
        "identity_evidence": [
            "user-declared target identity",
            "first six source filenames contain 선물순매수",
            "all later files form one exact-overlap continuous series",
            "the authenticated 15007 product selector exposes 코스피200 선물",
        ],
    },
    "scope_note": (
        "The inspected authenticated screen retained AMT/SUN/ALL/백만원 after the "
        "manual download sequence. The product selector had subsequently moved to "
        "코스피200 옵션; that later selection is not used as file identity evidence."
    ),
    "network_calls_for_review": 0,
}


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = _canonical(payload)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".json-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _landing_root(project_root: Path, digest: str) -> Path:
    return (
        project_root
        / "data/landing/manual/krx_basic_statistics/derivatives_investor"
        / digest
    )


def retain_settings_evidence(project_root: Path, inventory_sha256: str) -> tuple[Path, str]:
    root = _landing_root(project_root.resolve(), inventory_sha256)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("retained Landing root is missing or redirected")
    payload = {**SETTINGS_EVIDENCE, "inventory_sha256": inventory_sha256}
    body = _canonical(payload)
    digest = _sha256(body)
    target = root / f"source_settings_{digest}.json"
    if target.exists():
        if target.is_symlink() or target.read_bytes() != body:
            raise FileExistsError(target)
    else:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".settings-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target, digest


def build_normalized_candidate(project_root: Path, inventory_sha256: str) -> pd.DataFrame:
    project_root = project_root.resolve()
    manifest_path = _landing_root(project_root, inventory_sha256) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["inventory_sha256"] != inventory_sha256:
        raise RuntimeError("Landing manifest inventory identity differs")
    categories = tuple(manifest["files"][0]["header"][1:])
    by_date: dict[str, tuple[float, ...]] = {}
    for record in manifest["files"]:
        path = project_root / record["landing_file"]
        body = path.read_bytes()
        if len(body) != record["bytes"] or _sha256(body) != record["sha256"]:
            raise RuntimeError("Landing original differs from manifest")
        header, rows = _parse_csv(body)
        if tuple(header[1:]) != categories:
            raise RuntimeError("source categories changed")
        for row in rows:
            date = row[0].replace("/", "-")
            values = tuple(float(token) for token in row[1:])
            if date in by_date and by_date[date] != values:
                raise RuntimeError("conflicting overlap")
            by_date.setdefault(date, values)
    records = []
    for date in sorted(by_date):
        for category, value in zip(categories, by_date[date]):
            records.append(
                {
                    "date": date,
                    "product": "KOSPI200_FUTURES",
                    "session": "ALL",
                    "investor_type_source": category,
                    "net_purchase_trading_value": value,
                    "trading_value_unit_source": "백만원",
                    "source": "KRX_BASIC_STATISTICS",
                    "source_operation": "15007_DAILY_TREND_TRADING_VALUE_NET_PURCHASE",
                    "source_inventory_sha256": inventory_sha256,
                }
            )
    result = pd.DataFrame(records, columns=KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY.column_names)
    result = result.sort_values(
        list(KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_futures_investor_net_purchase(result)
    return result


def _parquet_manifest(root: Path) -> dict[str, object]:
    files = []
    for path in sorted(root.rglob("data.parquet")):
        body = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(body),
                "sha256": _sha256(body),
                "rows": pq.ParquetFile(path).metadata.num_rows,
            }
        )
    return {"files": files, "sha256": _sha256(_canonical(files))}


def promote_manual_history(project_root: Path, inventory_sha256: str) -> dict[str, object]:
    project_root = project_root.resolve()
    audit_retained_inventory(project_root, inventory_sha256)
    settings_path, settings_sha256 = retain_settings_evidence(project_root, inventory_sha256)
    candidate = build_normalized_candidate(project_root, inventory_sha256)
    target = project_root / "data/normalized/kr_kospi200_futures_investor_net_purchase_daily"
    state_path = project_root / "data/state/kr_kospi200_futures_investor_net_purchase_daily.json"
    if target.exists():
        current = read_dataset(
            target,
            KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY,
            validate_futures_investor_net_purchase,
        )
        pd.testing.assert_frame_equal(current, candidate, check_exact=True)
        status = "ALREADY_PROMOTED"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".krx-investor-net-", dir=target.parent))
        try:
            write_dataset_atomic(
                candidate,
                stage,
                KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY,
                validate_futures_investor_net_purchase,
            )
            verified = read_dataset(
                stage,
                KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY,
                validate_futures_investor_net_purchase,
            )
            pd.testing.assert_frame_equal(verified, candidate, check_exact=True)
            os.replace(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        status = "PROMOTED"
    manifest = _parquet_manifest(target)
    state = {
        "dataset": KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY.name,
        "contract_version": 1,
        "status": "DATA_COMPLETE_FOR_SOURCE_SCOPE",
        "rows": len(candidate),
        "unique_dates": int(candidate["date"].nunique()),
        "date_min": candidate["date"].min(),
        "date_max": candidate["date"].max(),
        "investor_categories": sorted(candidate["investor_type_source"].unique().tolist()),
        "product": "KOSPI200_FUTURES",
        "session": "ALL",
        "measure": "NET_PURCHASE_TRADING_VALUE",
        "unit": "백만원",
        "source_inventory_sha256": inventory_sha256,
        "source_settings_path": settings_path.relative_to(project_root).as_posix(),
        "source_settings_sha256": settings_sha256,
        "output_manifest": manifest,
        "network_calls": 0,
    }
    if state_path.exists():
        existing = json.loads(state_path.read_text(encoding="utf-8"))
        if existing != state:
            raise RuntimeError("existing state differs from deterministic rebuild")
    else:
        _atomic_json(state_path, state)
    return {**state, "promotion_status": status}


def audit_promoted_history(project_root: Path, inventory_sha256: str) -> dict[str, object]:
    project_root = project_root.resolve()
    expected = build_normalized_candidate(project_root, inventory_sha256)
    target = project_root / "data/normalized/kr_kospi200_futures_investor_net_purchase_daily"
    actual = read_dataset(
        target,
        KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY,
        validate_futures_investor_net_purchase,
    )
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    state_path = project_root / "data/state/kr_kospi200_futures_investor_net_purchase_daily.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    manifest = _parquet_manifest(target)
    if state["output_manifest"] != manifest or state["rows"] != len(actual):
        raise RuntimeError("state/output evidence differs")
    report = {
        "result": "PASS",
        "dataset": KR_KOSPI200_FUTURES_INVESTOR_NET_PURCHASE_DAILY.name,
        "rows": len(actual),
        "unique_dates": int(actual["date"].nunique()),
        "date_min": actual["date"].min(),
        "date_max": actual["date"].max(),
        "landing_to_normalized_exact": True,
        "primary_key_duplicates": int(actual.duplicated(["date", "investor_type_source"]).sum()),
        "null_cells": int(actual.isna().sum().sum()),
        "infinite_values": int(
            actual["net_purchase_trading_value"].isin([float("inf"), float("-inf")]).sum()
        ),
        "output_manifest_sha256": manifest["sha256"],
        "source_inventory_sha256": inventory_sha256,
        "network_calls": 0,
    }
    report_body = _canonical(report)
    report_sha256 = _sha256(report_body)
    audit_root = project_root / "data/state/audits/krx_kospi200_futures_investor_net_purchase_daily"
    audit_root.mkdir(parents=True, exist_ok=True)
    audit_path = audit_root / f"{report_sha256}.json"
    if audit_path.exists():
        if audit_path.read_bytes() != report_body:
            raise FileExistsError(audit_path)
    else:
        with tempfile.NamedTemporaryFile(dir=audit_root, prefix=".audit-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(report_body)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, audit_path)
        finally:
            temporary.unlink(missing_ok=True)
    return {**report, "audit_sha256": report_sha256, "audit_path": audit_path.relative_to(project_root).as_posix()}
