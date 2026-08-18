"""Single-process threaded Gunicorn configuration for Flask-SocketIO."""

from __future__ import annotations

import os

bind = f"{os.environ.get('NBA_APP_HOST', os.environ.get('HOST', '0.0.0.0'))}:{os.environ.get('NBA_APP_PORT', os.environ.get('PORT', '5000'))}"
worker_class = "gthread"
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT_SECONDS", "120"))
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
capture_output = True


def worker_exit(_server, _worker) -> None:
    """Signal any active per-game pollers during graceful worker shutdown."""
    from src.api.wsgi import app

    service = app.extensions.get("game_service")
    if service is not None:
        service.shutdown()
