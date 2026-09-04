from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

import numpy as np
import pandas as pd

from stock_data.contracts.global_market import (
    FRED_VIX_DAILY,
    GLOBAL_INDEX_PRICE_DAILY,
    US_VIX_TERM_STRUCTURE_DAILY,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.global_market import validate_fred, validate_global_index


DATASET = US_VIX_TERM_STRUCTURE_DAILY.name
DATASET_VERSION = US_VIX_TERM_STRUCTURE_DAILY.version
INDEX_SYMBOLS = ("VIX9D", "VIX3M", "VIX6M", "SKEW")
OUTPUT_COLUMNS = US_VIX_TERM_STRUCTURE_DAILY.column_names
PERCENTILE_WINDOW = 252
FORMULAS = {
    "ratio_1m_3m": "vix / vix3m",
    "ratio_9d_1m": "vix9d / vix",
    "regime": "contango when vix < vix3m else backwardation",
    "pct_rank_252": "percentile rank of ratio_1m_3m in the trailing 252 observations",
}


class VixTermStructureBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class Validation:
    rows: int
    coverage_start: str
    coverage_end: str
    primary_key_duplicates: int
    complete_curve_rows: int
    pct_rank_rows: int
    infinity_count: int


def _strict_numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    numeric = pd.to_numeric(frame[column], errors="coerce")
    if not numeric.isna().equals(frame[column].isna()):
        raise ValueError(f"invalid numeric source column: {column}")
    if numeric.dropna().map(lambda value: not math.isfinite(float(value))).any():
        raise ValueError(f"non-finite source column: {column}")
    return numeric.astype("float64")


def _source_dates(frame: pd.DataFrame, *, key: tuple[str, ...]) -> pd.Series:
    dates = pd.to_datetime(frame["date"], errors="raise")
    if dates.isna().any() or frame.loc[:, list(key)].duplicated().any():
        raise ValueError("invalid VIX term-structure source dates/keys")
    return dates.dt.date


def _last_percentile_rank(window: pd.Series) -> float:
    return float(window.rank(method="average", pct=True).iloc[-1])


def calculate_vix_term_structure(
    fred_vix: pd.DataFrame,
    global_indices: pd.DataFrame,
) -> pd.DataFrame:
    """Join retained source closes and calculate a full-window trailing rank."""

    if not {"date", "vixcls"} <= set(fred_vix.columns) or fred_vix.empty:
        raise ValueError("invalid fred_vix_daily source")
    if not {"date", "symbol", "close"} <= set(global_indices.columns) or global_indices.empty:
        raise ValueError("invalid global_index_price_daily source")

    vix = fred_vix.loc[:, ["date", "vixcls"]].copy()
    vix["date"] = _source_dates(vix, key=("date",))
    vix["vix"] = _strict_numeric(vix, "vixcls")
    vix = vix.drop(columns="vixcls")

    indices = global_indices.loc[
        global_indices["symbol"].astype(str).isin(INDEX_SYMBOLS),
        ["date", "symbol", "close"],
    ].copy()
    if set(indices["symbol"].astype(str)) != set(INDEX_SYMBOLS):
        raise ValueError("global index source does not contain every VIX term symbol")
    indices["symbol"] = indices["symbol"].astype(str)
    indices["date"] = _source_dates(indices, key=("date", "symbol"))
    indices["close"] = _strict_numeric(indices, "close")
    curve = indices.pivot(index="date", columns="symbol", values="close").rename(
        columns={"VIX9D": "vix9d", "VIX3M": "vix3m", "VIX6M": "vix6m", "SKEW": "skew"}
    )
    curve = curve.reindex(columns=("vix9d", "vix3m", "vix6m", "skew"))

    # FRED VIXCLS owns the derived observation calendar; Yahoo term closes are
    # joined onto those dates so a faster Yahoo row cannot advance freshness.
    result = vix.set_index("date").join(curve, how="left").sort_index().reset_index()
    result["ratio_1m_3m"] = result["vix"].div(result["vix3m"].replace(0.0, np.nan))
    result["ratio_9d_1m"] = result["vix9d"].div(result["vix"].replace(0.0, np.nan))
    result["regime"] = pd.Series(pd.NA, index=result.index, dtype="string")
    comparable = result[["vix", "vix3m"]].notna().all(axis=1)
    result.loc[comparable & result["vix"].lt(result["vix3m"]), "regime"] = "contango"
    result.loc[comparable & result["vix"].ge(result["vix3m"]), "regime"] = "backwardation"
    result["pct_rank_252"] = result["ratio_1m_3m"].rolling(
        PERCENTILE_WINDOW, min_periods=PERCENTILE_WINDOW,
    ).apply(_last_percentile_rank, raw=False)
    return result.loc[:, OUTPUT_COLUMNS].reset_index(drop=True)


def validate_vix_term_structure(
    fred_vix: pd.DataFrame,
    global_indices: pd.DataFrame,
    result: pd.DataFrame,
) -> Validation:
    if tuple(result.columns) != OUTPUT_COLUMNS or result.empty:
        raise VixTermStructureBuildError("output schema is empty or differs")
    dates = pd.to_datetime(result["date"], errors="raise")
    duplicates = int(dates.duplicated(keep=False).sum())
    if duplicates or dates.isna().any() or not dates.is_monotonic_increasing:
        raise VixTermStructureBuildError("output primary key is invalid")
    expected = calculate_vix_term_structure(fred_vix, global_indices)
    restored = result.copy()
    restored["date"] = dates.dt.date
    try:
        pd.testing.assert_frame_equal(
            restored.reset_index(drop=True), expected.reset_index(drop=True),
            check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-12,
        )
    except AssertionError as error:
        raise VixTermStructureBuildError("output formulas or null propagation differ") from error
    infinity = sum(
        int(restored[column].dropna().map(lambda value: not math.isfinite(float(value))).sum())
        for column in OUTPUT_COLUMNS
        if column not in {"date", "regime"}
    )
    if infinity:
        raise VixTermStructureBuildError("output contains infinity")
    complete = int(restored[["vix", "vix9d", "vix3m", "vix6m", "skew"]].notna().all(axis=1).sum())
    return Validation(
        rows=len(restored),
        coverage_start=dates.min().strftime("%Y-%m-%d"),
        coverage_end=dates.max().strftime("%Y-%m-%d"),
        primary_key_duplicates=duplicates,
        complete_curve_rows=complete,
        pct_rank_rows=int(restored["pct_rank_252"].notna().sum()),
        infinity_count=infinity,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(root: Path) -> list[dict[str, object]]:
    files = sorted(root.rglob("data.parquet"))
    if not files or files != sorted(root.rglob("*.parquet")):
        raise VixTermStructureBuildError(f"invalid partition topology: {root}")
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "rows": len(pd.read_parquet(path, columns=["date"])),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
    ]


def _read_state(path: Path, dataset: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VixTermStructureBuildError(f"invalid input state: {dataset}") from error
    if not isinstance(payload, dict) or payload.get("dataset") != dataset:
        raise VixTermStructureBuildError(f"input state identity differs: {dataset}")
    return payload


def _stage_json(payload: dict[str, object], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json.tmp", prefix=path.stem + "_",
            dir=path.parent, delete=False, newline="\n",
        ) as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            temporary = Path(stream.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise VixTermStructureBuildError("staged state read-back differs")
        return temporary
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _commit_pair(
    *, staged_output: Path, output_root: Path,
    staged_state: Path, output_state_path: Path,
) -> None:
    output_backup: Path | None = None
    state_backup: Path | None = None
    output_installed = False
    state_installed = False
    committed = False
    try:
        if output_root.exists():
            output_backup = output_root.parent / f".{output_root.name}.backup-{uuid4().hex}"
            output_root.replace(output_backup)
        if output_state_path.exists():
            state_backup = output_state_path.parent / f".{output_state_path.name}.backup-{uuid4().hex}"
            output_state_path.replace(state_backup)
        staged_output.replace(output_root)
        output_installed = True
        staged_state.replace(output_state_path)
        state_installed = True
        committed = True
    except Exception:
        if state_installed:
            output_state_path.unlink(missing_ok=True)
        if state_backup is not None and state_backup.exists():
            state_backup.replace(output_state_path)
        if output_installed:
            shutil.rmtree(output_root, ignore_errors=True)
        if output_backup is not None and output_backup.exists():
            output_backup.replace(output_root)
        raise
    finally:
        shutil.rmtree(staged_output, ignore_errors=True)
        staged_state.unlink(missing_ok=True)
        if committed and output_backup is not None:
            shutil.rmtree(output_backup, ignore_errors=True)
        if committed and state_backup is not None:
            state_backup.unlink(missing_ok=True)


def build_vix_term_structure_dataset(
    *,
    fred_vix_root: Path,
    fred_vix_state_path: Path,
    global_index_root: Path,
    global_index_state_path: Path,
    output_root: Path,
    output_state_path: Path,
) -> dict[str, object]:
    """Rebuild the derived dataset offline from two retained normalized inputs."""

    _read_state(fred_vix_state_path, FRED_VIX_DAILY.name)
    _read_state(global_index_state_path, GLOBAL_INDEX_PRICE_DAILY.name)
    fred_vix = read_dataset(fred_vix_root, FRED_VIX_DAILY, validate_fred)
    indices = read_dataset(global_index_root, GLOBAL_INDEX_PRICE_DAILY, validate_global_index)
    result = calculate_vix_term_structure(fred_vix, indices)
    validation = validate_vix_term_structure(fred_vix, indices, result)
    expected = result.copy()
    expected["date"] = pd.to_datetime(expected["date"]).dt.date

    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(tempfile.mkdtemp(prefix=f".{output_root.name}.stage-", dir=output_root.parent))
    staged_output = stage_parent / output_root.name

    def validator(frame: pd.DataFrame) -> None:
        if tuple(frame.columns) != OUTPUT_COLUMNS or frame.empty:
            raise VixTermStructureBuildError("staged output schema is empty or differs")
        restored = frame.copy()
        restored["date"] = pd.to_datetime(restored["date"], errors="raise").dt.date
        selected_dates = set(restored["date"])
        selected = expected.loc[expected["date"].isin(selected_dates)].reset_index(drop=True)
        try:
            pd.testing.assert_frame_equal(
                restored.reset_index(drop=True), selected,
                check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-12,
            )
        except AssertionError as error:
            raise VixTermStructureBuildError("staged output differs from full-history calculation") from error

    try:
        write_dataset_atomic(result, staged_output, US_VIX_TERM_STRUCTURE_DAILY, validator)
        output_files = _manifest(staged_output)
        payload: dict[str, object] = {
            "task_id": "OFFLINE_VIX_TERM_STRUCTURE_REBUILD",
            "status": "artifact_complete_provenance_limited",
            "dataset": DATASET,
            "dataset_version": DATASET_VERSION,
            "layer": "derived",
            "api_calls": 0,
            "formulas": FORMULAS,
            "primary_key": ["date"],
            "sort_key": ["date"],
            "partition_by": ["year"],
            "validation": asdict(validation),
            "inputs": [
                {
                    "dataset": FRED_VIX_DAILY.name,
                    "state_path": fred_vix_state_path.name,
                    "state_sha256": _sha256(fred_vix_state_path),
                    "files": _manifest(fred_vix_root),
                },
                {
                    "dataset": GLOBAL_INDEX_PRICE_DAILY.name,
                    "state_path": global_index_state_path.name,
                    "state_sha256": _sha256(global_index_state_path),
                    "files": _manifest(global_index_root),
                },
            ],
            "output_files": output_files,
            "failed": {},
            "staged": [],
        }
        staged_state = _stage_json(payload, output_state_path)
        _commit_pair(
            staged_output=staged_output, output_root=output_root,
            staged_state=staged_state, output_state_path=output_state_path,
        )
        return payload
    except Exception:
        shutil.rmtree(stage_parent, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(stage_parent, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the VIX term structure from retained data only.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    payload = build_vix_term_structure_dataset(
        fred_vix_root=root / "data/normalized/fred_vix_daily",
        fred_vix_state_path=root / "data/state/fred_vix_daily.json",
        global_index_root=root / "data/normalized/global_index_price_daily",
        global_index_state_path=root / "data/state/global_index_price_daily.json",
        output_root=root / "data/derived/us_vix_term_structure_daily",
        output_state_path=root / "data/state/us_vix_term_structure_daily.json",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
