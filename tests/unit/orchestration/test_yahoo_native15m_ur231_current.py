from datetime import datetime, timedelta, timezone
import json

import pandas as pd

from stock_data.contracts.market_15m import MARKET_15M_SERIES_POLICIES, MARKET_PRICE_15M_OBSERVATION
from stock_data.orchestration.yahoo_native15m_ur231_current import (
    LANES, claim, eligibility, ensure_manifest, execute_injected, operational_run, selected_boundary, state_path,
    validate_first_completed_bar,
)
from stock_data.validation.market_15m import YAHOO_15M_IDENTITIES


def _frame(lane_id: str, *, live: bool = False) -> pd.DataFrame:
    plan = LANES[lane_id]; start = pd.Timestamp(plan.source_start_utc)
    rows = []
    for series_id in plan.series_ids:
        market, instrument, session = YAHOO_15M_IDENTITIES[series_id]; policy = MARKET_15M_SERIES_POLICIES[series_id]
        rows.append({"market_date": start.tz_convert(policy.source_timezone).date(), "market": market, "series_id": series_id, "provider_symbol": series_id, "instrument_type": instrument, "bar_start": start, "bar_end": start + timedelta(minutes=15), "source_timezone": policy.source_timezone, "display_timezone": "Asia/Seoul", "session": session, "interval": "15m", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1, "provider": "yahoo_chart_api", "data_availability": "INDICATIVE_DELAYED_NOT_LICENSED_REALTIME", "retrieved_at": start + (timedelta(minutes=10) if live else timedelta(minutes=15))})
    return pd.DataFrame(rows, columns=MARKET_PRICE_15M_OBSERVATION.column_names)


def test_preboundary_and_half_open_selection_are_api_zero(tmp_path) -> None:
    ensure_manifest(tmp_path)
    treasury = datetime(2026, 8, 21, 13, 34, tzinfo=timezone.utc)
    assert eligibility(tmp_path, "YAHOO_TREASURY_QUOTE", now=treasury) == "API_ZERO_PREBOUNDARY_OR_WINDOW_CLOSED"
    assert selected_boundary("YAHOO_TREASURY_QUOTE", now=treasury + timedelta(minutes=1)) == "2026-08-21T22:35:00+09:00"
    assert selected_boundary("YAHOO_TREASURY_QUOTE", now=treasury + timedelta(minutes=16)) is None
    assert selected_boundary("CBOE_VIX", now=datetime(2026, 8, 21, 13, 45, tzinfo=timezone.utc)) == "2026-08-21T22:45:00+09:00"


def test_installed_manifest_readback_is_idempotent_and_ledgers_remain_api_zero(tmp_path) -> None:
    first = ensure_manifest(tmp_path); bytes_before = first.read_bytes()
    assert ensure_manifest(tmp_path).read_bytes() == bytes_before
    assert eligibility(tmp_path, "YAHOO_TREASURY_QUOTE", now=datetime(2026, 8, 21, 13, 35, tzinfo=timezone.utc)) == "ELIGIBLE"
    assert eligibility(tmp_path, "CBOE_VIX", now=datetime(2026, 8, 21, 13, 35, tzinfo=timezone.utc)) == "API_ZERO_PREBOUNDARY_OR_WINDOW_CLOSED"
    assert not (tmp_path / state_path("YAHOO_TREASURY_QUOTE")).exists()


def test_claim_orphan_and_terminal_are_no_repeat_per_lane(tmp_path) -> None:
    ensure_manifest(tmp_path); now = datetime(2026, 8, 21, 13, 35, tzinfo=timezone.utc)
    assert claim(tmp_path, "YAHOO_TREASURY_QUOTE", now=now) == "CLAIMED_NO_TRANSPORT"
    assert eligibility(tmp_path, "YAHOO_TREASURY_QUOTE", now=now) == "ORPHAN_ATTEMPTING_NO_REPEAT"
    state = tmp_path / state_path("YAHOO_TREASURY_QUOTE"); payload = json.loads(state.read_text(encoding="utf-8")); key = "2026-08-21T22:35:00+09:00"; payload["windows"][key]["status"] = "COMPLETE_FAILURE"; state.write_text(json.dumps(payload), encoding="utf-8")
    assert eligibility(tmp_path, "YAHOO_TREASURY_QUOTE", now=now) == "NO_REPEAT"
    assert eligibility(tmp_path, "CBOE_VIX", now=datetime(2026, 8, 21, 13, 45, tzinfo=timezone.utc)) == "ELIGIBLE"


def test_malformed_state_is_api_zero_and_does_not_touch_other_lane(tmp_path) -> None:
    ensure_manifest(tmp_path); broken = tmp_path / state_path("CBOE_VIX"); broken.parent.mkdir(parents=True, exist_ok=True); broken.write_text("{", encoding="utf-8")
    assert eligibility(tmp_path, "CBOE_VIX", now=datetime(2026, 8, 21, 13, 45, tzinfo=timezone.utc)) == "API_ZERO_INVALID_MANIFEST_OR_LEDGER"
    assert eligibility(tmp_path, "YAHOO_TREASURY_QUOTE", now=datetime(2026, 8, 21, 13, 35, tzinfo=timezone.utc)) == "ELIGIBLE"


def test_strict_first_completed_bar_rejects_live_forming_without_state_change(tmp_path) -> None:
    frame = _frame("CBOE_VIX", live=True); before = frame.to_json()
    try: validate_first_completed_bar("CBOE_VIX", frame, retrieved_at=datetime(2026, 8, 21, 13, 40, tzinfo=timezone.utc))
    except ValueError as error: assert "live-forming" in str(error)
    else: raise AssertionError("live-forming bar was accepted")
    assert frame.to_json() == before and not (tmp_path / state_path("CBOE_VIX")).exists()


def test_strict_first_completed_bar_accepts_only_exact_lane_grid() -> None:
    frame = _frame("YAHOO_TREASURY_QUOTE")
    validate_first_completed_bar("YAHOO_TREASURY_QUOTE", frame, retrieved_at=datetime(2026, 8, 21, 13, 35, tzinfo=timezone.utc))
    frame.loc[0, "bar_start"] = pd.Timestamp("2026-08-21T13:35:00Z"); frame.loc[0, "bar_end"] = pd.Timestamp("2026-08-21T13:50:00Z"); frame.loc[0, "retrieved_at"] = pd.Timestamp("2026-08-21T13:50:00Z")
    try: validate_first_completed_bar("YAHOO_TREASURY_QUOTE", frame, retrieved_at=datetime(2026, 8, 21, 13, 50, tzinfo=timezone.utc))
    except ValueError as error: assert "bar start" in str(error)
    else: raise AssertionError("wrong grid accepted")


def test_injected_lane_landing_and_projection_are_atomic_on_mixed_failure(tmp_path) -> None:
    ensure_manifest(tmp_path); now = datetime(2026, 8, 21, 13, 35, tzinfo=timezone.utc); calls = []
    def parser(series_id, _body, _now): return _frame("YAHOO_TREASURY_QUOTE").loc[lambda frame: frame.series_id.eq(series_id)].copy()
    def ok(series_id): return lambda: (calls.append(series_id) or (200, series_id.encode()))
    assert execute_injected(tmp_path, "YAHOO_TREASURY_QUOTE", now=now, responses={s: ok(s) for s in LANES["YAHOO_TREASURY_QUOTE"].series_ids}, parser=parser) == "COMPLETE_ACCEPTED"
    assert calls == list(LANES["YAHOO_TREASURY_QUOTE"].series_ids)
    assert eligibility(tmp_path, "YAHOO_TREASURY_QUOTE", now=now) == "NO_REPEAT"

    other = tmp_path / "other"; ensure_manifest(other); calls.clear()
    failed = {"^FVX": ok("^FVX"), "^TNX": lambda: (503, b"fail"), "^TYX": ok("^TYX")}
    assert execute_injected(other, "YAHOO_TREASURY_QUOTE", now=now, responses=failed, parser=parser) == "COMPLETE_FAILURE"
    assert calls == ["^FVX", "^TYX"] and not list((other / "data/state/current_observations").glob("**/*.json"))


def test_operational_transport_is_constructed_only_when_eligible_and_exact(tmp_path) -> None:
    ensure_manifest(tmp_path); calls = []
    fake = lambda **kwargs: (calls.append(kwargs) or (503, b"x"))
    assert operational_run(tmp_path, "YAHOO_TREASURY_QUOTE", now=datetime(2026, 8, 21, 13, 34, tzinfo=timezone.utc), transport=fake).startswith("API_ZERO")
    assert calls == []
    assert operational_run(tmp_path, "YAHOO_TREASURY_QUOTE", now=datetime(2026, 8, 21, 13, 35, tzinfo=timezone.utc), transport=fake) == "COMPLETE_FAILURE"
    assert [call["url"].rsplit("/", 1)[-1] for call in calls] == ["%5EFVX", "%5ETNX", "%5ETYX"]
    assert all(call["params"]["interval"] == "15m" and call["timeout"] == 10 and call["allow_redirects"] is False for call in calls)
