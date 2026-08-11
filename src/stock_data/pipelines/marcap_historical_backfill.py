from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import pandas as pd

from stock_data.contracts.kr_equity import (
    KR_EQUITY_CANONICAL_UNIVERSE_DAILY, KR_EQUITY_MARKET_CAP_DAILY, KR_EQUITY_MASTER,
    KR_EQUITY_PRICE_DAILY, KR_EQUITY_UNIVERSE_DAILY,
)
from stock_data.contracts.kr_market import KR_MARKET_BREADTH_DAILY
from stock_data.derived.market_breadth import calculate_market_breadth
from stock_data.pipelines.krx_historical_backfill import _partition_upsert
from stock_data.providers.financedata_marcap.equity import normalize_annual
from stock_data.published.canonical_equity_universe import (
    build_canonical_universe, validate_canonical_universe,
)
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.validation.kr_equity import (
    validate_equity_market_cap, validate_equity_master, validate_equity_price,
)
from stock_data.validation.kr_market import validate_market_breadth


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".parquet.tmp", prefix=path.stem + "_",
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
        frame.to_parquet(temporary, index=False)
        restored = pd.read_parquet(temporary)
        if len(restored) != len(frame) or tuple(restored.columns) != tuple(frame.columns):
            raise ValueError("marcap quarantine read-back failed")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_json_atomic(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json.tmp",
                                         prefix=path.stem + "_", dir=path.parent,
                                         delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            temporary = Path(handle.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise ValueError("marcap state read-back failed")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_marcap_historical_backfill(project_root: Path) -> dict[str, int]:
    landing = project_root / "data/landing/financedata_marcap"
    manifest = json.loads((landing / "manifest.json").read_text(encoding="utf-8"))
    master = read_dataset(
        project_root / "data/normalized/kr_equity_master", KR_EQUITY_MASTER,
        validate_equity_master,
    )
    all_prices, all_canonical = [], []
    totals = {"price_rows": 0, "cap_rows": 0, "universe_rows": 0,
              "canonical_rows": 0, "quarantine_rows": 0}

    for year in range(1995, 2010):
        source_file = f"marcap-{year}.parquet"
        source_path = landing / "raw" / source_file
        expected = manifest["files"][source_file]["sha256"]
        if _sha256(source_path) != expected:
            raise ValueError(f"marcap checksum mismatch: {source_file}")
        raw = pd.read_parquet(source_path)
        raw = raw[pd.to_datetime(raw["Date"]).between("1995-05-02", "2009-12-30")]
        normalized = normalize_annual(raw, source_file)
        _write_parquet_atomic(
            normalized.quarantine,
            landing / "quarantine" / f"year={year}" / "data.parquet",
        )
        for frame, root, contract, validator in (
            (normalized.price, project_root / "data/normalized/kr_equity_price_daily",
             KR_EQUITY_PRICE_DAILY, validate_equity_price),
            (normalized.market_cap, project_root / "data/normalized/kr_equity_market_cap_daily",
             KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap),
            (normalized.universe, project_root / "data/normalized/kr_equity_universe_daily",
             KR_EQUITY_UNIVERSE_DAILY,
             lambda value: validate_data_v1(value, KR_EQUITY_UNIVERSE_DAILY)),
        ):
            _partition_upsert(frame, root, contract, validator)

        identity = normalized.universe[["date", "market", "symbol", "isin"]].copy()
        identity["name"] = normalized.universe["short_name"]
        listed = normalized.universe.copy()
        listed["name"] = listed["short_name"]
        canonical = build_canonical_universe(listed, identity, master)
        _partition_upsert(
            canonical,
            project_root / "data/published/kr_equity_canonical_universe_daily",
            KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
            validate_canonical_universe,
        )
        all_prices.append(normalized.price)
        all_canonical.append(canonical)
        totals["price_rows"] += len(normalized.price)
        totals["cap_rows"] += len(normalized.market_cap)
        totals["universe_rows"] += len(normalized.universe)
        totals["canonical_rows"] += len(canonical)
        totals["quarantine_rows"] += len(normalized.quarantine)

    prices = pd.concat(all_prices, ignore_index=True).sort_values(
        list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    canonical = pd.concat(all_canonical, ignore_index=True).sort_values(
        list(KR_EQUITY_CANONICAL_UNIVERSE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    breadth = calculate_market_breadth(prices, canonical)
    _partition_upsert(
        breadth, project_root / "data/derived/kr_market_breadth_daily",
        KR_MARKET_BREADTH_DAILY, validate_market_breadth,
    )
    totals["breadth_rows"] = len(breadth)
    totals["master_missing_rows"] = int((~canonical["master_present"]).sum())
    _write_json_atomic({
        "dataset": "financedata_marcap_historical",
        "repository_commit": manifest["repository_commit"],
        "completed_years": list(range(1995, 2010)),
        "coverage_start": "1995-05-02",
        "coverage_end": "2009-12-30",
        "quarantine_rows": totals["quarantine_rows"],
        "staged": [], "failed": {}, "status": "complete",
    }, project_root / "data/state/financedata_marcap_backfill.json")
    return totals
