from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/maintenance/telegram_agent_bridge.py"
SPEC = importlib.util.spec_from_file_location("telegram_agent_bridge", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class RecordingClient:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    def send(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


def test_telegram_client_exposes_call_and_send_methods() -> None:
    client = bridge.TelegramClient("123:test-token")
    assert callable(client.call)
    assert callable(client.send)


def test_load_settings_prefers_environment_and_never_exposes_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=999:file-token\nTELEGRAM_ALLOWED_CHAT_ID=456\n",
        encoding="utf-8",
    )
    assert bridge.load_settings(
        {"TELEGRAM_BOT_TOKEN": "123:env-token", "TELEGRAM_ALLOWED_CHAT_ID": "789"},
        env_file,
    ) == ("123:env-token", 789)

    with pytest.raises(bridge.BridgeError, match="missing or malformed") as error:
        bridge.load_settings({}, tmp_path / "missing")
    assert "123:env-token" not in str(error.value)
    assert "999:file-token" not in str(error.value)


def test_project_status_summary_reads_only_selected_fields(tmp_path: Path) -> None:
    status = tmp_path / "PROJECT_STATUS.md"
    status.write_text(
        "| Field | Current value |\n|---|---|\n"
        "| Selected domain | `BACKTEST` |\n"
        "| Current phase | `OFFLINE` |\n"
        "| Next domain | Not selected |\n"
        "private detail that must not be returned\n",
        encoding="utf-8",
    )
    result = bridge.project_status_summary(status)
    assert "BACKTEST" in result and "OFFLINE" in result
    assert "private detail" not in result


def test_dashboard_status_summary_uses_compact_phase_and_typed_health(tmp_path: Path) -> None:
    status = tmp_path / "GUI_STATUS.md"
    status.write_text(
        "# GUI Status\n\n## Current phase\n\n`ACTIVE_GUI`\n\n"
        + "historical detail\n" * 100,
        encoding="utf-8",
    )
    health = tmp_path / "health.json"
    health.write_text(json.dumps({
        "generated_at": "2026-08-26T11:40:00+00:00",
        "datasets": [
            {"automation_enabled": True, "freshness": "CURRENT",
             "display_consumer_eligibility": "ELIGIBLE"},
            {"automation_enabled": False, "freshness": "STALE",
             "display_consumer_eligibility": "LIMITED"},
        ],
    }), encoding="utf-8")

    result = bridge.dashboard_status_summary(status, health)

    assert "ACTIVE_GUI" in result
    assert "자동관리 1/1" in result
    assert "화면 후보 공백 1" in result
    assert "2026-08-26 20:40 KST" in result
    assert "historical detail" not in result


def test_handle_updates_accepts_only_the_allowed_private_sender(monkeypatch) -> None:
    client = RecordingClient()
    monkeypatch.setattr(bridge, "command_reply", lambda text: f"reply:{text}")
    updates = [
        {"update_id": 10, "message": {
            "chat": {"id": 42, "type": "private"}, "from": {"id": 42}, "text": "/ping",
        }},
        {"update_id": 11, "message": {
            "chat": {"id": 99, "type": "private"}, "from": {"id": 99}, "text": "/status",
        }},
        {"update_id": 12, "message": {
            "chat": {"id": 42, "type": "group"}, "from": {"id": 42}, "text": "/queue",
        }},
    ]
    assert bridge.handle_updates(client, 42, updates) == 13
    assert client.messages == [(42, "reply:/ping")]


def test_handle_request_uses_agent_intake_only_for_allowlisted_sender(monkeypatch) -> None:
    client = RecordingClient()
    monkeypatch.setattr(bridge, "register_natural_language_request", lambda text: f"registered:{text}")
    updates = [{"update_id": 20, "message": {
        "chat": {"id": 42, "type": "private"}, "from": {"id": 42},
        "text": "/request 환율 카드 오류를 확인해줘",
    }}]
    assert bridge.handle_updates(client, 42, updates) == 21
    assert client.messages == [
        (42, "🤖 요청을 읽고 중복·범위를 검토 중입니다…"),
        (42, "registered:환율 카드 오류를 확인해줘"),
    ]


def test_register_request_validates_agent_output_before_queue_mutation(monkeypatch) -> None:
    record = {
        "decision": "register", "message": "", "title": "Fix bounded card defect",
        "fingerprint": "gui:card:bounded-defect", "symptom": "A card is incorrect.",
        "evidence": "Telegram user report; reproduction remains to be established.",
        "impact": "The displayed value may mislead the user.",
        "suspected_scope": "src/stock_data/gui", "reproduce": "Triage must establish reproduction.",
        "priority_hint": "P1", "duplicate_task_id": "",
    }
    monkeypatch.setattr(bridge, "analyze_request_with_codex", lambda text: record)

    class Completed:
        returncode = 0
        stdout = "RQ-20260826T123456-ABCD\n"
        stderr = ""

    observed = {}

    def fake_run(arguments, **kwargs):
        observed["arguments"] = arguments
        return Completed()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    reply = bridge.register_natural_language_request("카드 문제를 등록해줘")
    assert "RQ-20260826T123456-ABCD" in reply
    arguments = observed["arguments"]
    assert arguments[2] == "discover"
    assert "claim" not in arguments and "triage" not in arguments


def test_codex_intake_is_read_only_ephemeral_and_keeps_user_text_off_argv(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(bridge, "INTAKE_ROOT", tmp_path / "intake")
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "codex.exe")
    monkeypatch.setattr(bridge, "compact_repository_context", lambda: "compact-context")
    observed = {}

    class Completed:
        returncode = 0

    def fake_run(arguments, **kwargs):
        observed["arguments"] = arguments
        observed["input"] = kwargs["input"]
        output = Path(arguments[arguments.index("--output-last-message") + 1])
        output.write_text(json.dumps({
            "decision": "clarify", "message": "범위를 알려주세요.",
            "title": "", "fingerprint": "", "symptom": "", "evidence": "",
            "impact": "", "suspected_scope": "", "reproduce": "",
            "priority_hint": "P2", "duplicate_task_id": "",
        }), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    secret_text = "사용자가 보낸 민감할 수 있는 요청"
    assert bridge.analyze_request_with_codex(secret_text)["decision"] == "clarify"
    arguments = observed["arguments"]
    assert "--ephemeral" in arguments
    assert arguments[arguments.index("--sandbox") + 1] == "read-only"
    assert secret_text not in arguments
    assert observed["input"] == secret_text
    assert "compact-context" in arguments[-1]
    assert "Do not reread the full" in arguments[-1]


@pytest.mark.parametrize("kind", ["morning", "close"])
def test_market_report_uses_live_search_and_shared_polite_tone_in_read_only_codex(
    monkeypatch, tmp_path: Path, kind: str,
) -> None:
    monkeypatch.setattr(bridge, "INTAKE_ROOT", tmp_path / "reports")
    monkeypatch.setattr(bridge, "find_codex", lambda: "codex.exe")
    monkeypatch.setattr(bridge, "compact_repository_context", lambda: "compact-context")
    observed = {}

    class Completed:
        returncode = 0

    def fake_run(arguments, **kwargs):
        observed["arguments"] = arguments
        output = Path(arguments[arguments.index("--output-last-message") + 1])
        output.write_text("# 아침 브리핑\n출처: https://example.invalid", encoding="utf-8")
        return Completed()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    report = bridge.generate_market_report(kind)
    arguments = observed["arguments"]
    assert report.startswith("# 아침 브리핑")
    assert "--ephemeral" in arguments
    assert arguments[arguments.index("--sandbox") + 1] == "read-only"
    assert 'web_search="live"' in arguments
    prompt = arguments[-1]
    assert "🤖 시스템" in prompt
    assert "프로젝트 단계·대시보드" in prompt
    assert "긴 Status 문서를 다시 읽" in prompt
    assert "compact-context" in prompt
    assert "URL이나 긴 링크를 넣지 마라" in prompt
    assert "실제 사용한 기관·매체 이름만" in prompt
    assert "섹션 사이에만 빈 줄 하나" in prompt
    assert "🎯 목표가·전망" in prompt
    assert "최근 3거래일" in prompt
    assert "통틀어 최대 2건" in prompt
    assert "900~1,600자" in prompt
    assert "Telegram 메시지 하나로 끝내라" in prompt
    assert "격식체 존댓말('-습니다/-입니다')" in prompt
    assert "반말·해라체를 쓰지 마라" in prompt
    assert "고정 라벨" in prompt


def test_market_report_boundary_always_fits_one_telegram_message() -> None:
    assert bridge.MAX_REPORT_LENGTH < bridge.MAX_MESSAGE_LENGTH
    assert len(bridge.split_telegram_message("x" * bridge.MAX_REPORT_LENGTH)) == 1


def test_market_report_rejects_agent_output_above_compact_boundary(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(bridge, "INTAKE_ROOT", tmp_path / "reports")
    monkeypatch.setattr(bridge, "find_codex", lambda: "codex.exe")
    monkeypatch.setattr(bridge, "compact_repository_context", lambda: "compact-context")

    class Completed:
        returncode = 0

    def fake_run(arguments, **kwargs):
        output = Path(arguments[arguments.index("--output-last-message") + 1])
        output.write_text("x" * (bridge.MAX_REPORT_LENGTH + 1), encoding="utf-8")
        return Completed()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    with pytest.raises(bridge.BridgeError, match="character safety boundary"):
        bridge.generate_market_report("morning")


def test_long_report_splits_on_sections_without_truncation() -> None:
    text = "첫 섹션\n" + "가" * 2100 + "\n\n둘째 섹션\n" + "나" * 2100
    chunks = bridge.split_telegram_message(text, limit=2200)
    assert len(chunks) == 2
    restored = "\n\n".join(chunk.split("\n", 1)[1] for chunk in chunks)
    assert restored == text
    assert chunks[0].startswith("(1/2)\n")


def test_handle_manual_brief_rejects_unknown_kind_without_codex(monkeypatch) -> None:
    client = RecordingClient()
    monkeypatch.setattr(
        bridge, "generate_market_report",
        lambda kind: pytest.fail("Codex must not run for an invalid kind"),
    )
    updates = [{"update_id": 30, "message": {
        "chat": {"id": 42, "type": "private"}, "from": {"id": 42},
        "text": "/brief tomorrow",
    }}]
    assert bridge.handle_updates(client, 42, updates) == 31
    assert client.messages == [(42, "사용법: /brief morning 또는 /brief close")]


def test_nonregister_agent_decision_never_mutates_queue(monkeypatch) -> None:
    record = {
        "decision": "clarify", "message": "어느 화면인지 알려주세요.",
        "title": "", "fingerprint": "", "symptom": "", "evidence": "",
        "impact": "", "suspected_scope": "", "reproduce": "",
        "priority_hint": "P2", "duplicate_task_id": "",
    }
    monkeypatch.setattr(bridge, "analyze_request_with_codex", lambda text: record)
    monkeypatch.setattr(
        bridge.subprocess, "run",
        lambda *args, **kwargs: pytest.fail("queue manager must not run"),
    )
    assert "확인 필요" in bridge.register_natural_language_request("고쳐줘")


def test_hook_sends_no_prompt_or_assistant_content(capsys) -> None:
    client = RecordingClient()
    payload = {
        "hook_event_name": "Stop",
        "last_assistant_message": "sensitive result",
        "cwd": "private path",
    }
    monkeypatch_stdin = json.dumps(payload)
    import io
    import sys

    old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(monkeypatch_stdin)
        assert bridge.run_hook(client, 42) == 0
    finally:
        sys.stdin = old_stdin
    assert client.messages == [(42, "✅ 메인 에이전트 응답 종료")]
    assert "sensitive" not in client.messages[0][1]
    assert capsys.readouterr().out.strip() == "{}"


def test_read_only_command_surface(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "project_status_summary", lambda: "status")
    monkeypatch.setattr(bridge, "queue_status_summary", lambda: "queue")
    assert bridge.command_reply("/ping") == "🏓 연결 정상"
    assert bridge.command_reply("/status@my_bot") == "status"
    assert bridge.command_reply("/queue") == "queue"
    assert bridge.command_reply("run arbitrary shell") is None
    assert "지원하지 않는" in bridge.command_reply("/new")
