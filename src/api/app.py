"""Flask application factory for the NBA win-probability product layer."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from flask import Flask
from flask_socketio import SocketIO

from src.api.config import AppConfig
from src.api.routes import blueprint
from src.api.socket_events import register_socket_events
from src.live.service import GameService
from src.models.inference import WinProbabilityPredictor
from src.paths import MODEL_ARTIFACT_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)


def create_app(
    overrides: Mapping[str, Any] | None = None,
    *,
    predictor: WinProbabilityPredictor | None = None,
    service_options: Mapping[str, Any] | None = None,
) -> Flask:
    config = AppConfig.from_env().with_overrides(overrides)
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config.update(TESTING=config.testing)

    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins=None)
    logger.info("Loading frozen predictor from %s", MODEL_ARTIFACT_DIR)
    frozen_predictor = predictor or WinProbabilityPredictor.load(MODEL_ARTIFACT_DIR)
    logger.info("Frozen predictor loaded with %d features", len(frozen_predictor.preprocessor.feature_names))
    service = GameService(config, frozen_predictor, socketio, **dict(service_options or {}))
    app.extensions["socketio"] = socketio
    app.extensions["game_service"] = service
    app.extensions["application_config"] = config
    app.register_blueprint(blueprint)
    register_socket_events(socketio, service)
    logger.info("Application initialized mode=%s", config.mode)
    return app


def get_socketio(app: Flask) -> SocketIO:
    return app.extensions["socketio"]


if __name__ == "__main__":
    application = create_app()
    settings = application.extensions["application_config"]
    get_socketio(application).run(
        application,
        host=settings.host,
        port=settings.port,
        debug=settings.debug,
        allow_unsafe_werkzeug=True,
    )
