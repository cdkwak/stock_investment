from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest

from stock_data.providers.bok_ecos_market_rates_daily import (
    BokEcosMarketRatesProviderError,
    SERIES_SPECS,
    capture_range,
    parse_response,
    redacted_route,
)


NOW = datetime(2026, 9, 6, 1, 2, tzinfo=timezone.utc)


def _payload(series: str, *dates: str) -> dict[str, object]:
    spec = SERIES_SPECS[series]
    rows = [{
        "STAT_CODE": "817Y002",
        "STAT_NAME": "1.3.2.1. 시장금리(일별)",
        "ITEM_CODE1": spec["item_code"],
        "ITEM_NAME1": spec["item_name"],
        "UNIT_NAME": "연%",
        "TIME": value,
        "DATA_VALUE": "12.345",
    } for value in dates]
    return {"StatisticSearch": {"list_total_count": len(rows), "row": rows}}


def _body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("series", tuple(SERIES_SPECS))
def test_parse_synthetic_rows_keeps_series_separate(series: str) -> None:
    parsed = parse_response(
        _body(_payload(series, "20010203")),
        series=series,
        start=date(2001, 2, 1),
        end=date(2001, 2, 10),
        retrieved_at=NOW,
    )
    assert parsed.result_code == "SUCCESS"
    assert parsed.frame[["date", "series", "rate_percent"]].to_dict("records") == [{
        "date": date(2001, 2, 3), "series": series, "rate_percent": 12.345,
    }]


def test_parse_rejects_cross_series_identity_and_truncation() -> None:
    payload = _payload("CALL_RATE_OVERNIGHT", "20010203")
    payload["StatisticSearch"]["row"][0]["ITEM_CODE1"] = "010300000"
    with pytest.raises(BokEcosMarketRatesProviderError, match="item identity"):
        parse_response(
            _body(payload), series="CALL_RATE_OVERNIGHT",
            start=date(2001, 2, 1), end=date(2001, 2, 10), retrieved_at=NOW,
        )
    payload = _payload("CALL_RATE_OVERNIGHT", "20010203")
    payload["StatisticSearch"]["list_total_count"] = 2
    with pytest.raises(BokEcosMarketRatesProviderError, match="truncated"):
        parse_response(
            _body(payload), series="CALL_RATE_OVERNIGHT",
            start=date(2001, 2, 1), end=date(2001, 2, 10), retrieved_at=NOW,
        )


def test_capture_is_landing_first_and_never_persists_key() -> None:
    tmp_path = (
        Path(__file__).parents[3]
        / ".tmp/agents/bok_ecos_market_rates_20260906/provider_tests"
        / uuid4().hex
    )
    tmp_path.mkdir(parents=True)
    key = "synthetic-secret"

    class Response:
        status_code = 200
        content = _body(_payload("CALL_RATE_OVERNIGHT", "20010203"))

    class Session:
        def get(self, url: str, *, timeout: int):
            assert key in url and timeout == 30
            return Response()

    captured = capture_range(
        tmp_path,
        series="CALL_RATE_OVERNIGHT",
        start=date(2001, 2, 1),
        end=date(2001, 2, 10),
        api_key=key,
        session=Session(),
        retrieved_at=NOW,
    )
    assert "data/landing/bok_ecos/kr_market_rates_daily" in captured.run_dir.as_posix()
    assert redacted_route(
        "CALL_RATE_OVERNIGHT", date(2001, 2, 1), date(2001, 2, 10),
    ) in (captured.run_dir / "call_ledger.jsonl").read_text(encoding="utf-8")
    assert all(key.encode() not in path.read_bytes() for path in captured.run_dir.iterdir())


def test_info_200_is_valid_empty() -> None:
    parsed = parse_response(
        _body({"RESULT": {"CODE": "INFO-200", "MESSAGE": "synthetic no data"}}),
        series="CORP_BOND_3Y_AA_MINUS",
        start=date(1987, 1, 1), end=date(1987, 1, 2), retrieved_at=NOW,
    )
    assert parsed.no_data and parsed.frame.empty
