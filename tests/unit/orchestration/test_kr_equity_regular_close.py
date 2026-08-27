from datetime import date

from stock_data.orchestration.automatic_fallback import (
    AttemptFailure, DecisionOutcome, FailureKind,
)
from stock_data.orchestration.current_observation import CurrentObservationFileStore
from stock_data.orchestration.kr_equity_regular_close import (
    RegularCloseQuote, refresh_regular_close,
)


DAY = date(2026, 8, 21)
NOW = "2026-08-21T11:10:00+00:00"


def quote(symbol="005930", day=DAY, close=70000.0):
    return RegularCloseQuote(symbol, day, close, NOW)


def test_pykrx_is_primary_and_fdr_is_not_called(tmp_path):
    calls = []
    result = refresh_regular_close(
        store=CurrentObservationFileStore(tmp_path / "current.json"),
        symbol="005930", expected_date=DAY,
        pykrx_fetch=lambda symbol, day: (calls.append("pykrx") or quote(symbol, day)),
        fdr_fetch=lambda symbol, day: (calls.append("fdr") or quote(symbol, day)),
    )
    assert calls == ["pykrx"]
    assert result.decision.outcome is DecisionOutcome.PRIMARY_ACCEPTED
    assert result.observation.provider == "pykrx"
    assert result.observation.display_only is True
    assert result.observation.pit_safe is False


def test_technical_primary_failure_uses_one_fdr_attempt(tmp_path):
    calls = []
    def primary(_symbol, _day):
        calls.append("pykrx")
        raise TimeoutError
    result = refresh_regular_close(
        store=CurrentObservationFileStore(tmp_path / "current.json"),
        symbol="000660", expected_date=DAY, pykrx_fetch=primary,
        fdr_fetch=lambda symbol, day: (calls.append("fdr") or quote(symbol, day, 260000)),
    )
    assert calls == ["pykrx", "fdr"]
    assert result.api_calls == 2
    assert result.decision.outcome is DecisionOutcome.FALLBACK_ACCEPTED
    assert result.observation.provider == "FinanceDataReader"


def test_wrong_date_fails_closed_without_fdr(tmp_path):
    calls = []
    result = refresh_regular_close(
        store=CurrentObservationFileStore(tmp_path / "current.json"),
        symbol="005930", expected_date=DAY,
        pykrx_fetch=lambda symbol, _day: quote(symbol, date(2026, 8, 20)),
        fdr_fetch=lambda symbol, day: (calls.append("fdr") or quote(symbol, day)),
    )
    assert calls == []
    assert result.observation is None
    assert result.decision.outcome is DecisionOutcome.NUMERIC_FREE_FAIL_CLOSED
    assert result.decision.events[0].failure_kind is FailureKind.AMBIGUOUS_SEMANTICS


def test_invalid_primary_close_does_not_trigger_fdr(tmp_path):
    calls = []
    result = refresh_regular_close(
        store=CurrentObservationFileStore(tmp_path / "current.json"),
        symbol="005930", expected_date=DAY,
        pykrx_fetch=lambda symbol, day: quote(symbol, day, 0),
        fdr_fetch=lambda symbol, day: (calls.append("fdr") or quote(symbol, day)),
    )
    assert calls == []
    assert result.observation is None
    assert result.decision.events[0].failure_kind is FailureKind.SCHEMA_ERROR


def test_prior_valid_value_survives_both_provider_failures(tmp_path):
    store = CurrentObservationFileStore(tmp_path / "current.json")
    refresh_regular_close(
        store=store, symbol="005930", expected_date=DAY,
        pykrx_fetch=lambda symbol, day: quote(symbol, day),
        fdr_fetch=lambda symbol, day: quote(symbol, day),
    )
    def failed(_symbol, _day):
        raise AttemptFailure(FailureKind.HTTP_ERROR, safe_code="HTTP_503", request_count=1)
    result = refresh_regular_close(
        store=store, symbol="005930", expected_date=date(2026, 8, 24),
        pykrx_fetch=failed, fdr_fetch=failed,
    )
    assert result.decision.outcome is DecisionOutcome.PRIOR_VALID_PRESERVED
    assert result.observation.value == 70000.0
