"""Canonical, traceable representation of one historical game state."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

CLOCK_PATTERN = re.compile(r"^PT(?P<minutes>\d+)M(?P<seconds>\d+(?:\.\d+)?)S$")
REGULATION_PERIOD_SECONDS = 12 * 60


def parse_clock_seconds(clock: str) -> float:
    """Convert a historical ISO-8601 period clock to seconds remaining."""
    match = CLOCK_PATTERN.fullmatch(str(clock).strip())
    if match is None:
        raise ValueError(f"Unparseable NBA clock: {clock!r}")
    seconds = int(match.group("minutes")) * 60 + float(match.group("seconds"))
    if not 0 <= seconds <= REGULATION_PERIOD_SECONDS:
        raise ValueError(f"Clock outside an NBA period: {clock!r}")
    return seconds


def regulation_seconds_remaining(period: int, seconds_remaining_period: float) -> float:
    """Return regulation time remaining using only the current period.

    Regulation quarters contribute their current clock plus all *scheduled*
    later regulation quarters. Overtime never contributes future periods: once
    regulation has ended this value is zero, regardless of how many overtime
    periods the game eventually contains.
    """
    if period < 1:
        raise ValueError(f"Period must be positive, got {period}")
    if period <= 4:
        return (4 - period) * REGULATION_PERIOD_SECONDS + seconds_remaining_period
    return 0.0


@dataclass(frozen=True, slots=True)
class GameState:
    """Post-event state plus source linkage and the game-level outcome label."""

    season: str
    game_id: str
    source_event_index: int
    action_number: int | None
    action_id: str | None
    clock: str
    action_type: str
    sub_type: str
    semantic_event: str
    description: str
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int
    score_differential: int
    period: int
    seconds_remaining_period: float
    seconds_remaining_regulation: float
    is_overtime: int
    overtime_number: int
    possession_team_id: int | None
    home_possession: int | None
    possession_known: int
    possession_reason: str
    home_team_fouls_period: int
    away_team_fouls_period: int
    foul_marker_team_count: int | None
    foul_marker_matches: int | None
    home_win: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
