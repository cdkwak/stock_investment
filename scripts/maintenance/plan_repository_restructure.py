"""Generate deterministic, read-only move maps for tests and manual scripts."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re


SKIP_PARTS = {".git", ".venv", ".worktrees", "data", "artifacts", "docs/archive", "__pycache__"}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".txt", ".yml", ".yaml", ".json"}


def _category(name: str, *, test: bool) -> str:
    lowered = name.lower()
    if test:
        if any(word in lowered for word in ("historical", "backfill", "migration")):
            return "historical"
        if any(word in lowered for word in ("regression", "semantics", "audit")):
            return "regression"
        if any(word in lowered for word in ("pipeline", "incremental", "collection", "refresh", "snapshot")):
            return "integration"
        for domain in ("contract", "provider", "orchestration", "storage", "validation", "derived", "gui", "backtest", "feature"):
            if domain in lowered:
                return f"unit/{domain}s"
        return "unit/other"
    for prefix, category in (
        (("collect", "capture"), "collect"), (("backfill",), "backfill"),
        (("audit",), "audit"), (("diagnose", "diagnostic", "validate"), "diagnostic"),
        (("pilot", "probe"), "pilot"), (("migrate", "migration"), "migration"),
        (("repair",), "repair"), (("build", "rebuild", "refresh", "promote"), "build"),
    ):
        if lowered.startswith(prefix):
            return category
    return "research"


def _text_files(root: Path) -> list[tuple[Path, str]]:
    output = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(relative == part or relative.startswith(part + "/") for part in SKIP_PARTS):
            continue
        try:
            output.append((path, path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return output


def build_map(root: Path, sources: list[Path], *, test: bool) -> list[dict[str, str]]:
    texts = _text_files(root)
    rows = []
    proposed_counts: dict[str, int] = {}
    for source in sources:
        category = _category(source.name, test=test)
        base = "tests" if test else "scripts/manual"
        proposed = f"{base}/{category}/{source.name}"
        proposed_counts[proposed.casefold()] = proposed_counts.get(proposed.casefold(), 0) + 1
        references = []
        relative_source = source.relative_to(root)
        needles = {
            source.name,
            source.stem,
            relative_source.as_posix(),
            ".".join(relative_source.with_suffix("").parts),
        }
        for candidate, body in texts:
            if candidate == source:
                continue
            if any(needle in body for needle in needles):
                references.append(candidate.relative_to(root).as_posix())
        own = source.read_text(encoding="utf-8", errors="ignore")
        fixture_tokens = sorted(set(re.findall(r"(?:fixtures?|samples?)[/\\][A-Za-z0-9_./\\-]+", own)))
        rows.append({
            "current_path": source.relative_to(root).as_posix(),
            "category": category,
            "proposed_path": proposed,
            "fixture_dependencies": ";".join(fixture_tokens),
            "references": ";".join(sorted(references)),
            "reference_count": str(len(references)),
            "collision": "",
            "safe_move": "NO_REVIEW_REQUIRED" if references or fixture_tokens else "CANDIDATE",
        })
    for row in rows:
        row["collision"] = "YES" if proposed_counts[row["proposed_path"].casefold()] > 1 else "NO"
        if row["collision"] == "YES":
            row["safe_move"] = "NO_COLLISION"
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(); root = args.project_root.resolve()
    output = (args.output_root or root / "artifacts/agent_runs/restructure_maps").resolve()
    output.mkdir(parents=True, exist_ok=True)
    groups = {
        "tests_move_map.csv": build_map(root, sorted((root / "tests").rglob("test_*.py")), test=True),
        "manual_scripts_move_map.csv": build_map(root, sorted((root / "scripts/manual").rglob("*.py")), test=False),
    }
    for name, rows in groups.items():
        with (output / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
    print({name: len(rows) for name, rows in groups.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
