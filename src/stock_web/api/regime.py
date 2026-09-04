"""Pure, provider-free market-regime projections for the local web UI."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd

from stock_data.gui.query import LocalParquetQuery
from stock_data.gui.services import DashboardService
from stock_web.api.indicators import rsi_latest


DEFAULT_RULES_PATH = Path(
    r"C:\Users\k4545\Desktop\Obsidian\Investing\30_규칙\투자 규칙.md"
)
WEB_SETTINGS_RELATIVE = Path("artifacts/local_user/web_settings.json")


def resolve_rules_path(project_root: Path | None = None) -> Path:
    """Env var first, then the local (git-ignored) web settings file, then the default.

    The Obsidian vault can move when Google Drive changes sync mode, so the path is
    never hard-coded in tracked code; a missing file only disables the rules card.
    """
    override = os.environ.get("STOCK_WEB_RULES_PATH")
    if override:
        return Path(override)
    if project_root is not None:
        settings = project_root / WEB_SETTINGS_RELATIVE
        if settings.is_file():
            try:
                import json

                value = json.loads(settings.read_text(encoding="utf-8")).get("rules_path")
                if isinstance(value, str) and value:
                    return Path(value)
            except Exception:
                pass
    return DEFAULT_RULES_PATH


def oversold_strength(
    rsi: float | None,
    ma60_distance: float | None,
    volatility_percentile: float | None,
) -> tuple[float, tuple[tuple[str, float], ...]] | None:
    """Port of ``MainWindow._oversold_strength`` without importing Qt."""
    if rsi is None or ma60_distance is None or volatility_percentile is None:
        return None
    rsi_points = min(4.0, max(0.0, (50.0 - rsi) / 35.0 * 4.0))
    distance_points = min(3.0, max(0.0, -ma60_distance / 10.0 * 3.0))
    volatility_points = min(
        3.0, max(0.0, (volatility_percentile - 50.0) / 50.0 * 3.0)
    )
    components = (
        ("RSI14", rsi_points),
        ("이격", distance_points),
        ("변동성", volatility_points),
    )
    return round(sum(value for _label, value in components), 1), components


def temperature_label(
    rsi: float | None, moving_average_distance: float | None,
) -> str:
    """Use the Qt summary's corroborated 30/70 and trend-side thresholds."""
    if rsi is not None and moving_average_distance is not None:
        if rsi > 70.0 and moving_average_distance > 0.0:
            return "과열"
        if rsi < 30.0 and moving_average_distance < 0.0:
            return "침체"
    return "중립"


def global_risk_temperature(
    spread_level: float | None,
    spread_change_1m: float | None,
    yield_change_bp: float | None,
    wti_change_pct: float | None,
) -> str:
    """Classify corroborated macro stress/heat without mixing market cards."""
    recessionary = sum((
        spread_level is not None and spread_level < 0.0,
        spread_change_1m is not None and spread_change_1m <= -0.25,
        yield_change_bp is not None and yield_change_bp <= -25.0,
        wti_change_pct is not None and wti_change_pct <= -10.0,
    ))
    heated = sum((
        spread_level is not None and spread_level > 0.5,
        spread_change_1m is not None and spread_change_1m >= 0.25,
        yield_change_bp is not None and yield_change_bp >= 25.0,
        wti_change_pct is not None and wti_change_pct >= 10.0,
    ))
    if recessionary >= 2:
        return "침체"
    if heated >= 2:
        return "과열"
    return "중립"


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


def _last(frame: pd.DataFrame, column: str) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return _number(values.iloc[-1]) if not values.empty else None


def _percentile(frame: pd.DataFrame, column: str, rows: int) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna().tail(rows)
    return _number(values.rank(pct=True).iloc[-1] * 100.0) if not values.empty else None


def _change(frame: pd.DataFrame, column: str, periods: int, *, percent: bool) -> float | None:
    if frame.empty or column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if len(values) <= periods:
        return None
    current, previous = float(values.iloc[-1]), float(values.iloc[-periods - 1])
    if percent:
        return (current / previous - 1.0) * 100.0 if previous else None
    return current - previous


def _fmt(value: float | None, pattern: str) -> str:
    return "표시 불가" if value is None else pattern.format(value)


_NO_EVIDENCE_VALUES = frozenset({"근거 없음", "표시 불가", "수집 추가 필요", ""})


def _evidence_row(
    label: str, value: object, *, hint: str | None = None,
) -> dict[str, object]:
    display = "표시 불가" if value is None else str(value)
    return {
        "label": label,
        "value": display,
        "evidence": value is not None and display not in _NO_EVIDENCE_VALUES,
        "hint": hint,
    }


def _foreign_sell_streak(query: LocalParquetQuery) -> int | None:
    frame = query.tail(
        "normalized/kr_market_investor_trading_daily",
        rows=40,
        columns=["date", "market", "foreigner_buy_amount", "foreigner_sell_amount"],
        partitions={"market": "KOSPI"},
    )
    if frame.empty:
        return None
    net = (
        pd.to_numeric(frame["foreigner_buy_amount"], errors="coerce")
        - pd.to_numeric(frame["foreigner_sell_amount"], errors="coerce")
    ).dropna()
    streak = 0
    for value in reversed(net.tolist()):
        if value >= 0:
            break
        streak += 1
    return streak


def _rules_values(path: Path) -> tuple[dict[str, float], bool]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}, False
    values: dict[str, float] = {}
    rule_rows = 0
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in {"항목", "---"} or set(cells[0]) == {"-"}:
            continue
        rule_rows += 1
        raw = cells[1]
        if not raw or "[채우기]" in raw:
            continue
        match = re.search(r"[-+]?\d+(?:\.\d+)?", raw.replace(",", ""))
        if match:
            values[cells[0]] = float(match.group())
    return values, rule_rows > 0


def build_rules(
    account: dict[str, object], markets: list[dict[str, object]],
    project_root: Path | None = None,
) -> dict[str, object] | None:
    path = resolve_rules_path(project_root)
    values, has_rows = _rules_values(path)
    if not has_rows or not values:
        return None

    nominal = _number(account.get("leveraged_weight_pct"))
    exposure = _number(account.get("effective_exposure_pct"))
    cash = _number(account.get("cash_pct"))
    short_treasury = _number(account.get("short_treasury_pct")) or 0.0
    max_nominal = values.get("레버리지 ETF 최대 비중")
    hot_cap = values.get("과열 판정 시 레버리지 상한")
    min_cash = values.get("최소 현금 비중")
    rows = [
        ["레버리지 ETF 비중 (명목)", _fmt(nominal, "{:.0f}%"),
         f"/ 한도 {max_nominal:.0f}%" if max_nominal is not None else ""],
        ["실효 노출 (비중 x 배수)", _fmt(exposure, "{:.0f}%"),
         "= 보유 비중 x 확인된 배수"],
        ["현금 · 단기국채", f"{_fmt(cash, '{:.0f}%')} · {short_treasury:.0f}%",
         f"/ 최소 {min_cash:.0f}%" if min_cash is not None else ""],
    ]
    warnings: list[str] = []
    if nominal is not None and max_nominal is not None and nominal > max_nominal:
        warnings.append("레버리지 ETF 명목 비중이 사용자 한도를 초과합니다.")
    if (
        nominal is not None and hot_cap is not None
        and any(market.get("temperature") == "과열" for market in markets)
        and nominal > hot_cap
    ):
        warnings.append("과열 시 사용자 레버리지 상한을 초과합니다.")
    if cash is not None and min_cash is not None and cash + short_treasury < min_cash:
        warnings.append("현금·단기국채 비중이 사용자 최소값보다 낮습니다.")
    return {
        "rows": rows,
        "warning": " ".join(warnings),
        "source": path.name,
    }


def _index_rsi_and_ma200_distance(service: object, symbol: str) -> tuple[float | None, float | None]:
    """RSI14 and distance to the 200-day mean for one retained global index (dashboard asset key or symbol)."""
    from stock_data.gui.services import DASHBOARD_ASSETS

    key = next(
        (name for name, spec in DASHBOARD_ASSETS.items()
         if spec.get("kind") == "global" and spec.get("symbol") == symbol),
        symbol,
    )
    try:
        frame = service.index.asset_series(key, "1Y")
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        return None, None
    if frame.empty or "close" not in frame:
        return None, None
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    rsi = rsi_latest(close)
    ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else float("nan")
    distance = (
        (float(close.iloc[-1]) / float(ma200) - 1.0) * 100.0
        if pd.notna(ma200) and ma200 else None
    )
    return rsi, distance


def _pair_fmt(rsi: float | None, distance: float | None) -> str | None:
    if rsi is None and distance is None:
        return None
    return f"{_fmt(rsi, '{:.1f}') or '—'} · {_fmt(distance, '{:+.1f}%') or '—'}"


def build_regime(project_root: Path, account: dict[str, object]) -> dict[str, object]:
    service = DashboardService(project_root)
    query = service.query

    try:
        kospi = service.index.series("KOSPI", "1Y")
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        kospi = pd.DataFrame()
    kr_rsi = rsi_latest(kospi["close"]) if "close" in kospi else None
    kr_disparity = _last(kospi, "disparity60")
    kr_distance = kr_disparity - 100.0 if kr_disparity is not None else None

    try:
        sp500 = service.index.asset_series("SP500", "1Y")
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        sp500 = pd.DataFrame()
    us_rsi = rsi_latest(sp500["close"]) if "close" in sp500 else None
    if not sp500.empty and "close" in sp500:
        close = pd.to_numeric(sp500["close"], errors="coerce").dropna()
        ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else float("nan")
        us_distance = (
            (float(close.iloc[-1]) / float(ma200) - 1.0) * 100.0
            if pd.notna(ma200) and ma200 else None
        )
    else:
        us_distance = None

    try:
        volatility = service.volatility(days=250)
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        volatility = {}
    vk_pct = _number(volatility.get("VKOSPI", {}).get("percentile_250d"))
    vix_pct = _number(volatility.get("VIX", {}).get("percentile_250d"))

    valuation_pct = None
    try:
        valuation = service.market_valuation_views().get("KOSPI")
        window = next(
            (item for item in getattr(valuation, "rolling_windows", ())
             if getattr(item, "window_years", None) == 5),
            None,
        )
        valuation_pct = _number(getattr(window, "per_percentile", None))
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        pass

    credit = query.tail(
        "normalized/kr_credit_balance_daily", rows=370,
        columns=["date", "credit_financing_total"],
    )
    credit_pct = _percentile(credit, "credit_financing_total", 252)
    try:
        foreign_streak = _foreign_sell_streak(query)
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        foreign_streak = None

    kr_technical = all(value is not None for value in (kr_rsi, kr_distance, vk_pct))
    kr_axes = int(kr_technical) + int(valuation_pct is not None)
    kr_score = oversold_strength(kr_rsi, kr_distance, vk_pct)
    kr_evidence = [
        _evidence_row("KOSPI RSI14", _fmt(kr_rsi, "{:.1f}")),
        _evidence_row("60일선 대비", _fmt(kr_distance, "{:+.1f}%")),
        _evidence_row(
            "VKOSPI 250일 백분위", _fmt(vk_pct, "{:.0f}%"), hint="낮을수록 안정",
        ),
        _evidence_row(
            "KRX PER 5년 백분위", _fmt(valuation_pct, "{:.0f}%"), hint="낮을수록 저평가",
        ),
        _evidence_row(
            "신용잔고 1년 백분위", _fmt(credit_pct, "{:.0f}%"), hint="높을수록 과열",
        ),
        _evidence_row(
            "외국인 연속 순매도",
            None if foreign_streak is None else f"{foreign_streak}일",
        ),
        _evidence_row(
            "과매도 강도", None if kr_score is None else f"{kr_score[0]:.1f}/10",
            hint="높을수록 과매도",
        ),
        _evidence_row("실적 모멘텀", "근거 없음"),
    ]

    us_technical = all(value is not None for value in (us_rsi, us_distance, vix_pct))
    us_axes = int(us_technical)
    us_score = oversold_strength(us_rsi, us_distance, vix_pct)
    # The user's US exposure is technology/semiconductors (TQQQ, SOXL, SKHY), so the
    # US card carries sub-verdicts for NASDAQ-100 and the SOX index next to the S&P 500.
    tech_rsi, tech_distance = _index_rsi_and_ma200_distance(service, "NASDAQ100")
    semis_rsi, semis_distance = _index_rsi_and_ma200_distance(service, "SOX")
    tech_label = (
        temperature_label(tech_rsi, tech_distance)
        if tech_rsi is not None and tech_distance is not None else "자료 없음"
    )
    semis_label = (
        temperature_label(semis_rsi, semis_distance)
        if semis_rsi is not None and semis_distance is not None else "자료 없음"
    )
    us_evidence = [
        _evidence_row("S&P 500 RSI14", _fmt(us_rsi, "{:.1f}")),
        _evidence_row("200일선 대비", _fmt(us_distance, "{:+.1f}%")),
        _evidence_row("NASDAQ100 RSI14 · 200일선", _pair_fmt(tech_rsi, tech_distance)),
        _evidence_row("SOX RSI14 · 200일선", _pair_fmt(semis_rsi, semis_distance)),
        _evidence_row(
            "VIX 250일 백분위", _fmt(vix_pct, "{:.0f}%"), hint="낮을수록 안정",
        ),
        _evidence_row(
            "과매도 강도", None if us_score is None else f"{us_score[0]:.1f}/10",
            hint="높을수록 과매도",
        ),
        _evidence_row("밸류에이션", "수집 추가 필요"),
        _evidence_row("실적 모멘텀", "근거 없음"),
    ]

    yields = query.tail(
        "normalized/fred_treasury_yield_daily", rows=64,
        columns=["date", "dgs10", "dgs2"],
    )
    valid_yields = yields.copy()
    if not valid_yields.empty:
        valid_yields["spread"] = (
            pd.to_numeric(valid_yields["dgs10"], errors="coerce")
            - pd.to_numeric(valid_yields["dgs2"], errors="coerce")
        )
    spread_level = _last(valid_yields, "spread")
    spread_change = _change(valid_yields, "spread", 21, percent=False)
    yield_change = _change(valid_yields, "dgs10", 21, percent=False)
    yield_change_bp = yield_change * 100.0 if yield_change is not None else None
    try:
        wti = service.index.asset_series("WTI", "3M")
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        wti = pd.DataFrame()
    wti_change = _change(wti, "close", 21, percent=True)
    global_available = sum(
        value is not None
        for value in (spread_level, spread_change, yield_change_bp, wti_change)
    )
    global_evidence = [
        _evidence_row("10Y-2Y", _fmt(spread_level, "{:+.2f}%p")),
        _evidence_row("10Y-2Y 1개월", _fmt(spread_change, "{:+.2f}%p")),
        _evidence_row("미국 10Y 1개월", _fmt(yield_change_bp, "{:+.0f}bp")),
        _evidence_row("WTI 1개월", _fmt(wti_change, "{:+.1f}%")),
        _evidence_row("밸류에이션", "수집 추가 필요"),
        _evidence_row("실적 모멘텀", "근거 없음"),
    ]

    markets: list[dict[str, object]] = [
        {
            "title": "한국장",
            "temperature": temperature_label(kr_rsi, kr_distance),
            "hot": temperature_label(kr_rsi, kr_distance) == "과열",
            "subtitle": f"자료 {kr_axes}/3 · 실적 축 없음",
            "evidence": kr_evidence,
        },
        {
            "title": "미국장",
            "temperature": temperature_label(us_rsi, us_distance),
            "hot": temperature_label(us_rsi, us_distance) == "과열",
            "subtitle": f"기술 {tech_label} · 반도체 {semis_label} · 자료 {us_axes}/3",
            "sub_verdicts": {"NASDAQ100": tech_label, "SOX": semis_label},
            "evidence": us_evidence,
        },
        {
            "title": "글로벌 위험",
            "temperature": global_risk_temperature(
                spread_level, spread_change, yield_change_bp, wti_change,
            ),
            "hot": global_risk_temperature(
                spread_level, spread_change, yield_change_bp, wti_change,
            ) == "과열",
            "subtitle": f"자료 {1 if global_available >= 3 else 0}/3 · 매크로 축",
            "evidence": global_evidence,
        },
    ]
    return {"markets": markets, "rules": build_rules(account, markets, project_root)}


__all__ = [
    "build_regime", "build_rules", "global_risk_temperature",
    "oversold_strength", "temperature_label",
]
