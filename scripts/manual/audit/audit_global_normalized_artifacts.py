from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.audit.global_artifact_manifest import (  # noqa: E402
    CONTRACTS,
    build_global_artifact_audits,
    upgrade_global_artifact_audit_states,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit retained Yahoo/FRED Normalized artifacts without network access"
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--dataset", action="append", choices=sorted(CONTRACTS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run:
        result = build_global_artifact_audits(args.project_root, args.dataset)
    else:
        result = upgrade_global_artifact_audit_states(args.project_root, args.dataset)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
