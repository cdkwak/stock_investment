import pandas as pd

from stock_data.providers.financedata_marcap.equity import RAW_COLUMNS, normalize_annual


def _row(**changes):
    row = {
        "Code": "341A", "Name": "historical preferred", "Close": 100.0, "Dept": "",
        "ChangeCode": "1", "Changes": 0.0, "ChangesRatio": 0.0, "Volume": 0.0,
        "Amount": 0.0, "Open": 0.0, "High": 0.0, "Low": 0.0, "Marcap": 1000.0,
        "Stocks": 10, "Market": "KOSPI", "MarketId": "STK", "Rank": 1,
        "Date": pd.Timestamp("1995-05-02"),
    }
    row.update(changes)
    return row


def test_marcap_normalization_preserves_alphanumeric_and_source_zero():
    result = normalize_annual(pd.DataFrame([_row()], columns=RAW_COLUMNS), "marcap-1995.parquet")
    assert result.price.loc[0, "symbol"] == "341A"
    assert result.price.loc[0, ["open", "high", "low", "volume"]].tolist() == [0, 0, 0, 0]
    assert result.universe.loc[0, "name"] is None
    assert result.universe.loc[0, "short_name"] == "historical preferred"
    assert result.universe.loc[0, "isin"] is None
    assert result.price.loc[0, "source"] == "financedata_marcap"
    assert result.price.loc[0, "source_operation"] == "marcap-1995.parquet"


def test_marcap_numeric_symbol_is_zero_filled():
    result = normalize_annual(
        pd.DataFrame([_row(Code="5930")], columns=RAW_COLUMNS), "marcap-1995.parquet")
    assert result.price.loc[0, "symbol"] == "005930"


def test_marcap_invalid_rows_are_quarantined_without_fabrication():
    rows = [
        _row(Code="1", Close=None),
        _row(Code="2", Open=90.0, High=80.0, Low=70.0, Close=75.0),
        _row(Code="3", Open=70.0, High=90.0, Low=60.0, Close=80.0),
    ]
    result = normalize_annual(pd.DataFrame(rows, columns=RAW_COLUMNS), "marcap-1995.parquet")
    assert len(result.price) == len(result.market_cap) == len(result.universe) == 1
    assert len(result.quarantine) == 2
    assert result.quarantine["Close"].isna().sum() == 1
    assert result.quarantine["quarantine_reason"].str.contains("ohlc_invalid").all()
