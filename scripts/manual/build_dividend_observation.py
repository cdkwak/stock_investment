"""Rebuild the dividend retained-snapshot observation from an explicit Landing file.

This command is offline-only: the input Landing path is required, and it does
not import or configure any data.go.kr client.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from stock_data.providers.data_go_kr.dividend_observation import (  # noqa: E402
    build_dividend_observation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an immutable dividend observation from retained Landing JSON (offline only)."
    )
    parser.add_argument(
        "--landing-path", type=Path, required=True,
        help="Required retained full_history.json input; no network fallback exists.",
    )
    parser.add_argument(
        "--output-root", type=Path, default=ROOT / "data" / "normalized",
        help="Normalized-data parent; default is <project-root>/data/normalized.",
    )
    parser.add_argument(
        "--state-path", type=Path,
        default=ROOT / "data" / "state" / "kr_equity_dividend_source_observation.json",
        help="Artifact checkpoint; default is under <project-root>/data/state.",
    )
    args = parser.parse_args(argv)
    result = build_dividend_observation(
        landing_path=args.landing_path,
        output_root=args.output_root,
        state_path=args.state_path,
    )
    print(json.dumps({
        "status": "ARTIFACT_COMPLETE",
        "landing_file_sha256": result.landing_file_sha256,
        "source_snapshot_date": result.source_snapshot_date,
        "response_count": result.response_count,
        "row_count": result.row_count,
        "output_root": str(result.output_root),
        "state_path": str(result.state_path),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
