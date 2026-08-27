"""Read-only audit of FinanceData/marcap against existing official KRX data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
MARKET_MAP = {"STK": "KOSPI", "KSQ": "KOSDAQ"}
NUMERIC_MAP = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "Amount": "trading_value",
    "Marcap": "market_cap",
    "Stocks": "listed_shares",
}
COMPARE_DATES = ("2010-01-04", "2015-01-02", "2019-01-02")


def _marcap_day(path: Path, date: str) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    frame = frame.loc[
        (frame["Date"] == pd.Timestamp(date)) & frame["MarketId"].isin(MARKET_MAP)
    ].copy()
    frame["market"] = frame["MarketId"].map(MARKET_MAP)
    frame["symbol"] = frame["Code"].astype(str).str.strip().str.zfill(6)
    frame["name"] = frame["Name"].astype(str).str.strip()
    for source, target in NUMERIC_MAP.items():
        frame[target] = pd.to_numeric(frame[source], errors="coerce").astype("Int64")
    return frame[
        ["market", "symbol", "name", *NUMERIC_MAP.values()]
    ].sort_values(["market", "symbol"], kind="stable")


def _official_normalized(date: str) -> pd.DataFrame:
    year = date[:4]
    frames: list[pd.DataFrame] = []
    for market in ("KOSPI", "KOSDAQ"):
        base = ROOT / "data" / "normalized"
        price = pd.read_parquet(
            base / "kr_equity_price_daily" / f"market={market}" / f"year={year}" / "data.parquet"
        )
        cap = pd.read_parquet(
            base / "kr_equity_market_cap_daily" / f"market={market}" / f"year={year}" / "data.parquet"
        )
        universe = pd.read_parquet(
            base / "kr_equity_universe_daily" / f"market={market}" / f"year={year}" / "data.parquet"
        )
        keys = ["date", "market", "symbol"]
        day = price.loc[price["date"].astype(str) == date].merge(
            cap.loc[cap["date"].astype(str) == date], on=keys, validate="one_to_one"
        ).merge(
            universe.loc[
                universe["date"].astype(str) == date,
                keys + ["name", "short_name"],
            ],
            on=keys,
            validate="one_to_one",
        )
        day = day.rename(columns={"shares_outstanding": "listed_shares"})
        frames.append(day)
    return pd.concat(frames, ignore_index=True)[
        ["market", "symbol", "name", "short_name", *NUMERIC_MAP.values()]
    ]


def _integer(value: object) -> int:
    return int(str(value).replace(",", ""))


def _official_landing(date: str) -> pd.DataFrame:
    compact = date.replace("-", "")
    output: list[dict[str, Any]] = []
    for prefix, market in (("stk", "KOSPI"), ("ksq", "KOSDAQ")):
        trade_path = ROOT / "data" / "landing" / "krx_open_api" / f"{prefix}_bydd_trd" / f"{compact}.json"
        basic_path = ROOT / "data" / "landing" / "krx_open_api" / f"{prefix}_isu_base_info" / f"{compact}.json"
        trades = json.loads(trade_path.read_text(encoding="utf-8"))["OutBlock_1"]
        basics = json.loads(basic_path.read_text(encoding="utf-8"))["OutBlock_1"]
        identities = {
            str(row["ISU_SRT_CD"]).strip().removeprefix("A").zfill(6): (
                str(row["ISU_NM"]).strip(), str(row["ISU_ABBRV"]).strip()
            )
            for row in basics
        }
        for row in trades:
            symbol = str(row["ISU_CD"]).strip().removeprefix("A").zfill(6)
            output.append(
                {
                    "market": market,
                    "symbol": symbol,
                    "name": identities.get(symbol, (str(row["ISU_NM"]).strip(), None))[0],
                    "short_name": identities.get(symbol, (None, str(row["ISU_NM"]).strip()))[1],
                    "open": _integer(row["TDD_OPNPRC"]),
                    "high": _integer(row["TDD_HGPRC"]),
                    "low": _integer(row["TDD_LWPRC"]),
                    "close": _integer(row["TDD_CLSPRC"]),
                    "volume": _integer(row["ACC_TRDVOL"]),
                    "trading_value": _integer(row["ACC_TRDVAL"]),
                    "market_cap": _integer(row["MKTCAP"]),
                    "listed_shares": _integer(row["LIST_SHRS"]),
                }
            )
    return pd.DataFrame(output)


def _comparison(marcap: pd.DataFrame, official: pd.DataFrame) -> dict[str, Any]:
    keys = ["market", "symbol"]
    marcap_keys = set(map(tuple, marcap[keys].itertuples(index=False, name=None)))
    official_keys = set(map(tuple, official[keys].itertuples(index=False, name=None)))
    joined = marcap.merge(
        official,
        on=keys,
        suffixes=("_marcap", "_krx"),
        validate="one_to_one",
    )
    mismatches: dict[str, int] = {}
    max_abs: dict[str, int | None] = {}
    short_name_mismatch = joined["name_marcap"] != joined["short_name"]
    formal_name_mismatch = joined["name_marcap"] != joined["name_krx"]
    mismatches["name_vs_krx_short_name"] = int(short_name_mismatch.sum())
    mismatches["name_vs_krx_formal_name"] = int(formal_name_mismatch.sum())
    max_abs["name_vs_krx_short_name"] = None
    max_abs["name_vs_krx_formal_name"] = None
    for field in NUMERIC_MAP.values():
        left = joined[f"{field}_marcap"]
        right = joined[f"{field}_krx"]
        mismatches[field] = int((left != right).sum())
        difference = (left.astype("Int64") - right.astype("Int64")).abs()
        max_abs[field] = int(difference.max()) if difference.notna().any() else None
    return {
        "marcap_count": len(marcap),
        "krx_count": len(official),
        "common": len(marcap_keys & official_keys),
        "marcap_only": len(marcap_keys - official_keys),
        "krx_only": len(official_keys - marcap_keys),
        "marcap_only_examples": sorted(marcap_keys - official_keys)[:10],
        "krx_only_examples": sorted(official_keys - marcap_keys)[:10],
        "mismatch_count": mismatches,
        "max_absolute_difference": max_abs,
    }


def _annual_quality(path: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    frame = pd.read_parquet(path)
    frame["normalized_symbol"] = frame["Code"].astype(str).str.strip().str.zfill(6)
    positive_ohlc = (frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
    high_bad = positive_ohlc & (
        frame["High"] < frame[["Open", "Low", "Close"]].max(axis=1)
    )
    low_bad = positive_ohlc & (
        frame["Low"] > frame[["Open", "High", "Close"]].min(axis=1)
    )
    relevant = [
        "Date", "Code", "Name", "MarketId", "Open", "High", "Low", "Close",
        "Volume", "Amount", "Marcap", "Stocks",
    ]
    quality = {
        "rows": len(frame),
        "trading_days": int(frame["Date"].nunique()),
        "first_date": frame["Date"].min().date().isoformat(),
        "last_date": frame["Date"].max().date().isoformat(),
        "unique_symbols": int(frame["normalized_symbol"].nunique()),
        "markets": {str(key): int(value) for key, value in frame["MarketId"].value_counts(dropna=False).items()},
        "duplicate_date_market_symbol": int(
            frame.duplicated(["Date", "MarketId", "normalized_symbol"]).sum()
        ),
        "raw_symbol_length": {
            str(key): int(value)
            for key, value in frame["Code"].astype(str).str.len().value_counts().sort_index().items()
        },
        "nonnumeric_symbols": int((~frame["Code"].astype(str).str.fullmatch(r"[0-9]+", na=False)).sum()),
        "missing": {column: int(frame[column].isna().sum()) for column in relevant},
        "negative": {
            column: int((pd.to_numeric(frame[column], errors="coerce") < 0).sum())
            for column in ("Open", "High", "Low", "Close", "Volume", "Amount", "Marcap", "Stocks")
        },
        "zero_ohlc_rows": int((frame[["Open", "High", "Low", "Close"]] == 0).any(axis=1).sum()),
        "positive_ohlc_high_violation": int(high_bad.sum()),
        "positive_ohlc_low_violation": int(low_bad.sum()),
    }
    return quality, frame


def _current_symbols() -> tuple[set[str], set[str]]:
    master_frames = [
        pd.read_parquet(ROOT / "data" / "normalized" / "kr_equity_master" / f"market={market}" / "data.parquet")
        for market in ("KOSPI", "KOSDAQ")
    ]
    master = set(pd.concat(master_frames, ignore_index=True)["symbol"].astype(str).str.zfill(6))
    current_frames: list[pd.DataFrame] = []
    for market in ("KOSPI", "KOSDAQ"):
        frame = pd.read_parquet(
            ROOT / "data" / "published" / "kr_equity_canonical_universe_daily"
            / f"market={market}" / "year=2026" / "data.parquet"
        )
        current_frames.append(frame.loc[frame["date"] == frame["date"].max()])
    current = set(pd.concat(current_frames, ignore_index=True)["symbol"].astype(str).str.zfill(6))
    return master, current


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("marcap_dir", type=Path)
    args = parser.parse_args()

    comparisons: dict[str, Any] = {}
    for date in COMPARE_DATES:
        year = date[:4]
        marcap = _marcap_day(args.marcap_dir / f"marcap-{year}.parquet", date)
        official = _official_landing(date) if year == "2019" else _official_normalized(date)
        comparisons[date] = _comparison(marcap, official)

    annual: dict[str, Any] = {}
    historical_frames: list[pd.DataFrame] = []
    for year in range(1995, 2010):
        quality, frame = _annual_quality(args.marcap_dir / f"marcap-{year}.parquet")
        annual[str(year)] = quality
        historical_frames.append(
            frame[["normalized_symbol", "Name", "MarketId", "Date"]].drop_duplicates()
        )
    history = pd.concat(historical_frames, ignore_index=True)
    master_symbols, current_symbols = _current_symbols()
    history_symbols = set(history["normalized_symbol"])
    priority = history.loc[
        history["Name"].astype(str).str.contains(r"우|우선", regex=True, na=False),
        ["normalized_symbol", "Name"],
    ].drop_duplicates()
    names_per_symbol = history.groupby("normalized_symbol")["Name"].nunique()
    markets_per_symbol = history.groupby("normalized_symbol")["MarketId"].nunique()
    historical_only = history.loc[
        ~history["normalized_symbol"].isin(current_symbols),
        ["normalized_symbol", "Name", "MarketId"],
    ].drop_duplicates()
    report = {
        "comparison_dates": comparisons,
        "annual_quality_1995_2009": annual,
        "coverage": {
            "first_date": min(value["first_date"] for value in annual.values()),
            "last_date": max(value["last_date"] for value in annual.values()),
            "total_rows": sum(value["rows"] for value in annual.values()),
            "total_trading_days": int(history["Date"].nunique()),
            "first_kosdaq_date": (
                history.loc[history["MarketId"] == "KSQ", "Date"].min().date().isoformat()
                if (history["MarketId"] == "KSQ").any()
                else None
            ),
        },
        "survivorship": {
            "historical_unique_symbols": len(history_symbols),
            "not_in_current_canonical": len(history_symbols - current_symbols),
            "not_in_current_master": len(history_symbols - master_symbols),
            "historical_only_examples": historical_only.head(20).to_dict("records"),
            "priority_like_symbol_count": int(priority["normalized_symbol"].nunique()),
            "priority_examples": priority.head(20).to_dict("records"),
            "symbols_with_multiple_names": int((names_per_symbol > 1).sum()),
            "symbols_with_multiple_markets": int((markets_per_symbol > 1).sum()),
        },
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
