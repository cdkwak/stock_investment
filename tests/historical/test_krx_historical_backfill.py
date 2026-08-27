import json
from pathlib import Path

import pytest

from stock_data.pipelines.krx_historical_backfill import KrxStop, run_krx_historical_backfill


def trade(date):
    return {"BAS_DD":date, "ISU_CD":"005930", "ISU_NM":"Samsung", "MKT_NM":"KOSPI",
            "TDD_OPNPRC":"100", "TDD_HGPRC":"110", "TDD_LWPRC":"90",
            "TDD_CLSPRC":"105", "ACC_TRDVOL":"10", "ACC_TRDVAL":"1050",
            "MKTCAP":"105000", "LIST_SHRS":"1000"}


def basic():
    return {"ISU_CD":"KR7005930003", "ISU_SRT_CD":"005930", "ISU_NM":"Samsung",
            "ISU_ABBRV":"Samsung", "ISU_ENG_NM":"Samsung", "LIST_DD":"1975/06/11",
            "MKT_TP_NM":"KOSPI", "SECUGRP_NM":"Stock", "SECT_TP_NM":"-",
            "KIND_STKCERT_TP_NM":"Common", "PARVAL":"100", "LIST_SHRS":"1000"}


class Response:
    status_code = 200
    headers = {"content-type":"application/json"}
    def __init__(self, payload): self.payload = payload
    def json(self): return self.payload


class Session:
    def __init__(self): self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(url)
        date = kwargs["params"]["basDd"]
        return Response({"OutBlock_1":[basic() if "base_info" in url else trade(date)]})


def test_daily_cap_uses_whole_dates_and_resume_staging(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KRX_AUTH_KEY", "fixture")
    session = Session()
    result = run_krx_historical_backfill(
        tmp_path, ["20100104", "20100105"], observed_calls=7996,
        batch_size=2, session=session, sleep_fn=lambda _: None)
    assert result["completed_dates"] == 1
    assert result["calls_today"] == 8000
    assert len(session.calls) == 4
    state = json.loads((tmp_path / "data/state/krx_equity_historical.json").read_text())
    assert state["completed_partitions"] == ["20100104"]


class DeniedSession(Session):
    def get(self, url, **kwargs):
        response = Response({"respCode":"401", "respMsg":"Unauthorized"})
        response.status_code = 401
        return response


def test_authentication_error_stops_without_retry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KRX_AUTH_KEY", "fixture")
    with pytest.raises(KrxStop, match="immediate-stop"):
        run_krx_historical_backfill(
            tmp_path, ["20100104"], observed_calls=0,
            session=DeniedSession(), sleep_fn=lambda _: None)
    ledger = json.loads((tmp_path / "data/state/krx_open_api_call_ledger.json").read_text())
    assert ledger["calls"] == 1
