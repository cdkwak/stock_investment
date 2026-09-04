from __future__ import annotations

from pathlib import Path

import pytest

from stock_data.orchestration.release_readiness import (
    EXPECTED_WEB_ROUTES,
    assess_web_readiness_probe,
    run_web_readiness_probe,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_provider_free_web_dashboard_routes_are_release_ready() -> None:
    result = run_web_readiness_probe(PROJECT_ROOT)

    assert result["pages"] == EXPECTED_WEB_ROUTES
    assert result["web_probe_status_codes"] == {
        route: 200 for route in EXPECTED_WEB_ROUTES
    }
    assert all(
        result["web_probe_payload_sizes"][route] > 0
        for route in EXPECTED_WEB_ROUTES
    )
    assert all(
        result["web_probe_elapsed_ms"][route] >= 0
        for route in EXPECTED_WEB_ROUTES
    )
    assert assess_web_readiness_probe(result).status == "PASS"


@pytest.mark.parametrize("route", EXPECTED_WEB_ROUTES)
def test_release_gate_fails_for_each_non_200_route(route: str) -> None:
    result = {
        "pages": EXPECTED_WEB_ROUTES,
        "page_states": {path: True for path in EXPECTED_WEB_ROUTES},
        "web_probe_status_codes": {path: 200 for path in EXPECTED_WEB_ROUTES},
        "web_probe_payload_sizes": {path: 1 for path in EXPECTED_WEB_ROUTES},
        "web_probe_elapsed_ms": {path: 0.1 for path in EXPECTED_WEB_ROUTES},
        "web_probe_total_elapsed_ms": 0.5,
        "web_probe_all_200": True,
    }
    result["web_probe_status_codes"][route] = 500
    result["page_states"][route] = False
    result["web_probe_all_200"] = False

    assert assess_web_readiness_probe(result).status == "FAIL"
