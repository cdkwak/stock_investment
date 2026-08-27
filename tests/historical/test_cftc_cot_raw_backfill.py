from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from stock_data.providers.cftc import DISAGGREGATED_FUTURES_ONLY_URL, TFF_FUTURES_ONLY_URL
from stock_data.providers.public_http_capture import capture_public_response


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts/manual/backfill/backfill_cftc_cot_historical_raw.py"
    spec = spec_from_file_location("cftc_raw_backfill_test", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _zip(headers, row):
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("annual.txt", ",".join(headers) + "\n" + ",".join(row.get(header, "") for header in headers))
    return payload.getvalue()


class _Response:
    status_code = 200
    headers = {"Content-Type": "application/zip"}
    def __init__(self, content): self.content = content


class _CrashSession:
    def get(self, *args, **kwargs): raise AssertionError("existing verified Landing must not be downloaded again")


def _source_row(*, family):
    row = {
        "Market_and_Exchange_Names": "TEST MARKET", "As_of_Date_In_Form_YYMMDD": "060613",
        "Report_Date_as_YYYY-MM-DD": "2006-06-13", "CFTC_Contract_Market_Code": "001",
        "CFTC_Market_Code": "XXX", "CFTC_Commodity_Code": "001", "Open_Interest_All": "1",
        "FutOnly_or_Combined": "FutOnly", "Other_Rept_Positions_Long_All": "1", "Contract_Units": "CONTRACTS",
    }
    if family == "tff":
        row.update({"Dealer_Positions_Long_All": "1", "Asset_Mgr_Positions_Long_All": "1", "Lev_Money_Positions_Long_All": "1"})
    else:
        row.update({"Prod_Merc_Positions_Long_All": "1", "Swap_Positions_Long_All": "1", "M_Money_Positions_Long_All": "1"})
    return row


def test_existing_verified_pilot_style_landing_is_adopted_without_network(tmp_path):
    module = _load_module()
    for family, source_key, url in [
        *module.HISTORICAL_COMBINED,
        ("tff", "2017", TFF_FUTURES_ONLY_URL.format(year=2017)),
        ("disaggregated", "2017", DISAGGREGATED_FUTURES_ONLY_URL.format(year=2017)),
    ]:
        row = _source_row(family=family)
        capture_public_response(
            root=tmp_path / "data/landing/cftc/existing", provider="cftc", operation=f"cot_{family}_futures_only_annual_zip",
            request_url=url, request_parameters={}, response=_Response(_zip(list(row), row)),
        )
    result = module.run(tmp_path, session=_CrashSession(), current_year=2017)
    assert result["total_raw_files"] == 4
    assert {entry["capture_status"] for entry in result["entries"]} == {"ADOPTED_EXISTING_VERIFIED"}
    state = json.loads((tmp_path / module.STATE_RELATIVE / "latest.json").read_text())
    assert state["normalized_mutation"] is False
