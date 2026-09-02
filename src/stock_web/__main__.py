from __future__ import annotations

import argparse

import uvicorn

from stock_web.app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local read-only web dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code change (development)")
    args = parser.parse_args()
    if args.reload:
        uvicorn.run("stock_web.app:create_app", factory=True, host=args.host, port=args.port, reload=True)
    else:
        uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
