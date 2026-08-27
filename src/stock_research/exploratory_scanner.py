"""Practical provider-free current-stock exploration over retained local prices.

This is deliberately a descriptive daily scanner, not a PIT backtest feature.
Partial axes remain visible and never block an independently available axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from stock_data.providers.pykrx.kr_equity_fundamental_observation import (
    find_valid_equity_fundamental_observation,
)


EXPLORATORY_SCANNER_VERSION = "stock-exploratory-scanner/v1"
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
_CRITERIA = "RSI14 <= 30 OR close/SMA60 <= 80%"
_RANKING = "RSI14_ASC_THEN_DISPARITY60_ASC"
_SOURCE_NOTE = (
    "kr_equity_price_daily provider-native original price; current dated universe; "
    "optional exact-date KRX MDCSTAT03501 current PER/PBR observation; "
    "descriptive only; forward earnings and relative-value judgment not connected"
)


@dataclass(frozen=True, slots=True)
class ExploratoryStockCandidate:
    symbol: str
    name: str | None
    market: str
    as_of: str
    close: float
    volume: int
    rsi14: float
    disparity60: float
    technical_state: str
    data_caution: str | None
    earnings_state: str = "NOT_CONNECTED"
    valuation_state: str = "NOT_CONNECTED"
    per: float | None = None
    pbr: float | None = None
    valuation_as_of: str | None = None


@dataclass(frozen=True, slots=True)
class ExploratoryCandidateView:
    contract_version: str
    availability: str
    as_of: str | None
    scanned_instruments: int
    eligible_instruments: int
    candidates: tuple[ExploratoryStockCandidate, ...]
    criteria: str
    source_note: str
    unavailable_reason: str | None = None
    ranking_basis: str = _RANKING
    recommendation_state: str = "DESCRIPTIVE_NOT_A_RECOMMENDATION"


def validate_exploratory_candidate_view(view: object) -> ExploratoryCandidateView:
    if (
        type(view) is not ExploratoryCandidateView
        or view.contract_version != EXPLORATORY_SCANNER_VERSION
        or view.availability not in {"READY", "UNAVAILABLE"}
        or view.recommendation_state != "DESCRIPTIVE_NOT_A_RECOMMENDATION"
        or view.criteria != _CRITERIA
        or view.ranking_basis != _RANKING
        or view.source_note != _SOURCE_NOTE
        or type(view.scanned_instruments) is not int
        or type(view.eligible_instruments) is not int
        or min(view.scanned_instruments, view.eligible_instruments) < 0
    ):
        raise ValueError("exploratory candidate view envelope is invalid")
    if view.availability == "UNAVAILABLE":
        if (
            view.as_of is not None
            or view.scanned_instruments != 0
            or view.eligible_instruments != 0
            or view.candidates
            or not isinstance(view.unavailable_reason, str)
            or not view.unavailable_reason
        ):
            raise ValueError("unavailable exploratory candidate view is invalid")
        return view
    if view.unavailable_reason is not None or not isinstance(view.as_of, str):
        raise ValueError("ready exploratory candidate view is inconsistent")
    try:
        as_of = calendar_date.fromisoformat(view.as_of)
    except ValueError as error:
        raise ValueError("exploratory candidate as_of is invalid") from error
    if as_of.isoformat() != view.as_of or as_of > datetime.now(ZoneInfo("Asia/Seoul")).date():
        raise ValueError("exploratory candidate as_of is invalid")
    if (
        view.scanned_instruments < view.eligible_instruments
        or view.eligible_instruments < len(view.candidates)
        or len(view.candidates) > 80
    ):
        raise ValueError("exploratory candidate counts are invalid")
    identities: set[tuple[str, str]] = set()
    order: list[tuple[float, float, str, str]] = []
    for candidate in view.candidates:
        if (
            type(candidate) is not ExploratoryStockCandidate
            or candidate.market not in {"KOSPI", "KOSDAQ"}
            or type(candidate.symbol) is not str
            or _SYMBOL.fullmatch(candidate.symbol) is None
            or candidate.as_of != view.as_of
            or isinstance(candidate.volume, bool)
            or type(candidate.volume) is not int
            or candidate.volume < 0
            or candidate.earnings_state != "NOT_CONNECTED"
            or candidate.valuation_state not in {
                "NOT_CONNECTED", "AVAILABLE_CURRENT_TRAILING",
            }
            or candidate.technical_state not in {"과매도", "60일선 큰 폭 하회"}
            or candidate.data_caution not in {None, "원가격 급변/분할 영향 가능"}
        ):
            raise ValueError("exploratory candidate row is invalid")
        valuation_numbers = (candidate.per, candidate.pbr)
        if candidate.valuation_state == "NOT_CONNECTED":
            if (
                any(value is not None for value in valuation_numbers)
                or candidate.valuation_as_of is not None
            ):
                raise ValueError("disconnected valuation row carries values")
        elif (
            candidate.valuation_as_of != view.as_of
            or all(value is None for value in valuation_numbers)
            or any(
                isinstance(value, bool)
                or value is not None and (not np.isfinite(value) or value < 0.0)
                for value in valuation_numbers
            )
        ):
            raise ValueError("current valuation row is invalid")
        numeric = np.asarray(
            (candidate.close, candidate.rsi14, candidate.disparity60), dtype="float64"
        )
        if (
            not np.isfinite(numeric).all()
            or candidate.close <= 0.0
            or not 0.0 <= candidate.rsi14 <= 100.0
            or candidate.disparity60 <= 0.0
            or (candidate.rsi14 > 30.0 and candidate.disparity60 > 80.0)
        ):
            raise ValueError("exploratory candidate numeric state is invalid")
        expected_state = (
            "과매도" if candidate.rsi14 <= 30.0
            else "60일선 큰 폭 하회"
        )
        if candidate.technical_state != expected_state:
            raise ValueError("exploratory candidate technical state is invalid")
        identity = (candidate.market, candidate.symbol)
        if identity in identities:
            raise ValueError("duplicate exploratory candidate row")
        identities.add(identity)
        order.append((
            candidate.rsi14, candidate.disparity60,
            candidate.market, candidate.symbol,
        ))
    if order != sorted(order):
        raise ValueError("exploratory candidate ordering is invalid")
    return view


def _wilder_rsi_last(values: np.ndarray, period: int = 14) -> float | None:
    if len(values) <= period or not np.isfinite(values).all() or (values <= 0).any():
        return None
    delta = np.diff(values)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    average_gain = float(gains[:period].mean())
    average_loss = float(losses[:period].mean())
    for offset in range(period, len(delta)):
        average_gain = (average_gain * (period - 1) + float(gains[offset])) / period
        average_loss = (average_loss * (period - 1) + float(losses[offset])) / period
    if average_gain == 0.0 and average_loss == 0.0:
        return 50.0
    if average_loss == 0.0:
        return 100.0
    if average_gain == 0.0:
        return 0.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _provider_ratio(value: object) -> float | None:
    token = str(value).strip()
    if token == "-":
        return None
    try:
        parsed = float(token.replace(",", ""))
    except ValueError as error:
        raise ValueError("valuation ratio is not numeric") from error
    if not np.isfinite(parsed) or parsed < 0.0:
        raise ValueError("valuation ratio is out of range")
    return parsed


def _current_valuation_by_symbol(
    project_root: Path, *, as_of: str,
) -> dict[str, tuple[float | None, float | None]]:
    root = (
        project_root / "data/landing/kr_equity_fundamental_current_observation"
    )
    try:
        target = calendar_date.fromisoformat(as_of)
        observation = find_valid_equity_fundamental_observation(root, target)
        if observation is None or observation.duplicate_groups != 0:
            return {}
        payload = json.loads(observation.path.read_bytes())
        rows = payload["output"]
        return {
            str(row["ISU_SRT_CD"]).strip(): (
                _provider_ratio(row["PER"]), _provider_ratio(row["PBR"]),
            )
            for row in rows
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _latest_year(root: Path) -> int | None:
    years = []
    for path in root.glob("market=*/year=*"):
        token = path.name.removeprefix("year=")
        if path.is_dir() and token.isdigit():
            years.append(int(token))
    return max(years) if years else None


def _read_years(root: Path, years: tuple[int, ...], columns: list[str]) -> pd.DataFrame:
    paths = tuple(
        path
        for year in years
        for market in ("KOSPI", "KOSDAQ")
        for path in (root / f"market={market}" / f"year={year}" / "data.parquet",)
        if path.is_file()
    )
    if not paths:
        return pd.DataFrame(columns=columns)
    return pd.concat(
        (pd.read_parquet(path, columns=columns) for path in paths),
        ignore_index=True,
    )


def _safe_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    exact = value.strip()
    if not exact or "\ufffd" in exact:
        return None
    return exact


class LocalExploratoryCandidateScanner:
    """Read local current-universe and original-price data without providers."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)

    def unavailable(self, reason: str) -> ExploratoryCandidateView:
        return ExploratoryCandidateView(
            contract_version=EXPLORATORY_SCANNER_VERSION,
            availability="UNAVAILABLE",
            as_of=None,
            scanned_instruments=0,
            eligible_instruments=0,
            candidates=(),
            criteria=_CRITERIA,
            source_note=_SOURCE_NOTE,
            unavailable_reason=reason,
        )

    def scan(
        self,
        *,
        rsi_ceiling: float = 30.0,
        disparity_ceiling: float = 80.0,
        limit: int = 80,
    ) -> ExploratoryCandidateView:
        if (
            isinstance(rsi_ceiling, bool)
            or isinstance(disparity_ceiling, bool)
            or not np.isfinite([rsi_ceiling, disparity_ceiling]).all()
            or not 0.0 < float(rsi_ceiling) < 100.0
            or not 0.0 < float(disparity_ceiling) < 200.0
            or float(rsi_ceiling) != 30.0
            or float(disparity_ceiling) != 80.0
            or type(limit) is not int
            or not 1 <= limit <= 80
        ):
            raise ValueError("exploratory scanner parameters are invalid")
        price_root = self.project_root / "data/normalized/kr_equity_price_daily"
        universe_root = (
            self.project_root / "data/published/kr_equity_canonical_universe_daily"
        )
        latest_year = _latest_year(price_root)
        if latest_year is None:
            return self.unavailable("LOCAL_PRICE_DATASET_MISSING")
        current_paths = tuple(
            root / f"market={market}" / f"year={latest_year}" / "data.parquet"
            for root in (price_root, universe_root)
            for market in ("KOSPI", "KOSDAQ")
        )
        if any(not path.is_file() for path in current_paths):
            return self.unavailable("CURRENT_MARKET_PARTITION_INCOMPLETE")
        try:
            price = _read_years(
                price_root, (latest_year - 1, latest_year),
                ["date", "market", "symbol", "close", "volume"],
            )
            universe = _read_years(
                universe_root, (latest_year,),
                [
                    "date", "market", "symbol", "name", "listed_info_present",
                    "price_present",
                ],
            )
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            return self.unavailable("LOCAL_CANDIDATE_READ_FAILED")
        if price.empty or universe.empty:
            return self.unavailable("LOCAL_CANDIDATE_INPUT_EMPTY")

        price = price.copy()
        price["date"] = pd.to_datetime(price["date"], errors="coerce")
        price["close"] = pd.to_numeric(price["close"], errors="coerce")
        price["volume"] = pd.to_numeric(price["volume"], errors="coerce")
        price = price.dropna(subset=["date", "market", "symbol", "close", "volume"])
        price = price[
            price["market"].isin(("KOSPI", "KOSDAQ"))
            & (price["close"] > 0)
            & (price["volume"] >= 0)
        ]
        if price.empty:
            return self.unavailable("LOCAL_CANDIDATE_PRICE_INVALID")
        latest = price["date"].max()
        if latest.date() > datetime.now(ZoneInfo("Asia/Seoul")).date():
            return self.unavailable("FUTURE_DATED_INPUT")
        universe = universe.copy()
        universe["date"] = pd.to_datetime(universe["date"], errors="coerce")
        universe = universe[
            (universe["date"] == latest)
            & universe["listed_info_present"].eq(True)
            & universe["price_present"].eq(True)
        ].drop_duplicates(["market", "symbol"], keep=False)
        if universe.empty or set(universe["market"].astype(str)) != {"KOSPI", "KOSDAQ"}:
            return self.unavailable("CURRENT_UNIVERSE_NOT_ALIGNED")
        names = {
            (str(row.market), str(row.symbol)): _safe_name(row.name)
            for row in universe.itertuples(index=False)
        }
        valuation_by_symbol = _current_valuation_by_symbol(
            self.project_root, as_of=latest.date().isoformat(),
        )
        allowed = set(names)
        price = price[
            pd.MultiIndex.from_frame(price[["market", "symbol"]]).isin(allowed)
        ].sort_values(["market", "symbol", "date"])

        candidates: list[ExploratoryStockCandidate] = []
        scanned = 0
        for (market, symbol), group in price.groupby(["market", "symbol"], sort=False):
            if group["date"].iloc[-1] != latest or group["date"].duplicated().any():
                continue
            tail = group.tail(260)
            if len(tail) < 60:
                continue
            close = tail["close"].to_numpy(dtype="float64")
            if not np.isfinite(close).all() or (close <= 0).any():
                continue
            rsi = _wilder_rsi_last(close)
            average60 = float(close[-60:].mean())
            if rsi is None or not np.isfinite(average60) or average60 <= 0.0:
                continue
            disparity = float(close[-1] / average60 * 100.0)
            scanned += 1
            if rsi > rsi_ceiling and disparity > disparity_ceiling:
                continue
            recent_returns = close[-60:][1:] / close[-60:][:-1] - 1.0
            caution = (
                "원가격 급변/분할 영향 가능"
                if np.max(np.abs(recent_returns)) >= 0.5 else None
            )
            state = (
                "과매도" if rsi <= 30.0
                else "60일선 큰 폭 하회"
            )
            last = tail.iloc[-1]
            per, pbr = valuation_by_symbol.get(str(symbol), (None, None))
            valuation_available = per is not None or pbr is not None
            candidates.append(ExploratoryStockCandidate(
                symbol=str(symbol),
                name=names.get((str(market), str(symbol))),
                market=str(market),
                as_of=latest.date().isoformat(),
                close=float(close[-1]),
                volume=int(last["volume"]),
                rsi14=float(rsi),
                disparity60=disparity,
                technical_state=state,
                data_caution=caution,
                valuation_state=(
                    "AVAILABLE_CURRENT_TRAILING"
                    if valuation_available else "NOT_CONNECTED"
                ),
                per=per,
                pbr=pbr,
                valuation_as_of=(
                    latest.date().isoformat() if valuation_available else None
                ),
            ))
        candidates.sort(key=lambda item: (item.rsi14, item.disparity60, item.market, item.symbol))
        return ExploratoryCandidateView(
            contract_version=EXPLORATORY_SCANNER_VERSION,
            availability="READY",
            as_of=latest.date().isoformat(),
            scanned_instruments=scanned,
            eligible_instruments=len(candidates),
            candidates=tuple(candidates[:limit]),
            criteria=_CRITERIA,
            source_note=_SOURCE_NOTE,
        )
