from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Iterable, Mapping

import pandas as pd

from stock_data.contracts.kr_index_fundamental_daily import (
    KR_INDEX_FUNDAMENTAL_DAILY,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_index_fundamental_daily import (
    validate_kr_index_fundamental_daily,
)


_IDENTITIES = {"kospi": ("1001", "KOSPI"), "kosdaq": ("2001", "KOSDAQ")}
_ENTRY = re.compile(r"index_(kospi|kosdaq)_history_(\d{2})")
_MISSING_TOKENS = {"", "-", "--"}
_REQUIRED_FIELDS = {
    "TRD_DD", "CLSPRC_IDX", "WT_PER", "WT_STKPRC_NETASST_RTO", "DIV_YD"
}


@dataclass(frozen=True)
class PreparedIndexFundamentals:
    dataframe: pd.DataFrame
    report: dict[str, object]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _provider_number(value: object, *, field: str, nullable: bool) -> float | None:
    token = "" if value is None else str(value).strip()
    if token in _MISSING_TOKENS:
        if nullable:
            return None
        raise ValueError(f"{field} must not use a provider missing token")
    try:
        number = float(token.replace(",", ""))
    except ValueError as error:
        raise ValueError(f"{field} contains an invalid provider number") from error
    if not math.isfinite(number):
        raise ValueError(f"{field} contains a non-finite provider number")
    return number


def _canonical_records(dataframe: pd.DataFrame) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for row in dataframe.to_dict(orient="records"):
        records.append(
            {
                key: None if pd.isna(value) else value
                for key, value in row.items()
            }
        )
    return records


def _semantic_digest(dataframe: pd.DataFrame) -> str:
    encoded = json.dumps(
        _canonical_records(dataframe),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_index_fundamental_response(
    body: bytes, *, index_code: str, market: str,
) -> pd.DataFrame:
    """Normalize one immutable MDCSTAT00702 response and bind its raw digest."""
    expected_market = {"1001": "KOSPI", "2001": "KOSDAQ"}.get(index_code)
    if expected_market != market:
        raise ValueError("index_code and market are inconsistent")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("KRX response is not valid JSON") from error
    if not isinstance(payload, dict) or payload.get("_error_code") or payload.get("error"):
        raise ValueError("KRX response contains a source error")
    output = payload.get("output")
    if not isinstance(output, list):
        raise ValueError("KRX response output is missing")
    digest = hashlib.sha256(body).hexdigest()
    rows: list[dict[str, object]] = []
    for source_row in output:
        if not isinstance(source_row, dict) or not _REQUIRED_FIELDS.issubset(source_row):
            raise ValueError("KRX response schema differs")
        try:
            observed = datetime.strptime(str(source_row["TRD_DD"]), "%Y/%m/%d")
        except ValueError as error:
            raise ValueError("KRX response has an invalid date") from error
        rows.append({
            "date": observed.strftime("%Y-%m-%d"),
            "index_code": index_code,
            "market": market,
            "close": _provider_number(
                source_row["CLSPRC_IDX"], field="CLSPRC_IDX", nullable=False,
            ),
            "weighted_per": _provider_number(
                source_row["WT_PER"], field="WT_PER", nullable=True,
            ),
            "weighted_pbr": _provider_number(
                source_row["WT_STKPRC_NETASST_RTO"],
                field="WT_STKPRC_NETASST_RTO", nullable=True,
            ),
            "dividend_yield": _provider_number(
                source_row["DIV_YD"], field="DIV_YD", nullable=True,
            ),
            "source": "KRX_MDCSTAT00702",
            "source_response_sha256": digest,
        })
    frame = pd.DataFrame(rows, columns=KR_INDEX_FUNDAMENTAL_DAILY.column_names)
    if not frame.empty:
        frame = frame.sort_values(list(KR_INDEX_FUNDAMENTAL_DAILY.sort_key), kind="stable").reset_index(drop=True)
        validate_kr_index_fundamental_daily(frame)
    return frame


def merge_index_fundamental_frames(
    existing: pd.DataFrame, incoming_frames: Iterable[pd.DataFrame],
) -> pd.DataFrame:
    """Merge hash-bound rows without replacing a conflicting retained value."""
    validate_kr_index_fundamental_daily(existing)
    combined = existing.copy()
    keys = list(KR_INDEX_FUNDAMENTAL_DAILY.primary_key)
    value_columns = list(KR_INDEX_FUNDAMENTAL_DAILY.column_names)
    for incoming in incoming_frames:
        if incoming.empty:
            continue
        validate_kr_index_fundamental_daily(incoming)
        retained = combined.set_index(keys, drop=False)
        for row in incoming.to_dict(orient="records"):
            key = (row["date"], row["index_code"])
            if key in retained.index:
                current = retained.loc[key]
                if isinstance(current, pd.DataFrame):
                    raise ValueError("retained index fundamentals contain duplicate keys")
                left = [None if pd.isna(current[column]) else current[column] for column in value_columns]
                right = [None if pd.isna(row[column]) else row[column] for column in value_columns]
                if left != right:
                    raise ValueError(f"incoming row conflicts with retained key {key}")
                continue
            combined = pd.concat([
                combined,
                pd.DataFrame([row], columns=KR_INDEX_FUNDAMENTAL_DAILY.column_names),
            ], ignore_index=True)
    combined = combined.sort_values(list(KR_INDEX_FUNDAMENTAL_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_kr_index_fundamental_daily(combined)
    return combined


def _selected_entries(checkpoint: Mapping[str, object]) -> list[tuple[str, dict]]:
    if checkpoint.get("status") != "COMPLETE":
        raise ValueError("retained diagnostic checkpoint is not COMPLETE")
    completed = checkpoint.get("completed")
    if not isinstance(completed, dict):
        raise ValueError("retained diagnostic checkpoint completed map is invalid")
    selected: list[tuple[str, dict]] = []
    sequences: dict[str, list[int]] = {key: [] for key in _IDENTITIES}
    for name, raw_entry in completed.items():
        match = _ENTRY.fullmatch(str(name))
        if not match:
            continue
        if not isinstance(raw_entry, dict):
            raise ValueError(f"checkpoint entry {name} is invalid")
        if raw_entry.get("classification") != "SUCCESS":
            raise ValueError(f"checkpoint entry {name} is not SUCCESS")
        sequences[match.group(1)].append(int(match.group(2)))
        selected.append((str(name), raw_entry))
    for identity, values in sequences.items():
        ordered = sorted(values)
        if not ordered or ordered != list(range(1, max(ordered) + 1)):
            raise ValueError(f"{identity} retained response sequence is incomplete")
    return sorted(selected)


def prepare_retained_index_fundamentals(
    retained_run_root: Path,
) -> PreparedIndexFundamentals:
    retained_run_root = retained_run_root.resolve()
    checkpoint_path = retained_run_root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    inputs: list[dict[str, object]] = []

    for name, entry in _selected_entries(checkpoint):
        match = _ENTRY.fullmatch(name)
        assert match is not None
        index_code, market = _IDENTITIES[match.group(1)]
        body_file = entry.get("body_file")
        if not isinstance(body_file, str) or Path(body_file).name != body_file:
            raise ValueError(f"checkpoint entry {name} body_file is unsafe")
        body_path = retained_run_root / body_file
        body_sha256 = _sha256(body_path)
        if body_sha256 != entry.get("body_sha256"):
            raise ValueError(f"checkpoint entry {name} response SHA-256 differs")
        payload = json.loads(body_path.read_text(encoding="utf-8"))
        output = payload.get("output") if isinstance(payload, dict) else None
        if not isinstance(output, list) or len(output) != entry.get("rows"):
            raise ValueError(f"checkpoint entry {name} row count differs")
        for source_row in output:
            if not isinstance(source_row, dict) or not _REQUIRED_FIELDS.issubset(source_row):
                raise ValueError(f"checkpoint entry {name} response schema differs")
            try:
                date = datetime.strptime(
                    str(source_row["TRD_DD"]), "%Y/%m/%d"
                ).strftime("%Y-%m-%d")
            except ValueError as error:
                raise ValueError(f"checkpoint entry {name} has an invalid date") from error
            rows.append(
                {
                    "date": date,
                    "index_code": index_code,
                    "market": market,
                    "close": _provider_number(
                        source_row["CLSPRC_IDX"], field="CLSPRC_IDX", nullable=False
                    ),
                    "weighted_per": _provider_number(
                        source_row["WT_PER"], field="WT_PER", nullable=True
                    ),
                    "weighted_pbr": _provider_number(
                        source_row["WT_STKPRC_NETASST_RTO"],
                        field="WT_STKPRC_NETASST_RTO",
                        nullable=True,
                    ),
                    "dividend_yield": _provider_number(
                        source_row["DIV_YD"], field="DIV_YD", nullable=True
                    ),
                    "source": "KRX_MDCSTAT00702",
                    "source_response_sha256": body_sha256,
                }
            )
        inputs.append(
            {
                "checkpoint_entry": name,
                "body_file": body_file,
                "body_sha256": body_sha256,
                "rows": len(output),
                "index_code": index_code,
                "market": market,
            }
        )

    dataframe = pd.DataFrame(rows, columns=KR_INDEX_FUNDAMENTAL_DAILY.column_names)
    dataframe = dataframe.sort_values(
        list(KR_INDEX_FUNDAMENTAL_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_kr_index_fundamental_daily(dataframe)
    coverage = {
        market: {
            "rows": int(len(group)),
            "minimum_date": str(group["date"].min()),
            "maximum_date": str(group["date"].max()),
        }
        for market, group in dataframe.groupby("market", sort=True)
    }
    report: dict[str, object] = {
        "dataset": KR_INDEX_FUNDAMENTAL_DAILY.name,
        "mode": "API_ZERO_RETAINED_DRY_RUN",
        "checkpoint_status": checkpoint["status"],
        "checkpoint_run_id": checkpoint.get("run_id"),
        "selected_response_files": len(inputs),
        "rows": len(dataframe),
        "coverage": coverage,
        "semantic_sha256": _semantic_digest(dataframe),
        "inputs": inputs,
    }
    return PreparedIndexFundamentals(dataframe=dataframe, report=report)


def dry_run_retained_index_fundamentals(retained_run_root: Path) -> dict[str, object]:
    return prepare_retained_index_fundamentals(retained_run_root).report


def stage_retained_index_fundamentals(
    *, retained_run_root: Path, staging_root: Path
) -> dict[str, object]:
    retained_run_root = retained_run_root.resolve()
    staging_root = staging_root.resolve()
    if staging_root == retained_run_root or retained_run_root in staging_root.parents:
        raise ValueError("staging_root must not mutate retained Raw")
    prepared = prepare_retained_index_fundamentals(retained_run_root)
    output_root = staging_root / KR_INDEX_FUNDAMENTAL_DAILY.name
    write_dataset_atomic(
        prepared.dataframe,
        output_root,
        KR_INDEX_FUNDAMENTAL_DAILY,
        validate_kr_index_fundamental_daily,
    )
    restored = read_dataset(
        output_root,
        KR_INDEX_FUNDAMENTAL_DAILY,
        validate_kr_index_fundamental_daily,
    )
    if _semantic_digest(restored) != prepared.report["semantic_sha256"]:
        raise RuntimeError("staged Parquet semantic digest differs from retained input")
    report = dict(prepared.report)
    report.update(
        {
            "mode": "API_ZERO_RETAINED_STAGING",
            "output_root": str(output_root),
            "output_files": [
                {
                    "path": path.relative_to(staging_root).as_posix(),
                    "rows": len(pd.read_parquet(path)),
                    "sha256": _sha256(path),
                }
                for path in sorted(output_root.rglob("data.parquet"))
            ],
        }
    )
    return report


__all__ = [
    "PreparedIndexFundamentals",
    "dry_run_retained_index_fundamentals",
    "prepare_retained_index_fundamentals",
    "merge_index_fundamental_frames",
    "normalize_index_fundamental_response",
    "stage_retained_index_fundamentals",
]
