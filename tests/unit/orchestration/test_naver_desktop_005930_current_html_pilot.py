from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stock_data.orchestration.naver_desktop_005930_current_html_pilot import (
    NaverDesktopHtmlResponse,
    execute_naver_desktop_005930_current_html,
)


def _body(timestamp: str = "2026-08-21T14:30:00+09:00") -> bytes:
    payload = {"symbol": "005930", "venue": "KRX", "price": "71200", "unit": "KRW per share",
               "provider_timestamp": timestamp, "session": "OPEN", "delay_seconds": 0}
    return ("<script id=\"naver-current-observation\" type=\"application/json\">" + json.dumps(payload) + "</script>").encode()


def test_preclaim_landing_projection_and_api_zero_replay(tmp_path: Path) -> None:
    calls = 0
    def transport(url: str, timeout: int) -> NaverDesktopHtmlResponse:
        nonlocal calls
        state = json.loads((tmp_path / "data/state/naver_desktop_005930_current_html_ur174.json").read_text(encoding="utf-8"))
        assert state["attempts"]["2026-08-21"]["status"] == "ATTEMPTING"
        assert url.endswith("code=005930") and timeout == 10
        calls += 1
        return NaverDesktopHtmlResponse(200, _body())
    clock = lambda: datetime(2026, 8, 21, 5, 31, tzinfo=timezone.utc)
    result = execute_naver_desktop_005930_current_html(tmp_path, expected_market_date="2026-08-21", transport=transport, clock=clock)
    assert result.status == "COMPLETE" and result.raw_gets == 1 and result.replay_api_calls == 0 and calls == 1
    assert result.landing_file and (tmp_path / result.landing_file).is_file()
    replay = execute_naver_desktop_005930_current_html(tmp_path, expected_market_date="2026-08-21", transport=None, clock=clock)
    assert replay.status == "API_ZERO_REPLAY" and replay.raw_gets == replay.replay_api_calls == 0 and calls == 1


@pytest.mark.parametrize("body", [_body("2026-08-21T13:00:00+09:00"), b"<html>no explicit schema</html>"])
def test_stale_or_schema_failure_retains_landing_but_no_projection_and_replays_api_zero(tmp_path: Path, body: bytes) -> None:
    clock = lambda: datetime(2026, 8, 21, 5, 31, tzinfo=timezone.utc)
    with pytest.raises(Exception):
        execute_naver_desktop_005930_current_html(tmp_path, expected_market_date="2026-08-21", transport=lambda _u, _t: NaverDesktopHtmlResponse(200, body), clock=clock)
    state = json.loads((tmp_path / "data/state/naver_desktop_005930_current_html_ur174.json").read_text(encoding="utf-8"))
    assert state["attempts"]["2026-08-21"]["status"] == "FAILED"
    assert state["attempts"]["2026-08-21"]["landing_file"]
    assert not (tmp_path / "data/state/current_observations/naver_desktop_005930_current.json").exists()
    replay = execute_naver_desktop_005930_current_html(tmp_path, expected_market_date="2026-08-21", transport=None, clock=clock)
    assert replay.status == "API_ZERO_REPLAY_FAILURE" and replay.raw_gets == 0


def test_transport_http_and_landing_failure_preserve_prior_and_forbid_second_get(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = lambda: datetime(2026, 8, 21, 5, 31, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        execute_naver_desktop_005930_current_html(tmp_path, expected_market_date="2026-08-21", transport=lambda _u, _t: NaverDesktopHtmlResponse(503, b"ignored"), clock=clock)
    replay = execute_naver_desktop_005930_current_html(tmp_path, expected_market_date="2026-08-21", transport=None, clock=clock)
    assert replay.status == "API_ZERO_REPLAY_FAILURE"

    other = tmp_path / "landing-failure"
    import stock_data.orchestration.naver_desktop_005930_current_html_pilot as pilot
    monkeypatch.setattr(pilot, "_write_landing", lambda *_args: (_ for _ in ()).throw(OSError("synthetic landing failure")))
    with pytest.raises(OSError, match="landing failure"):
        execute_naver_desktop_005930_current_html(other, expected_market_date="2026-08-21", transport=lambda _u, _t: NaverDesktopHtmlResponse(200, _body()), clock=clock)
    assert not (other / "data/state/current_observations/naver_desktop_005930_current.json").exists()
