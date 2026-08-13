from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.audit.stock_lending_evidence import (  # noqa: E402
    build_stock_lending_evidence_audit,
    upgrade_stock_lending_evidence_state,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit retained stock-lending evidence without API calls")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = (
        build_stock_lending_evidence_audit(args.project_root)
        if args.dry_run else upgrade_stock_lending_evidence_state(args.project_root)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
