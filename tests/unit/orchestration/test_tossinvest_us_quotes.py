from __future__ import annotations

from datetime import datetime, timezone
import json

import pandas as pd
import pytest

from stock_data.orchestration.tossinvest_us_quotes import (
    ARTIFACT_PATH,
    CADENCE_GROUP,
    DATASET_PATH,
    TOSSINVEST_US_QUOTE_30M,
    TOSSINVEST_US_QUOTE_SYMBOLS,
    run_us_quote_lane,
    validate_tossinvest_us_quote_30m,
)
from stock_data.providers.tossinvest.client import (
    TossInvestAPIResponse,
    TossInvestHTTPDiagnostics,
    TossInvestRateLimit,
    TossInvestRateLimitError,
    TossInvestResponseError,
)
from stock_data.storage.contract_parquet import read_dataset


NOW = datetime(2026, 9, 4, 12, 41, tzinfo=timezone.utc)


def _payload(symbols=TOSSINVEST_US_QUOTE_SYMBOLS):
    return {"result": [{
        "symbol": symbol,
        "timestamp": "2026-09-04T21:41:00.000+09:00",
        "lastPrice": str(164.5 + index),
        "currency": "USD",
    } for index, symbol in enumerate(symbols)]}


class Client:
    def __init__(self, payload=None):
        self.payload = _payload() if payload is None else payload
        self.calls = []

    def get_market_data(self, path, *, params=None):
        self.calls.append((path, params))
        return TossInvestAPIResponse(
            200, self.payload,
            TossInvestRateLimit(group="STOCK_PRICE", limit=15),
        )


def test_toss_us_quote_dry_run_lists_filtered_registry_without_client(tmp_path) -> None:
    result = run_us_quote_lane(tmp_path, now=NOW, dry_run=True)
    assert result == {
        "lane": "TOSSINVEST_US_QUOTES_30M",
        "cadence_group": "GLOBAL_30M",
        "window_kst": "[17:00,06:00)",
        "symbols": list(TOSSINVEST_US_QUOTE_SYMBOLS),
        "retry_count": 0,
        "status": "DRY_RUN_PASS",
        "api_calls": 0,
    }
    assert CADENCE_GROUP == "GLOBAL_30M"


def test_toss_us_quote_lane_writes_exact_artifact_and_appends_all_symbols(tmp_path) -> None:
    client = Client()
    result = run_us_quote_lane(tmp_path, now=NOW, client=client)
    assert result["status"] == "COMPLETE"
    assert result["api_calls"] == 1
    assert result["rows_appended"] == len(TOSSINVEST_US_QUOTE_SYMBOLS)
    assert client.calls == [(
        "/api/v1/prices",
        {"symbols": ",".join(TOSSINVEST_US_QUOTE_SYMBOLS)},
    )]

    artifact = json.loads((tmp_path / ARTIFACT_PATH).read_text(encoding="utf-8"))
    assert set(artifact) == {"as_of_kst", "provider", "session_hint", "quotes"}
    assert artifact["as_of_kst"] == "2026-09-04T21:41:00+09:00"
    assert artifact["provider"] == "tossinvest"
    assert artifact["session_hint"] == "pre_market"
    assert len(artifact["quotes"]) == len(TOSSINVEST_US_QUOTE_SYMBOLS)
    assert all(set(row) == {"symbol", "last_price", "currency", "timestamp_kst"}
               for row in artifact["quotes"])

    retained = read_dataset(
        tmp_path / DATASET_PATH,
        TOSSINVEST_US_QUOTE_30M,
        validate_tossinvest_us_quote_30m,
    )
    assert retained["symbol"].tolist() == sorted(TOSSINVEST_US_QUOTE_SYMBOLS)
    assert retained["currency"].eq("USD").all()
    assert pd.to_datetime(retained["timestamp_kst"], utc=True).notna().all()


def test_toss_us_quote_valid_empty_preserves_existing_artifact(tmp_path) -> None:
    artifact = tmp_path / ARTIFACT_PATH
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"prior":"valid"}\n', encoding="utf-8")
    result = run_us_quote_lane(tmp_path, now=NOW, client=Client({"result": []}))
    assert result["status"] == "VALID_EMPTY_PRESERVED"
    assert result["api_calls"] == 1
    assert json.loads(artifact.read_text(encoding="utf-8")) == {"prior": "valid"}
    assert not (tmp_path / DATASET_PATH).exists()


def test_toss_us_quote_captures_landing_before_payload_validation(tmp_path) -> None:
    invalid = _payload(("SKHY",))
    with pytest.raises(TossInvestResponseError, match="omitted"):
        run_us_quote_lane(tmp_path, now=NOW, client=Client(invalid))

    landings = list((tmp_path / "data/landing/tossinvest/us_quotes_30m").rglob(
        "response.json"
    ))
    assert len(landings) == 1
    assert json.loads(landings[0].read_text(encoding="utf-8"))["response"] == invalid
    assert not (tmp_path / ARTIFACT_PATH).exists()
    assert not (tmp_path / DATASET_PATH).exists()


def test_toss_us_quote_429_skips_without_retry_or_write(tmp_path) -> None:
    class RateLimitedClient:
        calls = 0

        def get_market_data(self, path, *, params=None):
            self.calls += 1
            raise TossInvestRateLimitError(
                "rate limited",
                details=TossInvestHTTPDiagnostics(
                    http_status=429,
                    rate_limit=TossInvestRateLimit(
                        group="STOCK_PRICE", limit=15, retry_after_seconds=1,
                    ),
                ),
            )

    client = RateLimitedClient()
    result = run_us_quote_lane(tmp_path, now=NOW, client=client)
    assert result["status"] == "SKIPPED_RATE_LIMIT"
    assert result["api_calls"] == client.calls == 1
    assert result["retry_after_seconds"] == 1
    assert not (tmp_path / ARTIFACT_PATH).exists()
    assert not (tmp_path / DATASET_PATH).exists()
