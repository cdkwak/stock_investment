"""Build a privacy-safe morning investing-journal draft from local GUI data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from functools import lru_cache
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

REGIME_START = "<!-- auto:start regime -->"
REGIME_END = "<!-- auto:end regime -->"
MARKET_START = "<!-- auto:start market -->"
MARKET_END = "<!-- auto:end market -->"
KST = ZoneInfo("Asia/Seoul")
LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _exchange_calendar_module() -> Any:
    """Load the helper without executing the orchestration package's eager registry."""
    path = Path(__file__).resolve().parents[1] / "orchestration" / "exchange_calendar.py"
    name = "stock_data.journal._exchange_calendar"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise JournalError("exchange calendar helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _is_kr_trading_day(value: date) -> bool:
    calendar = _exchange_calendar_module()
    return bool(calendar.is_trading_day(calendar.ExchangeMarket.KR, value))


class JournalError(RuntimeError):
    """Base error for a journal operation that must report failure."""


class JournalPayloadError(JournalError):
    """Raised when the local Dashboard payload cannot be built."""


class JournalStatus(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    DRY_RUN_CREATED = "dry-run-created"
    DRY_RUN_UPDATED = "dry-run-updated"
    SKIPPED_NON_TRADING_DAY = "skipped-non-trading-day"
    SKIPPED_MISSING_DIRECTORY = "skipped-missing-directory"
    SKIPPED_LEGACY_FILE = "skipped-legacy-file"


@dataclass(frozen=True)
class JournalWriteResult:
    status: JournalStatus
    journal_path: Path | None
    journal_content: str | None = None
    brief_path: Path | None = None
    brief_content: str | None = None


def _load_home_payload(project_root: Path) -> dict[str, object]:
    # The web package is an explicitly read-only dependency of this projection.
    from stock_web.api.home_data import build_home_payload

    return build_home_payload(project_root)


def _warn(logger: logging.Logger, message: str, *args: object) -> None:
    logger.warning("investing_journal: " + message, *args)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise JournalError(f"could not atomically write {path}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _journal_directory(project_root: Path, logger: logging.Logger) -> Path | None:
    settings_path = project_root / "artifacts" / "local_user" / "web_settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _warn(logger, "journal_dir setting could not be read: %s", type(exc).__name__)
        return None
    raw = settings.get("journal_dir") if isinstance(settings, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        _warn(logger, "journal_dir setting is missing")
        return None
    directory = Path(raw).expanduser()
    if not directory.is_absolute():
        directory = project_root / directory
    if not directory.is_dir():
        _warn(logger, "journal directory does not exist: %s", directory)
        return None
    return directory


def _available_text(value: object) -> str:
    if value is None:
        return "표시 불가"
    if isinstance(value, float) and not math.isfinite(value):
        return "표시 불가"
    text = str(value).strip()
    if not text or text in {"—", "-", "N/A", "None", "null"}:
        return "표시 불가"
    return text


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _percent(value: object) -> str:
    number = _number(value)
    return "표시 불가" if number is None else f"{number:+.2f}%"


def _flow_amount(value: object) -> str:
    number = _number(value)
    return "표시 불가" if number is None else f"{number:+,.0f}"


def _table_cell(value: object) -> str:
    return _available_text(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _market(regime: Mapping[str, Any], title: str) -> Mapping[str, Any]:
    for item in _sequence(regime.get("markets")):
        candidate = _mapping(item)
        if candidate.get("title") == title:
            return candidate
    return {}


def _signal(market: Mapping[str, Any]) -> str:
    match = re.search(r"신호\s+(\d+/\d+)", str(market.get("subtitle", "")))
    return f"신호 {match.group(1)}" if match else "신호 표시 불가"


def _regime_projection(sections: Mapping[str, Any]) -> tuple[str, str]:
    regime = _mapping(sections.get("regime"))
    rendered: list[str] = []
    korean_label = "표시 불가"
    for title in ("한국장", "미국장", "글로벌 위험"):
        item = _market(regime, title)
        label = _available_text(item.get("temperature"))
        if title == "글로벌 위험":
            label = {"중립": "보통", "과열": "높음", "침체": "낮음"}.get(label, label)
        if title == "한국장":
            korean_label = label
        rendered.append(f"{title} {label} ({_signal(item)})")
    return korean_label, " · ".join(rendered)


def _tiles_by_name(sections: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(tile["name"]): tile
        for item in _sequence(sections.get("tiles"))
        if (tile := _mapping(item)) and isinstance(tile.get("name"), str)
    }


def _tile_change(tile: Mapping[str, Any]) -> str:
    if tile.get("change_label") is not None:
        return _available_text(tile.get("change_label"))
    return _percent(tile.get("change_pct"))


def _previous_value(tile: Mapping[str, Any]) -> str:
    for key in ("yesterday", "previous_value", "prev_value"):
        if key in tile:
            return _available_text(tile.get(key))
    return "표시 불가"


def _indicator_rows(sections: Mapping[str, Any]) -> list[str]:
    tiles = _tiles_by_name(sections)
    kospi = tiles.get("KOSPI", {})
    stats = _mapping(kospi.get("stats"))
    evidence = _evidence(_market(_mapping(sections.get("regime")), "한국장"))

    def tile_values(*names: str) -> tuple[str, str, str]:
        tile = next((tiles[name] for name in names if name in tiles), {})
        return (
            _previous_value(tile),
            _available_text(tile.get("value")),
            _tile_change(tile),
        )

    def stat_value(key: str, fallback_key: str | None = None) -> str:
        value = stats.get(key, kospi.get(key))
        if value is None and fallback_key is not None:
            value = evidence.get(fallback_key)
        return _available_text(value)

    per = stat_value("per")
    per_note = stat_value("per_note")
    if per != "표시 불가" and per_note != "표시 불가":
        per = f"{per} ({per_note})"

    values = [
        ("KOSPI", *tile_values("KOSPI")),
        ("KOSDAQ", *tile_values("KOSDAQ")),
        ("S&P 500", *tile_values("S&P 500", "S&P 500 선물")),
        ("나스닥100 선물", *tile_values("나스닥100 선물", "NASDAQ 100 선물")),
        ("USD/KRW", *tile_values("USD/KRW")),
        ("미국 10Y", *tile_values("미국 10Y")),
        ("10Y-2Y 스프레드", *tile_values("10Y-2Y 스프레드")),
        ("KOSPI PER", "표시 불가", per, "표시 불가"),
        ("KOSPI PBR", "표시 불가", stat_value("pbr"), "표시 불가"),
        ("RSI14 (KOSPI)", "표시 불가", stat_value("rsi14", "KOSPI RSI14"), "표시 불가"),
        ("60일선 대비", "표시 불가", stat_value("disp60_pct", "60일선 대비"), "표시 불가"),
        ("52주 고점 대비", "표시 불가", stat_value("drawdown_pct"), "표시 불가"),
    ]
    rows = ["| 지표 | 어제 | 오늘 | 변화 |", "|------|------|------|------|"]
    rows.extend(
        "| " + " | ".join(_table_cell(cell) for cell in row) + " |"
        for row in values
    )
    return rows


def _flow_rows(sections: Mapping[str, Any]) -> list[str]:
    flows = _mapping(sections.get("flows"))
    by_name = {
        str(row.get("name")): row
        for item in _sequence(flows.get("rows"))
        if (row := _mapping(item))
    }
    rows = ["### 수급 (KOSPI)", "", "| 그룹 | 오늘 | 5일 |", "|------|------|------|"]
    for name in ("외국인", "기관", "개인"):
        row = by_name.get(name, {})
        today = _flow_amount(row.get("today"))
        d5 = _flow_amount(row.get("d5"))
        if today != "표시 불가":
            today += "억"
        if d5 != "표시 불가":
            d5 += "억"
        rows.append(
            "| " + " | ".join((_table_cell(name), _table_cell(today), _table_cell(d5))) + " |"
        )
    return rows


def _numeric_display(value: object) -> str:
    text = _available_text(value)
    if text == "표시 불가" or re.match(r"^[+-]?\d[\d,]*(?:\.\d+)?", text) is None:
        return "표시 불가"
    return text


def _derivative_rows(sections: Mapping[str, Any]) -> list[str]:
    derivatives = _mapping(sections.get("derivatives"))
    values: dict[str, object] = {}
    for group in _sequence(derivatives.get("groups")):
        for row in _sequence(_mapping(group).get("rows")):
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                values[str(row[0])] = row[1]
    return [
        "### 파생",
        "",
        f"- 베이시스: {_numeric_display(values.get('선물 Basis'))}",
        f"- 거래량 PCR: {_numeric_display(values.get('거래량 PCR'))}",
        f"- 미결제 PCR: {_numeric_display(values.get('미결제약정 PCR'))}",
    ]


def _evidence(market: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in _sequence(market.get("evidence")):
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            result[str(row[0])] = _available_text(row[1])
    return result


def _brief_body(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        try:
            closing = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
        except StopIteration:
            return text.strip()
        lines = lines[closing + 1 :]
    return "\n".join(lines).strip()


def _brief_projection(
    project_root: Path, journal_dir: Path, journal_date: date, logger: logging.Logger,
) -> tuple[list[str], Path | None, str | None]:
    source = project_root / "artifacts" / "local_user" / "briefs" / f"{journal_date.isoformat()}-morning.md"
    if not source.is_file():
        return ["브리핑: 표시 불가"], None, None
    try:
        body = _brief_body(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        _warn(logger, "morning brief could not be read: %s", type(exc).__name__)
        return ["브리핑: 표시 불가"], None, None
    body_lines = body.splitlines()
    if len(body_lines) <= 12:
        first_three = [line for line in body_lines if line.strip()][:3]
        if not first_three:
            return ["브리핑: 표시 불가"], None, None
        return ["브리핑:", *(f"- {line}" for line in first_three)], None, None
    name = f"{journal_date.isoformat()} 브리핑"
    target = journal_dir.parent / "브리핑" / f"{name}.md"
    content = (
        "---\n"
        f"date: {journal_date.isoformat()}\n"
        "tags: [pk/investing]\n"
        "source: auto-draft\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )
    return [f"브리핑: [[{name}]]"], target, content


def _auto_lines(
    sections: Mapping[str, Any], brief_lines: Sequence[str], newline: str,
) -> tuple[str, str, str]:
    regime_label, regime_line = _regime_projection(sections)
    lines = [
        *_indicator_rows(sections),
        "",
        *_flow_rows(sections),
        "",
        *_derivative_rows(sections),
        "",
        *brief_lines,
    ]
    return regime_label, regime_line, newline.join(lines)


def _new_journal(
    journal_date: date, regime_label: str, regime_line: str, auto_content: str,
) -> str:
    stamp = journal_date.isoformat()
    return (
        "---\n"
        f"date: {stamp}\n"
        f"regime: {regime_label}\n"
        "leverage_pct: \n"
        "cash_pct: \n"
        "source: auto-draft\n"
        "tags:\n"
        '  - "pk/investing"\n'
        "---\n\n"
        f"# {stamp} 아침 일지\n\n"
        "## 오늘 국면 판단\n\n"
        f"{REGIME_START}\n"
        f"- 대시보드 국면: {regime_line}\n"
        f"{REGIME_END}\n"
        "- 내 판단 (동의 / 다르게 봄): \n"
        "- 근거 한 줄: \n\n"
        "## 어제와 달라진 것\n\n"
        f"{MARKET_START}\n"
        f"{auto_content}\n"
        f"{MARKET_END}\n\n"
        "## 오늘 행동\n\n"
        "- 할 것: \n"
        "- 하지 않을 것: \n"
        "- 레버리지 비중 (현재 → 목표): \n"
        "- 기타 메모: \n\n"
        "## 3개월 뒤 확인할 질문\n\n"
        "- \n\n"
        "---\n"
        "<!-- 마커 안(auto:start ~ auto:end)은 주식 세션이 매일 07:30 에 교체한다.\n"
        "     마커 밖은 사람만 쓴다. 자동 작업이 건드리지 않는다. -->\n"
    )


def _replace_frontmatter(text: str, values: Mapping[str, str], newline: str) -> str:
    first_end = text.find(newline)
    if first_end < 0 or text[:first_end].lstrip("\ufeff") != "---":
        raise JournalError("existing journal has no supported frontmatter")
    closing = text.find(newline + "---" + newline, first_end)
    if closing < 0:
        raise JournalError("existing journal frontmatter is not closed")
    end = closing + len(newline + "---")
    frontmatter = text[:end]
    for key, value in values.items():
        pattern = re.compile(rf"(?m)^{re.escape(key)}:[^\r\n]*")
        if pattern.search(frontmatter):
            frontmatter = pattern.sub(f"{key}: {value}", frontmatter)
        else:
            insert_at = frontmatter.rfind(newline + "---")
            frontmatter = frontmatter[:insert_at] + newline + f"{key}: {value}" + frontmatter[insert_at:]
    return frontmatter + text[end:]


def _update_existing(
    text: str,
    journal_date: date,
    regime_label: str,
    regime_line: str,
    auto_content: str,
) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    text = _replace_frontmatter(
        text,
        {"date": journal_date.isoformat(), "regime": regime_label, "source": "auto-draft"},
        newline,
    )
    text = _replace_named_region(
        text, REGIME_START, REGIME_END, f"- 대시보드 국면: {regime_line}", newline
    )
    return _replace_named_region(text, MARKET_START, MARKET_END, auto_content, newline)


def _replace_named_region(
    text: str, start_marker: str, end_marker: str, content: str, newline: str,
) -> str:
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise JournalError(f"existing journal is missing marker pair {start_marker}")
    return text[: start + len(start_marker)] + newline + content + newline + text[end:]


def _has_unique_named_regions(raw: bytes) -> bool:
    markers = (REGIME_START, REGIME_END, MARKET_START, MARKET_END)
    if any(raw.count(marker.encode("ascii")) != 1 for marker in markers):
        return False
    regime_start = raw.find(REGIME_START.encode("ascii"))
    regime_end = raw.find(REGIME_END.encode("ascii"))
    market_start = raw.find(MARKET_START.encode("ascii"))
    market_end = raw.find(MARKET_END.encode("ascii"))
    return regime_start < regime_end and market_start < market_end


def write_investing_journal(
    project_root: Path | str,
    journal_date: date | None = None,
    *,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> JournalWriteResult:
    """Create or update one trading-day journal without touching user-owned text."""
    selected_logger = logger or LOGGER
    selected_date = journal_date or datetime.now(KST).date()
    if not _is_kr_trading_day(selected_date):
        selected_logger.info(
            "investing_journal: skipped non-trading day %s", selected_date.isoformat()
        )
        return JournalWriteResult(JournalStatus.SKIPPED_NON_TRADING_DAY, None)

    root = Path(project_root).resolve()
    journal_dir = _journal_directory(root, selected_logger)
    if journal_dir is None:
        return JournalWriteResult(JournalStatus.SKIPPED_MISSING_DIRECTORY, None)
    target = journal_dir / f"{selected_date.isoformat()} 투자.md"

    existing: str | None = None
    if target.exists():
        raw = target.read_bytes()
        if not _has_unique_named_regions(raw):
            _warn(selected_logger, "existing journal has no unique named marker pairs; left untouched: %s", target)
            return JournalWriteResult(JournalStatus.SKIPPED_LEGACY_FILE, target)
        try:
            existing = raw.decode("utf-8")
        except UnicodeError as exc:
            raise JournalError("existing journal is not valid UTF-8") from exc

    try:
        payload = _load_home_payload(root)
    except Exception as exc:
        raise JournalPayloadError("dashboard payload could not be built") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("sections"), dict):
        raise JournalPayloadError("dashboard payload has no sections mapping")
    sections = payload["sections"]

    brief_lines, brief_path, brief_content = _brief_projection(
        root, journal_dir, selected_date, selected_logger
    )
    newline = "\r\n" if existing is not None and "\r\n" in existing else "\n"
    regime_label, regime_line, auto_content = _auto_lines(sections, brief_lines, newline)
    if existing is None:
        journal_content = _new_journal(selected_date, regime_label, regime_line, auto_content)
        status = JournalStatus.DRY_RUN_CREATED if dry_run else JournalStatus.CREATED
    else:
        journal_content = _update_existing(
            existing, selected_date, regime_label, regime_line, auto_content
        )
        status = JournalStatus.DRY_RUN_UPDATED if dry_run else JournalStatus.UPDATED

    if not dry_run:
        if brief_path is not None and brief_content is not None:
            try:
                brief_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise JournalError(f"could not create brief directory {brief_path.parent}") from exc
            _atomic_write(brief_path, brief_content)
        _atomic_write(target, journal_content)
    return JournalWriteResult(
        status=status,
        journal_path=target,
        journal_content=journal_content,
        brief_path=brief_path,
        brief_content=brief_content,
    )


__all__ = [
    "MARKET_END",
    "MARKET_START",
    "REGIME_END",
    "REGIME_START",
    "JournalError",
    "JournalPayloadError",
    "JournalStatus",
    "JournalWriteResult",
    "write_investing_journal",
]
