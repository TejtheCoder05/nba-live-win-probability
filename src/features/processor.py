"""Convert one raw historical game into leakage-safe post-event states."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import pandas as pd

from src.features.events import clean_text, normalize_event, score_snapshot_is_authoritative
from src.features.fouls import TeamFoulTracker
from src.features.game_state import GameState, parse_clock_seconds, regulation_seconds_remaining
from src.features.possession import PossessionEngine


@dataclass(slots=True)
class GameProcessingResult:
    game_id: str
    raw_events: int
    emitted_states: int
    known_possession_states: int
    unknown_possession_states: int
    final_score_matches: bool
    reconstructed_final_home_score: int
    reconstructed_final_away_score: int
    expected_final_home_score: int
    expected_final_away_score: int
    foul_markers_checked: int
    foul_markers_matched: int
    foul_marker_discrepancies: list[dict[str, Any]] = field(default_factory=list)
    possession_reasons: dict[str, int] = field(default_factory=dict)


def _score(value: Any, previous: int) -> int:
    text = clean_text(value)
    if not text:
        return previous
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"Invalid score value {value!r}") from exc


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    text = clean_text(value)
    return text or None


def _apply_nullable_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    nullable_ints = [
        "action_number",
        "possession_team_id",
        "home_possession",
        "foul_marker_team_count",
        "foul_marker_matches",
    ]
    for column in nullable_ints:
        frame[column] = frame[column].astype("Int64")
    return frame


def process_game(
    actions: Sequence[Mapping[str, Any]],
    *,
    season: str,
    game_id: str,
    home_team_id: int,
    away_team_id: int,
    expected_home_score: int,
    expected_away_score: int,
    home_win: int,
) -> tuple[pd.DataFrame, GameProcessingResult]:
    """Process one game in raw list order and return emitted states + metrics.

    The expected final score is not consulted until every event has been
    processed. It therefore validates reconstruction but cannot influence an
    intermediate feature.
    """
    possession = PossessionEngine(home_team_id, away_team_id)
    fouls = TeamFoulTracker(home_team_id, away_team_id)
    home_score = 0
    away_score = 0
    emitted: list[dict[str, Any]] = []
    previous_visible_state: tuple[Any, ...] | None = None
    marker_discrepancies: list[dict[str, Any]] = []
    marker_checked = 0
    marker_matched = 0
    possession_reasons: Counter[str] = Counter()
    max_action_number_by_clock: dict[tuple[int, str], int] = {}

    for source_index, action in enumerate(actions):
        period = int(action["period"])
        clock = clean_text(action.get("clock"))
        seconds_period = parse_clock_seconds(clock)
        event = normalize_event(action)
        action_number = _optional_int(action.get("actionNumber"))
        clock_key = (period, clock)
        prior_action_at_clock = max_action_number_by_clock.get(clock_key, -1)
        has_score_snapshot = bool(
            clean_text(action.get("scoreHome")) or clean_text(action.get("scoreAway"))
        )
        authoritative_score = score_snapshot_is_authoritative(event)
        out_of_order = (
            authoritative_score
            and has_score_snapshot
            and action_number is not None
            and action_number < prior_action_at_clock
        )
        if authoritative_score and has_score_snapshot and action_number is not None:
            max_action_number_by_clock[clock_key] = max(prior_action_at_clock, action_number)

        if not out_of_order and authoritative_score:
            candidate_home = _score(action.get("scoreHome"), home_score)
            candidate_away = _score(action.get("scoreAway"), away_score)
            if event.category != "period" or (
                candidate_home >= home_score and candidate_away >= away_score
            ):
                home_score = candidate_home
                away_score = candidate_away
        possession_state = possession.process(action)
        foul_state = fouls.process(action)

        if foul_state.marker_matches is not None:
            marker_checked += 1
            marker_matched += foul_state.marker_matches
            if not foul_state.marker_matches:
                marker_discrepancies.append(
                    {
                        "source_event_index": source_index,
                        "action_number": _optional_int(action.get("actionNumber")),
                        "period": period,
                        "clock": clock,
                        "team_id": event.team_id,
                        "sub_type": event.sub_type,
                        "description": event.description,
                        "marker": foul_state.marker_count,
                        "reconstructed": (
                            foul_state.home if event.team_id == int(home_team_id) else foul_state.away
                        ),
                    }
                )

        visible_state = (
            home_score,
            away_score,
            period,
            seconds_period,
            possession_state.team_id,
            foul_state.home,
            foul_state.away,
        )
        if visible_state == previous_visible_state:
            continue
        previous_visible_state = visible_state

        home_possession = None
        if possession_state.team_id == int(home_team_id):
            home_possession = 1
        elif possession_state.team_id == int(away_team_id):
            home_possession = 0

        possession_reasons[possession_state.reason] += 1
        state = GameState(
            season=season,
            game_id=str(game_id),
            source_event_index=source_index,
            action_number=action_number,
            action_id=_optional_text(action.get("actionId")),
            clock=clock,
            action_type=event.action_type,
            sub_type=event.sub_type,
            semantic_event=event.category,
            description=event.description,
            home_team_id=int(home_team_id),
            away_team_id=int(away_team_id),
            home_score=home_score,
            away_score=away_score,
            score_differential=home_score - away_score,
            period=period,
            seconds_remaining_period=seconds_period,
            seconds_remaining_regulation=regulation_seconds_remaining(period, seconds_period),
            is_overtime=int(period >= 5),
            overtime_number=max(0, period - 4),
            possession_team_id=possession_state.team_id,
            home_possession=home_possession,
            possession_known=int(possession_state.team_id is not None),
            possession_reason=possession_state.reason,
            home_team_fouls_period=foul_state.home,
            away_team_fouls_period=foul_state.away,
            foul_marker_team_count=foul_state.marker_count,
            foul_marker_matches=foul_state.marker_matches,
            home_win=int(home_win),
        )
        emitted.append(state.as_dict())

    frame = _apply_nullable_dtypes(pd.DataFrame(emitted))
    known = int(frame["possession_known"].sum()) if not frame.empty else 0
    final_matches = home_score == int(expected_home_score) and away_score == int(expected_away_score)
    result = GameProcessingResult(
        game_id=str(game_id),
        raw_events=len(actions),
        emitted_states=len(frame),
        known_possession_states=known,
        unknown_possession_states=len(frame) - known,
        final_score_matches=final_matches,
        reconstructed_final_home_score=home_score,
        reconstructed_final_away_score=away_score,
        expected_final_home_score=int(expected_home_score),
        expected_final_away_score=int(expected_away_score),
        foul_markers_checked=marker_checked,
        foul_markers_matched=marker_matched,
        foul_marker_discrepancies=marker_discrepancies,
        possession_reasons=dict(possession_reasons),
    )
    return frame, result
