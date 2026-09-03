from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import pytest

from stock_data.contracts.kr_fundamentals import KR_FUNDAMENTALS_QUARTERLY
from stock_data.orchestration.kr_fundamentals_quarterly import (
    FundamentalsRefreshError,
    _write_candidate,
    fundamental_health,
    latest_fundamental_rows,
    prepare_collection,
    promote_checkpoint,
    read_api_key,
    repair_period_end,
)
from stock_data.orchestration.dataset_universe import (
    ConsumerEligibility,
    build_dataset_universe,
)
from stock_data.providers.opendart_fundamentals import OpenDartDailyLimitError


API_KEY = "k" * 40


def test_dataset_universe_registers_both_manual_display_datasets():
    universe = build_dataset_universe({})
    for dataset_id in ("kr_corp_code_map", "kr_fundamentals_quarterly"):
        spec = universe[dataset_id]
        assert spec.automation_enabled is False
        assert spec.display_consumer_eligibility is ConsumerEligibility.ELIGIBLE
        assert spec.predictive_consumer_eligibility is ConsumerEligibility.BLOCKED


class _Response:
    def __init__(self, body: bytes):
        self.content = body
        self.status_code = 200


class _Session:
    def __init__(self, bodies: list[bytes]):
        self.bodies = list(bodies)
        self.calls: list[tuple[str, dict[str, str], int, bool]] = []

    def get(self, url, *, params, timeout, allow_redirects):
        self.calls.append((url, dict(params), timeout, allow_redirects))
        return _Response(self.bodies.pop(0))


def _corp_zip() -> bytes:
    xml = b"""<result><list><corp_code>00126380</corp_code><corp_name>Test</corp_name><corp_eng_name>Test</corp_eng_name><stock_code>005930</stock_code><modify_date>20260901</modify_date></list></result>"""
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr("CORPCODE.xml", xml)
    return result.getvalue()


def _account(report: str, receipt: str, account_id: str, name: str, statement: str, amount: int, cumulative: int | None = None):
    return {
        "rcept_no": receipt, "reprt_code": report, "bsns_year": "2025",
        "corp_code": "00126380", "sj_div": statement, "sj_nm": statement,
        "account_id": account_id, "account_nm": name, "account_detail": "",
        "thstrm_nm": "current", "thstrm_amount": f"{amount:,}",
        "thstrm_add_amount": "" if cumulative is None else f"{cumulative:,}",
        "frmtrm_nm": "prior", "frmtrm_amount": "0", "frmtrm_q_nm": "prior",
        "frmtrm_q_amount": "0", "frmtrm_add_amount": "0",
        "bfefrmtrm_nm": "", "bfefrmtrm_amount": "", "ord": "1", "currency": "KRW",
    }


def _statement(report: str, receipt: str, *, revenue: int, op: int, net: int, cumulative: tuple[int, int, int] | None = None) -> bytes:
    adds = cumulative or (None, None, None)
    rows = [
        _account(report, receipt, "ifrs-full_Revenue", "매출액", "IS", revenue, adds[0]),
        _account(report, receipt, "dart_OperatingIncomeLoss", "영업이익", "IS", op, adds[1]),
        _account(report, receipt, "ifrs-full_ProfitLoss", "당기순이익", "IS", net, adds[2]),
        _account(report, receipt, "ifrs-full_Liabilities", "부채총계", "BS", 150),
        _account(report, receipt, "ifrs-full_Equity", "자본총계", "BS", 100),
    ]
    return json.dumps({"status": "000", "message": "정상", "list": rows}, ensure_ascii=False).encode()


def _no_data() -> bytes:
    return json.dumps({"status": "013", "message": "조회된 데이타가 없습니다."}, ensure_ascii=False).encode()


def _operation_bodies(*, q1_fallback: bool) -> list[bytes]:
    financial = []
    if q1_fallback:
        financial.extend([_no_data(), _statement("11013", "20250515000001", revenue=100, op=10, net=8, cumulative=(100, 10, 8))])
    else:
        financial.append(_statement("11013", "20250515000001", revenue=100, op=10, net=8, cumulative=(100, 10, 8)))
    financial.extend([
        _statement("11012", "20250815000001", revenue=110, op=11, net=9, cumulative=(210, 21, 17)),
        _statement("11014", "20251115000001", revenue=120, op=12, net=10, cumulative=(330, 33, 27)),
        _statement("11011", "20260331000001", revenue=500, op=50, net=40),
    ])
    return financial


def test_live_phase_uses_cfs_then_ofs_fallback_and_accounts_daily_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDART_API_KEY", API_KEY)
    first_session = _Session([_corp_zip(), *_operation_bodies(q1_fallback=True)])
    first = prepare_collection(
        tmp_path, symbols=("005930",), years=(2025,), max_calls=10,
        session=first_session, sleeper=lambda _: None,
        now=datetime(2026, 9, 3, 1, 0, tzinfo=timezone.utc),
    )

    assert first["status"] == "CANDIDATE_REVIEW_REQUIRED"
    assert first["http_calls"] == 6
    assert first["calls_today"] == 6
    assert first["remaining_queries"] == 0
    assert [call[2:] for call in first_session.calls] == [(20, False)] * 6
    assert first_session.calls[1][1]["fs_div"] == "CFS"
    assert first_session.calls[2][1]["fs_div"] == "OFS"
    checkpoint_path = Path(first["checkpoint"])
    run_dir = tmp_path / "data/landing/opendart/kr_fundamentals_quarterly" / checkpoint_path.parent.name
    assert API_KEY not in (run_dir / "call_ledger.jsonl").read_text(encoding="utf-8")

    promoted = promote_checkpoint(
        tmp_path, checkpoint_path, expected_approval_digest=str(first["approval_digest"]),
    )
    assert promoted["status"] == "PROMOTED"
    stored = pd.read_parquet(tmp_path / "data/normalized/kr_fundamentals_quarterly/data.parquet")
    q1 = stored.loc[stored["reprt_code"] == "11013"].iloc[0]
    q4 = stored.loc[stored["reprt_code"] == "11011"].iloc[0]
    assert q1["fs_div"] == "OFS"
    assert q4["revenue"] == 170

    second_session = _Session(_operation_bodies(q1_fallback=False))
    second = prepare_collection(
        tmp_path, symbols=("005930",), years=(2025,), max_calls=5,
        session=second_session, sleeper=lambda _: None,
        now=datetime(2026, 9, 3, 2, 0, tzinfo=timezone.utc),
    )
    assert second["http_calls"] == 4
    assert second["calls_today"] == 10
    assert all("corpCode.xml" not in call[0] for call in second_session.calls)


def test_api_key_compatibility_spelling_and_status_020_checkpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    monkeypatch.setenv("OpenDART_API_KEY", API_KEY)
    assert read_api_key() == API_KEY
    limit = json.dumps({"status": "020", "message": "요청 제한"}, ensure_ascii=False).encode()
    session = _Session([_corp_zip(), limit])

    with pytest.raises(OpenDartDailyLimitError, match="status 020"):
        prepare_collection(
            tmp_path, symbols=("005930",), years=(2025,), max_calls=3,
            session=session, sleeper=lambda _: None,
            now=datetime(2026, 9, 3, 3, 0, tzinfo=timezone.utc),
        )

    checkpoints = list((tmp_path / "data/state/kr_fundamentals_quarterly").glob("*/checkpoint.json"))
    checkpoint = json.loads(checkpoints[0].read_text(encoding="utf-8"))
    assert checkpoint["status"] == "HARD_STOP_DAILY_LIMIT"
    assert checkpoint["http_calls"] == 2
    assert checkpoint["calls_today"] == 2
    ledger = next((tmp_path / "data/landing/opendart/kr_fundamentals_quarterly").glob("*/call_ledger.jsonl"))
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["documented_daily_limit"] == 20_000
    assert records[-1]["calls_today"] == 2
    assert API_KEY not in ledger.read_text(encoding="utf-8")
    blocked_session = _Session([])
    with pytest.raises(FundamentalsRefreshError, match="hard-stopped"):
        prepare_collection(
            tmp_path, symbols=("005930",), years=(2025,), max_calls=3,
            session=blocked_session, sleeper=lambda _: None,
            now=datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc),
        )
    assert blocked_session.calls == []


def test_live_phase_counts_and_drops_future_period_end(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDART_API_KEY", API_KEY)
    future_q1 = json.loads(
        _statement("11013", "20250515000001", revenue=100, op=10, net=8),
    )
    for row in future_q1["list"]:
        row["thstrm_dt"] = "2025.01.01 ~ 2025.06.30"
    session = _Session([
        _corp_zip(), json.dumps(future_q1, ensure_ascii=False).encode(),
        *_operation_bodies(q1_fallback=False)[1:],
    ])

    result = prepare_collection(
        tmp_path, symbols=("005930",), years=(2025,), max_calls=6,
        session=session, sleeper=lambda _: None,
        now=datetime(2026, 9, 3, 1, 0, tzinfo=timezone.utc),
    )

    assert result["new_normalized_rows"] == 3
    assert result["dropped_normalized_rows"] == 1
    assert result["dropped_rows_by_reason"] == {
        "PERIOD_END_AFTER_RECEIPT_DATE": 1,
    }
    checkpoint = json.loads(Path(result["checkpoint"]).read_text(encoding="utf-8"))
    assert checkpoint["dropped_normalized_rows"] == 1
    completed = checkpoint["completed_queries"][0]
    assert completed["normalization"] == "DROPPED"
    assert completed["drop_reason"] == "PERIOD_END_AFTER_RECEIPT_DATE"


def _normalized_row(
    report: str,
    period_end: str,
    revenue: int,
    op: int,
    net: int,
    receipt: str,
    retrieved_at: str,
    *,
    scope: str = "CFS",
    symbol: str = "005930",
    corp_code: str = "00126380",
    bsns_year: int = 2025,
) -> dict[str, object]:
    return {
        "symbol": symbol, "corp_code": corp_code, "bsns_year": bsns_year,
        "reprt_code": report, "fs_div": scope, "period_end": period_end,
        "revenue": revenue, "operating_income": op, "net_income": net,
        "total_liabilities": 150, "total_equity": 100, "debt_ratio_pct": 150.0,
        "rcept_no": receipt, "retrieved_at": retrieved_at,
        "source_terms_ref": "https://opendart.fss.or.kr/intro/terms.do",
    }


def test_health_uses_latest_revision_and_four_discrete_quarters(tmp_path):
    rows = [
        _normalized_row("11013", "2025-03-31", 100, 10, 8, "20250515000001", "2025-05-15T00:00:00Z"),
        _normalized_row("11012", "2025-06-30", 110, -1, 9, "20250815000001", "2025-08-15T00:00:00Z"),
        _normalized_row("11012", "2025-06-30", 110, 11, 9, "20250901000001", "2025-09-01T00:00:00Z"),
        _normalized_row("11014", "2025-09-30", 110, 12, 0, "20251115000001", "2025-11-15T00:00:00Z"),
        _normalized_row("11011", "2025-12-31", 120, 13, 11, "20260331000001", "2026-03-31T00:00:00Z"),
    ]
    frame = pd.DataFrame(rows, columns=KR_FUNDAMENTALS_QUARTERLY.column_names)
    root = tmp_path / "data/normalized/kr_fundamentals_quarterly"
    _write_candidate(frame, root, KR_FUNDAMENTALS_QUARTERLY)

    result = fundamental_health(tmp_path, datetime(2026, 4, 1, tzinfo=timezone.utc))

    assert result.to_dict("records") == [{
        "symbol": "005930",
        "debt_ratio_pct": 150.0,
        "op_income_positive_4q": True,
        "net_income_positive_4q": False,
        "revenue_trend": "INCREASING",
        "fundamentals_as_of": pd.Timestamp("2026-03-31T00:00:00Z"),
    }]
    before_correction = latest_fundamental_rows(
        frame, datetime(2025, 8, 31, tzinfo=timezone.utc),
    )
    assert before_correction.loc[before_correction["reprt_code"] == "11012", "operating_income"].item() == -1


def test_health_refuses_to_mix_cfs_and_ofs_across_four_quarters(tmp_path):
    rows = [
        _normalized_row("11013", "2025-03-31", 100, 10, 8, "20250515000001", "2025-05-15T00:00:00Z", scope="OFS"),
        _normalized_row("11012", "2025-06-30", 110, 11, 9, "20250815000001", "2025-08-15T00:00:00Z"),
        _normalized_row("11014", "2025-09-30", 120, 12, 10, "20251115000001", "2025-11-15T00:00:00Z"),
        _normalized_row("11011", "2025-12-31", 130, 13, 11, "20260331000001", "2026-03-31T00:00:00Z"),
    ]
    _write_candidate(
        pd.DataFrame(rows, columns=KR_FUNDAMENTALS_QUARTERLY.column_names),
        tmp_path / "data/normalized/kr_fundamentals_quarterly",
        KR_FUNDAMENTALS_QUARTERLY,
    )

    result = fundamental_health(tmp_path, datetime(2026, 4, 1, tzinfo=timezone.utc)).iloc[0]

    assert result["op_income_positive_4q"] is None
    assert result["net_income_positive_4q"] is None
    assert result["revenue_trend"] == "UNAVAILABLE"
    assert result["debt_ratio_pct"] == 150.0


def test_repair_period_end_uses_landing_or_removes_unsafe_row_atomically(tmp_path):
    rows = [
        _normalized_row(
            "11014", "2026-09-30", 100, 10, 8,
            "20260515000001", "2026-05-15T00:00:00Z",
            symbol="093240", corp_code="00111111", bsns_year=2026,
        ),
        _normalized_row(
            "11011", "2026-12-31", 100, 10, 8,
            "20260813000001", "2026-08-13T00:00:00Z",
            symbol="417310", corp_code="00222222", bsns_year=2026,
        ),
    ]
    target = tmp_path / "data/normalized/kr_fundamentals_quarterly"
    _write_candidate(
        pd.DataFrame(rows, columns=KR_FUNDAMENTALS_QUARTERLY.column_names),
        target, KR_FUNDAMENTALS_QUARTERLY,
    )
    landing = (
        tmp_path / "data/landing/opendart/kr_fundamentals_quarterly"
        / "20260515T000000Z_00000000000000000000000000000000"
    )
    landing.mkdir(parents=True)
    landing.joinpath("response_0001_financial_statement.json").write_text(
        json.dumps({"status": "000", "message": "정상", "list": [{
            "rcept_no": "20260515000001", "corp_code": "00111111",
            "bsns_year": "2026", "reprt_code": "11014",
            "thstrm_dt": "2025.07.01 ~ 2026.03.31",
        }]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = repair_period_end(tmp_path)

    assert result == {
        "status": "REPAIRED", "rows_before": 2, "rows_after": 1,
        "corrected_rows": 1, "removed_rows": 1,
    }
    stored = pd.read_parquet(target / "data.parquet")
    assert stored[["symbol", "period_end"]].astype(str).to_dict("records") == [{
        "symbol": "093240", "period_end": "2026-03-31",
    }]
    assert not list(target.parent.glob(".kr_fundamentals_quarterly.repair.*"))
