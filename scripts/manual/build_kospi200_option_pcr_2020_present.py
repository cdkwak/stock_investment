from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.derived.kospi200_option_pcr_modern import (  # noqa: E402
    build_modern_kospi200_option_pcr,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build 2020+ C002-compatible KOSPI200 option PCR from existing "
            "normalized Parquet without source calls."
        )
    )
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--input-state", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data/derived/kr_kospi200_option_pcr_daily",
    )
    parser.add_argument(
        "--output-state",
        type=Path,
        default=ROOT / "data/state/kospi200_option_pcr_2020_present.json",
    )
    parser.add_argument("--prior-derived-root", type=Path, required=True)
    parser.add_argument("--start", default="20200101")
    parser.add_argument("--end")
    args = parser.parse_args()
    result = build_modern_kospi200_option_pcr(
        input_root=args.input_root.resolve(),
        input_state_path=args.input_state.resolve(),
        output_root=args.output_root.resolve(),
        output_state_path=args.output_state.resolve(),
        prior_derived_root=args.prior_derived_root.resolve(),
        start=args.start,
        end=args.end,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
