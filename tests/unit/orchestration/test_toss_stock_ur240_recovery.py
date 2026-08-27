from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stock_data.orchestration.toss_stock_ur240_recovery import (
    TossUr240RecoveryError,
    recover_ur239_nxt_session_close,
)


def _landing(*, venue: str | None = None, session: str | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": "000660", "currency": "KRW", "lastPrice": "1761000",
        "timestamp": "2026-08-21T19:59:59+09:00",
    }
    if venue is not None:
        row["venue"] = venue
    if session is not None:
        row["session"] = session
    return {
        "captured_at_utc": "2026-08-21T13:12:25.785444+00:00",
        "endpoint": "/api/v1/prices", "expected_market_date": "2026-08-21",
        "params": {"symbols": "000660"}, "provider": "tossinvest_open_api",
        "raw_response": {"result": [row]}, "raw_sha256": "source-raw-hash",
    }


def _write_landing(root: Path, payload: dict[str, object]) -> tuple[Path, str]:
    path = root / "landing.json"
    path.write_bytes(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return path.relative_to(root), hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_venue_session_uses_user_authorized_inferred_close_and_replays_api_zero(tmp_path: Path) -> None:
    landing_path, digest = _write_landing(tmp_path, _landing())
    result = recover_ur239_nxt_session_close(
        tmp_path, landing_path=landing_path, expected_sha256=digest,
        projection_path=Path("projection.json"),
    )
    assert result.status == "TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW"
    assert result.venue_inferred is True
    assert result.provider_timestamp_utc == "2026-08-21T10:59:59+00:00"
    assert result.replay_api_calls == 0
    stored = json.loads((tmp_path / "projection.json").read_text(encoding="utf-8"))
    row = stored["observations"][0]
    assert row["identity"] == {"dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": "000660"}
    assert row["unit"] == "KRW per share"
    assert row["finality"] == "POST_CLOSE_SNAPSHOT"


@pytest.mark.parametrize("field", ("venue", "session"))
def test_partial_or_contradictory_venue_session_preserves_prior_projection(tmp_path: Path, field: str) -> None:
    good_path, good_hash = _write_landing(tmp_path, _landing())
    projection = Path("projection.json")
    recover_ur239_nxt_session_close(tmp_path, landing_path=good_path, expected_sha256=good_hash, projection_path=projection)
    before = (tmp_path / projection).read_bytes()
    partial_path, partial_hash = _write_landing(tmp_path, _landing(**{field: "XKRX" if field == "venue" else "NXT"}))
    with pytest.raises(TossUr240RecoveryError, match="venue-session"):
        recover_ur239_nxt_session_close(tmp_path, landing_path=partial_path, expected_sha256=partial_hash, projection_path=projection)
    assert (tmp_path / projection).read_bytes() == before


def test_hash_tamper_fails_before_parse_or_projection(tmp_path: Path) -> None:
    landing_path, digest = _write_landing(tmp_path, _landing())
    with pytest.raises(TossUr240RecoveryError, match="hash mismatch"):
        recover_ur239_nxt_session_close(
            tmp_path, landing_path=landing_path, expected_sha256="0" * 64,
            projection_path=Path("projection.json"),
        )
    assert not (tmp_path / "projection.json").exists()


def test_timestamp_outside_exclusive_nxt_close_window_is_not_inferred(tmp_path: Path) -> None:
    payload = _landing()
    payload["raw_response"]["result"][0]["timestamp"] = "2026-08-21T20:00:01+09:00"
    landing_path, digest = _write_landing(tmp_path, payload)
    with pytest.raises(TossUr240RecoveryError, match="exact NXT close window"):
        recover_ur239_nxt_session_close(
            tmp_path, landing_path=landing_path, expected_sha256=digest,
            projection_path=Path("projection.json"),
        )
    assert not (tmp_path / "projection.json").exists()
