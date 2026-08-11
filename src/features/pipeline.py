"""Season orchestration and aggregate Phase 3 validation metrics."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from src.data.downloader import get_season_game_ids
from src.data.play_by_play import existing_raw_path, read_raw_json
from src.features.processor import GameProcessingResult, process_game
from src.features.storage import game_states_path, write_game_states


@dataclass(slots=True)
class SeasonProcessingResult:
    season: str
    expected_games: int
    games_processed: int = 0
    raw_events: int = 0
    emitted_states: int = 0
    known_possession_states: int = 0
    unknown_possession_states: int = 0
    final_scores_valid: int = 0
    foul_markers_checked: int = 0
    foul_markers_matched: int = 0
    foul_marker_discrepancies: list[dict[str, Any]] = field(default_factory=list)
    possession_reasons: dict[str, int] = field(default_factory=dict)
    processed_bytes: int = 0
    elapsed_seconds: float = 0.0
    game_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def known_possession_percent(self) -> float:
        return 100 * self.known_possession_states / self.emitted_states if self.emitted_states else 0.0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["known_possession_percent"] = self.known_possession_percent
        payload["unknown_possession_percent"] = 100 - self.known_possession_percent if self.emitted_states else 0.0
        payload["final_score_validation_percent"] = (
            100 * self.final_scores_valid / self.games_processed if self.games_processed else 0.0
        )
        payload["foul_marker_match_percent"] = (
            100 * self.foul_markers_matched / self.foul_markers_checked
            if self.foul_markers_checked
            else 0.0
        )
        return payload


def _row_by_game_id(index: Any) -> dict[str, dict[str, Any]]:
    return {str(row["GAME_ID"]): row for row in index.to_dict(orient="records")}


def process_season(
    season: str,
    *,
    season_type: str = "Regular Season",
    game_ids: Iterable[str] | None = None,
    limit: int | None = None,
    progress_every: int = 50,
) -> SeasonProcessingResult:
    """Regenerate selected game partitions from raw JSON."""
    started = time.monotonic()
    all_game_ids, index = get_season_game_ids(season, season_type)
    selected = all_game_ids
    if game_ids is not None:
        wanted = {str(game_id) for game_id in game_ids}
        selected = [game_id for game_id in all_game_ids if game_id in wanted]
    if limit is not None:
        selected = selected[:limit]

    rows = _row_by_game_id(index)
    result = SeasonProcessingResult(season=season, expected_games=len(all_game_ids))
    reasons: Counter[str] = Counter()

    for position, game_id in enumerate(selected, start=1):
        path = existing_raw_path(game_id)
        if path is None:
            raise FileNotFoundError(f"Missing raw play-by-play for {game_id}")
        actions = read_raw_json(path)["game"]["actions"]
        row = rows[game_id]
        frame, game_result = process_game(
            actions,
            season=season,
            game_id=game_id,
            home_team_id=int(row["home_team_id"]),
            away_team_id=int(row["away_team_id"]),
            expected_home_score=int(row["home_pts"]),
            expected_away_score=int(row["away_pts"]),
            home_win=int(row["home_win"]),
        )
        output_path = write_game_states(frame, season, game_id)
        _accumulate(result, game_result, output_path, reasons)

        if progress_every and (position % progress_every == 0 or position == len(selected)):
            print(
                f"[{season}] processed {position}/{len(selected)} games | "
                f"states={result.emitted_states:,} | known possession={result.known_possession_percent:.1f}%"
            )

    result.possession_reasons = dict(reasons.most_common())
    result.elapsed_seconds = time.monotonic() - started
    return result


def _accumulate(
    season: SeasonProcessingResult,
    game: GameProcessingResult,
    output_path: Path,
    reasons: Counter[str],
) -> None:
    season.games_processed += 1
    season.raw_events += game.raw_events
    season.emitted_states += game.emitted_states
    season.known_possession_states += game.known_possession_states
    season.unknown_possession_states += game.unknown_possession_states
    season.final_scores_valid += int(game.final_score_matches)
    season.foul_markers_checked += game.foul_markers_checked
    season.foul_markers_matched += game.foul_markers_matched
    season.processed_bytes += output_path.stat().st_size
    reasons.update(game.possession_reasons)
    for discrepancy in game.foul_marker_discrepancies:
        if len(season.foul_marker_discrepancies) < 100:
            season.foul_marker_discrepancies.append({"game_id": game.game_id, **discrepancy})
    season.game_results.append(
        {
            "game_id": game.game_id,
            "raw_events": game.raw_events,
            "emitted_states": game.emitted_states,
            "known_possession_states": game.known_possession_states,
            "unknown_possession_states": game.unknown_possession_states,
            "final_score_matches": game.final_score_matches,
            "foul_markers_checked": game.foul_markers_checked,
            "foul_markers_matched": game.foul_markers_matched,
        }
    )


def write_processing_report(results: list[SeasonProcessingResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"seasons": [result.as_dict() for result in results]}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path
