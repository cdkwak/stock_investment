from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from stock_data.orchestration.kbsec_transactions_daily import (
    CASH_FLOWS_PATH,
    LANDING_ROOT,
    MAX_PAGE_CALLS,
    RECEIPT_PATH,
    STATE_PATH,
    merge_cash_flow_ledger,
    request_window,
    run_kbsec_transactions_daily,
)
from stock_data.providers.kbsec.client import KBSecResponse


NOW = datetime(2026, 9, 5, 7, 20, tzinfo=timezone.utc)
ENVIRONMENT = {
    "KBSEC_BASE_URL": "https://kb.example",
    "KBSEC_APP_KEY": "fixture-key",
    "KBSEC_APP_SECRET": "fixture-secret",
}


def _temp_root() -> Path:
    root = (
        Path(__file__).parents[3]
        / ".tmp/agents/kb_transactions_daily_tests_20260905"
        / uuid4().hex
    )
    root.mkdir(parents=True)
    return root


def _row(summary: str, *, code: str, amount: int, sequence: int) -> dict:
    return {
        "dl_dt": "20260904",
        "dl_typ_cd": code,
        "smry_typ_cd": f"{sequence:03d}",
        "smry_nm": summary,
        "dl_amt": f"{amount:,}",
        "incm_tx": "0",
        "rsdnt_tx": "0",
        "dl_sq": str(sequence),
        "cprty_ac_nm": "private fixture name",
    }


def _page(rows: list[dict], next_key: str = "") -> KBSecResponse:
    body = {"grid_cnt1": str(len(rows)), "nxt_key": next_key, "Record1": rows}
    raw = {
        "dataHeader": {"resultCode": "200", "processCode": "0011"},
        "dataBody": body,
    }
    return KBSecResponse("200", "0011", body, raw)


class _Client:
    def __init__(self, pages: dict[str, KBSecResponse]):
        self.pages = pages
        self.calls: list[tuple[date, date, str]] = []

    def transaction_history_page(self, start_date, end_date, *, next_key=""):
        self.calls.append((start_date, end_date, next_key))
        return self.pages[next_key]


def test_dry_run_prints_initial_gap_plan_and_makes_zero_calls() -> None:
    tmp_path = _temp_root()
    factories = []
    result = run_kbsec_transactions_daily(
        tmp_path,
        now=NOW,
        dry_run=True,
        client_factory=lambda **kwargs: factories.append(kwargs),
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["api_calls"] == 0 and factories == []
    assert result["window"] == {
        "start_date": "2025-01-01", "end_date": "2026-09-04", "overlap_days": 7,
    }
    assert result["request"]["first_page_body"]["inq_clsf"] == "1"
    assert result["request"]["max_page_calls"] == 40
    assert not (tmp_path / RECEIPT_PATH).exists()


def test_landing_first_merge_preserves_manual_entries_and_replay_is_api_zero() -> None:
    tmp_path = _temp_root()
    manual = {
        "id": "flow_manual_fixture",
        "date": "2026-08-01",
        "amount_krw": 101,
        "account": "직접 입력",
        "memo": "보존",
    }
    ledger_path = tmp_path / CASH_FLOWS_PATH
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps({"schema_version": 1, "entries": [manual]}, ensure_ascii=False),
        encoding="utf-8",
    )
    client = _Client({
        "": _page([
            _row("오픈뱅킹 입금", code="01", amount=303, sequence=1),
            _row("주식장내매수", code="02", amount=202, sequence=2),
        ], "next-page"),
        "next-page": _page([
            _row("송금 출금", code="02", amount=77, sequence=3),
        ]),
    })

    result = run_kbsec_transactions_daily(
        tmp_path,
        now=NOW,
        environment=ENVIRONMENT,
        confirm_live=True,
        client_factory=lambda **_kwargs: client,
    )

    assert result["status"] == "COMPLETE"
    assert result["api_calls"] == 2
    assert result["rows_observed"] == 3
    assert result["ledger_entries_added"] == 2
    assert len(list((tmp_path / LANDING_ROOT).rglob("page_*.json"))) == 2
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["entries"][0] == manual
    automatic = [entry for entry in ledger["entries"] if entry["account"] == "kb_auto"]
    assert sorted(entry["amount_krw"] for entry in automatic) == [-77, 303]
    assert all(entry["id"].startswith("kb_auto_") for entry in automatic)
    retained = json.loads((tmp_path / STATE_PATH).read_text(encoding="utf-8"))
    assert {row["category"] for row in retained["rows"]} == {
        "DEPOSIT", "WITHDRAWAL", "OTHER",
    }
    assert all("amount_krw" not in row and "tax_krw" not in row for row in retained["rows"])
    receipt_text = (tmp_path / RECEIPT_PATH).read_text(encoding="utf-8")
    assert "fixture-key" not in receipt_text and "fixture-secret" not in receipt_text
    assert "amount_krw" not in receipt_text and "tax_krw" not in receipt_text

    repeated = run_kbsec_transactions_daily(
        tmp_path,
        now=NOW,
        environment=ENVIRONMENT,
        confirm_live=True,
        client_factory=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("same-day replay must not create a client")
        ),
    )
    assert repeated["status"] == "NOOP_DAILY_OCCURRENCE_ALREADY_CLAIMED"
    assert repeated["api_calls"] == 0
    assert json.loads(ledger_path.read_text(encoding="utf-8"))["entries"] == ledger["entries"]


def test_row_hash_merge_is_idempotent_and_other_is_excluded() -> None:
    tmp_path = _temp_root()
    base = {
        "date": "2026-09-04", "direction": "IN", "category": "DEPOSIT",
        "amount_krw": 404, "tax_krw": 0, "summary_name": "이체 입금",
        "transaction_type_code": "01", "summary_type_code": "001",
        "raw_row_sha256": "a" * 64,
    }
    other = {**deepcopy(base), "category": "OTHER", "raw_row_sha256": "b" * 64}

    assert merge_cash_flow_ledger(tmp_path, [base, base, other])[0] == 1
    assert merge_cash_flow_ledger(tmp_path, [base, other])[0] == 0
    ledger = json.loads((tmp_path / CASH_FLOWS_PATH).read_text(encoding="utf-8"))
    assert len(ledger["entries"]) == 1


def test_dividend_cash_flow_is_written_after_tax() -> None:
    tmp_path = _temp_root()
    dividend = {
        "date": "2026-09-04", "direction": "IN", "category": "DIVIDEND",
        "amount_krw": 606, "tax_krw": 66, "summary_name": "현금 배당",
        "transaction_type_code": "01", "summary_type_code": "004",
        "raw_row_sha256": "c" * 64,
    }

    assert merge_cash_flow_ledger(tmp_path, [dividend])[0] == 1
    ledger = json.loads((tmp_path / CASH_FLOWS_PATH).read_text(encoding="utf-8"))
    assert ledger["entries"][0]["amount_krw"] == 540


def test_request_window_keeps_seven_day_overlap_and_older_gap() -> None:
    assert request_window(NOW, last_retained_date="2026-09-03") == (
        date(2026, 8, 29), date(2026, 9, 4),
    )
    assert request_window(NOW, last_retained_date="2026-08-01") == (
        date(2026, 8, 2), date(2026, 9, 4),
    )


def test_nonterminating_pagination_stops_at_40_and_preserves_landing() -> None:
    tmp_path = _temp_root()
    class EndlessClient:
        def __init__(self):
            self.calls = 0

        def transaction_history_page(self, start_date, end_date, *, next_key=""):
            self.calls += 1
            return _page([], f"page-{self.calls}")

    client = EndlessClient()
    result = run_kbsec_transactions_daily(
        tmp_path,
        now=NOW,
        environment=ENVIRONMENT,
        confirm_live=True,
        client_factory=lambda **_kwargs: client,
    )

    assert result["status"] == "PAGE_LIMIT_EXCEEDED_LANDING_PRESERVED"
    assert result["api_calls"] == MAX_PAGE_CALLS == client.calls
    assert len(result["landing_files"]) == MAX_PAGE_CALLS
    assert not (tmp_path / CASH_FLOWS_PATH).exists()
    assert not (tmp_path / STATE_PATH).exists()


def test_schema_error_is_landed_before_validation_and_preserves_targets() -> None:
    tmp_path = _temp_root()
    partial = _row("이체 입금", code="01", amount=505, sequence=1)
    del partial["dl_amt"]
    client = _Client({"": _page([partial])})

    result = run_kbsec_transactions_daily(
        tmp_path,
        now=NOW,
        environment=ENVIRONMENT,
        confirm_live=True,
        client_factory=lambda **_kwargs: client,
    )

    assert result["status"] == "PAGE_ERROR_LANDING_PRESERVED"
    assert result["api_calls"] == 1
    assert len(result["landing_files"]) == 1
    assert (tmp_path / result["landing_files"][0]).is_file()
    assert not (tmp_path / CASH_FLOWS_PATH).exists()
    assert not (tmp_path / STATE_PATH).exists()
