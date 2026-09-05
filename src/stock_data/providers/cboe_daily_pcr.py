"""One-request transports and strict parsers for Cboe put/call statistics."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import StringIO
import json
import math
import re
from typing import Callable, Mapping


CBOE_DAILY_PAGE_URL = "https://www.cboe.com/markets/us/options/market-statistics/daily"
CBOE_ARCHIVE_BASE_URL = "https://cdn.cboe.com/resources/options/volume_and_call_put_ratios"
CBOE_ARCHIVE_FILES = {
    "TOTAL": "totalpc.csv",
    "INDEX": "indexpc.csv",
    "EQUITY": "equitypc.csv",
    "ETP": "etppc.csv",
    "VIX": "vixpc.csv",
}
ARCHIVE_START_DATE = date(2006, 11, 1)
ARCHIVE_END_DATE = date(2019, 10, 4)
PROVIDER = "cboe_daily_market_statistics"
ARCHIVE_PROVIDER = "cboe_archive_csv"
REQUIRED_SCOPES = ("TOTAL", "INDEX", "ETP", "EQUITY", "VIX")
SUPPORTED_SCOPES = (*REQUIRED_SCOPES, "SPX_SPXW")


class CboeDailyPcrError(ValueError):
    pass


@dataclass(frozen=True)
class CboeDailyPcrDownload:
    body: bytes
    status_code: int
    content_type: str
    source_url: str
    retrieved_at: datetime


def _utc(value: datetime | None) -> datetime:
    captured = value or datetime.now(timezone.utc)
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise CboeDailyPcrError("retrieved_at must be timezone-aware")
    return captured.astimezone(timezone.utc)


def download_daily_pcr(
    *,
    transport: Callable[..., object],
    source_url: str = CBOE_DAILY_PAGE_URL,
    retrieved_at: datetime | None = None,
) -> CboeDailyPcrDownload:
    """Make exactly one public page request and return bytes without parsing."""
    response = transport(
        source_url,
        timeout=20,
        headers={
            "Accept": "text/html",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0 Safari/537.36"
            ),
        },
    )
    status_code = int(getattr(response, "status_code"))
    body = bytes(getattr(response, "content"))
    headers = getattr(response, "headers", {})
    content_type = str(headers.get("content-type", "")) if isinstance(headers, Mapping) else ""
    return CboeDailyPcrDownload(
        body=body,
        status_code=status_code,
        content_type=content_type,
        source_url=source_url,
        retrieved_at=_utc(retrieved_at),
    )


def _key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").strip().upper()).strip("_")


def _mapping_value(
    row: Mapping[str, object], *aliases: str, required: bool = True,
) -> object:
    normalized = {_key(name): value for name, value in row.items()}
    for alias in aliases:
        if _key(alias) in normalized:
            value = normalized[_key(alias)]
            if value is not None and str(value).strip() != "":
                return value
    if required:
        raise CboeDailyPcrError(f"missing Cboe field: {aliases[0]}")
    return None


def _count(value: object, field: str, *, nullable: bool = False) -> int | None:
    if value is None or str(value).strip() in {"", "-", "N/A", "null", "None"}:
        if nullable:
            return None
        raise CboeDailyPcrError(f"missing Cboe count: {field}")
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError as error:
        raise CboeDailyPcrError(f"invalid Cboe count: {field}") from error
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise CboeDailyPcrError(f"invalid Cboe count: {field}")
    return int(number)


def _ratio(value: object, field: str) -> Decimal:
    try:
        ratio = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise CboeDailyPcrError(f"invalid Cboe ratio: {field}") from error
    if not ratio.is_finite() or ratio < 0:
        raise CboeDailyPcrError(f"invalid Cboe ratio: {field}")
    return ratio


def _assert_published_ratio(
    *, call: int, put: int, published: object, scope: str,
) -> None:
    source_ratio = _ratio(published, scope)
    if call == 0:
        return
    calculated = (Decimal(put) / Decimal(call)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    published_rounded = source_ratio.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if calculated != published_rounded:
        raise CboeDailyPcrError(
            f"Cboe published volume ratio mismatch for {scope}: "
            f"computed {calculated}, published {published_rounded}"
        )


_FLIGHT_PUSH = re.compile(
    r"<script(?:\s[^>]*)?>\s*self\.__next_f\.push\((.*?)\)\s*</script>",
    re.DOTALL,
)


def _flight_text(html: str) -> str:
    chunks: list[str] = []
    for encoded in _FLIGHT_PUSH.findall(html):
        try:
            pushed = json.loads(encoded)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(pushed, list)
            and len(pushed) >= 2
            and pushed[0] == 1
            and isinstance(pushed[1], str)
        ):
            chunks.append(pushed[1])
    if not chunks:
        raise CboeDailyPcrError("Cboe page has no decodable Next.js flight data")
    return "".join(chunks)


def _payload_in(value: object) -> tuple[Mapping[str, object], object] | None:
    if isinstance(value, Mapping):
        selected_date = _mapping_value(value, "selectedDate", required=False)
        ratios = _mapping_value(value, "Ratios", "ratios", required=False)
        if selected_date is not None and isinstance(ratios, list):
            return value, selected_date
        options_data = _mapping_value(value, "optionsData", required=False)
        if selected_date is not None and isinstance(options_data, Mapping):
            ratios = _mapping_value(options_data, "Ratios", "ratios", required=False)
            if isinstance(ratios, list):
                return options_data, selected_date
        for nested in value.values():
            found = _payload_in(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _payload_in(nested)
            if found is not None:
                return found
    return None


def _extract_daily_payload(html: str) -> tuple[Mapping[str, object], object]:
    flight = _flight_text(html)
    decoder = json.JSONDecoder()
    anchors = [match.start() for match in re.finditer(r"selectedDate", flight, re.IGNORECASE)]
    for anchor in anchors:
        starts = [match.start() for match in re.finditer(r"\{", flight[:anchor])]
        for start in reversed(starts):
            try:
                candidate, _end = decoder.raw_decode(flight, start)
            except json.JSONDecodeError:
                continue
            found = _payload_in(candidate)
            if found is not None:
                return found
    raise CboeDailyPcrError(
        "Cboe flight data has no object containing Ratios and selectedDate"
    )


_GROUP_SCOPES = {
    "SUM_OF_ALL_PRODUCTS": "TOTAL",
    "INDEX_OPTIONS": "INDEX",
    "EXCHANGE_TRADED_PRODUCTS": "ETP",
    "EQUITY_OPTIONS": "EQUITY",
    "CBOE_VOLATILITY_INDEX_VIX": "VIX",
    "SPX_SPXW": "SPX_SPXW",
}
_RATIO_LABELS = {
    "TOTAL_PUT_CALL_RATIO": "TOTAL",
    "INDEX_PUT_CALL_RATIO": "INDEX",
    "EXCHANGE_TRADED_PRODUCTS_PUT_CALL_RATIO": "ETP",
    "EQUITY_PUT_CALL_RATIO": "EQUITY",
    "CBOE_VOLATILITY_INDEX_VIX_PUT_CALL_RATIO": "VIX",
    "SPX_SPXW_PUT_CALL_RATIO": "SPX_SPXW",
}


def _named_rows(value: object, group: str) -> dict[str, Mapping[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise CboeDailyPcrError(f"Cboe group rows are invalid: {group}")
    rows: dict[str, Mapping[str, object]] = {}
    for row in value:
        name = _key(_mapping_value(row, "name"))
        if name in rows:
            raise CboeDailyPcrError(f"duplicate Cboe row {name}: {group}")
        rows[name] = row
    for required in ("VOLUME", "OPEN_INTEREST"):
        if required not in rows:
            raise CboeDailyPcrError(f"Cboe group misses {required}: {group}")
    return rows


def _counts(row: Mapping[str, object], prefix: str) -> tuple[int, int]:
    call = _count(_mapping_value(row, "call"), f"{prefix}.call")
    put = _count(_mapping_value(row, "put"), f"{prefix}.put")
    total = _count(_mapping_value(row, "total"), f"{prefix}.total")
    assert call is not None and put is not None and total is not None
    if call + put != total:
        raise CboeDailyPcrError(f"Cboe total differs from call plus put: {prefix}")
    return call, put


def parse_daily_pcr(
    body: bytes,
    *,
    retrieved_at: datetime,
    observation_date: date | None = None,
    content_type: str = "text/html",
) -> list[dict[str, object]]:
    """Extract and normalize the daily page's server-rendered flight payload."""
    captured = _utc(retrieved_at)
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CboeDailyPcrError("Cboe response is not UTF-8") from error
    if not text.strip():
        raise CboeDailyPcrError("Cboe response is empty")
    if content_type and "html" not in content_type.lower():
        raise CboeDailyPcrError("Cboe daily response is not HTML")

    payload, selected_raw = _extract_daily_payload(text)
    try:
        selected_date = date.fromisoformat(str(selected_raw).strip())
    except ValueError as error:
        raise CboeDailyPcrError("invalid Cboe selectedDate") from error
    if observation_date is not None and selected_date != observation_date:
        raise CboeDailyPcrError(
            f"Cboe selectedDate {selected_date.isoformat()} differs from requested "
            f"date {observation_date.isoformat()}"
        )

    ratios_value = _mapping_value(payload, "Ratios", "ratios")
    if not isinstance(ratios_value, list) or not all(
        isinstance(item, Mapping) for item in ratios_value
    ):
        raise CboeDailyPcrError("Cboe Ratios rows are invalid")
    published: dict[str, object] = {}
    for ratio_row in ratios_value:
        label = _key(_mapping_value(ratio_row, "name"))
        scope = _RATIO_LABELS.get(label)
        if scope is not None:
            if scope in published:
                raise CboeDailyPcrError(f"duplicate Cboe published ratio: {scope}")
            published[scope] = _mapping_value(ratio_row, "value")

    parsed: list[dict[str, object]] = []
    seen: set[str] = set()
    for group_name, group_rows in payload.items():
        scope = _GROUP_SCOPES.get(_key(group_name))
        if scope is None:
            continue
        if scope in seen:
            raise CboeDailyPcrError(f"duplicate Cboe scope: {scope}")
        rows = _named_rows(group_rows, str(group_name))
        call_volume, put_volume = _counts(rows["VOLUME"], f"{scope}.volume")
        call_oi, put_oi = _counts(rows["OPEN_INTEREST"], f"{scope}.open_interest")
        if scope not in published:
            raise CboeDailyPcrError(f"Cboe Ratios misses scope: {scope}")
        _assert_published_ratio(
            call=call_volume, put=put_volume, published=published[scope], scope=scope,
        )
        parsed.append({
            "date": selected_date,
            "scope": scope,
            "call_volume": call_volume,
            "put_volume": put_volume,
            "volume_pcr": None if call_volume == 0 else put_volume / call_volume,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "oi_pcr": None if call_oi == 0 else put_oi / call_oi,
            "provider": PROVIDER,
            "retrieved_at": captured,
        })
        seen.add(scope)
    missing = set(REQUIRED_SCOPES) - seen
    if missing:
        raise CboeDailyPcrError(f"Cboe response misses required scopes: {sorted(missing)}")
    return sorted(parsed, key=lambda item: SUPPORTED_SCOPES.index(str(item["scope"])))


def parse_archive_pcr(
    body: bytes,
    *,
    scope: str,
    retrieved_at: datetime,
) -> list[dict[str, object]]:
    """Parse one legacy Cboe archive CSV into the normalized daily schema."""
    if scope not in CBOE_ARCHIVE_FILES:
        raise CboeDailyPcrError(f"unsupported Cboe archive scope: {scope}")
    captured = _utc(retrieved_at)
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CboeDailyPcrError("Cboe archive is not UTF-8") from error
    lines = text.splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if _key(line.split(",", 1)[0]) == "DATE"),
        None,
    )
    if header_index is None:
        raise CboeDailyPcrError("Cboe archive header is missing")
    reader = csv.DictReader(StringIO("\n".join(lines[header_index:])), skipinitialspace=True)
    parsed: list[dict[str, object]] = []
    seen: set[date] = set()
    for row in reader:
        raw_date = _mapping_value(row, "DATE")
        try:
            observed = datetime.strptime(str(raw_date).strip(), "%m/%d/%Y").date()
        except ValueError as error:
            raise CboeDailyPcrError("invalid Cboe archive date") from error
        if observed < ARCHIVE_START_DATE or observed > ARCHIVE_END_DATE:
            continue
        if observed in seen:
            raise CboeDailyPcrError(f"duplicate Cboe archive date: {observed.isoformat()}")
        call = _count(_mapping_value(row, "CALLS"), f"{scope}.archive.calls")
        put = _count(_mapping_value(row, "PUTS"), f"{scope}.archive.puts")
        total = _count(_mapping_value(row, "TOTAL"), f"{scope}.archive.total")
        assert call is not None and put is not None and total is not None
        if call + put != total:
            raise CboeDailyPcrError(f"Cboe archive total differs for {scope}")
        published = _mapping_value(row, "P/C Ratio")
        _assert_published_ratio(call=call, put=put, published=published, scope=scope)
        parsed.append({
            "date": observed,
            "scope": scope,
            "call_volume": call,
            "put_volume": put,
            "volume_pcr": None if call == 0 else put / call,
            "call_oi": None,
            "put_oi": None,
            "oi_pcr": None,
            "provider": ARCHIVE_PROVIDER,
            "retrieved_at": captured,
        })
        seen.add(observed)
    if not parsed:
        raise CboeDailyPcrError(f"Cboe archive has no supported rows: {scope}")
    return parsed


__all__ = [
    "ARCHIVE_END_DATE", "ARCHIVE_PROVIDER", "ARCHIVE_START_DATE",
    "CBOE_ARCHIVE_BASE_URL", "CBOE_ARCHIVE_FILES", "CBOE_DAILY_PAGE_URL",
    "CboeDailyPcrDownload", "CboeDailyPcrError", "PROVIDER", "REQUIRED_SCOPES",
    "SUPPORTED_SCOPES", "download_daily_pcr", "parse_archive_pcr", "parse_daily_pcr",
]
