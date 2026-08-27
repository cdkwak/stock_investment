"""Public activation contract for UR-167's remaining home-page windows."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
WINDOW_IDS = ("2026-08-21T14:30:00+09:00", "2026-08-21T15:00:00+09:00", "2026-08-21T15:30:00+09:00")
MANIFEST_PATH = Path("data/state/naver_mobile_home_ur167_activation.json")

def manifest_payload() -> dict[str, object]:
    return {"schema_version": 1, "operation_id": "UR-167", "route": "NAVER_WEB:/", "allowed_window_ids": list(WINDOW_IDS), "timeout_seconds": 10, "retry_count": 0, "redirect_count": 0, "display_only": True, "pit_safe": False, "collector_api": "NaverMobileHomeWindowedCollector.run(now, response_factory, allowed_window_ids)", "landing_root": "data/landing/naver_mobile_home/ur167", "projection_path": "data/state/current_observations/naver_mobile_home_current.json"}

def ensure_manifest(root: Path) -> Path:
    path = Path(root) / MANIFEST_PATH; expected = manifest_payload(); path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
    except FileExistsError: pass
    if json.loads(path.read_text(encoding="utf-8")) != expected: raise RuntimeError("UR-167 activation manifest differs from approved scope")
    return path

def read_manifest(root: Path) -> dict[str, object]:
    try: actual = json.loads((Path(root) / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error: raise RuntimeError("UR-167 activation manifest is unreadable") from error
    if actual != manifest_payload(): raise RuntimeError("UR-167 activation manifest differs from approved scope")
    return actual

def window_id(*, now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None: raise ValueError("timezone-aware clock required")
    local = now.astimezone(KST).replace(second=0, microsecond=0)
    return local.replace(minute=local.minute - local.minute % 30).isoformat()

def select_due_window(*, allowed_window_ids: list[str] | tuple[str, ...], now: datetime) -> str | None:
    """Select only the current [boundary, next-boundary) window; never backfill."""
    selected = window_id(now=now)
    return selected if selected in allowed_window_ids else None

def is_active(root: Path, *, now: datetime) -> bool:
    allowed = read_manifest(root)["allowed_window_ids"]
    return isinstance(allowed, list) and select_due_window(allowed_window_ids=allowed, now=now) is not None

__all__ = ["KST", "MANIFEST_PATH", "WINDOW_IDS", "ensure_manifest", "is_active", "manifest_payload", "read_manifest", "window_id"]
