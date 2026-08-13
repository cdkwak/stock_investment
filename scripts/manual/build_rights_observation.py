from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from stock_data.providers.data_go_kr.rights_observation import promote_rights_diagnostic


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote one retained Rights diagnostic without source calls"
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--diagnostic-root", type=Path, required=True)
    args = parser.parse_args()
    result = promote_rights_diagnostic(
        project_root=args.project_root, diagnostic_root=args.diagnostic_root
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
