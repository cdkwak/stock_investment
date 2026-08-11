from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.audit.dataset_inventory import (  # noqa: E402
    build_inventory,
    render_markdown,
    serialize_json,
    write_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic read-only Data-layer Parquet/state inventory."
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--max-key-rows", type=int, default=1_000_000)
    parser.add_argument("--max-scan-rows", type=int, default=1_000_000)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--format", choices=("json", "markdown", "both"), default="both")
    args = parser.parse_args()
    report = build_inventory(
        args.project_root,
        max_key_rows=args.max_key_rows,
        max_scan_rows=args.max_scan_rows,
    )
    write_outputs(
        report,
        json_output=args.json_output.resolve() if args.json_output else None,
        markdown_output=args.markdown_output.resolve() if args.markdown_output else None,
    )
    if args.json_output is None and args.markdown_output is None:
        if args.format in ("json", "both"):
            print(serialize_json(report), end="")
        if args.format == "both":
            print("---")
        if args.format in ("markdown", "both"):
            print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


