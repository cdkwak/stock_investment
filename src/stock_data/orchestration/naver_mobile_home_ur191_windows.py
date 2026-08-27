"""Public, next-session activation contract for UR-191 Naver mobile-home windows."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector
from stock_data.orchestration.naver_mobile_home_windows import KST, select_due_window, window_id


OPERATION_ID = "UR-191"
TARGET_DATE_KST = "2026-08-24"
WINDOW_IDS = tuple(f"2026-08-24T{hour:02d}:{minute:02d}:00+09:00" for hour, minute in (
    (9, 30), (10, 0), (10, 30), (11, 0), (11, 30), (12, 0), (12, 30),
    (13, 0), (13, 30), (14, 0), (14, 30), (15, 0), (15, 30),
))
CONDITIONAL_WINDOW_IDS = ("2026-08-24T15:30:00+09:00",)
MANIFEST_PATH = Path("data/state/naver_mobile_home_ur191_activation.json")
STATE_PATH = Path("data/state/naver_mobile_home_ur191_windows.json")
LANDING_ROOT = Path("data/landing/naver_mobile_home/ur191")


def manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_id": OPERATION_ID,
        "route": "NAVER_WEB:/",
        "target_date_kst": TARGET_DATE_KST,
        "allowed_window_ids": list(WINDOW_IDS),
        "conditional_window_ids": list(CONDITIONAL_WINDOW_IDS),
        "conditional_window_rule": "per-record strict parser acceptance requires explicit price and provider timestamp",
        "timeout_seconds": 10,
        "retry_count": 0,
        "redirect_count": 0,
        "fallback_count": 0,
        "display_only": True,
        "pit_safe": False,
        "collector_api": "NaverMobileHomeWindowedCollector.run(now, response_factory, allowed_window_ids)",
        "landing_root": LANDING_ROOT.as_posix(),
        "state_path": STATE_PATH.as_posix(),
        "projection_path": "data/state/current_observations/naver_mobile_home_current.json",
    }


def ensure_manifest(root: Path) -> Path:
    path = Path(root) / MANIFEST_PATH
    expected = manifest_payload()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
    except FileExistsError:
        pass
    if json.loads(path.read_text(encoding="utf-8")) != expected:
        raise RuntimeError("UR-191 activation manifest differs from approved scope")
    return path


def read_manifest(root: Path) -> dict[str, object]:
    try:
        actual = json.loads((Path(root) / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("UR-191 activation manifest is unreadable") from error
    if actual != manifest_payload():
        raise RuntimeError("UR-191 activation manifest differs from approved scope")
    return actual


def is_active(root: Path, *, now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    allowed = read_manifest(root)["allowed_window_ids"]
    return isinstance(allowed, list) and select_due_window(allowed_window_ids=allowed, now=now.astimezone(KST)) is not None

def selected_boundary(root: Path, *, now: datetime) -> str | None:
    allowed = read_manifest(root)["allowed_window_ids"]
    return select_due_window(allowed_window_ids=allowed, now=now.astimezone(KST)) if isinstance(allowed, list) else None

def eligible_boundary(root: Path, *, now: datetime) -> str | None:
    boundary = selected_boundary(root, now=now)
    path = Path(root) / STATE_PATH
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error: raise RuntimeError("UR-191 ledger unreadable") from error
        if not isinstance(state, dict) or set(state) != {"schema_version", "operation_id", "windows"} or state.get("schema_version") != 1 or state.get("operation_id") != OPERATION_ID or not isinstance(state.get("windows"), dict): raise RuntimeError("UR-191 ledger malformed")
        current = state["windows"].get(boundary) if boundary is not None else None
        if current is not None:
            if not isinstance(current, dict) or not isinstance(current.get("status"), str): raise RuntimeError("UR-191 current ledger record malformed")
            return None
    return boundary


def collector(root: Path) -> NaverMobileHomeWindowedCollector:
    return NaverMobileHomeWindowedCollector(
        Path(root), operation_id=OPERATION_ID, state_path=STATE_PATH, landing_root=LANDING_ROOT,
    )


__all__ = [
    "CONDITIONAL_WINDOW_IDS", "LANDING_ROOT", "MANIFEST_PATH", "OPERATION_ID", "STATE_PATH",
    "TARGET_DATE_KST", "WINDOW_IDS", "collector", "eligible_boundary", "ensure_manifest", "is_active", "manifest_payload", "read_manifest", "selected_boundary",
]
