from io import BytesIO
from zipfile import ZipFile

import pytest

from stock_data.providers.cftc import (
    CftcCotSchemaError, TARGET_IDENTITY_CODES, TARGET_MARKETS, parse_historical_zip,
    summarize_target_coverage,
)


def _zip_csv(headers, rows):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("annual.txt", ",".join(headers) + "\n" + "\n".join(",".join(row.get(header, "") for header in headers) for row in rows))
    return output.getvalue()


def _row(market, *, futures_only="FutOnly"):
    return {
        "Market_and_Exchange_Names": market, "As_of_Date_In_Form_YYMMDD": "250107",
        "Report_Date_as_YYYY-MM-DD": "2025-01-07", "CFTC_Contract_Market_Code": "001",
        "CFTC_Market_Code": "001", "CFTC_Commodity_Code": "001", "Open_Interest_All": "1",
        "FutOnly_or_Combined": futures_only, "Prod_Merc_Positions_Long_All": "1",
        "Swap_Positions_Long_All": "1", "M_Money_Positions_Long_All": "1",
        "Other_Rept_Positions_Long_All": "1", "Dealer_Positions_Long_All": "1",
        "Asset_Mgr_Positions_Long_All": "1", "Lev_Money_Positions_Long_All": "1", "Contract_Units": "CONTRACTS",
    }


def test_disaggregated_parser_preserves_raw_fields_and_blocks_release_date_inference():
    headers = list(_row("GOLD - COMMODITY EXCHANGE INC.").keys())
    rows = parse_historical_zip(_zip_csv(headers, [_row("GOLD - COMMODITY EXCHANGE INC.")]), family="disaggregated")
    with pytest.raises(CftcCotSchemaError, match="target is absent"):
        summarize_target_coverage(rows, family="disaggregated")
    assert rows[0]["As_of_Date_In_Form_YYMMDD"] == "250107"


def test_schema_anomaly_and_non_futures_rows_fail_closed():
    row = _row("E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE", futures_only="Combined")
    row["CFTC_Contract_Market_Code"] = "13874A"; row["CFTC_Market_Code"] = "CME"; row["CFTC_Commodity_Code"] = "138"
    headers = list(row.keys())
    rows = parse_historical_zip(_zip_csv(headers, [row]), family="tff")
    with pytest.raises(CftcCotSchemaError, match="unexpected FutOnly_or_Combined"):
        summarize_target_coverage(rows, family="tff")
    headers.remove("Contract_Units")
    with pytest.raises(CftcCotSchemaError, match="missing fields"):
        parse_historical_zip(_zip_csv(headers, [row]), family="tff")


def test_complete_target_summary_keeps_release_date_unknown():
    rows = []
    for target, market in TARGET_MARKETS["tff"].items():
        row = _row(market)
        row["CFTC_Contract_Market_Code"], row["CFTC_Market_Code"], row["CFTC_Commodity_Code"] = TARGET_IDENTITY_CODES["tff"][target]
        rows.append(row)
    headers = list(rows[0].keys())
    parsed = parse_historical_zip(_zip_csv(headers, rows), family="tff")
    summary = summarize_target_coverage(parsed, family="tff")
    assert set(summary) == set(TARGET_MARKETS["tff"])
    assert all(item["position_years"] == ["2025"] for item in summary.values())
    assert all(item["release_date"] is None for item in summary.values())
    assert all(item["release_date_status"] == "NOT_PUBLISHED_IN_HISTORICAL_ANNUAL_FILE" for item in summary.values())


def test_historical_combined_report_date_timestamp_is_validated_without_rewriting_raw_value():
    row = _row("TEST MARKET")
    row["Report_Date_as_YYYY-MM-DD"] = "06/13/2006 12:00:00 AM"
    parsed = parse_historical_zip(_zip_csv(list(row), [row]), family="tff")
    assert parsed[0]["Report_Date_as_YYYY-MM-DD"] == "06/13/2006 12:00:00 AM"
