from datetime import datetime, timezone
import json

from stock_data.orchestration.naver_equity_ur199_windows import IDENTITIES, WINDOW_IDS, ensure_manifest, is_active, runner, selected_boundary


class Response:
    def __init__(self, status_code: int, content: bytes) -> None: self.status_code, self.content = status_code, content


def payload(symbol: str, time_text: str) -> bytes:
    return json.dumps({"itemCode": symbol, "closePrice": "100,000", "marketStatus": "OPEN", "localTradedAt": time_text, "delayTime": 0, "stockExchangeType": {"code": "KS", "zoneId": "Asia/Seoul", "nationType": "KOR", "stockType": "domestic", "delayTime": 0, "startTime": "0900", "endTime": "1530"}}).encode()


def test_manifest_is_exact_and_pre_date_never_constructs_transport(tmp_path) -> None:
    ensure_manifest(tmp_path); calls: list[str] = []
    result = runner(tmp_path).run(now=datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc), response_factories={identity: lambda: calls.append(identity) for identity in IDENTITIES})
    assert not is_active(tmp_path, now=datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc))
    assert result.api_calls == 0 and calls == [] and set(result.statuses) == set(IDENTITIES)


def test_first_resume_boundary_is_0930_and_serial_order_is_fixed(tmp_path) -> None:
    ensure_manifest(tmp_path); calls: list[str] = []
    now = datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc)
    assert is_active(tmp_path, now=now) and WINDOW_IDS[0] == "2026-08-24T09:30:00+09:00"
    result = runner(tmp_path).run(now=now, response_factories={identity: (lambda item=identity: calls.append(item) or Response(200, payload(item, "2026-08-24T09:30:00+09:00"))) for identity in IDENTITIES})
    assert calls == list(IDENTITIES) and result.api_calls == 2


def test_semantic_failure_preserves_prior_and_second_identity_continues(tmp_path) -> None:
    ensure_manifest(tmp_path); now = datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc); calls: list[str] = []
    result = runner(tmp_path).run(now=now, response_factories={"000660": lambda: calls.append("000660") or Response(200, b"{}"), "005930": lambda: calls.append("005930") or Response(200, payload("005930", "2026-08-24T09:30:00+09:00"))})
    assert calls == ["000660", "005930"] and result.statuses["000660"] == "COMPLETE_SEMANTIC_FAILURE" and result.statuses["005930"] == "COMPLETE"


def test_orphan_is_no_repeat_and_replay_is_api_zero(tmp_path) -> None:
    ensure_manifest(tmp_path); now = datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc)
    first = runner(tmp_path).run(now=now, response_factories={identity: (lambda item=identity: Response(200, payload(item, "2026-08-24T09:30:00+09:00"))) for identity in IDENTITIES})
    repeated = runner(tmp_path).run(now=now, response_factories={identity: lambda: (_ for _ in ()).throw(AssertionError("no repeat")) for identity in IDENTITIES})
    assert first.api_calls == 2 and set(repeated.statuses.values()) == {"NO_REPEAT"} and repeated.api_calls == 0


def test_next_window_semantic_failure_preserves_exact_prior_bytes(tmp_path) -> None:
    ensure_manifest(tmp_path); first = datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc)
    runner(tmp_path).run(now=first, response_factories={identity: (lambda item=identity: Response(200, payload(item, "2026-08-24T09:30:00+09:00"))) for identity in IDENTITIES})
    prior = (tmp_path / "data/state/current_observations/naver_mobile_basic_000660_ur199.json").read_bytes()
    later = datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc)
    result = runner(tmp_path).run(now=later, response_factories={"000660": lambda: Response(200, b"{}"), "005930": lambda: Response(500, b"")})
    assert result.statuses["000660"] == "COMPLETE_SEMANTIC_FAILURE" and (tmp_path / "data/state/current_observations/naver_mobile_basic_000660_ur199.json").read_bytes() == prior


def test_half_open_due_selection_never_backfills(tmp_path) -> None:
    ensure_manifest(tmp_path)
    for minute, expected in ((30, "2026-08-24T09:30:00+09:00"), (31, "2026-08-24T09:30:00+09:00"), (59, "2026-08-24T09:30:00+09:00"), (0, "2026-08-24T10:00:00+09:00")):
        hour = 0 if minute else 1
        now = datetime(2026, 8, 24, hour, minute, 59, tzinfo=timezone.utc)
        assert selected_boundary(tmp_path, now=now) == expected
