from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.pipelines.legacy_market_investor_import import run_legacy_market_investor_import  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline fixed-scope legacy KOSPI investor import")
    parser.add_argument("--legacy-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_legacy_market_investor_import(project_root=ROOT, legacy_root=args.legacy_root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
