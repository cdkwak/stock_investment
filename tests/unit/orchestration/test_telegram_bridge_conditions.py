import importlib.util
from datetime import datetime
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

    class Client:
        def send(self, chat_id, text):
            sent.append(text)

    generated = (
        "마감 요약 본문\n"
        "출처: 거래소·Reuters\n"
        "※ 사실·시나리오 구분, 투자 조언 아님"
    )
    monkeypatch.setattr(bridge, "generate_market_report", lambda kind: generated)
    monkeypatch.setattr(bridge, "persist_market_report", lambda kind, report, ok: None)
    monkeypatch.setattr(bridge, "send_long_message", lambda client, chat_id, text: sent.append(text))
    monkeypatch.setattr(
        bridge, "watchlist_condition_summary",
        lambda: "📌 관심종목 (09/02 마감 기준)\nSK하이닉스 1,693,000 ▲1.1% · 고점 -30%↓\n설명용 · 신호 아님",
    )
    bridge.generate_send_and_persist_market_report(Client(), 1, "close")
    assert sent[-1].startswith("마감 요약 본문\n📌 관심종목 (09/02 마감 기준)")
    assert sent[-1].splitlines()[-2:] == [
        "출처: 거래소·Reuters", bridge.BRIEF_DISCLAIMER,
    ]

    def boom():
        raise RuntimeError("no retained data")

    monkeypatch.setattr(bridge, "watchlist_condition_summary", boom)
    bridge.generate_send_and_persist_market_report(Client(), 1, "close")
    assert sent[-1] == bridge.normalize_brief(generated)

    sent.clear()
    monkeypatch.setattr(bridge, "write_investing_journal_draft", lambda day: None)
    bridge.generate_send_and_persist_market_report(Client(), 1, "morning")
    assert sent[-1] == bridge.normalize_brief(generated)


def test_condition_summary_formats_hits_from_stocks_table(monkeypatch) -> None:
    import types, sys

    fake = types.ModuleType("stock_web.api.stocks_page")
    fake.build_stocks_page_data = lambda root: {"table": [
        {"name": "삼성전자", "symbol": "005930", "price": 261000.0, "change_pct": 0.4, "condition_matches": [], "as_of": "2026-09-02"},
        {"name": "SK하이닉스", "symbol": "000660", "price": 1693000.0, "change_pct": 1.14,
         "condition_matches": [{"name": "52주 고점 대비 -30% 이하"}, {"name": "60일선 대비 -10% 이하"}, {"name": "RSI14 ≥ 70"}]},
        {"name": "SOXL", "symbol": "SOXL", "price": 105.91, "change_pct": -6.1,
         "condition_matches": [{"name": "하루 -5% 이하 급락"}, {"name": "RSI14 ≤ 30"}, {"name": "사용자 조건"}]},
    ]}
    monkeypatch.setitem(sys.modules, "stock_web.api.stocks_page", fake)
    text = bridge.watchlist_condition_summary()
    assert "삼성전자" not in text
    assert text.startswith("📌 관심종목 (09/02 마감 기준)")
    assert "SK하이닉스 1,693,000 ▲1.1% · 고점 -30%↓ · 60일선 -10%↓ · RSI≥70" in text
    assert "SOXL 105.91 ▼6.1% · 일 -5%↓ · RSI≤30 · 사용자 조건" in text
    assert text.endswith("설명용 · 신호 아님")


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
