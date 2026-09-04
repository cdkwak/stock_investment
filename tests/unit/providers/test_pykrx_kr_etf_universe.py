from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from stock_data.contracts.kr_etf import KR_ETF_UNIVERSE_DAILY
from stock_data.orchestration.kr_etf_universe_daily import (
    KrEtfUniverseDailyError,
    run_kr_etf_universe_daily,
    validate_kr_etf_universe_daily,
)
from stock_data.providers.pykrx.kr_etf_universe import (
    KrEtfUniverseProviderError,
    PykrxKrEtfUniverseClient,
)
from stock_data.providers.pykrx.safety import PykrxRequestPolicy
from stock_data.storage.contract_parquet import read_dataset


def _project_root() -> Path:
    root = (
        Path(__file__).parents[3]
        / ".tmp/agents/trade_journal_etf_20260904/fixtures"
        / uuid4().hex
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _source_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "ISU_CD": ["KR70015B0001", "KR7123320000"],
        "ISU_SRT_CD": ["0015B0", "123320"],
        "ISU_NM": [
            "삼성 KoAct 미국나스닥성장기업액티브증권상장지수투자신탁",
            "미래에셋 TIGER 레버리지증권상장지수투자신탁",
        ],
        "ISU_ABBRV": ["KoAct 미국나스닥성장기업액티브", "TIGER 레버리지"],
        "ISU_ENG_NM": ["KoAct US Nasdaq Growth Active ETF", "TIGER Leverage ETF"],
        "LIST_DD": ["2025/02/25", "2010/04/06"],
        "ETF_OBJ_IDX_NM": ["NASDAQ US Growth Companies", "코스피 200"],
        "IDX_CALC_INST_NM1": ["NASDAQ", "KRX"],
    })


class _Operation:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self.calls = 0

    def fetch(self) -> pd.DataFrame:
        self.calls += 1
        return self.frame


class _Provider:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame
        self._request_count = 0

    @property
    def request_count(self) -> int:
        return self._request_count

    def fetch(self) -> pd.DataFrame:
        self._request_count += 1
        return self.frame.copy(deep=True)


def test_pykrx_universe_client_makes_exactly_one_injected_fetch() -> None:
    operation = _Operation(_source_frame())
    policy = PykrxRequestPolicy(min_interval_seconds=0, max_consecutive_requests=1)
    client = PykrxKrEtfUniverseClient(operation=operation, policy=policy)

    observed = client.fetch()

    assert operation.calls == client.request_count == 1
    pd.testing.assert_frame_equal(observed, _source_frame())
    with pytest.raises(KrEtfUniverseProviderError, match="request limit"):
        client.fetch()


def test_universe_collection_is_landing_first_normalized_and_date_idempotent() -> None:
    root = _project_root()
    source_date = date(2026, 9, 4)
    provider = _Provider(_source_frame())

    first = run_kr_etf_universe_daily(
        root, source_date=source_date, provider=provider,
    )

    assert first["status"] == "SUCCEEDED"
    assert first["api_calls"] == provider.request_count == 1
    assert first["rows"] == 2
    landing = root / "data/landing/pykrx/kr_etf_universe_daily/source_date=2026-09-04/response.json"
    assert landing.is_file()
    normalized_root = root / "data/normalized/kr_etf_universe_daily"
    assert list(normalized_root.rglob("source_date=2026-09-04/data.parquet"))
    normalized = read_dataset(
        normalized_root, KR_ETF_UNIVERSE_DAILY, validate_kr_etf_universe_daily,
    )
    koact = normalized.loc[normalized["symbol"].eq("0015B0")].iloc[0]
    assert koact["name"] == "KoAct 미국나스닥성장기업액티브"
    assert koact["full_name"].startswith("삼성 KoAct")
    assert koact["isin"] == "KR70015B0001"
    assert str(koact["listing_date"]) == "2025-02-25"
    assert koact["underlying_index"] == "NASDAQ US Growth Companies"
    assert koact["market"] == "KRX" and koact["security_type"] == "ETF"

    replay = run_kr_etf_universe_daily(root, source_date=source_date)
    assert replay == {
        "schema_version": 1,
        "dataset": "kr_etf_universe_daily",
        "status": "ALREADY_CURRENT",
        "source_date": "2026-09-04",
        "rows": 2,
        "api_calls": 0,
        "retry_count": 0,
        "normalized_write": False,
    }


def test_universe_collection_fails_closed_before_normalized_write() -> None:
    root = _project_root()
    malformed = _source_frame()
    malformed.loc[1, "ISU_SRT_CD"] = "0015B0"

    with pytest.raises((KrEtfUniverseDailyError, ValueError), match="duplicate"):
        run_kr_etf_universe_daily(
            root, source_date=date(2026, 9, 4), provider=_Provider(malformed),
        )

    assert not (root / "data/normalized/kr_etf_universe_daily").exists()


def test_optional_krx_identity_columns_remain_nullable() -> None:
    root = _project_root()
    provider = _Provider(pd.DataFrame({
        "ISU_SRT_CD": ["0015B0"],
        "ISU_ABBRV": ["KoAct 미국나스닥성장기업액티브"],
    }))
    run_kr_etf_universe_daily(
        root, source_date=date(2026, 9, 4), provider=provider,
    )
    frame = read_dataset(
        root / "data/normalized/kr_etf_universe_daily",
        KR_ETF_UNIVERSE_DAILY,
        validate_kr_etf_universe_daily,
    )
    assert frame.loc[0, ["full_name", "isin", "listing_date", "underlying_index"]].isna().all()
