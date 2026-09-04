"""Assemble the home-page payload from retained local data.

Each section is independent: a missing or unverified dataset yields an
absent section (or a ``reason``), never a substituted number.
"""
from __future__ import annotations

from collections.abc import Callable
import math
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stock_web.api import datasets as dsx
from stock_web.api.datasets import field
from stock_web.api.fmt import KST, format_kst
from stock_web.api.indicators import rsi_wilder
from stock_web.api.intraday import load_intraday_series

_HOME_CACHE_TTL_SECONDS = 60.0
_HOME_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
_PUBLIC_REGIME_LOCK = threading.Lock()
_PUBLIC_SCANNER_LOCK = threading.Lock()

RANGE_SESSIONS = {"3M": 63, "6M": 126, "1Y": 252, "3Y": 756, "ALL": None}

# symbol -> (dataset root, filter column, filter value, display name)
INDEX_SOURCES = {
    "KOSPI": ("data/normalized/kr_index_daily", "symbol", "KOSPI", "KOSPI"),
    "KOSDAQ": ("data/normalized/kr_index_daily", "symbol", "KOSDAQ", "KOSDAQ"),
    "KOSPI200": ("data/normalized/kr_kospi200_index_daily", "symbol", "KOSPI200", "KOSPI200"),
    "SP500": ("data/normalized/global_index_price_daily", "symbol", "SP500", "S&P 500"),
    "NASDAQ": ("data/normalized/global_index_price_daily", "symbol", "NASDAQ_COMPOSITE", "NASDAQ 종합"),
    "NDX": ("data/normalized/global_index_price_daily", "symbol", "NASDAQ100", "NASDAQ 100"),
    "NQF": ("data/normalized/global_commodity_futures_daily", "symbol", "NASDAQ100_FUTURES", "NASDAQ 100 선물"),
    "WTI": ("data/normalized/global_commodity_futures_daily", "symbol", "WTI_CRUDE_OIL", "WTI 선물"),
    "GOLD": ("data/normalized/global_commodity_futures_daily", "symbol", "GOLD", "금 선물"),
    "SOXX": ("data/normalized/global_etf_price_daily", "symbol", "SOXX", "SOXX (반도체 ETF)"),
    "EWY": ("data/normalized/global_etf_price_daily", "symbol", "EWY", "EWY (한국 ETF)"),
    "SOX": ("data/normalized/global_index_price_daily", "symbol", "SOX", "필라델피아 반도체"),
    "DOW": ("data/normalized/global_index_price_daily", "symbol", "DOW_JONES", "다우존스"),
    "DXY": ("data/normalized/global_index_price_daily", "symbol", "DOLLAR_INDEX", "달러 인덱스"),
    "ESF": ("data/normalized/global_commodity_futures_daily", "symbol", "SP500_FUTURES", "S&P 500 선물"),
    "YMF": ("data/normalized/global_commodity_futures_daily", "symbol", "DOW_FUTURES", "다우 선물"),
}


def _nan_to_none(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            return None
        return _nan_to_none(value)
    return value


def _ohlcv(project_root: Path, symbol: str) -> tuple[pd.DataFrame | None, str]:
    """Daily OHLCV for an index/commodity symbol or a 6-digit Korean stock code."""
    if symbol in INDEX_SOURCES:
        root, col, val, name = INDEX_SOURCES[symbol]
        frame = dsx.load(project_root, root, filter_expr=(field(col) == val))
        return frame, name
    if len(symbol) == 6 and symbol.isalnum() and symbol[0].isdigit():
        canonical = dsx.load(
            project_root,
            "data/normalized/kr_equity_price_daily",
            filter_expr=(field("symbol") == symbol),
        )
        provisional = dsx.load(
            project_root,
            "data/normalized/kr_equity_price_provisional_daily",
            filter_expr=(field("symbol") == symbol),
        )
        parts: list[pd.DataFrame] = []
        if canonical is not None and not canonical.empty:
            canonical = canonical.copy()
            canonical["provisional"] = False
            parts.append(canonical)
            if provisional is not None and not provisional.empty:
                latest_canonical = pd.to_datetime(canonical["date"], errors="raise").max()
                provisional = provisional.loc[
                    pd.to_datetime(provisional["date"], errors="raise") > latest_canonical
                ].copy()
        if provisional is not None and not provisional.empty:
            provisional = provisional.copy()
            provisional["provisional"] = True
            parts.append(provisional)
        if parts:
            frame = pd.concat(parts, ignore_index=True, sort=False).sort_values(
                "date", kind="stable"
            ).reset_index(drop=True)
            return frame, _stock_name(project_root, symbol)
        # Korean ETFs live in their own retained dataset (pykrx, kr_etf_price_daily).
        frame = dsx.load(
            project_root, "data/normalized/kr_etf_price_daily",
            filter_expr=(field("symbol") == symbol), partitioning=None,
        )
        return frame, _stock_name(project_root, symbol)
    from stock_web.api.symbol_resolver import global_equity_identity

    global_equity = global_equity_identity(symbol)
    if global_equity is not None:
        frame = dsx.load(
            project_root, "data/normalized/global_equity_price_daily",
            filter_expr=(field("symbol") == symbol), partitioning=None,
        )
        return frame, str(global_equity["name"])
    if symbol.isalpha() and 1 <= len(symbol) <= 5:
        frame = dsx.load(project_root, "data/normalized/global_etf_price_daily", filter_expr=(field("symbol") == symbol))
        return frame, symbol
    return None, symbol


_NAME_CACHE: dict[str, dict[str, str]] = {}


def _stock_name(project_root: Path, symbol: str) -> str:
    key = str(project_root.resolve())
    names = _NAME_CACHE.setdefault(key, {})
    if not names:
        for dataset in ("data/normalized/kr_equity_master", "data/normalized/kr_etf_master"):
            master = dsx.load(project_root, dataset)
            if master is not None and {"symbol", "name"} <= set(master.columns):
                names.update(dict(zip(master["symbol"].astype(str), master["name"].astype(str))))
    return names.get(symbol, symbol)


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"].astype(float)
    for w in (5, 20, 60, 120):
        out[f"ma{w}"] = close.rolling(w).mean()
    out["rsi14"] = rsi_wilder(close, 14)
    out["disp60_pct"] = (close / out["ma60"] - 1) * 100
    out["high_252"] = close.rolling(252, min_periods=20).max()
    out["drawdown_pct"] = (close / out["high_252"] - 1) * 100
    return out


def build_chart_payload(project_root: Path, *, symbol: str, range_key: str) -> dict[str, object]:
    frame, name = _ohlcv(project_root, symbol)
    if frame is None or frame.empty or "close" not in frame.columns:
        return {"symbol": symbol, "symbol_name": name, "reason": "보존 데이터 없음"}
    frame = frame.dropna(subset=["close"])
    ind = _indicators(frame)
    n = RANGE_SESSIONS.get(range_key, 126)
    view = ind.iloc[-n:] if n else ind
    dates = view["date"].dt.strftime("%Y-%m-%d")
    candles = []
    for d, row in zip(dates, view.itertuples(index=False)):
        o = _nan_to_none(getattr(row, "open", None)) or _nan_to_none(row.close)
        candles.append({
            "t": d, "o": o, "h": _nan_to_none(getattr(row, "high", None)) or o,
            "l": _nan_to_none(getattr(row, "low", None)) or o, "c": _nan_to_none(row.close),
            "v": _nan_to_none(getattr(row, "volume", None)),
        })
    ma = {f"ma{w}": [{"t": d, "v": _nan_to_none(v)} for d, v in zip(dates, view[f"ma{w}"])] for w in (5, 20, 60, 120)}
    last = ind.iloc[-1]
    stats: dict[str, object] = {
        "rsi14": _nan_to_none(last["rsi14"]), "disp60_pct": _nan_to_none(last["disp60_pct"]),
        "drawdown_pct": _nan_to_none(last["drawdown_pct"]),
    }
    stats.update(_valuation(project_root, symbol))
    return {
        "symbol": symbol, "symbol_name": name, "range": range_key,
        "as_of": str(dates.iloc[-1]),
        "provisional_dates": (
            view.loc[view["provisional"].fillna(False).astype(bool), "date"]
            .dt.strftime("%Y-%m-%d").tolist()
            if "provisional" in view.columns else []
        ),
        "candles": candles, "ma": ma, "stats": stats,
    }


def _valuation(project_root: Path, symbol: str) -> dict[str, object]:
    code = {"KOSPI": "1001", "KOSDAQ": "2001"}.get(symbol)
    if code is None:
        return {}
    frame = dsx.load(project_root, "data/normalized/kr_index_fundamental_daily", filter_expr=(field("index_code") == code))
    if frame is None or frame.empty:
        return {}
    frame = frame.dropna(subset=["weighted_per"])
    last = frame.iloc[-1]
    five_years = frame[frame["date"] >= frame["date"].max() - pd.Timedelta(days=365 * 5)]
    rank = (five_years["weighted_per"] < last["weighted_per"]).mean() * 100
    return {
        "per": _nan_to_none(last["weighted_per"]), "pbr": _nan_to_none(last["weighted_pbr"]),
        "per_note": f"5년 상위 {100 - rank:.0f}%", "valuation_as_of": last["date"].strftime("%Y-%m-%d"),
    }


def _tile_from_series(name: str, symbol: str | None, frame: pd.DataFrame | None, value_col: str,
                      fmt: str = "{:,.2f}", change_kind: str = "pct", window_label: str = "최근 30일 마감") -> dict[str, object]:
    if frame is None or frame.empty or value_col not in frame.columns:
        return {"name": name, "symbol": symbol, "value": "—", "note": "보존 데이터 없음"}
    series = frame.dropna(subset=[value_col])
    if len(series) < 2:
        return {"name": name, "symbol": symbol, "value": "—", "note": "데이터 부족"}
    values = series[value_col].astype(float)
    last, prev = values.iloc[-1], values.iloc[-2]
    ma5 = values.rolling(5).mean().iloc[-1]
    ma20 = values.rolling(20).mean().iloc[-1]
    ma5_delta = (last - ma5) * 100 if change_kind == "bp" else (last / ma5 - 1) * 100 if ma5 else None
    ma20_delta = (last - ma20) * 100 if change_kind == "bp" else (last / ma20 - 1) * 100 if ma20 else None
    tile: dict[str, object] = {
        "name": name, "symbol": symbol, "value": fmt.format(last),
        "ma5_pct": _nan_to_none(ma5_delta),
        "ma20_pct": _nan_to_none(ma20_delta),
        "spark": [round(float(v), 4) for v in values.iloc[-30:]],
        "window": f"{window_label} · {series['date'].iloc[-1]:%m-%d}",
        "_daily_value": float(last),
        "_daily_date": series["date"].iloc[-1].strftime("%Y-%m-%d"),
    }
    if change_kind == "pct":
        tile["change_pct"] = _nan_to_none((last / prev - 1) * 100)
    else:  # basis points for yields
        bp = (last - prev) * 100
        tile["change_pct"] = _nan_to_none(bp)
        tile["change_label"] = f"{bp:+.0f}bp"
        tile["ma5_label"] = f"{ma5_delta:+.0f}bp" if pd.notna(ma5_delta) else None
        tile["ma20_label"] = f"{ma20_delta:+.0f}bp" if pd.notna(ma20_delta) else None
    return tile


def _placeholder(name: str, note: str) -> dict[str, object]:
    return {"name": name, "symbol": None, "value": "—", "note": note}


def _previous_kst_session_close(points: list[dict[str, object]]) -> float | None:
    if not points:
        return None
    try:
        latest_date = datetime.fromisoformat(
            str(points[-1]["t"]).replace("Z", "+00:00")
        ).astimezone(KST).date()
        prior = [
            float(point["v"])
            for point in points
            if datetime.fromisoformat(
                str(point["t"]).replace("Z", "+00:00")
            ).astimezone(KST).date() < latest_date
        ]
    except (KeyError, TypeError, ValueError):
        return None
    return prior[-1] if prior else None


def _compact_number(value: object) -> str:
    return f"{float(value):,.2f}".rstrip("0").rstrip(".")


def _latest_value_date(
    frame: pd.DataFrame | None, value_column: str,
) -> tuple[float, pd.Timestamp] | None:
    if frame is None or frame.empty or not {"date", value_column}.issubset(frame.columns):
        return None
    work = frame[["date", value_column]].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work[value_column] = pd.to_numeric(work[value_column], errors="coerce")
    work = work.dropna(subset=["date", value_column])
    work = work[work[value_column] > 0].sort_values("date")
    if work.empty:
        return None
    row = work.iloc[-1]
    return float(row[value_column]), row["date"]


def _fx_reference_note(
    bok_fx: pd.DataFrame | None, fred_fx: pd.DataFrame | None,
) -> str | None:
    parts: list[str] = []
    bok = _latest_value_date(bok_fx, "rate_krw_per_usd")
    fred = _latest_value_date(fred_fx, "dexkous")
    if bok is not None:
        parts.append(f"BOK 매매기준율 {bok[1]:%m-%d}")
    if fred is not None:
        parts.append(f"FRED {fred[1]:%m-%d}")
    return " · ".join(parts) or None


def _fx_intraday_displacements(
    latest_value: float, daily_frame: pd.DataFrame | None, value_column: str,
) -> tuple[float | None, float | None]:
    if daily_frame is None or daily_frame.empty or value_column not in daily_frame:
        return None, None
    values = pd.to_numeric(daily_frame[value_column], errors="coerce").dropna()
    deltas: list[float | None] = []
    for window in (5, 20):
        mean = values.iloc[-window:].mean() if len(values) >= window else None
        deltas.append((latest_value / mean - 1.0) * 100.0 if mean else None)
    return deltas[0], deltas[1]


def _curve_frame(
    frame: pd.DataFrame | None, *, tenor_column: str, value_column: str,
    tenor_names: dict[str, str],
) -> pd.DataFrame:
    if (
        frame is None or frame.empty
        or not {"date", tenor_column, value_column}.issubset(frame.columns)
    ):
        return pd.DataFrame()
    work = frame[["date", tenor_column, value_column]].copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work[value_column] = pd.to_numeric(work[value_column], errors="coerce")
    work[tenor_column] = work[tenor_column].astype(str).map(tenor_names)
    work = work.dropna(subset=["date", tenor_column, value_column])
    if work.empty:
        return pd.DataFrame()
    curve = work.pivot_table(
        index="date", columns=tenor_column, values=value_column, aggfunc="last",
    ).reset_index()
    return curve.dropna(subset=["3Y", "10Y"]).sort_values("date", kind="stable")


def _korean_treasury_tile(project_root: Path) -> dict[str, object]:
    """Prefer a newer BOK curve, otherwise identify the current Toss fallback."""
    name = "한국 3Y · 10Y"
    try:
        bok = _curve_frame(
            dsx.load(
                project_root,
                "data/normalized/bok_ecos_kr_treasury_yield_source_observation",
                columns=["date", "tenor", "yield_percent"],
            ),
            tenor_column="tenor", value_column="yield_percent",
            tenor_names={"3Y": "3Y", "10Y": "10Y"},
        )
        toss = _curve_frame(
            dsx.load(
                project_root,
                "data/normalized/kr_treasury_yield_daily",
                columns=["date", "instrument", "close"],
            ),
            tenor_column="instrument", value_column="close",
            tenor_names={"KR_BOND_3Y": "3Y", "KR_BOND_10Y": "10Y"},
        )
        use_bok = not bok.empty and (
            toss.empty or pd.Timestamp(bok["date"].iloc[-1]) >= pd.Timestamp(toss["date"].iloc[-1])
        )
        curve = bok if use_bok else toss
        if curve.empty:
            return _placeholder(name, "한국 국채 보존 데이터 없음")
        latest = curve.iloc[-1]
        source_name = "BOK 국채" if use_bok else "Toss 국채"
        source_label = f"{source_name} {latest['date']:%m-%d}"
        tile = _tile_from_series(
            name, None, curve, "10Y", fmt="{:.2f}%", change_kind="bp",
            window_label=f"{source_name} 일별",
        )
        tile["value"] = f"3Y {float(latest['3Y']):.2f}% · 10Y {float(latest['10Y']):.2f}%"
        tile["source_label"] = source_label
        tile["sub_note"] = source_label
        if len(curve) >= 2:
            previous = curve.iloc[-2]
            change_3y = (float(latest["3Y"]) - float(previous["3Y"])) * 100.0
            change_10y = (float(latest["10Y"]) - float(previous["10Y"])) * 100.0
            tile["change_pct"] = change_10y
            tile["change_label"] = f"3Y {change_3y:+.0f}bp · 10Y {change_10y:+.0f}bp"
        return tile
    except Exception:
        return _placeholder(name, "한국 국채 데이터를 읽을 수 없음")


def build_tiles(project_root: Path) -> list[dict[str, object]]:
    def idx(sym: str):
        frame, _ = _ohlcv(project_root, sym)
        return frame

    fred_fx = dsx.load(project_root, "data/normalized/fred_usd_fx_daily")
    bok_fx = dsx.load(project_root, "data/normalized/bok_ecos_usd_krw_daily")
    bok_latest = _latest_value_date(bok_fx, "rate_krw_per_usd")
    if bok_latest is not None:
        fx = bok_fx.rename(columns={"rate_krw_per_usd": "dexkous"})
        fx_window = "BOK 일별"
        fx_source = "BOK 매매기준율"
    else:
        fx = fred_fx
        fx_window = "FRED 일별"
        fx_source = "FRED"
    fx_note = _fx_reference_note(bok_fx, fred_fx)
    vix = dsx.load(project_root, "data/normalized/fred_vix_daily")
    yields = dsx.load(project_root, "data/normalized/fred_treasury_yield_daily")
    spread = dsx.load(project_root, "data/derived/us_treasury_spread_daily")
    tiles = [
        _tile_from_series("KOSPI", "KOSPI", idx("KOSPI"), "close"),
        _tile_from_series("KOSDAQ", "KOSDAQ", idx("KOSDAQ"), "close"),
        _tile_from_series("밤사이 한국 ETF (EWY)", "EWY", idx("EWY"), "close"),
        _tile_from_series("NASDAQ 100 선물", "NQF", idx("NQF"), "close", fmt="{:,.0f}"),
        _tile_from_series("S&P 500 선물", "ESF", idx("ESF"), "close"),
        _tile_from_series("USD/KRW", None, fx, "dexkous", window_label=fx_window),
        _tile_from_series("미국 10Y", None, yields, "dgs10", fmt="{:.2f}%", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("10Y-2Y 스프레드", None, spread, "spread_10y_2y", fmt="{:+.2f}%p", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("필라델피아 반도체", "SOX", idx("SOX"), "close", fmt="{:,.0f}"),
        _tile_from_series("다우 선물", "YMF", idx("YMF"), "close", fmt="{:,.0f}"),
        (_tile_from_series("달러 인덱스", "DXY", idx("DXY"), "close") if idx("DXY") is not None
         else _placeholder("달러 인덱스", "첫 수집 대기 (DX-Y.NYB)")),
        _tile_from_series("WTI 선물", "WTI", idx("WTI"), "close"),
        _tile_from_series("미국 2Y", None, yields, "dgs2", fmt="{:.2f}%", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("미국 30Y", None, yields, "dgs30", fmt="{:.2f}%", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("VIX", None, vix, "vixcls"),
        _korean_treasury_tile(project_root),
    ]
    for tile in tiles:
        intraday = load_intraday_series(project_root, str(tile["name"]))
        daily_value = tile.pop("_daily_value", None)
        daily_date = tile.pop("_daily_date", None)
        if intraday is not None and len(intraday["points"]) >= 3:
            tile["spark"] = intraday["points"]
            tile["window"] = intraday["window"]
            tile["spark_kind"] = "intraday"
            tile["spark_source"] = intraday["source"]
            latest = intraday["points"][-1]
            latest_value = float(latest["v"])
            latest_clock = format_kst(latest["t"])[-5:]
            if tile["name"] == "USD/KRW":
                tile["value"] = f"{latest_value:,.2f}"
                tile["window"] = f"24h · {latest_clock} KST"
                previous_close = _previous_kst_session_close(intraday["points"])
                if previous_close is None and daily_value is not None:
                    previous_close = float(daily_value)
                tile.pop("change_label", None)
                if previous_close:
                    tile["change_pct"] = (latest_value / previous_close - 1.0) * 100.0
                else:
                    tile.pop("change_pct", None)
                ma5_pct, ma20_pct = _fx_intraday_displacements(latest_value, fx, "dexkous")
                tile["ma5_pct"] = _nan_to_none(ma5_pct)
                tile["ma20_pct"] = _nan_to_none(ma20_pct)
                tile["daily_reference_source"] = fx_source
                tile["daily_reference_date"] = str(daily_date) if daily_date else None
                if fx_note is not None:
                    tile["sub_note"] = fx_note
            elif daily_value is not None and str(latest.get("t", ""))[:10] <= str(daily_date or ""):
                # The intraday observation belongs to the session whose close is already retained
                # (e.g. the 15:00 KOSPI observation after the 20:30 close arrives): the close is the
                # truth, so no "장중" note and the headline change stays close-to-close.
                pass
            elif daily_value is not None and abs(latest_value / float(daily_value) - 1.0) > 0.0005:
                # The retained close is the previous session once newer intraday points exist, so
                # the headline change follows the live value; the close-to-close move is kept
                # separately (a -4% close yesterday must not read as today's move).
                intraday_change = (latest_value / float(daily_value) - 1.0) * 100.0
                if tile.get("change_pct") is not None and "change_label" not in tile:
                    tile["latest_intraday"] = {
                        "value": latest_value, "time": latest["t"],
                        "change_pct": intraday_change,
                    }
                    tile["close_change_pct"] = tile["change_pct"]
                    tile["close_date"] = str(daily_date)[5:10] if daily_date else None
                    tile["change_pct"] = intraday_change
            if tile["name"] == "VIX" and daily_value is not None:
                tile["window"] = f"24h · {latest_clock} KST"
                tile["sub_note"] = (
                    f"FRED 마감 {format_kst(daily_date)} {_compact_number(daily_value)}"
                    f" · 장중 ^VIX {_compact_number(latest_value)}"
                )
            elif tile["name"] == "미국 10Y" and daily_value is not None:
                tile["window"] = f"24h · {latest_clock} KST"
                tile["sub_note"] = (
                    f"FRED 마감 {format_kst(daily_date)} {_compact_number(daily_value)}%"
                    f" · 장중 ^TNX 지수 {_compact_number(latest_value)}"
                )
        else:
            tile["spark_kind"] = "daily"
            if tile.get("spark") and not tile.get("source_label"):
                tile["window"] = "최근 30일 마감"
            if tile["name"] == "VIX" and daily_value is not None:
                tile["sub_note"] = (
                    f"FRED 마감 {format_kst(daily_date)} {_compact_number(daily_value)}"
                )
            elif tile["name"] == "미국 10Y" and daily_value is not None:
                tile["sub_note"] = (
                    f"FRED 마감 {format_kst(daily_date)} {_compact_number(daily_value)}%"
                    " · ^TNX 지수는 장중 관측"
                )
        if tile["name"] == "USD/KRW" and fx_note is not None:
            tile["sub_note"] = fx_note
    return tiles


def build_flows(project_root: Path) -> dict[str, object]:
    from stock_web.api.home_cards import build_lending

    try:
        lending = build_lending(project_root)
    except Exception:
        lending = None
    frame = dsx.load(project_root, "data/normalized/kr_market_investor_trading_daily", filter_expr=(field("market") == "KOSPI"))
    required = {
        "date", "foreigner_buy_amount", "foreigner_sell_amount",
        "institution_buy_amount", "institution_sell_amount",
        "individual_buy_amount", "individual_sell_amount",
    }
    if frame is None or frame.empty or not required.issubset(frame.columns):
        return {"reason": "투자자 매매 데이터 없음", "lending": lending}
    frame = frame.sort_values("date")
    groups = {"외국인": "foreigner", "기관": "institution", "개인": "individual"}
    rows = []
    for label, key in groups.items():
        net = (frame[f"{key}_buy_amount"].astype(float) - frame[f"{key}_sell_amount"].astype(float)) / 1e8
        rows.append({"name": label, "today": _nan_to_none(net.iloc[-1]), "d5": _nan_to_none(net.iloc[-5:].sum()), "d20": _nan_to_none(net.iloc[-20:].sum())})
    balances = []
    try:
        credit = dsx.load(project_root, "data/normalized/kr_credit_balance_daily")
        if credit is not None and not credit.empty and "credit_financing_total" in credit.columns:
            c = credit.dropna(subset=["credit_financing_total"]).sort_values("date")
            v = c["credit_financing_total"].astype(float)
            year = c[c["date"] >= c["date"].max() - pd.Timedelta(days=365)]
            pos = (year["credit_financing_total"] < v.iloc[-1]).mean() * 100
            balances.append({
                "name": "신용잔고", "value": f"{v.iloc[-1] / 1e12:.1f}조 ({c['date'].iloc[-1]:%m-%d})",
                "position": f"1년 상위 {100 - pos:.0f}%", "hot": bool(pos >= 90),
                "d5_pct": _nan_to_none((v.iloc[-1] / v.iloc[-6] - 1) * 100) if len(v) > 6 else None,
                "d20_pct": _nan_to_none((v.iloc[-1] / v.iloc[-21] - 1) * 100) if len(v) > 21 else None,
                "spark": [round(float(x) / 1e12, 3) for x in v.iloc[-20:]],
            })
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "as_of": frame["date"].iloc[-1].strftime("%Y-%m-%d"),
        "market": "KOSPI", "rows": rows, "balances": balances,
        "lending": lending,
    }


def build_health(project_root: Path) -> dict[str, object]:
    from stock_data.gui.health_service import (
        DailyHealthArtifactService,
        summarize_health_artifact,
    )

    service = DailyHealthArtifactService(project_root)
    view = service.load()
    if view.artifact_state != "READY":
        return {"reason": f"데이터 상태 파일을 읽을 수 없습니다 · {view.warning or view.artifact_state}"}
    summary = summarize_health_artifact(view)
    as_of = None
    try:
        payload = json.loads(service.artifact_path.read_text(encoding="utf-8"))
        as_of = payload.get("as_of") or payload.get("generated_at")
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    # Same classes as the 데이터 page: 정시 / 지연 / 실패 (수동·보존·참고 rows are not counted).
    from stock_data.gui.health_service import _effective_display_status

    statuses = [_effective_display_status(row) for row in view.rows]
    return {
        "current": statuses.count("CURRENT"),
        "lag": statuses.count("LATE"),
        "fail": statuses.count("FAILED"),
        "preserved": statuses.count("PRESERVED"),
        "reference": statuses.count("REFERENCE"),
        "labels": {"current": "정시", "late": "지연", "failed": "실패"},
        "as_of": format_kst(as_of),
        "overall": summary.get("overall", "UNKNOWN"),
    }


def _latest_fx(
    project_root: Path,
) -> tuple[pd.DataFrame, float | None, str | None, str | None]:
    from stock_data.gui.query import LocalParquetQuery

    query = LocalParquetQuery(project_root / "data")
    candidates: list[tuple[pd.Timestamp, float, int, str, pd.DataFrame]] = []
    for path, column, priority, label in (
        ("normalized/bok_ecos_usd_krw_daily", "rate_krw_per_usd", 1, "BOK 매매기준율"),
        ("normalized/fred_usd_fx_daily", "dexkous", 0, "FRED"),
    ):
        frame = query.tail(path, rows=400, columns=["date", column])
        latest = _latest_value_date(frame, column)
        if latest is None:
            continue
        normalized = frame.rename(columns={column: "dexkous"}).copy()
        candidates.append((latest[1], latest[0], priority, label, normalized))
    if not candidates:
        return pd.DataFrame(), None, None, None
    observed, value, _priority, label, frame = max(
        candidates, key=lambda item: (item[0], item[2]),
    )
    return (
        frame,
        value,
        observed.date().isoformat(),
        f"{label} {observed:%m-%d}",
    )


def _leverage_multiple(name: str, style: str | None) -> tuple[float, bool]:
    text = f"{style or ''} {name}".upper()
    if "비레버리지" in text:
        return 1.0, True
    if any(token in text for token in ("3배", "3X", "ULTRAPRO", "TQQQ", "SOXL", "UPRO")):
        return 3.0, True
    if any(token in text for token in ("2배", "2X", "레버리지", "ULTRA ", "QLD")):
        return 2.0, True
    return 1.0, bool(style)


def _default_account_period(return_metrics: dict[str, dict[str, object]]) -> str:
    metric = return_metrics.get("3M", {})
    has_three_month_window = metric.get("start_date") is not None and any(
        metric.get(key) is not None
        for key in ("return_pct_modified_dietz", "return_pct_twr", "kospi_return_pct")
    )
    return "3M" if has_three_month_window else "ALL"


def build_account(project_root: Path) -> dict[str, object]:
    from stock_data.gui.account_snapshot_service import (
        LocalAccountPortfolioService,
        LocalAccountSnapshotService,
        LocalAccountSourceSpec,
        build_account_portfolio_presentation,
    )
    from stock_data.gui.services import EquityChartService, US_ETF_CHART_IDENTITIES
    from stock_web.api.account_page import build_account_page_data
    from stock_web.api.home_cards import account_extras

    account_page = build_account_page_data(project_root)
    try:
        extras = account_extras(account_page)
    except Exception:
        extras = {
            "summary_rows": [], "recent_cashflows": [],
            "extras_reason": "계좌 출처 요약을 읽을 수 없습니다.",
        }

    def finish(payload: dict[str, object]) -> dict[str, object]:
        return {**payload, **extras}

    account_summary = account_page["summary"]
    account_rows = account_page.get("rows", [])
    cash_unknown = any(
        bool(row.get("included"))
        and row.get("kind") in {"api", "manual"}
        and row.get("cash_krw") is None
        for row in account_rows
    )
    manual_data = account_page["manual_accounts"]
    manual_accounts = manual_data.get("accounts", [])
    invest_total = float(account_summary.get("invest_total_krw") or 0.0)
    return_metrics = account_page.get("return_metrics", {})
    flow_history = account_page.get("total_asset_history", [])
    flow_benchmark = account_page.get("benchmark", [])
    daily_true_change = account_page.get("daily_true_change_krw")
    month_true_pnl = account_page.get("month_true_pnl_krw")

    candidates = (
        ("toss_self", "Toss Securities · 본인", project_root / "data/normalized/toss_account_snapshot/latest.json"),
        ("kb_self", "KB Securities · 본인", project_root / "data/local/account_snapshots/kb_self.json"),
    )
    sources = []
    for source_id, title, path in candidates:
        if not path.is_file():
            continue
        snapshot = LocalAccountSnapshotService(path).load()
        if snapshot.displays_values:
            sources.append(LocalAccountSourceSpec(source_id, title, path))
    if not sources:
        if not manual_accounts and not account_page["net_worth"].get("exists"):
            return finish({
                "reason": "읽을 수 있는 로컬 계좌 스냅샷이 없습니다.",
                "sources": account_summary.get("sources", []),
            })
        manual_cash = float(manual_data.get("cash_krw") or 0.0)
        exposure_krw = leveraged_krw = short_treasury_krw = 0.0
        exposure_unverified: list[str] = []
        for account in manual_accounts:
            for holding in account.get("valued_positions", []):
                value_krw = holding.get("market_value_krw")
                if value_krw is None:
                    continue
                multiple, verified = _leverage_multiple(
                    str(holding.get("name") or holding.get("ticker") or ""), None,
                )
                if not verified:
                    exposure_unverified.append(str(holding.get("name") or holding.get("ticker")))
                exposure_krw += float(value_krw) * multiple
                if multiple > 1.0:
                    leveraged_krw += float(value_krw)
                name_upper = f"{holding.get('ticker', '')} {holding.get('name', '')}".upper()
                if any(token in name_upper for token in ("SGOV", "BIL", "SHV", "단기국채", "SHORT TREASURY")):
                    short_treasury_krw += float(value_krw)
        manual_usd_krw = sum(
            float(account.get("value_krw") or 0.0)
            for account in manual_accounts if account.get("currency") == "USD"
        )
        usdkrw = account_summary.get("fx_krw_per_usd")
        return finish({
            "total_krw": invest_total,
            "invest_total_krw": invest_total,
            "net_worth_krw": account_summary.get("net_worth_krw"),
            "net_worth_as_of": account_summary.get("net_worth_as_of"),
            "net_worth_as_of_label": account_summary.get("net_worth_as_of_label"),
            "sources": account_summary.get("sources", []),
            "day_change_pct": None, "day_change_krw": daily_true_change,
            "daily_true_change_krw": daily_true_change,
            "month_true_pnl_krw": month_true_pnl,
            "broker_reported_pnl_krw": account_summary.get("broker_reported_pnl_krw"),
            "return_metrics": return_metrics,
            "period_label": _default_account_period(return_metrics),
            "period_pct": return_metrics.get("3M", {}).get("return_pct_modified_dietz"),
            "kospi_period_pct": return_metrics.get("3M", {}).get("kospi_return_pct"),
            "ytd_pct": return_metrics.get("YTD", {}).get("return_pct_modified_dietz"),
            "cash_pct": (
                manual_cash / invest_total * 100.0
                if invest_total and not cash_unknown else None
            ),
            "cash_unknown": cash_unknown,
            "usd_assets_usd": manual_usd_krw / float(usdkrw) if usdkrw else None,
            "usd_assets_krw": manual_usd_krw,
            "usdkrw": usdkrw,
            "usdkrw_as_of": account_summary.get("fx_as_of"),
            "usdkrw_as_of_label": account_summary.get("fx_as_of_label"),
            "fx_effect_pct": None, "equity_effect_pct": None,
            "effective_exposure_pct": exposure_krw / invest_total * 100.0 if invest_total else None,
            "leveraged_weight_pct": leveraged_krw / invest_total * 100.0 if invest_total else None,
            "short_treasury_pct": short_treasury_krw / invest_total * 100.0 if invest_total else None,
            "exposure_unverified": list(dict.fromkeys(exposure_unverified)),
            "history": flow_history, "benchmark": flow_benchmark,
            "footnote": "입출금은 내 계좌 페이지에서 기록 · 기록이 없으면 변동 전체를 손익으로 간주",
        })

    portfolio = LocalAccountPortfolioService(
        tuple(sources), history_root=project_root / "data/local/account_value_history",
    ).load()
    presentation = build_account_portfolio_presentation(portfolio)
    if not presentation.available:
        return finish({"reason": "로컬 계좌 스냅샷이 표시 가능한 상태가 아닙니다."})

    fx, usdkrw, usdkrw_as_of, usdkrw_source = _latest_fx(project_root)
    amounts = {"KRW": 0.0, "USD": 0.0}
    cash = {"KRW": 0.0, "USD": 0.0}
    for entry in portfolio.entries:
        snapshot = entry.snapshot
        if not snapshot.displays_values:
            continue
        if snapshot.currency:
            cash_value = snapshot.cash_balance
            if cash_value is None:
                cash_value = snapshot.available_cash
            cash_value = float(cash_value or 0.0)
            total = snapshot.total_assets
            if total is None and snapshot.securities_value is not None:
                total = float(snapshot.securities_value) + cash_value
            if snapshot.currency in amounts and total is not None:
                amounts[snapshot.currency] += float(total)
                cash[snapshot.currency] += cash_value
        else:
            for summary in snapshot.currency_summaries:
                if summary.currency not in amounts or summary.securities_value is None:
                    continue
                cash_value = float(summary.cash_buying_power or 0.0)
                amounts[summary.currency] += float(summary.securities_value) + cash_value
                cash[summary.currency] += cash_value
    if amounts["USD"] and usdkrw is None:
        return finish({"reason": "USD 자산은 있으나 보존된 USD/KRW 환율을 확인할 수 없습니다."})
    total_krw = amounts["KRW"] + amounts["USD"] * float(usdkrw or 0.0)
    if total_krw <= 0:
        return finish({"reason": "로컬 계좌 총액을 안전하게 계산할 수 없습니다."})

    us_identities = {identity.symbol: identity for identity in US_ETF_CHART_IDENTITIES}
    equity = EquityChartService(project_root)
    exposure_krw = 0.0
    leveraged_krw = 0.0
    short_treasury_krw = 0.0
    exposure_unverified: list[str] = []
    for holding in presentation.holdings:
        if holding.market_value is None or holding.currency not in {"KRW", "USD"}:
            continue
        style = None
        if holding.currency == "USD":
            identity = us_identities.get(holding.symbol.upper())
            style = getattr(identity, "leverage_style", None)
        else:
            try:
                matches = equity.search(holding.symbol, limit=5).matches
                identity = next((item for item in matches if item.symbol == holding.symbol), None)
                style = getattr(identity, "leverage_style", None)
            except (KeyError, OSError, PermissionError, TypeError, ValueError):
                style = None
        multiple, verified = _leverage_multiple(holding.name, style)
        if not verified:
            exposure_unverified.append(holding.name)
        value_krw = float(holding.market_value) * (float(usdkrw) if holding.currency == "USD" else 1.0)
        exposure_krw += value_krw * multiple
        if multiple > 1.0:
            leveraged_krw += value_krw
        name_upper = f"{holding.symbol} {holding.name}".upper()
        if any(token in name_upper for token in ("SGOV", "BIL", "SHV", "단기국채", "SHORT TREASURY")):
            short_treasury_krw += value_krw

    history = flow_history
    benchmark = flow_benchmark
    day_change_krw = day_change_pct = fx_effect_pct = equity_effect_pct = None
    if len(history) >= 2:
        previous = float(history[-2]["v"])
        day_change_krw = daily_true_change
        day_change_pct = day_change_krw / previous * 100.0 if previous and day_change_krw is not None else None

    period_pct = return_metrics.get("3M", {}).get("return_pct_modified_dietz")
    ytd_pct = return_metrics.get("YTD", {}).get("return_pct_modified_dietz")
    kospi_period_pct = return_metrics.get("3M", {}).get("kospi_return_pct")

    unique_unverified = list(dict.fromkeys(exposure_unverified))
    manual_cash_krw = float(manual_data.get("cash_krw") or 0.0)
    manual_usd_krw = 0.0
    manual_exposure_krw = 0.0
    manual_leveraged_krw = 0.0
    manual_short_treasury_krw = 0.0
    for account in manual_accounts:
        if account.get("currency") == "USD":
            manual_usd_krw += float(account.get("value_krw") or 0.0)
        for holding in account.get("valued_positions", []):
            value_krw = holding.get("market_value_krw")
            if value_krw is None:
                continue
            multiple, verified = _leverage_multiple(
                str(holding.get("name") or holding.get("ticker") or ""), None,
            )
            if not verified:
                unique_unverified.append(str(holding.get("name") or holding.get("ticker")))
            manual_exposure_krw += float(value_krw) * multiple
            if multiple > 1.0:
                manual_leveraged_krw += float(value_krw)
            name_upper = f"{holding.get('ticker', '')} {holding.get('name', '')}".upper()
            if any(token in name_upper for token in ("SGOV", "BIL", "SHV", "단기국채", "SHORT TREASURY")):
                manual_short_treasury_krw += float(value_krw)
    usd_assets_krw = amounts["USD"] * float(usdkrw or 0.0) + manual_usd_krw
    return finish({
        "total_krw": invest_total,
        "invest_total_krw": invest_total,
        "net_worth_krw": account_summary.get("net_worth_krw"),
        "net_worth_as_of": account_summary.get("net_worth_as_of"),
        "net_worth_as_of_label": account_summary.get("net_worth_as_of_label"),
        "sources": account_summary.get("sources", []),
        "day_change_pct": day_change_pct,
        "day_change_krw": day_change_krw,
        "daily_true_change_krw": daily_true_change,
        "month_true_pnl_krw": month_true_pnl,
        "broker_reported_pnl_krw": account_summary.get("broker_reported_pnl_krw"),
        "return_metrics": return_metrics,
        "period_label": _default_account_period(return_metrics),
        "period_pct": period_pct,
        "kospi_period_pct": kospi_period_pct,
        "ytd_pct": ytd_pct,
        "cash_pct": (
            cash["KRW"] + cash["USD"] * float(usdkrw or 0.0) + manual_cash_krw
        ) / invest_total * 100.0 if invest_total and not cash_unknown else None,
        "cash_unknown": cash_unknown,
        "usd_assets_usd": usd_assets_krw / float(usdkrw) if usdkrw else None,
        "usd_assets_krw": usd_assets_krw,
        "usdkrw": usdkrw,
        "usdkrw_as_of": usdkrw_as_of,
        "usdkrw_as_of_label": format_kst(usdkrw_as_of),
        "usdkrw_source": usdkrw_source,
        "fx_effect_pct": fx_effect_pct,
        "equity_effect_pct": equity_effect_pct,
        "effective_exposure_pct": (exposure_krw + manual_exposure_krw) / invest_total * 100.0 if invest_total else None,
        "leveraged_weight_pct": (leveraged_krw + manual_leveraged_krw) / invest_total * 100.0 if invest_total else None,
        "short_treasury_pct": (short_treasury_krw + manual_short_treasury_krw) / invest_total * 100.0 if invest_total else None,
        "exposure_unverified": list(dict.fromkeys(unique_unverified)),
        "history": history,
        "benchmark": benchmark,
        "footnote": "입출금은 내 계좌 페이지에서 기록 · 기록이 없으면 변동 전체를 손익으로 간주",
    })


def build_derivatives(project_root: Path) -> dict[str, object]:
    from stock_data.gui.health_service import DailyHealthArtifactService
    from stock_data.gui.services import DashboardService
    from stock_data.gui.us_option_pcr_adapter import current_us_option_pcr_scope_views

    service = DashboardService(project_root)
    try:
        metrics = service.dashboard_metrics(DailyHealthArtifactService(project_root).load())
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        metrics = {}

    def unavailable(reason: object, *, us_row: bool = False) -> str:
        if us_row:
            return "미표시"
        text = str(reason or "보존 데이터 없음").strip()
        if text.isascii() and any(character.isalpha() for character in text):
            return "출처 검증 전 · 미표시"
        if text.isascii() and len(text) > 40:
            return "출처 검증 전 · 미표시"
        return text

    def display(key: str, pattern: str) -> str:
        metric = metrics.get(key)
        if metric is not None and metric.displays_value and metric.value is not None:
            value = pattern.format(float(metric.value))
            return f"{value} · {format_kst(metric.as_of)}" if metric.as_of else value
        return unavailable(getattr(metric, "unavailable_reason", None))

    from stock_web.api.home_cards import build_vix_term_structure_rows

    vix_rows = build_vix_term_structure_rows(project_root)
    try:
        cboe_views = current_us_option_pcr_scope_views()
        cboe_reason = cboe_views[0].reason if cboe_views else None
    except Exception:
        cboe_reason = None
    return {"groups": [
        {"title": "한국 · KOSPI200", "rows": [
            ["선물 Basis", display("KOSPI200_BASIS", "{:+.2f}")],
            ["거래량 PCR", display("VOLUME_PCR", "{:.3f}")],
            ["미결제약정 PCR", display("OI_PCR", "{:.3f}")],
            ["LS 선물 외국인 순계약", display("LS_FUTURES_FOREIGN_NET", "{:+,.0f}")],
        ]},
        {"title": "미국", "rows": [
            *vix_rows,
            ["CBOE PCR", unavailable(cboe_reason, us_row=True)],
        ]},
    ]}


def build_schedule(project_root: Path) -> dict[str, object]:
    from stock_web.api.home_cards import build_schedule as build_schedule_card

    return build_schedule_card(project_root)


def build_brief(project_root: Path) -> dict[str, object] | None:
    artifact_root = project_root / "artifacts"
    if not artifact_root.is_dir():
        return None
    paths = {
        path for pattern in ("*morning*brief*", "*market*brief*")
        for path in artifact_root.rglob(pattern)
        if path.is_file() and path.suffix.lower() in {".json", ".txt", ".md"}
    }
    if not paths:
        return None
    path = max(paths, key=lambda item: item.stat().st_mtime)
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw_lines = payload.get("lines")
            if not isinstance(raw_lines, list):
                text = payload.get("text") or payload.get("content")
                raw_lines = str(text).splitlines() if isinstance(text, str) else []
            created = payload.get("generated_at") or payload.get("created_at") or payload.get("as_of")
            source = payload.get("source") or path.name
        else:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
            created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            source = path.name
        lines = [str(line).strip().lstrip("-· ") for line in raw_lines if str(line).strip()]
        if not lines:
            return None
        created_label = format_kst(created) if created else "생성 시각 미상"
        return {"lines": lines, "meta": f"{created_label} · {source}"}
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None


def build_public_scanner(
    project_root: Path, *, avg_value_20d_min: float = 1_000_000_000.0,
    market_cap_min: float = 100_000_000_000.0,
    apply_liquidity_filter: bool = True,
) -> dict[str, object]:
    """Build the scanner without reading conditions or reading/writing its user cache."""
    from stock_web.api import scanner as scanner_api

    with _PUBLIC_SCANNER_LOCK:
        original_load = scanner_api.load_conditions
        original_read = scanner_api._read_cache
        original_write = scanner_api._write_cache
        scanner_api.load_conditions = lambda _root: {
            "schema_version": 1, "conditions": [],
        }
        scanner_api._read_cache = lambda *_args, **_kwargs: None
        scanner_api._write_cache = lambda *_args, **_kwargs: None
        try:
            return scanner_api.build_scanner(
                project_root,
                avg_value_20d_min=avg_value_20d_min,
                market_cap_min=market_cap_min,
                apply_liquidity_filter=apply_liquidity_filter,
            )
        finally:
            scanner_api.load_conditions = original_load
            scanner_api._read_cache = original_read
            scanner_api._write_cache = original_write


def build_scanner(project_root: Path, *, public_mode: bool = False) -> dict[str, object]:
    from stock_web.api.scanner import build_scanner as build_full_scanner

    result = (
        build_public_scanner(project_root)
        if public_mode else build_full_scanner(project_root)
    )
    return {
        key: result.get(key)
        for key in ("status", "as_of", "count", "rule", "top", "reason")
        if key in result
    }


def build_watchlist(project_root: Path, *, public_mode: bool = False) -> dict[str, object]:
    """Return Home watchlist rows with optional retained investor summaries."""
    if public_mode:
        from stock_web.api.stocks_page import build_home_watchlist

        return build_home_watchlist(project_root, public_mode=True)
    from stock_web.api.home_cards import build_watchlist as build_watchlist_card

    return build_watchlist_card(project_root)


def build_chart_symbols(project_root: Path) -> list[dict[str, str]]:
    """Return the exact retained-data symbol list shared by Home and Market."""
    return [
        {"symbol": symbol, "name": INDEX_SOURCES[symbol][3]}
        for symbol in (
            "KOSPI", "KOSDAQ", "KOSPI200", "SP500", "ESF", "NASDAQ", "NDX",
            "NQF", "DOW", "YMF", "SOX", "SOXX", "EWY", "DXY", "WTI", "GOLD",
        )
        if (_frame := _ohlcv(project_root, symbol)[0]) is not None and not _frame.empty
    ]


def _normalize_regime_cash_label(
    regime: dict[str, object], account: dict[str, object],
) -> dict[str, object]:
    """Keep an unknown cash balance separate from numeric Treasury weight."""
    if not account.get("cash_unknown"):
        return regime
    rules = regime.get("rules")
    rows = rules.get("rows") if isinstance(rules, dict) else None
    if not isinstance(rows, list):
        return regime
    try:
        short_treasury = float(account.get("short_treasury_pct") or 0.0)
    except (TypeError, ValueError):
        short_treasury = 0.0
    for row in rows:
        if isinstance(row, list) and len(row) >= 3 and row[0] == "현금 · 단기국채":
            row[1] = f"현금 — · 단기국채 {short_treasury:.0f}%"
            row[2] = ""
    return regime


def _attach_research_current(
    project_root: Path, regime: dict[str, object],
) -> dict[str, object]:
    """Project the research artifact's active KR state into the Home regime card."""
    from stock_web.api.research_page import build_current_status_lines

    regime["research_current"] = build_current_status_lines(project_root)
    return regime


def _build_public_regime(project_root: Path) -> dict[str, object]:
    """Build public market evidence without resolving the private rules file."""
    from stock_web.api import regime as regime_api

    with _PUBLIC_REGIME_LOCK:
        original_build_rules = regime_api.build_rules
        regime_api.build_rules = lambda _account, _markets, _root=None: None
        try:
            return _attach_research_current(
                project_root, regime_api.build_regime(project_root, {"guest": True}),
            )
        finally:
            regime_api.build_rules = original_build_rules


def _safe_home_section(builder: Callable[[], object], reason: str) -> object:
    try:
        return builder()
    except Exception:
        return {"reason": reason}


def _build_home_payload_uncached(
    project_root: Path, *, public_mode: bool = False,
) -> dict[str, object]:
    from stock_web.api.regime import build_regime

    sections: dict[str, object] = {}
    if public_mode:
        sections["account"] = {"guest": True}
        sections["regime"] = _safe_home_section(
            lambda: _build_public_regime(project_root),
            "시장 국면 근거를 읽을 수 없습니다.",
        )
    else:
        account = _safe_home_section(
            lambda: build_account(project_root), "계좌 데이터를 읽을 수 없습니다.",
        )
        if not isinstance(account, dict):
            account = {"reason": "계좌 데이터를 읽을 수 없습니다."}
        sections["account"] = account
        sections["regime"] = _safe_home_section(
            lambda: _attach_research_current(
                project_root,
                _normalize_regime_cash_label(build_regime(project_root, account), account),
            ),
            "시장 국면 근거를 읽을 수 없습니다.",
        )
    sections["derivatives"] = _safe_home_section(
        lambda: build_derivatives(project_root), "파생 지표를 읽을 수 없습니다.",
    )
    sections["health"] = build_health(project_root)
    sections["schedule"] = _safe_home_section(
        lambda: build_schedule(project_root), "오늘 브리핑을 읽을 수 없습니다.",
    )
    if not public_mode:
        brief = build_brief(project_root)
        if brief is not None:
            sections["brief"] = brief
    sections["scanner"] = build_scanner(project_root, public_mode=public_mode)
    sections["watchlist"] = _safe_home_section(
        lambda: build_watchlist(project_root, public_mode=public_mode),
        "관심종목을 읽을 수 없습니다.",
    )
    sections["tiles"] = _safe_home_section(
        lambda: build_tiles(project_root), "시장 지표를 읽을 수 없습니다.",
    )
    sections["chart_symbols"] = build_chart_symbols(project_root)
    sections["flows"] = _safe_home_section(
        lambda: build_flows(project_root), "수급 데이터를 읽을 수 없습니다.",
    )
    kospi = _ohlcv(project_root, "KOSPI")[0]
    as_of = kospi["date"].iloc[-1].strftime("%Y-%m-%d") if kospi is not None and not kospi.empty else None
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_label": f"한국장 마감 기준 {format_kst(as_of)}" if as_of else "",
        "sections": sections,
    }


_HOME_REFRESH_LOCK = threading.Lock()
_HOME_REFRESHING: set[str] = set()


def _home_cache_key(project_root: Path, *, public_mode: bool) -> str:
    base = str(Path(project_root).resolve())
    return f"{base}|guest" if public_mode else base


def clear_home_cache(project_root: Path, *, public_mode: bool = False) -> None:
    _HOME_CACHE.pop(_home_cache_key(project_root, public_mode=public_mode), None)


def _refresh_home_payload(root: Path, key: str, public_mode: bool) -> None:
    try:
        payload = (
            _build_home_payload_uncached(root, public_mode=True)
            if public_mode else _build_home_payload_uncached(root)
        )
        _HOME_CACHE[key] = (time.monotonic(), payload)
    except Exception as error:  # a failed background rebuild keeps the last good document
        print(f"stock_web: home payload refresh failed: {type(error).__name__}: {error}", file=sys.stderr)
    finally:
        with _HOME_REFRESH_LOCK:
            _HOME_REFRESHING.discard(key)


def build_home_payload(
    project_root: Path, *, public_mode: bool | None = None,
) -> dict[str, object]:
    """Build the home document with a stale-while-revalidate cache.

    Building takes several seconds (many retained datasets), so a request never waits for a
    rebuild once a document exists: a stale document is returned immediately and one
    background thread refreshes it. Only the very first request after startup builds inline
    (`warm_home_payload` is used at app startup to avoid even that).
    """
    root = Path(project_root).resolve()
    if public_mode is None:
        public_mode = os.environ.get("STOCK_WEB_PUBLIC_MODE") == "1"
    key = _home_cache_key(root, public_mode=public_mode)
    now = time.monotonic()
    cached = _HOME_CACHE.get(key)
    if cached is not None:
        if now - cached[0] >= _HOME_CACHE_TTL_SECONDS:
            with _HOME_REFRESH_LOCK:
                start = key not in _HOME_REFRESHING
                if start:
                    _HOME_REFRESHING.add(key)
            if start:
                threading.Thread(
                    target=_refresh_home_payload, args=(root, key, public_mode), daemon=True,
                ).start()
        return cached[1]
    payload = (
        _build_home_payload_uncached(root, public_mode=True)
        if public_mode else _build_home_payload_uncached(root)
    )
    _HOME_CACHE[key] = (time.monotonic(), payload)
    return payload


def warm_home_payload(
    project_root: Path, *, public_mode: bool | None = None,
    interval_seconds: float | None = None,
) -> threading.Thread:
    """Build the home document off the request path; optionally keep it fresh forever."""
    root = Path(project_root).resolve()
    if public_mode is None:
        public_mode = os.environ.get("STOCK_WEB_PUBLIC_MODE") == "1"

    def run() -> None:
        while True:
            try:
                build_home_payload(root, public_mode=public_mode)
            except Exception as error:
                print(f"stock_web: home payload warmup failed: {type(error).__name__}: {error}", file=sys.stderr)
            if interval_seconds is None:
                return
            time.sleep(interval_seconds)

    thread = threading.Thread(target=run, name="home-payload-warmup", daemon=True)
    thread.start()
    return thread
