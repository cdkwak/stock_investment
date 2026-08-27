from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from stock_data.contracts.investor_bridge import (
    KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
)
from stock_data.contracts.legacy_market_investor import (
    KR_MARKET_INVESTOR_NET_PURCHASE_DAILY,
)
from stock_data.contracts.tossinvest_historical import (
    KR_MARKET_INVESTOR_TRADING_DAILY,
)
from stock_data.orchestration.toss_market_investor_daily import (
    refresh_toss_market_investor_daily,
)
from stock_data.providers.tossinvest import TossInvestAPIResponse, TossInvestRateLimit
from stock_data.providers.tossinvest.historical import normalize_market_investor
from stock_data.published.investor_bridge import compose_investor_bridge
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.investor_bridge import validate_investor_bridge
from stock_data.validation.legacy_market_investor import (
    validate_legacy_market_investor_net_purchase,
)
from stock_data.validation.tossinvest_historical import validate_toss_historical


def _record(day: str) -> dict:
    zero = {"buyAmount": "0", "sellAmount": "0"}
    breakdown = {
        key: zero
        for key in (
            "financialInvestment",
            "insurance",
            "trust",
            "privateEquityFund",
            "bank",
            "otherFinancialInstitution",
            "pensionFund",
        )
    }
    return {
        "date": day,
        "updatedAt": day + "T18:00:00+09:00",
        "individual": zero,
        "foreigner": zero,
        "institution": {**zero, "breakdown": breakdown},
        "otherCorporation": zero,
    }


class _Client:
    def __init__(self) -> None:
        self.token_request_count = 0
        self.market_request_count = 0

    def get_market_data(self, path, *, params):
        assert params == {"interval": "1d", "count": 100}
        self.token_request_count = 1
        self.market_request_count += 1
        return TossInvestAPIResponse(
            200,
            {"result": {"records": [_record("2026-08-18")], "nextUntil": None}},
            TossInvestRateLimit("MARKET_INDICATOR", 10, 8, 1),
        )


def _seed(project) -> None:
    legacy = pd.DataFrame(
        [[
            "2014-06-30", "KOSPI", -10, 0, 3, 7, 0,
            "legacy_stock_investment_pykrx_1.2.8", "MDCSTAT02202",
            "legacy_pre_a001_only",
        ]],
        columns=KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.column_names,
    )
    write_dataset_atomic(
        legacy,
        project / "data/normalized/kr_market_investor_net_purchase_daily",
        KR_MARKET_INVESTOR_NET_PURCHASE_DAILY,
        validate_legacy_market_investor_net_purchase,
    )
    observed = datetime(2026, 8, 12, tzinfo=timezone.utc)
    toss = pd.concat(
        [
            normalize_market_investor([_record("2026-08-11")], market=market, collected_at=observed)
            for market in ("KOSPI", "KOSDAQ")
        ],
        ignore_index=True,
    ).sort_values(list(KR_MARKET_INVESTOR_TRADING_DAILY.sort_key)).reset_index(drop=True)
    write_dataset_atomic(
        toss,
        project / "data/normalized/kr_market_investor_trading_daily",
        KR_MARKET_INVESTOR_TRADING_DAILY,
        lambda frame: validate_toss_historical(frame, KR_MARKET_INVESTOR_TRADING_DAILY),
    )
    bridge = compose_investor_bridge(legacy, toss)
    write_dataset_atomic(
        bridge,
        project / "data/published/kr_market_investor_net_purchase_bridge_daily",
        KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
        validate_investor_bridge,
    )


def test_daily_refresh_promotes_two_markets_and_replays_with_api_zero(tmp_path):
    _seed(tmp_path)
    source_marker = tmp_path / "data/normalized/kr_market_investor_trading_daily/.acl-root-marker"
    bridge_marker = tmp_path / "data/published/kr_market_investor_net_purchase_bridge_daily/.acl-root-marker"
    source_marker.write_text("preserve-root", encoding="utf-8")
    bridge_marker.write_text("preserve-root", encoding="utf-8")
    client = _Client()
    result = refresh_toss_market_investor_daily(
        tmp_path, intended_date="2026-08-18", client=client
    )
    assert result["status"] == "complete"
    assert result["market_calls"] == 2
    assert result["promoted_rows"] == 2
    assert len(list((tmp_path / "data/landing/tossinvest").rglob("daily_*.json"))) == 2
    assert source_marker.read_text(encoding="utf-8") == "preserve-root"
    assert bridge_marker.read_text(encoding="utf-8") == "preserve-root"

    restored = read_dataset(
        tmp_path / "data/published/kr_market_investor_net_purchase_bridge_daily",
        KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
        validate_investor_bridge,
    )
    latest = restored.loc[restored["date"].astype(str).eq("2026-08-18")]
    assert set(latest["market"].astype(str)) == {"KOSPI", "KOSDAQ"}

    replay = refresh_toss_market_investor_daily(
        tmp_path, intended_date="2026-08-18", client=None
    )
    assert replay["status"] == "already_complete"
    assert replay["market_calls"] == 0
