import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "telegram_agent_bridge.py"
SPEC = importlib.util.spec_from_file_location("telegram_agent_bridge_conditions", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def test_close_brief_appends_condition_hits_and_never_blocks_on_failure(monkeypatch) -> None:
    sent: list[str] = []

    class Client:
        def send(self, chat_id, text):
            sent.append(text)

    monkeypatch.setattr(bridge, "generate_market_report", lambda kind: "마감 요약 본문")
    monkeypatch.setattr(bridge, "persist_market_report", lambda kind, report, ok: None)
    monkeypatch.setattr(bridge, "send_long_message", lambda client, chat_id, text: sent.append(text))
    monkeypatch.setattr(
        bridge, "watchlist_condition_summary",
        lambda: "📌 관심종목 조건 도달\n- SK하이닉스 1,693,000 (+1.1%): 52주 고점 대비 -30% 이하",
    )
    bridge.generate_send_and_persist_market_report(Client(), 1, "close")
    assert sent[-1].startswith("마감 요약 본문\n\n📌 관심종목 조건 도달")

    def boom():
        raise RuntimeError("no retained data")

    monkeypatch.setattr(bridge, "watchlist_condition_summary", boom)
    bridge.generate_send_and_persist_market_report(Client(), 1, "close")
    assert sent[-1] == "마감 요약 본문"

    sent.clear()
    monkeypatch.setattr(bridge, "write_investing_journal_draft", lambda day: None)
    bridge.generate_send_and_persist_market_report(Client(), 1, "morning")
    assert sent[-1] == "마감 요약 본문"


def test_condition_summary_formats_hits_from_stocks_table(monkeypatch) -> None:
    import types, sys

    fake = types.ModuleType("stock_web.api.stocks_page")
    fake.build_stocks_page_data = lambda root: {"table": [
        {"name": "삼성전자", "symbol": "005930", "price": 261000.0, "change_pct": 0.4, "condition_matches": []},
        {"name": "SK하이닉스", "symbol": "000660", "price": 1693000.0, "change_pct": 1.14,
         "condition_matches": [{"name": "52주 고점 대비 -30% 이하"}, {"name": "60일선 대비 -10% 이하"}]},
        {"name": "SOXL", "symbol": "SOXL", "price": 105.91, "change_pct": -6.1,
         "condition_matches": [{"name": "하루 -5% 이하 급락"}]},
    ]}
    monkeypatch.setitem(sys.modules, "stock_web.api.stocks_page", fake)
    text = bridge.watchlist_condition_summary()
    assert "삼성전자" not in text
    assert "- SK하이닉스 1,693,000 (+1.1%): 52주 고점 대비 -30% 이하 · 60일선 대비 -10% 이하" in text
    assert "- SOXL 105.91 (-6.1%): 하루 -5% 이하 급락" in text
    assert text.endswith("설명용 관찰 · 추천/주문 신호 아님")
