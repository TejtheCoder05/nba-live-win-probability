"""HTTP routes for the dashboard and JSON game API."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from src.live.play_by_play import validate_game_id

blueprint = Blueprint("application", __name__)


def _service() -> Any:
    return current_app.extensions["game_service"]


def _error(message: str, status: int):
    return jsonify({"error": message, "status": status}), status


@blueprint.get("/")
def dashboard():
    return render_template("index.html")


@blueprint.get("/api/health")
def health():
    return jsonify({"status": "ok", "mode": _service().mode})


@blueprint.get("/api/games")
def games():
    return jsonify(_service().list_games())


@blueprint.get("/api/games/<game_id>")
def game_state(game_id: str):
    try:
        normalized = validate_game_id(game_id)
    except ValueError:
        return _error("Invalid NBA game ID.", 400)
    state = _service().get_state(normalized)
    if state is None:
        return _error("Game not found.", 404)
    return jsonify(state)


@blueprint.post("/api/replay/<game_id>/<action>")
def replay_control(game_id: str, action: str):
    body = request.get_json(silent=True) or {}
    try:
        state = _service().control_replay(
            game_id,
            action,
            speed=body.get("speed"),
            steps=body.get("steps", 1),
        )
    except ValueError as exc:
        return _error(str(exc), 400)
    except KeyError:
        return _error("Replay game not found.", 404)
    return jsonify(state)
