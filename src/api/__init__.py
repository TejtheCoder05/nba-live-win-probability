"""Flask + SocketIO serving layer (Phase 6)."""
"""Flask and Socket.IO application package."""

from src.api.app import create_app, get_socketio

__all__ = ["create_app", "get_socketio"]
