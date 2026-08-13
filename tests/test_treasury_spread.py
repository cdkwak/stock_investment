from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_data.derived.treasury_spread import (
    SCHEMA,
    TreasurySpreadBuildError,
    build_treasury_spread_dataset,
    calculate_treasury_spreads,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_source(root: Path, frame: pd.DataFrame) -> None:
    for year, partition in frame.groupby(pd.to_datetime(frame["date"]).dt.year):
        path = root / f"year={year}" / "data.parquet"
        path.parent.mkdir(parents=True)
        table = pa.Table.from_pandas(
            partition.assign(date=pd.to_datetime(partition["date"]).dt.date),
            schema=pa.schema(
                (
                    pa.field("date", pa.date32()),
                    pa.field("dgs2", pa.float64()),
                    pa.field("dgs10", pa.float64()),
                    pa.field("dgs30", pa.float64()),
                )
            ),
            preserve_index=False,
        )
        pq.write_table(table, path)


def test_spreads_are_derived_without_filling_missing_values() -> None:
    source = pd.DataFrame(
        {
            "date": ["2026-08-03", "2026-08-04"],
            "dgs2": [3.0, None],
            "dgs10": [4.0, 4.1],
            "dgs30": [4.5, 4.6],
        }
    )
    result = calculate_treasury_spreads(source)
    assert result.spread_10y_2y.iloc[0] == 1.0
    assert pd.isna(result.spread_10y_2y.iloc[1])
    assert pd.isna(result.spread_30y_2y.iloc[1])


def test_builder_writes_atomic_contract_output_and_exact_lineage(tmp_path: Path) -> None:
    input_root = tmp_path / "normalized/fred_treasury_yield_daily"
    source = pd.DataFrame(
        {
            "date": ["2025-12-31", "2026-01-02", "2026-01-05"],
            "dgs2": [3.0, None, 3.2],
            "dgs10": [4.0, 4.1, None],
            "dgs30": [4.5, 4.6, 4.7],
        }
    )
    _write_source(input_root, source)
    input_state = tmp_path / "state/fred_treasury_yield_daily.json"
    input_state.parent.mkdir()
    input_state.write_text(
        '{"dataset":"fred_treasury_yield_daily","status":"complete"}\n',
        encoding="utf-8",
    )
    output_root = tmp_path / "derived/us_treasury_spread_daily"
    output_state = tmp_path / "state/us_treasury_spread_daily.json"

    payload = build_treasury_spread_dataset(
        input_root=input_root,
        input_state_path=input_state,
        output_root=output_root,
        output_state_path=output_state,
    )

    assert payload["status"] == "artifact_complete_provenance_limited"
    assert payload["api_calls"] == 0
    assert payload["validation"]["rows"] == 3
    assert payload["validation"]["output_null_counts"] == {
        "spread_10y_2y": 2,
        "spread_30y_2y": 1,
    }
    assert payload["input"]["state_sha256"] == _sha256(input_state)
    assert [item["path"] for item in payload["input"]["files"]] == [
        "year=2025/data.parquet",
        "year=2026/data.parquet",
    ]
    assert len(payload["output_files"]) == 2
    assert json.loads(output_state.read_text(encoding="utf-8")) == payload
    for item in payload["output_files"]:
        path = output_root / item["path"]
        assert item["sha256"] == _sha256(path)
        assert pq.ParquetFile(path).schema_arrow.equals(SCHEMA, check_metadata=True)


def test_builder_preserves_existing_output_on_invalid_source(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    _write_source(
        input_root,
        pd.DataFrame(
            {
                "date": ["2026-01-02", "2026-01-02"],
                "dgs2": [3.0, 3.1],
                "dgs10": [4.0, 4.1],
                "dgs30": [4.5, 4.6],
            }
        ),
    )
    input_state = tmp_path / "input_state.json"
    input_state.write_text(
        '{"dataset":"fred_treasury_yield_daily"}', encoding="utf-8"
    )
    output_root = tmp_path / "output"
    output_root.mkdir()
    marker = output_root / "valid.marker"
    marker.write_bytes(b"keep")
    output_state = tmp_path / "output_state.json"

    with pytest.raises(TreasurySpreadBuildError, match="duplicates"):
        build_treasury_spread_dataset(
            input_root=input_root,
            input_state_path=input_state,
            output_root=output_root,
            output_state_path=output_state,
        )

    assert marker.read_bytes() == b"keep"
    assert not output_state.exists()


def test_builder_rejects_unexpected_input_parquet_layout(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    _write_source(
        input_root,
        pd.DataFrame(
            {
                "date": ["2026-01-02"],
                "dgs2": [3.0],
                "dgs10": [4.0],
                "dgs30": [4.5],
            }
        ),
    )
    pq.write_table(pa.table({"x": [1]}), input_root / "unexpected.parquet")
    input_state = tmp_path / "input_state.json"
    input_state.write_text(
        '{"dataset":"fred_treasury_yield_daily"}', encoding="utf-8"
    )

    with pytest.raises(TreasurySpreadBuildError, match="only year"):
        build_treasury_spread_dataset(
            input_root=input_root,
            input_state_path=input_state,
            output_root=tmp_path / "output",
            output_state_path=tmp_path / "output_state.json",
        )


def test_state_commit_failure_restores_previous_output_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_root = tmp_path / "input"
    _write_source(
        input_root,
        pd.DataFrame(
            {
                "date": ["2026-01-02"],
                "dgs2": [3.0],
                "dgs10": [4.0],
                "dgs30": [4.5],
            }
        ),
    )
    input_state = tmp_path / "input_state.json"
    input_state.write_text(
        '{"dataset":"fred_treasury_yield_daily"}', encoding="utf-8"
    )
    output_root = tmp_path / "output"
    output_root.mkdir()
    marker = output_root / "valid.marker"
    marker.write_bytes(b"previous-output")
    output_state = tmp_path / "output_state.json"
    output_state.write_bytes(b"previous-state")
    original_replace = Path.replace

    def fail_state_install(self: Path, target: Path) -> Path:
        if self.name.endswith(".json.tmp") and Path(target) == output_state:
            raise OSError("simulated state install failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_state_install)
    with pytest.raises(OSError, match="simulated"):
        build_treasury_spread_dataset(
            input_root=input_root,
            input_state_path=input_state,
            output_root=output_root,
            output_state_path=output_state,
        )

    assert marker.read_bytes() == b"previous-output"
    assert output_state.read_bytes() == b"previous-state"
