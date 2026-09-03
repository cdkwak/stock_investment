from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from stock_web.api import account_page, trade_journal
from stock_web.api.trade_journal import build_trade_journal, derive_trade_events
from stock_web.app import create_app
from tests.unit.web import ASGITestClient


KST = timezone(timedelta(hours=9))


def _make_project() -> Path:
    root = Path(__file__).parents[3] / ".tmp/agents/trade-journal-20260903/fixtures" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _position(
    symbol: str, quantity: str, average: str, last: str, *,
    source: str = "toss", currency: str = "KRW", name: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": symbol, "name": name or symbol, "currency": currency,
        "quantity": quantity, "market_value": str(float(quantity) * float(last)),
        "purchase_amount": str(float(quantity) * float(average)),
    }
    if source == "toss":
        row.update({
            "market_country": "KR" if currency == "KRW" else "US",
            "average_purchase_price": average, "last_price": last,
            "commission": "0", "tax": "0", "profit_loss": "0",
        })
    else:
        row.update({
            "average_purchase_price": average, "current_price": last,
            "classification": "stock", "position_key": f"test:{symbol}",
        })
    return row


def _write_snapshot(
    root: Path, source: str, day: str, positions: list[dict[str, object]], cash: float,
    *, suffix: str = "one", hour: int = 7,
) -> Path:
    local_day = date.fromisoformat(day)
    observed = datetime.combine(local_day, time(hour, 0), tzinfo=KST).astimezone(timezone.utc)
    folder = (
        root / "data/landing/tossinvest/account_snapshot"
        if source == "toss" else root / "data/landing/kbsec/account_snapshot"
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{observed.strftime('%Y%m%dT%H%M%SZ')}-{suffix}.json"
    payload: dict[str, object] = {
        "collected_at": observed.isoformat().replace("+00:00", "Z"),
        "positions": positions,
    }
    if source == "toss":
        payload.update({
            "summaries": [{"currency": "KRW", "market_value": "0"}],
            "buying_power": [{"currency": "KRW", "cash_buying_power": str(cash)}],
        })
    else:
        payload.update({
            "cash_balance": str(cash), "total_assets": str(cash),
            "realized_pnl": "0", "purchase_amount": "0",
        })
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_parquet(root: Path, relative: str, frame: pd.DataFrame) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def test_buy_sell_new_gone_and_fractional_rows_are_one_daily_event() -> None:
    root = _make_project()
    days = ["2026-08-29", "2026-08-30", "2026-08-31", "2026-09-01"]
    _write_snapshot(root, "toss", days[0], [], 1_000_000)
    _write_snapshot(root, "toss", days[1], [
        _position("OLDER", "99", "1", "1"),
    ], 1, suffix="older", hour=6)
    _write_snapshot(root, "toss", days[1], [
        _position("FRAC", "0.050000", "10000", "10100"),
        _position("FRAC", "0.073456", "10000", "10100"),
        _position("GONE", "2", "20000", "21000"),
    ], 958_765.44)
    _write_snapshot(root, "toss", days[2], [
        _position("FRAC", "0.623456", "11763.121", "12500"),
        _position("GONE", "2", "20000", "21000"),
    ], 952_765.44)
    _write_snapshot(root, "toss", days[3], [
        _position("FRAC", "0.123456", "11763.121", "13000"),
    ], 1_001_265.44)

    _write_snapshot(root, "kb", days[0], [], 1_000_000)
    _write_snapshot(root, "kb", days[1], [
        _position("KBNEW", "10", "50000", "51000", source="kb"),
    ], 500_000)
    _write_snapshot(root, "kb", days[2], [
        _position("KBNEW", "8", "50000", "60000", source="kb"),
    ], 620_000)
    _write_snapshot(root, "kb", days[3], [], 1_100_000)

    events = derive_trade_events(root)
    by_key = {(row["source"], row["date"], row["symbol"], row["side"]): row for row in events}

    assert not [row for row in events if row["symbol"] == "OLDER"]
    new_fractional = by_key[("toss_self", "2026-08-30", "FRAC", "BUY")]
    assert new_fractional["quantity"] == 0.123456
    assert new_fractional["price"] == 10_000
    assert len([
        row for row in events
        if row["source"] == "toss_self" and row["date"] == "2026-08-30" and row["symbol"] == "FRAC"
    ]) == 1

    increased = by_key[("toss_self", "2026-08-31", "FRAC", "BUY")]
    expected_price = (11763.121 * 0.623456 - 10000 * 0.123456) / 0.5
    assert increased["price"] == pytest.approx(expected_price)
    assert increased["price_basis"] == "average_cost_delta"
    assert increased["snapshot_dates"] == ["2026-08-30", "2026-08-31"]
    assert increased["recurring_like"] is True

    reduced = by_key[("toss_self", "2026-09-01", "FRAC", "SELL")]
    assert reduced["quantity"] == 0.5
    assert reduced["price"] == 13_000
    assert reduced["realized_pnl_est"] == pytest.approx((13_000 - 11763.121) * 0.5)
    gone = by_key[("toss_self", "2026-09-01", "GONE", "SELL")]
    assert gone["quantity"] == 2
    assert gone["price"] == 21_000
    assert "전일" in gone["basis"]

    assert by_key[("kb_self", "2026-08-30", "KBNEW", "BUY")]["price"] == 50_000
    assert by_key[("kb_self", "2026-08-31", "KBNEW", "SELL")]["price"] == 60_000
    assert by_key[("kb_self", "2026-09-01", "KBNEW", "SELL")]["price"] == 60_000


def test_third_buy_in_last_five_snapshot_days_is_recurring_like() -> None:
    root = _make_project()
    _write_snapshot(root, "toss", "2026-08-29", [], 1_000_000)
    _write_snapshot(root, "toss", "2026-08-30", [_position("PLAN", "1", "200000", "200000")], 800_000)
    _write_snapshot(root, "toss", "2026-08-31", [_position("PLAN", "2", "205000", "210000")], 590_000)
    _write_snapshot(root, "toss", "2026-09-01", [_position("PLAN", "3", "210000", "220000")], 370_000)

    buys = [row for row in derive_trade_events(root) if row["symbol"] == "PLAN"]

    assert [row["recurring_like"] for row in buys] == [False, False, True]


def test_dividend_reference_matches_held_shares_and_keeps_residual_estimated() -> None:
    root = _make_project()
    held = [_position("005930", "10", "70000", "71000", name="삼성전자")]
    _write_snapshot(root, "toss", "2026-09-01", held, 10_000)
    _write_snapshot(root, "toss", "2026-09-02", held, 15_000)
    _write_parquet(root, "data/normalized/kr_equity_master/data.parquet", pd.DataFrame({
        "symbol": ["005930"], "isin": ["KR7005930003"],
    }))
    _write_parquet(root, "data/normalized/kr_equity_dividend/data.parquet", pd.DataFrame({
        "isin": ["KR7005930003"], "company": ["삼성전자"],
        "event_type": ["CASH_DIVIDEND"], "dividend_record_date": ["20260801"],
        "cash_payment_date": ["20260902"], "ordinary_dividend_amount": [500.0],
    }))

    payload = build_trade_journal(root, days=36_500)
    dividend = next(row for row in payload["events"] if row["side"] == "DIVIDEND")

    assert dividend["expected_amount"] == 5_000
    assert dividend["observed_cash_residual"] == 5_000
    assert dividend["estimated"] is True
    assert dividend["price_basis"] == "dividend_reference"
    assert payload["summary"]["dividends_est"] == {"KRW": 5_000.0}


def test_registered_deposit_is_not_reported_as_dividend() -> None:
    root = _make_project()
    _write_snapshot(root, "toss", "2026-09-01", [], 10_000)
    _write_snapshot(root, "toss", "2026-09-02", [], 110_000)
    ledger = root / "artifacts/local_user/cash_flows.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({
        "schema_version": 1,
        "entries": [{
            "id": "flow_deposit", "date": "2026-09-02", "amount_krw": 100_000,
            "account": "Toss", "memo": "투자금 입금",
        }],
    }, ensure_ascii=False), encoding="utf-8")

    payload = build_trade_journal(root, days=36_500)

    assert not [row for row in payload["events"] if row["side"] in {"DIVIDEND", "DIVIDEND?"}]
    assert payload["summary"]["dividends_est"] == {}


def test_missing_snapshot_day_is_reported_and_not_interpolated() -> None:
    root = _make_project()
    _write_snapshot(root, "kb", "2026-08-30", [], 100_000)
    _write_snapshot(root, "kb", "2026-09-01", [
        _position("SKIP", "1", "10000", "10000", source="kb"),
    ], 90_000)

    payload = build_trade_journal(root, days=36_500)

    assert not [row for row in payload["events"] if row["symbol"] == "SKIP"]
    gap = next(row for row in payload["gaps"] if row["type"] == "missing_snapshot_days")
    assert gap["missing_dates"] == ["2026-08-31"]
    assert gap["from_date"] == "2026-08-30"
    assert gap["to_date"] == "2026-09-01"


def test_unchanged_snapshot_filename_set_reuses_the_derivation_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _make_project()
    _write_snapshot(root, "toss", "2026-09-01", [], 100_000)
    _write_snapshot(root, "toss", "2026-09-02", [
        _position("CACHE", "1", "10000", "10000"),
    ], 90_000)
    first = derive_trade_events(root)
    cache_path = root / "artifacts/local_user/trade_journal_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))

    def unexpected_reload(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("immutable Landing JSON was reread despite an exact filename-set cache hit")

    monkeypatch.setattr(trade_journal, "_load_daily_snapshots", unexpected_reload)

    assert derive_trade_events(root) == first
    assert len(cache["snapshot_files"]) == 2


def test_manual_post_delete_are_atomic_validated_and_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _make_project()
    client = ASGITestClient(create_app(root))
    manual_path = root / "artifacts/local_user/trade_journal_manual.json"
    real_replace = account_page.os.replace
    replacements: list[tuple[Path, Path]] = []

    def observed_replace(source: object, target: object) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(account_page.os, "replace", observed_replace)
    entry = {
        "date": datetime.now(KST).date().isoformat(), "account_label": "미래에셋",
        "symbol": "005930", "name": "삼성전자", "side": "BUY",
        "quantity": 0.123456, "price": 70_000, "currency": "KRW",
        "memo": "수동 체결",
    }

    assert client.post("/api/trade-journal/manual", json=entry).status_code == 403
    assert not manual_path.exists()
    created = client.post(
        "/api/trade-journal/manual", json=entry, client_host="127.0.0.1",
    )
    assert created.status_code == 200
    manual = next(row for row in created.json()["events"] if row["origin"] == "manual")
    assert manual["estimated"] is False
    assert manual["amount"] == pytest.approx(8_641.92)
    manual_replacement = next(pair for pair in replacements if pair[1] == manual_path)
    assert manual_replacement[0].parent == manual_path.parent
    assert not list(manual_path.parent.glob("*.tmp"))

    invalid = client.post(
        "/api/trade-journal/manual", json={**entry, "quantity": 0},
        client_host="127.0.0.1",
    )
    assert invalid.status_code == 400
    duplicate_api_account = client.post(
        "/api/trade-journal/manual", json={**entry, "account_label": "Toss"},
        client_host="127.0.0.1",
    )
    assert duplicate_api_account.status_code == 400
    assert client.delete(
        "/api/trade-journal/manual", json={"id": manual["id"]},
    ).status_code == 403
    deleted = client.delete(
        "/api/trade-journal/manual", json={"id": manual["id"]}, client_host="::1",
    )
    assert deleted.status_code == 200
    assert not [row for row in deleted.json()["events"] if row["origin"] == "manual"]
    assert json.loads(manual_path.read_text(encoding="utf-8")) == {
        "schema_version": 1, "entries": [],
    }


def test_trade_journal_page_and_days_validation_are_exposed() -> None:
    root = _make_project()
    client = ASGITestClient(create_app(root))

    assert client.get("/api/trade-journal", params={"days": "0"}).status_code == 400
    html = client.get("/account").text
    assert "매매일지 (스냅샷 차이 기반 · 추정)" in html
    assert "모으기/소액" not in html
    assert "trade_journal_manual.json" not in html
