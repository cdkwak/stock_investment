from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest

from stock_data.providers.bok_ecos_fx_daily import (
    BokEcosFxProviderError,
    capture_range,
    parse_response,
    redacted_route,
)


NOW = datetime(2026, 9, 3, 8, 10, tzinfo=timezone.utc)


def _temp_root() -> Path:
    root = Path(__file__).parents[3] / ".tmp/agents/bok_fx_daily_20260903/fixtures" / uuid4().hex
    root.mkdir(parents=True)
    return root


def _payload(*dates: str) -> dict[str, object]:
    rows = [{
        "STAT_CODE": "731Y001",
        "STAT_NAME": "3.1.2.1. 주요국 통화의 대원화환율",
        "ITEM_CODE1": "0000001",
        "ITEM_NAME1": "원/미국달러(매매기준율)",
        "UNIT_NAME": "원",
        "TIME": value,
        "DATA_VALUE": "1,337.50",
    } for value in dates]
    return {"StatisticSearch": {"list_total_count": len(rows), "row": rows}}


def _body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_parse_documented_fixture_rows() -> None:
    parsed = parse_response(
        _body(_payload("20260902", "20260903")),
        start=date(2026, 9, 2), end=date(2026, 9, 3), retrieved_at=NOW,
    )

    assert parsed.result_code == "SUCCESS"
    assert parsed.frame["date"].astype(str).tolist() == ["2026-09-02", "2026-09-03"]
    assert parsed.frame["rate_krw_per_usd"].tolist() == [1337.5, 1337.5]
    assert set(parsed.frame["item_code"]) == {"0000001"}
    assert set(parsed.frame["stat_code"]) == {"731Y001"}


def test_info_200_is_valid_no_data() -> None:
    parsed = parse_response(
        _body({"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다"}}),
        start=date(2026, 9, 3), end=date(2026, 9, 3), retrieved_at=NOW,
    )

    assert parsed.no_data is True
    assert parsed.frame.empty


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"StatisticSearch": {"list_total_count": 1, "row": []}},
        {"StatisticSearch": {"list_total_count": 1, "row": [{"TIME": "20260903"}]}},
        _payload("20260904"),
        {"RESULT": {"CODE": "ERROR-001", "MESSAGE": "failure"}},
    ],
)
def test_malformed_or_error_response_fails_closed(payload: dict[str, object]) -> None:
    with pytest.raises(BokEcosFxProviderError):
        parse_response(
            _body(payload), start=date(2026, 9, 3), end=date(2026, 9, 3),
            retrieved_at=NOW,
        )


def test_capture_is_landing_first_redacted_and_read_back() -> None:
    tmp_path = _temp_root()
    key = "secret-test-key"

    class Response:
        status_code = 200
        content = _body(_payload("20260903"))

    class Session:
        def get(self, url: str, *, timeout: int):
            assert key in url and timeout == 30
            return Response()

    captured = capture_range(
        tmp_path, start=date(2026, 9, 3), end=date(2026, 9, 3),
        api_key=key, session=Session(), retrieved_at=NOW,
    )

    assert captured.api_calls == 1
    assert json.loads((captured.run_dir / "manifest.json").read_text(encoding="utf-8"))["status"] == "VALIDATED"
    assert redacted_route(date(2026, 9, 3), date(2026, 9, 3)) in (
        captured.run_dir / "call_ledger.jsonl"
    ).read_text(encoding="utf-8")
    assert all(key.encode() not in path.read_bytes() for path in captured.run_dir.iterdir())
