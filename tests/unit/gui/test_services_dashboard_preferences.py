"""Retained non-Qt coverage moved from test_dashboard_preferences.py."""
from __future__ import annotations
from dataclasses import replace
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from stock_data.gui import dashboard_preferences as subject
from stock_data.gui.services import DashboardDisplayState, DashboardMetricView, DashboardSparklineView

def _custom() -> subject.DashboardPreferences:
    return replace(subject.DEFAULT_PREFERENCES, card_order=('BITCOIN', 'USD_KRW_60M', 'KOSPI', 'KOSDAQ', 'NQ_FUTURES', 'NASDAQ', 'SP500', 'SOXX', 'GOLD', 'WTI'), hidden_cards=frozenset({'GOLD', 'WTI'}), pinned_cards=frozenset({'BITCOIN'}), section_order=('NQ_CHART', 'KOSPI_CHART', 'MARKET_FLOW', 'MARKET_TEMPERATURE', 'ACCOUNT_SUMMARY', 'FX_RATES', 'DERIVATIVES'), hidden_sections=frozenset({'ACCOUNT_SUMMARY'}), density='COMPACT', default_market_asset='S&P 500', default_market_period='1Y', default_nq_interval='주봉', window_geometry=subject.WindowGeometry(-9000, 9000, 2100, 1300, True))

def test_missing_settings_return_exact_accepted_default(tmp_path: Path) -> None:
    result = subject.LocalDashboardPreferencesStore(tmp_path / 'layout.json').load()
    assert result.preferences == subject.DEFAULT_PREFERENCES
    assert result.reason == 'DEFAULT_MISSING'
    assert result.preferences.window_geometry == subject.WindowGeometry(40, 40, 1600, 900, False)

def test_dashboard_market_period_presets_are_ordered_and_all_round_trip(tmp_path: Path) -> None:
    assert subject.MARKET_PERIODS == ('60D', '120D', '1Y', '3Y', '5Y', '10Y', 'MAX')
    store = subject.LocalDashboardPreferencesStore(tmp_path / 'layout.json')
    for period in subject.MARKET_PERIODS:
        expected = replace(subject.DEFAULT_PREFERENCES, default_market_period=period)
        store.save(expected)
        assert store.load().preferences == expected

def test_versioned_atomic_roundtrip_keeps_watchlists_and_private_data_out(tmp_path: Path) -> None:
    path = tmp_path / 'dashboard_preferences.json'
    watchlist = tmp_path / 'watchlists.json'
    watchlist.write_text('{"separate":true}', encoding='utf-8')
    store = subject.LocalDashboardPreferencesStore(path)
    store.save(_custom())
    loaded = store.load()
    payload = json.loads(path.read_text(encoding='utf-8'))
    body = path.read_text(encoding='utf-8').lower()
    assert loaded.preferences == _custom() and loaded.reason == 'LOADED'
    assert payload['schema_version'] == subject.SCHEMA_VERSION
    assert not any((token in body for token in ('credential', 'token', 'account_balance', 'provider_payload', 'market_data')))
    assert watchlist.read_text(encoding='utf-8') == '{"separate":true}'
    assert store.backup_path.is_file()

def test_v1_migration_adds_sections_pinning_nq_and_rewrites_v2(tmp_path: Path) -> None:
    path = tmp_path / 'layout.json'
    path.write_text(json.dumps({'schema_version': 1, 'visible_cards': [item for item in subject.CARD_IDS if item != 'WTI'], 'card_order': list(reversed(subject.CARD_IDS)), 'compact': True, 'default_market_asset': 'Nasdaq', 'default_market_period': '60D', 'window_geometry': {'x': 5, 'y': 6, 'width': 1500, 'height': 850, 'maximized': False}}), encoding='utf-8')
    result = subject.LocalDashboardPreferencesStore(path).load()
    migrated = json.loads(path.read_text(encoding='utf-8'))
    assert result.reason == 'MIGRATED_V1'
    assert result.preferences.hidden_cards == frozenset({'WTI'})
    assert result.preferences.pinned_cards == frozenset()
    assert result.preferences.section_order == subject.SECTION_IDS
    assert result.preferences.default_nq_interval == '일봉'
    assert migrated['schema_version'] == 8

def test_corrupt_primary_recovers_latest_valid_backup_without_startup_failure(tmp_path: Path) -> None:
    path = tmp_path / 'layout.json'
    store = subject.LocalDashboardPreferencesStore(path)
    store.save(_custom())
    path.write_text('{broken', encoding='utf-8')
    result = store.load()
    assert result.reason == 'RECOVERED_LAST_VALID'
    assert result.preferences == _custom()
    assert path.read_text(encoding='utf-8') == '{broken'

def test_failed_atomic_replace_preserves_primary_and_cleans_temporary_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / 'layout.json'
    store = subject.LocalDashboardPreferencesStore(path)
    store.save(subject.DEFAULT_PREFERENCES)
    before = path.read_bytes()
    original = subject.os.replace

    def fail_primary(source, target):
        if Path(target) == path:
            raise OSError('synthetic replace failure')
        return original(source, target)
    monkeypatch.setattr(subject.os, 'replace', fail_primary)
    with pytest.raises(subject.DashboardPreferencesError, match='WRITE_FAILED'):
        store.save(_custom())
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('*.tmp')) and (not list(tmp_path.glob('.*.tmp')))

def test_parent_directory_creation_failure_is_translated_without_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / 'unavailable'
    path = parent / 'layout.json'
    sibling = tmp_path / 'keep.txt'
    sibling.write_text('unchanged', encoding='utf-8')
    original = Path.mkdir

    def fail_exact_parent(self, *args, **kwargs):
        if self == parent:
            raise PermissionError('synthetic parent denial')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', fail_exact_parent)
    with pytest.raises(subject.DashboardPreferencesError, match='WRITE_FAILED'):
        subject.LocalDashboardPreferencesStore(path).save(_custom())
    assert not parent.exists()
    assert sibling.read_text(encoding='utf-8') == 'unchanged'
    assert not list(tmp_path.rglob('*.tmp'))

def test_safe_geometry_clamps_offscreen_and_reset_is_exact(tmp_path: Path) -> None:
    geometry = subject.safe_window_geometry(_custom().window_geometry, (0, 0, 1920, 1040))
    assert geometry == subject.WindowGeometry(0, 0, 1920, 1040, True)
    store = subject.LocalDashboardPreferencesStore(tmp_path / 'layout.json')
    store.save(_custom())
    reset = store.reset()
    assert reset == subject.DEFAULT_PREFERENCES
    assert store.load().preferences == subject.DEFAULT_PREFERENCES

@pytest.mark.parametrize('mutation', [lambda payload: payload.update({'account_balance': 123}), lambda payload: payload['card_order'].append('UNKNOWN'), lambda payload: payload.update({'pinned_cards': ['GOLD'], 'hidden_cards': ['GOLD']}), lambda payload: payload['window_geometry'].update({'width': 999999})])
def test_invalid_or_private_shaped_settings_fail_to_defaults(tmp_path: Path, mutation) -> None:
    path = tmp_path / 'layout.json'
    payload = subject.preferences_payload(subject.DEFAULT_PREFERENCES)
    mutation(payload)
    path.write_text(json.dumps(payload), encoding='utf-8')
    result = subject.LocalDashboardPreferencesStore(path).load()
    assert result.reason == 'DEFAULT_CORRUPT'
    assert result.preferences == subject.DEFAULT_PREFERENCES

