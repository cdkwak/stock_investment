from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from stock_data.providers.tossinvest import (
    TossInvestAPIResponse, TossInvestHTTPError, TossInvestRateLimit,
)
from stock_data.orchestration.toss_market_current_live import execute_toss_kospi_current_pilot


class _Client:
    def __init__(self, timestamp: str = "2026-08-21T10:30:00+09:00") -> None:
        self.token_request_count = 0
        self.market_request_count = 0
        self.timestamp = timestamp

    def get_market_data(self, path: str, *, params: dict[str, object]) -> TossInvestAPIResponse:
        assert path == "/api/v1/market-indicators/prices"
        assert params == {"symbols": "KOSPI"}
        self.token_request_count += 1
        self.market_request_count += 1
        return TossInvestAPIResponse(200, {"result": [{
            "symbol": "KOSPI", "timestamp": self.timestamp, "lastPrice": "3210.5",
        }]}, TossInvestRateLimit("MARKET_INDICATOR"))


def test_one_exact_toss_route_is_landing_first_atomic_and_replays_api_zero(tmp_path: Path) -> None:
    client = _Client()
    result = execute_toss_kospi_current_pilot(
        tmp_path, expected_market_date="2026-08-21", client=client,
        clock=lambda: datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc),
    )

    assert result.status == "COMPLETE"
    assert result.token_calls == result.market_calls == 1
    assert result.replay_api_calls == 0
    assert result.landing_file is not None and (tmp_path / result.landing_file).exists()
    replay = execute_toss_kospi_current_pilot(tmp_path, expected_market_date="2026-08-21", client=None)
    assert replay.status == "API_ZERO_REPLAY" and replay.token_calls == replay.market_calls == 0


def test_wrong_provider_kst_date_is_retained_then_fail_closed_without_projection(tmp_path: Path) -> None:
    client = _Client("2026-08-20T15:30:00+09:00")
    with pytest.raises(RuntimeError, match="unexpected KST market date"):
        execute_toss_kospi_current_pilot(tmp_path, expected_market_date="2026-08-21", client=client)

    state = (tmp_path / "data/state/toss_market_current_observation_pilot.json").read_text(encoding="utf-8")
    assert '"status": "FAILED"' in state
    assert client.token_request_count == client.market_request_count == 1
    assert list((tmp_path / "data/landing/tossinvest/current_observation").glob("*.json"))
    with pytest.raises(RuntimeError, match="already attempted"):
        execute_toss_kospi_current_pilot(tmp_path, expected_market_date="2026-08-21", client=client)


def test_oauth_or_transport_stop_records_one_token_zero_market_and_forbids_repeat(tmp_path: Path) -> None:
    class _OAuthFailureClient(_Client):
        def get_market_data(self, path: str, *, params: dict[str, object]) -> TossInvestAPIResponse:
            assert path == "/api/v1/market-indicators/prices" and params == {"symbols": "KOSPI"}
            self.token_request_count += 1
            raise TossInvestHTTPError("sanitized synthetic transport failure")

    client = _OAuthFailureClient()
    with pytest.raises(TossInvestHTTPError):
        execute_toss_kospi_current_pilot(tmp_path, expected_market_date="2026-08-21", client=client)

    state = (tmp_path / "data/state/toss_market_current_observation_pilot.json").read_text(encoding="utf-8")
    assert '"token_calls": 1' in state and '"market_calls": 0' in state
    assert not (tmp_path / "data/landing/tossinvest/current_observation").exists()
    with pytest.raises(RuntimeError, match="already attempted"):
        execute_toss_kospi_current_pilot(tmp_path, expected_market_date="2026-08-21", client=client)
