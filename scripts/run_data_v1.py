import argparse
import json
from pathlib import Path

from stock_data.orchestration.data_v1_runner import run_phase


def main():
    parser = argparse.ArgumentParser(description="Conservative Data v1 phase runner")
    parser.add_argument("--phase", type=int, choices=range(0, 7), action="append")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--max-calls", type=int, default=1)
    parser.add_argument("--live", action="store_true", help="explicitly permit approved official API calls")
    parser.add_argument("--no-live", action="store_true")
    parser.add_argument("--skip-krx", action=argparse.BooleanOptionalAction, default=True,
                        help="keep every KRX provider disabled (default: true)")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.max_calls < 1:
        parser.error("--max-calls must be positive")
    root = Path(__file__).resolve().parents[1]
    if args.status:
        states = sorted((root/"data/state").glob("*.json")) if (root/"data/state").exists() else []
        print(json.dumps({"checkpoints":[path.name for path in states]}, ensure_ascii=False)); return
    live = args.live and not args.no_live
    phases = args.phase or list(range(0, 7))
    results = []
    for phase in phases:
        effective_calls = min(args.max_calls, 2) if args.smoke_only else args.max_calls
        results.append(run_phase(root, phase, live=live, resume=args.resume,
                                 max_calls=effective_calls, skip_krx=args.skip_krx))
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
