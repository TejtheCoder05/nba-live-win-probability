"""Application-layer game state service for live polling and fixture replay."""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

import pandas as pd

from src.live.play_by_play import LivePlayByPlayClient, validate_game_id
from src.live.poller import PollerRegistry
from src.live.replay import LiveReplayEngine, ReplayResult
from src.live.scoreboard import (
    LiveGame,
    ScoreboardSnapshot,
    fetch_current_scoreboard,
    normalize_game_details,
)
from src.models.inference import WinProbabilityPredictor
from src.paths import LIVE_FIXTURE_DIR

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.api.config import AppConfig

REPLAY_GAME_ID = "0022000001"
LIVE_FEED_UNAVAILABLE = "Live NBA feed unavailable from this environment."


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def display_home_probability(game: LiveGame, model_probability: float) -> tuple[float, bool]:
    """Apply terminal certainty only in the product layer after official FINAL."""
    home_score = game.home_team.score
    away_score = game.away_team.score
    if game.is_final and home_score is not None and away_score is not None:
        return (1.0 if home_score > away_score else 0.0), True
    return float(model_probability), False


def _team_payload(team: Any, score: int | None) -> dict[str, Any]:
    return {
        "team_id": team.team_id,
        "name": team.name,
        "city": team.city,
        "tricode": team.tricode,
        "score": score,
    }


def _period_label(period: int) -> str:
    if period <= 0:
        return "Pregame"
    if period <= 4:
        return f"Q{period}"
    return f"OT{period - 4}"


def _json_value(value: Any) -> Any:
    if value is None or (not isinstance(value, (str, bool)) and pd.isna(value)):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


class GameService:
    """Own discovery, one-poller subscriptions, replay controls, and public state."""

    def __init__(
        self,
        config: AppConfig,
        predictor: WinProbabilityPredictor,
        socketio: Any,
        *,
        scoreboard_fetcher: Callable[..., ScoreboardSnapshot] = fetch_current_scoreboard,
        play_by_play_client_factory: Callable[..., LivePlayByPlayClient] = LivePlayByPlayClient,
        replay_play_by_play_path: Path | None = None,
        replay_game_details_path: Path | None = None,
    ) -> None:
        self.config = config
        self.predictor = predictor
        self.socketio = socketio
        self._scoreboard_fetcher = scoreboard_fetcher
        self._client_factory = play_by_play_client_factory
        self._lock = threading.RLock()
        self._games: dict[str, LiveGame] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._state_signatures: dict[str, str] = {}
        self._engines: dict[str, LiveReplayEngine] = {}
        self._clients: dict[str, LivePlayByPlayClient] = {}
        self._last_scoreboard_fetch = 0.0
        self._scoreboard_error: str | None = None

        self._replay_raw: dict[str, Any] | None = None
        self._replay_game: LiveGame | None = None
        self._replay_cursor = 1
        self._replay_paused = True
        self._replay_speed = config.replay_speed

        self.pollers = PollerRegistry(
            socketio.start_background_task,
            self._poll_game,
            enabled=config.start_background_tasks,
        )

        if config.mode == "replay":
            pbp_path = replay_play_by_play_path or (LIVE_FIXTURE_DIR / "playbyplay_0022000001.json")
            details_path = replay_game_details_path or (LIVE_FIXTURE_DIR / "game_details_0022000001.json")
            self._replay_raw = json.loads(pbp_path.read_text(encoding="utf-8"))
            self._replay_game = normalize_game_details(json.loads(details_path.read_text(encoding="utf-8")))
            self._games[self._replay_game.game_id] = self._replay_game
            self._engines[self._replay_game.game_id] = LiveReplayEngine(
                self._replay_game, predictor=self.predictor
            )
            self._rebuild_replay(emit=False)

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def ready(self) -> bool:
        """Application readiness is local and does not require NBA CDN access."""
        if self.predictor is None:
            return False
        return self.mode == "live" or (
            self._replay_raw is not None and self._replay_game is not None
        )

    def game_exists(self, game_id: str) -> bool:
        try:
            normalized = validate_game_id(game_id)
        except ValueError:
            return False
        if self.mode == "live":
            self.refresh_games()
        with self._lock:
            return normalized in self._games

    def refresh_games(self, *, force: bool = False) -> None:
        if self.mode != "live":
            return
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_scoreboard_fetch < self.config.scoreboard_refresh_seconds:
                return
            self._last_scoreboard_fetch = now
        snapshot = self._scoreboard_fetcher(timeout=self.config.endpoint_timeout_seconds)
        if snapshot.error:
            logger.warning("Live game discovery failed: %s", snapshot.error)
            with self._lock:
                self._scoreboard_error = LIVE_FEED_UNAVAILABLE
            return
        with self._lock:
            self._scoreboard_error = None
            for game in snapshot.games:
                self._games[game.game_id] = game

    def list_games(self) -> dict[str, Any]:
        self.refresh_games()
        with self._lock:
            games = [self._game_summary(game) for game in self._games.values()]
            error = self._scoreboard_error
        return {
            "mode": self.mode,
            "available": error is None,
            "error": error,
            "games": sorted(games, key=lambda item: item["game_id"]),
        }

    def _game_summary(self, game: LiveGame) -> dict[str, Any]:
        state = self._states.get(game.game_id)
        return {
            "game_id": game.game_id,
            "game_status": state["game_status"] if state else game.game_status,
            "status_text": state["status_text"] if state else game.game_status_text,
            "period": state["period"] if state else game.period,
            "clock": state["clock"] if state else game.clock,
            "home_team": _team_payload(game.home_team, state["home_team"]["score"] if state else game.home_team.score),
            "away_team": _team_payload(game.away_team, state["away_team"]["score"] if state else game.away_team.score),
        }

    def get_state(self, game_id: str) -> dict[str, Any] | None:
        normalized = validate_game_id(game_id)
        if self.mode == "live":
            self.refresh_games()
        with self._lock:
            state = self._states.get(normalized)
            if state is not None:
                return copy.deepcopy(state)
            game = self._games.get(normalized)
        return None if game is None else self._waiting_state(game)

    def _waiting_state(self, game: LiveGame) -> dict[str, Any]:
        return {
            "game_id": game.game_id,
            "mode": self.mode,
            "data_source": "NBA live endpoints",
            "game_status": game.game_status,
            "status_text": game.game_status_text or "Waiting for play-by-play",
            "period": game.period,
            "period_label": _period_label(game.period),
            "clock": game.clock,
            "home_team": _team_payload(game.home_team, game.home_team.score),
            "away_team": _team_payload(game.away_team, game.away_team.score),
            "score_differential": None,
            "possession": {"team_id": None, "label": "Unknown", "known": False},
            "fouls": {"home_period": 0, "away_period": 0},
            "home_win_probability": None,
            "away_win_probability": None,
            "model_home_win_probability": None,
            "final_override_applied": False,
            "last_event": None,
            "recent_events": [],
            "probability_history": [],
            "last_update": _utc_now(),
            "feed_status": "waiting",
            "error": self._scoreboard_error,
            "sequence": 0,
            "replay": None,
        }

    def subscribe(self, game_id: str, subscriber_id: str) -> dict[str, Any]:
        normalized = validate_game_id(game_id)
        if not self.game_exists(normalized):
            raise KeyError(normalized)
        if self.mode == "replay":
            with self._lock:
                self._replay_paused = False
            self._rebuild_replay(emit=False)
        self.pollers.subscribe(normalized, subscriber_id)
        logger.info("Subscriber joined game_id=%s viewers=%d", normalized, self.pollers.subscriber_count(normalized))
        state = self.get_state(normalized)
        assert state is not None
        return state

    def unsubscribe(self, game_id: str, subscriber_id: str) -> None:
        normalized = validate_game_id(game_id)
        self.pollers.unsubscribe(normalized, subscriber_id)
        logger.info("Subscriber left game_id=%s viewers=%d", normalized, self.pollers.subscriber_count(normalized))

    def disconnect(self, subscriber_id: str) -> tuple[str, ...]:
        return self.pollers.disconnect(subscriber_id)

    def shutdown(self) -> None:
        self.pollers.stop_all()

    def _poll_game(self, game_id: str, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                if self.mode == "replay":
                    with self._lock:
                        paused = self._replay_paused
                        speed = self._replay_speed
                    if not paused:
                        self.advance_replay(1, emit=True)
                    self.socketio.sleep(self.config.replay_interval_seconds / speed)
                else:
                    self.poll_live_game(game_id)
                    self.socketio.sleep(self.config.polling_interval_seconds)
        except Exception:
            logger.exception("Background poller stopped unexpectedly game_id=%s", game_id)
            self._mark_degraded(game_id)

    def poll_live_game(self, game_id: str) -> bool:
        normalized = validate_game_id(game_id)
        self.refresh_games()
        with self._lock:
            game = self._games.get(normalized)
            if game is None:
                raise KeyError(normalized)
            client = self._clients.get(normalized)
            if client is None:
                client = self._client_factory(
                    timeout=self.config.endpoint_timeout_seconds,
                    minimum_request_interval=self.config.polling_interval_seconds,
                )
                self._clients[normalized] = client
            engine = self._engines.get(normalized)
            if engine is None:
                engine = LiveReplayEngine(game, predictor=self.predictor)
                self._engines[normalized] = engine
        try:
            snapshot = client.fetch(normalized)
            result = engine.update(snapshot.raw)
            with self._lock:
                prior_state = self._states.get(normalized)
            metadata_changed = prior_state is None or any(
                (
                    prior_state.get("game_status") != game.game_status,
                    prior_state.get("status_text") != game.game_status_text,
                    prior_state.get("home_team", {}).get("score") != game.home_team.score,
                    prior_state.get("away_team", {}).get("score") != game.away_team.score,
                )
            )
            if not result.changed and not metadata_changed:
                return False
            state = self._build_state(game, result, feed_status="live", error=None)
            return self._store_state(normalized, state, emit=True)
        except Exception as exc:
            logger.warning("Live play-by-play update failed game_id=%s: %s", normalized, exc)
            self._mark_degraded(normalized)
            return False

    def _mark_degraded(self, game_id: str) -> None:
        with self._lock:
            prior = self._states.get(game_id)
            if prior is not None and prior.get("error") == LIVE_FEED_UNAVAILABLE:
                return
            if prior is not None:
                state = copy.deepcopy(prior)
                state.update(feed_status="degraded", error=LIVE_FEED_UNAVAILABLE, last_update=_utc_now())
                self._states[game_id] = state
            else:
                state = None
        self.socketio.emit(
            "game_error",
            {"game_id": game_id, "message": LIVE_FEED_UNAVAILABLE},
            to=game_id,
        )
        if state is not None:
            self.socketio.emit("game_state", state, to=game_id)

    def control_replay(
        self,
        game_id: str,
        action: str,
        *,
        speed: float | None = None,
        steps: int = 1,
    ) -> dict[str, Any]:
        normalized = validate_game_id(game_id)
        if self.mode != "replay" or normalized != REPLAY_GAME_ID:
            raise KeyError(normalized)
        action = str(action).strip().lower()
        if action not in {"start", "pause", "resume", "reset", "step", "speed"}:
            raise ValueError(f"Unsupported replay action: {action}")
        with self._lock:
            if action == "reset":
                self._replay_cursor = 1
                self._replay_paused = True
                assert self._replay_game is not None
                self._engines[normalized] = LiveReplayEngine(
                    self._replay_game, predictor=self.predictor
                )
            elif action == "start":
                assert self._replay_raw is not None
                if self._replay_cursor >= len(self._replay_raw["game"]["actions"]):
                    self._replay_cursor = 1
                    assert self._replay_game is not None
                    self._engines[normalized] = LiveReplayEngine(
                        self._replay_game, predictor=self.predictor
                    )
                self._replay_paused = False
            elif action == "pause":
                self._replay_paused = True
            elif action == "resume":
                self._replay_paused = False
            elif action == "step":
                self._replay_paused = True
            elif action == "speed":
                if speed is None or not 0.25 <= float(speed) <= 20.0:
                    raise ValueError("Replay speed must be between 0.25x and 20x")
                self._replay_speed = float(speed)
        logger.info("Replay control game_id=%s action=%s", normalized, action)
        if action == "step":
            return self.advance_replay(max(1, min(int(steps), 100)), emit=True)
        return self._rebuild_replay(emit=True)

    def advance_replay(self, steps: int = 1, *, emit: bool = True) -> dict[str, Any]:
        assert self._replay_raw is not None
        with self._lock:
            total = len(self._replay_raw["game"]["actions"])
            self._replay_cursor = min(total, self._replay_cursor + max(1, int(steps)))
            if self._replay_cursor >= total:
                self._replay_paused = True
        return self._rebuild_replay(emit=emit)

    def _dynamic_replay_game(self, result: ReplayResult) -> LiveGame:
        assert self._replay_game is not None and self._replay_raw is not None
        latest = result.latest_state
        total = len(self._replay_raw["game"]["actions"])
        final = self._replay_cursor >= total
        if final:
            status, text = 3, self._replay_game.game_status_text or "Final"
        elif self._replay_paused and self._replay_cursor <= 1:
            status, text = 2, "Replay ready"
        elif self._replay_paused:
            status, text = 2, "Replay paused"
        else:
            status, text = 2, "Replay in progress"
        home_score = int(latest["home_score"]) if latest else 0
        away_score = int(latest["away_score"]) if latest else 0
        return replace(
            self._replay_game,
            game_status=status,
            game_status_text=text,
            period=int(latest["period"]) if latest else 0,
            clock=str(latest["clock"]) if latest else "",
            home_team=replace(self._replay_game.home_team, score=home_score),
            away_team=replace(self._replay_game.away_team, score=away_score),
        )

    def _rebuild_replay(self, *, emit: bool) -> dict[str, Any]:
        assert self._replay_raw is not None and self._replay_game is not None
        with self._lock:
            raw = copy.deepcopy(self._replay_raw)
            raw["game"]["actions"] = raw["game"]["actions"][: self._replay_cursor]
            engine = self._engines[self._replay_game.game_id]
            result = engine.update(raw)
            game = self._dynamic_replay_game(result)
            self._games[game.game_id] = game
            state = self._build_state(game, result, feed_status="replay", error=None)
        self._store_state(game.game_id, state, emit=emit)
        current = self.get_state(game.game_id)
        assert current is not None
        return current

    def _build_state(
        self,
        game: LiveGame,
        result: ReplayResult,
        *,
        feed_status: str,
        error: str | None,
    ) -> dict[str, Any]:
        if result.states.empty or result.probabilities is None or len(result.probabilities) == 0:
            return self._waiting_state(game)
        rows = result.states.to_dict(orient="records")
        latest = rows[-1]
        raw_probability = float(result.probabilities[-1])
        displayed_probability, final_override = display_home_probability(game, raw_probability)

        history_start = max(0, len(rows) - self.config.probability_history_limit)
        history = [
            {
                "sequence": int(row["source_event_index"]),
                "period": int(row["period"]),
                "period_label": _period_label(int(row["period"])),
                "clock": str(row["clock"]),
                "home_probability": float(result.probabilities[index]),
            }
            for index, row in enumerate(rows[history_start:], start=history_start)
        ]
        recent_rows = rows[-self.config.recent_events_limit :]
        recent = [
            {
                "sequence": int(row["source_event_index"]),
                "period": int(row["period"]),
                "clock": str(row["clock"]),
                "description": str(row.get("description") or row.get("semantic_event") or "Game update"),
                "semantic_event": str(row.get("semantic_event") or ""),
                "home_score": int(row["home_score"]),
                "away_score": int(row["away_score"]),
            }
            for row in recent_rows
        ]

        possession_team_id = _json_value(latest.get("possession_team_id"))
        if possession_team_id == game.home_team.team_id:
            possession_label = game.home_team.tricode or game.home_team.name
        elif possession_team_id == game.away_team.team_id:
            possession_label = game.away_team.tricode or game.away_team.name
        else:
            possession_label = "Unknown"
        replay = None
        if self.mode == "replay":
            assert self._replay_raw is not None
            replay = {
                "cursor": self._replay_cursor,
                "total_actions": len(self._replay_raw["game"]["actions"]),
                "paused": self._replay_paused,
                "speed": self._replay_speed,
                "complete": game.is_final,
            }

        return {
            "game_id": game.game_id,
            "mode": self.mode,
            "data_source": "Authentic Phase 6 NBA live-format fixture" if self.mode == "replay" else "NBA live endpoints",
            "game_status": game.game_status,
            "status_text": game.game_status_text,
            "period": int(latest["period"]),
            "period_label": _period_label(int(latest["period"])),
            "clock": str(latest["clock"]),
            "home_team": _team_payload(game.home_team, int(latest["home_score"])),
            "away_team": _team_payload(game.away_team, int(latest["away_score"])),
            "score_differential": int(latest["score_differential"]),
            "possession": {
                "team_id": possession_team_id,
                "label": possession_label,
                "known": bool(latest["possession_known"]),
                "reason": str(latest.get("possession_reason") or ""),
            },
            "fouls": {
                "home_period": int(latest["home_team_fouls_period"]),
                "away_period": int(latest["away_team_fouls_period"]),
            },
            "home_win_probability": displayed_probability,
            "away_win_probability": 1.0 - displayed_probability,
            "model_home_win_probability": raw_probability,
            "final_override_applied": final_override,
            "last_event": recent[-1],
            "recent_events": recent,
            "probability_history": history,
            "last_update": _utc_now(),
            "feed_status": feed_status,
            "error": error,
            "sequence": int(latest["source_event_index"]),
            "replay": replay,
        }

    def _store_state(self, game_id: str, state: dict[str, Any], *, emit: bool) -> bool:
        comparable = {key: value for key, value in state.items() if key != "last_update"}
        if comparable.get("replay") is not None:
            comparable["replay"] = {
                key: value for key, value in comparable["replay"].items() if key != "cursor"
            }
        signature = json.dumps(comparable, sort_keys=True, separators=(",", ":"))
        with self._lock:
            prior = self._states.get(game_id)
            if self._state_signatures.get(game_id) == signature:
                return False
            self._state_signatures[game_id] = signature
            self._states[game_id] = copy.deepcopy(state)
        if state.get("final_override_applied") and not (prior or {}).get("final_override_applied"):
            logger.info("Official FINAL product override applied game_id=%s", game_id)
        logger.debug("Application state changed game_id=%s sequence=%s", game_id, state.get("sequence"))
        if emit:
            self.socketio.emit("game_state", state, to=game_id)
        return True
