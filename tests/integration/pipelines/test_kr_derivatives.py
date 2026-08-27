from __future__ import annotations

import json

import pandas as pd
import pytest

from stock_data.contracts.kr_derivatives import (
    KR_KOSPI200_FUTURES_DAILY,
    KR_KOSPI200_OPTIONS_DAILY,
)
from stock_data.pipelines.derivatives_backfill import (
    collect_derivative_dates,
    collect_derivative_ranges,
)
from stock_data.providers.data_go_kr.derivatives import (
    PRODUCT_SPECS,
    normalize_derivatives,
    range_request_filters,
    request_filters,
)


def future(name="코스피200 F 202212", code="101SC000"):
    return {
        "basDt": "20220919",
        "prdCtg": "파생 선물 코스피200 (주간)",
        "srtnCd": code,
        "isinCd": "KR4101SC0009",
        "itmsNm": name,
        "mkp": "306.1",
        "hipr": "310.1",
        "lopr": "305.9",
        "clpr": "306.9",
        "sptPrc": "306.49",
        "stmPrc": "306.9",
        "trqu": "241199",
        "trPrc": "18546277242500",
        "opnint": "301909",
    }


def option(name="코스피200 P 202210 180.0", code="301SA180"):
    return {
        "basDt": "20220919",
        "prdCtg": "파생 옵션 코스피200",
        "srtnCd": code,
        "isinCd": "KR4301SA1808",
        "itmsNm": name,
        "mkp": "0",
        "hipr": "0",
        "lopr": "0",
        "clpr": "0",
        "nxtDdBsPrc": "126.15",
        "iptVlty": "3",
        "trqu": "0",
        "trPrc": "0",
        "opnint": "7",
    }


def payload(item):
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "pageNo": 1,
                "numOfRows": 9999,
                "totalCount": 1,
                "items": {"item": [item]},
            },
        }
    }


class Response:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


def test_exact_product_filter_and_contract_scope():
    spec = PRODUCT_SPECS["kospi200_futures"]
    assert request_filters(spec, "20220919") == {
        "basDt": "20220919",
        "prdCtg": "파생 선물 코스피200 (주간)",
    }
    assert KR_KOSPI200_FUTURES_DAILY.primary_key == ("date", "contract")
    assert "maturity_month" in KR_KOSPI200_OPTIONS_DAILY.column_names
    assert range_request_filters(spec, "20220901", "20220930") == {
        "beginBasDt": "20220901",
        "endBasDt": "20221001",
        "prdCtg": "파생 선물 코스피200 (주간)",
    }


def test_futures_promotes_only_outrights_and_preserves_source_fields():
    spec = PRODUCT_SPECS["kospi200_futures"]
    frame = normalize_derivatives(
        [future(), future("코스피200 SP 2212-2303", "401SCT3S")], spec
    )
    assert len(frame) == 1
    assert frame.loc[0, "maturity_month"] == "2022-12"
    assert frame.loc[0, "underlying_value"] == 306.49
    assert frame.loc[0, "settlement_price"] == 306.9
    assert frame.loc[0, "open_interest"] == 301909


def test_options_parse_only_verified_tokens_and_preserve_zero():
    frame = normalize_derivatives([option()], PRODUCT_SPECS["kospi200_options"])
    assert frame.loc[0, "call_put"] == "PUT"
    assert frame.loc[0, "maturity_month"] == "2022-10"
    assert frame.loc[0, "strike"] == 180.0
    assert frame.loc[0, ["open", "high", "low", "close", "volume"]].eq(0).all()
    assert "underlying_value" not in frame.columns


def test_kosdaq150_option_strike_allows_source_thousands_separator():
    item = option("코스닥150 C 202210   1,275")
    item["prdCtg"] = "파생 옵션 코스닥150"
    frame = normalize_derivatives([item], PRODUCT_SPECS["kosdaq150_options"])
    assert frame.loc[0, "strike"] == 1275.0


def test_unknown_target_name_is_rejected_and_cross_product_row_is_not_promoted():
    with pytest.raises(ValueError, match="unexpected futures item name"):
        normalize_derivatives([future("코스피200 UNKNOWN")], PRODUCT_SPECS["kospi200_futures"])
    item = future()
    item["prdCtg"] = "파생 선물 미국달러 (주간)"
    assert normalize_derivatives([item], PRODUCT_SPECS["kospi200_futures"]).empty


def test_prefix_filtered_weekly_options_remain_landing_only():
    regular = option()
    weekly = option("코스피200 C 202209 300.0", "201S9300")
    weekly["prdCtg"] = "파생 옵션 코스피200 위클리"
    frame = normalize_derivatives([regular, weekly], PRODUCT_SPECS["kospi200_options"])
    assert len(frame) == 1
    assert frame.loc[0, "contract"] == regular["srtnCd"]


def test_backfill_uses_exact_filter_no_retry_and_atomic_readback(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, *, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers})
        return Response(payload(future()))

    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "fixture-secret")
    monkeypatch.setattr("requests.get", fake_get)
    result = collect_derivative_dates(
        project_root=tmp_path,
        spec=PRODUCT_SPECS["kospi200_futures"],
        dates=["20220919"],
        max_calls=1,
        min_interval_seconds=0,
    )
    assert result.api_calls == 1 and result.rows_written == 1
    assert len(calls) == 1
    assert calls[0]["params"]["prdCtg"] == "파생 선물 코스피200 (주간)"
    assert "fixture-secret" not in json.dumps(
        json.loads((tmp_path / "data/landing/data_go_kr/kr_kospi200_futures_daily/20220919.json").read_text(encoding="utf-8")),
        ensure_ascii=False,
    )
    stored = pd.read_parquet(
        tmp_path / "data/normalized/kr_kospi200_futures_daily/year=2022/data.parquet"
    )
    assert len(stored) == 1


def test_range_backfill_batches_dates_and_respects_global_call_cap(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, *, params, headers, timeout):
        calls.append(params)
        rows = []
        for value, code in (("20220919", "101SC000"), ("20220920", "101SC001")):
            row = future(code=code)
            row["basDt"] = value
            rows.append(row)
        body = payload(rows[0])
        body["response"]["body"]["totalCount"] = 2
        body["response"]["body"]["items"]["item"] = rows
        return Response(body)

    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "fixture-secret")
    monkeypatch.setattr("requests.get", fake_get)
    result = collect_derivative_ranges(
        project_root=tmp_path,
        spec=PRODUCT_SPECS["kospi200_futures"],
        dates=["20220919", "20220920"],
        max_calls=1,
        maximum_dates_per_range=60,
        min_interval_seconds=0,
    )
    assert result.api_calls == 1
    assert result.completed_dates == ("20220919", "20220920")
    assert result.unresolved_dates == ()
    assert calls[0]["beginBasDt"] == "20220919"
    assert calls[0]["endBasDt"] == "20220921"
    assert len(pd.read_parquet(
        tmp_path / "data/normalized/kr_kospi200_futures_daily/year=2022/data.parquet"
    )) == 2


def test_range_missing_date_is_unresolved_not_valid_empty(tmp_path, monkeypatch):
    def fake_get(url, *, params, headers, timeout):
        return Response(payload(future()))

    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "fixture-secret")
    monkeypatch.setattr("requests.get", fake_get)
    result = collect_derivative_ranges(
        project_root=tmp_path,
        spec=PRODUCT_SPECS["kospi200_futures"],
        dates=["20220919", "20220920"],
        max_calls=1,
        min_interval_seconds=0,
    )
    assert result.valid_empty_dates == ()
    assert result.unresolved_dates == ("20220920",)
    state = json.loads((
        tmp_path / "data/state/kr_kospi200_futures_daily.json"
    ).read_text(encoding="utf-8"))
    assert state["valid_empty_partitions"] == []
    assert state["staged_partitions"] == ["20220920"]


def test_no_live_without_explicit_manual_script_flag(monkeypatch, capsys):
    # The executable itself is exercised through its no-live path without importing pykrx.
    import scripts.manual.backfill.backfill_kospi200_derivatives as script

    monkeypatch.setattr("sys.argv", ["backfill_kospi200_derivatives.py"])
    assert script.main() == 2
    assert json.loads(capsys.readouterr().out)["api_calls"] == 0
