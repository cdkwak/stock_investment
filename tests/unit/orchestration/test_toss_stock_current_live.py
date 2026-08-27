from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stock_data.orchestration.toss_stock_current_live import execute_toss_stock_current_quote
from stock_data.orchestration.toss_stock_nxt_close_ur241 import recover_retained_inferred_close, validate_nxt_session_close
from stock_data.providers.tossinvest import TossInvestAPIResponse, TossInvestHTTPError, TossInvestRateLimit


class _Client:
    def __init__(self, *, timestamp: str = "2026-08-21T10:30:00+09:00") -> None:
        self.token_request_count = 0
        self.market_request_count = 0
        self.timestamp = timestamp

    def get_market_data(self, path: str, *, params: dict[str, object]) -> TossInvestAPIResponse:
        assert path == "/api/v1/prices"
        assert params == {"symbols": "005930"}
        self.token_request_count += 1
        self.market_request_count += 1
        return TossInvestAPIResponse(200, {"result": [{
            "symbol": "005930", "timestamp": self.timestamp,
            "lastPrice": "70000", "currency": "KRW",
        }]}, TossInvestRateLimit("STOCK_PRICE"))


def test_durable_claim_precedes_factory_landing_promotion_and_api_zero_replay(tmp_path: Path) -> None:
    client = _Client()

    def factory() -> _Client:
        state = json.loads((tmp_path / "data/state/toss_stock_current_quote_ur141.json").read_text(encoding="utf-8"))
        assert state["attempts"]["2026-08-21"]["status"] == "ATTEMPTING"
        return client

    result = execute_toss_stock_current_quote(
        tmp_path, expected_market_date="2026-08-21", client_factory=factory,
        clock=lambda: datetime(2026, 8, 21, 1, 45, tzinfo=timezone.utc),
    )

    assert result.status == "COMPLETE"
    assert result.token_calls == result.business_calls == 1
    assert result.replay_api_calls == 0
    assert result.landing_file is not None and (tmp_path / result.landing_file).exists()
    replay = execute_toss_stock_current_quote(
        tmp_path, expected_market_date="2026-08-21", client_factory=None,
    )
    assert replay.status == "API_ZERO_REPLAY" and replay.token_calls == replay.business_calls == 0


def test_old_provider_timestamp_is_landing_retained_then_fails_closed_without_projection(tmp_path: Path) -> None:
    client = _Client(timestamp="2026-08-21T08:00:00+09:00")
    with pytest.raises(RuntimeError, match="60-minute age gate"):
        execute_toss_stock_current_quote(
            tmp_path, expected_market_date="2026-08-21", client_factory=lambda: client,
            clock=lambda: datetime(2026, 8, 21, 1, 45, tzinfo=timezone.utc),
        )

    state = json.loads((tmp_path / "data/state/toss_stock_current_quote_ur141.json").read_text(encoding="utf-8"))
    assert state["attempts"]["2026-08-21"]["status"] == "FAILED"
    assert client.token_request_count == client.market_request_count == 1
    assert list((tmp_path / "data/landing/tossinvest/stock_current_quote_ur141").glob("*.json"))
    assert not (tmp_path / "data/state/current_observations/toss_005930_price_snapshot.json").exists()
    with pytest.raises(RuntimeError, match="already attempted"):
        execute_toss_stock_current_quote(
            tmp_path, expected_market_date="2026-08-21", client_factory=lambda: client,
        )


def test_oauth_or_transport_failure_records_zero_business_calls_and_forbids_repeat(tmp_path: Path) -> None:
    class _FailureClient(_Client):
        def get_market_data(self, path: str, *, params: dict[str, object]) -> TossInvestAPIResponse:
            assert path == "/api/v1/prices" and params == {"symbols": "005930"}
            self.token_request_count += 1
            raise TossInvestHTTPError("synthetic sanitized transport failure")

    client = _FailureClient()
    with pytest.raises(TossInvestHTTPError):
        execute_toss_stock_current_quote(
            tmp_path, expected_market_date="2026-08-21", client_factory=lambda: client,
        )

    state = json.loads((tmp_path / "data/state/toss_stock_current_quote_ur141.json").read_text(encoding="utf-8"))
    attempt = state["attempts"]["2026-08-21"]
    assert attempt["status"] == "FAILED" and attempt["token_calls"] == 1 and attempt["business_calls"] == 0
    with pytest.raises(RuntimeError, match="already attempted"):
        execute_toss_stock_current_quote(
            tmp_path, expected_market_date="2026-08-21", client_factory=lambda: client,
        )


def test_distinct_ur239_symbol_paths_do_not_reuse_ur141_state(tmp_path: Path) -> None:
    class _Client000660:
        token_request_count = 0
        market_request_count = 0

        def get_market_data(self, path: str, *, params: dict[str, object]) -> TossInvestAPIResponse:
            assert path == "/api/v1/prices" and params == {"symbols": "000660"}
            self.token_request_count += 1; self.market_request_count += 1
            return TossInvestAPIResponse(200, {"result": [{
                "symbol": "000660", "timestamp": "2026-08-21T10:30:00+09:00",
                "lastPrice": "250000", "currency": "KRW",
            }]}, TossInvestRateLimit("STOCK_PRICE"))

    result = execute_toss_stock_current_quote(
        tmp_path, expected_market_date="2026-08-21", client_factory=_Client000660,
        symbol="000660", state_path=Path("data/state/toss_stock_current_quote_ur239.json"),
        projection_path=Path("data/state/current_observations/toss_000660_price_snapshot_ur239.json"),
        landing_root=Path("data/landing/tossinvest/stock_current_quote_ur239"),
        clock=lambda: datetime(2026, 8, 21, 1, 45, tzinfo=timezone.utc),
    )
    assert result.status == "COMPLETE" and result.token_calls == result.business_calls == 1
    assert (tmp_path / "data/state/toss_stock_current_quote_ur239.json").exists()
    assert not (tmp_path / "data/state/toss_stock_current_quote_ur141.json").exists()


def test_ur241_accepts_only_explicit_nxt_session_close(tmp_path: Path) -> None:
    class _NxtClient:
        token_request_count = 0
        market_request_count = 0

        def get_market_data(self, path: str, *, params: dict[str, object]) -> TossInvestAPIResponse:
            assert path == "/api/v1/prices" and params == {"symbols": "005930"}
            self.token_request_count += 1; self.market_request_count += 1
            return TossInvestAPIResponse(200, {"result": [{"symbol": "005930", "timestamp": "2026-08-21T19:59:59+09:00", "lastPrice": "70000", "currency": "KRW"}]}, TossInvestRateLimit("STOCK_PRICE"))

    result = execute_toss_stock_current_quote(
        tmp_path, expected_market_date="2026-08-21", client_factory=_NxtClient,
        state_path=Path("data/state/toss_stock_nxt_close_ur241.json"),
        projection_path=Path("data/state/current_observations/toss_005930_nxt_close_ur241.json"),
        landing_root=Path("data/landing/tossinvest/stock_nxt_close_ur241"),
        acceptance_validator=validate_nxt_session_close, route_suffix=":TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
        clock=lambda: datetime(2026, 8, 21, 13, 25, tzinfo=timezone.utc),
    )
    assert result.status == "COMPLETE"
    state = json.loads((tmp_path / "data/state/toss_stock_nxt_close_ur241.json").read_text(encoding="utf-8"))
    assert state["attempts"]["2026-08-21"]["route_id"].endswith("TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW")


def test_ur241_retained_landing_recovery_is_api_zero_and_marks_inferred_venue(tmp_path: Path) -> None:
    state_path = tmp_path / "data/state/toss_stock_nxt_close_ur241.json"
    landing_path = tmp_path / "data/landing/tossinvest/stock_nxt_close_ur241/retained.json"
    landing_path.parent.mkdir(parents=True)
    raw = {"result": [{
        "symbol": "005930", "timestamp": "2026-08-21T19:59:59+09:00",
        "lastPrice": "70000", "currency": "KRW",
    }]}
    landing_path.write_text(json.dumps({
        "captured_at_utc": "2026-08-21T13:30:00+00:00",
        "raw_response": raw,
        "raw_sha256": hashlib.sha256(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
    }), encoding="utf-8")
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"schema_version": 1, "attempts": {
        "2026-08-21": {
            "status": "FAILED", "token_calls": 1, "business_calls": 1,
            "landing_file": "data/landing/tossinvest/stock_nxt_close_ur241/retained.json",
        },
    }}), encoding="utf-8")

    result = recover_retained_inferred_close(
        tmp_path, expected_date="2026-08-21", expected_landing_sha256=hashlib.sha256(landing_path.read_bytes()).hexdigest(),
    )

    assert result["status"] == "RETAINED_API_ZERO_INFERRED_NXT_CLOSE"
    assert result["replay_api_calls"] == 0
    recovered = json.loads(state_path.read_text(encoding="utf-8"))["attempts"]["2026-08-21"]["retained_api_zero_recovery"]
    assert recovered["venue_inferred"] is True and recovered["not_live"] is True
    assert recovered["external_api_calls"] == 0
    projection = json.loads((tmp_path / "data/state/current_observations/toss_005930_nxt_close_ur241.json").read_text(encoding="utf-8"))
    assert projection["observations"][0]["unit"] == "KRW per share"


def test_ur241_retained_recovery_landing_hash_mismatch_fails_before_projection(tmp_path: Path) -> None:
    state_path = tmp_path / "data/state/toss_stock_nxt_close_ur241.json"
    landing_path = tmp_path / "data/landing/tossinvest/stock_nxt_close_ur241/retained.json"
    landing_path.parent.mkdir(parents=True)
    landing_path.write_text(json.dumps({
        "captured_at_utc": "2026-08-21T13:30:00+00:00",
        "raw_response": {"result": [{"symbol": "005930", "timestamp": "2026-08-21T19:59:59+09:00", "lastPrice": "70000", "currency": "KRW"}]},
        "raw_sha256": "synthetic-raw-hash",
    }), encoding="utf-8")
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"schema_version": 1, "attempts": {"2026-08-21": {
        "status": "FAILED", "token_calls": 1, "business_calls": 1,
        "landing_file": "data/landing/tossinvest/stock_nxt_close_ur241/retained.json",
    }}}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Landing file hash mismatch"):
        recover_retained_inferred_close(tmp_path, expected_date="2026-08-21", expected_landing_sha256="0" * 64)
    assert not (tmp_path / "data/state/current_observations/toss_005930_nxt_close_ur241.json").exists()
