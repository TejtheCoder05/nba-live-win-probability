"""Current-scoreboard retrieval and stable home/away normalization."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from nba_api.live.nba.endpoints import scoreboard

from src.live.errors import LiveSchemaError
from src.live.headers import live_request_headers

logger = logging.getLogger(__name__)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


@dataclass(frozen=True, slots=True)
class LiveTeam:
    team_id: int | None
    name: str
    city: str
    tricode: str
    score: int | None


@dataclass(frozen=True, slots=True)
class LiveGame:
    game_id: str
    game_status: int | None
    game_status_text: str
    period: int
    clock: str
    home_team: LiveTeam
    away_team: LiveTeam

    @property
    def is_scheduled(self) -> bool:
        return self.game_status == 1

    @property
    def is_active(self) -> bool:
        return self.game_status == 2

    @property
    def is_final(self) -> bool:
        return self.game_status == 3


@dataclass(frozen=True, slots=True)
class ScoreboardSnapshot:
    game_date: str
    games: tuple[LiveGame, ...]
    raw: Mapping[str, Any]
    error: str | None = None


def _normalize_team(raw: Any) -> LiveTeam:
    team = raw if isinstance(raw, Mapping) else {}
    return LiveTeam(
        team_id=_optional_int(team.get("teamId")),
        name=_text(team.get("teamName")),
        city=_text(team.get("teamCity")),
        tricode=_text(team.get("teamTricode")),
        score=_optional_int(team.get("score")),
    )


def normalize_game(raw: Mapping[str, Any]) -> LiveGame:
    game_id = _text(raw.get("gameId"))
    if not game_id:
        raise LiveSchemaError("Scoreboard game is missing gameId")
    return LiveGame(
        game_id=game_id,
        game_status=_optional_int(raw.get("gameStatus")),
        game_status_text=_text(raw.get("gameStatusText")),
        period=_optional_int(raw.get("period")) or 0,
        clock=_text(raw.get("gameClock")),
        home_team=_normalize_team(raw.get("homeTeam")),
        away_team=_normalize_team(raw.get("awayTeam")),
    )


def normalize_scoreboard(raw: Mapping[str, Any]) -> ScoreboardSnapshot:
    board = raw.get("scoreboard")
    if not isinstance(board, Mapping):
        raise LiveSchemaError("Live scoreboard response is missing scoreboard object")
    games = board.get("games", [])
    if games is None:
        games = []
    if not isinstance(games, list):
        raise LiveSchemaError("scoreboard.games must be a list")
    return ScoreboardSnapshot(
        game_date=_text(board.get("gameDate")),
        games=tuple(normalize_game(game) for game in games if isinstance(game, Mapping)),
        raw=raw,
    )


def normalize_game_details(raw: Mapping[str, Any]) -> LiveGame:
    """Normalize the live BoxScore/game-details shape used by replay fixtures."""
    game = raw.get("game")
    if not isinstance(game, Mapping):
        raise LiveSchemaError("Live game-details response is missing game object")
    return normalize_game(game)


def fetch_current_scoreboard(
    *,
    timeout: int = 15,
    endpoint_factory: Callable[..., Any] = scoreboard.ScoreBoard,
) -> ScoreboardSnapshot:
    """Fetch today's board; endpoint failures become an inspectable empty result."""
    try:
        endpoint = endpoint_factory(timeout=timeout, headers=live_request_headers())
        raw = endpoint.get_dict()
        if not isinstance(raw, Mapping):
            raise LiveSchemaError("Live scoreboard endpoint returned a non-object response")
        return normalize_scoreboard(raw)
    except Exception as exc:  # external boundary must not take down a caller
        logger.warning("Live scoreboard unavailable: %s", exc)
        return ScoreboardSnapshot(game_date="", games=(), raw={}, error=f"{type(exc).__name__}: {exc}")
