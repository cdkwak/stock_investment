import json

import pytest

from stock_data.providers.pykrx.short_selling import (
    ShortSellingSourceError,
    investor_scope,
    parse_balance_body,
    parse_investor_body,
    parse_trading_body,
)


def body(rows):
    return json.dumps({"OutBlock_1": rows}, ensure_ascii=False).encode()


def trading_row():
    return {
        "ISU_CD": "00104K", "ISU_ABBRV": "source name", "SECUGRP_NM": "stock",
        "CVSRTSELL_TRDVOL": "3", "UPTICKRULE_APPL_TRDVOL": "2",
        "UPTICKRULE_EXCPT_TRDVOL": "1", "ACC_TRDVOL": "30", "TRDVOL_WT": "10.00",
        "CVSRTSELL_TRDVAL": "300", "UPTICKRULE_APPL_TRDVAL": "200",
        "UPTICKRULE_EXCPT_TRDVAL": "100", "ACC_TRDVAL": "3000", "TRDVAL_WT": "10.00",
    }


def test_trading_preserves_exact_source_identity_totals_ratios_and_alphanumeric_code():
    parsed = parse_trading_body(body([trading_row()]), date="2026-08-10", market="KOSPI")
    row = parsed.dataframe.iloc[0]
    assert parsed.classification == "SUCCESS"
    assert (row.symbol, row.source_name, row.source_security_type) == ("00104K", "source name", "stock")
    assert (row.total_trading_volume, row.total_trading_value) == (30, 3000)
    assert (row.short_volume_ratio, row.short_trading_value_ratio) == (10.0, 10.0)


def test_historical_source_zero_uptick_components_are_preserved_not_inferred():
    row = trading_row()
    row["UPTICKRULE_APPL_TRDVOL"] = row["UPTICKRULE_EXCPT_TRDVOL"] = "0"
    row["UPTICKRULE_APPL_TRDVAL"] = row["UPTICKRULE_EXCPT_TRDVAL"] = "0"
    parsed = parse_trading_body(body([row]), date="2008-01-02", market="KOSPI")
    assert parsed.dataframe.iloc[0].short_volume == 3
    assert parsed.dataframe.iloc[0].uptick_rule_applied_short_volume == 0


def test_balance_preserves_source_name_totals_and_ratio():
    row = {"ISU_CD": "005930", "ISU_ABBRV": "Samsung", "BAL_QTY": "1,000",
           "LIST_SHRS": "10,000", "BAL_AMT": "20,000", "MKTCAP": "200,000",
           "BAL_RTO": "10.0"}
    parsed = parse_balance_body(body([row]), date="2016-06-30", market="KOSPI")
    assert parsed.dataframe.iloc[0].to_dict()["shares_outstanding"] == 10000


def test_empty_placeholder_and_failure_are_distinct():
    empty = parse_investor_body(body([]), market="KOSPI", metric="volume")
    placeholder = {"TRD_DD": "", **{f"STR_CONST_VAL{i}": "0" for i in range(1, 6)}}
    blank = parse_investor_body(body([placeholder]), market="KOSPI", metric="volume")
    assert empty.classification == "VALID_EMPTY"
    assert blank.classification == "VALID_EMPTY_PLACEHOLDER"
    with pytest.raises(ShortSellingSourceError):
        parse_investor_body(b"<html>restricted</html>", market="KOSPI", metric="volume")


def test_investor_range_is_at_most_730_calendar_days():
    investor_scope("2024-01-01", "2025-12-31", "KOSPI", "volume")
    with pytest.raises(ValueError, match="730"):
        investor_scope("2024-01-01", "2026-01-01", "KOSPI", "volume")
