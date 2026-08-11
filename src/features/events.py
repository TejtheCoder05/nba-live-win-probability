"""Normalize observed PlayByPlayV3 values into internal basketball semantics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

FREE_THROW_SEQUENCE = re.compile(r"(?P<attempt>\d+)\s+of\s+(?P<total>\d+)", re.IGNORECASE)
TEAM_FOUL_MARKER = re.compile(r"\.T(?P<count>\d+)\)", re.IGNORECASE)

# Verified against all three downloaded seasons. These fouls do not advance the
# period team-foul counter encoded by the independent ``T#`` description marker.
NON_TEAM_FOUL_SUBTYPES = {
    "offensive",
    "offensive charge",
    "technical",
    "double technical",
    "delay technical",
    "non-unsportsmanlike technical",
    "hanging technical",
    "too many players technical",
    "excess timeout technical",
    "defense 3 second",
    "flopping",
    "bench",
}

POSSESSION_NEUTRAL_FOUL_SUBTYPES = {
    "technical",
    "double technical",
    "delay technical",
    "non-unsportsmanlike technical",
    "hanging technical",
    "too many players technical",
    "excess timeout technical",
    "defense 3 second",
    "flopping",
    "bench",
}


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_team_id(value: Any) -> int | None:
    try:
        team_id = int(value)
    except (TypeError, ValueError):
        return None
    return team_id if team_id > 0 else None


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    action_type: str
    sub_type: str
    category: str
    team_id: int | None
    description: str
    is_made: bool | None
    free_throw_attempt: int | None
    free_throw_total: int | None
    is_special_free_throw: bool


def normalize_event(action: Mapping[str, Any]) -> NormalizedEvent:
    action_type = clean_text(action.get("actionType"))
    sub_type = clean_text(action.get("subType"))
    description = clean_text(action.get("description"))
    lowered_type = action_type.lower()

    if not lowered_type:
        upper_description = description.upper()
        category = "credit" if " STEAL" in upper_description or " BLOCK" in upper_description else "metadata"
    else:
        category = lowered_type

    is_made: bool | None = None
    if category == "free throw":
        shot_result = clean_text(action.get("shotResult")).lower()
        is_made = shot_result == "made" if shot_result else not description.upper().startswith("MISS ")

    sequence = FREE_THROW_SEQUENCE.search(sub_type)
    attempt = int(sequence.group("attempt")) if sequence else None
    total = int(sequence.group("total")) if sequence else None
    special = any(token in sub_type.lower() for token in ("technical", "flagrant", "clear path"))

    return NormalizedEvent(
        action_type=action_type,
        sub_type=sub_type,
        category=category,
        team_id=normalize_team_id(action.get("teamId")),
        description=description,
        is_made=is_made,
        free_throw_attempt=attempt,
        free_throw_total=total,
        is_special_free_throw=special,
    )


def foul_counts_toward_team_total(event: NormalizedEvent) -> bool:
    return event.category == "foul" and event.sub_type.lower() not in NON_TEAM_FOUL_SUBTYPES


def foul_marker(description: str) -> int | None:
    match = TEAM_FOUL_MARKER.search(description)
    return int(match.group("count")) if match else None


def score_snapshot_is_authoritative(event: NormalizedEvent) -> bool:
    """Whether a nonblank score snapshot may update reconstructed score.

    The three-season audit found stale snapshots on replay outcomes that
    explicitly *support* or leave a ruling standing. Those events cannot
    logically remove points. An overturn can, so overturn replay snapshots
    remain authoritative. Period-end handling is conditional in the processor:
    it may confirm/recover a score but may not decrease one. Scoring actions
    remain authoritative because their snapshots sometimes carry legitimate
    earlier score corrections.
    """
    if event.category == "instant replay" and "overturn" not in event.sub_type.lower():
        return False
    return True
