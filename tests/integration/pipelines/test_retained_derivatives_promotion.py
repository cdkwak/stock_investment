from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.kr_derivatives import (
    KR_KOSDAQ150_FUTURES_DAILY,
    KR_KOSDAQ150_OPTIONS_DAILY,
)
from stock_data.pipelines.retained_derivatives_promotion import (
    CLASSIFICATION,
    promote_retained_kosdaq150,
)
from stock_data.providers.data_go_kr.derivatives import PRODUCT_SPECS, _KOREAN_UNDERLYING


def _future(name: str, code: str) -> dict[str, str]:
    spec = PRODUCT_SPECS["kosdaq150_futures"]
    return {
        "basDt": "20220919", "prdCtg": spec.product_category,
        "srtnCd": code, "isinCd": "KR4106SC0008", "itmsNm": name,
        "mkp": "1000.0", "hipr": "1010.0", "lopr": "990.0", "clpr": "1005.0",
        "sptPrc": "1004.0", "stmPrc": "1005.0", "trqu": "0",
        "trPrc": "0", "opnint": "7",
    }


def _option() -> dict[str, str]:
    spec = PRODUCT_SPECS["kosdaq150_options"]
    underlying = _KOREAN_UNDERLYING[spec.underlying]
    return {
        "basDt": "20220919", "prdCtg": spec.product_category,
        "srtnCd": "306SA127", "isinCd": "KR4306SA1277",
        "itmsNm": f"{underlying} C 202210 1,275", "mkp": "0", "hipr": "0",
        "lopr": "0", "clpr": "0", "nxtDdBsPrc": "0", "iptVlty": "3",
        "trqu": "0", "trPrc": "0", "opnint": "1",
    }


def _landing(path: Path, rows: list[dict[str, str]]) -> None:
    payload = [{
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "pageNo": 1, "numOfRows": 9999, "totalCount": len(rows),
                "items": {"item": rows},
            },
        }
    }]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_registered_schema_and_primary_keys_are_fixed_before_write():
    assert KR_KOSDAQ150_OPTIONS_DAILY.primary_key == ("date", "contract")
    assert KR_KOSDAQ150_FUTURES_DAILY.primary_key == ("date", "contract")
    assert KR_KOSDAQ150_OPTIONS_DAILY.column_names == (
        "date", "underlying", "contract", "isin", "name", "product_category",
        "maturity_month", "call_put", "strike", "open", "high", "low", "close",
        "next_day_base_price", "implied_volatility", "volume", "trading_value",
        "open_interest", "source", "source_operation",
    )
    assert KR_KOSDAQ150_FUTURES_DAILY.column_names == (
        "date", "underlying", "contract", "isin", "name", "product_category",
        "maturity_month", "open", "high", "low", "close", "underlying_value",
        "settlement_price", "volume", "trading_value", "open_interest", "source",
        "source_operation",
    )


def test_offline_promotion_is_atomic_manifested_and_documents_spread(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: pytest.fail("offline promotion attempted a network call"),
    )
    options = tmp_path / "options.json"
    futures = tmp_path / "futures.json"
    underlying = _KOREAN_UNDERLYING["KOSDAQ150"]
    _landing(options, [_option()])
    _landing(futures, [
        _future(f"{underlying} F 202212", "106SC000"),
        _future(f"{underlying} SP 2212-2303", "406SCT3S"),
    ])

    state = promote_retained_kosdaq150(
        project_root=tmp_path, options_input=options, futures_input=futures
    )

    assert state["classification"] == CLASSIFICATION
    assert [item["rows"] for item in state["outputs"]] == [1, 1]
    assert state["inputs"][1]["source_rows"] == 2
    assert state["inputs"][1]["declared_total_count"] == 2
    assert state["inputs"][1]["exact_category_rows"] == 2
    assert state["inputs"][1]["excluded_rows"] == 1
    assert state["inputs"][1]["exclusion_reason"] == "calendar_spread_landing_only"
    assert state["inputs"][1]["excluded_contracts"] == ["406SCT3S"]
    assert len(state["inputs"][0]["sha256"]) == 64
    assert len(state["outputs"][0]["output_files"][0]["sha256"]) == 64
    assert len(pd.read_parquet(
        tmp_path / "data/normalized/kr_kosdaq150_options_daily/year=2022/data.parquet"
    )) == 1
    persisted = json.loads((
        tmp_path / "data/state/d004_kosdaq150_retained_promotion.json"
    ).read_text(encoding="utf-8"))
    assert persisted == state


def test_undocumented_exact_category_exclusion_fails_before_any_write(tmp_path):
    options = tmp_path / "options.json"
    futures = tmp_path / "futures.json"
    _landing(options, [_option()])
    underlying = _KOREAN_UNDERLYING["KOSDAQ150"]
    _landing(futures, [_future(f"{underlying} UNKNOWN", "106BAD00")])
    with pytest.raises(ValueError, match="unexpected futures item name"):
        promote_retained_kosdaq150(
            project_root=tmp_path, options_input=options, futures_input=futures
        )
    assert not (tmp_path / "data").exists()


def test_incomplete_retained_pages_fail_before_any_write(tmp_path):
    options = tmp_path / "options.json"
    futures = tmp_path / "futures.json"
    _landing(options, [_option()])
    payload = json.loads(options.read_text(encoding="utf-8"))
    payload[0]["response"]["body"]["totalCount"] = 2
    options.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    underlying = _KOREAN_UNDERLYING["KOSDAQ150"]
    _landing(futures, [_future(f"{underlying} F 202212", "106SC000")])
    with pytest.raises(RuntimeError, match="retained Landing is incomplete"):
        promote_retained_kosdaq150(
            project_root=tmp_path, options_input=options, futures_input=futures
        )
    assert not (tmp_path / "data").exists()
