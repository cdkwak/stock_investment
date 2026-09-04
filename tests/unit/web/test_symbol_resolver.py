from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pandas as pd

from stock_web.api.symbol_resolver import resolve_symbol_code


def _root() -> Path:
    root = (
        Path(__file__).parents[3]
        / ".tmp/agents/symbol-lookup-20260904/fixtures"
        / uuid4().hex
    )
    root.mkdir(parents=True)
    return root


def _write(root: Path, relative: str, frame: pd.DataFrame) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def test_resolves_korean_stock() -> None:
    root = _root()
    _write(
        root,
        "data/normalized/kr_equity_master/market=KOSPI/data.parquet",
        pd.DataFrame({
            "symbol": ["005930"], "name": ["삼성전자"], "market": ["KOSPI"],
            "delisting_date": [None], "security_type_name": ["보통주"],
        }),
    )

    assert resolve_symbol_code(root, "005930") == {
        "found": True, "market": "KOSPI", "symbol": "005930", "name": "삼성전자",
        "currency": "KRW", "security_type": "보통주", "source": "kr_equity_master",
    }


def test_resolves_korean_etf_from_latest_universe_snapshot() -> None:
    root = _root()
    columns = {
        "market": ["KRX"], "security_type": ["ETF"],
        "listing_status": ["LISTED_AT_SOURCE_DATE"],
    }
    _write(
        root,
        "data/normalized/kr_etf_universe_daily/source_date=2026-09-03/data.parquet",
        pd.DataFrame({
            "source_date": ["2026-09-03"], "symbol": ["0015B0"],
            "name": ["이전 이름"], **columns,
        }),
    )
    _write(
        root,
        "data/normalized/kr_etf_universe_daily/source_date=2026-09-04/data.parquet",
        pd.DataFrame({
            "source_date": ["2026-09-04"], "symbol": ["0015B0"],
            "name": ["KoAct 미국나스닥성장기업액티브"], **columns,
        }),
    )

    resolved = resolve_symbol_code(root, "0015b0")
    assert resolved == {
        "found": True, "market": "KRX", "symbol": "0015B0",
        "name": "KoAct 미국나스닥성장기업액티브", "currency": "KRW",
        "security_type": "ETF", "source": "kr_etf_universe_daily",
    }


def test_korean_etf_falls_back_to_master_without_a_valid_universe() -> None:
    root = _root()
    _write(
        root,
        "data/normalized/kr_etf_master/market=KRX/data.parquet",
        pd.DataFrame({
            "symbol": ["069500"], "name": ["KODEX 200"], "market": ["KRX"],
            "security_type": ["ETF"], "listing_status": ["LISTED_AT_SOURCE_DATE"],
        }),
    )

    assert resolve_symbol_code(root, "069500") == {
        "found": True, "market": "KRX", "symbol": "069500", "name": "KODEX 200",
        "currency": "KRW", "security_type": "ETF", "source": "kr_etf_master",
    }


def test_resolves_us_etf_case_insensitively() -> None:
    root = _root()
    resolved = resolve_symbol_code(root, "spy")

    assert resolved == {
        "found": True, "market": "US ETF", "symbol": "SPY",
        "name": "SPDR S&P 500 ETF Trust", "currency": "USD",
        "security_type": "ETF", "source": "global_etf_registry",
    }
    assert resolve_symbol_code(root, "koru") == {
        "found": True, "market": "US ETF", "symbol": "KORU",
        "name": "Direxion Daily MSCI South Korea Bull 3X Shares", "currency": "USD",
        "security_type": "ETF", "source": "us_etf_catalog",
    }


def test_unknown_code_is_typed_not_found() -> None:
    assert resolve_symbol_code(_root(), "ZZZZ") == {
        "found": False, "reason": "미등록 코드",
    }
