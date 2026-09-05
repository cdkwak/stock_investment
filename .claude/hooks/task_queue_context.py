"""UserPromptSubmit hook: when the prompt asks about next tasks, inject the vault's 작업 큐 section.

Read-only. Reads hook JSON from stdin, prints hookSpecificOutput JSON with additionalContext
when the prompt matches; prints nothing otherwise. Never raises (a broken hook must not block prompts).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

NOTE = Path(
    r"C:\Users\k4545\내 드라이브\Obsidian\CDSecondBrain\20 Personal Knowledge\Investing\대시보드 요구사항.md"
)
TRIGGER = re.compile(r"다음|할\s*일|뭐\s*해야|todo|남은\s*작업|작업\s*큐", re.IGNORECASE)
SECTION_START = "## 작업 큐"
MAX_CHARS = 40000
DONE_LINES_KEPT = 10  # 완료 is history; 진행 중·대기 are the live queue and go in whole


def _queue_section(text: str) -> str:
    start = text.find(SECTION_START)
    if start < 0:
        return ""
    rest = text[start:]
    # The section ends at the next H2 heading.
    next_h2 = re.search(r"\n## (?!작업 큐)", rest)
    section = rest if next_h2 is None else rest[: next_h2.start()]
    section = section.replace("\r\n", "\n").replace("\r", "\n")
    # Keep the live sections whole; cap the 완료 history to its most recent lines.
    done_at = section.find("### 완료")
    if done_at >= 0:
        head, done = section[:done_at], section[done_at:]
        done_lines = done.split("\n")
        kept = done_lines[: DONE_LINES_KEPT + 1]
        omitted = len([line for line in done_lines[DONE_LINES_KEPT + 1:] if line.strip()])
        section = head + "\n".join(kept) + (f"\n- … (완료 {omitted}건 생략 — 파일 직접 참조)" if omitted else "")
    return section.strip()[:MAX_CHARS]


def main() -> int:
    try:
        # Hook input is UTF-8 JSON; on Windows the text stdin would decode with the console
        # code page and break the Korean trigger words, so read bytes explicitly.
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
        prompt = str(payload.get("prompt") or payload.get("user_prompt") or "")
        if not TRIGGER.search(prompt):
            return 0
        section = _queue_section(NOTE.read_text(encoding="utf-8")) if NOTE.exists() else ""
        if not section:
            context = f"[작업 큐 훅] 볼트 노트에서 '{SECTION_START}' 절을 찾지 못했습니다: {NOTE}"
        else:
            context = (
                "[작업 큐 훅] 볼트 '대시보드 요구사항.md'의 작업 큐 절 (기준 목록 · 답하기 전에 먼저 읽을 것):\n\n"
                + section
            )
        out = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - a hook failure must never block the prompt
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
