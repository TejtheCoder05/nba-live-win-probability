"""Translate observed NBA live actions into the shared historical action contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.features.events import clean_text, normalize_team_id
from src.live.errors import LiveSchemaError
from src.live.play_by_play import LivePlayByPlaySnapshot, parse_live_play_by_play


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _foul_subtype(action: Mapping[str, Any]) -> str:
    subtype = clean_text(action.get("subType")).lower()
    descriptor = clean_text(action.get("descriptor")).lower()
    if subtype == "offensive" and descriptor == "charge":
        return "Offensive Charge"
    if subtype == "offensive":
        return "Offensive"
    if subtype == "personal" and descriptor == "shooting":
        return "Shooting"
    if subtype == "personal" and descriptor == "loose ball":
        return "Loose Ball"
    if subtype == "personal":
        return "Personal"
    return " ".join(part for part in (descriptor, subtype) if part).title()


def _free_throw_subtype(action: Mapping[str, Any]) -> str:
    subtype = clean_text(action.get("subType"))
    descriptor = clean_text(action.get("descriptor"))
    qualifiers = " ".join(str(item) for item in (action.get("qualifiers") or []))
    special = " ".join(part for part in (descriptor, qualifiers) if part)
    special_tokens = [token for token in ("technical", "flagrant", "clear path") if token in special.lower()]
    prefix = " ".join(special_tokens)
    return " ".join(part for part in ("Free Throw", prefix, subtype) if part)


def _canonical_type_and_subtype(action: Mapping[str, Any]) -> tuple[str, str]:
    raw_type = clean_text(action.get("actionType")).lower()
    raw_subtype = clean_text(action.get("subType"))
    if raw_type in {"2pt", "3pt"}:
        result = clean_text(action.get("shotResult")).lower()
        return ("Made Shot" if result == "made" else "Missed Shot", raw_subtype)
    if raw_type == "freethrow":
        return "Free Throw", _free_throw_subtype(action)
    if raw_type == "rebound":
        return "Rebound", raw_subtype.title()
    if raw_type == "turnover":
        return "Turnover", raw_subtype.title()
    if raw_type == "foul":
        return "Foul", _foul_subtype(action)
    if raw_type == "jumpball":
        return "Jump Ball", raw_subtype.title()
    if raw_type == "period":
        return "period", raw_subtype.lower()
    if raw_type == "substitution":
        return "Substitution", raw_subtype
    if raw_type == "timeout":
        return "Timeout", raw_subtype.title()
    if raw_type == "violation":
        return "Violation", raw_subtype.title()
    # Live emits block/steal credit and display/stoppage rows separately.
    # The historical contract represents these as metadata with blank type;
    # the shared normalizer still identifies BLOCK/STEAL descriptions as credit.
    return "", ""


@dataclass(frozen=True, slots=True)
class CanonicalLiveEvent:
    game_id: str
    source_event_index: int
    sequence: int
    event_id: str
    period: int
    clock: str
    team_id: int | None
    team_tricode: str
    action_type: str
    sub_type: str
    description: str
    score_home: str
    score_away: str
    shot_result: str
    source_possession_team_id: int | None
    source_action_type: str
    source_action_subtype: str
    original_source: str = "nba_api.live.PlayByPlay"

    def as_processor_action(self) -> dict[str, Any]:
        """Return the exact source-neutral mapping consumed by the shared engine."""
        return {
            "gameId": self.game_id,
            "actionNumber": self.sequence,
            "actionId": self.event_id,
            "clock": self.clock,
            "period": self.period,
            "teamId": self.team_id,
            "teamTricode": self.team_tricode,
            "actionType": self.action_type,
            "subType": self.sub_type,
            "description": self.description,
            "scoreHome": self.score_home,
            "scoreAway": self.score_away,
            "shotResult": self.shot_result,
            "originalSource": self.original_source,
            "sourceActionType": self.source_action_type,
            "sourceActionSubtype": self.source_action_subtype,
            "sourcePossessionTeamId": self.source_possession_team_id,
            "sourceEventIndex": self.source_event_index,
        }


def adapt_live_action(game_id: str, action: Mapping[str, Any], source_index: int) -> CanonicalLiveEvent:
    period = _optional_int(action.get("period"))
    clock = clean_text(action.get("clock"))
    if period is None or period < 1:
        raise LiveSchemaError(f"Live action {source_index} has invalid period")
    if not clock:
        raise LiveSchemaError(f"Live action {source_index} is missing clock")
    action_type, sub_type = _canonical_type_and_subtype(action)
    sequence = _optional_int(action.get("actionNumber"))
    order_number = _optional_int(action.get("orderNumber"))
    if sequence is None:
        sequence = order_number if order_number is not None else source_index
    event_id = str(order_number if order_number is not None else sequence)
    return CanonicalLiveEvent(
        game_id=game_id,
        source_event_index=source_index,
        sequence=sequence,
        event_id=event_id,
        period=period,
        clock=clock,
        team_id=normalize_team_id(action.get("teamId")),
        team_tricode=clean_text(action.get("teamTricode")),
        action_type=action_type,
        sub_type=sub_type,
        description=clean_text(action.get("description")),
        score_home=clean_text(action.get("scoreHome")),
        score_away=clean_text(action.get("scoreAway")),
        shot_result=clean_text(action.get("shotResult")),
        source_possession_team_id=normalize_team_id(action.get("possession")),
        source_action_type=clean_text(action.get("actionType")),
        source_action_subtype=clean_text(action.get("subType")),
    )


def adapt_live_actions(game_id: str, actions: Sequence[Mapping[str, Any]]) -> tuple[CanonicalLiveEvent, ...]:
    """Deduplicate identities, apply corrections, and sort by live orderNumber."""
    latest: dict[tuple[Any, ...], tuple[int, Mapping[str, Any]]] = {}
    for source_index, action in enumerate(actions):
        order_number = _optional_int(action.get("orderNumber"))
        action_number = _optional_int(action.get("actionNumber"))
        identity = (
            "source-id",
            order_number,
            action_number,
        ) if order_number is not None or action_number is not None else ("source-index", source_index)
        latest[identity] = (source_index, action)
    ordered = sorted(
        latest.values(),
        key=lambda item: (
            _optional_int(item[1].get("orderNumber")) is None,
            _optional_int(item[1].get("orderNumber")) or item[0],
            item[0],
        ),
    )
    return tuple(adapt_live_action(game_id, action, source_index) for source_index, action in ordered)


def adapt_live_response(raw: Mapping[str, Any]) -> tuple[str, tuple[CanonicalLiveEvent, ...]]:
    snapshot: LivePlayByPlaySnapshot = parse_live_play_by_play(raw)
    return snapshot.game_id, adapt_live_actions(snapshot.game_id, snapshot.actions)
