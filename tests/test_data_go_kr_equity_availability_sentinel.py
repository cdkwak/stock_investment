import json
from pathlib import Path

import pytest

from scripts.manual.data_go_kr_equity_availability_sentinel import (
    SentinelError, _classify, adopt_nonempty_pair, run_sentinel,
)


def _price(date="20260812"):
    return {"basDt": date, "srtnCd": "A005930", "isinCd": "KR7005930003", "itmsNm": "Samsung", "mrktCtg": "KOSPI", "clpr": "70000", "vs": "1", "fltRt": "0.1", "mkp": "69000", "hipr": "71000", "lopr": "68000", "trqu": "100", "trPrc": "7000000", "lstgStCnt": "1000", "mrktTotAmt": "70000000"}


def _universe(date="20260812"):
    return {"basDt": date, "srtnCd": "A005930", "isinCd": "KR7005930003", "itmsNm": "Samsung", "mrktCtg": "KOSPI", "crno": "1", "corpNm": "Samsung"}


class Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    def __init__(self, item):
        self.payload = {"response": {"header": {"resultCode": "00", "resultMsg": "OK"}, "body": {"pageNo": 1, "numOfRows": 9999, "totalCount": 1, "items": {"item": [item]}}}}
        self.content = json.dumps(self.payload).encode()
    def json(self): return self.payload
    def raise_for_status(self): return None


class ItemsResponse(Response):
    def __init__(self, items):
        self.payload = {"response": {"header": {"resultCode": "00", "resultMsg": "OK"},
            "body": {"pageNo": 1, "numOfRows": 9999, "totalCount": len(items), "items": {"item": items}}}}
        self.content = json.dumps(self.payload).encode()


class Session:
    def __init__(self): self.calls = 0
    def get(self, *args, **kwargs):
        self.calls += 1
        return Response(_price() if self.calls == 1 else _universe())


class WrongDateSession(Session):
    def get(self, *args, **kwargs):
        self.calls += 1
        return Response(_price("20260811"))


def test_classification_distinguishes_nonempty_and_unavailable():
    assert _classify("price_cap", (), 0, "20260812")["classification"] == "VALID_EMPTY_NOT_YET_AVAILABLE"
    assert _classify("price_cap", (_price(),), 1, "20260812")["price_rows"] == 1
    assert _classify("universe", (_universe(),), 1, "20260812")["universe_rows"] == 1


def test_wrong_date_is_anomaly():
    with pytest.raises(SentinelError, match="date"):
        _classify("universe", (_universe("20260811"),), 1, "20260812")


def test_known_konex_is_reported_and_excluded_from_scoped_contract():
    konex = {**_price(), "srtnCd": "A123456", "isinCd": "KR7123450000", "mrktCtg": "KONEX"}
    result = _classify("price_cap", (_price(), konex), 2, "20260812")
    assert result["source_rows"] == 2 and result["scoped_rows"] == 1
    assert result["excluded_known_rows"] == 1
    assert result["source_market_counts"] == {"KONEX": 1, "KOSPI": 1}


def test_unknown_market_fails_closed():
    unknown = {**_price(), "mrktCtg": "OTC"}
    with pytest.raises(SentinelError, match="unknown.*OTC"):
        _classify("price_cap", (_price(), unknown), 2, "20260812")


def test_live_sentinel_is_two_calls_and_does_not_touch_production(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "secret")
    session = Session()
    result = run_sentinel(tmp_path, "20260812", session=session)
    assert session.calls == 2 and result["status"] == "NONEMPTY_AVAILABLE"
    assert result["production_checkpoint_writes"] is False
    assert result["normalized_writes"] is False
    assert not (tmp_path / "data/state/kr_equity_price_cap_daily.json").exists()
    retained = b"".join(p.read_bytes() for p in Path(result["run_root"]).iterdir() if p.is_file())
    assert b"secret" not in retained


def test_anomaly_stops_before_second_call_and_writes_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "secret")
    session = WrongDateSession()
    with pytest.raises(SentinelError, match="anomaly"):
        run_sentinel(tmp_path, "20260812", session=session)
    assert session.calls == 1
    manifest = json.loads(next((tmp_path / "data/landing/diagnostics/data_go_kr_equity_availability").rglob("manifest.json")).read_text())
    assert manifest["status"] == "ANOMALY" and manifest["adoption_eligible"] is False
    run_root = next((tmp_path / "data/landing/diagnostics/data_go_kr_equity_availability").iterdir())
    landing = run_root / manifest["results"]["price_cap"]["landing_file"]
    assert landing.is_file()
    assert manifest["results"]["price_cap"]["landing_sha256"]
    raw = run_root / manifest["results"]["price_cap"]["raw_body_file"]
    assert raw.is_file() and manifest["results"]["price_cap"]["raw_body_sha256"]


def test_classification_failure_preserves_exact_landing_before_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "secret")
    class UnknownSession:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return ItemsResponse([{**_price(), "mrktCtg": "OTC"}])
    session = UnknownSession()
    with pytest.raises(SentinelError, match="unknown"):
        run_sentinel(tmp_path, "20260812", session=session)
    run_root = next((tmp_path / "data/landing/diagnostics/data_go_kr_equity_availability").iterdir())
    manifest = json.loads((run_root / "manifest.json").read_text())
    landing = run_root / manifest["results"]["price_cap"]["landing_file"]
    assert session.calls == 1 and landing.is_file()
    assert __import__("hashlib").sha256(landing.read_bytes()).hexdigest() == manifest["results"]["price_cap"]["landing_sha256"]
    raw = run_root / manifest["results"]["price_cap"]["raw_body_file"]
    call = run_root / manifest["results"]["price_cap"]["raw_call_file"]
    assert b'"OTC"' in raw.read_bytes()
    retained_call = json.loads(call.read_text())
    assert "serviceKey" not in retained_call["public_parameters"]


def test_secret_echo_is_blocked_before_body_persistence(tmp_path, monkeypatch):
    secret = "secret%2Bencoded"
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", secret)
    class EchoSession:
        def get(self, *args, **kwargs):
            response = Response(_price())
            response.content = b'{"echo":"secret+encoded"}'
            return response
    with pytest.raises(SentinelError, match="credential variant"):
        run_sentinel(tmp_path, "20260812", session=EchoSession())
    run_root = next((tmp_path / "data/landing/diagnostics/data_go_kr_equity_availability").iterdir())
    assert not list(run_root.glob("*.body"))
    retained = b"".join(path.read_bytes() for path in run_root.iterdir() if path.is_file())
    assert b"secret%2Bencoded" not in retained and b"secret+encoded" not in retained
    assert json.loads((run_root / "manifest.json").read_text())["status"] == "ANOMALY"


def test_nonempty_pair_can_be_staged_offline_without_normalized_write(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "secret")
    result = run_sentinel(tmp_path, "20260812", session=Session())
    adopted = adopt_nonempty_pair(tmp_path, Path(result["run_root"]))
    assert adopted["network_requests"] == 0
    assert (tmp_path / "data/landing/data_go_kr/stock_price/20260812.json").is_file()
    assert (tmp_path / "data/landing/data_go_kr/kr_equity_universe_daily/20260812.json").is_file()
    assert not (tmp_path / "data/normalized").exists()
    for state_name in ("kr_equity_price_cap_daily", "kr_equity_universe_daily"):
        state = json.loads((tmp_path / "data/state" / f"{state_name}.json").read_text())
        assert state["staged_partitions"] == ["20260812"]


def test_adoption_rejects_raw_call_ledger_and_path_tamper(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "secret")
    result = run_sentinel(tmp_path, "20260812", session=Session())
    run_root = Path(result["run_root"])
    raw_call = next(run_root.glob("raw_call_01_*.json"))
    original = raw_call.read_bytes(); call = json.loads(original); call["sequence"] = 2
    raw_call.write_text(json.dumps(call))
    with pytest.raises(SentinelError, match="raw call|hash"):
        adopt_nonempty_pair(tmp_path, run_root)
    raw_call.write_bytes(original)
    ledger = run_root / "call_ledger.jsonl"; original_ledger = ledger.read_bytes()
    lines = ledger.read_text().splitlines(); lines.reverse(); ledger.write_text("\n".join(lines) + "\n")
    manifest = json.loads((run_root / "manifest.json").read_text()); manifest["call_ledger_sha256"] = __import__("hashlib").sha256(ledger.read_bytes()).hexdigest()
    (run_root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SentinelError, match="exactly two calls|ledger"):
        adopt_nonempty_pair(tmp_path, run_root)
    ledger.write_bytes(original_ledger)


def test_adoption_rejects_non_immediate_manifest_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "secret")
    result = run_sentinel(tmp_path, "20260812", session=Session())
    run_root = Path(result["run_root"]); manifest_path = run_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["results"]["price_cap"]["landing_file"] = "../escape.json"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SentinelError, match="safe immediate child|ledger"):
        adopt_nonempty_pair(tmp_path, run_root)


def test_adoption_rejects_linked_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "secret")
    result = run_sentinel(tmp_path, "20260812", session=Session())
    run_root = Path(result["run_root"]); manifest = json.loads((run_root / "manifest.json").read_text())
    landing = run_root / manifest["results"]["price_cap"]["landing_file"]
    outside = tmp_path / "outside.json"; outside.write_bytes(landing.read_bytes()); landing.unlink()
    try:
        landing.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(SentinelError, match="links/reparse"):
        adopt_nonempty_pair(tmp_path, run_root)


def test_confirmation_is_required(tmp_path):
    import subprocess, sys
    script = Path(__file__).parents[1] / "scripts/manual/data_go_kr_equity_availability_sentinel.py"
    result = subprocess.run([sys.executable, str(script), "--project-root", str(tmp_path), "--date", "20260812"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "explicit confirmation" in result.stderr
