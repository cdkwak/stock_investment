"""UR-227's independent 19:45 KST urllib USD/KRW window."""

from __future__ import annotations

import json
import os
from datetime import datetime, time
from pathlib import Path

from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector


OPERATION_ID = "UR-227"
WINDOW_ID = "2026-08-21T19:45:00+09:00"
MANIFEST_PATH = Path("data/state/naver_mobile_home_ur227_activation.json")
STATE_PATH = Path("data/state/naver_mobile_home_ur227_window.json")
LANDING_ROOT = Path("data/landing/naver_mobile_home/ur227")


def manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_id": OPERATION_ID,
        "allowed_window_ids": [WINDOW_ID],
        "route": "NAVER_WEB:/",
        "transport": "stdlib_urllib.request",
        "projection_cids": ["FX_USDKRW"],
        "timeout_seconds": 10,
        "request_cap": 1,
        "retry_count": 0,
        "redirect_count": 0,
        "fallback_count": 0,
        "cookies": False,
        "proxy_environment_inspection": False,
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
        raise RuntimeError("UR-227 activation manifest differs from approved scope")
    return path


def read_manifest(root: Path) -> dict[str, object]:
    try:
        payload = json.loads((Path(root) / MANIFEST_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("UR-227 activation manifest is unreadable") from error
    if payload != manifest_payload():
        raise RuntimeError("UR-227 activation manifest differs from approved scope")
    return payload


def selected_boundary(root: Path, *, now: datetime) -> str | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    kst = now.astimezone(datetime.fromisoformat(WINDOW_ID).tzinfo)
    active = kst.date().isoformat() == "2026-08-21" and time(19, 45) <= kst.timetz().replace(tzinfo=None) < time(20, 0)
    allowed = read_manifest(root).get("allowed_window_ids")
    return WINDOW_ID if active and isinstance(allowed, list) and allowed == [WINDOW_ID] else None


def _window_selector(*, now: datetime) -> str:
    return WINDOW_ID


def collector(root: Path) -> NaverMobileHomeWindowedCollector:
    return NaverMobileHomeWindowedCollector(
        Path(root), operation_id=OPERATION_ID, state_path=STATE_PATH,
        landing_root=LANDING_ROOT, projection_cids=("FX_USDKRW",),
        window_selector=_window_selector,
    )


__all__ = [
    "LANDING_ROOT", "MANIFEST_PATH", "OPERATION_ID", "STATE_PATH", "WINDOW_ID",
    "collector", "ensure_manifest", "manifest_payload", "read_manifest", "selected_boundary",
]
