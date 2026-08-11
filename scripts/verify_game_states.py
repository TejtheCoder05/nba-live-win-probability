"""Independently verify processed Phase 3 Parquet partitions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.downloader import get_season_game_ids  # noqa: E402
from src.features.game_state import REGULATION_PERIOD_SECONDS  # noqa: E402
from src.features.storage import game_states_path  # noqa: E402

REQUIRED_COLUMNS = {
    "season",
    "game_id",
    "source_event_index",
    "action_number",
    "action_id",
    "clock",
    "action_type",
    "sub_type",
    "semantic_event",
    "description",
    "home_team_id",
    "away_team_id",
    "home_score",
    "away_score",
    "score_differential",
    "period",
    "seconds_remaining_period",
    "seconds_remaining_regulation",
    "is_overtime",
    "overtime_number",
    "possession_team_id",
    "home_possession",
    "possession_known",
    "possession_reason",
    "home_team_fouls_period",
    "away_team_fouls_period",
    "foul_marker_team_count",
    "foul_marker_matches",
    "home_win",
}


def verify_season(season: str, season_type: str) -> bool:
    game_ids, index = get_season_game_ids(season, season_type)
    rows = {str(row["GAME_ID"]): row for row in index.to_dict(orient="records")}
    missing: list[str] = []
    invalid: list[tuple[str, str]] = []
    states = known = marker_checked = marker_matched = bytes_used = 0

    for position, game_id in enumerate(game_ids, start=1):
        path = game_states_path(season, game_id)
        if not path.exists():
            missing.append(game_id)
            continue
        try:
            frame = pd.read_parquet(path, engine="pyarrow")
            _validate_frame(frame, season, game_id, rows[game_id])
        except Exception as exc:  # verification must report rather than stop at one game
            invalid.append((game_id, str(exc)))
            continue

        states += len(frame)
        known += int(frame["possession_known"].sum())
        markers = frame["foul_marker_matches"].dropna().astype(int)
        marker_checked += len(markers)
        marker_matched += int(markers.sum())
        bytes_used += path.stat().st_size
        if position % 300 == 0:
            print(f"[{season}] verified {position}/{len(game_ids)}")

    verified = len(game_ids) - len(missing) - len(invalid)
    print(f"\n{season} {season_type}")
    print(f"  verified                 : {verified}/{len(game_ids)}")
    print(f"  missing                  : {len(missing)}")
    print(f"  invalid                  : {len(invalid)}")
    print(f"  emitted states           : {states:,}")
    print(f"  states/game              : {states / verified:.1f}" if verified else "  states/game              : n/a")
    print(f"  known possession         : {100 * known / states:.2f}%" if states else "  known possession         : n/a")
    print(f"  foul marker agreement    : {marker_matched:,}/{marker_checked:,}")
    print(f"  processed disk           : {bytes_used / (1024 * 1024):.1f} MB")
    if missing:
        print(f"  missing examples         : {missing[:5]}")
    if invalid:
        print(f"  invalid examples         : {invalid[:5]}")
    return not missing and not invalid


def _validate_frame(frame: pd.DataFrame, season: str, game_id: str, row: dict) -> None:
    missing_columns = REQUIRED_COLUMNS - set(frame.columns)
    if missing_columns:
        raise ValueError(f"missing columns: {sorted(missing_columns)}")
    if frame.empty:
        raise ValueError("empty state partition")
    if set(frame["season"]) != {season} or set(frame["game_id"]) != {game_id}:
        raise ValueError("season/game identity mismatch")
    if not frame["source_event_index"].is_monotonic_increasing:
        raise ValueError("source event linkage is not monotonic")
    if not (frame["score_differential"] == frame["home_score"] - frame["away_score"]).all():
        raise ValueError("score differential mismatch")
    final = frame.iloc[-1]
    if (int(final["home_score"]), int(final["away_score"])) != (
        int(row["home_pts"]),
        int(row["away_pts"]),
    ):
        raise ValueError("final score mismatch")
    if set(frame["home_win"].astype(int)) != {int(row["home_win"])}:
        raise ValueError("home_win label mismatch")

    regulation = frame[frame["period"] <= 4]
    expected = (
        (4 - regulation["period"]) * REGULATION_PERIOD_SECONDS
        + regulation["seconds_remaining_period"]
    )
    if not expected.equals(regulation["seconds_remaining_regulation"]):
        raise ValueError("regulation seconds conversion mismatch")
    overtime = frame[frame["period"] >= 5]
    if not overtime.empty and not overtime["seconds_remaining_regulation"].eq(0).all():
        raise ValueError("overtime leaks future time into regulation clock")

    valid_teams = {int(row["home_team_id"]), int(row["away_team_id"])}
    observed = set(frame["possession_team_id"].dropna().astype(int))
    if not observed <= valid_teams:
        raise ValueError(f"invalid possession team IDs: {observed - valid_teams}")
    known = frame["possession_known"].eq(1)
    expected_home = frame.loc[known, "possession_team_id"].eq(int(row["home_team_id"])).astype(int)
    if not expected_home.equals(frame.loc[known, "home_possession"].astype(int)):
        raise ValueError("home_possession inconsistent with possession_team_id")
    if (frame[["home_team_fouls_period", "away_team_fouls_period"]] < 0).any().any():
        raise ValueError("negative period foul count")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify processed historical game states")
    parser.add_argument("--seasons", nargs="+", required=True)
    parser.add_argument("--season-type", default="Regular Season")
    args = parser.parse_args()
    results = [verify_season(season, args.season_type) for season in args.seasons]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
