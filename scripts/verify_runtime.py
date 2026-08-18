#!/usr/bin/env python3
"""Verify the minimal production filesystem and one authentic replay inference."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_model_artifact import main as verify_model  # noqa: E402
from src.api import create_app  # noqa: E402
from src.live.service import REPLAY_GAME_ID  # noqa: E402


def main() -> None:
    required = (
        "artifacts/win_probability_v1/best_model.pt",
        "artifacts/win_probability_v1/model_config.json",
        "artifacts/win_probability_v1/preprocessing.json",
        "tests/fixtures/live/playbyplay_0022000001.json",
        "tests/fixtures/live/game_details_0022000001.json",
        "templates/index.html",
        "static/js/app.js",
        "static/vendor/socket.io.min.js",
    )
    missing = [item for item in required if not (PROJECT_ROOT / item).is_file()]
    if missing:
        raise SystemExit(f"Missing runtime files: {missing}")
    prohibited = (PROJECT_ROOT / "data" / "raw", PROJECT_ROOT / "data" / "processed")
    included = [str(path.relative_to(PROJECT_ROOT)) for path in prohibited if path.exists()]
    if included:
        raise SystemExit(f"Training datasets must not be in the runtime image: {included}")
    if (PROJECT_ROOT / "static/vendor/socket.io.min.js").stat().st_size < 40_000:
        raise SystemExit("Socket.IO browser bundle is incomplete")

    verify_model()
    app = create_app({"testing": True, "start_background_tasks": False})
    try:
        state = app.extensions["game_service"].get_state(REPLAY_GAME_ID)
        if state is None or not 0.0 <= state["model_home_win_probability"] <= 1.0:
            raise SystemExit("Authentic replay did not produce a valid frozen-model prediction")
    finally:
        app.extensions["game_service"].shutdown()
    print("Runtime filesystem and replay inference verified")


if __name__ == "__main__":
    main()
