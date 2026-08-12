from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from src.features.events import normalize_event
from src.features.game_state import parse_clock_seconds, regulation_seconds_remaining
from src.features.possession import PossessionEngine
from src.features.processor import process_game
from src.live.adapter import adapt_live_action, adapt_live_actions
from src.live.errors import LiveEndpointError, LiveSchemaError
from src.live.parity import compare_state_frames
from src.live.play_by_play import (
    LivePlayByPlayClient,
    cache_live_play_by_play,
    parse_live_play_by_play,
)
from src.live.replay import LiveReplayEngine, model_input_from_state, replay_live_response
from src.live.scoreboard import (
    fetch_current_scoreboard,
    normalize_game,
    normalize_game_details,
    normalize_scoreboard,
)
from src.models.dataset import V1_FEATURES
from src.models.inference import WinProbabilityPredictor
from src.paths import LIVE_FIXTURE_DIR, MODEL_ARTIFACT_DIR, TESTS_FIXTURE_DIR


@pytest.fixture(scope="session")
def live_raw() -> dict:
    return json.loads((LIVE_FIXTURE_DIR / "playbyplay_0022000001.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def game_details_raw() -> dict:
    return json.loads((LIVE_FIXTURE_DIR / "game_details_0022000001.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def completed_scoreboard_raw() -> dict:
    return json.loads((LIVE_FIXTURE_DIR / "scoreboard_completed.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def live_game(game_details_raw):
    return normalize_game_details(game_details_raw)


@pytest.fixture(scope="session")
def live_replay(live_raw, live_game):
    return replay_live_response(live_raw, live_game)


@pytest.fixture(scope="session")
def frozen_predictor():
    return WinProbabilityPredictor.load(MODEL_ARTIFACT_DIR)


def raw_action(live_raw: dict, action_type: str, subtype: str | None = None, shot_result: str | None = None):
    for action in live_raw["game"]["actions"]:
        if action.get("actionType") != action_type:
            continue
        if subtype is not None and action.get("subType") != subtype:
            continue
        if shot_result is not None and action.get("shotResult") != shot_result:
            continue
        return action
    raise AssertionError(f"No real fixture action for {action_type}/{subtype}/{shot_result}")


def test_empty_scoreboard_is_valid() -> None:
    snapshot = normalize_scoreboard({"scoreboard": {"gameDate": "2026-08-11", "games": []}})
    assert snapshot.game_date == "2026-08-11"
    assert snapshot.games == ()


def test_scheduled_game_normalization_uses_observed_schema(completed_scoreboard_raw) -> None:
    raw = copy.deepcopy(completed_scoreboard_raw["scoreboard"]["games"][0])
    raw.update(gameStatus=1, gameStatusText="7:30 pm ET", period=0, gameClock="")
    raw["homeTeam"]["score"] = 0
    raw["awayTeam"]["score"] = 0
    game = normalize_game(raw)
    assert game.is_scheduled and not game.is_active and not game.is_final
    assert game.home_team.score == 0 and game.away_team.score == 0


def test_active_and_halftime_game_normalization(completed_scoreboard_raw) -> None:
    raw = copy.deepcopy(completed_scoreboard_raw["scoreboard"]["games"][0])
    raw.update(gameStatus=2, gameStatusText="Halftime", period=2, gameClock="PT00M00.00S")
    game = normalize_game(raw)
    assert game.is_active
    assert game.game_status_text == "Halftime"
    assert game.period == 2


def test_completed_overtime_scoreboard_normalization(completed_scoreboard_raw) -> None:
    game = normalize_scoreboard(completed_scoreboard_raw).games[0]
    assert game.is_final
    assert game.game_status_text == "Final/OT"
    assert game.period == 5
    assert game.home_team.tricode == "DET"
    assert game.away_team.tricode == "DEN"


def test_scoreboard_fetch_gracefully_handles_endpoint_failure() -> None:
    def failing_endpoint(**_kwargs):
        raise TimeoutError("offline")

    snapshot = fetch_current_scoreboard(endpoint_factory=failing_endpoint)
    assert snapshot.games == ()
    assert "TimeoutError" in snapshot.error


def test_home_away_orientation_from_real_game_details(live_game) -> None:
    assert live_game.home_team.team_id == 1610612751
    assert live_game.home_team.tricode == "BKN"
    assert live_game.away_team.team_id == 1610612744
    assert live_game.away_team.tricode == "GSW"
    assert (live_game.home_team.score, live_game.away_team.score) == (125, 99)


def test_live_final_score_and_differential_orientation(live_replay) -> None:
    final = live_replay.states.iloc[-1]
    assert (final.home_score, final.away_score) == (125, 99)
    assert final.score_differential == 26
    assert live_replay.processing.final_score_matches is None


def test_regulation_clock_uses_shared_historical_functions(live_replay) -> None:
    state = live_replay.states.query("period == 2").iloc[5]
    assert state.seconds_remaining_period == parse_clock_seconds(state.clock)
    assert state.seconds_remaining_regulation == regulation_seconds_remaining(2, state.seconds_remaining_period)


def test_overtime_clock_has_no_future_overtime_leakage(completed_scoreboard_raw) -> None:
    game = normalize_scoreboard(completed_scoreboard_raw).games[0]
    assert game.period == 5
    assert regulation_seconds_remaining(game.period, parse_clock_seconds("PT04M12.50S")) == 0.0


@pytest.mark.parametrize(
    ("raw_type", "shot_result", "category"),
    [("2pt", "Made", "made shot"), ("3pt", "Missed", "missed shot")],
)
def test_shot_mapping_from_real_events(live_raw, raw_type, shot_result, category) -> None:
    action = raw_action(live_raw, raw_type, shot_result=shot_result)
    adapted = adapt_live_action("0022000001", action, 0).as_processor_action()
    assert normalize_event(adapted).category == category


@pytest.mark.parametrize(
    ("subtype", "expected"),
    [("offensive", "Offensive"), ("defensive", "Defensive")],
)
def test_rebound_mapping_from_real_events(live_raw, subtype, expected) -> None:
    action = raw_action(live_raw, "rebound", subtype=subtype)
    adapted = adapt_live_action("0022000001", action, 0).as_processor_action()
    assert normalize_event(adapted).category == "rebound"
    assert adapted["subType"] == expected


def test_shared_possession_engine_classifies_real_offensive_and_defensive_rebounds(live_raw, live_game) -> None:
    engine = PossessionEngine(live_game.home_team.team_id, live_game.away_team.team_id)
    reasons = set()
    for event in adapt_live_actions("0022000001", live_raw["game"]["actions"]):
        state = engine.process(event.as_processor_action())
        reasons.add(state.reason)
    assert "offensive rebound" in reasons
    assert "defensive rebound" in reasons


def test_turnover_free_throw_jump_ball_and_foul_mapping(live_raw) -> None:
    cases = [
        (raw_action(live_raw, "turnover"), "turnover"),
        (raw_action(live_raw, "freethrow", subtype="1 of 2"), "free throw"),
        (raw_action(live_raw, "jumpball"), "jump ball"),
        (raw_action(live_raw, "foul", subtype="personal"), "foul"),
    ]
    normalized = [normalize_event(adapt_live_action("0022000001", action, 0).as_processor_action()) for action, _ in cases]
    assert [event.category for event in normalized] == [expected for _, expected in cases]
    assert normalized[1].free_throw_attempt == 1 and normalized[1].free_throw_total == 2


def test_live_foul_descriptors_match_historical_counter_semantics(live_raw) -> None:
    shooting = next(
        action for action in live_raw["game"]["actions"]
        if action.get("actionType") == "foul" and action.get("descriptor") == "shooting"
    )
    offensive = raw_action(live_raw, "foul", subtype="offensive")
    assert adapt_live_action("0022000001", shooting, 0).sub_type == "Shooting"
    assert adapt_live_action("0022000001", offensive, 0).sub_type in {"Offensive", "Offensive Charge"}


def test_period_transition_resets_fouls_and_possession(live_replay) -> None:
    for period in (2, 3, 4):
        first = live_replay.states.query("period == @period").iloc[0]
        assert first.home_team_fouls_period == 0
        assert first.away_team_fouls_period == 0
        assert first.possession_known == 0


def test_jump_ball_preserves_unknown_possession(live_raw, live_game) -> None:
    jump = adapt_live_action("0022000001", raw_action(live_raw, "jumpball"), 0)
    engine = PossessionEngine(live_game.home_team.team_id, live_game.away_team.team_id)
    state = engine.process(jump.as_processor_action())
    assert jump.source_possession_team_id == live_game.home_team.team_id
    assert state.team_id is None
    assert state.reason == "jump ball winner ambiguous"


def test_adapter_sorts_order_number_and_deduplicates_exact_source_identity(live_raw) -> None:
    actions = list(reversed(live_raw["game"]["actions"][:20]))
    actions.append(copy.deepcopy(actions[-1]))
    adapted = adapt_live_actions("0022000001", actions)
    assert len(adapted) == 20
    assert [int(event.event_id) for event in adapted] == sorted(int(event.event_id) for event in adapted)


def test_repeated_identical_polling_does_not_duplicate_state(live_raw, live_game) -> None:
    engine = LiveReplayEngine(live_game)
    first = engine.update(live_raw)
    second = engine.update(live_raw)
    assert first.changed
    assert not second.changed
    assert len(second.states) == len(first.states)
    assert second.states.equals(first.states)


def test_client_marks_repeated_response_unchanged(live_raw) -> None:
    class Endpoint:
        def __init__(self, **_kwargs):
            pass

        def get_dict(self):
            return live_raw

    client = LivePlayByPlayClient(minimum_request_interval=0, endpoint_factory=Endpoint)
    assert client.fetch("0022000001").changed
    assert not client.fetch("0022000001").changed


def test_client_wraps_endpoint_decode_or_timeout_failures() -> None:
    class Endpoint:
        def __init__(self, **_kwargs):
            raise ValueError("endpoint returned non-JSON")

    client = LivePlayByPlayClient(minimum_request_interval=0, endpoint_factory=Endpoint)
    with pytest.raises(LiveEndpointError, match="Could not fetch"):
        client.fetch("0022000001")


def test_live_raw_caching_is_separate_and_atomic(live_raw, tmp_path) -> None:
    path = cache_live_play_by_play(live_raw, "0022000001", directory=tmp_path)
    assert path == tmp_path / "0022000001.json"
    assert json.loads(path.read_text(encoding="utf-8"))["game"]["gameId"] == "0022000001"
    assert not list(tmp_path.glob("*.tmp"))


def test_malformed_or_wrong_game_responses_are_rejected(live_raw) -> None:
    with pytest.raises(LiveSchemaError, match="missing game object"):
        parse_live_play_by_play({})
    with pytest.raises(LiveSchemaError, match="Requested game"):
        parse_live_play_by_play(live_raw, requested_game_id="0022000002")


def test_unexpected_event_type_becomes_safe_metadata(live_raw) -> None:
    action = copy.deepcopy(live_raw["game"]["actions"][0])
    action["actionType"] = "new-unseen-type"
    adapted = adapt_live_action("0022000001", action, 0)
    assert adapted.action_type == ""
    assert adapted.source_action_type == "new-unseen-type"


def test_feature_order_exactly_matches_frozen_artifact(frozen_predictor) -> None:
    assert tuple(frozen_predictor.preprocessor.feature_names) == V1_FEATURES


def test_live_model_input_unknown_possession_is_neutral_and_finite(live_replay, frozen_predictor) -> None:
    state = live_replay.states.loc[live_replay.states["possession_known"] == 0].iloc[0].to_dict()
    model_state = model_input_from_state(state, frozen_predictor.preprocessor.feature_names)
    assert tuple(model_state) == V1_FEATURES
    assert model_state["home_possession"] == 0.5
    transformed = frozen_predictor.preprocessor.transform_row(model_state)
    assert np.isfinite(transformed).all()


def test_saved_predictor_accepts_live_state_and_returns_probability(live_replay, frozen_predictor) -> None:
    state = model_input_from_state(live_replay.states.iloc[200].to_dict(), frozen_predictor.preprocessor.feature_names)
    probability = frozen_predictor.predict_one(state)
    assert 0.0 <= probability <= 1.0


def test_full_fixture_replay_is_deterministic(live_raw, live_game) -> None:
    first = replay_live_response(live_raw, live_game)
    second = replay_live_response(copy.deepcopy(live_raw), live_game)
    assert first.fingerprint == second.fingerprint
    assert first.states.equals(second.states)


def test_same_game_historical_live_semantic_parity(live_replay, live_game) -> None:
    historical_raw = json.loads(
        (TESTS_FIXTURE_DIR / "historical" / "playbyplayv3_0022000001.json").read_text(encoding="utf-8")
    )
    historical, result = process_game(
        historical_raw["game"]["actions"],
        season="parity",
        game_id=live_game.game_id,
        home_team_id=live_game.home_team.team_id,
        away_team_id=live_game.away_team.team_id,
        expected_home_score=125,
        expected_away_score=99,
        home_win=1,
    )
    report = compare_state_frames(historical, live_replay.states, game_id=live_game.game_id)
    assert result.final_score_matches
    assert report.historical_final_score == report.live_final_score == (125, 99)
    for field in (
        "period",
        "clock",
        "home_score",
        "away_score",
        "score_differential",
        "seconds_remaining_period",
        "seconds_remaining_regulation",
        "home_team_fouls_period",
        "away_team_fouls_period",
    ):
        assert report.field_parity[field].percent == 100.0
    assert report.field_parity["possession_known"].percent >= 98.0
    assert report.semantic_anchor_matches / report.historical_semantic_anchors >= 0.99
