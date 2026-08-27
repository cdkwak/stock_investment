from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from runtime_diagnostics import RuntimeDiagnosticStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="List sanitized local runtime failures.")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    store = RuntimeDiagnosticStore(
        args.project_root.resolve() / "artifacts/runtime_logs/application"
    )
    for event in store.latest(limit=args.limit):
        print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
