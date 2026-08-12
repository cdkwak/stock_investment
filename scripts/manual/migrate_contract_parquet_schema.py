from __future__ import annotations

import argparse
import json
from pathlib import Path

from stock_data.migrations.contract_schema import MIGRATION_SPECS, run_schema_migration


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline allowlisted Parquet schema migration")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--dataset", choices=sorted(MIGRATION_SPECS), required=True)
    parser.add_argument("--mode", choices=("verify", "dry-run", "apply"), required=True)
    parser.add_argument("--confirm-schema-only-migration")
    args = parser.parse_args()
    result = run_schema_migration(
        project_root=args.project_root,
        dataset=args.dataset,
        mode=args.mode,
        confirmation=args.confirm_schema_only_migration,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
