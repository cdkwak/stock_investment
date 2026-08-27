from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path

import pandas as pd
import pytest

import stock_data.orchestration.kospi200_intraday_pilot as pilot_module
from stock_data.contracts.kospi200_intraday_pilot import (
    RAW_BAR_TIME_POLICY,
    RAW_REVISION_POLICY,
)
from stock_data.orchestration.kospi200_intraday_pilot import (
    KOSPI200IntradayCaptureBatch,
    KOSPI200IntradayTransactionError,
    PILOT_DATE,
    PILOT_SYMBOLS,
    execute_offline_kospi200_intraday_pilot,
    plan_kospi200_intraday_pilot,
    recover_offline_kospi200_intraday_pilot,
    validate_retained_pilot_responses,
)
from stock_data.providers.ls_t8412 import (
    LST8412ExactPilotCaptureBuilder,
    LST8412PilotError,
    normalize_retained_t8412_capture,
)


def _body(
    symbol: str,
    *,
    day: str = "20260812",
    times: tuple[str, ...] = ("091500", "093000"),
) -> bytes:
    rows = [
        {
            "date": day,
            "time": provider_time,
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "jdiff_vol": 1_000 + index,
            "value": 10,
            "jongchk": 0,
            "rate": "0.00",
            "sign": "2",
        }
        for index, provider_time in enumerate(times)
    ]
    return json.dumps({
        "rsp_cd": "00000",
        "rsp_msg": "fixture",
        "t8412OutBlock": {
            "shcode": symbol,
            "s_time": "090000",
            "e_time": "153000",
            "dshmin": "10",
            "rec_count": len(rows),
        },
        "t8412OutBlock1": rows,
    }).encode()


def _captured_at() -> datetime:
    return datetime.fromisoformat("2026-08-13T08:00:00+09:00")


def _ready_plan():
    return plan_kospi200_intraday_pilot(
        data_status_route_selected=True,
        active_runbook_reviewed=True,
        bounded_entitlement_attempt_authorized=True,
    )


def _membership() -> pd.DataFrame:
    return pd.DataFrame({
        "date": [PILOT_DATE, PILOT_DATE],
        "observation_date": [PILOT_DATE, PILOT_DATE],
        "index_ticker": ["1028", "1028"],
        "symbol": list(PILOT_SYMBOLS),
    })


def _capture(
    responses: dict[str, bytes] | None = None,
    *,
    oauth_calls: int = 1,
    data_calls: int = 2,
    retries: int = 0,
) -> KOSPI200IntradayCaptureBatch:
    return KOSPI200IntradayCaptureBatch(
        responses=responses or {symbol: _body(symbol) for symbol in PILOT_SYMBOLS},
        captured_at=_captured_at(),
        oauth_calls=oauth_calls,
        data_calls=data_calls,
        retries=retries,
    )


def _projection(root: Path) -> Path:
    return root / (
        "data/raw/ls_t8412_kospi200_constituent_15m_pilot/"
        "year=2026/data.parquet"
    )


def _checkpoint_path(root: Path) -> Path:
    return root / "data/state/ls_t8412_kospi200_constituent_15m_pilot.json"


def _journal_path(root: Path) -> Path:
    return root / (
        "data/state/transactions/"
        "ls_t8412_kospi200_constituent_15m_pilot_20260812.json"
    )


def test_t8412_parser_accepts_historical_native_15m_source_observation() -> None:
    frame = normalize_retained_t8412_capture(
        _body("005930"),
        market_date=PILOT_DATE,
        membership_observation_date=PILOT_DATE,
        expected_symbol="005930",
        captured_at=_captured_at(),
    )
    assert frame["provider_time"].tolist() == ["091500", "093000"]
    assert frame["interval_minutes"].eq(15).all()
    assert frame["symbol"].eq("005930").all()
    assert frame["source_sha256"].nunique() == 1
    assert frame["pit_status"].str.startswith("PIT_BLOCKED").all()
    assert frame["bar_time_policy"].eq(RAW_BAR_TIME_POLICY).all()
    assert frame["revision_policy"].eq(RAW_REVISION_POLICY).all()


@pytest.mark.parametrize(
    ("body", "captured_at", "message"),
    [
        (_body("005930", day="20260813"), _captured_at(), "row date differs"),
        (_body("005930", times=("092000",)), _captured_at(), "native 15m grid"),
        (_body("005930", times=("091500", "091500")), _captured_at(), "duplicate"),
        (_body("005930"), datetime.fromisoformat("2026-08-12T16:00:00+09:00"), "same-day"),
    ],
)
def test_t8412_parser_fails_closed_on_unreviewed_or_unsafe_rows(
    body: bytes, captured_at: datetime, message: str,
) -> None:
    with pytest.raises(LST8412PilotError, match=message):
        normalize_retained_t8412_capture(
            body,
            market_date=PILOT_DATE,
            membership_observation_date=PILOT_DATE,
            expected_symbol="005930",
            captured_at=captured_at,
        )


def test_current_authority_plan_is_pre_network_review_required() -> None:
    plan = plan_kospi200_intraday_pilot()
    assert plan.action == "REVIEW_REQUIRED"
    assert plan.reason == "DATA_STATUS_ROUTE_SELECTION_REQUIRED"
    assert (plan.oauth_calls, plan.data_calls, plan.retries) == (0, 0, 0)


def test_reviewed_plan_has_exact_two_symbol_retry_zero_budget() -> None:
    plan = _ready_plan()
    assert plan.action == "READY"
    assert plan.symbols == ("000660", "005930")
    assert (plan.oauth_calls, plan.data_calls, plan.retries) == (1, 2, 0)
    assert plan.entitlement_verification == "FIRST_IN_BUDGET_T8412_SUCCESS_RESPONSE"
    assert plan.bar_time_policy == RAW_BAR_TIME_POLICY
    assert plan.revision_policy == RAW_REVISION_POLICY


def test_raw_only_policy_does_not_claim_resolved_time_or_revision_semantics() -> None:
    plan = plan_kospi200_intraday_pilot(
        data_status_route_selected=True,
        active_runbook_reviewed=True,
        bounded_entitlement_attempt_authorized=True,
    )
    assert plan.action == "READY"
    assert "UNKNOWN" in plan.bar_time_policy
    assert "UNKNOWN" in plan.revision_policy


def test_entitlement_attempt_is_not_an_extra_provider_call() -> None:
    plan = _ready_plan()
    assert plan.entitlement_verification == "FIRST_IN_BUDGET_T8412_SUCCESS_RESPONSE"
    assert plan.data_calls == len(PILOT_SYMBOLS) == 2


def test_all_symbol_responses_validate_together_without_partial_result() -> None:
    plan = _ready_plan()
    frame = validate_retained_pilot_responses(
        plan,
        membership=_membership(),
        responses={symbol: _body(symbol) for symbol in PILOT_SYMBOLS},
        captured_at=_captured_at(),
    )
    assert len(frame) == 4
    assert tuple(frame["symbol"].drop_duplicates()) == PILOT_SYMBOLS
    with pytest.raises(ValueError, match="all intended"):
        validate_retained_pilot_responses(
            plan,
            membership=_membership(),
            responses={"005930": _body("005930")},
            captured_at=_captured_at(),
        )


def test_same_date_membership_is_required_without_backprojection() -> None:
    membership = _membership().assign(
        date=date(2026, 8, 11), observation_date=date(2026, 8, 11)
    )
    with pytest.raises(ValueError, match="same-date"):
        validate_retained_pilot_responses(
            _ready_plan(),
            membership=membership,
            responses={symbol: _body(symbol) for symbol in PILOT_SYMBOLS},
            captured_at=_captured_at(),
        )


def test_offline_transaction_commits_both_immutable_landings_then_projection_and_checkpoint(
    tmp_path: Path,
) -> None:
    result = execute_offline_kospi200_intraday_pilot(
        _ready_plan(), project_root=tmp_path, membership=_membership(),
        capture_builder=lambda _plan: _capture(),
    )
    assert result == {
        "status": "SUCCEEDED", "oauth_calls": 1, "data_calls": 2,
        "retries": 0, "rows": 4, "landing_responses": 2,
    }
    frame = pd.read_parquet(_projection(tmp_path))
    assert len(frame) == 4
    assert set(frame["symbol"]) == set(PILOT_SYMBOLS)
    checkpoint = json.loads(_checkpoint_path(tmp_path).read_text(encoding="utf-8"))
    record = checkpoint["completed_dates"]["2026-08-12"]
    assert set(record["landing"]) == set(PILOT_SYMBOLS)
    for entry in record["landing"].values():
        assert (tmp_path / entry["path"]).is_file()
    assert json.loads(_journal_path(tmp_path).read_text(encoding="utf-8"))["status"] == "SUCCEEDED"


def test_successful_exact_date_replay_is_pre_builder_api_zero_and_preserves_bytes(
    tmp_path: Path,
) -> None:
    execute_offline_kospi200_intraday_pilot(
        _ready_plan(), project_root=tmp_path, membership=_membership(),
        capture_builder=lambda _plan: _capture(),
    )
    projection_before = _projection(tmp_path).read_bytes()
    checkpoint_before = _checkpoint_path(tmp_path).read_bytes()

    def forbidden_builder(_plan):
        raise AssertionError("replay must stop before the injected builder")

    result = execute_offline_kospi200_intraday_pilot(
        _ready_plan(), project_root=tmp_path, membership=_membership(),
        capture_builder=forbidden_builder,
    )
    assert result == {
        "status": "NOOP_ALREADY_SUCCEEDED", "oauth_calls": 0,
        "data_calls": 0, "retries": 0,
    }
    assert _projection(tmp_path).read_bytes() == projection_before
    assert _checkpoint_path(tmp_path).read_bytes() == checkpoint_before


def test_partial_scope_retains_only_received_landing_and_preserves_state(
    tmp_path: Path,
) -> None:
    partial = {PILOT_SYMBOLS[0]: _body(PILOT_SYMBOLS[0])}
    with pytest.raises(KOSPI200IntradayTransactionError, match="all intended"):
        execute_offline_kospi200_intraday_pilot(
            _ready_plan(), project_root=tmp_path, membership=_membership(),
            capture_builder=lambda _plan: _capture(
                partial, oauth_calls=1, data_calls=1,
            ),
        )
    landing = list((tmp_path / "data/landing").rglob("response.json"))
    assert len(landing) == 1
    assert not _projection(tmp_path).exists()
    assert not _checkpoint_path(tmp_path).exists()
    assert json.loads(_journal_path(tmp_path).read_text(encoding="utf-8"))["status"] == "FAILED"


@pytest.mark.parametrize(
    "capture",
    [
        _capture(oauth_calls=2),
        _capture(data_calls=3),
        _capture(retries=1),
    ],
)
def test_injected_builder_cannot_exceed_exact_call_budget(
    tmp_path: Path, capture: KOSPI200IntradayCaptureBatch,
) -> None:
    with pytest.raises(KOSPI200IntradayTransactionError, match="call budget"):
        execute_offline_kospi200_intraday_pilot(
            _ready_plan(), project_root=tmp_path, membership=_membership(),
            capture_builder=lambda _plan: capture,
        )
    assert not _projection(tmp_path).exists()
    assert not _checkpoint_path(tmp_path).exists()


def test_invalid_second_symbol_retains_both_landings_but_promotes_nothing(
    tmp_path: Path,
) -> None:
    invalid = {
        PILOT_SYMBOLS[0]: _body(PILOT_SYMBOLS[0]),
        PILOT_SYMBOLS[1]: _body(PILOT_SYMBOLS[1], times=("092000",)),
    }
    with pytest.raises(LST8412PilotError, match="native 15m grid"):
        execute_offline_kospi200_intraday_pilot(
            _ready_plan(), project_root=tmp_path, membership=_membership(),
            capture_builder=lambda _plan: _capture(invalid),
        )
    assert len(list((tmp_path / "data/landing").rglob("response.json"))) == 2
    assert not _projection(tmp_path).exists()
    assert not _checkpoint_path(tmp_path).exists()


def test_empty_scope_is_landing_only_and_preserves_prior_state(tmp_path: Path) -> None:
    empty = json.loads(_body(PILOT_SYMBOLS[1]))
    empty["t8412OutBlock"]["rec_count"] = 0
    empty["t8412OutBlock1"] = []
    responses = {
        PILOT_SYMBOLS[0]: _body(PILOT_SYMBOLS[0]),
        PILOT_SYMBOLS[1]: json.dumps(empty).encode(),
    }
    with pytest.raises(LST8412PilotError, match="missing or empty"):
        execute_offline_kospi200_intraday_pilot(
            _ready_plan(), project_root=tmp_path, membership=_membership(),
            capture_builder=lambda _plan: _capture(responses),
        )
    assert len(list((tmp_path / "data/landing").rglob("response.json"))) == 2
    assert not _projection(tmp_path).exists()
    assert not _checkpoint_path(tmp_path).exists()


def test_checkpoint_promotion_exception_rolls_back_projection_and_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_json = pilot_module._atomic_json

    def fail_checkpoint(path: Path, payload) -> None:
        if path == _checkpoint_path(tmp_path):
            raise OSError("fixture checkpoint failure")
        original_atomic_json(path, payload)

    monkeypatch.setattr(pilot_module, "_atomic_json", fail_checkpoint)
    with pytest.raises(OSError, match="checkpoint failure"):
        execute_offline_kospi200_intraday_pilot(
            _ready_plan(), project_root=tmp_path, membership=_membership(),
            capture_builder=lambda _plan: _capture(),
        )
    assert not _projection(tmp_path).exists()
    assert not _checkpoint_path(tmp_path).exists()
    assert json.loads(_journal_path(tmp_path).read_text(encoding="utf-8"))["status"] == "FAILED"


def test_promotion_failure_restores_preexisting_valid_projection_and_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = normalize_retained_t8412_capture(
        _body("005930", day="20260811"),
        market_date=date(2026, 8, 11),
        membership_observation_date=date(2026, 8, 11),
        expected_symbol="005930",
        captured_at=_captured_at(),
    )
    pilot_module._stage_projection(_projection(tmp_path), previous)
    previous_checkpoint = {
        "schema": "ls_t8412_kospi200_constituent_15m_pilot.checkpoint.v1",
        "dataset": "ls_t8412_kospi200_constituent_15m_pilot",
        "completed_dates": {},
    }
    pilot_module._atomic_json(_checkpoint_path(tmp_path), previous_checkpoint)
    projection_before = _projection(tmp_path).read_bytes()
    checkpoint_before = _checkpoint_path(tmp_path).read_bytes()
    original_atomic_json = pilot_module._atomic_json

    def fail_checkpoint(path: Path, payload) -> None:
        if path == _checkpoint_path(tmp_path):
            raise OSError("fixture checkpoint failure")
        original_atomic_json(path, payload)

    monkeypatch.setattr(pilot_module, "_atomic_json", fail_checkpoint)
    with pytest.raises(OSError, match="checkpoint failure"):
        execute_offline_kospi200_intraday_pilot(
            _ready_plan(), project_root=tmp_path, membership=_membership(),
            capture_builder=lambda _plan: _capture(),
        )
    assert _projection(tmp_path).read_bytes() == projection_before
    assert _checkpoint_path(tmp_path).read_bytes() == checkpoint_before


def test_restart_recovers_process_death_after_projection_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_json = pilot_module._atomic_json

    def interrupt_after_replace(path: Path, payload) -> None:
        if path == _journal_path(tmp_path) and payload.get("status") == "PROJECTION_PROMOTED":
            raise KeyboardInterrupt("fixture process death")
        original_atomic_json(path, payload)

    monkeypatch.setattr(pilot_module, "_atomic_json", interrupt_after_replace)
    with pytest.raises(KeyboardInterrupt, match="process death"):
        execute_offline_kospi200_intraday_pilot(
            _ready_plan(), project_root=tmp_path, membership=_membership(),
            capture_builder=lambda _plan: _capture(),
        )
    assert _projection(tmp_path).exists()
    assert json.loads(_journal_path(tmp_path).read_text(encoding="utf-8"))["status"] == "STAGED"

    monkeypatch.setattr(pilot_module, "_atomic_json", original_atomic_json)
    assert recover_offline_kospi200_intraday_pilot(tmp_path) == "RECOVERED"
    assert not _projection(tmp_path).exists()
    assert not _checkpoint_path(tmp_path).exists()
    assert json.loads(_journal_path(tmp_path).read_text(encoding="utf-8"))["status"] == "RECOVERED"


class _LiveResponse:
    def __init__(self, body: bytes, status_code: int = 200) -> None:
        self.content = body
        self.status_code = status_code

    def json(self):
        return json.loads(self.content)


class _LiveSession:
    def __init__(self, responses: list[_LiveResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_live_capture_builder_uses_exact_budget_and_request_scope() -> None:
    session = _LiveSession([
        _LiveResponse(json.dumps({"access_token": "fixture-token"}).encode()),
        _LiveResponse(_body(PILOT_SYMBOLS[0])),
        _LiveResponse(_body(PILOT_SYMBOLS[1])),
    ])
    sleeps: list[float] = []
    builder = LST8412ExactPilotCaptureBuilder(
        app_key="fixture-key", app_secret="fixture-secret",
        session=session, sleep=sleeps.append,
    )

    capture = builder(_ready_plan())

    assert (capture.oauth_calls, capture.data_calls, capture.retries) == (1, 2, 0)
    assert tuple(capture.responses) == PILOT_SYMBOLS
    assert sleeps == [1.0]
    assert session.calls[0][1]["params"] == {
        "grant_type": "client_credentials", "appkey": "fixture-key",
        "appsecretkey": "fixture-secret", "scope": "oob",
    }
    for call, symbol in zip(session.calls[1:], PILOT_SYMBOLS, strict=True):
        url, kwargs = call
        assert url == "https://openapi.ls-sec.co.kr:8080/stock/chart"
        assert kwargs["headers"]["tr_cd"] == "t8412"
        assert kwargs["headers"]["tr_cont"] == "N"
        assert kwargs["json"] == {"t8412InBlock": {
            "shcode": symbol, "ncnt": 15, "qrycnt": 500, "nday": "1",
            "sdate": "20260812", "stime": "", "edate": "20260812",
            "etime": "", "cts_date": "", "cts_time": "", "comp_yn": "N",
        }}


def test_live_capture_builder_stops_after_failed_entitlement_call() -> None:
    failed = json.dumps({"rsp_cd": "IGW00000", "rsp_msg": "not entitled"}).encode()
    session = _LiveSession([
        _LiveResponse(json.dumps({"access_token": "fixture-token"}).encode()),
        _LiveResponse(failed),
    ])
    builder = LST8412ExactPilotCaptureBuilder(
        app_key="fixture-key", app_secret="fixture-secret",
        session=session, sleep=lambda _: None,
    )

    capture = builder(_ready_plan())

    assert (capture.oauth_calls, capture.data_calls, capture.retries) == (1, 1, 0)
    assert capture.responses == {PILOT_SYMBOLS[0]: failed}
    assert len(session.calls) == 2
