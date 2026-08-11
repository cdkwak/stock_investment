from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.published.kospi200_derivatives_bridge import (  # noqa: E402
    build_kospi200_derivatives_bridge,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build offline Published provider-boundary KOSPI200 futures/options "
            "unions without defining a continuous-roll rule."
        )
    )
    parser.add_argument("--legacy-futures-root", type=Path, required=True)
    parser.add_argument("--official-futures-root", type=Path, required=True)
    parser.add_argument("--legacy-options-root", type=Path, required=True)
    parser.add_argument("--official-options-root", type=Path, required=True)
    parser.add_argument(
        "--output-bundle-root",
        type=Path,
        default=ROOT / "data/published/c007_kospi200_derivatives_bridge",
    )
    parser.add_argument(
        "--output-state",
        type=Path,
        default=ROOT / "data/state/kospi200_derivatives_bridge_2010_present.json",
    )
    args = parser.parse_args()
    result = build_kospi200_derivatives_bridge(
        legacy_futures_root=args.legacy_futures_root.resolve(),
        official_futures_root=args.official_futures_root.resolve(),
        legacy_options_root=args.legacy_options_root.resolve(),
        official_options_root=args.official_options_root.resolve(),
        output_bundle_root=args.output_bundle_root.resolve(),
        output_state_path=args.output_state.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
