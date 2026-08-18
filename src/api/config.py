"""Environment-backed application configuration without secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Mapping


def _boolean(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean configuration value: {value!r}")


@dataclass(frozen=True, slots=True)
class AppConfig:
    mode: str = "replay"
    environment: str = "development"
    polling_interval_seconds: float = 5.0
    replay_interval_seconds: float = 0.5
    replay_speed: float = 4.0
    host: str = "127.0.0.1"
    port: int = 5000
    debug: bool = False
    testing: bool = False
    start_background_tasks: bool = True
    endpoint_timeout_seconds: int = 15
    scoreboard_refresh_seconds: float = 30.0
    probability_history_limit: int = 240
    recent_events_limit: int = 12

    def __post_init__(self) -> None:
        normalized_mode = self.mode.strip().lower()
        if normalized_mode not in {"live", "replay"}:
            raise ValueError("NBA_APP_MODE must be 'live' or 'replay'")
        object.__setattr__(self, "mode", normalized_mode)
        normalized_environment = self.environment.strip().lower()
        if normalized_environment not in {"development", "production", "test"}:
            raise ValueError("NBA_APP_ENV must be 'development', 'production', or 'test'")
        object.__setattr__(self, "environment", normalized_environment)
        if normalized_environment == "production" and self.debug:
            raise ValueError("Debug mode cannot be enabled in production")
        if self.polling_interval_seconds < 2.0:
            raise ValueError("Live polling interval must be at least 2 seconds")
        if self.replay_interval_seconds <= 0:
            raise ValueError("Replay interval must be positive")
        if not 0.25 <= self.replay_speed <= 20.0:
            raise ValueError("Replay speed must be between 0.25x and 20x")
        if not 1 <= self.port <= 65535:
            raise ValueError("Port is outside the valid range")
        if self.probability_history_limit < 10 or self.recent_events_limit < 1:
            raise ValueError("History limits are too small")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AppConfig":
        values = os.environ if environ is None else environ
        return cls(
            mode=values.get("NBA_APP_MODE", "replay"),
            environment=values.get("NBA_APP_ENV", "development"),
            polling_interval_seconds=float(values.get("NBA_POLL_INTERVAL_SECONDS", "5")),
            replay_interval_seconds=float(values.get("NBA_REPLAY_INTERVAL_SECONDS", "0.5")),
            replay_speed=float(values.get("NBA_REPLAY_SPEED", "4")),
            host=values.get("NBA_APP_HOST", values.get("HOST", "127.0.0.1")),
            port=int(values.get("NBA_APP_PORT", values.get("PORT", "5000"))),
            debug=_boolean(values.get("NBA_APP_DEBUG"), False),
        )

    def with_overrides(self, overrides: Mapping[str, Any] | None) -> "AppConfig":
        if not overrides:
            return self
        allowed = {field for field in self.__dataclass_fields__}
        unknown = set(overrides) - allowed
        if unknown:
            raise KeyError(f"Unknown application configuration: {sorted(unknown)}")
        return replace(self, **dict(overrides))
