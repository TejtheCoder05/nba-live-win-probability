from __future__ import annotations

from typing import Any

import pytest

from src.data.play_by_play import existing_raw_path, read_raw_json
from src.features.processor import process_game


def action(
    number: int,
    action_type: str,
    team_id: int,
    clock: str,
    *,
    period: int = 1,
    subtype: str = "",
    description: str = "",
    home_score: str = "",
    away_score: str = "",
) -> dict[str, Any]:
    return {
        "actionNumber": number,
        "actionId": number,
        "actionType": action_type,
        "subType": subtype,
        "teamId": team_id,
        "description": description,
        "period": period,
        "clock": clock,
        "scoreHome": home_score,
        "scoreAway": away_score,
        "shotResult": "Made" if action_type in {"Made Shot", "Free Throw"} else "",
    }


def test_score_forward_fill_emission_traceability_and_label() -> None:
    actions = [
        action(1, "Period", 0, "PT12M00.00S", subtype="Start"),
        action(2, "Made Shot", 100, "PT11M30.00S", home_score="2", away_score="0"),
        action(3, "", 200, "PT11M30.00S", description="Visitor BLOCK (1 BLK)"),
        action(4, "Turnover", 200, "PT11M10.00S"),
    ]
    frame, result = process_game(
        actions,
        season="test",
        game_id="0000000001",
        home_team_id=100,
        away_team_id=200,
        expected_home_score=2,
        expected_away_score=0,
        home_win=1,
    )
    assert len(frame) == 3, "the duplicate metadata/credit state should not be emitted"
    assert frame["source_event_index"].tolist() == [0, 1, 3]
    assert frame["home_score"].tolist() == [0, 2, 2]
    assert frame["score_differential"].tolist() == [0, 2, 2]
    assert set(frame["home_win"]) == {1}
    assert result.final_score_matches


def test_official_final_score_is_validation_only() -> None:
    actions = [action(1, "Made Shot", 100, "PT11M30.00S", home_score="2", away_score="0")]
    frame, result = process_game(
        actions,
        season="test",
        game_id="0000000001",
        home_team_id=100,
        away_team_id=200,
        expected_home_score=99,
        expected_away_score=98,
        home_win=1,
    )
    assert frame.iloc[-1].home_score == 2
    assert frame.iloc[-1].away_score == 0
    assert not result.final_score_matches


def test_stale_support_replay_score_does_not_remove_confirmed_points() -> None:
    actions = [
        action(1, "Made Shot", 100, "PT00M00.30S", home_score="3", away_score="0"),
        action(
            2,
            "Instant Replay",
            0,
            "PT00M00.00S",
            subtype="Support Ruling",
            home_score="0",
            away_score="0",
        ),
        action(3, "Period", 0, "PT00M00.00S", subtype="End", home_score="0", away_score="0"),
    ]
    frame, result = process_game(
        actions,
        season="test",
        game_id="0000000001",
        home_team_id=100,
        away_team_id=200,
        expected_home_score=3,
        expected_away_score=0,
        home_win=1,
    )
    assert frame.iloc[-1].home_score == 3
    assert result.final_score_matches


def test_overturn_replay_score_can_apply_a_correction() -> None:
    actions = [
        action(1, "Made Shot", 100, "PT01M00.00S", home_score="3", away_score="0"),
        action(
            2,
            "Instant Replay",
            0,
            "PT01M00.00S",
            subtype="Overturn Ruling",
            home_score="0",
            away_score="0",
        ),
    ]
    frame, result = process_game(
        actions,
        season="test",
        game_id="0000000001",
        home_team_id=100,
        away_team_id=200,
        expected_home_score=0,
        expected_away_score=0,
        home_win=0,
    )
    assert frame.iloc[-1].home_score == 0
    assert result.final_score_matches


def test_out_of_order_score_snapshot_cannot_roll_back_newer_actions() -> None:
    actions = [
        action(10, "Made Shot", 100, "PT00M05.00S", home_score="2", away_score="0"),
        action(12, "Free Throw", 100, "PT00M03.00S", home_score="3", away_score="0"),
        action(11, "Free Throw", 100, "PT00M03.00S", home_score="2", away_score="0"),
        action(13, "Period", 0, "PT00M00.00S", subtype="End", home_score="3", away_score="0"),
    ]
    frame, result = process_game(
        actions,
        season="test",
        game_id="0000000001",
        home_team_id=100,
        away_team_id=200,
        expected_home_score=3,
        expected_away_score=0,
        home_win=1,
    )
    assert frame.iloc[-1].home_score == 3
    assert result.final_score_matches


def test_period_end_can_recover_but_not_decrease_score() -> None:
    actions = [
        action(1, "Made Shot", 100, "PT00M01.00S", home_score="2", away_score="0"),
        action(2, "Period", 0, "PT00M00.00S", subtype="End", home_score="3", away_score="0"),
        action(3, "Period", 0, "PT00M00.00S", subtype="End", home_score="1", away_score="0"),
    ]
    frame, result = process_game(
        actions,
        season="test",
        game_id="0000000001",
        home_team_id=100,
        away_team_id=200,
        expected_home_score=3,
        expected_away_score=0,
        home_win=1,
    )
    assert frame.iloc[-1].home_score == 3
    assert result.final_score_matches


def test_ignored_replay_action_number_does_not_block_same_clock_scoring() -> None:
    actions = [
        action(
            20,
            "Instant Replay",
            0,
            "PT00M00.00S",
            subtype="Support Ruling",
            home_score="0",
            away_score="0",
        ),
        action(18, "Made Shot", 100, "PT00M00.00S", home_score="2", away_score="0"),
        action(19, "Period", 0, "PT00M00.00S", subtype="End", home_score="2", away_score="0"),
    ]
    frame, result = process_game(
        actions,
        season="test",
        game_id="0000000001",
        home_team_id=100,
        away_team_id=200,
        expected_home_score=2,
        expected_away_score=0,
        home_win=1,
    )
    assert frame.iloc[-1].home_score == 2
    assert result.final_score_matches


def test_known_real_game_final_score_and_block_credit_rebound() -> None:
    path = existing_raw_path("0022300061")
    if path is None:
        pytest.skip("full historical raw data not present")
    actions = read_raw_json(path)["game"]["actions"]
    frame, result = process_game(
        actions,
        season="2023-24",
        game_id="0022300061",
        home_team_id=1610612743,
        away_team_id=1610612747,
        expected_home_score=119,
        expected_away_score=107,
        home_win=1,
    )
    assert result.final_score_matches
    assert result.raw_events == 473
    assert result.foul_markers_checked > 0
    assert result.foul_markers_checked == result.foul_markers_matched
    assert (frame["possession_reason"] == "offensive rebound").any()
    assert (frame["possession_reason"] == "defensive rebound").any()
