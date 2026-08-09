import pandas as pd
import pytest

from stock_data.contracts.kr_equity import KR_EQUITY_CANONICAL_UNIVERSE_DAILY
from stock_data.published.canonical_equity_universe import (
    CanonicalUniverseError, build_canonical_universe, price_identity_from_items,
    validate_canonical_universe,
)


def sources():
    listed = pd.DataFrame([
        {"date":"2026-08-06","market":"KOSPI","symbol":"005930","isin":"KR7005930003","name":"삼성전자"},
    ])
    price = price_identity_from_items([
        {"basDt":"20260806","mrktCtg":"KOSPI","srtnCd":"005930","isinCd":"KR7005930003","itmsNm":"삼성전자"},
        {"basDt":"20260806","mrktCtg":"KOSPI","srtnCd":"005935","isinCd":"KR7005931001","itmsNm":"삼성전자우"},
        {"basDt":"20260806","mrktCtg":"KOSPI","srtnCd":"900140","isinCd":"KYG5307W1015","itmsNm":"엘브이엠씨홀딩스"},
    ])
    master = pd.DataFrame([
        {"market":"KOSPI","symbol":"005930","name":"삼성전자"},
        {"market":"KOSPI","symbol":"005935","name":"삼성전자우"},
        {"market":"KOSPI","symbol":"900140","name":"엘브이엠씨홀딩스"},
        {"market":"KOSPI","symbol":"999999","name":"master only"},
    ])
    return listed, price, master


def test_union_preserves_price_only_and_excludes_master_only():
    result = build_canonical_universe(*sources())
    assert tuple(result.columns) == KR_EQUITY_CANONICAL_UNIVERSE_DAILY.column_names
    assert result.symbol.tolist() == ["005930","005935","900140"]
    assert result.universe_source.tolist() == ["listed_info+price","price_only","price_only"]
    assert result.master_present.all()
    assert result.set_index("symbol").loc["005935","security_type"] == "preferred_observed_name"
    assert result.set_index("symbol").loc["900140","security_type"] == "foreign_equity_observed_isin"


def test_provenance_and_isin_collisions_are_rejected():
    result = build_canonical_universe(*sources())
    result.loc[0,"universe_source"] = "price_only"
    with pytest.raises(CanonicalUniverseError, match="provenance"):
        validate_canonical_universe(result)
    listed, price, master = sources()
    price.loc[1,"isin"] = price.loc[0,"isin"]
    with pytest.raises(CanonicalUniverseError, match="ISIN collision"):
        build_canonical_universe(listed, price, master)


def test_daily_source_isin_disagreement_is_not_silently_resolved():
    listed, price, master = sources()
    listed.loc[0,"isin"] = "KR7005939999"
    with pytest.raises(CanonicalUniverseError, match="disagree"):
        build_canonical_universe(listed, price, master)
