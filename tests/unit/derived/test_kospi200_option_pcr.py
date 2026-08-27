from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_data.derived.kospi200_option_pcr import (
    DATASET,
    INPUT_COLUMNS,
    KOSPI200OptionPCRError,
    MARKET_SCOPE,
    PCR_SCHEMA,
    SCOPE,
    SOURCE,
    SOURCE_OPERATION,
    build_legacy_kospi200_option_pcr,
)


def _write_inputs(
    project: Path,
    legacy: Path,
    rows: list[dict],
    *,
    completed: list[str],
    empty: list[str],
) -> None:
    option_path = (
        project
        / "data/normalized/krx_legacy_kospi200_options_daily"
        / "year=2010/data.parquet"
    )
    option_path.parent.mkdir(parents=True)
    frame = pd.DataFrame(rows, columns=INPUT_COLUMNS)
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), option_path)
    state_path = legacy / "data/state/krx_derivatives_backfill.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "completed_options_dates": completed,
                "empty_options_dates": empty,
                "failed_dates": {},
            }
        ),
        encoding="utf-8",
    )
    pcr_path = (
        legacy
        / "data/processed/kr/derivatives/kospi200_option_pcr_daily.csv"
    )
    pcr_path.parent.mkdir(parents=True)
    aggregated: list[dict] = []
    def total(values: pd.Series):
        available = values.dropna()
        return "" if available.empty else int(available.sum())

    for date_value, daily in frame.groupby("date", sort=True):
        calls = daily.loc[daily["right_type"].eq("CALL")]
        puts = daily.loc[daily["right_type"].eq("PUT")]
        call_volume = total(calls["volume"])
        put_volume = total(puts["volume"])
        call_oi = total(calls["open_interest"])
        put_oi = total(puts["open_interest"])
        aggregated.append(
            {
                "date": pd.Timestamp(date_value).strftime("%Y%m%d"),
                "scope": SCOPE,
                "market_scope": MARKET_SCOPE,
                "call_volume": call_volume,
                "put_volume": put_volume,
                "volume_pcr": put_volume / call_volume
                if isinstance(call_volume, int) and call_volume
                and isinstance(put_volume, int)
                else "",
                "call_open_interest": call_oi,
                "put_open_interest": put_oi,
                "open_interest_pcr": put_oi / call_oi
                if isinstance(call_oi, int) and call_oi and isinstance(put_oi, int)
                else "",
                "call_rows": len(calls),
                "put_rows": len(puts),
                "unclassified_rows": 0,
                "source": SOURCE_OPERATION,
            }
        )
    with pcr_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(aggregated[0]))
        writer.writeheader()
        writer.writerows(aggregated)
    c001_state = project / "data/state/legacy_kospi200_derivatives_2010_2019.json"
    c001_state.parent.mkdir(parents=True)
    c001_state.write_text('{"status":"complete"}', encoding="utf-8")


def _row(date_value: str, right: str, volume, open_interest) -> dict:
    return {
        "date": date_value,
        "right_type": right,
        "volume": volume,
        "open_interest": open_interest,
        "source": SOURCE,
        "source_operation": SOURCE_OPERATION,
    }


def _build(project: Path, legacy: Path) -> dict:
    return build_legacy_kospi200_option_pcr(
        project_root=project,
        legacy_calendar_state_path=(
            legacy / "data/state/krx_derivatives_backfill.json"
        ),
        legacy_pcr_path=(
            legacy
            / "data/processed/kr/derivatives/kospi200_option_pcr_daily.csv"
        ),
        start="20100101",
        end="20101231",
    )


def test_builds_derived_pcr_without_deduplication_and_preserves_empty(tmp_path):
    project, legacy = tmp_path / "rev1", tmp_path / "legacy"
    _write_inputs(
        project,
        legacy,
        [
            _row("2010-01-04", "CALL", 100, 10),
            _row("2010-01-04", "CALL", 200, 20),
            _row("2010-01-04", "PUT", 150, 15),
        ],
        completed=["20100104", "20100105"],
        empty=["20100105"],
    )

    result = _build(project, legacy)

    assert result["api_calls"] == 0
    assert result["validation"]["rows"] == 2
    assert result["validation"]["observed_rows"] == 1
    assert result["validation"]["valid_empty_rows"] == 1
    assert result["parity"]["observed_rows_compared"] == 1
    output = pq.ParquetFile(
        project / f"data/derived/{DATASET}/year=2010/data.parquet"
    ).read()
    assert output.schema.equals(PCR_SCHEMA, check_metadata=True)
    saved = output.to_pandas()
    assert saved.loc[0, "call_rows"] == 2
    assert saved.loc[0, "call_volume"] == 300
    assert saved.loc[0, "volume_pcr"] == 0.5
    assert saved.loc[0, "open_interest_pcr"] == 0.5
    assert saved.loc[1, "observation_status"] == "valid_empty"
    assert saved.loc[1, "call_volume"] == 0
    assert pd.isna(saved.loc[1, "volume_pcr"])
    assert saved.loc[0, "source"] == "legacy_stock_investment"
    assert saved.loc[0, "source_operation"] == "krx_opt_bydd_trd"


def test_zero_denominator_is_null_and_missing_source_is_not_coerced_to_zero(
    tmp_path,
):
    project, legacy = tmp_path / "rev1", tmp_path / "legacy"
    _write_inputs(
        project,
        legacy,
        [
            _row("2010-01-04", "CALL", 0, None),
            _row("2010-01-04", "PUT", 7, 9),
        ],
        completed=["20100104"],
        empty=[],
    )
    result = _build(project, legacy)
    assert result["validation"]["rows"] == 1
    saved = pq.ParquetFile(
        project / f"data/derived/{DATASET}/year=2010/data.parquet"
    ).read().to_pandas().iloc[0]
    assert saved["call_volume"] == 0
    assert pd.isna(saved["volume_pcr"])
    assert pd.isna(saved["call_open_interest"])
    assert pd.isna(saved["open_interest_pcr"])


def test_calendar_mismatch_preserves_existing_dataset(tmp_path):
    project, legacy = tmp_path / "rev1", tmp_path / "legacy"
    _write_inputs(
        project,
        legacy,
        [_row("2010-01-04", "CALL", 1, 1), _row("2010-01-04", "PUT", 1, 1)],
        completed=["20100105"],
        empty=["20100105"],
    )
    existing = project / f"data/derived/{DATASET}/marker"
    existing.parent.mkdir(parents=True)
    existing.write_text("preserved", encoding="utf-8")
    with pytest.raises(KOSPI200OptionPCRError, match="calendar"):
        _build(project, legacy)
    assert existing.read_text(encoding="utf-8") == "preserved"
    assert not (
        project / "data/state/legacy_kospi200_option_pcr_2010_2019.json"
    ).exists()


@pytest.mark.parametrize("right", ["call", "UNKNOWN", ""])
def test_unverified_side_is_rejected(tmp_path, right):
    project, legacy = tmp_path / "rev1", tmp_path / "legacy"
    _write_inputs(
        project,
        legacy,
        [_row("2010-01-04", right, 1, 1)],
        completed=["20100104"],
        empty=[],
    )
    with pytest.raises(KOSPI200OptionPCRError, match="unverified option side"):
        _build(project, legacy)
