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

from stock_data.research.compound_ladder import LadderSpec, ladder_levels  # noqa: E402
from stock_data.research.condition_backtest import compute_signals  # noqa: E402
from stock_data.research.core_ammunition import (  # noqa: E402
    Episode,
    aggregate_values,
    cash_proxy_returns,
    classify_asset,
    cluster_level_two,
    duration_proxy_returns,
    measure_asset_episode,
    prepare_value_series,
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


def _episode_inputs(root: Path, quick: bool) -> tuple[list[Episode], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
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
    spec = LadderSpec(drawdown_threshold=-0.20, disp60_threshold=-0.10, levels=2)
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
    lines = [
        "# 낙폭 2단계의 코어 자산 실탄 조달력",
        "",
        "> 개발용 retained-data 기술통계다. T는 사다리 2단계가 종가에 관측된 같은 종가의 평가액이며, 그 신호를 보고 T 종가에 체결할 수 있었다는 뜻이 아니다.",
        "",
        f"- 규칙: `drawdown252 ≤ −20%`, `disp60 ≤ −10%`, 2단계; `compound_ladder.ladder_levels`의 `observed_level` 재사용",
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


def run(project_root: Path, *, quick: bool = False) -> dict[str, Any]:
    root = Path(project_root).resolve()
    manifest = _manifest(root)
    episodes, frames, ladders = _episode_inputs(root, quick)
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
            "id": "kr_dd_ladder_2",
            "drawdown252": -0.20,
            "disp60": -0.10,
            "levels": 2,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--quick", action="store_true", help="Use only the latest three episodes per market.")
    args = parser.parse_args()
    run(args.project_root, quick=args.quick)


if __name__ == "__main__":
    main()
