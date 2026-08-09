from stock_data.contracts.kr_equity import KR_EQUITY_UNIVERSE_DAILY
from stock_data.providers.data_go_kr.universe import normalize_universe_items


def test_universe_filters_konex_and_normalizes_symbol():
    base={"basDt":"20260806","isinCd":"KR7005930003","itmsNm":"삼성전자",
          "crno":"1301110006246","corpNm":"삼성전자(주)"}
    frame=normalize_universe_items([{**base,"mrktCtg":"KOSPI","srtnCd":"A005930"},
                                    {**base,"mrktCtg":"KONEX","srtnCd":"A005931"}])
    assert frame.to_dict("records")[0]["symbol"]=="005930" and len(frame)==1
    assert tuple(frame.columns)==KR_EQUITY_UNIVERSE_DAILY.column_names
