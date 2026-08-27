"""Strict local presentation preferences for the Research workspace.

Only allowlisted panel layout state is serializable here.  Instrument,
market, account, provider, identity, and observed-value data belong to their
own runtime owners and are intentionally outside this schema.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Mapping
from uuid import uuid4


SCHEMA_VERSION = 1
PANEL_IDS = (
    "CHART",
    "OHLCV",
    "INSTRUMENT_FACTS",
    "WATCHLIST",
    "SOURCE_STATUS",
)
MIN_LOGICAL_SIZE = 120
MAX_LOGICAL_SIZE = 4096
MAX_PRESETS = 12

_ROOT_KEYS = {"schema_version", "active_preset", "presets"}
_PRESET_KEYS = {"name", "panels"}
_PANEL_KEYS = {"panel_id", "visible", "logical_size", "focus_order"}
_PRESET_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,40}$")
_PRIVATE_NAME_TOKENS = (
    "account", "balance", "credential", "email", "identity", "market",
    "password", "phone", "portfolio", "provider", "secret", "symbol",
    "ticker", "token", "계좌", "비밀번호", "시장", "이메일", "잔고",
    "전화", "종목", "토큰",
)


class ResearchWorkspacePreferencesError(RuntimeError):
    """Value-free local preference failure."""


@dataclass(frozen=True, slots=True)
class PanelPreference:
    panel_id: str
    visible: bool
    logical_size: int
    focus_order: int


@dataclass(frozen=True, slots=True)
class WorkspacePreset:
    name: str
    panels: tuple[PanelPreference, ...]


@dataclass(frozen=True, slots=True)
class ResearchWorkspacePreferences:
    active_preset: str
    presets: tuple[WorkspacePreset, ...]


@dataclass(frozen=True, slots=True)
class ResearchWorkspacePreferencesLoadResult:
    preferences: ResearchWorkspacePreferences
    reason: str


DEFAULT_PREFERENCES = ResearchWorkspacePreferences(
    active_preset="기본",
    presets=(
        WorkspacePreset(
            name="기본",
            panels=tuple(
                PanelPreference(panel_id, True, logical_size, focus_order)
                for focus_order, (panel_id, logical_size) in enumerate((
                    ("CHART", 720),
                    ("OHLCV", 320),
                    ("INSTRUMENT_FACTS", 320),
                    ("WATCHLIST", 280),
                    ("SOURCE_STATUS", 240),
                ))
            ),
        ),
    ),
)


class LocalResearchWorkspacePreferencesStore:
    """Atomic primary plus last-valid backup for presentation-only settings."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.backup_path = self.path.with_name(
            f"{self.path.stem}.last_valid{self.path.suffix}"
        )

    def load(self) -> ResearchWorkspacePreferencesLoadResult:
        if not self.path.exists():
            recovered = self._try_load(self.backup_path)
            if recovered is not None:
                return ResearchWorkspacePreferencesLoadResult(
                    recovered, "RECOVERED_LAST_VALID"
                )
            return ResearchWorkspacePreferencesLoadResult(
                DEFAULT_PREFERENCES, "DEFAULT_MISSING"
            )

        loaded = self._try_load(self.path)
        if loaded is not None:
            return ResearchWorkspacePreferencesLoadResult(loaded, "LOADED")

        recovered = self._try_load(self.backup_path)
        if recovered is not None:
            return ResearchWorkspacePreferencesLoadResult(
                recovered, "RECOVERED_LAST_VALID"
            )
        return ResearchWorkspacePreferencesLoadResult(
            DEFAULT_PREFERENCES, "DEFAULT_CORRUPT"
        )

    def save(self, preferences: ResearchWorkspacePreferences) -> None:
        encoded = _encode(preferences_payload(preferences))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise ResearchWorkspacePreferencesError(
                "RESEARCH_WORKSPACE_PREFERENCES_WRITE_FAILED"
            ) from None

        current = self._read_valid_primary()
        try:
            if current is not None:
                _atomic_replace(self.backup_path, current)
            _atomic_replace(self.path, encoded)
            try:
                _atomic_replace(self.backup_path, encoded)
            except OSError:
                # Primary is committed and valid. Preserve any older valid
                # backup if refreshing last-valid storage fails.
                pass
        except OSError:
            raise ResearchWorkspacePreferencesError(
                "RESEARCH_WORKSPACE_PREFERENCES_WRITE_FAILED"
            ) from None

    def reset(self) -> ResearchWorkspacePreferences:
        self.save(DEFAULT_PREFERENCES)
        return DEFAULT_PREFERENCES

    def _read_valid_primary(self) -> bytes | None:
        try:
            if not self.path.is_file() or self.path.is_symlink():
                return None
            body = self.path.read_bytes()
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                return None
            _parse_payload(payload)
            return body
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ResearchWorkspacePreferencesError,
            TypeError,
            ValueError,
        ):
            return None

    def _try_load(self, path: Path) -> ResearchWorkspacePreferences | None:
        try:
            if not path.is_file() or path.is_symlink():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            return _parse_payload(payload)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ResearchWorkspacePreferencesError,
            TypeError,
            ValueError,
        ):
            return None


def preferences_payload(
    preferences: ResearchWorkspacePreferences,
) -> dict[str, object]:
    _validate_preferences(preferences)
    return {
        "schema_version": SCHEMA_VERSION,
        "active_preset": preferences.active_preset,
        "presets": [
            {
                "name": preset.name,
                "panels": [
                    {
                        "panel_id": panel.panel_id,
                        "visible": panel.visible,
                        "logical_size": panel.logical_size,
                        "focus_order": panel.focus_order,
                    }
                    for panel in preset.panels
                ],
            }
            for preset in preferences.presets
        ],
    }


def _parse_payload(payload: Mapping[str, object]) -> ResearchWorkspacePreferences:
    if set(payload) != _ROOT_KEYS or payload.get("schema_version") != SCHEMA_VERSION:
        raise ResearchWorkspacePreferencesError(
            "RESEARCH_WORKSPACE_PREFERENCES_SCHEMA_INVALID"
        )
    raw_presets = payload["presets"]
    if not isinstance(raw_presets, list):
        raise ResearchWorkspacePreferencesError(
            "RESEARCH_WORKSPACE_PREFERENCES_SCHEMA_INVALID"
        )
    presets = tuple(_parse_preset(value) for value in raw_presets)
    preferences = ResearchWorkspacePreferences(
        active_preset=payload["active_preset"],
        presets=presets,
    )
    _validate_preferences(preferences)
    return preferences


def _parse_preset(value: object) -> WorkspacePreset:
    if not isinstance(value, Mapping) or set(value) != _PRESET_KEYS:
        raise ResearchWorkspacePreferencesError(
            "RESEARCH_WORKSPACE_PREFERENCES_SCHEMA_INVALID"
        )
    raw_panels = value["panels"]
    if not isinstance(raw_panels, list):
        raise ResearchWorkspacePreferencesError(
            "RESEARCH_WORKSPACE_PREFERENCES_SCHEMA_INVALID"
        )
    return WorkspacePreset(
        name=value["name"],
        panels=tuple(_parse_panel(panel) for panel in raw_panels),
    )


def _parse_panel(value: object) -> PanelPreference:
    if not isinstance(value, Mapping) or set(value) != _PANEL_KEYS:
        raise ResearchWorkspacePreferencesError(
            "RESEARCH_WORKSPACE_PREFERENCES_SCHEMA_INVALID"
        )
    return PanelPreference(
        panel_id=value["panel_id"],
        visible=value["visible"],
        logical_size=value["logical_size"],
        focus_order=value["focus_order"],
    )


def _validate_preferences(preferences: ResearchWorkspacePreferences) -> None:
    if not isinstance(preferences, ResearchWorkspacePreferences):
        _invalid()
    if not isinstance(preferences.presets, tuple) or not (
        1 <= len(preferences.presets) <= MAX_PRESETS
    ):
        _invalid()

    names: list[str] = []
    for preset in preferences.presets:
        if not isinstance(preset, WorkspacePreset):
            _invalid()
        _validate_preset_name(preset.name)
        names.append(preset.name)
        if not isinstance(preset.panels, tuple):
            _invalid()
        panel_ids = tuple(panel.panel_id for panel in preset.panels if isinstance(panel, PanelPreference))
        if len(panel_ids) != len(preset.panels) or (
            len(panel_ids) != len(PANEL_IDS) or set(panel_ids) != set(PANEL_IDS)
        ):
            _invalid()
        focus_orders: list[int] = []
        for panel in preset.panels:
            if not isinstance(panel.visible, bool):
                _invalid()
            if isinstance(panel.logical_size, bool) or not isinstance(panel.logical_size, int):
                _invalid()
            if not MIN_LOGICAL_SIZE <= panel.logical_size <= MAX_LOGICAL_SIZE:
                _invalid()
            if isinstance(panel.focus_order, bool) or not isinstance(panel.focus_order, int):
                _invalid()
            focus_orders.append(panel.focus_order)
        if set(focus_orders) != set(range(len(PANEL_IDS))) or len(set(focus_orders)) != len(focus_orders):
            _invalid()

    if len(set(names)) != len(names):
        _invalid()
    if not isinstance(preferences.active_preset, str) or preferences.active_preset not in names:
        _invalid()


def _validate_preset_name(value: object) -> None:
    if not isinstance(value, str) or value != value.strip() or not _PRESET_NAME.fullmatch(value):
        _invalid()
    folded = value.casefold()
    if any(token in folded for token in _PRIVATE_NAME_TOKENS):
        _invalid()
    if any(marker in value for marker in ("@", "://", "\\", "/", "=")):
        _invalid()


def _invalid() -> None:
    raise ResearchWorkspacePreferencesError(
        "RESEARCH_WORKSPACE_PREFERENCES_SCHEMA_INVALID"
    )


def _encode(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _atomic_replace(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "DEFAULT_PREFERENCES",
    "LocalResearchWorkspacePreferencesStore",
    "MAX_LOGICAL_SIZE",
    "MAX_PRESETS",
    "MIN_LOGICAL_SIZE",
    "PANEL_IDS",
    "PanelPreference",
    "ResearchWorkspacePreferences",
    "ResearchWorkspacePreferencesError",
    "ResearchWorkspacePreferencesLoadResult",
    "SCHEMA_VERSION",
    "WorkspacePreset",
    "preferences_payload",
]
