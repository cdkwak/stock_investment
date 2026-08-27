from __future__ import annotations

from datetime import date
import json

import pytest

from stock_data.providers.pykrx.kr_index_fundamental_daily import (
    IndexFundamentalProviderError,
    capture_index_fundamental_range,
)


def _body(day: str) -> bytes:
    return json.dumps({"output": [{
        "TRD_DD": day, "CLSPRC_IDX": "3,200.0", "WT_PER": "15.0",
        "WT_STKPRC_NETASST_RTO": "1.2", "DIV_YD": "1.0",
    }]}).encode()


def test_credentials_fail_before_default_provider_access(tmp_path, monkeypatch):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *_args, **_kwargs: pytest.fail("network called before credential gate"),
    )
    with pytest.raises(IndexFundamentalProviderError, match="credentials"):
        capture_index_fundamental_range(
            date(2026, 8, 13), date(2026, 8, 13), run_id="missing-creds",
            landing_root=tmp_path / "landing", env_file=tmp_path / "absent.env",
        )
    assert not (tmp_path / "landing").exists()


def test_injected_two_call_capture_is_immutable_and_retry_zero(tmp_path):
    calls = []

    def fetch(index_code, start, end):
        calls.append((index_code, start, end))
        return _body("2026/08/13")

    result = capture_index_fundamental_range(
        date(2026, 8, 13), date(2026, 8, 13), run_id="bounded",
        landing_root=tmp_path / "landing", env_file=tmp_path / "unused",
        body_fetcher=fetch,
    )
    assert [item[0] for item in calls] == ["1001", "2001"]
    assert result.business_calls == 2 and result.retry_count == 0
    assert len(result.responses) == 2
    with pytest.raises(IndexFundamentalProviderError, match="already"):
        capture_index_fundamental_range(
            date(2026, 8, 13), date(2026, 8, 13), run_id="bounded",
            landing_root=tmp_path / "landing", env_file=tmp_path / "unused",
            body_fetcher=fetch,
        )
    assert len(calls) == 2


def test_valid_empty_is_distinct_and_stops_after_first_market(tmp_path):
    calls = []

    def empty(index_code, start, end):
        calls.append(index_code)
        return b'{"output": []}'

    with pytest.raises(IndexFundamentalProviderError, match="valid empty"):
        capture_index_fundamental_range(
            date(2026, 8, 13), date(2026, 8, 13), run_id="valid-empty",
            landing_root=tmp_path / "landing", env_file=tmp_path / "unused",
            body_fetcher=empty,
        )
    assert calls == ["1001"]
    assert (tmp_path / "landing/valid-empty/kospi.json").is_file()
