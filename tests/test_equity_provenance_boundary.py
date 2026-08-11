import pandas as pd
import pytest

from stock_data.contracts.kr_equity import KR_EQUITY_PRICE_DAILY
from stock_data.providers.data_go_kr.stock_price import normalize_stock_price_items
from stock_data.providers.krx_open_api.equity import normalize_basic_info, normalize_daily_trade
from stock_data.published.canonical_equity_universe import (
    CanonicalUniverseError, build_canonical_universe, validate_canonical_universe,
)


def krx_trade(date="2019/12/30"):
    return {"BAS_DD":date, "ISU_CD":"123456", "ISU_NM":"Historical Delisted",
            "MKT_NM":"KOSPI", "TDD_OPNPRC":"100", "TDD_HGPRC":"110",
            "TDD_LWPRC":"90", "TDD_CLSPRC":"105", "ACC_TRDVOL":"10",
            "ACC_TRDVAL":"1050", "MKTCAP":"105000", "LIST_SHRS":"1000"}


def krx_basic():
    return {"ISU_CD":"KR7123456000", "ISU_SRT_CD":"123456", "ISU_NM":"Historical Delisted",
            "ISU_ABBRV":"Historical", "ISU_ENG_NM":"Historical Delisted",
            "LIST_DD":"2010/01/04", "MKT_TP_NM":"KOSPI", "SECUGRP_NM":"Stock",
            "SECT_TP_NM":"-", "KIND_STKCERT_TP_NM":"Preferred", "PARVAL":"5000",
            "LIST_SHRS":"1000"}


def test_historical_missing_master_nullable_identity_and_exact_union_are_preserved() -> None:
    trade = normalize_daily_trade([krx_trade()], "KOSPI")
    basic = normalize_basic_info([krx_basic()], "KOSPI", "20191230")
    assert basic.loc[0, "corporate_number"] is None
    assert basic.loc[0, "corporate_name"] is None
    assert trade.price.loc[0, "source"] == basic.loc[0, "source"] == "krx_open_api"
    identity = basic[["date", "market", "symbol", "isin", "name"]]
    empty_master = pd.DataFrame(columns=["market", "symbol"])
    canonical = build_canonical_universe(basic, identity, empty_master)
    assert canonical.symbol.tolist() == ["123456"]
    assert not canonical.master_present.any()
    assert canonical.universe_source.tolist() == ["listed_info+price"]


def test_2019_krx_to_2020_fsc_has_one_price_contract_with_provenance() -> None:
    krx = normalize_daily_trade([krx_trade()], "KOSPI").price
    fsc = normalize_stock_price_items([{
        "basDt":"20200102", "srtnCd":"123456", "isinCd":"KR7123456000",
        "itmsNm":"Historical Delisted", "mrktCtg":"KOSPI", "clpr":"105",
        "vs":"0", "fltRt":"0", "mkp":"100", "hipr":"110", "lopr":"90",
        "trqu":"10", "trPrc":"1050", "lstgStCnt":"1000", "mrktTotAmt":"105000",
    }]).price
    boundary = pd.concat([krx, fsc], ignore_index=True).sort_values(
        list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    assert tuple(boundary.columns) == KR_EQUITY_PRICE_DAILY.column_names
    assert boundary.source.tolist() == ["krx_open_api", "data_go_kr"]
    assert boundary.source_date.tolist() == boundary.date.tolist()


def test_canonical_duplicate_is_rejected_across_boundary_sources() -> None:
    basic = normalize_basic_info([krx_basic()], "KOSPI", "20191230")
    duplicated = pd.concat([basic, basic], ignore_index=True)
    identity = basic[["date", "market", "symbol", "isin", "name"]]
    with pytest.raises(CanonicalUniverseError, match="duplicate"):
        build_canonical_universe(duplicated, identity, pd.DataFrame(columns=["market", "symbol"]))
