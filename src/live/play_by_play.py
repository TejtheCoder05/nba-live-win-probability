"""Reliable one-shot retrieval and raw caching for NBA live play-by-play."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from nba_api.live.nba.endpoints import playbyplay

from src.live.errors import LiveEndpointError, LiveSchemaError
from src.live.headers import live_request_headers
from src.paths import LIVE_DATA_DIR, ensure_dir

logger = logging.getLogger(__name__)
GAME_ID_PATTERN = re.compile(r"^\d{10}$")


def validate_game_id(game_id: str) -> str:
    normalized = str(game_id).strip()
    if not GAME_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid NBA game ID: {game_id!r}")
    return normalized


def response_fingerprint(raw: Mapping[str, Any]) -> str:
    encoded = json.dumps(raw, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LivePlayByPlaySnapshot:
    game_id: str
    actions: tuple[Mapping[str, Any], ...]
    raw: Mapping[str, Any]
    fingerprint: str
    changed: bool


def parse_live_play_by_play(
    raw: Mapping[str, Any], *, requested_game_id: str | None = None, changed: bool = True
) -> LivePlayByPlaySnapshot:
    game = raw.get("game")
    if not isinstance(game, Mapping):
        raise LiveSchemaError("Live play-by-play response is missing game object")
    game_id = validate_game_id(str(game.get("gameId", "")))
    if requested_game_id is not None and game_id != validate_game_id(requested_game_id):
        raise LiveSchemaError(f"Requested game {requested_game_id}, received {game_id}")
    actions = game.get("actions")
    if actions is None:
        actions = []
    if not isinstance(actions, list) or any(not isinstance(action, Mapping) for action in actions):
        raise LiveSchemaError("game.actions must be a list of objects")
    copied = deepcopy(raw)
    copied_actions = tuple(copied["game"].get("actions", []))
    return LivePlayByPlaySnapshot(
        game_id=game_id,
        actions=copied_actions,
        raw=copied,
        fingerprint=response_fingerprint(copied),
        changed=changed,
    )


def cache_live_play_by_play(raw: Mapping[str, Any], game_id: str, directory: Path | None = None) -> Path:
    """Atomically cache a live-format response outside historical raw storage."""
    target_directory = ensure_dir(directory or (LIVE_DATA_DIR / "playbyplay"))
    path = target_directory / f"{validate_game_id(game_id)}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


class LivePlayByPlayClient:
    """One-shot client with request spacing and change detection, not a poll loop."""

    def __init__(
        self,
        *,
        timeout: int = 15,
        minimum_request_interval: float = 1.0,
        endpoint_factory: Callable[..., Any] = playbyplay.PlayByPlay,
    ) -> None:
        self.timeout = timeout
        self.minimum_request_interval = max(0.0, float(minimum_request_interval))
        self.endpoint_factory = endpoint_factory
        self._last_request_at: float | None = None
        self._fingerprints: dict[str, str] = {}

    def fetch(self, game_id: str, *, cache: bool = False) -> LivePlayByPlaySnapshot:
        normalized_id = validate_game_id(game_id)
        if self._last_request_at is not None:
            delay = self.minimum_request_interval - (time.monotonic() - self._last_request_at)
            if delay > 0:
                time.sleep(delay)
        try:
            endpoint = self.endpoint_factory(
                game_id=normalized_id, timeout=self.timeout, headers=live_request_headers()
            )
            self._last_request_at = time.monotonic()
            raw = endpoint.get_dict()
            if not isinstance(raw, Mapping):
                raise LiveSchemaError("Live play-by-play endpoint returned a non-object response")
            snapshot = parse_live_play_by_play(raw, requested_game_id=normalized_id)
        except Exception as exc:
            self._last_request_at = time.monotonic()
            if isinstance(exc, LiveSchemaError):
                raise
            raise LiveEndpointError(f"Could not fetch live play-by-play for {normalized_id}: {exc}") from exc

        prior = self._fingerprints.get(normalized_id)
        changed = prior != snapshot.fingerprint
        self._fingerprints[normalized_id] = snapshot.fingerprint
        snapshot = LivePlayByPlaySnapshot(
            game_id=snapshot.game_id,
            actions=snapshot.actions,
            raw=snapshot.raw,
            fingerprint=snapshot.fingerprint,
            changed=changed,
        )
        if cache:
            cache_live_play_by_play(snapshot.raw, normalized_id)
        return snapshot


def fetch_live_play_by_play(game_id: str, *, timeout: int = 15) -> LivePlayByPlaySnapshot:
    return LivePlayByPlayClient(timeout=timeout, minimum_request_interval=0).fetch(game_id)
