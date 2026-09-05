"""Research-only data acquisition and normalization helpers."""

from stock_data.research.target_prices import (
    KOREAN_UNAVAILABLE_MESSAGE,
    TargetPriceRequest,
    WatchlistSecurity,
    append_target_price_vintages_atomic,
    build_request_plan,
    load_watchlist,
    parse_yahoo_financial_data,
    validate_target_price_consensus,
)
from stock_data.research.scoring import (
    result_card,
    score_buy_events,
    score_sell_events,
    validate_result_card,
)
from stock_data.research.signals import BuySignalSpec, compute_signal_features


__all__ = [
    "KOREAN_UNAVAILABLE_MESSAGE",
    "BuySignalSpec",
    "TargetPriceRequest",
    "WatchlistSecurity",
    "append_target_price_vintages_atomic",
    "build_request_plan",
    "compute_signal_features",
    "load_watchlist",
    "parse_yahoo_financial_data",
    "result_card",
    "score_buy_events",
    "score_sell_events",
    "validate_target_price_consensus",
    "validate_result_card",
]
