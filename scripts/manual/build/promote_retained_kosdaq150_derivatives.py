from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.pipelines.retained_derivatives_promotion import (  # noqa: E402
    promote_retained_kosdaq150,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote retained KOSDAQ150 derivative Landing JSON without network access."
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--options-input", type=Path, required=True)
    parser.add_argument("--futures-input", type=Path, required=True)
    parser.add_argument("--state-path", type=Path)
    args = parser.parse_args()
    state = promote_retained_kosdaq150(
        project_root=args.project_root,
        options_input=args.options_input,
        futures_input=args.futures_input,
        state_path=args.state_path,
    )
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
