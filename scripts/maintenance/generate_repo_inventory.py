"""Print a deterministic, bounded repository file inventory.

This utility reports names and types only. It never opens dataset payloads, follows
links, calls a provider, writes a file, or assigns project/dataset status.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


EXCLUDED_NAMES = {
    ".git",
    ".pytest_cache",
    ".venv",
    ".worktrees",
    "__pycache__",
}
DATA_DIRECTORY_DEPTH = 2


def _kind(entry: os.DirEntry[str]) -> str:
    if entry.is_symlink():
        return "link"
    if entry.is_dir(follow_symlinks=False):
        return "directory"
    return "file"


def build_inventory(root: Path, *, max_depth: int = 3) -> dict[str, object]:
    root = root.resolve()
    entries: list[dict[str, str]] = []

    def visit(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda item: item.name.casefold())
        for child in children:
            if child.name in EXCLUDED_NAMES or child.name.endswith(".egg-info"):
                continue
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            kind = _kind(child)
            entries.append({"path": relative, "kind": kind})
            if kind != "directory":
                continue
            if relative.startswith("data/"):
                continue
            if depth < max_depth:
                visit(path, depth + 1)

    visit(root, 1)
    return {
        "classification": "GENERATED_LOCATION_INVENTORY_NOT_STATUS",
        "root": ".",
        "max_depth": max_depth,
        "data_depth": DATA_DIRECTORY_DEPTH,
        "entries": entries,
    }


def render_markdown(inventory: dict[str, object]) -> str:
    lines = [
        "# Generated repository inventory",
        "",
        "> GENERATED_LOCATION_INVENTORY_NOT_STATUS. Names only; no dataset payloads were read.",
        "",
        "```text",
    ]
    for entry in inventory["entries"]:
        assert isinstance(entry, dict)
        path = str(entry["path"])
        suffix = "/" if entry["kind"] == "directory" else ""
        lines.append(f"{path}{suffix}")
    lines.extend(["```", ""])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_depth < 1:
        raise SystemExit("--max-depth must be at least 1")
    inventory = build_inventory(args.project_root, max_depth=args.max_depth)
    if args.format == "json":
        print(json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(inventory), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
