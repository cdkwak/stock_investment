"""Assemble the home-page payload from retained local data.

Each section is independent: a missing or unverified dataset yields an
absent section (or a ``reason``), never a substituted number.
"""
from __future__ import annotations

import math
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stock_web.api import datasets as dsx
from stock_web.api.datasets import field

_HOME_CACHE_TTL_SECONDS = 60.0
_HOME_CACHE: dict[str, tuple[float, dict[str, object]]] = {}

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
    if symbol.isdigit() and len(symbol) == 6:
        frame = dsx.load(project_root, "data/normalized/kr_equity_price_daily", filter_expr=(field("symbol") == symbol))
        return frame, _stock_name(project_root, symbol)
    return None, symbol


_NAME_CACHE: dict[str, dict[str, str]] = {}


def _stock_name(project_root: Path, symbol: str) -> str:
    key = str(project_root.resolve())
    names = _NAME_CACHE.setdefault(key, {})
    if not names:
        master = dsx.load(project_root, "data/normalized/kr_equity_master")
        if master is not None and {"symbol", "name"} <= set(master.columns):
            names.update(dict(zip(master["symbol"].astype(str), master["name"].astype(str))))
    return names.get(symbol, symbol)


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
        _tile_from_series("밤사이 한국 ETF (EWY)", "EWY", idx("EWY"), "close"),
        _tile_from_series("NASDAQ 100 선물", "NQF", idx("NQF"), "close", fmt="{:,.0f}"),
        _tile_from_series("S&P 500 선물", "ESF", idx("ESF"), "close"),
        _tile_from_series("USD/KRW", None, fx, "dexkous", window_label="FRED 일별"),
        _tile_from_series("미국 10Y", None, yields, "dgs10", fmt="{:.2f}%", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("10Y-2Y 스프레드", None, spread, "spread_10y_2y", fmt="{:+.2f}%p", change_kind="bp", window_label="FRED 일별"),
        _tile_from_series("필라델피아 반도체", "SOX", idx("SOX"), "close", fmt="{:,.0f}"),
        _tile_from_series("다우 선물", "YMF", idx("YMF"), "close", fmt="{:,.0f}"),
        (_tile_from_series("달러 인덱스", "DXY", idx("DXY"), "close") if idx("DXY") is not None
         else _placeholder("달러 인덱스", "첫 수집 대기 (DX-Y.NYB)")),
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
    current = int(summary.get("managed_current", 0))
    lag = int(summary.get("managed_expected_lag", 0))
    managed = int(summary.get("managed_total", 0))
    return {
        "current": current,
        "lag": lag,
        "fail": max(0, managed - current - lag),
        "as_of": as_of,
        "overall": summary.get("overall", "UNKNOWN"),
    }


def _latest_fx(project_root: Path) -> tuple[pd.DataFrame, float | None, str | None]:
    from stock_data.gui.query import LocalParquetQuery

    frame = LocalParquetQuery(project_root / "data").tail(
        "normalized/fred_usd_fx_daily", rows=400,
        columns=["date", "dexkous"],
    )
    if frame.empty:
        return frame, None, None
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["dexkous"] = pd.to_numeric(frame["dexkous"], errors="coerce")
    frame = frame.dropna(subset=["date", "dexkous"]).sort_values("date")
    if frame.empty:
        return frame, None, None
    return frame, float(frame["dexkous"].iloc[-1]), frame["date"].iloc[-1].date().isoformat()


def _leverage_multiple(name: str, style: str | None) -> tuple[float, bool]:
    text = f"{style or ''} {name}".upper()
    if "비레버리지" in text:
        return 1.0, True
    if any(token in text for token in ("3배", "3X", "ULTRAPRO", "TQQQ", "SOXL", "UPRO")):
        return 3.0, True
    if any(token in text for token in ("2배", "2X", "레버리지", "ULTRA ", "QLD")):
        return 2.0, True
    return 1.0, bool(style)


def _account_history(
    histories: tuple[object, ...], fx: pd.DataFrame,
) -> tuple[list[dict[str, object]], list[float | None]]:
    def day(value: object) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        return timestamp.normalize()

    series: list[tuple[str, pd.Series]] = []
    for index, history in enumerate(histories):
        points = getattr(history, "points", ())
        if not points:
            continue
        frame = pd.DataFrame({
            "date": [day(point.date) for point in points],
            "value": [float(point.total_assets) for point in points],
        }).sort_values("date").drop_duplicates("date", keep="last")
        series.append((
            str(getattr(history, "currency", "")),
            frame.set_index("date")["value"].rename(f"series_{index}"),
        ))
    if not series:
        return [], []
    dates = sorted(set().union(*(set(values.index) for _currency, values in series)))
    table = pd.DataFrame(index=pd.DatetimeIndex(dates))
    currencies: dict[str, str] = {}
    for index, (currency, values) in enumerate(series):
        column = f"series_{index}"
        table[column] = values.reindex(table.index).ffill()
        currencies[column] = currency
    table = table.dropna()
    if table.empty:
        return [], []

    fx_values: list[float | None] = []
    totals: list[float] = []
    fx_indexed = fx.set_index("date")["dexkous"] if not fx.empty else pd.Series(dtype=float)
    for date, row in table.iterrows():
        prior_fx = fx_indexed.loc[fx_indexed.index <= date]
        rate = float(prior_fx.iloc[-1]) if not prior_fx.empty else None
        fx_values.append(rate)
        total = 0.0
        valid = True
        for column, value in row.items():
            currency = currencies[column]
            if currency == "KRW":
                total += float(value)
            elif currency == "USD" and rate is not None:
                total += float(value) * rate
            else:
                valid = False
                break
        totals.append(total if valid else float("nan"))
    history = [
        {"t": date.date().isoformat(), "v": float(total)}
        for date, total in zip(table.index, totals) if math.isfinite(total)
    ][-90:]
    return history, fx_values[-len(history):] if history else []


def _kospi_benchmark(
    project_root: Path, history: list[dict[str, object]],
) -> tuple[list[dict[str, object]], float | None]:
    if not history:
        return [], None
    from stock_data.gui.query import LocalParquetQuery
    from stock_data.gui.services import IndexQueryService

    try:
        frame = IndexQueryService(
            LocalParquetQuery(project_root / "data"), project_root,
        ).series("KOSPI", "1Y")
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        return [], None
    if frame.empty:
        return [], None
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).sort_values("date")
    account_dates = pd.DataFrame({
        "date": pd.to_datetime([point["t"] for point in history]),
        "value": [float(point["v"]) for point in history],
    })
    joined = pd.merge_asof(account_dates, frame[["date", "close"]], on="date")
    joined = joined.dropna(subset=["close"])
    if joined.empty or not float(joined["close"].iloc[0]):
        return [], None
    scale = float(joined["value"].iloc[0]) / float(joined["close"].iloc[0])
    benchmark = [
        {"t": row.date.date().isoformat(), "v": float(row.close) * scale}
        for row in joined.itertuples(index=False)
    ]
    period_pct = (
        (float(joined["close"].iloc[-1]) / float(joined["close"].iloc[0]) - 1.0) * 100.0
        if len(joined) > 1 else None
    )
    return benchmark, period_pct


def build_account(project_root: Path) -> dict[str, object]:
    from stock_data.gui.account_snapshot_service import (
        LocalAccountPortfolioService,
        LocalAccountSnapshotService,
        LocalAccountSourceSpec,
        build_account_portfolio_presentation,
    )
    from stock_data.gui.services import EquityChartService, US_ETF_CHART_IDENTITIES
    from stock_web.api.account_page import build_account_page_data

    account_page = build_account_page_data(project_root)
    account_summary = account_page["summary"]
    manual_data = account_page["manual_accounts"]
    manual_accounts = manual_data.get("accounts", [])
    invest_total = float(account_summary.get("invest_total_krw") or 0.0)

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
            return {
                "reason": "읽을 수 있는 로컬 계좌 스냅샷이 없습니다.",
                "sources": account_summary.get("sources", []),
            }
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
        return {
            "total_krw": invest_total,
            "invest_total_krw": invest_total,
            "net_worth_krw": account_summary.get("net_worth_krw"),
            "net_worth_as_of": account_summary.get("net_worth_as_of"),
            "sources": account_summary.get("sources", []),
            "day_change_pct": None, "day_change_krw": None,
            "period_label": "3M", "period_pct": None, "kospi_period_pct": None,
            "ytd_pct": None,
            "cash_pct": manual_cash / invest_total * 100.0 if invest_total else None,
            "usd_assets_usd": manual_usd_krw / float(usdkrw) if usdkrw else None,
            "usd_assets_krw": manual_usd_krw,
            "usdkrw": usdkrw, "usdkrw_as_of": account_summary.get("fx_as_of"),
            "fx_effect_pct": None, "equity_effect_pct": None,
            "effective_exposure_pct": exposure_krw / invest_total * 100.0 if invest_total else None,
            "leveraged_weight_pct": leveraged_krw / invest_total * 100.0 if invest_total else None,
            "short_treasury_pct": short_treasury_krw / invest_total * 100.0 if invest_total else None,
            "exposure_unverified": list(dict.fromkeys(exposure_unverified)),
            "history": [], "benchmark": [],
            "footnote": "등락·기간 수익률·레버리지 비중·실효 노출은 투자 자산만 기준 · 수동 계좌는 과거 관측이 없어 수익률을 추정하지 않음",
        }

    portfolio = LocalAccountPortfolioService(
        tuple(sources), history_root=project_root / "data/local/account_value_history",
    ).load()
    presentation = build_account_portfolio_presentation(portfolio)
    if not presentation.available:
        return {"reason": "로컬 계좌 스냅샷이 표시 가능한 상태가 아닙니다."}

    fx, usdkrw, usdkrw_as_of = _latest_fx(project_root)
    amounts = {"KRW": 0.0, "USD": 0.0}
    cash = {"KRW": 0.0, "USD": 0.0}
    as_of_values: list[str] = []
    for entry in portfolio.entries:
        snapshot = entry.snapshot
        if not snapshot.displays_values:
            continue
        if snapshot.as_of:
            as_of_values.append(snapshot.as_of)
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
        return {"reason": "USD 자산은 있으나 보존된 USD/KRW 환율을 확인할 수 없습니다."}
    total_krw = amounts["KRW"] + amounts["USD"] * float(usdkrw or 0.0)
    if total_krw <= 0:
        return {"reason": "로컬 계좌 총액을 안전하게 계산할 수 없습니다."}

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

    history, history_fx = _account_history(presentation.histories, fx)
    if not history and as_of_values:
        history = [{"t": pd.Timestamp(max(as_of_values)).date().isoformat(), "v": total_krw}]
    benchmark, kospi_period_pct = _kospi_benchmark(project_root, history)
    day_change_krw = day_change_pct = fx_effect_pct = equity_effect_pct = None
    if len(history) >= 2:
        previous = float(history[-2]["v"])
        current = float(history[-1]["v"])
        day_change_krw = current - previous
        day_change_pct = (current / previous - 1.0) * 100.0 if previous else None
        if len(history_fx) >= 2 and history_fx[-1] is not None and history_fx[-2] is not None:
            fx_effect_krw = amounts["USD"] * (float(history_fx[-1]) - float(history_fx[-2]))
            fx_effect_pct = fx_effect_krw / previous * 100.0 if previous else None
            equity_effect_pct = day_change_pct - fx_effect_pct if day_change_pct is not None else None

    period_pct = ytd_pct = None
    if history:
        history_dates = [pd.Timestamp(point["t"]) for point in history]
        last_date = history_dates[-1]
        cutoff = last_date - pd.DateOffset(months=3)
        start_index = next((i for i, date in enumerate(history_dates) if date >= cutoff), 0)
        start_value = float(history[start_index]["v"])
        period_pct = (float(history[-1]["v"]) / start_value - 1.0) * 100.0 if start_value else None
        ytd_index = next((i for i, date in enumerate(history_dates) if date.year == last_date.year), None)
        if ytd_index is not None:
            ytd_value = float(history[ytd_index]["v"])
            ytd_pct = (float(history[-1]["v"]) / ytd_value - 1.0) * 100.0 if ytd_value else None

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
    if manual_accounts:
        history = []
        benchmark = []
        day_change_krw = day_change_pct = fx_effect_pct = equity_effect_pct = None
        period_pct = ytd_pct = kospi_period_pct = None
    usd_assets_krw = amounts["USD"] * float(usdkrw or 0.0) + manual_usd_krw
    return {
        "total_krw": invest_total,
        "invest_total_krw": invest_total,
        "net_worth_krw": account_summary.get("net_worth_krw"),
        "net_worth_as_of": account_summary.get("net_worth_as_of"),
        "sources": account_summary.get("sources", []),
        "day_change_pct": day_change_pct,
        "day_change_krw": day_change_krw,
        "period_label": "3M",
        "period_pct": period_pct,
        "kospi_period_pct": kospi_period_pct,
        "ytd_pct": ytd_pct,
        "cash_pct": (
            cash["KRW"] + cash["USD"] * float(usdkrw or 0.0) + manual_cash_krw
        ) / invest_total * 100.0 if invest_total else None,
        "usd_assets_usd": usd_assets_krw / float(usdkrw) if usdkrw else None,
        "usd_assets_krw": usd_assets_krw,
        "usdkrw": usdkrw,
        "usdkrw_as_of": usdkrw_as_of,
        "fx_effect_pct": fx_effect_pct,
        "equity_effect_pct": equity_effect_pct,
        "effective_exposure_pct": (exposure_krw + manual_exposure_krw) / invest_total * 100.0 if invest_total else None,
        "leveraged_weight_pct": (leveraged_krw + manual_leveraged_krw) / invest_total * 100.0 if invest_total else None,
        "short_treasury_pct": (short_treasury_krw + manual_short_treasury_krw) / invest_total * 100.0 if invest_total else None,
        "exposure_unverified": list(dict.fromkeys(unique_unverified)),
        "history": history,
        "benchmark": benchmark,
        "footnote": "등락·기간 수익률·레버리지 비중·실효 노출은 투자 자산만 기준 · 계좌 규모 변화는 입출금 미분리 · 점선은 같은 시점 KOSPI 비교",
    }


def build_derivatives(project_root: Path) -> dict[str, object]:
    from stock_data.gui.health_service import DailyHealthArtifactService
    from stock_data.gui.services import DashboardService
    from stock_data.gui.us_option_pcr_adapter import current_us_option_pcr_scope_views
    from stock_data.gui.vix_futures_adapter import build_vix_futures_dashboard_view

    service = DashboardService(project_root)
    try:
        metrics = service.dashboard_metrics(DailyHealthArtifactService(project_root).load())
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        metrics = {}

    def display(key: str, pattern: str) -> str:
        metric = metrics.get(key)
        if metric is not None and metric.displays_value and metric.value is not None:
            value = pattern.format(float(metric.value))
            return f"{value} · {metric.as_of}" if metric.as_of else value
        return str(getattr(metric, "unavailable_reason", None) or "보존 데이터 없음")

    vix_reason = build_vix_futures_dashboard_view().metric.unavailable_reason
    cboe_views = current_us_option_pcr_scope_views()
    cboe_reason = cboe_views[0].reason if cboe_views else "CBOE PCR source unavailable"
    return {"groups": [
        {"title": "한국 · KOSPI200", "rows": [
            ["선물 Basis", display("KOSPI200_BASIS", "{:+.2f}")],
            ["거래량 PCR", display("VOLUME_PCR", "{:.3f}")],
            ["미결제약정 PCR", display("OI_PCR", "{:.3f}")],
            ["LS 선물 외국인 순계약", display("LS_FUTURES_FOREIGN_NET", "{:+,.0f}")],
        ]},
        {"title": "미국", "rows": [
            ["VIX 선물", str(vix_reason)],
            ["CBOE PCR", str(cboe_reason)],
        ]},
    ]}


def build_schedule(project_root: Path) -> dict[str, object]:
    path = project_root / "data/local/calendar/events.json"
    if not path.is_file():
        return {"reason": "로컬 일정 파일이 없습니다."}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload["items"]
        if not isinstance(items, list):
            raise ValueError("items")
        clean = []
        for item in items:
            when = item.get("when")
            what = item.get("what")
            importance = item.get("importance")
            if (
                not isinstance(when, str)
                or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d|\d{2}-\d{2}", when) is None
                or not isinstance(what, str) or not what.strip()
                or type(importance) is not int or importance not in {1, 2, 3}
            ):
                raise ValueError("item")
            clean.append({"when": when, "what": what.strip(), "importance": importance})
        return {"items": clean}
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {"reason": "로컬 일정 파일 형식이 올바르지 않습니다."}


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
        return {"lines": lines, "meta": f"{created or '생성 시각 미상'} · {source}"}
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None


def build_scanner(project_root: Path) -> dict[str, object]:
    from stock_web.api.scanner import build_scanner as build_full_scanner

    result = build_full_scanner(project_root)
    return {
        key: result.get(key)
        for key in ("status", "as_of", "count", "rule", "top", "reason")
        if key in result
    }


def build_watchlist(project_root: Path) -> dict[str, object]:
    from stock_web.api.stocks_page import build_home_watchlist

    return build_home_watchlist(project_root)


def _build_home_payload_uncached(project_root: Path) -> dict[str, object]:
    from stock_web.api.regime import build_regime

    sections: dict[str, object] = {}
    account = build_account(project_root)
    sections["account"] = account
    sections["regime"] = build_regime(project_root, account)
    sections["derivatives"] = build_derivatives(project_root)
    sections["health"] = build_health(project_root)
    sections["schedule"] = build_schedule(project_root)
    brief = build_brief(project_root)
    if brief is not None:
        sections["brief"] = brief
    sections["scanner"] = build_scanner(project_root)
    sections["watchlist"] = build_watchlist(project_root)
    sections["tiles"] = build_tiles(project_root)
    sections["chart_symbols"] = [
        {"symbol": s, "name": INDEX_SOURCES[s][3]} for s in ("KOSPI", "KOSDAQ", "KOSPI200", "SP500", "ESF", "NASDAQ", "NDX", "NQF", "DOW", "YMF", "SOX", "SOXX", "EWY", "DXY", "WTI", "GOLD")
        if (_f := _ohlcv(project_root, s)[0]) is not None and not _f.empty
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


def build_home_payload(project_root: Path) -> dict[str, object]:
    """Build the home document, memoized for 60 seconds per resolved root."""
    root = Path(project_root).resolve()
    key = str(root)
    now = time.monotonic()
    cached = _HOME_CACHE.get(key)
    if cached is not None and now - cached[0] < _HOME_CACHE_TTL_SECONDS:
        return cached[1]
    payload = _build_home_payload_uncached(root)
    _HOME_CACHE[key] = (now, payload)
    return payload
