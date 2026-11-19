"""Run Tapline: python -m tapline"""
from __future__ import annotations

import argparse
import socket
import threading
import webbrowser

import uvicorn

from .server import create_app


def _free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port between {start} and {start + 19}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="tapline", description="Sniff, decode and test serial links.")
    ap.add_argument("--port", type=int, default=8765, help="web port (default 8765)")
    ap.add_argument("--project", default="Default", help="project name (folder under ~/Tapline)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = ap.parse_args(argv)
    try:
        import websockets  # noqa: F401  (uvicorn needs it for the live connection)
    except ImportError:
        try:
            import wsproto  # noqa: F401
        except ImportError:
            print("\n  Tapline needs the 'websockets' package for live updates. Install it with:\n"
                  "      py -m pip install -r requirements.txt\n  (or: pip install websockets), then start Tapline again.\n")
            raise SystemExit(1)
    port = _free_port(args.port)
    url = f"http://localhost:{port}"
    print(f"\n  Tapline is running at {url}\n  Press Ctrl+C to quit.\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(project=args.project), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
