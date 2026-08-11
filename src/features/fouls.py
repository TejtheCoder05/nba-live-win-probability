"""Period team-foul reconstruction and independent marker validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.features.events import foul_counts_toward_team_total, foul_marker, normalize_event


@dataclass(frozen=True, slots=True)
class FoulState:
    home: int
    away: int
    marker_count: int | None
    marker_matches: int | None


class TeamFoulTracker:
    def __init__(self, home_team_id: int, away_team_id: int) -> None:
        self.home_team_id = int(home_team_id)
        self.away_team_id = int(away_team_id)
        self.period: int | None = None
        self.home = 0
        self.away = 0

    def process(self, action: Mapping[str, Any]) -> FoulState:
        period = int(action["period"])
        if period != self.period:
            self.period = period
            self.home = 0
            self.away = 0

        event = normalize_event(action)
        if foul_counts_toward_team_total(event):
            if event.team_id == self.home_team_id:
                self.home += 1
            elif event.team_id == self.away_team_id:
                self.away += 1

        marker = foul_marker(event.description) if event.category == "foul" else None
        matches: int | None = None
        if marker is not None and event.team_id in {self.home_team_id, self.away_team_id}:
            reconstructed = self.home if event.team_id == self.home_team_id else self.away
            matches = int(marker == reconstructed)

        return FoulState(self.home, self.away, marker, matches)
