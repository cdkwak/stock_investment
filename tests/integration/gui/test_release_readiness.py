from __future__ import annotations

from pathlib import Path

from stock_data.orchestration.release_readiness import (
    NATIVE_GUI_HEALTH_TIMEOUT_MS,
    assess_native_gui,
    run_native_gui_smoke,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_provider_free_cold_gui_renders_managed_health_within_bound() -> None:
    result = run_native_gui_smoke(PROJECT_ROOT)

    assert result["health_row_count"] > 0
    assert result["health_managed_total"] > 0
    assert result["health_managed_acceptable"] == result["health_managed_total"]
    assert result["health_render_timeout_ms"] == NATIVE_GUI_HEALTH_TIMEOUT_MS
    assert result["health_render_elapsed_ms"] <= NATIVE_GUI_HEALTH_TIMEOUT_MS
    assert result["font_glyphs_supported"] is True
    assert result["dashboard_card_overlaps"] == ()
    assert assess_native_gui(result).status in {"PASS", "DEGRADED"}
