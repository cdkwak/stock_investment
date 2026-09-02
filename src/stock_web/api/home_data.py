"""Assemble the home-page payload from retained local data.

Each section is independent: a missing or unverified dataset yields an
absent section (or a ``reason``), never a substituted number.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stock_web.api import datasets as dsx
from stock_web.api.datasets import field

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
    if symbol.isdigit() and len(symbol) == 6:
        frame = dsx.load(project_root, "data/normalized/kr_equity_price_daily", filter_expr=(field("symbol") == symbol))
        return frame, _stock_name(project_root, symbol)
    return None, symbol


_NAME_CACHE: dict[str, str] = {}


def _stock_name(project_root: Path, symbol: str) -> str:
    if not _NAME_CACHE:
        master = dsx.load(project_root, "data/normalized/kr_equity_master")
        if master is not None and {"symbol", "name"} <= set(master.columns):
            _NAME_CACHE.update(dict(zip(master["symbol"].astype(str), master["name"].astype(str))))
    return _NAME_CACHE.get(symbol, symbol)


def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close = out["close"].astype(float)
    for w in (5, 20, 60, 120):
        out[f"ma{w}"] = close.rolling(w).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, float("nan"))
    out["rsi14"] = 100 - 100 / (1 + rs)
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
        "as_of": str(dates.iloc[-1]), "candles": candles, "ma": ma, "stats": stats,
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
    tile: dict[str, object] = {
        "name": name, "symbol": symbol, "value": fmt.format(last),
        "ma5_pct": _nan_to_none((last / ma5 - 1) * 100) if ma5 else None,
        "ma20_pct": _nan_to_none((last / ma20 - 1) * 100) if ma20 else None,
        "spark": [round(float(v), 4) for v in values.iloc[-30:]],
        "window": f"{window_label} · {series['date'].iloc[-1]:%m-%d}",
    }
    if change_kind == "pct":
        tile["change_pct"] = _nan_to_none((last / prev - 1) * 100)
    else:  # basis points for yields
        bp = (last - prev) * 100
        tile["change_pct"] = _nan_to_none(bp)
        tile["change_label"] = f"{bp:+.0f}bp"
    return tile


def _placeholder(name: str, note: str) -> dict[str, object]:
    return {"name": name, "symbol": None, "value": "—", "note": note}


def build_tiles(project_root: Path) -> list[dict[str, object]]:
    def idx(sym: str):
        frame, _ = _ohlcv(project_root, sym)
        return frame

    fx = dsx.load(project_root, "data/normalized/fred_usd_fx_daily")
    vix = dsx.load(project_root, "data/normalized/fred_vix_daily")
    yields = dsx.load(project_root, "data/normalized/fred_treasury_yield_daily")
    spread = dsx.load(project_root, "data/derived/us_treasury_spread_daily")
    tiles = [
        _tile_from_series("KOSPI", "KOSPI", idx("KOSPI"), "close"),
        _tile_from_series("KOSDAQ", "KOSDAQ", idx("KOSDAQ"), "close"),
        _placeholder("밤사이 한국 ETF (EWY)", "수집 추가 필요"),
        _tile_from_series("NASDAQ 100 선물", "NQF", idx("NQF"), "close", fmt="{:,.0f}"),
        _tile_from_series("S&P 500", "SP500", idx("SP500"), "close"),
        _tile_from_series("USD/KRW", None, fx, "dexkous", window_label="FRED 일별"),
        _tile_from_series("미국 10Y", None, yields, "dgs10", fmt="{:.2f}%", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("10Y-2Y 스프레드", None, spread, "spread_10y_2y", fmt="{:+.2f}%p", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("SOXX (반도체 ETF)", "SOXX", idx("SOXX"), "close"),
        _placeholder("다우 선물", "수집 추가 필요"),
        _placeholder("달러 인덱스 선물", "수집 추가 필요"),
        _tile_from_series("WTI 선물", "WTI", idx("WTI"), "close"),
        _tile_from_series("미국 2Y", None, yields, "dgs2", fmt="{:.2f}%", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("미국 30Y", None, yields, "dgs30", fmt="{:.2f}%", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("VIX (FRED 마감)", None, vix, "vixcls"),
        _placeholder("한국 3Y · 10Y", "한국은행 확정 검증 후 표시"),
    ]
    return tiles


def build_flows(project_root: Path) -> dict[str, object]:
    frame = dsx.load(project_root, "data/normalized/kr_market_investor_trading_daily", filter_expr=(field("market") == "KOSPI"))
    if frame is None or frame.empty:
        return {"reason": "투자자 매매 데이터 없음"}
    frame = frame.sort_values("date")
    groups = {"외국인": "foreigner", "기관": "institution", "개인": "individual"}
    rows = []
    for label, key in groups.items():
        net = (frame[f"{key}_buy_amount"].astype(float) - frame[f"{key}_sell_amount"].astype(float)) / 1e8
        rows.append({"name": label, "today": _nan_to_none(net.iloc[-1]), "d5": _nan_to_none(net.iloc[-5:].sum()), "d20": _nan_to_none(net.iloc[-20:].sum())})
    balances = []
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
    return {"as_of": frame["date"].iloc[-1].strftime("%Y-%m-%d"), "market": "KOSPI", "rows": rows, "balances": balances}


def build_health(project_root: Path) -> dict[str, object] | None:
    import json
    root = project_root / "artifacts/daily_health"
    files = sorted(root.glob("universe_data_v2_*.json"), key=lambda p: p.stat().st_mtime) if root.is_dir() else []
    if not files:
        return None
    try:
        report = json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None
    rows = [r for r in report.get("datasets", []) if isinstance(r, dict) and r.get("automation_enabled") is True]
    fresh = [str(r.get("freshness")) for r in rows]
    return {
        "current": fresh.count("CURRENT"), "lag": fresh.count("EXPECTED_LAG"),
        "fail": len([f for f in fresh if f not in ("CURRENT", "EXPECTED_LAG")]),
        "as_of": report.get("as_of"), "source": files[-1].name,
    }


def build_watchlist(project_root: Path) -> dict[str, object]:
    import json
    path = project_root / "artifacts/local_user/watchlists.json"
    if not path.is_file():
        return {"reason": "관심종목 파일 없음 · 종목 페이지에서 추가"}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"reason": "관심종목 파일을 읽을 수 없음"}
    items = [it for lst in state.get("lists", []) for it in lst.get("items", []) if isinstance(it, dict)]
    rows = []
    for it in items[:12]:
        symbol = str(it.get("symbol") or "")
        chart = build_chart_payload(project_root, symbol=symbol, range_key="3M") if symbol else {}
        candles = chart.get("candles") or []
        last = candles[-1] if candles else None
        prev = candles[-2] if len(candles) > 1 else None
        stats = chart.get("stats") or {}
        rows.append({
            "name": it.get("name") or symbol, "symbol": symbol, "held": False, "weight_pct": None,
            "price": f"{last['c']:,.0f}" if last and last.get("c") is not None else None,
            "change_pct": _nan_to_none((last["c"] / prev["c"] - 1) * 100) if last and prev and prev.get("c") else None,
            "drawdown_pct": stats.get("drawdown_pct"), "rsi14": stats.get("rsi14"),
            "flow_foreign": None, "flow_inst": None, "flow_indiv": None,
            "flag": "RSI 30 이하" if (stats.get("rsi14") or 100) <= 30 else "",
            "as_of": chart.get("as_of"),
        })
    return {"rows": rows, "held_count": 0, "watch_count": len(rows),
            "note": "보유 비중은 계좌 연결 후 · 종목별 수급은 수집 데이터 없음"}


def build_home_payload(project_root: Path) -> dict[str, object]:
    sections: dict[str, object] = {}
    sections["watchlist"] = build_watchlist(project_root)
    health = build_health(project_root)
    if health:
        sections["health"] = health
    sections["tiles"] = build_tiles(project_root)
    sections["chart_symbols"] = [
        {"symbol": s, "name": INDEX_SOURCES[s][3]} for s in ("KOSPI", "KOSDAQ", "KOSPI200", "SP500", "NASDAQ", "NDX", "NQF", "SOXX", "WTI", "GOLD")
        if _ohlcv(project_root, s)[0] is not None
    ]
    sections["flows"] = build_flows(project_root)
    kospi = _ohlcv(project_root, "KOSPI")[0]
    as_of = kospi["date"].iloc[-1].strftime("%Y-%m-%d") if kospi is not None and not kospi.empty else None
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of_label": f"한국장 마감 기준 {as_of}" if as_of else "",
        "sections": sections,
    }
