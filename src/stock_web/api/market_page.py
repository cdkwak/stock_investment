"""Provider-free data projections for the local Market page."""
from __future__ import annotations

import time
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
    "5Y": pd.DateOffset(years=5),
}

FLOW_RANGE_SESSIONS = {"20D": 20, "60D": 60, "1Y": 252, "ALL": None}
HISTORY_RANGES = {"1Y", "3Y", "5Y", "ALL"}
MARKET_RANGE_KEYS = {*FLOW_RANGE_SESSIONS, *HISTORY_RANGES}
_MARKET_CACHE_TTL_SECONDS = 60.0
_MARKET_CACHE: dict[tuple[str, str], tuple[float, dict[str, object]]] = {}

_WARNING_TRANSLATIONS = {
    "Raw provider observation; no Normalized/PIT-safe claim":
        "원시 관측값 · 정규화 전 · 수동 검증 전에는 표시하지 않습니다",
}

METRIC_EXPLANATIONS = {
    "rsi14": "RSI14는 최근 14개 종가 변화에 Wilder 지수이동평균 방식을 적용한 0–100 모멘텀 지표입니다.",
    "futures_basis": "선물 Basis는 KOSPI200 선물 정산가와 기초지수의 차이입니다. 양수·음수는 두 가격의 상대적 위치를 설명하며 방향 신호가 아닙니다.",
    "volume_pcr": "거래량 PCR은 풋 거래량 ÷ 콜 거래량입니다. 1보다 크면 해당일 풋 거래가 콜보다 많았다는 뜻입니다.",
    "oi_pcr": "미결제약정 PCR은 풋 미결제약정 ÷ 콜 미결제약정입니다. 1보다 크면 풋 잔고가 많다는 뜻으로, 하방 헤지 수요가 큰 상태로 읽습니다.",
    "ls_futures_foreign_net": "LS 선물 외국인 순계약은 보존된 정규장 원시 관측의 외국인 순계약 수입니다. 세션 최종성 검증 전 설명용 수치이며 신호가 아닙니다.",
    "call_wall": "Call Wall은 기초자산 ±15% 안에서 콜 미결제약정이 가장 큰 행사가입니다. 현재가와의 거리는 위치 관계를 보여줄 뿐 지지·저항을 보장하지 않습니다.",
    "put_wall": "Put Wall은 기초자산 ±15% 안에서 풋 미결제약정이 가장 큰 행사가입니다. 현재가와의 거리는 위치 관계를 보여줄 뿐 지지·저항을 보장하지 않습니다.",
    "credit_balance": "신용잔고는 신용융자로 매수한 주식의 남은 금액입니다. 증가는 레버리지 자금이 늘어난 상태를 뜻하지만 방향 신호는 아닙니다.",
    "lending_balance": "대차잔고는 빌려간 주식의 미상환 금액입니다. 공매도 외 목적도 포함될 수 있어 단독으로 방향을 판단하지 않습니다.",
    "market_breadth": "시장 등락 종목은 기준일의 상승·하락·보합 종목 수입니다. 지수 움직임이 시장 전반에 퍼졌는지 설명합니다.",
    "ad_ratio": "A/D는 상승 종목 수 ÷ 하락 종목 수입니다. 1보다 크면 상승 종목이, 1보다 작으면 하락 종목이 더 많습니다.",
    "weighted_per": "가중 PER은 지수 구성 종목의 주가수익비율을 시가총액 방식으로 집계한 값입니다. 과거와의 상대 위치를 설명하며 적정가 판단이 아닙니다.",
    "weighted_pbr": "가중 PBR은 지수 구성 종목의 주가순자산비율을 시가총액 방식으로 집계한 값입니다. 업종 구성 변화에 따라 비교 의미가 달라질 수 있습니다.",
    "five_year_percentile": "5년 백분위는 최근 5년 관측 중 현재 값 이하인 비율입니다. 높고 낮음은 과거 분포상 위치이며 매매 추천이 아닙니다.",
    "valuation_panel": "가중 PER은 지수 구성 종목의 주가수익비율, 가중 PBR은 주가순자산비율을 시가총액 방식으로 집계한 값입니다. 5년 백분위는 최근 5년 관측 중 현재 값 이하인 비율이고, 상위 비율은 그 보수입니다. 과거 분포상 위치이며 적정가나 매매 추천이 아닙니다.",
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


def format_compact_kr(value: object) -> str:
    """Format finite numbers with compact Korean 만/억/조 units."""
    numeric = _value(value)
    if numeric is None:
        return "—"
    absolute = abs(numeric)
    if absolute >= 1e12:
        scaled, unit, digits = numeric / 1e12, "조", 1
    elif absolute >= 1e8:
        scaled, unit, digits = numeric / 1e8, "억", 0 if absolute >= 1e11 else 1
    elif absolute >= 1e4:
        scaled, unit, digits = numeric / 1e4, "만", 1
    else:
        scaled, unit, digits = numeric, "", 0 if numeric.is_integer() else 1
    rendered = f"{scaled:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{scaled:,.0f}"
    return f"{rendered}{unit}"


def _basis_label(value: object, *, d_plus_one: bool = False) -> str | None:
    observed = _date(value)
    if observed is None:
        return None
    older = pd.Timestamp(observed).date() < pd.Timestamp.now(tz="Asia/Seoul").date()
    return f"기준일 {observed}{' · D+1 공개' if d_plus_one and older else ''}"


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


def _flow_range_view(frame: pd.DataFrame, range_key: str) -> pd.DataFrame:
    """Return a deterministic trading-session tail for investor-flow charts."""
    normalized = range_key if range_key in FLOW_RANGE_SESSIONS else "60D"
    sessions = FLOW_RANGE_SESSIONS[normalized]
    ordered = frame.sort_values("date")
    return ordered if sessions is None else ordered.tail(sessions).copy()


def _history_range(range_key: str) -> str:
    return range_key if range_key in HISTORY_RANGES else "1Y"


def _localized_warning(value: object) -> str | None:
    """Keep provider warnings Korean-only at the web projection boundary."""
    text = str(value or "").strip()
    if not text:
        return None
    translated = _WARNING_TRANSLATIONS.get(text)
    if translated is not None:
        return translated
    if text.isascii():
        return "원시 관측값 · 정규화·검증 상태를 확인할 수 없습니다"
    return text


def _cumulative_points(points: list[dict[str, object]]) -> list[dict[str, object]]:
    """Accumulate every observation, including the first day's net buy."""
    total: int | None = 0
    cumulative: list[dict[str, object]] = []
    for point in points:
        value = point.get("v")
        if total is None or value is None:
            total = None
            cumulative.append({"t": point.get("t"), "v": None})
            continue
        total += int(value)
        cumulative.append({"t": point.get("t"), "v": total})
    return cumulative


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


def _market_derivative_metrics(service: object, project_root: Path) -> dict[str, object]:
    """Build only the five derivative metrics rendered on the Market page."""
    from stock_data.gui.health_service import DailyHealthArtifactService

    health = DailyHealthArtifactService(project_root).load()
    rows = {row.dataset: row for row in getattr(health, "rows", ())}

    def health_expected(dataset_id: str) -> str | None:
        row = rows.get(dataset_id)
        expected = getattr(row, "expected", None) if row is not None else None
        return expected if isinstance(expected, str) and expected != "N/A" else None

    managed = {
        "automation_policy": "DEPENDENCY_DRIVEN",
        "automation_enabled": True,
        "require_expected_as_of": True,
    }
    return {
        "KOSPI200_BASIS": service._local_derivative_metric(
            "KOSPI200_BASIS", "KOSPI200 선물 Basis",
            "kr_kospi200_futures_nearest_listed_daily", "source-native difference",
            service._read_basis_metric,
            expected_as_of=health_expected("kr_kospi200_futures_nearest_listed_daily"),
            **managed,
        ),
        "VOLUME_PCR": service._local_derivative_metric(
            "VOLUME_PCR", "KOSPI200 옵션 거래량 P/C",
            "kr_kospi200_option_pcr_daily", "ratio", service._read_volume_pcr_metric,
            expected_as_of=health_expected("kr_kospi200_option_pcr_daily"), **managed,
        ),
        "OI_PCR": service._local_derivative_metric(
            "OI_PCR", "KOSPI200 옵션 OI P/C",
            "kr_kospi200_option_pcr_daily", "ratio", service._read_oi_pcr_metric,
            expected_as_of=health_expected("kr_kospi200_option_pcr_daily"), **managed,
        ),
        "CALL_WALL": service._local_derivative_metric(
            "CALL_WALL", "Call 최대 OI 행사가", "kr_kospi200_option_walls_daily",
            "strike", lambda: service._read_wall_metric("call"),
            expected_as_of=health_expected("kr_kospi200_option_walls_daily"), **managed,
        ),
        "PUT_WALL": service._local_derivative_metric(
            "PUT_WALL", "Put 최대 OI 행사가", "kr_kospi200_option_walls_daily",
            "strike", lambda: service._read_wall_metric("put"),
            expected_as_of=health_expected("kr_kospi200_option_walls_daily"), **managed,
        ),
        "LS_FUTURES_FOREIGN_NET": service._local_derivative_metric(
            "LS_FUTURES_FOREIGN_NET", "LS 선물 외국인 순계약",
            "ls_t8462_daily_raw", "contracts", service._read_ls_futures_foreign_net_metric,
        ),
    }


def build_derivatives(
    project_root: Path, *, range_key: str = "1Y",
) -> dict[str, object]:
    history_range = _history_range(range_key)
    try:
        from stock_data.gui.services import DashboardService

        service = DashboardService(project_root)
        metrics = _market_derivative_metrics(service, project_root)
    except (KeyError, OSError, PermissionError, RuntimeError, TypeError, ValueError) as error:
        return _unavailable(f"파생 표시 상태를 읽을 수 없습니다: {error}")

    basis = _metric_view(metrics.get("KOSPI200_BASIS"), pattern="{:+,.2f}")
    if basis["status"] == "VALUE":
        try:
            frame = service.query.read(
                "derived/kr_kospi200_futures_nearest_listed_daily",
                columns=["date", "session", "settlement_basis", "basis_status"],
            )
            frame = frame.loc[
                frame["session"].astype(str).eq("REGULAR_DAY")
                & frame["basis_status"].astype(str).eq(
                    "SAME_ROW_REGULAR_SESSION_SOURCE_NATIVE_DIFFERENCE"
                )
            ].sort_values("date")
            series = _indicator_points(_range_view(frame, history_range), "settlement_basis")
            if not series:
                basis = _unavailable("Basis 이력이 없습니다.")
            else:
                basis.update({"series": series, "basis_label": _basis_label(basis.get("as_of"), d_plus_one=True)})
        except (KeyError, OSError, PermissionError, RuntimeError, TypeError, ValueError):
            basis = _unavailable("Basis 60거래일 이력을 읽을 수 없습니다.")

    pcr_views: dict[str, dict[str, object]] = {
        "volume": _metric_view(metrics.get("VOLUME_PCR"), pattern="{:.2f}"),
        "oi": _metric_view(metrics.get("OI_PCR"), pattern="{:.2f}"),
    }
    try:
        pcr = service.query.read(
            "derived/kr_kospi200_option_pcr_daily",
            columns=["date", "volume_pcr", "open_interest_pcr", "observation_status"],
        ).sort_values("date")
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        pcr = pd.DataFrame()
    for key, column in (("volume", "volume_pcr"), ("oi", "open_interest_pcr")):
        if pcr_views[key]["status"] == "VALUE":
            series = _indicator_points(_range_view(pcr, history_range), column)
            if series:
                pcr_views[key].update({
                    "series": series,
                    "basis_label": _basis_label(pcr_views[key].get("as_of"), d_plus_one=True),
                })
            else:
                pcr_views[key] = _unavailable(f"{key.upper()} PCR 이력이 없습니다.")

    call_metric = _metric_view(metrics.get("CALL_WALL"), pattern="{:,.2f}")
    put_metric = _metric_view(metrics.get("PUT_WALL"), pattern="{:,.2f}")
    if call_metric["status"] == put_metric["status"] == "VALUE":
        try:
            walls, metadata = service.derivatives.option_wall()
            columns = [
                "date", "maturity_month", "underlying_price",
                "near_call_wall_strike", "near_call_wall_oi", "near_call_wall_distance_pct", "near_call_wall_status",
                "near_put_wall_strike", "near_put_wall_oi", "near_put_wall_distance_pct", "near_put_wall_status",
                "near_wall_window_pct",
            ]
            present = [column for column in columns if column in walls]
            rows = walls[present].sort_values("date").tail(10).iloc[::-1]
            has_near_columns = {"near_call_wall_strike", "near_put_wall_strike"} <= set(walls.columns)
            wall = {
                "status": "VALUE", "metadata": metadata,
                "as_of": _date(rows.iloc[0]["date"]) if not rows.empty else None,
                "basis_label": _basis_label(rows.iloc[0]["date"], d_plus_one=True) if not rows.empty else None,
                "near_window_available": has_near_columns,
                "rows": [
                    ({column: (_date(value) if column == "date" else _nan_to_none(value))
                      for column, value in row.items()} | {
                        "near_wall_note": (
                            "±15% 창 안에 양의 미결제약정이 없습니다."
                            if row.get("near_call_wall_status") == "NO_NEAR_WINDOW_OI"
                            or row.get("near_put_wall_status") == "NO_NEAR_WINDOW_OI"
                            else None
                        ) if has_near_columns else "기존 파일 형식이라 근접 Wall을 계산할 수 없습니다.",
                    })
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
            ls.update({
                "source_status": raw.get("status"),
                "warning": _localized_warning(raw.get("warning")),
                "basis_label": _basis_label(ls.get("as_of"), d_plus_one=True),
            })
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


def _flow_market(
    frame: pd.DataFrame, market: str, *, range_key: str = "60D",
) -> dict[str, object]:
    scoped = frame.loc[frame["market"].astype(str).eq(market)].sort_values("date")
    required = {
        f"{key}_{side}_amount"
        for key in ("foreigner", "institution", "individual")
        for side in ("buy", "sell")
    }
    if scoped.empty or not required <= set(scoped.columns):
        return _unavailable(f"{market} 투자자 매매 데이터가 없습니다.") | {"market": market}
    normalized_range = range_key if range_key in FLOW_RANGE_SESSIONS else "60D"
    scoped = _flow_range_view(scoped, normalized_range)
    series = {}
    for key, label in (("foreigner", "외국인"), ("institution", "기관"), ("individual", "개인")):
        values = (
            pd.to_numeric(scoped[f"{key}_buy_amount"], errors="coerce")
            - pd.to_numeric(scoped[f"{key}_sell_amount"], errors="coerce")
        ) / 1e8
        rounded = values.round().astype("Int64")
        daily_points = [
            {"t": date.strftime("%Y-%m-%d"), "v": None if pd.isna(value) else int(value)}
            for date, value in zip(pd.to_datetime(scoped["date"]), rounded)
        ]
        series[key] = {
            "label": label,
            "daily_points": daily_points,
            "cumulative_points": _cumulative_points(daily_points),
        }
    return {
        "status": "VALUE", "market": market, "series": series,
        "as_of": _date(scoped["date"].max()), "unit": "억원",
        "range": normalized_range,
        "presentation": "CUMULATIVE_FROM_RANGE_START",
    }


def build_flows_and_balances(
    project_root: Path, *, range_key: str = "60D", history_range_key: str = "1Y",
) -> dict[str, object]:
    normalized_range = range_key if range_key in FLOW_RANGE_SESSIONS else "60D"
    history_range = _history_range(history_range_key)
    flow_frame = dsx.load(project_root, "data/normalized/kr_market_investor_trading_daily")
    markets = [
        _flow_market(flow_frame, market, range_key=normalized_range) if flow_frame is not None
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
        credit_view = _range_view(credit_frame, history_range)
        credit = {
            "status": "VALUE", "series": _indicator_points(credit_view, "credit_financing_total"),
            "as_of": _date(latest), "unit": "원",
        } if not credit_frame.empty else _unavailable("신용잔고 이력이 없습니다.")

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
        if not aggregated.empty:
            lending_view = _range_view(aggregated, history_range)
            lending = {
                "status": "VALUE", "series": _indicator_points(lending_view, "balance_amount"),
                "as_of": _date(latest), "unit": "원",
            }

    lending_rows: list[dict[str, object]] = []
    breadth_rows: list[dict[str, object]] = []
    micro_reason = None
    try:
        from stock_data.gui.services import DashboardService

        micro = DashboardService(project_root).micro
        lending_latest = micro.lending_market()
        if lending_latest:
            lending_rows.append({
                "market": "전체",
                "as_of": _date(lending_latest.get("date")),
                "value": _value(lending_latest.get("balance_amount")),
                "change_1d": _value(lending_latest.get("change_1d")),
                "change_5d": _value(lending_latest.get("change_5d")),
                "unit": "원",
            })
        for row in micro.breadth():
            breadth_rows.append({
                "market": row.get("market"),
                "as_of": _date(row.get("date")), "advancing": _nan_to_none(row.get("advancing")),
                "declining": _nan_to_none(row.get("declining")), "unchanged": _nan_to_none(row.get("unchanged")),
                "ad_ratio": _value(row.get("ad_ratio")),
            })
    except (KeyError, OSError, PermissionError, TypeError, ValueError) as error:
        micro_reason = f"시장 미시구조 데이터를 읽을 수 없습니다: {error}"

    available = any(item.get("status") == "VALUE" for item in markets) or credit.get("status") == "VALUE" or lending is not None or bool(lending_rows) or bool(breadth_rows)
    return {
        "status": "VALUE" if available else "UNAVAILABLE",
        "reason": None if available else "표시 가능한 수급·잔고 상세가 없습니다.",
        "range": normalized_range,
        "markets": markets,
        "credit": credit,
        **({"lending": lending} if lending is not None else {}),
        "microstructure": {
            "status": "VALUE" if lending_rows or breadth_rows else "UNAVAILABLE",
            "reason": None if lending_rows or breadth_rows else (micro_reason or "시장 미시구조 데이터가 없습니다."),
            "breadth": {"status": "VALUE", "rows": breadth_rows} if breadth_rows else _unavailable(micro_reason or "등락 종목 데이터가 없습니다."),
            "lending_summary": {"status": "VALUE", "rows": lending_rows} if lending_rows else _unavailable(micro_reason or "대차잔고 요약이 없습니다."),
        },
    }


def build_valuation(
    project_root: Path, *, range_key: str = "1Y",
) -> dict[str, object]:
    history_range = _history_range(range_key)
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
        series_view = _range_view(valid, history_range)
        markets.append({
            "status": "VALUE", "market": market,
            "series": {
                "per": _indicator_points(series_view, "weighted_per"),
                "pbr": _indicator_points(series_view, "weighted_pbr"),
            },
            "axes": {
                "per": {"side": "left", "minimum": 0},
                "pbr": {"side": "right", "minimum": 0},
            },
            "secondary_axis": True,
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


def build_market_page_payload(
    project_root: Path, *, flows_range: str = "60D",
) -> dict[str, object]:
    """Build one cached, range-bounded Market payload."""
    root = Path(project_root).resolve()
    request_range = flows_range if flows_range in MARKET_RANGE_KEYS else "60D"
    normalized_flow_range = request_range if request_range in FLOW_RANGE_SESSIONS else "60D"
    history_range = _history_range(request_range)
    cache_key = (str(root), request_range)
    cached = _MARKET_CACHE.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < _MARKET_CACHE_TTL_SECONDS:
        return cached[1]

    def safely(builder, label: str) -> dict[str, object]:
        try:
            return builder()
        except (KeyError, OSError, PermissionError, RuntimeError, TypeError, ValueError) as error:
            return _unavailable(f"{label}을(를) 읽을 수 없습니다: {error}")

    payload = {
        "schema_version": 1,
        "flows_range": normalized_flow_range,
        "history_range": history_range,
        "explanations": METRIC_EXPLANATIONS,
        "chart_symbols": build_chart_symbols(root),
        "sections": {
            "derivatives": safely(lambda: build_derivatives(root, range_key=history_range), "파생 상세"),
            "flows": safely(
                lambda: build_flows_and_balances(
                    root, range_key=normalized_flow_range,
                    history_range_key=history_range,
                ),
                "수급·잔고 상세",
            ),
            "valuation": safely(lambda: build_valuation(root, range_key=history_range), "밸류에이션"),
        },
    }
    _MARKET_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


__all__ = [
    "build_derivatives", "build_flows_and_balances", "build_market_chart_payload",
    "build_market_page_payload", "build_valuation", "format_compact_kr",
    "FLOW_RANGE_SESSIONS", "HISTORY_RANGES", "METRIC_EXPLANATIONS", "_cumulative_points",
    "_flow_range_view", "_localized_warning", "_range_view",
]
