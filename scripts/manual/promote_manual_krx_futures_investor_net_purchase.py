from __future__ import annotations

import argparse
import json
from pathlib import Path

from stock_data.pipelines.manual_krx_futures_investor_net_purchase import (
    audit_promoted_history,
    promote_manual_history,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()
    function = audit_promoted_history if args.audit else promote_manual_history
    print(json.dumps(function(args.project_root, args.inventory_sha256), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
