from __future__ import annotations

import argparse
from pathlib import Path

from stock_data.pipelines.tossinvest_historical import (
    build_treasury_from_landing,
    rebuild_treasury_from_landing_atomic,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or rebuild Toss Korean treasury data from retained landing only."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--expected-files", type=int, default=60)
    parser.add_argument("--expected-rows", type=int, default=11_162)
    parser.add_argument("--expected-partitions", type=int, default=48)
    args = parser.parse_args()

    if args.promote:
        backup = rebuild_treasury_from_landing_atomic(
            args.project_root,
            expected_files=args.expected_files,
            expected_rows=args.expected_rows,
            expected_partitions=args.expected_partitions,
        )
        print(f"promoted backup={backup}")
    else:
        frame = build_treasury_from_landing(
            args.project_root,
            expected_files=args.expected_files,
            expected_rows=args.expected_rows,
        )
        print(
            f"validated rows={len(frame)} "
            f"availability_nulls={int(frame['availability_date'].isna().sum())}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
