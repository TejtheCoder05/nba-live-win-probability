from __future__ import annotations

from typing import Any

from src.features.fouls import TeamFoulTracker

HOME = 100
AWAY = 200


def foul(team_id: int, subtype: str, description: str = "", period: int = 1) -> dict[str, Any]:
    return {
        "actionType": "Foul",
        "subType": subtype,
        "teamId": team_id,
        "description": description,
        "period": period,
    }


def test_qualifying_fouls_count_for_the_correct_team() -> None:
    tracker = TeamFoulTracker(HOME, AWAY)
    assert tracker.process(foul(HOME, "Personal")).home == 1
    state = tracker.process(foul(AWAY, "Shooting"))
    assert (state.home, state.away) == (1, 1)


def test_offensive_and_technical_fouls_do_not_advance_team_fouls() -> None:
    tracker = TeamFoulTracker(HOME, AWAY)
    tracker.process(foul(HOME, "Offensive"))
    tracker.process(foul(HOME, "Technical"))
    assert tracker.process(foul(HOME, "Offensive Charge")).home == 0


def test_team_fouls_reset_each_period() -> None:
    tracker = TeamFoulTracker(HOME, AWAY)
    tracker.process(foul(HOME, "Personal", period=1))
    state = tracker.process(foul(AWAY, "Personal", period=2))
    assert (state.home, state.away) == (0, 1)


def test_description_marker_independently_validates_reconstruction() -> None:
    tracker = TeamFoulTracker(HOME, AWAY)
    first = tracker.process(foul(HOME, "Personal", "Player P.FOUL (P1.T1)"))
    bad = tracker.process(foul(HOME, "Shooting", "Player S.FOUL (P2.T3)"))
    assert first.marker_count == 1
    assert first.marker_matches == 1
    assert bad.marker_count == 3
    assert bad.marker_matches == 0
    assert bad.home == 2, "a marker discrepancy must not overwrite our independently counted state"

