"""Public, secret-free activation manifest for UR-161's exact three windows."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


MANIFEST_PATH = Path("data/state/naver_mobile_basic_000660_ur161_activation.json")
WINDOW_IDS = (
    "2026-08-21T14:30:00+09:00",
    "2026-08-21T15:00:00+09:00",
    "2026-08-21T15:30:00+09:00",
)
KST = ZoneInfo("Asia/Seoul")


def manifest_payload() -> dict[str, object]:
    """Return the GUI-readable activation truth; it never contains secrets."""
    return {
        "schema_version": 1,
        "operation_id": "UR-161",
        "route_id": "naver-web-current:XKRX:000660",
        "identity": {"dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": "000660"},
        "allowed_window_ids": list(WINDOW_IDS),
        "cadence": "30m",
        "timeout_seconds": 10,
        "retry_count": 0,
        "display_only": True,
        "pit_safe": False,
        "collector_api": "NaverMobileBasicWindowedCollector.run(now, response_factory, allowed_window_ids)",
        "durable_ledger_path": "data/state/naver_mobile_basic_000660_30m_ur153.json",
        "projection_path": "data/state/current_observations/naver_web_000660_current.json",
    }


def ensure_manifest(root: Path) -> Path:
    """Create/read back one immutable same-volume activation manifest."""
    path = Path(root) / MANIFEST_PATH
    expected = manifest_payload()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2))
            stream.flush(); os.fsync(stream.fileno())
    except FileExistsError:
        pass
    try:
        actual = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("UR-161 activation manifest is unreadable") from error
    if actual != expected:
        raise RuntimeError("UR-161 activation manifest differs from its exact approved scope")
    return path


def read_manifest(root: Path) -> dict[str, object]:
    """Read and exactly validate an existing public activation manifest."""
    path = Path(root) / MANIFEST_PATH
    try:
        actual = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("UR-161 activation manifest is unreadable") from error
    if actual != manifest_payload():
        raise RuntimeError("UR-161 activation manifest differs from its exact approved scope")
    return actual


def is_active(root: Path, *, now: datetime) -> bool:
    """Read-only public activation predicate; it never constructs transport."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("UR-161 activation clock must be timezone-aware")
    manifest = read_manifest(root)
    local = now.astimezone(KST).replace(second=0, microsecond=0)
    window = local.replace(minute=local.minute - (local.minute % 30)).isoformat()
    allowed = manifest["allowed_window_ids"]
    return isinstance(allowed, list) and window in allowed


__all__ = ["MANIFEST_PATH", "WINDOW_IDS", "ensure_manifest", "is_active", "manifest_payload", "read_manifest"]
