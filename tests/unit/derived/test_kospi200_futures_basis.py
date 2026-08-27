from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_data.contracts.kospi200_futures_basis import (
    KR_KOSPI200_FUTURES_NEAREST_LISTED_DAILY,
)
from stock_data.derived.kospi200_futures_basis import (
    BASIS_AVAILABLE,
    BASIS_BLOCKED,
    DATASET,
    EXPIRY_STATUS,
    LEGACY_SEGMENT,
    LEGACY_OPERATION,
    NIGHT_SESSION,
    OFFICIAL_SEGMENT,
    OFFICIAL_OPERATION,
    REGULAR_SESSION,
    SCHEMA,
    SELECTION_RULE,
    KOSPI200FuturesBasisError,
    build_kospi200_futures_nearest_listed,
    validate,
)


def _write(root: Path, year: int, rows: list[dict]) -> Path:
    path = root / f"year={year}" / "data.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return root


def _bridge_row(
    *,
    date: str,
    segment: str,
    session: str,
    code: str,
    maturity: str,
    row_no: int | None,
    close: float | None = 101.0,
) -> dict:
    return {
        "date": pd.Timestamp(date).date(),
        "bridge_segment": segment,
        "session": session,
        "source_session_label": "source-session",
        "source_contract_code": code,
        "source_name": f"KOSPI200 F {maturity.replace('-', '')}",
        "maturity_month": maturity,
        "expiry_date": None,
        "expiry_status": EXPIRY_STATUS,
        "close": close,
        "volume": 10,
        "open_interest": 20,
        "source": "legacy_stock_investment" if segment == LEGACY_SEGMENT else "data_go_kr",
        "source_operation": LEGACY_OPERATION if segment == LEGACY_SEGMENT else OFFICIAL_OPERATION,
        "source_row_no": row_no,
    }


def _inputs(tmp_path: Path) -> dict:
    bridge = _write(
        tmp_path / "bridge",
        2019,
        [
            _bridge_row(
                date="2019-12-30", segment=LEGACY_SEGMENT, session=REGULAR_SESSION,
                code="near", maturity="2020-03", row_no=1,
            ),
            _bridge_row(
                date="2019-12-30", segment=LEGACY_SEGMENT, session=REGULAR_SESSION,
                code="far", maturity="2020-06", row_no=2, close=None,
            ),
            _bridge_row(
                date="2019-12-30", segment=LEGACY_SEGMENT, session=NIGHT_SESSION,
                code="near", maturity="2020-03", row_no=3,
            ),
        ],
    )
    _write(
        bridge,
        2020,
        [
            _bridge_row(
                date="2020-01-02", segment=OFFICIAL_SEGMENT, session=REGULAR_SESSION,
                code="near", maturity="2020-03", row_no=None,
            ),
            _bridge_row(
                date="2020-01-02", segment=OFFICIAL_SEGMENT, session=REGULAR_SESSION,
                code="far", maturity="2020-06", row_no=None, close=None,
            ),
        ],
    )
    legacy = _write(
        tmp_path / "legacy",
        2019,
        [
            {"date": pd.Timestamp("2019-12-30").date(), "source_file_row_no": 1, "spot_price": 100.0, "settlement_price": 101.5},
            {"date": pd.Timestamp("2019-12-30").date(), "source_file_row_no": 2, "spot_price": 100.0, "settlement_price": 103.0},
            {"date": pd.Timestamp("2019-12-30").date(), "source_file_row_no": 3, "spot_price": 100.0, "settlement_price": None},
        ],
    )
    official = _write(
        tmp_path / "official",
        2020,
        [
            {"date": pd.Timestamp("2020-01-02").date(), "contract": "near", "underlying_value": 99.0, "settlement_price": 101.0},
            {"date": pd.Timestamp("2020-01-02").date(), "contract": "far", "underlying_value": 99.0, "settlement_price": 104.0},
        ],
    )
    return {"bridge_root": bridge, "legacy_root": legacy, "official_root": official}


def test_builds_nearest_listed_without_expiry_or_session_inference(tmp_path):
    output = tmp_path / "output"
    state = tmp_path / "state.json"
    result = build_kospi200_futures_nearest_listed(
        **_inputs(tmp_path), output_root=output, output_state_path=state
    )
    assert result["status"] == "complete_with_limits"
    assert result["api_calls"] == 0
    assert result["validation"]["rows"] == 3
    assert result["validation"]["basis_rows"] == 2
    assert json.loads(state.read_text(encoding="utf-8")) == result

    tables = [pq.ParquetFile(path).read() for path in sorted(output.glob("year=*/data.parquet"))]
    assert all(table.schema.equals(SCHEMA, check_metadata=True) for table in tables)
    frame = pa.concat_tables(tables).to_pandas()
    assert frame["source_contract_code"].eq("near").all()
    assert frame["selection_rule"].eq(SELECTION_RULE).all()
    assert frame["expiry_date"].isna().all()
    assert not frame["contract_transition"].any()
    regular = frame["session"].eq(REGULAR_SESSION)
    assert frame.loc[regular, "basis_status"].eq(BASIS_AVAILABLE).all()
    assert sorted(frame.loc[regular, "settlement_basis"]) == [1.5, 2.0]
    assert frame.loc[~regular, "basis_status"].eq(BASIS_BLOCKED).all()
    assert frame.loc[~regular, "settlement_basis"].isna().all()


def test_contract_matches_arrow_schema():
    contract = KR_KOSPI200_FUTURES_NEAREST_LISTED_DAILY
    assert contract.name == DATASET
    assert contract.primary_key == tuple(SCHEMA.metadata[b"primary_key"].decode().split(","))
    assert contract.column_names == tuple(SCHEMA.names)
    assert tuple(column.nullable for column in contract.columns) == tuple(
        field.nullable for field in SCHEMA
    )


def test_rejects_ambiguous_same_maturity(tmp_path):
    inputs = _inputs(tmp_path)
    path = inputs["bridge_root"] / "year=2020" / "data.parquet"
    frame = pq.ParquetFile(path).read().to_pandas()
    extra = frame.iloc[[0]].copy()
    extra["source_contract_code"] = "duplicate-maturity"
    pq.write_table(pa.Table.from_pandas(pd.concat([frame, extra], ignore_index=True), preserve_index=False), path)
    official_path = inputs["official_root"] / "year=2020" / "data.parquet"
    official = pq.ParquetFile(official_path).read().to_pandas()
    extra_official = official.iloc[[0]].copy()
    extra_official["contract"] = "duplicate-maturity"
    pq.write_table(pa.Table.from_pandas(pd.concat([official, extra_official], ignore_index=True), preserve_index=False), official_path)

    with pytest.raises(KOSPI200FuturesBasisError, match="multiple contracts"):
        build_kospi200_futures_nearest_listed(
            **inputs,
            output_root=tmp_path / "output",
            output_state_path=tmp_path / "state.json",
        )


def test_rejects_null_close_when_nearest_candidate_is_selected(tmp_path):
    inputs = _inputs(tmp_path)
    path = inputs["bridge_root"] / "year=2019" / "data.parquet"
    frame = pq.ParquetFile(path).read().to_pandas()
    selected = frame["source_contract_code"].eq("near")
    frame.loc[selected, "close"] = None
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)

    with pytest.raises(KOSPI200FuturesBasisError, match="contract non-nullability"):
        build_kospi200_futures_nearest_listed(
            **inputs,
            output_root=tmp_path / "output",
            output_state_path=tmp_path / "state.json",
        )


def _built_frame(tmp_path: Path) -> pd.DataFrame:
    output = tmp_path / "validated-output"
    build_kospi200_futures_nearest_listed(
        **_inputs(tmp_path / "validated-inputs"),
        output_root=output,
        output_state_path=tmp_path / "validated-state.json",
    )
    return pa.concat_tables(
        [pq.ParquetFile(path).read() for path in sorted(output.glob("year=*/data.parquet"))]
    ).to_pandas()


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("night_settlement", "night settlement price must remain null"),
        ("bogus_input", "provider provenance mapping differs"),
        ("malformed_maturity", "maturity_month format differs"),
        ("null_source_name", "contract non-nullability"),
        ("unknown_session", "provider/session matrix differs"),
        ("past_maturity", "maturity precedes observation month"),
    ],
)
def test_validator_rejects_fail_closed_cases(tmp_path, case, error):
    frame = _built_frame(tmp_path)
    if case == "night_settlement":
        frame.loc[frame["session"].eq(NIGHT_SESSION), "settlement_price"] = 1.0
    elif case == "bogus_input":
        frame.loc[frame["bridge_segment"].eq(LEGACY_SEGMENT), "input_normalized_dataset"] = "bogus"
    elif case == "malformed_maturity":
        frame.loc[frame.index[0], "maturity_month"] = "0000-00"
    elif case == "null_source_name":
        frame.loc[frame.index[0], "source_name"] = None
    elif case == "unknown_session":
        frame.loc[frame.index[0], "session"] = "UNKNOWN"
        frame = frame.sort_values(list(SCHEMA.metadata[b"primary_key"].decode().split(","))).reset_index(drop=True)
    elif case == "past_maturity":
        frame.loc[frame.index[0], "maturity_month"] = "2019-11"
    with pytest.raises(KOSPI200FuturesBasisError, match=error):
        validate(frame)


@pytest.mark.parametrize(("column", "value"), [("source", "bogus"), ("source_operation", "bogus")])
def test_validator_rejects_segment_source_mapping(tmp_path, column, value):
    frame = _built_frame(tmp_path)
    frame.loc[frame["bridge_segment"].eq(OFFICIAL_SEGMENT), column] = value
    with pytest.raises(KOSPI200FuturesBasisError, match="provider provenance mapping differs"):
        validate(frame)
