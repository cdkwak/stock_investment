from __future__ import annotations

import csv
from pathlib import Path

import pytest

from stock_data.orchestration.source_routing_registry import (
    FallbackGate,
    SourceRoutingRegistry,
    SourceRoutingRegistryError,
)


COLUMNS = (
    "record_id",
    "korean_description",
    "existing_retained_source",
    "ls_kb_toss_exact_route_or_noncoverage",
    "pykrx_fdr_yfinance_candidate_or_constraint",
    "preferred_current_display_priority",
    "achievable_interval",
    "latest_validation_outcome",
    "gui_use",
    "backtest_use",
    "pit_finality",
    "automatic_fallback_eligibility",
    "exact_blocker",
    "evidence",
    "followup_ur",
)


def _row(record_id: str, **overrides: str) -> dict[str, str]:
    row = {
        "record_id": record_id,
        "korean_description": f"fixture {record_id}",
        "existing_retained_source": "fixture_source",
        "ls_kb_toss_exact_route_or_noncoverage": "NO_BROKER_ROUTE",
        "pykrx_fdr_yfinance_candidate_or_constraint": "NO_CANDIDATE",
        "preferred_current_display_priority": "NO_DISPLAY_PRIORITY",
        "achievable_interval": "DAILY",
        "latest_validation_outcome": "OFFLINE_FIXTURE_ONLY",
        "gui_use": "NO",
        "backtest_use": "NO",
        "pit_finality": "NON_PREDICTIVE",
        "automatic_fallback_eligibility": "NO_AUTOMATIC_FALLBACK",
        "exact_blocker": "fixture_only",
        "evidence": "unit-test fixture",
        "followup_ur": "NONE",
    }
    row.update(overrides)
    return row


def _write_fixture(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def routing_csv(tmp_path: Path) -> Path:
    return _write_fixture(
        tmp_path / "routing.csv",
        [
            _row(
                "kb_domestic_index_snapshot",
                ls_kb_toss_exact_route_or_noncoverage="KB IVSA0070 snapshot only",
            ),
            _row("ls_t8462_daily_raw"),
            _row(
                "fred_vix_daily",
                automatic_fallback_eligibility="ONLY_VIXCLS_SCHEMA_FALLBACK_ACCEPTED",
            ),
        ],
    )


def test_registry_parses_rows_and_preserves_noncoverage_and_snapshot_only(
    routing_csv: Path,
) -> None:
    registry = SourceRoutingRegistry.from_csv(routing_csv)
    assert len(registry) == 3
    kb = registry["kb_domestic_index_snapshot"]
    assert "IVSA0070" in kb.broker_route
    assert not kb.automatic_fallback_allowed
    assert registry["ls_t8462_daily_raw"].backtest_promotion_allowed is False


def test_vixcls_is_the_only_automatic_fallback_exception(routing_csv: Path) -> None:
    registry = SourceRoutingRegistry.from_csv(routing_csv)
    assert registry["kb_domestic_index_snapshot"].automatic_fallback_allowed is False
    assert registry["fred_vix_daily"].fallback_gate is FallbackGate.VIXCLS_ONLY
    assert registry["fred_vix_daily"].automatic_fallback_allowed is True


def test_expected_ids_reconciliation_rejects_missing_row(routing_csv: Path) -> None:
    expected = frozenset(
        row["record_id"]
        for row in csv.DictReader(routing_csv.open(encoding="utf-8"))
    )
    assert len(SourceRoutingRegistry.from_csv(routing_csv, expected_ids=expected)) == 3
    with pytest.raises(SourceRoutingRegistryError, match="expected-ID"):
        SourceRoutingRegistry.from_csv(
            routing_csv,
            expected_ids=expected | {"missing_authoritative_id"},
        )


def test_duplicate_and_unknown_decision_are_rejected(tmp_path: Path) -> None:
    rows = [_row("duplicate"), _row("duplicate")]
    duplicate = _write_fixture(tmp_path / "duplicate.csv", rows)
    with pytest.raises(SourceRoutingRegistryError, match="duplicate"):
        SourceRoutingRegistry.from_csv(duplicate)

    rows[1]["record_id"] = "unique_record"
    rows[0]["pit_finality"] = "UNKNOWN_ENUM"
    bad = _write_fixture(tmp_path / "bad.csv", rows)
    with pytest.raises(SourceRoutingRegistryError, match="unknown"):
        SourceRoutingRegistry.from_csv(bad)
