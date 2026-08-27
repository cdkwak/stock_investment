from __future__ import annotations
import csv
from pathlib import Path
import pytest
from stock_data.orchestration.source_routing_registry import FallbackGate, SourceRoutingRegistry, SourceRoutingRegistryError

ROOT=Path(__file__).resolve().parents[3]
CSV_PATH=ROOT/'docs/data/ALL_DATA_SOURCE_COVERAGE_20260821.csv'

def test_registry_parses_all_rows_and_preserves_noncoverage_and_broker_snapshot_only():
    registry=SourceRoutingRegistry.from_csv(CSV_PATH)
    assert len(registry)==111
    kb=registry['kb_domestic_index_snapshot']
    assert 'IVSA0070' in kb.broker_route and not kb.automatic_fallback_allowed
    assert registry['ls_t8462_daily_raw'].backtest_promotion_allowed is False

def test_same_upstream_fdr_yahoo_is_not_automatic_fallback_and_vixcls_is_only_exception():
    registry=SourceRoutingRegistry.from_csv(CSV_PATH)
    assert registry['FDR_GLOBAL_YAHOO_PRICE'].automatic_fallback_allowed is False
    assert registry['fred_vix_daily'].fallback_gate is FallbackGate.VIXCLS_ONLY
    assert registry['fred_vix_daily'].automatic_fallback_allowed is True

def test_expected_ids_reconciliation_rejects_missing_row():
    expected=frozenset(row['record_id'] for row in csv.DictReader(CSV_PATH.open(encoding='utf-8-sig')))
    assert len(SourceRoutingRegistry.from_csv(CSV_PATH, expected_ids=expected)) == len(expected)
    with pytest.raises(SourceRoutingRegistryError,match='expected-ID'):
        SourceRoutingRegistry.from_csv(CSV_PATH, expected_ids=expected | {'missing_authoritative_id'})

def test_duplicate_and_unknown_decision_are_rejected(tmp_path: Path):
    rows=list(csv.DictReader(CSV_PATH.open(encoding='utf-8-sig')))
    rows[1]['record_id']=rows[0]['record_id']
    duplicate=tmp_path/'duplicate.csv'
    with duplicate.open('w',encoding='utf-8',newline='') as handle: csv.DictWriter(handle,fieldnames=rows[0].keys()).writeheader(); csv.DictWriter(handle,fieldnames=rows[0].keys()).writerows(rows)
    with pytest.raises(SourceRoutingRegistryError,match='duplicate'): SourceRoutingRegistry.from_csv(duplicate)
    rows[1]['record_id']='unique_record'; rows[0]['pit_finality']='UNKNOWN_ENUM'
    bad=tmp_path/'bad.csv'
    with bad.open('w',encoding='utf-8',newline='') as handle: csv.DictWriter(handle,fieldnames=rows[0].keys()).writeheader(); csv.DictWriter(handle,fieldnames=rows[0].keys()).writerows(rows)
    with pytest.raises(SourceRoutingRegistryError,match='unknown'): SourceRoutingRegistry.from_csv(bad)
