from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.legacy_market_investor import KR_MARKET_INVESTOR_NET_PURCHASE_DAILY
from stock_data.pipelines import legacy_market_investor_import as importer
from stock_data.pipelines.legacy_market_investor_import import (
    LegacyMarketInvestorImportError,
    run_legacy_market_investor_import,
)
from stock_data.validation.legacy_market_investor import validate_legacy_market_investor_net_purchase


def _source_path(root: Path) -> Path:
    path = root / importer.SOURCE_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    return path


def _write_source(root: Path, rows: list[dict[str, object]]) -> Path:
    path = _source_path(root)
    pd.DataFrame(rows, columns=importer.SOURCE_COLUMNS).to_csv(path, index=False)
    return path


def _row(date: str, **updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "date": date, "symbol": "KOSPI", "institution_net_buy": -10,
        "other_corporation_net_buy": 0, "individual_net_buy": 3,
        "foreign_net_buy": 7, "total_net_buy": 0,
    }
    result.update(updates)
    return result


def _allow_fixture_checksum(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(importer, "EXPECTED_SOURCE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(importer, "EXPECTED_ROWS", 2)


def test_import_writes_scoped_landing_normalized_and_state(monkeypatch, tmp_path: Path):
    legacy = tmp_path / "legacy"
    project = tmp_path / "project"
    source = _write_source(legacy, [
        _row("1999-01-04"),
        _row("2014-06-30", institution_net_buy=2, individual_net_buy=-2, foreign_net_buy=0),
        _row("2014-07-01"),
    ])
    _allow_fixture_checksum(monkeypatch, source)
    result = run_legacy_market_investor_import(project_root=project, legacy_root=legacy)
    assert result["api_calls"] == 0
    assert result["scope"]["a001_starts"] == "2014-07-01"
    assert result["scope"]["overlap_dates_permitted"] is False
    assert result["validation"]["rows"] == 2
    assert result["validation"]["primary_key_duplicates"] == 0
    assert result["validation"]["category_sum_mismatches"] == 0
    assert result["validation"]["valid_zero_rows"] == 2
    assert result["validation"]["negative_value_rows"] == 2
    landing = pd.concat([pd.read_parquet(path) for path in sorted((project / "data/landing").rglob("data.parquet"))], ignore_index=True)
    assert landing["source_file_row_no"].tolist() == [0, 1]
    assert landing["other_corporation_net_buy"].tolist() == ["0", "0"]
    normalized = pd.concat([pd.read_parquet(path) for path in sorted((project / "data/normalized").rglob("data.parquet"))], ignore_index=True)
    normalized["date"] = pd.to_datetime(normalized["date"]).dt.strftime("%Y-%m-%d")
    assert tuple(normalized.columns) == KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.column_names
    assert normalized["market"].tolist() == ["KOSPI", "KOSPI"]
    assert normalized["institution_net_buy"].tolist() == [-10, 2]
    assert normalized["provider_boundary"].eq("legacy_pre_a001_only").all()
    state = json.loads((project / "data/state/legacy_market_investor_import.json").read_text())
    assert state["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert state["normalized"]["schema"] == list(KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.column_names)


def test_checksum_mismatch_fails_before_artifact_write(tmp_path: Path):
    legacy = tmp_path / "legacy"
    project = tmp_path / "project"
    _write_source(legacy, [_row("1999-01-04")])
    with pytest.raises(LegacyMarketInvestorImportError, match="checksum"):
        run_legacy_market_investor_import(project_root=project, legacy_root=legacy)
    assert not (project / "data").exists()


def test_validation_rejects_duplicate_nonfinite_and_category_sum_mismatch():
    frame = pd.DataFrame([
        ["2014-06-30", "KOSPI", -10, 0, 3, 7, 0, "legacy_stock_investment_pykrx_1.2.8", "MDCSTAT02202", "legacy_pre_a001_only"],
        ["2014-06-30", "KOSPI", -10, 0, 3, 7, 0, "legacy_stock_investment_pykrx_1.2.8", "MDCSTAT02202", "legacy_pre_a001_only"],
    ], columns=KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.column_names)
    with pytest.raises(ValueError, match="duplicate"):
        validate_legacy_market_investor_net_purchase(frame)
    frame = frame.iloc[:1].copy()
    frame["foreign_net_buy"] = frame["foreign_net_buy"].astype("float64")
    frame.loc[0, "foreign_net_buy"] = float("inf")
    with pytest.raises(ValueError, match="invalid investor"):
        validate_legacy_market_investor_net_purchase(frame)
    frame["foreign_net_buy"] = 7
    frame["total_net_buy"] = 1
    with pytest.raises(ValueError, match="category sum"):
        validate_legacy_market_investor_net_purchase(frame)


@pytest.mark.parametrize(
    ("date", "error"),
    [("1999-01-03", "outside C004 legacy scope"), ("2014-07-01", "A001 provider boundary")],
)
def test_validation_rejects_dates_outside_c004_legacy_boundary(date: str, error: str):
    frame = pd.DataFrame([[
        date, "KOSPI", -10, 0, 3, 7, 0,
        "legacy_stock_investment_pykrx_1.2.8", "MDCSTAT02202", "legacy_pre_a001_only",
    ]], columns=KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.column_names)
    with pytest.raises(ValueError, match=error):
        validate_legacy_market_investor_net_purchase(frame)
