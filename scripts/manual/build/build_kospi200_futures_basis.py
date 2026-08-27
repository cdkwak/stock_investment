from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.derived.kospi200_futures_basis import (  # noqa: E402
    build_kospi200_futures_nearest_listed,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the offline KOSPI200 nearest-listed futures/basis dataset "
            "without network calls or expiry inference."
        )
    )
    parser.add_argument("--bridge-root", type=Path, required=True)
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--official-root", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data/derived/kr_kospi200_futures_nearest_listed_daily",
    )
    parser.add_argument(
        "--output-state",
        type=Path,
        default=ROOT / "data/state/kospi200_futures_nearest_listed_daily.json",
    )
    args = parser.parse_args()
    result = build_kospi200_futures_nearest_listed(
        bridge_root=args.bridge_root.resolve(),
        legacy_root=args.legacy_root.resolve(),
        official_root=args.official_root.resolve(),
        output_root=args.output_root.resolve(),
        output_state_path=args.output_state.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

