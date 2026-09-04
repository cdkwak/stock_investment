import importlib.util
import json
from datetime import date, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "telegram_agent_bridge.py"
SPEC = importlib.util.spec_from_file_location("telegram_agent_bridge_conditions", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    root = (
        Path(__file__).resolve().parents[3]
        / ".tmp/agents/telegram-brief-a-style-20260903/condition-fixtures"
        / uuid4().hex
    )
    root.mkdir(parents=True)
    yield root


def test_close_brief_appends_condition_hits_and_never_blocks_on_failure(monkeypatch) -> None:
    sent: list[str] = []
    refreshes: list[str] = []

    class Client:
        def send(self, chat_id, text):
            sent.append(text)

    generated = (
        "마감 요약 본문\n"
        "출처: 거래소·Reuters\n"
        "※ 사실·시나리오 구분, 투자 조언 아님"
    )
    monkeypatch.setattr(bridge, "generate_market_report", lambda kind: generated)
    monkeypatch.setattr(
        bridge, "persist_market_report", lambda kind, report, ok, *args, **kwargs: None,
    )
    monkeypatch.setattr(bridge, "send_long_message", lambda client, chat_id, text: sent.append(text))
    monkeypatch.setattr(
        bridge,
        "refresh_close_watchlist_same_day",
        lambda: refreshes.append("close") or "completed · 0 calls",
    )
    monkeypatch.setattr(
        bridge, "watchlist_condition_summary",
        lambda **kwargs: "📌 관심종목 (09/02 마감 기준)\nSK하이닉스 1,693,000 ▲1.1% · 고점 -30%↓\n설명용 · 신호 아님",
    )
    monkeypatch.setattr(
        bridge, "changes_block",
        lambda *_args: "🔔 오늘 달라진 것 (09/02)\n규칙 0 · 조건 1/0\n켜짐 · SK하이닉스 RSI14 30 이하",
    )
    monkeypatch.setattr(bridge, "_changes_payload", lambda: {"as_of": "2026-09-02"})
    bridge.generate_send_and_persist_market_report(Client(), 1, "close")
    assert sent[-1].startswith("마감 요약 본문\n📌 관심종목 (09/02 마감 기준)")
    assert "켜짐 · SK하이닉스 RSI14 30 이하" in sent[-1]
    assert sent[-1].splitlines()[-2:] == [
        "출처: 거래소·Reuters", bridge.BRIEF_DISCLAIMER,
    ]

    def boom():
        raise RuntimeError("no retained data")

    monkeypatch.setattr(bridge, "watchlist_condition_summary", lambda **kwargs: boom())
    monkeypatch.setattr(bridge, "changes_block", lambda *_args: "")
    bridge.generate_send_and_persist_market_report(Client(), 1, "close")
    assert sent[-1] == bridge.normalize_brief(generated)

    sent.clear()
    monkeypatch.setattr(bridge, "write_investing_journal_draft", lambda day: None)
    bridge.generate_send_and_persist_market_report(Client(), 1, "morning")
    assert sent[-1] == bridge.normalize_brief(generated)
    assert refreshes == ["close", "close"]


def test_same_day_refresh_runs_only_after_cutoff_on_xkrx_session(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(bridge, "_retained_korean_close_max_as_of", lambda: None)
    monkeypatch.setattr(bridge, "_same_day_lane_call_ceiling", lambda lane, target: 2)
    monkeypatch.setattr(
        bridge,
        "_run_same_day_lane",
        lambda lane, now: calls.append(lane) or {"status": "PASS", "api_calls": 2},
    )

    before = datetime(2026, 9, 4, 15, 39, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr(bridge, "_is_xkrx_trading_day", lambda day: True)
    assert bridge.refresh_close_watchlist_same_day(before) == "skipped · before_1540"
    assert calls == []

    after = datetime(2026, 9, 4, 16, 10, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr(bridge, "_is_xkrx_trading_day", lambda day: False)
    assert bridge.refresh_close_watchlist_same_day(after) == "skipped · non_trading_day"
    assert calls == []

    monkeypatch.setattr(bridge, "_is_xkrx_trading_day", lambda day: True)
    assert bridge.refresh_close_watchlist_same_day(after) == "completed · 4 calls"
    assert calls == list(bridge.SAME_DAY_REFRESH_LANES)


def test_same_day_refresh_skips_current_retained_session_at_api_zero(monkeypatch) -> None:
    current = datetime(2026, 9, 4, 16, 10, tzinfo=ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr(bridge, "_is_xkrx_trading_day", lambda day: True)
    monkeypatch.setattr(
        bridge, "_retained_korean_close_max_as_of", lambda: current.date(),
    )
    monkeypatch.setattr(
        bridge,
        "_run_same_day_lane",
        lambda *args: pytest.fail("current retained data must be API zero"),
    )

    assert bridge.refresh_close_watchlist_same_day(current) == "skipped · already_current"


def test_same_day_refresh_refuses_a_plan_above_the_call_cap(monkeypatch) -> None:
    current = datetime(2026, 9, 4, 16, 10, tzinfo=ZoneInfo("Asia/Seoul"))
    calls: list[str] = []
    monkeypatch.setattr(bridge, "_is_xkrx_trading_day", lambda day: True)
    monkeypatch.setattr(bridge, "_retained_korean_close_max_as_of", lambda: None)
    monkeypatch.setattr(
        bridge,
        "_same_day_lane_call_ceiling",
        lambda lane, target: 6 if lane == "KR_EQUITY_PROVISIONAL_DAILY" else 9,  # 4 used + 9 > cap 12
    )
    monkeypatch.setattr(
        bridge,
        "_run_same_day_lane",
        lambda lane, now: calls.append(lane) or {"status": "PASS", "api_calls": 4},
    )

    with pytest.raises(bridge.SameDayRefreshBudgetError):
        bridge.refresh_close_watchlist_same_day(current)
    assert calls == ["KR_EQUITY_PROVISIONAL_DAILY"]


def test_close_refresh_failure_still_sends_and_persists_typed_status(monkeypatch) -> None:
    generated_at = datetime(2026, 9, 4, 16, 10, tzinfo=ZoneInfo("Asia/Seoul"))
    generated = (
        "마감 요약 본문\n"
        "출처: 거래소\n"
        "※ 사실·시나리오 구분, 투자 조언 아님"
    )
    sent: list[str] = []
    persisted: dict[str, object] = {}
    monkeypatch.setattr(bridge, "_kst_now", lambda: generated_at)
    monkeypatch.setattr(bridge, "generate_market_report", lambda kind: generated)
    monkeypatch.setattr(bridge, "_is_xkrx_trading_day", lambda day: True)
    monkeypatch.setattr(bridge, "_retained_korean_close_max_as_of", lambda: None)
    monkeypatch.setattr(bridge, "_same_day_lane_call_ceiling", lambda lane, target: 2)

    def fail_lane(lane, now):
        raise OSError("mock provider failure")

    monkeypatch.setattr(bridge, "_run_same_day_lane", fail_lane)
    monkeypatch.setattr(
        bridge,
        "watchlist_condition_summary",
        lambda **kwargs: "📌 관심종목 (09/04 잠정 마감 기준)\n삼성전자 1 ▲1.0% · RSI≤30\n설명용 · 신호 아님",
    )
    monkeypatch.setattr(bridge, "send_long_message", lambda client, chat_id, text: sent.append(text))

    def persist(kind, report, ok, *args, **kwargs):
        persisted.update(kind=kind, report=report, ok=ok, **kwargs)
        return None

    monkeypatch.setattr(bridge, "persist_market_report", persist)

    bridge.generate_send_and_persist_market_report(object(), 1, "close")

    assert sent and "마감 요약 본문" in sent[0]
    assert persisted["ok"] is True
    assert persisted["basis_date"] == "2026-09-04"
    assert persisted["sameday_refresh"] == "failed · OSError"


def test_condition_summary_formats_hits_from_stocks_table(monkeypatch) -> None:
    import types, sys

    fake = types.ModuleType("stock_web.api.stocks_page")
    fake.build_stocks_page_data = lambda root: {"table": [
        {"name": "삼성전자", "symbol": "005930", "price": 261000.0, "change_pct": 0.4, "condition_matches": [], "as_of": "2026-09-02"},
        {"name": "SK하이닉스", "symbol": "000660", "price": 1693000.0, "change_pct": 1.14,
         "as_of": "2026-09-02", "price_basis": "canonical",
         "condition_matches": [{"name": "52주 고점 대비 -30% 이하"}, {"name": "60일선 대비 -10% 이하"}, {"name": "RSI14 ≥ 70"}]},
        {"name": "SOXL", "symbol": "SOXL", "price": 105.91, "change_pct": -6.1,
         "as_of": "2026-09-02", "price_basis": "canonical",
         "condition_matches": [{"name": "하루 -5% 이하 급락"}, {"name": "RSI14 ≤ 30"}, {"name": "사용자 조건"}]},
    ]}
    monkeypatch.setitem(sys.modules, "stock_web.api.stocks_page", fake)
    text = bridge.watchlist_condition_summary(same_day_basis=True)
    assert "삼성전자" not in text
    assert text.startswith("📌 관심종목 (09/02 마감 기준)")
    assert "SK하이닉스 1,693,000 ▲1.1% · 고점 -30%↓ · 60일선 -10%↓ · RSI≥70" in text
    assert "SOXL 105.91 ▼6.1% · 일 -5%↓ · RSI≤30 · 사용자 조건" in text
    assert text.endswith("설명용 · 신호 아님")


def test_condition_summary_labels_provisional_and_mixed_row_dates(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "_watchlist_table", lambda: [
        {
            "name": "삼성전자", "symbol": "005930", "price": 70000.0,
            "change_pct": 1.2, "as_of": "2026-09-04", "price_basis": "provisional",
            "condition_matches": [{"name": "RSI14 ≤ 30"}],
        },
        {
            "name": "SOXL", "symbol": "SOXL", "price": 105.91,
            "change_pct": -6.1, "as_of": "2026-09-03", "price_basis": "canonical",
            "condition_matches": [{"name": "하루 -5% 이하 급락"}],
        },
    ])

    text = bridge.watchlist_condition_summary(same_day_basis=True)

    assert text.startswith("📌 관심종목 (09/04 잠정 마감 기준) · 일부 전일")
    assert "삼성전자 70,000 ▲1.2%" in text
    assert "SOXL 105.91 (09/03) ▼6.1%" in text

    conditions_text = bridge.watchlist_condition_summary()
    assert conditions_text.startswith("📌 관심종목 (09/04 마감 기준)")
    assert "잠정" not in conditions_text and "일부 전일" not in conditions_text
    assert "SOXL 105.91 ▼6.1%" in conditions_text


def test_close_basis_marks_all_previous_rows_after_failed_refresh(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "_watchlist_table", lambda: [
        {
            "name": "삼성전자", "symbol": "005930", "price": 70000.0,
            "change_pct": 1.2, "as_of": "2026-09-03", "price_basis": "canonical",
            "condition_matches": [{"name": "RSI14 ≤ 30"}],
        },
    ])

    text = bridge.watchlist_condition_summary(
        same_day_basis=True,
        basis_target=date(2026, 9, 4),
    )

    assert text.startswith("📌 관심종목 (09/03 마감 기준) · 일부 전일")
    assert "삼성전자 70,000 (09/03) ▲1.2%" in text


def test_condition_summary_limits_hit_rows_to_eight(monkeypatch) -> None:
    import sys
    import types

    fake = types.ModuleType("stock_web.api.stocks_page")
    fake.build_stocks_page_data = lambda root: {"table": [
        {
            "name": f"종목{index}", "price": 1000.0 + index,
            "change_pct": 0.1, "as_of": "2026-09-03",
            "condition_matches": [{"name": "RSI14 ≤ 30"}],
        }
        for index in range(10)
    ]}
    monkeypatch.setitem(sys.modules, "stock_web.api.stocks_page", fake)

    lines = bridge.watchlist_condition_summary().splitlines()

    assert len(lines) == 10  # header + eight hits + fixed explanation
    assert "종목7" in lines[-2]
    assert all("종목8" not in line and "종목9" not in line for line in lines)


def test_conditions_report_skips_codex_and_persists_only_sent_hits(
    monkeypatch, tmp_path: Path,
) -> None:
    generated_at = datetime(2026, 9, 3, 20, 50, tzinfo=ZoneInfo("Asia/Seoul"))
    block = (
        "📌 관심종목 (09/03 마감 기준)\n"
        "SK하이닉스 1,693,000 ▲1.1% · 고점 -30%↓\n"
        "설명용 · 신호 아님"
    )
    monkeypatch.setattr(bridge, "BRIEFS_ROOT", tmp_path / "briefs")
    monkeypatch.setattr(bridge, "_kst_now", lambda: generated_at)
    monkeypatch.setattr(bridge, "watchlist_condition_summary", lambda: block)
    monkeypatch.setattr(
        bridge, "generate_market_report",
        lambda kind: pytest.fail("conditions must not call Codex"),
    )

    class Client:
        def __init__(self):
            self.messages = []

        def send(self, chat_id, text):
            self.messages.append((chat_id, text))

    client = Client()
    assert bridge.run_market_report(client, 42, "conditions") == 0
    assert client.messages == [(42, block)]
    saved = tmp_path / "briefs" / "2026-09-03-conditions.md"
    contents = saved.read_text(encoding="utf-8")
    assert "kind: conditions\n" in contents
    assert "sent: true\n" in contents
    assert "model: local\n" in contents
    assert "basis_date: 2026-09-03\n" in contents
    assert "sameday_refresh: not_applicable\n" in contents
    assert contents.endswith(f"---\n\n{block}\n")


def test_conditions_report_with_no_hits_sends_and_persists_nothing(
    monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(bridge, "watchlist_condition_summary", lambda: "")
    monkeypatch.setattr(
        bridge, "generate_market_report",
        lambda kind: pytest.fail("conditions must not call Codex"),
    )
    monkeypatch.setattr(
        bridge, "persist_market_report",
        lambda *args: pytest.fail("no-hit conditions must not be persisted"),
    )

    class Client:
        def send(self, chat_id, text):
            pytest.fail("no-hit conditions must not be sent")

    assert bridge.run_market_report(Client(), 42, "conditions") == 0
    assert "report conditions skipped=no_hits" in capsys.readouterr().out


def test_conditions_send_failure_is_not_persisted(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "watchlist_condition_summary", lambda: "조건 블록")
    monkeypatch.setattr(
        bridge, "persist_market_report",
        lambda *args: pytest.fail("unsent conditions must not be persisted"),
    )

    class Client:
        def send(self, chat_id, text):
            raise bridge.BridgeError("mock send failure")

    with pytest.raises(bridge.BridgeError, match="mock send failure"):
        bridge.generate_send_and_persist_market_report(Client(), 42, "conditions")


def test_changes_report_deduplicates_state_file_after_first_send(
    monkeypatch, tmp_path: Path,
) -> None:
    state = tmp_path / "condition_state.json"
    payload = {
        "as_of": "2026-09-04",
        "condition_entries": [{
            "condition_id": "rsi30", "symbol": "000660",
            "name": "RSI14 30 이하", "display": "SK하이닉스 RSI14 30 이하",
        }],
        "condition_exits": [],
    }
    monkeypatch.setattr(bridge, "CONDITION_STATE_FILE", state)
    monkeypatch.setattr(bridge, "_changes_payload", lambda: payload)

    class Client:
        def __init__(self):
            self.messages = []

        def send(self, chat_id, text):
            self.messages.append((chat_id, text))

    client = Client()
    assert bridge.run_market_report(client, 42, "changes") == 0
    assert bridge.run_market_report(client, 42, "changes") == 0

    assert client.messages == [(42, "🔔 조건 켜짐: SK하이닉스 RSI14 30 이하")]
    assert json.loads(state.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "as_of": "2026-09-04",
        "entries": ["rsi30|000660"],
        "exits": [],
    }


def test_changes_report_keeps_state_and_sends_nothing_when_payload_is_unavailable(
    monkeypatch, tmp_path: Path,
) -> None:
    state = tmp_path / "condition_state.json"
    original = {
        "schema_version": 1, "as_of": "2026-09-04",
        "entries": ["rsi30|000660"], "exits": [],
    }
    state.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(bridge, "CONDITION_STATE_FILE", state)
    monkeypatch.setattr(bridge, "_changes_payload", lambda: {
        "as_of": None, "condition_entries": [], "condition_exits": [],
    })

    class Client:
        def send(self, chat_id, text):
            pytest.fail("an unavailable changes payload must not send Telegram")

    assert bridge.run_market_report(Client(), 42, "changes") == 1
    assert json.loads(state.read_text(encoding="utf-8")) == original
