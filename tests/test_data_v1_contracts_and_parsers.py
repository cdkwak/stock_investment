import pandas as pd
import pytest

from stock_data.contracts.data_v1 import DATA_V1_CONTRACTS, KR_EQUITY_RIGHTS_SCHEDULE
from stock_data.providers.data_go_kr.data_v1 import (
    normalize_credit_balance, normalize_dividend, normalize_futures,
    normalize_market_liquidity, normalize_options, normalize_rights,
    normalize_stock_lending, normalize_stock_lending_market,
    normalize_stock_lending_participant,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import DataV1ValidationError, validate_data_v1


def test_contracts_are_separate_normalized_parquet_sources():
    assert len(DATA_V1_CONTRACTS) == 9
    assert len({c.name for c in DATA_V1_CONTRACTS}) == 9
    assert all(c.layer == "normalized" and c.storage_format == "parquet" for c in DATA_V1_CONTRACTS)
    assert "short" not in next(c for c in DATA_V1_CONTRACTS if c.name == "kr_stock_lending_daily").name


def test_kofia_fixtures_parse_to_distinct_datasets():
    liquidity = normalize_market_liquidity([{
        "basDt":"20220928", "invrDpsgAmt":"52567764085909", "onbdDrvPrdTrRcAdvAmt":"11463650187462",
        "toCstRpchCndBndSlgBal":"75296273129430", "brkTrdUcolMny":"285674308674",
        "brkTrdUcolMnyVsOppsTrdAmt":"29413324925", "ucolMnyVsOppsTrdRlImpt":"13",
    }])
    credit = normalize_credit_balance([{
        "basDt":"20220929", "crdTrFingWhl":"17461184", "crdTrFingScrs":"9364631",
        "crdTrFingKosdaq":"8096553", "crdTrLndrWhl":"52862", "crdTrLndrScrs":"39506",
        "crdTrLndrKosdaq":"13356", "sbscCapLn":"0", "dpsgScrtMogFing":"19995816",
    }])
    assert liquidity.loc[0, "forced_sale_ratio"] == 13
    assert credit.loc[0, "credit_financing_total"] == 17461184


def test_derivative_source_fields_and_zero_are_preserved():
    common = {"basDt":"20220919", "prdCtg":"파생 선물 코스피200", "srtnCd":"101SC000",
              "isinCd":"KR4101SC0009", "itmsNm":"코스피200 F 202212", "mkp":"306.1",
              "hipr":"310.1", "lopr":"305.9", "clpr":"306.9", "trqu":"241199",
              "trPrc":"18546277242500", "opnint":"301909"}
    futures = normalize_futures([{**common, "sptPrc":"306.49", "stmPrc":"306.9"}])
    options = normalize_options([{**common, "prdCtg":"파생 옵션 코스피200", "srtnCd":"201SA180",
                                  "isinCd":"KR4201SA1800", "itmsNm":"코스피200 C 202210 180.0",
                                  "mkp":"0", "hipr":"0", "lopr":"0", "clpr":"0", "trqu":"0",
                                  "trPrc":"0", "nxtDdBsPrc":"126.15", "iptVlty":"3"}])
    assert futures.loc[0, "settlement_price"] == 306.9
    assert options.loc[0, "implied_volatility"] == 3
    assert options.loc[0, ["open","high","low","close","volume"]].eq(0).all()


def test_signed_futures_prices_are_preserved_but_negative_volume_is_rejected():
    item = {"basDt":"20220919", "prdCtg":"spread", "srtnCd":"x", "isinCd":"x", "itmsNm":"x",
            "mkp":"-100", "hipr":"-90", "lopr":"-110", "clpr":"-95", "sptPrc":"0",
            "stmPrc":"0", "trqu":"1", "trPrc":"0", "opnint":"0"}
    assert normalize_futures([item]).loc[0, "open"] == -100
    item["trqu"] = "-1"
    with pytest.raises(DataV1ValidationError, match="negative"):
        normalize_futures([item])


def test_lending_operations_keep_distinct_grains():
    detail = normalize_stock_lending([{"basDt":"20231005","mrktClsfNm":"코스피","stckItmsNm":"동화약품",
        "stckItmsCd":"000020","cclStckCnt":"0","rdptStckCnt":"0","balnStckCnt":"207831","balnStckAmt":"2022195630"}])
    market = normalize_stock_lending_market([{"basDt":"20231005","cclStckCnt":"40533031",
        "rdptStckCnt":"38350310","balnStckCnt":"2024001925","balnStckAmt":"79853663382506"}])
    participant = normalize_stock_lending_participant([{"basDt":"20231005","invpnClsfNm":"내국인",
        "invpnClsfDtlNm":"자산운용","lndeCclStckAmt":"1731082","lndeCclStckAmtRto":"4.27",
        "borCclStckAmt":"1272826","borCclStckAmtRto":"3.14"}])
    assert detail.loc[0,"symbol"] == "000020" and market.loc[0,"balance_shares"] == 2024001925
    assert participant.loc[0,"borrower_ratio"] == 3.14


def test_dividend_and_rights_preserve_source_event_type():
    dividend = normalize_dividend([{"basDt":"20230104","isinCd":"KR7023450000","crno":"1101110057012","stckIssuCmpyNm":"동남합성",
        "scrsItmsKcdNm":"보통주","stckDvdnRcdNm":"무배당","dvdnBasDt":"19941231",
        "cashDvdnPayDt":"NULL","stckHndvDt":"NULL","stckGenrDvdnAmt":"0","stckGenrCashDvdnRt":"0",
        "stckGenrDvdnRt":"0","stckGrdnDvdnAmt":"0","cashGrdnDvdnRt":"0","stckGrdnDvdnRt":"0","stckParPrc":"500"}])
    rights = normalize_rights([{"basDt":"20191231","issuCmpyKsdCustNo":"1115","scrsIssuMnbdCd":"01115","crno":"1101110215578",
        "stckIssuRcd":"01","stckIssuRcdNm":"capital increase","rgtExertRcd":"02",
        "stckIssuCmpyNm":"CJ씨푸드","rgtExertRcdNm":"기준일","rgtExertSttgDt":"20191231",
        "rgtExertEdDt":"20191231","nmlsLckSttgDt":"20200101","nmlsLckEdDt":"20200115",
        "trsnmDptyDcd":"01","trsnmDptyDcdNm":"agent","stckParPrc":"500.000","stckStacMd":"1231"}],
        landing_response_body_sha256="a" * 64, source_page_no=1)
    assert dividend.loc[0,"event_type"] == "무배당" and pd.isna(dividend.loc[0,"cash_payment_date"])
    assert rights.loc[0,"rights_exercise_reason_name"] == "기준일"
    assert rights.loc[0,"ksd_issuer_customer_no"] == "1115"
    assert rights.loc[0,"securities_issuer_entity_code"] == "01115"


def test_validation_rejects_duplicates_and_bad_ohlc():
    contract = next(c for c in DATA_V1_CONTRACTS if c.name == "kr_derivatives_futures_daily")
    row = {name: 0 for name in contract.column_names}
    row.update(date="2022-09-19", product_category="x", symbol="x", isin="x", name="x",
               open=10.0, high=9.0, low=8.0, close=9.0)
    frame = pd.DataFrame([row], columns=contract.column_names)
    with pytest.raises(DataV1ValidationError, match="OHLC"):
        validate_data_v1(frame, contract)


def test_rights_observation_identity_is_provenance_backed():
    assert KR_EQUITY_RIGHTS_SCHEDULE.version == 2
    assert KR_EQUITY_RIGHTS_SCHEDULE.primary_key == (
        "landing_response_body_sha256", "source_item_ordinal"
    )
    assert "source_snapshot_date" not in KR_EQUITY_RIGHTS_SCHEDULE.primary_key


def test_rights_optional_fields_and_provenance_validation():
    item = {
        "basDt": "20191231", "issuCmpyKsdCustNo": "1115",
        "stckIssuCmpyNm": "issuer", "stckParPrc": "NULL", "stckStacMd": "NULL",
    }
    frame = normalize_rights(
        [item], landing_response_body_sha256="b" * 64, source_page_no=2,
    )
    assert frame.loc[0, "source_item_ordinal"] == 0
    assert frame.loc[0, "source_page_no"] == 2
    assert pd.isna(frame.loc[0, "issuance_reason_code"])
    assert pd.isna(frame.loc[0, "par_value"])
    assert len(frame.loc[0, "source_record_sha256"]) == 64
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        normalize_rights([item], landing_response_body_sha256="bad", source_page_no=1)


def test_rights_observation_parquet_round_trip_uses_snapshot_year(tmp_path):
    item = {
        "basDt": "20191231", "issuCmpyKsdCustNo": "1115",
        "stckIssuCmpyNm": "issuer", "stckParPrc": "500.000", "stckStacMd": "1231",
    }
    frame = normalize_rights(
        [item], landing_response_body_sha256="d" * 64, source_page_no=1,
    )
    validator = lambda value: validate_data_v1(value, KR_EQUITY_RIGHTS_SCHEDULE)
    root = tmp_path / KR_EQUITY_RIGHTS_SCHEDULE.name
    write_dataset_atomic(frame, root, KR_EQUITY_RIGHTS_SCHEDULE, validator)
    restored = read_dataset(root, KR_EQUITY_RIGHTS_SCHEDULE, validator)
    assert (root / "year=2019" / "data.parquet").is_file()
    assert restored.equals(frame)


@pytest.mark.parametrize(("field", "value", "error"), [
    ("stckStacMd", "1331", "MMDD"),
    ("stckParPrc", "1.2345", "decimal"),
    ("rgtExertSttgDt", "20190230", "YYYYMMDD"),
])
def test_rights_rejects_invalid_documented_values(field, value, error):
    item = {
        "basDt": "20191231", "issuCmpyKsdCustNo": "1115",
        "stckIssuCmpyNm": "issuer", field: value,
    }
    with pytest.raises(ValueError, match=error):
        normalize_rights([item], landing_response_body_sha256="c" * 64, source_page_no=1)
