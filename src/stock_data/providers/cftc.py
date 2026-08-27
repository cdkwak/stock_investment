"""CFTC Commitments of Traders historical-file parsing for Landing-only pilots.

This module deliberately has no Normalized writer.  CFTC's annual historical
files identify the Tuesday position date, but the Commission states that it
does not maintain a historical release-date list.  A caller must therefore
retain the source date separately and must not manufacture an availability
date for point-in-time use.
"""

from __future__ import annotations

import csv
from datetime import datetime
import hashlib
from io import BytesIO, TextIOWrapper
from typing import Iterable
from zipfile import BadZipFile, ZipFile


DISAGGREGATED_FUTURES_ONLY_URL = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
TFF_FUTURES_ONLY_URL = "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"

POSITION_DATE_FIELD = "As_of_Date_In_Form_YYMMDD"
# Current annual ZIP schema, observed directly in the official 2025 files.
# The CFTC variable-name page still presents an older label.
REPORT_DATE_FIELD = "Report_Date_as_YYYY-MM-DD"
MARKET_FIELD = "Market_and_Exchange_Names"
FUTURES_ONLY_FIELD = "FutOnly_or_Combined"

COMMON_REQUIRED_FIELDS = frozenset({
    MARKET_FIELD, POSITION_DATE_FIELD, REPORT_DATE_FIELD,
    "CFTC_Contract_Market_Code", "CFTC_Market_Code", "CFTC_Commodity_Code",
    "Open_Interest_All", FUTURES_ONLY_FIELD,
})
FAMILY_REQUIRED_FIELDS = {
    "tff": frozenset({
        "Dealer_Positions_Long_All", "Asset_Mgr_Positions_Long_All",
        "Lev_Money_Positions_Long_All", "Other_Rept_Positions_Long_All",
        "Contract_Units",
    }),
    "disaggregated": frozenset({
        "Prod_Merc_Positions_Long_All", "Swap_Positions_Long_All",
        "M_Money_Positions_Long_All", "Other_Rept_Positions_Long_All",
        "Contract_Units",
    }),
}

# These are source market names, not an inferred asset mapping.  The pilot
# stops if an annual file does not contain exactly one requested market.
TARGET_MARKETS = {
    "tff": {
        "SP500": "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE",
        "NASDAQ100": "NASDAQ-100 Consolidated - CHICAGO MERCANTILE EXCHANGE",
        "RUSSELL2000": "MICRO E-MINI RUSSELL 2000 INDX - CHICAGO MERCANTILE EXCHANGE",
        "UST_2Y": "UST 2Y NOTE - CHICAGO BOARD OF TRADE",
        "UST_5Y": "UST 5Y NOTE - CHICAGO BOARD OF TRADE",
        "UST_10Y": "UST 10Y NOTE - CHICAGO BOARD OF TRADE",
        "UST_ULTRA": "ULTRA UST BOND - CHICAGO BOARD OF TRADE",
    },
    "disaggregated": {
        "GOLD": "GOLD - COMMODITY EXCHANGE INC.",
        "SILVER": "SILVER - COMMODITY EXCHANGE INC.",
        "COPPER": "COPPER- #1 - COMMODITY EXCHANGE INC.",
        "WTI": "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
        "NATURAL_GAS": "HENRY HUB - NEW YORK MERCANTILE EXCHANGE",
    },
}

# These exact CFTC identifier triples were observed in the retained 2025
# pilot.  The contract-market code is necessary: market and commodity codes
# alone identify multiple contracts in older annual files.
TARGET_IDENTITY_CODES = {
    "tff": {
        "SP500": ("13874A", "CME", "138"), "NASDAQ100": ("20974+", "CME", "209"),
        "RUSSELL2000": ("239747", "CME", "239"), "UST_2Y": ("042601", "CBT", "042"),
        "UST_5Y": ("044601", "CBT", "044"), "UST_10Y": ("043602", "CBT", "043"),
        "UST_ULTRA": ("020604", "CBT", "020"),
    },
    "disaggregated": {
        "GOLD": ("088691", "CMX", "088"), "SILVER": ("084691", "CMX", "084"),
        "COPPER": ("085692", "CMX", "085"), "WTI": ("067651", "NYME", "067"),
        "NATURAL_GAS": ("03565B", "NYME", "023"),
    },
}


class CftcCotSchemaError(RuntimeError):
    """The retained source cannot be safely interpreted under the pilot scope."""


def _parse_source_date(value: str, *, field: str) -> str:
    value = value.strip()
    formats = ("%y%m%d",) if field == POSITION_DATE_FIELD else (
        "%m/%d/%Y", "%m/%d/%Y %I:%M:%S %p", "%m-%d-%Y", "%Y-%m-%d", "%Y_%m_%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise CftcCotSchemaError(f"CFTC {field} has an unsupported value: {value!r}")


def parse_historical_zip(body: bytes, *, family: str) -> list[dict[str, str]]:
    """Read one official CFTC annual ZIP without modifying any raw value.

    The result remains source-shaped strings.  Derived dates are validation
    only and must not be written as a replacement for the raw source fields.
    """
    if family not in FAMILY_REQUIRED_FIELDS:
        raise ValueError("unsupported CFTC COT family")
    try:
        with ZipFile(BytesIO(body)) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].is_dir() or not members[0].filename.lower().endswith(".txt"):
                raise CftcCotSchemaError("CFTC annual ZIP must contain exactly one .txt file")
            name = members[0].filename
            if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
                raise CftcCotSchemaError("CFTC annual ZIP member path is unsafe")
            with archive.open(members[0]) as raw, TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames is None:
                    raise CftcCotSchemaError("CFTC annual text has no header")
                headers = {item.strip() for item in reader.fieldnames}
                required = COMMON_REQUIRED_FIELDS | FAMILY_REQUIRED_FIELDS[family]
                missing = sorted(required - headers)
                if missing:
                    raise CftcCotSchemaError(f"CFTC {family} schema missing fields: {missing}")
                rows = []
                for source_row in reader:
                    if None in source_row:
                        raise CftcCotSchemaError("CFTC annual text has surplus unnamed columns")
                    row = {str(key).strip(): "" if value is None else value for key, value in source_row.items()}
                    if not row[MARKET_FIELD].strip() or not row[POSITION_DATE_FIELD].strip() or not row[REPORT_DATE_FIELD].strip():
                        raise CftcCotSchemaError("CFTC row is missing market, position date, or report date")
                    _parse_source_date(row[POSITION_DATE_FIELD], field=POSITION_DATE_FIELD)
                    _parse_source_date(row[REPORT_DATE_FIELD], field=REPORT_DATE_FIELD)
                    rows.append(row)
    except BadZipFile as error:
        raise CftcCotSchemaError("CFTC response is not a readable ZIP file") from error
    if not rows:
        raise CftcCotSchemaError("CFTC annual text has no data rows")
    return rows


def describe_historical_zip(body: bytes, *, family: str) -> dict[str, object]:
    """Return a validated annual-file schema observation without changing it."""
    rows = parse_historical_zip(body, family=family)
    try:
        with ZipFile(BytesIO(body)) as archive:
            member = archive.infolist()[0]
            with archive.open(member) as raw, TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
                headers = next(csv.reader(stream))
    except (BadZipFile, StopIteration) as error:
        raise CftcCotSchemaError("CFTC annual text has no readable header") from error
    normalized_headers = [header.strip() for header in headers]
    return {
        "archive_member": member.filename,
        "header_count": len(normalized_headers),
        "header_sha256": hashlib.sha256("\x1f".join(normalized_headers).encode("utf-8")).hexdigest(),
        "headers": normalized_headers,
        "raw_rows": len(rows),
    }


def summarize_target_coverage(
    rows: Iterable[dict[str, str]], *, family: str, require_all: bool = True,
) -> dict[str, dict[str, object]]:
    """Validate the bounded source-market coverage and summarize it losslessly."""
    if family not in TARGET_MARKETS:
        raise ValueError("unsupported CFTC COT family")
    rows = list(rows)
    result: dict[str, dict[str, object]] = {}
    for target, market in TARGET_MARKETS[family].items():
        contract_market_code, market_code, commodity_code = TARGET_IDENTITY_CODES[family][target]
        selected = [row for row in rows if (
            row["CFTC_Contract_Market_Code"].strip() == contract_market_code
            and row["CFTC_Market_Code"].strip() == market_code
            and row["CFTC_Commodity_Code"].strip() == commodity_code
        )]
        if not selected:
            if require_all:
                raise CftcCotSchemaError(f"CFTC {family} target is absent: {target} ({market})")
            result[target] = {
                "source_market_name": None, "rows": 0,
                "position_date_min": None, "position_date_max": None,
                "position_years": [],
                "source_report_dates_distinct": 0, "release_date": None,
                "release_date_status": "NOT_PUBLISHED_IN_HISTORICAL_ANNUAL_FILE",
                "target_match_status": "PILOT_IDENTITY_NOT_PRESENT",
            }
            continue
        non_futures_only = {row[FUTURES_ONLY_FIELD].strip() for row in selected if row[FUTURES_ONLY_FIELD].strip() != "FutOnly"}
        if non_futures_only:
            raise CftcCotSchemaError(f"CFTC {family} target has unexpected FutOnly_or_Combined values: {sorted(non_futures_only)}")
        dates = [_parse_source_date(row[POSITION_DATE_FIELD], field=POSITION_DATE_FIELD) for row in selected]
        if len(dates) != len(set(dates)):
            raise CftcCotSchemaError(f"CFTC {family} target has duplicate position dates: {target}")
        # This date is a report-date source field, not a release timestamp.
        report_dates = {_parse_source_date(row[REPORT_DATE_FIELD], field=REPORT_DATE_FIELD) for row in selected}
        result[target] = {
            "source_market_name": sorted({row[MARKET_FIELD].strip() for row in selected}),
            "rows": len(selected),
            "position_date_min": min(dates),
            "position_date_max": max(dates),
            "position_years": sorted({value[:4] for value in dates}),
            "source_report_dates_distinct": len(report_dates),
            "release_date": None,
            "release_date_status": "NOT_PUBLISHED_IN_HISTORICAL_ANNUAL_FILE",
            "target_match_status": "PILOT_IDENTITY_MATCHED",
        }
    return result
