from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from stock_data.storage.backup_restore import (  # noqa: E402
    create_backup,
    load_plan,
    restore_verified_to_staging,
    verify_backup,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, verify, or restore a bounded local data backup without production promotion."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create an immutable, manifest-bound backup version.")
    create.add_argument("--source-root", type=Path, required=True)
    create.add_argument("--backup-root", type=Path, required=True)
    create.add_argument("--plan", type=Path, required=True)
    create.add_argument("--source-label", default="stock_investment_rev1")
    create.add_argument("--max-files", type=int, default=500)
    create.add_argument("--max-bytes", type=int, default=2 * 1024 * 1024 * 1024)

    verify = subparsers.add_parser("verify", help="Verify the latest or a named immutable backup.")
    verify.add_argument("--backup-root", type=Path, required=True)
    verify.add_argument("--manifest-sha256")

    restore = subparsers.add_parser(
        "restore-staging", help="Restore only to a new isolated staging directory; never promote."
    )
    restore.add_argument("--backup-root", type=Path, required=True)
    restore.add_argument("--staging-destination", type=Path, required=True)
    restore.add_argument("--manifest-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "create":
        result = create_backup(
            source_root=args.source_root,
            backup_root=args.backup_root,
            items=load_plan(args.plan),
            source_label=args.source_label,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
        )
        output = {
            "status": "VERIFIED",
            "manifest_sha256": result.manifest_sha256,
            "file_count": result.file_count,
            "total_bytes": result.total_bytes,
            "reused_existing": result.reused_existing,
        }
    elif args.command == "verify":
        manifest = verify_backup(args.backup_root, args.manifest_sha256)
        output = {"status": "VERIFIED", "totals": manifest["totals"]}
    else:
        destination = restore_verified_to_staging(
            backup_root=args.backup_root,
            staging_destination=args.staging_destination,
            manifest_sha256=args.manifest_sha256,
        )
        output = {
            "status": "RESTORED_TO_ISOLATED_STAGING",
            "staging_destination": str(destination),
            "production_promotion_authorized": False,
        }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
