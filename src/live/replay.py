"""Deterministic full-feed replay through the frozen shared state engine."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.features.processor import GameProcessingResult, process_game
from src.live.adapter import CanonicalLiveEvent, adapt_live_actions
from src.live.play_by_play import LivePlayByPlaySnapshot, parse_live_play_by_play
from src.live.scoreboard import LiveGame
from src.models.dataset import V1_FEATURES
from src.models.inference import WinProbabilityPredictor


def model_input_from_state(
    state: Mapping[str, Any], feature_names: Sequence[str] = V1_FEATURES
) -> dict[str, float | int]:
    """Extract the exact frozen vector, preserving neutral unknown possession."""
    if tuple(feature_names) != tuple(V1_FEATURES):
        raise ValueError(f"Live feature order differs from frozen V1 order: {tuple(feature_names)}")
    missing = [name for name in feature_names if name not in state]
    if missing:
        raise KeyError(f"Live state is missing model features: {missing}")
    result: dict[str, float | int] = {}
    for name in feature_names:
        value = state[name]
        if name == "home_possession" and (value is None or pd.isna(value)):
            value = 0.5
        result[name] = float(value) if name in {"seconds_remaining_period", "seconds_remaining_regulation", "home_possession"} else int(value)
    values = np.asarray(list(result.values()), dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError("Live model input contains NaN or infinity")
    return result


@dataclass(frozen=True)
class ReplayResult:
    game_id: str
    adapted_events: tuple[CanonicalLiveEvent, ...]
    states: pd.DataFrame
    processing: GameProcessingResult
    fingerprint: str
    changed: bool
    probabilities: np.ndarray | None = None

    @property
    def latest_state(self) -> Mapping[str, Any] | None:
        return None if self.states.empty else self.states.iloc[-1].to_dict()


def replay_snapshot(
    snapshot: LivePlayByPlaySnapshot,
    game: LiveGame,
    *,
    predictor: WinProbabilityPredictor | None = None,
) -> ReplayResult:
    if snapshot.game_id != game.game_id:
        raise ValueError(f"Play-by-play game {snapshot.game_id} does not match metadata {game.game_id}")
    if game.home_team.team_id is None or game.away_team.team_id is None:
        raise ValueError("Live replay requires home and away team IDs")
    adapted = adapt_live_actions(snapshot.game_id, snapshot.actions)
    processor_actions = [event.as_processor_action() for event in adapted]
    states, processing = process_game(
        processor_actions,
        season="live",
        game_id=snapshot.game_id,
        home_team_id=game.home_team.team_id,
        away_team_id=game.away_team.team_id,
    )
    probabilities = None
    if predictor is not None and not states.empty:
        feature_names = predictor.preprocessor.feature_names
        inputs = [model_input_from_state(row, feature_names) for row in states.to_dict(orient="records")]
        probabilities = predictor.predict_batch(inputs)
    return ReplayResult(
        game_id=snapshot.game_id,
        adapted_events=adapted,
        states=states,
        processing=processing,
        fingerprint=snapshot.fingerprint,
        changed=snapshot.changed,
        probabilities=probabilities,
    )


def replay_live_response(
    raw: Mapping[str, Any],
    game: LiveGame,
    *,
    predictor: WinProbabilityPredictor | None = None,
) -> ReplayResult:
    return replay_snapshot(parse_live_play_by_play(raw), game, predictor=predictor)


class LiveReplayEngine:
    """Rebuild on changed feeds; return cached state for identical repeated polls."""

    def __init__(self, game: LiveGame, *, predictor: WinProbabilityPredictor | None = None) -> None:
        self.game = game
        self.predictor = predictor
        self._last: ReplayResult | None = None

    def update(self, raw: Mapping[str, Any]) -> ReplayResult:
        snapshot = parse_live_play_by_play(raw)
        if self._last is not None and snapshot.fingerprint == self._last.fingerprint:
            return replace(self._last, changed=False)
        result = replay_snapshot(snapshot, self.game, predictor=self.predictor)
        self._last = result
        return result
