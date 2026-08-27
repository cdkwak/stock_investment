from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.pipelines.legacy_derivatives_migration import (  # noqa: E402
    run_legacy_derivatives_migration,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline, read-only-source migration of legacy KOSPI200 derivatives"
    )
    parser.add_argument(
        "--legacy-root", type=Path, required=True,
        help="Read-only path to the legacy Stock Investment repository",
    )
    parser.add_argument("--start", default="20100101")
    parser.add_argument("--end", default="20191231")
    parser.add_argument("--chunksize", type=int, default=100_000)
    args = parser.parse_args()
    result = run_legacy_derivatives_migration(
        project_root=ROOT,
        legacy_root=args.legacy_root,
        start=args.start,
        end=args.end,
        chunksize=args.chunksize,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
