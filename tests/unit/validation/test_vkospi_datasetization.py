import json
from stock_data.providers.krx_mdc.vkospi import frames_from_history,parse_history_body
from stock_data.validation.vkospi_daily import validate_vkospi_daily,validate_vkospi_raw_daily
def body():
    rows=[{"TRD_DD":"2026/08/14","CLSPRC_IDX":"55.31","PRV_DD_CMPR":"0.03","UPDN_RATE":"+0.05","OPNPRC_IDX":"55.38","HGPRC_IDX":"56.27","LWPRC_IDX":"55.00","FLUC_TP_CD":"1"},{"TRD_DD":"2026/08/13","CLSPRC_IDX":"55.28","PRV_DD_CMPR":"1.20","UPDN_RATE":"-2.12","OPNPRC_IDX":"54.56","HGPRC_IDX":"55.82","LWPRC_IDX":"54.53","FLUC_TP_CD":"2"}]
    return json.dumps({"output":rows}).encode()
def test_frames_preserve_raw_and_validate_normalized():
    rows,_,_=parse_history_body(body()); raw,norm=frames_from_history(rows,collected_at="2026-08-17T00:00:00Z",landing_reference="data/landing/x.json",response_sha256="a"*64)
    validate_vkospi_raw_daily(raw); validate_vkospi_daily(norm)
    assert raw.iloc[0].UPDN_RATE=="-2.12" and norm.market_date.tolist()==["2026-08-13","2026-08-14"]
