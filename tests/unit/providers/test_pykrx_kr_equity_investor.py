from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_data.providers.pykrx.kr_equity_investor import (
    PykrxEquityInvestorClient,
    normalize_investor_flow,
)
from stock_data.providers.pykrx.safety import PykrxRequestPolicy
from stock_data.validation.kr_equity_investor_flow import (
    validate_kr_equity_investor_flow,
)


def _raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "기관합계": [100, -200],
            "기타법인": [20, 30],
            "개인": [-70, 50],
            "외국인합계": [-50, 120],
            "전체": [0, 0],
        },
        index=pd.to_datetime(["2026-09-03", "2026-09-04"]),
    )


class FakeStock:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    @classmethod
    def get_market_trading_value_by_date(cls, *args, **kwargs) -> pd.DataFrame:
        cls.calls.append((args, kwargs))
        return _raw()


def test_pykrx_investor_adapter_selects_net_purchase_amount_view() -> None:
    FakeStock.calls.clear()
    client = PykrxEquityInvestorClient(
        stock_module=FakeStock,
        policy=PykrxRequestPolicy(min_interval_seconds=0, max_consecutive_requests=1),
    )

    observed = client.get_market_trading_value_by_date(
        date(2026, 9, 3), date(2026, 9, 4), "005930",
    )

    assert observed.equals(_raw())
    assert FakeStock.calls == [(
        ("20260903", "20260904", "005930"),
        {"on": "순매수", "detail": False},
    )]
    assert client.request_count == 1


def test_provider_normalization_maps_exact_korean_columns_to_int64_won() -> None:
    frame = normalize_investor_flow(
        _raw(),
        symbol="005930",
        start=date(2026, 9, 3),
        end=date(2026, 9, 4),
        captured_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
    )

    assert frame[[
        "date", "symbol", "foreign_net", "institution_net",
        "individual_net", "other_corp_net", "total_net", "source",
    ]].values.tolist() == [
        [date(2026, 9, 3), "005930", -50, 100, -70, 20, 0, "pykrx"],
        [date(2026, 9, 4), "005930", 120, -200, 50, 30, 0, "pykrx"],
    ]
    assert all(frame[column].dtype == "int64" for column in (
        "foreign_net", "institution_net", "individual_net",
        "other_corp_net", "total_net",
    ))
    assert validate_kr_equity_investor_flow(frame) == ()


def test_validation_flags_sum_mismatch_without_rejecting_and_rejects_bad_keys() -> None:
    frame = normalize_investor_flow(
        _raw(),
        symbol="005930",
        start=date(2026, 9, 3),
        end=date(2026, 9, 4),
        captured_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
    )
    frame.loc[0, "total_net"] = 100

    warnings = validate_kr_equity_investor_flow(frame)

    assert len(warnings) == 1
    assert warnings[0].startswith("INVESTOR_FLOW_SUM_MISMATCH:2026-09-03:005930:")
    with pytest.raises(ValueError, match="duplicate"):
        validate_kr_equity_investor_flow(pd.concat([frame, frame.iloc[[0]]], ignore_index=True))
    malformed = frame.copy()
    malformed.loc[0, "symbol"] = "5930"
    with pytest.raises(ValueError, match="six-character"):
        validate_kr_equity_investor_flow(malformed)
