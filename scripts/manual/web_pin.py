"""Set or clear the optional remote web-dashboard PIN."""
from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from stock_web.auth import clear_pin, set_pin  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="로컬 웹 대시보드의 Tailscale 접속용 PIN을 관리합니다.",
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    set_command = commands.add_parser("set", help="PIN을 설정하거나 교체합니다.")
    set_command.add_argument(
        "--pin-stdin",
        action="store_true",
        help="대화형 프롬프트 대신 표준 입력의 첫 줄에서 PIN을 읽습니다.",
    )
    commands.add_parser("clear", help="PIN 잠금을 해제합니다.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.project_root.resolve()
    if args.command == "clear":
        removed = clear_pin(root)
        print("웹 대시보드 PIN을 해제했습니다." if removed else "설정된 웹 대시보드 PIN이 없습니다.")
        return 0

    pin = sys.stdin.readline().rstrip("\r\n") if args.pin_stdin else getpass.getpass("새 PIN (4~12자): ")
    try:
        set_pin(root, pin)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("웹 대시보드 PIN을 저장했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
