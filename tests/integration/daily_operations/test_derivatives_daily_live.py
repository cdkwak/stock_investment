from __future__ import annotations

from datetime import date, datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import stock_data.orchestration.derivatives_daily_live as module
from stock_data.orchestration.derivatives_daily_live import (
    DerivativesDailyLiveError,
    latest_finalized_session,
    recover_derivatives_daily_from_retained,
    run_derivatives_daily,
)


TARGET = date(2026, 8, 19)


def _calendar_with_dates(root: Path, dates) -> None:
    path = root / "data/normalized/kr_kospi200_index_daily/year=2026/data.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": list(dates)}).to_parquet(path, index=False)


def _calendar(root: Path) -> None:
    _calendar_with_dates(
        root, [date(2026, 8, 18), TARGET, date(2026, 8, 20)],
    )


def _source_through(root: Path, dates) -> None:
    for dataset in ("kr_kospi200_futures_daily", "kr_kospi200_options_daily"):
        path = root / "data/normalized" / dataset / "year=2026/data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": list(dates)}).to_parquet(path, index=False)


def _wall_inputs(root: Path, options_root: Path, dates) -> None:
    option_rows = []
    index_rows = []
    for offset, value in enumerate(dates):
        index_rows.append({
            "date": value,
            "symbol": "KOSPI200",
            "close": 400.0 + offset,
            "source": "pykrx",
        })
        for call_put in ("CALL", "PUT"):
            for strike, open_interest in ((390.0, 100 + offset), (410.0, 80 + offset)):
                option_rows.append({
                    "date": value,
                    "maturity_month": "2027-12",
                    "strike": strike,
                    "call_put": call_put,
                    "open_interest": open_interest + (10 if call_put == "PUT" else 0),
                    "volume": 20 + offset,
                    "bridge_segment": "OFFICIAL_2010_PRESENT",
                    "session": "KRX_REGULAR_SESSION",
                    "source": "data_go_kr",
                })
    options = pd.DataFrame(option_rows)
    index_daily = pd.DataFrame(index_rows)
    for year, frame in options.groupby(pd.to_datetime(options["date"]).dt.year):
        path = options_root / f"year={year}" / "data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
    for year, frame in index_daily.groupby(pd.to_datetime(index_daily["date"]).dt.year):
        path = root / "data/normalized/kr_kospi200_index_daily" / f"year={year}" / "data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)


def test_conservative_finality_selects_only_session_before_local_today(tmp_path):
    _calendar(tmp_path)
    now = datetime.fromisoformat("2026-08-20T00:05:00+09:00")
    assert latest_finalized_session(tmp_path, now=now) == date(2026, 8, 18)


def test_completed_successor_session_releases_exact_prior_session(tmp_path):
    _calendar(tmp_path)
    now = datetime.fromisoformat("2026-08-20T19:00:00+09:00")
    assert latest_finalized_session(tmp_path, now=now) == TARGET


def test_finality_uses_latest_retained_completed_successor_when_calendar_lags(tmp_path):
    _calendar(tmp_path)
    now = datetime.fromisoformat("2026-08-21T19:00:00+09:00")
    assert latest_finalized_session(tmp_path, now=now) == TARGET


def test_oldest_missing_eligible_session_never_skips_retained_source_gap(tmp_path):
    sessions = [
        date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
        date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25),
        date(2026, 8, 26),
    ]
    _calendar_with_dates(tmp_path, sessions)
    _source_through(tmp_path, sessions[:2])

    selected = module.oldest_missing_eligible_session(
        tmp_path, now=datetime.fromisoformat("2026-08-26T19:00:00+09:00"),
    )

    assert selected == date(2026, 8, 20)


def test_daily_target_cannot_skip_a_retained_xkrx_session(tmp_path):
    _calendar_with_dates(
        tmp_path,
        [date(2026, 8, 17), date(2026, 8, 18), TARGET, date(2026, 8, 20)],
    )
    for dataset in ("kr_kospi200_futures_daily", "kr_kospi200_options_daily"):
        path = tmp_path / "data/normalized" / dataset / "year=2026/data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": [date(2026, 8, 17)]}).to_parquet(path, index=False)
    with pytest.raises(DerivativesDailyLiveError, match="immediate next"):
        module._require_next_source_session(tmp_path, TARGET)


def test_valid_empty_success_envelope_is_landing_first_then_fail_closed(tmp_path, monkeypatch):
    payload = {"response": {"header": {"resultCode": "00"}, "body": {
        "pageNo": 1, "numOfRows": 9999, "totalCount": 0, "items": {},
    }}}

    class Client:
        def fetch_all(self, **kwargs):
            return SimpleNamespace(pages=(payload,), items=(), total_count=0)

    monkeypatch.setattr(module, "service_key_from_environment", lambda root: "fixture")
    monkeypatch.setattr(module, "DataGoKrClient", lambda **kwargs: Client())
    prior = tmp_path / "data/landing/data_go_kr/kr_kospi200_futures_daily/20260819.json"
    prior.parent.mkdir(parents=True)
    prior.write_text(json.dumps([payload]), encoding="utf-8")
    before = prior.read_bytes()
    revalidation = prior.parent / "observations/20260819/after_successor_20260820.json"
    with pytest.raises(DerivativesDailyLiveError, match="valid empty"):
        module._collect_exact(
            tmp_path, "kospi200_futures", "20260819",
            landing_override=revalidation,
        )
    assert prior.read_bytes() == before
    assert json.loads(revalidation.read_text(encoding="utf-8")) == [payload]


def test_missing_wall_artifact_rebuilds_recent_250_with_checkpoint_and_target(
    tmp_path,
):
    target = date(2026, 8, 20)
    dates = tuple(stamp.date() for stamp in pd.bdate_range(end=target, periods=251))
    options_root = tmp_path / "candidate/options"
    _wall_inputs(tmp_path, options_root, dates)
    checkpoint = module._target_paths(tmp_path)["checkpoint"]
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps({
        "dataset": "derivatives_price_daily_live",
        "completed_dates": [TARGET.isoformat()],
    }), encoding="utf-8")
    output = tmp_path / "candidate/wall.csv"

    rows = module._build_wall(tmp_path, options_root, target, output)

    restored = pd.read_csv(output, parse_dates=["date"])
    restored_dates = set(restored["date"].dt.date)
    assert rows == len(restored) == 250
    assert TARGET in restored_dates and target in restored_dates
    assert restored["date"].duplicated().sum() == 0
    assert dates[0] not in restored_dates


def test_existing_wall_preserves_unaffected_rows_and_replaces_only_target(
    tmp_path,
):
    dates = (date(2026, 8, 18), TARGET, date(2026, 8, 20))
    options_root = tmp_path / "candidate/options"
    _wall_inputs(tmp_path, options_root, dates)
    current = module._target_paths(tmp_path)["wall"]
    first = tmp_path / "candidate/first.csv"
    module._build_wall(tmp_path, options_root, TARGET, first)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(first.read_bytes())
    prior = pd.read_csv(current, parse_dates=["date"])
    output = tmp_path / "candidate/next.csv"

    module._build_wall(tmp_path, options_root, date(2026, 8, 20), output)

    restored = pd.read_csv(output, parse_dates=["date"])
    preserved = restored.loc[restored["date"].dt.date.isin(
        set(prior["date"].dt.date)
    ), prior.columns]
    pd.testing.assert_frame_equal(
        prior.reset_index(drop=True), preserved.reset_index(drop=True),
        check_dtype=False,
    )
    assert set(restored["date"].dt.date) == set(dates)
    assert restored["date"].duplicated().sum() == 0


def test_live_orchestration_bounds_two_source_calls_and_promotes_one_atomic_scope(tmp_path, monkeypatch):
    _calendar(tmp_path)
    targets = module._target_paths(tmp_path)
    for name in ("source_futures", "source_options"):
        targets[name].mkdir(parents=True)
    for name, dataset in (
        ("state_futures", "kr_kospi200_futures_daily"),
        ("state_options", "kr_kospi200_options_daily"),
    ):
        targets[name].parent.mkdir(parents=True, exist_ok=True)
        targets[name].write_text(json.dumps({"dataset": dataset}), encoding="utf-8")
    for path in (
        tmp_path / "data/normalized/krx_legacy_kospi200_futures_daily",
        tmp_path / "data/normalized/krx_legacy_kospi200_options_daily",
    ):
        path.mkdir(parents=True)
    prior = targets["pcr"] / "year=2019/data.parquet"
    prior.parent.mkdir(parents=True)
    prior.write_bytes(b"prior")

    calls = []
    from stock_data.contracts.derivatives_price_authority import DerivativesPriceAuthority
    monkeypatch.setattr(DerivativesPriceAuthority, "live_validation_ready", property(lambda self: True))
    monkeypatch.setattr(
        module, "latest_finalized_session",
        lambda root, now=None: date(2026, 8, 25),
    )
    monkeypatch.setattr(module, "_require_next_source_session", lambda root, target: None)
    monkeypatch.setattr(module, "_collect_exact", lambda root, key, compact, **kwargs: (pd.DataFrame({"key": [key]}), 1))
    monkeypatch.setattr(module, "_append_source", lambda root, state, frame, key, compact: calls.append(key) or 10)

    def bridge(**kwargs):
        for dataset in (
            "kr_kospi200_futures_provider_bridge_daily",
            "kr_kospi200_options_provider_bridge_daily",
        ):
            (kwargs["output_bundle_root"] / dataset).mkdir(parents=True)
        kwargs["output_state_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_state_path"].write_text("{}", encoding="utf-8")
        return {"datasets": {
            "kr_kospi200_futures_provider_bridge_daily": {"validation": {"rows": 20}},
            "kr_kospi200_options_provider_bridge_daily": {"validation": {"rows": 30}},
        }}

    def derived(**kwargs):
        kwargs["output_root"].mkdir(parents=True)
        kwargs["output_state_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_state_path"].write_text("{}", encoding="utf-8")
        return {"validation": {"rows": 40}}

    monkeypatch.setattr(module, "build_kospi200_derivatives_bridge", bridge)
    monkeypatch.setattr(module, "build_kospi200_futures_nearest_listed", derived)
    monkeypatch.setattr(module, "build_modern_kospi200_option_pcr", derived)
    monkeypatch.setattr(module, "_build_wall", lambda root, options, target, output: output.parent.mkdir(parents=True, exist_ok=True) or output.write_text("date\n2026-08-19\n", encoding="utf-8") or 1)
    promoted = []
    monkeypatch.setattr(module, "_promote_atomic", lambda root, candidates, outputs, target, **kwargs: promoted.append(tuple(candidates)))

    result = run_derivatives_daily(
        tmp_path, market_date=TARGET,
        now=datetime.fromisoformat("2026-08-20T19:00:00+09:00"),
    )
    assert result.status == "AFFECTED_DATE_COMPLETE"
    assert result.api_calls == 2 and result.retry_count == 0
    assert calls == ["kospi200_futures", "kospi200_options"]
    assert result.stages == ("source", "bridge", "basis", "pcr", "wall")
    assert promoted and set(promoted[0]) == set(targets)
    attempt = json.loads(module._attempt_path(tmp_path, TARGET).read_text(encoding="utf-8"))
    assert attempt["status"] == "SUCCEEDED"
    assert attempt["calls_started"] == list(module.SOURCE_KEYS)
    assert attempt["calls_completed"] == list(module.SOURCE_KEYS)


def test_automatic_live_run_collects_oldest_missing_eligible_session(
    tmp_path, monkeypatch,
):
    sessions = [
        date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
        date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25),
        date(2026, 8, 26),
    ]
    _calendar_with_dates(tmp_path, sessions)
    _source_through(tmp_path, sessions[:2])
    collected = []

    def collect(_root, key, compact, **_kwargs):
        collected.append((key, compact))
        return pd.DataFrame({"date": [date.fromisoformat(
            f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
        )]}), 1

    monkeypatch.setattr(module, "_collect_exact", collect)
    monkeypatch.setattr(
        module,
        "_complete_derivatives_transaction",
        lambda _root, **_kwargs: {"wall": 1},
    )

    result = run_derivatives_daily(
        tmp_path,
        now=datetime.fromisoformat("2026-08-26T19:00:00+09:00"),
    )

    assert result.market_date == "2026-08-20"
    assert result.api_calls == 2
    assert collected == [
        ("kospi200_futures", "20260820"),
        ("kospi200_options", "20260820"),
    ]
    attempt = json.loads(
        module._attempt_path(tmp_path, date(2026, 8, 20)).read_text(
            encoding="utf-8"
        )
    )
    assert attempt["completed_successor_session"] == "2026-08-21"


def test_bounded_catchup_advances_three_oldest_sessions_and_reports_remaining(
    tmp_path, monkeypatch,
):
    sessions = [
        date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
        date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25),
        date(2026, 8, 26),
    ]
    _calendar_with_dates(tmp_path, sessions)
    accepted = list(sessions[:2])
    _source_through(tmp_path, accepted)
    attempted = []

    def complete_one(root, *, market_date=None, now=None):
        assert market_date is not None
        attempted.append(market_date)
        accepted.append(market_date)
        _source_through(root, accepted)
        return module.DerivativesDailyLiveResult(
            "AFFECTED_DATE_COMPLETE", market_date.isoformat(), 2, 0,
            ("source", "bridge", "basis", "pcr", "wall"), {"wall": 1},
        )

    monkeypatch.setattr(module, "run_derivatives_daily", complete_one)
    monkeypatch.setattr(module, "monotonic", lambda: 100.0)

    result = module.run_derivatives_daily_catchup(
        tmp_path, now=datetime.fromisoformat("2026-08-26T19:00:00+09:00"),
    )

    assert result.status == "PARTIAL_LIMIT_REACHED"
    assert result.completed_dates == (
        "2026-08-20", "2026-08-21", "2026-08-24",
    )
    assert result.api_calls == 6 and result.retry_count == 0
    assert result.remaining_target == "2026-08-25"
    assert attempted == [
        date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24),
    ]


def test_bounded_catchup_stops_at_first_unresolved_session(tmp_path, monkeypatch):
    sessions = [
        date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
        date(2026, 8, 21), date(2026, 8, 24),
    ]
    _calendar_with_dates(tmp_path, sessions)
    accepted = list(sessions[:2])
    _source_through(tmp_path, accepted)
    attempted = []

    def fail_second(root, *, market_date=None, now=None):
        assert market_date is not None
        attempted.append(market_date)
        if market_date == date(2026, 8, 21):
            raise DerivativesDailyLiveError("unresolved exact date")
        accepted.append(market_date)
        _source_through(root, accepted)
        return module.DerivativesDailyLiveResult(
            "AFFECTED_DATE_COMPLETE", market_date.isoformat(), 2, 0,
            ("source", "bridge", "basis", "pcr", "wall"), {"wall": 1},
        )

    monkeypatch.setattr(module, "run_derivatives_daily", fail_second)
    monkeypatch.setattr(module, "monotonic", lambda: 100.0)

    with pytest.raises(DerivativesDailyLiveError, match="unresolved exact date"):
        module.run_derivatives_daily_catchup(
            tmp_path, now=datetime.fromisoformat("2026-08-24T19:00:00+09:00"),
        )

    assert attempted == [date(2026, 8, 20), date(2026, 8, 21)]
    assert max(accepted) == date(2026, 8, 20)


def test_bounded_catchup_current_result_is_verified_api_zero(tmp_path, monkeypatch):
    noop = module.DerivativesDailyLiveResult(
        "NOOP_IDEMPOTENT", TARGET.isoformat(), 0, 0,
        ("source", "bridge", "basis", "pcr", "wall"), {},
    )
    monkeypatch.setattr(
        module, "oldest_missing_eligible_session", lambda *_args, **_kwargs: None,
    )
    calls = []

    def replay(root, *, market_date=None, now=None):
        calls.append((root, market_date, now))
        return noop

    monkeypatch.setattr(module, "run_derivatives_daily", replay)

    result = module.run_derivatives_daily_catchup(tmp_path)

    assert result.status == "CURRENT"
    assert result.completed_dates == () and result.api_calls == 0
    assert result.remaining_target is None and result.last_result == noop
    assert calls == [(tmp_path.resolve(), None, None)]


def test_checkpointed_same_date_is_pre_network_noop(tmp_path, monkeypatch):
    _calendar(tmp_path)
    targets = module._target_paths(tmp_path)
    for name in ("source_futures", "source_options", "basis", "pcr"):
        path = targets[name] / "year=2026/data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": [TARGET]}).to_parquet(path, index=False)
    for dataset in (
        "kr_kospi200_futures_provider_bridge_daily",
        "kr_kospi200_options_provider_bridge_daily",
    ):
        path = targets["bridge"] / dataset / "year=2026/data.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": [TARGET]}).to_parquet(path, index=False)
    targets["wall"].parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": [TARGET]}).to_csv(targets["wall"], index=False)
    checkpoint = tmp_path / "data/state/derivatives_price_daily_live.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps({
        "dataset": "derivatives_price_daily_live", "completed_dates": [TARGET.isoformat()],
    }), encoding="utf-8")
    from stock_data.contracts.derivatives_price_authority import DerivativesPriceAuthority
    monkeypatch.setattr(DerivativesPriceAuthority, "live_validation_ready", property(lambda self: True))
    monkeypatch.setattr(module, "latest_finalized_session", lambda root, now=None: TARGET)
    monkeypatch.setattr(module, "_collect_exact", lambda *args: pytest.fail("network path must not run"))
    result = run_derivatives_daily(
        tmp_path, market_date=TARGET,
        now=datetime.fromisoformat("2026-08-20T08:00:00+09:00"),
    )
    assert result.status == "NOOP_IDEMPOTENT" and result.api_calls == 0


def test_multi_target_promotion_failure_restores_every_prior_output(tmp_path, monkeypatch):
    monkeypatch.setenv("PSModulePath", str(tmp_path / "poisoned-pwsh7-modules"))
    transaction = tmp_path / "data/staging/derivatives_daily_live/tx"
    candidates = {}
    targets = {}
    for name in ("source", "bridge", "checkpoint"):
        candidate = transaction / "candidates" / name
        target = tmp_path / "production" / name
        candidate.mkdir(parents=True)
        target.mkdir(parents=True)
        (candidate / "value.txt").write_text(f"new-{name}", encoding="utf-8")
        (target / "value.txt").write_text(f"old-{name}", encoding="utf-8")
        candidates[name] = candidate
        targets[name] = target
    original = module._atomic_json

    def fail_after_bridge(path, payload):
        original(path, payload)
        if isinstance(payload, dict) and payload.get("phase") == "PROMOTED_BRIDGE":
            raise OSError("injected journal failure")

    monkeypatch.setattr(module, "_atomic_json", fail_after_bridge)
    with pytest.raises(OSError, match="injected journal failure"):
        module._promote_atomic(tmp_path, candidates, targets, TARGET)
    assert all(
        (targets[name] / "value.txt").read_text(encoding="utf-8") == f"old-{name}"
        for name in targets
    )
    assert not (tmp_path / "data/state/derivatives_price_daily_live.transaction.json").exists()


def _promotion_fixture(tmp_path, *, missing_targets=()):
    transaction = tmp_path / "data/staging/derivatives_daily_live/tx"
    candidates = {}
    targets = {}
    for name in ("source", "bridge", "checkpoint"):
        candidate = transaction / "candidates" / name
        target = tmp_path / "production" / name
        candidate.mkdir(parents=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        (candidate / "value.txt").write_text(f"new-{name}", encoding="utf-8")
        if name not in missing_targets:
            target.mkdir()
            (target / "value.txt").write_text(f"old-{name}", encoding="utf-8")
        candidates[name] = candidate
        targets[name] = target
    return transaction, candidates, targets


def test_acl_restore_failure_rolls_back_replacement_recorded_before_restore(
    tmp_path, monkeypatch,
):
    _transaction, candidates, targets = _promotion_fixture(
        tmp_path, missing_targets={"source"},
    )
    journal = tmp_path / "data/state/derivatives_price_daily_live.transaction.json"
    observed = {}
    monkeypatch.setattr(
        module, "_capture_access_sddl", lambda path: f"DACL:{path.name}",
    )

    def fail_second_restore(path, _sddl):
        if path == targets["bridge"]:
            observed.update(json.loads(journal.read_text(encoding="utf-8")))
            raise PermissionError("injected ACL restore failure")

    monkeypatch.setattr(module, "_restore_access_sddl", fail_second_restore)

    with pytest.raises(PermissionError, match="ACL restore"):
        module._promote_atomic(tmp_path, candidates, targets, TARGET)

    assert observed["phase"] == "REPLACED_BRIDGE"
    assert observed["replaced"] == ["source", "bridge"]
    assert observed["promoted"] == ["source"]
    assert not targets["source"].exists()
    assert (targets["bridge"] / "value.txt").read_text(encoding="utf-8") == "old-bridge"
    assert (targets["checkpoint"] / "value.txt").read_text(encoding="utf-8") == "old-checkpoint"
    assert not journal.exists()


def test_rollback_failure_retains_journal_backup_and_blocks_next_promotion(
    tmp_path, monkeypatch,
):
    transaction, candidates, targets = _promotion_fixture(
        tmp_path, missing_targets={"source"},
    )
    journal = tmp_path / "data/state/derivatives_price_daily_live.transaction.json"
    monkeypatch.setattr(
        module, "_capture_access_sddl", lambda path: f"DACL:{path.name}",
    )

    def fail_second_restore(path, _sddl):
        if path == targets["bridge"]:
            raise PermissionError("injected ACL restore failure")

    original_remove = module._remove

    def fail_bridge_removal(path):
        if path == targets["bridge"]:
            raise OSError("injected rollback removal failure")
        original_remove(path)

    monkeypatch.setattr(module, "_restore_access_sddl", fail_second_restore)
    monkeypatch.setattr(module, "_remove", fail_bridge_removal)

    with pytest.raises(module.DerivativesDailyRollbackError) as error:
        module._promote_atomic(tmp_path, candidates, targets, TARGET)

    assert isinstance(error.value.__cause__, PermissionError)
    assert not targets["source"].exists()
    assert (targets["bridge"] / "value.txt").read_text(encoding="utf-8") == "new-bridge"
    assert (transaction / "backups/bridge/value.txt").read_text(encoding="utf-8") == "old-bridge"
    assert (targets["checkpoint"] / "value.txt").read_text(encoding="utf-8") == "old-checkpoint"
    retained = json.loads(journal.read_text(encoding="utf-8"))
    assert retained["phase"] == "ROLLBACK_FAILED"
    assert retained["transaction"] == "data/staging/derivatives_daily_live/tx"
    assert retained["replaced"] == ["source", "bridge"]
    assert retained["rollback_completed"] == ["source"]
    assert retained["rollback_failures"] == [{
        "name": "bridge",
        "stage": "REMOVE_REPLACEMENT",
        "error_type": "OSError",
    }]

    before = journal.read_bytes()
    with pytest.raises(DerivativesDailyLiveError, match="unfinished live transaction"):
        module._promote_atomic(tmp_path, candidates, targets, TARGET)
    assert journal.read_bytes() == before
    assert (transaction / "backups/bridge/value.txt").read_text(encoding="utf-8") == "old-bridge"


def test_complete_transaction_preserves_staging_when_promotion_rollback_fails(
    tmp_path, monkeypatch,
):
    def materialize_copy(source, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix:
            target.write_text("{}", encoding="utf-8")
        else:
            target.mkdir(exist_ok=True)

    def append_source(root, state, *_args):
        root.mkdir(parents=True, exist_ok=True)
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text("{}", encoding="utf-8")
        return 1

    def build_bridge(**kwargs):
        kwargs["output_bundle_root"].mkdir(parents=True, exist_ok=True)
        kwargs["output_state_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_state_path"].write_text("{}", encoding="utf-8")
        return {"datasets": {
            "kr_kospi200_futures_provider_bridge_daily": {"validation": {"rows": 1}},
            "kr_kospi200_options_provider_bridge_daily": {"validation": {"rows": 1}},
        }}

    def build_derived(**kwargs):
        kwargs["output_root"].mkdir(parents=True, exist_ok=True)
        kwargs["output_state_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_state_path"].write_text("{}", encoding="utf-8")
        return {"validation": {"rows": 1}}

    def build_wall(_root, _options, _target, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("date\n2026-08-19\n", encoding="utf-8")
        return 1

    def fail_promotion(project_root, candidates, _targets, _target, **_kwargs):
        transaction = next(iter(candidates.values())).parents[1]
        backup = transaction / "backups/bridge/value.txt"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text("old-bridge", encoding="utf-8")
        module._atomic_json(
            project_root / "data/state/derivatives_price_daily_live.transaction.json",
            {
                "version": 1,
                "phase": "ROLLBACK_FAILED",
                "transaction": transaction.relative_to(project_root).as_posix(),
            },
        )
        raise module.DerivativesDailyRollbackError("injected rollback failure")

    monkeypatch.setattr(module, "_copy_path", materialize_copy)
    monkeypatch.setattr(module, "_append_source", append_source)
    monkeypatch.setattr(module, "build_kospi200_derivatives_bridge", build_bridge)
    monkeypatch.setattr(module, "build_kospi200_futures_nearest_listed", build_derived)
    monkeypatch.setattr(module, "build_modern_kospi200_option_pcr", build_derived)
    monkeypatch.setattr(module, "_build_wall", build_wall)
    monkeypatch.setattr(module, "_promote_atomic", fail_promotion)
    status_path = tmp_path / "artifacts/attempt.json"
    status_record = {}

    with pytest.raises(module.DerivativesDailyRollbackError, match="injected"):
        module._complete_derivatives_transaction(
            tmp_path,
            target=TARGET,
            frames={
                "kospi200_futures": pd.DataFrame(),
                "kospi200_options": pd.DataFrame(),
            },
            api_calls=0,
            status_path=status_path,
            status_record=status_record,
        )

    transactions = list(
        (tmp_path / "data/staging/derivatives_daily_live").iterdir()
    )
    assert len(transactions) == 1
    assert (transactions[0] / "backups/bridge/value.txt").read_text(
        encoding="utf-8"
    ) == "old-bridge"
    assert json.loads(status_path.read_text(encoding="utf-8"))["error_type"] == (
        "DerivativesDailyRollbackError"
    )


def test_production_readback_failure_rolls_back_every_prior_output(tmp_path, monkeypatch):
    monkeypatch.setenv("PSModulePath", str(tmp_path / "poisoned-pwsh7-modules"))
    transaction = tmp_path / "data/staging/derivatives_daily_live/tx"
    candidates = {}
    targets = {}
    for name in ("source", "bridge", "checkpoint"):
        candidate = transaction / "candidates" / name
        target = tmp_path / "production" / name
        candidate.mkdir(parents=True)
        target.mkdir(parents=True)
        (candidate / "value.txt").write_text(f"new-{name}", encoding="utf-8")
        (target / "value.txt").write_text(f"old-{name}", encoding="utf-8")
        candidates[name] = candidate
        targets[name] = target

    def fail_readback():
        raise PermissionError("injected production read-back failure")

    with pytest.raises(PermissionError, match="production read-back"):
        module._promote_atomic(
            tmp_path, candidates, targets, TARGET,
            validate_readback=fail_readback,
        )
    assert all(
        (targets[name] / "value.txt").read_text(encoding="utf-8") == f"old-{name}"
        for name in targets
    )
    assert not (tmp_path / "data/state/derivatives_price_daily_live.transaction.json").exists()


def test_atomic_promotion_restores_each_preexisting_access_descriptor(
    tmp_path, monkeypatch,
):
    transaction = tmp_path / "data/staging/derivatives_daily_live/tx"
    candidates = {}
    targets = {}
    for name in ("source", "bridge", "checkpoint"):
        candidate = transaction / "candidates" / name
        target = tmp_path / "production" / name
        candidate.mkdir(parents=True)
        target.mkdir(parents=True)
        (candidate / "value.txt").write_text(f"new-{name}", encoding="utf-8")
        (target / "value.txt").write_text(f"old-{name}", encoding="utf-8")
        candidates[name] = candidate
        targets[name] = target
    restored = []
    monkeypatch.setattr(
        module, "_capture_access_sddl", lambda path: f"DACL:{path.name}",
    )
    monkeypatch.setattr(
        module, "_restore_access_sddl",
        lambda path, sddl: restored.append((path.name, sddl)),
    )

    module._promote_atomic(tmp_path, candidates, targets, TARGET)

    assert restored == [
        (name, f"DACL:{name}") for name in ("source", "bridge", "checkpoint")
    ]
    assert all(
        (targets[name] / "value.txt").read_text(encoding="utf-8") == f"new-{name}"
        for name in targets
    )


def test_acl_child_environment_drops_module_path_and_preserves_other_values(
    monkeypatch,
):
    monkeypatch.setenv("PSModulePath", "poisoned-pwsh7-modules")
    monkeypatch.setenv("STOCK_DATA_ENV_SENTINEL", "preserved")
    parent_path = os.environ.get("PATH")

    environment = module._acl_powershell_environment(
        STOCK_DATA_ACL_PATH="bounded-target",
        STOCK_DATA_ACL_SDDL="D:P",
    )

    assert not any(key.casefold() == "psmodulepath" for key in environment)
    assert any(key.casefold() == "psmodulepath" for key in os.environ)
    assert environment["STOCK_DATA_ENV_SENTINEL"] == "preserved"
    assert environment.get("PATH") == parent_path
    assert environment["STOCK_DATA_ACL_PATH"] == "bounded-target"
    assert environment["STOCK_DATA_ACL_SDDL"] == "D:P"


@pytest.mark.skipif(os.name != "nt", reason="Windows access descriptor behavior")
def test_windows_access_descriptor_snapshot_round_trips_without_privilege(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("PSModulePath", str(tmp_path / "poisoned-pwsh7-modules"))
    target = tmp_path / "protected-output"
    target.mkdir()
    result = module.subprocess.run(
        [str(Path(os.environ["SystemRoot"]) / "System32/icacls.exe"),
         str(target), "/inheritancelevel:d"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    captured = module._capture_access_sddl(target)
    assert captured is not None and captured.startswith("D:P")
    module._restore_access_sddl(target, captured)
    assert module._capture_access_sddl(target) == captured


@pytest.mark.parametrize(
    "failure_identity",
    [
        {"error_type": "PermissionError"},
        {
            "error_type": "DerivativesDailyLiveError",
            "failure_phase": "PROMOTING_BRIDGE",
            "error_message": "could not capture target ACL: str",
        },
    ],
)
def test_reviewed_retained_landing_recovery_is_api_zero_and_preserves_failed_attempt(
    tmp_path, monkeypatch, failure_identity,
):
    attempt_path = module._attempt_path(tmp_path, TARGET)
    landing_files = {
        key: str(
            module._revalidation_landing_path(
                tmp_path, key, TARGET, date(2026, 8, 20),
            ).relative_to(tmp_path)
        )
        for key in module.SOURCE_KEYS
    }
    attempt = {
        "version": 1,
        "dataset": "derivatives_price_daily_live",
        "market_date": TARGET.isoformat(),
        "completed_successor_session": "2026-08-20",
        "status": "FAILED_NO_RETRY",
        "max_calls": 2,
        "retry_count": 0,
        "api_calls": 2,
        "calls_started": list(module.SOURCE_KEYS),
        "calls_completed": list(module.SOURCE_KEYS),
        "landing_files": landing_files,
        **failure_identity,
    }
    module._atomic_json(attempt_path, attempt)
    for relative in landing_files.values():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
    before = attempt_path.read_bytes()

    monkeypatch.setattr(
        module, "latest_finalized_session",
        lambda root, now=None: date(2026, 8, 25),
    )
    monkeypatch.setattr(module, "_replay_complete", lambda root, target: False)
    monkeypatch.setattr(module, "_require_next_source_session", lambda root, target: None)
    monkeypatch.setattr(
        module, "_completed_successor_session",
        lambda root, target, now=None: date(2026, 8, 20),
    )
    retained = []

    def load_retained(root, key, target, successor):
        retained.append(key)
        return pd.DataFrame({"date": [TARGET]}), root / landing_files[key]

    monkeypatch.setattr(module, "_retained_exact", load_retained)
    completed = {}

    def complete(root, **kwargs):
        completed.update(kwargs)
        kwargs["status_record"].update({"status": "SUCCEEDED", "rows": {"wall": 1}})
        module._atomic_json(kwargs["status_path"], kwargs["status_record"])
        return {"wall": 1}

    monkeypatch.setattr(module, "_complete_derivatives_transaction", complete)
    monkeypatch.setattr(
        module, "_collect_exact",
        lambda *args, **kwargs: pytest.fail("provider-capable path must not run"),
    )

    result = recover_derivatives_daily_from_retained(
        tmp_path, market_date=TARGET,
        now=datetime.fromisoformat("2026-08-20T19:00:00+09:00"),
    )
    assert result.status == "AFFECTED_DATE_COMPLETE" and result.api_calls == 0
    assert retained == list(module.SOURCE_KEYS)
    assert completed["api_calls"] == 0
    assert attempt_path.read_bytes() == before
    recovery = json.loads(module._recovery_path(tmp_path, TARGET).read_text(encoding="utf-8"))
    assert recovery["mode"] == "RETAINED_LANDING_API_ZERO"
    assert recovery["source_api_calls"] == 2 and recovery["api_calls"] == 0
    assert set(recovery["landing_sha256"]) == set(module.SOURCE_KEYS)


def test_reviewed_acl_recovery_retry_is_api_zero_hash_bound_and_single_use(
    tmp_path, monkeypatch,
) -> None:
    successor = date(2026, 8, 20)
    attempt_path = module._attempt_path(tmp_path, TARGET)
    landing_files = {
        key: str(
            module._revalidation_landing_path(
                tmp_path, key, TARGET, successor,
            ).relative_to(tmp_path)
        )
        for key in module.SOURCE_KEYS
    }
    attempt = {
        "version": 1,
        "dataset": "derivatives_price_daily_live",
        "market_date": TARGET.isoformat(),
        "completed_successor_session": successor.isoformat(),
        "status": "FAILED_NO_RETRY",
        "max_calls": 2,
        "retry_count": 0,
        "api_calls": 2,
        "calls_started": list(module.SOURCE_KEYS),
        "calls_completed": list(module.SOURCE_KEYS),
        "landing_files": landing_files,
        "error_type": "DerivativesDailyLiveError",
        "failure_phase": "PROMOTING_BRIDGE",
        "error_message": "could not capture target ACL: str",
    }
    module._atomic_json(attempt_path, attempt)
    landing_hashes = {}
    for key, relative in landing_files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("utf-8"))
        landing_hashes[key] = module.hashlib.sha256(path.read_bytes()).hexdigest()
    recovery_path = module._recovery_path(tmp_path, TARGET)
    failed_recovery = {
        "version": 1,
        "dataset": "derivatives_price_daily_live",
        "market_date": TARGET.isoformat(),
        "completed_successor_session": successor.isoformat(),
        "status": "FAILED_NO_RETRY",
        "mode": "RETAINED_LANDING_API_ZERO",
        "source_attempt": str(attempt_path.relative_to(tmp_path)),
        "source_attempt_status": "FAILED_NO_RETRY",
        "source_api_calls": 2,
        "api_calls": 0,
        "retry_count": 0,
        "landing_files": landing_files,
        "landing_sha256": landing_hashes,
        "error_type": "DerivativesDailyLiveError",
        "failure_phase": "PROMOTING_BRIDGE",
        "error_message": "could not restore target ACL",
    }
    module._atomic_json(recovery_path, failed_recovery)
    attempt_before = attempt_path.read_bytes()
    recovery_before = recovery_path.read_bytes()

    monkeypatch.setattr(
        module, "latest_finalized_session",
        lambda root, now=None: date(2026, 8, 25),
    )
    monkeypatch.setattr(module, "_replay_complete", lambda root, target: False)
    monkeypatch.setattr(module, "_require_next_source_session", lambda root, target: None)
    monkeypatch.setattr(
        module, "_completed_successor_session",
        lambda root, target, now=None: successor,
    )
    monkeypatch.setattr(
        module,
        "_retained_exact",
        lambda root, key, target, completed: (
            pd.DataFrame({"date": [TARGET]}), root / landing_files[key],
        ),
    )

    def complete(_root, **kwargs):
        kwargs["status_record"].update({"status": "SUCCEEDED", "rows": {"wall": 1}})
        module._atomic_json(kwargs["status_path"], kwargs["status_record"])
        return {"wall": 1}

    monkeypatch.setattr(module, "_complete_derivatives_transaction", complete)
    monkeypatch.setattr(
        module, "_collect_exact",
        lambda *args, **kwargs: pytest.fail("provider-capable path must not run"),
    )

    result = recover_derivatives_daily_from_retained(
        tmp_path,
        market_date=TARGET,
        reviewed_acl_retry=True,
    )
    assert result.status == "AFFECTED_DATE_COMPLETE" and result.api_calls == 0
    assert attempt_path.read_bytes() == attempt_before
    assert recovery_path.read_bytes() == recovery_before
    retry_path = module._recovery_retry_path(tmp_path, TARGET)
    retry = json.loads(retry_path.read_text(encoding="utf-8"))
    assert retry["mode"] == "RETAINED_LANDING_API_ZERO_ACL_RETRY"
    assert retry["recovery_attempt"] == 2 and retry["api_calls"] == 0
    assert retry["source_recovery"] == str(recovery_path.relative_to(tmp_path))
    assert retry["landing_sha256"] == landing_hashes

    with pytest.raises(
        module.DerivativesDailyLiveError,
        match="forbids an unreviewed repeat",
    ):
        recover_derivatives_daily_from_retained(
            tmp_path,
            market_date=TARGET,
            reviewed_acl_retry=True,
        )


def test_retained_landing_recovery_rejects_unreviewed_transaction_failure(
    tmp_path,
) -> None:
    successor = date(2026, 8, 20)
    attempt = {
        "version": 1,
        "dataset": "derivatives_price_daily_live",
        "market_date": TARGET.isoformat(),
        "completed_successor_session": successor.isoformat(),
        "status": "FAILED_NO_RETRY",
        "max_calls": 2,
        "retry_count": 0,
        "api_calls": 2,
        "calls_started": list(module.SOURCE_KEYS),
        "calls_completed": list(module.SOURCE_KEYS),
        "landing_files": {
            key: str(
                module._revalidation_landing_path(
                    tmp_path, key, TARGET, successor,
                ).relative_to(tmp_path)
            )
            for key in module.SOURCE_KEYS
        },
        "error_type": "DerivativesDailyLiveError",
        "failure_phase": "BUILDING_PCR",
        "error_message": "unreviewed failure",
    }
    module._atomic_json(module._attempt_path(tmp_path, TARGET), attempt)

    with pytest.raises(
        module.DerivativesDailyLiveError,
        match="not an exact reviewed ACL failure",
    ):
        module._require_exact_failed_attempt(tmp_path, TARGET, successor)
