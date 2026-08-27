from __future__ import annotations

import argparse
import json
from pathlib import Path

from stock_data.pipelines.market_breadth_rebuild import DATASET, rebuild_market_breadth


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline retained-input market breadth rebuild")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("dry-run", "apply"), required=True)
    parser.add_argument("--confirm-rebuild")
    args = parser.parse_args()
    result = rebuild_market_breadth(
        project_root=args.project_root,
        mode=args.mode,
        confirmation=args.confirm_rebuild,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
