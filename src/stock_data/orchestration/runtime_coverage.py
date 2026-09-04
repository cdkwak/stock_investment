from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Callable

import pandas as pd

from stock_data.contracts.data_v1 import (
    KR_CREDIT_BALANCE_DAILY,
    KR_MARKET_LIQUIDITY_DAILY,
    KR_STOCK_LENDING_DAILY,
    KR_STOCK_LENDING_MARKET_DAILY,
    KR_STOCK_LENDING_PARTICIPANT_DAILY,
)
from stock_data.contracts.bok_ecos_treasury import (
    BOK_ECOS_KR_TREASURY_YIELD_SOURCE_OBSERVATION,
)
from stock_data.contracts.bok_ecos_fx import BOK_ECOS_USD_KRW_DAILY
from stock_data.orchestration.bok_ecos_fx_daily import validate_bok_fx
from stock_data.contracts.kr_derivatives import (
    KR_KOSPI200_FUTURES_DAILY,
    KR_KOSPI200_OPTIONS_DAILY,
)
from stock_data.contracts.kospi200_derivatives_bridge import (
    KR_KOSPI200_FUTURES_PROVIDER_BRIDGE_DAILY,
    KR_KOSPI200_OPTIONS_PROVIDER_BRIDGE_DAILY,
)
from stock_data.contracts.kospi200_futures_basis import (
    KR_KOSPI200_FUTURES_NEAREST_LISTED_DAILY,
)
from stock_data.contracts.kospi200_option_walls import (
    KR_KOSPI200_OPTION_WALLS_DAILY,
)
from stock_data.contracts.legacy_kospi200 import KR_KOSPI200_OPTION_PCR_DAILY
from stock_data.contracts.global_etf import GLOBAL_ETF_PRICE_DAILY
from stock_data.contracts.global_market import (
    FRED_TREASURY_YIELD_DAILY,
    FRED_USD_FX_DAILY,
    FRED_VIX_DAILY,
    GLOBAL_COMMODITY_FUTURES_DAILY,
    GLOBAL_INDEX_PRICE_DAILY,
    US_TREASURY_SPREAD_DAILY,
)
from stock_data.contracts.investor_bridge import (
    KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
)
from stock_data.contracts.kospi200_index_daily import KR_KOSPI200_INDEX_DAILY
from stock_data.contracts.kospi200_constituent_breadth import (
    KR_INDEX_CONSTITUENT_DAILY,
    KR_KOSPI200_BREADTH_DAILY,
    KR_KOSPI200_CONSTITUENT_PRICE_DAILY,
)
from stock_data.contracts.kr_index_fundamental_daily import (
    KR_INDEX_FUNDAMENTAL_DAILY,
)
from stock_data.contracts.kr_equity import (
    KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
    KR_EQUITY_MARKET_CAP_DAILY,
    KR_EQUITY_PRICE_DAILY,
    KR_EQUITY_UNIVERSE_DAILY,
)
from stock_data.contracts.kr_equity_provisional import (
    KR_EQUITY_PRICE_PROVISIONAL_DAILY,
    validate_kr_equity_price_provisional_daily,
)
from stock_data.contracts.kr_etf import KR_ETF_MASTER, KR_ETF_PRICE_DAILY
from stock_data.contracts.kr_fundamentals import (
    KR_CORP_CODE_MAP,
    KR_FUNDAMENTALS_QUARTERLY,
)
from stock_data.contracts.kr_market import KR_MARKET_BREADTH_DAILY
from stock_data.contracts.kr_short_selling import (
    KR_SHORT_SELLING_BALANCE_DAILY,
    KR_SHORT_SELLING_INVESTOR_DAILY,
    KR_SHORT_SELLING_TRADING_DAILY,
)
from stock_data.contracts.market_60m import MARKET_PRICE_60M_OBSERVATION
from stock_data.contracts.market_15m import MARKET_PRICE_15M_OBSERVATION
from stock_data.contracts.tossinvest_historical import (
    KR_MARKET_INVESTOR_TRADING_DAILY,
    KR_TREASURY_YIELD_DAILY,
)
from stock_data.contracts.vkospi_daily import KR_VKOSPI_DAILY
from stock_data.published.canonical_equity_universe import validate_canonical_universe
from stock_data.storage.atomic_parquet import read_kr_index_daily
from stock_data.storage.contract_parquet import read_dataset
from stock_data.storage.contract_arrow import (
    dataframe_to_contract_table,
    restore_contract_dates,
)
from stock_data.derived.kospi200_futures_basis import validate as validate_futures_basis
from stock_data.derived.kospi200_option_pcr_modern import validate_modern_pcr
from stock_data.published.kospi200_derivatives_bridge import (
    FUTURES_SCHEMA as DERIVATIVES_FUTURES_BRIDGE_SCHEMA,
    OPTIONS_SCHEMA as DERIVATIVES_OPTIONS_BRIDGE_SCHEMA,
    validate_bridge as validate_derivatives_bridge,
)
from stock_data.validation.global_market import (
    validate_fred,
    validate_global_commodity_futures,
    validate_global_etf,
    validate_global_index,
)
from stock_data.validation.investor_bridge import validate_investor_bridge
from stock_data.validation.kospi200_index_daily import validate_kospi200_index_daily
from stock_data.validation.kospi200_constituent_breadth import (
    validate_index_constituent_daily,
    validate_kospi200_breadth_daily,
    validate_kospi200_constituent_price_daily,
)
from stock_data.validation.kr_index_fundamental_daily import (
    validate_kr_index_fundamental_daily,
)
from stock_data.validation.kr_equity import (
    validate_equity_market_cap,
    validate_equity_price,
)
from stock_data.validation.kr_etf import (
    validate_kr_etf_master,
    validate_kr_etf_price_daily,
)
from stock_data.validation.kr_market import validate_market_breadth
from stock_data.validation.market_60m import validate_market_price_60m
from stock_data.validation.market_15m import validate_market_price_15m
from stock_data.validation.tossinvest_historical import validate_toss_historical
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.validation.vkospi_daily import validate_vkospi_daily


@dataclass(frozen=True)
class RuntimeCoverageResult:
    latest: dict[str, str]
    failures: dict[str, str]


@dataclass(frozen=True)
class _CoverageProbe:
    dataset_id: str
    relative_root: str
    date_column: str
    reader: Callable[[Path], pd.DataFrame]


def _contract_reader(contract, validator):
    return lambda root: read_dataset(root, contract, validator)


def _latest_year_contract_reader(contract, validator):
    def read(root: Path) -> pd.DataFrame:
        paths = tuple(root.rglob("data.parquet"))
        year_values = {
            int(part.name.removeprefix("year="))
            for path in paths
            for part in path.parents
            if part != root.parent
            and part.name.startswith("year=")
            and part.name.removeprefix("year=").isdigit()
        }
        if not year_values:
            raise FileNotFoundError(root)
        latest_year = max(year_values)
        latest_paths = [
            path for path in paths
            if any(part.name == f"year={latest_year}" for part in path.parents)
        ]
        frame = pd.concat(
            [pd.read_parquet(path) for path in sorted(latest_paths)], ignore_index=True,
        )
        frame = restore_contract_dates(frame, contract)
        frame = frame[list(contract.column_names)].sort_values(
            list(contract.sort_key), kind="stable",
        ).reset_index(drop=True)
        validator(frame)
        return frame

    return read


def _latest_finality_observation_reader(
    contract, validator, dataset_id: str, *, include_observation_dates: bool = True,
):
    """Validate Normalized data and count retained valid-empty observations.

    A retained observation can be used as an operational watermark only when
    the caller explicitly permits it. Numeric dataset health must instead use
    the latest contract-valid Normalized row.
    """

    read_normalized = _latest_year_contract_reader(contract, validator)

    def read(root: Path) -> pd.DataFrame:
        normalized = read_normalized(root)
        normalized_dates = pd.to_datetime(normalized["date"], errors="coerce")
        if normalized_dates.empty or normalized_dates.isna().any():
            raise ValueError("finality-observation Normalized date is invalid")

        state_path = root.parents[1] / "state/finality" / f"{dataset_id}.json"
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("dataset") != dataset_id:
            raise ValueError("finality-observation state identity differs")
        if payload.get("failures") not in (None, []):
            raise ValueError("finality-observation state retains a failure")
        dates = payload.get("dates")
        if not isinstance(dates, dict) or not dates:
            raise ValueError("finality-observation state has no dates")

        retained: list[date] = []
        for key, value in dates.items():
            if (
                not isinstance(key, str)
                or len(key) != 8
                or not key.isdigit()
                or not isinstance(value, dict)
                # REVISED = a same-day observation awaiting its confirming pass
                # (e.g. KOFIA credit balance published with a lag); it is a valid
                # in-flight entry, not corruption.
                or value.get("status") not in {"STABLE", "PROVISIONAL", "REVISED"}
                or not isinstance(value.get("observations"), list)
                or not value["observations"]
            ):
                raise ValueError("finality-observation date entry is invalid")
            parsed = date(int(key[:4]), int(key[4:6]), int(key[6:8]))
            if value.get("market_date") != parsed.isoformat():
                raise ValueError("finality-observation market date differs")
            retained.append(parsed)

        latest = normalized_dates.max().date()
        if include_observation_dates:
            latest = max(latest, max(retained))
        return pd.DataFrame({"date": [latest]})

    return read


def _data_v1_validator(contract):
    return lambda frame: validate_data_v1(frame, contract, allow_empty=False)


def _contract_integrity_validator(contract):
    """Validate a retained contract projection without adding economic rules."""

    def validate(frame: pd.DataFrame) -> None:
        if tuple(frame.columns) != tuple(contract.column_names) or frame.empty:
            raise ValueError(f"{contract.name}: schema or content is empty")
        if frame[list(contract.primary_key)].isna().any().any():
            raise ValueError(f"{contract.name}: null primary key")
        if frame.duplicated(list(contract.primary_key)).any():
            raise ValueError(f"{contract.name}: duplicate primary key")
        required = [column.name for column in contract.columns if not column.nullable]
        if frame[required].isna().any().any():
            raise ValueError(f"{contract.name}: required value is null")
        dataframe_to_contract_table(frame, contract)

    return validate


def _csv_contract_reader(contract, validator):
    def read(path: Path) -> pd.DataFrame:
        frame = pd.read_csv(path)
        frame = restore_contract_dates(frame, contract)
        frame = frame[list(contract.column_names)].sort_values(
            list(contract.sort_key), kind="stable",
        ).reset_index(drop=True)
        validator(frame)
        return frame

    return read


def _ls_t8462_state_reader(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("normalized_writes") is not False:
        raise ValueError("LS t8462 Raw state identity differs")
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise ValueError("LS t8462 Raw runs are absent")
    dates = [
        str(item.get("market_date"))
        for item in runs
        if isinstance(item, dict) and item.get("status") == "DAILY_COLLECTION_COMPLETE"
    ]
    if not dates:
        raise ValueError("LS t8462 has no complete Raw date")
    return pd.DataFrame({"date": pd.to_datetime(dates, format="%Y%m%d")})


_DERIVATIVES_CHAIN_IDS = frozenset({
    "kr_kospi200_futures_daily",
    "kr_kospi200_options_daily",
    "kr_kospi200_futures_provider_bridge_daily",
    "kr_kospi200_options_provider_bridge_daily",
    "kr_kospi200_futures_nearest_listed_daily",
    "kr_kospi200_option_pcr_daily",
    "kr_kospi200_option_walls_daily",
})


def _derivatives_completed_date(project_root: Path) -> date:
    path = project_root / "data/state/derivatives_price_daily_live.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("dataset") != "derivatives_price_daily_live"
        or payload.get("retry_count") != 0
    ):
        raise ValueError("derivatives completion checkpoint identity is invalid")
    values = payload.get("completed_dates")
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) for value in values)
        or len(values) != len(set(values))
    ):
        raise ValueError("derivatives completion dates are invalid")
    parsed = [date.fromisoformat(value) for value in values]
    if parsed != sorted(parsed):
        raise ValueError("derivatives completion dates are not monotonic")
    last_api_calls = payload.get("last_api_calls")
    if (
        not isinstance(last_api_calls, int)
        or isinstance(last_api_calls, bool)
        or not 0 <= last_api_calls <= 2
    ):
        raise ValueError("derivatives checkpoint call count is invalid")
    return parsed[-1]


_PROBES = (
    _CoverageProbe(
        "market_price_60m_observation", "data/normalized/market_price_60m_observation",
        "market_date", _contract_reader(MARKET_PRICE_60M_OBSERVATION, validate_market_price_60m),
    ),
    _CoverageProbe(
        "market_price_15m_observation", "data/normalized/market_price_15m_observation",
        "market_date", _latest_year_contract_reader(
            MARKET_PRICE_15M_OBSERVATION, validate_market_price_15m,
        ),
    ),
    _CoverageProbe(
        "kr_index_daily", "data/normalized/kr_index_daily", "date",
        read_kr_index_daily,
    ),
    _CoverageProbe(
        "kr_equity_price_daily", "data/normalized/kr_equity_price_daily", "date",
        _latest_year_contract_reader(KR_EQUITY_PRICE_DAILY, validate_equity_price),
    ),
    _CoverageProbe(
        "kr_equity_price_provisional_daily",
        "data/normalized/kr_equity_price_provisional_daily",
        "date",
        _latest_year_contract_reader(
            KR_EQUITY_PRICE_PROVISIONAL_DAILY,
            validate_kr_equity_price_provisional_daily,
        ),
    ),
    _CoverageProbe(
        "kr_etf_master", "data/normalized/kr_etf_master", "source_date",
        _contract_reader(KR_ETF_MASTER, validate_kr_etf_master),
    ),
    _CoverageProbe(
        "kr_etf_price_daily", "data/normalized/kr_etf_price_daily", "date",
        _contract_reader(KR_ETF_PRICE_DAILY, validate_kr_etf_price_daily),
    ),
    _CoverageProbe(
        "kr_corp_code_map", "data/normalized/kr_corp_code_map", "modify_date",
        _contract_reader(
            KR_CORP_CODE_MAP, _contract_integrity_validator(KR_CORP_CODE_MAP),
        ),
    ),
    _CoverageProbe(
        "kr_fundamentals_quarterly",
        "data/normalized/kr_fundamentals_quarterly",
        "period_end",
        _contract_reader(
            KR_FUNDAMENTALS_QUARTERLY,
            _contract_integrity_validator(KR_FUNDAMENTALS_QUARTERLY),
        ),
    ),
    _CoverageProbe(
        "kr_equity_market_cap_daily",
        "data/normalized/kr_equity_market_cap_daily", "date",
        _latest_year_contract_reader(
            KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap,
        ),
    ),
    _CoverageProbe(
        "kr_equity_universe_daily", "data/normalized/kr_equity_universe_daily", "date",
        _latest_year_contract_reader(
            KR_EQUITY_UNIVERSE_DAILY,
            _data_v1_validator(KR_EQUITY_UNIVERSE_DAILY),
        ),
    ),
    _CoverageProbe(
        "kr_equity_canonical_universe_daily",
        "data/published/kr_equity_canonical_universe_daily", "date",
        _latest_year_contract_reader(
            KR_EQUITY_CANONICAL_UNIVERSE_DAILY, validate_canonical_universe,
        ),
    ),
    _CoverageProbe(
        "kr_market_breadth_daily", "data/derived/kr_market_breadth_daily", "date",
        _latest_year_contract_reader(KR_MARKET_BREADTH_DAILY, validate_market_breadth),
    ),
    _CoverageProbe(
        "kr_kospi200_index_daily", "data/normalized/kr_kospi200_index_daily", "date",
        _contract_reader(KR_KOSPI200_INDEX_DAILY, validate_kospi200_index_daily),
    ),
    _CoverageProbe(
        "kr_index_constituent_daily", "data/normalized/kr_index_constituent_daily", "date",
        _latest_year_contract_reader(
            KR_INDEX_CONSTITUENT_DAILY, validate_index_constituent_daily,
        ),
    ),
    _CoverageProbe(
        "kr_kospi200_constituent_price_daily",
        "data/published/kr_kospi200_constituent_price_daily", "date",
        _latest_year_contract_reader(
            KR_KOSPI200_CONSTITUENT_PRICE_DAILY,
            validate_kospi200_constituent_price_daily,
        ),
    ),
    _CoverageProbe(
        "kr_kospi200_breadth_daily", "data/derived/kr_kospi200_breadth_daily", "date",
        _latest_year_contract_reader(
            KR_KOSPI200_BREADTH_DAILY, validate_kospi200_breadth_daily,
        ),
    ),
    _CoverageProbe(
        "kr_kospi200_futures_daily",
        "data/normalized/kr_kospi200_futures_daily", "date",
        _latest_year_contract_reader(
            KR_KOSPI200_FUTURES_DAILY,
            _data_v1_validator(KR_KOSPI200_FUTURES_DAILY),
        ),
    ),
    _CoverageProbe(
        "kr_kospi200_options_daily",
        "data/normalized/kr_kospi200_options_daily", "date",
        _latest_year_contract_reader(
            KR_KOSPI200_OPTIONS_DAILY,
            _data_v1_validator(KR_KOSPI200_OPTIONS_DAILY),
        ),
    ),
    _CoverageProbe(
        "kr_kospi200_futures_provider_bridge_daily",
        (
            "data/published/c007_kospi200_derivatives_bridge/"
            "kr_kospi200_futures_provider_bridge_daily"
        ),
        "date",
        _latest_year_contract_reader(
            KR_KOSPI200_FUTURES_PROVIDER_BRIDGE_DAILY,
            lambda frame: validate_derivatives_bridge(
                frame, DERIVATIVES_FUTURES_BRIDGE_SCHEMA,
            ),
        ),
    ),
    _CoverageProbe(
        "kr_kospi200_options_provider_bridge_daily",
        (
            "data/published/c007_kospi200_derivatives_bridge/"
            "kr_kospi200_options_provider_bridge_daily"
        ),
        "date",
        _latest_year_contract_reader(
            KR_KOSPI200_OPTIONS_PROVIDER_BRIDGE_DAILY,
            lambda frame: validate_derivatives_bridge(
                frame, DERIVATIVES_OPTIONS_BRIDGE_SCHEMA,
            ),
        ),
    ),
    _CoverageProbe(
        "kr_kospi200_futures_nearest_listed_daily",
        "data/derived/kr_kospi200_futures_nearest_listed_daily", "date",
        _latest_year_contract_reader(
            KR_KOSPI200_FUTURES_NEAREST_LISTED_DAILY, validate_futures_basis,
        ),
    ),
    _CoverageProbe(
        "kr_kospi200_option_pcr_daily",
        "data/derived/kr_kospi200_option_pcr_daily", "date",
        _latest_year_contract_reader(
            KR_KOSPI200_OPTION_PCR_DAILY, validate_modern_pcr,
        ),
    ),
    _CoverageProbe(
        "kr_kospi200_option_walls_daily",
        "artifacts/analysis/kospi200_option_wall_recent_250.csv", "date",
        _csv_contract_reader(
            KR_KOSPI200_OPTION_WALLS_DAILY,
            _contract_integrity_validator(KR_KOSPI200_OPTION_WALLS_DAILY),
        ),
    ),
    _CoverageProbe(
        "kr_index_fundamental_daily",
        "data/normalized/kr_index_fundamental_daily", "date",
        _latest_year_contract_reader(
            KR_INDEX_FUNDAMENTAL_DAILY, validate_kr_index_fundamental_daily,
        ),
    ),
    _CoverageProbe(
        "kr_vkospi_daily", "data/normalized/kr_vkospi_daily", "market_date",
        _contract_reader(KR_VKOSPI_DAILY, validate_vkospi_daily),
    ),
    _CoverageProbe(
        "kr_market_investor_net_purchase_bridge_daily",
        "data/published/kr_market_investor_net_purchase_bridge_daily", "date",
        _contract_reader(
            KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY, validate_investor_bridge,
        ),
    ),
    _CoverageProbe(
        "kr_market_investor_trading_daily",
        "data/normalized/kr_market_investor_trading_daily", "date",
        _contract_reader(
            KR_MARKET_INVESTOR_TRADING_DAILY,
            lambda frame: validate_toss_historical(
                frame, KR_MARKET_INVESTOR_TRADING_DAILY
            ),
        ),
    ),
    _CoverageProbe(
        "global_index_price_daily", "data/normalized/global_index_price_daily", "date",
        _contract_reader(GLOBAL_INDEX_PRICE_DAILY, validate_global_index),
    ),
    _CoverageProbe(
        "global_etf_price_daily", "data/normalized/global_etf_price_daily", "date",
        _contract_reader(GLOBAL_ETF_PRICE_DAILY, validate_global_etf),
    ),
    _CoverageProbe(
        "global_commodity_futures_daily",
        "data/normalized/global_commodity_futures_daily", "date",
        _contract_reader(
            GLOBAL_COMMODITY_FUTURES_DAILY, validate_global_commodity_futures,
        ),
    ),
    _CoverageProbe(
        "fred_treasury_yield_daily", "data/normalized/fred_treasury_yield_daily", "date",
        _contract_reader(FRED_TREASURY_YIELD_DAILY, validate_fred),
    ),
    _CoverageProbe(
        "fred_usd_fx_daily", "data/normalized/fred_usd_fx_daily", "date",
        _contract_reader(FRED_USD_FX_DAILY, validate_fred),
    ),
    _CoverageProbe(
        "bok_ecos_usd_krw_daily", "data/normalized/bok_ecos_usd_krw_daily", "date",
        _contract_reader(BOK_ECOS_USD_KRW_DAILY, validate_bok_fx),
    ),
    _CoverageProbe(
        "fred_vix_daily", "data/normalized/fred_vix_daily", "date",
        _contract_reader(FRED_VIX_DAILY, validate_fred),
    ),
    _CoverageProbe(
        "us_treasury_spread_daily", "data/derived/us_treasury_spread_daily", "date",
        _contract_reader(US_TREASURY_SPREAD_DAILY, validate_fred),
    ),
    _CoverageProbe(
        "kr_treasury_yield_daily", "data/normalized/kr_treasury_yield_daily", "date",
        _latest_year_contract_reader(
            KR_TREASURY_YIELD_DAILY,
            lambda frame: validate_toss_historical(frame, KR_TREASURY_YIELD_DAILY),
        ),
    ),
    _CoverageProbe(
        "bok_ecos_kr_treasury_yield_source_observation",
        "data/normalized/bok_ecos_kr_treasury_yield_source_observation", "date",
        _latest_year_contract_reader(
            BOK_ECOS_KR_TREASURY_YIELD_SOURCE_OBSERVATION,
            _contract_integrity_validator(
                BOK_ECOS_KR_TREASURY_YIELD_SOURCE_OBSERVATION,
            ),
        ),
    ),
    _CoverageProbe(
        "kr_stock_lending_daily", "data/normalized/kr_stock_lending_daily", "date",
        _latest_year_contract_reader(
            KR_STOCK_LENDING_DAILY, _data_v1_validator(KR_STOCK_LENDING_DAILY),
        ),
    ),
    _CoverageProbe(
        "kr_stock_lending_market_daily",
        "data/normalized/kr_stock_lending_market_daily", "date",
        _latest_year_contract_reader(
            KR_STOCK_LENDING_MARKET_DAILY,
            _data_v1_validator(KR_STOCK_LENDING_MARKET_DAILY),
        ),
    ),
    _CoverageProbe(
        "kr_stock_lending_participant_daily",
        "data/normalized/kr_stock_lending_participant_daily", "date",
        _latest_year_contract_reader(
            KR_STOCK_LENDING_PARTICIPANT_DAILY,
            _data_v1_validator(KR_STOCK_LENDING_PARTICIPANT_DAILY),
        ),
    ),
    _CoverageProbe(
        "kr_market_liquidity_daily",
        "data/normalized/kr_market_liquidity_daily", "date",
        _latest_finality_observation_reader(
            KR_MARKET_LIQUIDITY_DAILY,
            _data_v1_validator(KR_MARKET_LIQUIDITY_DAILY),
            "kr_market_liquidity_daily",
        ),
    ),
    _CoverageProbe(
        "kr_credit_balance_daily",
        "data/normalized/kr_credit_balance_daily", "date",
        _latest_finality_observation_reader(
            KR_CREDIT_BALANCE_DAILY,
            _data_v1_validator(KR_CREDIT_BALANCE_DAILY),
            "kr_credit_balance_daily",
            include_observation_dates=False,
        ),
    ),
    _CoverageProbe(
        "kr_short_selling_trading_daily",
        "data/normalized/kr_short_selling_trading_daily", "date",
        _latest_year_contract_reader(
            KR_SHORT_SELLING_TRADING_DAILY,
            _data_v1_validator(KR_SHORT_SELLING_TRADING_DAILY),
        ),
    ),
    _CoverageProbe(
        "kr_short_selling_balance_daily",
        "data/normalized/kr_short_selling_balance_daily", "date",
        _latest_year_contract_reader(
            KR_SHORT_SELLING_BALANCE_DAILY,
            _data_v1_validator(KR_SHORT_SELLING_BALANCE_DAILY),
        ),
    ),
    _CoverageProbe(
        "kr_short_selling_investor_daily",
        "data/normalized/kr_short_selling_investor_daily", "date",
        _latest_year_contract_reader(
            KR_SHORT_SELLING_INVESTOR_DAILY,
            _data_v1_validator(KR_SHORT_SELLING_INVESTOR_DAILY),
        ),
    ),
    _CoverageProbe(
        "ls_t8462_daily_raw", "data/state/ls_t8462_daily_raw.json", "date",
        _ls_t8462_state_reader,
    ),
)


def validated_runtime_coverage(
    project_root: Path, *, as_of: date | None = None,
) -> RuntimeCoverageResult:
    """Read Dashboard-facing artifacts and contract-validate current partitions."""
    root = Path(project_root).resolve()
    reference_date = as_of or date.today()
    latest: dict[str, str] = {}
    failures: dict[str, str] = {}
    derivatives_completed: date | None = None
    derivatives_checkpoint_failure: str | None = None
    try:
        derivatives_completed = _derivatives_completed_date(root)
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError) as error:
        derivatives_checkpoint_failure = type(error).__name__
    for probe in _PROBES:
        if (
            probe.dataset_id in _DERIVATIVES_CHAIN_IDS
            and derivatives_checkpoint_failure is not None
        ):
            failures[probe.dataset_id] = derivatives_checkpoint_failure
            continue
        try:
            frame = probe.reader(root / probe.relative_root)
            values = pd.to_datetime(frame[probe.date_column], errors="coerce")
            if values.empty or values.isna().any():
                raise ValueError("validated dataset has no canonical latest date")
            if probe.dataset_id == "kr_fundamentals_quarterly":
                cutoff = reference_date
                if "rcept_no" in frame.columns:
                    receipts = pd.to_datetime(
                        frame["rcept_no"].astype(str).str[:8],
                        format="%Y%m%d", errors="coerce",
                    )
                    if receipts.notna().any():
                        cutoff = min(cutoff, receipts.max().date())
                values = values[values.dt.date <= cutoff]
                if values.empty:
                    raise ValueError("fundamentals has no non-future reference period")
            actual_latest = values.max().date()
            if (
                probe.dataset_id in _DERIVATIVES_CHAIN_IDS
                and actual_latest != derivatives_completed
            ):
                raise ValueError(
                    "derivatives artifact latest date differs from completion checkpoint"
                )
            latest[probe.dataset_id] = actual_latest.isoformat()
        except (FileNotFoundError, KeyError, OSError, PermissionError, TypeError, ValueError) as error:
            failures[probe.dataset_id] = type(error).__name__
    return RuntimeCoverageResult(latest=latest, failures=failures)


__all__ = ["RuntimeCoverageResult", "validated_runtime_coverage"]
