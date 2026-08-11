from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import stock_data.derived.kospi200_option_pcr_modern as modern_module
from stock_data.derived.kospi200_option_pcr import (
    MARKET_SCOPE,
    PCR_SCHEMA,
    SCOPE,
    KOSPI200OptionPCRError,
)
from stock_data.derived.kospi200_option_pcr_modern import (
    INPUT_DATASET,
    SOURCE,
    SOURCE_OPERATION,
    build_modern_kospi200_option_pcr,
)


def _input_row(date_value: str, contract: str, side: str, volume, oi) -> dict:
    return {
        "date": pd.Timestamp(date_value).date(),
        "contract": contract,
        "call_put": side,
        "volume": volume,
        "open_interest": oi,
        "source": SOURCE,
        "source_operation": SOURCE_OPERATION,
    }


def _write_inputs(
    root: Path,
    rows: list[dict],
    *,
    completed: list[str],
    empty: list[str],
) -> tuple[Path, Path]:
    input_root = root / INPUT_DATASET
    path = input_root / "year=2020/data.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    state_path = root / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "dataset": INPUT_DATASET,
                "completed_partitions": completed,
                "valid_empty_partitions": empty,
                "failed_partitions": {},
                "staged_partitions": [],
            }
        ),
        encoding="utf-8",
    )
    return input_root, state_path


def _write_prior(root: Path) -> Path:
    prior = root / "prior"
    path = prior / "year=2019/data.parquet"
    path.parent.mkdir(parents=True)
    frame = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2019-12-31").date(),
                "scope": SCOPE,
                "market_scope": MARKET_SCOPE,
                "observation_status": "valid_empty",
                "call_volume": 0,
                "put_volume": 0,
                "volume_pcr": None,
                "call_open_interest": 0,
                "put_open_interest": 0,
                "open_interest_pcr": None,
                "call_rows": 0,
                "put_rows": 0,
                "unclassified_rows": 0,
                "source": "legacy_stock_investment",
                "source_operation": "krx_opt_bydd_trd",
                "input_dataset": "krx_legacy_kospi200_options_daily",
            }
        ],
        columns=PCR_SCHEMA.names,
    )
    table = pa.Table.from_pandas(
        frame, schema=PCR_SCHEMA, preserve_index=False
    ).replace_schema_metadata(PCR_SCHEMA.metadata)
    pq.write_table(table, path)
    return prior


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(tmp_path: Path, rows: list[dict], completed: list[str], empty: list[str]):
    input_root, state_path = _write_inputs(
        tmp_path / "input", rows, completed=completed, empty=empty
    )
    output_root = tmp_path / "output"
    output_state = tmp_path / "output-state.json"
    result = build_modern_kospi200_option_pcr(
        input_root=input_root,
        input_state_path=state_path,
        output_root=output_root,
        output_state_path=output_state,
        prior_derived_root=_write_prior(tmp_path),
        start="20200101",
        end="20200104",
    )
    return result, output_root, output_state


def test_builds_contract_compatible_modern_pcr_and_preserves_valid_empty(tmp_path):
    rows = [
        _input_row("2020-01-02", "C1", "CALL", 100, 10),
        _input_row("2020-01-02", "C2", "CALL", 200, 20),
        _input_row("2020-01-02", "P1", "PUT", 150, 15),
        _input_row("2020-01-03", "C3", "CALL", 0, None),
        _input_row("2020-01-03", "P2", "PUT", 7, 9),
    ]
    result, output_root, output_state = _build(
        tmp_path, rows, ["20200102", "20200103"], ["20200104"]
    )

    assert result["api_calls"] == 0
    assert result["validation"]["rows"] == 3
    assert result["validation"]["observed_rows"] == 2
    assert result["validation"]["valid_empty_rows"] == 1
    assert result["boundary"] == {
        "prior_last_date": "2019-12-31",
        "prior_last_observation_status": "valid_empty",
        "modern_first_date": "2020-01-02",
        "calendar_day_gap": 2,
    }
    assert result["combined_validation"]["rows"] == 4
    assert result["combined_validation"]["prior_rows"] == 1
    assert result["combined_validation"]["modern_rows"] == 3
    prior_source = tmp_path / "prior/year=2019/data.parquet"
    prior_output = output_root / "year=2019/data.parquet"
    assert _sha256(prior_output) == _sha256(prior_source)
    assert result["preserved_prior_files"][0]["sha256"] == _sha256(prior_source)
    table = pq.ParquetFile(output_root / "year=2020/data.parquet").read()
    assert table.schema.equals(PCR_SCHEMA, check_metadata=True)
    saved = table.to_pandas()
    assert list(saved["observation_status"]) == ["observed", "observed", "valid_empty"]
    observed = saved.loc[saved["date"].eq(pd.Timestamp("2020-01-02").date())].iloc[0]
    assert observed["call_volume"] == 300
    assert observed["put_volume"] == 150
    assert observed["volume_pcr"] == 0.5
    assert observed["call_open_interest"] == 30
    assert observed["put_open_interest"] == 15
    assert observed["open_interest_pcr"] == 0.5
    assert observed["call_rows"] == 2
    assert observed["put_rows"] == 1
    zero = saved.loc[saved["date"].eq(pd.Timestamp("2020-01-03").date())].iloc[0]
    assert zero["call_volume"] == 0
    assert pd.isna(zero["volume_pcr"])
    assert pd.isna(zero["call_open_interest"])
    assert pd.isna(zero["open_interest_pcr"])
    assert saved["source"].eq(SOURCE).all()
    assert saved["source_operation"].eq(SOURCE_OPERATION).all()
    assert saved["input_dataset"].eq(INPUT_DATASET).all()
    assert json.loads(output_state.read_text(encoding="utf-8")) == result


def test_checkpoint_mismatch_preserves_existing_output(tmp_path):
    input_root, state_path = _write_inputs(
        tmp_path / "input",
        [
            _input_row("2020-01-02", "C1", "CALL", 1, 1),
            _input_row("2020-01-02", "P1", "PUT", 1, 1),
        ],
        completed=["20200103"],
        empty=[],
    )
    output_root = _write_prior(tmp_path)
    prior_path = output_root / "year=2019/data.parquet"
    prior_hash = _sha256(prior_path)

    with pytest.raises(KOSPI200OptionPCRError, match="checkpoint differs"):
        build_modern_kospi200_option_pcr(
            input_root=input_root,
            input_state_path=state_path,
            output_root=output_root,
            output_state_path=tmp_path / "state.json",
            prior_derived_root=output_root,
            start="20200101",
            end="20200103",
        )

    assert _sha256(prior_path) == prior_hash
    assert not (output_root / "year=2020").exists()
    assert not (tmp_path / "state.json").exists()


def test_same_prior_and_output_root_preserves_prior_bytes(tmp_path):
    input_root, state_path = _write_inputs(
        tmp_path / "input",
        [
            _input_row("2020-01-02", "C1", "CALL", 10, 5),
            _input_row("2020-01-02", "P1", "PUT", 20, 15),
        ],
        completed=["20200102"],
        empty=[],
    )
    output_root = _write_prior(tmp_path)
    prior_path = output_root / "year=2019/data.parquet"
    prior_hash = _sha256(prior_path)
    result = build_modern_kospi200_option_pcr(
        input_root=input_root,
        input_state_path=state_path,
        output_root=output_root,
        output_state_path=tmp_path / "state.json",
        prior_derived_root=output_root,
        start="20200101",
        end="20200102",
    )

    assert _sha256(prior_path) == prior_hash
    assert (output_root / "year=2020/data.parquet").exists()
    assert result["combined_validation"]["rows"] == 2


def test_staging_failure_preserves_same_root_prior(tmp_path, monkeypatch):
    input_root, state_path = _write_inputs(
        tmp_path / "input",
        [
            _input_row("2020-01-02", "C1", "CALL", 10, 5),
            _input_row("2020-01-02", "P1", "PUT", 20, 15),
        ],
        completed=["20200102"],
        empty=[],
    )
    output_root = _write_prior(tmp_path)
    prior_path = output_root / "year=2019/data.parquet"
    prior_hash = _sha256(prior_path)

    def fail_validation(*args, **kwargs):
        raise KOSPI200OptionPCRError("injected staged validation failure")

    monkeypatch.setattr(modern_module, "_validate_combined", fail_validation)
    with pytest.raises(KOSPI200OptionPCRError, match="injected staged"):
        build_modern_kospi200_option_pcr(
            input_root=input_root,
            input_state_path=state_path,
            output_root=output_root,
            output_state_path=tmp_path / "state.json",
            prior_derived_root=output_root,
            start="20200101",
            end="20200102",
        )
    assert _sha256(prior_path) == prior_hash
    assert not (output_root / "year=2020").exists()
    assert not (tmp_path / "state.json").exists()


@pytest.mark.parametrize("side", ["call", "UNKNOWN", ""])
def test_unverified_side_is_rejected(tmp_path, side):
    input_root, state_path = _write_inputs(
        tmp_path / "input",
        [_input_row("2020-01-02", "X1", side, 1, 1)],
        completed=["20200102"],
        empty=[],
    )
    with pytest.raises(KOSPI200OptionPCRError, match="unverified modern option side"):
        build_modern_kospi200_option_pcr(
            input_root=input_root,
            input_state_path=state_path,
            output_root=tmp_path / "output",
            output_state_path=tmp_path / "state.json",
            prior_derived_root=_write_prior(tmp_path),
            start="20200101",
            end="20200102",
        )
