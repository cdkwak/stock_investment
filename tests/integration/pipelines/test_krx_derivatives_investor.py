from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.krx_derivatives_investor import (
    KR_KOSPI200_FUTURES_INVESTOR_TRADING_DAILY,
)
from stock_data.pipelines.krx_derivatives_investor import (
    KrxInvestorCollectionStopped, ResponseEnvelope, bounded_pilot_plan,
    build_request_plan, collect_landing_serial, coverage_chunks,
)
from stock_data.validation.krx_derivatives_investor import validate_derivatives_investor


PERMISSION_DIGEST = "a" * 64


def test_planner_preserves_scope_and_never_exceeds_two_years() -> None:
    chunks = coverage_chunks(date(2009, 12, 31))
    assert len(chunks) == 6
    assert chunks[0][0] == date(1999, 4, 26)
    assert chunks[-1][1] == date(2009, 12, 31)
    futures = build_request_plan("KOSPI200_FUTURES", date(2009, 12, 31))
    options = build_request_plan("KOSPI200_OPTIONS", date(2009, 12, 31))
    assert len(futures) == 6 * 3 * 2 * 3
    assert len(options) == 6 * 3 * 3 * 2 * 3
    assert {item.session for item in options} == {"ALL", "REGULAR", "NIGHT"}
    assert {item.option_right for item in options} == {"ALL", "CALL", "PUT"}
    assert len(bounded_pilot_plan("KOSPI200_FUTURES")) == 1
    with pytest.raises(ValueError, match="pre-source"):
        coverage_chunks(date(2000, 1, 1), start_date=date(1999, 4, 25))


def test_serial_collection_lands_ledgers_and_resumes(tmp_path: Path) -> None:
    plan = bounded_pilot_plan("KOSPI200_FUTURES")
    calls = []

    def request(spec):
        calls.append(spec.request_id)
        return ResponseEnvelope(200, {"content-type": "text/html"}, b'{"OutBlock_1":[{"x":1}]}')

    result = collect_landing_serial(
        plan, run_dir=tmp_path, request_fn=request,
        minimum_interval_seconds=0, sleep_fn=lambda _: None,
        permission_evidence_sha256=PERMISSION_DIGEST,
    )
    assert result["completed_requests"] == 1
    assert len(calls) == 1
    collect_landing_serial(
        plan, run_dir=tmp_path, request_fn=request,
        minimum_interval_seconds=0, sleep_fn=lambda _: None,
        permission_evidence_sha256=PERMISSION_DIGEST,
    )
    assert len(calls) == 1
    records = [
        json.loads(line)
        for line in (tmp_path / "request_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event"] for record in records] == ["REQUEST_STARTED", "REQUEST_COMPLETED"]


def test_restriction_stops_retry_zero_after_landing(tmp_path: Path) -> None:
    calls = 0

    def request(_):
        nonlocal calls
        calls += 1
        return ResponseEnvelope(403, {"content-type": "text/html"}, b"<html>denied</html>")

    with pytest.raises(KrxInvestorCollectionStopped, match="restriction HTTP 403"):
        collect_landing_serial(
            bounded_pilot_plan("KOSPI200_OPTIONS"), run_dir=tmp_path,
            request_fn=request, minimum_interval_seconds=0,
            permission_evidence_sha256=PERMISSION_DIGEST,
        )
    assert calls == 1
    assert json.loads((tmp_path / "checkpoint.json").read_text())["status"] == "STOPPED"
    assert len(list(tmp_path.glob("response_*.json"))) == 1


def test_permission_gate_fails_before_transport_or_artifact(tmp_path: Path) -> None:
    calls = 0

    def request(_):
        nonlocal calls
        calls += 1
        raise AssertionError("must not reach transport")

    with pytest.raises(KrxInvestorCollectionStopped, match="permission evidence"):
        collect_landing_serial(
            bounded_pilot_plan("KOSPI200_FUTURES"), run_dir=tmp_path,
            request_fn=request, minimum_interval_seconds=0,
        )
    assert calls == 0
    assert not list(tmp_path.iterdir())


def test_contract_validator_preserves_source_taxonomy_and_units() -> None:
    contract = KR_KOSPI200_FUTURES_INVESTOR_TRADING_DAILY
    frame = pd.DataFrame([{
        "date": "1999-04-26", "product": "KOSPI200_FUTURES", "option_right": "NA",
        "session": "ALL", "investor_type_source": "개인",
        "sell_volume": 10.0, "buy_volume": 13.0, "net_buy_volume": 3.0,
        "sell_trading_value": 20.0, "buy_trading_value": 19.0,
        "net_buy_trading_value": -1.0, "volume_unit_source": "계약",
        "trading_value_unit_source": "백만원", "source": "krx_basic_statistics",
        "source_operation": "15007_daily_trend",
    }])[list(contract.column_names)]
    validate_derivatives_investor(frame, contract)
    broken = frame.copy()
    broken.loc[0, "date"] = "1999-04-25"
    with pytest.raises(ValueError, match="precedes"):
        validate_derivatives_investor(broken, contract)
