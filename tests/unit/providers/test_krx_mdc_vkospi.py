import json
import pytest
from stock_data.providers.krx_mdc.vkospi import BUSINESS_BLD,VKOSPISourceError,parse_pilot_body,request_payload

EXPECTED={"2026-08-12":"56.48","2026-08-13":"55.28","2026-08-14":"55.31"}
def body():
    rows=[]
    for date,close in EXPECTED.items(): rows.append({"TRD_DD":date.replace("-","/"),"CLSPRC_IDX":close,"PRV_DD_CMPR":"0","UPDN_RATE":"0","OPNPRC_IDX":close,"HGPRC_IDX":close,"LWPRC_IDX":close,"FLUC_TP_CD":"1"})
    return json.dumps({"output":rows,"CURRENT_DATETIME":"2026.08.17 PM 02:05:18"}).encode()
def test_request_is_exact_official_scope():
    p=request_payload("20260812","20260814")
    assert p["bld"]==BUSINESS_BLD and p["indTpCd"]=="1" and p["idxIndCd"]=="300"
def test_exact_close_pilot_passes(): assert len(parse_pilot_body(body(),expected_closes=EXPECTED).rows)==3
def test_close_mismatch_fails_closed():
    with pytest.raises(VKOSPISourceError,match="EXACT_CLOSE_MISMATCH"): parse_pilot_body(body(),expected_closes={**EXPECTED,"2026-08-14":"55.32"})
def test_html_fails_closed():
    with pytest.raises(VKOSPISourceError,match="HTML_OR_BLOCK_PAGE"): parse_pilot_body(b"<html>blocked</html>",expected_closes=EXPECTED)
