"""Run the retained-Parquet core-ammunition drawdown study."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.dataset as pads


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.research.compound_ladder import (  # noqa: E402
    LadderSpec,
    ladder_levels,
    require_base_exposure,
    require_disp60_threshold,
    require_drawdown_threshold,
    require_levels,
    require_product_share_at_max,
)
from stock_data.research.condition_backtest import compute_signals  # noqa: E402
from stock_data.research.core_ammunition import (  # noqa: E402
    Episode,
    FOLLOWUP_HORIZONS,
    aggregate_values,
    cash_proxy_returns,
    classify_asset,
    cluster_level_two,
    duration_proxy_returns,
    fixed_crisis_types,
    measure_asset_episode,
    measure_asset_horizons,
    peak_after_episode,
    prepare_value_series,
    quantile_values,
    returns_to_nav,
)


INPUT_DATASETS = (
    "kr_index_daily",
    "global_index_price_daily",
    "fred_treasury_yield_daily",
    "fred_treasury_yield_ext_daily",
    "global_etf_price_daily",
    "global_commodity_futures_daily",
    "fred_usd_fx_daily",
)
YIELD_ASSETS = {
    "treasury_2y_proxy": ("미 국채 2Y 프록시", "dgs2", 1.9, "fred_treasury_yield_daily"),
    "treasury_3y_proxy": ("미 국채 3Y 프록시", "dgs3", 2.8, "fred_treasury_yield_ext_daily"),
    "treasury_5y_proxy": ("미 국채 5Y 프록시", "dgs5", 4.5, "fred_treasury_yield_ext_daily"),
    "treasury_10y_proxy": ("미 국채 10Y 프록시", "dgs10", 8.5, "fred_treasury_yield_daily"),
    "treasury_30y_proxy": ("미 국채 30Y 프록시", "dgs30", 18.0, "fred_treasury_yield_daily"),
}
TENOR_IDS = (
    "cash_3m_proxy",
    "treasury_2y_proxy",
    "treasury_3y_proxy",
    "treasury_5y_proxy",
    "treasury_10y_proxy",
    "treasury_30y_proxy",
)
ETF_ASSETS = {
    "shy": ("SHY (1–3Y ETF)", "SHY"),
    "ief": ("IEF (7–10Y ETF)", "IEF"),
    "tlt": ("TLT (20Y+ ETF)", "TLT"),
    "sgov": ("SGOV (0–3M ETF)", "SGOV"),
    "reit_vnq": ("리츠 (VNQ)", "VNQ"),
}
SPLIT_LABELS = ("전체", "경기침체형", "인플레형")
CYCLE_BUCKETS = (
    "1997–98 외환위기 (KR)",
    "2000–02 닷컴",
    "2008–09 금융위기",
    "2011 (EU/미국 신용등급)",
    "2015–16",
    "2018",
    "2020 코로나",
    "2022 인플레",
    "2025–26",
)
FOLLOWUP_ASSETS = (
    "treasury_30y_proxy",
    "tlt",
    "treasury_2y_proxy",
    "cash_3m_proxy",
)


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
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
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return None if pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d")
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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    pd.DataFrame(_json_value(rows)).to_csv(temporary, index=False, encoding="utf-8", lineterminator="\n")
    temporary.replace(path)


def _read_dataset(root: Path, name: str, columns: tuple[str, ...]) -> pd.DataFrame:
    path = root / "data/normalized" / name
    if not path.exists():
        raise FileNotFoundError(f"retained dataset is missing: {path}")
    dataset = pads.dataset(path, format="parquet", partitioning=None)
    missing = set(columns).difference(dataset.schema.names)
    if missing:
        raise ValueError(f"{name} is missing columns: {sorted(missing)}")
    return dataset.to_table(columns=list(columns)).to_pandas()


def _manifest(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for dataset_name in INPUT_DATASETS:
        dataset_root = root / "data/normalized" / dataset_name
        files = sorted(dataset_root.rglob("*.parquet"), key=lambda path: path.as_posix())
        if not files:
            raise FileNotFoundError(f"no retained Parquet files: {dataset_root}")
        for path in files:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append(
                {
                    "dataset": dataset_name,
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": digest,
                }
            )
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": 1,
        "partitioning": None,
        "file_count": len(entries),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "files": entries,
    }


def _index_frame(raw: pd.DataFrame, symbol: str, basket: str) -> pd.DataFrame:
    frame = raw.loc[raw["symbol"].astype(str).eq(symbol), ["date", "close", "volume"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame["series_id"] = symbol
    frame["basket"] = basket
    frame = frame.sort_values("date", kind="mergesort").reset_index(drop=True)
    if frame.empty or frame["date"].duplicated().any() or frame["close"].le(0).any():
        raise ValueError(f"invalid retained index series: {symbol}")
    return frame[["date", "series_id", "basket", "close", "volume"]]


def _episode_inputs(
    root: Path,
    quick: bool,
    *,
    drawdown_threshold: float | None = None,
    disp60_threshold: float | None = None,
    product_share_at_max: float | None = None,
    levels: int | None = None,
    base_exposure: float | None = None,
) -> tuple[list[Episode], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    decided_drawdown = require_drawdown_threshold(drawdown_threshold)
    decided_disp60 = require_disp60_threshold(disp60_threshold)
    decided_share = require_product_share_at_max(product_share_at_max)
    decided_levels = require_levels(levels)
    decided_base_exposure = require_base_exposure(base_exposure)
    kr_raw = _read_dataset(root, "kr_index_daily", ("date", "symbol", "close", "volume"))
    global_raw = _read_dataset(
        root, "global_index_price_daily", ("date", "symbol", "close", "volume")
    )
    frames = {
        "KR": _index_frame(kr_raw, "KOSPI", "KR"),
        "US": _index_frame(global_raw, "NASDAQ100", "US_TECH"),
    }
    starts = {"KR": "1990-01-01", "US": "1985-01-01"}
    series_ids = {"KR": "KOSPI", "US": "NASDAQ100"}
    ladders: dict[str, pd.DataFrame] = {}
    episodes: list[Episode] = []
    spec = LadderSpec(
        drawdown_threshold=decided_drawdown,
        disp60_threshold=decided_disp60,
        product_share_at_max=decided_share,
        levels=decided_levels,
        base_exposure=decided_base_exposure,
    )
    for market in ("KR", "US"):
        signals = compute_signals(frames[market])
        ladder = ladder_levels(signals, spec)
        ladders[market] = ladder
        selected = cluster_level_two(
            ladder,
            market=market,
            series_id=series_ids[market],
            start_date=starts[market],
        )
        episodes.extend(selected[-3:] if quick else selected)
    episodes.sort(key=lambda item: (item.signal_date, item.market))
    return episodes, frames, ladders


def _yield_nav(frame: pd.DataFrame, column: str, duration: float | None) -> pd.Series:
    ordered = frame[["date", column]].copy()
    ordered["date"] = pd.to_datetime(ordered["date"], errors="raise").dt.normalize()
    ordered = ordered.sort_values("date", kind="mergesort").drop_duplicates("date", keep="last")
    returns = (
        cash_proxy_returns(ordered[column])
        if duration is None
        else duration_proxy_returns(ordered[column], duration)
    )
    return prepare_value_series(ordered["date"], returns_to_nav(returns))


def _assets(root: Path) -> tuple[dict[str, dict[str, Any]], pd.Series]:
    treasury = _read_dataset(root, "fred_treasury_yield_daily", ("date", "dgs2", "dgs10", "dgs30"))
    treasury_ext = _read_dataset(root, "fred_treasury_yield_ext_daily", ("date", "dgs3", "dgs5", "dtb3"))
    sources = {
        "fred_treasury_yield_daily": treasury,
        "fred_treasury_yield_ext_daily": treasury_ext,
    }
    assets: dict[str, dict[str, Any]] = {
        "cash_3m_proxy": {
            "label": "3M 현금 프록시 (DTB3)",
            "kind": "cash_proxy",
            "currency": "USD",
            "source": "fred_treasury_yield_ext_daily:dtb3",
            "values": _yield_nav(treasury_ext, "dtb3", None),
        }
    }
    for asset_id, (label, column, duration, source_name) in YIELD_ASSETS.items():
        assets[asset_id] = {
            "label": label,
            "kind": "duration_proxy",
            "currency": "USD",
            "source": f"{source_name}:{column}",
            "duration": duration,
            "values": _yield_nav(sources[source_name], column, duration),
        }

    etfs = _read_dataset(
        root,
        "global_etf_price_daily",
        ("date", "symbol", "close", "adjusted_close", "currency"),
    )
    for asset_id, (label, symbol) in ETF_ASSETS.items():
        part = etfs.loc[etfs["symbol"].astype(str).eq(symbol)].copy()
        adjusted = pd.to_numeric(part["adjusted_close"], errors="coerce")
        part["total_return_value"] = adjusted.where(adjusted.gt(0), pd.to_numeric(part["close"], errors="coerce"))
        assets[asset_id] = {
            "label": label,
            "kind": "real_etf_crosscheck" if asset_id != "reit_vnq" else "reit",
            "currency": "USD",
            "source": f"global_etf_price_daily:{symbol}:adjusted_close",
            "values": prepare_value_series(part["date"], part["total_return_value"]),
        }

    commodity = _read_dataset(
        root, "global_commodity_futures_daily", ("date", "symbol", "source_ticker", "close")
    )
    gold = commodity.loc[
        commodity["symbol"].astype(str).eq("GOLD")
        & commodity["source_ticker"].astype(str).eq("GC=F")
    ].copy()
    assets["gold"] = {
        "label": "금 (GC=F)",
        "kind": "vendor_continuous_future",
        "currency": "USD",
        "source": "global_commodity_futures_daily:GOLD/GC=F",
        "values": prepare_value_series(gold["date"], gold["close"]),
    }

    fx = _read_dataset(root, "fred_usd_fx_daily", ("date", "dexkous"))
    fx_values = prepare_value_series(fx["date"], fx["dexkous"])
    assets["usdkrw"] = {
        "label": "USD/KRW (USD 현금의 KRW 가치)",
        "kind": "fx",
        "currency": "KRW per USD",
        "source": "fred_usd_fx_daily:dexkous",
        "values": fx_values,
    }
    return assets, fx_values


def _measure(
    episodes: list[Episode],
    frames: dict[str, pd.DataFrame],
    ladders: dict[str, pd.DataFrame],
    assets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for episode in episodes:
        equity = prepare_value_series(frames[episode.market]["date"], frames[episode.market]["close"])
        ladder = ladders[episode.market]
        window_start = pd.Timestamp(ladder.at[max(0, episode.signal_index - 60), "date"])
        for asset_id, metadata in assets.items():
            row = measure_asset_episode(
                metadata["values"],
                episode,
                equity,
                window_start_date=window_start,
            )
            row.update(
                {
                    "asset_id": asset_id,
                    "asset": metadata["label"],
                    "asset_kind": metadata["kind"],
                    "currency": metadata["currency"],
                    "source": metadata["source"],
                }
            )
            if asset_id == "usdkrw":
                for label in ("t", "20", "60"):
                    value = row[f"value_{label}"]
                    row[f"local_usd_value_{label}"] = 100.0 if value is not None else None
                    row[f"fx_move_{label}"] = value / 100.0 - 1.0 if value is not None else None
                    row[f"krw_value_{label}"] = value
            records.append(row)
    return records


def _asset_summaries(records: list[dict[str, Any]], assets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame(records)
    output: dict[str, Any] = {}
    for asset_id, metadata in assets.items():
        rows = frame.loc[frame["asset_id"].eq(asset_id)].copy()
        aggregates: dict[str, Any] = {}
        for split in SPLIT_LABELS:
            part = rows if split == "전체" else rows.loc[rows["cycle_type"].eq(split)]
            aggregates[split] = aggregate_values(part)
        output[asset_id] = {
            "asset_id": asset_id,
            "asset": metadata["label"],
            "kind": metadata["kind"],
            "currency": metadata["currency"],
            "source": metadata["source"],
            "coverage": {
                "start": metadata["values"].index.min(),
                "end": metadata["values"].index.max(),
                "observations": len(metadata["values"]),
            },
            "aggregate": aggregates,
            "classification": classify_asset(rows),
            "episodes": rows.to_dict("records"),
        }
    return output


def _tenor_table(records: list[dict[str, Any]], episodes: list[Episode]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(records)
    rows: list[dict[str, Any]] = []
    keys: list[tuple[str, str, str]] = []
    for episode in episodes:
        key = (episode.market, episode.cycle, episode.cycle_type)
        if key not in keys:
            keys.append(key)
    for market, cycle, cycle_type in keys:
        part = frame.loc[frame["market"].eq(market) & frame["cycle"].eq(cycle)]
        row: dict[str, Any] = {
            "market": market,
            "cycle": cycle,
            "cycle_type": cycle_type,
            "episode_count": int(part["episode_id"].nunique()),
            "signal_dates": sorted(part["signal_date"].drop_duplicates().tolist()),
        }
        for asset_id in TENOR_IDS:
            values = pd.to_numeric(
                part.loc[part["asset_id"].eq(asset_id), "value_t"], errors="coerce"
            ).dropna()
            row[asset_id] = float(values.median()) if len(values) else None
        rows.append(row)
    return rows


def _prediction_scorecard(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(records)
    non_2022 = frame.loc[frame["cycle_type"].ne("인플레형")]
    order = {"오판": 0, "부분 적중": 1, "적중": 2, "채점 제외": 3}
    scorecard: list[dict[str, Any]] = []

    def direction_row(asset_id: str, prediction: str) -> dict[str, Any]:
        values = pd.to_numeric(
            non_2022.loc[non_2022["asset_id"].eq(asset_id), "value_t"], errors="coerce"
        ).dropna()
        share = float(values.ge(100).mean()) if len(values) else None
        verdict = "적중" if share is not None and share > 0.50 else "오판"
        return {
            "prediction": prediction,
            "verdict": verdict,
            "evidence": f"2022 제외 T≥100 비율 {share:.1%} ({int(values.ge(100).sum())}/{len(values)})" if share is not None else "자료 없음",
        }

    proxy = pd.to_numeric(
        non_2022.loc[non_2022["asset_id"].eq("treasury_30y_proxy"), "value_t"],
        errors="coerce",
    ).dropna()
    real_tlt = pd.to_numeric(
        non_2022.loc[non_2022["asset_id"].eq("tlt"), "value_t"], errors="coerce"
    ).dropna()
    proxy_share = float(proxy.ge(100).mean()) if len(proxy) else None
    real_share = float(real_tlt.ge(100).mean()) if len(real_tlt) else None
    tlt_checks = [share is not None and share > 0.50 for share in (proxy_share, real_share)]
    scorecard.append(
        {
            "prediction": "TLT: 오를 것",
            "verdict": "적중" if all(tlt_checks) else "부분 적중" if any(tlt_checks) else "오판",
            "evidence": (
                f"2022 제외 30Y 프록시 T≥100 {proxy_share:.1%} ({int(proxy.ge(100).sum())}/{len(proxy)}); "
                f"실제 TLT 겹침 {real_share:.1%} ({int(real_tlt.ge(100).sum())}/{len(real_tlt)})"
                if proxy_share is not None and real_share is not None
                else "자료 없음"
            ),
        }
    )

    reit = non_2022.loc[non_2022["asset_id"].eq("reit_vnq")].copy()
    t_values = pd.to_numeric(reit["value_t"], errors="coerce").dropna()
    valid = reit[["value_t", "value_60", "equity_value_t", "equity_value_60"]].apply(
        pd.to_numeric, errors="coerce"
    ).dropna()
    fall_share = float(t_values.lt(100).mean()) if len(t_values) else None
    if len(valid):
        reit_recovery = valid["value_60"] / valid["value_t"] - 1.0
        equity_recovery = valid["equity_value_60"] / valid["equity_value_t"] - 1.0
        rebound_share = float((reit_recovery.gt(0) & reit_recovery.gt(equity_recovery)).mean())
    else:
        rebound_share = None
    checks = [fall_share is not None and fall_share > 0.50, rebound_share is not None and rebound_share > 0.50]
    verdict = "적중" if all(checks) else "부분 적중" if any(checks) else "오판"
    scorecard.append(
        {
            "prediction": "리츠: 2단계 — 당일엔 주식과 같이 빠지고, 이후 금리 인하로 급반등 (2022는 별도 채점)",
            "verdict": verdict,
            "evidence": (
                f"2022 제외 T<100 비율 {fall_share:.1%}; T→+60 양(+)이면서 주식 회복률 초과 {rebound_share:.1%}"
                if fall_share is not None and rebound_share is not None
                else "자료 없음"
            ),
        }
    )
    scorecard.append(direction_row("gold", "금: 오를 것"))

    fx = pd.to_numeric(frame.loc[frame["asset_id"].eq("usdkrw"), "value_t"], errors="coerce").dropna()
    if len(fx) and float(fx.min()) < 100 < float(fx.max()):
        fx_verdict = "적중"
    else:
        fx_verdict = "채점 제외"
    scorecard.append(
        {
            "prediction": "달러/원: 모르겠음 — 믿고 설계하면 안 되는 항목; 측정은 하되 평균이 아니라 범위·최악의 경우로 보고",
            "verdict": fx_verdict,
            "evidence": f"T의 USD100 KRW 가치 범위 {float(fx.min()):.2f}–{float(fx.max()):.2f}, 최악 {float(fx.min()):.2f}" if len(fx) else "자료 없음",
        }
    )
    return sorted(scorecard, key=lambda item: order[item["verdict"]])


def _validate_outputs(
    episodes: list[Episode],
    assets: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    quick: bool,
) -> None:
    if (quick and len(episodes) != 6) or (not quick and len(episodes) < 6):
        raise ValueError(f"unexpected episode count for quick={quick}: {len(episodes)}")
    if len(assets) != 13 or len(records) != len(episodes) * len(assets):
        raise ValueError("asset/episode output is incomplete")
    frame = pd.DataFrame(records)
    if frame[["episode_id", "asset_id"]].duplicated().any():
        raise ValueError("asset/episode keys must be unique")
    if set(frame["market"]) != {"KR", "US"}:
        raise ValueError("both KR and US episodes are required")
    if set(frame["cycle_type"]) != {"경기침체형", "인플레형"}:
        raise ValueError("both recession-type and inflation-type rows are required")
    if frame.loc[frame["asset_id"].eq("cash_3m_proxy"), "value_t"].isna().any():
        raise ValueError("3M cash proxy must cover every episode")
    for episode in episodes:
        if episode.t60_date is None:
            pending = frame.loc[frame["episode_id"].eq(episode.episode_id)]
            if pending["value_60"].notna().any() or pending["max_drawdown"].notna().any():
                raise ValueError("incomplete +60 windows must remain missing")


def _reit_2022_score(records: list[dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame(records)
    rows = frame.loc[
        frame["asset_id"].eq("reit_vnq") & frame["cycle_type"].eq("인플레형")
    ].copy()
    t_values = pd.to_numeric(rows["value_t"], errors="coerce").dropna()
    valid = rows[["value_t", "value_60", "equity_value_t", "equity_value_60"]].apply(
        pd.to_numeric, errors="coerce"
    ).dropna()
    fall_share = float(t_values.lt(100).mean()) if len(t_values) else None
    if len(valid):
        recovery = valid["value_60"] / valid["value_t"] - 1.0
        equity = valid["equity_value_60"] / valid["equity_value_t"] - 1.0
        rebound_share = float((recovery.gt(0) & recovery.gt(equity)).mean())
    else:
        rebound_share = None
    checks = [fall_share is not None and fall_share > 0.50, rebound_share is not None and rebound_share > 0.50]
    verdict = "적중" if all(checks) else "부분 적중" if any(checks) else "오판"
    return {
        "verdict": verdict,
        "fall_share": fall_share,
        "rebound_share": rebound_share,
        "observations": len(t_values),
    }


def _fmt(value: Any, digits: int = 2) -> str:
    return "자료 없음" if value is None or pd.isna(value) else f"{float(value):.{digits}f}"


def _fmt_pair(metrics: dict[str, Any]) -> str:
    return f"{_fmt(metrics['median'])} / {_fmt(metrics['worst'])}"


def _markdown(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    episodes = summary["episodes"]
    assets = summary["assets"]
    signal_rule = summary["signal_rule"]
    lines = [
        f"# 낙폭 {signal_rule['levels']}단계의 코어 자산 실탄 조달력",
        "",
        f"> 개발용 retained-data 기술통계다. T는 사다리 {signal_rule['levels']}단계가 종가에 관측된 같은 종가의 평가액이며, 그 신호를 보고 T 종가에 체결할 수 있었다는 뜻이 아니다.",
        "",
        (
            f"- 규칙: `drawdown252 ≤ {signal_rule['drawdown252']:g}`, "
            f"`disp60 ≤ {signal_rule['disp60']:g}`, {signal_rule['levels']}단계, "
            f"기본 노출 {signal_rule['base_exposure']:g}; "
            "`compound_ladder.ladder_levels`의 `observed_level` 재사용"
        ),
        f"- 에피소드: 총 **{len(episodes)}개** (KR {sum(item['market'] == 'KR' for item in episodes)}, US {sum(item['market'] == 'US' for item in episodes)}), 첫 T 뒤 120세션 재발 억제",
        "- 보유 시작: T−60 또는 T 전 마지막 level-0 중 더 늦은 날의 종가를 100으로 둠",
        f"- 입력 manifest: `{summary['input_manifest_sha256']}` ({summary['input_file_count']} Parquet, `partitioning=None`, API 호출 0)",
        "",
        "## 실행 전 사용자 예측 (원문)",
        "",
        '> "TLT: 오를 것"',
        "",
        '> "리츠: 2단계 — 당일엔 주식과 같이 빠지고, 이후 금리 인하로 급반등 (2022는 별도 채점)"',
        "",
        '> "금: 오를 것"',
        "",
        '> "달러/원: 모르겠음 — 믿고 설계하면 안 되는 항목; 측정은 하되 평균이 아니라 범위·최악의 경우로 보고"',
        "",
        "## 에피소드",
        "",
        "|시장|T|보유 시작|T+20|T+60|사이클|유형|drawdown252|disp60|",
        "|---|---:|---:|---:|---:|---|---|---:|---:|",
    ]
    for item in episodes:
        lines.append(
            f"|{item['market']}|{item['signal_date']}|{item['hold_start_date']}|{item['t20_date'] or '미도래'}|{item['t60_date'] or '미도래'}|{item['cycle']}|{item['cycle_type']}|{float(item['drawdown252']):.1%}|{float(item['disp60']):.1%}|"
        )

    lines.extend(
        [
            "",
            "## 헤드라인: 얼마의 실탄이 남았나",
            "",
            "각 칸은 `중앙값 / 최악`이다. USD/KRW는 아래에서 범위·최악만 별도로 제시한다.",
            "",
            "|자산|구간|N(T/+20/+60)|T|+20|+60|",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for asset_id, asset in assets.items():
        if asset_id == "usdkrw":
            continue
        for split in SPLIT_LABELS:
            aggregate = asset["aggregate"][split]
            counts = "/".join(str(aggregate[label]["count"]) for label in ("t", "20", "60"))
            lines.append(
                f"|{asset['asset']}|{split}|{counts}|{_fmt_pair(aggregate['t'])}|{_fmt_pair(aggregate['20'])}|{_fmt_pair(aggregate['60'])}|"
            )

    lines.extend(
        [
            "",
            "### 최선값",
            "",
            "|자산|구간|T best|+20 best|+60 best|",
            "|---|---|---:|---:|---:|",
        ]
    )
    for asset_id, asset in assets.items():
        if asset_id == "usdkrw":
            continue
        for split in SPLIT_LABELS:
            aggregate = asset["aggregate"][split]
            lines.append(
                f"|{asset['asset']}|{split}|{_fmt(aggregate['t']['best'])}|{_fmt(aggregate['20']['best'])}|{_fmt(aggregate['60']['best'])}|"
            )

    lines.extend(
        [
            "",
            "## 만기별 T 실탄 (사이클 내 중앙값)",
            "",
            "|시장|사이클|에피소드 수|3M|2Y|3Y|5Y|10Y|30Y|",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary["tenor_by_cycle"]:
        lines.append(
            "|{market}|{cycle}|{episode_count}|{cash}|{y2}|{y3}|{y5}|{y10}|{y30}|".format(
                market=row["market"],
                cycle=row["cycle"],
                episode_count=row["episode_count"],
                cash=_fmt(row["cash_3m_proxy"]),
                y2=_fmt(row["treasury_2y_proxy"]),
                y3=_fmt(row["treasury_3y_proxy"]),
                y5=_fmt(row["treasury_5y_proxy"]),
                y10=_fmt(row["treasury_10y_proxy"]),
                y30=_fmt(row["treasury_30y_proxy"]),
            )
        )
    gap_bits = []
    for asset_id in ("treasury_3y_proxy", "treasury_5y_proxy", "treasury_10y_proxy"):
        metric = assets[asset_id]["aggregate"]["전체"]["t"]
        gap_bits.append(f"{assets[asset_id]['asset']} 중앙/최악 {_fmt(metric['median'])}/{_fmt(metric['worst'])}")
    lines.extend(
        [
            "",
            "사용자 계좌의 SGOV–TLT 사이 3–10Y 공백에 대해 이 표가 말하는 것은 추천이 아니라 관측된 완충 위치다. "
            + "; ".join(gap_bits)
            + ". 장기 듀레이션 한 점(TLT/30Y)과 현금 한 점(SGOV/3M)만으로는 이 중간 만기의 서로 다른 낙폭 경로가 표현되지 않는다.",
            "",
            "## 실탄 / 매수 대상 / 중립 분류",
            "",
            "|자산|분류|T≥100 비율|최악 T|T→+60 주식 회복률 초과|N(T/+60 비교)|",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for asset in assets.values():
        item = asset["classification"]
        share_t = "자료 없음" if item["share_t_ge_100"] is None else f"{item['share_t_ge_100']:.1%}"
        share_recovery = (
            "자료 없음"
            if item["share_recovery_beats_equity"] is None
            else f"{item['share_recovery_beats_equity']:.1%}"
        )
        lines.append(
            f"|{asset['asset']}|{item['classification']}|{share_t}|{_fmt(item['worst_t'])}|{share_recovery}|{item['observations_t']}/{item['recovery_observations']}|"
        )

    lines.extend(
        [
            "",
            "## 예측 채점 — 오판부터",
            "",
            "|판정|사전 예측 원문|근거|",
            "|---|---|---|",
        ]
    )
    for item in summary["prediction_scorecard"]:
        lines.append(f"|{item['verdict']}|{item['prediction']}|{item['evidence']}|")

    fx_rows = pd.DataFrame(records).loc[lambda frame: frame["asset_id"].eq("usdkrw")]
    lines.extend(
        [
            "",
            "## USD/KRW — 범위와 최악만",
            "",
            "상승은 원화 약세이며 USD100의 원화 환산가치를 늘린다. 평균이나 중앙값은 의도적으로 표시하지 않는다.",
            "",
            "|구간|관측 수|FX 움직임 범위|USD100 KRW 가치 범위|최악|",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, title in (("t", "T"), ("20", "+20"), ("60", "+60")):
        values = pd.to_numeric(fx_rows[f"krw_value_{label}"], errors="coerce").dropna()
        moves = pd.to_numeric(fx_rows[f"fx_move_{label}"], errors="coerce").dropna()
        lines.append(
            f"|{title}|{len(values)}|{moves.min():.1%}–{moves.max():.1%}|{values.min():.2f}–{values.max():.2f}|{values.min():.2f}|"
        )

    lines.extend(
        [
            "",
            "## 2022 인플레형 별도 채점",
            "",
            f"리츠 예측: **{summary['reit_2022_score']['verdict']}** (T<100 {summary['reit_2022_score']['fall_share']:.1%}, T→+60 양(+)이면서 주식 회복률 초과 {summary['reit_2022_score']['rebound_share']:.1%}, N={summary['reit_2022_score']['observations']}).",
            "",
            "|시장|T|자산|T|+20|+60|주식 T|주식 +60|",
            "|---|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    inflation = pd.DataFrame(records)
    inflation = inflation.loc[
        inflation["cycle_type"].eq("인플레형")
        & inflation["asset_id"].isin(["tlt", "reit_vnq", "gold", "usdkrw"])
    ]
    for row in inflation.to_dict("records"):
        lines.append(
            f"|{row['market']}|{pd.Timestamp(row['signal_date']):%Y-%m-%d}|{row['asset']}|{_fmt(row['value_t'])}|{_fmt(row['value_20'])}|{_fmt(row['value_60'])}|{_fmt(row['equity_value_t'])}|{_fmt(row['equity_value_60'])}|"
        )

    lines.extend(
        [
            "",
            "## 해석 제한",
            "",
            "- 듀레이션 근사는 볼록성과 롤다운을 무시한다. 전일 수익률을 `−D×Δy + 전일 y/252`로 계산했다.",
            f"- 실제 보존 ETF 범위는 SHY/IEF {assets['shy']['coverage']['start']}~, VNQ {assets['reit_vnq']['coverage']['start']}~, TLT {assets['tlt']['coverage']['start']}~, SGOV {assets['sgov']['coverage']['start']}~이다. 현재 TLT 보존본은 상품의 2002년 상장까지 거슬러 가지 않으므로 TLT 채점은 30Y 프록시를 본체, 실제 TLT를 짧은 겹침 교차검증으로 썼다.",
            "- VNQ는 2004년부터이므로 그 이전 리츠는 `자료 없음`이다. 실물 ETF는 adjusted close 총수익 교차검증이며 실제 분배금 재투자·세금·스프레드는 반영하지 않는다.",
            "- 금은 2000년부터의 Yahoo `GC=F` 벤더 연속선물이다. 개별 만기·공식 결제가격·롤 비용과 동일하지 않다.",
            "- FRED 금리는 미국물뿐이며, 현재 보존본은 빈티지 고정 ALFRED가 아니다. 이 결과는 기술통계이지 PIT 예측성과가 아니다.",
            f"- 에피소드가 {len(episodes)}개로 작고 서로 같은 장기 약세장 안의 121세션 간격 재진입을 포함한다. 유형은 2022 대 나머지의 연구용 이분법이지 공식 경기침체 판정이 아니다.",
            "- 2026-07-13 KR 에피소드는 +60세션이 아직 없어 +60과 완전한 [T−60,T+60] 최대낙폭을 결측 처리했다.",
            "- KR/US 휴장일 차이는 각 코어 자산의 마지막 관측값을 해당 주식시장 목표일에 as-of 정렬했다.",
            "- T 값은 같은 종가 평가액이다. T 종가로 계산된 신호를 실제 거래에 쓰려면 최소 다음 실행 가능 시점이 필요하다.",
            "",
        ]
    )
    return "\n".join(lines)


def _followup_measurements(
    episodes: list[Episode],
    frames: dict[str, pd.DataFrame],
    ladders: dict[str, pd.DataFrame],
    assets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for episode in episodes:
        equity = prepare_value_series(frames[episode.market]["date"], frames[episode.market]["close"])
        session_dates = ladders[episode.market]["date"]
        for asset_id, metadata in assets.items():
            row = measure_asset_horizons(
                metadata["values"],
                episode,
                equity,
                session_dates,
                offsets=FOLLOWUP_HORIZONS,
            )
            row.update(
                {
                    "asset_id": asset_id,
                    "asset": metadata["label"],
                    "asset_kind": metadata["kind"],
                }
            )
            rows.append(row)
    return rows


def _path_summary(rows: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for asset_id in FOLLOWUP_ASSETS:
        part = rows.loc[rows["asset_id"].eq(asset_id)]
        label = str(part["asset"].iloc[0])
        for offset in FOLLOWUP_HORIZONS:
            horizon = "t" if offset == 0 else str(offset)
            values = pd.to_numeric(part[f"value_{horizon}"], errors="coerce").dropna()
            output.append(
                {
                    "asset_id": asset_id,
                    "asset": label,
                    "horizon": horizon,
                    "count": int(len(values)),
                    "median": float(values.median()) if len(values) else None,
                    "mean": float(values.mean()) if len(values) else None,
                }
            )
    return output


def _worst_episode_rows(rows: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for asset_id, part in rows.groupby("asset_id", sort=False):
        for horizon in ("t", "20", "60"):
            values = pd.to_numeric(part[f"value_{horizon}"], errors="coerce")
            if not values.notna().any():
                output.append(
                    {
                        "asset_id": asset_id,
                        "asset": part["asset"].iloc[0],
                        "horizon": horizon,
                        "value": None,
                        "episode_id": None,
                        "signal_date": None,
                        "target_date": None,
                        "cycle": None,
                    }
                )
                continue
            index = values.idxmin()
            row = part.loc[index]
            output.append(
                {
                    "asset_id": asset_id,
                    "asset": row["asset"],
                    "horizon": horizon,
                    "value": float(values.loc[index]),
                    "episode_id": row["episode_id"],
                    "signal_date": row["signal_date"],
                    "target_date": row[f"date_{horizon}"],
                    "cycle": row["cycle"],
                }
            )
    return output


def _same_episode_check(rows: pd.DataFrame, worst_rows: list[dict[str, Any]]) -> dict[str, Any]:
    worst = pd.DataFrame(worst_rows)
    proxy = worst.loc[worst["asset_id"].eq("treasury_30y_proxy")].set_index("horizon")
    worst_t_id = str(proxy.at["t", "episode_id"])
    worst_60_id = str(proxy.at["60", "episode_id"])
    proxy_rows = rows.loc[
        rows["asset_id"].eq("treasury_30y_proxy") & rows["episode_id"].eq(worst_t_id)
    ]
    worst_t_plus_60 = pd.to_numeric(proxy_rows["value_60"], errors="coerce").iloc[0]
    return {
        "worst_t_episode_id": worst_t_id,
        "worst_t_signal_date": proxy.at["t", "signal_date"],
        "worst_t_value": float(proxy.at["t", "value"]),
        "worst_60_episode_id": worst_60_id,
        "worst_60_signal_date": proxy.at["60", "signal_date"],
        "worst_60_value": float(proxy.at["60", "value"]),
        "same_episode": worst_t_id == worst_60_id,
        "worst_t_episode_value_60": float(worst_t_plus_60),
        "continues_below_100_at_60": bool(worst_t_plus_60 < 100.0),
    }


def _cycle_decomposition(rows: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decomposition: list[dict[str, Any]] = []
    overall: list[dict[str, Any]] = []
    for asset_id, asset_rows in rows.groupby("asset_id", sort=False):
        for cycle in CYCLE_BUCKETS:
            part = asset_rows.loc[asset_rows["cycle"].eq(cycle)]
            item: dict[str, Any] = {
                "asset_id": asset_id,
                "asset": asset_rows["asset"].iloc[0],
                "cycle": cycle,
                "episode_count": int(part["episode_id"].nunique()),
            }
            for horizon in ("t", "20", "60"):
                values = pd.to_numeric(part[f"value_{horizon}"], errors="coerce").dropna()
                item[f"count_{horizon}"] = int(len(values))
                item[f"median_{horizon}"] = float(values.median()) if len(values) else None
                item[f"worst_{horizon}"] = float(values.min()) if len(values) else None
            decomposition.append(item)

        candidates: list[tuple[float, str, pd.Series]] = []
        for _, row in asset_rows.iterrows():
            for horizon in ("t", "20", "60"):
                value = pd.to_numeric(pd.Series([row[f"value_{horizon}"]]), errors="coerce").iloc[0]
                if pd.notna(value):
                    candidates.append((float(value), horizon, row))
        value, horizon, row = min(candidates, key=lambda item: item[0])
        t_values = pd.to_numeric(asset_rows["value_t"], errors="coerce")
        t_index = t_values.idxmin()
        t_row = asset_rows.loc[t_index]
        overall.append(
            {
                "asset_id": asset_id,
                "asset": row["asset"],
                "worst_t_value": float(t_values.loc[t_index]),
                "worst_t_episode_id": t_row["episode_id"],
                "worst_t_cycle": t_row["cycle"],
                "worst_t_origin": (
                    "2022 인플레형" if t_row["cycle_type"] == "인플레형" else "침체형"
                ),
                "value": value,
                "horizon": horizon,
                "episode_id": row["episode_id"],
                "signal_date": row["signal_date"],
                "cycle": row["cycle"],
                "origin": "2022 인플레형" if row["cycle_type"] == "인플레형" else "침체형",
            }
        )
    return decomposition, overall


def _quantile_and_classification(rows: pd.DataFrame) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for asset_id, part in rows.groupby("asset_id", sort=False):
        original = classify_asset(part)
        p10 = classify_asset(part, floor_statistic="p10")
        output.append(
            {
                "asset_id": asset_id,
                "asset": part["asset"].iloc[0],
                "quantiles": quantile_values(part),
                "original_classification": original["classification"],
                "p10_classification": p10["classification"],
                "share_t_ge_100": original["share_t_ge_100"],
                "worst_t": original["worst_t"],
                "p10_t": p10["p10_t"],
                "changed": original["classification"] != p10["classification"],
            }
        )
    return output


def _carry_and_gap(
    treasury_ext: pd.DataFrame,
    episodes: list[Episode],
    rows: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    frame = treasury_ext.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame = frame.loc[frame["date"].ge(pd.Timestamp("1990-01-01"))]
    cash = pd.to_numeric(frame["dtb3"], errors="coerce")
    carry: list[dict[str, Any]] = []
    for asset_id, column, label in (
        ("treasury_3y_proxy", "dgs3", "3Y"),
        ("treasury_5y_proxy", "dgs5", "5Y"),
    ):
        tenor = pd.to_numeric(frame[column], errors="coerce")
        spread = tenor - cash
        spread = spread.dropna()
        asset_rows = rows.loc[rows["asset_id"].eq(asset_id)]
        t_values = pd.to_numeric(asset_rows["value_t"], errors="coerce").dropna()
        carry.append(
            {
                "asset_id": asset_id,
                "tenor": label,
                "observations": int(len(spread)),
                "start": frame.loc[spread.index, "date"].min(),
                "end": frame.loc[spread.index, "date"].max(),
                "annual_carry_advantage_pp": float(spread.mean()),
                "crisis_t_p25_value": float(t_values.quantile(0.25)),
                "crisis_t_worst_value": float(t_values.min()),
                "crisis_t_p25_loss_pct": float(t_values.quantile(0.25) - 100.0),
                "crisis_t_worst_loss_pct": float(t_values.min() - 100.0),
            }
        )

    gap_rows: list[dict[str, Any]] = []
    for market in ("KR", "US"):
        dates = sorted(episode.signal_date for episode in episodes if episode.market == market)
        gaps = np.diff(np.asarray(dates, dtype="datetime64[D]")).astype("timedelta64[D]").astype(int)
        years = gaps.astype("float64") / 365.2425
        gap_rows.append(
            {
                "market": market,
                "episode_count": len(dates),
                "interval_count": int(len(years)),
                "mean_gap_years": float(years.mean()),
                "median_gap_years": float(np.median(years)),
            }
        )

    break_even: list[dict[str, Any]] = []
    for carry_row in carry:
        for gap in gap_rows:
            break_even.append(
                {
                    "tenor": carry_row["tenor"],
                    "market_gap": gap["market"],
                    "annual_carry_advantage_pp": carry_row["annual_carry_advantage_pp"],
                    "mean_gap_years": gap["mean_gap_years"],
                    "carry_times_gap_pct": carry_row["annual_carry_advantage_pp"]
                    * gap["mean_gap_years"],
                    "crisis_t_p25_loss_pct": carry_row["crisis_t_p25_loss_pct"],
                    "crisis_t_worst_loss_pct": carry_row["crisis_t_worst_loss_pct"],
                }
            )
    return carry, gap_rows, break_even


def _reit_horizon(rows: pd.DataFrame) -> list[dict[str, Any]]:
    reit = rows.loc[rows["asset_id"].eq("reit_vnq")].copy()
    output: list[dict[str, Any]] = []
    for split, part in (
        ("침체형", reit.loc[reit["cycle_type"].eq("경기침체형")]),
        ("2022 인플레형", reit.loc[reit["cycle_type"].eq("인플레형")]),
    ):
        for horizon in ("60", "120", "250"):
            values = pd.to_numeric(part[f"value_{horizon}"], errors="coerce").dropna()
            comparison = part[["value_t", f"value_{horizon}", "equity_value_t", f"equity_value_{horizon}"]].apply(
                pd.to_numeric, errors="coerce"
            ).dropna()
            if len(comparison):
                reit_recovery = comparison[f"value_{horizon}"] / comparison["value_t"] - 1.0
                equity_recovery = (
                    comparison[f"equity_value_{horizon}"] / comparison["equity_value_t"] - 1.0
                )
                beat_share = float(reit_recovery.gt(equity_recovery).mean())
                positive_share = float(reit_recovery.gt(0.0).mean())
            else:
                beat_share = None
                positive_share = None
            output.append(
                {
                    "split": split,
                    "horizon": horizon,
                    "count": int(len(values)),
                    "median": float(values.median()) if len(values) else None,
                    "p25": float(values.quantile(0.25)) if len(values) else None,
                    "worst": float(values.min()) if len(values) else None,
                    "comparison_count": int(len(comparison)),
                    "beat_equity_recovery_share": beat_share,
                    "positive_recovery_share": positive_share,
                }
            )
    return output


def _peak_timing(
    episodes: list[Episode],
    ladders: dict[str, pd.DataFrame],
    assets: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    episode_rows: list[dict[str, Any]] = []
    for asset_id in ("treasury_30y_proxy", "tlt"):
        metadata = assets[asset_id]
        for episode in episodes:
            peak = peak_after_episode(
                metadata["values"],
                episode,
                ladders[episode.market]["date"],
                max_offset=250,
            )
            episode_rows.append(
                {
                    "asset_id": asset_id,
                    "asset": metadata["label"],
                    "episode_id": episode.episode_id,
                    "market": episode.market,
                    "signal_date": episode.signal_date,
                    "cycle": episode.cycle,
                    "cycle_type": episode.cycle_type,
                    **peak,
                }
            )

    peak_frame = pd.DataFrame(episode_rows)
    aggregates: list[dict[str, Any]] = []
    for asset_id in ("treasury_30y_proxy", "tlt"):
        asset_rows = peak_frame.loc[peak_frame["asset_id"].eq(asset_id)]
        for split, split_rows in (
            ("전체", asset_rows),
            ("침체형", asset_rows.loc[asset_rows["cycle_type"].eq("경기침체형")]),
            ("2022 인플레형", asset_rows.loc[asset_rows["cycle_type"].eq("인플레형")]),
        ):
            complete = split_rows.loc[split_rows["full_window"].eq(True)].copy()  # noqa: E712
            complete = complete.dropna(subset=["peak_offset", "peak_value"])
            offsets = pd.to_numeric(complete["peak_offset"], errors="coerce")
            levels = pd.to_numeric(complete["peak_value"], errors="coerce")
            aggregates.append(
                {
                    "asset_id": asset_id,
                    "asset": asset_rows["asset"].iloc[0],
                    "split": split,
                    "count": int(len(complete)),
                    "median_offset": float(offsets.median()) if len(offsets) else None,
                    "p25_offset": float(offsets.quantile(0.25)) if len(offsets) else None,
                    "p75_offset": float(offsets.quantile(0.75)) if len(offsets) else None,
                    "share_peak_gt_105": float(levels.gt(105.0).mean()) if len(levels) else None,
                }
            )
    return episode_rows, aggregates


def _series_asof(series: pd.Series, date: pd.Timestamp) -> float:
    position = int(series.index.searchsorted(pd.Timestamp(date), side="right")) - 1
    if position < 0:
        raise ValueError(f"no retained yield observation on or before {date:%Y-%m-%d}")
    return float(series.iloc[position])


def _crisis_axis(
    treasury: pd.DataFrame,
    episodes: list[Episode],
    ladders: dict[str, pd.DataFrame],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    yields = treasury.copy()
    yields["date"] = pd.to_datetime(yields["date"], errors="raise").dt.normalize()
    dgs10 = prepare_value_series(yields["date"], yields["dgs10"])
    dgs2 = prepare_value_series(yields["date"], yields["dgs2"])
    episode_rows: list[dict[str, Any]] = []
    for episode in episodes:
        ladder = ladders[episode.market]
        prior_index = episode.signal_index - 60
        if prior_index < 0:
            raise ValueError(f"episode lacks T-60 session: {episode.episode_id}")
        prior_date = pd.Timestamp(ladder.at[prior_index, "date"])
        delta_10y = _series_asof(dgs10, episode.signal_date) - _series_asof(dgs10, prior_date)
        delta_2y = _series_asof(dgs2, episode.signal_date) - _series_asof(dgs2, prior_date)
        rules = fixed_crisis_types(delta_10y, delta_2y)
        episode_rows.append(
            {
                "episode_id": episode.episode_id,
                "market": episode.market,
                "signal_date": episode.signal_date,
                "t_minus_60_date": prior_date,
                "cycle": episode.cycle,
                "actual_type": "인플레형" if episode.cycle_type == "인플레형" else "침체형",
                "delta_10y_pp": delta_10y,
                "delta_2y_pp": delta_2y,
                **rules,
            }
        )

    focal = pd.DataFrame(episode_rows)
    focal = focal.loc[
        focal["cycle"].isin(["2008–09 금융위기", "2020 코로나", "2022 인플레"])
    ]
    confusion: dict[str, Any] = {}
    for rule in ("ten_year_rule", "two_year_first_rule"):
        counts: list[dict[str, Any]] = []
        for actual in ("침체형", "인플레형"):
            for predicted in ("침체형", "인플레형"):
                count = int(
                    (focal["actual_type"].eq(actual) & focal[rule].eq(predicted)).sum()
                )
                counts.append({"actual": actual, "predicted": predicted, "count": count})
        confusion[rule] = {
            "counts": counts,
            "correct": int(focal[rule].eq(focal["actual_type"]).sum()),
            "total": int(len(focal)),
            "separates_focal_cycles": bool(focal[rule].eq(focal["actual_type"]).all()),
        }
    return episode_rows, confusion


def _fmt_pct(value: Any, digits: int = 1) -> str:
    return "자료 없음" if value is None or pd.isna(value) else f"{float(value):.{digits}%}"


def _followup_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# 코어 실탄 연구 후속 점검 — 위기 유형축 1차 검사",
        "",
        "> retained Parquet만 사용한 개발용 기술통계다. 기존 40개 level-2 에피소드와 보유 시작점 평가 함수를 재사용했다. T는 같은 종가의 설명용 평가이며 실행 가능 수익률이 아니다.",
        "",
        f"- 입력 manifest: `{summary['input_manifest_sha256']}` ({summary['input_file_count']} Parquet, API 호출 {summary['api_calls']})",
        f"- 에피소드: {len(summary['episodes'])}개; +120/+250은 각 시장의 retained 거래세션 기준",
        "- 값 100은 기존 보유 시작일 기준이며, 손실은 `값−100`%로 읽는다.",
        "",
        "## 1. TLT/30Y 중앙 경로와 단기물 비교",
        "",
        "|자산|시점|N|중앙값|평균|",
        "|---|---:|---:|---:|---:|",
    ]
    for row in summary["path_summary"]:
        horizon = "T" if row["horizon"] == "t" else f"+{row['horizon']}"
        lines.append(
            f"|{row['asset']}|{horizon}|{row['count']}|{_fmt(row['median'])}|{_fmt(row['mean'])}|"
        )
    lines.extend(
        [
            "",
            "30Y/TLT의 평소 이득과 최악 손실은 아래 2·4절의 worst/p10과 함께 봐야 한다. 2Y/3M은 같은 보유 시작점·같은 에피소드로 비교했다.",
            "",
            "## 2. 최악 T와 최악 +60은 같은 에피소드인가",
            "",
        ]
    )
    same = summary["same_episode_30y"]
    lines.append(
        f"**답:** 30Y 프록시의 최악 T와 최악 +60은 **{'같다' if same['same_episode'] else '다르다'}**. "
        f"최악 T는 `{same['worst_t_episode_id']}` ({_fmt(same['worst_t_value'])}), 최악 +60은 "
        f"`{same['worst_60_episode_id']}` ({_fmt(same['worst_60_value'])})이다."
    )
    lines.append("")
    lines.append(
        f"'한 번 빠지기 시작하면 계속 간다'는 명제는 이 최악-T 사례에서 **{'성립' if same['continues_below_100_at_60'] else '성립하지 않음'}**: "
        f"그 에피소드의 +60 값은 {_fmt(same['worst_t_episode_value_60'])}이다. 이는 한 사례 확인이지 일반적 지속성 검정은 아니다."
    )
    lines.extend(
        [
            "",
            "|자산|최악 T (에피소드 / T일)|최악 +20 (에피소드 / 목표일)|최악 +60 (에피소드 / 목표일)|",
            "|---|---|---|---|",
        ]
    )
    worst = pd.DataFrame(summary["worst_episodes"])
    for asset_id in worst["asset_id"].drop_duplicates():
        part = worst.loc[worst["asset_id"].eq(asset_id)].set_index("horizon")
        cells = []
        for horizon in ("t", "20", "60"):
            row = part.loc[horizon]
            cells.append(
                "자료 없음"
                if row["value"] is None or pd.isna(row["value"])
                else f"{_fmt(row['value'])} / `{row['episode_id']}` / {row['target_date']}"
            )
        lines.append(f"|{part.iloc[0]['asset']}|{cells[0]}|{cells[1]}|{cells[2]}|")

    lines.extend(
        [
            "",
            "## 3. 사이클별 분해",
            "",
            "각 칸은 `중앙값 / 최악`이며, 2015–16처럼 신호가 없으면 자료 없음이다. 이 표는 요청한 9개 사전 버킷만 보이고, 전체 worst 판정은 기타 초기 에피소드까지 포함한다.",
            "",
            "|자산|사이클|N|T|+20|+60|",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in summary["cycle_decomposition"]:
        lines.append(
            f"|{row['asset']}|{row['cycle']}|{row['episode_count']}|"
            f"{_fmt(row['median_t'])} / {_fmt(row['worst_t'])}|"
            f"{_fmt(row['median_20'])} / {_fmt(row['worst_20'])}|"
            f"{_fmt(row['median_60'])} / {_fmt(row['worst_60'])}|"
        )
    lines.extend(
        [
            "",
            "전체 worst는 T/+20/+60의 모든 유효 셀 중 최저값으로 정의했다.",
            "",
            "|자산|worst T / 출처|T 에피소드|T/+20/+60 전체 worst|시점|전체 worst 에피소드 / 사이클|출처 유형|",
            "|---|---|---|---:|---:|---|---|",
        ]
    )
    for row in summary["overall_worst_origin"]:
        horizon = "T" if row["horizon"] == "t" else f"+{row['horizon']}"
        lines.append(
            f"|{row['asset']}|{_fmt(row['worst_t_value'])} / **{row['worst_t_origin']}**|`{row['worst_t_episode_id']}`|"
            f"{_fmt(row['value'])}|{horizon}|`{row['episode_id']}` / {row['cycle']}|**{row['origin']}**|"
        )

    lines.extend(
        [
            "",
            "## 4. 분위수와 p10 바닥 분류",
            "",
            "|자산|시점|N|p10|p25|중앙값|p75|",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for asset in summary["quantiles"]:
        for horizon in ("t", "20", "60"):
            item = asset["quantiles"][horizon]
            title = "T" if horizon == "t" else f"+{horizon}"
            lines.append(
                f"|{asset['asset']}|{title}|{item['count']}|{_fmt(item['p10'])}|{_fmt(item['p25'])}|{_fmt(item['median'])}|{_fmt(item['p75'])}|"
            )
    lines.extend(
        [
            "",
            "다른 조건(T≥100 비율 70%, 회복률 비교)은 그대로 두고 `worst T ≥ 95`만 `p10 T ≥ 95`로 바꿨다.",
            "",
            "|자산|worst 기준|p10 기준|worst T|p10 T|변경|",
            "|---|---|---|---:|---:|---|",
        ]
    )
    for row in summary["quantiles"]:
        lines.append(
            f"|{row['asset']}|{row['original_classification']}|{row['p10_classification']}|{_fmt(row['worst_t'])}|{_fmt(row['p10_t'])}|{'예' if row['changed'] else '아니오'}|"
        )
    changed_count = sum(bool(row["changed"]) for row in summary["quantiles"])
    lines.append("")
    lines.append(f"결과적으로 분류 변경은 **{changed_count}/{len(summary['quantiles'])}개 자산**이다.")

    lines.extend(
        [
            "",
            "## 5. 중간 만기 carry–위기손실 산술",
            "",
            "1990-01-01 이후 같은 retained FRED 행에서 `(tenor yield − DTB3)`를 계산했다.",
            "",
            "|만기|관측 수|기간|연평균 carry 우위|위기 T p25 손실|위기 T 최악 손실|",
            "|---|---:|---|---:|---:|---:|",
        ]
    )
    for row in summary["carry_advantage"]:
        lines.append(
            f"|{row['tenor']}|{row['observations']}|{row['start']}–{row['end']}|{row['annual_carry_advantage_pp']:+.2f}%p|{row['crisis_t_p25_loss_pct']:+.2f}%|{row['crisis_t_worst_loss_pct']:+.2f}%|"
        )
    lines.extend(
        [
            "",
            "|시장|에피소드|간격 수|평균 달력 간격|중앙 달력 간격|",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in summary["episode_gap_years"]:
        lines.append(
            f"|{row['market']}|{row['episode_count']}|{row['interval_count']}|{row['mean_gap_years']:.2f}년|{row['median_gap_years']:.2f}년|"
        )
    lines.extend(
        [
            "",
            "|만기|간격 기준|carry × 평균 간격|위기 T p25 손실|최악 손실|산술 문장|",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for row in summary["break_even"]:
        sentence = (
            f"{row['tenor']}: 연 {row['annual_carry_advantage_pp']:+.2f}%p × 평균 {row['mean_gap_years']:.2f}년 = "
            f"{row['carry_times_gap_pct']:+.2f}% vs 위기 당일 p25 {row['crisis_t_p25_loss_pct']:+.2f}%, 최악 {row['crisis_t_worst_loss_pct']:+.2f}%"
        )
        lines.append(
            f"|{row['tenor']}|{row['market_gap']}|{row['carry_times_gap_pct']:+.2f}%|{row['crisis_t_p25_loss_pct']:+.2f}%|{row['crisis_t_worst_loss_pct']:+.2f}%|{sentence}|"
        )
    lines.append("")
    lines.append("이는 배분 조언이 아니라 관측 평균을 단순 곱한 산술 비교다. 복리, 듀레이션 변화, 세금·비용, 위기 간격 분포는 반영하지 않는다.")

    lines.extend(
        [
            "",
            "## 6. VNQ horizon 연장",
            "",
            "`주식 대비 우위`는 T→해당 horizon의 VNQ 회복률이 같은 에피소드 주식 기준 회복률보다 큰 비율이다.",
            "",
            "|구간|시점|N|중앙값|p25|최악|주식 대비 우위|T 이후 양(+) 회복|",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary["reit_horizon"]:
        if row["horizon"] == "60":
            continue
        lines.append(
            f"|{row['split']}|+{row['horizon']}|{row['count']}|{_fmt(row['median'])}|{_fmt(row['p25'])}|{_fmt(row['worst'])}|{_fmt_pct(row['beat_equity_recovery_share'])} ({row['comparison_count']})|{_fmt_pct(row['positive_recovery_share'])}|"
        )
    reit = {(row["split"], row["horizon"]): row for row in summary["reit_horizon"]}
    rec_60 = reit[("침체형", "60")]["beat_equity_recovery_share"]
    rec_120 = reit[("침체형", "120")]["beat_equity_recovery_share"]
    rec_250 = reit[("침체형", "250")]["beat_equity_recovery_share"]
    horizon_effect = max(value for value in (rec_120, rec_250) if value is not None) > float(rec_60)
    lines.append("")
    lines.append(
        f"침체형 주식 대비 우위 비율은 +60 {_fmt_pct(rec_60)} → +120 {_fmt_pct(rec_120)} → +250 {_fmt_pct(rec_250)}다. "
        f"따라서 약한 +60 반등은 **{'horizon 문제의 증거가 더 강하다' if horizon_effect else '단순 horizon 연장으로 해소되지 않아 mechanism 문제의 증거가 더 강하다'}**."
    )

    lines.extend(
        [
            "",
            "## 7. TLT/30Y 최고점 시기",
            "",
            "완전한 250세션 창의 집계만 사용했다. 미완전 창은 행에 `검열`로 표시하며 최고점 통계에서 제외한다. 동률 최고점은 최초 세션이다.",
            "",
            "|에피소드|사이클|30Y offset / 값|TLT offset / 값|창 상태|",
            "|---|---|---:|---:|---|",
        ]
    )
    peaks = pd.DataFrame(summary["peak_timing"]["episodes"])
    for episode_id in peaks["episode_id"].drop_duplicates():
        part = peaks.loc[peaks["episode_id"].eq(episode_id)].set_index("asset_id")
        cells = []
        states = []
        for asset_id in ("treasury_30y_proxy", "tlt"):
            row = part.loc[asset_id]
            unavailable = row["peak_offset"] is None or pd.isna(row["peak_offset"])
            cells.append("자료 없음" if unavailable else f"+{int(row['peak_offset'])} / {_fmt(row['peak_value'])}")
            states.append(
                "자료 없음"
                if unavailable
                else "완전"
                if row["full_window"]
                else f"검열(+{row['observed_through_offset']})"
            )
        lines.append(f"|`{episode_id}`|{part.iloc[0]['cycle']}|{cells[0]}|{cells[1]}|{' / '.join(states)}|")
    lines.extend(
        [
            "",
            "|자산|구간|N|중앙 offset|IQR|peak>105 비율|",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in summary["peak_timing"]["aggregate"]:
        iqr = (
            "자료 없음"
            if row["p25_offset"] is None
            else f"{row['p25_offset']:.1f}–{row['p75_offset']:.1f}"
        )
        lines.append(
            f"|{row['asset']}|{row['split']}|{row['count']}|{_fmt(row['median_offset'], 1)}|{iqr}|{_fmt_pct(row['share_peak_gt_105'])}|"
        )
    proxy_all = next(
        row
        for row in summary["peak_timing"]["aggregate"]
        if row["asset_id"] == "treasury_30y_proxy" and row["split"] == "전체"
    )
    lines.append("")
    lines.append(
        f"2차 실탄 가설에 들어갈 30Y 프록시의 중앙 최고점은 T+{proxy_all['median_offset']:.1f}세션, IQR {proxy_all['p25_offset']:.1f}–{proxy_all['p75_offset']:.1f}세션이며, 105를 넘긴 비율은 {_fmt_pct(proxy_all['share_peak_gt_105'])}다. 이는 매매 규칙이나 권고가 아니다."
    )

    lines.extend(
        [
            "",
            "## 8. 위기 유형축 1차 검사",
            "",
            "주 규칙은 `Δ10Y = dgs10(T) − dgs10(T−60 시장세션)`가 0보다 크면 인플레형, 아니면 침체형이다. 2Y-first 보조 규칙은 `Δ2Y < −0.5%p`면 침체형, 아니면 인플레형이다. 임계값은 조정하지 않았다.",
            "",
            "|에피소드|실제 사이클|Δ10Y|10Y 규칙|Δ2Y|2Y-first 규칙|실제 유형|",
            "|---|---|---:|---|---:|---|---|",
        ]
    )
    for row in summary["crisis_axis"]["episodes"]:
        lines.append(
            f"|`{row['episode_id']}`|{row['cycle']}|{row['delta_10y_pp']:+.2f}%p|{row['ten_year_rule']}|{row['delta_2y_pp']:+.2f}%p|{row['two_year_first_rule']}|{row['actual_type']}|"
        )
    lines.extend(
        [
            "",
            "혼동행렬은 요청한 2008/2020(실제 침체형)과 2022(실제 인플레형)만 사용한다.",
            "",
            "|규칙|실제|예측 침체형|예측 인플레형|정답/전체|완전 분리|",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for rule, label in (("ten_year_rule", "10Y"), ("two_year_first_rule", "2Y-first")):
        item = summary["crisis_axis"]["confusion"][rule]
        counts = {(row["actual"], row["predicted"]): row["count"] for row in item["counts"]}
        for actual in ("침체형", "인플레형"):
            lines.append(
                f"|{label}|{actual}|{counts[(actual, '침체형')]}|{counts[(actual, '인플레형')]}|{item['correct']}/{item['total']}|{'예' if item['separates_focal_cycles'] else '아니오'}|"
            )
    primary = summary["crisis_axis"]["confusion"]["ten_year_rule"]
    lines.append("")
    lines.append(
        "**한 문장 결론:** "
        + (
            "고정 10Y 축은 2008/2020과 2022를 완전 분리해 탐색적 증거가 있으나, 독립 클러스터 외부검증 전까지 연구축으로만 유지한다."
            if primary["separates_focal_cycles"]
            else "고정 10Y 축은 2008/2020과 2022를 완전 분리하지 못했으므로 현재 형태는 보류(shelve)한다."
        )
    )

    lines.extend(
        [
            "",
            "## 해석 제한",
            "",
            f"- 요청한 9개 사전 사이클 버킷 중 level-2가 실제 관측된 독립 클러스터는 **{summary['limitations']['observed_independent_cycle_clusters']}개**다(2015–16은 무신호). 2008과 2020의 KR/US 행은 각각 동기화된 같은 위기라 독립 표본 두 개로 세지 않는다.",
            "- 2022 인플레형은 에피소드 행이 여러 개여도 독립 위기 클러스터로는 **단 1개 관측**이다.",
            "- 30Y 등 듀레이션 프록시는 `−D×Δy + 전일 y/252`의 상수 듀레이션 근사로 볼록성·롤다운·실제 ETF 비용을 반영하지 않는다.",
            "- FRED 금리는 현재 보존본이며 빈티지 고정 ALFRED가 아니다. 이 결과는 descriptive/PIT-limited이고 예측성과나 실현 가능한 체결 성과가 아니다.",
            "- 2026-07-13 에피소드의 +60 이후 horizon/250세션 peak는 미도래 또는 우측 검열이다. 집계는 유효·완전 관측만 사용한다.",
            "- T−60은 각 시장 세션이며 FRED는 해당 날짜의 마지막 retained 관측을 as-of 정렬했다. 휴장일 차이를 미래 관측으로 메우지 않았다.",
            "- 어떤 표도 자산배분·매수/매도 권고나 적합성 판단이 아니다.",
            "",
        ]
    )
    return "\n".join(lines)


def run(
    project_root: Path,
    *,
    quick: bool = False,
    drawdown_threshold: float | None = None,
    disp60_threshold: float | None = None,
    product_share_at_max: float | None = None,
    levels: int | None = None,
    base_exposure: float | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    decided_drawdown = require_drawdown_threshold(drawdown_threshold)
    decided_disp60 = require_disp60_threshold(disp60_threshold)
    decided_share = require_product_share_at_max(product_share_at_max)
    decided_levels = require_levels(levels)
    decided_base_exposure = require_base_exposure(base_exposure)
    manifest = _manifest(root)
    episodes, frames, ladders = _episode_inputs(
        root,
        quick,
        drawdown_threshold=decided_drawdown,
        disp60_threshold=decided_disp60,
        product_share_at_max=decided_share,
        levels=decided_levels,
        base_exposure=decided_base_exposure,
    )
    assets, _ = _assets(root)
    records = _measure(episodes, frames, ladders, assets)
    _validate_outputs(episodes, assets, records, quick=quick)
    asset_summaries = _asset_summaries(records, assets)
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "core-ammunition/v1",
        "development_only": True,
        "api_calls": 0,
        "quick": quick,
        "signal_rule": {
            "id": f"kr_dd_ladder_{decided_levels}",
            "drawdown252": decided_drawdown,
            "disp60": decided_disp60,
            "levels": decided_levels,
            "base_exposure": decided_base_exposure,
            "product_share_at_max": decided_share,
            "level_field": "observed_level",
            "cooldown_sessions": 120,
        },
        "decision_clock": "T close descriptive mark; earliest signal-based action is after T close",
        "input_manifest_sha256": manifest["sha256"],
        "input_file_count": manifest["file_count"],
        "episodes": [episode.to_dict() for episode in episodes],
        "assets": asset_summaries,
        "tenor_by_cycle": _tenor_table(records, episodes),
        "prediction_scorecard": _prediction_scorecard(records),
        "reit_2022_score": _reit_2022_score(records),
    }

    output = root / "artifacts/research/core_ammunition"
    _write_json(output / "input_manifest.json", manifest)
    for asset_id, payload in asset_summaries.items():
        _write_json(output / "assets" / f"{asset_id}.json", payload)
    records_frame = pd.DataFrame(records)
    for episode in episodes:
        episode_rows = records_frame.loc[records_frame["episode_id"].eq(episode.episode_id)]
        _write_json(
            output / "episodes" / f"{episode.episode_id.lower()}.json",
            {"episode": episode.to_dict(), "assets": episode_rows.to_dict("records")},
        )
    _write_json(output / "summary.json", summary)
    _write_text(
        root / "docs/research/RESULTS_20260905_core_ammunition.md",
        _markdown(_json_value(summary), records),
    )
    print(
        f"DONE core-ammunition/v1 episodes={len(episodes)} assets={len(assets)} "
        f"manifest={manifest['sha256'][:12]} quick={quick}"
    )
    return summary


def _validate_followup(
    root: Path,
    manifest: dict[str, Any],
    episodes: list[Episode],
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    quick: bool,
) -> None:
    base_path = root / "artifacts/research/core_ammunition/summary.json"
    base = json.loads(base_path.read_text(encoding="utf-8"))
    expected_count = 6 if quick else 40
    if len(episodes) != expected_count or len(rows) != expected_count * 13:
        raise ValueError("follow-up episode/asset output is incomplete")
    if not quick:
        actual_episode_keys = [
            (episode.episode_id, episode.signal_date.strftime("%Y-%m-%d"), episode.hold_start_date.strftime("%Y-%m-%d"))
            for episode in episodes
        ]
        base_episode_keys = [
            (item["episode_id"], item["signal_date"], item["hold_start_date"])
            for item in base["episodes"]
        ]
        if actual_episode_keys != base_episode_keys:
            raise ValueError("follow-up must reuse the exact base episode table")
        if manifest["sha256"] != base["input_manifest_sha256"]:
            raise ValueError("follow-up retained input manifest differs from the base study")
        actual = pd.DataFrame(rows).set_index(["asset_id", "episode_id"])
        for asset_id, asset in base["assets"].items():
            for item in asset["episodes"]:
                key = (asset_id, item["episode_id"])
                for horizon in ("t", "20", "60"):
                    expected = item[f"value_{horizon}"]
                    observed = actual.at[key, f"value_{horizon}"]
                    if expected is None:
                        if pd.notna(observed):
                            raise ValueError(f"base/follow-up missingness mismatch: {key} {horizon}")
                    elif not math.isclose(float(observed), float(expected), rel_tol=0.0, abs_tol=1e-10):
                        raise ValueError(f"base/follow-up valuation mismatch: {key} {horizon}")
    frame = pd.DataFrame(rows)
    if frame[["episode_id", "asset_id"]].duplicated().any():
        raise ValueError("follow-up asset/episode keys must be unique")
    if set(frame["asset_id"]) != set(ETF_ASSETS) | set(YIELD_ASSETS) | {"cash_3m_proxy", "gold", "usdkrw"}:
        raise ValueError("follow-up asset set differs from the base study")
    crisis = summary["crisis_axis"]["episodes"]
    if len(crisis) != expected_count or any(item["ten_year_rule"] not in {"침체형", "인플레형"} for item in crisis):
        raise ValueError("crisis-axis classifications are incomplete")
    if not quick:
        empty_2015 = [
            item for item in summary["cycle_decomposition"] if item["cycle"] == "2015–16"
        ]
        if len(empty_2015) != 13 or any(item["episode_count"] != 0 for item in empty_2015):
            raise ValueError("the predefined 2015–16 empty bucket must be explicit")


def run_followup(
    project_root: Path,
    *,
    quick: bool = False,
    drawdown_threshold: float | None = None,
    disp60_threshold: float | None = None,
    product_share_at_max: float | None = None,
    levels: int | None = None,
    base_exposure: float | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    decided_drawdown = require_drawdown_threshold(drawdown_threshold)
    decided_disp60 = require_disp60_threshold(disp60_threshold)
    decided_share = require_product_share_at_max(product_share_at_max)
    decided_levels = require_levels(levels)
    decided_base_exposure = require_base_exposure(base_exposure)
    manifest = _manifest(root)
    episodes, frames, ladders = _episode_inputs(
        root,
        quick,
        drawdown_threshold=decided_drawdown,
        disp60_threshold=decided_disp60,
        product_share_at_max=decided_share,
        levels=decided_levels,
        base_exposure=decided_base_exposure,
    )
    assets, _ = _assets(root)
    rows = _followup_measurements(episodes, frames, ladders, assets)
    frame = pd.DataFrame(rows)
    worst_rows = _worst_episode_rows(frame)
    cycle_decomposition, overall_worst = _cycle_decomposition(frame)
    treasury_ext = _read_dataset(
        root,
        "fred_treasury_yield_ext_daily",
        ("date", "dgs3", "dgs5", "dtb3"),
    )
    carry, gap_rows, break_even = _carry_and_gap(treasury_ext, episodes, frame)
    peak_rows, peak_aggregate = _peak_timing(episodes, ladders, assets)
    treasury = _read_dataset(
        root,
        "fred_treasury_yield_daily",
        ("date", "dgs2", "dgs10", "dgs30"),
    )
    crisis_rows, confusion = _crisis_axis(treasury, episodes, ladders)
    observed_cycles = {episode.cycle for episode in episodes}.intersection(CYCLE_BUCKETS)
    base_summary_path = root / "artifacts/research/core_ammunition/summary.json"
    summary: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "core-ammunition-followup/v1",
        "development_only": True,
        "api_calls": 0,
        "quick": quick,
        "drawdown_threshold": decided_drawdown,
        "disp60_threshold": decided_disp60,
        "product_share_at_max": decided_share,
        "levels": decided_levels,
        "base_exposure": decided_base_exposure,
        "decision_clock": "T close descriptive mark; earliest signal-based action is after T close",
        "input_manifest_sha256": manifest["sha256"],
        "input_file_count": manifest["file_count"],
        "base_summary_sha256": hashlib.sha256(base_summary_path.read_bytes()).hexdigest(),
        "episode_table_reused": True,
        "episodes": [episode.to_dict() for episode in episodes],
        "path_summary": _path_summary(frame),
        "worst_episodes": worst_rows,
        "same_episode_30y": _same_episode_check(frame, worst_rows),
        "cycle_decomposition": cycle_decomposition,
        "overall_worst_origin": overall_worst,
        "quantiles": _quantile_and_classification(frame),
        "carry_advantage": carry,
        "episode_gap_years": gap_rows,
        "break_even": break_even,
        "reit_horizon": _reit_horizon(frame),
        "peak_timing": {"episodes": peak_rows, "aggregate": peak_aggregate},
        "crisis_axis": {"episodes": crisis_rows, "confusion": confusion},
        "limitations": {
            "predeclared_cycle_buckets": len(CYCLE_BUCKETS),
            "observed_independent_cycle_clusters": len(observed_cycles),
            "synchronous_cross_market_clusters": ["2008–09 금융위기", "2020 코로나"],
            "inflation_cluster_observations": 1,
        },
    }
    _validate_followup(root, manifest, episodes, rows, summary, quick=quick)

    output = root / "artifacts/research/core_ammunition/followup"
    _write_json(output / "input_manifest.json", manifest)
    _write_json(output / "summary.json", summary)
    _write_csv(output / "episode_horizons.csv", rows)
    _write_csv(output / "peak_timing.csv", peak_rows)
    _write_csv(output / "crisis_types.csv", crisis_rows)
    _write_text(
        root / "docs/research/RESULTS_20260905_core_ammunition_followup.md",
        _followup_markdown(_json_value(summary)),
    )
    print(
        f"DONE core-ammunition-followup/v1 episodes={len(episodes)} assets={len(assets)} "
        f"manifest={manifest['sha256'][:12]} quick={quick}"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--quick", action="store_true", help="Use only the latest three episodes per market.")
    parser.add_argument("--drawdown-threshold", type=float, default=None)
    parser.add_argument("--disp60-threshold", type=float, default=None)
    parser.add_argument(
        "--product-share-at-max",
        type=float,
        default=None,
        help="Required leveraged-product portfolio weight at the highest ladder level.",
    )
    parser.add_argument(
        "--levels",
        type=int,
        help="Required caller-selected ladder step count (1..4); no code default.",
    )
    parser.add_argument(
        "--base-exposure",
        type=float,
        help="Required caller-selected base exposure in [0, 3]; no code default.",
    )
    parser.add_argument(
        "--followup",
        action="store_true",
        help="Run the eight-question retained-data follow-up and fixed crisis-type check.",
    )
    args = parser.parse_args()
    if args.followup:
        run_followup(
            args.project_root,
            quick=args.quick,
            drawdown_threshold=args.drawdown_threshold,
            disp60_threshold=args.disp60_threshold,
            product_share_at_max=args.product_share_at_max,
            levels=args.levels,
            base_exposure=args.base_exposure,
        )
    else:
        run(
            args.project_root,
            quick=args.quick,
            drawdown_threshold=args.drawdown_threshold,
            disp60_threshold=args.disp60_threshold,
            product_share_at_max=args.product_share_at_max,
            levels=args.levels,
            base_exposure=args.base_exposure,
        )


if __name__ == "__main__":
    main()
