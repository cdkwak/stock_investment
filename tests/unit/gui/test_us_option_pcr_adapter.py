from stock_data.gui.us_option_pcr_adapter import (
    USOptionPCRDisplayState,
    current_us_option_pcr_scope_views,
)


def test_current_us_option_pcr_views_are_numeric_free_by_policy() -> None:
    views = current_us_option_pcr_scope_views()
    assert len(views) == 10
    assert len({view.scope_id for view in views}) == 10
    assert all(view.value is None and not view.displays_value for view in views)


def test_cboe_categories_remain_separate_and_license_blocked() -> None:
    views = {view.scope_id: view for view in current_us_option_pcr_scope_views()}
    cboe_ids = (
        "CBOE_TOTAL", "CBOE_INDEX", "CBOE_ETP", "CBOE_EQUITY", "CBOE_VIX",
        "CBOE_SPX_SPXW",
    )
    assert all(
        views[scope_id].display_state is USOptionPCRDisplayState.LICENSE_BLOCKED
        for scope_id in cboe_ids
    )
    assert views["CBOE_TOTAL"].source_scope.startswith(
        "Cboe daily statistics page SUM OF ALL PRODUCTS"
    )
    assert views["CBOE_SPX_SPXW"].source_scope.startswith("Combined SPX and SPXW")
    assert all("서명 라이선스" in views[scope_id].reason for scope_id in cboe_ids)


def test_nasdaq_qqq_ndx_and_soxx_have_no_aggregate_fallback() -> None:
    views = {view.scope_id: view for view in current_us_option_pcr_scope_views()}
    for scope_id in ("NASDAQ", "QQQ", "NDX", "SOXX"):
        view = views[scope_id]
        assert view.display_state is USOptionPCRDisplayState.SOURCE_UNAVAILABLE
        assert view.usage_status == "NO_APPROVED_SOURCE"
        assert view.value is None
    assert "ETP aggregate is not a QQQ-specific ratio" in views["QQQ"].reason
    assert "SPX+SPXW are not an NDX-specific ratio" in views["NDX"].reason
    assert "ETP aggregate is not a SOXX-specific ratio" in views["SOXX"].reason
