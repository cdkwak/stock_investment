from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from stock_data.orchestration.kr_etf_daily import _manual_account_etf_symbols


def _universe(root: Path, rows: list[tuple[str, str]]) -> None:
    folder = root / "data/normalized/kr_etf_universe_daily/year=2026"
    folder.mkdir(parents=True)
    pd.DataFrame({"symbol": [r[0] for r in rows], "security_type": [r[1] for r in rows]}).to_parquet(
        folder / "data.parquet", index=False,
    )


def _accounts(root: Path, name: str, tickers: list[str]) -> None:
    path = root / "artifacts/local_user" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "accounts": [{"source_id": "m1", "positions": [{"ticker": t, "name": t} for t in tickers]}],
    }), encoding="utf-8")


def test_manual_account_etfs_join_the_lane_only_when_the_universe_knows_them(tmp_path) -> None:
    _universe(tmp_path, [("0015B0", "ETF"), ("139260", "ETF"), ("005930", "보통주")])
    _accounts(tmp_path, "manual_accounts.json", ["0015B0", "005930", "ZZZZZZ"])
    _accounts(tmp_path, "manual_accounts_web.json", ["139260", "AAPL"])

    assert _manual_account_etf_symbols(tmp_path) == {"0015B0", "139260"}


def test_manual_account_symbols_are_empty_without_stores_or_universe(tmp_path) -> None:
    assert _manual_account_etf_symbols(tmp_path) == set()
    _accounts(tmp_path, "manual_accounts.json", ["0015B0"])
    assert _manual_account_etf_symbols(tmp_path) == set()
