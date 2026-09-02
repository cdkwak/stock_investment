"""Shared display-only formatting helpers for the local web dashboard."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def format_kst(value: object) -> str:
    """Format a date or timestamp as a compact KST label.

    Date-only values retain date-only meaning. Naive timestamps are interpreted
    as UTC because retained scheduler timestamps without an offset are UTC.
    """

    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value.strftime("%m-%d")
    else:
        text = str(value).strip()
        if _DATE_ONLY.fullmatch(text):
            return text[5:]
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST).strftime("%m-%d %H:%M")


__all__ = ["KST", "format_kst"]
