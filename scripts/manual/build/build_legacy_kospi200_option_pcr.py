from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.derived.kospi200_option_pcr import (  # noqa: E402
    build_legacy_kospi200_option_pcr,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build C002 PCR from C001 normalized KOSPI200 options."
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--legacy-root", type=Path, required=True)
    args = parser.parse_args()
    legacy_root = args.legacy_root.resolve()
    result = build_legacy_kospi200_option_pcr(
        project_root=args.project_root.resolve(),
        legacy_calendar_state_path=(
            legacy_root / "data/state/krx_derivatives_backfill.json"
        ),
        legacy_pcr_path=(
            legacy_root
            / "data/processed/kr/derivatives/kospi200_option_pcr_daily.csv"
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
