from __future__ import annotations

import argparse
import json
from pathlib import Path

from stock_data.audit.manual_krx_derivatives_investor import (
    audit_retained_inventory,
    build_inventory,
    retain_inventory,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--retain", action="store_true", help="copy exact files into immutable manual Landing")
    parser.add_argument("--audit-inventory-sha256", help="independently audit one retained inventory")
    parser.add_argument("--write-audit", action="store_true")
    args = parser.parse_args()
    if args.audit_inventory_sha256:
        result = audit_retained_inventory(
            args.project_root, args.audit_inventory_sha256, write=args.write_audit
        )
    else:
        result = retain_inventory(args.project_root) if args.retain else build_inventory(args.project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
