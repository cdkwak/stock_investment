"""Pure, provider-free market-regime projections for the local web UI."""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from math import log, sqrt
from pathlib import Path

import pandas as pd

from stock_data.gui.query import LocalParquetQuery
from stock_data.gui.services import DashboardService
from stock_web.api.indicators import rsi_latest


DEFAULT_RULES_PATH = Path(
    r"C:\Users\k4545\Desktop\Obsidian\Investing\30_규칙\투자 규칙.md"
)
WEB_SETTINGS_RELATIVE = Path("artifacts/local_user/web_settings.json")
REGIME_HISTORY_SESSIONS = 2_520
REGIME_MIN_SESSIONS = 750
REALIZED_VOLATILITY_SESSIONS = 20
# Implied-volatility axis (review 2026-09-06 00:10). VIX/VKOSPI are ranked in the SAME 10-year
# window as the trend axis (2,520 sessions): a 250-session window locked inside one abnormal
# regime turned a 23-year 94th-percentile VKOSPI into "40% · calm" and thinned the VIX low tail
# (250d 2.8% vs 10y 28%). The 250-session percentile is still reported next to the raw value.
IMPLIED_VOLATILITY_RECENT_SESSIONS = 250
# Hysteresis: ±1 switches on at the enter band and only switches off past the exit band, so a
# reading hovering at the threshold cannot flip the verdict every other session (17 flips in
# 45 sessions were measured on the plain 20% threshold).
VOLATILITY_CALM_ENTER = 20.0
VOLATILITY_CALM_EXIT = 30.0
VOLATILITY_STRESS_ENTER = 80.0
VOLATILITY_STRESS_EXIT = 70.0
HYSTERESIS_LOOKBACK_SESSIONS = 500
REGIME_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class _RegimeCacheEntry:
    created_at: float
    signature: tuple[tuple[Path, int, int], ...]
    markets: list[dict[str, object]]


_REGIME_CACHE: dict[str, _RegimeCacheEntry] = {}
_REGIME_CACHE_LOCK = threading.Lock()


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
    """0–10 oversold gauge: RSI14 (≤4) + moving-average distance (≤3) + volatility (≤3).

    The desktop ``MainWindow._oversold_strength`` this was ported from no longer exists; this
    function is the only definition. Since 2026-09-05 the volatility term takes the same
    10-year percentile as the regime score (it was the 250-session percentile before), so
    the Korean gauge moved 2.4 → 5.0 that day without any price change — the evidence row
    shows the three terms so the reader can see which one moved.
    """
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


OVERSOLD_HINT = "높을수록 과매도 · 변동성 항은 10년 백분위(09-05 척도 변경 · 이전 250일)"


def _oversold_evidence_value(
    oversold: tuple[float, tuple[tuple[str, float], ...]] | None,
) -> str | None:
    """'5.0/10 (RSI14 0.2 · 이격 2.2 · 변동성 2.6)' — the terms explain any jump."""
    if oversold is None:
        return None
    total, components = oversold
    terms = " · ".join(f"{label} {value:.1f}" for label, value in components)
    return f"{total:.1f}/10 ({terms})"


def _market_score_components(
    rsi: float | None,
    trend_percentile: float | None,
    volatility_percentile: float | None,
) -> tuple[int | None, int | None, int | None]:
    rsi_component = (
        None if rsi is None or pd.isna(rsi)
        else 2 if rsi >= 80.0
        else 1 if rsi >= 70.0
        else -2 if rsi <= 20.0
        else -1 if rsi <= 30.0
        else 0
    )
    trend_component = (
        None if trend_percentile is None or pd.isna(trend_percentile)
        else 2 if trend_percentile >= 97.0
        else 1 if trend_percentile >= 90.0
        else -2 if trend_percentile <= 3.0
        else -1 if trend_percentile <= 10.0
        else 0
    )
    volatility_component = (
        None if volatility_percentile is None or pd.isna(volatility_percentile)
        else 1 if volatility_percentile <= 20.0
        else -1 if volatility_percentile >= 80.0 else 0
    )
    return rsi_component, trend_component, volatility_component


def _aggregate_market_score(
    components: tuple[int | None, int | None, int | None],
) -> tuple[int | None, int | None, str | None]:
    available = tuple(value for value in components if value is not None)
    if not available:
        return None, None, None
    raw = sum(available)
    bounded = max(-2, min(2, raw))

    # Volatility modifies the raw score but is not a second directional price
    # confirmation. This stops low volatility from manufacturing an extreme.
    rsi_component, trend_component, volatility_component = components
    directional = (rsi_component, trend_component)
    if raw >= 2 and sum(value is not None and value > 0 for value in directional) < 2:
        return raw, 1, "과열은 RSI14와 추세의 서로 다른 두 근거가 필요"
    if raw <= -2 and sum(value is not None and value < 0 for value in directional) < 2:
        return raw, -1, "침체는 RSI14와 추세의 서로 다른 두 근거가 필요"
    if (
        volatility_component not in (None, 0)
        and all(value in (None, 0) for value in directional)
    ):
        return raw, bounded, "변동성 단독으로는 ±1까지"
    return raw, bounded, None


def market_score(
    rsi: float | None,
    trend_percentile: float | None,
    vol_percentile: float | None,
) -> int | None:
    """Return a corroboration-capped score from momentum, trend, and volatility."""
    components = _market_score_components(rsi, trend_percentile, vol_percentile)
    return _aggregate_market_score(components)[1]


def score_label(score: int | None) -> str:
    """Map the graded market score to its Korean display label."""
    if score is None:
        return "자료 없음"
    if score <= -2:
        return "침체"
    if score == -1:
        return "약세"
    if score == 0:
        return "중립"
    if score == 1:
        return "강세"
    return "과열"


def temperature_label(
    rsi: float | None,
    moving_average_distance: float | None,
    volatility_percentile: float | None = None,
) -> str:
    """Compatibility wrapper for callers that still pass an absolute MA distance."""
    trend_percentile = (
        None if moving_average_distance is None or pd.isna(moving_average_distance)
        else 90.0 if moving_average_distance >= 5.0
        else 10.0 if moving_average_distance <= -5.0
        else 50.0
    )
    return score_label(market_score(
        rsi, trend_percentile, volatility_percentile,
    ))


def global_risk_signal_counts(
    spread_level: float | None,
    spread_change_1m: float | None,
    yield_change_bp: float | None,
    wti_change_pct: float | None,
) -> tuple[int, int]:
    """(recessionary, heated) signal counts behind the 글로벌 위험 verdict."""
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
    return int(recessionary), int(heated)


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


def _latest_percentile(
    values: pd.Series, *, rows: int = REGIME_HISTORY_SESSIONS,
    minimum: int = REGIME_MIN_SESSIONS,
) -> float | None:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.empty or pd.isna(numeric.iloc[-1]):
        return None
    retained = numeric.dropna().tail(rows)
    if len(retained) < minimum:
        return None
    return _number(retained.rank(pct=True).iloc[-1] * 100.0)


def _plain_volatility_contribution(percentile: float | None) -> int | None:
    if percentile is None or pd.isna(percentile):
        return None
    if percentile <= VOLATILITY_CALM_ENTER:
        return 1
    if percentile >= VOLATILITY_STRESS_ENTER:
        return -1
    return 0


def _volatility_contribution_with_hysteresis(
    percentiles: pd.Series,
) -> tuple[int | None, int | None, bool]:
    """Walk the rolling-percentile history; a ±1 state only clears past its exit band.

    Returns (contribution, plain_threshold_contribution, held) where ``held`` is True when
    hysteresis kept a state the plain thresholds would already have dropped.
    """
    values = pd.to_numeric(percentiles, errors="coerce").dropna().tail(
        HYSTERESIS_LOOKBACK_SESSIONS,
    )
    if values.empty:
        return None, None, False
    state: int | None = None
    for percentile in values:
        contribution = _plain_volatility_contribution(float(percentile))
        if contribution == 0 and state == 1 and percentile <= VOLATILITY_CALM_EXIT:
            contribution = 1
        elif contribution == 0 and state == -1 and percentile >= VOLATILITY_STRESS_EXIT:
            contribution = -1
        state = contribution
    plain = _plain_volatility_contribution(float(values.iloc[-1]))
    return state, plain, state != plain


def _empty_volatility_axis() -> dict[str, object]:
    return {
        "value": None, "as_of": None, "percentile_250d": None, "percentile_10y": None,
        "contribution": None, "contribution_plain": None, "held": False,
    }


def _implied_volatility_axis(
    query: LocalParquetQuery, dataset: str, *, date_column: str, value_column: str,
) -> dict[str, object]:
    """Read one implied-volatility index and rank it like the trend axis (10-year window)."""
    try:
        frame = query.tail(
            dataset, rows=REGIME_HISTORY_SESSIONS + 64, columns=[date_column, value_column],
        )
    except (AttributeError, KeyError, OSError, PermissionError, TypeError, ValueError):
        return _empty_volatility_axis()
    if frame is None or frame.empty or value_column not in frame or date_column not in frame:
        return _empty_volatility_axis()
    values = pd.to_numeric(frame[value_column], errors="coerce")
    keep = values.notna()
    if not keep.any():
        return _empty_volatility_axis()
    series = values[keep].reset_index(drop=True)
    dates = pd.to_datetime(frame.loc[keep, date_column], errors="coerce").reset_index(drop=True)
    percentile_10y = _latest_percentile(series)
    recent = series.tail(IMPLIED_VOLATILITY_RECENT_SESSIONS)
    percentile_250d = _number(recent.rank(pct=True).iloc[-1] * 100.0)
    rolling = series.rolling(
        REGIME_HISTORY_SESSIONS, min_periods=REGIME_MIN_SESSIONS,
    ).rank(pct=True) * 100.0
    contribution, plain, held = _volatility_contribution_with_hysteresis(rolling)
    if percentile_10y is None:
        contribution, plain, held = None, None, False
    as_of = dates.iloc[-1]
    return {
        "value": _number(series.iloc[-1]),
        "as_of": None if pd.isna(as_of) else pd.Timestamp(as_of).date().isoformat(),
        "percentile_250d": percentile_250d,
        "percentile_10y": percentile_10y,
        "contribution": contribution,
        "contribution_plain": plain,
        "held": bool(held),
    }


def _last_date(frame: pd.DataFrame | None, column: str = "date") -> object:
    if frame is None or frame.empty or column not in frame:
        return None
    dates = pd.to_datetime(frame[column], errors="coerce").dropna()
    return None if dates.empty else dates.max()


def _short_date(value: object) -> str | None:
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(stamp) else stamp.strftime("%m-%d")


def _as_of_label(parts: list[tuple[str, object]]) -> str | None:
    """'기준 09-04' when every input shares a date, else '기준 KOSPI 09-04 · VKOSPI 09-03'."""
    dated = [(name, _short_date(value)) for name, value in parts]
    dated = [(name, date) for name, date in dated if date]
    if not dated:
        return None
    if len({date for _, date in dated}) == 1:
        return f"기준 {dated[0][1]}"
    return "기준 " + " · ".join(f"{name} {date}" for name, date in dated)


def _price_regime_metrics(
    frame: pd.DataFrame, moving_average_days: int,
) -> dict[str, float | None]:
    """Calculate regime inputs from one retained price history without I/O."""
    empty = {
        "rsi": None, "distance_pct": None, "trend_percentile": None,
        "realized_volatility": None, "realized_volatility_percentile": None,
    }
    if frame.empty or "close" not in frame:
        return empty
    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if close.empty:
        return empty
    moving_average = close.rolling(
        moving_average_days, min_periods=moving_average_days,
    ).mean()
    distances = (close / moving_average - 1.0) * 100.0
    log_returns = close.where(close > 0.0).map(log).diff()
    realized_volatility = (
        log_returns.rolling(
            REALIZED_VOLATILITY_SESSIONS,
            min_periods=REALIZED_VOLATILITY_SESSIONS,
        ).std(ddof=1) * sqrt(252.0) * 100.0
    )
    return {
        "rsi": rsi_latest(close),
        "distance_pct": _number(distances.iloc[-1]),
        "trend_percentile": _latest_percentile(distances),
        "realized_volatility": _number(realized_volatility.iloc[-1]),
        "realized_volatility_percentile": _latest_percentile(realized_volatility),
    }


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


def _score_text(score: int | None) -> str:
    if score is None:
        return "—"
    if score < 0:
        return f"−{abs(score)}"
    if score > 0:
        return f"+{score}"
    return "0"


def _market_verdict(
    rsi: float | None,
    trend_percentile: float | None,
    volatility_percentile: float | None,
    *,
    distance_pct: float | None,
    trend_name: str,
    volatility_name: str,
    volatility_axis: dict[str, object] | None = None,
) -> dict[str, object]:
    if volatility_axis is not None:
        volatility_percentile = _number(volatility_axis.get("percentile_10y"))
    values = tuple(
        None if value is None or pd.isna(value) else float(value)
        for value in (rsi, trend_percentile, volatility_percentile)
    )
    contributions = _market_score_components(*values)
    if volatility_axis is not None:
        axis_contribution = volatility_axis.get("contribution")
        contributions = (
            contributions[0], contributions[1],
            axis_contribution if isinstance(axis_contribution, int) else None,
        )
    raw_score, score, score_note = _aggregate_market_score(contributions)
    # Words must match the kind of evidence (obsidian session, 2026-09-05): VIX/실현변동성 is a
    # risk gauge, not a direction. When ±1 comes from volatility alone, say 저변동/고변동 —
    # never 강세/약세 — so the headline cannot be read as a price-direction call.
    rsi_component, trend_component, volatility_component = contributions
    volatility_only = (
        volatility_component not in (None, 0)
        and all(value in (None, 0) for value in (rsi_component, trend_component))
    )
    temperature = score_label(score)
    temperature_basis = "방향 근거" if score not in (None, 0) and not volatility_only else None
    if volatility_only and score is not None and score != 0:
        temperature = "저변동" if score > 0 else "고변동"
        temperature_basis = "변동성 단독 · 방향 근거 없음"
        score_note = score_note or "변동성 단독으로는 ±1까지"
    return {
        "score": score,
        "market_score_raw": raw_score,
        "market_score": score,
        "score_note": score_note,
        "score_max": 2,
        "temperature": temperature,
        "temperature_basis": temperature_basis,
        "hot": score is not None and score >= 2,
        "cold": score is not None and score <= -2,
        "components": [
            {"name": "RSI14", "value": values[0], "contribution": contributions[0]},
            {
                "name": f"{trend_name} 이격 10년 백분위",
                "value": values[1],
                "distance_pct": _number(distance_pct),
                "contribution": contributions[1],
            },
            {
                "name": volatility_name,
                "value": values[2],
                "contribution": contributions[2],
                **(
                    {
                        "raw_value": _number(volatility_axis.get("value")),
                        "percentile_250d": _number(volatility_axis.get("percentile_250d")),
                        "as_of": volatility_axis.get("as_of"),
                        "held": bool(volatility_axis.get("held")),
                        "contribution_plain": volatility_axis.get("contribution_plain"),
                    } if volatility_axis is not None else {}
                ),
            },
        ],
    }


def _volatility_evidence_value(component: dict[str, object]) -> str | None:
    """'39.3 · 250일 40% · 10년 93% → −1' — raw index, short and long percentile, contribution."""
    percentile_10y = _number(component.get("value"))
    if percentile_10y is None:
        return None
    parts: list[str] = []
    raw = _number(component.get("raw_value"))
    if raw is not None:
        parts.append(f"{raw:.1f}")
    percentile_250d = _number(component.get("percentile_250d"))
    if percentile_250d is not None:
        parts.append(f"250일 {percentile_250d:.0f}%")
    parts.append(f"10년 {percentile_10y:.0f}%")
    contribution = component.get("contribution")
    text = " · ".join(parts) + f" → {_score_text(contribution if isinstance(contribution, int) else None)}"
    if component.get("held"):
        text += " (유지 · 해제 밴드 안)"
    return text


def _trend_evidence_value(component: dict[str, object], trend_name: str) -> str:
    percentile = _number(component.get("value"))
    distance = _number(component.get("distance_pct"))
    if percentile is None or distance is None:
        return "자료 없음"
    sign = "+" if distance >= 0.0 else "−"
    contribution = component.get("contribution")
    # Same "→ ±N" tail as every other component row: the trend row is often the only
    # component that moves the score, so its contribution must be visible (06:00 review).
    return (
        f"{trend_name} {sign}{abs(distance):.1f}% "
        f"(10년 백분위 {percentile:.0f}%) → "
        f"{_score_text(contribution if isinstance(contribution, int) else None)}"
    )


def _component_evidence_value(
    component: dict[str, object], pattern: str,
) -> str | None:
    value = component.get("value")
    if value is None:
        return None
    display = pattern.format(value)
    if display.startswith("-"):
        display = f"−{display[1:]}"
    contribution = component.get("contribution")
    return f"{display} → {_score_text(contribution if isinstance(contribution, int) else None)}"


_NO_EVIDENCE_VALUES = frozenset(
    {"근거 없음", "자료 없음", "표시 불가", "수집 추가 필요", ""}
)


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
    # "모름" must not read as 0%: when the brokers did not report cash, say 미확인 and flag
    # that the weights below leave cash out of the denominator (review 09-05 11:40).
    cash_incomplete = (
        account.get("cash_complete") is False
        or bool(account.get("cash_unknown"))
        or ("cash_krw" in account and account.get("cash_krw") is None)
    )
    cash_known = cash is not None and not cash_incomplete
    cash_caveat = "" if cash_known else " · 현금 미반영(분모 과소 가능)"
    rows = [
        ["레버리지 ETF 비중 (명목)", _fmt(nominal, "{:.0f}%"),
         (f"/ 한도 {max_nominal:.0f}%" if max_nominal is not None else "") + cash_caveat],
        ["실효 노출 (비중 x 배수)", _fmt(exposure, "{:.0f}%"),
         "= 보유 비중 x 확인된 배수" + cash_caveat],
        ["현금 · 단기국채" if cash_known else "현금 · 단기국채 (현금 미확인)",
         (f"{_fmt(cash, '{:.0f}%')} · {short_treasury:.0f}%" if cash_known
          else f"미확인 · 단기국채 {short_treasury:.0f}%"),
         (f"/ 최소 {min_cash:.0f}%" if min_cash is not None else "") + ("" if cash_known else " · 현금 미확인이라 한도 판정 보류")],
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
    if cash_known and min_cash is not None and cash + short_treasury < min_cash:
        warnings.append("현금·단기국채 비중이 사용자 최소값보다 낮습니다.")
    return {
        "rows": rows,
        "warning": " ".join(warnings),
        "source": path.name,
    }


def _retained_price_history(
    query: LocalParquetQuery,
    dataset: str,
    *,
    partition: tuple[str, str],
) -> pd.DataFrame:
    """Read one exact retained symbol/market partition with only regime columns."""
    partition_name, partition_value = partition
    try:
        frame = query.read(
            dataset,
            columns=["date", "symbol", "close"],
            partitions={partition_name: partition_value},
        )
    except (AttributeError, KeyError, OSError, PermissionError, TypeError, ValueError):
        return pd.DataFrame()
    if frame.empty or "symbol" not in frame:
        return pd.DataFrame()
    frame = frame.loc[frame["symbol"].astype(str).eq(partition_value)].copy()
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)


def _index_regime_metrics(
    service: DashboardService, symbol: str, moving_average_days: int,
) -> dict[str, float | None]:
    """Read one exact retained index partition and calculate its inputs once."""
    frame = _retained_price_history(
        service.query,
        "normalized/global_index_price_daily",
        partition=("symbol", symbol),
    )
    if frame.empty:
        from stock_data.gui.services import DASHBOARD_ASSETS

        key = next(
            (name for name, spec in DASHBOARD_ASSETS.items()
             if spec.get("kind") == "global" and spec.get("symbol") == symbol),
            symbol,
        )
        try:
            frame = service.index.asset_series(key, "MAX")
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            frame = pd.DataFrame()
    return _price_regime_metrics(frame, moving_average_days)


def _market_valuation_percentile(
    project_root: Path, service: DashboardService,
) -> tuple[float | None, tuple[Path, ...]]:
    """Use the retained Health/state agreement to avoid rebuilding XKRX calendars."""
    state_path = project_root / "data/state/kr_index_fundamental_daily.json"
    health_path = project_root / "artifacts/daily_health/universe_data_v2_20260819.json"
    try:
        from stock_data.contracts.kr_index_fundamental_daily import (
            KR_INDEX_FUNDAMENTAL_DAILY,
        )

        state = json.loads(state_path.read_text(encoding="utf-8"))
        health = json.loads(health_path.read_text(encoding="utf-8"))
        accepted = state["last_accepted_market_date"]
        row = next(
            item for item in health["datasets"]
            if item.get("dataset", item.get("dataset_id")) == "kr_index_fundamental_daily"
        )
        if (
            state.get("schema_version") != 1
            or state.get("status") != "ACCEPTED_DESCRIPTIVE_NON_PREDICTIVE"
            or state.get("predictive_eligibility") != "NON_PREDICTIVE"
            or not isinstance(accepted, str)
            or row.get("latest") != accepted
            or row.get("expected") != accepted
            or row.get("display_status") != "CURRENT"
        ):
            raise ValueError("retained valuation freshness agreement differs")
        frame = service.query.read(
            "normalized/kr_index_fundamental_daily",
            columns=list(KR_INDEX_FUNDAMENTAL_DAILY.column_names),
        )
        if len(frame) != state.get("rows"):
            raise ValueError("retained valuation row count differs")
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime(
            "%Y-%m-%d"
        )
        frame = frame.sort_values(
            list(KR_INDEX_FUNDAMENTAL_DAILY.sort_key), kind="stable",
        ).reset_index(drop=True)
        views = service.build_market_valuation_views(
            frame, as_of=accepted, expected_as_of=accepted,
        )
    except (
        FileNotFoundError, StopIteration, json.JSONDecodeError, KeyError, OSError,
        PermissionError, TypeError, ValueError,
    ):
        views = service.market_valuation_views()
    valuation = views.get("KOSPI")
    window = next(
        (item for item in getattr(valuation, "rolling_windows", ())
         if getattr(item, "window_years", None) == 5),
        None,
    )
    return _number(getattr(window, "per_percentile", None)), (state_path, health_path)


def _build_regime_markets(
    project_root: Path,
) -> tuple[list[dict[str, object]], tuple[Path, ...]]:
    service = DashboardService(project_root)
    query = service.query

    kospi = _retained_price_history(
        query,
        "normalized/kr_index_daily",
        partition=("market", "KOSPI"),
    )
    if kospi.empty:
        try:
            kospi = service.index.series("KOSPI", "MAX")
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            kospi = pd.DataFrame()
    kr_metrics = _price_regime_metrics(kospi, 60)
    kr_rsi = kr_metrics["rsi"]
    kr_distance = kr_metrics["distance_pct"]
    kr_trend_pct = kr_metrics["trend_percentile"]

    sp500 = _retained_price_history(
        query,
        "normalized/global_index_price_daily",
        partition=("symbol", "SP500"),
    )
    if sp500.empty:
        try:
            sp500 = service.index.asset_series("SP500", "MAX")
        except (KeyError, OSError, PermissionError, TypeError, ValueError):
            sp500 = pd.DataFrame()
    us_metrics = _price_regime_metrics(sp500, 200)
    us_rsi = us_metrics["rsi"]
    us_distance = us_metrics["distance_pct"]
    us_trend_pct = us_metrics["trend_percentile"]

    vk_axis = _implied_volatility_axis(
        query, "normalized/kr_vkospi_daily", date_column="market_date", value_column="close",
    )
    vix_axis = _implied_volatility_axis(
        query, "normalized/fred_vix_daily", date_column="date", value_column="vixcls",
    )
    vk_pct = _number(vk_axis.get("percentile_10y"))
    vix_pct = _number(vix_axis.get("percentile_10y"))

    try:
        valuation_pct, valuation_inputs = _market_valuation_percentile(
            project_root, service,
        )
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        valuation_pct, valuation_inputs = None, ()

    credit = query.tail(
        "normalized/kr_credit_balance_daily", rows=370,
        columns=["date", "credit_financing_total"],
    )
    credit_pct = _percentile(credit, "credit_financing_total", 252)
    try:
        foreign_streak = _foreign_sell_streak(query)
    except (KeyError, OSError, PermissionError, TypeError, ValueError):
        foreign_streak = None

    kr_verdict = _market_verdict(
        kr_rsi, kr_trend_pct, vk_pct, distance_pct=kr_distance,
        trend_name="60일선", volatility_name="VKOSPI 10년 백분위",
        volatility_axis=vk_axis,
    )
    kr_available = sum(
        component["value"] is not None for component in kr_verdict["components"]
    )
    kr_oversold = oversold_strength(kr_rsi, kr_distance, vk_pct)
    kr_components = kr_verdict["components"]
    kr_evidence = [
        _evidence_row(
            "KOSPI RSI14", _component_evidence_value(kr_components[0], "{:.1f}"),
        ),
        _evidence_row(
            "추세 (60일선)", _trend_evidence_value(kr_components[1], "60일선"),
        ),
        _evidence_row(
            "VKOSPI 10년 백분위",
            _volatility_evidence_value(kr_components[2]),
            hint="낮을수록 안정",
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
            "과매도 강도", _oversold_evidence_value(kr_oversold), hint=OVERSOLD_HINT,
        ),
        _evidence_row("실적 모멘텀", "근거 없음"),
    ]

    us_verdict = _market_verdict(
        us_rsi, us_trend_pct, vix_pct, distance_pct=us_distance,
        trend_name="200일선", volatility_name="VIX 10년 백분위",
        volatility_axis=vix_axis,
    )
    us_available = sum(
        component["value"] is not None for component in us_verdict["components"]
    )
    us_oversold = oversold_strength(us_rsi, us_distance, vix_pct)
    us_components = us_verdict["components"]
    # The user's US exposure is technology/semiconductors (TQQQ, SOXL, SKHY), so the
    # US card carries sub-verdicts for NASDAQ-100 and the SOX index next to the S&P 500.
    tech_metrics = _index_regime_metrics(service, "NASDAQ100", 200)
    semis_metrics = _index_regime_metrics(service, "SOX", 200)
    tech_verdict = _market_verdict(
        tech_metrics["rsi"], tech_metrics["trend_percentile"],
        tech_metrics["realized_volatility_percentile"],
        distance_pct=tech_metrics["distance_pct"],
        trend_name="200일선", volatility_name="실현변동성 20일 백분위",
    )
    semis_verdict = _market_verdict(
        semis_metrics["rsi"], semis_metrics["trend_percentile"],
        semis_metrics["realized_volatility_percentile"],
        distance_pct=semis_metrics["distance_pct"],
        trend_name="200일선", volatility_name="실현변동성 20일 백분위",
    )
    us_evidence = [
        _evidence_row(
            "S&P 500 RSI14", _component_evidence_value(us_components[0], "{:.1f}"),
        ),
        _evidence_row(
            "추세 (200일선)", _trend_evidence_value(us_components[1], "200일선"),
        ),
        _evidence_row(
            "VIX 10년 백분위",
            _volatility_evidence_value(us_components[2]),
            hint="낮을수록 안정",
        ),
        _evidence_row(
            "NASDAQ-100 RSI14",
            _component_evidence_value(tech_verdict["components"][0], "{:.1f}"),
        ),
        _evidence_row(
            "NASDAQ-100 추세",
            _trend_evidence_value(tech_verdict["components"][1], "200일선"),
        ),
        _evidence_row(
            "NASDAQ-100 실현변동성 20일 백분위 (VXN 미보존)",
            _component_evidence_value(tech_verdict["components"][2], "{:.0f}%"),
            hint="낮을수록 안정",
        ),
        _evidence_row(
            "SOX RSI14",
            _component_evidence_value(semis_verdict["components"][0], "{:.1f}"),
        ),
        _evidence_row(
            "SOX 추세",
            _trend_evidence_value(semis_verdict["components"][1], "200일선"),
        ),
        _evidence_row(
            "SOX 실현변동성 20일 백분위",
            _component_evidence_value(semis_verdict["components"][2], "{:.0f}%"),
            hint="낮을수록 안정",
        ),
        _evidence_row(
            "과매도 강도", _oversold_evidence_value(us_oversold), hint=OVERSOLD_HINT,
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

    global_temperature = global_risk_temperature(spread_level, spread_change, yield_change_bp, wti_change)
    global_counts = global_risk_signal_counts(spread_level, spread_change, yield_change_bp, wti_change)
    # Each card says which session its inputs describe (review 2026-09-06 00:10: VIX rows were
    # 09-03 and VKOSPI rows 09-04 with no date anywhere while the VIX tile showed 09-05 intraday).
    kr_as_of = _as_of_label([("KOSPI", _last_date(kospi)), ("VKOSPI", vk_axis.get("as_of"))])
    us_as_of = _as_of_label([("S&P 500", _last_date(sp500)), ("VIX", vix_axis.get("as_of"))])
    global_as_of = _as_of_label([("금리", _last_date(valid_yields)), ("WTI", _last_date(wti))])
    markets: list[dict[str, object]] = [
        {
            "title": "한국장",
            **kr_verdict,
            "subtitle": f"점수 {_score_text(kr_verdict['score'])} · 자료 {kr_available}/3 · 실적 축 없음",
            "as_of_label": kr_as_of,
            "evidence": kr_evidence,
        },
        {
            "title": "미국장",
            **us_verdict,
            "as_of_label": us_as_of,
            "subtitle": (
                f"점수 {_score_text(us_verdict['score'])}"
                f" · 기술 {_score_text(tech_verdict['score'])}"
                f" · 반도체 {_score_text(semis_verdict['score'])}"
                " (부점수 변동성 축 = 자기 실현변동성)"
                f" · 자료 {us_available}/3"
            ),
            "sub_verdicts": {
                "NASDAQ100": tech_verdict,
                "SOX": semis_verdict,
            },
            "evidence": us_evidence,
        },
        {
            "title": "글로벌 위험",
            # No graded score here: two corroborating macro signals make 침체/과열, otherwise
            # nothing was judged — say so instead of a "중립" that looks like a verdict.
            "temperature": (
                global_temperature if global_temperature in ("침체", "과열") else "판정 없음"
            ),
            "temperature_basis": "매크로 신호 2개 이상일 때만 판정 · 점수 없음",
            "hot": global_temperature == "과열",
            "subtitle": f"자료 {1 if global_available >= 3 else 0}/3 · 매크로 축 · 침체 신호 {global_counts[0]} · 과열 신호 {global_counts[1]}",
            "as_of_label": global_as_of,
            "evidence": global_evidence,
        },
    ]
    parquet_inputs = tuple(dict.fromkeys(
        Path(path) for path in getattr(query, "files_read", ())
        if Path(path).suffix.lower() == ".parquet"
    ))
    watched_inputs: list[Path] = []
    for path in parquet_inputs:
        watched_inputs.extend((path, path.parent, path.parent.parent))
    inputs_read = tuple(dict.fromkeys((*watched_inputs, *valuation_inputs)))
    return markets, tuple(dict.fromkeys(inputs_read))


def _input_signature(
    paths: tuple[Path, ...],
) -> tuple[tuple[Path, int, int], ...] | None:
    signature: list[tuple[Path, int, int]] = []
    try:
        for path in paths:
            stat = path.stat()
            signature.append((path, stat.st_mtime_ns, stat.st_size))
    except OSError:
        return None
    return tuple(signature)


def clear_regime_cache(project_root: Path | None = None) -> None:
    """Clear cached market calculations, mainly for tests and explicit refreshes."""
    with _REGIME_CACHE_LOCK:
        if project_root is None:
            _REGIME_CACHE.clear()
        else:
            _REGIME_CACHE.pop(str(Path(project_root).resolve()), None)


def build_regime(project_root: Path, account: dict[str, object]) -> dict[str, object]:
    """Build account-independent market evidence once per retained-data signature."""
    root = Path(project_root).resolve()
    cache_key = str(root)
    now = time.monotonic()
    with _REGIME_CACHE_LOCK:
        cached = _REGIME_CACHE.get(cache_key)
        if cached is not None:
            current_signature = _input_signature(
                tuple(item[0] for item in cached.signature),
            )
            if (
                now - cached.created_at < REGIME_CACHE_TTL_SECONDS
                and current_signature == cached.signature
            ):
                markets = copy.deepcopy(cached.markets)
            else:
                markets, paths = _build_regime_markets(root)
                signature = _input_signature(paths) or ()
                _REGIME_CACHE[cache_key] = _RegimeCacheEntry(
                    created_at=time.monotonic(),
                    signature=signature,
                    markets=copy.deepcopy(markets),
                )
        else:
            markets, paths = _build_regime_markets(root)
            signature = _input_signature(paths) or ()
            _REGIME_CACHE[cache_key] = _RegimeCacheEntry(
                created_at=time.monotonic(),
                signature=signature,
                markets=copy.deepcopy(markets),
            )
    return {"markets": markets, "rules": build_rules(account, markets, root)}


__all__ = [
    "build_regime", "build_rules", "clear_regime_cache", "global_risk_temperature",
    "market_score", "oversold_strength", "score_label", "temperature_label",
]
