from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.registry import CONTRACTS
from stock_data.contracts.tossinvest_historical import (
    KR_MARKET_INVESTOR_TRADING_DAILY,
    TOSSINVEST_HISTORICAL_CONTRACTS,
)
from stock_data.pipelines.tossinvest_historical import (
    _atomic_json,
    backfill_toss_targets,
)
from stock_data.providers.tossinvest.client import (
    TossInvestAPIResponse,
    TossInvestHTTPDiagnostics,
    TossInvestRateLimit,
    TossInvestRateLimitError,
    TossInvestTimeoutError,
)
from stock_data.providers.tossinvest.historical import (
    MARKET_INVESTOR_OPERATION,
    normalize_credit_trading,
    normalize_market_investor,
    normalize_program_trading,
    normalize_securities_lending,
    normalize_short_selling,
    normalize_treasury_yield,
)
from stock_data.validation.tossinvest_historical import validate_toss_historical


FIXTURE = Path(__file__).parent / "fixtures" / "tossinvest_market_live.json"
OBSERVED = datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc)


def operation(name):
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["operations"][name]["response"]["result"]


def test_contracts_are_registered_separate_normalized_sources():
    assert len(TOSSINVEST_HISTORICAL_CONTRACTS) == 6
    assert all(contract.name in CONTRACTS for contract in TOSSINVEST_HISTORICAL_CONTRACTS)
    assert all(contract.source == "tossinvest_open_api" for contract in TOSSINVEST_HISTORICAL_CONTRACTS)
    common = {"source", "source_operation", "source_date", "collected_at", "updated_at", "availability_date"}
    assert all(common <= set(contract.column_names) for contract in TOSSINVEST_HISTORICAL_CONTRACTS)
    market = KR_MARKET_INVESTOR_TRADING_DAILY
    assert not any("net" in name for name in market.column_names)
    assert market.status == "active"
    assert TOSSINVEST_HISTORICAL_CONTRACTS[-1].status == "active"
    assert all(
        contract.status == "draft_blocked"
        for contract in TOSSINVEST_HISTORICAL_CONTRACTS[1:5]
    )


def test_fixture_normalizes_market_and_all_stock_datasets():
    market = normalize_market_investor(operation("market_investor_trading")["records"], market="KOSPI", collected_at=OBSERVED)
    short = normalize_short_selling(operation("stock_short_selling")["records"], symbol="005930", collected_at=OBSERVED)
    program = normalize_program_trading(operation("stock_program_trades")["records"], symbol="005930", collected_at=OBSERVED)
    lending = normalize_securities_lending(operation("stock_securities_lending")["records"], symbol="005930", collected_at=OBSERVED)
    credit = normalize_credit_trading(operation("stock_credit_trades")["records"], symbol="005930", collected_at=OBSERVED)
    assert len(market) == len(short) == len(program) == len(lending) == len(credit) == 2
    assert market.iloc[0]["institution_financial_investment_buy_amount"] >= 0
    assert program.iloc[0]["updated_at"] is None
    assert program.iloc[0]["availability_date"] is None
    assert short.iloc[0]["availability_date"] == short.iloc[0]["source_date"]
    assert credit["availability_date"].ge(credit["source_date"]).all()
    for frame, contract in zip((market, short, program, lending, credit), TOSSINVEST_HISTORICAL_CONTRACTS[:5]):
        validate_toss_historical(frame, contract)


def test_treasury_fixture_normalizes_ohlc_and_preserves_zero_volume():
    records = operation("market_indicator_daily_candles")["candles"]
    records[0]["volume"] = "0"
    frame = normalize_treasury_yield(records, instrument="KR_BOND_10Y", collected_at=OBSERVED)
    assert frame["volume"].eq(0).any()
    assert frame.iloc[0]["availability_date"] is None
    validate_toss_historical(frame, TOSSINVEST_HISTORICAL_CONTRACTS[-1])


def test_validator_enforces_availability_only_when_updated_at_exists():
    market = normalize_market_investor(
        operation("market_investor_trading")["records"],
        market="KOSPI",
        collected_at=OBSERVED,
    )
    missing = market.copy()
    missing.loc[missing.index[0], "availability_date"] = None
    with pytest.raises(ValueError, match="required with source updated_at"):
        validate_toss_historical(missing, KR_MARKET_INVESTOR_TRADING_DAILY)

    treasury = normalize_treasury_yield(
        operation("market_indicator_daily_candles")["candles"],
        instrument="KR_BOND_10Y",
        collected_at=OBSERVED,
    )
    guessed = treasury.copy()
    guessed.loc[guessed.index[0], "availability_date"] = guessed.iloc[0]["date"]
    with pytest.raises(ValueError, match="requires source updated_at"):
        validate_toss_historical(guessed, TOSSINVEST_HISTORICAL_CONTRACTS[-1])


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.token_request_count = 1
        self.market_request_count = 0

    def get_market_data(self, path, params=None):
        self.market_request_count += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def api(records, cursor, remaining=9):
    return TossInvestAPIResponse(
        200,
        {"result": {"records": records, "nextUntil": cursor}},
        TossInvestRateLimit("MARKET_INDICATOR", 10, remaining, 1),
    )


def market_record(day):
    pair = {"buyAmount": "0", "sellAmount": "0"}
    breakdown = {key: pair for key in (
        "financialInvestment", "insurance", "trust", "privateEquityFund", "bank",
        "otherFinancialInstitution", "pensionFund",
    )}
    return {"date": day, "updatedAt": day + "T18:00:00+09:00", "individual": pair,
            "foreigner": pair, "institution": {**pair, "breakdown": breakdown},
            "otherCorporation": pair}


def test_pipeline_uses_cursor_landing_checkpoint_resume_and_zero_preservation(tmp_path):
    client = FakeClient([api([market_record("2026-08-07")], "2026-08-06"), api([market_record("2026-08-06")], None)])
    result = backfill_toss_targets(
        tmp_path, client=client, contract=KR_MARKET_INVESTOR_TRADING_DAILY,
        targets=["KOSPI"], endpoint_for_target=lambda target: f"/api/v1/market-indicators/{target}/investor-trading",
        base_params={"interval": "1d", "count": 100}, cursor_parameter="until",
        cursor_key="nextUntil", row_key="records", operation=MARKET_INVESTOR_OPERATION,
        normalize_for_target=lambda target, rows, observed: normalize_market_investor(rows, market=target, collected_at=observed),
        batch_size=1,
    )
    assert result.status == "complete" and result.market_calls == 2 and result.rows == 2
    assert len(list((tmp_path / "data/landing/tossinvest").rglob("*.json"))) == 2
    stored = pd.concat([pd.read_parquet(path) for path in (tmp_path / "data/normalized/kr_market_investor_trading_daily").rglob("data.parquet")])
    assert len(stored) == 2 and stored["individual_buy_amount"].eq(0).all()
    second = backfill_toss_targets(
        tmp_path, client=FakeClient([]), contract=KR_MARKET_INVESTOR_TRADING_DAILY,
        targets=["KOSPI"], endpoint_for_target=lambda target: "unused",
        base_params={"interval": "1d", "count": 100}, cursor_parameter="until",
        cursor_key="nextUntil", row_key="records", operation=MARKET_INVESTOR_OPERATION,
        normalize_for_target=lambda target, rows, observed: normalize_market_investor(rows, market=target, collected_at=observed),
    )
    assert second.market_calls == 0 and second.completed_targets == 1


def test_429_stops_without_retry_and_checkpoint_is_preserved(tmp_path):
    error = TossInvestRateLimitError("limited", details=TossInvestHTTPDiagnostics(http_status=429))
    client = FakeClient([error])
    result = backfill_toss_targets(
        tmp_path, client=client, contract=KR_MARKET_INVESTOR_TRADING_DAILY,
        targets=["KOSPI"], endpoint_for_target=lambda target: "endpoint",
        base_params={"interval": "1d", "count": 100}, cursor_parameter="until",
        cursor_key="nextUntil", row_key="records", operation=MARKET_INVESTOR_OPERATION,
        normalize_for_target=lambda target, rows, observed: normalize_market_investor(rows, market=target, collected_at=observed),
    )
    assert result.status == "stopped_429" and result.market_calls == 1
    state = json.loads(next((tmp_path / "data/state").glob("*.json")).read_text())
    assert state["status"] == "stopped_429" and "KOSPI" in state["progress"]


def test_landing_rejects_secret_fields(tmp_path):
    with pytest.raises(ValueError, match="sensitive"):
        _atomic_json(tmp_path / "bad.json", {"access_token": "must-not-land"})


def test_timeout_retries_once_then_completes(tmp_path):
    client = FakeClient([
        TossInvestTimeoutError("timeout"),
        api([market_record("2026-08-07")], None),
    ])
    result = backfill_toss_targets(
        tmp_path, client=client, contract=KR_MARKET_INVESTOR_TRADING_DAILY,
        targets=["KOSPI"], endpoint_for_target=lambda target: "endpoint",
        base_params={"interval": "1d", "count": 100}, cursor_parameter="until",
        cursor_key="nextUntil", row_key="records", operation=MARKET_INVESTOR_OPERATION,
        normalize_for_target=lambda target, rows, observed: normalize_market_investor(
            rows, market=target, collected_at=observed
        ), batch_size=1,
    )
    assert result.status == "complete" and result.market_calls == 2


def test_validator_rejects_duplicate_primary_key():
    frame = normalize_market_investor(
        [market_record("2026-08-07")], market="KOSPI", collected_at=OBSERVED
    )
    duplicate = pd.concat([frame, frame], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        validate_toss_historical(duplicate, KR_MARKET_INVESTOR_TRADING_DAILY)


def test_validator_rejects_institution_breakdown_mismatch():
    frame = normalize_market_investor(
        [market_record("2026-08-07")], market="KOSPI", collected_at=OBSERVED
    )
    frame.loc[0, "institution_bank_buy_amount"] = 2
    with pytest.raises(ValueError, match="breakdown"):
        validate_toss_historical(frame, KR_MARKET_INVESTOR_TRADING_DAILY)
