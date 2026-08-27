from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_data.contracts.registry import CONTRACTS
from stock_data.published.kospi200_derivatives_bridge import (
    CONTRACT_UNIT,
    FUTURES_DATASET,
    FUTURES_SCHEMA,
    LEGACY_SEGMENT,
    NIGHT_SESSION,
    OFFICIAL_SEGMENT,
    OPTIONS_DATASET,
    OPTIONS_SCHEMA,
    PREDICTIVE_USE_STATUS,
    REGULAR_SESSION,
    UNSPECIFIED_SESSION,
    UNVERIFIED_UNIT,
    KOSPI200DerivativesBridgeError,
    build_kospi200_derivatives_bridge,
)


def _write(root: Path, year: int, rows: list[dict]) -> Path:
    path = root / f"year={year}/data.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    return root


def _legacy_future(name: str, market: str, contract: str, row_no: int) -> dict:
    return {
        "date": pd.Timestamp("2019-12-30").date(),
        "product_name": "코스피200 선물",
        "market_name": market,
        "contract": contract,
        "name": name,
        "open": 290.0,
        "high": 296.0,
        "low": 289.0,
        "close": 295.0,
        "volume": 10,
        "open_interest": 20,
        "source": "legacy_stock_investment",
        "source_operation": "krx_fut_bydd_trd",
        "source_file_row_no": row_no,
    }


def _official_future(*, invalid_ohlc: bool = False) -> dict:
    return {
        "date": pd.Timestamp("2020-01-02").date(),
        "underlying": "KOSPI200",
        "contract": "101Q3000",
        "isin": "KR4101Q30005",
        "name": "코스피200 F 202003",
        "product_category": "파생 선물 코스피200 (주간)",
        "maturity_month": "2020-03",
        "open": 294.0,
        "high": 293.0 if invalid_ohlc else 296.0,
        "low": 290.0,
        "close": 291.0,
        "volume": 30,
        "open_interest": 40,
        "source": "data_go_kr",
        "source_operation": "GetDerivativeProductInfoService/getStockFuturesPriceInfo",
    }


def _legacy_option() -> dict:
    return {
        "date": pd.Timestamp("2019-12-30").date(),
        "product_name": "코스피200 옵션",
        "right_type": "CALL",
        "contract": "201Q1212",
        "name": "코스피200 C 202001 212.5 (정규)",
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "volume": 0,
        "open_interest": 39,
        "source": "legacy_stock_investment",
        "source_operation": "krx_opt_bydd_trd",
        "source_file_row_no": 1,
        "source_row_no": 1,
    }


def _official_option() -> dict:
    return {
        "date": pd.Timestamp("2020-01-02").date(),
        "underlying": "KOSPI200",
        "contract": "201Q1212",
        "isin": "KR4201Q12126",
        "name": "코스피200 C 202001   212.5",
        "product_category": "파생 옵션 코스피200",
        "maturity_month": "2020-01",
        "call_put": "CALL",
        "strike": 212.5,
        "open": 0.0,
        "high": 0.0,
        "low": 0.0,
        "close": 0.0,
        "volume": 0,
        "open_interest": 39,
        "source": "data_go_kr",
        "source_operation": "GetDerivativeProductInfoService/getOptionsPriceInfo",
    }


def _inputs(tmp_path: Path, *, invalid_official_future: bool = False) -> dict:
    return {
        "legacy_futures_root": _write(
            tmp_path / "legacy-futures",
            2019,
            [
                _legacy_future(
                    "코스피200 F 202003 (주간)", "정규", "101Q3000", 1
                ),
                _legacy_future(
                    "코스피200 F 202003 (야간)", "야간", "101Q3000", 2
                ),
                _legacy_future(
                    "코스피200 SP 2003-2006 (주간)", "정규", "401Q3Q6S", 3
                ),
            ],
        ),
        "official_futures_root": _write(
            tmp_path / "official-futures",
            2020,
            [_official_future(invalid_ohlc=invalid_official_future)],
        ),
        "legacy_options_root": _write(
            tmp_path / "legacy-options", 2019, [_legacy_option()]
        ),
        "official_options_root": _write(
            tmp_path / "official-options", 2020, [_official_option()]
        ),
    }


def test_builds_session_safe_provider_boundary_unions(tmp_path):
    output = tmp_path / "published-bridge"
    state_path = tmp_path / "state.json"
    result = build_kospi200_derivatives_bridge(
        **_inputs(tmp_path),
        output_bundle_root=output,
        output_state_path=state_path,
    )

    assert result["status"] == "source_found_with_limits"
    assert result["api_calls"] == 0
    future_state = result["datasets"][FUTURES_DATASET]
    option_state = result["datasets"][OPTIONS_DATASET]
    assert future_state["validation"]["rows"] == 3
    assert future_state["excluded_legacy_spread_rows"] == 1
    assert future_state["boundary"]["contract_code_intersection"] == 1
    assert option_state["validation"]["rows"] == 2
    assert option_state["boundary"]["contract_code_intersection"] == 1

    future_tables = [
        pq.ParquetFile(path).read()
        for path in sorted((output / FUTURES_DATASET).glob("year=*/data.parquet"))
    ]
    option_tables = [
        pq.ParquetFile(path).read()
        for path in sorted((output / OPTIONS_DATASET).glob("year=*/data.parquet"))
    ]
    assert all(
        table.schema.equals(FUTURES_SCHEMA, check_metadata=True)
        for table in future_tables
    )
    assert all(
        table.schema.equals(OPTIONS_SCHEMA, check_metadata=True)
        for table in option_tables
    )
    futures = pa.concat_tables(future_tables).to_pandas()
    options = pa.concat_tables(option_tables).to_pandas()
    legacy_futures = futures.loc[futures["bridge_segment"].eq(LEGACY_SEGMENT)]
    official_futures = futures.loc[futures["bridge_segment"].eq(OFFICIAL_SEGMENT)]
    assert set(legacy_futures["session"]) == {REGULAR_SESSION, NIGHT_SESSION}
    assert official_futures["session"].eq(REGULAR_SESSION).all()
    assert legacy_futures["volume_unit_status"].eq(UNVERIFIED_UNIT).all()
    assert official_futures["volume_unit_status"].eq(CONTRACT_UNIT).all()
    assert options.loc[
        options["bridge_segment"].eq(OFFICIAL_SEGMENT), "session"
    ].eq(UNSPECIFIED_SESSION).all()
    assert options.loc[
        options["bridge_segment"].eq(LEGACY_SEGMENT), "open"
    ].isna().all()
    assert options.loc[
        options["bridge_segment"].eq(OFFICIAL_SEGMENT), "open"
    ].eq(0).all()
    assert futures["expiry_date"].isna().all()
    assert futures["predictive_use_status"].eq(PREDICTIVE_USE_STATUS).all()
    assert "continuous" not in futures.columns
    assert json.loads(state_path.read_text(encoding="utf-8")) == result


def test_registered_contracts_match_published_arrow_schemas():
    for dataset, schema in (
        (FUTURES_DATASET, FUTURES_SCHEMA),
        (OPTIONS_DATASET, OPTIONS_SCHEMA),
    ):
        contract = CONTRACTS[dataset]
        assert contract.primary_key == tuple(
            schema.metadata[b"primary_key"].decode().split(",")
        )
        assert contract.sort_key == contract.primary_key
        assert contract.partition_by == ("year",)
        assert contract.column_names == tuple(schema.names)
        assert tuple(column.nullable for column in contract.columns) == tuple(
            field.nullable for field in schema
        )


def test_staging_validation_failure_preserves_existing_bundle(tmp_path):
    output = tmp_path / "published-bridge"
    output.mkdir()
    marker = output / "marker"
    marker.write_text("preserved", encoding="utf-8")

    with pytest.raises(KOSPI200DerivativesBridgeError, match="OHLC coherence"):
        build_kospi200_derivatives_bridge(
            **_inputs(tmp_path, invalid_official_future=True),
            output_bundle_root=output,
            output_state_path=tmp_path / "state.json",
        )

    assert marker.read_text(encoding="utf-8") == "preserved"
    assert not (tmp_path / "state.json").exists()
