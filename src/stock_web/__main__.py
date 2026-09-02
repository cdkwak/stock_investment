from __future__ import annotations

import argparse

import uvicorn

from stock_web.app import create_app


def _ensure_streams() -> None:
    """Under pythonw.exe (no console) stdout/stderr are None; route them to a log file."""
    import sys
    from pathlib import Path

    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = Path(__file__).resolve().parents[2] / "artifacts" / "runtime_logs" / "web"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = open(log_dir / "stock_web.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def main() -> int:
    _ensure_streams()
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
