"""Semantic comparison of historical and live-format reconstructed states."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

PARITY_FIELDS = (
    "period",
    "clock",
    "home_score",
    "away_score",
    "score_differential",
    "seconds_remaining_period",
    "seconds_remaining_regulation",
    "is_overtime",
    "overtime_number",
    "home_team_fouls_period",
    "away_team_fouls_period",
    "possession_team_id",
    "possession_known",
)


@dataclass(frozen=True)
class FieldParity:
    matches: int
    compared: int
    percent: float


@dataclass(frozen=True)
class StateParityReport:
    game_id: str
    historical_states: int
    live_states: int
    aligned_action_states: int
    historical_final_score: tuple[int, int]
    live_final_score: tuple[int, int]
    field_parity: dict[str, FieldParity]
    semantic_anchor_matches: int
    historical_semantic_anchors: int
    live_semantic_anchors: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["historical_final_score"] = list(self.historical_final_score)
        payload["live_final_score"] = list(self.live_final_score)
        return payload


def _equal_with_null(left: pd.Series, right: pd.Series) -> pd.Series:
    return left.eq(right).fillna(False) | (left.isna() & right.isna())


def _state_set(frame: pd.DataFrame, columns: tuple[str, ...]) -> set[tuple[Any, ...]]:
    sentinel = "__UNKNOWN__"
    values = frame.loc[:, columns].astype(object).where(frame.loc[:, columns].notna(), sentinel)
    return set(map(tuple, values.to_numpy()))


def compare_state_frames(
    historical: pd.DataFrame,
    live: pd.DataFrame,
    *,
    game_id: str,
) -> StateParityReport:
    """Align emitted states by shared source action number and compare semantics."""
    if historical.empty or live.empty:
        raise ValueError("Parity comparison requires non-empty historical and live states")
    historical_actions = historical.drop_duplicates("action_number", keep="last").set_index("action_number")
    live_actions = live.drop_duplicates("action_number", keep="last").set_index("action_number")
    aligned = historical_actions.index.intersection(live_actions.index)
    field_parity: dict[str, FieldParity] = {}
    for field in PARITY_FIELDS:
        equal = _equal_with_null(historical_actions.loc[aligned, field], live_actions.loc[aligned, field])
        matches = int(equal.sum())
        compared = int(len(equal))
        field_parity[field] = FieldParity(
            matches=matches,
            compared=compared,
            percent=(100.0 * matches / compared if compared else 0.0),
        )

    anchor_columns = ("period", "clock", "home_score", "away_score")
    historical_anchors = _state_set(historical, anchor_columns)
    live_anchors = _state_set(live, anchor_columns)
    return StateParityReport(
        game_id=str(game_id),
        historical_states=int(len(historical)),
        live_states=int(len(live)),
        aligned_action_states=int(len(aligned)),
        historical_final_score=(int(historical.iloc[-1]["home_score"]), int(historical.iloc[-1]["away_score"])),
        live_final_score=(int(live.iloc[-1]["home_score"]), int(live.iloc[-1]["away_score"])),
        field_parity=field_parity,
        semantic_anchor_matches=len(historical_anchors & live_anchors),
        historical_semantic_anchors=len(historical_anchors),
        live_semantic_anchors=len(live_anchors),
    )
