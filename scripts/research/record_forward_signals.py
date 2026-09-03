from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.research.forward_test import record_forward_signals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record provider-free daily rule states")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--as-of")
    args = parser.parse_args(argv)
    result = record_forward_signals(args.project_root, as_of=args.as_of)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
