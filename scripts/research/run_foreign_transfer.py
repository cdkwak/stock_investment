"""Run the fixed-design foreign transfer test on retained Parquet only."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow.dataset as pads


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.research.compound_ladder import (  # noqa: E402
    LadderSpec,
    ladder_levels,
    simulate_account,
    simulate_baseline,
    weekly_curve,
    with_baseline_comparison,
)
from stock_data.research.condition_backtest import compute_signals  # noqa: E402
from stock_data.research.foreign_transfer import (  # noqa: E402
    CONFIDENCE_MARKETS,
    FIT_END,
    MARKET_GROUPS,
    annotate_episodes,
    annualized_log_volatility,
    compute_volatility_scale,
    diagnostic_flags,
    independent_observation_proxy,
    normalized_thresholds,
    period_episode_counts,
    restrict_japan_window,
    summarize_episode_classes,
    underperformance_summary,
)
from stock_data.research.leveraged_product import (  # noqa: E402
    load_short_rate,
    retained_manifest_digest,
    synthetic_daily_returns,
)


TRANSACTION_COST = 0.001
RAW_THRESHOLDS = (-0.20, -0.10)
CONFIRMATION_MARKETS = ("TAIEX", "SP500")
US_DIAGNOSTIC_MARKETS = ("SP500", "NASDAQ100")


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if value is pd.NA:
        return None
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_value(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _code_manifest(root: Path) -> tuple[str, list[dict[str, Any]]]:
    paths = (
        Path("src/stock_data/research/compound_ladder.py"),
        Path("src/stock_data/research/foreign_transfer.py"),
        Path("scripts/research/run_foreign_transfer.py"),
    )
    aggregate = hashlib.sha256()
    inventory: list[dict[str, Any]] = []
    for relative in paths:
        payload = (root / relative).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        name = relative.as_posix()
        inventory.append({"path": name, "bytes": len(payload), "sha256": digest})
        aggregate.update(name.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest(), inventory


def _load_symbol_rows(path: Path, symbols: Iterable[str], *, basket: str) -> pd.DataFrame:
    """Read only selected physical symbol rows with partition inference disabled."""

    symbol_list = tuple(symbols)
    dataset = pads.dataset(path, format="parquet", partitioning=None)
    required = {"date", "symbol", "close"}
    missing = required.difference(dataset.schema.names)
    if missing:
        raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
    columns = [name for name in ("date", "symbol", "close", "volume") if name in dataset.schema.names]
    table = dataset.to_table(
        columns=columns,
        filter=pads.field("symbol").isin(symbol_list),
    )
    frame = table.to_pandas()
    if frame.empty:
        raise ValueError(f"{path.name} has no rows for {symbol_list}")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame["series_id"] = frame["symbol"].astype(str)
    frame["basket"] = basket
    frame["close"] = pd.to_numeric(frame["close"], errors="raise").astype("float64")
    if "volume" not in frame:
        frame["volume"] = np.nan
    else:
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    if not np.isfinite(frame["close"]).all() or frame["close"].le(0.0).any():
        raise ValueError(f"{path.name} closes must be finite and positive")
    if frame[["series_id", "date"]].duplicated().any():
        raise ValueError(f"{path.name} contains duplicate symbol/date keys")
    observed = set(frame["series_id"])
    absent = sorted(set(symbol_list).difference(observed))
    if absent:
        raise ValueError(f"{path.name} is missing requested symbols: {absent}")
    return frame[["date", "series_id", "basket", "close", "volume"]].sort_values(
        ["series_id", "date"], kind="mergesort"
    ).reset_index(drop=True)


def load_retained_prices(root: Path) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    global_frame = _load_symbol_rows(
        root / "data/normalized/global_index_price_daily",
        MARKET_GROUPS,
        basket="FOREIGN_TRANSFER",
    )
    korea = _load_symbol_rows(
        root / "data/normalized/kr_index_daily",
        ("KOSPI",),
        basket="KR_REFERENCE",
    )
    markets: dict[str, pd.DataFrame] = {}
    for market in MARKET_GROUPS:
        frame = global_frame.loc[global_frame["series_id"].eq(market)].copy().reset_index(drop=True)
        if market == "NIKKEI225":
            frame = restrict_japan_window(frame)
        if len(frame) < 253:
            raise ValueError(f"{market} has fewer than 253 retained observations")
        markets[market] = frame
    return markets, korea


def _variant(
    frame: pd.DataFrame,
    signals: pd.DataFrame,
    product_returns: pd.Series,
    baseline: Any,
    market: str,
    thresholds: tuple[float, float],
) -> tuple[dict[str, Any], Any, pd.DataFrame]:
    spec = LadderSpec(
        drawdown_threshold=thresholds[0],
        disp60_threshold=thresholds[1],
        levels=2,
        base_exposure=1.0,
    )
    levels = ladder_levels(signals, spec)["executable_level"]
    strategy = simulate_account(
        frame["date"],
        product_returns,
        levels,
        underlying_returns=frame["close"].pct_change(fill_method=None).fillna(0.0),
        spec=spec,
        leverage_multiple=2,
        exit_variant="a",
        transaction_cost=TRANSACTION_COST,
        baseline_curve=baseline.curve,
    )
    cycles = annotate_episodes(strategy.cycles, market)
    compared = with_baseline_comparison(strategy.metrics, baseline.metrics)
    periods: dict[str, Any] = {}
    for period in ("fit", "holdout", "full"):
        row = dict(compared[period])
        row["baseline_max_drawdown"] = baseline.metrics[period]["max_drawdown"]
        row["episodes"] = period_episode_counts(cycles, period)
        periods[period] = row
    payload = {
        "thresholds": {"drawdown252": thresholds[0], "disp60": thresholds[1]},
        "fit": periods["fit"],
        "holdout": periods["holdout"],
        "full": periods["full"],
        "episode_classes": summarize_episode_classes(cycles),
        "cycles": cycles.to_dict("records"),
    }
    return payload, strategy, cycles


def _weekly_payload(market: str, normalized: Any, raw: Any, baseline: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "market": market,
        "frequency": "weekly_W_FRI_plus_final_observation",
        "normalized_rule": weekly_curve(normalized.curve),
        "raw_rule": weekly_curve(raw.curve),
        "baseline_buy_and_hold": weekly_curve(baseline.curve),
    }


def _fmt_multiple(value: Any) -> str:
    return "해당 없음" if value is None else f"{float(value):.3f}x"


def _fmt_pct(value: Any) -> str:
    return "해당 없음" if value is None else f"{float(value) * 100:.1f}%"


def _fmt_hit(value: Any) -> str:
    return "해당 없음" if value is None else f"{float(value) * 100:.1f}%"


def _vote(value: Any) -> str:
    if value is None:
        return "해당 없음"
    return f"{'예' if float(value) > 1.0 else '아니오'} ({float(value):.3f}x)"


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    header = list(headers)
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _report(summary: dict[str, Any]) -> str:
    markets = summary["markets"]
    vote_rows = []
    raw_vote_rows = []
    period_rows = []
    episode_rows = []
    scale_rows = [("KOSPI", "기준", f"{summary['korea_fit_sigma']:.4f}", "1.000", "-0.200", "-0.100")]
    for market, item in markets.items():
        headline = item["normalized"]
        raw = item["raw"]
        vote_rows.append((
            market,
            item["group"],
            _vote(headline["fit"]["relative_to_baseline"]),
            _vote(headline["holdout"]["relative_to_baseline"]),
            _vote(headline["full"]["relative_to_baseline"]),
        ))
        raw_vote_rows.append((
            market,
            _fmt_multiple(raw["fit"]["relative_to_baseline"]),
            _fmt_multiple(raw["holdout"]["relative_to_baseline"]),
            _fmt_multiple(raw["full"]["relative_to_baseline"]),
        ))
        scale_rows.append((
            market,
            item["group"],
            f"{item['fit_sigma']:.4f}",
            f"{item['volatility_scale']:.3f}",
            f"{headline['thresholds']['drawdown252']:.3f}",
            f"{headline['thresholds']['disp60']:.3f}",
        ))
        for period in ("fit", "holdout", "full"):
            metric = headline[period]
            counts = metric["episodes"]
            period_rows.append((
                market,
                period,
                _fmt_multiple(metric["final_wealth_multiple"]),
                _fmt_multiple(metric["baseline_final_wealth_multiple"]),
                _fmt_multiple(metric["relative_to_baseline"]),
                _fmt_pct(metric["max_drawdown"]),
                _fmt_pct(metric["baseline_max_drawdown"]),
                f"{counts['total']} / {counts['synchronous']} / {counts['idiosyncratic']}",
            ))
        for episode_class in ("all", "synchronous", "idiosyncratic"):
            row = headline["episode_classes"][episode_class]
            episode_rows.append((
                market,
                episode_class,
                row["episodes"],
                _fmt_hit(row["hit_rate_vs_baseline"]),
                _fmt_multiple(row["strategy_contribution_multiple"]),
                _fmt_multiple(row["baseline_contribution_multiple"]),
                _fmt_multiple(row["relative_contribution_multiple"]),
            ))

    japan = summary["japan_warning"]
    loss_rows = []
    for comparator in ("baseline", "cash"):
        row = japan["underperformance"][comparator]
        span = row["longest_below"]
        loss_rows.append((
            "1x 무조건 보유" if comparator == "baseline" else "현금 1.0",
            _fmt_pct(row["ending_shortfall"]),
            _fmt_pct(row["worst_shortfall"]),
            span["sessions"],
            span["calendar_days"],
            f"{span['start']}..{span['end']}" if span["start"] else "없음",
        ))
    flag_rows = []
    for name, row in japan["diagnostic_flags"].items():
        false = summary["diagnostic_false_flags"]
        flag_rows.append((
            name,
            row["first_flag_date"] or "없음",
            row["count"],
            false["KOSPI"][name]["count"],
            false["SP500"][name]["count"],
            false["NASDAQ100"][name]["count"],
        ))
    independent = summary["independent_observations"]
    lines = [
        "> This test can only add confidence; a failure does not falsify the rule. (이 테스트는 신뢰를 추가할 수만 있으며, 실패는 규칙을 반증하지 않는다.)",
        "",
        "# 해외 전이 복리 사다리 백테스트 결과",
        "",
        "> 고정 설계: `kr_dd_ladder_2`, 2단계, 일일 재설정 synthetic 2x, exit a, 편도 0.10% 비용, T 신호→다음 retained session 종가 실행. 숫자를 본 뒤 시장군·임계값·정규화 방식을 바꾸지 않았다.",
        "",
        "## 판정표 — 변동성 정규화가 헤드라인",
        "",
        _table(("시장", "사전 고정 그룹", "fit: 자기 기준선을 이겼나", "hold-out: 자기 기준선을 이겼나", "full: 자기 기준선을 이겼나"), vote_rows),
        "",
        "NASDAQ100은 미국 보조선이며 확인용 표결 수에는 넣지 않는다. 보너스 시장의 실패는 확인용 실패로 세지 않는다. 보너스군의 사전 이유는 장기 상승 추세가 약했다는 점이며, STOXX 600은 2000년 고점을 2021년에야 회복했다. 확인용 시장이 둘뿐이므로 애매한 결과가 정상이며 최종 판단은 전방검증이 한다.",
        "",
        "## 원시 임계값 보조 열",
        "",
        _table(("시장", "fit raw/기준", "hold-out raw/기준", "full raw/기준"), raw_vote_rows),
        "",
        "원시 임계값은 `drawdown252≤-0.20`, `disp60≤-0.10` 한 가지만 보조로 실행했다. 정규화 대안은 추가하지 않았다.",
        "",
        "## FIT 변동성 배율과 적용 임계값",
        "",
        _table(("시장", "그룹", "FIT 연환산 σ", "σ시장/σ한국", "적용 drawdown252", "적용 disp60"), scale_rows),
        "",
        "각 σ는 해당 시장 retained 시작일부터 2015-12-31까지의 일별 로그수익률 표준편차로 한 번만 계산했다. 한국 분모는 KOSPI의 retained 시작일부터 같은 FIT 종료일까지다. clamp는 각각 `[-0.60,-0.05]`, `[-0.30,-0.03]`이다.",
        "",
        "## 시장별 헤드라인 계좌",
        "",
        _table(("시장", "기간", "규칙 최종배수", "1x 기준선", "규칙/기준", "규칙 MDD", "기준 MDD", "episode 총/동시/고유"), period_rows),
        "",
        "## episode 표본과 복리 기여",
        "",
        _table(("시장", "분류", "episode", "기준선 초과 hit rate", "규칙 episode 복리", "기준 episode 복리", "상대 복리"), episode_rows),
        "",
        "hit는 해당 episode의 규칙 계좌 기여가 같은 날짜 구간의 1x 기준선 기여보다 큰 경우다. 분류별 복리 기여는 그 분류의 episode 변화율을 이어 곱한 진단치이며 별도 독립 포트폴리오가 아니다.",
        "",
        f"사전 창 기준 신뢰 추가 집합 5개 시장의 {independent['confidence_set']['episode_rows']}개 episode 행은 고유 {independent['confidence_set']['idiosyncratic_episodes']}개와 서로 다른 동시 창 {independent['confidence_set']['distinct_synchronous_windows']}개로 압축된다. 따라서 이 해외 시험이 추가한 독립 관측의 보수적 proxy는 **{independent['confidence_set']['independent_proxy']}개**다. 이는 통계적 독립성의 증명이 아니라 미리 정한 공통 충격 중복 제거 수치다. 일본 경고 표본까지 포함하면 proxy는 {independent['including_japan']['independent_proxy']}개다.",
        "",
        "## 규칙이 망가지는 조건 — 일본 1990-01-01..2012-12-31",
        "",
        _table(("비교 대상", "종료 시 열위", "최대 열위", "최장 열위 sessions", "달력일", "구간"), loss_rows),
        "",
        f"규칙 계좌 MDD는 {_fmt_pct(japan['account_max_drawdown'])}였다. 기준선보다 못한 실패 episode는 {japan['failed_vs_baseline_count']}개였고, 계좌 기여가 음수여서 현금보다 못한 episode는 {japan['negative_episode_count']}개였다.",
        "",
        "장기 상승 전제 경고 후보는 사전에 `N=252 retained sessions`, 회복=`drawdown252≥-5%`, `M=120 retained sessions`로 고정했다. 새 지표를 만들지 않고 `compute_signals`의 `high252`, `drawdown252`, `disp60`만 사용했다.",
        "",
        _table(("후보", "일본 최초 경고", "일본 횟수", "KOSPI false", "SP500 false", "NASDAQ100 false"), flag_rows),
        "",
        f"일본에서 가장 먼저 켜진 신호는 **{japan['earliest_flag']['candidate']}** ({japan['earliest_flag']['date']})였다. 한국·미국은 이 기간 뒤에도 장기 상승 전제가 유지된 비교군으로 보고 표의 발생 횟수를 false flag로 셌다.",
        "",
        "## 한계",
        "",
        "- 환율을 변환하지 않은 현지통화 지수 비교다.",
        "- 실제 해외 레버리지 ETF가 아니라 일일 재설정 synthetic 2x 상품이다.",
        "- 대만 이력은 1997년에 시작하므로 FIT 구간이 짧다.",
        "- retained 지수 종가의 당시 빈티지와 공개시각을 완전히 재현하지 못하므로 원천 가격 자체의 역사적 PIT 안전성을 주장하지 않는다.",
        "- 2016년 분할은 이 연구에 미리 고정된 설명용 hold-out이며, Phase-1의 별도 sealed final holdout을 모델 선택에 사용한 것이 아니다.",
        "- 독립 관측 수는 사전 위기창 중복만 제거한 보수적 proxy이며 시장 간 잔여 상관을 제거한 통계량은 아니다.",
        "",
        f"입력 manifest: `{summary['input_manifest_sha256']}` · 코드 manifest: `{summary['code_manifest_sha256']}` · API calls: `0` · 실행 모드: `{'quick' if summary['quick'] else 'full'}`",
        "",
    ]
    return "\n".join(lines)


def run(project_root: Path, *, quick: bool) -> dict[str, Any]:
    started = time.perf_counter()
    root = project_root.resolve()
    output = root / "artifacts/research/foreign_transfer"
    manifest_paths = [
        Path("data/normalized/global_index_price_daily"),
        Path("data/normalized/kr_index_daily"),
        *[path.relative_to(root) for path in sorted((root / "data/normalized").glob("fred_*"))],
    ]
    input_digest_before, input_manifest_before = retained_manifest_digest(root, manifest_paths)
    price_frames, korea = load_retained_prices(root)
    korea_fit = korea.loc[korea["date"].le(FIT_END)]
    korea_sigma = annualized_log_volatility(korea_fit["close"])
    results: dict[str, Any] = {}
    strategies: dict[str, Any] = {}
    signals_by_market: dict[str, pd.DataFrame] = {}
    cycles_by_market: dict[str, pd.DataFrame] = {}
    short_rate_sources: set[str] = set()

    for market, frame in price_frames.items():
        fit = frame.loc[frame["date"].le(FIT_END)]
        if len(fit) < 3:
            raise ValueError(f"{market} has insufficient FIT observations")
        sigma = annualized_log_volatility(fit["close"])
        scale = compute_volatility_scale(fit["close"], korea_fit["close"])
        thresholds = normalized_thresholds(scale)
        signals = compute_signals(frame)
        underlying_returns = frame["close"].pct_change(fill_method=None).fillna(0.0)
        short_rate = load_short_rate(root, frame["date"])
        short_rate_sources.add(short_rate.source)
        rate = pd.Series(short_rate.annual_rate.to_numpy(), index=frame.index)
        product_returns = synthetic_daily_returns(
            frame["close"], leverage_multiple=2, annual_short_rate=rate
        )
        baseline = simulate_baseline(
            frame["date"], underlying_returns, transaction_cost=TRANSACTION_COST
        )
        normalized, normalized_strategy, normalized_cycles = _variant(
            frame, signals, product_returns, baseline, market, thresholds
        )
        raw, raw_strategy, _ = _variant(
            frame, signals, product_returns, baseline, market, RAW_THRESHOLDS
        )
        equity_path = output / f"equity_{_slug(market)}.json"
        _write_json(
            equity_path,
            _weekly_payload(market, normalized_strategy, raw_strategy, baseline),
        )
        results[market] = {
            "group": MARKET_GROUPS[market],
            "coverage": {
                "start": frame["date"].iloc[0].strftime("%Y-%m-%d"),
                "end": frame["date"].iloc[-1].strftime("%Y-%m-%d"),
                "observations": len(frame),
                "fit_observations": len(fit),
            },
            "fit_sigma": sigma,
            "volatility_scale": scale,
            "normalized": normalized,
            "raw": raw,
            "short_rate_source": short_rate.source,
            "equity_curve_path": equity_path.relative_to(root).as_posix(),
        }
        strategies[market] = (normalized_strategy, baseline)
        signals_by_market[market] = signals
        cycles_by_market[market] = normalized_cycles

    korea_signals = compute_signals(korea)
    korea_returns = korea["close"].pct_change(fill_method=None).fillna(0.0)
    korea_rate = load_short_rate(root, korea["date"])
    short_rate_sources.add(korea_rate.source)
    korea_product = synthetic_daily_returns(
        korea["close"],
        leverage_multiple=2,
        annual_short_rate=pd.Series(korea_rate.annual_rate.to_numpy(), index=korea.index),
    )
    korea_baseline = simulate_baseline(korea["date"], korea_returns, transaction_cost=TRANSACTION_COST)
    _, _, korea_cycles = _variant(
        korea, korea_signals, korea_product, korea_baseline, "KOSPI", RAW_THRESHOLDS
    )
    diagnostic_false_flags = {
        "KOSPI": diagnostic_flags(korea_signals, korea_cycles),
        **{
            market: diagnostic_flags(signals_by_market[market], cycles_by_market[market])
            for market in US_DIAGNOSTIC_MARKETS
        },
    }

    japan_strategy, japan_baseline = strategies["NIKKEI225"]
    japan_cycles = cycles_by_market["NIKKEI225"]
    japan_flags = diagnostic_flags(signals_by_market["NIKKEI225"], japan_cycles)
    dated_flags = [
        (row["first_flag_date"], name)
        for name, row in japan_flags.items()
        if row["first_flag_date"] is not None
    ]
    earliest_date, earliest_name = min(dated_flags) if dated_flags else (None, "없음")
    failed = japan_cycles.loc[
        pd.to_numeric(japan_cycles["contribution_to_wealth"], errors="raise")
        .le(pd.to_numeric(japan_cycles["baseline_contribution"], errors="raise"))
    ]
    negative = japan_cycles.loc[
        pd.to_numeric(japan_cycles["contribution_to_wealth"], errors="raise").lt(0.0)
    ]
    japan_warning = {
        "window": {"start": "1990-01-01", "end": "2012-12-31"},
        "underperformance": underperformance_summary(japan_strategy.curve, japan_baseline.curve),
        "account_max_drawdown": japan_strategy.metrics["full"]["max_drawdown"],
        "failed_vs_baseline_count": len(failed),
        "failed_vs_baseline_episodes": failed.to_dict("records"),
        "negative_episode_count": len(negative),
        "negative_episodes": negative.to_dict("records"),
        "diagnostic_flags": japan_flags,
        "earliest_flag": {"candidate": earliest_name, "date": earliest_date},
    }

    input_digest_after, input_manifest_after = retained_manifest_digest(root, manifest_paths)
    if input_digest_after != input_digest_before or input_manifest_after != input_manifest_before:
        raise RuntimeError("retained input manifest changed during the experiment")
    code_digest, code_manifest = _code_manifest(root)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "foreign-transfer/v1-fixed-20260905",
        "development_only": True,
        "asymmetric_reading": "This test can only add confidence; failure does not falsify the rule.",
        "api_calls": 0,
        "retained_parquet_only": True,
        "quick": quick,
        "quick_semantics": "The fixed six-market design is unchanged; --quick is an interface-compatible deterministic run flag.",
        "fit_window": {"end": "2015-12-31"},
        "holdout_window": {"start": "2016-01-01"},
        "rule": {
            "levels": 2,
            "leverage_multiple": 2,
            "base_exposure": 1.0,
            "exit": "a",
            "transaction_cost_one_way": TRANSACTION_COST,
            "raw_thresholds": {"drawdown252": -0.20, "disp60": -0.10},
            "normalization": "fit annualized daily-log-return sigma market / KOSPI, fixed clamp",
        },
        "excluded_markets": {"HANG_SENG": "politics/regulation dominate after 2019; not loaded or simulated"},
        "korea_fit_sigma": korea_sigma,
        "markets": results,
        "independent_observations": {
            "confirmation_only": independent_observation_proxy(cycles_by_market, CONFIRMATION_MARKETS),
            "confidence_set": independent_observation_proxy(cycles_by_market, CONFIDENCE_MARKETS),
            "including_japan": independent_observation_proxy(cycles_by_market, MARKET_GROUPS),
        },
        "japan_warning": japan_warning,
        "diagnostic_false_flags": diagnostic_false_flags,
        "short_rate_sources": sorted(short_rate_sources),
        "input_manifest_sha256": input_digest_after,
        "input_manifest": input_manifest_after,
        "code_manifest_sha256": code_digest,
        "code_manifest": code_manifest,
    }
    serializable = _json_value(summary)
    _write_json(output / "summary.json", serializable)
    _write_text(
        root / "docs/research/RESULTS_20260905_foreign_transfer.md",
        _report(serializable),
    )
    serializable["runtime_seconds"] = time.perf_counter() - started
    return serializable


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fixed-design retained-Parquet foreign transfer backtest"
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run the same fixed design with a quick-mode receipt flag",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    summary = run(args.project_root, quick=args.quick)
    print("market | group | fit ratio | holdout ratio | full ratio")
    for market, item in summary["markets"].items():
        normalized = item["normalized"]
        print(
            f"{market} | {item['group']} | "
            f"{_fmt_multiple(normalized['fit']['relative_to_baseline'])} | "
            f"{_fmt_multiple(normalized['holdout']['relative_to_baseline'])} | "
            f"{_fmt_multiple(normalized['full']['relative_to_baseline'])}"
        )
    print(f"runtime_seconds={summary['runtime_seconds']:.3f}")
    print("FOREIGN_TRANSFER_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
