"""Small, provider-free projections used by Home data cards."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import re
from typing import Mapping

import pandas as pd

from stock_web.api import datasets as dsx
from stock_web.api.fmt import KST


_NO_SCHEDULE_SOURCE = "일정 출처 없음 · 브리핑의 오늘 밤 항목만 표시"
_EVENT_LINE = re.compile(
    r"^\s*(?:[-*·]\s*)?(?P<time>(?:[01]\d|2[0-3]):[0-5]\d)\s+(?P<text>\S.*)\s*$"
)


def build_lending(project_root: Path) -> dict[str, object] | None:
    """Project the latest market-wide stock-lending balance and its session moves."""
    frame = dsx.load(
        project_root,
        "data/normalized/kr_stock_lending_market_daily",
        columns=[
            "date", "executed_shares", "returned_shares", "balance_shares",
            "balance_amount",
        ],
    )
    if frame is None or frame.empty or not {"date", "balance_amount"}.issubset(frame):
        return None
    work = frame[["date", "balance_amount"]].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["balance_amount"] = pd.to_numeric(work["balance_amount"], errors="coerce")
    work = (
        work.dropna(subset=["date", "balance_amount"])
        .sort_values("date", kind="stable")
        .drop_duplicates("date", keep="last")
    )
    if work.empty:
        return None
    values = work["balance_amount"].astype(float)

    def change(sessions: int) -> float | None:
        if len(values) <= sessions:
            return None
        previous = float(values.iloc[-sessions - 1])
        return (float(values.iloc[-1]) / previous - 1.0) * 100.0 if previous else None

    return {
        "balance_amount": float(values.iloc[-1]),
        "d1_pct": change(1),
        "d5_pct": change(5),
        "trend_20d": [float(value) for value in values.iloc[-20:]],
        "as_of": work["date"].iloc[-1].date().isoformat(),
    }


def _front_matter(text: str) -> tuple[dict[str, str], list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, lines
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return {}, lines
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if separator and key.strip():
            metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, lines[end + 1:]


def _is_sent(value: object) -> bool:
    return str(value or "").strip().casefold() in {"true", "yes", "1", "sent"}


def _brief_time(value: object) -> tuple[str, datetime | None]:
    raw = str(value or "").strip()
    if not raw:
        return "", None
    try:
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=KST)
        observed = observed.astimezone(KST)
        return observed.strftime("%H:%M"), observed
    except ValueError:
        match = re.search(r"(?<!\d)([0-2]\d:[0-5]\d)(?!\d)", raw)
        return (match.group(1), None) if match else ("", None)


def _brief_payload(path: Path, default_kind: str) -> tuple[dict[str, object], list[str], datetime | None] | None:
    metadata, body_lines = _front_matter(path.read_text(encoding="utf-8"))
    if not _is_sent(metadata.get("sent")):
        return None
    first = next((index for index, line in enumerate(body_lines) if line.strip()), None)
    if first is None:
        return None
    time_label, generated = _brief_time(metadata.get("generated_at_kst"))
    title = body_lines[first].strip().lstrip("#").strip()
    remaining = body_lines[first + 1:]
    while remaining and not remaining[0].strip():
        remaining.pop(0)
    if len(remaining) > 40:
        protected_indexes = [
            index for index, line in enumerate(remaining)
            if line.strip().startswith(("출처", "※"))
        ]
        protected_indexes = protected_indexes[-4:]
        leading_indexes = [
            index for index in range(len(remaining))
            if index not in protected_indexes
        ][:40 - len(protected_indexes)]
        remaining = [remaining[index] for index in sorted(leading_indexes + protected_indexes)]
    body = "\n".join(remaining).strip()
    return ({
        "kind": metadata.get("kind") or default_kind,
        "time": time_label,
        "title": title,
        "body": body,
    }, body_lines, generated)


def _events_from_lines(lines: list[str]) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    in_block = False
    for raw in lines:
        line = raw.strip()
        if "🌙 오늘 밤" in line or line.startswith("📅"):
            in_block = True
            inline = line.split("오늘 밤", 1)[-1].strip(" :-·") if "오늘 밤" in line else line[1:].strip(" :-·")
            match = _EVENT_LINE.match(inline)
            if match:
                events.append(match.groupdict())
            continue
        if not in_block:
            continue
        if line.startswith("#") or (line and line[0] in "🌅☀️📈📊💰🧭🔎"):
            in_block = False
            continue
        match = _EVENT_LINE.match(line)
        if match:
            events.append(match.groupdict())
    return events


def _legacy_schedule(project_root: Path) -> dict[str, object] | None:
    import json

    path = project_root / "data/local/calendar/events.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload["items"]
    if not isinstance(items, list):
        raise ValueError("items")
    clean: list[dict[str, object]] = []
    for item in items:
        when = item.get("when")
        what = item.get("what")
        importance = item.get("importance")
        if (
            not isinstance(when, str)
            or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d|\d{2}-\d{2}", when) is None
            or not isinstance(what, str) or not what.strip()
            or type(importance) is not int or importance not in {1, 2, 3}
        ):
            raise ValueError("item")
        clean.append({"when": when, "what": what.strip(), "importance": importance})
    return {"items": clean}


def build_schedule(project_root: Path, *, today: date | None = None) -> dict[str, object]:
    """Read today's sent briefs and extract their explicitly listed night events."""
    selected_day = today or datetime.now(KST).date()
    brief_root = project_root / "artifacts/local_user/briefs"
    briefs_with_meta: list[tuple[dict[str, object], list[str], datetime | None]] = []
    try:
        for kind in ("morning", "close"):
            path = brief_root / f"{selected_day.isoformat()}-{kind}.md"
            if path.is_file() and (parsed := _brief_payload(path, kind)) is not None:
                briefs_with_meta.append(parsed)
        briefs_with_meta.sort(
            key=lambda item: item[2] or datetime.min.replace(tzinfo=KST), reverse=True,
        )
        events: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for _brief, lines, _generated in briefs_with_meta:
            for event in _events_from_lines(lines):
                identity = (event["time"], event["text"])
                if identity not in seen:
                    events.append(event)
                    seen.add(identity)
        result: dict[str, object] = {
            "briefs": [item[0] for item in briefs_with_meta],
            "events": events,
            "note": "" if events else _NO_SCHEDULE_SOURCE,
        }
        try:
            legacy = _legacy_schedule(project_root)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            legacy = {"reason": "로컬 일정 파일 형식이 올바르지 않습니다."}
        if legacy is not None:
            result["legacy"] = legacy
        return result
    except (OSError, UnicodeError, TypeError, ValueError):
        return {
            "briefs": [], "events": [], "note": _NO_SCHEDULE_SOURCE,
            "reason": "오늘 브리핑을 읽을 수 없습니다.",
        }


def account_extras(account_page_payload: Mapping[str, object]) -> dict[str, object]:
    """Copy compact account-source and cash-flow facts already exposed by Account."""
    summary = account_page_payload.get("summary")
    sources = summary.get("sources", []) if isinstance(summary, Mapping) else []
    summary_rows: list[dict[str, object]] = []
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, Mapping):
            continue
        raw_as_of = source.get("as_of")
        try:
            if raw_as_of is None or str(raw_as_of).strip() == "":
                raise ValueError("missing as_of")
            stamp = pd.Timestamp(raw_as_of)
            if pd.isna(stamp):
                raise ValueError("invalid as_of")
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize(KST)
            else:
                stamp = stamp.tz_convert(KST)
            as_of = stamp.strftime("%m-%d %H:%M")
        except (TypeError, ValueError):
            as_of = str(source.get("as_of_label") or "")
        summary_rows.append({
            "label": str(source.get("name") or ""),
            "as_of": as_of,
            "included": bool(source.get("included")),
            "note": str(source.get("note") or "")[:80],
        })

    cash_flows = account_page_payload.get("cash_flows")
    entries = cash_flows.get("entries", []) if isinstance(cash_flows, Mapping) else []
    recent_cashflows: list[dict[str, object]] = []
    for entry in entries[:5] if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        account = str(entry.get("account") or "").strip()
        memo = str(entry.get("memo") or "").strip()
        label = " · ".join(part for part in (account, memo) if part)
        recent_cashflows.append({
            "date": str(entry.get("date") or ""),
            "label": label,
            "amount_krw": entry.get("amount_krw"),
        })
    return {"summary_rows": summary_rows, "recent_cashflows": recent_cashflows}


def _kr_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    if len(symbol) == 7 and symbol.startswith("A"):
        symbol = symbol[1:]
    return symbol.zfill(6) if symbol.isdigit() else symbol


def _held_symbols(project_root: Path, account_page_payload: Mapping[str, object]) -> tuple[set[str], set[str]]:
    from stock_data.gui.account_snapshot_service import LocalAccountSnapshotService

    kr: set[str] = set()
    us: set[str] = set()
    manual = account_page_payload.get("manual_accounts")
    accounts = manual.get("accounts", []) if isinstance(manual, Mapping) else []
    for account in accounts if isinstance(accounts, list) else []:
        if not isinstance(account, Mapping):
            continue
        target = kr if account.get("currency") == "KRW" else us
        positions = account.get("valued_positions") or account.get("positions") or []
        for position in positions if isinstance(positions, list) else []:
            if isinstance(position, Mapping) and position.get("ticker"):
                symbol = str(position["ticker"]).strip().upper()
                target.add(_kr_symbol(symbol) if target is kr else symbol)

    for path in (
        project_root / "data/normalized/toss_account_snapshot/latest.json",
        project_root / "data/local/account_snapshots/kb_self.json",
    ):
        try:
            snapshot = LocalAccountSnapshotService(path).load()
        except (OSError, UnicodeError, TypeError, ValueError):
            continue
        if not snapshot.displays_values:
            continue
        for position in snapshot.positions:
            currency = position.currency or snapshot.currency
            if currency == "KRW":
                kr.add(_kr_symbol(position.symbol))
            elif currency == "USD":
                us.add(position.symbol.strip().upper())
    return kr, us


def build_watchlist(project_root: Path) -> dict[str, object]:
    """Attach identifier-only holding flags to the existing Home watchlist rows."""
    from stock_web.api.account_page import build_account_page_data
    from stock_web.api.stocks_page import build_home_watchlist

    try:
        payload = build_home_watchlist(project_root)
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return {"reason": str(payload.get("reason") or "관심종목을 읽을 수 없습니다.")}
        account_payload = build_account_page_data(project_root)
        kr, us = _held_symbols(project_root, account_payload)
        held_count = 0
        projected: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            held = _kr_symbol(symbol) in kr if len(symbol) == 6 and symbol[0:1].isdigit() else symbol in us
            held_count += int(held)
            projected.append({**row, "held": held, "weight_pct": None})
        return {
            **payload,
            "rows": projected,
            "held_count": held_count,
            "watch_count": len(projected),
            "note": "보유 여부만 표시 · 수량과 비중은 내 계좌에서 확인",
        }
    except Exception:
        return {"reason": "관심종목 또는 보유 여부를 읽을 수 없습니다."}


__all__ = ["account_extras", "build_lending", "build_schedule", "build_watchlist"]
