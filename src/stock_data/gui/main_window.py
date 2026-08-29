from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timezone
from pathlib import Path
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from uuid import uuid4

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6 import QtCharts, QtCore, QtGui, QtWidgets
from runtime_diagnostics import (
    RuntimeDiagnosticStore,
    new_session_id,
    safe_record_failure,
)

from stock_data.gui.account_snapshot_service import (
    AccountAllocationView,
    AccountHoldingView,
    AccountPortfolioView,
    AccountPortfolioPresentationView,
    AccountSourceActionView,
    AccountSnapshotState,
    AccountSnapshotView,
    LocalAccountPortfolioService,
    LocalAccountSnapshotService,
    LocalAccountSourceSpec,
    build_account_portfolio_presentation,
    build_account_source_action_views,
)
from stock_data.gui.google_sheet_account_import import load_appa_sheet_csv
from stock_data.gui.manual_account_store import (
    LocalManualAccountStore,
    ManualAccountPosition,
    ManualAccountRecord,
    ManualAccountRegistry,
    manual_account_registry_payload,
)
from stock_data.gui.backtest_service import BacktestExperimentView, BacktestResultService
from stock_data.gui.backtest_scenario_service import (
    SCENARIO_ADAPTER_VERSION,
    SCENARIO_ID,
    SCENARIO_INPUT_VERSION,
    BacktestScenarioInputs,
    BacktestScenarioService,
    BacktestScenarioView,
)
from stock_data.gui.dashboard_preferences import (
    CARD_IDS,
    ChartIndicatorPreferences,
    DEFAULT_PREFERENCES,
    SECTION_IDS,
    DashboardPreferences,
    DashboardPreferencesError,
    LocalDashboardPreferencesStore,
    INDICATOR_MODES,
    MARKET_PERIODS,
    WindowGeometry,
    safe_window_geometry,
    with_geometry,
)
from stock_data.gui.research_workspace_preferences import (
    DEFAULT_PREFERENCES as DEFAULT_RESEARCH_WORKSPACE_PREFERENCES,
    MAX_PRESETS as MAX_RESEARCH_WORKSPACE_PRESETS,
    PANEL_IDS as RESEARCH_WORKSPACE_PANEL_IDS,
    LocalResearchWorkspacePreferencesStore,
    PanelPreference as ResearchPanelPreference,
    ResearchWorkspacePreferences,
    ResearchWorkspacePreferencesError,
    WorkspacePreset,
)
from stock_data.gui.health_service import (
    DailyHealthArtifactService,
    HealthArtifactView,
    HealthDatasetRow,
    summarize_health_artifact,
)
from stock_data.gui.refresh_status import (
    RefreshStatusProjection,
    project_refresh_status,
)
from stock_data.gui.net_worth_service import (
    AssetClass,
    AssetEntry,
    BASE_CURRENCY,
    HolderRole,
    LiabilityClass,
    LiabilityEntry,
    LocalNetWorthHistoryStore,
    NetWorthHistoryRecord,
    NetWorthPersistenceError,
    NetWorthSnapshot,
    NetWorthValidationError,
    NetWorthView,
    NetWorthTimelineDeltaState,
    NetWorthTimelineDisplayState,
    NetWorthTimelinePoint,
    NetWorthTimelineView,
    SCHEMA_VERSION,
    ValuationMethod,
    ValuationSource,
    ValuationStatus,
    ValuationUncertainty,
    build_net_worth_timeline,
    parse_snapshot,
)
from stock_data.gui.services import (
    CurrentObservationCoverageView,
    DashboardAverageComparisonView,
    DashboardChartCoverage,
    DashboardCurrentStageView,
    DashboardDisplayState,
    DashboardMetricView,
    DashboardSparklineView,
    DashboardSeriesView,
    DashboardService,
    EquityIdentity,
    EquitySearchView,
    EquitySeriesView,
    InstrumentFactsView,
    IndexSeriesView,
    NormalizedBenchmarkComparisonView,
    MarketFundingView,
    MarketInvestorFlowView,
    MarketValuationView,
    TossShortWatchlistView,
    TreasuryRateView,
    VIXSourceView,
    instrument_facts_view,
    DASHBOARD_CHART_COVERAGE_ATTR,
)
from stock_data.gui.watchlist_service import (
    DEFAULT_LIST_ID,
    LocalWatchlistService,
    NamedWatchlist,
    WatchlistQuote,
    WatchlistState,
    quote_from_series,
)
from stock_data.gui.us_option_pcr_adapter import (
    USOptionPCRScopeView,
    current_us_option_pcr_scope_views,
)
from stock_data.contracts.vix_futures import VIXFuturesRouteStatus
from stock_data.gui.vix_futures_adapter import (
    VIXFuturesDashboardView,
    build_vix_futures_dashboard_view,
)
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.orchestration.account_privacy import (
    AccountSnapshotRemovalError,
    MASKED_VALUE,
    remove_retained_account_snapshots,
)
from stock_data.orchestration.toss_account_snapshot import AccountRefreshTrigger
from stock_research.candidate_discovery import (
    StockCandidateDiscoveryView,
    validate_candidate_discovery_view,
)
from stock_research.exploratory_scanner import (
    ExploratoryCandidateView,
    LocalExploratoryCandidateScanner,
    validate_exploratory_candidate_view,
)


DISPLAYABLE_FRESHNESS = {"CURRENT", "EXPECTED_LAG"}
DASHBOARD_MARKET_RENDER_POINT_BUDGET = 1200

# User-facing freshness copy.  The typed values remain available in tooltips and
# Data Status detail, but are not Dashboard headlines: CURRENT=latest finalized,
# EXPECTED_LAG=normal provider publication wait, STALE=refresh needed,
# UNKNOWN=confirmation needed, READ_FAILURE=local read failed, and
# MARKET_CLOSED_LAST_FINAL=latest completed market observation.
FRESHNESS_COPY = {
    "CURRENT": "최신 확정",
    "CURRENT_PROVISIONAL": "현재 잠정",
    "CURRENT_COMPLETED_15M": "15분 지연",
    "CURRENT_COMPLETED_30M": "30분 지연",
    "CURRENT_COMPLETED_60M": "60분 지연",
    "EXPECTED_LAG": "발행 대기",
    "STALE": "갱신 필요",
    "STALE_OR_MISSING": "갱신 필요",
    "UNKNOWN": "확인 필요",
    "MISSING": "확인 필요",
    "PARTIAL": "확인 필요",
    "CURRENT_GATE_BLOCKED": "실시간 없음",
    "BLOCKED": "표시 제한",
    "PROVIDER_DELAY": "발행 지연",
    "READ_FAILURE": "읽기 실패",
    "MARKET_CLOSED_LAST_FINAL": "장마감",
    "MARKET_CLOSED_LAST_VERIFIED": "휴장 · 최근 검증값",
    "60M_DELAYED": "60분 지연",
    "NOT_APPLICABLE": "해당 없음",
}


def _freshness_label(status: object) -> str:
    """Return the concise Korean display label for a typed freshness status."""
    return FRESHNESS_COPY.get(str(status or "UNKNOWN"), FRESHNESS_COPY["UNKNOWN"])


def _compact_freshness_label(status: object) -> str:
    """Keep card state visible without repeating long technical copy."""
    return {
        "CURRENT": "확정",
        "CURRENT_PROVISIONAL": "잠정",
        "CURRENT_COMPLETED_15M": "15분 지연",
        "CURRENT_COMPLETED_30M": "30분 지연",
        "CURRENT_COMPLETED_60M": "60분 지연",
        "EXPECTED_LAG": "발행 대기",
        "STALE": "갱신",
        "STALE_OR_MISSING": "갱신",
        "UNKNOWN": "확인",
        "MISSING": "확인",
        "PARTIAL": "확인",
        "CURRENT_GATE_BLOCKED": "실시간 없음",
        "BLOCKED": "제한",
        "READ_FAILURE": "읽기 실패",
        "MARKET_CLOSED_LAST_FINAL": "장마감",
        "MARKET_CLOSED_LAST_VERIFIED": "최근 마감",
    }.get(str(status or "UNKNOWN"), "확인")


def _refresh_projection_tooltip(projection: RefreshStatusProjection) -> str:
    lines = [
        f"contract={projection.contract_id}",
        f"generated_at_utc={projection.generated_at_utc}",
        f"overall={projection.overall_state}",
        "로컬 메타데이터만 읽음 · API/스케줄러/데이터 변경 없음",
    ]
    for surface in projection.surfaces:
        lines.append(
            f"{surface.surface_id}: operation={surface.operation_state}; "
            f"freshness={surface.freshness_state}; "
            f"source_as_of={surface.source_as_of or 'N/A'}; "
            f"last_success={surface.last_success_at or 'N/A'}; "
            f"next={surface.next_eligible_at or '확인되지 않음'}; "
            f"retry={surface.retry_capability}"
        )
    return "\n".join(lines)


def _is_read_failure(metric: DashboardMetricView | None) -> bool:
    if metric is None:
        return False
    reason = metric.unavailable_reason or ""
    return any(fragment in reason for fragment in ("읽을 수 없", "계약 검증", "스키마 오류", "로컬 파일 누락"))


def _freshness_message(status: object) -> str:
    return _freshness_label(status)


@dataclass(frozen=True)
class MarketSessionBarState:
    domestic_label: str
    domestic_open: bool
    us_label: str
    us_open: bool


def _market_session_bar_state(as_of_utc: object) -> MarketSessionBarState:
    """Return compact KRX/NXT and U.S. regular/after-hours session labels."""
    now = pd.Timestamp(as_of_utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("market-session bar clock must be timezone-aware")
    now = now.tz_convert("UTC")

    kr_calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    kr_now = now.tz_convert("Asia/Seoul")
    kr_date = kr_now.date()
    kr_trading_day = kr_calendar.is_trading_day(kr_date)
    krx_open = False
    if kr_trading_day:
        krx_started = pd.Timestamp(kr_calendar.session_open(kr_date)).tz_convert("UTC")
        krx_ended = pd.Timestamp(kr_calendar.session_close(kr_date)).tz_convert("UTC")
        krx_open = bool(krx_started <= now < krx_ended)
    nxt_started = pd.Timestamp(f"{kr_date.isoformat()} 08:00", tz="Asia/Seoul").tz_convert("UTC")
    nxt_ended = pd.Timestamp(f"{kr_date.isoformat()} 20:00", tz="Asia/Seoul").tz_convert("UTC")
    nxt_open = bool(kr_trading_day and nxt_started <= now < nxt_ended)
    domestic_open = krx_open or nxt_open
    domestic_label = (
        f"KRX {'장중' if krx_open else '장마감'} 09:00~15:30"
        f" · NXT {'장중' if nxt_open else '장마감'} 08:00~20:00"
    )

    us_calendar = ExchangeTradingCalendar(ExchangeMarket.US)
    ny_now = now.tz_convert("America/New_York")
    us_date = ny_now.date()
    us_trading_day = us_calendar.is_trading_day(us_date)
    us_regular_open = False
    us_after_open = False
    if us_trading_day:
        us_started = pd.Timestamp(us_calendar.session_open(us_date)).tz_convert("UTC")
        us_ended = pd.Timestamp(us_calendar.session_close(us_date)).tz_convert("UTC")
        us_after_ended = pd.Timestamp(
            f"{us_date.isoformat()} 20:00", tz="America/New_York"
        ).tz_convert("UTC")
        us_regular_open = bool(us_started <= now < us_ended)
        us_after_open = bool(us_ended <= now < us_after_ended)
        start_kst = us_started.tz_convert("Asia/Seoul").strftime("%H:%M")
        end_kst = us_ended.tz_convert("Asia/Seoul").strftime("%H:%M")
        after_kst = us_after_ended.tz_convert("Asia/Seoul").strftime("%H:%M")
        if us_regular_open:
            us_label = f"미국 정규장 {start_kst}~{end_kst} KST"
        elif us_after_open:
            us_label = f"미국 애프터장 {end_kst}~{after_kst} KST"
        elif now < us_started:
            us_label = f"미국 장 시작 전 · {start_kst} KST"
        else:
            us_label = "미국 장마감"
    else:
        us_label = "미국 장마감"
    us_open = us_regular_open or us_after_open
    return MarketSessionBarState(
        domestic_label=domestic_label,
        domestic_open=domestic_open,
        us_label=us_label,
        us_open=us_open,
    )


def _display_message(metric: DashboardMetricView | None) -> str:
    """Keep fail-closed cards empty while explaining the user-visible state."""
    if _is_read_failure(metric):
        return FRESHNESS_COPY["READ_FAILURE"]
    if metric is None:
        return FRESHNESS_COPY["UNKNOWN"]
    if metric.freshness == "CURRENT_GATE_BLOCKED":
        reason = metric.unavailable_reason or ""
        if any(token in reason for token in (
            "CURRENT_SOURCE_AGE_OVER_60M", "CURRENT_SOURCE_DATE_NOT_TODAY_KST",
        )):
            return "갱신 필요"
        if "CURRENT_SOURCE_TIMESTAMP_REQUIRED" in reason:
            return "실시간 미연동"
        return "실시간 표시 제한"
    if metric.display_state is DashboardDisplayState.REFRESH_REQUIRED:
        return _freshness_label(metric.freshness)
    return _freshness_label(metric.freshness)


def _pit_display(status: object) -> str:
    text = str(status or "UNKNOWN")
    if text.startswith("PIT_SAFE"):
        return f"{text} · 백테스트 가능"
    if text == "PIT_LIMITED":
        return f"{text} · 설명용 · 예측 사용 불가"
    if text in {"PIT_BLOCKED", "NON_PREDICTIVE", "RESEARCH_ONLY"}:
        return f"{text} · 예측 사용 불가"
    return f"{text} · 예측 사용 여부 확인 필요"


def _aware_timestamp_in_kst(value: object) -> tuple[str, str] | None:
    """Format only a retained timezone-aware timestamp in Korea time.

    A naive or invalid value is deliberately rejected: the Dashboard must not
    invent a timezone or substitute the current clock for missing provenance.
    The second tuple item preserves the exact original timestamp for details.
    """
    if value is None or pd.isna(value):
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    return (
        timestamp.tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M KST"),
        timestamp.isoformat(),
    )


def _last_retained_timestamp(
    frame: pd.DataFrame | None,
    columns: tuple[tuple[str, str], ...],
) -> tuple[str, str, str] | None:
    """Return label, KST text, and original value from an explicit field."""
    if frame is None or frame.empty:
        return None
    for column, label in columns:
        if column not in frame.columns:
            continue
        values = frame[column].dropna()
        if values.empty:
            continue
        converted = _aware_timestamp_in_kst(values.iloc[-1])
        if converted is not None:
            return label, converted[0], converted[1]
    return None


def _chart_reference_metadata(
    metric: DashboardMetricView | None,
    frame: pd.DataFrame | None = None,
    *,
    daily_session: bool,
    market_label: str,
) -> tuple[str, str]:
    """Build concise visible reference text plus exact provenance detail."""
    session_date = metric.as_of if metric and metric.as_of else "N/A"
    details = [
        f"dataset={metric.dataset_id if metric else 'N/A'}",
        f"source={metric.source if metric else 'N/A'}",
        f"route={metric.route if metric else 'N/A'}",
        f"freshness={metric.freshness if metric else 'UNKNOWN'}",
        f"source_timestamp={metric.source_timestamp if metric else 'N/A'}",
    ]
    if daily_session:
        visible = f"{market_label} 기준일 {session_date}"
        retained = _last_retained_timestamp(
            frame,
            (
                ("available_at", "가용 한국시간"),
                ("retrieved_at", "수집 한국시간"),
                ("captured_at_utc", "수집 한국시간"),
            ),
        )
        if retained is not None:
            label, kst_text, original = retained
            visible += f" · {label} {kst_text}"
            details.extend((f"{label}={kst_text}", f"original_timestamp={original}"))
        details.append(f"source_market_session_date={session_date}")
        return visible, "\n".join(details)

    retained = _last_retained_timestamp(frame, (("bar_end", "기준시각"),))
    if retained is None and metric is not None:
        converted = _aware_timestamp_in_kst(metric.source_timestamp)
        if converted is not None:
            retained = ("기준시각", converted[0], converted[1])
    if retained is None:
        return "기준시각 확인 필요", "\n".join(details)
    _label, kst_text, original = retained
    details.extend((f"기준시각={kst_text}", f"original_timestamp={original}"))
    return f"기준시각 {kst_text}", "\n".join(details)


def _data_status_area(row: HealthDatasetRow) -> str:
    """Assign a presentation-only area without changing registry semantics."""
    dataset = row.dataset.lower()
    if "account" in dataset:
        return "계좌"
    if "short_selling" in dataset or "lending" in dataset:
        return "공매도"
    if any(token in dataset for token in ("credit", "liquidity", "margin", "deposit")):
        return "신용·유동성"
    if any(token in dataset for token in (
        "treasury", "yield", "spread", "bond", "bok_", "dgs2", "dgs10", "dgs30",
    )):
        return "채권·금리"
    if any(token in dataset for token in (
        "option", "futures", "pcr", "wall", "basis", "vkospi", "cftc",
    )):
        return "파생상품"
    if dataset.startswith("kr_") or "kospi" in dataset or "kosdaq" in dataset:
        return "국내시장"
    if any(token in dataset for token in (
        "us_", "global_", "fred_", "yahoo_", "soxx", "nasdaq", "sp500", "vix",
    )):
        return "미국시장"
    return "기타"


def _data_status_display_name(dataset: str) -> str:
    """Create a compact human-readable label while retaining the ID in details."""
    phrases = (
        ("short_selling", "공매도"), ("market_investor", "시장 투자자"),
        ("foreign_ownership", "외국인 보유"), ("open_interest", "미결제약정"),
        ("treasury_spread", "국채 금리차"), ("treasury", "국채"),
        ("kospi200", "KOSPI200"), ("kospi", "KOSPI"), ("kosdaq", "KOSDAQ"),
        ("normalized", "정규화"), ("published", "게시"), ("derived", "계산"),
        ("observation", "관측"), ("investor", "투자자"), ("liquidity", "유동성"),
        ("futures", "선물"), ("future", "선물"), ("option", "옵션"),
        ("equity", "주식"), ("index", "지수"), ("price", "가격"),
        ("daily", "일별"), ("intraday", "장중"), ("snapshot", "스냅샷"),
        ("raw", "원천"), ("kr", "국내"), ("us", "미국"), ("global", "해외"),
    )
    label = dataset.lower()
    for source, target in phrases:
        label = label.replace(source, target)
    words = [word for word in label.replace("__", "_").split("_") if word]
    return " ".join(word.upper() if word in {"fred", "vix", "etf", "fx", "pcr", "oi", "cftc"} else word for word in words)


def _data_status_update_label(automation: str) -> str:
    if automation.endswith(" / ENABLED"):
        return "자동"
    if automation.startswith(("NO_REFRESH", "RESEARCH_ONLY")):
        return "갱신 없음"
    return "수동/비활성"


def _fmt(value: object, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "N/A"
    if isinstance(value, (int, np.integer)):
        return f"{value:,}"
    if isinstance(value, (float, np.floating)):
        return f"{value:,.{digits}f}"
    return str(value)


def _account_reference_kst(value: str | None) -> str:
    if not value:
        return "기준시각 N/A"
    aware = _aware_timestamp_in_kst(value)
    if aware is not None:
        return aware[0]
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return "기준시각 확인 필요"
    if "T" not in str(value):
        return f"{timestamp.date().isoformat()} KST 일자"
    return f"{value} · 시간대 미지정"


def _account_money(value: float | None, currency: str, *, hidden: bool) -> str:
    if value is None:
        return "N/A"
    if hidden:
        return MASKED_VALUE
    digits = 2 if currency == "USD" else 0
    return f"{_fmt(value, digits)} {currency}"


def _account_history_metric_label(metric: str) -> str:
    return {
        "TOTAL_ASSETS": "총자산",
        "OBSERVABLE_COMPONENT_SUM": "관찰 구성합(평가+현금매수가능)",
        "SECURITIES_VALUE": "보유평가금액(현금 제외)",
    }.get(metric, "계좌 규모")


def _fmt_krw_flow(value: object) -> str:
    """Format market-wide net purchases in familiar Korean large-number units."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not np.isfinite(number):
        return "N/A"
    sign = "+" if number > 0 else "" if number == 0 else "-"
    absolute = abs(number)
    if absolute >= 1_000_000_000_000:
        return f"{sign}{absolute / 1_000_000_000_000:.2f}조"
    if absolute >= 100_000_000:
        return f"{sign}{absolute / 100_000_000:,.0f}억"
    return f"{sign}{absolute:,.0f}원"


@dataclass(frozen=True)
class SessionAxisMapping:
    """A compact visual axis over retained observations, never filled sessions."""

    dates: tuple[pd.Timestamp, ...]
    missing_sessions: tuple[pd.Timestamp, ...] = ()
    unexpected_observations: tuple[pd.Timestamp, ...] = ()
    calendar_name: str | None = None

    @property
    def positions(self) -> np.ndarray:
        return np.arange(len(self.dates), dtype=float)


class SessionDateAxisItem(pg.AxisItem):
    """Render a dense observation axis while retaining each source calendar date."""

    def __init__(
        self,
        *args,
        minimum_label_spacing: float = 96.0,
        labels_visible: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._session_dates: tuple[pd.Timestamp, ...] = ()
        self._minimum_label_spacing = max(float(minimum_label_spacing), 1.0)
        self._labels_visible = bool(labels_visible)

    def set_session_dates(self, dates: tuple[pd.Timestamp, ...]) -> None:
        self._session_dates = dates
        self.update()

    def tickValues(self, minVal, maxVal, size):  # noqa: N802 - pyqtgraph API
        """Choose a width-aware, non-overlapping subset of session labels."""
        if not self._labels_visible or not self._session_dates or size <= 0:
            return []
        lower = max(0, int(np.ceil(float(minVal))))
        upper = min(len(self._session_dates) - 1, int(np.floor(float(maxVal))))
        if lower > upper:
            return []
        # Full ISO dates need a generous logical-width margin across DPR/font
        # variants. Linked followers deliberately emit no duplicate labels.
        max_labels = max(2, int(float(size) // self._minimum_label_spacing))
        span = upper - lower
        step = max(1, int(np.ceil(span / max(max_labels - 1, 1))))
        values = list(range(lower, upper + 1, step))
        if values[-1] != upper:
            values.append(upper)
        return [(float(step), [float(value) for value in values])]

    def tickStrings(self, values, scale, spacing):  # noqa: N802 - pyqtgraph API
        labels: list[str] = []
        for value in values:
            index = int(round(float(value)))
            if (
                0 <= index < len(self._session_dates)
                and abs(float(value) - index) < 0.25
            ):
                labels.append(self._session_dates[index].date().isoformat())
            else:
                labels.append("")
        return labels


class VolumeAxisItem(pg.AxisItem):
    """Render share volume with one readable unit for the visible Y range."""

    _UNITS = (
        (100_000_000.0, "억주"),
        (10_000.0, "만주"),
        (1.0, "주"),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._divisor = 1.0
        self._unit = "주"
        # A stable gutter prevents labels from colliding with bars as the
        # embedded view is resized or hosted in a larger window.
        self.enableAutoSIPrefix(False)
        self.setWidth(72)
        self.setLabel("거래량(주)")

    @staticmethod
    def _nice_step(span: float, intervals: int = 4) -> float:
        raw_step = max(float(span) / max(intervals, 1), np.finfo(float).eps)
        magnitude = 10.0 ** np.floor(np.log10(raw_step))
        fraction = raw_step / magnitude
        for candidate in (1.0, 2.0, 2.5, 5.0, 10.0):
            if fraction <= candidate:
                return candidate * magnitude
        return 10.0 * magnitude

    def _select_unit(self, min_value: float, max_value: float) -> None:
        visible_max = max(abs(float(min_value)), abs(float(max_value)))
        divisor, unit = next(
            (candidate_divisor, candidate_unit)
            for candidate_divisor, candidate_unit in self._UNITS
            if visible_max >= candidate_divisor or candidate_divisor == 1.0
        )
        if (divisor, unit) == (self._divisor, self._unit):
            return
        self._divisor = divisor
        self._unit = unit
        self.setLabel(f"거래량({unit})")

    def tickValues(self, minVal, maxVal, size):  # noqa: N802 - pyqtgraph API
        if not (np.isfinite(minVal) and np.isfinite(maxVal)) or minVal >= maxVal:
            return []
        self._select_unit(float(minVal), float(maxVal))
        step = self._nice_step(float(maxVal) - float(minVal))
        first = np.ceil(float(minVal) / step) * step
        values = np.arange(first, float(maxVal) + step * 0.01, step, dtype=float)
        # Rounding guards against floating point artifacts without changing
        # the underlying plot values.
        values = np.round(values / step, 10) * step
        return [(step, values[:5].tolist())]

    def tickStrings(self, values, scale, spacing):  # noqa: N802 - pyqtgraph API
        labels: list[str] = []
        for value in values:
            scaled = float(value) / self._divisor
            if abs(scaled) < 1e-12:
                scaled = 0.0
            if np.isclose(scaled, round(scaled), atol=1e-9):
                labels.append(f"{round(scaled):,}")
            else:
                labels.append(f"{scaled:,.2f}".rstrip("0").rstrip("."))
        return labels


def _format_exact_share_volume(value: object) -> str:
    """Format the retained, unscaled share count for hover information."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "거래량 N/A"
    if not number.is_finite():
        return "거래량 N/A"
    if number == number.to_integral_value():
        return f"거래량 {int(number):,}주"
    # Share-volume contracts are integral, but preserve a source decimal if a
    # local fixture or future validated contract supplies one.
    return f"거래량 {format(number, ',f').rstrip('0').rstrip('.')}주"


def _observation_axis_mapping(frame: pd.DataFrame) -> SessionAxisMapping:
    """Validate the actual retained observation order without inventing sessions."""
    if "date" not in frame:
        raise ValueError("retained chart observations have no date column")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("retained chart observations contain an invalid date")
    dates = dates.dt.normalize().reset_index(drop=True)
    if dates.duplicated().any():
        raise ValueError("retained chart observations contain duplicate sessions")
    if not dates.is_monotonic_increasing:
        raise ValueError("retained chart observations are not in ascending session order")
    return SessionAxisMapping(tuple(dates))


def _daily_session_axis_mapping(
    frame: pd.DataFrame, market: ExchangeMarket,
) -> SessionAxisMapping:
    """Use a trading-session axis and disclose, rather than hide, source gaps."""
    observed = _observation_axis_mapping(frame)
    if not observed.dates:
        return observed
    calendar = ExchangeTradingCalendar(market)
    expected = tuple(
        pd.Timestamp(value)
        for value in calendar.sessions_in_range(
            observed.dates[0].date(), observed.dates[-1].date(),
        )
    )
    observed_dates = set(observed.dates)
    expected_dates = set(expected)
    unexpected = tuple(value for value in observed.dates if value not in expected_dates)
    missing = tuple(value for value in expected if value not in observed_dates)
    return SessionAxisMapping(
        dates=observed.dates,
        missing_sessions=missing,
        unexpected_observations=unexpected,
        calendar_name=calendar.provenance.calendar_name,
    )


def _downsample_market_frame(
    frame: pd.DataFrame,
    max_points: int = DASHBOARD_MARKET_RENDER_POINT_BUDGET,
) -> pd.DataFrame:
    """Bound Dashboard rendering while preserving endpoints and global extrema.

    This is a display-only row selection. It never interpolates, aggregates,
    fills, or changes a retained value. The full retained coverage stays in the
    frame metadata supplied by the service.
    """
    if max_points < 2:
        raise ValueError("market render point budget must be at least two")
    if len(frame) <= max_points:
        return frame.copy()
    required = {0, len(frame) - 1}
    for column in frame.columns:
        if column == "date":
            continue
        numeric = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
        finite_positions = np.flatnonzero(np.isfinite(numeric))
        if not len(finite_positions):
            continue
        finite_values = numeric[finite_positions]
        required.add(int(finite_positions[int(np.argmin(finite_values))]))
        required.add(int(finite_positions[int(np.argmax(finite_values))]))
    if len(required) > max_points:
        # Extremely wide future schemas remain deterministic and bounded;
        # price/volume fields receive priority over optional overlays.
        priority = ("open", "high", "low", "close", "volume")
        required = {0, len(frame) - 1}
        for column in priority:
            if column not in frame:
                continue
            numeric = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
            finite_positions = np.flatnonzero(np.isfinite(numeric))
            if len(finite_positions):
                finite_values = numeric[finite_positions]
                required.update((
                    int(finite_positions[int(np.argmin(finite_values))]),
                    int(finite_positions[int(np.argmax(finite_values))]),
                ))
        interior = [position for position in sorted(required) if position not in {0, len(frame) - 1}]
        required = {0, len(frame) - 1, *interior[:max(0, max_points - 2)]}
    uniform = np.linspace(0, len(frame) - 1, max_points, dtype=int)
    selected = set(required)
    for position in uniform:
        if len(selected) >= max_points:
            break
        selected.add(int(position))
    if len(selected) < max_points:
        for position in range(len(frame)):
            if len(selected) >= max_points:
                break
            selected.add(position)
    result = frame.iloc[sorted(selected)].reset_index(drop=True)
    result.attrs = dict(frame.attrs)
    return result


def _downsampled_session_mapping(
    frame: pd.DataFrame,
    original: SessionAxisMapping,
) -> SessionAxisMapping:
    """Retain genuine calendar gaps but ignore intentional render thinning."""
    dates = tuple(pd.to_datetime(frame["date"], errors="raise").dt.normalize())
    selected = set(dates)
    return SessionAxisMapping(
        dates=dates,
        missing_sessions=original.missing_sessions,
        unexpected_observations=tuple(
            value for value in original.unexpected_observations if value in selected
        ),
        calendar_name=original.calendar_name,
    )


def _dashboard_chart_coverage_text(frame: pd.DataFrame) -> str:
    coverage = frame.attrs.get(DASHBOARD_CHART_COVERAGE_ATTR)
    if not isinstance(coverage, DashboardChartCoverage):
        return ""
    span = (
        f"{coverage.available_start}~{coverage.available_end}"
        if coverage.available_start and coverage.available_end else "N/A"
    )
    if coverage.period == "MAX":
        return f"전체 보유 {span} / {coverage.available_sessions:,}거래일"
    if coverage.complete:
        return ""
    return (
        f"보유 구간 일부 · 요청 {coverage.period}/{coverage.requested_sessions:,}거래일"
        f" · 보유 {span}/{coverage.available_sessions:,}거래일"
    )


def _session_axis_warning(mapping: SessionAxisMapping | None) -> str:
    """Expose genuine source gaps while known calendar closures remain gap-free."""
    if mapping is None:
        return ""
    fragments: list[str] = []
    if mapping.missing_sessions:
        dates = ", ".join(value.date().isoformat() for value in mapping.missing_sessions[:3])
        suffix = " …" if len(mapping.missing_sessions) > 3 else ""
        fragments.append(
            f"source missing {mapping.calendar_name or 'market'} sessions: {dates}{suffix}"
        )
    return " · ".join(fragments)


INDEX_CURVE_STYLES = {
    "close": ("종가", "#dce7ff", 2.0),
    "ma5": ("MA5", "#53d8fb", 1.35),
    "ma20": ("MA20", "#f6c85f", 1.35),
    "ma60": ("MA60", "#ed6a5a", 1.35),
    "ma120": ("MA120", "#9b8afb", 1.35),
    "rsi14": ("RSI14", "#63d297", 1.7),
    "disparity60": ("60일 괴리율", "#ff8fab", 1.7),
}


class IndicatorControlPanel(QtWidgets.QFrame):
    """Compact keyboard-accessible presentation controls for existing chart series."""

    settings_changed = QtCore.Signal(object)
    reset_requested = QtCore.Signal()

    def __init__(self, *, allows_lower_panels: bool, parent=None) -> None:
        super().__init__(parent)
        self._allows_lower_panels = allows_lower_panels
        self.setObjectName("indicatorControlPanel")
        self.setAccessibleName("차트 지표 표시 설정")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(4)
        title = QtWidgets.QLabel("지표")
        title.setObjectName("chartStatus")
        layout.addWidget(title)
        self.ma = {}
        for key, label in (("ma5", "MA5"), ("ma20", "MA20"), ("ma60", "MA60"), ("ma120", "MA120")):
            control = QtWidgets.QCheckBox(label)
            control.setAccessibleName(f"가격 오버레이 {label} 표시")
            control.toggled.connect(self._emit_settings)
            self.ma[key] = control
            layout.addWidget(control)
        self.volume = QtWidgets.QCheckBox("거래량")
        self.volume.setAccessibleName("하단 패널 거래량 표시")
        self.volume.toggled.connect(self._emit_settings)
        layout.addWidget(self.volume)
        self.extra_upper = {}
        for key, label in (("ema20", "EMA20"), ("bollinger_bands", "BB(20,2)")):
            control = QtWidgets.QCheckBox(label)
            control.setAccessibleName(f"price overlay {label}")
            control.toggled.connect(self._emit_settings)
            self.extra_upper[key] = control
            layout.addWidget(control)
        self.rsi = self._mode_combo("RSI14")
        self.disparity = self._mode_combo("60일 괴리율")
        layout.addWidget(self.rsi)
        layout.addWidget(self.disparity)
        self.extra_lower = {}
        if allows_lower_panels:
            for key, label in (("atr14_mode", "ATR14"), ("adx14_mode", "ADX14"), ("obv_mode", "OBV"), ("bollinger_bandwidth_mode", "BB width")):
                combo = self._lower_mode_combo(label)
                combo.currentIndexChanged.connect(
                    lambda _index, current=key: self._exclusive_lower_panel(current)
                )
                self.extra_lower[key] = combo
                layout.addWidget(combo)
        self.reset_button = QtWidgets.QPushButton("기본값")
        self.reset_button.setAccessibleName("현재 차트 지표 표시 기본값으로 재설정")
        self.reset_button.clicked.connect(self.reset_requested)
        layout.addWidget(self.reset_button)
        self.setToolTip("가격 오버레이와 하단 패널의 표시만 바꿉니다. 계산, 가격, 거래량, 데이터 저장은 변경하지 않습니다.")

    def _mode_combo(self, label: str) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.setMaximumWidth(112)
        combo.addItem(f"{label}: 끔", "Off")
        combo.addItem(f"{label}: 가격 위", "Overlay")
        if self._allows_lower_panels:
            combo.addItem(f"{label}: 하단 패널", "Panel")
        combo.setAccessibleName(f"{label} 표시 위치")
        combo.currentIndexChanged.connect(self._emit_settings)
        return combo

    def _lower_mode_combo(self, label: str) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        combo.setMaximumWidth(82)
        combo.addItem(f"{label}: off", "Off")
        combo.addItem(f"{label}: panel", "Panel")
        combo.setAccessibleName(f"{label} lower panel display")
        combo.currentIndexChanged.connect(self._emit_settings)
        return combo

    def _exclusive_lower_panel(self, selected: str) -> None:
        """A lower plot has one Y unit; retain existing RSI/disparity behavior."""
        if self.extra_lower[selected].currentData() != "Panel":
            return
        blockers = []
        try:
            for key, combo in self.extra_lower.items():
                if key != selected:
                    blockers.append(QtCore.QSignalBlocker(combo))
                    combo.setCurrentIndex(0)
        finally:
            del blockers

    def _normalize_lower_panels(self, selected: QtWidgets.QComboBox | None = None) -> None:
        """One shared lower ViewBox cannot truthfully carry mixed units."""
        controls = (self.rsi, self.disparity, *self.extra_lower.values())
        active = [control for control in controls if control.currentData() == "Panel"]
        keeper = selected if selected in active else (active[0] if active else None)
        if keeper is None:
            return
        blockers = []
        try:
            for control in active:
                if control is not keeper:
                    blockers.append(QtCore.QSignalBlocker(control))
                    control.setCurrentIndex(max(0, control.findData("Off")))
        finally:
            del blockers

    def settings(self) -> ChartIndicatorPreferences:
        return ChartIndicatorPreferences(
            self.ma["ma5"].isChecked(), self.ma["ma20"].isChecked(),
            self.ma["ma60"].isChecked(), self.ma["ma120"].isChecked(),
            self.volume.isChecked(), str(self.rsi.currentData()), str(self.disparity.currentData()),
            self.extra_upper["ema20"].isChecked(), self.extra_upper["bollinger_bands"].isChecked(),
            *(str(self.extra_lower[key].currentData()) if key in self.extra_lower else "Off" for key in ("atr14_mode", "adx14_mode", "obv_mode", "bollinger_bandwidth_mode")),
        )

    def apply(self, settings: ChartIndicatorPreferences) -> None:
        blockers = [QtCore.QSignalBlocker(widget) for widget in (
            *self.ma.values(), self.volume, *self.extra_upper.values(), self.rsi, self.disparity, *self.extra_lower.values(),
        )]
        for key in self.ma:
            self.ma[key].setChecked(getattr(settings, key))
        self.volume.setChecked(settings.volume)
        for key, control in self.extra_upper.items():
            control.setChecked(getattr(settings, key))
        self.rsi.setCurrentIndex(max(0, self.rsi.findData(settings.rsi14_mode)))
        self.disparity.setCurrentIndex(max(0, self.disparity.findData(settings.disparity60_mode)))
        for key, combo in self.extra_lower.items():
            combo.setCurrentIndex(max(0, combo.findData(getattr(settings, key))))
        del blockers
        self._normalize_lower_panels()

    def _emit_settings(self, *_args) -> None:
        sender = self.sender()
        selected = sender if isinstance(sender, QtWidgets.QComboBox) else None
        if selected is not None and selected.currentData() == "Panel":
            self._normalize_lower_panels(selected)
        self.settings_changed.emit(self.settings())


def _continuous_connection_mask(
    x_values: object,
    y_values: object,
    mapping: SessionAxisMapping | None = None,
) -> np.ndarray:
    """Connect only adjacent finite observations with no accepted-session gap."""
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or len(x) != len(y):
        raise ValueError("continuous curve x/y must be equal one-dimensional arrays")
    connect = np.zeros(len(x), dtype=np.int32)
    if len(x) < 2:
        return connect
    finite_pairs = (
        np.isfinite(x[:-1]) & np.isfinite(x[1:])
        & np.isfinite(y[:-1]) & np.isfinite(y[1:])
    )
    consecutive = np.ones(len(x) - 1, dtype=bool)
    if mapping is not None:
        if len(mapping.dates) != len(x):
            raise ValueError("session mapping length differs from curve")
        missing = tuple(pd.Timestamp(value) for value in mapping.missing_sessions)
        unexpected = set(pd.Timestamp(value) for value in mapping.unexpected_observations)
        for index, (left, right) in enumerate(zip(mapping.dates[:-1], mapping.dates[1:])):
            left_stamp, right_stamp = pd.Timestamp(left), pd.Timestamp(right)
            if left_stamp in unexpected or right_stamp in unexpected:
                consecutive[index] = False
                continue
            consecutive[index] = not any(left_stamp < value < right_stamp for value in missing)
    connect[:-1] = (finite_pairs & consecutive).astype(np.int32)
    return connect


def _plot_continuous_line(
    target: pg.PlotWidget | pg.ViewBox,
    x_values: object,
    y_values: object,
    *,
    color: str,
    width: float,
    name: str,
    mapping: SessionAxisMapping | None = None,
) -> pg.PlotDataItem:
    """Add an antialiased cosmetic curve without transforming source values."""
    x = np.asarray(x_values, dtype=float)
    y = pd.to_numeric(pd.Series(y_values), errors="coerce").to_numpy(dtype=float)
    connect = _continuous_connection_mask(x, y, mapping)
    pen = pg.mkPen(QtGui.QColor(color), width=width)
    pen.setCosmetic(True)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    item = pg.PlotDataItem(
        x=x,
        y=y,
        pen=pen,
        name=name,
        connect=connect,
        antialias=True,
        autoDownsample=False,
        downsample=1,
        clipToView=False,
        dynamicRangeLimit=None,
        skipFiniteCheck=False,
    )
    target.addItem(item)
    return item


class CandlestickItem(pg.GraphicsObject):
    """Small read-only OHLC renderer for retained daily/weekly/monthly bars."""

    def __init__(self, bars: list[tuple[float, float, float, float, float]]):
        super().__init__()
        self._bars = bars
        self._picture = QtGui.QPicture()
        self._generate_picture()

    def _generate_picture(self) -> None:
        self._picture = QtGui.QPicture()
        if not self._bars:
            return
        painter = QtGui.QPainter(self._picture)
        x_values = np.asarray([bar[0] for bar in self._bars], dtype=float)
        gaps = np.diff(x_values)
        positive_gaps = gaps[gaps > 0]
        width = float(np.median(positive_gaps) * 0.68) if positive_gaps.size else 48_000.0
        half_width = width / 2.0
        for x_value, open_value, high_value, low_value, close_value in self._bars:
            rising = close_value >= open_value
            color = QtGui.QColor("#e04f5f" if rising else "#3177d6")
            painter.setPen(pg.mkPen(color, width=1.1))
            painter.drawLine(QtCore.QPointF(x_value, low_value), QtCore.QPointF(x_value, high_value))
            painter.setBrush(pg.mkBrush(color))
            body_low = min(open_value, close_value)
            body_height = max(abs(close_value - open_value), 0.000001)
            painter.drawRect(QtCore.QRectF(x_value - half_width, body_low, width, body_height))
        painter.end()

    def paint(self, painter, option, widget=None) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self._picture.boundingRect())


def _aggregate_ohlc(
    frame: pd.DataFrame,
    interval: str,
    *,
    reference_date: object | None = None,
    market: ExchangeMarket = ExchangeMarket.KR,
) -> pd.DataFrame:
    """Aggregate observed OHLC rows without inventing sessions or volume.

    A period is complete only when the documented exchange calendar says its
    final session has passed the supplied reference date *and* every expected
    session in that period was retained. This deliberately leaves a period
    with a genuine source gap marked incomplete instead of silently treating a
    shorter observed sequence as a complete bar.
    """
    required = ["date", "open", "high", "low", "close"]
    if frame.empty or any(column not in frame for column in required):
        return pd.DataFrame(columns=required)
    # Preserve daily derived columns. They belong to the existing local daily
    # frame and must reappear unchanged when a user switches back from an
    # aggregate view; aggregate views intentionally set their unsupported
    # daily-only indicators to missing at the caller.
    columns = list(frame.columns)
    result = frame.loc[:, columns].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in ("open", "high", "low", "close"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if "volume" in result:
        volume = pd.to_numeric(result["volume"], errors="coerce")
        # A volume contribution is valid only when it is finite and
        # non-negative. ``sum(min_count=1)`` below keeps an all-missing period
        # missing rather than manufacturing a zero.
        result["volume"] = volume.where(np.isfinite(volume) & volume.ge(0))
    result = result.dropna(subset=required).sort_values("date")
    if interval == "일봉" or result.empty:
        result = result.reset_index(drop=True)
        result["incomplete_period"] = False
        return result
    period = "W-FRI" if interval == "주봉" else "M"
    result["period"] = result["date"].dt.to_period(period)
    aggregations = dict(
        date=("date", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    )
    if "volume" in result:
        aggregations["volume"] = ("volume", lambda values: values.sum(min_count=1))
    aggregated = result.groupby("period", sort=True, observed=True).agg(**aggregations)
    output = aggregated.reset_index()
    reference = pd.Timestamp(
        reference_date if reference_date is not None else result["date"].iloc[-1]
    ).normalize()
    calendar = ExchangeTradingCalendar(market)
    observed_dates = frozenset(pd.Timestamp(value).date() for value in result["date"])

    def _is_incomplete(value: pd.Period) -> bool:
        period_start = value.start_time.normalize().date()
        period_end = value.end_time.normalize().date()
        expected = calendar.sessions_in_range(period_start, period_end)
        if not expected:
            return True
        final_session = expected[-1]
        if reference.date() < final_session:
            return True
        return any(session not in observed_dates for session in expected)

    output["incomplete_period"] = output["period"].map(_is_incomplete)
    return output.drop(columns="period")


class MetricCard(QtWidgets.QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        box = QtWidgets.QVBoxLayout(self)
        self.title = QtWidgets.QLabel(title)
        self.title.setObjectName("cardTitle")
        self.setAccessibleName(title)
        self.body = QtWidgets.QLabel("Loading…")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        box.addWidget(self.title)
        box.addWidget(self.body)

    def set_lines(self, lines: list[str], tooltip: str = "") -> None:
        self.body.setText("\n".join(lines))
        self.setToolTip(tooltip)


def _flow_direction_text(value: int | None) -> tuple[str, str]:
    if value is None:
        return "표시 제한", "unavailable"
    if value > 0:
        return f"순매수 {_fmt_krw_flow(value)}", "positive"
    if value < 0:
        return f"순매도 {_fmt_krw_flow(value)}", "negative"
    return "중립 0원", "neutral"


class SignedFlowBar(QtWidgets.QWidget):
    """Zero-centred signed bar for comparing investor flow at a glance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value: int | None = None
        self._scale = 1.0
        self.setFixedHeight(7)
        self.setMinimumWidth(56)
        self.setAccessibleName("순매수·순매도 크기")

    def set_value(self, value: int | None, scale: float) -> None:
        self._value = value
        self._scale = max(float(scale), 1.0)
        if value is None:
            description = "표시 제한"
        else:
            direction = "순매수" if value > 0 else "순매도" if value < 0 else "중립"
            description = f"{direction} {value:+,}원"
        self.setAccessibleDescription(description)
        self.setToolTip(description)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        bounds = self.rect().adjusted(1, 1, -1, -1)
        center = bounds.center().x()
        painter.setPen(QtGui.QPen(QtGui.QColor("#cbd3dc"), 1.0))
        painter.drawLine(center, bounds.top(), center, bounds.bottom())
        if self._value is None or self._value == 0:
            return
        half_width = max((bounds.width() - 2) / 2.0, 1.0)
        magnitude = min(abs(float(self._value)) / self._scale, 1.0) * half_width
        color = QtGui.QColor("#d95763" if self._value > 0 else "#3f78c5")
        if self._value > 0:
            bar = QtCore.QRectF(center + 1, bounds.top(), magnitude, bounds.height())
        else:
            bar = QtCore.QRectF(center - magnitude, bounds.top(), magnitude, bounds.height())
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(bar, 2.0, 2.0)


class MarketFlowMarketPage(QtWidgets.QWidget):
    """Compact, market-scoped latest/WTD table with explicit signed copy."""

    def __init__(self, market: str, parent=None):
        super().__init__(parent)
        self.market = market
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(4, 3, 4, 2)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(1)
        for column, text in enumerate(("투자자", "최근", "이번 주 누계")):
            label = QtWidgets.QLabel(text)
            label.setObjectName("flowHeader")
            if column:
                label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            layout.addWidget(label, 0, column)
        self.latest_labels: dict[str, QtWidgets.QLabel] = {}
        self.weekly_labels: dict[str, QtWidgets.QLabel] = {}
        self.latest_bars: dict[str, SignedFlowBar] = {}
        self.weekly_bars: dict[str, SignedFlowBar] = {}
        for row, (investor_id, text) in enumerate((
            ("FOREIGN", "외국인"),
            ("INSTITUTION", "기관"),
            ("INDIVIDUAL", "개인"),
        ), start=1):
            name = QtWidgets.QLabel(text)
            name.setObjectName("flowInvestor")
            latest = QtWidgets.QLabel("표시 제한")
            weekly = QtWidgets.QLabel("표시 제한")
            for label in (latest, weekly):
                label.setObjectName("flowValue")
                label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.latest_labels[investor_id] = latest
            self.weekly_labels[investor_id] = weekly
            latest_cell = QtWidgets.QWidget()
            latest_layout = QtWidgets.QVBoxLayout(latest_cell)
            latest_layout.setContentsMargins(0, 0, 0, 0)
            latest_layout.setSpacing(0)
            latest_bar = SignedFlowBar()
            latest_layout.addWidget(latest)
            latest_layout.addWidget(latest_bar)
            weekly_cell = QtWidgets.QWidget()
            weekly_layout = QtWidgets.QVBoxLayout(weekly_cell)
            weekly_layout.setContentsMargins(0, 0, 0, 0)
            weekly_layout.setSpacing(0)
            weekly_bar = SignedFlowBar()
            weekly_layout.addWidget(weekly)
            weekly_layout.addWidget(weekly_bar)
            self.latest_bars[investor_id] = latest_bar
            self.weekly_bars[investor_id] = weekly_bar
            layout.addWidget(name, row, 0)
            layout.addWidget(latest_cell, row, 1)
            layout.addWidget(weekly_cell, row, 2)
        self.detail = QtWidgets.QLabel("기준일 N/A · 표시 제한")
        self.detail.setObjectName("compactMeta")
        self.detail.setWordWrap(False)
        layout.addWidget(self.detail, 4, 0, 1, 3)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(2, 2)

    @staticmethod
    def _apply_value(label: QtWidgets.QLabel, value: int | None) -> None:
        text, tone = _flow_direction_text(value)
        label.setText(text)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def set_view(self, view: MarketInvestorFlowView | None) -> None:
        if view is None or view.market != self.market or not view.displays_values:
            for label in (*self.latest_labels.values(), *self.weekly_labels.values()):
                self._apply_value(label, None)
            for bar in (*self.latest_bars.values(), *self.weekly_bars.values()):
                bar.set_value(None, 1.0)
            reason = (
                view.unavailable_reason
                if isinstance(view, MarketInvestorFlowView)
                else "검증된 로컬 시장 수급 view가 없습니다."
            )
            self.detail.setText("기준일 N/A · 표시 제한")
            self.setToolTip(reason or "표시 제한")
            return

        latest_scale = max(
            (abs(value.latest_value) for value in view.values
             if value.latest_value is not None),
            default=1,
        )
        weekly_scale = max(
            (abs(value.week_to_date_value) for value in view.values
             if value.week_to_date_value is not None),
            default=1,
        )
        for value in view.values:
            self._apply_value(self.latest_labels[value.investor_id], value.latest_value)
            self.latest_bars[value.investor_id].set_value(
                value.latest_value, latest_scale,
            )
            weekly_value = (
                value.week_to_date_value if view.weekly_complete_through_as_of else None
            )
            self._apply_value(
                self.weekly_labels[value.investor_id],
                weekly_value,
            )
            self.weekly_bars[value.investor_id].set_value(
                weekly_value, weekly_scale,
            )
        if view.missing_sessions:
            missing = ", ".join(view.missing_sessions)
            detail = f"{view.as_of} 기준 · 주간 누계 제한 · 누락 {missing}"
        else:
            detail = f"{view.as_of} 장마감"
        self.detail.setText(detail)
        exact_values = "\n".join(
            f"{value.label}: latest={value.latest_value:+,} KRW; "
            f"week_to_date={value.week_to_date_value:+,} KRW"
            if value.week_to_date_value is not None
            else f"{value.label}: latest={value.latest_value:+,} KRW; week_to_date=SUPPRESSED"
            for value in view.values
            if value.latest_value is not None
        )
        self.setToolTip(
            f"dataset={view.dataset_id}\nmarket={view.market}\nunit={view.value_unit}\n"
            f"source={view.source}\nsource_operation={view.source_operation}\n"
            f"provider_segment={view.provider_segment}\nas_of={view.as_of}\n"
            f"expected={view.expected_as_of}\nfreshness={view.freshness}\n"
            f"finality={view.finality}\ncovered_sessions={','.join(view.covered_sessions)}\n"
            f"required_sessions={','.join(view.required_sessions)}\n"
            f"weekly_limitation={view.weekly_unavailable_reason or 'none'}\n{exact_values}"
        )


class MarketFundingPage(QtWidgets.QWidget):
    """One compact tab for retained credit and market-liquidity aggregates."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(5, 4, 5, 3)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(2)
        self.value_labels: dict[str, QtWidgets.QLabel] = {}
        definitions = (
            ("CREDIT_FINANCING", "신용융자 잔고"),
            ("INVESTOR_DEPOSITS", "투자자 예탁금"),
            ("RECEIVABLES", "위탁매매 미수금"),
            ("FORCED_SALE", "반대매매 금액"),
        )
        for index, (value_id, label) in enumerate(definitions):
            row, column = divmod(index, 2)
            cell = QtWidgets.QWidget()
            cell_layout = QtWidgets.QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(0)
            name = QtWidgets.QLabel(label)
            name.setObjectName("flowInvestor")
            value = QtWidgets.QLabel("보존값 없음")
            value.setObjectName("flowValue")
            value.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            self.value_labels[value_id] = value
            cell_layout.addWidget(name)
            cell_layout.addWidget(value)
            layout.addWidget(cell, row, column)
        self.detail = QtWidgets.QLabel("각 항목의 기준일·출처를 따로 표시합니다.")
        self.detail.setObjectName("compactMeta")
        layout.addWidget(self.detail, 2, 0, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    @staticmethod
    def _format_value(value: object, unit: str) -> str:
        if value is None:
            return "보존값 없음"
        number = float(value)
        if unit == "KRW":
            if abs(number) >= 1_000_000_000_000:
                return f"{number / 1_000_000_000_000:,.2f}조원"
            if abs(number) >= 100_000_000:
                return f"{number / 100_000_000:,.0f}억원"
            return f"{number:,.0f}원"
        return f"{number:,.0f} · 공급자 원단위"

    def set_view(self, view: MarketFundingView | None) -> None:
        values = {item.value_id: item for item in view.values} if view else {}
        details = ["신용·시장자금 로컬 보존값 (서로 대체·합산하지 않음)"]
        dates: list[str] = []
        for value_id, label in self.value_labels.items():
            item = values.get(value_id)
            if item is None:
                label.setText("보존값 없음")
                continue
            label.setText(self._format_value(item.value, item.unit))
            label.setProperty("tone", "neutral" if item.value is not None else "unavailable")
            label.style().unpolish(label)
            label.style().polish(label)
            if item.as_of:
                dates.append(item.as_of)
            details.append(
                f"{item.label}: value={item.value if item.value is not None else 'N/A'}; "
                f"unit={item.unit}; as_of={item.as_of or 'N/A'}; "
                f"freshness={item.freshness}; source={item.source}; "
                f"reason={item.unavailable_reason or 'none'}"
            )
        unique_dates = sorted(set(dates))
        self.detail.setText(
            "기준일 " + (" / ".join(unique_dates) if unique_dates else "N/A")
            + " · 최신성은 상세 확인"
        )
        self.setToolTip("\n".join(details))


class MarketInvestorFlowPanel(QtWidgets.QFrame):
    """Dedicated right-side KOSPI/KOSDAQ market-flow surface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setAccessibleName("KOSPI KOSDAQ 시장 수급")
        self.setFixedHeight(178)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 5)
        layout.setSpacing(2)
        title = QtWidgets.QLabel("시장 수급")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setObjectName("marketFlowTabs")
        self.tabs.setAccessibleName("시장별 수급")
        self.pages = {
            market: MarketFlowMarketPage(market)
            for market in ("KOSPI", "KOSDAQ")
        }
        for market, page in self.pages.items():
            self.tabs.addTab(page, market)
        self.funding_page = MarketFundingPage()
        self.tabs.addTab(self.funding_page, "신용·자금")
        layout.addWidget(self.tabs)
        self.us_scope_note = QtWidgets.QLabel(
            "미국 수급 미지원 · CFTC는 별도 주간 포지션"
        )
        self.us_scope_note.setObjectName("compactMeta")
        self.us_scope_note.setToolTip(
            "미국 CFTC Dealer·Asset Manager·Leveraged Funds 포지션은 "
            "국내 외국인·기관·개인 일별 순매수와 의미·주기·단위가 달라 "
            "현재 시장 수급 표에 대체 표시하지 않습니다."
        )
        layout.addWidget(self.us_scope_note)

    def set_views(
        self,
        views: dict[str, MarketInvestorFlowView],
        funding: MarketFundingView | None = None,
    ) -> None:
        for market, page in self.pages.items():
            page.set_view(views.get(market))
        self.funding_page.set_view(funding)


class ValuationPercentileChart(QtWidgets.QWidget):
    """Neutral 5Y/10Y historical-percentile lanes for KRX PER and PBR."""

    def __init__(self, market: str, parent=None):
        super().__init__(parent)
        self.market = market
        self._values: dict[str, tuple[float | None, float | None]] = {
            "PER": (None, None), "PBR": (None, None),
        }
        self.setFixedHeight(34)
        self.setMinimumWidth(180)
        self.setAccessibleName(f"{market} PER PBR 역사 백분위")

    @staticmethod
    def _valid_percentile(value: object) -> float | None:
        number = pd.to_numeric(value, errors="coerce")
        if pd.isna(number) or not np.isfinite(number):
            return None
        return min(max(float(number), 0.0), 100.0)

    def set_values(
        self,
        per_5y: object,
        per_10y: object,
        pbr_5y: object,
        pbr_10y: object,
    ) -> None:
        self._values = {
            "PER": (
                self._valid_percentile(per_5y),
                self._valid_percentile(per_10y),
            ),
            "PBR": (
                self._valid_percentile(pbr_5y),
                self._valid_percentile(pbr_10y),
            ),
        }
        descriptions = []
        for name, (value_5y, value_10y) in self._values.items():
            descriptions.append(
                f"{name} 5년 {value_5y:.0f}백분위 · 10년 {value_10y:.0f}백분위"
                if value_5y is not None and value_10y is not None
                else f"{name} 표시 제한"
            )
        self.setAccessibleDescription(" · ".join(descriptions))
        self.setToolTip("동일 KRX 비율의 5년/10년 역사 백분위 · " + " · ".join(descriptions))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        label_width = 26
        right_width = 62
        track_left = label_width + 2
        track_right = max(track_left + 10, self.width() - right_width)
        track_width = track_right - track_left
        font = painter.font()
        font.setPixelSize(9)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#596675"))
        colors = (
            QtGui.QColor("#edf2f6"), QtGui.QColor("#e7edf3"),
            QtGui.QColor("#e0e8ef"), QtGui.QColor("#d9e3ec"),
            QtGui.QColor("#d2dee9"),
        )
        for lane, (name, values) in enumerate(self._values.items()):
            top = 2 + lane * 16
            painter.drawText(0, top, label_width, 12, QtCore.Qt.AlignVCenter, name)
            segment_width = track_width / 5.0
            for index, color in enumerate(colors):
                painter.fillRect(
                    QtCore.QRectF(track_left + index * segment_width, top + 3,
                                  segment_width + 0.5, 6),
                    color,
                )
            painter.setPen(QtGui.QPen(QtGui.QColor("#c2cad3"), 0.8))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(
                QtCore.QRectF(track_left, top + 3, track_width, 6), 2.0, 2.0,
            )
            if any(value is None for value in values):
                painter.setPen(QtGui.QColor("#8793a1"))
                painter.drawText(track_right + 4, top, right_width - 4, 12,
                                 QtCore.Qt.AlignVCenter, "N/A")
                continue
            value_5y, value_10y = values
            marker_5y_x = track_left + track_width * value_5y / 100.0
            marker_10y_x = track_left + track_width * value_10y / 100.0
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#19706b"))
            painter.drawEllipse(QtCore.QPointF(marker_5y_x, top + 5), 3.0, 3.0)
            painter.setBrush(QtGui.QColor("#27384a"))
            diamond = QtGui.QPolygonF([
                QtCore.QPointF(marker_10y_x, top + 5),
                QtCore.QPointF(marker_10y_x + 3.2, top + 8),
                QtCore.QPointF(marker_10y_x, top + 11),
                QtCore.QPointF(marker_10y_x - 3.2, top + 8),
            ])
            painter.drawPolygon(diamond)
            painter.setPen(QtGui.QColor("#27384a"))
            painter.drawText(track_right + 4, top, right_width - 4, 12,
                             QtCore.Qt.AlignVCenter,
                             f"{value_5y:.0f}/{value_10y:.0f}%")


class MiniSparkline(QtWidgets.QWidget):
    """Small non-interactive trend line; its values are already metric-gated."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = np.array([], dtype=float)
        self._color = QtGui.QColor("#2f6fb2")
        self._reference_value: float | None = None
        self.setFixedHeight(28)
        self.setMinimumWidth(54)
        self.setAccessibleName("최근 추이")

    def set_values(
        self, values: object, color: str = "#2f6fb2",
        *, reference_value: float | None = None,
    ) -> None:
        series = pd.to_numeric(pd.Series(values, dtype=object), errors="coerce")
        self._values = series.to_numpy(dtype=float)
        self._color = QtGui.QColor(color)
        reference = pd.to_numeric(reference_value, errors="coerce")
        self._reference_value = (
            float(reference) if pd.notna(reference) and np.isfinite(reference) else None
        )
        self.setVisible(bool(np.isfinite(self._values).sum() >= 2))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        finite = self._values[np.isfinite(self._values)]
        if len(finite) < 2:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(self._color, 1.7))
        bounds = self.rect().adjusted(3, 4, -3, -4)
        scale_values = finite
        if self._reference_value is not None:
            scale_values = np.append(scale_values, self._reference_value)
        low, high = float(np.min(scale_values)), float(np.max(scale_values))
        span = high - low or 1.0
        step = bounds.width() / max(len(self._values) - 1, 1)
        path = QtGui.QPainterPath()
        plotted: list[QtCore.QPointF] = []
        connected = False
        for index, value in enumerate(self._values):
            if not np.isfinite(value):
                connected = False
                continue
            x = bounds.left() + index * step
            y = bounds.bottom() - (float(value) - low) / span * bounds.height()
            plotted.append(QtCore.QPointF(x, y))
            if not connected:
                path.moveTo(x, y)
                connected = True
            else:
                path.lineTo(x, y)
        if self._reference_value is not None:
            reference_y = bounds.bottom() - (
                self._reference_value - low
            ) / span * bounds.height()
            baseline_pen = QtGui.QPen(QtGui.QColor("#c8d0d9"), 1.0)
            baseline_pen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(baseline_pen)
            painter.drawLine(
                QtCore.QPointF(bounds.left(), reference_y),
                QtCore.QPointF(bounds.right(), reference_y),
            )
            if len(plotted) >= 2:
                fill = QtGui.QPainterPath()
                fill.moveTo(plotted[0].x(), reference_y)
                for point in plotted:
                    fill.lineTo(point)
                fill.lineTo(plotted[-1].x(), reference_y)
                fill.closeSubpath()
                fill_color = QtGui.QColor(self._color)
                fill_color.setAlpha(28)
                painter.fillPath(fill, fill_color)
        painter.setPen(QtGui.QPen(self._color, 1.7))
        painter.drawPath(path)


class CompactMetricCard(QtWidgets.QFrame):
    _BADGE_LABELS = FRESHNESS_COPY

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("compactCard")
        self.setAccessibleName(title)
        self.setFixedHeight(92)
        layout = QtWidgets.QVBoxLayout(self)
        self.root_layout = layout
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(1)
        header = QtWidgets.QHBoxLayout()
        self.header_layout = header
        self.title = QtWidgets.QLabel(title)
        self.title.setObjectName("compactTitle")
        self.badge = QtWidgets.QLabel("UNKNOWN")
        self.badge.setObjectName("statusBadge")
        self.badge.setMaximumWidth(58)
        self.badge.setAlignment(QtCore.Qt.AlignCenter)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.badge)
        layout.addLayout(header)
        detail = QtWidgets.QHBoxLayout()
        self.detail_layout = detail
        text = QtWidgets.QVBoxLayout()
        self.text_layout = text
        self.body = QtWidgets.QLabel(FRESHNESS_COPY["UNKNOWN"])
        self.body.setObjectName("compactValue")
        self.meta = QtWidgets.QLabel("확인·N/A")
        self.meta.setAccessibleName("확인 · 기준 N/A")
        self.meta.setObjectName("compactMeta")
        self.comparison = QtWidgets.QLabel("평균 비교 N/A")
        self.comparison.setObjectName("compactMeta")
        self.comparison.setFixedHeight(24)
        self.comparison.hide()
        self.stack_value_change = False
        text.addWidget(self.body)
        text.addWidget(self.meta)
        text.addWidget(self.comparison)
        detail.addLayout(text, 4)
        self.sparkline = MiniSparkline()
        detail.addWidget(self.sparkline, 2)
        layout.addLayout(detail)

    def set_view(self, metric: DashboardMetricView | None, series: DashboardSeriesView | None = None) -> None:
        if metric is None:
            self.body.setText(FRESHNESS_COPY["UNKNOWN"])
            self.meta.setText("확인·N/A")
            self.meta.setAccessibleName("확인 · 기준 N/A")
            self.badge.setText(FRESHNESS_COPY["UNKNOWN"])
            self.sparkline.set_values([])
            self.setToolTip("Health V2 metric을 읽을 수 없습니다.")
            return
        manual_unavailable = (
            not metric.displays_value
            and not metric.automation_enabled
            and metric.automation_policy == "MANUAL_BOUNDED"
        )
        compact_status = (
            "수동 확인" if manual_unavailable
            else _compact_freshness_label(metric.freshness)
        )
        self.badge.setText(
            "수동 확인" if manual_unavailable else _freshness_label(metric.freshness)
        )
        display_value = (
            f"{float(metric.value):.5f}"
            if metric.value is not None and metric.unit == "ratio"
            else _fmt(metric.value)
        )
        if metric.displays_value:
            if metric.change is not None and metric.change_pct is not None:
                change_text = (
                    f"{metric.change:+,.2f}\n({metric.change_pct:+.2f}%)"
                    if self.stack_value_change
                    else f" {metric.change:+,.2f} ({metric.change_pct:+.2f}%)"
                )
                change_summary = f"{metric.change:+,.2f} ({metric.change_pct:+.2f}%) · "
            elif metric.change is not None:
                change_text = f" {metric.change:+,.2f}"
                change_summary = f"{metric.change:+,.2f} · "
            elif metric.change_pct is not None:
                change_text = f" ({metric.change_pct:+.2f}%)"
                change_summary = f"{metric.change_pct:+.2f}% · "
            else:
                change_text = ""
                change_summary = ""
            separator = "\n" if self.stack_value_change and change_text else ""
            self.body.setText(
                f"{display_value}{separator}{change_text.strip()}"
                if separator else f"{display_value}{change_text}"
            )
            display_date = self._compact_display_date(metric.as_of)
            self.meta.setText(
                f"{compact_status}·{display_date}"
                if self.stack_value_change
                else f"{change_summary}기준 {display_date}"
            )
            if self.stack_value_change:
                self.meta.setAccessibleName(
                    f"{compact_status} · 기준 {display_date}"
                )
            values = series.frame["value"] if series is not None and "value" in series.frame else []
            color = "#c75745" if (metric.change or 0) > 0 else "#2f6fb2"
            self.sparkline.set_values(values, color)
            if len(values):
                self.sparkline.setAccessibleName(
                    f"{self.title.text()} 완료 일봉 스파크라인"
                )
                self.sparkline.setToolTip(
                    "검증된 로컬 완료 일봉 추이 · 장중 시계열 아님 · 보간 없음"
                )
        else:
            self.body.setText("현재 표시 불가" if manual_unavailable else _display_message(metric))
            display_date = self._compact_display_date(metric.as_of)
            self.meta.setText(
                f"{compact_status}·{display_date}"
                if self.stack_value_change
                else f"기준일 {metric.as_of or 'N/A'}"
            )
            if self.stack_value_change:
                self.meta.setAccessibleName(
                    f"{compact_status} · 기준 {display_date}"
                )
            self.sparkline.set_values([])
        self.setToolTip(
            f"dataset={metric.dataset_id or 'N/A'}\nsource={metric.source}\n"
            f"표시 상태={_freshness_label(metric.freshness)}\nfreshness={metric.freshness}\nPIT={metric.pit_status} · {metric.pit_label}\n"
            f"value={display_value if metric.value is not None else 'N/A'}\n"
            f"change={_fmt(metric.change) if metric.change is not None else 'N/A'}\n"
            f"change_pct={f'{_fmt(metric.change_pct)}%' if metric.change_pct is not None else 'N/A'}\n"
            f"as_of={metric.as_of or 'N/A'}\n"
            f"expected={metric.expected_as_of or 'N/A'}\n{metric.unavailable_reason or metric.route}"
        )

    @staticmethod
    def _compact_display_date(as_of: str | None) -> str:
        """Keep a visible date legible in the narrow ten-card strip."""
        if not isinstance(as_of, str) or not as_of.strip():
            return "N/A"
        text = as_of.strip()
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[5:10]
        if len(text) >= 5 and text[2] == "-":
            return text[:5]
        return text

    def set_average_comparison(
        self,
        view: DashboardAverageComparisonView | None,
        metric: DashboardMetricView | None,
    ) -> None:
        """Show exact daily-average context without turning it into a signal."""
        self.comparison.show()
        self.badge.setMaximumWidth(48)
        status = metric.freshness if metric is not None else "UNKNOWN"
        self.badge.setText(_compact_freshness_label(status))
        self.badge.setAccessibleName(
            f"{self.title.text()} 상태 {_freshness_label(status)}"
        )

        def display(window: int, value: float | None, available: bool) -> str:
            if not available or value is None:
                return f"{window}일 평균 N/A"
            suffix = "bp" if view and view.comparison_kind == "basis_points" else "%"
            return f"{window}일 평균 {value:+.1f}{suffix}"

        if view is None:
            text_5, text_20 = "5일 평균 N/A", "20일 평균 N/A"
            detail = "완료 일봉 평균 비교 view가 없습니다."
        else:
            text_5 = display(5, view.comparison_5, view.displays_5)
            text_20 = display(20, view.comparison_20, view.displays_20)
            coverage_5 = (
                f"{view.coverage_5[0]}..{view.coverage_5[1]} ({view.coverage_5[2]} observations)"
                if view.coverage_5 else "N/A"
            )
            coverage_20 = (
                f"{view.coverage_20[0]}..{view.coverage_20[1]} ({view.coverage_20[2]} observations)"
                if view.coverage_20 else "N/A"
            )
            detail = (
                "completed daily arithmetic-mean comparison; no fill/resample\n"
                f"comparison_kind={view.comparison_kind}\ninterval={view.interval}\n"
                f"latest={view.latest_value if view.latest_value is not None else 'N/A'}\n"
                f"mean_5={view.mean_5 if view.mean_5 is not None else 'N/A'}\n"
                f"comparison_5={view.comparison_5 if view.comparison_5 is not None else 'N/A'}\n"
                f"coverage_5={coverage_5}\nreason_5={view.reason_5 or 'N/A'}\n"
                f"mean_20={view.mean_20 if view.mean_20 is not None else 'N/A'}\n"
                f"comparison_20={view.comparison_20 if view.comparison_20 is not None else 'N/A'}\n"
                f"coverage_20={coverage_20}\nreason_20={view.reason_20 or 'N/A'}\n"
                f"reason={view.unavailable_reason or 'N/A'}"
            )
        self.comparison.setText(f"{text_5}\n{text_20}")
        self.comparison.setAccessibleName(
            f"{self.title.text()} {text_5}, {text_20}; 투자 신호 아님"
        )
        self.comparison.setToolTip(detail)
        self.setToolTip(self.toolTip() + "\n\nDaily average comparison\n" + detail)

    def set_intraday_sparkline(self, view: DashboardSparklineView | None) -> None:
        """Render only a typed completed intraday session; never a daily fallback."""
        self.sparkline.setMinimumWidth(54)
        self.sparkline.setMaximumWidth(16_777_215)
        if view is None or not view.displays_values:
            self.sparkline.set_values([])
            reason = (
                view.unavailable_reason if view is not None
                else "15분 스파크라인 상태가 제공되지 않았습니다."
            )
            self.sparkline.setAccessibleName(f"{self.title.text()} 15분 스파크라인 미표시")
            self.sparkline.setToolTip(reason or "15분 스파크라인 현재 표시 불가")
            self.setToolTip(
                self.toolTip()
                + "\n\n15-minute card sparkline"
                + f"\nlane={view.lane_id if view is not None else 'N/A'}"
                + f"\nseries={view.series_id if view is not None else 'N/A'}"
                + "\ninterval=15m\nstatus=NOT_DISPLAYED"
                + f"\nwindow={view.visual_window if view is not None else 'N/A'}"
                + f"\nreason={reason or 'N/A'}"
            )
            return
        values = pd.to_numeric(view.frame["value"], errors="coerce")
        finite = values.dropna()
        if not finite.empty and view.reference_value is not None:
            change = float(finite.iloc[-1] - view.reference_value)
        else:
            change = float(finite.diff().iloc[-1]) if len(finite) > 1 else 0.0
        color = "#e52f3c" if change > 0 else "#2878d8" if change < 0 else "#8a96a3"
        self.sparkline.set_values(
            values,
            color,
            reference_value=view.reference_value,
        )
        self.sparkline.setAccessibleName(
            f"{self.title.text()} {view.interval} {view.session_label} 스파크라인"
        )
        detail = (
            f"lane={view.lane_id}\nseries={view.series_id}\ninterval={view.interval}\n"
            f"session={view.session_label}\nwindow={view.visual_window}\n"
            f"KST as-of={view.as_of_kst}\nsource_timestamp={view.source_timestamp}\n"
            f"source={view.source}\nfreshness={view.freshness}\n"
            "completed native bars only; no fill/interpolation"
        )
        self.sparkline.setToolTip(detail)
        self.setToolTip(self.toolTip() + "\n\n15-minute card sparkline\n" + detail)


class ThresholdScale(QtWidgets.QWidget):
    def __init__(self, thresholds: tuple[float, ...], *, minimum: float, maximum: float, parent=None):
        super().__init__(parent)
        self.thresholds = tuple(float(value) for value in thresholds)
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.setFixedHeight(13)
        threshold_text = ", ".join(f"{value:g}" for value in self.thresholds)
        self.setAccessibleName(f"기준선 {threshold_text}")

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        if self.maximum <= self.minimum:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        font = painter.font()
        font.setPixelSize(9)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#718198"))
        width = max(1, self.width() - 1)
        for threshold in self.thresholds:
            ratio = min(max((threshold - self.minimum) / (self.maximum - self.minimum), 0.0), 1.0)
            x = int(round(ratio * width))
            painter.drawLine(x, 0, x, 4)
            painter.drawText(
                QtCore.QRect(max(0, x - 12), 3, 24, 10),
                QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                f"{threshold:g}",
            )


class GaugeRow(QtWidgets.QWidget):
    def __init__(
        self,
        label: str,
        *,
        thresholds: tuple[float, ...] = (),
        minimum: float = 0,
        maximum: float = 100,
        threshold_tooltip: str = "",
        parent=None,
    ):
        super().__init__(parent)
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setHorizontalSpacing(7)
        layout.setRowMinimumHeight(0, 18)
        self.label = QtWidgets.QLabel(label)
        self.label.setObjectName("indicatorLabel")
        self.interpretation = QtWidgets.QLabel("확인 불가")
        self.interpretation.setObjectName("indicatorState")
        self.value = QtWidgets.QLabel("표시 불가")
        self.value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        title = QtWidgets.QHBoxLayout()
        title.setSpacing(6)
        title.addWidget(self.label)
        title.addWidget(self.interpretation)
        title.addStretch()
        layout.addLayout(title, 0, 0)
        layout.addWidget(self.value, 0, 1)
        layout.addWidget(self.bar, 1, 0, 1, 2)
        self.threshold_scale = ThresholdScale(
            thresholds,
            minimum=minimum,
            maximum=maximum,
        )
        self.threshold_scale.setVisible(bool(thresholds))
        self.threshold_scale.setToolTip(threshold_tooltip)
        layout.addWidget(self.threshold_scale, 2, 0, 1, 2)
        self.detail = QtWidgets.QLabel()
        self.detail.setObjectName("compactMeta")
        self.detail.setWordWrap(True)
        self.detail.hide()
        layout.addWidget(self.detail, 3, 0, 1, 2)
        # Threshold rows need an 18px title plus bar and 13px scale at native
        # DPR 1. Smaller rows clipped Korean glyphs in the 1600x900 layout.
        self.setMinimumHeight(54 if thresholds else 32)
        self.set_unavailable()

    def set_detail(self, text: str = "") -> None:
        self.detail.setText(text)
        self.detail.setVisible(bool(text))
        self.setMinimumHeight(
            (54 if self.threshold_scale.isVisible() else 32)
            + (18 if text else 0)
        )

    def set_gauge(
        self,
        value: float,
        *,
        minimum: float,
        maximum: float,
        text: str,
        interpretation: str = "",
        tone: str = "neutral",
    ) -> None:
        if not np.isfinite(value) or maximum <= minimum:
            self.set_unavailable()
            return
        position = int(round((min(max(value, minimum), maximum) - minimum) / (maximum - minimum) * 100))
        self.bar.setRange(0, 100)
        self.bar.setValue(position)
        self.bar.setEnabled(True)
        self.value.setText(text)
        self.interpretation.setText(interpretation or "확인")
        self.interpretation.setProperty("tone", tone)
        self.interpretation.style().unpolish(self.interpretation)
        self.interpretation.style().polish(self.interpretation)

    def set_unavailable(self, text: str = "표시 불가") -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setEnabled(False)
        self.value.setText(text)
        self.interpretation.setText("확인 불가")
        self.interpretation.setProperty("tone", "unavailable")
        self.interpretation.style().unpolish(self.interpretation)
        self.interpretation.style().polish(self.interpretation)
        self.set_detail()


class DivergingBar(QtWidgets.QWidget):
    """Signed scale that grows away from the zero midpoint in either direction."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimum = -1.0
        self._maximum = 1.0
        self._value: float | None = None
        self.setFixedHeight(10)

    def set_range(self, minimum: float, maximum: float) -> None:
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self.update()

    def set_value(self, value: float | None) -> None:
        self._value = None if value is None else float(value)
        self.update()

    def value(self) -> float | None:
        return self._value

    def direction(self) -> str:
        if self._value is None or self._value == 0:
            return "center"
        return "left" if self._value < 0 else "right"

    def fill_ratios(self) -> tuple[float, float]:
        span = self._maximum - self._minimum
        if self._value is None or span <= 0:
            return (0.5, 0.5)
        zero = min(max((0.0 - self._minimum) / span, 0.0), 1.0)
        value = min(max((self._value - self._minimum) / span, 0.0), 1.0)
        return (min(zero, value), max(zero, value))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        bounds = self.rect().adjusted(0, 1, -1, -1)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#edf1f6"))
        painter.drawRoundedRect(bounds, 4, 4)
        span = self._maximum - self._minimum
        if self._value is not None and span > 0:
            start, end = self.fill_ratios()
            fill = QtCore.QRectF(
                bounds.left() + start * bounds.width(), bounds.top(),
                max(1.0, (end - start) * bounds.width()), bounds.height(),
            )
            color = "#c75745" if self._value < 0 else "#25845b"
            painter.setBrush(QtGui.QColor(color))
            painter.drawRoundedRect(fill, 4, 4)
        zero = min(max((0.0 - self._minimum) / span, 0.0), 1.0) if span > 0 else 0.5
        zero_x = bounds.left() + zero * bounds.width()
        painter.setPen(QtGui.QPen(QtGui.QColor("#52677f"), 1))
        painter.drawLine(QtCore.QPointF(zero_x, bounds.top() - 1), QtCore.QPointF(zero_x, bounds.bottom() + 1))


class SignedGaugeRow(GaugeRow):
    """Gauge row for signed values; zero is the visual origin, not the left edge."""

    def __init__(self, label: str, **kwargs):
        super().__init__(label, **kwargs)
        old_bar = self.bar
        self.layout().removeWidget(old_bar)
        old_bar.deleteLater()
        self.bar = DivergingBar(self)
        self.layout().addWidget(self.bar, 1, 0, 1, 2)
        self.set_unavailable()

    def set_gauge(
        self,
        value: float,
        *,
        minimum: float,
        maximum: float,
        text: str,
        interpretation: str = "",
        tone: str = "neutral",
    ) -> None:
        if not np.isfinite(value) or maximum <= minimum or minimum >= 0 or maximum <= 0:
            self.set_unavailable()
            return
        self.bar.set_range(minimum, maximum)
        self.bar.set_value(min(max(float(value), minimum), maximum))
        self.bar.setEnabled(True)
        self.value.setText(text)
        self.interpretation.setText(interpretation or "확인")
        self.interpretation.setProperty("tone", tone)
        self.interpretation.style().unpolish(self.interpretation)
        self.interpretation.style().polish(self.interpretation)

    def set_unavailable(self, text: str = "표시 불가") -> None:
        if isinstance(self.bar, DivergingBar):
            self.bar.set_value(None)
            self.bar.setEnabled(False)
            self.value.setText(text)
            self.interpretation.setText("확인 불가")
            self.interpretation.setProperty("tone", "unavailable")
            self.interpretation.style().unpolish(self.interpretation)
            self.interpretation.style().polish(self.interpretation)
            self.set_detail()
        else:
            super().set_unavailable(text)


class RateRow(QtWidgets.QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("rateRow")
        self.setFixedHeight(38)
        self.setAccessibleName(label)
        self._metric_is_materially_old = False
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(0)
        self.label = QtWidgets.QLabel(label)
        self.label.setObjectName("compactTitle")
        self.label.setMinimumWidth(28)
        self.spark = MiniSparkline()
        self.spark.setFixedHeight(17)
        self.spark.setMinimumWidth(36)
        self.value = QtWidgets.QLabel(FRESHNESS_COPY["UNKNOWN"])
        self.value.setObjectName("compactValue")
        self.value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.change = QtWidgets.QLabel("변동 N/A")
        self.change.setObjectName("rateChange")
        self.change.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.change.setProperty("tone", "neutral")
        self.meta = QtWidgets.QLabel("기준일 N/A")
        self.meta.setObjectName("compactMeta")
        layout.addWidget(self.label, 0, 0)
        layout.addWidget(self.value, 0, 1)
        layout.addWidget(self.change, 0, 2)
        layout.addWidget(self.spark, 1, 0)
        layout.addWidget(self.meta, 1, 1, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

    @staticmethod
    def _metric_value(metric: DashboardMetricView) -> str:
        if metric.unit == "quote index points":
            return f"{_fmt(metric.value)} 지수"
        suffix = "%" if metric.unit == "percent" else "%p" if metric.unit == "percentage points" else ""
        return f"{_fmt(metric.value)}{suffix}"

    @staticmethod
    def _change_text(metric: DashboardMetricView) -> tuple[str, str]:
        if metric.change is None or not np.isfinite(metric.change):
            return "변동 N/A", "neutral"
        direction = "▲" if metric.change > 0 else "▼" if metric.change < 0 else "→"
        tone = "positive" if metric.change > 0 else "negative" if metric.change < 0 else "neutral"
        if metric.unit in {"percent", "percentage points"}:
            return f"{direction} {metric.change * 100:+.1f}bp", tone
        if metric.unit in {"KRW per USD", "JPY per USD"}:
            pct = f" ({metric.change_pct:+.2f}%)" if metric.change_pct is not None else ""
            return f"{direction} {metric.change:+.2f}{pct}", tone
        return f"{direction} {metric.change:+.2f}", tone

    def _set_change(self, text: str, tone: str) -> None:
        self.change.setText(text)
        self.change.setProperty("tone", tone)
        self.change.style().unpolish(self.change)
        self.change.style().polish(self.change)

    def _set_metric(
        self,
        metric: DashboardMetricView | None,
        series: DashboardSeriesView | None,
        *,
        reference_kind: str,
    ) -> None:
        # Never recover a number from the accompanying series when the typed
        # metric denied display. Data Status owns the reason and next action.
        self._metric_is_materially_old = self._is_materially_old_currency(metric)
        if metric is None or not metric.displays_value or self._metric_is_materially_old:
            self.value.setText("값 없음" if self._metric_is_materially_old else _display_message(metric))
            self._set_change("변동 N/A", "neutral")
            self.spark.set_values([])
            self.meta.setText(f"기준 {metric.as_of}" if self._metric_is_materially_old and metric else "기준일 N/A")
        else:
            self.value.setText(self._metric_value(metric))
            self._set_change(*self._change_text(metric))
            values = series.frame["value"] if series is not None and "value" in series.frame else []
            self.spark.set_values(values)
            short_date = metric.as_of[5:] if metric.as_of and len(metric.as_of) == 10 else metric.as_of or "N/A"
            self.meta.setText(short_date)
        tooltip = []
        if metric is not None:
            tooltip.append(
                f"{metric.dataset_id} · {metric.series_id} · {metric.source} · "
                f"기준 {metric.as_of or 'N/A'} · {_freshness_label(metric.freshness)}"
            )
            if metric.unavailable_reason:
                tooltip.append(metric.unavailable_reason)
        tooltip.append(f"{reference_kind} · 날짜형 관측은 KST 발행시각을 추정하지 않습니다.")
        self.setToolTip("\n".join(tooltip))

    @staticmethod
    def _is_materially_old_currency(metric: DashboardMetricView | None) -> bool:
        if (
            metric is None
            or metric.unit not in {"KRW per USD", "JPY per USD"}
            or metric.freshness != "EXPECTED_LAG"
            or not metric.as_of
            or not metric.expected_as_of
        ):
            return False
        try:
            observed = pd.Timestamp(metric.as_of).date()
            expected = pd.Timestamp(metric.expected_as_of).date()
        except (TypeError, ValueError):
            return False
        return (expected - observed).days > 3

    def set_views(
        self,
        primary: DashboardMetricView | None,
        series: DashboardSeriesView | None,
        secondary: DashboardMetricView | None = None,
        *,
        primary_tag: str = "60M 지연",
    ) -> None:
        self._set_metric(primary, series, reference_kind=primary_tag)
        if secondary is not None:
            self.setToolTip(
                self.toolTip() + "\n보조 상세: "
                f"{secondary.series_id} · {secondary.source} · {secondary.as_of or 'N/A'}"
            )

    def set_view(self, metric: DashboardMetricView | None, series: DashboardSeriesView | None) -> None:
        self._set_metric(metric, series, reference_kind="공식 일일")

    def set_unavailable(self, reason: str) -> None:
        self.value.setText("현재 표시 불가")
        self._set_change("변동 N/A", "neutral")
        self.spark.set_values([])
        self.meta.setText("상세는 Data Status")
        self.setToolTip(reason)

    def set_average_comparison(
        self, view: DashboardAverageComparisonView | None,
    ) -> None:
        if self._metric_is_materially_old:
            self._set_change("평균 비교 없음", "neutral")
            return
        def one(window: int, value: float | None, available: bool) -> str:
            if not available or value is None:
                return f"{window}일 N/A"
            suffix = "bp" if view and view.comparison_kind == "basis_points" else "%"
            return f"{window}일 {value:+.1f}{suffix}"

        text_5 = one(5, view.comparison_5 if view else None, bool(view and view.displays_5))
        text_20 = one(20, view.comparison_20 if view else None, bool(view and view.displays_20))
        self._set_change(f"{text_5} · {text_20}", "neutral")
        if view is None:
            detail = "완료 일봉 평균 비교 view가 없습니다."
        else:
            detail = (
                f"interval={view.interval}\ncomparison_kind={view.comparison_kind}\n"
                f"mean_5={view.mean_5 if view.mean_5 is not None else 'N/A'}\n"
                f"comparison_5={view.comparison_5 if view.comparison_5 is not None else 'N/A'}\n"
                f"coverage_5={view.coverage_5 or 'N/A'}\n"
                f"mean_20={view.mean_20 if view.mean_20 is not None else 'N/A'}\n"
                f"comparison_20={view.comparison_20 if view.comparison_20 is not None else 'N/A'}\n"
                f"coverage_20={view.coverage_20 or 'N/A'}\n"
                f"reason={view.unavailable_reason or view.reason_20 or view.reason_5 or 'N/A'}\n"
                "descriptive comparison only; not a signal"
            )
        self.setToolTip(self.toolTip() + "\nDaily average comparison\n" + detail)

    def set_treasury_view(
        self,
        view: TreasuryRateView,
        official_series: DashboardSeriesView | None,
        intraday_series: DashboardSeriesView | None,
    ) -> None:
        """Render the delayed Yahoo/Cboe yield as primary and FRED as reference."""
        self.setAccessibleName(view.label)
        official = view.official_daily
        quote = view.intraday_quote
        if quote is not None and quote.displays_value:
            # The accepted Yahoo chart observations already retain yield-level
            # values (for example TNX=4.738, TYX=5.276). Do not divide again.
            yahoo_yield = replace(
                quote,
                value=float(quote.value),
                unit="percent",
                change=(None if quote.change is None else float(quote.change)),
                label=view.label,
            )
            yahoo_series = intraday_series
            if intraday_series is not None and not intraday_series.frame.empty:
                frame = intraday_series.frame.copy()
                frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
                yahoo_series = replace(intraday_series, metric=yahoo_yield, frame=frame)
            self._set_metric(
                yahoo_yield, yahoo_series,
                reference_kind="Yahoo/Cboe 15분 지연 수익률",
            )
        else:
            self._set_metric(official, official_series, reference_kind="공식 FRED 일일 수익률")
        tooltip = [self.toolTip()]
        if quote is not None:
            if quote.displays_value:
                self.meta.setText(f"Yahoo · {quote.as_of or 'N/A'}")
            tooltip.append(
                f"보조 표시 {view.intraday_data_type} · {view.intraday_provider} · "
                f"{quote.source} · 기준 {quote.as_of or 'N/A'} · "
                f"{_freshness_label(quote.freshness)}"
            )
            tooltip.append(
                f"Yahoo 원본 관측={_fmt(quote.value)}; 이미 수익률 수준으로 보존되어 "
                "추가 배율 변환을 적용하지 않았습니다."
            )
        if official is not None:
            tooltip.append(
                f"FRED 일별 기준값={_fmt(official.value) if official.displays_value else _display_message(official)} "
                f"· 기준 {official.as_of or 'N/A'}"
            )
        if view.view_id == "UST10_2_SPREAD":
            tooltip.append("10Y−2Y는 계약된 일별 derived dataset이며 GUI에서 재계산하지 않습니다.")
        self.setToolTip("\n".join(tooltip))


class RateGroup(QtWidgets.QFrame):
    """One visually explicit rate/FX identity group in the compact wide panel."""

    def __init__(self, title: str, rows: tuple[RateRow, ...], parent=None):
        super().__init__(parent)
        self.setObjectName("rateGroup")
        self.setAccessibleName(title)
        self.setFixedHeight(43)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(4)
        label = QtWidgets.QLabel(title)
        label.setObjectName("rateGroupTitle")
        label.setFixedWidth(54)
        layout.addWidget(label)
        for row in rows:
            layout.addWidget(row, 1)


def _clear_private_widget_metadata(root: QtWidgets.QWidget) -> None:
    for widget in (root, *root.findChildren(QtWidgets.QWidget)):
        widget.setToolTip("")
        widget.setStatusTip("")
        widget.setWhatsThis("")
        widget.setAccessibleName("")
        widget.setAccessibleDescription("")


def _scrub_detached_widget(root: QtWidgets.QWidget) -> None:
    """Clear private presentation state before a dynamic widget is detached."""
    _clear_private_widget_metadata(root)
    for widget in (root, *root.findChildren(QtWidgets.QWidget)):
        if isinstance(widget, QtWidgets.QLabel):
            widget.clear()
        elif isinstance(widget, QtWidgets.QProgressBar):
            widget.reset()
            widget.setFormat("")
        elif isinstance(widget, QtWidgets.QComboBox):
            widget.clear()
        elif isinstance(widget, QtWidgets.QTableWidget):
            widget.clearContents()
            widget.setRowCount(0)
        if isinstance(widget, MiniSparkline):
            widget.set_values([])


class AccountOverviewPanel(QtWidgets.QFrame):
    """Privacy-safe account shell; it never invents or retains balance values."""

    open_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._view: AccountSnapshotView | None = None
        self._portfolio: AccountPortfolioView | None = None
        self._balances_hidden = False
        self._revealed = False
        self.setObjectName("panel")
        self.setAccessibleName("계좌 및 자산 현황")
        self.setFixedHeight(42)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(0)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("내 계좌")
        title.setObjectName("sectionTitle")
        self.open_button = QtWidgets.QPushButton("계좌 보기")
        self.open_button.setAccessibleName("전체 Account 화면 열기")
        self.open_button.setToolTip("검증된 로컬 계좌 상세 화면을 엽니다.")
        self.open_button.clicked.connect(self.open_requested)
        self.reveal_button = QtWidgets.QPushButton("계좌 요약 보기")
        self.reveal_button.setAccessibleName("Dashboard 계좌 요약 명시적으로 보기")
        self.reveal_button.setToolTip("현재 프로세스에서만 Dashboard 계좌 요약을 표시합니다.")
        self.reveal_button.clicked.connect(self._toggle_revealed)
        self.badge = QtWidgets.QLabel("연동 전")
        self.badge.setObjectName("statusBadge")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.open_button)
        header.addWidget(self.reveal_button)
        header.addWidget(self.badge)
        layout.addLayout(header)

        self.state = QtWidgets.QLabel("연동 전 · NOT_AVAILABLE")
        self.state.setObjectName("accountUnavailable")
        self.state.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.state, 1)
        self.message = QtWidgets.QLabel("잔액과 자산 변화는 표시하지 않습니다\n읽기 전용 계좌 계약 승인 후 연결")
        self.message.setObjectName("compactMeta")
        self.message.setAlignment(QtCore.Qt.AlignCenter)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.state.hide()
        self.message.hide()

        self.details = QtWidgets.QWidget()
        details_layout = QtWidgets.QGridLayout(self.details)
        details_layout.setContentsMargins(0, 2, 0, 0)
        details_layout.setHorizontalSpacing(10)
        details_layout.setVerticalSpacing(2)
        self.value_labels: dict[str, QtWidgets.QLabel] = {}
        for column, (key, label) in enumerate((
            ("total_assets", "총자산"),
            ("securities_value", "평가금액"),
            ("cash_balance", "현금잔고"),
            ("available_cash", "현금 매수가능"),
            ("unrealized_pnl", "평가손익"),
        )):
            caption = QtWidgets.QLabel(label)
            caption.setObjectName("compactMeta")
            value = QtWidgets.QLabel("N/A")
            value.setObjectName("compactValue")
            details_layout.addWidget(caption, 0, column)
            details_layout.addWidget(value, 1, column)
            self.value_labels[key] = value
        self.history = QtWidgets.QLabel("자산 변화 N/A")
        self.history.setObjectName("compactMeta")
        self.positions = QtWidgets.QLabel("보유 종목 N/A")
        self.positions.setObjectName("compactMeta")
        self.reconciled = QtWidgets.QLabel("대사 시각 N/A")
        self.reconciled.setObjectName("compactMeta")
        self.asset_chart = MiniSparkline()
        self.asset_chart.setFixedHeight(20)
        self.asset_chart.setAccessibleName("계좌 가치 이력 미표시")
        details_layout.addWidget(self.history, 2, 0, 1, 2)
        details_layout.addWidget(self.positions, 2, 2, 1, 2)
        details_layout.addWidget(self.asset_chart, 2, 4)
        details_layout.addWidget(self.reconciled, 3, 0, 1, 5)
        layout.addWidget(self.details)
        self.details.hide()

    def set_unavailable(self, reason: str = "연동 전 / NOT_AVAILABLE") -> None:
        self._revealed = False
        self._view = None
        self._portfolio = None
        _clear_private_widget_metadata(self)
        self.setAccessibleName("계좌 및 자산 현황")
        self.open_button.setAccessibleName("전체 Account 화면 열기")
        self.open_button.setToolTip("검증된 로컬 계좌 상세 화면을 엽니다.")
        self.reveal_button.setText("계좌 요약 보기")
        self.reveal_button.setAccessibleName("Dashboard 계좌 요약 명시적으로 보기")
        self.reveal_button.setToolTip("표시 가능한 로컬 계좌 요약이 없습니다.")
        self.setFixedHeight(60)
        self.badge.setText("데이터 없음")
        self.state.setText("계좌 데이터 없음 · 계좌 보기에서 로컬 상태 확인")
        self.message.setText("로컬 계좌 스냅샷 현재 표시 불가")
        for label in self.value_labels.values():
            label.setText("N/A")
        self.history.setText("자산 변화 N/A")
        self.positions.setText("보유 종목 N/A")
        self.reconciled.setText("대사 시각 N/A")
        self.state.show()
        self.message.hide()
        self.details.hide()
        self.asset_chart.set_values([])
        self.asset_chart.setAccessibleName("계좌 가치 이력 미표시")
        self.setToolTip(
            "실계좌 값은 표시하지 않습니다. 공식 읽기 전용 계좌 계약과 "
            "민감정보 보호 정책이 승인된 뒤 연결할 수 있습니다."
        )

    def set_view(self, view: AccountSnapshotView | None) -> None:
        if view is None or not view.available:
            self.set_unavailable(view.reason if view is not None and view.reason else "연동 전 / NOT_AVAILABLE")
            return
        self._view = view
        self._portfolio = None
        if not self._revealed:
            self._render_collapsed(
                provider=("Toss" if view.state is AccountSnapshotState.TOSS_READ_ONLY else "로컬 snapshot"),
                freshness=(view.freshness or "UNKNOWN"),
            )
            return
        self.setFixedHeight(122)
        self.reveal_button.setText("계좌 요약 숨기기")
        self.reveal_button.setAccessibleName("Dashboard 계좌 요약 즉시 숨기기")
        is_toss = view.state is AccountSnapshotState.TOSS_READ_ONLY
        self.badge.setText("Toss · 읽기 전용" if is_toss else "로컬 snapshot")
        self.state.hide()
        self.message.hide()
        self.details.show()
        for key, label in self.value_labels.items():
            value = getattr(view, key)
            label.setText(
                MASKED_VALUE if self._balances_hidden and value is not None
                else "N/A" if value is None or view.currency is None
                else f"{_fmt(value, 0)} {view.currency}"
            )
        if self._balances_hidden:
            self.history.setText("보유평가 숨김")
        elif is_toss:
            summary_text = " · ".join(
                f"{item.currency} {_fmt(item.securities_value, 2 if item.currency == 'USD' else 0)}"
                for item in view.currency_summaries
            )
            self.history.setText(f"보유평가 {summary_text}" if summary_text else "보유평가 N/A")
        elif len(view.asset_history) >= 2:
            first, last = view.asset_history[0], view.asset_history[-1]
            change = last.total_assets - first.total_assets
            self.history.setText(
                f"자산 변화 {_fmt(change, 0)} {view.currency} · {first.date}→{last.date}"
            )
        else:
            self.history.setText("자산 변화 N/A · 관측 2개 미만")
        if self._balances_hidden:
            self.positions.setText("보유 정보 숨김")
        else:
            symbols = ", ".join(item.symbol for item in view.positions[:3])
            suffix = "…" if len(view.positions) > 3 else ""
            self.positions.setText(
                f"보유 {len(view.positions)}종목" + (f" · {symbols}{suffix}" if symbols else "")
            )
        self.reconciled.setText(f"대사 {view.last_reconciled_at or 'N/A'}")
        if not self._balances_hidden and len(view.asset_history) >= 2:
            self.asset_chart.set_values([point.total_assets for point in view.asset_history])
            self.asset_chart.setAccessibleName(
                f"{view.currency or ''} 계좌 가치 {len(view.asset_history)}개 실제 관측"
            )
        else:
            self.asset_chart.set_values([])
            self.asset_chart.setAccessibleName("계좌 가치 이력 미표시")
        if is_toss:
            self.setToolTip(
                f"Toss 읽기 전용 holdings · 기준 {view.as_of or 'N/A'} · "
                "KRW/USD 미합산 · 현금잔고/실현손익 미제공 · 현금 매수가능금액 제공 · 주문/이체 없음"
            )
        else:
            self.setToolTip(
                "명시적으로 제공된 로컬 snapshot만 표시합니다. "
                "계좌 API, 주문, 전송 또는 누락 날짜 보간을 수행하지 않습니다."
            )

    def set_balances_hidden(self, hidden: bool) -> None:
        # Compatibility input from the full Account page may only tighten the
        # Dashboard boundary. Unmasking the full page never reveals Dashboard.
        self._balances_hidden = bool(hidden)
        if hidden:
            self._set_revealed(False)

    def _toggle_revealed(self) -> None:
        self._set_revealed(not self._revealed)

    def _set_revealed(self, revealed: bool) -> None:
        if revealed and self._portfolio is None and self._view is None:
            return
        self._revealed = bool(revealed)
        self._balances_hidden = False
        if self._portfolio is not None:
            self.set_portfolio(self._portfolio)
        elif self._view is not None:
            self.set_view(self._view)

    def _render_collapsed(self, *, provider: str, freshness: str) -> None:
        """Scrub every widget surface while retaining only safe availability."""

        _clear_private_widget_metadata(self)
        self.setAccessibleName("계좌 및 자산 현황 · 요약 숨김")
        self.open_button.setAccessibleName("전체 Account 화면 열기")
        self.open_button.setToolTip("검증된 로컬 계좌 상세 화면을 엽니다.")
        self.reveal_button.setText("계좌 요약 보기")
        self.reveal_button.setAccessibleName("Dashboard 계좌 요약 명시적으로 보기")
        self.reveal_button.setToolTip("현재 프로세스에서만 Dashboard 계좌 요약을 표시합니다.")
        self.setFixedHeight(66)
        self.badge.setText(f"{provider} · 준비됨")
        self.state.setText(f"계좌 데이터 준비됨 · 요약 숨김 · {freshness}")
        self.message.setText("명시적으로 보기 전까지 금액과 보유 정보는 표시하지 않습니다.")
        for label in self.value_labels.values():
            label.setText("N/A")
        self.history.setText("자산 변화 숨김")
        self.positions.setText("보유 정보 숨김")
        self.reconciled.setText("기준 시각 숨김")
        self.asset_chart.set_values([])
        self.asset_chart.setAccessibleName("계좌 가치 이력 숨김")
        self.details.hide()
        self.message.hide()
        self.state.show()
        self.setToolTip("읽기 전용 로컬 계좌 데이터 사용 가능 · Dashboard 요약 숨김")

    def set_portfolio(self, portfolio: AccountPortfolioView) -> None:
        presentation = build_account_portfolio_presentation(portfolio)
        if not presentation.available:
            self.set_unavailable("유효한 식별정보 제거 로컬 계좌 스냅샷이 없습니다.")
            return
        self._view = None
        self._portfolio = portfolio
        if not self._revealed:
            providers = sorted({
                entry.snapshot.provider or "로컬 snapshot"
                for entry in portfolio.entries if entry.snapshot.available
            })
            self._render_collapsed(
                provider=" / ".join(providers) or "로컬 snapshot",
                freshness=" / ".join(presentation.freshness_values) or "UNKNOWN",
            )
            return
        self.setFixedHeight(122)
        self.badge.setText("읽기 전용")
        self.reveal_button.setText("계좌 요약 숨기기")
        self.reveal_button.setAccessibleName("Dashboard 계좌 요약 즉시 숨기기")
        self.state.hide()
        self.message.hide()
        self.details.show()

        def combined(field: str) -> str:
            values = []
            for row in presentation.currencies:
                value = getattr(row, field)
                if value is not None:
                    values.append(_account_money(value, row.currency, hidden=self._balances_hidden))
            return " / ".join(values) if values else "N/A"

        for key, label in self.value_labels.items():
            label.setText(combined(key))
        holdings_count = len(presentation.holdings)
        self.positions.setText(
            "보유 정보 숨김" if self._balances_hidden else f"보유 {holdings_count}종목 · 통화별 분리"
        )
        histories = presentation.histories
        if self._balances_hidden:
            self.history.setText("보유평가 숨김")
            self.asset_chart.set_values([])
        elif histories:
            history = histories[0]
            self.history.setText(
                f"{history.account_title} 가치 이력 · 실제 {len(history.points)}개 관측"
            )
            self.asset_chart.set_values([point.total_assets for point in history.points])
            self.asset_chart.setAccessibleName(
                f"{history.currency} 계좌 가치 {len(history.points)}개 실제 관측"
            )
        else:
            self.history.setText("가치 이력 N/A · 실제 관측 2개 미만")
            self.asset_chart.set_values([])
        references = " / ".join(_account_reference_kst(value) for value in presentation.as_of_values)
        self.reconciled.setText(f"기준 {references or 'N/A'} · {', '.join(presentation.freshness_values)}")
        self.setToolTip(
            "식별정보 제거 로컬 스냅샷의 통화별 합계만 표시합니다.\n"
            f"displayable_accounts={presentation.displayable_accounts}\n"
            f"unavailable_accounts={presentation.unavailable_accounts}\n"
            f"as_of={presentation.as_of_values}\n"
            f"freshness={presentation.freshness_values}\n"
            "KRW/USD 미합산 · 주문/이체/자동매매 없음 · 순자산 집계와 별도"
        )


class AccountChartsOverview(QtWidgets.QWidget):
    """Currency-scoped charts over the already validated Account presentation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("accountChartsOverview")
        self._presentation: AccountPortfolioPresentationView | None = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        controls = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("계좌 시각화")
        title.setObjectName("sectionTitle")
        controls.addWidget(title)
        controls.addStretch()
        currency_label = QtWidgets.QLabel("표시 통화")
        currency_label.setObjectName("compactMeta")
        self.currency_selector = QtWidgets.QComboBox()
        self.currency_selector.setObjectName("accountChartCurrency")
        self.currency_selector.setAccessibleName("계좌 차트 표시 통화")
        self.currency_selector.currentTextChanged.connect(self._render_selected_currency)
        controls.addWidget(currency_label)
        controls.addWidget(self.currency_selector)
        root.addLayout(controls)

        charts = QtWidgets.QHBoxLayout()
        charts.setSpacing(8)
        self.allocation_panel, self.allocation_chart_view, self.allocation_empty = (
            self._chart_panel("종목별 자산 배분", "accountAllocationChart")
        )
        self.history_panel, self.history_chart_view, self.history_empty = (
            self._chart_panel("누적 증감 금액", "accountHistoryChart")
        )
        charts.addWidget(self.allocation_panel, 1)
        charts.addWidget(self.history_panel, 1)
        root.addLayout(charts)

    @staticmethod
    def _chart_panel(
        title: str, object_name: str,
    ) -> tuple[QtWidgets.QFrame, QtCharts.QChartView, QtWidgets.QLabel]:
        panel = QtWidgets.QFrame()
        panel.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        heading = QtWidgets.QLabel(title)
        heading.setObjectName("compactTitle")
        chart = QtCharts.QChart()
        chart.legend().setVisible(False)
        chart_view = QtCharts.QChartView(chart)
        chart_view.setObjectName(object_name)
        chart_view.setRenderHint(QtGui.QPainter.Antialiasing)
        chart_view.setMinimumHeight(220)
        empty = QtWidgets.QLabel("표시할 검증 데이터가 없습니다.")
        empty.setObjectName("compactMeta")
        empty.setAlignment(QtCore.Qt.AlignCenter)
        empty.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(chart_view, 1)
        layout.addWidget(empty, 1)
        return panel, chart_view, empty

    @staticmethod
    def _clear_chart(view: QtCharts.QChartView) -> None:
        """Remove values from non-widget QtCharts objects before reuse."""

        chart = view.chart()
        view.setToolTip("")
        view.setStatusTip("")
        view.setWhatsThis("")
        view.setAccessibleName("")
        view.setAccessibleDescription("")
        chart.setTitle("")
        chart.legend().setVisible(False)
        for series in tuple(chart.series()):
            series.setName("")
            if isinstance(series, QtCharts.QPieSeries):
                for slice_ in tuple(series.slices()):
                    slice_.setLabel("")
                    slice_.setValue(0.0)
                series.clear()
            elif isinstance(series, QtCharts.QXYSeries):
                series.clear()
            chart.removeSeries(series)
            series.deleteLater()
        for axis in tuple(chart.axes()):
            axis.setTitleText("")
            chart.removeAxis(axis)
            axis.deleteLater()

    @staticmethod
    def _set_empty(
        view: QtCharts.QChartView, label: QtWidgets.QLabel, message: str,
    ) -> None:
        AccountChartsOverview._clear_chart(view)
        view.hide()
        label.setText(message)
        label.setAccessibleName(message)
        label.show()

    def clear_private_state(self, message: str) -> None:
        self._presentation = None
        blocker = QtCore.QSignalBlocker(self.currency_selector)
        self.currency_selector.clear()
        del blocker
        self.currency_selector.hide()
        self._set_empty(self.allocation_chart_view, self.allocation_empty, message)
        self._set_empty(self.history_chart_view, self.history_empty, message)

    def render(
        self,
        presentation: AccountPortfolioPresentationView,
        *,
        hidden: bool,
    ) -> None:
        if hidden:
            self.clear_private_state("금액 및 보유 구성 숨김")
            return
        currencies = sorted({
            *(item.currency for item in presentation.allocations),
            *(item.currency for item in presentation.histories),
        })
        if not currencies:
            self.clear_private_state("표시 가능한 동일통화 차트 데이터가 없습니다.")
            return
        previous = self.currency_selector.currentText()
        self._presentation = presentation
        blocker = QtCore.QSignalBlocker(self.currency_selector)
        self.currency_selector.clear()
        self.currency_selector.addItems(currencies)
        if previous in currencies:
            self.currency_selector.setCurrentText(previous)
        del blocker
        self.currency_selector.show()
        self.currency_selector.setAccessibleName("계좌 차트 표시 통화")
        self._render_selected_currency(self.currency_selector.currentText())

    def _render_selected_currency(self, currency: str) -> None:
        if self._presentation is None or not currency:
            return
        self._render_allocation(currency)
        self._render_history(currency)

    def _render_allocation(self, currency: str) -> None:
        items = tuple(
            item for item in self._presentation.allocations
            if item.currency == currency
        )
        if not items:
            self._set_empty(
                self.allocation_chart_view,
                self.allocation_empty,
                f"{currency}의 완전한 동일통화 평가금액이 없어 배분을 표시하지 않습니다.",
            )
            return
        self._clear_chart(self.allocation_chart_view)
        chart = self.allocation_chart_view.chart()
        series = QtCharts.QPieSeries()
        series.setHoleSize(0.42)
        details = []
        for item in items:
            slice_ = series.append(item.label, item.market_value)
            slice_.setLabel(f"{item.label} {item.weight_pct:.1f}%")
            slice_.setLabelVisible(True)
            details.append(
                f"{item.label}: {item.weight_pct:.1f}% · "
                f"{_account_money(item.market_value, currency, hidden=False)}"
                + (
                    " · 구성 " + ", ".join(item.exact_breakdown)
                    if item.exact_breakdown else ""
                )
            )
        chart.addSeries(series)
        chart.setTitle(f"{currency} · 통화 간 합산 없음")
        chart.legend().setVisible(True)
        self.allocation_chart_view.setToolTip("\n".join(details))
        self.allocation_chart_view.setAccessibleName(f"{currency} 종목별 자산 배분")
        self.allocation_chart_view.setAccessibleDescription("; ".join(details))
        self._apply_chart_palette(chart)
        self.allocation_empty.hide()
        self.allocation_chart_view.show()

    def _render_history(self, currency: str) -> None:
        histories = tuple(
            item for item in self._presentation.histories
            if item.currency == currency
        )
        if not histories:
            self._set_empty(
                self.history_chart_view,
                self.history_empty,
                f"{currency}의 실제 유효 관측이 2개 미만이라 이력을 표시하지 않습니다.",
            )
            return
        self._clear_chart(self.history_chart_view)
        chart = self.history_chart_view.chart()
        timestamps: list[int] = []
        values: list[float] = []
        details = []
        for history in histories:
            series = QtCharts.QLineSeries()
            metric_label = _account_history_metric_label(history.metric)
            series.setName(f"{history.account_title} · {metric_label}")
            series.setPointsVisible(True)
            baseline = float(history.points[0].total_assets)
            for point in history.points:
                try:
                    source_time = datetime.fromisoformat(point.date)
                except ValueError:
                    source_date = date.fromisoformat(point.date)
                    source_time = datetime(
                        source_date.year, source_date.month, source_date.day,
                        tzinfo=timezone.utc,
                    )
                if source_time.tzinfo is None or source_time.utcoffset() is None:
                    source_time = source_time.replace(tzinfo=timezone.utc)
                timestamp = int(source_time.timestamp() * 1000)
                total_value = float(point.total_assets)
                value = total_value - baseline
                series.append(float(timestamp), value)
                timestamps.append(timestamp)
                values.append(value)
                details.append(
                    f"{history.account_title} · {metric_label} · {point.date} · "
                    f"누적 증감 {_account_money(value, currency, hidden=False)} · "
                    f"관측 합계 {_account_money(total_value, currency, hidden=False)}"
                )
            chart.addSeries(series)
        date_axis = QtCharts.QDateTimeAxis()
        date_axis.setFormat("yyyy-MM-dd")
        date_axis.setTitleText("실제 관측일")
        date_axis.setTickCount(min(6, max(2, len(set(timestamps)))))
        date_axis.setRange(
            QtCore.QDateTime.fromMSecsSinceEpoch(
                min(timestamps), QtCore.QTimeZone.utc()
            ),
            QtCore.QDateTime.fromMSecsSinceEpoch(
                max(timestamps), QtCore.QTimeZone.utc()
            ),
        )
        value_axis = QtCharts.QValueAxis()
        value_axis.setTitleText(f"누적 증감 ({currency})")
        value_axis.setLabelFormat("%.2f" if currency == "USD" else "%.0f")
        low, high = min(min(values), 0.0), max(max(values), 0.0)
        padding = max((high - low) * 0.08, abs(high) * 0.01, 1.0)
        value_axis.setRange(low - padding, high + padding)
        chart.addAxis(date_axis, QtCore.Qt.AlignBottom)
        chart.addAxis(value_axis, QtCore.Qt.AlignLeft)
        for series in chart.series():
            series.attachAxis(date_axis)
            series.attachAxis(value_axis)
        chart.setTitle(
            f"{currency} · 각 계좌 첫 관측=0 · 입출금/매매 미분리 · 수익률 아님"
        )
        chart.legend().setVisible(len(histories) > 1)
        self.history_chart_view.setToolTip("\n".join(details))
        self.history_chart_view.setAccessibleName(f"{currency} 계좌 누적 증감 금액")
        self.history_chart_view.setAccessibleDescription("; ".join(details))
        self._apply_chart_palette(chart)
        self.history_empty.hide()
        self.history_chart_view.show()

    def _apply_chart_palette(self, chart: QtCharts.QChart) -> None:
        text = self.palette().color(QtGui.QPalette.Text)
        chart.setTitleBrush(QtGui.QBrush(text))
        chart.legend().setLabelColor(text)
        for axis in chart.axes():
            axis.setLabelsBrush(QtGui.QBrush(text))
            axis.setTitleBrush(QtGui.QBrush(text))

    def changeEvent(self, event: QtCore.QEvent) -> None:
        super().changeEvent(event)
        if (
            event.type() in (QtCore.QEvent.PaletteChange, QtCore.QEvent.StyleChange)
            and hasattr(self, "allocation_chart_view")
        ):
            self._apply_chart_palette(self.allocation_chart_view.chart())
            self._apply_chart_palette(self.history_chart_view.chart())


class ManualAccountDialog(QtWidgets.QDialog):
    """Small local-only editor for one API-less account basis snapshot."""

    _KIND_LABELS = {
        "PENSION": "연금계좌",
        "ISA": "ISA",
        "GENERAL": "일반계좌",
    }

    def __init__(
        self, baseline: ManualAccountRecord | None = None, parent=None,
    ) -> None:
        super().__init__(parent)
        self._source_id = baseline.source_id if baseline else f"manual:{uuid4().hex[:16]}"
        self._record: ManualAccountRecord | None = None
        self.setWindowTitle("수동 계좌 수정" if baseline else "수동 계좌 추가")
        self.resize(760, 430)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "계좌번호는 입력하지 마세요. 현재가가 아닌 기준일의 수량·매입원가만 "
            "로컬에 저장하며 주문·이체 기능은 없습니다."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QtWidgets.QFormLayout()
        self.label_edit = QtWidgets.QLineEdit()
        self.label_edit.setPlaceholderText("예: 미래에셋 연금")
        self.kind_combo = QtWidgets.QComboBox()
        for key, label in self._KIND_LABELS.items():
            self.kind_combo.addItem(label, key)
        self.snapshot_date = QtWidgets.QDateEdit()
        self.snapshot_date.setCalendarPopup(True)
        self.snapshot_date.setDisplayFormat("yyyy-MM-dd")
        self.snapshot_date.setDate(QtCore.QDate.currentDate())
        form.addRow("표시 이름", self.label_edit)
        form.addRow("계좌 종류", self.kind_combo)
        form.addRow("기준일", self.snapshot_date)
        layout.addLayout(form)
        self.positions = QtWidgets.QTableWidget(0, 5)
        self.positions.setHorizontalHeaderLabels(
            ("종목명", "6자리 티커", "수량", "평균단가", "구매총액")
        )
        self.positions.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch
        )
        self.positions.setAccessibleName("수동 계좌 보유 종목 입력")
        layout.addWidget(self.positions)
        row_controls = QtWidgets.QHBoxLayout()
        add_row = QtWidgets.QPushButton("종목 행 추가")
        remove_row = QtWidgets.QPushButton("선택 행 삭제")
        add_row.clicked.connect(self._append_position_row)
        remove_row.clicked.connect(self._remove_selected_position_rows)
        row_controls.addWidget(add_row)
        row_controls.addWidget(remove_row)
        row_controls.addStretch()
        layout.addLayout(row_controls)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("저장")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if baseline is None:
            self._append_position_row()
        else:
            self.label_edit.setText(baseline.label)
            self.kind_combo.setCurrentIndex(
                max(0, self.kind_combo.findData(baseline.account_kind))
            )
            parsed_date = QtCore.QDate.fromString(
                baseline.snapshot_date, QtCore.Qt.ISODate
            )
            self.snapshot_date.setDate(parsed_date)
            for position in baseline.positions:
                self._append_position_row(position)

    def _append_position_row(
        self, position: ManualAccountPosition | None = None,
    ) -> None:
        row = self.positions.rowCount()
        self.positions.insertRow(row)
        values = (
            position.name if position else "",
            position.ticker if position else "",
            self._number_text(position.quantity) if position else "",
            self._number_text(position.average_cost) if position else "",
            self._number_text(position.purchase_total) if position else "",
        )
        for column, value in enumerate(values):
            self.positions.setItem(row, column, QtWidgets.QTableWidgetItem(value))

    @staticmethod
    def _number_text(value: float | None) -> str:
        if value is None:
            return ""
        return format(value, ".15g")

    def _remove_selected_position_rows(self) -> None:
        rows = sorted(
            {index.row() for index in self.positions.selectedIndexes()}, reverse=True
        )
        for row in rows:
            self.positions.removeRow(row)

    def _cell_text(self, row: int, column: int) -> str:
        item = self.positions.item(row, column)
        return item.text().strip() if item is not None else ""

    @staticmethod
    def _parse_number(text: str, *, required: bool) -> float | None:
        normalized = text.replace(",", "").strip()
        if not normalized and not required:
            return None
        if not normalized:
            raise ValueError("수량은 필수입니다")
        try:
            value = float(normalized)
        except ValueError:
            raise ValueError("수량·단가·구매총액은 숫자로 입력해 주세요") from None
        if not math.isfinite(value):
            raise ValueError("숫자는 유한한 값이어야 합니다")
        return value

    def account_record(self) -> ManualAccountRecord:
        if self._record is None:
            raise RuntimeError("dialog has not accepted a valid account")
        return self._record

    def accept(self) -> None:
        try:
            positions = tuple(
                ManualAccountPosition(
                    self._cell_text(row, 0),
                    self._cell_text(row, 1),
                    self._parse_number(self._cell_text(row, 2), required=True),
                    self._parse_number(self._cell_text(row, 3), required=False),
                    self._parse_number(self._cell_text(row, 4), required=False),
                )
                for row in range(self.positions.rowCount())
            )
            record = ManualAccountRecord(
                self._source_id,
                self.label_edit.text().strip(),
                str(self.kind_combo.currentData()),
                self.snapshot_date.date().toString(QtCore.Qt.ISODate),
                "KRW",
                positions,
            )
            manual_account_registry_payload(ManualAccountRegistry((record,)))
        except (TypeError, ValueError) as error:
            QtWidgets.QMessageBox.warning(self, "수동 계좌 입력 확인", str(error))
            return
        self._record = record
        super().accept()


class AccountPage(QtWidgets.QScrollArea):
    """Read-only account detail over validated local projections only."""

    refresh_requested = QtCore.Signal()
    remove_requested = QtCore.Signal()
    import_manual_requested = QtCore.Signal()
    add_manual_requested = QtCore.Signal()
    edit_manual_requested = QtCore.Signal(str)
    remove_manual_requested = QtCore.Signal(str)
    balances_hidden_changed = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._provider_refresh_enabled = False
        self._source_actions: dict[str, AccountSourceActionView] = {}
        self.setWidgetResizable(True)
        self.setAccessibleName("계좌 관리 읽기 전용")
        self.body = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.body)
        self.layout.setContentsMargins(18, 14, 18, 18)
        self.layout.setSpacing(10)
        header = QtWidgets.QHBoxLayout()
        title_box = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("계좌 관리")
        title.setObjectName("pageTitle")
        subtitle = QtWidgets.QLabel(
            "검증된 로컬 읽기 전용 스냅샷만 표시 · 주문·이체 기능 없음 · "
            "순자산은 이 작업공간의 별도 보기"
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        self.layout.addLayout(header)
        controls = QtWidgets.QHBoxLayout()
        controls.addStretch()
        source_label = QtWidgets.QLabel("표시 범위")
        source_label.setObjectName("compactTitle")
        self.source_selector = QtWidgets.QComboBox()
        self.source_selector.setAccessibleName("식별정보 없는 계좌 표시 범위 선택")
        self.source_selector.setToolTip(
            "전체 통합 또는 검증된 개별 source를 명시적으로 선택합니다. "
            "선택만으로 공급자 호출이나 저장은 발생하지 않습니다."
        )
        self.source_selector.setMinimumWidth(190)
        self._source_selector_updating = False
        self._selected_source_id: str | None = None
        self._selected_source_label: str | None = None
        self.source_selector.currentIndexChanged.connect(
            self._source_selection_changed
        )
        controls.addWidget(source_label)
        controls.addWidget(self.source_selector)
        self.hide_balances = QtWidgets.QCheckBox("금액 숨김")
        self.hide_balances.setAccessibleName("계좌 금액과 보유 종목 숨김")
        self.hide_balances.toggled.connect(self._set_balances_hidden)
        controls.addWidget(self.hide_balances)
        self.refresh_button = QtWidgets.QPushButton("로컬 새로 읽기")
        self.refresh_button.clicked.connect(self.refresh_requested)
        controls.addWidget(self.refresh_button)
        self.layout.addLayout(controls)
        manual_controls = QtWidgets.QHBoxLayout()
        manual_controls.addStretch()
        self.import_manual_button = QtWidgets.QPushButton("아빠 CSV로 계좌 추가·갱신")
        self.import_manual_button.setToolTip(
            "Google Sheets의 아빠 탭을 CSV로 내보낸 파일을 이번 한 번만 읽습니다. "
            "두 아빠 계좌만 같은 source로 교체하고 다른 수동 계좌는 보존합니다. "
            "현재가 열은 저장하지 않습니다."
        )
        self.import_manual_button.clicked.connect(self.import_manual_requested)
        manual_controls.addWidget(self.import_manual_button)
        self.add_manual_button = QtWidgets.QPushButton("수동 계좌 추가")
        self.add_manual_button.clicked.connect(self.add_manual_requested)
        manual_controls.addWidget(self.add_manual_button)
        self.edit_manual_button = QtWidgets.QPushButton("선택 계좌 수정")
        self.edit_manual_button.clicked.connect(self._emit_edit_manual_requested)
        manual_controls.addWidget(self.edit_manual_button)
        self.remove_manual_button = QtWidgets.QPushButton("선택 수동계좌 삭제")
        self.remove_manual_button.clicked.connect(self._emit_remove_manual_requested)
        manual_controls.addWidget(self.remove_manual_button)
        self.layout.addLayout(manual_controls)
        privacy_controls = QtWidgets.QHBoxLayout()
        privacy_note = QtWidgets.QLabel(
            "개인 데이터 관리 · 아래 삭제는 선택 계좌가 아니라 공급자 스냅샷과 "
            "계좌 가치 이력 전체에 적용됩니다."
        )
        privacy_note.setObjectName("compactMeta")
        privacy_note.setWordWrap(True)
        privacy_note.setMinimumWidth(0)
        privacy_note.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred,
        )
        privacy_controls.addWidget(privacy_note, 1)
        self.remove_button = QtWidgets.QPushButton("계좌 스냅샷·가치 이력 전체 삭제")
        self.remove_button.setAccessibleName(
            "모든 로컬 계좌 스냅샷과 계좌 가치 이력 삭제"
        )
        self.remove_button.setToolTip(
            "Toss·KB 등 공급자 및 고정 로컬 계좌 스냅샷과 계좌 가치 이력, "
            "관련 임시 기록을 전체 삭제합니다. 수동 계좌 저장소는 보존합니다. "
            "선택한 수동 계좌 삭제는 위의 별도 버튼을 사용하세요."
        )
        self.remove_button.clicked.connect(self.remove_requested)
        privacy_controls.addWidget(self.remove_button)
        self.layout.addLayout(privacy_controls)
        self.summary = QtWidgets.QLabel("사용자 선택 자금 합계 · 현재 표시 불가")
        self.summary.setObjectName("freshness")
        self.summary.setWordWrap(True)
        self.layout.addWidget(self.summary)

        self.empty_state = QtWidgets.QFrame()
        self.empty_state.setObjectName("unavailablePanel")
        empty_layout = QtWidgets.QVBoxLayout(self.empty_state)
        self.empty_title = QtWidgets.QLabel("계좌 데이터 없음")
        self.empty_title.setObjectName("unavailableState")
        self.empty_title.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_detail = QtWidgets.QLabel(
            "검증된 식별정보 제거 로컬 스냅샷이 없습니다.\n"
            "로컬 새로 읽기 또는 계좌별 상태를 확인해 주세요."
        )
        self.empty_detail.setObjectName("compactMeta")
        self.empty_detail.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_detail.setWordWrap(True)
        empty_layout.addStretch()
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_detail)
        empty_layout.addStretch()
        self.layout.addWidget(self.empty_state)
        self.configure_refresh_disclosure(False)

        self.headlines = QtWidgets.QWidget()
        headline_layout = QtWidgets.QHBoxLayout(self.headlines)
        headline_layout.setContentsMargins(0, 0, 0, 0)
        headline_layout.setSpacing(8)
        self.headline_labels: dict[str, QtWidgets.QLabel] = {}
        self.headline_meta: dict[str, QtWidgets.QLabel] = {}
        for key, title_text in (
            ("total_assets", "총자산"),
            ("securities_value", "평가금액"),
            ("cash", "현금잔고 · 매수가능"),
            ("unrealized_pnl", "평가손익"),
        ):
            card = QtWidgets.QFrame()
            card.setObjectName("panel")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(12, 8, 12, 8)
            title_label = QtWidgets.QLabel(title_text)
            title_label.setObjectName("compactTitle")
            value_label = QtWidgets.QLabel("N/A")
            value_label.setObjectName("compactValue")
            value_label.setWordWrap(True)
            meta_label = QtWidgets.QLabel("지원 필드 없음")
            meta_label.setObjectName("compactMeta")
            meta_label.setWordWrap(True)
            card_layout.addWidget(title_label)
            card_layout.addWidget(value_label)
            card_layout.addWidget(meta_label)
            headline_layout.addWidget(card, 1)
            self.headline_labels[key] = value_label
            self.headline_meta[key] = meta_label
        self.layout.addWidget(self.headlines)

        self.account_charts = AccountChartsOverview()
        self.layout.addWidget(self.account_charts)

        overview = QtWidgets.QWidget()
        overview_layout = QtWidgets.QHBoxLayout(overview)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(8)
        allocation_panel = QtWidgets.QFrame()
        allocation_panel.setObjectName("panel")
        allocation_layout = QtWidgets.QVBoxLayout(allocation_panel)
        allocation_title = QtWidgets.QLabel("자산 배분")
        allocation_title.setObjectName("sectionTitle")
        self.allocation_note = QtWidgets.QLabel(
            "상위 5개·비중 3% 이상 개별 표시 · 나머지는 기타"
        )
        self.allocation_note.setObjectName("compactMeta")
        self.allocation_note.setWordWrap(True)
        self.allocation_note.setMinimumWidth(0)
        self.allocation_note.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred,
        )
        self.allocation_rows = QtWidgets.QVBoxLayout()
        allocation_layout.addWidget(allocation_title)
        allocation_layout.addWidget(self.allocation_note)
        allocation_layout.addLayout(self.allocation_rows)
        allocation_layout.addStretch()
        history_panel = QtWidgets.QFrame()
        history_panel.setObjectName("panel")
        history_layout = QtWidgets.QVBoxLayout(history_panel)
        history_title = QtWidgets.QLabel("계좌 가치 이력")
        history_title.setObjectName("sectionTitle")
        self.history_note = QtWidgets.QLabel(
            "첫 관측 대비 누적 증감 · 입출금/매매 미분리 · 수익률 아님 · "
            "계좌·통화·지표별 2개 이상 표시"
        )
        self.history_note.setObjectName("compactMeta")
        self.history_note.setWordWrap(True)
        self.history_note.setMinimumWidth(0)
        self.history_note.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred,
        )
        self.history_rows = QtWidgets.QVBoxLayout()
        history_layout.addWidget(history_title)
        history_layout.addWidget(self.history_note)
        history_layout.addLayout(self.history_rows)
        history_layout.addStretch()
        overview_layout.addWidget(allocation_panel, 1)
        overview_layout.addWidget(history_panel, 1)
        self.overview = overview
        self.layout.addWidget(overview)

        holdings_title = QtWidgets.QLabel("보유 종목")
        holdings_title.setObjectName("sectionTitle")
        self.layout.addWidget(holdings_title)
        self.holdings_table = QtWidgets.QTableWidget(0, 13)
        self.holdings_table.setHorizontalHeaderLabels((
            "종목", "수량", "평균매입가", "현재가", "매입금액", "평가금액",
            "수익률", "평가손익", "비용후손익", "당일손익", "비중",
            "계좌 · 출처", "기준(KST)",
        ))
        self.holdings_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.holdings_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.holdings_table.setAlternatingRowColors(True)
        self.holdings_table.verticalHeader().hide()
        self.holdings_table.horizontalHeader().setStretchLastSection(True)
        self.holdings_table.setMinimumHeight(190)
        self.layout.addWidget(self.holdings_table)

        accounts_title = QtWidgets.QLabel("계좌별 소계")
        accounts_title.setObjectName("sectionTitle")
        self.layout.addWidget(accounts_title)
        self.cards = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.layout.addWidget(self.cards)
        self.layout.addStretch()
        self.setWidget(self.body)
        self._portfolio = AccountPortfolioView(entries=(), user_fund_totals=())
        self._update_manual_controls()

    def configure_refresh_disclosure(self, provider_enabled: bool) -> None:
        """Keep the visible refresh promise aligned with the injected capability."""

        self._provider_refresh_enabled = bool(provider_enabled)
        if self._provider_refresh_enabled:
            self.refresh_button.setText("공급자 갱신 시도 + 로컬 읽기")
            self.refresh_button.setAccessibleName(
                "외부 계좌 공급자 읽기 전용 갱신을 한 번 시도한 뒤 "
                "로컬 스냅샷 새로 읽기"
            )
            self.refresh_button.setToolTip(
                "외부 계좌 공급자에서 읽기 전용 갱신을 한 번 시도한 뒤 "
                "검증된 로컬 스냅샷을 다시 읽습니다."
            )
            self.empty_detail.setText(
                "검증된 식별정보 제거 로컬 스냅샷이 없습니다.\n"
                "공급자 갱신 시도 + 로컬 읽기 또는 계좌별 상태를 확인해 주세요."
            )
            return
        self.refresh_button.setText("로컬 새로 읽기")
        self.refresh_button.setAccessibleName("계좌 로컬 스냅샷 새로 읽기")
        self.refresh_button.setToolTip(
            "외부 공급자를 호출하지 않고 검증된 로컬 계좌 스냅샷만 다시 읽습니다."
        )
        self.empty_detail.setText(
            "검증된 식별정보 제거 로컬 스냅샷이 없습니다.\n"
            "로컬 새로 읽기 또는 계좌별 상태를 확인해 주세요."
        )

    def configure_source_actions(
        self, actions: tuple[AccountSourceActionView, ...],
    ) -> None:
        self._source_actions = {action.source_id: action for action in actions}

    def _set_balances_hidden(self, hidden: bool) -> None:
        self.balances_hidden_changed.emit(bool(hidden))
        self._render_selected_scope()

    def _sync_source_selector(self) -> None:
        selected = self._selected_source_id
        selected_label = self._selected_source_label
        self._source_selector_updating = True
        self.source_selector.clear()
        self.source_selector.addItem("전체 계좌 (통합)", None)
        for entry in self._portfolio.entries:
            self.source_selector.addItem(entry.title, entry.source_id)
        target = self.source_selector.findData(selected)
        if selected is not None and target < 0:
            tombstone_label = selected_label or "선택한 계좌"
            self.source_selector.addItem(
                f"{tombstone_label} · 현재 없음",
                selected,
            )
            target = self.source_selector.count() - 1
        elif target < 0:
            target = 0
        if selected is not None and target >= 0:
            current_label = self.source_selector.itemText(target)
            if not current_label.endswith(" · 현재 없음"):
                self._selected_source_label = current_label
        self.source_selector.setCurrentIndex(target)
        self.source_selector.setEnabled(
            bool(self._portfolio.entries) or self._selected_source_id is not None
        )
        self._source_selector_updating = False

    def _source_selection_changed(self, _index: int) -> None:
        if self._source_selector_updating:
            return
        self._selected_source_id = self.source_selector.currentData()
        self._selected_source_label = (
            self.source_selector.currentText()
            if self._selected_source_id is not None else None
        )
        self._update_manual_controls()
        self._render_selected_scope()

    def selected_source_id(self) -> str | None:
        return self._selected_source_id

    def _update_manual_controls(self) -> None:
        editable = bool(
            self._selected_source_id
            and self._selected_source_id.startswith("manual:")
            and any(
                entry.source_id == self._selected_source_id
                for entry in self._portfolio.entries
            )
        )
        self.edit_manual_button.setEnabled(editable)
        self.remove_manual_button.setEnabled(editable)

    def _emit_edit_manual_requested(self) -> None:
        if self.edit_manual_button.isEnabled() and self._selected_source_id:
            self.edit_manual_requested.emit(self._selected_source_id)

    def _emit_remove_manual_requested(self) -> None:
        if self.remove_manual_button.isEnabled() and self._selected_source_id:
            self.remove_manual_requested.emit(self._selected_source_id)

    def _selected_portfolio(self) -> AccountPortfolioView:
        if self._selected_source_id is None:
            return self._portfolio
        return AccountPortfolioView(
            entries=tuple(
                entry for entry in self._portfolio.entries
                if entry.source_id == self._selected_source_id
            ),
            user_fund_totals=(),
        )

    @staticmethod
    def _clear_dynamic_layout(layout: QtWidgets.QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                _scrub_detached_widget(widget)
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                AccountPage._clear_dynamic_layout(item.layout())

    def _headline_text(
        self,
        presentation: AccountPortfolioPresentationView,
        field: str,
    ) -> str:
        rows = []
        for currency in presentation.currencies:
            value = getattr(currency, field)
            if value is not None:
                rows.append(
                    _account_money(value, currency.currency, hidden=self.hide_balances.isChecked())
                )
        return " / ".join(rows) if rows else "N/A"

    def _render_allocation(
        self, presentation: AccountPortfolioPresentationView,
    ) -> None:
        self._clear_dynamic_layout(self.allocation_rows)
        if self.hide_balances.isChecked():
            label = QtWidgets.QLabel("금액 및 보유 구성 숨김")
            label.setObjectName("compactMeta")
            self.allocation_rows.addWidget(label)
            return
        if not presentation.allocations:
            label = QtWidgets.QLabel("완전한 동일통화 평가금액이 없어 배분을 표시하지 않습니다.")
            label.setObjectName("compactMeta")
            label.setWordWrap(True)
            self.allocation_rows.addWidget(label)
            return
        for item in presentation.allocations:
            row = QtWidgets.QWidget()
            layout = QtWidgets.QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            name = QtWidgets.QLabel(f"{item.label} · {item.currency}")
            name.setObjectName("compactTitle")
            bar = QtWidgets.QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(round(item.weight_pct * 10))
            bar.setFormat(f"{item.weight_pct:.1f}%")
            bar.setAccessibleName(f"{item.label} 비중 {item.weight_pct:.1f}%")
            detail = "\n".join(item.exact_breakdown)
            bar.setToolTip(
                ("기타 grouping: 상위 5개 밖 또는 3% 미만\n" if item.is_other else "")
                + f"exact breakdown\n{detail}"
            )
            layout.addWidget(name, 2)
            layout.addWidget(bar, 3)
            self.allocation_rows.addWidget(row)

    def _render_histories(
        self, presentation: AccountPortfolioPresentationView,
    ) -> None:
        self._clear_dynamic_layout(self.history_rows)
        if self.hide_balances.isChecked():
            label = QtWidgets.QLabel("계좌 가치 이력 숨김")
            label.setObjectName("compactMeta")
            self.history_rows.addWidget(label)
            return
        if not presentation.histories:
            reason = (
                "계좌 규모 이력 검증 실패 · 숫자 표시 차단"
                if presentation.history_reason
                else "실제 유효 관측이 2개 미만이라 차트를 만들지 않습니다."
            )
            label = QtWidgets.QLabel(reason)
            label.setObjectName("compactMeta")
            label.setWordWrap(True)
            self.history_rows.addWidget(label)
            return
        for history in presentation.histories:
            row = QtWidgets.QWidget()
            layout = QtWidgets.QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            label = QtWidgets.QLabel(
                f"{history.account_title} · {history.currency} · "
                f"{_account_history_metric_label(history.metric)} · "
                f"{len(history.points)}개"
            )
            label.setObjectName("compactTitle")
            spark = MiniSparkline()
            spark.setFixedHeight(32)
            spark.set_values([point.total_assets for point in history.points])
            spark.setAccessibleName(
                f"{history.account_title} 실제 계좌 가치 {len(history.points)}개 관측"
            )
            spark.setToolTip(
                f"{history.points[0].date}..{history.points[-1].date} · "
                "실제 로컬 관측만 사용 · 입출금/매매 미분리 · "
                "수익률 아님 · 보간 없음"
            )
            layout.addWidget(label, 2)
            layout.addWidget(spark, 3)
            self.history_rows.addWidget(row)

    def _render_holdings(
        self, presentation: AccountPortfolioPresentationView,
    ) -> None:
        table = self.holdings_table
        table.clearSpans()
        table.clearContents()
        if self.hide_balances.isChecked():
            table.setRowCount(1)
            table.setItem(0, 0, QtWidgets.QTableWidgetItem("보유 정보 숨김"))
            table.setSpan(0, 0, 1, table.columnCount())
            return
        table.setRowCount(len(presentation.holdings))
        for row_index, holding in enumerate(presentation.holdings):
            currency = holding.currency or "N/A"
            reference = holding.price_as_of or holding.as_of
            def percent(value: float | None) -> str:
                return "N/A" if value is None else f"{value:+.2f}%"

            values = (
                f"{holding.name} ({holding.symbol})",
                _fmt(holding.quantity, 4),
                _account_money(
                    holding.average_purchase_price, currency, hidden=False,
                ),
                _account_money(holding.current_price, currency, hidden=False),
                _account_money(holding.purchase_amount, currency, hidden=False),
                _account_money(holding.market_value, currency, hidden=False),
                (
                    f"{holding.return_pct:+.2f}%"
                    if holding.return_pct is not None else "N/A"
                ),
                _account_money(holding.unrealized_pnl, currency, hidden=False),
                _account_money(
                    holding.unrealized_pnl_after_cost, currency, hidden=False,
                ),
                _account_money(holding.daily_pnl, currency, hidden=False),
                f"{holding.weight_pct:.1f}%" if holding.weight_pct is not None else "N/A",
                f"{holding.account_title} · {holding.provider_scope}",
                _account_reference_kst(reference),
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    item.setToolTip(
                        f"주문가능수량={_fmt(holding.orderable_quantity, 4)}\n"
                        f"가격 공급자={holding.price_provider or holding.provider_scope}\n"
                        f"가격 심볼={holding.price_provider_symbol or holding.symbol}\n"
                        f"가격 단위={holding.price_unit or currency}\n"
                        f"가격 기준={_account_reference_kst(reference)}\n"
                        f"가격 최종성={holding.price_finality or holding.freshness}"
                    )
                elif column == 6:
                    item.setToolTip(
                        f"평가수익률={percent(holding.return_pct)}\n"
                        f"비용후수익률={percent(holding.return_pct_after_cost)}\n"
                        f"당일수익률={percent(holding.daily_return_pct)}"
                    )
                elif column in {7, 8, 9}:
                    item.setToolTip(
                        f"수수료={_account_money(holding.commission, currency, hidden=False)}\n"
                        f"세금={_account_money(holding.tax, currency, hidden=False)}"
                    )
                elif column == 11:
                    item.setToolTip(holding.ownership_scope)
                table.setItem(row_index, column, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(11, QtWidgets.QHeaderView.Stretch)

    def _render_account_cards(
        self,
        portfolio: AccountPortfolioView,
        *,
        allow_values: bool,
    ) -> None:
        self._clear_dynamic_layout(self.cards_layout)
        for entry in portfolio.entries:
            view = entry.snapshot
            card = QtWidgets.QFrame()
            card.setObjectName("panel")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(12, 8, 12, 8)
            card_layout.setSpacing(3)
            header = QtWidgets.QHBoxLayout()
            title = QtWidgets.QLabel(entry.title)
            title.setObjectName("sectionTitle")
            state = QtWidgets.QLabel(
                view.state.value if view.displays_values else _freshness_label(view.freshness)
            )
            state.setObjectName("statusBadge")
            header.addWidget(title)
            header.addStretch()
            header.addWidget(state)
            card_layout.addLayout(header)
            if not view.displays_values:
                unavailable = QtWidgets.QLabel(
                    f"현재 표시 불가 · {view.reason or view.freshness or '로컬 상태 미확인'}"
                )
                unavailable.setObjectName("compactMeta")
                card_layout.addWidget(unavailable)
                self.cards_layout.addWidget(card)
                continue
            ownership = (
                "가족 명의 계좌 · 사용자 신고 자금 · 법적 소유 주장 아님"
                if view.registered_holder_scope == "FAMILY_MEMBER"
                else "본인 명의 · 본인 자금"
            )
            ownership_label = QtWidgets.QLabel(ownership)
            ownership_label.setObjectName("compactMeta")
            card_layout.addWidget(ownership_label)
            provider = QtWidgets.QLabel(
                f"{view.provider or 'LOCAL'} · {view.source_mode or 'LOCAL'} · "
                f"{_account_reference_kst(view.as_of)} · {view.freshness}"
            )
            provider.setObjectName("compactMeta")
            card_layout.addWidget(provider)
            action = self._source_actions.get(entry.source_id)
            if action is not None:
                action_panel = QtWidgets.QFrame()
                action_panel.setObjectName("subtlePanel")
                action_layout = QtWidgets.QVBoxLayout(action_panel)
                action_layout.setContentsMargins(8, 5, 8, 5)
                action_layout.setSpacing(2)
                accepted = QtWidgets.QLabel(
                    "마지막 정상 "
                    f"{_account_reference_kst(action.last_accepted_at)} · "
                    f"{action.freshness}"
                    + (f" · {action.reason}" if action.reason else "")
                )
                accepted.setObjectName("compactMeta")
                accepted.setWordWrap(True)
                outcome = QtWidgets.QLabel(
                    f"갱신 {action.refresh_capability} · 최근 결과 "
                    f"{action.last_outcome} "
                    f"({_account_reference_kst(action.last_outcome_at)}) · "
                    f"{action.next_eligibility}"
                )
                outcome.setObjectName("compactMeta")
                outcome.setWordWrap(True)
                action_layout.addWidget(accepted)
                action_layout.addWidget(outcome)
                card_layout.addWidget(action_panel)
            if not allow_values:
                suppressed = QtWidgets.QLabel(
                    "통합 범위 불완전 · 금액과 보유정보 차단 · "
                    "표시 범위에서 이 source를 명시적으로 선택하세요."
                )
                suppressed.setObjectName("compactMeta")
                suppressed.setWordWrap(True)
                card_layout.addWidget(suppressed)
                self.cards_layout.addWidget(card)
                continue
            if self.hide_balances.isChecked():
                subtotal = "금액 숨김"
            elif view.total_assets is not None and view.currency is not None:
                subtotal = "총자산 " + _account_money(view.total_assets, view.currency, hidden=False)
            elif view.currency_summaries:
                subtotal = "평가금액 " + " / ".join(
                    _account_money(row.securities_value, row.currency, hidden=False)
                    for row in view.currency_summaries
                )
            elif view.securities_value is not None and view.currency is not None:
                subtotal = "평가금액 " + _account_money(view.securities_value, view.currency, hidden=False)
            else:
                subtotal = "금액 현재 표시 불가"
            amount = QtWidgets.QLabel(subtotal)
            amount.setObjectName("compactValue")
            card_layout.addWidget(amount)
            buying_power_values = [
                (row.currency, row.cash_buying_power)
                for row in view.currency_summaries
                if row.cash_buying_power is not None
            ]
            if (
                not buying_power_values
                and view.currency is not None
                and view.available_cash is not None
            ):
                buying_power_values.append((view.currency, view.available_cash))
            buying_power_text = (
                " / ".join(
                    _account_money(value, currency, hidden=self.hide_balances.isChecked())
                    for currency, value in buying_power_values
                )
                if buying_power_values else "N/A"
            )
            buying_power = QtWidgets.QLabel(
                f"현금 매수가능 {buying_power_text}"
            )
            buying_power.setObjectName("compactMeta")
            buying_power.setAccessibleName(
                f"{entry.source_id} 통화별 현금 매수가능"
            )
            card_layout.addWidget(buying_power)
            positions = QtWidgets.QLabel(
                "보유 정보 숨김" if self.hide_balances.isChecked()
                else f"보유 {len(view.positions)}종목 · 계좌별 소계"
            )
            positions.setObjectName("compactMeta")
            card_layout.addWidget(positions)
            inclusion = QtWidgets.QLabel(
                "사용자 선택 자금 합계 포함" if view.include_in_user_fund_total
                else "사용자 선택 자금 합계 제외"
            )
            inclusion.setObjectName("compactMeta")
            card_layout.addWidget(inclusion)
            card.setToolTip(
                f"provider={view.provider or 'LOCAL'}\nsource_mode={view.source_mode or 'LOCAL'}\n"
                f"as_of={view.as_of or 'N/A'}\nfreshness={view.freshness}\n"
                f"ownership={ownership}\n주문·이체·자동매매 없음"
            )
            self.cards_layout.addWidget(card)

    def render(self, portfolio: AccountPortfolioView) -> None:
        self._portfolio = portfolio
        self._sync_source_selector()
        self._update_manual_controls()
        self._render_selected_scope()

    def _render_selected_scope(self) -> None:
        presentation = build_account_portfolio_presentation(
            self._portfolio,
            selected_source_id=self._selected_source_id,
        )
        selected_portfolio = self._selected_portfolio()
        has_configured_sources = bool(self._portfolio.entries)
        hidden = self.hide_balances.isChecked()
        self.empty_state.setVisible(not presentation.available)
        self.headlines.setVisible(presentation.available)
        self.overview.setVisible(presentation.available)
        self.holdings_table.setVisible(presentation.available)
        self.cards.setVisible(has_configured_sources)
        self.account_charts.setVisible(presentation.available)

        if not has_configured_sources and self._selected_source_id is None:
            self._portfolio = AccountPortfolioView(entries=(), user_fund_totals=())
            _clear_private_widget_metadata(self)
            self.setAccessibleName("계좌 관리 읽기 전용")
            self.source_selector.setAccessibleName("식별정보 없는 계좌 표시 범위 선택")
            self.source_selector.setToolTip(
                "전체 통합 또는 검증된 개별 source를 명시적으로 선택합니다."
            )
            self.hide_balances.setAccessibleName("계좌 금액과 보유 종목 숨김")
            self.remove_button.setAccessibleName(
                "모든 로컬 계좌 스냅샷과 계좌 가치 이력 삭제"
            )
            self.configure_refresh_disclosure(self._provider_refresh_enabled)
            self.empty_title.setText("계좌 데이터 없음")
            self.summary.setText(
                "계좌 데이터 없음 · 유효한 식별정보 제거 로컬 스냅샷 없음 · "
                "순자산/부동산/전세/채무 화면과 별도"
            )
            for label in self.headline_labels.values():
                label.setText("N/A")
            for meta in self.headline_meta.values():
                meta.setText("현재 표시 불가")
            self._clear_dynamic_layout(self.allocation_rows)
            self._clear_dynamic_layout(self.history_rows)
            self.holdings_table.clearSpans()
            self.holdings_table.clearContents()
            self.holdings_table.setRowCount(0)
            self._clear_dynamic_layout(self.cards_layout)
            self.account_charts.clear_private_state("계좌 데이터 없음")
            return
        if not presentation.available:
            self.empty_title.setText("선택한 계좌 현재 표시 불가")
            self.empty_detail.setText(
                f"{presentation.scope_label} · "
                f"{presentation.scope_reason or '현재 상태를 확인할 수 없습니다.'}"
            )
            self.summary.setText(
                f"{presentation.scope_label} · 현재 표시 불가 · "
                f"{presentation.scope_reason or 'UNKNOWN'} · 통화 합산/추정 없음"
            )
            for label in self.headline_labels.values():
                label.setText("N/A")
            for meta in self.headline_meta.values():
                meta.setText("현재 표시 불가")
            self._clear_dynamic_layout(self.allocation_rows)
            self._clear_dynamic_layout(self.history_rows)
            self.holdings_table.clearSpans()
            self.holdings_table.clearContents()
            self.holdings_table.setRowCount(0)
            self.account_charts.clear_private_state(
                presentation.scope_reason or "현재 표시 불가"
            )
            self._render_account_cards(selected_portfolio, allow_values=False)
            return
        else:
            self.empty_title.setText("계좌 데이터 없음")
            references = " / ".join(
                _account_reference_kst(value) for value in presentation.as_of_values
            )
            scope_prefix = (
                presentation.scope_label
                if presentation.selected_source_id is not None
                else "사용자 선택 자금 합계"
            )
            if hidden:
                fund_summary = f"{scope_prefix} · 금액 숨김"
            else:
                fund_parts = []
                for row in presentation.currencies:
                    if row.total_assets is not None:
                        fund_parts.append(
                            f"{row.currency} {_fmt(row.total_assets, 2 if row.currency == 'USD' else 0)} "
                            f"({row.account_count}개 범위)"
                        )
                    else:
                        fund_parts.append(f"{row.currency} 합산 불가")
                fund_summary = f"{scope_prefix} · " + (" / ".join(fund_parts) or "N/A")
            self.summary.setText(
                fund_summary
                + f" · 검증 계좌 {presentation.displayable_accounts}개"
                + (f" · unavailable {presentation.unavailable_accounts}개" if presentation.unavailable_accounts else "")
                + (f" · {presentation.scope_reason}" if presentation.scope_reason else "")
                + f" · 기준 {references or 'N/A'}"
                + f" · freshness {', '.join(presentation.freshness_values) or 'UNKNOWN'}"
                + " · 통화별 분리 · 순자산 집계와 별도"
            )
            for key in ("total_assets", "securities_value", "unrealized_pnl"):
                self.headline_labels[key].setText(self._headline_text(presentation, key))
            cash_lines = []
            for row in presentation.currencies:
                cash = _account_money(row.cash_balance, row.currency, hidden=hidden)
                available = _account_money(row.available_cash, row.currency, hidden=hidden)
                if cash != "N/A" or available != "N/A":
                    cash_lines.append(f"{row.currency} 예수금 {cash} · 주문가능 {available}")
            self.headline_labels["cash"].setText("\n".join(cash_lines) if cash_lines else "N/A")
            for key, meta in self.headline_meta.items():
                if key == "cash":
                    meta.setText("현금잔고와 현금 매수가능금액은 서로 다른 값")
                elif key == "unrealized_pnl":
                    detail = []
                    for row in presentation.currencies:
                        after_cost = _account_money(
                            row.unrealized_pnl_after_cost,
                            row.currency, hidden=hidden,
                        )
                        daily = _account_money(
                            row.daily_pnl, row.currency, hidden=hidden,
                        )
                        if after_cost != "N/A" or daily != "N/A":
                            detail.append(
                                f"{row.currency} 비용후 {after_cost} · 당일 {daily}"
                            )
                    meta.setText(
                        " / ".join(detail)
                        if detail else "비용후손익·당일손익 미지원"
                    )
                else:
                    meta.setText("통화별 검증된 필드만 표시 · 미지원/부분 합산 없음")
            self._render_allocation(presentation)
            self._render_histories(presentation)
            self.account_charts.render(presentation, hidden=hidden)
            self._render_holdings(presentation)
        self._render_account_cards(
            selected_portfolio,
            allow_values=presentation.scope_complete,
        )


_NET_WORTH_ASSET_LABELS = {
    AssetClass.CASH: "현금성 자산",
    AssetClass.INVESTMENT: "금융 투자자산",
    AssetClass.REAL_ESTATE: "부동산",
    AssetClass.JEONSE_DEPOSIT: "전세 보증금",
    AssetClass.OTHER_RECEIVABLE: "기타 받을 금액",
}
_NET_WORTH_LIABILITY_LABELS = {
    LiabilityClass.MORTGAGE: "주택담보대출",
    LiabilityClass.JEONSE_LOAN: "전세 대출",
    LiabilityClass.DRAWN_OVERDRAFT: "사용한 마이너스통장",
    LiabilityClass.OTHER_DEBT: "기타 채무",
}
_NET_WORTH_ROLE_LABELS = {
    HolderRole.SELF: "본인",
    HolderRole.SPOUSE: "배우자",
    HolderRole.FAMILY: "가족",
    HolderRole.JOINT: "공동",
    HolderRole.OTHER_DECLARED: "기타 신고",
}


def _net_worth_money(value: int | None, *, hidden: bool) -> str:
    if value is None:
        return "N/A"
    return MASKED_VALUE if hidden else f"{value:,} KRW"


def _net_worth_hidden_id(prefix: str) -> str:
    # Numeric UUID runs resemble account identifiers to the repository-wide
    # privacy redactor.  Preserve UUID entropy using an a-p nibble alphabet.
    token = "".join(
        chr(ord("a") + nibble)
        for byte in uuid4().bytes
        for nibble in (byte >> 4, byte & 0x0F)
    )
    return f"{prefix}-{token}"


def _net_worth_snapshot_semantics(snapshot: NetWorthSnapshot) -> tuple[object, ...]:
    """Identity-free editable semantics; excludes revision metadata only."""

    return (
        snapshot.as_of_date,
        snapshot.base_currency,
        snapshot.assets,
        snapshot.liabilities,
    )


class _NetWorthEntryEditor(QtWidgets.QFrame):
    """Controlled editor for one identifier-free asset or liability row."""

    remove_requested = QtCore.Signal(object)

    def __init__(
        self,
        kind: str,
        *,
        as_of: date,
        entry: AssetEntry | LiabilityEntry | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if kind not in {"asset", "liability"}:
            raise ValueError("unsupported net-worth editor kind")
        self.kind = kind
        self._entry_as_of = as_of
        self.record_id = (
            entry.record_id if entry is not None else _net_worth_hidden_id(kind)
        )
        self.economic_claim_id = (
            entry.economic_claim_id
            if entry is not None
            else _net_worth_hidden_id("claim")
        )
        self.setObjectName("compactCard")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("자산 항목" if kind == "asset" else "부채 항목")
        title.setObjectName("compactTitle")
        remove = QtWidgets.QPushButton("항목 제거")
        remove.setAccessibleName(title.text() + " 제거")
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        top.addWidget(title)
        top.addStretch()
        top.addWidget(remove)
        outer.addLayout(top)

        form = QtWidgets.QGridLayout()
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        outer.addLayout(form)

        class_values = AssetClass if kind == "asset" else LiabilityClass
        self.class_combo = self._combo(class_values)
        if kind == "asset":
            for index, item in enumerate(AssetClass):
                self.class_combo.setItemText(index, _NET_WORTH_ASSET_LABELS[item])
        else:
            for index, item in enumerate(LiabilityClass):
                self.class_combo.setItemText(index, _NET_WORTH_LIABILITY_LABELS[item])
        self.gross = self._money_control("총액 KRW")
        self.economic = self._money_control("경제 귀속액 KRW")
        self.unused = (
            self._money_control("미사용 한도 KRW")
            if kind == "liability"
            else None
        )
        self.registered_holder = self._combo(HolderRole)
        self.economic_owner = self._combo(HolderRole)
        self.valuation_date = QtWidgets.QDateEdit()
        self.valuation_date.setCalendarPopup(True)
        self.valuation_date.setDisplayFormat("yyyy-MM-dd")
        self.valuation_date.setMinimumDate(QtCore.QDate(1752, 9, 14))
        self.valuation_date.setDate(QtCore.QDate(as_of.year, as_of.month, as_of.day))
        self.method = self._combo(ValuationMethod)
        self.source = self._combo(ValuationSource)
        self.status = self._combo(ValuationStatus)
        self.uncertainty = self._combo(ValuationUncertainty)

        controls: list[tuple[str, QtWidgets.QWidget]] = [
            ("분류", self.class_combo),
            ("상태", self.status),
            ("총액 KRW", self.gross),
            ("경제 귀속액 KRW", self.economic),
            ("명의 역할", self.registered_holder),
            ("경제 귀속 역할", self.economic_owner),
            ("평가일", self.valuation_date),
            ("평가 방법", self.method),
            ("평가 출처", self.source),
            ("불확실성", self.uncertainty),
        ]
        if self.unused is not None:
            controls.insert(4, ("미사용 한도 KRW", self.unused))
        for position, (label, control) in enumerate(controls):
            row, pair = divmod(position, 2)
            form.addWidget(QtWidgets.QLabel(label), row, pair * 2)
            form.addWidget(control, row, pair * 2 + 1)

        self.status.currentIndexChanged.connect(self._sync_control_state)
        self.class_combo.currentIndexChanged.connect(self._sync_control_state)
        if entry is not None:
            self._load(entry)
        else:
            self._set_combo(self.registered_holder, HolderRole.SELF)
            self._set_combo(self.economic_owner, HolderRole.SELF)
            self._set_combo(self.method, ValuationMethod.USER_DECLARED)
            self._set_combo(self.source, ValuationSource.USER_LOCAL)
            self._set_combo(self.status, ValuationStatus.CURRENT)
            self._set_combo(self.uncertainty, ValuationUncertainty.EXACT)
        self._sync_control_state()

    @staticmethod
    def _combo(values) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        for value in values:
            combo.addItem(value.value, value.value)
        return combo

    @staticmethod
    def _money_control(accessible_name: str) -> QtWidgets.QDoubleSpinBox:
        control = QtWidgets.QDoubleSpinBox()
        control.setAccessibleName(accessible_name)
        control.setDecimals(0)
        control.setRange(0, 9_000_000_000_000_000)
        control.setSingleStep(10_000)
        control.setGroupSeparatorShown(True)
        control.setSuffix(" KRW")
        return control

    @staticmethod
    def _set_combo(combo: QtWidgets.QComboBox, value: object) -> None:
        raw = getattr(value, "value", value)
        index = combo.findData(raw)
        if index < 0:
            raise ValueError("net-worth enum control mismatch")
        combo.setCurrentIndex(index)

    def _load(self, entry: AssetEntry | LiabilityEntry) -> None:
        class_value = (
            entry.asset_class if isinstance(entry, AssetEntry) else entry.liability_class
        )
        self._set_combo(self.class_combo, class_value)
        gross = (
            entry.gross_value_krw
            if isinstance(entry, AssetEntry)
            else entry.gross_principal_krw
        )
        economic = (
            entry.economic_value_krw
            if isinstance(entry, AssetEntry)
            else entry.economic_principal_krw
        )
        self.gross.setValue(float(gross or 0))
        self.economic.setValue(float(economic or 0))
        if self.unused is not None and isinstance(entry, LiabilityEntry):
            self.unused.setValue(float(entry.unused_limit_krw))
        self._set_combo(self.registered_holder, entry.registered_holder_role)
        self._set_combo(self.economic_owner, entry.economic_owner_role)
        if entry.valuation_date is not None:
            self.valuation_date.setDate(QtCore.QDate(
                entry.valuation_date.year,
                entry.valuation_date.month,
                entry.valuation_date.day,
            ))
        self._set_combo(self.method, entry.valuation_method)
        self._set_combo(self.source, entry.valuation_source)
        self._set_combo(self.status, entry.valuation_status)
        self._set_combo(self.uncertainty, entry.uncertainty)

    def _sync_control_state(self) -> None:
        missing = self.status.currentData() == ValuationStatus.MISSING.value
        if missing:
            self.gross.setSpecialValueText("N/A")
            self.economic.setSpecialValueText("N/A")
            self.gross.setValue(0)
            self.economic.setValue(0)
            self.valuation_date.setSpecialValueText("N/A")
            self.valuation_date.setDate(self.valuation_date.minimumDate())
            self._set_combo(self.method, ValuationMethod.NOT_AVAILABLE)
            self._set_combo(self.source, ValuationSource.NOT_AVAILABLE)
            self._set_combo(self.uncertainty, ValuationUncertainty.UNKNOWN)
        else:
            self.gross.setSpecialValueText("")
            self.economic.setSpecialValueText("")
            self.valuation_date.setSpecialValueText("")
            if self.valuation_date.date() == self.valuation_date.minimumDate():
                self.valuation_date.setDate(QtCore.QDate(
                    self._entry_as_of.year,
                    self._entry_as_of.month,
                    self._entry_as_of.day,
                ))
            if self.method.currentData() == ValuationMethod.NOT_AVAILABLE.value:
                self._set_combo(self.method, ValuationMethod.USER_DECLARED)
            if self.source.currentData() == ValuationSource.NOT_AVAILABLE.value:
                self._set_combo(self.source, ValuationSource.USER_LOCAL)
            if self.uncertainty.currentData() == ValuationUncertainty.UNKNOWN.value:
                self._set_combo(self.uncertainty, ValuationUncertainty.EXACT)
        for control in (
            self.gross,
            self.economic,
            self.valuation_date,
            self.method,
            self.source,
            self.uncertainty,
        ):
            control.setEnabled(not missing)
        if self.unused is not None:
            overdraft = (
                self.class_combo.currentData()
                == LiabilityClass.DRAWN_OVERDRAFT.value
            )
            self.unused.setEnabled(overdraft)
            if not overdraft:
                self.unused.setValue(0)

    def payload(self) -> dict[str, object]:
        status = ValuationStatus(self.status.currentData())
        missing = status is ValuationStatus.MISSING
        common: dict[str, object] = {
            "record_id": self.record_id,
            "economic_claim_id": self.economic_claim_id,
            "registered_holder_role": self.registered_holder.currentData(),
            "economic_owner_role": self.economic_owner.currentData(),
            "valuation_date": (
                None
                if missing
                else self.valuation_date.date().toString("yyyy-MM-dd")
            ),
            "valuation_method": (
                ValuationMethod.NOT_AVAILABLE.value
                if missing
                else self.method.currentData()
            ),
            "valuation_source": (
                ValuationSource.NOT_AVAILABLE.value
                if missing
                else self.source.currentData()
            ),
            "valuation_status": status.value,
            "uncertainty": (
                ValuationUncertainty.UNKNOWN.value
                if missing
                else self.uncertainty.currentData()
            ),
        }
        gross = None if missing else int(self.gross.value())
        economic = None if missing else int(self.economic.value())
        if self.kind == "asset":
            return {
                **common,
                "asset_class": self.class_combo.currentData(),
                "gross_value_krw": gross,
                "economic_value_krw": economic,
            }
        return {
            **common,
            "liability_class": self.class_combo.currentData(),
            "gross_principal_krw": gross,
            "economic_principal_krw": economic,
            "unused_limit_krw": int(self.unused.value()) if self.unused else 0,
        }


class NetWorthSnapshotDialog(QtWidgets.QDialog):
    """Identifier-free local create/revise dialog with controlled inputs only."""

    def __init__(
        self,
        *,
        baseline: NetWorthView | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.baseline = baseline
        self.accepted_payload: dict[str, object] | None = None
        self.snapshot_id = _net_worth_hidden_id("snapshot")
        self.asset_editors: list[_NetWorthEntryEditor] = []
        self.liability_editors: list[_NetWorthEntryEditor] = []
        self.setWindowTitle("순자산 스냅샷 수정" if baseline else "순자산 스냅샷 생성")
        self.setModal(True)
        self.resize(980, 720)
        root = QtWidgets.QVBoxLayout(self)
        notice = QtWidgets.QLabel(
            "로컬 KRW 값만 저장합니다. 이름·계좌번호·주소·메모·경로 입력란은 없습니다."
        )
        notice.setObjectName("compactMeta")
        notice.setWordWrap(True)
        root.addWidget(notice)
        date_row = QtWidgets.QHBoxLayout()
        date_row.addWidget(QtWidgets.QLabel("정확한 기준일"))
        self.date_edit = QtWidgets.QDateEdit()
        self.date_edit.setAccessibleName("순자산 정확한 기준일")
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setMaximumDate(QtCore.QDate.currentDate())
        selected = baseline.snapshot.as_of_date if baseline else date.today()
        self.date_edit.setDate(QtCore.QDate(selected.year, selected.month, selected.day))
        self.date_edit.setEnabled(baseline is None)
        date_row.addWidget(self.date_edit)
        date_row.addStretch()
        root.addLayout(date_row)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        self.asset_box, self.asset_layout, self.assets_empty_confirm = (
            self._section("자산", "자산 항목 추가", self.add_asset)
        )
        self.liability_box, self.liability_layout, self.liabilities_empty_confirm = (
            self._section("부채", "부채 항목 추가", self.add_liability)
        )
        body_layout.addWidget(self.asset_box)
        body_layout.addWidget(self.liability_box)
        body_layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.error_label = QtWidgets.QLabel("")
        self.error_label.setObjectName("freshness")
        self.error_label.setWordWrap(True)
        root.addWidget(self.error_label)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Save).setText("검증 후 로컬 저장")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        if baseline is not None:
            for entry in baseline.snapshot.assets:
                self.add_asset(entry)
            for entry in baseline.snapshot.liabilities:
                self.add_liability(entry)
            self.assets_empty_confirm.setChecked(not baseline.snapshot.assets)
            self.liabilities_empty_confirm.setChecked(
                not baseline.snapshot.liabilities
            )
        self._update_empty_controls()

    def _section(
        self,
        title: str,
        add_text: str,
        callback: Callable[[], object],
    ) -> tuple[QtWidgets.QGroupBox, QtWidgets.QVBoxLayout, QtWidgets.QCheckBox]:
        box = QtWidgets.QGroupBox(title)
        layout = QtWidgets.QVBoxLayout(box)
        add = QtWidgets.QPushButton(add_text)
        add.clicked.connect(lambda _checked=False: callback())
        empty = QtWidgets.QCheckBox(f"{title} 항목이 없음을 명시적으로 확인")
        layout.addWidget(add)
        layout.addWidget(empty)
        return box, layout, empty

    @property
    def as_of_date(self) -> date:
        selected = self.date_edit.date()
        return date(selected.year(), selected.month(), selected.day())

    def add_asset(self, entry: AssetEntry | None = None) -> _NetWorthEntryEditor:
        editor = _NetWorthEntryEditor(
            "asset", as_of=self.as_of_date, entry=entry, parent=self.asset_box
        )
        editor.remove_requested.connect(self._remove_editor)
        self.asset_editors.append(editor)
        self.asset_layout.insertWidget(self.asset_layout.count() - 1, editor)
        self._update_empty_controls()
        return editor

    def add_liability(
        self, entry: LiabilityEntry | None = None,
    ) -> _NetWorthEntryEditor:
        editor = _NetWorthEntryEditor(
            "liability",
            as_of=self.as_of_date,
            entry=entry,
            parent=self.liability_box,
        )
        editor.remove_requested.connect(self._remove_editor)
        self.liability_editors.append(editor)
        self.liability_layout.insertWidget(
            self.liability_layout.count() - 1, editor
        )
        self._update_empty_controls()
        return editor

    @QtCore.Slot(object)
    def _remove_editor(self, editor: object) -> None:
        if editor in self.asset_editors:
            self.asset_editors.remove(editor)
            self.asset_layout.removeWidget(editor)
        elif editor in self.liability_editors:
            self.liability_editors.remove(editor)
            self.liability_layout.removeWidget(editor)
        else:
            return
        editor.deleteLater()
        self._update_empty_controls()

    def _update_empty_controls(self) -> None:
        self.assets_empty_confirm.setEnabled(not self.asset_editors)
        self.liabilities_empty_confirm.setEnabled(not self.liability_editors)
        if self.asset_editors:
            self.assets_empty_confirm.setChecked(False)
        if self.liability_editors:
            self.liabilities_empty_confirm.setChecked(False)

    def snapshot_payload(self) -> dict[str, object]:
        if not self.asset_editors and not self.assets_empty_confirm.isChecked():
            raise NetWorthValidationError("NET_WORTH_EMPTY_ASSETS_UNCONFIRMED")
        if (
            not self.liability_editors
            and not self.liabilities_empty_confirm.isChecked()
        ):
            raise NetWorthValidationError(
                "NET_WORTH_EMPTY_LIABILITIES_UNCONFIRMED"
            )
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": self.snapshot_id,
            "as_of_date": self.as_of_date.isoformat(),
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "base_currency": BASE_CURRENCY,
            "assets": [editor.payload() for editor in self.asset_editors],
            "liabilities": [
                editor.payload() for editor in self.liability_editors
            ],
        }
        candidate = parse_snapshot(payload)
        if (
            self.baseline is not None
            and _net_worth_snapshot_semantics(candidate)
            == _net_worth_snapshot_semantics(self.baseline.snapshot)
        ):
            raise NetWorthValidationError("NET_WORTH_NOOP_REJECTED")
        return payload

    def accept(self) -> None:
        try:
            self.accepted_payload = self.snapshot_payload()
        except NetWorthValidationError:
            self.accepted_payload = None
            self.error_label.setText(
                "입력 검증 실패 · 금액이나 내부 식별값은 표시하지 않습니다."
            )
            return
        super().accept()


class NetWorthPage(QtWidgets.QScrollArea):
    """Separate local-only balance sheet over one exact dated typed view."""

    refresh_requested = QtCore.Signal()
    create_requested = QtCore.Signal()
    revise_requested = QtCore.Signal(object)
    remove_exact_requested = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setAccessibleName("로컬 순자산 읽기 전용")
        self._history: tuple[NetWorthHistoryRecord, ...] = ()
        self._view: NetWorthView | None = None
        self._timeline = NetWorthTimelineView(points=())
        self._selected_timeline_point: NetWorthTimelinePoint | None = None
        self._unavailable_reason = "유효한 정확한 날짜 스냅샷 없음"
        self.body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.body)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        titles = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("순자산")
        title.setObjectName("pageTitle")
        subtitle = QtWidgets.QLabel(
            "명시한 로컬 자산·채무의 정확한 날짜별 스냅샷 · brokerage Account와 별도"
        )
        subtitle.setObjectName("pageSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.date_selector = QtWidgets.QComboBox()
        self.date_selector.setAccessibleName("순자산 정확한 스냅샷 날짜")
        self.date_selector.currentIndexChanged.connect(self._select_date)
        header.addWidget(self.date_selector)
        self.hide_values = QtWidgets.QCheckBox("금액 숨김")
        self.hide_values.setAccessibleName("순자산 금액 숨김")
        self.hide_values.toggled.connect(lambda _checked: self._render_current())
        header.addWidget(self.hide_values)
        self.create_button = QtWidgets.QPushButton("새 스냅샷")
        self.create_button.setAccessibleName("새 로컬 순자산 스냅샷 생성")
        self.create_button.clicked.connect(self.create_requested)
        header.addWidget(self.create_button)
        self.revise_button = QtWidgets.QPushButton("선택 날짜 수정")
        self.revise_button.setAccessibleName(
            "선택한 정확한 날짜의 순자산 스냅샷 수정"
        )
        self.revise_button.clicked.connect(self._request_revise)
        header.addWidget(self.revise_button)
        self.remove_button = QtWidgets.QPushButton("이 날짜 스냅샷 삭제")
        self.remove_button.setAccessibleName("선택한 정확한 날짜의 순자산 스냅샷 삭제")
        self.remove_button.clicked.connect(self._request_remove)
        header.addWidget(self.remove_button)
        self.refresh_button = QtWidgets.QPushButton("로컬 새로 읽기")
        self.refresh_button.setAccessibleName("순자산 로컬 스냅샷 새로 읽기")
        self.refresh_button.setToolTip(
            "외부 공급자를 호출하지 않고 검증된 로컬 순자산 스냅샷만 다시 읽습니다."
        )
        self.refresh_button.clicked.connect(self.refresh_requested)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        self.summary = QtWidgets.QLabel("순자산 데이터 없음")
        self.summary.setObjectName("freshness")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.timeline_panel = QtWidgets.QFrame()
        self.timeline_panel.setObjectName("panel")
        timeline_layout = QtWidgets.QVBoxLayout(self.timeline_panel)
        timeline_title = QtWidgets.QLabel("순자산 이력 · 보간 없음")
        timeline_title.setObjectName("sectionTitle")
        self.timeline_dates = QtWidgets.QLabel("이력 없음")
        self.timeline_dates.setObjectName("compactMeta")
        self.timeline_dates.setWordWrap(True)
        self.timeline_delta = QtWidgets.QLabel("이전 완전 스냅샷 비교 불가")
        self.timeline_delta.setObjectName("compactMeta")
        self.timeline_delta.setWordWrap(True)
        self.timeline_chart = QtCharts.QChartView(QtCharts.QChart())
        self.timeline_chart.setRenderHint(QtGui.QPainter.Antialiasing)
        self.timeline_chart.setMinimumHeight(180)
        self.timeline_chart.setAccessibleName("순자산 날짜별 이력 차트")
        timeline_layout.addWidget(timeline_title)
        timeline_layout.addWidget(self.timeline_dates)
        timeline_layout.addWidget(self.timeline_delta)
        timeline_layout.addWidget(self.timeline_chart)
        layout.addWidget(self.timeline_panel)

        self.empty_state = QtWidgets.QFrame()
        self.empty_state.setObjectName("unavailablePanel")
        empty_layout = QtWidgets.QVBoxLayout(self.empty_state)
        empty_title = QtWidgets.QLabel("순자산 스냅샷 없음")
        empty_title.setObjectName("unavailableState")
        empty_title.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_detail = QtWidgets.QLabel(
            "유효한 식별정보 제거 로컬 스냅샷이 없습니다.\n"
            "이 화면은 계좌·외부 공급자에서 값을 자동으로 가져오지 않습니다."
        )
        self.empty_detail.setObjectName("compactMeta")
        self.empty_detail.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_detail.setWordWrap(True)
        empty_layout.addStretch()
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(self.empty_detail)
        empty_layout.addStretch()
        layout.addWidget(self.empty_state)

        self.headlines = QtWidgets.QWidget()
        headline_layout = QtWidgets.QHBoxLayout(self.headlines)
        headline_layout.setContentsMargins(0, 0, 0, 0)
        headline_layout.setSpacing(8)
        self.headline_labels: dict[str, QtWidgets.QLabel] = {}
        self.headline_meta: dict[str, QtWidgets.QLabel] = {}
        for key, title_text in (
            ("liquid", "유동 금융자산"),
            ("assets", "총자산"),
            ("liabilities", "총부채"),
            ("net_worth", "순자산"),
            ("unused_credit", "미사용 신용한도"),
        ):
            card = QtWidgets.QFrame()
            card.setObjectName("panel")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(12, 9, 12, 9)
            caption = QtWidgets.QLabel(title_text)
            caption.setObjectName("compactTitle")
            value = QtWidgets.QLabel("N/A")
            value.setObjectName("compactValue")
            value.setWordWrap(True)
            meta = QtWidgets.QLabel("정확한 현재 값 없음")
            meta.setObjectName("compactMeta")
            meta.setWordWrap(True)
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            card_layout.addWidget(meta)
            headline_layout.addWidget(card, 1)
            self.headline_labels[key] = value
            self.headline_meta[key] = meta
        layout.addWidget(self.headlines)

        columns = QtWidgets.QWidget()
        columns_layout = QtWidgets.QHBoxLayout(columns)
        columns_layout.setContentsMargins(0, 0, 0, 0)
        columns_layout.setSpacing(8)
        asset_panel = QtWidgets.QFrame()
        asset_panel.setObjectName("panel")
        asset_layout = QtWidgets.QVBoxLayout(asset_panel)
        asset_title = QtWidgets.QLabel("자산 · 받을 권리")
        asset_title.setObjectName("sectionTitle")
        asset_note = QtWidgets.QLabel("전세 보증금은 자산으로 별도 표시")
        asset_note.setObjectName("compactMeta")
        self.asset_rows = QtWidgets.QVBoxLayout()
        asset_layout.addWidget(asset_title)
        asset_layout.addWidget(asset_note)
        asset_layout.addLayout(self.asset_rows)
        asset_layout.addStretch()
        liability_panel = QtWidgets.QFrame()
        liability_panel.setObjectName("panel")
        liability_layout = QtWidgets.QVBoxLayout(liability_panel)
        liability_title = QtWidgets.QLabel("부채 · 갚을 의무")
        liability_title.setObjectName("sectionTitle")
        liability_note = QtWidgets.QLabel(
            "전세 대출은 부채, 마이너스통장은 사용액과 미사용 한도 분리"
        )
        liability_note.setObjectName("compactMeta")
        liability_note.setWordWrap(True)
        self.liability_rows = QtWidgets.QVBoxLayout()
        liability_layout.addWidget(liability_title)
        liability_layout.addWidget(liability_note)
        liability_layout.addLayout(self.liability_rows)
        liability_layout.addStretch()
        columns_layout.addWidget(asset_panel, 1)
        columns_layout.addWidget(liability_panel, 1)
        self.breakdowns = columns
        layout.addWidget(columns)
        layout.addStretch()
        self.setWidget(self.body)
        self.render_unavailable()

    @staticmethod
    def _clear_layout(layout: QtWidgets.QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                _scrub_detached_widget(widget)
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def set_history(
        self,
        history: tuple[NetWorthHistoryRecord, ...],
        *,
        selected_date: date | None = None,
    ) -> None:
        self._history = history
        try:
            self._timeline = build_net_worth_timeline(history)
        except NetWorthValidationError:
            self.render_unavailable("로컬 순자산 이력 검증 실패 · HISTORY_INVALID")
            return
        dates = [point.as_of_date for point in self._timeline.points]
        selected = selected_date or self.selected_date
        self.date_selector.blockSignals(True)
        self.date_selector.clear()
        for as_of in dates:
            self.date_selector.addItem(as_of.isoformat(), as_of)
        if dates:
            target = selected if selected in dates else dates[-1]
            self.date_selector.setCurrentIndex(dates.index(target))
        self.date_selector.blockSignals(False)
        self._select_date()

    @property
    def selected_date(self) -> date | None:
        value = self.date_selector.currentData()
        if isinstance(value, date):
            return value
        if isinstance(value, QtCore.QDate) and value.isValid():
            return date(value.year(), value.month(), value.day())
        return None

    def _select_date(self, _index: int = -1) -> None:
        selected = self.selected_date
        records = [
            record
            for record in self._history
            if record.view.snapshot.as_of_date == selected
        ]
        point = next(
            (item for item in self._timeline.points if item.as_of_date == selected),
            None,
        )
        self._selected_timeline_point = point
        if point is not None and point.display_state is NetWorthTimelineDisplayState.GAP:
            self._render_timeline_gap(point)
        elif records:
            record = max(records, key=lambda item: (item.saved_at_utc, item.record_digest))
            self.render(record.view)
        else:
            self.render_unavailable()

    def _request_remove(self) -> None:
        selected = self.selected_date
        if selected is not None:
            self.remove_exact_requested.emit(selected)

    def _request_revise(self) -> None:
        if self._view is not None:
            self.revise_requested.emit(self._view)

    def _render_current(self) -> None:
        if (
            self._selected_timeline_point is not None
            and self._selected_timeline_point.display_state
            is NetWorthTimelineDisplayState.GAP
        ):
            self._render_timeline_gap(self._selected_timeline_point)
        elif self._view is None:
            self.render_unavailable(self._unavailable_reason)
        else:
            self.render(self._view)

    def _render_timeline_gap(self, point: NetWorthTimelinePoint) -> None:
        self._view = None
        for label in self.headline_labels.values():
            label.setText("N/A")
        for meta in self.headline_meta.values():
            meta.setText("현재 표시 불가")
        self._clear_layout(self.asset_rows)
        self._clear_layout(self.liability_rows)
        self.empty_state.show()
        self.headlines.hide()
        self.breakdowns.hide()
        self.revise_button.setEnabled(False)
        self.remove_button.setEnabled(True)
        self.summary.setText(
            f"정확한 기준일 {point.as_of_date.isoformat()} · GAP · 숫자 표시 안 함"
        )
        self.empty_detail.setText(
            f"이 날짜는 {point.display_reason} · 이전 값을 보간하거나 대신 표시하지 않습니다."
        )
        self._render_timeline()

    def _scrub_timeline_chart(self, accessible_name: str) -> None:
        chart = self.timeline_chart.chart()
        chart.removeAllSeries()
        for axis in tuple(chart.axes()):
            chart.removeAxis(axis)
        chart.setTitle("")
        chart.setToolTip("")
        chart.legend().setToolTip("")
        _clear_private_widget_metadata(self.timeline_panel)
        self.timeline_chart.setAccessibleName(accessible_name)
        self.timeline_chart.hide()

    def _render_timeline(self) -> None:
        points = self._timeline.points
        if not points:
            self.timeline_dates.setText("이력 없음")
            self.timeline_delta.setText("이전 완전 스냅샷 비교 불가")
            self._scrub_timeline_chart("순자산 이력 차트 · 표시 불가")
            self.timeline_panel.hide()
            return
        self.timeline_panel.show()
        hidden = self.hide_values.isChecked()
        if hidden:
            self.timeline_dates.setText("순자산 이력 날짜 및 금액 숨김")
            self.timeline_delta.setText("이전 완전 스냅샷 대비 금액 숨김")
            self._scrub_timeline_chart("순자산 이력 차트 · 금액 숨김")
            return
        self.timeline_dates.setText(
            " · ".join(
                f"{point.as_of_date.isoformat()} "
                f"{'표시 가능' if point.display_state is NetWorthTimelineDisplayState.DISPLAYABLE else 'GAP'}"
                for point in points
            )
        )
        selected = self._selected_timeline_point
        if selected is None:
            self.timeline_delta.setText("선택한 날짜 없음 · 이전 완전 스냅샷 비교 불가")
        elif selected.delta_state is NetWorthTimelineDeltaState.AVAILABLE:
            assert selected.delta_from_previous_complete_krw is not None
            assert selected.previous_complete_date is not None
            self.timeline_delta.setText(
                f"이전 완전 {selected.previous_complete_date.isoformat()} 대비 "
                f"{selected.delta_from_previous_complete_krw:+,} KRW"
            )
        else:
            self.timeline_delta.setText(
                f"이전 완전 스냅샷 비교 불가 · {selected.delta_reason}"
            )

        if (
            selected is not None
            and selected.display_state is NetWorthTimelineDisplayState.GAP
        ):
            self._scrub_timeline_chart("순자산 이력 차트 · 선택 날짜 GAP")
            return
        segments: list[list[tuple[float, float]]] = []
        current: list[tuple[float, float]] = []
        chart_values_safe = True
        for index, point in enumerate(points):
            if (
                point.display_state is NetWorthTimelineDisplayState.DISPLAYABLE
                and point.net_worth_krw is not None
            ):
                try:
                    coordinate = float(point.net_worth_krw)
                except (OverflowError, ValueError):
                    chart_values_safe = False
                    break
                if (
                    not math.isfinite(coordinate)
                    or int(coordinate) != point.net_worth_krw
                ):
                    chart_values_safe = False
                    break
                current.append((float(index), coordinate))
            else:
                if current:
                    segments.append(current)
                    current = []
        if current:
            segments.append(current)
        axis_minimum = 0.0
        axis_maximum = 1.0
        if chart_values_safe and segments:
            plotted_values = [value for segment in segments for _, value in segment]
            minimum = min(plotted_values)
            maximum = max(plotted_values)
            span = maximum - minimum
            magnitude = max(1.0, abs(minimum), abs(maximum))
            padding = max(1.0, span * 0.1, magnitude * 0.01)
            axis_minimum = minimum - padding
            axis_maximum = maximum + padding
            chart_values_safe = (
                math.isfinite(span)
                and math.isfinite(padding)
                and math.isfinite(axis_minimum)
                and math.isfinite(axis_maximum)
                and axis_minimum < axis_maximum
            )
        if not chart_values_safe:
            self.timeline_delta.setText(
                "순자산 이력 차트 표시 불가 · CHART_VALUE_OUT_OF_RANGE"
            )
            self._scrub_timeline_chart("순자산 이력 차트 · 값 범위 표시 불가")
            return

        chart = self.timeline_chart.chart()
        chart.removeAllSeries()
        for axis in tuple(chart.axes()):
            chart.removeAxis(axis)
        self.timeline_chart.show()
        self.timeline_chart.setAccessibleName("순자산 날짜별 이력 차트 · GAP 보간 없음")
        for segment in segments:
            series = QtCharts.QLineSeries()
            series.setName("검증된 순자산")
            series.setPointsVisible(True)
            for index, value in segment:
                series.append(float(index), float(value))
            chart.addSeries(series)
        if segments:
            axis_x = QtCharts.QCategoryAxis()
            for index, point in enumerate(points):
                axis_x.append(point.as_of_date.isoformat(), float(index))
            axis_x.setRange(0.0, float(max(1, len(points) - 1)))
            axis_y = QtCharts.QValueAxis()
            axis_y.setLabelFormat("%.0f")
            axis_y.setRange(axis_minimum, axis_maximum)
            chart.addAxis(axis_x, QtCore.Qt.AlignBottom)
            chart.addAxis(axis_y, QtCore.Qt.AlignLeft)
            for series in chart.series():
                series.attachAxis(axis_x)
                series.attachAxis(axis_y)
        chart.legend().hide()

    def render_unavailable(self, reason: str = "유효한 정확한 날짜 스냅샷 없음") -> None:
        self._view = None
        self._history = ()
        self._timeline = NetWorthTimelineView(points=())
        self._selected_timeline_point = None
        _clear_private_widget_metadata(self)
        self.setAccessibleName("로컬 순자산 읽기 전용")
        self.date_selector.setAccessibleName("순자산 정확한 스냅샷 날짜")
        self.hide_values.setAccessibleName("순자산 금액 숨김")
        self.create_button.setAccessibleName("새 로컬 순자산 스냅샷 생성")
        self.revise_button.setAccessibleName(
            "선택한 정확한 날짜의 순자산 스냅샷 수정"
        )
        self.remove_button.setAccessibleName("선택한 정확한 날짜의 순자산 스냅샷 삭제")
        self.refresh_button.setAccessibleName("순자산 로컬 스냅샷 새로 읽기")
        self.refresh_button.setToolTip(
            "외부 공급자를 호출하지 않고 검증된 로컬 순자산 스냅샷만 다시 읽습니다."
        )
        self.date_selector.blockSignals(True)
        self.date_selector.clear()
        self.date_selector.blockSignals(False)
        for label in self.headline_labels.values():
            label.setText("N/A")
        for meta in self.headline_meta.values():
            meta.setText("현재 표시 불가")
        self._clear_layout(self.asset_rows)
        self._clear_layout(self.liability_rows)
        self.empty_state.show()
        self.headlines.hide()
        self.breakdowns.hide()
        self.timeline_dates.setText("이력 없음")
        self.timeline_delta.setText("이전 완전 스냅샷 비교 불가")
        self._scrub_timeline_chart("순자산 이력 차트 · 표시 불가")
        self.timeline_panel.hide()
        self.create_button.setEnabled(True)
        self.revise_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.summary.setText(
            "순자산 데이터 없음 · 숫자 표시 안 함 · Account 화면과 별도"
        )
        safe_reasons = {
            "유효한 정확한 날짜 스냅샷 없음",
            "로컬 순자산 이력 검증 실패 · HISTORY_INVALID",
            "삭제할 정확한 날짜가 없습니다.",
        }
        safe_reason = (
            reason if reason in safe_reasons
            else "로컬 순자산 스냅샷 현재 표시 불가"
        )
        self._unavailable_reason = safe_reason
        self.empty_detail.setText(
            f"{safe_reason}\n과거 날짜 값을 대신 표시하거나 외부 공급자를 호출하지 않습니다."
        )

    def _headline_meta(self, view: NetWorthView, key: str) -> str:
        stale_assets = any(
            item.valuation_status is not ValuationStatus.CURRENT
            for item in view.snapshot.assets
        )
        stale_liabilities = any(
            item.valuation_status is not ValuationStatus.CURRENT
            for item in view.snapshot.liabilities
        )
        stale_liquid = any(
            item.valuation_status is not ValuationStatus.CURRENT
            for item in view.snapshot.assets
            if item.asset_class in {AssetClass.CASH, AssetClass.INVESTMENT}
        )
        if key == "liquid" and stale_liquid:
            return "오래됨/누락 금융자산이 있어 합산 안 함"
        if key == "assets" and stale_assets:
            return "오래됨/누락 자산이 있어 합산 안 함"
        if key == "liabilities" and stale_liabilities:
            return "오래됨/누락 부채가 있어 합산 안 함"
        if key == "net_worth" and (stale_assets or stale_liabilities):
            return "자산 또는 부채가 불완전해 계산 안 함"
        if key == "unused_credit":
            return "부채에 포함하지 않는 미사용 한도"
        return "경제 귀속액 기준 · 중복 청구 차단"

    def render(self, view: NetWorthView) -> None:
        self._view = view
        hidden = self.hide_values.isChecked()
        snapshot = view.snapshot
        totals = view.totals
        self.empty_state.hide()
        self.headlines.show()
        self.breakdowns.show()
        self.create_button.setEnabled(True)
        self.revise_button.setEnabled(True)
        self.remove_button.setEnabled(True)
        state = "완전" if totals.complete else "부분 · 영향받은 합계 숨김"
        self.summary.setText(
            f"정확한 기준일 {snapshot.as_of_date.isoformat()} · KRW · {state} · "
            f"오래됨 {len(totals.stale_claim_ids)} · 누락 {len(totals.missing_claim_ids)} · "
            f"불확실성 표시 {len(totals.uncertain_claim_ids)} · Account와 자동 합산 안 함"
        )
        overdrafts = [
            item
            for item in snapshot.liabilities
            if item.liability_class is LiabilityClass.DRAWN_OVERDRAFT
        ]
        unused_credit = (
            totals.unused_credit_limit_krw
            if all(item.valuation_status is ValuationStatus.CURRENT for item in overdrafts)
            else None
        )
        values = {
            "liquid": totals.liquid_financial_assets_krw,
            "assets": totals.total_assets_krw,
            "liabilities": totals.total_liabilities_krw,
            "net_worth": totals.net_worth_krw,
            "unused_credit": unused_credit,
        }
        for key, label in self.headline_labels.items():
            label.setText(_net_worth_money(values[key], hidden=hidden))
            self.headline_meta[key].setText(self._headline_meta(view, key))
        self._render_assets(view)
        self._render_liabilities(view)
        self._render_timeline()

    def _entry_card(
        self,
        *,
        title: str,
        amount: int | None,
        current: bool,
        holder: HolderRole,
        owner: HolderRole,
        valuation_date: date | None,
        method: object,
        source: object,
        status: ValuationStatus,
        uncertainty: object,
        extra: str | None = None,
    ) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("compactCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)
        top = QtWidgets.QHBoxLayout()
        name = QtWidgets.QLabel(title)
        name.setObjectName("compactTitle")
        value = QtWidgets.QLabel(
            _net_worth_money(amount if current else None, hidden=self.hide_values.isChecked())
        )
        value.setObjectName("compactValue")
        badge = QtWidgets.QLabel(status.value)
        badge.setObjectName("statusBadge")
        top.addWidget(name)
        top.addStretch()
        top.addWidget(value)
        top.addWidget(badge)
        roles = QtWidgets.QLabel(
            f"명의 역할 {_NET_WORTH_ROLE_LABELS[holder]} · 경제 귀속 {_NET_WORTH_ROLE_LABELS[owner]}"
        )
        roles.setObjectName("compactMeta")
        details = QtWidgets.QLabel(
            f"평가일 {valuation_date.isoformat() if valuation_date else 'N/A'} · "
            f"{getattr(method, 'value', method)} · {getattr(source, 'value', source)} · "
            f"불확실성 {getattr(uncertainty, 'value', uncertainty)}"
            + (f" · {extra}" if extra else "")
        )
        details.setObjectName("compactMeta")
        details.setWordWrap(True)
        layout.addLayout(top)
        layout.addWidget(roles)
        layout.addWidget(details)
        card.setToolTip(
            details.text()
            + "\n식별자·주소·계좌번호 비보존 · 경제 귀속액 기준 · 경로 비표시"
        )
        return card

    def _render_assets(self, view: NetWorthView) -> None:
        self._clear_layout(self.asset_rows)
        for entry in view.snapshot.assets:
            current = entry.valuation_status is ValuationStatus.CURRENT
            self.asset_rows.addWidget(self._entry_card(
                title=_NET_WORTH_ASSET_LABELS[entry.asset_class],
                amount=entry.economic_value_krw,
                current=current,
                holder=entry.registered_holder_role,
                owner=entry.economic_owner_role,
                valuation_date=entry.valuation_date,
                method=entry.valuation_method,
                source=entry.valuation_source,
                status=entry.valuation_status,
                uncertainty=entry.uncertainty,
            ))

    def _render_liabilities(self, view: NetWorthView) -> None:
        self._clear_layout(self.liability_rows)
        for entry in view.snapshot.liabilities:
            current = entry.valuation_status is ValuationStatus.CURRENT
            extra = None
            if entry.liability_class is LiabilityClass.DRAWN_OVERDRAFT:
                unused = _net_worth_money(
                    entry.unused_limit_krw if current else None,
                    hidden=self.hide_values.isChecked(),
                )
                extra = f"미사용 한도 {unused} · 부채 제외"
            self.liability_rows.addWidget(self._entry_card(
                title=_NET_WORTH_LIABILITY_LABELS[entry.liability_class],
                amount=entry.economic_principal_krw,
                current=current,
                holder=entry.registered_holder_role,
                owner=entry.economic_owner_role,
                valuation_date=entry.valuation_date,
                method=entry.valuation_method,
                source=entry.valuation_source,
                status=entry.valuation_status,
                uncertainty=entry.uncertainty,
                extra=extra,
            ))


_DASHBOARD_CARD_LABELS = {
    "KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ",
    "NQ_FUTURES": "Nasdaq 100", "NASDAQ": "Nasdaq", "SP500": "S&P 500",
    "SOXX": "SOXX", "GOLD": "GOLD", "WTI": "WTI", "BITCOIN": "BITCOIN",
    "USD_KRW_60M": "USD/KRW",
}
_DASHBOARD_SECTION_LABELS = {
    "KOSPI_CHART": "KOSPI/시장 차트",
    "NQ_CHART": "NQ 연속선물 차트 (대시보드 미표시)",
    "MARKET_TEMPERATURE": "시장 온도",
    # Keep the persisted section identity stable while presenting both of the
    # accepted read-only market-context views in one compact side slot.
    "MARKET_FLOW": "시장 수급 · 밸류·실적",
    "FX_RATES": "환율·금리",
    "ACCOUNT_SUMMARY": "내 계좌 요약",
    "DERIVATIVES": "파생상품 요약",
}


class DashboardPreferencesDialog(QtWidgets.QDialog):
    """Keyboard-accessible editor for presentation-only Dashboard settings."""

    preferences_applied = QtCore.Signal(object)
    reset_requested = QtCore.Signal()

    def __init__(self, preferences: DashboardPreferences, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dashboard 화면 구성")
        self.setModal(False)
        self.resize(760, 680)
        self._preferences = preferences
        root = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("Dashboard 화면 구성")
        title.setObjectName("pageTitle")
        note = QtWidgets.QLabel(
            "표시와 순서만 바뀝니다. 데이터 수집·freshness·계좌 값·watchlist는 변경하지 않습니다.\n"
            "전체 실패 상태와 사용자 확인 필요 알림은 항상 Dashboard 상단과 Data Status에서 확인할 수 있습니다."
        )
        note.setObjectName("pageSubtitle")
        note.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(note)

        lists = QtWidgets.QHBoxLayout()
        card_box = QtWidgets.QGroupBox("시장 카드 · 체크=표시 · ★=고정")
        card_layout = QtWidgets.QVBoxLayout(card_box)
        self.card_list = QtWidgets.QListWidget()
        self.card_list.setAccessibleName("Dashboard 시장 카드 표시 고정 순서")
        self.card_list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.card_list.setDefaultDropAction(QtCore.Qt.MoveAction)
        card_layout.addWidget(self.card_list)
        card_buttons = QtWidgets.QHBoxLayout()
        self.pin_button = QtWidgets.QPushButton("고정 전환")
        self.pin_button.setAccessibleName("선택한 시장 카드 맨 앞 고정 전환")
        self.card_up = QtWidgets.QPushButton("위로")
        self.card_up.setAccessibleName("선택한 시장 카드 위로 이동 Alt Up")
        self.card_down = QtWidgets.QPushButton("아래로")
        self.card_down.setAccessibleName("선택한 시장 카드 아래로 이동 Alt Down")
        card_buttons.addWidget(self.pin_button)
        card_buttons.addWidget(self.card_up)
        card_buttons.addWidget(self.card_down)
        card_layout.addLayout(card_buttons)
        lists.addWidget(card_box, 1)

        section_box = QtWidgets.QGroupBox("섹션 · 각 화면 영역 안에서 순서 적용")
        section_layout = QtWidgets.QVBoxLayout(section_box)
        self.section_list = QtWidgets.QListWidget()
        self.section_list.setAccessibleName("Dashboard 섹션 표시 순서")
        self.section_list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.section_list.setDefaultDropAction(QtCore.Qt.MoveAction)
        section_layout.addWidget(self.section_list)
        section_buttons = QtWidgets.QHBoxLayout()
        self.section_up = QtWidgets.QPushButton("위로")
        self.section_up.setAccessibleName("선택한 Dashboard 섹션 위로 이동 Control Alt Up")
        self.section_down = QtWidgets.QPushButton("아래로")
        self.section_down.setAccessibleName("선택한 Dashboard 섹션 아래로 이동 Control Alt Down")
        section_buttons.addWidget(self.section_up)
        section_buttons.addWidget(self.section_down)
        section_layout.addLayout(section_buttons)
        lists.addWidget(section_box, 1)
        root.addLayout(lists, 1)

        defaults = QtWidgets.QGroupBox("밀도와 기본 선택")
        default_layout = QtWidgets.QGridLayout(defaults)
        self.density = QtWidgets.QComboBox()
        self.density.addItem("자세히", "DETAIL")
        self.density.addItem("간단히", "COMPACT")
        self.density.setAccessibleName("Dashboard 카드 표시 밀도")
        self.market_asset = QtWidgets.QComboBox()
        self.market_asset.addItems(["KOSPI", "KOSDAQ", "SOXX", "NASDAQ", "SP500"])
        self.market_asset.setAccessibleName("Dashboard 기본 시장 차트")
        self.market_period = QtWidgets.QComboBox()
        self.market_period.addItems(MARKET_PERIODS)
        self.market_period.setAccessibleName("Dashboard 기본 시장 기간")
        self.nq_interval = QtWidgets.QComboBox()
        self.nq_interval.addItems(["일봉", "주봉", "월봉"])
        self.nq_interval.setAccessibleName("Dashboard 기본 NQ 기간")
        for column, (label_text, widget) in enumerate((
            ("카드 밀도", self.density), ("시장", self.market_asset),
            ("시장 기간", self.market_period), ("NQ 주기", self.nq_interval),
        )):
            label = QtWidgets.QLabel(label_text)
            label.setBuddy(widget)
            default_layout.addWidget(label, 0, column)
            default_layout.addWidget(widget, 1, column)
        root.addWidget(defaults)

        bottom = QtWidgets.QHBoxLayout()
        self.reset_button = QtWidgets.QPushButton("기본 구성으로 즉시 재설정")
        self.reset_button.setAccessibleName("Dashboard 기본 구성과 1600x900 창으로 즉시 재설정")
        bottom.addWidget(self.reset_button)
        bottom.addStretch()
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Apply | QtWidgets.QDialogButtonBox.Close
        )
        buttons.button(QtWidgets.QDialogButtonBox.Apply).setText("적용 및 저장")
        buttons.button(QtWidgets.QDialogButtonBox.Close).setText("닫기")
        bottom.addWidget(buttons)
        root.addLayout(bottom)

        self.pin_button.clicked.connect(self._toggle_pin)
        self.card_up.clicked.connect(lambda: self._move(self.card_list, -1))
        self.card_down.clicked.connect(lambda: self._move(self.card_list, 1))
        self.section_up.clicked.connect(lambda: self._move(self.section_list, -1))
        self.section_down.clicked.connect(lambda: self._move(self.section_list, 1))
        self.reset_button.clicked.connect(self._reset)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self._apply)
        buttons.rejected.connect(self.close)
        self._card_up_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Alt+Up"), self)
        self._card_down_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Alt+Down"), self)
        self._section_up_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Alt+Up"), self)
        self._section_down_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Alt+Down"), self)
        self._card_up_shortcut.activated.connect(lambda: self._move(self.card_list, -1))
        self._card_down_shortcut.activated.connect(lambda: self._move(self.card_list, 1))
        self._section_up_shortcut.activated.connect(lambda: self._move(self.section_list, -1))
        self._section_down_shortcut.activated.connect(lambda: self._move(self.section_list, 1))
        self.load_preferences(preferences)

    @staticmethod
    def _item(identifier: str, label: str, *, checked: bool, pinned: bool = False):
        item = QtWidgets.QListWidgetItem(("★ " if pinned else "") + label)
        item.setData(QtCore.Qt.UserRole, identifier)
        item.setData(QtCore.Qt.UserRole + 1, pinned)
        item.setFlags(
            item.flags() | QtCore.Qt.ItemIsUserCheckable
            | QtCore.Qt.ItemIsDragEnabled | QtCore.Qt.ItemIsDropEnabled
        )
        item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        return item

    def load_preferences(self, preferences: DashboardPreferences) -> None:
        self._preferences = preferences
        self.card_list.clear()
        for identifier in preferences.card_order:
            self.card_list.addItem(self._item(
                identifier, _DASHBOARD_CARD_LABELS[identifier],
                checked=identifier not in preferences.hidden_cards,
                pinned=identifier in preferences.pinned_cards,
            ))
        self.section_list.clear()
        for identifier in preferences.section_order:
            self.section_list.addItem(self._item(
                identifier, _DASHBOARD_SECTION_LABELS[identifier],
                checked=identifier not in preferences.hidden_sections,
            ))
        self.density.setCurrentIndex(self.density.findData(preferences.density))
        self.market_asset.setCurrentText(preferences.default_market_asset)
        self.market_period.setCurrentText(preferences.default_market_period)
        self.nq_interval.setCurrentText(preferences.default_nq_interval)
        if self.card_list.count():
            self.card_list.setCurrentRow(0)
        if self.section_list.count():
            self.section_list.setCurrentRow(0)

    @staticmethod
    def _move(widget: QtWidgets.QListWidget, delta: int) -> None:
        row = widget.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= widget.count():
            return
        item = widget.takeItem(row)
        widget.insertItem(target, item)
        widget.setCurrentRow(target)
        widget.setFocus()

    def _toggle_pin(self) -> None:
        item = self.card_list.currentItem()
        if item is None:
            return
        pinned = not bool(item.data(QtCore.Qt.UserRole + 1))
        item.setData(QtCore.Qt.UserRole + 1, pinned)
        item.setCheckState(QtCore.Qt.Checked)
        identifier = str(item.data(QtCore.Qt.UserRole))
        item.setText(("★ " if pinned else "") + _DASHBOARD_CARD_LABELS[identifier])

    def _apply(self) -> None:
        card_items = [self.card_list.item(index) for index in range(self.card_list.count())]
        section_items = [self.section_list.item(index) for index in range(self.section_list.count())]
        hidden_cards = frozenset(
            str(item.data(QtCore.Qt.UserRole))
            for item in card_items if item.checkState() != QtCore.Qt.Checked
        )
        pinned_cards = frozenset(
            str(item.data(QtCore.Qt.UserRole))
            for item in card_items
            if bool(item.data(QtCore.Qt.UserRole + 1))
            and item.checkState() == QtCore.Qt.Checked
        )
        preferences = replace(
            self._preferences,
            card_order=tuple(str(item.data(QtCore.Qt.UserRole)) for item in card_items),
            hidden_cards=hidden_cards,
            pinned_cards=pinned_cards,
            section_order=tuple(str(item.data(QtCore.Qt.UserRole)) for item in section_items),
            hidden_sections=frozenset(
                str(item.data(QtCore.Qt.UserRole))
                for item in section_items if item.checkState() != QtCore.Qt.Checked
            ),
            density=str(self.density.currentData()),
            default_market_asset=self.market_asset.currentText(),
            default_market_period=self.market_period.currentText(),
            default_nq_interval=self.nq_interval.currentText(),
        )
        self._preferences = preferences
        self.preferences_applied.emit(preferences)

    def _reset(self) -> None:
        self.load_preferences(DEFAULT_PREFERENCES)
        self.reset_requested.emit()


class DashboardPage(QtWidgets.QScrollArea):
    COMPACT_MARKET_CARD_HEIGHT = 112
    DETAIL_MARKET_CARD_HEIGHT = 112
    """Visual-acceptance layout; all numeric inputs remain typed and gated."""

    market_chart_requested = QtCore.Signal(str, str)
    TOP_METRICS = (
        ("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ"),
        ("NQ_FUTURES", "Nasdaq 100"), ("NASDAQ", "Nasdaq"),
        ("SP500", "S&P 500"), ("SOXX", "SOXX"),
        ("GOLD", "GOLD"), ("WTI", "WTI"), ("BITCOIN", "BITCOIN"),
        ("USD_KRW_60M", "USD/KRW"),
    )
    CHART_METRICS = {
        "KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ",
        "Nasdaq 100": "NDX", "Nasdaq 100 Futures": "NQ_FUTURES",
        "Nasdaq": "NASDAQ", "S&P 500": "SP500", "SOXX": "SOXX",
        "GOLD": "GOLD", "WTI": "WTI",
    }
    CHART_MARKETS = {
        "KOSPI": ExchangeMarket.KR,
        "KOSDAQ": ExchangeMarket.KR,
        "Nasdaq 100": ExchangeMarket.US,
        "Nasdaq 100 Futures": ExchangeMarket.US,
        "SOXX": ExchangeMarket.US, "Nasdaq": ExchangeMarket.US,
        "S&P 500": ExchangeMarket.US, "GOLD": ExchangeMarket.US,
        "WTI": ExchangeMarket.US,
    }
    reload_requested = QtCore.Signal()
    preferences_changed = QtCore.Signal(object)
    selection_preferences_changed = QtCore.Signal(object)
    preferences_reset_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setAccessibleName("시장 한눈에 보기")
        self._preferences = DEFAULT_PREFERENCES
        self._applying_preferences = False
        self._preferences_dialog: DashboardPreferencesDialog | None = None
        content = QtWidgets.QWidget()
        content.setMinimumWidth(0)
        self.setWidget(content)
        root = QtWidgets.QVBoxLayout(content)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(7)
        self._dashboard_root = root

        heading_widget = QtWidgets.QWidget()
        self.heading_widget = heading_widget
        heading_widget.setFixedHeight(52)
        heading = QtWidgets.QHBoxLayout(heading_widget)
        heading.setContentsMargins(0, 0, 0, 0)
        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(0)
        title = QtWidgets.QLabel("시장 한눈에 보기")
        title.setObjectName("pageTitle")
        self.subtitle = QtWidgets.QLabel("시장 · 수급 · 밸류에이션 · 파생 · 환율/금리")
        self.subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        heading.addLayout(title_box)
        heading.addStretch()
        self.preference_status = QtWidgets.QLabel("기본 화면 구성")
        self.preference_status.setObjectName("compactMeta")
        self.preference_status.setAccessibleName("Dashboard 로컬 화면 구성 상태")
        heading.addWidget(self.preference_status, alignment=QtCore.Qt.AlignTop)
        self.preferences_button = QtWidgets.QPushButton("화면 구성")
        self.preferences_button.setAccessibleName("Dashboard 카드와 섹션 화면 구성 열기")
        self.preferences_button.setToolTip(
            "표시·고정·순서·밀도·기본 기간만 로컬에 저장합니다."
        )
        heading.addWidget(self.preferences_button, alignment=QtCore.Qt.AlignTop)
        self.freshness = QtWidgets.QLabel("로컬 데이터")
        self.freshness.setObjectName("freshness")
        self.freshness.setToolTip("로컬 파일만 읽으며 freshness 검증 실패 시 값을 표시하지 않습니다.")
        self.freshness.setMaximumHeight(34)
        heading.addWidget(self.freshness, alignment=QtCore.Qt.AlignTop)
        self.current_observation_status = QtWidgets.QLabel("Retained observations: local only")
        self.current_observation_status.setObjectName("compactMeta")
        self.current_observation_status.setAccessibleName("Current-observation coverage status")
        self.current_observation_status.setToolTip(
            "Retained local current-display observations only. Source date is not a live refresh time; no provider request is made by the GUI."
        )
        heading.addWidget(self.current_observation_status, alignment=QtCore.Qt.AlignTop)
        self.current_observation_status.hide()
        root.addWidget(heading_widget)

        self.market_session_strip = QtWidgets.QFrame()
        self.market_session_strip.setObjectName("marketSessionStrip")
        self.market_session_strip.setFixedHeight(38)
        session_layout = QtWidgets.QHBoxLayout(self.market_session_strip)
        self.session_layout = session_layout
        session_layout.setContentsMargins(10, 0, 10, 0)
        session_layout.setSpacing(22)
        self.domestic_market_session = QtWidgets.QLabel()
        self.domestic_market_session.setObjectName("marketSessionText")
        self.us_market_session = QtWidgets.QLabel()
        self.us_market_session.setObjectName("marketSessionText")
        session_layout.addWidget(self.domestic_market_session)
        session_layout.addWidget(self.us_market_session)
        session_layout.addStretch()
        root.addWidget(self.market_session_strip)
        # Compatibility aliases retain layout/lifecycle references while the
        # former current-observation diagnostic cells are no longer visible UI.
        self.current_observation_strip = self.market_session_strip
        self.current_observation_strip_cells: list[QtWidgets.QLabel] = []
        self.current_observation_strip_text = self.domestic_market_session
        self._render_market_session_bar(pd.Timestamp.now(tz="UTC"))

        self.market_cards = {key: CompactMetricCard(label) for key, label in self.TOP_METRICS}
        for card in self.market_cards.values():
            # Startup local reads are asynchronous.  Distinguish that bounded
            # loading interval from a typed UNKNOWN result so an empty
            # sparkline does not look like a failed or stale dataset.
            card.body.setText("불러오는 중…")
            card.body.setAccessibleName(f"{card.title.text()} 로컬 데이터 불러오는 중")
            card.setMinimumWidth(0)
            card.setSizePolicy(
                QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed,
            )
            card.setFixedHeight(self.COMPACT_MARKET_CARD_HEIGHT)
            card.root_layout.setContentsMargins(7, 2, 7, 2)
            card.root_layout.setSpacing(0)
            card.stack_value_change = True
            card.body.setMinimumWidth(0)
            card.body.setWordWrap(True)
            card.body.setSizePolicy(
                QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed,
            )
            card.title.setMinimumWidth(0)
            card.title.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred,
            )
            card.title.setStyleSheet("font-size:10px;")
            card.title.setFixedHeight(14)
            card.header_layout.setStretch(0, 1)
            card.header_layout.setStretch(1, 0)
            # A dedicated compact row gives both the full title and its typed
            # source date enough width even when ten cards share 1280px.
            card.meta.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            card.meta.setMinimumWidth(0)
            card.meta.setMaximumWidth(16_777_215)
            card.meta.setStyleSheet("font-size:10px;")
            card.meta.show()
            card.badge.hide()
            # Ten market cards must remain readable in one desktop row.  Give
            # the value/change line the full card width and place the compact
            # session trace below it, matching the dense market-strip pattern
            # used by the reference UI.
            card.detail_layout.removeWidget(card.sparkline)
            card.root_layout.addWidget(card.sparkline)
            card.body.setStyleSheet("font-size:10px;")
            card.text_layout.setSpacing(0)
            card.body.setFixedHeight(card.body.fontMetrics().lineSpacing() * 3)
            card.sparkline.setMinimumWidth(0)
            card.sparkline.setFixedHeight(18)
        self.top_widget = QtWidgets.QWidget()
        top_strip = QtWidgets.QGridLayout(self.top_widget)
        top_strip.setContentsMargins(0, 0, 0, 0)
        top_strip.setSpacing(6)
        self.top_strip = top_strip
        for column, (key, _label) in enumerate(self.TOP_METRICS):
            top_strip.addWidget(self.market_cards[key], 0, column)
        root.addWidget(self.top_widget)

        body_widget = QtWidgets.QWidget()
        self.body_widget = body_widget
        # The compact current-observation strip is a reviewed Dashboard row.
        # Keep every existing card/chart visible at the 1600x900 composition
        # without creating a scroll-only tail below the panels.
        body_widget.setMinimumHeight(480)
        body = QtWidgets.QGridLayout(body_widget)
        self.body_layout = body
        body.setContentsMargins(0, 0, 0, 0)
        body.setHorizontalSpacing(8)
        body.setColumnStretch(0, 2)
        body.setColumnStretch(1, 1)
        root.addWidget(body_widget, 1)

        left = QtWidgets.QWidget()
        self.left_widget = left
        left.setMinimumWidth(0)
        left.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred,
        )
        left_layout = QtWidgets.QVBoxLayout(left)
        self.left_layout = left_layout
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)
        body.addWidget(left, 0, 0)

        kospi_panel = QtWidgets.QFrame()
        kospi_panel.setObjectName("panel")
        self.kospi_panel = kospi_panel
        kospi_panel.setMinimumHeight(335)
        kospi_layout = QtWidgets.QVBoxLayout(kospi_panel)
        kospi_layout.setContentsMargins(9, 7, 20, 7)
        kospi_layout.setSpacing(3)
        controls = QtWidgets.QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(2)
        self.market_chart_controls = controls
        header_controls = QtWidgets.QHBoxLayout()
        header_controls.setContentsMargins(0, 0, 0, 0)
        self.market_chart_header_controls = header_controls
        self.kospi_chart_title = QtWidgets.QLabel("KOSPI 차트 · 불러오는 중…")
        self.kospi_chart_title.setObjectName("sectionTitle")
        header_controls.addWidget(self.kospi_chart_title)
        header_controls.addStretch()
        market_asset_label = QtWidgets.QLabel("시장/지수")
        self.market_asset = QtWidgets.QComboBox()
        self.market_asset.addItems(list(self.CHART_METRICS))
        self.market_asset.setToolTip("표시할 로컬 시장/지수 차트를 선택합니다.")
        market_asset_label.setBuddy(self.market_asset)
        market_period_label = QtWidgets.QLabel("기간")
        self.market_period = QtWidgets.QComboBox()
        self.market_period.addItems(MARKET_PERIODS)
        self.market_period.setCurrentText("120D")
        self.market_period.setToolTip("로컬 차트의 표시 기간을 선택합니다.")
        market_period_label.setBuddy(self.market_period)
        self.reload_button = QtWidgets.QPushButton("로컬 새로고침")
        self.reload_button.setAccessibleName("로컬 Dashboard 새로고침")
        self.reload_button.setToolTip("로컬에 저장된 화면 데이터만 다시 읽습니다. 네트워크 호출은 하지 않습니다.")
        header_controls.addWidget(market_asset_label)
        header_controls.addWidget(self.market_asset)
        header_controls.addWidget(market_period_label)
        header_controls.addWidget(self.market_period)
        self.market_indicator_button = QtWidgets.QPushButton("보조지표")
        self.market_indicator_button.setCheckable(True)
        self.market_indicator_button.setAccessibleName("차트 보조지표 설정 펼치기")
        self.market_indicator_button.setToolTip("이동평균선과 보조지표 설정을 한 번에 펼칩니다.")
        self.market_indicator_panel = IndicatorControlPanel(allows_lower_panels=True)
        self.market_indicator_panel.apply(DEFAULT_PREFERENCES.dashboard_indicators)
        self.market_indicator_panel.settings_changed.connect(self._dashboard_indicator_changed)
        self.market_indicator_panel.reset_requested.connect(self._reset_dashboard_indicators)
        header_controls.addWidget(self.reload_button)
        header_controls.addWidget(self.market_indicator_button)
        controls.addLayout(header_controls)
        indicator_controls = QtWidgets.QHBoxLayout()
        indicator_controls.setContentsMargins(0, 0, 0, 0)
        indicator_controls.addWidget(self.market_indicator_panel)
        indicator_controls.addStretch()
        self.market_indicator_controls = indicator_controls
        controls.addLayout(indicator_controls)
        self.market_indicator_panel.hide()
        self.market_indicator_button.toggled.connect(
            self.market_indicator_panel.setVisible
        )
        kospi_layout.addLayout(controls)
        self._market_axis = SessionDateAxisItem(orientation="bottom")
        self.market_chart = pg.PlotWidget(axisItems={"bottom": self._market_axis})
        self.market_chart.setBackground("#ffffff")
        self.market_chart.setAccessibleName("시장 가격 차트")
        self.market_chart.showGrid(x=True, y=True, alpha=.12)
        self.market_chart.getAxis("left").setWidth(72)
        # Dashboard overlays share the accepted Index Graph presentation model:
        # price stays on its own scale while RSI and disparity retain truthful,
        # independently-labelled Y axes over the same session positions.
        self._market_rsi_overlay_axis = self.market_chart.getAxis("right")
        self._market_rsi_overlay_axis.setLabel(
            "RSI14 (0–100)", color=INDEX_CURVE_STYLES["rsi14"][1],
        )
        self._market_rsi_overlay_axis.setWidth(62)
        self._market_rsi_overlay_axis.hide()
        self._market_rsi_overlay_view = pg.ViewBox(enableMenu=False)
        self._market_rsi_overlay_view.setObjectName("dashboardRsi14OverlayView")
        self._market_rsi_overlay_view.setMouseEnabled(x=False, y=False)
        self._market_rsi_overlay_view.setZValue(-90)
        self.market_chart.scene().addItem(self._market_rsi_overlay_view)
        self._market_rsi_overlay_axis.linkToView(self._market_rsi_overlay_view)
        self._market_rsi_overlay_view.setXLink(self.market_chart.getViewBox())

        self._market_disparity_overlay_axis = pg.AxisItem(orientation="right")
        self._market_disparity_overlay_axis.setLabel(
            "괴리60 (pp)", color=INDEX_CURVE_STYLES["disparity60"][1],
        )
        self._market_disparity_overlay_axis.setWidth(68)
        self._market_disparity_overlay_axis.setToolTip("0pp는 MA60 대비 100%와 같습니다.")
        self._market_disparity_overlay_axis.hide()
        self.market_chart.plotItem.layout.addItem(
            self._market_disparity_overlay_axis, 2, 3,
        )
        self._market_disparity_overlay_view = pg.ViewBox(enableMenu=False)
        self._market_disparity_overlay_view.setObjectName(
            "dashboardDisparity60OverlayView",
        )
        self._market_disparity_overlay_view.setMouseEnabled(x=False, y=False)
        self._market_disparity_overlay_view.setZValue(-89)
        self.market_chart.scene().addItem(self._market_disparity_overlay_view)
        self._market_disparity_overlay_axis.linkToView(
            self._market_disparity_overlay_view,
        )
        self._market_disparity_overlay_view.setXLink(
            self.market_chart.getViewBox(),
        )
        self._market_overlay_items: dict[str, pg.PlotDataItem] = {}
        self._market_overlay_guides: dict[str, tuple[pg.InfiniteLine, ...]] = {}
        self.market_chart.getViewBox().sigResized.connect(
            self._sync_market_indicator_overlay_geometry,
        )
        kospi_layout.addWidget(self.market_chart, 1)
        self.market_indicator_legend = QtWidgets.QLabel()
        self.market_indicator_legend.setObjectName("indexLegend")
        self.market_indicator_legend.setAccessibleName("시장 차트 표시 지표 범례")
        self.market_indicator_legend.setWordWrap(True)
        self.market_indicator_legend.hide()
        kospi_layout.addWidget(self.market_indicator_legend)
        self._market_volume_axis = SessionDateAxisItem(orientation="bottom")
        self._market_volume_value_axis = VolumeAxisItem(orientation="left")
        self.market_volume = pg.PlotWidget(axisItems={
            "bottom": self._market_volume_axis,
            "left": self._market_volume_value_axis,
        })
        self.market_volume.setBackground("#ffffff")
        self.market_volume.setXLink(self.market_chart)
        self.market_volume.setFixedHeight(48)
        self.market_volume.setAccessibleName("시장 거래량 차트")
        kospi_layout.addWidget(self.market_volume)
        self._market_indicator_axis = SessionDateAxisItem(orientation="bottom")
        self.market_indicator = pg.PlotWidget(axisItems={"bottom": self._market_indicator_axis})
        self.market_indicator.setBackground("#ffffff")
        self.market_indicator.setXLink(self.market_chart)
        self.market_indicator.setFixedHeight(54)
        self.market_indicator.getAxis("left").setWidth(72)
        self.market_indicator.setAccessibleName("RSI14 및 60일 괴리율 차트")
        self.market_indicator.hide()
        kospi_layout.addWidget(self.market_indicator)
        self.market_chart_status = QtWidgets.QLabel("로컬 차트 불러오는 중…")
        self.market_chart_status.setObjectName("chartStatus")
        kospi_layout.addWidget(self.market_chart_status)
        self.kospi200_breadth = MetricCard("KOSPI200 구성종목 등락")
        breadth_layout = self.kospi200_breadth.layout()
        breadth_layout.setContentsMargins(9, 4, 9, 4)
        breadth_layout.setSpacing(2)
        breadth_metrics = self.kospi200_breadth.body.fontMetrics()
        breadth_title_height = breadth_metrics.height()
        breadth_body_height = max(36, breadth_metrics.boundingRect(
            QtCore.QRect(0, 0, 2000, 1000),
            QtCore.Qt.TextWordWrap,
            "첫째 줄\n둘째 줄",
        ).height())
        self.kospi200_breadth.title.setFixedHeight(breadth_title_height)
        self.kospi200_breadth.body.setFixedHeight(breadth_body_height)
        self.kospi200_breadth.setFixedHeight(90)
        self.toss_short_watchlist = MetricCard("Toss 종목별 EOD · KRX-only")
        toss_layout = self.toss_short_watchlist.layout()
        toss_layout.setContentsMargins(9, 4, 9, 4)
        toss_layout.setSpacing(2)
        self.toss_short_watchlist.title.setFixedHeight(breadth_title_height)
        self.toss_short_watchlist.body.setFixedHeight(max(48, breadth_body_height))
        self.toss_short_watchlist.setFixedHeight(90)
        provider_row = QtWidgets.QHBoxLayout()
        provider_row.setContentsMargins(0, 0, 0, 0)
        provider_row.setSpacing(7)
        provider_row.addWidget(self.kospi200_breadth, 1)
        provider_row.addWidget(self.toss_short_watchlist, 1)
        kospi_layout.addLayout(provider_row)
        left_layout.addWidget(kospi_panel)

        nq_panel = QtWidgets.QFrame()
        nq_panel.setObjectName("panel")
        self.nq_panel = nq_panel
        nq_panel.setMinimumHeight(195)
        nq_layout = QtWidgets.QVBoxLayout(nq_panel)
        nq_layout.setContentsMargins(11, 8, 11, 8)
        nq_header = QtWidgets.QHBoxLayout()
        self.nq_chart_title = QtWidgets.QLabel("나스닥100 연속선물 · NQ=F")
        self.nq_chart_title.setObjectName("sectionTitle")
        self.nq_interval = QtWidgets.QComboBox()
        self.nq_interval.addItems(["일봉", "주봉", "월봉"])
        self.nq_interval.setAccessibleName("나스닥100 선물 캔들 주기")
        nq_header.addWidget(self.nq_chart_title)
        nq_header.addStretch()
        nq_header.addWidget(self.nq_interval)
        self._nq_axis = SessionDateAxisItem(orientation="bottom")
        self.nq_chart = pg.PlotWidget(axisItems={"bottom": self._nq_axis})
        self.nq_chart.setBackground("#ffffff")
        self.nq_chart.setAccessibleName("나스닥100 연속선물 캔들 차트")
        self.nq_chart.showGrid(x=True, y=True, alpha=.12)
        self.nq_state = QtWidgets.QLabel(FRESHNESS_COPY["UNKNOWN"])
        self.nq_state.setObjectName("unavailableState")
        self.nq_detail = QtWidgets.QLabel("연속선물 · 설명용")
        self.nq_detail.setObjectName("compactMeta")
        self.nq_detail.setWordWrap(True)
        self.nq_detail.setToolTip(
            "개별 만기, 공식 결제, 미결제약정 또는 백테스트 입력을 대체하지 않습니다."
        )
        nq_layout.addLayout(nq_header)
        nq_layout.addWidget(self.nq_chart, 1)
        nq_layout.addWidget(self.nq_state)
        nq_layout.addWidget(self.nq_detail)
        left_layout.addWidget(nq_panel)
        nq_panel.hide()

        side = QtWidgets.QWidget()
        self.side_widget = side
        side.setMinimumWidth(0)
        side.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred,
        )
        side_layout = QtWidgets.QVBoxLayout(side)
        self.side_layout = side_layout
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(7)
        oscillator = QtWidgets.QFrame()
        oscillator.setObjectName("panel")
        oscillator_layout = QtWidgets.QVBoxLayout(oscillator)
        oscillator_layout.setContentsMargins(10, 8, 10, 8)
        oscillator_layout.setSpacing(4)
        oscillator_title = QtWidgets.QLabel("시장 온도")
        oscillator_title.setObjectName("sectionTitle")
        oscillator_layout.addWidget(oscillator_title)
        self.temperature_coverage = QtWidgets.QLabel("근거 0/3 · 지표 확인 중")
        self.temperature_coverage.setObjectName("compactMeta")
        oscillator_layout.addWidget(self.temperature_coverage)
        self.gauges = {
            "RSI14": GaugeRow(
                "모멘텀 · KOSPI RSI14",
                thresholds=(30, 70),
                threshold_tooltip="RSI14 참고선: 30 과매도, 70 과매수 · 투자 판단 기준이 아님",
            ),
            "DISPARITY60": SignedGaugeRow(
                "추세 · KOSPI MA60 이격",
                thresholds=(0,), minimum=-20, maximum=20,
                threshold_tooltip="0은 60일 이동평균선과 일치하는 중앙 기준선입니다.",
            ),
            "VIX": GaugeRow("공포 · VIX 250D 순위"),
            "VKOSPI": GaugeRow("공포 · VKOSPI 250D 순위"),
        }
        self._temperature_values: dict[str, float] = {}
        for gauge in self.gauges.values():
            oscillator_layout.addWidget(gauge, 1)
        valuation_panel = QtWidgets.QFrame()
        valuation_panel.setObjectName("panel")
        valuation_panel.setAccessibleName("KOSPI KOSDAQ 시장 밸류에이션 및 실적 축")
        valuation_panel.setFixedHeight(246)
        valuation_layout = QtWidgets.QVBoxLayout(valuation_panel)
        valuation_layout.setContentsMargins(10, 8, 10, 8)
        valuation_layout.setSpacing(4)
        valuation_title = QtWidgets.QLabel("시장 밸류·실적 맥락 · KRX 완료 일별")
        valuation_title.setObjectName("sectionTitle")
        valuation_title.setToolTip(
            "KRX 1001/2001 공급자 정의 가중 PER·PBR의 설명용 역사 비교입니다. "
            "Forward/TTM 기간은 확인되지 않았으며 예측·매매 신호가 아닙니다."
        )
        valuation_layout.addWidget(valuation_title)
        valuation_basis = QtWidgets.QLabel(
            "현재 KRX 가중 PER/PBR · 5년/10년 역사 위치 · 선행 비율로 사용 금지"
        )
        valuation_basis.setObjectName("compactMeta")
        valuation_basis.setWordWrap(True)
        valuation_layout.addWidget(valuation_basis)
        self.market_valuation_labels = {
            market: QtWidgets.QLabel(f"{market} · 현재 표시 불가")
            for market in ("KOSPI", "KOSDAQ")
        }
        self.market_valuation_charts = {
            market: ValuationPercentileChart(market)
            for market in ("KOSPI", "KOSDAQ")
        }
        for market, label in self.market_valuation_labels.items():
            label.setObjectName("compactMeta")
            label.setWordWrap(True)
            label.setMinimumHeight(48)
            label.setAccessibleName(f"{market} 시장 PER PBR 역사 비교")
            valuation_layout.addWidget(label)
            valuation_layout.addWidget(self.market_valuation_charts[market])
        self.market_valuation_regime_gate = QtWidgets.QLabel(
            "시장 국면 근거 0/3 · Forward EPS·Revision·ROE 미지원 · "
            "고점·저점 판정 보류"
        )
        self.market_valuation_regime_gate.setObjectName("unavailableState")
        self.market_valuation_regime_gate.setAccessibleName("시장 국면 판정 보류")
        self.market_valuation_regime_gate.setToolTip(
            "Valuation과 Earnings Momentum을 독립 축으로 유지합니다. "
            "Forward EPS/BPS/ROE의 PIT-safe vintage와 이익수정 breadth가 없으므로 "
            "기대이익성장, PBR/ROE 잔차, Forward earnings-yield gap, "
            "multiple expansion 및 시장 고점·저점 국면을 계산하지 않습니다."
        )
        valuation_layout.addWidget(self.market_valuation_regime_gate)
        self._valuation_axis_available = False
        self._earnings_axis_available = False
        valuation_layout.addStretch()
        self.market_valuation_panel = valuation_panel
        self.momentum_summary = QtWidgets.QLabel("단기 모멘텀 · 중기 추세를 확인할 수 없습니다")
        self.momentum_summary.setObjectName("momentumSummary")
        self.momentum_summary.setWordWrap(True)
        self.momentum_summary.setToolTip(
            "과매도 강도(설명용): RSI14 4점 + MA60 하방 이격 3점 + "
            "VKOSPI 250거래일 백분위 3점. VKOSPI가 없을 때만 VIX를 사용합니다. "
            "결측 축은 0점으로 간주하지 않고 산출을 보류합니다."
        )
        oscillator_layout.addWidget(self.momentum_summary)
        oscillator_layout.addWidget(self.market_valuation_panel)
        note = QtWidgets.QLabel("지표별 상태 · 투자판단 아님")
        note.setObjectName("compactMeta")
        note.setWordWrap(True)
        note.setToolTip("RSI14, MA60 이격도, VIX, VKOSPI의 관측 상태이며 투자판단을 제공하지 않습니다.")
        note.hide()
        self.oscillator_note = note
        oscillator_layout.addWidget(note)
        self.oscillator_panel = oscillator
        self.oscillator_panel.setAccessibleName(
            "시장 온도 · 기술 변동성 · KRX 밸류에이션 · 실적 가용성"
        )
        self.oscillator_panel.setMinimumHeight(635)
        side_layout.addWidget(oscillator)
        self.market_flow_panel = MarketInvestorFlowPanel(self.left_widget)
        self.market_context_tabs = QtWidgets.QTabWidget(self.left_widget)
        self.market_context_tabs.setObjectName("marketContextTabs")
        self.market_context_tabs.setAccessibleName("시장 수급")
        self.market_context_tabs.setFixedHeight(264)
        self.market_context_tabs.addTab(self.market_flow_panel, "시장 수급")
        self.market_context_tabs.setCurrentWidget(self.market_flow_panel)
        left_layout.addWidget(self.market_context_tabs)
        rates_panel = QtWidgets.QFrame()
        rates_panel.setObjectName("panel")
        rates_layout = QtWidgets.QVBoxLayout(rates_panel)
        rates_layout.setContentsMargins(9, 7, 9, 7)
        rates_layout.setSpacing(2)
        rates_layout.setAlignment(QtCore.Qt.AlignTop)
        rates_title = QtWidgets.QLabel("환율 · 금리")
        rates_title.setObjectName("sectionTitle")
        rates_layout.addWidget(rates_title)
        self.rate_rows = {
            key: RateRow(label) for key, label in (
                ("USD_KRW", "USD/KRW"), ("USD_JPY", "USD/JPY"),
                ("KR_TREASURY", "국고채"), ("UST2", "2Y"),
                ("UST5_QUOTE", "5Y"),
                ("UST10", "10Y"), ("UST30", "30Y"),
                ("UST10_2_SPREAD", "10Y−2Y"),
            )
        }
        self.rate_groups = {
            "FX": RateGroup(
                "환율", (self.rate_rows["USD_KRW"], self.rate_rows["USD_JPY"])
            ),
            "KR": RateGroup("한국 국채", (self.rate_rows["KR_TREASURY"],)),
            "US": RateGroup(
                "미국 국채",
                (self.rate_rows["UST2"], self.rate_rows["UST10"], self.rate_rows["UST30"]),
            ),
            "SPREAD": RateGroup("금리차", (self.rate_rows["UST10_2_SPREAD"],)),
        }
        for group in self.rate_groups.values():
            rates_layout.addWidget(group)
        # Keep the official Korean-yield lane visible even while its BOK
        # publication/finality gate is numeric-free.  Toss candles and quotes
        # are never substituted for an official yield.
        self.rate_groups["SPREAD"].hide()
        rates_layout.addStretch(1)
        self.rates_panel = rates_panel
        side_layout.addWidget(rates_panel, 1)
        self.account_placeholder = AccountOverviewPanel()
        side_layout.addWidget(self.account_placeholder)
        body.addWidget(side, 0, 1)

        derivatives = QtWidgets.QFrame()
        derivatives.setObjectName("panel")
        derivatives.setFixedHeight(112)
        derivative_layout = QtWidgets.QVBoxLayout(derivatives)
        self.derivative_layout = derivative_layout
        derivative_layout.setContentsMargins(9, 6, 9, 6)
        derivative_layout.setSpacing(3)
        derivative_title = QtWidgets.QLabel("파생상품 요약")
        derivative_title.setObjectName("sectionTitle")
        derivative_layout.addWidget(derivative_title)
        derivative_row = QtWidgets.QGridLayout()
        derivative_row.setSpacing(6)
        self.derivative_grid = derivative_row
        self.derivative_cards = {
            key: CompactMetricCard(label) for key, label in (
                ("KOSPI200_BASIS", "KOSPI200 선물 Basis"),
                ("VOLUME_PCR", "KOSPI200 옵션 거래량 P/C"),
                ("OI_PCR", "KOSPI200 옵션 OI P/C"),
                ("VKOSPI", "VKOSPI"), ("WALL", "Call / Put 최대 OI"),
                ("SHORT_SELLING_VALUE", "공매도 거래대금"),
                ("VIX_FUTURES", "VIX 선물"),
                ("US_OPTION_PCR", "미국 옵션 P/C"),
            )
        }
        for column, card in enumerate(self.derivative_cards.values()):
            card.setMinimumWidth(0)
            card.setSizePolicy(
                QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed,
            )
            card.setFixedHeight(70)
            card.title.setMinimumWidth(0)
            card.title.setWordWrap(True)
            card.title.setSizePolicy(
                QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred,
            )
            card.body.setMinimumWidth(0)
            card.body.setWordWrap(True)
            card.body.setSizePolicy(
                QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred,
            )
            card.badge.hide()
            derivative_row.addWidget(card, 0, column)
        # Identity/licence blockers are operator diagnostics, not daily-use
        # headlines. Their machine-readable decisions live in Data Status.
        self.derivative_cards["VIX_FUTURES"].hide()
        self.derivative_cards["US_OPTION_PCR"].hide()
        derivative_layout.addLayout(derivative_row)
        root.addWidget(derivatives)
        self.derivatives_panel = derivatives

        self.market_asset.setAccessibleName("시장 가격 차트 자산")
        self.market_period.setAccessibleName("시장 가격 차트 기간")
        self.market_asset.currentTextChanged.connect(self._request_market_chart)
        self.market_period.currentTextChanged.connect(self._request_market_chart)
        self.nq_interval.currentTextChanged.connect(self._rerender_nq_chart)
        self.market_asset.currentTextChanged.connect(self._selection_preference_changed)
        self.market_period.currentTextChanged.connect(self._selection_preference_changed)
        self.nq_interval.currentTextChanged.connect(self._selection_preference_changed)
        self.reload_button.clicked.connect(self._request_local_reload)
        self.preferences_button.clicked.connect(self._open_preferences_dialog)
        self._market_chart_request_active = False
        self._local_reload_active = False
        self._market_frame = pd.DataFrame()
        self._market_session_mapping: SessionAxisMapping | None = None
        self._nq_session_mapping: SessionAxisMapping | None = None
        self._metrics: dict[str, DashboardMetricView] = {}
        self._series: dict[str, DashboardSeriesView] = {}
        self._market_metadata: dict[str, dict] = {}
        self._market_frame_issue: str | None = None
        self.index_cards = {}
        self._crosshair = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#7187aa", style=QtCore.Qt.DashLine))
        self.market_chart.addItem(self._crosshair, ignoreBounds=True)
        self._crosshair.hide()
        self._hover_proxy = pg.SignalProxy(self.market_chart.scene().sigMouseMoved, rateLimit=30, slot=self._mouse_moved)
        self.apply_preferences(DEFAULT_PREFERENCES)
        QtCore.QTimer.singleShot(0, self._apply_dashboard_density)

    def _open_preferences_dialog(self) -> None:
        dialog = self._preferences_dialog
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return
        dialog = DashboardPreferencesDialog(self._preferences, self)
        dialog.setStyleSheet(self.window().styleSheet())
        dialog.preferences_applied.connect(self.preferences_changed)
        dialog.reset_requested.connect(self.preferences_reset_requested)
        dialog.finished.connect(lambda _result: setattr(self, "_preferences_dialog", None))
        self._preferences_dialog = dialog
        dialog.show()

    def _selection_preference_changed(self, _value: str = "") -> None:
        if self._applying_preferences:
            return
        updated = replace(
            self._preferences,
            default_market_asset=self.market_asset.currentText(),
            default_market_period=self.market_period.currentText(),
            default_nq_interval=self.nq_interval.currentText(),
        )
        self._preferences = updated
        self.selection_preferences_changed.emit(updated)

    def _dashboard_indicator_changed(self, settings: ChartIndicatorPreferences) -> None:
        if self._applying_preferences:
            return
        updated = self._preferences.with_indicators("DASHBOARD", settings)
        self._preferences = updated
        self.selection_preferences_changed.emit(updated)
        self._rerender_market_chart()

    def _reset_dashboard_indicators(self) -> None:
        settings = DEFAULT_PREFERENCES.dashboard_indicators
        self.market_indicator_panel.apply(settings)
        self._dashboard_indicator_changed(settings)

    def set_preferences_status(self, reason: str) -> None:
        text = {
            "LOADED": "저장 화면 구성",
            "SAVED": "화면 구성 저장됨",
            "RESET": "기본 화면 구성",
            "DEFAULT_MISSING": "기본 화면 구성 · 저장 전",
            "DEFAULT_CORRUPT": "손상 설정 무시 · 기본 구성",
            "RECOVERED_LAST_VALID": "마지막 정상 구성 복구",
            "MIGRATED_V1": "이전 설정 변환 완료",
            "MIGRATED_V1_MEMORY_ONLY": "이전 설정 임시 적용 · 저장 확인 필요",
            "WRITE_FAILED": "화면 구성 저장 실패 · 기존 설정 보존",
        }.get(reason, "로컬 화면 구성")
        self.preference_status.setText(text)
        self.preference_status.setToolTip(
            f"layout_status={reason}\n시장 데이터·계좌 값·watchlist와 별도"
        )

    def render_refresh_status(self, projection: RefreshStatusProjection) -> None:
        """Render one compact lifecycle summary without changing data values."""
        labels = {
            "SUCCEEDED": "갱신 정상",
            "IN_PROGRESS": "갱신 중",
            "PARTIAL_FAILURE": "일부 갱신 확인",
            "FAILED": "갱신 실패",
            "UNKNOWN": "갱신 상태 확인",
        }
        current = projection.surface("DASHBOARD_CURRENT")
        suffix = (
            " · 30분 로컬 확인"
            if current.cadence_seconds == 1800 else ""
        )
        self.freshness.setText(labels.get(projection.overall_state, "갱신 상태 확인") + suffix)
        self.freshness.setAccessibleName("Dashboard 통합 갱신 상태")
        self.freshness.setToolTip(_refresh_projection_tooltip(projection))

    def apply_preferences(self, preferences: DashboardPreferences) -> None:
        """Apply presentation state without reading data or emitting reloads."""
        self._preferences = preferences
        self._applying_preferences = True
        blockers = [
            QtCore.QSignalBlocker(self.market_asset),
            QtCore.QSignalBlocker(self.market_period),
            QtCore.QSignalBlocker(self.nq_interval),
        ]
        try:
            self.market_asset.setCurrentText(preferences.default_market_asset)
            self.market_period.setCurrentText(preferences.default_market_period)
            self.nq_interval.setCurrentText(preferences.default_nq_interval)
            self.market_indicator_panel.apply(preferences.dashboard_indicators)
        finally:
            del blockers
            self._applying_preferences = False

        for card in self.market_cards.values():
            self.top_strip.removeWidget(card)
        visible_cards = []
        for column, identifier in enumerate(preferences.effective_card_order):
            card = self.market_cards[identifier]
            pinned = identifier in preferences.pinned_cards
            card.setProperty("pinned", pinned)
            card.style().unpolish(card)
            card.style().polish(card)
            card.setVisible(identifier not in preferences.hidden_cards)
            self.top_strip.addWidget(card, 0, column)
            if identifier not in preferences.hidden_cards:
                visible_cards.append(identifier)
        detail = preferences.density == "DETAIL"
        card_height = (
            self.DETAIL_MARKET_CARD_HEIGHT
            if detail else self.COMPACT_MARKET_CARD_HEIGHT
        )
        for identifier, card in self.market_cards.items():
            card.setFixedHeight(card_height)
            card.comparison.setVisible(detail)
            if identifier == "VIX":
                card.meta.setVisible(detail)
        self.top_widget.setFixedHeight(card_height if visible_cards else 0)
        self.top_widget.setVisible(bool(visible_cards))
        self._visible_market_card_ids = tuple(visible_cards)
        hidden_labels = [
            _DASHBOARD_CARD_LABELS[item]
            for item in preferences.card_order if item in preferences.hidden_cards
        ]
        hidden_text = f"숨김 {len(hidden_labels)}개" if hidden_labels else "모든 시장 카드 표시"
        self.preference_status.setAccessibleDescription(
            hidden_text + " · 전체 상태와 확인 필요 항목은 Data Status에서 항상 확인 가능"
        )
        if hidden_labels:
            self.preference_status.setToolTip(
                self.preference_status.toolTip()
                + "\nhidden_cards=" + ", ".join(hidden_labels)
                + "\n전체 실패/사용자 확인 알림은 상단 상태와 Data Status에서 유지"
            )

        section_widgets = {
            "KOSPI_CHART": self.kospi_panel,
            "NQ_CHART": self.nq_panel,
            "MARKET_TEMPERATURE": self.oscillator_panel,
            "MARKET_FLOW": self.market_context_tabs,
            "FX_RATES": self.rates_panel,
            "ACCOUNT_SUMMARY": self.account_placeholder,
            "DERIVATIVES": self.derivatives_panel,
        }
        for widget in section_widgets.values():
            widget.setVisible(True)
        for widget in (self.kospi_panel, self.nq_panel, self.market_context_tabs):
            self.left_layout.removeWidget(widget)
        for widget in (
            self.oscillator_panel, self.market_context_tabs,
            self.rates_panel, self.account_placeholder,
        ):
            self.side_layout.removeWidget(widget)
        left_ids = {"KOSPI_CHART", "MARKET_FLOW"}
        side_ids = {"MARKET_TEMPERATURE", "FX_RATES", "ACCOUNT_SUMMARY"}
        for identifier in preferences.section_order:
            widget = section_widgets[identifier]
            visible = (
                identifier not in preferences.hidden_sections
                and identifier != "NQ_CHART"
            )
            widget.setVisible(visible)
            if identifier in side_ids:
                self.side_layout.addWidget(widget)
        # The retired NQ section may still exist in a persisted schema.  Keep
        # the useful market-context surface directly below the primary chart
        # regardless of an older user-defined section order.
        for identifier in ("KOSPI_CHART", "MARKET_FLOW"):
            widget = section_widgets[identifier]
            if not widget.isHidden():
                self.left_layout.addWidget(widget)
        left_visible = any(item not in preferences.hidden_sections for item in left_ids)
        side_visible = any(item not in preferences.hidden_sections for item in side_ids)
        self.left_widget.setVisible(left_visible)
        self.side_widget.setVisible(side_visible)
        self.body_layout.removeWidget(self.left_widget)
        self.body_layout.removeWidget(self.side_widget)
        if left_visible and side_visible:
            self.body_layout.addWidget(self.left_widget, 0, 0)
            self.body_layout.addWidget(self.side_widget, 0, 1)
            self.body_layout.setColumnStretch(0, 2)
            self.body_layout.setColumnStretch(1, 1)
        elif left_visible:
            self.body_layout.addWidget(self.left_widget, 0, 0, 1, 2)
            self.body_layout.setColumnStretch(0, 1)
            self.body_layout.setColumnStretch(1, 0)
        elif side_visible:
            self.body_layout.addWidget(self.side_widget, 0, 0, 1, 2)
            self.body_layout.setColumnStretch(0, 1)
            self.body_layout.setColumnStretch(1, 0)
        self.body_widget.setVisible(left_visible or side_visible)
        self.derivatives_panel.setVisible("DERIVATIVES" not in preferences.hidden_sections)
        self._apply_dashboard_density()

    def _request_market_chart(self, _=None) -> None:
        """Emit one synchronous local chart read and reject re-entrant requests."""
        if self._market_chart_request_active:
            return
        self._market_chart_request_active = True
        try:
            self.market_chart_requested.emit(
                self.CHART_METRICS[self.market_asset.currentText()],
                self.market_period.currentText(),
            )
        finally:
            self._market_chart_request_active = False

    def _request_local_reload(self) -> None:
        """Prevent a nested refresh from starting overlapping local reads."""
        if self._local_reload_active:
            return
        self._local_reload_active = True
        self.reload_button.setEnabled(False)
        try:
            self.reload_requested.emit()
        finally:
            self.reload_button.setEnabled(True)
            self._local_reload_active = False

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_dashboard_density()

    def _apply_dashboard_density(self) -> None:
        """Reflow cards and columns at desktop, compact, and narrow widths."""
        # QScrollArea can report its previous viewport width during a resize
        # event. The outer widget width is already final for the event and keeps
        # breakpoint selection deterministic.
        viewport_width = max(self.viewport().width(), self.width() - 20, 360)
        viewport_height = max(self.viewport().height(), 540)

        # Ratios are selected from logical Qt pixels, so Windows display scaling
        # (125%/150%) naturally enters the compact profiles instead of forcing
        # the 1600px composition into a smaller physical screen.
        layout_width = max(viewport_width, self.width())
        if layout_width >= 1280:
            market_columns, derivative_columns = 10, 6
            left_stretch, side_stretch = 2, 1
        elif viewport_width >= 1180:
            market_columns, derivative_columns = 5, 3
            left_stretch, side_stretch = 3, 2
        elif viewport_width >= 900:
            market_columns, derivative_columns = 4, 3
            left_stretch, side_stretch = 1, 1
        else:
            market_columns, derivative_columns = 2, 2
            left_stretch, side_stretch = 1, 1

        narrow = viewport_width < 900
        self.subtitle.setVisible(not narrow)
        self.preference_status.setVisible(viewport_width >= 1180)
        self.session_layout.setDirection(
            QtWidgets.QBoxLayout.TopToBottom
            if narrow else QtWidgets.QBoxLayout.LeftToRight
        )
        self.market_session_strip.setFixedHeight(58 if narrow else 38)

        visible_market_ids = getattr(self, "_visible_market_card_ids", ())
        for card in self.market_cards.values():
            self.top_strip.removeWidget(card)
        for index, identifier in enumerate(visible_market_ids):
            self.top_strip.addWidget(
                self.market_cards[identifier], index // market_columns,
                index % market_columns,
            )
        market_rows = (
            (len(visible_market_ids) + market_columns - 1) // market_columns
            if visible_market_ids else 0
        )
        preferences = getattr(self, "_preferences", DEFAULT_PREFERENCES)
        card_height = (
            self.DETAIL_MARKET_CARD_HEIGHT
            if preferences.density == "DETAIL"
            else self.COMPACT_MARKET_CARD_HEIGHT
        )
        top_height = market_rows * card_height + max(market_rows - 1, 0) * 6
        self.top_widget.setFixedHeight(top_height)

        visible_derivatives = [
            card for card in self.derivative_cards.values() if not card.isHidden()
        ]
        for card in self.derivative_cards.values():
            self.derivative_grid.removeWidget(card)
        for index, card in enumerate(visible_derivatives):
            self.derivative_grid.addWidget(
                card, index // derivative_columns, index % derivative_columns,
            )
        derivative_rows = (
            (len(visible_derivatives) + derivative_columns - 1) // derivative_columns
            if visible_derivatives else 0
        )
        derivative_height = (
            34 + derivative_rows * 70 + max(derivative_rows - 1, 0) * 6
            if not self.derivatives_panel.isHidden() else 0
        )
        self.derivatives_panel.setFixedHeight(derivative_height)

        left_visible = not self.left_widget.isHidden()
        side_visible = not self.side_widget.isHidden()
        self.body_layout.removeWidget(self.left_widget)
        self.body_layout.removeWidget(self.side_widget)
        stacked = viewport_width < 1180 and left_visible and side_visible
        if stacked:
            self.body_layout.addWidget(self.left_widget, 0, 0, 1, 2)
            self.body_layout.addWidget(self.side_widget, 1, 0, 1, 2)
            self.body_layout.setColumnStretch(0, 1)
            self.body_layout.setColumnStretch(1, 0)
        elif left_visible and side_visible:
            self.body_layout.addWidget(self.left_widget, 0, 0)
            self.body_layout.addWidget(self.side_widget, 0, 1)
            self.body_layout.setColumnStretch(0, left_stretch)
            self.body_layout.setColumnStretch(1, side_stretch)
        elif left_visible:
            self.body_layout.addWidget(self.left_widget, 0, 0, 1, 2)
        elif side_visible:
            self.body_layout.addWidget(self.side_widget, 0, 0, 1, 2)

        tape_height = self.top_widget.height() if not self.top_widget.isHidden() else 0
        strip_height = (
            self.current_observation_strip.height()
            if not self.current_observation_strip.isHidden()
            else 0
        )
        fixed_height = 52 + strip_height + tape_height + derivative_height + 48
        body_visible = not self.body_widget.isHidden()
        account_height = self.account_placeholder.height()
        temperature_height = 635
        visible_rate_groups = sum(
            not group.isHidden() for group in self.rate_groups.values()
        )
        rates_height = 34 + visible_rate_groups * 43
        side_heights = [
            height for widget, height in (
                (self.oscillator_panel, temperature_height),
                (self.rates_panel, rates_height),
                (self.account_placeholder, account_height),
            ) if not widget.isHidden()
        ]
        side_required_height = sum(side_heights) + max(len(side_heights) - 1, 0) * 7
        context_height = (
            self.market_context_tabs.height()
            if not self.market_context_tabs.isHidden() else 0
        )
        left_required_height = 470 + context_height + (7 if context_height else 0)
        if stacked and body_visible:
            body_height = max(
                left_required_height + side_required_height + 7,
                viewport_height - fixed_height,
            )
        else:
            body_height = (
                max(left_required_height, side_required_height, viewport_height - fixed_height)
                if body_visible else 0
            )
        self.body_widget.setFixedHeight(body_height)
        if not body_visible:
            return

        left_row_height = left_required_height if stacked else body_height
        available_height = max(
            470,
            left_row_height - context_height - (7 if context_height else 0),
        )
        kospi_visible = not self.kospi_panel.isHidden()
        nq_visible = not self.nq_panel.isHidden()
        if kospi_visible and nq_visible:
            kospi_height = max(470, int(available_height * 0.67))
            nq_height = available_height - kospi_height
            if nq_height < 220:
                nq_height = 220
                kospi_height = max(470, available_height - nq_height)
            self.kospi_panel.setFixedHeight(kospi_height)
            self.nq_panel.setFixedHeight(nq_height)
        elif kospi_visible:
            self.kospi_panel.setFixedHeight(available_height)
        elif nq_visible:
            self.nq_panel.setFixedHeight(available_height)

        if not self.oscillator_panel.isHidden():
            self.oscillator_panel.setFixedHeight(temperature_height)
        if not self.account_placeholder.isHidden():
            self.account_placeholder.setFixedHeight(account_height)
        if not self.rates_panel.isHidden():
            self.rates_panel.setFixedHeight(rates_height)

    @staticmethod
    def _state_message(metric: DashboardMetricView | None) -> str:
        return _display_message(metric)

    def _render_vix_sources(self, view: VIXSourceView | None) -> None:
        """Compose two gated VIX identities without merging or substituting them."""
        if "VIX" not in self.market_cards:
            return
        card = self.market_cards["VIX"]
        card.title.setText("VIX · FRED 일별")
        card.setAccessibleName("VIX FRED 완료 일별 및 Yahoo 지연 15분봉")
        card.title.setFixedHeight(16)
        card.badge.setFixedHeight(20)
        card.body.setFixedHeight(18)
        card.meta.setWordWrap(True)
        card.meta.setFixedHeight(27)
        card.sparkline.setMinimumWidth(54)
        card.sparkline.setMaximumWidth(16_777_215)
        card.sparkline.set_values([])

        quote = view.intraday_quote if view is not None else None
        if quote is not None and quote.displays_value and quote.completed_bar is True:
            source_time = quote.as_of or "N/A"
            if len(source_time) >= 16 and source_time[:4].isdigit():
                source_time = source_time[5:]
            source_date, _, source_clock = source_time.partition(" ")
            card.meta.setText(
                f"Yahoo15m {_fmt(quote.value)}·{source_date}\n"
                f"{(source_clock or 'KST').replace(' ', '')}·지연/비실시간"
            )
        else:
            state = _display_message(quote) if quote is not None else "현재 표시 불가"
            card.meta.setText(
                f"Yahoo15m ^VIX·{state}\n지연/비실시간"
            )

        official = view.official_daily if view is not None else self._metrics.get("VIX")
        official_detail = (
            f"{view.official_provider} · {view.official_data_type}"
            if view is not None else "FRED / VIXCLS · COMPLETED_DAILY_PRIMARY"
        )
        quote_detail = (
            f"{view.intraday_provider} · {view.intraday_data_type}"
            if view is not None else "Yahoo / ^VIX · 표시 불가"
        )
        card.setToolTip(
            card.toolTip()
            + "\n\nVIX source separation"
            + f"\nPrimary: {official_detail} · as_of={official.as_of if official else 'N/A'}"
            + f"\nIntraday: {quote_detail} · as_of={quote.as_of if quote else 'N/A'}"
            + f"\nsource_timestamp={quote.source_timestamp if quote else 'N/A'}"
            + f"\ndelay_status={quote.delay_status if quote else 'N/A'}"
            + "\nYahoo 값은 FRED VIXCLS와 병합하거나 대체하지 않습니다."
        )

    def _render_market_session_bar(self, as_of_utc: object) -> None:
        state = _market_session_bar_state(as_of_utc)
        for label, text, is_open, accessible_name in (
            (
                self.domestic_market_session,
                state.domestic_label,
                state.domestic_open,
                "국내 정규장" if state.domestic_open else "국내 장마감",
            ),
            (
                self.us_market_session,
                state.us_label,
                state.us_open,
                "미국 정규장" if "정규장" in state.us_label else "미국 장마감",
            ),
        ):
            dot = "#179c68" if is_open else "#c8d0d9"
            label.setText(f'<span style="color:{dot}">●</span>&nbsp;{text}')
            label.setAccessibleName(accessible_name)
            label.setToolTip(text)

    def _render_current_observation_coverage(
        self, coverage: dict[str, CurrentObservationCoverageView],
    ) -> None:
        """Summarize the local-only current-observation matrix in the header.

        Exact route detail stays in the tooltip so compact cards do not imply
        that unavailable broker candidates supplied a value.
        """
        owner_only_ids = {
            "EQUITY_000660_NXT_CLOSE",
            "EQUITY_005930_NXT_CLOSE",
        }
        owner_only = [
            view for key, view in coverage.items()
            if key in owner_only_ids and getattr(view, "displays_value", False)
        ]
        displayed = [
            view for key, view in coverage.items()
            if key not in owner_only_ids and getattr(view, "displays_value", False)
        ]
        # Prefer the oldest accepted source timestamp in the compact single
        # line, so the row nearest the shared 60-minute fail-closed boundary
        # stays visible.  This is generic across local accepted identities.
        displayed.sort(key=lambda view: str(getattr(view, "provider_timestamp_utc", "")))
        unavailable = [view for view in coverage.values() if not getattr(view, "displays_value", False)]
        self.current_observation_status.setText(
            (
                f"Retained: {len(displayed)} strip display / {len(owner_only)} Korean-equity owner-only / "
                f"{len(unavailable)} unavailable"
            )
            if owner_only else f"Retained: {len(displayed)} display / {len(unavailable)} unavailable"
        )
        lines = [
            "Retained current-display coverage (local read only; GUI provider calls=0)",
            "The daily source date is an as-retrieved source-date label, not a live provider refresh or availability timestamp.",
            "Finalized history and Backtest inputs are separate.",
        ]
        for key in sorted(coverage):
            if key in owner_only_ids:
                continue
            view = coverage[key]
            value = getattr(view, "value", None)
            value_text = "N/A" if value is None else str(value)
            lines.extend((
                f"[{key}] {getattr(view, 'label', key)} | value={value_text}",
                f"provider={getattr(view, 'provider', 'N/A')} | route={getattr(view, 'route', 'N/A')}",
                f"interval={getattr(view, 'interval', 'N/A')} | as_of={getattr(view, 'as_of', None) or 'N/A'} "
                f"| retrieved_at_utc={getattr(view, 'retrieved_at_utc', None) or 'N/A'}",
                f"provider_timestamp_utc={getattr(view, 'provider_timestamp_utc', None) or 'N/A'} "
                f"| source_route={getattr(view, 'source_route', None) or 'N/A'}",
                f"freshness={getattr(view, 'freshness', 'N/A')} | finality={getattr(view, 'finality', 'N/A')} "
                f"| display_only={getattr(view, 'display_only', None)} | pit_safe={getattr(view, 'pit_safe', None)} "
                f"| reason={getattr(view, 'unavailable_reason', None) or 'accepted retained observation'}",
            ))
        tooltip = "\n".join(lines)
        self.current_observation_status.setToolTip(tooltip)
        self.current_observation_status.setAccessibleDescription(tooltip)
        # Detailed current-observation provenance remains available to tests and
        # accessibility tooling, but the visible Dashboard header is reserved
        # for the two market-session labels requested by the user.

    def _render_market_card_sparklines(
        self, views: dict[str, DashboardSparklineView],
    ) -> None:
        """Prefer completed intraday lanes, retaining typed daily fallbacks."""
        for key, card in self.market_cards.items():
            view = views.get(key)
            if view is not None and view.displays_values:
                card.set_intraday_sparkline(view)
        view = views.get("VIX")
        quote = self._metrics.get("VIX_INTRADAY_15M")
        if (
            view is None or not view.displays_values
            or quote is None or not quote.displays_value
        ):
            return
        source_time = view.as_of_kst or "N/A"
        date_text, _, clock_text = source_time.partition(" ")
        short_date = date_text[5:] if len(date_text) == 10 else date_text
        short_session = (
            "직전장" if view.session_label.startswith("직전") else "완료장"
        )
        session_date = view.session_date[5:] if view.session_date else "N/A"
        if "VIX" not in self.market_cards:
            return
        self.market_cards["VIX"].meta.setText(
            f"Yahoo15m {_fmt(quote.value)}·{short_date}\n"
            f"{(clock_text or 'KST').replace(' ', '')}·{short_session}{session_date}·지연"
        )

    def _render_us_option_pcr_scopes(
        self, views: tuple[USOptionPCRScopeView, ...] | None = None,
    ) -> None:
        """Expose every exact U.S. scope while the typed gate remains numeric-free."""
        scopes = views if views is not None else current_us_option_pcr_scope_views()
        card = self.derivative_cards["US_OPTION_PCR"]
        card.title.setText("미국 옵션 P/C")
        card.body.setText("Cboe 6종 · 라이선스 필요")
        card.meta.setText("Nasdaq·QQQ·NDX·SOXX · 소스 없음")
        card.badge.setText("숫자 차단")
        card.sparkline.set_values([])
        card.setAccessibleName(
            "미국 옵션 P/C 10개 범위 · Cboe 6종 라이선스 필요 · "
            "Nasdaq QQQ NDX SOXX 소스 없음"
        )
        detail = [
            "미국 옵션 P/C 범위별 표시 상태 · 모든 값 N/A",
            "Cboe 범위는 사전 서면 승인·서명 라이선스 전까지 숫자 표시 금지입니다.",
            "Nasdaq·QQQ·NDX·SOXX는 승인된 정확한 소스가 없습니다.",
            "어떤 집계·ETP·지수·OI·가격·한국·Yahoo·ORATS 값도 대체하지 않습니다.",
        ]
        for scope in scopes:
            detail.extend((
                f"\n[{scope.scope_id}] {scope.label} | {scope.display_state.value} | value=N/A",
                f"source_scope={scope.source_scope}",
                f"usage_status={scope.usage_status}",
                f"finality_status={scope.finality_status}",
                f"reason={scope.reason}",
            ))
        card.setToolTip("\n".join(detail))

    def _render_market_valuation_views(
        self, views: dict[str, MarketValuationView],
    ) -> None:
        """Render descriptive KRX PER/PBR context without signal language."""

        self._valuation_axis_available = False
        for market, label in self.market_valuation_labels.items():
            view = views.get(market)
            if not isinstance(view, MarketValuationView):
                label.setText(f"{market} · 현재 표시 불가")
                label.setToolTip("typed market valuation view가 없습니다.")
                self.market_valuation_charts[market].set_values(
                    None, None, None, None,
                )
                continue

            rolling_windows = {
                window.window_years: window for window in view.rolling_windows
            }
            if (
                len(view.rolling_windows) != 2
                or len(rolling_windows) != 2
                or set(rolling_windows) != {5, 10}
            ):
                rolling_windows = {}

            try:
                view_as_of = pd.Timestamp(view.as_of)
                if (
                    pd.isna(view_as_of)
                    or view_as_of.tzinfo is not None
                    or view_as_of != view_as_of.normalize()
                ):
                    raise ValueError("valuation as-of must be an exact date")
            except (AttributeError, TypeError, ValueError, OverflowError):
                view_as_of = None

            def metric_line(
                name: str,
                value: float | None,
                mean: float | None,
                median: float | None,
                percentile: float | None,
                observations: int,
                baseline_start: str | None,
                baseline_end: str | None,
                displays: bool,
            ) -> tuple[str, str, float | None, float | None]:
                if (
                    not displays or value is None or mean is None
                    or median is None or percentile is None
                    or observations <= 0 or baseline_start is None
                    or baseline_end is None
                ):
                    return f"{name} N/A", f"{name} N/A", None, None
                if percentile <= 10.0:
                    band = "하위 10%"
                elif percentile <= 25.0:
                    band = "하위권"
                elif percentile < 75.0:
                    band = "중앙권"
                elif percentile < 90.0:
                    band = "상위권"
                else:
                    band = "상위 10%"
                median_difference = (
                    f"{((value / median) - 1.0) * 100.0:+.0f}%"
                    if median != 0.0 else "N/A"
                )
                mean_difference = (
                    f"{((value / mean) - 1.0) * 100.0:+.0f}%"
                    if mean != 0.0 else "N/A"
                )
                window_values: list[float] = []
                window_details: list[str] = []
                prefix = name.lower()
                for years in (5, 10):
                    window = rolling_windows.get(years)
                    window_percentile = (
                        getattr(window, f"{prefix}_percentile", None)
                        if window is not None else None
                    )
                    window_count = (
                        getattr(window, f"{prefix}_observations", 0)
                        if window is not None else 0
                    )
                    window_start = (
                        getattr(window, f"{prefix}_baseline_start", None)
                        if window is not None else None
                    )
                    window_end = (
                        getattr(window, f"{prefix}_baseline_end", None)
                        if window is not None else None
                    )
                    try:
                        window_percentile_value = float(window_percentile)
                        window_start_date = pd.Timestamp(window_start)
                        window_end_date = pd.Timestamp(window_end)
                        window_is_valid = (
                            not isinstance(window_percentile, (bool, np.bool_))
                            and np.isfinite(window_percentile_value)
                            and 0.0 <= window_percentile_value <= 100.0
                            and isinstance(window_count, (int, np.integer))
                            and not isinstance(window_count, (bool, np.bool_))
                            and window_count > 0
                            and view_as_of is not None
                            and not pd.isna(window_start_date)
                            and not pd.isna(window_end_date)
                            and window_start_date.tzinfo is None
                            and window_end_date.tzinfo is None
                            and window_start_date == window_start_date.normalize()
                            and window_end_date == window_end_date.normalize()
                            and isinstance(window_start, str)
                            and isinstance(window_end, str)
                            and window_start == window_start_date.date().isoformat()
                            and window_end == window_end_date.date().isoformat()
                            and window_start_date <= window_end_date == view_as_of
                            and window_start_date >= (
                                view_as_of - pd.DateOffset(years=years)
                            )
                            and window_count <= (
                                window_end_date - window_start_date
                            ).days + 1
                        )
                    except (AttributeError, TypeError, ValueError, OverflowError):
                        window_is_valid = False
                    if (
                        window_percentile is None
                        or window_start is None
                        or window_end is None
                        or not window_is_valid
                    ):
                        window_values = []
                        window_details = []
                        break
                    window_values.append(window_percentile_value)
                    window_details.append(
                        f"{name}_{years}Y percentile={window_percentile:.1f}; "
                        f"observations={window_count}; "
                        f"coverage={window_start}..{window_end}"
                    )
                compact = (
                    f"{name} {value:.2f} · 5Y {window_values[0]:.0f}% · "
                    f"10Y {window_values[1]:.0f}%"
                    if len(window_values) == 2
                    else f"{name} {value:.2f} · 5Y N/A · 10Y N/A"
                )
                detail = (
                    f"{name} {value:.2f} · 평균 {mean:.2f} · "
                    f"평균대비 {mean_difference} · 중앙 {median:.2f} · "
                    f"중앙대비 {median_difference} · "
                    f"역사순위 {percentile:.0f}%({band}) · "
                    f"관측 {observations} · {baseline_start}~{baseline_end}"
                )
                if window_details:
                    detail += "\n" + "\n".join(window_details)
                return (
                    compact,
                    detail,
                    window_values[0] if len(window_values) == 2 else None,
                    window_values[1] if len(window_values) == 2 else None,
                )

            per, per_detail, per_5y, per_10y = metric_line(
                "PER", view.weighted_per, view.per_mean, view.per_median,
                view.per_percentile, view.per_observations,
                view.per_baseline_start, view.per_baseline_end,
                view.displays_per,
            )
            pbr, pbr_detail, pbr_5y, pbr_10y = metric_line(
                "PBR", view.weighted_pbr, view.pbr_mean, view.pbr_median,
                view.pbr_percentile, view.pbr_observations,
                view.pbr_baseline_start, view.pbr_baseline_end,
                view.displays_pbr,
            )
            label.setText(
                f"{market} · {per} | {pbr} · 기준 {view.as_of or 'N/A'}"
                if view.display_state is DashboardDisplayState.VALUE
                else f"{market} · 현재 표시 불가 · 기준 {view.as_of or 'N/A'}"
            )
            self.market_valuation_charts[market].set_values(
                per_5y, per_10y, pbr_5y, pbr_10y,
            )
            if market == "KOSPI":
                self._valuation_axis_available = bool(
                    view.display_state is DashboardDisplayState.VALUE
                    and all(
                        value is not None
                        for value in (per_5y, per_10y, pbr_5y, pbr_10y)
                    )
                )
            label.setToolTip(
                f"dataset=kr_index_fundamental_daily\nmarket={view.market}\n"
                f"index_code={view.index_code}\nsource={view.source}\n"
                f"as_of={view.as_of or 'N/A'}\nexpected={view.expected_as_of or 'N/A'}\n"
                f"baseline={view.baseline_start or 'N/A'}..{view.baseline_end or 'N/A'}\n"
                f"per_coverage={view.per_baseline_start or 'N/A'}..{view.per_baseline_end or 'N/A'} "
                f"({view.per_observations} observations)\n"
                f"pbr_coverage={view.pbr_baseline_start or 'N/A'}..{view.pbr_baseline_end or 'N/A'} "
                f"({view.pbr_observations} observations)\n"
                f"{per_detail}\n{pbr_detail}\n"
                f"pit_status={view.pit_status}\n"
                "ratio_horizon=PROVIDER_DEFINED_UNRESOLVED_NOT_FORWARD\n"
                "forward_per=UNSUPPORTED; forward_pbr=UNSUPPORTED\n"
                "earnings_momentum=UNSUPPORTED; market_regime=UNAVAILABLE\n"
                f"reason={view.unavailable_reason or 'accepted descriptive local view'}\n"
                "as-of 이하 관측만 사용 · 채움/대체/예측 없음"
            )
            label.setAccessibleDescription(label.toolTip())
        self._render_market_regime_axis_summary()

    def _render_vix_futures_unavailable(
        self, view: VIXFuturesDashboardView | None = None,
    ) -> None:
        """Render the unresolved Yahoo-to-CFE route without any numeric fallback."""
        gate = view if view is not None else build_vix_futures_dashboard_view()
        card = self.derivative_cards["VIX_FUTURES"]
        gate_is_unavailable = (
            gate.route_status is VIXFuturesRouteStatus.UNAVAILABLE_IDENTITY_UNVERIFIED
            and not gate.metric.displays_value
            and gate.metric.value is None
            and gate.provider_symbol is None
            and gate.series_kind is None
            and gate.contract_symbol is None
            and gate.expiry is None
            and gate.roll_policy is None
        )
        reason = (
            gate.metric.unavailable_reason
            if gate_is_unavailable
            else "Typed VIX-futures gate did not preserve the unavailable identity boundary."
        )
        card.title.setText("VIX 선물 · CFE VX")
        card.body.setText("현재 표시 불가")
        card.meta.setText("Yahoo→CFE VX · 식별 검증 필요")
        card.badge.setText("미확인")
        card.sparkline.set_values([])
        card.setAccessibleName("VIX 선물 CFE VX · 현재 표시 불가 · 식별 검증 필요")
        unresolved = ", ".join(gate.unresolved_rules) or "TYPED_GATE_MISMATCH"
        card.setToolTip(
            "VIX 선물은 현물 VIX와 다른 상품입니다.\n"
            f"route_status={gate.route_status.value}\n"
            f"exchange={gate.exchange}\n"
            f"exchange_product_root={gate.exchange_product_root}\n"
            f"provider_symbol={gate.provider_symbol or 'UNVERIFIED'}\n"
            f"series_kind={gate.series_kind.value if gate.series_kind else 'UNVERIFIED'}\n"
            f"contract_symbol={gate.contract_symbol or 'UNVERIFIED'}\n"
            f"expiry={gate.expiry or 'UNVERIFIED'}\n"
            f"roll_policy={gate.roll_policy or 'UNVERIFIED'}\n"
            f"source_timezone={gate.source_timezone}\n"
            f"unresolved_rules={unresolved}\n"
            f"reason={reason}\n"
            "^VIX·ETN·ETF·옵션·유사 선물·Yahoo·ORATS 값을 대체하지 않습니다."
        )

    def render_current_stage(self, view: DashboardCurrentStageView) -> None:
        """Publish current-only cards/rates without clearing full surfaces."""

        self._render_market_session_bar(view.as_of_utc)
        self._metrics.update(view.metrics)
        for key, card in self.market_cards.items():
            metric = view.metrics.get(key)
            if metric is None:
                continue
            card.set_view(metric, self._series.get(key))
            if metric.route.startswith((
                "yahoo-global60m-current:", "yahoo-market-current:",
            )):
                card.badge.setMaximumWidth(64)
                card.badge.setText(
                    "24시간" if key == "BITCOIN" else
                    "선물 거래" if key in {"NQ_FUTURES", "GOLD", "WTI"} else
                    _compact_freshness_label(metric.freshness)
                )
            self._market_metadata[key] = {
                "status": metric.freshness, "source": metric.source,
            }

        usd_krw = view.metrics.get("USD_KRW_60M")
        if usd_krw is not None:
            row = self.rate_rows["USD_KRW"]
            row.set_view(usd_krw, self._series.get("USD_KRW"))
            if usd_krw.displays_value:
                row.meta.setText(f"Yahoo · {usd_krw.as_of or 'N/A'}")
            row.setToolTip(
                row.toolTip() + "\n현재 카드 전용 로컬 투영 · 공식 H.10 값을 대체하지 않음"
            )
        for row_key, series_key in (
            ("UST5_QUOTE", "UST5_QUOTE_15M"),
            ("UST10", "UST10_QUOTE_15M"),
            ("UST30", "UST30_QUOTE_15M"),
        ):
            if series_key not in view.metrics:
                continue
            rate_view = view.treasury_rate_views.get(row_key)
            if rate_view is not None:
                self.rate_rows[row_key].set_treasury_view(
                    rate_view, None, self._series.get(series_key),
                )

    def render(self, data: dict) -> None:
        self._render_market_session_bar(
            data.get("market_session_as_of_utc", pd.Timestamp.now(tz="UTC"))
        )
        self._metrics = {
            key: value for key, value in data.get("dashboard_metrics", {}).items()
            if isinstance(value, DashboardMetricView)
        }
        self._series = {
            key: value for key, value in data.get("dashboard_series", {}).items()
            if isinstance(value, DashboardSeriesView)
        }
        coverage = {
            key: value for key, value in data.get("current_observation_coverage", {}).items()
            if isinstance(value, CurrentObservationCoverageView)
        }
        self._render_current_observation_coverage(coverage)
        treasury_rate_views = {
            key: value for key, value in data.get("treasury_rate_views", {}).items()
            if isinstance(value, TreasuryRateView)
        }
        vix_source_views = {
            key: value for key, value in data.get("vix_source_views", {}).items()
            if isinstance(value, VIXSourceView)
        }
        for key, card in self.market_cards.items():
            metric = self._metrics.get(key)
            card.set_view(metric, self._series.get(key))
            if metric is not None and metric.route.startswith("yahoo-global60m-current:"):
                card.badge.setMaximumWidth(64)
                card.badge.setText(
                    "24시간" if key == "BITCOIN" else
                    "선물 거래" if key in {"NQ_FUTURES", "GOLD", "WTI"} else
                    "60분 완료"
                )
        self._render_vix_sources(vix_source_views.get("VIX"))
        card_sparklines = {
            key: value for key, value in data.get("market_card_sparklines", {}).items()
            if isinstance(value, DashboardSparklineView)
        }
        self._render_market_card_sparklines(card_sparklines)
        average_comparisons = {
            key: value for key, value in data.get("daily_average_comparisons", {}).items()
            if isinstance(value, DashboardAverageComparisonView)
        }
        market_valuations = {
            key: value for key, value in data.get("market_valuation_views", {}).items()
            if isinstance(value, MarketValuationView)
        }
        self._render_market_valuation_views(market_valuations)
        for key, card in self.market_cards.items():
            card.set_average_comparison(
                average_comparisons.get(key), self._metrics.get(key),
            )
            # set_average_comparison updates its content for tooltips and a
            # later density switch, but compact mode must not expand the
            # one-row market strip after every data refresh.
            card.comparison.setVisible(self._preferences.density == "DETAIL")
            metric = self._metrics.get(key)
            if metric is not None and metric.route.startswith("yahoo-global60m-current:"):
                card.badge.setMaximumWidth(64)
                card.badge.setText(
                    "24시간" if key == "BITCOIN" else
                    "선물 거래" if key in {"NQ_FUTURES", "GOLD", "WTI"} else
                    "60분 완료"
                )
        for key in ("NQ_FUTURES", "GOLD", "WTI"):
            card = self.market_cards[key]
            metric = self._metrics.get(key)
            if (
                metric is not None and metric.displays_value
                and metric.dataset_id == "global_commodity_futures_daily"
            ):
                display_date = (
                    metric.as_of[5:]
                    if metric.as_of and len(metric.as_of) == 10 else metric.as_of or "N/A"
                )
                card.badge.setMaximumWidth(52)
                card.badge.setText("일봉 완료")
                card.meta.setText(
                    f"{_compact_freshness_label(metric.freshness)}"
                    f"·{display_date}"
                )
                card.meta.setAccessibleName(
                    f"{_compact_freshness_label(metric.freshness)} · "
                    f"Yahoo 기준 {metric.as_of or 'N/A'}"
                )
        self._market_metadata = {
            key: {"status": metric.freshness, "source": metric.source}
            for key, metric in self._metrics.items()
        }

        flow_views = {
            key: value for key, value in data.get("market_flow_views", {}).items()
            if isinstance(value, MarketInvestorFlowView)
        }
        funding_view = data.get("market_funding_view")
        self.market_flow_panel.set_views(
            flow_views,
            funding_view if isinstance(funding_view, MarketFundingView) else None,
        )

        breadth = [
            self._metrics.get(key)
            for key in ("KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED")
        ]
        if all(metric is not None and metric.displays_value for metric in breadth):
            advancing, declining, unchanged = breadth
            assert advancing is not None and declining is not None and unchanged is not None
            same_date = len({metric.as_of for metric in breadth}) == 1
            if same_date:
                self.kospi200_breadth.set_lines(
                    [
                        f"상승 {_fmt(advancing.value)} · 하락 {_fmt(declining.value)} · 보합 {_fmt(unchanged.value)}",
                        f"기준 {advancing.as_of}",
                    ],
                    tooltip=(
                        f"dataset={advancing.dataset_id}\nsource={advancing.source}\n"
                        f"scope=KOSPI200-only exact constituent date\n"
                        f"freshness={advancing.freshness}\nas_of={advancing.as_of}\n"
                        f"expected={advancing.expected_as_of}\nroute={advancing.route}"
                    ),
                )
            else:
                self.kospi200_breadth.set_lines([
                    "시장폭 미반영",
                    "상세는 Data Status",
                ])
        else:
            reason = next((
                metric.unavailable_reason for metric in breadth
                if metric is not None and metric.unavailable_reason
            ), "KOSPI200-only breadth 데이터를 읽을 수 없습니다.")
            as_of = next((metric.as_of for metric in breadth if metric is not None and metric.as_of), "N/A")
            self.kospi200_breadth.set_lines([
                "시장폭 미반영",
                f"기준 {as_of}",
            ], tooltip=(
                f"reason={reason}\n상세 상태·예상일·다음 조치는 Data Status에서 확인"
            ))

        toss_short = data.get("toss_short_watchlist")
        if isinstance(toss_short, TossShortWatchlistView) and toss_short.displays_values:
            self.toss_short_watchlist.set_lines(
                [
                    *(
                        f"{member.name} {_fmt(member.short_selling_volume, 0)}주 · "
                        f"{_fmt(member.short_selling_amount, 0)}원"
                        for member in toss_short.members
                    ),
                    f"기준 {toss_short.as_of}",
                ],
                tooltip=(
                    f"dataset={toss_short.dataset_id}\nsource={toss_short.source}\n"
                    f"scope={toss_short.source_scope}\nas_of={toss_short.as_of}\n"
                    f"freshness={toss_short.freshness}\nroute={toss_short.route}\n"
                    f"pit={toss_short.pit_label}\nautomation_enabled={toss_short.automation_enabled}\n"
                    "Toss 종목별 KRX-only EOD이며 공식 KRX 시장 전체, KRX+NXT 합산, "
                    "공매도 잔고를 대체·혼합하지 않습니다."
                ),
            )
        else:
            reason = (
                toss_short.unavailable_reason
                if isinstance(toss_short, TossShortWatchlistView)
                else "검증된 Toss 종목별 EOD 로컬 view가 없습니다."
            )
            as_of = getattr(toss_short, "as_of", None) or "N/A"
            self.toss_short_watchlist.set_lines(
                ["종목별 EOD 미반영", f"기준 {as_of}"],
                tooltip=(
                    f"dataset={getattr(toss_short, 'dataset_id', 'toss_equity_short_watchlist_daily')}\n"
                    f"scope={getattr(toss_short, 'source_scope', 'KRX_ONLY_PROVIDER_EOD')}\n"
                    f"reason={reason}\n공식 KRX 시장 전체 또는 공매도 잔고 대체 없음"
                ),
            )

        nxt_000660 = coverage.get("EQUITY_000660_NXT_CLOSE")
        if (
            isinstance(nxt_000660, CurrentObservationCoverageView)
            and nxt_000660.displays_value
            and not (
                isinstance(toss_short, TossShortWatchlistView)
                and toss_short.displays_values
            )
        ):
            label = nxt_000660.visible_label or "NXT close"
            value = _fmt(nxt_000660.value, 0)
            provenance = nxt_000660.unavailable_reason or "NOT_LIVE"
            tooltip = (
                f"identity={nxt_000660.coverage_id}\nvalue={value} {nxt_000660.unit}\n"
                f"provider={nxt_000660.provider}\nprovider_timestamp_utc={nxt_000660.provider_timestamp_utc}\n"
                f"visible_session_label={label}\nroute={nxt_000660.route}\n"
                f"source_route={nxt_000660.source_route}\nfinality={nxt_000660.finality}\n"
                f"display_only={nxt_000660.display_only}; pit_safe={nxt_000660.pit_safe}\n"
                f"provenance={provenance}\n"
                "This is an inferred NXT close, not a provider-declared venue/session or live quote. "
                "The Toss EOD row remains separate."
            )
            self.toss_short_watchlist.set_lines(
                [
                    f"000660 {value} {nxt_000660.unit} · {label}",
                    "NXT inferred · NOT_LIVE · display-only; EOD separate",
                ],
                tooltip=tooltip,
            )
            self.toss_short_watchlist.setAccessibleDescription(tooltip)

        nq = self._metrics.get("NQ_FUTURES")
        self._render_nq_chart(nq, self._series.get("NQ_FUTURES"))
        self._render_percentile_gauge("VIX")
        self._render_percentile_gauge("VKOSPI")

        account_portfolio = data.get("account_portfolio")
        if isinstance(account_portfolio, AccountPortfolioView):
            self.account_placeholder.set_portfolio(account_portfolio)
        else:
            account_view = data.get("account_snapshot")
            self.account_placeholder.set_view(
                account_view if isinstance(account_view, AccountSnapshotView) else None
            )
        for key, row in self.rate_rows.items():
            if key in {"USD_KRW", "USD_JPY"}:
                primary = (
                    self._metrics.get("USD_KRW_60M")
                    if key == "USD_KRW"
                    and self._metrics.get("USD_KRW_60M") is not None
                    and self._metrics["USD_KRW_60M"].displays_value
                    else self._metrics.get(key)
                )
                row.set_view(primary, self._series.get(key))
                if key == "USD_JPY":
                    row.setToolTip(
                        row.toolTip() + "\nDEXJPUS · JPY per one USD · 역수/100단위 변환 없음"
                    )
                elif key == "USD_KRW":
                    intraday = self._metrics.get("USD_KRW_60M")
                    if intraday is not None:
                        if intraday.displays_value:
                            row.meta.setText(
                                f"Yahoo · {intraday.as_of or 'N/A'}"
                            )
                        row.setToolTip(
                            row.toolTip() + "\n상세 전용 Yahoo KRW=X 60분 지연 · "
                            f"{intraday.as_of or 'N/A'} · 공식 H.10 값을 대체하지 않음"
                        )
            elif key == "KR_TREASURY":
                row.set_unavailable(
                    "BOK ECOS 817Y002는 publication/finality 관측이 완료되지 않아 "
                    "어떤 만기도 현재 숫자로 표시하지 않습니다."
                )
            elif key in treasury_rate_views:
                quote_series_key = {
                    "UST5_QUOTE": "UST5_QUOTE_15M",
                    "UST10": "UST10_QUOTE_15M",
                    "UST30": "UST30_QUOTE_15M",
                }.get(key)
                row.set_treasury_view(
                    treasury_rate_views[key], self._series.get(key),
                    self._series.get(quote_series_key) if quote_series_key else None,
                )
            else:
                row.set_views(
                    self._metrics.get(key), self._series.get(key),
                    primary_tag="공식 일일",
                )
            if key in average_comparisons:
                row.set_average_comparison(average_comparisons[key])

        for key in ("KOSPI200_BASIS", "VOLUME_PCR", "OI_PCR", "VKOSPI"):
            self.derivative_cards[key].set_view(self._metrics.get(key), self._series.get(key))
        ls_flow_metric = self._metrics.get("LS_FUTURES_FOREIGN_NET")
        basis_card = self.derivative_cards["KOSPI200_BASIS"]
        if ls_flow_metric is not None and ls_flow_metric.value is not None:
            basis_card.meta.setText(
                f"{basis_card.meta.text()} · LS 외국인 {float(ls_flow_metric.value):+,.0f}계약"
            )
        basis_card.setToolTip(
            basis_card.toolTip()
            + "\nLS t8462 KOSPI200 선물 외국인 순계약: "
            + (
                f"{float(ls_flow_metric.value):+,.0f}계약 · {ls_flow_metric.as_of or 'N/A'}"
                if ls_flow_metric is not None and ls_flow_metric.value is not None
                else "현재 로컬 검증값 없음"
            )
            + "\nRaw 설명값이며 선물 현재가·미결제약정·실시간 체결값이나 Backtest 입력이 아닙니다."
        )
        short_card = self.derivative_cards["SHORT_SELLING_VALUE"]
        short_metric = self._metrics.get("SHORT_SELLING_VALUE")
        short_card.set_view(short_metric, None)
        if short_metric is not None and short_metric.displays_value:
            short_card.body.setText(f"{float(short_metric.value) / 1_000_000_000_000:,.2f}조원")
            short_card.meta.setText(
                f"KOSPI+KOSDAQ · {short_metric.as_of or 'N/A'} 장마감"
            )
        short_card.setToolTip(
            short_card.toolTip()
            + "\n공식 KOSPI·KOSDAQ 종목별 공매도 거래대금을 같은 기준일에서 합산한 값입니다. "
            "공매도 잔고·대차잔고·Toss 관심종목 값과 서로 대체하지 않습니다."
        )
        volume_pcr_card = self.derivative_cards["VOLUME_PCR"]
        volume_pcr_card.setToolTip(
            volume_pcr_card.toolTip() + "\nKOSPI200 정규 옵션 공급자 범위의 "
            "put_volume / call_volume입니다. 주간옵션과 미국 옵션시장을 포함하지 않으며 "
            "valid-empty는 0이 아닌 N/A입니다."
        )
        oi_pcr_card = self.derivative_cards["OI_PCR"]
        oi_pcr_card.setToolTip(
            oi_pcr_card.toolTip() + "\nKOSPI200 정규 옵션 공급자 범위의 "
            "put_open_interest / call_open_interest입니다. 거래량 P/C, 가격 P/C, "
            "미국 옵션 P/C와 서로 대체하지 않습니다."
        )
        call, put = self._metrics.get("CALL_WALL"), self._metrics.get("PUT_WALL")
        wall_card = self.derivative_cards["WALL"]
        if call and put and call.displays_value and put.displays_value and call.as_of == put.as_of:
            wall_card.set_view(call)
            wall_card.title.setText("Call / Put 최대 OI")
            wall_card.body.setText(f"C {_fmt(call.value)} · P {_fmt(put.value)}")
            wall_card.meta.setText(f"행사가 · 기준 {call.as_of[5:]}")
            wall_card.setToolTip(
                "당일 최근월물에서 미결제약정이 가장 큰 행사가입니다. "
                "active/gamma wall 또는 가격 예측값이 아닙니다."
            )
        else:
            wall_card.set_view(call or put)
            wall_card.title.setText("Call / Put 최대 OI")
        self._render_vix_futures_unavailable()
        self._render_us_option_pcr_scopes()
        self.freshness.setText("로컬 데이터")
        future_notes = {
            "NQ_FUTURES": "Yahoo NQ=F 완료 60분봉이며 미국 현물 정규장 상태와 별도인 연속선물입니다.",
            "GOLD": "Yahoo GC=F 완료 60분봉 연속선물이며 금 현물 가격이 아닙니다.",
            "WTI": "Yahoo CL=F 완료 60분봉 연속선물이며 WTI 현물 가격이 아닙니다.",
            "BITCOIN": "Yahoo BTC-USD 완료 60분봉이며 24시간 거래 자산입니다.",
        }
        for key, note in future_notes.items():
            card = self.market_cards[key]
            card.setToolTip(f"{card.toolTip()}\n{note}")

        selected = self.market_asset.currentText()
        selected_metric = self._metrics.get(self.CHART_METRICS.get(selected, selected))
        if selected_metric is None:
            self.render_market_chart(pd.DataFrame())

    def _metric_status(self, key: str) -> str:
        metric = self._metrics.get(key)
        if metric is None:
            return FRESHNESS_COPY["UNKNOWN"]
        return f"{_freshness_label(metric.freshness)} · {metric.pit_label}"

    def _render_nq_chart(
        self,
        metric: DashboardMetricView | None,
        series: DashboardSeriesView | None,
    ) -> None:
        self.nq_chart.clear()
        nq_provenance = (
            "NQ 일봉 차트는 보존된 완료 일봉만 사용합니다. "
            "60분 현재 관측과 거래일을 혼합하지 않습니다.\n"
            "백테스트 입력을 대체하지 않습니다. "
            "상세 출처·경로·세션·최종성·조치 판단은 Data Status에서 확인합니다."
        )
        self.nq_chart_title.setToolTip(nq_provenance)
        self.nq_chart.setToolTip(nq_provenance)
        self.nq_detail.setToolTip(nq_provenance)
        if series is None or series.frame.empty:
            self._nq_session_mapping = None
            self._nq_axis.set_session_dates(())
            self.nq_state.setText(self._state_message(metric))
            self.nq_detail.setText(
                metric.unavailable_reason if metric and metric.unavailable_reason
                else "연속선물 데이터가 없습니다."
            )
            return
        interval = self.nq_interval.currentText()
        frame = _aggregate_ohlc(series.frame, interval)
        if frame.empty:
            self._nq_session_mapping = None
            self._nq_axis.set_session_dates(())
            self.nq_state.setText(FRESHNESS_COPY["UNKNOWN"])
            self.nq_detail.setText("차트로 표시할 유효한 OHLC 값이 없습니다.")
            return
        total_rows = len(frame)
        visible_frame = frame.tail(120) if interval == "일봉" else frame
        try:
            self._nq_session_mapping = (
                _daily_session_axis_mapping(visible_frame, ExchangeMarket.US)
                if interval == "일봉" else _observation_axis_mapping(visible_frame)
            )
        except ValueError as error:
            self._nq_session_mapping = None
            self.nq_state.setText(FRESHNESS_COPY["UNKNOWN"])
            self.nq_detail.setText(f"retained NQ chart unavailable: {error}")
            return
        self._nq_axis.set_session_dates(self._nq_session_mapping.dates)
        positions = self._nq_session_mapping.positions
        bars = [
            (float(position), float(row.open), float(row.high), float(row.low), float(row.close))
            for position, row in zip(positions, visible_frame.itertuples(index=False))
        ]
        self.nq_chart.addItem(CandlestickItem(bars))
        self.nq_chart.enableAutoRange()
        count_text = (
            f"최근 {len(visible_frame)}개 표시 · 전체 {total_rows}개 보유"
            if len(visible_frame) != total_rows else f"전체 {total_rows}개 표시"
        )
        latest = visible_frame.iloc[-1]
        latest_close = float(latest["close"])
        latest_date = pd.Timestamp(latest["date"]).date().isoformat()
        change_text = "등락 N/A"
        if len(visible_frame) > 1:
            previous = float(visible_frame.iloc[-2]["close"])
            if np.isfinite(previous) and previous != 0:
                change_text = f"{(latest_close / previous - 1.0) * 100:+.2f}%"
        state = (
            f"{interval} · {count_text} · {_fmt(latest_close)} · {change_text} · "
            f"완료 일봉 기준 {latest_date}"
        )
        if interval == "일봉":
            self.nq_state.setText(state)
            warning = _session_axis_warning(self._nq_session_mapping)
            self.nq_detail.setText(
                "완료 일봉 · 연속선물 · 60분 현재값과 분리"
                + (f" · {warning}" if warning else "")
            )
        else:
            observed_until = pd.Timestamp(visible_frame["date"].iloc[-1]).date().isoformat()
            progress = f"진행 중 집계 · {observed_until}까지"
            self.nq_state.setText(f"{state} · {progress}")
            self.nq_detail.setText(
                f"완료 일봉 기반 · {progress} · 연속선물 · 설명용"
            )

    def _rerender_nq_chart(self) -> None:
        self._render_nq_chart(
            self._metrics.get("NQ_FUTURES"), self._series.get("NQ_FUTURES")
        )

    def _render_percentile_gauge(self, key: str) -> None:
        metric = self._metrics.get(key)
        series = self._series.get(key)
        gauge = self.gauges[key]
        current_metric = metric
        current_source_time: pd.Timestamp | None = None
        if key == "VIX":
            intraday = self._metrics.get("VIX_INTRADAY_15M")
            try:
                source_time = pd.Timestamp(intraday.source_timestamp)
                retrieved_at = pd.Timestamp(intraday.retrieved_at_utc)
                exact_current = (
                    intraday.displays_value
                    and intraday.dataset_id == "market_price_15m_current"
                    and intraday.series_id == "^VIX"
                    and intraday.unit == "index points"
                    and intraday.route == "yahoo-market-current:CBOE:VIX"
                    and intraday.source == (
                        "Yahoo ^VIX completed provider-native 15m current "
                        "projection; not FRED VIXCLS"
                    )
                    and intraday.freshness == "CURRENT_COMPLETED_15M"
                    and intraday.pit_status == "PIT_BLOCKED"
                    and intraday.automation_policy == "EVERY_30_MIN_CURRENT_ONLY"
                    and intraday.automation_enabled is True
                    and intraday.completed_bar is True
                    and intraday.delay_status == "DELAYED_COMPLETED_BAR"
                    and intraday.timestamp_basis == "PROVIDER_TIMESTAMP"
                    and source_time.tzinfo is not None
                    and retrieved_at.tzinfo is not None
                    and source_time <= retrieved_at
                    and intraday.as_of
                    == source_time.tz_convert("Asia/Seoul").strftime(
                        "%Y-%m-%d %H:%M KST"
                    )
                )
            except (AttributeError, TypeError, ValueError):
                exact_current = False
            if exact_current:
                current_metric = intraday
                current_source_time = source_time.tz_convert("Asia/Seoul")
        fred_bound = True
        if key == "VIX":
            fred_bound = bool(
                metric is not None
                and metric.displays_value
                and metric.dataset_id == "fred_vix_daily"
                and metric.series_id == "VIX"
                and metric.unit == "index points"
                and metric.route == "NORMALIZED_DAILY"
                and metric.source == "fred_vixcls"
                and series is not None
                and series.metric == metric
            )
        if (
            current_metric is None or not current_metric.displays_value
            or series is None or series.frame.empty
            or (key == "VIX" and not fred_bound)
        ):
            self._temperature_values.pop(key, None)
            gauge.set_unavailable()
            self._render_market_temperature_summary()
            return
        if key == "VIX":
            try:
                dates = pd.to_datetime(series.frame["date"], errors="coerce")
                if (
                    dates.isna().any() or dates.dt.tz is not None
                    or not dates.eq(dates.dt.normalize()).all()
                    or not dates.is_monotonic_increasing or dates.duplicated().any()
                ):
                    raise ValueError("FRED VIX daily dates are invalid")
            except (KeyError, TypeError, ValueError):
                self._temperature_values.pop(key, None)
                gauge.set_unavailable()
                self._render_market_temperature_summary()
                return
        values = pd.to_numeric(
            series.frame.get("value"), errors="coerce"
        ).dropna().tail(250)
        if values.empty:
            self._temperature_values.pop(key, None)
            gauge.set_unavailable()
            self._render_market_temperature_summary()
            return
        current_value = float(current_metric.value)
        if not np.isfinite(current_value):
            self._temperature_values.pop(key, None)
            gauge.set_unavailable()
            self._render_market_temperature_summary()
            return
        # VIX's latest Yahoo completed 15-minute observation is useful for a
        # current descriptive read even while FRED remains the completed-daily
        # history authority.  Compare the current value with the retained FRED
        # distribution; never append it to daily history or Backtest inputs.
        if key == "VIX" and current_metric is not metric:
            percentile = float(values.le(current_value).mean() * 100)
            source_detail = (
                "Yahoo 15분 완료·지연 · "
                + current_source_time.strftime("%m-%d %H:%M KST")
            )
            gauge.label.setText(
                f"공포 · VIX 현재 / FRED 일봉 {len(values)}개 중 위치"
            )
            gauge.setToolTip(
                "현재값: Yahoo ^VIX provider-native 완료 15분봉(지연 가능)\n"
                f"현재 기준(KST): {current_source_time.strftime('%Y-%m-%d %H:%M')}\n"
                f"비교분포: FRED VIXCLS 완료 일봉 {len(values)}개\n"
                "방법: 현재값 이하인 일봉 관측 비율(ECDF ≤)\n"
                "표시 전용이며 일별 이력·백테스트 입력에 혼합하지 않습니다."
            )
        else:
            percentile = float(values.rank(pct=True).iloc[-1] * 100)
            source_detail = ""
            if key == "VIX":
                gauge.label.setText(
                    f"공포 · VIX FRED 일봉 {len(values)}개 중 위치"
                )
                gauge.setToolTip(
                    f"FRED VIXCLS 완료 일봉 {len(values)}개 · pandas average-rank ECDF"
                )
        self._temperature_values[key] = percentile
        label, tone = self._volatility_state(percentile)
        gauge.set_gauge(
            percentile, minimum=0, maximum=100,
            text=f"{percentile:.0f}% · {current_value:.2f}",
            interpretation=label, tone=tone,
        )
        if key == "VIX":
            gauge.set_detail(source_detail or "FRED VIXCLS 완료 일봉")
        self._render_market_temperature_summary()

    def _render_market_temperature_summary(self) -> None:
        """Show corroboration, not a one-indicator trading conclusion."""
        rsi = self._temperature_values.get("RSI14")
        distance = self._temperature_values.get("DISPARITY60")
        volatility_key = (
            "VKOSPI" if "VKOSPI" in self._temperature_values
            else "VIX" if "VIX" in self._temperature_values
            else None
        )
        volatility = (
            self._temperature_values[volatility_key]
            if volatility_key is not None else None
        )
        available = [
            label for label, value in (
                ("RSI", rsi), ("MA60", distance),
                (volatility_key or "변동성", volatility),
            ) if value is not None
        ]
        missing = [
            label for label, value in (
                ("RSI", rsi), ("MA60", distance), ("변동성", volatility),
            ) if value is None
        ]
        self.temperature_coverage.setText(
            f"근거 {len(available)}/3 · "
            + (" · ".join(available) if available else "사용 가능 지표 없음")
            + (f" · 미반영 {', '.join(missing)}" if missing else "")
        )
        self._render_market_regime_axis_summary()
        score = self._oversold_strength(rsi, distance, volatility)
        if score is None:
            self.momentum_summary.setText(
                "과매도 강도 산출 보류 · RSI·MA60·변동성 근거가 모두 필요합니다"
            )
            return
        oversold_score, components = score
        candidate = ""
        if rsi < 30 and distance < 0:
            candidate = "과매도 후보"
        elif rsi > 70 and distance > 0:
            candidate = "과매수 후보"
        detail = " · ".join(
            f"{label} {value:.1f}" for label, value in components
        )
        if candidate:
            self.momentum_summary.setText(
                f"과매도 강도 {oversold_score:.1f}/10 · {candidate} · {detail} · "
                "가격 반전·거래량 확인 필요"
            )
        else:
            self.momentum_summary.setText(
                f"과매도 강도 {oversold_score:.1f}/10 · 단정 보류 · {detail}"
            )

    def _render_market_regime_axis_summary(self) -> None:
        """Expose independent regime evidence without imputing unsupported axes."""
        if not hasattr(self, "market_valuation_regime_gate"):
            return
        technical_available = all(
            self._temperature_values.get(key) is not None
            for key in ("RSI14", "DISPARITY60")
        ) and any(
            self._temperature_values.get(key) is not None
            for key in ("VKOSPI", "VIX")
        )
        axes = (
            ("가격·추세·변동성", technical_available),
            ("KOSPI 밸류에이션", self._valuation_axis_available),
            ("Forward EPS·Revision·ROE", self._earnings_axis_available),
        )
        available = [label for label, present in axes if present]
        missing = [
            f"{label} 미지원" if label == "Forward EPS·Revision·ROE" else label
            for label, present in axes if not present
        ]
        self.market_valuation_regime_gate.setText(
            f"시장 국면 근거 {len(available)}/3"
            + (f" · {' · '.join(available)}" if available else "")
            + (f" · 미반영 {', '.join(missing)}" if missing else "")
            + " · 고점·저점 판정 보류"
        )

    @staticmethod
    def _oversold_strength(
        rsi: float | None,
        ma60_distance: float | None,
        volatility_percentile: float | None,
    ) -> tuple[float, tuple[tuple[str, float], ...]] | None:
        """Return a descriptive 0–10 score only when all independent axes exist."""
        if rsi is None or ma60_distance is None or volatility_percentile is None:
            return None
        rsi_points = min(4.0, max(0.0, (50.0 - rsi) / 35.0 * 4.0))
        distance_points = min(3.0, max(0.0, -ma60_distance / 10.0 * 3.0))
        volatility_points = min(
            3.0, max(0.0, (volatility_percentile - 50.0) / 50.0 * 3.0)
        )
        components = (
            ("RSI", rsi_points),
            ("이격", distance_points),
            ("변동성", volatility_points),
        )
        return round(sum(value for _label, value in components), 1), components

    @staticmethod
    def _rsi_state(value: float) -> tuple[str, str]:
        if value < 30:
            return "과매도", "negative"
        if value < 45:
            return "약세", "negative"
        if value <= 55:
            return "중립", "neutral"
        if value <= 70:
            return "강세", "positive"
        return "과매수", "warning"

    @staticmethod
    def _volatility_state(percentile: float) -> tuple[str, str]:
        if percentile < 25:
            return "낮음", "positive"
        if percentile < 50:
            return "보통", "neutral"
        if percentile < 75:
            return "높음", "warning"
        return "매우 높음", "negative"

    def render_market_chart(self, frame: pd.DataFrame) -> None:
        selected = self.market_asset.currentText()
        metric = self._metrics.get(self.CHART_METRICS.get(selected, selected))
        self._market_frame_issue = None
        candidate = frame.copy() if metric is not None else pd.DataFrame()
        coverage = candidate.attrs.get(DASHBOARD_CHART_COVERAGE_ATTR)
        if selected == "KOSPI" and not candidate.empty:
            candidate, self._market_frame_issue = self._validated_kospi_ohlc(candidate)
        if not candidate.empty:
            try:
                full_mapping = _daily_session_axis_mapping(
                    candidate, self.CHART_MARKETS[selected],
                )
                candidate = _downsample_market_frame(candidate)
                if coverage is not None:
                    candidate.attrs[DASHBOARD_CHART_COVERAGE_ATTR] = coverage
                self._market_session_mapping = _downsampled_session_mapping(
                    candidate, full_mapping,
                )
            except ValueError as error:
                candidate = pd.DataFrame()
                self._market_session_mapping = None
                self._market_frame_issue = str(error)
        else:
            self._market_session_mapping = None
        self._market_frame = candidate
        self._rerender_market_chart()
        if selected == "KOSPI":
            if self._market_frame.empty:
                self._temperature_values.pop("RSI14", None)
                self._temperature_values.pop("DISPARITY60", None)
                self.gauges["RSI14"].set_unavailable()
                self.gauges["DISPARITY60"].set_unavailable()
                self._render_market_temperature_summary()
                return
            if metric is None or not metric.displays_value:
                self._temperature_values.pop("RSI14", None)
                self._temperature_values.pop("DISPARITY60", None)
                self.gauges["RSI14"].set_unavailable()
                self.gauges["DISPARITY60"].set_unavailable()
                self._render_market_temperature_summary()
                reason = metric.unavailable_reason if metric is not None else "현재 숫자 source timestamp가 없습니다."
                self.momentum_summary.setText(f"retained history only · current gauge withheld · {reason}")
                return
            rsi = pd.to_numeric(self._market_frame.get("rsi14"), errors="coerce").dropna()
            disparity = pd.to_numeric(self._market_frame.get("disparity60"), errors="coerce").dropna()
            if rsi.empty:
                self._temperature_values.pop("RSI14", None)
                self.gauges["RSI14"].set_unavailable()
            else:
                rsi_value = float(rsi.iloc[-1])
                self._temperature_values["RSI14"] = rsi_value
                rsi_label, rsi_tone = self._rsi_state(rsi_value)
                self.gauges["RSI14"].set_gauge(
                    rsi_value, minimum=0, maximum=100,
                    text=f"{rsi_value:.1f} · 30 / 70",
                    interpretation=rsi_label, tone=rsi_tone,
                )
            if disparity.empty:
                self._temperature_values.pop("DISPARITY60", None)
                self.gauges["DISPARITY60"].set_unavailable()
            else:
                disparity_value = float(disparity.iloc[-1])
                distance = disparity_value - 100.0
                self._temperature_values["DISPARITY60"] = distance
                trend_label = "중기 추세 강세" if distance > 0 else "중기 추세 약세" if distance < 0 else "60일선 일치"
                trend_tone = "positive" if distance > 0 else "negative" if distance < 0 else "neutral"
                self.gauges["DISPARITY60"].set_gauge(
                    distance, minimum=-20, maximum=20,
                    text=f"60일선 대비 {distance:+.1f}%",
                    interpretation=trend_label, tone=trend_tone,
                )
            self._render_market_temperature_summary()

    @staticmethod
    def _validated_kospi_ohlc(frame: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
        """Accept only one valid retained OHLC row per KOSPI session.

        The GUI deliberately does not repair a missing/duplicate/inconsistent
        source row: a candle is either the contracted retained observation or
        the whole chart becomes unavailable with its reason.
        """
        required = ["date", "open", "high", "low", "close"]
        if any(column not in frame for column in required):
            return pd.DataFrame(), "retained KOSPI OHLC fields are unavailable"
        result = frame.copy()
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        for column in required[1:]:
            result[column] = pd.to_numeric(result[column], errors="coerce")
        if result[required].isna().any().any():
            return pd.DataFrame(), "retained KOSPI OHLC contains a missing or non-numeric value"
        if result["date"].duplicated().any():
            return pd.DataFrame(), "retained KOSPI OHLC contains duplicate sessions"
        valid_range = (
            result["high"].ge(result[["open", "close"]].max(axis=1))
            & result["low"].le(result[["open", "close"]].min(axis=1))
            & result["low"].le(result["high"])
        )
        if not bool(valid_range.all()):
            return pd.DataFrame(), "retained KOSPI OHLC contains an inconsistent high/low range"
        return result.sort_values("date").reset_index(drop=True), None

    def _rerender_market_chart(self, _checked: bool | None = None) -> None:
        frame = self._market_frame
        self._clear_market_indicator_overlays()
        self.market_chart.clear()
        self.market_volume.clear()
        self.market_indicator.clear()
        self._crosshair = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#7187aa", style=QtCore.Qt.DashLine))
        self.market_chart.addItem(self._crosshair, ignoreBounds=True)
        self._crosshair.hide()
        if frame.empty:
            self._market_axis.set_session_dates(())
            self._market_volume_axis.set_session_dates(())
            self._market_indicator_axis.set_session_dates(())
            self.market_volume.hide()
            self.market_indicator.hide()
            self.market_indicator_legend.hide()
            self._set_market_chart_accessibility(())
            selected = self.market_asset.currentText()
            metric = self._metrics.get(self.CHART_METRICS.get(selected, selected))
            if metric is not None and metric.freshness == "STALE":
                message = f"{_freshness_label(metric.freshness)} · 기준일 {metric.as_of or 'N/A'}"
            else:
                reason = self._market_frame_issue or (getattr(metric, "unavailable_reason", None) if metric else None)
                message = f"{_display_message(metric)} · {reason or '로컬 데이터 확인 필요'}"
            self.market_chart_status.setText(message)
            self.market_chart_status.setToolTip(
                _chart_reference_metadata(
                    metric, frame, daily_session=True,
                    market_label=("미국장" if self.CHART_MARKETS[selected] is ExchangeMarket.US else "한국장"),
                )[1]
            )
            return
        mapping = self._market_session_mapping
        if mapping is None:
            self.market_volume.hide()
            self.market_indicator.hide()
            self.market_indicator_legend.hide()
            self._set_market_chart_accessibility(())
            self.market_chart_status.setText("retained chart session axis unavailable")
            return
        self._market_axis.set_session_dates(mapping.dates)
        self._market_volume_axis.set_session_dates(mapping.dates)
        self._market_indicator_axis.set_session_dates(mapping.dates)
        x = mapping.positions
        selected = self.market_asset.currentText()
        if selected == "KOSPI":
            bars = [
                (float(timestamp), float(row.open), float(row.high), float(row.low), float(row.close))
                for timestamp, row in zip(x, frame.itertuples(index=False))
            ]
            self.market_chart.addItem(CandlestickItem(bars))
        else:
            _plot_continuous_line(
                self.market_chart, x, frame["close"], color="#2f6fb2", width=2.0,
                name="종가", mapping=mapping,
            )
        market_indicators = self.market_indicator_panel.settings()
        enabled_labels: list[str] = []
        for column in ("ma5", "ma20", "ma60", "ma120"):
            if not getattr(market_indicators, column) or column not in frame:
                continue
            label, color, width = INDEX_CURVE_STYLES[column]
            _plot_continuous_line(
                self.market_chart, x, frame[column], color=color, width=width,
                name=label, mapping=mapping,
            )
            enabled_labels.append(label)
        if market_indicators.ema20 and "ema20" in frame:
            _plot_continuous_line(
                self.market_chart, x, frame["ema20"], color="#7ad151", width=1.45,
                name="EMA20", mapping=mapping,
            )
            enabled_labels.append("EMA20")
        if market_indicators.bollinger_bands and {
            "bollinger_upper", "bollinger_mid", "bollinger_lower",
        }.issubset(frame):
            for column, name in (
                ("bollinger_upper", "BB 상단"),
                ("bollinger_mid", "BB 중심"),
                ("bollinger_lower", "BB 하단"),
            ):
                _plot_continuous_line(
                    self.market_chart, x, frame[column], color="#c792ea", width=1.15,
                    name=name, mapping=mapping,
                )
            enabled_labels.append("BB(20,2)")
        self._render_market_indicator_overlays(x, mapping, market_indicators)
        if market_indicators.rsi14_mode == "Overlay" and "rsi14" in frame:
            enabled_labels.append("RSI14 (0–100 독립축)")
        if market_indicators.disparity60_mode == "Overlay" and "disparity60" in frame:
            enabled_labels.append("괴리60 (pp 독립축)")
        if enabled_labels:
            self.market_indicator_legend.setText("표시 지표: " + " · ".join(enabled_labels))
            self.market_indicator_legend.show()
        else:
            self.market_indicator_legend.hide()
        self._set_market_chart_accessibility(tuple(enabled_labels))
        volume = (
            pd.to_numeric(frame["volume"], errors="coerce")
            if "volume" in frame else pd.Series(dtype="float64")
        )
        if market_indicators.volume and volume.notna().any():
            self.market_volume.show()
            self.market_volume.addItem(pg.BarGraphItem(x=x, height=volume.fillna(0), width=.65, brush="#6b8fba"))
        else:
            self.market_volume.hide()
        self.market_indicator.clear()
        if market_indicators.rsi14_mode == "Panel" and "rsi14" in frame:
            _plot_continuous_line(
                self.market_indicator, x, frame["rsi14"], color="#16a085",
                width=1.7, name="RSI14", mapping=mapping,
            )
            for value in (30.0, 70.0):
                self.market_indicator.addItem(
                    pg.InfiniteLine(
                        pos=value, angle=0, movable=False,
                        pen=pg.mkPen("#aab3bd", style=QtCore.Qt.DashLine),
                    )
                )
            self.market_indicator.setYRange(0.0, 100.0, padding=0)
            self.market_indicator.getAxis("left").setLabel("RSI14")
            self.market_indicator.show()
        elif market_indicators.disparity60_mode == "Panel" and "disparity60" in frame:
            distance = pd.to_numeric(frame["disparity60"], errors="coerce") - 100.0
            _plot_continuous_line(
                self.market_indicator, x, distance, color="#9b59b6",
                width=1.7, name="60일 이격도", mapping=mapping,
            )
            self.market_indicator.addItem(
                pg.InfiniteLine(
                    pos=0.0, angle=0, movable=False,
                    pen=pg.mkPen("#aab3bd", style=QtCore.Qt.DashLine),
                )
            )
            finite = distance.dropna()
            extent = max(float(finite.abs().max()) if not finite.empty else 0.0, 1.0)
            self.market_indicator.setYRange(-extent * 1.12, extent * 1.12, padding=0)
            self.market_indicator.getAxis("left").setLabel("60일선 대비 %")
            self.market_indicator.show()
        else:
            self.market_indicator.hide()
        metric = self._metrics.get(self.CHART_METRICS.get(self.market_asset.currentText(), self.market_asset.currentText()))
        self.kospi_chart_title.setText(f"{selected}{' 캔들 차트' if selected == 'KOSPI' else ' 차트'}")
        warning = _session_axis_warning(mapping)
        reference_text, reference_detail = _chart_reference_metadata(
            metric,
            frame,
            daily_session=True,
            market_label=("미국장" if self.CHART_MARKETS[selected] is ExchangeMarket.US else "한국장"),
        )
        self.market_chart_status.setText(
            f"{self.market_asset.currentText()} · {self.market_period.currentText()} · {reference_text}"
            + (f" · {coverage_text}" if (coverage_text := _dashboard_chart_coverage_text(frame)) else "")
            + (f" · {warning}" if warning else "")
        )
        if metric is not None and metric.freshness == "STALE" and metric.unavailable_reason:
            self.market_chart_status.setText(
                f"STALE RETAINED HISTORY: {metric.unavailable_reason} | "
                + self.market_chart_status.text()
            )
        self.market_chart_status.setToolTip(
            "차트 출처·경로·원본 시각은 Data Status에서 확인하세요."
            + (f"\nsession_warning={warning}" if warning else "")
        )
        if metric is not None and metric.unavailable_reason:
            self.market_chart_status.setToolTip(
                self.market_chart_status.toolTip()
                + f"\nwarning={metric.unavailable_reason}"
            )

    def _sync_market_indicator_overlay_geometry(self, *_args) -> None:
        """Keep Dashboard's independent indicator views aligned to price pixels."""
        bounds = self.market_chart.getViewBox().sceneBoundingRect()
        for view in (
            self._market_rsi_overlay_view,
            self._market_disparity_overlay_view,
        ):
            view.setGeometry(bounds)
            view.linkedViewChanged(self.market_chart.getViewBox(), pg.ViewBox.XAxis)

    def _clear_market_indicator_overlays(self) -> None:
        """Clear every Dashboard overlay, guide, independent axis, and tooltip state."""
        for view in (
            self._market_rsi_overlay_view,
            self._market_disparity_overlay_view,
        ):
            view.clear()
            view.hide()
        self._market_rsi_overlay_axis.hide()
        self._market_disparity_overlay_axis.hide()
        self._market_overlay_items = {}
        self._market_overlay_guides = {}
        self.market_chart.setToolTip("")

    @staticmethod
    def _market_reference_line(
        position: float, label: str, color: str,
    ) -> pg.InfiniteLine:
        pen_color = QtGui.QColor(color)
        pen_color.setAlpha(145)
        return pg.InfiniteLine(
            pos=position, angle=0, movable=False,
            pen=pg.mkPen(pen_color, width=1, style=QtCore.Qt.DashLine),
            label=label, labelOpts={"position": 0.08, "color": color},
        )

    def _render_market_indicator_overlays(
        self,
        x: np.ndarray,
        mapping: SessionAxisMapping,
        settings: ChartIndicatorPreferences,
    ) -> None:
        """Render Dashboard RSI/disparity Overlay modes with truthful units."""
        if settings.rsi14_mode == "Overlay" and "rsi14" in self._market_frame:
            label, color, width = INDEX_CURVE_STYLES["rsi14"]
            self._market_rsi_overlay_view.show()
            self._market_rsi_overlay_axis.show()
            self._market_overlay_items["rsi14"] = _plot_continuous_line(
                self._market_rsi_overlay_view, x, self._market_frame["rsi14"],
                color=color, width=width, name=label, mapping=mapping,
            )
            guides = (
                self._market_reference_line(30.0, "RSI 30", color),
                self._market_reference_line(70.0, "RSI 70", color),
            )
            for guide in guides:
                self._market_rsi_overlay_view.addItem(guide, ignoreBounds=True)
            self._market_overlay_guides["rsi14"] = guides
            self._market_rsi_overlay_view.setYRange(0.0, 100.0, padding=0)
        if settings.disparity60_mode == "Overlay" and "disparity60" in self._market_frame:
            label, color, width = INDEX_CURVE_STYLES["disparity60"]
            exact = pd.to_numeric(
                self._market_frame["disparity60"], errors="coerce",
            ).to_numpy(dtype=float)
            signed_distance = exact - 100.0
            self._market_disparity_overlay_view.show()
            self._market_disparity_overlay_axis.show()
            self._market_overlay_items["disparity60"] = _plot_continuous_line(
                self._market_disparity_overlay_view, x, signed_distance,
                color=color, width=width, name=f"{label} (100 대비 pp)",
                mapping=mapping,
            )
            baseline = self._market_reference_line(0.0, "0 (=100%)", color)
            self._market_disparity_overlay_view.addItem(baseline, ignoreBounds=True)
            self._market_overlay_guides["disparity60"] = (baseline,)
            finite = signed_distance[np.isfinite(signed_distance)]
            extent = max(float(np.max(np.abs(finite))) if len(finite) else 0.0, 1.0)
            margin = max(extent * .12, .35)
            self._market_disparity_overlay_view.setYRange(
                -extent - margin, extent + margin, padding=0,
            )
        self._sync_market_indicator_overlay_geometry()

    def _set_market_chart_accessibility(self, enabled_labels: tuple[str, ...]) -> None:
        suffix = " · ".join(enabled_labels)
        self.market_chart.setAccessibleName(
            "시장 가격 차트" + (f" · 표시 지표: {suffix}" if suffix else ""),
        )

    def _measure_clicked(self, event) -> None:
        if self._frame.empty or self._session_mapping is None:
            return
        point = self.plot.plotItem.vb.mapSceneToView(event.scenePos())
        index = int(np.abs(self._dates - point.x()).argmin())
        self._measurement_points = (self._measurement_points + [index])[-2:]
        if len(self._measurement_points) < 2:
            self.measurement.setText(f"측정 A: {self._session_mapping.dates[index].date().isoformat()}")
            return
        left, right = self._measurement_points
        first, last = self._frame.iloc[left], self._frame.iloc[right]
        change = float(last.close) - float(first.close)
        self.measurement.setText(f"측정 {self._session_mapping.dates[left].date().isoformat()} → {self._session_mapping.dates[right].date().isoformat()} · {change:+,.2f}")

    def _mouse_moved(self, event) -> None:
        if self._market_frame.empty:
            self._crosshair.hide()
            return
        position = event[0] if isinstance(event, tuple) else event
        if not self.market_chart.sceneBoundingRect().contains(position):
            self._crosshair.hide()
            return
        point = self.market_chart.plotItem.vb.mapSceneToView(position)
        mapping = self._market_session_mapping
        if mapping is None:
            self._crosshair.hide()
            return
        dates = pd.Series(mapping.dates)
        x = mapping.positions
        index = int(np.abs(x - point.x()).argmin())
        self._crosshair.setPos(x[index])
        self._crosshair.show()
        row = self._market_frame.iloc[index]
        if self.market_asset.currentText() == "KOSPI":
            tooltip = (
                f"{dates.iloc[index].date()} · O {_fmt(row.get('open'))} · "
                f"H {_fmt(row.get('high'))} · L {_fmt(row.get('low'))} · C {_fmt(row.get('close'))} · "
                f"{_format_exact_share_volume(row.get('volume'))}"
            )
        else:
            tooltip = (
                f"{dates.iloc[index].date()} · close {_fmt(row.get('close'))} · "
                f"{_format_exact_share_volume(row.get('volume'))}"
            )
        self.market_chart.setToolTip(tooltip + self._market_overlay_tooltip(row))

    def _market_overlay_tooltip(self, row: pd.Series) -> str:
        """Expose every currently-rendered Dashboard indicator in the tooltip."""
        settings = self.market_indicator_panel.settings()
        values: list[str] = []
        for column in ("ma5", "ma20", "ma60", "ma120"):
            value = pd.to_numeric(row.get(column), errors="coerce")
            if getattr(settings, column) and pd.notna(value):
                values.append(f"{INDEX_CURVE_STYLES[column][0]} {_fmt(value)}")
        ema20 = pd.to_numeric(row.get("ema20"), errors="coerce")
        if settings.ema20 and pd.notna(ema20):
            values.append(f"EMA20 {_fmt(ema20)}")
        if settings.bollinger_bands:
            for column, label in (
                ("bollinger_upper", "BB 상단"),
                ("bollinger_mid", "BB 중심"),
                ("bollinger_lower", "BB 하단"),
            ):
                value = pd.to_numeric(row.get(column), errors="coerce")
                if pd.notna(value):
                    values.append(f"{label} {_fmt(value)}")
        if settings.rsi14_mode == "Overlay":
            rsi = pd.to_numeric(row.get("rsi14"), errors="coerce")
            if pd.notna(rsi):
                values.append(f"RSI14 {_fmt(rsi)}")
        if settings.disparity60_mode == "Overlay":
            disparity = pd.to_numeric(row.get("disparity60"), errors="coerce")
            if pd.notna(disparity):
                values.append(f"괴리60 {_fmt(disparity)} (pp {_fmt(float(disparity) - 100.0)})")
        return (" · " + " · ".join(values)) if values else ""

    def render_unavailable(self, reason: str) -> None:
        self.render({"dashboard_metrics": {}, "dashboard_series": {}})
        self._market_frame = pd.DataFrame()
        self._rerender_market_chart()
        self.freshness.setText(f"{FRESHNESS_COPY['READ_FAILURE']} · {reason}")


class IndexPage(QtWidgets.QWidget):
    request_series = QtCore.Signal(str, str)
    detach_requested = QtCore.Signal()
    indicator_settings_changed = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._index_view: IndexSeriesView | None = None
        self._detail_text = ""
        root=QtWidgets.QVBoxLayout(self)
        self.title = QtWidgets.QLabel("INDEX")
        self.title.setObjectName("pageTitle")
        root.addWidget(self.title)
        controls_widget = QtWidgets.QWidget()
        controls_root = QtWidgets.QVBoxLayout(controls_widget)
        controls_root.setContentsMargins(0, 0, 0, 0)
        controls_root.setSpacing(4)
        controls=QtWidgets.QHBoxLayout()
        indicator_controls = QtWidgets.QHBoxLayout()
        measurement_controls = QtWidgets.QHBoxLayout()
        controls_root.addLayout(controls)
        controls_root.addLayout(indicator_controls)
        controls_root.addLayout(measurement_controls)
        self.controls = controls
        self.indicator_controls = indicator_controls
        self.measurement_controls = measurement_controls
        self.control_labels = {}
        self.index=QtWidgets.QComboBox(); self.index.addItems(["KOSPI","KOSDAQ","KOSPI200"])
        self.period=QtWidgets.QComboBox(); self.period.addItems(["20D","60D","120D","1Y","3Y","MAX"]); self.period.setCurrentText("120D")
        self.rsi=QtWidgets.QComboBox(); self.rsi.addItems(["Off","Overlay","Panel"])
        self.disparity=QtWidgets.QComboBox(); self.disparity.addItems(["Off","Overlay","Panel"])
        for label, widget in (("Index",self.index),("Period",self.period),("RSI14",self.rsi),("60D Disparity",self.disparity)):
            label_widget = QtWidgets.QLabel(label)
            self.control_labels[label] = label_widget
            controls.addWidget(label_widget); controls.addWidget(widget)
            widget.setAccessibleName(label)
        self.indicator_panel = IndicatorControlPanel(allows_lower_panels=True)
        self.indicator_panel.apply(DEFAULT_PREFERENCES.index_indicators)
        self.indicator_panel.settings_changed.connect(self._apply_indicator_settings)
        self.indicator_panel.reset_requested.connect(self._reset_indicator_settings)
        self.control_labels["RSI14"].hide(); self.rsi.hide()
        self.control_labels["60D Disparity"].hide(); self.disparity.hide()
        indicator_controls.addWidget(self.indicator_panel)
        indicator_controls.addStretch()
        self.measurement = QtWidgets.QLabel("측정: 두 관측값을 선택하세요")
        self.measurement.setAccessibleName("정확한 표시 관측값 두 점 측정")
        self.measurement.setToolTip("차트의 두 표시 관측값 사이 가격·기간 변화를 읽기 전용으로 표시합니다.")
        measurement_controls.addWidget(self.measurement)
        self.clear_measurement_button = QtWidgets.QPushButton("측정 제거")
        self.clear_measurement_button.setAccessibleName("두 점 측정 제거")
        self.clear_measurement_button.clicked.connect(self._clear_measurement)
        measurement_controls.addWidget(self.clear_measurement_button)
        self.add_measurement_button = QtWidgets.QPushButton("현재 점 측정")
        self.add_measurement_button.setAccessibleName(
            "현재 표시 관측값을 두 점 측정에 추가"
        )
        self.add_measurement_button.setToolTip(
            "키보드로도 현재 교차선 또는 마지막 표시 관측값을 측정 점에 추가합니다."
        )
        self.add_measurement_button.clicked.connect(self._measure_current_observation)
        measurement_controls.addWidget(self.add_measurement_button)
        measurement_controls.addStretch()
        controls.addStretch()
        self.index_reload_button = QtWidgets.QPushButton("현재 선택 다시 읽기")
        self.index_reload_button.setAccessibleName(
            "현재 지수와 기간의 로컬 시계열 다시 읽기"
        )
        self.index_reload_button.setToolTip(
            "현재 선택을 바꾸거나 차트를 지우지 않고 로컬 저장 데이터만 다시 읽습니다."
        )
        self.index_reload_button.clicked.connect(self._request)
        controls.addWidget(self.index_reload_button)
        self.detach_button = QtWidgets.QPushButton("새 창에서 열기")
        self.detach_button.setAccessibleName("현재 차트를 독립된 새 창에서 열기")
        self.detach_button.clicked.connect(self.detach_requested)
        controls.addWidget(self.detach_button)
        root.addWidget(controls_widget)
        self.index_info = QtWidgets.QFrame()
        self.index_info.setObjectName("indexInfo")
        info_layout = QtWidgets.QHBoxLayout(self.index_info)
        info_layout.setContentsMargins(9, 5, 7, 5)
        info_layout.setSpacing(8)
        info_text = QtWidgets.QVBoxLayout()
        info_text.setSpacing(1)
        self.index_summary = QtWidgets.QLabel("선택한 지수의 검증된 로컬 일봉을 읽는 중입니다.")
        self.index_summary.setObjectName("indexSummary")
        self.index_summary.setWordWrap(True)
        self.index_meta = QtWidgets.QLabel("단위·기준일·신선도 확인 중")
        self.index_meta.setObjectName("indexMeta")
        self.index_meta.setWordWrap(True)
        info_text.addWidget(self.index_summary)
        info_text.addWidget(self.index_meta)
        info_layout.addLayout(info_text, 1)
        self.index_detail_button = QtWidgets.QPushButton("출처·기준 상세")
        self.index_detail_button.setAccessibleName("지수 차트 출처와 기준 상세")
        self.index_detail_button.clicked.connect(self._show_reference_details)
        info_layout.addWidget(self.index_detail_button)
        root.addWidget(self.index_info)
        self.index_legend = QtWidgets.QLabel()
        self.index_legend.setObjectName("indexLegend")
        self.index_legend.setWordWrap(True)
        root.addWidget(self.index_legend)
        self._price_axis = SessionDateAxisItem(
            orientation="bottom", minimum_label_spacing=132.0,
        )
        self.plot=pg.PlotWidget(axisItems={"bottom": self._price_axis}); self.plot.showGrid(x=True,y=True,alpha=.15); root.addWidget(self.plot,3)
        self.plot.getAxis("left").setWidth(72)
        self.plot.setAccessibleName("지수 가격·이동평균과 독립축 RSI14·60일 괴리율 차트")
        self._rsi_overlay_axis = self.plot.getAxis("right")
        self._rsi_overlay_axis.setLabel("RSI14 (0–100)", color=INDEX_CURVE_STYLES["rsi14"][1])
        self._rsi_overlay_axis.setWidth(62)
        self._rsi_overlay_axis.hide()
        self._rsi_overlay_view = pg.ViewBox(enableMenu=False)
        self._rsi_overlay_view.setObjectName("rsi14OverlayView")
        self._rsi_overlay_view.setMouseEnabled(x=False, y=False)
        self._rsi_overlay_view.setZValue(-90)
        self.plot.scene().addItem(self._rsi_overlay_view)
        self._rsi_overlay_axis.linkToView(self._rsi_overlay_view)
        self._rsi_overlay_view.setXLink(self.plot.getViewBox())

        self._disparity_overlay_axis = pg.AxisItem(orientation="right")
        self._disparity_overlay_axis.setLabel(
            "괴리60 (pp, 0=100%)", color=INDEX_CURVE_STYLES["disparity60"][1],
        )
        self._disparity_overlay_axis.setWidth(74)
        self._disparity_overlay_axis.hide()
        self.plot.plotItem.layout.addItem(self._disparity_overlay_axis, 2, 3)
        self._disparity_overlay_view = pg.ViewBox(enableMenu=False)
        self._disparity_overlay_view.setObjectName("disparity60OverlayView")
        self._disparity_overlay_view.setMouseEnabled(x=False, y=False)
        self._disparity_overlay_view.setZValue(-89)
        self.plot.scene().addItem(self._disparity_overlay_view)
        self._disparity_overlay_axis.linkToView(self._disparity_overlay_view)
        self._disparity_overlay_view.setXLink(self.plot.getViewBox())
        self._overlay_items: dict[str, pg.PlotDataItem] = {}
        self._overlay_guides: dict[str, tuple[pg.InfiniteLine, ...]] = {}
        self.plot.getViewBox().sigResized.connect(self._sync_indicator_overlay_geometry)
        self._volume_axis = SessionDateAxisItem(
            orientation="bottom", labels_visible=False,
        )
        self._volume_value_axis = VolumeAxisItem(orientation="left")
        self.volume=pg.PlotWidget(axisItems={"bottom": self._volume_axis, "left": self._volume_value_axis}); self.volume.setXLink(self.plot); self.volume.setMaximumHeight(180); root.addWidget(self.volume,1)
        self.volume.setAccessibleName("지수 거래량 차트")
        self._indicator_axis = SessionDateAxisItem(
            orientation="bottom", labels_visible=False,
        )
        self.indicator=pg.PlotWidget(axisItems={"bottom": self._indicator_axis}); self.indicator.setXLink(self.plot); self.indicator.setMaximumHeight(160); self.indicator.hide(); root.addWidget(self.indicator,1)
        self.indicator.getAxis("left").setWidth(72)
        self.indicator.setAccessibleName("지수 RSI14 및 60일 괴리율 차트")
        self.crosshairs=[]
        for chart in (self.plot,self.volume,self.indicator):
            line=pg.InfiniteLine(angle=90,movable=False,pen=pg.mkPen("#7187aa",style=QtCore.Qt.DashLine)); chart.addItem(line,ignoreBounds=True); self.crosshairs.append(line)
        self.hover=QtWidgets.QLabel("차트 위에서 기준일 확인 · 출처 KRX/pykrx · 로컬 보존 데이터")
        self.hover.setWordWrap(True)
        self.hover.setMinimumWidth(0)
        self.hover.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred,
        )
        root.addWidget(self.hover)
        self._proxy=pg.SignalProxy(self.plot.scene().sigMouseMoved,rateLimit=30,slot=self._mouse_moved)
        self.plot.scene().sigMouseClicked.connect(self._measure_clicked)
        self._measurement_points: list[int] = []
        self._last_observation_index: int | None = None
        self._dates=np.array([])
        self._session_mapping: SessionAxisMapping | None = None
        self._frame = pd.DataFrame()
        self._candlestick_mode = False
        self.latest_value_marker: pg.InfiniteLine | None = None
        # A fitted range belongs to the selected index/period, not to the window
        # geometry. Track an intentional ViewBox interaction separately so a
        # resize cannot turn a 120-session view back into an unbounded auto-range.
        self._manual_view = False
        self._applying_range = False
        self._fit_timer = QtCore.QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_visible_ranges)
        self.plot.getViewBox().sigRangeChangedManually.connect(self._manual_range_changed)
        for widget in (self.index, self.period):
            widget.currentTextChanged.connect(self._reset_view_and_request)
        for widget in (self.rsi, self.disparity):
            widget.currentTextChanged.connect(self._request)
        self._update_index_legend()

    def _apply_indicator_settings(self, settings: ChartIndicatorPreferences) -> None:
        self.indicator_panel.apply(settings)
        settings = self.indicator_panel.settings()
        blockers = [QtCore.QSignalBlocker(widget) for widget in (self.rsi, self.disparity)]
        self.rsi.setCurrentText(settings.rsi14_mode)
        self.disparity.setCurrentText(settings.disparity60_mode)
        del blockers
        self._request()
        self.indicator_settings_changed.emit(settings)

    def _reset_indicator_settings(self) -> None:
        self.indicator_panel.apply(DEFAULT_PREFERENCES.index_indicators)
        self._apply_indicator_settings(DEFAULT_PREFERENCES.index_indicators)

    def _sync_indicator_overlay_geometry(self, *_args) -> None:
        """Keep independent indicator views pixel-aligned with the price ViewBox."""
        bounds = self.plot.getViewBox().sceneBoundingRect()
        for view in (self._rsi_overlay_view, self._disparity_overlay_view):
            view.setGeometry(bounds)
            view.linkedViewChanged(self.plot.getViewBox(), pg.ViewBox.XAxis)

    def _clear_indicator_overlays(self) -> None:
        """Remove every overlay curve/guide/scale before applying the new state."""
        for view in (self._rsi_overlay_view, self._disparity_overlay_view):
            view.clear()
            view.hide()
        self._rsi_overlay_axis.hide()
        self._disparity_overlay_axis.hide()
        self._overlay_items = {}
        self._overlay_guides = {}

    @staticmethod
    def _reference_line(position: float, label: str, color: str) -> pg.InfiniteLine:
        pen_color = QtGui.QColor(color)
        pen_color.setAlpha(145)
        return pg.InfiniteLine(
            pos=position,
            angle=0,
            movable=False,
            pen=pg.mkPen(pen_color, width=1, style=QtCore.Qt.DashLine),
            label=label,
            labelOpts={"position": 0.08, "color": color},
        )

    def _render_indicator_overlays(
        self, x: np.ndarray, mapping: SessionAxisMapping,
    ) -> None:
        """Render indicators over price time positions on independent Y mappings."""
        if self.rsi.currentText() == "Overlay" and "rsi14" in self._frame:
            label, color, width = INDEX_CURVE_STYLES["rsi14"]
            self._rsi_overlay_view.show()
            self._rsi_overlay_axis.show()
            self._overlay_items["rsi14"] = _plot_continuous_line(
                self._rsi_overlay_view, x, self._frame["rsi14"], color=color,
                width=width, name=label, mapping=mapping,
            )
            guides = (
                self._reference_line(30.0, "RSI 30", color),
                self._reference_line(70.0, "RSI 70", color),
            )
            for guide in guides:
                self._rsi_overlay_view.addItem(guide, ignoreBounds=True)
            self._overlay_guides["rsi14"] = guides
            self._rsi_overlay_view.setYRange(0.0, 100.0, padding=0)

        if self.disparity.currentText() == "Overlay" and "disparity60" in self._frame:
            label, color, width = INDEX_CURVE_STYLES["disparity60"]
            exact = pd.to_numeric(self._frame["disparity60"], errors="coerce").to_numpy(dtype=float)
            signed_distance = exact - 100.0
            self._disparity_overlay_view.show()
            self._disparity_overlay_axis.show()
            self._overlay_items["disparity60"] = _plot_continuous_line(
                self._disparity_overlay_view, x, signed_distance, color=color,
                width=width, name=f"{label} (100 대비 pp)", mapping=mapping,
            )
            baseline = self._reference_line(0.0, "0 (=100%)", color)
            self._disparity_overlay_view.addItem(baseline, ignoreBounds=True)
            self._overlay_guides["disparity60"] = (baseline,)
            finite = signed_distance[np.isfinite(signed_distance)]
            extent = max(float(np.max(np.abs(finite))) if len(finite) else 0.0, 1.0)
            margin = max(extent * .12, .35)
            self._disparity_overlay_view.setYRange(-extent - margin, extent + margin, padding=0)

        self._sync_indicator_overlay_geometry()

    def _request(self, _=None): self.request_series.emit(self.index.currentText(), self.period.currentText())

    def _reset_view_and_request(self, _=None) -> None:
        """A new series selection owns a new fitted viewport."""
        self._manual_view = False
        self._request()

    def _manual_range_changed(self, _changed_axes) -> None:
        if not self._applying_range:
            self._manual_view = True

    @staticmethod
    def _finite_range(value) -> bool:
        return (
            len(value) == 2
            and all(np.isfinite(bound) for bound in value)
            and value[0] < value[1]
        )

    def _fit_visible_ranges(self) -> None:
        """Fit selected observations without delegating to global auto-range."""
        if self._manual_view or not len(self._dates):
            return
        x_min, x_max = float(self._dates.min()), float(self._dates.max())
        x_span = max(x_max - x_min, 1.0)
        x_margin = max(.75, x_span * .02)

        price_columns = ["close", "ma5", "ma20", "ma60", "ma120"]
        if self._candlestick_mode:
            price_columns.extend(["open", "high", "low"])
        price_arrays = [
            pd.to_numeric(self._frame[column], errors="coerce").dropna().to_numpy(dtype=float)
            for column in price_columns if column in self._frame
        ]
        price_values = np.concatenate(price_arrays) if price_arrays else np.array([])
        price_values = price_values[np.isfinite(price_values)]
        if not len(price_values):
            return
        price_min, price_max = float(price_values.min()), float(price_values.max())
        price_span = max(price_max - price_min, 1.0)
        price_margin = max(price_span * .05, max(abs(price_min), abs(price_max), 1.0) * .002)
        volume_values = pd.to_numeric(
            self._frame.get("volume", pd.Series(dtype=float)), errors="coerce",
        )
        volume_values = volume_values[np.isfinite(volume_values) & (volume_values >= 0)]
        volume_max = float(volume_values.max()) if len(volume_values) else 0.0

        self._applying_range = True
        try:
            fitted_x = (x_min - x_margin, x_max + x_margin)
            for chart in (self.plot, self.volume, self.indicator):
                chart.getViewBox().enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
            self.plot.setXRange(*fitted_x, padding=0)
            self.plot.setYRange(price_min - price_margin, price_max + price_margin, padding=0)
            self.volume.setYRange(0.0, max(volume_max * 1.08, 1.0), padding=0)
        finally:
            self._applying_range = False

    def _restore_manual_ranges(self, ranges) -> bool:
        price_range, volume_range = ranges
        if not all(self._finite_range(axis) for chart_range in ranges for axis in chart_range):
            return False
        self._applying_range = True
        try:
            for chart in (self.plot, self.volume, self.indicator):
                chart.getViewBox().enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
            self.plot.setXRange(*price_range[0], padding=0)
            self.plot.setYRange(*price_range[1], padding=0)
            # X remains linked to the price panel; its own Y view is independent.
            self.volume.setYRange(*volume_range[1], padding=0)
        finally:
            self._applying_range = False
        return True

    def render(self, value: pd.DataFrame | IndexSeriesView) -> None:
        view = value if isinstance(value, IndexSeriesView) else None
        self._index_view = view
        frame = value.frame if view is not None and view.displays_values else (
            pd.DataFrame() if view is not None else value
        )
        prior_ranges = (self.plot.getViewBox().viewRange(), self.volume.getViewBox().viewRange())
        self._clear_indicator_overlays()
        self.plot.clear(); self.volume.clear(); self.indicator.clear(); self.indicator.hide()
        self.latest_value_marker = None
        self._update_index_legend()
        if frame.empty:
            self._dates = np.array([])
            self._session_mapping = None
            self._frame = pd.DataFrame()
            for axis in (self._price_axis, self._volume_axis, self._indicator_axis):
                axis.set_session_dates(())
            self._manual_view = False
            reason = (
                view.unavailable_reason
                if view is not None else "로컬 보존 시계열을 읽을 수 없습니다"
            )
            self._render_unavailable_information(reason or "현재 표시할 수 없습니다.")
            return
        try:
            mapping = _daily_session_axis_mapping(frame, ExchangeMarket.KR)
        except (KeyError, TypeError, ValueError):
            self._dates = np.array([])
            self._session_mapping = None
            self._frame = pd.DataFrame()
            for axis in (self._price_axis, self._volume_axis, self._indicator_axis):
                axis.set_session_dates(())
            self._manual_view = False
            self._render_unavailable_information("보존 시계열의 기준일을 확인할 수 없습니다")
            return
        self._session_mapping = mapping
        self._frame = frame.copy().reset_index(drop=True)
        for axis in (self._price_axis, self._volume_axis, self._indicator_axis):
            axis.set_session_dates(mapping.dates)
        x=mapping.positions
        self._dates=x
        self._render_latest_information()
        if self._candlestick_mode:
            self.plot.addItem(CandlestickItem(
                [(float(position), float(row.open), float(row.high), float(row.low), float(row.close))
                 for position, row in zip(x, self._frame.itertuples(index=False))]
            ))
        else:
            label, color, width = INDEX_CURVE_STYLES["close"]
            _plot_continuous_line(
                self.plot, x, self._frame.close, color=color, width=width,
                name=label, mapping=mapping,
            )
        for column in ("ma5", "ma20", "ma60", "ma120"):
            if not getattr(self.indicator_panel.settings(), column):
                continue
            label, color, width = INDEX_CURVE_STYLES[column]
            _plot_continuous_line(
                self.plot, x, self._frame[column], color=color, width=width,
                name=label, mapping=mapping,
            )
        settings = self.indicator_panel.settings()
        if settings.ema20 and "ema20" in self._frame:
            _plot_continuous_line(self.plot, x, self._frame["ema20"], color="#7ad151", width=1.45, name="EMA20", mapping=mapping)
        if settings.bollinger_bands and {"bollinger_upper", "bollinger_mid", "bollinger_lower"}.issubset(self._frame):
            for column, name, color in (("bollinger_upper", "BB upper", "#c792ea"), ("bollinger_mid", "BB mid", "#c792ea"), ("bollinger_lower", "BB lower", "#c792ea")):
                _plot_continuous_line(self.plot, x, self._frame[column], color=color, width=1.15, name=name, mapping=mapping)
        self._render_indicator_overlays(x, mapping)
        self.volume.setVisible(self.indicator_panel.settings().volume)
        if self.indicator_panel.settings().volume:
            self.volume.addItem(pg.BarGraphItem(x=x,height=self._frame.volume.fillna(0),width=.65,brush="#40577d"))
        panel_labels = []
        if self.rsi.currentText()=="Panel":
            label, color, width = INDEX_CURVE_STYLES["rsi14"]
            self.indicator.show(); _plot_continuous_line(self.indicator, x, self._frame.rsi14, color=color, width=width, name=label, mapping=mapping); panel_labels.append(label)
        if self.disparity.currentText()=="Panel":
            label, color, width = INDEX_CURVE_STYLES["disparity60"]
            self.indicator.show(); _plot_continuous_line(self.indicator, x, self._frame.disparity60, color=color, width=width, name=label, mapping=mapping); panel_labels.append(label)
        for mode, column, label, color, unit in (
            (settings.atr14_mode, "atr14", "ATR14", "#f78c6c", "price unit"),
            (settings.adx14_mode, "adx14", "ADX14", "#82aaff", "0-100"),
            (settings.obv_mode, "obv", "OBV", "#ffcb6b", "shares"),
            (settings.bollinger_bandwidth_mode, "bollinger_bandwidth", "BB width", "#89ddff", "%"),
        ):
            if mode == "Panel" and column in self._frame:
                self.indicator.show()
                _plot_continuous_line(self.indicator, x, self._frame[column], color=color, width=1.5, name=label, mapping=mapping)
                panel_labels.append(f"{label} ({unit})")
        if panel_labels:
            self.indicator.setLabel("left", " / ".join(panel_labels))
        if view is not None and view.displays_values:
            latest = pd.to_numeric(self._frame["close"], errors="coerce").iloc[-1]
            if pd.notna(latest):
                self.latest_value_marker = pg.InfiniteLine(
                    pos=float(latest), angle=0, movable=False,
                    pen=pg.mkPen("#2f6fb2", width=1, style=QtCore.Qt.DashLine),
                    label=f"{float(latest):,.2f}",
                    labelOpts={"position": 0.98, "color": "#dce7ff", "fill": "#2f6fb2"},
                )
                self.plot.addItem(self.latest_value_marker, ignoreBounds=True)
        self.crosshairs=[]
        for chart in (self.plot,self.volume,self.indicator):
            line=pg.InfiniteLine(angle=90,movable=False,pen=pg.mkPen("#7187aa",style=QtCore.Qt.DashLine)); chart.addItem(line,ignoreBounds=True); self.crosshairs.append(line)
        if self._manual_view and self._restore_manual_ranges(prior_ranges):
            return
        self._manual_view = False
        self._fit_visible_ranges()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if len(self._dates) and not self._manual_view:
            # Child PlotWidgets finish their geometry update after this event.
            self._fit_timer.start(0)

    def _clear_measurement(self) -> None:
        self._measurement_points = []
        self.measurement.setText("측정: 두 관측값을 선택하세요")

    def _add_measurement_point(self, index: int) -> None:
        """Add one exact displayed observation to the local measurement state."""
        if self._frame.empty or self._session_mapping is None:
            return
        index = max(0, min(int(index), len(self._frame) - 1))
        self._measurement_points = (self._measurement_points + [index])[-2:]
        dates = self._session_mapping.dates
        if len(self._measurement_points) == 1:
            self.measurement.setText(f"측정 A: {dates[index].date().isoformat()}")
            return
        first, last = self._measurement_points
        start, end = self._frame.iloc[first], self._frame.iloc[last]
        change = float(end.close) - float(start.close)
        pct = change / float(start.close) * 100 if float(start.close) else float("nan")
        volume = ""
        if "volume" in self._frame and pd.notna(start.get("volume")) and pd.notna(end.get("volume")):
            volume = f" · 거래량 Δ {float(end.volume) - float(start.volume):+,.0f}"
        self.measurement.setText(
            f"측정 {dates[first].date().isoformat()} → {dates[last].date().isoformat()} "
            f"· {abs(last-first)+1} 세션 · {change:+,.2f} ({pct:+.2f}%){volume}"
        )

    def _measure_current_observation(self) -> None:
        """Keyboard-accessible counterpart to selecting a plotted observation."""
        if self._last_observation_index is not None:
            self._add_measurement_point(self._last_observation_index)

    def _measure_clicked(self, event) -> None:
        if self._frame.empty or self._session_mapping is None:
            return
        point = self.plot.plotItem.vb.mapSceneToView(event.scenePos())
        index = int(np.abs(self._dates - point.x()).argmin())
        self._add_measurement_point(index)

    def _mouse_moved(self, event) -> None:
        if not len(self._dates) or self._session_mapping is None: return
        point=self.plot.plotItem.vb.mapSceneToView(event[0]); x=point.x()
        nearest=float(self._dates[np.abs(self._dates-x).argmin()])
        for line in self.crosshairs: line.setPos(nearest)
        index = int(round(nearest))
        self._show_observation(index)

    def _show_observation(self, index: int) -> None:
        if self._session_mapping is None or self._frame.empty:
            return
        self._last_observation_index = index
        row = self._frame.iloc[index]
        details = [self._session_mapping.dates[index].date().isoformat()]
        for label, column in (("시", "open"), ("고", "high"), ("저", "low"), ("종", "close")):
            numeric = pd.to_numeric(row.get(column), errors="coerce")
            if pd.notna(numeric):
                details.append(f"{label} {float(numeric):,.2f}")
        current = pd.to_numeric(row.get("close"), errors="coerce")
        if index > 0 and pd.notna(current):
            previous = pd.to_numeric(self._frame.iloc[index - 1].get("close"), errors="coerce")
            if pd.notna(previous) and float(previous) != 0:
                change = float(current - previous)
                details.append(f"등락 {change:+,.2f} ({change / float(previous) * 100:+.2f}%)")
        volume = pd.to_numeric(row.get("volume"), errors="coerce")
        if pd.notna(volume):
            details.append(_format_exact_share_volume(volume))
        for label, column, enabled in (
            ("MA5", "ma5", True), ("MA20", "ma20", True),
            ("MA60", "ma60", True), ("MA120", "ma120", True),
            ("RSI14", "rsi14", self.rsi.currentText() != "Off"),
            ("60일 괴리율", "disparity60", self.disparity.currentText() != "Off"),
        ):
            numeric = pd.to_numeric(row.get(column), errors="coerce")
            if enabled and pd.notna(numeric):
                details.append(f"{label} {float(numeric):,.2f}")
        self.hover.setText(" · ".join(details))

    def _render_latest_information(self) -> None:
        if self._frame.empty or self._session_mapping is None:
            return
        view = self._index_view
        if view is None:
            self.index_summary.setText(
                f"{self.index.currentText()} · {self.period.currentText()} · 로컬 보존 일봉"
            )
            self.index_meta.setText("표시 자격·출처 상세 미제공")
            self._detail_text = "typed index metadata unavailable"
            self.index_detail_button.setToolTip(self._detail_text)
            self._show_observation(len(self._frame) - 1)
            return
        latest = float(pd.to_numeric(self._frame["close"], errors="coerce").iloc[-1])
        change = (
            f"{view.change:+,.2f} ({view.change_pct:+.2f}%)"
            if view.change is not None and view.change_pct is not None else "등락 N/A"
        )
        period_range = (
            f"기간 고 {view.period_high:,.2f} / 저 {view.period_low:,.2f}"
            if view.period_high is not None and view.period_low is not None else "기간 고저 N/A"
        )
        self.index_summary.setText(
            f"{view.name} · {view.exact_identity} · {view.period} · "
            f"최근 {latest:,.2f}포인트 · {change} · {period_range}"
        )
        self.index_meta.setText(
            f"일봉 · 단위 지수 포인트 · {view.reference_kst or 'KST 기준시각 미보존'} · "
            f"{_freshness_label(view.freshness)} · {view.price_basis}"
        )
        if view.freshness == "STALE" and view.unavailable_reason:
            self.index_meta.setText(
                f"STALE RETAINED HISTORY: {view.unavailable_reason} | "
                + self.index_meta.text()
            )
        self._detail_text = (
            f"dataset={view.dataset_id}\nidentity={view.exact_identity}\nsource={view.source}\n"
            f"source_session_date={view.as_of or '미확인'}\n"
            f"expected_as_of={view.expected_as_of or '미확인'}\nfreshness={view.freshness}\n"
            f"period={view.period}\nunit=index points\nprice_basis={view.price_basis}\n"
            f"reference_kst={view.reference_kst or '미보존'}"
        )
        if view.unavailable_reason:
            self._detail_text += f"\nwarning={view.unavailable_reason}"
        self.index_detail_button.setToolTip(self._detail_text)
        self._show_observation(len(self._frame) - 1)

    def _render_unavailable_information(self, reason: str) -> None:
        view = self._index_view
        identity = (
            f"{view.name} · {view.exact_identity} · {view.period}"
            if view is not None else f"{self.index.currentText()} · {self.period.currentText()}"
        )
        self.index_summary.setText(f"{identity} · 현재 숫자 표시 불가")
        freshness = _freshness_label(view.freshness) if view is not None else "확인 필요"
        self.index_meta.setText(f"{freshness} · {reason}")
        self.hover.setText("가격·등락·기간 통계·지표 숨김 · 이전 차트 상태 초기화 완료")
        self._detail_text = (
            f"dataset={view.dataset_id}\nsource={view.source}\nfreshness={view.freshness}\n"
            f"expected_as_of={view.expected_as_of or '미확인'}\nreason={reason}"
            if view is not None else f"reason={reason}"
        )
        self.index_detail_button.setToolTip(self._detail_text)

    def _visible_series_specs(self) -> tuple[tuple[str, str], ...]:
        price = (("원본 OHLC", "#ed6a5a"),) if self._candlestick_mode else ((INDEX_CURVE_STYLES["close"][0], INDEX_CURVE_STYLES["close"][1]),)
        moving = tuple(
            (INDEX_CURVE_STYLES[column][0], INDEX_CURVE_STYLES[column][1])
            for column in ("ma5", "ma20", "ma60", "ma120")
        )
        indicators = ()
        if self.rsi.currentText() != "Off":
            label = f"{INDEX_CURVE_STYLES['rsi14'][0]} ({self.rsi.currentText()})"
            if self.rsi.currentText() == "Overlay":
                label += " · 우측축 0–100"
            indicators += ((label, INDEX_CURVE_STYLES["rsi14"][1]),)
        if self.disparity.currentText() != "Off":
            label = f"{INDEX_CURVE_STYLES['disparity60'][0]} ({self.disparity.currentText()})"
            if self.disparity.currentText() == "Overlay":
                label += " · 우측축 100 대비 pp"
            indicators += ((label, INDEX_CURVE_STYLES["disparity60"][1]),)
        return price + moving + (("거래량", "#40577d"),) + indicators

    def _update_index_legend(self) -> None:
        entries = [
            f'<span style="color:{color}; font-weight:800">■</span> {label}'
            for label, color in self._visible_series_specs()
        ]
        self.index_legend.setText("범례 · " + " · ".join(entries))

    def _show_reference_details(self) -> None:
        QtWidgets.QMessageBox.information(
            self, "지수 차트 출처·기준 상세", self._detail_text or "상세 정보가 없습니다.",
        )

    def leaveEvent(self, event) -> None:
        if not self._frame.empty:
            self._show_observation(len(self._frame) - 1)
        super().leaveEvent(event)


class IndividualEquityPage(IndexPage):
    """Searchable, fail-closed provider-native security chart."""

    search_requested = QtCore.Signal(str)
    series_requested = QtCore.Signal(object, str)
    comparison_requested = QtCore.Signal(object)
    favorite_toggled = QtCore.Signal(object, str, bool)
    watchlist_item_moved = QtCore.Signal(str, object, int)
    context_identity_open_requested = QtCore.Signal(object)
    context_watchlist_quotes_requested = QtCore.Signal(object)

    def __init__(self, parent=None, *, universe: str = "KR_EQUITY"):
        if universe not in {"KR_EQUITY", "US_ETF"}:
            raise ValueError("unsupported chart universe")
        self.universe = universe
        self._selected_identity: EquityIdentity | None = None
        self._series_view: EquitySeriesView | None = None
        self._reload_preserving_accepted = False
        self._comparison_view: NormalizedBenchmarkComparisonView | None = None
        self._favorite_keys_by_list: dict[str, frozenset[tuple[str, str]]] = {}
        self._watchlist_state: WatchlistState | None = None
        self._context_watchlist_quotes: dict[tuple[str, str], WatchlistQuote] = {}
        super().__init__(parent)
        self._candlestick_mode = True
        is_us_etf = universe == "US_ETF"
        self.title.setText("미국 ETF 차트" if is_us_etf else "개별종목 차트")
        self.title.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed,
        )
        self.index_info.hide()
        self.index_legend.hide()
        self.index.hide()
        self.index_reload_button.hide()
        self.control_labels["Index"].hide()
        self.control_labels["Period"].setText("기간")
        self.control_labels["60D Disparity"].setText("60일 괴리율")
        self.plot.setAccessibleName("선택 종목 원본 일봉 캔들 및 이동평균 차트")
        self.volume.setAccessibleName("선택 종목 거래량 차트")
        self.indicator.setAccessibleName("선택 종목 RSI14 및 60일 괴리율 차트")

        search_row = QtWidgets.QHBoxLayout()
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText(
            "ETF 이름 또는 티커 (예: SPY, JEPQ)" if is_us_etf
            else "회사명 또는 종목코드 (예: 삼성전자, 005930)"
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setAccessibleName(
            "승인된 미국 ETF 이름 또는 티커 검색" if is_us_etf
            else "한국 상장회사 이름 또는 종목코드 검색"
        )
        self.search_button = QtWidgets.QPushButton("검색")
        self.search_button.setAccessibleName("종목 검색 실행")
        self.search_results = QtWidgets.QComboBox()
        self.search_results.setMinimumContentsLength(34)
        self.search_results.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.search_results.setAccessibleName("시장과 종목코드가 포함된 검색 결과")
        self.open_button = QtWidgets.QPushButton("차트 보기")
        self.open_button.setEnabled(False)
        self.search_favorite_button = QtWidgets.QPushButton("☆ 관심종목")
        self.search_favorite_button.setEnabled(False)
        self.search_favorite_button.setAccessibleName("선택 검색 결과 관심종목 추가 또는 제거")
        self.favorite_target = QtWidgets.QComboBox()
        self.favorite_target.setAccessibleName("관심종목을 추가하거나 제거할 목록")
        self.favorite_target.setMinimumContentsLength(8)
        search_row.addWidget(self.search_input, 3)
        search_row.addWidget(self.search_button)
        search_row.addWidget(self.search_results, 3)
        search_row.addWidget(self.open_button)
        search_row.addWidget(self.favorite_target)
        search_row.addWidget(self.search_favorite_button)
        self.layout().insertLayout(1, search_row)

        self.instrument_facts = QtWidgets.QFrame()
        self.instrument_facts.setObjectName("chartStartCard")
        self.instrument_facts.setAccessibleName("선택 종목 출처 안전 핵심 정보")
        facts_layout = QtWidgets.QVBoxLayout(self.instrument_facts)
        facts_layout.setContentsMargins(12, 8, 12, 8)
        facts_layout.setSpacing(2)
        self.instrument_facts_identity = QtWidgets.QLabel()
        self.instrument_facts_identity.setObjectName("sectionTitle")
        self.instrument_facts_context = QtWidgets.QLabel()
        self.instrument_facts_context.setObjectName("chartStatus")
        self.instrument_facts_risk = QtWidgets.QLabel()
        self.instrument_facts_risk.setObjectName("freshness")
        for label in (
            self.instrument_facts_identity,
            self.instrument_facts_context,
            self.instrument_facts_risk,
        ):
            label.setWordWrap(True)
            facts_layout.addWidget(label)
        self.layout().insertWidget(2, self.instrument_facts)
        self.instrument_facts.hide()

        self.timeframe = QtWidgets.QComboBox()
        self.timeframe.addItems(["주봉", "월봉"])
        self.timeframe.addItem("일봉")
        # Daily is the accepted retained source grain; aggregate views are an
        # explicit local presentation choice rather than an implicit default.
        self.timeframe.setCurrentText("일봉")
        self.timeframe.setEnabled(True)
        self.timeframe.setAccessibleName(
            "검증된 종목 일봉 주봉 월봉 선택: 이미 읽은 로컬 일봉만 집계"
        )
        self.timeframe_label = QtWidgets.QLabel("주기")
        self.timeframe_label.setBuddy(self.timeframe)
        self.controls.insertWidget(0, self.timeframe_label)
        self.controls.insertWidget(1, self.timeframe)
        self.timeframe_aggregate_status = QtWidgets.QLabel("진행 중 집계")
        self.timeframe_aggregate_status.setObjectName("chartStatus")
        self.timeframe_aggregate_status.setAccessibleName("선택 종목 주기 집계 상태")
        self.timeframe_aggregate_status.setToolTip(
            "최신 주봉 또는 월봉은 기준일까지의 보존 일봉만 집계한 진행 중 막대입니다."
        )
        self.timeframe_aggregate_status.hide()
        self.controls.insertWidget(2, self.timeframe_aggregate_status)
        self.intraday_note = QtWidgets.QLabel(
            "일봉·주봉·월봉 지원 · 승인된 로컬 미국 ETF 일봉만 집계" if is_us_etf
            else "일봉·주봉·월봉 지원 · 15분봉 미지원 및 검증된 종목별 장중 계약 없음"
        )
        self.intraday_note.setObjectName("chartStatus")
        self.intraday_note.setToolTip("지원되지 않는 장중 주기는 임의 수집하거나 일봉에서 합성하지 않습니다.")
        self.controls.addWidget(self.intraday_note)
        self.reload_button = QtWidgets.QPushButton("다시 읽기")
        self.reload_button.setEnabled(False)
        self.controls.addWidget(self.reload_button)
        self.chart_favorite_button = QtWidgets.QPushButton("☆ 관심종목")
        self.chart_favorite_button.setEnabled(False)
        self.chart_favorite_button.setAccessibleName("현재 차트 종목 관심종목 추가 또는 제거")
        self.controls.addWidget(self.chart_favorite_button)
        self.large_chart_button = QtWidgets.QPushButton("큰 차트")
        self.large_chart_button.setCheckable(True)
        self.large_chart_button.setAccessibleName("선택과 지표를 보존하는 큰 차트 모드")
        self.large_chart_button.toggled.connect(self._set_large_chart_mode)
        self.controls.addWidget(self.large_chart_button)

        self.summary = QtWidgets.QLabel(
            "ETF를 검색한 뒤 티커·발행사·노출·설정일을 확인해 선택하세요."
            if is_us_etf else "종목을 검색한 뒤 시장·종목코드를 확인해 선택하세요."
        )
        self.summary.setObjectName("sectionTitle")
        self.summary.setWordWrap(True)
        self.status = QtWidgets.QLabel(
            "USD 공급자 원본(미조정) OHLCV만 표시 · 조정주가/총수익/Backtest/기업행사 마커 미제공"
            if is_us_etf else
            "원본(미조정) OHLCV만 표시 · 조정주가와 기업행사 마커는 검증 전 제공하지 않음"
        )
        self.status.setObjectName("freshness")
        self.status.setWordWrap(True)
        self.legend = QtWidgets.QLabel(
            "범례 · 캔들(상승 빨강/하락 파랑) · MA5 하늘 · MA20 노랑 · MA60 빨강 · MA120 보라"
        )
        self.legend.setObjectName("chartStatus")
        self.layout().insertWidget(3, self.summary)
        self.layout().insertWidget(4, self.status)
        self.layout().insertWidget(5, self.legend)
        self.workspace = QtWidgets.QTabWidget()
        self.workspace.setAccessibleName("선택 종목 읽기 전용 상세 작업공간")
        self.workspace_info = QtWidgets.QLabel("선택한 정확한 종목의 로컬 식별정보만 표시합니다.")
        self.workspace_dividend = QtWidgets.QLabel("배당: 승인된 로컬 계약이 없어 숫자 표시 불가")
        self.workspace_option = QtWidgets.QLabel("옵션: 승인된 로컬 계약이 없어 숫자 표시 불가")
        self.workspace_watchlist = QtWidgets.QLabel("관심종목은 정확한 시장·종목 식별정보만 사용합니다.")
        for label, title in ((self.workspace_info, "정보"), (self.workspace_dividend, "배당"), (self.workspace_option, "옵션 가능 여부"), (self.workspace_watchlist, "관심종목")):
            page = QtWidgets.QWidget(); box = QtWidgets.QVBoxLayout(page); box.addWidget(label); box.addStretch(); self.workspace.addTab(page, title)
        self.layout().insertWidget(6, self.workspace)
        self.context_watchlist_rail = QtWidgets.QGroupBox("관심종목 · 최근 흐름")
        self.context_watchlist_rail.setAccessibleName("현재 차트 옆 관심종목과 최근 가격 흐름")
        self.context_watchlist_rail.setMinimumWidth(210)
        self.context_watchlist_rail.setMaximumWidth(390)
        rail_layout = QtWidgets.QVBoxLayout(self.context_watchlist_rail)
        self.context_watchlist_selector = QtWidgets.QComboBox()
        self.context_watchlist_selector.setAccessibleName("차트 컨텍스트 관심종목 목록")
        self.context_watchlist_items = QtWidgets.QListWidget()
        self.context_watchlist_items.setAccessibleName("정확한 식별정보와 최근 흐름 관심종목")
        self.context_watchlist_items.setAlternatingRowColors(True)
        self.context_watchlist_items.setWordWrap(True)
        rail_actions = QtWidgets.QGridLayout()
        self.context_watchlist_add = QtWidgets.QPushButton("현재 종목 추가")
        self.context_watchlist_add.setAccessibleName(
            "현재 차트 종목을 선택한 관심종목 목록에 추가"
        )
        self.context_watchlist_open = QtWidgets.QPushButton("열기")
        self.context_watchlist_remove = QtWidgets.QPushButton("제거")
        self.context_watchlist_up = QtWidgets.QPushButton("위로")
        self.context_watchlist_down = QtWidgets.QPushButton("아래로")
        rail_actions.addWidget(self.context_watchlist_add, 0, 0, 1, 2)
        rail_actions.addWidget(self.context_watchlist_open, 0, 2)
        rail_actions.addWidget(self.context_watchlist_remove, 1, 0)
        rail_actions.addWidget(self.context_watchlist_up, 1, 1)
        rail_actions.addWidget(self.context_watchlist_down, 1, 2)
        rail_actions.setColumnStretch(3, 1)
        rail_layout.addWidget(self.context_watchlist_selector)
        rail_layout.addWidget(self.context_watchlist_items)
        rail_layout.addLayout(rail_actions)
        self.layout().insertWidget(7, self.context_watchlist_rail)
        self.context_watchlist_selector.currentIndexChanged.connect(
            self._context_watchlist_changed
        )
        self.context_watchlist_open.clicked.connect(self._open_context_watchlist_item)
        self.context_watchlist_items.itemActivated.connect(
            lambda _item: self._open_context_watchlist_item()
        )
        self.context_watchlist_add.clicked.connect(
            self._add_current_to_context_watchlist
        )
        self.context_watchlist_remove.clicked.connect(self._remove_context_watchlist_item)
        self.context_watchlist_up.clicked.connect(lambda: self._move_context_watchlist_item(-1))
        self.context_watchlist_down.clicked.connect(lambda: self._move_context_watchlist_item(1))

        self.comparison_toggle = QtWidgets.QPushButton("공통 기준 100 비교")
        self.comparison_toggle.setCheckable(True)
        self.comparison_toggle.setEnabled(False)
        self.comparison_toggle.setAccessibleName("선택 종목과 정확한 벤치마크의 공통 기준 100 비교")
        self.controls.addWidget(self.comparison_toggle)
        # Keep the timeframe and exact measurement readable in a normal desktop
        # viewport. The dense indicator choices remain available on their own
        # line rather than forcing the local-only timeframe controls beyond the
        # right edge.
        self.controls.removeWidget(self.indicator_panel)
        self.controls.removeWidget(self.intraday_note)
        for widget in (
            self.reload_button,
            self.chart_favorite_button,
            self.large_chart_button,
            self.comparison_toggle,
        ):
            self.controls.removeWidget(widget)
        self._equity_indicator_row = QtWidgets.QHBoxLayout()
        self._equity_indicator_row.setContentsMargins(0, 0, 0, 0)
        self._equity_indicator_row.addWidget(self.indicator_panel)
        self._equity_indicator_row.addStretch()
        self._equity_action_row = QtWidgets.QHBoxLayout()
        self._equity_action_row.setContentsMargins(0, 0, 0, 0)
        self._equity_action_row.addWidget(self.intraday_note)
        self._equity_action_row.addWidget(self.reload_button)
        self._equity_action_row.addWidget(self.chart_favorite_button)
        self._equity_action_row.addWidget(self.large_chart_button)
        self._equity_action_row.addWidget(self.comparison_toggle)
        self._equity_action_row.addStretch()
        self.layout().insertLayout(3, self._equity_indicator_row)
        self.layout().insertLayout(4, self._equity_action_row)
        self.comparison_panel = QtWidgets.QFrame()
        comparison_layout = QtWidgets.QVBoxLayout(self.comparison_panel)
        comparison_layout.setContentsMargins(0, 3, 0, 0)
        comparison_layout.setSpacing(2)
        self.comparison_summary = QtWidgets.QLabel()
        self.comparison_summary.setObjectName("chartStatus")
        self.comparison_summary.setWordWrap(True)
        comparison_layout.addWidget(self.comparison_summary)
        self._comparison_axis = SessionDateAxisItem(
            orientation="bottom", labels_visible=False,
        )
        self.comparison_plot = pg.PlotWidget(axisItems={"bottom": self._comparison_axis})
        self.comparison_plot.setMaximumHeight(165)
        self.comparison_plot.setXLink(self.plot)
        self.comparison_plot.showGrid(x=True, y=True, alpha=.15)
        self.comparison_plot.getAxis("left").setLabel("공통 기준 100")
        self.comparison_plot.setAccessibleName("공통 날짜 기준 100 정규화 벤치마크 비교 차트")
        self._comparison_crosshair = pg.InfiniteLine(
            angle=90, movable=False, pen=pg.mkPen("#7187aa", style=QtCore.Qt.DashLine),
        )
        self.comparison_plot.addItem(self._comparison_crosshair, ignoreBounds=True)
        comparison_layout.addWidget(self.comparison_plot)
        self.layout().addWidget(self.comparison_panel)
        self.comparison_panel.hide()

        self.empty_state = QtWidgets.QFrame()
        self.empty_state.setObjectName("chartStartCard")
        self.empty_state.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed,
        )
        self.empty_state.setAccessibleName(
            "미국 ETF 차트 검색 시작" if is_us_etf else "한국 종목 차트 검색 시작"
        )
        empty_layout = QtWidgets.QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(18, 16, 18, 16)
        empty_layout.setSpacing(8)
        self.empty_state_title = QtWidgets.QLabel(
            "ETF를 검색해 차트를 시작하세요"
            if is_us_etf else "종목을 검색해 차트를 시작하세요"
        )
        self.empty_state_title.setObjectName("sectionTitle")
        self.empty_state_body = QtWidgets.QLabel(
            "승인된 로컬 미국 ETF 목록에서 정확한 티커를 먼저 확인합니다. "
            "검색만으로 가격 시계열을 자동 조회하지 않습니다."
            if is_us_etf else
            "회사명 또는 6자리 종목코드로 정확한 시장·종목을 먼저 확인합니다. "
            "검색만으로 가격 시계열을 자동 조회하지 않습니다."
        )
        self.empty_state_body.setWordWrap(True)
        empty_actions = QtWidgets.QHBoxLayout()
        self.guided_search_button = QtWidgets.QPushButton(
            "예시 검색 · SOXX" if is_us_etf else "예시 검색 · 삼성전자 005930"
        )
        self.guided_search_button.setAccessibleName(
            "SOXX를 로컬 ETF 목록에서 검색"
            if is_us_etf else "삼성전자 005930을 로컬 종목 목록에서 검색"
        )
        self.guided_search_button.clicked.connect(
            lambda _checked=False, query=("SOXX" if is_us_etf else "005930"):
            self._start_guided_search(query)
        )
        self.empty_direct_search_button = QtWidgets.QPushButton("직접 검색어 입력")
        self.empty_direct_search_button.setAccessibleName("종목 검색 입력란으로 이동")
        self.empty_direct_search_button.clicked.connect(self._focus_search_input)
        empty_actions.addWidget(self.guided_search_button)
        empty_actions.addWidget(self.empty_direct_search_button)
        empty_actions.addStretch()
        empty_layout.addWidget(self.empty_state_title)
        empty_layout.addWidget(self.empty_state_body)
        empty_layout.addLayout(empty_actions)
        self.search_feedback = QtWidgets.QLabel()
        self.search_feedback.setObjectName("freshness")
        self.search_feedback.setAccessibleName("종목 검색 결과 상태")
        self.search_feedback.setWordWrap(True)
        self.search_feedback.hide()
        self.layout().insertWidget(2, self.search_feedback)
        self.layout().insertWidget(3, self.empty_state)

        self._chart_workspace_widgets = (
            self.instrument_facts,
            self.timeframe_label,
            self.timeframe,
            self.control_labels["Period"],
            self.period,
            self.indicator_panel,
            self.measurement,
            self.clear_measurement_button,
            self.add_measurement_button,
            self.detach_button,
            self.intraday_note,
            self.reload_button,
            self.favorite_target,
            self.search_favorite_button,
            self.chart_favorite_button,
            self.large_chart_button,
            self.comparison_toggle,
            self.summary,
            self.status,
            self.legend,
            self.workspace,
            self.plot,
            self.volume,
            self.hover,
        )

        self.search_input.returnPressed.connect(self._request_search)
        self.search_button.clicked.connect(self._request_search)
        self.search_results.currentIndexChanged.connect(self._search_selection_changed)
        self.favorite_target.currentIndexChanged.connect(self._sync_favorite_controls)
        self.open_button.clicked.connect(self._load_selected)
        self.search_favorite_button.clicked.connect(self._toggle_search_favorite)
        self.chart_favorite_button.clicked.connect(self._toggle_chart_favorite)
        self.comparison_toggle.toggled.connect(self._comparison_toggled)
        self.reload_button.clicked.connect(self._reload_selected)
        self.timeframe.currentTextChanged.connect(self._request)
        self.search_results.activated.connect(lambda _index: self._load_selected())
        QtWidgets.QWidget.setTabOrder(self.search_input, self.search_button)
        QtWidgets.QWidget.setTabOrder(self.search_button, self.search_results)
        QtWidgets.QWidget.setTabOrder(self.search_results, self.open_button)
        self._clear_search_results()
        self._install_context_watchlist_sidebar()
        self._set_chart_workspace_visible(False)

    def _render_instrument_facts(self, view: EquitySeriesView) -> None:
        facts: InstrumentFactsView = instrument_facts_view(view)
        self.instrument_facts_identity.setText(facts.identity_line)
        self.instrument_facts_context.setText(
            f"{facts.market_line}\n{facts.source_line}"
        )
        self.instrument_facts_risk.setText(
            f"{facts.risk_line}\n{facts.unsupported_line}"
        )
        self.instrument_facts.setProperty(
            "displaysPriceFacts", facts.displays_price_facts,
        )
        self.instrument_facts.setToolTip(
            "정확히 보존된 식별·출처 필드만 표시합니다. 외부 조회나 환산을 수행하지 않습니다."
        )

    def _install_context_watchlist_sidebar(self) -> None:
        """Move the existing rail beside the chart without changing data ownership."""

        root = self.layout()
        captured: list[tuple[QtWidgets.QLayoutItem, int]] = []
        while root.count():
            stretch = root.stretch(0)
            item = root.takeAt(0)
            if item.widget() is self.context_watchlist_rail:
                continue
            captured.append((item, stretch))

        content = QtWidgets.QWidget()
        content.setObjectName("individualEquityChartContent")
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(root.spacing())
        for item, stretch in captured:
            if item.widget() is not None:
                content_layout.addWidget(item.widget(), stretch)
            elif item.layout() is not None:
                content_layout.addLayout(item.layout(), stretch)
            elif item.spacerItem() is not None:
                content_layout.addItem(item.spacerItem())

        self._chart_content_layout = content_layout
        self.context_watchlist_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.context_watchlist_splitter.setAccessibleName(
            "개별종목 차트와 관심종목 최근 흐름 분할 보기"
        )
        self.context_watchlist_splitter.setChildrenCollapsible(False)
        self.context_watchlist_splitter.addWidget(content)
        self.context_watchlist_splitter.addWidget(self.context_watchlist_rail)
        self.context_watchlist_splitter.setStretchFactor(0, 1)
        self.context_watchlist_splitter.setStretchFactor(1, 0)
        self.context_watchlist_splitter.setSizes([1080, 330])
        root.addWidget(self.context_watchlist_splitter, 1)

    def _set_chart_workspace_visible(self, visible: bool) -> None:
        """Show analysis tools only after one exact identity is selected."""

        self.empty_state.setVisible(not visible)
        layout = self._chart_content_layout
        layout.setAlignment(
            QtCore.Qt.Alignment() if visible else QtCore.Qt.AlignTop
        )
        for widget, stretch in (
            (self.plot, 3),
            (self.volume, 1),
            (self.indicator, 1),
        ):
            index = layout.indexOf(widget)
            if index >= 0:
                layout.setStretch(index, stretch if visible else 0)
        for widget in self._chart_workspace_widgets:
            widget.setVisible(visible)
        if not visible:
            self.timeframe_aggregate_status.hide()
            self.indicator.hide()
            self.comparison_panel.hide()

    def _start_guided_search(self, query: str) -> None:
        """Run only the existing local identity search; never select/load."""

        self.search_input.setText(query)
        self._request_search()

    def _focus_search_input(self) -> None:
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _set_large_chart_mode(self, enabled: bool) -> None:
        """Expand locally without fetching or changing the exact selected identity."""
        if enabled:
            self._workspace_focus = QtWidgets.QApplication.focusWidget()
        for widget in (self.workspace, self.summary, self.status, self.legend):
            widget.setVisible(not enabled)
        if enabled:
            self.plot.setFocus()
        elif getattr(self, "_workspace_focus", None) is not None:
            self._workspace_focus.setFocus()

    def _request_search(self) -> None:
        query = self.search_input.text().strip()
        self.search_feedback.hide()
        self.search_feedback.clear()
        self._clear_search_results("검색 중…")
        self.search_button.setEnabled(False)
        self.search_requested.emit(query)

    def _clear_search_results(self, placeholder: str = "검색 결과") -> None:
        self.search_results.clear()
        self.search_results.addItem(placeholder, None)
        self.search_results.setCurrentIndex(0)
        self.open_button.setEnabled(False)
        self.search_favorite_button.setEnabled(False)

    @QtCore.Slot(object)
    def render_search(self, view: EquitySearchView) -> None:
        if view.query != self.search_input.text().strip():
            return
        self.search_button.setEnabled(True)
        self.search_results.clear()
        self.search_results.addItem("시장·종목코드를 확인해 선택", None)
        for identity in view.matches:
            self.search_results.addItem(identity.display_label, identity)
        self.search_results.setCurrentIndex(0)
        self.open_button.setEnabled(False)
        self._sync_favorite_controls()
        if view.unavailable_reason:
            self.search_feedback.setText(view.unavailable_reason)
            self.search_feedback.show()
        elif not view.matches:
            self.search_feedback.setText("일치하는 로컬 검색 결과가 없습니다.")
            self.search_feedback.show()
        else:
            self.search_feedback.hide()
            self.search_feedback.clear()

    def _load_selected(self) -> None:
        identity = self.search_results.currentData()
        if not isinstance(identity, EquityIdentity):
            return
        self._request_identity(identity)

    def _reload_selected(self) -> None:
        if self._selected_identity is not None:
            self._request_identity(self._selected_identity)

    def _request_identity(self, identity: EquityIdentity) -> None:
        preserve_accepted = (
            self._selected_identity is not None
            and self._selected_identity.key == identity.key
            and self._series_view is not None
            and self._series_view.displays_values
        )
        self._selected_identity = identity
        self.begin_series(identity, preserve_accepted=preserve_accepted)
        self.series_requested.emit(identity, self.period.currentText())

    def set_watchlists(self, state: WatchlistState) -> None:
        self._watchlist_state = state
        current = self.favorite_target.currentData()
        blocker = QtCore.QSignalBlocker(self.favorite_target)
        self.favorite_target.clear()
        self._favorite_keys_by_list = {}
        for watchlist in state.lists:
            self.favorite_target.addItem(watchlist.name, watchlist.list_id)
            self._favorite_keys_by_list[watchlist.list_id] = frozenset(item.key for item in watchlist.items)
        target = self.favorite_target.findData(current or DEFAULT_LIST_ID)
        self.favorite_target.setCurrentIndex(max(0, target))
        del blocker
        self._sync_favorite_controls()
        rail_current = self.context_watchlist_selector.currentData()
        rail_blocker = QtCore.QSignalBlocker(self.context_watchlist_selector)
        self.context_watchlist_selector.clear()
        for watchlist in state.lists:
            self.context_watchlist_selector.addItem(watchlist.name, watchlist.list_id)
        rail_target = self.context_watchlist_selector.findData(rail_current or DEFAULT_LIST_ID)
        self.context_watchlist_selector.setCurrentIndex(max(0, rail_target))
        del rail_blocker
        self._render_context_watchlist_items()
        self._sync_favorite_controls()

    def _context_watchlist_changed(self, _index: int = -1) -> None:
        self._context_watchlist_quotes = {}
        self._render_context_watchlist_items()
        identities = self.context_watchlist_identities()
        if identities:
            self.context_watchlist_quotes_requested.emit(identities)

    def context_watchlist_identities(self) -> tuple[EquityIdentity, ...]:
        if self._watchlist_state is None:
            return ()
        try:
            watchlist = self._watchlist_state.list_by_id(
                str(self.context_watchlist_selector.currentData())
            )
        except StopIteration:
            return ()
        return tuple(item.identity for item in watchlist.items)

    def render_context_watchlist_quotes(
        self,
        identities: tuple[EquityIdentity, ...],
        quotes: tuple[WatchlistQuote, ...],
    ) -> None:
        if tuple(identity.key for identity in identities) != tuple(
            identity.key for identity in self.context_watchlist_identities()
        ):
            return
        self._context_watchlist_quotes = {
            quote.identity.key: quote for quote in quotes
        }
        self._render_context_watchlist_items()

    def _context_watchlist_selection(self) -> tuple[str, EquityIdentity] | None:
        item = self.context_watchlist_items.currentItem()
        identity = item.data(QtCore.Qt.UserRole) if item is not None else None
        list_id = self.context_watchlist_selector.currentData()
        if isinstance(identity, EquityIdentity) and isinstance(list_id, str):
            return list_id, identity
        return None

    def _render_context_watchlist_items(self) -> None:
        self.context_watchlist_items.clear()
        if self._watchlist_state is None:
            return
        try:
            watchlist = self._watchlist_state.list_by_id(
                str(self.context_watchlist_selector.currentData())
            )
        except StopIteration:
            return
        for item in watchlist.items:
            identity = item.identity
            quote = self._context_watchlist_quotes.get(identity.key)
            row = QtWidgets.QListWidgetItem(
                self._context_watchlist_text(identity, quote)
            )
            row.setData(QtCore.Qt.UserRole, identity)
            row.setSizeHint(QtCore.QSize(270, 70))
            if quote is not None:
                row.setToolTip(
                    quote.unavailable_reason
                    or f"{quote.reference_kst or '기준시각 미보존'} · {_freshness_label(quote.freshness)}"
                )
            self.context_watchlist_items.addItem(row)

    @staticmethod
    def _recent_flow_sparkline(values: tuple[float, ...]) -> str:
        if len(values) < 2:
            return ""
        levels = "▁▂▃▄▅▆▇█"
        low, high = min(values), max(values)
        if high == low:
            return "▄" * len(values)
        return "".join(
            levels[min(len(levels) - 1, int((value - low) / (high - low) * len(levels)))]
            for value in values
        )

    @classmethod
    def _context_watchlist_text(
        cls,
        identity: EquityIdentity,
        quote: WatchlistQuote | None,
    ) -> str:
        heading = f"{identity.name} · {identity.symbol}"
        if quote is None:
            return f"{heading}\n로컬 최근 흐름 확인 중…"
        if not quote.displays_values:
            return (
                f"{heading}\n가격·흐름 숨김 · {_freshness_label(quote.freshness)}"
            )
        if identity.is_us_etf:
            price = f"${quote.price:,.2f}"
        else:
            price = f"{quote.price:,.0f}원"
        daily = (
            f"{quote.change_pct:+.2f}%"
            if quote.change_pct is not None else "당일 N/A"
        )
        flow_parts = []
        if quote.five_session_pct is not None:
            flow_parts.append(f"5거래일 {quote.five_session_pct:+.2f}%")
        if quote.recent_period_pct is not None:
            flow_parts.append(
                f"최근 {len(quote.recent_closes)}개 {quote.recent_period_pct:+.2f}%"
            )
        sparkline = cls._recent_flow_sparkline(quote.recent_closes)
        flow = " · ".join(flow_parts) or "최근 흐름 N/A"
        return f"{heading}\n{price} · 당일 {daily}\n{flow}  {sparkline}".rstrip()

    def _open_context_watchlist_item(self) -> None:
        selected = self._context_watchlist_selection()
        if selected is not None:
            self.context_identity_open_requested.emit(selected[1])

    def _add_current_to_context_watchlist(self) -> None:
        identity = self._selected_identity
        list_id = self.context_watchlist_selector.currentData()
        if not isinstance(identity, EquityIdentity) or not isinstance(list_id, str):
            return
        favorite_keys = self._favorite_keys_by_list.get(list_id, frozenset())
        if identity.key not in favorite_keys:
            self.favorite_toggled.emit(identity, list_id, True)

    def _remove_context_watchlist_item(self) -> None:
        selected = self._context_watchlist_selection()
        if selected is not None:
            list_id, identity = selected
            self.favorite_toggled.emit(identity, list_id, False)

    def _move_context_watchlist_item(self, offset: int) -> None:
        selected = self._context_watchlist_selection()
        if selected is not None:
            list_id, identity = selected
            self.watchlist_item_moved.emit(list_id, identity.key, offset)

    def _current_favorite_keys(self) -> frozenset[tuple[str, str]]:
        return self._favorite_keys_by_list.get(str(self.favorite_target.currentData() or DEFAULT_LIST_ID), frozenset())

    def _search_selection_changed(self, index: int) -> None:
        selected = self.search_results.itemData(index) if index >= 0 else None
        self.open_button.setEnabled(isinstance(selected, EquityIdentity))
        self._sync_favorite_controls()

    def _sync_favorite_controls(self) -> None:
        selected = self.search_results.currentData()
        selected_identity = selected if isinstance(selected, EquityIdentity) else None
        favorite_keys = self._current_favorite_keys()
        self.search_favorite_button.setEnabled(selected_identity is not None)
        self.search_favorite_button.setText(
            "★ 관심종목" if selected_identity is not None and selected_identity.key in favorite_keys
            else "☆ 관심종목"
        )
        self.chart_favorite_button.setEnabled(self._selected_identity is not None)
        self.chart_favorite_button.setText(
            "★ 관심종목" if self._selected_identity is not None and self._selected_identity.key in favorite_keys
            else "☆ 관심종목"
        )
        rail_list_id = self.context_watchlist_selector.currentData()
        rail_keys = self._favorite_keys_by_list.get(
            str(rail_list_id), frozenset(),
        )
        self.context_watchlist_add.setEnabled(
            self._selected_identity is not None
            and isinstance(rail_list_id, str)
            and self._selected_identity.key not in rail_keys
        )

    def _toggle_search_favorite(self) -> None:
        identity = self.search_results.currentData()
        if isinstance(identity, EquityIdentity):
            list_id = str(self.favorite_target.currentData() or DEFAULT_LIST_ID)
            self.favorite_toggled.emit(identity, list_id, identity.key not in self._current_favorite_keys())

    def _toggle_chart_favorite(self) -> None:
        if self._selected_identity is not None:
            list_id = str(self.favorite_target.currentData() or DEFAULT_LIST_ID)
            self.favorite_toggled.emit(
                self._selected_identity,
                list_id,
                self._selected_identity.key not in self._current_favorite_keys(),
            )

    def _clear_comparison(self, *, keep_toggle: bool = False) -> None:
        self._comparison_view = None
        for item in tuple(getattr(self, "_comparison_items", ())):
            self.comparison_plot.removeItem(item)
        self._comparison_items = ()
        self.comparison_summary.clear()
        self.comparison_panel.hide()
        if not keep_toggle:
            blocker = QtCore.QSignalBlocker(self.comparison_toggle)
            self.comparison_toggle.setChecked(False)
            del blocker

    def _comparison_toggled(self, enabled: bool) -> None:
        if not enabled:
            self._clear_comparison(keep_toggle=True)
            return
        if self._series_view is None:
            self._clear_comparison()
            return
        self.comparison_summary.setText("공통 eligible 날짜와 정확한 벤치마크를 로컬에서 확인하는 중입니다.")
        self.comparison_panel.show()
        self.comparison_plot.hide()
        self.comparison_requested.emit(self._series_view)

    @QtCore.Slot(object)
    def render_comparison(self, view: NormalizedBenchmarkComparisonView) -> None:
        if (
            self._series_view is None
            or view.target.key != self._series_view.identity.key
            or view.period != self._series_view.period
            or not self.comparison_toggle.isChecked()
        ):
            return
        self._clear_comparison(keep_toggle=True)
        self._comparison_view = view
        self.comparison_panel.show()
        detail = (
            f"benchmark={view.benchmark_id}\nbenchmark_label={view.benchmark_label}\n"
            f"common_start={view.common_start or 'N/A'}\nperiod={view.period}\n"
            f"currency={view.currency}\ntarget_price_basis={view.target_price_basis}\n"
            f"benchmark_price_basis={view.benchmark_price_basis}\n"
            f"target_as_of={view.target_as_of or 'N/A'}\nbenchmark_as_of={view.benchmark_as_of or 'N/A'}\n"
            f"target_freshness={view.target_freshness}\nbenchmark_freshness={view.benchmark_freshness}\n"
            "exact-date inner join only; no holiday forward-fill; descriptive comparison only"
        )
        if not view.displays_values:
            self.comparison_plot.hide()
            self.comparison_summary.setText(view.unavailable_reason or "비교 숫자를 표시할 수 없습니다.")
            self.comparison_summary.setToolTip(detail)
            return
        x = view.frame["target_position"].to_numpy(dtype=float)
        target_values = view.frame["target_normalized"].to_numpy(dtype=float)
        benchmark_values = view.frame["benchmark_normalized"].to_numpy(dtype=float)
        items = (
            self.comparison_plot.plot(x, target_values, pen=pg.mkPen("#53d8fb", width=2), name="selected"),
            self.comparison_plot.plot(x, benchmark_values, pen=pg.mkPen("#f6c945", width=2), name="benchmark"),
            pg.InfiniteLine(pos=100.0, angle=0, movable=False, pen=pg.mkPen("#7187aa", style=QtCore.Qt.DashLine)),
        )
        self.comparison_plot.addItem(items[2], ignoreBounds=True)
        self._comparison_items = items
        if self._session_mapping is not None:
            self._comparison_axis.set_session_dates(self._session_mapping.dates)
        target_change = float(target_values[-1] - 100.0)
        benchmark_change = float(benchmark_values[-1] - 100.0)
        self.comparison_summary.setText(
            f"공통 기준 100 · 시작 {view.common_start} · {view.target.symbol} {target_change:+.2f}%p · "
            f"{view.benchmark_label} {benchmark_change:+.2f}%p · {view.currency} · 원본 가격 / 지수 레벨"
        )
        self.comparison_summary.setToolTip(detail)
        self.comparison_plot.show()

    def _request(self, _=None) -> None:
        if self._series_view is not None and self._series_view.displays_values:
            # Timeframe changes are a local presentation transform of this exact
            # already-loaded view. They neither request another series nor
            # preserve coordinates/measurements whose displayed observations
            # have changed.
            self._manual_view = False
            self._clear_measurement()
            frame = _aggregate_ohlc(
                self._series_view.frame,
                self.timeframe.currentText(),
                reference_date=(
                    self._series_view.expected_as_of
                    or self._series_view.as_of
                ),
                market=(ExchangeMarket.US if self._series_view.identity.is_us_etf else ExchangeMarket.KR),
            )
            if self.timeframe.currentText() != "일봉":
                for column in ("ma5", "ma20", "ma60", "ma120", "rsi14", "disparity60", "ema20", "bollinger_upper", "bollinger_mid", "bollinger_lower", "atr14", "adx14", "obv", "bollinger_bandwidth"):
                    frame[column] = np.nan
            IndexPage.render(self, frame)
            for line in self.crosshairs:
                line.hide()
            self._set_timeframe_aggregate_status(frame)
            self._show_latest_observation()

    def _set_timeframe_aggregate_status(self, frame: pd.DataFrame) -> None:
        """Expose an in-progress aggregate without relabelling completed bars."""
        in_progress = (
            self.timeframe.currentText() != "일봉"
            and "incomplete_period" in frame
            and not frame.empty
            and bool(frame["incomplete_period"].iloc[-1])
        )
        self.timeframe_aggregate_status.setVisible(in_progress)

    def _reset_view_and_request(self, _=None) -> None:
        self._manual_view = False
        if self._selected_identity is not None:
            self._request_identity(self._selected_identity)

    def _restore_measurement_points(self, points: tuple[int, ...]) -> None:
        """Recompute a retained selection against the newly rendered frame."""

        self._clear_measurement()
        if not points or any(index < 0 or index >= len(self._frame) for index in points):
            return
        for index in points:
            self._add_measurement_point(index)

    def begin_series(
        self, identity: EquityIdentity, *, preserve_accepted: bool = False,
    ) -> None:
        self._set_chart_workspace_visible(True)
        self.instrument_facts.hide()
        self._selected_identity = identity
        self._reload_preserving_accepted = bool(
            preserve_accepted
            and self._series_view is not None
            and self._series_view.displays_values
            and self._series_view.identity.key == identity.key
        )
        if self._reload_preserving_accepted:
            self.status.setText(
                "기존 검증 차트를 유지한 채 동일 종목의 로컬 일봉을 다시 읽는 중입니다."
            )
            self.reload_button.setEnabled(False)
            self._sync_favorite_controls()
            return
        self._manual_view = False
        self._clear_measurement()
        self._series_view = None
        self._clear_comparison()
        IndexPage.render(self, pd.DataFrame())
        self.summary.setText(f"{identity.display_label} · 읽는 중…")
        self.status.setText("가격·지표·툴팁을 초기화하고 검증된 로컬 일봉을 확인하는 중입니다.")
        self.status.setToolTip("")
        self.reload_button.setEnabled(False)
        self._sync_favorite_controls()

    @QtCore.Slot(object)
    def render_series(self, view: EquitySeriesView) -> None:
        if self._selected_identity is None or view.identity.key != self._selected_identity.key:
            return
        self._set_chart_workspace_visible(True)
        self._render_instrument_facts(view)
        preserved = (
            self._reload_preserving_accepted
            and self._series_view is not None
            and self._series_view.displays_values
            and self._series_view.identity.key == view.identity.key
        )
        preserved_measurement = (
            tuple(self._measurement_points) if preserved else ()
        )
        self._reload_preserving_accepted = False
        self.reload_button.setEnabled(True)
        if preserved and not view.displays_values:
            self.status.setText(
                "새로고침 실패 · 기존 검증 차트 유지 · "
                + (view.unavailable_reason or "로컬 시계열을 확인할 수 없습니다.")
            )
            self.status.setToolTip(
                f"reload_freshness={view.freshness}\n"
                f"reload_expected={view.expected_as_of or '미확인'}\n"
                f"reload_source={view.source}\n"
                f"accepted_as_of={self._series_view.as_of or '미확인'}\n"
                "accepted chart, zoom, measurement, and comparison retained"
            )
            return
        self._series_view = view
        identity_text = f"{view.identity.name} · {view.identity.market}:{view.identity.symbol}"
        self.workspace_info.setText(
            f"{identity_text}\nsource={view.source}\nas_of={view.as_of or 'N/A'}\nfreshness={view.freshness}"
        )
        self.workspace_dividend.setText(f"{identity_text}\n배당: 승인된 로컬 계약이 없어 숫자 표시 불가")
        self.workspace_option.setText(f"{identity_text}\n옵션: 승인된 로컬 계약이 없어 숫자 표시 불가")
        self.workspace_watchlist.setText(f"{identity_text}\n관심종목은 이 정확한 identity만 추가·제거합니다.")
        if not view.displays_values:
            self.timeframe_aggregate_status.hide()
            self._clear_comparison()
            # A numeric-free comparison detail still exposes the exact benchmark
            # identity, currency, and original-price basis without reading a
            # fallback series or manufacturing a value.
            self.comparison_toggle.setEnabled(True)
            IndexPage.render(self, pd.DataFrame())
            self.summary.setText(
                f"{view.identity.display_label} · 현재 조회값 {view.current_value:,.0f}원 · 업데이트됨"
                if view.current_refresh_status == "UPDATED" and view.current_value is not None
                else view.identity.display_label
            )
            self.status.setText(view.unavailable_reason or "현재 표시할 수 없습니다.")
            self.status.setToolTip(
                f"freshness={view.freshness}\nexpected={view.expected_as_of or '미확인'}\n"
                f"source={view.source}\nprice_mode={view.price_mode}"
            )
            self.hover.setText("가격·등락·지표 숨김 · 이전 종목 상태 초기화 완료")
            if view.identity.is_us_etf:
                self.status.setToolTip(
                    self.status.toolTip()
                    + f"\nissuer={view.identity.issuer or 'not retained'}"
                    + f"\nexposure={view.identity.exposure or 'not retained'}"
                    + f"\ninception={view.identity.listing_date or 'not retained'}"
                    + f"\ncurrency={view.identity.currency or 'not retained'}"
                    + f"\nleverage={view.identity.leverage_style or 'not retained'}"
                    + f"\ndistribution={view.identity.distribution_style or 'not retained'}"
                    + f"\nidentity_source={view.identity.identity_source or 'not retained'}"
                )
            return
        IndexPage.render(self, view.frame)
        if self.timeframe.currentText() != "일봉":
            self._request()
        else:
            self.timeframe_aggregate_status.hide()
        if preserved_measurement:
            self._restore_measurement_points(preserved_measurement)
        self.comparison_toggle.setEnabled(True)
        latest = view.frame.iloc[-1]
        change_text = (
            f"{view.change:+,.0f} ({view.change_pct:+.2f}%)"
            if view.change is not None and view.change_pct is not None else "등락 N/A"
        )
        if view.identity.is_us_etf:
            change_text = (
                f"${view.change:+,.2f} ({view.change_pct:+.2f}%)"
                if view.change is not None and view.change_pct is not None else "등락 N/A"
            )
            self.summary.setText(
                f"{view.identity.name} · {view.identity.symbol} · {view.identity.issuer} · "
                f"종가 ${float(latest.close):,.2f} · {change_text} · "
                f"기간 고가 ${view.period_high:,.2f} / 저가 ${view.period_low:,.2f}\n"
                f"{view.identity.exposure} · 설정일 {view.identity.listing_date} · "
                f"{view.identity.leverage_style} · {view.identity.distribution_style}"
            )
        else:
            current_text = (
                f" · 현재 조회값 {view.current_value:,.0f}원 · 업데이트됨"
                if view.current_refresh_status == "UPDATED" and view.current_value is not None
                else ""
            )
            self.summary.setText(
                f"{view.identity.name} · {view.identity.symbol} · {view.identity.market} · "
                f"종가 {float(latest.close):,.0f}원 · {change_text} · "
                f"기간 고가 {view.period_high:,.0f}원 / 저가 {view.period_low:,.0f}원"
                f"{current_text}"
            )
        self.status.setText(
            f"{view.price_mode} · {_freshness_label(view.freshness)} · "
            f"{view.reference_kst} · 조정주가/총수익/Backtest/기업행사 마커 미제공"
        )
        if view.current_refresh_status == "UPDATED":
            self.status.setText(
                f"업데이트됨 · {view.current_provider} · 원천일자 {view.current_source_date} · "
                f"조회 {view.current_retrieved_at_utc} · 확정 EOD/Backtest와 분리 | "
                + self.status.text()
            )
        if view.current_refresh_status in {
            "CURRENT_SOURCE_TIMESTAMP_VALID", "NXT_SESSION_CLOSE", "NXT_SESSION_CLOSE_INFERRED",
        }:
            current_label = (
                view.current_visible_label
                if view.current_visible_label else
                "LS 15m retained"
                if view.current_provider == "LS_OPENAPI" else
                f"{view.current_provider} {view.current_interval or 'snapshot'} retained"
            )
            self.summary.setText(
                self.summary.text()
                + f" | {current_label} {view.current_value:,.0f} ({view.current_unit})"
            )
            self.status.setText(
                f"{current_label}; display-only, PIT-blocked, and separate from EOD/Backtest"
                + (f" | {view.current_unavailable_reason}" if view.current_unavailable_reason else "")
                + " | "
                + self.status.text()
            )
        if view.current_refresh_status == "CURRENT_GATE_BLOCKED":
            self.status.setText(
                "Current numeric withheld: "
                + (view.current_unavailable_reason or "CURRENT_SOURCE_TIMESTAMP_REQUIRED")
                + " | retained daily history remains separate | "
                + self.status.text()
            )
        if view.current_refresh_status == "CURRENT_UNAVAILABLE":
            self.status.setText(
                "Current numeric unavailable: "
                + (view.current_unavailable_reason or "CURRENT_SOURCE_TIMESTAMP_REQUIRED")
                + " | retained daily history remains separate | "
                + self.status.text()
            )
        if view.freshness == "STALE" and view.unavailable_reason:
            self.status.setText(
                f"STALE RETAINED HISTORY: {view.unavailable_reason} | "
                + self.status.text()
            )
        self.status.setToolTip(
            f"identity={view.identity.market}:{view.identity.symbol}\n"
            f"isin={view.identity.isin or '미보존'}\nsource={view.source}\n"
            f"issuer={view.identity.issuer or '해당 없음'}\n"
            f"exposure={view.identity.exposure or '해당 없음'}\n"
            f"inception={view.identity.listing_date or '미보존'}\n"
            f"currency={view.identity.currency or 'KRW'}\n"
            f"expected_as_of={view.expected_as_of or '미확인'}\nprice_mode={view.price_mode}"
        )
        if view.current_refresh_status in {
            "CURRENT_SOURCE_TIMESTAMP_VALID", "NXT_SESSION_CLOSE", "NXT_SESSION_CLOSE_INFERRED",
            "CURRENT_GATE_BLOCKED", "CURRENT_UNAVAILABLE",
        }:
            self.status.setToolTip(
                self.status.toolTip()
                + f"\ncurrent_route={view.current_route}"
                + f"\ncurrent_interval={view.current_interval}"
                + f"\ncurrent_finality={view.current_finality}"
                + f"\ncurrent_provider_timestamp_utc={view.current_provider_timestamp_utc}"
                + f"\ncurrent_source_date_kst={view.current_source_date}"
                + f"\ncurrent_retrieved_at_utc={view.current_retrieved_at_utc}"
                + f"\ncurrent_source_route={view.current_source_route}"
                + f"\ncurrent_display_only={view.current_display_only}"
                + f"\ncurrent_pit_safe={view.current_pit_safe}"
                + f"\ncurrent_gate_reason={view.current_unavailable_reason or 'accepted today-KST <=60m source timestamp'}"
            )
        if view.unavailable_reason:
            self.status.setToolTip(
                self.status.toolTip() + f"\nwarning={view.unavailable_reason}"
            )
        if view.identity.is_us_etf:
            self.status.setToolTip(
                self.status.toolTip()
                + f"\nleverage={view.identity.leverage_style or 'not applicable'}"
                + f"\ndistribution={view.identity.distribution_style or 'not applicable'}"
                + f"\nidentity_source={view.identity.identity_source or 'not retained'}"
            )
        if preserved and self.comparison_toggle.isChecked():
            self._clear_comparison(keep_toggle=True)
            self._comparison_toggled(True)
        self._show_latest_observation()

    def _show_observation(self, index: int) -> None:
        if self._session_mapping is None or self._frame.empty:
            return
        self._last_observation_index = index
        row = self._frame.iloc[index]
        usd = self._selected_identity is not None and self._selected_identity.is_us_etf
        price = (lambda value: f"${float(value):,.2f}") if usd else (lambda value: f"{float(value):,.0f}")
        details = [
            self._session_mapping.dates[index].date().isoformat(),
            f"시 {price(row.open)}", f"고 {price(row.high)}",
            f"저 {price(row.low)}", f"종 {price(row.close)}",
            _format_exact_share_volume(row.volume),
        ]
        if index > 0 and float(self._frame.iloc[index - 1].close) != 0:
            change = float(row.close - self._frame.iloc[index - 1].close)
            rate = change / float(self._frame.iloc[index - 1].close) * 100
            details.append(
                f"등락 {'$' if usd else ''}{change:+,.2f} ({rate:+.2f}%)"
                if usd else f"등락 {change:+,.0f} ({rate:+.2f}%)"
            )
        for label, column, enabled in (
            ("MA5", "ma5", True), ("MA20", "ma20", True),
            ("MA60", "ma60", True), ("MA120", "ma120", True),
            ("RSI14", "rsi14", self.rsi.currentText() != "Off"),
            ("괴리60", "disparity60", self.disparity.currentText() != "Off"),
        ):
            value = pd.to_numeric(row.get(column), errors="coerce")
            if enabled and pd.notna(value):
                details.append(f"{label} {float(value):,.2f}")
        self.hover.setText(" · ".join(details))

    def _show_latest_observation(self) -> None:
        if not self._frame.empty:
            self._show_observation(len(self._frame) - 1)

    def _mouse_moved(self, event) -> None:
        if not len(self._dates) or self._session_mapping is None:
            return
        point = self.plot.plotItem.vb.mapSceneToView(event[0])
        nearest = float(self._dates[np.abs(self._dates - point.x()).argmin()])
        for line in self.crosshairs:
            line.setPos(nearest)
        self._show_observation(int(round(nearest)))
        self._comparison_crosshair.setPos(nearest)
        view = self._comparison_view
        if view is None or not view.displays_values:
            return
        row = view.frame.loc[np.isclose(view.frame["target_position"], nearest)]
        if row.empty:
            return
        selected = row.iloc[0]
        self.hover.setText(
            self.hover.text()
            + f" · 공통100 {view.target.symbol} {float(selected.target_normalized):.2f} · "
            f"{view.benchmark_id} {float(selected.benchmark_normalized):.2f}"
        )

    def leaveEvent(self, event) -> None:
        self._show_latest_observation()
        super().leaveEvent(event)


class WatchlistPage(QtWidgets.QWidget):
    """Compact editor for local user lists; market values remain fail-closed."""

    list_created = QtCore.Signal(str)
    list_renamed = QtCore.Signal(str, str)
    list_removed = QtCore.Signal(str)
    list_moved = QtCore.Signal(str, int)
    item_removed = QtCore.Signal(str, object)
    item_moved = QtCore.Signal(str, object, int)
    open_requested = QtCore.Signal(object, bool)
    selection_changed = QtCore.Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state: WatchlistState | None = None
        self._quotes: dict[tuple[str, str], WatchlistQuote] = {}
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 12)
        title = QtWidgets.QLabel("관심종목")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        subtitle = QtWidgets.QLabel(
            "개인 로컬 목록 · 시장+종목코드 식별자 보존 · 목록을 열어도 외부 조회를 시작하지 않음"
        )
        subtitle.setObjectName("pageSubtitle")
        root.addWidget(subtitle)

        controls = QtWidgets.QHBoxLayout()
        self.list_selector = QtWidgets.QComboBox()
        self.list_selector.setMinimumWidth(220)
        self.list_selector.setAccessibleName("개인 관심종목 목록")
        self.create_button = QtWidgets.QPushButton("목록 추가")
        self.rename_button = QtWidgets.QPushButton("이름 변경")
        self.remove_list_button = QtWidgets.QPushButton("목록 삭제")
        self.list_up_button = QtWidgets.QPushButton("목록 ↑")
        self.list_down_button = QtWidgets.QPushButton("목록 ↓")
        for widget in (
            self.list_selector, self.create_button, self.rename_button,
            self.remove_list_button, self.list_up_button, self.list_down_button,
        ):
            controls.addWidget(widget)
        controls.addStretch()
        root.addLayout(controls)

        self.notice = QtWidgets.QLabel("관심종목이 없습니다. 종목 검색 또는 차트에서 ☆를 눌러 추가하세요.")
        self.notice.setObjectName("freshness")
        self.notice.setWordWrap(True)
        root.addWidget(self.notice)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("종목", "최근 가격·등락", "기준·상태", "작업"))
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setAccessibleName("관심종목의 정확한 종목, 가격, 기준시각, 상태와 작업")
        self.table.cellDoubleClicked.connect(self._open_row)
        root.addWidget(self.table, 1)

        self.list_selector.currentIndexChanged.connect(self._selected)
        self.create_button.clicked.connect(self._prompt_create)
        self.rename_button.clicked.connect(self._prompt_rename)
        self.remove_list_button.clicked.connect(self._confirm_remove_list)
        self.list_up_button.clicked.connect(lambda: self._move_list(-1))
        self.list_down_button.clicked.connect(lambda: self._move_list(1))

    @property
    def selected_list_id(self) -> str:
        return str(self.list_selector.currentData() or DEFAULT_LIST_ID)

    def render(
        self,
        state: WatchlistState,
        quotes: tuple[WatchlistQuote, ...] = (),
        *,
        preserve_selection: bool = True,
    ) -> None:
        selected = self.selected_list_id if preserve_selection else DEFAULT_LIST_ID
        self._state = state
        self._quotes = {quote.identity.key: quote for quote in quotes}
        blocker = QtCore.QSignalBlocker(self.list_selector)
        self.list_selector.clear()
        for watchlist in state.lists:
            self.list_selector.addItem(f"{watchlist.name} ({len(watchlist.items)})", watchlist.list_id)
        position = self.list_selector.findData(selected)
        self.list_selector.setCurrentIndex(position if position >= 0 else 0)
        del blocker
        self._render_rows()

    def show_error(self, message: str) -> None:
        self.notice.setText(message)
        self.notice.show()

    def _current_list(self) -> NamedWatchlist | None:
        if self._state is None:
            return None
        try:
            return self._state.list_by_id(self.selected_list_id)
        except StopIteration:
            return None

    def _selected(self) -> None:
        self._quotes = {}
        self._render_rows()
        self.selection_changed.emit(self.selected_list_id)

    def _render_rows(self) -> None:
        watchlist = self._current_list()
        items = watchlist.items if watchlist is not None else ()
        self.table.setRowCount(len(items))
        self.remove_list_button.setEnabled(watchlist is not None and watchlist.list_id != DEFAULT_LIST_ID)
        self.rename_button.setEnabled(watchlist is not None)
        position = self.list_selector.currentIndex()
        self.list_up_button.setEnabled(position > 0)
        self.list_down_button.setEnabled(0 <= position < self.list_selector.count() - 1)
        if not items:
            self.notice.setText("관심종목이 없습니다. 종목 검색 또는 차트에서 ☆를 눌러 추가하세요.")
            self.notice.show()
            return
        self.notice.hide()
        for row, saved in enumerate(items):
            identity = saved.identity
            identity_item = QtWidgets.QTableWidgetItem(
                f"{identity.name} · {identity.symbol} · {identity.market} · {identity.security_type}"
            )
            identity_item.setData(QtCore.Qt.UserRole, identity)
            identity_item.setToolTip(
                f"identity={identity.market}:{identity.symbol}\nisin={identity.isin or '미보존'}"
            )
            self.table.setItem(row, 0, identity_item)
            quote = self._quotes.get(identity.key)
            if quote is None:
                price_text = "로컬 상태 확인 중…"
                status_text = "가격·등락 표시 보류"
            elif quote.displays_values and identity.is_us_etf:
                change = (
                    f"${quote.change:+,.2f} ({quote.change_pct:+.2f}%)"
                    if quote.change is not None and quote.change_pct is not None
                    else "change N/A"
                )
                price_text = f"${quote.price:,.2f} · {change}"
                status_text = f"{quote.reference_kst or 'KST reference unavailable'} · {_freshness_label(quote.freshness)}"
            elif quote.displays_values:
                change = (
                    f"{quote.change:+,.0f}원 ({quote.change_pct:+.2f}%)"
                    if quote.change is not None and quote.change_pct is not None else "등락 N/A"
                )
                price_text = f"{quote.price:,.0f}원 · {change}"
                status_text = f"{quote.reference_kst or '기준시각 미보존'} · {_freshness_label(quote.freshness)}"
            else:
                price_text = "가격·등락 숨김"
                status_text = f"{_freshness_label(quote.freshness)} · {quote.unavailable_reason or '현재 표시 불가'}"
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(price_text))
            status_item = QtWidgets.QTableWidgetItem(status_text)
            status_item.setToolTip(status_text)
            self.table.setItem(row, 2, status_item)
            actions = QtWidgets.QWidget()
            action_layout = QtWidgets.QHBoxLayout(actions)
            action_layout.setContentsMargins(2, 1, 2, 1)
            for text, callback, enabled in (
                ("열기", lambda _=False, identity=identity: self.open_requested.emit(identity, False), True),
                ("새 창", lambda _=False, identity=identity: self.open_requested.emit(identity, True), True),
                ("↑", lambda _=False, key=identity.key: self.item_moved.emit(self.selected_list_id, key, -1), row > 0),
                ("↓", lambda _=False, key=identity.key: self.item_moved.emit(self.selected_list_id, key, 1), row < len(items) - 1),
                ("제거", lambda _=False, key=identity.key: self.item_removed.emit(self.selected_list_id, key), True),
            ):
                button = QtWidgets.QPushButton(text)
                button.setEnabled(enabled)
                button.clicked.connect(callback)
                action_layout.addWidget(button)
            self.table.setCellWidget(row, 3, actions)
            self.table.setRowHeight(row, 42)

    def _open_row(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        identity = item.data(QtCore.Qt.UserRole) if item is not None else None
        if isinstance(identity, EquityIdentity):
            self.open_requested.emit(identity, False)

    def _prompt_create(self) -> None:
        name, accepted = QtWidgets.QInputDialog.getText(self, "목록 추가", "새 목록 이름")
        if accepted and name.strip():
            self.list_created.emit(name.strip())

    def _prompt_rename(self) -> None:
        watchlist = self._current_list()
        if watchlist is None:
            return
        name, accepted = QtWidgets.QInputDialog.getText(
            self, "이름 변경", "목록 이름", text=watchlist.name,
        )
        if accepted and name.strip():
            self.list_renamed.emit(watchlist.list_id, name.strip())

    def _confirm_remove_list(self) -> None:
        watchlist = self._current_list()
        if watchlist is None or watchlist.list_id == DEFAULT_LIST_ID:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "목록 삭제", f"'{watchlist.name}' 목록을 삭제할까요?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self.list_removed.emit(watchlist.list_id)

    def _move_list(self, offset: int) -> None:
        if self._current_list() is not None:
            self.list_moved.emit(self.selected_list_id, offset)


class BacktestPage(QtWidgets.QScrollArea):
    """Read-only legacy result plus one fixed offline close-proxy workflow."""

    run_requested = QtCore.Signal()
    reload_requested = QtCore.Signal()
    export_requested = QtCore.Signal()
    scenario_requested = QtCore.Signal()

    FROZEN_DIGEST = (
        "a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713"
    )
    BUNDLE_ARTIFACT_NAMES = (
        "bundle.json",
        "experiments.json",
        "portfolio_ledger.json",
        "result.json",
        "signals.csv",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        self.setWidget(content)
        root = QtWidgets.QVBoxLayout(content)
        title = QtWidgets.QLabel("BACKTEST / SIGNAL REPLAY")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        self.fixed_configuration = MetricCard("FIXED OFFLINE CONFIGURATION")
        self.fixed_configuration.set_lines([
            f"fixed digest: {self.FROZEN_DIGEST}",
            "dev 8225 · before sealed holdout",
            "sealed holdout 1222 · results_reviewed=false",
            "T-close→next retained close",
            "long/cash only · normalized initial cash 1.0",
            "zero yield · one-way 10bp",
            "no leverage/short/liquidation",
            "DEVELOPMENT_ONLY_CLOSE_PROXY · NOT_EXECUTABLE_INSTRUMENT",
        ])

        controls = QtWidgets.QHBoxLayout()
        self.run_button = QtWidgets.QPushButton("오프라인 실행")
        self.run_button.setAccessibleName("고정 오프라인 백테스트 실행")
        self.run_button.setToolTip(
            "고정된 로컬 입력과 조건으로만 실행합니다. 공급자나 계좌를 호출하지 않습니다."
        )
        self.reload_button = QtWidgets.QPushButton("검증 번들 새로 읽기")
        self.reload_button.setAccessibleName("검증된 백테스트 번들 새로 읽기")
        self.reload_button.setToolTip(
            "로컬 5파일 번들을 다시 검증합니다. 계산이나 외부 호출은 하지 않습니다."
        )
        self.export_button = QtWidgets.QPushButton("정확한 번들 내보내기")
        self.export_button.setAccessibleName("검증된 백테스트 번들 정확히 내보내기")
        self.export_button.setToolTip(
            "마지막으로 검증된 5파일의 바이트만 빈 폴더로 복사합니다."
        )
        self.export_button.setEnabled(False)
        self.run_button.clicked.connect(self.run_requested)
        self.reload_button.clicked.connect(self.reload_requested)
        self.export_button.clicked.connect(self.export_requested)
        controls.addWidget(self.run_button)
        controls.addWidget(self.reload_button)
        controls.addWidget(self.export_button)
        controls.addStretch()
        root.addLayout(controls)

        self.workflow_status = QtWidgets.QLabel(
            "검증된 close-proxy 실행 결과를 읽는 중입니다."
        )
        self.workflow_status.setObjectName("freshness")
        self.workflow_status.setWordWrap(True)
        self.workflow_status.setAccessibleName("백테스트 작업 상태")
        root.addWidget(self.workflow_status)

        self.scenario_panel = QtWidgets.QGroupBox(
            "고정 RSI14 30/70 시나리오 · DEVELOPMENT ONLY"
        )
        self.scenario_panel.setAccessibleName(
            "개발 전용 고정 RSI14 조건 및 다음 시가 시나리오"
        )
        scenario_root = QtWidgets.QVBoxLayout(self.scenario_panel)
        scenario_controls = QtWidgets.QHBoxLayout()
        self.scenario_button = QtWidgets.QPushButton("고정 시나리오 실행")
        self.scenario_button.setAccessibleName(
            "고정 RSI14 30 70 개발 시나리오 실행"
        )
        self.scenario_button.setToolTip(
            "미리 정한 RSI14 LOW30/HIGH70 조건과 30진입/70청산을 "
            "개발 입력에서만 평가합니다. 탐색·순위·추천은 하지 않습니다."
        )
        self.scenario_button.clicked.connect(self.scenario_requested)
        self.scenario_status = QtWidgets.QLabel(
            "정확한 typed development 입력이 연결되지 않았습니다."
        )
        self.scenario_status.setObjectName("freshness")
        self.scenario_status.setWordWrap(True)
        self.scenario_status.setAccessibleName("고정 시나리오 작업 상태")
        scenario_controls.addWidget(self.scenario_button)
        scenario_controls.addWidget(self.scenario_status, 1)
        scenario_root.addLayout(scenario_controls)
        self.scenario_conditions = MetricCard("조건부 요약 · 고정 LOW30 / HIGH70")
        self.scenario_coverage = MetricCard("SIGNAL COVERAGE · 순위 선택 없음")
        self.scenario_execution = MetricCard("NEXT-OPEN LEDGER · 개발 전용")
        self.scenario_matched_hold = MetricCard("MATCHED-HOLD DIFFERENCE · 추천 아님")
        for card in (
            self.scenario_conditions,
            self.scenario_coverage,
            self.scenario_execution,
            self.scenario_matched_hold,
        ):
            card.set_lines(["실행 결과 없음"])
        scenario_grid = QtWidgets.QGridLayout()
        scenario_grid.addWidget(self.scenario_conditions, 0, 0)
        scenario_grid.addWidget(self.scenario_coverage, 0, 1)
        scenario_grid.addWidget(self.scenario_execution, 1, 0)
        scenario_grid.addWidget(self.scenario_matched_hold, 1, 1)
        scenario_grid.setColumnStretch(0, 1)
        scenario_grid.setColumnStretch(1, 1)
        scenario_root.addLayout(scenario_grid)
        root.addWidget(self.scenario_panel)

        self.experiment = MetricCard("EXPERIMENT STATUS")
        self.coverage = MetricCard("INPUT COVERAGE")
        self.features = MetricCard("FEATURE SET / THRESHOLDS")
        self.signals = MetricCard("HORIZONS / SIGNALS")
        self.metrics = MetricCard("DESCRIPTIVE METRICS")
        self.crises = MetricCard("CRISIS DIAGNOSTICS / HOLDOUT")
        self.scope = MetricCard("PORTFOLIO SCOPE")
        grid = QtWidgets.QGridLayout()
        grid.addWidget(self.experiment, 0, 0)
        grid.addWidget(self.coverage, 0, 1)
        grid.addWidget(self.features, 1, 0)
        grid.addWidget(self.signals, 1, 1)
        grid.addWidget(self.metrics, 2, 0, 1, 2)
        grid.addWidget(self.crises, 3, 0, 1, 2)
        grid.addWidget(self.scope, 4, 0, 1, 2)

        self.portfolio_metrics = MetricCard(
            "CLOSE-PROXY PORTFOLIO METRICS · NOT EXECUTABLE"
        )
        self.portfolio_metrics.set_lines(["검증된 실행 결과 없음"])
        self.bundle_receipt = MetricCard("VALIDATED 5-FILE BUNDLE RECEIPT")
        self.bundle_receipt.set_lines(["검증된 실행 영수증 없음"])
        root.addWidget(self.portfolio_metrics)

        self.chart_status = QtWidgets.QLabel(
            "CLOSE-PROXY NAV / DRAWDOWN · NOT_EXECUTABLE_INSTRUMENT"
        )
        self.chart_status.setObjectName("sectionTitle")
        root.addWidget(self.chart_status)
        self._nav_axis = SessionDateAxisItem(
            orientation="bottom", labels_visible=False,
        )
        self.nav_plot = pg.PlotWidget(axisItems={"bottom": self._nav_axis})
        self.nav_plot.setAccessibleName(
            "실행 불가능 close-proxy 정규화 NAV 차트"
        )
        self.nav_plot.setLabel("left", "정규화 NAV")
        self.nav_plot.showGrid(x=True, y=True, alpha=0.15)
        self.nav_plot.setMinimumHeight(220)
        self._drawdown_axis = SessionDateAxisItem(orientation="bottom")
        self.drawdown_plot = pg.PlotWidget(
            axisItems={"bottom": self._drawdown_axis}
        )
        self.drawdown_plot.setAccessibleName(
            "실행 불가능 close-proxy 낙폭 차트"
        )
        self.drawdown_plot.setLabel("left", "낙폭", units="ratio")
        self.drawdown_plot.setLabel("bottom", "보존 거래일")
        self.drawdown_plot.showGrid(x=True, y=True, alpha=0.15)
        self.drawdown_plot.setMaximumHeight(180)
        self.drawdown_plot.setXLink(self.nav_plot)
        self.nav_curve = None
        self.drawdown_curve = None
        root.addWidget(self.nav_plot, 2)
        root.addWidget(self.drawdown_plot, 1)

        self.evidence_toggle = QtWidgets.QToolButton()
        self.evidence_toggle.setCheckable(True)
        self.evidence_toggle.setChecked(False)
        self.evidence_toggle.setText("기술 근거 펼치기")
        self.evidence_toggle.setAccessibleName(
            "백테스트 고정 설정, 검증 영수증 및 기술 근거 펼치기 또는 접기"
        )
        self.evidence_toggle.setToolTip(
            "결과 계산에는 영향을 주지 않고 고정 조건과 검증 근거를 표시합니다."
        )
        self.evidence_toggle.setArrowType(QtCore.Qt.RightArrow)
        self.evidence_toggle.setToolButtonStyle(
            QtCore.Qt.ToolButtonTextBesideIcon
        )
        root.addWidget(self.evidence_toggle)

        self.evidence_panel = QtWidgets.QWidget()
        self.evidence_panel.setAccessibleName("백테스트 기술 근거")
        evidence_layout = QtWidgets.QVBoxLayout(self.evidence_panel)
        evidence_layout.setContentsMargins(0, 0, 0, 0)
        evidence_layout.addWidget(self.fixed_configuration)
        evidence_layout.addWidget(self.bundle_receipt)
        evidence_layout.addLayout(grid)
        self.evidence_panel.setVisible(False)
        self.evidence_toggle.toggled.connect(self._set_evidence_expanded)
        root.addWidget(self.evidence_panel)
        self._workflow_busy = False
        self._has_accepted_bundle = False
        self._scenario_available = False
        self._has_scenario_result = False
        root.addStretch()

    @property
    def has_accepted_bundle(self) -> bool:
        return self._has_accepted_bundle

    def _set_evidence_expanded(self, expanded: bool) -> None:
        self.evidence_panel.setVisible(expanded)
        self.evidence_toggle.setArrowType(
            QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow
        )
        self.evidence_toggle.setText(
            "기술 근거 접기" if expanded else "기술 근거 펼치기"
        )

    def _apply_workflow_button_state(self) -> None:
        enabled = not self._workflow_busy
        self.run_button.setEnabled(enabled)
        self.reload_button.setEnabled(enabled)
        self.export_button.setEnabled(enabled and self._has_accepted_bundle)
        self.scenario_button.setEnabled(enabled and self._scenario_available)

    def configure_scenario_available(self, available: bool) -> None:
        self._scenario_available = bool(available)
        self._apply_workflow_button_state()
        if available:
            self.scenario_status.setText(
                "고정 typed development 입력 준비됨 · holdout 미검토 · API 0"
            )
        else:
            self.scenario_status.setText(
                "정확한 typed development 입력이 없어 숫자를 표시하지 않습니다."
            )

    def set_workflow_busy(self, action: str | None) -> None:
        self._workflow_busy = action is not None
        self._apply_workflow_button_state()
        if action is None:
            return
        labels = {
            "RUN": "고정 오프라인 replay를 실행하고 전체 번들을 검증하는 중입니다.",
            "RELOAD": "로컬 5파일 번들을 다시 검증하는 중입니다.",
            "EXPORT": "마지막 검증 세대를 정확한 바이트로 내보내는 중입니다.",
            "SCENARIO": "고정 RSI14 개발 시나리오를 백그라운드에서 평가하는 중입니다.",
        }
        if action == "SCENARIO":
            self.scenario_status.setText(labels[action])
        else:
            self.workflow_status.setText(
                labels.get(action, "백테스트 작업을 처리하는 중입니다.")
            )

    def set_workflow_failure(self, action: str) -> None:
        labels = {
            "RUN": "실행 또는 검증에 실패했습니다. 마지막 검증 결과는 그대로 보존했습니다.",
            "RELOAD": "번들 재검증에 실패했습니다. 마지막 검증 결과는 그대로 보존했습니다.",
            "EXPORT": "내보내기를 완료하지 못했습니다. 원본과 화면 결과는 변경하지 않았습니다.",
            "SCENARIO": "시나리오 평가에 실패했습니다. 마지막 시나리오 결과는 그대로 보존했습니다.",
        }
        if action == "SCENARIO":
            self.scenario_status.setText(labels[action])
        else:
            self.workflow_status.setText(labels.get(action, "작업에 실패했습니다."))

    def render_scenario(self, view: BacktestScenarioView) -> None:
        """Atomically render one immutable fixed-scenario result view."""
        if (
            type(view) is not BacktestScenarioView
            or view.contract_version != SCENARIO_ADAPTER_VERSION
            or view.input_contract_version != SCENARIO_INPUT_VERSION
            or view.scenario_id != SCENARIO_ID
            or view.status != "DEVELOPMENT_ONLY_FIXED_SCENARIO"
            or view.study_contract_version != "predefined-indicator-study/v1"
            or view.strategy_contract_version != "predefined-threshold-band/v1"
            or view.matched_hold_contract_version
            != "threshold-band-matched-hold/v1"
            or view.execution.contract_version != "historical-next-open/v1"
            or view.matched_hold.contract_version
            != "threshold-band-matched-hold/v1"
            or view.results_reviewed is not False
            or view.winner_selected is not False
            or view.recommendation_provided is not False
            or tuple(
                (item.candidate_id, item.direction, item.threshold)
                for item in view.conditional
            ) != (
                ("RSI14_LOW_30", "LOW", 30.0),
                ("RSI14_HIGH_70", "HIGH", 70.0),
            )
        ):
            raise ValueError("fixed Backtest scenario view identity differs")

        def percent(value: float | None) -> str:
            return "N/A" if value is None else f"{value:+.2%}"

        condition_lines: list[str] = []
        coverage_lines = [
            "후보 순위·winner 선택 없음 · 투자 추천 아님",
            f"개발 구간: {view.market_start} ~ {view.market_end}",
        ]
        for item in view.conditional:
            condition_lines.extend((
                f"{item.candidate_id} · {item.availability}",
                "conditional / unconditional / difference: "
                f"{percent(item.conditional_mean_return)} / "
                f"{percent(item.unconditional_mean_return)} / "
                f"{percent(item.mean_return_difference)}",
                "conditional positive / mean drawdown: "
                f"{percent(item.conditional_positive_rate)} / "
                f"{percent(item.conditional_mean_max_drawdown)}",
            ))
            coverage_lines.append(
                f"{item.candidate_id}: {item.signal_observations:,} / "
                f"{item.aligned_observations:,} ({item.signal_rate:.2%}) · "
                f"{item.availability}"
            )
        execution = view.execution
        execution_lines = [
            f"{execution.contract_version} · {execution.execution_claim}",
            f"{execution.instrument_id} / {execution.currency}",
            f"observations / trades: {execution.observations:,} / {execution.trade_count:,}",
            f"ending NAV / total return: {execution.ending_nav:.6f} / {execution.total_return:+.2%}",
            f"annualized volatility / max drawdown: {execution.annualized_volatility:.2%} / {execution.max_drawdown:.2%}",
            f"turnover / long exposure: {execution.total_turnover:.2%} / {execution.average_long_exposure:.2%}",
            f"transaction cost: {execution.transaction_cost_paid:.6f}",
        ]
        matched = view.matched_hold
        matched_lines = [
            f"{matched.contract_version} · {matched.availability}",
            "동일 최초 진입·동일 clock·동일 cost 가정",
        ]
        if matched.availability == "EVALUATED":
            matched_lines.extend((
                f"entry observation / usable: {matched.entry_observation_date} / {matched.entry_usable_from}",
                f"ending NAV difference: {matched.ending_nav_difference:+.6f}",
                f"total return difference: {percent(matched.total_return_difference)}",
                f"volatility difference: {percent(matched.annualized_volatility_difference)}",
                f"max drawdown difference: {percent(matched.max_drawdown_difference)}",
                f"incremental transaction cost: {matched.incremental_transaction_cost:+.6f}",
            ))
        else:
            matched_lines.append("진입 관측 없음 · 비교 숫자 생성 안 함")

        cards = (
            self.scenario_conditions,
            self.scenario_coverage,
            self.scenario_execution,
            self.scenario_matched_hold,
        )
        prior = tuple(card.body.text() for card in cards)
        prior_status = self.scenario_status.text()
        try:
            for card, lines in zip(
                cards,
                (condition_lines, coverage_lines, execution_lines, matched_lines),
                strict=True,
            ):
                card.set_lines(lines)
            self.scenario_status.setText(
                "DEVELOPMENT ONLY · 고정 RSI14 30/70 · holdout 미검토 · "
                "탐색·순위·추천 없음"
            )
            self._has_scenario_result = True
        except Exception:
            for card, text in zip(cards, prior, strict=True):
                card.body.setText(text)
            self.scenario_status.setText(prior_status)
            raise

    def set_export_success(self) -> None:
        self.workflow_status.setText(
            "검증된 5파일 번들을 바이트 변경 없이 내보냈습니다."
        )

    def set_legacy_fallback(self) -> None:
        self.workflow_status.setText(
            "기존 설명용 백테스트 결과입니다. 검증된 5파일 번들이 아닙니다."
        )

    def set_workflow_close_pending(self) -> None:
        self.workflow_status.setText(
            "백그라운드 작업 완료 후 자동으로 종료합니다. "
            "실행 중인 작업은 강제 종료하지 않습니다."
        )

    @staticmethod
    def _required_attribute(subject: object, name: str) -> object:
        if not hasattr(subject, name):
            raise TypeError(f"validated Backtest view has no {name}")
        return getattr(subject, name)

    def render_validated_bundle(self, view: object) -> None:
        """Atomically present one already validated development-only bundle."""
        holdout = self._required_attribute(view, "holdout")
        portfolio = self._required_attribute(view, "portfolio")
        receipt = self._required_attribute(view, "bundle_receipt")
        assumptions = self._required_attribute(portfolio, "assumptions")
        curve = tuple(self._required_attribute(portfolio, "curve"))

        if (
            self._required_attribute(portfolio, "status")
            != "DEVELOPMENT_ONLY_CLOSE_PROXY"
            or self._required_attribute(portfolio, "instrument_claim")
            != "NOT_EXECUTABLE_INSTRUMENT"
            or self._required_attribute(receipt, "schema")
            != "market-backtest-phase1-replay/v1"
            or self._required_attribute(receipt, "status") != "READY"
            or self._required_attribute(receipt, "frozen_input_digest")
            != self.FROZEN_DIGEST
            or self._required_attribute(holdout, "development_observations") != 8_225
            or self._required_attribute(holdout, "holdout_observations") != 1_222
            or type(self._required_attribute(holdout, "results_reviewed")) is not bool
            or self._required_attribute(holdout, "results_reviewed") is not False
        ):
            raise ValueError("validated Backtest view violates the fixed GUI boundary")
        if not isinstance(assumptions, Mapping):
            raise TypeError("validated Backtest assumptions must be immutable mapping data")
        expected_assumptions = {
            "initial_nav": 1.0,
            "long_position": 1,
            "cash_position": 0,
            "cash_yield_rate": 0.0,
            "one_way_transaction_cost_rate": 0.001,
            "annualization_sessions": 252,
            "execution_price": "RETAINED_DAILY_FINAL_CLOSE_PROXY",
            "timing_policy": (
                "T_CLOSE_SIGNAL_T_PLUS_1_0900_USABLE_EXECUTE_T_PLUS_1_FINAL_CLOSE"
            ),
            "leverage_allowed": False,
            "shorting_allowed": False,
            "forced_liquidation": False,
        }
        if any(
            name not in assumptions
            or type(assumptions[name]) is not type(expected)
            or assumptions[name] != expected
            for name, expected in expected_assumptions.items()
        ):
            raise ValueError("validated Backtest assumptions differ from fixed GUI copy")
        if not curve:
            raise ValueError("validated Backtest equity curve is empty")

        dates = tuple(
            pd.Timestamp(str(self._required_attribute(point, "date")))
            for point in curve
        )
        nav = np.asarray([
            float(self._required_attribute(point, "nav")) for point in curve
        ], dtype="float64")
        drawdown = np.asarray([
            float(self._required_attribute(point, "drawdown")) for point in curve
        ], dtype="float64")
        if (
            any(pd.isna(value) for value in dates)
            or any(dates[index] <= dates[index - 1] for index in range(1, len(dates)))
            or not np.isfinite(nav).all()
            or not np.isfinite(drawdown).all()
        ):
            raise ValueError("validated Backtest curve is not finite")
        x = np.arange(len(curve), dtype="float64")

        def metric(name: str) -> object:
            return self._required_attribute(portfolio, name)

        metric_lines = [
            "DEVELOPMENT_ONLY_CLOSE_PROXY · NOT_EXECUTABLE_INSTRUMENT",
            f"observations / intervals: {_fmt(len(curve))} / {_fmt(max(0, len(curve) - 1))}",
            f"initial / ending NAV: {_fmt(metric('initial_nav'), 6)} / {_fmt(metric('ending_nav'), 6)}",
            f"total return: {float(metric('total_return')):.2%}",
            f"annualized return: {float(metric('annualized_return')):.2%}",
            f"annualized volatility: {float(metric('annualized_volatility')):.2%}",
            f"maximum drawdown: {float(metric('max_drawdown')):.2%}",
            f"trades: {_fmt(metric('trade_count'))}",
            f"total turnover: {float(metric('total_turnover')):.2%}",
            f"average long exposure: {float(metric('average_long_exposure')):.2%}",
            f"transaction cost paid (normalized NAV): {_fmt(metric('transaction_cost_paid'), 6)}",
        ]
        period = f"{dates[0].date().isoformat()}부터 {dates[-1].date().isoformat()}"
        nav_accessible_description = (
            "개발 전용 실행 불가능 close-proxy 정규화 NAV. "
            f"기간 {period}, 관측 {len(curve):,}개. "
            f"초기 NAV {float(metric('initial_nav')):.6f}, "
            f"종료 NAV {float(metric('ending_nav')):.6f}, "
            f"총수익률 {float(metric('total_return')):.2%}. "
            "봉인 holdout 결과는 미검토 상태입니다."
        )
        drawdown_accessible_description = (
            "개발 전용 실행 불가능 close-proxy 낙폭. "
            f"기간 {period}, 관측 {len(curve):,}개. "
            f"최대 낙폭 {float(metric('max_drawdown')):.2%}. "
            "봉인 holdout 결과는 미검토 상태입니다."
        )
        artifacts = tuple(self._required_attribute(receipt, "artifacts"))
        if tuple(
            self._required_attribute(item, "name") for item in artifacts
        ) != self.BUNDLE_ARTIFACT_NAMES:
            raise ValueError("validated Backtest receipt does not bind five artifacts")
        receipt_lines = [
            f"schema / status: {self._required_attribute(receipt, 'schema')} / {self._required_attribute(receipt, 'status')}",
            f"fixed input: {self._required_attribute(receipt, 'frozen_input_digest')}",
            f"bundle digest: {self._required_attribute(receipt, 'bundle_digest')}",
            *(
                f"{self._required_attribute(item, 'name')}: "
                f"{_fmt(self._required_attribute(item, 'bytes'))} bytes · "
                f"{str(self._required_attribute(item, 'sha256'))[:16]}…"
                for item in artifacts
            ),
        ]
        if not isinstance(view, BacktestExperimentView):
            raise TypeError("validated Backtest bundle view has an unsupported type")

        mutable_cards = (
            self.experiment,
            self.coverage,
            self.features,
            self.signals,
            self.metrics,
            self.crises,
            self.scope,
            self.portfolio_metrics,
            self.bundle_receipt,
        )
        card_snapshot = tuple(
            (card, card.body.text(), card.toolTip()) for card in mutable_cards
        )
        old_workflow_status = self.workflow_status.text()
        old_chart_status = self.chart_status.text()
        old_nav_accessible_description = self.nav_plot.accessibleDescription()
        old_drawdown_accessible_description = (
            self.drawdown_plot.accessibleDescription()
        )
        old_nav_dates = self._nav_axis._session_dates
        old_drawdown_dates = self._drawdown_axis._session_dates
        old_nav_curve = self.nav_curve
        old_drawdown_curve = self.drawdown_curve
        old_accepted = self._has_accepted_bundle
        old_button_state = (
            self.run_button.isEnabled(),
            self.reload_button.isEnabled(),
            self.export_button.isEnabled(),
        )
        nav_plot_item = self.nav_plot.getPlotItem()
        drawdown_plot_item = self.drawdown_plot.getPlotItem()
        old_nav_items = tuple(nav_plot_item.items)
        old_drawdown_items = tuple(drawdown_plot_item.items)
        old_nav_curve_present = any(
            item is old_nav_curve for item in old_nav_items
        )
        old_drawdown_curve_present = any(
            item is old_drawdown_curve for item in old_drawdown_items
        )
        old_nav_range = tuple(tuple(bounds) for bounds in self.nav_plot.viewRange())
        old_drawdown_range = tuple(
            tuple(bounds) for bounds in self.drawdown_plot.viewRange()
        )
        old_nav_auto_range = tuple(self.nav_plot.getViewBox().state["autoRange"])
        old_drawdown_auto_range = tuple(
            self.drawdown_plot.getViewBox().state["autoRange"]
        )
        updates_were_enabled = self.updatesEnabled()
        new_nav_curve = None
        new_drawdown_curve = None

        def _contains_identity(items: object, target: object) -> bool:
            return any(item is target for item in items)

        def _remove_new_items(plot_item: object, original_items: tuple[object, ...]) -> None:
            for item in tuple(plot_item.items):
                if not _contains_identity(original_items, item):
                    try:
                        plot_item.removeItem(item)
                    except Exception:
                        pass

        def _restore_old_curve(
            plot_item: object, curve: object, was_present: bool,
        ) -> None:
            if (
                curve is not None
                and was_present
                and not _contains_identity(tuple(plot_item.items), curve)
            ):
                try:
                    plot_item.addItem(curve)
                except Exception:
                    pass

        self.setUpdatesEnabled(False)
        try:
            new_nav_curve = self.nav_plot.plot(
                x, nav,
                pen=pg.mkPen("#2f6fb2", width=2),
                name="Close-proxy normalized NAV",
            )
            new_drawdown_curve = self.drawdown_plot.plot(
                x, drawdown,
                pen=pg.mkPen("#b4493a", width=2),
                brush=pg.mkBrush(180, 73, 58, 45),
                fillLevel=0.0,
                name="Close-proxy drawdown",
            )
            if (
                _contains_identity(old_nav_items, new_nav_curve)
                or not _contains_identity(
                    tuple(nav_plot_item.items), new_nav_curve,
                )
                or _contains_identity(old_drawdown_items, new_drawdown_curve)
                or not _contains_identity(
                    tuple(drawdown_plot_item.items), new_drawdown_curve,
                )
            ):
                raise RuntimeError("new backtest curves were not staged independently")
            self.render(view)
            self.portfolio_metrics.set_lines(metric_lines)
            self.bundle_receipt.set_lines(receipt_lines)
            self._nav_axis.set_session_dates(dates)
            self._drawdown_axis.set_session_dates(dates)
            self.nav_plot.enableAutoRange()
            self.drawdown_plot.enableAutoRange()
            self.chart_status.setText(
                "DEVELOPMENT-ONLY CLOSE-PROXY NAV / DRAWDOWN · "
                "NOT_EXECUTABLE_INSTRUMENT"
            )
            self.nav_plot.setAccessibleDescription(nav_accessible_description)
            self.drawdown_plot.setAccessibleDescription(
                drawdown_accessible_description
            )
            self._has_accepted_bundle = True
            self._apply_workflow_button_state()
            self.workflow_status.setText(
                "고정 입력의 검증된 development-only close-proxy 결과입니다. "
                "봉인 holdout 결과는 열지 않았습니다."
            )
            if old_nav_curve_present:
                nav_plot_item.removeItem(old_nav_curve)
            if old_drawdown_curve_present:
                drawdown_plot_item.removeItem(old_drawdown_curve)
            self.nav_curve = new_nav_curve
            self.drawdown_curve = new_drawdown_curve
        except Exception:
            _remove_new_items(nav_plot_item, old_nav_items)
            _remove_new_items(drawdown_plot_item, old_drawdown_items)
            _restore_old_curve(
                nav_plot_item, old_nav_curve, old_nav_curve_present,
            )
            _restore_old_curve(
                drawdown_plot_item,
                old_drawdown_curve,
                old_drawdown_curve_present,
            )
            for card, body, tooltip in card_snapshot:
                card.body.setText(body)
                card.setToolTip(tooltip)
            self.workflow_status.setText(old_workflow_status)
            self.chart_status.setText(old_chart_status)
            self.nav_plot.setAccessibleDescription(
                old_nav_accessible_description
            )
            self.drawdown_plot.setAccessibleDescription(
                old_drawdown_accessible_description
            )
            self._nav_axis._session_dates = old_nav_dates
            self._drawdown_axis._session_dates = old_drawdown_dates
            self._nav_axis.update()
            self._drawdown_axis.update()
            self.nav_curve = old_nav_curve
            self.drawdown_curve = old_drawdown_curve
            self._has_accepted_bundle = old_accepted
            self.run_button.setEnabled(old_button_state[0])
            self.reload_button.setEnabled(old_button_state[1])
            self.export_button.setEnabled(old_button_state[2])
            try:
                self.nav_plot.setRange(
                    xRange=old_nav_range[0], yRange=old_nav_range[1],
                    padding=0.0, disableAutoRange=False,
                )
                self.drawdown_plot.setRange(
                    xRange=old_drawdown_range[0],
                    yRange=old_drawdown_range[1],
                    padding=0.0,
                    disableAutoRange=False,
                )
                for axis, enabled in enumerate(old_nav_auto_range):
                    self.nav_plot.getViewBox().enableAutoRange(
                        axis=axis, enable=enabled,
                    )
                for axis, enabled in enumerate(old_drawdown_auto_range):
                    self.drawdown_plot.getViewBox().enableAutoRange(
                        axis=axis, enable=enabled,
                    )
            except Exception:
                pass
            raise
        finally:
            self.setUpdatesEnabled(updates_were_enabled)

    def render(self, view: BacktestExperimentView) -> None:
        if view.portfolio is None or view.bundle_receipt is None:
            self.nav_plot.setAccessibleDescription("")
            self.drawdown_plot.setAccessibleDescription("")
        warning = [f"warning: {view.warning}"] if view.warning else []
        self.experiment.set_lines([
            view.artifact_state,
            view.experiment_status,
            f"source: {view.source}",
            *warning,
        ])
        coverage = view.input_coverage
        self.coverage.set_lines([
            f"dataset: {coverage.dataset}",
            f"contract v{coverage.contract_version}",
            f"{coverage.coverage_start} to {coverage.coverage_end}",
            f"rows {_fmt(coverage.rows)} / files {_fmt(coverage.files)}",
            f"decision: {coverage.decision_rule}",
            f"manifest: {coverage.manifest_sha256[:12]}...",
        ] if coverage else ["N/A"])
        self.features.set_lines(
            [f"{item.name}: {_fmt(item.value, 4)}" for item in view.feature_set] or ["N/A"]
        )
        self.signals.set_lines([
            *(f"horizon: {item}" for item in view.horizons),
            *(f"signal: {item}" for item in view.signals),
        ] or ["N/A"])
        self.metrics.set_lines(
            [f"{item.name}: {_fmt(item.value, 4)}" for item in view.metrics] or ["N/A"]
        )
        crisis_lines = []
        for crisis in view.crises:
            prefix = f"{crisis.event}: {crisis.start} to {crisis.end} · {crisis.status}"
            if crisis.status == "UNTOUCHED_HOLDOUT":
                crisis_lines.append(prefix + " · outcomes NOT INSPECTED")
            else:
                crisis_lines.append(
                    prefix + f" · obs {_fmt(crisis.observations)} / risk-off {_fmt(crisis.risk_off_observations)}"
                    + f" · mean F20 {_fmt(crisis.mean_forward_20d_return, 4)}"
                    + f" · worst DD20 {_fmt(crisis.worst_forward_20d_drawdown, 4)}"
                )
        self.crises.set_lines(crisis_lines or ["N/A"])
        self.scope.set_lines([
            view.portfolio_scope,
            "설명용 신호 재현 · 예측 사용 불가 · 포트폴리오 백테스트 아님",
            "No features, signals, labels, metrics, positions, or returns are calculated by the GUI.",
        ])


class DataStatusPage(QtWidgets.QScrollArea):
    """Read-only shell over the DailyHealthReport-compatible summary view."""

    refresh_status_reread_requested = QtCore.Signal()

    STATUS_FILTERS = (
        ("확인 필요", "ISSUES"),
        ("전체 데이터", "ALL"),
        ("정상", "CURRENT"),
        ("정상적인 지연", "EXPECTED_LAG"),
        ("오래됨", "STALE"),
        ("상태 미확인", "UNKNOWN"),
        ("운영 차단", "BLOCKED"),
        ("일별 데이터", "DAILY"),
        ("연구/정적", "RESEARCH_STATIC"),
    )
    AREAS = (
        "전체 영역", "국내시장", "미국시장", "파생상품", "채권·금리",
        "신용·유동성", "공매도", "계좌", "기타",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        content.setMinimumWidth(0)
        self.setWidget(content)
        root = QtWidgets.QVBoxLayout(content)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(7)
        title = QtWidgets.QLabel("데이터 상태")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        subtitle = QtWidgets.QLabel(
            "문제 항목을 먼저 보여줍니다 · 정상적인 공급자 발행 대기는 오류가 아닙니다"
        )
        subtitle.setObjectName("pageSubtitle")
        root.addWidget(subtitle)
        self.overall = MetricCard("확인 필요")
        self.freshness = MetricCard("정상")
        self.eligibility = MetricCard("정상적인 지연")
        self.boundary = MetricCard("전체 데이터")
        summary = QtWidgets.QHBoxLayout()
        summary.setSpacing(7)
        for card in (self.overall, self.freshness, self.eligibility, self.boundary):
            card.setFixedHeight(102)
            card.setFocusPolicy(QtCore.Qt.StrongFocus)
            card.installEventFilter(self)
            summary.addWidget(card, 1)
        root.addLayout(summary)

        self.refresh_lifecycle_group = QtWidgets.QGroupBox("통합 갱신 상태")
        self.refresh_lifecycle_group.setCheckable(True)
        self.refresh_lifecycle_group.setChecked(True)
        self.refresh_lifecycle_group.setAccessibleName(
            "데이터 화면별 갱신 주기와 마지막 성공 상세"
        )
        lifecycle_layout = QtWidgets.QVBoxLayout(self.refresh_lifecycle_group)
        lifecycle_layout.setContentsMargins(9, 7, 9, 7)
        self.refresh_lifecycle_table = QtWidgets.QTableWidget(0, 6)
        self.refresh_lifecycle_table.setHorizontalHeaderLabels([
            "화면", "주기", "상태", "데이터 기준", "마지막 성공", "다음/조치",
        ])
        self.refresh_lifecycle_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.refresh_lifecycle_table.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection
        )
        self.refresh_lifecycle_table.setAlternatingRowColors(True)
        self.refresh_lifecycle_table.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff
        )
        lifecycle_header = self.refresh_lifecycle_table.horizontalHeader()
        lifecycle_header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 6):
            lifecycle_header.setSectionResizeMode(
                column, QtWidgets.QHeaderView.ResizeToContents
            )
        lifecycle_layout.addWidget(self.refresh_lifecycle_table)
        lifecycle_actions = QtWidgets.QHBoxLayout()
        self.refresh_lifecycle_summary = QtWidgets.QLabel("갱신 상태 미로드")
        self.refresh_lifecycle_summary.setObjectName("compactMeta")
        lifecycle_actions.addWidget(self.refresh_lifecycle_summary)
        lifecycle_actions.addStretch()
        self.refresh_lifecycle_reread = QtWidgets.QPushButton("로컬 상태 다시 읽기")
        self.refresh_lifecycle_reread.setAccessibleName(
            "통합 갱신 상태 로컬 다시 읽기"
        )
        self.refresh_lifecycle_reread.setToolTip(
            "이미 저장된 로컬 상태만 다시 읽습니다. API나 예약 작업을 시작하지 않습니다."
        )
        self.refresh_lifecycle_reread.clicked.connect(
            self.refresh_status_reread_requested
        )
        lifecycle_actions.addWidget(self.refresh_lifecycle_reread)
        lifecycle_layout.addLayout(lifecycle_actions)
        root.addWidget(self.refresh_lifecycle_group)
        self._summary_filters = {
            self.overall: "ISSUES",
            self.freshness: "CURRENT",
            self.eligibility: "EXPECTED_LAG",
            self.boundary: "ALL",
        }

        self.current_group = QtWidgets.QGroupBox("현재 표시 데이터 출처·세션")
        self.current_group.setCheckable(True)
        self.current_group.setChecked(False)
        self.current_group.setAccessibleName("현재 표시 데이터 출처와 세션 상세 펼치기 또는 접기")
        current_layout = QtWidgets.QVBoxLayout(self.current_group)
        current_layout.setContentsMargins(9, 7, 9, 7)
        self.current_source_table = QtWidgets.QTableWidget(0, 7)
        self.current_source_table.setHorizontalHeaderLabels([
            "데이터", "판정", "유효시각", "출처", "시간 기준", "세션", "다음 조치",
        ])
        self.current_source_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.current_source_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.current_source_table.setAlternatingRowColors(True)
        self.current_source_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.current_source_table.verticalHeader().setDefaultSectionSize(27)
        current_header = self.current_source_table.horizontalHeader()
        current_header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 7):
            current_header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        current_layout.addWidget(self.current_source_table)
        self.current_source_table.hide()
        root.addWidget(self.current_group)

        self.decision_group = QtWidgets.QGroupBox("대시보드 자동 판정 매트릭스")
        self.decision_group.setCheckable(True)
        self.decision_group.setChecked(False)
        self.decision_group.setAccessibleName("대시보드 자동 판정 상세 펼치기 또는 접기")
        decision_layout = QtWidgets.QVBoxLayout(self.decision_group)
        decision_layout.setContentsMargins(9, 7, 9, 7)
        self.dashboard_decision_table = QtWidgets.QTableWidget(0, 6)
        self.dashboard_decision_table.setHorizontalHeaderLabels([
            "화면 항목", "판정", "관측 기준", "출처/경로", "세션·최종성", "다음 조치",
        ])
        self.dashboard_decision_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.dashboard_decision_table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.dashboard_decision_table.setAlternatingRowColors(True)
        self.dashboard_decision_table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        decision_header = self.dashboard_decision_table.horizontalHeader()
        decision_header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 6):
            decision_header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        decision_layout.addWidget(self.dashboard_decision_table)
        self.dashboard_decision_table.hide()
        root.addWidget(self.decision_group)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("보기"))
        self.status_filter = QtWidgets.QComboBox()
        for label, key in self.STATUS_FILTERS:
            self.status_filter.addItem(label, key)
        controls.addWidget(self.status_filter)
        controls.addSpacing(8)
        controls.addWidget(QtWidgets.QLabel("영역"))
        self.area_filter = QtWidgets.QComboBox()
        self.area_filter.addItems(self.AREAS)
        controls.addWidget(self.area_filter)
        controls.addSpacing(8)
        controls.addWidget(QtWidgets.QLabel("검색"))
        self.text_filter = QtWidgets.QLineEdit()
        self.text_filter.setClearButtonEnabled(True)
        self.text_filter.setPlaceholderText("데이터·출처·차단 사유·다음 조치")
        self.text_filter.setAccessibleName("데이터 상태 텍스트 검색")
        controls.addWidget(self.text_filter, 1)
        self.reset_filters_button = QtWidgets.QPushButton("초기화")
        self.reset_filters_button.setAccessibleName("데이터 상태 필터와 검색 초기화")
        controls.addWidget(self.reset_filters_button)
        controls.addStretch()
        self.report_state = QtWidgets.QLabel("상태 보고서 미로드")
        self.report_state.setObjectName("compactMeta")
        controls.addWidget(self.report_state)
        root.addLayout(controls)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "데이터", "상태", "기준일", "예상일", "자동 업데이트",
        ])
        self.table.setAccessibleName("데이터 상태 목록 · 행을 선택하면 상세 정보 표시")
        self.status_filter.setAccessibleName("데이터 상태 보기 필터")
        self.area_filter.setAccessibleName("데이터 업무 영역 필터")
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        root.addWidget(self.table, 1)

        self.detail_panel = QtWidgets.QGroupBox("선택한 데이터 상세")
        self.detail_panel.setCheckable(True)
        self.detail_panel.setChecked(True)
        self.detail_panel.setAccessibleName("선택한 데이터의 기술 상세 펼치기 또는 접기")
        detail_layout = QtWidgets.QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(9, 7, 9, 7)
        self.detail_text = QtWidgets.QLabel("행을 선택하면 정확한 기술 상태를 확인할 수 있습니다.")
        self.detail_text.setObjectName("compactMeta")
        self.detail_text.setWordWrap(True)
        self.detail_text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.detail_text.setAccessibleName("선택한 데이터 기술 상세")
        detail_layout.addWidget(self.detail_text)
        root.addWidget(self.detail_panel)

        self._report_rows: tuple[HealthDatasetRow, ...] = ()
        self._visible_rows: tuple[HealthDatasetRow, ...] = ()
        self.status_filter.currentTextChanged.connect(self._apply_filter)
        self.area_filter.currentTextChanged.connect(self._apply_filter)
        self.text_filter.textChanged.connect(self._apply_filter)
        self.reset_filters_button.clicked.connect(self._reset_filters)
        self.current_group.toggled.connect(self.current_source_table.setVisible)
        self.refresh_lifecycle_group.toggled.connect(
            self.refresh_lifecycle_table.setVisible
        )
        self.decision_group.toggled.connect(self.dashboard_decision_table.setVisible)
        self.table.currentCellChanged.connect(self._show_selected_detail)
        self.table.itemActivated.connect(lambda _item: self.detail_panel.setChecked(True))
        self.detail_panel.toggled.connect(self.detail_text.setVisible)
        QtWidgets.QWidget.setTabOrder(self.status_filter, self.area_filter)
        QtWidgets.QWidget.setTabOrder(self.area_filter, self.text_filter)
        QtWidgets.QWidget.setTabOrder(self.text_filter, self.reset_filters_button)
        QtWidgets.QWidget.setTabOrder(self.reset_filters_button, self.table)
        QtWidgets.QWidget.setTabOrder(self.table, self.detail_panel)

    def render_refresh_status(self, projection: RefreshStatusProjection) -> None:
        labels = {
            "DASHBOARD_CURRENT": "현재 시장",
            "DATA_HEALTH": "데이터 상태",
            "ACCOUNT_SNAPSHOT": "계좌",
            "US_MARKET_FLOW": "미국 수급",
        }
        cadence = {
            "FIXED_INTERVAL": lambda row: (
                f"{int(row.cadence_seconds / 60)}분" if row.cadence_seconds else "주기 미확인"
            ),
            "SCHEDULED_LOCAL": lambda _row: "로컬 예약",
            "MANUAL": lambda _row: "수동",
            "UNSUPPORTED": lambda _row: "미지원",
        }
        state_labels = {
            "SUCCEEDED": "정상",
            "IN_PROGRESS": "진행 중",
            "PARTIAL_FAILURE": "일부 실패",
            "FAILED": "실패",
            "UNKNOWN": "확인 필요",
            "UNSUPPORTED": "미지원",
        }
        self.refresh_lifecycle_table.setRowCount(len(projection.surfaces))
        for row_index, surface in enumerate(projection.surfaces):
            cadence_text = cadence.get(surface.cadence_kind, lambda _row: "확인 필요")(surface)
            action = (
                "로컬 다시 읽기"
                if surface.retry_action_id == "dashboard-local-reread"
                else "계좌 화면에서 수동 갱신"
                if surface.retry_capability == "READONLY_REFRESH_REQUEST"
                else "지원 경로 없음"
                if surface.retry_capability == "NONE"
                else "대기"
            )
            next_text = (
                surface.next_eligible_at
                if surface.next_eligible_at is not None
                else f"다음 시각 미확정 · {action}"
            )
            values = (
                labels[surface.surface_id], cadence_text,
                state_labels.get(surface.operation_state, "확인 필요"),
                surface.source_as_of or surface.market_date or "확인되지 않음",
                surface.last_success_at or "확인되지 않음", next_text,
            )
            detail = (
                f"surface={surface.surface_id}\nsemantics={surface.observation_semantics}\n"
                f"operation={surface.operation_state}\nfreshness={surface.freshness_state}\n"
                f"source_basis={surface.source_time_basis}\n"
                f"retained={surface.retained_value_state}\n"
                f"next_basis={surface.next_eligible_basis}\n"
                f"retry={surface.retry_capability}\n"
                f"reasons={','.join(surface.reason_codes) or 'none'}"
            )
            for column_index, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(detail)
                self.refresh_lifecycle_table.setItem(
                    row_index, column_index, item
                )
        self.refresh_lifecycle_table.setFixedHeight(
            34 + len(projection.surfaces)
            * self.refresh_lifecycle_table.verticalHeader().defaultSectionSize()
        )
        self.refresh_lifecycle_summary.setText(
            {
                "SUCCEEDED": "모든 지원 화면의 최근 갱신 상태가 정상입니다.",
                "IN_PROGRESS": "로컬 갱신 상태를 확인 중입니다.",
                "PARTIAL_FAILURE": "일부 화면은 보존값 또는 확인이 필요합니다.",
                "FAILED": "지원 화면 갱신이 실패했습니다.",
                "UNKNOWN": "확인되지 않은 갱신 상태가 있습니다.",
            }.get(projection.overall_state, "갱신 상태를 확인할 수 없습니다.")
        )
        self.refresh_lifecycle_summary.setToolTip(
            _refresh_projection_tooltip(projection)
        )

    def eventFilter(self, watched, event) -> bool:
        status_filter = getattr(self, "_summary_filters", {}).get(watched)
        if status_filter is not None:
            activated = (
                event.type() == QtCore.QEvent.MouseButtonRelease
                and event.button() == QtCore.Qt.LeftButton
            ) or (
                event.type() == QtCore.QEvent.KeyPress
                and event.key() in {QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Space}
            )
            if activated:
                self._activate_summary_filter(status_filter)
                return True
        return super().eventFilter(watched, event)

    def _activate_summary_filter(self, status_filter: str) -> None:
        index = self.status_filter.findData(status_filter)
        if index >= 0:
            self.status_filter.setCurrentIndex(index)
        self.table.setFocus(QtCore.Qt.OtherFocusReason)
        self.ensureWidgetVisible(self.table)

    def _reset_filters(self) -> None:
        self.status_filter.setCurrentIndex(self.status_filter.findData("ISSUES"))
        self.area_filter.setCurrentIndex(0)
        self.text_filter.clear()
        self._apply_filter()

    @staticmethod
    def _current_session_label(
        coverage_id: str, freshness: str, session: MarketSessionBarState,
    ) -> str:
        if freshness in {"MARKET_CLOSED_LAST_FINAL", "MARKET_CLOSED_LAST_VERIFIED"}:
            return "국내 장마감"
        if coverage_id in {
            "KOSPI", "KOSDAQ", "EQUITY_000660", "EQUITY_000660_NXT_CLOSE",
            "EQUITY_005930_NXT_CLOSE", "TOSS_KOSPI", "TOSS_KOSDAQ", "LS_T8412",
            "KB_IVSA0070",
        }:
            return session.domestic_label
        if coverage_id in {
            "SP500", "NASDAQ", "SOXX", "SP500_CURRENT_60M",
            "SPY_CURRENT_60M", "NASDAQ_CURRENT_60M", "SOXX_CURRENT_60M",
        }:
            return session.us_label
        if coverage_id in {
            "NQ_FUTURES", "GOLD", "WTI", "UST2_FUTURES_60M",
            "UST10_FUTURES_60M", "UST30_FUTURES_60M",
            "NQ_FUTURES_CURRENT_60M",
        }:
            return "선물 세션 별도"
        return "세션 비적용"

    def render_current_sources(
        self,
        coverage: dict[str, CurrentObservationCoverageView],
        *,
        as_of_utc: object,
    ) -> None:
        """Render provenance and session facts outside the Dashboard surface."""
        session = _market_session_bar_state(as_of_utc)
        rows = [coverage[key] for key in sorted(coverage)]
        self.current_source_table.setRowCount(len(rows))
        basis_labels = {
            "PROVIDER_TIMESTAMP": "제공시각",
            "RETRIEVAL_TIMESTAMP": "조회시각",
        }
        for row_index, view in enumerate(rows):
            basis = basis_labels.get(view.timestamp_basis, "시간 기준 미확정")
            effective_time = (
                view.retrieved_at_utc
                if view.timestamp_basis == "RETRIEVAL_TIMESTAMP"
                else view.provider_timestamp_utc
            )
            status = (
                "ACCEPT" if view.displays_value else
                "REFRESH" if view.display_state is DashboardDisplayState.REFRESH_REQUIRED else
                "BLOCK" if view.display_state is DashboardDisplayState.PROHIBITED else
                "HOLD"
            )
            action = (
                "KEEP" if view.displays_value else
                "RUN_AUTHORIZED_LANE" if view.display_state is DashboardDisplayState.REFRESH_REQUIRED else
                "DO_NOT_USE" if view.display_state is DashboardDisplayState.PROHIBITED else
                "VERIFY_SOURCE_OR_CONTRACT"
            )
            values = (
                view.label,
                status,
                effective_time or "N/A",
                view.provider,
                basis,
                self._current_session_label(view.coverage_id, view.freshness, session),
                action,
            )
            detail = (
                f"identity={view.coverage_id}\nprovider={view.provider}\nroute={view.route}\n"
                f"timestamp_basis={view.timestamp_basis}\n"
                f"provider_timestamp_utc={view.provider_timestamp_utc or 'N/A'}\n"
                f"retrieved_at_utc={view.retrieved_at_utc or 'N/A'}\n"
                f"effective_display_timestamp_utc={effective_time or 'N/A'}\n"
                f"session={self._current_session_label(view.coverage_id, view.freshness, session)}\n"
                f"freshness={view.freshness}\nfinality={view.finality}\n"
                f"reason={view.unavailable_reason or 'accepted retained observation'}"
            )
            for column_index, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(detail)
                self.current_source_table.setItem(row_index, column_index, item)
        self.current_source_table.setFixedHeight(
            min(245, 32 + max(1, len(rows)) * self.current_source_table.verticalHeader().defaultSectionSize())
        )

    @staticmethod
    def _metric_decision(metric: DashboardMetricView | None) -> tuple[str, str]:
        if metric is None:
            return "UNKNOWN", "VERIFY_LOCAL_INPUT"
        if metric.display_state is DashboardDisplayState.PROHIBITED:
            return "BLOCKED", "DO_NOT_USE"
        if metric.display_state is DashboardDisplayState.REFRESH_REQUIRED:
            if not metric.automation_enabled:
                return "UNAVAILABLE", "VERIFY_SOURCE_OR_CONTRACT"
            return "STALE", "RUN_AUTHORIZED_LANE"
        if not metric.displays_value:
            return "UNAVAILABLE", "VERIFY_SOURCE_OR_CONTRACT"
        if metric.freshness == "EXPECTED_LAG":
            return "EXPECTED_LAG", "WAIT_PROVIDER_SCHEDULE"
        return "ACCEPT", "KEEP"

    def render_dashboard_decisions(self, data: dict) -> None:
        """Expose enough typed facts for an agent to decide without Dashboard prose."""
        metrics = {
            key: value for key, value in data.get("dashboard_metrics", {}).items()
            if isinstance(value, DashboardMetricView)
        }
        series = {
            key: value for key, value in data.get("dashboard_series", {}).items()
            if isinstance(value, DashboardSeriesView)
        }
        rows: list[tuple[str, str, str, str, str, str, str]] = []
        nq_series = series.get("NQ_FUTURES")
        if nq_series is not None and not nq_series.frame.empty:
            daily_date = pd.Timestamp(nq_series.frame["date"].iloc[-1]).date().isoformat()
            rows.append((
                "NQ=F 일봉 차트", "ACCEPT_DAILY_ONLY", daily_date,
                "retained daily OHLC", "완료 일봉 · 연속선물",
                "KEEP_SEPARATE_FROM_60M", "일봉 차트와 60분 현재 관측을 혼합하지 않음",
            ))
        else:
            rows.append((
                "NQ=F 일봉 차트", "UNKNOWN", "N/A", "retained daily OHLC",
                "완료 일봉 · 연속선물", "VERIFY_LOCAL_INPUT", "보존 일봉 없음",
            ))
        labels = {
            "NQ_FUTURES": "NQ=F 60분 현재 관측",
            "USD_KRW_60M": "USD/KRW 최근 완료 FX 세션",
            "USD_JPY": "USD/JPY 공식 일별",
            "KOSPI200_BASIS": "KOSPI200 Basis",
            "VOLUME_PCR": "KOSPI200 거래량 P/C",
            "OI_PCR": "KOSPI200 OI P/C",
            "VKOSPI": "VKOSPI",
            "CALL_WALL": "Call 최대 OI",
            "PUT_WALL": "Put 최대 OI",
        }
        for key, label in labels.items():
            metric = metrics.get(key)
            decision, action = self._metric_decision(metric)
            rows.append((
                label, decision, (metric.as_of or "N/A") if metric else "N/A",
                (f"{metric.source} / {metric.route}" if metric else "N/A"),
                (f"freshness={metric.freshness}; pit={metric.pit_status}" if metric else "UNKNOWN"),
                action, (metric.unavailable_reason or "accepted typed metric") if metric else "metric missing",
            ))
        rows.extend((
            ("한국 국채", "BLOCKED_FINALITY", "N/A", "BOK ECOS 817Y002",
             "publication/finality 미검증", "VERIFY_PUBLICATION_FINALITY", "최종성 검증 전 숫자 표시 금지"),
            ("VIX 선물 · CFE VX", "BLOCKED_IDENTITY", "N/A", "Yahoo→CFE VX",
             "상품·만기·롤 식별 미검증", "VERIFY_CONTRACT_IDENTITY", "현물 VIX나 유사 상품으로 대체 금지"),
            ("미국 옵션 P/C", "BLOCKED_ENTITLEMENT", "N/A", "Cboe/Nasdaq scopes",
             "라이선스·정확 소스 미확정", "OBTAIN_ENTITLEMENT_AND_SOURCE", "Cboe 범위와 Nasdaq 후보를 하나의 출처로 취급하지 않음"),
        ))
        self.dashboard_decision_table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            detail = values[-1]
            for column_index, value in enumerate(values[:-1]):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(detail)
                self.dashboard_decision_table.setItem(row_index, column_index, item)
        self.dashboard_decision_table.setFixedHeight(
            min(360, 34 + len(rows) * self.dashboard_decision_table.verticalHeader().defaultSectionSize())
        )

    def render(self, health: dict) -> None:
        self.overall.set_lines([
            f"오래됨 {_fmt(health.get('stale', 0))} · 읽기 실패 {_fmt(health.get('failed', 0))}",
            f"운영 차단 {_fmt(health.get('operational_blocked', 0))}",
        ])
        self.freshness.set_lines([
            f"{_freshness_label('CURRENT')} {_fmt(health.get('current', 0))}",
        ])
        self.eligibility.set_lines([
            f"{_freshness_label('EXPECTED_LAG')} {_fmt(health.get('expected_lag', 0))}",
            "공급자 일정상 정상",
        ])
        self.boundary.set_lines([
            "로컬 보존 데이터 · 읽기 전용",
            f"연구/정적 {_fmt(health.get('research_only', 0))}",
        ])

    @staticmethod
    def _is_research_static(row: HealthDatasetRow) -> bool:
        return row.automation.startswith(("RESEARCH_ONLY", "NO_REFRESH"))

    def render_report(self, view: HealthArtifactView) -> None:
        self._report_rows = view.rows
        if view.rows:
            summary = summarize_health_artifact(view)
            freshness = Counter(row.freshness for row in view.rows)
            operational_blocked = sum(row.operational == "BLOCKED" for row in view.rows)
            predictive_blocked = sum(row.pit == "PIT_BLOCKED" for row in view.rows)
            research_static = sum(
                self._is_research_static(row) for row in view.rows
            )
            issue_count = sum(
                row.freshness in {"STALE", "UNKNOWN"} or row.operational == "BLOCKED"
                for row in view.rows
            )
            self.overall.set_lines([
                f"자동 운영 {summary['managed_acceptable']}/{summary['managed_total']} 정상",
                f"화면 후보 갱신 필요 {summary['display_stale']} · 확인 필요 {summary['display_unknown']}",
            ])
            self.freshness.set_lines([
                f"전체 재고 · {_freshness_label('CURRENT')} {freshness['CURRENT']}",
                f"관리 자동화 CURRENT {summary['managed_current']}",
            ])
            self.eligibility.set_lines([
                f"관리 자동화 발행 대기 {summary['managed_expected_lag']}",
                "아티팩트 EXPECTED_LAG 판정만 정상 범위",
            ])
            self.boundary.set_lines([
                f"전체 {len(view.rows)} · 연구/정적 {research_static}",
                f"확인 대상 {issue_count} · 예측 사용 제한 {predictive_blocked} · 로컬 읽기 전용",
            ])
        detail = f"{'준비됨' if view.artifact_state == 'READY' else '사용 불가'} · {view.source}"
        if view.warning:
            detail += f" · {view.warning}"
        self.report_state.setText(detail)
        self._apply_filter()

    @staticmethod
    def _matches_status(row: HealthDatasetRow, status_filter: str) -> bool:
        if status_filter == "ALL":
            return True
        if status_filter == "ISSUES":
            return row.freshness in {"STALE", "UNKNOWN"} or row.operational == "BLOCKED"
        if status_filter == "BLOCKED":
            return row.operational == "BLOCKED"
        if status_filter == "DAILY":
            return row.cadence == "DAILY"
        if status_filter == "RESEARCH_STATIC":
            return DataStatusPage._is_research_static(row)
        return row.freshness == status_filter

    @staticmethod
    def _row_priority(row: HealthDatasetRow) -> tuple[int, str]:
        priority = (
            0 if row.operational == "BLOCKED" else
            1 if row.freshness == "STALE" else
            2 if row.freshness == "UNKNOWN" else
            3 if row.freshness == "EXPECTED_LAG" else
            4 if row.freshness == "CURRENT" else 5
        )
        return priority, row.dataset

    @staticmethod
    def _technical_detail(row: HealthDatasetRow) -> str:
        return (
            f"dataset_id={row.dataset}\n"
            f"업무 영역={_data_status_area(row)} · role={row.role} · cadence={row.cadence}\n"
            f"typed freshness={row.freshness} · 표시={_freshness_label(row.freshness)}\n"
            f"기준일={row.latest} · 예상일={row.expected}\n"
            f"operational={row.operational} · blocker={row.blocker}\n"
            f"PIT={_pit_display(row.pit)}\n"
            f"provenance/source={row.source}\n"
            f"automation={row.automation} · runtime coverage={row.runtime_coverage}"
        )

    def _apply_filter(self, _value: str = "") -> None:
        status_filter = str(self.status_filter.currentData() or "ISSUES")
        area = self.area_filter.currentText() or "전체 영역"
        query = self.text_filter.text().strip().casefold()
        rows = tuple(
            row for row in self._report_rows
            if self._matches_status(row, status_filter)
            and (area == "전체 영역" or _data_status_area(row) == area)
            and (
                not query
                or query in "\n".join((
                    row.dataset,
                    _data_status_display_name(row.dataset),
                    self._technical_detail(row),
                    _data_status_update_label(row.automation),
                )).casefold()
            )
        )
        rows = tuple(sorted(rows, key=self._row_priority))
        self._visible_rows = rows
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = (
                _data_status_display_name(row.dataset),
                f"● {_freshness_label(row.freshness)}",
                row.latest,
                row.expected,
                _data_status_update_label(row.automation),
            )
            for column_index, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setToolTip(self._technical_detail(row))
                if column_index == 0:
                    item.setData(QtCore.Qt.UserRole, row)
                if column_index == 1:
                    colors = {
                        "CURRENT": ("#176b49", "#e7f5ee"),
                        "EXPECTED_LAG": ("#315a84", "#eaf1f8"),
                        "STALE": ("#a33f38", "#fbeceb"),
                        "UNKNOWN": ("#8a5b12", "#fff3d6"),
                    }
                    foreground, background = colors.get(row.freshness, ("#52677f", "#edf3f8"))
                    item.setForeground(QtGui.QColor(foreground))
                    item.setBackground(QtGui.QColor(background))
                self.table.setItem(row_index, column_index, item)
        self.table.setSortingEnabled(True)
        if rows:
            self.table.selectRow(0)
            self._show_selected_detail(0, 0, -1, -1)
        else:
            self.detail_text.setText("선택한 조건에 해당하는 데이터가 없습니다.")

    def _show_selected_detail(
        self, current_row: int, _current_column: int,
        _previous_row: int, _previous_column: int,
    ) -> None:
        item = self.table.item(current_row, 0) if current_row >= 0 else None
        row = item.data(QtCore.Qt.UserRole) if item is not None else None
        if isinstance(row, HealthDatasetRow):
            self.detail_text.setText(self._technical_detail(row))


@dataclass(frozen=True)
class AccountWorkspaceView:
    primary: AccountSnapshotView
    portfolio: AccountPortfolioView


@dataclass(frozen=True)
class _BacktestLegacyReload:
    """Legacy read used only when no validated workflow bundle is available."""

    view: BacktestExperimentView


class BacktestRunWorker(QtCore.QObject):
    """Run every replay/bundle file operation on one background lane."""

    completed = QtCore.Signal(str, object)
    failed = QtCore.Signal(object)
    ACTIONS = frozenset({"RUN", "RELOAD", "EXPORT", "SCENARIO"})

    def __init__(
        self,
        service: BacktestResultService,
        action: str,
        *,
        accepted_bundle: object | None = None,
        destination: Path | None = None,
        diagnostic_run_id: str | None = None,
        scenario_service: BacktestScenarioService | None = None,
        scenario_inputs: BacktestScenarioInputs | None = None,
    ) -> None:
        super().__init__()
        if action not in self.ACTIONS:
            raise ValueError("unsupported backtest worker action")
        self.service = service
        self.action = action
        self.accepted_bundle = accepted_bundle
        self.destination = destination
        self.diagnostic_run_id = diagnostic_run_id or new_session_id()
        self.scenario_service = scenario_service
        self.scenario_inputs = scenario_inputs

    @QtCore.Slot()
    def run(self) -> None:
        try:
            if self.action == "RUN":
                self.service._diagnostic_run_id = self.diagnostic_run_id
                try:
                    result = self.service.run_validated()
                finally:
                    self.service.__dict__.pop("_diagnostic_run_id", None)
            elif self.action == "RELOAD":
                try:
                    result = self.service.load_validated_bundle()
                except Exception:
                    result = _BacktestLegacyReload(self.service.load())
            elif self.action == "EXPORT":
                if self.accepted_bundle is None or self.destination is None:
                    raise ValueError("validated bundle and export destination are required")
                result = self.service.export_exact_bundle(
                    self.accepted_bundle, self.destination,
                )
            else:
                if (
                    type(self.scenario_service) is not BacktestScenarioService
                    or type(self.scenario_inputs) is not BacktestScenarioInputs
                ):
                    raise ValueError("typed scenario service and inputs are required")
                result = self.scenario_service.evaluate(self.scenario_inputs)
        except Exception as error:
            if self.action != "RUN":
                self.failed.emit(error)
            result = error
        self.completed.emit(self.action, result)


class AccountSnapshotWorker(QtCore.QObject):
    """Load/optionally refresh an account snapshot outside the GUI thread."""

    completed = QtCore.Signal(object)
    failed = QtCore.Signal(object)

    def __init__(
        self,
        service: LocalAccountSnapshotService,
        portfolio_service: LocalAccountPortfolioService,
        trigger: AccountRefreshTrigger | None,
        refresher: Callable[[AccountRefreshTrigger], object] | None = None,
        *,
        primary_enabled: bool = True,
        unavailable_reason: str = "RUNTIME_CONFIG_REQUIRED",
        kb_refresher: Callable[[AccountRefreshTrigger], object] | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.portfolio_service = portfolio_service
        if trigger not in (None, AccountRefreshTrigger.MANUAL):
            raise ValueError("desktop account worker accepts only local load or MANUAL")
        self.trigger = trigger
        self.refresher = refresher
        self.primary_enabled = primary_enabled
        self.unavailable_reason = unavailable_reason
        self.kb_refresher = kb_refresher

    @QtCore.Slot()
    def run(self) -> None:
        try:
            failures: dict[str, str] = {}
            if self.trigger is AccountRefreshTrigger.MANUAL:
                for source_id, refresher in (
                    ("toss_self", self.refresher),
                    ("kb_self", self.kb_refresher),
                ):
                    if refresher is None:
                        continue
                    try:
                        refresh_result = refresher(self.trigger)
                    except Exception:
                        failures[source_id] = "ACCOUNT_REFRESH_FAILED_CLOSED"
                        continue
                    if getattr(refresh_result, "status", None) == "FAILED_PRESERVED_PRIOR":
                        reason = getattr(refresh_result, "reason", None)
                        failures[source_id] = (
                            reason
                            if isinstance(reason, str) and reason
                            else "ACCOUNT_REFRESH_FAILED_CLOSED"
                        )
            sources = tuple(
                replace(
                    source,
                    enabled=False,
                    unavailable_reason=failures[source.source_id],
                )
                if source.source_id in failures
                else source
                for source in self.portfolio_service.sources
            )
            portfolio = LocalAccountPortfolioService(
                sources,
                manual_store=self.portfolio_service.manual_store,
                history_root=self.portfolio_service.history_root,
            ).load()
            failure_views = {
                source_id: AccountSnapshotView(
                    state=AccountSnapshotState.NOT_AVAILABLE,
                    reason=reason,
                    freshness="READ_FAILURE",
                )
                for source_id, reason in failures.items()
            }
            if failure_views:
                portfolio = replace(
                    portfolio,
                    entries=tuple(
                        replace(entry, snapshot=failure_views[entry.source_id])
                        if entry.source_id in failure_views
                        else entry
                        for entry in portfolio.entries
                    ),
                )
            primary = (
                failure_views["toss_self"]
                if "toss_self" in failure_views
                else self.service.load()
                if self.primary_enabled
                else AccountSnapshotView(
                    state=AccountSnapshotState.NOT_AVAILABLE,
                    reason=self.unavailable_reason,
                )
            )
            result = AccountWorkspaceView(primary=primary, portfolio=portfolio)
        except Exception as error:
            self.failed.emit(error)
            unavailable = AccountSnapshotView(
                state=AccountSnapshotState.NOT_AVAILABLE,
                reason="계좌 snapshot 갱신 또는 읽기 실패",
                freshness="READ_FAILURE",
            )
            result = AccountWorkspaceView(
                primary=unavailable,
                portfolio=AccountPortfolioView(entries=(), user_fund_totals=()),
            )
        self.completed.emit(result)


class CurrentObservationAcquisitionWorker(QtCore.QObject):
    """Request one approved collector run outside the GUI thread.

    The injected callable is the sole acquisition boundary.  This widget layer
    neither constructs provider transports nor interprets their responses; it
    only rereads the collector's atomic local projection once that boundary
    returns.
    """

    completed = QtCore.Signal(object)
    failed = QtCore.Signal(object)

    def __init__(self, runner: Callable[[], object]) -> None:
        super().__init__()
        self.runner = runner

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result: object = self.runner()
        except Exception as error:  # The local projection remains authoritative.
            self.failed.emit(error)
            result = error
        self.completed.emit(result)


@dataclass(frozen=True)
class _DashboardLocalRead:
    snapshot: object
    market_frame: object
    health_view: object
    market_generation: int
    current_stage_generation: int = -1


class DashboardCurrentStageWorker(QtCore.QObject):
    """Read provider-free current JSON projections on an independent lane."""

    completed = QtCore.Signal(int, object)
    failed = QtCore.Signal(object)

    def __init__(self, service: DashboardService, generation: int) -> None:
        super().__init__()
        self.service = service
        self.generation = generation

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result: object = self.service.current_card_stage()
        except Exception as error:
            self.failed.emit(error)
            result = error
        self.completed.emit(self.generation, result)


class LocalReadWorker(QtCore.QObject):
    """Run one coalesced Dashboard/Index local read on the shared bounded lane."""

    completed = QtCore.Signal(int, str, object)
    failed = QtCore.Signal(object)

    def __init__(
        self,
        service: DashboardService,
        health_service: object,
        generation: int,
        action: str,
        request: object,
    ) -> None:
        super().__init__()
        if action not in {"dashboard", "index", "market_chart"}:
            raise ValueError("unsupported local-read action")
        self.service = service
        self.health_service = health_service
        self.generation = generation
        self.action = action
        self.request = request

    @staticmethod
    def _read_or_error(reader: Callable[[], object]) -> object:
        try:
            return reader()
        except Exception as error:
            return error

    @QtCore.Slot()
    def run(self) -> None:
        try:
            if self.action == "dashboard":
                if len(self.request) == 5:
                    session, asset, period, market_generation, current_generation = self.request
                else:
                    session, asset, period, market_generation = self.request
                    current_generation = -1
                result: object = _DashboardLocalRead(
                    snapshot=self._read_or_error(
                        lambda: self.service.snapshot(session)
                    ),
                    market_frame=self._read_or_error(
                        lambda: self.service.chart_series(asset, period)
                    ),
                    health_view=self._read_or_error(self.health_service.load),
                    market_generation=market_generation,
                    current_stage_generation=current_generation,
                )
            elif self.action == "index":
                index, period = self.request
                result = self.service.index.chart_view(index, period)
            else:
                asset, period = self.request
                result = self.service.chart_series(asset, period)
        except Exception as error:
            self.failed.emit(error)
            result = error
        self.completed.emit(self.generation, self.action, result)


class EquityChartWorker(QtCore.QObject):
    """Run bounded local catalog/Parquet reads outside the GUI thread."""

    completed = QtCore.Signal(str, object, object)
    failed = QtCore.Signal(object)

    def __init__(self, service, action: str, request: object) -> None:
        super().__init__()
        self.service = service
        self.action = action
        self.request = request

    @QtCore.Slot()
    def run(self) -> None:
        try:
            if self.action == "search":
                result = self.service.search(str(self.request))
            elif self.action == "global_search":
                query = str(self.request)
                kr = self.service.equity.search(query)
                us = self.service.us_etf.search(query)
                reasons = tuple(
                    reason for reason in (kr.unavailable_reason, us.unavailable_reason)
                    if reason
                )
                result = EquitySearchView(
                    query=query,
                    matches=tuple(kr.matches) + tuple(us.matches),
                    unavailable_reason=" · ".join(reasons) or None,
                )
            elif self.action in {"series", "research_series"}:
                identity, period = self.request
                result = self.service.series(identity, period)
            elif self.action == "comparison":
                result = self.service.benchmark_comparison(self.request)
            elif self.action == "watchlist":
                result = tuple(
                    quote_from_series(
                        (
                            self.service.us_etf
                            if identity.is_us_etf and hasattr(self.service, "us_etf")
                            else self.service.equity
                            if hasattr(self.service, "equity")
                            else self.service
                        ).series(identity, "20D")
                    )
                    for identity in self.request
                )
            elif self.action == "candidate_scan":
                result = self.service.scan()
            elif self.action == "candidate_identity":
                _market, symbol = self.request
                result = self.service.search(str(symbol))
            else:
                raise ValueError("unsupported equity worker action")
        except Exception as error:
            self.failed.emit(error)
            result = error
        self.completed.emit(self.action, self.request, result)


class DetachedChartWorker(QtCore.QObject):
    """Run one detached chart's bounded local read outside the GUI thread."""

    completed = QtCore.Signal(str, object, object)

    def __init__(self, service: DashboardService, action: str, request: object) -> None:
        super().__init__()
        self.service = service
        self.action = action
        self.request = request

    @QtCore.Slot()
    def run(self) -> None:
        try:
            if self.action == "index":
                index, period = self.request
                result = self.service.index.chart_view(index, period)
            elif self.action in {"search", "us_etf_search"}:
                chart_service = (
                    self.service.us_etf if self.action == "us_etf_search"
                    else self.service.equity
                )
                result = chart_service.search(str(self.request))
            elif self.action in {"equity", "us_etf"}:
                identity, period = self.request
                chart_service = (
                    self.service.us_etf if self.action == "us_etf"
                    else self.service.equity
                )
                result = chart_service.series(identity, period)
            elif self.action == "comparison":
                result = self.service.benchmark_comparison(self.request)
            else:
                raise ValueError("unsupported detached chart action")
        except Exception as error:
            result = error
        self.completed.emit(self.action, self.request, result)


class DetachedChartWindow(QtWidgets.QMainWindow):
    """Independent chart state over the main window's shared read-only service."""

    closed = QtCore.Signal(object)

    def __init__(
        self,
        kind: str,
        service: DashboardService,
        source_page: IndexPage | IndividualEquityPage,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if kind not in {"index", "equity", "us_etf"}:
            raise ValueError("unsupported detached chart kind")
        self.kind = kind
        self.service = service
        self._closing = False
        self._close_pending = False
        self._thread: QtCore.QThread | None = None
        self._worker: DetachedChartWorker | None = None
        self._pending: tuple[str, object] | None = None
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setMinimumSize(900, 620)
        self.resize(1280, 820)

        if kind == "index":
            if not isinstance(source_page, IndexPage) or isinstance(source_page, IndividualEquityPage):
                raise TypeError("index detachment requires IndexPage")
            page: IndexPage | IndividualEquityPage = IndexPage()
            page.request_series.connect(self._request_index)
            self._copy_index_state(source_page, page)
        else:
            if not isinstance(source_page, IndividualEquityPage):
                raise TypeError("equity detachment requires IndividualEquityPage")
            page = IndividualEquityPage(universe=source_page.universe)
            page.search_requested.connect(self._request_search)
            page.series_requested.connect(self._request_equity)
            page.comparison_requested.connect(self._request_comparison)
            self._copy_equity_state(source_page, page)
        page.detach_button.hide()
        self.page = page
        self.setCentralWidget(page)
        self._update_title()

    @staticmethod
    def _set_controls(
        source: IndexPage, target: IndexPage,
    ) -> None:
        widgets = (target.index, target.period, target.rsi, target.disparity)
        blockers = [QtCore.QSignalBlocker(widget) for widget in widgets]
        target.index.setCurrentText(source.index.currentText())
        target.period.setCurrentText(source.period.currentText())
        target.rsi.setCurrentText(source.rsi.currentText())
        target.disparity.setCurrentText(source.disparity.currentText())
        del blockers
        target.indicator_panel.apply(source.indicator_panel.settings())

    @staticmethod
    def _copy_ranges_and_crosshair(source: IndexPage, target: IndexPage) -> None:
        target._manual_view = source._manual_view
        if source._manual_view:
            target._restore_manual_ranges((
                source.plot.getViewBox().viewRange(),
                source.volume.getViewBox().viewRange(),
            ))
        for source_line, target_line in zip(source.crosshairs, target.crosshairs):
            target_line.setPos(source_line.pos())
            target_line.setVisible(source_line.isVisible())
        target.hover.setText(source.hover.text())
        target._measurement_points = list(source._measurement_points)
        target.measurement.setText(source.measurement.text())

    @classmethod
    def _copy_index_state(cls, source: IndexPage, target: IndexPage) -> None:
        cls._set_controls(source, target)
        target._candlestick_mode = source._candlestick_mode
        target.render(
            replace(source._index_view, frame=source._index_view.frame.copy())
            if source._index_view is not None else source._frame.copy()
        )
        target.title.setText(source.title.text())
        cls._copy_ranges_and_crosshair(source, target)

    @classmethod
    def _copy_equity_state(
        cls, source: IndividualEquityPage, target: IndividualEquityPage,
    ) -> None:
        cls._set_controls(source, target)
        timeframe_blocker = QtCore.QSignalBlocker(target.timeframe)
        target.timeframe.setCurrentText(source.timeframe.currentText())
        del timeframe_blocker
        target.search_input.setText(source.search_input.text())
        target.search_results.clear()
        for index in range(source.search_results.count()):
            target.search_results.addItem(
                source.search_results.itemText(index),
                source.search_results.itemData(index),
            )
        target.search_results.setCurrentIndex(source.search_results.currentIndex())
        target.search_feedback.setText(source.search_feedback.text())
        target.search_feedback.setVisible(not source.search_feedback.isHidden())
        if source._watchlist_state is not None:
            target.set_watchlists(source._watchlist_state)
            target.favorite_target.setCurrentIndex(
                max(0, target.favorite_target.findData(source.favorite_target.currentData()))
            )
        target._selected_identity = source._selected_identity
        if source._series_view is not None:
            cloned = replace(source._series_view, frame=source._series_view.frame.copy())
            target._series_view = cloned
            target.render_series(cloned)
            if source.comparison_toggle.isChecked():
                blocker = QtCore.QSignalBlocker(target.comparison_toggle)
                target.comparison_toggle.setChecked(True)
                del blocker
                if source._comparison_view is not None:
                    target.render_comparison(replace(
                        source._comparison_view,
                        frame=source._comparison_view.frame.copy(),
                    ))
        else:
            IndexPage.render(target, source._frame.copy())
            target.summary.setText(source.summary.text())
            target._set_chart_workspace_visible(
                source._selected_identity is not None
            )
        target.status.setText(source.status.text())
        target.status.setToolTip(source.status.toolTip())
        target.reload_button.setEnabled(source.reload_button.isEnabled())
        cls._copy_ranges_and_crosshair(source, target)

    def _request_index(self, index: str, period: str) -> None:
        self._start_job("index", (index, period))

    def _request_search(self, query: str) -> None:
        self._start_job("us_etf_search" if self.kind == "us_etf" else "search", query)

    def _request_equity(self, identity: EquityIdentity, period: str) -> None:
        self._start_job("us_etf" if self.kind == "us_etf" else "equity", (identity, period))

    def _request_comparison(self, view: EquitySeriesView) -> None:
        self._start_job("comparison", view)

    def _start_job(self, action: str, request: object) -> None:
        if self._closing:
            return
        if self._thread is not None and self._thread.isRunning():
            self._pending = (action, request)
            return
        thread = QtCore.QThread(self)
        worker = DetachedChartWorker(self.service, action, request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._loaded)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(
            self._thread_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        thread.destroyed.connect(self._thread_destroyed)
        self._thread = thread
        self._worker = worker
        thread.start()

    @QtCore.Slot(str, object, object)
    def _loaded(self, action: str, request: object, result: object) -> None:
        if self._closing:
            return
        if action == "index":
            index, period = request
            if (
                self.kind != "index"
                or self.page.index.currentText() != index
                or self.page.period.currentText() != period
            ):
                return
            if isinstance(result, IndexSeriesView):
                view = result
            else:
                exact = (
                    "KRX:KOSPI200 · 업종코드 1028" if index == "KOSPI200"
                    else f"KRX:{index}"
                )
                view = IndexSeriesView.unavailable(
                    index, index, exact, period,
                    "kr_kospi200_index_daily" if index == "KOSPI200" else "kr_index_daily",
                    "지수 로컬 시계열을 읽거나 검증할 수 없습니다.",
                )
            self.page.render(view)
        elif action in {"search", "us_etf_search"} and self.kind in {"equity", "us_etf"}:
            if (action == "us_etf_search") != (self.kind == "us_etf"):
                return
            view = (
                result if isinstance(result, EquitySearchView)
                else EquitySearchView(str(request), (), "종목 식별정보를 읽거나 검증할 수 없습니다.")
            )
            self.page.render_search(view)
        elif action in {"equity", "us_etf"} and self.kind in {"equity", "us_etf"}:
            if action != self.kind:
                return
            identity, period = request
            if (
                self.page._selected_identity is None
                or self.page._selected_identity.key != identity.key
                or self.page.period.currentText() != period
            ):
                return
            view = (
                result if isinstance(result, EquitySeriesView)
                else EquitySeriesView(
                    identity=identity, period=period, frame=pd.DataFrame(),
                    display_state=DashboardDisplayState.UNAVAILABLE,
                    freshness="UNKNOWN", as_of=None, expected_as_of=None,
                    source="local retained data", reference_kst=None,
                    unavailable_reason="선택한 종목의 로컬 가격을 읽거나 검증할 수 없습니다.",
                )
            )
            self.page.render_series(view)
        elif action == "comparison" and self.kind in {"equity", "us_etf"}:
            if isinstance(result, NormalizedBenchmarkComparisonView):
                self.page.render_comparison(result)
        self._update_title()

    @QtCore.Slot()
    def _thread_finished(self) -> None:
        thread = self.sender()
        if not isinstance(thread, QtCore.QThread) or self._thread is not thread:
            return
        if thread.isRunning():
            return
        thread.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(
            thread, QtCore.QEvent.DeferredDelete,
        )

    @QtCore.Slot(object)
    def _thread_destroyed(self, _destroyed: object) -> None:
        self._thread = None
        self._worker = None
        pending = self._pending
        self._pending = None
        if self._close_pending:
            QtCore.QTimer.singleShot(0, self.close)
        elif pending is not None and not self._closing:
            QtCore.QTimer.singleShot(0, lambda pending=pending: self._start_job(*pending))

    def _update_title(self) -> None:
        if self.kind == "index":
            reference = (
                f"{self.page._session_mapping.dates[-1].date().isoformat()} KST 일봉"
                if self.page._session_mapping and self.page._session_mapping.dates
                else "KST 일봉 · 기준일 확인 필요"
            )
            self.setWindowTitle(
                f"Stock Investment · {self.page.index.currentText()} · "
                f"{self.page.period.currentText()} · {reference}"
            )
            return
        identity = self.page._selected_identity
        if identity is None:
            self.setWindowTitle("Stock Investment · 종목 검색 · KST")
            return
        reference = (
            self.page._series_view.reference_kst
            if self.page._series_view is not None and self.page._series_view.reference_kst
            else "KST 일봉 · 기준일 확인 필요"
        )
        self.setWindowTitle(
            f"Stock Investment · {identity.name} · {identity.symbol} · "
            f"{identity.market} · {reference}"
        )

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        thread = self._thread
        if thread is not None:
            self._closing = True
            self._close_pending = True
            self._pending = None
            if thread.isRunning():
                thread.requestInterruption()
                thread.quit()
            event.ignore()
            return
        self._closing = True
        self._close_pending = False
        self.closed.emit(self)
        super().closeEvent(event)


class GlobalSymbolSwitcher(QtWidgets.QDialog):
    """Keyboard-first selector over the two accepted local identity catalogs."""

    search_requested = QtCore.Signal(str)
    identity_selected = QtCore.Signal(object)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("로컬 종목 전환")
        self.setModal(False)
        self.resize(620, 320)
        layout = QtWidgets.QVBoxLayout(self)
        self.query = QtWidgets.QLineEdit()
        self.query.setPlaceholderText("회사명·6자리 종목코드·미국 ETF 티커")
        self.query.setAccessibleName("전역 로컬 종목 검색")
        self.results = QtWidgets.QListWidget()
        self.results.setAccessibleName("시장과 통화를 포함한 전역 종목 검색 결과")
        self.status = QtWidgets.QLabel("승인된 로컬 식별정보만 검색합니다.")
        self.status.setWordWrap(True)
        layout.addWidget(self.query)
        layout.addWidget(self.results)
        layout.addWidget(self.status)
        self.query.returnPressed.connect(self._request)
        self.results.itemActivated.connect(lambda _item: self._choose())

    def open_and_focus(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self.query.setFocus(QtCore.Qt.ShortcutFocusReason)
        self.query.selectAll()
        # Window activation is asynchronous on Qt's offscreen and native
        # backends. Reassert focus in the next GUI turn so Ctrl+K remains
        # keyboard-first regardless of the page that owned focus previously.
        QtCore.QTimer.singleShot(
            0, lambda: self.query.setFocus(QtCore.Qt.ShortcutFocusReason)
        )

    def _request(self) -> None:
        query = self.query.text().strip()
        self.results.clear()
        self.status.setText("로컬 식별정보 검색 중…")
        self.search_requested.emit(query)

    def render(self, view: EquitySearchView) -> None:
        if view.query != self.query.text().strip():
            return
        self.results.clear()
        for identity in view.matches:
            currency = identity.currency or ("KRW" if identity.market in {"KOSPI", "KOSDAQ"} else "-")
            self.results.addItem(
                f"{identity.name} · {identity.symbol} · {identity.market} · "
                f"{identity.security_type} · {currency}"
            )
            self.results.item(self.results.count() - 1).setData(QtCore.Qt.UserRole, identity)
        if view.unavailable_reason:
            self.status.setText(view.unavailable_reason)
        elif not view.matches:
            self.status.setText("일치하는 로컬 식별정보가 없습니다.")
        elif len(view.matches) > 1:
            self.status.setText("여러 결과가 있습니다. 시장·종목코드를 확인해 명시적으로 선택하세요.")
        else:
            self.status.setText("Enter 또는 더블클릭으로 엽니다. 가격은 선택 뒤 기존 로컬 읽기로만 요청됩니다.")
        if view.matches:
            self.results.setCurrentRow(0)
            self.results.setFocus()

    def _choose(self) -> None:
        item = self.results.currentItem()
        identity = item.data(QtCore.Qt.UserRole) if item is not None else None
        if isinstance(identity, EquityIdentity):
            self.identity_selected.emit(identity)
            self.hide()


class ResearchWorkspacePage(QtWidgets.QWidget):
    """One-screen, read-only composition of an exact typed equity view."""

    identity_requested = QtCore.Signal(object)
    candidate_scan_requested = QtCore.Signal()
    candidate_symbol_requested = QtCore.Signal(str, str)

    _PANEL_LABELS = {
        "CHART": "차트",
        "OHLCV": "표시 OHLCV",
        "INSTRUMENT_FACTS": "종목 정보",
        "WATCHLIST": "선택 관심종목",
        "SOURCE_STATUS": "출처·상태",
    }

    def __init__(
        self,
        preferences_store: LocalResearchWorkspacePreferencesStore,
        preferences: ResearchWorkspacePreferences,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.preferences_store = preferences_store
        self._preferences = preferences
        self._selected_identity: EquityIdentity | None = None
        self._series_view: EquitySeriesView | None = None
        self._watchlist_state = WatchlistState(())
        self._panel_order = list(RESEARCH_WORKSPACE_PANEL_IDS)
        self._logical_sizes = {
            panel_id: 240 for panel_id in RESEARCH_WORKSPACE_PANEL_IDS
        }
        self.setAccessibleName("읽기 전용 Research Workspace")
        self.setMinimumSize(900, 500)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        title = QtWidgets.QLabel("Research Workspace · 로컬 읽기 전용")
        title.setObjectName("title")
        root.addWidget(title)

        toolbar = QtWidgets.QHBoxLayout()
        self.preset_selector = QtWidgets.QComboBox()
        self.preset_selector.setAccessibleName("Research Workspace preset")
        self.preset_name = QtWidgets.QLineEdit()
        self.preset_name.setMaxLength(40)
        self.preset_name.setPlaceholderText("프리셋 이름")
        self.preset_name.setAccessibleName("저장할 프리셋 이름")
        self.save_preset_button = QtWidgets.QPushButton("프리셋 저장")
        self.reset_preset_button = QtWidgets.QPushButton("기본값 복원")
        self.panel_selector = QtWidgets.QComboBox()
        self.panel_selector.setAccessibleName("구성할 workspace panel")
        self.panel_visible = QtWidgets.QCheckBox("표시")
        self.panel_size = QtWidgets.QSpinBox()
        self.panel_size.setRange(120, 4096)
        self.panel_size.setSuffix(" px")
        self.panel_up = QtWidgets.QPushButton("왼쪽")
        self.panel_down = QtWidgets.QPushButton("오른쪽")
        for widget in (
            self.preset_selector, self.preset_name, self.save_preset_button,
            self.reset_preset_button, self.panel_selector, self.panel_visible,
            self.panel_size, self.panel_up, self.panel_down,
        ):
            toolbar.addWidget(widget)
        root.addLayout(toolbar)

        self.summary = QtWidgets.QLabel(
            "Ctrl+K로 정확한 국내 종목 또는 승인된 미국 ETF를 선택하세요."
        )
        self.summary.setObjectName("sectionTitle")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        candidate_panel = QtWidgets.QGroupBox("종목 후보 발굴 · 연구용")
        candidate_panel.setObjectName("stockCandidateDiscovery")
        candidate_panel.setAccessibleName("P4 종목 후보 발굴 연구 화면")
        candidate_layout = QtWidgets.QVBoxLayout(candidate_panel)
        candidate_layout.setContentsMargins(8, 6, 8, 6)
        candidate_layout.setSpacing(4)
        candidate_toolbar = QtWidgets.QHBoxLayout()
        candidate_toolbar.addStretch()
        self.candidate_scan_button = QtWidgets.QPushButton("현재 후보 새로고침")
        self.candidate_scan_button.setAccessibleName("로컬 현재 후보 새로고침")
        candidate_toolbar.addWidget(self.candidate_scan_button)
        self.candidate_axis_status = QtWidgets.QLabel()
        self.candidate_axis_status.setObjectName("sectionTitle")
        self.candidate_axis_status.setWordWrap(True)
        self.candidate_status = QtWidgets.QLabel()
        self.candidate_status.setWordWrap(True)
        self.candidate_table = QtWidgets.QTableWidget(0, 6)
        self.candidate_table.setHorizontalHeaderLabels((
            "종목", "과매도", "실적", "현재 PER/PBR", "재무·가치함정", "기준시각",
        ))
        self.candidate_table.setAccessibleName("현재 데이터 종목 관찰 후보")
        self.candidate_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.candidate_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.candidate_table.verticalHeader().setVisible(False)
        self.candidate_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Stretch
        )
        self.candidate_table.setMaximumHeight(128)
        candidate_layout.addLayout(candidate_toolbar)
        candidate_layout.addWidget(self.candidate_axis_status)
        candidate_layout.addWidget(self.candidate_status)
        candidate_layout.addWidget(self.candidate_table)
        root.addWidget(candidate_panel)
        self.begin_candidate_scan()

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.splitterMoved.connect(self._capture_splitter_sizes)
        root.addWidget(self.splitter, 1)
        self.panels: dict[str, QtWidgets.QGroupBox] = {}

        chart_panel = self._new_panel("CHART")
        chart_layout = QtWidgets.QVBoxLayout(chart_panel)
        self.chart = pg.PlotWidget()
        self.chart.setAccessibleName("선택 종목 종가 차트")
        self.chart.showGrid(x=True, y=True, alpha=.2)
        self.chart.setLabel("left", "가격")
        self.chart.setLabel("bottom", "표시 관측 순서")
        chart_layout.addWidget(self.chart)

        table_panel = self._new_panel("OHLCV")
        table_layout = QtWidgets.QVBoxLayout(table_panel)
        self.ohlcv_table = QtWidgets.QTableWidget(0, 6)
        self.ohlcv_table.setHorizontalHeaderLabels(
            ("date", "open", "high", "low", "close", "volume")
        )
        self.ohlcv_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.ohlcv_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.ohlcv_table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        table_layout.addWidget(self.ohlcv_table)

        facts_panel = self._new_panel("INSTRUMENT_FACTS")
        facts_layout = QtWidgets.QVBoxLayout(facts_panel)
        self.instrument_facts = QtWidgets.QLabel("정확한 종목을 선택하세요.")
        self.instrument_facts.setWordWrap(True)
        self.instrument_facts.setAlignment(QtCore.Qt.AlignTop)
        facts_layout.addWidget(self.instrument_facts)
        facts_layout.addStretch()

        watchlist_panel = self._new_panel("WATCHLIST")
        watchlist_layout = QtWidgets.QVBoxLayout(watchlist_panel)
        self.watchlist_selector = QtWidgets.QComboBox()
        self.watchlist_items = QtWidgets.QListWidget()
        self.watchlist_selector.setAccessibleName("workspace 관심종목 목록")
        self.watchlist_items.setAccessibleName("workspace exact 관심종목")
        watchlist_layout.addWidget(self.watchlist_selector)
        watchlist_layout.addWidget(self.watchlist_items)

        source_panel = self._new_panel("SOURCE_STATUS")
        source_layout = QtWidgets.QVBoxLayout(source_panel)
        self.source_status = QtWidgets.QLabel(
            "가격과 출처 상태는 exact typed view가 수락된 뒤 표시됩니다."
        )
        self.source_status.setWordWrap(True)
        self.source_status.setAlignment(QtCore.Qt.AlignTop)
        source_layout.addWidget(self.source_status)
        source_layout.addStretch()

        self.preset_selector.currentIndexChanged.connect(self._preset_selected)
        self.save_preset_button.clicked.connect(self.save_named_preset)
        self.reset_preset_button.clicked.connect(self.reset_preferences)
        self.panel_selector.currentIndexChanged.connect(self._sync_panel_controls)
        self.panel_visible.toggled.connect(self._set_selected_panel_visible)
        self.panel_size.valueChanged.connect(self._set_selected_panel_size)
        self.panel_up.clicked.connect(lambda: self._move_selected_panel(-1))
        self.panel_down.clicked.connect(lambda: self._move_selected_panel(1))
        self.watchlist_selector.currentIndexChanged.connect(self._render_watchlist)
        self.watchlist_items.itemActivated.connect(self._request_watchlist_identity)
        self.candidate_scan_button.clicked.connect(self.candidate_scan_requested.emit)
        self.candidate_table.itemActivated.connect(self._request_candidate_symbol)
        self._reload_preset_selector()
        self.apply_preset(self._preferences.active_preset)

    def _new_panel(self, panel_id: str) -> QtWidgets.QGroupBox:
        panel = QtWidgets.QGroupBox(self._PANEL_LABELS[panel_id])
        panel.setObjectName(f"researchWorkspace{panel_id.title().replace('_', '')}")
        panel.setAccessibleName(f"Research Workspace {self._PANEL_LABELS[panel_id]}")
        panel.setMinimumWidth(120)
        panel.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.panels[panel_id] = panel
        self.splitter.addWidget(panel)
        return panel

    @property
    def panel_order(self) -> tuple[str, ...]:
        return tuple(self._panel_order)

    @property
    def panel_visibility(self) -> dict[str, bool]:
        return {panel_id: not self.panels[panel_id].isHidden() for panel_id in self._panel_order}

    @property
    def logical_sizes(self) -> dict[str, int]:
        return dict(self._logical_sizes)

    def begin_identity(self, identity: EquityIdentity) -> None:
        if not isinstance(identity, EquityIdentity):
            return
        self._selected_identity = identity
        self._series_view = None
        self.summary.setText(f"{identity.display_label} · 로컬 시계열 읽는 중…")
        self._clear_numeric_surfaces()
        self.instrument_facts.setText(identity.display_label)
        self.source_status.setText("이전 숫자 제거 완료 · exact local typed view 확인 중")

    def render_series(self, view: EquitySeriesView) -> None:
        if (
            self._selected_identity is None
            or view.identity != self._selected_identity
        ):
            return
        self._series_view = view
        displays_values = view.displays_values and "STALE" not in view.freshness.upper()
        try:
            facts = instrument_facts_view(view)
        except (TypeError, ValueError):
            self._clear_numeric_surfaces()
            self.instrument_facts.setText(view.identity.display_label)
            self.source_status.setText("종목 정보 계약 검증 실패 · 숫자 숨김")
            self.summary.setText(f"{view.identity.display_label} · 숫자 숨김")
            return
        self.instrument_facts.setText("\n".join((
            facts.identity_line, facts.market_line, facts.source_line,
            facts.risk_line, facts.unsupported_line,
        )))
        self.source_status.setText(
            f"source={view.source}\nfreshness={view.freshness}\n"
            f"as_of={view.as_of or 'N/A'}\nreference={view.reference_kst or 'N/A'}\n"
            f"state={view.display_state.value}"
            + (f"\nreason={view.unavailable_reason}" if view.unavailable_reason else "")
        )
        if not displays_values:
            self._clear_numeric_surfaces()
            self.summary.setText(
                f"{view.identity.display_label} · 숫자 숨김 · "
                f"{view.unavailable_reason or view.freshness}"
            )
            return
        self.summary.setText(
            f"{view.identity.display_label} · {view.price_mode} · {view.as_of or 'N/A'}"
        )
        frame = view.frame.reset_index(drop=True)
        close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
        self.chart.clear()
        self.chart.plot(np.arange(len(close), dtype=float), close, pen=pg.mkPen("#60a5fa", width=2))
        self._render_ohlcv(frame)

    def _clear_numeric_surfaces(self) -> None:
        self.chart.clear()
        self.ohlcv_table.clearContents()
        self.ohlcv_table.setRowCount(0)

    def render_candidate_discovery(self, view: StockCandidateDiscoveryView) -> None:
        """Render only an accepted typed candidate view; never derive or rank."""
        self.candidate_table.clearContents()
        self.candidate_table.setRowCount(0)
        try:
            view = validate_candidate_discovery_view(view)
        except (TypeError, ValueError):
            self.candidate_axis_status.setText(
                "평가 가능 0/3 · 과매도 N/A · Forward EPS 수정 N/A · 상대 가치 N/A"
            )
            self.candidate_status.setText("결론 보류 · 후보 계약 검증 실패 · 종목과 숫자 숨김")
            return
        if view.availability != "COMPLETE":
            reasons = ", ".join(view.unavailable_reasons) or "INPUT_UNAVAILABLE"
            self.candidate_axis_status.setText(
                "평가 가능 0/3 · 과매도 N/A · Forward EPS 수정 N/A · 상대 가치 N/A"
            )
            self.candidate_status.setText(
                "결론 보류 · PIT 검증 입력이 아직 없어 후보를 표시하지 않습니다. "
                f"({reasons}) · 추천/순위 아님"
            )
            return
        self.candidate_axis_status.setText(
            "과매도 ✓ · 실적 상향 ✓ · 상대 저평가 ✓ · 재무건전성/가치함정 별도 확인"
        )
        self.candidate_status.setText(
            f"검증 대상 {view.evaluated_instruments}개 · 연구 후보 "
            f"{len(view.candidates)}개 · 점수/순위/매매 추천 없음"
        )
        self.candidate_table.setRowCount(len(view.candidates))
        for row, candidate in enumerate(view.candidates):
            values = (
                f"{candidate.name} · {candidate.symbol} · {candidate.market}",
                "과매도",
                "실적 상향",
                "상대 저평가",
                "상대 가치 정의에 포함",
                candidate.decision_at,
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, value)
                self.candidate_table.setItem(row, column, item)

    def begin_candidate_scan(self) -> None:
        self.candidate_scan_button.setEnabled(False)
        self.candidate_table.clearContents()
        self.candidate_table.setRowCount(0)
        self.candidate_axis_status.setText(
            "기술 축 불러오는 중… · 실적 축 N/A · 현재 PER/PBR 확인 중…"
        )
        self.candidate_status.setText(
            "현재 로컬 종목 일봉을 읽는 중입니다 · 화면은 계속 사용할 수 있습니다."
        )

    def render_exploratory_candidates(self, view: ExploratoryCandidateView) -> None:
        """Render partial current-data axes without claiming PIT validation."""
        self.candidate_scan_button.setEnabled(True)
        self.candidate_table.clearContents()
        self.candidate_table.setRowCount(0)
        try:
            view = validate_exploratory_candidate_view(view)
        except (AttributeError, TypeError, ValueError):
            self.candidate_axis_status.setText("기술 축 N/A · 실적 축 N/A · 가치 축 N/A")
            self.candidate_status.setText("로컬 후보 결과 검증 실패 · 행 숨김")
            return
        if view.availability != "READY":
            self.candidate_axis_status.setText("기술 축 N/A · 실적 축 N/A · 가치 축 N/A")
            self.candidate_status.setText(
                f"현재 후보를 읽지 못했습니다 ({view.unavailable_reason or 'UNKNOWN'})"
            )
            return
        valuation_rows = sum(
            candidate.valuation_state == "AVAILABLE_CURRENT_TRAILING"
            for candidate in view.candidates
        )
        self.candidate_axis_status.setText(
            "기술 축 사용 가능 · 실적 상향 N/A · "
            + (
                f"KRX 현재 PER/PBR {valuation_rows}/{len(view.candidates)} 표시 · "
                "상대저평가 판정 N/A"
                if valuation_rows else "현재 PER/PBR N/A"
            )
        )
        self.candidate_status.setText(
            f"{view.as_of} · {view.scanned_instruments:,}종목 스캔 · "
            f"관찰 후보 {view.eligible_instruments:,}개 중 {len(view.candidates)}개 표시 · "
            "부분 축 허용 · 설명용 정렬, 매매 추천 아님"
        )
        self.candidate_status.setToolTip(
            f"criteria={view.criteria}\nsource={view.source_note}\n"
            f"ranking={view.ranking_basis}"
        )
        self.candidate_table.setRowCount(len(view.candidates))
        for row, candidate in enumerate(view.candidates):
            identity = (
                f"{candidate.name} · {candidate.symbol} · {candidate.market}"
                if candidate.name else f"{candidate.symbol} · {candidate.market}"
            )
            ratios = " · ".join((
                *(f"PER {candidate.per:.2f}" for _ in (0,) if candidate.per is not None),
                *(f"PBR {candidate.pbr:.2f}" for _ in (0,) if candidate.pbr is not None),
            )) or "N/A · 미연결"
            values = (
                identity,
                f"RSI {candidate.rsi14:.1f} · {candidate.technical_state}",
                "N/A · 미연결",
                ratios,
                candidate.data_caution or "별도 확인",
                candidate.as_of,
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, value)
                item.setToolTip(
                    f"close={candidate.close:,.0f} KRW · volume={candidate.volume:,} · "
                    f"RSI14={candidate.rsi14:.2f} · close/SMA60={candidate.disparity60:.2f}%"
                )
                if column == 0:
                    item.setData(QtCore.Qt.UserRole + 1, (candidate.market, candidate.symbol))
                self.candidate_table.setItem(row, column, item)

    def _request_candidate_symbol(self, item: QtWidgets.QTableWidgetItem) -> None:
        first = self.candidate_table.item(item.row(), 0)
        identity = first.data(QtCore.Qt.UserRole + 1) if first is not None else None
        if (
            isinstance(identity, tuple)
            and len(identity) == 2
            and all(isinstance(value, str) and value for value in identity)
        ):
            self.candidate_symbol_requested.emit(identity[0], identity[1])

    def _render_ohlcv(self, frame: pd.DataFrame) -> None:
        columns = ("date", "open", "high", "low", "close", "volume")
        self.ohlcv_table.setRowCount(len(frame))
        for row_index, (_, row) in enumerate(frame.iterrows()):
            for column_index, column in enumerate(columns):
                value = row.get(column)
                if column == "date" and pd.notna(value):
                    exact = pd.Timestamp(value).date().isoformat()
                elif pd.isna(value):
                    exact = None
                elif isinstance(value, np.generic):
                    exact = value.item()
                else:
                    exact = value
                item = QtWidgets.QTableWidgetItem("N/A" if exact is None else str(exact))
                item.setData(QtCore.Qt.UserRole, exact)
                self.ohlcv_table.setItem(row_index, column_index, item)

    def displayed_ohlcv(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            tuple(
                self.ohlcv_table.item(row, column).data(QtCore.Qt.UserRole)
                for column in range(self.ohlcv_table.columnCount())
            )
            for row in range(self.ohlcv_table.rowCount())
        )

    def set_watchlists(self, state: WatchlistState) -> None:
        self._watchlist_state = state
        selected = self.watchlist_selector.currentData()
        self.watchlist_selector.blockSignals(True)
        self.watchlist_selector.clear()
        for watchlist in state.lists:
            self.watchlist_selector.addItem(watchlist.name, watchlist.list_id)
        position = self.watchlist_selector.findData(selected)
        self.watchlist_selector.setCurrentIndex(position if position >= 0 else 0)
        self.watchlist_selector.blockSignals(False)
        self._render_watchlist()

    def _render_watchlist(self, _index: int = -1) -> None:
        self.watchlist_items.clear()
        list_id = self.watchlist_selector.currentData()
        try:
            watchlist = self._watchlist_state.list_by_id(list_id)
        except (StopIteration, ValueError):
            return
        for entry in watchlist.items:
            item = QtWidgets.QListWidgetItem(entry.identity.display_label)
            item.setData(QtCore.Qt.UserRole, entry.identity)
            self.watchlist_items.addItem(item)

    def _request_watchlist_identity(self, item: QtWidgets.QListWidgetItem) -> None:
        identity = item.data(QtCore.Qt.UserRole)
        if isinstance(identity, EquityIdentity):
            self.identity_requested.emit(identity)

    def _reload_preset_selector(self) -> None:
        self.preset_selector.blockSignals(True)
        self.preset_selector.clear()
        for preset in self._preferences.presets:
            self.preset_selector.addItem(preset.name, preset.name)
        index = self.preset_selector.findData(self._preferences.active_preset)
        self.preset_selector.setCurrentIndex(max(index, 0))
        self.preset_selector.blockSignals(False)

    def apply_preset(self, name: str) -> None:
        try:
            preset = next(item for item in self._preferences.presets if item.name == name)
        except StopIteration:
            preset = DEFAULT_RESEARCH_WORKSPACE_PREFERENCES.presets[0]
        ordered = list(preset.panels)
        self._panel_order = [panel.panel_id for panel in ordered]
        self._logical_sizes = {panel.panel_id: panel.logical_size for panel in ordered}
        for index, setting in enumerate(ordered):
            self.splitter.insertWidget(index, self.panels[setting.panel_id])
            self.panels[setting.panel_id].setVisible(setting.visible)
        self.splitter.setSizes([self._logical_sizes[item] for item in self._panel_order])
        focus_panels = [
            self.panels[item.panel_id]
            for item in sorted(ordered, key=lambda panel: panel.focus_order)
            if item.visible
        ]
        for left, right in zip(focus_panels, focus_panels[1:]):
            QtWidgets.QWidget.setTabOrder(left, right)
        self.panel_selector.blockSignals(True)
        self.panel_selector.clear()
        for panel_id in self._panel_order:
            self.panel_selector.addItem(self._PANEL_LABELS[panel_id], panel_id)
        self.panel_selector.blockSignals(False)
        self._sync_panel_controls()

    def _preset_selected(self, _index: int) -> None:
        name = self.preset_selector.currentData()
        if isinstance(name, str):
            self.apply_preset(name)

    def _sync_panel_controls(self, _index: int = -1) -> None:
        panel_id = self.panel_selector.currentData()
        if panel_id not in self.panels:
            return
        self.panel_visible.blockSignals(True)
        self.panel_size.blockSignals(True)
        self.panel_visible.setChecked(not self.panels[panel_id].isHidden())
        self.panel_size.setValue(self._logical_sizes[panel_id])
        self.panel_visible.blockSignals(False)
        self.panel_size.blockSignals(False)

    def _set_selected_panel_visible(self, visible: bool) -> None:
        panel_id = self.panel_selector.currentData()
        if panel_id in self.panels:
            self.panels[panel_id].setVisible(visible)

    def _set_selected_panel_size(self, size: int) -> None:
        panel_id = self.panel_selector.currentData()
        if panel_id not in self.panels:
            return
        self._logical_sizes[panel_id] = size
        self.splitter.setSizes([self._logical_sizes[item] for item in self._panel_order])

    def _capture_splitter_sizes(self, _position: int = 0, _index: int = 0) -> None:
        for panel_id, size in zip(self._panel_order, self.splitter.sizes()):
            if not self.panels[panel_id].isHidden() and size >= 120:
                self._logical_sizes[panel_id] = min(size, 4096)

    def _move_selected_panel(self, offset: int) -> None:
        panel_id = self.panel_selector.currentData()
        if panel_id not in self._panel_order:
            return
        current = self._panel_order.index(panel_id)
        target = current + offset
        if target < 0 or target >= len(self._panel_order):
            return
        self._panel_order[current], self._panel_order[target] = (
            self._panel_order[target], self._panel_order[current],
        )
        for index, current_id in enumerate(self._panel_order):
            self.splitter.insertWidget(index, self.panels[current_id])
        self.panel_selector.blockSignals(True)
        self.panel_selector.clear()
        for current_id in self._panel_order:
            self.panel_selector.addItem(self._PANEL_LABELS[current_id], current_id)
        self.panel_selector.setCurrentIndex(target)
        self.panel_selector.blockSignals(False)
        self._sync_panel_controls()

    def _current_preset(self, name: str) -> WorkspacePreset:
        self._capture_splitter_sizes()
        return WorkspacePreset(
            name=name,
            panels=tuple(
                ResearchPanelPreference(
                    panel_id=panel_id,
                    visible=not self.panels[panel_id].isHidden(),
                    logical_size=self._logical_sizes[panel_id],
                    focus_order=index,
                )
                for index, panel_id in enumerate(self._panel_order)
            ),
        )

    def save_named_preset(self) -> None:
        name = self.preset_name.text().strip() or self.preset_selector.currentData()
        if not isinstance(name, str):
            return
        replacement = self._current_preset(name)
        presets = list(self._preferences.presets)
        existing = next((index for index, item in enumerate(presets) if item.name == name), None)
        if existing is None:
            if len(presets) >= MAX_RESEARCH_WORKSPACE_PRESETS:
                self.source_status.setText("프리셋 저장 실패 · 최대 개수 초과")
                return
            presets.append(replacement)
        else:
            presets[existing] = replacement
        candidate = ResearchWorkspacePreferences(name, tuple(presets))
        try:
            self.preferences_store.save(candidate)
        except ResearchWorkspacePreferencesError as error:
            self.source_status.setText(str(error))
            return
        self._preferences = candidate
        self._reload_preset_selector()
        self.apply_preset(name)

    def reset_preferences(self) -> None:
        try:
            self._preferences = self.preferences_store.reset()
        except ResearchWorkspacePreferencesError as error:
            self.source_status.setText(str(error))
            return
        self._reload_preset_selector()
        self.apply_preset(self._preferences.active_preset)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        project_root: Path,
        account_snapshot_path: Path | None = None,
        *,
        kb_account_snapshot_path: Path | None = None,
        family_account_snapshot_path: Path | None = None,
        account_refresher: Callable[[AccountRefreshTrigger], object] | None = None,
        kb_account_refresher: Callable[[AccountRefreshTrigger], object] | None = None,
        toss_runtime_enabled: bool | None = None,
        toss_runtime_reason: str | None = None,
        kb_runtime_enabled: bool | None = None,
        kb_runtime_reason: str | None = None,
        current_observation_runner: Callable[[], object] | None = None,
        current_observation_runner_factory: Callable[[], Callable[[], object] | None] | None = None,
        net_worth_history_root: Path | None = None,
        net_worth_dialog_factory: Callable[
            [NetWorthView | None, QtWidgets.QWidget], NetWorthSnapshotDialog
        ] | None = None,
        manual_account_store_path: Path | None = None,
        manual_account_dialog_factory: Callable[
            [ManualAccountRecord | None, QtWidgets.QWidget], ManualAccountDialog
        ] | None = None,
        dashboard_preferences_path: Path | None = None,
        research_workspace_preferences_path: Path | None = None,
        backtest_runner: Callable[[object], object] | None = None,
        backtest_output_root: Path | None = None,
        backtest_scenario_service: BacktestScenarioService | None = None,
        backtest_scenario_inputs: BacktestScenarioInputs | None = None,
        diagnostic_session_id: str | None = None,
        current_stage_service: DashboardService | None = None,
    ):
        super().__init__()
        self.project_root = project_root
        self._diagnostic_session_id = diagnostic_session_id or new_session_id()
        self._diagnostic_store = RuntimeDiagnosticStore(
            project_root / "artifacts/runtime_logs/application"
        )
        self.service = DashboardService(project_root)
        self.candidate_scanner = LocalExploratoryCandidateScanner(project_root)
        # Never share LocalParquetQuery's mutable caches across concurrent
        # lanes. The dedicated service is restricted to current_card_stage(),
        # whose implementation reads typed current JSON projections only.
        self.current_stage_service = current_stage_service or DashboardService(
            project_root
        )
        self.watchlist_service = LocalWatchlistService(
            project_root / "artifacts/local_user/watchlists.json"
        )
        self._watchlist_state = self.watchlist_service.load()
        backtest_service_options: dict[str, object] = {}
        if backtest_runner is not None:
            backtest_service_options["runner"] = backtest_runner
        if backtest_output_root is not None:
            backtest_service_options["output_root"] = backtest_output_root
        self.backtest_service = BacktestResultService(
            project_root,
            diagnostic_session_id=self._diagnostic_session_id,
            **backtest_service_options,
        )
        self.backtest_scenario_service = (
            backtest_scenario_service or BacktestScenarioService()
        )
        self.backtest_scenario_inputs = backtest_scenario_inputs
        self.health_artifact_service = DailyHealthArtifactService(project_root)
        self.net_worth_store = LocalNetWorthHistoryStore(
            net_worth_history_root
            or project_root / "data/local/net_worth_history"
        )
        self.manual_account_store = LocalManualAccountStore(
            manual_account_store_path
            or project_root / "artifacts/local_user/manual_accounts.json"
        )
        self._manual_account_dialog_factory = (
            manual_account_dialog_factory
            or (lambda baseline, parent: ManualAccountDialog(baseline, parent))
        )
        self._net_worth_dialog_factory = (
            net_worth_dialog_factory
            or (
                lambda baseline, parent: NetWorthSnapshotDialog(
                    baseline=baseline, parent=parent
                )
            )
        )
        self.dashboard_preferences_store = LocalDashboardPreferencesStore(
            dashboard_preferences_path
            or project_root / "artifacts/local_user/dashboard_preferences.json"
        )
        dashboard_preferences_result = self.dashboard_preferences_store.load()
        self._dashboard_preferences = dashboard_preferences_result.preferences
        self.research_workspace_preferences_store = (
            LocalResearchWorkspacePreferencesStore(
                research_workspace_preferences_path
                or project_root / "artifacts/local_user/research_workspace_preferences.json"
            )
        )
        research_preferences_result = self.research_workspace_preferences_store.load()
        snapshot_path = account_snapshot_path or (
            project_root / "data/normalized/toss_account_snapshot/latest.json"
        )
        self.account_snapshot_service = LocalAccountSnapshotService(snapshot_path)
        kb_path = kb_account_snapshot_path or (
            project_root / "data/local/account_snapshots/kb_self.json"
        )
        family_path = family_account_snapshot_path or (
            project_root / "data/local/account_snapshots/family_mirae_etf.json"
        )
        self.account_portfolio_service = LocalAccountPortfolioService((
            LocalAccountSourceSpec(
                "toss_self",
                "Toss Securities · 본인",
                snapshot_path,
                enabled=toss_runtime_enabled is not False,
                unavailable_reason=toss_runtime_reason or "RUNTIME_CONFIG_REQUIRED",
            ),
            LocalAccountSourceSpec(
                "kb_self",
                "KB Securities · 본인",
                kb_path,
                enabled=kb_runtime_enabled is not False,
                unavailable_reason=kb_runtime_reason or "RUNTIME_CONFIG_REQUIRED",
            ),
            LocalAccountSourceSpec(
                "family_mirae",
                "미래에셋 가족 명의 ETF · 로컬 수동",
                family_path,
            ),
        ),
            manual_store=self.manual_account_store,
            history_root=project_root / "data/local/account_value_history",
        )
        self.account_refresher = account_refresher
        self.kb_account_refresher = kb_account_refresher
        self.toss_runtime_enabled = toss_runtime_enabled is not False
        self.toss_runtime_reason = toss_runtime_reason or "RUNTIME_CONFIG_REQUIRED"
        self.kb_runtime_enabled = kb_runtime_enabled is not False
        self.kb_runtime_reason = kb_runtime_reason or "RUNTIME_CONFIG_REQUIRED"
        self._account_view = AccountSnapshotView(
            state=AccountSnapshotState.NOT_AVAILABLE,
            reason="계좌 snapshot을 읽는 중입니다",
        )
        self._account_portfolio = AccountPortfolioView(entries=(), user_fund_totals=())
        self._account_thread: QtCore.QThread | None = None
        self._account_worker: AccountSnapshotWorker | None = None
        self._account_pending_trigger: AccountRefreshTrigger | None = None
        self._manual_account_reload_pending = False
        self._backtest_thread: QtCore.QThread | None = None
        self._backtest_worker: BacktestRunWorker | None = None
        self._backtest_action: str | None = None
        self._accepted_backtest_bundle: object | None = None
        self._backtest_close_pending = False
        self._close_pending = False
        # The runner is supplied by the explicitly authorized operation entry
        # point.  The optional factory is reevaluated per due request so its
        # public activation manifest, rather than GUI state, controls whether
        # the supported collector is ever injected.
        self.current_observation_runner = current_observation_runner
        self.current_observation_runner_factory = current_observation_runner_factory
        self._current_observation_thread: QtCore.QThread | None = None
        self._current_observation_worker: CurrentObservationAcquisitionWorker | None = None
        self._current_observation_last_result: object | None = None
        self._local_read_thread: QtCore.QThread | None = None
        self._local_read_worker: LocalReadWorker | None = None
        self._local_read_active: tuple[str, int] | None = None
        self._local_read_pending: dict[str, tuple[int, int, object]] = {}
        self._local_read_generations = {
            "dashboard": 0,
            "index": 0,
            "market_chart": 0,
        }
        self._local_read_sequence = 0
        self._current_stage_thread: QtCore.QThread | None = None
        self._current_stage_worker: DashboardCurrentStageWorker | None = None
        self._current_stage_generation = 0
        self._current_stage_pending: int | None = None
        self._latest_current_stage: tuple[int, DashboardCurrentStageView] | None = None
        self._equity_thread: QtCore.QThread | None = None
        self._equity_worker: EquityChartWorker | None = None
        self._equity_pending: tuple[str, object] | None = None
        self._candidate_scan_pending = False
        self._us_etf_thread: QtCore.QThread | None = None
        self._us_etf_worker: EquityChartWorker | None = None
        self._us_etf_pending: tuple[str, object] | None = None
        self._global_symbol_origin: QtWidgets.QWidget | None = None
        self._candidate_scan_started = False
        self._detached_windows: set[DetachedChartWindow] = set()
        self._closing = False
        self.setWindowTitle("Stock Investment · Market Overview"); self.resize(1600,900); self.setMinimumSize(900,640)
        tabs = QtWidgets.QTabWidget()
        self.tabs = tabs
        self.dashboard = DashboardPage()
        self.index_page = IndexPage()
        self.equity_page = IndividualEquityPage()
        self.research_workspace_page = ResearchWorkspacePage(
            self.research_workspace_preferences_store,
            research_preferences_result.preferences,
        )
        self.watchlist_page = WatchlistPage()
        self.data_status_page = DataStatusPage()
        self.account_page = AccountPage()
        self.net_worth_page = NetWorthPage()
        self.account_workspace_page = QtWidgets.QWidget()
        self.account_workspace_page.setAccessibleName("계좌 및 순자산 작업공간")
        account_workspace_layout = QtWidgets.QVBoxLayout(self.account_workspace_page)
        account_workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.account_workspace_tabs = QtWidgets.QTabWidget()
        self.account_workspace_tabs.setAccessibleName("계좌 보유 및 순자산 보기")
        self.account_workspace_tabs.addTab(self.account_page, "계좌·보유")
        self.account_workspace_tabs.addTab(self.net_worth_page, "순자산·증감")
        account_workspace_layout.addWidget(self.account_workspace_tabs)
        self.backtest_page = BacktestPage()
        tabs.addTab(self.dashboard, "Dashboard")
        tabs.addTab(self.index_page, "Index Graph")
        tabs.addTab(self.equity_page, "종목 차트")
        tabs.addTab(self.watchlist_page, "관심종목")
        tabs.addTab(self.data_status_page, "Data Status")
        tabs.addTab(self.account_workspace_page, "계좌·순자산")
        tabs.addTab(self.backtest_page, "Backtest")
        self.setCentralWidget(tabs)
        self.account_page.configure_refresh_disclosure(
            self.account_refresher is not None or self.kb_account_refresher is not None
        )
        self.us_etf_page = IndividualEquityPage(universe="US_ETF")
        tabs.insertTab(tabs.indexOf(self.watchlist_page), self.us_etf_page, "미국 ETF")
        tabs.insertTab(
            tabs.indexOf(self.watchlist_page),
            self.research_workspace_page,
            "Research Workspace",
        )
        self.global_symbol_switcher = GlobalSymbolSwitcher(self)
        self.global_symbol_switcher.search_requested.connect(
            lambda query: self._request_equity_job("global_search", query)
        )
        self.global_symbol_switcher.identity_selected.connect(
            self._open_global_identity
        )
        self.global_symbol_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence("Ctrl+K"), self,
        )
        self.global_symbol_shortcut.setContext(QtCore.Qt.ApplicationShortcut)
        self.global_symbol_shortcut.activated.connect(
            self._open_global_symbol_switcher
        )
        self.index_page.request_series.connect(self.refresh_index)
        self.index_page.indicator_settings_changed.connect(
            lambda settings: self._save_chart_indicator_preferences("INDEX", settings)
        )
        self.index_page.detach_requested.connect(self.open_detached_index)
        self.equity_page.search_requested.connect(self.search_equities)
        self.equity_page.indicator_settings_changed.connect(
            lambda settings: self._save_chart_indicator_preferences("EQUITY", settings)
        )
        self.equity_page.series_requested.connect(self.refresh_equity)
        self.equity_page.comparison_requested.connect(self.refresh_equity_comparison)
        self.equity_page.detach_requested.connect(self.open_detached_equity)
        self.equity_page.favorite_toggled.connect(self._toggle_watchlist_item)
        self.equity_page.watchlist_item_moved.connect(self._move_watchlist_item)
        self.equity_page.context_identity_open_requested.connect(
            self._open_chart_identity
        )
        self.equity_page.context_watchlist_quotes_requested.connect(
            self.refresh_context_watchlist_quotes
        )
        self.us_etf_page.search_requested.connect(self.search_us_etfs)
        self.us_etf_page.indicator_settings_changed.connect(
            lambda settings: self._save_chart_indicator_preferences("US_ETF", settings)
        )
        self.us_etf_page.series_requested.connect(self.refresh_us_etf)
        self.us_etf_page.comparison_requested.connect(self.refresh_us_etf_comparison)
        self.us_etf_page.detach_requested.connect(self.open_detached_us_etf)
        self.us_etf_page.favorite_toggled.connect(self._toggle_watchlist_item)
        self.us_etf_page.watchlist_item_moved.connect(self._move_watchlist_item)
        self.us_etf_page.context_identity_open_requested.connect(
            self._open_chart_identity
        )
        self.us_etf_page.context_watchlist_quotes_requested.connect(
            self.refresh_context_watchlist_quotes
        )
        self.research_workspace_page.identity_requested.connect(
            self._open_research_identity
        )
        self.research_workspace_page.candidate_scan_requested.connect(
            self.refresh_candidate_scan
        )
        self.research_workspace_page.candidate_symbol_requested.connect(
            self._open_candidate_symbol
        )
        self.tabs.currentChanged.connect(self._candidate_tab_changed)
        self.watchlist_page.list_created.connect(self._create_watchlist)
        self.watchlist_page.list_renamed.connect(self._rename_watchlist)
        self.watchlist_page.list_removed.connect(self._remove_watchlist)
        self.watchlist_page.list_moved.connect(self._move_watchlist)
        self.watchlist_page.item_removed.connect(
            lambda list_id, key: self._change_watchlist_item(list_id, key, None)
        )
        self.watchlist_page.item_moved.connect(self._move_watchlist_item)
        self.watchlist_page.open_requested.connect(self._open_watchlist_identity)
        self.watchlist_page.selection_changed.connect(self.refresh_watchlist_quotes)
        self._render_watchlists()
        self.dashboard.market_chart_requested.connect(self.refresh_market_chart)
        self.dashboard.reload_requested.connect(self.reload_dashboard)
        self.data_status_page.refresh_status_reread_requested.connect(
            self._queue_local_dashboard_reload
        )
        self.dashboard.preferences_changed.connect(
            self._apply_and_save_dashboard_preferences
        )
        self.dashboard.selection_preferences_changed.connect(
            self._persist_dashboard_selection_preferences
        )
        self.dashboard.preferences_reset_requested.connect(
            self._reset_dashboard_preferences
        )
        self.account_page.refresh_requested.connect(
            lambda: self._request_account_snapshot(AccountRefreshTrigger.MANUAL)
        )
        self.dashboard.account_placeholder.open_requested.connect(
            self._open_account_workspace
        )
        self.account_page.remove_requested.connect(self._confirm_remove_account_snapshots)
        self.account_page.import_manual_requested.connect(
            self._choose_manual_account_csv
        )
        self.account_page.add_manual_requested.connect(
            lambda: self._edit_manual_account(None)
        )
        self.account_page.edit_manual_requested.connect(self._edit_manual_account)
        self.account_page.remove_manual_requested.connect(
            self._confirm_remove_manual_account
        )
        self.net_worth_page.refresh_requested.connect(self._reload_net_worth)
        self.net_worth_page.create_requested.connect(
            self._create_net_worth_snapshot
        )
        self.net_worth_page.revise_requested.connect(
            self._revise_net_worth_snapshot
        )
        self.net_worth_page.remove_exact_requested.connect(
            self._confirm_remove_net_worth_snapshot
        )
        self.backtest_page.run_requested.connect(self._request_backtest_run)
        self.backtest_page.reload_requested.connect(self._request_backtest_reload)
        self.backtest_page.export_requested.connect(self._request_backtest_export)
        self.backtest_page.scenario_requested.connect(
            self._request_backtest_scenario
        )
        self.backtest_page.configure_scenario_available(
            self.backtest_scenario_inputs is not None
        )
        self.local_reload_timer = QtCore.QTimer(self)
        self.local_reload_timer.setSingleShot(True)
        self.local_reload_timer.setInterval(900)
        self.local_reload_timer.timeout.connect(self._queue_local_dashboard_reload)
        self._local_dashboard_reload_queued = False
        self.current_observation_reload_timer = QtCore.QTimer(self)
        self.current_observation_reload_timer.setInterval(30 * 60 * 1000)
        self.current_observation_reload_timer.timeout.connect(
            self._on_current_observation_timer
        )
        self.current_observation_reload_timer.start()
        self.local_data_watcher = QtCore.QFileSystemWatcher(self)
        self._health_directory = project_root / "artifacts/daily_health"
        self._current_observation_directory = (
            project_root / "data/state/current_observations/global60m_current"
        )
        self._local_watch_targets = (
            self._health_directory,
            self._current_observation_directory,
        )
        self._local_watch_assignments: dict[Path, Path] = {}
        self._refresh_local_watch_paths()
        self.local_data_watcher.directoryChanged.connect(
            self._on_local_watch_directory_changed
        )
        self._close_retry_timer = QtCore.QTimer(self)
        self._close_retry_timer.setSingleShot(True)
        self._close_retry_timer.setInterval(50)
        self._close_retry_timer.timeout.connect(self._retry_pending_close)
        tabs.setAccessibleName("주요 화면")
        self.setStyleSheet("""
            QWidget { background:#f4f7fb; color:#132238; font-size:9.75pt; }
            QLabel { background:transparent; }
            QFrame#panel, QFrame#card { background:#ffffff; border:1px solid #d7e0eb; border-radius:9px; }
            QFrame#card { padding:7px; }
            QFrame#compactCard { background:#ffffff; border:1px solid #d7e0eb; border-radius:8px; }
            QFrame#compactCard[pinned="true"] { border:2px solid #2f6fb2; }
            QFrame#rateRow { background:#f8fafc; border:1px solid #e0e7f0; border-radius:5px; }
            QFrame#rateGroup { background:#ffffff; border:1px solid #e0e7f0; border-radius:6px; }
            QFrame#indexInfo { background:#ffffff; border:1px solid #d7e0eb; border-radius:7px; }
            QFrame#chartStartCard { background:#f8fafc; border:1px dashed #9eb1c8; border-radius:9px; }
            QLabel#indexSummary { color:#17375e; font-size:13px; font-weight:750; }
            QLabel#indexMeta { color:#5e7188; font-size:11px; }
            QLabel#indexLegend { color:#52677f; background:#eef4fa; border-radius:5px; padding:3px 7px; font-size:11px; }
            QLabel#rateGroupTitle { color:#31506f; font-size:10px; font-weight:800; }
            QLabel#rateChange { font-size:9px; font-weight:700; }
            QLabel#rateChange[tone="positive"] { color:#176b49; }
            QLabel#rateChange[tone="negative"] { color:#b4493a; }
            QLabel#rateChange[tone="neutral"] { color:#718198; }
            QFrame#unavailablePanel { background:#f8fafc; border:1px dashed #b9c7d8; border-radius:9px; }
            QLabel#cardTitle { color:#53657b; font-weight:700; font-size:12px; }
            QLabel#compactTitle { color:#52677f; font-weight:700; font-size:11px; }
            QLabel#compactValue { color:#10233d; font-weight:800; font-size:14px; }
            QLabel#compactMeta { color:#718198; font-size:10px; }
            QFrame#marketSessionStrip { background:#ffffff; border:0; border-radius:0; }
            QLabel#marketSessionText { color:#39414d; font-size:13px; font-weight:650; }
            QLabel#indicatorLabel { color:#2b4059; font-size:11px; }
            QLabel#indicatorState { border-radius:7px; padding:1px 6px; font-size:10px; font-weight:700; }
            QLabel#indicatorState[tone="positive"] { color:#176b49; background:#e7f5ee; }
            QLabel#indicatorState[tone="negative"] { color:#a33f38; background:#fbeceb; }
            QLabel#indicatorState[tone="warning"] { color:#8a5b12; background:#fff3d6; }
            QLabel#indicatorState[tone="neutral"] { color:#42617f; background:#edf3f8; }
            QLabel#indicatorState[tone="unavailable"] { color:#718198; background:#f2f4f7; }
            QLabel#flowHeader { color:#718198; font-size:9px; font-weight:700; }
            QLabel#flowInvestor { color:#31506f; font-size:10px; font-weight:700; }
            QLabel#flowValue { font-size:10px; font-weight:750; }
            QLabel#flowValue[tone="positive"] { color:#176b49; }
            QLabel#flowValue[tone="negative"] { color:#b4493a; }
            QLabel#flowValue[tone="neutral"] { color:#52677f; }
            QLabel#flowValue[tone="unavailable"] { color:#8a6b32; }
            QLabel#momentumSummary { color:#17375e; background:#eef4fa; border-radius:7px; padding:6px 8px; font-size:11px; font-weight:700; }
            QLabel#accountUnavailable { color:#718198; font-size:17px; font-weight:750; }
            QLabel#statusBadge { background:#eaf1f8; color:#315a84; border-radius:5px; padding:2px 5px; font-size:9px; font-weight:700; }
            QLabel#unavailableState { color:#755c29; font-size:16px; font-weight:800; }
            QLabel#pageTitle { font-size:24px; font-weight:800; color:#10233d; }
            QLabel#pageSubtitle { color:#6b7d91; }
            QLabel#sectionTitle { font-size:15px; font-weight:750; color:#17375e; }
            QLabel#chartStatus { color:#5e7188; padding:4px; }
            QLabel#freshness { background:#fff8e8; border:1px solid #efd28a; border-radius:6px; padding:7px; color:#805b16; }
            QProgressBar { background:#edf1f6; border:0; border-radius:4px; }
            QProgressBar::chunk { background:#4e7ba9; border-radius:4px; }
            QPushButton { background:#ffffff; border:1px solid #b9c7d8; border-radius:4px; padding:5px 9px; }
            QPushButton:hover { background:#edf4fb; }
            QComboBox, QCheckBox { background:#ffffff; border:1px solid #b9c7d8; padding:5px; border-radius:4px; }
            QComboBox:focus, QCheckBox:focus, QTabBar::tab:focus { border:2px solid #2f6fb2; }
            QTabWidget::pane { border:0; }
            QTabBar::tab { padding:10px 18px; background:#eef3f8; color:#52677f; }
            QTabBar::tab:selected { background:#ffffff; color:#174f88; font-weight:700; border-bottom:2px solid #2f6fb2; }
            QTabWidget#marketFlowTabs QTabBar::tab { padding:3px 16px; font-size:10px; }
            QHeaderView::section { background:#e8eef5; color:#243b55; padding:6px; border:1px solid #c9d4e1; }
            QTableWidget { background:#ffffff; gridline-color:#d7e0eb; alternate-background-color:#f7f9fc; }
        """)
        self.dashboard.apply_preferences(self._dashboard_preferences)
        for context, page in (
            ("INDEX", self.index_page), ("EQUITY", self.equity_page),
            ("US_ETF", self.us_etf_page),
        ):
            settings = self._dashboard_preferences.indicators_for(context)
            page.indicator_panel.apply(settings)
            page._apply_indicator_settings(settings)
        self.dashboard.set_preferences_status(dashboard_preferences_result.reason)
        self._apply_dashboard_window_geometry(
            self._dashboard_preferences.window_geometry
        )
        QtCore.QTimer.singleShot(0, self._queue_local_dashboard_reload); QtCore.QTimer.singleShot(0, self._request_current_observation_acquisition); QtCore.QTimer.singleShot(0, lambda: self.refresh_index("KOSPI","120D")); QtCore.QTimer.singleShot(0, self.refresh_backtest)
        # Startup performs one provider-free local read.  A provider-capable
        # refresh is constructed only from the Account page's MANUAL click.
        QtCore.QTimer.singleShot(0, self._request_account_snapshot)
        QtCore.QTimer.singleShot(0, self._reload_net_worth)
        QtCore.QTimer.singleShot(0, self.refresh_watchlist_quotes)

    def _closest_local_watch_path(self, target: Path) -> Path | None:
        """Return only an existing project-local target or its closest parent."""

        try:
            resolved_root = self.project_root.resolve(strict=True)
            target.resolve(strict=False).relative_to(resolved_root)
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            return None
        candidate = target
        while candidate != self.project_root and not candidate.is_dir():
            candidate = candidate.parent
        if not candidate.is_dir():
            return None
        try:
            candidate.resolve(strict=True).relative_to(resolved_root)
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            return None
        return candidate

    def _refresh_local_watch_paths(self) -> None:
        """Keep exact Health/current watches, using only safe parents until creation."""

        assignments = {
            target: candidate
            for target in self._local_watch_targets
            if (candidate := self._closest_local_watch_path(target)) is not None
        }
        desired = {str(candidate) for candidate in assignments.values()}
        try:
            current = set(self.local_data_watcher.directories())
        except RuntimeError:
            return
        for path in sorted(current - desired):
            try:
                self.local_data_watcher.removePath(path)
            except (OSError, RuntimeError):
                continue
        try:
            current = set(self.local_data_watcher.directories())
        except RuntimeError:
            return
        for path in sorted(desired - current):
            try:
                self.local_data_watcher.addPath(path)
            except (OSError, RuntimeError):
                continue
        self._local_watch_assignments = assignments

    @QtCore.Slot(str)
    def _on_local_watch_directory_changed(self, path: str) -> None:
        if self._closing:
            return
        event_path = Path(path)
        affected = tuple(
            (target, watched)
            for target, watched in self._local_watch_assignments.items()
            if watched == event_path
        )
        self._refresh_local_watch_paths()
        if any(watched == target or target.is_dir() for target, watched in affected):
            self.local_reload_timer.start()

    def _available_window_rectangle(self, geometry: WindowGeometry) -> tuple[int, int, int, int]:
        point = QtCore.QPoint(geometry.x, geometry.y)
        screen = QtWidgets.QApplication.screenAt(point)
        if screen is None:
            screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return (0, 0, 1600, 900)
        available = screen.availableGeometry()
        return (available.x(), available.y(), available.width(), available.height())

    def _apply_dashboard_window_geometry(self, geometry: WindowGeometry) -> None:
        safe = safe_window_geometry(
            geometry, self._available_window_rectangle(geometry)
        )
        if self.isMaximized() and not safe.maximized:
            self.showNormal()
        self.setGeometry(safe.x, safe.y, safe.width, safe.height)
        if safe.maximized:
            self.setWindowState(self.windowState() | QtCore.Qt.WindowMaximized)

    def _apply_and_save_dashboard_preferences(
        self, preferences: DashboardPreferences,
    ) -> None:
        previous = self._dashboard_preferences
        try:
            self.dashboard_preferences_store.save(preferences)
        except DashboardPreferencesError:
            self.dashboard.apply_preferences(previous)
            self.dashboard.set_preferences_status("WRITE_FAILED")
            return
        self._dashboard_preferences = preferences
        self.dashboard.apply_preferences(preferences)
        self.dashboard.set_preferences_status("SAVED")
        if (
            previous.default_market_asset != preferences.default_market_asset
            or previous.default_market_period != preferences.default_market_period
        ):
            self.refresh_market_chart(
                preferences.default_market_asset, preferences.default_market_period
            )
        if previous.default_nq_interval != preferences.default_nq_interval:
            self.dashboard._rerender_nq_chart()

    def _save_chart_indicator_preferences(
        self, context: str, settings: ChartIndicatorPreferences,
    ) -> None:
        previous = self._dashboard_preferences
        try:
            preferences = previous.with_indicators(context, settings)
            self.dashboard_preferences_store.save(preferences)
        except (DashboardPreferencesError, KeyError):
            return
        self._dashboard_preferences = preferences

    def _persist_dashboard_selection_preferences(
        self, preferences: DashboardPreferences,
    ) -> None:
        previous = self._dashboard_preferences
        try:
            self.dashboard_preferences_store.save(preferences)
        except DashboardPreferencesError:
            self.dashboard.apply_preferences(previous)
            self.dashboard.set_preferences_status("WRITE_FAILED")
            return
        self._dashboard_preferences = preferences
        self.dashboard.set_preferences_status("SAVED")

    def _reset_dashboard_preferences(self) -> None:
        previous = self._dashboard_preferences
        try:
            preferences = self.dashboard_preferences_store.reset()
        except DashboardPreferencesError:
            self.dashboard.apply_preferences(previous)
            self.dashboard.set_preferences_status("WRITE_FAILED")
            return
        self._dashboard_preferences = preferences
        self.dashboard.apply_preferences(preferences)
        self.dashboard.set_preferences_status("RESET")
        self._apply_dashboard_window_geometry(preferences.window_geometry)
        if (
            previous.default_market_asset != preferences.default_market_asset
            or previous.default_market_period != preferences.default_market_period
        ):
            self.refresh_market_chart(
                preferences.default_market_asset, preferences.default_market_period
            )
        if previous.default_nq_interval != preferences.default_nq_interval:
            self.dashboard._rerender_nq_chart()

    def _store_dashboard_window_geometry(self) -> None:
        rectangle = self.normalGeometry() if self.isMaximized() else self.geometry()
        geometry = WindowGeometry(
            rectangle.x(), rectangle.y(), rectangle.width(), rectangle.height(),
            self.isMaximized(),
        )
        preferences = with_geometry(self._dashboard_preferences, geometry)
        try:
            self.dashboard_preferences_store.save(preferences)
        except DashboardPreferencesError:
            self.dashboard.set_preferences_status("WRITE_FAILED")
            return
        self._dashboard_preferences = preferences

    def _render_dashboard_snapshot(self, snapshot: object) -> None:
        if isinstance(snapshot, Exception) or not isinstance(snapshot, dict):
            self.dashboard.render_unavailable("로컬 데이터 읽기 실패")
            projection = project_refresh_status(
                self.project_root, health={}, metrics={}, account=self._account_view,
            )
            self.dashboard.render_refresh_status(projection)
            self.data_status_page.render_refresh_status(projection)
            self.data_status_page.render_current_sources(
                {}, as_of_utc=pd.Timestamp.now(tz="UTC")
            )
            self.data_status_page.render_dashboard_decisions({})
            return
        try:
            snapshot["account_snapshot"] = self._account_view
            snapshot["account_portfolio"] = self._account_portfolio
            self.dashboard.render(snapshot)
            self.data_status_page.render(snapshot.get("data_health", {}))
            projection = project_refresh_status(
                self.project_root,
                health=snapshot.get("data_health", {}),
                metrics=snapshot.get("dashboard_metrics", {}),
                account=self._account_view,
                generated_at_utc=snapshot.get("market_session_as_of_utc"),
                current_in_progress=self._current_observation_thread is not None,
            )
            self.dashboard.render_refresh_status(projection)
            self.data_status_page.render_refresh_status(projection)
            self.data_status_page.render_current_sources(
                {
                    key: value
                    for key, value in snapshot.get("current_observation_coverage", {}).items()
                    if isinstance(value, CurrentObservationCoverageView)
                },
                as_of_utc=snapshot.get(
                    "market_session_as_of_utc", pd.Timestamp.now(tz="UTC")
                ),
            )
            self.data_status_page.render_dashboard_decisions(snapshot)
        except (OSError, PermissionError, ValueError, KeyError, TypeError):
            self.dashboard.render_unavailable("로컬 데이터 읽기 실패")
            projection = project_refresh_status(
                self.project_root, health={}, metrics={}, account=self._account_view,
            )
            self.dashboard.render_refresh_status(projection)
            self.data_status_page.render_refresh_status(projection)
            self.data_status_page.render_current_sources(
                {}, as_of_utc=pd.Timestamp.now(tz="UTC")
            )
            self.data_status_page.render_dashboard_decisions({})

    def _request_current_card_stage(self) -> int | None:
        if self._closing:
            return None
        self._current_stage_generation += 1
        generation = self._current_stage_generation
        self._current_stage_pending = generation
        self._start_current_card_stage()
        return generation

    def _start_current_card_stage(self) -> None:
        if (
            self._closing
            or self._current_stage_thread is not None
            or self._current_stage_pending is None
        ):
            return
        generation = self._current_stage_pending
        self._current_stage_pending = None
        thread = QtCore.QThread(self)
        diagnostic_run_id = new_session_id()
        worker = DashboardCurrentStageWorker(
            self.current_stage_service, generation,
        )
        worker.moveToThread(thread)
        worker.failed.connect(
            lambda error, run_id=diagnostic_run_id: self._record_worker_failure(
                error, run_id=run_id, code="CURRENT_CARD_STAGE_FAILED",
                stage="LOCAL_CURRENT_CARD_READ",
            )
        )
        thread.started.connect(worker.run)
        worker.completed.connect(self._current_card_stage_completed)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(
            self._managed_thread_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        thread.destroyed.connect(self._current_card_stage_thread_destroyed)
        self._current_stage_thread = thread
        self._current_stage_worker = worker
        thread.start()

    @QtCore.Slot(int, object)
    def _current_card_stage_completed(
        self, generation: int, result: object,
    ) -> None:
        if self._closing or generation != self._current_stage_generation:
            return
        if not isinstance(result, DashboardCurrentStageView):
            return
        self._latest_current_stage = (generation, result)
        self.dashboard.render_current_stage(result)

    @QtCore.Slot(object)
    def _current_card_stage_thread_destroyed(self, _destroyed: object) -> None:
        self._current_stage_thread = None
        self._current_stage_worker = None
        if not self._closing:
            QtCore.QTimer.singleShot(0, self._start_current_card_stage)
        self._schedule_pending_close_check()

    @staticmethod
    def _metric_source_time(metric: DashboardMetricView | None) -> pd.Timestamp | None:
        if metric is None or metric.source_timestamp is None:
            return None
        try:
            value = pd.Timestamp(metric.source_timestamp)
            if value.tzinfo is None or value.utcoffset() is None:
                return None
            return value.tz_convert("UTC")
        except (TypeError, ValueError):
            return None

    def _merge_latest_current_stage(
        self, snapshot: object, full_stage_generation: int,
    ) -> object:
        latest = self._latest_current_stage
        if not isinstance(snapshot, dict) or latest is None:
            return snapshot
        generation, stage = latest
        if generation < full_stage_generation:
            return snapshot
        merged = dict(snapshot)
        metrics = {
            key: value for key, value in snapshot.get("dashboard_metrics", {}).items()
            if isinstance(value, DashboardMetricView)
        }
        for key, staged in stage.metrics.items():
            existing = metrics.get(key)
            staged_time = self._metric_source_time(staged)
            existing_time = self._metric_source_time(existing)
            if staged.displays_value:
                if existing_time is not None and staged_time is not None and existing_time > staged_time:
                    continue
                metrics[key] = staged
                continue
            # A newer rejected current projection invalidates only a previous
            # current-only value. It does not erase an independently valid
            # finalized daily surface from the full snapshot.
            if existing is not None and (
                existing.dataset_id in {
                    "market_price_60m_current", "market_price_15m_current",
                    "KR_INDEX_CURRENT",
                }
                or existing.route.startswith((
                    "yahoo-market-current:", "yahoo-global60m-current:",
                    "toss-market-price:",
                ))
            ):
                if (
                    existing_time is not None
                    and staged_time is not None
                    and existing_time > staged_time
                ):
                    continue
                metrics[key] = staged
        merged["dashboard_metrics"] = metrics
        merged["treasury_rate_views"] = self.service.treasury_rate_views(metrics)
        merged["market_session_as_of_utc"] = stage.as_of_utc
        return merged

    def refresh_dashboard(self, session="U") -> int | None:
        if self._closing:
            return None
        current_stage_generation = self._request_current_card_stage()
        market_generation = self._local_read_generations["market_chart"] + 1
        self._local_read_generations["market_chart"] = market_generation
        self._local_read_pending.pop("market_chart", None)
        return self._request_local_read(
            "dashboard",
            (
                session,
                self.dashboard.market_asset.currentText(),
                self.dashboard.market_period.currentText(),
                market_generation,
                current_stage_generation if current_stage_generation is not None else -1,
            ),
        )

    def reload_dashboard(self):
        """Queue one retained-local Dashboard reload without external activity."""
        self._queue_local_dashboard_reload()

    def _queue_local_dashboard_reload(self) -> None:
        """Coalesce startup, manual, watcher, and 30-minute local reloads."""
        if self._closing or self._local_dashboard_reload_queued:
            return
        self._local_dashboard_reload_queued = True
        QtCore.QTimer.singleShot(0, self._run_local_dashboard_reload)

    def _run_local_dashboard_reload(self) -> None:
        if self._closing:
            self._local_dashboard_reload_queued = False
            return
        try:
            self.refresh_dashboard("U")
        finally:
            self._local_dashboard_reload_queued = False

    def _request_local_read(self, action: str, request: object) -> int | None:
        if self._closing:
            return None
        if action not in self._local_read_generations:
            raise ValueError("unsupported local-read action")
        generation = self._local_read_generations[action] + 1
        self._local_read_generations[action] = generation
        self._local_read_sequence += 1
        self._local_read_pending[action] = (
            self._local_read_sequence, generation, request,
        )
        self._start_next_local_read()
        return generation

    def _start_next_local_read(self) -> None:
        if self._closing or self._local_read_thread is not None:
            return
        if not self._local_read_pending:
            return
        action, (_sequence, generation, request) = min(
            self._local_read_pending.items(), key=lambda item: item[1][0]
        )
        del self._local_read_pending[action]
        thread = QtCore.QThread(self)
        diagnostic_run_id = new_session_id()
        worker = LocalReadWorker(
            self.service,
            self.health_artifact_service,
            generation,
            action,
            request,
        )
        worker.moveToThread(thread)
        worker.failed.connect(
            lambda error, run_id=diagnostic_run_id: self._record_worker_failure(
                error, run_id=run_id, code="LOCAL_READ_WORKER_FAILED",
                stage="LOCAL_READ",
            )
        )
        thread.started.connect(worker.run)
        worker.completed.connect(self._local_read_completed)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(
            self._managed_thread_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        thread.destroyed.connect(self._local_read_thread_destroyed)
        self._local_read_thread = thread
        self._local_read_worker = worker
        self._local_read_active = (action, generation)
        thread.start()

    @QtCore.Slot(int, str, object)
    def _local_read_completed(
        self, generation: int, action: str, result: object,
    ) -> None:
        if self._closing or generation != self._local_read_generations.get(action):
            return
        if action == "dashboard":
            if not isinstance(result, _DashboardLocalRead):
                result = _DashboardLocalRead(result, result, result, -1)
            self._render_dashboard_snapshot(
                self._merge_latest_current_stage(
                    result.snapshot, result.current_stage_generation,
                )
            )
            if (
                result.market_generation
                == self._local_read_generations["market_chart"]
            ):
                self.dashboard.render_market_chart(
                    result.market_frame
                    if isinstance(result.market_frame, pd.DataFrame)
                    else pd.DataFrame()
                )
            if not isinstance(result.health_view, Exception):
                self.data_status_page.render_report(result.health_view)
        elif action == "index":
            if isinstance(result, Exception):
                self.index_page.render(pd.DataFrame())
            else:
                try:
                    self.index_page.render(result)
                except (OSError, PermissionError, ValueError, KeyError, TypeError):
                    self.index_page.render(pd.DataFrame())
        elif action == "market_chart":
            self.dashboard.render_market_chart(
                result if isinstance(result, pd.DataFrame) else pd.DataFrame()
            )

    @QtCore.Slot(object)
    def _local_read_thread_destroyed(self, _destroyed: object) -> None:
        self._local_read_thread = None
        self._local_read_worker = None
        self._local_read_active = None
        if not self._closing:
            QtCore.QTimer.singleShot(0, self._start_next_local_read)
        self._schedule_pending_close_check()

    @QtCore.Slot(object)
    def _record_worker_failure(
        self, error: object, *, run_id: str, code: str, stage: str,
    ) -> None:
        if not isinstance(error, BaseException):
            return
        safe_record_failure(
            self._diagnostic_store,
            project_root=self.project_root, domain="GUI",
            kind="TERMINAL_FAILURE", session_id=self._diagnostic_session_id,
            run_id=run_id, code=code, stage=stage, error=error,
        )

    def _request_current_observation_acquisition(self) -> None:
        """Coalesce due acquisition requests; the collector owns API budgets."""
        if self._closing:
            return
        if self._current_observation_thread is not None:
            # Keep the lane owned through QObject destruction, not merely
            # QThread.finished.  A finished thread can still have deferred
            # delete events pending in the GUI loop.  Do not even reevaluate
            # the activation factory in that gap.
            return
        runner = self.current_observation_runner
        if runner is None and self.current_observation_runner_factory is not None:
            try:
                runner = self.current_observation_runner_factory()
            except Exception:
                # A missing/malformed activation boundary is provider-free.
                return
        if runner is None:
            return
        thread = QtCore.QThread(self)
        diagnostic_run_id = new_session_id()
        worker = CurrentObservationAcquisitionWorker(runner)
        worker.moveToThread(thread)
        worker.failed.connect(
            lambda error, run_id=diagnostic_run_id: self._record_worker_failure(
                error, run_id=run_id, code="CURRENT_OBSERVATION_FAILED",
                stage="ACQUISITION"
            )
        )
        thread.started.connect(worker.run)
        worker.completed.connect(self._current_observation_acquisition_completed)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(
            self._managed_thread_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        thread.destroyed.connect(self._current_observation_thread_destroyed)
        self._current_observation_thread = thread
        self._current_observation_worker = worker
        thread.start()

    def _on_current_observation_timer(self) -> None:
        """Keep the accepted local reread while optionally requesting a due run."""
        self._queue_local_dashboard_reload()
        self._request_current_observation_acquisition()

    @QtCore.Slot(object)
    def _current_observation_acquisition_completed(self, result: object) -> None:
        self._current_observation_last_result = result
        if not self._closing:
            # The collector itself is Landing-first and atomically preserves a
            # prior projection on failure.  Always reread rather than carrying
            # a worker result into the Dashboard.
            self._queue_local_dashboard_reload()

    @QtCore.Slot(object)
    def _current_observation_thread_destroyed(self, _destroyed: object) -> None:
        """Release the lane only after Qt has completed deferred deletion."""

        self._current_observation_thread = None
        self._current_observation_worker = None
        self._schedule_pending_close_check()

    @QtCore.Slot()
    def _managed_thread_finished(self) -> None:
        """Queue retirement after every already-posted finished observer."""

        thread = self.sender()
        if (
            not isinstance(thread, QtCore.QThread)
            or not any(thread is owned for owned in self._managed_worker_threads())
        ):
            return
        QtCore.QTimer.singleShot(
            0, lambda thread=thread: self._retire_managed_thread(thread),
        )

    def _retire_managed_thread(self, thread: QtCore.QThread) -> None:
        """Delete one still-owned stopped thread in a later GUI turn."""

        if not any(thread is owned for owned in self._managed_worker_threads()):
            return
        if thread.isRunning():
            # QThread.finished may be delivered while the wrapper still
            # transiently reports running.  Dropping that one notification
            # leaves the owned pointer live forever and a close-pending window
            # can never retire.  Recheck in a later GUI turn instead.
            QtCore.QTimer.singleShot(
                10, lambda thread=thread: self._retire_managed_thread(thread),
            )
            return
        thread.deleteLater()
        # Nested processEvents loops do not guarantee DeferredDelete delivery,
        # so flush only this stopped receiver after all finished observers from
        # the preceding GUI turn have had a chance to run.
        QtCore.QCoreApplication.sendPostedEvents(
            thread, QtCore.QEvent.DeferredDelete,
        )

    def _request_account_snapshot(
        self, trigger: AccountRefreshTrigger | None = None,
    ) -> None:
        if self._closing:
            return
        if trigger not in (None, AccountRefreshTrigger.MANUAL):
            return
        if self._account_thread is not None:
            if trigger is AccountRefreshTrigger.MANUAL:
                self._account_pending_trigger = trigger
            return
        thread = QtCore.QThread(self)
        diagnostic_run_id = new_session_id()
        worker = AccountSnapshotWorker(
            self.account_snapshot_service,
            self.account_portfolio_service,
            trigger,
            (
                self.account_refresher
                if trigger is AccountRefreshTrigger.MANUAL
                else None
            ),
            primary_enabled=self.toss_runtime_enabled,
            unavailable_reason=self.toss_runtime_reason,
            kb_refresher=(
                self.kb_account_refresher
                if trigger is AccountRefreshTrigger.MANUAL
                else None
            ),
        )
        worker.moveToThread(thread)
        worker.failed.connect(
            lambda error, run_id=diagnostic_run_id: self._record_worker_failure(
                error, run_id=run_id, code="ACCOUNT_WORKER_FAILED", stage="ACCOUNT"
            )
        )
        thread.started.connect(worker.run)
        worker.completed.connect(self._account_snapshot_loaded)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(
            lambda thread=thread: self._account_thread_finished(thread),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self._account_thread = thread
        self._account_worker = worker
        thread.start()

    @QtCore.Slot()
    def _open_account_workspace(self) -> None:
        self.tabs.setCurrentWidget(self.account_workspace_page)
        self.account_workspace_tabs.setCurrentWidget(self.account_page)

    def _choose_manual_account_csv(self) -> None:
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "아빠 시트 CSV 가져오기",
            str(self.project_root),
            "CSV 파일 (*.csv);;모든 파일 (*)",
        )
        if not path:
            return
        try:
            self._import_manual_account_csv(Path(path))
        except (OSError, UnicodeError, TypeError, ValueError):
            self.account_page.summary.setText(
                "CSV 가져오기 실패 · 아빠 탭 형식과 기준일·수량·원가를 확인해 주세요"
            )
            return
        self.account_page.summary.setText(
            "아빠 시트 CSV 가져오기 완료 · 예약 동기화 없음 · 현재가는 포함하지 않음"
        )

    def _import_manual_account_csv(self, path: Path) -> None:
        imported = load_appa_sheet_csv(path)
        existing = self.manual_account_store.load()
        imported_ids = {account.source_id for account in imported.accounts}
        merged = ManualAccountRegistry(tuple(
            account for account in existing.accounts
            if account.source_id not in imported_ids
        ) + imported.accounts)
        self.manual_account_store.save(merged)
        self._schedule_manual_account_reload()

    def _edit_manual_account(self, source_id: str | None) -> None:
        try:
            registry = self.manual_account_store.load()
        except (OSError, TypeError, ValueError):
            self.account_page.summary.setText(
                "수동 계좌 저장소 검증 실패 · 기존 파일을 수정하지 않았습니다"
            )
            return
        baseline = next(
            (account for account in registry.accounts if account.source_id == source_id),
            None,
        )
        if source_id is not None and baseline is None:
            self.account_page.summary.setText("선택한 수동 계좌가 현재 없습니다")
            return
        dialog = self._manual_account_dialog_factory(baseline, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            self._upsert_manual_account(dialog.account_record())
        except (OSError, TypeError, ValueError):
            self.account_page.summary.setText(
                "수동 계좌 저장 실패 · 기존 파일을 수정하지 않았습니다"
            )
            return
        self.account_page.summary.setText("수동 계좌 저장 완료 · 현재가 없는 원가 기준")

    def _upsert_manual_account(self, record: ManualAccountRecord) -> None:
        manual_account_registry_payload(ManualAccountRegistry((record,)))
        registry = self.manual_account_store.load()
        replaced = False
        accounts: list[ManualAccountRecord] = []
        for account in registry.accounts:
            if account.source_id == record.source_id:
                accounts.append(record)
                replaced = True
            else:
                accounts.append(account)
        if not replaced:
            accounts.append(record)
        self.manual_account_store.save(ManualAccountRegistry(tuple(accounts)))
        self._schedule_manual_account_reload()

    def _confirm_remove_manual_account(self, source_id: str) -> None:
        try:
            registry = self.manual_account_store.load()
        except (OSError, TypeError, ValueError):
            self.account_page.summary.setText(
                "수동 계좌 저장소 검증 실패 · 삭제하지 않았습니다"
            )
            return
        account = next(
            (item for item in registry.accounts if item.source_id == source_id), None
        )
        if account is None:
            self.account_page.summary.setText("선택한 수동 계좌가 현재 없습니다")
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "수동 계좌 삭제",
            f"'{account.label}' 수동 계좌와 보유 종목을 로컬 저장소에서 삭제할까요?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self._remove_manual_account(source_id)
        self.account_page.summary.setText("선택한 수동 계좌 삭제 완료")

    def _remove_manual_account(self, source_id: str) -> None:
        registry = self.manual_account_store.load()
        retained = tuple(
            account for account in registry.accounts
            if account.source_id != source_id
        )
        if len(retained) == len(registry.accounts):
            raise ValueError("manual account does not exist")
        self.manual_account_store.save(ManualAccountRegistry(retained))
        self._schedule_manual_account_reload()

    def _schedule_manual_account_reload(self) -> None:
        if self._account_thread is not None:
            self._manual_account_reload_pending = True
            return
        self._request_account_snapshot()

    @QtCore.Slot(object)
    def _account_snapshot_loaded(self, result: AccountWorkspaceView) -> None:
        if self._closing:
            return
        self._account_view = result.primary
        self._account_portfolio = result.portfolio
        self.dashboard.account_placeholder.set_portfolio(result.portfolio)
        self.account_page.configure_source_actions(
            build_account_source_action_views(
                result.portfolio,
                self.project_root,
                toss_runtime_enabled=self.toss_runtime_enabled,
                kb_runtime_enabled=self.kb_runtime_enabled,
            )
        )
        self.account_page.render(result.portfolio)

    def _account_thread_finished(self, thread: QtCore.QThread) -> None:
        """Release the Account lane as soon as its event loop has stopped."""

        if self._account_thread is not thread:
            return
        if thread.isRunning():
            # Qt may deliver ``finished`` before the Python wrapper stops
            # reporting ``isRunning()``.  Recheck instead of dropping the only
            # completion notification; otherwise Account ownership can remain
            # live forever and a close-pending window can never retire.
            QtCore.QTimer.singleShot(
                10, lambda thread=thread: self._account_thread_finished(thread),
            )
            return
        self._account_thread = None
        self._account_worker = None
        pending = self._account_pending_trigger
        self._account_pending_trigger = None
        manual_reload = self._manual_account_reload_pending
        self._manual_account_reload_pending = False
        # Retire the stopped QObject before either a pending cycle or a pending
        # window close runs.  Close must not depend on DeferredDelete delivery
        # to discover that the provider call has already finished.
        QtCore.QTimer.singleShot(
            0, lambda thread=thread: self._retire_stopped_thread(thread),
        )
        if pending is not None and not self._closing:
            # Start the single coalesced successor in this GUI turn so callers
            # never observe a false idle gap between the two cycles.
            self._request_account_snapshot(pending)
        elif manual_reload and not self._closing:
            self._request_account_snapshot()
        self._schedule_pending_close_check()

    @staticmethod
    def _retire_stopped_thread(thread: QtCore.QThread) -> None:
        if thread.isRunning():
            return
        thread.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(
            thread, QtCore.QEvent.DeferredDelete,
        )

    def _confirm_remove_account_snapshots(self) -> None:
        if self._account_thread is not None:
            QtWidgets.QMessageBox.information(
                self,
                "계좌 로컬 기록 전체 삭제",
                "계좌 스냅샷 읽기 또는 갱신이 끝난 뒤 다시 시도해 주세요.",
            )
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "계좌 로컬 기록 전체 삭제",
            "이 프로젝트의 로컬 계좌 스냅샷과 계좌 가치 이력, "
            "관련 임시 기록을 모두 삭제할까요?\n"
            "수동 계좌 저장소, 인증정보와 시장 데이터는 변경하지 않습니다.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.account_refresher = None
        self.kb_account_refresher = None
        self.account_page.configure_refresh_disclosure(False)
        try:
            remove_retained_account_snapshots(self.project_root)
        except AccountSnapshotRemovalError:
            self.account_page.summary.setText(
                "계좌 스냅샷·가치 이력 삭제 실패 · 로컬 저장소 상태 확인 필요"
            )
            return
        unavailable = AccountSnapshotView(
            state=AccountSnapshotState.NOT_AVAILABLE,
            reason="ACCOUNT_SNAPSHOT_REMOVED",
        )
        self._account_view = unavailable
        self._account_portfolio = AccountPortfolioView(
            entries=(), user_fund_totals=()
        )
        self.dashboard.account_placeholder.set_view(unavailable)
        self.account_page.render(self._account_portfolio)
        self.account_page.summary.setText(
            "로컬 계좌 스냅샷·가치 이력 삭제 완료 · 수동 계좌 저장소 보존"
        )

    def _reload_net_worth(self, selected_date: date | None = None) -> None:
        try:
            history = self.net_worth_store.load_history()
        except NetWorthPersistenceError:
            self.net_worth_page.set_history(())
            self.net_worth_page.render_unavailable(
                "로컬 순자산 이력 검증 실패 · HISTORY_INVALID"
            )
            return
        self.net_worth_page.set_history(history, selected_date=selected_date)

    @QtCore.Slot()
    def _create_net_worth_snapshot(self) -> None:
        self._open_net_worth_snapshot_dialog(None)

    @QtCore.Slot(object)
    def _revise_net_worth_snapshot(self, view: object) -> None:
        if type(view) is not NetWorthView:
            self.net_worth_page.summary.setText(
                "순자산 수정 실패 · 현재 선택 상태를 다시 확인하세요."
            )
            return
        self._open_net_worth_snapshot_dialog(view)

    def _open_net_worth_snapshot_dialog(
        self, baseline: NetWorthView | None,
    ) -> None:
        dialog = self._net_worth_dialog_factory(baseline, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        payload = getattr(dialog, "accepted_payload", None)
        if not isinstance(payload, Mapping):
            try:
                payload = dialog.snapshot_payload()
            except (AttributeError, NetWorthValidationError):
                self.net_worth_page.summary.setText(
                    "순자산 입력 검증 실패 · 값은 저장되지 않았습니다."
                )
                return
        self._save_net_worth_snapshot(payload, baseline=baseline)

    def _save_net_worth_snapshot(
        self,
        payload: Mapping[str, object],
        *,
        baseline: NetWorthView | None,
    ) -> None:
        try:
            candidate = parse_snapshot(payload)
            history = self.net_worth_store.load_history()
            exact = [
                record.view
                for record in history
                if record.view.snapshot.as_of_date == candidate.as_of_date
            ]
            latest = exact[-1] if exact else None
            if baseline is None and latest is not None:
                self.net_worth_page.summary.setText(
                    "같은 날짜 이력이 있습니다 · 선택 날짜 수정으로 저장하세요."
                )
                return
            if baseline is not None:
                if (
                    candidate.as_of_date != baseline.snapshot.as_of_date
                    or latest is None
                    or _net_worth_snapshot_semantics(latest.snapshot)
                    != _net_worth_snapshot_semantics(baseline.snapshot)
                ):
                    self.net_worth_page.summary.setText(
                        "순자산 수정 기준이 변경되었습니다 · 다시 불러오세요."
                    )
                    return
                if (
                    _net_worth_snapshot_semantics(candidate)
                    == _net_worth_snapshot_semantics(baseline.snapshot)
                ):
                    self.net_worth_page.summary.setText(
                        "변경 내용 없음 · 저장하지 않았습니다."
                    )
                    return
            record = self.net_worth_store.save_snapshot(payload)
        except NetWorthValidationError:
            self.net_worth_page.summary.setText(
                "순자산 입력 검증 실패 · 값은 저장되지 않았습니다."
            )
            return
        except NetWorthPersistenceError:
            self.net_worth_page.summary.setText(
                "순자산 로컬 저장 실패 · 기존 이력은 유지됩니다."
            )
            return
        self._reload_net_worth(selected_date=record.view.snapshot.as_of_date)
        self.net_worth_page.summary.setText(
            f"{record.view.snapshot.as_of_date.isoformat()} 로컬 순자산 스냅샷 저장 완료"
        )

    @QtCore.Slot(object)
    def _confirm_remove_net_worth_snapshot(self, as_of_date: object) -> None:
        if not isinstance(as_of_date, date):
            self.net_worth_page.render_unavailable("삭제할 정확한 날짜가 없습니다.")
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "순자산 스냅샷 삭제",
            f"{as_of_date.isoformat()} 로컬 순자산 스냅샷만 삭제할까요?\n"
            "계좌·인증정보·시장 데이터와 다른 날짜 이력은 변경하지 않습니다.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            removed = self.net_worth_store.remove_exact_date(as_of_date)
        except NetWorthPersistenceError:
            self.net_worth_page.summary.setText(
                "정확한 날짜 스냅샷 삭제 실패 · 최신 날짜인지 로컬 상태 확인 필요"
            )
            return
        self._reload_net_worth()
        if removed:
            self.net_worth_page.summary.setText(
                f"{as_of_date.isoformat()} 로컬 순자산 스냅샷 삭제 완료 · 숫자 표시 안 함"
            )

    def refresh_index(self, index, period):
        return self._request_local_read("index", (index, period))

    def _render_watchlists(self, *, selected_list_id: str | None = None) -> None:
        self.watchlist_page.render(self._watchlist_state)
        if selected_list_id is not None:
            position = self.watchlist_page.list_selector.findData(selected_list_id)
            if position >= 0:
                self.watchlist_page.list_selector.setCurrentIndex(position)
        self.equity_page.set_watchlists(self._watchlist_state)
        self.us_etf_page.set_watchlists(self._watchlist_state)
        self.research_workspace_page.set_watchlists(self._watchlist_state)
        for window in tuple(self._detached_windows):
            if window.kind in {"equity", "us_etf"}:
                window.page.set_watchlists(self._watchlist_state)
        requests: dict[tuple[tuple[str, str], ...], tuple[EquityIdentity, ...]] = {}
        pages = [self.equity_page, self.us_etf_page]
        pages.extend(
            window.page for window in tuple(self._detached_windows)
            if window.kind in {"equity", "us_etf"}
        )
        for page in pages:
            identities = page.context_watchlist_identities()
            if identities:
                requests.setdefault(tuple(item.key for item in identities), identities)
        for identities in requests.values():
            self.refresh_context_watchlist_quotes(identities)

    @QtCore.Slot(object)
    def refresh_context_watchlist_quotes(
        self, identities: tuple[EquityIdentity, ...],
    ) -> None:
        if identities:
            self._request_equity_job("watchlist", tuple(identities))

    @QtCore.Slot(object, str, bool)
    def _toggle_watchlist_item(
        self, identity: EquityIdentity, list_id: str, should_add: bool,
    ) -> None:
        try:
            self._watchlist_state = (
                self.watchlist_service.add_item(list_id, identity)
                if should_add else self.watchlist_service.remove_item(list_id, identity.key)
            )
        except (OSError, PermissionError, ValueError, KeyError) as error:
            self.watchlist_page.show_error(f"관심종목 저장 실패 · 기존 목록 보존 · {error}")
            return
        self._render_watchlists(selected_list_id=list_id)
        self.refresh_watchlist_quotes(list_id)

    def _change_watchlist_item(
        self, list_id: str, key: tuple[str, str], identity: EquityIdentity | None,
    ) -> None:
        try:
            self._watchlist_state = (
                self.watchlist_service.add_item(list_id, identity)
                if identity is not None else self.watchlist_service.remove_item(list_id, key)
            )
        except (OSError, PermissionError, ValueError, KeyError) as error:
            self.watchlist_page.show_error(f"관심종목 저장 실패 · 기존 목록 보존 · {error}")
            return
        self._render_watchlists(selected_list_id=list_id)
        self.refresh_watchlist_quotes(list_id)

    @QtCore.Slot(str)
    def _create_watchlist(self, name: str) -> None:
        existing = {item.list_id for item in self._watchlist_state.lists}
        try:
            self._watchlist_state = self.watchlist_service.create_list(name)
        except (OSError, PermissionError, ValueError, KeyError) as error:
            self.watchlist_page.show_error(f"목록 추가 실패 · 기존 목록 보존 · {error}")
            return
        created = next(item.list_id for item in self._watchlist_state.lists if item.list_id not in existing)
        self._render_watchlists(selected_list_id=created)

    @QtCore.Slot(str, str)
    def _rename_watchlist(self, list_id: str, name: str) -> None:
        try:
            self._watchlist_state = self.watchlist_service.rename_list(list_id, name)
        except (OSError, PermissionError, ValueError, KeyError) as error:
            self.watchlist_page.show_error(f"이름 변경 실패 · 기존 목록 보존 · {error}")
            return
        self._render_watchlists(selected_list_id=list_id)
        self.refresh_watchlist_quotes(list_id)

    @QtCore.Slot(str)
    def _remove_watchlist(self, list_id: str) -> None:
        try:
            self._watchlist_state = self.watchlist_service.remove_list(list_id)
        except (OSError, PermissionError, ValueError, KeyError) as error:
            self.watchlist_page.show_error(f"목록 삭제 실패 · 기존 목록 보존 · {error}")
            return
        self._render_watchlists(selected_list_id=DEFAULT_LIST_ID)
        self.refresh_watchlist_quotes(DEFAULT_LIST_ID)

    @QtCore.Slot(str, int)
    def _move_watchlist(self, list_id: str, offset: int) -> None:
        try:
            self._watchlist_state = self.watchlist_service.move_list(list_id, offset)
        except (OSError, PermissionError, ValueError, KeyError) as error:
            self.watchlist_page.show_error(f"목록 순서 저장 실패 · 기존 목록 보존 · {error}")
            return
        self._render_watchlists(selected_list_id=list_id)
        self.refresh_watchlist_quotes(list_id)

    @QtCore.Slot(str, object, int)
    def _move_watchlist_item(
        self, list_id: str, key: tuple[str, str], offset: int,
    ) -> None:
        try:
            self._watchlist_state = self.watchlist_service.move_item(list_id, key, offset)
        except (OSError, PermissionError, ValueError, KeyError) as error:
            self.watchlist_page.show_error(f"종목 순서 저장 실패 · 기존 목록 보존 · {error}")
            return
        self._render_watchlists(selected_list_id=list_id)
        self.refresh_watchlist_quotes(list_id)

    @QtCore.Slot()
    @QtCore.Slot(str)
    def refresh_watchlist_quotes(self, list_id: str | None = None) -> None:
        selected = list_id or self.watchlist_page.selected_list_id
        try:
            watchlist = self._watchlist_state.list_by_id(selected)
        except StopIteration:
            return
        if not watchlist.items:
            self.watchlist_page.render(self._watchlist_state, (), preserve_selection=True)
            return
        self._request_equity_job("watchlist", tuple(item.identity for item in watchlist.items))

    @QtCore.Slot(object, bool)
    def _open_watchlist_identity(self, identity: EquityIdentity, detached: bool) -> None:
        if detached:
            (
                self.open_detached_us_etf(identity)
                if identity.is_us_etf else self.open_detached_equity(identity)
            )
            return
        page = self.us_etf_page if identity.is_us_etf else self.equity_page
        self.tabs.setCurrentWidget(page)
        page._request_identity(identity)

    @QtCore.Slot()
    def open_detached_index(self) -> DetachedChartWindow:
        window = DetachedChartWindow("index", self.service, self.index_page)
        window.setStyleSheet(self.styleSheet())
        window.closed.connect(self._detached_closed)
        self._detached_windows.add(window)
        window.show()
        return window

    @QtCore.Slot(object)
    def _open_global_identity(self, identity: EquityIdentity) -> None:
        """Route an explicitly selected exact identity through the existing chart read."""

        if not isinstance(identity, EquityIdentity):
            return
        origin = self._global_symbol_origin
        self._global_symbol_origin = None
        if origin is self.research_workspace_page:
            self._open_research_identity(identity)
            return
        self._open_chart_identity(identity)

    @QtCore.Slot(object)
    def _open_chart_identity(self, identity: EquityIdentity) -> None:
        """Route an exact rail/search identity to its ordinary chart page."""

        if not isinstance(identity, EquityIdentity):
            return
        page = self.us_etf_page if identity.is_us_etf else self.equity_page
        self.tabs.setCurrentWidget(page)
        page._request_identity(identity)

    @QtCore.Slot()
    def _open_global_symbol_switcher(self) -> None:
        self._global_symbol_origin = self.tabs.currentWidget()
        self.global_symbol_switcher.open_and_focus()

    @QtCore.Slot(object)
    def _open_research_identity(self, identity: EquityIdentity) -> None:
        """Load one exact identity with its existing local KR/U.S. chart service."""

        if not isinstance(identity, EquityIdentity):
            return
        self.tabs.setCurrentWidget(self.research_workspace_page)
        self.research_workspace_page.begin_identity(identity)
        request = (identity, "120D")
        if identity.is_us_etf:
            self._request_us_etf_job("research_series", request)
        else:
            self._request_equity_job("research_series", request)

    @QtCore.Slot()
    def open_detached_equity(
        self, identity: EquityIdentity | None = None,
    ) -> DetachedChartWindow:
        source = self.equity_page
        if isinstance(identity, EquityIdentity):
            source = IndividualEquityPage()
            source.set_watchlists(self._watchlist_state)
            source.begin_series(identity)
        window = DetachedChartWindow("equity", self.service, source)
        window.setStyleSheet(self.styleSheet())
        window.closed.connect(self._detached_closed)
        window.page.favorite_toggled.connect(self._toggle_watchlist_item)
        window.page.watchlist_item_moved.connect(self._move_watchlist_item)
        window.page.context_identity_open_requested.connect(
            self._open_chart_identity
        )
        window.page.context_watchlist_quotes_requested.connect(
            self.refresh_context_watchlist_quotes
        )
        self._detached_windows.add(window)
        window.show()
        if isinstance(identity, EquityIdentity):
            window.page._request_identity(identity)
        return window

    @QtCore.Slot()
    def open_detached_us_etf(
        self, identity: EquityIdentity | None = None,
    ) -> DetachedChartWindow:
        source = self.us_etf_page
        if isinstance(identity, EquityIdentity):
            if not identity.is_us_etf:
                raise ValueError("U.S. ETF detachment requires a U.S. ETF identity")
            source = IndividualEquityPage(universe="US_ETF")
            source.set_watchlists(self._watchlist_state)
            source.begin_series(identity)
        window = DetachedChartWindow("us_etf", self.service, source)
        window.setStyleSheet(self.styleSheet())
        window.closed.connect(self._detached_closed)
        window.page.favorite_toggled.connect(self._toggle_watchlist_item)
        window.page.watchlist_item_moved.connect(self._move_watchlist_item)
        window.page.context_identity_open_requested.connect(
            self._open_chart_identity
        )
        window.page.context_watchlist_quotes_requested.connect(
            self.refresh_context_watchlist_quotes
        )
        self._detached_windows.add(window)
        window.show()
        if isinstance(identity, EquityIdentity):
            window.page._request_identity(identity)
        return window

    @QtCore.Slot(object)
    def _detached_closed(self, window: DetachedChartWindow) -> None:
        self._detached_windows.discard(window)
        self._schedule_pending_close_check()

    def search_equities(self, query: str) -> None:
        self._request_equity_job("search", query)

    def refresh_equity(self, identity: EquityIdentity, period: str) -> None:
        self._request_equity_job("series", (identity, period))

    def refresh_equity_comparison(self, view: EquitySeriesView) -> None:
        self._request_equity_job("comparison", view)

    def refresh_candidate_scan(self) -> None:
        if self._closing:
            return
        self._candidate_scan_started = True
        self.research_workspace_page.begin_candidate_scan()
        if self._equity_thread is not None:
            self._candidate_scan_pending = True
            return
        self._request_equity_job("candidate_scan", None)

    @QtCore.Slot(int)
    def _candidate_tab_changed(self, _index: int) -> None:
        if (
            not self._candidate_scan_started
            and self.tabs.currentWidget() is self.research_workspace_page
        ):
            self.refresh_candidate_scan()

    @QtCore.Slot(str, str)
    def _open_candidate_symbol(self, market: str, symbol: str) -> None:
        self._request_equity_job("candidate_identity", (market, symbol))

    def search_us_etfs(self, query: str) -> None:
        self._request_us_etf_job("search", query)

    def refresh_us_etf(self, identity: EquityIdentity, period: str) -> None:
        self._request_us_etf_job("series", (identity, period))

    def refresh_us_etf_comparison(self, view: EquitySeriesView) -> None:
        self._request_us_etf_job("comparison", view)

    def _request_equity_job(self, action: str, request: object) -> None:
        if self._closing:
            return
        if self._equity_thread is not None:
            self._equity_pending = (action, request)
            return
        thread = QtCore.QThread(self)
        diagnostic_run_id = new_session_id()
        worker = EquityChartWorker(
            self.candidate_scanner if action == "candidate_scan"
            else self.service if action in {"watchlist", "comparison", "global_search"}
            else self.service.equity,
            action,
            request,
        )
        worker.moveToThread(thread)
        worker.failed.connect(
            lambda error, run_id=diagnostic_run_id: self._record_worker_failure(
                error, run_id=run_id, code="EQUITY_WORKER_FAILED", stage="LOCAL_READ"
            )
        )
        thread.started.connect(worker.run)
        worker.completed.connect(self._equity_loaded)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(
            self._managed_thread_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        thread.destroyed.connect(self._equity_thread_destroyed)
        self._equity_thread = thread
        self._equity_worker = worker
        thread.start()

    @QtCore.Slot(str, object, object)
    def _equity_loaded(self, action: str, request: object, result: object) -> None:
        if self._closing:
            return
        if isinstance(result, Exception):
            if action in {"search", "global_search", "candidate_identity"}:
                query = request[1] if action == "candidate_identity" else request
                result = EquitySearchView(
                    str(query), (), "종목 식별정보를 읽거나 검증할 수 없습니다.",
                )
            elif action == "watchlist":
                result = tuple(
                    WatchlistQuote(
                        identity=identity,
                        price=None,
                        change=None,
                        change_pct=None,
                        reference_kst=None,
                        freshness="READ_FAILURE",
                        unavailable_reason="로컬 가격을 읽거나 검증할 수 없습니다.",
                    )
                    for identity in request
                )
            elif action == "comparison":
                view = request
                result = NormalizedBenchmarkComparisonView.unavailable(
                    view.identity, view.period,
                    "The selected benchmark comparison could not be read or validated.",
                    benchmark_id=view.identity.market,
                    benchmark_label=f"{view.identity.market} (KRX:{view.identity.market})",
                    currency=view.identity.currency or "KRW",
                    target_freshness=view.freshness, target_as_of=view.as_of,
                )
            elif action == "candidate_scan":
                result = self.candidate_scanner.unavailable(
                    "LOCAL_CANDIDATE_SCAN_FAILED"
                )
            else:
                identity, period = request
                result = EquitySeriesView(
                    identity=identity, period=period, frame=pd.DataFrame(),
                    display_state=DashboardDisplayState.UNAVAILABLE,
                    freshness="UNKNOWN", as_of=None, expected_as_of=None,
                    source="local retained data", reference_kst=None,
                    unavailable_reason="선택한 종목의 로컬 가격을 읽거나 검증할 수 없습니다.",
                )
        if action == "global_search" and isinstance(result, EquitySearchView):
            self.global_symbol_switcher.render(result)
        elif action == "candidate_identity" and isinstance(result, EquitySearchView):
            market, symbol = request
            matches = tuple(
                identity for identity in result.matches
                if identity.market == market and identity.symbol == symbol
            )
            if len(matches) == 1:
                self._open_research_identity(matches[0])
            else:
                self.research_workspace_page.candidate_status.setText(
                    "후보 종목의 정확한 로컬 식별정보를 확인하지 못했습니다."
                )
        elif action == "search" and isinstance(result, EquitySearchView):
            self.equity_page.render_search(result)
        elif action == "series" and isinstance(result, EquitySeriesView):
            self.equity_page.render_series(result)
        elif action == "research_series" and isinstance(result, EquitySeriesView):
            self.research_workspace_page.render_series(result)
        elif action == "candidate_scan" and isinstance(result, ExploratoryCandidateView):
            self.research_workspace_page.render_exploratory_candidates(result)
        elif action == "comparison" and isinstance(result, NormalizedBenchmarkComparisonView):
            self.equity_page.render_comparison(result)
        elif action == "watchlist" and isinstance(result, tuple):
            quotes = tuple(
                item for item in result if isinstance(item, WatchlistQuote)
            )
            for page in (self.equity_page, self.us_etf_page):
                page.render_context_watchlist_quotes(tuple(request), quotes)
            for window in tuple(self._detached_windows):
                if window.kind in {"equity", "us_etf"}:
                    window.page.render_context_watchlist_quotes(
                        tuple(request), quotes,
                    )
            try:
                selected = self._watchlist_state.list_by_id(
                    self.watchlist_page.selected_list_id
                )
            except StopIteration:
                return
            request_keys = tuple(identity.key for identity in request)
            if tuple(item.key for item in selected.items) == request_keys:
                self.watchlist_page.render(
                    self._watchlist_state,
                    quotes,
                )

    @QtCore.Slot(object)
    def _equity_thread_destroyed(self, _destroyed: object) -> None:
        self._equity_thread = None
        self._equity_worker = None
        pending = self._equity_pending
        self._equity_pending = None
        candidate_pending = self._candidate_scan_pending
        self._candidate_scan_pending = False
        if candidate_pending and not self._closing:
            self._equity_pending = pending
            QtCore.QTimer.singleShot(0, self.refresh_candidate_scan)
        elif pending is not None and not self._closing:
            QtCore.QTimer.singleShot(
                0, lambda pending=pending: self._request_equity_job(*pending),
            )
        self._schedule_pending_close_check()

    def _request_us_etf_job(self, action: str, request: object) -> None:
        if self._closing:
            return
        if self._us_etf_thread is not None:
            self._us_etf_pending = (action, request)
            return
        thread = QtCore.QThread(self)
        diagnostic_run_id = new_session_id()
        worker = EquityChartWorker(
            self.service if action == "comparison" else self.service.us_etf,
            action, request,
        )
        worker.moveToThread(thread)
        worker.failed.connect(
            lambda error, run_id=diagnostic_run_id: self._record_worker_failure(
                error, run_id=run_id, code="ETF_WORKER_FAILED", stage="LOCAL_READ"
            )
        )
        thread.started.connect(worker.run)
        worker.completed.connect(self._us_etf_loaded)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(
            self._managed_thread_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        thread.destroyed.connect(self._us_etf_thread_destroyed)
        self._us_etf_thread = thread
        self._us_etf_worker = worker
        thread.start()

    @QtCore.Slot(str, object, object)
    def _us_etf_loaded(self, action: str, request: object, result: object) -> None:
        if self._closing:
            return
        if isinstance(result, Exception):
            if action == "search":
                result = EquitySearchView(
                    str(request), (), "The accepted U.S. ETF identity catalog is unavailable.",
                )
            elif action == "comparison":
                view = request
                result = NormalizedBenchmarkComparisonView.unavailable(
                    view.identity, view.period,
                    "U.S. ETF comparison could not be read or validated; no benchmark file was used.",
                    benchmark_id="SP500_OR_NASDAQ100",
                    benchmark_label="S&P 500 (SP500) or Nasdaq-100 (NASDAQ100)",
                    currency="USD", target_freshness=view.freshness, target_as_of=view.as_of,
                )
            else:
                identity, period = request
                result = EquitySeriesView(
                    identity=identity, period=period, frame=pd.DataFrame(),
                    display_state=DashboardDisplayState.UNAVAILABLE,
                    freshness="UNKNOWN", as_of=None, expected_as_of=None,
                    source="accepted local U.S. ETF scope", reference_kst=None,
                    price_mode="provider-native original OHLCV; USD",
                    unavailable_reason=(
                        "The selected U.S. ETF local price scope could not be read or validated."
                    ),
                )
        if action == "search" and isinstance(result, EquitySearchView):
            self.us_etf_page.render_search(result)
        elif action == "series" and isinstance(result, EquitySeriesView):
            self.us_etf_page.render_series(result)
        elif action == "research_series" and isinstance(result, EquitySeriesView):
            self.research_workspace_page.render_series(result)
        elif action == "comparison" and isinstance(result, NormalizedBenchmarkComparisonView):
            self.us_etf_page.render_comparison(result)

    @QtCore.Slot(object)
    def _us_etf_thread_destroyed(self, _destroyed: object) -> None:
        self._us_etf_thread = None
        self._us_etf_worker = None
        pending = self._us_etf_pending
        self._us_etf_pending = None
        if pending is not None and not self._closing:
            QtCore.QTimer.singleShot(
                0, lambda pending=pending: self._request_us_etf_job(*pending),
            )
        self._schedule_pending_close_check()

    def refresh_market_chart(self, asset, period):
        return self._request_local_read("market_chart", (asset, period))

    def _request_backtest_run(self) -> bool:
        return self._start_backtest_job("RUN")

    def _request_backtest_reload(self) -> bool:
        return self._start_backtest_job("RELOAD")

    def _request_backtest_scenario(self) -> bool:
        if self.backtest_scenario_inputs is None:
            self.backtest_page.configure_scenario_available(False)
            return False
        return self._start_backtest_job("SCENARIO")

    def _request_backtest_export(self) -> bool:
        if (
            self._closing
            or self._backtest_close_pending
            or self._accepted_backtest_bundle is None
        ):
            return False
        selected, _selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "검증된 5파일 번들을 내보낼 새 폴더 이름",
            str(self.project_root / "validated-backtest-bundle"),
            "새 폴더 (*)",
        )
        if not selected:
            return False
        return self._start_backtest_job("EXPORT", Path(selected))

    def _start_backtest_job(
        self, action: str, destination: Path | None = None,
    ) -> bool:
        if (
            self._closing
            or self._backtest_close_pending
            or self._backtest_thread is not None
        ):
            return False
        if action not in BacktestRunWorker.ACTIONS:
            raise ValueError("unsupported backtest action")
        if action == "EXPORT" and (
            self._accepted_backtest_bundle is None or destination is None
        ):
            return False

        thread = QtCore.QThread(self)
        diagnostic_run_id = new_session_id()
        worker = BacktestRunWorker(
            self.backtest_service,
            action,
            accepted_bundle=self._accepted_backtest_bundle,
            destination=destination,
            diagnostic_run_id=diagnostic_run_id,
            scenario_service=self.backtest_scenario_service,
            scenario_inputs=self.backtest_scenario_inputs,
        )
        worker.moveToThread(thread)
        worker.failed.connect(
            lambda error, run_id=diagnostic_run_id: self._record_worker_failure(
                error, run_id=run_id, code="BACKTEST_WORKER_FAILED", stage="WORKER"
            )
        )
        thread.started.connect(worker.run)
        worker.completed.connect(self._backtest_job_completed)
        # Schedule QObject destruction while the worker event loop is still
        # alive.  Dropping the last Python worker reference from QThread.finished
        # can otherwise destroy it from the GUI thread and abort Qt.
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(
            self._managed_thread_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        thread.destroyed.connect(self._backtest_thread_destroyed)
        self._backtest_thread = thread
        self._backtest_worker = worker
        self._backtest_action = action
        self.backtest_page.set_workflow_busy(action)
        thread.start()
        return True

    @QtCore.Slot(str, object)
    def _backtest_job_completed(self, action: str, result: object) -> None:
        if (
            self._closing
            or self._backtest_close_pending
            or action != self._backtest_action
        ):
            return
        if isinstance(result, Exception):
            self.backtest_page.set_workflow_failure(action)
            return
        if isinstance(result, _BacktestLegacyReload):
            if self._accepted_backtest_bundle is None:
                try:
                    self.backtest_page.render(result.view)
                except Exception:
                    self.backtest_page.set_workflow_failure("RELOAD")
                    return
                if result.view.artifact_state == "READY":
                    self.backtest_page.set_legacy_fallback()
                    return
            self.backtest_page.set_workflow_failure("RELOAD")
            return
        if action == "SCENARIO":
            try:
                self.backtest_page.render_scenario(result)
            except Exception:
                self.backtest_page.set_workflow_failure(action)
            return
        if action in {"RUN", "RELOAD"}:
            view = getattr(result, "view", None)
            try:
                self.backtest_page.render_validated_bundle(view)
            except Exception:
                self.backtest_page.set_workflow_failure(action)
                return
            self._accepted_backtest_bundle = result
            return
        if action == "EXPORT":
            self.backtest_page.set_export_success()

    @QtCore.Slot(object)
    def _backtest_thread_destroyed(self, _destroyed: object) -> None:
        self._backtest_thread = None
        self._backtest_worker = None
        self._backtest_action = None
        if self._close_pending:
            self._schedule_pending_close_check()
        elif not self._closing:
            self._backtest_close_pending = False
            self.backtest_page.set_workflow_busy(None)

    def refresh_backtest(self) -> bool:
        """Compatibility entry point for the legacy startup refresh."""
        return self._request_backtest_reload()

    def _managed_worker_threads(
        self,
    ) -> tuple[QtCore.QThread | None, ...]:
        return (
            self._local_read_thread,
            self._current_stage_thread,
            self._backtest_thread,
            self._account_thread,
            self._current_observation_thread,
            self._equity_thread,
            self._us_etf_thread,
        )

    def _schedule_pending_close_check(self) -> None:
        if self._close_pending:
            self._close_retry_timer.start(0)

    @QtCore.Slot()
    def _retry_pending_close(self) -> None:
        if not self._close_pending:
            return
        if any(thread is not None for thread in self._managed_worker_threads()):
            self._close_retry_timer.start()
            return
        self.close()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if any(
            thread is not None for thread in self._managed_worker_threads()
        ):
            self._close_pending = True
            self._backtest_close_pending = True
            self._closing = True
            self._local_read_pending.clear()
            self._current_stage_pending = None
            self._candidate_scan_pending = False
            self.backtest_page.set_workflow_close_pending()
            self._schedule_pending_close_check()
            event.ignore()
            return
        self._close_pending = True
        for window in tuple(self._detached_windows):
            window.close()
            if window._close_pending or window.isVisible():
                event.ignore()
                return
        self._close_pending = False
        self._backtest_close_pending = False
        self._close_retry_timer.stop()
        self._closing = True
        self.local_reload_timer.stop()
        self.current_observation_reload_timer.stop()
        try:
            self.local_data_watcher.blockSignals(True)
            watched_directories = self.local_data_watcher.directories()
            if watched_directories:
                self.local_data_watcher.removePaths(watched_directories)
        except (OSError, RuntimeError):
            pass
        self._local_watch_assignments.clear()
        self._detached_windows.clear()
        self._backtest_thread = None
        self._backtest_worker = None
        self._backtest_action = None
        self._account_thread = None
        self._account_worker = None
        self._account_pending_trigger = None
        self._current_observation_thread = None
        self._current_observation_worker = None
        self._local_read_thread = None
        self._local_read_worker = None
        self._local_read_active = None
        self._local_read_pending.clear()
        self._current_stage_thread = None
        self._current_stage_worker = None
        self._current_stage_pending = None
        self._latest_current_stage = None
        self._equity_thread = None
        self._equity_worker = None
        self._equity_pending = None
        self._candidate_scan_pending = False
        self._us_etf_thread = None
        self._us_etf_worker = None
        self._us_etf_pending = None
        dialog = self.dashboard._preferences_dialog
        if dialog is not None:
            dialog.close()
        self._store_dashboard_window_geometry()
        super().closeEvent(event)
