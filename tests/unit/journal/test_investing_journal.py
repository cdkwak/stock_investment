from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re
from typing import Iterator
from uuid import uuid4

import pytest

from stock_data.journal import investing_journal as journal


TRADING_DAY = date(2026, 9, 3)


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Avoid pytest's broken Python 3.13 Windows 0700 temporary ACL."""
    root = (
        Path(__file__).resolve().parents[3]
        / ".tmp/agents/journal-morning-draft-20260903/fixtures"
        / uuid4().hex
    )
    root.mkdir(parents=True)
    yield root


def _configure(root: Path, journal_dir: Path) -> None:
    settings = root / "artifacts" / "local_user" / "web_settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps({"journal_dir": str(journal_dir)}, ensure_ascii=False),
        encoding="utf-8",
    )


def _payload() -> dict[str, object]:
    return {
        "sections": {
            "account": {
                "balance": 999_999_999,
                "holdings": ["SECRET-HOLDING"],
                "account_id": "SECRET-ACCOUNT-ID",
            },
            "regime": {
                "markets": [
                    {
                        "title": "한국장",
                        "temperature": "중립",
                        "subtitle": "신호 2/3 · 실적 축 없음",
                        "evidence": [
                            ["KOSPI RSI14", "48.2"],
                            ["60일선 대비", "+1.3%"],
                            ["KRX PER 5년 순위", "62%"],
                        ],
                    },
                    {"title": "미국장", "temperature": "중립", "subtitle": "신호 1/3"},
                    {"title": "글로벌 위험", "temperature": "중립", "subtitle": "신호 1/3"},
                ]
            },
            "tiles": [
                {
                    "name": "KOSPI",
                    "value": "2,700.25",
                    "change_pct": 0.5,
                    "ma5_pct": 1.25,
                    "ma20_pct": -0.75,
                    "stats": {
                        "rsi14": 48.2,
                        "disp60_pct": "+1.3%",
                        "drawdown_pct": "-4.2%",
                        "per": 12.4,
                        "pbr": 0.96,
                        "per_note": "5년 상위 38%",
                    },
                },
                {"name": "한국 3Y · 10Y", "value": "—"},
            ],
            "flows": {
                "rows": [
                    {"name": "외국인", "today": 100, "d5": -200, "d20": 300},
                    {"name": "기관", "today": -40, "d5": 50, "d20": 60},
                    {"name": "개인", "today": -60, "d5": 150, "d20": -360},
                ],
                "balances": [
                    {"name": "신용잔고", "value": "18.2조 (09-02)", "position": "1년 상위 12%"},
                    {"name": "대차잔고", "value": "71.4조"},
                ],
            },
            "derivatives": {
                "groups": [
                    {
                        "rows": [
                            ["선물 Basis", "+1.25 · 09-02"],
                            ["거래량 PCR", "0.923"],
                            ["미결제약정 PCR", "1.104"],
                            ["LS 선물 외국인 순계약", "+3,200"],
                        ]
                    }
                ]
            },
        }
    }


def _mask_machine_owned(data: bytes) -> bytes:
    for key in (b"date", b"regime", b"source"):
        data = re.sub(rb"(?m)^" + key + rb":[^\r\n]*", key + b": <machine>", data)
    data = re.sub(
        rb"(?s)(<!-- auto:start regime -->).*?(<!-- auto:end regime -->)",
        rb"\1<machine>\2",
        data,
    )
    data = re.sub(
        rb"(?s)(<!-- auto:start market -->).*?(<!-- auto:end market -->)",
        rb"\1<machine>\2",
        data,
    )
    return data


def test_missing_file_creates_complete_marked_journal(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "vault" / "일지"
    vault.mkdir(parents=True)
    _configure(tmp_path, vault)
    brief = tmp_path / "artifacts" / "local_user" / "briefs" / "2026-09-03-morning.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("---\nkind: morning\n---\n\n첫 줄\n둘째 줄\n셋째 줄\n넷째 줄\n", encoding="utf-8")
    monkeypatch.setattr(journal, "_load_home_payload", lambda root: _payload())

    result = journal.write_investing_journal(tmp_path, TRADING_DAY)

    target = vault / "2026-09-03 투자.md"
    assert result.status is journal.JournalStatus.CREATED
    contents = target.read_text(encoding="utf-8")
    assert contents.startswith(
        "---\n"
        "date: 2026-09-03\n"
        "regime: 중립\n"
        "leverage_pct: \n"
        "cash_pct: \n"
        "source: auto-draft\n"
        "tags:\n"
        '  - "pk/investing"\n'
        "---\n\n"
        "# 2026-09-03 아침 일지\n"
    )
    for marker in (
        journal.REGIME_START, journal.REGIME_END,
        journal.MARKET_START, journal.MARKET_END,
    ):
        assert contents.count(marker) == 1
    assert (
        f"{journal.REGIME_END}\n"
        "- 내 판단 (동의 / 다르게 봄): \n"
        "- 근거 한 줄: "
    ) in contents
    assert "## 오늘 국면 판단" in contents
    assert "## 어제와 달라진 것" in contents
    assert "## 오늘 행동" in contents
    assert "## 3개월 뒤 확인할 질문" in contents
    assert "글로벌 위험 보통 (신호 1/3)" in contents
    assert "| 지표 | 어제 | 오늘 | 변화 |" in contents
    assert "| KOSPI | 표시 불가 | 2,700.25 | +0.50% |" in contents
    assert "| KOSDAQ | 표시 불가 | 표시 불가 | 표시 불가 |" in contents
    assert "| KOSPI PER | 표시 불가 | 12.4 (5년 상위 38%) | 표시 불가 |" in contents
    assert "| 그룹 | 오늘 | 5일 |" in contents
    assert "| 외국인 | +100억 | -200억 |" in contents
    assert "### 파생\n\n- 베이시스: +1.25 · 09-02" in contents
    assert "- 첫 줄\n- 둘째 줄\n- 셋째 줄" in contents
    assert "넷째 줄" not in contents
    assert "999999999" not in contents
    assert "SECRET-HOLDING" not in contents
    assert "SECRET-ACCOUNT-ID" not in contents


def test_existing_file_changes_only_machine_owned_bytes(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "vault" / "일지"
    vault.mkdir(parents=True)
    _configure(tmp_path, vault)
    target = vault / "2026-09-03 투자.md"
    original = (
        "---\r\n"
        "date: 2020-01-01\r\n"
        "regime: 사용자 수정\r\n"
        "leverage_pct: 35\r\n"
        "cash_pct: 18\r\n"
        "source: manual\r\n"
        "tags: [pk/investing, user/custom]\r\n"
        "mood: 사용자 값\r\n"
        "---\r\n"
        "# 사용자 제목\r\n\r\n"
        "## 오늘 국면 판단\r\n"
        "<!-- auto:start regime -->\r\n"
        "- 대시보드 국면: 오래된 값\r\n"
        "<!-- auto:end regime -->\r\n"
        "- 내 판단 (동의 / 다르게 봄): USER-JUDGMENT\r\n"
        "- 근거 한 줄: USER-REASON\r\n\r\n"
        "## 어제와 달라진 것\r\n"
        "<!-- auto:start market -->\r\nOLD AUTO\r\n<!-- auto:end market -->\r\n\r\n"
        "## 오늘 행동\r\n- USER-ACTION\r\n\r\n"
        "## 3개월 뒤 확인할 질문\r\n- USER-QUESTION\r\n"
        "## 사용자 추가 섹션\r\nUSER-EXTRA\r\n"
    ).encode("utf-8")
    target.write_bytes(original)
    monkeypatch.setattr(journal, "_load_home_payload", lambda root: _payload())

    result = journal.write_investing_journal(tmp_path, TRADING_DAY)

    updated = target.read_bytes()
    assert result.status is journal.JournalStatus.UPDATED
    assert _mask_machine_owned(updated) == _mask_machine_owned(original)
    assert b"tags: [pk/investing, user/custom]\r\n" in updated
    assert b"leverage_pct: 35\r\n" in updated
    assert b"cash_pct: 18\r\n" in updated
    assert "mood: 사용자 값\r\n".encode() in updated
    assert "USER-JUDGMENT\r\n".encode() in updated
    assert "USER-EXTRA\r\n".encode() in updated
    assert b"OLD AUTO" not in updated
    assert b"date: 2026-09-03\r\n" in updated


@pytest.mark.parametrize("missing", ["regime", "market"])
def test_existing_file_missing_either_named_pair_is_untouched(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, missing: str,
) -> None:
    vault = tmp_path / "일지"
    vault.mkdir()
    _configure(tmp_path, vault)
    target = vault / "2026-09-03 투자.md"
    regime = (
        "" if missing == "regime" else
        "<!-- auto:start regime -->\nold regime\n<!-- auto:end regime -->\n"
    )
    market = (
        "" if missing == "market" else
        "<!-- auto:start market -->\nold market\n<!-- auto:end market -->\n"
    )
    original = f"---\ndate: 2026-09-03\n---\n{regime}{market}사용자 일지\n".encode("utf-8")
    target.write_bytes(original)
    monkeypatch.setattr(
        journal,
        "_load_home_payload",
        lambda root: pytest.fail("legacy file must be rejected before payload construction"),
    )

    result = journal.write_investing_journal(tmp_path, TRADING_DAY)

    assert result.status is journal.JournalStatus.SKIPPED_LEGACY_FILE
    assert target.read_bytes() == original
    assert "left untouched" in caplog.text


def test_weekend_is_skipped_before_settings_or_payload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        journal,
        "_load_home_payload",
        lambda root: pytest.fail("weekend must not build a payload"),
    )

    result = journal.write_investing_journal(tmp_path, date(2026, 9, 5))

    assert result.status is journal.JournalStatus.SKIPPED_NON_TRADING_DAY
    assert not list(tmp_path.rglob("*.md"))


def test_krx_one_off_holiday_is_skipped(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        journal,
        "_load_home_payload",
        lambda root: pytest.fail("KRX holiday must not build a payload"),
    )

    result = journal.write_investing_journal(tmp_path, date(2026, 6, 3))

    assert result.status is journal.JournalStatus.SKIPPED_NON_TRADING_DAY


def test_missing_journal_directory_warns_and_does_nothing(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    _configure(tmp_path, tmp_path / "missing-vault")
    monkeypatch.setattr(
        journal,
        "_load_home_payload",
        lambda root: pytest.fail("missing journal directory must not build a payload"),
    )

    result = journal.write_investing_journal(tmp_path, TRADING_DAY)

    assert result.status is journal.JournalStatus.SKIPPED_MISSING_DIRECTORY
    assert "journal directory does not exist" in caplog.text


def test_payload_failure_creates_no_file(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "일지"
    vault.mkdir()
    _configure(tmp_path, vault)

    def fail(_root: Path) -> dict[str, object]:
        raise RuntimeError("mock dashboard failure")

    monkeypatch.setattr(journal, "_load_home_payload", fail)

    with pytest.raises(journal.JournalPayloadError, match="could not be built"):
        journal.write_investing_journal(tmp_path, TRADING_DAY)
    assert not list(vault.iterdir())


def test_long_brief_is_written_separately_and_linked(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "vault" / "일지"
    vault.mkdir(parents=True)
    _configure(tmp_path, vault)
    source = tmp_path / "artifacts" / "local_user" / "briefs" / "2026-09-03-morning.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"브리핑 {index}" for index in range(1, 14))
    source.write_text(f"---\nkind: morning\n---\n\n{body}\n", encoding="utf-8")
    monkeypatch.setattr(journal, "_load_home_payload", lambda root: _payload())

    result = journal.write_investing_journal(tmp_path, TRADING_DAY)

    brief = tmp_path / "vault" / "브리핑" / "2026-09-03 브리핑.md"
    assert result.brief_path == brief
    assert brief.read_text(encoding="utf-8") == (
        "---\n"
        "date: 2026-09-03\n"
        "tags: [pk/investing]\n"
        "source: auto-draft\n"
        "---\n\n"
        f"{body}\n"
    )
    journal_text = (vault / "2026-09-03 투자.md").read_text(encoding="utf-8")
    assert "브리핑: [[2026-09-03 브리핑]]" in journal_text
    assert "브리핑 1" not in journal_text


def test_dry_run_returns_complete_content_without_writes(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "일지"
    vault.mkdir()
    _configure(tmp_path, vault)
    monkeypatch.setattr(journal, "_load_home_payload", lambda root: _payload())

    result = journal.write_investing_journal(tmp_path, TRADING_DAY, dry_run=True)

    assert result.status is journal.JournalStatus.DRY_RUN_CREATED
    assert result.journal_content is not None
    assert journal.REGIME_START in result.journal_content
    assert journal.MARKET_START in result.journal_content
    assert not list(vault.iterdir())


def test_new_file_matches_reference_template_structure(monkeypatch, tmp_path: Path) -> None:
    vault = tmp_path / "vault" / "일지"
    vault.mkdir(parents=True)
    _configure(tmp_path, vault)
    monkeypatch.setattr(journal, "_load_home_payload", lambda root: _payload())

    result = journal.write_investing_journal(tmp_path, TRADING_DAY, dry_run=True)

    assert result.journal_content is not None
    contents = result.journal_content
    headings = re.findall(r"(?m)^#{1,2} .+$", contents)
    assert headings == [
        "# 2026-09-03 아침 일지",
        "## 오늘 국면 판단",
        "## 어제와 달라진 것",
        "## 오늘 행동",
        "## 3개월 뒤 확인할 질문",
    ]
    assert "- 할 것: \n- 하지 않을 것: \n- 레버리지 비중 (현재 → 목표): \n- 기타 메모: " in contents
    assert contents.endswith(
        "---\n"
        "<!-- 마커 안(auto:start ~ auto:end)은 주식 세션이 매일 07:30 에 교체한다.\n"
        "     마커 밖은 사람만 쓴다. 자동 작업이 건드리지 않는다. -->\n"
    )
