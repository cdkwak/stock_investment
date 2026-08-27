from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_data.contracts.kr_equity import KR_EQUITY_PRICE_DAILY
from stock_data.orchestration import kospi200_constituent_breadth as operation
from stock_data.providers.krx_mdc.kospi200_constituents import (
    KOSPI200ConstituentSourceError,
    capture_kospi200_constituents,
    normalize_kospi200_constituent_landing,
    plan_latest_completed_kospi200_request,
)
from stock_data.validation.kospi200_constituent_breadth import validate_index_constituent_daily


def membership(day: str, symbols=("000001", "000002", "000003")) -> pd.DataFrame:
    body = json.dumps({"output": [{"ISU_SRT_CD": symbol, "ISU_ABBRV": symbol} for symbol in symbols]}).encode()
    return normalize_kospi200_constituent_landing(
        body, observation_date=date.fromisoformat(day), captured_at=f"{day}T18:30:00+09:00"
    )


def prices(days=("2026-08-17", "2026-08-18"), symbols=("000001", "000002", "000003")) -> pd.DataFrame:
    records = []
    closes = {"000001": (100, 110, 120), "000002": (100, 90, 80), "000003": (100, 100, 100)}
    for day_index, day in enumerate(days):
        for symbol in symbols:
            close = closes[symbol][day_index]
            records.append({
                "date": day, "market": "KOSPI", "symbol": symbol,
                "open": close, "high": close, "low": close, "close": close,
                "volume": 10, "trading_value": close * 10,
                "source": "fixture", "source_operation": "exact_date",
                "source_date": day,
            })
    return pd.DataFrame(records, columns=KR_EQUITY_PRICE_DAILY.column_names).sort_values(
        list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)


def bulk_prices(
    days=("2026-08-24", "2026-08-25"),
    symbols=tuple(f"{value:06d}" for value in range(1, 201)),
) -> pd.DataFrame:
    records = []
    for day_index, day in enumerate(days):
        for value, symbol in enumerate(symbols, start=1):
            close = 1000 + value + day_index
            records.append({
                "date": day, "market": "KOSPI", "symbol": symbol,
                "open": close, "high": close, "low": close, "close": close,
                "volume": 10, "trading_value": close * 10,
                "source": "fixture", "source_operation": "exact_date",
                "source_date": day,
            })
    return pd.DataFrame(records, columns=KR_EQUITY_PRICE_DAILY.column_names).sort_values(
        list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)


def test_parser_binds_observation_and_effective_date_without_interval_inference() -> None:
    frame = membership("2026-08-18")
    validate_index_constituent_daily(frame)
    assert frame["date"].eq("2026-08-18").all()
    assert frame["observation_date"].eq("2026-08-18").all()
    assert frame["pit_status"].eq("EXACT_DATE_ONLY_NO_INTERVAL_INFERENCE").all()


def test_source_plan_is_one_exact_completed_session_call_without_availability_guess() -> None:
    plan = plan_latest_completed_kospi200_request(
        datetime.fromisoformat("2026-08-20T04:02:00+09:00")
    )
    assert plan.market_date == date(2026, 8, 19)
    assert plan.previous_session_date == date(2026, 8, 18)
    assert dict(plan.parameters) == {"date": "20260819", "ticker": "1028"}
    assert (plan.business_call_limit, plan.retry_count) == (1, 0)
    assert plan.availability_status.startswith("UNVERIFIED")


def test_capture_writes_one_immutable_landing_before_normalization(tmp_path: Path) -> None:
    calls = []
    body = json.dumps({
        "output": [{"ISU_SRT_CD": "000001", "ISU_ABBRV": "sample"}],
    }).encode()

    capture, frame = capture_kospi200_constituents(
        date(2026, 8, 25), run_id="capture-1",
        landing_root=tmp_path / "landing", env_file=tmp_path / ".env",
        captured_at="2026-08-26T13:30:00+00:00",
        body_fetcher=lambda target: calls.append(target) or body,
    )

    assert calls == [date(2026, 8, 25)]
    assert capture.business_calls == 1 and capture.retry_count == 0
    assert capture.path.read_bytes() == body
    assert frame.iloc[0]["source_sha256"] == capture.sha256

    with pytest.raises(KOSPI200ConstituentSourceError, match="immutable Landing"):
        capture_kospi200_constituents(
            date(2026, 8, 25), run_id="capture-1",
            landing_root=tmp_path / "landing", env_file=tmp_path / ".env",
            captured_at="2026-08-26T13:31:00+00:00",
            body_fetcher=lambda _target: pytest.fail("duplicate run reached provider"),
        )


def test_invalid_response_is_retained_in_landing_and_not_normalized(tmp_path: Path) -> None:
    with pytest.raises(KOSPI200ConstituentSourceError, match="missing or empty"):
        capture_kospi200_constituents(
            date(2026, 8, 25), run_id="invalid-1",
            landing_root=tmp_path / "landing", env_file=tmp_path / ".env",
            captured_at="2026-08-26T13:30:00+00:00",
            body_fetcher=lambda _target: b'{"output":[]}',
        )
    assert (tmp_path / "landing/invalid-1/response.json").read_bytes() == b'{"output":[]}'


def test_current_membership_cannot_be_backprojected() -> None:
    with pytest.raises(operation.KOSPI200BreadthOperationError, match="backprojection"):
        operation.build_exact_kospi200_scope(
            membership("2026-08-18"), prices(), market_date="2026-08-17"
        )


def test_partial_exact_scope_is_rejected_without_fabrication() -> None:
    incomplete = prices().loc[lambda frame: ~(
        frame["date"].eq("2026-08-18") & frame["symbol"].eq("000003")
    )].reset_index(drop=True)
    with pytest.raises(operation.KOSPI200BreadthOperationError, match="incomplete"):
        operation.build_exact_kospi200_scope(
            membership("2026-08-18"), incomplete, market_date="2026-08-18"
        )


def test_atomic_scope_success_and_same_date_replay_is_api_zero(tmp_path: Path) -> None:
    result = operation.run_offline_kospi200_scope(
        tmp_path, membership("2026-08-18"), prices(),
        market_date="2026-08-18", run_id="first",
    )
    assert result.status == "SUCCEEDED" and result.api_calls == 0
    assert (result.constituent_rows, result.price_rows, result.breadth_rows) == (3, 3, 1)
    breadth = pd.read_parquet(
        tmp_path / "data/derived/kr_kospi200_breadth_daily/year=2026/data.parquet"
    )
    assert breadth.iloc[0][["advancing", "declining", "unchanged", "total"]].tolist() == [1, 1, 1, 3]
    replay = operation.run_offline_kospi200_scope(
        tmp_path, membership("2026-08-18"), prices(),
        market_date="2026-08-18", run_id="replay",
    )
    assert replay.status == "NOOP_ALREADY_SUCCEEDED" and replay.api_calls == 0


def test_daily_operation_uses_latest_canonical_date_then_replays_before_provider(
    tmp_path: Path, monkeypatch,
) -> None:
    symbols = tuple(f"{value:06d}" for value in range(1, 201))
    target_membership = membership("2026-08-25", symbols)
    monkeypatch.setattr(
        operation, "latest_accepted_canonical_target", lambda _root: date(2026, 8, 25),
    )
    real_read_dataset = operation.read_dataset
    monkeypatch.setattr(
        operation, "read_dataset",
        lambda root, contract, validator: (
            bulk_prices()
            if contract is KR_EQUITY_PRICE_DAILY
            else real_read_dataset(root, contract, validator)
        ),
    )
    capture_calls = []
    monkeypatch.setattr(
        operation, "capture_kospi200_constituents",
        lambda target, **_kwargs: (
            capture_calls.append(target) or SimpleNamespace(business_calls=1, retry_count=0),
            target_membership,
        ),
    )

    result = operation.run_kospi200_constituent_breadth_daily(
        tmp_path, market_date="2026-08-25", run_id="daily-first",
    )
    assert result.status == "SUCCEEDED" and result.api_calls == 1
    assert capture_calls == [date(2026, 8, 25)]
    assert result.constituent_rows == 200 and result.price_rows == 200

    monkeypatch.setattr(
        operation, "capture_kospi200_constituents",
        lambda *_args, **_kwargs: pytest.fail("replay reached provider"),
    )
    replay = operation.run_kospi200_constituent_breadth_daily(
        tmp_path, market_date="2026-08-25", run_id="daily-replay",
    )
    assert replay.status == "NOOP_ALREADY_SUCCEEDED" and replay.api_calls == 0


def test_daily_operation_rejects_nonlatest_canonical_target_before_provider(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        operation, "latest_accepted_canonical_target", lambda _root: date(2026, 8, 25),
    )
    monkeypatch.setattr(
        operation, "capture_kospi200_constituents",
        lambda *_args, **_kwargs: pytest.fail("invalid target reached provider"),
    )
    with pytest.raises(operation.KOSPI200BreadthOperationError, match="latest canonical"):
        operation.run_kospi200_constituent_breadth_daily(
            tmp_path, market_date="2026-08-24", run_id="wrong-target",
        )


def test_checkpoint_failure_restores_all_prior_valid_outputs(tmp_path: Path, monkeypatch) -> None:
    operation.run_offline_kospi200_scope(
        tmp_path, membership("2026-08-18"), prices(),
        market_date="2026-08-18", run_id="prior",
    )
    roots = operation._roots(tmp_path)
    before = {
        name: sorted((str(path.relative_to(root)), path.read_bytes()) for path in root.rglob("data.parquet"))
        for name, root in roots.items()
    }
    real_atomic = operation._atomic_json
    failed = False

    def fail_checkpoint_once(path: Path, payload: object) -> None:
        nonlocal failed
        if path.name == "kr_kospi200_constituent_breadth.json" and not failed:
            failed = True
            raise OSError("injected checkpoint failure")
        real_atomic(path, payload)

    monkeypatch.setattr(operation, "_atomic_json", fail_checkpoint_once)
    with pytest.raises(OSError, match="injected"):
        operation.run_offline_kospi200_scope(
            tmp_path, membership("2026-08-19"),
            prices(("2026-08-18", "2026-08-19")),
            market_date="2026-08-19", run_id="failed",
        )
    after = {
        name: sorted((str(path.relative_to(root)), path.read_bytes()) for path in root.rglob("data.parquet"))
        for name, root in roots.items()
    }
    assert after == before
    checkpoint = json.loads((tmp_path / "data/state/kr_kospi200_constituent_breadth.json").read_text())
    assert checkpoint["market_date"] == "2026-08-18"
