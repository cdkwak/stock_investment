"""Small, provider-free projections used by Home data cards."""
from __future__ import annotations

from datetime import date, datetime
import json
import os
from pathlib import Path
import re
from typing import Mapping
from uuid import uuid4

import pandas as pd
import pyarrow.dataset as pads

from stock_web.api import datasets as dsx
from stock_web.api.fmt import KST


_NO_SCHEDULE_SOURCE = "일정 출처 없음 · 브리핑의 오늘 밤 항목만 표시"
_CREDIT_LAG_NOTE = "KOFIA 신용잔고는 2거래일 뒤 발표"
_LENDING_LAG_NOTE = "공공데이터포털 대차잔고는 1거래일 뒤 발표"
_EVENT_LINE = re.compile(
    r"^\s*(?:[-*·]\s*)?(?P<time>(?:[01]\d|2[0-3]):[0-5]\d)\s+(?P<text>\S.*)\s*$"
)
_JOURNAL_AMOUNT = re.compile(r"(?:[₩$]\s*\d)|(?:\d[\d,.]*\s*(?:원|만원|억|천만|달러|불))")
_AUTO_MARKER = re.compile(r"<!--\s*auto:(start|end)(?:\s+[^>]*)?-->")


class JournalNoteError(ValueError):
    """A sanitized one-line journal note validation or write failure."""


def _journal_directory(project_root: Path) -> Path:
    settings_path = project_root / "artifacts/local_user/web_settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise JournalNoteError("투자일지 폴더 설정을 읽을 수 없습니다.") from error
    raw = settings.get("journal_dir") if isinstance(settings, Mapping) else None
    if not isinstance(raw, str) or not raw.strip():
        raise JournalNoteError("투자일지 폴더가 설정되지 않았습니다.")
    directory = Path(raw).expanduser()
    if not directory.is_absolute():
        directory = project_root / directory
    if not directory.is_dir():
        raise JournalNoteError("투자일지 폴더를 찾을 수 없습니다.")
    return directory


def _auto_regions(text: str) -> tuple[tuple[int, int], ...]:
    starts: list[int] = []
    regions: list[tuple[int, int]] = []
    for marker in _AUTO_MARKER.finditer(text):
        if marker.group(1) == "start":
            starts.append(marker.start())
        elif not starts:
            raise JournalNoteError("투자일지 자동 영역 마커가 올바르지 않습니다.")
        else:
            regions.append((starts.pop(), marker.end()))
    if starts:
        raise JournalNoteError("투자일지 자동 영역 마커가 올바르지 않습니다.")
    return tuple(regions)


def _append_note_text(text: str, line: str) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    regions = _auto_regions(text)
    heading_matches = [
        match for match in re.finditer(r"(?m)^## 오늘 판단\s*$", text)
        if not any(start <= match.start() < end for start, end in regions)
    ]
    if not heading_matches:
        separator = "" if not text else ("" if text.endswith(("\n", "\r")) else newline)
        return f"{text}{separator}{newline if text else ''}## 오늘 판단{newline}{newline}{line}{newline}"
    heading = heading_matches[-1]
    section_start = heading.end()
    next_heading = re.search(r"(?m)^##\s+", text[section_start:])
    section_end = section_start + next_heading.start() if next_heading else len(text)
    insert_at = section_end
    while insert_at > section_start and text[insert_at - 1] in "\r\n":
        insert_at -= 1
    prefix = text[:insert_at]
    suffix = text[insert_at:]
    gap = newline if prefix.endswith(("\n", "\r")) else newline + newline
    return f"{prefix}{gap}{line}{newline}{suffix}"


def append_journal_note(
    project_root: Path, payload: object, *, now: datetime | None = None,
) -> dict[str, str]:
    """Append one amount-free judgment outside all auto-managed journal regions."""
    if not isinstance(payload, Mapping) or set(payload) != {"text"}:
        raise JournalNoteError("오늘 판단 요청 형식이 올바르지 않습니다.")
    note = re.sub(r"\s+", " ", str(payload.get("text") or "")).strip()
    if not note or len(note) > 300:
        raise JournalNoteError("오늘 판단은 1~300자로 적어 주세요.")
    if _JOURNAL_AMOUNT.search(note):
        raise JournalNoteError("금액은 적지 않습니다")
    observed = now or datetime.now(KST)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=KST)
    observed = observed.astimezone(KST)
    target = _journal_directory(Path(project_root).resolve()) / f"{observed.date().isoformat()} 투자.md"
    try:
        original = target.read_text(encoding="utf-8") if target.is_file() else ""
        original_regions = tuple(original[start:end] for start, end in _auto_regions(original))
        updated = _append_note_text(original, f"- {observed:%H:%M} 판단: {note}")
        updated_regions = tuple(updated[start:end] for start, end in _auto_regions(updated))
        if original_regions != updated_regions:
            raise JournalNoteError("투자일지 자동 영역을 보존할 수 없습니다.")
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except JournalNoteError:
        raise
    except (OSError, UnicodeError) as error:
        raise JournalNoteError("오늘 판단을 저장할 수 없습니다.") from error
    finally:
        if "temporary" in locals():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return {"status": "saved", "date": observed.date().isoformat(), "time": observed.strftime("%H:%M")}


def build_credit_balance_metadata(project_root: Path) -> dict[str, str] | None:
    """Return the retained credit-balance basis date and publication lag."""
    frame = dsx.load(
        project_root,
        "data/normalized/kr_credit_balance_daily",
        columns=["date"],
    )
    if frame is None or frame.empty or "date" not in frame:
        return None
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return {
        "as_of": dates.max().date().isoformat(),
        "lag_note": _CREDIT_LAG_NOTE,
    }


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

    result: dict[str, object] = {
        "balance_amount": float(values.iloc[-1]),
        "d1_pct": change(1),
        "d5_pct": change(5),
        "trend_20d": [float(value) for value in values.iloc[-20:]],
        "as_of": work["date"].iloc[-1].date().isoformat(),
        "lag_note": _LENDING_LAG_NOTE,
    }
    credit = build_credit_balance_metadata(project_root)
    if credit is not None:
        result["credit"] = credit
    return result


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


def _investor_flow_summaries(
    project_root: Path, symbols: set[str],
) -> dict[str, dict[str, object]]:
    """Return raw-won 1/5/20-session net-purchase sums by Korean symbol."""
    root = project_root / "data/normalized/kr_equity_investor_flow_daily"
    if not symbols or not root.is_dir():
        return {}
    columns = ["date", "symbol", "foreign_net", "institution_net", "individual_net"]
    try:
        dataset = pads.dataset(root, format="parquet", partitioning=None)
        read_columns = [*columns]
        if "captured_at" in dataset.schema.names:
            read_columns.append("captured_at")
        frame = dataset.to_table(
            columns=read_columns,
            filter=pads.field("symbol").isin(sorted(symbols)),
        ).to_pandas()
    except Exception:
        return {}
    if frame.empty or not set(columns).issubset(frame.columns):
        return {}
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in columns[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["symbol"] = frame["symbol"].astype(str).map(_kr_symbol)
    frame = frame.dropna(subset=["date", *columns[2:]])
    sort_columns = ["date"]
    if "captured_at" in frame.columns:
        frame["captured_at"] = pd.to_datetime(frame["captured_at"], utc=True, errors="coerce")
        sort_columns.append("captured_at")
    summaries: dict[str, dict[str, object]] = {}
    for symbol, rows in frame.groupby("symbol", sort=False):
        rows = (
            rows.sort_values(sort_columns, kind="stable")
            .drop_duplicates("date", keep="last")
        )
        if rows.empty:
            continue
        result: dict[str, object] = {"as_of": rows["date"].iloc[-1].date().isoformat()}
        for sessions in (1, 5, 20):
            tail = rows.tail(sessions)
            result.update({
                f"foreign_{sessions}d": int(tail["foreign_net"].sum()),
                f"institution_{sessions}d": int(tail["institution_net"].sum()),
                f"individual_{sessions}d": int(tail["individual_net"].sum()),
            })
        summaries[symbol] = result
    return summaries


def build_watchlist(project_root: Path) -> dict[str, object]:
    """Attach holding flags and retained Korean investor flow to Home rows."""
    from stock_web.api.account_page import build_account_page_data
    from stock_web.api.stocks_page import build_home_watchlist

    try:
        payload = build_home_watchlist(project_root)
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return {"reason": str(payload.get("reason") or "관심종목을 읽을 수 없습니다.")}
        account_payload = build_account_page_data(project_root)
        kr, us = _held_symbols(project_root, account_payload)
        symbols = {
            _kr_symbol(row.get("symbol")) for row in rows
            if isinstance(row, Mapping)
            and re.fullmatch(r"\d{6}", _kr_symbol(row.get("symbol")))
        }
        investor_by_symbol = _investor_flow_summaries(project_root, symbols)
        held_count = 0
        projected: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            held = _kr_symbol(symbol) in kr if len(symbol) == 6 and symbol[0:1].isdigit() else symbol in us
            held_count += int(held)
            projected_row = {**row, "held": held, "weight_pct": None}
            investor = investor_by_symbol.get(_kr_symbol(symbol))
            if investor is not None:
                projected_row["investor"] = investor
            projected.append(projected_row)
        return {
            **payload,
            "rows": projected,
            "held_count": held_count,
            "watch_count": len(projected),
            "note": "보유 여부만 표시 · 수량과 비중은 내 계좌에서 확인",
        }
    except Exception:
        return {"reason": "관심종목 또는 보유 여부를 읽을 수 없습니다."}


__all__ = [
    "JournalNoteError", "account_extras", "append_journal_note",
    "build_credit_balance_metadata", "build_lending", "build_schedule", "build_watchlist",
]


def build_vix_term_structure_rows(project_root: Path) -> list[list[str]]:
    """Home 파생 card rows from the retained ``us_vix_term_structure_daily`` dataset.

    Returns ``[["VIX 기간구조", "..."], ["SKEW", "..."]]`` or a single 미표시 row when the
    derived dataset is absent or unreadable. Values are never fetched live here.
    """
    root = Path(project_root) / "data/derived/us_vix_term_structure_daily"
    if not root.is_dir():
        return [["VIX 기간구조", "미표시"]]
    try:
        import pyarrow.dataset as ds

        frame = ds.dataset(str(root), format="parquet", partitioning=None).to_table(
            columns=["date", "vix", "vix9d", "vix3m", "skew", "ratio_1m_3m", "regime", "pct_rank_252"],
        ).to_pandas()
    except Exception:
        return [["VIX 기간구조", "미표시"]]
    if frame.empty:
        return [["VIX 기간구조", "미표시"]]
    frame = frame.sort_values("date")
    latest = frame.dropna(subset=["ratio_1m_3m"]).tail(1)
    rows: list[list[str]] = []
    if not latest.empty:
        row = latest.iloc[0]
        regime = {"contango": "콘탱고", "backwardation": "백워데이션"}.get(str(row["regime"]), "—")
        parts = [regime, f"1M/3M {float(row['ratio_1m_3m']):.2f}"]
        if row["pct_rank_252"] == row["pct_rank_252"]:
            parts.append(f"1년 백분위 {float(row['pct_rank_252']) * 100:.0f}%")
        vix9d, vix = row["vix9d"], row["vix"]
        if vix9d == vix9d and vix == vix:
            parts.append(f"9D {float(vix9d):.1f} · 1M {float(vix):.1f} · 3M {float(row['vix3m']):.1f}")
        rows.append(["VIX 기간구조", " · ".join(parts) + f" · {str(row['date'])[5:]}"])
    skew = frame.dropna(subset=["skew"]).tail(1)
    if not skew.empty:
        row = skew.iloc[0]
        rows.append(["SKEW", f"{float(row['skew']):.1f} · {str(row['date'])[5:]}"])
    return rows or [["VIX 기간구조", "미표시"]]
