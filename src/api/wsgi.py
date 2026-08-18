"""Production WSGI application imported by Gunicorn."""

import logging
import os

from src.api.app import create_app


def _configure_application_logging() -> None:
    level_name = os.environ.get("GUNICORN_LOG_LEVEL", "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    gunicorn_error = logging.getLogger("gunicorn.error")
    if gunicorn_error.handlers:
        root.handlers = gunicorn_error.handlers
    elif not root.handlers:
        logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root.setLevel(level)


_configure_application_logging()
app = create_app()
