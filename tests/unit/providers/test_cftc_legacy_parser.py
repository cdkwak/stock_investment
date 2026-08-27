from io import BytesIO
from zipfile import ZipFile

import pytest

from stock_data.providers.cftc import CftcCotSchemaError
from stock_data.providers.cftc_legacy import LEGACY_FUTURES_ONLY, parse_historical_zip


def _archive(headers, row):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("legacy.txt", ",".join(headers) + "\n" + ",".join(row.get(header, "") for header in headers))
    return output.getvalue()


def test_legacy_source_rows_are_preserved_and_old_report_dates_validate():
    row = {"Market_and_Exchange_Names": "WHEAT", "As_of_Date_In_Form_YYMMDD": "860114", "Report_Date_as_YYYY-MM-DD": "01/14/1986 12:00:00 AM", "Noncommercial_Positions_Long_All": "1"}
    rows, schema = parse_historical_zip(_archive(list(row), row), report_type=LEGACY_FUTURES_ONLY)
    assert rows[0] == row
    assert schema["raw_rows"] == 1


def test_legacy_schema_missing_position_date_fails_closed():
    row = {"Market_and_Exchange_Names": "WHEAT", "Report_Date_as_YYYY-MM-DD": "1986-01-14"}
    with pytest.raises(CftcCotSchemaError, match="missing logical fields"):
        parse_historical_zip(_archive(list(row), row), report_type=LEGACY_FUTURES_ONLY)


def test_legacy_space_named_historical_header_is_validated_without_rewriting_keys():
    row = {"Market and Exchange Names": "WHEAT", "As of Date in Form YYMMDD": "971230", "As of Date in Form YYYY-MM-DD": "1997-12-30"}
    rows, schema = parse_historical_zip(_archive(list(row), row), report_type=LEGACY_FUTURES_ONLY)
    assert rows[0] == row
    assert schema["source_fields"]["position_date"] == "As of Date in Form YYMMDD"
