from __future__ import annotations

from typing import Any

import pytest

from src.features.possession import PossessionEngine

HOME = 100
AWAY = 200


def event(
    action_type: str,
    team_id: int = 0,
    *,
    subtype: str = "",
    description: str = "",
    period: int = 1,
    shot_result: str = "",
) -> dict[str, Any]:
    return {
        "actionType": action_type,
        "subType": subtype,
        "teamId": team_id,
        "description": description,
        "period": period,
        "shotResult": shot_result,
    }


def test_made_basket_changes_possession() -> None:
    engine = PossessionEngine(HOME, AWAY)
    assert engine.process(event("Made Shot", HOME)).team_id == AWAY


def test_missed_shot_offensive_rebound_keeps_possession() -> None:
    engine = PossessionEngine(HOME, AWAY)
    engine.process(event("Missed Shot", HOME))
    state = engine.process(event("Rebound", HOME))
    assert state.team_id == HOME
    assert state.reason == "offensive rebound"


def test_missed_shot_defensive_rebound_changes_possession() -> None:
    engine = PossessionEngine(HOME, AWAY)
    engine.process(event("Missed Shot", HOME))
    state = engine.process(event("Rebound", AWAY))
    assert state.team_id == AWAY
    assert state.reason == "defensive rebound"


@pytest.mark.parametrize("credit", ["James BLOCK (1 BLK)", "Jones STEAL (1 STL)"])
def test_credit_row_between_miss_and_rebound_is_ignored(credit: str) -> None:
    engine = PossessionEngine(HOME, AWAY)
    engine.process(event("Missed Shot", HOME))
    engine.process(event("", AWAY, description=credit))
    assert engine.process(event("Rebound", AWAY)).reason == "defensive rebound"


def test_turnover_changes_possession() -> None:
    engine = PossessionEngine(HOME, AWAY)
    assert engine.process(event("Turnover", HOME)).team_id == AWAY


def test_multi_shot_free_throw_sequence_changes_only_after_final_make() -> None:
    engine = PossessionEngine(HOME, AWAY)
    first = engine.process(
        event("Free Throw", HOME, subtype="Free Throw 1 of 2", shot_result="Made")
    )
    final = engine.process(
        event("Free Throw", HOME, subtype="Free Throw 2 of 2", shot_result="Made")
    )
    assert first.team_id == HOME
    assert final.team_id == AWAY


def test_final_missed_free_throw_offensive_rebound() -> None:
    engine = PossessionEngine(HOME, AWAY)
    engine.process(
        event(
            "Free Throw",
            HOME,
            subtype="Free Throw 2 of 2",
            shot_result="Missed",
            description="MISS Home Free Throw 2 of 2",
        )
    )
    assert engine.process(event("Rebound", HOME)).reason == "offensive rebound"


def test_final_missed_free_throw_defensive_rebound() -> None:
    engine = PossessionEngine(HOME, AWAY)
    engine.process(
        event(
            "Free Throw",
            HOME,
            subtype="Free Throw 1 of 1",
            description="MISS Home Free Throw 1 of 1",
        )
    )
    assert engine.process(event("Rebound", AWAY)).reason == "defensive rebound"


def test_and_one_sequence() -> None:
    engine = PossessionEngine(HOME, AWAY)
    assert engine.process(event("Made Shot", HOME)).team_id == AWAY
    assert engine.process(event("Foul", AWAY, subtype="Shooting")).team_id == HOME
    assert engine.process(
        event("Free Throw", HOME, subtype="Free Throw 1 of 1", shot_result="Made")
    ).team_id == AWAY


def test_technical_free_throw_does_not_change_known_possession() -> None:
    engine = PossessionEngine(HOME, AWAY)
    engine.process(event("Made Shot", AWAY))
    before = engine.team_id
    after = engine.process(
        event("Free Throw", AWAY, subtype="Free Throw Technical", shot_result="Made")
    )
    assert after.team_id == before


def test_flagrant_free_throws_retain_ball_for_shooting_team() -> None:
    engine = PossessionEngine(HOME, AWAY)
    state = engine.process(
        event("Free Throw", HOME, subtype="Free Throw Flagrant 2 of 2", shot_result="Made")
    )
    assert state.team_id == HOME


def test_period_transition_resets_possession_to_unknown() -> None:
    engine = PossessionEngine(HOME, AWAY)
    engine.process(event("Made Shot", HOME, period=1))
    assert engine.process(event("Period", period=2, subtype="Start")).team_id is None


def test_ambiguous_team_rebound_is_unknown_not_fabricated() -> None:
    engine = PossessionEngine(HOME, AWAY)
    engine.process(event("Missed Shot", HOME))
    state = engine.process(event("Rebound", 0, description="HOME Team Rebound"))
    assert state.team_id is None
    assert "ambiguous" in state.reason

