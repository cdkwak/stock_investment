"""Offline CLI for the read-only A007 artifact audit."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path

from stock_data.audit.a007_short_selling import (
    DATASETS,
    audit_a007,
    canonical_plan,
    render_markdown,
)


def _plan(value: str) -> tuple[str, date, date]:
    try:
        dataset, start, end = value.split(":", 2)
        if dataset not in DATASETS:
            raise ValueError
        return dataset, date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as error:
        raise argparse.ArgumentTypeError("plan must be DATASET:YYYY-MM-DD:YYYY-MM-DD") from error


def _terminal(value: str) -> tuple[str, str]:
    try:
        dataset, status = value.split(":", 1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("terminal status must be DATASET:STATUS") from error
    if dataset not in DATASETS or not status:
        raise argparse.ArgumentTypeError("terminal status must be DATASET:STATUS")
    return dataset, status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--dataset", action="append", choices=DATASETS, dest="datasets")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--plan", action="append", type=_plan, default=[], metavar="DATASET:START:END",
        help="Override a dataset terminal range; otherwise use the full canonical range.",
    )
    parser.add_argument(
        "--terminal-status", action="append", type=_terminal, default=[],
        metavar="DATASET:STATUS",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected = tuple(args.datasets or DATASETS)
    ranges = {}
    for dataset, start, end in args.plan:
        if dataset in ranges:
            raise SystemExit(f"duplicate --plan for {dataset}")
        ranges[dataset] = (start, end)
    terminal: dict[str, list[str]] = {}
    for dataset, status in args.terminal_status:
        terminal.setdefault(dataset, []).append(status)
    unknown = (set(ranges) | set(terminal)) - set(selected)
    if unknown:
        raise SystemExit(f"plan/status supplied for unselected dataset: {sorted(unknown)}")
    plans = {
        dataset: canonical_plan(
            args.project_root,
            dataset,
            start=ranges.get(dataset, (None, None))[0],
            end=ranges.get(dataset, (None, None))[1],
            acceptable_terminal_statuses=tuple(terminal.get(dataset, ("BATCH_COMPLETE",))),
        )
        for dataset in selected
    }
    report = audit_a007(args.project_root, selected, plans=plans)
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.format == "json" else render_markdown(report)
    )
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
