from datetime import date

import pytest

from stock_data.providers.pykrx.kr_equity_daily import fetch_market_day
from stock_data.providers.pykrx.kr_index_daily import fetch_indices
from stock_data.providers.pykrx.kr_investor_flow import fetch_investor_flow
from stock_data.providers.pykrx.safety import (
    PykrxAutomationDisabledError, require_manual_live_access,
)


@pytest.mark.parametrize(
    "call",
    [
        lambda: fetch_indices(date(2026, 8, 3), date(2026, 8, 4)),
        lambda: fetch_market_day(date(2026, 8, 3), "KOSPI"),
        lambda: fetch_investor_flow(date(2026, 8, 3), date(2026, 8, 4), "KOSPI"),
    ],
)
def test_live_pykrx_entry_points_are_blocked_by_default(call) -> None:
    with pytest.raises(PykrxAutomationDisabledError, match="automation is disabled"):
        call()


def test_manual_pykrx_range_is_bounded() -> None:
    require_manual_live_access(manual=True, requested_days=10)
    with pytest.raises(PykrxAutomationDisabledError, match="at most 10"):
        require_manual_live_access(manual=True, requested_days=11)
