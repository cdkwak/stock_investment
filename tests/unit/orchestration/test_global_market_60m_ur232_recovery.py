from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from stock_data.orchestration.global_market_60m_ur232_recovery import (
    LANDING_ROOT, PROJECTION_ROOT, RUN_ID, SPECS, recover, read_observation,
)


AUDIT = datetime(2026, 8, 21, 12, 12, tzinfo=timezone.utc)


def _body(series_id: str, *, start: datetime = datetime(2026, 8, 21, 11, tzinfo=timezone.utc)) -> bytes:
    spec = SPECS[series_id]
    symbol = "USDKRW=X" if series_id == "USD_KRW_60M" else spec.provider_symbol
    instrument = "CURRENCY" if series_id == "USD_KRW_60M" else "FUTURE"
    payload = {"chart": {"error": None, "result": [{
        "meta": {"symbol": symbol, "dataGranularity": "60m", "instrumentType": instrument, "regularMarketTime": int(AUDIT.timestamp())},
        "timestamp": [int(start.timestamp())],
        "indicators": {"quote": [{"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.5], "volume": [None]}]},
    }]}}
    return json.dumps(payload).encode("utf-8")


def _landing(root: Path, series_id: str, body: bytes) -> Path:
    call = root / LANDING_ROOT / "yahoo" / "global_chart_60m" / series_id / "call.json"
    call.parent.mkdir(parents=True, exist_ok=True)
    response = call.with_name("response.body")
    response.write_bytes(body)
    call.write_text(json.dumps({
        "provider": "yahoo", "operation": "global_chart_60m", "http_status": 200,
        "landing_body_file": "response.body", "response_body_sha256": hashlib.sha256(body).hexdigest(),
        "request_parameters": {"series_id": series_id},
    }), encoding="utf-8")
    return response


def _all_landing(root: Path, *, start: datetime = datetime(2026, 8, 21, 11, tzinfo=timezone.utc)) -> None:
    for series_id in SPECS:
        _landing(root, series_id, _body(series_id, start=start))


def test_recovery_hash_reads_exact_bodies_and_projects_four_current_observations(tmp_path: Path) -> None:
    _all_landing(tmp_path)
    result = recover(tmp_path, audit_at=AUDIT)
    assert result.api_calls == 0 and set(result.accepted) == set(SPECS) and result.rejected == {}
    for series_id, observation in result.accepted.items():
        assert observation.interval.value == "60m" and observation.provider_timestamp_utc == "2026-08-21T12:00:00+00:00"
        assert read_observation(tmp_path, series_id) == observation
    assert result.accepted["USD_KRW_60M"].unit == "KRW per USD"
    assert "yield" not in result.accepted["UST10_FUTURES_60M"].unit
    assert RUN_ID in (tmp_path / PROJECTION_ROOT / "ust10_futures_60m.json").read_text(encoding="utf-8")


def test_tampered_body_is_rejected_without_touching_prior_bytes(tmp_path: Path) -> None:
    _all_landing(tmp_path)
    assert "USD_KRW_60M" in recover(tmp_path, audit_at=AUDIT).accepted
    projection = tmp_path / PROJECTION_ROOT / "usd_krw_60m.json"
    prior = projection.read_bytes()
    body = tmp_path / LANDING_ROOT / "yahoo" / "global_chart_60m" / "USD_KRW_60M" / "response.body"
    body.write_bytes(body.read_bytes() + b"x")
    result = recover(tmp_path, audit_at=AUDIT)
    assert result.rejected["USD_KRW_60M"] == "LANDING_BODY_HASH_MISMATCH"
    assert projection.read_bytes() == prior


def test_live_forming_and_stale_bars_are_numeric_free(tmp_path: Path) -> None:
    _all_landing(tmp_path, start=datetime(2026, 8, 21, 12, tzinfo=timezone.utc))
    live = recover(tmp_path, audit_at=AUDIT)
    assert live.accepted == {} and set(live.rejected) == set(SPECS)
    stale_root = tmp_path / "stale"; _all_landing(stale_root, start=datetime(2026, 8, 21, 10, tzinfo=timezone.utc))
    stale = recover(stale_root, audit_at=AUDIT)
    assert stale.accepted == {} and set(stale.rejected.values()) == {"SOURCE_AGE_OVER_60M_OR_FUTURE"}


def test_unit_or_yield_semantics_mismatch_is_rejected(tmp_path: Path, monkeypatch) -> None:
    _all_landing(tmp_path)
    monkeypatch.setitem(SPECS, "UST10_FUTURES_60M", replace(SPECS["UST10_FUTURES_60M"], unit="Treasury yield"))
    result = recover(tmp_path, audit_at=AUDIT)
    assert result.rejected["UST10_FUTURES_60M"] == "UNIT_OR_SEMANTICS_OR_OHLC_REJECTED"
    assert "UST10_FUTURES_60M" not in result.accepted


def test_idempotent_replay_is_api_zero_and_does_not_replace_bytes(tmp_path: Path) -> None:
    _all_landing(tmp_path)
    first = recover(tmp_path, audit_at=AUDIT)
    before = {series: (tmp_path / PROJECTION_ROOT / f"{series.lower()}.json").read_bytes() for series in SPECS}
    second = recover(tmp_path, audit_at=AUDIT)
    after = {series: (tmp_path / PROJECTION_ROOT / f"{series.lower()}.json").read_bytes() for series in SPECS}
    assert set(first.accepted) == set(SPECS) and second.api_calls == 0 and set(second.replayed) == set(SPECS)
    assert after == before
