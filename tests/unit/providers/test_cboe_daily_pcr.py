from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from stock_data.providers.cboe_daily_pcr import (
    CboeDailyPcrError,
    download_daily_pcr,
    parse_daily_pcr,
)


CSV = b"""date,scope,call_volume,put_volume,call_oi,put_oi
2026-09-04,SUM OF ALL PRODUCTS,100,125,200,100
2026-09-04,INDEX PUT/CALL RATIO,0,4,0,7
2026-09-04,EXCHANGE TRADED PRODUCTS,50,25,80,40
2026-09-04,EQUITY,40,60,90,135
2026-09-04,CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO,10,30,,
"""


def test_parser_preserves_scopes_and_computes_put_divided_by_call() -> None:
    rows = parse_daily_pcr(
        CSV, observation_date=date(2026, 9, 4),
        retrieved_at=datetime(2026, 9, 4, 21, 30, tzinfo=timezone.utc),
    )

    assert [row["scope"] for row in rows] == ["TOTAL", "INDEX", "ETP", "EQUITY", "VIX"]
    assert rows[0]["volume_pcr"] == pytest.approx(1.25)
    assert rows[0]["oi_pcr"] == pytest.approx(0.5)
    assert rows[1]["volume_pcr"] is None
    assert rows[1]["oi_pcr"] is None
    assert rows[-1]["call_oi"] is None and rows[-1]["oi_pcr"] is None


def test_parser_fails_closed_for_partial_scope_or_mismatched_date() -> None:
    with pytest.raises(CboeDailyPcrError, match="required scopes"):
        parse_daily_pcr(
            b"date,scope,call_volume,put_volume\n2026-09-04,TOTAL,1,2\n",
            observation_date=date(2026, 9, 4), retrieved_at=datetime.now(timezone.utc),
        )
    with pytest.raises(CboeDailyPcrError, match="date differs"):
        parse_daily_pcr(
            CSV, observation_date=date(2026, 9, 3), retrieved_at=datetime.now(timezone.utc),
        )


def test_download_makes_one_call_and_does_not_parse_before_landing() -> None:
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, content=b"unparsed", headers={"content-type": "text/csv"})

    result = download_daily_pcr(
        date(2026, 9, 4), transport=transport,
        source_url_template="https://example.test/{date}.csv",
        retrieved_at=datetime(2026, 9, 4, 22, tzinfo=timezone.utc),
    )

    assert len(calls) == 1
    assert result.body == b"unparsed"
    assert result.source_url == "https://example.test/2026-09-04.csv"
