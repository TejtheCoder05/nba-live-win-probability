"""Compare the same real game through historical V3 and live-format adapters."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.processor import process_game  # noqa: E402
from src.live.parity import compare_state_frames  # noqa: E402
from src.live.replay import replay_live_response  # noqa: E402
from src.live.scoreboard import normalize_game_details  # noqa: E402
from src.paths import LIVE_FIXTURE_DIR, PROCESSED_DIR, TESTS_FIXTURE_DIR  # noqa: E402


def main() -> int:
    live_raw = json.loads((LIVE_FIXTURE_DIR / "playbyplay_0022000001.json").read_text(encoding="utf-8"))
    details = json.loads((LIVE_FIXTURE_DIR / "game_details_0022000001.json").read_text(encoding="utf-8"))
    historical_raw = json.loads(
        (TESTS_FIXTURE_DIR / "historical" / "playbyplayv3_0022000001.json").read_text(encoding="utf-8")
    )
    game = normalize_game_details(details)
    live = replay_live_response(live_raw, game)
    historical, historical_result = process_game(
        historical_raw["game"]["actions"],
        season="parity",
        game_id=game.game_id,
        home_team_id=game.home_team.team_id,
        away_team_id=game.away_team.team_id,
        expected_home_score=game.home_team.score,
        expected_away_score=game.away_team.score,
        home_win=int(game.home_team.score > game.away_team.score),
    )
    if not historical_result.final_score_matches:
        raise RuntimeError("Historical parity fixture did not reconstruct the official final score")
    report = compare_state_frames(historical, live.states, game_id=game.game_id)
    payload = report.to_dict()
    output = PROCESSED_DIR / "phase6_parity_report.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
