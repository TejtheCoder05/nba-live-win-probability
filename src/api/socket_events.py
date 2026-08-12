"""Socket.IO subscription and replay-control contract."""

from __future__ import annotations

from typing import Any

from flask import request
from flask_socketio import emit, join_room, leave_room

from src.live.play_by_play import validate_game_id


def register_socket_events(socketio: Any, service: Any) -> None:
    @socketio.on("connect")
    def connect():
        emit("game_status", {"status": "connected", "mode": service.mode})

    @socketio.on("subscribe_game")
    def subscribe_game(payload=None):
        game_id = (payload or {}).get("game_id", "")
        try:
            normalized = validate_game_id(game_id)
            if not service.game_exists(normalized):
                raise KeyError(normalized)
            join_room(normalized)
            state = service.subscribe(normalized, request.sid)
        except ValueError:
            emit("game_error", {"game_id": game_id, "message": "Invalid NBA game ID."})
            return
        except KeyError:
            emit("game_error", {"game_id": game_id, "message": "Game not found."})
            return
        emit("game_status", {"game_id": normalized, "status": "subscribed"})
        emit("game_state", state)

    @socketio.on("unsubscribe_game")
    def unsubscribe_game(payload=None):
        game_id = (payload or {}).get("game_id", "")
        try:
            normalized = validate_game_id(game_id)
        except ValueError:
            emit("game_error", {"game_id": game_id, "message": "Invalid NBA game ID."})
            return
        service.unsubscribe(normalized, request.sid)
        leave_room(normalized)
        emit("game_status", {"game_id": normalized, "status": "unsubscribed"})

    @socketio.on("replay_control")
    def replay_control(payload=None):
        body = payload or {}
        game_id = body.get("game_id", "")
        try:
            state = service.control_replay(
                game_id,
                body.get("action", ""),
                speed=body.get("speed"),
                steps=body.get("steps", 1),
            )
        except ValueError as exc:
            emit("game_error", {"game_id": game_id, "message": str(exc)})
            return
        except KeyError:
            emit("game_error", {"game_id": game_id, "message": "Replay game not found."})
            return
        emit("game_status", {"game_id": game_id, "status": "replay_updated"})

    @socketio.on("disconnect")
    def disconnect():
        service.disconnect(request.sid)
