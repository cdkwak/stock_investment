from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.research.rule_leaderboard import run_rule_leaderboard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the retained-data rule leaderboard")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    latest, dated, payload = run_rule_leaderboard(args.project_root)
    print(json.dumps({
        "status": "PASS",
        "latest": str(latest),
        "dated": str(dated),
        "rules_version": payload["rules_version"],
        "candidates": len(payload["candidates"]),
        "api_calls": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
