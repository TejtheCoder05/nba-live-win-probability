#!/usr/bin/env python3
"""Run the local Flask-SocketIO application in live or replay mode."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api import create_app, get_socketio  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "replay"), default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    overrides = {
        key: value
        for key, value in {"mode": args.mode, "host": args.host, "port": args.port}.items()
        if value is not None
    }
    if args.debug:
        overrides["debug"] = True
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(overrides)
    config = app.extensions["application_config"]
    try:
        get_socketio(app).run(
            app,
            host=config.host,
            port=config.port,
            debug=config.debug,
            allow_unsafe_werkzeug=True,
        )
    finally:
        app.extensions["game_service"].shutdown()


if __name__ == "__main__":
    main()
