from stock_data.providers.krx_open_api.equity import normalize_basic_info, normalize_daily_trade


def test_krx_trade_and_basic_info_map_verified_fields() -> None:
    trade = {"BAS_DD":"2019/01/02", "ISU_CD":"005930", "ISU_NM":"Samsung",
             "MKT_NM":"KOSPI", "TDD_OPNPRC":"40,000", "TDD_HGPRC":"41,000",
             "TDD_LWPRC":"39,000", "TDD_CLSPRC":"40,500", "ACC_TRDVOL":"10",
             "ACC_TRDVAL":"405,000", "MKTCAP":"405,000,000", "LIST_SHRS":"10,000"}
    normalized = normalize_daily_trade([trade], "KOSPI")
    assert normalized.price.iloc[0].to_dict()["close"] == 40500
    assert normalized.market_cap.iloc[0].to_dict()["shares_outstanding"] == 10000

    basic = {"ISU_CD":"KR7005930003", "ISU_SRT_CD":"005930", "ISU_NM":"Samsung",
             "ISU_ABBRV":"Samsung", "ISU_ENG_NM":"Samsung Electronics",
             "LIST_DD":"1975/06/11", "MKT_TP_NM":"KOSPI", "SECUGRP_NM":"Stock",
             "SECT_TP_NM":"-", "KIND_STKCERT_TP_NM":"Common", "PARVAL":"100",
             "LIST_SHRS":"10,000"}
    frame = normalize_basic_info([basic], "KOSPI", "20190102")
    assert frame.iloc[0]["isin"] == "KR7005930003"
    assert frame.iloc[0]["date"] == "2019-01-02"
