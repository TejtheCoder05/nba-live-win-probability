"""Replay one captured NBA live-format game through the frozen predictor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.live.replay import model_input_from_state, replay_live_response  # noqa: E402
from src.live.scoreboard import normalize_game_details  # noqa: E402
from src.models.inference import WinProbabilityPredictor  # noqa: E402
from src.paths import LIVE_FIXTURE_DIR, MODEL_ARTIFACT_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline replay of a captured NBA live-format game")
    parser.add_argument(
        "fixture",
        nargs="?",
        type=Path,
        default=LIVE_FIXTURE_DIR / "playbyplay_0022000001.json",
    )
    parser.add_argument(
        "--game-details",
        type=Path,
        default=LIVE_FIXTURE_DIR / "game_details_0022000001.json",
    )
    parser.add_argument("--samples", type=int, default=15)
    args = parser.parse_args()

    raw = json.loads(args.fixture.read_text(encoding="utf-8"))
    details = json.loads(args.game_details.read_text(encoding="utf-8"))
    game = normalize_game_details(details)
    predictor = WinProbabilityPredictor.load(MODEL_ARTIFACT_DIR)
    result = replay_live_response(raw, game, predictor=predictor)
    if result.states.empty:
        print(f"{game.game_id}: no emitted states")
        return 0

    step = max(1, len(result.states) // max(1, args.samples))
    indices = list(range(0, len(result.states), step))
    if indices[-1] != len(result.states) - 1:
        indices.append(len(result.states) - 1)
    print(
        f"{game.game_id} {game.away_team.tricode} at {game.home_team.tricode} | "
        f"raw={len(result.adapted_events)} states={len(result.states)} status={game.game_status_text}"
    )
    for index in indices:
        state = result.states.iloc[index].to_dict()
        model_state = model_input_from_state(state, predictor.preprocessor.feature_names)
        possession = "unknown" if not model_state["possession_known"] else (
            "home" if model_state["home_possession"] == 1.0 else "away"
        )
        print(
            f"P{state['period']} {state['clock']} {int(state['home_score'])}-{int(state['away_score'])} "
            f"diff={int(state['score_differential']):+d} poss={possession} "
            f"fouls={int(state['home_team_fouls_period'])}-{int(state['away_team_fouls_period'])} "
            f"P(home)={result.probabilities[index]:.4f}"
        )
    if game.is_final:
        print("Official status is FINAL; a future product layer may display official-score certainty.")
        print("The frozen raw model probability above is intentionally not overridden here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
