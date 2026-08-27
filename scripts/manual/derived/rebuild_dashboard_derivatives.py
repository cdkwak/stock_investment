from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.derived.kospi200_futures_basis import (  # noqa: E402
    build_kospi200_futures_nearest_listed,
)
from stock_data.derived.kospi200_option_pcr_modern import (  # noqa: E402
    build_modern_kospi200_option_pcr,
)
from stock_data.derived.option_walls import (  # noqa: E402
    PIT_SAFE_EOD_T_PLUS_1,
    compute_front_month_wall,
    compute_option_walls,
    join_kospi200_daily_index,
)
from stock_data.published.kospi200_derivatives_bridge import (  # noqa: E402
    build_kospi200_derivatives_bridge,
)


def _latest_wall(project_root: Path) -> dict:
    options_root = (
        project_root / "data/published/c007_kospi200_derivatives_bridge"
        / "kr_kospi200_options_provider_bridge_daily"
    )
    latest_partition = sorted(options_root.glob("year=*/data.parquet"))[-1]
    options = pd.read_parquet(latest_partition)
    options["date"] = pd.to_datetime(options["date"], errors="raise").dt.normalize()
    latest_date = options["date"].max()
    selected = options.loc[options["date"].eq(latest_date)].copy()
    walls = compute_front_month_wall(compute_option_walls(selected))

    index_path = (
        project_root / "data/normalized/kr_kospi200_index_daily"
        / f"year={latest_date.year}" / "data.parquet"
    )
    index_daily = pd.read_parquet(
        index_path, columns=["date", "symbol", "close", "source"]
    )
    joined = join_kospi200_daily_index(
        walls,
        index_daily,
        dataset_name="kr_kospi200_index_daily",
        symbol="KOSPI200",
        pit_status=PIT_SAFE_EOD_T_PLUS_1,
        require_complete=True,
    )
    if len(joined) != 1:
        raise RuntimeError("latest front-month wall must contain exactly one row")

    output = project_root / "artifacts/analysis/kospi200_option_wall_recent_250.csv"
    prior = pd.read_csv(output, parse_dates=["date"]) if output.exists() else pd.DataFrame()
    combined = pd.concat([prior, joined], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"], errors="raise").dt.normalize()
    combined = combined.sort_values("date").drop_duplicates("date", keep="last").tail(250)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".csv",
            prefix=f".{output.stem}.", dir=output.parent, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            combined.to_csv(handle, index=False)
        pd.read_csv(temporary, parse_dates=["date"])
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    row = joined.iloc[-1]
    return {
        "date": latest_date.date().isoformat(),
        "call_max_oi_strike": float(row["call_wall_strike"]),
        "put_max_oi_strike": float(row["put_wall_strike"]),
        "rows_retained": len(combined),
    }


def main() -> int:
    data = ROOT / "data"
    legacy_futures = data / "normalized/krx_legacy_kospi200_futures_daily"
    official_futures = data / "normalized/kr_kospi200_futures_daily"
    legacy_options = data / "normalized/krx_legacy_kospi200_options_daily"
    official_options = data / "normalized/kr_kospi200_options_daily"
    bundle = data / "published/c007_kospi200_derivatives_bridge"
    bridge = build_kospi200_derivatives_bridge(
        legacy_futures_root=legacy_futures,
        official_futures_root=official_futures,
        legacy_options_root=legacy_options,
        official_options_root=official_options,
        output_bundle_root=bundle,
        output_state_path=data / "state/kospi200_derivatives_bridge_2010_present.json",
    )
    basis = build_kospi200_futures_nearest_listed(
        bridge_root=bundle / "kr_kospi200_futures_provider_bridge_daily",
        legacy_root=legacy_futures,
        official_root=official_futures,
        output_root=data / "derived/kr_kospi200_futures_nearest_listed_daily",
        output_state_path=data / "state/kospi200_futures_nearest_listed_daily.json",
    )
    pcr_root = data / "derived/kr_kospi200_option_pcr_daily"
    with tempfile.TemporaryDirectory(prefix="kospi200-pcr-prior-") as temp_name:
        prior_root = Path(temp_name)
        for source in sorted(pcr_root.glob("year=20[01][0-9]/data.parquet")):
            target = prior_root / source.parent.name / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        pcr = build_modern_kospi200_option_pcr(
            input_root=official_options,
            input_state_path=data / "state/kr_kospi200_options_daily.json",
            output_root=pcr_root,
            output_state_path=data / "state/kospi200_option_pcr_2020_present.json",
            prior_derived_root=prior_root,
            start="20200101",
        )
    wall = _latest_wall(ROOT)
    print(json.dumps({
        "status": "COMPLETE",
        "api_calls": 0,
        "bridge_rows": {
            key: value["validation"]["rows"]
            for key, value in bridge["datasets"].items()
        },
        "basis": basis["validation"],
        "pcr": pcr["validation"],
        "wall": wall,
    }, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
