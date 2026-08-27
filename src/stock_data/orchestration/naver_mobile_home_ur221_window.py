"""UR-221's independent 19:30 KST USD/KRW mobile-home window."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector
from stock_data.orchestration.naver_mobile_home_windows import window_id


OPERATION_ID = "UR-221"
WINDOW_ID = "2026-08-21T19:30:00+09:00"
MANIFEST_PATH = Path("data/state/naver_mobile_home_ur221_activation.json")
STATE_PATH = Path("data/state/naver_mobile_home_ur221_window.json")
LANDING_ROOT = Path("data/landing/naver_mobile_home/ur221")


def manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_id": OPERATION_ID,
        "allowed_window_ids": [WINDOW_ID],
        "route": "NAVER_WEB:/",
        "projection_cids": ["FX_USDKRW"],
        "timeout_seconds": 10,
        "request_cap": 1,
        "retry_count": 0,
        "redirect_count": 0,
        "fallback_count": 0,
        "display_only": True,
        "pit_safe": False,
        "state_path": STATE_PATH.as_posix(),
        "landing_root": LANDING_ROOT.as_posix(),
    }


def ensure_manifest(root: Path) -> Path:
    path = Path(root) / MANIFEST_PATH
    expected = manifest_payload()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        pass
    if json.loads(path.read_text(encoding="utf-8")) != expected:
        raise RuntimeError("UR-221 activation manifest differs from approved scope")
    return path


def read_manifest(root: Path) -> dict[str, object]:
    try:
        payload = json.loads((Path(root) / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("UR-221 activation manifest is unreadable") from error
    if payload != manifest_payload():
        raise RuntimeError("UR-221 activation manifest differs from approved scope")
    return payload


def selected_boundary(root: Path, *, now: datetime) -> str | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    allowed = read_manifest(root).get("allowed_window_ids")
    current = window_id(now=now)
    return current if isinstance(allowed, list) and current == WINDOW_ID and current in allowed else None


def collector(root: Path) -> NaverMobileHomeWindowedCollector:
    return NaverMobileHomeWindowedCollector(
        Path(root), operation_id=OPERATION_ID, state_path=STATE_PATH,
        landing_root=LANDING_ROOT, projection_cids=("FX_USDKRW",),
    )


__all__ = [
    "LANDING_ROOT", "MANIFEST_PATH", "OPERATION_ID", "STATE_PATH", "WINDOW_ID",
    "collector", "ensure_manifest", "manifest_payload", "read_manifest", "selected_boundary",
]
