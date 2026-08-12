"""Offline CLI for the read-only A007 artifact audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from stock_data.audit.a007_short_selling import DATASETS, audit_a007, render_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--dataset", action="append", choices=DATASETS, dest="datasets")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit_a007(args.project_root, args.datasets or DATASETS)
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.format == "json" else render_markdown(report)
    )
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
