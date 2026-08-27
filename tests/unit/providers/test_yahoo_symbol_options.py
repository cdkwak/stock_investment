from datetime import datetime, timedelta, timezone
import copy
import json

import pytest

from stock_data.gui.us_option_pcr_adapter import (
    USOptionPCRDisplayState,
    yahoo_symbol_option_pcr_scope_views,
)
from stock_data.providers.yahoo_symbol_options import (
    ALL_PILOT_SYMBOLS,
    SymbolOptionPCRStatus,
    YahooSymbolOptionError,
    derive_yahoo_symbol_volume_pcr,
    parse_yahoo_option_chain,
)
from scripts.manual.pilot.pilot_yahoo_symbol_options import (
    EXPIRY_EPOCH as PILOT_EXPIRY,
    LATEST_COMPLETED_XNYS_SESSION,
    evaluate_retained_body,
    replay_run,
    run_live,
    write_checkpoint,
)
from scripts.manual.pilot import pilot_yahoo_option_requests_fallback as requests_fallback


CAPTURED = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)
EXPIRY = 1_789_689_600


def _row(symbol: str, side: str, strike: int, volume, *, size: str = "REGULAR"):
    encoded_strike = f"{strike * 1000:08d}"
    return {
        "contractSymbol": f"{symbol}270917{side}{encoded_strike}",
        "lastTradeDate": 1_787_200_000,
        "strike": strike,
        "volume": volume,
        "openInterest": 100,
        "contractSize": size,
    }


def _payload(symbol: str = "SPY", *, call_volume=20, put_volume=30):
    return {
        "optionChain": {
            "error": None,
            "result": [{
                "quote": {"symbol": symbol, "regularMarketTime": 1_787_210_000},
                "options": [{
                    "expirationDate": EXPIRY,
                    "calls": [
                        _row(symbol, "C", 500, call_volume),
                        _row(symbol, "C", 510, 999, size="MINI"),
                    ],
                    "puts": [_row(symbol, "P", 500, put_volume)],
                }],
            }],
        },
    }


def test_offline_chain_parser_preserves_expiry_capture_and_contract_classification():
    snapshot = parse_yahoo_option_chain(_payload(), symbol="SPY", captured_at_utc=CAPTURED)
    assert snapshot.symbol == "SPY"
    assert snapshot.captured_at_utc == CAPTURED
    assert len(snapshot.contracts) == 3
    assert [row.contract_size for row in snapshot.contracts] == ["REGULAR", "MINI", "REGULAR"]
    assert snapshot.contracts[0].side == "CALL"
    assert snapshot.contracts[-1].side == "PUT"


def test_per_symbol_volume_pcr_excludes_nonstandard_and_never_aggregates_symbols():
    spy = parse_yahoo_option_chain(_payload(), symbol="SPY", captured_at_utc=CAPTURED)
    result = derive_yahoo_symbol_volume_pcr(
        "SPY", [spy], multiplier_verified_symbols=frozenset({"SPY"}),
        now_utc=CAPTURED,
    )
    assert result.status is SymbolOptionPCRStatus.AVAILABLE
    assert result.value == 1.5
    assert result.call_volume == 20
    assert result.put_volume == 30
    assert result.excluded_nonstandard_count == 1
    assert result.backtest_eligible is False

    qqq = parse_yahoo_option_chain(_payload("QQQ"), symbol="QQQ", captured_at_utc=CAPTURED)
    with pytest.raises(YahooSymbolOptionError, match="cross-symbol aggregation"):
        derive_yahoo_symbol_volume_pcr(
            "SPY", [spy, qqq], multiplier_verified_symbols=frozenset({"SPY"}),
            now_utc=CAPTURED,
        )


def test_multiplier_missing_volume_zero_side_and_staleness_fail_closed():
    snapshot = parse_yahoo_option_chain(_payload(), symbol="SPY", captured_at_utc=CAPTURED)
    assert derive_yahoo_symbol_volume_pcr("SPY", [snapshot], now_utc=CAPTURED).status is (
        SymbolOptionPCRStatus.MULTIPLIER_UNVERIFIED
    )

    missing = parse_yahoo_option_chain(
        _payload(call_volume=None), symbol="SPY", captured_at_utc=CAPTURED,
    )
    result = derive_yahoo_symbol_volume_pcr(
        "SPY", [missing], multiplier_verified_symbols=frozenset({"SPY"}), now_utc=CAPTURED,
    )
    assert result.status is SymbolOptionPCRStatus.MISSING_VOLUME
    assert result.value is None

    zero = parse_yahoo_option_chain(
        _payload(call_volume=0), symbol="SPY", captured_at_utc=CAPTURED,
    )
    result = derive_yahoo_symbol_volume_pcr(
        "SPY", [zero], multiplier_verified_symbols=frozenset({"SPY"}), now_utc=CAPTURED,
    )
    assert result.status is SymbolOptionPCRStatus.INSUFFICIENT_LIQUIDITY
    assert result.value is None

    result = derive_yahoo_symbol_volume_pcr(
        "SPY", [snapshot], multiplier_verified_symbols=frozenset({"SPY"}),
        now_utc=datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc),
    )
    assert result.status is SymbolOptionPCRStatus.STALE
    assert result.value is None


def test_parser_rejects_wrong_symbol_duplicate_contract_and_price_only_option_probe():
    with pytest.raises(YahooSymbolOptionError, match="does not match"):
        parse_yahoo_option_chain(_payload("QQQ"), symbol="SPY", captured_at_utc=CAPTURED)
    payload = _payload()
    option = payload["optionChain"]["result"][0]["options"][0]
    option["puts"][0]["contractSymbol"] = option["calls"][0]["contractSymbol"]
    with pytest.raises(YahooSymbolOptionError, match="duplicate"):
        parse_yahoo_option_chain(payload, symbol="SPY", captured_at_utc=CAPTURED)
    with pytest.raises(YahooSymbolOptionError, match="outside option-chain pilot"):
        parse_yahoo_option_chain(_payload("DRAM"), symbol="DRAM", captured_at_utc=CAPTURED)


def test_gui_adapter_lists_every_symbol_separately_and_never_creates_market_total():
    snapshot = parse_yahoo_option_chain(_payload(), symbol="SPY", captured_at_utc=CAPTURED)
    spy = derive_yahoo_symbol_volume_pcr(
        "SPY", [snapshot], multiplier_verified_symbols=frozenset({"SPY"}), now_utc=CAPTURED,
    )
    views = yahoo_symbol_option_pcr_scope_views((spy,))
    assert len(views) == len(ALL_PILOT_SYMBOLS)
    assert len({view.scope_id for view in views}) == len(views)
    assert all("TOTAL" not in view.scope_id and "MARKET" not in view.scope_id for view in views)
    indexed = {view.scope_id: view for view in views}
    assert indexed["YAHOO_SPY"].display_state is USOptionPCRDisplayState.VALUE
    assert indexed["YAHOO_SPY"].value == 1.5
    assert indexed["YAHOO_SPY"].usage_status == "DASHBOARD_RESEARCH_ONLY_NOT_BACKTEST"
    assert indexed["YAHOO_QQQ"].display_state is USOptionPCRDisplayState.SUPPRESSED
    assert indexed["YAHOO_QQQ"].value is None
    assert indexed["YAHOO_DRAM"].display_state is USOptionPCRDisplayState.SUPPRESSED
    assert indexed["YAHOO_SKHY"].value is None


def test_price_only_symbols_remain_numeric_free_for_option_pcr():
    for symbol in ("DRAM", "SKHY"):
        result = derive_yahoo_symbol_volume_pcr(symbol, ())
        assert result.status is SymbolOptionPCRStatus.PRICE_ONLY
        assert result.value is None
        assert result.displays_value is False


def test_pilot_retained_evaluation_requires_exact_osi_identity_and_latest_two_sided_trade():
    trade_at = int(datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc).timestamp())
    payload = _payload()
    option = payload["optionChain"]["result"][0]["options"][0]
    option["expirationDate"] = PILOT_EXPIRY
    for side, marker in (("calls", "C"), ("puts", "P")):
        for index, row in enumerate(option[side]):
            row["contractSymbol"] = f"SPY260918{marker}{(500 + index * 10) * 1000:08d}"
            row["lastTradeDate"] = trade_at
    body = __import__("json").dumps(payload).encode("utf-8")
    result = evaluate_retained_body(
        body,
        symbol="SPY",
        captured_at_utc=CAPTURED,
        landing_sha256="a" * 64,
    )
    assert result["status"] == SymbolOptionPCRStatus.AVAILABLE.value
    assert result["trade_freshness"]["passed"] is True
    assert result["trade_freshness"]["required_latest_completed_xnys_session"] == (
        LATEST_COMPLETED_XNYS_SESSION.isoformat()
    )


def test_pilot_retained_evaluation_hides_stale_trade_and_rejects_adjusted_root():
    payload = _payload()
    option = payload["optionChain"]["result"][0]["options"][0]
    option["expirationDate"] = PILOT_EXPIRY
    for side, marker in (("calls", "C"), ("puts", "P")):
        for index, row in enumerate(option[side]):
            row["contractSymbol"] = f"SPY260918{marker}{(500 + index * 10) * 1000:08d}"
    body = __import__("json").dumps(payload).encode("utf-8")
    result = evaluate_retained_body(
        body,
        symbol="SPY",
        captured_at_utc=CAPTURED,
        landing_sha256="b" * 64,
    )
    assert result["status"] == "STALE_TRADE_EVIDENCE"
    assert result["value"] is None

    option["calls"][0]["contractSymbol"] = "SPY1260918C00500000"
    with pytest.raises(YahooSymbolOptionError, match="adjusted or cross-symbol"):
        evaluate_retained_body(
            __import__("json").dumps(payload).encode("utf-8"),
            symbol="SPY",
            captured_at_utc=CAPTURED,
            landing_sha256="c" * 64,
        )


def test_pilot_retains_request_timing_allows_symbol_empty_and_replays_at_api_zero(tmp_path):
    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self.content = json.dumps(payload).encode("utf-8")
            self.headers = {"Content-Type": "application/json"}

    class Session:
        def __init__(self):
            self.calls = []
            self.responses = [
                Response(200, {"optionChain": {"error": None, "result": []}}),
                Response(403, {"finance": {"error": {"code": "Forbidden"}}}),
            ]

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return self.responses.pop(0)

    start = datetime(2026, 8, 20, 11, 15, tzinfo=timezone.utc)
    moments = iter(start + timedelta(seconds=index) for index in range(6))
    session = Session()
    live = run_live(session=session, now_fn=lambda: next(moments), landing_root=tmp_path)

    assert live["calls_consumed"] == 2
    assert live["retry_count"] == 0
    assert live["global_stop_reason"] == "HTTP_RESTRICTION:403"
    assert live["results"]["SPY"]["status"] == "MALFORMED_OR_EMPTY"
    assert live["results"]["QQQ"]["status"] == "GLOBAL_TRANSPORT_STOP"
    assert live["not_attempted_symbols"] == ["IWM", "TLT", "SOXX", "SOXL", "TQQQ"]
    spy_metadata = json.loads(
        (tmp_path / live["capture_id"] / "SPY" / f"{PILOT_EXPIRY}.metadata.json").read_text()
    )
    assert spy_metadata["request_started_at_utc"] < spy_metadata["request_ended_at_utc"]

    replay = replay_run(tmp_path / live["capture_id"])
    assert replay["api_calls"] == 0
    assert replay["retry_count"] == 0
    assert replay["replay_matches_live"] is True

    artifact = tmp_path / "checkpoint.json"
    checkpoint = write_checkpoint(tmp_path / live["capture_id"], artifact)
    assert checkpoint["live_ledger"]["calls_consumed"] == 2
    assert checkpoint["api_zero_replay"]["api_calls"] == 0
    assert artifact.exists()


def test_ur098_requests_fallback_retains_only_schema_valid_success_and_replays_api_zero(
    tmp_path, monkeypatch,
):
    expiry = int((datetime.now(timezone.utc) + timedelta(days=14)).timestamp())
    row = {
        "contractSymbol": "QQQ260904C00400000",
        "expirationDate": expiry,
        "lastTradeDate": int(datetime.now(timezone.utc).timestamp()) - 1,
        "strike": 400.0,
        "bid": 1.0,
        "ask": 1.1,
        "impliedVolatility": 0.2,
        "volume": 10,
        "openInterest": 20,
    }
    payload = {
        "optionChain": {
            "error": None,
            "result": [{
                "quote": {
                    "symbol": "QQQ", "currency": "USD",
                    "exchangeTimezoneName": "America/New_York",
                },
                "expirationDates": [expiry],
                "options": [{"expirationDate": expiry, "calls": [row], "puts": [dict(row, contractSymbol="QQQ260904P00400000")] }],
            }],
        },
    }

    class Response:
        status_code = 200
        content = json.dumps(payload).encode("utf-8")

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    monkeypatch.setattr(requests_fallback.requests, "Session", lambda: session)
    artifact = tmp_path / "checkpoint.json"
    result = requests_fallback.run_live(landing_root=tmp_path / "landing", artifact_path=artifact)

    assert result["outcome"] == "AS_RETRIEVED_SCHEMA_VALID_NUMERIC_DATA_NOT_ACCEPTED"
    assert result["business_calls_consumed"] == 2
    assert result["numeric_data_accepted"] is False
    assert len(session.calls) == 2
    assert session.calls[1][0].startswith(requests_fallback.BASE_URL + "?date=")
    monkeypatch.setattr(
        requests_fallback, "_utc_now",
        lambda: datetime.fromtimestamp(expiry + 24 * 60 * 60, tz=timezone.utc),
    )
    assert requests_fallback.replay(artifact_path=artifact, landing_root=tmp_path / "landing") == {
        "api_calls": 0, "retry_count": 0, "replay_valid": True,
    }
    assert len(session.calls) == 2

    checkpoint = json.loads(artifact.read_text(encoding="utf-8"))
    metadata = (
        tmp_path / "landing" / checkpoint["capture_id"] / "nearest_chain" / "metadata.json"
    )
    metadata.write_text("{}", encoding="utf-8")
    with pytest.raises(requests_fallback.PilotValidationError, match="LANDING_READBACK_HASH_MISMATCH"):
        requests_fallback.replay(artifact_path=artifact, landing_root=tmp_path / "landing")


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("expirationDate", 9, "ROW_EXPIRY_MISMATCH"),
        ("lastTradeDate", 0, "LAST_TRADE_DATE_INVALID"),
        ("lastTradeDate", 1_800_000_001, "LAST_TRADE_DATE_AFTER_CAPTURE"),
        ("strike", float("nan"), "STRIKE_INVALID"),
        ("bid", 2.0, "CROSSED_BID_ASK"),
        ("volume", 1.5, "VOLUME_INVALID"),
        ("openInterest", 1.5, "OPEN_INTEREST_INVALID"),
        ("impliedVolatility", -0.1, "IMPLIEDVOLATILITY_INVALID"),
    ],
)
def test_ur098_requests_fallback_chain_row_gates_are_fail_closed(field, value, reason):
    expiry = 1_800_000_000
    row = {
        "contractSymbol": "QQQ270115C00400000",
        "expirationDate": expiry,
        "lastTradeDate": 1_700_000_000,
        "strike": 400.0,
        "bid": 1.0,
        "ask": 1.1,
        "impliedVolatility": 0.2,
        "volume": 10,
        "openInterest": 20,
    }
    row[field] = value
    payload = {
        "optionChain": {
            "error": None,
            "result": [{
                "quote": {
                    "symbol": "QQQ", "currency": "USD",
                    "exchangeTimezoneName": "America/New_York",
                },
                "options": [{
                    "expirationDate": expiry,
                    "calls": [row],
                    "puts": [dict(copy.deepcopy(row), contractSymbol="QQQ270115P00400000")],
                }],
            }],
        },
    }
    with pytest.raises(requests_fallback.PilotValidationError, match=reason):
        requests_fallback.validate_nearest_chain(
            payload,
            nearest_expiry=expiry,
            captured_at_utc=datetime.fromtimestamp(1_800_000_000, tz=timezone.utc),
        )


def test_ur098_requests_fallback_requires_explicit_integral_row_expiry():
    expiry = 1_800_000_000
    row = {
        "contractSymbol": "QQQ270115C00400000",
        "expirationDate": expiry,
        "lastTradeDate": 1_700_000_000,
        "strike": 400.0,
        "bid": 1.0,
        "ask": 1.1,
        "impliedVolatility": 0.2,
        "volume": 10,
        "openInterest": 20,
    }
    payload = {
        "optionChain": {
            "error": None,
            "result": [{
                "quote": {
                    "symbol": "QQQ", "currency": "USD",
                    "exchangeTimezoneName": "America/New_York",
                },
                "options": [{
                    "expirationDate": expiry,
                    "calls": [row],
                    "puts": [dict(copy.deepcopy(row), contractSymbol="QQQ270115P00400000")],
                }],
            }],
        },
    }
    captured_at = datetime.fromtimestamp(expiry, tz=timezone.utc)
    missing = copy.deepcopy(payload)
    for side in ("calls", "puts"):
        missing["optionChain"]["result"][0]["options"][0][side][0].pop("expirationDate")
    with pytest.raises(requests_fallback.PilotValidationError, match="ROW_EXPIRY_MISSING"):
        requests_fallback.validate_nearest_chain(
            missing, nearest_expiry=expiry, captured_at_utc=captured_at,
        )

    invalid = copy.deepcopy(payload)
    for side in ("calls", "puts"):
        invalid["optionChain"]["result"][0]["options"][0][side][0]["expirationDate"] = "invalid"
    with pytest.raises(requests_fallback.PilotValidationError, match="ROW_EXPIRY_INVALID"):
        requests_fallback.validate_nearest_chain(
            invalid, nearest_expiry=expiry, captured_at_utc=captured_at,
        )


def test_ur098_requests_fallback_does_not_retain_unauthorized_response_body(tmp_path, monkeypatch):
    class Response:
        status_code = 401
        content = b"do-not-retain"

    class Session:
        def get(self, url, **kwargs):
            return Response()

    monkeypatch.setattr(requests_fallback.requests, "Session", Session)
    artifact = tmp_path / "checkpoint.json"
    result = requests_fallback.run_live(landing_root=tmp_path / "landing", artifact_path=artifact)

    assert result["outcome"] == "TESTED_ROUTE_UNAUTHORIZED"
    assert result["business_calls_consumed"] == 1
    assert not (tmp_path / "landing" / result["capture_id"]).exists()
