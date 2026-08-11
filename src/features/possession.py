"""Stateful, conservative possession reconstruction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.features.events import NormalizedEvent, POSSESSION_NEUTRAL_FOUL_SUBTYPES, normalize_event


@dataclass(frozen=True, slots=True)
class PossessionState:
    team_id: int | None
    reason: str


class PossessionEngine:
    """Infer post-event possession without alternating blindly.

    A pending missed attempt is retained across credit/substitution/replay rows,
    which is the essential protection against assigning a rebound from only the
    immediately preceding raw row.
    """

    def __init__(self, home_team_id: int, away_team_id: int) -> None:
        self.home_team_id = int(home_team_id)
        self.away_team_id = int(away_team_id)
        self.team_id: int | None = None
        self.reason = "game start unknown"
        self.pending_miss_team_id: int | None = None
        self.pending_miss_kind: str | None = None
        self.period: int | None = None

    def opponent(self, team_id: int | None) -> int | None:
        if team_id == self.home_team_id:
            return self.away_team_id
        if team_id == self.away_team_id:
            return self.home_team_id
        return None

    def _set(self, team_id: int | None, reason: str) -> None:
        self.team_id = team_id if team_id in {self.home_team_id, self.away_team_id} else None
        self.reason = reason

    def process(self, action: Mapping[str, Any]) -> PossessionState:
        event = normalize_event(action)
        period = int(action["period"])
        if period != self.period:
            self.period = period
            self.pending_miss_team_id = None
            self.pending_miss_kind = None
            self._set(None, "period start unknown")

        category = event.category
        team_id = event.team_id

        if category == "period":
            if event.sub_type.lower() == "end":
                self.pending_miss_team_id = None
                self.pending_miss_kind = None
                self._set(None, "period ended")

        elif category == "jump ball":
            # teamId is not the tip winner consistently; examples in the real
            # data attribute the same field differently. Wait for the next
            # possession-establishing basketball action.
            self.pending_miss_team_id = None
            self.pending_miss_kind = None
            self._set(None, "jump ball winner ambiguous")

        elif category == "made shot":
            self.pending_miss_team_id = None
            self.pending_miss_kind = None
            self._set(self.opponent(team_id), "made field goal")

        elif category == "missed shot":
            self.pending_miss_team_id = team_id
            self.pending_miss_kind = "field goal"
            self._set(team_id, "missed field goal awaiting rebound")

        elif category == "rebound":
            if self.pending_miss_team_id is None or team_id is None:
                self._set(None, "rebound ownership or associated miss ambiguous")
            elif team_id == self.pending_miss_team_id:
                self._set(team_id, "offensive rebound")
            else:
                self._set(team_id, "defensive rebound")
            self.pending_miss_team_id = None
            self.pending_miss_kind = None

        elif category == "turnover":
            self.pending_miss_team_id = None
            self.pending_miss_kind = None
            self._set(self.opponent(team_id), "turnover")

        elif category == "foul":
            subtype = event.sub_type.lower()
            if subtype in POSSESSION_NEUTRAL_FOUL_SUBTYPES:
                pass
            else:
                # For defensive/common fouls teamId is the fouling team; for
                # offensive/charge fouls the same opponent result is correct.
                self._set(self.opponent(team_id), f"{subtype or 'untyped'} foul")

        elif category == "free throw":
            subtype = event.sub_type.lower()
            is_technical = "technical" in subtype
            is_retained_ball = "flagrant" in subtype or "clear path" in subtype

            if is_technical:
                # Technical shots do not decide possession and their misses can
                # produce dead-ball team-rebound rows that must not be treated
                # as live rebounds.
                self.pending_miss_team_id = None
                self.pending_miss_kind = None
            elif event.free_throw_attempt is None or event.free_throw_total is None:
                self._set(None, "unparseable free throw sequence")
            elif event.free_throw_attempt < event.free_throw_total:
                self.pending_miss_team_id = None
                self.pending_miss_kind = None
                self._set(team_id, "free throw sequence continues")
            elif is_retained_ball:
                self.pending_miss_team_id = None
                self.pending_miss_kind = None
                self._set(team_id, "special free throws retain possession")
            elif event.is_made:
                self.pending_miss_team_id = None
                self.pending_miss_kind = None
                self._set(self.opponent(team_id), "final free throw made")
            else:
                self.pending_miss_team_id = team_id
                self.pending_miss_kind = "free throw"
                self._set(team_id, "final free throw missed awaiting rebound")

        return PossessionState(self.team_id, self.reason)
