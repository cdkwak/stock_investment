"""Lossless parser for official CFTC Legacy COT historical archives.

Legacy report rows must remain separate from TFF and Disaggregated reports.
This module validates archive shape and source date fields only; it never maps
or converts participant categories.
"""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
from io import BytesIO, TextIOWrapper
from zipfile import BadZipFile, ZipFile

from stock_data.providers.cftc import CftcCotSchemaError


LEGACY_FUTURES_ONLY = "LEGACY_FUTURES_ONLY"
LEGACY_FUTURES_OPTIONS_COMBINED = "LEGACY_FUTURES_OPTIONS_COMBINED"
MARKET_FIELD = "Market_and_Exchange_Names"
POSITION_DATE_FIELD = "As_of_Date_In_Form_YYMMDD"
REPORT_DATE_FIELD = "Report_Date_as_YYYY-MM-DD"
FIELD_ALIASES = {
    "market": (MARKET_FIELD, "Market and Exchange Names"),
    "position_date": (POSITION_DATE_FIELD, "As of Date in Form YYMMDD"),
    # Legacy archives use the as-of date rendered in ISO form, not a release date.
    "source_date": (REPORT_DATE_FIELD, "As of Date in Form YYYY-MM-DD"),
}


def parse_position_date(value: str) -> str:
    try:
        return datetime.strptime(value.strip(), "%y%m%d").date().isoformat()
    except ValueError as error:
        raise CftcCotSchemaError(f"CFTC Legacy position date has unsupported value: {value!r}") from error


def _parse_report_date(value: str) -> str:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%Y %I:%M:%S %p"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise CftcCotSchemaError(f"CFTC Legacy report date has unsupported value: {value!r}")


def parse_historical_zip(body: bytes, *, report_type: str) -> tuple[list[dict[str, str]], dict[str, object]]:
    if report_type not in {LEGACY_FUTURES_ONLY, LEGACY_FUTURES_OPTIONS_COMBINED}:
        raise ValueError("unsupported CFTC Legacy report type")
    try:
        with ZipFile(BytesIO(body)) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].is_dir() or not members[0].filename.lower().endswith(".txt"):
                raise CftcCotSchemaError("CFTC Legacy archive must contain exactly one .txt file")
            member = members[0]
            if member.filename.startswith(("/", "\\")) or ".." in member.filename.replace("\\", "/").split("/"):
                raise CftcCotSchemaError("CFTC Legacy archive member path is unsafe")
            with archive.open(member) as raw, TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames is None:
                    raise CftcCotSchemaError("CFTC Legacy text has no header")
                headers = [header.strip() for header in reader.fieldnames]
                source_fields = {
                    logical: next((candidate for candidate in candidates if candidate in headers), None)
                    for logical, candidates in FIELD_ALIASES.items()
                }
                missing = sorted(logical for logical, field in source_fields.items() if field is None)
                if missing:
                    raise CftcCotSchemaError(f"CFTC Legacy schema missing logical fields: {missing}")
                rows = []
                for source_row in reader:
                    if None in source_row:
                        raise CftcCotSchemaError("CFTC Legacy text has surplus unnamed columns")
                    row = {str(key).strip(): "" if value is None else value for key, value in source_row.items()}
                    if not row[str(source_fields["market"])].strip():
                        raise CftcCotSchemaError("CFTC Legacy row has no market name")
                    parse_position_date(row[str(source_fields["position_date"])])
                    _parse_report_date(row[str(source_fields["source_date"])])
                    rows.append(row)
    except BadZipFile as error:
        raise CftcCotSchemaError("CFTC Legacy response is not a readable ZIP file") from error
    if not rows:
        raise CftcCotSchemaError("CFTC Legacy text has no data rows")
    return rows, {
        "archive_member": member.filename,
        "header_count": len(headers),
        "header_sha256": hashlib.sha256("\x1f".join(headers).encode("utf-8")).hexdigest(),
        "headers": headers,
        "source_fields": source_fields,
        "raw_rows": len(rows),
    }
