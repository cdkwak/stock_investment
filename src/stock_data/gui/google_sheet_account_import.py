from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from pathlib import Path
import re

from stock_data.gui.manual_account_store import (
    ManualAccountPosition,
    ManualAccountRecord,
    ManualAccountRegistry,
    manual_account_registry_payload,
    parse_manual_account_registry,
)


_TITLE_DATE = re.compile(r"\((?P<year>\d{2})\.(?P<month>\d{1,2})\.(?P<day>\d{1,2})\)")
_SECTION = {"아빠 ISA 60%": ("manual:appa_isa", "아빠 ISA", "ISA"),
            "아빠 종합 40%": ("manual:appa_general", "아빠 종합", "GENERAL")}
_HEADERS = {
    ("EFT", "종목 티커", "수량", "평균단가", "현재단가", "구매총액"),
    ("ETF", "종목 티커", "수량", "평균단가", "현재단가", "구매총액"),
}


def _number(text: str, *, optional: bool = False) -> float | None:
    compact = text.strip().replace(",", "").replace("₩", "").replace("원", "")
    if not compact:
        if optional:
            return None
        raise ValueError("required Google Sheet numeric cell is empty")
    try:
        return float(compact)
    except ValueError as error:
        raise ValueError("Google Sheet numeric cell is invalid") from error


def parse_appa_sheet_csv(text: str) -> ManualAccountRegistry:
    """Parse a user-exported `아빠` tab; never retain spreadsheet identity."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("Google Sheet CSV is empty")
    rows = list(csv.reader(StringIO(text)))
    if not rows or not rows[0]:
        raise ValueError("Google Sheet CSV title is missing")
    match = _TITLE_DATE.search(rows[0][0])
    if match is None:
        raise ValueError("Google Sheet CSV snapshot date is missing")
    snapshot_date = date(
        2000 + int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
    ).isoformat()
    accounts: list[ManualAccountRecord] = []
    index = 0
    while index < len(rows):
        first = rows[index][0].strip() if rows[index] else ""
        section = _SECTION.get(first)
        if section is None:
            index += 1
            continue
        if index + 1 >= len(rows):
            raise ValueError("Google Sheet section header is missing")
        header = tuple(
            rows[index + 1][column].strip()
            if column < len(rows[index + 1]) else ""
            for column in range(6)
        )
        if header not in _HEADERS:
            raise ValueError("Google Sheet holdings headers do not match")
        positions: list[ManualAccountPosition] = []
        cursor = index + 2
        while cursor < len(rows):
            row = rows[cursor]
            name = row[0].strip() if row else ""
            ticker = row[1].strip() if len(row) > 1 else ""
            if not name and not ticker:
                break
            if name in _SECTION:
                break
            if len(row) < 6:
                raise ValueError("Google Sheet holding row is incomplete")
            quantity = _number(row[2])
            average_cost = _number(row[3], optional=True)
            purchase_total = _number(row[5], optional=True)
            positions.append(ManualAccountPosition(
                name, ticker, float(quantity), average_cost, purchase_total,
            ))
            cursor += 1
        if not positions:
            raise ValueError("Google Sheet section contains no holdings")
        source_id, label, account_kind = section
        accounts.append(ManualAccountRecord(
            source_id, label, account_kind, snapshot_date, "KRW", tuple(positions),
        ))
        index = cursor
    if {account.source_id for account in accounts} != {
        "manual:appa_isa", "manual:appa_general",
    }:
        raise ValueError("Google Sheet required sections are missing or duplicated")
    registry = ManualAccountRegistry(tuple(accounts))
    return parse_manual_account_registry(manual_account_registry_payload(registry))


def load_appa_sheet_csv(path: Path) -> ManualAccountRegistry:
    return parse_appa_sheet_csv(Path(path).read_text(encoding="utf-8-sig"))


__all__ = ["load_appa_sheet_csv", "parse_appa_sheet_csv"]
