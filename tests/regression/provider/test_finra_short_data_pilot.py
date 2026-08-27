from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from stock_data.providers.finra import FinraSchemaError, parse_daily_short_sale_volume, parse_short_interest


DAILY = b"Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n20250815|SPY|100|0|200|Q\n20250815|AAPL|10|1|20|Q\n"
SHORT = json.dumps([
    {"issueSymbolIdentifier": "SPY", "settlementDate": "2025-08-15", "currentShortShareNumber": 5,
     "averageShortShareNumber": 12, "marketCategoryCode": "Q"},
    {"issueSymbolIdentifier": "AAPL", "settlementDate": "2025-08-15", "currentShortShareNumber": 7,
     "averageShortShareNumber": 14, "marketCategoryCode": "Q"},
]).encode()


def _script_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "manual" / "pilot" / "pilot_finra_short_data_landing.py"
    spec = importlib.util.spec_from_file_location("finra_pilot", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_daily_parser_requires_exact_schema_and_keys():
    parsed = parse_daily_short_sale_volume(DAILY)
    assert len(parsed.rows) == 2
    assert parsed.rows[0]["Symbol"] == "SPY"
    assert parsed.schema_sha256 == hashlib.sha256(
        b'["Date","Symbol","ShortVolume","ShortExemptVolume","TotalVolume","Market"]'
    ).hexdigest()
    with pytest.raises(FinraSchemaError, match="unexpected daily short-sale schema"):
        parse_daily_short_sale_volume(b"Date|Symbol\n20250815|SPY\n")


def test_short_interest_parser_rejects_conflicting_duplicate():
    parsed = parse_short_interest(SHORT)
    assert parsed.rows[0]["settlementDate"] == "2025-08-15"
    duplicate = json.dumps([
        {"issueSymbolIdentifier": "SPY", "settlementDate": "2025-08-15", "currentShortShareNumber": 1,
         "averageShortShareNumber": 1},
        {"issueSymbolIdentifier": "SPY", "settlementDate": "2025-08-15", "currentShortShareNumber": 2,
         "averageShortShareNumber": 1},
    ]).encode()
    with pytest.raises(FinraSchemaError, match="conflicting duplicate"):
        parse_short_interest(duplicate)


class _Response:
    def __init__(self, status: int, content: bytes, method: str, headers: dict[str, str] | None = None):
        self.status_code = status
        self.content = content
        self.headers = headers or {"Content-Type": "application/json"}
        self.request = type("Request", (), {"method": method})()


class _Session:
    def get(self, *args, **kwargs):
        return _Response(200, DAILY, "GET", {"Content-Type": "text/plain"})

    def post(self, *args, **kwargs):
        return _Response(200, SHORT, "POST")


class _BadDailySession:
    def get(self, *args, **kwargs):
        return _Response(200, DAILY.replace(b"|100|", b"|unknown|"), "GET", {"Content-Type": "text/plain"})

    def post(self, *args, **kwargs):
        raise AssertionError("short-interest request must not follow a daily schema anomaly")


def test_mocked_pilot_landing_preserves_raw_in_each_independent_family(tmp_path, monkeypatch):
    module = _script_module()
    monkeypatch.setattr(module, "LANDING_ROOT", tmp_path / "data" / "landing" / "finra")
    monkeypatch.setattr(module, "STATE_ROOT", tmp_path / "data" / "state")
    result = module.run(
        family="daily_short_sale_volume", trade_date="20250815", settlement_date="2025-08-15", session=_Session(),
    )
    assert result["status"] == "PILOT_COMPLETE_WITH_LIMITS"
    assert len(result["captures"]) == 1
    run_dir = module.LANDING_ROOT / "daily_short_sale_volume_pilot" / result["run_id"]
    daily_body = run_dir / "daily_short_sale_volume" / "response.body"
    assert daily_body.read_bytes() == DAILY
    daily_provenance = json.loads((daily_body.with_name("provenance.json")).read_text())
    assert daily_provenance["body_sha256"] == hashlib.sha256(DAILY).hexdigest()
    assert (module.STATE_ROOT / "us_finra_daily_short_sale_volume_pilot" / "latest.json").is_file()

    short_result = module.run(
        family="short_interest", trade_date="20250815", settlement_date="2025-08-15", session=_Session(),
    )
    assert short_result["status"] == "PILOT_COMPLETE_WITH_LIMITS"
    short_dir = module.LANDING_ROOT / "short_interest_pilot" / short_result["run_id"]
    interest_body = short_dir / "short_interest" / "response.body"
    assert interest_body.read_bytes() == SHORT
    interest_provenance = json.loads((interest_body.with_name("provenance.json")).read_text())
    assert interest_provenance["body_sha256"] == hashlib.sha256(SHORT).hexdigest()
    assert (module.STATE_ROOT / "us_finra_short_interest_pilot" / "latest.json").is_file()


def test_schema_failure_retains_received_raw_without_retry(tmp_path, monkeypatch):
    module = _script_module()
    monkeypatch.setattr(module, "LANDING_ROOT", tmp_path / "data" / "landing" / "finra")
    monkeypatch.setattr(module, "STATE_ROOT", tmp_path / "data" / "state")
    result = module.run(
        family="daily_short_sale_volume", trade_date="20250815", settlement_date="2025-08-15", session=_BadDailySession(),
    )
    assert result["status"] == "PILOT_STOPPED_SCHEMA_ANOMALY"
    assert len(result["captures"]) == 1
    run_dir = module.LANDING_ROOT / "daily_short_sale_volume_pilot" / result["run_id"]
    body = run_dir / "daily_short_sale_volume" / "response.body"
    provenance = json.loads((body.with_name("provenance.json")).read_text())
    assert b"unknown" in body.read_bytes()
    assert provenance["validation_status"] == "FAILED_CLOSED"
