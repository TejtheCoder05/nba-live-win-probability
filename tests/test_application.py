from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from src.api import create_app
from src.live.scoreboard import LiveGame, LiveTeam, ScoreboardSnapshot
from src.live.play_by_play import parse_live_play_by_play
from src.live.scoreboard import normalize_game_details
from src.live.service import LIVE_FEED_UNAVAILABLE, REPLAY_GAME_ID, display_home_probability
from src.models.inference import WinProbabilityPredictor
from src.paths import MODEL_ARTIFACT_DIR


@pytest.fixture(scope="module")
def predictor():
    return WinProbabilityPredictor.load(MODEL_ARTIFACT_DIR)


@pytest.fixture()
def app(predictor):
    application = create_app(
        {"testing": True, "start_background_tasks": False},
        predictor=predictor,
    )
    yield application
    application.extensions["game_service"].shutdown()


def event_payloads(client, name: str) -> list[dict]:
    return [event["args"][0] for event in client.get_received() if event["name"] == name]


def test_dashboard_and_health_are_available(app) -> None:
    client = app.test_client()
    page = client.get("/")
    assert page.status_code == 200
    assert b"NBA Win Probability" in page.data
    assert b"probabilityChart" in page.data
    application_js = client.get("/static/js/app.js")
    socket_client = client.get("/static/vendor/socket.io.min.js")
    assert application_js.status_code == 200
    assert socket_client.status_code == 200
    assert len(socket_client.data) > 40_000
    assert b"Socket.IO v4.8.1" in socket_client.data
    assert b"Manager" in socket_client.data and b"connect" in socket_client.data
    assert hashlib.sha256(socket_client.data).hexdigest() == (
        "b0e735814f8dcfecd6cdb8a7ce95a297a7e1e5f2727a29e6f5901801d52fa0c5"
    )
    html = page.get_data(as_text=True)
    assert html.index("vendor/socket.io.min.js") < html.index("js/app.js")
    assert client.get("/api/health").json == {"mode": "replay", "status": "ok"}


def test_games_and_state_api_are_json_serializable(app) -> None:
    client = app.test_client()
    listing = client.get("/api/games")
    assert listing.status_code == 200
    assert listing.json["mode"] == "replay"
    assert listing.json["games"][0]["game_id"] == REPLAY_GAME_ID

    response = client.get(f"/api/games/{REPLAY_GAME_ID}")
    assert response.status_code == 200
    state = response.json
    assert state["data_source"] == "Authentic Phase 6 NBA live-format fixture"
    assert 0 <= state["model_home_win_probability"] <= 1
    assert state["final_override_applied"] is False
    assert state["replay"]["total_actions"] == 610


def test_api_validates_game_ids_and_unknown_games(app) -> None:
    client = app.test_client()
    assert client.get("/api/games/not-an-id").status_code == 400
    assert client.get("/api/games/9999999999").status_code == 404
    assert client.post(f"/api/replay/{REPLAY_GAME_ID}/warp", json={}).status_code == 400
    assert client.post(f"/api/replay/{REPLAY_GAME_ID}/speed", json={"speed": 100}).status_code == 400


def test_product_override_requires_official_final() -> None:
    home = LiveTeam(1, "Home", "Here", "HOM", 101)
    away = LiveTeam(2, "Away", "There", "AWY", 99)
    game = LiveGame("0000000001", 2, "0:00", 4, "PT00M00.00S", home, away)
    assert display_home_probability(game, 0.73) == (0.73, False)
    final = replace(game, game_status=3, game_status_text="Final")
    assert display_home_probability(final, 0.73) == (1.0, True)
    tied_final = replace(final, home_team=replace(home, score=99))
    assert display_home_probability(tied_final, 0.73) == (0.0, True)


def test_replay_http_controls_reconstruct_and_reach_official_final(app) -> None:
    client = app.test_client()
    reset = client.post(f"/api/replay/{REPLAY_GAME_ID}/reset").json
    assert reset["replay"]["cursor"] == 1 and reset["replay"]["paused"]
    progressed = client.post(
        f"/api/replay/{REPLAY_GAME_ID}/step", json={"steps": 100}
    ).json
    assert progressed["replay"]["cursor"] == 101
    assert progressed["sequence"] > reset["sequence"]
    while not progressed["replay"]["complete"]:
        progressed = client.post(
            f"/api/replay/{REPLAY_GAME_ID}/step", json={"steps": 100}
        ).json
    assert (progressed["away_team"]["score"], progressed["home_team"]["score"]) == (99, 125)
    assert progressed["game_status"] == 3
    assert progressed["home_win_probability"] == 1.0
    assert progressed["final_override_applied"] is True
    assert 0 < progressed["model_home_win_probability"] < 1


def test_socket_subscription_streams_future_state(app) -> None:
    service = app.extensions["game_service"]
    service.control_replay(REPLAY_GAME_ID, "reset")
    socketio = app.extensions["socketio"]
    client = socketio.test_client(app)
    client.get_received()
    client.emit("subscribe_game", {"game_id": REPLAY_GAME_ID})
    initial = event_payloads(client, "game_state")
    assert initial and initial[-1]["game_id"] == REPLAY_GAME_ID
    before = initial[-1]["sequence"]
    client.emit("replay_control", {"game_id": REPLAY_GAME_ID, "action": "step", "steps": 25})
    later = event_payloads(client, "game_state")
    assert later and later[-1]["sequence"] > before
    client.emit("unsubscribe_game", {"game_id": REPLAY_GAME_ID})
    assert service.pollers.subscriber_count(REPLAY_GAME_ID) == 0
    client.disconnect()


def test_multiple_clients_share_one_game_poller_and_disconnect_cleanly(app) -> None:
    socketio = app.extensions["socketio"]
    service = app.extensions["game_service"]
    first = socketio.test_client(app)
    second = socketio.test_client(app)
    first.get_received()
    second.get_received()
    starts_before = service.pollers.start_count(REPLAY_GAME_ID)
    first.emit("subscribe_game", {"game_id": REPLAY_GAME_ID})
    second.emit("subscribe_game", {"game_id": REPLAY_GAME_ID})
    assert service.pollers.subscriber_count(REPLAY_GAME_ID) == 2
    assert service.pollers.start_count(REPLAY_GAME_ID) == starts_before + 1
    first.disconnect()
    assert service.pollers.subscriber_count(REPLAY_GAME_ID) == 1
    second.disconnect()
    assert service.pollers.subscriber_count(REPLAY_GAME_ID) == 0


def test_identical_replay_state_is_not_broadcast_twice(app) -> None:
    socketio = app.extensions["socketio"]
    service = app.extensions["game_service"]
    client = socketio.test_client(app)
    client.get_received()
    client.emit("subscribe_game", {"game_id": REPLAY_GAME_ID})
    client.get_received()
    service.control_replay(REPLAY_GAME_ID, "pause")
    client.get_received()
    service.control_replay(REPLAY_GAME_ID, "pause")
    assert event_payloads(client, "game_state") == []
    client.disconnect()


def test_metadata_only_replay_action_is_not_broadcast_as_a_new_state(app) -> None:
    socketio = app.extensions["socketio"]
    service = app.extensions["game_service"]
    service.control_replay(REPLAY_GAME_ID, "reset")
    client = socketio.test_client(app)
    client.get_received()
    client.emit("subscribe_game", {"game_id": REPLAY_GAME_ID})
    client.get_received()
    service.control_replay(REPLAY_GAME_ID, "pause")
    client.get_received()
    found_metadata_only = False
    for _ in range(150):
        prior_sequence = service.get_state(REPLAY_GAME_ID)["sequence"]
        service.advance_replay(1, emit=True)
        current_sequence = service.get_state(REPLAY_GAME_ID)["sequence"]
        received = event_payloads(client, "game_state")
        if current_sequence == prior_sequence:
            assert received == []
            found_metadata_only = True
            break
    assert found_metadata_only
    client.disconnect()


def test_live_discovery_failure_is_friendly_and_nonfatal(predictor) -> None:
    def unavailable_scoreboard(**_kwargs):
        return ScoreboardSnapshot("", (), {}, error="HTTPError: 403 Client Error with internals")

    application = create_app(
        {
            "mode": "live",
            "testing": True,
            "start_background_tasks": False,
            "scoreboard_refresh_seconds": 30,
        },
        predictor=predictor,
        service_options={"scoreboard_fetcher": unavailable_scoreboard},
    )
    response = application.test_client().get("/api/games")
    assert response.status_code == 200
    assert response.json == {
        "available": False,
        "error": LIVE_FEED_UNAVAILABLE,
        "games": [],
        "mode": "live",
    }
    assert "403" not in response.get_data(as_text=True)
    application.extensions["game_service"].shutdown()


def test_unchanged_play_by_play_still_publishes_official_final(predictor) -> None:
    import json

    from src.paths import LIVE_FIXTURE_DIR

    raw = json.loads((LIVE_FIXTURE_DIR / "playbyplay_0022000001.json").read_text(encoding="utf-8"))
    details = json.loads((LIVE_FIXTURE_DIR / "game_details_0022000001.json").read_text(encoding="utf-8"))
    final_game = normalize_game_details(details)
    active_game = replace(final_game, game_status=2, game_status_text="0:00")
    calls = 0

    def scoreboard(**_kwargs):
        nonlocal calls
        calls += 1
        game = active_game if calls <= 2 else final_game
        return ScoreboardSnapshot("2020-12-22", (game,), {})

    class Client:
        def fetch(self, game_id):
            return parse_live_play_by_play(raw, requested_game_id=game_id)

    application = create_app(
        {
            "mode": "live",
            "testing": True,
            "start_background_tasks": False,
            "scoreboard_refresh_seconds": 0,
        },
        predictor=predictor,
        service_options={
            "scoreboard_fetcher": scoreboard,
            "play_by_play_client_factory": lambda **_kwargs: Client(),
        },
    )
    service = application.extensions["game_service"]
    assert service.list_games()["games"]
    assert service.poll_live_game(REPLAY_GAME_ID)
    active_state = service.get_state(REPLAY_GAME_ID)
    assert active_state["game_status"] == 2
    assert active_state["final_override_applied"] is False
    assert active_state["home_win_probability"] == active_state["model_home_win_probability"]

    assert service.poll_live_game(REPLAY_GAME_ID)
    final_state = service.get_state(REPLAY_GAME_ID)
    assert final_state["game_status"] == 3
    assert final_state["final_override_applied"] is True
    assert final_state["home_win_probability"] == 1.0
    service.shutdown()


def test_socket_errors_are_structured(app) -> None:
    client = app.extensions["socketio"].test_client(app)
    client.get_received()
    client.emit("subscribe_game", {"game_id": "bad"})
    errors = event_payloads(client, "game_error")
    assert errors == [{"game_id": "bad", "message": "Invalid NBA game ID."}]
    client.disconnect()
