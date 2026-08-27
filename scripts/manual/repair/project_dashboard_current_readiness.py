"""Project approved local current-state facts into a runbook-gated readiness CSV."""
from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path
from stock_data.orchestration.dashboard_current_readiness_projector import CSV_PATH, project

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--audit-at", required=True, help="Timezone-aware ISO-8601 clock")
    parser.add_argument("--backup", type=Path, help="New durable preimage path, required for production")
    parser.add_argument("--confirm-nonproduction-projector", action="store_true")
    parser.add_argument("--confirm-production-projector", action="store_true")
    args = parser.parse_args()
    production = args.csv == CSV_PATH
    if production and not args.confirm_production_projector:
        parser.error("--confirm-production-projector is required for the owned production CSV")
    if production and args.backup is None:
        parser.error("--backup is required for the owned production CSV")
    if not production and not args.confirm_nonproduction_projector:
        parser.error("--confirm-nonproduction-projector is required")
    print(project(args.project_root, now=datetime.fromisoformat(args.audit_at), csv_path=args.csv, production_confirmed=args.confirm_production_projector, backup_path=args.backup))
    return 0

if __name__ == "__main__": raise SystemExit(main())
