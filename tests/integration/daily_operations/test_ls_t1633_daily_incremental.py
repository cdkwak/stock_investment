from datetime import date, datetime, timezone
import json

import pandas as pd
import pytest

from stock_data.contracts.ls_t1633 import LS_T1633_PROGRAM_TRADING_DAILY
from stock_data.orchestration.ls_t1633_daily_incremental import (
    LST1633DailyIncrementalError,
    execute_ls_t1633_daily,
    latest_t_plus_one_market_date,
    plan_ls_t1633_daily,
    run_ls_t1633_daily,
)
from stock_data.providers.ls_t1633 import LST1633DailyCandidateBuilder
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.ls_t1633 import (
    normalize_ls_t1633_market_pair,
    validate_ls_t1633_program_trading,
)


TARGET = date(2026, 8, 19)


def _source_row(day: str = "20260819") -> dict[str, str]:
    return {
        "date": day,
        "tot1": "100", "tot2": "80", "tot3": "20",
        "cha1": "30", "cha2": "35", "cha3": "-5",
        "bcha1": "70", "bcha2": "45", "bcha3": "25",
    }


def _candidate(markets=("KOSPI", "KOSDAQ"), day: date = TARGET) -> pd.DataFrame:
    frames = []
    source_day = day.strftime("%Y%m%d")
    for index, market in enumerate(markets):
        frames.append(normalize_ls_t1633_market_pair(
            amount_row=_source_row(source_day), quantity_row=_source_row(source_day), market=market,
            collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
            amount_landing_sha256=("a" if index == 0 else "c") * 64,
            quantity_landing_sha256=("b" if index == 0 else "d") * 64,
        ))
    return pd.concat(frames, ignore_index=True).sort_values(
        list(LS_T1633_PROGRAM_TRADING_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)


def _plan(root, **overrides):
    values = {
        "project_root": root, "market_date": TARGET,
        "latest_finalized_market_date": TARGET,
        "accepted_market_dates": (TARGET,),
        "source_operation_reviewed": True, "source_finality_reviewed": True,
    }
    values.update(overrides)
    return plan_ls_t1633_daily(**values)


def test_plan_fails_closed_on_current_source_and_finality_gates(tmp_path) -> None:
    operation = _plan(tmp_path, source_operation_reviewed=False)
    finality = _plan(tmp_path, source_finality_reviewed=False)
    assert (operation.action, operation.reason) == (
        "BLOCKED", "ACTIVE_EXACT_DATE_OPERATION_REVIEW_REQUIRED",
    )
    assert (finality.action, finality.reason) == (
        "BLOCKED", "PUBLICATION_AND_REVISION_FINALITY_REQUIRED",
    )
    called = []
    with pytest.raises(LST1633DailyIncrementalError):
        execute_ls_t1633_daily(operation, project_root=tmp_path, candidate_builder=lambda _: called.append(1))
    assert called == []


def test_two_markets_promote_together_and_replay_without_builder(tmp_path) -> None:
    result = execute_ls_t1633_daily(
        _plan(tmp_path), project_root=tmp_path, candidate_builder=lambda _: _candidate(),
    )
    assert result == {"status": "COMPLETE", "business_calls": 4, "promoted_rows": 2}
    restored = read_dataset(
        tmp_path / "data/normalized/ls_t1633_program_trading_daily",
        LS_T1633_PROGRAM_TRADING_DAILY, validate_ls_t1633_program_trading,
    )
    assert set(restored["market"].astype(str)) == {"KOSPI", "KOSDAQ"}
    checkpoint = json.loads(
        (tmp_path / "data/state/ls_t1633_program_trading_daily.json").read_text()
    )
    assert checkpoint["completed_dates"] == [TARGET.isoformat()]
    replay_plan = _plan(tmp_path)
    assert replay_plan.action == "NOOP_IDEMPOTENT"
    replay = execute_ls_t1633_daily(
        replay_plan, project_root=tmp_path,
        candidate_builder=lambda _: pytest.fail("replay reached builder"),
    )
    assert replay["business_calls"] == 0


def test_missing_second_market_changes_no_production_or_checkpoint(tmp_path) -> None:
    normalized = tmp_path / "data/normalized/ls_t1633_program_trading_daily"
    normalized.mkdir(parents=True)
    marker = normalized / "retained.txt"
    marker.write_text("valid-before", encoding="utf-8")
    checkpoint = tmp_path / "data/state/ls_t1633_program_trading_daily.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(json.dumps({
        "dataset": LS_T1633_PROGRAM_TRADING_DAILY.name,
        "completed_dates": ["2026-08-18"],
    }), encoding="utf-8")
    before = checkpoint.read_bytes()
    with pytest.raises(ValueError, match="KOSPI and KOSDAQ"):
        execute_ls_t1633_daily(
            _plan(tmp_path), project_root=tmp_path,
            candidate_builder=lambda _: _candidate(("KOSPI",)),
        )
    assert marker.read_text(encoding="utf-8") == "valid-before"
    assert checkpoint.read_bytes() == before


def test_restart_recovers_interrupted_joint_promotion_before_new_build(tmp_path) -> None:
    token = TARGET.strftime("%Y%m%d")
    transaction = tmp_path / f"data/staging/ls_t1633_program_trading_daily/{token}"
    previous = transaction / "previous_normalized"
    write_dataset_atomic(
        _candidate(day=date(2026, 8, 18)), previous,
        LS_T1633_PROGRAM_TRADING_DAILY, validate_ls_t1633_program_trading,
    )
    (previous / "retained.txt").write_text("valid-before", encoding="utf-8")
    normalized = tmp_path / "data/normalized/ls_t1633_program_trading_daily"
    normalized.mkdir(parents=True)
    (normalized / "partial.txt").write_text("KOSPI-only", encoding="utf-8")
    state = tmp_path / "data/state"
    state.mkdir(parents=True)
    journal = state / f"transactions/ls_t1633_program_trading_daily_{token}.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({
        "dataset": LS_T1633_PROGRAM_TRADING_DAILY.name,
        "market_date": TARGET.isoformat(),
        "normalized_existed": True, "checkpoint_existed": False,
        "status": "NORMALIZED_PROMOTED",
    }), encoding="utf-8")
    plan = _plan(tmp_path)
    assert plan.action == "READY"
    assert (normalized / "retained.txt").read_text(encoding="utf-8") == "valid-before"
    result = execute_ls_t1633_daily(
        plan, project_root=tmp_path, candidate_builder=lambda _: _candidate(),
    )
    assert result["status"] == "COMPLETE"
    assert not (normalized / "partial.txt").exists()


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = json.dumps(payload, sort_keys=True).encode()
        self.headers = {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, *, fail_at=None, failure_status=200):
        self.calls = []
        self.fail_at = fail_at
        self.failure_status = failure_status

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/oauth2/token"):
            return _Response({"access_token": "ephemeral-token"})
        data_call = len(self.calls) - 1
        if self.fail_at == data_call:
            return _Response(
                {"rsp_cd": "99999", "rsp_msg": "failed"},
                status_code=self.failure_status,
            )
        return _Response({"rsp_cd": "00000", "t1633OutBlock1": [_source_row()]})


def test_live_builder_uses_four_exact_scopes_and_retains_landing(tmp_path) -> None:
    session = _Session()
    builder = LST1633DailyCandidateBuilder(
        project_root=tmp_path,
        app_key="unit-app-key",
        app_secret="unit-app-secret",
        base_url="https://openapi.ls-sec.co.kr:8080",
        session=session,
    )
    frame = builder(TARGET)
    assert set(frame["market"]) == {"KOSPI", "KOSDAQ"}
    assert (builder.oauth_calls, builder.data_calls, builder.retry_count) == (1, 4, 0)
    blocks = [call[1]["json"]["t1633InBlock"] for call in session.calls[1:]]
    assert {(item["gubun"], item["gubun1"]) for item in blocks} == {
        ("0", "0"), ("0", "1"), ("1", "0"), ("1", "1"),
    }
    assert all(item["fdate"] == item["tdate"] == "20260819" for item in blocks)
    assert len(list(builder.run_dir.glob("*.response.json"))) == 4
    retained = b"".join(path.read_bytes() for path in builder.run_dir.glob("*"))
    assert b"unit-app-key" not in retained
    assert b"unit-app-secret" not in retained
    assert b"ephemeral-token" not in retained


def test_live_builder_retries_one_transient_failure_and_retains_redacted_provenance(tmp_path) -> None:
    session = _Session(fail_at=2, failure_status=500)
    builder = LST1633DailyCandidateBuilder(
        project_root=tmp_path,
        app_key="unit-app-key",
        app_secret="unit-app-secret",
        base_url="https://openapi.ls-sec.co.kr:8080",
        session=session,
    )
    frame = builder(TARGET)
    assert set(frame["market"]) == {"KOSPI", "KOSDAQ"}
    assert (builder.oauth_calls, builder.data_calls, builder.retry_count) == (1, 5, 1)
    failure = json.loads(next(builder.run_dir.glob("*.failure.provenance.json")).read_text())
    assert failure["market"] == "KOSPI"
    assert failure["measure"] == "QUANTITY"
    assert failure["http_status"] == 500
    assert failure["raw_response_persisted"] is False
    assert failure["credentials_persisted"] is False
    assert failure["token_persisted"] is False
    assert len(session.calls) == 6


def test_t_plus_one_runner_replays_before_credentials_or_provider(tmp_path) -> None:
    calendar = tmp_path / "data/normalized/kr_kospi200_index_daily/year=2026"
    calendar.mkdir(parents=True)
    pd.DataFrame({"date": pd.to_datetime(["2026-08-18", "2026-08-19", "2026-08-20"])}).to_parquet(
        calendar / "data.parquet", index=False,
    )
    now = datetime(2026, 8, 20, 9, 10, tzinfo=timezone.utc)
    assert latest_t_plus_one_market_date(tmp_path, now=now) == TARGET
    factory_calls = []
    result = run_ls_t1633_daily(
        tmp_path,
        market_date=TARGET,
        now=now,
        candidate_builder_factory=lambda root: factory_calls.append(root) or (lambda _: _candidate()),
    )
    assert result["status"] == "COMPLETE"
    replay = run_ls_t1633_daily(
        tmp_path,
        market_date=TARGET,
        now=now,
        candidate_builder_factory=lambda _: pytest.fail("replay loaded provider"),
    )
    assert replay == {"status": "NOOP_IDEMPOTENT", "business_calls": 0, "promoted_rows": 0}
    assert factory_calls == [tmp_path]
