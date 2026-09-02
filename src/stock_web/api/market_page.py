"""Provider-free data projections for the local Market page."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_web.api import datasets as dsx
from stock_web.api.datasets import field
from stock_web.api.home_data import (
    _nan_to_none,
    _ohlcv,
    build_chart_symbols,
)
from stock_web.api.indicators import (
    calculate_indicators,
    normalize_indicators,
    resample_ohlcv,
)


RANGE_OFFSETS = {
    "3M": pd.DateOffset(months=3),
    "6M": pd.DateOffset(months=6),
    "1Y": pd.DateOffset(years=1),
    "3Y": pd.DateOffset(years=3),
}


def _unavailable(reason: object) -> dict[str, object]:
    return {"status": "UNAVAILABLE", "reason": str(reason or "보존 데이터 없음")}


def _value(value: object) -> float | None:
    converted = _nan_to_none(value)
    try:
        return None if converted is None else float(converted)
    except (TypeError, ValueError):
        return None


def _date(value: object) -> str | None:
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def _points(frame: pd.DataFrame, column: str) -> list[dict[str, object]]:
    if frame.empty or column not in frame or "date" not in frame:
        return []
    return [
        {"t": observed.strftime("%Y-%m-%d"), "v": _value(value)}
        for observed, value in zip(pd.to_datetime(frame["date"]), frame[column])
        if pd.notna(observed)
    ]


def _indicator_points(frame: pd.DataFrame, column: str) -> list[dict[str, object]]:
    return [point for point in _points(frame, column) if point["v"] is not None]


def _range_view(frame: pd.DataFrame, range_key: str) -> pd.DataFrame:
    if frame.empty or range_key == "ALL":
        return frame
    offset = RANGE_OFFSETS.get(range_key, RANGE_OFFSETS["6M"])
    last = pd.to_datetime(frame["date"], errors="coerce").max()
    return frame.loc[pd.to_datetime(frame["date"]) >= last - offset].copy()


def build_market_chart_payload(
    project_root: Path,
    *,
    symbol: str,
    interval: str,
    range_key: str,
    indicators: str | tuple[str, ...] | None,
) -> dict[str, object]:
    """Build resampled OHLCV and only the requested allowlisted indicators."""
    selected = normalize_indicators(indicators)
    try:
        frame, name = _ohlcv(project_root, symbol)
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        frame, name = None, symbol
    if frame is None or frame.empty or "close" not in frame:
        return {
            "symbol": symbol, "symbol_name": name, "interval": interval,
            "range": range_key, "active_indicators": selected,
            "reason": "보존 데이터 없음",
        }

    safe_interval = interval if interval in {"1d", "1w", "1M"} else "1d"
    daily = frame.dropna(subset=["date", "close"]).sort_values("date")
    bars = resample_ohlcv(daily, safe_interval)
    calculated = calculate_indicators(bars)
    view = _range_view(calculated, range_key)
    if view.empty:
        return {
            "symbol": symbol, "symbol_name": name, "interval": safe_interval,
            "range": range_key, "active_indicators": selected,
            "reason": "선택 기간의 보존 데이터 없음",
        }

    candles: list[dict[str, object]] = []
    for row in view.itertuples(index=False):
        close = _value(row.close)
        open_value = _value(getattr(row, "open", None))
        high = _value(getattr(row, "high", None))
        low = _value(getattr(row, "low", None))
        candles.append({
            "t": pd.Timestamp(row.date).strftime("%Y-%m-%d"),
            "o": close if open_value is None else open_value,
            "h": close if high is None else high,
            "l": close if low is None else low,
            "c": close,
            "v": _value(getattr(row, "volume", None)),
        })

    payload: dict[str, object] = {}
    for moving_average in ("ma5", "ma20", "ma60", "ma120"):
        if moving_average in selected:
            payload[moving_average] = _indicator_points(view, moving_average)
    if "bollinger" in selected:
        payload["bollinger"] = {
            "upper": _indicator_points(view, "bollinger_upper"),
            "middle": _indicator_points(view, "bollinger_mid"),
            "lower": _indicator_points(view, "bollinger_lower"),
        }
    if "rsi14" in selected:
        payload["rsi14"] = _indicator_points(view, "rsi14")
    if "macd" in selected:
        payload["macd"] = {
            "macd": _indicator_points(view, "macd"),
            "signal": _indicator_points(view, "macd_signal"),
            "histogram": _indicator_points(view, "macd_histogram"),
        }
    if "stochastic" in selected:
        payload["stochastic"] = {
            "k": _indicator_points(view, "stochastic_k"),
            "d": _indicator_points(view, "stochastic_d"),
        }
    if "volume" in selected:
        payload["volume"] = [
            {"t": candle["t"], "v": candle["v"]} for candle in candles
        ]

    last = calculated.iloc[-1]
    return {
        "symbol": symbol,
        "symbol_name": name,
        "interval": safe_interval,
        "range": range_key if range_key in {*RANGE_OFFSETS, "ALL"} else "6M",
        "as_of": candles[-1]["t"],
        "active_indicators": selected,
        "candles": candles,
        "indicators": payload,
        "stats": {"rsi14": _value(last.get("rsi14"))},
    }


def _metric_view(metric: object, *, pattern: str = "{:,.2f}") -> dict[str, object]:
    if metric is None:
        return _unavailable("표시 상태를 확인할 수 없습니다.")
    if not bool(getattr(metric, "displays_value", False)):
        return _unavailable(getattr(metric, "unavailable_reason", None)) | {
            "display_state": str(getattr(getattr(metric, "display_state", None), "value", "UNAVAILABLE")),
            "as_of": getattr(metric, "as_of", None),
        }
    numeric = _value(getattr(metric, "value", None))
    return {
        "status": "VALUE",
        "display_state": "VALUE",
        "value": numeric,
        "display_value": pattern.format(numeric) if numeric is not None else "—",
        "as_of": getattr(metric, "as_of", None),
        "unit": getattr(metric, "unit", ""),
        "source": getattr(metric, "source", ""),
    }


def build_derivatives(project_root: Path) -> dict[str, object]:
    try:
        from stock_data.gui.services import DashboardService

        service = DashboardService(project_root)
        metrics = service.dashboard_metrics()
    except (KeyError, OSError, PermissionError, TypeError, ValueError) as error:
        return _unavailable(f"파생 표시 상태를 읽을 수 없습니다: {error}")

    basis = _metric_view(metrics.get("KOSPI200_BASIS"), pattern="{:+,.2f}")
    if basis["status"] == "VALUE":
        try:
            frame = service.query.tail(
                "derived/kr_kospi200_futures_nearest_listed_daily", rows=120,
                columns=["date", "session", "settlement_basis", "basis_status"],
            )
            frame = frame.loc[
                frame["session"].astype(str).eq("REGULAR_DAY")
                & frame["basis_status"].astype(str).eq(
                    "SAME_ROW_REGULAR_SESSION_SOURCE_NATIVE_DIFFERENCE"
                )
            ].sort_values("date").tail(60)
            series = _indicator_points(frame, "settlement_basis")
            if not series:
                basis = _unavailable("Basis 60거래일 이력이 없습니다.")
            else:
                basis["series"] = series
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            basis = _unavailable("Basis 60거래일 이력을 읽을 수 없습니다.")

    pcr_views: dict[str, dict[str, object]] = {
        "volume": _metric_view(metrics.get("VOLUME_PCR"), pattern="{:.3f}"),
        "oi": _metric_view(metrics.get("OI_PCR"), pattern="{:.3f}"),
    }
    try:
        pcr = service.derivatives.pcr(days=60)
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        pcr = pd.DataFrame()
    for key, column in (("volume", "volume_pcr"), ("oi", "open_interest_pcr")):
        if pcr_views[key]["status"] == "VALUE":
            series = _indicator_points(pcr, column)
            if series:
                pcr_views[key]["series"] = series
            else:
                pcr_views[key] = _unavailable(f"{key.upper()} PCR 60거래일 이력이 없습니다.")

    call_metric = _metric_view(metrics.get("CALL_WALL"), pattern="{:,.2f}")
    put_metric = _metric_view(metrics.get("PUT_WALL"), pattern="{:,.2f}")
    if call_metric["status"] == put_metric["status"] == "VALUE":
        try:
            walls, metadata = service.derivatives.option_wall()
            columns = [
                "date", "maturity_month", "underlying_price",
                "call_wall_strike", "call_wall_oi", "call_wall_distance_pct",
                "put_wall_strike", "put_wall_oi", "put_wall_distance_pct",
            ]
            present = [column for column in columns if column in walls]
            rows = walls[present].sort_values("date").tail(10).iloc[::-1]
            wall = {
                "status": "VALUE", "metadata": metadata,
                "rows": [
                    {column: (_date(value) if column == "date" else _nan_to_none(value))
                     for column, value in row.items()}
                    for row in rows.to_dict(orient="records")
                ],
            } if not rows.empty else _unavailable("Call/Put Wall 보존 이력이 없습니다.")
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            wall = _unavailable("Call/Put Wall 보존 이력을 읽을 수 없습니다.")
    else:
        reasons = [str(item.get("reason")) for item in (call_metric, put_metric) if item.get("reason")]
        wall = _unavailable(" / ".join(dict.fromkeys(reasons)) or "Wall 표시 상태가 차단되었습니다.")

    ls = _metric_view(metrics.get("LS_FUTURES_FOREIGN_NET"), pattern="{:+,.0f}")
    if ls["status"] == "VALUE":
        try:
            raw = service.derivatives.ls_flow()
            ls.update({"source_status": raw.get("status"), "warning": raw.get("warning")})
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            ls = _unavailable("LS 선물 외국인 순계약 보존 행을 읽을 수 없습니다.")

    parts = [basis, *pcr_views.values(), wall, ls]
    return {
        "status": "VALUE" if any(part.get("status") == "VALUE" for part in parts) else "UNAVAILABLE",
        "reason": None if any(part.get("status") == "VALUE" for part in parts) else "표시 가능한 KOSPI200 파생 상세가 없습니다.",
        "basis": basis,
        "pcr": pcr_views,
        "wall": wall,
        "ls_flow": ls,
    }


def _flow_market(frame: pd.DataFrame, market: str) -> dict[str, object]:
    scoped = frame.loc[frame["market"].astype(str).eq(market)].sort_values("date").tail(20)
    required = {
        f"{key}_{side}_amount"
        for key in ("foreigner", "institution", "individual")
        for side in ("buy", "sell")
    }
    if scoped.empty or not required <= set(scoped.columns):
        return _unavailable(f"{market} 투자자 매매 20거래일 데이터가 없습니다.") | {"market": market}
    series = {}
    for key, label in (("foreigner", "외국인"), ("institution", "기관"), ("individual", "개인")):
        values = (
            pd.to_numeric(scoped[f"{key}_buy_amount"], errors="coerce")
            - pd.to_numeric(scoped[f"{key}_sell_amount"], errors="coerce")
        ) / 1e8
        series[key] = {
            "label": label,
            "points": [
                {"t": date.strftime("%Y-%m-%d"), "v": _value(value)}
                for date, value in zip(pd.to_datetime(scoped["date"]), values)
            ],
        }
    return {"status": "VALUE", "market": market, "series": series}


def build_flows_and_balances(project_root: Path) -> dict[str, object]:
    flow_frame = dsx.load(project_root, "data/normalized/kr_market_investor_trading_daily")
    markets = [
        _flow_market(flow_frame, market) if flow_frame is not None
        else _unavailable("투자자 매매 데이터셋이 없습니다.") | {"market": market}
        for market in ("KOSPI", "KOSDAQ")
    ]

    credit: dict[str, object]
    credit_frame = dsx.load(
        project_root, "data/normalized/kr_credit_balance_daily",
        columns=["date", "credit_financing_total"],
    )
    if credit_frame is None or credit_frame.empty:
        credit = _unavailable("신용잔고 데이터셋이 없습니다.")
    else:
        credit_frame = credit_frame.dropna(subset=["credit_financing_total"]).sort_values("date")
        latest = credit_frame["date"].max()
        year = credit_frame.loc[credit_frame["date"] >= latest - pd.DateOffset(years=1)]
        credit = {
            "status": "VALUE", "series": _indicator_points(year, "credit_financing_total"),
            "as_of": _date(latest), "unit": "원",
        } if not year.empty else _unavailable("신용잔고 1년 이력이 없습니다.")

    lending_frame = dsx.load(
        project_root, "data/normalized/kr_stock_lending_market_daily",
        columns=["date", "balance_amount"],
    )
    lending: dict[str, object] | None = None
    if lending_frame is not None and not lending_frame.empty:
        aggregated = (
            lending_frame.assign(
                balance_amount=pd.to_numeric(lending_frame["balance_amount"], errors="coerce")
            ).groupby("date", as_index=False)["balance_amount"].sum(min_count=1).sort_values("date")
        )
        latest = aggregated["date"].max()
        year = aggregated.loc[aggregated["date"] >= latest - pd.DateOffset(years=1)]
        if not year.empty:
            lending = {
                "status": "VALUE", "series": _indicator_points(year, "balance_amount"),
                "as_of": _date(latest), "unit": "원",
            }

    micro_rows: list[dict[str, object]] = []
    micro_reason = None
    try:
        from stock_data.gui.services import DashboardService

        micro = DashboardService(project_root).micro
        lending_latest = micro.lending_market()
        if lending_latest:
            micro_rows.append({
                "name": "대차잔고", "market": "전체",
                "as_of": _date(lending_latest.get("date")),
                "value": _value(lending_latest.get("balance_amount")),
                "change_1d": _value(lending_latest.get("change_1d")),
                "change_5d": _value(lending_latest.get("change_5d")),
                "unit": "원",
            })
        for row in micro.breadth():
            micro_rows.append({
                "name": "시장 등락 종목", "market": row.get("market"),
                "as_of": _date(row.get("date")), "advancing": _nan_to_none(row.get("advancing")),
                "declining": _nan_to_none(row.get("declining")), "unchanged": _nan_to_none(row.get("unchanged")),
                "ad_ratio": _value(row.get("ad_ratio")),
            })
    except (KeyError, OSError, PermissionError, TypeError, ValueError) as error:
        micro_reason = f"시장 미시구조 데이터를 읽을 수 없습니다: {error}"

    available = any(item.get("status") == "VALUE" for item in markets) or credit.get("status") == "VALUE" or lending is not None or bool(micro_rows)
    return {
        "status": "VALUE" if available else "UNAVAILABLE",
        "reason": None if available else "표시 가능한 수급·잔고 상세가 없습니다.",
        "markets": markets,
        "credit": credit,
        **({"lending": lending} if lending is not None else {}),
        "microstructure": {"status": "VALUE", "rows": micro_rows} if micro_rows else _unavailable(micro_reason),
    }


def build_valuation(project_root: Path) -> dict[str, object]:
    markets = []
    for market, code in (("KOSPI", "1001"), ("KOSDAQ", "2001")):
        frame = dsx.load(
            project_root, "data/normalized/kr_index_fundamental_daily",
            filter_expr=(field("index_code") == code),
        )
        if frame is None or frame.empty or not {"weighted_per", "weighted_pbr"} <= set(frame.columns):
            markets.append(_unavailable(f"{market} PER/PBR 데이터가 없습니다.") | {"market": market})
            continue
        valid = frame.dropna(subset=["date"]).sort_values("date")
        latest = valid["date"].max()
        five_years = valid.loc[valid["date"] >= latest - pd.DateOffset(years=5)].copy()
        current = five_years.iloc[-1]
        per = pd.to_numeric(five_years["weighted_per"], errors="coerce")
        pbr = pd.to_numeric(five_years["weighted_pbr"], errors="coerce")
        current_per, current_pbr = _value(current.get("weighted_per")), _value(current.get("weighted_pbr"))
        per_percentile = _value((per.dropna() <= current_per).mean() * 100.0) if current_per is not None else None
        pbr_percentile = _value((pbr.dropna() <= current_pbr).mean() * 100.0) if current_pbr is not None else None
        markets.append({
            "status": "VALUE", "market": market,
            "per": _indicator_points(five_years, "weighted_per"),
            "pbr": _indicator_points(five_years, "weighted_pbr"),
            "current": {
                "t": _date(current["date"]), "per": current_per, "pbr": current_pbr,
                "per_percentile": per_percentile, "pbr_percentile": pbr_percentile,
            },
        })
    available = any(item.get("status") == "VALUE" for item in markets)
    return {
        "status": "VALUE" if available else "UNAVAILABLE",
        "reason": None if available else "표시 가능한 KOSPI/KOSDAQ 밸류에이션 이력이 없습니다.",
        "markets": markets,
        "forward_note": "선행 PER·PBR — 소스 검증 전",
    }


def build_market_page_payload(project_root: Path) -> dict[str, object]:
    """Build independent optional sections; one missing source never blocks others."""
    def safely(builder, label: str) -> dict[str, object]:
        try:
            return builder(project_root)
        except (KeyError, OSError, PermissionError, TypeError, ValueError) as error:
            return _unavailable(f"{label}을(를) 읽을 수 없습니다: {error}")

    return {
        "schema_version": 1,
        "chart_symbols": build_chart_symbols(project_root),
        "sections": {
            "derivatives": safely(build_derivatives, "파생 상세"),
            "flows": safely(build_flows_and_balances, "수급·잔고 상세"),
            "valuation": safely(build_valuation, "밸류에이션"),
        },
    }


__all__ = [
    "build_derivatives", "build_flows_and_balances", "build_market_chart_payload",
    "build_market_page_payload", "build_valuation",
]
