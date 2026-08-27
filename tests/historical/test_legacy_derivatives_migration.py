from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.pipelines.legacy_derivatives_migration import (
    FUTURES_SOURCE_COLUMNS,
    OPTIONS_SOURCE_COLUMNS,
    LegacyDerivativeMigrationError,
    run_legacy_derivatives_migration,
)


def _source_paths(root: Path) -> tuple[Path, Path]:
    futures = root / "data/raw/kr/krx/derivatives/futures/fut_bydd_trd_all.csv"
    options = root / "data/raw/kr/krx/derivatives/options/opt_bydd_trd_all.csv"
    futures.parent.mkdir(parents=True)
    options.parent.mkdir(parents=True)
    return futures, options


def _future(**updates) -> dict[str, str]:
    row = dict.fromkeys(FUTURES_SOURCE_COLUMNS, "")
    row.update({
        "BAS_DD": "20100104", "PROD_NM": "코스피200 선물", "MKT_NM": "정규",
        "ISU_CD": "101E3000", "ISU_NM": "코스피200 F 201003 (주간)",
        "TDD_CLSPRC": "220.00", "CMPPREVDD_PRC": "0",
        "TDD_OPNPRC": "0", "TDD_HGPRC": "0", "TDD_LWPRC": "0",
        "SPOT_PRC": "223.15", "SETL_PRC": "220.00", "ACC_TRDVOL": "0",
        "ACC_TRDVAL": "0", "ACC_OPNINT_QTY": "0",
    })
    row.update(updates)
    return row


def _option(**updates) -> dict[str, str]:
    row = dict.fromkeys(OPTIONS_SOURCE_COLUMNS, "")
    row.update({
        "BAS_DD": "20100104", "PROD_NM": "코스피200 옵션", "RGHT_TP_NM": "CALL",
        "ISU_CD": "201E3220", "ISU_NM": "코스피200 C 201001 220.0",
        "TDD_CLSPRC": "0", "CMPPREVDD_PRC": "0", "TDD_OPNPRC": "0",
        "TDD_HGPRC": "0", "TDD_LWPRC": "0", "IMP_VOLT": "8.3",
        "NXTDD_BAS_PRC": "45.7", "ACC_TRDVOL": "0", "ACC_TRDVAL": "0",
        "ACC_OPNINT_QTY": "7", "SOURCE_ROW_NO": "0",
    })
    row.update(updates)
    return row


def _write_sources(root: Path, futures_rows: list[dict], options_rows: list[dict]) -> None:
    futures, options = _source_paths(root)
    pd.DataFrame(futures_rows, columns=FUTURES_SOURCE_COLUMNS).to_csv(
        futures, index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(options_rows, columns=OPTIONS_SOURCE_COLUMNS).to_csv(
        options, index=False, encoding="utf-8-sig"
    )


def test_migration_preserves_landing_values_zero_missing_and_option_duplicates(tmp_path):
    legacy = tmp_path / "legacy"
    project = tmp_path / "rev1"
    _write_sources(
        legacy,
        [
            _future(),
            _future(MKT_NM="야간", ISU_NM="코스피200 F 201003 (야간)"),
            _future(BAS_DD="20200102", ISU_CD="101F3000"),
            _future(PROD_NM="미국달러 선물", ISU_CD="175E3000"),
        ],
        [
            _option(),
            _option(SOURCE_ROW_NO="1"),
            _option(PROD_NM="미니코스피200 옵션", SOURCE_ROW_NO="2"),
        ],
    )

    result = run_legacy_derivatives_migration(
        project_root=project, legacy_root=legacy, chunksize=1
    )

    assert result["api_calls"] == 0
    datasets = {item["dataset"]: item for item in result["datasets"]}
    assert datasets["krx_legacy_kospi200_futures_daily"]["rows"] == 2
    assert datasets["krx_legacy_kospi200_futures_daily"]["primary_key"] == (
        "date", "market_name", "contract"
    )
    assert datasets["krx_legacy_kospi200_futures_daily"][
        "secondary_key_duplicate_rows"
    ] == 2
    options_result = datasets["krx_legacy_kospi200_options_daily"]
    assert options_result["rows"] == 2
    assert options_result["primary_key_duplicates"] == 0
    assert options_result["secondary_key_duplicate_groups"] == 1
    assert options_result["secondary_key_duplicate_rows"] == 2

    landing = pd.read_parquet(
        project / "data/landing/legacy_stock_investment/krx_derivatives_2010_2019"
        / "options/year=2010/data.parquet"
    )
    assert landing["ISU_CD"].tolist() == ["201E3220", "201E3220"]
    assert landing["SOURCE_ROW_NO"].tolist() == ["0", "1"]
    assert landing["SOURCE_FILE_ROW_NO"].tolist() == [0, 1]
    normalized = pd.read_parquet(
        project / "data/normalized/krx_legacy_kospi200_options_daily"
        / "year=2010/data.parquet"
    )
    assert normalized["source_row_no"].tolist() == [0, 1]
    assert normalized["source_file_row_no"].tolist() == [0, 1]
    assert normalized[["open", "high", "low", "close", "volume"]].eq(0).all().all()
    assert normalized["contract"].tolist() == ["201E3220", "201E3220"]
    assert json.loads((
        project / "data/state/legacy_kospi200_derivatives_2010_2019.json"
    ).read_text(encoding="utf-8"))["status"] == "complete"


def test_migration_preserves_empty_numeric_as_null(tmp_path):
    legacy = tmp_path / "legacy"
    project = tmp_path / "rev1"
    _write_sources(
        legacy,
        [_future(TDD_CLSPRC="", TDD_OPNPRC="", TDD_HGPRC="", TDD_LWPRC="")],
        [_option(TDD_CLSPRC="", TDD_OPNPRC="", TDD_HGPRC="", TDD_LWPRC="")],
    )
    run_legacy_derivatives_migration(project_root=project, legacy_root=legacy)
    frame = pd.read_parquet(
        project / "data/normalized/krx_legacy_kospi200_futures_daily"
        / "year=2010/data.parquet"
    )
    assert frame[["open", "high", "low", "close"]].isna().all().all()


def test_duplicate_source_row_key_fails_before_promotion(tmp_path):
    legacy = tmp_path / "legacy"
    project = tmp_path / "rev1"
    _write_sources(legacy, [_future()], [_option(), _option()])
    with pytest.raises(LegacyDerivativeMigrationError, match="pk_duplicates=2"):
        run_legacy_derivatives_migration(project_root=project, legacy_root=legacy)
    assert not (project / "data/normalized/krx_legacy_kospi200_options_daily").exists()
    assert not (project / "data/state/legacy_kospi200_derivatives_2010_2019.json").exists()


def test_invalid_ohlc_fails_before_promotion(tmp_path):
    legacy = tmp_path / "legacy"
    project = tmp_path / "rev1"
    _write_sources(
        legacy,
        [_future(
            TDD_OPNPRC="230", TDD_HGPRC="220", TDD_LWPRC="210",
            ACC_TRDVOL="1",
        )],
        [_option()],
    )
    with pytest.raises(LegacyDerivativeMigrationError, match="ohlc=1"):
        run_legacy_derivatives_migration(project_root=project, legacy_root=legacy)
