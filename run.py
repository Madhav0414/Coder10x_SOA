"""Start the Prahari central station.

    python run.py              # opens http://127.0.0.1:8600 in your browser
    python run.py --port 9000
    python run.py --no-browser
"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="Prahari central station")
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"Prahari: calibrating twins, then serving {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(4.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("prahari.api:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
