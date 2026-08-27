from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from stock_data.gui import research_workspace_preferences as subject


def _custom() -> subject.ResearchWorkspacePreferences:
    compact = subject.WorkspacePreset(
        name="분석 중심",
        panels=tuple(
            replace(
                panel,
                visible=panel.panel_id not in {"WATCHLIST", "SOURCE_STATUS"},
                logical_size=480 if panel.panel_id == "CHART" else 180,
                focus_order=len(subject.PANEL_IDS) - 1 - panel.focus_order,
            )
            for panel in reversed(subject.DEFAULT_PREFERENCES.presets[0].panels)
        ),
    )
    return subject.ResearchWorkspacePreferences(
        active_preset=compact.name,
        presets=(subject.DEFAULT_PREFERENCES.presets[0], compact),
    )


def test_missing_settings_return_exact_safe_default(tmp_path: Path) -> None:
    result = subject.LocalResearchWorkspacePreferencesStore(
        tmp_path / "research_workspace.json"
    ).load()

    assert result.preferences == subject.DEFAULT_PREFERENCES
    assert result.reason == "DEFAULT_MISSING"
    assert tuple(
        panel.panel_id for panel in result.preferences.presets[0].panels
    ) == subject.PANEL_IDS


@pytest.mark.parametrize("unsupported_version", [0, 2])
def test_inaugural_schema_has_no_predecessor_and_unsupported_versions_fail_safe(
    tmp_path: Path, unsupported_version: int,
) -> None:
    path = tmp_path / "research_workspace.json"
    payload = subject.preferences_payload(subject.DEFAULT_PREFERENCES)
    payload["schema_version"] = unsupported_version
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = subject.LocalResearchWorkspacePreferencesStore(path).load()

    assert result == subject.ResearchWorkspacePreferencesLoadResult(
        subject.DEFAULT_PREFERENCES, "DEFAULT_CORRUPT"
    )


def test_strict_v1_roundtrip_contains_presentation_state_only(tmp_path: Path) -> None:
    path = tmp_path / "research_workspace.json"
    store = subject.LocalResearchWorkspacePreferencesStore(path)
    store.save(_custom())

    result = store.load()
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = path.read_text(encoding="utf-8").casefold()

    assert result == subject.ResearchWorkspacePreferencesLoadResult(
        _custom(), "LOADED"
    )
    assert set(payload) == {"schema_version", "active_preset", "presets"}
    assert payload["schema_version"] == 1
    assert not any(token in body for token in (
        "account", "balance", "credential", "identity", "market_data",
        "provider", "symbol", "ticker", "token", "price", "ohlcv_value",
    ))
    assert store.backup_path.is_file()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"account_id": "private"}),
        lambda payload: payload["presets"][0].update({"provider": "raw"}),
        lambda payload: payload["presets"][0]["panels"][0].update({"close": 123.4}),
        lambda payload: payload["presets"][0]["panels"].append(
            dict(payload["presets"][0]["panels"][0])
        ),
        lambda payload: payload["presets"][0]["panels"][0].update(
            {"panel_id": "UNKNOWN"}
        ),
        lambda payload: payload["presets"][0]["panels"][0].update(
            {"visible": 1}
        ),
        lambda payload: payload["presets"][0]["panels"][0].update(
            {"logical_size": 119}
        ),
        lambda payload: payload["presets"][0]["panels"][0].update(
            {"logical_size": 4097}
        ),
        lambda payload: payload["presets"][0]["panels"][1].update(
            {"focus_order": payload["presets"][0]["panels"][0]["focus_order"]}
        ),
        lambda payload: payload["presets"].append(dict(payload["presets"][0])),
        lambda payload: payload.update({"active_preset": "없는 프리셋"}),
        lambda payload: payload["presets"][0].update({"name": "account:1234"}),
    ],
)
def test_unknown_private_value_shaped_duplicate_and_bounds_fail_closed(
    tmp_path: Path, mutation,
) -> None:
    path = tmp_path / "research_workspace.json"
    payload = subject.preferences_payload(subject.DEFAULT_PREFERENCES)
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = subject.LocalResearchWorkspacePreferencesStore(path).load()

    assert result == subject.ResearchWorkspacePreferencesLoadResult(
        subject.DEFAULT_PREFERENCES, "DEFAULT_CORRUPT"
    )


def test_corrupt_primary_recovers_last_valid_without_rewriting_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "research_workspace.json"
    store = subject.LocalResearchWorkspacePreferencesStore(path)
    store.save(_custom())
    path.write_text("{broken", encoding="utf-8")

    result = store.load()

    assert result == subject.ResearchWorkspacePreferencesLoadResult(
        _custom(), "RECOVERED_LAST_VALID"
    )
    assert path.read_text(encoding="utf-8") == "{broken"


def test_missing_primary_recovers_last_valid(tmp_path: Path) -> None:
    path = tmp_path / "research_workspace.json"
    store = subject.LocalResearchWorkspacePreferencesStore(path)
    store.save(_custom())
    path.unlink()

    result = store.load()

    assert result.preferences == _custom()
    assert result.reason == "RECOVERED_LAST_VALID"


def test_failed_primary_replace_preserves_primary_backup_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "research_workspace.json"
    store = subject.LocalResearchWorkspacePreferencesStore(path)
    store.save(subject.DEFAULT_PREFERENCES)
    primary_before = path.read_bytes()
    backup_before = store.backup_path.read_bytes()
    original_replace = subject.os.replace

    def fail_primary(source, target):
        if Path(target) == path:
            raise OSError("synthetic primary failure")
        return original_replace(source, target)

    monkeypatch.setattr(subject.os, "replace", fail_primary)
    with pytest.raises(
        subject.ResearchWorkspacePreferencesError, match="WRITE_FAILED"
    ):
        store.save(_custom())

    assert path.read_bytes() == primary_before
    assert store.backup_path.read_bytes() == backup_before
    assert not list(tmp_path.glob(".*.tmp"))


def test_failed_backup_refresh_keeps_new_valid_primary_and_old_valid_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "research_workspace.json"
    store = subject.LocalResearchWorkspacePreferencesStore(path)
    store.save(subject.DEFAULT_PREFERENCES)
    original_replace = subject.os.replace
    backup_calls = 0

    def fail_backup_refresh(source, target):
        nonlocal backup_calls
        if Path(target) == store.backup_path:
            backup_calls += 1
            if backup_calls == 2:
                raise OSError("synthetic backup refresh failure")
        return original_replace(source, target)

    monkeypatch.setattr(subject.os, "replace", fail_backup_refresh)
    store.save(_custom())

    assert store.load().preferences == _custom()
    path.write_text("{broken", encoding="utf-8")
    assert store.load().preferences == subject.DEFAULT_PREFERENCES
    assert not list(tmp_path.glob(".*.tmp"))


def test_reset_is_exact_and_write_failure_preserves_previous_preferences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "research_workspace.json"
    store = subject.LocalResearchWorkspacePreferencesStore(path)
    store.save(_custom())
    assert store.reset() == subject.DEFAULT_PREFERENCES
    assert store.load().preferences == subject.DEFAULT_PREFERENCES

    before = path.read_bytes()
    monkeypatch.setattr(
        subject.os,
        "replace",
        lambda _source, _target: (_ for _ in ()).throw(OSError("failure")),
    )
    with pytest.raises(subject.ResearchWorkspacePreferencesError):
        store.save(_custom())
    assert path.read_bytes() == before
