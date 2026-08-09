from datetime import date

from stock_data.providers.krx_open_api import KRX_DATASET_MAPPINGS


def test_krx_mappings_are_blocked_or_unconfirmed_without_live_transport() -> None:
    assert set(KRX_DATASET_MAPPINGS) >= {
        "kr_index_daily", "kr_equity_price_daily", "kr_equity_market_cap_daily",
        "kr_equity_master", "kr_investor_flow_daily",
    }
    assert all(item.status in {"blocked", "unconfirmed"} for item in KRX_DATASET_MAPPINGS.values())
    assert all(item.approval_required for item in KRX_DATASET_MAPPINGS.values())
    assert KRX_DATASET_MAPPINGS["kr_equity_price_daily"].available_from == date(2010, 1, 4)


def test_only_verified_derivative_api_ids_are_recorded() -> None:
    identified = {name: item.api_ids for name, item in KRX_DATASET_MAPPINGS.items() if item.api_ids}
    assert identified == {
        "kr_derivatives_futures_daily": ("fut_bydd_trd",),
        "kr_derivatives_options_daily": ("opt_bydd_trd",),
    }
